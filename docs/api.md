# REST API Reference

Reference for the Orchard orchestrator HTTP API. All endpoints require an `X-API-Key` header unless authentication is disabled.

---

## Endpoints

### 1. Create a sandbox

```bash
POST /sandboxes
Content-Type: application/json
X-API-Key: your-api-key

{
  "image": "python:3.11-slim",
  "block_network": true,
  "sandbox_id": "optional-custom-id",
  "cpu": "4",
  "memory": "16Gi",
  "timeout": 3600
}
```

Response:
```json
{
  "sandbox_id": "abc12345",
  "namespace": "sbx-abc12345",
  "image": "python:3.11-slim",
  "block_network": true,
  "cpu": "4",
  "memory": "16Gi",
  "timeout": 3600,
  "status": "pending"
}
```

### 2. Execute a command (asynchronous)

```bash
POST /sandboxes/{sandbox_id}/exec
Content-Type: application/json
X-API-Key: your-api-key

{
  "command": "echo Hello",
  "timeout_seconds": 300,
  "cwd": "/workspace",
  "env": {"KEY": "value"},
  "login_shell": false
}
```

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}
```

### 3. Query job status

```bash
GET /jobs/{job_id}
X-API-Key: your-api-key
```

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "sandbox_id": "abc12345",
  "command": "echo Hello",
  "status": "succeeded",
  "stdout": "Hello\n",
  "stderr": "",
  "exit_code": 0,
  "created_at": 1702900000.0,
  "started_at": 1702900001.0,
  "completed_at": 1702900002.0
}
```

Possible statuses: `queued`, `running`, `succeeded`, `failed`.

### 4. Apply a git patch

```bash
POST /sandboxes/{sandbox_id}/apply_patch
Content-Type: application/json
X-API-Key: your-api-key

{
  "patch": "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1,2 @@\n line1\n+line2",
  "timeout_seconds": 30
}
```

Response:
```json
{
  "success": true,
  "stdout": "",
  "stderr": "",
  "exit_code": 0
}
```

### 5. Delete a sandbox

```bash
DELETE /sandboxes/{sandbox_id}
X-API-Key: your-api-key
```

Response:
```json
{
  "status": "deleted",
  "sandbox_id": "abc12345"
}
```

### 6. Get sandbox info

```bash
GET /sandboxes/{sandbox_id}
X-API-Key: your-api-key
```

### 7. Health check

```bash
GET /health
```

### 8. Upload a file

```bash
POST /sandboxes/{sandbox_id}/files
Content-Type: application/json
X-API-Key: your-api-key

{
  "path": "/workspace/test.py",
  "content": "<base64 encoded content>"
}
```

Response:
```json
{
  "success": true,
  "path": "/workspace/test.py",
  "size": 1024
}
```

### 9. Download a file

```bash
GET /sandboxes/{sandbox_id}/files?path=/workspace/test.py
X-API-Key: your-api-key
```

Response:
```json
{
  "path": "/workspace/test.py",
  "content": "<base64 encoded content>",
  "size": 1024
}
```

### 10. List files

```bash
GET /sandboxes/{sandbox_id}/files/list?path=/workspace
X-API-Key: your-api-key
```

Response:
```json
{
  "path": "/workspace",
  "files": [
    {"name": "test.py", "type": "file", "size": "1024"},
    {"name": "src", "type": "directory", "size": "4096"}
  ]
}
```

---

## Python client usage

Install dependencies:

```bash
pip install orchard
```

**Basic usage (synchronous client):**

```python
from orchard import SandboxClient
import os

# Option 1: configure the API key via environment variable
os.environ["SANDBOX_API_KEY"] = "your-api-key"
client = SandboxClient("http://localhost:8000")

# Option 2: pass the API key directly
client = SandboxClient("http://localhost:8000", api_key="your-api-key")

# Health check
print(client.health())

# Create a sandbox (auto-cleanup via context manager)
with client.create_sandbox("python:3.11-slim") as sandbox:
    # Run a shell command
    result = sandbox.exec("echo 'Hello World'")
    print(f"Exit code: {result.exit_code}")
    print(f"Output: {result.stdout}")

    # Run Python code
    result = sandbox.exec([
        "python", "-c",
        "print('Hello from Python')"
    ])
    print(result.stdout)

    # Write a file
    sandbox.exec("echo 'content' > /workspace/test.txt")

    # Read a file
    result = sandbox.exec("cat /workspace/test.txt")
    print(result.stdout)

# Sandbox is deleted automatically on context exit
```

**Asynchronous execution (no wait):**

```python
# Submit a job without waiting for it to complete
result = sandbox.exec("long_running_command", wait=False)
print(f"Job submitted: {result.job_id}")

# Poll for completion later
import time
time.sleep(5)
result = client.get_job(result.job_id)
print(f"Status: {result.status}")
```

**Apply a patch:**

```python
patch = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,4 @@
 def hello():
-    print("Hello")
+    print("Hello World")
+    return True
"""

result = sandbox.apply_patch(patch)
if result["success"]:
    print("Patch applied successfully")
else:
    print(f"Error: {result.get('stderr')}")
```

**Manual lifecycle management (no context manager):**

```python
# Create with custom CPU / memory / timeout
sandbox = client.create_sandbox(
    "ubuntu:22.04",
    cpu="8",
    memory="32Gi",
    timeout=7200,  # 2 hours
)

try:
    result = sandbox.exec("apt-get update")
    # ... other operations
finally:
    # Always clean up
    sandbox.delete()
```

**File operations:**

```python
# Upload a local file
sandbox.upload_file("local_file.py", "/workspace/remote_file.py")

# Upload in-memory content
sandbox.upload_content(b"print('hello')", "/workspace/hello.py")

# Download to a local path
sandbox.download_file("/workspace/output.txt", "local_output.txt")

# Download into memory
content = sandbox.download_content("/workspace/output.txt")

# List files
files = sandbox.list_files("/workspace")
for f in files:
    print(f"{f['name']} ({f['type']})")
```

**Async client:**

```python
import asyncio
from orchard import AsyncSandboxClient

async def main():
    async with AsyncSandboxClient("http://localhost:8000", api_key="your-key") as client:
        async with await client.create_sandbox("python:3.11-slim") as sandbox:
            result = await sandbox.exec("echo 'Hello async!'")
            print(result.stdout)

asyncio.run(main())
```
