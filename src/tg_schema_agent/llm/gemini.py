"""Gemini-powered schema designer.

Architecture (hybrid — LLM does NOT own the core logic per PDF spec):
1. Profiler (deterministic) reads CSV → column profiles, sample rows, PII flags
2. Pattern library (hand-authored YAML) is reference context
3. Gemini receives: profiles + user intent + pattern reference + sample rows
   → produces a Schema customized to THIS specific data
4. Validator + Scorer (deterministic) sanity-check the output
5. If Gemini errors or returns invalid output, caller falls back to
   the deterministic designer.

Inputs given to Gemini:
- The actual column names, types, cardinality, PII tags from the user's data
- 3 sample rows per table (so the model sees real shapes)
- The pattern library entry for the chosen use case (canonical structure)
- The user's free-text intent (chat message), if any
- The use case (FRAUD / CUSTOMER_360 / etc.)

Output: a JSON object matching the Schema Pydantic model, returned via
Gemini's structured-output / response_schema mechanism.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd

from tg_schema_agent.enums import UseCase
from tg_schema_agent.io_utils import inputs_hash, load_csv
from tg_schema_agent.models import Pattern, Schema, TableProfile
from tg_schema_agent.patterns import load_patterns

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


# ---------- prompt building ----------


_SYSTEM_INSTRUCTION = """You are TigerGraph Savanna's Schema Design Agent.

Your job is to design the BEST possible TigerGraph 4.x graph schema for THIS
specific dataset and THIS specific user. "Best" means: maximally useful for the
user's actual motive, given the actual columns and values in their data —
not a generic template, not the canonical pattern recopied.

You must reason in three steps before producing the schema:

STEP 1 — UNDERSTAND THE DATA
Look at every column in the profile, its dtype, cardinality, null %, name-
pattern hits, sample values, and PII tags. Identify:
- Which columns are DURABLE ENTITIES (things that exist independently and can
  participate in many relationships: customers, accounts, devices, merchants).
- Which columns are SHARED IDENTIFIERS (values that connect multiple records:
  email, phone, IP, device id, card number, address parts). These are the
  most important fraud signals — they MUST become standalone vertices, not
  attributes on Customer.
- Which columns describe EVENTS (rows with id + amount + timestamp).
  Events become VERTICES (not edges) so the graph can answer questions about
  the events themselves.
- Which columns are simple ATTRIBUTES of an entity (name, dob, gender, job).
- Which columns are GEOGRAPHIC (lat/long, city, state, zip) — consider
  whether the user's intent justifies modeling Geolocation or City vertices.
- Which columns are LABELS/SIGNALS (is_fraud, risk_score, category). These
  belong as attributes on the closest event vertex.

STEP 2 — UNDERSTAND THE MOTIVE
The user told you their goal in `user_intent`. Read it carefully. Ask:
- What questions do they ACTUALLY want to answer? (ring detection? mule
  accounts? shared-device fraud? geo anomalies? velocity patterns?)
- Which TRAVERSALS does the schema need to support cheaply (1-3 hops)?
- What's the user's domain (banking, retail, healthcare)? Use vocabulary
  that fits.
- What additional vertices/edges — NOT in the canonical pattern — would
  better serve this specific motive? If the data has lat/long, the intent is
  fraud, and a Geolocation vertex would help, ADD it. If the data has a
  customer profile/segment column, a Segment vertex enables behavioral
  cohort queries — consider it.

STEP 3 — DESIGN THE BEST SCHEMA
Use the canonical reference pattern as a STARTING POINT, not a hard
constraint:
- Reuse canonical names (Customer, Account, Transaction, Merchant, Device,
  Email, Phone, IPAddress, Card, Address) when the corresponding entity is
  in the data — this keeps the schema consistent and TigerGraph-idiomatic.
- ADD extra vertices/edges the canonical pattern misses but the data and
  motive demand. Examples: Geolocation, City, MerchantCategory, TimeWindow,
  Segment, FraudCase, Beneficiary, KnownFraudFlag.
- DROP canonical entities that don't exist in the data — don't invent
  columns to satisfy a template.

CRITICAL RULES (output will be rejected if violated):

1. ONLY use column names that actually appear in the data profiles. Never
   invent a column name. If a canonical attribute (e.g. "amount") doesn't
   exist in the data, omit it or map it to a real column that means the
   same thing (e.g. "amt" → amount).

