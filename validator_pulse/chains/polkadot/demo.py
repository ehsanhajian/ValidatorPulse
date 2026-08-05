from __future__ import annotations

import math
import time

from validator_pulse.collectors.demo import build_demo_infrastructure
from validator_pulse.models import (
    AttestationDuty,
    AttestationStats,
    ConsensusHealth,
    DutyOutcome,
    InfrastructureHealth,
    ProposalDuty,
    ProposalStats,
    ValidatorStats,
)
from validator_pulse.scoring import compute_effectiveness_score, compute_slashing_risk_score


def _seeded_noise(seed: int, salt: int) -> float:
    x = math.sin(seed * 12.9898 + salt * 78.233) * 43758.5453
    return x - math.floor(x)


def _pick_outcome(roll: float, miss_rate: float) -> DutyOutcome:
    if roll < miss_rate:
        return "missed"
    if roll < miss_rate + 0.03:
        return "late"
    return "success"


def build_demo_collator_consensus(now: float | None = None) -> ConsensusHealth:
    now_s = now or time.time()
    block = int(now_s // 6)  # ~6s parachain block time
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=block,
        finalized_epoch=max(0, block - 5),
        justified_epoch=block,
        peer_count=42,
        connected_peers=40,
        status="healthy",
    )


def build_demo_collators(
    addresses: list[str],
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    parachain_id: int | None = None,
    token_decimals: int = 10,
    now: float | None = None,
) -> list[ValidatorStats]:
    """Demo collators — collation rounds map to attestations, authored blocks to proposals."""
    now_s = now or time.time()
    head = consensus.head_slot
    operators: list[ValidatorStats] = []
    unit = 10 ** max(token_decimals, 0)

    for i, address in enumerate(addresses):
        seed = sum(ord(c) for c in address) + i
        miss_bias = 0.015 if i == 0 else 0.004 + i * 0.002
        expected_rounds = 24
        expected_blocks = 4 if i == 0 else 2

        successful = missed = late = consecutive_missed = rewards = 0
        recent_rounds: list[AttestationDuty] = []

        for r in range(expected_rounds):
            roll = _seeded_noise(seed, head - r + int(now_s // 86_400))
            outcome = _pick_outcome(roll, miss_bias)
            if outcome == "success":
                successful += 1
                consecutive_missed = 0
                reward = int(unit * (0.00012 + roll * 0.00003))
                rewards += reward
                delay = 1
            elif outcome == "late":
                late += 1
                consecutive_missed = 0
                reward = int(unit * 0.00006)
                rewards += reward
                delay = 2
            else:
                missed += 1
                consecutive_missed += 1
                reward = 0
                delay = None

            recent_rounds.append(
                AttestationDuty(
                    epoch=max(0, (head - r) // 10),
                    slot=max(0, head - r),
                    validator_index=i,
                    outcome=outcome,
                    inclusion_delay=delay,
                    reward_gwei=reward,
                )
            )

        prop_roll = _seeded_noise(seed, head)
        blocks_successful = max(
            0, expected_blocks - (1 if prop_roll < 0.08 else 0)
        )
        blocks_missed = expected_blocks - blocks_successful
        if blocks_successful:
            rewards += blocks_successful * int(unit * 0.015)

        recent_proposals = [
            ProposalDuty(
                epoch=max(0, head // 10),
                slot=max(0, head - j),
                validator_index=i,
                outcome="success" if j < blocks_successful else "missed",
                reward_gwei=int(unit * 0.015) if j < blocks_successful else 0,
            )
            for j in range(expected_blocks)
        ]

        effectiveness = compute_effectiveness_score(
            attestations_expected=expected_rounds,
            attestations_successful=successful,
            attestations_late=late,
            proposals_expected=expected_blocks,
            proposals_successful=blocks_successful,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_attestations=consecutive_missed,
            missed_proposals=blocks_missed,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )

        status = "active_collator"
        if parachain_id is not None:
            status = f"active_collator_para_{parachain_id}"

        # Demo balances/rewards stored in the token's base units (planck/wei).
        base_balance = 10 * unit
        operators.append(
            ValidatorStats(
                index=i,
                pubkey=address,
                status=status,
                balance_gwei=base_balance + rewards,
                effective_balance_gwei=base_balance,
                attestations=AttestationStats(
                    expected=expected_rounds,
                    successful=successful,
                    missed=missed,
                    late=late,
                ),
                proposals=ProposalStats(
                    expected=expected_blocks,
                    successful=blocks_successful,
                    missed=blocks_missed,
                ),
                rewards_gwei=rewards,
                effectiveness_score=effectiveness,
                slashing_risk_score=risk,
                recent_attestations=recent_rounds[:8],
                recent_proposals=recent_proposals,
            )
        )

    return operators


def apply_demo_infrastructure(
    base: InfrastructureHealth,
) -> InfrastructureHealth:
    return build_demo_infrastructure(base)
