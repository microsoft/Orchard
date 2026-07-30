#!/usr/bin/env python3
"""
Async concurrent PTY test: spin up N sandboxes and exercise the
``sandbox.exec(..., pty=True)`` path through ``AsyncContainerProcess``.

Each sandbox runs a fixed sequence of PTY operations:

  pty_1  – ``tty -s && echo HAS_TTY`` (verifies a real PTY is allocated)
  pty_2  – ``cat`` with stdin write/read round-trip (line-discipline echo)
  pty_3  – ``sleep 60`` followed by ``kill('KILL')`` (signal delivery)
  pty_4  – ``stty size`` before/after ``resize`` (TIOCSWINSZ propagation)

Timing records and the lifecycle summary table are reused verbatim from
``bench_concurrent``.  Run modes mirror that script:

    --test-type pty       only run PTY ops on pre-created sandboxes (--sandbox-ids)
    --test-type full      create -> pty ops -> delete (default, supports --phased)
    --phased              create-all / pty-all / delete-all instead of interleaved
"""

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Add project root to Python path
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

from orchard_env import AsyncSandboxClient
from tests.integration.bench_concurrent import TimingRecord, TimingTracker

# Global tracker (independent of the one in bench_concurrent)
tracker = TimingTracker()


# ────────────────────────────────────────────────────────────
# PTY interaction primitives
# ────────────────────────────────────────────────────────────


async def _pty_op_tty(sandbox) -> dict[str, Any]:
    """pty_1: verify real TTY allocation + exit code propagation."""
    proc = await sandbox.exec(
        ["bash", "-c", "tty -s && echo HAS_TTY; echo done; exit 7"],
        pty=True,
    )
    try:
        out = await proc.read_all(timeout=15)
        rc = await proc.wait(timeout=10)
        ok = rc == 7 and b"HAS_TTY" in out and b"done" in out
        return {
            "ok": ok,
            "detail": f"rc={rc} bytes={len(out)}",
            "error": None if ok else f"unexpected rc={rc} out={out!r}",
        }
    finally:
        await proc.close()


async def _pty_op_stdin(sandbox) -> dict[str, Any]:
    """pty_2: stdin echo round-trip through ``cat``."""
    proc = await sandbox.exec(["cat"], pty=True)
    try:
        token = f"pty-{int(time.time() * 1000) & 0xFFFF:x}".encode()
        await proc.write_stdin(token + b"\n")
        # Real PTYs return CRLF; allow up to ~2s for the echo to arrive.
        deadline = time.time() + 2.0
        buf = b""
        while time.time() < deadline:
            try:
                chunk = await proc.read(timeout=deadline - time.time())
            except TimeoutError:
                break
            if not chunk:
                break
            buf += chunk
            if token in buf:
                break
        await proc.kill("TERM")
        rc = await proc.wait(timeout=5)
        ok = token in buf
        return {
            "ok": ok,
            "detail": f"rc={rc} echoed={token in buf}",
            "error": None if ok else f"echo not received: {buf!r}",
        }
    finally:
        await proc.close()


async def _pty_op_kill(sandbox) -> dict[str, Any]:
    """pty_3: SIGKILL delivery to a long-running process."""
    proc = await sandbox.exec(["sleep", "60"], pty=True)
    try:
        t0 = time.time()
        await proc.kill("KILL")
        rc = await proc.wait(timeout=5)
        elapsed = time.time() - t0
        # rc == -9 (SIGKILL) and the round-trip should be sub-second
        ok = rc == -9 and elapsed < 3.0
        return {
            "ok": ok,
            "detail": f"rc={rc} kill_latency={elapsed*1000:.0f}ms",
            "error": None if ok else f"rc={rc} elapsed={elapsed:.2f}s",
        }
    finally:
        await proc.close()


async def _pty_op_resize(sandbox) -> dict[str, Any]:
    """pty_4: TIOCSWINSZ propagation – stty size before and after resize."""
    proc = await sandbox.exec(
        ["bash", "-c", "stty size; sleep 0.4; stty size; exit 0"],
        pty=True,
        rows=24,
        cols=80,
    )
    try:
        # Give the first `stty size` a moment to print under the initial 24x80
        await asyncio.sleep(0.1)
        await proc.resize(50, 200)
        out = await proc.read_all(timeout=10)
        rc = await proc.wait(timeout=5)
        ok = rc == 0 and b"24 80" in out and b"50 200" in out
        return {
            "ok": ok,
            "detail": f"rc={rc} bytes={len(out)}",
            "error": None if ok else f"resize not observed: {out!r}",
        }
    finally:
        await proc.close()


# Ordered sequence: (op_name, label, coroutine_factory)
PTY_OPS = [
    ("pty_1", "tty -s", _pty_op_tty),
    ("pty_2", "cat stdin", _pty_op_stdin),
    ("pty_3", "kill sleep", _pty_op_kill),
    ("pty_4", "resize", _pty_op_resize),
]


# ────────────────────────────────────────────────────────────
# Test harness
# ────────────────────────────────────────────────────────────


