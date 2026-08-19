#!/usr/bin/env bash
# Start port-forwards for services that are not reachable via the Traefik ingress.
#
# With k3d + Traefik, asapbackend and asapui are reachable at their ingress
# hostnames (asapbackend.localhost, asapui.localhost) on port 80 — no port-forward needed.
#
# This script is only needed when running asapbackend LOCALLY (outside the
# cluster) for hot-reload development. In that case the backend needs to reach
# graphdb-mcp and postgres via localhost.
#
# Forwards (local → in-cluster service):
#
#   localhost:8001  →  asapbackend:8001
#     Needed when running the UI locally (npm run dev). The Vite proxy
#     forwards /api/* to localhost:8001. In production the UI reaches the
#     backend through the standard Kubernetes Ingress instead.
#
#   localhost:8080  →  graphdb-mcp:8080
#     Only needed when running asapbackend LOCALLY (outside the cluster),
#     because .env sets NEO4J_MCP_URL=http://localhost:8080. When the
#     backend runs inside the cluster it uses ClusterIP DNS (graphdb-mcp:8080)
#     and this forward is not needed.
#
#   localhost:5432  →  postgres:5432
#     Optional — for DB inspection tools (TablePlus, psql, etc.).
#     Not needed by any application component.
#
# Neo4j browser (7474) and Bolt (7687) are already exposed via the k3d
# node port mappings in k3d-config.yaml and do NOT need port-forwards.
#
# Usage:
#   ./scripts/dev-portforward.sh          # foreground, Ctrl-C to stop all
#   ./scripts/dev-portforward.sh --detach # background (PIDs written to .portforward.pids)

set -euo pipefail

NAMESPACE="asap"
DETACH=false
PID_FILE="$(cd "$(dirname "$0")/.." && pwd)/.portforward.pids"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --detach|-d) DETACH=true; shift ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────

pf() {
  local label="$1" local_port="$2" svc="$3" remote_port="$4"
  echo "  ▶ $label  localhost:$local_port → $svc:$remote_port"
  if [[ "$DETACH" == true ]]; then
    kubectl port-forward -n "$NAMESPACE" "svc/$svc" "$local_port:$remote_port" \
      --address 127.0.0.1 > /dev/null 2>&1 &
    echo $! >> "$PID_FILE"
  else
    kubectl port-forward -n "$NAMESPACE" "svc/$svc" "$local_port:$remote_port" \
      --address 127.0.0.1 &
  fi
}

stop_existing() {
  if [[ -f "$PID_FILE" ]]; then
    echo "Stopping existing port-forwards..."
    while read -r pid; do
      kill "$pid" 2>/dev/null || true
    done < "$PID_FILE"
    rm -f "$PID_FILE"
  fi
}

# ── Main ─────────────────────────────────────────────────────────────────────

stop_existing

echo ""
echo "Starting port-forwards for namespace '$NAMESPACE'..."
echo ""

pf "asapbackend" 8001 "asapbackend"  8001
pf "graphdb-mcp"  8080 "graphdb-mcp"  8080
pf "postgres"     5432 "postgres"     5432

if [[ "$DETACH" == true ]]; then
  echo ""
  echo "✓ Port-forwards running in background."
  echo "  PIDs saved to: $PID_FILE"
  echo "  Stop with: kill \$(cat $PID_FILE)"
else
  echo ""
  echo "✓ Port-forwards active. Press Ctrl-C to stop all."
  wait
fi
