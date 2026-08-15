from __future__ import annotations

import time

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.ton.api import (
    QosRow,
    TonMetrics,
    collect_ton_consensus,
    collect_ton_snapshot,
    derive_qos_url,
    index_role,
    nanotons_to_label,
    try_fetch_metrics,
)
from validator_pulse.chains.ton.demo import (
    apply_demo_infrastructure,
    build_demo_ton_consensus,
    build_demo_validators,
)
from validator_pulse.chains.ton.mytonctrl import load_mytonctrl_snapshot
from validator_pulse.chains.ton.state import (
    AdnlHistoryStore,
    AdnlObservation,
    EfficiencyThreshold,
    complaints_are_critical,
    completed_round_below_threshold,
    efficiency_is_actionable,
    is_adnl,
    normalize_adnl,
    parse_complaint,
    resolve_efficiency_threshold,
    ton_effectiveness,
)
from validator_pulse.config import Settings
from validator_pulse.http_client import (
    RpcHttpConfig,
    bind_rpc_http_config,
    reset_rpc_http_config,
)
from validator_pulse.models import (
    AttestationStats,
    DutyStats,
    InfrastructureHealth,
    ProposalStats,
    ProtocolEvent,
    ValidatorStats,
)
from validator_pulse.scoring import compute_slashing_risk_score


