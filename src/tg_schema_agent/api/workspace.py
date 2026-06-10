"""Workspace storage — one directory per session.

Workspaces live under WORKSPACE_ROOT (env TG_SCHEMA_WORKSPACE_ROOT, default
``./build/workspaces``). Each gets a uuid4 id and contains uploaded CSVs plus
all generated artifacts.

Kept deliberately filesystem-based so the same agent the CLI uses also serves
the API — no database, no extra moving parts. A future Savanna integration can
swap this for object storage by replacing this module.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(
    os.environ.get("TG_SCHEMA_WORKSPACE_ROOT", "build/workspaces")
).resolve()


def _root() -> Path:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return WORKSPACE_ROOT


def create_workspace() -> str:
    wid = uuid.uuid4().hex[:12]
    (workspace_dir(wid)).mkdir(parents=True, exist_ok=True)
    return wid


def workspace_dir(workspace_id: str) -> Path:
    return _root() / workspace_id


def assert_workspace(workspace_id: str) -> Path:
    d = workspace_dir(workspace_id)
    if not d.is_dir():
        raise FileNotFoundError(f"Workspace not found: {workspace_id}")
    return d


def list_csv_files(workspace_id: str) -> list[str]:
    d = assert_workspace(workspace_id)
    return [p.name for p in sorted(d.glob("*.csv"))]


def save_upload(workspace_id: str, filename: str, content: bytes) -> Path:
    d = assert_workspace(workspace_id)
    # Sanitize filename — strip path components
    safe = Path(filename).name
    out = d / safe
    out.write_bytes(content)
    return out


def delete_workspace(workspace_id: str) -> None:
    d = workspace_dir(workspace_id)
    if d.exists():
        shutil.rmtree(d)


def chat_history_path(workspace_id: str) -> Path:
    return assert_workspace(workspace_id) / "chat.json"


def load_chat_history(workspace_id: str) -> list[dict[str, Any]]:
    p = chat_history_path(workspace_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_chat_history(workspace_id: str, messages: list[dict[str, Any]]) -> None:
    p = chat_history_path(workspace_id)
    p.write_text(json.dumps(messages, indent=2, default=str), encoding="utf-8")


def append_chat_message(
    workspace_id: str,
    role: str,
    content: str,
    *,
    type: str = "answer",  # noqa: A002 — domain term
    schema_json: dict[str, Any] | None = None,
    suggested_replies: list[str] | None = None,
) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "role": role,
        "content": content,
        "type": type,
        "timestamp": datetime.utcnow().isoformat(),
    }
    if schema_json is not None:
        msg["schema_json"] = schema_json
    if suggested_replies:
        msg["suggested_replies"] = suggested_replies
    history = load_chat_history(workspace_id)
    history.append(msg)
    save_chat_history(workspace_id, history)
    return msg


def clear_chat_history(workspace_id: str) -> None:
    p = chat_history_path(workspace_id)
    if p.exists():
        p.unlink()


def workspace_state(workspace_id: str) -> dict[str, bool]:
    d = assert_workspace(workspace_id)
    return {
        "profiles_ready": (d / "profile.json").exists(),
        "schema_ready": (d / "schema.json").exists(),
        "validation_ready": (d / "validation.json").exists(),
        "score_ready": (d / "score.json").exists(),
        "gsql_ready": (d / "schema.gsql").exists(),
        "markdown_ready": (d / "schema.md").exists(),
        "deployed": (d / "deploy_report.json").exists(),
    }
