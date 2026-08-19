"""
System prompt management — admin only.

Updates are in-place; modified_ts is refreshed on every PUT.
Deletes are soft: expiration_ts is set to now() so the row is preserved for
auditing. get_system_prompt() filters out soft-deleted rows automatically.

Tag sub-resource: one tag associated/disassociated at a time. The tag must
already exist — use /tags endpoints to create it first.

is_default: at most one system prompt may be the default at any time.
Setting is_default=true on a prompt automatically clears it on all others.
"""

import uuid
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from asapbackend.auth.dependencies import CurrentUser, require_role
from asapbackend.database import get_db

router = APIRouter(
    prefix="/system-prompts",
    tags=["system-prompts"],
    dependencies=[Depends(require_role("admin"))],
)

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]

_SELECT_COLS = """
    id, creating_user_id, title, description, prompt_text, is_default,
    create_ts, modified_ts, expiration_ts
"""


# ── Pydantic models ───────────────────────────────────────────────────────────

class SystemPromptCreate(BaseModel):
    title: str
    description: str | None = None
    prompt_text: str
    is_default: bool = False


class SystemPromptUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    prompt_text: str | None = None
    is_default: bool | None = None


class SystemPromptResponse(BaseModel):
    id: str
    creating_user_id: str
    title: str
    description: str | None
    prompt_text: str
    is_default: bool
    create_ts: datetime
    modified_ts: datetime | None
    expiration_ts: datetime | None
    # Populated by the list endpoint only — used for tag filtering in the UI.
    tag_ids: list[str] = []


class TagAssociationRequest(BaseModel):
    tag_id: uuid.UUID


class PromptTagResponse(BaseModel):
    association_id: str
    tag_id: str
    name: str
    description: str | None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_response(row: dict) -> SystemPromptResponse:
    return SystemPromptResponse(
        id=str(row["id"]),
        creating_user_id=str(row["creating_user_id"]),
        title=row["title"],
        description=row["description"],
        prompt_text=row["prompt_text"],
        is_default=row["is_default"],
        create_ts=row["create_ts"],
        modified_ts=row["modified_ts"],
        expiration_ts=row["expiration_ts"],
    )


async def _get_or_404(conn: psycopg.AsyncConnection, prompt_id: uuid.UUID) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            f"SELECT {_SELECT_COLS} FROM system_prompt WHERE id = %s",
            (prompt_id,),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System prompt not found.")
    return row


# ── System prompt CRUD ────────────────────────────────────────────────────────

