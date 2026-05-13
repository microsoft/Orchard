# Examples

Runnable examples for the Orchard Python SDK.

## Prerequisites

- A running Orchard orchestrator service (see [docs/deployment.md](../docs/deployment.md))
- The `orchard` package installed: `pip install -e .` from the repo root
- The following environment variables set:

  ```bash
  export SANDBOX_BASE_URL="http://your-orchestrator-host:8000"
  export SANDBOX_API_KEY="your-api-key"
  ```

## Examples

### `getting_started.py`

End-to-end walkthrough covering both sync and async clients: create sandboxes,
execute commands, upload/download files, and apply git patches.

```bash
python examples/getting_started.py
```

### `swe/` — Orchard-SWE training recipe

Placeholder for the SFT + RL training recipe that produced 67.5% on SWE-bench
Verified. **Coming soon.** See [`swe/README.md`](swe/README.md).

### `gui/` — Orchard-GUI training recipe

Placeholder for the SFT + RL training recipe that produced 68.4% average across
WebVoyager / Online-Mind2Web / DeepShop. **Coming soon.** See
[`gui/README.md`](gui/README.md).
