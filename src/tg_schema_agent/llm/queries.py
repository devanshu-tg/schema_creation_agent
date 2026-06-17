"""LLM-generated starter GSQL queries (Autograph Behavior 9).

After a schema is deployed, generate 5-8 useful GSQL queries tailored to
the user's business context + the schema's vertex/edge shape. Each query
is dry-run validated via `INTERPRET QUERY` against the live graph (no
install) and re-prompted with the error on syntax failures.

Output goes through `tigergraph__install_query` when the user clicks the
install button on the Starter Queries panel.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from pydantic import BaseModel, Field

from tg_schema_agent.models import BusinessContext, Schema

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")


class StarterQuery(BaseModel):
    name: str = Field(..., description="snake_case query name; valid GSQL identifier")
    description: str = Field(
        ..., description="One-sentence user-facing purpose."
    )
    business_question: str = Field(
        "",
        description="The exact target_question this query answers, when applicable.",
    )
    gsql: str = Field(..., description="Full GSQL query body (CREATE QUERY ...).")
    expected_output_description: str = Field(
        "", description="What the user sees when this query runs."
    )
    validated: bool = Field(
        default=False,
        description="Whether INTERPRET QUERY dry-run passed.",
    )
    validation_error: str | None = Field(
        default=None,
        description="Last validation error if dry-run failed.",
    )


class StarterQuerySet(BaseModel):
    queries: list[StarterQuery] = Field(default_factory=list)


_SYSTEM_INSTRUCTION = """You are a senior TigerGraph solutions architect writing
starter GSQL 4.x queries for a graph that was just designed.

Your job: produce 5 to 8 GSQL queries that demonstrate the graph's value
for the user's business questions, covering: multi-hop traversals,
shared-identifier ring detection, event filtering, aggregations, top-K.

== HARD RULES (violating any of these = query is rejected as draft) ==

R1. **Edge directions are strict.** The `EDGE DIRECTIONS` table in the
    prompt tells you the exact source→target for every edge. The edge
    only traverses in that direction. If you need the opposite direction,
    look for the paired REVERSE edge in the table.
      WRONG: `FROM Transaction:t -(Account_MADE_Transaction)-> Account:a`
             (Account_MADE_Transaction goes Account→Transaction, not the reverse)
      RIGHT: `FROM Transaction:t -(Transaction_INITIATED_BY_Account)-> Account:a`

R2. **Use ONLY attribute names listed in the schema.** Every vertex has
    a `ATTRIBUTES` list. Do not invent attribute names. If you need an
    attribute that isn't there, the query should not use it.
      WRONG: `SELECT a.customer_name FROM ...`  (when schema has `name`, not `customer_name`)
      RIGHT: `SELECT a.name FROM ...`

R3. **Edge syntax**: use `-(EdgeName:e)-` for undirected reference,
    `-(EdgeName:e)->` for explicit forward direction. Both work for
    DIRECTED_WITH_REVERSE edges; pick `-( )->` for clarity.

R4. **Vertex set declaration**: always `Start = {VertexType.*};` (with
    the dot-star), never `Start = {VertexType};`.

R5. **FROM clause syntax**: `Result = SELECT t FROM Start:s -(Edge:e)-> Target:t [WHERE ...] [ACCUM ...];`
    The aliases `:s`, `:e`, `:t` are required when you reference them later.

R6. **Accumulator placement**: declare accumulators inside the query body,
    NOT inside the SELECT. Vertex-attached accums use `@`, global use `@@`.
      DECLARE: `SumAccum<INT> @tx_count;`        (vertex-attached)
               `MapAccum<STRING, INT> @@counts;` (global)
      USE:     `ACCUM v.@tx_count += 1, @@counts += (v.id -> 1)`

R7. **Parameters MUST be scalar** (STRING, INT, FLOAT, DATETIME).
    NEVER use VERTEX<T> as a parameter — it cannot be passed from the
    chat. For "queries about a specific X", take the primary_id as a
    STRING and look it up inside the query:
      Wrong: `CREATE QUERY q(VERTEX<Account> acc) ...`
      Right: `CREATE QUERY q(STRING acc_id) {
                Start = {Account.* WHERE acc_id == acc_id};
                ...
              }`

