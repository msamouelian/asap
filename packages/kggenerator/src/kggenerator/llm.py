"""LLM extraction client: chunk in, validated entity/relation graph out.

Plain prompt-instructed JSON with a robust parse and a feedback retry —
structured-output grammars are not reliably supported for reasoning models
on LM Studio, and the validation loop catches what sampling lets through.
Validation is fail-closed on provenance: an evidence index that does not
exist in the chunk is an error, never silently accepted.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from kggenerator import config
from kggenerator.rico_relations import RELATIONS_BY_NAME

logger = logging.getLogger(__name__)

GENERIC_RELATION = "inferredGenericRelationship"
ENTITY_TYPES = {"agent", "place", "event"}
AGENT_TYPES = {"person", "family", "corporate_body"}

_PROMPT_PATH = Path(__file__).parent / "prompts" / "kg_extraction.txt"

# System prompt for entity-resolution pair adjudication (resolve phase).
ER_ADJUDICATION_PROMPT = (
    Path(__file__).parent / "prompts" / "er_adjudication.txt"
).read_text(encoding="utf-8")


def build_system_prompt() -> str:
    menu = "\n".join(
        f"- {r['name']} — {r['hint']}" for r in RELATIONS_BY_NAME.values()
    )
    return _PROMPT_PATH.read_text(encoding="utf-8").replace("{relation_menu}", menu)


class ExtractionError(RuntimeError):
    """Raised when a chunk still fails validation after all retries."""


class KGExtractor:
    def __init__(self) -> None:
        # One LLM serves BOTH extraction and adjudication. No defaults: the
        # kggenerator chart supplies KG_INFERENCE_* (install-charts.sh
        # --kg-inference-*), so fail loudly rather than call a phantom host.
        missing = [
            name for name, value in (
                ("KG_INFERENCE_BASE_URL", config.INFERENCE_BASE_URL),
                ("KG_INFERENCE_MODEL", config.INFERENCE_MODEL),
                ("KG_INFERENCE_API_KEY", config.INFERENCE_API_KEY),
            ) if not value
        ]
        if missing:
            raise RuntimeError(
                "Knowledge-graph LLM is not configured — missing "
                + ", ".join(missing)
                + ". Provide them via install-charts.sh --kg-inference-base-url / "
                "--kg-inference-model / --kg-inference-api-key."
            )
        self._client = httpx.Client(
            base_url=config.INFERENCE_BASE_URL,
            headers={"Authorization": f"Bearer {config.INFERENCE_API_KEY}"},
            timeout=config.INFERENCE_TIMEOUT_S,
        )
        self._system_prompt = build_system_prompt()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KGExtractor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------

    def _chat(self, messages: list[dict[str, str]]) -> str:
        # Same OpenAI-compatibility rules as asapbackend/llm/client.py:
        # temperature -1 omits the parameter (gpt-5-class rejects non-default
        # values); a configured reasoning effort is sent and switches the
        # completion cap to max_completion_tokens (gpt-5-class rejects max_tokens).
        payload: dict[str, Any] = {
            "model": config.INFERENCE_MODEL,
            "messages": messages,
        }
        if config.INFERENCE_TEMPERATURE >= 0:
            payload["temperature"] = config.INFERENCE_TEMPERATURE
        if config.INFERENCE_REASONING_EFFORT:
            payload["reasoning_effort"] = config.INFERENCE_REASONING_EFFORT
        if config.INFERENCE_MAX_TOKENS > 0:
            cap_key = "max_completion_tokens" if config.INFERENCE_REASONING_EFFORT else "max_tokens"
            payload[cap_key] = config.INFERENCE_MAX_TOKENS
        resp = self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"] or ""
        # Reasoning models may leak a think block into content.
        return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Extract the outermost JSON object, tolerating fences and prose."""
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object found in model output")
        obj = json.loads(text[start : end + 1])
        if not isinstance(obj, dict):
            raise ValueError("model output is not a JSON object")
        return obj

    # ------------------------------------------------------------------

    def extract_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        """Extract one chunk, retrying with validation feedback, then salvage.

        Returns {"entities": [...], "relations": [...]} where every item has
        passed validation. On the final attempt, invalid items are dropped
        (logged) rather than failing the whole chunk.
        """
        valid_indices = {u["i"] for u in chunk["texts"]}
        texts_by_i = {u["i"]: u["text"] for u in chunk["texts"]}
        user_msg = json.dumps(chunk, ensure_ascii=False)
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_msg},
        ]

        last_errors: list[str] = []
        for attempt in range(config.LLM_RETRIES + 1):
            try:
                raw = self._chat(messages)
                graph = self._parse_json(raw)
            except (ValueError, json.JSONDecodeError) as exc:
                last_errors = [f"output was not valid JSON: {exc}"]
                graph = None
            except httpx.HTTPError as exc:
                raise ExtractionError(f"inference request failed: {exc}") from exc

            if graph is not None:
                graph, errors = _validate(graph, valid_indices, texts_by_i)
                if not errors:
                    return graph
                last_errors = errors
                if attempt == config.LLM_RETRIES:
                    return _salvage(graph, valid_indices, texts_by_i, errors)

            logger.warning(
                "Chunk extraction attempt %d failed validation (%d errors); retrying.",
                attempt + 1, len(last_errors),
            )
            messages = [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": raw},
                {"role": "user", "content":
                    "Your output failed validation:\n- "
                    + "\n- ".join(last_errors[:15])
                    + "\nReturn the corrected complete JSON object only."},
            ]
        raise ExtractionError(f"chunk failed after retries: {last_errors[:5]}")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _quote_matches(quote: str, evidence: list, texts_by_i: dict) -> bool:
    """The verbatim quote must appear in at least one CITED unit — quoting
    one unit while citing its neighbor was observed in evaluation (citation
    drift) and silently corrupts provenance. Whitespace- and case-tolerant."""
    q = " ".join(str(quote).split()).casefold().strip('"\u201c\u201d ')
    if not q:
        return True
    for i in evidence or []:
        t = " ".join(str(texts_by_i.get(i, "")).split()).casefold()
        if q in t:
            return True
    return False


