"""TigerGraph 4.x GSQL emitter.

Produces:
- CREATE VERTEX statements
- CREATE DIRECTED EDGE statements (with WITH REVERSE_EDGE clause)
- CREATE GRAPH statement
- CREATE LOADING JOB with the detected CSV delimiter
"""

from __future__ import annotations

from tg_schema_agent.enums import DataKind, EdgeDirection
from tg_schema_agent.models import (
    Attribute,
    Edge,
    Schema,
    TableProfile,
    Vertex,
)

# Map our DataKind → TigerGraph 4.x types
_GSQL_TYPE = {
    DataKind.INT: "INT",
    DataKind.FLOAT: "DOUBLE",
    DataKind.STRING: "STRING",
    DataKind.DATETIME: "DATETIME",
    DataKind.BOOL: "BOOL",
    DataKind.CATEGORICAL: "STRING",
    DataKind.ID_LIKE: "STRING",
}

_RESERVED = {
    # Common TigerGraph 4.x GSQL reserved words that show up as attribute names.
    # We err on the side of quoting too much rather than too little; quoting is
    # always safe but missing one breaks the entire CREATE statement.
    "timestamp", "to", "from", "type", "value", "name", "description",
    "long", "short", "int", "string", "double", "float", "bool", "boolean",
    "datetime", "default", "null", "true", "false",
    "primary", "nullable", "vertex", "edge", "graph", "select", "where",
    "order", "limit", "group", "by", "having", "between",
    "category", "profile", "state", "key", "user", "role", "object",
    "data", "source", "target", "set", "list", "map", "tuple",
}


def _q(name: str) -> str:
    """Quote every attribute name unconditionally.

    The TigerGraph reserved-word list is large, undocumented, and shifts
    between versions. Quoting all attribute names is safe (always parses
    correctly) and bulletproofs against future collisions.
    """
    return f'"{name}"'


def _attr_clause(attr: Attribute) -> str:
    return f"{_q(attr.name)} {_GSQL_TYPE[attr.dtype]}"


def emit_create_vertex(v: Vertex) -> str:
    parts = [f"PRIMARY_ID {v.primary_id} {_GSQL_TYPE[v.primary_id_dtype]}"]
    for a in v.attributes:
        parts.append(_attr_clause(a))
    body = ", ".join(parts)
    return f'CREATE VERTEX {v.name} ({body}) WITH primary_id_as_attribute="true"'


def emit_create_edge(e: Edge) -> str:
    attr_parts = [_attr_clause(a) for a in e.attributes]
    attr_clause = ""
    if attr_parts:
        attr_clause = ", " + ", ".join(attr_parts)
    base = f"CREATE DIRECTED EDGE {e.name} (FROM {e.from_vertex}, TO {e.to_vertex}{attr_clause})"
    if e.direction == EdgeDirection.DIRECTED_WITH_REVERSE and e.reverse_edge_name:
        base += f' WITH REVERSE_EDGE="{e.reverse_edge_name}"'
    return base


def emit_create_graph(schema: Schema, graph_name: str | None = None) -> str:
    gname = graph_name or f"{schema.use_case.value.lower()}_graph"
    vlist = ", ".join(v.name for v in schema.vertices)
    elist = ", ".join(e.name for e in schema.edges)
    return f"CREATE GRAPH {gname} ({vlist}, {elist})"


def _loading_filename(profile: TableProfile) -> str:
    return f"{profile.name}.csv"


