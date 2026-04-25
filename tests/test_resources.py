"""Unit tests for the /resources endpoint and K8sClient.get_cluster_resources."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – lightweight fakes for Kubernetes API objects
# ---------------------------------------------------------------------------

def _make_node(name: str, capacity: dict, allocatable: dict):
    """Build a minimal fake V1Node."""
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(capacity=capacity, allocatable=allocatable),
    )


def _make_pod(name: str, phase: str, containers: list):
    """Build a minimal fake V1Pod.

    *containers* is a list of dicts like ``{"cpu": "4", "memory": "16Gi"}``.
    """
    fake_containers = []
    for c in containers:
        requests = {}
        if "cpu" in c:
            requests["cpu"] = c["cpu"]
        if "memory" in c:
            requests["memory"] = c["memory"]
        fake_containers.append(
            SimpleNamespace(
                resources=SimpleNamespace(requests=requests if requests else None)
            )
        )
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(phase=phase),
        spec=SimpleNamespace(containers=fake_containers),
    )


# ---------------------------------------------------------------------------
# Tests for _parse_cpu / _parse_memory
# ---------------------------------------------------------------------------

class TestParseCpu:
    """K8sClient._parse_cpu static-method tests."""

    def test_integer(self):
        from orchestrator.k8s_client import K8sClient
        assert K8sClient._parse_cpu("4") == 4.0

    def test_float(self):
        from orchestrator.k8s_client import K8sClient
        assert K8sClient._parse_cpu("0.5") == 0.5

    def test_millicpu(self):
        from orchestrator.k8s_client import K8sClient
        assert K8sClient._parse_cpu("500m") == 0.5

    def test_millicpu_small(self):
        from orchestrator.k8s_client import K8sClient
        assert K8sClient._parse_cpu("100m") == pytest.approx(0.1)


class TestParseMemory:
    """K8sClient._parse_memory static-method tests."""

    def test_gi(self):
        from orchestrator.k8s_client import K8sClient
        assert K8sClient._parse_memory("16Gi") == 16 * 1024 ** 3

    def test_mi(self):
        from orchestrator.k8s_client import K8sClient
        assert K8sClient._parse_memory("512Mi") == 512 * 1024 ** 2

    def test_ki(self):
        from orchestrator.k8s_client import K8sClient
        assert K8sClient._parse_memory("1024Ki") == 1024 * 1024

    def test_plain_bytes(self):
        from orchestrator.k8s_client import K8sClient
        assert K8sClient._parse_memory("1048576") == 1048576


# ---------------------------------------------------------------------------
# Tests for get_cluster_resources
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetClusterResources:
    """Tests for K8sClient.get_cluster_resources."""

    async def test_returns_correct_summary(self):
        """Aggregated totals and per-pod details should be correct."""
        nodes = [
            _make_node(
                "node-1",
                capacity={"cpu": "8", "memory": "32Gi"},
                allocatable={"cpu": "7500m", "memory": "30Gi"},
            ),
            _make_node(
                "node-2",
                capacity={"cpu": "8", "memory": "32Gi"},
                allocatable={"cpu": "7500m", "memory": "30Gi"},
            ),
        ]
        pods = [
            _make_pod("sbx-aaa", "Running", [{"cpu": "4", "memory": "16Gi"}]),
            _make_pod("sbx-bbb", "Pending", [{"cpu": "2", "memory": "8Gi"}]),
            # Succeeded pods should be excluded
            _make_pod("sbx-done", "Succeeded", [{"cpu": "4", "memory": "16Gi"}]),
        ]

        with (
            patch("orchestrator.k8s_client.config"),
            patch("orchestrator.k8s_client.client"),
            patch("orchestrator.k8s_client.settings") as mock_settings,
        ):
            mock_settings.in_cluster = False
            mock_settings.k8s_api_pool_size = 10
            mock_settings.k8s_api_concurrency = 10
            mock_settings.k8s_exec_concurrency = 10
            mock_settings.max_concurrent_execs = 10
            mock_settings.sandbox_namespace = "sandbox-pods"
            mock_settings.k8s_connect_timeout = 5
            mock_settings.k8s_api_timeout = 10
            mock_settings.k8s_api_retries = 1

            from orchestrator.k8s_client import K8sClient

            k8s = K8sClient()

            # Patch _k8s_call to return our fake objects
            async def fake_k8s_call(func, *args, **kwargs):
                name = getattr(func, "__name__", str(func))
                if "list_node" in name:
                    return SimpleNamespace(items=nodes)
                if "list_namespaced_pod" in name:
                    return SimpleNamespace(items=pods)
                raise AssertionError(f"Unexpected call: {name}")

            k8s._k8s_call = fake_k8s_call

            result = await k8s.get_cluster_resources()

        # Capacity
        assert result["total_capacity"]["cpu"] == 16.0  # 8 + 8
        # Allocatable
        assert result["total_allocatable"]["cpu"] == 15.0  # 7.5 + 7.5
        # Requested (only Running + Pending pods)
        assert result["total_requested"]["cpu"] == 6.0  # 4 + 2
        # Available
        assert result["available"]["cpu"] == 9.0  # 15 - 6
        # Sandbox pods list should not include the Succeeded pod
        assert len(result["sandbox_pods"]) == 2

    async def test_empty_cluster(self):
        """No nodes and no pods should return zeroes."""
        with (
            patch("orchestrator.k8s_client.config"),
            patch("orchestrator.k8s_client.client"),
            patch("orchestrator.k8s_client.settings") as mock_settings,
        ):
            mock_settings.in_cluster = False
            mock_settings.k8s_api_pool_size = 10
            mock_settings.k8s_api_concurrency = 10
            mock_settings.k8s_exec_concurrency = 10
            mock_settings.max_concurrent_execs = 10
            mock_settings.sandbox_namespace = "sandbox-pods"
            mock_settings.k8s_connect_timeout = 5
            mock_settings.k8s_api_timeout = 10
            mock_settings.k8s_api_retries = 1

            from orchestrator.k8s_client import K8sClient

            k8s = K8sClient()

            async def fake_k8s_call(func, *args, **kwargs):
                return SimpleNamespace(items=[])

            k8s._k8s_call = fake_k8s_call

            result = await k8s.get_cluster_resources()

        assert result["total_capacity"]["cpu"] == 0.0
        assert result["total_requested"]["cpu"] == 0.0
        assert result["available"]["cpu"] == 0.0
        assert result["sandbox_pods"] == []


# ---------------------------------------------------------------------------
# Tests for /resources API endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestResourcesEndpoint:
    """Tests for GET /resources."""

    async def test_returns_resource_data(self):
        """The endpoint should return the data from get_cluster_resources."""
        fake_data = {
            "nodes": [],
            "total_capacity": {"cpu": 8.0, "memory_bytes": 1024},
            "total_allocatable": {"cpu": 7.0, "memory_bytes": 900},
            "total_requested": {"cpu": 2.0, "memory_bytes": 200},
            "available": {"cpu": 5.0, "memory_bytes": 700},
            "sandbox_pods": [],
        }

        with (
            patch("orchestrator.api.settings") as mock_settings,
            patch("orchestrator.k8s_client.config"),
            patch("orchestrator.k8s_client.client"),
            patch("orchestrator.k8s_client.settings") as mock_k8s_settings,
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

            # We import the app and override the k8s_client at module level
            import orchestrator.api as api_module

            mock_k8s = MagicMock()
            mock_k8s.get_cluster_resources = AsyncMock(return_value=fake_data)
            api_module.k8s_client = mock_k8s

            from httpx import AsyncClient, ASGITransport

            transport = ASGITransport(app=api_module.app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/resources")

            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "ok"
            assert body["available"]["cpu"] == 5.0
