"""Configuration loaded from environment variables / .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the workspace root (5 levels up from src/kggenerator/config.py)
_env_path = Path(__file__).parents[4] / ".env"
load_dotenv(dotenv_path=_env_path)

# Neo4j connection (read-only use: the extract phase only reads the
# extracted graph; writes to :Inferred nodes happen in the load phase).
NEO4J_URI: str = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.environ.get("NEO4J_PASSWORD", "")

# Postgres artifact store (same database the backend uses).
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL", "postgresql://asap:password@localhost:5432/postgres"
)

# Inference server (OpenAI-compatible; LM Studio in the current deployment).
# In-cluster jobs reach the host's LM Studio via host.k3d.internal.
INFERENCE_BASE_URL: str = os.environ.get(
    "KG_INFERENCE_BASE_URL", "http://localhost:1234/v1"
)
INFERENCE_MODEL: str = os.environ.get("KG_INFERENCE_MODEL", "openai/gpt-oss-20b")
INFERENCE_API_KEY: str = os.environ.get("KG_INFERENCE_API_KEY", "lm-studio")
# Extraction wants repeatability, like every RAG-internal call in ASAP.
INFERENCE_TEMPERATURE: float = float(os.environ.get("KG_INFERENCE_TEMPERATURE", "0.0"))
# Per-request completion cap; 0 omits the parameter (server default).
INFERENCE_MAX_TOKENS: int = int(os.environ.get("KG_INFERENCE_MAX_TOKENS", "0"))
INFERENCE_TIMEOUT_S: float = float(os.environ.get("KG_INFERENCE_TIMEOUT_S", "600"))

# Token budget per extraction chunk (text-unit payload, excluding the system
# prompt). Estimated at ~4 chars/token; small chunks keep JSON adherence high
# on a 20B model.
CHUNK_TOKEN_BUDGET: int = int(os.environ.get("KG_CHUNK_TOKEN_BUDGET", "3000"))
# Single text units longer than this many characters are split on paragraph
# boundaries at surface-build time so every unit fits a chunk.
TEXT_SPLIT_CHAR_LIMIT: int = int(os.environ.get("KG_TEXT_SPLIT_CHAR_LIMIT", "6000"))

# Retries per chunk after a failed parse/validation (the retry feeds the
# validation errors back to the model).
LLM_RETRIES: int = int(os.environ.get("KG_LLM_RETRIES", "2"))

# vLLM embedding server (OpenAI-compatible) — same model as the extractor's
# note-chunk embeddings so inferred entities are comparable in vector search.
VLLM_BASE_URL: str = os.environ.get("VLLM_BASE_URL", "http://vllm-embedding:8000/v1")
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
EMBEDDING_DIMENSIONS: int = 384  # bge-small-en-v1.5; must match vector indexes
EMBEDDING_BATCH_SIZE: int = int(os.environ.get("EMBEDDING_BATCH_SIZE", "64"))

# Pilot collections (hard-coded for initial development; matched
# case-insensitively against Collection.title). Override with
# KG_COLLECTION_TITLES as a '||'-separated list.
_titles_env = os.environ.get("KG_COLLECTION_TITLES", "").strip()
PILOT_COLLECTION_TITLES: list[str] = (
    [t.strip() for t in _titles_env.split("||") if t.strip()]
    if _titles_env
    else [
        # Exact Collection.title values verified against the graph 2026-08-09.
        "Penn Central Transportation Corporation records",
        "New York, New Haven, and Hartford Railroad Company records",
        "Boston and Albany Railroad Company records",
        "Boston and Albany Railroad Co. photograph album",
        "Ware River Railroad record book",
        "Boston and Lowell Railroad, Woburn Branch, Woburn Massachusetts records",
        "Boston & Maine Railroad Malden Station records",
        "Connecticut and Passumpsic Rivers Railroad Company freight book",
        "New York Railroads records",
        "Hudson River Railroad Records",
        "William Badger Lawrence papers relating to the proposed takeover of the Boston and Maine Railroad by the New York, New Haven, and Hartford Railroad Company",
        "Old Colony Railroad Company records",
        "Deed books for Western Rail Road Corporation",
        "Boston and Providence Railroad Corporation records"
    ]
)
