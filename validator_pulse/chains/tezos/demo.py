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

MUTEZ = 10**6

_DEMO_BAKERS = {
    "healthy": "tz1HealthyBaker1111111111111111111111",
    "missed_rights": "tz1MissedBaker222222222222222222222",
    "low_remaining": "tz1LowRemain3333333333333333333333",
    "forbidden": "tz1ForbiddenBaker4444444444444444444",
}


def demo_baker_address(kind: str) -> str:
    return _DEMO_BAKERS[kind]


def tezos_effectiveness(*, attest_expected: int, attest_ok: int, bake_expected: int, bake_ok: int) -> float:
    parts: list[tuple[float, float]] = []
    if attest_expected > 0:
        parts.append(((attest_ok / attest_expected) * 100.0, 0.65))
    if bake_expected > 0:
        parts.append(((bake_ok / bake_expected) * 100.0, 0.35))
    if not parts:
        return 100.0
    weight = sum(w for _, w in parts)
    return round(min(100.0, max(0.0, sum(s * w for s, w in parts) / weight)), 1)


def build_demo_tezos_consensus(now_level: int = 3_500_000) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_level,
        finalized_epoch=now_level,
        justified_epoch=now_level,
        peer_count=18,
        connected_peers=16,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    baker_addresses: list[str] | None = None,
    remaining_miss_alert: int = 2,
) -> list[ValidatorStats]:
    kinds = ("healthy", "missed_rights", "low_remaining", "forbidden")
    if baker_addresses:
        planned = [(addr, kinds[i % len(kinds)]) for i, addr in enumerate(baker_addresses)]
    else:
        planned = [(demo_baker_address(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (baker, kind) in enumerate(planned):
        if kind == "healthy":
            attest_exp, attest_ok = 120, 118
            bake_exp, bake_ok = 8, 8
            remaining = 20
            status = "active"
            stake = 2_000_000 * MUTEZ
            rewards = 45_000_000
            events: list[ProtocolEvent] = []
        elif kind == "missed_rights":
            attest_exp, attest_ok = 100, 88
            bake_exp, bake_ok = 6, 4
            remaining = 8
            status = "degraded"
            stake = 1_200_000 * MUTEZ
            rewards = 12_000_000
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="Missed attestation/baking rights in current cycle.",
                    confirmed=True,
                )
            ]
        elif kind == "low_remaining":
            attest_exp, attest_ok = 90, 72
            bake_exp, bake_ok = 5, 4
            remaining = remaining_miss_alert
            status = "near_reward_loss"
            stake = 900_000 * MUTEZ
            rewards = 5_000_000
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=f"Only {remaining} allowed misses remain before reward loss.",
                    confirmed=True,
                )
            ]
        else:
            attest_exp, attest_ok = 50, 10
            bake_exp, bake_ok = 4, 0
            remaining = 0
            status = "forbidden"
            stake = 0
            rewards = 0
            events = [
                ProtocolEvent(
                    kind="slashed",
                    severity="critical",
                    message="Delegate forbidden after double-signing evidence.",
                    confirmed=True,
                ),
                ProtocolEvent(
                    kind="suspended",
                    severity="critical",
                    message="Baker deactivated / forbidden — slashing risk critical.",
                    confirmed=True,
                ),
            ]

        attest_missed = max(0, attest_exp - attest_ok)
        bake_missed = max(0, bake_exp - bake_ok)
        effectiveness = tezos_effectiveness(
            attest_expected=attest_exp,
            attest_ok=attest_ok,
            bake_expected=bake_exp,
            bake_ok=bake_ok,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(attest_missed // 3, 40),
            missed_secondary_duties=min(bake_missed, 20),
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )
        if kind == "forbidden":
            risk = 100.0
        elif kind == "low_remaining":
            risk = max(risk, 75.0)
        elif kind == "missed_rights":
            risk = max(risk, 50.0)

        operators.append(
            ValidatorStats(
                index=index,
                operator_id=baker,
                operator_index=index,
                pubkey=baker,
                status=status,
                balance_base_units=stake + max(rewards, 0),
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=attest_exp,
                    successful=attest_ok,
                    missed=attest_missed,
                    late=0,
                ),
                proposals=ProposalStats(
                    expected=bake_exp,
                    successful=bake_ok,
                    missed=bake_missed,
                ),
                duties=[
                    DutyStats(
                        category="attestation",
                        label="Attestations",
                        expected=attest_exp,
                        successful=attest_ok,
                        missed=attest_missed,
                        late=0,
                        weight=0.65,
                    ),
                    DutyStats(
                        category="block",
                        label="Baking rights",
                        expected=bake_exp,
                        successful=bake_ok,
                        missed=bake_missed,
                        late=0,
                        weight=0.35,
                    ),
                ],
                rewards_base_units=rewards,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="slashing",
                protocol_events=events,
                display_name=f"Tezos · {kind.replace('_', ' ')} (remaining misses {remaining})",
                display_name_source="demo",
            )
        )

    return operators
