#!/usr/bin/env bash
# Delete and recreate the local k3d cluster.
# Compatible with both bash and zsh.
#
# The Mac host directories that back the persistent volumes are created if
# they do not exist. Existing data (Neo4j, Postgres, Ollama model cache)
# is preserved across cluster recreations because it lives on the Mac
# filesystem, not inside the k3d node container.
#
# Usage:
#   ./scripts/recreate-cluster.sh [--cluster-name asap]

set -euo pipefail

CLUSTER_NAME="asap"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cluster-name) CLUSTER_NAME="$2"; shift 2 ;;
    *)
      echo "Error: unknown argument '$1'"
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
K3D_CONFIG="$WORKSPACE_ROOT/k3d-config.yaml"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
if ! command -v k3d > /dev/null 2>&1; then
  echo "Error: 'k3d' is not installed or not on PATH."
  echo "  Install: https://k3d.io/#installation"
  exit 1
fi

if ! command -v kubectl > /dev/null 2>&1; then
  echo "Error: 'kubectl' is not installed or not on PATH."
  exit 1
fi

if ! command -v helm > /dev/null 2>&1; then
  echo "Error: 'helm' is not installed or not on PATH."
  exit 1
fi

if [[ ! -f "$K3D_CONFIG" ]]; then
  echo "Error: k3d config not found at $K3D_CONFIG"
  exit 1
fi

# ---------------------------------------------------------------------------
# Delete existing cluster
# ---------------------------------------------------------------------------
if k3d cluster list 2>/dev/null | grep -q "$CLUSTER_NAME"; then
  echo "▶ Deleting existing k3d cluster '${CLUSTER_NAME}'..."
  k3d cluster delete "$CLUSTER_NAME"
  echo "✓ Cluster deleted."
else
  echo "  No existing k3d cluster named '${CLUSTER_NAME}' found — skipping delete."
fi

# ---------------------------------------------------------------------------
# Create Mac host directories (data is preserved if they already exist)
# ---------------------------------------------------------------------------
echo ""
echo "▶ Creating host data directories (skipped if already present)..."
mkdir -p /Users/msamouelian/neo4j/data
mkdir -p /Users/msamouelian/postgres/data
mkdir -p /Users/msamouelian/ollama/data
mkdir -p /Users/msamouelian/vllm/data
mkdir -p /Users/msamouelian/keycloak/data
echo "✓ Host directories ready."

# ---------------------------------------------------------------------------
# Recreate cluster
# ---------------------------------------------------------------------------
echo ""
echo "▶ Creating k3d cluster '${CLUSTER_NAME}' from ${K3D_CONFIG}..."
k3d cluster create --config "$K3D_CONFIG"
echo "✓ Cluster '${CLUSTER_NAME}' is ready."

# ---------------------------------------------------------------------------
# Restore host.k3d.internal DNS (k3s addon controller wipes the k3d-injected
# NodeHosts entry; the coredns-custom ConfigMap survives rewrites)
# ---------------------------------------------------------------------------
echo ""
echo "▶ Applying coredns-custom ConfigMap (host.k3d.internal fix)..."
kubectl apply -f "$WORKSPACE_ROOT/manifests/coredns-custom.yaml"
kubectl rollout restart deployment/coredns -n kube-system
echo "✓ host.k3d.internal will resolve to the Mac host from inside the cluster."

# ---------------------------------------------------------------------------
# Hostname note
# ---------------------------------------------------------------------------
echo ""
echo "────────────────────────────────────────────────────────────────"
echo "  Ingress hosts use the .localhost TLD, which macOS and modern"
echo "  browsers resolve to 127.0.0.1 natively — no /etc/hosts entries"
echo "  are needed (and unlike .local, no mDNS timeouts)."
echo "────────────────────────────────────────────────────────────────"
echo ""
echo "  Then reinstall the TLS secret (lost on cluster recreation):"
echo ""
echo "    ./scripts/setup-tls.sh"
echo "────────────────────────────────────────────────────────────────"

# ---------------------------------------------------------------------------
# Next steps
# ---------------------------------------------------------------------------
echo ""
echo "Next step — install TLS then helm charts:"
echo ""
echo "  ./scripts/install-charts.sh \\"
echo "    --neo4j-password      '<password>'                     \\"
echo "    --pg-user             '<user>'                         \\"
echo "    --pg-password         '<password>'                     \\"
echo "    --aspace-user         '<username>'                     \\"
echo "    --aspace-password     '<password>'                     \\"
echo "    --inference-api-key   '<key>'                          \\"
echo "    --inference-base-url  'http://host.k3d.internal:1234/v1' \\"
echo "    --ingress-host        asapbackend.localhost                \\"
echo "    --skip-ollama"
echo ""
echo "  Note: host.k3d.internal resolves to your Mac from inside the cluster."
echo "  Replace port 1234 with your LMStudio port."
