# Knowledge Graph Conventions

Design contract for the automated knowledge graph (KG) built by the
`kggenerator` package. The KG is *derived* data inferred by an LLM from the
text surface of the **extracted graph** (the authoritative ArchivesSpace →
Neo4j graph built by `asapextractor`). Both live in the single Neo4j
Community database; label scoping is the isolation mechanism.

Agreed 2026-08 (design discussion with M. Samouelian).

## Terminology

| Term | Meaning |
|---|---|
| extracted graph | Nodes/edges built by `asapextractor` from ArchivesSpace. Authoritative. |
| knowledge graph (KG) | `:Inferred*` nodes/edges built by `kggenerator` from unstructured text. Derived. |
| text surface | All narrative text of one collection: collection notes/title, AO titles/notes, linked-agent names and their notes. |
| artifact | Per-collection JSON extraction output persisted in Postgres between pipeline phases. |

## Isolation rules (hard invariants)

1. Every KG node carries the common label `:Inferred` **plus** one specific
   label: `:InferredAgent`, `:InferredPlace`, or `:InferredEvent`.
   (No "thing" type — entities that are not an agent, place, or event are
   deliberately omitted to keep the KG focused on connections between agents.)
2. `asapextractor` never deletes `:Inferred` nodes (`wipe_graph()` excludes
   them). `kggenerator` deletes **only** `:Inferred` nodes.
3. KG relationships connect only `:Inferred` nodes. There are **no hard
   relationships across the boundary** — an inferred agent references its
   extracted counterpart via the `extracted_agent_twin` property (the
   extracted Agent's `uri`, e.g. `/agents/people/4049`), which survives
   either job running without the other.
4. `kggenerator` never updates or deletes anything in the extracted graph,
   and (like every component) only ever reads from ArchivesSpace.

## Entity types

- **InferredAgent** — person, family, or corporate body (`agent_type`
  property, same vocabulary as extracted Agents).
- **InferredPlace** — named geographic location.
- **InferredEvent** — a discrete occurrence (see decision rule below).

## Event decision rule (applied in the extraction prompt, in order)

1. Is the fact the *duration of a relationship between two entities*
   ("Land was chairman of Polaroid from 1937")? → `date` property on that
   relationship edge. No event node.
2. Is it the *beginning or end of an entity's existence* (birth, death,
   founding, ceasing operations)? → `exists_from` / `exists_to` property on
   the entity node. (These also feed entity resolution.)
3. Otherwise, is it a *discrete occurrence worth recording* ("Siebert took a
   leave of absence in 1977", the 1968 Penn Central merger)? → an
   `InferredEvent` node with `RICO_HAS_OR_HAD_PARTICIPANT` edges to each
   participant. Single-participant events qualify.

## Relationship vocabulary

- Types come from a **curated subset of RiC-O 1.1 object properties**
  (see `packages/kggenerator/src/kggenerator/rico_relations.py`, generated
  from `ric/RiC-O_1-1_core.rdf` by
  `packages/kggenerator/scripts/generate_rico_relations.py`).
- Neo4j relationship type = `RICO_` + SCREAMING_SNAKE of the RiC property
  name (`rico:hasOrHadEmployer` → `RICO_HAS_OR_HAD_EMPLOYER`), with the
  canonical IRI kept in the `rico_uri` property.
- Every RiC pair is **materialized in both directions atomically**
  (`RICO_HAS_OR_HAD_MANAGER` and `RICO_IS_OR_WAS_MANAGER_OF`), created and
  deleted together, so LLM-generated Cypher is direction-proof. Symmetric
  properties (e.g. `hasOrHadCorrespondent`) get one edge each way with the
  same type.
- When no curated RiC relation fits: `INFERRED_GENERIC_RELATIONSHIP` with a
  free-text `nature` property. (RiC's own generic `isRelatedTo` is
  deliberately excluded from the menu so it doesn't swallow everything.)
- We adopt RiC's relationship *vocabulary* only — no RDF machinery. Dates
  are verbatim string properties, never Date nodes.

## Properties

Snake_case throughout (matches the extracted graph).

**All inferred nodes**: `id` (UUID, MERGE key), `create_time`,
`creating_user` = `"kggenerator"`, `kg_run_id`, `source_uri` (list of
ArchivesSpace URIs the entity was inferred from), `confidence`, `embedding`
(bge-small, for hybrid/semantic search).

**InferredAgent**: `display_name`, `title` (= display_name, for search-index
parity), `agent_type` (person | family | corporate_body), `alternate_names`
(list), `exists_from`, `exists_to` (verbatim strings, as archivists write
dates), `description` (one-line LLM summary), `extracted_agent_twin`
(nullable uri).

**InferredPlace**: `display_name`, `title`.

**InferredEvent**: `display_name`, `title`, `event_type` (free),
`date` (verbatim string).

**All inferred relationships**: `rico_uri` (null for generic), `source_uri`
(list), `quote` (short verbatim evidence snippet), `date` (verbatim,
optional), `confidence`, `kg_run_id`, `create_time`, `creating_user`.

## Provenance (fail-closed)

The LLM **never emits URIs**. Text-surface documents give every text unit an
integer index and carry the `source_uri` server-side; the LLM cites evidence
as indices; `kggenerator` maps indices → URIs programmatically and rejects
out-of-range indices. Notes have no URI of their own, so a note's text unit
carries the URI of its owning parent (collection, AO, or agent).

## Entity resolution posture

**Under-merge.** Two nodes for one person is an inconvenience; one node for
two people is a factual error alongside an authoritative graph. Resolution
runs at three levels: within-chunk (the LLM), within-collection
(deterministic + LLM tie-break), cross-collection (block → score → LLM
adjudication → cluster; contradictory existence dates veto a merge).
Collection date context is used for scoring; industry classification is not.
Inferred-vs-extracted agent matches are never merged — they set
`extracted_agent_twin` only.

## Pipeline phases and artifacts

```
extract   per collection: text surface → chunks → LLM → per-chunk graphs
          → within-collection merge → artifact row (Postgres kg_artifact)
resolve   cross-collection entity resolution over all artifacts → merged KG
          artifact + ER decision log
load      merged KG → Neo4j :Inferred nodes/edges, inverse edges, embeddings,
          twin matching, schema metadata (NodeSchema/RelationshipSchema rows
          labelled :Inferred — kggenerator documents its own schema)
```

Extraction (the expensive LLM phase) checkpoints per collection in
`kg_artifact`; `resolve` and `load` re-run cheaply from artifacts. The final
graph is built fresh from resolved artifacts — no in-Neo4j node rewiring.

## Pilot collections (hard-coded for initial development)

Exact `Collection.title` values (verified against the graph):

- Penn Central Transportation Corporation records (389 AOs)
- New York, New Haven, and Hartford Railroad Company records (564 AOs)
- Boston and Albany Railroad Company records (757 AOs)
- Boston and Albany Railroad Co. photograph album (56 AOs)
- Ware River Railroad record book (1 AO)
