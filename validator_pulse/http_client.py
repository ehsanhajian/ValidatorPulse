from __future__ import annotations

import logging
import ssl
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal
from urllib.parse import urlparse

import httpx

from validator_pulse.config import Settings

logger = logging.getLogger(__name__)

TransportKind = Literal["tls", "connection", "timeout", "http", "other"]


@dataclass(frozen=True)
class ParsedRpcUrl:
    raw: str
    scheme: str
    host: str
    port: int | None
    path: str
    uses_tls: bool

    @property
    def origin(self) -> str:
        netloc = self.host
        if self.port is not None:
            netloc = f"{self.host}:{self.port}"
        return f"{self.scheme}://{netloc}"

    @property
    def base_url(self) -> str:
        path = self.path.rstrip("/") if self.path and self.path != "/" else ""
        return f"{self.origin}{path}"


@dataclass(frozen=True)
class RpcHttpConfig:
    """Shared TLS / timeout policy for chain RPC clients."""

    verify: bool | str = True
    timeout: float = 8.0
    insecure: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> RpcHttpConfig:
        insecure = bool(settings.rpc_tls_insecure)
        if insecure:
            verify: bool | str = False
        elif settings.rpc_tls_ca_bundle and settings.rpc_tls_ca_bundle.strip():
            verify = settings.rpc_tls_ca_bundle.strip()
        else:
            verify = bool(settings.rpc_tls_verify)
        timeout = float(settings.rpc_connect_timeout_seconds or 8.0)
        return cls(verify=verify, timeout=timeout, insecure=insecure)


_rpc_http_config: ContextVar[RpcHttpConfig | None] = ContextVar(
    "rpc_http_config", default=None
)
_insecure_warned = False


def get_rpc_http_config() -> RpcHttpConfig:
    return _rpc_http_config.get() or RpcHttpConfig()


def bind_rpc_http_config(config: RpcHttpConfig):
    """Bind TLS/timeout config for the current task (adapters should call this)."""
    global _insecure_warned
    if config.insecure and not _insecure_warned:
        logger.warning(
            "RPC_TLS_INSECURE=true — TLS certificate verification is disabled "
            "(lab use only)."
        )
        _insecure_warned = True
    return _rpc_http_config.set(config)


def reset_rpc_http_config(token) -> None:
    _rpc_http_config.reset(token)


def normalize_rpc_url(url: str) -> str:
    """Return a full URL with scheme; preserve host, port, and path prefix."""
    text = (url or "").strip()
    if not text:
        raise ValueError("RPC URL is empty")
    if "://" not in text:
        text = f"http://{text}"
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported RPC URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError(f"RPC URL missing host: {url!r}")
    # Rebuild without trailing slash so callers can append paths safely.
    path = parsed.path or ""
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    netloc = parsed.netloc
    return f"{parsed.scheme}://{netloc}{path}"


def parse_rpc_url(url: str) -> ParsedRpcUrl:
    normalized = normalize_rpc_url(url)
    parsed = urlparse(normalized)
    assert parsed.hostname is not None
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    path = parsed.path or ""
    if path == "/":
        path = ""
    return ParsedRpcUrl(
        raw=normalized,
        scheme=parsed.scheme,
        host=parsed.hostname,
        port=port,
        path=path,
        uses_tls=parsed.scheme == "https",
    )


class RpcTransportError(Exception):
    """Transport-layer failure reaching an RPC endpoint (before JSON-RPC/API logic)."""

    def __init__(self, kind: TransportKind, message: str, *, cause: BaseException | None = None):
        self.kind = kind
        self.cause = cause
        super().__init__(message)


def classify_transport_error(exc: BaseException) -> tuple[TransportKind, str]:
    """Map httpx/ssl exceptions to a stable kind + operator-facing message."""
    if isinstance(exc, RpcTransportError):
        return exc.kind, str(exc)

    # Walk the cause chain for SSL / socket details.
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__  # type: ignore[assignment]

    if any(isinstance(e, (ssl.SSLError, ssl.SSLCertVerificationError)) for e in chain):
        ssl_detail = next(
            (
                str(e).strip()
                for e in chain
                if isinstance(e, (ssl.SSLError, ssl.SSLCertVerificationError))
                and str(e).strip()
            ),
            None,
        )
        detail = ssl_detail or _first_message(chain) or "TLS handshake failed"
        return "tls", f"TLS error: {detail}"

    text = " | ".join(str(e) for e in chain if str(e)).lower()
    if "certificate" in text or "ssl" in text or "tls" in text:
        detail = _first_message(chain) or str(exc)
        return "tls", f"TLS error: {detail}"

    if isinstance(exc, httpx.TimeoutException) or any(
        isinstance(e, httpx.TimeoutException) for e in chain
    ):
        return "timeout", f"Connection timed out: {_first_message(chain) or exc}"

    if isinstance(exc, httpx.ConnectError) or any(
        isinstance(e, httpx.ConnectError) for e in chain
    ):
        detail = _first_message(chain) or str(exc)
        lowered = detail.lower()
        if "certificate" in lowered or "ssl" in lowered or "tls" in lowered:
            return "tls", f"TLS error: {detail}"
        return "connection", f"Connection failed: {detail}"

    if isinstance(exc, httpx.HTTPStatusError):
        return "http", f"HTTP {exc.response.status_code}: {exc}"

    return "other", str(exc)


def format_transport_error(exc: BaseException) -> str:
    kind, message = classify_transport_error(exc)
    if kind == "tls":
        return message
    if kind == "connection":
        return message
    if kind == "timeout":
        return message
    return message


def _first_message(chain: list[BaseException]) -> str:
    for item in chain:
        text = str(item).strip()
        if text:
            return text
    return ""


def create_async_client(
    *,
    timeout: float | None = None,
    config: RpcHttpConfig | None = None,
) -> httpx.AsyncClient:
    cfg = config or get_rpc_http_config()
    return httpx.AsyncClient(
        timeout=timeout if timeout is not None else cfg.timeout,
        verify=cfg.verify,
        follow_redirects=True,
    )


@asynccontextmanager
async def async_rpc_client(
    *,
    timeout: float | None = None,
    config: RpcHttpConfig | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    client = create_async_client(timeout=timeout, config=config)
    try:
        yield client
    finally:
        await client.aclose()


async def probe_rpc_endpoint(
    url: str,
    *,
    config: RpcHttpConfig | None = None,
    timeout: float | None = None,
) -> ParsedRpcUrl:
    """Check TCP reachability and TLS handshake for an RPC base URL.

    Performs a lightweight HTTPS/HTTP request to the origin (path ignored for the
    probe target host). Raises ``RpcTransportError`` on failure.
    """
    parsed = parse_rpc_url(url)
    cfg = config or get_rpc_http_config()
    # Probe the origin root; many RPC servers return 404/405 here — that's fine.
    probe_url = parsed.origin + "/"
    try:
        async with async_rpc_client(timeout=timeout or min(cfg.timeout, 5.0), config=cfg) as client:
            await client.get(probe_url)
    except Exception as exc:  # noqa: BLE001
        kind, message = classify_transport_error(exc)
        # HTTP 4xx/5xx still proves TCP+TLS worked.
        if isinstance(exc, httpx.HTTPStatusError):
            return parsed
        raise RpcTransportError(kind, message, cause=exc) from exc
    return parsed


def rpc_http_config_dict(config: RpcHttpConfig | None = None) -> dict[str, Any]:
    cfg = config or get_rpc_http_config()
    return {
        "verify": cfg.verify,
        "timeout": cfg.timeout,
        "insecure": cfg.insecure,
    }
