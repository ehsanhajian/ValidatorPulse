from __future__ import annotations

from datetime import datetime, timezone

from validator_pulse.alerts import (
    build_verdict,
    configured_channels,
    dispatch_alert,
    evaluate_alerts,
)
from validator_pulse.chains import UnsupportedChainError, get_adapter
from validator_pulse.chains.polkadot.tokens import resolve_reward_token
from validator_pulse.collectors.infrastructure import collect_infrastructure
from validator_pulse.config import Settings, get_settings
from validator_pulse.identity import enrich_operator_names
from validator_pulse.models import PulseSnapshot
from validator_pulse.scoring import aggregate_fleet_metrics
from validator_pulse.store import get_alert_history, get_snapshot, set_snapshot


def _snapshot_parachain_id(adapter: object, settings: Settings) -> int | None:
    """Parachain id is a Polkadot collator label only — never leak onto other chains."""
    if getattr(adapter, "name", "") != "polkadot":
        return None
    if getattr(adapter, "role", None) == "validator":
        return None
    return settings.parachain_id


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

    if hasattr(adapter, "configure"):
        adapter.configure(settings)

    infrastructure = collect_infrastructure()
    collection = await adapter.collect(settings, infrastructure)
    consensus = collection.consensus
    validators = collection.operators
    infrastructure = collection.infrastructure
    demo_mode = adapter.is_demo(settings)

    parachain_id = _snapshot_parachain_id(adapter, settings)
    await enrich_operator_names(
        validators,
        chain=adapter.name,
        parachain_id=parachain_id,
        enabled=settings.fetch_operator_names,
        subscan_api_key=settings.subscan_api_key,
        beaconcha_base_url=settings.beaconcha_base_url,
        beaconcha_api_key=settings.beaconcha_api_key,
        beacon_api_url=settings.beacon_api_url if not demo_mode else None,
        rated_api_key=settings.rated_api_key,
        rated_api_base_url=settings.rated_api_base_url,
        rated_network=settings.rated_network,
        ens_lookup_enabled=settings.ens_lookup_enabled,
        ens_api_key=settings.ens_api_key,
        ens_api_base_url=settings.ens_api_base_url,
    )

    metrics = aggregate_fleet_metrics(validators)
    token = resolve_reward_token(
        chain=adapter.name,
        parachain_id=parachain_id,
        symbol_override=settings.reward_token_symbol,
        decimals_override=settings.reward_token_decimals,
        cosmos_profile=settings.cosmos_profile,
    )
    risk_kind = getattr(adapter, "risk_kind", "slashing")
    risk_label = getattr(adapter, "risk_label", "Slashing risk")
    primary_duty_label = getattr(adapter, "primary_duty_label", "Attestations")
    secondary_duty_label = getattr(adapter, "secondary_duty_label", "Proposals")
    missed_duty_label = getattr(adapter, "missed_duty_label", "Missed attestations")
    consensus_node_label = getattr(adapter, "consensus_node_label", "Beacon")

    partial = PulseSnapshot(
        collected_at=collected_at,
        demo_mode=demo_mode,
        schema_version=2,
        chain=adapter.name,
        chain_display_name=adapter.display_name,
        operator_label=adapter.operator_label,
        risk_kind=risk_kind,
        risk_label=risk_label,
        primary_duty_label=primary_duty_label,
        secondary_duty_label=secondary_duty_label,
        missed_duty_label=missed_duty_label,
        consensus_node_label=consensus_node_label,
        parachain_id=parachain_id,
        reward_token_symbol=token.symbol,
        reward_token_decimals=token.decimals,
        reward_token_base_unit=token.base_unit,
        verdict=build_verdict(
            {
                "consensus": consensus,
                "infrastructure": infrastructure,
                "validators": validators,
                "metrics": metrics,
                "operator_label": adapter.operator_label,
                "risk_label": risk_label,
                "primary_duty_label": primary_duty_label,
                "missed_duty_label": missed_duty_label,
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
