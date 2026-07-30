"""Streaming process handle for PTY-based sandbox.exec.

Connects to the orchestrator's WebSocket endpoint
``WS /sandboxes/{id}/exec/pty`` and surfaces a Modal-style
``ContainerProcess`` interface.

Wire protocol (JSON text frames, base64-encoded data):
    client -> server: init, stdin, signal, resize, close
    server -> client: ready, stdout, exit, error

See ``agent/server.py`` for full protocol docs.
"""

from __future__ import annotations

import base64
import json
import queue
import threading
import time
from urllib.parse import urlparse, urlunparse


def _ws_url(base_url: str, sandbox_id: str, api_key: str | None) -> str:
    """Build the WS URL from the HTTP base_url."""
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/") + f"/sandboxes/{sandbox_id}/exec/pty"
    query = f"api_key={api_key}" if api_key else ""
    return urlunparse((scheme, parsed.netloc, path, "", query, ""))


def _build_init_frame(
    cmd: list[str],
    *,
    env: dict[str, str] | None,
    workdir: str | None,
    rows: int,
    cols: int,
    login_shell: bool,
) -> str:
    return json.dumps(
        {
            "type": "init",
            "cmd": cmd,
            "env": env or {},
            "cwd": workdir,
            "rows": rows,
            "cols": cols,
            "login_shell": login_shell,
        }
    )


# ---------------------------------------------------------------------------
# Synchronous client (websocket-client + background reader thread)
# ---------------------------------------------------------------------------


