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


def _labels_from_partial(snapshot: PulseSnapshot | dict) -> tuple[str, str, str, str]:
    if isinstance(snapshot, PulseSnapshot):
        operator = snapshot.operator_label or "validator"
        primary = (snapshot.primary_duty_label or "duties").lower()
        risk = (snapshot.risk_label or "Risk").lower()
        missed = (snapshot.missed_duty_label or f"Missed {primary}").lower()
        return operator, primary, risk, missed

    operator = snapshot.get("operator_label") or "validator"
    primary = (snapshot.get("primary_duty_label") or "duties").lower()
    risk = (snapshot.get("risk_label") or "risk").lower()
    missed = (snapshot.get("missed_duty_label") or f"Missed {primary}").lower()
    return operator, primary, risk, missed


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

    operator, duty_word, risk_word, _missed_label = _labels_from_partial(snapshot)

    statuses: list[HealthStatus] = [consensus_status, infra_status]
    for v in validators:
        risk_score = v.risk_score if v.risk_score is not None else v.slashing_risk_score
        if risk_score >= 60 or v.effectiveness_score < 80:
            statuses.append("critical")
        elif risk_score >= 30 or v.effectiveness_score < 95:
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

    missed = (
        metrics.missed_primary_duties_total
        if metrics.missed_primary_duties_total is not None
        else metrics.validator_missed_attestations_total
    )
    effectiveness = (
        metrics.effectiveness_score
        if metrics.effectiveness_score is not None
        else metrics.validator_effectiveness_score
    )
    risk = (
        metrics.risk_score
        if metrics.risk_score is not None
        else metrics.validator_slashing_risk_score
    )

    if status == "healthy":
        return Verdict(
            status=status,
            answer=f"Yes — your {operator} is operating correctly.",
            summary=(
                f"Fleet effectiveness {effectiveness}% with {risk_word} {risk}. "
                "Consensus and infrastructure look healthy."
            ),
        )
    if status == "degraded":
        return Verdict(
            status=status,
            answer="Partially — attention needed.",
            summary=(
                f"Effectiveness {effectiveness}%, missed {duty_word} {missed}, "
                f"{risk_word} {risk}. Review degraded signals before they escalate."
            ),
        )
    return Verdict(
        status=status,
        answer="No — intervention required.",
        summary=(
            f"Critical signals detected. Effectiveness {effectiveness}%, "
            f"missed {duty_word} {missed}, {risk_word} {risk}. "
            "Act now to avoid downtime or penalties."
        ),
    )


