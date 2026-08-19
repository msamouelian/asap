"""Extractor: writes schema metadata into Neo4j as a queryable meta-graph.

These nodes describe the purpose and structure of every node type, relationship
type, and key property in the graph — in the context of ArchivesSpace.  An AI
agent can query this metadata to understand the schema before formulating data
queries.
"""

import logging
from typing import Any

from asapextractor.extractors.base import BaseExtractor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node schema definitions
# ---------------------------------------------------------------------------

NODE_SCHEMAS: list[dict[str, Any]] = [
    {
        "label": "Collection",
        "description": (
            "An ArchivesSpace resource record representing the top-level unit of a finding aid. "
            "Each Collection corresponds to a distinct archival collection (manuscript collection, "
            "record group, etc.) held by the repository. Collections are the primary access point "
            "for researchers and carry the full finding aid metadata including dates, extents, "
            "scope notes, and biographical/historical context. TERMINOLOGY: users refer to these "
            "records interchangeably as 'collections', 'resources', 'resource records', 'finding "
            "aids', or collectively as 'holdings' — all of these mean Collection nodes, never "
            "Accession or DigitalObject nodes."
        ),
        "aspace_jsonmodel": "resource",
        "aspace_endpoint": "repositories/{repo_id}/resources",
        "unique_key": "ead_id",
        "neo4j_label": "Collection",
    },
    {
        "label": "Note",
        "description": (
            "A descriptive or administrative note attached to a Collection, ArchivalObject, or Agent. "
            "Notes encode rich textual information following DACS (Describing Archives: A Content "
            "Standard) conventions. Common note types include: abstract (brief summary), bioghist "
            "(biographical or historical note), scopecontent (scope and content description), "
            "accessrestrict (access restrictions), acqinfo (provenance/acquisition), userestrict "
            "(use restrictions), processinfo (processing information), and relatedmaterial. "
            "Notes with narrative content (types abstract, scopecontent, physdesc, odd, physfacet, "
            "acqinfo, bioghist, note_bioghist, relatedmaterial, separatedmaterial, custodhist, "
            "otherfindaid, originalsloc) additionally carry the :IndexableNote label and are covered "
            "by the free_text_index full-text index on their content and label properties. Abstract "
            "notes are curated summaries and often the only place facts like correspondent lists "
            "are enumerated."
        ),
        "aspace_jsonmodel": "note_singlepart / note_multipart",
        "aspace_endpoint": "embedded in parent record",
        "unique_key": "persistent_id",
        "neo4j_label": "Note",
    },
    {
        "label": "NoteChunk",
        "description": (
            "A retrieval-sized slice (~1,000 characters) of an IndexableNote's content, created "
            "by the embedding phase for semantic search. Every IndexableNote has at least one "
            "chunk; long notes have several, in seq order, with overlapping boundaries. Each "
            "chunk carries a 384-dimensional embedding covered by the note_chunk_embedding "
            "vector index. Semantic searches over note content should target NoteChunk, then "
            "traverse (parent)-[:HAS_NOTE]->(:Note)-[:HAS_CHUNK]->(chunk) back to the parent "
            "record. Derived data: rebuilt on every extraction run, not sourced from ArchivesSpace."
        ),
        "aspace_jsonmodel": "none (derived)",
        "aspace_endpoint": "none (derived from Note content)",
        "unique_key": "chunk_id",
        "neo4j_label": "NoteChunk",
    },
    {
        "label": "DocumentCollection",
        "description": (
            "A user-uploaded library of supporting documents (standards, guidelines, research "
            "material) used for retrieval-augmented answers. NOT extracted from ArchivesSpace: "
            "uploaded through the ASAP UI and preserved across extraction runs. visibility is "
            "'private' (only the uploader) or 'shared' (all users); archived collections are "
            "hidden from selection but retained for conversation history. Contains Document "
            "nodes via CONTAINS."
        ),
        "aspace_jsonmodel": "none (user-uploaded)",
        "aspace_endpoint": "none (user-uploaded)",
        "unique_key": "id",
        "neo4j_label": "DocumentCollection",
    },
    {
        "label": "Document",
        "description": (
            "A single document (PDF, Word, Markdown, text) inside a DocumentCollection. Carries "
            "file metadata only — the document text lives in its DocumentChunk nodes via "
            "HAS_CHUNK. NOT extracted from ArchivesSpace; preserved across extraction runs."
        ),
        "aspace_jsonmodel": "none (user-uploaded)",
        "aspace_endpoint": "none (user-uploaded)",
        "unique_key": "id",
        "neo4j_label": "Document",
    },
    {
        "label": "DocumentChunk",
        "description": (
            "A retrieval-sized slice of an uploaded Document's text, with a semantic embedding "
            "covered by the document_chunk_embedding vector index and full-text coverage via "
            "document_chunk_fulltext. Used for retrieval-augmented answers; chunks retrieved "
            "for a conversation turn are referenced from Postgres for rehydration. NOT "
            "extracted from ArchivesSpace; preserved across extraction runs."
        ),
        "aspace_jsonmodel": "none (user-uploaded)",
        "aspace_endpoint": "none (derived from Document content)",
        "unique_key": "id",
        "neo4j_label": "DocumentChunk",
    },
    {
        "label": "Extent",
        "description": (
            "A quantitative measurement of the physical or digital volume of materials associated "
            "with a Collection. Typically expressed in linear feet for physical archival collections. "
            "A collection may have multiple Extent nodes (e.g. one for the primary series plus a "
            "separate entry for unprocessed additions). The container_summary field provides a "
            "human-readable breakdown such as '9 volumes, 1231 boxes, 74 oversize folders'."
        ),
        "aspace_jsonmodel": "extent",
        "aspace_endpoint": "embedded in parent record",
        "unique_key": "none (identified by HAS_EXTENT relationship)",
        "neo4j_label": "Extent",
    },
    {
        "label": "RevisionStatement",
        "description": (
            "An administrative record documenting a significant change made to a finding aid. "
            "Records the date and nature of revisions, providing an audit trail for collection "
            "description updates. Useful for tracking when and how finding aids have been modified, "
            "including automated preprocessing and migration events."
        ),
        "aspace_jsonmodel": "revision_statement",
        "aspace_endpoint": "embedded in parent record",
        "unique_key": "none (identified by HAS_REVISION relationship)",
        "neo4j_label": "RevisionStatement",
    },
    {
        "label": "Agent",
        "description": (
            "A person, family, or corporate body with a relationship to one or more archival "
            "collections or other agents. Agents may be creators of records (role: creator), "
            "subjects of collections (role: subject), or related to other agents via inter-agent "
            "relationships. Agent records are shared across collections — the same agent can be "
            "linked to many collections. The agent_type property distinguishes between 'person', "
            "'corporate_entity', and 'family'. All agents in the ArchivesSpace system are extracted, "
            "including those not linked to any collection (orphaned agents)."
        ),
        "aspace_jsonmodel": "agent_person / agent_corporate_entity / agent_family",
        "aspace_endpoint": "agents/people, agents/corporate_entities, agents/families",
        "unique_key": "uri",
        "neo4j_label": "Agent",
    },
    {
        "label": "DigitalObject",
        "description": (
            "An ArchivesSpace digital object record representing a digitized or born-digital "
            "item linked to one or more archival components. Digital objects carry metadata "
            "such as title, publish status, and access restrictions, and point to one or more "
            "FileVersion nodes that hold the actual file URIs. A DigitalObject is linked to "
            "its archival component via the HAS_DIGITAL_OBJECT relationship on the ArchivalObject."
        ),
        "aspace_jsonmodel": "digital_object",
        "aspace_endpoint": "repositories/{repo_id}/digital_objects",
        "unique_key": "uri",
        "neo4j_label": "DigitalObject",
    },
    {
        "label": "FileVersion",
        "description": (
            "A single file associated with a DigitalObject — typically a URL pointing to a "
            "digitized image, PDF, or other online resource. A DigitalObject may have multiple "
            "FileVersions (e.g. a thumbnail and a full-resolution image). The is_representative "
            "flag identifies the primary file to display. FileVersions are linked to their parent "
            "DigitalObject via the HAS_FILE_VERSION relationship."
        ),
        "aspace_jsonmodel": "file_version",
        "aspace_endpoint": "embedded in digital_object record",
        "unique_key": "file_uri",
        "neo4j_label": "FileVersion",
    },
    {
        "label": "AgentName",
        "description": (
            "An authorized or variant name form associated with an Agent. Each Agent may have "
            "multiple AgentName nodes — one authorized (is_display_name = true) and zero or more "
            "variant or alternate forms. Name fields vary by agent type: persons have primary_name "
            "and rest_of_name; corporate entities have primary_name; families have family_name. "
            "Use the authorized name for display and all names for search/discovery."
        ),
        "aspace_jsonmodel": "name_person / name_corporate_entity / name_family",
        "aspace_endpoint": "embedded in agent record (names array)",
        "unique_key": "none — no global key; nodes are deleted and recreated on each pipeline run",
        "neo4j_label": "AgentName",
    },
    {
        "label": "AgentIdentifier",
        "description": (
            "An authority file identifier linking an Agent to an external name authority system "
            "such as the Library of Congress Name Authority File (LCNAF/NAF). A single agent may "
            "have multiple identifiers from different sources. The primary identifier "
            "(primary_identifier = true) is the canonical external reference."
        ),
        "aspace_jsonmodel": "agent_record_identifier",
        "aspace_endpoint": "embedded in agent record (agent_record_identifiers array)",
        "unique_key": "none — nodes are deleted and recreated on each pipeline run",
        "neo4j_label": "AgentIdentifier",
    },
    {
        "label": "AgentMaintenanceHistory",
        "description": (
            "An audit trail entry documenting a maintenance event performed on an Agent record. "
            "Captures who made the change (agent field), when (event_date), what type of event "
            "occurred (maintenance_event_type: created, modified, etc.), and an optional "
            "descriptive note. Useful for finding agents created or modified by specific tools "
            "such as the LCNAF Import plugin."
        ),
        "aspace_jsonmodel": "agent_maintenance_history",
        "aspace_endpoint": "embedded in agent record (agent_maintenance_histories array)",
        "unique_key": "none — nodes are deleted and recreated on each pipeline run",
        "neo4j_label": "AgentMaintenanceHistory",
    },
    {
        "label": "Deaccession",
        "description": (
            "A record documenting the removal of materials from an archival collection. "
            "Captures the description of removed items, the reason for removal, the disposition "
            "method, and the date of the deaccession event. A collection may have multiple "
            "Deaccession nodes if materials were removed in separate events."
        ),
        "aspace_jsonmodel": "deaccession",
        "aspace_endpoint": "embedded in parent resource record",
        "unique_key": "none — nodes are deleted and recreated on each pipeline run",
        "neo4j_label": "Deaccession",
    },
    {
        "label": "Accession",
        "description": (
            "An ArchivesSpace accession record documenting a discrete transfer of materials to "
            "the repository. Accession records capture acquisition metadata including title, "
            "acquisition type, provenance, content description, inventory, access and use "
            "restrictions, and accession date. An accession may be linked to one or more "
            "Collection (resource) records via RELATED_TO, to other Accession records via "
            "HAS_PART or FORMS_PART_OF relationships, to the specific ArchivalObject components "
            "that describe its materials via HAS_COMPONENT, and to Agents via CREATED_BY, "
            "HAS_SUBJECT, or SOURCE relationships."
        ),
        "aspace_jsonmodel": "accession",
        "aspace_endpoint": "repositories/{repo_id}/accessions",
        "unique_key": "uri",
        "neo4j_label": "Accession",
    },
    {
        "label": "ArchivalObject",
        "description": (
            "A component of an archival collection below the resource level. ArchivalObjects "
            "represent all hierarchical levels within a finding aid — series, sub-series, file, "
            "and item. They encode the intellectual arrangement of a collection and may be nested "
            "to arbitrary depth. Each ArchivalObject is linked to its parent (a Collection or "
            "another ArchivalObject) via a HAS_PART relationship. Use (HAS_PART*) in Cypher to "
            "traverse the full hierarchy."
        ),
        "aspace_jsonmodel": "archival_object",
        "aspace_endpoint": "repositories/{repo_id}/archival_objects/{id}",
        "unique_key": "uri",
        "neo4j_label": "ArchivalObject",
    },
]


