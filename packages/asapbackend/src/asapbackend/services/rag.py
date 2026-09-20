"""Retrieval-augmented generation over user document collections.

Flow per user query (when the conversation has collections in effect):
  1. Resolve effective collections (user scope ∪ conversation scope,
     filtered to ready + unarchived).
  2. Hybrid retrieval over their chunks: exact cosine (filter-first, no
     index — arbitrary predicates, exact scores) + phrase-boosted Lucene.
     Both queries run through the read-only MCP path. Semantic search runs
     twice — raw question AND an LLM-distilled query — RRF-fused by rank;
     structural junk (tables, TOCs, cover pages) is filtered from candidates.
  3. RELEVANCE GATE — open when EITHER signal clears its floor:
       - semantic: top cosine >= rag_semantic_floor; keep the above-floor
         head, clamped to rag_max_chunks
       - lexical rescue: top Lucene score >= rag_lexical_floor passes even
         when semantics fail (codenames, part numbers embed weakly but
         match exactly)
     Calibration note (2026-07, DACS + Polaroid corpora): a delta-over-
     background gate was tried first and abandoned — bge cosine curves are
     compressed and nonsense queries produced LARGER top-minus-background
     deltas than genuinely relevant ones. Absolute floors separated every
     probe: on-topic >= 0.847 cosine (or >= 5.22 lexical when the corpus is
     tiny and cosine dips), off-topic <= 0.809 cosine / <= 4.21 lexical.
  4. Survivors are RRF-ordered, expanded with their seq-neighbors (a match
     on a section opening carries the rules that follow it), merged into
     contiguous passages, and injected as a labelled context block the model
     must cite ([Passage N]); chunk ids are persisted per user message so
     conversations rehydrate with identical grounding.

Every retrieval logs its score curve — the gate knobs in Settings are meant
to be calibrated from those logs against the real corpus.
"""

import asyncio
import json
import logging
import re


def _rag_extra() -> dict:
    from asapbackend.config import settings
    if settings.inference_reasoning_effort:
        return {"extra_body": {"reasoning_effort": settings.inference_reasoning_effort}}
    return {}
import statistics
import uuid
from typing import Any

import httpx
import psycopg

from asapbackend import mcp
from asapbackend.config import settings
from asapbackend.services import graph_docs

log = logging.getLogger(__name__)

# ── Lucene query building (mirrors tools/hybrid_search.py) ───────────────────

_LUCENE_SPECIALS = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')


def _lucene_query(topic: str) -> str:
    clean = " ".join(_LUCENE_SPECIALS.sub(" ", topic).split())
    if not clean:
        return topic
    if " " not in clean:
        return clean
    return f'"{clean}"^2 OR ({clean})'


# ── Retrieval queries (read-only MCP path) ───────────────────────────────────

_SEMANTIC_CYPHER = """
MATCH (dc:DocumentCollection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(ch:DocumentChunk)
WHERE dc.id IN $ids AND ch.embedding IS NOT NULL
WITH dc, d, ch, vector.similarity.cosine(ch.embedding, $qv) AS score
ORDER BY score DESC
LIMIT $k
RETURN ch.id AS id, ch.text AS text, ch.page_no AS page_no,
       ch.heading_path AS heading, ch.seq AS seq, d.id AS document_id,
       d.file_name AS file_name, dc.title AS collection, score
"""

_LEXICAL_CYPHER = """
CALL db.index.fulltext.queryNodes('document_chunk_fulltext', $lucene)
YIELD node, score
MATCH (dc:DocumentCollection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(node)
WHERE dc.id IN $ids
RETURN node.id AS id, node.text AS text, node.page_no AS page_no,
       node.heading_path AS heading, node.seq AS seq, d.id AS document_id,
       d.file_name AS file_name, dc.title AS collection, score
ORDER BY score DESC
LIMIT 10
"""

_NEIGHBOR_CYPHER = """
UNWIND $targets AS t
MATCH (dc:DocumentCollection)-[:CONTAINS]->(d:Document {id: t.doc})-[:HAS_CHUNK]->(ch:DocumentChunk)
WHERE ch.seq >= t.lo AND ch.seq <= t.hi
RETURN DISTINCT ch.id AS id, ch.text AS text, ch.page_no AS page_no,
       ch.heading_path AS heading, ch.seq AS seq, d.id AS document_id,
       d.file_name AS file_name, dc.title AS collection
"""

