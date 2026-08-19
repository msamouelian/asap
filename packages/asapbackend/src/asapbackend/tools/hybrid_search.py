"""Backend-native hybrid-search tool.

Runs the canned hybrid topic-search Cypher (semantic note chunks + semantic
collection titles + semantic agent names + full-text word matches, fused with
weighted reciprocal rank fusion) against Neo4j via the MCP read-cypher tool.

Result granularity: one row per matched RECORD, at the level where the match
occurred — a note match returns the note's direct parent (Collection,
ArchivalObject, Agent, or DigitalObject); a title/name/provenance match
returns that record itself. ArchivalObject rows carry their owning collection
as linked context.

This exists because the query is ~90 lines: asking a small LLM to copy it
verbatim into a JSON tool argument fails — the model gets lost escaping the
string and falls back to hallucinating results. As a native tool the LLM only
emits {"topic": "...", "limit": N}; the Cypher lives here, parameterised, and
never passes through the model. The result is likewise returned as a finished
markdown table so the model never transcribes cells.
"""

import json
import logging
import re

from asapbackend import mcp

log = logging.getLogger(__name__)

TOOL_NAME = "hybrid-search"

# OpenAI-format definition, merged with the MCP tool list in llm/agent.py.
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Hybrid discovery search over the archival graph. Finds records "
            "ABOUT a topic OR connected to a person/organization by combining "
            "semantic search (note chunks, collection titles, archival object "
            "titles, agent names) with fuzzy full-text word matching, fused "
            "with weighted reciprocal rank fusion. Returns a ranked markdown "
            "table of matched records — collections, archival objects, "
            "agents, accessions, or digital objects — each at the level where "
            "the match occurred. Use for ANY question that asks to find "
            "materials: topical questions AND entity-anchored ones "
            "(correspondence with X, photographs of X, records about X). "
            "Strictly outperforms hand-written full-text queries for "
            "discovery; use raw full-text only to verify exact strings or "
            "fetch known items. Each result row includes the record's uri — "
            "pass it to get-record-text to read that record's full notes "
            "instead of writing CONTAINS queries to re-find it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": (
                        "What to search for. Combine the ENTITY and the FACT "
                        "sought in one topic — 'Chester Barnard "
                        "correspondents', 'Edwin Land resignation' — never "
                        "just the bare name: the extra terms are what rank "
                        "the relevant records to the top."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Maximum records to return. Default 10 — right for "
                        "discovery and factual questions. Raise it (up to "
                        "200) ONLY when the user asks to enumerate ALL "
                        "records on a topic."
                    ),
                },
            },
            "required": ["topic"],
        },
    },
}

