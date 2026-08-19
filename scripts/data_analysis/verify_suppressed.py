"""Check whether suppressed archival objects account for the count discrepancy.

The tree endpoint may silently exclude suppressed records while the bulk
archival_objects endpoint counts them in its total.
"""

from asapextractor.aspace_client import ASpaceClient
from asapextractor import config

client = ASpaceClient()

# Fetch first two pages from the bulk endpoint and tally suppressed vs unsuppressed
endpoint = f"repositories/{config.ASPACE_REPO_ID}/archival_objects"
total_reported = None
suppressed = 0
unsuppressed = 0
sample_pages = 4  # sample 1000 records (4 x 250)

for page in range(1, sample_pages + 1):
    data = client.call_endpoint(endpoint, params={"page": page, "page_size": 250})
    if total_reported is None:
        total_reported = data.get("total")
        last_page = data.get("last_page")
        print(f"Total AOs reported by bulk endpoint: {total_reported}")
        print(f"Total pages: {last_page}")
        print()

    for record in data.get("results", []):
        if record.get("suppressed"):
            suppressed += 1
        else:
            unsuppressed += 1

sampled = suppressed + unsuppressed
print(f"Sample size: {sampled} records (pages 1-{sample_pages})")
print(f"  Suppressed:   {suppressed}  ({100*suppressed/sampled:.1f}%)")
print(f"  Unsuppressed: {unsuppressed}  ({100*unsuppressed/sampled:.1f}%)")
print()
if suppressed > 0:
    estimated_suppressed = int(total_reported * suppressed / sampled)
    estimated_unsuppressed = total_reported - estimated_suppressed
    print(f"Extrapolated across all {total_reported}:")
    print(f"  ~{estimated_suppressed} suppressed")
    print(f"  ~{estimated_unsuppressed} unsuppressed")
    print()
    print("If the tree endpoint excludes suppressed records, the expected")
    print(f"tree count would be ~{estimated_unsuppressed} — compare to 253,866 in Neo4j.")
else:
    print("No suppressed records found in sample — suppression is not the cause.")
