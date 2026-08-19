"""Print the full resource tree for /repositories/11/resources/8400.

Indents each node by its depth to visualize nesting.
Prints a running total at the end.
"""

import sys
from asapextractor.aspace_client import ASpaceClient
from asapextractor import config

RESOURCE_URI = "/repositories/11/resources/8400"
RESOURCE_ID  = RESOURCE_URI.split("/")[-1]

client = ASpaceClient()
total = 0


def iter_waypoints(precomputed, key, total_waypoints):
    yield from (precomputed.get(key) or {}).get("0") or []
    for wp in range(1, total_waypoints):
        if key == "":
            ep = (f"repositories/{config.ASPACE_REPO_ID}/resources/{RESOURCE_ID}"
                  f"/tree/root?waypoint={wp}")
        else:
            ep = (f"repositories/{config.ASPACE_REPO_ID}/resources/{RESOURCE_ID}"
                  f"/tree/node?node_uri={key}&waypoint={wp}")
        try:
            extra = client.call_endpoint(ep)
            yield from (extra.get("precomputed_waypoints") or {}).get(key, {}).get("0") or []
        except RuntimeError as e:
            print(f"  WARN waypoint {wp} for {key}: {e}", file=sys.stderr)


def print_node(node, depth=0):
    global total
    uri   = node.get("uri") or node.get("record_uri", "")
    title = node.get("title") or "(no title)"
    level = node.get("level") or "?"
    ident = node.get("identifier") or ""
    prefix = "  " * depth
    label  = f"[{level}] {title}"
    if ident:
        label += f"  ({ident})"
    print(f"{prefix}{label}")
    print(f"{prefix}  uri: {uri}  child_count: {node.get('child_count', 0)}")
    total += 1

    if node.get("child_count", 0) > 0:
        ep = (f"repositories/{config.ASPACE_REPO_ID}/resources/{RESOURCE_ID}"
              f"/tree/node?node_uri={uri}")
        try:
            data = client.call_endpoint(ep)
            for child in iter_waypoints(
                data.get("precomputed_waypoints", {}), uri, data.get("waypoints", 1)
            ):
                print_node(child, depth + 1)
        except RuntimeError as e:
            print(f"{prefix}  ERROR: {e}", file=sys.stderr)


# Fetch root
root = client.call_endpoint(
    f"repositories/{config.ASPACE_REPO_ID}/resources/{RESOURCE_ID}/tree/root"
)
print(f"Resource: {root.get('title')}")
print(f"URI: {RESOURCE_URI}")
print(f"Reported child_count: {root.get('child_count', 0)}")
print("=" * 80)

for child in iter_waypoints(
    root.get("precomputed_waypoints", {}), "", root.get("waypoints", 1)
):
    print_node(child, depth=1)

print("=" * 80)
print(f"Total archival objects counted: {total}")
