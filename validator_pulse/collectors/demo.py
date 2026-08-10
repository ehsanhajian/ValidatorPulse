from __future__ import annotations

import math
import time

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
    if roll < miss_rate + 0.04:
        return "late"
    return "success"


def build_demo_consensus(now: float | None = None) -> ConsensusHealth:
    now_ms = int((now or time.time()) * 1000)
    slot = now_ms // 12_000
    epoch = slot // 32
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=slot,
        finalized_epoch=epoch - 2,
        justified_epoch=epoch - 1,
        peer_count=68,
        connected_peers=64,
        status="healthy",
    )


def build_demo_infrastructure(
    base: InfrastructureHealth, now: float | None = None
) -> InfrastructureHealth:
    now_s = now or time.time()
    wobble = _seeded_noise(int(now_s // 30), 3)
    disk_usage_percent = round(38 + wobble * 12, 1)
    clock_drift_ms = round(_seeded_noise(int(now_s // 60), 7) * 80, 1)
    disk_total = base.disk_total_bytes or int(1.2e12)

    next_infra = base.model_copy(
        update={
            "disk_usage_percent": disk_usage_percent,
            "disk_used_bytes": int(disk_total * (disk_usage_percent / 100)),
            "clock_drift_ms": clock_drift_ms,
            "network_rx_bytes_per_sec": round(900_000 + wobble * 800_000),
            "network_tx_bytes_per_sec": round(600_000 + wobble * 500_000),
        }
    )

    critical = (
        not next_infra.network_healthy
        or next_infra.clock_drift_ms > 1000
        or next_infra.disk_usage_percent >= 95
        or next_infra.memory_usage_percent >= 95
        or next_infra.cpu_usage_percent >= 95
    )
    degraded = (
        next_infra.disk_usage_percent >= 85
        or next_infra.memory_usage_percent >= 85
        or next_infra.cpu_usage_percent >= 85
        or next_infra.disk_latency_ms > 50
        or next_infra.clock_drift_ms > 500
    )
    status = "critical" if critical else "degraded" if degraded else "healthy"
    return next_infra.model_copy(update={"status": status})


def build_demo_validators(
    indices: list[int],
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    now: float | None = None,
) -> list[ValidatorStats]:
    now_ms = int((now or time.time()) * 1000)
    head_epoch = consensus.head_slot // 32
    validators: list[ValidatorStats] = []

    for i, index in enumerate(indices):
        miss_bias = 0.02 if index == indices[0] else 0.005 + i * 0.002
        expected_att = 32
        expected_prop = 1 if i == 0 else 0

        recent_attestations: list[AttestationDuty] = []
        successful = missed = late = consecutive_missed = rewards = 0

        for e in range(expected_att):
            roll = _seeded_noise(index, head_epoch - e + now_ms // 86_400_000)
            outcome = _pick_outcome(roll, miss_bias)
            if outcome == "success":
                successful += 1
                consecutive_missed = 0
                reward = 18_000 + int(roll * 4_000)
                rewards += reward
                delay = 1
            elif outcome == "late":
                late += 1
                consecutive_missed = 0
                reward = 9_000
                rewards += reward
                delay = 2 + int(roll * 3)
            else:
                missed += 1
                consecutive_missed += 1
                reward = 0
                delay = None

            recent_attestations.append(
                AttestationDuty(
                    epoch=head_epoch - e,
                    slot=(head_epoch - e) * 32 + ((index + e) % 32),
                    validator_index=index,
                    outcome=outcome,
                    inclusion_delay=delay,
                    reward_gwei=reward,
                )
            )

        prop_roll = _seeded_noise(index, head_epoch)
        if expected_prop == 0:
            prop_outcome: DutyOutcome = "pending"
            proposals_successful = proposals_missed = 0
            recent_proposals: list[ProposalDuty] = []
        else:
            prop_outcome = "missed" if prop_roll < 0.05 else "success"
            proposals_successful = 1 if prop_outcome == "success" else 0
            proposals_missed = 1 if prop_outcome == "missed" else 0
            if prop_outcome == "success":
                rewards += 25_000_000
            recent_proposals = [
                ProposalDuty(
                    epoch=head_epoch,
                    slot=head_epoch * 32 + (index % 32),
                    validator_index=index,
                    outcome=prop_outcome,
                    reward_gwei=25_000_000 if prop_outcome == "success" else 0,
                )
            ]

        effectiveness = compute_effectiveness_score(
            attestations_expected=expected_att,
            attestations_successful=successful,
            attestations_late=late,
            proposals_expected=expected_prop,
            proposals_successful=proposals_successful,
        )
        slashing_risk = compute_slashing_risk_score(
            consecutive_missed_attestations=consecutive_missed,
            missed_proposals=proposals_missed,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )

        pubkey = f"0xdemo{index:08x}" + ("a" * 88)
        validators.append(
            ValidatorStats(
                index=index,
                operator_id=pubkey[:98],
                operator_index=index,
                pubkey=pubkey[:98],
                status="active_ongoing",
                balance_gwei=32_000_000_000 + rewards,
                effective_balance_gwei=32_000_000_000,
                attestations=AttestationStats(
                    expected=expected_att,
                    successful=successful,
                    missed=missed,
                    late=late,
                ),
                proposals=ProposalStats(
                    expected=expected_prop,
                    successful=proposals_successful,
                    missed=proposals_missed,
                ),
                rewards_gwei=rewards,
                effectiveness_score=effectiveness,
                slashing_risk_score=slashing_risk,
                risk_kind="slashing",
                recent_attestations=recent_attestations[:8],
                recent_proposals=recent_proposals,
            )
        )

    return validators
