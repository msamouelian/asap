"""
Conversation folder management — every operation restricted to the folder owner.

Folders form a per-user tree. The root folder 'All' is implicit and never
stored: a folder with parent_id NULL is a direct child of 'All', and a
conversation with folder_id NULL lives in 'All'. The root therefore cannot
be renamed or deleted by construction.

Invariants enforced here (the schema's FK actions are only backstops):
  - folder names are non-empty, <= 100 chars, unique among siblings
  - a folder can only be deleted when it contains no folders and no
    conversations (409 otherwise)
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
    prefix="/folders",
    tags=["folders"],
    dependencies=[Depends(require_role("user"))],
)

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]


# ── Pydantic models ───────────────────────────────────────────────────────────

class FolderCreate(BaseModel):
    name: str
    parent_id: uuid.UUID | None = None   # None = child of the root 'All'


class FolderUpdate(BaseModel):
    name: str


class FolderResponse(BaseModel):
    id: str
    parent_id: str | None
    name: str
    created_ts: datetime


# ── Helpers ───────────────────────────────────────────────────────────────────

def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found.")


def _row_to_response(row: dict) -> FolderResponse:
    return FolderResponse(
        id=str(row["id"]),
        parent_id=str(row["parent_id"]) if row["parent_id"] else None,
        name=row["name"],
        created_ts=row["created_ts"],
    )


def _clean_name(name: str) -> str:
    trimmed = name.strip()
    if not trimmed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Folder name cannot be empty.")
    if len(trimmed) > 100:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Folder name is too long (max 100 characters).")
    return trimmed


async def _assert_owned(conn: psycopg.AsyncConnection, folder_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, parent_id, name, created_ts FROM conversation_folder WHERE id = %s AND user_id = %s",
            (folder_id, user_id),
        )
        row = await cur.fetchone()
    if not row:
        raise _not_found()
    return row


async def _assert_name_free(
    conn: psycopg.AsyncConnection,
    user_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    name: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Case-insensitive sibling-uniqueness check (parent_id IS NOT DISTINCT
    FROM handles the NULL root, which a unique index can't cover portably)."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT 1 FROM conversation_folder
            WHERE user_id = %s
              AND parent_id IS NOT DISTINCT FROM %s
              AND lower(name) = lower(%s)
              AND (%s::uuid IS NULL OR id != %s)
            """,
            (user_id, parent_id, name, exclude_id, exclude_id),
        )
        if await cur.fetchone():
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"A folder named '{name}' already exists here.",
            )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[FolderResponse])
async def list_folders(user: CurrentUser, conn: DBConn):
    """All of the caller's folders as a flat list — the UI assembles the tree."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, parent_id, name, created_ts
            FROM conversation_folder
            WHERE user_id = %s
            ORDER BY name
            """,
            (uuid.UUID(user.id),),
        )
        return [_row_to_response(r) for r in await cur.fetchall()]


@router.post("", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(body: FolderCreate, user: CurrentUser, conn: DBConn):
    uid = uuid.UUID(user.id)
    name = _clean_name(body.name)
    if body.parent_id is not None:
        await _assert_owned(conn, body.parent_id, uid)
    await _assert_name_free(conn, uid, body.parent_id, name)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO conversation_folder (user_id, parent_id, name)
            VALUES (%s, %s, %s)
            RETURNING id, parent_id, name, created_ts
            """,
            (uid, body.parent_id, name),
        )
        return _row_to_response(await cur.fetchone())


@router.put("/{folder_id}", response_model=FolderResponse)
async def rename_folder(folder_id: uuid.UUID, body: FolderUpdate, user: CurrentUser, conn: DBConn):
    uid = uuid.UUID(user.id)
    name = _clean_name(body.name)
    existing = await _assert_owned(conn, folder_id, uid)
    await _assert_name_free(conn, uid, existing["parent_id"], name, exclude_id=folder_id)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE conversation_folder SET name = %s
            WHERE id = %s AND user_id = %s
            RETURNING id, parent_id, name, created_ts
            """,
            (name, folder_id, uid),
        )
        return _row_to_response(await cur.fetchone())


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: uuid.UUID, user: CurrentUser, conn: DBConn):
    """Delete an EMPTY folder. Non-empty folders are refused with 409 —
    the user must move or delete the contents first."""
    uid = uuid.UUID(user.id)
    await _assert_owned(conn, folder_id, uid)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM conversation_folder WHERE parent_id = %s) AS subfolders,
              (SELECT COUNT(*) FROM conversation WHERE folder_id = %s)        AS conversations
            """,
            (folder_id, folder_id),
        )
        counts = await cur.fetchone()
        if counts["subfolders"] or counts["conversations"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Folder is not empty — move or delete its contents first.",
            )
        await cur.execute(
            "DELETE FROM conversation_folder WHERE id = %s AND user_id = %s",
            (folder_id, uid),
        )
