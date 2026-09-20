"""Backend-native kg-search tool: one entity's knowledge-graph dossier.

Given an entity name, resolves it against the AI-generated knowledge graph
(:Inferred nodes built by kggenerator) and returns everything the KG holds:
identity, existence dates, the archivist-created twin record, and every
relationship with its RiC type, date, verbatim supporting quote, and source
records resolved to ready-made ArchivesSpace links.

This is the KG counterpart of get-record-text, built on the same evaluation
lesson: the model must never hand-write Cypher over long relationship type
names (RICO_HAS_OR_HAD_CORRESPONDENT) it would mangle in transcription. It
passes a name; everything else happens here. Citation is fail-closed — the
tool resolves source URIs to links server-side, so the model never
constructs one.

Name resolution: full-text over inferred names first; if nothing clears the
bar, a vector search over the inferred embeddings catches semantic aliases.
Executes via MCP read-cypher (read-only); embeddings via the vLLM server.
"""

import json
import logging
import re

import httpx

from asapbackend import mcp
from asapbackend.config import settings

log = logging.getLogger(__name__)

TOOL_NAME = "kg-search"

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Look up a person, organization, place, or event in the "
            "AI-generated knowledge graph and return its full dossier: who "
            "or what it is, existence dates, name variants, the archivist-"
            "created record it corresponds to, and EVERY known relationship "
            "(successors, controllers, owners, leaders, employers, "
            "correspondents, family, participants in events, locations) — "
            "each with a verbatim supporting quote and source links. USE "
            "THIS FIRST for relationship and network questions ('who "
            "succeeded X', 'what companies did X control', 'who are X's "
            "correspondents', 'how are X and Y connected'). One call also "
            "returns every DISTINCT same-name entity (a company, its "
            "successor, its bankruptcy event) — calling again with the "
            "same name returns the identical result, so never repeat a "
            "call. The knowledge graph covers only collections it has "
            "been generated for; when this returns no match, fall back to "
            "hybrid-search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "The entity's name, as plainly as possible: "
                        "'Boston and Albany Railroad', 'Penn Central', "
                        "'William Bliss'. Name only — no fact terms."
                    ),
                },
            },
            "required": ["name"],
        },
    },
}

# Full-text score floor for accepting a match without vector fallback.
_FT_FLOOR = 1.0
# Cosine floor for the vector fallback.
_VEC_FLOOR = 0.80
_MAX_RELATIONS = 60
_MAX_FAMILY = 3           # same-name entities appended after the best match
_MAX_FAMILY_RELATIONS = 25
_MAX_SOURCES = 12


async def _read(query: str, **params) -> list[dict]:
    raw = await mcp.client.call_tool(
        "read-cypher", {"query": query, "params": params}
    )
    try:
        rows = json.loads(raw)
        return rows if isinstance(rows, list) else []
    except (json.JSONDecodeError, TypeError):
        log.warning("kg-search: non-JSON read-cypher result.")
        return []


async def _embed(text: str) -> list[float] | None:
    try:
        headers = (
            {"Authorization": f"Bearer {settings.embedding_api_key}"}
            if settings.embedding_api_key else {}
        )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.embedding_base_url}/embeddings",
                json={"model": settings.embedding_model, "input": [text]},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()["data"][0]["embedding"]
    except Exception as exc:  # noqa: BLE001 — fallback is optional
        log.warning("kg-search: embedding fallback unavailable: %s", exc)
        return None


def _humanize(rel_type: str, nature: str | None) -> str:
    if rel_type == "INFERRED_GENERIC_RELATIONSHIP":
        return nature or "related to"
    return rel_type.removeprefix("RICO_").replace("_", " ").lower()


_STOP_TOKENS = {"the", "of", "and", "co", "company", "corporation", "inc",
                "railroad", "railway", "rail", "road"}


def _name_agrees(query: str, candidate_names: list[str]) -> bool:
    """Every distinctive query token must appear in the candidate's names.

    Guards against first-name token matches ('Chester Barnard' resolving to
    'Chapin, Chester W.') — the tool must under-match rather than hand the
    model a confident wrong entity. Corporate boilerplate tokens don't count
    as distinctive in either direction.
    """
    q_tokens = {t for t in re.findall(r"[a-z0-9&]+", query.lower())
                if len(t) > 2 and t not in _STOP_TOKENS}
    if not q_tokens:
        return True  # nothing distinctive to check — let the match stand
    cand_tokens = {t for n in candidate_names
                   for t in re.findall(r"[a-z0-9&]+", (n or "").lower())}
    return q_tokens <= cand_tokens


_NO_MATCH = (
    "No knowledge-graph entity matched {name!r}. The knowledge graph covers "
    "only collections it has been generated for — answer instead with the "
    "standard strategy: hybrid-search, then get-record-text on the "
    "promising results."
)


