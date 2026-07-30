#!/bin/bash
set -euo pipefail

echo "============================================"
echo "AKS RBAC Diagnostic Script"
echo "============================================"
echo ""

# Get orchestrator pod
ORCH_POD=$(kubectl get pods -n orchestrator -l app=sandbox-orchestrator -o jsonpath='{.items[0].metadata.name}')
echo "Orchestrator pod: $ORCH_POD"

# Get sandbox namespace
SANDBOX_NS="${SANDBOX_NAMESPACE:-sandbox-pods}"
echo "Sandbox namespace: $SANDBOX_NS"

# Get sandbox pod (use arg or pick first)
SANDBOX_ID="${1:-}"
if [ -n "$SANDBOX_ID" ]; then
    SANDBOX_POD="sandbox-${SANDBOX_ID}"
else
    SANDBOX_POD=$(kubectl get pods -n "$SANDBOX_NS" -l app=sandbox -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
fi

if [ -z "$SANDBOX_POD" ]; then
    echo "No sandbox pod found. Create a sandbox first."
    exit 1
fi

echo "Sandbox pod: $SANDBOX_POD"
echo ""

# Test 1: Can kubectl exec work?
echo "Test 1: Direct kubectl exec (should work):"
kubectl exec -n $SANDBOX_NS $SANDBOX_POD -c sandbox -- echo "kubectl exec works"
echo ""

# Test 2: Check if orchestrator can see the pod
echo "Test 2: Can orchestrator service account see the pod?"
kubectl auth can-i get pods --as=system:serviceaccount:orchestrator:sandbox-orchestrator -n $SANDBOX_NS
echo ""

# Test 3: Check if orchestrator can exec
echo "Test 3: Can orchestrator service account create pods/exec?"
kubectl auth can-i create pods/exec --as=system:serviceaccount:orchestrator:sandbox-orchestrator -n $SANDBOX_NS
echo ""

# Test 4: Try to exec from orchestrator pod directly
echo "Test 4: Try exec from inside orchestrator pod:"
kubectl exec -n orchestrator $ORCH_POD -- python3 -c "
import os
from kubernetes import client, config

# Load in-cluster config
config.load_incluster_config()

# Create API client
v1 = client.CoreV1Api()

# Try to exec
from kubernetes.stream import stream

try:
    resp = stream(
        v1.connect_get_namespaced_pod_exec,
        name='$SANDBOX_POD',
        namespace='$SANDBOX_NS',
        container='sandbox',
        command=['echo', 'test'],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False
    )
    print('SUCCESS: Exec worked!')
    print('Output:', resp)
except Exception as e:
    print('ERROR:', str(e))
    import traceback
    traceback.print_exc()
"
echo ""

# Test 5: Check AKS Azure RBAC
echo "Test 5: Checking if AKS uses Azure RBAC integration:"
az aks show --resource-group $RESOURCE_GROUP --name $CLUSTER_NAME --query "aadProfile.enableAzureRbac" 2>/dev/null || echo "Could not check (az cli may not be configured)"
echo ""

# Test 6: Check pod security standards
echo "Test 6: Checking namespace labels for pod security:"
kubectl get ns $SANDBOX_NS -o jsonpath='{.metadata.labels}' | grep -i security || echo "No pod security labels"
echo ""
echo ""

echo "============================================"
echo "Diagnostic Complete"
echo "============================================"
echo ""
echo "If Test 1 works but Test 4 fails, the issue is RBAC."
echo "If both fail, the pod may not be ready or there's a PSP/PSS issue."
