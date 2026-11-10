"""
neo4j/mcp v2 HTTP contract: database in the path, Bolt URI in a header,
credentials per request. A regression here crash-loops nothing but makes
every chat tool call fail with a 404 or 400 from the MCP server.
"""

import base64

from asapbackend.config import settings
from asapbackend.mcp import client as mcp


def test_endpoint_puts_the_database_in_the_path(monkeypatch):
    monkeypatch.setattr(settings, "neo4j_mcp_url", "http://graphdb-mcp:8080/")
    monkeypatch.setattr(settings, "neo4j_mcp_database", "neo4j")
    assert mcp._mcp_url() == "http://graphdb-mcp:8080/db/neo4j/mcp"


def test_headers_carry_uri_auth_and_session(monkeypatch):
    monkeypatch.setattr(settings, "neo4j_uri", "bolt://graphdb:7687")
    monkeypatch.setattr(settings, "neo4j_mcp_auth_user", "neo4j")
    monkeypatch.setattr(settings, "neo4j_mcp_auth_password", "pw")
    h = mcp._request_headers(session_id="abc")
    assert h["X-Neo4j-MCP-URI"] == "bolt://graphdb:7687"
    assert h["Authorization"] == "Basic " + base64.b64encode(b"neo4j:pw").decode()
    assert h["MCP-Session-Id"] == "abc"
    assert h["Accept"] == "application/json, text/event-stream"


def test_no_auth_header_when_credentials_unset(monkeypatch):
    monkeypatch.setattr(settings, "neo4j_mcp_auth_user", "")
    monkeypatch.setattr(settings, "neo4j_mcp_auth_password", "")
    assert "Authorization" not in mcp._request_headers()
