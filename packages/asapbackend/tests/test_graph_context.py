"""
The system prompt's schema and metadata must not reveal the document library.

Fixtures mirror the live shapes captured 2026-10-09: db.schema.* procedure
rows for the schema, and NodeSchema/PropertySchema/RelationshipSchema rows
(as returned by prompts/metadata_query.cypher) for the metadata.
"""

from __future__ import annotations

import json

from asapbackend.services import graph_context as gc
from asapbackend.tools.validator import get_allowed_tool_names


# ── Fixtures ────────────────────────────────────────────────────────────────

NODE_PROPS = [
    {"nodeLabels": ["Collection"], "propertyName": "ead_id", "propertyTypes": ["STRING NOT NULL"]},
    {"nodeLabels": ["Collection"], "propertyName": "title", "propertyTypes": ["STRING"]},
    {"nodeLabels": ["Collection"], "propertyName": "create_time", "propertyTypes": ["DATE_TIME", "STRING"]},
    {"nodeLabels": ["Note", "IndexableNote"], "propertyName": "content", "propertyTypes": ["STRING"]},
    {"nodeLabels": ["NoteChunk"], "propertyName": "embedding", "propertyTypes": ["LIST<FLOAT NOT NULL>"]},
    {"nodeLabels": ["NoteChunk"], "propertyName": "text", "propertyTypes": ["STRING"]},
    {"nodeLabels": ["Inferred", "InferredAgent"], "propertyName": "name", "propertyTypes": ["STRING"]},
    {"nodeLabels": ["DocumentCollection"], "propertyName": "title", "propertyTypes": ["STRING"]},
    {"nodeLabels": ["DocumentCollection"], "propertyName": "uploaded_by", "propertyTypes": ["STRING"]},
    {"nodeLabels": ["Document"], "propertyName": "file_name", "propertyTypes": ["STRING"]},
    {"nodeLabels": ["DocumentChunk"], "propertyName": "text", "propertyTypes": ["STRING"]},
    {"nodeLabels": ["DocumentChunk"], "propertyName": "embedding", "propertyTypes": ["LIST<FLOAT NOT NULL>"]},
]
REL_PROPS = [
    {"relType": ":`HAS_NOTE`", "propertyName": None, "propertyTypes": None},
    {"relType": ":`HAS_CHUNK`", "propertyName": None, "propertyTypes": None},
    {"relType": ":`CONTAINS`", "propertyName": None, "propertyTypes": None},
    {"relType": ":`HAS_DOCUMENT_CHUNK`", "propertyName": None, "propertyTypes": None},
    {"relType": ":`RICO_HAS_OR_HAD_LEADER`", "propertyName": "confidence", "propertyTypes": ["FLOAT"]},
    {"relType": ":`RICO_HAS_OR_HAD_LEADER`", "propertyName": "quotes", "propertyTypes": ["LIST<STRING NOT NULL>"]},
]
EDGES = [
    ("HAS_NOTE", "Collection", "Note"),
    ("HAS_CHUNK", "Note", "NoteChunk"),
    ("HAS_DOCUMENT_CHUNK", "Document", "DocumentChunk"),
    ("CONTAINS", "DocumentCollection", "Document"),
    ("RICO_HAS_OR_HAD_LEADER", "InferredAgent", "InferredAgent"),
]

