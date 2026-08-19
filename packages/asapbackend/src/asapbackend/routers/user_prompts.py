"""
User prompt management.

Authorization is enforced in the SQL WHERE clause of every query — not in
application-layer fetch-then-check patterns. A prompt that doesn't exist and a
prompt the caller isn't allowed to touch both return 404 (never 403), which
avoids leaking the existence of private resources.

Visibility rules (encoded in SQL):
  read  — own prompts OR global prompts
  write — own prompts only (update, delete, tag add/remove)
"""

import uuid
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from asapbackend.auth.dependencies import CurrentUser, require_role
from asapbackend.database import get_db

router = APIRouter(
    prefix="/user-prompts",
    tags=["user-prompts"],
    dependencies=[Depends(require_role("user"))],
)

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]

_COLS = """
    id, creating_user_id, title, prompt_text,
    create_ts, modified_ts, "global" AS is_global
"""


# ── Pydantic models ───────────────────────────────────────────────────────────

class UserPromptCreate(BaseModel):
    title: str
    prompt_text: str
    is_global: bool = False


class UserPromptUpdate(BaseModel):
    title: str | None = None
    prompt_text: str | None = None
    is_global: bool | None = None


class UserPromptResponse(BaseModel):
    id: str
    creating_user_id: str
    title: str
    prompt_text: str
    create_ts: datetime
    modified_ts: datetime | None
    is_global: bool


class TagAssociationRequest(BaseModel):
    tag_id: uuid.UUID


class PromptTagResponse(BaseModel):
    association_id: str
    tag_id: str
    name: str
    description: str | None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _to_response(row: dict) -> UserPromptResponse:
    return UserPromptResponse(
        id=str(row["id"]),
        creating_user_id=str(row["creating_user_id"]),
        title=row["title"],
        prompt_text=row["prompt_text"],
        create_ts=row["create_ts"],
        modified_ts=row["modified_ts"],
        is_global=row["is_global"],
    )


async def _caller_id(conn: psycopg.AsyncConnection, user: CurrentUser) -> str:
    return user.id


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User prompt not found.")


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[UserPromptResponse])
async def list_user_prompts(user: CurrentUser, conn: DBConn):
    """Own prompts (any visibility) plus all global prompts."""
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT {_COLS} FROM user_prompt
            WHERE creating_user_id = %s OR "global" = true
            ORDER BY COALESCE(modified_ts, create_ts) DESC
            """,
            (uuid.UUID(cid),),
        )
        return [_to_response(r) for r in await cur.fetchall()]


@router.post("", response_model=UserPromptResponse, status_code=status.HTTP_201_CREATED)
async def create_user_prompt(body: UserPromptCreate, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            INSERT INTO user_prompt (creating_user_id, title, prompt_text, "global")
            VALUES (%s, %s, %s, %s)
            RETURNING {_COLS}
            """,
            (uuid.UUID(cid), body.title, body.prompt_text, body.is_global),
        )
        return _to_response(await cur.fetchone())


@router.get("/{prompt_id}", response_model=UserPromptResponse)
async def get_user_prompt(prompt_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            SELECT {_COLS} FROM user_prompt
            WHERE id = %s AND (creating_user_id = %s OR "global" = true)
            """,
            (prompt_id, uuid.UUID(cid)),
        )
        row = await cur.fetchone()
    if not row:
        raise _not_found()
    return _to_response(row)


@router.put("/{prompt_id}", response_model=UserPromptResponse)
async def update_user_prompt(prompt_id: uuid.UUID, body: UserPromptUpdate, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    updates = ["modified_ts = now()"]
    params: list = []
    if body.title is not None:
        updates.append("title = %s")
        params.append(body.title)
    if body.prompt_text is not None:
        updates.append("prompt_text = %s")
        params.append(body.prompt_text)
    if body.is_global is not None:
        updates.append('"global" = %s')
        params.append(body.is_global)
    if len(updates) == 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update.")

    params.extend([prompt_id, uuid.UUID(cid)])
    async with conn.cursor() as cur:
        await cur.execute(
            f"""
            UPDATE user_prompt SET {', '.join(updates)}
            WHERE id = %s AND creating_user_id = %s
            RETURNING {_COLS}
            """,
            params,
        )
        row = await cur.fetchone()
    if not row:
        raise _not_found()
    return _to_response(row)


@router.delete("/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_prompt(prompt_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        await cur.execute(
            "DELETE FROM user_prompt WHERE id = %s AND creating_user_id = %s RETURNING id",
            (prompt_id, uuid.UUID(cid)),
        )
        if not await cur.fetchone():
            raise _not_found()


# ── Tag sub-resource ──────────────────────────────────────────────────────────

@router.get("/{prompt_id}/tags", response_model=list[PromptTagResponse])
async def list_prompt_tags(prompt_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        # Visibility check — same rule as GET /{prompt_id}.
        await cur.execute(
            "SELECT EXISTS(SELECT 1 FROM user_prompt WHERE id = %s AND (creating_user_id = %s OR \"global\" = true)) AS ok",
            (prompt_id, uuid.UUID(cid)),
        )
        if not (await cur.fetchone())["ok"]:
            raise _not_found()
        await cur.execute(
            """
            SELECT upt.id AS association_id, t.id AS tag_id, t.name, t.description
            FROM user_prompt_tag upt
            JOIN tag t ON t.id = upt.tag_id
            WHERE upt.user_prompt_id = %s
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
async def add_prompt_tag(prompt_id: uuid.UUID, body: TagAssociationRequest, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        # Verify tag exists.
        await cur.execute("SELECT id, name, description FROM tag WHERE id = %s", (body.tag_id,))
        tag = await cur.fetchone()
        if not tag:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found.")
        try:
            # INSERT only when the caller owns the prompt — ownership enforced in WHERE.
            await cur.execute(
                """
                INSERT INTO user_prompt_tag (user_prompt_id, tag_id)
                SELECT %s, %s
                WHERE EXISTS (
                    SELECT 1 FROM user_prompt WHERE id = %s AND creating_user_id = %s
                )
                RETURNING id AS association_id
                """,
                (prompt_id, body.tag_id,
                 prompt_id, uuid.UUID(cid)),
            )
            assoc = await cur.fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tag is already associated with this prompt.",
            )
    if not assoc:
        raise _not_found()
    return PromptTagResponse(
        association_id=str(assoc["association_id"]),
        tag_id=str(tag["id"]),
        name=tag["name"],
        description=tag["description"],
    )


@router.delete("/{prompt_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_prompt_tag(prompt_id: uuid.UUID, tag_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    cid = await _caller_id(conn, user)
    async with conn.cursor() as cur:
        # Ownership enforced via USING join — deletes only when caller owns the prompt.
        await cur.execute(
            """
            DELETE FROM user_prompt_tag upt
            USING user_prompt up
            WHERE upt.user_prompt_id = up.id
              AND upt.user_prompt_id = %s
              AND upt.tag_id = %s
              AND up.creating_user_id = %s
            RETURNING upt.id
            """,
            (prompt_id, tag_id, uuid.UUID(cid)),
        )
        if not await cur.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tag association not found.",
            )