def emit_loading_job(
    schema: Schema,
    profiles: list[TableProfile],
    graph_name: str | None = None,
    attribute_mapping: dict[str, dict[str, str]] | None = None,
) -> str:
    """Generate a single LOADING JOB that loads vertices + edges from the source CSVs.

    For wide-denormalized inputs (single CSV), every vertex and edge sources from that
    one file via different column mappings.

    Args:
        attribute_mapping: Optional rename map produced by
            `build_attribute_mapping(schema)`. The CSV column references use
            the ORIGINAL column names (from `Attribute.source_column`) which
            is correct because TigerGraph's LOAD VALUES is positional — the
            values bind in attribute-declaration order. We accept the map for
            forward compatibility (named-binding syntax in future TG versions)
            and to emit a leading comment that documents the mapping for
            debug.
    """
    gname = graph_name or f"{schema.use_case.value.lower()}_graph"
    profiles_by_name = {p.name: p for p in profiles}

    file_aliases: dict[str, str] = {}
    file_blocks: list[str] = []
    for i, p in enumerate(profiles):
        alias = f"f_{p.name.replace('-', '_').replace(' ', '_')[:40]}"
        file_aliases[p.name] = alias
        # IMPORTANT: TG 4.2+ has two distinct path checks at job-CREATE time:
        #   1. Sensitive directory: "./<file>" resolves into TG's app dir → rejected.
        #   2. File must exist: any absolute path is validated → rejected if missing.
        # For `run_loading_job_with_data`, the data comes inline at run-time —
        # the path here is a placeholder. The pattern that passes BOTH checks
        # is a bare filename with no path component.
        file_blocks.append(f'  DEFINE FILENAME {alias} = "{_loading_filename(p)}";')

    load_blocks: list[str] = []

    for v in schema.vertices:
        table_name = v.source.table
        if table_name not in profiles_by_name:
            continue
        alias = file_aliases[table_name]
        # Build TO VERTEX with column mappings
        # PRIMARY_ID: first source column (for composed_from, use first; will hash if needed)
        pid_src = v.source.columns[0] if v.source.columns else v.primary_id
        mappings = [f"$\"{pid_src}\""]
        for a in v.attributes:
            mappings.append(f"$\"{a.source_column}\"")
        delim = profiles_by_name[table_name].detected_delimiter
        load_blocks.append(
            f"  LOAD {alias} TO VERTEX {v.name} VALUES ({', '.join(mappings)})\n"
            f'    USING SEPARATOR="{delim}", HEADER="true", EOL="\\n";'
        )

    for e in schema.edges:
        # Find the vertices' source tables/columns
        from_v = next((v for v in schema.vertices if v.name == e.from_vertex), None)
        to_v = next((v for v in schema.vertices if v.name == e.to_vertex), None)
        if not (from_v and to_v):
            continue
        # For a single wide-denormalized input, both vertices source from the same table
        if from_v.source.table != to_v.source.table:
            continue  # skip cross-table edges in this Phase 1 loader
        table_name = from_v.source.table
        alias = file_aliases.get(table_name)
        if not alias:
            continue
        from_pid_src = from_v.source.columns[0] if from_v.source.columns else from_v.primary_id
        to_pid_src = to_v.source.columns[0] if to_v.source.columns else to_v.primary_id
        attr_mappings = []
        for a in e.attributes:
            if a.source_column:
                attr_mappings.append(f"$\"{a.source_column}\"")
        suffix = (", " + ", ".join(attr_mappings)) if attr_mappings else ""
        delim = profiles_by_name[table_name].detected_delimiter
        load_blocks.append(
            f"  LOAD {alias} TO EDGE {e.name} VALUES "
            f"($\"{from_pid_src}\", $\"{to_pid_src}\"{suffix})\n"
            f'    USING SEPARATOR="{delim}", HEADER="true", EOL="\\n";'
        )

    body = "\n".join(file_blocks) + "\n\n" + "\n".join(load_blocks)
    header = ""
    if attribute_mapping:
        # Surface the rename map in a comment so debugging post-load
        # data isn't a guessing game when reserved-word columns
        # (`job` → `job_value`, etc.) get involved.
        rename_lines = []
        for vname, m in attribute_mapping.items():
            renames = [f"{k}->{v}" for k, v in m.items() if k != v]
            if renames:
                rename_lines.append(f"//   {vname}: {', '.join(renames)}")
        if rename_lines:
            header = "// Attribute renames (CSV column → schema attribute):\n" + "\n".join(rename_lines) + "\n"
    return f"{header}CREATE LOADING JOB load_{gname} FOR GRAPH {gname} {{\n{body}\n}}"


def emit(
    schema: Schema,
    profiles: list[TableProfile] | None = None,
    graph_name: str | None = None,
) -> str:
    """Emit the full DDL: vertices, edges, graph, loading job (if profiles provided)."""
    lines: list[str] = []
    lines.append(f"// Generated by tg-schema-agent v0.1.0")
    lines.append(f"// Use case: {schema.use_case.value}")
    lines.append(f"// Schema: {schema.name} (pattern {schema.pattern_version})")
    lines.append(f"// Inputs hash: {schema.inputs_hash}")
    lines.append("")
    for v in schema.vertices:
        lines.append(emit_create_vertex(v))
    lines.append("")
    for e in schema.edges:
        lines.append(emit_create_edge(e))
    lines.append("")
    lines.append(emit_create_graph(schema, graph_name=graph_name))
    if profiles:
        lines.append("")
        lines.append(emit_loading_job(schema, profiles, graph_name=graph_name))
    return "\n".join(lines) + "\n"
