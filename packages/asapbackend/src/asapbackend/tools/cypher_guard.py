"""
Cypher guard — policy for Cypher that did NOT come from our own code.

Applied to the two untrusted entrypoints before anything reaches Neo4j:
the LLM's read-cypher tool calls (llm/agent.py) and the chat UI's "Run
full query" button (routers/cypher.py). Backend-native tools and RAG run
fixed, parameterised queries and are not guarded (RAG legitimately reads
document chunks).

Two layers, both fail closed:

1. Lexical pre-filter. Cheap checks on the text with string literals and
   comments removed: size, one statement only, no EXPLAIN/PROFILE/USE,
   no admin commands, no write keywords, no LOAD CSV, no dynamic labels.
   Gives the model a fast, specific message for the obvious cases.

2. Planner rules. The query is sent to Neo4j as EXPLAIN (planned, never
   executed) and the plan the server would run is inspected:
     * query type must be read-only ('r');
     * no AllNodesScan (an unlabelled scan can reach any node, including
       document chunks, without ever naming the label);
     * no DynamicLabelNodeLookup, no LoadCSV, no write operators;
     * every procedure/function name in the plan must be allow-listed,
       which also keeps the model away from db.labels/db.schema.* and
       everything under apoc/gds/dbms;
     * no label or relationship type from the document library may appear
       anywhere in the plan — including inside EXISTS/COUNT/UNION branches.

Why the planner and not a grammar: a parser cannot know that
`MATCH (n) WHERE n.text IS NOT NULL` reads document chunks, nor that
`MATCH (n:$("DocumentChunk"))` resolves to that label at runtime. The plan
shows both. Document nodes connect only to each other (CONTAINS,
HAS_DOCUMENT_CHUNK), so a plan that starts from allowed labels and contains no
unlabelled scan cannot reach them.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from asapbackend.tools.validator import ToolCallError

log = logging.getLogger(__name__)


class CypherRejected(ToolCallError):
    """Raised when a query violates the guard policy. Message is user/LLM-safe."""


# ── Policy ──────────────────────────────────────────────────────────────────

MAX_QUERY_CHARS = 4000

# The document library. Off limits to anything the model or a user types.
DENIED_LABELS: frozenset[str] = frozenset({"Document", "DocumentChunk", "DocumentCollection"})
# Relationship types used ONLY by the document library. (HAS_CHUNK is the
# archival Note->NoteChunk type and is allowed; documents use their own.)
DENIED_REL_TYPES: frozenset[str] = frozenset({"CONTAINS", "HAS_DOCUMENT_CHUNK"})
_DENIED_NAMES = DENIED_LABELS | DENIED_REL_TYPES

# Procedures and functions the model is taught to use (prompts/) and has
# actually used (157 stored tool calls scanned 2026-10-09: only the first).
# Kept in step with helm/neo4j values.yaml security.procedureAllowlist.
ALLOWED_CALLABLES: frozenset[str] = frozenset({
    "db.index.fulltext.queryNodes",
    "db.index.vector.queryNodes",
    "ai.text.embed",
})

# Plan operators that are never acceptable on the read path.
_DENIED_OPERATORS: dict[str, str] = {
    "AllNodesScan": "scans every node; start from a labelled node instead",
    "DynamicLabelNodeLookup": "uses a label computed at runtime",
    "LoadCSV": "loads external data",
}
_WRITE_OPERATOR_PREFIXES = (
    "Create", "Delete", "DetachDelete", "Merge", "Set", "Remove", "Foreach",
)

# ── Lexical layer ───────────────────────────────────────────────────────────

# String literals (single/double quoted, with escapes), backtick names and
# comments, removed before keyword scanning so `RETURN 'CREATE'` is fine.
_STRIP_RE = re.compile(
    r"""
      '(?:\\.|[^'\\])*'          # 'single-quoted'
    | "(?:\\.|[^"\\])*"          # "double-quoted"
    | `(?:``|[^`])*`             # `backticked identifier`
    | //[^\n]*                   # // line comment
    | /\*.*?\*/                  # /* block comment */
    """,
    re.VERBOSE | re.DOTALL,
)

_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|NODETACH|SET|REMOVE|DROP|FOREACH)\b", re.I
)
_ADMIN_KEYWORDS = re.compile(
    r"\b(SHOW|GRANT|DENY|REVOKE|ALTER|START|STOP|TERMINATE|RENAME|ENABLE|DEALLOCATE|REALLOCATE)\b",
    re.I,
)
_LOAD_CSV = re.compile(r"\bLOAD\s+CSV\b", re.I)
_LEADING_MODE = re.compile(r"^\s*(EXPLAIN|PROFILE|CYPHER\b[^\n]*?\bEXPLAIN|CYPHER\b[^\n]*?\bPROFILE)\b", re.I)
_USE_CLAUSE = re.compile(r"\bUSE\s+[`\w.]", re.I)
_DYNAMIC_LABEL = re.compile(r":\s*\$\s*\(")


