# Sandbox Client

Python client library for interacting with the Sandbox Orchestrator service.

## Installation

Install from source:

```bash
git clone repo
cd Orchard
pip install -e .
```

## Configuration

The client supports configuration via environment variables or constructor parameters.

### Environment Variables (recommended)

```bash
export SANDBOX_BASE_URL="http://YOUR_URL"
export SANDBOX_API_KEY="YOUR_KEYS"
export SANDBOX_PREFIX="myapp"           # Optional: prefix for sandbox IDs
```

With environment variables set, you can create a client without any parameters:

```python
client = SandboxClient()  # Uses env vars automatically
```

### Constructor Parameters

```python
client = SandboxClient(
    base_url="http://YOUR_URL",
    api_key="your-api-key-here",
    prefix="myapp"              # Optional: prefix for sandbox IDs
)
```

**Priority**: Constructor parameters > Environment variables > Defaults

## Usage

### Synchronous Client

```python
from orchard import SandboxClient

# Using context manager (recommended - auto cleanup)
with SandboxClient() as client:
    with client.create_sandbox("python:3.11-slim") as sandbox:
        # Execute commands
        result = sandbox.exec("echo 'Hello, World!'")
        print(result.stdout)
        
        # Execute with custom timeout and working directory
        result = sandbox.exec(
            "python script.py",
            timeout=60,
            cwd="/workspace",
            env={"DEBUG": "1"}
        )        
        
        # Upload files
        sandbox.upload_file("local_file.py", "/workspace/script.py")
        
        # Download files
        sandbox.download_file("/workspace/output.txt", "local_output.txt")
        
        # Apply git patches
        sandbox.apply_patch(patch_content)
```

### Asynchronous Client

```python
from orchard import AsyncSandboxClient

async def main():
    async with AsyncSandboxClient() as client:
        async with await client.create_sandbox("python:3.11-slim") as sandbox:
            # Execute commands
            result = await sandbox.exec("echo 'Hello, World!'")
            print(result.stdout)
            
            # Upload files
            await sandbox.upload_file("local_file.py", "/workspace/script.py")
            
            # Download files
            await sandbox.download_file("/workspace/output.txt", "local_output.txt")

asyncio.run(main())
```

## API Reference

### SandboxClient / AsyncSandboxClient

#### Constructor

```python
SandboxClient(
    base_url: Optional[str] = None,  # Falls back to SANDBOX_BASE_URL env var
    timeout: int = 1200,             # Request timeout in seconds
    auto_cleanup: bool = True,       # Auto cleanup sandboxes on exit
    api_key: Optional[str] = None,   # Falls back to SANDBOX_API_KEY env var
    prefix: Optional[str] = None     # Falls back to SANDBOX_PREFIX env var
)
```

#### Methods

| Method | Description |
|--------|-------------|
| `create_sandbox(image, ...)` | Create a new sandbox container |
| `get_sandbox(sandbox_id)` | Get an existing sandbox by ID |
| `delete_sandbox(sandbox_id)` | Delete a sandbox |
| `cleanup_all()` | Delete all sandboxes created by this client |

### SandboxInstance / AsyncSandboxInstance

#### Methods

| Method | Description |
|--------|-------------|
| `exec(command, timeout, cwd, env)` | Execute a command in the sandbox |
| `apply_patch(patch, timeout)` | Apply a git patch |
| `upload_file(local_path, remote_path)` | Upload a file to the sandbox |
| `upload_content(content, remote_path)` | Upload content directly |
| `download_file(remote_path, local_path)` | Download a file from the sandbox |
| `download_content(remote_path)` | Download file content as bytes |
| `list_files(remote_path)` | List files in a directory |
| `get_job(job_id)` | Get job status and results |
| `delete()` | Delete the sandbox |

### JobResult

Result object returned by `exec()`:

```python
result = sandbox.exec("ls -la")

result.job_id       # Unique job identifier
result.status       # "succeeded", "failed", "running", "queued"
result.stdout       # Standard output
result.stderr       # Standard error
result.exit_code    # Process exit code
result.succeeded    # True if exit_code == 0
result.failed       # True if status == "failed"
result.is_complete  # True if job finished
```

## Sandbox Creation Options

```python
sandbox = client.create_sandbox(
    image="python:3.11-slim",    # Container image
    block_network=False,         # Block outbound network (default: False)
    sandbox_id=None,             # Custom sandbox ID (auto-generated if None)
    cpu="4",                     # CPU cores (e.g., "4", "2000m")
    memory="16Gi",               # Memory limit (e.g., "16Gi", "8Gi")
    timeout=3600,                # Timeout for sandbox to become ready
    wait_ready=True,             # Wait for sandbox to be ready
    poll_interval=1.0            # Polling interval when waiting
)
```

## Auto Cleanup

The client automatically tracks all created sandboxes and cleans them up:

1. **Context manager exit**: Sandboxes are deleted when exiting `with` blocks
2. **Program exit**: Remaining sandboxes are deleted via `atexit` handler
3. **Signal handling**: Cleanup runs on `SIGINT` (Ctrl+C) and `SIGTERM`

To disable auto cleanup:

```python
client = SandboxClient(auto_cleanup=False)
```

## Error Handling

```python
from orchard import SandboxClient

try:
    with SandboxClient() as client:
        with client.create_sandbox("python:3.11-slim") as sandbox:
            result = sandbox.exec("exit 1")
            if not result.succeeded:
                print(f"Command failed: {result.stderr}")
except TimeoutError as e:
    print(f"Sandbox creation timed out: {e}")
except requests.exceptions.HTTPError as e:
    print(f"API error: {e}")
```

## Retry Logic

The client automatically retries on transient failures:

- Connection errors
- Timeouts
- Chunked encoding errors

Default: 3 retries with exponential backoff (1s, 2s, 4s).
