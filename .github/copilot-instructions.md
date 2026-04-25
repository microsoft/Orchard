# Copilot Instructions for azure-modal (aks_modal)

## Architecture

This is a **sandbox orchestration service** running on Azure AKS, designed for multi-turn agent↔sandbox interactions (e.g., SWE-bench). Three main layers:

1. **Client SDK** (`client/sandbox_client.py`) — Sync (`SandboxClient`) and async (`AsyncSandboxClient`) Python clients. Both use context managers for lifecycle management. `SandboxInstance` / `AsyncSandboxInstance` handle exec, file ops, and patching. Public API is re-exported from `aks_modal/__init__.py`.

2. **Orchestrator** (`orchestrator/`) — FastAPI service managing sandbox lifecycle. Key components:
   - `api.py` — All routes defined directly (no APIRouter), request-ID middleware, API-key auth via `X-API-Key` header
   - `sandbox_manager.py` — Creates/deletes pods in a shared `sandbox-pods` namespace, manages network policies
   - `exec_manager.py` — Submits exec jobs, runs them under per-sandbox locks via the in-pod agent
   - `agent_client.py` — Direct HTTP calls to pod IPs (bypasses K8s API server for exec/file hot paths)
   - `job_store.py` / `redis_job_store.py` — Job state storage (in-memory or Redis for multi-replica)
   - `pod_watcher.py` — K8s Watch/Informer for cached pod status

3. **Sandbox Agent** (`agent/server.py`) — Lightweight FastAPI server injected into every sandbox pod. Handles `/exec`, `/files/upload`, `/files/download`, `/files/list`. Injected via init container (`Dockerfile.agent-injector`) that bundles a self-contained Python interpreter so it works with ANY user image.

### Exec flow

Client calls `POST /sandboxes/{id}/exec` with `wait=True` → orchestrator runs command via agent HTTP → returns result. If the server-side wait times out (response status is `"running"`), the client falls back to polling `GET /jobs/{id}`. **Critical**: a `"running"` status from the server-side wait means the job is still executing — never treat it as complete.

### Container images

- `Dockerfile` — Orchestrator (`python -m orchestrator.main`)
- `Dockerfile.sandbox` — Sandbox with baked-in agent (for known images)
- `Dockerfile.agent-injector` — Init container that copies agent + bundled Python into any user image via emptyDir volume

## Build, Test, and Lint

```bash
# Install (editable)
pip install -e ".[dev]"

# Lint
ruff check .
black --check .

# Format
black .

# Unit tests (no running orchestrator needed)
python -m pytest tests/test_exec_timeout.py -v
python -m pytest tests/test_resources.py -v

# Run a single test
python -m pytest tests/test_exec_timeout.py::TestJobResult::test_running_is_not_complete -v

# Integration tests (require running orchestrator + SANDBOX_BASE_URL set)
python tests/test_run.py
python tests/test_async.py
python tests/test_files.py
python tests/test_file_ops.py

# Build container images (requires ACR access)
./scripts/build_push.sh
```

## Key Conventions

### Configuration

- **Orchestrator**: `pydantic-settings` (`orchestrator/settings.py`), configured via env vars or `.env` file. Singleton `settings` object imported throughout.
- **Client SDK**: Constructor params take priority over env vars (`SANDBOX_BASE_URL`, `SANDBOX_API_KEY`, `SANDBOX_PREFIX`).

### Error handling

- Client retries on connection errors, timeouts, and 503s with exponential backoff + jitter (3 retries, 1s/2s/4s base).
- Cleanup is always best-effort — exceptions during sandbox deletion are silently caught.
- Orchestrator uses HTTP status codes consistently: 401/403 for auth, 404 for missing resources, 408 for wait timeouts, 503 for transient overload.

### Logging

- Orchestrator uses structured JSON logging by default (`orchestrator/utils.py`). Request IDs are propagated via `ContextVar`.
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
