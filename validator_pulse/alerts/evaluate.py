from __future__ import annotations

from datetime import datetime, timezone

from validator_pulse.config import Settings
from validator_pulse.models import (
    AlertChannelName,
    AlertEvent,
    HealthStatus,
    PulseSnapshot,
    Verdict,
)


def configured_channels(settings: Settings) -> list[AlertChannelName]:
    channels: list[AlertChannelName] = []
    if settings.telegram_bot_token and settings.telegram_chat_id:
        channels.append("telegram")
    if settings.slack_webhook_url:
        channels.append("slack")
    if settings.discord_webhook_url:
        channels.append("discord")
    if settings.webhook_url:
        channels.append("webhook")
    if settings.pagerduty_routing_key:
        channels.append("pagerduty")
    return channels


def build_verdict(snapshot: PulseSnapshot | dict) -> Verdict:
    if isinstance(snapshot, PulseSnapshot):
        consensus_status = snapshot.consensus.status
        infra_status = snapshot.infrastructure.status
        validators = snapshot.validators
        metrics = snapshot.metrics
    else:
        consensus_status = snapshot["consensus"].status
        infra_status = snapshot["infrastructure"].status
        validators = snapshot["validators"]
        metrics = snapshot["metrics"]

    statuses: list[HealthStatus] = [consensus_status, infra_status]
    for v in validators:
        if v.slashing_risk_score >= 60 or v.effectiveness_score < 80:
            statuses.append("critical")
        elif v.slashing_risk_score >= 30 or v.effectiveness_score < 95:
            statuses.append("degraded")
        else:
            statuses.append("healthy")

    if "critical" in statuses:
        status: HealthStatus = "critical"
    elif "degraded" in statuses:
        status = "degraded"
    elif "unknown" in statuses:
        status = "unknown"
    else:
        status = "healthy"

    missed = metrics.validator_missed_attestations_total
    effectiveness = metrics.validator_effectiveness_score
    risk = metrics.validator_slashing_risk_score

    if status == "healthy":
        return Verdict(
            status=status,
            answer="Yes — your validator is operating correctly.",
            summary=(
                f"Fleet effectiveness {effectiveness}% with slashing risk {risk}. "
                "Consensus and infrastructure look healthy."
            ),
        )
    if status == "degraded":
        return Verdict(
            status=status,
            answer="Partially — attention needed.",
            summary=(
                f"Effectiveness {effectiveness}%, missed attestations {missed}, "
                f"slashing risk {risk}. Review degraded signals before they escalate."
            ),
        )
    return Verdict(
        status=status,
        answer="No — intervention required.",
        summary=(
            f"Critical signals detected. Effectiveness {effectiveness}%, "
            f"missed attestations {missed}, slashing risk {risk}. "
            "Act now to avoid downtime or slashing."
        ),
    )


def evaluate_alerts(snapshot: PulseSnapshot, settings: Settings) -> list[AlertEvent]:
    channels = configured_channels(settings)
    now = datetime.now(timezone.utc).isoformat()
    alerts: list[AlertEvent] = []

    for v in snapshot.validators:
        if v.attestations.missed >= settings.alert_missed_attestations:
            alerts.append(
                AlertEvent(
                    id=f"missed-att-{v.index}-{now}",
                    severity="warning",
                    title=f"Missed attestations on validator {v.index}",
                    message=(
                        f"{v.attestations.missed} missed attestations in the current "
                        f"window (threshold {settings.alert_missed_attestations})."
                    ),
                    source="validator",
                    created_at=now,
                    channels=channels,
                )
            )
        if v.effectiveness_score < settings.alert_effectiveness_below:
            alerts.append(
                AlertEvent(
                    id=f"eff-{v.index}-{now}",
                    severity="warning",
                    title=f"Low effectiveness on validator {v.index}",
                    message=(
                        f"Effectiveness {v.effectiveness_score}% is below "
                        f"{settings.alert_effectiveness_below}%."
                    ),
                    source="validator",
                    created_at=now,
                    channels=channels,
                )
            )
        if v.slashing_risk_score >= settings.alert_slashing_risk_above:
            alerts.append(
                AlertEvent(
                    id=f"slash-{v.index}-{now}",
                    severity="critical",
                    title=f"Elevated slashing risk on validator {v.index}",
                    message=(
                        f"Slashing risk score {v.slashing_risk_score} exceeds "
                        f"threshold {settings.alert_slashing_risk_above}."
                    ),
                    source="validator",
                    created_at=now,
                    channels=channels,
                )
            )

    if not snapshot.consensus.beacon_reachable:
        alerts.append(
            AlertEvent(
                id=f"beacon-down-{now}",
                severity="critical",
                title="Beacon node unreachable",
                message=snapshot.consensus.last_error or "Beacon health check failed.",
                source="consensus",
                created_at=now,
                channels=channels,
            )
        )
    elif snapshot.consensus.syncing:
        alerts.append(
            AlertEvent(
                id=f"beacon-sync-{now}",
                severity="warning",
                title="Beacon node syncing",
                message=f"Sync distance {snapshot.consensus.sync_distance} slots.",
                source="consensus",
                created_at=now,
                channels=channels,
            )
        )

    if snapshot.infrastructure.disk_usage_percent >= settings.alert_disk_usage_above:
        alerts.append(
            AlertEvent(
                id=f"disk-{now}",
                severity="warning",
                title="Disk usage high",
                message=(
                    f"Disk at {snapshot.infrastructure.disk_usage_percent}% "
                    f"(threshold {settings.alert_disk_usage_above}%)."
                ),
                source="infrastructure",
                created_at=now,
                channels=channels,
            )
        )

    if snapshot.infrastructure.clock_drift_ms >= settings.alert_clock_drift_ms:
        alerts.append(
            AlertEvent(
                id=f"clock-{now}",
                severity="critical",
                title="Clock drift detected",
                message=(
                    f"Clock drift {snapshot.infrastructure.clock_drift_ms}ms exceeds "
                    f"{settings.alert_clock_drift_ms}ms."
                ),
                source="infrastructure",
                created_at=now,
                channels=channels,
            )
        )

    return alerts
