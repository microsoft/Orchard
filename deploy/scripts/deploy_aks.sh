#!/bin/bash
set -euo pipefail

###############################################################################
# Deploy AKS Infrastructure
# This is a wrapper script that calls the main deployment script
###############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# scripts live at deploy/scripts/, project root is two levels up
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Change to project root
cd "$PROJECT_ROOT"

# Run the infrastructure deployment
bash deploy/azure/deploy_aks.sh "$@"
