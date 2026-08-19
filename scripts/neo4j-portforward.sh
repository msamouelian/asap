#!/usr/bin/env bash
# Port-forward the Neo4j Bolt port for local access.
#
# Needed when running the asapextractor locally (NEO4J_URI=bolt://127.0.0.1:7687)
# or connecting from the Neo4j Browser at http://neo4j.localhost. The graphdb
# service is ClusterIP-only — NodePort was removed because k3s conntrack
# drops idle connections during long extraction phases.
#
# Usage:
#   ./scripts/neo4j-portforward.sh          # foreground, Ctrl-C to stop
#   ./scripts/neo4j-portforward.sh --detach # background (PID written to .neo4j-portforward.pid)

set -euo pipefail

NAMESPACE="asap"
DETACH=false
PID_FILE="$(cd "$(dirname "$0")/.." && pwd)/.neo4j-portforward.pid"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --detach|-d) DETACH=true; shift ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# Stop an existing forward from a previous run
if [[ -f "$PID_FILE" ]]; then
  echo "Stopping existing Neo4j port-forward..."
  kill "$(cat "$PID_FILE")" 2>/dev/null || true
  rm -f "$PID_FILE"
fi

echo "▶ Forwarding localhost:7687 → graphdb:7687 (Bolt)..."

if [[ "$DETACH" == true ]]; then
  kubectl port-forward -n "$NAMESPACE" svc/graphdb 7687:7687 \
    --address 127.0.0.1 > /dev/null 2>&1 &
  echo $! > "$PID_FILE"
  echo "✓ Running in background (PID $(cat "$PID_FILE"))."
  echo "  Stop with: kill \$(cat $PID_FILE)"
else
  echo "✓ Press Ctrl-C to stop."
  kubectl port-forward -n "$NAMESPACE" svc/graphdb 7687:7687 --address 127.0.0.1
fi
