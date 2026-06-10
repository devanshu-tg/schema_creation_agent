"""Schema designer: orchestrates profiler + rules + patterns into a Schema.

Precedence:
- The pattern library is authoritative for canonical naming.
- Rules add anything outside the pattern (extra edges from FKs, transfer edges, etc.).
- Wide-denormalized tables are decomposed by R11 + pattern matching: each canonical
  pattern vertex sources from a column-group of the single wide table.
"""

from __future__ import annotations

import re
from pathlib import Path

from tg_schema_agent import io_utils, patterns, rules
from tg_schema_agent.enums import (
    DataKind,
    EdgeDirection,
    PIIClass,
    UseCase,
)
from tg_schema_agent.models import (
    Attribute,
    CanonicalAttribute,
    ColumnProfile,
    Edge,
    EdgeSource,
    EdgeSpec,
    Pattern,
    RuleRecommendation,
    Schema,
    TableProfile,
    Vertex,
    VertexSource,
    VertexSpec,
)
from tg_schema_agent.patterns import VertexMatch


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.lower())


def _find_column(table: TableProfile, name: str) -> ColumnProfile | None:
    for c in table.columns:
        if _norm(c.name) == _norm(name):
            return c
    return None


def _matching_column(
    table: TableProfile, aliases: list[str]
) -> ColumnProfile | None:
    """Return the first column whose normalized name matches any alias."""
    alias_set = {_norm(a) for a in aliases}
    for col in table.columns:
        n = _norm(col.name)
        if n in alias_set:
            return col
        # token-level match: "tran_primary_id" matches alias "tran_primary_id"
        for a in alias_set:
            if a in n.split("_"):
                return col
    return None


def _build_vertex(
    spec: VertexSpec,
    match: VertexMatch,
    table: TableProfile,
) -> Vertex:
    # Resolve primary-id source column
    pid_col = _matching_column(
        table,
        [spec.primary_id, spec.name, *spec.name_aliases, *spec.composed_from],
    )

    # Build attribute list from canonical_attributes that match real columns
    attrs: list[Attribute] = []
    for ca in spec.canonical_attributes:
        col = _matching_column(table, [ca.name, *ca.aliases])
        if not col:
            continue
        attrs.append(
            Attribute(
                name=ca.name,
                dtype=ca.dtype,
                source_table=table.name,
                source_column=col.name,
                pii_class=col.pii_class,
                nullable=ca.optional or col.null_pct > 0,
            )
        )

    # Composed_from primary id: synthesize from concatenation; source columns listed
    if spec.composed_from and not pid_col:
        source_cols = match.source_columns
        source_col_name = source_cols[0] if source_cols else spec.primary_id
    else:
        source_col_name = pid_col.name if pid_col else spec.primary_id

    vsource = VertexSource(
        kind="column_group" if spec.composed_from else "table_column",
        table=table.name,
        columns=match.source_columns or [source_col_name],
    )

    rationale_bits = [
        f"Mapped to pattern vertex '{spec.name}' (conf={match.confidence:.2f})",
        f"primary_id sourced from column '{source_col_name}'",
    ]
    if spec.promotion_rule:
        rationale_bits.append(f"promotion_rule={spec.promotion_rule}")
    rationale = "; ".join(rationale_bits)

    return Vertex(
        name=spec.name,
        primary_id=spec.primary_id,
        primary_id_dtype=spec.dtype,
        attributes=attrs,
        source=vsource,
        rationale=rationale,
        pattern_origin=f"{spec.name}@pattern",
    )


def _build_edge(
    spec: EdgeSpec,
    matched_vertex_names: set[str],
    matched_table: str,
) -> Edge | None:
    if spec.from_ not in matched_vertex_names or spec.to not in matched_vertex_names:
        return None
    if spec.optional and (spec.from_ not in matched_vertex_names or spec.to not in matched_vertex_names):
        return None

    edge_attrs = [
        Attribute(
            name=ca.name,
            dtype=ca.dtype,
            source_table=matched_table,
            source_column=ca.name,
            nullable=ca.optional,
        )
        for ca in spec.attributes
    ]

    rationale = (
        f"Pattern edge '{spec.name}': both '{spec.from_}' and '{spec.to}' vertices "
        "matched against profiled data."
    )

    return Edge(
        name=spec.name,
        from_vertex=spec.from_,
        to_vertex=spec.to,
        direction=spec.direction,
        reverse_edge_name=spec.reverse_name,
        attributes=edge_attrs,
        source=EdgeSource(kind="derived", table=matched_table),
        rationale=rationale,
        pattern_origin=f"{spec.name}@pattern",
    )


