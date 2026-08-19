"""
Chat endpoint — streams agent responses via Server-Sent Events.

POST /chat
  Body: {"conversation_id": "<uuid>", "message": "<user text>"}
  Response: text/event-stream, each line: data: <json>\n\n

If conversation_id is omitted a new conversation is created automatically.
"""

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from asapbackend.auth.dependencies import CurrentUser
from asapbackend.config import settings
from asapbackend.database import get_db
from asapbackend.llm import agent
from asapbackend.services import conversation as conv_svc
from asapbackend.services import graph_docs
from asapbackend.services import rag
from asapbackend.services.system_prompt import get_system_prompt

import psycopg

router = APIRouter(prefix="/chat", tags=["chat"])

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]


class ChatRequest(BaseModel):
    message: str
    # Validated as a UUID at the API boundary; converted to str internally.
    conversation_id: uuid.UUID | None = None
    # Document collections to attach when this message CREATES a conversation
    # (chips selected on the New Chat screen). Attached before the agent runs
    # so retrieval sees them on the very first turn. Ignored for existing
    # conversations — those attach via the scopes API.
    document_collection_ids: list[uuid.UUID] = []
    # Sampling temperature for THIS message only (the UI's Precise/Balanced/
    # Exploratory stops). Not persisted — each submission carries the user's
    # current choice; None falls back to settings.inference_temperature.
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)


async def _stream(
    user: CurrentUser,
    request: ChatRequest,
    conn: psycopg.AsyncConnection,
):
    # Look up the user — they must be pre-provisioned by an admin.
    user_db_id = user.id
    if not user_db_id:
        yield f"data: {json.dumps({'type': 'error', 'detail': 'User not provisioned. Contact an administrator.'})}\n\n"
        return

    # Resolve this user's effective system prompt (always fresh from DB —
    # assigned prompt, else newest default prompt, else bundled file).
    system_prompt = await get_system_prompt(conn, user_db_id)

    # Create a new conversation if none was supplied.
    conversation_id = str(request.conversation_id) if request.conversation_id else None
    if not conversation_id:
        title = request.message[:80]
        conversation_id = await conv_svc.create_conversation(conn, user_db_id, title)
        # Persist the system prompt once per conversation for auditing.
        await conv_svc.append_message(conn, conversation_id, "system", system_prompt)

        # Attach pre-selected document collections (New Chat chips) so the
        # first turn's retrieval already has them. Same validation as the
        # scopes API: visible to this user, ready, not archived.
        if request.document_collection_ids:
            ids = [str(i) for i in request.document_collection_ids]
            colls = await asyncio.to_thread(graph_docs.get_collections_by_ids, ids)
            for c in colls:
                visible = c["visibility"] == "shared" or c["uploaded_by"] == user_db_id
                if visible and c["status"] == "ready" and not c["archived"]:
                    await conn.execute(
                        "INSERT INTO conversation_document_collection "
                        "(conversation_id, document_collection_id) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (conversation_id, c["id"]),
                    )
            await conn.commit()

        yield f"data: {json.dumps({'type': 'conversation_id', 'conversation_id': conversation_id})}\n\n"

    # Load history excluding the stored system message — we always prepend the
    # current system prompt separately so the LLM never sees two system turns.
    history = await conv_svc.load_conversation_messages(conn, conversation_id)

    # Rehydration: re-apply retrieved document context to past user turns so
    # the model sees exactly the grounding it saw when those turns ran.
    if settings.rag_enabled and history:
        await rag.augment_history(conn, conversation_id, history)

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": request.message})

    # Persist the user message immediately (the RAW text — the retrieved
    # context below is composed at assembly time, never stored in history).
    user_msg_id = await conv_svc.append_message(
        conn, conversation_id, "user", request.message
    )

    # RAG: retrieve supporting chunks from the collections in effect. The
    # gate keeps irrelevant content out; failures never block the chat.
    if settings.rag_enabled:
        chunks, search_query = await rag.retrieve(
            conn, user_db_id, conversation_id, request.message
        )
        if chunks:
            await conv_svc.save_chunk_refs(
                conn, user_msg_id, [c["id"] for c in chunks], search_query
            )
            # Tell the UI what was retrieved (collapsible evidence block).
            # Passages, not raw chunks — the same units the LLM cites.
            ui_chunks = [
                {k: c.get(k) for k in ("id", "text", "page_no", "heading",
                                        "file_name", "collection")}
                for c in rag.to_passages(chunks)
            ]
            yield (
                "data: "
                + json.dumps({"type": "rag_context", "chunks": ui_chunks,
                              "search_query": search_query})
                + "\n\n"
            )
            messages[-1]["content"] = rag.augment_user_message(request.message, chunks)

    # Run the agent loop. All assistant and tool messages are persisted inside
    # agent.run() as they are generated — no further saves needed here.
    async for event in agent.run(
        messages, conn, conversation_id, temperature=request.temperature
    ):
        yield f"data: {json.dumps(event)}\n\n"


@router.post("")
async def chat(
    request: ChatRequest,
    user: CurrentUser,
    conn: DBConn,
):
    return StreamingResponse(
        _stream(user, request, conn),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
