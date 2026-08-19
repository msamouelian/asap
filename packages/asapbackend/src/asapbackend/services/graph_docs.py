"""Scoped Neo4j access for the document library.

The backend's MCP access is read-only BY DESIGN — that boundary constrains
LLM-generated Cypher, which continues to flow through it. This module is the
one deliberate exception: direct driver access for application-code
operations on user-owned document nodes (DocumentCollection / Document /
DocumentChunk). It must never touch ArchivesSpace-derived labels.

All functions are synchronous (the Neo4j driver's session API); routers call
them via asyncio.to_thread.
"""

import logging
from typing import Any

from neo4j import Driver, GraphDatabase

from asapbackend.config import settings

log = logging.getLogger(__name__)

_driver: Driver | None = None


def _get_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


# ── Reads ─────────────────────────────────────────────────────────────────────

def list_visible_collections(user_id: str) -> list[dict[str, Any]]:
    """Collections the user may see: all shared + their own private ones.
    Includes archived and non-ready entries — the caller/UI filters by use."""
    with _get_driver().session() as s:
        result = s.run(
            """
            MATCH (dc:DocumentCollection)
            WHERE dc.visibility = 'shared' OR dc.uploaded_by = $user_id
            OPTIONAL MATCH (dc)-[:CONTAINS]->(d:Document)
            WITH dc, count(d) AS documents, sum(coalesce(d.chunk_count, 0)) AS chunks
            RETURN dc.id AS id, dc.title AS title, dc.description AS description,
                   dc.visibility AS visibility, dc.archived AS archived,
                   dc.status AS status, dc.uploaded_by AS uploaded_by,
                   dc.source_location AS source_location,
                   toString(dc.created_at) AS created_at,
                   documents, chunks
            ORDER BY dc.created_at DESC
            """,
            user_id=user_id,
        )
        return [dict(r) for r in result]


def get_collection(collection_id: str) -> dict[str, Any] | None:
    with _get_driver().session() as s:
        rec = s.run(
            """
            MATCH (dc:DocumentCollection {id: $id})
            RETURN dc.id AS id, dc.title AS title, dc.visibility AS visibility,
                   dc.archived AS archived, dc.status AS status,
                   dc.uploaded_by AS uploaded_by
            """,
            id=collection_id,
        ).single()
        return dict(rec) if rec else None


def get_collections_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    """Batch title/status lookup for scope listings."""
    if not ids:
        return []
    with _get_driver().session() as s:
        result = s.run(
            """
            MATCH (dc:DocumentCollection)
            WHERE dc.id IN $ids
            RETURN dc.id AS id, dc.title AS title, dc.visibility AS visibility,
                   dc.archived AS archived, dc.status AS status,
                   dc.uploaded_by AS uploaded_by
            """,
            ids=ids,
        )
        return [dict(r) for r in result]


def collection_chunk_ids(collection_id: str) -> list[str]:
    """All chunk ids of a collection — used for the used-in-conversation check."""
    with _get_driver().session() as s:
        result = s.run(
            """
            MATCH (:DocumentCollection {id: $id})-[:CONTAINS]->(:Document)-[:HAS_CHUNK]->(ch:DocumentChunk)
            RETURN ch.id AS id
            """,
            id=collection_id,
        )
        return [r["id"] for r in result]


def get_chunks_by_ids(ids: list[str]) -> list[dict[str, Any]]:
    """Chunk text + provenance for rehydration and UI display, in the order
    of the input ids (retrieval order is meaningful)."""
    if not ids:
        return []
    with _get_driver().session() as s:
        result = s.run(
            """
            MATCH (dc:DocumentCollection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(ch:DocumentChunk)
            WHERE ch.id IN $ids
            RETURN ch.id AS id, ch.text AS text, ch.page_no AS page_no,
                   ch.heading_path AS heading, ch.seq AS seq,
                   d.id AS document_id, d.file_name AS file_name,
                   dc.title AS collection
            """,
            ids=ids,
        )
        by_id = {r["id"]: dict(r) for r in result}
    return [by_id[i] for i in ids if i in by_id]


# ── Writes (document labels only) ────────────────────────────────────────────

def set_archived(collection_id: str, archived: bool) -> None:
    with _get_driver().session() as s:
        s.run(
            "MATCH (dc:DocumentCollection {id: $id}) SET dc.archived = $archived",
            id=collection_id, archived=archived,
        )


def delete_collection_tree(collection_id: str) -> None:
    """Hard delete a collection with its documents and chunks. Guards
    (ownership, used-in-conversation) are enforced by the router BEFORE this
    is called — this function only removes the subgraph."""
    with _get_driver().session() as s:
        s.run(
            """
            MATCH (dc:DocumentCollection {id: $id})
            OPTIONAL MATCH (dc)-[:CONTAINS]->(d:Document)
            OPTIONAL MATCH (d)-[:HAS_CHUNK]->(ch:DocumentChunk)
            DETACH DELETE dc, d, ch
            """,
            id=collection_id,
        )
    log.info("Hard-deleted document collection %s", collection_id)
