# Orchard

A sandbox orchestration service that runs on Azure AKS, designed for
multi-turn agent ↔ sandbox interactions (e.g. SWE-bench Verified). Provides a
Modal-style Python SDK over a FastAPI orchestrator that manages sandbox pods
on a dedicated node pool.

## Features

- **REST API** for sandbox lifecycle management (create / exec / files / patch / delete)
- **Sync and async Python SDK** with auto-cleanup, retries, and context-manager ergonomics
- **In-pod agent** for low-latency exec over Pod IP (bypasses K8s API server hot path)
- **Multi-replica orchestrator** with Redis-backed state and distributed locks
- **Network isolation** via Calico NetworkPolicy (deny-egress by default)
- **Per-sandbox CPU / memory / timeout** and TTL-based cleanup
- **API-key authentication** (`X-API-Key` header)
- **AKS-native**: dual node pool architecture (`sys` + `sbx`), ACR, Log Analytics

## Repository layout

```
.
├── src/orchard/    # Python SDK (pip install orchard)
├── server/         # FastAPI orchestrator (runs in AKS)
├── agent/          # Lightweight FastAPI agent injected into sandbox pods
├── docker/         # Dockerfiles (orchestrator, sandbox, agent-injector)
├── deploy/
│   ├── azure/      # Azure infra scripts (AKS, ACR, Log Analytics)
│   ├── k8s/        # Kubernetes manifests
│   └── scripts/    # Build / deploy / smoke-test scripts
├── docs/           # Deployment guide, REST API reference, architecture, SDK
├── examples/       # Runnable SDK examples
└── tests/          # Unit (default) and integration tests
```

### Layout rationale

Three deployable artifacts live side by side, each with a different release form:

- **`src/orchard/`** — the only `pip install`-able package. The `src/` layout
  ensures local development always exercises the installed wheel rather than
  the source tree, catching missing `package_data` or import bugs early.
- **`server/`** — a service shipped as a container image (`docker/orchestrator.Dockerfile`).
  It is *not* published to PyPI, so it intentionally stays at the repo root:
  this keeps `COPY server/ ./server/` and `python -m server.main` symmetric,
  avoids polluting `src/` with non-library code, and means
  `pip install orchard` does not pull in `kubernetes` / `redis-py`.
- **`agent/`** — also shipped as a container image and bundled into a
  self-contained Python interpreter; same reasoning as `server/`.

## Quickstart

### Install the SDK

```bash
pip install -e .
```

### Use the SDK against an existing orchestrator

```python
from orchard import SandboxClient

with SandboxClient(base_url="http://<orchestrator>:8000", api_key="...") as client:
    with client.create_sandbox("python:3.11-slim") as sandbox:
        result = sandbox.exec("echo 'hello from orchard'")
        print(result.stdout)
```

Async variant:

```python
import asyncio
from orchard import AsyncSandboxClient

async def main():
    async with AsyncSandboxClient() as client:  # reads SANDBOX_BASE_URL / SANDBOX_API_KEY
        async with await client.create_sandbox("python:3.11-slim") as sandbox:
            print((await sandbox.exec("uname -a")).stdout)

asyncio.run(main())
```

See [docs/client.md](docs/client.md) for the full SDK reference.

### Deploy your own orchestrator

```bash
# 1. Provision Azure resources (AKS + ACR + Log Analytics)
./deploy/scripts/deploy_aks.sh

# 2. Build and push container images
./deploy/scripts/build_push.sh

# 3. Generate API keys and create the K8s secret
python deploy/k8s/gen_keys.py
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml
# ...paste generated keys into deploy/k8s/secret.yaml...

# 4. Deploy to Kubernetes
./deploy/scripts/deploy_k8s.sh

# 5. Smoke-test
./deploy/scripts/smoke_test.sh
```

Full walkthrough: [docs/deployment.md](docs/deployment.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/client.md](docs/client.md) | Python SDK reference (sync + async) |
| [docs/api.md](docs/api.md) | REST API endpoints + configuration env vars |
| [docs/deployment.md](docs/deployment.md) | AKS deployment, configuration, ops, cost estimates |
| [docs/architecture.md](docs/architecture.md) | Architecture deep dive |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Development

```bash
pip install -e ".[dev]"             # SDK + dev tools
pip install -e ".[dev,server]"      # also include server runtime deps

# Lint and format
ruff check .
black --check .

# Unit tests (no orchestrator required)
pytest tests/unit -v

# Integration scripts (require a running orchestrator + SANDBOX_BASE_URL + SANDBOX_API_KEY)
# These are runnable scripts, not pytest tests:
python tests/integration/test_run.py
python tests/integration/test_async.py
python tests/integration/test_files.py
```

CI runs `ruff`, `black --check`, and the unit suite on every push and pull request
(see `.github/workflows/ci.yml`).

## Contributing

Contributions are welcome. Please open an issue or pull request.

## License

[MIT](LICENSE) © Microsoft Corporation.
