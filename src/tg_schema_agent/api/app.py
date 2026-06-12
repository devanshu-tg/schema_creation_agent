"""FastAPI application.

Endpoints are 1:1 with the CLI commands plus workspace management:

POST   /api/workspaces                              create workspace
GET    /api/workspaces/{id}                         workspace info
DELETE /api/workspaces/{id}                         delete workspace
POST   /api/workspaces/{id}/files                   upload CSV file(s)
GET    /api/workspaces/{id}/files                   list files
GET    /api/workspaces/{id}/files/{name}            download an artifact

POST   /api/workspaces/{id}/profile                 profile CSVs in workspace
POST   /api/workspaces/{id}/design                  design schema {use_case}
POST   /api/workspaces/{id}/validate                validate current schema
POST   /api/workspaces/{id}/score                   score current schema
POST   /api/workspaces/{id}/emit                    emit gsql|markdown
POST   /api/workspaces/{id}/run                     run full pipeline
GET    /api/workspaces/{id}/run/stream              SSE-stream the pipeline

POST   /api/workspaces/{id}/deploy                  deploy to TigerGraph via MCP

GET    /api/use-cases                               list available patterns
GET    /api/health                                  health check
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    Body,
    FastAPI,
    File,
    HTTPException,
    Path as PathParam,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from tg_schema_agent import __version__, io_utils, patterns, scorer, validator
from tg_schema_agent.api import schemas as api_schemas
from tg_schema_agent.api.workspace import (
    append_chat_message,
    assert_workspace,
    clear_chat_history,
    create_workspace,
    delete_workspace,
    list_csv_files,
    load_chat_history,
    save_upload,
    workspace_dir,
    workspace_state,
)
from tg_schema_agent.designer import design_schema, design_schema_with_ai
from tg_schema_agent.emitters import gsql as gsql_emitter
from tg_schema_agent.emitters import markdown as md_emitter
from tg_schema_agent.enums import UseCase
from tg_schema_agent.profiler import profile_directory

app = FastAPI(
    title="TigerGraph Schema Creation Agent",
    version=__version__,
    description=(
        "Reads tabular data + a use-case intent and produces a TigerGraph graph schema. "
        "Endpoints map 1:1 to the `tg-schema` CLI commands."
    ),
)

# CORS — wide-open for dev; tighten in prod via env var TG_SCHEMA_CORS_ORIGINS.
_cors_env = os.environ.get("TG_SCHEMA_CORS_ORIGINS", "*")
_origins = [o.strip() for o in _cors_env.split(",")] if _cors_env != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


# ---------- helpers ----------


def _load_schema_or_404(workspace_id: str):
    d = assert_workspace(workspace_id)
    schema_path = d / "schema.json"
    if not schema_path.exists():
        raise HTTPException(
            status_code=400,
            detail="No schema in this workspace. Call /design first.",
        )
    return io_utils.load_schema(schema_path)


# ---------- meta ----------


@app.get("/api/health", response_model=api_schemas.HealthResponse, tags=["meta"])
def health() -> api_schemas.HealthResponse:
    return api_schemas.HealthResponse(
        status="ok",
        version=__version__,
        use_cases=[u.value for u in UseCase],
    )


@app.get("/api/use-cases", response_model=list[api_schemas.UseCaseInfo], tags=["meta"])
def list_use_cases() -> list[api_schemas.UseCaseInfo]:
    out = []
    for uc, pat in patterns.load_patterns().items():
        out.append(
            api_schemas.UseCaseInfo(
                id=uc.value,
                name=pat.name,
                version=pat.version,
                description=pat.description,
                vertex_count=len(pat.vertices),
                edge_count=len(pat.edges),
                target_question_count=len(pat.target_questions),
            )
        )
    return out


# ---------- workspace ----------


@app.post("/api/workspaces", response_model=api_schemas.WorkspaceCreated, tags=["workspace"])
def workspace_create() -> api_schemas.WorkspaceCreated:
    wid = create_workspace()
    return api_schemas.WorkspaceCreated(workspace_id=wid)


@app.get(
    "/api/workspaces/{workspace_id}",
    response_model=api_schemas.WorkspaceInfo,
    tags=["workspace"],
)
def workspace_get(workspace_id: str) -> api_schemas.WorkspaceInfo:
    assert_workspace(workspace_id)
    state = workspace_state(workspace_id)
    return api_schemas.WorkspaceInfo(
        workspace_id=workspace_id,
        files=list_csv_files(workspace_id),
        profiles_ready=state["profiles_ready"],
        schema_ready=state["schema_ready"],
        deployed=state["deployed"],
    )


@app.delete("/api/workspaces/{workspace_id}", tags=["workspace"])
def workspace_delete(workspace_id: str) -> dict[str, str]:
    delete_workspace(workspace_id)
    return {"status": "deleted", "workspace_id": workspace_id}


@app.post("/api/workspaces/{workspace_id}/files", tags=["workspace"])
async def upload_files(
    workspace_id: str,
    files: Annotated[list[UploadFile], File(description="CSV files")],
) -> dict:
    assert_workspace(workspace_id)
    saved = []
    for f in files:
        content = await f.read()
        path = save_upload(workspace_id, f.filename or "upload.csv", content)
        saved.append({"name": path.name, "bytes": len(content)})
    return {"workspace_id": workspace_id, "files": saved}


@app.get("/api/workspaces/{workspace_id}/files", tags=["workspace"])
def list_files(workspace_id: str) -> dict:
    assert_workspace(workspace_id)
    return {"workspace_id": workspace_id, "files": list_csv_files(workspace_id)}


@app.get("/api/workspaces/{workspace_id}/files/{name}", tags=["workspace"])
def download_file(workspace_id: str, name: str) -> FileResponse:
    d = assert_workspace(workspace_id)
    target = d / Path(name).name
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {name}")
    return FileResponse(target, filename=target.name)


# ---------- pipeline ----------


@app.post(
    "/api/workspaces/{workspace_id}/profile",
    response_model=api_schemas.ProfileResponse,
    tags=["pipeline"],
)
def api_profile(workspace_id: str) -> api_schemas.ProfileResponse:
    d = assert_workspace(workspace_id)
    profiles = profile_directory(d)
    if not profiles:
        raise HTTPException(status_code=400, detail="No CSV files uploaded.")
    io_utils.dump_json(
        [p.model_dump(mode="json") for p in profiles], d / "profile.json"
    )
    return api_schemas.ProfileResponse(workspace_id=workspace_id, profiles=profiles)


@app.post(
    "/api/workspaces/{workspace_id}/design",
    response_model=api_schemas.DesignResponse,
    tags=["pipeline"],
)
def api_design(
    workspace_id: str,
    req: api_schemas.DesignRequest = Body(default=api_schemas.DesignRequest()),
) -> api_schemas.DesignResponse:
    d = assert_workspace(workspace_id)
    profiles = profile_directory(d)
    if not profiles:
        raise HTTPException(status_code=400, detail="Upload CSVs and run /profile first.")

    csv_paths = sorted(d.glob("*.csv"))
    if req.use_ai:
        schema, info = design_schema_with_ai(
            profiles=profiles,
            use_case=req.use_case,
            user_prompt=req.user_prompt,
            csv_paths=csv_paths,
        )
        design_mode = str(info.get("mode", "deterministic"))
    else:
        schema = design_schema(profiles, req.use_case)
        info = {"mode": "deterministic", "reason": "use_ai=false"}
        design_mode = "deterministic"

    io_utils.dump_schema(schema, d / "schema.json")
    io_utils.dump_json(info, d / "design_info.json")
    pattern = patterns.load_patterns().get(req.use_case)
    return api_schemas.DesignResponse(
        workspace_id=workspace_id,
        schema=schema,
        pattern=pattern,
        design_mode=design_mode,
        design_info=info,
    )


@app.post(
    "/api/workspaces/{workspace_id}/validate",
    response_model=api_schemas.ValidateResponse,
    tags=["pipeline"],
)
def api_validate(workspace_id: str) -> api_schemas.ValidateResponse:
    d = assert_workspace(workspace_id)
    schema = _load_schema_or_404(workspace_id)
    result = validator.validate(schema)
    io_utils.dump_json(result.model_dump(mode="json"), d / "validation.json")
    return api_schemas.ValidateResponse(workspace_id=workspace_id, validation=result)


@app.post(
    "/api/workspaces/{workspace_id}/score",
    response_model=api_schemas.ScoreResponse,
    tags=["pipeline"],
)
def api_score(workspace_id: str) -> api_schemas.ScoreResponse:
    d = assert_workspace(workspace_id)
    schema = _load_schema_or_404(workspace_id)
    result = validator.validate(schema)
    pattern = patterns.load_patterns()[schema.use_case]
    s = scorer.score_schema(schema, result, pattern)
    confidence = scorer.compute_confidence(s, result, schema.assumptions)
    io_utils.dump_json(s.model_dump(mode="json"), d / "score.json")
    return api_schemas.ScoreResponse(
        workspace_id=workspace_id, score=s, confidence=confidence
    )


@app.post(
    "/api/workspaces/{workspace_id}/emit",
    response_model=api_schemas.EmitResponse,
    tags=["pipeline"],
)
def api_emit(workspace_id: str, fmt: str = "gsql") -> api_schemas.EmitResponse:
    d = assert_workspace(workspace_id)
    schema = _load_schema_or_404(workspace_id)
    if fmt == "gsql":
        profiles = profile_directory(d)
        text = gsql_emitter.emit(schema, profiles)
        (d / "schema.gsql").write_text(text, encoding="utf-8")
    elif fmt == "markdown":
        result = validator.validate(schema)
        pattern = patterns.load_patterns()[schema.use_case]
        s = scorer.score_schema(schema, result, pattern)
        text = md_emitter.emit_markdown(schema, result, s)
        (d / "schema.md").write_text(text, encoding="utf-8")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown format: {fmt}")
    return api_schemas.EmitResponse(workspace_id=workspace_id, format=fmt, content=text)


@app.post(
    "/api/workspaces/{workspace_id}/run",
    response_model=api_schemas.RunResponse,
    tags=["pipeline"],
)
def api_run(
    workspace_id: str,
    req: api_schemas.DesignRequest = Body(default=api_schemas.DesignRequest()),
) -> api_schemas.RunResponse:
    d = assert_workspace(workspace_id)
    profiles = profile_directory(d)
    if not profiles:
        raise HTTPException(status_code=400, detail="Upload CSVs first.")
    io_utils.dump_json(
        [p.model_dump(mode="json") for p in profiles], d / "profile.json"
    )

    csv_paths = sorted(d.glob("*.csv"))
    if req.use_ai:
        schema, info = design_schema_with_ai(
            profiles=profiles,
            use_case=req.use_case,
            user_prompt=req.user_prompt,
            csv_paths=csv_paths,
        )
        design_mode = str(info.get("mode", "deterministic"))
    else:
        schema = design_schema(profiles, req.use_case)
        info = {"mode": "deterministic", "reason": "use_ai=false"}
        design_mode = "deterministic"

    io_utils.dump_schema(schema, d / "schema.json")
    io_utils.dump_json(info, d / "design_info.json")
    result = validator.validate(schema)
    io_utils.dump_json(result.model_dump(mode="json"), d / "validation.json")
    pattern = patterns.load_patterns()[req.use_case]
    s = scorer.score_schema(schema, result, pattern, user_prompt=req.user_prompt)
    io_utils.dump_json(s.model_dump(mode="json"), d / "score.json")
    gsql_text = gsql_emitter.emit(schema, profiles)
    (d / "schema.gsql").write_text(gsql_text, encoding="utf-8")
    md_text = md_emitter.emit_markdown(schema, result, s)
    (d / "schema.md").write_text(md_text, encoding="utf-8")

    # Optional: AI critic adds qualitative judgment alongside the deterministic score
    critic_dict: dict | None = None
    if req.use_ai:
        try:
            from tg_schema_agent.llm.critic import critique

            review = critique(
                schema=schema,
                validation=result,
                score=s,
                pattern=pattern,
                user_prompt=req.user_prompt,
                use_case=req.use_case,
            )
            if review is not None:
                critic_dict = review.model_dump()
                io_utils.dump_json(critic_dict, d / "critic.json")
        except Exception:  # noqa: BLE001 — critic is best-effort
            critic_dict = None

    return api_schemas.RunResponse(
        workspace_id=workspace_id,
        profiles=profiles,
        schema=schema,
        validation=result,
        score=s,
        gsql=gsql_text,
        markdown=md_text,
        design_mode=design_mode,
        design_info=info,
        critic=critic_dict,
    )


@app.get("/api/workspaces/{workspace_id}/run/stream", tags=["pipeline"])
async def api_run_stream(workspace_id: str, use_case: str = "FRAUD") -> StreamingResponse:
    """Server-Sent Events stream of the pipeline progress. The frontend can
    subscribe to this and update a progress bar as each step completes."""
    d = assert_workspace(workspace_id)
    try:
        uc = UseCase(use_case.upper())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def event_stream():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

        yield sse("step", {"step": "profile", "status": "started"})
        profiles = await asyncio.to_thread(profile_directory, d)
        io_utils.dump_json(
            [p.model_dump(mode="json") for p in profiles], d / "profile.json"
        )
        yield sse(
            "step",
            {
                "step": "profile",
                "status": "done",
                "table_count": len(profiles),
                "tables": [
                    {
                        "name": p.name,
                        "rows": p.row_count,
                        "wide_denormalized": p.is_wide_denormalized,
                        "event_signature": p.has_event_signature,
                    }
                    for p in profiles
                ],
            },
        )

        yield sse("step", {"step": "design", "status": "started"})
        schema = await asyncio.to_thread(design_schema, profiles, uc)
        io_utils.dump_schema(schema, d / "schema.json")
        yield sse(
            "step",
            {
                "step": "design",
                "status": "done",
                "vertices": len(schema.vertices),
                "edges": len(schema.edges),
            },
        )

        yield sse("step", {"step": "validate", "status": "started"})
        result = await asyncio.to_thread(validator.validate, schema)
        io_utils.dump_json(result.model_dump(mode="json"), d / "validation.json")
        yield sse(
            "step",
            {
                "step": "validate",
                "status": "done",
                "answerable": result.answerable_questions,
                "unanswerable": result.unanswerable_questions,
            },
        )

        yield sse("step", {"step": "score", "status": "started"})
        pattern = patterns.load_patterns()[uc]
        s = await asyncio.to_thread(scorer.score_schema, schema, result, pattern)
        io_utils.dump_json(s.model_dump(mode="json"), d / "score.json")
        yield sse(
            "step",
            {
                "step": "score",
                "status": "done",
                "total": s.total,
                "breakdown": s.breakdown,
            },
        )

        yield sse("step", {"step": "emit", "status": "started"})
        gsql_text = gsql_emitter.emit(schema, profiles)
        (d / "schema.gsql").write_text(gsql_text, encoding="utf-8")
        md_text = md_emitter.emit_markdown(schema, result, s)
        (d / "schema.md").write_text(md_text, encoding="utf-8")
        yield sse("step", {"step": "emit", "status": "done"})

        yield sse("done", {"workspace_id": workspace_id, "score": s.total})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------- chat (conversational agent) ----------


@app.get(
    "/api/workspaces/{workspace_id}/chat",
    response_model=list[api_schemas.ChatMessageOut],
    tags=["chat"],
)
def chat_history(workspace_id: str) -> list[api_schemas.ChatMessageOut]:
    assert_workspace(workspace_id)
    return [api_schemas.ChatMessageOut(**m) for m in load_chat_history(workspace_id)]


@app.delete("/api/workspaces/{workspace_id}/chat", tags=["chat"])
def chat_clear(workspace_id: str) -> dict[str, str]:
    assert_workspace(workspace_id)
    clear_chat_history(workspace_id)
    return {"status": "cleared", "workspace_id": workspace_id}


from pydantic import BaseModel as _BaseModel


class _ChatEventBody(_BaseModel):
    """Minimal body for posting a non-agent event into the chat transcript."""

    role: str = "agent"  # 'agent' | 'system'
    content: str
    type: str = "answer"  # 'answer' | 'progress'


@app.post("/api/workspaces/{workspace_id}/chat/event", tags=["chat"])
def chat_event(
    workspace_id: str,
    body: _ChatEventBody,
) -> dict[str, Any]:
    """Append a non-LLM event into the chat transcript so the user has a
    single Claude-Code-style timeline of everything — deploy completion,
    starter-query install, etc."""
    assert_workspace(workspace_id)
    saved = append_chat_message(
        workspace_id,
        role=body.role,
        content=body.content,
        type=body.type,
    )
    return {"workspace_id": workspace_id, "message": saved}


@app.post(
    "/api/workspaces/{workspace_id}/chat",
    response_model=api_schemas.ChatTurnResponse,
    tags=["chat"],
)
def chat_turn(
    workspace_id: str,
    req: api_schemas.ChatTurnRequest = Body(default=api_schemas.ChatTurnRequest()),
) -> api_schemas.ChatTurnResponse:
    from tg_schema_agent.llm import chat_agent as agent_mod

    d = assert_workspace(workspace_id)

    # Profiles are nice-to-have, not required. The agent can answer
    # general TigerGraph questions, deploy, query the live graph, etc.
    # even without an uploaded CSV.
    profiles = profile_directory(d)

    history_raw = load_chat_history(workspace_id)
    history = [agent_mod.ChatMessage(**m) for m in history_raw]

    user_msg = req.message.strip()
    is_kickoff = (not history_raw) and not user_msg

    # Record the user's message in history (skip on kickoff — agent starts)
    if user_msg:
        append_chat_message(workspace_id, "user", user_msg, type="answer")
        history.append(
            agent_mod.ChatMessage(role="user", content=user_msg, type="answer")
        )

    if not agent_mod.is_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Conversational agent requires GEMINI_API_KEY. Set it in .env and restart."
            ),
        )

    try:
        csv_paths = sorted(d.glob("*.csv"))
        reply = agent_mod.reply(
            user_message=user_msg,
            history=history,
            profiles=profiles,
            use_case=req.use_case,
            csv_paths=csv_paths,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Agent failed: {exc}") from exc

    # Persist the agent's reply
    saved = append_chat_message(
        workspace_id,
        "agent",
        reply.message,
        type=reply.type,
        schema_json=reply.schema_json,
        suggested_replies=reply.suggested_replies,
    )

    # If the agent proposed/updated a schema, also run validation + scoring + persist
    schema_obj = None
    val_obj = None
    score_obj = None
    if reply.type in ("propose_schema", "update_schema") and reply.schema_json:
        try:
            schema_obj = io_utils.load_schema(  # noqa: F841 — just for type
                d / "schema.json"
            ) if False else None
            # Easier: deserialize directly
            from tg_schema_agent.models import Schema as SchemaModel

            schema_obj = SchemaModel.model_validate(reply.schema_json)
            io_utils.dump_schema(schema_obj, d / "schema.json")
            val_obj = validator.validate(schema_obj)
            io_utils.dump_json(val_obj.model_dump(mode="json"), d / "validation.json")
            pattern = patterns.load_patterns()[req.use_case]
            # Find the latest user goal message to score motive alignment
            last_user_goal = next(
                (m.content for m in reversed(history) if m.role == "user"),
                None,
            )
            score_obj = scorer.score_schema(
                schema_obj, val_obj, pattern, user_prompt=last_user_goal
            )
            io_utils.dump_json(score_obj.model_dump(mode="json"), d / "score.json")
        except Exception as exc:  # noqa: BLE001
            # Schema processing failure — still return the chat message
            import logging

            logging.getLogger(__name__).warning("Post-propose processing failed: %s", exc)

    all_msgs = [api_schemas.ChatMessageOut(**m) for m in load_chat_history(workspace_id)]
    latest = api_schemas.ChatMessageOut(**saved)

    return api_schemas.ChatTurnResponse(
        workspace_id=workspace_id,
        messages=all_msgs,
        latest=latest,
        schema=schema_obj,
        score=score_obj,
        validation=val_obj,
    )


@app.post("/api/workspaces/{workspace_id}/chat/stream", tags=["chat"])
async def chat_turn_stream(
    workspace_id: str,
    req: api_schemas.ChatTurnRequest = Body(default=api_schemas.ChatTurnRequest()),
) -> StreamingResponse:
    """Drive the agentic tool-use loop over Server-Sent Events.

    Event taxonomy (see Phase 3 plan):
    - `event: plan`         — agent's plan for this turn (optional)
    - `event: thinking`     — model produced prose between/before tool calls
    - `event: tool_call`    — `{id, name, args}` each time the model invokes a tool
    - `event: tool_result`  — `{id, name, ok, summary}` after the tool runs
    - `event: schema_update`— `{schema}` after every propose/remove mutation
    - `event: final`        — final structured payload (terminates the stream)
    - `event: error`        — fatal error (terminates the stream)
    """
    from tg_schema_agent.llm import chat_agent as agent_mod

    d = assert_workspace(workspace_id)

    # Profiles are optional — agent can answer general TigerGraph
    # questions and operate the live graph without an upload.
    profiles = profile_directory(d)

    history_raw = load_chat_history(workspace_id)
    history = [agent_mod.ChatMessage(**m) for m in history_raw]
    user_msg = req.message.strip()

    # Persist the user message but DON'T add it to the in-memory history we
    # pass to the agent — `_history_to_contents` injects `user_message` as
    # the final turn, so doing both would duplicate the user's reply and
    # confuse Gemini (which then returns finish_reason=STOP with 0 parts).
    if user_msg:
        append_chat_message(workspace_id, "user", user_msg, type="answer")

    if not agent_mod.is_available():
        raise HTTPException(
            status_code=503,
            detail="Conversational agent requires GEMINI_API_KEY.",
        )

    def sse(event: str, payload: Any) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"

    async def event_stream():
        last_user_goal = user_msg or next(
            (m.content for m in reversed(history) if m.role == "user"),
            None,
        )

        terminal_payload: dict[str, Any] | None = None

        try:
            async for event_name, payload in agent_mod.run_agentic_turn(
                workspace_dir=d,
                user_message=user_msg,
                use_case=req.use_case,
                chat_history=history,
                user_prompt_for_scoring=last_user_goal,
            ):
                yield sse(event_name, payload)
                if event_name == "final":
                    terminal_payload = payload
                if event_name == "error":
                    return
        except Exception as exc:  # noqa: BLE001
            yield sse("error", {"message": f"agent loop crashed: {exc}", "code": "agent_crash"})
            return

        # Persist the agent's final message to chat history
        if terminal_payload:
            append_chat_message(
                workspace_id,
                "agent",
                terminal_payload.get("message", ""),
                type=terminal_payload.get("type", "answer"),
                schema_json=terminal_payload.get("schema"),
                suggested_replies=terminal_payload.get("suggested_replies", []),
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- deploy ----------


@app.post(
    "/api/workspaces/{workspace_id}/deploy",
    response_model=api_schemas.DeployResponse,
    tags=["deploy"],
)
async def api_deploy(
    workspace_id: str,
    req: api_schemas.DeployRequest,
) -> api_schemas.DeployResponse:
    from tg_schema_agent.deploy import build_plan, deploy as run_deploy, render_dry_run

    d = assert_workspace(workspace_id)
    schema = _load_schema_or_404(workspace_id)
    csv_path = d / Path(req.csv_filename).name
    if not csv_path.exists():
        raise HTTPException(
            status_code=400, detail=f"CSV not found in workspace: {req.csv_filename}"
        )

    profiles = profile_directory(d)
    if not profiles:
        raise HTTPException(status_code=400, detail="No CSVs in workspace.")

    # Hydrate creds from server .env when the client omits fields.
    # This is the "creds live in .env" path the user picked during planning.
    effective_host = req.creds.host or os.environ.get("TG_HOST", "")
    effective_graph = req.creds.graph_name or os.environ.get("TG_GRAPHNAME", "")
    if not effective_host:
        raise HTTPException(
            status_code=400,
            detail="No TG_HOST configured. Set it in .env or pass creds.host.",
        )
    if not effective_graph:
        raise HTTPException(
            status_code=400,
            detail="No TG_GRAPHNAME configured. Set it in .env or pass creds.graph_name.",
        )

    plan = build_plan(schema, profiles, csv_path, graph_name=effective_graph)

    # Build env dict for the MCP subprocess (creds-from-request override .env)
    env = {
        "TG_HOST": effective_host,
        "TG_GRAPHNAME": effective_graph,
    }
    if req.creds.username or os.environ.get("TG_USERNAME"):
        env["TG_USERNAME"] = req.creds.username or os.environ["TG_USERNAME"]
    if req.creds.password or os.environ.get("TG_PASSWORD"):
        env["TG_PASSWORD"] = req.creds.password or os.environ["TG_PASSWORD"]
    if req.creds.api_token or os.environ.get("TG_API_TOKEN"):
        env["TG_API_TOKEN"] = req.creds.api_token or os.environ["TG_API_TOKEN"]
    if req.creds.restpp_port is not None:
        env["TG_RESTPP_PORT"] = str(req.creds.restpp_port)
    elif os.environ.get("TG_RESTPP_PORT"):
        env["TG_RESTPP_PORT"] = os.environ["TG_RESTPP_PORT"]
    if req.creds.gs_port is not None:
        env["TG_GS_PORT"] = str(req.creds.gs_port)
    elif os.environ.get("TG_GS_PORT"):
        env["TG_GS_PORT"] = os.environ["TG_GS_PORT"]
    if req.creds.is_tgcloud or os.environ.get("TG_TGCLOUD", "").lower() == "true":
        env["TG_TGCLOUD"] = "true"

    if req.dry_run:
        return api_schemas.DeployResponse(
            workspace_id=workspace_id,
            graph_name=plan.graph_name,
            vertex_counts={},
            steps=[],
            errors=[],
            dry_run_plan=render_dry_run(plan, env),
        )

    # Inject env into os.environ for the MCP subprocess; deploy() reads OS env.
    saved = {k: os.environ.get(k) for k in env}
    try:
        os.environ.update(env)
        report = await run_deploy(
            schema=schema,
            profiles=profiles,
            csv_path=csv_path,
            graph_name=effective_graph,
            remote=True,  # TG Cloud / Savanna — send CSV inline via MCP
            load_data=req.load_data,
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    io_utils.dump_json(report, d / "deploy_report.json")
    return api_schemas.DeployResponse(
        workspace_id=workspace_id,
        graph_name=report.get("graph_name", plan.graph_name),
        vertex_counts=report.get("vertex_counts", {}),
        steps=report.get("steps", []),
        errors=report.get("errors", []),
    )


@app.post(
    "/api/workspaces/{workspace_id}/deploy/stream",
    tags=["deploy"],
)
async def api_deploy_stream(
    workspace_id: str,
    req: api_schemas.DeployRequest,
) -> StreamingResponse:
    """Stream deployment progress as Server-Sent Events.

    Emits the following events:
    - `event: step`  {phase, name, status, summary}
    - `event: count` {vertex, count}
    - `event: done`  {graph_name, vertex_counts, errors}
    - `event: error` {message, code}
    """
    from tg_schema_agent.deploy import build_plan, deploy as run_deploy

    d = assert_workspace(workspace_id)
    schema = _load_schema_or_404(workspace_id)
    csv_path = d / Path(req.csv_filename).name
    if not csv_path.exists():
        raise HTTPException(
            status_code=400, detail=f"CSV not found in workspace: {req.csv_filename}"
        )

    profiles = profile_directory(d)
    if not profiles:
        raise HTTPException(status_code=400, detail="No CSVs in workspace.")

    # Hydrate creds from .env when missing
    effective_host = req.creds.host or os.environ.get("TG_HOST", "")
    effective_graph = req.creds.graph_name or os.environ.get("TG_GRAPHNAME", "")
    if not effective_host or not effective_graph:

        def _missing_stream():
            missing = []
            if not effective_host:
                missing.append("TG_HOST")
            if not effective_graph:
                missing.append("TG_GRAPHNAME")
            yield (
                f"event: error\ndata: "
                f"{json.dumps({'message': f'Missing required config: {missing}. Set in .env.', 'code': 'missing_config'})}\n\n"
            )

        return StreamingResponse(_missing_stream(), media_type="text/event-stream")

    plan = build_plan(schema, profiles, csv_path, graph_name=effective_graph)

    env: dict[str, str] = {
        "TG_HOST": effective_host,
        "TG_GRAPHNAME": effective_graph,
    }
    if req.creds.username or os.environ.get("TG_USERNAME"):
        env["TG_USERNAME"] = req.creds.username or os.environ["TG_USERNAME"]
    if req.creds.password or os.environ.get("TG_PASSWORD"):
        env["TG_PASSWORD"] = req.creds.password or os.environ["TG_PASSWORD"]
    if req.creds.api_token or os.environ.get("TG_API_TOKEN"):
        env["TG_API_TOKEN"] = req.creds.api_token or os.environ["TG_API_TOKEN"]
    if req.creds.restpp_port is not None:
        env["TG_RESTPP_PORT"] = str(req.creds.restpp_port)
    elif os.environ.get("TG_RESTPP_PORT"):
        env["TG_RESTPP_PORT"] = os.environ["TG_RESTPP_PORT"]
    if req.creds.gs_port is not None:
        env["TG_GS_PORT"] = str(req.creds.gs_port)
    elif os.environ.get("TG_GS_PORT"):
        env["TG_GS_PORT"] = os.environ["TG_GS_PORT"]
    if req.creds.is_tgcloud or os.environ.get("TG_TGCLOUD", "").lower() == "true":
        env["TG_TGCLOUD"] = "true"

    def sse(event: str, payload: Any) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"

    async def event_stream():
        # Bridge: deploy() runs in the same task and pushes events into the queue;
        # we drain it and emit SSE frames.
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress(event: Any) -> None:
            # `event` may be a dict (new structured form) or a plain str (legacy)
            if isinstance(event, dict):
                payload = {
                    "phase": event.get("phase", ""),
                    "name": event.get("name", ""),
                    "status": event.get("status", "running"),
                    "summary": event.get("summary", ""),
                }
            else:
                payload = {"phase": "log", "name": "", "status": "info", "summary": str(event)}
            # called from inside deploy() — schedule onto the loop
            loop.call_soon_threadsafe(queue.put_nowait, ("step", payload))

        # Inject env into os.environ while deploy() runs
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)

        async def runner():
            try:
                report = await run_deploy(
                    schema=schema,
                    profiles=profiles,
                    csv_path=csv_path,
                    graph_name=effective_graph,
                    progress=progress,
                    remote=True,
                    load_data=req.load_data,
                )
                await queue.put(("done", {
                    "graph_name": report.get("graph_name", plan.graph_name),
                    "vertex_counts": report.get("vertex_counts", {}),
                    "errors": report.get("errors", []),
                }))
                try:
                    io_utils.dump_json(report, d / "deploy_report.json")
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001
                await queue.put(("error", {
                    "message": f"deploy failed: {type(exc).__name__}: {exc}",
                    "code": "deploy_crash",
                }))
            finally:
                # restore env
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
                await queue.put(("__end__", None))

        task = asyncio.create_task(runner())
        try:
            while True:
                event_name, payload = await queue.get()
                if event_name == "__end__":
                    break
                yield sse(event_name, payload)
                # Emit a separate `count` event when the step is a vertex count
                if (
                    event_name == "step"
                    and isinstance(payload, dict)
                    and payload.get("phase") == "counts"
                    and payload.get("status") == "ok"
                ):
                    name = payload.get("name", "")
                    # Parse "VertexName = 77" from the summary
                    summary = payload.get("summary", "")
                    if "=" in summary:
                        try:
                            count_val = int(summary.split("=", 1)[1].strip())
                            yield sse("count", {"vertex": name, "count": count_val})
                        except ValueError:
                            pass
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- starter queries (Autograph Behavior 9) ----------


@app.post(
    "/api/workspaces/{workspace_id}/queries/generate",
    response_model=api_schemas.StarterQueriesResponse,
    tags=["deploy"],
)
async def api_generate_starter_queries(
    workspace_id: str,
    req: api_schemas.DeployRequest,
) -> api_schemas.StarterQueriesResponse:
    """Generate LLM-authored starter GSQL queries against the deployed
    graph. Each query is dry-run validated via INTERPRET QUERY; failed
    ones are re-prompted once with the error in context."""
    from tg_schema_agent.deploy import _load_env, _open_session
    from tg_schema_agent.llm.queries import generate_starter_queries

    d = assert_workspace(workspace_id)
    schema = _load_schema_or_404(workspace_id)

    effective_host = req.creds.host or os.environ.get("TG_HOST", "")
    effective_graph = req.creds.graph_name or os.environ.get("TG_GRAPHNAME", "")
    if not effective_host or not effective_graph:
        raise HTTPException(
            status_code=400,
            detail="Missing TG_HOST or TG_GRAPHNAME; set in .env or pass creds.",
        )

    env = {"TG_HOST": effective_host, "TG_GRAPHNAME": effective_graph}
    if req.creds.api_token or os.environ.get("TG_API_TOKEN"):
        env["TG_API_TOKEN"] = req.creds.api_token or os.environ["TG_API_TOKEN"]
    if req.creds.username or os.environ.get("TG_USERNAME"):
        env["TG_USERNAME"] = req.creds.username or os.environ["TG_USERNAME"]
    if req.creds.password or os.environ.get("TG_PASSWORD"):
        env["TG_PASSWORD"] = req.creds.password or os.environ["TG_PASSWORD"]
    if req.creds.is_tgcloud or os.environ.get("TG_TGCLOUD", "").lower() == "true":
        env["TG_TGCLOUD"] = "true"

    saved = {k: os.environ.get(k) for k in env}
    try:
        os.environ.update(env)
        merged_env = _load_env(None)
        async with _open_session(merged_env) as session:
            qs = await generate_starter_queries(
                session=session,
                schema=schema,
                graph_name=effective_graph,
                business_context=schema.business_context,
            )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    items = [api_schemas.StarterQueryItem(**q.model_dump()) for q in qs.queries]
    io_utils.dump_json(
        {"graph_name": effective_graph, "queries": [q.model_dump(mode="json") for q in qs.queries]},
        d / "starter_queries.json",
    )
    return api_schemas.StarterQueriesResponse(
        workspace_id=workspace_id,
        graph_name=effective_graph,
        queries=items,
    )


@app.post(
    "/api/workspaces/{workspace_id}/queries/install",
    response_model=api_schemas.InstallStarterQueryResponse,
    tags=["deploy"],
)
async def api_install_starter_query(
    workspace_id: str,
    req: api_schemas.InstallStarterQueryRequest,
) -> api_schemas.InstallStarterQueryResponse:
    """Install one starter query into TigerGraph via
    `tigergraph__install_query`. Caller passes the GSQL body verbatim."""
    from tg_schema_agent.deploy import (
        _call,
        _is_success,
        _load_env,
        _open_session,
        _summarize_error,
    )

    assert_workspace(workspace_id)

    effective_host = req.creds.host or os.environ.get("TG_HOST", "")
    effective_graph = req.creds.graph_name or os.environ.get("TG_GRAPHNAME", "")
    if not effective_host or not effective_graph:
        raise HTTPException(status_code=400, detail="Missing TG creds")

    env = {"TG_HOST": effective_host, "TG_GRAPHNAME": effective_graph}
    if req.creds.api_token or os.environ.get("TG_API_TOKEN"):
        env["TG_API_TOKEN"] = req.creds.api_token or os.environ["TG_API_TOKEN"]
    if req.creds.is_tgcloud or os.environ.get("TG_TGCLOUD", "").lower() == "true":
        env["TG_TGCLOUD"] = "true"

    saved = {k: os.environ.get(k) for k in env}
    try:
        os.environ.update(env)
        merged_env = _load_env(None)
        async with _open_session(merged_env) as session:
            create_payload = await _call(
                session,
                "tigergraph__gsql",
                {"command": f"USE GRAPH {effective_graph}\n{req.gsql}"},
            )
            if not _is_success(create_payload):
                return api_schemas.InstallStarterQueryResponse(
                    workspace_id=workspace_id,
                    query_name=req.query_name,
                    ok=False,
                    summary="define failed",
                    error=_summarize_error(create_payload),
                )

            install_payload = await _call(
                session,
                "tigergraph__install_query",
                {"graph_name": effective_graph, "query_name": req.query_name},
            )
            if not _is_success(install_payload):
                return api_schemas.InstallStarterQueryResponse(
                    workspace_id=workspace_id,
                    query_name=req.query_name,
                    ok=False,
                    summary="install failed",
                    error=_summarize_error(install_payload),
                )
            return api_schemas.InstallStarterQueryResponse(
                workspace_id=workspace_id,
                query_name=req.query_name,
                ok=True,
                summary=f"Installed {req.query_name} in {effective_graph}",
            )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
