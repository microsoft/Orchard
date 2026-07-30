"""Execution manager for running commands in sandboxes.

Uses the in-pod agent for command execution (direct HTTP to Pod IP),
completely bypassing the K8s API Server on the exec path.
"""

import asyncio
import logging
import random

from orchard_env.orchestrator.agent_client import AgentClient
from orchard_env.orchestrator.job_store import JobStatus
from orchard_env.orchestrator.k8s_client import K8sClient
from orchard_env.orchestrator.sandbox_manager import SandboxManager
from orchard_env.orchestrator.settings import settings
from orchard_env.orchestrator.utils import generate_job_id

logger = logging.getLogger(__name__)


class ExecManager:
    """Manages command execution in sandboxes."""

    def __init__(
        self,
        k8s_client: K8sClient,
        sandbox_manager: SandboxManager,
        job_store,  # JobStore or RedisJobStore - both have same interface
        agent_client: AgentClient | None = None,
    ):
        """Initialize execution manager."""
        self.k8s = k8s_client
        self.sandbox_manager = sandbox_manager
        self.job_store = job_store
        self.agent = agent_client or AgentClient()

    async def submit_exec(
        self,
        sandbox_id: str,
        command: str | list[str],
        timeout_seconds: int | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        login_shell: bool = False,
    ) -> str:
        """Submit a command for execution."""
        # Validate sandbox exists
        sandbox = await self.sandbox_manager.get_sandbox(sandbox_id)
        if not sandbox:
            raise ValueError(f"Sandbox {sandbox_id} not found")

        # Live readiness gate (Plan A): consult PodWatcher's live cache for the
        # agent's CURRENT reachability instead of the latched sandbox.ready
        # flag, which never resets to False even if the agent became
        # unreachable.  Fast path returns instantly when already ready; only a
        # not-ready sandbox waits (event-driven) for it to settle.
        ready = await self.sandbox_manager.wait_until_ready(
            sandbox_id, timeout=settings.agent_ready_wait_seconds
        )
        if not ready:
            raise ValueError(f"Sandbox {sandbox_id} agent not ready")

        # Generate job ID
        job_id = generate_job_id()

        # Create job record
        command_str = command if isinstance(command, str) else " ".join(command)
        await self.job_store.create_job(
            job_id=job_id, sandbox_id=sandbox_id, command=command_str
        )

        # Start execution in background
        asyncio.create_task(
            self._execute_job(
                job_id=job_id,
                sandbox_id=sandbox_id,
                command=command,
                timeout_seconds=timeout_seconds or settings.default_timeout_seconds,
                cwd=cwd,
                env=env,
                login_shell=login_shell,
            )
        )

        logger.info(f"Submitted job {job_id} for sandbox {sandbox_id}")
        return job_id

    async def _execute_job(
        self,
        job_id: str,
        sandbox_id: str,
        command: str | list[str],
        timeout_seconds: int,
        cwd: str | None,
        env: dict[str, str] | None,
        login_shell: bool = False,
    ) -> None:
        """Execute a job (internal)."""
        try:
            # Get sandbox lock to ensure serial execution per sandbox
            sandbox_lock = await self.sandbox_manager.get_sandbox_lock(sandbox_id)
            if not sandbox_lock:
                await self.job_store.update_job_status(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    error=f"Sandbox {sandbox_id} not found",
                )
                return

            # Wait for sandbox lock (serializes execs within one sandbox)
            async with sandbox_lock:
                await self._run_command(
                    job_id=job_id,
                    sandbox_id=sandbox_id,
                    command=command,
                    timeout_seconds=timeout_seconds,
                    cwd=cwd,
                    env=env,
                    login_shell=login_shell,
                )

        except Exception as e:
            logger.error(f"Error executing job {job_id}: {e}", exc_info=True)
            await self.job_store.update_job_status(
                job_id=job_id, status=JobStatus.FAILED, error=str(e)
            )

    async def _run_command(
        self,
        job_id: str,
        sandbox_id: str,
        command: str | list[str],
        timeout_seconds: int,
        cwd: str | None,
        env: dict[str, str] | None,
        login_shell: bool = False,
    ) -> None:
        """Run a command via the in-pod agent (direct HTTP, no K8s API)."""
        # Update job status to running
        await self.job_store.update_job_status(job_id=job_id, status=JobStatus.RUNNING)

        # Get pod IP from PodWatcher cache (with K8s API fallback)
        pod_ip = await self.sandbox_manager.get_pod_ip(sandbox_id)
        if not pod_ip:
            await self.job_store.update_job_status(
                job_id=job_id,
                status=JobStatus.FAILED,
                error=f"No pod IP available for sandbox {sandbox_id}",
            )
            return

        # Prepare command string
        if isinstance(command, list):
            command_str = " ".join(command)
        else:
            command_str = command

        # Retry transient agent connection failures over a window wide enough to
        # cover cold image pull + agent startup (pull median ~19s, max ~55s).
        # Connection failures are retryable; command timeouts are NOT.
        retry_window = settings.exec_connect_retry_window
        backoff_cap = settings.exec_connect_backoff_cap
        deadline = asyncio.get_event_loop().time() + retry_window
        last_err = None
        attempt = 0
        while True:
            try:
                stdout, stderr, exit_code = await self.agent.exec_command(
                    pod_ip=pod_ip,
                    command=command_str,
                    timeout=timeout_seconds,
                    cwd=cwd,
                    env=env,
                    login_shell=login_shell,
                )

                # Exit code 124 indicates timeout
                if exit_code == 124:
                    status = JobStatus.FAILED
                    error = f"Command timed out after {timeout_seconds}s"
                else:
                    status = JobStatus.SUCCEEDED if exit_code == 0 else JobStatus.FAILED
                    error = None

                await self.job_store.update_job_status(
                    job_id=job_id,
                    status=status,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    error=error,
                )

                logger.info(
                    f"Job {job_id} completed: exit_code={exit_code}, "
                    f"status={status}"
                )
                return  # success — exit retry loop

            except TimeoutError:
                logger.error(f"Job {job_id} timed out after {timeout_seconds}s")
                await self.job_store.update_job_status(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    stdout="",
                    stderr=f"Command exceeded timeout of {timeout_seconds}s and was forcibly terminated",
                    error=f"Execution timed out after {timeout_seconds}s",
                )
                return  # timeout is not retryable

            except (ConnectionError, OSError) as e:
                last_err = e
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    logger.error(
                        f"Job {job_id} agent connection failed; retry budget "
                        f"({retry_window}s) exhausted after {attempt} attempts: {e}"
                    )
                    break
                # Exponential backoff with cap + jitter, clamped to the budget.
                backoff = min(0.5 * (2**attempt), backoff_cap)
                backoff += random.uniform(0, 0.3 * backoff)  # jitter
                backoff = min(backoff, remaining)
                attempt += 1
                logger.warning(
                    f"Job {job_id} agent connection failed (attempt {attempt}): {e}. "
                    f"Re-checking readiness, retrying in up to {backoff:.1f}s "
                    f"({remaining:.0f}s of retry budget left)..."
                )
                # Live readiness re-check (cache read) — wait for the agent to
                # settle instead of a blind sleep, then refresh the pod IP in
                # case it changed.
                await self.sandbox_manager.wait_until_ready(sandbox_id, timeout=backoff)
                pod_ip = await self.sandbox_manager.get_pod_ip(sandbox_id) or pod_ip

            except Exception as e:
                last_err = e
                logger.error(
                    f"Error running command in job {job_id}: {e}", exc_info=True
                )
                break  # non-retryable

        # All retries exhausted or non-retryable error
        if last_err:
            await self.job_store.update_job_status(
                job_id=job_id, status=JobStatus.FAILED, error=str(last_err)
            )

    async def apply_patch(
        self, sandbox_id: str, patch: str, timeout_seconds: int = 30
    ) -> dict[str, any]:
        """Apply a git patch in the sandbox via the agent."""
        # Validate sandbox exists
        sandbox = await self.sandbox_manager.get_sandbox(sandbox_id)
        if not sandbox:
            raise ValueError(f"Sandbox {sandbox_id} not found")

        pod_ip = await self.sandbox_manager.get_pod_ip(sandbox_id)
        if not pod_ip:
            raise ValueError(f"No pod IP available for sandbox {sandbox_id}")

        # Write patch to a temp file and apply it
        command = f"""
cat > /tmp/patch.diff << 'PATCH_EOF'
{patch}
PATCH_EOF
cd {settings.default_working_dir}
git apply /tmp/patch.diff
"""

        # Get sandbox lock
        sandbox_lock = await self.sandbox_manager.get_sandbox_lock(sandbox_id)
        if not sandbox_lock:
            raise ValueError(f"Sandbox {sandbox_id} not found")

        async with sandbox_lock:
            try:
                stdout, stderr, exit_code = await self.agent.exec_command(
                    pod_ip=pod_ip,
                    command=command,
                    timeout=timeout_seconds,
                )

                return {
                    "success": exit_code == 0,
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": exit_code,
                }

            except Exception as e:
                logger.error(f"Error applying patch in sandbox {sandbox_id}: {e}")
                return {"success": False, "error": str(e)}
