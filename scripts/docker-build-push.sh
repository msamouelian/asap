#!/bin/zsh
# Build and push Docker images for ASAP services.
#
# Usage:
#   ./scripts/docker-build-push.sh                  # build + push all services
#   ./scripts/docker-build-push.sh asapextractor    # build + push one service
#
# Environment variables:
#   DOCKER_USERNAME   Your Docker Hub username (required)
#   TAG               Image tag (default: latest)
#   PLATFORMS         Comma-separated target platforms
#                     (default: linux/amd64,linux/arm64).
#
# Images are multi-arch by default. The k3d node on an Apple-silicon Mac is
# arm64; amd64-only images run there under emulation, which made every
# torch/docling step in asapdocworker (and plain Python in the other
# services) several times slower than native. Kubernetes picks the matching
# platform from the manifest list automatically, so nothing in the charts
# changes. Set PLATFORMS=linux/arm64 for a faster local-only build.
#
# Requires a builder that can push manifest lists: Docker Desktop with the
# containerd image store (the default since 4.34) or a docker-container
# builder (`docker buildx create --driver docker-container --use`).

set -euo pipefail

DOCKER_USERNAME="${DOCKER_USERNAME:-}"
TAG="${TAG:-latest}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

if [[ -z "$DOCKER_USERNAME" ]]; then
  echo "Error: DOCKER_USERNAME is not set."
  echo "  export DOCKER_USERNAME=your-dockerhub-username"
  exit 1
fi

# Resolve workspace root regardless of where the script is called from.
WORKSPACE_ROOT="${0:A:h}/.."
cd "$WORKSPACE_ROOT"

build_and_push() {
  local service="$1"
  local dockerfile="$2"
  local image="${DOCKER_USERNAME}/${service}:${TAG}"

  echo ""
  echo "▶ Building ${image} for ${PLATFORMS} ..."
  # --provenance=false avoids the OCI attestation manifest that older
  # containerd versions cannot resolve to a platform.
  docker buildx build \
    --file "$dockerfile" \
    --tag "$image" \
    --platform "$PLATFORMS" \
    --provenance=false \
    --push \
    .

  echo "✓ ${image} pushed."
}

# ----- services -----
declare -A SERVICES
SERVICES[asapextractor]="packages/asapextractor/Dockerfile"
SERVICES[asapbackend]="packages/asapbackend/Dockerfile"
SERVICES[asapui]="packages/asapui/Dockerfile"
SERVICES[asapdocworker]="packages/asapdocworker/Dockerfile"
SERVICES[kggenerator]="packages/kggenerator/Dockerfile"

TARGET="${1:-}"

if [[ -n "$TARGET" ]]; then
  if [[ -z "${SERVICES[$TARGET]:-}" ]]; then
    echo "Error: unknown service '${TARGET}'. Known services: ${(k)SERVICES}"
    exit 1
  fi
  build_and_push "$TARGET" "${SERVICES[$TARGET]}"
else
  for service dockerfile in "${(@kv)SERVICES}"; do
    build_and_push "$service" "$dockerfile"
  done
fi

echo ""
echo "All done."
