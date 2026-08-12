from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.cardano.counters import CounterSnapshotStore
from validator_pulse.chains.cardano.demo import (
    apply_demo_infrastructure,
    build_demo_cardano_consensus,
    build_demo_validators,
    cardano_effectiveness,
)
from validator_pulse.chains.cardano.metrics import consensus_from_tracer
from validator_pulse.chains.cardano.tracer import fetch_tracer_metrics
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


class CardanoAdapter:
    """Cardano stake-pool adapter via local cardano-tracer Prometheus metrics."""

    name = "cardano"
    display_name = "Cardano"
    operator_label = "stake pool"
    risk_kind = "suspension"
    risk_label = "Operational risk"
    primary_duty_label = "Leader slots"
    secondary_duty_label = "Blocks forged"
    missed_duty_label = "Missed slots"
    consensus_node_label = "Block producer"

    def __init__(self) -> None:
        self._counters = CounterSnapshotStore()

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.cardano_tracer_url and settings.cardano_tracer_url.strip())

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
        consensus = build_demo_cardano_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        pools = settings.cardano_pool_id_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            pool_ids=pools,
            kes_warning_periods=settings.alert_cardano_kes_warning,
            kes_critical_periods=settings.alert_cardano_kes_critical,
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
            tracer_url = (settings.cardano_tracer_url or "").strip()
            node_name = (settings.cardano_node_name or "block-producer").strip()
            pool_ids = settings.cardano_pool_id_list()

            if not pool_ids:
                consensus = ConsensusHealth(
                    beacon_reachable=False,
                    syncing=True,
                    sync_distance=-1,
                    head_slot=0,
                    finalized_epoch=0,
                    justified_epoch=0,
                    peer_count=0,
                    connected_peers=0,
                    status="critical",
                    last_error="No CARDANO_POOL_IDS configured",
                )
                return ChainCollection(
                    consensus=consensus,
                    operators=[],
                    infrastructure=infrastructure,
                )

            tracer_metrics, tracer_error = await fetch_tracer_metrics(tracer_url, node_name)
            reachable = tracer_error is None and any(
                v is not None
                for v in (
                    tracer_metrics.blocks_forged,
                    tracer_metrics.slots_missed,
                    tracer_metrics.epoch,
                    tracer_metrics.remaining_kes_periods,
                )
            )
            consensus = consensus_from_tracer(
                tracer_metrics,
                reachable=reachable,
                last_error=tracer_error,
            )

            operators = [
                self._build_live_operator(
                    index,
                    pool_id,
                    tracer_metrics,
                    consensus,
                    infrastructure,
                    settings,
                    tracer_available=reachable,
                )
                for index, pool_id in enumerate(pool_ids)
            ]

            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    def _build_live_operator(
        self,
        index: int,
        pool_id: str,
        tracer_metrics,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
        *,
        tracer_available: bool,
    ) -> ValidatorStats:
        if not tracer_available:
            return self._unknown_duty_operator(
                index,
                pool_id,
                consensus,
                infrastructure,
                tracer_metrics,
                settings,
            )

        forged_total = tracer_metrics.blocks_forged or 0
        missed_total = tracer_metrics.slots_missed or 0
        opp_total = tracer_metrics.leader_opportunities or 0
        cannot_total = tracer_metrics.cannot_forge or 0

        delta = self._counters.observe(
            pool_id,
            blocks_forged=forged_total,
            slots_missed=missed_total,
            leader_opportunities=opp_total,
            cannot_forge=cannot_total,
        )

        forged = delta.forged
        missed = delta.missed + delta.cannot_forge
        opportunities = delta.opportunities

        # First poll after startup: counters exist but deltas are zero — use totals
        # only when we have leader opportunities signal; never invent private slots.
        if delta.reset and opportunities == 0 and forged == 0 and missed == 0:
            if opp_total > 0:
                opportunities = opp_total
                forged = forged_total
                missed = missed_total + cannot_total
            else:
                # Tracer up but no leader counters yet — unknown duty window.
                return self._unknown_duty_operator(
                    index,
                    pool_id,
                    consensus,
                    infrastructure,
                    tracer_metrics,
                    settings,
                    note="Leader slot counters not available yet; duty window unknown.",
                )

        if opportunities == 0 and forged > 0:
            # Observed forges without opportunity counter — treat forged as successful only.
            opportunities = forged + missed

        effectiveness = cardano_effectiveness(
            opportunities=opportunities,
            forged=forged,
        )

        kes = tracer_metrics.remaining_kes_periods
        events: list[ProtocolEvent] = []
        status = "registered"

        if kes is not None:
            if kes <= 0:
                status = "kes_expired"
                events.append(
                    ProtocolEvent(
                        kind="kes_expired",
                        severity="critical",
                        message="KES operational certificate expired — renew op-cert and KES keys.",
                        confirmed=True,
                    )
                )
                events.append(
                    ProtocolEvent(
                        kind="suspended",
                        severity="critical",
                        message="Block producer cannot forge until KES is renewed.",
                        confirmed=True,
                    )
                )
            elif kes <= settings.alert_cardano_kes_critical:
                status = "kes_critical"
                events.append(
                    ProtocolEvent(
                        kind="kes_expired",
                        severity="critical",
                        message=(
                            f"KES expires in {kes} period(s) "
                            f"(critical threshold {settings.alert_cardano_kes_critical})."
                        ),
                        confirmed=False,
                    )
                )
            elif kes <= settings.alert_cardano_kes_warning:
                status = "kes_warning"
                events.append(
                    ProtocolEvent(
                        kind="kes_expired",
                        severity="warning",
                        message=(
                            f"KES expires in {kes} period(s) "
                            f"(warning threshold {settings.alert_cardano_kes_warning})."
                        ),
                        confirmed=False,
                    )
                )

        if tracer_metrics.forging_enabled == 0 and status == "registered":
            status = "cannot_forge"
            events.append(
                ProtocolEvent(
                    kind="suspended",
                    severity="critical",
                    message="Forging disabled on block producer.",
                    confirmed=True,
                )
            )

        if missed > 0 and status not in {"kes_expired", "cannot_forge"}:
            status = "degraded"
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Missed {missed} slot(s) in poll window — expected reward loss, "
                        "not stake slashing."
                    ),
                    confirmed=True,
                )
            )

        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed * 2, 40),
            missed_secondary_duties=0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if status in {"kes_expired", "cannot_forge"}:
            risk = 100.0
        elif status in {"kes_critical", "kes_warning"}:
            risk = max(risk, 70.0 if status == "kes_critical" else 55.0)
        elif missed > 0:
            risk = max(risk, 40.0)

        rewards = int(forged * 50_000)  # placeholder lovelace window from forged blocks

        return ValidatorStats(
            index=index,
            operator_id=pool_id,
            operator_index=index,
            pubkey=pool_id,
            status=status,
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(
                expected=opportunities if opportunities > 0 else 0,
                successful=forged,
                missed=missed,
                late=0,
            ),
            proposals=ProposalStats(
                expected=opportunities if opportunities > 0 else 0,
                successful=forged,
                missed=missed,
            ),
            duties=[
                DutyStats(
                    category="leader_slot",
                    label="Leader slots",
                    expected=opportunities if opportunities > 0 else None,
                    successful=forged,
                    missed=missed,
                    late=0,
                    weight=0.85,
                ),
                DutyStats(
                    category="block",
                    label="Blocks forged",
                    expected=(forged + missed) if (forged + missed) > 0 else None,
                    successful=forged,
                    missed=missed,
                    late=0,
                    weight=0.15,
                ),
            ],
            rewards_base_units=rewards,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="suspension",
            protocol_events=events,
        )

    def _unknown_duty_operator(
        self,
        index: int,
        pool_id: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        tracer_metrics,
        settings: Settings,
        *,
        note: str | None = None,
    ) -> ValidatorStats:
        err = note or consensus.last_error or "cardano-tracer metrics unavailable"
        kes = tracer_metrics.remaining_kes_periods
        events: list[ProtocolEvent] = [
            ProtocolEvent(
                kind="rpc_error",
                severity="warning",
                message=err,
                confirmed=False,
            )
        ]
        status = "unknown"
        risk = 30.0

        if kes is not None and kes <= settings.alert_cardano_kes_critical:
            status = "kes_critical" if kes > 0 else "kes_expired"
            events.append(
                ProtocolEvent(
                    kind="kes_expired",
                    severity="critical" if kes <= 0 else "warning",
                    message=f"KES remaining periods: {kes}",
                    confirmed=kes <= 0,
                )
            )
            risk = 85.0 if kes > 0 else 100.0

        return ValidatorStats(
            index=index,
            operator_id=pool_id,
            operator_index=index,
            pubkey=pool_id,
            status=status,
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(expected=0, successful=0, missed=0),
            duties=[
                DutyStats(
                    category="leader_slot",
                    label="Leader slots",
                    expected=None,
                    successful=0,
                    missed=0,
                    late=0,
                    weight=1.0,
                )
            ],
            rewards_base_units=0,
            effectiveness_score=0.0,
            risk_score=risk,
            risk_kind="suspension",
            protocol_events=events,
        )
