FROM python:3.12-slim

# Non-root user — uid 1000 is safe for most production clusters.
RUN useradd --uid 1000 --create-home --shell /bin/bash asap

WORKDIR /app

# Patch OS-level CVEs (e.g. tar) via Debian's security backport channel,
# then upgrade pip/wheel to remediate any PyPI-level CVEs in the bundled versions.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# Copy only the asapextractor package tree. Build context must be the
# workspace root so this path resolves (see scripts/docker-build-push.sh).
COPY packages/asapextractor packages/asapextractor/

RUN pip install --no-cache-dir "pip>=26.1" wheel

# Editable install keeps __file__ anchored in the source tree, which is
# required for the industry CSV path resolution in extractors/resources.py.
RUN pip install --no-cache-dir -e packages/asapextractor/

# Switch to non-root user before running.
USER asap

# ArchivesSpace credentials (~/.asnake.yml) must be mounted as a Kubernetes
# Secret volume at /home/asap/.asnake.yml in the Job spec.

ENTRYPOINT ["python", "-m", "asapextractor.main"]
