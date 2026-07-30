# Orchard overview

Orchard is a toolkit for training and evaluating agents in real, isolated
execution environments. It separates *where the agent acts* from *how the agent
learns*.

## Components

### `orchard_env/` — the environment

A Kubernetes-based sandbox orchestration service and a Python SDK. It gives an
agent a fresh container per task and lets it run many turns of commands, file
edits, and git patches against that container.

- **Control plane** (`orchard_env/orchestrator/`) — FastAPI service that manages
  sandbox lifecycle, exec jobs, and cleanup. Scales horizontally with Redis.
- **In-pod agent** (`orchard_env/agent/`) — a small FastAPI server injected into
  every sandbox pod. The orchestrator calls it directly over the pod IP, so exec
  and file I/O never touch the Kubernetes API server.
- **SDK** (`orchard_env/client/`) — sync (`SandboxClient`) and async
  (`AsyncSandboxClient`) clients with context-manager lifecycle handling.

See [orchard_env/README.md](../orchard_env/README.md) and
[orchard_env/docs/architecture.md](../orchard_env/docs/architecture.md).

### `trainer/slime/` — the trainer

A vendored fork of the [slime](https://github.com/THUDM/slime) RL training stack,
with Orchard-specific rollout code under `examples/orchard/`. Fork-local changes
are tracked in `trainer/slime/ORCHARD_CHANGES.md`.

## How they fit together

```
   ┌──────────────┐    rollout requests    ┌────────────────────┐
   │  trainer/    │ ─────────────────────▶ │   orchard_env      │
   │  slime       │                        │   orchestrator     │
   │              │ ◀───────────────────── │   (FastAPI)        │
   └──────────────┘   trajectories/rewards └─────────┬──────────┘
                                                     │ HTTP to pod IP
                                           ┌─────────▼──────────┐
                                           │   sandbox pods     │
                                           │   (in-pod agent)   │
                                           └────────────────────┘
```

The trainer drives rollouts through the `orchard_env` SDK; each rollout gets its
own sandbox, and results flow back as trajectories for the RL loop.
