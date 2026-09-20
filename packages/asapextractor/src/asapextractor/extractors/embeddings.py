"""Embedding phase: NoteChunk nodes + semantic vector embeddings + indexes.

Reads text already loaded into Neo4j (this phase has no ArchivesSpace
dependency), splits note content into retrieval-sized chunks, generates
embeddings via the vLLM server, and creates the vector indexes.

Design (see also the system prompt's Semantic search section):
- Embeddings are computed over COMPOSED CONTEXTUAL STRINGS, never bare
  property values. A chunk is embedded as
  "Collection: Plymouth Cordage Company records — Scope and contents: <text>"
  so its vector is grounded in whose note it is and what kind of note it is.
  The stored `text` property holds only the raw slice.
- The embedding lives on NoteChunk, never on Note: Note is the source of
  record (and stays in the full-text index); chunks are derived artifacts of
  the current chunking policy and are rebuilt from scratch every run.
- Every IndexableNote gets at least one chunk, so semantic queries search a
  single vector index regardless of note length.

ArchivalObject titles are embedded as the composed string
"Collection: <collection title> — Item: <ao title>" — generic titles like
"Correspondence" carry little meaning without their collection context. At
~427k AOs this is the longest part of the phase (~30 minutes at the vLLM
server's measured throughput).
"""

import logging
import re
import time
from typing import Any

import requests

from asapextractor import config
from asapextractor.extractors.base import BaseExtractor

logger = logging.getLogger(__name__)

# Chunking parameters. ~1,000 chars ≈ 256 tokens — half the model's 512-token
# window, leaving headroom for the contextual prefix and staying at the
# paragraph scale where bge-class embeddings retrieve best.
CHUNK_TARGET_CHARS = 1000
CHUNK_OVERLAP_CHARS = 200

