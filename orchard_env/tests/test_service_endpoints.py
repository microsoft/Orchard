"""Route-level tests for sandbox service endpoints.

These drive the real FastAPI app with a real in-process upstream server, so
they exercise the actual proxy path: token minting, allowlist enforcement,
header handling, streaming, and the WebSocket bridge. Only Kubernetes is
faked — the sandbox record and pod IP are supplied directly.
"""

import gzip
import threading
from contextlib import ExitStack, asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, patch

import pytest
import uvicorn
from fastapi import FastAPI, Response, WebSocket
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient

from orchard_env.orchestrator import service_proxy, service_tokens
from orchard_env.orchestrator.sandbox_manager import Sandbox

SANDBOX_ID = "sandbox-test"
API_KEY = "test-api-key"


# ---------------------------------------------------------------------------
# A real upstream service, standing in for a server inside a sandbox
# ---------------------------------------------------------------------------


def _build_upstream_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "healthy"}

    @app.get("/echo")
    def echo(request_value: str = "none"):
        return {"value": request_value}

    @app.post("/body")
    async def body(request: dict):
        return {"received": request}

    @app.get("/stream")
    def stream():
        def chunks():
            for index in range(5):
                yield f"chunk-{index};"

        return StreamingResponse(chunks(), media_type="text/plain")

    @app.get("/")
    def root():
        return PlainTextResponse("root-ok")

    @app.get("/gzipped")
    def gzipped():
        raw = b"compressible-payload;" * 200
        body = gzip.compress(raw)
        return Response(
            content=body,
            media_type="text/plain",
            headers={
                "Content-Encoding": "gzip",
                "Content-Length": str(len(body)),
            },
        )

    @app.get("/teapot")
    def teapot():
        return PlainTextResponse("short and stout", status_code=418)

    @app.websocket("/ws")
    async def websocket_echo(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("text") is not None:
                    if message["text"] == "close-please":
                        await ws.close(code=4200)
                        return
                    await ws.send_text(f"echo:{message['text']}")
                elif message.get("bytes") is not None:
                    await ws.send_bytes(b"bin:" + message["bytes"])
        except Exception:
            pass

    return app


def _ws_implementation() -> str:
    """Pick a WebSocket server implementation uvicorn can actually use."""
    try:
        import websockets  # noqa: F401

        return "websockets"
    except ImportError:
        pass
    try:
        import wsproto  # noqa: F401

        return "wsproto"
    except ImportError:
        return "none"


WS_AVAILABLE = _ws_implementation() != "none"
requires_websockets = pytest.mark.skipif(
    not WS_AVAILABLE,
    reason="needs a uvicorn WebSocket implementation (websockets or wsproto)",
)


@contextmanager
def running_upstream():
    """Run the upstream app on a real loopback port."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    config = uvicorn.Config(
        _build_upstream_app(),
        host="127.0.0.1",
        port=port,
        log_level="error",
        # Pin the WebSocket implementation instead of relying on auto-detection,
        # so a missing `websockets` extra surfaces as an explicit skip rather
        # than a confusing 404 on the upgrade handshake.
        ws=_ws_implementation(),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    import time

    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("upstream server did not start")

    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# ---------------------------------------------------------------------------
# Test harness wiring the app to a fake sandbox on a real upstream port
# ---------------------------------------------------------------------------


@contextmanager
def orchestrator_client(upstream_port: int, exposed_ports=None, enabled=True):
    """Yield a TestClient with the service routes wired to a fake sandbox.

    The app's real lifespan builds Kubernetes and Redis clients, so it is
    replaced with a no-op and the managers it would have created are injected
    directly. That keeps the routes, the proxy client, and the ASGI stack real
    while removing the cluster.
    """
    import orchard_env.orchestrator.api as api_module

    sandbox = Sandbox(
        sandbox_id=SANDBOX_ID,
        namespace="sandbox-pods",
        image="python:3.11-slim",
        pod_name=f"sandbox-{SANDBOX_ID}",
        block_network=True,
        cpu="1",
        memory="1Gi",
        ready=True,
        exposed_ports=list(exposed_ports if exposed_ports is not None else []),
    )

    async def fake_get_sandbox(sandbox_id):
        return sandbox if sandbox_id == SANDBOX_ID else None

    async def fake_expose(sandbox_id, port):
        if sandbox_id != SANDBOX_ID:
            return None
        if port not in sandbox.exposed_ports:
            sandbox.exposed_ports.append(port)
        return list(sandbox.exposed_ports)

    async def fake_revoke(sandbox_id, port):
        if sandbox_id == SANDBOX_ID and port in sandbox.exposed_ports:
            sandbox.exposed_ports.remove(port)
            return True
        return False

    manager = AsyncMock()
    manager.get_sandbox.side_effect = fake_get_sandbox
    manager.expose_service.side_effect = fake_expose
    manager.revoke_service.side_effect = fake_revoke
    manager.get_pod_ip.return_value = "127.0.0.1"
    manager.heartbeat.return_value = True

    proxy_client = service_proxy.ServiceProxyClient()

    @asynccontextmanager
    async def noop_lifespan(_app):
        yield

    previous_lifespan = api_module.app.router.lifespan_context
    previous_manager = getattr(api_module, "sandbox_manager", None)
    previous_proxy = getattr(api_module, "service_proxy_client", None)
    api_module.app.router.lifespan_context = noop_lifespan
    api_module.sandbox_manager = manager
    api_module.service_proxy_client = proxy_client

    try:
        patches = [
            patch.object(api_module.settings, "enable_service_endpoints", enabled),
            patch.object(api_module.settings, "require_api_key", True),
            patch.object(api_module.settings, "api_keys", API_KEY),
            patch.object(
                service_tokens.settings, "service_token_secret", "unit-test-secret"
            ),
            patch.object(service_proxy.settings, "agent_port", 9090),
        ]
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            # The upstream port must not collide with the reserved agent port.
            assert upstream_port != 9090
            with TestClient(api_module.app) as client:
                try:
                    yield client, sandbox
                finally:
                    # Close the pooled session on the loop that created it.
                    client.portal.call(proxy_client.close)
    finally:
        api_module.app.router.lifespan_context = previous_lifespan
        if previous_manager is not None:
            api_module.sandbox_manager = previous_manager
        if previous_proxy is not None:
            api_module.service_proxy_client = previous_proxy


@pytest.fixture
def upstream():
    with running_upstream() as port:
        yield port


AUTH = {"X-API-Key": API_KEY}


# ---------------------------------------------------------------------------
# Exposing and revoking
# ---------------------------------------------------------------------------


class TestServiceLifecycle:
    def test_expose_returns_a_usable_url(self, upstream):
        with orchestrator_client(upstream) as (client, sandbox):
            response = client.post(
                f"/sandboxes/{SANDBOX_ID}/services",
                json={"port": upstream},
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body["port"] == upstream
            assert "/s/" in body["url"]
            assert body["expires_at"] > 0
            assert upstream in sandbox.exposed_ports

    def test_expose_requires_api_key(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            response = client.post(
                f"/sandboxes/{SANDBOX_ID}/services", json={"port": upstream}
            )
            assert response.status_code == 401

    def test_expose_rejects_agent_port(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            response = client.post(
                f"/sandboxes/{SANDBOX_ID}/services", json={"port": 9090}, headers=AUTH
            )
            assert response.status_code == 400
            assert "reserved" in response.json()["detail"]

    def test_expose_unknown_sandbox_is_404(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            response = client.post(
                "/sandboxes/does-not-exist/services",
                json={"port": upstream},
                headers=AUTH,
            )
            assert response.status_code == 404

    def test_expose_is_idempotent(self, upstream):
        with orchestrator_client(upstream) as (client, sandbox):
            for _ in range(3):
                assert (
                    client.post(
                        f"/sandboxes/{SANDBOX_ID}/services",
                        json={"port": upstream},
                        headers=AUTH,
                    ).status_code
                    == 200
                )
            assert sandbox.exposed_ports.count(upstream) == 1

    def test_list_reports_exposed_ports(self, upstream):
        with orchestrator_client(upstream, exposed_ports=[upstream]) as (client, _):
            response = client.get(f"/sandboxes/{SANDBOX_ID}/services", headers=AUTH)
            assert response.status_code == 200
            assert response.json()["ports"] == [upstream]

    def test_revoke_removes_the_port(self, upstream):
        with orchestrator_client(upstream, exposed_ports=[upstream]) as (
            client,
            sandbox,
        ):
            response = client.delete(
                f"/sandboxes/{SANDBOX_ID}/services/{upstream}", headers=AUTH
            )
            assert response.status_code == 200
            assert sandbox.exposed_ports == []

    def test_revoking_an_unexposed_port_is_404(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            response = client.delete(
                f"/sandboxes/{SANDBOX_ID}/services/4321", headers=AUTH
            )
            assert response.status_code == 404

    def test_disabled_feature_hides_the_routes(self, upstream):
        with orchestrator_client(upstream, enabled=False) as (client, _):
            response = client.post(
                f"/sandboxes/{SANDBOX_ID}/services",
                json={"port": upstream},
                headers=AUTH,
            )
            assert response.status_code == 404
            assert "disabled" in response.json()["detail"]

    def test_wait_ready_polls_the_service(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            response = client.post(
                f"/sandboxes/{SANDBOX_ID}/services",
                json={
                    "port": upstream,
                    "wait_ready": True,
                    "health_path": "/health",
                    "ready_timeout": 10,
                },
                headers=AUTH,
            )
            assert response.status_code == 200

    def test_wait_ready_times_out_on_a_dead_service(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            response = client.post(
                f"/sandboxes/{SANDBOX_ID}/services",
                json={
                    "port": upstream,
                    "wait_ready": True,
                    "health_path": "/nope",
                    "ready_timeout": 1,
                },
                headers=AUTH,
            )
            assert response.status_code == 408


class TestServiceUrlHost:
    """Where the URL points decides where the capability token is sent."""

    def test_forwarded_host_is_ignored_by_default(self, upstream):
        """A forged X-Forwarded-Host must not redirect the token off-site."""
        with orchestrator_client(upstream) as (client, _):
            response = client.post(
                f"/sandboxes/{SANDBOX_ID}/services",
                json={"port": upstream},
                headers={**AUTH, "X-Forwarded-Host": "attacker.example.com"},
            )
            assert response.status_code == 200
            assert "attacker.example.com" not in response.json()["url"]

    def test_forwarded_host_used_only_when_trusted(self, upstream):
        import orchard_env.orchestrator.api as api_module

        with orchestrator_client(upstream) as (client, _):
            with patch.object(
                api_module.settings, "service_trust_forwarded_headers", True
            ):
                response = client.post(
                    f"/sandboxes/{SANDBOX_ID}/services",
                    json={"port": upstream},
                    headers={
                        **AUTH,
                        "X-Forwarded-Host": "public.example.com",
                        "X-Forwarded-Proto": "https",
                    },
                )
            assert response.json()["url"].startswith("https://public.example.com/s/")

    def test_configured_base_url_wins(self, upstream):
        """An explicit base URL is authoritative, whatever headers claim."""
        import orchard_env.orchestrator.api as api_module

        with orchestrator_client(upstream) as (client, _):
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(
                        api_module.settings,
                        "service_public_base_url",
                        "https://orchard.example.com",
                    )
                )
                stack.enter_context(
                    patch.object(
                        api_module.settings, "service_trust_forwarded_headers", True
                    )
                )
                response = client.post(
                    f"/sandboxes/{SANDBOX_ID}/services",
                    json={"port": upstream},
                    headers={**AUTH, "X-Forwarded-Host": "attacker.example.com"},
                )
            url = response.json()["url"]
            assert url.startswith("https://orchard.example.com/s/")
            assert "attacker.example.com" not in url


# ---------------------------------------------------------------------------
# HTTP proxying
# ---------------------------------------------------------------------------


def _expose(client, port) -> str:
    response = client.post(
        f"/sandboxes/{SANDBOX_ID}/services", json={"port": port}, headers=AUTH
    )
    assert response.status_code == 200
    # TestClient URLs are absolute; keep only the path so the request routes.
    return "/s/" + response.json()["url"].split("/s/", 1)[1]


class TestHttpProxy:
    def test_get_is_proxied(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            response = client.get(f"{base}/health")
            assert response.status_code == 200
            assert response.json() == {"status": "healthy"}

    def test_proxy_needs_no_api_key(self, upstream):
        """The URL is the credential; header-less clients must work."""
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            assert client.get(f"{base}/health").status_code == 200

    def test_query_string_reaches_upstream(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            response = client.get(f"{base}/echo?request_value=hello")
            assert response.json() == {"value": "hello"}

    def test_post_body_reaches_upstream(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            response = client.post(f"{base}/body", json={"a": 1})
            assert response.json() == {"received": {"a": 1}}

    def test_upstream_status_is_preserved(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            response = client.get(f"{base}/teapot")
            assert response.status_code == 418
            assert response.text == "short and stout"

    def test_streaming_response_is_complete(self, upstream):
        """Regression guard: mis-copied framing headers truncate the body."""
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            response = client.get(f"{base}/stream")
            assert response.status_code == 200
            assert response.text == "chunk-0;chunk-1;chunk-2;chunk-3;chunk-4;"

    def test_unknown_upstream_path_yields_upstream_404(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            assert client.get(f"{base}/missing").status_code == 404

    def test_compressed_response_is_still_decodable(self, upstream):
        """Regression guard: the body is relayed as-is, so Content-Encoding
        must survive. Stripping it while forwarding gzipped bytes hands the
        client undecodable data."""
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            response = client.get(f"{base}/gzipped")
            assert response.status_code == 200
            assert response.text == "compressible-payload;" * 200

    def test_root_of_the_service_url_is_reachable(self, upstream):
        """A bare `curl $URL` must work, not just paths beneath it."""
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            assert client.get(base).text == "root-ok"
            assert client.get(f"{base}/").text == "root-ok"

    def test_unreachable_service_does_not_leak_the_pod_ip(self, upstream):
        """A 502 must not tell an external caller the cluster's internals."""
        import socket

        # A port nothing is listening on, so a real connection error occurs.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            dead_port = probe.getsockname()[1]

        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, dead_port)
            response = client.get(f"{base}/health")

            assert response.status_code == 502
            detail = response.json()["detail"]
            assert detail == "Service unreachable"
            assert "127.0.0.1" not in detail
            assert str(dead_port) not in detail


