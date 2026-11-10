"""
kg-search: name resolution must honour aliases, and a capped dossier must
say so and keep leadership lines. Regression tests from the Division of
Research investigation (2026-11).
"""

from __future__ import annotations

import pytest

from asapbackend.tools import kg_search as ks


# ── Ranking ─────────────────────────────────────────────────────────────────

MAIN = {"id": "main", "score": 2.1, "labels": ["Inferred", "InferredAgent"],
        "name": "Harvard University. Graduate School of Business Administration. Division of Research Administration",
        "alt": ["DOR", "Division of Research", "Division of Research Administration"]}
COMMITTEE = {"id": "committee", "score": 3.4, "labels": ["Inferred", "InferredAgent"],
             "name": "Division of Research Committee on Research", "alt": ["Committee on Research"]}
AVIATION = {"id": "aviation", "score": 2.8, "labels": ["Inferred", "InferredEvent"],
            "name": "Aviation research conducted by the Division of Research", "alt": []}


def test_exact_alias_match_outranks_higher_scoring_prefix_match():
    ranked = ks._rank_hits("Division of Research", [COMMITTEE, AVIATION, MAIN])
    assert [h["id"] for h in ranked] == ["main", "committee", "aviation"]


def test_exact_display_name_match_wins_too():
    ranked = ks._rank_hits("Committee on Research", [MAIN, COMMITTEE])
    assert ranked[0]["id"] == "committee"


def test_without_an_exact_match_score_order_is_kept():
    ranked = ks._rank_hits("Research Division", [MAIN, COMMITTEE, AVIATION])
    assert [h["id"] for h in ranked] == ["committee", "aviation", "main"]


def test_norm_ignores_case_punctuation_and_leading_the():
    assert ks._norm("The Division of Research") == ks._norm("division, of research")


# ── Dossier rendering with a fake graph ─────────────────────────────────────

def _fake_read(entity: dict, rels: list[dict]):
    async def read(query: str, **params):
        q = " ".join(query.split())
        if "fulltext.queryNodes" in q:
            return [{"id": entity["id"], "name": entity["display_name"],
                     "alt": entity.get("alternate_names", []), "labels": ["Inferred", "InferredAgent"], "score": 3.0}]
        if "RETURN properties(n) AS p" in q:
            return [{"p": entity}]
        if "-[r]->(m:Inferred)" in q:
            return rels
        if "UNWIND $uris" in q:
            return []
        if "MATCH (a:Agent {uri: $uri})" in q:
            return []
        return []
    return read


def rel(t: str, other: str, quote: str = "", date: str | None = None) -> dict:
    return {"t": t, "nature": None, "date": date, "quote": quote, "sources": [], "conf": 1.0, "other": other}


async def test_capped_dossier_keeps_leaders_and_announces_truncation(monkeypatch):
    entity = {"id": "x", "display_name": "Some Body", "alternate_names": [], "agent_type": "corporate_body"}
    # 70 correspondents (alphabetically early) + 3 leaders (alphabetically late type).
    rels = [rel("RICO_HAS_OR_HAD_CORRESPONDENT", f"Corr {i:02d}") for i in range(70)]
    rels += [rel("RICO_HAS_OR_HAD_LEADER", "Fox, Bertrand", "Director from 1953 to 1968", "1953 to 1968"),
             rel("RICO_HAS_OR_HAD_LEADER", "Ruggles, Clyde", "Director from 1940 to 1942", "1940 to 1942"),
             rel("RICO_IS_OR_WAS_SUBDIVISION_OF", "Harvard Business School")]
    monkeypatch.setattr(ks, "_read", _fake_read(entity, rels))
    out = await ks.run("Some Body")
    assert "has or had leader: Fox, Bertrand (1953 to 1968)" in out
    assert "has or had leader: Ruggles, Clyde (1940 to 1942)" in out
    assert "is or was subdivision of: Harvard Business School" in out
    assert f"showing the first {ks._MAX_RELATIONS} of 73 relationships" in out
    # leaders precede correspondents in the rendered order
    assert out.index("has or had leader") < out.index("has or had correspondent")


async def test_uncapped_dossier_has_no_truncation_note(monkeypatch):
    entity = {"id": "x", "display_name": "Small Body", "alternate_names": []}
    rels = [rel("RICO_HAS_OR_HAD_LEADER", "A"), rel("RICO_HAS_OR_HAD_MEMBER", "B")]
    monkeypatch.setattr(ks, "_read", _fake_read(entity, rels))
    out = await ks.run("Small Body")
    assert "Relationships (2):" in out
    assert "showing the first" not in out


