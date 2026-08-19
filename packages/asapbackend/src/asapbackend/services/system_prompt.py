"""
System prompt resolution.

Resolution order for a given user (re-evaluated on every conversation turn so
admin changes take effect immediately — database prompts are never cached):

  1. The user's explicitly assigned system prompt, if it is not expired.
  2. The most recent non-expired prompt marked is_default. At most one default
     should exist (the API enforces this), but the query is defensive against
     multiple defaults and picks the most recently modified.
  3. The bundled default_system_prompt.txt file. This is the only cached
     prompt — changing it requires a code deploy anyway.

Placeholder expansion ({schema}, {metadata}) uses str.replace, so prompts that
omit either placeholder — or contain other braced text — are handled safely.
"""

import uuid
from functools import lru_cache
from pathlib import Path

import psycopg

_DEFAULT_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "default_system_prompt.txt"

@lru_cache(maxsize=1)
def _load_default() -> str:
    """Read the bundled fallback prompt. Cached for the process lifetime."""
    return _DEFAULT_PROMPT_PATH.read_text(encoding="utf-8").strip()


async def get_system_prompt(conn: psycopg.AsyncConnection, user_id: str) -> str:
    """Resolve the effective system prompt for this user (see module docstring)."""
    async with conn.cursor() as cur:
        # 1. Explicit assignment, if unexpired.
        await cur.execute(
            """
            SELECT sp.prompt_text
            FROM asap_user u
            JOIN system_prompt sp ON sp.id = u.system_prompt_id
            WHERE u.id = %s
              AND (sp.expiration_ts IS NULL OR sp.expiration_ts > now())
            """,
            (uuid.UUID(user_id),),
        )
        row = await cur.fetchone()

        # 2. Most recent unexpired default prompt.
        if not row:
            await cur.execute(
                """
                SELECT prompt_text
                FROM system_prompt
                WHERE is_default = true
                  AND (expiration_ts IS NULL OR expiration_ts > now())
                ORDER BY COALESCE(modified_ts, create_ts) DESC
                LIMIT 1
                """
            )
            row = await cur.fetchone()

    # 3. Bundled file fallback.
    template = row["prompt_text"] if row else _load_default()
    return _inject_graph_context(template)


def _inject_graph_context(template: str) -> str:
    """Replace {schema} and {metadata} placeholders with live graph data.

    Uses str.replace rather than str.format so prompts missing either
    placeholder (or containing unrelated braces) never raise.
    """
    if "{schema}" not in template and "{metadata}" not in template:
        return template
    from asapbackend.mcp.client import get_cached_schema, get_cached_metadata
    return template.replace("{schema}", get_cached_schema()).replace(
        "{metadata}", get_cached_metadata()
    )
