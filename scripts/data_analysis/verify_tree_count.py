"""Compare ArchivesSpace tree traversal count vs Neo4j count for one collection.

Traverses the full tree in memory (no writes) and reports any discrepancy.
"""

from asapextractor.aspace_client import ASpaceClient
from asapextractor.neo4j_client import Neo4jClient
from asapextractor import config

# "Joel Dean papers" — largest collection in Neo4j at 5,222 AOs.
# Change COLLECTION_URI to test a different collection.
COLLECTION_URI = None  # auto-detected below


def count_tree(client: ASpaceClient, collection_uri: str) -> tuple[int, int, int]:
    """Return (total_nodes, api_errors, nodes_skipped_no_uri)."""
    resource_id = collection_uri.rstrip("/").split("/")[-1]
    root = client.call_endpoint(
        f"repositories/{config.ASPACE_REPO_ID}/resources/{resource_id}/tree/root"
    )
    total = 0
    errors = 0
    no_uri = 0

    def iter_waypoints(precomputed, key, total_waypoints):
        wp_data = precomputed.get(key, {})
        yield from wp_data.get("0") or []
        for wp in range(1, total_waypoints):
            if key == "":
                ep = (f"repositories/{config.ASPACE_REPO_ID}/resources/{resource_id}"
                      f"/tree/root?waypoint={wp}")
            else:
                ep = (f"repositories/{config.ASPACE_REPO_ID}/resources/{resource_id}"
                      f"/tree/node?node_uri={key}&waypoint={wp}")
            try:
                extra = client.call_endpoint(ep)
                yield from extra.get("precomputed_waypoints", {}).get(key, {}).get("0") or []
            except RuntimeError as e:
                print(f"  WARN waypoint {wp} for {key}: {e}")

    def process_node(node, depth=0):
        nonlocal total, errors, no_uri
        uri = node.get("uri") or node.get("record_uri")
        if not uri:
            no_uri += 1
            return
        total += 1
        if node.get("child_count", 0) > 0:
            ep = (f"repositories/{config.ASPACE_REPO_ID}/resources/{resource_id}"
                  f"/tree/node?node_uri={uri}")
            try:
                data = client.call_endpoint(ep)
                for child in iter_waypoints(
                    data.get("precomputed_waypoints", {}), uri, data.get("waypoints", 1)
                ):
                    process_node(child, depth + 1)
            except RuntimeError as e:
                errors += 1
                print(f"  ERROR fetching {uri}: {e}")

    for child in iter_waypoints(
        root.get("precomputed_waypoints", {}), "", root.get("waypoints", 1)
    ):
        process_node(child)

    return total, errors, no_uri


# --- main ---
neo4j = Neo4jClient()

# Find the collection with the most AOs in Neo4j
with neo4j._driver.session() as session:
    result = session.run("""
        MATCH (c:Collection)-[:HAS_PART*]->(a:ArchivalObject)
        RETURN c.title AS title, c.uri AS uri, count(a) AS ao_count
        ORDER BY ao_count DESC LIMIT 1
    """)
    row = result.single()
    collection_uri = row["uri"]
    neo4j_count = row["ao_count"]
    title = row["title"]

neo4j.close()

print(f"Collection: {title}")
print(f"URI:        {collection_uri}")
print(f"Neo4j AO count (HAS_PART*): {neo4j_count}")
print()
print("Traversing ArchivesSpace tree (read-only)...")

client = ASpaceClient()
tree_count, errors, no_uri = count_tree(client, collection_uri)

print(f"Tree traversal count: {tree_count}")
print(f"API errors:           {errors}")
print(f"Nodes missing URI:    {no_uri}")
print()
diff = tree_count - neo4j_count
if diff == 0:
    print("Counts match exactly.")
elif diff > 0:
    print(f"Tree has {diff} MORE nodes than Neo4j — TreeExtractor is still missing nodes.")
else:
    print(f"Neo4j has {-diff} MORE nodes than tree — unexpected, investigate manually.")
