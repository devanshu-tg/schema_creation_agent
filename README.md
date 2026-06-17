---
title: Autograph Backend
emoji: 🐯
colorFrom: orange
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: TigerGraph schema-design agent (FastAPI + MCP + Claude Sonnet)
---

# Autograph — TigerGraph Schema Creation Agent

The FastAPI backend for **Autograph** (a.k.a. "Savanna AI") — an LLM-driven
graph schema designer for TigerGraph. The agent runs a ReAct-style tool
loop that profiles your data, recognizes industry patterns, designs a
schema, deploys it to a live TigerGraph Savanna instance, and writes
GSQL queries against it.

## What this Hugging Face Space is

This Space hosts only the **backend**. The Next.js frontend lives on
Vercel and points its `NEXT_PUBLIC_API_BASE` at this Space's URL.

- Frontend (Vercel): https://schema-creation-agent.vercel.app
- Backend (this Space): `https://<user>-<space>.hf.space`
- Source code: https://github.com/devanshu-tg/schema_creation_agent

## Endpoints

- `GET  /api/health` — liveness probe
- `GET  /api/use-cases` — list supported industry patterns
- `POST /api/workspaces` — create a workspace
- `POST /api/workspaces/{id}/files` — upload CSV(s)
- `POST /api/workspaces/{id}/chat/stream` — SSE chat with the agent
- `POST /api/workspaces/{id}/deploy/stream` — deploy schema to TigerGraph
- `POST /api/workspaces/{id}/queries/generate` — generate starter GSQL
- `POST /api/workspaces/{id}/queries/install` — install a GSQL query

## Environment variables (set in Space settings → Variables and secrets)

Required:
- `LLM_PROVIDER` — `openrouter` or `gemini`
- `OPENROUTER_API_KEY` — your OpenRouter key (when `LLM_PROVIDER=openrouter`)
- `OPENROUTER_MODEL` — model slug (default: `anthropic/claude-sonnet-4.6`)
- `GEMINI_API_KEY` — your Gemini key (when `LLM_PROVIDER=gemini`)
- `TG_HOST` — your TigerGraph Savanna URL
- `TG_GRAPHNAME` — graph name (e.g. `mcp_demo`)
- `TG_SECRET` — GSQL secret
- `TG_TGCLOUD` — `true` for TG Cloud instances

Optional:
- `GEMINI_MODEL` — defaults to `gemini-3.1-pro-preview`
- `OPENROUTER_REFERER` / `OPENROUTER_APP_TITLE` — OpenRouter attribution
- `MAX_PROFILE_ROWS` — cap CSV rows read (default 50,000; 0 = read all)

## Running locally

```bash
uv sync --extra web --extra llm --extra tigergraph
uv run uvicorn tg_schema_agent.api.app:app --host 127.0.0.1 --port 8001
```

Frontend:
```bash
cd frontend && npm install && npm run dev
```
