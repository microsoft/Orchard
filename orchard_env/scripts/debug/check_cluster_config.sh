#!/bin/bash
set -euo pipefail

###############################################################################
# AKS Cluster Configuration Check
# This script displays cluster settings including autoscaling
###############################################################################

echo "============================================"
echo "AKS Cluster Configuration"
echo "============================================"
echo ""

# Source config if available
if [ -f .azure-config ]; then
    source .azure-config
    echo "Loaded configuration from .azure-config"
else
    echo "No .azure-config found. Please set environment variables manually."
    RESOURCE_GROUP="${RESOURCE_GROUP:-}"
    CLUSTER_NAME="${CLUSTER_NAME:-}"
fi

echo ""

# 1. Cluster basic info
echo "=== 1. Cluster Basic Info ==="
if [ -n "$RESOURCE_GROUP" ] && [ -n "$CLUSTER_NAME" ]; then
    az aks show \
        --resource-group "$RESOURCE_GROUP" \
        --name "$CLUSTER_NAME" \
        --query "{name:name, location:location, kubernetesVersion:kubernetesVersion, nodeResourceGroup:nodeResourceGroup}" \
        -o table
else
    echo "RESOURCE_GROUP or CLUSTER_NAME not set"
fi
echo ""

# 2. Node pools
echo "=== 2. Node Pools ==="
kubectl get nodes -o wide
echo ""

# 3. Node pool details with autoscaling
echo "=== 3. Node Pool Autoscaling Configuration ==="
if [ -n "$RESOURCE_GROUP" ] && [ -n "$CLUSTER_NAME" ]; then
    az aks nodepool list \
        --resource-group "$RESOURCE_GROUP" \
        --cluster-name "$CLUSTER_NAME" \
        --query "[].{Name:name, VMSize:vmSize, Count:count, MinCount:minCount, MaxCount:maxCount, Autoscale:enableAutoScaling, Mode:mode}" \
        -o table
else
    echo "RESOURCE_GROUP or CLUSTER_NAME not set"
fi
echo ""

# 4. Cluster autoscaler status
echo "=== 4. Cluster Autoscaler Logs (Recent Events) ==="
kubectl get events -n kube-system --sort-by='.lastTimestamp' | grep -i autoscaler | tail -10 || echo "No autoscaler events found"
echo ""

# 5. Current node resource usage
echo "=== 5. Node Resource Usage ==="
kubectl top nodes 2>/dev/null || echo "Metrics server not available. Install with: kubectl apply -f https://github.com/kubernetes-metrics-server/metrics-server/releases/latest/download/components.yaml"
echo ""

# 6. Node capacity and allocatable resources
echo "=== 6. Node Capacity Details ==="
kubectl get nodes -o custom-columns=\
NAME:.metadata.name,\
CPU_CAP:.status.capacity.cpu,\
MEM_CAP:.status.capacity.memory,\
CPU_ALLOC:.status.allocatable.cpu,\
MEM_ALLOC:.status.allocatable.memory
echo ""

# 7. Pods per node
echo "=== 7. Pods Distribution Across Nodes ==="
for node in $(kubectl get nodes -o jsonpath='{.items[*].metadata.name}'); do
    pod_count=$(kubectl get pods --all-namespaces --field-selector spec.nodeName=$node --no-headers | wc -l)
    echo "$node: $pod_count pods"
done
echo ""

# 8. Autoscaler configuration
echo "=== 8. Cluster Autoscaler ConfigMap ==="
kubectl get configmap -n kube-system cluster-autoscaler-status -o yaml 2>/dev/null || echo "Autoscaler configmap not found"
echo ""

# 9. Check sandbox node pool specifically
echo "=== 9. Sandbox Node Pool Details ==="
kubectl get nodes -l workload=sandbox -o wide
echo ""
if [ -n "$RESOURCE_GROUP" ] && [ -n "$CLUSTER_NAME" ]; then
    echo "Sandbox node pool configuration:"
    az aks nodepool show \
        --resource-group "$RESOURCE_GROUP" \
        --cluster-name "$CLUSTER_NAME" \
        --name sbx \
        --query "{Name:name, Count:count, MinCount:minCount, MaxCount:maxCount, AutoScale:enableAutoScaling, VMSize:vmSize, Taints:nodeTaints}" \
        -o json 2>/dev/null || echo "Sandbox node pool 'sbx' not found"
fi
echo ""

# 10. System node pool details
echo "=== 10. System Node Pool Details ==="
kubectl get nodes -l pool=system -o wide
echo ""
if [ -n "$RESOURCE_GROUP" ] && [ -n "$CLUSTER_NAME" ]; then
    echo "System node pool configuration:"
    az aks nodepool show \
        --resource-group "$RESOURCE_GROUP" \
        --cluster-name "$CLUSTER_NAME" \
        --name sys \
        --query "{Name:name, Count:count, MinCount:minCount, MaxCount:maxCount, AutoScale:enableAutoScaling, VMSize:vmSize}" \
        -o json 2>/dev/null || echo "System node pool 'sys' not found"
fi
echo ""

# 11. Check for pending pods (trigger autoscaling)
echo "=== 11. Pending Pods (May Trigger Autoscaling) ==="
kubectl get pods --all-namespaces --field-selector status.phase=Pending
echo ""

# 12. Resource quotas
echo "=== 12. Resource Quotas ==="
kubectl get resourcequota --all-namespaces
echo ""

echo "============================================"
echo "Configuration Check Complete"
echo "============================================"
echo ""
echo "Key Points:"
echo "- Check if autoscaling is enabled (enableAutoScaling: true)"
echo "- Verify min/max counts for node pools"
echo "- Monitor pending pods that might trigger scale-up"
echo "- Watch autoscaler events for scale decisions"
echo ""
echo "To manually scale a node pool:"
echo "  az aks nodepool scale --resource-group \$RESOURCE_GROUP --cluster-name \$CLUSTER_NAME --name <pool-name> --node-count <count>"
echo ""
