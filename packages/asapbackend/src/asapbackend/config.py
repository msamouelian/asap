"""Settings loaded from environment variables / .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # -------------------------------------------------------------------------
    # PostgreSQL
    # -------------------------------------------------------------------------
    database_url: str = "postgresql://asap:password@postgres:5432/postgres"

    # -------------------------------------------------------------------------
    # Keycloak / OIDC
    # keycloak_url: internal cluster service URL used for JWKS fetching.
    # keycloak_public_url: browser-accessible URL returned to the SPA via /auth/config.
    # -------------------------------------------------------------------------
    keycloak_url: str = "http://keycloak"
    keycloak_public_url: str = "http://keycloak.localhost"
    keycloak_realm: str = "asap"
    keycloak_client_id: str = "asapui"
    oidc_role_claim: str = "asap_roles"

    @property
    def oidc_jwks_url(self) -> str:
        return f"{self.keycloak_url}/realms/{self.keycloak_realm}/protocol/openid-connect/certs"

    # -------------------------------------------------------------------------
    # Inference server (OpenAI-compatible)
    # -------------------------------------------------------------------------
    # No defaults on purpose: the asapbackend chart supplies these
    # (INFERENCE_* from asapbackend-inference-config, the key from
    # asapbackend-secret) and get_llm_client() fails loudly if any is
    # missing. ONE client serves the chat agent and the RAG-internal calls
    # (query distillation, relevance filter).
    inference_base_url: str = ""
    inference_model: str = ""
    inference_api_key: str = ""

    # -------------------------------------------------------------------------
    # Neo4j MCP server
    # In HTTP transport mode, graphdb-mcp requires per-request Basic Auth
    # using the Neo4j database credentials.
    # -------------------------------------------------------------------------
    neo4j_mcp_url: str = "http://graphdb-mcp:8080"
    neo4j_mcp_auth_user: str = ""
    neo4j_mcp_auth_password: str = ""

    # Direct Neo4j driver access — used ONLY by services/graph_docs.py for
    # document-library writes (archive/delete). LLM-generated Cypher stays on
    # the read-only MCP path above.
    neo4j_uri: str = "bolt://graphdb:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # -------------------------------------------------------------------------
    # RAG (document collections)
    # embedding_base_url: embedding server, same model as ingestion so query and
    # chunk vectors are comparable. Gate: inject when top cosine >= the
    # semantic floor OR top lexical >= the lexical floor. Floors calibrated
    # empirically (2026-07) across the test corpora: on-topic queries scored
    # >= 0.847 cosine / >= 5.22 lexical, off-topic <= 0.809 / <= 4.21. A
    # delta-over-background gate was tried first and abandoned — nonsense
    # queries produce LARGER top-minus-background deltas than real ones.
    # Every retrieval logs its score curve; recalibrate from those logs.
    # -------------------------------------------------------------------------
    rag_enabled: bool = True
    embedding_base_url: str = "http://vllm-embedding:8000/v1"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Bearer token for the embedding endpoint (EMBEDDING_API_KEY, from
    # asapbackend-secret). Empty for the in-cluster vLLM; required for hosted
    # APIs. Also passed to Neo4j's GenAI plugin for hybrid-search query
    # embedding, and its presence switches off vLLM-only request parameters.
    embedding_api_key: str = ""
    rag_semantic_candidates: int = 30
    rag_max_chunks: int = 8
    rag_semantic_floor: float = 0.82
    rag_lexical_floor: float = 5.0
    # Neighbor expansion: pull this many adjacent chunks (by seq) on each
    # side of every surviving hit, so a match on a section opening carries
    # the rules that follow it. 0 disables.
    rag_neighbor_window: int = 1
    # Distill the conversational question into a focused search query with
    # the chat LLM before embedding — strips framing like collection names
    # that dilute the embedding against the document corpus.
    rag_query_distillation: bool = True
    # One batched LLM call that drops gate survivors whose content doesn't
    # actually help the question (changelogs, prefaces, standards
    # comparisons) — the bi-encoder can't make that distinction.
    rag_relevance_filter: bool = True
    # Section growth: extend each passage to cover its whole section. With a
    # detected section number ('2.7.x'), grow while adjacent chunks keep
    # referencing it, tolerating grow_gap non-referencing chunks (examples);
    # otherwise grow while the query cosine clears grow_floor.
    rag_grow_floor: float = 0.80
    rag_grow_gap: int = 2
    rag_grow_max_forward: int = 30
    rag_grow_max_backward: int = 3
    rag_total_max_chunks: int = 40

    # Default sampling temperature for the conversational agent. Low by
    # design: the agent's output is Cypher, tool selections, and figures,
    # which want repeatability (routing measured 1/4 correct at ~0.8 vs 4/4
    # after fixes; see docs). Users can raise it per message from the UI
    # (Precise 0.2 / Balanced 0.5 / Exploratory 0.8). RAG-internal calls
    # (distillation, relevance filter) stay pinned at 0.0 regardless.
    # Set to -1 to OMIT the temperature parameter from every request —
    # required for OpenAI reasoning-class models, which reject it. This
    # overrides per-message UI values and the RAG-internal 0.0 pins alike.
    inference_temperature: float = 0.2
    # Min-p truncation for agent completions: keep only tokens whose
    # probability is >= min_p x the top token's. Self-scales with model
    # confidence, so one constant serves all temperature stops — strict
    # mid-Cypher, permissive in prose. Not an official OpenAI param; sent
    # via extra_body, honored by llama.cpp/LM Studio and vLLM. 0 disables.
    inference_min_p: float = 0.05
    # Reasoning effort sent with every completion request. Empty = omit the
    # parameter (correct for LM Studio / gpt-oss, which manage their own
    # reasoning). OpenAI gpt-5-class models REQUIRE 'none' here to use
    # function tools on /v1/chat/completions — their server-side default
    # enables reasoning, which that endpoint cannot combine with tools.
    inference_reasoning_effort: str = ""

    # Safety ceiling for the chat UI's "run full query" button (direct
    # read-cypher execution with LIMIT clauses stripped). The response is
    # truncated to this many rows and flagged, so a broad query cannot ship
    # an unbounded payload to the browser.
    cypher_run_max_rows: int = 50000

    # Total effective context window in tokens for the configured model +
    # hardware. Used to compute context-usage percentage shown in the UI.
    # Set in values.yaml (inferenceContextWindowTokens) and override with
    # --set at helm install time when changing models or hardware.
    inference_context_window_tokens: int = 131072

    # -------------------------------------------------------------------------
    # Reasoning tokens
    # When true, captured reasoning is prepended (wrapped in <think> tags) to
    # the assistant message when rebuilding conversation history for re-submission.
    # Appropriate for OSS reasoning models (DeepSeek R1, Qwen QwQ, gpt-oss-20b).
    # Set to false for o-series models that regenerate reasoning from scratch.
    # -------------------------------------------------------------------------
    reasoning_include_in_context: bool = True

    # -------------------------------------------------------------------------
    # Kubernetes (for Job triggering)
    # -------------------------------------------------------------------------
    k8s_namespace: str = "asap"
    extractor_job_name: str = "asapextractor"
    kggenerator_job_name: str = "kggenerator"

    # Document ingestion: suspended template Job cloned per upload, and the
    # shared staging volume where uploads await the worker.
    docworker_job_name: str = "asapdocworker"
    upload_root: str = "/uploads"


settings = Settings()
