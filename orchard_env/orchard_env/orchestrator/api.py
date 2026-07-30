"""FastAPI application and routes."""

import asyncio
import logging
from contextlib import asynccontextmanager

import aiohttp
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Security,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from orchard_env.orchestrator.agent_client import AgentClient
from orchard_env.orchestrator.exec_manager import ExecManager
from orchard_env.orchestrator.job_store import JobStore
from orchard_env.orchestrator.k8s_client import K8sClient
from orchard_env.orchestrator.pod_watcher import PodWatcher
from orchard_env.orchestrator.redis_job_store import RedisJobStore
from orchard_env.orchestrator.sandbox_manager import SandboxManager
from orchard_env.orchestrator.settings import settings
from orchard_env.orchestrator.utils import (
    generate_request_id,
    generate_sandbox_id,
    request_id_var,
    setup_logging,
)

logger = logging.getLogger(__name__)

# API Key security scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """Verify the API key from request header."""
    if not settings.require_api_key:
        return "no-auth"

    valid_keys = settings.get_api_keys_set()
    if not valid_keys:
        logger.warning("No API keys configured but auth is required")
        raise HTTPException(
            status_code=500, detail="Server misconfiguration: no API keys configured"
        )

    if not api_key:
        raise HTTPException(
            status_code=401, detail="Missing API key. Please provide X-API-Key header."
        )

    if api_key not in valid_keys:
        raise HTTPException(status_code=403, detail="Invalid API key")

    return api_key


# Global managers
k8s_client: K8sClient
sandbox_manager: SandboxManager
job_store: JobStore
exec_manager: ExecManager
pod_watcher: PodWatcher
agent_client: AgentClient
cleanup_task: asyncio.Task | None = None


async def cleanup_loop():
    """Background task for cleaning up expired resources."""
    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_seconds)

            # Clean up expired sandboxes
            await sandbox_manager.cleanup_expired_sandboxes()

            # Clean up old jobs
            await job_store.cleanup_old_jobs(settings.orphan_job_ttl_hours)

            # Reconcile sandboxes with Kubernetes
            await sandbox_manager.reconcile_sandboxes()

        except Exception as e:
            logger.error(f"Error in cleanup loop: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global k8s_client, sandbox_manager, job_store, exec_manager, pod_watcher, agent_client, cleanup_task

    # Setup logging
    setup_logging()
    logger.info("Starting Sandbox Orchestrator")

    # Increase default thread pool size for concurrent K8s operations.
    # Each exec runs stream+read in a thread; we need extra headroom
    # beyond max_concurrent_execs for non-exec operations (create/delete pods,
    # check pod status, etc.) that also use asyncio.to_thread.
    import concurrent.futures

    loop = asyncio.get_running_loop()
    thread_pool_size = settings.max_concurrent_execs + 50  # extra headroom
    loop.set_default_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=thread_pool_size)
    )

    # Initialize managers
    k8s_client = K8sClient()
    sandbox_manager = SandboxManager(k8s_client)

    # Ensure the shared sandbox namespace exists
    await sandbox_manager.ensure_sandbox_namespace()

    # Use Redis-based job store for multi-replica support
    if settings.use_redis:
        logger.info("Using Redis-based job store for multi-replica support")
        job_store = RedisJobStore()
        await job_store.connect()
    else:
        logger.info("Using in-memory job store (single replica mode)")
        job_store = JobStore()

    exec_manager = ExecManager(k8s_client, sandbox_manager, job_store)

    # Agent client for direct pod communication (exec / file ops)
    agent_client = AgentClient()

    # Start pod watcher (Watch/Informer pattern)
    pod_watcher = PodWatcher()
    await pod_watcher.start()
    sandbox_manager.set_pod_watcher(pod_watcher)
    logger.info("Started PodWatcher (Watch/Informer)")

    # Start cleanup task
    cleanup_task = asyncio.create_task(cleanup_loop())
    logger.info("Started cleanup background task")

    yield

    # Cleanup
    if agent_client:
        await agent_client.close()

    if pod_watcher:
        await pod_watcher.stop()

    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

    logger.info("Shutting down Sandbox Orchestrator")


