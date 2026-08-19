"""Entry point for the ArchivesSpace → Neo4j data extraction pipeline."""

import logging
import sys

from asapextractor import config
from asapextractor.aspace_client import ASpaceClient
from asapextractor.neo4j_client import Neo4jClient
from asapextractor.extractors.resources import ResourceExtractor
from asapextractor.extractors.archival_objects import ArchivalObjectExtractor
from asapextractor.extractors.digital_objects import DigitalObjectExtractor
from asapextractor.extractors.agents import AgentExtractor
from asapextractor.extractors.accessions import AccessionExtractor
from asapextractor.extractors.embeddings import EmbeddingExtractor
from asapextractor.extractors.metadata import MetadataExtractor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("=== aspaceanalytics extraction pipeline starting ===")

    with Neo4jClient() as neo4j:
        # Rebuild from a clean graph so records deleted in ArchivesSpace
        # don't linger as stale nodes. Skipped when WIPE_GRAPH=false.
        if config.WIPE_GRAPH:
            neo4j.wipe_graph()
        else:
            logger.info("WIPE_GRAPH=false — skipping graph wipe.")

        neo4j.ensure_constraints()

        # Establish a connection to ArchivesSpace backend
        aspace = ASpaceClient()

        # Begin running all extractors

        # 1. Collections, Notes, Extents, RevisionStatements, stub Agents/Accessions
        ResourceExtractor(aspace, neo4j).run()

        # 2. Bulk-fetch full AO records + HAS_PART + HAS_DIGITAL_OBJECT links
        ArchivalObjectExtractor(aspace, neo4j).run()

        # 3. Enrich DigitalObject stubs + FileVersion nodes + agent links
        DigitalObjectExtractor(aspace, neo4j).run()

        # 4. Full accession records + extents + accession parts + collection/agent links
        #    Must run before AgentExtractor so agent stubs from accessions are created first.
        AccessionExtractor(aspace, neo4j).run()

        # 5. Agent nodes scoped to repo 11 + agent notes + inter-agent relationships
        AgentExtractor(aspace, neo4j).run()

        # 6. Full-text search setup — labels indexable notes and builds the
        #    cross-label index. Must follow every extractor that creates Notes.
        neo4j.setup_fulltext_search()

        # 7. Semantic search — NoteChunk nodes, embeddings via vLLM, and
        #    vector indexes. Must follow setup_fulltext_search, which applies
        #    the :IndexableNote labels this phase reads.
        EmbeddingExtractor(aspace, neo4j).run()

        # 8. Schema metadata — describes nodes, relationships, and properties
        MetadataExtractor(aspace, neo4j).run()

    logger.info("=== Extraction complete ===")


if __name__ == "__main__":
    main()
