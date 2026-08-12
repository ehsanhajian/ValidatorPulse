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

YOCTO = 10**24

_DEMO_ACCOUNTS = {
    "healthy": "healthy.pool.near",
    "near_kickout": "nearkickout.pool.near",
    "set_transition": "transition.pool.near",
    "slashed": "slashed.pool.near",
}


def demo_account_id(kind: str) -> str:
    return _DEMO_ACCOUNTS[kind]


def near_effectiveness(
    *,
    expected_blocks: int,
    produced_blocks: int,
    expected_chunks: int,
    produced_chunks: int,
    expected_endorsements: int,
    produced_endorsements: int,
) -> float:
    """Blocks weighted highest, then chunks, then endorsements."""
    parts: list[tuple[float, float]] = []
    if expected_blocks > 0:
        parts.append(((produced_blocks / expected_blocks) * 100.0, 0.5))
    if expected_chunks > 0:
        parts.append(((produced_chunks / expected_chunks) * 100.0, 0.3))
    if expected_endorsements > 0:
        parts.append(((produced_endorsements / expected_endorsements) * 100.0, 0.2))
    if not parts:
        return 100.0
    weight_sum = sum(w for _, w in parts)
    score = sum(s * w for s, w in parts) / weight_sum
    return round(min(100.0, max(0.0, score)), 1)


def build_demo_near_consensus(now_height: int = 120_000_000) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_height,
        finalized_epoch=now_height,
        justified_epoch=now_height,
        peer_count=45,
        connected_peers=42,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    account_ids: list[str] | None = None,
) -> list[ValidatorStats]:
    """Offline demo: healthy, near-kickout, set-transition, slashed."""
    kinds = ("healthy", "near_kickout", "set_transition", "slashed")
    if account_ids:
        planned = [(addr, kinds[i % len(kinds)]) for i, addr in enumerate(account_ids)]
    else:
        planned = [(demo_account_id(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (account_id, kind) in enumerate(planned):
        if kind == "healthy":
            eb, pb = 100, 99
            ec, pc = 800, 790
            ee, pe = 2000, 1980
            status = "active"
            stake = 5_000_000 * YOCTO
            rewards = int(0.12 * YOCTO)
            events: list[ProtocolEvent] = []
            in_next = True
        elif kind == "near_kickout":
            eb, pb = 100, 70
            ec, pc = 800, 520
            ee, pe = 2000, 1400
            status = "near_kickout"
            stake = 2_000_000 * YOCTO
            rewards = int(0.02 * YOCTO)
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="Chunk/block completion approaching kickout thresholds.",
                    confirmed=False,
                )
            ]
            in_next = True
        elif kind == "set_transition":
            # Leaving current set but proposing for next (or vice versa).
            eb, pb = 40, 38
            ec, pc = 200, 190
            ee, pe = 400, 380
            status = "set_transition"
            stake = 1_500_000 * YOCTO
            rewards = int(0.05 * YOCTO)
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message="Validator is transitioning between current and next epoch sets.",
                    confirmed=True,
                )
            ]
            in_next = False
        else:  # slashed
            eb, pb = 100, 0
            ec, pc = 800, 0
            ee, pe = 2000, 0
            status = "slashed"
            stake = 0
            rewards = 0
            events = [
                ProtocolEvent(
                    kind="slashed",
                    severity="critical",
                    message="Validator is_slashed=true (malicious / double-sign style penalty).",
                    confirmed=True,
                )
            ]
            in_next = False

        missed_blocks = max(0, eb - pb)
        missed_chunks = max(0, ec - pc)
        missed_endorsements = max(0, ee - pe)
        effectiveness = near_effectiveness(
            expected_blocks=eb,
            produced_blocks=pb,
            expected_chunks=ec,
            produced_chunks=pc,
            expected_endorsements=ee,
            produced_endorsements=pe,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed_blocks + missed_chunks // 10, 40),
            missed_secondary_duties=min(missed_endorsements // 50, 20),
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )
        if kind == "slashed":
            risk = 100.0
        elif kind == "near_kickout":
            risk = max(risk, 70.0)
            events.append(
                ProtocolEvent(
                    kind="kicked",
                    severity="warning",
                    message="Elevated kickout risk from incomplete blocks/chunks.",
                    confirmed=False,
                )
            )
        elif kind == "set_transition" and not in_next:
            risk = max(risk, 35.0)

        # Primary display: chunks map to attestation-shaped stats; blocks to proposals.
        operators.append(
            ValidatorStats(
                index=index,
                operator_id=account_id,
                operator_index=index,
                pubkey=account_id,
                status=status,
                balance_base_units=stake + rewards,
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=ec + ee,
                    successful=pc + pe,
                    missed=missed_chunks + missed_endorsements,
                    late=0,
                ),
                proposals=ProposalStats(
                    expected=eb,
                    successful=pb,
                    missed=missed_blocks,
                ),
                duties=[
                    DutyStats(
                        category="block",
                        label="Blocks",
                        expected=eb,
                        successful=pb,
                        missed=missed_blocks,
                        late=0,
                        weight=0.5,
                    ),
                    DutyStats(
                        category="chunk",
                        label="Chunks",
                        expected=ec,
                        successful=pc,
                        missed=missed_chunks,
                        late=0,
                        weight=0.3,
                    ),
                    DutyStats(
                        category="endorsement",
                        label="Endorsements",
                        expected=ee,
                        successful=pe,
                        missed=missed_endorsements,
                        late=0,
                        weight=0.2,
                    ),
                ],
                rewards_base_units=rewards,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="kickout",
                protocol_events=events,
                display_name=f"NEAR · {kind.replace('_', ' ')}",
                display_name_source="demo",
            )
        )

    return operators
