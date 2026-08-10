from __future__ import annotations

import logging

import uvicorn

from validator_pulse.auth import warn_if_exposed_without_auth
from validator_pulse.config import get_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    warn_if_exposed_without_auth(settings)
    uvicorn.run(
        "validator_pulse.web:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )


if __name__ == "__main__":
    main()