METADATA_ROWS = [
    {"schema_type": "NodeSchema", "details": {"label": "Collection", "neo4j_label": "Collection",
        "description": "An ArchivesSpace resource record.", "aspace_jsonmodel": "resource"}},
    {"schema_type": "NodeSchema", "details": {"label": "NoteChunk", "neo4j_label": "NoteChunk",
        "description": "A retrieval-sized slice of an IndexableNote's content. Semantic searches over note "
                       "content should target NoteChunk, then traverse (parent)-[:HAS_NOTE]->(:Note)-[:HAS_CHUNK]->(chunk) "
                       "back to the parent record."}},
    {"schema_type": "NodeSchema", "details": {"label": "Deaccession", "neo4j_label": "Deaccession",
        "description": "A record documenting the removal of materials from an archival collection."}},
    {"schema_type": "NodeSchema", "details": {"label": "DocumentCollection", "neo4j_label": "DocumentCollection",
        "description": "A user-uploaded library of supporting documents."}},
    {"schema_type": "NodeSchema", "details": {"label": "Document", "neo4j_label": "Document", "description": "A single document."}},
    {"schema_type": "NodeSchema", "details": {"label": "DocumentChunk", "neo4j_label": "DocumentChunk", "description": "A slice."}},
    {"schema_type": "PropertySchema", "details": {"id": "Collection.title", "node_label": "Collection", "property": "title",
        "data_type": "String", "description": "Title. Use WHERE c.title CONTAINS 'x' for substring search."}},
    {"schema_type": "PropertySchema", "details": {"id": "DocumentChunk.text", "node_label": "DocumentChunk",
        "property": "text", "data_type": "String", "description": "Chunk text."}},
    {"schema_type": "PropertySchema", "details": {"id": "DocumentCollection.uploaded_by", "node_label": "DocumentCollection",
        "property": "uploaded_by", "data_type": "String", "description": "Owner."}},
    {"schema_type": "RelationshipSchema", "details": {"type": "HAS_CHUNK", "from_labels": "Note, Document",
        "to_labels": "NoteChunk, DocumentChunk",
        "description": "Links a text-bearing node to its retrieval-sized chunks used for semantic search: "
                       "IndexableNote to NoteChunk (archival note content) and Document to DocumentChunk "
                       "(uploaded document content). Chunks are ordered by their seq property. For note chunks, "
                       "traverse (parent)-[:HAS_NOTE]->(note)-[:HAS_CHUNK]->(chunk) to find the owning record; "
                       "for document chunks, traverse (dc:DocumentCollection)-[:CONTAINS]->(d:Document)-[:HAS_CHUNK]->(chunk)."}},
    {"schema_type": "RelationshipSchema", "details": {"type": "HAS_DOCUMENT_CHUNK", "from_labels": "Document",
        "to_labels": "DocumentChunk", "description": "Links a Document to its chunks."}},
    {"schema_type": "RelationshipSchema", "details": {"type": "CONTAINS", "from_labels": "DocumentCollection",
        "to_labels": "Document", "description": "Links a DocumentCollection to the Documents inside it."}},
    {"schema_type": "RelationshipSchema", "details": {"type": "HAS_NOTE", "from_labels": "Collection, ArchivalObject",
        "to_labels": "Note", "description": "Attaches a note to a record."}},
]


# ── Schema ──────────────────────────────────────────────────────────────────

def test_schema_has_no_document_library():
    schema = gc.assemble_schema(NODE_PROPS, REL_PROPS, EDGES)
    text = json.dumps(schema)
    assert gc.offending_tokens(text) == []
    keys = {e["key"] for e in schema}
    assert {"Collection", "Note", "IndexableNote", "NoteChunk", "InferredAgent", "HAS_NOTE", "HAS_CHUNK"} <= keys
    assert not keys & {"Document", "DocumentChunk", "DocumentCollection", "CONTAINS", "HAS_DOCUMENT_CHUNK"}


def test_schema_keeps_the_archival_side_of_has_chunk():
    schema = {e["key"]: e["value"] for e in gc.assemble_schema(NODE_PROPS, REL_PROPS, EDGES)}
    assert schema["Note"]["relationships"]["HAS_CHUNK"] == {"direction": "out", "labels": ["NoteChunk"]}
    assert schema["NoteChunk"]["relationships"]["HAS_CHUNK"] == {"direction": "in", "labels": ["Note"]}
    assert "DocumentChunk" not in json.dumps(schema["Note"])


