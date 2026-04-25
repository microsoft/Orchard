#!/bin/bash
set -euo pipefail

###############################################################################
# Deploy AKS Infrastructure
# This is a wrapper script that calls the main deployment script
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Change to project root
cd "$PROJECT_ROOT"

# Run the infrastructure deployment
bash infra/deploy_aks.sh "$@"
