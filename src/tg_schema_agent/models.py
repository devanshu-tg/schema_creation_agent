from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tg_schema_agent.enums import (
    Cardinality,
    DataKind,
    EdgeDirection,
    PIIClass,
    UseCase,
)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class ColumnRef(_Base):
    table: str
    column: str


class ForeignKey(_Base):
    column: str
    references: ColumnRef


class ColumnProfile(_Base):
    name: str
    dtype: DataKind
    null_pct: float = 0.0
    distinct_count: int = 0
    row_count: int = 0
    cardinality: Cardinality = Cardinality.MEDIUM
    is_primary_key_candidate: bool = False
    is_foreign_key_candidate: bool = False
    references: list[ColumnRef] = Field(default_factory=list)
    name_pattern_hits: list[str] = Field(default_factory=list)
    pii_class: PIIClass = PIIClass.NONE
    sample_values: list[str] = Field(default_factory=list)


class TableProfile(_Base):
    name: str
    row_count: int
    columns: list[ColumnProfile]
    primary_key: list[str] | None = None
    foreign_keys: list[ForeignKey] = Field(default_factory=list)
    has_event_signature: bool = False
    has_join_signature: bool = False
    is_wide_denormalized: bool = False
    detected_delimiter: str = ","

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)


# ---------- Schema artifacts ----------


class Attribute(_Base):
    name: str
    dtype: DataKind
    source_table: str
    source_column: str
    pii_class: PIIClass = PIIClass.NONE
    nullable: bool = True


class VertexSource(_Base):
    kind: Literal["table_column", "column_group", "derived"] = "table_column"
    table: str
    columns: list[str]  # one for table_column; multiple for column_group


class EdgeSource(_Base):
    kind: Literal["fk", "wide_table_pair", "derived"] = "fk"
    table: str
    from_column: str | None = None
    to_column: str | None = None


class Vertex(_Base):
    name: str
    primary_id: str
    primary_id_dtype: DataKind = DataKind.STRING
    attributes: list[Attribute] = Field(default_factory=list)
    source: VertexSource
    rationale: str = ""
    pattern_origin: str | None = None


class Edge(_Base):
    name: str
    from_vertex: str
    to_vertex: str
    direction: EdgeDirection = EdgeDirection.DIRECTED
    reverse_edge_name: str | None = None
    attributes: list[Attribute] = Field(default_factory=list)
    source: EdgeSource | None = None
    rationale: str = ""
    pattern_origin: str | None = None


class TargetQuestion(_Base):
    id: str
    text: str
    required_vertices: list[str] = Field(default_factory=list)
    required_edges: list[str] = Field(default_factory=list)
    max_hops: int = 3


class Assumption(_Base):
    """A modeling decision the agent committed to during exploration.

    Captured live (via `record_assumption` tool) so the reasoning trail is
    evidence-grounded rather than post-hoc rationalization.
    """

    text: str
    evidence: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"


class BusinessContext(_Base):
    """The business problem the user is solving (Autograph Behavior 1+2).

    Captured during the DECISION stage, before the agent starts modeling.
    Lets the schema, validation, and starter queries be tailored to the
    actual decision instead of being guessed from data shape.
    """

    domain: str = ""  # "fraud", "customer_360", "entity_resolution", "supply_chain", etc.
    sub_scenarios: list[str] = Field(default_factory=list)
    """e.g. ["payment_fraud", "mule_accounts", "ring_investigation"]."""
    goal_type: Literal["", "detection", "investigation", "explainability", "risk_scoring"] = ""
    business_questions: list[str] = Field(default_factory=list)
    """Free-text questions the graph must answer."""
    stakeholders: list[str] = Field(default_factory=list)
    """e.g. ["investigators", "ml_models", "ai_agents", "analysts"]."""


class DesignRationale(_Base):
    """High-level architectural reasoning the agent surfaces at finalize time
    (Autograph Behavior 6). Distinct from per-vertex/edge `rationale` fields —
    this explains the schema's overall shape, not each piece."""

    bullets: list[str] = Field(default_factory=list)


