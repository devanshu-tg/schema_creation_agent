"""Deterministic rule engine (PDF page 13 + extensions).

Each rule is a pure function that consumes table profiles and emits RuleRecommendations.
RuleRecommendations are consumed by the Designer to build a Schema.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from tg_schema_agent.enums import Cardinality, DataKind
from tg_schema_agent.models import (
    ColumnProfile,
    RuleRecommendation,
    TableProfile,
)

SHARED_IDENTIFIER_GROUPS = {"device", "ip", "email", "phone", "card", "ssn", "address"}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.lower())


def _matches_group(col: ColumnProfile, group: str) -> bool:
    return group in col.name_pattern_hits


# -------------------- Rule implementations --------------------


def r1_shared_identifier(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """Promote columns that look like shared identifiers to standalone Vertices."""
    recs: list[RuleRecommendation] = []
    # Index: which tables does each shared-identifier group appear in?
    group_tables: dict[str, list[tuple[TableProfile, ColumnProfile]]] = {}
    for tbl in profiles:
        for col in tbl.columns:
            for hit in col.name_pattern_hits:
                if hit in SHARED_IDENTIFIER_GROUPS:
                    group_tables.setdefault(hit, []).append((tbl, col))

    for group, hits in group_tables.items():
        if not hits:
            continue
        multi_table = len({t.name for t, _ in hits}) >= 2
        for tbl, col in hits:
            sub_row = col.distinct_count < col.row_count
            if multi_table or sub_row or col.cardinality in (Cardinality.LOW, Cardinality.MEDIUM):
                recs.append(
                    RuleRecommendation(
                        rule_id="R1_shared_identifier",
                        action="promote_vertex",
                        target=col.name,
                        rationale=(
                            f"Column '{col.name}' matches shared-identifier group "
                            f"'{group}' (distinct={col.distinct_count}, "
                            f"rows={col.row_count}, multi_table={multi_table})."
                        ),
                        metadata={"group": group, "source_table": tbl.name},
                    )
                )
    return recs


def r2_event_vertex(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """Tables with event signature (id + amount + timestamp) become event Vertices."""
    recs: list[RuleRecommendation] = []
    for tbl in profiles:
        if tbl.has_event_signature:
            recs.append(
                RuleRecommendation(
                    rule_id="R2_event_vertex",
                    action="promote_event_vertex",
                    target=tbl.name,
                    rationale=(
                        f"Table '{tbl.name}' has event signature "
                        "(primary id + amount + timestamp); model as event vertex."
                    ),
                    metadata={"source_table": tbl.name},
                )
            )
    return recs


def r3_transfer_edge(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """source_X_id + target_X_id pattern creates an X_TO_X transfer Edge."""
    recs: list[RuleRecommendation] = []
    for tbl in profiles:
        sources = {}
        targets = {}
        for col in tbl.columns:
            n = _norm(col.name)
            m_src = re.match(r"(?:source|src|from)_(.+?)(_id|_num)?$", n)
            m_tgt = re.match(r"(?:target|tgt|dest|to)_(.+?)(_id|_num)?$", n)
            if m_src:
                sources[m_src.group(1)] = col.name
            if m_tgt:
                targets[m_tgt.group(1)] = col.name
        for prefix, src_col in sources.items():
            if prefix in targets:
                recs.append(
                    RuleRecommendation(
                        rule_id="R3_transfer_edge",
                        action="create_transfer_edge",
                        target=f"{prefix}_TO_{prefix}",
                        rationale=(
                            f"Table '{tbl.name}' has '{src_col}' + '{targets[prefix]}' "
                            f"forming a directed transfer between two '{prefix}' instances."
                        ),
                        metadata={
                            "source_table": tbl.name,
                            "from_column": src_col,
                            "to_column": targets[prefix],
                            "entity": prefix,
                        },
                    )
                )
    return recs


def r4_low_card_attribute(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """Low-cardinality non-shared columns stay as attributes."""
    recs: list[RuleRecommendation] = []
    for tbl in profiles:
        for col in tbl.columns:
            if col.cardinality == Cardinality.LOW and not any(
                h in SHARED_IDENTIFIER_GROUPS for h in col.name_pattern_hits
            ):
                recs.append(
                    RuleRecommendation(
                        rule_id="R4_low_card_attribute",
                        action="keep_attribute",
                        target=col.name,
                        rationale=(
                            f"Column '{col.name}' has low cardinality "
                            f"({col.distinct_count}/{col.row_count}) and no "
                            "shared-identifier signal; keep as attribute."
                        ),
                        metadata={"source_table": tbl.name},
                    )
                )
    return recs


def r5_pk_to_vertex(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """A table with a PK that is not a pure join table becomes a Vertex."""
    recs: list[RuleRecommendation] = []
    for tbl in profiles:
        if tbl.primary_key and not tbl.has_join_signature:
            recs.append(
                RuleRecommendation(
                    rule_id="R5_pk_to_vertex",
                    action="promote_vertex",
                    target=tbl.name,
                    rationale=(
                        f"Table '{tbl.name}' has primary key {tbl.primary_key} "
                        "and is not a pure join table; promote to vertex."
                    ),
                    metadata={"source_table": tbl.name, "primary_key": tbl.primary_key},
                )
            )
    return recs


def r6_fk_to_edge(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """An FK between two vertex tables (no event payload) becomes an Edge."""
    recs: list[RuleRecommendation] = []
    for tbl in profiles:
        if tbl.has_event_signature:
            # Event tables emit event-vertex edges instead, not flat FK edges
            continue
        for fk in tbl.foreign_keys:
            recs.append(
                RuleRecommendation(
                    rule_id="R6_fk_to_edge",
                    action="create_edge",
                    target=f"{tbl.name}_{fk.column}_{fk.references.table}",
                    rationale=(
                        f"FK {tbl.name}.{fk.column} -> "
                        f"{fk.references.table}.{fk.references.column} "
                        "between two non-event tables; create edge."
                    ),
                    metadata={
                        "source_table": tbl.name,
                        "from_column": fk.column,
                        "to_table": fk.references.table,
                        "to_column": fk.references.column,
                    },
                )
            )
    return recs


def r7_reverse_edge(_profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """Handled later in the Designer using pattern target_questions; emit no recs here."""
    return []


def r8_event_attrs(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """Event vertices should carry amount/timestamp/status/channel when present."""
    recs: list[RuleRecommendation] = []
    for tbl in profiles:
        if not tbl.has_event_signature:
            continue
        for col in tbl.columns:
            n = _norm(col.name)
            if (
                col.dtype in (DataKind.FLOAT, DataKind.INT)
                and any(h in n for h in ("amount", "amt", "value", "total"))
            ) or col.dtype == DataKind.DATETIME or "status" in n or "channel" in n:
                recs.append(
                    RuleRecommendation(
                        rule_id="R8_event_attrs",
                        action="attach_attribute",
                        target=col.name,
                        rationale=(
                            f"Event vertex should carry '{col.name}' "
                            "(amount/timestamp/status/channel)."
                        ),
                        metadata={"source_table": tbl.name},
                    )
                )
    return recs


def r9_pii_tag(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """Tag PII columns."""
    recs: list[RuleRecommendation] = []
    for tbl in profiles:
        for col in tbl.columns:
            if col.pii_class.value != "NONE":
                recs.append(
                    RuleRecommendation(
                        rule_id="R9_pii_tag",
                        action="tag_pii",
                        target=col.name,
                        rationale=(
                            f"Column '{col.name}' classified as PII type "
                            f"{col.pii_class.value}."
                        ),
                        metadata={
                            "source_table": tbl.name,
                            "pii_class": col.pii_class.value,
                        },
                    )
                )
    return recs


def r10_high_card_no_value(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """UNIQUE columns that are not PK or FK are likely noisy."""
    recs: list[RuleRecommendation] = []
    for tbl in profiles:
        for col in tbl.columns:
            if (
                col.cardinality == Cardinality.UNIQUE
                and not col.is_primary_key_candidate
                and not col.is_foreign_key_candidate
            ):
                recs.append(
                    RuleRecommendation(
                        rule_id="R10_high_card_no_value",
                        action="warn",
                        target=col.name,
                        rationale=(
                            f"Column '{col.name}' is unique but not a PK or FK; "
                            "likely noisy — consider dropping."
                        ),
                        metadata={"source_table": tbl.name},
                    )
                )
    return recs


def r11_wide_table_decompose(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    """Wide-denormalized tables: emit a decomposition recommendation per entity group."""
    recs: list[RuleRecommendation] = []
    for tbl in profiles:
        if not tbl.is_wide_denormalized:
            continue
        # Collect column-groups present
        groups: dict[str, list[str]] = {}
        for col in tbl.columns:
            for hit in col.name_pattern_hits:
                groups.setdefault(hit, []).append(col.name)
        for group, cols in groups.items():
            recs.append(
                RuleRecommendation(
                    rule_id="R11_wide_table_decompose",
                    action="decompose_wide_table",
                    target=group,
                    rationale=(
                        f"Wide-denormalized table '{tbl.name}' contains "
                        f"{len(cols)} column(s) for entity group '{group}'; "
                        "extract as separate vertex."
                    ),
                    metadata={
                        "source_table": tbl.name,
                        "entity_group": group,
                        "columns": cols,
                    },
                )
            )
    return recs


# -------------------- Aggregator --------------------


_ALL_RULES = [
    r1_shared_identifier,
    r2_event_vertex,
    r3_transfer_edge,
    r4_low_card_attribute,
    r5_pk_to_vertex,
    r6_fk_to_edge,
    r7_reverse_edge,
    r8_event_attrs,
    r9_pii_tag,
    r10_high_card_no_value,
    r11_wide_table_decompose,
]


def run_all(profiles: list[TableProfile]) -> list[RuleRecommendation]:
    out: list[RuleRecommendation] = []
    for rule in _ALL_RULES:
        out.extend(rule(profiles))
    return out


def by_rule(recs: Iterable[RuleRecommendation]) -> dict[str, list[RuleRecommendation]]:
    grouped: dict[str, list[RuleRecommendation]] = {}
    for r in recs:
        grouped.setdefault(r.rule_id, []).append(r)
    return grouped
