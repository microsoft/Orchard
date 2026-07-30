#!/bin/bash
set -euo pipefail

###############################################################################
# Build and Push Docker Image to ACR
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load Azure configuration if available
if [ -f "$PROJECT_ROOT/.azure-config" ]; then
    source "$PROJECT_ROOT/.azure-config"
    echo "Loaded configuration from .azure-config"
fi

# Check required variables
if [ -z "${ACR_NAME:-}" ]; then
    echo "Error: ACR_NAME is not set"
    echo "Either:"
    echo "  1. Run scripts/deploy_aks.sh first, or"
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

# Sandbox tools: the agent CLIs mounted into every sandbox
# (codex / claude / pi / opencode / hermes). Set the *_VERSION vars below to
# pin exact CLI versions.
TOOLS_IMAGE_NAME="sandbox-tools"
TOOLS_IMAGE_TAG="${TOOLS_TAG:-latest}"
FULL_TOOLS_IMAGE="${ACR_NAME}.azurecr.io/${TOOLS_IMAGE_NAME}:${TOOLS_IMAGE_TAG}"

# Redis is imported from upstream into ACR (not built) so the orchestrator's
# state store can be pulled from the private registry on locked-down nodes.
REDIS_IMAGE_NAME="redis"
REDIS_IMAGE_TAG="${REDIS_IMAGE_TAG:-7-alpine}"
REDIS_SOURCE_IMAGE="${REDIS_SOURCE_IMAGE:-docker.io/library/redis:${REDIS_IMAGE_TAG}}"
FULL_REDIS_IMAGE="${ACR_NAME}.azurecr.io/${REDIS_IMAGE_NAME}:${REDIS_IMAGE_TAG}"

# What to build: orchestrator, sandbox, agent-injector, redis, or all (default)
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
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "tools" ]]; then
    echo "Sandbox-tools image:    $FULL_TOOLS_IMAGE"
fi
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "redis" ]]; then
    echo "Redis image:            $FULL_REDIS_IMAGE"
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
    docker build -t "$FULL_IMAGE" .
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
    docker build -f Dockerfile.sandbox -t "$FULL_SANDBOX_IMAGE" .
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
    docker build -f Dockerfile.agent-injector -t "$FULL_AGENT_INJECTOR_IMAGE" .
    echo "✓ Image built: $FULL_AGENT_INJECTOR_IMAGE"
    echo ""

    echo "Pushing agent-injector image to ACR..."
    docker push "$FULL_AGENT_INJECTOR_IMAGE"
    echo "✓ Image pushed to ACR"
    echo ""
fi

# ---- Sandbox tools image (agent CLIs for every sandbox) ----
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "tools" ]]; then
    echo "Building sandbox-tools image (codex + claude + pi + opencode + hermes CLIs)..."
    docker build -f Dockerfile.tools \
        --build-arg "CODEX_VERSION=${CODEX_VERSION:-latest}" \
        --build-arg "CLAUDE_CODE_VERSION=${CLAUDE_CODE_VERSION:-latest}" \
        --build-arg "OPENCODE_VERSION=${OPENCODE_VERSION:-latest}" \
        ${PI_VERSION:+--build-arg "PI_VERSION=${PI_VERSION}"} \
        ${HERMES_VERSION:+--build-arg "HERMES_VERSION=${HERMES_VERSION}"} \
        -t "$FULL_TOOLS_IMAGE" .
    echo "✓ Image built: $FULL_TOOLS_IMAGE"
    echo ""

    echo "Pushing sandbox-tools image to ACR..."
    docker push "$FULL_TOOLS_IMAGE"
    echo "✓ Image pushed to ACR"
    echo ""
fi

# ---- Redis state store (imported from upstream, not built) ----
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "redis" ]]; then
    echo "Importing Redis image into ACR (${REDIS_SOURCE_IMAGE})..."
    az acr import \
        --name "$ACR_NAME" \
        --source "$REDIS_SOURCE_IMAGE" \
        --image "${REDIS_IMAGE_NAME}:${REDIS_IMAGE_TAG}" \
        --force
    echo "✓ Image imported: $FULL_REDIS_IMAGE"
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
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "tools" ]]; then
    az acr repository show \
        --name "$ACR_NAME" \
        --repository "$TOOLS_IMAGE_NAME" \
        --output table
fi
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "redis" ]]; then
    az acr repository show \
        --name "$ACR_NAME" \
        --repository "$REDIS_IMAGE_NAME" \
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
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "tools" ]]; then
    echo "Sandbox-tools:    $FULL_TOOLS_IMAGE"
fi
if [[ "$BUILD_TARGET" == "all" || "$BUILD_TARGET" == "redis" ]]; then
    echo "Redis:            $FULL_REDIS_IMAGE"
fi
echo ""
echo "Usage:"
echo "  $0                # build all images"
echo "  $0 orchestrator   # build orchestrator only"
echo "  $0 sandbox        # build sandbox only (agent baked in)"
echo "  $0 agent-injector # build agent-injector init container only"
echo "  $0 tools          # build sandbox-tools (agent CLIs) only"
echo "  $0 redis          # import redis state-store image only"
echo ""
echo "Next step: Deploy to Kubernetes"
echo "  ./scripts/deploy_k8s.sh"
echo "============================================"
