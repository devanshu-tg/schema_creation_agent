"""Gemini-powered schema critic.

Runs AFTER the deterministic validator + scorer. Asks Gemini to qualitatively
grade the schema, give a letter grade, and write 3 strengths + 3 improvements.
The numeric score from `scorer.py` is still the contract — this critic just
adds plain-language judgment alongside.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tg_schema_agent.enums import UseCase
from tg_schema_agent.models import Pattern, Schema, SchemaScore, ValidationResult
from tg_schema_agent.patterns import load_patterns

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")


class CriticReview(BaseModel):
    """Plain-language judgment from Gemini, alongside the deterministic score."""

    grade: str = Field(..., description="Letter grade: A+ / A / A- / B+ / B / B- / C+ / C / C- / D / F")
    overall_judgment: str = Field(..., description="One-paragraph summary.")
    strengths: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    motive_match: str = Field(
        "", description="How well the schema serves the user's stated motive."
    )
    next_step_suggestion: str = Field(
        "", description="A concrete next action the user could take to make it even better."
    )


_SYSTEM_INSTRUCTION = """You are a senior TigerGraph solutions architect reviewing
a colleague's graph schema design. The schema was just designed by an AI agent
from the user's CSV and intent.

Your job: write a short, honest, expert review. Be specific. Cite real vertex
and edge names. Reference the user's stated motive. Don't be sycophantic —
if something is missing or sub-optimal, say so.

Return ONLY a JSON object with this exact shape:

{
  "grade": "A+ | A | A- | B+ | B | B- | C+ | C | C- | D | F",
  "overall_judgment": "1-2 sentence summary of how good the schema is.",
  "strengths": [
    "3-5 concrete strengths, each tied to a real vertex/edge/attribute.",
    "...",
    "..."
  ],
  "improvements": [
    "3-5 concrete suggestions. Each should name a specific addition or change.",
    "...",
    "..."
  ],
  "motive_match": "1-2 sentences on how well the schema serves the user's stated motive.",
  "next_step_suggestion": "One concrete next action that would most improve the schema."
}

No prose outside the JSON. No markdown fences. Just the JSON.
"""


def is_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def critique(
    schema: Schema,
    validation: ValidationResult,
    score: SchemaScore,
    pattern: Pattern | None = None,
    user_prompt: str | None = None,
    use_case: UseCase | None = None,
    model: str | None = None,
) -> CriticReview | None:
    """Ask Gemini for a plain-language review. Returns None if AI is unavailable
    or anything goes wrong (caller treats critic as a nice-to-have).
    """
    if not is_available():
        return None

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        return None

    if pattern is None and use_case is not None:
        try:
            pattern = load_patterns()[use_case]
        except KeyError:
            pattern = None

    payload = {
        "user_intent": user_prompt or "(no specific intent given — generic best schema)",
        "use_case": (use_case or schema.use_case).value if use_case else schema.use_case.value,
        "schema_summary": {
            "name": schema.name,
            "vertices": [
                {
                    "name": v.name,
                    "primary_id": v.primary_id,
                    "attributes": [a.name for a in v.attributes],
                    "rationale": v.rationale,
                }
                for v in schema.vertices
            ],
            "edges": [
                {
                    "name": e.name,
                    "from": e.from_vertex,
                    "to": e.to_vertex,
                    "direction": e.direction.value if hasattr(e.direction, "value") else str(e.direction),
                    "reverse_edge_name": e.reverse_edge_name,
                }
                for e in schema.edges
            ],
            "target_questions": [q.id for q in schema.target_questions],
        },
        "deterministic_score": {
            "total": score.total,
            "breakdown": score.breakdown,
            "answerable_questions": validation.answerable_questions,
            "unanswerable_questions": validation.unanswerable_questions,
        },
        "reference_pattern_name": pattern.name if pattern else None,
    }

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)
    model_name = model or DEFAULT_MODEL

    _thinking_default = 32768 if "pro" in model_name.lower() else 0
    try:
        _thinking_budget = int(os.environ.get("GEMINI_THINKING_BUDGET", _thinking_default))
    except ValueError:
        _thinking_budget = _thinking_default
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=json.dumps(payload, indent=2, default=str),
            config=genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.3,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=_thinking_budget),
            ),
        )
        raw = (resp.text or "").strip()
        if not raw:
            return None
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        data = json.loads(raw)
        return CriticReview.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — critic is best-effort
        log.warning("Critic failed: %s", exc)
        return None
