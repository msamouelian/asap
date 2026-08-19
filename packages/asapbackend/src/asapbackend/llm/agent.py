"""
Agentic loop — orchestrates tool calls between the LLM and MCP servers.

Always uses streaming so tokens reach the client immediately on every turn.
Tool-call detection is done by accumulating delta chunks and inspecting
finish_reason — no second API call needed.

SSE event types emitted:
  {"type": "thinking_token", "content": "<reasoning chunk>"}
  {"type": "thinking_done"}
  {"type": "tool_start",  "tool": "<name>", "args": "<raw json>"}
  {"type": "tool_result", "tool": "<name>", "summary": "<first 200 chars>"}
  {"type": "token",       "content": "<text chunk>"}
  {"type": "usage",       "prompt_tokens": N, "context_pct": F}
  {"type": "done"}
  {"type": "error",       "detail": "<message>"}

Reasoning token detection:
  LM Studio returns delta.reasoning (stored in model_extra by the OpenAI SDK).
  OpenAI o-series returns delta.reasoning_content (also model_extra).
  Both are captured and stored in the conversation_message.reasoning column.
  On re-submission, settings.reasoning_include_in_context controls whether
  they are prepended as <think>…</think> in the assistant turn content.

Context usage:
  stream_options={"include_usage": True} is passed to every API call so the
  inference server appends a final usage chunk with prompt_tokens.  A "usage"
  SSE event is emitted after every LLM call in the loop (including tool-call
  rounds) so the UI updates live.  On the final stop turn the token count is
  also persisted to the conversation row.  If the server does not return usage
  (chunk.usage is None) the event is silently omitted.
"""

import json
import logging
import re
from typing import AsyncIterator

import psycopg

from asapbackend import mcp
from asapbackend.config import settings
from asapbackend.llm.client import get_llm_client, sampling_kwargs
from asapbackend.services.conversation import append_message, update_prompt_tokens
from asapbackend.tools import hybrid_search, kg_search, record_text
from asapbackend.tools.validator import ToolCallError, validate_tool_call

log = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 50


def _canonical_tool_name(name: str, known: set[str]) -> str:
    """Map a model-emitted tool name onto a known tool, tolerating the
    mangling gpt-oss-class models produce on hyphenated names (underscores
    for hyphens, stray trailing punctuation: 'get_record_text?'). Unknown
    names are reduced to their legal characters so they can sit safely in
    the message history while the validator rejects them."""
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]", "", (name or "").strip())
    candidate = cleaned.lower().replace("_", "-")
    # Also tolerate a namespace prefix ('functions.get_record_text').
    bare = candidate.rsplit(".", 1)[-1]
    for k in known:
        if k.lower() in (candidate, bare):
            return k
    if cleaned != name:
        log.warning("Sanitized unrecognized tool name %r -> %r", name, cleaned)
    return cleaned or "unknown-tool"


def _extract_reasoning(delta) -> str | None:
    """Pull reasoning content from a streaming delta.

    LM Studio uses 'reasoning'; OpenAI o-series uses 'reasoning_content'.
    Both arrive as extra (non-standard) fields stored in model_extra by the
    OpenAI Pydantic SDK.
    """
    extra: dict = getattr(delta, 'model_extra', None) or {}
    return extra.get('reasoning') or extra.get('reasoning_content') or None


def _usage_event(prompt_tokens: int) -> dict:
    context_pct = round(prompt_tokens / settings.inference_context_window_tokens * 100, 1)
    return {"type": "usage", "prompt_tokens": prompt_tokens, "context_pct": context_pct}


