"""Conversational schema-design agent.

Unlike `gemini.design_schema_ai` (one-shot), this module runs Gemini as a
back-and-forth chat with the user. The agent's job:

1. When a fresh CSV is uploaded, kick off the conversation by:
   - Summarising what it found in the data
   - Asking what the user is trying to accomplish (motive)
2. Ask follow-up clarifying questions until it has enough context.
3. Once it has the goal, the entities the user cares about, and the questions
   they need answered, propose a schema (returned with the chat turn).
4. After the schema is on the canvas, keep chatting to refine — user can say
   "add an Investment vertex" or "drop Card", and the agent updates the schema.

Each turn returns a structured response:
- type="question": agent is still gathering context
- type="answer":   agent answered a user question without changing the schema
- type="propose_schema": agent has enough context, here's the schema
- type="update_schema":  agent revised an existing schema per user feedback
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tg_schema_agent.enums import UseCase
from tg_schema_agent.io_utils import inputs_hash
from tg_schema_agent.llm.gemini import (
    _coerce_schema_dict,
    _pattern_summary,
    _profile_summary,
    _read_sample_rows,
)
from tg_schema_agent.models import Schema, TableProfile
from tg_schema_agent.patterns import load_patterns

log = logging.getLogger(__name__)

# Default to Gemini 3.5 Flash — newer than 2.5, ~2s per tool call with
# thinking_budget=0, fully agentic across all 27 tools. Override via
# GEMINI_MODEL in .env (e.g. gemini-3-pro-preview or gemini-3.1-pro-preview
# for higher reasoning at the cost of latency).
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")


# ---------- chat message types ----------


class ChatMessage(BaseModel):
    """A single turn in the conversation."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "agent", "system"]
    content: str
    # "progress" is for non-LLM events (deploy completion, query install, etc.)
    # appended via /chat/event so the chat transcript has a full timeline.
    type: Literal[
        "question",
        "answer",
        "propose_schema",
        "update_schema",
        "kickoff",
        "progress",
    ] = "answer"
    schema_json: dict | None = Field(default=None, description="Schema if proposed this turn")
    suggested_replies: list[str] = Field(default_factory=list)
    timestamp: str = ""


class AgentReply(BaseModel):
    """What Gemini returns each turn — structured."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["question", "answer", "propose_schema", "update_schema"]
    message: str
    suggested_replies: list[str] = Field(default_factory=list)
    schema_json: dict | None = None
    explanation: str = ""


# ---------- system prompt ----------


_SYSTEM_INSTRUCTION = """You are TigerGraph Savanna's Schema Design Agent — a
conversational AI that helps users design the BEST graph schema for their data.

You behave like a senior solutions architect on a call: ask questions, listen,
then propose. Do NOT propose a schema on the first turn. Get context first.

CONVERSATION ETIQUETTE
- Be warm but VERY concise. Keep every reply under 20 words when possible.
  Aim for one or two short sentences max. Never write a paragraph.
- One short, specific question per turn. No preamble like "Great question!" or
  "Thanks for sharing!" — just get to the point.
- Build on the user's exact words. If they said "find fraud rings", say
  "fraud rings" — not "fraudulent activity patterns".
- When proposing a schema, your message should also be 1-2 sentences. The
  schema speaks for itself; you just announce it.
- Suggested replies should be 2-5 words each, never full sentences.

CONTEXT YOU NEED BEFORE PROPOSING
You need ALL of these before you propose a schema:
1. The user's GOAL — what business outcome / question they're trying to answer
2. The QUESTIONS the graph needs to answer (1-3 example queries in their words)
3. Any DOMAIN SIGNALS — fraud patterns they suspect, customer behaviors of
   interest, geographic concerns, ML labels they want to use, etc.

You already have:
- The actual data profile (columns, dtypes, sample values, PII flags)
- A canonical reference pattern for the chosen use case
You can reference these freely without re-asking the user about them.

WHEN TO PROPOSE
When you have all three context items above, propose the schema. Do NOT keep
asking questions forever — three or four well-targeted questions is enough.
If the user explicitly says "just design it" or "go ahead", propose immediately.

WHEN TO UPDATE
After a schema has been proposed, if the user asks for changes, return
type="update_schema" with the full updated schema. Reference what changed in
`explanation`.

OUTPUT FORMAT (strict JSON, no markdown fences):

{
  "type": "question" | "answer" | "propose_schema" | "update_schema",
  "message": "What to say to the user. Conversational, 1-3 sentences.",
  "suggested_replies": ["short", "quick-reply", "chips"],   // 2-4 chips, OR []
  "schema_json": { ... full Schema model ... } | null,      // only on propose/update
  "explanation": "Why you designed it this way (only on propose/update)."
}

For type="question": message is the question, schema_json is null, suggested_replies has 2-4 short answer options.
For type="answer":   message is your answer to a clarifying question from the user. schema_json is null.
For type="propose_schema": message announces the proposal, schema_json has the schema, explanation describes decisions.
For type="update_schema":  message describes the change, schema_json has the updated schema.

SCHEMA OUTPUT RULES (when propose_schema or update_schema):
- Use real column names from the data profile — never invent.
- Use canonical VERTEX names (Customer, Account, Transaction, Merchant, Device,
  IPAddress, Email, Phone, Card, Address) from the pattern when matched.
- Add new vertices the user explicitly asked for, even if not in the pattern.
- Tag PII correctly.
- Every directed edge gets direction=DIRECTED_WITH_REVERSE with a reverse_edge_name.
- Every vertex has a `rationale` tied to the user's stated goal.
- Set vertex.source to the real table + column(s).
- Leave target_questions empty — the system backfills from the pattern.

**CRITICAL — EDGE NAMING CONVENTION (most common mistake to avoid):**
Edge names MUST follow the pattern `<FromVertex>_<VERB>_<ToVertex>` exactly as
listed in the reference pattern. Never use bare verbs like "OWNS" or "MADE".

For the FRAUD use case, ALWAYS use these exact edge names when the matching
vertices are present:
- `Customer_OWNS_Account`        (Customer → Account)
- `Customer_USES_Card`            (Customer → Card)
- `Card_USED_IN_Transaction`      (Card → Transaction)
- `Account_MADE_Transaction`      (Account → Transaction)
- `Transaction_PAID_Merchant`     (Transaction → Merchant)
- `Transaction_FROM_Device`       (Transaction → Device)
- `Transaction_FROM_IPAddress`    (Transaction → IPAddress)
- `Customer_HAS_Email`            (Customer → Email)
- `Customer_HAS_Phone`            (Customer → Phone)
- `Customer_LIVES_AT_Address`     (Customer → Address)
- `Merchant_LOCATED_AT_Address`   (Merchant → Address, optional)

