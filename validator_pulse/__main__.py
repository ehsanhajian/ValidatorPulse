from __future__ import annotations

import uvicorn

from validator_pulse.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "validator_pulse.web:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )


if __name__ == "__main__":
    main()
