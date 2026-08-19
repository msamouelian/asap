"""Within-collection merge: fold per-chunk graphs into one collection graph.

The LLM deduplicates within a chunk; this pass deduplicates across chunks of
the same collection. Inside a single collection, an identical normalized name
of the same entity type almost always denotes the same real-world entity, so
the merge is deterministic — the probabilistic machinery (and its
under-merge posture) is reserved for the cross-collection resolve phase.

This is also where evidence indices become provenance: every merged entity
and relation gets a source_uri list resolved from the surface's text units.
Indices stay on the artifact for audit.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _norm_name(name: str) -> str:
    """Normalization for same-collection identity: case, punctuation,
    whitespace, and a few archival name conventions (trailing dates)."""
    n = name.lower()
    n = re.sub(r"[.,;:'\"()\[\]]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    # "smith, john, 1850-1920" → drop a trailing life-dates segment
    n = re.sub(r"\s*\b\d{4}\s*-\s*(\d{4})?$", "", n).strip()
    return n


def _entity_key(e: dict[str, Any]) -> tuple[str, str, str]:
    return (e["type"], e.get("agent_type") or "", _norm_name(e["name"]))


def merge_chunk_graphs(
    chunk_graphs: list[dict[str, Any]], surface: dict[str, Any]
) -> dict[str, Any]:
    """Merge chunk graphs into {"entities": [...], "relations": [...]}.

    Merged entities carry collection-scoped ids (c1, c2, ...); relations are
    remapped onto them and deduplicated on (from, to, type).
    """
    uri_by_index = {u["i"]: u["source_uri"] for u in surface["texts"]}

    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    id_map: dict[tuple[int, str], str] = {}  # (chunk_no, chunk-local id) -> merged id

    for chunk_no, graph in enumerate(chunk_graphs):
        for e in graph.get("entities", []):
            key = _entity_key(e)
            if key not in merged:
                merged[key] = {
                    "id": f"c{len(merged) + 1}",
                    "type": e["type"],
                    **({"agent_type": e["agent_type"]} if e.get("agent_type") else {}),
                    "name": e["name"],
                    "alternate_names": [],
                    "evidence": [],
                    "confidence": 0.0,
                }
            m = merged[key]
            id_map[(chunk_no, e["id"])] = m["id"]

            # Fullest name wins; every other observed form is an alternate.
            for candidate in [e["name"], *(e.get("alternate_names") or [])]:
                if len(candidate) > len(m["name"]):
                    if m["name"] not in m["alternate_names"]:
                        m["alternate_names"].append(m["name"])
                    m["name"] = candidate
                elif candidate != m["name"] and candidate not in m["alternate_names"]:
                    m["alternate_names"].append(candidate)

            m["evidence"] = sorted(set(m["evidence"]) | set(e["evidence"]))
            m["confidence"] = max(m["confidence"], float(e.get("confidence") or 0.5))
            for field in ("description", "exists_from", "exists_to",
                          "event_type", "date"):
                val = str(e.get(field) or "").strip()
                if not val:
                    continue
                if not m.get(field):
                    m[field] = val
                elif m[field] != val and field in ("exists_from", "exists_to", "date"):
                    logger.warning(
                        "Conflicting %s for %r within collection: %r vs %r "
                        "(keeping the first)", field, m["name"], m[field], val,
                    )

    relations: dict[tuple[str, str, str], dict[str, Any]] = {}
    dangling = 0
    for chunk_no, graph in enumerate(chunk_graphs):
        for r in graph.get("relations", []):
            src = id_map.get((chunk_no, r["from"]))
            dst = id_map.get((chunk_no, r["to"]))
            if not src or not dst:
                dangling += 1
                continue
            if src == dst:  # name variants merged into one entity
                continue
            key = (src, dst, r["type"])
            if key not in relations:
                relations[key] = {
                    "from": src, "to": dst, "type": r["type"],
                    "evidence": [], "confidence": 0.0,
                }
            m = relations[key]
            m["evidence"] = sorted(set(m["evidence"]) | set(r["evidence"]))
            m["confidence"] = max(m["confidence"], float(r.get("confidence") or 0.5))
            for field in ("quote", "date", "nature"):
                val = str(r.get(field) or "").strip()
                if val and not m.get(field):
                    m[field] = val
    if dangling:
        logger.warning("Dropped %d relations with unmapped endpoints.", dangling)

    entities = list(merged.values())
    rel_list = list(relations.values())
    for item in [*entities, *rel_list]:
        item["source_uri"] = sorted({
            uri_by_index[i] for i in item["evidence"] if i in uri_by_index
        })
    logger.info(
        "Merged %d chunk graphs: %d entities, %d relations.",
        len(chunk_graphs), len(entities), len(rel_list),
    )
    return {"entities": entities, "relations": rel_list}
