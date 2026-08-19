"""Document ingestion job: convert → chunk → embed → load.

Runs as a Kubernetes Job spawned by asapbackend. Reads its work order from
the document_processing_job row identified by $JOB_ID, processes every file
under $UPLOAD_ROOT/$JOB_ID, and writes DocumentCollection / Document /
DocumentChunk nodes to Neo4j. Progress is written back to the job row so the
UI can poll it; the staged files are deleted at the end (originals are not
retained — re-chunking requires re-upload).

Failure semantics:
- A single unreadable file marks THAT Document as failed and continues.
- A worker-level failure marks the job and the collection node as failed;
  partially-written nodes are removed so no half-ingested collection is ever
  visible or retrievable.
"""

import hashlib
import logging
import shutil
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import requests
from neo4j import GraphDatabase

from asapdocworker import config
from asapdocworker.processing import SUPPORTED_SUFFIXES, convert_and_chunk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("asapdocworker")

# The HybridChunker measures oversized candidate segments with the bge
# tokenizer before splitting them, which triggers transformers' "Token
# indices sequence length is longer than..." warning. No model runs on those
# sequences (final chunks are capped, and vLLM truncates defensively), so
# the warning is pure noise in job logs.
logging.getLogger("transformers").setLevel(logging.ERROR)


# ── Progress / job row ────────────────────────────────────────────────────────

def _update_job(conn: psycopg.Connection, **fields) -> None:
    sets = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(
        f"UPDATE document_processing_job SET {sets}, updated_ts = now() WHERE id = %s",
        [*fields.values(), config.JOB_ID],
    )
    conn.commit()


def _load_job(conn: psycopg.Connection) -> dict:
    row = conn.execute(
        """
        SELECT user_id, document_collection_id, collection_title,
               collection_description, visibility, source_location
        FROM document_processing_job WHERE id = %s
        """,
        [config.JOB_ID],
    ).fetchone()
    if row is None:
        raise RuntimeError(f"No document_processing_job row with id {config.JOB_ID}")
    keys = ["user_id", "document_collection_id", "collection_title",
            "collection_description", "visibility", "source_location"]
    return dict(zip(keys, [str(v) if v is not None else v for v in row]))


# ── Embeddings ────────────────────────────────────────────────────────────────

def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch via vLLM with retries; raises after 3 attempts."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{config.VLLM_BASE_URL}/embeddings",
                json={
                    "model": config.EMBEDDING_MODEL,
                    "input": texts,
                    "truncate_prompt_tokens": -1,
                },
                timeout=300,
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]
        except requests.RequestException as exc:
            last = exc
            log.warning("embed retry %d/3: %s", attempt + 1, exc)
            time.sleep(2**attempt)
    raise RuntimeError(f"embedding failed after 3 attempts: {last}")


# ── Neo4j writes ──────────────────────────────────────────────────────────────

def _create_collection_node(driver, job: dict) -> None:
    with driver.session() as s:
        s.run(
            """
            MERGE (dc:DocumentCollection {id: $id})
            SET dc.title = $title,
                dc.description = $description,
                dc.source_location = $source_location,
                dc.uploaded_by = $uploaded_by,
                dc.visibility = $visibility,
                dc.archived = false,
                dc.status = 'processing',
                dc.created_at = datetime()
            """,
            id=job["document_collection_id"],
            title=job["collection_title"],
            description=job["collection_description"],
            source_location=job["source_location"],
            uploaded_by=job["user_id"],
            visibility=job["visibility"],
        )


def _set_collection_status(driver, collection_id: str, status: str) -> None:
    with driver.session() as s:
        s.run(
            "MATCH (dc:DocumentCollection {id: $id}) SET dc.status = $status",
            id=collection_id, status=status,
        )


def _delete_collection_tree(driver, collection_id: str) -> None:
    """Remove a partially-ingested collection (worker-level failure path)."""
    with driver.session() as s:
        s.run(
            """
            MATCH (dc:DocumentCollection {id: $id})
            OPTIONAL MATCH (dc)-[:CONTAINS]->(d:Document)
            OPTIONAL MATCH (d)-[:HAS_CHUNK]->(ch:DocumentChunk)
            DETACH DELETE dc, d, ch
            """,
            id=collection_id,
        )


