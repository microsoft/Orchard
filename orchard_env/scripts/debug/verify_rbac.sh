#!/bin/bash
set -euo pipefail

echo "============================================"
echo "RBAC Verification Script"
echo "============================================"
echo ""

# Get sandbox namespace
SANDBOX_NS="${SANDBOX_NAMESPACE:-sandbox-pods}"

echo "Testing with sandbox namespace: $SANDBOX_NS"
echo ""

# Get pod name in sandbox namespace
SANDBOX_ID="${1:-}"
if [ -n "$SANDBOX_ID" ]; then
    POD_NAME="sandbox-${SANDBOX_ID}"
else
    POD_NAME=$(kubectl get pods -n "$SANDBOX_NS" -l app=sandbox -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
fi

if [ -z "$POD_NAME" ]; then
    echo "No sandbox pod found in namespace $SANDBOX_NS"
    exit 1
fi

echo "Pod name: $POD_NAME"
echo ""

# Test RBAC permissions
echo "1. Testing if service account can create pods/exec in sandbox namespace:"
kubectl auth can-i create pods/exec \
    --as=system:serviceaccount:orchestrator:sandbox-orchestrator \
    -n "$SANDBOX_NS"
echo ""

echo "2. Testing if service account can get pods in sandbox namespace:"
kubectl auth can-i get pods \
    --as=system:serviceaccount:orchestrator:sandbox-orchestrator \
    -n "$SANDBOX_NS"
echo ""

echo "3. Checking ClusterRole:"
kubectl get clusterrole sandbox-orchestrator -o yaml
echo ""

echo "4. Checking ClusterRoleBinding:"
kubectl get clusterrolebinding sandbox-orchestrator -o yaml
echo ""

echo "5. Checking ServiceAccount:"
kubectl get serviceaccount -n orchestrator sandbox-orchestrator -o yaml
echo ""

echo "6. Testing actual exec (should work):"
kubectl exec -n "$SANDBOX_NS" "$POD_NAME" -c sandbox -- ls /
echo ""

echo "7. Checking orchestrator pod image:"
kubectl get pods -n orchestrator -l app=sandbox-orchestrator -o jsonpath='{.items[*].spec.containers[*].image}'
echo ""
echo ""

echo "8. Checking orchestrator pod creation time:"
kubectl get pods -n orchestrator -l app=sandbox-orchestrator -o wide
echo ""

echo "============================================"
echo "Verification Complete"
echo "============================================"
