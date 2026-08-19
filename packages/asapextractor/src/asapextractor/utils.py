"""Shared data-transformation utilities for the extraction pipeline."""

import re
from datetime import datetime
from typing import Any

_ASPACE_TIMESTAMP_FIELDS = frozenset({"create_time", "user_mtime"})

# ArchivesSpace bumps system_mtime on system-initiated touches (cascades from
# linked records, repository-wide reindex triggers), so it does not reflect a
# meaningful user edit. It is dropped from the graph entirely: user_mtime is
# the "last modified" field, create_time the "created" field.
_ASPACE_DROPPED_FIELDS = frozenset({"system_mtime"})


def _parse_aspace_timestamp(value: str) -> datetime | None:
    """Parse an ArchivesSpace ISO 8601 UTC timestamp to a timezone-aware datetime.

    Returns None if the value is empty or unparseable so callers can fall back
    to the raw string rather than silently dropping the field.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Note helpers
# ---------------------------------------------------------------------------

def extract_note_content(note: dict[str, Any]) -> str:
    """Return plain-text content from a singlepart or multipart note.

    Strips XML/HTML-like markup tags that ArchivesSpace embeds in note text.
    """
    if note.get("jsonmodel_type") in ("note_singlepart", "note_digital_object"):
        raw = " ".join(note.get("content") or [])
    else:
        # multipart (note_multipart, note_bioghist, etc.) — content lives in subnotes
        parts = [
            subnote.get("content", "")
            for subnote in (note.get("subnotes") or [])
            if subnote.get("jsonmodel_type") == "note_text"
        ]
        raw = " ".join(parts)

    return re.sub(r"<[^>]+>", "", raw).strip()


def transform_note(note: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a raw ArchivesSpace note dict to a Neo4j-ready property dict.

    Returns None if the note has no persistent_id (cannot be uniquely keyed).
    """
    pid = note.get("persistent_id", "")
    if not pid:
        return None

    return {
        "persistent_id": pid,
        # prefer explicit 'type'; fall back to jsonmodel_type for agent notes
        "type": note.get("type") or note.get("jsonmodel_type", ""),
        "label": note.get("label") or "",
        "content": extract_note_content(note),
        "publish": bool(note.get("publish", False)),
    }


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def extract_dates(dates: list[dict[str, Any]]) -> dict[str, str]:
    """Flatten the `dates` array into properties keyed by date_type.

    Example output keys: date_inclusive_begin, date_inclusive_end,
    date_bulk_begin, date_bulk_end, date_inclusive_expression, …
    """
    result: dict[str, str] = {}
    for d in dates:
        date_type = d.get("date_type", "")
        if date_type:
            result[f"date_{date_type}_expression"] = d.get("expression") or ""
            result[f"date_{date_type}_begin"] = d.get("begin") or ""
            result[f"date_{date_type}_end"] = d.get("end") or ""
    return result


# ---------------------------------------------------------------------------
# Language helpers
# ---------------------------------------------------------------------------

def extract_languages(lang_materials: list[dict[str, Any]]) -> list[str]:
    """Extract unique ISO language codes from the `lang_materials` array."""
    seen: set[str] = set()
    codes: list[str] = []
    for lm in lang_materials or []:
        ls = lm.get("language_and_script") or {}
        lang = ls.get("language", "")
        if lang and lang not in seen:
            seen.add(lang)
            codes.append(lang)
    return codes


# ---------------------------------------------------------------------------
# Agent / relationship helpers
# ---------------------------------------------------------------------------

def agent_type_from_uri(uri: str) -> str:
    """Derive the agent type string from an ArchivesSpace agent URI.

    /agents/people/123         -> "person"
    /agents/corporate_entities/123 -> "corporate_entity"
    /agents/families/123       -> "family"
    """
    _map = {
        "people": "person",
        "corporate_entities": "corporate_entity",
        "families": "family",
    }
    parts = uri.strip("/").split("/")
    # uri parts: ['agents', '<plural_type>', '<id>']
    if len(parts) >= 2:
        return _map.get(parts[1], parts[1])
    return ""


def sanitize_rel_type(relator: str) -> str:
    """Convert an ArchivesSpace relator string to a valid Cypher relationship type.

    e.g. 'is_member_of' -> 'IS_MEMBER_OF'
    Non-alphanumeric characters are replaced with underscores.
    """
    return re.sub(r"[^a-zA-Z0-9]", "_", relator).upper()


def label_from_uri(uri: str) -> str:
    """Infer the Neo4j node label for a given ArchivesSpace URI.

    Used to build label-qualified MATCH clauses for better query performance.
    """
    if "/resources/" in uri:
        return "Collection"
    if "/archival_objects/" in uri:
        return "ArchivalObject"
    if "/agents/" in uri:
        return "Agent"
    if "/digital_objects/" in uri:
        return "DigitalObject"
    if "/accessions/" in uri:
        return "Accession"
    return ""


