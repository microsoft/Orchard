# Copilot Instructions for Orchard

## Architecture

This is a **sandbox orchestration service** running on Azure AKS, designed for multi-turn agent↔sandbox interactions (e.g., SWE-bench). Three main layers, each shipped differently:

1. **Client SDK** (`src/orchard/client.py`) — Sync (`SandboxClient`) and async (`AsyncSandboxClient`) Python clients. Both use context managers for lifecycle management. `SandboxInstance` / `AsyncSandboxInstance` handle exec, file ops, and patching. Public API is re-exported from `src/orchard/__init__.py`. **Shipped as a `pip install orchard` package** (only thing under `src/`).

2. **Orchestrator** (`server/`) — FastAPI service managing sandbox lifecycle. **Shipped as a container image** (`docker/orchestrator.Dockerfile`), not as a Python package — that is why it lives at the repo root and not under `src/`. Key components:
   - `api.py` — All routes defined directly (no APIRouter), request-ID middleware, API-key auth via `X-API-Key` header
   - `sandbox_manager.py` — Creates/deletes pods in a shared `sandbox-pods` namespace, manages network policies
   - `exec_manager.py` — Submits exec jobs, runs them under per-sandbox locks via the in-pod agent
   - `agent_client.py` — Direct HTTP calls to pod IPs (bypasses K8s API server for exec/file hot paths)
   - `job_store.py` / `redis_job_store.py` — Job state storage (in-memory or Redis for multi-replica)
   - `pod_watcher.py` — K8s Watch/Informer for cached pod status

3. **Sandbox Agent** (`agent/server.py`) — Lightweight FastAPI server injected into every sandbox pod. Handles `/exec`, `/files/upload`, `/files/download`, `/files/list`. Injected via init container (`docker/agent-injector.Dockerfile`) that bundles a self-contained Python interpreter so it works with ANY user image. Also shipped as a container image, not a Python package.

### Exec flow

Client calls `POST /sandboxes/{id}/exec` with `wait=True` → orchestrator runs command via agent HTTP → returns result. If the server-side wait times out (response status is `"running"`), the client falls back to polling `GET /jobs/{id}`. **Critical**: a `"running"` status from the server-side wait means the job is still executing — never treat it as complete.

### Container images

- `docker/orchestrator.Dockerfile` — Orchestrator (`python -m server.main`)
- `docker/sandbox.Dockerfile` — Sandbox with baked-in agent (for known images)
- `docker/agent-injector.Dockerfile` — Init container that copies agent + bundled Python into any user image via emptyDir volume

## Build, Test, and Lint

```bash
# Install (editable)
pip install -e ".[dev]"

# Lint
ruff check .
black --check .

# Format
black .

# Unit tests (no running orchestrator needed) — also the default `pytest` target
python -m pytest tests/unit -v

# Run a single test
python -m pytest tests/unit/test_exec_timeout.py::TestJobResult::test_running_is_not_complete -v

# Integration scripts (require running orchestrator + SANDBOX_BASE_URL set).
# These are runnable scripts, not pytest tests:
python tests/integration/test_run.py
python tests/integration/test_async.py
python tests/integration/test_files.py
python tests/integration/test_file_ops.py

# Build container images (requires ACR access)
./deploy/scripts/build_push.sh
```

## Key Conventions

### Configuration

- **Orchestrator**: `pydantic-settings` (`server/settings.py`), configured via env vars or `.env` file. Singleton `settings` object imported throughout.
- **Client SDK**: Constructor params take priority over env vars (`SANDBOX_BASE_URL`, `SANDBOX_API_KEY`, `SANDBOX_PREFIX`).

### Error handling

- Client retries on connection errors, timeouts, and 503s with exponential backoff + jitter (3 retries, 1s/2s/4s base).
- Cleanup is always best-effort — exceptions during sandbox deletion are silently caught.
- Orchestrator uses HTTP status codes consistently: 401/403 for auth, 404 for missing resources, 408 for wait timeouts, 503 for transient overload.

### Logging

- Orchestrator uses structured JSON logging by default (`server/utils.py`). Request IDs are propagated via `ContextVar`.
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