@router.get("", response_model=list[SystemPromptResponse])
async def list_system_prompts(
    conn: DBConn,
    active_only: bool = Query(default=True, description="Exclude soft-deleted prompts"),
):
    where = "WHERE sp.expiration_ts IS NULL OR sp.expiration_ts > now()" if active_only else ""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT sp.id, sp.creating_user_id, sp.title, sp.description,
                   sp.prompt_text, sp.is_default, sp.create_ts, sp.modified_ts,
                   sp.expiration_ts,
                   COALESCE(
                     array_agg(spt.tag_id) FILTER (WHERE spt.tag_id IS NOT NULL),
                     '{{}}'
                   ) AS tag_ids
            FROM system_prompt sp
            LEFT JOIN system_prompt_tag spt ON spt.system_prompt_id = sp.id
            {where}
            GROUP BY sp.id
            ORDER BY sp.is_default DESC, COALESCE(sp.modified_ts, sp.create_ts) DESC
            """
        )
        rows = await cur.fetchall()
    results = []
    for r in rows:
        resp = _row_to_response(r)
        resp.tag_ids = [str(t) for t in r["tag_ids"]]
        results.append(resp)
    return results


@router.post("", response_model=SystemPromptResponse, status_code=status.HTTP_201_CREATED)
async def create_system_prompt(body: SystemPromptCreate, user: CurrentUser, conn: DBConn):
    async with conn.cursor() as cur:
        if body.is_default:
            await cur.execute("UPDATE system_prompt SET is_default = false")
        await cur.execute(
            f"""
            INSERT INTO system_prompt (creating_user_id, title, description, prompt_text, is_default)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING {_SELECT_COLS}
            """,
            (uuid.UUID(user.id), body.title, body.description, body.prompt_text, body.is_default),
        )
        row = await cur.fetchone()
    return _row_to_response(row)


@router.get("/{prompt_id}", response_model=SystemPromptResponse)
async def get_system_prompt_by_id(prompt_id: uuid.UUID, conn: DBConn):
    return _row_to_response(await _get_or_404(conn, prompt_id))


@router.put("/{prompt_id}", response_model=SystemPromptResponse)
async def update_system_prompt(prompt_id: uuid.UUID, body: SystemPromptUpdate, conn: DBConn):
    updates = ["modified_ts = now()"]
    params: list = []
    if body.title is not None:
        updates.append("title = %s")
        params.append(body.title)
    if body.description is not None:
        updates.append("description = %s")
        params.append(body.description)
    if body.prompt_text is not None:
        updates.append("prompt_text = %s")
        params.append(body.prompt_text)
    if body.is_default is not None:
        updates.append("is_default = %s")
        params.append(body.is_default)
    if len(updates) == 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update.")
    params.append(prompt_id)
    async with conn.cursor() as cur:
        # When setting this prompt as default, clear all others first.
        if body.is_default is True:
            await cur.execute(
                "UPDATE system_prompt SET is_default = false WHERE id != %s",
                (prompt_id,),
            )
        await cur.execute(
            f"""
            UPDATE system_prompt SET {', '.join(updates)}
            WHERE id = %s
            RETURNING {_SELECT_COLS}
            """,
            params,
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System prompt not found.")
    return _row_to_response(row)


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def expire_system_prompt(prompt_id: uuid.UUID, conn: DBConn):
    """Soft delete — sets expiration_ts to now() so the row is retained for auditing.

    Refused when the prompt is assigned to any user (regardless of user status);
    the admin must unassign it via the Manage Users screen first.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT count(*) AS cnt FROM asap_user WHERE system_prompt_id = %s",
            (prompt_id,),
        )
        cnt = (await cur.fetchone())["cnt"]
        if cnt:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"This system prompt is assigned to {cnt} user(s). "
                    "Unassign it from those users in the Manage Users screen before expiring it."
                ),
            )
        await cur.execute(
            "UPDATE system_prompt SET expiration_ts = now(), is_default = false WHERE id = %s RETURNING id",
            (prompt_id,),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System prompt not found.")


@router.post("/{prompt_id}/unexpire", response_model=SystemPromptResponse)
async def unexpire_system_prompt(prompt_id: uuid.UUID, conn: DBConn):
    """Reverse a soft delete — clears expiration_ts so the prompt is active again."""
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            UPDATE system_prompt SET expiration_ts = NULL, modified_ts = now()
            WHERE id = %s
            RETURNING {_SELECT_COLS}
            """,
            (prompt_id,),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System prompt not found.")
    return _row_to_response(row)


# ── Tag association sub-resource ──────────────────────────────────────────────

@router.get("/{prompt_id}/tags", response_model=list[PromptTagResponse])
async def list_prompt_tags(prompt_id: uuid.UUID, conn: DBConn):
    await _get_or_404(conn, prompt_id)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT spt.id AS association_id, t.id AS tag_id, t.name, t.description
            FROM system_prompt_tag spt
            JOIN tag t ON t.id = spt.tag_id
            WHERE spt.system_prompt_id = %s
            ORDER BY t.name
            """,
            (prompt_id,),
        )
        rows = await cur.fetchall()
    return [
        PromptTagResponse(
            association_id=str(r["association_id"]),
            tag_id=str(r["tag_id"]),
            name=r["name"],
            description=r["description"],
        )
        for r in rows
    ]


@router.post("/{prompt_id}/tags", response_model=PromptTagResponse,
             status_code=status.HTTP_201_CREATED)
async def add_prompt_tag(prompt_id: uuid.UUID, body: TagAssociationRequest, conn: DBConn):
    await _get_or_404(conn, prompt_id)
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, name, description FROM tag WHERE id = %s",
            (body.tag_id,),
        )
        tag = await cur.fetchone()
        if not tag:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found.")
        try:
            await cur.execute(
                """
                INSERT INTO system_prompt_tag (system_prompt_id, tag_id)
                VALUES (%s, %s) RETURNING id AS association_id
                """,
                (prompt_id, body.tag_id),
            )
            assoc = await cur.fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tag is already associated with this system prompt.",
            )
    return PromptTagResponse(
        association_id=str(assoc["association_id"]),
        tag_id=str(tag["id"]),
        name=tag["name"],
        description=tag["description"],
    )


@router.delete("/{prompt_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_prompt_tag(prompt_id: uuid.UUID, tag_id: uuid.UUID, conn: DBConn):
    async with conn.cursor() as cur:
        await cur.execute(
            """
            DELETE FROM system_prompt_tag
            WHERE system_prompt_id = %s AND tag_id = %s
            RETURNING id
            """,
            (prompt_id, tag_id),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag association not found.",
        )