# The canned query. Parameters:
#   $topic       — raw topic text, embedded for the semantic branches
#   $luceneQuery — phrase-boosted Lucene query built by _lucene_query()
#   $limit       — final row cap
HYBRID_CYPHER = """
WITH $topic AS query
WITH query, toFloatList(ai.text.embed(query, 'OpenAI',
     { token: 'dummy-token', model: 'BAAI/bge-small-en-v1.5' })) AS qv

// Branch 1: semantic over note chunks. The result row is the note's direct
// parent (Collection, ArchivalObject, Agent, or DigitalObject); for AO
// parents the owning collection is carried as context.
CALL (qv) {
  MATCH (chunk:NoteChunk)
    SEARCH chunk IN (VECTOR INDEX note_chunk_embedding FOR qv LIMIT 150)
    SCORE AS s
  MATCH (rec)-[:HAS_NOTE]->(note:Note)-[:HAS_CHUNK]->(chunk)
  OPTIONAL MATCH (col:Collection)-[:HAS_PART*]->(rec)
  WITH rec, col, s, 'note (' + note.type + ')' AS source, left(chunk.text, 150) AS excerpt
  ORDER BY s DESC
  WITH rec, collect({s: s, source: source, excerpt: excerpt, col: col})[0] AS m
  ORDER BY m.s DESC
  RETURN collect({rec: rec, m: m}) AS chunkHits
}

// Branch 2: semantic over collection titles — the collection is the row
CALL (qv) {
  MATCH (c:Collection)
    SEARCH c IN (VECTOR INDEX collection_embedding FOR qv LIMIT 50)
    SCORE AS s
  WITH c, s ORDER BY s DESC
  RETURN collect({rec: c, m: {s: s, source: 'collection title', excerpt: c.title, col: NULL}}) AS titleHits
}

// Branch 3: semantic over agent names — the agent is the row
CALL (qv) {
  MATCH (a:Agent)
    SEARCH a IN (VECTOR INDEX agent_embedding FOR qv LIMIT 25)
    SCORE AS s
  WITH a, s ORDER BY s DESC
  RETURN collect({rec: a, m: {s: s, source: 'agent name', excerpt: a.display_name, col: NULL}}) AS agentHits
}

// Branch 5: semantic over archival object titles — the AO is the row
CALL (qv) {
  MATCH (ao:ArchivalObject)
    SEARCH ao IN (VECTOR INDEX archival_object_embedding FOR qv LIMIT 150)
    SCORE AS s
  OPTIONAL MATCH (col:Collection)-[:HAS_PART*]->(ao)
  WITH ao, col, s ORDER BY s DESC
  RETURN collect({rec: ao, m: {s: s, source: 'archival object title', excerpt: ao.title, col: col}}) AS aoHits
}

// Branch 4: full-text word matches anywhere (phrase-boosted). A note match
// resolves to the note's parent; other matches are the record itself.
// ($luceneQuery is a parameter, visible inside the subquery without import.)
CALL () {
  CALL db.index.fulltext.queryNodes('free_text_index', $luceneQuery) YIELD node, score
  OPTIONAL MATCH (owner)-[:HAS_NOTE]->(node)
  WITH coalesce(owner, node) AS rec, node, score
  OPTIONAL MATCH (col:Collection)-[:HAS_PART*]->(rec)
  WITH rec, col, node, score,
       CASE
         WHEN node:Note THEN 'note (' + node.type + ') word match'
         WHEN node:Collection THEN 'collection word match'
         WHEN node:ArchivalObject THEN 'archival object word match'
         WHEN node:Agent THEN 'agent name word match'
         WHEN node:Accession THEN 'accession word match'
         ELSE 'word match'
       END AS source,
       left(coalesce(node.content, node.title, node.display_name, node.provenance), 150) AS excerpt
  ORDER BY score DESC
  WITH rec, collect({s: score, source: source, excerpt: excerpt, col: col})[0] AS m
  ORDER BY m.s DESC LIMIT 250
  RETURN collect({rec: rec, m: m}) AS textHits
}

// Weighted reciprocal rank fusion (agent-name evidence weighted 0.5),
// grouped per matched record
WITH [i IN range(0, size(chunkHits) - 1) | {rec: chunkHits[i].rec, m: chunkHits[i].m, r: 1.0 / (60 + i + 1)}] +
     [i IN range(0, size(titleHits) - 1) | {rec: titleHits[i].rec, m: titleHits[i].m, r: 1.0 / (60 + i + 1)}] +
     [i IN range(0, size(agentHits) - 1) | {rec: agentHits[i].rec, m: agentHits[i].m, r: 0.5 / (60 + i + 1)}] +
     [i IN range(0, size(aoHits) - 1)    | {rec: aoHits[i].rec,    m: aoHits[i].m,    r: 1.0 / (60 + i + 1)}] +
     [i IN range(0, size(textHits) - 1)  | {rec: textHits[i].rec,  m: textHits[i].m,  r: 1.0 / (60 + i + 1)}] AS scored
UNWIND scored AS row
WITH row.rec AS rec, row.r AS r, row.m AS m
ORDER BY r DESC
WITH rec, sum(r) AS rrf, collect(m)[0] AS best
RETURN coalesce(rec.title, rec.display_name) AS title,
       labels(rec)[0] AS record_type,
       rec.uri AS uri,
       rec.aspace_url AS aspace_url,
       round(rrf, 4) AS score,
       best.source AS match_source,
       best.excerpt AS excerpt,
       best.col.title AS in_collection,
       best.col.aspace_url AS in_collection_url
ORDER BY score DESC
LIMIT $limit
"""

# Chat default: small on purpose. Discovery wants a shortlist the model can
# actually read and then deepen record-by-record with get-record-text; 50
# rows bloat context and bury the relevant hits (evaluation Q6). Enumeration
# questions ("which records do we have about X") should raise it explicitly.
DEFAULT_LIMIT = 10
MAX_LIMIT = 200

# Lucene special characters, stripped from the topic before query building.
_LUCENE_SPECIALS = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


def _fuzzy_terms(words: list[str]) -> str:
    """Render words with edit-distance fuzziness proportional to length.

    Catches derivational variants that stemming cannot unify (e.g.
    organization ~2~ organizational). Short words get no fuzz — a one-edit
    neighbour of a 4-letter word is usually a different word entirely.
    """
    out = []
    for w in words:
        if len(w) >= 8:
            out.append(f"{w}~2")
        elif len(w) >= 5:
            out.append(f"{w}~1")
        else:
            out.append(w)
    return " ".join(out)


