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
    async with _open_session(env) as session:
        # Define the query first via gsql (USE GRAPH X; CREATE QUERY ...)
        create_payload = await _call(
            session,
            "tigergraph__gsql",
            {"command": f"USE GRAPH {graph_name}\n{target['gsql']}"},
        )
        if not _is_success(create_payload):
            return _err(
                f"Defining query failed: {_summarize_error(create_payload)}"
            )
        # Install it
        install_payload = await _call(
            session,
            "tigergraph__install_query",
            {"graph_name": graph_name, "query_name": query_name},
        )
        if not _is_success(install_payload):
            return _err(
                f"Install failed: {_summarize_error(install_payload)}"
            )
    return _ok(
        f"Installed query '{query_name}' in graph '{graph_name}'.",
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
                "parameters": params or {},
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