def test_schema_mirrors_the_prompt_format():
    schema = {e["key"]: e["value"] for e in gc.assemble_schema(NODE_PROPS, REL_PROPS, EDGES)}
    assert schema["Collection"] == {
        "type": "node",
        "properties": {"create_time": "DATE_TIME|STRING", "ead_id": "STRING", "title": "STRING"},
        "relationships": {"HAS_NOTE": {"direction": "out", "labels": ["Note"]}},
    }
    assert schema["NoteChunk"]["properties"] == {"embedding": "LIST", "text": "STRING"}
    assert schema["RICO_HAS_OR_HAD_LEADER"] == {
        "type": "relationship", "properties": {"confidence": "FLOAT", "quotes": "LIST"},
    }
    assert schema["HAS_NOTE"] == {"type": "relationship"}


def test_multi_label_nodes_get_properties_on_each_label():
    schema = {e["key"]: e["value"] for e in gc.assemble_schema(NODE_PROPS, REL_PROPS, EDGES)}
    assert schema["Note"]["properties"] == {"content": "STRING"}
    assert schema["IndexableNote"]["properties"] == {"content": "STRING"}


# ── Metadata ────────────────────────────────────────────────────────────────

def test_metadata_drops_document_entities():
    kept = gc.filter_metadata(METADATA_ROWS)
    idents = {r["details"].get("label") or r["details"].get("id") or r["details"].get("type") for r in kept}
    assert idents == {"Collection", "NoteChunk", "Deaccession", "Collection.title", "HAS_CHUNK", "HAS_NOTE"}


def test_metadata_text_never_mentions_the_document_library():
    text = gc.render_metadata(gc.filter_metadata(METADATA_ROWS))
    assert gc.offending_tokens(text) == []


def test_metadata_rewrites_legacy_combined_has_chunk_row():
    # Graphs extracted before 2026-10 carry one HAS_CHUNK row covering both
    # notes and documents; the filter must still neutralise it.
    kept = {r["details"].get("type"): r["details"] for r in gc.filter_metadata(METADATA_ROWS)
            if r["schema_type"] == "RelationshipSchema"}
    hc = kept["HAS_CHUNK"]
    assert hc["from_labels"] == "Note"
    assert hc["to_labels"] == "NoteChunk"
    assert "Chunks are ordered by their seq property." in hc["description"]
    assert "(parent)-[:HAS_NOTE]->(note)-[:HAS_CHUNK]->(chunk)" in hc["description"]
    assert "Document" not in hc["description"]
    assert "CONTAINS" not in hc["description"]


def test_metadata_keeps_prose_that_merely_looks_similar():
    kept = {r["details"].get("label") or r["details"].get("id"): r["details"] for r in gc.filter_metadata(METADATA_ROWS)}
    # lowercase "documenting" is English, not the label
    assert "documenting" in kept["Deaccession"]["description"]
    # CONTAINS the Cypher string predicate is not the relationship type
    assert "CONTAINS 'x'" in kept["Collection.title"]["description"]
    # NoteChunk's archival HAS_CHUNK traversal hint survives intact
    assert "[:HAS_CHUNK]->(chunk)" in kept["NoteChunk"]["description"]


def test_scrub_helpers():
    assert gc.mentions_documents("see (dc:DocumentCollection)") is True
    assert gc.mentions_documents("traverse -[:CONTAINS]->") is True
    assert gc.mentions_documents("WHERE c.title CONTAINS 'x'") is False
    assert gc.mentions_documents("documenting the removal") is False
    assert gc.scrub_text("Keep this. Drop the Document sentence. Keep that; drop [:CONTAINS] clause.") == \
        "Keep this. Keep that;"


# ── Tool surface ────────────────────────────────────────────────────────────

def test_get_schema_is_not_offered_to_the_model():
    names = get_allowed_tool_names()
    assert "get-schema" not in names
    assert "read-cypher" in names
