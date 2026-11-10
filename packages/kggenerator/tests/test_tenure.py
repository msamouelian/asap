"""Role-tenure patterns, against the real sentences from the Division of
Research finding aids (2026-11 investigation) plus the negatives that must
NOT produce a leader relationship."""

import pytest

from kggenerator.tenure import Tenure, build_org_index, find_tenures, resolve_org


def one(text: str) -> Tenure:
    found = find_tenures(text)
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("text, org, date", [
    ("Fox also served as the Director of the Division of Research at HBS from 1953 to 1968, "
     "where he had the responsibility for funding and publishing all the research at the School.",
     "Division of Research", "1953 to 1968"),
    ("He also served as the Director of the Division of Research from 1940 to 1942.",
     "Division of Research", "1940 to 1942"),
    ("He held a number of administrative roles including Director of HBS Doctoral Programs, Associate Dean "
     "for Research and Course Development, and Director of the Division of Research from 1977 to 1979.",
     "Division of Research", "1977 to 1979"),
    ("Professor of Investment and Director of the Division of Research from 1930 to 1933 at Harvard Business School.",
     "Division of Research", "1930 to 1933"),
    ("He became Director of the Division of Research at HBS in 1933 and served until 1936.",
     "Division of Research", "1933 to 1936"),
    ("Appointed Director of the Division of Research in the Graduate School of Business Administration in 1936 "
     "and served in that role until 1940.",
     "Division of Research", "1936 to 1940"),
    ("During his tenure as professor HBS, Corey wore many hats, including the head of several of the School’s "
     "executive education programs in the 1960s and as the Director of the Division of Research from 1980 to 1986.",
     "Division of Research", "1980 to 1986"),
    ("He was promoted to the Director of the Division of Research in 1969, taking the role over from Lawrence E. Fouraker.",
     "Division of Research", "from 1969"),
    ("She was Dean of the Faculty between 1990 and 1995.", "Faculty", "1990 to 1995"),
    ("He was Acting Director of the Bureau of Business Research (1926-1929).", "Bureau of Business Research", "1926 to 1929"),
])
def test_positive_patterns(text, org, date):
    t = one(text)
    assert t.org == org
    assert t.date == date
    assert t.sentence.startswith(text.split(" ")[0])


def test_two_tenures_in_one_sentence_get_their_own_dates():
    text = ("He was Director of the Bureau of Business Research at HBS from 1916 to 1926 and "
            "Director of the Division of Research from 1943 to 1953.")
    found = find_tenures(text)
    assert [(t.org, t.date) for t in found] == [
        ("Bureau of Business Research", "1916 to 1926"),
        ("Division of Research", "1943 to 1953"),
    ]


@pytest.mark.parametrize("text", [
    "From 1964 to 1972 he was the Associate Director of the Division of Research.",
    "Tully became the Assistant Director of the Division of Research in the fall of 1948, and held the position to 1951.",
    "He was Vice President of the Company from 1950 to 1955.",
    "He served as Deputy Director of the Institute from 1960 to 1962.",
    "Director of HBS Doctoral Programs.",                               # no date
    "He was a director of research at several firms from 1950 to 1960.",  # common noun, lowercase org
    "The instructions were created under various directors of the Bureau of Business Research from 1923 to 1944.",
    # appositive about someone else: the role belongs to Lowell, not the note's subject
    "Lawrence Lowell, then president of Harvard College, asked Donham to serve as the second dean for the Business School in 1919.",
    "He worked for James Smith, who was president of the Company from 1950 to 1960.",
    # a company "director" is a board seat, not leadership of the body
    "He was Treasurer and later Director of Incorporated Investors from 1930-1936.",
])
def test_negative_patterns(text):
    assert find_tenures(text) == []


def test_company_president_still_counts_and_unit_director_still_counts():
    assert one("He was President of Massachusetts Distributors, Inc., from 1936 to 1939.").org == "Massachusetts Distributors"
    assert one("He also became the Director of the Associates of Harvard Business School in 1951.").org == "Associates of Harvard Business School"


def test_acting_prefix_is_kept_in_role():
    t = one("He was Acting Dean of the School from 1970 to 1971.")
    assert t.role.lower() == "acting dean"


def test_org_resolution_by_alternate_name_only_when_unique():
    bodies = [
        {"id": "main", "display_name": "Harvard University. Graduate School of Business Administration. Division of Research Administration",
         "alternate_names": ["DOR", "Division of Research", "Division of Research at Harvard Business School"]},
        {"id": "committee", "display_name": "Division of Research Committee on Research", "alternate_names": ["Committee on Research"]},
        {"id": "bbr", "display_name": "Harvard University. Graduate School of Business Administration. Bureau of Business Research",
         "alternate_names": ["Bureau of Business Research", "BBR"]},
        {"id": "dup1", "display_name": "Publishing Division", "alternate_names": []},
        {"id": "dup2", "display_name": "Publishing Division", "alternate_names": ["HBS Publishing Division"]},
    ]
    index = build_org_index(bodies)
    assert resolve_org("Division of Research", index) == "main"
    assert resolve_org("the Division of Research at HBS", index) == "main"
    assert resolve_org("Bureau of Business Research", index) == "bbr"
    assert resolve_org("Committee on Research", index) == "committee"
    assert resolve_org("Publishing Division", index) is None      # ambiguous → fail closed
    assert resolve_org("Division of Marketing", index) is None    # unknown → fail closed
