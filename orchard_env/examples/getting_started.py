#!/usr/bin/env python3
"""
Getting Started: driving sandboxes with the orchard_env SDK.

Covers the synchronous and asynchronous clients, file operations, git patches,
custom resources, error handling, PTY sessions, and a multi-turn agent session.

Prerequisites:
    - A running orchestrator (see ../docs/deployment.md)
    - pip install -e "orchard_env[dev]"

Usage:
    export SANDBOX_BASE_URL="http://your-orchestrator-host"
    export SANDBOX_API_KEY="your-api-key"

    python examples/getting_started.py              # run everything
    python examples/getting_started.py basic files  # run selected examples
    python examples/getting_started.py --help       # list example names
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Allow running straight from a checkout without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchard_env import AsyncSandboxClient, SandboxClient

# =============================================================================
# Example 1: Basic Synchronous Usage
# =============================================================================


def example_basic_sync():
    """
    Basic synchronous example: Create a sandbox, run commands, and clean up.

    This is the simplest way to use the sandbox client.
    """
    print("\n" + "=" * 60)
    print("Example 1: Basic Synchronous Usage")
    print("=" * 60)

    # Using context managers ensures proper cleanup
    with SandboxClient() as client:
        # Check service health first
        health = client.health()
        print(f"Service health: {health}")

        # Create a sandbox with a Python image.
        # Sandboxes have no outbound network access by default
        # (block_network=True); see the "resources" example for enabling it.
        print("\nCreating sandbox...")
        with client.create_sandbox(image="python:3.11-slim") as sandbox:
            print(f"✓ Sandbox created: {sandbox.sandbox_id}")

            # Execute a simple command
            result = sandbox.exec("echo 'Hello from sandbox!'")
            print(f"\nCommand output: {result.stdout.strip()}")
            print(f"Exit code: {result.exit_code}")

            # Run Python code
            result = sandbox.exec('python -c "print(2 + 2)"')
            print(f"\nPython result: {result.stdout.strip()}")

            # Inspect the environment — no network needed
            result = sandbox.exec("python --version && whoami && pwd")
            print(f"\nEnvironment:\n{result.stdout.strip()}")

            # Sandbox is automatically deleted when exiting the context
        print("\n✓ Sandbox automatically deleted")


# =============================================================================
# Example 2: File Operations
# =============================================================================


def example_file_operations():
    """
    Demonstrate file upload, download, and listing operations.
    """
    print("\n" + "=" * 60)
    print("Example 2: File Operations")
    print("=" * 60)

    with SandboxClient() as client:
        with client.create_sandbox(image="python:3.11-slim") as sandbox:
            print(f"Sandbox created: {sandbox.sandbox_id}")

            # Upload content directly (as bytes)
            python_code = b"""
import json

data = {
    "message": "Hello from uploaded script!",
    "numbers": [1, 2, 3, 4, 5],
    "sum": sum([1, 2, 3, 4, 5])
}

