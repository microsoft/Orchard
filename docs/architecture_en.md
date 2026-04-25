# Sandbox Orchestrator Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              User Application                                    │
│                         (Python Script / Notebook)                               │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                               Client Layer                                       │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │   SandboxClient (Sync)              │      AsyncSandboxClient (Async)       ││
│  │   ├── create_sandbox()              │      ├── create_sandbox()             ││
│  │   ├── delete_sandbox()              │      ├── delete_sandbox()             ││
│  │   ├── get_sandbox()                 │      ├── get_sandbox()                ││
│  │   └── health()                      │      └── health()                     ││
│  └─────────────────────────────────────┴───────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │   SandboxInstance / AsyncSandboxInstance                                    ││
│  │   ├── exec()          - Execute commands                                    ││
│  │   ├── apply_patch()   - Apply Git patches                                   ││
│  │   ├── upload_file()   - Upload files                                        ││
│  │   ├── download_file() - Download files                                      ││
│  │   ├── list_files()    - List files                                          ││
│  │   └── delete()        - Delete sandbox                                      ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │ HTTP/REST
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            Orchestrator Layer                                    │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                         FastAPI Application (api.py)                      │  │
│  │                                                                           │  │
│  │   Endpoints:                                                              │  │
│  │   ├── POST   /sandboxes              - Create sandbox                     │  │
│  │   ├── GET    /sandboxes/{id}         - Get sandbox status                 │  │
│  │   ├── DELETE /sandboxes/{id}         - Delete sandbox                     │  │
│  │   ├── POST   /sandboxes/{id}/exec    - Execute command                    │  │
│  │   ├── POST   /sandboxes/{id}/apply_patch - Apply patch                    │  │
│  │   ├── POST   /sandboxes/{id}/files   - Upload file                        │  │
│  │   ├── GET    /sandboxes/{id}/files   - Download file                      │  │
│  │   ├── GET    /sandboxes/{id}/files/list - List files                      │  │
│  │   ├── GET    /jobs/{id}              - Get job status                     │  │
│  │   └── GET    /health                 - Health check                       │  │
│  └──────────────────┬───────────────────────────────────────────────────────┘  │
│                     │                                                           │
│         ┌───────────┼───────────┬───────────────────────────────┐              │
│         ▼           ▼           ▼                               ▼              │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐           ┌────────────┐        │
│  │  Sandbox   │ │   Exec     │ │    Job     │           │   Redis    │        │
│  │  Manager   │ │  Manager   │ │   Store    │           │   Store    │        │
│  │            │ │            │ │            │           │ (Optional) │        │
│  │ sandbox_   │ │ exec_      │ │ job_       │           │ redis_     │        │
│  │ manager.py │ │ manager.py │ │ store.py   │           │ store.py   │        │
│  └─────┬──────┘ └─────┬──────┘ └────────────┘           └─────┬──────┘        │
│        │              │                                       │               │
│        └──────────────┼───────────────────────────────────────┘               │
│                       ▼                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                        K8s Client (k8s_client.py)                         │  │
│  │                                                                           │  │
│  │   ├── create_namespace()     - Create namespace                           │  │
│  │   ├── delete_namespace()     - Delete namespace                           │  │
│  │   ├── create_pod()           - Create Pod                                 │  │
│  │   ├── delete_pod()           - Delete Pod                                 │  │
│  │   ├── wait_pod_ready()       - Wait for Pod ready                         │  │
│  │   ├── exec_command()         - Execute command in Pod                     │  │
│  │   ├── apply_network_policy() - Apply network policy                       │  │
│  │   ├── copy_file_to_pod()     - Copy file to Pod                           │  │
│  │   └── copy_file_from_pod()   - Copy file from Pod                         │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────┬─────────────────────────────────────────────┘
                                    │ Kubernetes API
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          Azure Kubernetes Service (AKS)                          │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                     Sandbox Namespace (sb-{sandbox_id})                     ││
│  │  ┌────────────────────────────────────────────────────────────────────────┐││
│  │  │                        Sandbox Pod                                     │││
│  │  │  ┌──────────────────────────────────────────────────────────────────┐ │││
│  │  │  │                     Container (User Image)                        │ │││
│  │  │  │                                                                   │ │││
│  │  │  │   /workspace (Working Directory)                                  │ │││
│  │  │  │   - Execute user commands                                         │ │││
│  │  │  │   - File operations                                               │ │││
│  │  │  │   - Git patch application                                         │ │││
│  │  │  │                                                                   │ │││
│  │  │  └──────────────────────────────────────────────────────────────────┘ │││
│  │  └────────────────────────────────────────────────────────────────────────┘││
│  │  ┌────────────────────────────────────────────────────────────────────────┐││
│  │  │                     NetworkPolicy (Optional)                          │││
│  │  │                     - Block egress network access                     │││
│  │  └────────────────────────────────────────────────────────────────────────┘││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │               Orchestrator Namespace (sandbox-orchestrator)                 ││
│  │  ┌────────────────────────────────────────┐  ┌────────────────────────────┐││
│  │  │    Orchestrator Pod (Multi-replica)    │  │    Redis Pod (Optional)    │││
│  │  │                                        │  │                            │││
│  │  │   FastAPI + Uvicorn                    │  │   Redis Server             │││
│  │  │                                        │  │   (For multi-replica       │││
│  │  │                                        │  │    state sharing)          │││
│  │  └────────────────────────────────────────┘  └────────────────────────────┘││
│  └─────────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Flow Diagrams