2. Every shared identifier (device, email, phone, IP, card, address) gets
   its own VERTEX, not an attribute on Customer.

3. Every row that has id + amount + timestamp becomes an event VERTEX with
   those attributes — NOT an edge.

4. Every DIRECTED edge that needs to be traversed in both directions for
   multi-hop queries (which is almost all of them) MUST use
   direction=DIRECTED_WITH_REVERSE with a populated reverse_edge_name.

5. Tag PII correctly: ssn → SSN, cc_num → CARD, email → EMAIL, phone → PHONE,
   ip → IP, name fields → NAME, address fields → ADDRESS.

6. Set every Vertex's `source` to the real table name + the actual column(s)
   used as the primary id. Use kind="column_group" for composite ids like
   Address from (street, city, state, zip).

7. For every vertex and edge, write a 1-sentence `rationale` explaining WHY
   it exists IN THE CONTEXT OF THE USER'S MOTIVE. Not generic — specific to
   the data + the goal.

8. Quality goal: the schema must answer the canonical target questions in
   ≤3 hops, AND specifically support the user's stated motive.

OUTPUT FORMAT — return a single JSON object with this exact shape:

{
  "analysis": {
    "data_summary": "1-2 sentences describing what the data actually contains.",
    "motive_interpretation": "1-2 sentences on what the user is trying to accomplish.",
    "key_design_decisions": [
      "WHY you promoted X to a vertex.",
      "WHY you added Y that isn't in the canonical pattern.",
      "WHY you dropped Z that IS in the canonical pattern.",
      "..."
    ]
  },
  "use_case": "FRAUD" | "CUSTOMER_360" | "ENTITY_RESOLUTION" | "RECOMMENDATION",
  "name": "fraud_schema",
  "version": "0.1.0",
  "pattern_version": "1.0.0",
  "vertices": [ { ... full vertex objects per the Schema model ... } ],
  "edges":    [ { ... full edge objects ... } ],
  "target_questions": [ ]  // leave empty — the system backfills from the pattern
}

No prose outside the JSON. No markdown fences. Just the JSON object.
"""


_CRITIQUE_INSTRUCTION = """You previously designed a TigerGraph schema for this dataset. The validator + scorer have now reviewed it. Your job: identify gaps and refine.

Below are:
- Your previous analysis and schema (so you remember your own reasoning).
- The validator results: which target questions are unanswerable + structural warnings.
- The score breakdown: which dimensions lost points.

