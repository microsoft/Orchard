"""Unit tests for the exposed-port allowlist on SandboxManager.

The allowlist is what makes a service URL usable and what makes revocation
immediate, so these tests pin its concurrency behaviour: a naive
read-modify-write loses ports when two callers expose different ports at the
same time.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from orchard_env.orchestrator.sandbox_manager import Sandbox, SandboxManager


def _make_sandbox(**overrides) -> Sandbox:
    defaults = dict(
        sandbox_id="s1",
        namespace="sandbox-pods",
        image="python:3.11-slim",
        pod_name="sandbox-s1",
        block_network=True,
        cpu="1",
        memory="1Gi",
        ready=True,
    )
    defaults.update(overrides)
    return Sandbox(**defaults)


class FakeRedisStore:
    """Redis stand-in whose transaction semantics match the real store.

    ``mutate_sandbox`` is serialised (as a Redis WATCH/MULTI transaction is),
    while ``update_sandbox`` deliberately interleaves a read and a write so the
    lost-update bug is reproducible in a test.
    """

    def __init__(self, record: dict):
        self.data = json.dumps(record)
        self._lock = asyncio.Lock()

    async def get_sandbox(self, sandbox_id):
        return json.loads(self.data)

    async def sandbox_exists(self, sandbox_id):
        return True

    async def update_sandbox(self, sandbox_id, updates):
        record = json.loads(self.data)
        await asyncio.sleep(0)  # yield: this is where the lost update happens
        record.update(updates)
        self.data = json.dumps(record)
        return True

    async def mutate_sandbox(self, sandbox_id, mutate, max_attempts=5):
        async with self._lock:
            record = json.loads(self.data)
            updates = mutate(record)
            if updates is None:
                return None
            record.update(updates)
            await asyncio.sleep(0)
            self.data = json.dumps(record)
            return record


@pytest.fixture
def memory_manager():
    """A manager backed by the in-memory store (single-replica mode)."""
    with patch("orchard_env.orchestrator.sandbox_manager.settings.use_redis", False):
        manager = SandboxManager(k8s_client=None)
        manager._sandboxes["s1"] = _make_sandbox()
        yield manager


@pytest.fixture
def redis_manager():
    """A manager backed by the fake Redis store."""
    manager = SandboxManager(k8s_client=None)
    store = FakeRedisStore(_make_sandbox().to_dict())
    manager._redis_store = store

    async def _get_store():
        return store

    manager._get_redis_store = _get_store
    return manager


class TestExposeAndRevoke:
    @pytest.mark.asyncio
    async def test_expose_adds_the_port(self, memory_manager):
        assert await memory_manager.expose_service("s1", 8000) == [8000]
        assert memory_manager._sandboxes["s1"].exposed_ports == [8000]

    @pytest.mark.asyncio
    async def test_expose_is_idempotent(self, memory_manager):
        for _ in range(3):
            assert await memory_manager.expose_service("s1", 8000) == [8000]

    @pytest.mark.asyncio
    async def test_expose_unknown_sandbox_returns_none(self, memory_manager):
        assert await memory_manager.expose_service("nope", 8000) is None

    @pytest.mark.asyncio
    async def test_expose_enforces_the_limit(self, memory_manager):
        with patch(
            "orchard_env.orchestrator.sandbox_manager.settings."
            "max_services_per_sandbox",
            2,
        ):
            await memory_manager.expose_service("s1", 8000)
            await memory_manager.expose_service("s1", 8001)
            with pytest.raises(ValueError, match="MAX_SERVICES_PER_SANDBOX"):
                await memory_manager.expose_service("s1", 8002)

    @pytest.mark.asyncio
    async def test_revoke_removes_the_port(self, memory_manager):
        await memory_manager.expose_service("s1", 8000)
        assert await memory_manager.revoke_service("s1", 8000) is True
        assert memory_manager._sandboxes["s1"].exposed_ports == []

    @pytest.mark.asyncio
    async def test_revoking_an_unexposed_port_is_false(self, memory_manager):
        assert await memory_manager.revoke_service("s1", 8000) is False

    @pytest.mark.asyncio
    async def test_revoke_unknown_sandbox_is_false(self, memory_manager):
        assert await memory_manager.revoke_service("nope", 8000) is False


class TestConcurrency:
    """Two callers exposing different ports must not lose each other's write."""

    @pytest.mark.asyncio
    async def test_concurrent_exposes_all_land_in_memory_mode(self, memory_manager):
        ports = list(range(8000, 8010))
        with patch(
            "orchard_env.orchestrator.sandbox_manager.settings."
            "max_services_per_sandbox",
            len(ports),
        ):
            await asyncio.gather(
                *(memory_manager.expose_service("s1", port) for port in ports)
            )
        assert sorted(memory_manager._sandboxes["s1"].exposed_ports) == ports

    @pytest.mark.asyncio
    async def test_concurrent_exposes_all_land_with_redis(self, redis_manager):
        ports = list(range(8000, 8010))
        with patch(
            "orchard_env.orchestrator.sandbox_manager.settings."
            "max_services_per_sandbox",
            len(ports),
        ):
            await asyncio.gather(
                *(redis_manager.expose_service("s1", port) for port in ports)
            )
        record = json.loads(redis_manager._redis_store.data)
        assert sorted(record["exposed_ports"]) == ports

    @pytest.mark.asyncio
    async def test_concurrent_revokes_all_land_with_redis(self, redis_manager):
        ports = list(range(8000, 8006))
        with patch(
            "orchard_env.orchestrator.sandbox_manager.settings."
            "max_services_per_sandbox",
            len(ports),
        ):
            for port in ports:
                await redis_manager.expose_service("s1", port)

            await asyncio.gather(
                *(redis_manager.revoke_service("s1", port) for port in ports[:3])
            )

        record = json.loads(redis_manager._redis_store.data)
        assert sorted(record["exposed_ports"]) == ports[3:]

    @pytest.mark.asyncio
    async def test_naive_update_would_lose_writes(self, redis_manager):
        """Demonstrates the bug the atomic path avoids.

        Using the plain last-write-wins update for the same workload drops
        ports, which is exactly why expose/revoke do not use it.
        """
        store = redis_manager._redis_store

        async def naive_expose(port):
            record = await store.get_sandbox("s1")
            exposed = list(record.get("exposed_ports") or [])
            exposed.append(port)
            await store.update_sandbox("s1", {"exposed_ports": exposed})

        await asyncio.gather(*(naive_expose(p) for p in range(8000, 8010)))
        record = json.loads(store.data)
        assert len(record["exposed_ports"]) < 10
