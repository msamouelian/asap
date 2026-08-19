"""FastAPI auth dependencies."""

from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from asapbackend.auth.models import AuthUser
from asapbackend.auth.oidc import PyJWTError, provision_or_load_user, validate_token_claims
from asapbackend.database import get_db

_bearer = HTTPBearer()

DBConn = Annotated[psycopg.AsyncConnection, Depends(get_db)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
    conn: DBConn,
) -> AuthUser:
    """Validate the Bearer token, auto-provision the user, enforce active status."""
    try:
        claims = await validate_token_claims(credentials.credentials)
    except PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        )

    row = await provision_or_load_user(conn, claims)

    if row["status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been deactivated. Contact an administrator.",
        )

    return AuthUser(
        id=str(row["id"]),
        keycloak_id=row["keycloak_id"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        status=row["status"],
    )


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


def require_role(role: str):
    """Dependency factory — raises 403 if the caller lacks the required role."""
    async def _check(user: CurrentUser) -> AuthUser:
        if not user.has_role(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' required.",
            )
        return user
    return _check