### 1. Create Sandbox Flow

```
┌──────────┐      ┌──────────────┐      ┌─────────────┐      ┌───────────────┐      ┌─────────┐
│  Client  │      │   FastAPI    │      │  Sandbox    │      │   K8s Client  │      │   AKS   │
│          │      │   (api.py)   │      │  Manager    │      │               │      │         │
└────┬─────┘      └──────┬───────┘      └──────┬──────┘      └───────┬───────┘      └────┬────┘
     │                   │                     │                     │                   │
     │ POST /sandboxes   │                     │                     │                   │
     │──────────────────>│                     │                     │                   │
     │                   │                     │                     │                   │
     │                   │ create_sandbox()    │                     │                   │
     │                   │────────────────────>│                     │                   │
     │                   │                     │                     │                   │
     │                   │                     │ create_namespace()  │                   │
     │                   │                     │────────────────────>│                   │
     │                   │                     │                     │ Create Namespace  │
     │                   │                     │                     │──────────────────>│
     │                   │                     │                     │                   │
     │                   │                     │ apply_network_policy() (if block_network)
     │                   │                     │────────────────────>│                   │
     │                   │                     │                     │ Create NetworkPolicy
     │                   │                     │                     │──────────────────>│
     │                   │                     │                     │                   │
     │                   │                     │ create_pod()        │                   │
     │                   │                     │────────────────────>│                   │
     │                   │                     │                     │ Create Pod        │
     │                   │                     │                     │──────────────────>│
     │                   │                     │                     │                   │
     │                   │                     │                     │<─ Pod Created ────│
     │                   │                     │<────────────────────│                   │
     │                   │                     │                     │                   │
     │                   │ (store sandbox in Redis/Memory)          │                   │
     │                   │<────────────────────│                     │                   │
     │                   │                     │                     │                   │
     │<── Return sandbox_id                    │                     │                   │
     │   (status: pending)                     │                     │                   │
     │                   │                     │                     │                   │
     │ GET /sandboxes/{id} (polling)           │                     │                   │
     │──────────────────>│                     │                     │                   │
     │                   │ get_sandbox()       │                     │                   │
     │                   │────────────────────>│                     │                   │
     │                   │                     │ check pod ready     │                   │
     │                   │                     │────────────────────>│                   │
     │                   │                     │                     │ Get Pod Status    │
     │                   │                     │                     │──────────────────>│
     │                   │                     │                     │<─ Pod Running ────│
     │                   │                     │<────────────────────│                   │
     │<── Return ready: true                   │                     │                   │
     │                   │                     │                     │                   │
```

### 2. Command Execution Flow

