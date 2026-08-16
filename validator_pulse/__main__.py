from __future__ import annotations

import logging
import os

import uvicorn

from validator_pulse.config import get_settings


def _reload_enabled(host: str) -> bool:
    raw = os.environ.get("UVICORN_RELOAD")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return (host or "").strip().lower() in {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    uvicorn.run(
        "validator_pulse.web:app",
        host=settings.host,
        port=settings.port,
        reload=_reload_enabled(settings.host),
    )


if __name__ == "__main__":
    main()
