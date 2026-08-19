FROM python:3.12-slim

# Non-root user — uid 1000 is safe for most production clusters.
RUN useradd --uid 1000 --create-home --shell /bin/bash asap

WORKDIR /app

# Patch OS-level CVEs via Debian's security channel, then upgrade pip/wheel.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# Copy only the kggenerator package tree. Build context must be the
# workspace root so this path resolves (see scripts/docker-build-push.sh).
COPY packages/kggenerator packages/kggenerator/

RUN pip install --no-cache-dir "pip>=26.1" wheel
RUN pip install --no-cache-dir -e packages/kggenerator/

USER asap

# Reads the extracted graph from Neo4j (never ArchivesSpace) and writes only
# :Inferred nodes plus Postgres kg_artifact/kg_resolution rows.
ENTRYPOINT ["python", "-m", "kggenerator.main"]
CMD ["all"]
