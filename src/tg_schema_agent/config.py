from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from tg_schema_agent.io_utils import load_yaml

_DEFAULT_RULES_FILE = Path(__file__).resolve().parent.parent.parent / "rules" / "deterministic.yaml"


@lru_cache(maxsize=4)
def load_rules_config(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else _DEFAULT_RULES_FILE
    return load_yaml(target)
