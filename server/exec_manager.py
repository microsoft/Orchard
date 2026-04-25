"""Execution manager for running commands in sandboxes.

Uses the in-pod agent for command execution (direct HTTP to Pod IP),
completely bypassing the K8s API Server on the exec path.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Union

from server.agent_client import AgentClient
from server.job_store import JobStatus, JobStore
from server.k8s_client import K8sClient
from server.sandbox_manager import SandboxManager
from server.settings import settings
from server.utils import generate_job_id

logger = logging.getLogger(__name__)


class ExecManager:
    """Manages command execution in sandboxes."""
    
    def __init__(
        self,
        k8s_client: K8sClient,
        sandbox_manager: SandboxManager,
        job_store,  # JobStore or RedisJobStore - both have same interface
        agent_client: Optional[AgentClient] = None,
    ):
        """Initialize execution manager."""
        self.k8s = k8s_client
        self.sandbox_manager = sandbox_manager
        self.job_store = job_store
        self.agent = agent_client or AgentClient()
    
    async def submit_exec(
        self,
        sandbox_id: str,
        command: Union[str, List[str]],
        timeout_seconds: Optional[int] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        login_shell: bool = False,
    ) -> str:
        """Submit a command for execution."""
        # Validate sandbox exists
        sandbox = await self.sandbox_manager.get_sandbox(sandbox_id)
        if not sandbox:
            raise ValueError(f"Sandbox {sandbox_id} not found")
        
        if not sandbox.ready:
            raise ValueError(f"Sandbox {sandbox_id} is not ready")
        
        # Generate job ID
        job_id = generate_job_id()
        
        # Create job record
        command_str = command if isinstance(command, str) else " ".join(command)
        await self.job_store.create_job(
            job_id=job_id,
            sandbox_id=sandbox_id,
            command=command_str
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
        command: Union[str, List[str]],
        timeout_seconds: int,
        cwd: Optional[str],
        env: Optional[Dict[str, str]],
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
                    error=f"Sandbox {sandbox_id} not found"
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
                job_id=job_id,
                status=JobStatus.FAILED,
                error=str(e)
            )
    
    async def _run_command(
        self,
        job_id: str,
        sandbox_id: str,
        command: Union[str, List[str]],
        timeout_seconds: int,
        cwd: Optional[str],
        env: Optional[Dict[str, str]],
        login_shell: bool = False,
    ) -> None:
        """Run a command via the in-pod agent (direct HTTP, no K8s API)."""
        # Update job status to running
        await self.job_store.update_job_status(
            job_id=job_id,
            status=JobStatus.RUNNING
        )
        
        # Get pod IP from PodWatcher cache (with K8s API fallback)
        pod_ip = await self.sandbox_manager.get_pod_ip(sandbox_id)
        if not pod_ip:
            await self.job_store.update_job_status(
                job_id=job_id,
                status=JobStatus.FAILED,
                error=f"No pod IP available for sandbox {sandbox_id}"
            )
            return
        
        # Prepare command string
        if isinstance(command, list):
            command_str = " ".join(command)
        else:
            command_str = command
        
        # Retry on transient agent connection failures
        max_retries = 3
        last_err = None
        for attempt in range(max_retries):
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
                    error=error
                )
                
                logger.info(
                    f"Job {job_id} completed: exit_code={exit_code}, "
                    f"status={status}"
                )
                return  # success — exit retry loop
            
            except asyncio.TimeoutError:
                logger.error(f"Job {job_id} timed out after {timeout_seconds}s")
                await self.job_store.update_job_status(
                    job_id=job_id,
                    status=JobStatus.FAILED,
                    stdout="",
                    stderr=f"Command exceeded timeout of {timeout_seconds}s and was forcibly terminated",
                    error=f"Execution timed out after {timeout_seconds}s"
                )
                return  # timeout is not retryable
            
            except (ConnectionError, OSError) as e:
                last_err = e
                if attempt < max_retries - 1:
                    wait = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s
                    logger.warning(
                        f"Job {job_id} agent connection failed (attempt {attempt+1}/{max_retries}): {e}. "
                        f"Retrying in {wait}s..."
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Job {job_id} agent connection failed after {max_retries} attempts: {e}")
            
            except Exception as e:
                last_err = e
                logger.error(f"Error running command in job {job_id}: {e}", exc_info=True)
                break  # non-retryable
        
        # All retries exhausted or non-retryable error
        if last_err:
            await self.job_store.update_job_status(
                job_id=job_id,
                status=JobStatus.FAILED,
                error=str(last_err)
            )
    
    async def apply_patch(
        self,
        sandbox_id: str,
        patch: str,
        timeout_seconds: int = 30
    ) -> Dict[str, any]:
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
                    "exit_code": exit_code
                }
            
            except Exception as e:
                logger.error(f"Error applying patch in sandbox {sandbox_id}: {e}")
                return {
                    "success": False,
                    "error": str(e)
                }
