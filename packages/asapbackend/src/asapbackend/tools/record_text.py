"""Backend-native get-record-text tool: the full text surface of one record.

Given a record's ArchivesSpace uri (as returned in hybrid-search results),
returns everything textual about that record in one shot: identity
properties, archival context (owning collection, linked agents), and the
FULL content of every attached note. This collapses the drill-down pattern
observed in evaluation (hybrid-search → CONTAINS query to recover the exact
title → note fetch, three-plus calls with a transcription step at each hop)
into a single call keyed by an identifier the model copies verbatim.

The record type is inferred from the uri path — /agents/... is an Agent,
.../resources/N a Collection, .../archival_objects/N an ArchivalObject —
so the tool has exactly one argument. Unknown uri shapes are rejected
(fail-closed: the model cannot fetch what it cannot name precisely).

Executes via the MCP read-cypher tool, inheriting its read-only guarantees.
"""

import json
import logging
import re

from asapbackend import mcp

log = logging.getLogger(__name__)

TOOL_NAME = "get-record-text"

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Fetch the complete text surface of ONE record by its uri: all "
            "identity fields, its archival context (owning collection, "
            "linked agents), and the FULL content of every note attached to "
            "it. Use this as the READ step after hybrid-search: pick the "
            "most promising 1-3 results and fetch each one's text with this "
            "tool, then answer from what the notes actually say. Works for "
            "collections, archival objects, agents, accessions, and digital "
            "objects. Far better than writing CONTAINS queries to re-find a "
            "record you already see in search results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "uri": {
                    "type": "string",
                    "description": (
                        "The record's uri exactly as shown in the hybrid-"
                        "search uri column, e.g. '/repositories/11/"
                        "archival_objects/2479794' or '/agents/people/4049'."
                    ),
                },
            },
            "required": ["uri"],
        },
    },
}

# uri path fragment → Neo4j label. Fail-closed: anything else is rejected.
_URI_LABELS = [
    (re.compile(r"^/agents/(people|corporate_entities|families|software)/\d+$"), "Agent"),
    (re.compile(r"^/repositories/\d+/resources/\d+$"), "Collection"),
    (re.compile(r"^/repositories/\d+/archival_objects/\d+$"), "ArchivalObject"),
    (re.compile(r"^/repositories/\d+/accessions/\d+$"), "Accession"),
    (re.compile(r"^/repositories/\d+/digital_objects/\d+$"), "DigitalObject"),
]

# Properties never worth model context in a READ-for-facts call: vectors,
# duplicated identifiers, and system audit fields (staff-attribution
# questions go through Cypher on creating_user/modifying_user directly).
_SKIP_PROPS = {
    "embedding", "uri", "aspace_url",
    "create_time", "user_mtime", "system_mtime", "neo4j_created_at",
    "creating_user", "modifying_user", "lock_version",
    "finding_aid_script", "finding_aid_language", "finding_aid_filing_title",
}

# Total response budget: keeps a note-heavy collection from flooding the
# context window. Notes are included in full until the budget runs out.
MAX_CHARS = 15000


def _label_for(uri: str) -> str | None:
    for pattern, label in _URI_LABELS:
        if pattern.match(uri):
            return label
    return None


async def _read(query: str, **params) -> list[dict]:
    raw = await mcp.client.call_tool(
        "read-cypher", {"query": query, "params": params}
    )
    try:
        rows = json.loads(raw)
        return rows if isinstance(rows, list) else []
    except (json.JSONDecodeError, TypeError):
        log.warning("get-record-text: non-JSON read-cypher result.")
        return []


def _fmt_props(props: dict) -> list[str]:
    lines = []
    for k in sorted(props):
        v = props[k]
        if k in _SKIP_PROPS or v is None or v == "" or v == []:
            continue
        s = ", ".join(map(str, v)) if isinstance(v, list) else str(v)
        if len(s) > 300:
            s = s[:300] + "…"
        lines.append(f"- {k}: {s}")
    return lines


