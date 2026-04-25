# Deployment Guide

End-to-end guide for deploying Orchard on Azure AKS.

---

## Quick start

### Prerequisites

- An Azure account with an active subscription
- Azure CLI (`az`) installed and signed in
- Docker installed
- kubectl installed
- Python 3.11+ (for the client)

### Step 1: Provision Azure resources

Create the AKS cluster, ACR, Log Analytics workspace, and supporting resources:

```bash
# Clone or change into the project directory
cd Orchard

# Make the scripts executable
chmod +x deploy/scripts/*.sh deploy/azure/*.sh

# Deploy AKS (takes ~10–15 minutes)
./deploy/scripts/deploy_aks.sh
```

**Optional configuration (via environment variables):**

```bash
export RESOURCE_GROUP="my-sandbox-rg"
export LOCATION="westus2"
export CLUSTER_NAME="my-aks"
export ACR_NAME="mysandboxacr$(date +%s)"
./deploy/scripts/deploy_aks.sh
```

The script provisions:

- **Resource group** containing all resources
- **AKS cluster** with Calico NetworkPolicy enabled
  - **`sys` node pool**: 3–6 nodes (Standard_D4s_v5) for system components and the orchestrator
  - **`sbx` node pool**: 0–50 nodes (Standard_D8s_v5), labeled `workload=sandbox`, for sandbox pods
- **ACR** for the orchestrator image
- **Log Analytics** workspace for monitoring and logs

When it finishes, configuration is printed and saved to `.azure-config`.

### Step 2: Get AKS credentials

```bash
# If the script ran successfully, credentials are already configured.
# Otherwise, fetch them manually:
source .azure-config
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --overwrite-existing

# Verify connectivity
kubectl get nodes
```

You should see two node pools:

- `sys-*` nodes (system pool)
- `sbx-*` nodes (sandbox pool — possibly 0 if the autoscaler has scaled down)

### Step 3: Build and push the orchestrator image

```bash
# Build the image and push it to ACR
./deploy/scripts/build_push.sh

# If .azure-config does not exist, specify the ACR explicitly:
export ACR_NAME=your-acr-name
./deploy/scripts/build_push.sh
```

This script:

1. Logs in to ACR
2. Builds the Docker image
3. Pushes it to ACR
4. Verifies the upload succeeded

### Step 4: Deploy the orchestrator to Kubernetes

```bash
# Apply all K8s resources
./deploy/scripts/deploy_k8s.sh
```

This creates:

- The `orchestrator` namespace
- ServiceAccount and RBAC rules (least privilege)
- ConfigMap (environment configuration)
- Deployment (2 replicas)
- Service (ClusterIP)
- HPA and PDB (optional)

Wait for the pods to become ready:

```bash
kubectl get pods -n orchestrator -w
```

Tail the logs:

```bash
kubectl logs -n orchestrator -l app=sandbox-orchestrator -f
```

### Step 5: Access the service

**Option 1: Port forward (recommended for testing)**

```bash
kubectl port-forward -n orchestrator svc/sandbox-orchestrator 8000:80
```

Then point your client at `http://localhost:8000`.

**Option 2: Ingress (production)**

Edit the Ingress configuration in `deploy/k8s/optional.yaml` to set your domain and TLS certificate, then:

```bash
kubectl apply -f deploy/k8s/optional.yaml
```

### Step 6: Run the smoke test

```bash
# Make sure port-forward is still running
./deploy/scripts/smoke_test.sh
```

The smoke test will:

1. Create a sandbox
2. Run an `echo` command
3. Query the job status
4. Apply a git patch (optional)
5. Delete the sandbox

On success you should see:

```
============================================
Smoke Test Complete!
============================================
All basic operations completed successfully
```

---

## Configuration

### Environment variables (set in `deploy/k8s/configmap.yaml` and `deploy/k8s/secret.yaml`)

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICE_NAME` | `sandbox-orchestrator` | Service name |
| `HOST` | `0.0.0.0` | Listen address |
| `PORT` | `8000` | Listen port |
| `IN_CLUSTER` | `true` | Whether the orchestrator is running inside the cluster |
| `NAMESPACE_PREFIX` | `sbx-` | Prefix for sandbox namespaces |
| `DEFAULT_CPU` | `4` | Default sandbox pod CPU |
| `DEFAULT_MEMORY` | `16Gi` | Default sandbox pod memory |
| `DEFAULT_WORKING_DIR` | `/workspace` | Default working directory |
| `DEFAULT_BLOCK_NETWORK` | `true` | Block egress traffic by default |
| `MAX_CONCURRENT_EXECS` | `20` | Global maximum concurrent executions |
| `DEFAULT_TIMEOUT_SECONDS` | `300` | Default command timeout |
| `SANDBOX_TTL_HOURS` | `2` | Auto-cleanup TTL for sandboxes |
| `ORPHAN_JOB_TTL_HOURS` | `1` | Cleanup TTL for orphaned jobs |
| `CLEANUP_INTERVAL_SECONDS` | `300` | Cleanup task interval |
| `LOG_LEVEL` | `INFO` | Log level |
| `LOG_FORMAT` | `json` | Log format |
| `USE_REDIS` | `true` | Use Redis (required for multi-replica) |
| `REDIS_URL` | `redis://redis-service...` | Redis connection URL |
| `REQUIRE_API_KEY` | `true` | Require `X-API-Key` authentication |
| `API_KEYS` | (secret) | Comma-separated list of valid API keys |

