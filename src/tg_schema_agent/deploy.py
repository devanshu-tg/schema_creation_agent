"""TigerGraph schema-only deploy via the tigergraph-mcp MCP server.

Schema-only phase: this module ONLY creates the graph + vertex types + edge
types in TigerGraph. Loading jobs / data load are deferred to a later phase.

The agent doesn't talk to TigerGraph directly — it spawns the `tigergraph-mcp`
binary as a stdio MCP subprocess and calls its tools. Connection config is
read from environment variables / .env (TG_HOST, TG_USERNAME, TG_PASSWORD,
TG_GRAPHNAME, TG_API_TOKEN, etc.).

The key MCP tool used here is `tigergraph__create_graph`, which accepts a
structured payload (vertex_types + edge_types) and creates the whole schema
atomically — no GSQL DDL strings, no SCHEMA_CHANGE_JOB ceremony. The MCP
server handles all that internally.
"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tg_schema_agent.enums import DataKind
from tg_schema_agent.models import Edge, Schema, TableProfile, Vertex

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = None  # type: ignore[assignment]

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _HAS_MCP = True
except ImportError:  # pragma: no cover
    _HAS_MCP = False


# Our DataKind → TigerGraph 4.x types (matches the GSQL emitter so dry-run
# and real-deploy stay consistent).
_TG_TYPE = {
    DataKind.INT: "INT",
    DataKind.FLOAT: "DOUBLE",
    DataKind.STRING: "STRING",
    DataKind.DATETIME: "DATETIME",
    DataKind.BOOL: "BOOL",
    DataKind.CATEGORICAL: "STRING",
    DataKind.ID_LIKE: "STRING",
}

# TigerGraph 4.x GSQL reserved words that commonly show up as attribute names
# in real datasets. `tigergraph__create_graph` does NOT quote attribute names
# when it generates GSQL, so any reserved-word collision produces a parser
# error. We rename them transparently here (e.g. `job` → `job_value`).
_GSQL_RESERVED = {
    "timestamp", "to", "from", "type", "value", "name", "description",
    "long", "short", "int", "string", "double", "float", "bool", "boolean",
    "datetime", "default", "null", "true", "false",
    "primary", "nullable", "vertex", "edge", "graph", "select", "where",
    "order", "limit", "group", "by", "having", "between",
    "category", "profile", "state", "key", "user", "role", "object",
    "data", "source", "target", "set", "list", "map", "tuple",
    "job", "status", "index", "table", "column", "view", "schema",
    "case", "when", "then", "else", "end", "and", "or", "not", "in",
    "exists", "any", "all", "as", "is", "like", "into", "values",
    "update", "delete", "insert", "create", "drop", "alter", "truncate",
}


def _safe_attr_name(name: str) -> str:
    """Rename a column that would collide with a GSQL reserved word.

    `tigergraph__create_graph` emits unquoted GSQL, so reserved attribute
    names break the parser. We append a `_value` suffix to keep the original
    column-name intent readable while avoiding the collision.
    """
    if name.lower() in _GSQL_RESERVED:
        return f"{name}_value"
    return name


def _required_mcp() -> None:
    if not _HAS_MCP:
        raise RuntimeError(
            "The `mcp` package is required for `tg-schema deploy`. Install with: "
            "uv sync --extra tigergraph"
        )


def _load_env(env_file: Path | None) -> dict[str, str]:
    """Merge OS env with optional .env file. OS env wins."""
    env: dict[str, str] = {}
    if env_file and env_file.exists():
        if dotenv_values is None:
            raise RuntimeError("python-dotenv not installed; install [tigergraph] extra")
        for k, v in dotenv_values(env_file).items():
            if v is not None:
                env[k] = v
    for k, v in os.environ.items():
        if k.startswith("TG_") or k in {
            "PATH",
            "PYTHONPATH",
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
        }:
            env[k] = v
    return env


# -------------------- Schema → MCP payload --------------------


def _vertex_payload(v: Vertex) -> dict[str, Any]:
    return {
        "name": v.name,
        "primary_id": _safe_attr_name(v.primary_id),
        "primary_id_type": _TG_TYPE[v.primary_id_dtype],
        "attributes": [
            {"name": _safe_attr_name(a.name), "type": _TG_TYPE[a.dtype]}
            for a in v.attributes
        ],
    }


def _edge_payload(e: Edge) -> dict[str, Any]:
    return {
        "name": e.name,
        "from_vertex": e.from_vertex,
        "to_vertex": e.to_vertex,
        "directed": True,
        "attributes": [
            {"name": _safe_attr_name(a.name), "type": _TG_TYPE[a.dtype]}
            for a in e.attributes
        ],
    }


def build_attribute_mapping(schema: Schema) -> dict[str, dict[str, str]]:
    """Return the rename map for every vertex's attributes.

    Shape: `{vertex_name: {original_attr_name: safe_attr_name}}`. Used by
    the loading-job builder so CSV column references (which use the
    ORIGINAL column names) map to the renamed attribute (`job` → `job_value`,
    `category` → `category_value`, etc.). Threading this explicitly
    prevents silent data-loss when the loading job tries to write into
    an attribute name that no longer exists.
    """
    mapping: dict[str, dict[str, str]] = {}
    for v in schema.vertices:
        per_vertex: dict[str, str] = {}
        per_vertex[v.primary_id] = _safe_attr_name(v.primary_id)
        for a in v.attributes:
            per_vertex[a.name] = _safe_attr_name(a.name)
        mapping[v.name] = per_vertex
    return mapping


def schema_to_mcp_payload(
    schema: Schema, graph_name: str | None = None
) -> dict[str, Any]:
    """Build the JSON payload `tigergraph__create_graph` expects.

    Dedupes vertices and edges by name as a defensive backstop — the agent's
    `propose_*` tools should already reject duplicates, but if anything slips
    through (e.g. multiple turns proposing the same canonical edge under
    different LLM-provided names), TG would reject the whole CREATE GRAPH
    with "Duplicate add edge". Filtering here keeps deploy resilient.
    """
    gname = graph_name or os.environ.get("TG_GRAPHNAME") or f"{schema.use_case.value.lower()}_graph"

    seen_v: set[str] = set()
    vertex_types: list[dict[str, Any]] = []
    for v in schema.vertices:
        if v.name in seen_v:
            continue
        seen_v.add(v.name)
        vertex_types.append(_vertex_payload(v))

    seen_e: set[str] = set()
    seen_endpoints: set[tuple[str, str]] = set()
    edge_types: list[dict[str, Any]] = []
    for e in schema.edges:
        if e.name in seen_e:
            continue
        # Also dedupe by (from, to) — the same logical edge under
        # different names is still a "Duplicate add edge" to TigerGraph.
        endpoints = (e.from_vertex, e.to_vertex)
        if endpoints in seen_endpoints:
            continue
        seen_e.add(e.name)
        seen_endpoints.add(endpoints)
        edge_types.append(_edge_payload(e))

    return {
        "graph_name": gname,
        "vertex_types": vertex_types,
        "edge_types": edge_types,
    }


@dataclass
class DeployPlan:
    """Plan of MCP calls — used by --dry-run and the real executor."""

    graph_name: str
    vertex_types: list[dict[str, Any]] = field(default_factory=list)
    edge_types: list[dict[str, Any]] = field(default_factory=list)


def build_plan(
    schema: Schema,
    profiles: list[TableProfile] | None = None,  # accepted for API compatibility
    csv_path: Path | None = None,  # accepted for API compatibility
    graph_name: str | None = None,
) -> DeployPlan:
    """Pure: build a DeployPlan from the schema. No side effects.

    The `profiles` and `csv_path` args are accepted for backwards compatibility
    with the loading-job path; they are unused in schema-only deploy.
    """
    payload = schema_to_mcp_payload(schema, graph_name=graph_name)
    return DeployPlan(
        graph_name=payload["graph_name"],
        vertex_types=payload["vertex_types"],
        edge_types=payload["edge_types"],
    )


def render_dry_run(plan: DeployPlan, env: dict[str, str]) -> str:
    """Human-readable representation of what `deploy` would do."""
    host = env.get("TG_HOST", "(unset)")
    user = env.get("TG_USERNAME", "(default)")
    graphname_env = env.get("TG_GRAPHNAME", "(unset)")

    lines: list[str] = []
    lines.append("=== DRY RUN: tg-schema deploy (schema-only) ===")
    lines.append(f"Target host:      {host}")
    lines.append(f"Auth:             {'API_TOKEN' if env.get('TG_API_TOKEN') else f'user={user}'}")
    lines.append(f"Graph name:       {plan.graph_name}  (env TG_GRAPHNAME={graphname_env})")
    lines.append("")
    lines.append("Step 1: spawn `tigergraph-mcp` as stdio MCP subprocess")
    lines.append("Step 2: drop existing graph (best-effort, ignored if absent)")
    lines.append("Step 3: tigergraph__create_graph with the structured payload:")
    lines.append(f"   - graph_name: {plan.graph_name}")
    lines.append(f"   - vertex_types: {len(plan.vertex_types)}")
    for vt in plan.vertex_types:
        attrs = ", ".join(a["name"] for a in vt.get("attributes", []))
        lines.append(f"       • {vt['name']}({vt['primary_id']}: {vt['primary_id_type']}, attrs=[{attrs}])")
    lines.append(f"   - edge_types: {len(plan.edge_types)}")
    for et in plan.edge_types:
        lines.append(
            f"       - {et['name']} ({et['from_vertex']} -> {et['to_vertex']})"
        )
    lines.append("Step 4: verify with tigergraph__get_graph_schema")
    lines.append("")
    lines.append("(Loading job / data load is intentionally NOT part of this deploy. "
                 "Run loading separately when ready.)")
    return "\n".join(lines)


# -------------------- live deploy --------------------


@asynccontextmanager
async def _open_session(env: dict[str, str], verbose: bool = False):
    _required_mcp()
    cmd = shutil.which("tigergraph-mcp")
    if not cmd:
        raise RuntimeError(
            "tigergraph-mcp executable not found on PATH. Install with "
            "`uv sync --extra tigergraph` and ensure the project venv is active."
        )
    args = ["-vv"] if verbose else []
    params = StdioServerParameters(command=cmd, args=args, env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


class TGGraphScopeViolation(RuntimeError):
    """Raised when code attempts to call an MCP tool against a graph that
    isn't in the configured allowlist. Caught and surfaced as a deploy
    error rather than silently letting the call go through."""


def _allowed_graphs() -> set[str]:
    """Read the allowed-graphs list from env.

    Defaults to just `TG_GRAPHNAME`. Set `TG_ALLOWED_GRAPHS` (comma-separated)
    to widen the scope intentionally — e.g. for migrations across graphs.
    """
    explicit = os.environ.get("TG_ALLOWED_GRAPHS", "").strip()
    if explicit:
        return {g.strip() for g in explicit.split(",") if g.strip()}
    base = os.environ.get("TG_GRAPHNAME", "").strip()
    return {base} if base else set()


# Tools that read or write global state outside any single graph. These
# bypass the scope guard but are limited to a small, audited list — they
# can't drop other graphs or run arbitrary GSQL.
_GLOBAL_TOOLS_OK = {
    "tigergraph__list_graphs",
    "tigergraph__list_connections",
    "tigergraph__show_connection",
    "tigergraph__get_global_schema",
    "tigergraph__discover_tools",
    "tigergraph__get_workflow",
    "tigergraph__get_tool_info",
    "tigergraph__validate_schema_names",
}


def _enforce_scope(tool: str, args: dict[str, Any]) -> None:
    """Reject any MCP call that targets a graph outside the allowlist.

    Three checks:
      1. If args has a `graph_name`, it must be in the allowed set.
      2. For raw `tigergraph__gsql` calls, scan the command for
         `USE GRAPH X`, `CREATE GRAPH X`, `DROP GRAPH X` references — every
         named graph must be in the allowlist.
      3. Global-namespace tools (list_graphs, etc.) are always allowed.
    """
    allowed = _allowed_graphs()
    if not allowed:
        # No allowlist configured → permissive (preserves old behavior).
        return

    if tool in _GLOBAL_TOOLS_OK:
        return

    gname = args.get("graph_name")
    if gname and gname not in allowed:
        raise TGGraphScopeViolation(
            f"Refusing to call '{tool}' on graph '{gname}' — outside the "
            f"allowlist {sorted(allowed)}. Set TG_ALLOWED_GRAPHS to widen "
            "scope if this is intentional."
        )

    if tool == "tigergraph__gsql":
        import re as _re

        cmd = str(args.get("command", "") or args.get("gsql", ""))
        # Look for any GRAPH-targeting GSQL command — both USE GRAPH and
        # any CREATE/DROP/INTERPRET QUERY ... FOR GRAPH X form.
        named = set(
            _re.findall(
                r"(?:USE\s+GRAPH|CREATE\s+GRAPH|DROP\s+GRAPH|FOR\s+GRAPH)\s+(\w+)",
                cmd,
                _re.IGNORECASE,
            )
        )
        for g in named:
            if g not in allowed:
                raise TGGraphScopeViolation(
                    f"Refusing GSQL referencing graph '{g}' — outside the "
                    f"allowlist {sorted(allowed)}. Command starts: "
                    f"{cmd[:120]!r}"
                )


async def _call(session: Any, tool: str, args: dict[str, Any]) -> Any:
    """Call a tool and parse the structured JSON response if present.

    Enforces the graph-scope security layer: every call is checked against
    the `TG_GRAPHNAME` allowlist before being forwarded to the MCP server.
    """
    _enforce_scope(tool, args)
    result = await session.call_tool(tool, arguments=args)
    payload: Any = None
    for content in result.content:
        text = getattr(content, "text", None)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = text
    return payload


def _parse_mcp_payload(payload: Any) -> Any:
    """Extract the structured JSON inside an MCP markdown response.

    tigergraph-mcp wraps results as ```json {...} ``` inside a text block.
    Returns the parsed dict if found, otherwise the original payload.
    """
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        return payload
    fence_start = payload.find("```json")
    if fence_start != -1:
        body_start = payload.find("\n", fence_start) + 1
        fence_end = payload.find("```", body_start)
        if fence_end != -1:
            try:
                return json.loads(payload[body_start:fence_end].strip())
            except json.JSONDecodeError:
                pass
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return payload


_GSQL_FAILURE_MARKERS = (
    '"success": false',
    "**failed",
    "(code: gsql-",
    "input validation error",
    "is a required property",
    "semantic check fails",
    "internal server error",
    # GSQL embeds these in result strings even when the MCP envelope says success
    "query installation failed",
    "the query .* doesn't exist",
    "cannot be found!",
    "installation failed",
    # CREATE QUERY accepts semantically-invalid queries as drafts. We don't
    # want those reported as successful — INSTALL QUERY would later reject
    # them anyway. These markers appear in the CREATE response.
    "type check error",
    "saved as draft",
    "draft query with type/semantic error",
)


def _is_success(payload: Any) -> bool:
    parsed = _parse_mcp_payload(payload)
    if isinstance(parsed, dict):
        # The MCP envelope's success flag tells us whether the tool call
        # itself succeeded — but for tigergraph__gsql, a syntactically valid
        # command that the server REJECTS (semantic check, install failure)
        # still gets success=true with the error tucked into data.result.
        # So we ALSO scan the result body for known failure markers.
        envelope_ok = bool(parsed.get("success", True))
        if "error" in parsed and parsed["error"]:
            return False
        # Drill into data.result (gsql tool) or data (everything else)
        data = parsed.get("data")
        body_str = ""
        if isinstance(data, dict):
            body_str = str(data.get("result") or data)
        elif isinstance(data, str):
            body_str = data
        low = body_str.lower()
        if any(m in low for m in _GSQL_FAILURE_MARKERS):
            return False
        return envelope_ok
    if isinstance(payload, str):
        low = payload.lower()
        if any(m in low for m in _GSQL_FAILURE_MARKERS):
            return False
    return True


def _summarize_error(payload: Any) -> str:
    parsed = _parse_mcp_payload(payload)
    if isinstance(parsed, dict):
        for key in ("error", "summary", "message", "data"):
            v = parsed.get(key)
            if v:
                return str(v)[:300]
    return str(payload)[:300]


def _extract_query_names(listing: str) -> list[str]:
    """Pull installed query names out of a `show_graph_details(detail_type='query')` listing.

    The listing looks like:
        # installed v2
        CREATE QUERY fraud_by_city_state() FOR GRAPH mcp_demo { ... }

        # installed v2
        CREATE QUERY top_fraud_merchants() FOR GRAPH mcp_demo { ... }
    """
    import re

    names: list[str] = []
    for m in re.finditer(r"CREATE\s+QUERY\s+(\w+)\s*\(", listing or "", re.IGNORECASE):
        names.append(m.group(1))
    return names


async def _cascade_drop_graph(
    session: Any,
    graph_name: str,
    base_args: dict[str, Any],
    report: dict[str, Any],
    emit,
) -> None:
    """Best-effort cleanup before recreating the graph.

    Order: drop installed queries → drop the graph. If the graph doesn't
    exist, every step is a no-op and emit-level "ok" is recorded. We never
    raise — schema creation is the next step's responsibility.
    """
    emit("drop", graph_name, "running",
         f"Looking for {graph_name} and its dependencies")

    # 1. Find installed queries depending on this graph
    queries_payload = await _call(
        session,
        "tigergraph__show_graph_details",
        {**base_args, "graph_name": graph_name, "detail_type": "query"},
    )
    report["steps"].append({"call": "show_graph_details(query)", "result": queries_payload})
    listing = ""
    parsed = _parse_mcp_payload(queries_payload)
    if isinstance(parsed, dict):
        data = parsed.get("data")
        if isinstance(data, dict):
            listing = data.get("listing", "")
    query_names = _extract_query_names(listing)

    if query_names:
        emit("drop", graph_name, "running",
             f"Found {len(query_names)} installed query(ies) to drop: {', '.join(query_names)}")
        for qname in query_names:
            drop_q = await _call(
                session,
                "tigergraph__drop_query",
                {**base_args, "graph_name": graph_name, "query_name": qname},
            )
            report["steps"].append({"call": f"drop_query({qname})", "result": drop_q})
            ok = _is_success(drop_q)
            emit("drop_query", qname, "ok" if ok else "failed",
                 f"Dropped query {qname}" if ok else _summarize_error(drop_q))

    # 1b. Drop any leftover LOADING JOBS that depend on this graph.
    # TG refuses to drop a graph when a job still depends on it.
    try:
        jobs_payload = await _call(
            session,
            "tigergraph__get_loading_jobs",
            {**base_args, "graph_name": graph_name},
        )
        jp = _parse_mcp_payload(jobs_payload) or {}
        jobs = []
        d = jp.get("data") if isinstance(jp, dict) else None
        if isinstance(d, dict):
            jobs = d.get("jobs") or d.get("loading_jobs") or []
        elif isinstance(d, list):
            jobs = d
        job_names = []
        for j in jobs:
            if isinstance(j, dict):
                name = j.get("name") or j.get("job_name")
                if name:
                    job_names.append(name)
            elif isinstance(j, str):
                job_names.append(j)
        if job_names:
            emit("drop", graph_name, "running",
                 f"Found {len(job_names)} loading job(s) to drop: {', '.join(job_names)}")
            for jname in job_names:
                dj = await _call(
                    session,
                    "tigergraph__drop_loading_job",
                    {**base_args, "graph_name": graph_name, "job_name": jname},
                )
                ok = _is_success(dj)
                emit("drop_query", jname, "ok" if ok else "failed",
                     f"Dropped loading job {jname}" if ok else _summarize_error(dj))
    except Exception:  # noqa: BLE001
        # Listing/dropping loading jobs is best-effort — proceed to drop_graph
        # which will surface the dependency error if anything's left.
        pass

    # 2. Now drop the graph itself
    try:
        drop_payload = await _call(
            session,
            "tigergraph__drop_graph",
            {**base_args, "graph_name": graph_name},
        )
        report["steps"].append({"call": "drop_graph", "result": drop_payload})
        if _is_success(drop_payload):
            emit("drop", graph_name, "ok", f"Dropped graph {graph_name}")
            return
        # Drop failed — see if it's because the graph doesn't exist (fine)
        # or because something else still depends on it.
        err = _summarize_error(drop_payload).lower()
        if "could not be dropped" in err and "depend" in err:
            emit("drop", graph_name, "failed",
                 f"Graph still has dependencies the agent couldn't clean: "
                 f"{_summarize_error(drop_payload)[:200]}")
            report["errors"].append({"phase": "drop", "result": drop_payload})
        else:
            # Probably "graph doesn't exist" — that's the desired state.
            emit("drop", graph_name, "ok", f"No existing {graph_name} to drop")
    except Exception as exc:  # noqa: BLE001
        emit("drop", graph_name, "ok", f"Nothing to drop ({exc})")


def _build_loading_files_config(
    schema: Schema,
    file_alias: str,
    separator: str,
    csv_header_columns: list[str],
) -> list[dict[str, Any]]:
    """Build the `files: [...]` payload for `tigergraph__create_loading_job`.

    Because we don't pass a `file_path` (data is streamed inline at run
    time), TG can't read the file header at job-creation time. We must
    therefore pass column INDICES (ints), not header names (strings),
    in every attribute_mapping. `csv_header_columns` is the parsed first
    line of the actual CSV — we use it to resolve source_column → index.
    """
    col_idx: dict[str, int] = {name: i for i, name in enumerate(csv_header_columns)}

    def _resolve(col: str) -> int | str:
        # Fall back to header name if the column isn't found (TG might
        # accept it if the loader does read the inline data's header).
        return col_idx.get(col, col)

    node_mappings: list[dict[str, Any]] = []
    for v in schema.vertices:
        pk_src = v.source.columns[0] if v.source.columns else v.primary_id
        attrs: dict[str, int | str] = {_safe_attr_name(v.primary_id): _resolve(pk_src)}
        for a in v.attributes:
            if a.source_column:
                attrs[_safe_attr_name(a.name)] = _resolve(a.source_column)
        node_mappings.append({
            "vertex_type": v.name,
            "attribute_mappings": attrs,
        })

    edge_mappings: list[dict[str, Any]] = []
    by_name = {v.name: v for v in schema.vertices}
    for e in schema.edges:
        from_v = by_name.get(e.from_vertex)
        to_v = by_name.get(e.to_vertex)
        if not from_v or not to_v:
            continue
        if from_v.source.table != to_v.source.table:
            continue
        from_src = from_v.source.columns[0] if from_v.source.columns else from_v.primary_id
        to_src = to_v.source.columns[0] if to_v.source.columns else to_v.primary_id
        em: dict[str, Any] = {
            "edge_type": e.name,
            "source_column": _resolve(from_src),
            "target_column": _resolve(to_src),
        }
        if e.attributes:
            em["attribute_mappings"] = {
                _safe_attr_name(a.name): _resolve(a.source_column)
                for a in e.attributes
                if a.source_column
            }
        edge_mappings.append(em)

    return [
        {
            "file_alias": file_alias,
            "separator": separator,
            "header": "true",
            "eol": "\\n",
            "node_mappings": node_mappings,
            "edge_mappings": edge_mappings,
        }
    ]


async def _run_loading_job_phase(
    session: Any,
    schema: Schema,
    profiles: list[TableProfile],
    csv_path: Path,
    graph_name: str,
    base_args: dict[str, Any],
    report: dict[str, Any],
    emit,
) -> None:
    """Build + create + run + poll a loading job for the given schema.

    Uses the structured `tigergraph__create_loading_job` MCP tool (NOT the
    raw GSQL one) and `tigergraph__run_loading_job_with_data` (inline CSV
    streaming — required for TG Cloud / Savanna).

    Critical: attribute names in the loading job's VALUES clauses use the
    RENAMED attribute (via _safe_attr_name) so reserved-word columns
    like `job` → `job_value` keep landing in the right field.
    """
    import asyncio

    from tg_schema_agent.emitters import gsql as gsql_emitter

    # 1. Build the loading-job GSQL with explicit attribute rename map.
    attr_mapping = build_attribute_mapping(schema)
    loading_job_gsql = gsql_emitter.emit_loading_job(
        schema, profiles, graph_name=graph_name, attribute_mapping=attr_mapping
    )
    # Extract the job name (matches what emit_loading_job picks)
    import re as _re

    m = _re.search(r"CREATE\s+LOADING\s+JOB\s+(\w+)", loading_job_gsql, _re.IGNORECASE)
    job_name = m.group(1) if m else f"load_{graph_name}"

    # 2. Create the loading job via the STRUCTURED MCP tool.
    #    TG 4.2 refuses every literal path we can put in DEFINE FILENAME
    #    (./*: sensitive dir; /tmp/*, /home/*: missing files), so writing
    #    the GSQL ourselves is a dead end on Cloud. The structured tool's
    #    `files: [...]` payload skips the path validation because no
    #    file_path is supplied — data comes at run-time via inline data.
    emit("loading_job", job_name, "running",
         f"Creating loading job {job_name}")
    primary_profile = next(
        (p for p in profiles if p.name == csv_path.stem), profiles[0]
    )
    file_alias = f"f_{primary_profile.name.replace('-', '_').replace(' ', '_')[:40]}"
    delim = getattr(primary_profile, "detected_delimiter", ",") or ","

    # Read CSV header so we can map source_columns → column indices (TG
    # can't read headers at job-create time when no file_path is given).
    try:
        with csv_path.open("r", encoding="utf-8") as fh:
            first_line = fh.readline().rstrip("\r\n")
        csv_header_columns = [c.strip() for c in first_line.split(delim)]
    except Exception as exc:  # noqa: BLE001
        emit("loading_job", job_name, "failed",
             f"Could not read CSV header: {exc}")
        report["errors"].append({"phase": "read_csv_header", "error": str(exc)})
        return

    files_config = _build_loading_files_config(
        schema=schema,
        file_alias=file_alias,
        separator=delim,
        csv_header_columns=csv_header_columns,
    )
    create_payload = await _call(
        session,
        "tigergraph__create_loading_job",
        {
            **base_args,
            "graph_name": graph_name,
            "job_name": job_name,
            "files": files_config,
        },
    )
    report["steps"].append({"call": "create_loading_job(structured)", "result": create_payload})
    if not _is_success(create_payload):
        emit("loading_job", job_name, "failed", _summarize_error(create_payload))
        report["errors"].append({"phase": "create_loading_job", "result": create_payload})
        return
    emit("loading_job", job_name, "ok", f"Loading job {job_name} created")

    # 3. Stream the CSV into TigerGraph
    try:
        csv_bytes = csv_path.read_bytes()
        csv_text = csv_bytes.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        emit("run_load", csv_path.name, "failed", f"Could not read CSV: {exc}")
        report["errors"].append({"phase": "read_csv", "error": str(exc)})
        return

    # file_tag must match the file_alias from the structured create payload.
    file_tag = file_alias
    sep = delim

    row_count = csv_text.count("\n")
    emit("run_load", csv_path.name, "running",
         f"Streaming {csv_path.name} (~{row_count:,} rows, sep={sep!r}) to TigerGraph")
    run_payload = await _call(
        session,
        "tigergraph__run_loading_job_with_data",
        {
            **base_args,
            "graph_name": graph_name,
            "job_name": job_name,
            "file_tag": file_tag,
            "data": csv_text,
            "separator": sep,
            # TG timeout is in MILLISECONDS. Default 16000 — too short for
            # anything past a few thousand rows. 600_000 = 10 minutes.
            "timeout": 600_000,
            "size_limit": 0,
        },
    )
    report["steps"].append({"call": "run_loading_job_with_data", "result": run_payload})
    if not _is_success(run_payload):
        emit("run_load", csv_path.name, "failed", _summarize_error(run_payload))
        report["errors"].append({"phase": "run_load", "result": run_payload})
        return

    # 4. Poll status until terminal — the inline-data MCP variant usually
    #    runs synchronously, but the API supports async jobs too.
    parsed = _parse_mcp_payload(run_payload)
    job_id = ""
    if isinstance(parsed, dict):
        data_blob = parsed.get("data") or {}
        if isinstance(data_blob, dict):
            job_id = str(data_blob.get("job_id") or data_blob.get("jobId") or "")

    if job_id:
        for _ in range(30):  # ~60s total
            status_payload = await _call(
                session,
                "tigergraph__get_loading_job_status",
                {**base_args, "graph_name": graph_name, "job_id": job_id},
            )
            sp = _parse_mcp_payload(status_payload)
            if isinstance(sp, dict):
                status = ((sp.get("data") or {}).get("status") or "").lower()
                if status in {"finished", "succeeded", "done", "complete", "completed"}:
                    break
                if status in {"failed", "error"}:
                    emit("run_load", csv_path.name, "failed",
                         _summarize_error(status_payload))
                    report["errors"].append({
                        "phase": "loading_job_status", "result": status_payload,
                    })
                    return
            await asyncio.sleep(2.0)

    emit("run_load", csv_path.name, "ok",
         f"{csv_path.name} loaded into {graph_name}")

    # 5. Get per-vertex counts. TG occasionally returns 0 immediately after a
    # successful load (background index refresh hasn't settled) — poll up to
    # ~10s before giving up so the agent's reply shows real numbers.
    async def _count(vname: str) -> Any:
        count_payload = await _call(
            session,
            "tigergraph__get_vertex_count",
            {**base_args, "graph_name": graph_name, "vertex_type": vname},
        )
        parsed = _parse_mcp_payload(count_payload)
        if isinstance(parsed, dict):
            d = parsed.get("data") or {}
            if isinstance(d, dict):
                return d.get("count")
        return None

    for v in schema.vertices:
        count: Any = None
        for attempt in range(5):
            count = await _count(v.name)
            if isinstance(count, int) and count > 0:
                break
            await asyncio.sleep(2.0)
        report["vertex_counts"][v.name] = count if isinstance(count, int) else None
        emit(
            "counts",
            v.name,
            "ok" if isinstance(count, int) else "failed",
            f"{v.name} = {count}" if isinstance(count, int) else f"{v.name}: count unavailable",
        )


async def deploy(
    schema: Schema,
    profiles: list[TableProfile] | None = None,
    csv_path: Path | None = None,
    graph_name: str | None = None,
    env_file: Path | None = None,
    profile_name: str | None = None,
    verbose: bool = False,
    progress=None,
    remote: bool = True,
    load_data: bool = False,
) -> dict[str, Any]:
    """Create the schema in TigerGraph via MCP, optionally loading data.

    Args:
        profiles: Required when `load_data=True` for building the loading
            job. Ignored otherwise.
        csv_path: Required when `load_data=True`. Path to the CSV that
            will be streamed into TigerGraph via
            `tigergraph__run_loading_job_with_data` (TG Cloud requires
            inline data).
        load_data: If True, after the schema is created the function
            additionally builds a loading job, runs it against `csv_path`,
            polls until completion, and reports vertex counts. Default
            False so existing schema-only callers don't change behavior.
        progress: Optional callback. Called with either a plain str OR a
            structured dict {phase, name, status, summary}.
        remote: Reserved for future on-prem support — currently always
            uses `run_loading_job_with_data` (inline CSV). Kept for API
            compatibility.

    Returns a dict summarizing the outcome:
        {
          "graph_name": str,
          "vertex_counts": {} or per-vertex counts if load_data,
          "steps": [...],
          "errors": [...],
        }
    """
    plan = build_plan(schema, profiles, csv_path, graph_name=graph_name)
    env = _load_env(env_file)
    if profile_name:
        env["TG_PROFILE"] = profile_name

    report: dict[str, Any] = {
        "graph_name": plan.graph_name,
        "steps": [],
        "vertex_counts": {},  # always empty in schema-only mode
        "errors": [],
    }

    def _emit(phase: str, name: str = "", status: str = "running", summary: str = "") -> None:
        if progress is None:
            return
        try:
            progress({"phase": phase, "name": name, "status": status, "summary": summary})
        except TypeError:
            progress(summary or f"{phase}: {name} [{status}]")

    base_args: dict[str, Any] = {}
    if profile_name:
        base_args["profile"] = profile_name

    _emit("spawn", "tigergraph-mcp", "running", "Spawning tigergraph-mcp subprocess")
    async with _open_session(env, verbose=verbose) as session:
        _emit("spawn", "tigergraph-mcp", "ok", "MCP session ready")

        # --- 1. Validate names before doing anything destructive ---
        _emit("validate", plan.graph_name, "running",
              f"Validating {len(plan.vertex_types)} vertex types + {len(plan.edge_types)} edge types")
        validate_payload = await _call(
            session,
            "tigergraph__validate_schema_names",
            {
                **base_args,
                "graph_name": plan.graph_name,
                "vertex_types": plan.vertex_types,
                "edge_types": plan.edge_types,
            },
        )
        report["steps"].append({"call": "validate_schema_names", "result": validate_payload})
        if not _is_success(validate_payload):
            _emit("validate", plan.graph_name, "failed", _summarize_error(validate_payload))
            report["errors"].append({"phase": "validate", "result": validate_payload})
            return report
        _emit("validate", plan.graph_name, "ok", "Names look good")

        # --- 2. Cascade-drop so re-deploys don't collide ---
        # The graph may have installed queries / loading jobs depending on it.
        # We drop those first, then the graph itself. Errors at any step are
        # logged but not fatal — if the graph doesn't exist, all of this is a no-op.
        await _cascade_drop_graph(
            session, plan.graph_name, base_args, report, _emit
        )

        # --- 3. The one atomic call: create the entire schema ---
        for vt in plan.vertex_types:
            _emit("vertex", vt["name"], "running", f"Queuing vertex {vt['name']}")
        for et in plan.edge_types:
            _emit("edge", et["name"], "running",
                  f"Queuing edge {et['name']} ({et['from_vertex']} -> {et['to_vertex']})")
        _emit("graph", plan.graph_name, "running",
              f"Creating {plan.graph_name} via tigergraph__create_graph "
              f"({len(plan.vertex_types)} vertices, {len(plan.edge_types)} edges)")

        create_payload = await _call(
            session,
            "tigergraph__create_graph",
            {
                **base_args,
                "graph_name": plan.graph_name,
                "vertex_types": plan.vertex_types,
                "edge_types": plan.edge_types,
            },
        )
        report["steps"].append({"call": "create_graph", "result": create_payload})

        create_ok = _is_success(create_payload)
        for vt in plan.vertex_types:
            _emit("vertex", vt["name"], "ok" if create_ok else "failed",
                  f"{vt['name']} created" if create_ok else _summarize_error(create_payload))
        for et in plan.edge_types:
            _emit("edge", et["name"], "ok" if create_ok else "failed",
                  f"{et['name']} created" if create_ok else _summarize_error(create_payload))
        _emit("graph", plan.graph_name, "ok" if create_ok else "failed",
              f"Graph {plan.graph_name} is live" if create_ok
              else _summarize_error(create_payload))

        if not create_ok:
            report["errors"].append({"phase": "create_graph", "result": create_payload})
            return report

        # --- 4. Verify by reading the schema back ---
        _emit("verify", plan.graph_name, "running", "Reading schema back from TigerGraph")
        verify_payload = await _call(
            session,
            "tigergraph__get_graph_schema",
            {**base_args, "graph_name": plan.graph_name},
        )
        report["steps"].append({"call": "get_graph_schema", "result": verify_payload})
        verified = _parse_mcp_payload(verify_payload)
        sch = None
        if isinstance(verified, dict):
            data = verified.get("data") or {}
            # MCP get_graph_schema nests the schema dict: data.schema.{VertexTypes,EdgeTypes}
            sch = data.get("schema") if isinstance(data, dict) else None
        if isinstance(sch, dict):
            vcount = len(sch.get("VertexTypes") or sch.get("vertex_types") or [])
            ecount = len(sch.get("EdgeTypes") or sch.get("edge_types") or [])
            _emit("verify", plan.graph_name, "ok",
                  f"TigerGraph reports {vcount} vertex types + {ecount} edge types")
            report["verified_vertex_count"] = vcount
            report["verified_edge_count"] = ecount
        else:
            _emit("verify", plan.graph_name, "ok", "Schema created (verify response unparseable)")

        # --- 5. Loading job (optional) ---------------------------------
        if load_data:
            if csv_path is None or not profiles:
                _emit("loading_job", "", "failed",
                      "load_data=True but csv_path or profiles missing")
                report["errors"].append({
                    "phase": "loading_job",
                    "result": "csv_path/profiles missing for load_data=True",
                })
                return report

            await _run_loading_job_phase(
                session=session,
                schema=schema,
                profiles=profiles,
                csv_path=csv_path,
                graph_name=plan.graph_name,
                base_args=base_args,
                report=report,
                emit=_emit,
            )

    return report