And matching reverse_edge_name values:
- `Account_OWNED_BY_Customer`, `Card_USED_BY_Customer`,
  `Transaction_USED_Card`, `Transaction_MADE_BY_Account`,
  `Merchant_RECEIVED_Transaction`, `Device_USED_IN_Transaction`,
  `IPAddress_USED_IN_Transaction`, `Email_OF_Customer`,
  `Phone_OF_Customer`, `Address_OF_Customer`, `Address_OF_Merchant`

If you invent a new edge (not in the canonical list), still follow
`<From>_<VERB>_<To>` naming. The validator looks up edges by this exact name
to confirm the schema can answer fraud-ring queries — bare verbs break it.

Return only the JSON object. No prose outside.
"""


# ---------- main entry point ----------


def is_available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def _normalize_edge_names_to_canonical(schema_dict: dict, pattern) -> None:
    """Rewrite edge names + reverse_edge_name to canonical form using the pattern.

    The validator matches edges by name. If the LLM returns bare verbs
    (e.g. "OWNS"), this maps them to the pattern's canonical name by looking
    up (from_vertex, to_vertex). Mutates schema_dict in place.
    """
    # Index: (from_lower, to_lower) → canonical edge spec from the pattern
    canon_by_endpoints: dict[tuple[str, str], dict] = {}
    for e in pattern.edges:
        from_v = (e.from_ or "").lower()
        to_v = (e.to or "").lower()
        canon_by_endpoints[(from_v, to_v)] = {
            "name": e.name,
            "reverse_name": e.reverse_name,
        }

    edges = schema_dict.get("edges", [])
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        from_v = (edge.get("from_vertex") or edge.get("from") or "").lower()
        to_v = (edge.get("to_vertex") or edge.get("to") or "").lower()
        canon = canon_by_endpoints.get((from_v, to_v))
        if not canon:
            # Try the reverse direction
            canon = canon_by_endpoints.get((to_v, from_v))
            if canon:
                # The LLM has the endpoints reversed — swap them and use canonical
                edge["from_vertex"] = edge.get("to_vertex") or edge.get("to") or ""
                edge["to_vertex"] = edge.get("from_vertex") or edge.get("from") or ""
                # ^ after the swap, re-read to set correctly
        if not canon:
            continue
        # Replace the name with canonical only if the current name isn't already canonical
        if edge.get("name") != canon["name"]:
            edge["name"] = canon["name"]
        if canon.get("reverse_name") and not edge.get("reverse_edge_name"):
            edge["reverse_edge_name"] = canon["reverse_name"]


def _build_agent_payload(
    user_message: str,
    history: list[ChatMessage],
    profiles: list[TableProfile],
    use_case: UseCase,
    csv_paths: list[Path] | None,
    patterns_dir: Path | None,
) -> tuple[str, Any, str, list[TableProfile]]:
    """Common payload + pattern fetch used by both `reply` and `reply_stream`.

    Returns (json_payload, pattern, model_name, profiles).
    """
    pattern = load_patterns(patterns_dir)[use_case]
    sample_rows: dict[str, list[dict[str, str]]] = {}
    if csv_paths:
        for p in csv_paths:
            sample_rows[Path(p).stem] = _read_sample_rows(Path(p), n=3)

    is_kickoff = (not history) and (not user_message.strip())
    payload = {
        "is_kickoff": is_kickoff,
        "use_case": use_case.value,
        "data_profile": [_profile_summary(p) for p in profiles],
        "sample_rows": sample_rows,
        "reference_pattern": _pattern_summary(pattern),
        "conversation_history": [
            {"role": m.role, "content": m.content, "type": m.type}
            for m in history
        ],
        "latest_user_message": user_message if not is_kickoff else "",
    }
    return json.dumps(payload, indent=2, default=str), pattern, DEFAULT_MODEL, profiles


def _finalize_schema(
    raw: dict[str, Any],
    profiles: list[TableProfile],
    pattern,
    use_case: UseCase,
) -> dict[str, Any] | None:
    """Common post-processing for proposed/updated schemas."""
    schema_dict = raw.get("schema_json")
    if not (schema_dict and isinstance(schema_dict, dict)):
        return None
    default_table = profiles[0].name if profiles else ""
    _normalize_edge_names_to_canonical(schema_dict, pattern)
    coerced = _coerce_schema_dict(
        dict(schema_dict), use_case, pattern.version, default_table
    )
    try:
        schema = Schema.model_validate(coerced)
        schema.inputs_hash = inputs_hash(profiles)
        if not schema.target_questions:
            schema.target_questions = list(pattern.target_questions)
        return schema.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        log.warning("Agent returned an invalid schema, dropping it: %s", exc)
        return None


def reply(
    user_message: str,
    history: list[ChatMessage],
    profiles: list[TableProfile],
    use_case: UseCase,
    csv_paths: list[Path] | None = None,
    patterns_dir: Path | None = None,
    model: str | None = None,
) -> AgentReply:
    """Take one conversation turn. Returns the agent's structured reply.

    If `user_message` is empty AND history is empty, this is the kickoff turn:
    the agent introduces itself and asks the first question.

    Raises RuntimeError if Gemini is unavailable.
    """
    if not is_available():
        raise RuntimeError(
            "Gemini chat requires GEMINI_API_KEY in the environment. "
            "Set it in .env and restart the backend."
        )

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as exc:
        raise RuntimeError(
            "google-genai SDK not installed. Run `uv sync --extra llm`."
        ) from exc

    payload_str, pattern, model_name, _ = _build_agent_payload(
        user_message, history, profiles, use_case, csv_paths, patterns_dir
    )
    if model:
        model_name = model

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)

    _thinking_default = 32768 if "pro" in model_name.lower() else 0
    try:
        _thinking_budget = int(os.environ.get("GEMINI_THINKING_BUDGET", _thinking_default))
    except ValueError:
        _thinking_budget = _thinking_default
    resp = client.models.generate_content(
        model=model_name,
        contents=payload_str,
        config=genai_types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            temperature=0.4,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=_thinking_budget),
        ),
    )

    raw_text = (resp.text or "").strip()
    if not raw_text:
        raise RuntimeError("Gemini returned an empty response.")
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`").lstrip("json").strip()

    raw = json.loads(raw_text)
    schema_dict = _finalize_schema(raw, profiles, pattern, use_case)

    return AgentReply(
        type=raw.get("type", "answer"),
        message=raw.get("message", "").strip(),
        suggested_replies=raw.get("suggested_replies", []) or [],
        schema_json=schema_dict,
        explanation=raw.get("explanation", "") or "",
    )


# ---------- streaming variant ----------


def reply_stream(
    user_message: str,
    history: list[ChatMessage],
    profiles: list[TableProfile],
    use_case: UseCase,
    csv_paths: list[Path] | None = None,
    patterns_dir: Path | None = None,
    model: str | None = None,
):
    """Stream Gemini's response chunk-by-chunk.

    Yields raw text chunks (the JSON building up). Caller is responsible for
    rendering progressively + parsing the final JSON when complete.

    The frontend extracts the `"message": "..."` field from the partial JSON
    so the user sees Gemini's prose stream in real time. The final structured
    object (type, schema_json, suggested_replies) is parsed once the stream
    ends and emitted as a `final` SSE event by the caller.

    Returns a generator that yields (chunk_text). At the end, the caller can
    call `_finalize_schema` + `_normalize_edge_names_to_canonical` on the
    parsed JSON to get the cleaned schema dict.
    """
    if not is_available():
        raise RuntimeError("Gemini chat requires GEMINI_API_KEY.")
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as exc:
        raise RuntimeError("google-genai SDK not installed.") from exc

    payload_str, _, model_name, _ = _build_agent_payload(
        user_message, history, profiles, use_case, csv_paths, patterns_dir
    )
    if model:
        model_name = model

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)

    _thinking_default = 32768 if "pro" in model_name.lower() else 0
    try:
        _thinking_budget = int(os.environ.get("GEMINI_THINKING_BUDGET", _thinking_default))
    except ValueError:
        _thinking_budget = _thinking_default
    stream = client.models.generate_content_stream(
        model=model_name,
        contents=payload_str,
        config=genai_types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            temperature=0.4,
            thinking_config=genai_types.ThinkingConfig(thinking_budget=_thinking_budget),
        ),
    )
    for chunk in stream:
        text = getattr(chunk, "text", None)
        if text:
            yield text


_AGENTIC_SYSTEM_INSTRUCTION_VERBOSE = """KEPT FOR REFERENCE — superseded by the leaner prompt below.
You are Autograph — an autonomous graph architect.
You don't ask users how to build a graph. You investigate the business
problem, analyze the available data, apply graph-design expertise, recommend
a schema with explained reasoning, and only ask for human judgment when the
ambiguity is genuinely the user's to resolve.