R8. **Print results with accumulators**. Use `HeapAccum<TUPLE>` for
    top-K rankings, `MapAccum` for grouped counts, `SetAccum` for
    distinct collections. Always end with `PRINT <accum>;`.

R9. **Don't use SQL or Cypher syntax**: no JOIN, no MATCH, no WHERE on
    edges in the SELECT (use WHERE inside FROM).

== QUERY NAMING ==
- snake_case
- Reuse the user's `business_questions` verbatim when possible
- Examples: `accounts_sharing_device`, `fraud_rings_three_hops`,
  `top_merchants_by_volume`, `customers_with_shared_phone`

== OUTPUT FORMAT ==
Return ONLY a JSON object — no prose, no code fences:

{
  "queries": [
    {
      "name": "snake_case_name",
      "description": "One sentence: what this query does for the user.",
      "business_question": "The user's question this answers, verbatim if possible.",
      "gsql": "CREATE QUERY name(...) FOR GRAPH g { ... }",
      "expected_output_description": "What the result tuple/heap looks like."
    }
  ]
}
"""

# Few-shot examples — known-good GSQL against a typical fraud schema.
# The model pattern-matches these heavily; keep them clean and
# representative of the patterns we want.
_FEW_SHOT_EXAMPLES = """
== FEW-SHOT EXAMPLES (study these carefully — your output should match this style) ==

Schema for these examples:
  Vertex Account(account_id PRIMARY_ID, name STRING, status STRING)
  Vertex Device(device_id PRIMARY_ID, os STRING)
  Vertex Transaction(tx_id PRIMARY_ID, amount FLOAT, ts DATETIME, is_fraud BOOL)
  Vertex Merchant(merchant_id PRIMARY_ID, category STRING)

  Edge Account_MADE_Transaction      from=Account     to=Transaction
  Edge Transaction_INITIATED_BY_Account from=Transaction to=Account  (reverse)
  Edge Transaction_PAID_Merchant     from=Transaction to=Merchant
  Edge Merchant_RECEIVED_Transaction from=Merchant    to=Transaction (reverse)
  Edge Transaction_FROM_Device       from=Transaction to=Device
  Edge Device_USED_BY_Transaction    from=Device      to=Transaction (reverse)

EXAMPLE 1 — accounts sharing a device (classic fraud ring pattern):

  CREATE QUERY accounts_sharing_device(INT min_shared = 2) FOR GRAPH mcp_demo {
    MapAccum<STRING, SetAccum<STRING>> @@device_accounts;
    HeapAccum<TUPLE<STRING device_id, INT account_count>>(20, account_count DESC) @@top_shared;

    Start = {Account.*};
    Tx = SELECT t FROM Start:s -(Account_MADE_Transaction:e)-> Transaction:t
         ACCUM @@device_accounts += (t.tx_id -> s.account_id);

    FOREACH (tx_id, accts) IN @@device_accounts DO
      IF accts.size() >= min_shared THEN
        @@top_shared += TUPLE<STRING, INT>(tx_id, accts.size());
      END;
    END;

    PRINT @@top_shared;
  }

EXAMPLE 2 — top merchants by transaction volume (aggregation + ranking):

  CREATE QUERY top_merchants_by_volume(INT k = 10) FOR GRAPH mcp_demo {
    SumAccum<FLOAT> @total_amount;
    SumAccum<INT> @tx_count;
    HeapAccum<TUPLE<STRING merchant_id, INT tx_count, FLOAT total_amount>>(k, total_amount DESC) @@top;

    Start = {Merchant.*};
    Result = SELECT m FROM Start:m -(Merchant_RECEIVED_Transaction:e)-> Transaction:t
             ACCUM m.@total_amount += t.amount, m.@tx_count += 1
             POST-ACCUM @@top += TUPLE<STRING, INT, FLOAT>(m.merchant_id, m.@tx_count, m.@total_amount);

    PRINT @@top;
  }

