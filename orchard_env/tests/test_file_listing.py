"""Unit tests for the file-listing endpoint response model.

Regression coverage for a bug where ``ListFilesResponse.files`` was typed
``list[dict[str, str]]`` while the in-pod agent returns ``size`` as an int.
Pydantic refused to coerce int -> str, so ``GET /sandboxes/{id}/files/list``
returned HTTP 500 for any non-empty directory (empty ones happened to pass).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# A listing exactly as the in-pod agent produces it (see agent/server.py FileInfo).
AGENT_FILES = [
    {
        "name": "script.py",
        "type": "file",
        "size": 1024,
        "modified": "2026-07-29T10:00:00",
    },
    {
        "name": "src",
        "type": "directory",
        "size": 4096,
        "modified": "2026-07-29T10:00:00",
    },
]


class TestListFilesResponseModel:
    """The response model must accept the agent's payload verbatim."""

    def test_accepts_integer_sizes(self):
        from orchard_env.orchestrator.api import ListFilesResponse

        resp = ListFilesResponse(path="/workspace", files=AGENT_FILES)

        assert resp.files[0].name == "script.py"
        assert resp.files[0].size == 1024
        assert isinstance(resp.files[0].size, int)
        assert resp.files[1].type == "directory"

    def test_modified_is_optional(self):
        """Older agents may omit `modified`."""
        from orchard_env.orchestrator.api import ListFilesResponse

        resp = ListFilesResponse(
            path="/workspace",
            files=[{"name": "a.txt", "type": "file", "size": 0}],
        )

        assert resp.files[0].modified is None

    def test_empty_listing(self):
        from orchard_env.orchestrator.api import ListFilesResponse

        resp = ListFilesResponse(path="/workspace", files=[])

        assert resp.files == []


@pytest.mark.asyncio
class TestListFilesEndpoint:
    """GET /sandboxes/{id}/files/list end-to-end through the ASGI app."""

    async def test_returns_agent_listing(self):
        with (
            patch("orchard_env.orchestrator.api.settings") as mock_settings,
            patch("orchard_env.orchestrator.k8s_client.config"),
            patch("orchard_env.orchestrator.k8s_client.client"),
            patch("orchard_env.orchestrator.k8s_client.settings") as mock_k8s_settings,
        ):
            mock_settings.require_api_key = False
            mock_settings.service_name = "test"
            mock_settings.use_redis = False

            mock_k8s_settings.in_cluster = False
            mock_k8s_settings.k8s_api_pool_size = 10
            mock_k8s_settings.k8s_api_concurrency = 10
            mock_k8s_settings.k8s_exec_concurrency = 10
            mock_k8s_settings.max_concurrent_execs = 10
            mock_k8s_settings.sandbox_namespace = "sandbox-pods"

            import orchard_env.orchestrator.api as api_module

            mock_manager = MagicMock()
            mock_manager.get_sandbox = AsyncMock(
                return_value=MagicMock(ready=True, sandbox_id="s1")
            )
            mock_manager.get_pod_ip = AsyncMock(return_value="10.1.2.3")
            api_module.sandbox_manager = mock_manager

            mock_agent = MagicMock()
            mock_agent.list_files = AsyncMock(return_value=AGENT_FILES)
            api_module.agent_client = mock_agent

            from httpx import ASGITransport, AsyncClient

            transport = ASGITransport(app=api_module.app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/sandboxes/s1/files/list?path=/workspace")

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["path"] == "/workspace"
            assert [f["name"] for f in body["files"]] == ["script.py", "src"]
            assert body["files"][0]["size"] == 1024
