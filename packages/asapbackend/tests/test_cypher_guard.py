"""
Cypher guard: policy for LLM- and user-supplied Cypher.

Unit tests drive the two layers directly. The plan fixtures mirror real
EXPLAIN output captured from Neo4j 2026.05 on 2026-10-09 (operator names,
"Details" strings). An integration test at the bottom runs the same corpus
through a live server when NEO4J_URI is set, so the fixtures cannot
silently drift from the planner.
"""

from __future__ import annotations

import os

import pytest

from asapbackend.tools import cypher_guard as g
from asapbackend.tools.cypher_guard import CypherRejected, analyze_plan, lexical_check


# ── Plan fixtures (shape: driver summary.plan) ──────────────────────────────

def op(otype: str, details: str | None = None, *children: dict) -> dict:
    node: dict = {"operatorType": f"{otype}@neo4j", "args": {}, "children": list(children)}
    if details is not None:
        node["args"]["Details"] = details
    return node


def produce(*children: dict) -> dict:
    return op("ProduceResults", "x", *children)


PLANS = {
    "legit collection":  ("r", produce(op("Projection", "c.title AS `c.title`",
                                       op("Limit", "5", op("NodeByLabelScan", "c:Collection"))))),
    "legit fulltext":    ("r", produce(op("Limit", "5", op("ProcedureCall",
                                       "db.index.fulltext.queryNodes($autostring_0, $q) :: (node :: NODE, score :: FLOAT)")))),
    "legit vector":      ("r", produce(op("ProcedureCall",
                                       "db.index.vector.queryNodes($autostring_0, $autoint_1, $v) :: (node :: NODE, score :: FLOAT)"))),
    "legit embed fn":    ("r", produce(op("Projection", "ai.text.embed($autostring_0, $autostring_1) AS v"))),
    "legit path":        ("r", produce(op("EagerAggregation", "labels(x) AS `labels(x)`, count(*) AS `count(*)`",
                                       op("VarLengthExpand(All)", "(c)-[:HAS_PART*..3]->(x)",
                                          op("NodeByLabelScan", "c:Collection"))))),
    "legit backticked":  ("r", produce(op("NodeByLabelScan", "n:`Archival Object`"))),
    "legit rel scan":    ("r", produce(op("DirectedRelationshipTypeScan", "(a)-[:HAS_PART]->(b)"))),
    "legit note chunks": ("r", produce(op("Projection", "c.text AS `c.text`",
                                       op("Expand(All)", "(n)-[:HAS_CHUNK]->(c)", op("NodeByLabelScan", "n:Note"))))),
    "legit note chunk count": ("r", produce(op("EagerAggregation", "count(c) AS n",
                                       op("Expand(All)", "(n)-[:HAS_CHUNK]->(c)", op("NodeByLabelScan", "n:IndexableNote"))))),

    "doc label scan":    ("r", produce(op("Expand(All)", "(d)-[:HAS_CHUNK]->(ch)",
                                       op("Filter", "dc:DocumentCollection",
                                          op("DirectedRelationshipTypeScan", "(dc)-[:CONTAINS]->(d)"))))),
    "unlabeled scan":    ("r", produce(op("Filter", "cache[n.text] IS NOT NULL", op("AllNodesScan", "n")))),
    "label in WHERE":    ("r", produce(op("NodeByLabelScan", "n:Document"))),
    "label expr":        ("r", produce(op("UnionNodeByLabelsScan", "c:Collection|DocumentChunk"))),
    "exists subquery":   ("r", produce(op("SemiApply", None,
                                       op("NodeByLabelScan", "c:Collection"),
                                       op("NodeByLabelScan", "x:DocumentChunk")))),
    "count subquery":    ("r", produce(op("NodeCountFromCountStore", "count( (:DocumentChunk) ) AS n"))),
    "dynamic label":     ("r", produce(op("DynamicLabelNodeLookup", "n:$all($autostring_0)"))),
    "labels() filter":   ("r", produce(op("Filter", "any(l IN labels(n) WHERE l STARTS WITH $autostring_0)",
                                       op("AllNodesScan", "n")))),
    "apoc.load.json":    ("r", produce(op("ProcedureCall", "apoc.load.json($autostring_0) :: (value :: MAP)"))),
    "load csv":          ("r", produce(op("LoadCSV", "row"))),
    "db.labels":         ("r", produce(op("ProcedureCall", "db.labels() :: (label :: STRING)"))),
    "db.schema":         ("r", produce(op("ProcedureCall", "db.schema.visualization() :: (nodes :: LIST<NODE>)"))),
    "apoc function":     ("r", produce(op("Projection", "apoc.text.join([x], $autostring_0) AS j", op("NodeByLabelScan", "c:Collection")))),
    "write":             ("rw", produce(op("Create", "(n:Foo)"))),
    "delete":            ("w", produce(op("Delete", "n", op("NodeByLabelScan", "n:Collection")))),
    "schema write":      ("s", produce(op("CreateIndex", "INDEX FOR (n:Collection) ON (n.title)"))),
    "union with docs":   ("r", produce(op("Union", None,
                                       op("NodeByLabelScan", "c:Collection"),
                                       op("NodeByLabelScan", "d:Document")))),
    "doc rel only":      ("r", produce(op("DirectedRelationshipTypeScan", "(a)-[:CONTAINS]->(b)"))),
    "doc chunk scan":    ("r", produce(op("DirectedRelationshipTypeScan", "(a)-[:HAS_DOCUMENT_CHUNK]->(b)"))),
    "doc chunk expand":  ("r", produce(op("Expand(All)", "(d)-[:HAS_DOCUMENT_CHUNK]->(c)", op("NodeByLabelScan", "d:Document")))),
    "legit has_chunk count": ("r", produce(op("RelationshipCountFromCountStore", "count( ( )-[:HAS_CHUNK]->( ) ) AS n"))),
}

