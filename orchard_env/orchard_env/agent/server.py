"""Sandbox Agent — lightweight FastAPI server running inside each sandbox pod.

Accepts commands from the orchestrator over HTTP (Pod IP direct connection),
completely bypassing the K8s API Server for exec/file operations.

Usage (container entrypoint):
    python -m orchard_env.agent.server &
    sleep infinity
"""

import asyncio
import base64
import json
import logging
import os
import shlex
import signal
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

# Support both layouts:
#   - running as a script in the injector image: /opt/sandbox-agent/server.py
#     where pty_runner.py is a sibling and the `orchard_env` package is absent
#   - running from the repo as `python -m orchard_env.agent.server`
try:
    from orchard_env.agent.pty_runner import spawn_pty
except ImportError:  # pragma: no cover — runtime fallback in the injector image
    from pty_runner import spawn_pty  # type: ignore

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
    cwd: str | None = None
    env: dict[str, str] | None = None
    login_shell: bool = False


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int


class UploadFileRequest(BaseModel):
    path: str
    content: str  # base64-encoded
    mode: str | None = None  # e.g. "0755"


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
    files: list[FileInfo]


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Sandbox Agent", version="0.1.0")

AGENT_PORT = int(os.environ.get("AGENT_PORT", "9090"))


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
        wrapped_cmd = "if [ -f ~/.bashrc ]; then . ~/.bashrc; fi; " + request.command
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
        except TimeoutError:
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
                    if line.startswith("ModuleNotFoundError:") or line.startswith(
                        "ImportError:"
                    ):
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
        return UploadFileResponse(
            success=True, path=request.path, size=len(file_content)
        )
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

    files: list[FileInfo] = []
    try:
        for entry in sorted(target.iterdir()):
            stat = entry.stat()
            files.append(
                FileInfo(
                    name=entry.name,
                    type="directory" if entry.is_dir() else "file",
                    size=stat.st_size,
                    modified=time.strftime(
                        "%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)
                    ),
                )
            )
    except PermissionError:
        pass

    return ListFilesResponse(path=path, files=files)


# ---------------------------------------------------------------------------
# Streaming PTY exec (WebSocket)
# ---------------------------------------------------------------------------
#
# Wire protocol — all frames are JSON text frames.
#
#   client -> server:
#     init  (sent exactly once, immediately after connect)
#       {"type":"init","cmd":[...],"env":{...}|null,"cwd":"..."|null,
#        "rows":24,"cols":80,"login_shell":false}
#     stdin
#       {"type":"stdin","data":"<base64>"}
#     signal
#       {"type":"signal","sig":"TERM"|"KILL"|"INT"|"HUP"}
#     resize  (accepted but optional for MVP — TIOCSWINSZ best-effort)
#       {"type":"resize","rows":N,"cols":N}
#     close
#       {"type":"close"}                       — request graceful shutdown
#
#   server -> client:
#     ready                                    — child spawned, pid attached
#       {"type":"ready","pid":N}
#     stdout                                   — PTY output (stdout+stderr merged)
#       {"type":"stdout","data":"<base64>"}
#     exit                                     — child reaped
#       {"type":"exit","code":N}               — N may be negative if signalled
#     error
#       {"type":"error","message":"..."}
#
# The server closes the WS after sending ``exit`` or ``error``.

_SIGNAL_NAMES = {
    "TERM": signal.SIGTERM,
    "KILL": signal.SIGKILL,
    "INT": signal.SIGINT,
    "HUP": signal.SIGHUP,
    "QUIT": signal.SIGQUIT,
}