print(json.dumps(data, indent=2))
"""
            sandbox.upload_content(python_code, "/workspace/script.py")
            print("\n✓ Uploaded Python script")

            # List files in workspace
            files = sandbox.list_files("/workspace")
            print("\nFiles in /workspace:")
            for f in files:
                print(f"  {f['type']:10} {f['size']:>8} bytes  {f['name']}")

            # Execute the uploaded script
            result = sandbox.exec("python /workspace/script.py")
            print(f"\nScript output:\n{result.stdout}")

            # Download the file content back
            content = sandbox.download_content("/workspace/script.py")
            print(f"✓ Downloaded {len(content)} bytes")

            # Create a file via exec and download it
            sandbox.exec("echo 'Generated in sandbox' > /workspace/output.txt")
            output = sandbox.download_content("/workspace/output.txt")
            print(f"\nGenerated file content: {output.decode().strip()}")


# =============================================================================
# Example 3: Working with Environment Variables and Working Directory
# =============================================================================


def example_env_and_cwd():
    """
    Demonstrate using environment variables and working directory options.
    """
    print("\n" + "=" * 60)
    print("Example 3: Environment Variables and Working Directory")
    print("=" * 60)

    with SandboxClient() as client:
        with client.create_sandbox(image="python:3.11-slim") as sandbox:
            print(f"Sandbox created: {sandbox.sandbox_id}")

            # Execute with custom environment variables.
            # Note the quoting: keep the inner command free of nested double
            # quotes, since the whole string is handed to `bash -c`.
            result = sandbox.exec(
                "printenv API_KEY DEBUG",
                env={"API_KEY": "secret123", "DEBUG": "true"},
            )
            print(f"\nWith custom env vars:\n{result.stdout.strip()}")

            # The same thing read from Python inside the sandbox
            result = sandbox.exec(
                "python -c 'import os; print(os.environ[\"API_KEY\"])'",
                env={"API_KEY": "secret123"},
            )
            print(f"Read from Python: {result.stdout.strip()}")

            # Create a directory and work inside it
            sandbox.exec("mkdir -p /workspace/myproject")

            # Execute with custom working directory
            result = sandbox.exec("pwd", cwd="/workspace/myproject")
            print(f"Working directory: {result.stdout.strip()}")

            # Create files in the working directory
            result = sandbox.exec(
                "touch file1.txt file2.txt && ls -la", cwd="/workspace/myproject"
            )
            print(f"\nFiles in myproject:\n{result.stdout}")


# =============================================================================
# Example 4: Applying Git Patches
# =============================================================================


def example_git_patch():
    """
    Demonstrate applying git patches (useful for SWE-bench type tasks).
    """
    print("\n" + "=" * 60)
    print("Example 4: Applying Git Patches")
    print("=" * 60)

    with SandboxClient() as client:
        # block_network=False so apt-get can reach the Debian mirrors.
        # If your base image already ships git (most SWE-bench images do), the
        # default block_network=True is enough.
        with client.create_sandbox(
            image="python:3.11-slim", block_network=False
        ) as sandbox:
            print(f"Sandbox created: {sandbox.sandbox_id}")

            # Initialize a git repo and create a file
            result = sandbox.exec(
                "command -v git || apt-get update -qq && apt-get install -y git -qq",
                timeout=300,
            )
            if not result.succeeded:
                print(f"  Could not install git, skipping: {result.stderr.strip()}")
                return

            sandbox.exec("git config --global user.email 'test@example.com'")
            sandbox.exec("git config --global user.name 'Test User'")
            sandbox.exec("cd /workspace && git init", cwd="/workspace")

            # Create initial file
            initial_code = b"""def greet(name):
    return f"Hello, {name}"

if __name__ == "__main__":
    print(greet("World"))
"""
            sandbox.upload_content(initial_code, "/workspace/app.py")
            sandbox.exec(
                "git add app.py && git commit -m 'Initial commit'", cwd="/workspace"
            )

            print("\n✓ Created initial file and git repo")

            # Show original file
            result = sandbox.exec("cat /workspace/app.py")
            print(f"\nOriginal file:\n{result.stdout}")

            # Create and apply a patch
            patch = """--- a/app.py
+++ b/app.py
@@ -1,5 +1,9 @@
 def greet(name):
-    return f"Hello, {name}"
+    \"\"\"Greet someone by name.\"\"\"
+    return f"Hello, {name}!"
+
+def farewell(name):
+    return f"Goodbye, {name}!"

 if __name__ == "__main__":
     print(greet("World"))