LEGIT = [k for k in PLANS if k.startswith("legit")]
ATTACK = [k for k in PLANS if not k.startswith("legit")]


# ── Planner layer ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", LEGIT)
def test_plan_accepts_legitimate_queries(name):
    qtype, plan = PLANS[name]
    report = analyze_plan(plan, qtype)
    assert report.ok, report.issues


@pytest.mark.parametrize("name", ATTACK)
def test_plan_rejects_attack_shapes(name):
    qtype, plan = PLANS[name]
    report = analyze_plan(plan, qtype)
    assert not report.ok, f"{name} should have been rejected"


def test_plan_names_the_offending_label():
    _, plan = PLANS["exists subquery"]
    reasons = [i.reason for i in analyze_plan(plan, "r").issues]
    assert any("DocumentChunk" in r for r in reasons)


def test_plan_rejects_any_unlisted_callable_even_under_db():
    plan = produce(op("ProcedureCall", "db.relationshipTypes() :: (relationshipType :: STRING)"))
    assert not analyze_plan(plan, "r").ok


def test_denied_sets_cover_the_whole_document_library():
    assert g.DENIED_LABELS == {"Document", "DocumentChunk", "DocumentCollection"}
    assert g.DENIED_REL_TYPES == {"CONTAINS", "HAS_DOCUMENT_CHUNK"}
    assert "HAS_CHUNK" not in g.DENIED_REL_TYPES  # archival Note->NoteChunk


# ── Lexical layer ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "MATCH (c:Collection) RETURN c.title LIMIT 5",
    "MATCH (c:Collection) WHERE c.title CONTAINS 'CREATE' RETURN c",       # keyword inside a string
    'MATCH (c:Collection) WHERE c.title = "DELETE me" RETURN c',
    "MATCH (c:Collection) RETURN c.title // SET looks scary in a comment",
    "MATCH (c:Collection) /* MERGE */ RETURN c.title",
    "MATCH (n:`Archival Object`) RETURN n LIMIT 1",
    "CALL { MATCH (c:Collection) RETURN c LIMIT 1 } RETURN c",             # CALL subquery is a read construct
    "MATCH (c:Collection) WHERE EXISTS { (c)-[:HAS_PART]->() } RETURN c",
    "MATCH (c:Collection) RETURN c.title;",                                # single trailing semicolon
])
def test_lexical_accepts(query):
    assert lexical_check(query)


@pytest.mark.parametrize("query, fragment", [
    ("", "empty"),
    ("MATCH (c:Collection) RETURN c; MATCH (d:Document) RETURN d", "one statement"),
    ("EXPLAIN MATCH (c:Collection) RETURN c", "EXPLAIN"),
    ("PROFILE MATCH (c:Collection) RETURN c", "EXPLAIN"),
    ("CYPHER runtime=slotted PROFILE MATCH (c) RETURN c", "EXPLAIN"),
    ("USE system SHOW USERS", "USE"),
    ("MATCH (n:$('DocumentChunk')) RETURN n", "Dynamic labels"),
    ("LOAD CSV FROM 'http://x/' AS row RETURN row", "LOAD CSV"),
    ("CREATE (n:Foo) RETURN n", "CREATE"),
    ("MATCH (n) SET n.x = 1", "SET"),
    ("MATCH (n) DETACH DELETE n", "DETACH"),
    ("MATCH (n) NODETACH DELETE n", "NODETACH"),
    ("MERGE (n:Foo) RETURN n", "MERGE"),
    ("DROP INDEX foo", "DROP"),
    ("SHOW DATABASES", "SHOW"),
    ("GRANT MATCH {*} ON GRAPH neo4j TO reader", "GRANT"),
    ("TERMINATE TRANSACTIONS 'x'", "TERMINATE"),
    ("x" * 5000, "longer"),
])
def test_lexical_rejects(query, fragment):
    with pytest.raises(CypherRejected) as exc:
        lexical_check(query)
    assert fragment.lower() in str(exc.value).lower()