def _attach_rule_provenance(vertex: Vertex, recs: list[RuleRecommendation]) -> None:
    """Append rule-engine rationale to the vertex's existing rationale."""
    rule_notes = []
    for r in recs:
        # Match by target = column or table name relevant to this vertex
        if vertex.source.table != r.metadata.get("source_table"):
            continue
        if r.target in vertex.source.columns or r.target == vertex.source.table:
            rule_notes.append(f"{r.rule_id}: {r.action}")
    if rule_notes:
        vertex.rationale += " | rules: " + ", ".join(sorted(set(rule_notes)))


def design_schema_with_ai(
    profiles: list[TableProfile],
    use_case: UseCase,
    user_prompt: str | None = None,
    csv_paths: list[Path] | None = None,
    patterns_dir: Path | None = None,
) -> tuple[Schema, dict[str, object]]:
    """AI-first design with deterministic fallback.

    Returns (schema, info) where info has {"mode": "ai" | "deterministic", ...}.
    """
    import logging

    log = logging.getLogger(__name__)

    try:
        from tg_schema_agent.llm.gemini import GeminiUnavailable, design_schema_ai, is_available
    except ImportError as exc:
        log.warning("LLM module unavailable, using deterministic: %s", exc)
        schema = design_schema(profiles, use_case, patterns_dir=patterns_dir)
        return schema, {"mode": "deterministic", "reason": "llm_module_missing"}

    if not is_available():
        log.info("No GEMINI_API_KEY set — using deterministic designer")
        schema = design_schema(profiles, use_case, patterns_dir=patterns_dir)
        return schema, {"mode": "deterministic", "reason": "no_api_key"}

    try:
        schema, debug = design_schema_ai(
            profiles=profiles,
            use_case=use_case,
            user_prompt=user_prompt,
            csv_paths=csv_paths,
            patterns_dir=patterns_dir,
        )
        return schema, {"mode": "ai", **debug}
    except GeminiUnavailable as exc:
        log.warning("Gemini unavailable, falling back: %s", exc)
        schema = design_schema(profiles, use_case, patterns_dir=patterns_dir)
        return schema, {"mode": "deterministic", "reason": f"gemini_unavailable: {exc}"}
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional for fallback
        log.exception("Gemini designer failed, falling back to deterministic")
        schema = design_schema(profiles, use_case, patterns_dir=patterns_dir)
        return schema, {
            "mode": "deterministic",
            "reason": f"gemini_error: {type(exc).__name__}: {exc}",
        }


def design_schema(
    profiles: list[TableProfile],
    use_case: UseCase,
    patterns_dir: Path | None = None,
) -> Schema:
    pattern_map = patterns.load_patterns(patterns_dir)
    pattern: Pattern = pattern_map[use_case]

    recs = rules.run_all(profiles)
    vertex_matches = patterns.match_pattern(profiles, use_case)
    matched_by_name = {m.vertex_name: m for m in vertex_matches}
    profiles_by_name = {p.name: p for p in profiles}

    # Build vertices in pattern order
    vertices: list[Vertex] = []
    for vspec in pattern.vertices:
        m = matched_by_name.get(vspec.name)
        if not m and vspec.optional:
            continue
        if not m:
            # Required vertex missing — skip but designer-level rationale will surface this
            continue
        table = profiles_by_name[m.source_table]
        v = _build_vertex(vspec, m, table)
        _attach_rule_provenance(v, recs)
        vertices.append(v)

    matched_vertex_names = {v.name for v in vertices}

    # Build edges
    edges: list[Edge] = []
    # Use the primary source table — for a wide-denormalized single-table input we
    # use that table for every edge.
    primary_table = profiles[0].name if profiles else ""
    for espec in pattern.edges:
        # Optional edges only included if both endpoints present
        e = _build_edge(espec, matched_vertex_names, primary_table)
        if e:
            edges.append(e)

    # Also add transfer edges discovered by rules (R3)
    for r in recs:
        if r.rule_id == "R3_transfer_edge":
            entity = str(r.metadata.get("entity", ""))
            cap = entity.capitalize()
            if cap in matched_vertex_names:
                edges.append(
                    Edge(
                        name=f"{cap}_TO_{cap}",
                        from_vertex=cap,
                        to_vertex=cap,
                        direction=EdgeDirection.DIRECTED,
                        attributes=[],
                        source=EdgeSource(
                            kind="derived",
                            table=str(r.metadata.get("source_table", "")),
                            from_column=str(r.metadata.get("from_column", "")),
                            to_column=str(r.metadata.get("to_column", "")),
                        ),
                        rationale=r.rationale,
                        pattern_origin=None,
                    )
                )

    schema = Schema(
        use_case=use_case,
        name=f"{use_case.value.lower()}_schema",
        version="0.1.0",
        pattern_version=pattern.version,
        vertices=vertices,
        edges=edges,
        target_questions=pattern.target_questions,
        inputs_hash=io_utils.inputs_hash(profiles),
    )
    return schema
