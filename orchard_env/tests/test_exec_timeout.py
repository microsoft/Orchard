"""Tests for exec timeout handling — ensure 'running' status is not treated as complete."""

import time
from unittest.mock import MagicMock, patch

import pytest

from orchard_env.client.sandbox_client import JobResult, SandboxInstance


class TestJobResult:
    """Tests for JobResult.is_complete property."""

    def test_succeeded_is_complete(self):
        result = JobResult(
            {
                "job_id": "j1",
                "sandbox_id": "s1",
                "command": "echo hi",
                "status": "succeeded",
                "exit_code": 0,
                "stdout": "hi\n",
                "created_at": 1.0,
            }
        )
        assert result.is_complete is True
        assert result.succeeded is True
        assert result.failed is False

    def test_failed_is_complete(self):
        result = JobResult(
            {
                "job_id": "j1",
                "sandbox_id": "s1",
                "command": "false",
                "status": "failed",
                "exit_code": 1,
                "created_at": 1.0,
            }
        )
        assert result.is_complete is True
        assert result.succeeded is False
        assert result.failed is True

    def test_running_is_not_complete(self):
        result = JobResult(
            {
                "job_id": "j1",
                "sandbox_id": "s1",
                "command": "sleep 999",
                "status": "running",
                "created_at": 1.0,
            }
        )
        assert result.is_complete is False
        assert result.succeeded is False
        assert result.failed is False

    def test_queued_is_not_complete(self):
        result = JobResult(
            {
                "job_id": "j1",
                "sandbox_id": "s1",
                "command": "echo hi",
                "status": "queued",
                "created_at": 1.0,
            }
        )
        assert result.is_complete is False


class TestSyncExecStatusHandling:
    """Tests that SandboxInstance.exec correctly handles server-side wait responses."""

    @staticmethod
    def _make_sandbox(request_side_effect):
        """Create a SandboxInstance with mocked client._request."""
        client = MagicMock()
        client._request = MagicMock(side_effect=request_side_effect)
        sandbox = SandboxInstance(client=client, sandbox_id="s1", data={})
        return sandbox

    def test_exec_returns_immediately_on_succeeded(self):
        """Server-side wait returned succeeded — return right away."""

        def fake_request(method, path, **kwargs):
            return {
                "job_id": "j1",
                "sandbox_id": "s1",
                "command": "echo hi",
                "status": "succeeded",
                "exit_code": 0,
                "stdout": "hi\n",
                "stderr": "",
                "created_at": 1.0,
            }

        sandbox = self._make_sandbox(fake_request)
        result = sandbox.exec("echo hi")
        assert result.status == "succeeded"
        assert result.exit_code == 0
        assert result.stdout == "hi\n"

    def test_exec_returns_immediately_on_failed(self):
        """Server-side wait returned failed — return right away."""

        def fake_request(method, path, **kwargs):
            return {
                "job_id": "j1",
                "sandbox_id": "s1",
                "command": "false",
                "status": "failed",
                "exit_code": 1,
                "stdout": "",
                "stderr": "err\n",
                "created_at": 1.0,
            }

        sandbox = self._make_sandbox(fake_request)
        result = sandbox.exec("false")
        assert result.status == "failed"
        assert result.exit_code == 1

    def test_exec_polls_when_server_returns_running(self):
        """Server-side wait timed out (status=running) — must fall through to polling."""
        call_count = 0

        def fake_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if "exec" in path:
                # Server-side wait timed out, returned running status
                return {
                    "job_id": "j1",
                    "sandbox_id": "s1",
                    "command": "sleep 999",
                    "status": "running",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "created_at": 1.0,
                }
            # GET /jobs/j1 polling calls
            if call_count <= 3:
                return {
                    "job_id": "j1",
                    "sandbox_id": "s1",
                    "command": "sleep 999",
                    "status": "running",
                    "created_at": 1.0,
                }
            return {
                "job_id": "j1",
                "sandbox_id": "s1",
                "command": "sleep 999",
                "status": "succeeded",
                "exit_code": 0,
                "stdout": "done\n",
                "stderr": "",
                "created_at": 1.0,
            }

        sandbox = self._make_sandbox(fake_request)
        with patch("orchard_env.client.sandbox_client.time") as mock_time:
            mock_time.monotonic = time.monotonic
            mock_time.time = time.time
            mock_time.sleep = MagicMock()  # Don't actually sleep
            result = sandbox.exec("sleep 999", timeout=300, poll_interval=0.01)

        assert result.status == "succeeded"
        assert result.exit_code == 0
        assert result.stdout == "done\n"
        # Verify it polled (at least the exec call + some get_job calls)
        assert call_count >= 3

    def test_exec_polling_raises_on_timeout(self):
        """Client-side polling must raise TimeoutError when deadline is exceeded."""

        def fake_request(method, path, **kwargs):
            if "exec" in path:
                # Server-side wait timed out
                return {
                    "job_id": "j1",
                    "sandbox_id": "s1",
                    "command": "sleep 999",
                    "status": "running",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                    "created_at": 1.0,
                }
            # Always return running for polling
            return {
                "job_id": "j1",
                "sandbox_id": "s1",
                "command": "sleep 999",
                "status": "running",
                "created_at": 1.0,
            }

        sandbox = self._make_sandbox(fake_request)

        # Use a very short timeout and simulate time advancing past the deadline
        monotonic_values = iter([0.0, 0.0, 1000.0])  # 3rd call exceeds deadline

        with patch("orchard_env.client.sandbox_client.time") as mock_time:
            mock_time.monotonic = lambda: next(monotonic_values)
            mock_time.time = time.time
            mock_time.sleep = MagicMock()
            with pytest.raises(TimeoutError, match="did not complete"):
                sandbox.exec("sleep 999", timeout=5, poll_interval=0.01)

    def test_exec_no_wait_returns_queued(self):
        """wait=False should return immediately with 'queued' status."""
        call_count = 0

        def fake_request(method, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Server-side wait attempt returns queued (not complete)
                return {
                    "job_id": "j1",
                    "sandbox_id": "s1",
                    "command": "echo hi",
                    "status": "queued",
                    "created_at": 1.0,
                }
            return {"job_id": "j1"}

        sandbox = self._make_sandbox(fake_request)
        result = sandbox.exec("echo hi", wait=False)
        assert result.status == "queued"
