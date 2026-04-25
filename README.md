# Sandbox Client

Python client library for interacting with the Sandbox Orchestrator service.

## Installation

Install from source:

```bash
git clone repo
cd azure-modal
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


# Azure AKS Sandbox Orchestrator

一个运行在 Azure AKS 上的类 Modal 的 Sandbox Orchestrator，支持 SWE-bench Verified 的 agent ↔ sandbox 多轮命令式交互（异步执行模式）。

## 功能特性

✅ **FastAPI Orchestrator 服务**
- RESTful API 用于 sandbox 生命周期管理
- 异步命令执行（A 模式），支持轮询查询
- 每个 sandbox 串行执行，全局并发控制
- 支持 git patch 应用
- 文件上传/下载/列表操作
- 自定义 CPU/内存/超时配置
- 自动 TTL 清理和资源回收

✅ **安全特性**
- API Key 认证（X-API-Key header）
- 预生成 50 个 API Key 便于分发
- 支持禁用认证（内部部署）

✅ **多副本支持**
- Redis 存储共享 sandbox 状态
- 分布式锁保证执行一致性
- 支持回退到内存存储（单副本）

✅ **Azure AKS 集成**
- Calico NetworkPolicy 支持网络隔离
- 双 node pool 架构（system + sandbox）
- 精确的 RBAC 权限控制
- ACR 集成用于镜像管理
- Log Analytics 监控

✅ **生产就绪**
- 结构化 JSON 日志
- 健康检查和就绪探针
- HPA 自动扩缩容
- Pod Disruption Budget
- 完整的错误处理

## 项目结构

```
.
├── src/orchard/          # Python SDK (installable: `pip install orchard`)
│   ├── __init__.py
│   └── client.py         # Modal 风格的同步/异步客户端
├── server/               # FastAPI orchestrator 服务（运行在 AKS 中）
│   ├── main.py
│   ├── api.py
│   ├── settings.py
│   ├── k8s_client.py
│   ├── sandbox_manager.py
│   ├── exec_manager.py
│   ├── job_store.py
│   ├── redis_store.py
│   └── utils.py
├── agent/                # 注入到每个 sandbox pod 的轻量 agent
│   ├── server.py
│   └── requirements.txt
├── docker/               # 所有 Dockerfile
│   ├── orchestrator.Dockerfile
│   ├── sandbox.Dockerfile
│   └── agent-injector.Dockerfile
├── deploy/
│   ├── azure/            # Azure 基础设施脚本（AKS, ACR, Log Analytics）
│   │   └── deploy_aks.sh
│   ├── k8s/              # Kubernetes 清单
│   │   ├── namespace.yaml
│   │   ├── serviceaccount.yaml
│   │   ├── rbac.yaml
│   │   ├── configmap.yaml
│   │   ├── secret.example.yaml  # API Keys 模板（请复制为 secret.yaml）
│   │   ├── redis.yaml
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── optional.yaml        # HPA, PDB, Ingress
│   └── scripts/          # 部署和测试脚本
│       ├── deploy_aks.sh
│       ├── build_push.sh
│       ├── deploy_k8s.sh
│       └── smoke_test.sh
├── examples/
├── docs/
├── tests/
├── pyproject.toml
├── LICENSE
└── README.md
```

## 快速开始

### 前置要求

- Azure 账号和有效订阅
- Azure CLI (`az`) 已安装并登录
- Docker 已安装
- kubectl 已安装
- Python 3.11+ （用于客户端）

### 步骤 1: 部署 Azure 资源

创建 AKS 集群、ACR、Log Analytics 等资源：

```bash
# 克隆或进入项目目录
cd azure-modal