# ---------------------------------------------------------------------------
# ArchivesSpace staff-UI links
# ---------------------------------------------------------------------------

# Maps the agent-type segment of an API URI to its staff-UI path segment.
_AGENT_URL_SEGMENTS = {
    "people": "agent_person",
    "corporate_entities": "agent_corporate_entity",
    "families": "agent_family",
    "software": "agent_software",
}


def aspace_public_url(base_url: str, uri: str, resource_uri: str = "") -> str:
    """Build the ArchivesSpace staff-UI URL for a record's API uri.

    Collections:       /repositories/11/resources/561
                       -> {base}/resources/561#tree::resource_561
    Archival objects:  /repositories/11/archival_objects/105942 (+ resource_uri)
                       -> {base}/resources/561#tree::archival_object_105942
    Agents:            /agents/people/1745
                       -> {base}/agents/agent_person/1745

    Returns "" when the uri does not match a known shape, so callers can skip
    setting the property rather than storing a broken link.
    """
    base = base_url.rstrip("/")
    parts = uri.strip("/").split("/")

    if len(parts) == 4 and parts[0] == "repositories" and parts[2] == "resources":
        rid = parts[3]
        return f"{base}/resources/{rid}#tree::resource_{rid}"

    if len(parts) == 4 and parts[0] == "repositories" and parts[2] == "archival_objects":
        res_parts = resource_uri.strip("/").split("/")
        if len(res_parts) == 4 and res_parts[2] == "resources":
            return f"{base}/resources/{res_parts[3]}#tree::archival_object_{parts[3]}"
        return ""

    if len(parts) == 4 and parts[0] == "repositories" and parts[2] == "accessions":
        return f"{base}/accessions/{parts[3]}"

    if len(parts) == 3 and parts[0] == "agents":
        segment = _AGENT_URL_SEGMENTS.get(parts[1])
        if segment:
            return f"{base}/agents/{segment}/{parts[2]}"
        return ""

    return ""


# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------

def clean_props(props: dict[str, Any]) -> dict[str, Any]:
    """Remove None values and dropped fields; convert ArchivesSpace timestamp strings to datetime objects."""
    result: dict[str, Any] = {}
    for k, v in props.items():
        if v is None or k in _ASPACE_DROPPED_FIELDS:
            continue
        if k in _ASPACE_TIMESTAMP_FIELDS and isinstance(v, str):
            parsed = _parse_aspace_timestamp(v)
            result[k] = parsed if parsed is not None else v
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# ArchivesSpace audit-field renames
# ---------------------------------------------------------------------------

# "created_by" collides with the archival CREATED_BY relationship (the
# creator of the materials): questions like "collections created by <name>"
# must distinguish the staff account that entered the record from the
# archival creator. The graph therefore renames the ASpace audit fields;
# "user" in the name marks them as system-account fields. Keep the
# PROPERTY_SCHEMAS entries in extractors/metadata.py in sync.
_AUDIT_FIELD_RENAMES = {
    "created_by": "creating_user",
    "last_modified_by": "modifying_user",
}


def rename_audit_fields(props: dict[str, Any]) -> dict[str, Any]:
    """Rename ArchivesSpace audit fields to their graph names, in place."""
    for old, new in _AUDIT_FIELD_RENAMES.items():
        if old in props:
            props[new] = props.pop(old)
    return props


# ---------------------------------------------------------------------------
# Processor credits ("By: <name>" convention in processinfo notes)
# ---------------------------------------------------------------------------

# Matches the descriptive-practice convention "By: <name>" inside processing
# information notes. The colon is required: bare "by" appears constantly in
# prose ("removed by the creator") and must not be captured. The capture
# stops at sentence punctuation; a 60-char cap bounds prose bleed when the
# convention is followed loosely.
_PROCESSED_BY_RE = re.compile(r"\b[Bb]y:\s*([^.;\n]{2,60})")

# Separators between multiple credited processors in one "By:" line.
_PROCESSOR_SPLIT_RE = re.compile(r"\s+and\s+|\s*,\s*|\s*&\s*")


def extract_processed_by(processinfo_contents) -> list[str]:
    """Extract verbatim processor credits from processinfo note texts.

    Returns a de-duplicated list preserving first-seen order. Names are NOT
    normalized — identity cleanup (e.g. unifying 'staff' variants) belongs to
    metadata remediation, and verbatim values keep the property auditable
    against the source note.
    """
    seen: dict[str, None] = {}
    for content in processinfo_contents:
        for match in _PROCESSED_BY_RE.findall(content or ""):
            for name in _PROCESSOR_SPLIT_RE.split(match.strip()):
                name = name.strip().rstrip(",")
                if len(name) >= 2:
                    seen.setdefault(name, None)
    return list(seen)