def _write_document(driver, collection_id: str, doc: dict, chunks: list[dict]) -> None:
    """Create one Document node and its chunks (embeddings included)."""
    with driver.session() as s:
        s.run(
            """
            MATCH (dc:DocumentCollection {id: $collection_id})
            CREATE (d:Document {
                id: $id, file_name: $file_name, relative_path: $relative_path,
                format: $format, size_bytes: $size_bytes, sha256: $sha256,
                chunk_count: $chunk_count, processing_status: $processing_status,
                uploaded_at: datetime()
            })
            SET d.page_count = $page_count, d.error = $error
            CREATE (dc)-[:CONTAINS]->(d)
            """,
            collection_id=collection_id, **doc,
        )
        for i in range(0, len(chunks), 200):
            s.run(
                """
                MATCH (d:Document {id: $doc_id})
                UNWIND $rows AS row
                CREATE (ch:DocumentChunk {id: row.id, text: row.text, seq: row.seq})
                SET ch.page_no = row.page_no, ch.heading_path = row.heading_path
                CREATE (d)-[:HAS_CHUNK]->(ch)
                WITH ch, row
                CALL db.create.setNodeVectorProperty(ch, 'embedding', row.embedding)
                """,
                doc_id=doc["id"], rows=chunks[i : i + 200],
            )


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not config.JOB_ID:
        raise SystemExit("JOB_ID is required")

    staging = Path(config.UPLOAD_ROOT) / config.JOB_ID
    conn = psycopg.connect(config.DATABASE_URL)
    driver = GraphDatabase.driver(
        config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
    )

    job = _load_job(conn)
    collection_id = job["document_collection_id"]
    log.info("Job %s: collection %r (%s)", config.JOB_ID, job["collection_title"], collection_id)

    files = sorted(
        p for p in staging.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    skipped = sorted(
        str(p.relative_to(staging)) for p in staging.rglob("*")
        if p.is_file() and p.suffix.lower() not in SUPPORTED_SUFFIXES
    )
    if skipped:
        log.warning("Skipping %d unsupported file(s): %s", len(skipped), skipped[:10])

    try:
        _update_job(conn, status="running", total_documents=len(files))
        _create_collection_node(driver, job)

        if not files:
            raise RuntimeError("No supported documents found in the upload.")

        total_chunks = 0
        processed = 0
        failed_docs = 0
        first_error: str | None = None

        # Convert documents in parallel; embed + write sequentially as each
        # conversion lands (embedding is the vLLM server's job, not ours).
        # Progress granularity: docling exposes no per-page hooks, so the
        # finest visible stages are per-document conversion and per-batch
        # embedding — current_document narrates both.
        with ProcessPoolExecutor(max_workers=config.CONVERT_WORKERS) as pool:
            results = iter(pool.map(convert_and_chunk, files))
            for path in files:
                rel = str(path.relative_to(staging))
                # Set BEFORE blocking on the conversion result, so the UI
                # shows what the worker is chewing on during the long phase.
                _update_job(conn, current_document=f"Converting {rel}")
                result = next(results)

                doc = {
                    "id": str(uuid.uuid4()),
                    "file_name": path.name,
                    "relative_path": rel,
                    "format": path.suffix.lstrip(".").lower(),
                    "size_bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "page_count": result.page_count,
                    "chunk_count": len(result.chunks),
                    "processing_status": "ok" if result.error is None else "failed",
                    "error": result.error,
                }

                chunk_rows: list[dict] = []
                if result.error is None and result.chunks:
                    # Embed the composed contextual string; store the raw text.
                    # Tick progress per embedding batch — for a 400-chunk PDF
                    # this is the difference between a live counter and a bar
                    # frozen at zero for minutes.
                    for i in range(0, len(result.chunks), config.EMBEDDING_BATCH_SIZE):
                        batch = result.chunks[i : i + config.EMBEDDING_BATCH_SIZE]
                        vectors = _embed([c.embed_text for c in batch])
                        for c, v in zip(batch, vectors):
                            chunk_rows.append({
                                "id": str(uuid.uuid4()),
                                "text": c.text,
                                "seq": c.seq,
                                "page_no": c.page_no,
                                "heading_path": c.heading_path,
                                "embedding": v,
                            })
                        _update_job(
                            conn,
                            total_chunks=total_chunks + len(chunk_rows),
                            current_document=(
                                f"Embedding {rel} "
                                f"({len(chunk_rows)}/{len(result.chunks)} chunks)"
                            ),
                        )
                else:
                    failed_docs += 1
                    if first_error is None:
                        first_error = f"{rel}: {result.error}"
                    log.warning("Document failed: %s (%s)", rel, result.error)

                _write_document(driver, collection_id, doc, chunk_rows)
                processed += 1
                total_chunks += len(chunk_rows)
                _update_job(
                    conn,
                    processed_documents=processed,
                    total_chunks=total_chunks,
                )
                log.info("%s: %d chunks (%d/%d docs)", rel, len(chunk_rows), processed, len(files))

        if failed_docs == len(files):
            raise RuntimeError(
                "Every document in the upload failed to process. "
                f"First error — {first_error}"
            )

        _set_collection_status(driver, collection_id, "ready")
        _update_job(
            conn, status="completed", current_document=None,
            completed_ts=datetime.now(timezone.utc),
        )
        log.info(
            "Collection %s ready: %d documents (%d failed), %d chunks.",
            collection_id, processed, failed_docs, total_chunks,
        )

    except Exception as exc:
        log.exception("Job failed")
        _delete_collection_tree(driver, collection_id)
        _set_collection_status(driver, collection_id, "failed")  # tombstone
        _create_failed_tombstone(driver, job)
        _update_job(
            conn, status="failed", error=str(exc)[:2000],
            completed_ts=datetime.now(timezone.utc),
        )
        raise SystemExit(1)
    finally:
        # Originals are not retained (design decision): remove staged files.
        shutil.rmtree(staging, ignore_errors=True)
        conn.close()
        driver.close()


def _create_failed_tombstone(driver, job: dict) -> None:
    """Leave a minimal failed collection node so the failure is diagnosable
    in listings (status='failed'), without any document content attached."""
    with driver.session() as s:
        s.run(
            """
            MERGE (dc:DocumentCollection {id: $id})
            SET dc.title = $title, dc.description = $description,
                dc.source_location = $source_location, dc.uploaded_by = $uploaded_by,
                dc.visibility = $visibility, dc.archived = false, dc.status = 'failed'
            """,
            id=job["document_collection_id"], title=job["collection_title"],
            description=job["collection_description"],
            source_location=job["source_location"],
            uploaded_by=job["user_id"], visibility=job["visibility"],
        )


if __name__ == "__main__":
    main()
