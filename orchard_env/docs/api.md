# HTTP API reference

All endpoints except `GET /` and `GET /health` require an API key in the
`X-API-Key` header (unless the orchestrator runs with `REQUIRE_API_KEY=false`).

Base URL examples below assume a local port-forward:

```bash
kubectl port-forward -n orchestrator svc/sandbox-orchestrator 8000:80
export BASE_URL=http://localhost:8000
```

## Status codes

| Code | Meaning |
| --- | --- |
| `401` / `403` | Missing or invalid API key |
| `404` | Sandbox or job not found |
| `408` | Server-side wait timed out |
| `503` | Transient overload — safe to retry |

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/` | Service banner |
| `GET` | `/health` | Health check (no auth) |
| `GET` | `/resources` | Cluster resource summary |
| `POST` | `/sandboxes` | Create a sandbox |
| `GET` | `/sandboxes/{sandbox_id}` | Get sandbox info |
| `GET` | `/sandboxes/{sandbox_id}/wait` | Block until the sandbox is ready |
| `DELETE` | `/sandboxes/{sandbox_id}` | Delete a sandbox |
| `POST` | `/sandboxes/{sandbox_id}/heartbeat` | Refresh the sandbox liveness timer |
| `POST` | `/sandboxes/{sandbox_id}/exec` | Run a command |
| `WS` | `/sandboxes/{sandbox_id}/exec/pty` | Interactive PTY session |
| `POST` | `/sandboxes/{sandbox_id}/apply_patch` | Apply a git patch |
| `POST` | `/sandboxes/{sandbox_id}/files` | Upload a file |
| `GET` | `/sandboxes/{sandbox_id}/files` | Download a file |
| `GET` | `/sandboxes/{sandbox_id}/files/list` | List a directory |
| `GET` | `/jobs/{job_id}` | Get job status and results |
| `GET` | `/jobs/{job_id}/wait` | Block until the job completes |

### Create a sandbox

```http
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

```json
{
  "sandbox_id": "abc12345",
  "namespace": "sandbox-pods",
  "image": "python:3.11-slim",
  "block_network": true,
  "cpu": "4",
  "memory": "16Gi",
  "timeout": 3600,
  "status": "pending"
}
```

Wait for readiness without polling:

```http
GET /sandboxes/abc12345/wait?timeout=3600
```

### Execute a command

```http
POST /sandboxes/{sandbox_id}/exec
Content-Type: application/json
X-API-Key: your-api-key

{
  "command": "echo Hello",
  "timeout_seconds": 300,
  "cwd": "/workspace",
  "env": {"KEY": "value"},
  "login_shell": false,
  "wait": false
}
```

With `wait: false` (default) the call returns immediately:

```json
{ "job_id": "550e8400-e29b-41d4-a716-446655440000", "status": "queued" }
```

With `wait: true` the orchestrator blocks until the job finishes and returns the
full job object. **If the response status is `"running"`, the server-side wait
timed out and the job is still executing** — keep polling `GET /jobs/{job_id}`
or call `GET /jobs/{job_id}/wait`.

### Query a job

```http
GET /jobs/{job_id}
X-API-Key: your-api-key
```

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
  "completed_at": 1702900002.0,
  "error": null
}
```

Statuses: `queued` → `running` → `succeeded` | `failed`.

Server-side wait (no client polling):

```http
GET /jobs/{job_id}/wait?timeout=300
```

### Apply a git patch

```http
POST /sandboxes/{sandbox_id}/apply_patch
Content-Type: application/json
X-API-Key: your-api-key

{
  "patch": "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1,2 @@\n line1\n+line2",
  "timeout_seconds": 30
}
```

```json
{ "success": true, "stdout": "", "stderr": "", "exit_code": 0 }
```

### File operations

Upload (content is base64-encoded):

```http
POST /sandboxes/{sandbox_id}/files
Content-Type: application/json
X-API-Key: your-api-key

{ "path": "/workspace/test.py", "content": "<base64>" }
```

```json
{ "success": true, "path": "/workspace/test.py", "size": 1024 }
```

Download:

```http
GET /sandboxes/{sandbox_id}/files?path=/workspace/test.py
X-API-Key: your-api-key
```

```json
{ "path": "/workspace/test.py", "content": "<base64>", "size": 1024 }
```

List:

```http
GET /sandboxes/{sandbox_id}/files/list?path=/workspace
X-API-Key: your-api-key
```

```json
{
  "path": "/workspace",
  "files": [
    { "name": "test.py", "type": "file", "size": 1024, "modified": "2026-07-29T10:00:00" },
    { "name": "src", "type": "directory", "size": 4096, "modified": "2026-07-29T10:00:00" }
  ]
}
```

`size` is an integer (bytes). `type` is `"file"` or `"directory"`.

### Delete a sandbox

```http
DELETE /sandboxes/{sandbox_id}
X-API-Key: your-api-key
```

```json
{ "status": "deleted", "sandbox_id": "abc12345" }
```
