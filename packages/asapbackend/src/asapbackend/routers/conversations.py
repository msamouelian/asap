"""
Conversation management — every operation restricted to the conversation owner.

Ownership is enforced in SQL WHERE clauses (id = %s AND user_id = %s), so
unauthorized access and not-found both return 404, avoiding resource existence
leakage.

Note: conversations are also created implicitly by POST /chat when no
conversation_id is supplied. POST /conversations is provided for the UI to
pre-create a titled conversation before the first message is sent.
"""

import uuid
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from asapbackend.auth.dependencies import CurrentUser, require_role
from asapbackend.config import settings
from asapbackend.database import get_db
from asapbackend.services import rag

router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
    dependencies=[Depends(require_role("user"))],
)

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]


# ── Pydantic models ───────────────────────────────────────────────────────────

class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationUpdate(BaseModel):
    title: str


class ConversationFolderUpdate(BaseModel):
    folder_id: uuid.UUID | None   # None = move to the implicit root folder 'All'


class ConversationResponse(BaseModel):
    id: str
    user_id: str
    title: str | None
    created_ts: datetime
    message_count: int
    context_pct: float | None = None
    folder_id: str | None = None  # None = implicit root folder 'All'


class RetrievedChunk(BaseModel):
    id: str
    text: str
    page_no: int | None = None
    heading: str | None = None
    file_name: str
    collection: str


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    message: str
    reasoning: str | None = None
    created_ts: datetime
    # Document chunks retrieved for this (user) message — lets the UI show
    # the collapsible "retrieved context" block when reloading history.
    retrieved_chunks: list[RetrievedChunk] | None = None
    # The distilled search query that produced them, when distillation ran.
    retrieval_query: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _caller_id(conn: psycopg.AsyncConnection, user: CurrentUser) -> str:
    # user.id is populated by get_current_user via auto-provisioning
    return user.id


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")


def _context_pct(last_prompt_tokens: int | None) -> float | None:
    if last_prompt_tokens is None:
        return None
    return round(last_prompt_tokens / settings.inference_context_window_tokens * 100, 1)


def _row_to_response(row: dict) -> ConversationResponse:
    return ConversationResponse(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        title=row["title"],
        created_ts=row["created_ts"],
        message_count=row["message_count"],
        context_pct=_context_pct(row.get("last_prompt_tokens")),
        folder_id=str(row["folder_id"]) if row.get("folder_id") else None,
    )


async def _get_or_404(conn: psycopg.AsyncConnection, conversation_id: uuid.UUID, caller_db_id: str) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT c.id, c.user_id, c.title, c.last_prompt_tokens, c.created_ts,
                   c.folder_id, COUNT(cm.id) AS message_count
            FROM conversation c
            LEFT JOIN conversation_message cm ON cm.conversation_id = c.id
            WHERE c.id = %s AND c.user_id = %s
            GROUP BY c.id
            """,
            (conversation_id, uuid.UUID(caller_db_id)),
        )
        row = await cur.fetchone()
    if not row:
        raise _not_found()
    return row


# ── Conversation CRUD ─────────────────────────────────────────────────────────

@router.get("", response_model=list[ConversationResponse])
async def list_conversations(user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT c.id, c.user_id, c.title, c.last_prompt_tokens, c.created_ts,
                   c.folder_id, COUNT(cm.id) AS message_count
            FROM conversation c
            LEFT JOIN conversation_message cm ON cm.conversation_id = c.id
            WHERE c.user_id = %s
            GROUP BY c.id
            ORDER BY c.created_ts DESC
            """,
            (uuid.UUID(cid),),
        )
        return [_row_to_response(r) for r in await cur.fetchall()]


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(body: ConversationCreate, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO conversation (user_id, title) VALUES (%s, %s) RETURNING id, user_id, title, created_ts",
            (uuid.UUID(cid), body.title),
        )
        row = await cur.fetchone()
    return ConversationResponse(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        title=row["title"],
        created_ts=row["created_ts"],
        message_count=0,
    )


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    return _row_to_response(await _get_or_404(conn, conversation_id, cid))