_GROW_CYPHER = """
UNWIND $targets AS t
MATCH (dc:DocumentCollection)-[:CONTAINS]->(d:Document {id: t.doc})-[:HAS_CHUNK]->(ch:DocumentChunk)
WHERE ch.seq >= t.lo AND ch.seq <= t.hi AND ch.embedding IS NOT NULL
RETURN DISTINCT ch.id AS id, ch.text AS text, ch.page_no AS page_no,
       ch.heading_path AS heading, ch.seq AS seq, d.id AS document_id,
       d.file_name AS file_name, dc.title AS collection,
       vector.similarity.cosine(ch.embedding, $qv) AS score
"""


async def _embed_query(text: str) -> list[float]:
    payload: dict = {"model": settings.embedding_model, "input": [text]}
    headers: dict[str, str] = {}
    if settings.embedding_api_key:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
    else:
        payload["truncate_prompt_tokens"] = -1  # vLLM-only; hosted APIs reject it
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.embedding_base_url}/embeddings", json=payload, headers=headers,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


async def _run_cypher(query: str, params: dict) -> list[dict]:
    raw = await mcp.client.call_tool("read-cypher", {"query": query, "params": params})
    try:
        rows = json.loads(raw)
        return rows if isinstance(rows, list) else []
    except (json.JSONDecodeError, TypeError):
        log.warning("RAG retrieval returned non-JSON result; skipping.")
        return []


# ── Query distillation ────────────────────────────────────────────────────────

_DISTILL_PROMPT = (
    "Rewrite the user's chat question as a short, focused search query for a "
    "reference-document knowledge base. Keep the core informational need and "
    "all domain terms, named standards, and technical vocabulary. Drop "
    "conversational framing and references to specific archival collections, "
    "records, or people whose papers are being described — the reference "
    "documents do not mention them. Phrase the query as the topic a relevant "
    "passage would discuss, using terminology likely to appear in the text "
    "itself, not as an instruction to evaluate or list. Output ONLY the "
    "search query, nothing else."
)


async def _distill_query(text: str) -> str:
    """Distill a conversational question into a retrieval query, e.g.
    'For the Muriel Siebert collection, evaluate the biographical note against
    DACS standards' -> 'DACS requirements for biographical history notes'.
    Mixed-intent framing measurably dilutes bge embeddings against the
    document corpus. Falls back to the raw text on any failure."""
    from asapbackend.llm.client import completion_cap_kwargs, get_llm_client, sampling_kwargs

    try:
        resp = await asyncio.wait_for(
            get_llm_client().chat.completions.create(
                model=settings.inference_model,
                messages=[
                    {"role": "system", "content": _DISTILL_PROMPT},
                    {"role": "user", "content": text},
                ],
                **completion_cap_kwargs(2048),
                **sampling_kwargs(0.0),
                **(_rag_extra() or {}),
            ),
            timeout=30.0,
        )
        distilled = " ".join((resp.choices[0].message.content or "").split())
        if not distilled or len(distilled) > 300:
            return text
        log.info("RAG query distilled: %r -> %r", text, distilled)
        return distilled
    except Exception:
        log.warning("RAG query distillation failed; using the raw question.")
        return text


# ── LLM relevance filter ──────────────────────────────────────────────────────

_RELEVANCE_PROMPT = (
    "You are filtering search results. The user asked a question; below are "
    "numbered passages retrieved from reference documents. Judge each passage "
    "on whether its CONTENT helps answer the question — front matter, prefaces, "
    "changelogs, version histories, standards comparisons, and passages that "
    "merely mention the same document or standard do NOT help. A passage that "
    "answers only part of the question IS useful — judge partial usefulness "
    "generously. Reply with ONLY a JSON array of the numbers of the useful "
    "passages, e.g. [1,3]. If none are useful, reply []."
)


