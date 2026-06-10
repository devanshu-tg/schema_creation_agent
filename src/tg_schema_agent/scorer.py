"""Schema scorer: 10-dimension quality score (9 from PDF step 10 + motive_alignment).

Also exposes `compute_confidence` — a composite High/Medium/Low label
that drives the Autograph OutcomesPanel header. The composite is
deliberately conservative: it requires BOTH structural quality AND
outcome coverage to claim High.
"""

from __future__ import annotations

import re
from typing import Literal

from tg_schema_agent.config import load_rules_config
from tg_schema_agent.enums import DataKind, EdgeDirection
from tg_schema_agent.models import (
    Assumption,
    Pattern,
    Schema,
    SchemaScore,
    ValidationResult,
)


def _query_coverage(validation: ValidationResult, schema: Schema) -> int:
    total = len(schema.target_questions) or 1
    return int(round(len(validation.answerable_questions) / total * 100))


def _traversal_efficiency(schema: Schema) -> int:
    score = 100
    for e in schema.edges:
        if e.direction == EdgeDirection.DIRECTED and not e.reverse_edge_name:
            score -= 10
    long_paths = sum(1 for q in schema.target_questions if q.max_hops >= 5)
    score -= 15 * long_paths
    return max(0, score)


def _entity_reuse(schema: Schema) -> int:
    incoming_sources: dict[str, set[str]] = {}
    for e in schema.edges:
        incoming_sources.setdefault(e.to_vertex, set()).add(e.from_vertex)
    shared = {"Device", "IPAddress", "Email", "Phone", "Address", "Card"}
    bonus = 0
    for v in schema.vertices:
        if v.name in shared and len(incoming_sources.get(v.name, set())) >= 1:
            bonus += 15
    return min(100, bonus)


def _attribute_placement(validation: ValidationResult) -> int:
    bad = next((c for c in validation.checks if c.id == "C7_attribute_placement"), None)
    if not bad or bad.passed:
        return 100
    return max(0, 100 - 10 * len(bad.detail.split(";")))


def _simplicity(schema: Schema) -> int:
    """Reward thorough use of the data — don't penalize larger, richer schemas.

    The previous bucket aggressively punished schemas over 10 elements which
    encouraged the agent to ignore meaningful columns. Now we reward 15-40
    elements (the sweet spot for "uses every column without over-modeling")
    and only drop below 80 when the schema is genuinely sprawling (50+).
    """
    total = len(schema.vertices) + len(schema.edges)
    if total <= 15:
        return 100
    if total <= 25:
        return 100  # still reward — using every column is good
    if total <= 40:
        return 90
    if total <= 60:
        return 80
    if total <= 100:
        return 65
    return 50


def _data_completeness(schema: Schema, pattern: Pattern) -> int:
    """% of pattern canonical attributes that mapped to a real source column."""
    expected = sum(
        len(v.canonical_attributes) for v in pattern.vertices if not v.optional
    )
    actual = sum(len(v.attributes) for v in schema.vertices)
    if expected == 0:
        return 100
    return min(100, int(round(actual / expected * 100)))


def _use_case_fit(schema: Schema, pattern: Pattern) -> int:
    expected_v = {v.name for v in pattern.vertices if not v.optional}
    present = {v.name for v in schema.vertices}
    if not expected_v:
        return 100
    return int(round(len(expected_v & present) / len(expected_v) * 100))


def _graph_algo_readiness(schema: Schema) -> int:
    """Does the schema support common algorithms? Heuristics: connected, shared-id vertices,
    event vertex with timestamp."""
    score = 0
    if any(v.name in {"Device", "Email", "Phone", "IPAddress", "Card"} for v in schema.vertices):
        score += 40
    if any("Transaction" in v.name and any(a.dtype == DataKind.DATETIME for a in v.attributes) for v in schema.vertices):
        score += 40
    if len(schema.edges) >= 5:
        score += 20
    return min(100, score)


