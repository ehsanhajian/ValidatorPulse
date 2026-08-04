from __future__ import annotations

from datetime import datetime, timezone

from validator_pulse.alerts import (
    build_verdict,
    configured_channels,
    dispatch_alert,
    evaluate_alerts,
)
from validator_pulse.chains import UnsupportedChainError, get_adapter
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings, get_settings
from validator_pulse.models import PulseSnapshot
from validator_pulse.scoring import aggregate_fleet_metrics
from validator_pulse.store import get_alert_history, get_snapshot, set_snapshot


async def collect_pulse(
    settings: Settings | None = None, *, dispatch_alerts: bool = False
) -> PulseSnapshot:
    settings = settings or get_settings()
    collected_at = datetime.now(timezone.utc).isoformat()
    channels = configured_channels(settings)

    try:
        adapter = get_adapter(settings.chain)
    except UnsupportedChainError:
        raise

    infrastructure = collect_infrastructure()
    collection = await adapter.collect(settings, infrastructure)
    consensus = collection.consensus
    validators = collection.operators
    infrastructure = collection.infrastructure
    demo_mode = adapter.is_demo(settings)

    metrics = aggregate_fleet_metrics(validators)
    partial = PulseSnapshot(
        collected_at=collected_at,
        demo_mode=demo_mode,
        chain=adapter.name,
        chain_display_name=adapter.display_name,
        operator_label=adapter.operator_label,
        verdict=build_verdict(
            {
                "consensus": consensus,
                "infrastructure": infrastructure,
                "validators": validators,
                "metrics": metrics,
            }
        ),
        validators=validators,
        consensus=consensus,
        infrastructure=infrastructure,
        metrics=metrics,
        recent_alerts=[],
        configured_channels=channels,
    )

    fresh_alerts = evaluate_alerts(partial, settings)
    if dispatch_alerts and fresh_alerts and channels:
        for alert in fresh_alerts:
            results = await dispatch_alert(settings, alert)
            alert.delivered = any(r.ok for r in results)

    recent = fresh_alerts if fresh_alerts else get_alert_history()[:5]
    snapshot = partial.model_copy(update={"recent_alerts": recent})
    set_snapshot(snapshot)
    return snapshot


async def get_or_collect_pulse(settings: Settings | None = None) -> PulseSnapshot:
    settings = settings or get_settings()
    existing = get_snapshot()
    if existing:
        try:
            age = (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(existing.collected_at)
            ).total_seconds()
        except ValueError:
            age = settings.poll_interval_seconds + 1
        if age < settings.poll_interval_seconds:
            return existing
    return await collect_pulse(settings, dispatch_alerts=True)