```
┌──────────┐      ┌──────────────┐      ┌─────────────┐      ┌───────────────┐      ┌───────────┐      ┌─────────┐
│  Client  │      │   FastAPI    │      │    Exec     │      │   Job Store   │      │ K8s Client│      │   Pod   │
│          │      │   (api.py)   │      │   Manager   │      │               │      │           │      │         │
└────┬─────┘      └──────┬───────┘      └──────┬──────┘      └───────┬───────┘      └─────┬─────┘      └────┬────┘
     │                   │                     │                     │                    │                 │
     │ POST /sandboxes/{id}/exec               │                     │                    │                 │
     │──────────────────>│                     │                     │                    │                 │
     │                   │                     │                     │                    │                 │
     │                   │ submit_exec()       │                     │                    │                 │
     │                   │────────────────────>│                     │                    │                 │
     │                   │                     │                     │                    │                 │
     │                   │                     │ create_job()        │                    │                 │
     │                   │                     │────────────────────>│                    │                 │
     │                   │                     │<────────────────────│                    │                 │
     │                   │                     │                     │                    │                 │
     │<── Return job_id  │                     │                     │                    │                 │
     │                   │                     │                     │                    │                 │
     │                   │                     │ (async) _execute_job()                   │                 │
     │                   │                     │ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─>│                 │
     │                   │                     │                     │                    │                 │
     │                   │                     │                     │ update_job_status  │                 │
     │                   │                     │                     │<─ ─ ─ ─ ─ ─ ─ ─ ─ │                 │
     │                   │                     │                     │    (RUNNING)       │                 │
     │                   │                     │                     │                    │                 │
     │                   │                     │                     │                    │ exec_command()  │
     │                   │                     │                     │                    │────────────────>│
     │                   │                     │                     │                    │                 │
     │                   │                     │                     │                    │<── stdout/stderr│
     │                   │                     │                     │                    │                 │
     │                   │                     │                     │ update_job_status  │                 │
     │                   │                     │                     │<─ ─ ─ ─ ─ ─ ─ ─ ─ │                 │
     │                   │                     │                     │ (SUCCEEDED/FAILED) │                 │
     │                   │                     │                     │                    │                 │
     │ GET /jobs/{job_id} (polling)            │                     │                    │                 │
     │──────────────────>│                     │                     │                    │                 │
     │                   │────────────────────>│                     │                    │                 │
     │                   │                     │ get_job()           │                    │                 │
     │                   │                     │────────────────────>│                    │                 │
     │                   │                     │<────────────────────│                    │                 │
     │<── Return JobResult                     │                     │                    │                 │
     │   (stdout/stderr/exit_code)             │                     │                    │                 │
```

### 3. File Operations Flow

```
┌──────────┐      ┌──────────────┐      ┌─────────────┐      ┌───────────────┐      ┌─────────┐
│  Client  │      │   FastAPI    │      │  Sandbox    │      │   K8s Client  │      │   Pod   │
│          │      │   (api.py)   │      │  Manager    │      │               │      │         │
└────┬─────┘      └──────┬───────┘      └──────┬──────┘      └───────┬───────┘      └────┬────┘
     │                   │                     │                     │                   │
     │ ═══════════════ Upload File ═══════════════════════════════════════════════════════
     │                   │                     │                     │                   │
     │ POST /sandboxes/{id}/files              │                     │                   │
     │ (path + base64 content)                 │                     │                   │
     │──────────────────>│                     │                     │                   │
     │                   │ upload_file()       │                     │                   │
     │                   │────────────────────>│                     │                   │
     │                   │                     │ copy_file_to_pod()  │                   │
     │                   │                     │────────────────────>│                   │
     │                   │                     │                     │ kubectl cp        │
     │                   │                     │                     │──────────────────>│
     │                   │                     │                     │<─────────────────-│
     │                   │                     │<────────────────────│                   │
     │<── {success: true} │                    │                     │                   │
     │                   │                     │                     │                   │
     │ ═══════════════ Download File ═════════════════════════════════════════════════════
     │                   │                     │                     │                   │
     │ GET /sandboxes/{id}/files?path=...      │                     │                   │
     │──────────────────>│                     │                     │                   │
     │                   │ download_file()     │                     │                   │
     │                   │────────────────────>│                     │                   │
     │                   │                     │ copy_file_from_pod()│                   │
     │                   │                     │────────────────────>│                   │
     │                   │                     │                     │ kubectl cp        │
     │                   │                     │                     │──────────────────>│
     │                   │                     │                     │<─────────────────-│
     │                   │                     │<────────────────────│                   │
     │<── {content: base64}                    │                     │                   │
     │                   │                     │                     │                   │
     │ ═══════════════ List Files ════════════════════════════════════════════════════════
     │                   │                     │                     │                   │
     │ GET /sandboxes/{id}/files/list?path=... │                     │                   │
     │──────────────────>│                     │                     │                   │
     │                   │ list_files()        │                     │                   │
     │                   │────────────────────>│                     │                   │
     │                   │                     │ exec_command("ls")  │                   │
     │                   │                     │────────────────────>│                   │
     │                   │                     │                     │ exec in pod       │
     │                   │                     │                     │──────────────────>│
     │                   │                     │                     │<─────────────────-│
     │                   │                     │<────────────────────│                   │
     │<── {files: [...]} │                     │                     │                   │
     │                   │                     │                     │                   │
```

