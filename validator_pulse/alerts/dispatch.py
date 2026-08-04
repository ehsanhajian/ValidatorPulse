from __future__ import annotations

from dataclasses import dataclass

import httpx

from validator_pulse.config import Settings
from validator_pulse.models import AlertChannelName, AlertEvent


@dataclass
class DeliveryResult:
    channel: AlertChannelName
    ok: bool
    detail: str


async def deliver_telegram(settings: Settings, alert: AlertEvent) -> DeliveryResult:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return DeliveryResult("telegram", False, "not configured")
    text = (
        f"*[ValidatorPulse]* {alert.severity.upper()}\n"
        f"*{alert.title}*\n{alert.message}"
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        )
    return DeliveryResult("telegram", res.is_success, "sent" if res.is_success else f"HTTP {res.status_code}")


async def deliver_slack(settings: Settings, alert: AlertEvent) -> DeliveryResult:
    url = settings.slack_webhook_url
    if not url:
        return DeliveryResult("slack", False, "not configured")
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            url,
            json={
                "text": (
                    f"[ValidatorPulse] {alert.severity.upper()}: "
                    f"{alert.title}\n{alert.message}"
                )
            },
        )
    return DeliveryResult("slack", res.is_success, "sent" if res.is_success else f"HTTP {res.status_code}")


async def deliver_discord(settings: Settings, alert: AlertEvent) -> DeliveryResult:
    url = settings.discord_webhook_url
    if not url:
        return DeliveryResult("discord", False, "not configured")
    color = {"critical": 0xC45C26, "warning": 0xC4922A, "info": 0x2A7A6B}[alert.severity]
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            url,
            json={
                "embeds": [
                    {
                        "title": alert.title,
                        "description": alert.message,
                        "color": color,
                        "footer": {"text": f"ValidatorPulse · {alert.source}"},
                        "timestamp": alert.created_at,
                    }
                ]
            },
        )
    return DeliveryResult("discord", res.is_success, "sent" if res.is_success else f"HTTP {res.status_code}")


async def deliver_webhook(settings: Settings, alert: AlertEvent) -> DeliveryResult:
    url = settings.webhook_url
    if not url:
        return DeliveryResult("webhook", False, "not configured")
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            url,
            json={"source": "validator-pulse", "alert": alert.model_dump()},
        )
    return DeliveryResult("webhook", res.is_success, "sent" if res.is_success else f"HTTP {res.status_code}")


async def deliver_pagerduty(settings: Settings, alert: AlertEvent) -> DeliveryResult:
    routing_key = settings.pagerduty_routing_key
    if not routing_key:
        return DeliveryResult("pagerduty", False, "not configured")
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(
            "https://events.pagerduty.com/v2/enqueue",
            json={
                "routing_key": routing_key,
                "event_action": "trigger",
                "payload": {
                    "summary": f"{alert.title}: {alert.message}",
                    "severity": "critical" if alert.severity == "critical" else "warning",
                    "source": "validator-pulse",
                    "component": alert.source,
                    "group": "validators",
                    "class": alert.severity,
                },
            },
        )
    return DeliveryResult(
        "pagerduty",
        res.is_success,
        "sent" if res.is_success else f"HTTP {res.status_code}",
    )


_DELIVERERS = {
    "telegram": deliver_telegram,
    "slack": deliver_slack,
    "discord": deliver_discord,
    "webhook": deliver_webhook,
    "pagerduty": deliver_pagerduty,
}


async def dispatch_alert(
    settings: Settings,
    alert: AlertEvent,
    channels: list[AlertChannelName] | None = None,
) -> list[DeliveryResult]:
    targets = channels or alert.channels
    results: list[DeliveryResult] = []
    for channel in targets:
        try:
            results.append(await _DELIVERERS[channel](settings, alert))
        except Exception as exc:  # noqa: BLE001
            results.append(DeliveryResult(channel, False, str(exc)))
    return results