class ContainerProcess:
    """Synchronous handle to a PTY process running in a sandbox.

    Returned by ``SandboxInstance.exec(..., pty=True)``.

    Lifecycle:
        proc = sandbox.exec("bash", pty=True)
        proc.write_stdin(b"echo hi\\n")
        out = proc.read(timeout=1.0)        # bytes, possibly partial
        exit_code = proc.wait(timeout=30)   # raises TimeoutError on timeout

    Thread-safety:
        ``read``/``wait`` may be called from any thread.
        ``write_stdin``/``kill``/``close`` may be called concurrently with reads.
    """

    def __init__(
        self,
        ws_url: str,
        init_frame: str,
        *,
        connect_timeout: float = 10.0,
    ):
        try:
            import websocket  # type: ignore  # websocket-client
        except ImportError as e:
            raise ImportError(
                "ContainerProcess (sync) requires the 'websocket-client' package. "
                "Install it with: pip install websocket-client"
            ) from e

        self._ws_mod = websocket
        self._ws = websocket.create_connection(
            ws_url,
            timeout=connect_timeout,
            # Allow large bursts of PTY output.
            max_size=4 * 1024 * 1024,
        )
        # Send init synchronously.
        self._ws.send(init_frame)
        self._lock = threading.Lock()  # serializes ws.send() across threads
        self._stdout_q: queue.Queue[bytes] = queue.Queue()
        self._exit_code: int | None = None
        self._error: str | None = None
        self._exited = threading.Event()
        self._pid: int | None = None
        self._ready = threading.Event()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="pty-reader", daemon=True
        )
        self._reader_thread.start()
        # Block briefly for the ``ready`` frame so callers see ``.pid``.
        self._ready.wait(timeout=connect_timeout)

    # ------- public API ---------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def returncode(self) -> int | None:
        """Process return code if exited, else ``None``.  Negative = signal."""
        return self._exit_code

    def poll(self) -> int | None:
        """Non-blocking exit check."""
        return self._exit_code if self._exited.is_set() else None

    def write_stdin(self, data: bytes | str) -> None:
        """Write bytes to the child's stdin."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        frame = json.dumps(
            {
                "type": "stdin",
                "data": base64.b64encode(data).decode("ascii"),
            }
        )
        with self._lock:
            self._ws.send(frame)

    def read(self, timeout: float | None = None) -> bytes:
        """Read the next chunk of stdout.

        Returns ``b""`` if the process has exited and no buffered output
        remains.  Raises ``TimeoutError`` if no data within ``timeout``.
        """
        try:
            return self._stdout_q.get(timeout=timeout)
        except queue.Empty:
            if self._exited.is_set():
                return b""
            raise TimeoutError("no stdout within timeout")

    def read_all(self, timeout: float | None = None) -> bytes:
        """Read until the process exits (and the pipe drains).

        ``timeout`` bounds the total wait time.
        """
        deadline = None if timeout is None else (time.monotonic() + timeout)
        chunks = []
        while True:
            remaining = (
                None if deadline is None else max(0.0, deadline - time.monotonic())
            )
            try:
                chunk = self._stdout_q.get(timeout=remaining)
            except queue.Empty:
                if self._exited.is_set():
                    break
                raise TimeoutError("read_all timed out")
            if not chunk:
                break
            chunks.append(chunk)
            if self._exited.is_set() and self._stdout_q.empty():
                break
        return b"".join(chunks)

    def kill(self, signal_name: str = "TERM") -> None:
        """Send a signal to the process group."""
        frame = json.dumps({"type": "signal", "sig": signal_name})
        try:
            with self._lock:
                self._ws.send(frame)
        except Exception:
            pass

    def resize(self, rows: int, cols: int) -> None:
        """Resize the PTY window (best-effort)."""
        frame = json.dumps({"type": "resize", "rows": int(rows), "cols": int(cols)})
        try:
            with self._lock:
                self._ws.send(frame)
        except Exception:
            pass

    def wait(self, timeout: float | None = None) -> int:
        """Block until the process exits; return its exit code."""
        if not self._exited.wait(timeout=timeout):
            raise TimeoutError(f"process did not exit within {timeout}s")
        if self._error:
            raise RuntimeError(f"pty session error: {self._error}")
        return self._exit_code if self._exit_code is not None else -1

    def close(self) -> None:
        """Close the WS and reap the reader thread (idempotent)."""
        try:
            with self._lock:
                self._ws.close()
        except Exception:
            pass
        self._exited.set()

    # Context manager
    def __enter__(self) -> ContainerProcess:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if not self._exited.is_set():
                self.kill("TERM")
        finally:
            self.close()

    # ------- internals ----------------------------------------------------

    def _reader_loop(self) -> None:
        try:
            while True:
                msg = self._ws.recv()
                if not msg:
                    break
                try:
                    frame = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                t = frame.get("type")
                if t == "stdout":
                    data = base64.b64decode(frame.get("data", ""))
                    if data:
                        self._stdout_q.put(data)
                elif t == "ready":
                    self._pid = frame.get("pid")
                    self._ready.set()
                elif t == "exit":
                    self._exit_code = int(frame.get("code", -1))
                    break
                elif t == "error":
                    self._error = frame.get("message")
                    break
        except Exception as e:
            if not self._exited.is_set():
                self._error = self._error or str(e)
        finally:
            # Sentinel so any blocked read() unblocks.
            self._stdout_q.put(b"")
            self._exited.set()
            self._ready.set()


# ---------------------------------------------------------------------------
# Async client (aiohttp)
# ---------------------------------------------------------------------------


class AsyncContainerProcess:
    """Async handle to a PTY process running in a sandbox.

    Returned by ``AsyncSandboxInstance.exec(..., pty=True)``.

    Usage:
        proc = await sandbox.exec("bash", pty=True)
        await proc.write_stdin(b"echo hi\\n")
        chunk = await proc.read(timeout=1.0)
        exit_code = await proc.wait()
    """

    def __init__(self):
        # Use ``connect`` classmethod to construct — keeps ``__init__`` sync.
        import asyncio

        self._exit_code: int | None = None
        self._error: str | None = None
        self._exited: asyncio.Event = asyncio.Event()
        self._ready: asyncio.Event = asyncio.Event()
        self._stdout_q: asyncio.Queue[bytes] = asyncio.Queue()
        self._pid: int | None = None
        self._ws = None  # type: ignore  # aiohttp.ClientWebSocketResponse
        self._session = None  # type: ignore  # aiohttp.ClientSession (owned)
        self._reader_task = None  # type: ignore

    @classmethod
    async def connect(
        cls,
        ws_url: str,
        init_frame: str,
        *,
        connect_timeout: float = 10.0,
        session=None,
    ) -> AsyncContainerProcess:
        import asyncio

        import aiohttp

        self = cls()
        self._owns_session = session is None
        if self._owns_session:
            self._session = aiohttp.ClientSession()
        else:
            self._session = session
        try:
            self._ws = await asyncio.wait_for(
                self._session.ws_connect(
                    ws_url,
                    heartbeat=30.0,
                    max_msg_size=4 * 1024 * 1024,
                ),
                timeout=connect_timeout,
            )
        except Exception:
            if self._owns_session:
                await self._session.close()
            raise
        await self._ws.send_str(init_frame)
        self._reader_task = asyncio.create_task(self._reader_loop())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=connect_timeout)
        except TimeoutError:
            await self.close()
            raise
        return self

    # ------- public API ---------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def returncode(self) -> int | None:
        return self._exit_code

    def poll(self) -> int | None:
        return self._exit_code if self._exited.is_set() else None

    async def write_stdin(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        await self._ws.send_str(
            json.dumps(
                {
                    "type": "stdin",
                    "data": base64.b64encode(data).decode("ascii"),
                }
            )
        )

    async def read(self, timeout: float | None = None) -> bytes:
        import asyncio

        try:
            if timeout is None:
                return await self._stdout_q.get()
            return await asyncio.wait_for(self._stdout_q.get(), timeout=timeout)
        except TimeoutError:
            if self._exited.is_set():
                return b""
            raise TimeoutError("no stdout within timeout")

    async def read_all(self, timeout: float | None = None) -> bytes:
        import asyncio

        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else (loop.time() + timeout)
        chunks: list[bytes] = []
        while True:
            remaining = None if deadline is None else max(0.0, deadline - loop.time())
            try:
                if remaining is None:
                    chunk = await self._stdout_q.get()
                else:
                    chunk = await asyncio.wait_for(
                        self._stdout_q.get(), timeout=remaining
                    )
            except TimeoutError:
                if self._exited.is_set():
                    break
                raise TimeoutError("read_all timed out")
            if not chunk:
                break
            chunks.append(chunk)
            if self._exited.is_set() and self._stdout_q.empty():
                break
        return b"".join(chunks)

    async def kill(self, signal_name: str = "TERM") -> None:
        try:
            await self._ws.send_str(json.dumps({"type": "signal", "sig": signal_name}))
        except Exception:
            pass

    async def resize(self, rows: int, cols: int) -> None:
        try:
            await self._ws.send_str(
                json.dumps(
                    {
                        "type": "resize",
                        "rows": int(rows),
                        "cols": int(cols),
                    }
                )
            )
        except Exception:
            pass

    async def wait(self, timeout: float | None = None) -> int:
        import asyncio

        try:
            if timeout is None:
                await self._exited.wait()
            else:
                await asyncio.wait_for(self._exited.wait(), timeout=timeout)
        except TimeoutError:
            raise TimeoutError(f"process did not exit within {timeout}s")
        if self._error:
            raise RuntimeError(f"pty session error: {self._error}")
        return self._exit_code if self._exit_code is not None else -1

    async def close(self) -> None:
        try:
            if self._ws is not None and not self._ws.closed:
                await self._ws.close()
        except Exception:
            pass
        self._exited.set()
        self._ready.set()
        if self._reader_task is not None:
            try:
                await self._reader_task
            except Exception:
                pass
        if (
            self._owns_session
            and self._session is not None
            and not self._session.closed
        ):
            await self._session.close()

    async def __aenter__(self) -> AsyncContainerProcess:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if not self._exited.is_set():
                await self.kill("TERM")
        finally:
            await self.close()

    # ------- internals ----------------------------------------------------

    async def _reader_loop(self) -> None:
        import aiohttp

        try:
            async for msg in self._ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        break
                    continue
                try:
                    frame = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                t = frame.get("type")
                if t == "stdout":
                    data = base64.b64decode(frame.get("data", ""))
                    if data:
                        await self._stdout_q.put(data)
                elif t == "ready":
                    self._pid = frame.get("pid")
                    self._ready.set()
                elif t == "exit":
                    self._exit_code = int(frame.get("code", -1))
                    break
                elif t == "error":
                    self._error = frame.get("message")
                    break
        except Exception as e:
            if not self._exited.is_set():
                self._error = self._error or str(e)
        finally:
            await self._stdout_q.put(b"")
            self._exited.set()
            self._ready.set()
