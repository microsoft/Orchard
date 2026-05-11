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
- 🤗 **Dataset:** [`microsoft/Orchard`](https://huggingface.co/datasets/microsoft/Orchard) — 107,185 SWE agent trajectories across 2,788 repositories
- 🗺️ **Roadmap:** RL training code, evaluation suite, and GUI/Claw recipes — see [Roadmap](#roadmap)

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
- **Orchard-GUI** — browser navigation. A 4B backbone trained on only ~2.6k
  tasks averages **68.4%** across WebVoyager / Online-Mind2Web / DeepShop.
- **Orchard-Claw** — personal-assistant workflows. **73.9%** pass@3 on Claw-Eval
  with the ZeroClaw harness.

References:

- 📄 Paper: arXiv link coming soon (COLM 2026)
- 🤗 SWE trajectory dataset: [`microsoft/Orchard`](https://huggingface.co/datasets/microsoft/Orchard) —
  107,185 multi-turn SWE rollouts across 2,788 repositories, with verified
  resolve labels (74,649 resolved · 32,536 unresolved).
- 🤗 GUI trajectory dataset: `microsoft/Orchard-GUI` — coming soon. Browser-navigation
  trajectories used to train the Orchard-GUI recipe.

## Roadmap

This release ships Orchard Env — the environment-service foundation. Additional
components from the paper will be released on top of it:

- **Orchard-SWE RL training code** — on-policy RL rollouts and policy
  optimization that produced the 67.5% SWE-bench Verified result, built on
  Orchard Env's sandbox interface.
- **Evaluation suite** — harness-agnostic evaluation pipelines (SWE-bench
  Verified, SWE-bench Multilingual, Terminal-Bench 2.0) running on Orchard Env.
- **Orchard-GUI** — browser-navigation agentic-modeling recipe and trajectory
  data.
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
