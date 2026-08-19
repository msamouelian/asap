"""Docling conversion and chunking.

convert_and_chunk() is a module-level function so ProcessPoolExecutor can
pickle it; the (expensive) DocumentConverter and tokenizer are lazy globals
initialised once per worker process.

Chunking uses docling's HybridChunker: structure-aware (respects headings,
tables, lists) and tokenizer-aware, capped below the embedding model's
512-token window. Each chunk's embed_text is a composed contextual string —
"Document: <file> — <headings + text>" — mirroring the NoteChunk design:
the vector carries the chunk's provenance, while the stored text stays raw.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

from asapdocworker import config

log = logging.getLogger(__name__)

# Formats accepted for ingestion. Anything else in the upload is skipped
# (recorded in the worker log, not treated as a failure).
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".md", ".txt", ".html", ".pptx", ".xlsx"}


@dataclass
class Chunk:
    text: str          # raw chunk text (stored on the node, shown to users)
    embed_text: str    # contextualised string that gets embedded
    seq: int
    page_no: int | None
    heading_path: str | None


@dataclass
class ConversionResult:
    chunks: list[Chunk] = field(default_factory=list)
    page_count: int | None = None
    error: str | None = None


# ── Lazy per-process singletons ───────────────────────────────────────────────

_converter = None
_chunker = None


def _get_converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter
        _converter = DocumentConverter()
    return _converter


def _get_chunker():
    global _chunker
    if _chunker is None:
        from docling.chunking import HybridChunker
        _chunker = HybridChunker(
            tokenizer=config.EMBEDDING_MODEL,
            max_tokens=config.CHUNK_MAX_TOKENS,
        )
    return _chunker


def _convert(path: Path):
    """Convert a file to a DoclingDocument. Plain .txt is parsed as Markdown
    (a safe superset for prose) since docling has no dedicated txt pipeline."""
    converter = _get_converter()
    if path.suffix.lower() == ".txt":
        from docling.datamodel.base_models import DocumentStream
        stream = DocumentStream(
            name=path.with_suffix(".md").name,
            stream=io.BytesIO(path.read_bytes()),
        )
        return converter.convert(stream)
    return converter.convert(path)


def convert_and_chunk(path: Path) -> ConversionResult:
    """Convert one document and chunk it. Never raises: per-document failures
    are returned as ConversionResult(error=...) so one corrupt file cannot
    sink a whole collection."""
    try:
        result = _convert(path)
        doc = result.document

        page_count: int | None = None
        try:
            page_count = doc.num_pages() or None
        except Exception:
            pass

        chunker = _get_chunker()
        chunks: list[Chunk] = []
        for seq, chunk in enumerate(chunker.chunk(doc)):
            text = (chunk.text or "").strip()
            if not text:
                continue

            headings = list(getattr(chunk.meta, "headings", None) or [])
            heading_path = " > ".join(headings) if headings else None

            page_no: int | None = None
            try:
                items = getattr(chunk.meta, "doc_items", None) or []
                if items and items[0].prov:
                    page_no = items[0].prov[0].page_no
            except Exception:
                pass

            # contextualize() prepends docling's heading context to the text;
            # the file name adds cross-document provenance.
            contextualised = chunker.contextualize(chunk)
            embed_text = f"Document: {path.name} — {contextualised}"

            chunks.append(Chunk(
                text=text,
                embed_text=embed_text,
                seq=len(chunks),
                page_no=page_no,
                heading_path=heading_path,
            ))

        if not chunks:
            return ConversionResult(
                page_count=page_count,
                error="No text content could be extracted.",
            )
        return ConversionResult(chunks=chunks, page_count=page_count)

    except Exception as exc:
        log.warning("Conversion failed for %s: %s", path.name, exc)
        return ConversionResult(error=f"{type(exc).__name__}: {exc}"[:500])
