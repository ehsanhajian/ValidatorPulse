from __future__ import annotations

from validator_pulse.chains.sui.metrics import sui_effectiveness
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

MIST = 10**9

_DEMO_VALIDATORS = {
    "healthy": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "degraded": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "critical": "0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
}


def demo_validator_address(kind: str) -> str:
    return _DEMO_VALIDATORS[kind]


def build_demo_sui_consensus(now_checkpoint: int = 300_000_000) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_checkpoint,
        finalized_epoch=1_200,
        justified_epoch=1_200,
        peer_count=12,
        connected_peers=12,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    validator_addresses: list[str] | None = None,
) -> list[ValidatorStats]:
    kinds = ("healthy", "degraded", "critical")
    if validator_addresses:
        planned = [
            (addr, kinds[i % len(kinds)]) for i, addr in enumerate(validator_addresses)
        ]
    else:
        planned = [(demo_validator_address(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (address, kind) in enumerate(planned):
        if kind == "healthy":
            proposals, checkpoints = 42, 120
            at_risk = 0
            reported = False
            status = "active"
            stake = 30_000_000 * MIST
            events: list[ProtocolEvent] = []
            metrics_ok = True
        elif kind == "degraded":
            proposals, checkpoints = 0, 5
            at_risk = 2
            reported = False
            status = "at_risk"
            stake = 8_000_000 * MIST
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Low-stake atRisk for {at_risk} epochs "
                        "(distinct from reward slashing)."
                    ),
                    confirmed=True,
                ),
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="No consensus proposals observed in recent metrics window.",
                    confirmed=True,
                ),
            ]
            metrics_ok = True
        else:
            proposals, checkpoints = 0, 0
            at_risk = 0
            reported = True
            status = "reward_slashed"
            stake = 15_000_000 * MIST
            events = [
                ProtocolEvent(
                    kind="slashed",
                    severity="critical",
                    message="Validator has report records — reward slashing risk (not principal).",
                    confirmed=True,
                )
            ]
            metrics_ok = True

        effectiveness = sui_effectiveness(
            in_set=True,
            proposals_delta=proposals,
            checkpoint_advancing=checkpoints > 0,
            at_risk_epochs=at_risk,
            reported=reported,
            safe_mode=False,
            metrics_available=metrics_ok,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=0 if proposals else 15,
            missed_secondary_duties=0 if checkpoints else 5,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if kind == "critical":
            risk = 100.0
        elif kind == "degraded":
            risk = max(risk, 60.0)

        operators.append(
            ValidatorStats(
                index=index,
                operator_id=address,
                operator_index=index,
                pubkey=address,
                status=status,
                balance_base_units=stake,
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=0, successful=0, missed=0, late=0
                ),
                proposals=ProposalStats(
                    expected=proposals if metrics_ok else 0,
                    successful=proposals,
                    missed=0,
                ),
                duties=[
                    DutyStats(
                        category="block",
                        label="Proposals",
                        expected=proposals if metrics_ok else None,
                        successful=proposals,
                        missed=0,
                        late=0,
                        weight=0.55,
                    ),
                    DutyStats(
                        category="checkpoint",
                        label="Checkpoints",
                        expected=checkpoints if metrics_ok else None,
                        successful=checkpoints,
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
                display_name=f"Sui · {kind}",
                display_name_source="demo",
            )
        )

    return operators