Produce an IMPROVED schema that:
1. Fixes every unanswerable target question by adding the missing vertex(es) or edge(s).
2. Adds reverse edges anywhere `traversal_efficiency` lost points.
3. Adds canonical attributes that scored low in `data_completeness` IF they actually exist in the data (don't invent columns).
4. Keeps every vertex/edge from the previous schema unless it's clearly wrong.
5. Updates `analysis.key_design_decisions` to explain what you changed and why.

Same JSON shape as before. No prose outside the JSON.
"""


def _profile_summary(profile: TableProfile) -> dict[str, Any]:
    return {
        "table_name": profile.name,
        "row_count": profile.row_count,
        "detected_delimiter": profile.detected_delimiter,
        "primary_key": profile.primary_key,
        "has_event_signature": profile.has_event_signature,
        "is_wide_denormalized": profile.is_wide_denormalized,
        "columns": [
            {
                "name": c.name,
                "dtype": c.dtype.value,
                "cardinality": c.cardinality.value,
                "distinct_count": c.distinct_count,
                "null_pct": round(c.null_pct, 4),
                "is_primary_key_candidate": c.is_primary_key_candidate,
                "is_foreign_key_candidate": c.is_foreign_key_candidate,
                "name_pattern_hits": c.name_pattern_hits,
                "pii_class": c.pii_class.value,
                "sample_values": c.sample_values[:3],
            }
            for c in profile.columns
        ],
    }


def _read_sample_rows(csv_path: Path, n: int = 3) -> list[dict[str, str]]:
    try:
        df = load_csv(csv_path)
        return [
            {col: str(val) for col, val in row.items()}
            for _, row in df.head(n).iterrows()
        ]
    except Exception as exc:
        log.warning("Could not read sample rows from %s: %s", csv_path, exc)
        return []


def _pattern_summary(pattern: Pattern) -> dict[str, Any]:
    return {
        "use_case": pattern.use_case.value,
        "name": pattern.name,
        "version": pattern.version,
        "description": pattern.description,
        "canonical_vertices": [
            {
                "name": v.name,
                "primary_id": v.primary_id,
                "dtype": v.dtype.value,
                "name_aliases": v.name_aliases,
                "promotion_rule": v.promotion_rule,
                "pii": v.pii.value,
                "optional": v.optional,
                "composed_from": v.composed_from,
                "canonical_attributes": [
                    {
                        "name": ca.name,
                        "dtype": ca.dtype.value,
                        "aliases": ca.aliases,
                        "optional": ca.optional,
                    }
                    for ca in v.canonical_attributes
                ],
            }
            for v in pattern.vertices
        ],
        "canonical_edges": [
            {
                "name": e.name,
                "from": e.from_,
                "to": e.to,
                "direction": e.direction.value,
                "reverse_name": e.reverse_name,
                "optional": e.optional,
            }
            for e in pattern.edges
        ],
        "target_questions": [
            {
                "id": q.id,
                "text": q.text,
                "required_vertices": q.required_vertices,
                "required_edges": q.required_edges,
                "max_hops": q.max_hops,
            }
            for q in pattern.target_questions
        ],
    }


def _build_prompt(
    profiles: list[TableProfile],
    pattern: Pattern,
    use_case: UseCase,
    user_intent: str | None,
    sample_rows_by_table: dict[str, list[dict[str, str]]],
) -> str:
    intent = (
        user_intent.strip()
        if user_intent and user_intent.strip()
        else f"Design the best {use_case.value} graph schema for this dataset."
    )

    payload = {
        "user_intent": intent,
        "use_case": use_case.value,
        "data_profiles": [_profile_summary(p) for p in profiles],
        "sample_rows": sample_rows_by_table,
        "reference_pattern": _pattern_summary(pattern),
    }
    return json.dumps(payload, indent=2, default=str)


# ---------- Schema JSON post-processing ----------


_VALID_DTYPES = {"INT", "FLOAT", "STRING", "DATETIME", "BOOL", "CATEGORICAL", "ID_LIKE"}
_VALID_DIRECTIONS = {"DIRECTED", "UNDIRECTED", "DIRECTED_WITH_REVERSE"}
_VALID_PII = {"NONE", "EMAIL", "PHONE", "ADDRESS", "NAME", "DOC_ID", "IP", "SSN", "CARD"}


def _norm_dtype(value: Any) -> str:
    if not value:
        return "STRING"
    v = str(value).upper().strip()
    aliases = {
        "INTEGER": "INT",
        "DOUBLE": "FLOAT",
        "NUMBER": "FLOAT",
        "TEXT": "STRING",
        "TIMESTAMP": "DATETIME",
        "DATE": "DATETIME",
        "TIME": "DATETIME",
        "BOOLEAN": "BOOL",
        "ENUM": "CATEGORICAL",
    }
    v = aliases.get(v, v)
    return v if v in _VALID_DTYPES else "STRING"


def _norm_pii(value: Any) -> str:
    if not value:
        return "NONE"
    v = str(value).upper().strip()
    return v if v in _VALID_PII else "NONE"


def _norm_direction(value: Any) -> str:
    if not value:
        return "DIRECTED_WITH_REVERSE"
    v = str(value).upper().strip()
    return v if v in _VALID_DIRECTIONS else "DIRECTED_WITH_REVERSE"


def _coerce_attribute(
    attr: dict[str, Any], default_source_table: str
) -> dict[str, Any]:
    out = dict(attr)
    # Pull common Gemini renamings
    if "type" in out and "dtype" not in out:
        out["dtype"] = out.pop("type")
    out["dtype"] = _norm_dtype(out.get("dtype"))
    out["pii_class"] = _norm_pii(out.get("pii_class") or out.get("pii"))
    out.pop("pii", None)
    # source_table defaults to the parent vertex's source table
    out.setdefault("source_table", default_source_table)
    # source_column defaults to the attribute's own name (best guess)
    if not out.get("source_column"):
        out["source_column"] = out.get("name", "")
    out.setdefault("nullable", True)
    # Strip fields Pydantic doesn't know
    return {
        k: v
        for k, v in out.items()
        if k in {"name", "dtype", "source_table", "source_column", "pii_class", "nullable"}
    }


def _flatten_to_string(value: Any) -> str:
    """Gemini sometimes wraps simple identifiers in dicts like
    {"name": "ssn", "dtype": "STRING", "kind": "column"}. Pull the string back out.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "column", "id", "primary_id", "value"):
            if key in value and isinstance(value[key], str):
                return value[key]
    if value is None:
        return ""
    return str(value)