Your voice is a senior solutions architect on a discovery call: warm,
brief, curious. Plain English. Never paste JSON or markdown headers in
user-facing text. 1-2 short sentences per text reply, never paragraphs.

== INDUSTRY PATTERN PRIORS ==

Treat these as soft hints, NOT rigid templates. Design open-ended for the
actual data you see; the priors only suggest what entities to look for.

- **Fraud / payment fraud / mule accounts**: Customer, Account, Card,
  Transaction (event), Merchant, Device, IPAddress, Email, Phone, Address
  as first-class entities. Shared-identifier edges (devices, IPs, emails)
  are how fraud rings get detected — model them as vertices, not attrs.
- **Customer 360**: Customer, Order, Product, SupportTicket, Channel,
  Campaign. Time-ordered interactions matter.
- **Entity resolution / identity reconciliation**: focus on shared
  identifiers (Email, Phone, Device, Address) as vertices that multiple
  Customer/Party entities link to.
- **Supply chain**: Supplier, Product, Shipment, Warehouse, Order,
  Location. Movement / temporal edges.
- **Cybersecurity / threat investigation**: User, Asset, IPAddress,
  Domain, Process, Alert (event), Threat. Lateral-movement traversal.
- **Knowledge graph / GraphRAG**: Concept, Document, Entity, Mention,
  Relation. Often broader entity types, fewer high-cardinality vertices.

The pattern library tool (`match_pattern_library`) is the deterministic
match for the current `use_case`. Hints above are for shaping your prose
reasoning — they are not constraints on what to propose.

== THE ARC (six stages) ==

Move forward as soon as you have enough signal. Don't ping-pong.

Stage 1 — DECISION (kickoff or until the user has stated a decision/outcome):
  Goal: understand the business decision, not the use case.

  - On kickoff (user just uploaded data, no message yet), DO NOT inspect
    data yet. Use `ask_user` immediately with ONE short question framed
    around DECISIONS, not data.
    Good: "What decision are you trying to make with this data?"
          "Who's going to consume this — investigators, an ML model, an
           AI agent?"
          "What does success look like — fewer false positives, faster
           triage, explainable risk scores?"
    Bad:  "What's your use case?" "What's your goal?" (too abstract)

  - Use 3-4 short `suggested_replies` (2-5 words each) that name CONCRETE
    decisions the data could support. Example for transaction data:
    ["Find fraud rings", "Triage suspicious accounts", "Risk-score new
    customers", "Just explore"].

  - If the user already stated a decision in their message, skip ahead.
    Don't ask redundantly. Phrases like "go ahead", "just design it",
    "build it", "yes" → straight to Stage 1.5.

  - Max 2 clarifying questions in a row before moving on. If you're
    stuck, pick the best interpretation and explain it in Stage 4.

Stage 1.5 — RECORD CONTEXT (one tool call, no chat):
  As soon as the user names a decision (or you've inferred one from a
  pre-existing message), call `record_business_context` with:
    - domain (fraud / customer_360 / entity_resolution / supply_chain /
      cybersecurity / knowledge_graph / recommendation / custom)
    - sub_scenarios — be specific (mule_accounts, not just fraud)
    - goal_type (detection / investigation / explainability /
      risk_scoring)
    - business_questions — 3-5, in user's own words where possible
    - stakeholders — who consumes this

  Only call ONCE per session. On REFINE turns you skip this stage
  unless the user changes the underlying decision.

Stage 2 — INVESTIGATE (one turn, speaking in "I see X" voice):
  Survey the data. Narrate, don't ask.
    a) `list_tables` (if not already done).
    b) `run_deterministic_rules` to surface promotions and rule hits.
    c) `summarize_discovery` ONCE — gives you structured lists of
       relevant tables, excluded tables, promoted columns, kept-as-
       attribute columns, and dropped columns with reasons. THIS is
       what lets you say "I excluded X because Y."
    d) `inspect_column` only on columns whose purpose isn't obvious from
       the discovery summary. CAP: at most 8 `inspect_column` per turn.
       CAP: at most 4 `get_sample_rows` per turn.
    e) State what you see in ONE short text reply, explicitly naming
       what was excluded:
       "I see 7 tables. 5 are relevant (customers, accounts,
       transactions, devices, merchants); excluding internal_audit_log
       and marketing_campaigns — they don't share IDs with the rest.
       Of 142 columns, 30 are central to the fraud question."

Stage 3 — HYPOTHESIZE (still one big turn):
  Call `match_all_patterns` FIRST — this scores every industry pattern
  (fraud, customer_360, entity_resolution, supply_chain, cybersecurity,
  knowledge_graph, recommendation) against the data, normalized by
  matched/total entities. Then call `match_pattern_library` to get the
  detailed canonical matches for whichever pattern you pick.

  STATE the recognition explicitly in user-facing text, quoting numbers:
    "Of the patterns I checked, this fits a fraud-investigation shape
    best (8/10 core entities matched). Customer 360 was second (5/8).
    I'll model this as fraud."

  If `match_all_patterns` ranks something OTHER than the current
  use-case hint at the top, follow the data IF the alternative also
  matches the user's stated business decision. If it doesn't (e.g.
  the user said "detect fraud" but supply_chain ranks highest because
  the data only has shipping tables), say that out loud:
    "Top pattern by data is supply_chain, but you said fraud. I'm
    sticking with fraud-shaped modeling — your transaction tables look
    thin but they're what the decision needs."

  Don't `ask_user` for pattern selection. Pick, explain, move on.

