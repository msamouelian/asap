"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

# uvicorn only configures its own loggers; without a root handler,
# application INFO logs (e.g. RAG gate telemetry) are silently dropped.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("neo4j").setLevel(logging.WARNING)

from asapbackend.config import settings
from asapbackend.database import close_pool, open_pool
from asapbackend.mcp.client import refresh_graph_context, refresh_tools
from asapbackend.routers import (
    chat,
    search,
    conversations,
    extractor,
    documents,
    kgjob,
    system_prompts,
    tags,
    user_prompts,
    users,
)

_METADATA_QUERY = (
    Path(__file__).parent / "prompts" / "metadata_query.cypher"
).read_text(encoding="utf-8").strip()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    await refresh_tools()
    await refresh_graph_context(_METADATA_QUERY)
    yield
    await close_pool()


app = FastAPI(title="ASAP Backend", version="0.1.0", lifespan=lifespan)


# ── OIDC config endpoint (unauthenticated — used by SPA before login) ─────────
@app.get("/auth/config", tags=["auth"])
async def get_auth_config():
    """Return the public Keycloak URL and client config for the SPA PKCE flow."""
    return {
        "keycloak_url": settings.keycloak_public_url,
        "realm": settings.keycloak_realm,
        "client_id": settings.keycloak_client_id,
    }


# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(chat.router)
app.include_router(search.router)
app.include_router(users.router)
app.include_router(conversations.router)
app.include_router(user_prompts.router)
app.include_router(system_prompts.router)
app.include_router(documents.router)
app.include_router(tags.router)
app.include_router(extractor.router)
app.include_router(kgjob.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