class RecommendedEntity(_Base):
    name: str
    one_liner: str = ""


class RecommendationSummary(_Base):
    """Structured pre-deploy recommendation (Autograph Behavior 8).

    Populated by the agent during `finalize_schema`. Renders as the three
    new sections in OutcomesPanel: Recommended Entities / Expected Outcomes
    / Potential Future Enhancements.
    """

    entities: list[RecommendedEntity] = Field(default_factory=list)
    expected_outcomes: list[str] = Field(default_factory=list)
    """Capabilities the graph unlocks; distinct from answerable target questions."""
    future_enhancements: list[str] = Field(default_factory=list)
    """Explicit deferred work — what we chose NOT to model in this phase."""


class Schema(_Base):
    use_case: UseCase
    name: str
    version: str = "0.1.0"
    pattern_version: str | None = None
    vertices: list[Vertex] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    target_questions: list[TargetQuestion] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    # Autograph Behaviors 1, 2, 6, 8 — captured live by the agent, never
    # consumed by the GSQL emitter (purely presentation).
    business_context: BusinessContext | None = None
    design_rationale: DesignRationale | None = None
    recommendation: RecommendationSummary | None = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    inputs_hash: str = ""

    def vertex(self, name: str) -> Vertex | None:
        return next((v for v in self.vertices if v.name == name), None)

    def edge(self, name: str) -> Edge | None:
        return next((e for e in self.edges if e.name == name), None)


# ---------- Pattern library ----------


class CanonicalAttribute(_Base):
    name: str
    dtype: DataKind
    aliases: list[str] = Field(default_factory=list)
    optional: bool = False


class VertexSpec(_Base):
    name: str
    primary_id: str
    dtype: DataKind = DataKind.STRING
    name_aliases: list[str] = Field(default_factory=list)
    canonical_attributes: list[CanonicalAttribute] = Field(default_factory=list)
    promotion_rule: str | None = None  # e.g. event_signature, shared_identifier
    pii: PIIClass = PIIClass.NONE
    composed_from: list[str] = Field(default_factory=list)
    optional: bool = False


class EdgeSpec(_Base):
    name: str
    from_: str = Field(alias="from")
    to: str
    direction: EdgeDirection = EdgeDirection.DIRECTED
    reverse_name: str | None = None
    attributes: list[CanonicalAttribute] = Field(default_factory=list)
    optional: bool = False

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Pattern(_Base):
    use_case: UseCase
    name: str
    version: str
    description: str = ""
    vertices: list[VertexSpec]
    edges: list[EdgeSpec]
    target_questions: list[TargetQuestion]
    sample_gsql: str | None = None


# ---------- Validation & scoring ----------


class CheckResult(_Base):
    id: str
    name: str
    passed: bool
    detail: str = ""


class ValidationResult(_Base):
    passed: bool
    checks: list[CheckResult] = Field(default_factory=list)
    answerable_questions: list[str] = Field(default_factory=list)
    unanswerable_questions: list[str] = Field(default_factory=list)
    structural_warnings: list[str] = Field(default_factory=list)


class SchemaScore(_Base):
    total: int
    breakdown: dict[str, int] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


# ---------- Rule engine outputs ----------


class RuleRecommendation(_Base):
    """Output from a single deterministic rule in `rules.py`.

    Renamed from `Recommendation` to free up that name for the new
    Autograph-style schema-design recommendation (Behavior 8).
    """

    rule_id: str
    action: Literal[
        "promote_vertex",
        "promote_event_vertex",
        "create_edge",
        "create_transfer_edge",
        "keep_attribute",
        "add_reverse_edge",
        "attach_attribute",
        "tag_pii",
        "warn",
        "decompose_wide_table",
    ]
    target: str  # column name, table name, or vertex/edge name
    rationale: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)
