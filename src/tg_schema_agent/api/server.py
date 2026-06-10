"""tg-schema-server CLI entrypoint.

Runs the FastAPI app via uvicorn. Defaults to localhost:8000.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("TG_SCHEMA_HOST", "127.0.0.1")
    port = int(os.environ.get("TG_SCHEMA_PORT", "8000"))
    reload_flag = os.environ.get("TG_SCHEMA_RELOAD", "0") == "1"
    uvicorn.run(
        "tg_schema_agent.api.app:app",
        host=host,
        port=port,
        reload=reload_flag,
        log_level=os.environ.get("TG_SCHEMA_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
