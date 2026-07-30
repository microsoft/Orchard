"""PTY-based subprocess runner.

Spawns a child under a fresh PTY in its own session so that the entire
process group can be signalled with ``os.killpg``.  The caller drives I/O
on the returned master fd (read/write bytes) and waits on the child via
``os.waitpid``.

Why ``pty.fork()`` instead of ``subprocess + pty.openpty()``?
  ``pty.fork()`` already:
    - creates a new session (``setsid``)
    - opens a controlling terminal on the slave side
    - dup2's the slave fd onto stdin/stdout/stderr in the child
  which is exactly the semantics interactive programs (``claude``, ``vim``,
  ``python -i``) expect when they call ``isatty(0)``.
"""

from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import termios
from dataclasses import dataclass


@dataclass
class PtyProcess:
    pid: int
    master_fd: int

    def write(self, data: bytes) -> int:
        """Write bytes to the PTY master (i.e. the child's stdin)."""
        return os.write(self.master_fd, data)

    def resize(self, rows: int, cols: int) -> None:
        """Set the PTY window size (TIOCSWINSZ)."""
        size = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, size)

    def kill(self, sig: int = signal.SIGTERM) -> None:
        """Signal the entire process group.

        Best-effort: silently ignores ``ProcessLookupError`` if the group
        is already gone.
        """
        try:
            os.killpg(os.getpgid(self.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(self.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    def poll(self) -> int | None:
        """Return exit status if the child has exited, else None.

        Returns the conventional ``returncode`` (negative if killed by
        signal N: -N).
        """
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return -1
        if pid == 0:
            return None
        return _status_to_returncode(status)

    def wait(self) -> int:
        """Block until the child exits and return its returncode."""
        try:
            _, status = os.waitpid(self.pid, 0)
        except ChildProcessError:
            return -1
        return _status_to_returncode(status)

    def close(self) -> None:
        """Close the master fd.  Idempotent."""
        try:
            os.close(self.master_fd)
        except OSError:
            pass


def _status_to_returncode(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return -1


def spawn_pty(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    rows: int = 24,
    cols: int = 80,
) -> PtyProcess:
    """Fork a child under a new PTY and exec ``argv``.

    The child runs in its own session (``pty.fork`` calls ``setsid``) so
    the parent can ``killpg`` the whole group on shutdown.

    Args:
        argv: program + args.  Must be non-empty; ``argv[0]`` is the
            program to ``execvp``.
        env: environment for the child.  If ``None``, inherits the
            agent's environment.
        cwd: working directory in the child.  Falls back to ``/`` if it
            doesn't exist.
        rows, cols: initial PTY window size.

    Returns:
        A :class:`PtyProcess` whose ``master_fd`` you read/write from
        and whose ``pid`` you reap.
    """
    if not argv:
        raise ValueError("argv must not be empty")

    pid, master_fd = pty.fork()
    if pid == 0:
        # ---- Child process ------------------------------------------------
        try:
            if cwd:
                try:
                    os.chdir(cwd)
                except OSError:
                    os.chdir("/")
            # Set initial window size on the controlling tty (fd 0/1/2).
            try:
                size = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(0, termios.TIOCSWINSZ, size)
            except OSError:
                pass
            child_env = env if env is not None else os.environ.copy()
            # Ensure TERM is set so curses-based apps don't crash.
            child_env.setdefault("TERM", "xterm-256color")
            os.execvpe(argv[0], argv, child_env)
        except FileNotFoundError:
            os.write(2, f"pty_runner: command not found: {argv[0]}\n".encode())
            os._exit(127)
        except Exception as e:  # pragma: no cover — defensive
            os.write(2, f"pty_runner: exec failed: {e}\n".encode())
            os._exit(126)

    # ---- Parent process ---------------------------------------------------
    # Make master_fd non-blocking so the asyncio read loop can poll it
    # without ever stalling.
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    return PtyProcess(pid=pid, master_fd=master_fd)
