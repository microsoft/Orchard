#!/usr/bin/env python3
"""
Async concurrent test script: Create and test multiple sandboxes using asyncio.

Every interaction (create/exec/delete) is tracked. On exit the script prints
detailed statistics including P50/P90/P95/P99/min/max/avg percentiles.
"""

import sys
import time
import random
import asyncio
import math
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import json
import os
from datetime import datetime

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from aks_modal import AsyncSandboxClient, JobResult


# ────────────────────────────────────────────────────────────
# Timing Tracker
# ────────────────────────────────────────────────────────────

@dataclass
class TimingRecord:
    """Timing record for a single operation."""
    sandbox_index: int          # sandbox number (1-based)
    sandbox_id: Optional[str]   # sandbox ID (available after creation)
    operation: str              # operation type: create, exec_1, exec_2, ..., delete
    command: str = ""           # command executed (for exec operations)
    start_time: float = 0.0    # absolute start time (time.time())
    end_time: float = 0.0      # absolute end time
    duration: float = 0.0      # duration (seconds)
    success: bool = False       # whether succeeded
    error: Optional[str] = None # error message


class TimingTracker:
    """Global timing tracker that records all operations and prints stats on exit."""

    def __init__(self):
        self.records: List[TimingRecord] = []
        self.test_start_time: float = 0.0
        self.test_end_time: float = 0.0
        self._lock = asyncio.Lock()

    def start(self):
        """Mark test start."""
        self.test_start_time = time.time()

    def stop(self):
        """Mark test end."""
        self.test_end_time = time.time()

    async def record(self, rec: TimingRecord):
        """Thread-safe record insertion."""
        async with self._lock:
            self.records.append(rec)

    @staticmethod
    def _percentile(sorted_data: List[float], p: float) -> float:
        """Calculate percentile (0-100)."""
        if not sorted_data:
            return 0.0
        k = (len(sorted_data) - 1) * (p / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_data[int(k)]
        return sorted_data[f] * (c - k) + sorted_data[c] * (k - f)

    def _compute_stats(self, durations: List[float]) -> Dict[str, float]:
        """Compute statistics for a list of durations."""
        if not durations:
            return {}
        s = sorted(durations)
        return {
            "count": len(s),
            "min": s[0],
            "max": s[-1],
            "avg": sum(s) / len(s),
            "p50": self._percentile(s, 50),
            "p90": self._percentile(s, 90),
            "p95": self._percentile(s, 95),
            "p99": self._percentile(s, 99),
        }

    def print_summary(self):
        """Print full timing summary."""
        total_time = self.test_end_time - self.test_start_time

        print()
        print("=" * 80)
        print("  Timing Summary")
        print("=" * 80)
        print(f"  Total test time: {total_time:.2f}s")
        print(f"  Total records:   {len(self.records)}")
        print()

        # ── Group by operation type ──
        by_op: Dict[str, List[TimingRecord]] = defaultdict(list)
        for r in self.records:
            by_op[r.operation].append(r)

        # Define output order
        op_order = ["create"]
        exec_ops = sorted([op for op in by_op if op.startswith("exec_")])
        op_order.extend(exec_ops)
        if "delete" in by_op:
            op_order.append("delete")
        # Add other operations not listed
        for op in sorted(by_op.keys()):
            if op not in op_order:
                op_order.append(op)

        op_labels = {
            "create": "Create",
            "delete": "Delete",
        }
        for op in exec_ops:
            idx = op.replace("exec_", "")
            op_labels[op] = f"Exec #{idx}"

        for op in op_order:
            if op not in by_op:
                continue
            records = by_op[op]
            label = op_labels.get(op, op)

            success_records = [r for r in records if r.success]
            fail_records = [r for r in records if not r.success]
            durations = [r.duration for r in success_records]
            stats = self._compute_stats(durations)

            print(f"  ┌─ {label}")
            print(f"  │  Total: {len(records)}  Success: {len(success_records)}  Failed: {len(fail_records)}")
            if stats:
                print(f"  │  Duration (seconds):")
                print(f"  │    min={stats['min']:.3f}  avg={stats['avg']:.3f}  max={stats['max']:.3f}")
                print(f"  │    P50={stats['p50']:.3f}  P90={stats['p90']:.3f}  P95={stats['p95']:.3f}  P99={stats['p99']:.3f}")
            if fail_records:
                # Show up to 3 failure reasons
                print(f"  │  Failure examples:")
                for r in fail_records[:3]:
                    err = (r.error or "unknown")[:80]
                    print(f"  │    [{r.sandbox_index}] {err}")
                if len(fail_records) > 3:
                    print(f"  │    ... and {len(fail_records) - 3} more failures")
            print(f"  └{'─' * 60}")
            print()

        # ── Aggregated exec stats ──
        all_exec_durations = []
        all_exec_success = 0
        all_exec_total = 0
        for op in exec_ops:
            for r in by_op[op]:
                all_exec_total += 1
                if r.success:
                    all_exec_success += 1
                    all_exec_durations.append(r.duration)

        if all_exec_durations:
            stats = self._compute_stats(all_exec_durations)
            print(f"  ┌─ All Exec Combined")
            print(f"  │  Total: {all_exec_total}  Success: {all_exec_success}  Failed: {all_exec_total - all_exec_success}")
            print(f"  │  Duration (seconds):")
            print(f"  │    min={stats['min']:.3f}  avg={stats['avg']:.3f}  max={stats['max']:.3f}")
            print(f"  │    P50={stats['p50']:.3f}  P90={stats['p90']:.3f}  P95={stats['p95']:.3f}  P99={stats['p99']:.3f}")
            print(f"  └{'─' * 60}")
            print()

        # ── Per-sandbox lifecycle timeline ──
        print("  ┌─ Per-Sandbox Lifecycle Duration (seconds)")
        print("  │")

        # Group by sandbox_index
        by_sandbox: Dict[int, List[TimingRecord]] = defaultdict(list)
        for r in self.records:
            by_sandbox[r.sandbox_index].append(r)

        # Header
        header_parts = ["  │  {:>4s}  {:>10s}".format("#", "sandbox_id")]
        for op in op_order:
            col_label = op.replace("exec_", "e")
            if op == "create":
                col_label = "create"
            elif op == "delete":
                col_label = "delete"
            header_parts.append(f"{col_label:>8s}")
        header_parts.append(f"{'total':>8s}")
        print("  ".join(header_parts))
        print(f"  │  {'─' * (len(op_order) * 10 + 30)}")

        sandbox_totals = []
        for idx in sorted(by_sandbox.keys()):
            recs = by_sandbox[idx]
            sid = ""
            for r in recs:
                if r.sandbox_id:
                    sid = r.sandbox_id[:10]
                    break

            row_parts = [f"  │  {idx:>4d}  {sid:>10s}"]
            total_for_sandbox = 0.0
            for op in op_order:
                matching = [r for r in recs if r.operation == op]
                if matching:
                    r = matching[0]
                    mark = "" if r.success else "✗"
                    row_parts.append(f"{r.duration:>7.2f}{mark}")
                    total_for_sandbox += r.duration
                else:
                    row_parts.append(f"{'—':>8s}")
            row_parts.append(f"{total_for_sandbox:>8.2f}")
            sandbox_totals.append(total_for_sandbox)
            print("  ".join(row_parts))

        if sandbox_totals:
            total_stats = self._compute_stats(sandbox_totals)
            print(f"  │  {'─' * (len(op_order) * 10 + 30)}")
            print(f"  │  Lifecycle total: min={total_stats['min']:.2f}  avg={total_stats['avg']:.2f}  "
                  f"max={total_stats['max']:.2f}  P50={total_stats['p50']:.2f}  P90={total_stats['p90']:.2f}")

        print(f"  └{'─' * 60}")
        print()

        # ── Throughput ──
        create_records = by_op.get("create", [])
        create_success = sum(1 for r in create_records if r.success)
        delete_records = by_op.get("delete", [])
        delete_success = sum(1 for r in delete_records if r.success)

        print(f"  ┌─ Throughput")
        if create_success > 0:
            print(f"  │  Create: {create_success / total_time:.2f} sandboxes/s ({create_success} in {total_time:.1f}s)")
        if all_exec_success > 0:
            print(f"  │  Exec:   {all_exec_success / total_time:.2f} execs/s ({all_exec_success} in {total_time:.1f}s)")
        if delete_success > 0:
            print(f"  │  Delete: {delete_success / total_time:.2f} deletes/s ({delete_success} in {total_time:.1f}s)")
        print(f"  └{'─' * 60}")

        print()
        print("=" * 80)
        print()

    def save_to_json(self, filepath: str):
        """Save detailed records to JSON."""
        data = {
            "test_start": self.test_start_time,
            "test_end": self.test_end_time,
            "total_time": self.test_end_time - self.test_start_time,
            "records": [
                {
                    "sandbox_index": r.sandbox_index,
                    "sandbox_id": r.sandbox_id,
                    "operation": r.operation,
                    "command": r.command,
                    "start_time": r.start_time,
                    "end_time": r.end_time,
                    "duration": r.duration,
                    "success": r.success,
                    "error": r.error,
                }
                for r in self.records
            ],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Timing data saved to: {filepath}")


# Global tracker instance
tracker = TimingTracker()


# ────────────────────────────────────────────────────────────
# Test Class
# ────────────────────────────────────────────────────────────

class AsyncConcurrentSandboxTest:
    """Async concurrent sandbox tester."""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: int = 600, api_key: Optional[str] = None):
        self.base_url = base_url
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("SANDBOX_API_KEY")
        self.results: List[Dict[str, Any]] = []

    async def create_single_sandbox(
        self,
        client: AsyncSandboxClient,
        index: int,
        image: str,
        cpu: str = "1",
        memory: str = "2Gi"
    ) -> Dict[str, Any]:
        """Create a single sandbox and record performance metrics."""
        result = {
            "index": index,
            "success": False,
            "sandbox_id": None,
            "create_time": None,
            "error": None,
            "start_timestamp": None,
            "end_timestamp": None,
        }

        rec = TimingRecord(sandbox_index=index, sandbox_id=None, operation="create")

        try:
            start_time = time.time()
            rec.start_time = start_time
            result["start_timestamp"] = datetime.now().isoformat()

            print(f"[{index}] Creating sandbox...")

            sandbox = await client.create_sandbox(
                image=image,
                block_network=False,
                cpu=cpu,
                memory=memory,
            )

            end_time = time.time()
            rec.end_time = end_time
            rec.duration = end_time - start_time
            rec.sandbox_id = sandbox.sandbox_id
            rec.success = True

            result["end_timestamp"] = datetime.now().isoformat()
            result["create_time"] = rec.duration
            result["sandbox_id"] = sandbox.sandbox_id
            result["success"] = True

            print(f"[{index}] Created: {result['sandbox_id']} ({rec.duration:.2f}s)")

        except Exception as e:
            rec.end_time = time.time()
            rec.duration = rec.end_time - rec.start_time
            rec.error = str(e)
            result["error"] = str(e)
            result["end_timestamp"] = datetime.now().isoformat()
            print(f"[{index}] FAIL create: {e}")

        await tracker.record(rec)
        return result

    async def test_concurrent_create(
        self,
        num_sandboxes: int = 20,
        image: str = "mirror.gcr.io/python:3.11-slim",
        cpu: str = "1",
        memory: str = "2Gi",
    ) -> Dict[str, Any]:
        """Concurrently create multiple sandboxes."""
        print(f"\n{'='*60}")
        print(f"Concurrent create test: {num_sandboxes} sandboxes")
        print(f"Image: {image}")
        print(f"Resources: CPU={cpu}, Memory={memory}")
        print(f"{'='*60}\n")

        self.results = []
        overall_start = time.time()

        async with AsyncSandboxClient(self.base_url, timeout=self.timeout, api_key=self.api_key) as client:
            tasks = [
                self.create_single_sandbox(client, i, image, cpu, memory)
                for i in range(1, num_sandboxes + 1)
            ]
            self.results = await asyncio.gather(*tasks)

        overall_end = time.time()
        overall_time = overall_end - overall_start

        stats = self._calculate_statistics(overall_time)
        self._print_statistics(stats)

        return stats

    async def exec_single_command(
        self,
        client: AsyncSandboxClient,
        sandbox_id: str,
        index: int,
        command: str
    ) -> Dict[str, Any]:
        """Execute a command in a single sandbox."""
        result = {
            "index": index,
            "sandbox_id": sandbox_id,
            "success": False,
            "exec_time": None,
            "exit_code": None,
            "stdout": None,
            "stderr": None,
            "error": None,
        }

        rec = TimingRecord(sandbox_index=index, sandbox_id=sandbox_id,
                           operation="exec_1", command=command)

        try:
            start_time = time.time()
            rec.start_time = start_time
            print(f"[{index}] Exec: {command}")

            sandbox = await client.get_sandbox(sandbox_id)
            job_result = await sandbox.exec(command)

            end_time = time.time()
            rec.end_time = end_time
            rec.duration = end_time - start_time
            rec.success = job_result.succeeded

            result["exec_time"] = rec.duration
            result["success"] = job_result.succeeded
            result["exit_code"] = job_result.exit_code
            result["stdout"] = job_result.stdout
            result["stderr"] = job_result.stderr

            status = "OK" if job_result.succeeded else "FAIL"
            print(f"[{index}] {status} exec done ({rec.duration:.2f}s)")

        except Exception as e:
            rec.end_time = time.time()
            rec.duration = rec.end_time - rec.start_time
            rec.error = str(e)
            result["error"] = str(e)
            print(f"[{index}] FAIL exec: {e}")

        await tracker.record(rec)
        return result

    async def test_concurrent_exec(
        self,
        sandbox_ids: List[str],
        command: str = "echo 'Hello World'"
    ) -> Dict[str, Any]:
        """Execute a command concurrently in multiple sandboxes."""
        print(f"\n{'='*60}")
        print(f"Concurrent exec test: {len(sandbox_ids)} sandboxes")
        print(f"Command: {command}")
        print(f"{'='*60}\n")

        overall_start = time.time()

        async with AsyncSandboxClient(self.base_url, timeout=self.timeout, api_key=self.api_key) as client:
            tasks = [
                self.exec_single_command(client, sandbox_id, i+1, command)
                for i, sandbox_id in enumerate(sandbox_ids)
            ]
            results = await asyncio.gather(*tasks)

        overall_end = time.time()
        overall_time = overall_end - overall_start

        successful = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        exec_times = [r["exec_time"] for r in successful if r["exec_time"]]

        stats = {
            "total_executions": len(results),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate": len(successful) / len(results) * 100 if results else 0,
            "overall_time": overall_time,
        }

        if exec_times:
            stats.update({
                "avg_exec_time": sum(exec_times) / len(exec_times),
                "min_exec_time": min(exec_times),
                "max_exec_time": max(exec_times),
            })

        self._print_exec_statistics(stats)

        return stats

    async def test_concurrent_create_and_exec(
        self,
        num_sandboxes: int = 10,
        image: str = "python:3.11-slim",
        command: str = "python -c 'import sys; print(f\"Python {sys.version}\")'",
        cpu: str = "2",
        memory: str = "4Gi",
        cleanup: bool = True,
        phased: bool = False,
    ) -> Dict[str, Any]:
        """Concurrently create sandboxes, exec commands, then optionally cleanup.

        Args:
            phased: If True, use phased execution (create all -> exec all -> delete all)
                    to avoid mixed-workload contention on the K8s API.
                    If False (default), each sandbox runs create->exec->delete independently.
        """
        if phased:
            return await self._test_phased(num_sandboxes, image, command, cpu, memory, cleanup)
        else:
            return await self._test_interleaved(num_sandboxes, image, command, cpu, memory, cleanup)

    async def _test_phased(
        self,
        num_sandboxes: int,
        image: str,
        command: str,
        cpu: str,
        memory: str,
        cleanup: bool,
    ) -> Dict[str, Any]:
        """Phased execution: create all -> exec all -> delete all."""
        print(f"\n{'='*60}")
        print(f"Full test (phased): Create -> Exec -> Cleanup")
        print(f"Sandboxes: {num_sandboxes}")
        print(f"Image: {image}")
        print(f"Command: {command}")
        print(f"{'='*60}\n")

        overall_start = time.time()

        exec_commands = [
            command,
            'ls /',
            'cat /etc/os-release | head -1',
            'cat /etc/os-release | head -1',
        ]
        exec_login_shell = [False, False, False, True]

        async with AsyncSandboxClient(self.base_url, timeout=self.timeout, api_key=self.api_key) as client:
            # ════════════════════════════════════════════
            # Phase 1: 并发创建所有 sandbox
            # ════════════════════════════════════════════
            phase_start = time.time()
            print(f"\n{'─'*40}")
            print(f"  Phase 1/3: Create {num_sandboxes} sandboxes")
            print(f"{'─'*40}")

            async def do_create(index: int):
                rec = TimingRecord(sandbox_index=index, sandbox_id=None, operation="create")
                start = time.time()
                rec.start_time = start
                try:
                    sandbox = await client.create_sandbox(
                        image=image, block_network=False, cpu=cpu, memory=memory,
                    )
                    end = time.time()
                    rec.end_time = end
                    rec.duration = end - start
                    rec.sandbox_id = sandbox.sandbox_id
                    rec.success = True
                    print(f"[{index}] Created: {sandbox.sandbox_id} ({rec.duration:.2f}s)")
                except Exception as e:
                    rec.end_time = time.time()
                    rec.duration = rec.end_time - start
                    rec.error = str(e)
                    print(f"[{index}] FAIL create: {e}")
                await tracker.record(rec)
                return rec

            create_recs = await asyncio.gather(*[do_create(i) for i in range(1, num_sandboxes + 1)])
            phase_create_time = time.time() - phase_start
            success_creates = [r for r in create_recs if r.success]
            print(f"\n  Phase 1 done: {len(success_creates)}/{num_sandboxes} succeeded in {phase_create_time:.2f}s\n")

            # Build index→sandbox_id mapping for successful creates
            sandbox_map = {r.sandbox_index: r.sandbox_id for r in success_creates}

            # ════════════════════════════════════════════
            # Phase 2: 并发执行命令 (每个 sandbox 串行执行多条)
            # ════════════════════════════════════════════
            phase_start = time.time()
            print(f"{'─'*40}")
            print(f"  Phase 2/3: Exec commands ({len(exec_commands)} cmds/sandbox x {len(sandbox_map)} sandboxes)")
            print(f"{'─'*40}")

            async def do_exec_all(index: int, sandbox_id: str):
                sandbox = await client.get_sandbox(sandbox_id)
                for exec_idx, (cmd, login) in enumerate(zip(exec_commands, exec_login_shell), start=1):
                    op_name = f"exec_{exec_idx}"
                    rec = TimingRecord(
                        sandbox_index=index, sandbox_id=sandbox_id,
                        operation=op_name, command=cmd,
                    )
                    start = time.time()
                    rec.start_time = start
                    try:
                        job_result = await sandbox.exec(cmd, login_shell=login)
                        end = time.time()
                        rec.end_time = end
                        rec.duration = end - start
                        rec.success = job_result.succeeded
                        if not rec.success:
                            rec.error = (job_result.stderr or '').strip()[:200] or f'exit_code={job_result.exit_code}'
                        stdout_preview = job_result.stdout.strip()[:50]
                        print(f"[{index}] exec#{exec_idx}: {stdout_preview}... ({rec.duration:.2f}s)")
                    except Exception as e:
                        rec.end_time = time.time()
                        rec.duration = rec.end_time - start
                        rec.error = str(e)
                        print(f"[{index}] FAIL exec#{exec_idx}: {e}")
                    await tracker.record(rec)

            await asyncio.gather(*[
                do_exec_all(idx, sid) for idx, sid in sorted(sandbox_map.items())
            ])
            phase_exec_time = time.time() - phase_start
            print(f"\n  Phase 2 done in {phase_exec_time:.2f}s\n")

            # ════════════════════════════════════════════
            # Phase 3: 并发删除
            # ════════════════════════════════════════════
            if cleanup:
                phase_start = time.time()
                print(f"{'─'*40}")
                print(f"  Phase 3/3: Delete {len(sandbox_map)} sandboxes")
                print(f"{'─'*40}")

                async def do_delete(index: int, sandbox_id: str):
                    rec = TimingRecord(sandbox_index=index, sandbox_id=sandbox_id, operation="delete")
                    start = time.time()
                    rec.start_time = start
                    try:
                        sandbox = await client.get_sandbox(sandbox_id)
                        await sandbox.delete()
                        end = time.time()
                        rec.end_time = end
                        rec.duration = end - start
                        rec.success = True
                        print(f"[{index}] Deleted ({rec.duration:.2f}s)")
                    except Exception as e:
                        rec.end_time = time.time()
                        rec.duration = rec.end_time - start
                        rec.error = str(e)
                        print(f"[{index}] FAIL delete: {e}")
                    await tracker.record(rec)
                    return rec

                delete_recs = await asyncio.gather(*[
                    do_delete(idx, sid) for idx, sid in sorted(sandbox_map.items())
                ])
                phase_delete_time = time.time() - phase_start
                success_deletes = sum(1 for r in delete_recs if r.success)
                print(f"\n  Phase 3 done: {success_deletes}/{len(sandbox_map)} succeeded in {phase_delete_time:.2f}s\n")

        overall_time = time.time() - overall_start

        stats = {
            "total": num_sandboxes,
            "create_success": len(success_creates),
            "exec_success": len(sandbox_map),  # approximate
            "cleanup_success": success_deletes if cleanup else None,
            "overall_time": overall_time,
            "sandbox_ids": list(sandbox_map.values()),
        }
        self._print_full_test_statistics(stats, cleanup)
        return stats

    async def _test_interleaved(
        self,
        num_sandboxes: int,
        image: str,
        command: str,
        cpu: str,
        memory: str,
        cleanup: bool,
    ) -> Dict[str, Any]:
        """Interleaved execution: each sandbox runs create->exec->delete independently."""
        print(f"\n{'='*60}")
        print(f"Full test (interleaved): Create + Exec + Cleanup")
        print(f"Sandboxes: {num_sandboxes}")
        print(f"Image: {image}")
        print(f"Command: {command}")
        print(f"{'='*60}\n")

        overall_start = time.time()

        exec_commands = [
            command,
            'ls /',
            'cat /etc/os-release | head -1',
            'cat /etc/os-release | head -1',
        ]
        exec_login_shell = [False, False, False, True]

        async with AsyncSandboxClient(self.base_url, timeout=self.timeout, api_key=self.api_key) as client:

            async def create_exec_cleanup(index: int):
                result = {
                    "index": index,
                    "sandbox_id": None,
                    "create_success": False,
                    "exec_success": False,
                    "cleanup_success": False,
                    "create_time": None,
                    "exec_times": [],
                    "cleanup_time": None,
                    "stdout": None,
                    "error": None,
                }

                try:
                    # ── Create ──
                    rec_create = TimingRecord(sandbox_index=index, sandbox_id=None, operation="create")
                    print(f"[{index}] Creating sandbox...")
                    start = time.time()
                    rec_create.start_time = start

                    sandbox = await client.create_sandbox(
                        image=image,
                        block_network=False,
                        cpu=cpu,
                        memory=memory,
                    )

                    end = time.time()
                    rec_create.end_time = end
                    rec_create.duration = end - start
                    rec_create.sandbox_id = sandbox.sandbox_id
                    rec_create.success = True
                    await tracker.record(rec_create)

                    result["create_time"] = rec_create.duration
                    result["sandbox_id"] = sandbox.sandbox_id
                    result["create_success"] = True
                    print(f"[{index}] Created: {sandbox.sandbox_id} ({rec_create.duration:.2f}s)")

                    # ── Execute commands ──
                    for exec_idx, (cmd, login) in enumerate(zip(exec_commands, exec_login_shell), start=1):
                        op_name = f"exec_{exec_idx}"
                        rec_exec = TimingRecord(
                            sandbox_index=index,
                            sandbox_id=sandbox.sandbox_id,
                            operation=op_name,
                            command=cmd,
                        )
                        print(f"[{index}] Exec #{exec_idx}...")
                        start = time.time()
                        rec_exec.start_time = start

                        job_result = await sandbox.exec(cmd, login_shell=login)

                        end = time.time()
                        rec_exec.end_time = end
                        rec_exec.duration = end - start
                        rec_exec.success = job_result.succeeded
                        if not rec_exec.success:
                            rec_exec.error = (job_result.stderr or '').strip()[:200] or f'exit_code={job_result.exit_code}'
                        await tracker.record(rec_exec)

                        result["exec_times"].append(rec_exec.duration)
                        result["exec_success"] = job_result.succeeded
                        result["stdout"] = job_result.stdout.strip()
                        print(f"[{index}] Exec#{exec_idx} done: {result['stdout'][:50]}... ({rec_exec.duration:.2f}s)")

                    # ── Cleanup ──
                    if cleanup:
                        rec_del = TimingRecord(
                            sandbox_index=index,
                            sandbox_id=sandbox.sandbox_id,
                            operation="delete",
                        )
                        print(f"[{index}] Deleting sandbox...")
                        start = time.time()
                        rec_del.start_time = start

                        await sandbox.delete()

                        end = time.time()
                        rec_del.end_time = end
                        rec_del.duration = end - start
                        rec_del.success = True
                        await tracker.record(rec_del)

                        result["cleanup_time"] = rec_del.duration
                        result["cleanup_success"] = True
                        print(f"[{index}] Deleted ({rec_del.duration:.2f}s)")

                except Exception as e:
                    result["error"] = str(e)
                    print(f"[{index}] FAIL: {e}")

                return result

            # Run all tasks concurrently
            tasks = [create_exec_cleanup(i) for i in range(1, num_sandboxes + 1)]
            results = await asyncio.gather(*tasks)

        overall_time = time.time() - overall_start

        # Statistics
        stats = {
            "total": len(results),
            "create_success": sum(1 for r in results if r["create_success"]),
            "exec_success": sum(1 for r in results if r["exec_success"]),
            "cleanup_success": sum(1 for r in results if r["cleanup_success"]) if cleanup else None,
            "overall_time": overall_time,
            "sandbox_ids": [r["sandbox_id"] for r in results if r["sandbox_id"]],
            "results": results,
        }

        create_times = [r["create_time"] for r in results if r["create_time"]]
        all_exec_times = []
        for r in results:
            all_exec_times.extend(r.get("exec_times", []))

        if create_times:
            stats["avg_create_time"] = sum(create_times) / len(create_times)
        if all_exec_times:
            stats["avg_exec_time"] = sum(all_exec_times) / len(all_exec_times)

        self._print_full_test_statistics(stats, cleanup)

        return stats

    def _calculate_statistics(self, overall_time: float) -> Dict[str, Any]:
        """Calculate statistics."""
        successful = [r for r in self.results if r["success"]]
        failed = [r for r in self.results if not r["success"]]

        create_times = [r["create_time"] for r in successful]

        stats = {
            "total_sandboxes": len(self.results),
            "successful": len(successful),
            "failed": len(failed),
            "success_rate": len(successful) / len(self.results) * 100 if self.results else 0,
            "overall_time": overall_time,
            "sandbox_ids": [r["sandbox_id"] for r in successful],
            "failed_indices": [r["index"] for r in failed],
        }

        if create_times:
            stats.update({
                "avg_create_time": sum(create_times) / len(create_times),
                "min_create_time": min(create_times),
                "max_create_time": max(create_times),
            })

        return stats

    def _print_statistics(self, stats: Dict[str, Any]):
        """Print statistics."""
        print(f"\n{'='*60}")
        print("Test Results")
        print(f"{'='*60}")
        print(f"Total: {stats['total_sandboxes']}")
        print(f"Success: {stats['successful']} ({stats['success_rate']:.1f}%)")
        print(f"Failed: {stats['failed']}")
        print(f"Total time: {stats['overall_time']:.2f}s")

        if stats['successful'] > 0:
            print(f"\nCreate time stats:")
            print(f"  Avg: {stats['avg_create_time']:.2f}s")
            print(f"  Min: {stats['min_create_time']:.2f}s")
            print(f"  Max: {stats['max_create_time']:.2f}s")
            print(f"  Throughput: {stats['successful'] / stats['overall_time']:.2f} sandboxes/s")

        if stats['failed'] > 0:
            print(f"\nFailed sandbox indices: {stats['failed_indices']}")

        print(f"{'='*60}\n")

    def _print_exec_statistics(self, stats: Dict[str, Any]):
        """Print exec statistics."""
        print(f"\n{'='*60}")
        print("Exec Results")
        print(f"{'='*60}")
        print(f"Total: {stats['total_executions']}")
        print(f"Success: {stats['successful']} ({stats['success_rate']:.1f}%)")
        print(f"Failed: {stats['failed']}")
        print(f"Total time: {stats['overall_time']:.2f}s")

        if 'avg_exec_time' in stats:
            print(f"\nExec time stats:")
            print(f"  Avg: {stats['avg_exec_time']:.2f}s")
            print(f"  Min: {stats['min_exec_time']:.2f}s")
            print(f"  Max: {stats['max_exec_time']:.2f}s")

        print(f"{'='*60}\n")

    def _print_full_test_statistics(self, stats: Dict[str, Any], cleanup: bool):
        """Print full test statistics."""
        print(f"\n{'='*60}")
        print("Full Test Results")
        print(f"{'='*60}")
        print(f"Total: {stats['total']}")
        print(f"Create success: {stats['create_success']}")
        print(f"Exec success: {stats['exec_success']}")
        if cleanup:
            print(f"Cleanup success: {stats['cleanup_success']}")
        print(f"Total time: {stats['overall_time']:.2f}s")

        if 'avg_create_time' in stats:
            print(f"\nAvg create time: {stats['avg_create_time']:.2f}s")
        if 'avg_exec_time' in stats:
            print(f"Avg exec time: {stats['avg_exec_time']:.2f}s")

        print(f"{'='*60}\n")

    def save_results(self, filename: Optional[str] = None):
        """Save test results to JSON file."""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"async_test_results_{timestamp}.json"

        filepath = Path(__file__).parent / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)

        print(f"Results saved to: {filepath}")


