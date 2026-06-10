"""TigerGraph Schema Creation Agent — deterministic core."""

__version__ = "0.1.0"

# Auto-load .env from the project root if present, so GEMINI_API_KEY etc.
# are picked up by both the CLI and the API server. Idempotent and safe.
try:
    from pathlib import Path

    from dotenv import load_dotenv

    _root = Path(__file__).resolve().parent.parent.parent
    _env = _root / ".env"
    if _env.exists():
        load_dotenv(_env, override=False)
except ImportError:
    # python-dotenv only installed with [tigergraph] or [llm] extras.
    pass
