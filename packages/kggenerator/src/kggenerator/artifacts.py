"""Postgres artifact store — the durable handoff between pipeline phases.

Extraction (the expensive LLM phase) checkpoints one row per collection;
resolve/load re-run cheaply from these rows without re-invoking the LLM.
One current artifact per collection (collection_uri is the key); --force
re-extracts in place.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from kggenerator import config

logger = logging.getLogger(__name__)

# Mirrored in helm/postgres/migrations/004_kg_artifacts.sql (and init.sql for
# fresh volumes); ensured here too so local dev runs never trip on ordering.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS kg_artifact (
    collection_uri   TEXT PRIMARY KEY,
    collection_title TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    status           TEXT NOT NULL,
    surface          JSONB,
    graph            JSONB,
    stats            JSONB,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS kg_resolution (
    id         SERIAL PRIMARY KEY,
    run_id     TEXT NOT NULL,
    graph      JSONB NOT NULL,
    decisions  JSONB NOT NULL,
    stats      JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class ArtifactStore:
    def __init__(self) -> None:
        self._conn = psycopg.connect(
            config.DATABASE_URL, autocommit=True, row_factory=dict_row
        )
        self._conn.execute(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ArtifactStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------

    def get(self, collection_uri: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT collection_uri, collection_title, run_id, status, stats, "
            "error, updated_at FROM kg_artifact WHERE collection_uri = %s",
            (collection_uri,),
        ).fetchone()
        return row

    def begin_extraction(
        self, run_id: str, collection_uri: str, title: str, surface: dict[str, Any]
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO kg_artifact
                (collection_uri, collection_title, run_id, status, surface,
                 graph, stats, error)
            VALUES (%s, %s, %s, 'extracting', %s, NULL, NULL, NULL)
            ON CONFLICT (collection_uri) DO UPDATE SET
                collection_title = EXCLUDED.collection_title,
                run_id = EXCLUDED.run_id,
                status = 'extracting',
                surface = EXCLUDED.surface,
                graph = NULL, stats = NULL, error = NULL,
                updated_at = now()
            """,
            (collection_uri, title, run_id, Jsonb(surface)),
        )

    def save_graph(
        self, collection_uri: str, graph: dict[str, Any], stats: dict[str, Any]
    ) -> None:
        self._conn.execute(
            "UPDATE kg_artifact SET status = 'extracted', graph = %s, "
            "stats = %s, error = NULL, updated_at = now() "
            "WHERE collection_uri = %s",
            (Jsonb(graph), Jsonb(stats), collection_uri),
        )

    def mark_failed(self, collection_uri: str, error: str) -> None:
        self._conn.execute(
            "UPDATE kg_artifact SET status = 'failed', error = %s, "
            "updated_at = now() WHERE collection_uri = %s",
            (error[:4000], collection_uri),
        )

    def load_graph(self, collection_uri: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT graph FROM kg_artifact "
            "WHERE collection_uri = %s AND status = 'extracted'",
            (collection_uri,),
        ).fetchone()
        graph = row["graph"] if row else None
        return json.loads(graph) if isinstance(graph, str) else graph

    def load_extracted_artifacts(self) -> list[dict[str, Any]]:
        """Every extracted artifact, with the collection date context the
        resolve phase uses for scoring (pulled from the stored surface)."""
        rows = self._conn.execute(
            "SELECT collection_uri, collection_title, graph, "
            "surface -> 'collection' ->> 'dates' AS collection_dates "
            "FROM kg_artifact WHERE status = 'extracted' "
            "ORDER BY collection_title"
        ).fetchall()
        for row in rows:
            if isinstance(row["graph"], str):
                row["graph"] = json.loads(row["graph"])
        return rows

    def save_resolution(
        self, run_id: str, graph: dict[str, Any],
        decisions: dict[str, Any], stats: dict[str, Any],
    ) -> None:
        self._conn.execute(
            "INSERT INTO kg_resolution (run_id, graph, decisions, stats) "
            "VALUES (%s, %s, %s, %s)",
            (run_id, Jsonb(graph), Jsonb(decisions), Jsonb(stats)),
        )

    def load_latest_resolution(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT run_id, graph, decisions, stats, created_at "
            "FROM kg_resolution ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and isinstance(row["graph"], str):
            row["graph"] = json.loads(row["graph"])
        return row
