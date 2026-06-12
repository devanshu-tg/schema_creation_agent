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
starter GSQL queries for a graph that was just designed by an AI agent.

Your job: produce 5 to 8 GSQL queries that demonstrate the graph's value
for the user's stated business questions. Cover the most important
traversals — multi-hop, shared-identifier, event filtering, aggregation.

Constraints:
- Use TigerGraph 4.x GSQL syntax (CREATE QUERY ... FOR GRAPH ... { ... }).
- Every query MUST start with `CREATE QUERY <name>(...) FOR GRAPH <graph> {`
  and end with `}`.
- Use ONLY the vertex types and edge types provided in the schema. Don't
  invent attributes — only reference attributes that exist on the given
  vertices.
- Prefer `PRINT` statements with a tuple/heap accumulator so results are
  inspectable.
- Reuse the user's business_questions verbatim when naming queries when
  possible (snake_case the question).
- Each query has ONE clear job — don't overload.

CRITICAL — edge directions:
- Each edge in the schema has `from` and `to` vertex types. The edge name
  ONLY traverses from `from` → `to`. Going the other way requires the
  REVERSE edge (named in pattern Vertex_VERB_OtherVertex; you'll see both
  edges in the schema list when reverse-paired).
- Example: if the schema has edge `Account_MADE_Transaction` with
  from=Account, to=Transaction, you write:
    `FROM acc:a -(Account_MADE_Transaction:e)- Transaction:t`
  going from Account; you CANNOT use this edge starting from Transaction.
  Look for the paired `Transaction_INITIATED_BY_Account` (the reverse) for
  Transaction→Account traversal.
- When picking an edge for a FROM clause, verify the `from` field matches
  your source vertex type — otherwise you'll get a TYP-111 type check error
  and the query is rejected as a draft.

CRITICAL — query parameters:
- If you parameterize a query, prefer scalar types (STRING, INT, FLOAT,
  DATETIME) over VERTEX<T> — VERTEX parameters can be hard to pass from
  the chat. For "find for a specific X" queries, take the X's primary_id
  as a STRING/INT and look it up inside the query with
  `Start = {X.* WHERE x.primary_id == param}`.

Return ONLY a JSON object with this exact shape:

{
  "queries": [
    {
      "name": "snake_case_name",
      "description": "What this query does, 1 sentence.",
      "business_question": "The user's question this answers, verbatim if possible.",
      "gsql": "CREATE QUERY name(...) FOR GRAPH g { ... }",
      "expected_output_description": "What the result tuple/heap looks like."
    },
    ...
  ]
}

No prose outside the JSON. No code fences."""


def _schema_summary_for_prompt(schema: Schema, graph_name: str) -> dict[str, Any]:
    """Compact representation of the schema for the LLM prompt."""
    return {
        "graph_name": graph_name,
        "use_case": schema.use_case.value,
        "vertices": [
            {
                "name": v.name,
                "primary_id": v.primary_id,
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


def is_available() -> bool:
    """Check whether the Gemini SDK + API key are wired up."""
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
        return StarterQuerySet.model_validate(parsed)
    except Exception as exc:  # noqa: BLE001
        log.warning("Invalid StarterQuerySet shape: %s", exc)
        return None


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


async def generate_starter_queries(
    session: Any,
    schema: Schema,
    graph_name: str,
    business_context: BusinessContext | None = None,
    *,
    max_retries: int = 1,
) -> StarterQuerySet:
    """Generate + dry-run-validate a set of starter queries.

    Returns a StarterQuerySet whose `queries[*].validated` flag indicates
    which queries passed the INTERPRET dry-run. Failed queries keep their
    `validation_error` populated so the UI can show why.
    """
    if not is_available():
        log.info("Gemini not available — returning empty starter query set.")
        return StarterQuerySet()

    qs = _generate_with_gemini(schema, graph_name, business_context)
    if qs is None:
        return StarterQuerySet()

    # Dry-run each query against the live graph
    for q in qs.queries:
        ok, err = await _dry_run_query(session, graph_name, q)
        q.validated = ok
        q.validation_error = err

    # Retry once for failed queries, in aggregate, with the errors in context
    failed = [q for q in qs.queries if not q.validated]
    if failed and max_retries > 0:
        error_summary = "\n".join(
            f"- {q.name}: {q.validation_error}" for q in failed
        )
        prior_attempt = json.dumps(
            {"queries": [q.model_dump() for q in qs.queries]}, indent=2
        )
        retried = _generate_with_gemini(
            schema,
            graph_name,
            business_context,
            retry_with_error=error_summary,
            prior_attempt=prior_attempt,
        )
        if retried:
            # Re-validate retried set
            for q in retried.queries:
                ok, err = await _dry_run_query(session, graph_name, q)
                q.validated = ok
                q.validation_error = err
            # Prefer the retried set if it has MORE validated queries
            retried_ok = sum(1 for q in retried.queries if q.validated)
            original_ok = sum(1 for q in qs.queries if q.validated)
            if retried_ok > original_ok:
                qs = retried

    return qs
