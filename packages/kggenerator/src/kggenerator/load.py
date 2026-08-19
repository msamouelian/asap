"""Load phase: latest resolved knowledge graph → Neo4j :Inferred subgraph.

Invariants enforced here (docs/kg-conventions.md):
- every node carries :Inferred plus one specific label;
- deletion is scoped to :Inferred only — the extracted graph is never touched;
- relationships connect only :Inferred nodes; the bridge to extracted Agents
  is the extracted_agent_twin property, set by deterministic name matching
  with a uniqueness guard (ambiguity skips, never guesses);
- every RiC pair is materialized in both directions;
- kggenerator writes its own schema metadata, labelled :Inferred, so the
  extractor's wipe spares it and the KG wipe removes it.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from neo4j import GraphDatabase

from kggenerator import config
from kggenerator.resolve import norm_name, _tokens
from kggenerator.rico_relations import RELATIONS_BY_NAME, RICO_RELATIONS

logger = logging.getLogger(__name__)

GENERIC_TYPE = "INFERRED_GENERIC_RELATIONSHIP"
LABEL_BY_TYPE = {"agent": "InferredAgent", "place": "InferredPlace",
                 "event": "InferredEvent"}


class Neo4jLoader:
    def __init__(self) -> None:
        self._driver = GraphDatabase.driver(
            config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
        )

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "Neo4jLoader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _run(self, cypher: str, **params: Any):
        with self._driver.session() as session:
            return session.run(cypher, **params).data()

    # ------------------------------------------------------------------

    def wipe_inferred(self) -> None:
        """Delete ONLY the knowledge graph — everything labelled :Inferred."""
        result = self._run("""
            CALL apoc.periodic.iterate(
              "MATCH (n:Inferred) RETURN n",
              "DETACH DELETE n",
              {batchSize: 1000}
            ) YIELD total, failedBatches, errorMessages
            RETURN total, failedBatches, errorMessages
        """)
        row = result[0]
        if row["failedBatches"]:
            raise RuntimeError(f"Inferred wipe failed: {row['errorMessages']}")
        logger.info("Wiped %d :Inferred nodes.", row["total"])

    def ensure_indexes(self) -> None:
        self._run("CREATE CONSTRAINT inferred_id IF NOT EXISTS "
                  "FOR (n:Inferred) REQUIRE n.id IS UNIQUE")
        for label, index in [("InferredAgent", "inferred_agent_embedding"),
                             ("InferredPlace", "inferred_place_embedding"),
                             ("InferredEvent", "inferred_event_embedding")]:
            self._run(
                f"CREATE VECTOR INDEX {index} IF NOT EXISTS "
                f"FOR (n:{label}) ON n.embedding "
                "OPTIONS { indexConfig: { "
                f"`vector.dimensions`: {config.EMBEDDING_DIMENSIONS}, "
                "`vector.similarity_function`: 'cosine' } }"
            )
        # Drop+recreate so the definition always matches the code (same
        # rationale as the extractor's free_text_index — but this one is
        # kggenerator's own and rebuilds with every load).
        self._run("DROP INDEX inferred_fulltext IF EXISTS")
        self._run(
            "CREATE FULLTEXT INDEX inferred_fulltext "
            "FOR (n:InferredAgent|InferredPlace|InferredEvent) "
            "ON EACH [n.display_name, n.title, n.alternate_names, n.description] "
            "OPTIONS { indexConfig: { `fulltext.analyzer`: 'english' } }"
        )
        logger.info("Inferred constraints and indexes ensured.")

    # ------------------------------------------------------------------

    def load_entities(self, graph: dict[str, Any], run_id: str) -> dict[str, str]:
        """Create Inferred nodes; returns canonical-id -> node-id map."""
        now = datetime.now(timezone.utc).isoformat()
        node_id_of: dict[str, str] = {}
        rows_by_label: dict[str, list[dict[str, Any]]] = {}
        for e in graph["entities"]:
            nid = uuid.uuid4().hex
            node_id_of[e["id"]] = nid
            props: dict[str, Any] = {
                "id": nid,
                "display_name": e["name"],
                "title": e["name"],
                "alternate_names": e.get("alternate_names") or [],
                "source_uri": e.get("source_uri") or [],
                "confidence": float(e.get("confidence") or 0.5),
                "kg_run_id": run_id,
                "create_time": now,
                "creating_user": "kggenerator",
                "member_collection_uris": sorted(
                    {m["collection_uri"] for m in e.get("members", [])}),
                "member_names": sorted(
                    {m["name"] for m in e.get("members", [])}),
            }
            for field in ("agent_type", "description", "exists_from",
                          "exists_to", "event_type", "date"):
                if str(e.get(field) or "").strip():
                    props[field] = e[field]
            rows_by_label.setdefault(LABEL_BY_TYPE[e["type"]], []).append(props)

        for label, rows in rows_by_label.items():
            self._run(
                f"UNWIND $rows AS row CREATE (n:Inferred:{label}) SET n = row",
                rows=rows,
            )
            logger.info("Created %d %s nodes.", len(rows), label)
        return node_id_of

    def load_relations(
        self, graph: dict[str, Any], node_id_of: dict[str, str], run_id: str
    ) -> int:
        """Create every relation in BOTH directions (atomically per pair)."""
        now = datetime.now(timezone.utc).isoformat()
        edges: list[dict[str, Any]] = []
        for r in graph["relations"]:
            base = {
                "source_uri": r.get("source_uri") or [],
                "quote": (r.get("quotes") or [""])[0],
                "quotes": r.get("quotes") or [],
                "confidence": float(r.get("confidence") or 0.5),
                "kg_run_id": run_id,
                "create_time": now,
                "creating_user": "kggenerator",
            }
            for field in ("date", "nature"):
                if str(r.get(field) or "").strip():
                    base[field] = r[field]
            spec = RELATIONS_BY_NAME.get(r["type"])
            if spec:
                edges.append({"from": node_id_of[r["from"]],
                              "to": node_id_of[r["to"]],
                              "type": spec["neo4j_type"],
                              "props": {**base, "rico_uri": spec["uri"]}})
                edges.append({"from": node_id_of[r["to"]],
                              "to": node_id_of[r["from"]],
                              "type": spec["inverse_neo4j_type"],
                              "props": {**base, "rico_uri": spec["inverse_uri"]}})
            else:  # generic fallback — same type both directions
                for src, dst in [(r["from"], r["to"]), (r["to"], r["from"])]:
                    edges.append({"from": node_id_of[src],
                                  "to": node_id_of[dst],
                                  "type": GENERIC_TYPE, "props": base})
        self._run("""
            UNWIND $edges AS e
            MATCH (a:Inferred {id: e.from}), (b:Inferred {id: e.to})
            CALL apoc.create.relationship(a, e.type, e.props, b) YIELD rel
            RETURN count(rel)
        """, edges=edges)
        logger.info("Created %d directed relationships (%d logical relations).",
                    len(edges), len(graph["relations"]))
        return len(edges)

    # ------------------------------------------------------------------

    def set_embeddings(self, graph: dict[str, Any],
                       node_id_of: dict[str, str]) -> None:
        """Embed name + alternates + description via vLLM (same model as the
        extractor's note chunks, so vectors are comparable in hybrid search)."""
        texts, ids = [], []
        for e in graph["entities"]:
            parts = [e["name"], *(e.get("alternate_names") or [])]
            if e.get("description"):
                parts.append(e["description"])
            texts.append(". ".join(parts))
            ids.append(node_id_of[e["id"]])

        client = httpx.Client(base_url=config.VLLM_BASE_URL, timeout=120)
        rows: list[dict[str, Any]] = []
        for i in range(0, len(texts), config.EMBEDDING_BATCH_SIZE):
            batch = texts[i:i + config.EMBEDDING_BATCH_SIZE]
            resp = client.post("/embeddings", json={
                "model": config.EMBEDDING_MODEL, "input": batch})
            resp.raise_for_status()
            for j, item in enumerate(resp.json()["data"]):
                rows.append({"id": ids[i + j], "embedding": item["embedding"]})
        client.close()

        self._run("""
            UNWIND $rows AS row
            MATCH (n:Inferred {id: row.id})
            CALL db.create.setNodeVectorProperty(n, 'embedding', row.embedding)
            RETURN count(n)
        """, rows=rows)
        logger.info("Set embeddings on %d nodes.", len(rows))

    # ------------------------------------------------------------------

    def match_twins(self, graph: dict[str, Any],
                    node_id_of: dict[str, str]) -> int:
        """Set extracted_agent_twin on inferred agents that deterministically
        match an extracted Agent by name. Exact normalized equality first,
        then token-subset (extracted display names carry expansions and life
        dates: 'Land, Edwin H. (Edwin Herbert), 1909-1991'). A name matching
        MORE than one extracted agent is skipped — never guessed."""
        extracted = self._run(
            "MATCH (a:Agent) WHERE a.display_name IS NOT NULL "
            "RETURN a.uri AS uri, a.display_name AS name")
        by_norm: dict[str, list[str]] = {}
        by_token: dict[frozenset, list[str]] = {}
        for a in extracted:
            by_norm.setdefault(norm_name(a["name"]), []).append(a["uri"])
            by_token.setdefault(frozenset(_tokens(a["name"])), []).append(a["uri"])

        updates, ambiguous = [], 0
        for e in graph["entities"]:
            if e["type"] != "agent":
                continue
            candidates: set[str] = set()
            for name in [e["name"], *(e.get("alternate_names") or [])]:
                candidates.update(by_norm.get(norm_name(name), []))
            if not candidates:
                for name in [e["name"], *(e.get("alternate_names") or [])]:
                    toks = _tokens(name)
                    if len(toks) < 2:
                        continue
                    for ext_toks, uris in by_token.items():
                        if toks <= ext_toks:
                            candidates.update(uris)
            if len(candidates) == 1:
                updates.append({"id": node_id_of[e["id"]],
                                "twin": candidates.pop()})
            elif len(candidates) > 1:
                ambiguous += 1
                logger.info("Twin ambiguous for %r (%d extracted candidates) "
                            "— skipped.", e["name"], len(candidates))
        if updates:
            self._run("""
                UNWIND $rows AS row
                MATCH (n:InferredAgent {id: row.id})
                SET n.extracted_agent_twin = row.twin
            """, rows=updates)
        logger.info("Twin matching: %d matched, %d ambiguous (skipped).",
                    len(updates), ambiguous)
        return len(updates)

    # ------------------------------------------------------------------

    def write_schema_metadata(self, used_relation_names: set[str]) -> None:
        """Document the Inferred subgraph in the same NodeSchema /
        RelationshipSchema / PropertySchema meta-graph the chat agent reads.
        Every schema node written here carries :Inferred so ownership follows
        the data: the extractor wipe spares it, the KG wipe removes it."""
        node_schemas = [
            {"label": "InferredAgent", "description": (
                "A person, family, or corporate body INFERRED BY AI (kggenerator) from "
                "the narrative text of finding aids — notes, titles, and display names. "
                "Part of the derived knowledge graph, NOT authoritative archival "
                "description: every node carries source_uri (the ArchivesSpace records "
                "it was inferred from) and confidence. Where the same real-world agent "
                "also exists as an archivist-created Agent node, extracted_agent_twin "
                "holds that Agent's uri — follow it to reach authoritative records. "
                "Inferred nodes connect ONLY to other Inferred nodes.")},
            {"label": "InferredPlace", "description": (
                "A named geographic location inferred by AI from finding-aid text. "
                "Derived data with source_uri evidence; connects only to Inferred nodes.")},
            {"label": "InferredEvent", "description": (
                "A discrete occurrence (merger, bankruptcy, lawsuit, disaster, lease) "
                "inferred by AI from finding-aid text, with a verbatim date string and "
                "RICO_HAS_OR_HAD_PARTICIPANT edges to the agents involved. Derived data "
                "with source_uri evidence; connects only to Inferred nodes.")},
        ]
        rel_schemas = [{
            "type": GENERIC_TYPE,
            "description": (
                "AI-inferred relationship between Inferred entities that fits no "
                "RiC-O relation; the nature property describes the connection in "
                "free text. Both directions are materialized."),
        }]
        for spec in RICO_RELATIONS:
            if spec["name"] not in used_relation_names:
                continue
            rel_schemas.append({
                "type": spec["neo4j_type"],
                "description": (
                    f"AI-inferred relationship (Records in Contexts rico:{spec['name']}). "
                    f"{spec['hint']} {spec['definition']} "
                    f"Direction: (A)-[:{spec['neo4j_type']}]->(B). The inverse edge "
                    f"{spec['inverse_neo4j_type']} is always materialized too, so either "
                    "direction can be queried. Connects Inferred nodes only; carries "
                    "source_uri evidence, a verbatim quote, and confidence."),
            })
        prop_schemas = [
            {"id": "InferredAgent.extracted_agent_twin", "description": (
                "uri of the archivist-created Agent node this inferred agent duplicates "
                "(e.g. '/agents/people/4049'), set only when the match is unambiguous. "
                "Join via MATCH (i:InferredAgent), (a:Agent {uri: i.extracted_agent_twin}) "
                "to combine inferred context with authoritative records. Absent when no "
                "extracted counterpart exists.")},
            {"id": "InferredAgent.source_uri", "description": (
                "List of ArchivesSpace record URIs whose text this entity was inferred "
                "from — its evidence trail. Present on all Inferred nodes and "
                "relationships. Notes have no URI, so a note's evidence carries the URI "
                "of its owning record (collection, archival object, or agent).")},
            {"id": "InferredAgent.exists_from", "description": (
                "Verbatim year/date the text gives for the start of the agent's "
                "existence (birth, incorporation). String, exactly as written; may "
                "legitimately differ between sources (charter vs opening date). "
                "exists_to is the counterpart for death/dissolution.")},
            {"id": "InferredAgent.member_collection_uris", "description": (
                "URIs of the collections whose text mentioned this entity — useful for "
                "'which collections hold material about X' when combined with "
                "source_uri. member_names lists the name variants seen per source.")},
        ]
        for item in node_schemas:
            self._run("MERGE (n:NodeSchema {label: $label}) "
                      "SET n.description = $description, n:Inferred",
                      label=item["label"], description=item["description"])
        for item in rel_schemas:
            self._run("MERGE (r:RelationshipSchema {type: $type}) "
                      "SET r.description = $description, r:Inferred",
                      type=item["type"], description=item["description"])
        for item in prop_schemas:
            self._run("MERGE (p:PropertySchema {id: $id}) "
                      "SET p.description = $description, p:Inferred",
                      id=item["id"], description=item["description"])
        logger.info("Wrote schema metadata: %d node, %d relationship, %d "
                    "property entries (all :Inferred).",
                    len(node_schemas), len(rel_schemas), len(prop_schemas))


def load_resolution(resolution: dict[str, Any]) -> dict[str, int]:
    """Full load: wipe :Inferred, create nodes/edges, embed, twin, document."""
    graph, run_id = resolution["graph"], resolution["run_id"]
    with Neo4jLoader() as loader:
        loader.wipe_inferred()
        loader.ensure_indexes()
        node_id_of = loader.load_entities(graph, run_id)
        n_edges = loader.load_relations(graph, node_id_of, run_id)
        loader.set_embeddings(graph, node_id_of)
        n_twins = loader.match_twins(graph, node_id_of)
        used = {r["type"] for r in graph["relations"]} & set(RELATIONS_BY_NAME)
        loader.write_schema_metadata(used)
    return {"nodes": len(node_id_of), "directed_edges": n_edges,
            "twins": n_twins}
