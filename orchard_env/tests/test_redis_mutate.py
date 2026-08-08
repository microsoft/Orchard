"""Unit tests for RedisSandboxStore.mutate_sandbox.

The exposed-port allowlist relies on this being a genuine compare-and-set, so
these tests drive it through a fake that follows redis-py's real pipeline
semantics: `watch`/`get` execute immediately and are awaited, commands after
`multi()` are buffered and are not, and a concurrent write raises `WatchError`
from `execute()`.
"""

import json

import pytest
from redis.exceptions import WatchError

from orchard_env.orchestrator.redis_store import RedisSandboxStore


class FakePipeline:
    """Mimics redis.asyncio Pipeline for the watch/multi/execute flow."""

    def __init__(self, store: "FakeRedisClient"):
        self._store = store
        self._watched_key: str | None = None
        self._watched_version: int | None = None
        self._buffered: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def watch(self, key):
        self._watched_key = key
        self._watched_version = self._store.versions.get(key, 0)

    async def unwatch(self):
        self._watched_key = None

    async def get(self, key):
        # The store may mutate between watch and get in the real world; the
        # version check at execute() is what catches it.
        return self._store.data.get(key)

    def multi(self):
        self._buffered = []

    def set(self, key, value, ex=None):
        # Buffered: deliberately not a coroutine, matching redis-py.
        self._buffered.append((key, value))
        return self

    async def execute(self):
        key = self._watched_key
        if (
            key is not None
            and self._store.versions.get(key, 0) != self._watched_version
        ):
            raise WatchError("WATCHed key changed")
        for buffered_key, value in self._buffered:
            self._store.write(buffered_key, value)
        return [True] * len(self._buffered)


class FakeRedisClient:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.versions: dict[str, int] = {}
        self.on_watch = None

    def write(self, key, value):
        self.data[key] = value
        self.versions[key] = self.versions.get(key, 0) + 1

    def pipeline(self):
        return FakePipeline(self)


@pytest.fixture
def store():
    redis_store = RedisSandboxStore(redis_url="redis://unused")
    client = FakeRedisClient()
    client.write("sandbox:s1", json.dumps({"sandbox_id": "s1", "exposed_ports": []}))

    async def _ensure_connected():
        return client

    redis_store._ensure_connected = _ensure_connected
    redis_store._fake_client = client
    return redis_store


class TestMutateSandbox:
    @pytest.mark.asyncio
    async def test_applies_the_mutation(self, store):
        result = await store.mutate_sandbox(
            "s1", lambda record: {"exposed_ports": [8000]}
        )
        assert result["exposed_ports"] == [8000]
        stored = json.loads(store._fake_client.data["sandbox:s1"])
        assert stored["exposed_ports"] == [8000]

    @pytest.mark.asyncio
    async def test_mutation_sees_current_state(self, store):
        await store.mutate_sandbox("s1", lambda r: {"exposed_ports": [8000]})
        seen = {}

        def mutate(record):
            seen["ports"] = record["exposed_ports"]
            return {"exposed_ports": record["exposed_ports"] + [8001]}

        await store.mutate_sandbox("s1", mutate)
        assert seen["ports"] == [8000]

    @pytest.mark.asyncio
    async def test_returning_none_writes_nothing(self, store):
        assert await store.mutate_sandbox("s1", lambda record: None) is None
        stored = json.loads(store._fake_client.data["sandbox:s1"])
        assert stored["exposed_ports"] == []

    @pytest.mark.asyncio
    async def test_missing_sandbox_returns_none(self, store):
        assert await store.mutate_sandbox("gone", lambda record: {"a": 1}) is None

    @pytest.mark.asyncio
    async def test_conflicting_write_is_retried(self, store):
        """A concurrent writer must cause a retry, not a lost update."""
        client = store._fake_client
        attempts = {"count": 0}

        def mutate(record):
            attempts["count"] += 1
            if attempts["count"] == 1:
                # Simulate another replica writing between our watch and exec.
                client.write(
                    "sandbox:s1",
                    json.dumps({"sandbox_id": "s1", "exposed_ports": [9001]}),
                )
            return {"exposed_ports": record["exposed_ports"] + [8000]}

        result = await store.mutate_sandbox("s1", mutate)
        assert attempts["count"] == 2
        # The retry observed the other replica's write and preserved it.
        assert result["exposed_ports"] == [9001, 8000]

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self, store):
        client = store._fake_client

        def mutate(record):
            client.write("sandbox:s1", json.dumps({"sandbox_id": "s1"}))
            return {"exposed_ports": [8000]}

        with pytest.raises(TimeoutError, match="contention"):
            await store.mutate_sandbox("s1", mutate, max_attempts=3)
