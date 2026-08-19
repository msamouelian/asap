"""
Tool allowlist and call validator.

Enforces two security properties:
  1. Only approved tools are offered to the LLM (enforced at discovery time
     in mcp/client.py via get_allowed_tool_names()).
  2. Only approved tool calls with valid arguments are executed (enforced at
     call time in llm/agent.py via validate_tool_call()).

To add a new tool: update allowed_tools.yaml, review, and rebuild the image.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

log = logging.getLogger(__name__)

_ALLOWLIST_PATH = Path(__file__).parent / "allowed_tools.yaml"

_PYTHON_TYPES: dict[str, type] = {
    "str":   str,
    "int":   int,
    "float": float,
    "bool":  bool,
    "list":  list,
    "dict":  dict,
}


# ── Pydantic models for the YAML schema ──────────────────────────────────────

class ToolArgDef(BaseModel):
    name: str
    type: Literal["str", "int", "float", "bool", "list", "dict"]
    required: bool = True
    description: str = ""


class AllowedTool(BaseModel):
    name: str
    description: str = ""
    arguments: list[ToolArgDef] = []


class ToolAllowlist(BaseModel):
    tools: list[AllowedTool]


# ── Singleton cache ───────────────────────────────────────────────────────────

_allowlist: ToolAllowlist | None = None


def get_allowlist() -> ToolAllowlist:
    global _allowlist
    if _allowlist is None:
        data = yaml.safe_load(_ALLOWLIST_PATH.read_text(encoding="utf-8"))
        _allowlist = ToolAllowlist.model_validate(data)
        log.info(
            "Tool allowlist loaded: %s",
            [t.name for t in _allowlist.tools],
        )
    return _allowlist


def get_allowed_tool_names() -> frozenset[str]:
    return frozenset(t.name for t in get_allowlist().tools)


# ── Validation ────────────────────────────────────────────────────────────────

class ToolCallError(ValueError):
    """Raised when a tool call fails allowlist validation."""


def validate_tool_call(name: str, arguments: dict) -> None:
    """
    Validate a tool call against the allowlist.

    Raises ToolCallError if:
      - the tool name is not in the allowlist
      - an unexpected argument is present
      - a required argument is missing
      - an argument value is the wrong type
    """
    allowlist = get_allowlist()
    tool_def = next((t for t in allowlist.tools if t.name == name), None)

    if tool_def is None:
        raise ToolCallError(
            f"Tool '{name}' is not in the approved tool list. "
            f"Approved tools: {sorted(t.name for t in allowlist.tools)}."
        )

    allowed_arg_names = {a.name for a in tool_def.arguments}

    # Reject unexpected arguments.
    extra = set(arguments.keys()) - allowed_arg_names
    if extra:
        raise ToolCallError(
            f"Tool '{name}' received unexpected argument(s): {sorted(extra)}."
        )

    # Check each defined argument.
    for arg_def in tool_def.arguments:
        if arg_def.name not in arguments:
            if arg_def.required:
                raise ToolCallError(
                    f"Tool '{name}' is missing required argument '{arg_def.name}'."
                )
            continue

        value = arguments[arg_def.name]
        expected_type = _PYTHON_TYPES[arg_def.type]

        # bool is a subclass of int in Python — check bool before int.
        if arg_def.type == "int" and isinstance(value, bool):
            raise ToolCallError(
                f"Tool '{name}' argument '{arg_def.name}': "
                f"expected int, got bool."
            )

        if not isinstance(value, expected_type):
            raise ToolCallError(
                f"Tool '{name}' argument '{arg_def.name}': "
                f"expected {arg_def.type}, got {type(value).__name__}."
            )
