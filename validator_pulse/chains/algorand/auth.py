from __future__ import annotations

import re
from pathlib import Path

from validator_pulse.config import Settings

_TOKEN_IN_TEXT = re.compile(
    r"(?i)(x-algo-api-token|algod\.token|api[_-]?token|bearer)\s*[:=]?\s*[\w\-./+=]+"
)


def resolve_algod_token(settings: Settings) -> str | None:
    """Load algod API token from env or token file. Never log the return value."""
    direct = (settings.algorand_algod_token or "").strip()
    if direct:
        return direct
    path = (settings.algorand_algod_token_file or "").strip()
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def redact_secrets(message: str, *secrets: str | None) -> str:
    """Strip algod credentials from error/log text."""
    out = message or ""
    for secret in secrets:
        if secret and secret.strip():
            out = out.replace(secret.strip(), "[REDACTED]")
    out = _TOKEN_IN_TEXT.sub(r"\1=[REDACTED]", out)
    return out