async def main():
    """Main test function."""
    import argparse

    parser = argparse.ArgumentParser(description="Async concurrent sandbox test")
    parser.add_argument("--url", default="http://localhost:8000", help="Orchestrator URL")
    parser.add_argument("--num", type=int, default=10, help="Sandbox 数量")
    parser.add_argument("--image", default="mirror.gcr.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-12907", help="Docker image")
    parser.add_argument("--cpu", default="2", help="CPU resources")
    parser.add_argument("--memory", default="8Gi", help="Memory resources")
    parser.add_argument("--test-type", choices=["create", "exec", "full"], default="full",
                        help="Test type: create=create only, exec=exec only, full=full lifecycle")
    parser.add_argument("--no-cleanup", action="store_true", help="Don't cleanup sandboxes")
    parser.add_argument("--phased", action="store_true",
                        help="Phased execution (create all -> exec all -> delete all)")
    parser.add_argument("--command", default="python -c 'import sys; print(f\"Python {sys.version}\")'",
                        help="Command to execute")
    parser.add_argument("--api-key", default=None, help="API key for authentication (or set SANDBOX_API_KEY env var)")
    parser.add_argument("--save-timing", default=None, help="Save detailed timing data to JSON file")

    args = parser.parse_args()

    tester = AsyncConcurrentSandboxTest(base_url=args.url, timeout=600, api_key=args.api_key)

    # Start timing
    tracker.start()

    try:
        if args.test_type == "create":
            stats = await tester.test_concurrent_create(
                num_sandboxes=args.num,
                image=args.image,
                cpu=args.cpu,
                memory=args.memory,
            )
            tester.save_results()

        elif args.test_type == "exec":
            print("Step 1: Creating sandboxes...")
            create_stats = await tester.test_concurrent_create(
                num_sandboxes=args.num,
                image=args.image,
                cpu=args.cpu,
                memory=args.memory,
            )

            if create_stats['successful'] > 0:
                print("\nStep 2: Concurrent exec...")
                await tester.test_concurrent_exec(
                    sandbox_ids=create_stats['sandbox_ids'],
                    command=args.command,
                )

        elif args.test_type == "full":
            await tester.test_concurrent_create_and_exec(
                num_sandboxes=args.num,
                image=args.image,
                command=args.command,
                cpu=args.cpu,
                memory=args.memory,
                cleanup=not args.no_cleanup,
                phased=args.phased,
            )

    finally:
        # Always print timing stats
        tracker.stop()
        tracker.print_summary()

        if args.save_timing:
            tracker.save_to_json(args.save_timing)
        else:
            # Default: save to tests/ directory
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            default_path = str(Path(__file__).parent / f"timing_{timestamp}.json")
            tracker.save_to_json(default_path)


if __name__ == "__main__":
    asyncio.run(main())
