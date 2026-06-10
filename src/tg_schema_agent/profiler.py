from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from tg_schema_agent.config import load_rules_config
from tg_schema_agent.enums import Cardinality, DataKind, PIIClass
from tg_schema_agent.io_utils import detect_delimiter, load_csv
from tg_schema_agent.models import (
    ColumnProfile,
    ColumnRef,
    ForeignKey,
    TableProfile,
)

_DATETIME_HINTS = ("date", "time", "_at", "_ts")
_AMOUNT_HINTS = ("amount", "amt", "value", "price", "total", "balance")
_ENTITY_GROUPS = (
    "customer",
    "account",
    "card",
    "transaction",
    "merchant",
    "device",
    "ip",
    "email",
    "phone",
    "address",
    "user",
    "item",
)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.lower())


def _infer_dtype(series: pd.Series, col_name: str) -> DataKind:
    s = series.dropna()
    if s.empty:
        return DataKind.STRING

    sample = s.astype(str).head(200)
    name_l = col_name.lower()

    if any(h in name_l for h in _DATETIME_HINTS) or name_l in {"dob", "unix_time"}:
        try:
            pd.to_datetime(sample, errors="raise", format="mixed")
            return DataKind.DATETIME
        except (ValueError, TypeError):
            pass

    # Bool-like
    uniq = set(sample.str.strip().str.lower().unique())
    if uniq.issubset({"0", "1", "true", "false", "yes", "no", "y", "n", "t", "f"}):
        # Numeric 0/1 is also INT; treat as BOOL only if column name suggests a flag
        if any(h in name_l for h in ("is_", "has_", "flag", "fraud")):
            return DataKind.BOOL

    # Numeric
    try:
        nums = pd.to_numeric(sample, errors="raise")
        if (nums % 1 == 0).all():
            return DataKind.INT
        return DataKind.FLOAT
    except (ValueError, TypeError):
        pass

    # ID-like: name ends with _id / _num / _no AND values look like IDs
    if re.search(r"(_id|_num|_no|num)$", name_l) or name_l.endswith("id"):
        return DataKind.ID_LIKE

    # Categorical heuristic: low distinct count
    distinct_ratio = s.nunique() / max(len(s), 1)
    if distinct_ratio < 0.02 and s.nunique() <= 50:
        return DataKind.CATEGORICAL

    return DataKind.STRING


def _classify_cardinality(distinct: int, total: int) -> Cardinality:
    cfg = load_rules_config()["cardinality_thresholds"]
    ratio = distinct / total if total else 0
    if ratio >= cfg["unique_ratio"]:
        return Cardinality.UNIQUE
    if ratio >= cfg["high_ratio"]:
        return Cardinality.HIGH
    if ratio >= cfg["medium_ratio"]:
        return Cardinality.MEDIUM
    return Cardinality.LOW


def _name_pattern_hits(col_name: str) -> list[str]:
    cfg = load_rules_config()["name_patterns"]
    name_l = _norm(col_name)
    hits: list[str] = []
    for group, patterns in cfg.items():
        for pat in patterns:
            if pat == name_l or pat in name_l.split("_"):
                hits.append(group)
                break
    return sorted(set(hits))


def _classify_pii(col_name: str) -> PIIClass:
    cfg = load_rules_config()["pii_patterns"]
    name_l = _norm(col_name)
    for cls, patterns in cfg.items():
        for pat in patterns:
            if pat == name_l or pat in name_l.split("_"):
                try:
                    return PIIClass(cls)
                except ValueError:
                    return PIIClass.NONE
    return PIIClass.NONE


def _detect_primary_key(df: pd.DataFrame, columns: list[ColumnProfile]) -> list[str] | None:
    # Single-column PK: unique, no nulls, name ends in _id/_num
    for col in columns:
        if col.cardinality == Cardinality.UNIQUE and col.null_pct == 0.0:
            name_l = _norm(col.name)
            if re.search(r"(_id|_num|primary_id|primary_key)$", name_l) or name_l == "id":
                return [col.name]
    # Fallback: first unique no-null column
    for col in columns:
        if col.cardinality == Cardinality.UNIQUE and col.null_pct == 0.0:
            return [col.name]
    return None