def test_rel_order_puts_priority_types_first_then_alphabetical():
    rows = [rel("RICO_IS_ASSOCIATED_WITH_EVENT", "z"), rel("RICO_HAS_OR_HAD_MEMBER", "m"),
            rel("RICO_HAS_OR_HAD_LEADER", "b"), rel("RICO_HAS_OR_HAD_LEADER", "a"), rel("INFERRED_GENERIC_RELATIONSHIP", "g")]
    ordered = sorted(rows, key=ks._rel_order)
    assert [(r["t"], r["other"]) for r in ordered] == [
        ("RICO_HAS_OR_HAD_LEADER", "a"), ("RICO_HAS_OR_HAD_LEADER", "b"), ("RICO_HAS_OR_HAD_MEMBER", "m"),
        ("INFERRED_GENERIC_RELATIONSHIP", "g"), ("RICO_IS_ASSOCIATED_WITH_EVENT", "z"),
    ]


# ── Call-time trace ─────────────────────────────────────────────────────────

async def test_each_call_is_logged_with_the_resolved_entity(monkeypatch, caplog):
    entity = {"id": "x", "display_name": "Some Body", "alternate_names": []}
    monkeypatch.setattr(ks, "_read", _fake_read(entity, []))
    with caplog.at_level("INFO", logger=ks.log.name):
        await ks.run("Some Body")
    assert "kg-search: name='Some Body' -> 'Some Body'" in caplog.text


async def test_a_no_match_call_is_logged_too(monkeypatch, caplog):
    async def nothing(query: str, **params):
        return []
    async def no_embed(text: str):
        return None
    monkeypatch.setattr(ks, "_read", nothing)
    monkeypatch.setattr(ks, "_embed", no_embed)
    with caplog.at_level("INFO", logger=ks.log.name):
        await ks.run("Nobody At All")
    assert "kg-search: name='Nobody At All' -> no match" in caplog.text


# ── Leadership succession table ─────────────────────────────────────────────

def test_succession_table_is_counted_date_ordered_and_deduplicated():
    rows = [rel("RICO_HAS_OR_HAD_LEADER", "Fox, Bertrand", "Director 1953-1968", "1953 to 1968"),
            rel("RICO_HAS_OR_HAD_LEADER", "Yoshino", "a director"),
            rel("RICO_HAS_OR_HAD_LEADER", "Lewis", "Appointed Director in 1936", "1936 to 1940"),
            rel("RICO_HAS_OR_HAD_LEADER", "Lewis", "same edge again", "1936 to 1940"),
            rel("RICO_HAS_OR_HAD_LEADER", "Dolan", "briefly | in 2001", "briefly in 2001"),
            rel("RICO_HAS_OR_HAD_MEMBER", "Someone")]
    out = ks._succession_table(rows)
    assert "COMPLETE LIST, 4 entries" in out[0]
    body = [l for l in out if l.startswith("| ") and not l.startswith("| #") and not l.startswith("| ---")]
    assert [l.split(" | ")[1] for l in body] == ["Lewis", "Fox, Bertrand", "Dolan", "Yoshino"]
    assert body[-1].split(" | ")[2] == "not stated"
    assert "briefly / in 2001" in body[2]          # pipes cannot break the table


def test_no_succession_table_for_fewer_than_two_leaders():
    assert ks._succession_table([rel("RICO_HAS_OR_HAD_LEADER", "Only One", "", "1900")]) == []
    assert ks._succession_table([rel("RICO_HAS_OR_HAD_MEMBER", "A"), rel("RICO_HAS_OR_HAD_MEMBER", "B")]) == []


async def test_succession_table_survives_the_relationship_cap(monkeypatch):
    entity = {"id": "x", "display_name": "Big Body", "alternate_names": []}
    rels = [rel("RICO_HAS_OR_HAD_LEADER", f"Leader {i:02d}", "", str(1900 + i)) for i in range(ks._MAX_RELATIONS + 5)]
    monkeypatch.setattr(ks, "_read", _fake_read(entity, rels))
    out = await ks.run("Big Body")
    assert f"COMPLETE LIST, {ks._MAX_RELATIONS + 5} entries" in out
    assert f"| {ks._MAX_RELATIONS + 5} | Leader {ks._MAX_RELATIONS + 4:02d} |" in out