class AsyncConcurrentPtyTest:
    """Run PTY operations concurrently across many sandboxes."""

    def __init__(self, base_url: str, timeout: int = 600, api_key: str | None = None):
        self.base_url = base_url
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("SANDBOX_API_KEY")

    # ----- single-sandbox helpers (record per op) -----

    async def _run_pty_sequence(self, sandbox, index: int):
        """Run PTY_OPS sequentially on one sandbox, recording each."""
        for op_name, label, factory in PTY_OPS:
            rec = TimingRecord(
                sandbox_index=index,
                sandbox_id=sandbox.sandbox_id,
                operation=op_name,
                command=label,
            )
            start = time.time()
            rec.start_time = start
            try:
                res = await factory(sandbox)
                rec.end_time = time.time()
                rec.duration = rec.end_time - start
                rec.success = bool(res.get("ok"))
                if not rec.success:
                    rec.error = (res.get("error") or "failed")[:200]
                marker = "OK" if rec.success else "FAIL"
                print(
                    f"[{index}] {op_name} {marker}: {res.get('detail','')} ({rec.duration:.2f}s)"
                )
            except Exception as e:
                rec.end_time = time.time()
                rec.duration = rec.end_time - start
                rec.error = str(e)[:200]
                print(f"[{index}] {op_name} FAIL: {e}")
            await tracker.record(rec)

    # ----- public entry points -----

    async def test_pty_only(self, sandbox_ids: list[str]):
        """Run PTY sequence on a pre-existing list of sandboxes."""
        print(f"\n{'='*60}")
        print(f"PTY-only test: {len(sandbox_ids)} sandboxes, {len(PTY_OPS)} ops each")
        print(f"{'='*60}\n")

        async with AsyncSandboxClient(
            self.base_url, timeout=self.timeout, api_key=self.api_key
        ) as client:

            async def per_sandbox(idx: int, sid: str):
                sandbox = await client.get_sandbox(sid)
                await self._run_pty_sequence(sandbox, idx)

            await asyncio.gather(
                *[per_sandbox(i + 1, sid) for i, sid in enumerate(sandbox_ids)]
            )

    async def test_full(
        self,
        num_sandboxes: int,
        image: str,
        cpu: str,
        memory: str,
        cleanup: bool,
        phased: bool,
    ):
        if phased:
            await self._test_phased(num_sandboxes, image, cpu, memory, cleanup)
        else:
            await self._test_interleaved(num_sandboxes, image, cpu, memory, cleanup)

    # ----- interleaved: create -> pty ops -> delete per sandbox -----

    async def _test_interleaved(self, num_sandboxes, image, cpu, memory, cleanup):
        print(f"\n{'='*60}")
        print(f"PTY full test (interleaved): {num_sandboxes} sandboxes")
        print(f"Image: {image}  CPU={cpu}  Mem={memory}")
        print(f"{'='*60}\n")

        async with AsyncSandboxClient(
            self.base_url, timeout=self.timeout, api_key=self.api_key
        ) as client:

            async def lifecycle(index: int):
                # Create
                rec_c = TimingRecord(
                    sandbox_index=index, sandbox_id=None, operation="create"
                )
                start = time.time()
                rec_c.start_time = start
                sandbox = None
                try:
                    sandbox = await client.create_sandbox(
                        image=image,
                        block_network=False,
                        cpu=cpu,
                        memory=memory,
                    )
                    rec_c.end_time = time.time()
                    rec_c.duration = rec_c.end_time - start
                    rec_c.sandbox_id = sandbox.sandbox_id
                    rec_c.success = True
                    print(
                        f"[{index}] Created: {sandbox.sandbox_id} ({rec_c.duration:.2f}s)"
                    )
                except Exception as e:
                    rec_c.end_time = time.time()
                    rec_c.duration = rec_c.end_time - start
                    rec_c.error = str(e)[:200]
                    print(f"[{index}] FAIL create: {e}")
                await tracker.record(rec_c)
                if sandbox is None:
                    return

                # PTY sequence
                try:
                    await self._run_pty_sequence(sandbox, index)
                finally:
                    if cleanup:
                        rec_d = TimingRecord(
                            sandbox_index=index,
                            sandbox_id=sandbox.sandbox_id,
                            operation="delete",
                        )
                        start = time.time()
                        rec_d.start_time = start
                        try:
                            await sandbox.delete()
                            rec_d.end_time = time.time()
                            rec_d.duration = rec_d.end_time - start
                            rec_d.success = True
                            print(f"[{index}] Deleted ({rec_d.duration:.2f}s)")
                        except Exception as e:
                            rec_d.end_time = time.time()
                            rec_d.duration = rec_d.end_time - start
                            rec_d.error = str(e)[:200]
                            print(f"[{index}] FAIL delete: {e}")
                        await tracker.record(rec_d)

            await asyncio.gather(*[lifecycle(i) for i in range(1, num_sandboxes + 1)])

    # ----- phased: create-all / pty-all / delete-all -----

    async def _test_phased(self, num_sandboxes, image, cpu, memory, cleanup):
        print(f"\n{'='*60}")
        print(f"PTY full test (phased): {num_sandboxes} sandboxes")
        print(f"Image: {image}  CPU={cpu}  Mem={memory}")
        print(f"{'='*60}\n")

        async with AsyncSandboxClient(
            self.base_url, timeout=self.timeout, api_key=self.api_key
        ) as client:
            # Phase 1: create
            print(
                f"\n{'─'*40}\n  Phase 1/3: Create {num_sandboxes} sandboxes\n{'─'*40}"
            )

            async def do_create(index: int):
                rec = TimingRecord(
                    sandbox_index=index, sandbox_id=None, operation="create"
                )
                start = time.time()
                rec.start_time = start
                sandbox = None
                try:
                    sandbox = await client.create_sandbox(
                        image=image,
                        block_network=False,
                        cpu=cpu,
                        memory=memory,
                    )
                    rec.end_time = time.time()
                    rec.duration = rec.end_time - start
                    rec.sandbox_id = sandbox.sandbox_id
                    rec.success = True
                    print(
                        f"[{index}] Created: {sandbox.sandbox_id} ({rec.duration:.2f}s)"
                    )
                except Exception as e:
                    rec.end_time = time.time()
                    rec.duration = rec.end_time - start
                    rec.error = str(e)[:200]
                    print(f"[{index}] FAIL create: {e}")
                await tracker.record(rec)
                return index, sandbox

            t0 = time.time()
            created = await asyncio.gather(
                *[do_create(i) for i in range(1, num_sandboxes + 1)]
            )
            print(f"\n  Phase 1 done in {time.time()-t0:.2f}s")
            live = [(idx, sb) for idx, sb in created if sb is not None]

            # Phase 2: PTY ops
            print(
                f"\n{'─'*40}\n  Phase 2/3: PTY ops ({len(PTY_OPS)} ops x {len(live)} sandboxes)\n{'─'*40}"
            )
            t0 = time.time()
            await asyncio.gather(*[self._run_pty_sequence(sb, idx) for idx, sb in live])
            print(f"\n  Phase 2 done in {time.time()-t0:.2f}s")

            # Phase 3: delete
            if cleanup:
                print(
                    f"\n{'─'*40}\n  Phase 3/3: Delete {len(live)} sandboxes\n{'─'*40}"
                )

                async def do_delete(index: int, sandbox):
                    rec = TimingRecord(
                        sandbox_index=index,
                        sandbox_id=sandbox.sandbox_id,
                        operation="delete",
                    )
                    start = time.time()
                    rec.start_time = start
                    try:
                        await sandbox.delete()
                        rec.end_time = time.time()
                        rec.duration = rec.end_time - start
                        rec.success = True
                        print(f"[{index}] Deleted ({rec.duration:.2f}s)")
                    except Exception as e:
                        rec.end_time = time.time()
                        rec.duration = rec.end_time - start
                        rec.error = str(e)[:200]
                        print(f"[{index}] FAIL delete: {e}")
                    await tracker.record(rec)

                t0 = time.time()
                await asyncio.gather(*[do_delete(idx, sb) for idx, sb in live])
                print(f"\n  Phase 3 done in {time.time()-t0:.2f}s")