# ---------------------------------------------------------------------------
# Relationship schema definitions
# ---------------------------------------------------------------------------

RELATIONSHIP_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "HAS_NOTE",
        "from_labels": "Collection, ArchivalObject, Agent, DigitalObject",
        "to_labels": "Note",
        "description": (
            "Links a Collection, ArchivalObject, Agent, or DigitalObject to one of its "
            "descriptive or administrative notes. A single parent may have many notes of "
            "different types. Note types follow DACS conventions (abstract, bioghist, "
            "scopecontent, etc.)."
        ),
    },
    {
        "type": "HAS_CHUNK",
        "from_labels": "Note, Document",
        "to_labels": "NoteChunk, DocumentChunk",
        "description": (
            "Links a text-bearing node to its retrieval-sized chunks used for semantic search: "
            "IndexableNote to NoteChunk (archival note content) and Document to DocumentChunk "
            "(uploaded document content). Chunks are ordered by their seq property. For note "
            "chunks, traverse (parent)-[:HAS_NOTE]->(note)-[:HAS_CHUNK]->(chunk) to find the "
            "owning record; for document chunks, traverse "
            "(dc:DocumentCollection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(chunk)."
        ),
    },
    {
        "type": "CONTAINS",
        "from_labels": "DocumentCollection",
        "to_labels": "Document",
        "description": (
            "Links a user-uploaded DocumentCollection to the Documents inside it. A collection "
            "contains one or more documents."
        ),
    },
    {
        "type": "HAS_EXTENT",
        "from_labels": "Collection, DigitalObject, Accession",
        "to_labels": "Extent",
        "description": (
            "Links a Collection, DigitalObject, or Accession to its physical or digital extent "
            "measurement. A collection may have multiple Extent nodes, one for each distinct "
            "extent entry recorded in ArchivesSpace (e.g. processed materials plus a separate "
            "unprocessed addition)."
        ),
    },
    {
        "type": "HAS_REVISION",
        "from_labels": "Collection",
        "to_labels": "RevisionStatement",
        "description": (
            "Links a Collection to an administrative record of a finding aid revision. Provides "
            "an audit trail of changes to the collection description over time."
        ),
    },
    {
        "type": "CREATED_BY",
        "from_labels": "Collection, ArchivalObject, DigitalObject, Accession",
        "to_labels": "Agent",
        "description": (
            "Links a Collection, ArchivalObject, DigitalObject, or Accession to an Agent who "
            "created the records in that collection or component. "
            "Direction: (Collection)-[:CREATED_BY]->(Agent) — the arrow points FROM the "
            "source record TO the Agent, never the reverse. "
            "Corresponds to the 'creator' role in the ArchivesSpace linked_agents array. "
            "A collection may have multiple creators. This is the ARCHIVAL creator — the "
            "person/organization whose materials these are. It is NOT the staff member who "
            "entered the record (creating_user property) nor who processed the collection "
            "(processed_by property on Collection)."
        ),
    },
    {
        "type": "HAS_SUBJECT",
        "from_labels": "Collection, ArchivalObject, DigitalObject, Accession",
        "to_labels": "Agent",
        "description": (
            "Links a Collection, ArchivalObject, DigitalObject, or Accession to an Agent who "
            "is a subject of (i.e., documented within) that record. Direction: "
            "(Collection)-[:HAS_SUBJECT]->(Agent) — the arrow points FROM the source record "
            "TO the Agent, never the reverse. Corresponds to the 'subject' role in the "
            "ArchivesSpace linked_agents array."
        ),
    },
    {
        "type": "SOURCE",
        "from_labels": "Collection, ArchivalObject, DigitalObject, Accession",
        "to_labels": "Agent",
        "description": (
            "Links a Collection, ArchivalObject, DigitalObject, or Accession to an Agent who "
            "is the source of the records (e.g., a donor or transferring organization). "
            "Direction: (Collection)-[:SOURCE]->(Agent) — the arrow points FROM the source "
            "record TO the Agent, never the reverse. Corresponds to the 'source' role in "
            "the ArchivesSpace linked_agents array."
        ),
    },
    {
        "type": "HAS_DEACCESSION",
        "from_labels": "Collection, Accession",
        "to_labels": "Deaccession",
        "description": (
            "Links a Collection or Accession to a record of materials removed from it. "
            "A parent node may have multiple HAS_DEACCESSION edges if removals occurred "
            "at different times or involved different materials."
        ),
    },
    {
        "type": "RELATED_TO",
        "from_labels": "Collection, DigitalObject",
        "to_labels": "Accession",
        "description": (
            "Links a Collection or DigitalObject to a related Accession record. Indicates that "
            "the materials were acquired (at least in part) through that accession event."
        ),
    },
    {
        "type": "HAS_PART",
        "from_labels": "Collection, ArchivalObject, Accession",
        "to_labels": "ArchivalObject, Accession",
        "description": (
            "Links a parent node to a child node. For Collection/ArchivalObject sources, encodes "
            "the intellectual hierarchy of a finding aid (series, sub-series, file, item) — "
            "traversable to arbitrary depth using Cypher path expressions (HAS_PART*). "
            "For Accession sources, derived from the 'has_part' relator in the ArchivesSpace "
            "related_accessions array — indicates that the target Accession is a component part "
            "of the source Accession."
        ),
    },
    {
        "type": "FORMS_PART_OF",
        "from_labels": "Accession",
        "to_labels": "Accession",
        "description": (
            "Links an Accession to the larger Accession of which it forms a part. Derived from "
            "the 'forms_part_of' relator in the ArchivesSpace related_accessions array. "
            "The inverse of HAS_PART between Accession nodes."
        ),
    },
    {
        "type": "HAS_COMPONENT",
        "from_labels": "Accession",
        "to_labels": "ArchivalObject",
        "description": (
            "Links an Accession to the ArchivalObject component(s) in a finding aid that "
            "describe its materials. Derived from the component_links field on the accession "
            "record. Use this relationship to navigate from an accession to the specific series, "
            "file, or item level components where the accessioned materials are described."
        ),
    },
    {
        "type": "HAS_DIGITAL_OBJECT",
        "from_labels": "ArchivalObject",
        "to_labels": "DigitalObject",
        "description": (
            "Links an ArchivalObject to a DigitalObject that is a digital representation of "
            "that archival component. Derived from the 'instances' field on the archival object "
            "record where instance_type is 'digital_object'. An archival object may have more "
            "than one digital object."
        ),
    },
    {
        "type": "HAS_FILE_VERSION",
        "from_labels": "DigitalObject",
        "to_labels": "FileVersion",
        "description": (
            "Links a DigitalObject to one of its file versions. A digital object may have "
            "multiple file versions (e.g. different resolutions or formats). Use the "
            "is_representative property on FileVersion to identify the primary display file."
        ),
    },
    {
        "type": "HAS_NAME",
        "from_labels": "Agent",
        "to_labels": "AgentName",
        "description": (
            "Links an Agent to one of its name forms. An agent may have multiple HAS_NAME "
            "edges — one to its authorized display name and others to variant or alternate forms. "
            "Filter on AgentName.is_display_name = true to retrieve the authorized form only."
        ),
    },
    {
        "type": "HAS_IDENTIFIER",
        "from_labels": "Agent",
        "to_labels": "AgentIdentifier",
        "description": (
            "Links an Agent to an authority file identifier (e.g. a Library of Congress NAF ID). "
            "An agent may have multiple identifiers from different authority sources. "
            "Filter on AgentIdentifier.primary_identifier = true for the canonical identifier."
        ),
    },
    {
        "type": "HAS_MAINTENANCE_HISTORY",
        "from_labels": "Agent",
        "to_labels": "AgentMaintenanceHistory",
        "description": (
            "Links an Agent to an audit trail entry recording a maintenance event on that record. "
            "Traverse this relationship to find when an agent was created, by what tool or person, "
            "and what changes were made over time."
        ),
    },
    {
        "type": "AGENT_INTER_RELATIONSHIP",
        "from_labels": "Agent",
        "to_labels": "Agent",
        "description": (
            "A family of directional relationships between Agent nodes derived from the "
            "ArchivesSpace related_agents array. The specific relationship type is derived from "
            "the ArchivesSpace 'relator' field and stored as the Neo4j relationship type "
            "(e.g. IS_MEMBER_OF, IS_EARLIER_FORM_OF, ASSOCIATIVE). These relationships express "
            "historical or organizational connections between agents, such as a person being a "
            "member of a corporate body or a predecessor organization."
        ),
    },
]