### 4. Delete Sandbox Flow

```
┌──────────┐      ┌──────────────┐      ┌─────────────┐      ┌───────────────┐      ┌─────────┐
│  Client  │      │   FastAPI    │      │  Sandbox    │      │   K8s Client  │      │   AKS   │
│          │      │   (api.py)   │      │  Manager    │      │               │      │         │
└────┬─────┘      └──────┬───────┘      └──────┬──────┘      └───────┬───────┘      └────┬────┘
     │                   │                     │                     │                   │
     │ DELETE /sandboxes/{id}                  │                     │                   │
     │──────────────────>│                     │                     │                   │
     │                   │                     │                     │                   │
     │                   │ delete_sandbox()    │                     │                   │
     │                   │────────────────────>│                     │                   │
     │                   │                     │                     │                   │
     │                   │                     │ delete_namespace()  │                   │
     │                   │                     │────────────────────>│                   │
     │                   │                     │                     │ Delete Namespace  │
     │                   │                     │                     │ (cascade delete)  │
     │                   │                     │                     │──────────────────>│
     │                   │                     │                     │                   │
     │                   │                     │                     │<──── Deleted ─────│
     │                   │                     │<────────────────────│                   │
     │                   │                     │                     │                   │
     │                   │                     │ (remove from Redis/Memory)              │
     │                   │<────────────────────│                     │                   │
     │                   │                     │                     │                   │
     │<── {success: true} │                    │                     │                   │
     │                   │                     │                     │                   │
```

---

## Component Responsibilities

### Client Layer

| Component | File | Responsibility |
|-----------|------|----------------|
| `SandboxClient` | `client/sandbox_client.py` | Synchronous HTTP client with auto-retry and exit cleanup |
| `AsyncSandboxClient` | `client/sandbox_client.py` | Asynchronous HTTP client for concurrent operations |
| `SandboxInstance` | `client/sandbox_client.py` | Single sandbox instance, encapsulates command execution and file operations |
| `JobResult` | `client/sandbox_client.py` | Data class for job execution results |

### Orchestrator Layer

| Component | File | Responsibility |
|-----------|------|----------------|
| `FastAPI App` | `orchestrator/api.py` | REST API entry point, route definitions, request handling |
| `SandboxManager` | `orchestrator/sandbox_manager.py` | Sandbox lifecycle management, state storage |
| `ExecManager` | `orchestrator/exec_manager.py` | Command execution scheduling, concurrency control |
| `JobStore` | `orchestrator/job_store.py` | Job status storage and tracking |
| `K8sClient` | `orchestrator/k8s_client.py` | Kubernetes API wrapper |
| `RedisStore` | `orchestrator/redis_store.py` | Redis backend storage (multi-replica support) |

### Infrastructure Layer

| Component | Location | Responsibility |
|-----------|----------|----------------|
| Kubernetes Namespace | AKS | Sandbox isolation boundary |
| Sandbox Pod | AKS | Container for running user code |
| NetworkPolicy | AKS | Network isolation policy |
| Redis | AKS | Distributed state storage (optional) |

---

## Automatic Cleanup Mechanisms

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                              Cleanup Mechanisms                                 │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                                                │
│  1. Client Exit Cleanup                                                        │
│     ┌─────────────────────────────────────────────────────────────────────┐   │
│     │  - atexit handler: Cleanup on normal program exit                    │   │
│     │  - signal handler: Catch SIGINT/SIGTERM signals                      │   │
│     │  - context manager: Auto cleanup when exiting 'with' statement       │   │
│     └─────────────────────────────────────────────────────────────────────┘   │
│                                                                                │
│  2. Server Background Cleanup                                                  │
│     ┌─────────────────────────────────────────────────────────────────────┐   │
│     │  cleanup_loop() runs periodically:                                   │   │
│     │  - cleanup_expired_sandboxes(): Clean up timed-out sandboxes         │   │
│     │  - cleanup_old_jobs(): Clean up expired job records                  │   │
│     │  - reconcile_sandboxes(): Sync Redis/Memory state with K8s           │   │
│     └─────────────────────────────────────────────────────────────────────┘   │
│                                                                                │
│  3. Kubernetes Cascade Delete                                                  │
│     ┌─────────────────────────────────────────────────────────────────────┐   │
│     │  When deleting a Namespace, automatically deletes:                   │   │
│     │  - Pod                                                               │   │
│     │  - NetworkPolicy                                                     │   │
│     │  - All other resources within the namespace                          │   │
│     └─────────────────────────────────────────────────────────────────────┘   │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## Multi-Replica Deployment Architecture

