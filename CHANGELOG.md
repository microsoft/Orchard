# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- Renamed Python SDK package from `aks_modal` to `orchard`; SDK now lives at `src/orchard/`.
- Renamed `orchestrator/` server module to `server/`.
- Consolidated all Dockerfiles under `docker/` (`<component>.Dockerfile` naming).
- Consolidated deployment artifacts under `deploy/` (`deploy/k8s/`, `deploy/azure/`, `deploy/scripts/`).
- Split tests into `tests/unit/` and `tests/integration/`; integration tests are skipped by default.
- Added `LICENSE` (MIT, © Microsoft Corporation) and reference it from `pyproject.toml`.
- Renamed `k8s/secret.yaml` to `deploy/k8s/secret.example.yaml`; real secret is gitignored.
- Added `.github/workflows/ci.yml` (ruff + black + unit tests).

### Removed
- Duplicate scratch infra scripts (`deploy_aks_large*.sh`, `dry_run_cmd_n2.md`).

## [0.2.0] - 2024-12-26

- API Key authentication via `X-API-Key` header.
- Redis storage for multi-replica state sharing.
- File upload / download / list operations.
- Per-sandbox CPU / memory / timeout customization.
- New `AsyncSandboxClient`.
- Login-shell exec mode (`bash -lc`).
- 50 pre-generated API keys.
- Distributed execution lock for consistency.

## [0.1.0] - 2024-12-18

- Initial release.
- Full sandbox lifecycle management.
- Asynchronous command execution.
- Azure AKS integration.
- NetworkPolicy support.
- Python client library.
- End-to-end deployment scripts.
