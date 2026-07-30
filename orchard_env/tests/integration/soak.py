#!/usr/bin/env python3
"""Soak test: keep a sandbox alive across many commands with idle gaps.

The point is not the commands themselves — ``examples/getting_started.py``
already covers basic exec. What this exercises is the *connection* behaviour
over a long session with idle periods: HTTP keep-alive, connection-pool reuse,
and the client's retry path. A regression here shows up as spurious
``ConnectionError`` / ``ReadTimeout`` on the request that follows a long idle
gap, when a pooled connection races a server-initiated FIN.

Requires a running orchestrator:

    export SANDBOX_BASE_URL="http://your-orchestrator-host"
    export SANDBOX_API_KEY="your-api-key"

    python tests/integration/soak.py                 # sync client, 20 rounds
    python tests/integration/soak.py --mode async
    python tests/integration/soak.py --mode both --rounds 50 --max-idle 30
"""

import argparse
import asyncio
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from orchard_env import AsyncSandboxClient, SandboxClient

IMAGE = "python:3.11-slim"
# NOTE: passed as a single string. The non-PTY exec path joins list commands
# with a bare " ".join(), which loses quoting — see tests/README.md.
COMMAND = "python -c 'print(\"sandbox python execution\")'"


class Outcome:
    """Tally of what happened during a run."""

    def __init__(self, label: str):
        self.label = label
        self.ok = 0
        self.bad = 0
        self.errors: list[str] = []

    def record(self, succeeded: bool, detail: str = "") -> None:
        if succeeded:
            self.ok += 1
        else:
            self.bad += 1
            self.errors.append(detail)

    def report(self) -> bool:
        total = self.ok + self.bad
        print(f"\n{self.label}: {self.ok}/{total} rounds succeeded")
        for err in self.errors[:10]:
            print(f"  - {err}")
        if len(self.errors) > 10:
            print(f"  ... and {len(self.errors) - 10} more")
        return self.bad == 0


def run_sync(rounds: int, min_idle: float, max_idle: float) -> Outcome:
    outcome = Outcome("sync")
    print(f"\n=== sync soak: {rounds} rounds on {IMAGE} ===")

    with SandboxClient() as client:
        with client.create_sandbox(image=IMAGE, cpu="4", memory="16Gi") as sandbox:
            print(f"sandbox: {sandbox.sandbox_id}")

            for i in range(1, rounds + 1):
                idle = random.uniform(min_idle, max_idle)
                try:
                    result = sandbox.exec(COMMAND)
                    outcome.record(
                        result.succeeded,
                        f"round {i}: exit={result.exit_code} stderr={result.stderr.strip()[:120]}",
                    )
                    status = "ok" if result.succeeded else "FAIL"
                except Exception as e:
                    outcome.record(False, f"round {i}: {type(e).__name__}: {e}")
                    status = "ERROR"

                print(f"  [{i}/{rounds}] {status}  (idle {idle:.1f}s)")
                if i < rounds:
                    time.sleep(idle)

    return outcome


async def run_async(rounds: int, min_idle: float, max_idle: float) -> Outcome:
    outcome = Outcome("async")
    print(f"\n=== async soak: {rounds} rounds on {IMAGE} ===")

    async with AsyncSandboxClient() as client:
        async with await client.create_sandbox(image=IMAGE) as sandbox:
            print(f"sandbox: {sandbox.sandbox_id}")

            for i in range(1, rounds + 1):
                idle = random.uniform(min_idle, max_idle)
                try:
                    result = await sandbox.exec(COMMAND)
                    outcome.record(
                        result.succeeded,
                        f"round {i}: exit={result.exit_code} stderr={result.stderr.strip()[:120]}",
                    )
                    status = "ok" if result.succeeded else "FAIL"
                except Exception as e:
                    outcome.record(False, f"round {i}: {type(e).__name__}: {e}")
                    status = "ERROR"

                print(f"  [{i}/{rounds}] {status}  (idle {idle:.1f}s)")
                if i < rounds:
                    await asyncio.sleep(idle)

    return outcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--mode",
        choices=["sync", "async", "both"],
        default="sync",
        help="client to use",
    )
    parser.add_argument("--rounds", type=int, default=20, help="commands per sandbox")
    parser.add_argument("--min-idle", type=float, default=1.0, help="min idle seconds")
    parser.add_argument("--max-idle", type=float, default=10.0, help="max idle seconds")
    args = parser.parse_args()

    outcomes = []
    if args.mode in ("sync", "both"):
        outcomes.append(run_sync(args.rounds, args.min_idle, args.max_idle))
    if args.mode in ("async", "both"):
        outcomes.append(
            asyncio.run(run_async(args.rounds, args.min_idle, args.max_idle))
        )

    sys.exit(0 if all(o.report() for o in outcomes) else 1)


if __name__ == "__main__":
    main()