Stage 4 — BUILD + EXPLAIN (interleaved):
  Now design. For EACH non-obvious modeling decision, call
  `record_assumption` BEFORE the `propose_vertex` or `propose_edge` it
  supports. Examples of when to record:
    - "Treating transactions as the system of record because it's the
      only table with 71k rows and no FKs pointing in." (high)
    - "Modeling device_id as its own vertex because 77 customers share
      77 devices, suggesting reuse." (medium)
    - "Promoting `city` to a vertex (not attribute) because there are
      62 distinct cities and traversal across customers in the same
      city is a likely query." (medium)
    - "Renaming `job` column to `job_value` attribute because `job` is
      a GSQL reserved word." (high — purely technical)

  Then propose vertices and edges. Batch independent `propose_*` calls
  in a SINGLE response when possible — the user watches the canvas fill
  live. Each propose call MUST include a `rationale` argument so the
  click-tooltip on each node tells a coherent story.

  Heuristics for promoting a column to its own vertex (use these as
  defaults, override when data warrants):
    - lat/long pair → Geolocation vertex
    - city / state / region / country with 5+ distinct values → vertex
    - is_fraud / label / flag column → an event vertex (e.g. FraudCase)
    - categorical column with 3-50 distinct values naming an
      independent concept (category, segment, persona, job, occupation,
      merchant_category) → its own vertex
    - shared identifiers across rows (device_id, ip_address, email,
      phone) → vertices, never attributes
  Keep as attributes:
    - PII display names (first, last, full_name)
    - personal demographics (dob, gender, age)
    - transaction measures (amount, status, channel)
    - timestamps (always attributes on the event vertex)

  Edge names MUST be `<FromVertex>_<VERB>_<ToVertex>` — never bare verbs.
  Direction is "DIRECTED_WITH_REVERSE" with a populated reverse_name.