Reapply after editing:

```bash
kubectl apply -f deploy/k8s/configmap.yaml
kubectl rollout restart deployment/sandbox-orchestrator -n orchestrator
```

### Sandbox resource limits

Default per-sandbox-pod configuration:

- **CPU**: 4 cores (requests = limits)
- **Memory**: 16Gi (requests = limits)
- **Node selector**: `workload: sandbox`
- **Working directory**: `/workspace`
- **Timeout**: 3600 seconds (1 hour)

**Change the global defaults:**

1. Edit `DEFAULT_CPU` and `DEFAULT_MEMORY` in `deploy/k8s/configmap.yaml`
2. Reapply the ConfigMap
3. Restart the orchestrator Deployment

**Override per sandbox:**

```python
# Specify resources at creation time
sandbox = client.create_sandbox(
    "python:3.11-slim",
    cpu="8",         # 8 cores
    memory="32Gi",   # 32 GB RAM
    timeout=7200,    # 2 hour timeout
)
```

---

## Operations

### Monitoring

**Tail orchestrator logs:**

```bash
kubectl logs -n orchestrator -l app=sandbox-orchestrator -f --tail=100
```

**List all sandbox namespaces:**

```bash
kubectl get namespaces | grep sbx-
```

**Inspect a specific sandbox pod:**

```bash
kubectl get pods -n sbx-{sandbox_id}
```

**Inspect NetworkPolicies:**

```bash
kubectl get networkpolicies -n sbx-{sandbox_id}
```

**Orchestrator metrics:**

```bash
kubectl top pods -n orchestrator
```

**Sandbox node pool status:**

```bash
kubectl get nodes -l workload=sandbox
```

### Scaling

**Manually scale the orchestrator:**

```bash
kubectl scale deployment/sandbox-orchestrator -n orchestrator --replicas=5
```

**HPA-driven autoscaling** (when `optional.yaml` is applied):

```bash
kubectl get hpa -n orchestrator
```

**Resize the sandbox node pool:**

```bash
az aks nodepool scale \
  --resource-group $RESOURCE_GROUP \
  --cluster-name $CLUSTER_NAME \
  --name sbx \
  --node-count 10
```

### Troubleshooting

**Pod fails to start:**

```bash
kubectl describe pod -n orchestrator -l app=sandbox-orchestrator
```

**Sandbox pod cannot be scheduled:**

Check whether the sandbox node pool has nodes:

```bash
kubectl get nodes -l workload=sandbox
```

If there are none, the autoscaler will provision more (this can take a few minutes).

**Command execution fails:**

Check the sandbox pod logs:

```bash
kubectl logs -n sbx-{sandbox_id} sandbox-{sandbox_id}
```

**Network issues:**

Inspect the egress NetworkPolicy:

```bash
kubectl describe networkpolicy -n sbx-{sandbox_id} deny-egress
```

### Cleanup

**Delete a specific sandbox:**

```bash
kubectl delete namespace sbx-{sandbox_id}
```

**Delete all sandboxes:**

```bash
kubectl delete namespaces -l managed-by=orchestrator
```

**Delete the orchestrator:**

```bash
kubectl delete namespace orchestrator
```

**Delete the entire Azure resource group:**

```bash
az group delete --name $RESOURCE_GROUP --yes --no-wait
```

---

## Cost estimate

Based on Azure East US pricing (2024):

### Hourly

| Resource | Spec | $/hour | Count | Hourly cost |
|----------|------|--------|-------|-------------|
| System nodes | Standard_D4s_v5 | ~$0.23 | 3 | ~$0.69 |
| Sandbox nodes (active) | Standard_D8s_v5 | ~$0.46 | variable | $0.46 × N |
| AKS control plane | Free tier | $0 | 1 | $0 |
| ACR | Standard | ~$0.007 | 1 | ~$0.007 |
| Log Analytics | Per GB | ~$2.3/GB | — | variable |

### Monthly (examples)

**Minimal (no sandbox load):**

- 3 × system nodes: ~$500/month
- ACR: ~$5/month
- Log Analytics: ~$50/month (assumes ~20 GB/month)
- **Total**: ~$555/month

**Typical (10 active sandboxes on average):**

- System nodes: ~$500/month
- 10 × sandbox nodes: ~$3,350/month
- ACR + Logs: ~$55/month
- **Total**: ~$3,905/month

**Optimization tips:**

- Use Azure Reserved Instances (30–50% savings)
- Tune node pool autoscaler settings
- Set a sensible sandbox TTL
- Use spot instances for the sandbox node pool (70–90% savings)

---

## Performance tuning

### Orchestrator

- **Concurrency**: tune `MAX_CONCURRENT_EXECS` (default 20)
- **Replicas**: increase orchestrator replicas to handle more API traffic
- **Resources**: raise CPU/memory limits on the orchestrator pod

### Sandbox

- **Node spec**: pick a VM size that matches your workload
- **Autoscaler**: tune min/max node counts
- **Resource limits**: adjust per-pod CPU/memory

### Networking

- **NetworkPolicy**: if strict isolation is not required, set `block_network: false`
- **Pod networking**: consider Azure CNI Overlay mode
