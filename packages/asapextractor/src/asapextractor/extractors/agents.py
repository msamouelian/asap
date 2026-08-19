"""Extractor: ArchivesSpace Agents → Neo4j Agent nodes (scoped to repository 11)."""

import logging
from typing import Any

from asapextractor import config
from asapextractor.extractors.base import BaseExtractor
from asapextractor.utils import aspace_public_url, rename_audit_fields, transform_note

logger = logging.getLogger(__name__)

# Maps the plural path segment in an agent URI to a normalized agent_type string
_AGENT_TYPE_MAP: dict[str, str] = {
    "people": "person",
    "corporate_entities": "corporate_entity",
    "families": "family",
}

# Fields to exclude from the flat Agent node properties
_EXCLUDED_FIELDS = frozenset({
    "lock_version",
    "jsonmodel_type",
    "is_slug_auto",
    "import_previous_arks",
    "metadata_rights_declarations",
    "linked_events",
    # Handled separately as child nodes
    "names",
    "dates_of_existence",
    "notes",
    "related_agents",
    "agent_record_identifiers",
    "agent_maintenance_histories",
    # Internal refs / unused arrays
    "agent_contacts",
    "external_ids",
    "external_documents",
    "used_within_repositories",
    "used_within_published_repositories",
    # Excluded to prevent the raw API value ('agent_person') from overriding
    # the clean URI-derived value ('person') set explicitly in _transform_agent
    "agent_type",
})

# Fields to strip from raw name records before storing as AgentName properties
_NAME_EXCLUDED = frozenset({
    "lock_version",
    "jsonmodel_type",
    "sort_name_auto_generate",
    "created_by",
    "last_modified_by",
    "create_time",
    "system_mtime",
    "user_mtime",
    "use_dates",       # processed separately into use_date_* scalars
    "parallel_names",  # not currently extracted
})

# Fields to strip from agent_record_identifier entries
_IDENTIFIER_EXCLUDED = frozenset({
    "lock_version",
    "jsonmodel_type",
    "created_by",
    "last_modified_by",
    "create_time",
    "system_mtime",
    "user_mtime",
})

# Fields to strip from agent_maintenance_history entries
_HISTORY_EXCLUDED = frozenset({
    "lock_version",
    "jsonmodel_type",
    "created_by",
    "last_modified_by",
    "create_time",
    "system_mtime",
    "user_mtime",
})


def _parse_agent_uri(uri: str) -> tuple[str, str]:
    """Return (endpoint, agent_type) derived from an ArchivesSpace agent URI.

    e.g. '/agents/people/24021' -> ('agents/people/24021', 'person')
    """
    parts = uri.strip("/").split("/")
    # parts: ['agents', '<plural_type>', '<id>']
    agent_type = _AGENT_TYPE_MAP.get(parts[1], parts[1]) if len(parts) >= 2 else ""
    endpoint = "/".join(parts)
    return endpoint, agent_type


def _display_name(names: list[dict[str, Any]]) -> str:
    """Extract display / sort name from the names array."""
    if not names:
        return ""
    primary = names[0]
    return primary.get("sort_name") or primary.get("primary_name") or ""


def _existence_dates(dates_of_existence: list[dict[str, Any]]) -> dict[str, str]:
    """Flatten the first dates_of_existence entry into scalar properties.

    ArchivesSpace returns dates as a 'structured_date_label' with either:
      - 'structured_date_range': a date range with begin and end fields.
      - 'structured_date_single': a single point-in-time date.
    """
    if not dates_of_existence:
        return {}
    d = dates_of_existence[0]

    r = d.get("structured_date_range")
    if r:
        begin = r.get("begin_date_standardized") or r.get("begin_date_expression") or ""
        end = r.get("end_date_standardized") or r.get("end_date_expression") or ""
        begin_expr = r.get("begin_date_expression") or ""
        end_expr = r.get("end_date_expression") or ""
        expression = f"{begin_expr}-{end_expr}" if begin_expr and end_expr else begin_expr or end_expr
        return {"existence_begin": begin, "existence_end": end, "existence_expression": expression}

    s = d.get("structured_date_single")
    if s:
        point = s.get("date_standardized") or s.get("date_expression") or ""
        return {"existence_begin": point, "existence_end": "", "existence_expression": s.get("date_expression") or ""}

    return {"existence_begin": "", "existence_end": "", "existence_expression": ""}


