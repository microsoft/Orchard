#!/bin/bash
set -euo pipefail

###############################################################################
# Build and Push Docker Image to ACR
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
    echo "Error: ACR_NAME is not set"
    echo "Either:"
    echo "  1. Run deploy/scripts/deploy_aks.sh first, or"
    echo "  2. Set ACR_NAME environment variable"
    echo ""
    echo "Example: export ACR_NAME=myacr"
    exit 1
fi

IMAGE_NAME="sandbox-orchestrator"
IMAGE_TAG="${IMAGE_TAG:-latest}"
FULL_IMAGE="${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}"

SANDBOX_IMAGE_NAME="sandbox-python"
SANDBOX_IMAGE_TAG="${SANDBOX_IMAGE_TAG:-3.11}"
FULL_SANDBOX_IMAGE="${ACR_NAME}.azurecr.io/${SANDBOX_IMAGE_NAME}:${SANDBOX_IMAGE_TAG}"

AGENT_INJECTOR_IMAGE_NAME="sandbox-agent-injector"
AGENT_INJECTOR_IMAGE_TAG="${AGENT_INJECTOR_TAG:-latest}"
FULL_AGENT_INJECTOR_IMAGE="${ACR_NAME}.azurecr.io/${AGENT_INJECTOR_IMAGE_NAME}:${AGENT_INJECTOR_IMAGE_TAG}"

# What to build: orchestrator, sandbox, agent-injector, or all (default)
BUILD_TARGET="${1:-all}"

echo "============================================"
echo "Build and Push Docker Images"
echo "============================================"
echo "ACR: $ACR_NAME"
echo "Target: $BUILD_TARGET"
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "orchestrator" ]]; then
    echo "Orchestrator image:     $FULL_IMAGE"
fi
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "sandbox" ]]; then
    echo "Sandbox image:          $FULL_SANDBOX_IMAGE"
fi
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "agent-injector" ]]; then
    echo "Agent-injector image:   $FULL_AGENT_INJECTOR_IMAGE"
fi
echo "============================================"
echo ""

# Change to project root
cd "$PROJECT_ROOT"

# Login to ACR
echo "Logging in to ACR..."
az acr login --name "$ACR_NAME"
echo "✓ Logged in to ACR"
echo ""

# ---- Orchestrator image ----
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "orchestrator" ]]; then
    echo "Building orchestrator image..."
    docker build -f docker/orchestrator.Dockerfile -t "$FULL_IMAGE" .
    echo "✓ Image built: $FULL_IMAGE"
    echo ""

    echo "Pushing orchestrator image to ACR..."
    docker push "$FULL_IMAGE"
    echo "✓ Image pushed to ACR"
    echo ""
fi

# ---- Sandbox image (with agent baked in — optional, for python-only use) ----
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "sandbox" ]]; then
    echo "Building sandbox image (with in-pod agent)..."
    docker build -f docker/sandbox.Dockerfile -t "$FULL_SANDBOX_IMAGE" .
    echo "✓ Image built: $FULL_SANDBOX_IMAGE"
    echo ""

    echo "Pushing sandbox image to ACR..."
    docker push "$FULL_SANDBOX_IMAGE"
    echo "✓ Image pushed to ACR"
    echo ""
fi

# ---- Agent-injector init container image ----
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "agent-injector" ]]; then
    echo "Building agent-injector image (init container for any custom image)..."
    docker build -f docker/agent-injector.Dockerfile -t "$FULL_AGENT_INJECTOR_IMAGE" .
    echo "✓ Image built: $FULL_AGENT_INJECTOR_IMAGE"
    echo ""

    echo "Pushing agent-injector image to ACR..."
    docker push "$FULL_AGENT_INJECTOR_IMAGE"
    echo "✓ Image pushed to ACR"
    echo ""
fi

# Verify images
echo "Verifying images in ACR..."
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "orchestrator" ]]; then
    az acr repository show \
        --name "$ACR_NAME" \
        --repository "$IMAGE_NAME" \
        --output table
fi
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "sandbox" ]]; then
    az acr repository show \
        --name "$ACR_NAME" \
        --repository "$SANDBOX_IMAGE_NAME" \
        --output table
fi
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "agent-injector" ]]; then
    az acr repository show \
        --name "$ACR_NAME" \
        --repository "$AGENT_INJECTOR_IMAGE_NAME" \
        --output table
fi

echo ""
echo "============================================"
echo "Build Complete!"
echo "============================================"
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "orchestrator" ]]; then
    echo "Orchestrator:     $FULL_IMAGE"
fi
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "sandbox" ]]; then
    echo "Sandbox:          $FULL_SANDBOX_IMAGE"
fi
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "agent-injector" ]]; then
    echo "Agent-injector:   $FULL_AGENT_INJECTOR_IMAGE"
fi
echo ""
echo "Usage:"
echo "  $0                # build all images"
echo "  $0 orchestrator   # build orchestrator only"
echo "  $0 sandbox        # build sandbox only (agent baked in)"
echo "  $0 agent-injector # build agent-injector init container only"
echo ""
echo "Next step: Deploy to Kubernetes"
echo "  ./deploy/scripts/deploy_k8s.sh"
echo "============================================"
