"""Unit tests for the sandbox service proxy helpers.

Covers the pure logic a reviewer most wants pinned down: which ports may be
opened, which headers must not be forwarded, and how upstream URLs are built.
"""

from unittest.mock import patch

import pytest

from orchard_env.orchestrator import service_proxy
from orchard_env.orchestrator.service_proxy import (
    ServicePortError,
    build_service_url,
    build_upstream_url,
    filter_request_headers,
    filter_response_headers,
    validate_port,
)


class TestValidatePort:
    def test_ordinary_port_allowed(self):
        assert validate_port(8000) == 8000

    @pytest.mark.parametrize("port", [1, 65535])
    def test_boundaries_allowed(self, port):
        assert validate_port(port) == port

    @pytest.mark.parametrize("port", [0, -1, 65536, 99999])
    def test_out_of_range_rejected(self, port):
        with pytest.raises(ServicePortError, match="valid range"):
            validate_port(port)

    def test_agent_port_always_rejected(self):
        """Exposing the agent would hand out unauthenticated exec in the pod."""
        with pytest.raises(ServicePortError, match="reserved for the in-pod"):
            validate_port(service_proxy.settings.agent_port)

    def test_agent_port_rejected_even_when_reconfigured(self):
        with patch.object(service_proxy.settings, "agent_port", 7777):
            with pytest.raises(ServicePortError, match="reserved for the in-pod"):
                validate_port(7777)

    def test_operator_reserved_ports_rejected(self):
        with patch.object(service_proxy.settings, "service_reserved_ports", "22, 6379"):
            with pytest.raises(ServicePortError, match="reserved"):
                validate_port(6379)
            assert validate_port(8000) == 8000

    def test_reserved_list_tolerates_junk(self):
        with patch.object(
            service_proxy.settings, "service_reserved_ports", "22 notaport 6379"
        ):
            assert validate_port(8000) == 8000
            with pytest.raises(ServicePortError):
                validate_port(22)

    @pytest.mark.parametrize("port", ["8000", None, 80.5, True])
    def test_non_integer_rejected(self, port):
        # `True` is int-like in Python; a bool is never a deliberate port.
        with pytest.raises(ServicePortError):
            validate_port(port)


class TestHeaderFiltering:
    def test_hop_by_hop_request_headers_dropped(self):
        filtered = filter_request_headers(
            {
                "Connection": "keep-alive",
                "Upgrade": "websocket",
                "Transfer-Encoding": "chunked",
                "Content-Type": "application/json",
            }
        )
        assert filtered == {"Content-Type": "application/json"}

    def test_host_and_content_length_dropped_from_request(self):
        """Both describe the client's hop; aiohttp recomputes them upstream."""
        filtered = filter_request_headers(
            {"Host": "orchestrator", "Content-Length": "12", "X-Trace": "abc"}
        )
        assert filtered == {"X-Trace": "abc"}

    def test_filtering_is_case_insensitive(self):
        filtered = filter_request_headers({"CoNnEcTiOn": "keep-alive", "A": "b"})
        assert filtered == {"A": "b"}

    def test_response_hop_by_hop_headers_dropped(self):
        filtered = filter_response_headers(
            {
                "Transfer-Encoding": "chunked",
                "Connection": "close",
                "Content-Type": "text/html",
            }
        )
        assert filtered == {"Content-Type": "text/html"}

    def test_content_headers_survive_the_response(self):
        """The body is relayed unmodified, so these still describe it. Dropping
        Content-Encoding would hand the client undecodable compressed bytes."""
        filtered = filter_response_headers(
            {"Content-Length": "100", "Content-Encoding": "gzip"}
        )
        assert filtered == {"Content-Length": "100", "Content-Encoding": "gzip"}

    def test_ordinary_headers_survive_both_directions(self):
        headers = {"Authorization": "Bearer x", "X-Custom": "1"}
        assert filter_request_headers(headers) == headers
        assert filter_response_headers(headers) == headers


class TestUrlBuilding:
    def test_path_without_leading_slash_is_normalised(self):
        assert build_upstream_url("10.0.0.1", 8000, "health") == (
            "http://10.0.0.1:8000/health"
        )

    def test_leading_slash_preserved(self):
        assert build_upstream_url("10.0.0.1", 8000, "/health") == (
            "http://10.0.0.1:8000/health"
        )

    def test_empty_path_becomes_root(self):
        assert build_upstream_url("10.0.0.1", 8000, "") == "http://10.0.0.1:8000/"

    def test_query_string_passed_through_verbatim(self):
        url = build_upstream_url("10.0.0.1", 8000, "/search", "q=a+b&n=1")
        assert url == "http://10.0.0.1:8000/search?q=a+b&n=1"

    def test_ws_scheme_supported(self):
        url = build_upstream_url("10.0.0.1", 8000, "/ws", scheme="ws")
        assert url == "ws://10.0.0.1:8000/ws"

    def test_service_url_puts_token_in_path(self):
        """A client appending its own suffix must produce a working URL."""
        url = build_service_url("https://orchestrator.example.com", "tok123")
        assert url == "https://orchestrator.example.com/s/tok123"
        # This is exactly what an OpenEnv EnvClient does:
        assert f"{url}/ws" == "https://orchestrator.example.com/s/tok123/ws"

    def test_service_url_tolerates_trailing_slash(self):
        assert build_service_url("https://host/", "tok") == "https://host/s/tok"
