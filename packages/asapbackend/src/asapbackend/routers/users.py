"""User management endpoints."""

import uuid
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from asapbackend.auth.dependencies import CurrentUser, require_role
from asapbackend.database import get_db

router = APIRouter(prefix="/users", tags=["users"])

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]

# Shared SELECT — user columns plus the assigned system prompt title.
_USER_SELECT = """
    SELECT u.id, u.keycloak_id, u.email, u.full_name, u.role, u.status,
           u.system_prompt_id, sp.title AS system_prompt_title
    FROM asap_user u
    LEFT JOIN system_prompt sp ON sp.id = u.system_prompt_id
"""


# ── Pydantic models ───────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: str
    keycloak_id: str
    email: str
    full_name: str
    role: str
    status: str
    system_prompt_id: str | None = None
    system_prompt_title: str | None = None


class StatusUpdate(BaseModel):
    status: str  # 'active' | 'inactive'


class SystemPromptAssignment(BaseModel):
    system_prompt_id: uuid.UUID | None  # null = unassign


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_response(row: dict) -> UserResponse:
    return UserResponse(
        id=str(row["id"]),
        keycloak_id=row["keycloak_id"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        status=row["status"],
        system_prompt_id=str(row["system_prompt_id"]) if row["system_prompt_id"] else None,
        system_prompt_title=row["system_prompt_title"],
    )


async def _fetch_user(conn: psycopg.AsyncConnection, user_id: uuid.UUID) -> dict | None:
    async with conn.cursor() as cur:
        await cur.execute(f"{_USER_SELECT} WHERE u.id = %s", (user_id,))
        return await cur.fetchone()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserResponse)
async def get_me(user: CurrentUser, conn: DBConn):
    """Return the profile of the currently authenticated user.

    Includes the assigned system prompt title only when the assignment is
    effective (i.e. the prompt is not expired) — an expired assignment is
    treated as unassigned, matching the chat-time resolution logic.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT u.id, u.keycloak_id, u.email, u.full_name, u.role, u.status,
                   sp.id AS system_prompt_id, sp.title AS system_prompt_title
            FROM asap_user u
            LEFT JOIN system_prompt sp
              ON sp.id = u.system_prompt_id
             AND (sp.expiration_ts IS NULL OR sp.expiration_ts > now())
            WHERE u.id = %s
            """,
            (uuid.UUID(user.id),),
        )
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return _row_to_response(row)


@router.get("", response_model=list[UserResponse], dependencies=[Depends(require_role("admin"))])
async def list_users(conn: DBConn):
    """List all ASAP users — admin only."""
    async with conn.cursor() as cur:
        await cur.execute(f"{_USER_SELECT} ORDER BY u.full_name")
        rows = await cur.fetchall()
    return [_row_to_response(r) for r in rows]


@router.patch(
    "/{user_id}/status",
    response_model=UserResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def update_status(user_id: uuid.UUID, body: StatusUpdate, conn: DBConn):
    """Activate or deactivate a user account — admin only."""
    if body.status not in ("active", "inactive"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be 'active' or 'inactive'.",
        )
    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE asap_user SET status = %s, updated_at = now() WHERE id = %s RETURNING id",
            (body.status, user_id),
        )
        updated = await cur.fetchone()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    row = await _fetch_user(conn, user_id)
    return _row_to_response(row)


@router.patch(
    "/{user_id}/system-prompt",
    response_model=UserResponse,
    dependencies=[Depends(require_role("admin"))],
)
async def assign_system_prompt(user_id: uuid.UUID, body: SystemPromptAssignment, conn: DBConn):
    """Assign a system prompt to a user, or unassign with null — admin only."""
    prompt_uuid = None
    if body.system_prompt_id is not None:
        prompt_uuid = body.system_prompt_id
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, expiration_ts FROM system_prompt
                WHERE id = %s
                """,
                (prompt_uuid,),
            )
            prompt = await cur.fetchone()
        if not prompt:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="System prompt not found."
            )
        if prompt["expiration_ts"] is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot assign an expired system prompt.",
            )

    async with conn.cursor() as cur:
        await cur.execute(
            "UPDATE asap_user SET system_prompt_id = %s, updated_at = now() WHERE id = %s RETURNING id",
            (prompt_uuid, user_id),
        )
        updated = await cur.fetchone()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    row = await _fetch_user(conn, user_id)
    return _row_to_response(row)
