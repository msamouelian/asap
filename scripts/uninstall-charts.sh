#!/usr/bin/env bash
# Uninstall all ASAP helm releases from the cluster.
# Compatible with both bash and zsh.
#
# Usage:
#   ./scripts/uninstall-charts.sh [--namespace asap]

set -euo pipefail

NAMESPACE="asap"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace) NAMESPACE="$2"; shift 2 ;;
    *)
      echo "Error: unknown argument '$1'"
      exit 1
      ;;
  esac
done

echo ""
echo "Uninstalling ASAP helm releases from namespace '${NAMESPACE}'..."

# Reverse of install order — application layer first, infrastructure last.
for release in asapui asapbackend kggenerator asapextractor asapdocworker neo4j-mcp vllm postgres neo4j keycloak; do
  if helm status "$release" --namespace "$NAMESPACE" > /dev/null 2>&1; then
    echo "  ▶ Uninstalling ${release}..."
    helm uninstall "$release" --namespace "$NAMESPACE" --wait
    echo "  ✓ ${release} uninstalled."
  else
    echo "  — ${release} not installed, skipping."
  fi
done

echo ""
echo "▶ Removing TLS secret..."
if kubectl get secret asap-tls --namespace "$NAMESPACE" > /dev/null 2>&1; then
  kubectl delete secret asap-tls --namespace "$NAMESPACE"
  echo "  ✓ asap-tls deleted."
else
  echo "  — asap-tls not found, skipping."
fi

echo ""
echo "Done."
