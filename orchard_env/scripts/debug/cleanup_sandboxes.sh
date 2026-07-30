#!/bin/bash
set -euo pipefail

###############################################################################
# Cleanup all sandbox pods in the shared namespace
# Also cleans up any legacy sbx-* namespaces if they exist
###############################################################################

SANDBOX_NS="${SANDBOX_NAMESPACE:-sandbox-pods}"

echo "============================================"
echo "Sandbox Cleanup Script"
echo "============================================"
echo ""

# Clean up pods in the shared namespace
echo "Checking sandbox pods in namespace: $SANDBOX_NS"
SANDBOX_PODS=$(kubectl get pods -n "$SANDBOX_NS" -l app=sandbox -o name 2>/dev/null || echo "")

if [ -z "$SANDBOX_PODS" ]; then
    echo "No sandbox pods found in $SANDBOX_NS."
else
    COUNT=$(echo "$SANDBOX_PODS" | wc -l)
    echo "Found $COUNT sandbox pod(s) to delete:"
    kubectl get pods -n "$SANDBOX_NS" -l app=sandbox -o wide
    echo ""

    read -p "Do you want to delete all sandbox pods? (yes/no): " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "Aborted."
        exit 0
    fi

    echo ""
    echo "Deleting sandbox pods..."
    kubectl delete pods -n "$SANDBOX_NS" -l app=sandbox --wait=false

    # Also delete per-pod network policies
    echo "Deleting sandbox network policies..."
    kubectl delete networkpolicy -n "$SANDBOX_NS" -l sandbox-id --wait=false 2>/dev/null || true
    kubectl delete networkpolicy -n "$SANDBOX_NS" --all --wait=false 2>/dev/null || true

    echo ""
    echo "✓ Cleanup initiated for $COUNT pod(s)"
fi

# Also clean up any legacy sbx-* namespaces
LEGACY_NS=$(kubectl get namespaces -o name 2>/dev/null | grep "namespace/sbx-" | sed 's|namespace/||' || echo "")
if [ -n "$LEGACY_NS" ]; then
    LEGACY_COUNT=$(echo "$LEGACY_NS" | wc -l)
    echo ""
    echo "Found $LEGACY_COUNT legacy sbx-* namespace(s):"
    echo "$LEGACY_NS"
    read -p "Delete legacy namespaces too? (yes/no): " CONFIRM2
    if [ "$CONFIRM2" = "yes" ]; then
        for NS in $LEGACY_NS; do
            echo "  Deleting $NS..."
            kubectl delete namespace "$NS" --wait=false
        done
        echo "✓ Legacy namespace cleanup initiated"
    fi
fi

echo ""
echo "Check status with: kubectl get pods -n $SANDBOX_NS -l app=sandbox"
echo "============================================"