"""

            result = sandbox.apply_patch(patch)
            print(f"✓ Patch applied: {result}")

            # Show modified file
            result = sandbox.exec("cat /workspace/app.py")
            print(f"\nModified file:\n{result.stdout}")

            # Run the modified code
            result = sandbox.exec("python /workspace/app.py")
            print(f"Output: {result.stdout.strip()}")


# =============================================================================
# Example 5: Async Usage
# =============================================================================


async def example_async_basic():
    """
    Basic async example - useful for concurrent operations.
    """
    print("\n" + "=" * 60)
    print("Example 5: Async Usage")
    print("=" * 60)

    async with AsyncSandboxClient() as client:
        # Create sandbox
        print("\nCreating sandbox asynchronously...")
        async with await client.create_sandbox(image="python:3.11-slim") as sandbox:
            print(f"✓ Sandbox created: {sandbox.sandbox_id}")

            # Execute commands
            result = await sandbox.exec("echo 'Hello from async sandbox!'")
            print(f"Output: {result.stdout.strip()}")

            # Upload and run a script
            code = b"print('Async operations complete!')"
            await sandbox.upload_content(code, "/workspace/test.py")
            result = await sandbox.exec("python /workspace/test.py")
            print(f"Script output: {result.stdout.strip()}")

        print("✓ Sandbox automatically deleted")


# =============================================================================
# Example 6: Concurrent Sandboxes
# =============================================================================


async def example_concurrent_sandboxes():
    """
    Create and run multiple sandboxes concurrently.
    """
    print("\n" + "=" * 60)
    print("Example 6: Concurrent Sandboxes")
    print("=" * 60)

    async def run_task(client, task_id: int):
        """Run a task in its own sandbox."""
        async with await client.create_sandbox(image="python:3.11-slim") as sandbox:
            print(f"  [Task {task_id}] Sandbox {sandbox.sandbox_id} created")

            # Simulate some work
            result = await sandbox.exec(
                f"python -c \"import time; time.sleep(0.5); print('Task {task_id} complete')\""
            )
            return {
                "task_id": task_id,
                "sandbox_id": sandbox.sandbox_id,
                "output": result.stdout.strip(),
            }

    async with AsyncSandboxClient() as client:
        # Create 3 sandboxes concurrently
        print("\nStarting 3 concurrent tasks...")
        tasks = [run_task(client, i) for i in range(1, 4)]
        results = await asyncio.gather(*tasks)

        print("\nResults:")
        for r in results:
            print(f"  Task {r['task_id']}: {r['output']}")


# =============================================================================
# Example 7: Custom Resources
# =============================================================================


def example_custom_resources():
    """
    Create sandbox with custom CPU and memory resources.
    """
    print("\n" + "=" * 60)
    print("Example 7: Custom Resources")
    print("=" * 60)

    with SandboxClient() as client:
        # Create sandbox with custom resources
        print("\nCreating sandbox with custom resources (2 CPU, 4Gi memory)...")
        with client.create_sandbox(
            image="python:3.11-slim",
            cpu="2",
            memory="4Gi",
            block_network=False,  # Allow network access
        ) as sandbox:
            print(f"✓ Sandbox created: {sandbox.sandbox_id}")

            # Show available resources
            result = sandbox.exec("cat /proc/cpuinfo | grep processor | wc -l")
            print(f"CPU cores visible: {result.stdout.strip()}")

            result = sandbox.exec("cat /proc/meminfo | head -1")
            print(f"Memory info: {result.stdout.strip()}")


# =============================================================================
# Example 8: Error Handling
# =============================================================================


def example_error_handling():
    """
    Demonstrate proper error handling.
    """
    print("\n" + "=" * 60)
    print("Example 8: Error Handling")
    print("=" * 60)

    with SandboxClient() as client:
        with client.create_sandbox(image="python:3.11-slim") as sandbox:
            print(f"Sandbox created: {sandbox.sandbox_id}")

            # Command that will fail
            result = sandbox.exec("exit 1")
            print("\nCommand 'exit 1':")
            print(f"  Succeeded: {result.succeeded}")
            print(f"  Failed: {result.failed}")
            print(f"  Exit code: {result.exit_code}")

            # Command that produces stderr
            result = sandbox.exec("ls /nonexistent")
            print("\nCommand 'ls /nonexistent':")
            print(f"  Exit code: {result.exit_code}")
            print(f"  Stderr: {result.stderr.strip()}")

            # Timeout handling
            result = sandbox.exec("sleep 10", timeout=2)
            print("\nCommand with timeout:")
            print(f"  Status: {result.status}")
            if result.error:
                print(f"  Error: {result.error}")


# =============================================================================
# Example 8b: Interactive PTY (sandbox.exec(pty=True))
# =============================================================================


def example_pty():
    """
    Run commands on a real PTY inside the sandbox.

    Use this when a program needs ``isatty() == True`` (REPLs, ``pip``, ``apt``,
    progress bars, ``less`` / ``vim`` / ``top``), when you want to stream output
    as it is produced instead of buffering until the command completes, or when
    you need to drive stdin / send signals interactively.

    Returns a ``ContainerProcess`` handle instead of a ``JobResult``.
    """
    print("\n" + "=" * 60)
    print("Example 8b: Interactive PTY")
    print("=" * 60)

    with SandboxClient() as client:
        with client.create_sandbox("ubuntu:22.04") as sandbox:
            # ── (1) verify a real TTY is allocated and propagate exit code ──
            print("\n[1] tty -s && exit 7")
            proc = sandbox.exec(
                ["bash", "-c", "tty -s && echo HAS_TTY; echo done; exit 7"],
                pty=True,
                rows=24,
                cols=80,
            )
            try:
                out = proc.read_all(timeout=10)
                rc = proc.wait(timeout=5)
                print(f"  pid={proc.pid} rc={rc}")
                print(f"  output: {out.decode(errors='replace').strip()!r}")
            finally:
                proc.close()

            # ── (2) drive stdin, observe CRLF echo from the line discipline ──
            print("\n[2] cat – stdin round-trip")
            proc = sandbox.exec(["cat"], pty=True)
            try:
                proc.write_stdin(b"ping\n")
                chunk = proc.read(timeout=2)  # PTY echoes 'ping\r\n'
                print(f"  echoed: {chunk!r}")
                proc.kill("TERM")
                proc.wait(timeout=3)
            finally:
                proc.close()

            # ── (3) signal a long-running process ──
            print("\n[3] kill sleep 60")
            proc = sandbox.exec(["sleep", "60"], pty=True)
            try:
                import time as _t

                t0 = _t.time()
                proc.kill("KILL")
                rc = proc.wait(timeout=5)
                print(f"  rc={rc}  kill latency={(_t.time()-t0)*1000:.0f}ms")
            finally:
                proc.close()

            # ── (4) update window size mid-session (TIOCSWINSZ) ──
            print("\n[4] resize")
            proc = sandbox.exec(
                ["bash", "-c", "stty size; sleep 0.4; stty size"],
                pty=True,
                rows=24,
                cols=80,
            )
            try:
                import time as _t

                _t.sleep(0.1)
                proc.resize(50, 200)
                out = proc.read_all(timeout=5)
                proc.wait(timeout=5)
                print(f"  output: {out.decode(errors='replace').strip()}")
            finally:
                proc.close()


async def example_pty_async():
    """Same as ``example_pty`` but using ``AsyncSandboxClient``."""
    print("\n" + "=" * 60)
    print("Example 8c: Interactive PTY (async)")
    print("=" * 60)

    async with AsyncSandboxClient() as client:
        sandbox = await client.create_sandbox("ubuntu:22.04")
        try:
            proc = await sandbox.exec(
                [
                    "bash",
                    "-c",
                    "echo $$; for i in 1 2 3; do echo line $i; sleep 0.1; done",
                ],
                pty=True,
            )
            try:
                out = await proc.read_all(timeout=10)
                rc = await proc.wait(timeout=5)
                print(f"  pid={proc.pid} rc={rc}")
                print(f"  output:\n{out.decode(errors='replace')}")
            finally:
                await proc.close()
        finally:
            await sandbox.delete()


# =============================================================================
# Example 9: Interactive Multi-Turn Session
# =============================================================================


def example_multi_turn_session():
    """
    Demonstrate a multi-turn interactive session (like SWE-bench agent workflow).
    """
    print("\n" + "=" * 60)
    print("Example 9: Multi-Turn Session (Agent Workflow)")
    print("=" * 60)

    with SandboxClient() as client:
        with client.create_sandbox(image="python:3.11-slim") as sandbox:
            print(f"Sandbox created: {sandbox.sandbox_id}")

            # Turn 1: Explore the environment
            print("\n[Turn 1] Exploring environment...")
            result = sandbox.exec("python --version && pip --version")
            print(f"  {result.stdout.strip()}")

            # Turn 2: Create project structure
            print("\n[Turn 2] Creating project structure...")
            sandbox.exec("mkdir -p /workspace/myapp/{src,tests}")
            result = sandbox.exec("find /workspace/myapp -type d")
            print(f"  Directories: {result.stdout.strip()}")

            # Turn 3: Write code
            print("\n[Turn 3] Writing application code...")
            app_code = b'''"""Simple calculator module."""

def add(a, b):
    """Add two numbers."""
    return a + b

def subtract(a, b):
    """Subtract b from a."""
    return a - b

def multiply(a, b):
    """Multiply two numbers."""
    return a * b

def divide(a, b):
    """Divide a by b."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
