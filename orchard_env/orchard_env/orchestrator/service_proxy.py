"""Proxying to a service running inside a sandbox.

Orchard already reaches sandboxes on the fast path by talking straight to the
pod IP: exec, file I/O, and the PTY WebSocket all bypass the Kubernetes API
server. This module extends that to *user* services — an OpenEnv environment
server, an MCP server, a dev server, an evaluation endpoint — so a caller
outside the cluster can drive one over ordinary HTTP and WebSocket.

Two details matter for correctness:

* **Hop-by-hop headers must not be forwarded.** ``Connection``,
  ``Transfer-Encoding``, ``Upgrade`` and friends describe a single hop, so
  copying them onto the next one corrupts framing (RFC 9110 section 7.6.1).
* **The port must be allowlisted per sandbox.** The proxy is a deliberate hole
  in the sandbox boundary, so it only ever opens the ports an operator asked
  for, and never the in-pod agent's own port.

Nothing here imports Kubernetes: the pod IP arrives as a plain string, which
keeps the module unit-testable without a cluster.
"""

import logging
from urllib.parse import urlencode

import aiohttp

from orchard_env.orchestrator.settings import settings

logger = logging.getLogger(__name__)

# Headers that describe a single transport hop rather than the payload. They
# are stripped in both directions (RFC 9110 section 7.6.1).
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)

# Never forwarded upstream: they describe the client's connection to the
# orchestrator, and aiohttp recomputes them for the upstream request.
_REQUEST_DROP_HEADERS = frozenset({"host", "content-length"})

# Responses are forwarded byte for byte (the client session is created with
# `auto_decompress=False`), so `Content-Encoding` and `Content-Length` still
# describe the payload accurately and MUST be preserved — stripping
# `Content-Encoding` while passing compressed bytes through hands the client
# undecodable data. Only hop-by-hop headers are removed.
_RESPONSE_DROP_HEADERS: frozenset[str] = frozenset()


class ServicePortError(ValueError):
    """Raised when a requested port may not be exposed."""


def validate_port(port: int) -> int:
    """Return *port* if it may be exposed, else raise :class:`ServicePortError`.

    The in-pod agent's port is always refused: exposing it would hand the
    caller unauthenticated exec and file access inside the sandbox.
    """
    if not isinstance(port, int) or isinstance(port, bool):
        raise ServicePortError("Port must be an integer")
    if not 1 <= port <= 65535:
        raise ServicePortError(f"Port {port} is outside the valid range 1-65535")
    if port == settings.agent_port:
        raise ServicePortError(
            f"Port {port} is reserved for the in-pod sandbox agent and cannot "
            "be exposed"
        )
    if port in _reserved_ports():
        raise ServicePortError(f"Port {port} is reserved and cannot be exposed")
    return port


def _reserved_ports() -> set[int]:
    """Parse the operator-configured reserved port list."""
    raw = settings.service_reserved_ports
    if not raw:
        return set()
    ports: set[int] = set()
    for chunk in str(raw).replace(",", " ").split():
        try:
            ports.add(int(chunk))
        except ValueError:
            logger.warning(
                "Ignoring non-integer entry in SERVICE_RESERVED_PORTS: %r", chunk
            )
    return ports


