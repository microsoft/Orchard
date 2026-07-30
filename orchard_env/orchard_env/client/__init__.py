"""Client package for Sandbox Orchestrator."""

from orchard_env.client.sandbox_client import (
    AsyncSandbox,
    AsyncSandboxClient,
    AsyncSandboxInstance,
    JobResult,
    # Aliases for backward compatibility
    Sandbox,
    SandboxClient,
    SandboxInstance,
)

__all__ = [
    "SandboxClient",
    "SandboxInstance",
    "AsyncSandboxClient",
    "AsyncSandboxInstance",
    "JobResult",
    # Aliases
    "Sandbox",
    "AsyncSandbox",
]