@router.put("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(conversation_id: uuid.UUID, body: ConversationUpdate, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE conversation SET title = %s WHERE id = %s AND user_id = %s RETURNING id",
            (body.title, conversation_id, uuid.UUID(cid)),
        )
        if not await cur.fetchone():
            raise _not_found()
    return _row_to_response(await _get_or_404(conn, conversation_id, cid))


@router.put("/{conversation_id}/folder", response_model=ConversationResponse)
async def move_conversation(
    conversation_id: uuid.UUID, body: ConversationFolderUpdate, user: CurrentUser, conn: DBConn,
):
    """Move a conversation into a folder (folder_id null = the root 'All')."""
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        if body.folder_id is not None:
            # The target folder must exist and belong to the caller.
            await cur.execute(
                "SELECT 1 FROM conversation_folder WHERE id = %s AND user_id = %s",
                (body.folder_id, uuid.UUID(cid)),
            )
            if not await cur.fetchone():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found.")
        await cur.execute(
            "UPDATE conversation SET folder_id = %s WHERE id = %s AND user_id = %s RETURNING id",
            (body.folder_id, conversation_id, uuid.UUID(cid)),
        )
        if not await cur.fetchone():
            raise _not_found()
    return _row_to_response(await _get_or_404(conn, conversation_id, cid))


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    """Hard delete — conversation_message rows are removed via ON DELETE CASCADE."""
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        await cur.execute(
            "DELETE FROM conversation WHERE id = %s AND user_id = %s RETURNING id",
            (conversation_id, uuid.UUID(cid)),
        )
        if not await cur.fetchone():
            raise _not_found()


# ── Messages sub-resource (read-only) ────────────────────────────────────────

@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(conversation_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    """Return all messages for this conversation, including the stored system prompt."""
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        # Ownership check enforced in EXISTS subquery.
        await cur.execute(
            "SELECT EXISTS(SELECT 1 FROM conversation WHERE id = %s AND user_id = %s) AS ok",
            (conversation_id, uuid.UUID(cid)),
        )
        if not (await cur.fetchone())["ok"]:
            raise _not_found()
        await cur.execute(
            """
            SELECT id, conversation_id, role, message, reasoning, created_ts,
                   retrieval_query
            FROM conversation_message
            WHERE conversation_id = %s
            ORDER BY created_ts ASC
            """,
            (conversation_id,),
        )
        rows = await cur.fetchall()

        # Attach retrieved-chunk provenance to user messages (RAG turns).
        msg_ids = [str(r["id"]) for r in rows if r["role"] == "user"]
        chunks_by_msg: dict[str, list[dict]] = {}
        if msg_ids:
            await cur.execute(
                """
                SELECT conversation_message_id, document_chunk_id
                FROM conversation_message_document_chunk
                WHERE conversation_message_id = ANY(%s::uuid[])
                ORDER BY conversation_message_id, retrieval_order
                """,
                (msg_ids,),
            )
            refs = await cur.fetchall()
            if refs:
                import asyncio
                from asapbackend.services import graph_docs
                all_ids = [str(r["document_chunk_id"]) for r in refs]
                chunk_map = {
                    c["id"]: c
                    for c in await asyncio.to_thread(graph_docs.get_chunks_by_ids, all_ids)
                }
                for r in refs:
                    mid = str(r["conversation_message_id"])
                    cid = str(r["document_chunk_id"])
                    if cid in chunk_map:
                        chunks_by_msg.setdefault(mid, []).append(chunk_map[cid])

    return [
        MessageResponse(
            id=str(r["id"]),
            conversation_id=str(r["conversation_id"]),
            role=r["role"],
            message=r["message"],
            reasoning=r["reasoning"],
            created_ts=r["created_ts"],
            retrieval_query=r["retrieval_query"],
            retrieved_chunks=(
                # Merge stored chunk refs into passages — the same units the
                # LLM cited, so [Passage N] labels line up on reload.
                [
                    RetrievedChunk(
                        **{k: p.get(k) for k in RetrievedChunk.model_fields}
                    )
                    for p in rag.to_passages(chunks_by_msg[str(r["id"])])
                ]
                if str(r["id"]) in chunks_by_msg else None
            ),
        )
        for r in rows
    ]