def filter_request_headers(headers: dict) -> dict:
    """Strip hop-by-hop and connection-specific headers from a client request."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
        and key.lower() not in _REQUEST_DROP_HEADERS
    }


def filter_response_headers(headers) -> dict:
    """Strip hop-by-hop headers from an upstream response.

    Content headers are deliberately kept: the body is relayed unmodified, so
    ``Content-Encoding`` and ``Content-Length`` still describe it correctly.
    """
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
        and key.lower() not in _RESPONSE_DROP_HEADERS
    }


def build_upstream_url(
    pod_ip: str, port: int, path: str, query_string: str = "", scheme: str = "http"
) -> str:
    """Build the in-cluster URL for a proxied request.

    ``path`` is the remainder after the proxy prefix and may be empty. Query
    strings are passed through verbatim so upstream parsing is unchanged.
    """
    suffix = path if path.startswith("/") else f"/{path}"
    url = f"{scheme}://{pod_ip}:{port}{suffix}"
    if query_string:
        url = f"{url}?{query_string}"
    return url


def build_service_url(base_url: str, token: str) -> str:
    """Build the externally reachable base URL for a service endpoint.

    The token rides in the path rather than the query string so that clients
    which append their own path — an OpenEnv ``EnvClient`` appending ``/ws`` —
    produce a working URL without special-casing.
    """
    return f"{base_url.rstrip('/')}/s/{token}"


class ServiceProxyClient:
    """Forwards HTTP and WebSocket traffic to a service inside a sandbox.

    Holds one pooled :class:`aiohttp.ClientSession`, mirroring
    :class:`~orchard_env.orchestrator.agent_client.AgentClient`, so connections
    to the same pod are reused across requests.
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=settings.service_proxy_pool_size,
                limit_per_host=settings.service_proxy_pool_limit_per_host,
                ttl_dns_cache=0,  # pod IPs are ephemeral; never cache DNS
            )
            self._session = aiohttp.ClientSession(
                connector=connector,
                # Per-request timeouts are applied at the call site; a service
                # may legitimately stream for a long time.
                timeout=aiohttp.ClientTimeout(
                    total=None, connect=settings.service_proxy_connect_timeout
                ),
                auto_decompress=False,  # pass the upstream encoding through untouched
            )
        return self._session

    async def request(
        self,
        method: str,
        pod_ip: str,
        port: int,
        path: str,
        query_string: str = "",
        headers: dict | None = None,
        body: bytes | None = None,
        timeout: float | None = None,
    ) -> aiohttp.ClientResponse:
        """Send a proxied HTTP request and return the streaming response.

        The response is returned before its body is read so large or streaming
        payloads are not buffered in the orchestrator. The caller owns the
        response and must release it.
        """
        session = await self._get_session()
        url = build_upstream_url(pod_ip, port, path, query_string)
        request_timeout = aiohttp.ClientTimeout(
            total=(
                timeout
                if timeout is not None
                else settings.service_proxy_timeout_seconds
            ),
            connect=settings.service_proxy_connect_timeout,
        )
        return await session.request(
            method,
            url,
            headers=headers or {},
            data=body,
            timeout=request_timeout,
            allow_redirects=False,  # redirects are the caller's to interpret
        )

    async def open_websocket(
        self,
        pod_ip: str,
        port: int,
        path: str,
        query_string: str = "",
        headers: dict | None = None,
        protocols: tuple[str, ...] = (),
    ) -> aiohttp.ClientWebSocketResponse:
        """Open a WebSocket to a service inside the sandbox.

        The caller owns the returned socket and must close it. ``heartbeat`` is
        left unset: ping/pong is forwarded end to end so the real client's
        keepalive reaches the real server.
        """
        session = await self._get_session()
        url = build_upstream_url(pod_ip, port, path, query_string, scheme="ws")
        return await session.ws_connect(
            url,
            headers=headers or {},
            protocols=protocols,
            max_msg_size=settings.service_proxy_max_message_bytes,
            autoping=True,
        )

    async def probe(
        self, pod_ip: str, port: int, path: str, timeout: float = 5.0
    ) -> bool:
        """Return True when the service answers *path* with a 2xx status."""
        session = await self._get_session()
        url = build_upstream_url(pod_ip, port, path)
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout, connect=timeout)
            ) as response:
                return 200 <= response.status < 300
        except Exception:
            return False

    async def close(self) -> None:
        """Close the pooled session."""
        if self._session and not self._session.closed:
            await self._session.close()


def encode_query(params: dict) -> str:
    """Encode a mapping as a query string (helper for tests and callers)."""
    return urlencode(params, doseq=True)
