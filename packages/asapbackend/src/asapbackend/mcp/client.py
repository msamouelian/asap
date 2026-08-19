"""
MCP HTTP client — speaks the MCP Streamable HTTP protocol (2025-03-26).

The graphdb-mcp server requires per-request Basic Auth using the Neo4j
database credentials (set via NEO4J_MCP_AUTH_USER / _PASSWORD env vars).

Tool definitions are fetched at startup and cached for the process lifetime.
Call refresh_tools() to reload (e.g. after an MCP server restart).
"""

import base64
import json
import logging

import httpx

from asapbackend.config import settings

log = logging.getLogger(__name__)

# Cached OpenAI-format tool list, populated by refresh_tools() at startup.
_tools_cache: list[dict] = []

# Graph context caches — injected into the system prompt template at startup.
_schema_cache: str = ""
_metadata_cache: str = ""


def _basic_auth_header(user: str, password: str) -> str:
    encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {encoded}"


def _neo4j_auth_header() -> str | None:
    if settings.neo4j_mcp_auth_user and settings.neo4j_mcp_auth_password:
        return _basic_auth_header(
            settings.neo4j_mcp_auth_user, settings.neo4j_mcp_auth_password
        )
    return None


def _mcp_url() -> str:
    return settings.neo4j_mcp_url.rstrip("/") + "/mcp"


def _request_headers(session_id: str | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    auth = _neo4j_auth_header()
    if auth:
        headers["Authorization"] = auth
    if session_id:
        headers["MCP-Session-Id"] = session_id
    return headers


async def _post(
    client: httpx.AsyncClient,
    method: str,
    params: dict | None = None,
    rpc_id: int = 1,
    session_id: str | None = None,
) -> tuple[dict, str | None]:
    """Send a JSON-RPC request and return (result_dict, session_id)."""
    payload: dict = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        payload["params"] = params

    resp = await client.post(
        _mcp_url(), json=payload, headers=_request_headers(session_id)
    )
    resp.raise_for_status()
    new_session_id = resp.headers.get("MCP-Session-Id", session_id)
    return resp.json(), new_session_id


def _to_openai_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get(
                "inputSchema", {"type": "object", "properties": {}}
            ),
        },
    }


async def refresh_tools(retries: int = 5, delay: float = 5.0) -> list[dict]:
    """
    Fetch the tool list from the MCP server and update the cache.

    Retries with a fixed delay to handle the common case where the MCP server
    pod is not yet ready when the backend starts up.
    """
    global _tools_cache
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                _, sid = await _post(
                    client,
                    "initialize",
                    {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "asapbackend", "version": "0.1.0"},
                    },
                )
                result, _ = await _post(client, "tools/list", session_id=sid)
                from asapbackend.tools.validator import get_allowed_tool_names
                allowed = get_allowed_tool_names()
                tools = [t for t in result.get("result", {}).get("tools", [])
                         if t["name"] in allowed]
                _tools_cache = [_to_openai_tool(t) for t in tools]
                log.info(
                    "MCP tools loaded (allowlist applied): %s",
                    [t["function"]["name"] for t in _tools_cache],
                )
                return _tools_cache
        except Exception as exc:
            if attempt < retries:
                log.warning("MCP tool load attempt %d/%d failed (%s) — retrying in %.0fs.", attempt, retries, exc, delay)
                import asyncio
                await asyncio.sleep(delay)
            else:
                log.warning("Could not load MCP tools after %d attempts: %s — proceeding without tools.", retries, exc)
                _tools_cache = []
    return _tools_cache


def get_cached_tools() -> list[dict]:
    return _tools_cache


def get_cached_schema() -> str:
    return _schema_cache


def get_cached_metadata() -> str:
    return _metadata_cache


async def refresh_graph_context(metadata_query: str) -> None:
    """Fetch schema and metadata from the graph and update the caches."""
    global _schema_cache, _metadata_cache
    try:
        _schema_cache = await call_tool("get-schema", {})
        log.info("Graph schema loaded (%d chars).", len(_schema_cache))
    except Exception as exc:
        log.warning("Could not load graph schema: %s", exc)

    try:
        _metadata_cache = await call_tool("read-cypher", {"query": metadata_query})
        log.info("Graph metadata loaded (%d chars).", len(_metadata_cache))
    except Exception as exc:
        log.warning("Could not load graph metadata: %s", exc)


async def call_tool(tool_name: str, arguments: dict) -> str:
    """Call an MCP tool and return its text result."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        _, sid = await _post(
            client,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "asapbackend", "version": "0.1.0"},
            },
        )
        result, _ = await _post(
            client,
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            session_id=sid,
        )

    content = result.get("result", {}).get("content", [])
    return "\n".join(
        c.get("text", "") for c in content if c.get("type") == "text"
    )
