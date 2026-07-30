# Examples

Runnable examples for the `orchard_env` Python SDK. Each one talks to a live
orchestrator — see the [deployment guide](../docs/deployment.md) if you need to
stand one up first.

## Setup

```bash
pip install -e "orchard_env[dev]"

export SANDBOX_BASE_URL="http://your-orchestrator-host"
export SANDBOX_API_KEY="your-api-key"
```

Both variables are read automatically by `SandboxClient()` and
`AsyncSandboxClient()`. If the orchestrator runs with `REQUIRE_API_KEY=false`,
`SANDBOX_API_KEY` can be omitted.

Every example creates real sandbox pods and deletes them on exit.

## [`getting_started.py`](getting_started.py)

A tour of the SDK. Run everything, or name the parts you want:

```bash
python examples/getting_started.py
python examples/getting_started.py basic files
python examples/getting_started.py --help
```

| Name | Shows |
| --- | --- |
| `basic` | Create a sandbox, run commands, auto-cleanup |
| `files` | Upload/download files and content, list directories |
| `env` | Environment variables and working directories |
| `patch` | Applying a git patch |
| `resources` | Custom CPU/memory and enabling network egress |
| `errors` | Non-zero exits, stderr, and command timeouts |
| `session` | A multi-turn agent session building a small project |
| `pty` | Interactive PTY: TTY detection, stdin, signals, resize |
| `async` | The asynchronous client |
| `concurrent` | Several sandboxes in parallel with `asyncio.gather` |
| `pty-async` | PTY sessions from the async client |

## [`agent_harness.py`](agent_harness.py)

The bundled agent harnesses (`codex`, `claude`, `pi`, `opencode`, `hermes`) are
on `PATH` in every sandbox, whatever the base image.

```bash
# Inspect what's available — no provider credentials needed
python examples/agent_harness.py
python examples/agent_harness.py --image python:3.11-slim

# Actually drive a harness
OPENAI_API_KEY=sk-...        python examples/agent_harness.py --run codex
ANTHROPIC_API_KEY=sk-ant-... python examples/agent_harness.py --run claude
```

Credentials are passed per `exec()` call and are never baked into an image.
Driving a harness needs `block_network=False`, since the harness itself must
reach the model API.

## See also

- [SDK reference](../docs/sdk.md)
- [HTTP API reference](../docs/api.md)
- [Architecture](../docs/architecture.md)
