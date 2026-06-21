# Backend container for Cloud Run.
# Builds the FastAPI app + tigergraph-mcp + Gemini SDK into a slim image.
#
# Frontend (Next.js) deploys separately to Vercel — NOT in this image.

FROM python:3.12-slim AS base

# Install uv (fast Python package manager). Using the official image stage
# instead of pip-installing uv inside Python (smaller, faster).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# System deps:
#   - build-essential / gcc for pandas/pyarrow wheels (some platforms)
#   - curl for healthchecks if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Lockfile-first install for layer caching: if the lockfile doesn't change,
# Docker reuses the dep layer on every code edit.
COPY pyproject.toml uv.lock ./

# Install ALL required extras: web (FastAPI), llm (Gemini), tigergraph (MCP).
# --frozen ensures we install exactly what's in uv.lock.
RUN uv sync --extra web --extra llm --extra tigergraph --frozen --no-dev

# Copy the source code + the pattern YAMLs + rules config (loaded at startup).
COPY src/ ./src/
COPY patterns/ ./patterns/
COPY rules/ ./rules/

# Port:
#   - Render sets $PORT explicitly (typically 10000) — we honor it.
#   - Hugging Face Spaces sets $PORT to whatever app_port in README.md says
#     (7860 by default for Docker Spaces).
#   - Cloud Run sets $PORT to 8080 by default.
# Default to 7860 so a no-env-var run works on HF; the CMD honors $PORT
# whenever it's set so Render/Cloud Run override transparently.
ENV PORT=7860
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Bake the NON-SECRET LLM config so HF doesn't need these as Space vars.
# (Secrets — GEMINI_API_KEY, TG_HOST, TG_SECRET — must still be set in the
# Space's Settings → Variables and secrets; they're never committed.)
# NOTE: a Space-level env var of the same name OVERRIDES these — so if you
# previously set LLM_PROVIDER=openrouter on the Space, remove it.
ENV LLM_PROVIDER=gemini
ENV GEMINI_MODEL=gemini-3.1-pro-preview
ENV TG_GRAPHNAME=mcp_demo
ENV TG_TGCLOUD=true

EXPOSE 7860

# Single worker is fine — the chat-agent loop is async and Cloud Run
# scales horizontally by spinning up more instances when concurrency > 80.
CMD ["sh", "-c", "uv run --no-dev uvicorn tg_schema_agent.api.app:app --host 0.0.0.0 --port ${PORT}"]
