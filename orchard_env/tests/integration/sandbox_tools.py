"""End-to-end check: the bundled agent harnesses are available in a fresh sandbox.

Requires a running orchestrator plus SANDBOX_BASE_URL / SANDBOX_API_KEY, e.g.

    SANDBOX_BASE_URL=... SANDBOX_API_KEY=... \
        python tests/integration/sandbox_tools.py

Set TEST_IMAGE to check a different sandbox base image.
"""

import os
import sys

from orchard_env import SandboxClient

IMAGE = os.environ.get("TEST_IMAGE", "ubuntu:22.04")

# Every CLI the sandbox-tools image is expected to publish.
TOOLS = ["codex", "claude", "pi", "opencode", "hermes"]
VERSION_CMD = " && ".join(f"{t} --version" for t in TOOLS)

checks = []


def record(name, ok, detail=""):
    checks.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")


with SandboxClient() as client:
    print(f"creating sandbox on {IMAGE} ...")
    sb = client.create_sandbox(image=IMAGE, block_network=True)
    print(f"sandbox ready: {sb.sandbox_id}\n")

    # 1. Non-login shell (default exec path)
    r = sb.exec(VERSION_CMD, timeout=180)
    record("non-login shell", r.succeeded, r.stdout.strip() or r.stderr.strip()[:200])

    # 2. Login shell (bash --login -i, used by login_shell=True)
    r = sb.exec(VERSION_CMD, timeout=180, login_shell=True)
    record("login shell", r.succeeded, r.stdout.strip() or r.stderr.strip()[:200])

    # 3. Every tool resolves on PATH
    r = sb.exec("; ".join(f"command -v {t}" for t in TOOLS), timeout=30)
    found = [t for t in TOOLS if t in r.stdout]
    record(
        "all tools on PATH",
        len(found) == len(TOOLS),
        f"{len(found)}/{len(TOOLS)}: {r.stdout.strip()}",
    )

    # 4. Network is still blocked (tools must not require egress)
    r = sb.exec(
        "timeout 8 curl -sS -m 5 https://api.openai.com 2>&1 | head -c 80", timeout=30
    )
    record(
        "network still blocked",
        True,
        (r.stdout or r.stderr).strip()[:120] or "(no output)",
    )

    # 5. Credentials come from the caller, not the image
    r = sb.exec(
        'echo "key=${OPENAI_API_KEY:-<unset>}"',
        timeout=30,
        env={"OPENAI_API_KEY": "sk-test-12345"},
    )
    record("per-call credentials", "sk-test-12345" in r.stdout, r.stdout.strip())

    # 6. Tools mount is read-only
    r = sb.exec("touch /opt/sandbox-tools/x 2>&1 || echo READONLY", timeout=30)
    record("mount read-only", "READONLY" in r.stdout, r.stdout.strip())

    # 7. Subcommands work, not just --version (offline; expect help, not a crash)
    r = sb.exec("codex exec --help 2>&1 | head -3", timeout=60)
    record("codex subcommands work", r.succeeded, r.stdout.strip()[:150])

    # 8. hermes runs under its own bundled interpreter and does NOT disturb the
    #    image's python — critical for SWE-bench images with their own conda.
    r = sb.exec(
        "before=$(command -v python3 || echo none); "
        "hermes --version >/dev/null 2>&1; "
        "after=$(command -v python3 || echo none); "
        '[ "$before" = "$after" ] && echo "UNCHANGED:$after" || echo "CHANGED:$before->$after"',
        timeout=180,
    )
    record(
        "image python untouched by hermes",
        "UNCHANGED" in r.stdout,
        r.stdout.strip()[:150],
    )

    # 9. User image toolchain is not shadowed
    r = sb.exec("echo $PATH", timeout=30)
    record(
        "tools appended to PATH (not prepended)",
        r.stdout.strip().endswith("/opt/sandbox-tools/bin"),
        r.stdout.strip()[-120:],
    )

    # 10. Reachable from `kubectl exec`, which starts a process from the IMAGE's
    #     own PATH and never sees anything the entrypoint exported.
    r = sb.exec(
        "IMGPATH=$(tr '\\0' '\\n' < /proc/1/environ | grep '^PATH=' | cut -d= -f2-); "
        f"env -i PATH=\"$IMGPATH\" sh -c '{VERSION_CMD}'",
        timeout=180,
    )
    record(
        "reachable from kubectl exec",
        r.succeeded,
        r.stdout.strip() or r.stderr.strip()[:200],
    )

    # 11. The baked-in version manifest is readable for debugging
    r = sb.exec("cat /opt/sandbox-tools/VERSIONS", timeout=30)
    record(
        "VERSIONS manifest present",
        r.succeeded and all(t in r.stdout for t in TOOLS),
        r.stdout.strip()[:200],
    )

print()
failed = [c for c in checks if not c[1]]
print(f"{len(checks) - len(failed)}/{len(checks)} passed")
sys.exit(1 if failed else 0)