'''
            sandbox.upload_content(app_code, "/workspace/myapp/src/calculator.py")
            print("  ✓ Created calculator.py")

            # Turn 4: Write tests
            print("\n[Turn 4] Writing tests...")
            test_code = '''"""Tests for calculator module."""
import sys
sys.path.insert(0, "/workspace/myapp/src")

from calculator import add, subtract, multiply, divide

def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    print("[PASS] test_add passed")

def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(1, 1) == 0
    print("[PASS] test_subtract passed")

def test_multiply():
    assert multiply(3, 4) == 12
    assert multiply(-2, 3) == -6
    print("[PASS] test_multiply passed")

def test_divide():
    assert divide(10, 2) == 5
    try:
        divide(1, 0)
        assert False, "Should raise ValueError"
    except ValueError:
        pass
    print("[PASS] test_divide passed")

if __name__ == "__main__":
    test_add()
    test_subtract()
    test_multiply()
    test_divide()
    print("\\nAll tests passed!")
'''
            sandbox.upload_content(
                test_code.encode(), "/workspace/myapp/tests/test_calculator.py"
            )
            print("  ✓ Created test_calculator.py")

            # Turn 5: Run tests
            print("\n[Turn 5] Running tests...")
            result = sandbox.exec("python /workspace/myapp/tests/test_calculator.py")
            print(f"  {result.stdout}")

            # Turn 6: Verify final state
            print("[Turn 6] Final project structure:")
            result = sandbox.exec("find /workspace/myapp -type f -name '*.py'")
            for line in result.stdout.strip().split("\n"):
                print(f"  {line}")


# =============================================================================
# Main
# =============================================================================

EXAMPLES = {
    "basic": example_basic_sync,
    "files": example_file_operations,
    "env": example_env_and_cwd,
    "patch": example_git_patch,
    "resources": example_custom_resources,
    "errors": example_error_handling,
    "session": example_multi_turn_session,
    "pty": example_pty,
    "async": example_async_basic,
    "concurrent": example_concurrent_sandboxes,
    "pty-async": example_pty_async,
}


def _run(fn):
    """Run an example, awaiting it if it is a coroutine function."""
    if asyncio.iscoroutinefunction(fn):
        asyncio.run(fn())
    else:
        fn()


def main():
    """Run one or all examples."""
    parser = argparse.ArgumentParser(
        description="orchard_env getting-started examples",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="available examples: " + ", ".join(EXAMPLES),
    )
    parser.add_argument(
        "examples",
        nargs="*",
        metavar="EXAMPLE",
        help="which examples to run (default: all)",
    )
    args = parser.parse_args()

    unknown = [name for name in args.examples if name not in EXAMPLES]
    if unknown:
        parser.error(
            f"unknown example(s): {', '.join(unknown)}\n"
            f"available: {', '.join(EXAMPLES)}"
        )

    base_url = os.getenv("SANDBOX_BASE_URL", "http://localhost:8000")

    print("\n" + "=" * 60)
    print("orchard_env — Getting Started Examples")
    print("=" * 60)
    print(f"\nService URL: {base_url}")
    if not os.getenv("SANDBOX_API_KEY"):
        print(
            "Note: SANDBOX_API_KEY is not set. This only works if the "
            "orchestrator runs with REQUIRE_API_KEY=false."
        )

    selected = args.examples or list(EXAMPLES)

    try:
        for name in selected:
            _run(EXAMPLES[name])

        print("\n" + "=" * 60)
        print("✓ All selected examples completed successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ Error running examples: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
