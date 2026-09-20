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

# Knowledge-graph LLM (OpenAI-compatible). ONE model serves extraction and
# adjudication. No defaults: the kggenerator chart supplies these from
# install-charts.sh --kg-inference-*; KGExtractor fails loudly if unset.
INFERENCE_BASE_URL: str = os.environ.get("KG_INFERENCE_BASE_URL", "")
INFERENCE_MODEL: str = os.environ.get("KG_INFERENCE_MODEL", "")
INFERENCE_API_KEY: str = os.environ.get("KG_INFERENCE_API_KEY", "")
# Extraction wants repeatability, like every RAG-internal call in ASAP.
# -1 OMITS the parameter: OpenAI gpt-5-class models reject any non-default
# temperature (every adjudication 400'd with 0.0 on 2026-09-20).
INFERENCE_TEMPERATURE: float = float(os.environ.get("KG_INFERENCE_TEMPERATURE", "0.0"))
# Reasoning effort; "" omits (LM Studio / gpt-oss). A configured value is
# also this job's marker for gpt-5-class parameter rules (see _chat).
INFERENCE_REASONING_EFFORT: str = os.environ.get("KG_INFERENCE_REASONING_EFFORT", "")
# Per-request completion cap; 0 omits the parameter (server default). Sent
# as max_tokens, or max_completion_tokens when a reasoning effort is set.
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
EMBEDDING_BASE_URL: str = os.environ.get("EMBEDDING_BASE_URL", "http://vllm-embedding:8000/v1")
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
# Must match the extractor's vector indexes (same --embedding-dimensions flag).
EMBEDDING_DIMENSIONS: int = int(os.environ.get("EMBEDDING_DIMENSIONS", "384"))
# Bearer token for the embedding endpoint; empty for the in-cluster vLLM.
EMBEDDING_API_KEY: str = os.environ.get("EMBEDDING_API_KEY", "")
EMBEDDING_BATCH_SIZE: int = int(os.environ.get("EMBEDDING_BATCH_SIZE", "64"))

# Pilot collections, identified by ead_id — the durable, uniqueness-
# constrained identifier. Titles proved fragile (renames during archival
# editing, HTML-entity encoding drift aborted a run 2026-08-22); ead_ids
# survive description work. Titles in comments are for humans only.
# Override with KG_COLLECTION_EAD_IDS as a '||'-separated list.
_ead_ids_env = os.environ.get("KG_COLLECTION_EAD_IDS", "").strip()
PILOT_COLLECTION_EAD_IDS: list[str] = (
    [t.strip() for t in _ead_ids_env.split("||") if t.strip()]
    if _ead_ids_env
    else [
        "bak00358",  # Penn Central Transportation Corporation records
        "bak00857",  # New York, New Haven, and Hartford Railroad Company records
        "bak00034",  # Boston and Albany Railroad Company records
        "bak00541",  # Boston and Albany Railroad Company photograph album
        "bak00750",  # Ware River Railroad record book
        "bak01476",  # Boston and Lowell Railroad, Woburn Branch records
        "bak01442",  # Boston & Maine Railroad Malden Station records
        "bak01952",  # Connecticut and Passumpsic Rivers Railroad freight book
        "bak01448",  # New York Railroads records
        "bak01446",  # Hudson River Railroad records
        "bak00083",  # William Badger Lawrence papers (B&M takeover)
        "bak00672",  # Old Colony Railroad Company records
        "bak00357",  # Deed books for Western Rail Road Corporation
        "bak00367",  # Boston and Providence Railroad Corporation records
        "bak02171",  # Business History Foundation, Inc. Records
    ]
)
