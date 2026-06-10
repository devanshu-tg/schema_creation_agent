"""Schema validator: structural checks + target-question coverage."""

from __future__ import annotations

from tg_schema_agent.enums import DataKind, EdgeDirection
from tg_schema_agent.models import (
    CheckResult,
    Schema,
    ValidationResult,
)


def _check_primary_ids(schema: Schema) -> CheckResult:
    missing = [v.name for v in schema.vertices if not v.primary_id]
    return CheckResult(
        id="C1_primary_id_present",
        name="Every vertex has a primary_id",
        passed=not missing,
        detail="" if not missing else f"Vertices missing primary_id: {missing}",
    )


def _check_edge_endpoints(schema: Schema) -> CheckResult:
    vnames = {v.name for v in schema.vertices}
    bad: list[str] = []
    for e in schema.edges:
        if e.from_vertex not in vnames or e.to_vertex not in vnames:
            bad.append(f"{e.name}({e.from_vertex} -> {e.to_vertex})")
    return CheckResult(
        id="C2_edge_endpoints_resolve",
        name="Every edge endpoint resolves to a vertex",
        passed=not bad,
        detail="" if not bad else f"Edges with unresolved endpoints: {bad}",
    )


def _check_unique_names(schema: Schema) -> CheckResult:
    vnames = [v.name for v in schema.vertices]
    enames = [e.name for e in schema.edges]
    dup_v = [n for n in set(vnames) if vnames.count(n) > 1]
    dup_e = [n for n in set(enames) if enames.count(n) > 1]
    bad = []
    if dup_v:
        bad.append(f"duplicate vertices: {dup_v}")
    if dup_e:
        bad.append(f"duplicate edges: {dup_e}")
    return CheckResult(
        id="C3_unique_names",
        name="No duplicate vertex or edge names",
        passed=not bad,
        detail="; ".join(bad),
    )


def _check_reverse_edges_for_multi_hop(schema: Schema) -> CheckResult:
    multi_hop_edges = set()
    for q in schema.target_questions:
        if q.max_hops > 1:
            multi_hop_edges.update(q.required_edges)
    missing = []
    for e in schema.edges:
        if (
            e.name in multi_hop_edges
            and e.direction == EdgeDirection.DIRECTED
            and not e.reverse_edge_name
        ):
            missing.append(e.name)
    return CheckResult(
        id="C4_multi_hop_reverse_edges",
        name="Multi-hop edges have reverse edges or are directed-with-reverse",
        passed=not missing,
        detail="" if not missing else f"Missing reverse edges: {missing}",
    )


def _check_event_vertex_attrs(schema: Schema) -> CheckResult:
    """Event vertices should carry timestamp + amount when those exist in the source."""
    bad = []
    for v in schema.vertices:
        if v.pattern_origin and "Transaction" in v.name or v.name.endswith("Event"):
            has_ts = any(a.dtype == DataKind.DATETIME for a in v.attributes)
            has_amount = any(a.dtype in (DataKind.FLOAT, DataKind.INT) for a in v.attributes)
            if not (has_ts and has_amount):
                bad.append(v.name)
    return CheckResult(
        id="C5_event_vertex_attrs",
        name="Event vertices carry timestamp + amount",
        passed=not bad,
        detail="" if not bad else f"Event vertices missing ts/amount: {bad}",
    )


def _check_target_questions(schema: Schema) -> tuple[CheckResult, list[str], list[str]]:
    vnames = {v.name for v in schema.vertices}
    enames = {e.name for e in schema.edges}
    answerable: list[str] = []
    unanswerable: list[str] = []
    detail_bits = []
    for q in schema.target_questions:
        missing_v = [v for v in q.required_vertices if v not in vnames]
        missing_e = [e for e in q.required_edges if e not in enames]
        if not missing_v and not missing_e:
            answerable.append(q.id)
        else:
            unanswerable.append(q.id)
            if missing_v:
                detail_bits.append(f"{q.id}: missing vertices {missing_v}")
            if missing_e:
                detail_bits.append(f"{q.id}: missing edges {missing_e}")
    check = CheckResult(
        id="C6_target_questions_answerable",
        name="All target questions answerable",
        passed=not unanswerable,
        detail="; ".join(detail_bits),
    )
    return check, answerable, unanswerable


def _check_attribute_placement(schema: Schema) -> CheckResult:
    """If a Vertex X exists in the schema, no other Vertex should carry X's id as an attribute."""
    bad = []
    vnames_lower = {v.name.lower(): v for v in schema.vertices}
    for v in schema.vertices:
        for a in v.attributes:
            n = a.name.lower().rstrip("0123456789_")
            # If attribute name matches another vertex name, that's misplaced.
            for vname_lower in vnames_lower:
                if vname_lower != v.name.lower() and vname_lower in n and vname_lower not in {"name", "id"}:
                    if vname_lower in ("device", "email", "phone", "card", "merchant", "account"):
                        bad.append(f"{v.name}.{a.name} should belong to vertex '{vnames_lower[vname_lower].name}'")
    return CheckResult(
        id="C7_attribute_placement",
        name="Attributes on the right vertex",
        passed=not bad,
        detail="; ".join(bad),
    )


def _check_shared_identifier_incoming(schema: Schema) -> CheckResult:
    """Every shared-identifier vertex should have ≥1 incoming edge from somewhere."""
    shared_names = {"Device", "IPAddress", "Email", "Phone", "Address", "Card"}
    incoming: dict[str, int] = {}
    for e in schema.edges:
        incoming[e.to_vertex] = incoming.get(e.to_vertex, 0) + 1
    bad = []
    for v in schema.vertices:
        if v.name in shared_names and incoming.get(v.name, 0) == 0:
            bad.append(v.name)
    return CheckResult(
        id="C8_shared_identifier_incoming",
        name="Shared-identifier vertices have incoming edges",
        passed=not bad,
        detail="" if not bad else f"No incoming edges: {bad}",
    )


def validate(schema: Schema) -> ValidationResult:
    checks: list[CheckResult] = []
    checks.append(_check_primary_ids(schema))
    checks.append(_check_edge_endpoints(schema))
    checks.append(_check_unique_names(schema))
    checks.append(_check_reverse_edges_for_multi_hop(schema))
    checks.append(_check_event_vertex_attrs(schema))
    tq_check, answerable, unanswerable = _check_target_questions(schema)
    checks.append(tq_check)
    checks.append(_check_attribute_placement(schema))
    checks.append(_check_shared_identifier_incoming(schema))

    structural_warnings = [c.detail for c in checks if not c.passed and c.detail]
    passed = all(c.passed for c in checks)
    return ValidationResult(
        passed=passed,
        checks=checks,
        answerable_questions=answerable,
        unanswerable_questions=unanswerable,
        structural_warnings=structural_warnings,
    )