async def run(
    messages: list[dict],
    conn: psycopg.AsyncConnection,
    conversation_id: str,
    temperature: float | None = None,
) -> AsyncIterator[dict]:
    """
    Drive the agent loop for one conversation turn, always streaming.

    messages:        full OpenAI-format history including the new user message.
    conn:            database connection for immediate message persistence.
    conversation_id: UUID string of the current conversation.
    temperature:     sampling temperature for this turn; falls back to
                     settings.inference_temperature when None.
    """
    client = get_llm_client()
    # MCP-served tools plus backend-native tools (canned queries the LLM
    # invokes by name instead of copying large Cypher through JSON).
    tools = mcp.client.get_cached_tools() + [
        hybrid_search.TOOL_DEFINITION,
        record_text.TOOL_DEFINITION,
        kg_search.TOOL_DEFINITION,
    ]
    tool_kwargs = {"tools": tools, "tool_choice": "auto"} if tools else {}

    # Duplicate-call guard: every tool here is read-only and deterministic,
    # so an exact repeat of (tool, arguments) within one turn can only waste
    # context and rounds. Observed with kg-search and hybrid-search: the
    # model repeats a call hoping for different information instead of
    # changing the argument. Repeats get a short notice, not a re-execution.
    executed_calls: set[tuple[str, str]] = set()

    try:
        for _ in range(_MAX_TOOL_ROUNDS):
            stream = await client.chat.completions.create(
                model=settings.inference_model,
                messages=messages,
                **sampling_kwargs(temperature),
                stream=True,
                stream_options={"include_usage": True},
                **(
                    {"extra_body": {"min_p": settings.inference_min_p}}
                    if settings.inference_min_p > 0 else {}
                ),
                **tool_kwargs,
            )

            # Accumulators for this streaming call.
            content_chunks: list[str] = []
            reasoning_chunks: list[str] = []
            # Keyed by tool-call index; each value is the partial tool call dict.
            tool_calls_acc: dict[int, dict] = {}
            finish_reason: str | None = None
            reasoning_done_emitted: bool = False
            prompt_tokens: int | None = None

            async for chunk in stream:
                # Usage-only trailing chunk emitted by the server when
                # stream_options={"include_usage": True} is set.
                if chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens

                # Skip the usage-only chunk that has no choices.
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # finish_reason arrives on the last chunk.
                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                # Reasoning tokens — emitted before content by the model.
                reasoning_chunk = _extract_reasoning(delta)
                if reasoning_chunk:
                    reasoning_chunks.append(reasoning_chunk)
                    yield {"type": "thinking_token", "content": reasoning_chunk}

                # Content tokens — seal the thinking block on the first one.
                if delta.content:
                    if reasoning_chunks and not reasoning_done_emitted:
                        yield {"type": "thinking_done"}
                        reasoning_done_emitted = True
                    content_chunks.append(delta.content)
                    yield {"type": "token", "content": delta.content}

                # Accumulate tool-call deltas — name and arguments arrive in
                # fragments across multiple chunks.
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc_delta.id or "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        entry = tool_calls_acc[idx]
                        if tc_delta.id:
                            entry["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                entry["function"]["name"] += tc_delta.function.name
                            if tc_delta.function.arguments:
                                entry["function"]["arguments"] += tc_delta.function.arguments

            # ── Emit usage after every LLM call (tool rounds + final stop) ────
            # Gives the UI live updates as the context grows across rounds.
            if prompt_tokens is not None:
                yield _usage_event(prompt_tokens)

            # ── Branch on finish_reason ───────────────────────────────────────

            reasoning_text: str | None = "".join(reasoning_chunks) or None

            if finish_reason == "tool_calls":
                # Seal reasoning block if the model reasoned before tool calls.
                if reasoning_chunks and not reasoning_done_emitted:
                    yield {"type": "thinking_done"}

                tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]

                # Canonicalize tool names BEFORE persisting or dispatching.
                # gpt-oss-class models occasionally mangle hyphenated names
                # ('get_record_text?'); an illegal function name in a
                # resubmitted assistant turn makes the inference server 500
                # on every subsequent round (observed 2026-08-13), so the
                # mangled form must never enter the history. Recognizable
                # names map onto the real tool; unrecognizable ones are
                # reduced to legal characters and left for the validator to
                # reject gracefully.
                known_names = {t["function"]["name"] for t in tools}
                for tc in tool_calls:
                    tc["function"]["name"] = _canonical_tool_name(
                        tc["function"]["name"], known_names
                    )

                # Persist the assistant's tool-call decision (with any reasoning).
                assistant_turn = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": tool_calls,
                }
                messages.append(assistant_turn)
                await append_message(
                    conn, conversation_id, "assistant", assistant_turn,
                    reasoning=reasoning_text,
                )

                # Execute each tool and persist the result.
                for tc in tool_calls:
                    name = tc["function"]["name"]
                    raw_args = tc["function"]["arguments"] or "{}"
                    args = json.loads(raw_args)
                    yield {"type": "tool_start", "tool": name, "args": raw_args}

                    call_key = (name, json.dumps(args, sort_keys=True))
                    if call_key in executed_calls:
                        result = (
                            f"You already called {name} with these exact "
                            "arguments in this conversation turn. The tool "
                            "is deterministic — the result is identical to "
                            "the one you received; refer to it. To get "
                            "DIFFERENT information, change the arguments "
                            "(a different name, topic, uri, or query) or "
                            "use a different tool."
                        )
                        yield {"type": "tool_result", "tool": name,
                               "summary": result[:200]}
                        messages.append({
                            "role": "tool", "tool_call_id": tc["id"],
                            "content": result,
                        })
                        await append_message(
                            conn, conversation_id, "tool",
                            {"role": "tool", "tool_call_id": tc["id"],
                             "content": result},
                        )
                        continue
                    executed_calls.add(call_key)

                    try:
                        # Enforcement point 2 — validate before executing.
                        validate_tool_call(name, args)
                        if name == hybrid_search.TOOL_NAME:
                            result = await hybrid_search.run(**args)
                        elif name == record_text.TOOL_NAME:
                            result = await record_text.run(**args)
                        elif name == kg_search.TOOL_NAME:
                            result = await kg_search.run(**args)
                        else:
                            result = await mcp.client.call_tool(name, args)
                    except ToolCallError as exc:
                        log.warning("Blocked tool call '%s': %s", name, exc)
                        result = f"Tool call rejected by security policy: {exc}"
                    except Exception as exc:
                        result = f"Tool error: {exc}"

                    yield {"type": "tool_result", "tool": name, "summary": result[:200]}

                    tool_turn = {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result,
                    }
                    messages.append(tool_turn)
                    await append_message(conn, conversation_id, "tool", tool_turn)

                # Loop — send updated messages back to the LLM.

            else:
                # finish_reason == "stop" (or None for models that omit it).
                # Tokens were already streamed; just persist the full response.
                if content_chunks:
                    await append_message(
                        conn, conversation_id, "assistant", "".join(content_chunks),
                        reasoning=reasoning_text,
                    )
                # Persist the final token count so the conversation list can
                # show context-usage percentage without an active stream.
                if prompt_tokens is not None:
                    await update_prompt_tokens(conn, conversation_id, prompt_tokens)
                yield {"type": "done"}
                return

        yield {"type": "error", "detail": "Agent reached maximum tool-call limit."}

    except Exception as exc:
        log.exception("Agent loop error")
        yield {"type": "error", "detail": str(exc)}
