# Deployment Guide

End-to-end guide for deploying Orchard on Azure AKS.

---

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

---

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


---

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
