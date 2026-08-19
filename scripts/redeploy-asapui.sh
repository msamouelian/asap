#!/bin/zsh
# Build, push, and redeploy the asapui image to the local k3d cluster.
# Intended for fast iteration during UI development.
#
# Usage:
#   ./scripts/redeploy-asapui.sh
#
# Environment variables:
#   DOCKER_USERNAME   Docker Hub username (required)
#   TAG               Image tag (default: latest)
#   NAMESPACE         Kubernetes namespace (default: asap)

set -euo pipefail

DOCKER_USERNAME="${DOCKER_USERNAME:-}"
TAG="${TAG:-latest}"
NAMESPACE="${NAMESPACE:-asap}"

if [[ -z "$DOCKER_USERNAME" ]]; then
  echo "Error: DOCKER_USERNAME is not set."
  echo "  export DOCKER_USERNAME=your-dockerhub-username"
  exit 1
fi

WORKSPACE_ROOT="${0:A:h}/.."
cd "$WORKSPACE_ROOT"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Rebuilding and redeploying asapui"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "▶ Building and pushing ${DOCKER_USERNAME}/asapui:${TAG}..."
./scripts/docker-build-push.sh asapui

echo ""
echo "▶ Rolling out new image in namespace '${NAMESPACE}'..."
kubectl rollout restart deployment/asapui -n "$NAMESPACE"

echo ""
echo "▶ Waiting for rollout to complete..."
kubectl rollout status deployment/asapui -n "$NAMESPACE" --timeout=120s

echo ""
echo "✓ Done. Open http://asapui.localhost"
