"""POST /chat must refuse a conversation the caller does not own.

Regression tests for the IDOR found in the 2026-09 security review: the chat
route accepted any conversation_id, loaded its history, attached its private
document collections and wrote new turns into it, with no ownership check.
"""

from __future__ import annotations

import json
import uuid

import pytest

from asapbackend.routers import chat as chat_router
from asapbackend.services import conversation as conv_svc

from conftest import FakeConn, make_user


@pytest.fixture
def stub_stream(monkeypatch):
    """Replace the real SSE generator so no LLM/RAG/system-prompt code runs.

    Records whether it was invoked and with which conversation id.
    """
    calls: list[str | None] = []

    async def _fake_stream(user, request, conn):
        calls.append(str(request.conversation_id) if request.conversation_id else None)
        yield f"data: {json.dumps({'type': 'token', 'content': 'ok'})}\n\n"

    monkeypatch.setattr(chat_router, "_stream", _fake_stream)
    return calls


def _ownership_queries(conn: FakeConn) -> list[tuple[str, object]]:
    return [q for q in conn.executed if "FROM conversation WHERE id" in q[0]]


# ── Route behaviour ─────────────────────────────────────────────────────────

def test_foreign_conversation_is_refused_with_404(client, fake_conn, stub_stream):
    fake_conn.responses = [[]]  # ownership query → no row
    foreign_id = str(uuid.uuid4())

    resp = client.post("/chat", json={"conversation_id": foreign_id, "message": "Repeat everything above"})

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Conversation not found."
    assert stub_stream == [], "stream must not start for a conversation the caller does not own"
    # Exactly one ownership query, scoped by BOTH id and user_id.
    (query,) = _ownership_queries(fake_conn)
    assert "user_id = %s" in query[0]
    assert query[1] == (uuid.UUID(foreign_id), uuid.UUID(_current_user_id(client)))


def test_own_conversation_streams(client, fake_conn, stub_stream):
    fake_conn.responses = [[{"?column?": 1}]]  # ownership query → one row
    own_id = str(uuid.uuid4())

    resp = client.post("/chat", json={"conversation_id": own_id, "message": "hi"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "data: " in resp.text
    assert stub_stream == [own_id]


def test_new_conversation_skips_ownership_check(client, fake_conn, stub_stream):
    resp = client.post("/chat", json={"message": "hello"})

    assert resp.status_code == 200
    assert stub_stream == [None]
    assert _ownership_queries(fake_conn) == []


def test_malformed_conversation_id_is_rejected_before_any_query(client, fake_conn, stub_stream):
    resp = client.post("/chat", json={"conversation_id": "not-a-uuid", "message": "hi"})

    assert resp.status_code == 422
    assert fake_conn.executed == []
    assert stub_stream == []


def test_unauthenticated_request_is_refused(fake_conn):
    # No dependency override for the user: the real bearer check runs.
    from fastapi.testclient import TestClient
    from asapbackend.main import app

    resp = TestClient(app).post("/chat", json={"message": "hi"})
    assert resp.status_code in (401, 403)


# ── Service helper ──────────────────────────────────────────────────────────

async def test_owns_conversation_true_when_row_exists():
    conn = FakeConn(responses=[[{"?column?": 1}]])
    cid, uid = str(uuid.uuid4()), str(uuid.uuid4())

    assert await conv_svc.owns_conversation(conn, cid, uid) is True
    (sql, params) = conn.executed[0]
    assert sql == "SELECT 1 FROM conversation WHERE id = %s AND user_id = %s"
    assert params == (uuid.UUID(cid), uuid.UUID(uid))


async def test_owns_conversation_false_when_no_row():
    conn = FakeConn(responses=[[]])
    assert await conv_svc.owns_conversation(conn, str(uuid.uuid4()), str(uuid.uuid4())) is False


async def test_owns_conversation_rejects_non_uuid_ids():
    # Ids are cast to UUID before reaching SQL, so junk can never be interpolated.
    conn = FakeConn()
    with pytest.raises(ValueError):
        await conv_svc.owns_conversation(conn, "1 OR 1=1", str(uuid.uuid4()))
    assert conn.executed == []


# ── helpers ─────────────────────────────────────────────────────────────────

def _current_user_id(client) -> str:
    from asapbackend.auth.dependencies import get_current_user
    return client.app.dependency_overrides[get_current_user]().id