def _detect_foreign_keys(
    columns: list[ColumnProfile], other_tables: list[TableProfile]
) -> list[ForeignKey]:
    fks: list[ForeignKey] = []
    for col in columns:
        name_l = _norm(col.name)
        if not name_l.endswith("_id") and not name_l.endswith("_num"):
            continue
        prefix = re.sub(r"(_id|_num)$", "", name_l)
        if not prefix:
            continue
        for other in other_tables:
            other_norm = _norm(other.name)
            if prefix == other_norm or prefix == other_norm.rstrip("s"):
                other_pk = other.primary_key[0] if other.primary_key else None
                if other_pk:
                    fks.append(
                        ForeignKey(
                            column=col.name,
                            references=ColumnRef(table=other.name, column=other_pk),
                        )
                    )
                break
    return fks


def _has_event_signature(columns: list[ColumnProfile]) -> bool:
    has_id = any(c.is_primary_key_candidate for c in columns)
    has_amount = any(
        any(h in _norm(c.name) for h in _AMOUNT_HINTS) and c.dtype in (DataKind.FLOAT, DataKind.INT)
        for c in columns
    )
    has_timestamp = any(c.dtype == DataKind.DATETIME for c in columns)
    return has_id and has_amount and has_timestamp


def _has_join_signature(columns: list[ColumnProfile]) -> bool:
    fk_like = [c for c in columns if c.is_foreign_key_candidate]
    payload = [
        c
        for c in columns
        if not c.is_foreign_key_candidate
        and not c.is_primary_key_candidate
        and c.dtype not in (DataKind.DATETIME,)
    ]
    return len(fk_like) == 2 and len(payload) <= 1


def _detect_wide_denormalized(columns: list[ColumnProfile]) -> bool:
    """Wide-denormalized: one table containing column-groups for >=3 distinct entity groups."""
    groups_present: set[str] = set()
    for c in columns:
        for hit in c.name_pattern_hits:
            if hit in _ENTITY_GROUPS:
                groups_present.add(hit)
    return len(groups_present) >= 3


def profile_csv(path: Path, name: str | None = None) -> TableProfile:
    df = load_csv(path)
    delim = detect_delimiter(path)
    table_name = name or path.stem
    row_count = len(df)

    columns: list[ColumnProfile] = []
    for col in df.columns:
        series = df[col]
        distinct = int(series.nunique(dropna=True))
        null_pct = float(series.isna().sum() / max(row_count, 1))
        dtype = _infer_dtype(series, col)
        cardinality = _classify_cardinality(distinct, row_count)
        hits = _name_pattern_hits(col)
        pii = _classify_pii(col)
        sample_values = [str(v) for v in series.dropna().astype(str).head(5).tolist()]
        cp = ColumnProfile(
            name=col,
            dtype=dtype,
            null_pct=null_pct,
            distinct_count=distinct,
            row_count=row_count,
            cardinality=cardinality,
            name_pattern_hits=hits,
            pii_class=pii,
            sample_values=sample_values,
        )
        columns.append(cp)

    # PK detection
    pk = _detect_primary_key(df, columns)
    if pk:
        for c in columns:
            if c.name in pk:
                c.is_primary_key_candidate = True

    # Mark FK-candidate columns by name shape (table-level FK linking happens in profile_directory)
    for c in columns:
        name_l = _norm(c.name)
        if (name_l.endswith("_id") or name_l.endswith("_num")) and not c.is_primary_key_candidate:
            c.is_foreign_key_candidate = True

    return TableProfile(
        name=table_name,
        row_count=row_count,
        columns=columns,
        primary_key=pk,
        foreign_keys=[],  # resolved in profile_directory
        has_event_signature=_has_event_signature(columns),
        has_join_signature=_has_join_signature(columns),
        is_wide_denormalized=_detect_wide_denormalized(columns),
        detected_delimiter=delim,
    )


def profile_directory(directory: Path) -> list[TableProfile]:
    csv_files = sorted(directory.glob("*.csv"))
    profiles = [profile_csv(p) for p in csv_files]
    # Resolve FK references now that all PKs are known
    for prof in profiles:
        others = [p for p in profiles if p.name != prof.name]
        prof.foreign_keys = _detect_foreign_keys(prof.columns, others)
        # Promote columns referenced as FK
        ref_cols = {fk.column for fk in prof.foreign_keys}
        for c in prof.columns:
            if c.name in ref_cols:
                c.is_foreign_key_candidate = True
    return profiles