def lexical_check(query: str) -> str:
    """Return the stripped query, or raise CypherRejected."""
    q = query.strip()
    if not q:
        raise CypherRejected("Query is empty.")
    if len(q) > MAX_QUERY_CHARS:
        raise CypherRejected(f"Query is longer than {MAX_QUERY_CHARS} characters.")

    bare = _STRIP_RE.sub(" ", q)

    if ";" in bare.rstrip().rstrip(";"):
        raise CypherRejected("Only one statement per query.")
    if _LEADING_MODE.search(bare):
        raise CypherRejected("EXPLAIN and PROFILE are not allowed.")
    if _USE_CLAUSE.search(bare):
        raise CypherRejected("USE is not allowed; queries always run on the default database.")
    if _DYNAMIC_LABEL.search(bare):
        raise CypherRejected("Dynamic labels (:$(...)) are not allowed; name the label.")
    if _LOAD_CSV.search(bare):
        raise CypherRejected("LOAD CSV is not allowed.")
    m = _WRITE_KEYWORDS.search(bare)
    if m:
        raise CypherRejected(f"Only read-only queries are allowed ({m.group(1).upper()} detected).")
    m = _ADMIN_KEYWORDS.search(bare)
    if m:
        raise CypherRejected(f"Administrative commands are not allowed ({m.group(1).upper()} detected).")
    return q


# ── Planner layer ───────────────────────────────────────────────────────────

# `n:Document`, `c:Collection|DocumentChunk`, `(:DocumentChunk)`, `[:CONTAINS]`,
# `` n:`Document` `` — every label/type in a plan's Details follows ':' or '|'.
_NAME_AFTER_COLON = re.compile(r"[:|]\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
# Qualified callable names: apoc.load.json(, db.labels(, ai.text.embed(, gds.*
_CALLABLE = re.compile(r"\b((?:apoc|gds|dbms|genai|ai|db|tx|cdc)\.[A-Za-z_][\w.]*)\s*\(")


@dataclass
class PlanIssue:
    operator: str
    reason: str


@dataclass
class PlanReport:
    query_type: str
    issues: list[PlanIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _details(op: dict[str, Any]) -> str:
    args = op.get("args") or {}
    d = args.get("Details")
    return d if isinstance(d, str) else ""


def _walk(op: dict[str, Any]):
    yield op
    for child in op.get("children") or []:
        yield from _walk(child)


def analyze_plan(plan: dict[str, Any], query_type: str) -> PlanReport:
    """Apply the planner rules to an EXPLAIN plan (driver's summary.plan)."""
    report = PlanReport(query_type=query_type)
    if query_type != "r":
        report.issues.append(PlanIssue("<query>", "only read-only queries are allowed"))

    for op in _walk(plan):
        # Operator types look like "NodeByLabelScan@neo4j".
        otype = str(op.get("operatorType", "")).split("@", 1)[0]
        details = _details(op)

        if otype in _DENIED_OPERATORS:
            report.issues.append(PlanIssue(otype, _DENIED_OPERATORS[otype]))
        elif otype.startswith(_WRITE_OPERATOR_PREFIXES):
            report.issues.append(PlanIssue(otype, "writes to the graph"))

        for name in _CALLABLE.findall(details):
            if name not in ALLOWED_CALLABLES:
                report.issues.append(PlanIssue(otype, f"procedure/function {name} is not allowed"))

        for name in _NAME_AFTER_COLON.findall(details):
            if name in _DENIED_NAMES:
                report.issues.append(PlanIssue(otype, f"{name} is not accessible"))

        if "$all(" in details or "$any(" in details:
            report.issues.append(PlanIssue(otype, "uses a label computed at runtime"))

    return report


def _explain_sync(query: str) -> tuple[dict[str, Any], str]:
    # Imported lazily: graph_docs opens a driver on first use and tests
    # patch this function instead of needing a database.
    from asapbackend.services.graph_docs import get_driver

    with get_driver().session() as session:
        result = session.run("EXPLAIN " + query)
        summary = result.consume()
        return summary.plan or {}, summary.query_type or ""


async def explain(query: str) -> tuple[dict[str, Any], str]:
    """Plan `query` on the server without executing it."""
    return await asyncio.to_thread(_explain_sync, query)


async def check(query: str) -> str:
    """
    Validate `query` against the policy. Returns the query to execute, or
    raises CypherRejected with a reason the model or user can act on.
    """
    q = lexical_check(query)
    try:
        plan, query_type = await explain(q)
    except Exception as exc:  # syntax errors, unknown labels, server down …
        # Neo4j's own message is the most useful thing to hand back; it never
        # contains data, only the query text the caller already has.
        msg = str(exc).strip().splitlines()[0][:300] if str(exc) else type(exc).__name__
        raise CypherRejected(f"Query could not be planned: {msg}")

    report = analyze_plan(plan, query_type)
    if not report.ok:
        reasons = "; ".join(f"{i.reason} ({i.operator})" for i in report.issues[:4])
        log.warning("Cypher rejected: %s | %s", reasons, q[:200].replace("\n", " "))
        raise CypherRejected(f"Query rejected by security policy: {reasons}.")
    return q
