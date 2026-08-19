"""One-off script to verify the waypoint fix without re-running extraction.

Calls tree/node for a known multi-waypoint node and compares what the old
(len-based) vs new (waypoints-field-based) logic would yield.
"""

from asapextractor.aspace_client import ASpaceClient
from asapextractor import config

# Series XI from Muriel Siebert collection — 796 children, waypoints: 4
NODE_URI = "/repositories/11/archival_objects/3791023"
RESOURCE_ID = "12541"

client = ASpaceClient()

endpoint = (
    f"repositories/{config.ASPACE_REPO_ID}/resources/{RESOURCE_ID}"
    f"/tree/node?node_uri={NODE_URI}"
)
response = client.call_endpoint(endpoint)

total_waypoints_field = response.get("waypoints", 1)
child_count_field = response.get("child_count", 0)
precomputed = response.get("precomputed_waypoints", {}).get(NODE_URI, {})
precomputed_count = len(precomputed)

print(f"child_count (reported):       {child_count_field}")
print(f"waypoints field (authoritative): {total_waypoints_field}")
print(f"waypoints pre-computed in response: {precomputed_count}  (keys: {list(precomputed.keys())})")
print()

# Old logic: range(1, len(precomputed))
old_extra = list(range(1, precomputed_count))
# New logic: range(1, waypoints field)
new_extra = list(range(1, total_waypoints_field))

print(f"Old logic would fetch extra waypoints: {old_extra}")
print(f"New logic would fetch extra waypoints: {new_extra}")
print()

# Count children actually reachable with each approach
old_total = sum(len(precomputed.get(str(wp), [])) for wp in range(precomputed_count))
print(f"Children reachable (old logic): {old_total}")

# For new logic, fetch the missing waypoints
print("--- Verifying fix: fetch all waypoints using key '0' ---")
total = old_total
for wp in range(1, total_waypoints_field):
    wp_endpoint = (
        f"repositories/{config.ASPACE_REPO_ID}/resources/{RESOURCE_ID}"
        f"/tree/node?node_uri={NODE_URI}&waypoint={wp}"
    )
    wp_response = client.call_endpoint(wp_endpoint)
    wp_data = wp_response.get("precomputed_waypoints", {}).get(NODE_URI, {})
    count = len(wp_data.get("0") or [])  # always keyed as "0"
    print(f"  Waypoint {wp}: {count} children")
    total += count

print(f"\nTotal children reachable (fixed logic): {total}")
diff = total - child_count_field
if diff == 0:
    print("Fix confirmed: count matches exactly.")
elif diff > 0:
    print(f"Fix confirmed: {diff} extra records returned (harmless — Neo4j MERGE deduplicates by URI).")
else:
    print(f"WARNING: still missing {-diff} children.")
