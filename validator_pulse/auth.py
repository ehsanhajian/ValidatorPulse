from __future__ import annotations

import base64
import logging
import secrets
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from validator_pulse.config import Settings, get_settings

logger = logging.getLogger(__name__)

_PUBLIC_PREFIXES = ("/static",)


def web_auth_enabled(settings: Settings | None = None) -> bool:
    """True when both username and password are configured."""
    settings = settings or get_settings()
    user = (settings.web_auth_username or "").strip()
    password = settings.web_auth_password or ""
    return bool(user and password)


def metrics_token_enabled(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool((settings.web_metrics_token or "").strip())


def warn_if_exposed_without_auth(settings: Settings | None = None) -> None:
    """Log a warning when binding off-loopback without panel credentials."""
    settings = settings or get_settings()
    host = (settings.host or "").strip().lower()
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if loopback or web_auth_enabled(settings):
        return
    logger.warning(
        "WEB_AUTH_USERNAME/PASSWORD are unset while HOST=%s — the dashboard and "
        "APIs are reachable without credentials. Set web auth before exposing "
        "ValidatorPulse beyond localhost.",
        settings.host,
    )


def _secure_equal(left: str, right: str) -> bool:
    """Constant-time string compare; never logs values."""
    return secrets.compare_digest(
        left.encode("utf-8"),
        right.encode("utf-8"),
    )


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, rest = header.partition(" ")
    if scheme.lower() != "basic" or not rest:
        return None
    try:
        decoded = base64.b64decode(rest.strip()).decode("utf-8")
    except Exception:  # noqa: BLE001 — treat as missing/invalid auth
        return None
    if ":" not in decoded:
        return None
    username, _, password = decoded.partition(":")
    return username, password


def _extract_metrics_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if header:
        scheme, _, rest = header.partition(" ")
        if scheme.lower() == "bearer" and rest.strip():
            return rest.strip()
    token_header = request.headers.get("x-metrics-token") or request.headers.get(
        "X-Metrics-Token"
    )
    if token_header and token_header.strip():
        return token_header.strip()
    query_token = request.query_params.get("token")
    if query_token and query_token.strip():
        return query_token.strip()
    return None


def basic_auth_ok(request: Request, settings: Settings) -> bool:
    creds = _basic_credentials(request)
    if creds is None:
        return False
    username, password = creds
    expected_user = (settings.web_auth_username or "").strip()
    expected_pass = settings.web_auth_password or ""
    # Compare both even if user mismatches to avoid leaking which field failed.
    user_ok = _secure_equal(username, expected_user)
    pass_ok = _secure_equal(password, expected_pass)
    return user_ok and pass_ok


def metrics_token_ok(request: Request, settings: Settings) -> bool:
    expected = (settings.web_metrics_token or "").strip()
    if not expected:
        return False
    provided = _extract_metrics_token(request)
    if provided is None:
        return False
    return _secure_equal(provided, expected)


def _unauthorized_basic() -> Response:
    return PlainTextResponse(
        "Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="ValidatorPulse"'},
    )


def _unauthorized_metrics() -> Response:
    return PlainTextResponse(
        "Unauthorized",
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer realm="ValidatorPulse-metrics"'},
    )


def _is_public_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _PUBLIC_PREFIXES)


def _is_metrics_path(path: str) -> bool:
    return path == "/api/metrics" or path.startswith("/api/metrics/")


class WebAuthMiddleware(BaseHTTPMiddleware):
    """Optional HTTP Basic auth for the panel + APIs; metrics token for scrapers."""

    def __init__(
        self,
        app,
        settings_factory: Callable[[], Settings] | None = None,
    ) -> None:
        super().__init__(app)
        self._settings_factory = settings_factory or get_settings

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_public_path(path):
            return await call_next(request)

        settings = self._settings_factory()
        auth_on = web_auth_enabled(settings)
        metrics_path = _is_metrics_path(path)
        token_on = metrics_token_enabled(settings)

        if not auth_on:
            # Optional metrics-only token when panel auth is off.
            if metrics_path and token_on and not metrics_token_ok(request, settings):
                return _unauthorized_metrics()
            return await call_next(request)

        if metrics_path and token_on and metrics_token_ok(request, settings):
            return await call_next(request)

        if basic_auth_ok(request, settings):
            return await call_next(request)

        return _unauthorized_basic()
