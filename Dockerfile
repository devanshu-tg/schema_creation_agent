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

# Copy the source code + the pattern YAMLs (loaded at startup).
COPY src/ ./src/
COPY patterns/ ./patterns/

# Cloud Run sets PORT (default 8080). Honor it; default to 8001 for local.
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

# Single worker is fine — the chat-agent loop is async and Cloud Run
# scales horizontally by spinning up more instances when concurrency > 80.
CMD ["sh", "-c", "uv run --no-dev uvicorn tg_schema_agent.api.app:app --host 0.0.0.0 --port ${PORT}"]
