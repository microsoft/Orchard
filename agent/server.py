"""Sandbox Agent — lightweight FastAPI server running inside each sandbox pod.

Accepts commands from the orchestrator over HTTP (Pod IP direct connection),
completely bypassing the K8s API Server for exec/file operations.

Usage (container entrypoint):
    python -m agent.server &
    sleep infinity
"""

import asyncio
import base64
import logging
import os
import shlex
import signal
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sandbox-agent")

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ExecRequest(BaseModel):
    command: str
    timeout: int = 300
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    login_shell: bool = False


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


class UploadFileRequest(BaseModel):
    path: str
    content: str  # base64-encoded
    mode: Optional[str] = None  # e.g. "0755"


class UploadFileResponse(BaseModel):
    success: bool
    path: str
    size: int


class DownloadFileResponse(BaseModel):
    path: str
    content: str  # base64-encoded
    size: int


class FileInfo(BaseModel):
    name: str
    type: str  # "file" or "directory"
    size: int
    modified: str


class ListFilesResponse(BaseModel):
    path: str
    files: List[FileInfo]


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Sandbox Agent", version="0.1.0")

AGENT_PORT = int(os.environ.get("AGENT_PORT", "8080"))


@app.get("/health")
async def health():
    """Health check endpoint — used as readiness probe."""
    return {"status": "ok", "pid": os.getpid()}


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

@app.post("/exec", response_model=ExecResponse)
async def exec_command(request: ExecRequest):
    """Execute a shell command and return stdout, stderr, exit_code."""
    cwd = request.cwd or os.environ.get("WORKING_DIR", "/workspace")
    # Fall back to / if the default cwd doesn't exist (custom images)
    if not os.path.isdir(cwd):
        cwd = "/"
    env = os.environ.copy()
    # Remove agent-specific Python env vars that would pollute user commands.
    # The agent runs with PYTHONHOME/PYTHONPATH pointing to /opt/sandbox-agent,
    # which breaks conda and any other Python in the user's image.
    for key in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH"):
        env.pop(key, None)
    if request.env:
        env.update(request.env)

    # Build the shell command.
    # When login_shell is True, we need the full bashrc/conda environment.
    # Challenge: most bashrc files have a guard at the top:
    #   case $- in *i*) ;; *) return;; esac
    # This causes bashrc to exit immediately in non-interactive shells.
    # The ONLY way to pass that guard is to start bash with `-i` so that
    # $- contains 'i'.  This produces two harmless TTY warnings on stderr:
    #   "bash: cannot set terminal process group ..."
    #   "bash: no job control in this shell"
    # We strip those from the returned stderr.
    # shell = "bash"
    # filter_tty_warnings = False
    # if request.login_shell:
    #     shell_args = [shell, "--login", "-i", "-c", request.command]
    #     filter_tty_warnings = True
    # else:
    #     shell_args = [shell, "-c", request.command]

    shell = "bash"
    filter_tty_warnings = False

    if request.login_shell:
        # bash --login 只读 profile 文件，不读 ~/.bashrc。
        # swebench 等镜像的 conda activate 写在 bashrc 里，需要手动 source。
        # Agent 的 PYTHONHOME/PYTHONPATH 已在上方清除，不会污染 conda。
        wrapped_cmd = 'if [ -f ~/.bashrc ]; then . ~/.bashrc; fi; ' + request.command
        shell_args = [shell, "--login", "-i", "-c", wrapped_cmd]
        filter_tty_warnings = True
    else:
        shell_args = [shell, "-c", request.command]

    try:
        proc = await asyncio.create_subprocess_exec(
            *shell_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=request.timeout,
            )
        except asyncio.TimeoutError:
            # Kill the process tree
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            # Drain remaining output
            stdout_bytes, stderr_bytes = await proc.communicate()
            return ExecResponse(
                stdout=stdout_bytes.decode(errors="replace") if stdout_bytes else "",
                stderr=(stderr_bytes.decode(errors="replace") if stderr_bytes else "")
                    + f"\nCommand timed out after {request.timeout}s and was killed",
                exit_code=124,
            )

        stderr_text = stderr_bytes.decode(errors="replace")
        if filter_tty_warnings:
            # Filter known harmless stderr noise from bash -i and bashrc:
            # 1. TTY warnings from bash -i without a terminal
            # 2. Conda ModuleNotFoundError tracebacks from broken conda in bashrc
            filtered_lines = []
            skip_traceback = False
            for line in stderr_text.splitlines():
                # Skip TTY warnings
                if "cannot set terminal process group" in line:
                    continue
                if "no job control in this shell" in line:
                    continue
                # Skip conda ModuleNotFoundError tracebacks (from bashrc sourcing)
                if line.startswith("Traceback (most recent call last):"):
                    skip_traceback = True
                    continue
                if skip_traceback:
                    if line.startswith("ModuleNotFoundError:") or line.startswith("ImportError:"):
                        skip_traceback = False
                        continue
                    if line.startswith("  ") or line == "":
                        continue  # trace frame lines
                    # Not a trace line — stop skipping, keep this line
                    skip_traceback = False
                filtered_lines.append(line)
            stderr_text = "\n".join(filtered_lines)

        return ExecResponse(
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_text,
            exit_code=proc.returncode or 0,
        )

    except FileNotFoundError as e:
        return ExecResponse(stdout="", stderr=str(e), exit_code=127)
    except Exception as e:
        logger.error(f"exec error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

@app.post("/files/upload", response_model=UploadFileResponse)
async def upload_file(request: UploadFileRequest):
    """Upload a file (base64-encoded content) to the sandbox."""
    try:
        file_content = base64.b64decode(request.content)
        target = Path(request.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file_content)
        if request.mode:
            target.chmod(int(request.mode, 8))
        return UploadFileResponse(success=True, path=request.path, size=len(file_content))
    except Exception as e:
        logger.error(f"upload error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files/download")
async def download_file(path: str):
    """Download a file as base64-encoded content."""
    target = Path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    try:
        file_content = target.read_bytes()
        return DownloadFileResponse(
            path=path,
            content=base64.b64encode(file_content).decode(),
            size=len(file_content),
        )
    except Exception as e:
        logger.error(f"download error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/files/list", response_model=ListFilesResponse)
async def list_files(path: str = "/workspace"):
    """List files in a directory."""
    target = Path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    files: List[FileInfo] = []
    try:
        for entry in sorted(target.iterdir()):
            stat = entry.stat()
            files.append(FileInfo(
                name=entry.name,
                type="directory" if entry.is_dir() else "file",
                size=stat.st_size,
                modified=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
            ))
    except PermissionError:
        pass

    return ListFilesResponse(path=path, files=files)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger.info(f"Starting sandbox agent on port {AGENT_PORT}")
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=AGENT_PORT,
        log_level="warning",  # reduce uvicorn noise
        access_log=False,
        backlog=2048,  # handle burst of concurrent connections from orchestrator
    )


if __name__ == "__main__":
    main()