# ── check(): end to end with a stubbed planner ──────────────────────────────

async def test_check_returns_query_when_plan_is_clean(monkeypatch):
    async def fake_explain(q):
        return PLANS["legit collection"][1], "r"
    monkeypatch.setattr(g, "explain", fake_explain)
    assert await g.check("  MATCH (c:Collection) RETURN c.title LIMIT 5 ") == "MATCH (c:Collection) RETURN c.title LIMIT 5"


async def test_check_rejects_on_plan_issue_with_reason(monkeypatch):
    async def fake_explain(q):
        return PLANS["unlabeled scan"][1], "r"
    monkeypatch.setattr(g, "explain", fake_explain)
    with pytest.raises(CypherRejected) as exc:
        await g.check("MATCH (n) WHERE n.text IS NOT NULL RETURN n.text")
    assert "AllNodesScan" in str(exc.value)


async def test_check_fails_closed_when_planning_fails(monkeypatch):
    async def fake_explain(q):
        raise RuntimeError("Invalid input 'RETRUN'")
    monkeypatch.setattr(g, "explain", fake_explain)
    with pytest.raises(CypherRejected) as exc:
        await g.check("MATCH (c:Collection) RETRUN c")
    assert "could not be planned" in str(exc.value)


async def test_check_never_reaches_planner_for_lexical_rejections(monkeypatch):
    called = []

    async def fake_explain(q):
        called.append(q)
        return {}, "r"
    monkeypatch.setattr(g, "explain", fake_explain)
    with pytest.raises(CypherRejected):
        await g.check("MATCH (n) DETACH DELETE n")
    assert called == []


def test_rejection_is_a_tool_call_error():
    # agent.py catches ToolCallError and returns the message to the model.
    from asapbackend.tools.validator import ToolCallError
    assert issubclass(CypherRejected, ToolCallError)


# ── Integration: the real planner ───────────────────────────────────────────

LIVE_QUERIES = {
    "legit collection":  "MATCH (c:Collection) RETURN c.title LIMIT 5",
    "legit fulltext":    "CALL db.index.fulltext.queryNodes('free_text_index', 'x') YIELD node, score RETURN node.title, score LIMIT 5",
    "legit path":        "MATCH (c:Collection)-[:HAS_PART*1..3]->(x) RETURN labels(x), count(*)",
    "legit exists":      "MATCH (c:Collection) WHERE EXISTS { (c)-[:HAS_PART]->() } RETURN c.title LIMIT 5",
    "doc label scan":    "MATCH (dc:DocumentCollection)-[:CONTAINS]->(d)-[:HAS_CHUNK]->(ch) RETURN ch.text LIMIT 5",
    "unlabeled scan":    "MATCH (n) WHERE n.text IS NOT NULL RETURN n.text LIMIT 5",
    "label in WHERE":    "MATCH (n) WHERE n:Document RETURN n LIMIT 5",
    "label expr":        "MATCH (c:Collection|DocumentChunk) RETURN c LIMIT 5",
    "exists subquery":   "MATCH (c:Collection) WHERE EXISTS { MATCH (x:DocumentChunk) } RETURN c.title LIMIT 5",
    "count subquery":    "RETURN COUNT { MATCH (x:DocumentChunk) } AS n",
    "labels() filter":   "MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH 'Doc') RETURN n LIMIT 5",
    "db.labels":         "CALL db.labels() YIELD label RETURN label",
    "db.schema":         "CALL db.schema.visualization()",
    "union with docs":   "MATCH (c:Collection) RETURN c.title AS t UNION MATCH (d:Document) RETURN d.file_name AS t",
    "rel only":          "MATCH ()-[r:HAS_DOCUMENT_CHUNK]->() RETURN count(r)",
    "legit has_chunk count": "MATCH ()-[r:HAS_CHUNK]->() RETURN count(r)",
    "legit note chunks": "MATCH (n:Note)-[:HAS_CHUNK]->(c) RETURN c.text LIMIT 3",
    "call subquery doc": "CALL { MATCH (d:DocumentChunk) RETURN d.text AS t LIMIT 1 } RETURN t",
}


@pytest.mark.skipif(not os.environ.get("NEO4J_URI"), reason="set NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD to run against a live server")
@pytest.mark.parametrize("name", list(LIVE_QUERIES))
async def test_live_planner(name, monkeypatch):
    from asapbackend.config import settings
    monkeypatch.setattr(settings, "neo4j_uri", os.environ["NEO4J_URI"])
    monkeypatch.setattr(settings, "neo4j_user", os.environ.get("NEO4J_USER", "neo4j"))
    monkeypatch.setattr(settings, "neo4j_password", os.environ.get("NEO4J_PASSWORD", ""))
    query = LIVE_QUERIES[name]
    if name.startswith("legit"):
        assert await g.check(query) == query
    else:
        with pytest.raises(CypherRejected):
            await g.check(query)
