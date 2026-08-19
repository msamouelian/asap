"""Pack a collection's text surface into LLM-sized extraction chunks.

Text units keep their original surface index `i` inside every chunk, so
evidence indices the LLM returns are globally valid for the whole surface.
"""

from __future__ import annotations

from typing import Any

from kggenerator import config


def _est_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token for English prose)."""
    return len(text) // 4 + 1


def chunk_surface(surface: dict[str, Any]) -> list[dict[str, Any]]:
    """Split the surface's text units into chunks under the token budget.

    Units are packed in surface order (collection notes, then AO titles and
    notes, then agent material), which keeps related units — an AO title and
    its own notes — adjacent in the same chunk wherever they fit.
    """
    budget = config.CHUNK_TOKEN_BUDGET
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append({"collection": surface["collection"], "texts": current})
            current = []
            current_tokens = 0

    for unit in surface["texts"]:
        t = _est_tokens(unit["text"]) + 20  # unit JSON overhead
        if current and current_tokens + t > budget:
            flush()
        current.append(unit)
        current_tokens += t
    flush()
    return chunks
