"""Job store for tracking execution jobs."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    """Job execution status."""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class Job:
    """Execution job."""
    job_id: str
    sandbox_id: str
    command: str
    status: JobStatus = JobStatus.QUEUED
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None


class JobStore:
    """In-memory job store."""
    
    def __init__(self):
        """Initialize job store."""
        self._jobs: Dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._completion_events: Dict[str, asyncio.Event] = {}
    
    async def create_job(
        self,
        job_id: str,
        sandbox_id: str,
        command: str
    ) -> Job:
        """Create a new job."""
        async with self._lock:
            job = Job(
                job_id=job_id,
                sandbox_id=sandbox_id,
                command=command
            )
            self._jobs[job_id] = job
            self._completion_events[job_id] = asyncio.Event()
            logger.info(f"Created job {job_id} for sandbox {sandbox_id}")
            return job
    
    async def get_job(self, job_id: str) -> Optional[Job]:
        """Get a job by ID."""
        async with self._lock:
            return self._jobs.get(job_id)
    
    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        stdout: str = "",
        stderr: str = "",
        exit_code: Optional[int] = None,
        error: Optional[str] = None
    ) -> None:
        """Update job status."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                logger.warning(f"Job {job_id} not found")
                return
            
            job.status = status
            if stdout:
                job.stdout = stdout
            if stderr:
                job.stderr = stderr
            if exit_code is not None:
                job.exit_code = exit_code
            if error:
                job.error = error
            
            if status == JobStatus.RUNNING and job.started_at is None:
                job.started_at = time.time()
            
            if status in [JobStatus.SUCCEEDED, JobStatus.FAILED]:
                job.completed_at = time.time()
            
            logger.info(f"Updated job {job_id} status to {status}")
        
        # Notify waiters outside lock
        if status in [JobStatus.SUCCEEDED, JobStatus.FAILED]:
            event = self._completion_events.get(job_id)
            if event:
                event.set()
    
    async def delete_job(self, job_id: str) -> None:
        """Delete a job."""
        async with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                self._completion_events.pop(job_id, None)
                logger.info(f"Deleted job {job_id}")
    
    async def get_jobs_by_sandbox(self, sandbox_id: str) -> list[Job]:
        """Get all jobs for a sandbox."""
        async with self._lock:
            return [
                job for job in self._jobs.values()
                if job.sandbox_id == sandbox_id
            ]
    
    async def cleanup_old_jobs(self, max_age_hours: int) -> int:
        """Clean up old completed jobs."""
        async with self._lock:
            now = time.time()
            max_age_seconds = max_age_hours * 3600
            
            to_delete = []
            for job_id, job in self._jobs.items():
                if job.status in [JobStatus.SUCCEEDED, JobStatus.FAILED]:
                    age = now - job.created_at
                    if age > max_age_seconds:
                        to_delete.append(job_id)
            
            for job_id in to_delete:
                del self._jobs[job_id]
            
            if to_delete:
                logger.info(f"Cleaned up {len(to_delete)} old jobs")
            
            return len(to_delete)

    async def wait_for_completion(self, job_id: str, timeout: float = 300) -> Optional[Job]:
        """Wait for a job to complete. Returns the completed Job or None if not found."""
        # Check if already complete
        job = await self.get_job(job_id)
        if job and job.status in [JobStatus.SUCCEEDED, JobStatus.FAILED]:
            return job
        
        event = self._completion_events.get(job_id)
        if not event:
            return job  # Job not found or no event
        
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        
        return await self.get_job(job_id)