# ---------------------------------------------------------------------------
# Property schema definitions (key properties only)
# ---------------------------------------------------------------------------

PROPERTY_SCHEMAS: list[dict[str, Any]] = [
    # Collection
    {
        "id": "Collection.ead_id",
        "node_label": "Collection",
        "property": "ead_id",
        "description": "Encoded Archival Description identifier — the unique key for this collection in ArchivesSpace and the graph.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Collection.uri",
        "node_label": "Collection",
        "property": "uri",
        "description": "ArchivesSpace REST API URI for this resource (e.g. /repositories/11/resources/8400). Used as the linking key for relationship queries.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Collection.title",
        "node_label": "Collection",
        "property": "title",
        "description": "Full title of the collection as it appears in the finding aid.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Collection.level",
        "node_label": "Collection",
        "property": "level",
        "description": "Hierarchical level — always 'collection' for resource records.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.publish",
        "node_label": "Collection",
        "property": "publish",
        "description": "Whether this collection is publicly visible in the ArchivesSpace public interface.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "Collection.date_inclusive_begin",
        "node_label": "Collection",
        "property": "date_inclusive_begin",
        "description": "Start year of the inclusive date range of the collection materials (e.g. '1905').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.date_inclusive_end",
        "node_label": "Collection",
        "property": "date_inclusive_end",
        "description": "End year of the inclusive date range of the collection materials (e.g. '2000').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.date_bulk_begin",
        "node_label": "Collection",
        "property": "date_bulk_begin",
        "description": "Start year of the bulk date range — the period most heavily represented in the collection.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.date_bulk_end",
        "node_label": "Collection",
        "property": "date_bulk_end",
        "description": "End year of the bulk date range.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.date_inclusive_expression",
        "node_label": "Collection",
        "property": "date_inclusive_expression",
        "description": "Human-readable inclusive date expression as recorded in ArchivesSpace (e.g. '1905-2000'). Use for display; prefer begin/end fields for range filtering.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.date_bulk_expression",
        "node_label": "Collection",
        "property": "date_bulk_expression",
        "description": "Human-readable bulk date expression as recorded in ArchivesSpace (e.g. '1940-1980'). Use for display; prefer begin/end fields for range filtering.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.languages",
        "node_label": "Collection",
        "property": "languages",
        "description": "List of ISO 639-2 language codes for the languages of the collection materials (e.g. ['eng', 'fre']).",
        "data_type": "List<String>",
        "required": False,
    },
    {
        "id": "Collection.collection_type_prefix",
        "node_label": "Collection",
        "property": "collection_type_prefix",
        "description": (
            "The collection type prefix derived from the id_0 field (the call number), "
            "NORMALIZED to canonical casing regardless of how id_0 is typed. The legal "
            "values are EXACTLY these case-sensitive strings: 'Mss' (manuscript "
            "collections), 'Arch' (HBS archives), 'Kress', 'Vis', 'Archive-It', 'Other'. "
            "Always match with = against one of these exact values — never uppercase, "
            "lowercase, or otherwise restyle them (e.g. use 'Arch', NOT 'ARCH'). "
            "id_0 itself keeps its original, possibly inconsistent, formatting."
        ),
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.industry_top_level_code",
        "node_label": "Collection",
        "property": "industry_top_level_code",
        "description": (
            "Single-character top-level industry code derived from the MSS leaf code "
            "(e.g. '9' for leaf code '912'). "
            "'Not Applicable' for non-MSS collections. 'Not Found' if the code cannot be derived."
        ),
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.industry_second_level_code",
        "node_label": "Collection",
        "property": "industry_second_level_code",
        "description": (
            "Two-character second-level industry code derived from the MSS leaf code "
            "(e.g. '91' for leaf code '912'). 'None' for 1-character leaf codes. "
            "'Not Applicable' for non-MSS collections."
        ),
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.industry_leaf_code",
        "node_label": "Collection",
        "property": "industry_leaf_code",
        "description": (
            "The full industry classification code extracted from id_0 for MSS: prefixed "
            "collections — the most specific level in the hierarchy "
            "(e.g. '912', '131A2'). 'Not Applicable' for all non-MSS collections."
        ),
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.industry_top_level_label",
        "node_label": "Collection",
        "property": "industry_top_level_label",
        "description": (
            "Industry classification label at the top level, corresponding to the "
            "single-character top-level code (e.g. 'Government services' for code '9'). "
            "'Not Applicable' for non-MSS collections. 'Not Found' if absent from the table."
        ),
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.industry_second_level_label",
        "node_label": "Collection",
        "property": "industry_second_level_label",
        "description": (
            "Industry classification label at the second level, corresponding to the "
            "two-character second-level code (e.g. 'Public welfare institutions' for code '93'). "
            "'None' when the leaf code has only one character. "
            "'Not Applicable' for non-MSS collections."
        ),
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.industry_leaf_label",
        "node_label": "Collection",
        "property": "industry_leaf_label",
        "description": (
            "Industry classification label at the leaf (most specific) level, corresponding "
            "to the full industry_leaf_code (e.g. 'Education' for code '931'). "
            "'Not Applicable' for non-MSS collections. 'Not Found' if absent from the table."
        ),
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.industry_path",
        "node_label": "Collection",
        "property": "industry_path",
        "description": (
            "Full hierarchical industry classification path built by concatenating labels "
            "from top to leaf with hyphens. "
            "Format: 1-char code → '{leaf_label}'; "
            "2-char code → '{top_label}-{leaf_label}'; "
            "3+ char code → '{top_label}-{second_label}-{leaf_label}' "
            "('second_label' is 'None' if the 2-char prefix is absent from the table). "
            "Example: code '912' → 'Government services-Executive and administrative units (...)-Military and naval (including privateering)'. "
            "'Not Applicable' for non-MSS collections. 'Not Found' if the leaf code is absent from the table."
        ),
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.finding_aid_author",
        "node_label": "Collection",
        "property": "finding_aid_author",
        "description": "The archivist(s) who created or last significantly updated the finding aid.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.creating_user",
        "node_label": "Collection",
        "property": "creating_user",
        "description": "ArchivesSpace username of the staff account that first created this record — a system audit field. Values are usernames in inconsistent formats (e.g. 'bsmith', 'ben smith'). Use for questions about which staff member ENTERED records. Distinct from the CREATED_BY relationship (the archival creator of the materials) and from the processed_by property on Collection (processing credit).",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Collection.processed_by",
        "node_label": "Collection",
        "property": "processed_by",
        "description": "List of processor credits extracted verbatim from the collection's Processing Information notes ('By: <name>' convention). Use for questions about who PROCESSED a collection or breakdowns by processor. Values may be individuals ('Ben Smith') or collective credits ('Baker Library Special Collections Staff'); empty when no note records a credit. Distinct from creating_user (staff account that entered the record) and the CREATED_BY relationship (archival creator).",
        "data_type": "List[String]",
        "required": False,
    },
    {
        "id": "Collection.modifying_user",
        "node_label": "Collection",
        "property": "modifying_user",
        "description": "ArchivesSpace username of the staff account that last modified this record — a system audit field with the same username-format caveats as creating_user.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Collection.create_time",
        "node_label": "Collection",
        "property": "create_time",
        "description": "ISO 8601 date and timestamp in UTC when this resource record was created in ArchivesSpace (e.g. '2019-07-18T14:32:09Z'). Always present.",
        "data_type": "DateTime",
        "required": True,
    },
    {
        "id": "Collection.user_mtime",
        "node_label": "Collection",
        "property": "user_mtime",
        "description": "Timestamp (UTC) of the last user-driven modification to this record in ArchivesSpace. Only changes when a staff member edits the record. Use this for any 'last modified' or 'recently edited' question.",
        "data_type": "DateTime",
        "required": True,
    },
    {
        "id": "Collection.suppressed",
        "node_label": "Collection",
        "property": "suppressed",
        "description": "Whether this collection record has been suppressed (hidden from non-privileged staff) in ArchivesSpace.",
        "data_type": "Boolean",
        "required": True,
    },
    {
        "id": "Collection.neo4j_created_at",
        "node_label": "Collection",
        "property": "neo4j_created_at",
        "description": "Timestamp when this Collection node was first created in the Neo4j graph database. Set once on initial insert; not updated on subsequent pipeline runs. Distinct from create_time (ArchivesSpace creation) and user_mtime (ArchivesSpace last modified).",
        "data_type": "DateTime",
        "required": False,
    },
    {
        "id": "Collection.neo4j_last_updated_at",
        "node_label": "Collection",
        "property": "neo4j_last_updated_at",
        "description": "Timestamp when this Collection node was last updated in the Neo4j graph database during a pipeline run. Updated on every subsequent run after the initial insert.",
        "data_type": "DateTime",
        "required": False,
    },
    {
        "id": "Collection.aspace_url",
        "node_label": "Collection",
        "property": "aspace_url",
        "description": "Direct link to this collection in the ArchivesSpace staff interface. ALWAYS return this alongside the title so the answer can display the title as a hyperlink.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Collection.embedding",
        "node_label": "Collection",
        "property": "embedding",
        "description": "384-dimensional semantic embedding of the collection's title and industry path, covered by the collection_embedding vector index. Query it with vector search or vector.similarity.cosine; NEVER return this property directly — it is a large float array with no human-readable value.",
        "data_type": "Vector (384 floats)",
        "required": False,
    },
    # Note
    {
        "id": "Note.persistent_id",
        "node_label": "Note",
        "property": "persistent_id",
        "description": "Globally unique identifier for this note within ArchivesSpace. Used as the unique key for the node.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Note.type",
        "node_label": "Note",
        "property": "type",
        "description": "DACS-based note type identifying the category of the note. Common values: abstract, bioghist, scopecontent, accessrestrict, acqinfo, userestrict, prefercite, processinfo, relatedmaterial, separatedmaterial.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Note.label",
        "node_label": "Note",
        "property": "label",
        "description": "Optional human-readable label that overrides the default display name for this note type.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Note.content",
        "node_label": "Note",
        "property": "content",
        "description": "Full plain-text content of the note, with XML markup stripped. May contain multi-paragraph narrative text.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Note.publish",
        "node_label": "Note",
        "property": "publish",
        "description": "Whether this note is publicly visible in the ArchivesSpace public interface.",
        "data_type": "Boolean",
        "required": False,
    },
    # NoteChunk
    {
        "id": "NoteChunk.chunk_id",
        "node_label": "NoteChunk",
        "property": "chunk_id",
        "description": "Unique key: the parent note's persistent_id plus '#' and the chunk sequence number (e.g. 'aspace_abc123#0').",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "NoteChunk.seq",
        "node_label": "NoteChunk",
        "property": "seq",
        "description": "0-based position of this chunk within its parent note's content.",
        "data_type": "Integer",
        "required": True,
    },
    {
        "id": "NoteChunk.text",
        "node_label": "NoteChunk",
        "property": "text",
        "description": "The raw text slice of the parent note's content that this chunk covers (~1,000 characters). Useful for showing the user which passage matched a semantic search.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "NoteChunk.embedding",
        "node_label": "NoteChunk",
        "property": "embedding",
        "description": "384-dimensional semantic embedding of this chunk's text with contextual prefix (parent record and note type), covered by the note_chunk_embedding vector index. Query it with vector search or vector.similarity.cosine; NEVER return this property directly — it is a large float array with no human-readable value.",
        "data_type": "Vector (384 floats)",
        "required": True,
    },
    # Agent
    {
        "id": "Agent.uri",
        "node_label": "Agent",
        "property": "uri",
        "description": "ArchivesSpace REST API URI for this agent (e.g. /agents/people/24021). Used as the unique key.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Agent.agent_type",
        "node_label": "Agent",
        "property": "agent_type",
        "description": "Classification of the agent. Values: 'person', 'corporate_entity', 'family'.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Agent.display_name",
        "node_label": "Agent",
        "property": "display_name",
        "description": "The authorized form of the agent's name in sort format (e.g. 'Land, Edwin H.' for a person, 'Polaroid Corporation' for a corporate entity).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Agent.existence_begin",
        "node_label": "Agent",
        "property": "existence_begin",
        "description": "Birth year (for persons) or founding year (for corporate entities/families).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Agent.existence_end",
        "node_label": "Agent",
        "property": "existence_end",
        "description": "Death year (for persons) or dissolution year (for corporate entities/families).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Agent.existence_expression",
        "node_label": "Agent",
        "property": "existence_expression",
        "description": "Free-text date expression for the agent's lifespan or active period when structured begin/end years are unavailable (e.g. 'active 1920s', 'fl. 1850–1870').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Agent.publish",
        "node_label": "Agent",
        "property": "publish",
        "description": "Whether this agent record is publicly visible in the ArchivesSpace public interface. Use to filter queries to public-facing agents only.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "Agent.create_time",
        "node_label": "Agent",
        "property": "create_time",
        "description": "ISO 8601 date and timestamp in UTC when this agent record was created in ArchivesSpace. ArchivesSpace populates this on every record; in the graph it is absent only on stub Agent nodes (agents referenced by a collection but deleted from ArchivesSpace before extraction — see the AgentFetchFailed label).",
        "data_type": "DateTime",
        "required": False,
    },
    {
        "id": "Agent.user_mtime",
        "node_label": "Agent",
        "property": "user_mtime",
        "description": "ISO 8601 timestamp of the last user-driven modification to this agent record in ArchivesSpace. Absent only on stub Agent nodes.",
        "data_type": "DateTime",
        "required": False,
    },
    {
        "id": "Agent.creating_user",
        "node_label": "Agent",
        "property": "creating_user",
        "description": "ArchivesSpace username of the staff account that first created this record — a system audit field. Values are usernames in inconsistent formats (e.g. 'bsmith', 'ben smith'). Use for questions about which staff member ENTERED records. Distinct from the CREATED_BY relationship (the archival creator of the materials) and from the processed_by property on Collection (processing credit). Absent only on stub Agent nodes.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Agent.modifying_user",
        "node_label": "Agent",
        "property": "modifying_user",
        "description": "ArchivesSpace username of the staff account that last modified this record — a system audit field with the same username-format caveats as creating_user.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Agent.aspace_url",
        "node_label": "Agent",
        "property": "aspace_url",
        "description": "Direct link to this agent in the ArchivesSpace staff interface. ALWAYS return this alongside the display name so the answer can display the name as a hyperlink.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Agent.embedding",
        "node_label": "Agent",
        "property": "embedding",
        "description": "384-dimensional semantic embedding of the agent's display name, covered by the agent_embedding vector index. Query it with vector search or vector.similarity.cosine; NEVER return this property directly — it is a large float array with no human-readable value.",
        "data_type": "Vector (384 floats)",
        "required": False,
    },
    # Extent
    {
        "id": "Extent.extent_type",
        "node_label": "Extent",
        "property": "extent_type",
        "description": "The unit of measurement for this extent (e.g. 'linear feet', 'gigabytes', 'items').",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Extent.number",
        "node_label": "Extent",
        "property": "number",
        "description": "The numeric quantity of the extent, stored as a string (e.g. '620'). Use toFloat() in Cypher for arithmetic.",
        "data_type": "String (numeric)",
        "required": True,
    },
    {
        "id": "Extent.portion",
        "node_label": "Extent",
        "property": "portion",
        "description": "Whether this extent covers the whole collection or only a part. Values: 'whole', 'part'.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Extent.container_summary",
        "node_label": "Extent",
        "property": "container_summary",
        "description": "Human-readable summary of the physical containers (e.g. '9 volumes, 1231 boxes, 74 oversize folders').",
        "data_type": "String",
        "required": False,
    },
    # ArchivalObject
    {
        "id": "ArchivalObject.uri",
        "node_label": "ArchivalObject",
        "property": "uri",
        "description": "ArchivesSpace REST API URI for this archival object (e.g. /repositories/11/archival_objects/123456). Used as the unique key.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "ArchivalObject.aspace_url",
        "node_label": "ArchivalObject",
        "property": "aspace_url",
        "description": "Direct link to this archival object in the ArchivesSpace staff interface, anchored within its collection tree. ALWAYS return this alongside the title so the answer can display the title as a hyperlink.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.embedding",
        "node_label": "ArchivalObject",
        "property": "embedding",
        "description": "384-dimensional semantic embedding of the archival object's title with its collection context, covered by the archival_object_embedding vector index. Query it with vector search or vector.similarity.cosine; NEVER return this property directly — it is a large float array with no human-readable value.",
        "data_type": "Vector (384 floats)",
        "required": False,
    },
    {
        "id": "ArchivalObject.level",
        "node_label": "ArchivalObject",
        "property": "level",
        "description": "Hierarchical level within the collection. Common values: 'series', 'subseries', 'file', 'item'.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "ArchivalObject.component_id",
        "node_label": "ArchivalObject",
        "property": "component_id",
        "description": "Short identifier for this component within its parent (e.g. 'I', 'II', '1.1', 'Box 3'). Often used as the series or sub-series number. Useful for queries like 'find Series II'.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.title",
        "node_label": "ArchivalObject",
        "property": "title",
        "description": "Title of this archival component as it appears in the finding aid.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.display_string",
        "node_label": "ArchivalObject",
        "property": "display_string",
        "description": "Full display-ready string combining the level designation, component_id, and title (e.g. 'Series I: Scientist records'). Prefer this for human-readable output.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.publish",
        "node_label": "ArchivalObject",
        "property": "publish",
        "description": "Whether this component is publicly visible in the ArchivesSpace public interface.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "ArchivalObject.date_inclusive_begin",
        "node_label": "ArchivalObject",
        "property": "date_inclusive_begin",
        "description": "Start year of the inclusive date range of the materials in this component (e.g. '1952').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.date_inclusive_end",
        "node_label": "ArchivalObject",
        "property": "date_inclusive_end",
        "description": "End year of the inclusive date range of the materials in this component (e.g. '1985').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.date_bulk_begin",
        "node_label": "ArchivalObject",
        "property": "date_bulk_begin",
        "description": "Start year of the bulk date range — the period most heavily represented in this component.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.date_bulk_end",
        "node_label": "ArchivalObject",
        "property": "date_bulk_end",
        "description": "End year of the bulk date range for this component.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.date_inclusive_expression",
        "node_label": "ArchivalObject",
        "property": "date_inclusive_expression",
        "description": "Human-readable inclusive date expression for this component (e.g. '1952-1985'). Use for display; prefer begin/end fields for range filtering.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.date_bulk_expression",
        "node_label": "ArchivalObject",
        "property": "date_bulk_expression",
        "description": "Human-readable bulk date expression for this component. Use for display; prefer begin/end fields for range filtering.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.ref_id",
        "node_label": "ArchivalObject",
        "property": "ref_id",
        "description": "Unique identifier for this component within its collection, used in EAD exports (e.g. 'ref123'). Stable reference for cross-system linking.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "ArchivalObject.position",
        "node_label": "ArchivalObject",
        "property": "position",
        "description": "Integer sort position of this component within its parent, reflecting the intellectual arrangement order. Use ORDER BY ao.position to return components in finding aid order.",
        "data_type": "Integer",
        "required": False,
    },
    {
        "id": "ArchivalObject.languages",
        "node_label": "ArchivalObject",
        "property": "languages",
        "description": "List of ISO 639-2 language codes for the languages of the materials in this component (e.g. ['eng', 'fre']).",
        "data_type": "List<String>",
        "required": False,
    },
    {
        "id": "ArchivalObject.create_time",
        "node_label": "ArchivalObject",
        "property": "create_time",
        "description": "ISO 8601 date and timestamp in UTC when this archival object was created in ArchivesSpace. Always present.",
        "data_type": "DateTime",
        "required": True,
    },
    {
        "id": "ArchivalObject.user_mtime",
        "node_label": "ArchivalObject",
        "property": "user_mtime",
        "description": "ISO 8601 timestamp of the last user-driven modification to this record in ArchivesSpace.",
        "data_type": "DateTime",
        "required": True,
    },
    {
        "id": "ArchivalObject.creating_user",
        "node_label": "ArchivalObject",
        "property": "creating_user",
        "description": "ArchivesSpace username of the staff account that first created this record — a system audit field. Values are usernames in inconsistent formats (e.g. 'bsmith', 'ben smith'). Use for questions about which staff member ENTERED records. Distinct from the CREATED_BY relationship (the archival creator of the materials) and from the processed_by property on Collection (processing credit).",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "ArchivalObject.modifying_user",
        "node_label": "ArchivalObject",
        "property": "modifying_user",
        "description": "ArchivesSpace username of the staff account that last modified this record — a system audit field with the same username-format caveats as creating_user.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "ArchivalObject.suppressed",
        "node_label": "ArchivalObject",
        "property": "suppressed",
        "description": "Whether this archival object has been suppressed (hidden from non-privileged staff) in ArchivesSpace.",
        "data_type": "Boolean",
        "required": True,
    },
    # RevisionStatement
    {
        "id": "RevisionStatement.date",
        "node_label": "RevisionStatement",
        "property": "date",
        "description": "Date of the revision event, as recorded in ArchivesSpace (free-text format, e.g. '12/02/2016').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "RevisionStatement.description",
        "node_label": "RevisionStatement",
        "property": "description",
        "description": "Narrative description of the revision that was made to the finding aid.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "RevisionStatement.repository_ref",
        "node_label": "RevisionStatement",
        "property": "repository_ref",
        "description": "ArchivesSpace URI of the repository associated with this revision statement (e.g. '/repositories/11').",
        "data_type": "String",
        "required": False,
    },
    # DigitalObject
    {
        "id": "DigitalObject.uri",
        "node_label": "DigitalObject",
        "property": "uri",
        "description": "ArchivesSpace REST API URI for this digital object (e.g. /repositories/11/digital_objects/701). Used as the unique key.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "DigitalObject.digital_object_id",
        "node_label": "DigitalObject",
        "property": "digital_object_id",
        "description": "A secondary identifier for the digital object, often a UUID or local system identifier (e.g. 'c93e20be-cece-413e-b775-bca6ae74ce21').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "DigitalObject.title",
        "node_label": "DigitalObject",
        "property": "title",
        "description": "Title of the digital object, typically matching the title of the linked archival component.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "DigitalObject.publish",
        "node_label": "DigitalObject",
        "property": "publish",
        "description": "Whether this digital object is publicly visible in the ArchivesSpace public interface. Use to filter queries to publicly accessible digital content.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "DigitalObject.restrictions",
        "node_label": "DigitalObject",
        "property": "restrictions",
        "description": "Whether access to this digital object is restricted.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "DigitalObject.suppressed",
        "node_label": "DigitalObject",
        "property": "suppressed",
        "description": "Whether this digital object has been suppressed (hidden) in ArchivesSpace.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "DigitalObject.creating_user",
        "node_label": "DigitalObject",
        "property": "creating_user",
        "description": "ArchivesSpace username of the staff account that first created this record — a system audit field. Values are usernames in inconsistent formats (e.g. 'bsmith', 'ben smith'). Use for questions about which staff member ENTERED records. Distinct from the CREATED_BY relationship (the archival creator of the materials) and from the processed_by property on Collection (processing credit).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "DigitalObject.modifying_user",
        "node_label": "DigitalObject",
        "property": "modifying_user",
        "description": "ArchivesSpace username of the staff account that last modified this record — a system audit field with the same username-format caveats as creating_user.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "DigitalObject.create_time",
        "node_label": "DigitalObject",
        "property": "create_time",
        "description": "ISO 8601 date and timestamp in UTC when this digital object was created in ArchivesSpace. Present on all fully extracted digital objects; absent only on unenriched stub nodes (e.g. digital objects deleted from ArchivesSpace after being referenced).",
        "data_type": "DateTime",
        "required": False,
    },
    {
        "id": "DigitalObject.user_mtime",
        "node_label": "DigitalObject",
        "property": "user_mtime",
        "description": "ISO 8601 timestamp of the last user-driven modification to this record in ArchivesSpace.",
        "data_type": "DateTime",
        "required": False,
    },
    # FileVersion
    {
        "id": "FileVersion.file_uri",
        "node_label": "FileVersion",
        "property": "file_uri",
        "description": "The URL or URI of the digital file (e.g. 'http://nrs.harvard.edu/urn-3:...'). Used as the unique key for this node.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "FileVersion.publish",
        "node_label": "FileVersion",
        "property": "publish",
        "description": "Whether this specific file version is publicly accessible.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "FileVersion.is_representative",
        "node_label": "FileVersion",
        "property": "is_representative",
        "description": "True if this is the primary file version to display for the digital object. Use WHERE fv.is_representative = true to retrieve the canonical file URI.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "FileVersion.identifier",
        "node_label": "FileVersion",
        "property": "identifier",
        "description": "Internal identifier for this file version within ArchivesSpace.",
        "data_type": "String",
        "required": False,
    },
    # ArchivalObject (additional)
    {
        "id": "ArchivalObject.container_instance_types",
        "node_label": "ArchivalObject",
        "property": "container_instance_types",
        "description": "List of physical container instance types associated with this archival component (e.g. ['graphic_materials', 'Mixed Materials']). Populated from the instances field where instance_type is not 'digital_object'. Absent if the component has no container instances.",
        "data_type": "List<String>",
        "required": False,
    },
    # AgentName
    {
        "id": "AgentName.sort_name",
        "node_label": "AgentName",
        "property": "sort_name",
        "description": "The full name in sort (inverted) order, e.g. 'Merritt, Leonidas (1844-1926)'. Primary field for name display and search.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentName.primary_name",
        "node_label": "AgentName",
        "property": "primary_name",
        "description": "The primary component of the name. For persons: family/last name. For corporate entities and families: the main name.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentName.rest_of_name",
        "node_label": "AgentName",
        "property": "rest_of_name",
        "description": "The remainder of a person's name after the primary_name (e.g. given name or initials). Not present for corporate entities or families.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentName.authorized",
        "node_label": "AgentName",
        "property": "authorized",
        "description": "Whether this name is the authorized form according to the naming convention used (e.g. LCNAF). Only one name per agent is typically authorized.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "AgentName.is_display_name",
        "node_label": "AgentName",
        "property": "is_display_name",
        "description": "Whether this name is the preferred display name for the agent. Use WHERE n.is_display_name = true to retrieve the primary display form.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "AgentName.name_order",
        "node_label": "AgentName",
        "property": "name_order",
        "description": "The order convention used for this name form. Common values: 'inverted' (family name first), 'direct' (given name first).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentName.authority_id",
        "node_label": "AgentName",
        "property": "authority_id",
        "description": "The authority file identifier associated with this specific name form (e.g. ' n 92062128 ' from LCNAF). May differ from AgentIdentifier.record_identifier.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentName.source",
        "node_label": "AgentName",
        "property": "source",
        "description": "The controlled vocabulary or authority source for this name (e.g. 'lcnaf', 'local').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentName.dates",
        "node_label": "AgentName",
        "property": "dates",
        "description": "Free-text date qualifier appended to this name form (e.g. '1844-1926'). Distinct from the agent's structured existence dates.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentName.use_date_begin",
        "node_label": "AgentName",
        "property": "use_date_begin",
        "description": "Start year of the period during which this name form was in use, derived from the name's use_dates structured date range.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentName.use_date_end",
        "node_label": "AgentName",
        "property": "use_date_end",
        "description": "End year of the period during which this name form was in use.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentName.use_date_expression",
        "node_label": "AgentName",
        "property": "use_date_expression",
        "description": "Human-readable expression of the name use date range (e.g. '1844-1926').",
        "data_type": "String",
        "required": False,
    },
    # AgentIdentifier
    {
        "id": "AgentIdentifier.record_identifier",
        "node_label": "AgentIdentifier",
        "property": "record_identifier",
        "description": "The authority file identifier string (e.g. 'n 2007049141' from LCNAF). Use this to match agents against external authority systems.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentIdentifier.source",
        "node_label": "AgentIdentifier",
        "property": "source",
        "description": "The authority source system for this identifier (e.g. 'naf' for Library of Congress Name Authority File).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentIdentifier.identifier_type",
        "node_label": "AgentIdentifier",
        "property": "identifier_type",
        "description": "The type classification of the identifier within its source system (e.g. 'loc' for Library of Congress).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentIdentifier.primary_identifier",
        "node_label": "AgentIdentifier",
        "property": "primary_identifier",
        "description": "True if this is the primary (canonical) identifier for the agent. Filter on this to retrieve the single most authoritative external ID.",
        "data_type": "Boolean",
        "required": False,
    },
    # AgentMaintenanceHistory
    {
        "id": "AgentMaintenanceHistory.maintenance_event_type",
        "node_label": "AgentMaintenanceHistory",
        "property": "maintenance_event_type",
        "description": "The type of maintenance event. Common values: 'created', 'modified', 'cancelled', 'deleted', 'derived', 'updated'.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentMaintenanceHistory.maintenance_agent_type",
        "node_label": "AgentMaintenanceHistory",
        "property": "maintenance_agent_type",
        "description": "Whether the maintenance was performed by a person or an automated tool. Values: 'human', 'machine'.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentMaintenanceHistory.agent",
        "node_label": "AgentMaintenanceHistory",
        "property": "agent",
        "description": "Name of the person or tool that performed the maintenance event (e.g. 'LCNAF Import plugin', 'bsmith@bbb.edu').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentMaintenanceHistory.event_date",
        "node_label": "AgentMaintenanceHistory",
        "property": "event_date",
        "description": "Date and time when the maintenance event occurred (e.g. '2026-03-18 00:00:00 UTC').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "AgentMaintenanceHistory.descriptive_note",
        "node_label": "AgentMaintenanceHistory",
        "property": "descriptive_note",
        "description": "Free-text description of what was done during this maintenance event.",
        "data_type": "String",
        "required": False,
    },
    # Accession
    {
        "id": "Accession.title",
        "node_label": "Accession",
        "property": "title",
        "description": "Full title of the accession as recorded in ArchivesSpace.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.accession_date",
        "node_label": "Accession",
        "property": "accession_date",
        "description": "Date the materials were formally accessioned into the repository (ISO 8601 date string, e.g. '2018-12-17').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.acquisition_type",
        "node_label": "Accession",
        "property": "acquisition_type",
        "description": "How the materials were acquired. Common values: 'gift', 'transfer', 'purchase', 'deposit'.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.resource_type",
        "node_label": "Accession",
        "property": "resource_type",
        "description": "Broad classification of the accession materials (e.g. 'harvard university records', 'papers').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.content_description",
        "node_label": "Accession",
        "property": "content_description",
        "description": "Narrative description of the content and nature of the accessioned materials.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.provenance",
        "node_label": "Accession",
        "property": "provenance",
        "description": "Statement of the origin and custody history of the accessioned materials. Covered by the free_text_index full-text index.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.inventory",
        "node_label": "Accession",
        "property": "inventory",
        "description": "Summary inventory or description of the items included in the accession.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.id_0",
        "node_label": "Accession",
        "property": "id_0",
        "description": "First component of the accession identifier (e.g. 'A-19-027'). Together with id_1, id_2, id_3 forms the full accession number.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.restrictions_apply",
        "node_label": "Accession",
        "property": "restrictions_apply",
        "description": "True if any access or use restrictions apply to this accession.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "Accession.access_restrictions",
        "node_label": "Accession",
        "property": "access_restrictions",
        "description": "True if access to the accession is restricted.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "Accession.use_restrictions",
        "node_label": "Accession",
        "property": "use_restrictions",
        "description": "True if use of the accession materials is restricted.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "Accession.publish",
        "node_label": "Accession",
        "property": "publish",
        "description": "Whether this accession record is publicly visible in the ArchivesSpace public interface.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "Accession.general_note",
        "node_label": "Accession",
        "property": "general_note",
        "description": "Free-text general note about the accession — may include processing instructions, MMS IDs, transfer form references, or other administrative context.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.access_restrictions_note",
        "node_label": "Accession",
        "property": "access_restrictions_note",
        "description": "Narrative description of any access restrictions on the accession. Present when access_restrictions is true or when staff need to record a clarifying note.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.create_time",
        "node_label": "Accession",
        "property": "create_time",
        "description": "ISO 8601 date and timestamp in UTC when this accession record was created in ArchivesSpace. ArchivesSpace populates this on every record; in the graph it is absent only on stub Accession nodes.",
        "data_type": "DateTime",
        "required": False,
    },
    {
        "id": "Accession.user_mtime",
        "node_label": "Accession",
        "property": "user_mtime",
        "description": "ISO 8601 timestamp of the last user-driven modification to this accession record in ArchivesSpace. Absent only on stub Accession nodes.",
        "data_type": "DateTime",
        "required": False,
    },
    {
        "id": "Accession.creating_user",
        "node_label": "Accession",
        "property": "creating_user",
        "description": "ArchivesSpace username of the staff account that first created this record — a system audit field. Values are usernames in inconsistent formats (e.g. 'bsmith', 'ben smith'). Use for questions about which staff member ENTERED records. Distinct from the CREATED_BY relationship (the archival creator of the materials) and from the processed_by property on Collection (processing credit).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.modifying_user",
        "node_label": "Accession",
        "property": "modifying_user",
        "description": "ArchivesSpace username of the staff account that last modified this record — a system audit field with the same username-format caveats as creating_user.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Accession.suppressed",
        "node_label": "Accession",
        "property": "suppressed",
        "description": "Whether this accession record has been suppressed (hidden from non-privileged staff) in ArchivesSpace.",
        "data_type": "Boolean",
        "required": False,
    },
    # Deaccession
    {
        "id": "Deaccession.description",
        "node_label": "Deaccession",
        "property": "description",
        "description": "Narrative description of the materials that were removed from the collection.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Deaccession.reason",
        "node_label": "Deaccession",
        "property": "reason",
        "description": "Explanation of why the materials were deaccessioned (e.g. poor condition, duplicate, out of scope).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Deaccession.disposition",
        "node_label": "Deaccession",
        "property": "disposition",
        "description": "Description of what was done with the removed materials (e.g. destroyed, transferred, returned to donor).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Deaccession.scope",
        "node_label": "Deaccession",
        "property": "scope",
        "description": "Whether the deaccession covers the whole collection or only a part. Values: 'whole', 'part'.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Deaccession.notification",
        "node_label": "Deaccession",
        "property": "notification",
        "description": "Whether the donor or relevant parties were notified about the deaccession.",
        "data_type": "Boolean",
        "required": False,
    },
    {
        "id": "Deaccession.date_expression",
        "node_label": "Deaccession",
        "property": "date_expression",
        "description": "Human-readable expression of the deaccession date (e.g. '2023 April 10').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Deaccession.date_begin",
        "node_label": "Deaccession",
        "property": "date_begin",
        "description": "Standardized start year of the deaccession date (e.g. '2023').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Deaccession.date_type",
        "node_label": "Deaccession",
        "property": "date_type",
        "description": "Date type as recorded in ArchivesSpace (e.g. 'single', 'inclusive').",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Deaccession.creating_user",
        "node_label": "Deaccession",
        "property": "creating_user",
        "description": "ArchivesSpace username of the staff account that first created this record — a system audit field. Values are usernames in inconsistent formats (e.g. 'bsmith', 'ben smith'). Use for questions about which staff member ENTERED records. Distinct from the CREATED_BY relationship (the archival creator of the materials) and from the processed_by property on Collection (processing credit).",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "Deaccession.create_time",
        "node_label": "Deaccession",
        "property": "create_time",
        "description": "Timestamp when this deaccession record was created in ArchivesSpace.",
        "data_type": "DateTime",
        "required": False,
    },
    {
        "id": "Deaccession.user_mtime",
        "node_label": "Deaccession",
        "property": "user_mtime",
        "description": "Timestamp of the last user-driven modification to this deaccession record in ArchivesSpace.",
        "data_type": "DateTime",
        "required": False,
    },
    {
        "id": "Deaccession.modifying_user",
        "node_label": "Deaccession",
        "property": "modifying_user",
        "description": "ArchivesSpace username of the staff account that last modified this record — a system audit field with the same username-format caveats as creating_user.",
        "data_type": "String",
        "required": False,
    },
    # Accession
    {
        "id": "Accession.uri",
        "node_label": "Accession",
        "property": "uri",
        "description": "ArchivesSpace REST API URI for this accession (e.g. /repositories/11/accessions/18100). Used as the unique key.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Accession.aspace_url",
        "node_label": "Accession",
        "property": "aspace_url",
        "description": "Direct link to this accession in the ArchivesSpace staff interface. ALWAYS return this alongside the title so the answer can display the title as a hyperlink.",
        "data_type": "String",
        "required": False,
    },
    # DocumentCollection (user-uploaded RAG library)
    {
        "id": "DocumentCollection.id",
        "node_label": "DocumentCollection",
        "property": "id",
        "description": "UUID unique key for this collection; referenced from Postgres scope tables.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "DocumentCollection.title",
        "node_label": "DocumentCollection",
        "property": "title",
        "description": "User-supplied name of the document collection.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "DocumentCollection.description",
        "node_label": "DocumentCollection",
        "property": "description",
        "description": "User-supplied description of the collection's contents and purpose.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "DocumentCollection.source_location",
        "node_label": "DocumentCollection",
        "property": "source_location",
        "description": "Name of the file or folder the user uploaded, as provided by the browser. Informational provenance only — original files are not retained after processing.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "DocumentCollection.uploaded_by",
        "node_label": "DocumentCollection",
        "property": "uploaded_by",
        "description": "Postgres asap_user id (UUID) of the user who uploaded the collection.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "DocumentCollection.visibility",
        "node_label": "DocumentCollection",
        "property": "visibility",
        "description": "'private' (visible only to the uploader) or 'shared' (visible to all users).",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "DocumentCollection.archived",
        "node_label": "DocumentCollection",
        "property": "archived",
        "description": "True when the collection is archived: hidden from selection but retained so past conversations that used it can still be rehydrated.",
        "data_type": "Boolean",
        "required": True,
    },
    {
        "id": "DocumentCollection.status",
        "node_label": "DocumentCollection",
        "property": "status",
        "description": "Processing lifecycle: 'processing' while the ingestion job runs, 'ready' when usable, 'failed' on error. Only 'ready' collections can be enabled for RAG.",
        "data_type": "String",
        "required": True,
    },
    # Document
    {
        "id": "Document.id",
        "node_label": "Document",
        "property": "id",
        "description": "UUID unique key for this document.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Document.file_name",
        "node_label": "Document",
        "property": "file_name",
        "description": "Original file name of the uploaded document (e.g. 'Polaroid Marketing 1972.pdf').",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Document.relative_path",
        "node_label": "Document",
        "property": "relative_path",
        "description": "Path of the document within the uploaded folder, including the file name.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Document.format",
        "node_label": "Document",
        "property": "format",
        "description": "Detected document format (e.g. 'pdf', 'docx', 'md', 'txt').",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Document.size_bytes",
        "node_label": "Document",
        "property": "size_bytes",
        "description": "Size of the uploaded file in bytes.",
        "data_type": "Integer",
        "required": True,
    },
    {
        "id": "Document.page_count",
        "node_label": "Document",
        "property": "page_count",
        "description": "Number of pages, for paginated formats such as PDF. Absent for plain text.",
        "data_type": "Integer",
        "required": False,
    },
    {
        "id": "Document.sha256",
        "node_label": "Document",
        "property": "sha256",
        "description": "SHA-256 checksum of the uploaded file, for de-duplication and integrity.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Document.chunk_count",
        "node_label": "Document",
        "property": "chunk_count",
        "description": "Number of DocumentChunk nodes generated from this document.",
        "data_type": "Integer",
        "required": True,
    },
    {
        "id": "Document.processing_status",
        "node_label": "Document",
        "property": "processing_status",
        "description": "'ok' or 'failed' — a single unreadable file does not fail the whole collection; failures are recorded here with the error property.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "Document.uploaded_at",
        "node_label": "Document",
        "property": "uploaded_at",
        "description": "Timestamp (UTC) when the document was uploaded and processed.",
        "data_type": "DateTime",
        "required": True,
    },
    # DocumentChunk
    {
        "id": "DocumentChunk.id",
        "node_label": "DocumentChunk",
        "property": "id",
        "description": "UUID unique key for this chunk; referenced from Postgres conversation_message_document_chunk for conversation rehydration.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "DocumentChunk.text",
        "node_label": "DocumentChunk",
        "property": "text",
        "description": "The source document text of this chunk. Shown to users as retrieval evidence and injected into conversations for retrieval-augmented answers.",
        "data_type": "String",
        "required": True,
    },
    {
        "id": "DocumentChunk.seq",
        "node_label": "DocumentChunk",
        "property": "seq",
        "description": "0-based position of this chunk within its parent document's reading order.",
        "data_type": "Integer",
        "required": True,
    },
    {
        "id": "DocumentChunk.page_no",
        "node_label": "DocumentChunk",
        "property": "page_no",
        "description": "Page number the chunk starts on, for paginated formats. Used in citations.",
        "data_type": "Integer",
        "required": False,
    },
    {
        "id": "DocumentChunk.heading_path",
        "node_label": "DocumentChunk",
        "property": "heading_path",
        "description": "Section heading context for the chunk (e.g. 'Product History > SX-70 Launch'). Used in citations.",
        "data_type": "String",
        "required": False,
    },
    {
        "id": "DocumentChunk.embedding",
        "node_label": "DocumentChunk",
        "property": "embedding",
        "description": "384-dimensional semantic embedding of the chunk text with document context, covered by the document_chunk_embedding vector index. Query it with vector search or vector.similarity.cosine; NEVER return this property directly — it is a large float array with no human-readable value.",
        "data_type": "Vector (384 floats)",
        "required": True,
    },
]


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class MetadataExtractor(BaseExtractor):
    """Writes schema metadata into Neo4j as a queryable meta-graph.

    Runs last in the pipeline after all data has been loaded. The metadata
    is hardcoded in this module — it does not make any ArchivesSpace API calls.
    """

    def extract(self) -> list[Any]:
        """Return the three categories of schema metadata as a single list."""
        return [
            {"kind": "node", "items": NODE_SCHEMAS},
            {"kind": "relationship", "items": RELATIONSHIP_SCHEMAS},
            {"kind": "property", "items": PROPERTY_SCHEMAS},
        ]

    def transform(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Pass through — metadata is already in its final form."""
        return raw

    def load(self, records: list[dict[str, Any]]) -> None:
        """Write all schema metadata nodes into Neo4j."""
        for batch in records:
            kind = batch["kind"]
            for item in batch["items"]:
                if kind == "node":
                    self.neo4j.merge_node_schema(item)
                elif kind == "relationship":
                    self.neo4j.merge_relationship_schema(item)
                elif kind == "property":
                    self.neo4j.merge_property_schema(item)

        logger.info(
            "MetadataExtractor: wrote %d NodeSchema, %d RelationshipSchema, "
            "%d PropertySchema nodes.",
            len(NODE_SCHEMAS),
            len(RELATIONSHIP_SCHEMAS),
            len(PROPERTY_SCHEMAS),
        )
