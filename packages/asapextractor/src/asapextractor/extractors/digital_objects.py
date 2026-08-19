"""Extractor: ArchivesSpace DigitalObjects → Neo4j DigitalObject + FileVersion nodes."""

import logging
from typing import Any

from asapextractor import config
from asapextractor.extractors.base import BaseExtractor
from asapextractor.utils import (
    rename_audit_fields,
    extract_dates,
    extract_languages,
    transform_note,
    agent_type_from_uri,
)

logger = logging.getLogger(__name__)


_EXCLUDED_FIELDS = frozenset({
    "lock_version",
    "jsonmodel_type",
    "is_slug_auto",
    "metadata_rights_declarations",
    "linked_events",
    "subjects",
    "external_ids",
    "external_documents",
    "rights_statements",
    "classifications",
    "import_previous_arks",
    # Handled separately
    "notes",
    "dates",
    "lang_materials",
    "linked_agents",
    "file_versions",
    # Structural refs (expressed as relationships, not properties)
    "repository",
    "tree",
    "collection",
})

# Fields to strip from raw file_version dicts before storing as properties
_FV_EXCLUDED_FIELDS = frozenset({
    "lock_version",
    "jsonmodel_type",
    "created_by",
    "last_modified_by",
    "create_time",
    "system_mtime",
    "user_mtime",
})


def _transform_file_version(fv: dict[str, Any]) -> dict[str, Any] | None:
    """Return Neo4j-ready properties for a file_version entry, or None if no file_uri."""
    file_uri = fv.get("file_uri", "")
    if not file_uri:
        return None
    return {
        k: v for k, v in fv.items()
        if k not in _FV_EXCLUDED_FIELDS and not isinstance(v, (dict, list))
    }


def _transform(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a raw digital object record into a Neo4j-ready payload."""
    uri = raw.get("uri", "")
    if not uri:
        return None

    scalar: dict[str, Any] = rename_audit_fields({
        k: v for k, v in raw.items()
        if k not in _EXCLUDED_FIELDS and not isinstance(v, (dict, list))
    })
    dates = extract_dates(raw.get("dates") or [])
    languages = extract_languages(raw.get("lang_materials") or [])

    do_data = {**scalar, **dates, "languages": languages}

    notes = [
        n for raw_note in (raw.get("notes") or [])
        if (n := transform_note(raw_note)) is not None
    ]

    extents = [
        {
            "extent_type": e.get("extent_type", ""),
            "number": e.get("number", ""),
            "portion": e.get("portion", ""),
            "container_summary": e.get("container_summary", ""),
        }
        for e in (raw.get("extents") or [])
    ]

    accession_links = [
        {"uri": li["ref"]}
        for li in (raw.get("linked_instances") or [])
        if li.get("ref") and "/accessions/" in li["ref"]
    ]

    agent_links = [
        {
            "uri": la["ref"],
            "role": la.get("role", ""),
            "agent_type": agent_type_from_uri(la["ref"]),
        }
        for la in (raw.get("linked_agents") or [])
        if la.get("ref")
    ]

    file_versions = [
        fv for raw_fv in (raw.get("file_versions") or [])
        if (fv := _transform_file_version(raw_fv)) is not None
    ]

    return {
        "do": do_data,
        "notes": notes,
        "extents": extents,
        "accession_links": accession_links,
        "agent_links": agent_links,
        "file_versions": file_versions,
    }


class DigitalObjectExtractor(BaseExtractor):
    """Bulk-fetches all digital objects for the repository and writes full
    record data to Neo4j.

    Enriches DigitalObject stub nodes created by ArchivalObjectExtractor with
    complete properties, then creates FileVersion nodes and agent links.
    Must run after ArchivalObjectExtractor so that DigitalObject stubs and
    their HAS_DIGITAL_OBJECT edges already exist.
    """

    _PAGE_SIZE = 250  # ArchivesSpace silently caps page_size at 250

    def extract(self) -> list[Any]:
        """Page through all digital objects for the repository."""
        endpoint = f"repositories/{config.ASPACE_REPO_ID}/digital_objects"
        records: list[Any] = []
        page = 1
        while True:
            logger.debug(
                "DigitalObjectExtractor: fetching page %d (page_size=%d).",
                page, self._PAGE_SIZE,
            )
            try:
                data = self.aspace.call_endpoint(
                    endpoint,
                    params={"page": page, "page_size": self._PAGE_SIZE},
                )
            except RuntimeError as exc:
                logger.error("DigitalObjectExtractor: fetch failed on page %d: %s", page, exc)
                break

            results = data.get("results") or []
            records.extend(results)
            logger.info(
                "DigitalObjectExtractor: page %d — %d records (total so far: %d).",
                page, len(results), len(records),
            )

            if data.get("last_page", page) <= page:
                break
            page += 1

        logger.info("DigitalObjectExtractor: extracted %d digital objects total.", len(records))
        return records

    def transform(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Transform raw records into Neo4j-ready payloads."""
        results = []
        for r in raw:
            t = _transform(r)
            if t is not None:
                results.append(t)
        return results

    def load(self, records: list[dict[str, Any]]) -> None:
        """Enrich DigitalObject nodes and create FileVersion nodes and agent links."""
        file_versions_created = 0
        for i, payload in enumerate(records):
            if not i % 1000:
                logger.info("DigitalObjectExtractor: %d records processed.", i)

            do_data = payload["do"]
            uri = do_data.get("uri")
            if not uri:
                continue

            self.neo4j.merge_digital_object(do_data)

            for note in payload["notes"]:
                self.neo4j.merge_note(note)
                self.neo4j.link_note(uri, note["persistent_id"])

            self.neo4j.replace_extents(uri, payload["extents"])

            for acc in payload["accession_links"]:
                self.neo4j.merge_accession_stub(acc["uri"])
                self.neo4j.link_digital_object_accession(uri, acc["uri"])

            for al in payload["agent_links"]:
                self.neo4j.merge_agent_stub(al["uri"])
                self.neo4j.link_agent(uri, al["uri"], al["role"])

            for fv in payload["file_versions"]:
                self.neo4j.merge_file_version(uri, fv)
                file_versions_created += 1

        logger.info(
            "DigitalObjectExtractor: loaded %d digital objects, %d file versions.",
            len(records), file_versions_created,
        )
