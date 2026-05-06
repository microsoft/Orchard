# Orchard: Architecture, Data Flow & Threat Model

> Generated from source-code analysis — May 2026.

---

## Table of Contents

1. [Object Model](#1-object-model)
2. [Exec & File Data Flow](#2-exec--file-data-flow)
3. [Overall Orchestration Architecture](#3-overall-orchestration-architecture)
4. [Threat Model (STRIDE)](#4-threat-model-stride)

---

## 1. Object Model

Key classes and their relationships across all three layers (SDK, Orchestrator, Agent).

```mermaid
classDiagram
    %% ── SDK layer ────────────────────────────────────────────────
    class SandboxClient {
        +base_url: str
        +api_key: str
        +create_sandbox(image) SandboxInstance
        +_request(method, path, **kwargs)
        +close()
        +__enter__() / __exit__()
    }

    class AsyncSandboxClient {
        +base_url: str
        +api_key: str
        +create_sandbox(image) AsyncSandboxInstance
        +_request(method, path, **kwargs)
        +close()
        +__aenter__() / __aexit__()
    }

    class SandboxInstance {
        +sandbox_id: str
        +namespace: str
        +image: str
        +block_network: bool
        +exec(command, timeout, cwd, env) JobResult
        +upload_file(path, content)
        +download_file(path) bytes
        +list_files(path) list
        +apply_patch(patch)
        +start_heartbeat(interval)
        +stop_heartbeat()
        +delete()
        +__enter__() / __exit__()
    }

    class AsyncSandboxInstance {
        +sandbox_id: str
        +exec(command, ...) JobResult
        +upload_file(path, content)
        +download_file(path) bytes
        +apply_patch(patch)
        +__aenter__() / __aexit__()
    }

    class JobResult {
        +job_id: str
        +sandbox_id: str
        +status: str
        +stdout: str
        +stderr: str
        +exit_code: int
        +succeeded: bool
        +failed: bool
        +is_complete: bool
    }

    SandboxClient --> SandboxInstance : creates
    AsyncSandboxClient --> AsyncSandboxInstance : creates
    SandboxInstance --> JobResult : exec() returns
    AsyncSandboxInstance --> JobResult : exec() returns

    %% ── Orchestrator layer ───────────────────────────────────────
    class FastAPI_App {
        +POST /sandboxes
        +GET  /sandboxes/[id]
        +DELETE /sandboxes/[id]
        +POST /sandboxes/[id]/exec
        +GET  /jobs/[id]
        +POST /sandboxes/[id]/files/upload
        +GET  /sandboxes/[id]/files/download
        +GET  /sandboxes/[id]/files/list
        +POST /sandboxes/[id]/patch
        +POST /sandboxes/[id]/heartbeat
        +GET  /health
        +GET  /resources
    }

    class SandboxManager {
        +_sandboxes: dict
        +_locks: dict
        +_redis_store: RedisSandboxStore
        +create_sandbox(id, image, ...) Sandbox
        +get_sandbox(id) Sandbox
        +delete_sandbox(id)
        +get_sandbox_lock(id) Lock
        +cleanup_expired_sandboxes()
        +reconcile_sandboxes()
        +ensure_sandbox_namespace()
    }

    class Sandbox {
        +sandbox_id: str
        +namespace: str
        +image: str
        +pod_name: str
        +block_network: bool
        +cpu: str
        +memory: str
        +created_at: float
        +ready: bool
        +creation_timeout: int
        +last_heartbeat: float
    }

    class ExecManager {
        +submit_exec(sandbox_id, command, ...) job_id
        +_execute_job(job_id, sandbox_id, ...)
        +_run_command(job_id, sandbox_id, ...)
        +wait_for_job(job_id, timeout) Job
    }

    class JobStore {
        +_jobs: dict
        +_completion_events: dict
        +create_job(job_id, sandbox_id, command) Job
        +get_job(job_id) Job
        +update_job_status(job_id, status, ...)
        +wait_for_completion(job_id, timeout) Job
        +get_jobs_by_sandbox(sandbox_id) list
        +cleanup_old_jobs(ttl_hours)
    }

    class RedisJobStore {
        +redis_url: str
        +_client: Redis
        +connect()
        +create_job(...)
        +get_job(...)
        +update_job_status(...)
        +wait_for_completion(...)
    }

    class K8sClient {
        +_core_v1: CoreV1Api
        +_networking_v1: NetworkingV1Api
        +_api_semaphore: Semaphore
        +_exec_semaphore: Semaphore
        +create_pod(sandbox_id, image, ...) pod
        +delete_pod(pod_name, namespace)
        +get_pod_status(pod_name, namespace) dict
        +list_pods(namespace) list
        +create_network_policy(sandbox_id)
        +delete_network_policy(sandbox_id)
        +get_cluster_resources() dict
    }

    class PodWatcher {
        +_cache: dict[str, PodInfo]
        +_ready_events: dict[str, Event]
        +_failed_sandboxes: set
        +start()
        +stop()
        +get_pod_status(sandbox_id) dict
        +wait_for_ready(sandbox_id, timeout) bool
        +_watch_loop()
    }

    class PodInfo {
        +name: str
        +sandbox_id: str
        +phase: str
        +ready: bool
        +status: str
        +pod_ip: str
        +updated_at: float
    }

    class AgentClient {
        +_session: aiohttp.ClientSession
        +_agent_port: int
        +exec_command(pod_ip, command, ...) tuple
        +upload_file(pod_ip, path, content_b64) dict
        +download_file(pod_ip, path) dict
        +list_files(pod_ip, path) dict
        +close()
    }

    class Job {
        +job_id: str
        +sandbox_id: str
        +command: str
        +status: JobStatus
        +stdout: str
        +stderr: str
        +exit_code: int
        +created_at: float
        +started_at: float
        +completed_at: float
        +error: str
    }

    class Settings {
        +host / port
        +api_keys / require_api_key
        +sandbox_namespace
        +default_cpu / default_memory
        +max_concurrent_execs: int
        +agent_port / agent_pool_size
        +redis_url / use_redis
        +sandbox_ttl_hours
        +heartbeat_timeout_seconds
    }

    FastAPI_App --> SandboxManager : uses
    FastAPI_App --> ExecManager : uses
    FastAPI_App --> AgentClient : uses (file ops)
    SandboxManager --> K8sClient : creates/deletes pods
    SandboxManager --> PodWatcher : reads cached status
    SandboxManager --> Sandbox : manages
    ExecManager --> AgentClient : exec via pod IP
    ExecManager --> JobStore : tracks jobs
    ExecManager --> SandboxManager : looks up sandbox
    PodWatcher --> PodInfo : caches
    JobStore <|-- RedisJobStore : same interface

    %% ── Agent layer (in-pod) ─────────────────────────────────────
    class AgentServer {
        +POST /exec
        +POST /files/upload
        +GET  /files/download
        +GET  /files/list
    }

    class ExecRequest_Agent {
        +command: str
        +timeout: int
        +cwd: str
        +env: dict
        +login_shell: bool
    }

    class ExecResponse_Agent {
        +stdout: str
        +stderr: str
        +exit_code: int
    }

    AgentClient --> AgentServer : HTTP to pod IP
    AgentServer --> ExecRequest_Agent : accepts
    AgentServer --> ExecResponse_Agent : returns
```

---

## 2. Exec & File Data Flow

### 2a. Command Execution (wait=True, completes within server timeout)

```mermaid
sequenceDiagram
    participant U as User Code (SDK)
    participant ORC as Orchestrator API
    participant EM as ExecManager
    participant JS as JobStore / RedisJobStore
    participant AC as AgentClient
    participant AGT as Sandbox Agent (pod)

    U->>ORC: POST /sandboxes/{id}/exec<br/>{command, wait:true, timeout}
    ORC->>EM: submit_exec(sandbox_id, command)
    EM->>JS: create_job(job_id)  → QUEUED
    EM-->>EM: asyncio.create_task(_execute_job)
    EM->>JS: update_job_status → RUNNING
    EM->>AC: exec_command(pod_ip, command, timeout)
    AC->>AGT: POST http://{pod_ip}:8080/exec
    AGT-->>AGT: subprocess.run(command)
    AGT-->>AC: {stdout, stderr, exit_code}
    AC-->>EM: (stdout, stderr, exit_code)
    EM->>JS: update_job_status → SUCCEEDED/FAILED
    JS-->>ORC: wait_for_completion event fires
    ORC-->>U: 200 {job_id, status, stdout, stderr, exit_code}
```

### 2b. Command Execution (server-side timeout — client falls back to polling)

```mermaid
sequenceDiagram
    participant U as User Code (SDK)
    participant ORC as Orchestrator API
    participant EM as ExecManager
    participant JS as JobStore
    participant AC as AgentClient
    participant AGT as Sandbox Agent

    U->>ORC: POST /sandboxes/{id}/exec {wait:true, timeout:T}
    ORC->>EM: submit_exec → job_id
    EM-->>EM: _execute_job running in background
    Note over ORC: server-side wait times out<br/>(status still "running")
    ORC-->>U: 408 or {status:"running", job_id}
    loop Poll until complete
        U->>ORC: GET /jobs/{job_id}
        ORC->>JS: get_job(job_id)
        JS-->>ORC: Job{status}
        ORC-->>U: {status, stdout?, stderr?}
    end
    Note over AGT: command finishes
    AC-->>EM: result
    EM->>JS: SUCCEEDED / FAILED
    U->>ORC: GET /jobs/{job_id}
    ORC-->>U: {status:"succeeded", stdout, stderr, exit_code}
```

### 2c. File Upload / Download Flow

```mermaid
sequenceDiagram
    participant U as User Code (SDK)
    participant ORC as Orchestrator API
    participant SM as SandboxManager
    participant PW as PodWatcher
    participant AC as AgentClient
    participant AGT as Sandbox Agent (pod)

    U->>ORC: POST /sandboxes/{id}/files/upload<br/>{path, content_b64}
    ORC->>SM: get_sandbox(id) → Sandbox{pod_ip}
    SM->>PW: get_pod_status(id) → {pod_ip}
    PW-->>SM: pod_ip (from cache, zero K8s API calls)
    SM-->>ORC: Sandbox
    ORC->>AC: upload_file(pod_ip, path, content_b64)
    AC->>AGT: POST http://{pod_ip}:8080/files/upload
    AGT-->>AGT: base64.decode → write file
    AGT-->>AC: {success, path, size}
    AC-->>ORC: dict
    ORC-->>U: 200 {success, path, size}

    U->>ORC: GET /sandboxes/{id}/files/download?path=...
    ORC->>AC: download_file(pod_ip, path)
    AC->>AGT: GET http://{pod_ip}:8080/files/download?path=...
    AGT-->>AGT: read file → base64.encode
    AGT-->>AC: {content_b64, size}
    AC-->>ORC: dict
    ORC-->>U: 200 {content_b64, size}
```

### 2d. Sandbox Lifecycle (Create → Ready → Delete)

```mermaid
sequenceDiagram
    participant U as User Code (SDK)
    participant ORC as Orchestrator API
    participant SM as SandboxManager
    participant K8S as K8sClient
    participant PW as PodWatcher
    participant KAPI as K8s API Server

    U->>ORC: POST /sandboxes {image, block_network, cpu, memory}
    ORC->>SM: create_sandbox(id, image, ...)
    SM->>K8S: create_pod(sandbox_id, image, ...)
    K8S->>KAPI: POST /api/v1/namespaces/sandbox-pods/pods
    KAPI-->>K8S: Pod created (Pending)
    K8S-->>SM: pod_name
    opt block_network=false
        SM->>K8S: create_network_policy(sandbox_id)
        K8S->>KAPI: POST NetworkPolicy (allow egress)
    end
    SM-->>ORC: Sandbox{status:pending}
    ORC-->>U: 200 {sandbox_id, status:"pending"}

    loop Poll until ready (or use heartbeat)
        U->>ORC: GET /sandboxes/{id}
        ORC->>PW: get_pod_status(id) [cache]
        Note over PW: Watch stream from K8s<br/>updates cache in real-time
        KAPI-->>PW: ADDED/MODIFIED event → pod_ip, ready=true
        PW-->>ORC: {ready:true, pod_ip}
        ORC-->>U: {status:"ready"}
    end

    U->>ORC: DELETE /sandboxes/{id}
    ORC->>SM: delete_sandbox(id)
    SM->>K8S: delete_pod(pod_name)
    K8S->>KAPI: DELETE pod
    opt network policy exists
        SM->>K8S: delete_network_policy(sandbox_id)
    end
    ORC-->>U: 200 {deleted:true}
```

---

## 3. Overall Orchestration Architecture

```mermaid
graph TB
    subgraph Client["Client Layer (pip install orchard)"]
        SDK["SandboxClient / AsyncSandboxClient<br/><i>context-managers, retry, atexit cleanup</i>"]
    end

    subgraph Internet["Network Boundary"]
        LB["Azure Load Balancer<br/>port 443 / 8000"]
    end

    subgraph SysPool["AKS — sys node pool"]
        subgraph OrchestratorNS["Namespace: orchestrator"]
            ORC["Orchestrator Pod(s)<br/>FastAPI + uvicorn<br/><i>X-API-Key auth, request-ID middleware</i>"]
            REDIS["Redis Pod<br/><i>job state + sandbox state<br/>distributed locks</i>"]
            ORC <-->|"job CRUD<br/>sandbox metadata"| REDIS
        end
    end

    subgraph SbxPool["AKS — sbx node pool  (workload=sandbox)"]
        subgraph SandboxNS["Namespace: sandbox-pods"]
            subgraph SBX1["Sandbox Pod (sandbox-{id})"]
                INIT["Init Container<br/><i>agent-injector<br/>copies bundled Python + agent<br/>via emptyDir volume</i>"]
                APP1["User Container<br/><i>any OCI image</i>"]
                AGT1["Agent Process<br/>FastAPI :8080<br/>/exec /files/*"]
                INIT -->|"inject agent binary"| AGT1
            end
            SBX2["Sandbox Pod 2"]
            SBXN["Sandbox Pod N"]

            NP["Calico NetworkPolicy<br/><i>deny-all-egress default<br/>per-sandbox allow when<br/>block_network=false</i>"]
        end
    end

    subgraph K8sCP["Kubernetes Control Plane"]
        KAPI["K8s API Server"]
        ETCD[("etcd")]
        KAPI <--> ETCD
    end

    SDK -->|"HTTPS REST<br/>X-API-Key header"| LB
    LB --> ORC

    ORC -->|"create/delete pods<br/>NetworkPolicy CRUD<br/>list/get resources"| KAPI
    KAPI -->|"Watch stream<br/>(pod events, IPs)"| ORC

    ORC -->|"direct HTTP<br/>pod IP:8080<br/>(bypasses K8s)"| AGT1
    ORC -->|"direct HTTP<br/>pod IP:8080"| SBX2
    ORC -->|"direct HTTP<br/>pod IP:8080"| SBXN

    style Client fill:#dbeafe,stroke:#2563eb
    style SysPool fill:#f0fdf4,stroke:#16a34a
    style SbxPool fill:#fefce8,stroke:#ca8a04
    style K8sCP fill:#fdf4ff,stroke:#9333ea
    style NP fill:#fee2e2,stroke:#dc2626
```

### Background Processes (Orchestrator)

```mermaid
graph LR
    subgraph Orchestrator["Orchestrator — Background Tasks"]
        CL["cleanup_loop<br/><i>every 5 min</i>"]
        PW["PodWatcher<br/><i>Watch/Informer loop<br/>auto-reconnect</i>"]
        HB["Heartbeat Monitor<br/><i>sandbox.last_heartbeat<br/>TTL: 3 min</i>"]
    end

    CL -->|"delete expired sandboxes<br/>(TTL > 2 hrs)"| SM["SandboxManager"]
    CL -->|"delete orphan jobs<br/>(TTL > 1 hr)"| JS["JobStore / Redis"]
    CL -->|"reconcile: delete pods<br/>not in store"| SM
    PW -->|"push updates to cache"| CACHE["PodInfo cache<br/>(pod IP, ready, phase)"]
    HB -->|"mark dead if no<br/>heartbeat for 3 min"| SM
```

---

## 4. Threat Model (STRIDE)

The STRIDE analysis is applied to the principal trust boundaries and data flows in the system.

> **Status of remediations:** The four high-risk findings (T-08/T-09, T-13,
> T-14, T-17) are currently tracked as open issues in
> [known-issues.md](known-issues.md). They are **not** yet remediated.

### Trust Boundaries

```mermaid
graph TB
    TB1["TB-1: Internet → Orchestrator<br/><i>(via Load Balancer)</i>"]
    TB2["TB-2: Orchestrator → K8s API Server<br/><i>(in-cluster RBAC)</i>"]
    TB-3["TB-3: Orchestrator → Sandbox Agent<br/><i>(pod-to-pod, no auth)</i>"]
    TB4["TB-4: Sandbox Agent → Host OS<br/><i>(container isolation)</i>"]
    TB5["TB-5: Orchestrator → Redis<br/><i>(in-cluster, no TLS by default)</i>"]

    style TB1 fill:#fee2e2,stroke:#dc2626
    style TB2 fill:#fef9c3,stroke:#ca8a04
    style TB-3 fill:#fee2e2,stroke:#dc2626
    style TB4 fill:#fee2e2,stroke:#dc2626
    style TB5 fill:#fef9c3,stroke:#ca8a04
```

### STRIDE Threat Table

| ID | Boundary | Category | Threat | Current Mitigation | Residual Risk | Recommendation |
|----|----------|----------|--------|--------------------|---------------|----------------|
| T-01 | TB-1 | **Spoofing** | Attacker impersonates legitimate client by stealing or brute-forcing an API key | `X-API-Key` header checked against a set of pre-shared keys in `settings.api_keys` | Medium | Rotate keys regularly; consider short-lived tokens (JWT/OIDC) over long-lived symmetric keys |
| T-02 | TB-1 | **Tampering** | Man-in-the-middle modifies exec command or file content in transit | TLS via Azure Load Balancer (HTTPS) | Low (if TLS enforced) | Enforce HTTPS; reject plain HTTP at load balancer |
| T-03 | TB-1 | **Repudiation** | Caller denies submitting a malicious command | `X-Request-ID` header logged on every request (structured JSON logs → Log Analytics) | Low | Ensure logs are shipped to an immutable store (Azure Log Analytics / Storage immutable policy) |
| T-04 | TB-1 | **Information Disclosure** | Unhandled exception leaks internal details | Global exception handler returns sanitized `{"error":"Internal server error"}` + detail string | Medium | Strip `detail` from 500 responses in production; only log internally |
| T-05 | TB-1 | **Denial of Service** | Flood of sandbox-create requests exhausts node pool | `max_concurrent_creates` semaphore (default 20); K8s scheduler limits | Medium | Add per-client rate limiting at the API gateway; set hard pod quotas in the `sandbox-pods` namespace |
| T-06 | TB-1 | **Elevation of Privilege** | Client passes crafted `image` referencing a privileged image (e.g., host-mount, `--privileged`) | Pod spec is constructed in `sandbox_manager.py` — no `privileged` flag, no `hostPath` volumes | Low | Enforce `PodSecurity` admission policy (`restricted` standard) on the `sandbox-pods` namespace |
| T-07 | TB-3 | **Spoofing** | Rogue pod pretends to be a legitimate sandbox agent; orchestrator connects to wrong pod IP | Orchestrator resolves pod IP from K8s API/PodWatcher (trusted source) | Low-Medium | Consider mutual TLS between orchestrator and agent; validate pod labels before trusting IP |
| T-08 | TB-3 | **Tampering** | Agent HTTP traffic modified in transit between orchestrator and pod (e.g., compromised node) | Pod-to-pod communication is unencrypted HTTP | **High** | Implement mTLS (e.g., Istio/Linkerd service mesh, or self-signed certs in the agent injector) |
| T-09 | TB-3 | **Information Disclosure** | Attacker on the same Kubernetes node intercepts exec stdout/stderr or uploaded file contents | No encryption on the pod-IP channel | **High** | Same as T-08 — enforce mTLS or encrypt at the application layer |
| T-10 | TB-3 | **Denial of Service** | Orchestrator floods a single sandbox agent with concurrent execs | Per-sandbox `asyncio.Lock` serializes exec; `max_concurrent_execs` semaphore bounds total | Low | Already mitigated well |
| T-11 | TB-3 | **Elevation of Privilege** | Agent executes commands as root inside container; malicious command escapes to host | Container isolation (cgroups/namespaces); no `CAP_SYS_ADMIN`; `block_network=True` by default | Medium | Run agent and user code as a non-root user; apply `seccomp` profile (`RuntimeDefault`); use `readOnlyRootFilesystem` where possible |
| T-12 | TB-4 | **Elevation of Privilege** | Container breakout via kernel exploit (e.g., runc CVE) allows access to host or other pods | Sandbox pods on dedicated `sbx` node pool; node-level isolation | Medium | Enable `gVisor`/`kata-containers` runtime class for sandbox pods; keep node OS and runtime patched |
| T-13 | TB-4 | **Information Disclosure** | Sandbox reads secrets from pod environment variables or mounted service-account tokens | Service-account token auto-mount may expose cluster credentials inside sandbox | **High** | Set `automountServiceAccountToken: false` on sandbox pod specs; strip environment variables before exec |
| T-14 | TB-5 | **Tampering** | Attacker who compromises the cluster injects fake job results into Redis | Redis is in-cluster with no TLS or password by default (`redis://redis-service...`) | **High** | Enable Redis AUTH + TLS; use Kubernetes Network Policy to allow only orchestrator pods to reach Redis |
| T-15 | TB-5 | **Denial of Service** | Redis is flooded or crashed, breaking multi-replica job state | Single Redis instance; no HA mentioned in manifests | Medium | Use Redis Sentinel or Redis Cluster; implement graceful fallback to in-memory store on Redis failure |
| T-16 | TB-2 | **Elevation of Privilege** | Compromised orchestrator pod uses its service account to escalate K8s privileges | RBAC manifest (`rbac.yaml`) exists; scope unknown without reviewing exact rules | Medium | Apply least-privilege RBAC: only `get/list/watch/create/delete` on `pods` and `networkpolicies` in `sandbox-pods` namespace |
| T-17 | TB-1 | **Tampering** | Attacker submits a malformed or path-traversal `path` in file upload/download | Path is passed directly to agent which uses `Path(path)` — potential traversal | **High** | Validate that resolved path stays within an allowed root (e.g., `/workspace`); reject `..` segments server-side before forwarding |
| T-18 | TB-1 | **Information Disclosure** | `GET /resources` or `GET /jobs/{id}` returns data belonging to a different tenant | No per-client tenancy isolation; any valid API key can list/exec any sandbox | Medium | Implement per-API-key sandbox ownership; scope `GET /jobs/{id}` to the authenticated key's sandboxes |

---

### Attack Surface Summary

```mermaid
graph LR
    subgraph HighRisk["High Risk"]
        H1["T-08/T-09: Unencrypted\norchestrator→agent channel"]
        H2["T-13: Service account token\nexposed inside sandbox"]
        H3["T-14: Redis unauthenticated\nand unencrypted"]
        H4["T-17: Path traversal in\nfile upload/download"]
    end

    subgraph MediumRisk["Medium Risk"]
        M1["T-01: Long-lived symmetric API keys"]
        M2["T-05: No per-client rate limiting"]
        M3["T-11: Agent/workload runs as root"]
        M4["T-12: Container breakout risk"]
        M5["T-15: Redis single point of failure"]
        M6["T-16: Overly broad RBAC"]
        M7["T-18: No per-tenant isolation"]
    end

    subgraph LowRisk["Low / Mitigated"]
        L1["T-02: MITM in transit (TLS)"]
        L2["T-03: Repudiation (request IDs + logs)"]
        L3["T-06: Privileged pod (no hostPath/privileged)"]
        L4["T-10: Agent flood (per-sandbox lock)"]
    end

    style HighRisk fill:#fee2e2,stroke:#dc2626
    style MediumRisk fill:#fef9c3,stroke:#ca8a04
    style LowRisk fill:#f0fdf4,stroke:#16a34a
```

---

### Top Remediation Priorities

| Priority | Threat(s) | Action |
|----------|-----------|--------|
| 🔴 P0 | T-08, T-09 | Enable mTLS between orchestrator and sandbox agents (Istio sidecar or self-signed certs baked into injector) |
| 🔴 P0 | T-13 | Set `automountServiceAccountToken: false` in sandbox pod spec |
| 🔴 P0 | T-14 | Enable Redis `requirepass` + TLS; restrict with NetworkPolicy |
| 🔴 P0 | T-17 | Server-side path validation: reject `..` and enforce `/workspace` root before forwarding to agent |
| 🟠 P1 | T-01 | Replace shared API keys with short-lived OIDC tokens or Azure Managed Identity |
| 🟠 P1 | T-11 | Run containers as non-root (`runAsNonRoot: true`, `runAsUser: 1000`) |
| 🟠 P1 | T-12 | Apply `RuntimeClass: gvisor` to sandbox pod specs |
| 🟠 P1 | T-16 | Audit and tighten RBAC to minimum required verbs and resources |
| 🟡 P2 | T-05 | Add API-gateway rate limiting (e.g., NGINX Ingress `limit_req`) |
| 🟡 P2 | T-18 | Scope sandbox/job access to the creating API key |
