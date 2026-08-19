"""Worker configuration from environment variables (set on the K8s Job)."""

import os

# Identity of the job row this worker owns (set by the backend at spawn time).
JOB_ID: str = os.environ.get("JOB_ID", "")

# Staging directory holding the uploaded files for this job:
# <UPLOAD_ROOT>/<JOB_ID>/... — deleted after processing (originals are not kept).
UPLOAD_ROOT: str = os.environ.get("UPLOAD_ROOT", "/uploads")

# Postgres — job progress + final status live here.
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL", "postgresql://asap:password@postgres:5432/postgres"
)

# Neo4j — document nodes and chunk embeddings are written directly.
NEO4J_URI: str = os.environ.get("NEO4J_URI", "bolt://graphdb:7687")
NEO4J_USER: str = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.environ.get("NEO4J_PASSWORD", "")

# vLLM embedding server — same model as the archival semantic search so
# query-time embeddings (ai.text.embed) are directly comparable.
VLLM_BASE_URL: str = os.environ.get("VLLM_BASE_URL", "http://vllm-embedding:8000/v1")
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_BATCH_SIZE: int = int(os.environ.get("EMBEDDING_BATCH_SIZE", "64"))

# Chunking: docling's HybridChunker is tokenizer-aware; cap below the
# embedding model's 512-token window to leave room for the contextual prefix.
CHUNK_MAX_TOKENS: int = int(os.environ.get("CHUNK_MAX_TOKENS", "384"))

# Parallel docling conversions. Document parsing is CPU-bound; keep modest on
# the shared dev node. On a GPU cluster docling accelerates automatically.
CONVERT_WORKERS: int = int(os.environ.get("CONVERT_WORKERS", "2"))
