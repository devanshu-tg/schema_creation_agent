"""Live TigerGraph MCP tools the chat agent can invoke.

The schema-design tools (`propose_vertex`, `validate_schema`, etc.) work on
the in-memory `working_schema` object — they never touch TigerGraph.

These tools do. They wrap `tigergraph-mcp` calls so the agent can deploy
the schema, load data, generate/install starter queries, query the live
graph, and reset state — all from the chat. Every call routes through the
`_enforce_scope` security guard in `deploy.py`, so the agent literally
cannot touch a graph other than the one in `TG_GRAPHNAME` / `TG_ALLOWED_GRAPHS`.

Destructive tools (`wipe_graph`, `drop_graph_data`, `deploy_schema` which
re-creates) should ONLY be called after the agent has used `ask_user` to
confirm with the user. The system prompt enforces this norm.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tg_schema_agent.llm.tools import ToolContext, _err, _ok


# ---------- helpers ----------


def _resolve_graph() -> str:
    """The single graph the agent operates on. Sourced from env."""
    g = os.environ.get("TG_GRAPHNAME") or "mcp_demo"
    return g


def _pick_csv(ctx: ToolContext) -> Path | None:
    """Return the workspace's primary CSV (first uploaded file)."""
    if not ctx.csv_paths:
        return None
    return ctx.csv_paths[0]


async def _open_and_call(tool: str, args: dict[str, Any]) -> Any:
    """Spawn an MCP session, run one tool call (through the security guard),
    return the parsed payload."""
    from tg_schema_agent.deploy import _call, _load_env, _open_session, _parse_mcp_payload

    env = _load_env(None)
    async with _open_session(env) as session:
        payload = await _call(session, tool, args)
        return _parse_mcp_payload(payload)


# ---------- deploy schema ----------


