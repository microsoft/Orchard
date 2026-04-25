# REST API Reference

Reference for the Orchard orchestrator HTTP API. All endpoints require an `X-API-Key` header unless authentication is disabled.

---

## 使用指南

### API 端点

#### 1. 创建 Sandbox

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

响应：
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

#### 2. 执行命令（异步）

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

响应：
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}
```

#### 3. 查询 Job 状态

```bash
GET /jobs/{job_id}
X-API-Key: your-api-key
```

响应：
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

可能的状态：`queued`, `running`, `succeeded`, `failed`

#### 4. 应用 Git Patch

```bash
POST /sandboxes/{sandbox_id}/apply_patch
Content-Type: application/json
X-API-Key: your-api-key

{
  "patch": "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1,2 @@\n line1\n+line2",
  "timeout_seconds": 30
}
```

响应：
```json
{
  "success": true,
  "stdout": "",
  "stderr": "",
  "exit_code": 0
}
```

#### 5. 删除 Sandbox

```bash
DELETE /sandboxes/{sandbox_id}
X-API-Key: your-api-key
```

响应：
```json
{
  "status": "deleted",
  "sandbox_id": "abc12345"
}
```

#### 6. 获取 Sandbox 信息

```bash
GET /sandboxes/{sandbox_id}
X-API-Key: your-api-key
```

#### 7. 健康检查

```bash
GET /health
```

#### 8. 上传文件

```bash
POST /sandboxes/{sandbox_id}/files
Content-Type: application/json
X-API-Key: your-api-key

{
  "path": "/workspace/test.py",
  "content": "<base64 encoded content>"
}
```

响应：
```json
{
  "success": true,
  "path": "/workspace/test.py",
  "size": 1024
}
```

#### 9. 下载文件

```bash
GET /sandboxes/{sandbox_id}/files?path=/workspace/test.py
X-API-Key: your-api-key
```

响应：
```json
{
  "path": "/workspace/test.py",
  "content": "<base64 encoded content>",
  "size": 1024
}
```

#### 10. 列出文件

```bash
GET /sandboxes/{sandbox_id}/files/list?path=/workspace
X-API-Key: your-api-key
```

响应：
```json
{
  "path": "/workspace",
  "files": [
    {"name": "test.py", "type": "file", "size": "1024"},
    {"name": "src", "type": "directory", "size": "4096"}
  ]
}

### Python Client 使用

安装依赖：

```bash
pip install requests aiohttp
```

**基本使用（同步客户端）：**

```python
from orchard import SandboxClient
import os

# 方法 1: 通过环境变量设置 API Key
os.environ["SANDBOX_API_KEY"] = "your-api-key"
client = SandboxClient("http://localhost:8000")

# 方法 2: 直接传入 API Key
client = SandboxClient("http://localhost:8000", api_key="your-api-key")

# 检查健康状态
print(client.health())

# 创建 sandbox（自动清理）
with client.create_sandbox("python:3.11-slim") as sandbox:
    # 执行命令
    result = sandbox.exec("echo 'Hello World'")
    print(f"Exit code: {result.exit_code}")
    print(f"Output: {result.stdout}")
    
    # 执行 Python 代码
    result = sandbox.exec([
        "python", "-c",
        "print('Hello from Python')"
    ])
    print(result.stdout)
    
    # 创建文件
    sandbox.exec("echo 'content' > /workspace/test.txt")
    
    # 读取文件
    result = sandbox.exec("cat /workspace/test.txt")
    print(result.stdout)
    
# Sandbox 自动删除
```

**异步执行（不等待）：**

```python
# 提交任务但不等待
result = sandbox.exec("long_running_command", wait=False)
print(f"Job submitted: {result.job_id}")

# 稍后查询
import time
time.sleep(5)
result = client.get_job(result.job_id)
print(f"Status: {result.status}")
```

**应用 Patch：**

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

**手动管理（无 context manager）：**

```python
# 创建（可自定义 CPU/内存/超时）
sandbox = client.create_sandbox(
    "ubuntu:22.04",
    cpu="8",
    memory="32Gi",
    timeout=7200  # 2 hours
)

try:
    result = sandbox.exec("apt-get update")
    # ... 其他操作
finally:
    # 清理
    sandbox.delete()
```

**文件操作：**

```python
# 上传文件
sandbox.upload_file("local_file.py", "/workspace/remote_file.py")

# 上传内容
sandbox.upload_content(b"print('hello')", "/workspace/hello.py")

# 下载文件
sandbox.download_file("/workspace/output.txt", "local_output.txt")

# 下载内容
content = sandbox.download_content("/workspace/output.txt")

# 列出文件
files = sandbox.list_files("/workspace")
for f in files:
    print(f"{f['name']} ({f['type']})")
```

**异步客户端：**

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