def _coerce_vertex(v: dict[str, Any]) -> dict[str, Any]:
    out = dict(v)
    # Gemini sometimes nests primary_id as a dict — pull the column name out
    out["primary_id"] = _flatten_to_string(out.get("primary_id"))
    # primary_id_dtype renames
    if "dtype" in out and "primary_id_dtype" not in out:
        out["primary_id_dtype"] = out.pop("dtype")
    out["primary_id_dtype"] = _norm_dtype(out.get("primary_id_dtype"))
    # source
    src = out.get("source")
    if not isinstance(src, dict):
        src = {}
    table = src.get("table") or out.get("source_table") or ""
    raw_cols = src.get("columns") or out.get("source_columns") or [out["primary_id"]]
    if isinstance(raw_cols, str):
        raw_cols = [raw_cols]
    columns = [_flatten_to_string(c) for c in raw_cols if c is not None]
    columns = [c for c in columns if c]
    if not columns:
        columns = [out["primary_id"]]
    valid_kinds = {"table_column", "column_group", "derived"}
    kind_raw = (src.get("kind") or "").lower()
    if kind_raw not in valid_kinds:
        kind_raw = "column_group" if len(columns) > 1 else "table_column"
    out["source"] = {
        "kind": kind_raw,
        "table": _flatten_to_string(table),
        "columns": columns,
    }
    out.pop("source_table", None)
    out.pop("source_columns", None)
    # Normalize attributes
    raw_attrs = out.get("attributes") or []
    out["attributes"] = [_coerce_attribute(a, out["source"]["table"]) for a in raw_attrs if isinstance(a, dict)]
    out.setdefault("rationale", "")
    out["pattern_origin"] = out.get("pattern_origin")
    out["name"] = _flatten_to_string(out.get("name"))
    # Strip extras
    keep = {"name", "primary_id", "primary_id_dtype", "attributes", "source", "rationale", "pattern_origin"}
    return {k: v for k, v in out.items() if k in keep}


def _coerce_edge(e: dict[str, Any], default_table: str) -> dict[str, Any]:
    out = dict(e)
    # from/to renames — Pydantic uses from_vertex / to_vertex
    if "from" in out and "from_vertex" not in out:
        out["from_vertex"] = out.pop("from")
    if "to" in out and "to_vertex" not in out:
        out["to_vertex"] = out.pop("to")
    out["name"] = _flatten_to_string(out.get("name"))
    out["from_vertex"] = _flatten_to_string(out.get("from_vertex"))
    out["to_vertex"] = _flatten_to_string(out.get("to_vertex"))
    out["direction"] = _norm_direction(out.get("direction"))
    out["reverse_edge_name"] = out.get("reverse_edge_name") or out.get("reverse_name")
    out.pop("reverse_name", None)
    # Normalize attributes
    raw_attrs = out.get("attributes") or []
    out["attributes"] = [
        _coerce_attribute(a, default_table) for a in raw_attrs if isinstance(a, dict)
    ]
    out.setdefault("rationale", "")
    out["pattern_origin"] = out.get("pattern_origin")
    # source
    src = out.get("source")
    valid_edge_kinds = {"fk", "wide_table_pair", "derived"}
    if isinstance(src, dict):
        kind_raw = (src.get("kind") or "").lower()
        if kind_raw not in valid_edge_kinds:
            kind_raw = "derived"
        out["source"] = {
            "kind": kind_raw,
            "table": src.get("table") or default_table,
            "from_column": src.get("from_column"),
            "to_column": src.get("to_column"),
        }
    else:
        out["source"] = None
    keep = {
        "name", "from_vertex", "to_vertex", "direction", "reverse_edge_name",
        "attributes", "source", "rationale", "pattern_origin",
    }
    return {k: v for k, v in out.items() if k in keep}


def _coerce_target_question(q: dict[str, Any]) -> dict[str, Any]:
    out = dict(q)
    out.setdefault("required_vertices", [])
    out.setdefault("required_edges", [])
    out.setdefault("max_hops", 3)
    keep = {"id", "text", "required_vertices", "required_edges", "max_hops"}
    return {k: v for k, v in out.items() if k in keep}


