"""Agent client — communicates with the in-pod sandbox agent over HTTP.

Replaces K8s API Server exec (WebSocket through kubelet) with direct
HTTP calls to the agent running at the pod's IP address, eliminating
K8s API Server as a bottleneck for exec / file operations.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import aiohttp

from orchestrator.settings import settings

logger = logging.getLogger(__name__)


class AgentClient:
    """Async HTTP client for the sandbox agent.

    Maintains a shared aiohttp session with a connection pool so that
    keep-alive connections are reused across calls to the same pod IP.
    """

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._agent_port = settings.agent_port

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazily create the shared aiohttp session."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=settings.agent_pool_size,
                limit_per_host=20,
                ttl_dns_cache=0,  # pod IPs are ephemeral, don't cache DNS
            )
            timeout = aiohttp.ClientTimeout(
                total=None,  # per-request timeout handled below
                connect=settings.agent_connect_timeout,
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
            )
        return self._session

    def _url(self, pod_ip: str, path: str) -> str:
        return f"http://{pod_ip}:{self._agent_port}{path}"

    # ---------------------------------------------------------------
    # Command execution
    # ---------------------------------------------------------------

    async def exec_command(
        self,
        pod_ip: str,
        command: str,
        timeout: int = 300,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        login_shell: bool = False,
    ) -> Tuple[str, str, int]:
        """Execute a command via the agent and return (stdout, stderr, exit_code).

        Raises on HTTP errors or connection failures.
        """
        session = await self._get_session()
        payload = {
            "command": command,
            "timeout": timeout,
            "cwd": cwd,
            "env": env,
            "login_shell": login_shell,
        }

        # Total HTTP timeout = command timeout + buffer for agent overhead
        req_timeout = aiohttp.ClientTimeout(
            total=timeout + 30,
            connect=settings.agent_connect_timeout,
        )

        try:
            async with session.post(
                self._url(pod_ip, "/exec"),
                json=payload,
                timeout=req_timeout,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(
                        f"Agent exec failed (HTTP {resp.status}): {body}"
                    )
                data = await resp.json()
                return data["stdout"], data["stderr"], data["exit_code"]

        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"Agent exec timed out after {timeout}s"
            )
        except aiohttp.ClientError as e:
            raise ConnectionError(f"Agent connection error: {e}") from e

    # ---------------------------------------------------------------
    # File operations
    # ---------------------------------------------------------------

    async def upload_file(
        self,
        pod_ip: str,
        path: str,
        content_b64: str,
    ) -> dict:
        """Upload a file to the sandbox agent."""
        session = await self._get_session()
        payload = {"path": path, "content": content_b64}

        async with session.post(
            self._url(pod_ip, "/files/upload"),
            json=payload,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Agent upload failed (HTTP {resp.status}): {body}")
            return await resp.json()

    async def download_file(self, pod_ip: str, path: str) -> dict:
        """Download a file from the sandbox agent."""
        session = await self._get_session()

        async with session.get(
            self._url(pod_ip, "/files/download"),
            params={"path": path},
        ) as resp:
            if resp.status == 404:
                raise FileNotFoundError(f"File not found: {path}")
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Agent download failed (HTTP {resp.status}): {body}")
            return await resp.json()

    async def list_files(self, pod_ip: str, path: str = "/workspace") -> List[dict]:
        """List files in a directory via the sandbox agent."""
        session = await self._get_session()

        async with session.get(
            self._url(pod_ip, "/files/list"),
            params={"path": path},
        ) as resp:
            if resp.status == 404:
                raise FileNotFoundError(f"Path not found: {path}")
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Agent list_files failed (HTTP {resp.status}): {body}")
            data = await resp.json()
            return data["files"]

    async def health_check(self, pod_ip: str) -> bool:
        """Check if the agent is healthy."""
        session = await self._get_session()
        try:
            async with session.get(
                self._url(pod_ip, "/health"),
                timeout=aiohttp.ClientTimeout(total=5, connect=3),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def close(self):
        """Close the shared session."""
        if self._session and not self._session.closed:
            await self._session.close()