async def _relevance_filter(question: str, kept: list[dict]) -> list[dict]:
    """One batched LLM call to drop gate survivors whose content is not
    actually useful for the question — the bi-encoder can't tell 'discusses
    biographical notes' from 'is the changelog of the standard that contains
    rules about biographical notes'. Fails open: any error keeps everything."""
    from asapbackend.llm.client import completion_cap_kwargs, get_llm_client, sampling_kwargs

    if len(kept) <= 1:
        return kept
    snippets = "\n\n".join(
        f"[{n}] ({c.get('heading') or 'no heading'}) {c['text'][:500]}"
        for n, c in enumerate(kept, 1)
    )
    try:
        resp = await asyncio.wait_for(
            get_llm_client().chat.completions.create(
                model=settings.inference_model,
                messages=[
                    {"role": "system", "content": _RELEVANCE_PROMPT},
                    {"role": "user", "content": f"Question: {question}\n\n{snippets}"},
                ],
                **completion_cap_kwargs(4096),
                **sampling_kwargs(0.0),
                **(_rag_extra() or {}),
            ),
            timeout=45.0,
        )
        content = resp.choices[0].message.content or ""
        m = re.search(r"\[[\d,\s]*\]", content)
        if not m:
            return kept
        chosen = {int(x) for x in re.findall(r"\d+", m.group(0))}
        filtered = [c for n, c in enumerate(kept, 1) if n in chosen]
        log.info(
            "RAG relevance filter kept %d of %d survivors: %s",
            len(filtered), len(kept), sorted(chosen),
        )
        return filtered  # an explicit [] is a verdict: nothing useful, inject nothing
    except Exception:
        log.warning("RAG relevance filter failed; keeping all survivors.")
        return kept


# ── Junk-chunk filter ─────────────────────────────────────────────────────────

_MIN_ALPHA_RATIO = 0.55


def _is_junk(text: str) -> bool:
    """Structural junk (crosswalk tables, TOCs, cover pages, page-number
    strays) embeds as match-everything attractors and wins ties against flat
    cosine curves. Thresholds validated on the DACS corpus; the size floor
    stays small because real one-line rules run ~40-70 chars."""
    stripped = text.strip()
    if len(stripped) < 30:  # 'm', 'xxiii xxiv', bare heading strays
        return True
    if text.count("<td>") + text.count("</tr>") >= 3:  # crosswalk tables
        return True
    lower = text.lower()
    if "©" in text or "all rights reserved" in lower or "isbn" in lower:
        return True  # copyright/edition boilerplate
    alpha = sum(1 for ch in text if ch.isalpha() or ch.isspace())
    if alpha / max(1, len(text)) < _MIN_ALPHA_RATIO:  # digit/punct-dense
        return True
    tokens = stripped.split()
    if len(tokens) >= 12:  # TOC dot-leader debris, letter-spaced cover text
        single = sum(1 for t in tokens if len(t) == 1)
        if single / len(tokens) > 0.3:
            return True
    return False


# ── Effective collections ────────────────────────────────────────────────────

