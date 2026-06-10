"""Regression tests for the GSQL emitter.

The Schema model carries presentation-only fields (business_context,
design_rationale, recommendation, assumptions) that the emitter MUST ignore.
This test guards against accidental leakage into the generated DDL.
"""
from __future__ import annotations

from tg_schema_agent.emitters import gsql as gsql_emitter
from tg_schema_agent.enums import DataKind, EdgeDirection, UseCase
from tg_schema_agent.models import (
    Assumption,
    Attribute,
    BusinessContext,
    DesignRationale,
    Edge,
    EdgeSource,
    RecommendationSummary,
    RecommendedEntity,
    Schema,
    Vertex,
    VertexSource,
)


def _minimal_schema() -> Schema:
    return Schema(
        use_case=UseCase.FRAUD,
        name="fraud_schema",
        vertices=[
            Vertex(
                name="Customer",
                primary_id="customer_id",
                primary_id_dtype=DataKind.STRING,
                attributes=[
                    Attribute(
                        name="first_name",
                        dtype=DataKind.STRING,
                        source_table="data",
                        source_column="first_name",
                    )
                ],
                source=VertexSource(table="data", columns=["customer_id"]),
            ),
            Vertex(
                name="Transaction",
                primary_id="transaction_id",
                primary_id_dtype=DataKind.STRING,
                attributes=[],
                source=VertexSource(table="data", columns=["transaction_id"]),
            ),
        ],
        edges=[
            Edge(
                name="Customer_MADE_Transaction",
                from_vertex="Customer",
                to_vertex="Transaction",
                direction=EdgeDirection.DIRECTED,
                attributes=[],
                source=EdgeSource(table="data"),
            )
        ],
    )


def test_emitter_ignores_business_context():
    base = _minimal_schema()
    baseline_gsql = gsql_emitter.emit(base)

    enriched = base.model_copy(
        update={
            "business_context": BusinessContext(
                domain="fraud",
                sub_scenarios=["mule_accounts"],
                goal_type="investigation",
                business_questions=["Which accounts share devices?"],
                stakeholders=["investigators"],
            ),
            "design_rationale": DesignRationale(
                bullets=[
                    "Treated transactions as event vertices for traversal.",
                    "Modeled devices as shared-identifier vertices.",
                ]
            ),
            "recommendation": RecommendationSummary(
                entities=[
                    RecommendedEntity(name="Customer", one_liner="People who own accounts"),
                ],
                expected_outcomes=["Detect fraud rings"],
                future_enhancements=["GNN feature engineering"],
            ),
            "assumptions": [
                Assumption(
                    text="transactions is the system of record",
                    evidence="71k rows, no FKs pointing in",
                    confidence="high",
                )
            ],
        }
    )
    enriched_gsql = gsql_emitter.emit(enriched)

    assert baseline_gsql == enriched_gsql, (
        "Schema presentation fields (business_context / design_rationale / "
        "recommendation / assumptions) must NOT leak into generated GSQL DDL."
    )


def test_emitter_does_not_mention_presentation_fields():
    enriched = _minimal_schema().model_copy(
        update={
            "business_context": BusinessContext(domain="fraud"),
            "design_rationale": DesignRationale(bullets=["A bullet"]),
            "recommendation": RecommendationSummary(expected_outcomes=["Detect X"]),
            "assumptions": [Assumption(text="t", evidence="e")],
        }
    )
    gsql = gsql_emitter.emit(enriched)
    for forbidden in (
        "business_context",
        "design_rationale",
        "recommendation",
        "assumption",
        "expected_outcome",
        "future_enhancement",
        "Detect X",
        "A bullet",
    ):
        assert forbidden not in gsql, f"Found {forbidden!r} leaked into GSQL DDL"
