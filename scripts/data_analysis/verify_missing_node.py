"""Investigate why AO 2646236 is missing from the tree under parent 2637521."""

import json
from asapextractor.aspace_client import ASpaceClient
from asapextractor import config

RESOURCE_ID  = "8400"
PARENT_URI   = "/repositories/11/archival_objects/2637521"
MISSING_URI  = "/repositories/11/archival_objects/2646236"

client = ASpaceClient()


def section(title):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print('=' * 70)


# 1. Fetch the missing AO's full record to confirm it exists and check its parent
section("1. Full record for missing AO 2646236")
try:
    record = client.call_endpoint(f"repositories/{config.ASPACE_REPO_ID}/archival_objects/2646236")
    print(f"  title:      {record.get('title')}")
    print(f"  level:      {record.get('level')}")
    print(f"  suppressed: {record.get('suppressed')}")
    print(f"  parent:     {(record.get('parent') or {}).get('ref')}")
    print(f"  resource:   {(record.get('resource') or {}).get('ref')}")
    print(f"  position:   {record.get('position')}")
except RuntimeError as e:
    print(f"  ERROR: {e}")


# 2. Fetch the parent's full record to check child_count and suppressed state
section("2. Full record for parent AO 2637521")
try:
    parent = client.call_endpoint(f"repositories/{config.ASPACE_REPO_ID}/archival_objects/2637521")
    print(f"  title:      {parent.get('title')}")
    print(f"  level:      {parent.get('level')}")
    print(f"  suppressed: {parent.get('suppressed')}")
    print(f"  children:   {parent.get('children', '(field not present)')}")
except RuntimeError as e:
    print(f"  ERROR: {e}")


# 3. Fetch tree/node for the parent and show ALL children across all waypoints
section("3. tree/node response for parent 2637521")
try:
    data = client.call_endpoint(
        f"repositories/{config.ASPACE_REPO_ID}/resources/{RESOURCE_ID}"
        f"/tree/node?node_uri={PARENT_URI}"
    )
    print(f"  child_count:      {data.get('child_count')}")
    print(f"  waypoints:        {data.get('waypoints')}")
    print(f"  waypoint_size:    {data.get('waypoint_size')}")

    all_children = []
    precomputed = data.get("precomputed_waypoints", {})
    wp_data = precomputed.get(PARENT_URI, {})
    for wp_key, items in wp_data.items():
        all_children.extend(items or [])

    # Fetch additional waypoints if any
    for wp in range(1, data.get("waypoints", 1)):
        ep = (f"repositories/{config.ASPACE_REPO_ID}/resources/{RESOURCE_ID}"
              f"/tree/node?node_uri={PARENT_URI}&waypoint={wp}")
        extra = client.call_endpoint(ep)
        extra_items = (extra.get("precomputed_waypoints") or {}).get(PARENT_URI, {}).get("0") or []
        all_children.extend(extra_items)

    print(f"  children returned: {len(all_children)}")
    found = any(c.get("uri") == MISSING_URI for c in all_children)
    print(f"  missing AO present in tree/node response: {found}")

    print(f"\n  Children listing:")
    for c in all_children:
        marker = " <-- MISSING AO" if c.get("uri") == MISSING_URI else ""
        print(f"    [{c.get('level')}] {c.get('title')}  uri={c.get('uri')}  child_count={c.get('child_count')}{marker}")

except RuntimeError as e:
    print(f"  ERROR: {e}")
