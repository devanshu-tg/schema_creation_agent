from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tg_schema_agent.enums import UseCase
from tg_schema_agent.io_utils import load_yaml
from tg_schema_agent.models import (
    ColumnProfile,
    Pattern,
    TableProfile,
    VertexSpec,
)

_DEFAULT_PATTERNS_DIR = Path(__file__).resolve().parent.parent.parent / "patterns"


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.lower())


@dataclass(frozen=True)
class ColumnMatch:
    table: str
    column: str
    confidence: float
    reason: str  # "name_alias" | "name_pattern" | "primary_id" | "canonical_attribute"


@dataclass(frozen=True)
class VertexMatch:
    vertex_name: str
    source_table: str
    source_columns: list[str]
    confidence: float
    column_matches: list[ColumnMatch]


@lru_cache(maxsize=4)
def load_patterns(patterns_dir: Path | None = None) -> dict[UseCase, Pattern]:
    target_dir = Path(patterns_dir) if patterns_dir else _DEFAULT_PATTERNS_DIR
    patterns: dict[UseCase, Pattern] = {}
    for yaml_path in sorted(target_dir.glob("*.yaml")):
        data = load_yaml(yaml_path)
        if not isinstance(data, dict):
            continue
        pattern = Pattern.model_validate(data)
        patterns[pattern.use_case] = pattern
    return patterns


def _matches_alias(col_name: str, aliases: list[str]) -> bool:
    name_n = _norm(col_name)
    for a in aliases:
        a_n = _norm(a)
        if name_n == a_n:
            return True
        # Also allow alias-as-token: column "tran_primary_id" matches alias "tran_primary_id"
        # and column "card_id" matches alias "card"
        if a_n in name_n.split("_"):
            return True
    return False


def _match_vertex_in_table(spec: VertexSpec, table: TableProfile) -> VertexMatch | None:
    """Look for a primary-id column for `spec` inside `table`.

    Returns a VertexMatch when found. Confidence increases when canonical attributes
    are also present.
    """
    # Build alias list = name itself + name_aliases + primary_id token + composed_from
    pid_token = _norm(spec.primary_id).removesuffix("_id").removesuffix("_address")
    aliases = (
        [spec.name, spec.primary_id, pid_token]
        + list(spec.name_aliases)
        + list(spec.composed_from)
    )

    matched_cols: list[ColumnMatch] = []

    # Find a primary-id column
    pid_col: ColumnProfile | None = None
    for col in table.columns:
        if _matches_alias(col.name, aliases):
            pid_col = col
            matched_cols.append(
                ColumnMatch(
                    table=table.name,
                    column=col.name,
                    confidence=0.9,
                    reason="primary_id",
                )
            )
            break

    if pid_col is None and not spec.composed_from:
        return None

    # If composed_from, ALL of those columns must be present
    if spec.composed_from:
        composed_cols = []
        for piece in spec.composed_from:
            for col in table.columns:
                if _norm(col.name) == _norm(piece):
                    composed_cols.append(col.name)
                    matched_cols.append(
                        ColumnMatch(
                            table=table.name,
                            column=col.name,
                            confidence=0.8,
                            reason="composed_from",
                        )
                    )
                    break
        if len(composed_cols) < max(2, len(spec.composed_from) - 1):
            return None
        source_cols = composed_cols
    else:
        source_cols = [pid_col.name] if pid_col else []

    # Canonical-attribute matches (boost confidence)
    for ca in spec.canonical_attributes:
        ca_aliases = [ca.name, *ca.aliases]
        for col in table.columns:
            if _matches_alias(col.name, ca_aliases):
                matched_cols.append(
                    ColumnMatch(
                        table=table.name,
                        column=col.name,
                        confidence=0.6,
                        reason="canonical_attribute",
                    )
                )
                break

    # Confidence: 0.6 base + 0.1 per canonical attr match (capped at 1.0)
    canon_hits = sum(1 for m in matched_cols if m.reason == "canonical_attribute")
    confidence = min(1.0, 0.7 + 0.1 * canon_hits)

    return VertexMatch(
        vertex_name=spec.name,
        source_table=table.name,
        source_columns=source_cols,
        confidence=confidence,
        column_matches=matched_cols,
    )


def match_pattern(profiles: list[TableProfile], use_case: UseCase) -> list[VertexMatch]:
    """For each vertex in the use-case pattern, find the best matching table+column(s)."""
    pattern = load_patterns()[use_case]
    matches: list[VertexMatch] = []
    for vspec in pattern.vertices:
        best: VertexMatch | None = None
        for table in profiles:
            m = _match_vertex_in_table(vspec, table)
            if m and (best is None or m.confidence > best.confidence):
                best = m
        if best:
            matches.append(best)
    return matches
