"""Entry point for the knowledge graph generation pipeline.

Phase 1 implements `extract`: per pilot collection, build the text surface
from the extracted graph, run LLM entity/relation extraction chunk by chunk,
merge within the collection, and checkpoint the result as a Postgres
artifact. `resolve` (cross-collection entity resolution) and `load` (Neo4j
:Inferred load) build on these artifacts in later phases.

Usage:
    uv run --project packages/kggenerator python -m kggenerator.main extract
        [--force] [--max-chunks N] [--collections "Title A||Title B"]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from kggenerator import config
from kggenerator.artifacts import ArtifactStore
from kggenerator.chunker import chunk_surface
from kggenerator.llm import ExtractionError, KGExtractor
from kggenerator.merge import merge_chunk_graphs
from kggenerator.surface import GraphReader, build_surface, resolve_collections

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def cmd_extract(args: argparse.Namespace) -> None:
    run_id = datetime.now(timezone.utc).strftime("kg-%Y%m%d-%H%M%S")
    titles = (
        [t.strip() for t in args.collections.split("||") if t.strip()]
        if args.collections
        else config.PILOT_COLLECTION_TITLES
    )
    logger.info("=== KG extraction %s over %d collections ===", run_id, len(titles))

    with GraphReader() as reader, ArtifactStore() as store, KGExtractor() as llm:
        collections = resolve_collections(reader, titles)
        done = failed = skipped = 0
        for coll in collections:
            existing = store.get(coll["uri"])
            if existing and existing["status"] == "extracted" and not args.force:
                logger.info("Skipping %r — already extracted (%s). Use --force "
                            "to re-extract.", coll["title"], existing["run_id"])
                skipped += 1
                continue
            try:
                _extract_collection(coll, run_id, reader, store, llm,
                                    max_chunks=args.max_chunks)
                done += 1
            except Exception as exc:  # checkpoint the failure, keep going
                logger.exception("Extraction failed for %r", coll["title"])
                store.mark_failed(coll["uri"], f"{type(exc).__name__}: {exc}")
                failed += 1

    logger.info("=== KG extraction complete: %d extracted, %d failed, "
                "%d skipped ===", done, failed, skipped)
    if failed:
        sys.exit(1)


def _extract_collection(coll, run_id, reader, store, llm, max_chunks: int) -> None:
    started = time.monotonic()
    surface = build_surface(reader, coll)
    store.begin_extraction(run_id, coll["uri"], coll["title"], surface)

    chunks = chunk_surface(surface)
    if max_chunks and len(chunks) > max_chunks:
        logger.warning("--max-chunks %d: processing %d of %d chunks of %r "
                       "(development cap — the artifact will be PARTIAL).",
                       max_chunks, max_chunks, len(chunks), coll["title"])
        chunks = chunks[:max_chunks]

    chunk_graphs = []
    for n, chunk in enumerate(chunks, start=1):
        graph = llm.extract_chunk(chunk)
        chunk_graphs.append(graph)
        logger.info("%r chunk %d/%d: %d entities, %d relations.",
                    coll["title"], n, len(chunks),
                    len(graph["entities"]), len(graph["relations"]))

    merged = merge_chunk_graphs(chunk_graphs, surface)
    elapsed = round(time.monotonic() - started, 1)
    stats = {
        "text_units": len(surface["texts"]),
        "chunks_total": len(chunk_surface(surface)),
        "chunks_processed": len(chunks),
        "partial": bool(max_chunks) and len(chunks) < len(chunk_surface(surface)),
        "entities": len(merged["entities"]),
        "relations": len(merged["relations"]),
        "seconds": elapsed,
    }
    store.save_graph(coll["uri"], merged, stats)
    logger.info("Artifact saved for %r: %d entities, %d relations in %.1fs.",
                coll["title"], stats["entities"], stats["relations"], elapsed)


def cmd_resolve(args: argparse.Namespace) -> None:
    from kggenerator.resolve import resolve_artifacts

    run_id = datetime.now(timezone.utc).strftime("kgres-%Y%m%d-%H%M%S")
    with ArtifactStore() as store:
        artifacts = store.load_extracted_artifacts()
        if not artifacts:
            logger.error("No extracted artifacts found — run `extract` first.")
            sys.exit(1)
        logger.info("=== KG resolve %s over %d artifacts ===",
                    run_id, len(artifacts))
        if args.no_llm:
            merged, decisions = resolve_artifacts(artifacts, llm=None)
        else:
            with KGExtractor() as llm:
                merged, decisions = resolve_artifacts(artifacts, llm=llm)
        stats = decisions.pop("stats")
        store.save_resolution(run_id, merged, decisions, stats)
    logger.info("=== KG resolve complete: %s ===",
                ", ".join(f"{k}={v}" for k, v in stats.items()))


def cmd_load(args: argparse.Namespace) -> None:
    from kggenerator.load import load_resolution

    with ArtifactStore() as store:
        resolution = store.load_latest_resolution()
    if not resolution:
        logger.error("No resolution found — run `resolve` first.")
        sys.exit(1)
    logger.info("=== KG load of resolution %s (created %s) ===",
                resolution["run_id"], resolution["created_at"])
    stats = load_resolution(resolution)
    logger.info("=== KG load complete: %s ===",
                ", ".join(f"{k}={v}" for k, v in stats.items()))


def cmd_all(args: argparse.Namespace) -> None:
    """Full pipeline for the Kubernetes Job: extract → resolve → load.

    Extraction is incremental (collections with existing artifacts are
    skipped) so a re-run only pays LLM cost for new/failed collections;
    set KG_FORCE=true in the Job environment to re-extract everything.
    The load step always deletes and rebuilds the entire :Inferred graph.
    """
    import os

    force = os.environ.get("KG_FORCE", "").strip().lower() in ("true", "1", "yes")
    extract_args = argparse.Namespace(force=force, max_chunks=0, collections="")
    cmd_extract(extract_args)
    cmd_resolve(argparse.Namespace(no_llm=False))
    cmd_load(args)


def main() -> None:
    parser = argparse.ArgumentParser(description="ASAP knowledge graph generator")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="LLM extraction per collection")
    p_extract.add_argument("--force", action="store_true",
                           help="re-extract collections that already have artifacts")
    p_extract.add_argument("--max-chunks", type=int, default=0,
                           help="development cap on chunks per collection (partial artifact)")
    p_extract.add_argument("--collections", default="",
                           help="'||'-separated collection titles (default: pilot list)")
    p_extract.set_defaults(func=cmd_extract)

    p_resolve = sub.add_parser(
        "resolve", help="cross-collection entity resolution over artifacts")
    p_resolve.add_argument("--no-llm", action="store_true",
                           help="skip LLM adjudication (borderline pairs stay separate)")
    p_resolve.set_defaults(func=cmd_resolve)

    p_load = sub.add_parser(
        "load", help="load the latest resolution into Neo4j as :Inferred nodes")
    p_load.set_defaults(func=cmd_load)

    p_all = sub.add_parser(
        "all", help="extract (incremental) + resolve + load — the Job entrypoint")
    p_all.set_defaults(func=cmd_all)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
