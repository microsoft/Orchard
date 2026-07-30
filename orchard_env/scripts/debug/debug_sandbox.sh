#!/bin/bash
set -euo pipefail

###############################################################################
# Debug Sandbox Pod Creation Issues
#
# All sandbox pods now live in a shared namespace (sandbox-pods).
###############################################################################

SANDBOX_NS="${SANDBOX_NAMESPACE:-sandbox-pods}"

echo "============================================"
echo "Sandbox Pod Diagnostics"
echo "============================================"
echo "Shared sandbox namespace: $SANDBOX_NS"
echo ""

# Check if the shared namespace exists
echo "1. Checking shared sandbox namespace..."
kubectl get namespace "$SANDBOX_NS" 2>/dev/null || {
    echo "ERROR: Namespace '$SANDBOX_NS' not found. Was deploy_k8s.sh run?"
    exit 1
}
echo ""

# List all sandbox pods
echo "2. All sandbox pods in $SANDBOX_NS:"
echo "============================================"
kubectl get pods -n "$SANDBOX_NS" -l app=sandbox -o wide
echo ""

SANDBOX_ID="${1:-}"

if [ -n "$SANDBOX_ID" ]; then
    POD_NAME="sandbox-${SANDBOX_ID}"
    echo "Debugging specific sandbox: $SANDBOX_ID (pod: $POD_NAME)"
else
    # Get the most recently created sandbox pod
    POD_NAME=$(kubectl get pods -n "$SANDBOX_NS" -l app=sandbox \
        --sort-by='.metadata.creationTimestamp' \
        -o jsonpath='{.items[-1:].metadata.name}' 2>/dev/null || echo "")
    if [ -z "$POD_NAME" ]; then
        echo "No sandbox pods found. Try creating a sandbox first."
        exit 1
    fi
    echo "Latest sandbox pod: $POD_NAME"
fi
echo ""

# Describe the pod for detailed status
echo "3. Pod detailed status:"
echo "============================================"
kubectl describe pod -n "$SANDBOX_NS" "$POD_NAME"
echo ""

# Check events
echo "4. Recent events in namespace:"
echo "============================================"
kubectl get events -n "$SANDBOX_NS" --sort-by='.lastTimestamp' | tail -20
echo ""

# Check pod logs if container started
echo "5. Pod logs (if available):"
echo "============================================"
kubectl logs -n "$SANDBOX_NS" "$POD_NAME" 2>&1 || echo "No logs available (container may not have started)"
echo ""

# Check node status for sandbox nodes
echo "6. Checking sandbox node pool status:"
echo "============================================"
kubectl get nodes -l workload=sandbox -o wide || echo "No nodes with label workload=sandbox"
echo ""

# Check all nodes
echo "7. All nodes:"
kubectl get nodes -o wide
echo ""

# Summary counts
echo "8. Sandbox pod summary:"
echo "============================================"
echo "  Running:  $(kubectl get pods -n "$SANDBOX_NS" -l app=sandbox --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l)"
echo "  Pending:  $(kubectl get pods -n "$SANDBOX_NS" -l app=sandbox --field-selector=status.phase=Pending --no-headers 2>/dev/null | wc -l)"
echo "  Failed:   $(kubectl get pods -n "$SANDBOX_NS" -l app=sandbox --field-selector=status.phase=Failed --no-headers 2>/dev/null | wc -l)"
echo ""

# Problematic pods
echo "9. Problematic sandbox pods:"
echo "============================================"
kubectl get pods -n "$SANDBOX_NS" -l app=sandbox -o wide | grep -E "Pending|ImagePull|ErrImage|CrashLoop" || echo "No problematic pods found"
echo ""

echo "============================================"
echo "Diagnostics Complete"
echo "============================================"
echo ""
echo "Usage: $0 [sandbox_id]"
echo ""
echo "Common issues:"
echo "1. ImagePullBackOff: Image cannot be pulled (check image name and registry access)"
echo "2. Pending + 'no nodes available': Sandbox node pool has no nodes (autoscaler issue)"
echo "3. Pending + 'Insufficient resources': Nodes don't have enough CPU/memory"
echo "4. Pending + 'node had taint': Pod doesn't have toleration for sandbox taint"
echo ""
