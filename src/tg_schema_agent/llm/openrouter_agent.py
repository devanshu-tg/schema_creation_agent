"""OpenRouter-backed agentic loop (mirror of chat_agent.run_agentic_turn).

This module reproduces the same ReAct-style tool loop as
``chat_agent.run_agentic_turn`` but uses the OpenAI SDK pointed at
OpenRouter as the underlying LLM provider — letting us use Anthropic
Claude (Sonnet 4.6 / Opus / Haiku) or any other model OpenRouter exposes.

The high-level shape is identical to ``chat_agent.run_agentic_turn``:
  - Build a ToolContext from the workspace
  - Drive a tool-calling loop (max 30 iterations)
  - Yield SSE event tuples (``thinking`` / ``tool_call`` / ``tool_result`` /
    ``schema_update`` / ``final`` / ``error``)
  - Terminate on ``finalize_schema``, ``ask_user``, ``reply_to_user``
    (in TERMINATING_TOOLS) or when the model emits text with no tool call.

Only the LLM-touching parts differ:
  - Tool schemas are translated Gemini-FunctionDeclaration → OpenAI function
  - Conversation history is rebuilt in OpenAI chat-completions format
  - Response parsing uses ``choices[0].message`` instead of
    ``candidates[0].content.parts``
  - Tool results are sent back as ``{role: "tool", tool_call_id, content}``
    messages, not as Gemini FunctionResponse parts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tg_schema_agent.enums import UseCase
from tg_schema_agent.llm.chat_agent import (
    _AGENTIC_SYSTEM_INSTRUCTION,
    MAX_AGENT_ITERS,
    ChatMessage,
)


DEFAULT_OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "anthropic/claude-sonnet-4.6"
)
DEFAULT_OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)


def is_available() -> bool:
    if not os.environ.get("OPENROUTER_API_KEY"):
        return False
    try:
        import openai  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Tool schema translation: Gemini FunctionDeclaration -> OpenAI function
# ---------------------------------------------------------------------------


def _gemini_schema_to_jsonschema(schema: Any) -> dict[str, Any]:
    """Recursively convert a google.genai Schema object to a plain JSON
    Schema dict. The two formats use the same field names; we just need to
    materialise the proto objects as dicts (and lowercase ``type``)."""
    if schema is None:
        return {}
    if isinstance(schema, dict):
        # Already a dict — passthrough but normalise nested 'type'
        out = dict(schema)
        if "type" in out and isinstance(out["type"], str):
            out["type"] = out["type"].lower()
        if "properties" in out and isinstance(out["properties"], dict):
            out["properties"] = {
                k: _gemini_schema_to_jsonschema(v)
                for k, v in out["properties"].items()
            }
        if "items" in out:
            out["items"] = _gemini_schema_to_jsonschema(out["items"])
        return out

    out: dict[str, Any] = {}
    t = getattr(schema, "type", None) or getattr(schema, "type_", None)
    if t:
        out["type"] = str(t).lower().replace("type.", "")
    desc = getattr(schema, "description", None)
    if desc:
        out["description"] = desc
    enum = getattr(schema, "enum", None)
    if enum:
        out["enum"] = list(enum)
    required = getattr(schema, "required", None)
    if required:
        out["required"] = list(required)
    properties = getattr(schema, "properties", None)
    if properties:
        out["properties"] = {
            k: _gemini_schema_to_jsonschema(v) for k, v in properties.items()
        }
    items = getattr(schema, "items", None)
    if items:
        out["items"] = _gemini_schema_to_jsonschema(items)
    return out


def build_openai_tools() -> list[dict[str, Any]]:
    """Translate the project's Gemini FunctionDeclarations into OpenAI's
    ``tools=[{type:"function", function:{name,description,parameters}}]``
    shape that OpenRouter (and OpenAI directly) accepts."""
    from tg_schema_agent.llm.tools import build_function_declarations

    gemini_tools = build_function_declarations()
    out: list[dict[str, Any]] = []
    # build_function_declarations returns a list[Tool] each containing
    # function_declarations: list[FunctionDeclaration]. Flatten.
    for tool in gemini_tools:
        decls = getattr(tool, "function_declarations", None) or []
        for fd in decls:
            name = getattr(fd, "name", None)
            description = getattr(fd, "description", None)
            params = _gemini_schema_to_jsonschema(getattr(fd, "parameters", None))
            if not name:
                continue
            # OpenAI strict mode requires "type":"object" and "properties" present.
            if "type" not in params:
                params["type"] = "object"
            params.setdefault("properties", {})
            out.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": description or "",
                    "parameters": params,
                },
            })
    return out


# ---------------------------------------------------------------------------
# Message history translation: ChatMessage -> OpenAI chat-completions messages
# ---------------------------------------------------------------------------


def _history_to_openai_messages(
    history: list[ChatMessage], latest_user: str
) -> list[dict[str, Any]]:
    """Build the ``messages`` array for the OpenAI chat-completions call.

    Prior turns are flattened to plain text — same as
    ``chat_agent._history_to_contents`` — and the latest user message is
    appended. Roles map ``user`` -> ``user``, ``agent`` -> ``assistant``.
    """
    msgs: list[dict[str, Any]] = []
    for m in history:
        if m.role not in ("user", "agent"):
            continue
        text = (m.content or "").strip()
        if not text:
            continue
        role = "user" if m.role == "user" else "assistant"
        msgs.append({"role": role, "content": text})
    if latest_user:
        msgs.append({"role": "user", "content": latest_user})
    return msgs


# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------


async def run_agentic_turn(
    workspace_dir: Path,
    user_message: str,
    use_case: UseCase,
    chat_history: list[ChatMessage],
    *,
    user_prompt_for_scoring: str | None = None,
    model: str | None = None,
    max_iters: int = MAX_AGENT_ITERS,
):
    """SSE event generator — mirror of chat_agent.run_agentic_turn over
    OpenRouter. See chat_agent for the canonical event taxonomy."""
    if not is_available():
        yield "error", {
            "message": "OPENROUTER_API_KEY not set or openai SDK missing.",
            "code": "no_api_key",
        }
        return

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        yield "error", {
            "message": f"openai SDK not installed: {exc}",
            "code": "no_sdk",
        }
        return

    from tg_schema_agent.llm.tools import (
        MUTATING_TOOLS,
        TERMINATING_TOOLS,
        ToolContext,
        execute_tool,
    )

    try:
        ctx = ToolContext.load(
            workspace_dir, use_case, user_prompt=user_prompt_for_scoring
        )
    except Exception as exc:  # noqa: BLE001
        yield "error", {
            "message": f"Failed to load workspace context: {exc}",
            "code": "workspace_load",
        }
        return

    is_first_design = (
        not ctx.working_schema.vertices and not ctx.working_schema.edges
    )

    # --- Cross-turn session memory (fixes "asks the same question every
    # turn"). We persist a compact record of what's already established and
    # replay it as a SESSION MEMORY block so the agent has continuity across
    # turns the way Claude Code does. See llm/session_memory.py. ---
    from tg_schema_agent.llm.session_memory import SessionMemory

    mem = SessionMemory.load(workspace_dir)
    turn_events: list[dict[str, Any]] = []

    def _flush_session_memory(last_question: str | None = None) -> None:
        """Fold this turn's tool activity into memory and persist it.

        Called from EVERY exit branch so the next turn inherits what we did.
        """
        for ev in turn_events:
            mem.record_tool_event(
                ev["name"], ev["ok"], ev["summary"], ev.get("args")
            )
        mem.set_schema_summary(ctx.working_schema)
        if last_question:
            mem.note_question(last_question)
        mem.save(workspace_dir)

    # Merge the SESSION MEMORY block into the single system message — some
    # OpenRouter model adapters collapse or reject a second system message,
    # so we keep it to one.
    _system_content = _AGENTIC_SYSTEM_INSTRUCTION
    _mem_block = mem.render_for_prompt()
    if _mem_block:
        _system_content = _AGENTIC_SYSTEM_INSTRUCTION + "\n\n" + _mem_block
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_content},
    ]
    history_msgs = _history_to_openai_messages(chat_history, user_message)
    if not history_msgs:
        kickoff = (
            "Hi, I just uploaded data. Before you look at it, ask me what "
            "business decision I'm trying to make with this graph."
        )
        history_msgs = [{"role": "user", "content": kickoff}]
    messages.extend(history_msgs)

    client = AsyncOpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=DEFAULT_OPENROUTER_BASE_URL,
        default_headers={
            # OpenRouter rankings/attribution headers — optional but polite.
            "HTTP-Referer": os.environ.get(
                "OPENROUTER_REFERER", "https://github.com/devanshu-tg/schema_creation_agent"
            ),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Autograph"),
        },
    )
    model_name = model or DEFAULT_OPENROUTER_MODEL
    tools = build_openai_tools()

    terminating_payload: dict[str, Any] | None = None
    terminating_kind: str | None = None
    accumulated_text_parts: list[str] = []

    _budget: dict[str, int] = {
        "inspect_column": 0,
        "get_sample_rows": 0,
        "find_columns_matching": 0,
        "analyze_column_distribution": 0,
    }
    # Caps sized for WIDE datasets — see chat_agent for rationale. A 20-25
    # column CSV needs enough analyze_column_distribution calls to examine
    # every column for vertex promotion, or the agent falls back to a flat
    # 6-vertex schema.
    _BUDGET_LIMITS = {
        "inspect_column": 28,
        "get_sample_rows": 12,
        "find_columns_matching": 18,
        "analyze_column_distribution": 30,
    }

    for iteration in range(max_iters):
        try:
            resp = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.3,
                # Sonnet 4.6 + adaptive thinking is automatic on OpenRouter;
                # we don't need to pass a budget. For models that don't
                # support tools, this still returns plain text and we'll
                # treat it as a conversational reply.
            )
        except Exception as exc:  # noqa: BLE001
            yield "error", {
                "message": f"OpenRouter call failed: {exc}",
                "code": "openrouter_failure",
            }
            return

        choice = (resp.choices or [None])[0]
        if choice is None:
            yield "error", {
                "message": "OpenRouter returned no choices.",
                "code": "no_candidates",
            }
            return

        msg = choice.message
        finish_reason = getattr(choice, "finish_reason", None) or ""

        # Carry the assistant turn forward — OpenAI requires the exact
        # assistant message (with tool_calls) before the matching tool
        # results.
        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            assistant_msg["content"] = msg.content
        else:
            assistant_msg["content"] = None
        tool_calls_raw = list(getattr(msg, "tool_calls", None) or [])
        if tool_calls_raw:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls_raw
            ]
        messages.append(assistant_msg)

        # Stream any text the model emitted before we look at tool calls.
        if msg.content and msg.content.strip():
            yield "thinking", {"text": msg.content.strip()}
            accumulated_text_parts.append(msg.content.strip())

        produced_any_tool_call = bool(tool_calls_raw)
        # Execute every tool call sequentially (the model can issue several
        # in one turn; OpenAI returns them all in `tool_calls`).
        for idx, tc in enumerate(tool_calls_raw):
            fc_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            call_id = tc.id or f"tc-{iteration}-{idx}"
            yield "tool_call", {"id": call_id, "name": fc_name, "args": args}

            if fc_name in _BUDGET_LIMITS:
                _budget[fc_name] += 1
                if _budget[fc_name] > _BUDGET_LIMITS[fc_name]:
                    result = {
                        "ok": False,
                        "summary": (
                            f"Budget exceeded: {fc_name} can only be called "
                            f"{_BUDGET_LIMITS[fc_name]} times per turn. "
                            "Move on to the next stage."
                        ),
                        "data": {"budget_exceeded": True},
                    }
                else:
                    result = await execute_tool(ctx, fc_name, args)
            else:
                result = await execute_tool(ctx, fc_name, args)

            yield "tool_result", {
                "id": call_id,
                "name": fc_name,
                "ok": bool(result.get("ok", False)),
                "summary": result.get("summary", ""),
            }

            # Capture for session memory so the next turn knows we did this.
            turn_events.append({
                "name": fc_name,
                "ok": bool(result.get("ok", False)),
                "summary": result.get("summary", ""),
                "args": args,
            })

            if fc_name in MUTATING_TOOLS:
                yield "schema_update", {
                    "schema": ctx.working_schema.model_dump(mode="json")
                }

            if fc_name in TERMINATING_TOOLS and result.get("ok", False):
                terminating_kind = fc_name
                terminating_payload = result

            # Tool result message — must match the tool_call_id from the
            # assistant turn or the API will 400.
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps({
                    "ok": bool(result.get("ok", False)),
                    "summary": result.get("summary", ""),
                    "data": result.get("data"),
                }, default=str),
            })

        if terminating_kind:
            break

        # No tool calls and no more iterations expected — the model
        # answered with prose. Exit so we can emit it as the final reply.
        if not produced_any_tool_call:
            break
    else:
        # Hit max iters without a terminating call.
        ctx.persist_schema()
        _flush_session_memory()
        last_summary = accumulated_text_parts[-1][:240] if accumulated_text_parts else ""
        yield "final", {
            "type": "answer",
            "message": (
                last_summary
                or "Done — the requested operation ran. (Ask me to check the "
                "graph state if you want details.)"
            ),
            "suggested_replies": [
                "Show graph state",
                "Generate starter queries",
                "Run a query",
            ],
            "schema": ctx.working_schema.model_dump(mode="json"),
            "validation": None,
            "score": None,
        }
        return

    # Build the final payload — terminating-tool branches mirror chat_agent.
    if terminating_kind == "ask_user":
        question_data = (terminating_payload or {}).get("data") or {}
        _question_text = question_data.get(
            "question",
            (terminating_payload or {}).get("summary", "") if terminating_payload else "",
        )
        # Persist BEFORE returning — previously the ask_user branch dropped
        # any business context / assumptions recorded earlier this turn,
        # which is exactly why the agent re-asked next turn.
        ctx.persist_schema()
        _flush_session_memory(last_question=_question_text)
        yield "final", {
            "type": "question",
            "message": _question_text,
            "suggested_replies": question_data.get("suggested_replies", []),
            "schema": ctx.working_schema.model_dump(mode="json")
            if (ctx.working_schema.vertices or ctx.working_schema.edges)
            else None,
            "validation": None,
            "score": None,
        }
        return

    if terminating_kind == "reply_to_user":
        reply_data = (terminating_payload or {}).get("data") or {}
        ctx.persist_schema()
        _flush_session_memory()
        yield "final", {
            "type": "answer",
            "message": reply_data.get(
                "message",
                (terminating_payload or {}).get("summary", "") if terminating_payload else "",
            ),
            "suggested_replies": reply_data.get("suggested_replies", []),
            "schema": ctx.working_schema.model_dump(mode="json")
            if (ctx.working_schema.vertices or ctx.working_schema.edges)
            else None,
            "validation": None,
            "score": None,
        }
        return

    # Conversational reply (model produced only text, no terminating tool).
    if not terminating_kind and accumulated_text_parts and not ctx.working_schema.vertices:
        ctx.persist_schema()
        _flush_session_memory()
        yield "final", {
            "type": "answer",
            "message": " ".join(accumulated_text_parts).strip(),
            "suggested_replies": [],
            "schema": None,
            "validation": None,
            "score": None,
        }
        return

    # Defensive kickoff fallback — see chat_agent for rationale.
    if (
        not terminating_kind
        and not accumulated_text_parts
        and not ctx.working_schema.vertices
        and is_first_design
    ):
        default_q = (
            "What decision are you trying to make with this data? "
            "That'll shape how I design the graph."
        )
        default_replies = [
            "Find fraud rings",
            "Detect mule accounts",
            "Build a Customer 360",
            "Just explore",
        ]
        _flush_session_memory(last_question=default_q)
        yield "final", {
            "type": "question",
            "message": default_q,
            "suggested_replies": default_replies,
            "schema": None,
            "validation": None,
            "score": None,
        }
        return

    # Default + finalize_schema path — validate + score + emit.
    from tg_schema_agent import scorer as scorer_mod
    from tg_schema_agent import validator as validator_mod

    val = validator_mod.validate(ctx.working_schema)
    score = scorer_mod.score_schema(
        ctx.working_schema, val, ctx.pattern, user_prompt=ctx.user_prompt
    )
    ctx.persist_schema()
    _flush_session_memory()

    final_message = ""
    final_chips: list[str] = []
    if terminating_kind == "finalize_schema" and terminating_payload:
        data = terminating_payload.get("data") or {}
        final_message = (
            data.get("user_summary")
            or terminating_payload.get("summary")
            or f"Schema designed: {len(ctx.working_schema.vertices)} vertices, "
            f"{len(ctx.working_schema.edges)} edges."
        )
        final_chips = list(data.get("suggested_replies") or [])
    elif accumulated_text_parts:
        final_message = " ".join(accumulated_text_parts).strip()
    else:
        final_message = (
            f"Designed a schema with {len(ctx.working_schema.vertices)} vertices "
            f"and {len(ctx.working_schema.edges)} edges. Score: {score.total}/100."
        )

    yield "final", {
        "type": "propose_schema" if terminating_kind == "finalize_schema" else "answer",
        "message": final_message,
        "suggested_replies": final_chips,
        "schema": ctx.working_schema.model_dump(mode="json"),
        "validation": val.model_dump(mode="json"),
        "score": score.model_dump(mode="json"),
    }
