"""Generate the curated RiC-O relation table used by the KG extraction prompt
and the Neo4j loader.

Parses ric/RiC-O_1-1_core.rdf and validates the CURATED list below against
the ontology: every property must exist, and every inverse pairing must match
the ontology's owl:inverseOf declarations (symmetric properties must be
declared owl:SymmetricProperty). The script fails loudly on any mismatch, so
a typo here can never ship a relation RiC does not define.

Output is written to src/kggenerator/rico_relations.py and checked in — the
RDF is parsed once here at build time, never at runtime.

Run from the repo root:
    uv run --project packages/kggenerator python packages/kggenerator/scripts/generate_rico_relations.py
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RDF_PATH = REPO_ROOT / "ric" / "RiC-O_1-1_core.rdf"
OUT_PATH = REPO_ROOT / "packages" / "kggenerator" / "src" / "kggenerator" / "rico_relations.py"

RICO = "https://www.ica.org/standards/RiC/ontology#"
NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
}

# ---------------------------------------------------------------------------
# Curated subset: (canonical name, inverse name or None if symmetric,
#                  category, hint shown to the extraction LLM)
#
# The canonical direction is the one the LLM is asked to emit; the loader
# materializes both directions. Categories group the prompt menu.
# RiC's generic isRelatedTo is deliberately excluded — the fallback for
# "no curated relation fits" is INFERRED_GENERIC_RELATIONSHIP.
# ---------------------------------------------------------------------------
CURATED: list[tuple[str, str | None, str, str]] = [
    # --- agent ↔ agent ---
    ("hasOrHadCorrespondent", None, "agent-agent",
     "A exchanged letters or other correspondence with B (symmetric)."),
    ("hasOrHadEmployer", "isOrWasEmployerOf", "agent-agent",
     "Person A was employed by agent B."),
    ("hasOrHadManager", "isOrWasManagerOf", "agent-agent",
     "A was managed or administered by person B."),
    ("hasOrHadLeader", "isOrWasLeaderOf", "agent-agent",
     "Corporate body or group A was led by B (president, chairman, director, superintendent)."),
    ("hasOrHadMember", "isOrWasMemberOf", "agent-agent",
     "Group, family, or corporate body A had B as a member."),
    ("hasOrHadOwner", "isOrWasOwnerOf", "agent-agent",
     "A was owned by agent B."),
    ("hasOrHadController", "isOrWasControllerOf", "agent-agent",
     "Corporate body A was controlled by B (parent company, holding company, controlling lessee)."),
    ("hasOrHadSubdivision", "isOrWasSubdivisionOf", "agent-agent",
     "Corporate body A had B as a subdivision, department, or branch."),
    ("hasSuccessor", "isSuccessorOf", "agent-agent",
     "Agent A was succeeded by B (corporate reorganization, merger, renamed entity)."),
    ("hasOrHadAuthorityOver", "isOrWasUnderAuthorityOf", "agent-agent",
     "A held authority over B."),
    ("hasOrHadSpouse", None, "agent-agent",
     "A and B were married (symmetric)."),
    ("hasChild", "isChildOf", "agent-agent",
     "Person A had child B."),
    ("hasSibling", None, "agent-agent",
     "A and B were siblings (symmetric)."),
    ("hasAncestor", "hasDescendant", "agent-agent",
     "Person A had ancestor B (beyond parent/child)."),
    ("hasOrHadTeacher", "hasOrHadStudent", "agent-agent",
     "Person A was taught or mentored by B."),
    ("knows", None, "agent-agent",
     "A and B knew each other personally (symmetric); use only when no more specific relation fits."),
    # --- agent ↔ place ---
    ("agentHasOrHadLocation", "isOrWasLocationOfAgent", "agent-place",
     "Agent A was located or headquartered at place B."),
    ("hasBirthPlace", "isBirthPlaceOf", "agent-place",
     "Person A was born at place B."),
    ("hasDeathPlace", "isDeathPlaceOf", "agent-place",
     "Person A died at place B."),
    ("isAssociatedWithPlace", "isPlaceAssociatedWith", "any-place",
     "A (agent or event) is associated with place B (area of operations or activity)."),
    # --- event ---
    ("hasOrHadParticipant", "isOrWasParticipantIn", "event-agent",
     "Event A had agent B as a participant."),
    ("isAssociatedWithEvent", "isEventAssociatedWith", "any-event",
     "A is associated with event B (affected by it, subject of it)."),
    ("hasOrHadSubevent", "isOrWasSubeventOf", "event-event",
     "Event A included B as a subevent."),
]


def snake(name: str) -> str:
    """rico camelCase property name → RICO_SCREAMING_SNAKE Neo4j type."""
    return "RICO_" + re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).upper()


def parse_ontology() -> dict[str, dict]:
    """Index every rico object property: inverse, symmetry, definition."""
    root = ET.parse(RDF_PATH).getroot()
    props: dict[str, dict] = {}
    about_attr = f"{{{NS['rdf']}}}about"
    resource_attr = f"{{{NS['rdf']}}}resource"

    for el in root:
        about = el.get(about_attr, "")
        if not about.startswith(RICO):
            continue
        name = about[len(RICO):]
        tag = el.tag.split("}")[1]
        is_object_property = tag == "ObjectProperty"
        is_symmetric = tag == "SymmetricProperty"
        inverse = None
        definition = ""
        for child in el:
            ctag = child.tag.split("}")[1]
            if ctag == "inverseOf":
                res = child.get(resource_attr, "")
                if res.startswith(RICO):
                    inverse = res[len(RICO):]
            elif ctag == "type":
                res = child.get(resource_attr, "")
                if res.endswith("SymmetricProperty"):
                    is_symmetric = True
                elif res.endswith("ObjectProperty"):
                    is_object_property = True
            elif ctag == "comment" and not definition:
                definition = " ".join((child.text or "").split())
        if is_object_property or is_symmetric:
            entry = props.setdefault(
                name, {"inverse": None, "symmetric": False, "definition": ""}
            )
            entry["inverse"] = entry["inverse"] or inverse
            entry["symmetric"] = entry["symmetric"] or is_symmetric
            entry["definition"] = entry["definition"] or definition
    return props


def main() -> None:
    props = parse_ontology()
    errors: list[str] = []
    rows: list[dict] = []

    for name, inverse, category, hint in CURATED:
        if name not in props:
            errors.append(f"{name}: not found in RiC-O core")
            continue
        onto = props[name]
        if inverse is None:
            if not onto["symmetric"]:
                errors.append(
                    f"{name}: curated as symmetric but not owl:SymmetricProperty "
                    f"(ontology inverse: {onto['inverse']})"
                )
                continue
        else:
            if inverse not in props:
                errors.append(f"{name}: curated inverse {inverse} not found in RiC-O core")
                continue
            # inverseOf may be declared on either member of the pair.
            declared = onto["inverse"] or next(
                (n for n, p in props.items() if p["inverse"] == name), None
            )
            if declared != inverse and props[inverse].get("inverse") != name:
                errors.append(
                    f"{name}: curated inverse {inverse} does not match ontology "
                    f"({declared!r})"
                )
                continue
        rows.append({
            "name": name,
            "inverse": inverse,
            "category": category,
            "hint": hint,
            "neo4j_type": snake(name),
            "inverse_neo4j_type": snake(inverse) if inverse else snake(name),
            "uri": RICO + name,
            "inverse_uri": (RICO + inverse) if inverse else (RICO + name),
            "definition": onto["definition"],
        })

    if errors:
        print("RiC-O validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    lines = [
        '"""Curated RiC-O 1.1 relation table. GENERATED — do not edit by hand.',
        "",
        "Regenerate with scripts/generate_rico_relations.py after changing the",
        "curated list; the generator validates every entry against the ontology.",
        '"""',
        "",
        "RICO_RELATIONS: list[dict] = [",
    ]
    for r in rows:
        lines.append("    {")
        for k, v in r.items():
            lines.append(f"        {k!r}: {v!r},")
        lines.append("    },")
    lines.append("]")
    lines.append("")
    lines.append("RELATIONS_BY_NAME = {r['name']: r for r in RICO_RELATIONS}")
    lines.append("")
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(rows)} validated relations to {OUT_PATH}")


if __name__ == "__main__":
    main()
