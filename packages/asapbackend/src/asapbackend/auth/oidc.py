"""OIDC JWT validation and user auto-provisioning via Keycloak JWKS."""

import asyncio
import logging

import jwt
import psycopg
from jwt.exceptions import PyJWTError  # noqa: F401 — re-exported for callers

from asapbackend.config import settings

logger = logging.getLogger(__name__)

_jwks_client: jwt.PyJWKClient | None = None


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(settings.oidc_jwks_url, cache_keys=True)
    return _jwks_client


async def validate_token_claims(token: str) -> dict:
    """Validate JWT signature via Keycloak JWKS and return the claims dict."""
    client = _get_jwks_client()
    signing_key = await asyncio.to_thread(client.get_signing_key_from_jwt, token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        options={
            "verify_aud": False,
            # Issuer URL seen by the browser (http://keycloak.localhost/realms/asap)
            # differs from the internal service URL the backend uses for JWKS.
            # Signature validation is sufficient — issuer check is skipped.
            "verify_iss": False,
        },
    )


def _role_from_claims(claims: dict) -> str:
    """Derive a single role from the asap_roles JWT claim.

    'admin' takes precedence; anything else defaults to 'user'.
    """
    roles = claims.get(settings.oidc_role_claim, [])
    if isinstance(roles, str):
        roles = [roles]
    return "admin" if "admin" in roles else "user"


async def provision_or_load_user(
    conn: psycopg.AsyncConnection, claims: dict
) -> dict:
    """Return the asap_user row, creating or updating it on every login.

    - First login: INSERT with role derived from JWT, status defaults to 'active'.
    - Subsequent logins: UPDATE email/full_name/role to stay in sync with Keycloak.
      Status is NOT updated here — admins control it via the ASAP UI.

    Returns a dict with keys: id, keycloak_id, email, full_name, role, status.
    """
    keycloak_id: str = claims["sub"]
    email: str = claims.get("email", "")
    full_name: str = (
        claims.get("name")
        or claims.get("preferred_username")
        or keycloak_id
    )
    role: str = _role_from_claims(claims)

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO asap_user (keycloak_id, email, full_name, role)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (keycloak_id) DO UPDATE
                SET email      = EXCLUDED.email,
                    full_name  = EXCLUDED.full_name,
                    role       = EXCLUDED.role,
                    updated_at = now()
            RETURNING id, keycloak_id, email, full_name, role, status
            """,
            (keycloak_id, email, full_name, role),
        )
        row = await cur.fetchone()

    logger.debug(
        "User provisioned/loaded: keycloak_id=%s role=%s status=%s",
        keycloak_id, row["role"], row["status"],
    )
    return dict(row)