app = FastAPI(
    title="Sandbox Orchestrator",
    description="Azure AKS-based sandbox orchestration service",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add request ID to all requests."""
    request_id = generate_request_id()
    request_id_var.set(request_id)

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "request_id": request_id_var.get(""),
        },
    )


# API Models


class CreateSandboxRequest(BaseModel):
    """Request to create a sandbox."""

    image: str = Field(..., description="Container image to use")
    block_network: bool = Field(
        default=True, description="Whether to block network egress"
    )
    sandbox_id: str | None = Field(
        default=None, description="Optional custom sandbox ID"
    )
    cpu: str | None = Field(
        default=None,
        description="CPU request/limit (e.g., '4', '2000m'). Defaults to 4 cores.",
    )
    memory: str | None = Field(
        default=None,
        description="Memory request/limit (e.g., '16Gi', '8Gi'). Defaults to 16Gi.",
    )
    timeout: int | None = Field(
        default=None,
        description="Timeout in seconds for sandbox to become ready. Defaults to 3600.",
    )


class CreateSandboxResponse(BaseModel):
    """Response for sandbox creation."""

    sandbox_id: str
    namespace: str
    image: str
    block_network: bool
    cpu: str
    memory: str
    timeout: int
    status: str = "pending"  # pending, ready, failed


class ExecRequest(BaseModel):
    """Request to execute a command."""

    command: str | list[str] = Field(..., description="Command to execute")
    timeout_seconds: int | None = Field(
        default=None, description="Execution timeout in seconds"
    )
    cwd: str | None = Field(default=None, description="Working directory")
    env: dict[str, str] | None = Field(
        default=None, description="Environment variables"
    )
    login_shell: bool = Field(
        default=False,
        description="Use login shell (bash -lc) instead of regular shell (bash -c)",
    )
    wait: bool = Field(
        default=False,
        description="If true, block until job completes and return full result inline",
    )


class ExecResponse(BaseModel):
    """Response for command execution submission."""

    job_id: str
    status: str = "queued"


class JobResponse(BaseModel):
    """Response for job status query."""

    job_id: str
    sandbox_id: str
    command: str
    status: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    created_at: float
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None


class ApplyPatchRequest(BaseModel):
    """Request to apply a git patch."""

    patch: str = Field(..., description="Unified diff patch content")
    timeout_seconds: int = Field(default=30, description="Timeout in seconds")


class ApplyPatchResponse(BaseModel):
    """Response for patch application."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None


# API Routes


@app.get("/")
async def root():
    """Root endpoint."""
    return {"service": settings.service_name, "version": "0.1.0", "status": "running"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/resources")
async def get_resources(api_key: str = Depends(verify_api_key)):
    """Return cluster resource summary.

    Lists all requested CPU/memory resources and available resources in
    the Kubernetes cluster.  Useful for capacity planning and monitoring.
    """
    try:
        data = await k8s_client.get_cluster_resources()
        return {"status": "ok", **data}
    except Exception as e:
        logger.error(f"Error fetching cluster resources: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch cluster resources: {e}",
        )


@app.post("/sandboxes", response_model=CreateSandboxResponse)
async def create_sandbox(
    request: CreateSandboxRequest, api_key: str = Depends(verify_api_key)
):
    """Create a new sandbox.

    Returns immediately after creating the pod. Client should poll
    GET /sandboxes/{sandbox_id} to check when it's ready.
    This allows proper cancellation - if client cancels, they can
    send DELETE to cleanup the pending sandbox.
    """
    sandbox_id = request.sandbox_id or generate_sandbox_id()
    timeout = request.timeout or 3600

    try:
        logger.info(f"Creating sandbox {sandbox_id} with image {request.image}")

        sandbox = await sandbox_manager.create_sandbox(
            sandbox_id=sandbox_id,
            image=request.image,
            block_network=request.block_network,
            cpu=request.cpu,
            memory=request.memory,
            wait_ready=False,  # Don't wait for pod to be ready
            timeout=timeout,
        )

        return CreateSandboxResponse(
            sandbox_id=sandbox.sandbox_id,
            namespace=sandbox.namespace,
            image=sandbox.image,
            block_network=sandbox.block_network,
            cpu=sandbox.cpu,
            memory=sandbox.memory,
            timeout=timeout,
            status="pending" if not sandbox.ready else "ready",
        )

    except asyncio.CancelledError:
        # Client disconnected - try cleanup
        logger.warning(f"Client disconnected during sandbox creation {sandbox_id}")
        try:
            await sandbox_manager.delete_sandbox(sandbox_id)
        except Exception:
            pass
        raise

    except Exception as e:
        logger.error(f"Error creating sandbox: {e}", exc_info=True)
        # Return 503 for transient K8s API errors so clients can retry
        err_str = str(e)
        if any(
            keyword in err_str
            for keyword in [
                "Connection timed out",
                "Max retries exceeded",
                "timed out",
                "Too Many Requests",
                "429",
                "503",
                "502",
                "RemoteDisconnected",
                "Connection aborted",
                "Connection refused",
                "Connection reset",
            ]
        ):
            raise HTTPException(
                status_code=503,
                detail=f"Service temporarily overloaded, please retry: {e}",
                headers={"Retry-After": "5"},
            )
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sandboxes/{sandbox_id}/exec")
async def exec_command(
    sandbox_id: str, request: ExecRequest, api_key: str = Depends(verify_api_key)
):
    """Execute a command in a sandbox.

    If wait=True, blocks until the job completes and returns full result.
    If wait=False (default), returns immediately with job_id for polling.
    """
    try:
        job_id = await exec_manager.submit_exec(
            sandbox_id=sandbox_id,
            command=request.command,
            timeout_seconds=request.timeout_seconds,
            cwd=request.cwd,
            env=request.env,
            login_shell=request.login_shell,
        )

        if not request.wait:
            return ExecResponse(job_id=job_id)

        # Server-side wait: block until job completes
        exec_timeout = request.timeout_seconds or settings.default_timeout_seconds
        job = await job_store.wait_for_completion(job_id, timeout=exec_timeout + 30)

        if not job:
            return ExecResponse(job_id=job_id)

        return JobResponse(
            job_id=job.job_id,
            sandbox_id=job.sandbox_id,
            command=job.command,
            status=job.status.value,
            stdout=job.stdout,
            stderr=job.stderr,
            exit_code=job.exit_code,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            error=job.error,
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error submitting exec: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, api_key: str = Depends(verify_api_key)):
    """Get job status and results."""
    job = await job_store.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return JobResponse(
        job_id=job.job_id,
        sandbox_id=job.sandbox_id,
        command=job.command,
        status=job.status.value,
        stdout=job.stdout,
        stderr=job.stderr,
        exit_code=job.exit_code,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error=job.error,
    )


@app.get("/jobs/{job_id}/wait")
async def wait_job(
    job_id: str, timeout: int = 300, api_key: str = Depends(verify_api_key)
):
    """Wait for a job to complete (server-side wait).

    Blocks until the job finishes or timeout. Eliminates client-side polling.
    The exec task runs on this same replica, so the local asyncio.Event
    fires instantly when the job completes — zero polling overhead.
    """
    job = await job_store.wait_for_completion(job_id, timeout=timeout)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return JobResponse(
        job_id=job.job_id,
        sandbox_id=job.sandbox_id,
        command=job.command,
        status=job.status.value,
        stdout=job.stdout,
        stderr=job.stderr,
        exit_code=job.exit_code,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error=job.error,
    )


@app.post("/sandboxes/{sandbox_id}/apply_patch", response_model=ApplyPatchResponse)
async def apply_patch(
    sandbox_id: str, request: ApplyPatchRequest, api_key: str = Depends(verify_api_key)
):
    """Apply a git patch in the sandbox."""
    try:
        result = await exec_manager.apply_patch(
            sandbox_id=sandbox_id,
            patch=request.patch,
            timeout_seconds=request.timeout_seconds,
        )

        return ApplyPatchResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error applying patch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sandboxes/{sandbox_id}/heartbeat")
async def sandbox_heartbeat(sandbox_id: str, api_key: str = Depends(verify_api_key)):
    """Send heartbeat for a sandbox to keep it alive.

    Clients should send heartbeats periodically (e.g., every 60s).
    Sandboxes that don't receive heartbeats within the configured timeout
    will be cleaned up automatically.
    """
    success = await sandbox_manager.heartbeat(sandbox_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Sandbox {sandbox_id} not found")
    return {"status": "ok", "sandbox_id": sandbox_id}


@app.delete("/sandboxes/{sandbox_id}")
async def delete_sandbox(sandbox_id: str, api_key: str = Depends(verify_api_key)):
    """Delete a sandbox."""
    try:
        await sandbox_manager.delete_sandbox(sandbox_id)
        return {"status": "deleted", "sandbox_id": sandbox_id}

    except Exception as e:
        logger.error(f"Error deleting sandbox: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sandboxes/{sandbox_id}")
async def get_sandbox(sandbox_id: str, api_key: str = Depends(verify_api_key)):
    """Get sandbox information.

    Uses PodWatcher cache for instant status checks (zero K8s API calls).
    Falls back to direct K8s API call if not in cache.
    """
    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=404, detail=f"Sandbox {sandbox_id} not found")

    # Try cached status from PodWatcher first (zero API calls)
    pod_status = pod_watcher.get_pod_status(sandbox_id)

    if pod_status is None:
        # Not in watch cache yet — fall back to direct check
        sandbox = await sandbox_manager.check_sandbox_ready(sandbox_id)
        if not sandbox:
            raise HTTPException(
                status_code=404, detail=f"Sandbox {sandbox_id} not found"
            )
        pod_status = getattr(sandbox, "_pod_status", None) or {}
    else:
        # Update sandbox ready state from cache if needed
        if pod_status["ready"] and not sandbox.ready:
            sandbox.ready = True
            await sandbox_manager._update_sandbox(sandbox_id, {"ready": True})

    status = pod_status.get("status", "ready" if sandbox.ready else "pending")

    return {
        "sandbox_id": sandbox.sandbox_id,
        "namespace": sandbox.namespace,
        "image": sandbox.image,
        "pod_name": sandbox.pod_name,
        "block_network": sandbox.block_network,
        "cpu": sandbox.cpu,
        "memory": sandbox.memory,
        "created_at": sandbox.created_at,
        "ready": sandbox.ready or pod_status.get("ready", False),
        "status": status,
        "status_message": pod_status.get("message", ""),
    }


@app.get("/sandboxes/{sandbox_id}/wait")
async def wait_sandbox_ready(
    sandbox_id: str, timeout: int = 3600, api_key: str = Depends(verify_api_key)
):
    """Wait for a sandbox to become ready (server-side wait).

    Instead of the client polling GET /sandboxes/{id} every 2 seconds,
    this endpoint blocks until the pod is ready, failed, or timeout.
    Uses PodWatcher events — zero polling, instant notification.

    Args:
        timeout: Maximum seconds to wait (default: 3600)

    Returns:
        Sandbox info with ready=True, or error status.
    """
    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=404, detail=f"Sandbox {sandbox_id} not found")

    try:
        pod_status = await pod_watcher.wait_for_ready(sandbox_id, timeout=timeout)
    except TimeoutError as e:
        raise HTTPException(status_code=408, detail=str(e))

    if pod_status["ready"] and not sandbox.ready:
        sandbox.ready = True
        await sandbox_manager._update_sandbox(sandbox_id, {"ready": True})

    status = pod_status.get("status", "ready" if pod_status["ready"] else "pending")

    return {
        "sandbox_id": sandbox.sandbox_id,
        "namespace": sandbox.namespace,
        "image": sandbox.image,
        "pod_name": sandbox.pod_name,
        "block_network": sandbox.block_network,
        "cpu": sandbox.cpu,
        "memory": sandbox.memory,
        "created_at": sandbox.created_at,
        "ready": pod_status["ready"],
        "status": status,
        "status_message": pod_status.get("message", ""),
    }


# File Operations Models


class UploadFileRequest(BaseModel):
    """Request to upload a file."""

    path: str = Field(..., description="Destination path in the sandbox")
    content: str = Field(..., description="Base64 encoded file content")


class UploadFileResponse(BaseModel):
    """Response for file upload."""

    success: bool
    path: str
    size: int


class DownloadFileResponse(BaseModel):
    """Response for file download."""

    path: str
    content: str  # Base64 encoded
    size: int


class FileInfo(BaseModel):
    """A single entry returned by the file-listing endpoint."""

    name: str
    type: str  # "file" or "directory"
    size: int
    modified: str | None = None


class ListFilesResponse(BaseModel):
    """Response for listing files."""

    path: str
    files: list[FileInfo]


# File Operations Routes


@app.post("/sandboxes/{sandbox_id}/files", response_model=UploadFileResponse)
async def upload_file(
    sandbox_id: str, request: UploadFileRequest, api_key: str = Depends(verify_api_key)
):
    """Upload a file to the sandbox.

    The content should be base64 encoded.
    """
    import base64

    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=404, detail=f"Sandbox {sandbox_id} not found")

    if not sandbox.ready:
        raise HTTPException(
            status_code=400, detail=f"Sandbox {sandbox_id} is not ready"
        )

    try:
        # Decode base64 content
        file_content = base64.b64decode(request.content)

        # Get pod IP and upload via agent
        pod_ip = await sandbox_manager.get_pod_ip(sandbox_id)
        if not pod_ip:
            raise HTTPException(
                status_code=503, detail=f"No pod IP available for sandbox {sandbox_id}"
            )

        await agent_client.upload_file(
            pod_ip=pod_ip,
            path=request.path,
            content_b64=request.content,
        )

        return UploadFileResponse(
            success=True,
            path=request.path,
            size=len(file_content),
        )

    except Exception as e:
        logger.error(f"Error uploading file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sandboxes/{sandbox_id}/files")
