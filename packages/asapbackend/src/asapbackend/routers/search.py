"""Direct hybrid search — the LLM-free path to the same server-side query
the chat tool runs. Serves the UI's Hybrid Search screen: staff type a
topic and get the full ranked result set as JSON, with no model in the
loop (no routing variance, no table transcription, no context limits)."""

import psycopg
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from asapbackend.auth.dependencies import CurrentUser
from asapbackend.database import get_db
from asapbackend.tools import hybrid_search

router = APIRouter(prefix="/search", tags=["search"])

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]

# The UI may request more rows than the chat tool's cap — results go to a
# paged table, not into model context.
MAX_UI_LIMIT = 500


class HybridSearchRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=MAX_UI_LIMIT, ge=1, le=MAX_UI_LIMIT)


@router.post("/hybrid")
async def hybrid(req: HybridSearchRequest, user: CurrentUser, conn: DBConn):
    """Run the server-side hybrid query and return ranked rows."""
    rows = await hybrid_search.run_rows(req.topic.strip(), req.limit)
    return {
        "topic": req.topic.strip(),
        "count": len(rows),
        "results": [
            {
                "rank": i + 1,
                "title": r.get("title"),
                "record_type": r.get("record_type"),
                "score": r.get("score"),
                "match_source": r.get("match_source"),
                "in_collection": r.get("in_collection"),
                "in_collection_url": r.get("in_collection_url"),
                "excerpt": r.get("excerpt"),
                "aspace_url": r.get("aspace_url"),
            }
            for i, r in enumerate(rows)
        ],
    }
