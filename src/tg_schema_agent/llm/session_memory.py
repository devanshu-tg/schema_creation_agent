"""Cross-turn session memory for the conversational agent.

THE PROBLEM THIS SOLVES
-----------------------
Each chat turn spins up a fresh agent loop (`run_agentic_turn`). When the
turn ends, only the user's text and the agent's final text are persisted to
chat history — every tool call and tool result is thrown away. So next turn
the agent is replayed a flat transcript of *words* with no memory of what it
actually DID: which tables it listed, which columns it analyzed, which
pattern it matched, what business context it recorded, or even what
questions it already asked the user.

That amnesia is the root cause of the "it's confused and asks the same
question every time" complaint. Claude Code + MCP doesn't have this problem
because it keeps its ENTIRE tool trace in one continuous context window for
the whole session — it can always see "I already ran list_tables and found
7 tables," so it never restarts from scratch.

THE FIX
-------
This module persists a compact, structured memory of what the agent has
ALREADY established this session: the decision, business context, tables
seen, pattern hypothesis, the questions already asked, current schema shape,
and a rolling list of key findings. `render_for_prompt()` turns that into a
"SESSION MEMORY" block that the agent loop injects at the top of every turn
— giving the agent the same continuity Claude Code gets for free, without
replaying every raw tool result (which would bloat the context window).

It is deliberately defensive: every method swallows its own errors so a
memory hiccup can never crash a chat turn.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MEMORY_FILENAME = "session_memory.json"
_MAX_FINDINGS = 50  # keep most-recent N findings — bounds the prompt size
_MAX_QUESTIONS = 25
_SUMMARY_TRUNC = 200

# Tools whose successful results are worth remembering as a finding. Noisy,
# low-signal reads (e.g. get_sample_rows) are intentionally excluded so the
# memory block stays focused on decisions and discoveries.
_FINDING_TOOLS = {
    "run_deterministic_rules",
    "summarize_discovery",
    "find_columns_matching",
    "propose_vertex",
    "propose_edge",
    "remove_vertex",
    "remove_edge",
    "record_assumption",
    "validate_schema",
    "score_schema",
    "finalize_schema",
    "deploy_schema_live",
    "load_data_live",
    "write_and_install_query_live",
    "install_query_live",
    "run_query_live",
    "generate_starter_queries_live",
    "get_graph_state_live",
    "drop_graph_data_live",
    "wipe_graph_live",
}


@dataclass
class SessionMemory:
    """Compact, persisted record of session progress (see module docstring)."""

    decision: str = ""
    business_context: dict[str, Any] = field(default_factory=dict)
    tables_seen: list[str] = field(default_factory=list)
    questions_asked: list[str] = field(default_factory=list)
    pattern_hypothesis: str = ""
    schema_summary: str = ""
    findings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, workspace_dir: Path | str) -> "SessionMemory":
        p = Path(workspace_dir) / _MEMORY_FILENAME
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — corrupt file → start fresh
            return cls()
        m = cls()
        for key in ("decision", "pattern_hypothesis", "schema_summary"):
            if isinstance(data.get(key), str):
                setattr(m, key, data[key])
        if isinstance(data.get("business_context"), dict):
            m.business_context = data["business_context"]
        for key in ("tables_seen", "questions_asked", "findings"):
            val = data.get(key)
            if isinstance(val, list):
                setattr(m, key, [str(x) for x in val])
        return m

    def save(self, workspace_dir: Path | str) -> None:
        try:
            (Path(workspace_dir) / _MEMORY_FILENAME).write_text(
                json.dumps(asdict(self), indent=2, default=str), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("session memory save failed: %s", exc)

    @staticmethod
    def clear(workspace_dir: Path | str) -> None:
        """Delete the memory file — call this when chat history is reset."""
        try:
            p = Path(workspace_dir) / _MEMORY_FILENAME
            if p.exists():
                p.unlink()
        except Exception as exc:  # noqa: BLE001
            log.debug("session memory clear failed: %s", exc)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def _add_finding(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        # Dedupe by moving an existing identical finding to the most-recent
        # slot rather than appending a copy (keeps "analyzed X" from piling up).
        if text in self.findings:
            self.findings.remove(text)
        self.findings.append(text)
        if len(self.findings) > _MAX_FINDINGS:
            self.findings = self.findings[-_MAX_FINDINGS:]

    def note_question(self, question: str) -> None:
        q = (question or "").strip()
        if not q or q in self.questions_asked:
            return
        self.questions_asked.append(q)
        if len(self.questions_asked) > _MAX_QUESTIONS:
            self.questions_asked = self.questions_asked[-_MAX_QUESTIONS:]

    def record_tool_event(
        self,
        name: str,
        ok: bool,
        summary: str,
        args: dict[str, Any] | None = None,
    ) -> None:
        """Fold one successful tool call into memory. No-op on failures."""
        if not ok:
            return
        args = args or {}
        s = (summary or "").strip().replace("\n", " ")[:_SUMMARY_TRUNC]

        if name == "record_business_context":
            domain = str(args.get("domain", "")).strip()
            scen = args.get("sub_scenarios") or []
            scen = [str(x) for x in scen] if isinstance(scen, list) else []
            self.business_context = {
                "domain": domain,
                "sub_scenarios": scen,
                "goal_type": str(args.get("goal_type", "")).strip(),
            }
            bits = [b for b in (domain, "/".join(scen[:3])) if b]
            if bits and not self.decision:
                self.decision = " — ".join(bits)
            self._add_finding(f"business context recorded: {s}")
            return

        if name == "list_tables":
            # summary shape: "7 table(s): a; b; c"
            tail = s.split(":", 1)[1] if ":" in s else ""
            for raw in tail.split(";"):
                n = raw.split("(")[0].strip()
                if n and n not in self.tables_seen:
                    self.tables_seen.append(n)
            return

        if name in ("match_all_patterns", "match_pattern_library"):
            self.pattern_hypothesis = s
            self._add_finding(f"pattern match: {s}")
            return

        if name in (
            "analyze_column_distribution",
            "inspect_column",
            "analyze_column_for_promotion",
        ):
            tbl = str(args.get("table", "")).strip()
            col = str(args.get("column", "")).strip()
            label = f"{tbl}.{col}".strip(".")
            self._add_finding(
                f"analyzed {label}: {s}" if label else f"analyzed: {s}"
            )
            return

        if name in _FINDING_TOOLS:
            self._add_finding(f"{name}: {s}")

    def set_schema_summary(self, schema: Any) -> None:
        """Snapshot the current schema shape + back-fill context from it."""
        try:
            nv = len(schema.vertices)
            ne = len(schema.edges)
        except Exception:  # noqa: BLE001
            return
        if nv == 0 and ne == 0:
            self.schema_summary = ""
        else:
            vnames = ", ".join(v.name for v in schema.vertices[:12])
            self.schema_summary = f"{nv} vertices ({vnames}), {ne} edges"

        # If business context was recorded on the schema (and survived via
        # persist_schema) but memory missed it, mirror it here.
        bc = getattr(schema, "business_context", None)
        if bc and not self.business_context:
            try:
                self.business_context = {
                    "domain": bc.domain,
                    "sub_scenarios": list(bc.sub_scenarios),
                    "goal_type": bc.goal_type,
                }
                if bc.domain and not self.decision:
                    self.decision = bc.domain
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def is_empty(self) -> bool:
        return not (
            self.decision
            or self.business_context
            or self.tables_seen
            or self.questions_asked
            or self.pattern_hypothesis
            or self.schema_summary
            or self.findings
        )

    def render_for_prompt(self) -> str:
        """Render the SESSION MEMORY block injected at the top of each turn.

        Returns "" when there's nothing established yet (first turn) so the
        agent isn't handed an empty, confusing block.
        """
        if self.is_empty():
            return ""

        lines = [
            "=== SESSION MEMORY — what you have ALREADY established this "
            "session. Do NOT redo this work or re-ask these questions. ===",
        ]
        if self.decision:
            lines.append(f"Decision the user is making: {self.decision}")
        if self.business_context:
            bc = self.business_context
            ctx_bits = [
                b
                for b in (
                    f"domain={bc.get('domain', '')}" if bc.get("domain") else "",
                    "scenarios="
                    + ", ".join(bc.get("sub_scenarios", []) or [])
                    if bc.get("sub_scenarios")
                    else "",
                    f"goal={bc.get('goal_type', '')}" if bc.get("goal_type") else "",
                )
                if b
            ]
            if ctx_bits:
                lines.append("Business context (already recorded): " + " · ".join(ctx_bits))
        if self.tables_seen:
            lines.append("Tables already discovered: " + ", ".join(self.tables_seen))
        if self.pattern_hypothesis:
            lines.append(f"Pattern hypothesis (already determined): {self.pattern_hypothesis}")
        if self.schema_summary:
            lines.append(f"Current working schema: {self.schema_summary}")
        if self.questions_asked:
            lines.append(
                "Questions you ALREADY asked the user — NEVER ask these "
                "again; build on their answers in the conversation:"
            )
            for q in self.questions_asked[-8:]:
                lines.append(f"  - {q}")
        if self.findings:
            lines.append("Key findings so far:")
            for finding in self.findings[-18:]:
                lines.append(f"  - {finding}")
        lines.append(
            "CONTINUE from where you left off. If a step above is already "
            "done, move to the NEXT step — do not restart from Stage 1."
        )
        return "\n".join(lines)
