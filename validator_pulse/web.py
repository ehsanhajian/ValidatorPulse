from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from validator_pulse.alerts import configured_channels, dispatch_alert
from validator_pulse.chains import UnsupportedChainError
from validator_pulse.config import get_settings
from validator_pulse.metrics import to_prometheus
from validator_pulse.models import AlertEvent
from validator_pulse.pulse import collect_pulse, get_or_collect_pulse

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))

app = FastAPI(
    title="ValidatorPulse",
    description="Keep blockchain validators healthy and prevent downtime and slashing.",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def _status_color(status: str) -> str:
    return {
        "healthy": "var(--ok)",
        "degraded": "var(--warn)",
        "critical": "var(--crit)",
    }.get(status, "var(--ink-muted)")


def _format_balance(amount: int, snapshot) -> str:
    from validator_pulse.chains.polkadot.tokens import TokenInfo, format_token_amount

    token = TokenInfo(
        symbol=getattr(snapshot, "reward_token_symbol", "TOKEN"),
        decimals=int(getattr(snapshot, "reward_token_decimals", 0) or 0),
        base_unit=getattr(snapshot, "reward_token_base_unit", "unit"),
    )
    return format_token_amount(amount, token)


def _format_rewards(amount: int, snapshot) -> str:
    if (
        getattr(snapshot, "reward_token_base_unit", "") == "gwei"
        and getattr(snapshot, "reward_token_symbol", "") == "ETH"
    ):
        if abs(amount) < 1_000_000:
            return f"{amount:,} gwei"
        return f"{amount / 1e9:.6f} ETH"
    return _format_balance(amount, snapshot)


TEMPLATES.env.globals["status_color"] = _status_color
TEMPLATES.env.globals["format_balance"] = _format_balance
TEMPLATES.env.globals["format_rewards"] = _format_rewards
TEMPLATES.env.globals["now"] = lambda: datetime.now(timezone.utc)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    try:
        snapshot = await collect_pulse(dispatch_alerts=False)
    except UnsupportedChainError as exc:
        return HTMLResponse(
            f"<h1>Unsupported chain</h1><p>{exc}</p>",
            status_code=400,
        )
    return TEMPLATES.TemplateResponse(
        request,
        "dashboard.html",
        {"snapshot": snapshot, "channels": ["telegram", "slack", "discord", "webhook", "pagerduty"]},
    )


@app.get("/api/status")
async def api_status():
    try:
        snapshot = await get_or_collect_pulse()
    except UnsupportedChainError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return snapshot.model_dump(mode="json")


@app.get("/api/metrics")
async def api_metrics():
    try:
        snapshot = await get_or_collect_pulse()
    except UnsupportedChainError as exc:
        return PlainTextResponse(f"# error {exc}\n", status_code=400)
    return PlainTextResponse(
        to_prometheus(snapshot),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.post("/api/collect")
async def api_collect():
    try:
        snapshot = await collect_pulse(dispatch_alerts=True)
    except UnsupportedChainError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return {
        "ok": True,
        "collected_at": snapshot.collected_at,
        "snapshot": snapshot.model_dump(mode="json"),
    }


@app.post("/api/alerts/test")
async def api_alerts_test():
    settings = get_settings()
    channels = configured_channels(settings)
    if not channels:
        return JSONResponse(
            {
                "ok": False,
                "error": (
                    "No alert channels configured. Set Telegram, Slack, Discord, "
                    "webhook, or PagerDuty env vars."
                ),
            },
            status_code=400,
        )

    alert = AlertEvent(
        id=f"test-{int(datetime.now(timezone.utc).timestamp())}",
        severity="info",
        title="ValidatorPulse test alert",
        message="This is a test notification from ValidatorPulse. Alerting pipeline is reachable.",
        source="system",
        created_at=datetime.now(timezone.utc).isoformat(),
        channels=channels,
    )
    results = await dispatch_alert(settings, alert, channels)
    alert.delivered = any(r.ok for r in results)
    return {
        "ok": alert.delivered,
        "alert": alert.model_dump(mode="json"),
        "results": [{"channel": r.channel, "ok": r.ok, "detail": r.detail} for r in results],
    }