async def effective_collection_ids(
    conn: psycopg.AsyncConnection, user_id: str, conversation_id: str
) -> list[str]:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT document_collection_id FROM user_document_collection WHERE user_id = %s
            UNION
            SELECT document_collection_id FROM conversation_document_collection
            WHERE conversation_id = %s
            """,
            (uuid.UUID(user_id), uuid.UUID(conversation_id)),
        )
        ids = [str(r["document_collection_id"]) for r in await cur.fetchall()]
    if not ids:
        return []
    import asyncio
    colls = await asyncio.to_thread(graph_docs.get_collections_by_ids, ids)
    return [c["id"] for c in colls if not c["archived"] and c["status"] == "ready"]


# ── The relevance gate ────────────────────────────────────────────────────────

def _fuse_semantic(a: list[dict], b: list[dict]) -> list[dict]:
    """RRF-fuse two ranked candidate lists from different query embeddings
    (raw question vs distilled query). Their absolute cosines are not
    comparable, so fuse by rank; each chunk keeps its best score for the
    floor check. Sections the raw phrasing finds survive even when the
    distilled phrasing drifts toward document-level meta content."""
    if not a or not b:
        return a or b
    rrf: dict[str, float] = {}
    best: dict[str, dict] = {}
    for lst in (a, b):
        for i, r in enumerate(lst):
            rrf[r["id"]] = rrf.get(r["id"], 0.0) + 1.0 / (60 + i + 1)
            if r["id"] not in best or r["score"] > best[r["id"]]["score"]:
                best[r["id"]] = r
    return [best[i] for i in sorted(rrf, key=lambda i: rrf[i], reverse=True)]


def _gate(semantic: list[dict], lexical: list[dict]) -> list[dict]:
    """Apply the relevance gate; return the chunks to inject."""
    survivors: list[dict] = []

    if semantic:
        cosines = [r["score"] for r in semantic]
        # Fused input is RRF-ordered, not score-ordered — gate on the best
        # score anywhere in the candidate set.
        top = max(cosines)
        # Background (median of the candidate tail) is logged for
        # calibration only — it does not gate. See module docstring.
        tail = cosines[15:] if len(cosines) >= 20 else cosines[len(cosines) // 2:]
        background = statistics.median(tail) if tail else top
        if top >= settings.rag_semantic_floor:
            survivors = [
                r for r in semantic if r["score"] >= settings.rag_semantic_floor
            ][: settings.rag_max_chunks]
        gate_state = "open" if survivors else "closed"
        log.info(
            "RAG gate %s: top=%.3f floor=%.3f background=%.3f candidates=%d kept=%d",
            gate_state, top, settings.rag_semantic_floor, background,
            len(semantic), len(survivors),
        )

    # Lexical rescue: exact/phrase evidence passes regardless of the
    # semantic gate — union the strongest matches.
    strong_lexical = [r for r in lexical if r["score"] >= settings.rag_lexical_floor][:3]
    if strong_lexical:
        log.info(
            "RAG lexical rescue: %d matches >= %.1f (top=%.2f)",
            len(strong_lexical), settings.rag_lexical_floor, strong_lexical[0]["score"],
        )

    # Fuse: RRF over the two ranked lists (k=60), preserving only survivors.
    ranks: dict[str, float] = {}
    chunk_by_id: dict[str, dict] = {}
    for rank, r in enumerate(survivors):
        ranks[r["id"]] = ranks.get(r["id"], 0) + 1.0 / (60 + rank + 1)
        chunk_by_id[r["id"]] = r
    for rank, r in enumerate(strong_lexical):
        ranks[r["id"]] = ranks.get(r["id"], 0) + 1.0 / (60 + rank + 1)
        chunk_by_id.setdefault(r["id"], r)

    ordered = sorted(ranks, key=lambda i: ranks[i], reverse=True)[: settings.rag_max_chunks]
    return [chunk_by_id[i] for i in ordered]


# ── Neighbor expansion ────────────────────────────────────────────────────────

async def _expand_neighbors(kept: list[dict]) -> list[dict]:
    """Pull rag_neighbor_window adjacent chunks around each surviving hit —
    a match on a section opening then carries the rules that follow it.
    Returns a flat chunk list: contiguous runs adjacent, runs ordered by the
    best retrieval rank of the hits inside them, chunks within a run by seq."""
    w = settings.rag_neighbor_window
    if w <= 0 or not kept:
        return kept
    targets = [
        {"doc": c["document_id"], "lo": c["seq"] - w, "hi": c["seq"] + w}
        for c in kept
        if c.get("document_id") is not None and c.get("seq") is not None
    ]
    if not targets:
        return kept
    rows = await _run_cypher(_NEIGHBOR_CYPHER, {"targets": targets})
    if not rows:
        return kept

    rank = {c["id"]: i for i, c in enumerate(kept)}
    by_doc: dict[str, dict[int, dict]] = {}
    for r in rows:
        # Neighbors must pass the junk filter too — a good hit next to a
        # cover page must not drag the cover page in. Original hits stay.
        if r["id"] not in rank and _is_junk(r["text"]):
            continue
        by_doc.setdefault(r["document_id"], {})[r["seq"]] = r

    runs: list[list[dict]] = []
    for seqmap in by_doc.values():
        seqs = sorted(seqmap)
        run = [seqmap[seqs[0]]]
        for prev, cur in zip(seqs, seqs[1:]):
            if cur == prev + 1:
                run.append(seqmap[cur])
            else:
                runs.append(run)
                run = [seqmap[cur]]
        runs.append(run)
    runs.sort(key=lambda run: min(rank.get(c["id"], len(kept)) for c in run))
    expanded = [c for run in runs for c in run]
    log.info(
        "RAG neighbor expansion: %d hits -> %d chunks in %d passages",
        len(kept), len(expanded), len(runs),
    )
    return expanded


def _runs_of(chunks: list[dict]) -> list[list[dict]]:
    """Split an ordered chunk list into contiguous same-document runs."""
    runs: list[list[dict]] = []
    for c in chunks:
        if (
            runs
            and c.get("document_id") == runs[-1][-1].get("document_id")
            and c.get("seq") is not None
            and runs[-1][-1].get("seq") is not None
            and c["seq"] == runs[-1][-1]["seq"] + 1
        ):
            runs[-1].append(c)
        else:
            runs.append([c])
    return runs


_SECTION_NUMBER = re.compile(r"\b(\d+\.\d+)(?:\.\d+)*\b")


def _section_prefix(run: list[dict]) -> str | None:
    """Detect the dominant section number ('2.7') in a passage's chunks.
    Numbered reference standards carry their rule numbers in the text —
    the most reliable section-boundary signal this side of a reranker.
    Requires >= 2 occurrences so a stray cross-reference doesn't anchor."""
    counts: dict[str, int] = {}
    for c in run:
        for m in _SECTION_NUMBER.findall(c["text"]):
            counts[m] = counts.get(m, 0) + 1
    if not counts:
        return None
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] >= 2 else None


