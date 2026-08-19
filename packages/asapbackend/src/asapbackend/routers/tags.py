"""Tag management — both 'user' and 'admin' roles have full CRUD access."""

import uuid
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from asapbackend.auth.dependencies import CurrentUser, require_role
from asapbackend.database import get_db

router = APIRouter(
    prefix="/tags",
    tags=["tags"],
    dependencies=[Depends(require_role("user"))],
)

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]


# ── Pydantic models ───────────────────────────────────────────────────────────

class TagCreate(BaseModel):
    name: str
    description: str | None = None


class TagUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class TagResponse(BaseModel):
    id: str
    name: str
    description: str | None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_response(row: dict) -> TagResponse:
    return TagResponse(id=str(row["id"]), name=row["name"], description=row["description"])


async def _get_or_404(conn: psycopg.AsyncConnection, tag_id: uuid.UUID) -> dict:
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT id, name, description FROM tag WHERE id = %s",
            (tag_id,),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found.")
    return row


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[TagResponse])
async def list_tags(conn: DBConn):
    async with conn.cursor() as cur:
        await cur.execute("SELECT id, name, description FROM tag ORDER BY name")
        rows = await cur.fetchall()
    return [_row_to_response(r) for r in rows]


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(body: TagCreate, conn: DBConn):
    async with conn.cursor() as cur:
        try:
            await cur.execute(
                "INSERT INTO tag (name, description) VALUES (%s, %s) RETURNING id, name, description",
                (body.name, body.description),
            )
            row = await cur.fetchone()
        except psycopg.errors.UniqueViolation:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tag '{body.name}' already exists.",
            )
    return _row_to_response(row)


@router.get("/{tag_id}", response_model=TagResponse)
async def get_tag(tag_id: uuid.UUID, conn: DBConn):
    return _row_to_response(await _get_or_404(conn, tag_id))


@router.put("/{tag_id}", response_model=TagResponse)
async def update_tag(tag_id: uuid.UUID, body: TagUpdate, conn: DBConn):
    if not body.name and body.description is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update.")
    async with conn.cursor() as cur:
        await cur.execute(
            """
            UPDATE tag
            SET name        = COALESCE(%s, name),
                description = COALESCE(%s, description)
            WHERE id = %s
            RETURNING id, name, description
            """,
            (body.name, body.description, tag_id),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found.")
    return _row_to_response(row)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: uuid.UUID, conn: DBConn):
    async with conn.cursor() as cur:
        await cur.execute(
            "DELETE FROM tag WHERE id = %s RETURNING id", (tag_id,)
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found.")
