"""Configuration loaded from environment variables / .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the workspace root (5 levels up from src/asapextractor/config.py)
_env_path = Path(__file__).parents[4] / ".env"
load_dotenv(dotenv_path=_env_path)

# Neo4j connection
NEO4J_URI: str = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.environ.get("NEO4J_PASSWORD", "")

# ArchivesSpace extraction settings
ASPACE_BASE_URL: str = "https://arstaff.lib.harvard.edu/api"
# Staff-UI base URL used to build aspace_url link properties on nodes.
ASPACE_PUBLIC_URL: str = os.environ.get("ASPACE_PUBLIC_URL", "https://arstaff.lib.harvard.edu")
ASPACE_REPO_ID: int = int(os.environ.get("ASPACE_REPO_ID", "11"))
ASPACE_PAGE_SIZE: int = int(os.environ.get("ASPACE_PAGE_SIZE", "100"))

# Wipe the entire Neo4j graph before extraction begins. Default true — every
# run rebuilds from a clean graph so records deleted in ArchivesSpace do not
# linger as stale nodes. Set WIPE_GRAPH=false for partial dev runs.
WIPE_GRAPH: bool = os.environ.get("WIPE_GRAPH", "true").strip().lower() in ("true", "1", "yes")

# vLLM embedding server (OpenAI-compatible) used by the embedding phase.
# The default is the in-cluster service; override for local runs.
VLLM_BASE_URL: str = os.environ.get("VLLM_BASE_URL", "http://vllm-embedding:8000/v1")
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
# bge-small-en-v1.5 output dimensionality; must match the vector indexes.
EMBEDDING_DIMENSIONS: int = 384
# Texts per embedding request. CPU inference: keep moderate.
EMBEDDING_BATCH_SIZE: int = int(os.environ.get("EMBEDDING_BATCH_SIZE", "64"))