class TonAdapter:
    """TON validator adapter via Validation API + QoS catchain efficiency.

    Optional local MyTonCtrl (read-only, argument-safe) and Prometheus enrich
    sync lag and roster efficiency. Complaints/fines are operational fine risk.
    Zero/null efficiency at round start is not treated as a miss.
    """

    name = "ton"
    display_name = "TON"
    operator_label = "validator"
    risk_kind = "operational"
    risk_label = "Fine risk"
    primary_duty_label = "Validation rounds"
    secondary_duty_label = "Catchain efficiency"
    missed_duty_label = "Low-efficiency rounds"
    consensus_node_label = "TON validator"

    def __init__(self) -> None:
        self._history = AdnlHistoryStore()

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        api = bool(settings.ton_validation_api_url and settings.ton_validation_api_url.strip())
        prom = bool(settings.ton_prometheus_url and settings.ton_prometheus_url.strip())
        return not (api or prom)

    async def collect(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        if self.is_demo(settings):
            return self._collect_demo(settings, infrastructure)
        return await self._collect_live(settings, infrastructure)

    def _collect_demo(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        _ = settings
        consensus = build_demo_ton_consensus()
        operators = build_demo_validators(consensus, infrastructure)
        return ChainCollection(
            consensus=consensus,
            operators=operators,
            infrastructure=apply_demo_infrastructure(infrastructure),
        )

    async def _collect_live(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        token = bind_rpc_http_config(RpcHttpConfig.from_settings(settings))
        try:
            configured = [
                normalize_adnl(item)
                for item in settings.ton_adnl_list()
                if is_adnl(item)
            ]
            validation_url = (settings.ton_validation_api_url or "").strip() or None
            qos_url = derive_qos_url(validation_url, settings.ton_qos_api_url)
            threshold = resolve_efficiency_threshold(settings.ton_efficiency_threshold)
            now = time.time()

            if not configured:
                consensus = await collect_ton_consensus(
                    None,
                    None,
                    [],
                    "TON_ADNL_ADDRESSES is empty — configure 64-hex ADNL identities.",
                )
                return ChainCollection(
                    consensus=consensus, operators=[], infrastructure=infrastructure
                )

            metrics, metrics_err = await try_fetch_metrics(settings.ton_prometheus_url)
            local = load_mytonctrl_snapshot(settings.ton_mytonctrl_command)
            cycles: list = []
            elections: list = []
            qos_by_adnl: dict[str, list[QosRow]] = {}
            api_err = None
            if validation_url:
                cycles, elections, qos_by_adnl, api_err = await collect_ton_snapshot(
                    validation_url, qos_url, configured
                )
            errors = [item for item in (api_err, metrics_err) if item]
            current = cycles[0] if cycles else None
            previous = cycles[1] if len(cycles) > 1 else None

            for adnl in configured:
                participant = current.validators.get(adnl) if current else None
                qos = _qos_for(qos_by_adnl.get(adnl) or [], current.cycle_id if current else None)
                self._history.record(
                    AdnlObservation(
                        adnl=adnl,
                        cycle_id=current.cycle_id if current else 0,
                        index=(participant.index if participant else None)
                        or (qos.index if qos else None),
                        efficiency=qos.efficiency if qos else None,
                        in_set=bool(participant),
                        stake=participant.stake if participant else None,
                        seen_at=now,
                        utime_until=current.utime_until if current else None,
                    )
                )

            tracked = self._history.tracked_adnls(configured, now)
            operators = [
                self._build_operator(
                    adnl=adnl,
                    configured=configured,
                    current=current,
                    previous=previous,
                    elections=elections,
                    qos_rows=qos_by_adnl.get(adnl) or [],
                    metrics=metrics,
                    local=local,
                    threshold=threshold,
                    infrastructure=infrastructure,
                    now=now,
                    settings=settings,
                )
                for adnl in tracked
            ]
            consensus = await collect_ton_consensus(
                validation_url, metrics, cycles, "; ".join(errors) if errors else None
            )
            if current:
                consensus.head_slot = current.cycle_id
                if current.utime_since:
                    consensus.finalized_epoch = current.utime_since
                    consensus.justified_epoch = current.utime_since
            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    def _build_operator(
        self,
        *,
        adnl: str,
        configured: list[str],
        current,
        previous,
        elections,
        qos_rows: list[QosRow],
        metrics: TonMetrics | None,
        local,
        threshold: EfficiencyThreshold,
        infrastructure: InfrastructureHealth,
        now: float,
        settings: Settings,
    ) -> ValidatorStats:
        participant = current.validators.get(adnl) if current else None
        past = previous.validators.get(adnl) if previous else None
        qos_current = _qos_for(qos_rows, current.cycle_id if current else None)
        qos_past = _qos_for(qos_rows, previous.cycle_id if previous else None)
        local_row = (local.roster.get(adnl) if local else None) or (
            local.past_roster.get(adnl) if local else None
        )
        in_set = bool(participant)
        rotated = adnl not in configured
        index = (
            (participant.index if participant else None)
            or (qos_current.index if qos_current else None)
            or (local_row.index if local_row else None)
        )
        stake = (participant.stake if participant else None) or (
            int(local_row.stake * 1e9) if local_row and local_row.stake else None
        )
        efficiency, efficiency_source, eff_since, eff_until = _pick_efficiency(
            qos_current, qos_past, local_row, current, previous, now
        )
        actionable = efficiency_is_actionable(
            efficiency, utime_since=eff_since, utime_until=eff_until, now=now
        )
        below = completed_round_below_threshold(
            efficiency, threshold, utime_until=eff_until, now=now
        )
        complaints = list(participant.complaints if participant else [])
        if past:
            complaints.extend(past.complaints)
        if local:
            for raw in local.complaints:
                parsed = parse_complaint(raw)
                if not parsed:
                    continue
                raw_adnl = normalize_adnl(
                    raw.get("adnl_addr") or raw.get("adnl") or raw.get("adnlAddr")
                )
                if raw_adnl and raw_adnl != adnl:
                    continue
                complaints.append(parsed)
        fined = complaints_are_critical(complaints)
        election = _election_for(elections, adnl)
        missed_election = bool(elections) and election is None and not in_set
        lag = metrics.master_out_of_sync if metrics else None
        severe_lag = bool(lag is not None and lag > 60)
        recovering = rotated or (missed_election and not fined)
        history = self._history.history_for(adnl, now)

        events: list[ProtocolEvent] = []
        freshness = "ok"
        if current and current.utime_until and now > current.utime_until + 600:
            freshness = "stale"
        events.append(
            ProtocolEvent(
                kind="other",
                severity="info",
                message=(
                    f"ADNL {adnl} cycle={current.cycle_id if current else 'n/a'} "
                    f"index={index if index is not None else 'n/a'} "
                    f"role={index_role(index)} in_set={in_set} "
                    f"sync_lag={lag if lag is not None else 'unknown'}s "
                    f"election={'in' if election else 'not-in'} "
                    f"freshness={freshness} network={settings.ton_network}."
                ),
                confirmed=True,
            )
        )
        if history:
            prior = ", ".join(
                f"cycle {row.cycle_id} eff={row.efficiency if row.efficiency is not None else 'n/a'}"
                for row in history[-4:]
            )
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"ADNL history window retained ({len(history)} cycle(s)): {prior}. "
                        "Rotation does not discard prior-window observations."
                    ),
                    confirmed=True,
                )
            )
        if rotated:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"ADNL {adnl} rotated off TON_ADNL_ADDRESSES but remains in the "
                        "time-bounded history window."
                    ),
                    confirmed=True,
                )
            )
        if efficiency is None:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        "Catchain efficiency unavailable from QoS cycleScoreboard "
                        "(null is not treated as 0%). Local MyTonCtrl vl/check_ef "
                        "and Prometheus supply efficiency when present."
                    ),
                    confirmed=True,
                )
            )
        elif not actionable:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"Efficiency {efficiency:.1f}% ignored at round start "
                        f"(grace; threshold {threshold.label()}). "
                        "Zero efficiency at the beginning of a cycle is expected."
                    ),
                    confirmed=True,
                )
            )
        else:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical" if below else "info",
                    message=(
                        f"Catchain efficiency {efficiency:.1f}% source={efficiency_source} "
                        f"threshold {threshold.label()} "
                        f"{'completed round below policy' if below else 'within policy'}."
                    ),
                    confirmed=True,
                )
            )
        if fined:
            fine_bits = []
            for item in complaints:
                if item.get("fine") is not None:
                    try:
                        fine_bits.append(nanotons_to_label(int(item["fine"])))
                    except (TypeError, ValueError):
                        fine_bits.append(str(item["fine"]))
            events.append(
                ProtocolEvent(
                    kind="fined",
                    severity="critical",
                    message=(
                        "Confirmed complaint/fine"
                        + (f" ({', '.join(fine_bits)})" if fine_bits else "")
                        + ". Operational fine risk from elector complaints, "
                        "not Ethereum-style principal slashing."
                    ),
                    confirmed=True,
                )
            )
        if missed_election:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="ADNL is not in the latest election participants list.",
                    confirmed=True,
                )
            )
        if severe_lag:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message=(
                        f"Severe masterchain sync lag {lag:.0f}s "
                        "(Prometheus validator_masterchain_out_of_sync_seconds)."
                    ),
                    confirmed=True,
                )
            )

        effectiveness = ton_effectiveness(
            in_set=in_set,
            efficiency=efficiency,
            efficiency_actionable=actionable,
            fined=fined,
            missed_election=missed_election,
            severe_lag=severe_lag,
            recovering=recovering,
        )
        missed_rounds = 1 if below else 0
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=missed_rounds,
            missed_secondary_duties=1 if missed_election else 0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=severe_lag or (metrics.synced is False if metrics else False),
            peer_count=max(1, 8 if metrics and metrics.console_up else 1),
            effectiveness_score=effectiveness,
        )
        if fined:
            risk = max(risk, 92.0)
            status = "fined"
        elif severe_lag:
            risk = max(risk, 80.0)
            status = "lagging"
        elif below:
            risk = max(risk, 70.0)
            status = "degraded"
        elif recovering:
            risk = max(risk, 35.0)
            status = "recovering"
        elif in_set:
            status = "active"
        else:
            status = "inactive"
            risk = max(risk, 45.0)

        expected_eff = 100 if efficiency is not None and actionable else None
        successful_eff = int(round(efficiency)) if efficiency is not None and actionable else 0
        return ValidatorStats(
            index=index or 0,
            operator_id=adnl,
            operator_index=index,
            pubkey=adnl,
            withdrawal_address=participant.wallet if participant else None,
            status=status,
            balance_base_units=stake or 0,
            effective_balance_base_units=stake or 0,
            attestations=AttestationStats(
                expected=expected_eff or 0,
                successful=successful_eff,
                missed=max(0, (expected_eff or 0) - successful_eff),
                late=0,
            ),
            proposals=ProposalStats(
                expected=1 if in_set or below else 0,
                successful=1 if in_set and not below else 0,
                missed=missed_rounds,
            ),
            duties=[
                DutyStats(
                    category="round",
                    label="Validation rounds",
                    expected=1 if in_set or below else 0,
                    successful=1 if in_set and not below else 0,
                    missed=missed_rounds,
                    weight=1.0,
                ),
                DutyStats(
                    category="other",
                    label="Catchain efficiency",
                    expected=expected_eff,
                    successful=successful_eff,
                    missed=max(0, (expected_eff or 0) - successful_eff),
                    weight=0.8,
                ),
            ],
            rewards_base_units=0,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="operational",
            protocol_events=events,
        )