def _ml_graphrag_readiness(schema: Schema) -> int:
    """Bonus for label columns (is_fraud) and dense identity graph."""
    score = 50
    for v in schema.vertices:
        for a in v.attributes:
            if a.dtype == DataKind.BOOL and "fraud" in a.name.lower():
                score += 30
            if "label" in a.name.lower():
                score += 10
    if len(schema.vertices) >= 6:
        score += 10
    return min(100, score)


_MOTIVE_KEYWORDS = {
    # canonical keyword → vertex / attribute / edge names that satisfy it
    "fraud":        {"Transaction", "is_fraud", "FraudCase"},
    "ring":         {"Device", "IPAddress", "Email", "Phone", "Card"},
    "device":       {"Device"},
    "ip":           {"IPAddress"},
    "email":        {"Email"},
    "phone":        {"Phone"},
    "card":         {"Card", "Credit_Card"},
    "merchant":     {"Merchant"},
    "geographic":   {"lat", "long", "Address", "Geolocation", "City"},
    "geo":          {"lat", "long", "Address", "Geolocation", "City"},
    "location":     {"lat", "long", "Address", "Geolocation", "City"},
    "city":         {"City", "Address"},
    "region":       {"City", "Address"},
    "mule":         {"Account", "Account_TO_Account"},
    "laundering":   {"Account", "Account_TO_Account", "Transaction"},
    "transfer":     {"Account_TO_Account"},
    "temporal":     {"timestamp", "trans_date_time", "TimeWindow"},
    "time":         {"timestamp", "trans_date_time", "TimeWindow"},
    "velocity":     {"timestamp", "TimeWindow"},
    "amount":       {"amount", "amt"},
    "category":     {"category", "MerchantCategory"},
    "segment":      {"profile", "Segment"},
    "behavior":     {"Segment", "profile"},
    "anomaly":      {"is_fraud", "risk_score"},
    "ml":           {"is_fraud", "risk_score"},
    "supervised":   {"is_fraud"},
    "label":        {"is_fraud", "label"},
    "customer":     {"Customer"},
    "address":      {"Address"},
    "session":      {"WebSession"},
    "ticket":       {"SupportTicket"},
}


def _motive_alignment(schema: Schema, user_prompt: str | None) -> tuple[int, list[str]]:
    """How well does the schema reflect what the user asked for?

    Tokenizes the prompt, finds motive keywords, and checks whether the schema
    contains the vertex / attribute / edge names that satisfy each one. Returns
    (score 0–100, list of satisfied keywords).

    If no prompt is given, returns a neutral 75 — no penalty, no bonus.
    """
    if not user_prompt or not user_prompt.strip():
        return 75, []

    prompt_l = user_prompt.lower()
    # Extract every name that appears in the schema (vertices, attributes, edges)
    schema_tokens: set[str] = set()
    for v in schema.vertices:
        schema_tokens.add(v.name)
        for a in v.attributes:
            schema_tokens.add(a.name)
    for e in schema.edges:
        schema_tokens.add(e.name)
    schema_tokens_l = {t.lower() for t in schema_tokens}

    requested: set[str] = set()
    satisfied: set[str] = set()
    for keyword, expected_names in _MOTIVE_KEYWORDS.items():
        if not re.search(rf"\b{re.escape(keyword)}\b", prompt_l):
            continue
        requested.add(keyword)
        if any(name.lower() in schema_tokens_l for name in expected_names):
            satisfied.add(keyword)

    if not requested:
        # Prompt didn't mention anything specific we can score
        return 80, []
    pct = int(round(len(satisfied) / len(requested) * 100))
    return pct, sorted(satisfied)


