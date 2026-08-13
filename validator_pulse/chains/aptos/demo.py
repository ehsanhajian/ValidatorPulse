from __future__ import annotations

from validator_pulse.chains.aptos.scoring import aptos_effectiveness
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

OCTA = 10**8

_DEMO_POOLS = {
    "active": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "degraded": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "inactive": "0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
}


def demo_pool_address(kind: str) -> str:
    return _DEMO_POOLS[kind]


def build_demo_aptos_consensus(now_height: int = 900_000_000) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_height,
        finalized_epoch=16_000,
        justified_epoch=16_000,
        peer_count=0,
        connected_peers=0,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    pool_addresses: list[str] | None = None,
) -> list[ValidatorStats]:
    kinds = ("active", "degraded", "inactive")
    if pool_addresses:
        planned = [(addr, kinds[i % len(kinds)]) for i, addr in enumerate(pool_addresses)]
    else:
        planned = [(demo_pool_address(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (pool, kind) in enumerate(planned):
        if kind == "active":
            success, failed = 48, 1
            status = "active"
            stake = 5_000_000 * OCTA
            rewards = 1_200 * OCTA
            in_set = True
            events: list[ProtocolEvent] = []
        elif kind == "degraded":
            success, failed = 20, 12
            status = "degraded"
            stake = 2_000_000 * OCTA
            rewards = 200 * OCTA
            in_set = True
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Elevated failed proposals this epoch "
                        f"({failed} failed / {success + failed} total) — reward risk."
                    ),
                    confirmed=True,
                )
            ]
        else:
            success, failed = 0, 0
            status = "inactive"
            stake = 500_000 * OCTA
            rewards = 0
            in_set = False
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message="Pool is inactive — not in the current validator set.",
                    confirmed=True,
                )
            ]

        expected = success + failed
        effectiveness = aptos_effectiveness(
            successful=success,
            failed=failed,
            in_set=in_set,
            syncing=consensus.syncing,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(failed, 40),
            missed_secondary_duties=0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if kind == "inactive":
            risk = 100.0
        elif kind == "degraded":
            risk = max(risk, 55.0)

        operators.append(
            ValidatorStats(
                index=index,
                operator_id=pool,
                operator_index=index,
                pubkey=pool,
                status=status,
                balance_base_units=stake + max(rewards, 0),
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=0,
                    successful=0,
                    missed=0,
                    late=0,
                ),
                proposals=ProposalStats(
                    expected=expected,
                    successful=success,
                    missed=failed,
                ),
                duties=[
                    DutyStats(
                        category="block",
                        label="Proposals",
                        expected=expected if in_set else None,
                        successful=success,
                        missed=failed,
                        late=0,
                        weight=0.85,
                    ),
                    DutyStats(
                        category="other",
                        label="Set membership",
                        expected=1 if in_set else 0,
                        successful=1 if in_set else 0,
                        missed=0 if in_set else 1,
                        late=0,
                        weight=0.15,
                    ),
                ],
                rewards_base_units=rewards,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="reward_loss",
                protocol_events=events,
                display_name=f"Aptos · {kind}",
                display_name_source="demo",
            )
        )

    return operators