def _coerce_schema_dict(
    raw: dict[str, Any], use_case: UseCase, pattern_version: str, default_table: str
) -> dict[str, Any]:
    """Aggressively patch Gemini's natural JSON shape to match the Pydantic Schema model."""
    # `analysis` is captured by the caller separately — drop it before validation
    # since Schema has extra="forbid".
    raw.pop("analysis", None)
    raw.pop("design_decisions", None)
    raw.pop("motive_interpretation", None)
    raw.pop("data_summary", None)
    raw.setdefault("use_case", use_case.value)
    raw.setdefault("name", f"{use_case.value.lower()}_schema")
    raw.setdefault("version", "0.1.0")
    raw.setdefault("pattern_version", pattern_version)
    raw.setdefault("inputs_hash", "")

    raw["vertices"] = [_coerce_vertex(v) for v in raw.get("vertices", []) if isinstance(v, dict)]
    # Use the most common vertex source.table as the default for edges
    edge_default_table = default_table
    if raw["vertices"]:
        from collections import Counter

        tbls = Counter(
            v.get("source", {}).get("table", "") for v in raw["vertices"]
        )
        if tbls:
            edge_default_table = tbls.most_common(1)[0][0] or default_table

    raw["edges"] = [
        _coerce_edge(e, edge_default_table)
        for e in raw.get("edges", [])
        if isinstance(e, dict)
    ]
    raw["target_questions"] = [
        _coerce_target_question(q)
        for q in raw.get("target_questions", [])
        if isinstance(q, dict)
    ]

    return raw


# ---------- public API ----------


class GeminiUnavailable(RuntimeError):
    """Raised when the Gemini API key is missing or the client cannot be created."""


def is_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def _parse_json_response(raw_text: str) -> dict[str, Any]:
    """Parse Gemini's text response into JSON, handling code-fence wrapping."""
    try:
        return json.loads(raw_text)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        stripped = raw_text.strip()
        if stripped.startswith("```"):
            inner = stripped.strip("`").lstrip("json").strip()
            return json.loads(inner)  # type: ignore[no-any-return]
        raise RuntimeError(f"Gemini returned non-JSON: {raw_text[:300]}")


def _build_schema_from_raw(
    raw: dict[str, Any],
    profiles: list[TableProfile],
    pattern: Any,
    use_case: UseCase,
) -> Schema:
    default_table = profiles[0].name if profiles else ""
    patched = _coerce_schema_dict(raw, use_case, pattern.version, default_table)
    schema = Schema.model_validate(patched)
    schema.inputs_hash = inputs_hash(profiles)
    if not schema.target_questions:
        schema.target_questions = list(pattern.target_questions)
    return schema


def _call_gemini(
    client: Any,
    model_name: str,
    system_instruction: str,
    user_payload: str,
    genai_types: Any,
    temperature: float = 0.2,
) -> tuple[dict[str, Any], int]:
    resp = client.models.generate_content(
        model=model_name,
        contents=user_payload,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            temperature=temperature,
        ),
    )
    raw_text = resp.text or ""
    if not raw_text.strip():
        raise RuntimeError("Gemini returned an empty response.")
    return _parse_json_response(raw_text), len(raw_text)