def _validate(
    graph: dict[str, Any], valid_indices: set[int], texts_by_i: dict
) -> tuple[dict[str, Any], list[str]]:
    """Normalize in place and return (graph, error list)."""
    errors: list[str] = []
    entities = graph.get("entities")
    relations = graph.get("relations")
    if not isinstance(entities, list):
        return graph, ['missing or non-list "entities"']
    if not isinstance(relations, list):
        graph["relations"] = relations = []

    ids: set[str] = set()
    for n, e in enumerate(entities):
        if not isinstance(e, dict):
            errors.append(f"entities[{n}] is not an object")
            continue
        eid = str(e.get("id") or "")
        name = str(e.get("name") or "").strip()
        etype = e.get("type")
        if not eid:
            errors.append(f"entities[{n}] has no id")
        elif eid in ids:
            errors.append(f"duplicate entity id {eid!r}")
        ids.add(eid)
        if not name:
            errors.append(f"entity {eid!r} has no name")
        if etype not in ENTITY_TYPES:
            errors.append(f"entity {eid!r} has invalid type {etype!r} "
                          f"(must be agent, place, or event)")
        if etype == "agent" and e.get("agent_type") not in AGENT_TYPES:
            errors.append(f"agent {eid!r} needs agent_type person, family, "
                          f"or corporate_body (got {e.get('agent_type')!r})")
        errors.extend(_check_evidence(e, f"entity {eid!r}", valid_indices))

    for n, r in enumerate(relations):
        if not isinstance(r, dict):
            errors.append(f"relations[{n}] is not an object")
            continue
        rtype = str(r.get("type") or "")
        label = f"relation {r.get('from')!r}->{r.get('to')!r} ({rtype})"
        if r.get("from") not in ids or r.get("to") not in ids:
            errors.append(f"{label}: from/to must be ids from the entities list")
        if r.get("from") == r.get("to"):
            errors.append(f"{label}: relates an entity to itself")
        if rtype != GENERIC_RELATION and rtype not in RELATIONS_BY_NAME:
            errors.append(f"{label}: type not in the relation menu "
                          f"(use {GENERIC_RELATION} if nothing fits)")
        errors.extend(_check_evidence(r, label, valid_indices))
        if r.get("quote") and isinstance(r.get("evidence"), list) \
                and not _check_evidence(r, label, valid_indices) \
                and not _quote_matches(r["quote"], r["evidence"], texts_by_i):
            errors.append(
                f"{label}: the quote does not appear in any cited evidence "
                "unit — cite the unit the quote actually comes from, or fix "
                "the quote")
    return graph, errors


def _check_evidence(item: dict[str, Any], label: str, valid: set[int]) -> list[str]:
    ev = item.get("evidence")
    if not isinstance(ev, list) or not ev:
        return [f"{label}: evidence (list of text unit indices) is required"]
    bad = [i for i in ev if not isinstance(i, int) or i not in valid]
    if bad:
        return [f"{label}: evidence indices {bad} do not exist in this input"]
    return []


def _salvage(
    graph: dict[str, Any], valid_indices: set[int], texts_by_i: dict,
    errors: list[str]
) -> dict[str, Any]:
    """Keep the valid subset of a graph that failed final validation."""
    entities = [
        e for e in graph.get("entities", [])
        if isinstance(e, dict)
        and e.get("id") and str(e.get("name") or "").strip()
        and e.get("type") in ENTITY_TYPES
        and (e.get("type") != "agent" or e.get("agent_type") in AGENT_TYPES)
        and not _check_evidence(e, "", valid_indices)
    ]
    ids = {e["id"] for e in entities}
    relations = [
        r for r in graph.get("relations", [])
        if isinstance(r, dict)
        and r.get("from") in ids and r.get("to") in ids
        and r.get("from") != r.get("to")
        and (r.get("type") == GENERIC_RELATION or r.get("type") in RELATIONS_BY_NAME)
        and not _check_evidence(r, "", valid_indices)
        and _quote_matches(r.get("quote", ""), r.get("evidence"), texts_by_i)
    ]
    dropped_e = len(graph.get("entities", [])) - len(entities)
    dropped_r = len(graph.get("relations", [])) - len(relations)
    logger.warning(
        "Salvaged chunk: dropped %d entities and %d relations that failed "
        "validation after retries (first errors: %s)",
        dropped_e, dropped_r, errors[:3],
    )
    return {"entities": entities, "relations": relations}
