#!/usr/bin/env python3
"""
Running agent harnesses inside a sandbox.

Every sandbox ships with five agent harnesses already on ``PATH`` — ``codex``,
``claude``, ``pi``, ``opencode`` and ``hermes`` — regardless of the base image.
Nothing is installed at runtime and no network access is needed inside the
sandbox to make them available.

This is what makes any-harness training practical: switching the harness you
collect rollouts under is a change of command, not a change of image.

Prerequisites:
    - A running orchestrator with ENABLE_SANDBOX_TOOLS=true (the default)
    - Model-provider credentials for whichever harness you drive

Usage:
    export SANDBOX_BASE_URL="http://your-orchestrator-host"
    export SANDBOX_API_KEY="your-api-key"
    export OPENAI_API_KEY="sk-..."          # only for the codex example
    export ANTHROPIC_API_KEY="sk-ant-..."   # only for the claude example

    python examples/agent_harness.py                 # inspect only, no credentials needed
    python examples/agent_harness.py --run codex
    python examples/agent_harness.py --run claude
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchard_env import SandboxClient

HARNESSES = ["codex", "claude", "pi", "opencode", "hermes"]

# Credential each harness needs, and the command shape used to drive it
# non-interactively.
RUNNERS = {
    "codex": {
        "env_var": "OPENAI_API_KEY",
        "command": "codex exec 'List the files in this directory and summarize them.'",
    },
    "claude": {
        "env_var": "ANTHROPIC_API_KEY",
        "command": "claude -p 'List the files in this directory and summarize them.'",
    },
}


def inspect_harnesses(image: str) -> None:
    """Show that the harnesses are present in an arbitrary base image."""
    print("\n" + "=" * 60)
    print(f"Harnesses available in {image}")
    print("=" * 60)

    # block_network=True is fine here: we only inspect, we don't call model APIs.
    with SandboxClient() as client:
        with client.create_sandbox(image=image, block_network=True) as sandbox:
            print(f"\nSandbox: {sandbox.sandbox_id}\n")

            for name in HARNESSES:
                result = sandbox.exec(f"command -v {name} || echo '<not found>'")
                print(f"  {name:<10} {result.stdout.strip()}")

            print("\nBaked-in versions:")
            result = sandbox.exec("cat /opt/sandbox-tools/VERSIONS")
            if result.succeeded:
                for line in result.stdout.strip().splitlines():
                    print(f"  {line}")
            else:
                print("  (VERSIONS file not readable — are the tools enabled?)")

            # The harnesses are appended to PATH, so an existing toolchain in the
            # image keeps priority over them.
            result = sandbox.exec("echo $PATH")
            print(f"\nPATH: {result.stdout.strip()}")


def run_harness(harness: str, image: str) -> None:
    """Drive one harness against a small repo inside the sandbox."""
    spec = RUNNERS[harness]
    env_var = spec["env_var"]
    api_key = os.getenv(env_var)

    print("\n" + "=" * 60)
    print(f"Running the {harness} harness")
    print("=" * 60)

    if not api_key:
        print(f"\nSkipped: {env_var} is not set.")
        return

    # block_network=False: the harness itself needs egress to reach the model API.
    with SandboxClient() as client:
        with client.create_sandbox(image=image, block_network=False) as sandbox:
            print(f"\nSandbox: {sandbox.sandbox_id}")

            # Give the harness something to look at.
            sandbox.upload_content(
                b"def add(a, b):\n    return a + b\n",
                "/workspace/calculator.py",
            )
            sandbox.upload_content(
                b"# Demo project\n\nA tiny calculator.\n",
                "/workspace/README.md",
            )

            print(f"\n$ {spec['command']}")
            result = sandbox.exec(
                spec["command"],
                cwd="/workspace",
                # Credentials are passed per call — never baked into the image.
                env={env_var: api_key},
                timeout=600,
            )

            print(f"\nExit code: {result.exit_code}")
            print(f"\n{result.stdout.strip()}")
            if result.stderr.strip():
                print(f"\n[stderr]\n{result.stderr.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run agent harnesses inside an orchard_env sandbox"
    )
    parser.add_argument(
        "--image",
        default="ubuntu:22.04",
        help="base image to use (default: ubuntu:22.04 — deliberately not a Python image)",
    )
    parser.add_argument(
        "--run",
        choices=sorted(RUNNERS),
        help="actually drive a harness (requires that provider's API key)",
    )
    args = parser.parse_args()

    if args.run:
        run_harness(args.run, args.image)
    else:
        inspect_harnesses(args.image)


if __name__ == "__main__":
    main()
