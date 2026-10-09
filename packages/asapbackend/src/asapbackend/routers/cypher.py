"""
Direct Cypher execution for the "run full query" button on read-cypher tool
call boxes in the chat UI. No LLM involvement — the user-cleaned query is
executed verbatim and the tabular result returned for display/CSV export.

Security posture:
  - Dispatches through the SAME MCP read-cypher tool the LLM uses, so the
    MCP server's read-only enforcement is fully inherited.
  - A defence-in-depth keyword check rejects obvious write clauses before
    the query leaves this process.
  - Row count is capped at settings.cypher_run_max_rows (the response says
    when truncation happened) so a LIMIT-stripped broad query cannot ship
    an unbounded payload to the browser.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from asapbackend.auth.dependencies import require_role
from asapbackend.config import settings
from asapbackend.mcp import client as mcp_client
from asapbackend.tools import cypher_guard
from asapbackend.tools.cypher_guard import CypherRejected

router = APIRouter(
    prefix="/cypher",
    tags=["cypher"],
    dependencies=[Depends(require_role("user"))],
)



class CypherRunRequest(BaseModel):
    query: str


class CypherRunResponse(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int          # rows returned (after truncation)
    truncated: bool         # true when the safety ceiling cut the result


@router.post("/run", response_model=CypherRunResponse)
async def run_cypher(body: CypherRunRequest):
    # Same policy as the LLM's read-cypher tool: lexical pre-filter plus
    # planner rules (read-only, no unlabelled scans, allow-listed
    # procedures, document library off limits). The MCP layer's own
    # read-only check remains behind it.
    try:
        query = await cypher_guard.check(body.query)
    except CypherRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    try:
        raw = await mcp_client.call_tool("read-cypher", {"query": query})
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Query execution failed: {exc}")

    # read-cypher returns the result as JSON text; anything unparseable is an
    # error message from Neo4j (syntax error, unknown label, ...).
    try:
        records = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, raw.strip() or "Query failed.")
    if not isinstance(records, list):
        records = [records] if records is not None else []

    cap = settings.cypher_run_max_rows
    truncated = len(records) > cap
    records = records[:cap]

    # Column order: first-seen key order across all records (queries with
    # heterogeneous RETURNs still get a stable union of columns).
    columns: list[str] = []
    for rec in records:
        if isinstance(rec, dict):
            for k in rec:
                if k not in columns:
                    columns.append(k)
    if not columns and records:
        columns = ["value"]  # scalar rows (shouldn't happen with read-cypher)

    rows = [
        [rec.get(c) for c in columns] if isinstance(rec, dict) else [rec]
        for rec in records
    ]

    return CypherRunResponse(
        columns=columns, rows=rows, row_count=len(rows), truncated=truncated,
    )
