"""Conversation persistence — save and reload conversation turns."""

import json
import uuid
from datetime import datetime, timezone

import psycopg

from asapbackend.config import settings


async def get_user_id(conn: psycopg.AsyncConnection, keycloak_id: str) -> str | None:
    """Return the user's database UUID by Keycloak sub claim."""
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM asap_user WHERE keycloak_id = %s",
            (keycloak_id,),
        )
        row = await cur.fetchone()
    return str(row["id"]) if row else None


async def create_conversation(
    conn: psycopg.AsyncConnection, user_id: str, title: str
) -> str:
    async with conn.cursor() as cur:
        await cur.execute(
            "INSERT INTO conversation (user_id, title) VALUES (%s, %s) RETURNING id",
            (uuid.UUID(user_id), title),
        )
        row = await cur.fetchone()
    return str(row["id"])


async def append_message(
    conn: psycopg.AsyncConnection,
    conversation_id: str,
    role: str,
    content: str | dict,
    reasoning: str | None = None,
) -> str:
    """Persist a single message turn and return the new message id.

    content may be a string or a dict (for tool calls).
    reasoning is only populated for assistant turns that include reasoning tokens.
    """
    text = content if isinstance(content, str) else json.dumps(content)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO conversation_message (conversation_id, role, message, reasoning)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (uuid.UUID(conversation_id), role, text, reasoning),
        )
        row = await cur.fetchone()
    return str(row["id"])


async def save_chunk_refs(
    conn: psycopg.AsyncConnection,
    message_id: str,
    chunk_ids: list[str],
    retrieval_query: str | None = None,
) -> None:
    """Record which document chunks were retrieved for a user message, plus
    the distilled search query that retrieved them (surfaced in the UI).
    Only ids are stored — chunk TEXT lives in Neo4j and is re-fetched on
    rehydration, so history never bloats with duplicated content."""
    async with conn.cursor() as cur:
        for order, cid in enumerate(chunk_ids):
            await cur.execute(
                """
                INSERT INTO conversation_message_document_chunk
                    (conversation_message_id, document_chunk_id, retrieval_order)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (uuid.UUID(message_id), uuid.UUID(cid), order),
            )
        if retrieval_query is not None:
            await cur.execute(
                "UPDATE conversation_message SET retrieval_query = %s WHERE id = %s",
                (retrieval_query, uuid.UUID(message_id)),
            )


async def update_prompt_tokens(
    conn: psycopg.AsyncConnection,
    conversation_id: str,
    prompt_tokens: int,
) -> None:
    """Persist the most recent prompt token count on the conversation row.

    Called after the final stop turn so the context-usage percentage is
    available when reloading the conversation from the API.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE conversation SET last_prompt_tokens = %s WHERE id = %s",
            (prompt_tokens, uuid.UUID(conversation_id)),
        )


async def load_conversation_messages(
    conn: psycopg.AsyncConnection,
    conversation_id: str,
    exclude_roles: frozenset[str] = frozenset({"system"}),
) -> list[dict]:
    """
    Return the message history in OpenAI format.

    System messages are excluded by default because the caller always prepends
    the current system prompt. Pass exclude_roles=frozenset() to retrieve all
    roles (e.g. for an audit endpoint).

    When settings.reasoning_include_in_context is True, any stored reasoning is
    prepended to the assistant message content as <think>…</think> so OSS
    reasoning models see their own chain-of-thought in history.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT role, message, reasoning FROM conversation_message
            WHERE conversation_id = %s
              AND role != ALL(%s)
            ORDER BY created_ts ASC
            """,
            (uuid.UUID(conversation_id), list(exclude_roles)),
        )
        rows = await cur.fetchall()

    messages = []
    for row in rows:
        text = row["message"]
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = text

        if isinstance(parsed, dict):
            # Full message object (assistant tool-call turn, tool result, etc.) —
            # reasoning is not re-injected here; tool-call turns have content=None.
            messages.append(parsed)
        else:
            if (
                row["role"] == "assistant"
                and row["reasoning"]
                and settings.reasoning_include_in_context
            ):
                content = f"<think>{row['reasoning']}</think>{parsed}"
            else:
                content = parsed
            messages.append({"role": row["role"], "content": content})
    return messages
