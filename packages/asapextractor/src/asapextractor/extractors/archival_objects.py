"""Extractor: bulk ArchivesSpace archival objects → full Neo4j ArchivalObject nodes."""

import logging
from typing import Any

from asapextractor import config
from asapextractor.extractors.base import BaseExtractor
from asapextractor.utils import (
    rename_audit_fields,
    aspace_public_url,
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
    "import_previous_arks",
    "metadata_rights_declarations",
    "linked_events",
    # Handled separately
    "notes",
    "extents",
    "lang_materials",
    "dates",
    "linked_agents",
    # Internal refs — stored as parent_uri / resource_uri instead
    "resource",
    "parent",
    "repository",
    "external_ids",
    "external_documents",
    "rights_statements",
    "subjects",
    "classifications",
})


def _transform(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a full archival object record into a Neo4j-ready payload."""
    scalar: dict[str, Any] = rename_audit_fields({
        k: v for k, v in raw.items()
        if k not in _EXCLUDED_FIELDS and not isinstance(v, (dict, list))
    })
    dates = extract_dates(raw.get("dates") or [])
    languages = extract_languages(raw.get("lang_materials") or [])

    ao_data = {
        **scalar,
        **dates,
        "languages": languages,
        # Staff-UI link needs the owning resource's id alongside the AO's own.
        "aspace_url": aspace_public_url(
            config.ASPACE_PUBLIC_URL,
            raw.get("uri", ""),
            resource_uri=(raw.get("resource") or {}).get("ref", ""),
        ),
    }

    # Determine the parent URI: immediate parent AO, or the resource (collection)
    parent_uri: str = (
        (raw.get("parent") or {}).get("ref")
        or (raw.get("resource") or {}).get("ref")
        or ""
    )

    notes = [
        n for raw_note in (raw.get("notes") or [])
        if (n := transform_note(raw_note)) is not None
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

    # Split instances into digital object refs (become graph edges) and
    # container instance types (stored as a property on the AO node).
    digital_object_refs: list[str] = []
    container_instance_types: list[str] = []
    for inst in (raw.get("instances") or []):
        inst_type = inst.get("instance_type", "")
        if inst_type == "digital_object":
            do_ref = (inst.get("digital_object") or {}).get("ref", "")
            if do_ref:
                digital_object_refs.append(do_ref)
        elif inst_type:
            container_instance_types.append(inst_type)

    if container_instance_types:
        ao_data["container_instance_types"] = container_instance_types

    return {
        "ao": ao_data,
        "parent_uri": parent_uri,
        "notes": notes,
        "agent_links": agent_links,
        "digital_object_refs": digital_object_refs,
    }


class ArchivalObjectExtractor(BaseExtractor):
    """Bulk-fetches all archival objects for the repository and writes full
    record data to Neo4j.

    Runs in two passes:
      Pass 1 — upsert all ArchivalObject nodes with full record data (notes,
               dates, languages, agent links).
      Pass 2 — create all HAS_PART relationships using each record's parent.ref
               (or resource.ref for root-level objects).  Running this after
               pass 1 ensures both endpoints of every relationship already exist,
               regardless of the order records were returned by the API.
    """

    _PAGE_SIZE = 250  # ArchivesSpace silently caps page_size at 250

    # ------------------------------------------------------------------
    # BaseExtractor interface
    # ------------------------------------------------------------------

    def extract(self) -> list[Any]:
        """Page through all archival objects for the repository and return raw records."""
        endpoint = f"repositories/{config.ASPACE_REPO_ID}/archival_objects"
        records: list[Any] = []
        page = 1
        while True:
            logger.debug(
                "ArchivalObjectExtractor: fetching page %d (page_size=%d).",
                page, self._PAGE_SIZE,
            )
            try:
                data = self.aspace.call_endpoint(
                    endpoint,
                    params={"page": page, "page_size": self._PAGE_SIZE},
                )
            except RuntimeError as exc:
                logger.error("ArchivalObjectExtractor: fetch failed on page %d: %s", page, exc)
                break

            results = data.get("results") or []
            records.extend(results)
            logger.info(
                "ArchivalObjectExtractor: page %d — %d records (total so far: %d).",
                page, len(results), len(records),
            )

            if data.get("last_page", page) <= page:
                break
            page += 1

        logger.info("ArchivalObjectExtractor: extracted %d archival objects total.", len(records))
        return records

    def transform(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Transform raw records into Neo4j-ready payloads."""
        return [_transform(r) for r in raw]

    def load(self, records: list[dict[str, Any]]) -> None:
        """Two-pass load: nodes first, then HAS_PART relationships."""

        # ------------------------------------------------------------------
        # Pass 1: upsert all ArchivalObject nodes, notes, and agent links
        # ------------------------------------------------------------------
        for i, payload in enumerate(records):
            if not i % 1000:
                logger.info("ArchivalObjectExtractor: pass 1 — %d records processed.", i)

            ao_data = payload["ao"]
            uri = ao_data.get("uri")
            if not uri:
                continue

            self.neo4j.merge_archival_object(ao_data)

            for note in payload["notes"]:
                self.neo4j.merge_note(note)
                self.neo4j.link_note(uri, note["persistent_id"])

            for al in payload["agent_links"]:
                self.neo4j.merge_agent_stub(al["uri"])
                self.neo4j.link_agent(uri, al["uri"], al["role"])

            for do_uri in payload["digital_object_refs"]:
                self.neo4j.merge_digital_object_stub(do_uri)

        logger.info(
            "ArchivalObjectExtractor: pass 1 complete (%d records). "
            "Starting pass 2 (HAS_PART + HAS_DIGITAL_OBJECT links)...",
            len(records),
        )

        # ------------------------------------------------------------------
        # Pass 2: create HAS_PART and HAS_DIGITAL_OBJECT links
        # ------------------------------------------------------------------
        part_links = 0
        do_links = 0
        for payload in records:
            uri = payload["ao"].get("uri")
            parent_uri = payload.get("parent_uri")
            if uri and parent_uri:
                self.neo4j.link_archival_object(parent_uri, uri)
                part_links += 1
            for do_uri in payload["digital_object_refs"]:
                self.neo4j.link_digital_object(uri, do_uri)
                do_links += 1

        logger.info(
            "ArchivalObjectExtractor: pass 2 complete — %d HAS_PART links, "
            "%d HAS_DIGITAL_OBJECT links.",
            part_links, do_links,
        )
