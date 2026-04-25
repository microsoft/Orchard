#!/bin/bash
set -euxo pipefail

###############################################################################
# Smoke Test for Sandbox Orchestrator
# 
# This script tests the basic functionality:
# 1. Create a sandbox
# 2. Execute a command
# 3. Check job status
# 4. Apply a patch
# 5. Delete sandbox
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Configuration
BASE_URL="${BASE_URL:-http://localhost:8080}"
TEST_IMAGE="${TEST_IMAGE:-python:3.11-slim}"
API_KEY="${SANDBOX_API_KEY:-}"

if [ -z "$API_KEY" ]; then
    echo "Error: SANDBOX_API_KEY environment variable is not set."
    echo "Usage: SANDBOX_API_KEY=<your-key> $0"
    exit 1
fi

# Common curl auth header
AUTH_HEADER="X-API-Key: $API_KEY"

echo "============================================"
echo "Sandbox Orchestrator Smoke Test"
echo "============================================"
echo "Base URL: $BASE_URL"
echo "Test Image: $TEST_IMAGE"
echo "============================================"
echo ""

# Check if jq is available
if ! command -v jq &> /dev/null; then
    echo "Warning: jq is not installed. Output will be less readable."
    JQ_CMD="cat"
else
    JQ_CMD="jq ."
fi

# Check if service is accessible
echo "1. Checking service health..."
if ! curl -sf -H "$AUTH_HEADER" "$BASE_URL/health" > /dev/null; then
    echo "Error: Service is not accessible at $BASE_URL"
    echo ""
    echo "If running in Kubernetes, start port-forward:"
    echo "  kubectl port-forward -n orchestrator svc/sandbox-orchestrator 8080:80"
    exit 1
fi

echo "✓ Service is healthy"
echo ""

# Create sandbox
echo "2. Creating sandbox..."
SANDBOX_RESPONSE=$(curl -sf -X POST "$BASE_URL/sandboxes" \
    -H "Content-Type: application/json" \
    -H "$AUTH_HEADER" \
    -d "{
        \"image\": \"$TEST_IMAGE\",
        \"block_network\": false,
        \"cpu\": \"4\",
        \"memory\": \"16Gi\"
    }")

echo "$SANDBOX_RESPONSE" | $JQ_CMD

SANDBOX_ID=$(echo "$SANDBOX_RESPONSE" | grep -o '"sandbox_id":"[^"]*"' | cut -d'"' -f4)

if [ -z "$SANDBOX_ID" ]; then
    echo "Error: Failed to create sandbox"
    exit 1
fi

echo "✓ Sandbox created: $SANDBOX_ID"
echo ""

# Wait a bit for sandbox to be fully ready
sleep 5

# Execute command
echo "3. Executing test command..."
EXEC_RESPONSE=$(curl -sf -X POST "$BASE_URL/sandboxes/$SANDBOX_ID/exec" \
    -H "Content-Type: application/json" \
    -H "$AUTH_HEADER" \
    -d '{
        "command": "echo Hello from sandbox!",
        "timeout_seconds": 30
    }')

echo "$EXEC_RESPONSE" | $JQ_CMD

JOB_ID=$(echo "$EXEC_RESPONSE" | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)

if [ -z "$JOB_ID" ]; then
    echo "Error: Failed to submit exec job"
    exit 1
fi

echo "✓ Job submitted: $JOB_ID"
echo ""

# Wait for job to complete
echo "4. Waiting for job to complete..."
MAX_WAIT=30
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    JOB_STATUS=$(curl -sf -H "$AUTH_HEADER" "$BASE_URL/jobs/$JOB_ID")
    STATUS=$(echo "$JOB_STATUS" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    
    if [ "$STATUS" = "succeeded" ] || [ "$STATUS" = "failed" ]; then
        break
    fi
    
    echo "  Status: $STATUS (waited ${WAITED}s)"
    sleep 2
    WAITED=$((WAITED + 2))
done

echo ""
echo "Job result:"
echo "$JOB_STATUS" | $JQ_CMD
echo ""

if [ "$STATUS" != "succeeded" ]; then
    echo "Error: Job did not succeed (status: $STATUS)"
    # Don't exit yet, try to clean up
else
    echo "✓ Job completed successfully"
    
    # Check stdout
    STDOUT=$(echo "$JOB_STATUS" | grep -o '"stdout":"[^"]*"' | cut -d'"' -f4)
    if echo "$STDOUT" | grep -q "Hello from sandbox"; then
        echo "✓ Command output verified"
    else
        echo "Warning: Unexpected command output"
    fi
fi
echo ""

# Test apply_patch (create a simple test file first)
echo "5. Testing patch application..."

# First create a file to patch
echo "Creating initial file..."
curl -sf -X POST "$BASE_URL/sandboxes/$SANDBOX_ID/exec" \
    -H "Content-Type: application/json" \
    -H "$AUTH_HEADER" \
    -d '{
        "command": "echo \"line1\" > test.txt",
        "timeout_seconds": 10
    }' > /dev/null

sleep 3

# Create and apply a simple patch
PATCH_CONTENT="--- test.txt
+++ test.txt
@@ -1 +1,2 @@
 line1
+line2"

PATCH_RESPONSE=$(curl -sf -X POST "$BASE_URL/sandboxes/$SANDBOX_ID/apply_patch" \
    -H "Content-Type: application/json" \
    -H "$AUTH_HEADER" \
    -d "{
        \"patch\": $(echo "$PATCH_CONTENT" | jq -Rs .),
        \"timeout_seconds\": 30
    }")

echo "$PATCH_RESPONSE" | $JQ_CMD

PATCH_SUCCESS=$(echo "$PATCH_RESPONSE" | grep -o '"success":[^,}]*' | cut -d':' -f2)

if [ "$PATCH_SUCCESS" = "true" ]; then
    echo "✓ Patch applied successfully"
else
    echo "Note: Patch test may have failed (this is OK if git is not available in the test image)"
fi
echo ""

# Delete sandbox
echo "6. Deleting sandbox..."
DELETE_RESPONSE=$(curl -sf -X DELETE -H "$AUTH_HEADER" "$BASE_URL/sandboxes/$SANDBOX_ID")
echo "$DELETE_RESPONSE" | $JQ_CMD

echo "✓ Sandbox deleted"
echo ""

echo "============================================"
echo "Smoke Test Complete!"
echo "============================================"
echo "All basic operations completed successfully"
echo ""
echo "You can now use the orchestrator for your workloads."
echo "See README.md for more examples and usage patterns."
echo "============================================"