def _transform_name(name: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw name record into Neo4j-ready AgentName properties.

    Retains all scalar name fields (sort_name, primary_name, rest_of_name,
    authorized, is_display_name, name_order, authority_id, source, dates,
    name_suffix, fuller_form, title, etc.) and derives use_date scalars from
    the first use_dates entry.
    """
    props: dict[str, Any] = {
        k: v for k, v in name.items()
        if k not in _NAME_EXCLUDED and not isinstance(v, (dict, list))
    }

    # Derive use_date scalars from the first use_dates structured date entry
    use_dates = name.get("use_dates") or []
    if use_dates:
        ud = _existence_dates(use_dates)
        props["use_date_begin"] = ud.get("existence_begin", "")
        props["use_date_end"] = ud.get("existence_end", "")
        props["use_date_expression"] = ud.get("existence_expression", "")

    return props


def _transform_identifier(ident: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw agent_record_identifier entry into Neo4j-ready properties."""
    return {
        k: v for k, v in ident.items()
        if k not in _IDENTIFIER_EXCLUDED and not isinstance(v, (dict, list))
    }


def _transform_maintenance_history(h: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw agent_maintenance_history entry into Neo4j-ready properties."""
    return {
        k: v for k, v in h.items()
        if k not in _HISTORY_EXCLUDED and not isinstance(v, (dict, list))
    }


def _transform_agent(
    raw: dict[str, Any], agent_type: str
) -> dict[str, Any] | None:
    """Convert a raw agent record into a structured dict ready for loading."""
    uri = raw.get("uri", "")
    if not uri:
        return None

    scalar: dict[str, Any] = rename_audit_fields({
        k: v for k, v in raw.items()
        if k not in _EXCLUDED_FIELDS and not isinstance(v, (dict, list))
    })

    agent_data = {
        "uri": uri,
        "agent_type": agent_type,
        "display_name": _display_name(raw.get("names") or []),
        "aspace_url": aspace_public_url(config.ASPACE_PUBLIC_URL, uri),
        **scalar,
        **_existence_dates(raw.get("dates_of_existence") or []),
    }

    notes = [
        n for raw_note in (raw.get("notes") or [])
        if (n := transform_note(raw_note)) is not None
    ]

    related = [
        {"target_uri": ra["ref"], "relator": ra.get("relator", "ASSOCIATIVE")}
        for ra in (raw.get("related_agents") or [])
        if ra.get("ref")
    ]

    names = [_transform_name(n) for n in (raw.get("names") or [])]

    identifiers = [
        _transform_identifier(i)
        for i in (raw.get("agent_record_identifiers") or [])
    ]

    histories = [
        _transform_maintenance_history(h)
        for h in (raw.get("agent_maintenance_histories") or [])
    ]

    return {
        "agent": agent_data,
        "notes": notes,
        "related_agents": related,
        "names": names,
        "identifiers": identifiers,
        "maintenance_histories": histories,
    }


class AgentExtractor(BaseExtractor):
    """Fetches full agent records for all agents linked to repository 11 resources.

    Rather than pulling all agents system-wide, this extractor reads the Agent
    stub nodes already created by ResourceExtractor (scoped to repo 11), fetches
    the full record for each one from ArchivesSpace, and merges the details into
    Neo4j.

    After the initial pass, an iterative BFS loop resolves stubs created by
    inter-agent relationships at arbitrary depth — agent A → agent B → agent C
    etc. — until no unfilled stubs remain.
    """

    _MAX_BFS_PASSES = 10

    def run(self) -> None:
        """Run the full agent extraction with iterative stub resolution."""
        # Initial pass: all agents directly linked to Collections / ArchivalObjects
        uris = self.extract()
        records = self.transform(uris)
        self.load(records)

        # BFS: each load() may create new stubs via inter-agent related_agents.
        # Keep fetching unfilled stubs until none remain (handles arbitrary depth).
        # Stubs that fail to fetch are marked fetch_failed and excluded from future
        # passes, preventing infinite loops caused by deleted ASpace agent records.
        # _MAX_BFS_PASSES is a hard safety cap for any unanticipated cycle.
        iteration = 1
        while iteration <= self._MAX_BFS_PASSES:
            stub_uris = self.neo4j.get_agent_stub_uris()
            if not stub_uris:
                break
            logger.info(
                "AgentExtractor: BFS pass %d — resolving %d unfilled stubs.",
                iteration,
                len(stub_uris),
            )
            records = self.transform(stub_uris)
            self.load(records)
            iteration += 1
        else:
            remaining = self.neo4j.get_agent_stub_uris()
            logger.warning(
                "AgentExtractor: BFS safety cap (%d passes) reached — %d stubs still unresolved.",
                self._MAX_BFS_PASSES,
                len(remaining),
            )

        logger.info("AgentExtractor: BFS complete after %d extra pass(es).", iteration - 1)

    def extract(self) -> list[Any]:
        """Return agent URIs for all agents linked to a Collection or ArchivalObject."""
        uris = self.neo4j.get_linked_agent_uris()
        logger.info("AgentExtractor: found %d linked agents to process.", len(uris))
        return uris

    def transform(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Fetch each agent's full record from ArchivesSpace and transform it."""
        records = []
        for i, uri in enumerate(raw):
            if not i % 100:
                logger.info("AgentExtractor: %d records processed.", i)

            endpoint, agent_type = _parse_agent_uri(uri)
            try:
                agent_raw = self.aspace.call_endpoint(endpoint)
            except RuntimeError:
                logger.warning(
                    "AgentExtractor: could not fetch %s — marking fetch_failed to prevent BFS loop.", uri
                )
                self.neo4j.mark_agent_fetch_failed(uri)
                continue

            transformed = _transform_agent(agent_raw, agent_type)
            if transformed is not None:
                records.append(transformed)

        return records

    def load(self, records: list[dict[str, Any]]) -> None:
        """Write agents, notes, names, identifiers, histories, and inter-agent relationships."""
        loaded = 0
        for rec in records:
            if not loaded % 100:
                logger.info("AgentExtractor: %d records loaded.", loaded)

            agent = rec["agent"]
            uri = agent["uri"]

            self.neo4j.merge_agent(agent)

            for note in rec["notes"]:
                self.neo4j.merge_note(note)
                self.neo4j.link_note(uri, note["persistent_id"])

            for rel in rec["related_agents"]:
                self.neo4j.merge_agent_stub(rel["target_uri"])
                self.neo4j.link_agents(uri, rel["target_uri"], rel["relator"])

            self.neo4j.replace_agent_names(uri, rec["names"])
            self.neo4j.replace_agent_identifiers(uri, rec["identifiers"])
            self.neo4j.replace_agent_maintenance_histories(uri, rec["maintenance_histories"])

            loaded += 1

        logger.info("AgentExtractor: loaded %d agents into Neo4j.", loaded)
