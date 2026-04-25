#!/bin/bash
set -euo pipefail

# Dry run mode: print commands without executing
# Set to false to execute commands
DRY_RUN=false

# Function to execute or print commands
run_cmd() {
    echo ">>> $*"
    if [ "$DRY_RUN" = false ]; then
        if ! "$@"; then
            echo "ERROR: Command failed: $*"
            exit 1
        fi
    fi
}

###############################################################################
# Azure AKS Cluster Deployment Script
# 
# This script creates all necessary Azure resources for the sandbox orchestrator:
# - Resource Group
# - AKS Cluster with Calico network policy
# - Two node pools: system (sys) and sandbox (sbx)
# - Azure Container Registry (ACR)
# - Log Analytics workspace
###############################################################################

# Configuration variables (edit these as needed)
RESOURCE_GROUP="${RESOURCE_GROUP:-sandbox-orchestrator-rg}"
LOCATION="${LOCATION:-westus2}"
CLUSTER_NAME="${CLUSTER_NAME:-sandbox-aks}"
ACR_NAME="${ACR_NAME:-sandboxacr$(date +%s)}"  # Must be globally unique
LOG_ANALYTICS_WORKSPACE="${LOG_ANALYTICS_WORKSPACE:-sandbox-logs}"

# Node pool configurations
SYSTEM_NODE_SIZE="${SYSTEM_NODE_SIZE:-Standard_D8as_v5}"
SYSTEM_NODE_COUNT="${SYSTEM_NODE_COUNT:-1}"
SYSTEM_NODE_MIN="${SYSTEM_NODE_MIN:-1}"
SYSTEM_NODE_MAX="${SYSTEM_NODE_MAX:-2}"

SANDBOX_NODE_SIZE="${SANDBOX_NODE_SIZE:-Standard_D8as_v5}"
SANDBOX_NODE_MIN="${SANDBOX_NODE_MIN:-1}"
SANDBOX_NODE_MAX="${SANDBOX_NODE_MAX:-5}"

# Kubernetes version
K8S_VERSION="${K8S_VERSION:-1.32}"

echo "============================================"
echo "Azure AKS Sandbox Orchestrator Deployment"
echo "============================================"
echo "Resource Group: $RESOURCE_GROUP"
echo "Location: $LOCATION"
echo "Cluster Name: $CLUSTER_NAME"
echo "ACR Name: $ACR_NAME"
echo "============================================"
echo ""

# Check if Azure CLI is installed
if ! command -v az &> /dev/null; then
    echo "Error: Azure CLI is not installed. Please install it first."
    echo "https://docs.microsoft.com/en-us/cli/azure/install-azure-cli"
    exit 1
fi

# Check if logged in (skip in dry run mode)
if [ "$DRY_RUN" = false ]; then
    echo "Checking Azure CLI login status..."
    if ! az account show &> /dev/null; then
        echo "Not logged in. Please log in to Azure..."
        az login
    fi
    SUBSCRIPTION_ID=$(az account show --query id -o tsv)
    echo "Using subscription: $SUBSCRIPTION_ID"
    echo ""
else
    echo "[DRY RUN MODE] Skipping login check"
    SUBSCRIPTION_ID="<subscription-id>"
    echo ""
fi

# Create resource group
echo "Creating resource group: $RESOURCE_GROUP"
run_cmd az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --tags "purpose=sandbox-orchestrator" "managed-by=script"

echo "✓ Resource group created"
echo ""

# Create Log Analytics workspace
echo "Creating Log Analytics workspace: $LOG_ANALYTICS_WORKSPACE"
run_cmd az monitor log-analytics workspace create \
    --resource-group "$RESOURCE_GROUP" \
    --workspace-name "$LOG_ANALYTICS_WORKSPACE" \
    --location "$LOCATION"

if [ "$DRY_RUN" = false ]; then
    LOG_ANALYTICS_ID=$(az monitor log-analytics workspace show \
        --resource-group "$RESOURCE_GROUP" \
        --workspace-name "$LOG_ANALYTICS_WORKSPACE" \
        --query id -o tsv)
else
    LOG_ANALYTICS_ID="<log-analytics-id>"
fi

echo "✓ Log Analytics workspace created"
echo ""

# Create ACR
echo "Creating Azure Container Registry: $ACR_NAME"
run_cmd az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACR_NAME" \
    --sku Standard \
    --location "$LOCATION" \
    --admin-enabled false

echo "✓ ACR created"
echo ""

