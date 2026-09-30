"""Shared fixtures for asapbackend tests.

Tests run without Postgres, Neo4j, Keycloak or an LLM. The FastAPI app is
exercised through TestClient with two dependencies overridden:

* ``get_current_user`` → a fixed AuthUser (no token validation);
* ``get_db`` → a FakeConn that answers scripted SQL.

The app's lifespan (pool, MCP discovery) is NOT started: TestClient is used
without a context manager, so nothing external is contacted.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from asapbackend.auth.dependencies import get_current_user
from asapbackend.auth.models import AuthUser
from asapbackend.database import get_db
from asapbackend.main import app


@dataclass
class FakeCursor:
    conn: "FakeConn"
    _rows: list[dict] = field(default_factory=list)

    async def execute(self, sql: str, params: Any = None) -> "FakeCursor":
        self.conn.executed.append((" ".join(sql.split()), params))
        self._rows = list(self.conn.next_rows())
        return self

    async def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[dict]:
        return list(self._rows)


@dataclass
class FakeConn:
    """Minimal stand-in for psycopg.AsyncConnection.

    ``responses`` is consumed in order, one entry per executed statement;
    each entry is the list of rows that statement returns. When the script
    runs out, statements return no rows.
    """

    responses: list[list[dict]] = field(default_factory=list)
    executed: list[tuple[str, Any]] = field(default_factory=list)

    def next_rows(self) -> list[dict]:
        return self.responses.pop(0) if self.responses else []

    def cursor(self):
        conn = self

        @asynccontextmanager
        async def _cur():
            yield FakeCursor(conn)

        return _cur()

    async def execute(self, sql: str, params: Any = None) -> FakeCursor:
        return await FakeCursor(self).execute(sql, params)

    async def commit(self) -> None:  # autocommit in production; no-op here
        pass


def make_user(role: str = "user", user_id: str | None = None) -> AuthUser:
    uid = user_id or str(uuid.uuid4())
    return AuthUser(
        id=uid,
        keycloak_id=f"kc-{uid}",
        email=f"{uid[:8]}@example.org",
        full_name="Test User",
        role=role,
        status="active",
    )


@pytest.fixture
def fake_conn() -> FakeConn:
    return FakeConn()


@pytest.fixture
def current_user() -> AuthUser:
    return make_user()


@pytest.fixture
def client(fake_conn: FakeConn, current_user: AuthUser):
    async def _db():
        yield fake_conn

    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
