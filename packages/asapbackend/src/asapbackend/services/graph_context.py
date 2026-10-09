"""
Graph context for the system prompt: schema and metadata, minus the
document library.

The model never calls get-schema (removed from the tool allowlist); it gets
the schema and the metadata catalogue injected into its system prompt at
startup via the {schema} and {metadata} placeholders. Both payloads are
built here and both are scrubbed of every reference to the document
library — DocumentCollection, Document, DocumentChunk, the CONTAINS and
HAS_DOCUMENT_CHUNK relationships, their properties, and prose that mentions them — so the
model has no way to learn those nodes exist. tools/cypher_guard enforces
the same boundary on every query it tries to run.

Schema source: Neo4j's built-in db.schema.* procedures through the
backend's own driver. (The MCP server's get-schema depends on
apoc.meta.schema, which the procedure allowlist no longer loads.) Output
mirrors the shape the prompt was written against: a JSON list of
{"key": <label or type>, "value": {"type": "node"|"relationship",
"properties": {...}, "relationships": {...}}}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from typing import Any, Iterable

from asapbackend.tools.cypher_guard import DENIED_LABELS, DENIED_REL_TYPES

log = logging.getLogger(__name__)

# Whole-word, case-sensitive: "Document" the label, not "documenting" in
# prose. CONTAINS is handled structurally (relationship type / [:CONTAINS])
# because the word is also Cypher's string predicate.
_LABEL_TOKEN = re.compile(r"\b(" + "|".join(sorted(DENIED_LABELS, key=len, reverse=True)) + r")\b")
_REL_PATTERN = re.compile(r"\[:(" + "|".join(sorted(DENIED_REL_TYPES)) + r")\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.;])\s+")


def mentions_documents(text: str) -> bool:
    return bool(_LABEL_TOKEN.search(text) or _REL_PATTERN.search(text))


def offending_tokens(text: str) -> list[str]:
    """Every denied label/relationship token found in `text` (for checks/tests)."""
    return sorted(set(_LABEL_TOKEN.findall(text)) | set(_REL_PATTERN.findall(text)))


def scrub_text(text: str) -> str:
    """Drop every sentence/clause that mentions the document library."""
    kept = [s for s in _SENTENCE_SPLIT.split(text) if s and not mentions_documents(s)]
    return " ".join(kept).strip()


# ── Schema (from db.schema.* procedures) ────────────────────────────────────

def _norm_type(types: Iterable[str] | None) -> str:
    out: list[str] = []
    for t in types or []:
        t = t.replace(" NOT NULL", "").strip()
        if t.startswith("LIST"):
            t = "LIST"
        if t and t not in out:
            out.append(t)
    return "|".join(out) or "ANY"


def assemble_schema(
    node_props: list[dict[str, Any]],
    rel_props: list[dict[str, Any]],
    edges: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """
    Pure assembly, testable without a database.

    node_props: rows of db.schema.nodeTypeProperties (nodeLabels, propertyName, propertyTypes)
    rel_props:  rows of db.schema.relTypeProperties (relType, propertyName, propertyTypes)
    edges:      (relationship type, start label, end label) from db.schema.visualization
    """
    props_by_label: dict[str, dict[str, str]] = defaultdict(dict)
    for row in node_props:
        labels = [l for l in (row.get("nodeLabels") or []) if l not in DENIED_LABELS]
        name = row.get("propertyName")
        if not name:
            for l in labels:
                props_by_label.setdefault(l, {})
            continue
        for l in labels:
            props_by_label[l][name] = _norm_type(row.get("propertyTypes"))

    props_by_rel: dict[str, dict[str, str]] = defaultdict(dict)
    for row in rel_props:
        rel = str(row.get("relType") or "").strip(":`")
        if not rel or rel in DENIED_REL_TYPES:
            continue
        props_by_rel.setdefault(rel, {})
        if row.get("propertyName"):
            props_by_rel[rel][row["propertyName"]] = _norm_type(row.get("propertyTypes"))

    out_edges: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    in_edges: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    live_rels: set[str] = set()
    for rel, start, end in edges:
        if rel in DENIED_REL_TYPES or start in DENIED_LABELS or end in DENIED_LABELS:
            continue
        live_rels.add(rel)
        if end not in out_edges[start][rel]:
            out_edges[start][rel].append(end)
        if start not in in_edges[end][rel]:
            in_edges[end][rel].append(start)

    schema: list[dict[str, Any]] = []
    for label in sorted(props_by_label):
        rels: dict[str, Any] = {}
        for rel, ends in sorted(out_edges.get(label, {}).items()):
            rels[rel] = {"direction": "out", "labels": sorted(ends)}
        for rel, starts in sorted(in_edges.get(label, {}).items()):
            if rel not in rels:
                rels[rel] = {"direction": "in", "labels": sorted(starts)}
        value: dict[str, Any] = {"type": "node", "properties": dict(sorted(props_by_label[label].items()))}
        if rels:
            value["relationships"] = rels
        schema.append({"key": label, "value": value})

    for rel in sorted(live_rels | set(props_by_rel)):
        if rel in DENIED_REL_TYPES:
            continue
        value = {"type": "relationship"}
        if props_by_rel.get(rel):
            value["properties"] = dict(sorted(props_by_rel[rel].items()))
        schema.append({"key": rel, "value": value})
    return schema


def _fetch_schema_sync() -> list[dict[str, Any]]:
    from asapbackend.services.graph_docs import get_driver

    with get_driver().session() as s:
        node_props = [dict(r) for r in s.run(
            "CALL db.schema.nodeTypeProperties() "
            "YIELD nodeLabels, propertyName, propertyTypes RETURN nodeLabels, propertyName, propertyTypes"
        )]
        rel_props = [dict(r) for r in s.run(
            "CALL db.schema.relTypeProperties() "
            "YIELD relType, propertyName, propertyTypes RETURN relType, propertyName, propertyTypes"
        )]
        rec = s.run(
            "CALL db.schema.visualization() YIELD relationships "
            "RETURN [r IN relationships | [type(r), labels(startNode(r))[0], labels(endNode(r))[0]]] AS edges"
        ).single()
        edges = [tuple(e) for e in (rec["edges"] if rec else [])]
    return assemble_schema(node_props, rel_props, edges)


async def build_schema() -> str:
    """Schema JSON for the prompt, document library excluded."""
    schema = await asyncio.to_thread(_fetch_schema_sync)
    text = json.dumps(schema, separators=(",", ":"))
    bad = offending_tokens(text)
    if bad:  # cannot happen by construction; fail closed if it ever does
        log.error("Schema still references the document library (%s); withholding it.", bad)
        return ""
    return text


# ── Metadata (NodeSchema / PropertySchema / RelationshipSchema rows) ────────

def _split_labels(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def filter_metadata(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Drop the document library from the metadata catalogue and scrub prose
    that mentions it. Rows that still mention it after scrubbing are dropped
    (belt and braces), and the drop is logged.
    """
    kept: list[dict[str, Any]] = []
    for row in rows:
        stype = row.get("schema_type")
        d = dict(row.get("details") or {})

        if stype == "NodeSchema" and (d.get("label") in DENIED_LABELS or d.get("neo4j_label") in DENIED_LABELS):
            continue
        if stype == "PropertySchema" and (
            d.get("node_label") in DENIED_LABELS or str(d.get("id", "")).split(".")[0] in DENIED_LABELS
        ):
            continue
        if stype == "RelationshipSchema":
            if d.get("type") in DENIED_REL_TYPES:
                continue
            endpoints_ok = True
            for key in ("from_labels", "to_labels"):
                if key in d:
                    live = [l for l in _split_labels(d[key]) if l not in DENIED_LABELS]
                    if not live:
                        endpoints_ok = False
                    d[key] = ", ".join(live)
            if not endpoints_ok:
                continue

        for key, value in list(d.items()):
            if isinstance(value, str) and mentions_documents(value):
                d[key] = scrub_text(value)

        if mentions_documents(json.dumps(d)):
            log.warning("Metadata row still mentions the document library after scrubbing; dropped: %s",
                        d.get("label") or d.get("id") or d.get("type"))
            continue
        kept.append({**row, "details": d})
    return kept


def render_metadata(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, separators=(",", ":"), default=str)