```
                              ┌─────────────────────┐
                              │    Load Balancer    │
                              │   (K8s Service)     │
                              └──────────┬──────────┘
                                         │
            ┌────────────────────────────┼────────────────────────────┐
            │                            │                            │
            ▼                            ▼                            ▼
   ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
   │  Orchestrator   │         │  Orchestrator   │         │  Orchestrator   │
   │  Pod (Replica 1)│         │  Pod (Replica 2)│         │  Pod (Replica 3)│
   └────────┬────────┘         └────────┬────────┘         └────────┬────────┘
            │                            │                            │
            └────────────────────────────┼────────────────────────────┘
                                         │
                                         ▼
                              ┌─────────────────────┐
                              │    Redis Server     │
                              │ (Shared State Store)│
                              └─────────────────────┘
                                         │
            ┌────────────────────────────┼────────────────────────────┐
            │                            │                            │
            ▼                            ▼                            ▼
   ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
   │   sb-sandbox-1  │         │   sb-sandbox-2  │         │   sb-sandbox-3  │
   │   (Namespace)   │         │   (Namespace)   │         │   (Namespace)   │
   └─────────────────┘         └─────────────────┘         └─────────────────┘
```

---

## Data Flow Summary

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Request Flow                                        │
│                                                                                  │
│   User Code                                                                      │
│      │                                                                           │
│      ▼                                                                           │
│   SandboxClient/AsyncSandboxClient                                               │
│      │                                                                           │
│      │ HTTP Request (JSON)                                                       │
│      ▼                                                                           │
│   FastAPI (api.py)                                                               │
│      │                                                                           │
│      ├──► SandboxManager ──► K8sClient ──► Kubernetes API ──► Namespace/Pod     │
│      │                                                                           │
│      ├──► ExecManager ──► JobStore (track jobs)                                  │
│      │         │                                                                 │
│      │         └──► K8sClient ──► kubectl exec ──► Pod Container                 │
│      │                                                                           │
│      └──► RedisStore (if multi-replica)                                          │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Usage Examples

### Synchronous Client
```python
from client.sandbox_client import SandboxClient

with SandboxClient("http://orchestrator:8000") as client:
    with client.create_sandbox("python:3.11-slim") as sandbox:
        result = sandbox.exec("python -c 'print(1+1)'")
        print(result.stdout)  # "2\n"
```

### Asynchronous Client
```python
from client.sandbox_client import AsyncSandboxClient
import asyncio

async def main():
    async with AsyncSandboxClient("http://orchestrator:8000") as client:
        async with await client.create_sandbox("python:3.11-slim") as sandbox:
            result = await sandbox.exec("python -c 'print(1+1)'")
            print(result.stdout)  # "2\n"

asyncio.run(main())
```

### File Operations
```python
from client.sandbox_client import SandboxClient

with SandboxClient("http://orchestrator:8000") as client:
    with client.create_sandbox("python:3.11-slim") as sandbox:
        # Upload file
        sandbox.upload_content(b"print('hello')", "/workspace/script.py")
        
        # Execute uploaded script
        result = sandbox.exec("python /workspace/script.py")
        print(result.stdout)  # "hello\n"
        
        # List files
        files = sandbox.list_files("/workspace")
        print(files)  # [{"name": "script.py", "type": "file", ...}]
        
        # Download file
        content = sandbox.download_content("/workspace/script.py")
        print(content)  # b"print('hello')"
```

### Concurrent Sandboxes (Async)
```python
from client.sandbox_client import AsyncSandboxClient
import asyncio

async def run_in_sandbox(client, task_id):
    async with await client.create_sandbox("python:3.11-slim") as sandbox:
        result = await sandbox.exec(f"echo 'Task {task_id}'")
        return result.stdout.strip()

async def main():
    async with AsyncSandboxClient("http://orchestrator:8000") as client:
        # Run 5 tasks concurrently
        tasks = [run_in_sandbox(client, i) for i in range(5)]
        results = await asyncio.gather(*tasks)
        print(results)  # ['Task 0', 'Task 1', 'Task 2', 'Task 3', 'Task 4']

asyncio.run(main())
```
