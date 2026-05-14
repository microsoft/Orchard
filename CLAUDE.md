# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

**Orchard** is an open-source framework for scalable agentic modeling (Peng et al., arXiv 2026). This repository ships **Orchard Env** — the Kubernetes-native sandbox/environment service that the framework is built around. Companion artifacts live elsewhere:

- **Trajectory datasets** are published at [`microsoft/Orchard`](https://huggingface.co/datasets/microsoft/Orchard) on Hugging Face — two configs: `swe` (107K SWE rollouts) and `gui` (3,070 multimodal browser-navigation rollouts). When linking the dataset in docs, always use the `microsoft/Orchard` namespace.
- **Training recipes (SFT + RL)** and an **evaluation suite** are upcoming; `examples/swe/` and `examples/gui/` are intentional empty placeholders (they each contain only a README) for those releases. Don't repurpose those directories.
- The **paper** will appear as an arXiv preprint — the arXiv ID/link is not ready yet. In the README, keep `arXiv preprint — link coming soon` as the placeholder, and in the BibTeX citation block use `@misc` with a `note` field (NOT `@inproceedings` / `booktitle=COLM` — this is incorrect; the paper is not a COLM submission). Swap in `eprint` / `archivePrefix` / `primaryClass` once the arXiv ID is available.

The README's Quickstart deliberately runs **deploy → install → test** in that order — first-time visitors have no orchestrator to talk to, so flipping it back to "install first" is a regression. Both the sync and async SDK examples in the README read `SANDBOX_BASE_URL` / `SANDBOX_API_KEY` from the env (don't reintroduce hard-coded `base_url=` / `api_key=` kwargs).

## Architecture — three deployable artifacts

The repo packages **three independent artifacts**, each with a different release form. This is why `server/` and `agent/` live at the repo root rather than under `src/`:

| Artifact | Path | Release form |
|---|---|---|
| **Client SDK** | `src/orchard/` | The only `pip install`-able package (`pip install orchard`) |
| **Orchestrator** | `server/` | Container image (`docker/orchestrator.Dockerfile`); deployed to AKS, not on PyPI |
| **Sandbox agent** | `agent/` | Container image bundled with a self-contained Python interpreter; injected into every sandbox pod via init container (`docker/agent-injector.Dockerfile`) so it works inside **any** user image |

Keeping `server/` / `agent/` outside `src/` means `pip install orchard` does **not** pull in `kubernetes` / `redis` / `fastapi`. Do not move them under `src/` without weighing this.

### How an exec actually flows

1. Client `POST /sandboxes/{id}/exec` (with `wait=True`) → orchestrator (`server/exec_manager.py`) routes to the in-pod agent.
2. Orchestrator calls the in-pod agent **directly over the pod IP** (`server/agent_client.py`) — it does **not** go through the K8s API-server exec endpoint on the hot path. This is the key performance choice; preserve it.
3. If the server-side wait times out, the response carries `status: "running"`. The SDK falls back to polling `GET /jobs/{id}`.

**Critical gotcha:** a `"running"` status from the server-side wait means the job is **still executing**, never that it has completed. `JobResult.is_complete` is false for `"running"` — checking `.succeeded` / `.failed` is the right way to gate downstream logic. Several integration tests exist specifically to catch this regression (`tests/unit/test_exec_timeout.py`).

### Multi-replica state

The orchestrator can run with **multiple replicas**. State is shared via Redis (`server/redis_job_store.py`, `server/redis_store.py`); without Redis it falls back to in-memory stores. Distributed locks gate per-sandbox exec ordering. If you add new orchestrator state, decide upfront whether it must be Redis-backed.

### Kubernetes layout

- Sandboxes are pods in the shared `sandbox-pods` namespace, named `sandbox-{sandbox_id}`. The architecture doc shows a per-sandbox namespace; the current code uses a shared namespace — trust the code.
- Network isolation is via **Calico NetworkPolicy** (deny-all-egress by default; per-sandbox allow when `block_network=False`).
- Dual node pool: `sys` (system / orchestrator) and `sbx` (sandbox pods, with `workload=sandbox` node selector). Sandbox pods must carry that toleration / nodeSelector.
- K8s API calls are throttled with semaphores and retried with backoff — match this pattern when adding new K8s calls.

## Commands

```bash
# Install (editable, with dev tools)
pip install -e ".[dev]"
pip install -e ".[dev,server]"   # also pulls server runtime deps (fastapi, kubernetes, redis)

# Lint + format
ruff check .
black --check .
black .                          # auto-format

# Unit tests (no orchestrator needed; this is also the default pytest target)
python -m pytest tests/unit -v

# Run a single test
python -m pytest tests/unit/test_exec_timeout.py::TestJobResult::test_running_is_not_complete -v

# Integration scripts — these are runnable scripts, NOT pytest tests.
# Require SANDBOX_BASE_URL + SANDBOX_API_KEY and a running orchestrator:
python tests/integration/test_run.py
python tests/integration/test_async.py
python tests/integration/test_files.py
python tests/integration/test_file_ops.py

# Build + push container images (requires ACR access)
./deploy/scripts/build_push.sh

# Smoke-test a deployed orchestrator end-to-end
./deploy/scripts/smoke_test.sh
```

`pytest`'s `testpaths` is `tests/unit` in `pyproject.toml`, so a bare `pytest` will not run anything under `tests/integration/`. Running the integration scripts via `pytest` will silently do nothing useful — invoke them with `python` directly.

## Key conventions

- **Configuration.** Orchestrator settings use `pydantic-settings` (`server/settings.py`) — a singleton `settings` object imported throughout, driven by env vars or `.env`. Client SDK reads `SANDBOX_BASE_URL`, `SANDBOX_API_KEY`, `SANDBOX_PREFIX` from env unless overridden in the constructor.
- **Error handling.** Client retries on connection errors / timeouts / 503 with exponential backoff (3 retries, 1s/2s/4s base, jittered). Cleanup is best-effort — exceptions during sandbox deletion are silently caught so finalizers don't mask the original error.
- **HTTP status codes.** 401/403 for auth, 404 for missing resources, 408 for wait timeouts, 503 for transient overload — keep this contract when adding endpoints.
- **Logging.** Orchestrator emits **structured JSON** (`server/utils.py`) with request IDs propagated via `ContextVar`. Agent uses plain-text logging. Don't mix the two.
- **SDK ergonomics.** Public API is `SandboxClient` / `AsyncSandboxClient`, both context managers. The sync client also registers `atexit` + signal handlers for cleanup; the async client cleans up in `__aexit__`. Job status lifecycle is `queued → running → succeeded | failed`.
- **Python / style.** Target 3.11+. `black` (line-length 88). `ruff` rules: `E, F, I, N, W, UP`; `E501` (line length) ignored.

## Where to dig deeper

- `.github/copilot-instructions.md` — the most detailed project-conventions doc; updated by the team for AI-agent use. When it disagrees with this file, prefer this file (it's been kept in sync with the framework reframing) but cross-check the conventions.
- `docs/architecture.md` — full layered diagrams and per-component routes. Note: some sections describe a *per-sandbox* namespace that the current code has consolidated into the shared `sandbox-pods` namespace.
- `docs/threat-model.md` + `docs/known-issues.md` — security model and current known gaps; consult before changing auth, network policy, or sandbox isolation code.
- `docs/deployment.md` — full AKS provisioning, configuration, and cost estimates.

## Commit / PR style

Recent commits follow Conventional Commits (`docs:`, `docs(examples):`, `feat:`, `fix:`). The `merge-import` branch is ahead of `upstream/main` and is the active integration branch — do not push to `main` directly.
