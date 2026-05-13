# Orchard

**Orchard is an open-source framework for scalable agentic modeling**, built
around a thin, reusable environment layer. At its core is **Orchard Env**, a
Kubernetes-native service that exposes generic primitives — sandbox lifecycle,
command execution, file I/O, network policy, and a REST API — without coupling
to any specific agent harness, trainer, inference backend, or task domain. The
same environment is reused for trajectory distillation, on-policy RL rollouts,
and evaluation, so artifacts (datasets, recipes, models) remain portable across
harnesses and domains.

This repository contains **Orchard Env**: the SDK, FastAPI orchestrator, and
in-pod execution agent. Azure AKS is the reference deployment, but the service
is plain Kubernetes — any conformant cluster works.

- 📄 **Paper:** *Orchard: An Open-Source Agentic Modeling Framework* (Peng et al., COLM 2026) — arXiv link coming soon
- 🤗 **Dataset:** [`microsoft/Orchard`](https://huggingface.co/datasets/microsoft/Orchard) — `swe` (107K SWE trajectories) and `gui` (3,070 multimodal browser-navigation rollouts) subsets
- 🗺️ **Roadmap:** training recipes, evaluation suite, and the Orchard-Claw recipe — see [Roadmap](#roadmap)

## What Orchard Env provides

- **REST API** for sandbox lifecycle management (create / exec / files / patch / delete)
- **Sync and async Python SDK** with auto-cleanup, retries, and context-manager ergonomics
- **In-pod agent** for low-latency exec over Pod IP (bypasses K8s API server hot path)
- **Multi-replica orchestrator** with Redis-backed state and distributed locks
- **Network isolation** via Calico NetworkPolicy (deny-egress by default)
- **Per-sandbox CPU / memory / timeout** and TTL-based cleanup
- **API-key authentication** (`X-API-Key` header)
- **Kubernetes-native** (reference deployment on Azure AKS with a dual node pool architecture — `sys` + `sbx` — plus ACR and Log Analytics)

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

The first-time flow is **deploy → install → test**: stand up an orchestrator on
your cluster, install the Python SDK, then run the SDK against the deployment
you just created. If you already have access to a running orchestrator
(e.g. one hosted by your team), skip step 1 and jump to step 2.

### 1. Deploy your own orchestrator

```bash
# Provision Azure resources (AKS + ACR + Log Analytics)
./deploy/scripts/deploy_aks.sh

# Build and push container images
./deploy/scripts/build_push.sh

# Generate API keys and create the K8s secret
python deploy/k8s/gen_keys.py
cp deploy/k8s/secret.example.yaml deploy/k8s/secret.yaml
# ...paste generated keys into deploy/k8s/secret.yaml...

# Deploy to Kubernetes
./deploy/scripts/deploy_k8s.sh

# Smoke-test the deployment
./deploy/scripts/smoke_test.sh
```

The smoke-test prints the orchestrator's reachable `base_url` and the API key
to use. Export them so the SDK picks them up automatically:

```bash
export SANDBOX_BASE_URL="http://<orchestrator>:8000"
export SANDBOX_API_KEY="<key-from-gen_keys.py>"
```

Full walkthrough (AKS provisioning, configuration, ops, cost estimates):
[docs/deployment.md](docs/deployment.md).

### 2. Install the SDK

```bash
pip install -e .
```

### 3. Test the SDK against your orchestrator

```python
from orchard import SandboxClient

with SandboxClient() as client:  # reads SANDBOX_BASE_URL / SANDBOX_API_KEY
    with client.create_sandbox("python:3.11-slim") as sandbox:
        result = sandbox.exec("echo 'hello from orchard'")
        print(result.stdout)
```

Async variant:

```python
import asyncio
from orchard import AsyncSandboxClient

async def main():
    async with AsyncSandboxClient() as client:
        async with await client.create_sandbox("python:3.11-slim") as sandbox:
            print((await sandbox.exec("uname -a")).stdout)

asyncio.run(main())
```

See [docs/client.md](docs/client.md) for the full SDK reference.

## Documentation

| Document | Contents |
|---|---|
| [docs/client.md](docs/client.md) | Python SDK reference (sync + async) |
| [docs/api.md](docs/api.md) | REST API endpoints + configuration env vars |
| [docs/deployment.md](docs/deployment.md) | AKS deployment, configuration, ops, cost estimates |
| [docs/architecture.md](docs/architecture.md) | Architecture deep dive |
| [docs/threat-model.md](docs/threat-model.md) | Object model, data flow & STRIDE threat model |
| [docs/known-issues.md](docs/known-issues.md) | Known security issues / threat-model findings |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

All documents above describe Orchard Env (this repository); paper-level material
lives in the references below.

## Paper & dataset

The framework is described in **Orchard: An Open-Source Agentic Modeling
Framework** (Peng et al., COLM 2026). The paper presents three agentic-modeling
recipes built on Orchard Env:

- **Orchard-SWE** — software engineering. Qwen3-30B-A3B-Thinking reaches
  **64.3%** on SWE-bench Verified after SFT and **67.5%** after SFT + RL, a
  +45.5-point lift over the base model and state-of-the-art among open-source
  models of comparable size.
- **Orchard-GUI** — browser navigation. Qwen3-VL-4B-Thinking trained on only
  ~2.6k tasks reaches **68.4%** average across WebVoyager / Online-Mind2Web /
  DeepShop (74.1 / 67.0 / 64.0) after SFT + RL — a +30.3-point lift over the
  base 4B model.
- **Orchard-Claw** — personal-assistant workflows. **73.9%** pass@3 on Claw-Eval
  with the ZeroClaw harness.

References:

- 📄 Paper: arXiv link coming soon (COLM 2026)
- 🤗 Trajectory datasets: [`microsoft/Orchard`](https://huggingface.co/datasets/microsoft/Orchard) — one repository ships two parallel subsets, both produced inside the same Orchard Env sandbox infrastructure:
  - **`swe` config** — 107,185 multi-turn SWE rollouts across 2,788 repositories,
    with verified resolve labels (74,649 resolved · 32,536 unresolved).
  - **`gui` config** — 3,070 judge-verified successful per-step rollouts from a
    web-browsing GUI agent across 409 WebVoyager-style tasks, each with a
    rendered screenshot (multimodal).

## Roadmap

This release ships Orchard Env — the environment-service foundation — alongside
the SWE and GUI trajectory datasets ([`microsoft/Orchard`](https://huggingface.co/datasets/microsoft/Orchard)).
Additional components from the paper will follow on top of Orchard Env:

- **Training recipes (SFT + RL)** — the SFT and on-policy RL pipelines that
  produced the 67.5% SWE-bench Verified and 68.4% GUI-average results, built
  directly on Orchard Env's sandbox interface.
- **Evaluation suite** — harness-agnostic evaluation pipelines (SWE-bench
  Verified, SWE-bench Multilingual, Terminal-Bench 2.0, WebVoyager /
  Online-Mind2Web / DeepShop) running on Orchard Env.
- **Orchard-Claw** — personal-assistant agentic-modeling recipe and trajectory
  data.

Track progress via GitHub releases and the project's issues page.

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

## Citation

If you use Orchard or Orchard Env in your research, please cite:

```bibtex
@inproceedings{peng2026orchard,
  title={Orchard: An Open-Source Agentic Modeling Framework},
  author={Peng, Baolin and Yao, Wenlin and Wu, Qianhui and Cheng, Hao and
          Yu, Xiao and Yang, Rui and Ge, Tao and Sordoni, Alessandro and
          Yuan, Xingdi and Shen, Yelong and He, Pengcheng and Zhang, Tong and
          Yu, Zhou and Gao, Jianfeng},
  booktitle={Conference on Language Modeling (COLM)},
  year={2026}
}
```

## License

[MIT](LICENSE) © Microsoft Corporation.
