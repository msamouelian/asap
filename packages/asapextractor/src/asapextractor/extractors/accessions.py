"""Extractor: ArchivesSpace Accessions → Neo4j Accession nodes and related graph.

Enriches Accession stub nodes (created by ResourceExtractor and
DigitalObjectExtractor) with full property data, then creates:
  - Extent nodes (HAS_EXTENT)
  - Accession-to-Accession part relationships (HAS_PART, FORMS_PART_OF)
  - Accession-to-Collection links (Collection-[:RELATED_TO]->Accession)
  - Accession-to-Agent links (CREATED_BY, HAS_SUBJECT, SOURCE)

Must run after ResourceExtractor (Collection nodes must exist for resource
links) and AgentExtractor (Agent nodes should be fully populated).
"""

import logging
from typing import Any

from asapextractor import config
from asapextractor.extractors.base import BaseExtractor
from asapextractor.utils import (
    rename_audit_fields,
    aspace_public_url,
    extract_dates,
    extract_languages,
    agent_type_from_uri,
)

logger = logging.getLogger(__name__)


_EXCLUDED_FIELDS = frozenset({
    "lock_version",
    "is_slug_auto",
    "jsonmodel_type",
    "external_ids",
    "subjects",
    "linked_events",
    "external_documents",
    "rights_statements",
    "classifications",
    "metadata_rights_declarations",
    "component_links",
    # Handled separately
    "dates",
    "lang_materials",
    "extents",
    "deaccessions",
    "linked_agents",
    # Structural refs expressed as relationships
    "repository",
    "related_accessions",
    "related_resources",
    "component_links",
    "instances",
})

_DEACCESSION_EXCLUDED = frozenset({
    "lock_version",
    "jsonmodel_type",
    "repository",
    "extents",   # nested extents on deaccessions are not currently extracted
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


def _transform(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a raw accession record into a Neo4j-ready payload."""
    uri = raw.get("uri", "")
    if not uri:
        return None

    scalar: dict[str, Any] = rename_audit_fields({
        k: v for k, v in raw.items()
        if k not in _EXCLUDED_FIELDS and not isinstance(v, (dict, list))
    })
    dates = extract_dates(raw.get("dates") or [])
    languages = extract_languages(raw.get("lang_materials") or [])

    acc_data = {
        **scalar,
        **dates,
        "languages": languages,
        "aspace_url": aspace_public_url(config.ASPACE_PUBLIC_URL, uri),
    }

    extents = [
        {
            "extent_type": e.get("extent_type", ""),
            "number": e.get("number", ""),
            "portion": e.get("portion", ""),
            "container_summary": e.get("container_summary", ""),
        }
        for e in (raw.get("extents") or [])
    ]

    deaccessions = [
        _transform_deaccession(d)
        for d in (raw.get("deaccessions") or [])
    ]

    # related_accessions: part/whole relationships to other Accession nodes.
    # Each entry carries a relator ('has_part', 'forms_part_of') that becomes
    # the Neo4j relationship type.
    accession_parts = [
        {
            "uri": ra["ref"],
            "relator": ra.get("relator", "related"),
        }
        for ra in (raw.get("related_accessions") or [])
        if ra.get("ref")
    ]

    # related_resources: links to Collection (resource) nodes.
    resource_links = [
        {"uri": rr["ref"]}
        for rr in (raw.get("related_resources") or [])
        if rr.get("ref")
    ]

    # component_links: archival object components that describe this accession's materials.
    component_links = [
        {"uri": cl["ref"]}
        for cl in (raw.get("component_links") or [])
        if cl.get("ref")
    ]

    # linked_agents: creator/subject/source links.
    agent_links = [
        {
            "uri": la["ref"],
            "role": la.get("role", ""),
            "agent_type": agent_type_from_uri(la["ref"]),
        }
        for la in (raw.get("linked_agents") or [])
        if la.get("ref")
    ]

    return {
        "acc": acc_data,
        "extents": extents,
        "deaccessions": deaccessions,
        "accession_parts": accession_parts,
        "resource_links": resource_links,
        "component_links": component_links,
        "agent_links": agent_links,
    }


class AccessionExtractor(BaseExtractor):
    """Bulk-fetches all accession records for the repository and writes full
    data to Neo4j.

    Enriches Accession stub nodes created by earlier extractors with complete
    properties, then creates Extent nodes and all relationship edges.
    Must run after ResourceExtractor (Collection nodes) and AgentExtractor
    (Agent nodes).
    """

    _PAGE_SIZE = 100

    def extract(self) -> list[Any]:
        """Page through all accessions for the repository."""
        endpoint = f"repositories/{config.ASPACE_REPO_ID}/accessions"
        records: list[Any] = []
        page = 1
        while True:
            logger.debug(
                "AccessionExtractor: fetching page %d (page_size=%d).",
                page, self._PAGE_SIZE,
            )
            try:
                data = self.aspace.call_endpoint(
                    endpoint,
                    params={"page": page, "page_size": self._PAGE_SIZE},
                )
            except RuntimeError as exc:
                logger.error("AccessionExtractor: fetch failed on page %d: %s", page, exc)
                break

            results = data.get("results") or []
            records.extend(results)
            logger.info(
                "AccessionExtractor: page %d — %d records (total so far: %d).",
                page, len(results), len(records),
            )

            if data.get("last_page", page) <= page:
                break
            page += 1

        logger.info("AccessionExtractor: extracted %d accessions total.", len(records))
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
        """Enrich Accession nodes and create extents and relationship edges."""
        for i, payload in enumerate(records):
            if not i % 500:
                logger.info("AccessionExtractor: %d records processed.", i)

            acc_data = payload["acc"]
            uri = acc_data.get("uri")
            if not uri:
                continue

            # Enrich (or create) the Accession node with full properties.
            self.neo4j.merge_accession(acc_data)

            # Extents — replace_extents now handles Accession nodes correctly
            # because label_from_uri recognises /accessions/ URIs.
            self.neo4j.replace_extents(uri, payload["extents"])
            self.neo4j.replace_deaccessions(uri, payload["deaccessions"])

            # Accession-to-Accession part relationships.
            for part in payload["accession_parts"]:
                self.neo4j.merge_accession_stub(part["uri"])
                self.neo4j.link_accession_parts(uri, part["uri"], part["relator"])

            # Accession-to-Collection: creates Collection-[:RELATED_TO]->Accession,
            # which is the same direction as ResourceExtractor — MERGE is idempotent.
            for res in payload["resource_links"]:
                self.neo4j.link_accession(res["uri"], uri)

            # Accession-to-ArchivalObject component links.
            for cl in payload["component_links"]:
                self.neo4j.link_accession_component(uri, cl["uri"])

            # Agent links (CREATED_BY, HAS_SUBJECT, SOURCE).
            for al in payload["agent_links"]:
                self.neo4j.merge_agent_stub(al["uri"])
                self.neo4j.link_agent(uri, al["uri"], al["role"])

        logger.info(
            "AccessionExtractor: loaded %d accessions.",
            len(records),
        )
