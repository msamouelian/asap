#!/bin/zsh
# Export full hybrid-search results to CSV, bypassing the chat (no LLM in the
# loop — runs the same server-side query the hybrid-search tool executes).
#
# Usage:
#   ./scripts/hybrid-export.sh "organizational charts"            # limit 200
#   ./scripts/hybrid-export.sh "rope making" 100 ~/Desktop/rope.csv
set -euo pipefail

TOPIC="${1:?usage: hybrid-export.sh <topic> [limit] [outfile]}"
LIMIT="${2:-200}"
SLUG=$(echo "$TOPIC" | tr ' ' '-' | tr -cd '[:alnum:]-' | cut -c1-40)
OUT="${3:-$HOME/Desktop/hybrid-${SLUG}.csv}"
ROOT="${0:A:h}/.."

kubectl port-forward -n asap svc/graphdb 7687:7687 > /dev/null 2>&1 &
PF=$!
trap "kill $PF 2>/dev/null" EXIT
until nc -z 127.0.0.1 7687 2>/dev/null; do sleep 1; done

NEO4J_PASSWORD=$(kubectl get secret neo4j-auth -n asap -o jsonpath='{.data.neo4j-password}' | base64 -d)
export NEO4J_URI="bolt://127.0.0.1:7687" NEO4J_USER="neo4j" NEO4J_PASSWORD

TOPIC="$TOPIC" LIMIT="$LIMIT" OUT="$OUT" \
uv run --project "$ROOT/packages/asapbackend" --no-sync python - <<'PYEOF'
import csv, os
from asapbackend.services import graph_docs
from asapbackend.tools import hybrid_search as hs

topic, limit, out = os.environ["TOPIC"], int(os.environ["LIMIT"]), os.environ["OUT"]
with graph_docs._get_driver().session() as s:
    rows = list(s.run(hs.HYBRID_CYPHER, topic=topic,
                      luceneQuery=hs._lucene_query(topic), limit=limit))
with open(out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["rank", "title", "record_type", "score", "match_source",
                "in_collection", "excerpt", "aspace_url"])
    for i, r in enumerate(rows, 1):
        w.writerow([i, r["title"], r["record_type"], r["score"], r["match_source"],
                    r["in_collection"], r["excerpt"], r["aspace_url"]])
print(f"{len(rows)} rows -> {out}")
graph_docs._get_driver().close()
PYEOF