async def _grow_sections(chunks: list[dict], qv: list[float]) -> list[dict]:
    """Extend each passage to cover its whole section. When the passage
    carries a section number ('2.7.x' rules), grow while adjacent chunks
    keep referencing that section, tolerating rag_grow_gap consecutive
    non-referencing chunks (interleaved examples); embeddings can't do this
    — the NEXT section scores as high as the matched one. For unnumbered
    documents, fall back to growing while the query cosine clears
    rag_grow_floor. Junk chunks are skipped in the output but don't stop
    growth (tables sit mid-section)."""
    fwd, back = settings.rag_grow_max_forward, settings.rag_grow_max_backward
    if (fwd <= 0 and back <= 0) or not chunks:
        return chunks
    runs = _runs_of(chunks)
    targets = []
    for run in runs:
        doc, lo, hi = run[0].get("document_id"), run[0].get("seq"), run[-1].get("seq")
        if doc is None or lo is None:
            continue
        if back > 0:
            targets.append({"doc": doc, "lo": lo - back, "hi": lo - 1})
        if fwd > 0:
            targets.append({"doc": doc, "lo": hi + 1, "hi": hi + fwd})
    if not targets:
        return chunks
    rows = await _run_cypher(_GROW_CYPHER, {"targets": targets, "qv": qv})
    scored: dict[tuple, dict] = {(r["document_id"], r["seq"]): r for r in rows}

    have = {(c.get("document_id"), c.get("seq")) for c in chunks}
    added: list[dict] = []

    def _walk(doc: str, seqs: range, prefix: str | None) -> None:
        """Absorb frontier chunks in walk order; non-matching chunks are
        included only when a later chunk matches again (sandwiched examples)."""
        # Full rule numbers only ('2.7.13') — neighboring sections cite the
        # bare section number ('see 2.7') and must not extend growth.
        matcher = (
            re.compile(rf"\b{re.escape(prefix)}\.\d+\b") if prefix else None
        )
        pending: list[dict] = []
        for s in seqs:
            r = scored.get((doc, s))
            if r is None or (doc, s) in have:
                break
            matched = (
                bool(matcher.search(f"{r['text']} {r.get('heading') or ''}"))
                if matcher
                else r["score"] >= settings.rag_grow_floor
            )
            if matched:
                for p in pending:
                    have.add((p["document_id"], p["seq"]))
                    if not _is_junk(p["text"]):
                        added.append(p)
                pending = []
                have.add((doc, s))
                if not _is_junk(r["text"]):
                    added.append(r)
            else:
                pending.append(r)
                if len(pending) > settings.rag_grow_gap:
                    break

    for run in runs:
        doc, lo, hi = run[0].get("document_id"), run[0].get("seq"), run[-1].get("seq")
        if doc is None or lo is None:
            continue
        prefix = _section_prefix(run)
        _walk(doc, range(lo - 1, lo - back - 1, -1), prefix)
        _walk(doc, range(hi + 1, hi + fwd + 1), prefix)
    if not added:
        return chunks
    merged = chunks + added
    # Re-establish passage order: original run order, chunks by seq — then cap.
    def _key(c):
        for i, run in enumerate(runs):
            if c.get("document_id") == run[0].get("document_id") and run[0].get("seq") is not None \
                    and c.get("seq") is not None and run[0]["seq"] - back <= c["seq"] <= run[-1]["seq"] + fwd:
                return (i, c["seq"])
        return (len(runs), c.get("seq") or 0)
    merged.sort(key=_key)
    if len(merged) > settings.rag_total_max_chunks:
        log.info(
            "RAG growth capped: %d chunks trimmed to %d",
            len(merged), settings.rag_total_max_chunks,
        )
        merged = merged[: settings.rag_total_max_chunks]
    log.info("RAG section growth: %d -> %d chunks", len(chunks), len(merged))
    return merged


