"""Agentic tools the Gemini-driven schema agent can call.

Every tool wraps an existing utility in the codebase — there is NO new
business logic here. The agent gets affordances to:
  - inspect data (list_tables, inspect_column, find_columns_matching, get_sample_rows)
  - consult deterministic helpers (run_deterministic_rules, match_pattern_library)
  - mutate a per-turn working schema (propose_vertex / propose_edge / remove_*)
  - self-check (validate_schema, score_schema)
  - terminate the loop (finalize_schema, ask_user)

Each tool returns a uniform shape:

    {"ok": bool, "summary": str, "data": <serializable>}

`summary` is rendered in the SSE `tool_result` event so the user sees a
one-line status. The LLM receives the full dict back as a FunctionResponse.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tg_schema_agent import io_utils
from tg_schema_agent.enums import (
    DataKind,
    EdgeDirection,
    PIIClass,
    UseCase,
)
from tg_schema_agent.models import (
    Assumption,
    Attribute,
    BusinessContext,
    DesignRationale,
    Edge,
    EdgeSource,
    Pattern,
    RecommendationSummary,
    RecommendedEntity,
    Schema,
    TableProfile,
    Vertex,
    VertexSource,
)


# ----------------------------------------------------------------------
# ToolContext — passed as the first arg to every tool. The LLM never sees it.
# ----------------------------------------------------------------------


@dataclass
class ToolContext:
    """Per-turn execution context for the tool loop."""

    workspace_dir: Path
    use_case: UseCase
    profiles: list[TableProfile]
    pattern: Pattern
    working_schema: Schema
    user_prompt: str | None = None
    csv_paths: list[Path] = field(default_factory=list)

    @classmethod
    def load(
        cls,
        workspace_dir: Path,
        use_case: UseCase,
        user_prompt: str | None = None,
    ) -> "ToolContext":
        from tg_schema_agent.patterns import load_patterns
        from tg_schema_agent.profiler import profile_directory

        profiles = profile_directory(workspace_dir)
        pattern = load_patterns()[use_case]
        csv_paths = sorted(workspace_dir.glob("*.csv"))
        existing_schema_path = workspace_dir / "schema.json"
        if existing_schema_path.exists():
            try:
                working_schema = io_utils.load_schema(existing_schema_path)
            except Exception:
                working_schema = _blank_schema(use_case, pattern)
        else:
            working_schema = _blank_schema(use_case, pattern)
        return cls(
            workspace_dir=workspace_dir,
            use_case=use_case,
            profiles=profiles,
            pattern=pattern,
            working_schema=working_schema,
            user_prompt=user_prompt,
            csv_paths=csv_paths,
        )

    def persist_schema(self) -> None:
        """Snapshot the working schema to schema.json so reloads see the latest."""
        try:
            io_utils.dump_schema(self.working_schema, self.workspace_dir / "schema.json")
        except Exception:
            pass

    def find_profile(self, table: str) -> TableProfile | None:
        for p in self.profiles:
            if p.name == table or p.name.lower() == table.lower():
                return p
        return None


def _blank_schema(use_case: UseCase, pattern: Pattern) -> Schema:
    return Schema(
        use_case=use_case,
        name=f"{use_case.value.lower()}_schema",
        version="0.1.0",
        pattern_version=pattern.version,
        vertices=[],
        edges=[],
        target_questions=list(pattern.target_questions),
    )


# ----------------------------------------------------------------------
# Uniform tool result helpers
# ----------------------------------------------------------------------


def _ok(summary: str, data: Any = None) -> dict[str, Any]:
    return {"ok": True, "summary": summary, "data": data}


def _err(summary: str, data: Any = None) -> dict[str, Any]:
    return {"ok": False, "summary": summary, "data": data}


def _norm_dtype(value: Any, default: str = "STRING") -> str:
    if not value:
        return default
    v = str(value).upper().strip()
    aliases = {
        "INTEGER": "INT",
        "DOUBLE": "FLOAT",
        "NUMBER": "FLOAT",
        "TEXT": "STRING",
        "TIMESTAMP": "DATETIME",
        "BOOLEAN": "BOOL",
    }
    v = aliases.get(v, v)
    valid = {x.value for x in DataKind}
    return v if v in valid else default


# ----------------------------------------------------------------------
# READ tools — data inspection
# ----------------------------------------------------------------------


def list_tables(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    if not ctx.profiles:
        return _err("No tables loaded — upload a CSV first.")
    summary_lines = [
        f"{p.name} ({p.row_count} rows, {len(p.columns)} cols, "
        f"delim={p.detected_delimiter!r}, "
        f"event={p.has_event_signature}, wide={p.is_wide_denormalized})"
        for p in ctx.profiles
    ]
    return _ok(
        f"{len(ctx.profiles)} table(s): " + "; ".join(p.name for p in ctx.profiles),
        {
            "tables": [
                {
                    "name": p.name,
                    "row_count": p.row_count,
                    "column_count": len(p.columns),
                    "primary_key": p.primary_key,
                    "has_event_signature": p.has_event_signature,
                    "is_wide_denormalized": p.is_wide_denormalized,
                    "columns": [c.name for c in p.columns],
                }
                for p in ctx.profiles
            ],
            "detail": summary_lines,
        },
    )


def inspect_column(ctx: ToolContext, table: str, column: str) -> dict[str, Any]:
    prof = ctx.find_profile(table)
    if not prof:
        return _err(f"Unknown table '{table}'. Call list_tables first.")
    col = prof.column(column)
    if col is None:
        # Case-insensitive fallback
        col = next(
            (c for c in prof.columns if c.name.lower() == column.lower()),
            None,
        )
    if col is None:
        return _err(f"Unknown column '{column}' in '{table}'.")
    data = col.model_dump(mode="json")
    summary = (
        f"{table}.{col.name}: dtype={col.dtype.value}, cardinality={col.cardinality.value}, "
        f"distinct={col.distinct_count}/{col.row_count}, pii={col.pii_class.value}, "
        f"hits={col.name_pattern_hits}"
    )
    return _ok(summary, data)


def find_columns_matching(ctx: ToolContext, pattern: str) -> dict[str, Any]:
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return _err(f"Invalid regex: {exc}")
    hits: list[dict[str, Any]] = []
    for prof in ctx.profiles:
        for col in prof.columns:
            if regex.search(col.name) or any(regex.search(h) for h in col.name_pattern_hits):
                hits.append(
                    {
                        "table": prof.name,
                        "column": col.name,
                        "dtype": col.dtype.value,
                        "name_pattern_hits": col.name_pattern_hits,
                        "pii_class": col.pii_class.value,
                    }
                )
    return _ok(f"{len(hits)} column(s) match /{pattern}/", {"matches": hits})


def get_sample_rows(ctx: ToolContext, table: str, n: int = 3) -> dict[str, Any]:
    prof = ctx.find_profile(table)
    if not prof:
        return _err(f"Unknown table '{table}'.")
    n = max(1, min(int(n), 10))
    # Locate the CSV file for this table
    csv_path = next(
        (p for p in ctx.csv_paths if p.stem == prof.name),
        None,
    )
    if not csv_path:
        return _err(f"Could not locate CSV for table '{table}'.")
    try:
        df = io_utils.load_csv(csv_path)
        rows = [{c: str(v) for c, v in r.items()} for _, r in df.head(n).iterrows()]
    except Exception as exc:
        return _err(f"Failed to read CSV: {exc}")
    return _ok(f"{len(rows)} sample row(s) from '{table}'", {"rows": rows})


# ----------------------------------------------------------------------
# DETERMINISTIC HELPERS — wrap rules + patterns
# ----------------------------------------------------------------------


def run_deterministic_rules(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    from tg_schema_agent import rules

    recs = rules.run_all(ctx.profiles)
    grouped = rules.by_rule(recs)
    out_groups = {
        rid: [
            {
                "rule_id": r.rule_id,
                "action": r.action,
                "target": r.target,
                "rationale": r.rationale,
                "metadata": dict(r.metadata),
            }
            for r in rs
        ]
        for rid, rs in grouped.items()
    }
    summary = (
        f"{len(recs)} recommendation(s) across {len(grouped)} rule(s): "
        + ", ".join(f"{rid}={len(rs)}" for rid, rs in grouped.items())
    )
    return _ok(summary, {"recommendations": out_groups, "total": len(recs)})


def match_pattern_library(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    from tg_schema_agent import patterns as patterns_mod

    matches = patterns_mod.match_pattern(ctx.profiles, ctx.use_case)
    out = [
        {
            "vertex_name": m.vertex_name,
            "source_table": m.source_table,
            "source_columns": list(m.source_columns),
            "confidence": round(m.confidence, 2),
            "column_matches": [
                {"column": c.column, "reason": c.reason, "confidence": round(c.confidence, 2)}
                for c in m.column_matches
            ],
        }
        for m in matches
    ]
    summary = (
        f"{len(matches)} canonical vertex match(es) for {ctx.use_case.value}: "
        + ", ".join(m.vertex_name for m in matches)
    )
    return _ok(summary, {"matches": out})


def analyze_column_for_promotion(
    ctx: ToolContext, table: str, column: str, **_kwargs: Any
) -> dict[str, Any]:
    """Score a single column against the rule heuristics (Autograph B5/B7).

    Returns a structured `{should_promote, recommend_as, reasons,
    evidence}` payload so the agent can ground each `propose_vertex`
    call in something concrete instead of LLM intuition.

    Use this BEFORE a non-obvious `propose_vertex` to validate the
    promotion decision; pair with `record_assumption` to capture the
    reasoning in the schema's audit trail.
    """
    prof = ctx.find_profile(table)
    if not prof:
        return _err(f"Unknown table '{table}'.")
    col = prof.column(column)
    if not col:
        return _err(f"Unknown column '{column}' in table '{table}'.")

    reasons: list[str] = []
    evidence: dict[str, Any] = {
        "dtype": col.dtype.value,
        "distinct_count": col.distinct_count,
        "row_count": col.row_count,
        "null_pct": round(col.null_pct, 3),
        "cardinality": col.cardinality.value,
        "name_pattern_hits": list(col.name_pattern_hits),
        "pii_class": col.pii_class.value,
    }

    # Heuristics — mirror the rules in src/tg_schema_agent/rules.py
    is_shared_identifier = any(
        hit in {"device", "ip", "email", "phone", "card", "ssn", "address"}
        for hit in col.name_pattern_hits
    )
    if is_shared_identifier:
        reasons.append(
            f"shared-identifier pattern hits {col.name_pattern_hits} — "
            "promotion enables cross-account ring detection"
        )
    if col.is_primary_key_candidate:
        reasons.append("flagged as a primary-key candidate by the profiler")
    if col.is_foreign_key_candidate:
        reasons.append(
            "flagged as a foreign-key candidate — usually becomes an edge endpoint"
        )

    # Cardinality-based promotion (geographic / categorical hubs)
    distinct = col.distinct_count
    rows = max(1, col.row_count)
    if rows > 50 and 5 <= distinct <= 500 and col.dtype.value in {"STRING", "CATEGORICAL"}:
        reasons.append(
            f"low-medium cardinality ({distinct} distinct over {rows} rows) — "
            "candidate vertex for traversal-driven grouping"
        )
    if distinct <= 1:
        reasons.append("single-value column — drop")
    if col.null_pct > 0.9:
        reasons.append(f"{col.null_pct:.0%} null — drop")

    # Decide
    should_promote = bool(
        is_shared_identifier
        or col.is_primary_key_candidate
        or (rows > 50 and 5 <= distinct <= 500 and col.dtype.value in {"STRING", "CATEGORICAL"})
    )
    if distinct <= 1 or col.null_pct > 0.9:
        recommend_as = "drop"
        should_promote = False
    elif should_promote:
        recommend_as = "vertex_or_primary_id"
    else:
        recommend_as = "attribute"

    return _ok(
        f"{table}.{column}: recommend_as={recommend_as}",
        {
            "table": table,
            "column": column,
            "should_promote": should_promote,
            "recommend_as": recommend_as,
            "reasons": reasons,
            "evidence": evidence,
        },
    )


def summarize_discovery(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    """Categorize every input column for the discovery narrative (Autograph B3).

    Returns a structured summary the agent can speak from:
      "I see 7 tables and 142 columns total. 5 tables look relevant
       (customers, accounts, transactions, devices, merchants); I'll
       exclude internal_audit_log and marketing_campaigns because they
       don't share identifiers with the rest. Of the 30 columns in
       relevant tables, 8 will become vertex primary IDs, 14 will be
       vertex attributes, and 8 I'm dropping because they're internal
       flags / redundant FKs / contain only one value."

    Uses the existing rule-engine output for column categorization plus
    pattern matches for table relevance — no new heuristics, just a
    presentation layer.
    """
    from tg_schema_agent import patterns as patterns_mod
    from tg_schema_agent import rules

    # 1. Table relevance: a table is "relevant" if at least one pattern
    #    (across all use_cases) matches at least one vertex against it.
    from tg_schema_agent.patterns import load_patterns

    table_to_matched_vertices: dict[str, set[str]] = {p.name: set() for p in ctx.profiles}
    for uc in load_patterns():
        for m in patterns_mod.match_pattern(ctx.profiles, uc):
            table_to_matched_vertices.setdefault(m.source_table, set()).add(m.vertex_name)

    relevant_tables = [
        p.name for p in ctx.profiles if table_to_matched_vertices.get(p.name)
    ]
    excluded_tables = [
        {
            "name": p.name,
            "reason": (
                "no canonical entities matched any of the 7 industry "
                "patterns — likely an internal / log / config table"
            ),
        }
        for p in ctx.profiles
        if not table_to_matched_vertices.get(p.name)
    ]

    # 2. Column categorization via the rule engine.
    recs = rules.run_all(ctx.profiles)
    promoted_cols: set[tuple[str, str]] = set()  # (table, column)
    pii_cols: set[tuple[str, str]] = set()
    for r in recs:
        if r.action in ("promote_vertex", "promote_event_vertex"):
            table = str(r.metadata.get("table") or "")
            col = str(r.metadata.get("column") or r.target)
            if table and col:
                promoted_cols.add((table, col))
        if r.action == "tag_pii":
            table = str(r.metadata.get("table") or "")
            col = str(r.metadata.get("column") or r.target)
            if table and col:
                pii_cols.add((table, col))

    promoted_list: list[dict[str, str]] = []
    attribute_list: list[dict[str, str]] = []
    dropped_list: list[dict[str, str]] = []

    for prof in ctx.profiles:
        if prof.name not in relevant_tables:
            continue
        for col in prof.columns:
            key = (prof.name, col.name)
            entry = {"table": prof.name, "column": col.name, "dtype": col.dtype.value}
            if key in promoted_cols or col.is_primary_key_candidate:
                promoted_list.append({**entry, "as": "vertex_or_primary_id"})
            elif col.distinct_count <= 1 or col.null_pct > 0.9:
                reason = (
                    "single-value or >90% null — no graph signal"
                    if col.distinct_count <= 1 or col.null_pct > 0.9
                    else "no rule promoted it"
                )
                dropped_list.append({**entry, "reason": reason})
            else:
                attribute_list.append({**entry})

    total_cols = sum(len(p.columns) for p in ctx.profiles)
    summary = (
        f"{len(ctx.profiles)} table(s) ({len(relevant_tables)} relevant, "
        f"{len(excluded_tables)} excluded), {total_cols} columns total: "
        f"{len(promoted_list)} promoted, {len(attribute_list)} attributes, "
        f"{len(dropped_list)} dropped."
    )
    return _ok(
        summary,
        {
            "tables": {
                "total": len(ctx.profiles),
                "relevant": relevant_tables,
                "excluded": excluded_tables,
            },
            "columns": {
                "promoted_to_vertex_or_primary": promoted_list,
                "kept_as_attribute": attribute_list,
                "dropped": dropped_list,
            },
        },
    )


def match_all_patterns(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    """Rank ALL industry patterns by how well they fit the data (Autograph B4).

    Returns a sorted list (best fit first). Score is matched_vertices /
    pattern_vertex_count — normalized so smaller patterns don't win by
    sheer brevity. The agent uses this in Stage 3 (HYPOTHESIZE) to STATE
    its pattern recognition explicitly, e.g.
        "This looks like a fraud-investigation shape (8/10 entities
         matched). Customer 360 was second (5/8)."

    The current ctx.use_case is a HINT, not a constraint — if the top
    pattern disagrees with the hint, the agent should follow the data
    while staying anchored to the user's stated decision.
    """
    from tg_schema_agent import patterns as patterns_mod
    from tg_schema_agent.patterns import load_patterns

    all_patterns = load_patterns()
    rankings: list[dict[str, Any]] = []
    for uc, pattern in all_patterns.items():
        matches = patterns_mod.match_pattern(ctx.profiles, uc)
        total_vertices = max(1, len(pattern.vertices))
        matched_count = len(matches)
        score = matched_count / total_vertices
        matched_names = [m.vertex_name for m in matches]
        missing_names = [
            v.name for v in pattern.vertices if v.name not in matched_names
        ]
        rankings.append(
            {
                "use_case": uc.value,
                "pattern_name": pattern.name,
                "score": round(score, 3),
                "matched_count": matched_count,
                "pattern_vertex_count": total_vertices,
                "matched_vertices": matched_names,
                "missing_vertices": missing_names,
                "is_current_hint": uc == ctx.use_case,
            }
        )
    rankings.sort(key=lambda r: r["score"], reverse=True)

    # Build a one-line summary the agent can quote in chat
    top = rankings[0] if rankings else None
    if top:
        runner_up = rankings[1] if len(rankings) > 1 else None
        summary_parts = [
            f"Best fit: {top['use_case']} "
            f"({top['matched_count']}/{top['pattern_vertex_count']} entities)"
        ]
        if runner_up:
            summary_parts.append(
                f"runner-up {runner_up['use_case']} "
                f"({runner_up['matched_count']}/{runner_up['pattern_vertex_count']})"
            )
        if not top["is_current_hint"]:
            summary_parts.append(
                f"current hint {ctx.use_case.value} ranked lower — consider switching"
            )
        summary = "; ".join(summary_parts)
    else:
        summary = "No patterns matched."

    return _ok(summary, {"rankings": rankings})


# ----------------------------------------------------------------------
# MUTATE tools — propose / remove vertices and edges
# ----------------------------------------------------------------------


def propose_vertex(
    ctx: ToolContext,
    name: str,
    primary_id: str,
    source_table: str,
    source_columns: list[str] | None = None,
    dtype: str = "STRING",
    attributes: list[dict[str, Any]] | None = None,
    rationale: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    if not name or not primary_id or not source_table:
        return _err("propose_vertex requires name, primary_id, source_table.")
    existing = ctx.working_schema.vertex(name)
    if existing:
        return _err(f"Vertex '{name}' already exists in the working schema.")

    cols = list(source_columns) if source_columns else [primary_id]
    cols = [c for c in cols if c]
    if not cols:
        cols = [primary_id]

    attrs_out: list[Attribute] = []
    for a in attributes or []:
        try:
            attrs_out.append(
                Attribute(
                    name=str(a.get("name", "")),
                    dtype=DataKind(_norm_dtype(a.get("dtype"))),
                    source_table=source_table,
                    source_column=str(a.get("source_column", a.get("name", ""))),
                    pii_class=PIIClass(str(a.get("pii_class", "NONE")).upper()) if a.get("pii_class") else PIIClass.NONE,
                    nullable=bool(a.get("nullable", True)),
                )
            )
        except Exception:  # noqa: BLE001
            continue

    try:
        vertex = Vertex(
            name=name,
            primary_id=primary_id,
            primary_id_dtype=DataKind(_norm_dtype(dtype)),
            attributes=attrs_out,
            source=VertexSource(
                kind="column_group" if len(cols) > 1 else "table_column",
                table=source_table,
                columns=cols,
            ),
            rationale=rationale,
            pattern_origin=None,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"Invalid vertex args: {exc}")

    ctx.working_schema.vertices.append(vertex)
    ctx.persist_schema()
    return _ok(
        f"Added vertex '{name}' (pk={primary_id}, attrs={len(attrs_out)})",
        {"vertex": vertex.model_dump(mode="json")},
    )


def propose_edge(
    ctx: ToolContext,
    name: str,
    from_vertex: str,
    to_vertex: str,
    direction: str = "DIRECTED_WITH_REVERSE",
    reverse_name: str | None = None,
    attributes: list[dict[str, Any]] | None = None,
    rationale: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    if not name or not from_vertex or not to_vertex:
        return _err("propose_edge requires name, from_vertex, to_vertex.")
    if not ctx.working_schema.vertex(from_vertex):
        return _err(
            f"from_vertex '{from_vertex}' not in working schema. "
            "Call propose_vertex first."
        )
    if not ctx.working_schema.vertex(to_vertex):
        return _err(
            f"to_vertex '{to_vertex}' not in working schema. "
            "Call propose_vertex first."
        )
    # First do the canonical-name normalization, THEN dedupe.
    # Otherwise the LLM can submit two different bare-verb names
    # (e.g. "OWNS" and "HAS_ACCOUNT") that both normalize to the same
    # canonical edge ("Customer_OWNS_Account") and slip past the
    # duplicate check, ending up as two ADD EDGE statements in the
    # schema-change job — TigerGraph then rejects it as "Duplicate
    # add edge: Customer_OWNS_Account".
    from tg_schema_agent.llm.chat_agent import _normalize_edge_names_to_canonical

    edge_dict = {
        "name": name,
        "from_vertex": from_vertex,
        "to_vertex": to_vertex,
        "reverse_edge_name": reverse_name,
    }
    _normalize_edge_names_to_canonical({"edges": [edge_dict]}, ctx.pattern)
    canonical_name = edge_dict["name"]
    canonical_reverse = edge_dict.get("reverse_edge_name") or reverse_name

    # Dedupe by canonical name AND by (from_vertex, to_vertex) — the LLM
    # sometimes proposes the same logical edge under a different name
    # later in the turn ("Customer_OWNS_Account" and again as
    # "Customer_HAS_Account").
    if ctx.working_schema.edge(canonical_name):
        return _err(
            f"Edge '{canonical_name}' already exists (you proposed it earlier as "
            f"'{name}' if different)."
        )
    for existing in ctx.working_schema.edges:
        if (
            existing.from_vertex == from_vertex
            and existing.to_vertex == to_vertex
        ):
            return _err(
                f"An edge from {from_vertex} to {to_vertex} already exists "
                f"as '{existing.name}'. Refine that one instead of adding "
                "a parallel edge with a different name."
            )

    try:
        direction_enum = EdgeDirection(direction.upper()) if direction else EdgeDirection.DIRECTED_WITH_REVERSE
    except ValueError:
        direction_enum = EdgeDirection.DIRECTED_WITH_REVERSE

    attrs_out: list[Attribute] = []
    primary_table = ctx.profiles[0].name if ctx.profiles else ""
    for a in attributes or []:
        try:
            attrs_out.append(
                Attribute(
                    name=str(a.get("name", "")),
                    dtype=DataKind(_norm_dtype(a.get("dtype"))),
                    source_table=str(a.get("source_table", primary_table)),
                    source_column=str(a.get("source_column", a.get("name", ""))),
                    nullable=bool(a.get("nullable", True)),
                )
            )
        except Exception:  # noqa: BLE001
            continue

    try:
        edge = Edge(
            name=canonical_name,
            from_vertex=from_vertex,
            to_vertex=to_vertex,
            direction=direction_enum,
            reverse_edge_name=canonical_reverse,
            attributes=attrs_out,
            source=EdgeSource(kind="derived", table=primary_table),
            rationale=rationale,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"Invalid edge args: {exc}")

    ctx.working_schema.edges.append(edge)
    ctx.persist_schema()
    return _ok(
        f"Added edge '{canonical_name}' ({from_vertex} -> {to_vertex})",
        {"edge": edge.model_dump(mode="json")},
    )


def remove_vertex(ctx: ToolContext, name: str, **_kwargs: Any) -> dict[str, Any]:
    before_v = len(ctx.working_schema.vertices)
    ctx.working_schema.vertices = [
        v for v in ctx.working_schema.vertices if v.name != name
    ]
    removed_v = before_v - len(ctx.working_schema.vertices)
    before_e = len(ctx.working_schema.edges)
    ctx.working_schema.edges = [
        e
        for e in ctx.working_schema.edges
        if e.from_vertex != name and e.to_vertex != name
    ]
    removed_e = before_e - len(ctx.working_schema.edges)
    if removed_v == 0:
        return _err(f"Vertex '{name}' not found.")
    ctx.persist_schema()
    return _ok(
        f"Removed vertex '{name}' and {removed_e} edge(s) that referenced it.",
        {"removed_vertex": name, "removed_edges": removed_e},
    )


def remove_edge(ctx: ToolContext, name: str, **_kwargs: Any) -> dict[str, Any]:
    before = len(ctx.working_schema.edges)
    ctx.working_schema.edges = [
        e for e in ctx.working_schema.edges if e.name != name
    ]
    removed = before - len(ctx.working_schema.edges)
    if removed == 0:
        return _err(f"Edge '{name}' not found.")
    ctx.persist_schema()
    return _ok(f"Removed edge '{name}'.", {"removed_edge": name})


# ----------------------------------------------------------------------
# SELF-CHECK tools — validator + scorer
# ----------------------------------------------------------------------


def validate_schema(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    from tg_schema_agent import validator

    result = validator.validate(ctx.working_schema)
    try:
        io_utils.dump_json(
            result.model_dump(mode="json"),
            ctx.workspace_dir / "validation.json",
        )
    except Exception:
        pass
    answerable = result.answerable_questions
    unanswerable = result.unanswerable_questions
    summary = f"{len(answerable)}/{len(answerable) + len(unanswerable)} target questions answerable"
    if unanswerable:
        summary += f"; missing: {unanswerable}"
    return _ok(summary, result.model_dump(mode="json"))


def score_schema(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    from tg_schema_agent import scorer, validator

    val = validator.validate(ctx.working_schema)
    score = scorer.score_schema(
        ctx.working_schema, val, ctx.pattern, user_prompt=ctx.user_prompt
    )
    try:
        io_utils.dump_json(score.model_dump(mode="json"), ctx.workspace_dir / "score.json")
    except Exception:
        pass
    return _ok(
        f"Score {score.total}/100 — {len(val.answerable_questions)} questions answerable",
        score.model_dump(mode="json"),
    )


# ----------------------------------------------------------------------
# TERMINATING tools
# ----------------------------------------------------------------------


def finalize_schema(
    ctx: ToolContext,
    user_summary: str = "",
    design_rationale: list[str] | None = None,
    recommended_entities: list[dict] | None = None,
    expected_outcomes: list[str] | None = None,
    future_enhancements: list[str] | None = None,
    suggested_replies: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Mark the schema as final and populate the Autograph presentation
    fields (Behaviors 6 + 8). The loop driver picks this up and emits the
    `final` SSE event with type=propose_schema.

    Args:
        user_summary: One-sentence outcomes-language summary the user sees.
        design_rationale: 3-6 bullets explaining the overall architectural
            choices (NOT per-vertex — that's the `rationale` field on each
            Vertex/Edge). Examples: "Modeled Device + IPAddress as
            vertices so multi-account sharing surfaces as edges, not
            joins." (Behavior 6)
        recommended_entities: List of {name, one_liner} dicts naming each
            recommended entity with a one-line purpose. (Behavior 8)
        expected_outcomes: Capabilities the graph unlocks, in outcomes
            language. Distinct from "questions answerable" — these are
            broader (e.g. "Detect fraud rings spanning shared infra").
        future_enhancements: Explicit deferred work the user should know
            about — e.g. "Real-time scoring service",
            "Geospatial features", "Graph algorithms (Louvain)".
        suggested_replies: 3-4 short follow-up chips to surface after the
            schema lands (Behavior 7). Examples:
            ["Any compliance requirements?",
             "Additional questions to answer?",
             "Future use cases to design for now?"]
    """
    if not ctx.working_schema.vertices:
        return _err("Cannot finalize an empty schema. Propose vertices first.")

    # Behavior 6 — design rationale
    if design_rationale:
        bullets = [b.strip() for b in design_rationale if b and b.strip()]
        if bullets:
            ctx.working_schema.design_rationale = DesignRationale(bullets=bullets)

    # Behavior 8 — recommendations summary
    rec_entities: list[RecommendedEntity] = []
    for e in recommended_entities or []:
        name = str(e.get("name", "")).strip() if isinstance(e, dict) else ""
        one_liner = str(e.get("one_liner", "")).strip() if isinstance(e, dict) else ""
        if name:
            rec_entities.append(RecommendedEntity(name=name, one_liner=one_liner))
    outcomes = [o.strip() for o in (expected_outcomes or []) if o and o.strip()]
    future = [f.strip() for f in (future_enhancements or []) if f and f.strip()]
    if rec_entities or outcomes or future:
        ctx.working_schema.recommendation = RecommendationSummary(
            entities=rec_entities,
            expected_outcomes=outcomes,
            future_enhancements=future,
        )

    ctx.persist_schema()

    # Behavior 7 — follow-up suggested replies the chat layer renders as chips.
    # Default set covers compliance + additional questions + future use cases
    # so the agent doesn't ship without inviting validation.
    chips = [s.strip() for s in (suggested_replies or []) if s and s.strip()] or [
        "Any additional questions this should answer?",
        "Compliance or audit requirements to design for?",
        "Future use cases worth designing for now?",
    ]

    return _ok(
        user_summary
        or f"Schema finalized: {len(ctx.working_schema.vertices)} vertices, "
        f"{len(ctx.working_schema.edges)} edges.",
        {
            "schema": ctx.working_schema.model_dump(mode="json"),
            "user_summary": user_summary or "Schema finalized.",
            "suggested_replies": chips,
        },
    )


