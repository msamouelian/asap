"""Check whether the ordered_records endpoint returns the missing AO 2646236.

Also reports total count and whether any URIs are missing compared to child_count.
"""

from asapextractor.aspace_client import ASpaceClient
from asapextractor import config

RESOURCE_ID  = "8400"
MISSING_URI  = "/repositories/11/archival_objects/2646236"

client = ASpaceClient()

endpoint = f"repositories/{config.ASPACE_REPO_ID}/resources/{RESOURCE_ID}/ordered_records"
print(f"Fetching: {endpoint}")

data = client.call_endpoint(endpoint)

uris = data.get("uris") or []
total = len(uris)

print(f"Total URIs returned:  {total}")

# Separate resource URI from archival object URIs
ao_uris   = [u["ref"] for u in uris if "/archival_objects/" in u["ref"]]
other_uris = [u["ref"] for u in uris if "/archival_objects/" not in u["ref"]]

print(f"Archival object URIs: {len(ao_uris)}")
print(f"Other URIs:           {len(other_uris)} {other_uris}")

# Check for the missing AO
found = MISSING_URI in ao_uris
print(f"\nMissing AO {MISSING_URI} present: {found}")
if found:
    idx = ao_uris.index(MISSING_URI)
    print(f"  Position in AO list: {idx}")
