"""Extractor: ArchivesSpace Resources → Neo4j Collection nodes and related graph."""

import logging
from pathlib import Path
from typing import Any

from asapextractor import config
from asapextractor.extractors.base import BaseExtractor
from asapextractor.utils import (
    extract_processed_by,
    rename_audit_fields,
    aspace_public_url,
    extract_dates,
    extract_languages,
    transform_note,
    agent_type_from_uri,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Industry lookup
# ---------------------------------------------------------------------------

def _load_industry_lookup() -> dict[str, str]:
    """Load the industry code → label mapping from resources/industry-list.csv.

    Each row is parsed by splitting on the first comma only, so industry labels
    that contain commas are preserved correctly as part of the label value.
    Both code and label are stripped of leading/trailing whitespace.
    Codes are treated as strings (they may contain letters, e.g. '131A2').
    """
    csv_path = Path(__file__).parents[3] / "resources" / "industry-list.csv"
    lookup: dict[str, str] = {}
    try:
        with csv_path.open(encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0:  # skip header row
                    continue
                line = line.rstrip("\n")
                if "," not in line:
                    continue
                code, label = line.split(",", 1)
                code = code.strip()
                label = label.strip()
                if code:
                    lookup[code] = label
        logger.debug("Loaded %d industry codes from %s.", len(lookup), csv_path)
    except FileNotFoundError:
        logger.warning(
            "Industry lookup CSV not found at %s — 'industry' will be set to 'Not Applicable'.",
            csv_path,
        )
    return lookup


_INDUSTRY_LOOKUP: dict[str, str] = _load_industry_lookup()


# Canonical casing for known prefix families. Source id_0 values carry
# case/whitespace variants ('MSS:421', ' Mss:783', 'ARCH GA 9.75') — 28
# such records found by the accuracy evaluation (Q2, 2026-08-12). id_0
# itself stays verbatim; only this DERIVED property is normalized, so
# exact-match Cypher on the canonical values always works.
_CANONICAL_PREFIXES = {
    "mss": "Mss",
    "arch": "Arch",
    "kress": "Kress",
    "vis": "Vis",
    "archive-it": "Archive-It",
    "other": "Other",
}


def _compute_collection_type_prefix(id_0: str) -> str:
    """Extract the collection type prefix from id_0, normalized to
    canonical casing (see _CANONICAL_PREFIXES).

    Rules:
      - id_0 contains a colon → everything before the first colon.
      - id_0 starts with 'ARCH' (case-insensitive) and has no colon → 'Arch'.
      - All other values → 'Other'.
    """
    id_0 = (id_0 or "").strip()
    if not id_0:
        return "Other"
    if ":" in id_0:
        raw = id_0.split(":", 1)[0].strip()
        return _CANONICAL_PREFIXES.get(raw.lower(), raw)
    if id_0.upper().startswith("ARCH"):
        return "Arch"
    return "Other"


def _compute_industry_code(id_0: str) -> str:
    """Extract the raw industry code from id_0 for MSS: prefixed records only.

    Returns the first whitespace-delimited token after 'MSS:' (case-insensitive),
    or an empty string for all other id_0 values.
    """
    if id_0.upper()[:4] == "MSS:":
        remainder = id_0[4:].strip()
        return remainder.split()[0] if remainder else ""
    return "Not Applicable"


def _compute_industry(id_0: str) -> str:
    """Derive a hierarchical industry label from an id_0 value.

    Only MSS: prefixed records carry an industry code. All others return
    'Not Applicable'.

    Label format by code length:
      1 char  → '{code_label}'
      2 chars → '{root_label}-{code_label}'
      3+ chars → '{root_label}-{second_level_label}-{code_label}'
                  where second_level_label is 'None' if the 2-char prefix
                  is absent from the lookup table.

    The root is the first character of the code; the second level is the
    first two characters (taken literally, including any embedded letters).
    """
    if id_0.upper()[:4] != "MSS:":
        return "Not Applicable"

    remainder = id_0[4:].strip()
    code = remainder.split()[0] if remainder else ""
    if not code:
        return "Not Found"

    code_label = _INDUSTRY_LOOKUP.get(code, "Not Found")

    if len(code) == 1:
        return code_label

    root_label = _INDUSTRY_LOOKUP.get(code[0], "Not Found")

    if len(code) == 2:
        return f"{root_label}-{code_label}"

    # 3+ characters: include second-level (first 2 chars)
    second_label = _INDUSTRY_LOOKUP.get(code[:2], "None")
    return f"{root_label}-{second_label}-{code_label}"

# Fields present on a raw resource record that we intentionally exclude
_EXCLUDED_FIELDS = frozenset({
    "lock_version",
    "jsonmodel_type",
    "is_slug_auto",
    "import_previous_arks",
    "metadata_rights_declarations",
    "linked_events",
    # These are handled as separate nodes / relationships, not scalar props
    "notes",
    "extents",
    "lang_materials",
    "dates",
    "linked_agents",
    "related_accessions",
    "revision_statements",
    # Internal refs we don't need as properties
    "repository",
    "tree",
    "external_ids",
    "external_documents",
    "rights_statements",
    "related_events",
    "classifications",
    "instances",
})


_DEACCESSION_EXCLUDED = frozenset({
    "lock_version",
    "jsonmodel_type",
    "repository",
    "extents",   # complex sub-object; not currently extracted
    "date",      # flattened into scalar prefixed fields below
})


def _transform_deaccession(d: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw deaccession entry into Neo4j-ready properties."""
    props: dict[str, Any] = rename_audit_fields({
        k: v for k, v in d.items()
        if k not in _DEACCESSION_EXCLUDED and not isinstance(v, (dict, list))
    })
    date = d.get("date") or {}
    props["date_expression"] = date.get("expression") or ""
    props["date_begin"] = date.get("begin") or ""
    props["date_type"] = date.get("date_type") or ""
    props["date_label"] = date.get("label") or ""
    return props


def _transform_resource(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a raw resource record into a structured dict ready for loading.

    Returns None if the record has no ead_id (cannot be uniquely keyed).
    """
    ead_id = raw.get("ead_id", "")
    if not ead_id:
        logger.warning(
            "Skipping resource (uri=%s) — ead_id is missing.", raw.get("uri", "<unknown>")
        )
        return None

    # --- Scalar properties -----------------------------------------------
    scalar_props: dict[str, Any] = rename_audit_fields({
        k: v for k, v in raw.items()
        if k not in _EXCLUDED_FIELDS and not isinstance(v, (dict, list))
    })

    # --- Derived / flattened properties -----------------------------------
    dates = extract_dates(raw.get("dates") or [])
    languages = extract_languages(raw.get("lang_materials") or [])

    # Stripped once for ALL id_0-derived properties: source values with a
    # leading space (' Mss:783 ...') otherwise break industry derivation
    # too, not just the type prefix. The stored id_0 property stays verbatim.
    _id0 = str(scalar_props.get("id_0") or "").strip()

    collection = {
        "ead_id": ead_id,
        **scalar_props,
        **dates,
        "languages": languages,
        "collection_type_prefix": _compute_collection_type_prefix(_id0),
        "industry_leaf_code": _compute_industry_code(_id0),
        "industry_path": _compute_industry(_id0),
        "aspace_url": aspace_public_url(config.ASPACE_PUBLIC_URL, raw.get("uri", "")),
    }

    # Derive top-level, second-level, and leaf industry codes and labels
    _icode = collection["industry_leaf_code"]
    if _icode == "Not Applicable":
        collection["industry_top_level_code"] = "Not Applicable"
        collection["industry_second_level_code"] = "Not Applicable"
        collection["industry_top_level_label"] = "Not Applicable"
        collection["industry_second_level_label"] = "Not Applicable"
        collection["industry_leaf_label"] = "Not Applicable"
    else:
        _root = _icode[0] if _icode else ""
        _second = _icode[:2] if len(_icode) >= 2 else ""
        collection["industry_top_level_code"] = _root or "Not Found"
        collection["industry_second_level_code"] = _second if _second else "None"
        collection["industry_top_level_label"] = _INDUSTRY_LOOKUP.get(_root, "Not Found") if _root else "Not Found"
        collection["industry_second_level_label"] = _INDUSTRY_LOOKUP.get(_second, "None") if _second else "None"
        collection["industry_leaf_label"] = _INDUSTRY_LOOKUP.get(_icode, "Not Found") if _icode else "Not Found"

    # --- Notes ------------------------------------------------------------
    notes = [
        n for raw_note in (raw.get("notes") or [])
        if (n := transform_note(raw_note)) is not None
    ]

    # Processor credits: Baker's descriptive practice records who processed a
    # collection inside the Processing Information note as "By: <name>".
    # Materialize that convention as a queryable property so processor
    # breakdowns don't re-parse prose per question. Names are kept VERBATIM
    # (including collective credits like "Baker Library Special Collections
    # Staff") — normalizing identities is a data-cleanup concern, not the
    # pipeline's.
    collection["processed_by"] = extract_processed_by(
        n["content"] for n in notes if n.get("type") == "processinfo"
    )

    # --- Extents ----------------------------------------------------------
    extents = [
        {
            "extent_type": e.get("extent_type", ""),
            "number": e.get("number", ""),
            "portion": e.get("portion", ""),
            "container_summary": e.get("container_summary", ""),
        }
        for e in (raw.get("extents") or [])
    ]

    # --- Revision statements ----------------------------------------------
    revisions = [
        {
            "date": r.get("date", ""),
            "description": r.get("description", ""),
            "repository_ref": (r.get("repository") or {}).get("ref", ""),
        }
        for r in (raw.get("revision_statements") or [])
    ]

    # --- Agent stubs + roles ---------------------------------------------
    agent_links = [
        {
            "uri": la["ref"],
            "role": la.get("role", ""),
            "agent_type": agent_type_from_uri(la["ref"]),
        }
        for la in (raw.get("linked_agents") or [])
        if la.get("ref")
    ]

    # --- Accession stubs -------------------------------------------------
    accession_links = [
        {"uri": acc["ref"]}
        for acc in (raw.get("related_accessions") or [])
        if acc.get("ref")
    ]

    # --- Deaccessions ----------------------------------------------------
    deaccessions = [
        _transform_deaccession(d)
        for d in (raw.get("deaccessions") or [])
    ]

    return {
        "collection": collection,
        "notes": notes,
        "extents": extents,
        "revisions": revisions,
        "agent_links": agent_links,
        "accession_links": accession_links,
        "deaccessions": deaccessions,
    }


class ResourceExtractor(BaseExtractor):
    """Fetches all resource records for the configured repository and writes
    Collections, Notes, Extents, RevisionStatements, stub Agents, and stub
    Accessions into Neo4j.
    """

    def extract(self) -> list[Any]:
        """Page through all resources in the repository and return raw records."""
        endpoint = f"repositories/{config.ASPACE_REPO_ID}/resources"
        all_records: list[Any] = []

        for page_num, page in enumerate(
            self.aspace.get_paged(endpoint, page_size=config.ASPACE_PAGE_SIZE), start=1
        ):
            logger.info(
                "Fetched page %d: %d records (running total: %d)",
                page_num,
                len(page),
                len(all_records) + len(page),
            )
            all_records.extend(page)

        return all_records

    def transform(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Transform raw resource records, skipping those without ead_id."""
        records = []
        for item in raw:
            transformed = _transform_resource(item)
            if transformed is not None:
                records.append(transformed)
        return records

    def load(self, records: list[dict[str, Any]]) -> None:
        """Write all transformed records into Neo4j."""
        loaded = 0
        for rec in records:
            col = rec["collection"]
            uri = col.get("uri", "")

            # 1. Collection node
            self.neo4j.merge_collection(col)

            # 2. Notes
            for note in rec["notes"]:
                self.neo4j.merge_note(note)
                if uri:
                    self.neo4j.link_note(uri, note["persistent_id"])

            # 3. Extents (delete + recreate)
            if uri:
                self.neo4j.replace_extents(uri, rec["extents"])

            # 4. Revision statements (delete + recreate)
            if uri:
                self.neo4j.replace_revision_statements(uri, rec["revisions"])

            # 5. Agent stubs + relationships
            for al in rec["agent_links"]:
                self.neo4j.merge_agent_stub(al["uri"])
                if uri:
                    self.neo4j.link_agent(uri, al["uri"], al["role"])

            # 6. Accession stubs + relationships
            for acc in rec["accession_links"]:
                self.neo4j.merge_accession_stub(acc["uri"])
                if uri:
                    self.neo4j.link_accession(uri, acc["uri"])

            # 7. Deaccessions (delete + recreate)
            if uri:
                self.neo4j.replace_deaccessions(uri, rec["deaccessions"])

            loaded += 1

        logger.info(
            "ResourceExtractor: loaded %d collections into Neo4j.", loaded
        )