# ---------------------------------------------------------------------------
# Authorisation on the proxy path
# ---------------------------------------------------------------------------


class TestProxyAuthorisation:
    def test_forged_token_rejected(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            _expose(client, upstream)
            assert client.get("/s/not-a-real-token/health").status_code == 403

    def test_token_for_an_unexposed_port_rejected(self, upstream):
        """A token alone is not enough; the port must still be allowlisted."""
        with orchestrator_client(upstream) as (client, _):
            token, _expiry = service_tokens.mint_token(SANDBOX_ID, upstream)
            assert client.get(f"/s/{token}/health").status_code == 403

    def test_revocation_takes_effect_immediately(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            assert client.get(f"{base}/health").status_code == 200

            assert (
                client.delete(
                    f"/sandboxes/{SANDBOX_ID}/services/{upstream}", headers=AUTH
                ).status_code
                == 200
            )
            # Same, still-unexpired URL must now be refused.
            assert client.get(f"{base}/health").status_code == 403

    def test_expired_token_rejected(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            import time as time_module

            with patch.object(
                service_tokens.time, "time", return_value=time_module.time() + 86400
            ):
                assert client.get(f"{base}/health").status_code == 403

    def test_proxy_disabled_when_feature_off(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
        with orchestrator_client(upstream, exposed_ports=[upstream], enabled=False) as (
            client2,
            _,
        ):
            assert client2.get(f"{base}/health").status_code == 404


# ---------------------------------------------------------------------------
# WebSocket proxying — the reason the capability URL exists
# ---------------------------------------------------------------------------


class TestWebSocketProxy:
    @requires_websockets
    def test_text_frames_round_trip(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            with client.websocket_connect(f"{base}/ws") as ws:
                ws.send_text("hello")
                assert ws.receive_text() == "echo:hello"

    @requires_websockets
    def test_many_frames_round_trip(self, upstream):
        """A persistent session, which is how an RL rollout drives an env."""
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            with client.websocket_connect(f"{base}/ws") as ws:
                for index in range(25):
                    ws.send_text(f"m{index}")
                    assert ws.receive_text() == f"echo:m{index}"

    @requires_websockets
    def test_binary_frames_round_trip(self, upstream):
        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            with client.websocket_connect(f"{base}/ws") as ws:
                ws.send_bytes(b"\x00\x01\x02")
                assert ws.receive_bytes() == b"bin:\x00\x01\x02"

    @requires_websockets
    def test_upstream_close_code_is_propagated(self, upstream):
        """A client must be able to tell a clean close from a proxy failure."""
        from starlette.websockets import WebSocketDisconnect

        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            with client.websocket_connect(f"{base}/ws") as ws:
                ws.send_text("close-please")
                with pytest.raises(WebSocketDisconnect) as excinfo:
                    ws.receive_text()
            assert excinfo.value.code == 4200

    def test_forged_token_refused_before_accept(self, upstream):
        from starlette.websockets import WebSocketDisconnect

        with orchestrator_client(upstream) as (client, _):
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with client.websocket_connect("/s/bogus-token/ws"):
                    pass
            assert excinfo.value.code == 4403

    def test_revoked_port_refuses_new_sockets(self, upstream):
        from starlette.websockets import WebSocketDisconnect

        with orchestrator_client(upstream) as (client, _):
            base = _expose(client, upstream)
            client.delete(f"/sandboxes/{SANDBOX_ID}/services/{upstream}", headers=AUTH)
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with client.websocket_connect(f"{base}/ws"):
                    pass
            assert excinfo.value.code == 4403