def _lucene_query(topic: str) -> str:
    """Build a phrase-boosted, fuzz-expanded Lucene query from the topic.

    Three clauses, strongest first:
      "topic"^2      — exact (stemmed) phrase, boosted; counters BM25 length
                       normalisation that buries long notes containing the
                       literal phrase
      (topic words)  — any word, normal scoring
      (fuzzy words)  — edit-distance variants as a low-scoring recall net for
                       morphology that stemming misses
    """
    clean = _LUCENE_SPECIALS.sub(" ", topic).strip()
    clean = " ".join(clean.split())
    if not clean:
        return topic
    words = clean.split()
    fuzzy = _fuzzy_terms(words)
    if len(words) == 1:
        return clean if fuzzy == clean else f"{clean} OR ({fuzzy})"
    return f'"{clean}"^2 OR ({clean}) OR ({fuzzy})'


def _cell(text: str | None) -> str:
    """Sanitise a value for use inside a markdown table cell."""
    if not text:
        return ""
    return str(text).replace("|", "/").replace("\n", " ").strip()


def _linked(title: str | None, url: str | None) -> str:
    """Render a title as a markdown link when a URL is available."""
    label = _cell(title) or "Untitled"
    return f"[{label}]({url})" if url else label


_TYPE_LABELS = {
    "Collection": "Collection",
    "ArchivalObject": "Archival object",
    "Agent": "Agent",
    "Accession": "Accession",
    "DigitalObject": "Digital object",
}


def _format_table(rows: list[dict]) -> str:
    """Render result rows as a finished markdown table.

    Formatting happens here — not in the LLM — because transcribing a
    50-row JSON payload into a table is exactly the kind of mechanical
    restructuring a small model gets wrong (transposed cells, invented
    links). The model is instructed to reproduce this table verbatim.
    """
    lines = [
        "| Record | Type | uri | Score | Match source | In collection | Evidence excerpt |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _linked(r.get("title"), r.get("aspace_url")),
                    _TYPE_LABELS.get(r.get("record_type", ""), r.get("record_type", "")),
                    _cell(r.get("uri")),
                    str(r.get("score", "")),
                    _cell(r.get("match_source")),
                    _linked(r.get("in_collection"), r.get("in_collection_url"))
                    if r.get("in_collection") else "",
                    _cell(r.get("excerpt")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


async def run_rows(topic: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Execute the hybrid search and return structured result rows.

    Shared by the LLM tool (which renders markdown) and the direct
    /search/hybrid endpoint (which returns JSON to the UI's Hybrid Search
    screen). The endpoint allows a higher limit than the chat tool: rows
    returned to the UI cost nothing in model context.
    """
    log.info("hybrid-search: topic=%r limit=%d", topic, limit)
    raw = await mcp.client.call_tool(
        "read-cypher",
        {
            "query": HYBRID_CYPHER,
            "params": {
                "topic": topic,
                "luceneQuery": _lucene_query(topic),
                "limit": limit,
            },
        },
    )
    try:
        rows = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("hybrid-search: unexpected non-JSON result.")
        return []
    return rows if isinstance(rows, list) else []


async def run(topic: str, limit: int = DEFAULT_LIMIT) -> str:
    """Execute the hybrid search and return a ready-made markdown table."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    rows = await run_rows(topic, limit)
    if not rows:
        return f"No records found for topic: {topic}"
    # Truncation signal: ranked retrieval has no total count, so when the
    # result set fills the limit the model cannot know more records exist
    # unless the tool says so. Without this, enumeration questions ("which
    # records do we have about X") silently present top-k as the complete
    # answer (accuracy eval Q4: 50 rows shown, 120-record reference set).
    truncation_note = ""
    if len(rows) >= limit:
        if limit < MAX_LIMIT:
            truncation_note = (
                f"\n\nNOTE: results filled the {limit}-row limit — more "
                f"matching records likely exist. If the user asked for ALL "
                f"records on this topic, call hybrid-search again with a "
                f"higher limit (max {MAX_LIMIT}); otherwise tell the user "
                "this list is partial, showing the most relevant matches."
            )
        else:
            truncation_note = (
                f"\n\nNOTE: results filled the maximum limit of {MAX_LIMIT} "
                "rows — more matching records likely exist. Tell the user "
                "this list is partial and that the Hybrid Search screen "
                "(magnifying glass in the menu) can retrieve up to 500."
            )
    return (
        f"{len(rows)} records found for topic \"{topic}\", ranked by "
        "relevance. Present this table to the user EXACTLY as-is:\n\n"
        + _format_table(rows)
        + truncation_note
    )