def ask_user(
    ctx: ToolContext,
    question: str,
    suggested_replies: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Pause the loop and ask the user for clarification."""
    if not question or not question.strip():
        return _err("ask_user requires a non-empty question.")
    return _ok(
        question,
        {
            "question": question,
            "suggested_replies": list(suggested_replies) if suggested_replies else [],
        },
    )


def reply_to_user(
    ctx: ToolContext,
    message: str,
    suggested_replies: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """End the turn with a plain reply (no question, no schema finalization).

    Use this as the FINAL call on any turn that ran live MCP ops
    (deploy_schema_live, load_data_live, run_query_live, etc.) — once the
    requested action is done, call reply_to_user with a one-line summary
    of what happened. This is the only way to terminate a live-ops turn
    cleanly; without it the agent will loop and hit the iteration cap.
    """
    if not message or not message.strip():
        return _err("reply_to_user requires a non-empty 'message'.")
    return _ok(
        message,
        {
            "message": message,
            "suggested_replies": list(suggested_replies) if suggested_replies else [],
        },
    )


def record_business_context(
    ctx: ToolContext,
    domain: str = "",
    sub_scenarios: list[str] | None = None,
    goal_type: str = "",
    business_questions: list[str] | None = None,
    stakeholders: list[str] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Record the business context for this design session (Autograph B2).

    Called during Stage 1 — DECISION, right after the user names their
    decision and BEFORE any data inspection. Captures the structured
    context that drives pattern matching, validation, and starter queries.
    """
    domain = (domain or "").strip().lower()
    if not domain:
        return _err(
            "record_business_context requires a 'domain' string "
            "(e.g. 'fraud', 'customer_360', 'entity_resolution', "
            "'supply_chain', 'cybersecurity', 'knowledge_graph')."
        )
    gt = (goal_type or "").strip().lower()
    if gt and gt not in {"detection", "investigation", "explainability", "risk_scoring"}:
        gt = ""

    bc = BusinessContext(
        domain=domain,
        sub_scenarios=[s.strip() for s in (sub_scenarios or []) if s and s.strip()],
        goal_type=gt,  # type: ignore[arg-type]
        business_questions=[
            q.strip() for q in (business_questions or []) if q and q.strip()
        ],
        stakeholders=[s.strip() for s in (stakeholders or []) if s and s.strip()],
    )
    ctx.working_schema.business_context = bc

    summary_parts = [f"domain={domain}"]
    if bc.sub_scenarios:
        summary_parts.append(f"scenarios={','.join(bc.sub_scenarios[:3])}")
    if bc.goal_type:
        summary_parts.append(f"goal={bc.goal_type}")
    return _ok(
        "Recorded business context: " + " · ".join(summary_parts),
        {"business_context": bc.model_dump(mode="json")},
    )


def record_assumption(
    ctx: ToolContext,
    text: str,
    evidence: str = "",
    confidence: str = "medium",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Capture a modeling assumption the agent is committing to.

    Use this when committing to a non-obvious decision (e.g. "treating
    transactions table as the system of record because it has 71k rows and
    no FKs pointing in"). The evidence field grounds the assumption in
    something concrete; confidence is high/medium/low.
    """
    if not text or not text.strip():
        return _err("record_assumption requires non-empty 'text'.")
    # B3 (evidence gate): force the agent to ground each assumption in
    # something observable. Empty/whitespace evidence → reject. This
    # prevents post-hoc vibes-only assumptions from polluting the trail.
    if not evidence or not evidence.strip():
        return _err(
            "record_assumption requires non-empty 'evidence'. Cite the "
            "column name, row count, distinct-value count, or sample value "
            "you observed that supports this assumption."
        )
    conf = confidence.lower().strip()
    if conf not in {"high", "medium", "low"}:
        conf = "medium"
    a = Assumption(text=text.strip(), evidence=evidence.strip(), confidence=conf)  # type: ignore[arg-type]
    ctx.working_schema.assumptions.append(a)
    return _ok(
        f"Recorded assumption ({conf}): {text.strip()[:90]}",
        {
            "assumption": a.model_dump(mode="json"),
            "count": len(ctx.working_schema.assumptions),
        },
    )


# ----------------------------------------------------------------------
# Dispatch table + executor
# ----------------------------------------------------------------------


from tg_schema_agent.llm.live_tools import (
    deploy_schema_live,
    drop_graph_data_live,
    generate_starter_queries_live,
    get_graph_state_live,
    install_query_live,
    load_data_live,
    run_query_live,
    wipe_graph_live,
)


TOOL_DISPATCH = {
    "list_tables": list_tables,
    "inspect_column": inspect_column,
    "find_columns_matching": find_columns_matching,
    "get_sample_rows": get_sample_rows,
    "run_deterministic_rules": run_deterministic_rules,
    "match_pattern_library": match_pattern_library,
    "match_all_patterns": match_all_patterns,
    "summarize_discovery": summarize_discovery,
    "analyze_column_for_promotion": analyze_column_for_promotion,
    "propose_vertex": propose_vertex,
    "propose_edge": propose_edge,
    "remove_vertex": remove_vertex,
    "remove_edge": remove_edge,
    "validate_schema": validate_schema,
    "score_schema": score_schema,
    "record_business_context": record_business_context,
    "record_assumption": record_assumption,
    "finalize_schema": finalize_schema,
    "ask_user": ask_user,
    "reply_to_user": reply_to_user,
    # Live TigerGraph MCP tools (all scoped to TG_GRAPHNAME via _enforce_scope)
    "deploy_schema_live": deploy_schema_live,
    "load_data_live": load_data_live,
    "get_graph_state_live": get_graph_state_live,
    "generate_starter_queries_live": generate_starter_queries_live,
    "install_query_live": install_query_live,
    "run_query_live": run_query_live,
    "drop_graph_data_live": drop_graph_data_live,
    "wipe_graph_live": wipe_graph_live,
}


TERMINATING_TOOLS = {"finalize_schema", "ask_user", "reply_to_user"}
MUTATING_TOOLS = {
    "propose_vertex",
    "propose_edge",
    "remove_vertex",
    "remove_edge",
    "record_assumption",
    "record_business_context",
}


async def execute_tool(
    ctx: ToolContext, name: str, args: dict[str, Any] | str | None
) -> dict[str, Any]:
    """Dispatch a function call to the matching tool. Catches all exceptions.

    Tools may be either sync (returning a dict) or async (returning a
    coroutine that resolves to a dict). The new live-MCP tools are async
    because they spawn an MCP subprocess; the schema-design tools are sync.
    """
    import asyncio

    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return _err(f"Unknown tool '{name}'.")

    # Gemini sometimes returns args as a JSON string instead of a dict
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return _err(f"Could not parse args for '{name}'.")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _err(f"Bad args type for '{name}': {type(args).__name__}")

    try:
        result = fn(ctx, **args)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except TypeError as exc:
        return _err(f"Invalid args for '{name}': {exc}")
    except Exception as exc:  # noqa: BLE001
        return _err(f"Tool '{name}' failed: {type(exc).__name__}: {exc}")


# ----------------------------------------------------------------------
# FunctionDeclaration generators for Gemini
# ----------------------------------------------------------------------


def build_function_declarations() -> list[Any]:
    """Build google.genai FunctionDeclaration objects for every tool.

    Returns a list of `genai_types.Tool` (one Tool containing many
    function_declarations) ready to be passed to GenerateContentConfig.

    Imports happen inside the function so this module is importable even
    if google-genai isn't installed (e.g. during unit tests).
    """
    from google.genai import types as genai_types

    decls = [
        genai_types.FunctionDeclaration(
            name="list_tables",
            description=(
                "Always call this first. Returns every uploaded table with row count, "
                "column names, primary key, and signature flags (event/wide). Use this "
                "to understand what data is available before reasoning."
            ),
            parameters=genai_types.Schema(
                type="OBJECT", properties={}, required=[]
            ),
        ),
        genai_types.FunctionDeclaration(
            name="inspect_column",
            description=(
                "Get detailed stats for one column: dtype, cardinality, null %, "
                "distinct count, name-pattern hits, PII class, sample values. "
                "Call this whenever you need to decide if a column is a primary id, "
                "a shared identifier, or a regular attribute."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "table": genai_types.Schema(type="STRING"),
                    "column": genai_types.Schema(type="STRING"),
                },
                required=["table", "column"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="find_columns_matching",
            description=(
                "Find every column across all tables whose name or name-pattern hits "
                "match the given regex (case-insensitive). Useful for surveys like "
                "'find all timestamp-like columns' or 'find any PII columns'."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "pattern": genai_types.Schema(
                        type="STRING",
                        description="Regex (case-insensitive).",
                    ),
                },
                required=["pattern"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="get_sample_rows",
            description=(
                "Fetch N raw sample rows from a table. Use when the dtype + name "
                "don't tell you enough about the column's actual values."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "table": genai_types.Schema(type="STRING"),
                    "n": genai_types.Schema(type="INTEGER"),
                },
                required=["table"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="run_deterministic_rules",
            description=(
                "Run the deterministic rule engine over all profiled tables and "
                "return its Recommendations (shared-identifier promotions, event "
                "vertices, wide-table decompositions, PII tags, etc.). Always "
                "consult this before proposing vertices — it tells you which "
                "columns to promote."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="match_pattern_library",
            description=(
                "Run the canonical-pattern matcher for the current use case and "
                "return which pattern vertices were detected in the data, with "
                "confidence scores. This tells you the canonical entity names + "
                "source columns to use when proposing vertices."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="analyze_column_for_promotion",
            description=(
                "Score a single column against the rule heuristics. Returns "
                "{should_promote, recommend_as: 'vertex_or_primary_id' | "
                "'attribute' | 'drop', reasons[], evidence{}}. Use BEFORE a "
                "non-obvious propose_vertex so the decision is grounded in "
                "observed shared-identifier hits, cardinality, null %, and "
                "PII class — not just LLM intuition. Pair with "
                "record_assumption when you commit to the choice."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "table": genai_types.Schema(type="STRING"),
                    "column": genai_types.Schema(type="STRING"),
                },
                required=["table", "column"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="summarize_discovery",
            description=(
                "Categorize every input column for the discovery narrative. "
                "Returns three lists: tables (relevant + excluded with "
                "reason), columns (promoted to vertex / kept as attribute "
                "/ dropped with reason). Call ONCE in Stage 2 — INVESTIGATE, "
                "after list_tables and run_deterministic_rules. Use the "
                "result to narrate the 'I see X, excluded Y because Z' beat "
                "explicitly to the user."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="match_all_patterns",
            description=(
                "Rank ALL industry patterns (fraud, customer_360, "
                "entity_resolution, supply_chain, cybersecurity, "
                "knowledge_graph, recommendation) by how well they fit "
                "the data. Returns a sorted list with matched/total entity "
                "counts per pattern. Use this in Stage 3 (HYPOTHESIZE) "
                "BEFORE match_pattern_library so you can STATE the "
                "pattern recognition explicitly in chat. The current "
                "use_case is a hint, not a constraint — if a different "
                "pattern fits the data better AND the user's stated "
                "decision, say so and use that one."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="propose_vertex",
            description=(
                "Add one vertex to the working schema. Prefer canonical names "
                "(Customer, Account, Transaction, Merchant, Device, IPAddress, "
                "Email, Phone, Card, Address) returned by match_pattern_library."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "name": genai_types.Schema(type="STRING"),
                    "primary_id": genai_types.Schema(type="STRING"),
                    "source_table": genai_types.Schema(type="STRING"),
                    "source_columns": genai_types.Schema(
                        type="ARRAY", items=genai_types.Schema(type="STRING")
                    ),
                    "dtype": genai_types.Schema(type="STRING"),
                    "attributes": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(
                            type="OBJECT",
                            properties={
                                "name": genai_types.Schema(type="STRING"),
                                "dtype": genai_types.Schema(type="STRING"),
                                "source_column": genai_types.Schema(type="STRING"),
                                "pii_class": genai_types.Schema(type="STRING"),
                                "nullable": genai_types.Schema(type="BOOLEAN"),
                            },
                        ),
                    ),
                    "rationale": genai_types.Schema(type="STRING"),
                },
                required=["name", "primary_id", "source_table"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="propose_edge",
            description=(
                "Add one edge to the working schema. Edge name MUST follow "
                "<FromVertex>_<VERB>_<ToVertex> (e.g. Customer_OWNS_Account). "
                "The server normalizes the name to canonical form automatically "
                "if you propose a bare verb. Almost always use "
                "direction='DIRECTED_WITH_REVERSE' so multi-hop queries work."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "name": genai_types.Schema(type="STRING"),
                    "from_vertex": genai_types.Schema(type="STRING"),
                    "to_vertex": genai_types.Schema(type="STRING"),
                    "direction": genai_types.Schema(type="STRING"),
                    "reverse_name": genai_types.Schema(type="STRING"),
                    "attributes": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(type="OBJECT", properties={}),
                    ),
                    "rationale": genai_types.Schema(type="STRING"),
                },
                required=["name", "from_vertex", "to_vertex"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="remove_vertex",
            description="Remove a vertex (and any edges that touch it) from the working schema.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={"name": genai_types.Schema(type="STRING")},
                required=["name"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="remove_edge",
            description="Remove an edge from the working schema by name.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={"name": genai_types.Schema(type="STRING")},
                required=["name"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="validate_schema",
            description=(
                "Run the structural + query-coverage validator on the current "
                "working schema. Returns which target questions are answerable. "
                "Always call this before finalize_schema."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="score_schema",
            description=(
                "Compute the 10-dimension quality score. Returns total (0-100) + "
                "breakdown + strengths + gaps. If total < 85 OR any target "
                "question is unanswerable, iterate by adding missing entities/edges."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="finalize_schema",
            description=(
                "Declare the schema complete AND populate the Autograph "
                "presentation fields. The loop terminates after this call. "
                "REQUIRED user_summary is a one-sentence outcomes-language "
                "summary. STRONGLY RECOMMENDED to also pass: "
                "design_rationale (3-6 bullets explaining architectural "
                "choices); recommended_entities (one-liner per vertex); "
                "expected_outcomes (capabilities the graph unlocks); "
                "future_enhancements (deferred work to flag). These power "
                "the post-finalize Outcomes panel — leaving them empty "
                "ships a less-good demo."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "user_summary": genai_types.Schema(
                        type="STRING",
                        description="One-sentence outcomes-language summary.",
                    ),
                    "design_rationale": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(type="STRING"),
                        description=(
                            "3-6 bullets explaining the OVERALL architectural "
                            "choices: why certain concepts are vertices vs "
                            "edges, what hub-and-spoke design you picked, "
                            "what key assumptions shape the graph. NOT "
                            "per-vertex rationale (that goes on the Vertex "
                            "objects)."
                        ),
                    ),
                    "recommended_entities": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(
                            type="OBJECT",
                            properties={
                                "name": genai_types.Schema(type="STRING"),
                                "one_liner": genai_types.Schema(type="STRING"),
                            },
                            required=["name"],
                        ),
                        description=(
                            "One row per recommended vertex with a short "
                            "purpose line."
                        ),
                    ),
                    "expected_outcomes": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(type="STRING"),
                        description=(
                            "Broad capabilities the graph unlocks (e.g. "
                            "'detect fraud rings spanning shared infra'). "
                            "Distinct from the questions in target_questions."
                        ),
                    ),
                    "future_enhancements": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(type="STRING"),
                        description=(
                            "Explicit deferred work — e.g. 'Graph algorithms "
                            "(Louvain) for community detection', 'Real-time "
                            "scoring service', 'GraphRAG embeddings'."
                        ),
                    ),
                    "suggested_replies": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(type="STRING"),
                        description=(
                            "3-4 follow-up chips rendered after the schema "
                            "ships (Behavior 7). Default suggests asking "
                            "about compliance, additional questions, and "
                            "future use cases."
                        ),
                    ),
                },
                required=["user_summary"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="ask_user",
            description=(
                "Pause the loop and ask the user for clarification. ONLY use this "
                "when ambiguity is genuine and you cannot proceed (e.g. multiple "
                "tables look like the event vertex; user gave no kickoff and the "
                "data supports several use cases; the user said something "
                "contradictory). Do NOT use this to ping-pong — pick a best guess "
                "and explain in vertex/edge rationale instead."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "question": genai_types.Schema(type="STRING"),
                    "suggested_replies": genai_types.Schema(
                        type="ARRAY", items=genai_types.Schema(type="STRING")
                    ),
                },
                required=["question"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="reply_to_user",
            description=(
                "End the turn with a plain reply. Use this as the FINAL "
                "call on any turn that ran live MCP ops (deploy_schema_live, "
                "load_data_live, run_query_live, install_query_live, "
                "drop_graph_data_live, wipe_graph_live, get_graph_state_live, "
                "generate_starter_queries_live) — once the requested action "
                "is done, call reply_to_user with a one-line summary of what "
                "happened. This is the ONLY way to terminate a live-ops turn "
                "cleanly; without it the agent loops until the iteration cap. "
                "Do NOT use this for schema design turns — use finalize_schema "
                "for those. Do NOT use this to ask a question — use ask_user."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "message": genai_types.Schema(type="STRING"),
                    "suggested_replies": genai_types.Schema(
                        type="ARRAY", items=genai_types.Schema(type="STRING")
                    ),
                },
                required=["message"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="record_business_context",
            description=(
                "Capture the business context for this design session. Call "
                "this ONCE during Stage 1 — right after the user names the "
                "decision they're trying to make, and BEFORE you inspect "
                "data. The captured context drives pattern selection, "
                "outcomes validation, and starter-query generation. "
                "Don't re-call on refinement turns unless the user changes "
                "the business problem."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "domain": genai_types.Schema(
                        type="STRING",
                        description=(
                            "Primary business domain. One of: fraud, "
                            "customer_360, entity_resolution, supply_chain, "
                            "cybersecurity, knowledge_graph, recommendation, "
                            "or a custom slug if none fit."
                        ),
                    ),
                    "sub_scenarios": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(type="STRING"),
                        description=(
                            "Specific scenarios within the domain. For "
                            "fraud: payment_fraud / account_takeover / "
                            "synthetic_identity / mule_accounts / "
                            "money_laundering / ring_investigation."
                        ),
                    ),
                    "goal_type": genai_types.Schema(
                        type="STRING",
                        description=(
                            "Primary goal: detection, investigation, "
                            "explainability, or risk_scoring. Pick the "
                            "BEST single fit; leave empty if the user "
                            "wants multiple equally."
                        ),
                    ),
                    "business_questions": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(type="STRING"),
                        description=(
                            "3-5 concrete questions the graph must answer, "
                            "in the user's own words where possible."
                        ),
                    ),
                    "stakeholders": genai_types.Schema(
                        type="ARRAY",
                        items=genai_types.Schema(type="STRING"),
                        description=(
                            "Who consumes the graph: investigators, "
                            "ml_models, ai_agents, analysts, etc."
                        ),
                    ),
                },
                required=["domain"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="record_assumption",
            description=(
                "Record a modeling assumption you are committing to right now. "
                "Use this whenever you make a non-obvious decision based on data "
                "you just inspected — e.g. 'transactions is the system of record' "
                "or 'device_id uniquely identifies devices'. Call it BEFORE the "
                "propose_vertex / propose_edge that depends on it, so the "
                "reasoning trail is evidence-grounded. The user sees these "
                "assumptions in the Outcomes panel."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "text": genai_types.Schema(
                        type="STRING",
                        description="The assumption itself, in one short sentence.",
                    ),
                    "evidence": genai_types.Schema(
                        type="STRING",
                        description=(
                            "What you observed in the data that supports it "
                            "(e.g. column name, row count, distinct values)."
                        ),
                    ),
                    "confidence": genai_types.Schema(
                        type="STRING",
                        description="One of: high, medium, low. Default medium.",
                    ),
                },
                required=["text"],
            ),
        ),
        # ---------------- live TigerGraph MCP tools ----------------
        genai_types.FunctionDeclaration(
            name="deploy_schema_live",
            description=(
                "Push the current working schema to the live TigerGraph "
                "graph (mcp_demo). DESTRUCTIVE — cascades drop of any "
                "existing graph with the same name. ALWAYS call ask_user "
                "first to confirm with the user. Requires the working "
                "schema to be non-empty (call propose_vertex / "
                "propose_edge first, or finalize_schema)."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="load_data_live",
            description=(
                "Stream the uploaded CSV into the live graph via a "
                "generated loading job. Call AFTER deploy_schema_live "
                "succeeds. Reports per-vertex row counts."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="get_graph_state_live",
            description=(
                "Read-only inspection of the live graph: existence, "
                "vertex types, edge types, per-type counts, installed "
                "queries. Safe to call anytime. Use this to confirm what's "
                "currently in TigerGraph before deciding next steps."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="generate_starter_queries_live",
            description=(
                "Use Gemini to write 5-8 starter GSQL queries tailored to "
                "the current schema and business context, dry-run validated "
                "via INTERPRET QUERY against the live graph. Persists the "
                "list in the workspace; install_query_live can install any "
                "of them. Call after deploy_schema_live."
            ),
            parameters=genai_types.Schema(type="OBJECT", properties={}, required=[]),
        ),
        genai_types.FunctionDeclaration(
            name="install_query_live",
            description=(
                "Install one starter query into the live graph by name. "
                "Requires generate_starter_queries_live to have been "
                "called first (or for the query to exist in "
                "starter_queries.json)."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "query_name": genai_types.Schema(
                        type="STRING",
                        description="Exact name from generate_starter_queries_live result.",
                    ),
                },
                required=["query_name"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="run_query_live",
            description=(
                "Run an installed query against the live graph and return "
                "results. Read-only against TigerGraph. Use after "
                "install_query_live."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "query_name": genai_types.Schema(type="STRING"),
                    "params": genai_types.Schema(
                        type="OBJECT",
                        description="Query parameters as a flat dict (optional).",
                    ),
                },
                required=["query_name"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="drop_graph_data_live",
            description=(
                "DESTRUCTIVE: clear all vertex + edge data from the live "
                "graph, keeping the schema. ALWAYS use ask_user first to "
                "confirm the user wants this. Then call with confirm=true."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "confirm": genai_types.Schema(
                        type="BOOLEAN",
                        description="Must be true. Pass after ask_user gives consent.",
                    ),
                },
                required=["confirm"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="wipe_graph_live",
            description=(
                "DESTRUCTIVE: cascade-drop all installed queries AND the "
                "entire graph. Full reset. ALWAYS use ask_user first to "
                "confirm the user wants this. Then call with confirm=true."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "confirm": genai_types.Schema(
                        type="BOOLEAN",
                        description="Must be true. Pass after ask_user gives consent.",
                    ),
                },
                required=["confirm"],
            ),
        ),
    ]

    return [genai_types.Tool(function_declarations=decls)]
