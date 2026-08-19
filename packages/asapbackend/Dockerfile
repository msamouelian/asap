FROM python:3.12-slim

# Non-root user — uid 1000 is safe for most production clusters.
RUN useradd --uid 1000 --create-home --shell /bin/bash asap

WORKDIR /app

# Apply OS-level security patches from Debian's security channel.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# Copy the asapbackend package tree. Build context must be the workspace root.
COPY packages/asapbackend packages/asapbackend/

RUN pip install --no-cache-dir "pip>=26.1" wheel

# Editable install keeps __file__ anchored in the source tree so the bundled
# prompt files (prompts/default_system_prompt.txt, prompts/metadata_query.cypher)
# resolve correctly at runtime.
RUN pip install --no-cache-dir -e packages/asapbackend/

USER asap

ENTRYPOINT ["uvicorn", "asapbackend.main:app", "--host", "0.0.0.0", "--port", "8001"]