async def _pty_reader(ws: WebSocket, master_fd: int, stop: asyncio.Event) -> None:
    """Stream bytes from the PTY master fd into the WebSocket as JSON frames."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future | None = None

    def _readable() -> None:
        nonlocal fut
        if fut and not fut.done():
            fut.set_result(None)

    loop.add_reader(master_fd, _readable)
    try:
        while not stop.is_set():
            fut = loop.create_future()
            try:
                await asyncio.wait_for(fut, timeout=0.5)
            except TimeoutError:
                continue
            try:
                data = os.read(master_fd, 65536)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return  # master fd closed (child exited)
            if not data:
                return
            try:
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "stdout",
                            "data": base64.b64encode(data).decode("ascii"),
                        }
                    )
                )
            except (WebSocketDisconnect, RuntimeError):
                return
    finally:
        try:
            loop.remove_reader(master_fd)
        except (ValueError, OSError):
            pass


async def _pty_waiter(pid: int, master_fd: int) -> int:
    """Reap the child in a background thread; return its returncode."""

    def _wait() -> int:
        try:
            _, status = os.waitpid(pid, 0)
        except ChildProcessError:
            return -1
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return -1

    return await asyncio.to_thread(_wait)


@app.websocket("/exec/pty")
async def exec_pty(ws: WebSocket) -> None:
    """Run a command under a PTY and stream IO over a WebSocket.

    Lifecycle:
      1. accept WS
      2. read one ``init`` frame
      3. spawn child; send ``ready``
      4. concurrently:
         - read PTY master → send ``stdout`` frames
         - read WS frames → write stdin / forward signals
      5. on child exit, send ``exit`` and close
    """
    await ws.accept()
    proc = None
    stop = asyncio.Event()
    reader_task: asyncio.Task | None = None
    waiter_task: asyncio.Task | None = None
    try:
        # ---- 1. handshake ----------------------------------------------------
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        except TimeoutError:
            await ws.send_text(json.dumps({"type": "error", "message": "init timeout"}))
            await ws.close(code=4400)
            return
        try:
            init = json.loads(raw)
            if init.get("type") != "init":
                raise ValueError("first frame must be type=init")
            cmd = init["cmd"]
            if not isinstance(cmd, list) or not cmd:
                raise ValueError("cmd must be a non-empty list of strings")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            await ws.send_text(
                json.dumps({"type": "error", "message": f"bad init frame: {e}"})
            )
            await ws.close(code=4400)
            return

        env_in = init.get("env") or {}
        cwd = init.get("cwd") or os.environ.get("WORKING_DIR", "/workspace")
        if not os.path.isdir(cwd):
            cwd = "/"
        rows = int(init.get("rows") or 24)
        cols = int(init.get("cols") or 80)
        login_shell = bool(init.get("login_shell", False))

        # Inherit the agent's environment, but strip Python-related vars that
        # would pollute user commands (same logic as the non-PTY /exec route).
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH"):
            env.pop(key, None)
        env.update(env_in)

        # Convert cmd → argv.  If login_shell is requested, wrap with bash --login -i -c.
        if login_shell:
            joined = " ".join(shlex.quote(c) for c in cmd)
            argv = [
                "bash",
                "--login",
                "-i",
                "-c",
                f"if [ -f ~/.bashrc ]; then . ~/.bashrc; fi; {joined}",
            ]
        else:
            argv = list(cmd)

        # ---- 2. spawn --------------------------------------------------------
        try:
            proc = spawn_pty(argv, env=env, cwd=cwd, rows=rows, cols=cols)
        except Exception as e:
            logger.error(f"pty spawn failed: {e}", exc_info=True)
            await ws.send_text(
                json.dumps({"type": "error", "message": f"spawn failed: {e}"})
            )
            await ws.close(code=4500)
            return

        await ws.send_text(json.dumps({"type": "ready", "pid": proc.pid}))

        # ---- 3. concurrent IO ------------------------------------------------
        reader_task = asyncio.create_task(_pty_reader(ws, proc.master_fd, stop))
        waiter_task = asyncio.create_task(_pty_waiter(proc.pid, proc.master_fd))

        async def _client_loop() -> None:
            while True:
                try:
                    msg = await ws.receive_text()
                except WebSocketDisconnect:
                    return
                try:
                    frame = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                kind = frame.get("type")
                if kind == "stdin":
                    try:
                        data = base64.b64decode(frame.get("data", ""))
                        if data:
                            proc.write(data)
                    except (OSError, ValueError):
                        return
                elif kind == "signal":
                    sig = _SIGNAL_NAMES.get(
                        str(frame.get("sig", "TERM")).upper(), signal.SIGTERM
                    )
                    proc.kill(sig)
                elif kind == "resize":
                    try:
                        proc.resize(int(frame["rows"]), int(frame["cols"]))
                    except (OSError, KeyError, ValueError):
                        pass
                elif kind == "close":
                    proc.kill(signal.SIGTERM)
                    return

        client_task = asyncio.create_task(_client_loop())

        # Wait for the child to exit; then stop the other tasks.
        returncode = await waiter_task
        stop.set()
        client_task.cancel()
        # Give the reader a moment to drain final bytes from the master fd.
        try:
            await asyncio.wait_for(reader_task, timeout=1.0)
        except (TimeoutError, asyncio.CancelledError):
            pass

        try:
            await ws.send_text(json.dumps({"type": "exit", "code": returncode}))
        except (WebSocketDisconnect, RuntimeError):
            pass
        await ws.close()

    except WebSocketDisconnect:
        # Client gone — clean up the child if still running.
        if proc is not None:
            proc.kill(signal.SIGTERM)
    except Exception as e:
        logger.error(f"pty session error: {e}", exc_info=True)
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
            await ws.close(code=4500)
        except Exception:
            pass
        if proc is not None:
            proc.kill(signal.SIGTERM)
    finally:
        stop.set()
        if reader_task and not reader_task.done():
            reader_task.cancel()
        if proc is not None:
            proc.close()


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
