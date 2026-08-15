from __future__ import annotations

from validator_pulse.chains.avalanche.demo import (
    apply_demo_infrastructure,
    build_demo_avalanche_consensus,
    build_demo_validators,
)
from validator_pulse.chains.avalanche.rpc import (
    avalanche_endpoints,
    collect_avalanche_consensus,
    fetch_current_validators,
    fetch_helicon_ts,
    fetch_info_uptime,
    fetch_local_node_id,
    fetch_peer_view,
    normalize_node_id,
    try_fetch_metrics,
)
from validator_pulse.chains.avalanche.state import (
    avalanche_effectiveness,
    now_ts,
    recovery_runway,
    resolve_uptime_threshold,
)
from validator_pulse.chains.base import ChainCollection
from validator_pulse.config import Settings
from validator_pulse.http_client import (
    RpcHttpConfig,
    bind_rpc_http_config,
    reset_rpc_http_config,
)
from validator_pulse.models import (
    AttestationStats,
    ConsensusHealth,
    DutyStats,
    InfrastructureHealth,
    ProposalStats,
    ProtocolEvent,
    ValidatorStats,
)
from validator_pulse.scoring import compute_slashing_risk_score


class AvalancheAdapter:
    """Avalanche Primary Network validator adapter (P-Chain + local info/metrics).

    `info.uptime` describes the local node only — never a public RPC's uptime
    as the configured validator. Reward forfeiture is not principal slashing.
    Custom Avalanche L1s are out of scope.
    """

    name = "avalanche"
    display_name = "Avalanche"
    operator_label = "validator"
    risk_kind = "reward_loss"
    risk_label = "Reward risk"
    primary_duty_label = "Uptime"
    secondary_duty_label = "Consensus polls"
    missed_duty_label = "Failed polls"
    consensus_node_label = "Avalanche node"

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.avalanche_rpc_url and settings.avalanche_rpc_url.strip())

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
        consensus = build_demo_avalanche_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        ids = settings.avalanche_node_id_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            node_ids=ids,
            runway_warn_hours=settings.alert_avalanche_runway_hours,
        )
        return ChainCollection(
            consensus=consensus,
            operators=operators,
            infrastructure=infrastructure,
        )

    async def _collect_live(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        token = bind_rpc_http_config(RpcHttpConfig.from_settings(settings))
        try:
            rpc_url = (settings.avalanche_rpc_url or "").strip()
            node_ids = settings.avalanche_node_id_list()
            p_url, info_url, _health, metrics_url = avalanche_endpoints(rpc_url)
            if settings.avalanche_metrics_url and settings.avalanche_metrics_url.strip():
                metrics_url = settings.avalanche_metrics_url.strip()
            consensus = await collect_avalanche_consensus(
                rpc_url, expected_network=settings.avalanche_network
            )

            node_metrics = None
            metrics_note = None
            node_metrics, metrics_note = await try_fetch_metrics(metrics_url)
            if metrics_note:
                consensus = consensus.model_copy(
                    update={
                        "last_error": (
                            f"{consensus.last_error}; {metrics_note}"
                            if consensus.last_error
                            else metrics_note
                        )
                    }
                )
            if node_metrics and node_metrics.peers is not None:
                consensus = consensus.model_copy(
                    update={
                        "peer_count": node_metrics.peers,
                        "connected_peers": node_metrics.peers,
                    }
                )

            if not node_ids:
                err = "No AVALANCHE_NODE_IDS configured"
                consensus = consensus.model_copy(
                    update={
                        "status": (
                            "degraded"
                            if consensus.status == "healthy"
                            else consensus.status
                        ),
                        "last_error": (
                            f"{consensus.last_error}; {err}"
                            if consensus.last_error
                            else err
                        ),
                    }
                )
                return ChainCollection(
                    consensus=consensus,
                    operators=[],
                    infrastructure=infrastructure,
                )

            current: dict[str, object] = {}
            helicon_ts = None
            local_id = None
            local_uptime = None
            if consensus.beacon_reachable:
                try:
                    rows = await fetch_current_validators(p_url, node_ids=node_ids)
                    current = {row.node_id: row for row in rows}
                except Exception as exc:  # noqa: BLE001
                    consensus = consensus.model_copy(
                        update={
                            "status": "degraded",
                            "last_error": (
                                f"{consensus.last_error}; P-Chain: {exc}"
                                if consensus.last_error
                                else f"P-Chain: {exc}"
                            ),
                        }
                    )
                helicon_ts = await fetch_helicon_ts(info_url)
                try:
                    local_id = await fetch_local_node_id(info_url)
                except Exception:  # noqa: BLE001
                    local_id = None
                if local_id:
                    try:
                        local_uptime = await fetch_info_uptime(info_url)
                    except Exception as exc:  # noqa: BLE001
                        consensus = consensus.model_copy(
                            update={
                                "last_error": (
                                    f"{consensus.last_error}; info.uptime: {exc}"
                                    if consensus.last_error
                                    else f"info.uptime: {exc}"
                                )
                            }
                        )

            operators = [
                await self._build_operator(
                    index,
                    node_id,
                    current.get(normalize_node_id(node_id)),
                    consensus,
                    infrastructure,
                    settings,
                    info_url=info_url,
                    local_id=local_id,
                    local_uptime=local_uptime,
                    helicon_ts=helicon_ts,
                    node_metrics=node_metrics,
                )
                for index, node_id in enumerate(node_ids)
            ]
            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    async def _build_operator(
        self,
        index: int,
        raw_id: str,
        row,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
        *,
        info_url: str,
        local_id: str | None,
        local_uptime,
        helicon_ts: float | None,
        node_metrics,
    ) -> ValidatorStats:
        node_id = normalize_node_id(raw_id)
        events: list[ProtocolEvent] = []
        in_set = row is not None
        start = int(row.start_time) if row else 0
        end = int(row.end_time) if row else 0
        connected = bool(row.connected) if row else False
        p_uptime = row.uptime_pct if row else None
        stake = int(row.stake_n_avax) if row else 0
        now = now_ts()
        threshold = resolve_uptime_threshold(
            start_time=start,
            helicon_ts=helicon_ts,
            override=settings.avalanche_uptime_threshold,
        )

        is_local = bool(local_id and local_id == node_id)
        rewarding = weighted = None
        if is_local and local_uptime is not None:
            rewarding = local_uptime.rewarding_stake_pct
            weighted = local_uptime.weighted_average_pct
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        "Local info.uptime: rewardingStake="
                        f"{rewarding}% vs weightedAverage={weighted}% "
                        "(this node only; distinct percentages). "
                        f"Requirement {threshold.label()}."
                    ),
                    confirmed=True,
                )
            )
        elif local_id and not is_local:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"info.uptime describes local node {local_id}, not "
                        f"{node_id}; ignoring public/local uptime as operator truth."
                    ),
                    confirmed=True,
                )
            )
        elif p_uptime is not None:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"P-Chain queried-node uptime {p_uptime:.2f}% is that "
                        "node's view, not the validator's reliable uptime. "
                        f"Requirement {threshold.label()}."
                    ),
                    confirmed=False,
                )
            )

        observed, benched = None, []
        if consensus.beacon_reachable:
            observed, benched = await fetch_peer_view(info_url, node_id)
            if benched:
                events.append(
                    ProtocolEvent(
                        kind="other",
                        severity="warning",
                        message=f"Peer is benched on chains: {', '.join(benched)}.",
                        confirmed=True,
                    )
                )

        scoring_uptime = weighted if is_local and weighted is not None else (
            p_uptime if p_uptime is not None else observed
        )
        recovery = None
        if scoring_uptime is not None and start and end:
            recovery = recovery_runway(
                uptime_pct=scoring_uptime,
                start_time=start,
                end_time=end,
                now=now,
                requirement_pct=threshold.percent,
            )

        if not in_set:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"{node_id} is not in the current Primary Network "
                        "validator set (no active validation period)."
                    ),
                    confirmed=True,
                )
            )
        else:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"Active Primary Network period {start}–{end} "
                        f"stake={stake} nAVAX connected={connected}."
                    ),
                    confirmed=True,
                )
            )

        if in_set and not connected:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="Validator is not connected to the queried node.",
                    confirmed=True,
                )
            )

        warn_seconds = settings.alert_avalanche_runway_hours * 3600
        if recovery is not None and in_set:
            if not recovery.possible:
                events.append(
                    ProtocolEvent(
                        kind="other",
                        severity="critical",
                        message=(
                            "Uptime cannot recover before the staking period ends "
                            f"(max final {recovery.max_final_pct:.1f}% vs "
                            f"{threshold.label()}). Reward forfeiture, not "
                            "principal slashing."
                        ),
                        confirmed=True,
                    )
                )
            elif recovery.slack_seconds < warn_seconds:
                events.append(
                    ProtocolEvent(
                        kind="other",
                        severity="warning",
                        message=(
                            "Reward eligibility at risk: recovery slack "
                            f"{recovery.slack_seconds / 3600:.1f}h is below "
                            f"{settings.alert_avalanche_runway_hours:.0f}h "
                            f"({threshold.label()}) — forfeiture risk, not slashing."
                        ),
                        confirmed=True,
                    )
                )

        poll_ratio = node_metrics.poll_success_ratio if node_metrics else None
        connected_stake = node_metrics.connected_stake if node_metrics else None
        if is_local and node_metrics and (
            node_metrics.polls_failed or connected_stake is not None
        ):
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info" if (node_metrics.polls_failed or 0) == 0 else "warning",
                    message=(
                        "Local metrics: "
                        f"polls ok={node_metrics.polls_successful} "
                        f"failed={node_metrics.polls_failed} "
                        f"connected_stake={connected_stake}."
                    ),
                    confirmed=True,
                )
            )

        op_status = "inactive"
        if in_set and recovery is not None and not recovery.possible:
            op_status = "forfeiture"
        elif in_set and recovery is not None and recovery.slack_seconds < warn_seconds:
            op_status = "degraded"
        elif in_set and connected:
            op_status = "active"
        elif in_set:
            op_status = "degraded"

        effectiveness = avalanche_effectiveness(
            in_set=in_set,
            uptime_pct=scoring_uptime,
            requirement_pct=threshold.percent,
            connected=connected,
            rewarding_stake_pct=rewarding if is_local else connected_stake,
            poll_success_ratio=poll_ratio if is_local else None,
            recovery=recovery,
        )
        missed_polls = 0
        if is_local and node_metrics and node_metrics.polls_failed:
            missed_polls = min(int(node_metrics.polls_failed), 40)
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=0 if (scoring_uptime or 100) >= threshold.percent else 8,
            missed_secondary_duties=missed_polls,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing or not connected,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if op_status == "forfeiture":
            risk = max(risk, 90.0)
        elif op_status == "degraded":
            risk = max(risk, 50.0)

        polls_ok = node_metrics.polls_successful if node_metrics and is_local else None
        polls_fail = node_metrics.polls_failed if node_metrics and is_local else None
        uptime_success = int(round(scoring_uptime)) if scoring_uptime is not None else 0
        return ValidatorStats(
            index=index,
            operator_id=node_id,
            pubkey=node_id,
            status=op_status,
            balance_base_units=stake,
            effective_balance_base_units=stake,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(
                expected=(polls_ok or 0) + (polls_fail or 0),
                successful=polls_ok or 0,
                missed=polls_fail or 0,
            ),
            duties=[
                DutyStats(
                    category="other",
                    label="Uptime",
                    expected=100 if scoring_uptime is not None else None,
                    successful=uptime_success,
                    missed=max(0, 100 - uptime_success) if scoring_uptime is not None else 0,
                    late=0,
                    weight=0.7,
                ),
                DutyStats(
                    category="poll",
                    label="Consensus polls",
                    expected=(
                        (polls_ok or 0) + (polls_fail or 0)
                        if polls_ok is not None or polls_fail is not None
                        else None
                    ),
                    successful=polls_ok or 0,
                    missed=polls_fail or 0,
                    late=0,
                    weight=0.3,
                ),
            ],
            rewards_base_units=int(row.potential_reward) if row else 0,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="reward_loss",
            protocol_events=events,
        )