EXAMPLE 3 — accounts within N hops of a known fraud transaction (multi-hop):

  CREATE QUERY accounts_near_fraud(INT max_hops = 3) FOR GRAPH mcp_demo {
    SetAccum<STRING> @@suspicious_accounts;

    FraudTx = SELECT t FROM Transaction:t WHERE t.is_fraud == true;
    Frontier = FraudTx;

    WHILE Frontier.size() > 0 LIMIT max_hops DO
      Frontier = SELECT a FROM Frontier:t -(Transaction_INITIATED_BY_Account:e)-> Account:a
                 ACCUM @@suspicious_accounts += a.account_id;
      // Also walk the reverse to find more transactions of those accounts
      Frontier = SELECT t2 FROM Frontier:a -(Account_MADE_Transaction:e)-> Transaction:t2;
    END;

    PRINT @@suspicious_accounts;
  }

EXAMPLE 4 — find a specific account's recent transactions (parameterized lookup):

  CREATE QUERY account_recent_transactions(STRING acc_id, INT n = 10) FOR GRAPH mcp_demo {
    HeapAccum<TUPLE<STRING tx_id, FLOAT amount, DATETIME ts>>(n, ts DESC) @@recent;

    Start = {Account.* WHERE Account.account_id == acc_id};
    Result = SELECT t FROM Start:s -(Account_MADE_Transaction:e)-> Transaction:t
             ACCUM @@recent += TUPLE<STRING, FLOAT, DATETIME>(t.tx_id, t.amount, t.ts);

    PRINT @@recent;
  }

EXAMPLE 5 — count distinct merchants per account (group-by aggregation):

  CREATE QUERY merchants_per_account() FOR GRAPH mcp_demo {
    MapAccum<STRING, SetAccum<STRING>> @@acc_merchants;

    Start = {Account.*};
    Tx = SELECT t FROM Start:s -(Account_MADE_Transaction:e)-> Transaction:t
         ACCUM @@acc_merchants += (s.account_id -> "tx-" + t.tx_id);
    Mer = SELECT m FROM Tx:t -(Transaction_PAID_Merchant:e)-> Merchant:m
          ACCUM @@acc_merchants += (m.merchant_id -> "mer-" + m.merchant_id);

    PRINT @@acc_merchants;
  }