async def run(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "kg-search needs an entity name."

    # ── Resolve the name ────────────────────────────────────────────────
    hits = await _read(
        """
        CALL db.index.fulltext.queryNodes('inferred_fulltext', $q)
        YIELD node, score
        RETURN node.id AS id, node.display_name AS name,
               node.alternate_names AS alt, labels(node) AS labels, score
        ORDER BY score DESC LIMIT 8
        """,
        q=name.replace("/", " "),
    )
    hits = [h for h in hits if h["score"] >= _FT_FLOOR
            and _name_agrees(name, [h["name"], *(h.get("alt") or [])])]
    if not hits:
        vec = await _embed(name)
        if vec is not None:
            for index in ("inferred_agent_embedding", "inferred_place_embedding",
                          "inferred_event_embedding"):
                hits += await _read(
                    f"""
                    CALL db.index.vector.queryNodes('{index}', 3, $v)
                    YIELD node, score WHERE score >= $floor
                    RETURN node.id AS id, node.display_name AS name,
                           node.alternate_names AS alt,
                           labels(node) AS labels, score
                    """,
                    v=vec, floor=_VEC_FLOOR,
                )
            hits = [h for h in hits
                    if _name_agrees(name, [h["name"], *(h.get("alt") or [])])]
            hits.sort(key=lambda h: -h["score"])
    if not hits:
        return _NO_MATCH.format(name=name)

    # Name-family view: the entity-resolution pipeline deliberately keeps
    # same-named-but-distinct entities separate (a company vs the holding
    # company that succeeded it; a company vs its bankruptcy event).
    # Under-merged in storage, reunified at read time — one call shows the
    # whole family, clearly labelled as distinct entities, so the model
    # never re-calls with the same name hoping for the rest.
    best = hits[0]
    family: list[dict] = []
    seen_names = {best["name"]}
    for h in hits[1:]:
        if h["name"] not in seen_names:
            seen_names.add(h["name"])
            family.append(h)
    family = family[:_MAX_FAMILY]

    out, source_uris = await _entity_block(best, top=True)
    for h in family:
        lines, uris = await _entity_block(h, top=False)
        out += [""] + lines
        for u in uris:
            if u not in source_uris:
                source_uris.append(u)

    # ── Sources resolved to links (fail-closed citation) ────────────────
    if source_uris:
        recs = await _read(
            """
            UNWIND $uris AS uri
            MATCH (rec {uri: uri})
            WHERE rec:Collection OR rec:ArchivalObject OR rec:Agent
            RETURN uri, coalesce(rec.title, rec.display_name) AS title,
                   rec.aspace_url AS url
            """,
            uris=source_uris[:_MAX_SOURCES],
        )
        if recs:
            out.append("\n## Source records (cite these — the evidence "
                       "text lives here; pass a uri to get-record-text "
                       "to read it in full)")
            for rec in recs:
                out.append(f"- [{rec['title']}]({rec['url']}) (uri {rec['uri']})")
    return "\n".join(out)


async def _entity_block(hit: dict, top: bool) -> tuple[list[str], list[str]]:
    """Render one entity's card + relationships; return (lines, source_uris)."""
    ent = (await _read(
        "MATCH (n:Inferred {id: $id}) RETURN properties(n) AS p", id=hit["id"]
    ))[0]["p"]
    kind = next(l for l in hit["labels"] if l.startswith("Inferred"))
    if top:
        out = [f"# {kind}: {ent['display_name']}",
               "(AI-inferred from finding-aid text — cite the source "
               "records below)"]
    else:
        out = [f"## Distinct same-name entity — {kind}: {ent['display_name']}",
               "(kept separate on evidence; do not merge it with the entity "
               "above when answering)"]
    for field, label in [("agent_type", "type"), ("exists_from", "exists from"),
                         ("exists_to", "exists to"), ("event_type", "event type"),
                         ("date", "date"), ("description", "description")]:
        if ent.get(field):
            out.append(f"- {label}: {ent[field]}")
    if ent.get("alternate_names"):
        out.append(f"- also seen as: {', '.join(ent['alternate_names'][:6])}")

    if ent.get("extracted_agent_twin"):
        twin = await _read(
            "MATCH (a:Agent {uri: $uri}) RETURN a.display_name AS n, "
            "a.aspace_url AS u", uri=ent["extracted_agent_twin"])
        if twin:
            out.append(f"- archivist-created record: [{twin[0]['n']}]"
                       f"({twin[0]['u']}) — use this link for the "
                       "authoritative agent record")

    # Outgoing edges cover everything: inverses are materialized.
    rels = await _read(
        """
        MATCH (n:Inferred {id: $id})-[r]->(m:Inferred)
        RETURN type(r) AS t, r.nature AS nature, r.date AS date,
               r.quote AS quote, r.source_uri AS sources,
               r.confidence AS conf, m.display_name AS other
        ORDER BY t, other LIMIT $cap
        """,
        id=hit["id"], cap=_MAX_RELATIONS if top else _MAX_FAMILY_RELATIONS,
    )
    source_uris: list[str] = list(ent.get("source_uri") or [])
    if rels:
        seen_lines: set[str] = set()
        rel_lines: list[str] = []
        for r in rels:
            line = f"- {_humanize(r['t'], r['nature'])}: {r['other']}"
            if r.get("date"):
                line += f" ({r['date']})"
            if r.get("quote"):
                line += f' — "{r["quote"]}"'
            # Surface the extractor's own uncertainty so answers hedge
            # title-inferred and ambiguous claims (extraction reserves 1.0
            # for facts stated outright in narrative text).
            if isinstance(r.get("conf"), (int, float)) and r["conf"] < 0.95:
                line += f" [confidence {r['conf']:.1f}]"
            if line not in seen_lines:  # inverse pairs of generic relations
                seen_lines.add(line)
                rel_lines.append(line)
            for u in r.get("sources") or []:
                if u not in source_uris:
                    source_uris.append(u)
        out.append(f"Relationships ({len(rel_lines)}):")
        out.extend(rel_lines)
    else:
        out.append("(no relationships recorded for this entity)")
    return out, source_uris