def to_passages(chunks: list[dict]) -> list[dict]:
    """Merge contiguous chunks (same document, consecutive seq) into passages
    — the unit the LLM cites and the UI displays. Pure function of the chunk
    list and its order, so live retrieval and rehydration from stored refs
    produce identical passages."""
    passages: list[dict] = []
    cur: dict | None = None
    for c in chunks:
        if (
            cur is not None
            and c.get("document_id") is not None
            and c.get("document_id") == cur.get("_document_id")
            and c.get("seq") is not None
            and cur.get("_last_seq") is not None
            and c["seq"] == cur["_last_seq"] + 1
        ):
            cur["text"] += "\n\n" + c["text"]
            cur["_last_seq"] = c["seq"]
            if cur.get("heading") is None:
                cur["heading"] = c.get("heading")
        else:
            cur = {
                "id": c["id"],
                "text": c["text"],
                "page_no": c.get("page_no"),
                "heading": c.get("heading"),
                "file_name": c["file_name"],
                "collection": c["collection"],
                "_document_id": c.get("document_id"),
                "_last_seq": c.get("seq"),
            }
            passages.append(cur)
    for p in passages:
        p.pop("_document_id", None)
        p.pop("_last_seq", None)
    return passages


# ── Public API ────────────────────────────────────────────────────────────────

