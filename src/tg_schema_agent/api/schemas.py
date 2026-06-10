"""API request/response models.

These are the wire-format types for the HTTP API. They wrap the internal Pydantic
models from `tg_schema_agent.models` for clean OpenAPI documentation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from tg_schema_agent.enums import UseCase
from tg_schema_agent.models import (
    Pattern,
    Schema,
    SchemaScore,
    TableProfile,
    ValidationResult,
)


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    use_cases: list[str]


class UseCaseInfo(BaseModel):
    id: str
    name: str
    version: str
    description: str
    vertex_count: int
    edge_count: int
    target_question_count: int


# ---------- Workspace (session) ----------


class WorkspaceCreated(BaseModel):
    workspace_id: str
    files: list[str] = Field(default_factory=list)


class WorkspaceInfo(BaseModel):
    workspace_id: str
    files: list[str]
    profiles_ready: bool = False
    schema_ready: bool = False
    deployed: bool = False


# ---------- Pipeline ----------


class ProfileResponse(BaseModel):
    workspace_id: str
    profiles: list[TableProfile]


class DesignRequest(BaseModel):
    use_case: UseCase = UseCase.FRAUD
    user_prompt: str | None = Field(
        default=None,
        description=(
            "Free-text intent from the user (e.g. 'I want to detect fraud rings "
            "where multiple accounts share devices'). When provided alongside a "
            "valid GEMINI_API_KEY, the schema is designed by Gemini using this "
            "prompt + the data profiles + the canonical pattern."
        ),
    )
    use_ai: bool = Field(
        default=True,
        description=(
            "If true and GEMINI_API_KEY is set, the LLM designs the schema. "
            "Falls back to deterministic when AI is unavailable."
        ),
    )


class DesignResponse(BaseModel):
    workspace_id: str
    schema: Schema  # noqa: A003 — domain term
    pattern: Pattern | None = None
    design_mode: str = "deterministic"
    design_info: dict = Field(default_factory=dict)


class ValidateResponse(BaseModel):
    workspace_id: str
    validation: ValidationResult


class ScoreResponse(BaseModel):
    workspace_id: str
    score: SchemaScore
    confidence: str | None = Field(
        default=None,
        description="Composite confidence label (High/Medium/Low) — Autograph Behavior 8.",
    )


class EmitResponse(BaseModel):
    workspace_id: str
    format: str
    content: str


class RunResponse(BaseModel):
    workspace_id: str
    profiles: list[TableProfile]
    schema: Schema  # noqa: A003
    validation: ValidationResult
    score: SchemaScore
    gsql: str
    markdown: str
    design_mode: str = "deterministic"
    design_info: dict = Field(default_factory=dict)
    critic: dict | None = Field(
        default=None,
        description=(
            "Optional plain-language review from Gemini. Includes letter grade, "
            "strengths, improvement suggestions, and a next-step recommendation. "
            "Null if no API key is set or the critic call failed."
        ),
    )


# ---------- Deploy ----------


class TigerGraphCreds(BaseModel):
    """TigerGraph connection. All fields optional — server falls back to
    the matching `TG_*` env var when a field is empty / unset."""

    host: str = Field(
        default="", description="e.g. http://localhost or https://savanna.example.com"
    )
    graph_name: str = ""
    username: str | None = None
    password: str | None = None
    api_token: str | None = None
    restpp_port: int | None = None
    gs_port: int | None = None
    is_tgcloud: bool = False


class DeployRequest(BaseModel):
    csv_filename: str = Field(..., description="Name of an uploaded CSV in the workspace")
    creds: TigerGraphCreds = Field(default_factory=TigerGraphCreds)
    dry_run: bool = False
    load_data: bool = Field(
        default=False,
        description=(
            "If True, after schema creation also build + run a loading "
            "job for the given CSV. Default False so schema-only deploys "
            "(the prior behavior) still work unchanged."
        ),
    )


class DeployResponse(BaseModel):
    workspace_id: str
    graph_name: str
    vertex_counts: dict[str, int | None]
    steps: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    dry_run_plan: str | None = None


# ---------- Starter queries (Autograph Behavior 9) ----------


class StarterQueryItem(BaseModel):
    name: str
    description: str
    business_question: str = ""
    gsql: str
    expected_output_description: str = ""
    validated: bool = False
    validation_error: str | None = None


class StarterQueriesResponse(BaseModel):
    workspace_id: str
    graph_name: str
    queries: list[StarterQueryItem] = Field(default_factory=list)


class InstallStarterQueryRequest(BaseModel):
    creds: TigerGraphCreds = Field(default_factory=TigerGraphCreds)
    query_name: str
    gsql: str


class InstallStarterQueryResponse(BaseModel):
    workspace_id: str
    query_name: str
    ok: bool
    summary: str = ""
    error: str | None = None


# ---------- Chat (conversational agent) ----------


class ChatMessageOut(BaseModel):
    role: str
    content: str
    type: str = "answer"
    schema_json: dict | None = None
    suggested_replies: list[str] = Field(default_factory=list)
    timestamp: str = ""


class ChatTurnRequest(BaseModel):
    message: str = Field(default="", description="The user's latest message (empty = kickoff).")
    use_case: UseCase = UseCase.FRAUD


class ChatTurnResponse(BaseModel):
    workspace_id: str
    messages: list[ChatMessageOut]
    latest: ChatMessageOut
    schema: Schema | None = None  # noqa: A003
    score: SchemaScore | None = None
    validation: ValidationResult | None = None
