"""Role-tenure backstop: pattern-based leader relationships from biographical notes.

Why this exists. LLM extraction gets most leadership facts, but it is not
exhaustive: in the Division of Research test (2026-11) it dropped Rosenbloom's
"Director of the Division of Research from 1977 to 1979", attached McNair's
directorship to the wrong body, and reduced Lewis's to a generic relation —
three of sixteen dated director statements. Those statements follow a small
set of regular English patterns in biographical notes, so a deterministic
pass can catch what the model missed. It never overrides the LLM: it only
ADDS a hasOrHadLeader pair when the person and the organisation both exist
in the knowledge graph and no leader edge between them exists yet.

Scope, deliberately narrow (fail closed, per docs/kg-conventions.md):
  * leadership roles only — director, dean, president, chair, head, provost,
    principal, superintendent, optionally prefixed "acting"/"interim";
    "associate", "assistant", "deputy", "vice" and "co-" roles are skipped
    (an Associate Director is not the Division's leader);
  * the organisation must resolve to EXACTLY ONE inferred corporate body by
    normalised display name or alternate name; none or several → skipped;
  * at least a start year must be stated; the date is stored verbatim-style
    ("1953 to 1968", "from 1969", "1940 to 1942") like LLM-extracted dates;
  * every edge carries the sentence as its quote, the note's record as
    source_uri, confidence 1.0, and method="pattern" so it can be audited or
    removed independently of LLM output.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from kggenerator.resolve import norm_name

logger = logging.getLogger(__name__)

_ROLE = r"(?:director|dean|president|chair(?:man|woman|person)?|head|provost|principal|superintendent)"
_YEAR = r"(?:1[89]\d\d|20\d\d)"

# "(the) Director of (the) <Org>" where <Org> runs until a clause break or a
# connector that introduces a place or a date ("at HBS", "from 1953", "in
# 1936", "between 1940 and 1942"). Tempered so the org never swallows them.
_ROLE_OF_ORG = re.compile(
    rf"""
    (?P<pre>\b(?:acting|interim)\s+)?
    \b(?P<role>{_ROLE})\s+of\s+(?:the\s+)?
    (?P<org>(?:(?!\s+(?:at|from|in|between|during|until|since|where)\b)[^.;,()])
             {{2,100}})
    """,
    re.IGNORECASE | re.VERBOSE,
)
_EXCLUDED_PREFIX = re.compile(r"(?:associate|assistant|deputy|vice|co)[-\s]+$", re.IGNORECASE)

# An appositive or relative clause right before the role names SOMEONE ELSE
# ("Lawrence Lowell, then president of Harvard College, asked Donham …").
_APPOSITIVE_BEFORE = re.compile(r"(?:,\s*(?:then|the|a|an|later|former|its)\s+|\bwho\s+(?:was|became|had been|is)\s+(?:the\s+)?)$", re.IGNORECASE)

# "Director of X" is only a leadership claim when X is an organisational
# unit. "Director of Incorporated Investors" is a board seat, not the head.
_UNIT_WORDS = re.compile(
    r"\b(?:division|bureau|department|office|cent(?:er|re)|institute|program(?:me)?|school|"
    r"laborator(?:y|ies)|library|museum|committee|project|research|studies|associates|"
    r"services|group|unit|council|foundation|society|association|fund|archives?|"
    r"doctoral|education|college|faculty|agency|authority|board|commission|administration)\b",
    re.IGNORECASE,
)

# Date shapes, tried in order on the text that follows the org name within
# the same sentence (and before the next role mention).
_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\bfrom\s+(?P<a>{_YEAR})\s*(?:to|until|through|-|–|—)\s*(?P<b>{_YEAR})", re.I), "range"),
    (re.compile(rf"\bbetween\s+(?P<a>{_YEAR})\s+and\s+(?P<b>{_YEAR})", re.I), "range"),
    (re.compile(rf"\(?\b(?P<a>{_YEAR})\s*[-–—]\s*(?P<b>{_YEAR})\b\)?"), "range"),
    (re.compile(rf"\bin\s+(?P<a>{_YEAR})\b.{{0,80}}?\b(?:until|through|to)\s+(?P<b>{_YEAR})\b", re.I), "range"),
    (re.compile(rf"\b(?:in|since|beginning)\s+(?P<a>{_YEAR})\b", re.I), "start"),
    (re.compile(rf"\bfrom\s+(?P<a>{_YEAR})\b", re.I), "start"),
]
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


@dataclass(frozen=True)
class Tenure:
    role: str          # as written, e.g. "Director", "Acting Dean"
    org: str           # organisation text as written, e.g. "Division of Research"
    date: str          # "1953 to 1968" | "from 1969" | "1940 to 1942"
    start: int
    end: int | None
    sentence: str      # the verbatim sentence (the quote)


def _date_after(text: str) -> tuple[str, int, int | None] | None:
    for pat, kind in _DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        a = int(m.group("a"))
        if kind == "range":
            b = int(m.group("b"))
            if b < a:
                continue
            return f"{a} to {b}", a, b
        return f"from {a}", a, None
    return None


def find_tenures(text: str) -> list[Tenure]:
    """Every leadership tenure stated in `text` (a biographical note)."""
    out: list[Tenure] = []
    for sentence in _SENTENCE_SPLIT.split(" ".join((text or "").split())):
        matches = list(_ROLE_OF_ORG.finditer(sentence))
        for i, m in enumerate(matches):
            before = sentence[: m.start("role")]
            if _EXCLUDED_PREFIX.search(before) or _APPOSITIVE_BEFORE.search(before):
                continue
            org = m.group("org").strip()
            if not org[:1].isupper():
                continue  # "director of research" as a common-noun phrase
            if m.group("role").lower() == "director" and not _UNIT_WORDS.search(org):
                continue  # a company "director" is a board member, not its leader
            # Dates belong to this role mention: look only up to the next one.
            stop = matches[i + 1].start() if i + 1 < len(matches) else len(sentence)
            found = _date_after(sentence[m.end(): stop])
            if not found:
                continue
            date, start, end = found
            role = ((m.group("pre") or "") + m.group("role")).strip()
            out.append(Tenure(role=role, org=org, date=date, start=start, end=end,
                              sentence=sentence.strip()))
    return out


_ORG_SUFFIXES = (" at harvard business school", " at hbs",
                 " harvard business school", " hbs")  # longest first


def _org_keys(name: str) -> set[str]:
    """Normalised forms an organisation mention may match under: with and
    without a leading "the", with and without a trailing institution tag."""
    base = norm_name(name)
    variants = {base, base[4:]} if base.startswith("the ") else {base}
    keys: set[str] = set()
    for v in variants:
        keys.add(v)
        for suffix in _ORG_SUFFIXES:
            if v.endswith(suffix):
                keys.add(v[: -len(suffix)].strip())
                break
    return {k for k in keys if k}


def build_org_index(bodies: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    """norm name → {inferred ids} over corporate bodies' display + alternate names."""
    index: dict[str, set[str]] = {}
    for b in bodies:
        names = [b.get("display_name") or ""] + list(b.get("alternate_names") or [])
        for nm in names:
            for key in _org_keys(nm):
                index.setdefault(key, set()).add(b["id"])
    return index


def resolve_org(org_text: str, index: dict[str, set[str]]) -> str | None:
    """The single inferred id the mention resolves to, else None (fail closed)."""
    ids: set[str] = set()
    for key in _org_keys(org_text):
        ids |= index.get(key, set())
    return next(iter(ids)) if len(ids) == 1 else None
