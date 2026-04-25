"""Client package for Sandbox Orchestrator."""

from client.sandbox_client import (
    JobResult, 
    SandboxClient,
    SandboxInstance,
    AsyncSandboxClient, 
    AsyncSandboxInstance,
    # Aliases for backward compatibility
    Sandbox,
    AsyncSandbox,
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