# 使脚本可执行
chmod +x deploy/scripts/*.sh deploy/azure/*.sh

# 部署 AKS（约 10-15 分钟）
./deploy/scripts/deploy_aks.sh
```

**可选配置（通过环境变量）：**

```bash
export RESOURCE_GROUP="my-sandbox-rg"
export LOCATION="westus2"
export CLUSTER_NAME="my-aks"
export ACR_NAME="mysandboxacr$(date +%s)"
./deploy/scripts/deploy_aks.sh
```

脚本会创建：
- **资源组**: 包含所有资源
- **AKS 集群**: 启用 Calico NetworkPolicy
  - **sys node pool**: 3-6 nodes (Standard_D4s_v5), 用于系统组件和 orchestrator
  - **sbx node pool**: 0-50 nodes (Standard_D8s_v5), 标签 `workload=sandbox`, 用于 sandbox pods
- **ACR**: 用于存储 orchestrator 镜像
- **Log Analytics**: 用于监控和日志

完成后会输出配置信息并保存到 `.azure-config` 文件。

### 步骤 2: 获取 AKS 凭证

```bash
# 如果脚本已执行，凭证已自动配置
# 否则手动获取：
source .azure-config
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --overwrite-existing

# 验证连接
kubectl get nodes
```

你应该看到两个 node pool：
- `sys-*` 节点（system pool）
- `sbx-*` 节点（sandbox pool，可能为 0 因为 autoscaler）

### 步骤 3: 构建并推送 Orchestrator 镜像

```bash
# 构建镜像并推送到 ACR
./deploy/scripts/build_push.sh

# 如果 .azure-config 不存在，手动指定 ACR：
export ACR_NAME=your-acr-name
./deploy/scripts/build_push.sh
```

这会：
1. 登录到 ACR
2. 构建 Docker 镜像
3. 推送到 ACR
4. 验证镜像上传成功

### 步骤 4: 部署 Orchestrator 到 Kubernetes

```bash
# 部署所有 K8s 资源
./deploy/scripts/deploy_k8s.sh
```

这会创建：
- `orchestrator` namespace
- ServiceAccount 和 RBAC 规则（最小权限）
- ConfigMap（环境变量配置）
- Deployment（2 replicas）
- Service（ClusterIP）
- HPA 和 PDB（可选）

等待 pods 就绪：

```bash
kubectl get pods -n orchestrator -w
```

查看日志：

```bash
kubectl logs -n orchestrator -l app=sandbox-orchestrator -f
```

### 步骤 5: 访问服务

**方法 1: Port Forward（推荐用于测试）**

```bash
kubectl port-forward -n orchestrator svc/sandbox-orchestrator 8000:80
```

然后访问 `http://localhost:8000`

**方法 2: Ingress（生产环境）**

编辑 `deploy/k8s/optional.yaml` 中的 Ingress 配置，设置你的域名和 TLS 证书，然后：

```bash
kubectl apply -f deploy/k8s/optional.yaml
```

### 步骤 6: 运行冒烟测试

```bash
# 确保 port-forward 在运行
./deploy/scripts/smoke_test.sh
```

测试会：
1. 创建一个 sandbox
2. 执行 echo 命令
3. 查询 job 状态
4. 应用 git patch（可选）
5. 删除 sandbox

如果一切正常，你会看到：

```
============================================
Smoke Test Complete!
============================================
All basic operations completed successfully
```

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

## 配置

### 环境变量（在 deploy/k8s/configmap.yaml 和 deploy/k8s/secret.yaml 中配置）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SERVICE_NAME` | `sandbox-orchestrator` | 服务名称 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8000` | 监听端口 |
| `IN_CLUSTER` | `true` | 是否在集群内运行 |
| `NAMESPACE_PREFIX` | `sbx-` | Sandbox namespace 前缀 |
| `DEFAULT_CPU` | `4` | 默认 Pod CPU 资源 |
| `DEFAULT_MEMORY` | `16Gi` | 默认 Pod 内存资源 |
| `DEFAULT_WORKING_DIR` | `/workspace` | 工作目录 |
| `DEFAULT_BLOCK_NETWORK` | `true` | 默认阻止网络出站 |
| `MAX_CONCURRENT_EXECS` | `20` | 全局最大并发执行数 |
| `DEFAULT_TIMEOUT_SECONDS` | `300` | 默认命令超时 |
| `SANDBOX_TTL_HOURS` | `2` | Sandbox 自动清理时间 |
| `ORPHAN_JOB_TTL_HOURS` | `1` | 孤立 Job 清理时间 |
| `CLEANUP_INTERVAL_SECONDS` | `300` | 清理任务间隔 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `LOG_FORMAT` | `json` | 日志格式 |
| `USE_REDIS` | `true` | 是否使用 Redis（多副本需要） |
| `REDIS_URL` | `redis://redis-service...` | Redis 连接 URL |
| `REQUIRE_API_KEY` | `true` | 是否要求 API Key 认证 |
| `API_KEYS` | (secret) | 逗号分隔的有效 API Keys |

修改配置后重新部署：

```bash
kubectl apply -f deploy/k8s/configmap.yaml
kubectl rollout restart deployment/sandbox-orchestrator -n orchestrator
```

### Sandbox 资源限制

每个 sandbox pod 的默认资源配置：

- **CPU**: 4 cores (requests = limits)
- **Memory**: 16Gi (requests = limits)
- **Node Selector**: `workload: sandbox`
- **工作目录**: `/workspace`
- **超时**: 3600秒 (1小时)

**设置全局默认值**：
1. 编辑 `deploy/k8s/configmap.yaml` 中的 `DEFAULT_CPU` 和 `DEFAULT_MEMORY`
2. 重新部署 ConfigMap
3. 重启 orchestrator deployment

**按 sandbox 自定义**：
```python
# 在创建时指定资源
sandbox = client.create_sandbox(
    "python:3.11-slim",
    cpu="8",         # 8 cores
    memory="32Gi",   # 32 GB RAM
    timeout=7200     # 2 hour timeout
)
```

## 架构设计

### 组件架构

```
┌─────────────────────────────────────────────────────────┐
│                     Azure AKS Cluster                   │
│                                                         │
│  ┌──────────────────────┐  ┌──────────────────────────┐│
│  │   System Node Pool   │  │   Sandbox Node Pool      ││
│  │   (sys)              │  │   (sbx)                  ││
│  │                      │  │   workload=sandbox       ││
│  │  ┌───────────────┐   │  │                          ││
│  │  │ Orchestrator  │   │  │   ┌──────────────────┐  ││
│  │  │  Deployment   │   │  │   │  Sandbox Pod 1   │  ││
│  │  │  (2 replicas) │───┼──┼──▶│  (namespace 1)   │  ││
│  │  └───────────────┘   │  │   └──────────────────┘  ││
│  │         │             │  │   ┌──────────────────┐  ││
│  │         │             │  │   │  Sandbox Pod 2   │  ││
│  │         └─────────────┼──┼──▶│  (namespace 2)   │  ││
│  │                      │  │   └──────────────────┘  ││
│  └──────────────────────┘  └──────────────────────────┘│
│                                                         │
│  Calico Network Policy: Egress Control                 │
└─────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
  ┌─────────────┐              ┌──────────────┐
  │     ACR     │              │ Log Analytics│
  │   (Images)  │              │  (Monitoring)│
  └─────────────┘              └──────────────┘
```

### 执行流程

1. **创建 Sandbox**:
   - 创建独立 namespace (`sbx-{id}`)
   - 应用 NetworkPolicy（可选阻止 egress）
   - 创建 Pod（调度到 `workload=sandbox` 节点）
   - 等待 Pod Ready
   - 返回 sandbox ID

2. **执行命令（异步）**:
   - 提交 exec 请求，立即返回 job_id
   - Job 进入队列（状态：`queued`）
   - 获取 sandbox 锁（保证串行）
   - 获取全局执行信号量（限制并发）
   - 执行命令（状态：`running`）
   - 捕获 stdout/stderr/exit_code
   - 更新 job 状态（`succeeded` 或 `failed`）
   - 释放锁和信号量

3. **查询 Job**:
   - 通过 job_id 查询状态
   - 返回完整的执行结果

4. **清理**:
   - 手动删除：`DELETE /sandboxes/{id}`
   - 自动 TTL：超过 N 小时自动删除
   - 删除 namespace（级联删除所有资源）

### 关键实现细节

#### 1. Kubernetes Exec 实现

使用 `kubernetes.stream.stream` + `_preload_content=False` 实现可靠的命令执行：

```python
# 包装命令以捕获退出码
wrapped_command = [
    "bash", "-c",
    f"{command}; echo __EXIT_CODE__: $? >&2"
]

# 执行并读取输出
resp = stream(
    core_v1.connect_get_namespaced_pod_exec,
    pod_name, namespace,
    command=wrapped_command,
    stderr=True, stdout=True,
    _preload_content=False,
    _request_timeout=timeout,
)

# 分别读取 stdout 和 stderr
while resp.is_open():
    if resp.peek_stdout():
        stdout_data.append(resp.read_stdout())
    if resp.peek_stderr():
        stderr_data.append(resp.read_stderr())

# 从 stderr 解析退出码
exit_code = parse_exit_code_from_stderr(stderr)
```

#### 2. 并发控制

- **Per-sandbox 串行**: 每个 sandbox 一个 `asyncio.Lock`（单副本）或 Redis 分布式锁（多副本）
- **全局并发限制**: `asyncio.Semaphore(max_concurrent_execs)`

```python
async with sandbox_lock:
    async with global_semaphore:
        await execute_command()
```

#### 3. Redis 状态共享

多副本 Orchestrator 通过 Redis 共享 sandbox 状态：

```python
# Key schema
sandbox:{sandbox_id}     # JSON sandbox metadata
sandbox:lock:{sandbox_id} # 分布式执行锁
sandbox:all              # Set of all sandbox IDs
```

#### 4. 网络隔离

使用 Calico NetworkPolicy 默认拒绝 egress：

```yaml
spec:
  podSelector: {}
  policyTypes: ["Egress"]
  egress: []  # 空列表 = 拒绝所有出站
```

#### 5. 资源保证

Pod 设置 requests = limits 保证资源：

```yaml
resources:
  requests:
    cpu: "4"
    memory: "16Gi"
  limits:
    cpu: "4"
    memory: "16Gi"
```

#### 6. RBAC 最小权限

只授予必要的权限：

```yaml
rules:
  - apiGroups: [""]
    resources: ["namespaces", "pods"]
    verbs: ["create", "delete", "get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/exec"]
    verbs: ["create"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies"]
    verbs: ["create", "delete", "get", "list"]
```

## 运维指南

### 监控

**查看 orchestrator 日志：**

```bash
kubectl logs -n orchestrator -l app=sandbox-orchestrator -f --tail=100
```

**查看所有 sandbox namespaces：**

```bash
kubectl get namespaces | grep sbx-
```

**查看某个 sandbox 的 pod：**

```bash
kubectl get pods -n sbx-{sandbox_id}
```

**查看 NetworkPolicy：**

```bash
kubectl get networkpolicies -n sbx-{sandbox_id}
```

**查看 orchestrator metrics：**

```bash
kubectl top pods -n orchestrator
```

**查看 node pool 状态：**

```bash
kubectl get nodes -l workload=sandbox
```

### 扩缩容

**手动扩缩 orchestrator：**

```bash
kubectl scale deployment/sandbox-orchestrator -n orchestrator --replicas=5
```

**HPA 会自动扩缩容**（如果已部署 optional.yaml）：

```bash
kubectl get hpa -n orchestrator
```

**调整 sandbox node pool：**

```bash
az aks nodepool scale \
  --resource-group $RESOURCE_GROUP \
  --cluster-name $CLUSTER_NAME \
  --name sbx \
  --node-count 10
```

### 故障排查

**Pod 无法启动：**

```bash
kubectl describe pod -n orchestrator -l app=sandbox-orchestrator
```

**Sandbox pod 无法调度：**

检查 node pool 是否有节点：
```bash
kubectl get nodes -l workload=sandbox
```

如果没有节点，autoscaler 会自动创建（需要几分钟）。

**命令执行失败：**

检查 sandbox pod 日志：
```bash
kubectl logs -n sbx-{sandbox_id} sandbox-{sandbox_id}
```

**网络问题：**

检查 NetworkPolicy：
```bash
kubectl describe networkpolicy -n sbx-{sandbox_id} deny-egress
```

### 清理

**删除特定 sandbox：**

```bash
kubectl delete namespace sbx-{sandbox_id}
```

**删除所有 sandboxes：**

```bash
kubectl delete namespaces -l managed-by=orchestrator
```

**删除 orchestrator：**

```bash
kubectl delete namespace orchestrator
```

**删除整个 Azure 资源组：**

```bash
az group delete --name $RESOURCE_GROUP --yes --no-wait
```

## 成本估算

基于 Azure East US 区域定价（2024）：

### 按小时计费

| 资源 | 规格 | 单价/小时 | 数量 | 小时成本 |
|------|------|-----------|------|----------|
| System nodes | Standard_D4s_v5 | ~$0.23 | 3 | ~$0.69 |
| Sandbox nodes (active) | Standard_D8s_v5 | ~$0.46 | 变动 | $0.46 × N |
| AKS 控制平面 | 免费层 | $0 | 1 | $0 |
| ACR | Standard | ~$0.007 | 1 | ~$0.007 |
| Log Analytics | 按数据量 | ~$2.3/GB | - | 变动 |

### 月度估算（示例）

**最小配置**（无 sandbox 负载）：
- 3 × System nodes: ~$500/月
- ACR: ~$5/月
- Log Analytics: ~$50/月（估计 20GB/月）
- **总计**: ~$555/月

**典型负载**（平均 10 个活动 sandboxes）：
- System nodes: ~$500/月
- 10 × Sandbox nodes: ~$3,350/月
- ACR + Logs: ~$55/月
- **总计**: ~$3,905/月

**优化建议**：
- 使用 Azure Reserved Instances（可节省 30-50%）
- 调整 node pool autoscaler 参数
- 设置合理的 sandbox TTL
- 使用 spot instances 作为 sandbox nodes（可节省 70-90%）

## 性能调优

### Orchestrator 性能

- **并发执行数**: 调整 `MAX_CONCURRENT_EXECS`（默认 20）
- **Replicas**: 增加 orchestrator replicas 处理更多 API 请求
- **资源**: 增加 orchestrator pod 的 CPU/内存限制

### Sandbox 性能

- **Node 规格**: 根据工作负载选择合适的 VM 大小
- **Autoscaler**: 调整 min/max 节点数
- **资源限制**: 调整 sandbox pod 的 CPU/内存

### 网络性能

- **NetworkPolicy**: 如不需要严格隔离，可设置 `block_network: false`
- **Pod 网络**: 考虑使用 Azure CNI Overlay 模式

## 安全考虑

### 已实施

✅ **API Key 认证** - 所有 API 端点需要 X-API-Key 认证  
✅ 50 个预生成 API Key 便于分发  
✅ 支持禁用认证（内部部署场景）  
✅ 最小权限 RBAC（不使用 cluster-admin）  
✅ NetworkPolicy 默认拒绝 egress  
✅ Pod 资源限制（防止资源耗尽）  
✅ Namespace 隔离（每个 sandbox 独立）  
✅ ServiceAccount 专用于 orchestrator  
✅ 非 root 用户运行 orchestrator 容器  

### 建议增强

⚠️ 使用 Pod Security Standards（restricted）  
⚠️ 启用 Azure Policy for Kubernetes  
⚠️ 配置 Private AKS cluster  
⚠️ 使用 Azure Key Vault 管理敏感配置  
⚠️ 启用 audit logging  
⚠️ 实施镜像扫描（ACR Defender）  
⚠️ 使用 Workload Identity（而非 Service Principal）  

## 常见问题

### Q: Sandbox 创建很慢？

A: 首次创建时需要等待：
1. Autoscaler 创建新节点（3-5 分钟）
2. 拉取镜像（取决于镜像大小）

优化：预热节点，使用较小的基础镜像。

### Q: 命令执行超时？

A: 检查：
1. `timeout_seconds` 是否足够
2. Sandbox pod 是否正常运行
3. 命令是否陷入死循环

### Q: 网络无法访问外部？

A: 这是预期行为（`block_network: true`）。如需外网访问：
1. 创建时设置 `block_network: false`
2. 或修改 NetworkPolicy 允许特定目标

### Q: 如何持久化 sandbox 状态？

A: 当前 sandbox 是临时的。如需持久化：
1. 在删除前执行命令导出数据
2. 使用外部存储（PVC、Azure Blob）
3. 构建包含数据的新镜像

### Q: 可以运行 GPU 工作负载吗？

A: 可以，但需要：
1. 创建 GPU node pool
2. 修改 sandbox pod spec 请求 GPU
3. 使用 GPU 镜像

### Q: 如何调试 exec 失败？

A: 
1. 查看 job 的 stderr
2. 检查 sandbox pod 日志
3. 手动 kubectl exec 到 pod 测试命令

## 开发指南

### 本地开发

```bash
# 安装依赖
pip install -e ".[dev]"

# 运行 orchestrator（需要 kubeconfig）
export IN_CLUSTER=false
python -m server.main

# 运行测试（TODO）
pytest tests/

# 代码格式化
black server/ src/
ruff check server/ src/
```

### 添加新功能

1. 修改 `server/api.py` 添加新端点
2. 在对应的 manager 中实现逻辑
3. 更新 `src/orchard/client.py`
4. 更新文档
5. 添加测试

### Redis 配置

Redis 用于多副本之间共享 sandbox 状态，默认已启用。

**部署 Redis：**
```bash
kubectl apply -f deploy/k8s/redis.yaml
```

**禁用 Redis（仅单副本）：**
```yaml
# 在 configmap.yaml 中设置
USE_REDIS: "false"
```

**使用 Azure Cache for Redis：**
```yaml
# 在 configmap.yaml 中设置
REDIS_URL: "redis://:password@your-redis.redis.cache.windows.net:6380/0?ssl=true"
```

## 贡献

欢迎贡献！请：

1. Fork 项目
2. 创建 feature 分支
3. 提交代码并确保格式正确
4. 创建 Pull Request

## 许可证

MIT License

## 支持

遇到问题？

1. 查看本 README 的故障排查部分
2. 检查 GitHub Issues
3. 查看 orchestrator 和 sandbox pod 日志

## 更新日志

### v0.2.0 (2024-12-26)

- ✨ **API Key 认证** - 支持 X-API-Key header 认证
- ✨ **Redis 存储** - 多副本状态共享
- ✨ **文件操作** - 上传/下载/列出文件
- ✨ **资源自定义** - 每个 sandbox 可配置 CPU/内存/超时
- ✨ **异步客户端** - 新增 AsyncSandboxClient
- ✨ **Login Shell** - 支持 bash -lc 登录 shell 模式
- ✅ 50 个预生成 API Keys
- ✅ 分布式锁保证执行一致性

### v0.1.0 (2024-12-18)

- ✨ 初始版本
- ✅ 完整的 sandbox 生命周期管理
- ✅ 异步命令执行
- ✅ Azure AKS 集成
- ✅ NetworkPolicy 支持
- ✅ Python 客户端库
- ✅ 完整部署脚本

---

**Happy Sandboxing! 🎉**
