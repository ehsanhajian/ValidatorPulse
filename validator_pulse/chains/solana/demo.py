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
from validator_pulse.scoring import compute_effectiveness_score, compute_slashing_risk_score

# Stable offline demo vote / identity pubkeys (base58-looking; not real accounts).
_DEMO_ACCOUNTS = {
    "healthy": (
        "Vote11111111111111111111111111111112",
        "Node11111111111111111111111111111112",
    ),
    "high_skip": (
        "Vote22222222222222222222222222222223",
        "Node22222222222222222222222222222223",
    ),
    "delinquent": (
        "Vote33333333333333333333333333333334",
        "Node33333333333333333333333333333334",
    ),
    "low_credits": (
        "Vote44444444444444444444444444444445",
        "Node44444444444444444444444444444445",
    ),
}


def demo_vote_account(kind: str) -> str:
    return _DEMO_ACCOUNTS[kind][0]


def demo_identity(kind: str) -> str:
    return _DEMO_ACCOUNTS[kind][1]


def build_demo_solana_consensus(now_slot: int = 250_000_000) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_slot,
        finalized_epoch=650,
        justified_epoch=650,
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
    vote_accounts: list[str] | None = None,
) -> list[ValidatorStats]:
    """Offline demo: healthy, high skip, delinquent, low credits."""
    kinds = ("healthy", "high_skip", "delinquent", "low_credits")
    if vote_accounts:
        planned = [
            (addr, kinds[i % len(kinds)]) for i, addr in enumerate(vote_accounts)
        ]
    else:
        planned = [(demo_vote_account(kind), kind) for kind in kinds]

    lamports = 10**9
    operators: list[ValidatorStats] = []

    for index, (vote_pubkey, kind) in enumerate(planned):
        identity = demo_identity(kind) if vote_pubkey == demo_vote_account(kind) else f"NodeDemo{index}"

        if kind == "healthy":
            leader_slots, produced = 200, 196
            credits, expected_credits = 400_000, 400_000
            delinquent = False
            status = "active"
            stake = 1_500_000 * lamports
        elif kind == "high_skip":
            leader_slots, produced = 200, 150
            credits, expected_credits = 360_000, 400_000
            delinquent = False
            status = "high_skip"
            stake = 900_000 * lamports
        elif kind == "delinquent":
            leader_slots, produced = 50, 10
            credits, expected_credits = 20_000, 400_000
            delinquent = True
            status = "delinquent"
            stake = 100_000 * lamports
        else:  # low_credits
            leader_slots, produced = 180, 170
            credits, expected_credits = 80_000, 400_000
            delinquent = False
            status = "low_credits"
            stake = 600_000 * lamports

        skipped = max(0, leader_slots - produced)
        skip_rate = (skipped / leader_slots * 100.0) if leader_slots else 0.0
        missed_credits = max(0, expected_credits - credits)

        effectiveness = compute_effectiveness_score(
            attestations_expected=expected_credits,
            attestations_successful=credits,
            attestations_late=0,
            proposals_expected=leader_slots,
            proposals_successful=produced,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed_credits // 10_000, 40),
            missed_secondary_duties=min(skipped, 20),
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 8),
            effectiveness_score=effectiveness,
        )

        events: list[ProtocolEvent] = []
        if delinquent:
            risk = 100.0
            events.append(
                ProtocolEvent(
                    kind="delinquent",
                    severity="critical",
                    message="Vote account is delinquent (last vote lagging cluster tip).",
                    confirmed=True,
                )
            )
        elif skip_rate >= 10.0:
            risk = max(risk, 55.0)
            events.append(
                ProtocolEvent(
                    kind="high_skip_rate",
                    severity="warning",
                    message=f"Skip rate {skip_rate:.1f}% exceeds healthy leader performance.",
                    confirmed=True,
                )
            )

        rewards = int(lamports * (0.12 if kind == "healthy" else 0.02))
        operators.append(
            ValidatorStats(
                index=index,
                operator_id=vote_pubkey,
                operator_index=index,
                pubkey=identity,
                status=status,
                balance_base_units=stake + rewards,
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=expected_credits,
                    successful=credits,
                    missed=missed_credits,
                    late=0,
                ),
                proposals=ProposalStats(
                    expected=leader_slots,
                    successful=produced,
                    missed=skipped,
                ),
                duties=[
                    DutyStats(
                        category="vote",
                        label="Epoch credits",
                        expected=expected_credits,
                        successful=credits,
                        missed=missed_credits,
                        late=0,
                        weight=0.7,
                    ),
                    DutyStats(
                        category="block",
                        label="Leader slots",
                        expected=leader_slots,
                        successful=produced,
                        missed=skipped,
                        late=0,
                        weight=0.3,
                    ),
                ],
                rewards_base_units=rewards,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="slashing",
                protocol_events=events,
                display_name=f"Solana · {kind.replace('_', ' ')}",
                display_name_source="demo",
            )
        )

    return operators
