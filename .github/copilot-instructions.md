# Copilot Instructions for Orchard

## Architecture

This repo is the **Orchard** project. Everything currently lives under `orchard_env/` — a Kubernetes-based sandbox orchestration service for multi-turn agent↔sandbox interactions (e.g. SWE-bench). `trainer/slime/` is a scaffolded placeholder for the RL trainer fork.

All paths below are relative to `orchard_env/`. The importable package is `orchard_env` (`orchard_env/orchard_env/`). Three main layers:

1. **Client SDK** (`orchard_env/client/sandbox_client.py`) — Sync (`SandboxClient`) and async (`AsyncSandboxClient`) Python clients. Both use context managers for lifecycle management. `SandboxInstance` / `AsyncSandboxInstance` handle exec, file ops, and patching. Public API is re-exported from `orchard_env/__init__.py`.

2. **Orchestrator** (`orchard_env/orchestrator/`) — FastAPI service managing sandbox lifecycle. Key components:
   - `api.py` — All routes defined directly (no APIRouter), request-ID middleware, API-key auth via `X-API-Key` header
   - `sandbox_manager.py` — Creates/deletes pods in a shared `sandbox-pods` namespace, manages network policies
   - `exec_manager.py` — Submits exec jobs, runs them under per-sandbox locks via the in-pod agent
   - `agent_client.py` — Direct HTTP calls to pod IPs (bypasses K8s API server for exec/file hot paths)
   - `job_store.py` / `redis_job_store.py` — Job state storage (in-memory or Redis for multi-replica)
   - `pod_watcher.py` — K8s Watch/Informer for cached pod status

3. **Sandbox Agent** (`orchard_env/agent/server.py`) — Lightweight FastAPI server injected into every sandbox pod. Handles `/exec`, `/files/upload`, `/files/download`, `/files/list`. Injected via init container (`Dockerfile.agent-injector`) that bundles a self-contained Python interpreter so it works with ANY user image.

### Exec flow

Client calls `POST /sandboxes/{id}/exec` with `wait=True` → orchestrator runs command via agent HTTP → returns result. If the server-side wait times out (response status is `"running"`), the client falls back to polling `GET /jobs/{id}`. **Critical**: a `"running"` status from the server-side wait means the job is still executing — never treat it as complete.

### Container images

- `Dockerfile` — Orchestrator (`python -m orchard_env.orchestrator.main`); build context is `orchard_env/`
- `Dockerfile.sandbox` — Sandbox with baked-in agent (for known images)
- `Dockerfile.agent-injector` — Init container that copies agent + bundled Python into any user image via emptyDir volume

## Build, Test, and Lint

```bash
# Install (editable, from the repo root)
pip install -e "orchard_env[dev]"

# Lint
ruff check .
black --check .

# Format
black .

# Unit tests (offline, no orchestrator needed) — all live in tests/
python -m pytest

# Run a single test
python -m pytest tests/test_exec_timeout.py::TestJobResult::test_running_is_not_complete -v

# Integration scripts (require a running orchestrator + SANDBOX_BASE_URL/SANDBOX_API_KEY).
# These live in tests/integration/, are NOT named test_*.py, and are excluded
# from pytest collection because importing them fires real network calls.
python tests/integration/soak.py
python tests/integration/sandbox_tools.py
python tests/integration/bench_concurrent.py

# Build container images (run from orchard_env/, requires registry access)
./scripts/build_push.sh
```

## Key Conventions

### Configuration

- **Orchestrator**: `pydantic-settings` (`orchard_env/orchestrator/settings.py`), configured via env vars or `.env` file. Singleton `settings` object imported throughout.
- **Client SDK**: Constructor params take priority over env vars (`SANDBOX_BASE_URL`, `SANDBOX_API_KEY`, `SANDBOX_PREFIX`).

### Error handling

- Client retries on connection errors, timeouts, and 503s with exponential backoff + jitter (3 retries, 1s/2s/4s base).
- Cleanup is always best-effort — exceptions during sandbox deletion are silently caught.
- Orchestrator uses HTTP status codes consistently: 401/403 for auth, 404 for missing resources, 408 for wait timeouts, 503 for transient overload.

### Logging

- Orchestrator uses structured JSON logging by default (`orchard_env/orchestrator/utils.py`). Request IDs are propagated via `ContextVar`.
- Agent uses plain text logging (`%(asctime)s [%(levelname)s]` format).

### Python

- Target: Python 3.11+
- Formatting: `black` (line-length 88)
- Linting: `ruff` (rules: E, F, I, N, W, UP; E501 ignored)
- Async: `pytest-asyncio` for async test support

### Client SDK patterns

- Always use context managers (`with SandboxClient() as client:` / `async with AsyncSandboxClient() as client:`)
- `SandboxInstance.exec()` returns a `JobResult` — check `.succeeded`, `.failed`, or `.is_complete`
- Job statuses: `"queued"` → `"running"` → `"succeeded"` | `"failed"`
- Sync client registers `atexit` + signal handlers for cleanup; async client cleans up on `__aexit__`

### Kubernetes

- Sandboxes are pods in a shared `sandbox-pods` namespace, named `sandbox-{sandbox_id}`
- Network isolation via Calico NetworkPolicy (deny-all-egress default, per-sandbox allow when `block_network=False`)
- Dual node pool architecture: `sys` (system components) + `sbx` (sandbox pods, with `workload=sandbox` node selector)
- K8s API calls are throttled with semaphores and retried with backoff