# Create AKS cluster with system node pool
echo "Creating AKS cluster: $CLUSTER_NAME"
echo "This may take 10-15 minutes..."
run_cmd az aks create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CLUSTER_NAME" \
    --location "$LOCATION" \
    --kubernetes-version "$K8S_VERSION" \
    --tier standard \
    --node-count "$SYSTEM_NODE_COUNT" \
    --node-vm-size "$SYSTEM_NODE_SIZE" \
    --nodepool-name sys \
    --nodepool-labels "pool=system" \
    --enable-cluster-autoscaler \
    --min-count "$SYSTEM_NODE_MIN" \
    --max-count "$SYSTEM_NODE_MAX" \
    --cluster-autoscaler-profile \
        ignore-daemonsets-utilization=true \
        scale-down-delay-after-add=10m \
        scale-down-unneeded-time=10m \
        scale-down-delay-after-delete=1m \
    --network-plugin azure \
    --network-policy calico \
    --enable-managed-identity \
    --attach-acr "$ACR_NAME" \
    --enable-addons monitoring \
    --workspace-resource-id "$LOG_ANALYTICS_ID" \
    --generate-ssh-keys \
    --tags "purpose=sandbox-orchestrator"

echo "✓ AKS cluster created with system node pool"
echo ""

# Add sandbox node pool
echo "Adding sandbox node pool..."
run_cmd az aks nodepool add \
    --resource-group "$RESOURCE_GROUP" \
    --cluster-name "$CLUSTER_NAME" \
    --name sbx \
    --node-count 0 \
    --node-vm-size "$SANDBOX_NODE_SIZE" \
    --labels "workload=sandbox" "pool=sandbox" \
    --enable-cluster-autoscaler \
    --min-count "$SANDBOX_NODE_MIN" \
    --max-count "$SANDBOX_NODE_MAX" \
    --node-taints "workload=sandbox:NoSchedule"

echo "✓ Sandbox node pool added"
echo ""

# Get AKS credentials
echo "Getting AKS credentials..."
run_cmd az aks get-credentials \
    --resource-group "$RESOURCE_GROUP" \
    --name "$CLUSTER_NAME" \
    --overwrite-existing

echo "✓ Credentials configured"
echo ""

# Patch calico-typha to tolerate sandbox taint
# AKS-managed Calico doesn't tolerate custom taints by default,
# so calico-typha pods can't schedule on sandbox nodes.
echo "Patching calico-typha to tolerate sandbox node taint..."
run_cmd kubectl -n calico-system patch deployment calico-typha --type=strategic -p '{
  "spec": {
    "template": {
      "spec": {
        "tolerations": [
          {
            "key": "workload",
            "value": "sandbox",
            "effect": "NoSchedule"
          }
        ]
      }
    }
  }
}'
echo "✓ calico-typha patched"
echo ""

# Verify cluster access
echo "Verifying cluster access..."
run_cmd kubectl cluster-info
echo ""
run_cmd kubectl get nodes
echo ""

# Display summary
echo "============================================"
echo "Deployment Complete!"
echo "============================================"
echo ""
echo "Resource Group: $RESOURCE_GROUP"
echo "AKS Cluster: $CLUSTER_NAME"
echo "ACR Name: $ACR_NAME"
echo "ACR Login Server: ${ACR_NAME}.azurecr.io"
echo ""
echo "Node Pools:"
echo "  - sys: System pool (${SYSTEM_NODE_SIZE}, ${SYSTEM_NODE_MIN}-${SYSTEM_NODE_MAX} nodes)"
echo "  - sbx: Sandbox pool (${SANDBOX_NODE_SIZE}, ${SANDBOX_NODE_MIN}-${SANDBOX_NODE_MAX} nodes)"
echo ""
echo "Next Steps:"
echo "1. Build and push the orchestrator image:"
echo "   export ACR_NAME=$ACR_NAME"
echo "   ./deploy/scripts/build_push.sh"
echo ""
echo "2. Deploy the orchestrator to Kubernetes:"
echo "   ./deploy/scripts/deploy_k8s.sh"
echo ""
echo "3. Run smoke tests:"
echo "   ./deploy/scripts/smoke_test.sh"
echo ""
echo "To delete all resources:"
echo "   az group delete --name $RESOURCE_GROUP --yes --no-wait"
echo "============================================"

# Save configuration for other scripts
if [ "$DRY_RUN" = false ]; then
    cat > .azure-config << EOF
export RESOURCE_GROUP="$RESOURCE_GROUP"
export LOCATION="$LOCATION"
export CLUSTER_NAME="$CLUSTER_NAME"
export ACR_NAME="$ACR_NAME"
export LOG_ANALYTICS_WORKSPACE="$LOG_ANALYTICS_WORKSPACE"
EOF
    echo ""
    echo "Configuration saved to .azure-config"
    echo "Source it in other scripts with: source .azure-config"
else
    echo ""
    echo "[DRY RUN] Would save configuration to .azure-config"
fi
