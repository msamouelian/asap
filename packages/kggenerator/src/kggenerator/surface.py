"""Text-surface builder: gather one collection's narrative text from the
extracted graph into an indexed JSON document.

Every text unit carries an integer index `i` and the ArchivesSpace URI of the
record it came from. The extraction LLM cites evidence as indices, never
URIs — kggenerator maps indices back to URIs programmatically (fail-closed
provenance; see docs/kg-conventions.md). Notes have no URI of their own, so
a note's unit carries the URI of its owning parent (collection, archival
object, or agent).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from neo4j import GraphDatabase

from kggenerator import config

logger = logging.getLogger(__name__)

# Note types with narrative content worth mining for entities and relations.
# Deliberately excludes processinfo (repository staff names — extracting the
# archivist as an InferredAgent would be a category error), physical
# description types, and access/use boilerplate.
NARRATIVE_NOTE_TYPES = [
    "bioghist",
    "note_bioghist",
    "abstract",
    "scopecontent",
    "custodhist",
    "acqinfo",
    "odd",
    "relatedmaterial",
    "separatedmaterial",
]


class GraphReader:
    """Read-only access to the extracted graph."""

    def __init__(self) -> None:
        self._driver = GraphDatabase.driver(
            config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
        )

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "GraphReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def run(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            return [r.data() for r in session.run(cypher, **params)]


def resolve_collections(reader: GraphReader, ead_ids: list[str]) -> list[dict[str, Any]]:
    """Resolve pilot ead_ids to Collection nodes, failing loudly.

    ead_id is the durable identifier (uniqueness-constrained in the graph);
    titles are deliberately NOT used for matching — they change under
    normal archival editing. A missing ead_id aborts the run before any LLM
    spend: it means the collection was deleted or unpublished in
    ArchivesSpace, which a human should look at.
    """
    rows = reader.run(
        "MATCH (c:Collection) WHERE c.ead_id IN $eads "
        "RETURN c.ead_id AS ead_id, c.uri AS uri, c.title AS title, "
        "c.date_inclusive_expression AS date_expression, "
        "c.date_inclusive_begin AS date_begin, "
        "c.date_inclusive_end AS date_end",
        eads=ead_ids,
    )
    by_ead = {r["ead_id"]: r for r in rows}
    missing = [e for e in ead_ids if e not in by_ead]
    if missing:
        for ead in missing:
            logger.error("Collection not found for ead_id %r — deleted or "
                         "unpublished in ArchivesSpace?", ead)
        raise SystemExit(f"{len(missing)} pilot collection(s) not found — aborting.")
    resolved = [by_ead[e] for e in ead_ids]
    for c in resolved:
        logger.info("Pilot collection %s = %r", c["ead_id"], c["title"])
    return resolved


def _split_long_text(text: str, limit: int) -> list[str]:
    """Split oversized text on paragraph (then sentence) boundaries."""
    if len(text) <= limit:
        return [text]
    paragraphs = re.split(r"\n\s*\n", text)
    parts: list[str] = []
    buf = ""
    for p in paragraphs:
        while len(p) > limit:  # a single monster paragraph: split on sentences
            cut = p.rfind(". ", 0, limit)
            cut = cut + 1 if cut > 0 else limit
            if buf:
                parts.append(buf)
                buf = ""
            parts.append(p[:cut].strip())
            p = p[cut:].strip()
        if len(buf) + len(p) + 2 > limit and buf:
            parts.append(buf)
            buf = p
        else:
            buf = f"{buf}\n\n{p}" if buf else p
    if buf:
        parts.append(buf)
    return [p for p in parts if p.strip()]


def build_surface(reader: GraphReader, collection: dict[str, Any]) -> dict[str, Any]:
    """Assemble the indexed text surface for one collection."""
    uri = collection["uri"]
    date = collection.get("date_expression") or "-".join(
        v for v in (collection.get("date_begin"), collection.get("date_end")) if v
    )
    header = {"uri": uri, "title": collection["title"], "dates": date or ""}

    units: list[dict[str, Any]] = []

    def add(source_uri: str, kind: str, text: str, label: str = "",
            series: str | None = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        parts = _split_long_text(text, config.TEXT_SPLIT_CHAR_LIMIT)
        for n, part in enumerate(parts):
            unit: dict[str, Any] = {
                "i": len(units),
                "source_uri": source_uri,
                "kind": kind,
                "text": part,
            }
            # Multilevel description: an item inherits context from its
            # series ("Letter books—G. Twichell, superintendent" under
            # "Series II: Western Rail-Road Corporation records" means
            # superintendent of the Western). Without this field the model
            # can only guess the owning organization from chunk
            # neighborhood — observed misattributions 2026-08-21.
            if series:
                unit["series"] = series
            part_label = label
            if len(parts) > 1:
                part_label = f"{label or kind} (part {n + 1}/{len(parts)})"
            if part_label:
                unit["label"] = part_label
            units.append(unit)

    # 1. Collection title and narrative notes.
    add(uri, "collection_title", collection["title"])
    for row in reader.run(
        "MATCH (c:Collection {uri: $uri})-[:HAS_NOTE]->(n:Note) "
        "WHERE n.type IN $types AND n.content IS NOT NULL "
        "RETURN n.type AS type, n.label AS label, n.content AS content "
        "ORDER BY n.persistent_id",
        uri=uri, types=NARRATIVE_NOTE_TYPES,
    ):
        add(uri, f"collection_note:{row['type']}", row["content"], row["label"] or "")

    # 2. Archival object titles (entity-rich: correspondent names, companies,
    #    places) and their narrative notes, in hierarchy order. Each unit
    #    carries its top-level ancestor (the series) so extraction can use
    #    multilevel description instead of guessing context.
    for row in reader.run(
        "MATCH (c:Collection {uri: $uri})-[:HAS_PART]->(top:ArchivalObject) "
        "MATCH (top)-[:HAS_PART*0..]->(ao:ArchivalObject) "
        "RETURN DISTINCT ao.uri AS uri, ao.title AS title, "
        "CASE WHEN top = ao THEN NULL ELSE top.title END AS series "
        "ORDER BY ao.uri",
        uri=uri,
    ):
        add(row["uri"], "ao_title", row["title"], series=row["series"])
    for row in reader.run(
        "MATCH (c:Collection {uri: $uri})-[:HAS_PART]->(top:ArchivalObject) "
        "MATCH (top)-[:HAS_PART*0..]->(ao:ArchivalObject)-[:HAS_NOTE]->(n:Note) "
        "WHERE n.type IN $types AND n.content IS NOT NULL "
        "RETURN DISTINCT ao.uri AS uri, n.type AS type, n.label AS label, "
        "n.content AS content, "
        "CASE WHEN top = ao THEN NULL ELSE top.title END AS series "
        "ORDER BY ao.uri",
        uri=uri, types=NARRATIVE_NOTE_TYPES,
    ):
        add(row["uri"], f"ao_note:{row['type']}", row["content"],
            row["label"] or "", series=row["series"])

    # 3. Archivist-linked agents (strong anchors) and their narrative notes.
    #    The display name lives on the Agent record, so the agent's own uri is
    #    the source — which is also what twin matching consumes later.
    for row in reader.run(
        # *0.. includes the collection node itself alongside its descendants.
        "MATCH (c:Collection {uri: $uri})-[:HAS_PART*0..]->(p) "
        "MATCH (p)-[r:CREATED_BY|HAS_SUBJECT]->(a:Agent) "
        "RETURN DISTINCT a.uri AS uri, a.display_name AS display_name, "
        "type(r) AS role ORDER BY a.uri",
        uri=uri,
    ):
        role = "creator" if row["role"] == "CREATED_BY" else "subject"
        add(row["uri"], f"linked_agent_{role}",
            f"Archivist-linked {role} agent: {row['display_name']}")
    for row in reader.run(
        "MATCH (c:Collection {uri: $uri})-[:HAS_PART*0..]->(p) "
        "MATCH (p)-[:CREATED_BY|HAS_SUBJECT]->(a:Agent)-[:HAS_NOTE]->(n:Note) "
        "WHERE n.type IN $types AND n.content IS NOT NULL "
        "RETURN DISTINCT a.uri AS uri, a.display_name AS display_name, "
        "n.type AS type, n.content AS content ORDER BY a.uri",
        uri=uri, types=NARRATIVE_NOTE_TYPES,
    ):
        add(row["uri"], f"agent_note:{row['type']}", row["content"],
            f"About {row['display_name']}")

    logger.info(
        "Surface for %r: %d text units (%d chars).",
        collection["title"], len(units), sum(len(u["text"]) for u in units),
    )
    return {"collection": header, "texts": units}
