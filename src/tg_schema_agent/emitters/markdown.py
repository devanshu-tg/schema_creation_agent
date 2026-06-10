"""Markdown emitter: human-readable schema doc with Mermaid diagram."""

from __future__ import annotations

from tg_schema_agent.models import Schema, SchemaScore, ValidationResult


def _vertices_table(schema: Schema) -> str:
    lines = ["| Vertex | Primary ID | Attributes | Source |", "| --- | --- | --- | --- |"]
    for v in schema.vertices:
        attrs = ", ".join(f"`{a.name}`" for a in v.attributes) or "_(none)_"
        src = f"`{v.source.table}.{', '.join(v.source.columns)}`"
        lines.append(f"| **{v.name}** | `{v.primary_id}` | {attrs} | {src} |")
    return "\n".join(lines)


def _edges_table(schema: Schema) -> str:
    lines = [
        "| Edge | From | To | Direction | Reverse |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e in schema.edges:
        rev = f"`{e.reverse_edge_name}`" if e.reverse_edge_name else "_n/a_"
        lines.append(
            f"| **{e.name}** | {e.from_vertex} | {e.to_vertex} | {e.direction.value} | {rev} |"
        )
    return "\n".join(lines)


def _questions_table(schema: Schema, validation: ValidationResult) -> str:
    lines = ["| Question | Hops | Answerable? |", "| --- | --- | --- |"]
    answerable = set(validation.answerable_questions)
    for q in schema.target_questions:
        mark = "yes" if q.id in answerable else "no"
        lines.append(f"| `{q.id}` — {q.text} | {q.max_hops} | **{mark}** |")
    return "\n".join(lines)


def _score_table(score: SchemaScore) -> str:
    lines = ["| Dimension | Score |", "| --- | --- |"]
    for k, v in score.breakdown.items():
        lines.append(f"| {k.replace('_', ' ')} | {v}/100 |")
    lines.append(f"| **Total (weighted)** | **{score.total}/100** |")
    return "\n".join(lines)


def _mermaid_diagram(schema: Schema) -> str:
    lines = ["```mermaid", "graph LR"]
    for v in schema.vertices:
        safe = v.name.replace(" ", "_")
        lines.append(f"  {safe}([{v.name}])")
    for e in schema.edges:
        a = e.from_vertex.replace(" ", "_")
        b = e.to_vertex.replace(" ", "_")
        lines.append(f"  {a} -- {e.name} --> {b}")
    lines.append("```")
    return "\n".join(lines)


def emit_markdown(
    schema: Schema, validation: ValidationResult, score: SchemaScore
) -> str:
    parts: list[str] = []
    parts.append(f"# Schema: {schema.name}")
    parts.append("")
    parts.append(f"- **Use case:** `{schema.use_case.value}`")
    parts.append(f"- **Pattern version:** `{schema.pattern_version}`")
    parts.append(f"- **Inputs hash:** `{schema.inputs_hash}`")
    parts.append(f"- **Generated:** `{schema.generated_at.isoformat()}`")
    parts.append("")

    parts.append("## Score")
    parts.append("")
    parts.append(_score_table(score))
    if score.strengths:
        parts.append("\n**Strengths:**")
        for s in score.strengths:
            parts.append(f"- {s}")
    if score.gaps:
        parts.append("\n**Gaps:**")
        for g in score.gaps:
            parts.append(f"- {g}")
    if score.suggestions:
        parts.append("\n**Suggestions:**")
        for s in score.suggestions:
            parts.append(f"- {s}")

    parts.append("")
    parts.append("## Vertices")
    parts.append("")
    parts.append(_vertices_table(schema))

    parts.append("")
    parts.append("## Edges")
    parts.append("")
    parts.append(_edges_table(schema))

    parts.append("")
    parts.append("## Target Questions")
    parts.append("")
    parts.append(_questions_table(schema, validation))

    parts.append("")
    parts.append("## Diagram")
    parts.append("")
    parts.append(_mermaid_diagram(schema))

    parts.append("")
    parts.append("## Rationale")
    parts.append("")
    for v in schema.vertices:
        parts.append(f"- **{v.name}** — {v.rationale}")

    return "\n".join(parts) + "\n"