def score_schema(
    schema: Schema,
    validation: ValidationResult,
    pattern: Pattern,
    user_prompt: str | None = None,
) -> SchemaScore:
    weights = dict(load_rules_config()["scoring_weights"])
    weights.setdefault("motive_alignment", 1.5)  # new dimension — heavy weight

    motive_score, satisfied_motives = _motive_alignment(schema, user_prompt)

    breakdown = {
        "use_case_fit": _use_case_fit(schema, pattern),
        "query_coverage": _query_coverage(validation, schema),
        "traversal_efficiency": _traversal_efficiency(schema),
        "data_completeness": _data_completeness(schema, pattern),
        "entity_reuse": _entity_reuse(schema),
        "attribute_placement": _attribute_placement(validation),
        "graph_algo_readiness": _graph_algo_readiness(schema),
        "ml_graphrag_readiness": _ml_graphrag_readiness(schema),
        "simplicity": _simplicity(schema),
        "motive_alignment": motive_score,
    }

    total_w = sum(weights.values())
    weighted_sum = sum(breakdown[k] * weights.get(k, 1.0) for k in breakdown)
    total = int(round(weighted_sum / total_w))

    strengths: list[str] = []
    gaps: list[str] = []
    suggestions: list[str] = []

    if validation.answerable_questions:
        strengths.append(
            f"Schema answers {len(validation.answerable_questions)} of "
            f"{len(schema.target_questions)} target questions."
        )
    if any(v.name in {"Device", "Email", "Phone", "IPAddress"} for v in schema.vertices):
        strengths.append("Shared-identifier vertices (Device/IP/Email/Phone) modeled — fraud-ring traversal enabled.")
    if any("Transaction" in v.name for v in schema.vertices):
        strengths.append("Transaction modeled as event vertex with timestamp + amount.")
    if any(
        a.dtype == DataKind.BOOL and "fraud" in a.name.lower()
        for v in schema.vertices
        for a in v.attributes
    ):
        strengths.append("Supervised fraud label (is_fraud) preserved — ML-ready.")
    if satisfied_motives:
        strengths.append(
            f"Aligned with user motive — satisfied: {', '.join(satisfied_motives)}."
        )

    for q in validation.unanswerable_questions:
        gaps.append(f"Target question '{q}' not answerable.")
    if user_prompt and motive_score < 70:
        gaps.append(
            f"Motive alignment is {motive_score}/100 — schema doesn't fully reflect "
            "the user's stated goal."
        )
    if breakdown["data_completeness"] < 80:
        gaps.append("Some canonical attributes from the pattern have no source column.")
    if breakdown["traversal_efficiency"] < 100:
        suggestions.append("Add reverse edges to all multi-hop directed edges.")

    return SchemaScore(
        total=total,
        breakdown=breakdown,
        strengths=strengths,
        gaps=gaps,
        suggestions=suggestions,
    )


ConfidenceLabel = Literal["High", "Medium", "Low"]


def compute_confidence(
    score: SchemaScore | None,
    validation: ValidationResult | None,
    assumptions: list[Assumption] | None = None,
) -> ConfidenceLabel:
    """Composite confidence label for the Autograph OutcomesPanel header.

    Conservative by design — a wrong "High" destroys user trust.

    High requires ALL:
      - structural score >= 75
      - answerable-question coverage >= 80%
      - no low-confidence assumption

    Medium requires ALL:
      - structural score >= 50
      - answerable-question coverage >= 50%

    Otherwise Low. When key signals are missing, defaults to Medium so the
    panel doesn't look broken on cold-start schemas.
    """
    if score is None and validation is None:
        return "Medium"

    structural = (score.total if score else 0)
    answerable = len(validation.answerable_questions) if validation else 0
    unanswerable = len(validation.unanswerable_questions) if validation else 0
    total_q = answerable + unanswerable
    coverage = answerable / total_q if total_q else 0.0
    low_assumption = any(
        (a.confidence or "").lower() == "low" for a in (assumptions or [])
    )

    if total_q == 0:
        # No target questions defined — fall back to structural-only signal.
        if structural >= 75 and not low_assumption:
            return "Medium"
        return "Low" if structural < 50 else "Medium"

    if structural >= 75 and coverage >= 0.8 and not low_assumption:
        return "High"
    if structural >= 50 and coverage >= 0.5:
        return "Medium"
    if structural < 50 or coverage < 0.3:
        return "Low"
    return "Medium"