def _qos_for(rows: list[QosRow], cycle_id: int | None) -> QosRow | None:
    if cycle_id is None:
        return rows[0] if rows else None
    for row in rows:
        if row.cycle_id == cycle_id:
            return row
    return None


def _pick_efficiency(qos_current, qos_past, local_row, current, previous, now: float):
    if qos_current and qos_current.efficiency is not None:
        return (
            qos_current.efficiency,
            qos_current.source,
            qos_current.utime_since or (current.utime_since if current else None),
            qos_current.utime_until or (current.utime_until if current else None),
        )
    if local_row and local_row.efficiency is not None:
        return (
            local_row.efficiency,
            "mytonctrl-vl",
            current.utime_since if current else None,
            current.utime_until if current else None,
        )
    if qos_past and qos_past.efficiency is not None:
        return (
            qos_past.efficiency,
            qos_past.source,
            qos_past.utime_since or (previous.utime_since if previous else None),
            qos_past.utime_until or (previous.utime_until if previous else None),
        )
    current_eff = qos_current.efficiency if qos_current else None
    return (
        current_eff,
        "qos-cycleScoreboard" if qos_current else "unavailable",
        current.utime_since if current else None,
        current.utime_until if current else None,
    )


def _election_for(elections, adnl: str):
    for entry in elections:
        if adnl in entry.participants:
            return entry
    return None