async def run(uri: str) -> str:
    uri = (uri or "").strip()
    label = _label_for(uri)
    if label is None:
        return (
            f"Unrecognized uri: {uri!r}. Pass a uri exactly as returned by "
            "hybrid-search (e.g. '/repositories/11/archival_objects/123' or "
            "'/agents/people/456') — never construct or guess one."
        )

    rows = await _read(
        f"MATCH (n:{label} {{uri: $uri}}) RETURN properties(n) AS props, "
        "n.aspace_url AS url",
        uri=uri,
    )
    if not rows:
        return f"No {label} found with uri {uri}. Use the uri exactly as search returned it."
    props, url = rows[0]["props"], rows[0]["url"]

    name = props.get("title") or props.get("display_name") or uri
    out = [f"# {label}: {name}", f"uri: {uri}"]
    if url:
        out.append(f"link (cite this): {url}")
    out.extend(_fmt_props(props))

    # ── Archival context ────────────────────────────────────────────────
    if label == "ArchivalObject":
        ctx = await _read(
            "MATCH (c:Collection)-[:HAS_PART*1..]->(n:ArchivalObject {uri: $uri}) "
            "RETURN c.title AS t, c.aspace_url AS u LIMIT 1", uri=uri)
        if ctx:
            out.append(f"\nIn collection: {ctx[0]['t']} ({ctx[0]['u']})")
    if label in ("Collection", "ArchivalObject", "Accession", "DigitalObject"):
        agents = await _read(
            f"MATCH (n:{label} {{uri: $uri}})-[r:CREATED_BY|HAS_SUBJECT]->(a:Agent) "
            "RETURN type(r) AS rel, a.display_name AS name, a.uri AS auri LIMIT 15",
            uri=uri)
        if agents:
            out.append("\nLinked agents:")
            out.extend(f"- {a['rel']}: {a['name']} (uri {a['auri']})" for a in agents)
    if label == "Agent":
        related = await _read(
            "MATCH (a:Agent {uri: $uri})-[r]-(b:Agent) "
            "WHERE b.display_name IS NOT NULL "
            "RETURN DISTINCT type(r) AS rel, b.display_name AS name LIMIT 15",
            uri=uri)
        if related:
            out.append("\nRelated agents:")
            out.extend(f"- {x['rel']}: {x['name']}" for x in related)
        linked = await _read(
            "MATCH (rec)-[r:CREATED_BY|HAS_SUBJECT]->(a:Agent {uri: $uri}) "
            "RETURN labels(rec)[0] AS lbl, rec.title AS t, rec.aspace_url AS u "
            "ORDER BY lbl LIMIT 10", uri=uri)
        if linked:
            out.append("\nLinked records (creator/subject of):")
            out.extend(f"- [{x['lbl']}] {x['t']} ({x['u']})" for x in linked)

    # ── Full notes ───────────────────────────────────────────────────────
    notes = await _read(
        f"MATCH (n:{label} {{uri: $uri}})-[:HAS_NOTE]->(note:Note) "
        "WHERE note.content IS NOT NULL AND note.content <> '' "
        "RETURN note.type AS type, note.label AS lab, note.content AS content "
        "ORDER BY size(note.content) DESC", uri=uri)
    if notes:
        out.append(f"\n## Notes ({len(notes)}) — full text. Notes have no "
                   "URL of their own; cite the record link above.")
        for nt in notes:
            header = nt["lab"] or nt["type"]
            out.append(f"\n### {header} ({nt['type']})")
            out.append(nt["content"])
    else:
        out.append("\n(no notes with content on this record)")

    text = "\n".join(out)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + (
            "\n\n[TRUNCATED — this record's notes exceed the response "
            "budget. Ask a read-cypher query scoped to the specific note "
            "type you need for the remainder.]"
        )
    return text