Notice the patterns:
- Every traversal explicitly names the edge AND its direction (`-( )->`)
- Every variable on the FROM clause has an alias (`:s`, `:e`, `:t`)
- All accumulators declared in the query body, used in ACCUM
- Vertex-attached `@`, global `@@` — never confused
- Parameters are all scalar (STRING, INT) — no VERTEX<T>
"""


def _build_edge_direction_table(schema: Schema) -> str:
    """Human-readable edge direction reference card for the LLM prompt.

    Lists each edge with its from→to direction, and notes the reverse
    edge when one exists (pairs are detected by matching from/to flip).
    This is the single most effective fix for TYP-111 errors — the model
    can no longer guess the direction because it's right there.
    """
    by_endpoints = {(e.from_vertex, e.to_vertex): e.name for e in schema.edges}
    lines = []
    seen: set[str] = set()
    for e in schema.edges:
        if e.name in seen:
            continue
        seen.add(e.name)
        reverse_key = (e.to_vertex, e.from_vertex)
        reverse_name = by_endpoints.get(reverse_key)
        line = f"  {e.name:<48} from={e.from_vertex} to={e.to_vertex}"
        if reverse_name and reverse_name != e.name:
            line += f"  (reverse: {reverse_name})"
            seen.add(reverse_name)
        lines.append(line)
        # Also output the reverse on its own line for clarity
        if reverse_name and reverse_name != e.name:
            lines.append(
                f"  {reverse_name:<48} from={e.to_vertex} to={e.from_vertex}  (reverse of {e.name})"
            )
    return "EDGE DIRECTIONS (use these exactly):\n" + "\n".join(lines)


def _build_vertex_attribute_table(schema: Schema) -> str:
    """Per-vertex attribute reference. Eliminates 'hallucinated attribute'
    errors where the model uses `customer_name` when the field is `name`."""
    lines = []
    for v in schema.vertices:
        attrs = ", ".join(
            f"{a.name}:{a.dtype.value}" for a in v.attributes
        ) or "(none beyond primary_id)"
        lines.append(
            f"  {v.name:<24} primary_id={v.primary_id} ({v.primary_id_dtype.value})"
            f"  attributes: {attrs}"
        )
    return "VERTEX ATTRIBUTES (use ONLY these names):\n" + "\n".join(lines)


def _schema_summary_for_prompt(schema: Schema, graph_name: str) -> dict[str, Any]:
    """Compact representation of the schema for the LLM prompt.

    Also adds the edge-direction and vertex-attribute reference tables as
    string fields — the model reads these directly when writing queries.
    """
    return {
        "graph_name": graph_name,
        "use_case": schema.use_case.value,
        "vertices": [
            {
                "name": v.name,
                "primary_id": v.primary_id,
                "primary_id_dtype": v.primary_id_dtype.value,
                "attributes": [{"name": a.name, "type": a.dtype.value} for a in v.attributes],
            }
            for v in schema.vertices
        ],
        "edges": [
            {
                "name": e.name,
                "from": e.from_vertex,
                "to": e.to_vertex,
                "attributes": [{"name": a.name, "type": a.dtype.value} for a in e.attributes],
            }
            for e in schema.edges
        ],
        "edge_direction_table": _build_edge_direction_table(schema),
        "vertex_attribute_table": _build_vertex_attribute_table(schema),
    }


def _business_context_for_prompt(bc: BusinessContext | None) -> dict[str, Any]:
    if not bc:
        return {}
    return {
        "domain": bc.domain,
        "sub_scenarios": list(bc.sub_scenarios),
        "goal_type": bc.goal_type,
        "business_questions": list(bc.business_questions),
        "stakeholders": list(bc.stakeholders),
    }


def _active_provider() -> str:
    """LLM_PROVIDER selects which backend handles starter-query generation."""
    return (os.environ.get("LLM_PROVIDER") or "gemini").strip().lower()


def is_available() -> bool:
    """True if the configured provider has both SDK + API key wired up."""
    if _active_provider() == "openrouter":
        if not os.environ.get("OPENROUTER_API_KEY"):
            return False
        try:
            import openai  # noqa: F401
            return True
        except ImportError:
            return False
    # Default: Gemini
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return False
    try:
        import google.genai  # noqa: F401
        return True
    except ImportError:
        return False


def _generate_with_gemini(
    schema: Schema,
    graph_name: str,
    business_context: BusinessContext | None,
    model: str | None = None,
    retry_with_error: str | None = None,
    prior_attempt: str | None = None,
) -> StarterQuerySet | None:
    """Call Gemini and parse its JSON response into a StarterQuerySet."""
    from google import genai
    from google.genai import types as genai_types

    target_questions = [q.text for q in schema.target_questions]
    payload = {
        "schema": _schema_summary_for_prompt(schema, graph_name),
        "business_context": _business_context_for_prompt(business_context),
        "target_questions": target_questions,
    }
    user_text_parts = [
        "Here is the schema and business context. Produce 5-8 starter "
        "GSQL queries that answer the business questions and demonstrate "
        "the graph's value.",
        "",
        json.dumps(payload, indent=2),
    ]
    if retry_with_error and prior_attempt:
        user_text_parts.extend([
            "",
            "Your previous attempt failed validation. Here is the error:",
            "",
            retry_with_error,
            "",
            "And your previous attempt was:",
            "",
            prior_attempt,
            "",
            "Fix the syntax / attribute references and try again. Return JSON only.",
        ])

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)

    model_name = model or DEFAULT_MODEL
    _thinking_default = 32768 if "pro" in model_name.lower() else 0
    try:
        _thinking_budget = int(os.environ.get("GEMINI_THINKING_BUDGET", _thinking_default))
    except ValueError:
        _thinking_budget = _thinking_default
    try:
        resp = client.models.generate_content(
            model=model_name,
            contents=[
                genai_types.Content(
                    role="user",
                    parts=[genai_types.Part.from_text(text="\n".join(user_text_parts))],
                )
            ],
            config=genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.2,
                thinking_config=genai_types.ThinkingConfig(thinking_budget=_thinking_budget),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini call failed in starter queries: %s", exc)
        return None

    candidate = (resp.candidates or [None])[0]
    if candidate is None:
        return None
    content_obj = getattr(candidate, "content", None)
    if content_obj is None:
        return None
    parts = getattr(content_obj, "parts", None) or []
    text = "".join(getattr(p, "text", "") or "" for p in parts).strip()
    if not text:
        return None

    # Strip code fences just in case the model ignored response_mime_type
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        if text.endswith("```"):
            text = text[:-3]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("Could not parse Gemini queries JSON: %s", exc)
        return None
    try:
        qset = StarterQuerySet.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001
        log.warning("Invalid StarterQuerySet shape: %s", exc)
        return None
    for q in qset.queries:
        q.gsql = _gsql_postprocess(q.gsql)
    return qset


def _generate_with_openrouter(
    schema: Schema,
    graph_name: str,
    business_context: BusinessContext | None,
    model: str | None = None,
    retry_with_error: str | None = None,
    prior_attempt: str | None = None,
) -> StarterQuerySet | None:
    """OpenRouter path — uses a query-specific model override (Opus 4.8 by
    default) so we can use the strongest available model for GSQL even
    when the chat agent runs on a cheaper one like Sonnet 4.6."""
    from openai import OpenAI

    schema_summary = _schema_summary_for_prompt(schema, graph_name)
    target_questions = [q.text for q in schema.target_questions]
    business = _business_context_for_prompt(business_context)

    # The prompt is layered: rules → few-shot examples → THIS schema's
    # reference tables (most important) → JSON of schema + context → ask
    user_text_parts = [
        _FEW_SHOT_EXAMPLES,
        "",
        "=" * 70,
        "== THIS SCHEMA'S REFERENCE TABLES (use these exactly) ==",
        "=" * 70,
        "",
        schema_summary["edge_direction_table"],
        "",
        schema_summary["vertex_attribute_table"],
        "",
        "=" * 70,
        "== BUSINESS CONTEXT ==",
        "=" * 70,
        "",
        f"Graph name: {graph_name}",
        f"Use case: {schema_summary['use_case']}",
        f"Business questions: {target_questions}",
        f"Domain: {business.get('domain', '')}",
        f"Sub-scenarios: {business.get('sub_scenarios', [])}",
        f"Stakeholders: {business.get('stakeholders', [])}",
        "",
        "=" * 70,
        "== TASK ==",
        "=" * 70,
        "",
        "Produce 5-8 starter GSQL queries that answer the business questions "
        "above using ONLY the edges and attributes listed in the reference "
        "tables. Follow the few-shot examples' style exactly.",
        "",
        "Reminder of the hard rules:",
        "  R1: Edge directions are STRICT — only use edges from→to as listed.",
        "  R2: ONLY use attribute names from the VERTEX ATTRIBUTES table.",
        "  R3: Use `-(EdgeName:e)->` syntax, with alias.",
        "  R4: `Start = {VertexType.*};` always (note the .*)",
        "  R7: All parameters MUST be scalar (STRING/INT/FLOAT/DATETIME).",
        "       Never VERTEX<T>.",
        "",
        "Return JSON only — no prose, no code fences.",
    ]
    if retry_with_error and prior_attempt:
        user_text_parts.extend([
            "",
            "=" * 70,
            "== PREVIOUS ATTEMPT FAILED VALIDATION ==",
            "=" * 70,
            "",
            "TigerGraph rejected these queries with the following errors:",
            "",
            retry_with_error,
            "",
            "Common fixes:",
            "  - TYP-111: you used the wrong edge direction. Check the "
            "EDGE DIRECTIONS table and use the REVERSE edge if needed.",
            "  - 'attribute X not found': you invented an attribute name. "
            "Use ONLY names from the VERTEX ATTRIBUTES table.",
            "  - 'Saved as draft': syntactic or semantic error — re-check "
            "syntax against the FEW-SHOT EXAMPLES.",
            "",
            "Your previous (failed) JSON was:",
            "",
            prior_attempt,
            "",
            "Fix all errors and return a corrected JSON. Same shape.",
        ])

    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        default_headers={
            "HTTP-Referer": os.environ.get(
                "OPENROUTER_REFERER", "https://github.com/devanshu-tg/schema_creation_agent"
            ),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Autograph"),
        },
    )
    # Query-specific model override. Defaults to Opus 4.8 (most capable
    # Anthropic model available) since GSQL is hard for LLMs and we want
    # the best shot at correct-on-first-try.
    model_name = (
        model
        or os.environ.get("OPENROUTER_QUERY_MODEL")
        or os.environ.get("OPENROUTER_MODEL")
        or "anthropic/claude-opus-4.8"
    )
    log.info("Generating starter queries via OpenRouter model: %s", model_name)
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": _SYSTEM_INSTRUCTION},
                {"role": "user", "content": "\n".join(user_text_parts)},
            ],
            temperature=0.1,  # lower than chat — GSQL is precision work
            max_tokens=8000,  # need room for 5-8 multi-line GSQL queries
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("OpenRouter call failed in starter queries: %s", exc)
        return None

    choice = (resp.choices or [None])[0]
    if choice is None or not choice.message or not choice.message.content:
        return None
    text = choice.message.content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        if text.endswith("```"):
            text = text[:-3]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("Could not parse OpenRouter queries JSON: %s", exc)
        return None
    try:
        qset = StarterQuerySet.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001
        log.warning("Invalid StarterQuerySet shape: %s", exc)
        return None
    # Post-process every query's GSQL to fix common case/syntax mistakes
    for q in qset.queries:
        q.gsql = _gsql_postprocess(q.gsql)
    return qset


# ---------------------------------------------------------------------------
# Post-processing: fix common LLM mistakes in generated GSQL
# ---------------------------------------------------------------------------

# Map of lowercase type names → uppercase. The model (Opus 4.8 and Sonnet
# both) consistently writes `tuple<string id, int count>` even though GSQL
# requires `TUPLE<STRING id, INT count>`. Rather than re-prompt 3 times to
# get the model to capitalize, fix it deterministically.
_GSQL_TYPE_LOWERCASE_FIX = [
    # ordered: longer names first to avoid partial matches
    ("DATETIME", ["datetime"]),
    ("STRING", ["string"]),
    ("FLOAT", ["float"]),
    ("DOUBLE", ["double"]),
    ("BOOL", ["bool"]),
    ("INT", ["int"]),
    ("UINT", ["uint"]),
    ("TUPLE", ["tuple"]),
]


def _gsql_postprocess(gsql: str) -> str:
    """Fix common case-sensitivity mistakes in generated GSQL.

    GSQL types (TUPLE, STRING, INT, etc.) are uppercase. LLMs frequently
    write them lowercase, which causes parser errors like:
      'no viable alternative at input HeapAccum<tuple<string id, int count'

    Strategy: walk character-by-character, tracking bracket depth. Within
    any `<...>` block (nested or not), uppercase known type tokens. This
    is more surgical than a global replace which would clobber attribute
    names or string literals that contain the word `string`/`int`/etc.
    """
    import re

    # Build word-boundary regex for known lowercase types
    pattern = re.compile(
        r"\b(" + "|".join(v for _, vars_ in _GSQL_TYPE_LOWERCASE_FIX for v in vars_) + r")\b"
    )
    upper_map = {v: u for u, vars_ in _GSQL_TYPE_LOWERCASE_FIX for v in vars_}

    out_chars: list[str] = []
    depth = 0
    in_string = False
    string_quote: str | None = None
    buf = ""  # accumulator for the chunk inside `<...>`

    i = 0
    n = len(gsql)
    while i < n:
        c = gsql[i]
        # Handle string-literal regions — never touch their contents
        if in_string:
            buf += c if depth > 0 else ""
            if not buf and depth == 0:
                out_chars.append(c)
            if c == string_quote and (i == 0 or gsql[i - 1] != "\\"):
                in_string = False
                string_quote = None
            if depth == 0:
                pass  # already appended
            i += 1
            continue
        if c in ("'", '"'):
            in_string = True
            string_quote = c
            if depth > 0:
                buf += c
            else:
                out_chars.append(c)
            i += 1
            continue
        if c == "<":
            if depth > 0:
                buf += c
            else:
                # Starting a new top-level angle block — flush current
                # accumulator and start new
                buf = ""
            depth += 1
            i += 1
            continue
        if c == ">":
            depth -= 1
            if depth == 0:
                # End of a top-level <...> block — process buf, emit
                fixed = pattern.sub(lambda m: upper_map[m.group(1)], buf)
                out_chars.append("<")
                out_chars.append(fixed)
                out_chars.append(">")
                buf = ""
            else:
                buf += c
            i += 1
            continue
        # Regular char
        if depth > 0:
            buf += c
        else:
            out_chars.append(c)
        i += 1

    return "".join(out_chars)


def _generate(
    schema: Schema,
    graph_name: str,
    business_context: BusinessContext | None,
    **kwargs: Any,
) -> StarterQuerySet | None:
    """Dispatch to the configured provider's generator."""
    if _active_provider() == "openrouter":
        return _generate_with_openrouter(schema, graph_name, business_context, **kwargs)
    return _generate_with_gemini(schema, graph_name, business_context, **kwargs)