def design_schema_ai(
    profiles: list[TableProfile],
    use_case: UseCase,
    user_prompt: str | None = None,
    csv_paths: list[Path] | None = None,
    model: str | None = None,
    patterns_dir: Path | None = None,
) -> tuple[Schema, dict[str, Any]]:
    """Design a schema using Gemini with deep contextual reasoning + self-critique.

    Pass 1: Gemini analyzes the data + user motive, designs an initial schema with
            per-decision rationale.
    Pass 2: Runs the validator + scorer locally. If any target question is
            unanswerable OR the score is below 90, calls Gemini again with the
            validator/scorer feedback to refine the schema.

    Returns (final_schema, debug_info) where debug_info includes Gemini's analysis
    and any refinement steps. Raises GeminiUnavailable if no API key is configured.
    """
    if not is_available():
        raise GeminiUnavailable(
            "No GEMINI_API_KEY (or GOOGLE_API_KEY) in environment. "
            "Set one in your .env or shell."
        )

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as exc:  # pragma: no cover
        raise GeminiUnavailable(
            "google-genai SDK not installed. Run `uv sync --extra llm`."
        ) from exc

    # Local imports to avoid circular deps at module load
    from tg_schema_agent import scorer as scorer_mod
    from tg_schema_agent import validator as validator_mod

    pattern = load_patterns(patterns_dir)[use_case]
    sample_rows: dict[str, list[dict[str, str]]] = {}
    if csv_paths:
        for p in csv_paths:
            sample_rows[Path(p).stem] = _read_sample_rows(Path(p), n=3)

    prompt = _build_prompt(profiles, pattern, use_case, user_prompt, sample_rows)

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)
    model_name = model or DEFAULT_MODEL

    log.info("Pass 1: Gemini reasoning + initial design (model=%s)", model_name)
    raw1, raw1_chars = _call_gemini(
        client, model_name, _SYSTEM_INSTRUCTION, prompt, genai_types, temperature=0.3
    )
    analysis_1 = raw1.get("analysis", {}) if isinstance(raw1.get("analysis"), dict) else {}
    schema_1 = _build_schema_from_raw(raw1, profiles, pattern, use_case)

    # Local sanity check
    val_1 = validator_mod.validate(schema_1)
    score_1 = scorer_mod.score_schema(schema_1, val_1, pattern, user_prompt=user_prompt)

    debug: dict[str, Any] = {
        "model": model_name,
        "user_prompt": user_prompt,
        "analysis": analysis_1,
        "pass1": {
            "vertex_count": len(schema_1.vertices),
            "edge_count": len(schema_1.edges),
            "score": score_1.total,
            "answerable": list(val_1.answerable_questions),
            "unanswerable": list(val_1.unanswerable_questions),
            "raw_chars": raw1_chars,
        },
    }

    # If there are gaps, run a self-critique pass.
    needs_refinement = bool(val_1.unanswerable_questions) or score_1.total < 90
    if not needs_refinement:
        debug["passes"] = 1
        return schema_1, debug

    log.info(
        "Pass 2: Self-critique (score=%d, unanswerable=%d)",
        score_1.total,
        len(val_1.unanswerable_questions),
    )

    critique_payload = json.dumps(
        {
            "user_intent": user_prompt
            or f"Design the best {use_case.value} schema for this data.",
            "previous_analysis": analysis_1,
            "previous_schema": schema_1.model_dump(mode="json"),
            "validator_result": val_1.model_dump(mode="json"),
            "score_breakdown": score_1.model_dump(mode="json"),
            "reference_pattern": _pattern_summary(pattern),
            "data_profiles": [_profile_summary(p) for p in profiles],
        },
        indent=2,
        default=str,
    )

    try:
        raw2, raw2_chars = _call_gemini(
            client,
            model_name,
            _CRITIQUE_INSTRUCTION,
            critique_payload,
            genai_types,
            temperature=0.2,
        )
        analysis_2 = (
            raw2.get("analysis", {}) if isinstance(raw2.get("analysis"), dict) else {}
        )
        schema_2 = _build_schema_from_raw(raw2, profiles, pattern, use_case)
        val_2 = validator_mod.validate(schema_2)
        score_2 = scorer_mod.score_schema(schema_2, val_2, pattern, user_prompt=user_prompt)

        # Use whichever pass produced the higher-scoring + more-answerable schema.
        if score_2.total > score_1.total or len(val_2.answerable_questions) > len(
            val_1.answerable_questions
        ):
            debug["analysis"] = analysis_2
            debug["pass2"] = {
                "vertex_count": len(schema_2.vertices),
                "edge_count": len(schema_2.edges),
                "score": score_2.total,
                "answerable": list(val_2.answerable_questions),
                "unanswerable": list(val_2.unanswerable_questions),
                "raw_chars": raw2_chars,
            }
            debug["passes"] = 2
            debug["chosen_pass"] = 2
            return schema_2, debug

        debug["pass2"] = {
            "vertex_count": len(schema_2.vertices),
            "edge_count": len(schema_2.edges),
            "score": score_2.total,
            "answerable": list(val_2.answerable_questions),
            "unanswerable": list(val_2.unanswerable_questions),
            "raw_chars": raw2_chars,
            "note": "pass1 was better — kept it",
        }
        debug["passes"] = 2
        debug["chosen_pass"] = 1
        return schema_1, debug

    except Exception as exc:  # noqa: BLE001 — critique failure is non-fatal
        log.warning("Self-critique pass failed, returning pass-1 schema: %s", exc)
        debug["pass2_error"] = f"{type(exc).__name__}: {exc}"
        debug["passes"] = 1
        return schema_1, debug