# ────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="Async concurrent PTY test")
    parser.add_argument(
        "--url", default=os.environ.get("SANDBOX_BASE_URL", "http://localhost:8000")
    )
    parser.add_argument("--num", type=int, default=10)
    parser.add_argument("--image", default="mirror.gcr.io/library/ubuntu:22.04")
    parser.add_argument("--cpu", default="1")
    parser.add_argument("--memory", default="2Gi")
    parser.add_argument(
        "--test-type",
        choices=["pty", "full"],
        default="full",
        help="pty = use --sandbox-ids; full = create -> pty -> delete",
    )
    parser.add_argument(
        "--sandbox-ids",
        default="",
        help="(pty mode) comma-separated existing sandbox IDs",
    )
    parser.add_argument(
        "--phased",
        action="store_true",
        help="Phased lifecycle (create-all -> pty-all -> delete-all)",
    )
    parser.add_argument("--no-cleanup", action="store_true")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--save-timing", default=None)
    args = parser.parse_args()

    tester = AsyncConcurrentPtyTest(
        base_url=args.url, timeout=600, api_key=args.api_key
    )
    tracker.start()
    try:
        if args.test_type == "pty":
            ids = [s.strip() for s in args.sandbox_ids.split(",") if s.strip()]
            if not ids:
                print("error: --test-type pty requires --sandbox-ids", file=sys.stderr)
                sys.exit(2)
            await tester.test_pty_only(ids)
        else:
            await tester.test_full(
                num_sandboxes=args.num,
                image=args.image,
                cpu=args.cpu,
                memory=args.memory,
                cleanup=not args.no_cleanup,
                phased=args.phased,
            )
    finally:
        tracker.stop()
        tracker.print_summary()
        if args.save_timing:
            tracker.save_to_json(args.save_timing)
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            tracker.save_to_json(str(Path(__file__).parent / f"pty_timing_{ts}.json"))


if __name__ == "__main__":
    asyncio.run(main())
