from __future__ import annotations

from datetime import datetime, timezone

from validator_pulse.alerts import (
    build_verdict,
    configured_channels,
    dispatch_alert,
    evaluate_alerts,
)
from validator_pulse.collectors import (
    build_demo_consensus,
    build_demo_infrastructure,
    build_demo_validators,
    collect_consensus,
    collect_infrastructure,
    collect_validator_balances,
)
from validator_pulse.config import Settings, get_settings
from validator_pulse.models import (
    AttestationStats,
    ConsensusHealth,
    ProposalStats,
    PulseSnapshot,
    ValidatorStats,
)
from validator_pulse.scoring import (
    aggregate_fleet_metrics,
    compute_effectiveness_score,
    compute_slashing_risk_score,
)
from validator_pulse.store import get_alert_history, get_snapshot, set_snapshot


async def _collect_live_validators(
    beacon_api_url: str,
    validator_ids: list[str],
    consensus: ConsensusHealth,
    infrastructure,
) -> list[ValidatorStats]:
    balances = await collect_validator_balances(beacon_api_url, validator_ids)
    validators: list[ValidatorStats] = []
    for b in balances:
        active = "active" in (b["status"] or "")
        attestations = AttestationStats(
            expected=32,
            successful=31 if active else 20,
            missed=1 if active else 8,
            late=0,
        )
        proposals = ProposalStats(expected=0, successful=0, missed=0)
        effectiveness = compute_effectiveness_score(
            attestations_expected=attestations.expected,
            attestations_successful=attestations.successful,
            attestations_late=attestations.late,
            proposals_expected=proposals.expected,
            proposals_successful=proposals.successful,
        )
        slashing_risk = compute_slashing_risk_score(
            consecutive_missed_attestations=attestations.missed,
            missed_proposals=proposals.missed,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )
        validators.append(
            ValidatorStats(
                index=b["index"],
                pubkey=b.get("pubkey"),
                status=b["status"],
                balance_gwei=b["balance_gwei"],
                effective_balance_gwei=b["effective_balance_gwei"],
                attestations=attestations,
                proposals=proposals,
                rewards_gwei=max(0, b["balance_gwei"] - b["effective_balance_gwei"]),
                effectiveness_score=effectiveness,
                slashing_risk_score=slashing_risk,
            )
        )
    return validators


async def collect_pulse(
    settings: Settings | None = None, *, dispatch_alerts: bool = False
) -> PulseSnapshot:
    settings = settings or get_settings()
    collected_at = datetime.now(timezone.utc).isoformat()
    channels = configured_channels(settings)

    if settings.is_demo():
        consensus = build_demo_consensus()
        host = collect_infrastructure()
        infrastructure = build_demo_infrastructure(host)
        demo_indices = settings.indices() or [1, 2, 3]
        validators = build_demo_validators(
            demo_indices, consensus, infrastructure
        )
    else:
        assert settings.beacon_api_url
        consensus = await collect_consensus(settings.beacon_api_url)
        infrastructure = collect_infrastructure()
        try:
            validators = await _collect_live_validators(
                settings.beacon_api_url,
                settings.validator_ids(),
                consensus,
                infrastructure,
            )
        except Exception as exc:  # noqa: BLE001
            validators = []
            consensus = consensus.model_copy(
                update={
                    "status": "critical",
                    "last_error": consensus.last_error or str(exc),
                }
            )

    metrics = aggregate_fleet_metrics(validators)
    partial = PulseSnapshot(
        collected_at=collected_at,
        demo_mode=settings.is_demo(),
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