async def download_file(
    sandbox_id: str, path: str, api_key: str = Depends(verify_api_key)
):
    """Download a file from the sandbox.

    Returns base64 encoded content.
    """

    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=404, detail=f"Sandbox {sandbox_id} not found")

    if not sandbox.ready:
        raise HTTPException(
            status_code=400, detail=f"Sandbox {sandbox_id} is not ready"
        )

    try:
        # Get pod IP and download via agent
        pod_ip = await sandbox_manager.get_pod_ip(sandbox_id)
        if not pod_ip:
            raise HTTPException(
                status_code=503, detail=f"No pod IP available for sandbox {sandbox_id}"
            )

        result = await agent_client.download_file(pod_ip=pod_ip, path=path)

        return DownloadFileResponse(
            path=path,
            content=result["content"],
            size=result.get("size", 0),
        )

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except Exception as e:
        logger.error(f"Error downloading file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sandboxes/{sandbox_id}/files/list", response_model=ListFilesResponse)
async def list_files(
    sandbox_id: str, path: str = "/workspace", api_key: str = Depends(verify_api_key)
):
    """List files in a directory in the sandbox."""
    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(status_code=404, detail=f"Sandbox {sandbox_id} not found")

    if not sandbox.ready:
        raise HTTPException(
            status_code=400, detail=f"Sandbox {sandbox_id} is not ready"
        )

    try:
        # Get pod IP and list via agent
        pod_ip = await sandbox_manager.get_pod_ip(sandbox_id)
        if not pod_ip:
            raise HTTPException(
                status_code=503, detail=f"No pod IP available for sandbox {sandbox_id}"
            )

        files = await agent_client.list_files(pod_ip=pod_ip, path=path)

        return ListFilesResponse(
            path=path,
            files=files,
        )

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    except Exception as e:
        logger.error(f"Error listing files: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Streaming PTY exec (WebSocket proxy to agent)
# ---------------------------------------------------------------------------
#
# Client connects to:
#   WS /sandboxes/{sandbox_id}/exec/pty?api_key=<key>
# We forward verbatim (text frames in both directions) to the agent's
# WS /exec/pty endpoint on the pod IP.  No frame parsing — the wire
# protocol is the agent's contract; the orchestrator is a dumb pipe.
#
# Auth: WebSocket can't use APIKeyHeader; we accept either the
# ``api_key`` query parameter or the ``Sec-WebSocket-Protocol`` header.


def _verify_ws_api_key(ws: WebSocket) -> bool:
    """Return True if the WS connection is authorized."""
    if not settings.require_api_key:
        return True
    valid_keys = settings.get_api_keys_set()
    if not valid_keys:
        return False
    key = ws.query_params.get("api_key")
    if not key:
        # Also accept Sec-WebSocket-Protocol: api_key.<key> for clients
        # that prefer not to put secrets in URLs.
        proto = ws.headers.get("sec-websocket-protocol", "")
        for p in [x.strip() for x in proto.split(",")]:
            if p.startswith("api_key."):
                key = p[len("api_key.") :]
                break
    return bool(key) and key in valid_keys


@app.websocket("/sandboxes/{sandbox_id}/exec/pty")
async def exec_pty_ws(ws: WebSocket, sandbox_id: str) -> None:
    """Proxy a PTY exec WebSocket to the sandbox agent.

    Forwards text frames in both directions.  Closes both sides on
    either disconnect.
    """
    # Auth
    if not _verify_ws_api_key(ws):
        await ws.close(code=4401)  # custom: unauthorized
        return

    # Resolve sandbox + pod IP BEFORE accepting, so we can reject cleanly.
    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        await ws.close(code=4404)
        return
    if not sandbox.ready:
        await ws.close(code=4409)  # conflict: not ready
        return
    pod_ip = await sandbox_manager.get_pod_ip(sandbox_id)
    if not pod_ip:
        await ws.close(code=4503)
        return

    await ws.accept()

    try:
        agent_ws = await agent_client.open_pty_ws(pod_ip)
    except Exception as e:
        logger.error(f"Failed to open agent WS for {sandbox_id}: {e}")
        try:
            await ws.send_text(f'{{"type":"error","message":"agent unreachable: {e}"}}')
        except Exception:
            pass
        await ws.close(code=4503)
        return

    async def _c2a() -> None:
        """Forward frames client → agent."""
        try:
            while True:
                msg = await ws.receive_text()
                await agent_ws.send_str(msg)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.warning(f"c2a forward error: {e}")
        finally:
            await agent_ws.close()

    async def _a2c() -> None:
        """Forward frames agent → client."""
        try:
            async for msg in agent_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws.send_text(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await ws.send_bytes(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        except Exception as e:
            logger.warning(f"a2c forward error: {e}")
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    c2a = asyncio.create_task(_c2a())
    a2c = asyncio.create_task(_a2c())
    done, pending = await asyncio.wait({c2a, a2c}, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    try:
        await agent_ws.close()
    except Exception:
        pass
