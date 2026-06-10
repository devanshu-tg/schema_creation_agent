from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from tg_schema_agent.models import Schema, TableProfile

_DELIM_CANDIDATES = ["|", ",", "\t", ";"]


def detect_delimiter(path: Path, sample_bytes: int = 16384) -> str:
    """Detect delimiter for a CSV-ish file.

    Strategy: count each candidate delimiter in the header line. The header is the
    cleanest signal — it contains only column names, no quoted values with embedded
    commas. csv.Sniffer is unreliable when fields contain literal commas (e.g. a job
    title like "Therapist, drama" in a pipe-delimited file), so we rank by header
    occurrence first and only fall back to Sniffer if the header is ambiguous.
    """
    with path.open("r", encoding="utf-8", errors="replace") as f:
        sample = f.read(sample_bytes)

    if not sample:
        return ","
    header = sample.splitlines()[0]
    counts = {d: header.count(d) for d in _DELIM_CANDIDATES}
    best = max(counts, key=lambda d: counts[d])
    if counts[best] >= 2:
        return best

    # Ambiguous header — fall back to csv.Sniffer over the full sample
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(_DELIM_CANDIDATES))
        return dialect.delimiter
    except csv.Error:
        return ","


def load_csv(path: Path, delimiter: str | None = None) -> pd.DataFrame:
    """Load a CSV with auto-detected delimiter.

    Handles two quirks observed in the user's real fraud CSV:
    - Pipe-delimited despite a .csv extension
    - Trailing empty columns in the header (e.g. ``...trans_num,,``)
    """
    delim = delimiter or detect_delimiter(path)
    df = pd.read_csv(path, sep=delim, dtype=str, keep_default_na=False, na_values=[""])

    # Drop trailing empty/unnamed columns from a trailing delimiter in the header.
    drop_cols = [c for c in df.columns if c == "" or str(c).startswith("Unnamed:")]
    if drop_cols:
        df = df.drop(columns=drop_cols)
    # Strip whitespace + stray trailing commas (real data: header ended with ``,,`` and
    # rows ended with a stray ``,`` despite the delimiter being ``|``).
    df.columns = [str(c).strip().rstrip(",").strip() for c in df.columns]
    for col in df.columns:
        df[col] = df[col].astype("string").str.rstrip(",").str.strip()
    return df


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def dump_yaml(data: Any, path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def dump_schema(schema: Schema, path: Path) -> None:
    path.write_text(
        schema.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
    )


def load_schema(path: Path) -> Schema:
    return Schema.model_validate_json(path.read_text(encoding="utf-8"))


def dump_json(data: Any, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def inputs_hash(profiles: list[TableProfile]) -> str:
    """Stable hash of input profiles for reproducibility tracking."""
    payload = json.dumps(
        [p.model_dump(mode="json") for p in profiles],
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