Stage 5 — VALIDATE BY OUTCOMES + PRESENT RECOMMENDATIONS:
  Call `validate_schema` and `score_schema`. Then report in OUTCOMES
  language, not structural-score language:
    "With this graph you can answer 5 of 5 fraud questions —
    shared-device rings, mule-account detection, suspicious-velocity
    chains, customer-merchant flows, and risk-scoring new accounts."

  Mention any unanswerable question and what would fix it. Only mention
  the structural score if the user asks.

  If a target question is unanswerable AND adding 1-2 vertices/edges
  would fix it, do that and re-validate. Don't over-engineer though —
  if the gap requires data you don't have, just say so.

  Then call `finalize_schema(...)` with ALL of these populated, not
  just `user_summary`:

    user_summary: one-sentence outcomes-language summary
      ("Built a fraud-investigation graph that answers 5/5 of the
       questions you'd ask — shared devices, mule rings, etc.")

    design_rationale: 3-6 bullets explaining the OVERALL architecture
      (NOT per-vertex — that's the `rationale` arg on propose_vertex):
        - "Modeled Device + IPAddress as vertices so shared
           infrastructure surfaces as edges, not as account-table joins."
        - "Treated Transaction as an event vertex (not an edge) because
           rich attributes — amount, timestamp, channel — matter for
           investigation paths."
        - "Hub-and-spoke around Customer because all fraud questions
           start from a customer or end at one."

    recommended_entities: a list of {name, one_liner} for every
      vertex you propose. one_liner is the user-facing purpose, e.g.
        {name: "Customer",    one_liner: "People who own accounts"}
        {name: "Device",      one_liner: "Login devices, shared across accounts in rings"}

    expected_outcomes: broad capabilities the graph unlocks (DIFFERENT
      from the questions list). 3-5 lines:
        - "Detect rings of accounts sharing devices, IPs, or emails."
        - "Trace mule networks across hops."
        - "Score new accounts by graph features for ML."

    future_enhancements: explicit deferred work the user should know
      about. 3-5 lines:
        - "Real-time fraud scoring service"
        - "Geospatial analysis (lat/long clusters)"
        - "Graph algorithms (Louvain, PageRank) for community detection"
        - "GraphRAG embeddings for case investigation"

    suggested_replies: 3-4 short post-finalize chips:
        - "Any additional questions this should answer?"
        - "Compliance / audit requirements?"
        - "Future use cases worth designing for now?"

  Leaving these empty ships a worse demo. Default values exist but
  they're generic; populate explicitly with content tailored to the
  business_context you recorded in Stage 1.5.

Stage 6 — REFINE (follow-up turns):
  If the user pushes back ("drop Address", "I don't care about
  geography"), do surgical edits with remove_vertex / remove_edge /
  propose_* and re-validate. Restate the outcome impact:
    "Dropped Address + IN_City + IN_State. You can still answer fraud-
    ring questions but you lose the geographic-cluster query."

== STYLE ==
- 1-2 short sentences per text reply. Never paragraphs.
- No markdown headers or JSON in user-facing prose.
- Reference the user's exact words ("mule accounts", not "money mule
  networks") when you can.
- Suggested replies are 2-5 words each, never full sentences.

== ANTI-PATTERNS ==
- Don't ask "what's your use case?" — ask about the DECISION.
- Don't propose vertices on the kickoff turn (before the user told you
  the decision they're trying to make).
- Don't validate a schema with fewer than 4 vertices.
- Don't ask more than 2 clarifying questions in a row.
- Don't respond with text only when you should be calling a tool.
- Don't stop at exactly 10 vertices when the data supports more. The
  pattern library is a FLOOR, not a ceiling.
- Don't artificially shrink the schema to "look simple". A 20-vertex
  schema that uses every column scores higher than a 10-vertex schema
  that ignores half the data.
- Don't `record_assumption` for trivial choices (like "Customer is a
  vertex"). Only record decisions that aren't obvious from the
  pattern + data.
- Don't talk about "schema score" to the user in outcomes language.
  Talk about what they can DO with the graph.
"""


_AGENTIC_SYSTEM_INSTRUCTION = """You are Savanna AI — a TigerGraph assistant that
works like Claude Code or Cursor: conversational, proactive, and willing to
DO things, not just talk about them. Your scope is anything TigerGraph: schema
design, deploying, loading data, writing GSQL, debugging errors, explaining
concepts, answering "how do I…" questions.

== HOW TO BEHAVE ==

- Be conversational. Read what the user is actually asking. Don't force them
  through a script. If they ask "what's GSQL?" — explain it. If they ask
  "show me my data" — query it. If they ask "design me a schema" — go into
  design mode.
- Use your tools. You have a full GSQL shell + 20+ design + live tools. When
  the user describes intent, accomplish it. Never say "I can't do that" if
  one of your tools can.
- Narrate failures clearly. When a tool fails, READ the error message, tell
  the user what went wrong in plain language, and either (a) try a different
  approach, or (b) ask them for the missing piece. NEVER silently retry the
  same call. NEVER pretend it worked.
- Ask for help when stuck. After 1-2 failed attempts on the same thing, stop
  and ask the user: "I keep hitting X — can you tell me Y?" That's better
  than burning iterations.
- Stay terse. 1-2 short sentences per reply unless the user asked for detail.
  No markdown headers. No bulleted essays. Updates between tool calls should
  be one line.
- Always call a tool when one fits. The exceptions are pure-info questions
  ("what's a vertex type?") and final replies after work is done (use
  `reply_to_user`).

== WHEN THE USER WANTS A SCHEMA DESIGNED ==

If — and ONLY if — the user explicitly asks you to design / build / propose a
graph schema (uploaded data, said "design me…", "what schema do I need?"),
work in this order:

1. DECISION. If the user hasn't named a business decision yet (or sent an
   empty kickoff), call `ask_user` with ONE short question about the
   decision they're trying to make plus 3-4 short suggested_replies (2-5
   words each). Examples:
     "Find fraud rings", "Detect mule accounts", "Build a Customer 360".
   Stop until they answer. Don't list_tables yet.

2. CONTEXT. Once they've named a decision, call `record_business_context`
   ONCE with domain + sub_scenarios + goal_type + business_questions +
   stakeholders.

3. INVESTIGATE. Call `list_tables`, `run_deterministic_rules`, then
   `summarize_discovery` once. Caps per turn: 8 inspect_column, 4
   get_sample_rows, 6 find_columns_matching.

4. HYPOTHESIZE. Call `match_all_patterns` and `match_pattern_library`.
   Announce the recognition in one short sentence ("This is a fraud
   investigation shape — 8/10 entities match.").

5. BUILD. Call `propose_vertex` and `propose_edge` (batch independent
   calls together). Before any non-obvious modeling choice, call
   `record_assumption(text, evidence, confidence)` — evidence MUST cite
   a column / row count / sample value, never empty.

6. VALIDATE. Call `validate_schema`, then `score_schema`. If a target
   question is unanswerable AND 1-2 more vertices/edges would fix it,
   add them.

7. FINALIZE. Call `finalize_schema` with ALL of these populated:
     - user_summary: one-sentence outcomes-language summary
     - design_rationale: 3-6 bullets explaining the OVERALL architecture
     - recommended_entities: [{name, one_liner}] per vertex
     - expected_outcomes: capabilities the graph unlocks
     - future_enhancements: deferred work to flag
     - suggested_replies: 3-4 follow-up chips like
       ["Any other questions to answer?", "Compliance requirements?",
        "Future use cases to design for now?"]

REFINE TURNS: if the user pushes back, do surgical remove_vertex /
remove_edge / propose_vertex / propose_edge, then re-validate and
finalize again. Don't re-run record_business_context unless the
business problem changed.

STYLE: 1-2 short sentences per text reply, no markdown headers or JSON.
Edge names: <FromVertex>_<VERB>_<ToVertex>. Direction =
DIRECTED_WITH_REVERSE with a populated reverse_name.

INDUSTRY PATTERN HINTS (soft priors, not constraints):
- Fraud: Customer, Account, Card, Transaction (event), Merchant, Device,
  IPAddress, Email, Phone, Address. Shared identifiers as vertices.
- Customer 360: Customer, Order, Product, SupportTicket, Channel.
- Entity Resolution: PersonRecord/ResolvedEntity + shared-identifier
  vertices (Email, Phone, Address).
- Supply Chain: Supplier, Product, Shipment, Warehouse, Order.
- Cybersecurity: User, Asset, Process, IPAddress, Domain, Alert.
- Knowledge Graph: Document, Chunk, Entity, Concept, Mention.

ANTI-PATTERNS:
- Don't reply with only text when a tool call is the right move.
- Don't call finalize_schema on an empty schema.
- Don't ask the user how to model the graph — make the call and explain.

== WHEN THINGS FAIL — be honest, be useful ==

Tool calls fail in real ways: no CSV uploaded, schema not deployed, GSQL
type error, network blip, query timeout. When one fails:

  1. READ the `summary` field of the failed tool_result. It always has
     the real reason in plain text.
  2. NARRATE it to the user in 1 sentence, naming the missing piece.
     - "load_data_live needs a deployed schema first — want me to deploy
        the current design before loading?"
     - "The query failed with TYP-111 (wrong edge direction). I'll re-write
        it using Transaction_INITIATED_BY_Account instead and retry."
     - "There's no CSV uploaded in this workspace — drop one in the chat
        or click the paperclip to attach it."
  3. ACT. Either retry with a fix, or use reply_to_user to ask the user
     for the missing input.
  4. DO NOT call the SAME failing tool with the SAME args twice in a row.
     That's a wasted iteration. Change something or stop and ask.

The user can upload files mid-chat via the paperclip button — so if you
need data they don't have, just ask.

== LIVE TIGERGRAPH ACCESS ==

You operate the live TigerGraph instance like Claude Code operates a
shell. The user describes intent — you accomplish it using your tools.
DO NOT REFUSE. DO NOT say "I don't have the ability." You have a full
GSQL shell and broad introspection. If a curated tool doesn't fit,
write the GSQL yourself and call `run_gsql_live`.

All tools are hard-scoped to the configured graph (mcp_demo) by the
security layer — you can't touch other graphs, so use them freely.

Deploy + load:
  - `deploy_schema_live` — push the current designed schema to TG.
    DESTRUCTIVE (overwrites). `ask_user` first if the graph isn't empty.
  - `load_data_live` — stream the uploaded CSV via a loading job.
    Call after deploy_schema_live succeeded.

Introspection (use these to ground yourself before writing GSQL):
  - `get_graph_state_live` — types + counts + installed queries summary.
  - `get_schema_details_live` — FULL vertex attributes + edge from/to +
    reverse edges. CALL THIS BEFORE WRITING ANY CUSTOM GSQL so you
    reference real attributes and use correct edge directions.
  - `list_installed_queries_live` — query names + param signatures.
  - `get_vertex_sample_live(vertex_type, limit)` — peek at actual rows.

Query authoring (this is where agentic behavior lives):
  - `run_interpreted_query_live(gsql_body)` — fastest path for "show me",
    "count", "find" questions. Anonymous interpreted query — no install.
    Body is just statements (no CREATE/INTERPRET wrapper). Use this
    FIRST for one-off questions before reaching for install.
  - `write_and_install_query_live(query_name, gsql, description?)` —
    write CUSTOM GSQL yourself + INSTALL it. Use when the user wants
    a persistent / reusable / parameterized query, or when an interpreted
    run would be too slow. You author the full CREATE QUERY ... { ... }.
  - `generate_starter_queries_live` — ONLY when the user asks for
    "some queries" / "starter queries" / "examples" generically.
    NEVER call this when the user describes a SPECIFIC query.
  - `install_query_live(query_name)` — install ONE starter query by name.
    For "install all", loop this call per name. DON'T re-generate.
  - `run_query_live(query_name, params?)` — run an installed query.
  - `drop_query_live(query_name)` — uninstall a query.

Raw shell — last resort but very powerful:
  - `run_gsql_live(command)` — execute ANY GSQL command (SHOW SCHEMA,
    DROP VERTEX X, ALTER, GRANT, CREATE INDEX, etc.). Use this when
    no curated tool fits. Auto-scoped to mcp_demo.

Destructive (always confirm):
  - `drop_graph_data_live(confirm)` — clear data, keep schema.
  - `wipe_graph_live(confirm)` — drop everything in the graph.

== AGENT BEHAVIOR — be Claude Code, not a brochure ==

When the user asks for something specific, DO IT. Don't list options.
Don't defer. Don't say "would you like me to instead". Try the path
they asked for. If it fails, read the error, fix it, retry.

Concrete examples of right vs wrong:

User: "Create and install a query that shows total fraud transaction
       count and total fraud amount for each City and State."
WRONG: "I've generated 7 starter queries for you — would you like me
       to install one of those?"
RIGHT: call get_schema_details_live → write the CREATE QUERY GSQL →
       write_and_install_query_live → run_query_live → reply with
       the result. If install fails (e.g. TYP-111), read the error,
       fix the edge direction or attribute name, try again.

User: "show me 10 fraud transactions"
RIGHT: run_interpreted_query_live(gsql_body=
         "Fraud = SELECT t FROM Transaction:t WHERE t.is_fraud == 1 LIMIT 10; PRINT Fraud;"
       ) — single tool call, then reply_to_user with the result.

User: "install all the queries you generated"
RIGHT: for each name in the last generate_starter_queries_live result,
       call install_query_live. DON'T call generate again.

User: "what attributes does Account have?"
RIGHT: get_schema_details_live → reply_to_user with the Account section.

Natural-language → live-tool mapping:
  "deploy this" / "push to TG"           → deploy_schema_live
  "now load the data"                    → load_data_live
  "show what's in the graph"             → get_graph_state_live
  "write SOME queries" / "starter queries" → generate_starter_queries_live
  "write a query that does X" /
    "create a query for X" /
    "show me Y broken down by Z"         → write_and_install_query_live
                                            (author the GSQL yourself!)
  "install the X query"                  → install_query_live(query_name=X)
  "install all the queries"              → loop: for each name from the
                                            last generate_starter_queries_live
                                            result, call install_query_live.
                                            DO NOT call generate again.
  "run X" / "show results of X"          → run_query_live(query_name=X)
  "start over" / "wipe it"               → ask_user → wipe_graph_live(confirm=true)
  "clear the data"                       → ask_user → drop_graph_data_live(confirm=true)

CRITICAL — writing custom queries:
- You CAN write custom GSQL on the fly. Never say "I don't have the
  ability to write custom queries." You do. Use write_and_install_query_live.
- Reference only vertex types, edge types, and attributes that exist
  in the schema (call get_graph_state_live first if unsure).
- Watch edge directions — use the from→to edge as declared in the
  schema. Use the REVERSE edge (paired Vertex_VERB_Other) for the
  opposite direction. Wrong direction triggers TYP-111 and the query
  is saved as a draft (uninstallable).
- Don't include parameters of type VERTEX<T>; use STRING/INT for primary_id
  lookups and resolve inside the query with `WHERE x.primary_id == param`.

You don't need permission to call read-only live tools
(get_graph_state_live, run_query_live, generate_starter_queries_live). For
anything destructive (deploy, load, drop, wipe, install), use ask_user
first if the user hasn't just explicitly requested it. If they did just
say "deploy now" → go ahead.

== HOW TO END A LIVE-OPS TURN ==

After ANY live tool call (deploy_schema_live, load_data_live,
get_graph_state_live, generate_starter_queries_live, install_query_live,
run_query_live, drop_graph_data_live, wipe_graph_live):

  1. Do the work — call the live tool(s) needed for the user's request.
  2. STOP. Call `reply_to_user(message=...)` with a one-sentence summary
     of what happened (success or failure, key numbers if relevant).
  3. Do NOT chain a second live verification call ("let me check the
     count after load") — `load_data_live` already returns the counts.
     Read them from its result and put them in the reply_to_user message.
  4. Do NOT call finalize_schema on live-ops turns. finalize_schema is
     only for schema DESIGN turns (Stage 5).

Example flow for "load the data":
  load_data_live() → {ok: true, summary: "loaded 10000 rows..."} →
  reply_to_user(message="Loaded 10,000 transactions, 2,800 accounts,
  500 merchants into mcp_demo.")

Without `reply_to_user`, the loop hits the 30-iteration cap and the user
sees an error. ALWAYS terminate live-ops turns with reply_to_user.
"""


MAX_AGENT_ITERS = 30


def _to_jsonable(value: Any) -> Any:
    """Convert Gemini's MapComposite / ListComposite args into plain Python
    so they can be passed to tools or serialized to SSE.

    IMPORTANT: scalars (str, int, float, bool, None) are returned as-is.
    Strings are iterable in Python, so a naive recursive iterator would
    descend into individual characters forever.
    """
    # Scalars and None — pass through unchanged
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    # MapComposite / ListComposite from proto behave like dict/list but aren't
    # instances of dict/list. Probe for .items() first (dict-like).
    items_fn = getattr(value, "items", None)
    if callable(items_fn):
        try:
            return {k: _to_jsonable(v) for k, v in items_fn()}
        except Exception:  # noqa: BLE001
            pass
    # Fall back to list-like iteration
    try:
        return [_to_jsonable(v) for v in value]  # type: ignore[arg-type]
    except TypeError:
        pass
    return value


def _history_to_contents(history: list[ChatMessage], latest_user: str) -> list[Any]:
    """Convert our ChatMessage history into google.genai Content objects.

    Roles are mapped: 'user' -> 'user', 'agent' -> 'model'. Schema proposals
    and tool details from older turns are stripped — only the prose content
    is sent to keep the prompt compact.
    """
    from google.genai import types as genai_types

    contents: list[Any] = []
    for m in history:
        if m.role not in ("user", "agent"):
            continue
        text = (m.content or "").strip()
        if not text:
            continue
        role = "user" if m.role == "user" else "model"
        contents.append(
            genai_types.Content(role=role, parts=[genai_types.Part.from_text(text=text)])
        )
    if latest_user:
        contents.append(
            genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=latest_user)])
        )
    return contents


async def run_agentic_turn(
    workspace_dir: Path,
    user_message: str,
    use_case: UseCase,
    chat_history: list[ChatMessage],
    *,
    user_prompt_for_scoring: str | None = None,
    model: str | None = None,
    max_iters: int = MAX_AGENT_ITERS,
):
    """Yield SSE event tuples (event_name, payload_dict) as the agent works.

    The caller (the /chat/stream endpoint) serializes each tuple into an SSE
    frame. This generator drives the ReAct-style tool loop:
    Gemini -> tool_calls -> execute -> feed responses -> repeat until
    `finalize_schema` or `ask_user` is called (or MAX_ITERS hit).
    """
    if not is_available():
        yield "error", {"message": "GEMINI_API_KEY not set.", "code": "no_api_key"}
        return

    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError as exc:
        yield "error", {"message": f"google-genai not installed: {exc}", "code": "no_sdk"}
        return

    from tg_schema_agent.llm.tools import (
        MUTATING_TOOLS,
        TERMINATING_TOOLS,
        ToolContext,
        build_function_declarations,
        execute_tool,
    )

    # Build the context (loads profiles, pattern, existing working schema)
    try:
        ctx = ToolContext.load(
            workspace_dir, use_case, user_prompt=user_prompt_for_scoring
        )
    except Exception as exc:  # noqa: BLE001
        yield "error", {
            "message": f"Failed to load workspace context: {exc}",
            "code": "workspace_load",
        }
        return

    # Reset working schema for a fresh turn IF the user message implies a new
    # design (kickoff or no schema yet). For follow-ups (refinement turns) we
    # keep the existing working schema so the agent can edit it.
    is_first_design = (
        not ctx.working_schema.vertices and not ctx.working_schema.edges
    )

    contents = _history_to_contents(chat_history, user_message)
    if not contents:
        # Kickoff: open-ended seed so Gemini asks the user about goals first
        # (per Stage 1 of the system prompt) rather than jumping into tools.
        kickoff = (
            "Hi, I just uploaded data. Before you look at it, ask me what "
            "business decision I'm trying to make with this graph."
        )
        contents = [
            genai_types.Content(
                role="user", parts=[genai_types.Part.from_text(text=kickoff)]
            )
        ]

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)
    model_name = model or DEFAULT_MODEL
    tools = build_function_declarations()

    terminating_payload: dict[str, Any] | None = None
    terminating_kind: str | None = None  # "finalize_schema" | "ask_user"
    # Accumulate any prose the model emits in this turn — if it stops with
    # only text (no terminating tool), treat that text as the conversational
    # reply to the user.
    accumulated_text_parts: list[str] = []

    # Per-turn budget caps — defensive backstop against runaway exploration.
    # The system prompt also asks for these limits, but the model occasionally
    # ignores soft caps; this returns a stub error result so the model sees
    # the cap and moves on instead of looping forever on inspect_column.
    _budget: dict[str, int] = {
        "inspect_column": 0,
        "get_sample_rows": 0,
        "find_columns_matching": 0,
    }
    _BUDGET_LIMITS = {
        "inspect_column": 8,
        "get_sample_rows": 4,
        "find_columns_matching": 6,
    }

    # Thinking budget is model-dependent:
    #   - gemini-3.x pro: high budget (~32k tokens) is verified safe and
    #     adds only ~1-2s latency vs ~4k. Worth it for agentic multi-tool
    #     reasoning (correct edge directions, correct retry on failure).
    #   - gemini-2.5-pro REQUIRES thinking_budget > 0 (it raises 400 on 0).
    #   - gemini-2.5-flash burns its default 1024-token budget on thinking
    #     before emitting any tool call, returning 0 parts. Disable it.
    # Override via GEMINI_THINKING_BUDGET env (positive int, or -1 for
    # dynamic — let the model decide per turn).
    _thinking_default = 32768 if "pro" in model_name.lower() else 0
    try:
        _thinking_budget = int(os.environ.get("GEMINI_THINKING_BUDGET", _thinking_default))
    except ValueError:
        _thinking_budget = _thinking_default

    _gen_config = genai_types.GenerateContentConfig(
        system_instruction=_AGENTIC_SYSTEM_INSTRUCTION,
        tools=tools,
        temperature=0.3,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=_thinking_budget),
    )

    for iteration in range(max_iters):
        try:
            resp = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=_gen_config,
            )
        except Exception as exc:  # noqa: BLE001
            yield "error", {
                "message": f"Gemini call failed: {exc}",
                "code": "gemini_failure",
            }
            return

        candidate = (resp.candidates or [None])[0]
        if candidate is None:
            yield "error", {"message": "Gemini returned no candidates.", "code": "no_candidates"}
            return

        content_obj = getattr(candidate, "content", None)
        if content_obj is None:
            # Gemini sometimes returns a candidate with no content — usually a
            # safety filter or graceful end-of-loop. Stop the loop with a
            # best-effort finish.
            finish_reason = getattr(candidate, "finish_reason", None)
            if finish_reason and str(finish_reason).upper() in ("STOP", "MAX_TOKENS"):
                # Normal stop without tool call — exit loop and build final payload below
                break
            yield "error", {
                "message": f"Gemini returned no content (finish_reason={finish_reason}).",
                "code": "no_content",
            }
            return

        parts = getattr(content_obj, "parts", None) or []
        # Carry the assistant turn forward in the conversation so tool responses
        # are interpreted in context.
        contents.append(content_obj)

        produced_any_tool_call = False
        tool_responses: list[Any] = []

        for idx, part in enumerate(parts):
            # Plain text part — surface as `thinking` AND accumulate (in case
            # the model is in conversational mode and never calls a tool).
            text = getattr(part, "text", None)
            if text and text.strip():
                yield "thinking", {"text": text.strip()}
                accumulated_text_parts.append(text.strip())

            fc = getattr(part, "function_call", None)
            if not fc:
                continue

            produced_any_tool_call = True
            call_id = f"tc-{iteration}-{idx}"
            args = _to_jsonable(fc.args) if fc.args is not None else {}

            yield "tool_call", {"id": call_id, "name": fc.name, "args": args}

            # Enforce per-turn budget for expensive inspection tools so the
            # model can't burn the context window aggressively profiling.
            if fc.name in _BUDGET_LIMITS:
                _budget[fc.name] += 1
                if _budget[fc.name] > _BUDGET_LIMITS[fc.name]:
                    result = {
                        "ok": False,
                        "summary": (
                            f"Budget exceeded: {fc.name} can only be called "
                            f"{_BUDGET_LIMITS[fc.name]} times per turn. "
                            "Move on to the next stage — propose vertices "
                            "from what you already know."
                        ),
                        "data": {"budget_exceeded": True, "limit": _BUDGET_LIMITS[fc.name]},
                    }
                else:
                    result = await execute_tool(ctx, fc.name, args)
            else:
                result = await execute_tool(ctx, fc.name, args)

            yield "tool_result", {
                "id": call_id,
                "name": fc.name,
                "ok": bool(result.get("ok", False)),
                "summary": result.get("summary", ""),
            }

            # If the tool mutated the schema, push the latest snapshot
            if fc.name in MUTATING_TOOLS:
                yield "schema_update", {
                    "schema": ctx.working_schema.model_dump(mode="json")
                }

            # If terminating, remember and break out after the loop body —
            # but ONLY if the tool actually succeeded. A failed
            # `finalize_schema` (e.g., empty schema) should let the loop
            # continue so the model can try `propose_vertex` first.
            if fc.name in TERMINATING_TOOLS and result.get("ok", False):
                terminating_kind = fc.name
                terminating_payload = result

            # Build the FunctionResponse content part to feed back to Gemini
            tool_responses.append(
                genai_types.Part.from_function_response(
                    name=fc.name,
                    response={
                        "ok": bool(result.get("ok", False)),
                        "summary": result.get("summary", ""),
                        "data": result.get("data"),
                    },
                )
            )

        # If the model produced tool calls, append them all in one Content
        if tool_responses:
            contents.append(genai_types.Content(role="user", parts=tool_responses))

        if terminating_kind:
            break

        # If the model returned only text with no tool calls, stop the loop
        if not produced_any_tool_call:
            break
    else:
        # Hit max iters without a terminating call. This usually means the
        # model chained too many verifications on a live-ops turn and forgot
        # to call reply_to_user. Persist what we have and finish gracefully
        # — the user almost certainly got what they asked for (the live tool
        # already ran), just without a clean closing message.
        ctx.persist_schema()
        last_summary = ""
        if accumulated_text_parts:
            last_summary = accumulated_text_parts[-1][:240]
        yield "final", {
            "type": "answer",
            "message": (
                last_summary
                or "Done — the requested operation ran. "
                "(Note: the agent kept verifying instead of stopping. "
                "Ask me to check the graph state if you want details.)"
            ),
            "suggested_replies": [
                "Show graph state",
                "Generate starter queries",
                "Run a query",
            ],
            "schema": ctx.working_schema.model_dump(mode="json"),
            "validation": None,
            "score": None,
        }
        return

    # Build the final payload based on which terminating call was made
    if terminating_kind == "ask_user":
        question_data = (terminating_payload or {}).get("data") or {}
        yield "final", {
            "type": "question",
            "message": question_data.get("question", terminating_payload.get("summary", "") if terminating_payload else ""),
            "suggested_replies": question_data.get("suggested_replies", []),
            "schema": ctx.working_schema.model_dump(mode="json") if (ctx.working_schema.vertices or ctx.working_schema.edges) else None,
            "validation": None,
            "score": None,
        }
        return

    if terminating_kind == "reply_to_user":
        reply_data = (terminating_payload or {}).get("data") or {}
        ctx.persist_schema()
        yield "final", {
            "type": "answer",
            "message": reply_data.get("message", terminating_payload.get("summary", "") if terminating_payload else ""),
            "suggested_replies": reply_data.get("suggested_replies", []),
            "schema": ctx.working_schema.model_dump(mode="json") if (ctx.working_schema.vertices or ctx.working_schema.edges) else None,
            "validation": None,
            "score": None,
        }
        return

    # The model ended the turn with only prose (no terminating tool, no
    # mutation tools either). Treat that as a conversational reply — the
    # agent is in Stage 1 (still understanding the goal). Emit a `final`
    # event with type="answer" so the chat panel shows the agent's text.
    if not terminating_kind and accumulated_text_parts and not ctx.working_schema.vertices:
        yield "final", {
            "type": "answer",
            "message": " ".join(accumulated_text_parts).strip(),
            "suggested_replies": [],
            "schema": None,
            "validation": None,
            "score": None,
        }
        return

    # Defensive fallback for the kickoff path: if the agent ran the loop
    # without proposing anything AND without calling ask_user AND without
    # producing text, force a decision-first question so the user isn't
    # stuck on an empty "Designed a schema with 0 vertices…" reply.
    # This happens when Gemini misreads the heavy system prompt on the
    # very first turn (no user message, no schema yet).
    if (
        not terminating_kind
        and not accumulated_text_parts
        and not ctx.working_schema.vertices
        and is_first_design
    ):
        default_q = (
            "What decision are you trying to make with this data? "
            "That'll shape how I design the graph."
        )
        default_replies = [
            "Find fraud rings",
            "Detect mule accounts",
            "Build a Customer 360",
            "Just explore",
        ]
        yield "final", {
            "type": "question",
            "message": default_q,
            "suggested_replies": default_replies,
            "schema": None,
            "validation": None,
            "score": None,
        }
        return

    # Default + finalize_schema path (or model stopped after partial schema work)
    from tg_schema_agent import scorer as scorer_mod
    from tg_schema_agent import validator as validator_mod

    val = validator_mod.validate(ctx.working_schema)
    score = scorer_mod.score_schema(
        ctx.working_schema, val, ctx.pattern, user_prompt=ctx.user_prompt
    )
    ctx.persist_schema()

    final_message = ""
    final_chips: list[str] = []
    if terminating_kind == "finalize_schema" and terminating_payload:
        data = terminating_payload.get("data") or {}
        final_message = (
            data.get("user_summary")
            or terminating_payload.get("summary")
            or f"Schema designed: {len(ctx.working_schema.vertices)} vertices, "
            f"{len(ctx.working_schema.edges)} edges."
        )
        # Behavior 7 — post-finalize chips (compliance / additional questions /
        # future use cases). `finalize_schema` always returns a default set.
        final_chips = list(data.get("suggested_replies") or [])
    elif accumulated_text_parts:
        # Model produced both schema mutations AND text — use the text as the summary
        final_message = " ".join(accumulated_text_parts).strip()
    else:
        final_message = (
            f"Designed a schema with {len(ctx.working_schema.vertices)} vertices "
            f"and {len(ctx.working_schema.edges)} edges. Score: {score.total}/100."
        )

    from tg_schema_agent.scorer import compute_confidence

    confidence = compute_confidence(score, val, ctx.working_schema.assumptions)

    yield "final", {
        "type": "propose_schema" if is_first_design else "update_schema",
        "message": final_message,
        "suggested_replies": final_chips,
        "schema": ctx.working_schema.model_dump(mode="json"),
        "validation": val.model_dump(mode="json"),
        "score": score.model_dump(mode="json"),
        "confidence": confidence,
    }


def parse_final_reply(
    full_text: str,
    profiles: list[TableProfile],
    use_case: UseCase,
    patterns_dir: Path | None = None,
) -> AgentReply:
    """Parse the accumulated streamed JSON into a structured AgentReply.

    Called by the streaming endpoint once the stream ends.
    """
    pattern = load_patterns(patterns_dir)[use_case]
    text = (full_text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse streamed JSON: {exc}") from exc

    schema_dict = _finalize_schema(raw, profiles, pattern, use_case)
    return AgentReply(
        type=raw.get("type", "answer"),
        message=raw.get("message", "").strip(),
        suggested_replies=raw.get("suggested_replies", []) or [],
        schema_json=schema_dict,
        explanation=raw.get("explanation", "") or "",
    )