def evaluate_alerts(snapshot: PulseSnapshot, settings: Settings) -> list[AlertEvent]:
    channels = configured_channels(settings)
    now = datetime.now(timezone.utc).isoformat()
    alerts: list[AlertEvent] = []
    operator = snapshot.operator_label or "validator"
    duty_word = (snapshot.primary_duty_label or "duties").lower()
    risk_word = (snapshot.risk_label or "Risk").lower()
    node_label = snapshot.consensus_node_label or "Consensus node"

    for v in snapshot.validators:
        label = v.operator_id or v.pubkey or str(v.index)
        missed = v.attestations.missed
        risk_score = v.risk_score if v.risk_score is not None else v.slashing_risk_score
        if missed >= settings.alert_missed_attestations:
            alerts.append(
                AlertEvent(
                    id=f"missed-duty-{label}-{now}",
                    severity="warning",
                    title=f"Missed {duty_word} on {operator} {label}",
                    message=(
                        f"{missed} missed {duty_word} in the current "
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
                    id=f"eff-{label}-{now}",
                    severity="warning",
                    title=f"Low effectiveness on {operator} {label}",
                    message=(
                        f"Effectiveness {v.effectiveness_score}% is below "
                        f"{settings.alert_effectiveness_below}%."
                    ),
                    source="validator",
                    created_at=now,
                    channels=channels,
                )
            )
        if risk_score >= settings.alert_slashing_risk_above:
            alerts.append(
                AlertEvent(
                    id=f"risk-{label}-{now}",
                    severity="critical",
                    title=f"Elevated {risk_word} on {operator} {label}",
                    message=(
                        f"Risk score {risk_score} exceeds "
                        f"threshold {settings.alert_slashing_risk_above}."
                    ),
                    source="validator",
                    created_at=now,
                    channels=channels,
                )
            )

        # Relay NPoS: explicit offline + low era-point alerts.
        if snapshot.chain == "polkadot" and snapshot.risk_kind == "slashing":
            status_l = (v.status or "").lower()
            if status_l in {"offline", "unreachable"} or "offline" in status_l:
                alerts.append(
                    AlertEvent(
                        id=f"offline-{label}-{now}",
                        severity="critical",
                        title=f"Relay validator offline: {label}",
                        message=(
                            f"Status is '{v.status}'. Check heartbeat / session keys "
                            "and relay node connectivity."
                        ),
                        source="validator",
                        created_at=now,
                        channels=channels,
                    )
                )
            era_points = v.primary_duty().successful
            if (
                v.primary_duty().expected
                and era_points < settings.alert_low_era_points_below
            ):
                alerts.append(
                    AlertEvent(
                        id=f"era-points-{label}-{now}",
                        severity="warning",
                        title=f"Low era points on validator {label}",
                        message=(
                            f"Era points {era_points} are below "
                            f"threshold {settings.alert_low_era_points_below}."
                        ),
                        source="validator",
                        created_at=now,
                        channels=channels,
                    )
                )

        # Cosmos: jail / tombstone protocol events.
        if snapshot.chain == "cosmos":
            for event in v.protocol_events:
                if event.kind in {"jailed", "tombstoned"}:
                    alerts.append(
                        AlertEvent(
                            id=f"{event.kind}-{label}-{now}",
                            severity=event.severity,
                            title=f"Validator {event.kind}: {label}",
                            message=event.message,
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )

        # Solana: delinquency + high skip-rate protocol events.
        if snapshot.chain == "solana":
            for event in v.protocol_events:
                if event.kind in {"delinquent", "high_skip_rate"}:
                    alerts.append(
                        AlertEvent(
                            id=f"{event.kind}-{label}-{now}",
                            severity=event.severity,
                            title=(
                                f"Validator delinquent: {label}"
                                if event.kind == "delinquent"
                                else f"High skip rate on validator {label}"
                            ),
                            message=event.message,
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )
            # Also alert when leader skip % crosses the configured threshold.
            leader = next(
                (d for d in v.duties if d.label == "Leader slots"),
                None,
            )
            if leader and leader.expected > 0:
                skip_pct = (leader.missed / leader.expected) * 100.0
                if skip_pct >= settings.alert_skip_rate_above and not any(
                    e.kind == "high_skip_rate" for e in v.protocol_events
                ):
                    alerts.append(
                        AlertEvent(
                            id=f"skip-rate-{label}-{now}",
                            severity="warning",
                            title=f"High skip rate on validator {label}",
                            message=(
                                f"Skip rate {skip_pct:.1f}% is at or above "
                                f"{settings.alert_skip_rate_above}% "
                                f"({leader.missed}/{leader.expected} skipped)."
                            ),
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )

        # NEAR: kickout vs malicious slash are distinct.
        if snapshot.chain == "near":
            for event in v.protocol_events:
                if event.kind == "slashed":
                    alerts.append(
                        AlertEvent(
                            id=f"slashed-{label}-{now}",
                            severity="critical",
                            title=f"Validator slashed: {label}",
                            message=event.message,
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )
                elif event.kind == "kicked":
                    alerts.append(
                        AlertEvent(
                            id=f"kicked-{label}-{now}",
                            severity=event.severity,
                            title=(
                                f"Validator kickout: {label}"
                                if event.confirmed
                                else f"Elevated kickout risk on validator {label}"
                            ),
                            message=event.message,
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )

        # Tezos: forbidden / denunciation / remaining miss budget — slashing terminology OK.
        if snapshot.chain == "tezos":
            for event in v.protocol_events:
                if event.kind == "slashed":
                    alerts.append(
                        AlertEvent(
                            id=f"slashed-{label}-{now}",
                            severity="critical",
                            title=f"Baker slashed / forbidden: {label}",
                            message=event.message,
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )
                elif event.kind == "suspended":
                    alerts.append(
                        AlertEvent(
                            id=f"forbidden-{label}-{now}",
                            severity="critical",
                            title=f"Baker forbidden: {label}",
                            message=event.message,
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )
                elif event.kind == "other" and "allowed" in event.message.lower():
                    alerts.append(
                        AlertEvent(
                            id=f"remaining-misses-{label}-{now}",
                            severity=event.severity,
                            title=f"Low remaining miss budget on baker {label}",
                            message=event.message,
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )

        # Cardano: KES expiry / forging suspension — never slashing terminology.
        if snapshot.chain == "cardano":
            for event in v.protocol_events:
                if event.kind == "kes_expired":
                    alerts.append(
                        AlertEvent(
                            id=f"kes-{label}-{now}",
                            severity=event.severity,
                            title=(
                                f"KES expired on stake pool {label}"
                                if event.confirmed
                                else f"KES expiry warning on stake pool {label}"
                            ),
                            message=event.message,
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )
                elif event.kind == "suspended":
                    alerts.append(
                        AlertEvent(
                            id=f"suspended-{label}-{now}",
                            severity=event.severity,
                            title=f"Stake pool forging suspended: {label}",
                            message=event.message,
                            source="validator",
                            created_at=now,
                            channels=channels,
                        )
                    )

    if not snapshot.consensus.beacon_reachable:
        alerts.append(
            AlertEvent(
                id=f"node-down-{now}",
                severity="critical",
                title=f"{node_label} unreachable",
                message=snapshot.consensus.last_error or f"{node_label} health check failed.",
                source="consensus",
                created_at=now,
                channels=channels,
            )
        )
    elif snapshot.consensus.syncing:
        alerts.append(
            AlertEvent(
                id=f"node-sync-{now}",
                severity="warning",
                title=f"{node_label} syncing",
                message=f"Sync distance {snapshot.consensus.sync_distance}.",
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