async def _dry_run_query(
    session: Any,
    graph_name: str,
    query: StarterQuery,
) -> tuple[bool, str | None]:
    """Validate the query by running it as INTERPRET QUERY (no install).

    Returns (ok, error_text_or_none). On parse / semantic errors, returns
    the GSQL error so the caller can re-prompt Gemini with the failure.
    """
    # Validate via CREATE QUERY (parse + semantic check, no compilation)
    # then DROP QUERY for cleanup. INTERPRET QUERY can't be used because
    # GSQL rejects both names AND parameters on interpreted queries; our
    # generated queries have both.
    from tg_schema_agent.deploy import _call, _is_success, _summarize_error

    create = await _call(
        session,
        "tigergraph__gsql",
        {"command": f"USE GRAPH {graph_name}\n{query.gsql.strip()}"},
    )
    if not _is_success(create):
        return False, _summarize_error(create)

    # Cleanup — drop the draft so a later install_query_live can re-create
    # it without "already exists" conflicts.
    await _call(
        session,
        "tigergraph__gsql",
        {"command": f"USE GRAPH {graph_name}\nDROP QUERY {query.name}"},
    )
    return True, None


def _repair_one_with_openrouter(
    schema: Schema,
    graph_name: str,
    business_context: BusinessContext | None,
    failed: StarterQuery,
    error: str,
    model: str | None = None,
) -> StarterQuery | None:
    """One-shot repair of a single failing query.

    This is the key architectural fix vs batch-retry: we hand the model
    ONE failing query + its exact error and ask it to fix that one
    query. Same pattern Claude Code uses — fix one thing, see if it
    works, fix the next. Avoids the "regenerated all 8, broke a passing
    one" failure mode.
    """
    from openai import OpenAI

    schema_summary = _schema_summary_for_prompt(schema, graph_name)
    business = _business_context_for_prompt(business_context)

    user_text_parts = [
        _FEW_SHOT_EXAMPLES,
        "",
        "=" * 70,
        "== THIS SCHEMA'S REFERENCE TABLES (use these exactly) ==",
        "=" * 70,
        "",
        schema_summary["edge_direction_table"],
        "",
        schema_summary["vertex_attribute_table"],
        "",
        "=" * 70,
        "== REPAIR TASK ==",
        "=" * 70,
        "",
        f"Graph: {graph_name}",
        f"Domain: {business.get('domain', '')}",
        f"Sub-scenarios: {business.get('sub_scenarios', [])}",
        f"Query name: {failed.name}",
        f"Business question: {failed.business_question}",
        "",
        "The following GSQL query FAILED dry-run validation on TigerGraph:",
        "",
        "```gsql",
        failed.gsql.strip(),
        "```",
        "",
        "TigerGraph error:",
        "",
        error,
        "",
        "Common error → fix mapping:",
        "  - TYP-111 (no such edge / wrong direction): use the EXACT edge "
        "name from the EDGE DIRECTIONS table. If you need to traverse the "
        "opposite way, find the paired REVERSE edge in the same table.",
        "  - 'attribute X not found': only use names from VERTEX ATTRIBUTES.",
        "  - 'Saved as draft' / 'no viable alternative': syntactic error. "
        "Compare against the FEW-SHOT EXAMPLES line by line.",
        "  - lowercase type names (tuple/string/int) — must be UPPERCASE.",
        "  - VERTEX<T> parameter — change to STRING and look up by primary_id "
        "inside the query.",
        "",
        "Fix the query and return ONLY the corrected JSON for THIS ONE query.",
        "Shape:",
        "",
        '{"name": "...", "description": "...", "business_question": "...", '
        '"gsql": "CREATE QUERY ...", "expected_output_description": "..."}',
        "",
        "Keep the same `name` if at all possible — that's the user's identifier.",
    ]

    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        default_headers={
            "HTTP-Referer": os.environ.get(
                "OPENROUTER_REFERER", "https://github.com/devanshu-tg/schema_creation_agent"
            ),
            "X-Title": os.environ.get("OPENROUTER_APP_TITLE", "Autograph"),
        },
    )
    model_name = (
        model
        or os.environ.get("OPENROUTER_QUERY_MODEL")
        or os.environ.get("OPENROUTER_MODEL")
        or "anthropic/claude-opus-4.8"
    )
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": _SYSTEM_INSTRUCTION},
                {"role": "user", "content": "\n".join(user_text_parts)},
            ],
            temperature=0.05,  # even lower than batch — single-shot precision
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Repair call failed for %s: %s", failed.name, exc)
        return None

    choice = (resp.choices or [None])[0]
    if choice is None or not choice.message or not choice.message.content:
        return None
    text = choice.message.content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
        if text.endswith("```"):
            text = text[:-3]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("Repair JSON parse failed for %s: %s", failed.name, exc)
        return None
    try:
        fixed = StarterQuery.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001
        log.warning("Repair output shape invalid for %s: %s", failed.name, exc)
        return None
    fixed.gsql = _gsql_postprocess(fixed.gsql)
    return fixed