async def retrieve(
    conn: psycopg.AsyncConnection, user_id: str, conversation_id: str, query: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Retrieve supporting chunks for a query. Returns (chunks, search_query)
    where search_query is the distilled query when one was used (surfaced in
    the UI), else None. Returns ([], None) on no collections in effect, gate
    closed, or any retrieval failure — RAG must never break chat."""
    try:
        ids = await effective_collection_ids(conn, user_id, conversation_id)
        if not ids:
            return [], None

        last_qv: list[float] | None = None

        async def _semantic_for(text: str) -> list[dict]:
            nonlocal last_qv
            last_qv = qv = await _embed_query(text)
            rows = await _run_cypher(
                _SEMANTIC_CYPHER,
                {"ids": ids, "qv": qv, "k": settings.rag_semantic_candidates},
            )
            clean = [r for r in rows if not _is_junk(r["text"])]
            if len(clean) < len(rows):
                log.info(
                    "RAG junk filter dropped %d of %d semantic candidates",
                    len(rows) - len(clean), len(rows),
                )
            return clean

        # Dual-query retrieval: the raw question and the distilled query each
        # surface sections the other misses; fuse by rank.
        semantic = await _semantic_for(query)
        distilled: str | None = None
        if settings.rag_query_distillation:
            candidate = await _distill_query(query)
            if candidate != query:
                distilled = candidate
                semantic = _fuse_semantic(semantic, await _semantic_for(distilled))
        # last_qv now holds the most topical embedding (distilled when used) —
        # section growth measures topic continuity against it.
        grow_qv = last_qv

        # Lexical runs on the raw question — it carries every exact term the
        # user typed; distillation can only drop phrases, never add them.
        lexical = [
            r
            for r in await _run_cypher(
                _LEXICAL_CYPHER, {"ids": ids, "lucene": _lucene_query(query)}
            )
            if not _is_junk(r["text"])
        ]
        kept = _gate(semantic, lexical)
        if kept and settings.rag_relevance_filter:
            filtered = await _relevance_filter(query, kept)
            if not filtered:
                # Lexical evidence overrules a blanket veto — an exact phrase
                # match is stronger proof than a snippet-based judgment.
                lex_ids = {r["id"] for r in lexical}
                filtered = [c for c in kept if c["id"] in lex_ids][:2]
                if filtered:
                    log.info(
                        "RAG relevance veto overruled by lexical evidence: %d kept",
                        len(filtered),
                    )
            kept = filtered
        if not kept:
            return [], distilled
        chunks = await _expand_neighbors(kept)
        if grow_qv is not None:
            chunks = await _grow_sections(chunks, grow_qv)
        return chunks, distilled
    except Exception:
        log.exception("RAG retrieval failed — continuing without document context")
        return [], None


def format_context(chunks: list[dict]) -> str:
    """Render the injected context block with citation labels. Contiguous
    chunks are merged into passages first — labels number passages."""
    lines = [
        "Supporting documents (retrieved from the document collections enabled "
        "for this conversation). Ground your answer on these passages where "
        "relevant and cite them with their labels, e.g. [Passage 1]. Do not "
        "invent citations for passages that are not listed here.",
        "",
    ]
    for n, c in enumerate(to_passages(chunks), 1):
        where = []
        if c.get("page_no") is not None:
            where.append(f"p. {c['page_no']}")
        if c.get("heading"):
            where.append(f"§ {c['heading']}")
        loc = f" ({', '.join(where)})" if where else ""
        lines.append(f"[Passage {n}] {c['file_name']}{loc} — collection: {c['collection']}")
        lines.append(c["text"])
        lines.append("")
    return "\n".join(lines).strip()


def augment_user_message(user_text: str, chunks: list[dict]) -> str:
    """The content the LLM sees for a user turn with retrieved context.
    The RAW user message is what gets persisted and displayed — this
    composition is applied at message-assembly time, live and on rehydration
    alike, so the model always sees identical grounding."""
    return f"{format_context(chunks)}\n\n---\n\nQuestion: {user_text}"


async def augment_history(
    conn: psycopg.AsyncConnection, conversation_id: str, messages: list[dict]
) -> None:
    """Re-apply document context to historical user turns (rehydration).

    `messages` is the OpenAI-format history from load_conversation_messages
    (system excluded). Alignment: this queries the same rows in the same
    order, so index i here corresponds to messages[i].
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT id, role FROM conversation_message
            WHERE conversation_id = %s AND role != 'system'
            ORDER BY created_ts ASC
            """,
            (uuid.UUID(conversation_id),),
        )
        rows = await cur.fetchall()
    if len(rows) != len(messages):
        log.warning("RAG rehydration alignment mismatch (%d rows vs %d messages); skipping.",
                    len(rows), len(messages))
        return

    user_msg_ids = [str(r["id"]) for r in rows if r["role"] == "user"]
    if not user_msg_ids:
        return
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT conversation_message_id, document_chunk_id
            FROM conversation_message_document_chunk
            WHERE conversation_message_id = ANY(%s::uuid[])
            ORDER BY conversation_message_id, retrieval_order
            """,
            (user_msg_ids,),
        )
        refs = await cur.fetchall()
    if not refs:
        return

    by_msg: dict[str, list[str]] = {}
    for r in refs:
        by_msg.setdefault(str(r["conversation_message_id"]), []).append(
            str(r["document_chunk_id"])
        )

    import asyncio
    all_chunk_ids = [cid for ids in by_msg.values() for cid in ids]
    chunks = await asyncio.to_thread(graph_docs.get_chunks_by_ids, all_chunk_ids)
    chunk_map = {c["id"]: c for c in chunks}

    for i, row in enumerate(rows):
        mid = str(row["id"])
        if row["role"] != "user" or mid not in by_msg:
            continue
        msg_chunks = [chunk_map[c] for c in by_msg[mid] if c in chunk_map]
        if msg_chunks and isinstance(messages[i].get("content"), str):
            messages[i]["content"] = augment_user_message(messages[i]["content"], msg_chunks)
