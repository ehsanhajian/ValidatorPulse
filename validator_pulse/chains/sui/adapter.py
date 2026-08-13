from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.sui.counters import MetricsDeltaStore
from validator_pulse.chains.sui.demo import (
    apply_demo_infrastructure,
    build_demo_sui_consensus,
    build_demo_validators,
)
from validator_pulse.chains.sui.graphql import (
    SuiChainSnapshot,
    SuiValidatorInfo,
    collect_sui_consensus,
    fetch_chain_snapshot,
    try_fetch_sui_metrics,
)
from validator_pulse.chains.sui.metrics import sui_effectiveness
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


def _normalize_addr(address: str) -> str:
    text = address.strip()
    if text and not text.startswith("0x"):
        text = f"0x{text}"
    return text


class SuiAdapter:
    """Sui validator adapter via GraphQL (+ optional local Prometheus).

    Never uses deprecated Sui JSON-RPC.
    """

    name = "sui"
    display_name = "Sui"
    operator_label = "validator"
    risk_kind = "reward_loss"
    risk_label = "Reward risk"
    primary_duty_label = "Proposals"
    secondary_duty_label = "Checkpoints"
    missed_duty_label = "Stalled progress"
    consensus_node_label = "Sui GraphQL"

    def __init__(self) -> None:
        self._deltas = MetricsDeltaStore()

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.sui_graphql_url and settings.sui_graphql_url.strip())

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
        consensus = build_demo_sui_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        addresses = settings.sui_validator_address_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            validator_addresses=addresses,
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
            graphql_url = (settings.sui_graphql_url or "").strip()
            addresses = [
                _normalize_addr(a) for a in settings.sui_validator_address_list()
            ]

            snap: SuiChainSnapshot | None = None
            try:
                snap = await fetch_chain_snapshot(graphql_url)
                consensus = ConsensusHealth(
                    beacon_reachable=True,
                    syncing=snap.safe_mode or snap.checkpoint == 0,
                    sync_distance=-1 if snap.safe_mode else 0,
                    head_slot=snap.checkpoint,
                    finalized_epoch=snap.epoch_id,
                    justified_epoch=snap.epoch_id,
                    peer_count=0,
                    connected_peers=0,
                    status=(
                        "critical"
                        if snap.safe_mode
                        else ("degraded" if snap.checkpoint == 0 else "healthy")
                    ),
                    last_error=(
                        "Network safe mode enabled" if snap.safe_mode else None
                    ),
                )
            except Exception:  # noqa: BLE001
                consensus = await collect_sui_consensus(graphql_url)

            metrics_url = (settings.sui_metrics_url or "").strip()
            node_metrics = None
            if metrics_url:
                node_metrics, note = await try_fetch_sui_metrics(metrics_url)
                if note:
                    consensus = consensus.model_copy(
                        update={
                            "last_error": (
                                f"{consensus.last_error}; {note}"
                                if consensus.last_error
                                else note
                            )
                        }
                    )
                if node_metrics and node_metrics.connected_peers is not None:
                    consensus = consensus.model_copy(
                        update={
                            "peer_count": node_metrics.connected_peers,
                            "connected_peers": node_metrics.connected_peers,
                        }
                    )

            if not addresses:
                err = "No SUI_VALIDATOR_ADDRESSES configured"
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

            by_addr: dict[str, SuiValidatorInfo] = {}
            reported_by: dict[str, set[str]] = {}
            safe_mode = False
            grace = settings.alert_sui_at_risk_epochs
            if snap is not None:
                by_addr = {v.address.lower(): v for v in snap.validators}
                reported_by = snap.reported_by
                safe_mode = snap.safe_mode
                grace = snap.low_stake_grace_period or grace

            operators = [
                self._build_operator(
                    index,
                    address,
                    consensus,
                    infrastructure,
                    settings,
                    info=by_addr.get(address.lower()),
                    reporters=reported_by.get(address.lower(), set()),
                    safe_mode=safe_mode,
                    grace_period=grace,
                    node_metrics=node_metrics,
                )
                for index, address in enumerate(addresses)
            ]
            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    def _build_operator(
        self,
        index: int,
        address: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
        *,
        info: SuiValidatorInfo | None,
        reporters: set[str],
        safe_mode: bool,
        grace_period: int,
        node_metrics,
    ) -> ValidatorStats:
        in_set = info is not None
        at_risk = info.at_risk if info else 0
        stake = info.stake_mist if info else 0
        reported = len(reporters) > 0
        metrics_available = node_metrics is not None and (
            node_metrics.proposed_blocks is not None
            or node_metrics.highest_synced_checkpoint is not None
        )

        proposals_delta: int | None = None
        checkpoint_delta: int | None = None
        if metrics_available and node_metrics is not None:
            deltas = self._deltas.observe(
                address.lower(),
                proposals=node_metrics.proposed_blocks,
                checkpoint=node_metrics.highest_synced_checkpoint
                or node_metrics.last_executed_checkpoint,
            )
            # After first baseline reset, show cumulative observed totals for the
            # process lifetime (monotonic store already prevents double-count).
            proposals_delta = deltas.proposals
            checkpoint_delta = deltas.checkpoint

        events: list[ProtocolEvent] = []
        op_status = "active" if in_set else "inactive"

        if safe_mode:
            op_status = "safe_mode"
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message="Network safe mode is enabled — critical operational risk.",
                    confirmed=True,
                )
            )

        if reported:
            op_status = "reward_slashed"
            events.append(
                ProtocolEvent(
                    kind="slashed",
                    severity="critical",
                    message=(
                        f"Reported by {len(reporters)} validator(s) — "
                        "reward slashing risk (rewards, not principal)."
                    ),
                    confirmed=True,
                )
            )
        elif at_risk > 0:
            op_status = "at_risk" if op_status == "active" else op_status
            severity = (
                "critical"
                if at_risk >= settings.alert_sui_at_risk_epochs
                else "warning"
            )
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity=severity,
                    message=(
                        f"Low-stake atRisk for {at_risk} epoch(s) "
                        f"(grace ≈ {grace_period}). Distinct from reward slashing."
                    ),
                    confirmed=True,
                )
            )

        if not in_set and not safe_mode:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message="Address is not in the current active validator set.",
                    confirmed=True,
                )
            )

        if metrics_available and proposals_delta == 0 and in_set and not reported:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="No new consensus proposals since last poll.",
                    confirmed=True,
                )
            )
            if op_status == "active":
                op_status = "degraded"
        elif not metrics_available and in_set:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        "Local Prometheus metrics unavailable — "
                        "on-chain set/atRisk/report state preserved; duty detail unknown."
                    ),
                    confirmed=False,
                )
            )

        effectiveness = sui_effectiveness(
            in_set=in_set,
            proposals_delta=proposals_delta,
            checkpoint_advancing=(
                None
                if checkpoint_delta is None
                else checkpoint_delta > 0
            ),
            at_risk_epochs=at_risk,
            reported=reported,
            safe_mode=safe_mode,
            metrics_available=metrics_available,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=(
                0 if (proposals_delta or 0) > 0 else (10 if metrics_available else 0)
            ),
            missed_secondary_duties=0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing or safe_mode,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if safe_mode or reported or not in_set:
            risk = 100.0
        elif at_risk >= settings.alert_sui_at_risk_epochs:
            risk = max(risk, 85.0)
        elif at_risk > 0:
            risk = max(risk, 55.0)

        prop_success = proposals_delta if proposals_delta is not None else 0
        cp_success = checkpoint_delta if checkpoint_delta is not None else 0

        return ValidatorStats(
            index=index,
            operator_id=address,
            operator_index=index,
            pubkey=address,
            status=op_status,
            balance_base_units=stake,
            effective_balance_base_units=stake,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(
                expected=prop_success if metrics_available else 0,
                successful=prop_success,
                missed=0,
            ),
            duties=[
                DutyStats(
                    category="block",
                    label="Proposals",
                    expected=prop_success if metrics_available else None,
                    successful=prop_success,
                    missed=0,
                    late=0,
                    weight=0.55,
                ),
                DutyStats(
                    category="checkpoint",
                    label="Checkpoints",
                    expected=cp_success if metrics_available else None,
                    successful=cp_success,
                    missed=0,
                    late=0,
                    weight=0.45,
                ),
            ],
            rewards_base_units=0,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="reward_loss",
            protocol_events=events,
            display_name=info.name if info and info.name else None,
            display_name_source="on_chain" if info and info.name else None,
        )
