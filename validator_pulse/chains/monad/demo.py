from __future__ import annotations

from validator_pulse.chains.monad.state import monad_effectiveness
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

WEI = 10**18

_DEMO_IDS = {
    "healthy": 101,
    "lag": 202,
    "failed": 303,
    "set_transition": 404,
}


def demo_validator_id(kind: str) -> int:
    return _DEMO_IDS[kind]


def build_demo_monad_consensus(now_block: int = 38_200_000) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_block,
        finalized_epoch=764,
        justified_epoch=764,
        peer_count=48,
        connected_peers=48,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    validator_ids: list[int] | None = None,
) -> list[ValidatorStats]:
    kinds = ("healthy", "lag", "failed", "set_transition")
    if validator_ids:
        planned = [(vid, kinds[i % len(kinds)]) for i, vid in enumerate(validator_ids)]
    else:
        planned = [(demo_validator_id(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (vid, kind) in enumerate(planned):
        if kind == "healthy":
            authored, missed = 36, 0
            local = True
            in_set = True
            eligible = True
            lagging = False
            status = "active"
            stake = 12_000_000 * WEI
            events: list[ProtocolEvent] = []
        elif kind == "lag":
            authored, missed = 20, 1
            local = True
            in_set = True
            eligible = True
            lagging = True
            status = "lagging"
            stake = 11_000_000 * WEI
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="Consensus lag: local round/head behind network tip.",
                    confirmed=True,
                )
            ]
        elif kind == "failed":
            authored, missed = 4, 8
            local = True
            in_set = True
            eligible = True
            lagging = False
            status = "degraded"
            stake = 10_500_000 * WEI
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Local ledger evidence: {missed} missed proposals "
                        f"({authored} authored) — reward/eligibility risk, not slashing."
                    ),
                    confirmed=True,
                )
            ]
        else:
            authored, missed = 0, 0
            local = False
            in_set = False
            eligible = True
            lagging = False
            status = "pending_set"
            stake = 10_000_000 * WEI
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        "Set transition: validator is not in the current consensus "
                        "leader set (snapshot/execution pending next epoch)."
                    ),
                    confirmed=True,
                )
            ]

        effectiveness = monad_effectiveness(
            in_consensus_set=in_set,
            eligible=eligible,
            local_evidence=local,
            authored=authored,
            missed=missed,
            lagging=lagging,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed, 40),
            missed_secondary_duties=8 if lagging else 0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing or lagging,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if kind == "failed":
            risk = max(risk, 70.0)
        elif kind == "lag":
            risk = max(risk, 50.0)
        elif kind == "set_transition":
            risk = max(risk, 55.0)

        expected = authored + missed if local else None
        operators.append(
            ValidatorStats(
                index=index,
                operator_id=str(vid),
                operator_index=vid,
                pubkey=str(vid),
                status=status,
                balance_base_units=stake,
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=0, successful=0, missed=0, late=0
                ),
                proposals=ProposalStats(
                    expected=expected or 0,
                    successful=authored,
                    missed=missed,
                ),
                duties=[
                    DutyStats(
                        category="block",
                        label="Proposals",
                        expected=expected,
                        successful=authored,
                        missed=missed,
                        late=0,
                        weight=0.75,
                    ),
                    DutyStats(
                        category="other",
                        label="Set membership",
                        expected=1 if in_set else 0,
                        successful=1 if in_set else 0,
                        missed=0 if in_set else 1,
                        late=0,
                        weight=0.25,
                    ),
                ],
                rewards_base_units=0,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="reward_loss",
                protocol_events=events,
                display_name=f"Monad · {kind.replace('_', ' ')} (val {vid})",
                display_name_source="demo",
            )
        )
    return operators