def _repair_one(
    schema: Schema,
    graph_name: str,
    business_context: BusinessContext | None,
    failed: StarterQuery,
    error: str,
) -> StarterQuery | None:
    """Provider-dispatch wrapper for single-query repair.

    Only OpenRouter has the per-query repair path implemented — Gemini
    continues to use batch retry inside `_generate_with_gemini`.
    """
    if _active_provider() == "openrouter":
        return _repair_one_with_openrouter(
            schema, graph_name, business_context, failed, error
        )
    return None


async def generate_starter_queries(
    session: Any,
    schema: Schema,
    graph_name: str,
    business_context: BusinessContext | None = None,
    *,
    max_repair_attempts_per_query: int = 2,
) -> StarterQuerySet:
    """Generate + dry-run-validate a set of starter queries.

    Returns a StarterQuerySet whose `queries[*].validated` flag indicates
    which queries passed the dry-run. Failed queries keep their
    `validation_error` populated so the UI can show why.

    Strategy (per-query repair, like Claude Code):
    1. Generate the initial batch in one call.
    2. Dry-run every query — passing queries are FROZEN, never touched again.
    3. For each FAILING query individually, ask the model to repair just
       that one query (with its specific error). Re-validate the fix.
    4. Repeat per-query repair up to `max_repair_attempts_per_query` times.
    5. Failed queries past the repair budget keep their last error.

    This is much closer to how an interactive agent fixes things —
    incremental, isolated, no churn on what already works.
    """
    if not is_available():
        log.info("LLM provider not available — returning empty starter query set.")
        return StarterQuerySet()

    qs = _generate(schema, graph_name, business_context)
    if qs is None:
        return StarterQuerySet()

    # First pass: validate every query
    for q in qs.queries:
        ok, err = await _dry_run_query(session, graph_name, q)
        q.validated = ok
        q.validation_error = err

    initial_ok = sum(1 for q in qs.queries if q.validated)
    log.info(
        "Starter queries — initial batch: %d/%d validated",
        initial_ok, len(qs.queries),
    )

    # Per-query repair loop
    for q_idx, q in enumerate(qs.queries):
        if q.validated:
            continue  # Already good — leave it alone

        for attempt in range(1, max_repair_attempts_per_query + 1):
            log.info(
                "Repairing %s (attempt %d/%d): %s",
                q.name, attempt, max_repair_attempts_per_query,
                (q.validation_error or "")[:120],
            )
            fixed = _repair_one(
                schema, graph_name, business_context, q, q.validation_error or "",
            )
            if fixed is None:
                continue  # LLM call failed — try again
            # Re-validate the fixed query
            ok, err = await _dry_run_query(session, graph_name, fixed)
            fixed.validated = ok
            fixed.validation_error = err
            # Replace in place — preserve order
            qs.queries[q_idx] = fixed
            q = fixed
            if ok:
                log.info("  → %s repaired successfully", q.name)
                break
            log.info("  → %s still failing: %s", q.name, (err or "")[:120])

    final_ok = sum(1 for q in qs.queries if q.validated)
    log.info(
        "Starter queries — final: %d/%d validated after per-query repair",
        final_ok, len(qs.queries),
    )
    return qs