# Human-readable labels for note types, used in the embedded prefix when the
# note has no explicit label of its own.
_NOTE_TYPE_LABELS = {
    "abstract": "Abstract",
    "scopecontent": "Scope and contents",
    "physdesc": "Physical description",
    "odd": "General note",
    "physfacet": "Physical facet",
    "acqinfo": "Acquisition information",
    "bioghist": "Biographical/Historical note",
    "note_bioghist": "Biographical/Historical note",
    "relatedmaterial": "Related material",
    "separatedmaterial": "Separated material",
    "custodhist": "Custodial history",
    "otherfindaid": "Other finding aid",
    "originalsloc": "Location of originals",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def chunk_text(
    text: str,
    target: int = CHUNK_TARGET_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Split text into ~target-char chunks on natural boundaries.

    Paragraphs are packed together while they fit; an oversized paragraph is
    split on sentence boundaries; an unpunctuated run longer than target is
    hard-sliced as a last resort. Consecutive chunks share ~overlap chars of
    trailing context so a thought straddling a boundary is retrievable from
    either side. Deterministic: same input always yields the same chunks.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= target:
        return [text]

    # Flatten into units no longer than target.
    units: list[str] = []
    for para in _PARAGRAPH_SPLIT.split(text):
        para = " ".join(para.split())
        if not para:
            continue
        if len(para) <= target:
            units.append(para)
            continue
        for sent in _SENTENCE_SPLIT.split(para):
            if len(sent) <= target:
                units.append(sent)
            else:
                units.extend(sent[i : i + target] for i in range(0, len(sent), target))

    # Pack units into chunks, carrying overlap from the previous chunk.
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 1 + len(unit) > target:
            chunks.append(current)
            tail = current[-overlap:]
            # Start the overlap at a word boundary.
            if " " in tail:
                tail = tail[tail.index(" ") + 1 :]
            current = f"{tail} {unit}"
        else:
            current = f"{current} {unit}".strip()
    if current:
        chunks.append(current)
    return chunks


def _compose_chunk_prefix(
    parent_label: str | None,
    parent_name: str | None,
    note_type: str,
    note_label: str | None,
) -> str:
    """Build the contextual prefix a chunk is embedded with."""
    note_kind = note_label or _NOTE_TYPE_LABELS.get(note_type, note_type)
    if parent_label and parent_name:
        return f"{parent_label}: {parent_name} — {note_kind}"
    return note_kind


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via the vLLM OpenAI-compatible endpoint.

    Retries transient failures; raises after three attempts so the pipeline
    fails loudly rather than silently skipping embeddings.
    """
    payload: dict[str, Any] = {"model": config.EMBEDDING_MODEL, "input": texts}
    headers: dict[str, str] = {}
    if config.EMBEDDING_API_KEY:
        headers["Authorization"] = f"Bearer {config.EMBEDDING_API_KEY}"
    else:
        # vLLM-only: prefix + chunk should fit the model's window, but truncate
        # rather than 400-error on the rare overshoot. Hosted APIs reject
        # unknown parameters, so only send it to the (keyless) local server.
        payload["truncate_prompt_tokens"] = -1
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{config.EMBEDDING_BASE_URL}/embeddings", json=payload,
                headers=headers, timeout=300,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("Embedding request failed (attempt %d/3): %s", attempt + 1, exc)
            time.sleep(2**attempt)
    raise RuntimeError(f"Embedding request failed after 3 attempts: {last_exc}")


class EmbeddingExtractor(BaseExtractor):
    """Chunk indexable notes, embed all semantic text units, build indexes."""

    # -- extract -----------------------------------------------------------

    def extract(self) -> list[Any]:
        """Read every text unit needing an embedding from Neo4j."""
        notes = self.neo4j.run_query(
            """
            MATCH (n:IndexableNote)
            WHERE n.content IS NOT NULL AND n.content <> ''
            OPTIONAL MATCH (parent)-[:HAS_NOTE]->(n)
            WITH n, collect(parent)[0] AS parent
            RETURN 'note' AS kind,
                   n.persistent_id AS pid,
                   n.content AS content,
                   n.type AS type,
                   n.label AS label,
                   CASE WHEN parent IS NULL THEN NULL ELSE labels(parent)[0] END AS parent_label,
                   CASE WHEN parent IS NULL THEN NULL ELSE
                        coalesce(parent.title, parent.display_name, parent.display_string)
                   END AS parent_name
            """
        )
        collections = self.neo4j.run_query(
            """
            MATCH (c:Collection)
            WHERE c.title IS NOT NULL AND c.title <> ''
            RETURN 'collection' AS kind, c.ead_id AS key,
                   c.title AS title, c.industry_path AS industry_path
            """
        )
        agents = self.neo4j.run_query(
            """
            MATCH (a:Agent)
            WHERE a.display_name IS NOT NULL AND a.display_name <> ''
            RETURN 'agent' AS kind, a.uri AS key, a.display_name AS display_name
            """
        )
        archival_objects = self.neo4j.run_query(
            """
            MATCH (ao:ArchivalObject)
            WHERE ao.title IS NOT NULL AND ao.title <> ''
            OPTIONAL MATCH (col:Collection)-[:HAS_PART*]->(ao)
            RETURN 'ao' AS kind, ao.uri AS key, ao.title AS title,
                   col.title AS collection_title
            """
        )
        return notes + collections + agents + archival_objects

    # -- transform ---------------------------------------------------------

    def transform(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Chunk notes and compose the contextual strings to be embedded."""
        records: list[dict[str, Any]] = []
        for row in raw:
            if row["kind"] == "note":
                prefix = _compose_chunk_prefix(
                    row["parent_label"], row["parent_name"], row["type"], row["label"]
                )
                for seq, chunk in enumerate(chunk_text(row["content"])):
                    records.append(
                        {
                            "kind": "chunk",
                            "note_pid": row["pid"],
                            "chunk_id": f"{row['pid']}#{seq}",
                            "seq": seq,
                            "text": chunk,
                            "embed_text": f"{prefix}: {chunk}",
                        }
                    )
            elif row["kind"] == "collection":
                embed_text = f"Collection: {row['title']}"
                industry = row["industry_path"] or ""
                if industry and not industry.startswith("Not Applicable"):
                    embed_text += f" — Industry: {industry}"
                records.append(
                    {"kind": "collection", "key": row["key"], "embed_text": embed_text}
                )
            elif row["kind"] == "agent":
                records.append(
                    {
                        "kind": "agent",
                        "key": row["key"],
                        "embed_text": f"Agent: {row['display_name']}",
                    }
                )
            elif row["kind"] == "ao":
                if row["collection_title"]:
                    embed_text = f"Collection: {row['collection_title']} — Item: {row['title']}"
                else:
                    embed_text = f"Item: {row['title']}"
                records.append(
                    {"kind": "ao", "key": row["key"], "embed_text": embed_text}
                )
        return records

    # -- load ----------------------------------------------------------------

    def load(self, records: list[dict[str, Any]]) -> None:
        chunks = [r for r in records if r["kind"] == "chunk"]
        collections = [r for r in records if r["kind"] == "collection"]
        agents = [r for r in records if r["kind"] == "agent"]
        archival_objects = [r for r in records if r["kind"] == "ao"]
        logger.info(
            "[EmbeddingExtractor] %d chunks, %d collections, %d agents, "
            "%d archival objects to embed.",
            len(chunks), len(collections), len(agents), len(archival_objects),
        )

        # Chunks are derived artifacts — rebuild from scratch every run so a
        # chunking-policy change never leaves stale chunks behind.
        self.neo4j.delete_note_chunks()
        for i in range(0, len(chunks), 1000):
            self.neo4j.create_note_chunks(chunks[i : i + 1000])
        logger.info("[EmbeddingExtractor] NoteChunk nodes created.")

        self._embed_and_store(chunks, "NoteChunk", "chunk_id", id_field="chunk_id")
        self._embed_and_store(collections, "Collection", "ead_id", id_field="key")
        self._embed_and_store(agents, "Agent", "uri", id_field="key")
        self._embed_and_store(archival_objects, "ArchivalObject", "uri", id_field="key")

        self.neo4j.create_vector_indexes()

    def _embed_and_store(
        self, records: list[dict[str, Any]], label: str, key_prop: str, id_field: str
    ) -> None:
        """Embed records in batches and write vectors onto their nodes."""
        batch_size = config.EMBEDDING_BATCH_SIZE
        total = len(records)
        for i in range(0, total, batch_size):
            batch = records[i : i + batch_size]
            vectors = _embed_batch([r["embed_text"] for r in batch])
            self.neo4j.set_node_embeddings(
                label,
                key_prop,
                [{"key": r[id_field], "vector": v} for r, v in zip(batch, vectors)],
            )
            done = min(i + batch_size, total)
            if done % 5000 < batch_size or done == total:
                logger.info("[EmbeddingExtractor] %s: %d/%d embedded.", label, done, total)