async def deploy_schema_live(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    """Push the current working schema to TigerGraph. Destructive — cascades
    drop of any existing graph with the same name. Confirm via `ask_user`
    first before calling on a non-empty graph."""
    if not ctx.working_schema.vertices:
        return _err(
            "Working schema is empty — propose vertices first before deploying."
        )
    from tg_schema_agent.deploy import deploy

    graph_name = _resolve_graph()
    progress: list[dict[str, Any]] = []

    def on_progress(event: dict[str, Any] | str) -> None:
        if isinstance(event, dict):
            progress.append(event)

    try:
        report = await deploy(
            schema=ctx.working_schema,
            profiles=ctx.profiles,
            csv_path=_pick_csv(ctx),
            graph_name=graph_name,
            progress=on_progress,
            load_data=False,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"Deploy failed: {type(exc).__name__}: {exc}")

    errors = report.get("errors") or []
    if errors:
        return _err(
            f"Deploy completed with {len(errors)} error(s). "
            f"First error: {str(errors[0])[:300]}"
        )

    return _ok(
        f"Deployed {len(ctx.working_schema.vertices)} vertices and "
        f"{len(ctx.working_schema.edges)} edges to graph '{graph_name}'.",
        {
            "graph_name": graph_name,
            "verified_vertex_count": report.get("verified_vertex_count"),
            "verified_edge_count": report.get("verified_edge_count"),
            "progress_steps": len(progress),
        },
    )


# ---------- load data ----------


async def load_data_live(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    """Stream the workspace's uploaded CSV into the live graph via the
    loading job. Requires the schema to already be deployed. Use after
    `deploy_schema_live`."""
    csv_path = _pick_csv(ctx)
    if csv_path is None:
        return _err("No CSV in workspace. Upload data before loading.")
    if not ctx.working_schema.vertices:
        return _err("No schema designed yet. Propose vertices first.")
    from tg_schema_agent.deploy import deploy

    graph_name = _resolve_graph()
    progress: list[dict[str, Any]] = []

    def on_progress(event: dict[str, Any] | str) -> None:
        if isinstance(event, dict):
            progress.append(event)

    try:
        report = await deploy(
            schema=ctx.working_schema,
            profiles=ctx.profiles,
            csv_path=csv_path,
            graph_name=graph_name,
            progress=on_progress,
            load_data=True,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"Load failed: {type(exc).__name__}: {exc}")

    counts = report.get("vertex_counts") or {}
    total = sum(c for c in counts.values() if isinstance(c, int))
    return _ok(
        f"Loaded {csv_path.name} into '{graph_name}'. Total rows: {total:,}. "
        f"Per-vertex: {counts}",
        {"graph_name": graph_name, "vertex_counts": counts, "csv": csv_path.name},
    )


# ---------- graph state ----------


async def get_graph_state_live(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    """Inspect the current state of the live graph: vertex types, edge types,
    installed queries, per-type vertex counts. Read-only. Safe to call
    anytime to confirm what's currently in TigerGraph."""
    from tg_schema_agent.deploy import _call, _load_env, _open_session, _parse_mcp_payload

    graph_name = _resolve_graph()
    env = _load_env(None)
    state: dict[str, Any] = {
        "graph_name": graph_name,
        "graph_exists": False,
        "vertex_types": [],
        "edge_types": [],
        "vertex_counts": {},
        "installed_queries": [],
    }
    async with _open_session(env) as session:
        # List graphs (global tool — passes security guard)
        lg = _parse_mcp_payload(
            await _call(session, "tigergraph__list_graphs", {})
        )
        graphs = ((lg or {}).get("data") or {}).get("graphs", [])
        if graph_name not in graphs:
            return _ok(
                f"Graph '{graph_name}' does not exist yet. "
                f"Existing graphs: {graphs}",
                state,
            )
        state["graph_exists"] = True

        # Schema
        sch_payload = _parse_mcp_payload(
            await _call(
                session, "tigergraph__get_graph_schema", {"graph_name": graph_name}
            )
        )
        sch = ((sch_payload or {}).get("data") or {}).get("schema") or {}
        vts = [v.get("Name") for v in (sch.get("VertexTypes") or []) if v.get("Name")]
        ets = [e.get("Name") for e in (sch.get("EdgeTypes") or []) if e.get("Name")]
        state["vertex_types"] = vts
        state["edge_types"] = ets

        # Per-vertex counts (cap at 12 to keep latency tolerable)
        for vname in vts[:12]:
            cp = _parse_mcp_payload(
                await _call(
                    session,
                    "tigergraph__get_vertex_count",
                    {"graph_name": graph_name, "vertex_type": vname},
                )
            )
            cnt = ((cp or {}).get("data") or {}).get("count")
            state["vertex_counts"][vname] = cnt if isinstance(cnt, int) else None

        # Installed queries
        q_payload = _parse_mcp_payload(
            await _call(
                session,
                "tigergraph__show_graph_details",
                {"graph_name": graph_name, "detail_type": "query"},
            )
        )
        listing = ((q_payload or {}).get("data") or {}).get("listing", "")
        import re

        state["installed_queries"] = re.findall(
            r"CREATE\s+QUERY\s+(\w+)\s*\(", listing or "", re.IGNORECASE
        )

    summary = (
        f"Graph '{graph_name}': {len(state['vertex_types'])} vertex types, "
        f"{len(state['edge_types'])} edge types, "
        f"{len(state['installed_queries'])} installed query(ies). "
        f"Total rows: {sum(c for c in state['vertex_counts'].values() if isinstance(c, int)):,}."
    )
    return _ok(summary, state)


# ---------- starter queries ----------


async def generate_starter_queries_live(
    ctx: ToolContext, **_kwargs: Any
) -> dict[str, Any]:
    """Use Gemini to write starter GSQL queries tailored to the current
    schema + business context, then dry-run validate each against the live
    graph. Returns the list of queries with `validated` flags."""
    if not ctx.working_schema.vertices:
        return _err("No schema. Deploy or design a schema first.")
    from tg_schema_agent.deploy import _load_env, _open_session
    from tg_schema_agent.llm.queries import generate_starter_queries

    graph_name = _resolve_graph()
    env = _load_env(None)
    async with _open_session(env) as session:
        qs = await generate_starter_queries(
            session=session,
            schema=ctx.working_schema,
            graph_name=graph_name,
            business_context=ctx.working_schema.business_context,
        )
    # Persist for later install_query calls
    try:
        from tg_schema_agent import io_utils

        io_utils.dump_json(
            {
                "graph_name": graph_name,
                "queries": [q.model_dump(mode="json") for q in qs.queries],
            },
            ctx.workspace_dir / "starter_queries.json",
        )
    except Exception:  # noqa: BLE001
        pass

    validated = sum(1 for q in qs.queries if q.validated)
    return _ok(
        f"Generated {len(qs.queries)} starter queries against '{graph_name}' "
        f"({validated} validated via INTERPRET dry-run).",
        {
            "graph_name": graph_name,
            "queries": [
                {
                    "name": q.name,
                    "description": q.description,
                    "business_question": q.business_question,
                    "validated": q.validated,
                    "validation_error": q.validation_error,
                }
                for q in qs.queries
            ],
        },
    )


async def write_and_install_query_live(
    ctx: ToolContext,
    query_name: str = "",
    gsql: str = "",
    description: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Write a custom GSQL query and install it on the live graph in one step.

    Use this when the user describes a SPECIFIC analysis they want
    (e.g. "fraud volume by city + state", "accounts with >5 declined
    transactions in 24h"), and that query is NOT in the starter list.
    DO NOT generate starter queries in response — write the specific
    GSQL the user asked for.

    Args:
      query_name: snake_case identifier (becomes the REST endpoint).
      gsql: FULL GSQL — must start with `CREATE QUERY <name>(...) FOR
            GRAPH <graph> {` and end with `}`. Reference only the
            vertex/edge types and attributes that exist in the schema.
            Watch edge directions — use the from→to edge as declared.
      description: One-line description (stored alongside).

    After this returns ok, `run_query_live(query_name=...)` can execute it.
    """
    if not query_name or not query_name.strip():
        return _err("write_and_install_query_live requires `query_name`.")
    if not gsql or not gsql.strip():
        return _err(
            "write_and_install_query_live requires `gsql` — the full "
            "`CREATE QUERY <name>(...) FOR GRAPH <graph> { ... }` text."
        )
    from tg_schema_agent.deploy import (
        _call, _is_success, _load_env, _open_session, _summarize_error,
    )

    graph_name = _resolve_graph()
    env = _load_env(None)
    query_text = gsql.strip()
    async with _open_session(env) as session:
        # CREATE QUERY via gsql (with drop-and-retry on conflict)
        create = await _call(
            session,
            "tigergraph__gsql",
            {"command": f"USE GRAPH {graph_name}\n{query_text}"},
        )
        if not _is_success(create):
            err = _summarize_error(create)
            if "already exists" in err.lower() or "duplicate" in err.lower():
                drop = await _call(
                    session,
                    "tigergraph__gsql",
                    {"command": f"USE GRAPH {graph_name}\nDROP QUERY {query_name}"},
                )
                if not _is_success(drop):
                    return _err(
                        f"CREATE failed and drop-existing also failed: "
                        f"create={err}; drop={_summarize_error(drop)}"
                    )
                create = await _call(
                    session,
                    "tigergraph__gsql",
                    {"command": f"USE GRAPH {graph_name}\n{query_text}"},
                )
                if not _is_success(create):
                    return _err(
                        f"CREATE re-try after drop failed: {_summarize_error(create)}"
                    )
            else:
                return _err(f"CREATE QUERY failed: {err}")

        # INSTALL QUERY (compiles + exposes /restpp/query/<graph>/<name>)
        install = await _call(
            session,
            "tigergraph__gsql",
            {"command": f"USE GRAPH {graph_name}\nINSTALL QUERY {query_name}"},
        )
        if not _is_success(install):
            return _err(
                f"INSTALL QUERY failed (query defined but not compiled): "
                f"{_summarize_error(install)}"
            )

    # Persist into starter_queries.json so run_query_live + UI listings
    # see this query alongside the starter set.
    try:
        sq_path = ctx.workspace_dir / "starter_queries.json"
        if sq_path.exists():
            sq = json.loads(sq_path.read_text(encoding="utf-8"))
        else:
            sq = {"graph_name": graph_name, "queries": []}
        sq["queries"] = [q for q in sq.get("queries", []) if q.get("name") != query_name]
        sq["queries"].append({
            "name": query_name,
            "description": description or f"Custom query: {query_name}",
            "business_question": description or "",
            "gsql": query_text,
            "expected_output_description": "",
            "validated": True,
            "validation_error": None,
        })
        sq_path.write_text(json.dumps(sq, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    return _ok(
        f"Wrote and installed custom query '{query_name}' in '{graph_name}'. "
        f"Use run_query_live to execute it.",
        {"graph_name": graph_name, "query_name": query_name},
    )


async def install_query_live(
    ctx: ToolContext,
    query_name: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Install a previously-generated starter query into the live graph.
    The query must already exist in `starter_queries.json` from a prior
    `generate_starter_queries_live` call."""
    if not query_name:
        return _err("install_query requires `query_name`.")
    sq_path = ctx.workspace_dir / "starter_queries.json"
    if not sq_path.exists():
        return _err(
            "No starter queries generated yet. Call generate_starter_queries_live first."
        )
    try:
        sq = json.loads(sq_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _err(f"Could not read starter_queries.json: {exc}")
    target = next((q for q in sq.get("queries", []) if q.get("name") == query_name), None)
    if target is None:
        avail = [q.get("name") for q in sq.get("queries", [])]
        return _err(f"Query '{query_name}' not found. Available: {avail}")
    from tg_schema_agent.deploy import _call, _is_success, _load_env, _open_session, _summarize_error

    graph_name = _resolve_graph()
    env = _load_env(None)
    query_text = target["gsql"]
    async with _open_session(env) as session:
        # IMPORTANT: We use tigergraph__gsql for BOTH steps because
        # tigergraph__install_query (despite the name) does not reliably
        # define the query in the catalog — it sometimes silently no-ops
        # and the next INSTALL QUERY then fails with "query cannot be found".
        # The gsql tool with the full CREATE QUERY text works reliably.
        create_payload = await _call(
            session,
            "tigergraph__gsql",
            {"command": f"USE GRAPH {graph_name}\n{query_text}"},
        )
        if not _is_success(create_payload):
            err = _summarize_error(create_payload)
            if "already exists" in err.lower() or "duplicate" in err.lower():
                drop_payload = await _call(
                    session,
                    "tigergraph__gsql",
                    {"command": f"USE GRAPH {graph_name}\nDROP QUERY {query_name}"},
                )
                if not _is_success(drop_payload):
                    return _err(
                        f"CREATE QUERY failed and drop-existing also failed: "
                        f"create={err}; drop={_summarize_error(drop_payload)}"
                    )
                create_payload = await _call(
                    session,
                    "tigergraph__gsql",
                    {"command": f"USE GRAPH {graph_name}\n{query_text}"},
                )
                if not _is_success(create_payload):
                    return _err(
                        f"CREATE QUERY re-try after drop failed: {_summarize_error(create_payload)}"
                    )
            else:
                return _err(f"CREATE QUERY failed: {err}")

        # Now compile the query. INSTALL QUERY on Savanna can take 30-120s.
        # tigergraph-mcp's gsql tool waits synchronously for completion.
        install_payload = await _call(
            session,
            "tigergraph__gsql",
            {"command": f"USE GRAPH {graph_name}\nINSTALL QUERY {query_name}"},
        )
        if not _is_success(install_payload):
            return _err(
                f"INSTALL QUERY failed (query is defined but not compiled): "
                f"{_summarize_error(install_payload)}"
            )
    return _ok(
        f"Installed query '{query_name}' in graph '{graph_name}' (CREATE + INSTALL).",
        {"graph_name": graph_name, "query_name": query_name},
    )


async def run_query_live(
    ctx: ToolContext,
    query_name: str = "",
    params: dict[str, Any] | None = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run an installed query against the live graph and return its
    result. Read-only against TigerGraph."""
    if not query_name:
        return _err("run_query requires `query_name`.")
    from tg_schema_agent.deploy import _call, _is_success, _load_env, _open_session, _parse_mcp_payload, _summarize_error

    graph_name = _resolve_graph()
    env = _load_env(None)
    async with _open_session(env) as session:
        payload = await _call(
            session,
            "tigergraph__run_installed_query",
            {
                "graph_name": graph_name,
                "query_name": query_name,
                "params": params or {},
            },
        )
    parsed = _parse_mcp_payload(payload)
    if not _is_success(payload):
        return _err(
            f"Query '{query_name}' failed: {_summarize_error(payload)}"
        )
    data = (parsed or {}).get("data") if isinstance(parsed, dict) else None
    # Trim huge result blobs for the chat
    blob = json.dumps(data, default=str)
    if len(blob) > 4000:
        blob = blob[:4000] + " …(truncated)"
    return _ok(
        f"Ran '{query_name}' on '{graph_name}'.",
        {"graph_name": graph_name, "query_name": query_name, "results": data, "results_preview": blob},
    )


# ---------- raw GSQL + introspection (Claude-Code-style power tools) ----------


async def run_gsql_live(
    ctx: ToolContext,
    command: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Execute arbitrary GSQL against the live TigerGraph instance.

    This is the agent's raw shell — like `bash` for Claude Code. Use it
    for anything the curated tools don't cover: SHOW SCHEMA, SHOW QUERY,
    DROP VERTEX, GRANT, ALTER, CREATE TYPE, USE GRAPH X, etc. The
    command is automatically scoped to the configured graph if it
    doesn't start with USE GRAPH.

    For SAFE data inspection use run_interpreted_query_live (anonymous,
    no install). For pre-installed queries, use run_query_live.
    Returns the raw GSQL output (truncated to 4 KB for the chat)."""
    if not command or not command.strip():
        return _err("run_gsql_live requires a non-empty `command` string.")
    from tg_schema_agent.deploy import _call, _is_success, _load_env, _open_session, _parse_mcp_payload, _summarize_error

    graph_name = _resolve_graph()
    cmd = command.strip()
    # If the command doesn't pin a graph already, prepend USE GRAPH to
    # keep operations scoped to mcp_demo per the security guard.
    if not cmd.upper().startswith("USE GRAPH"):
        cmd = f"USE GRAPH {graph_name}\n{cmd}"

    env = _load_env(None)
    async with _open_session(env) as session:
        payload = await _call(session, "tigergraph__gsql", {"command": cmd})
    parsed = _parse_mcp_payload(payload)
    if not _is_success(payload):
        return _err(f"GSQL failed: {_summarize_error(payload)}")
    result = ""
    if isinstance(parsed, dict):
        data = parsed.get("data") or {}
        if isinstance(data, dict):
            result = str(data.get("result") or "")
        elif isinstance(data, str):
            result = data
    blob = result[:4000] + (" …(truncated)" if len(result) > 4000 else "")
    return _ok(
        f"GSQL executed ({len(result)} chars).",
        {"graph_name": graph_name, "command": command[:200], "result": result, "preview": blob},
    )


async def run_interpreted_query_live(
    ctx: ToolContext,
    gsql_body: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run an ANONYMOUS interpreted GSQL query — no install, no params.

    Use this for ad-hoc data exploration: "show me 5 fraud transactions",
    "count vertices by city", etc. The body should be the query CONTENTS
    only (statements between { and }), e.g.:

        Start = {Transaction.*};
        Fraud = SELECT t FROM Start:t WHERE t.is_fraud == 1 LIMIT 5;
        PRINT Fraud;

    Faster than write_and_install_query_live for one-off questions
    (no compilation step). For repeat use, prefer the installed path."""
    if not gsql_body or not gsql_body.strip():
        return _err("run_interpreted_query_live requires `gsql_body` (the query content).")
    from tg_schema_agent.deploy import _call, _is_success, _load_env, _open_session, _parse_mcp_payload, _summarize_error

    graph_name = _resolve_graph()
    # Anonymous interpreted query — no name, no params
    wrapped = f"INTERPRET QUERY () FOR GRAPH {graph_name} {{\n{gsql_body.strip()}\n}}"
    env = _load_env(None)
    async with _open_session(env) as session:
        payload = await _call(
            session,
            "tigergraph__gsql",
            {"command": f"USE GRAPH {graph_name}\n{wrapped}"},
        )
    parsed = _parse_mcp_payload(payload)
    if not _is_success(payload):
        return _err(f"Interpreted query failed: {_summarize_error(payload)}")
    data = (parsed or {}).get("data") if isinstance(parsed, dict) else None
    result_str = ""
    if isinstance(data, dict):
        result_str = str(data.get("result") or "")
    blob = result_str[:4000] + (" …(truncated)" if len(result_str) > 4000 else "")
    return _ok(
        f"Interpreted query executed.",
        {"graph_name": graph_name, "result": result_str, "preview": blob},
    )


async def get_schema_details_live(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    """Full schema dump: every vertex type with its attributes + dtypes,
    every edge type with from/to and attributes. Use BEFORE writing custom
    queries so you reference real attributes and respect edge directions."""
    from tg_schema_agent.deploy import _call, _load_env, _open_session, _parse_mcp_payload

    graph_name = _resolve_graph()
    env = _load_env(None)
    async with _open_session(env) as session:
        payload = _parse_mcp_payload(
            await _call(session, "tigergraph__get_graph_schema", {"graph_name": graph_name})
        )
    sch = ((payload or {}).get("data") or {}).get("schema") or {}
    vts = sch.get("VertexTypes") or []
    ets = sch.get("EdgeTypes") or []
    # Compact per-type summary
    vertices = []
    for v in vts:
        attrs = [{"name": a.get("AttributeName"), "type": (a.get("AttributeType") or {}).get("Name")}
                 for a in (v.get("Attributes") or [])]
        vertices.append({
            "name": v.get("Name"),
            "primary_id": (v.get("PrimaryId") or {}).get("AttributeName"),
            "attributes": attrs,
        })
    edges = []
    for e in ets:
        attrs = [{"name": a.get("AttributeName"), "type": (a.get("AttributeType") or {}).get("Name")}
                 for a in (e.get("Attributes") or [])]
        edges.append({
            "name": e.get("Name"),
            "from": e.get("FromVertexTypeName"),
            "to": e.get("ToVertexTypeName"),
            "is_directed": e.get("IsDirected"),
            "reverse_edge": e.get("Config", {}).get("REVERSE_EDGE") if isinstance(e.get("Config"), dict) else None,
            "attributes": attrs,
        })
    return _ok(
        f"Schema for '{graph_name}': {len(vertices)} vertex types, {len(edges)} edge types.",
        {"graph_name": graph_name, "vertices": vertices, "edges": edges},
    )


async def list_installed_queries_live(ctx: ToolContext, **_kwargs: Any) -> dict[str, Any]:
    """List installed query names + their parameter signatures. Use to
    discover what's already installed before asking to run one."""
    from tg_schema_agent.deploy import _call, _load_env, _open_session, _parse_mcp_payload

    graph_name = _resolve_graph()
    env = _load_env(None)
    async with _open_session(env) as session:
        payload = _parse_mcp_payload(
            await _call(session, "tigergraph__show_graph_details",
                        {"graph_name": graph_name, "detail_type": "query"})
        )
    listing = ((payload or {}).get("data") or {}).get("listing", "") or ""
    import re
    names = re.findall(r"CREATE\s+QUERY\s+(\w+)\s*\(([^)]*)\)", listing, re.IGNORECASE)
    queries = [{"name": n, "params_signature": p.strip()} for n, p in names]
    return _ok(
        f"Found {len(queries)} installed query(ies) in '{graph_name}'.",
        {"graph_name": graph_name, "queries": queries},
    )


async def drop_query_live(
    ctx: ToolContext,
    query_name: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Uninstall a query from the live graph. Use when the user asks to
    remove a query, or as a cleanup step before re-installing under the
    same name with different GSQL."""
    if not query_name or not query_name.strip():
        return _err("drop_query_live requires `query_name`.")
    from tg_schema_agent.deploy import _call, _is_success, _load_env, _open_session, _summarize_error

    graph_name = _resolve_graph()
    env = _load_env(None)
    async with _open_session(env) as session:
        payload = await _call(
            session,
            "tigergraph__gsql",
            {"command": f"USE GRAPH {graph_name}\nDROP QUERY {query_name}"},
        )
    if not _is_success(payload):
        return _err(f"DROP QUERY failed: {_summarize_error(payload)}")
    return _ok(f"Dropped query '{query_name}' from '{graph_name}'.")


async def get_vertex_sample_live(
    ctx: ToolContext,
    vertex_type: str = "",
    limit: int = 5,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Get a sample of N actual vertex rows by type. Use to inspect real
    data when the user asks 'what does the data look like' / 'show me
    some accounts' / etc. Default limit is 5 (max 50)."""
    if not vertex_type or not vertex_type.strip():
        return _err("get_vertex_sample_live requires `vertex_type`.")
    n = max(1, min(int(limit or 5), 50))
    from tg_schema_agent.deploy import _call, _is_success, _load_env, _open_session, _parse_mcp_payload, _summarize_error

    graph_name = _resolve_graph()
    env = _load_env(None)
    async with _open_session(env) as session:
        payload = await _call(
            session,
            "tigergraph__gsql",
            {"command": f"USE GRAPH {graph_name}\nSELECT * FROM {vertex_type} LIMIT {n}"},
        )
    if not _is_success(payload):
        return _err(f"Sample fetch failed: {_summarize_error(payload)}")
    parsed = _parse_mcp_payload(payload) or {}
    result = ((parsed.get("data") or {}).get("result") or "")
    blob = result[:4000] + (" …(truncated)" if len(result) > 4000 else "")
    return _ok(
        f"Sampled up to {n} '{vertex_type}' vertex(ies) from '{graph_name}'.",
        {"graph_name": graph_name, "vertex_type": vertex_type, "limit": n, "result": result, "preview": blob},
    )


# ---------- destructive ----------


async def drop_graph_data_live(ctx: ToolContext, confirm: bool = False, **_kwargs: Any) -> dict[str, Any]:
    """Delete all vertex + edge rows from the live graph, leaving the
    schema intact. Destructive — only call after `ask_user` confirms."""
    if not confirm:
        return _err(
            "drop_graph_data is destructive. Re-call with confirm=true AFTER "
            "you've used ask_user to get the user's explicit consent."
        )
    from tg_schema_agent.deploy import _call, _is_success, _load_env, _open_session, _summarize_error

    graph_name = _resolve_graph()
    env = _load_env(None)
    async with _open_session(env) as session:
        payload = await _call(
            session,
            "tigergraph__clear_graph_data",
            {"graph_name": graph_name, "confirm": True},
        )
    if not _is_success(payload):
        return _err(f"clear_graph_data failed: {_summarize_error(payload)}")
    return _ok(f"Cleared all data from '{graph_name}'. Schema intact.")


async def wipe_graph_live(ctx: ToolContext, confirm: bool = False, **_kwargs: Any) -> dict[str, Any]:
    """Cascade-drop installed queries + the entire graph. Full reset.
    Destructive — only call after `ask_user` confirms."""
    if not confirm:
        return _err(
            "wipe_graph removes the entire graph and all queries. Re-call "
            "with confirm=true AFTER ask_user gets explicit consent."
        )
    from tg_schema_agent.deploy import _cascade_drop_graph, _load_env, _open_session

    graph_name = _resolve_graph()
    env = _load_env(None)
    report: dict[str, Any] = {"steps": [], "errors": []}

    def _emit(*_args: Any, **_kw: Any) -> None:
        pass

    async with _open_session(env) as session:
        await _cascade_drop_graph(session, graph_name, {}, report, _emit)
    if report.get("errors"):
        return _err(
            f"Wipe completed with errors: {report['errors'][0]}"
        )
    return _ok(f"Wiped graph '{graph_name}' (queries + graph dropped).")
