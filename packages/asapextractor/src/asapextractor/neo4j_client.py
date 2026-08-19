"""Neo4j client: driver wrapper with MERGE/link helpers for non-destructive updates."""

import logging
from types import TracebackType
from typing import Any, Self

from neo4j import Driver, GraphDatabase

from asapextractor import config
from asapextractor.utils import clean_props, label_from_uri, sanitize_rel_type

logger = logging.getLogger(__name__)

# Mapping from ArchivesSpace linked_agent role to Neo4j relationship type
_AGENT_ROLE_MAP: dict[str, str] = {
    "creator": "CREATED_BY",
    "subject": "HAS_SUBJECT",
    "source": "SOURCE",
}


class Neo4jClient:
    """Manages the Neo4j driver and exposes MERGE/link operations for each node type."""

    def __init__(self) -> None:
        self._driver: Driver = GraphDatabase.driver(
            config.NEO4J_URI,
            auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
            keep_alive=True,
        )
        logger.info("Neo4jClient connected to %s", config.NEO4J_URI)

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Graph wipe
    # ------------------------------------------------------------------

    def wipe_graph(self) -> None:
        """Delete the archival graph in batches, PRESERVING document nodes.

        RAG document-library nodes (DocumentCollection, Document,
        DocumentChunk) are uploaded by users through the backend and are not
        rebuilt by extraction — wiping them would orphan Postgres references
        and break conversation rehydration. They only relate to each other,
        so DETACH DELETE on the archival side cannot leave dangling
        relationships. (Neo4j Community has a single database, so label
        exclusion is the isolation mechanism — separate databases are an
        Enterprise feature.)

        Knowledge-graph nodes (label :Inferred, produced by the kggenerator
        job) are likewise preserved: extraction only deletes the data it
        generates. Inferred nodes never hold hard relationships to extracted
        nodes (they link via the extracted_agent_twin uri property), so
        DETACH DELETE on the extracted side cannot orphan them.
        See docs/kg-conventions.md.

        Uses apoc.periodic.iterate so deletion is committed in batches of 1000
        and never builds one giant transaction. Constraints and indices are
        unaffected. Raises RuntimeError if any batch fails.
        """
        logger.info("Wiping Neo4j graph (preserving document-library nodes)...")
        cypher = """
            CALL apoc.periodic.iterate(
              "MATCH (n) WHERE NOT n:DocumentCollection AND NOT n:Document AND NOT n:DocumentChunk AND NOT n:Inferred RETURN n",
              "DETACH DELETE n",
              {batchSize: 1000}
            )
            YIELD total, batches, failedBatches, errorMessages
            RETURN total, batches, failedBatches, errorMessages
        """
        with self._driver.session() as session:
            record = session.run(cypher).single()
        if record is None:
            raise RuntimeError("Graph wipe returned no result.")
        if record["failedBatches"]:
            raise RuntimeError(
                f"Graph wipe failed: {record['failedBatches']} of "
                f"{record['batches']} batches errored: {record['errorMessages']}"
            )
        logger.info(
            "Graph wiped: %d nodes deleted in %d batches.",
            record["total"], record["batches"],
        )

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------

    def ensure_constraints(self) -> None:
        """Create uniqueness constraints and indices required by the graph schema.

        Safe to call repeatedly — all statements use IF NOT EXISTS.
        """
        statements = [
            # Uniqueness constraints (also create supporting indices)
            "CREATE CONSTRAINT collection_ead_id IF NOT EXISTS "
            "FOR (c:Collection) REQUIRE c.ead_id IS UNIQUE",

            "CREATE CONSTRAINT note_persistent_id IF NOT EXISTS "
            "FOR (n:Note) REQUIRE n.persistent_id IS UNIQUE",

            "CREATE CONSTRAINT agent_uri IF NOT EXISTS "
            "FOR (a:Agent) REQUIRE a.uri IS UNIQUE",

            "CREATE CONSTRAINT accession_uri IF NOT EXISTS "
            "FOR (acc:Accession) REQUIRE acc.uri IS UNIQUE",

            "CREATE CONSTRAINT archival_object_uri IF NOT EXISTS "
            "FOR (ao:ArchivalObject) REQUIRE ao.uri IS UNIQUE",

            # Index on Collection.uri for cross-node link lookups
            "CREATE INDEX collection_uri_idx IF NOT EXISTS "
            "FOR (c:Collection) ON (c.uri)",

            "CREATE CONSTRAINT digital_object_uri IF NOT EXISTS "
            "FOR (d:DigitalObject) REQUIRE d.uri IS UNIQUE",

            "CREATE CONSTRAINT file_version_file_uri IF NOT EXISTS "
            "FOR (fv:FileVersion) REQUIRE fv.file_uri IS UNIQUE",

            "CREATE CONSTRAINT note_chunk_chunk_id IF NOT EXISTS "
            "FOR (ch:NoteChunk) REQUIRE ch.chunk_id IS UNIQUE",

            # RAG document-library nodes (written by the asapbackend document
            # worker, not this extractor; constraints kept here so the whole
            # graph schema is declared in one place).
            "CREATE CONSTRAINT document_collection_id IF NOT EXISTS "
            "FOR (dc:DocumentCollection) REQUIRE dc.id IS UNIQUE",

            "CREATE CONSTRAINT document_id IF NOT EXISTS "
            "FOR (d:Document) REQUIRE d.id IS UNIQUE",

            "CREATE CONSTRAINT document_chunk_id IF NOT EXISTS "
            "FOR (ch:DocumentChunk) REQUIRE ch.id IS UNIQUE",
        ]
        with self._driver.session() as session:
            for stmt in statements:
                session.run(stmt)
            # Search indexes for RAG document chunks. Deliberately IF NOT
            # EXISTS (never drop+recreate like the archival indexes): document
            # nodes survive extraction wipes, so dropping these would force a
            # full re-index of data this pipeline does not rebuild.
            session.run(
                "CREATE VECTOR INDEX document_chunk_embedding IF NOT EXISTS "
                "FOR (ch:DocumentChunk) ON ch.embedding "
                "OPTIONS { indexConfig: { "
                f"`vector.dimensions`: {config.EMBEDDING_DIMENSIONS}, "
                "`vector.similarity_function`: 'cosine' } }"
            )
            session.run(
                "CREATE FULLTEXT INDEX document_chunk_fulltext IF NOT EXISTS "
                "FOR (ch:DocumentChunk) ON EACH [ch.text] "
                "OPTIONS { indexConfig: { `fulltext.analyzer`: 'english' } }"
            )
        self.ensure_schema_constraints()
        logger.info("Neo4j constraints and indices verified.")

    # ------------------------------------------------------------------
    # Full-text search
    # ------------------------------------------------------------------

    # Note types whose text is worth full-text indexing (narrative/descriptive
    # content). Notes of these types get a second :IndexableNote label because
    # Neo4j full-text indexes cannot filter on property values — the extra
    # label is the standard workaround for a partial index.
    INDEXABLE_NOTE_TYPES = [
        # Curated summaries — often the ONLY place structured facts like
        # correspondent lists appear (accuracy eval Q6 found 654 abstract
        # notes invisible to every search tool; added 2026-08-12).
        "abstract",
        "scopecontent",
        "physdesc",
        "odd",
        "physfacet",
        "acqinfo",
        "bioghist",
        "note_bioghist",
        "relatedmaterial",
        "separatedmaterial",
        "custodhist",
        "otherfindaid",
        "originalsloc",
    ]

    def setup_fulltext_search(self) -> None:
        """Label indexable notes and create the cross-label full-text index.

        Must run after all extractors so every Note node exists. Safe to call
        repeatedly: labelling skips already-labelled notes and index creation
        uses IF NOT EXISTS.
        """
        logger.info("Applying :IndexableNote label to searchable note types...")
        label_cypher = """
            CALL apoc.periodic.iterate(
              "MATCH (n:Note) WHERE n.type IN $types AND NOT n:IndexableNote RETURN n",
              "SET n:IndexableNote",
              {batchSize: 5000, params: {types: $types}}
            )
            YIELD total, batches, failedBatches, errorMessages
            RETURN total, failedBatches, errorMessages
        """
        with self._driver.session() as session:
            record = session.run(
                label_cypher, types=self.INDEXABLE_NOTE_TYPES
            ).single()
            if record and record["failedBatches"]:
                raise RuntimeError(
                    f"IndexableNote labelling failed: {record['errorMessages']}"
                )
            logger.info(
                "Labelled %s notes as :IndexableNote.",
                record["total"] if record else 0,
            )

            logger.info("Creating full-text index free_text_index...")
            # Full-text index definitions cannot be altered, and IF NOT EXISTS
            # would silently keep an outdated definition. Drop and recreate so
            # the index always matches the code; the rebuild happens once here
            # rather than incrementally during data load.
            session.run("DROP INDEX free_text_index IF EXISTS")
            # The 'english' analyzer applies Porter stemming + stop words so
            # chart/charts and similar inflections match each other. The
            # default (standard-no-stop-words) matches exact tokens only,
            # which buried plural/singular variants deep in the rankings.
            session.run(
                "CREATE FULLTEXT INDEX free_text_index "
                "FOR (n:Collection|IndexableNote|ArchivalObject|Agent|Accession) "
                "ON EACH [n.title, n.industry_path, n.content, n.label, "
                "n.display_name, n.provenance] "
                "OPTIONS { indexConfig: { `fulltext.analyzer`: 'english' } }"
            )
            # Population is asynchronous; block until searchable so a query
            # issued right after the pipeline finishes doesn't miss results.
            session.run("CALL db.awaitIndex('free_text_index', 600)")
        logger.info("Full-text index free_text_index is online.")

    # ------------------------------------------------------------------
    # Semantic search (embeddings)
    # ------------------------------------------------------------------

    def run_query(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Run a read query and return all rows as dicts."""
        with self._driver.session() as session:
            return [record.data() for record in session.run(cypher, **params)]

    def delete_note_chunks(self) -> None:
        """Remove all NoteChunk nodes (they are rebuilt every run)."""
        cypher = """
            CALL apoc.periodic.iterate(
              "MATCH (ch:NoteChunk) RETURN ch",
              "DETACH DELETE ch",
              {batchSize: 5000}
            )
            YIELD failedBatches, errorMessages
            RETURN failedBatches, errorMessages
        """
        with self._driver.session() as session:
            record = session.run(cypher).single()
        if record and record["failedBatches"]:
            raise RuntimeError(f"NoteChunk cleanup failed: {record['errorMessages']}")

    def create_note_chunks(self, rows: list[dict[str, Any]]) -> None:
        """Create NoteChunk nodes linked to their parent notes.

        Plain CREATE (not MERGE): delete_note_chunks() always runs first, so
        chunks never pre-exist.
        """
        cypher = """
            UNWIND $rows AS row
            // Seek on :Note — the uniqueness constraint (and its index) lives
            // on the Note label, and indexes are label-specific: matching
            // :IndexableNote alone forces a full label scan per row. The
            // WHERE clause then enforces (cheaply, on the single found node)
            // that chunks are only ever attached to indexable notes.
            MATCH (n:Note {persistent_id: row.note_pid})
            WHERE n:IndexableNote
            CREATE (ch:NoteChunk {chunk_id: row.chunk_id, seq: row.seq, text: row.text})
            CREATE (n)-[:HAS_CHUNK]->(ch)
        """
        with self._driver.session() as session:
            session.run(cypher, rows=rows)

    def set_node_embeddings(
        self, label: str, key_prop: str, rows: list[dict[str, Any]]
    ) -> None:
        """Write embedding vectors onto nodes of the given label.

        rows: [{key: <key_prop value>, vector: [float, ...]}, ...]
        Uses db.create.setNodeVectorProperty so vectors are stored in Neo4j's
        compact float array representation rather than as generic lists.
        """
        # label/key_prop come from internal constants, never user input.
        cypher = f"""
            UNWIND $rows AS row
            MATCH (n:{label} {{{key_prop}: row.key}})
            CALL db.create.setNodeVectorProperty(n, 'embedding', row.vector)
        """
        with self._driver.session() as session:
            session.run(cypher, rows=rows)

    def create_vector_indexes(self) -> None:
        """Create the semantic vector indexes (drop + recreate).

        Vector index definitions cannot be altered, and IF NOT EXISTS would
        silently keep an outdated definition — same rationale as the
        full-text index. Also drops legacy index names from earlier manual
        experimentation.
        """
        indexes = {
            "note_chunk_embedding": "NoteChunk",
            "collection_embedding": "Collection",
            "agent_embedding": "Agent",
            "archival_object_embedding": "ArchivalObject",
        }
        with self._driver.session() as session:
            session.run("DROP INDEX collection_title_embedding IF EXISTS")  # legacy
            for name, label in indexes.items():
                session.run(f"DROP INDEX {name} IF EXISTS")
                session.run(
                    f"CREATE VECTOR INDEX {name} "
                    f"FOR (n:{label}) ON n.embedding "
                    "OPTIONS { indexConfig: { "
                    f"`vector.dimensions`: {config.EMBEDDING_DIMENSIONS}, "
                    "`vector.similarity_function`: 'cosine' } }"
                )
            for name in indexes:
                session.run(f"CALL db.awaitIndex('{name}', 600)")
        logger.info("Vector indexes online: %s", ", ".join(indexes))

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    def merge_collection(self, data: dict[str, Any]) -> None:
        """Non-destructively upsert a Collection node keyed on ead_id."""
        cypher = """
            MERGE (c:Collection {ead_id: $ead_id})
            ON CREATE SET c += $props, c.neo4j_created_at = datetime()
            ON MATCH  SET c += $props, c.neo4j_last_updated_at = datetime()
        """
        props = clean_props({k: v for k, v in data.items() if k != "ead_id"})
        with self._driver.session() as session:
            session.run(cypher, ead_id=data["ead_id"], props=props)

    # ------------------------------------------------------------------
    # Note
    # ------------------------------------------------------------------

    def merge_note(self, data: dict[str, Any]) -> None:
        """Upsert a Note node keyed on persistent_id."""
        cypher = """
            MERGE (n:Note {persistent_id: $persistent_id})
            SET n += $props
        """
        props = clean_props({k: v for k, v in data.items() if k != "persistent_id"})
        with self._driver.session() as session:
            session.run(cypher, persistent_id=data["persistent_id"], props=props)

    def link_note(self, parent_uri: str, note_persistent_id: str) -> None:
        """Create a HAS_NOTE relationship from a parent node to a Note."""
        label = label_from_uri(parent_uri)
        match_clause = f"(p:{label} {{uri: $parent_uri}})" if label else "(p {uri: $parent_uri})"
        cypher = f"""
            MATCH {match_clause}
            MATCH (n:Note {{persistent_id: $note_pid}})
            MERGE (p)-[:HAS_NOTE]->(n)
        """
        with self._driver.session() as session:
            session.run(cypher, parent_uri=parent_uri, note_pid=note_persistent_id)

    # ------------------------------------------------------------------
    # Extent  (delete-and-recreate — no natural key)
    # ------------------------------------------------------------------

    def replace_extents(self, parent_uri: str, extents: list[dict[str, Any]]) -> None:
        """Delete all existing Extent nodes for a parent, then recreate them."""
        label = label_from_uri(parent_uri)
        match_clause = f"(p:{label} {{uri: $uri}})" if label else "(p {{uri: $uri}})"
        with self._driver.session() as session:
            session.run(
                f"MATCH {match_clause}-[r:HAS_EXTENT]->(e:Extent) DELETE r, e",
                uri=parent_uri,
            )
            for e in extents:
                props = clean_props(e)
                session.run(
                    f"MATCH {match_clause} CREATE (p)-[:HAS_EXTENT]->(ex:Extent $props)",
                    uri=parent_uri,
                    props=props,
                )

    # ------------------------------------------------------------------
    # RevisionStatement  (delete-and-recreate — no natural key)
    # ------------------------------------------------------------------

    def replace_revision_statements(
        self, parent_uri: str, stmts: list[dict[str, Any]]
    ) -> None:
        """Delete all existing RevisionStatement nodes for a parent, then recreate."""
        label = label_from_uri(parent_uri)
        match_clause = f"(p:{label} {{uri: $uri}})" if label else "(p {{uri: $uri}})"
        with self._driver.session() as session:
            session.run(
                f"MATCH {match_clause}-[r:HAS_REVISION]->(rs:RevisionStatement) "
                "DELETE r, rs",
                uri=parent_uri,
            )
            for s in stmts:
                props = clean_props(s)
                session.run(
                    f"MATCH {match_clause} "
                    "CREATE (p)-[:HAS_REVISION]->(rs:RevisionStatement $props)",
                    uri=parent_uri,
                    props=props,
                )

    # ------------------------------------------------------------------
    # Deaccession  (delete-and-recreate — no natural key)
    # ------------------------------------------------------------------

    def replace_deaccessions(self, parent_uri: str, deaccessions: list[dict[str, Any]]) -> None:
        """Delete all existing Deaccession nodes for a parent, then recreate."""
        label = label_from_uri(parent_uri)
        match_clause = f"(p:{label} {{uri: $uri}})" if label else "(p {{uri: $uri}})"
        with self._driver.session() as session:
            session.run(
                f"MATCH {match_clause}-[r:HAS_DEACCESSION]->(d:Deaccession) DELETE r, d",
                uri=parent_uri,
            )
            for d in deaccessions:
                props = clean_props(d)
                session.run(
                    f"MATCH {match_clause} CREATE (p)-[:HAS_DEACCESSION]->(d:Deaccession $props)",
                    uri=parent_uri,
                    props=props,
                )

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------

    def merge_agent_stub(self, uri: str) -> None:
        """Create an Agent node with uri only if it does not already exist.

        Does NOT overwrite any existing properties — preserves data from
        a previous full merge_agent() call.
        """
        with self._driver.session() as session:
            session.run("MERGE (a:Agent {uri: $uri})", uri=uri)

    def merge_agent(self, data: dict[str, Any]) -> None:
        """Full upsert of an Agent node — sets all provided properties."""
        cypher = """
            MERGE (a:Agent {uri: $uri})
            SET a += $props
        """
        props = clean_props({k: v for k, v in data.items() if k != "uri"})
        with self._driver.session() as session:
            session.run(cypher, uri=data["uri"], props=props)

    def link_agent(self, parent_uri: str, agent_uri: str, role: str) -> None:
        """Create CREATED_BY or HAS_SUBJECT from a parent node to an Agent.

        role: 'creator' -> CREATED_BY, 'subject' -> HAS_SUBJECT
        """
        rel_type = _AGENT_ROLE_MAP.get(role, "RELATED_TO")
        label = label_from_uri(parent_uri)
        match_clause = f"(p:{label} {{uri: $parent_uri}})" if label else "(p {{uri: $parent_uri}})"
        cypher = f"""
            MATCH {match_clause}
            MATCH (a:Agent {{uri: $agent_uri}})
            MERGE (p)-[:{rel_type}]->(a)
        """
        with self._driver.session() as session:
            session.run(cypher, parent_uri=parent_uri, agent_uri=agent_uri)

    def replace_agent_names(self, agent_uri: str, names: list[dict[str, Any]]) -> None:
        """Delete all existing AgentName nodes for this agent, then recreate them."""
        with self._driver.session() as session:
            session.run(
                "MATCH (a:Agent {uri: $uri})-[r:HAS_NAME]->(n:AgentName) DELETE r, n",
                uri=agent_uri,
            )
            for name in names:
                props = clean_props(name)
                session.run(
                    "MATCH (a:Agent {uri: $uri}) CREATE (a)-[:HAS_NAME]->(n:AgentName $props)",
                    uri=agent_uri,
                    props=props,
                )

    def replace_agent_identifiers(self, agent_uri: str, identifiers: list[dict[str, Any]]) -> None:
        """Delete all existing AgentIdentifier nodes for this agent, then recreate them."""
        with self._driver.session() as session:
            session.run(
                "MATCH (a:Agent {uri: $uri})-[r:HAS_IDENTIFIER]->(n:AgentIdentifier) DELETE r, n",
                uri=agent_uri,
            )
            for ident in identifiers:
                props = clean_props(ident)
                session.run(
                    "MATCH (a:Agent {uri: $uri}) CREATE (a)-[:HAS_IDENTIFIER]->(n:AgentIdentifier $props)",
                    uri=agent_uri,
                    props=props,
                )

    def replace_agent_maintenance_histories(self, agent_uri: str, histories: list[dict[str, Any]]) -> None:
        """Delete all existing AgentMaintenanceHistory nodes for this agent, then recreate them."""
        with self._driver.session() as session:
            session.run(
                "MATCH (a:Agent {uri: $uri})-[r:HAS_MAINTENANCE_HISTORY]->(n:AgentMaintenanceHistory) DELETE r, n",
                uri=agent_uri,
            )
            for h in histories:
                props = clean_props(h)
                session.run(
                    "MATCH (a:Agent {uri: $uri}) CREATE (a)-[:HAS_MAINTENANCE_HISTORY]->(n:AgentMaintenanceHistory $props)",
                    uri=agent_uri,
                    props=props,
                )

    def link_agents(self, source_uri: str, target_uri: str, relator: str) -> None:
        """Create a directional inter-agent relationship.

        The relator string is sanitized and used as the relationship type,
        e.g. 'is_member_of' -> IS_MEMBER_OF.
        """
        rel_type = sanitize_rel_type(relator)
        cypher = f"""
            MATCH (a:Agent {{uri: $source_uri}})
            MATCH (b:Agent {{uri: $target_uri}})
            MERGE (a)-[:{rel_type}]->(b)
        """
        with self._driver.session() as session:
            session.run(cypher, source_uri=source_uri, target_uri=target_uri)

    # ------------------------------------------------------------------
    # Accession
    # ------------------------------------------------------------------

    def merge_accession_stub(self, uri: str) -> None:
        """Create an Accession stub node if it does not already exist."""
        with self._driver.session() as session:
            session.run("MERGE (acc:Accession {uri: $uri})", uri=uri)

    def merge_accession(self, data: dict[str, Any]) -> None:
        """Full upsert of an Accession node — sets all provided properties."""
        cypher = """
            MERGE (acc:Accession {uri: $uri})
            SET acc += $props
        """
        props = clean_props({k: v for k, v in data.items() if k != "uri"})
        with self._driver.session() as session:
            session.run(cypher, uri=data["uri"], props=props)

    def link_accession_component(self, accession_uri: str, ao_uri: str) -> None:
        """Create a HAS_COMPONENT edge from an Accession to an ArchivalObject."""
        cypher = """
            MATCH (acc:Accession {uri: $accession_uri})
            MATCH (ao:ArchivalObject {uri: $ao_uri})
            MERGE (acc)-[:HAS_COMPONENT]->(ao)
        """
        with self._driver.session() as session:
            session.run(cypher, accession_uri=accession_uri, ao_uri=ao_uri)

    def link_accession_parts(self, from_uri: str, to_uri: str, relator: str) -> None:
        """Create a directional part relationship between two Accession nodes.

        relator is sanitized to a Neo4j relationship type:
          'has_part'      -> HAS_PART
          'forms_part_of' -> FORMS_PART_OF
        """
        rel_type = sanitize_rel_type(relator)
        cypher = f"""
            MATCH (a:Accession {{uri: $from_uri}})
            MATCH (b:Accession {{uri: $to_uri}})
            MERGE (a)-[:{rel_type}]->(b)
        """
        with self._driver.session() as session:
            session.run(cypher, from_uri=from_uri, to_uri=to_uri)

    def link_accession(self, collection_uri: str, accession_uri: str) -> None:
        """Create a RELATED_TO edge from a Collection to an Accession."""
        cypher = """
            MATCH (c:Collection {uri: $collection_uri})
            MATCH (acc:Accession {uri: $accession_uri})
            MERGE (c)-[:RELATED_TO]->(acc)
        """
        with self._driver.session() as session:
            session.run(
                cypher, collection_uri=collection_uri, accession_uri=accession_uri
            )

    # ------------------------------------------------------------------
    # DigitalObject
    # ------------------------------------------------------------------

    def merge_digital_object_stub(self, uri: str) -> None:
        """Create a DigitalObject node with uri only if it does not already exist."""
        with self._driver.session() as session:
            session.run("MERGE (d:DigitalObject {uri: $uri})", uri=uri)

    def merge_digital_object(self, data: dict[str, Any]) -> None:
        """Full upsert of a DigitalObject node — sets all provided properties."""
        cypher = """
            MERGE (d:DigitalObject {uri: $uri})
            SET d += $props
        """
        props = clean_props({k: v for k, v in data.items() if k != "uri"})
        with self._driver.session() as session:
            session.run(cypher, uri=data["uri"], props=props)

    def link_digital_object(self, ao_uri: str, do_uri: str) -> None:
        """Create a HAS_DIGITAL_OBJECT edge from an ArchivalObject to a DigitalObject."""
        cypher = """
            MATCH (ao:ArchivalObject {uri: $ao_uri})
            MATCH (d:DigitalObject {uri: $do_uri})
            MERGE (ao)-[:HAS_DIGITAL_OBJECT]->(d)
        """
        with self._driver.session() as session:
            session.run(cypher, ao_uri=ao_uri, do_uri=do_uri)

    def link_digital_object_accession(self, do_uri: str, accession_uri: str) -> None:
        """Create a RELATED_TO edge from a DigitalObject to an Accession."""
        cypher = """
            MATCH (d:DigitalObject {uri: $do_uri})
            MATCH (acc:Accession {uri: $accession_uri})
            MERGE (d)-[:RELATED_TO]->(acc)
        """
        with self._driver.session() as session:
            session.run(cypher, do_uri=do_uri, accession_uri=accession_uri)

    def merge_file_version(self, do_uri: str, data: dict[str, Any]) -> None:
        """Upsert a FileVersion node keyed on file_uri and link it to its DigitalObject."""
        file_uri = data.get("file_uri", "")
        if not file_uri:
            return
        cypher = """
            MERGE (fv:FileVersion {file_uri: $file_uri})
            SET fv += $props
            WITH fv
            MATCH (d:DigitalObject {uri: $do_uri})
            MERGE (d)-[:HAS_FILE_VERSION]->(fv)
        """
        props = clean_props({k: v for k, v in data.items() if k != "file_uri"})
        with self._driver.session() as session:
            session.run(cypher, file_uri=file_uri, do_uri=do_uri, props=props)

    # ------------------------------------------------------------------
    # ArchivalObject
    # ------------------------------------------------------------------

    def merge_archival_object(self, data: dict[str, Any]) -> None:
        """Upsert an ArchivalObject node keyed on uri."""
        cypher = """
            MERGE (ao:ArchivalObject {uri: $uri})
            ON CREATE SET ao += $props, ao.neo4j_created_at = datetime()
            ON MATCH  SET ao += $props, ao.neo4j_last_updated_at = datetime()
        """
        props = clean_props({k: v for k, v in data.items() if k != "uri"})
        with self._driver.session() as session:
            session.run(cypher, uri=data["uri"], props=props)

    def link_archival_object(self, parent_uri: str, child_uri: str) -> None:
        """Create a HAS_PART edge from a parent (Collection or ArchivalObject) to a child."""
        label = label_from_uri(parent_uri)
        match_clause = f"(p:{label} {{uri: $parent_uri}})" if label else "(p {{uri: $parent_uri}})"
        cypher = f"""
            MATCH {match_clause}
            MATCH (child:ArchivalObject {{uri: $child_uri}})
            MERGE (p)-[:HAS_PART]->(child)
        """
        with self._driver.session() as session:
            session.run(cypher, parent_uri=parent_uri, child_uri=child_uri)

    # ------------------------------------------------------------------
    # Schema metadata nodes
    # ------------------------------------------------------------------

    def ensure_schema_constraints(self) -> None:
        """Create uniqueness constraints for schema metadata nodes."""
        statements = [
            "CREATE CONSTRAINT node_schema_label IF NOT EXISTS "
            "FOR (n:NodeSchema) REQUIRE n.label IS UNIQUE",
            "CREATE CONSTRAINT rel_schema_type IF NOT EXISTS "
            "FOR (r:RelationshipSchema) REQUIRE r.type IS UNIQUE",
            "CREATE CONSTRAINT prop_schema_id IF NOT EXISTS "
            "FOR (p:PropertySchema) REQUIRE p.id IS UNIQUE",
        ]
        with self._driver.session() as session:
            for stmt in statements:
                session.run(stmt)

    def merge_node_schema(self, data: dict[str, Any]) -> None:
        """Upsert a NodeSchema node keyed on label."""
        cypher = """
            MERGE (n:NodeSchema {label: $label})
            SET n += $props
        """
        props = clean_props({k: v for k, v in data.items() if k != "label"})
        with self._driver.session() as session:
            session.run(cypher, label=data["label"], props=props)

    def merge_relationship_schema(self, data: dict[str, Any]) -> None:
        """Upsert a RelationshipSchema node keyed on type."""
        cypher = """
            MERGE (r:RelationshipSchema {type: $type})
            SET r += $props
        """
        props = clean_props({k: v for k, v in data.items() if k != "type"})
        with self._driver.session() as session:
            session.run(cypher, type=data["type"], props=props)

    def merge_property_schema(self, data: dict[str, Any]) -> None:
        """Upsert a PropertySchema node keyed on id (= 'NodeLabel.property_name')."""
        cypher = """
            MERGE (p:PropertySchema {id: $id})
            SET p += $props
        """
        props = clean_props({k: v for k, v in data.items() if k != "id"})
        with self._driver.session() as session:
            session.run(cypher, id=data["id"], props=props)

    # ------------------------------------------------------------------
    # Query helpers (used by extractors to read from Neo4j)
    # ------------------------------------------------------------------

    def get_collection_uris(self) -> list[str]:
        """Return the uri property of every Collection node in the graph."""
        cypher = "MATCH (c:Collection) WHERE c.uri IS NOT NULL RETURN c.uri AS uri"
        with self._driver.session() as session:
            result = session.run(cypher)
            return [record["uri"] for record in result]

    def get_agent_stub_uris(self) -> list[str]:
        """Return URIs of Agent nodes that have no display_name (unfilled stubs).

        These are agents referenced only via inter-agent relationships — they
        were created as stubs but have never been fully fetched.
        Excludes stubs labelled :AgentFetchFailed (deleted/missing in ASpace).
        """
        cypher = """
            MATCH (a:Agent)
            WHERE a.display_name IS NULL AND NOT a:AgentFetchFailed
            RETURN a.uri AS uri
        """
        with self._driver.session() as session:
            result = session.run(cypher)
            return [record["uri"] for record in result]

    def mark_agent_fetch_failed(self, uri: str) -> None:
        """Label an Agent stub as unresolvable so it is skipped in future BFS passes.

        Called when ArchivesSpace returns an error for a stub URI — typically
        because the agent was deleted but references to it still exist in Neo4j.
        Uses a label rather than a property to avoid Neo4j unknown-property warnings
        when no fetch failures have occurred.
        """
        cypher = """
            MATCH (a:Agent {uri: $uri})
            SET a:AgentFetchFailed
        """
        with self._driver.session() as session:
            session.run(cypher, uri=uri)

    def get_linked_agent_uris(self) -> list[str]:
        """Return URIs of all Agent nodes linked to a Collection or ArchivalObject.

        These represent the agents scoped to the extracted repository — excludes
        any system-wide agents that are not associated with local resources.
        """
        cypher = """
            MATCH (a:Agent)<-[:CREATED_BY|HAS_SUBJECT|SOURCE]-()
            RETURN DISTINCT a.uri AS uri
        """
        with self._driver.session() as session:
            result = session.run(cypher)
            return [record["uri"] for record in result]
