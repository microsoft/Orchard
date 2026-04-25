#!/bin/bash
set -euo pipefail

###############################################################################
# Deploy Orchestrator to Kubernetes
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Load Azure configuration if available
if [ -f "$PROJECT_ROOT/.azure-config" ]; then
    source "$PROJECT_ROOT/.azure-config"
    echo "Loaded configuration from .azure-config"
fi

# Check required variables
if [ -z "${ACR_NAME:-}" ]; then
    echo "Warning: ACR_NAME is not set. Using placeholder."
    echo "You may need to manually update the deployment image."
    ACR_NAME="REPLACE_WITH_YOUR_ACR"
fi

echo "============================================"
echo "Deploy Orchestrator to Kubernetes"
echo "============================================"
echo "ACR: $ACR_NAME"
echo "============================================"
echo ""

# Change to project root
cd "$PROJECT_ROOT"

# Check kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl is not installed"
    exit 1
fi

# Check cluster connection
echo "Checking cluster connection..."
if ! kubectl cluster-info &> /dev/null; then
    echo "Error: Cannot connect to Kubernetes cluster"
    echo "Run: az aks get-credentials --resource-group <RG> --name <CLUSTER>"
    exit 1
fi

echo "✓ Connected to cluster"
echo ""

# Process templates with ACR name
echo "Processing templates..."
TEMP_DEPLOYMENT=$(mktemp)
TEMP_CONFIGMAP=$(mktemp)
sed "s/\${ACR_NAME}/$ACR_NAME/g" deploy/k8s/deployment.yaml > "$TEMP_DEPLOYMENT"
sed "s/\${ACR_NAME}/$ACR_NAME/g" deploy/k8s/configmap.yaml > "$TEMP_CONFIGMAP"
echo "✓ Templates processed"
echo ""

# Apply Kubernetes manifests in order
echo "Applying Kubernetes manifests..."

echo "1. Creating namespace..."
kubectl apply -f deploy/k8s/namespace.yaml

echo "2. Creating service account..."
kubectl apply -f deploy/k8s/serviceaccount.yaml

echo "3. Creating RBAC rules..."
kubectl apply -f deploy/k8s/rbac.yaml

echo "4. Creating config map..."
kubectl apply -f "$TEMP_CONFIGMAP"

echo "5. Creating API keys secret..."
if [ ! -f deploy/k8s/secret.yaml ]; then
    echo "ERROR: deploy/k8s/secret.yaml not found."
    echo "       Copy deploy/k8s/secret.example.yaml to deploy/k8s/secret.yaml and populate API_KEYS."
    echo "       You can generate keys with: python deploy/k8s/gen_keys.py"
    exit 1
fi
kubectl apply -f deploy/k8s/secret.yaml

echo "6. Creating shared sandbox namespace..."
kubectl create namespace sandbox-pods --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace sandbox-pods app=sandbox managed-by=orchestrator --overwrite

echo "7. Deploying Redis for state sharing..."
kubectl apply -f deploy/k8s/redis.yaml
echo "   Waiting for Redis to be ready..."
kubectl wait --for=condition=available --timeout=60s deployment/redis -n orchestrator || {
    echo "Warning: Redis did not become ready in time"
}

echo "8. Creating deployment..."
kubectl apply -f "$TEMP_DEPLOYMENT"

echo "9. Creating service..."
kubectl apply -f deploy/k8s/service.yaml

# Optional resources
if [ -f "deploy/k8s/optional.yaml" ]; then
    echo "10. Creating optional resources (HPA, PDB)..."
    kubectl apply -f deploy/k8s/optional.yaml || echo "Note: Some optional resources may have failed (this is OK)"
fi

# Clean up temp files
rm -f "$TEMP_DEPLOYMENT" "$TEMP_CONFIGMAP"

echo ""
echo "✓ All manifests applied"
echo ""

# Wait for deployment to be ready
echo "Waiting for deployment to be ready..."
kubectl wait --for=condition=available --timeout=300s \
    deployment/sandbox-orchestrator -n orchestrator || {
    echo "Warning: Deployment did not become ready in time"
    echo "Check status with: kubectl get pods -n orchestrator"
}

echo ""
echo "============================================"
echo "Deployment Complete!"
echo "============================================"
echo ""
echo "Check status:"
echo "  kubectl get pods -n orchestrator"
echo "  kubectl logs -n orchestrator -l app=sandbox-orchestrator"
echo ""
echo "Access the service (port-forward):"
echo "  kubectl port-forward -n orchestrator svc/sandbox-orchestrator 8000:80"
echo "  Then visit: http://localhost:8000"
echo ""
echo "Run smoke tests:"
echo "  ./deploy/scripts/smoke_test.sh"
echo "============================================"
