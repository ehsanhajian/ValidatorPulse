from __future__ import annotations

from validator_pulse.collectors.demo import build_demo_infrastructure
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

LOVELACE = 10**6

_DEMO_POOLS = {
    "healthy": "pool1healthy",
    "missed_slots": "pool1missed",
    "kes_warning": "pool1keswarn",
    "kes_expired": "pool1kesexp",
}


def demo_pool_id(kind: str) -> str:
    return _DEMO_POOLS[kind]


def cardano_effectiveness(*, opportunities: int, forged: int) -> float:
    if opportunities <= 0:
        return 100.0
    return round(min(100.0, max(0.0, (forged / opportunities) * 100.0)), 1)


def build_demo_cardano_consensus(now_slot: int = 12_345_678) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_slot,
        finalized_epoch=450,
        justified_epoch=450,
        peer_count=28,
        connected_peers=26,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    pool_ids: list[str] | None = None,
    kes_warning_periods: int = 5,
    kes_critical_periods: int = 1,
) -> list[ValidatorStats]:
    """Offline demo: healthy forge, missed slots, KES warning, KES expired."""
    kinds = ("healthy", "missed_slots", "kes_warning", "kes_expired")
    if pool_ids:
        planned = [(pid, kinds[i % len(kinds)]) for i, pid in enumerate(pool_ids)]
    else:
        planned = [(demo_pool_id(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (pool_id, kind) in enumerate(planned):
        if kind == "healthy":
            opportunities, forged, missed = 8, 8, 0
            kes_remaining = 40
            status = "registered"
            stake = 25_000_000 * LOVELACE
            rewards = 1_250_000
            events: list[ProtocolEvent] = []
        elif kind == "missed_slots":
            opportunities, forged, missed = 6, 4, 2
            kes_remaining = 25
            status = "degraded"
            stake = 18_000_000 * LOVELACE
            rewards = 400_000
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="Missed leader slots reduce expected block rewards (not stake slashing).",
                    confirmed=True,
                )
            ]
        elif kind == "kes_warning":
            opportunities, forged, missed = 5, 5, 0
            kes_remaining = kes_warning_periods
            status = "kes_warning"
            stake = 20_000_000 * LOVELACE
            rewards = 900_000
            events = [
                ProtocolEvent(
                    kind="kes_expired",
                    severity="warning",
                    message=f"KES operational certificate expires in {kes_remaining} period(s).",
                    confirmed=False,
                )
            ]
        else:  # kes_expired
            opportunities, forged, missed = 3, 0, 3
            kes_remaining = 0
            status = "kes_expired"
            stake = 15_000_000 * LOVELACE
            rewards = -150_000
            events = [
                ProtocolEvent(
                    kind="kes_expired",
                    severity="critical",
                    message="KES operational certificate expired — block forging halted.",
                    confirmed=True,
                ),
                ProtocolEvent(
                    kind="suspended",
                    severity="critical",
                    message="Producer cannot forge blocks until KES/op-cert is renewed.",
                    confirmed=True,
                ),
            ]

        effectiveness = cardano_effectiveness(opportunities=opportunities, forged=forged)
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed * 2, 40),
            missed_secondary_duties=0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )
        if kind == "kes_expired":
            risk = 100.0
        elif kind == "kes_warning":
            risk = max(risk, 65.0)
        elif kind == "missed_slots":
            risk = max(risk, 45.0)

        operators.append(
            ValidatorStats(
                index=index,
                operator_id=pool_id,
                operator_index=index,
                pubkey=pool_id,
                status=status,
                balance_base_units=stake + max(rewards, 0),
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=opportunities,
                    successful=forged,
                    missed=missed,
                    late=0,
                ),
                proposals=ProposalStats(
                    expected=opportunities,
                    successful=forged,
                    missed=missed,
                ),
                duties=[
                    DutyStats(
                        category="leader_slot",
                        label="Leader slots",
                        expected=opportunities,
                        successful=forged,
                        missed=missed,
                        late=0,
                        weight=0.85,
                    ),
                    DutyStats(
                        category="block",
                        label="Blocks forged",
                        expected=forged + missed,
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
                display_name=f"Cardano · {kind.replace('_', ' ')} (KES {kes_remaining})",
                display_name_source="demo",
            )
        )

    return operators
