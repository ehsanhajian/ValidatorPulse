from __future__ import annotations

from validator_pulse.chains.mina.state import (
    SlotOutcome,
    mina_effectiveness,
)
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

NANOMINA = 10**9

_DEMO_IDS = {
    "schedule": "B62qSchedule1111111111111111111111111111111",
    "orphan": "B62qOrphan111111111111111111111111111111111",
    "miss": "B62qMissed111111111111111111111111111111111",
    "unsynced": "B62qUnsynced1111111111111111111111111111111",
    "recovery": "B62qRecover11111111111111111111111111111111",
}


def demo_producer_key(kind: str) -> str:
    return _DEMO_IDS[kind]


def build_demo_mina_consensus() -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=410_000,
        finalized_epoch=57,
        justified_epoch=57,
        peer_count=18,
        connected_peers=18,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    public_keys: list[str] | None = None,
) -> list[ValidatorStats]:
    kinds = ("schedule", "orphan", "miss", "unsynced", "recovery")
    if public_keys:
        planned = [(key, kinds[i % len(kinds)]) for i, key in enumerate(public_keys)]
    else:
        planned = [(demo_producer_key(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (pubkey, kind) in enumerate(planned):
        if kind == "schedule":
            outcomes = [
                SlotOutcome(slot=100, kind="canonical"),
                SlotOutcome(slot=140, kind="canonical"),
                SlotOutcome(slot=180, kind="pending"),
            ]
            status = "active"
            synced = True
            activated = True
            coinbase = 144 * NANOMINA
        elif kind == "orphan":
            outcomes = [
                SlotOutcome(slot=90, kind="canonical"),
                SlotOutcome(slot=120, kind="orphaned"),
            ]
            status = "orphaned"
            synced = True
            activated = True
            coinbase = 72 * NANOMINA
        elif kind == "miss":
            outcomes = [
                SlotOutcome(slot=80, kind="canonical"),
                SlotOutcome(slot=110, kind="missed"),
                SlotOutcome(slot=130, kind="missed"),
            ]
            status = "missed"
            synced = True
            activated = True
            coinbase = 72 * NANOMINA
        elif kind == "unsynced":
            outcomes = [SlotOutcome(slot=consensus.head_slot + 1, kind="pending")]
            status = "unsynced"
            synced = False
            activated = True
            coinbase = 0
        else:
            outcomes = [
                SlotOutcome(slot=70, kind="missed"),
                SlotOutcome(slot=150, kind="canonical"),
                SlotOutcome(slot=170, kind="canonical"),
            ]
            status = "recovering"
            synced = True
            activated = True
            coinbase = 144 * NANOMINA

        completed = [o for o in outcomes if o.kind != "pending"]
        canonical = sum(1 for o in completed if o.kind == "canonical")
        missed = sum(1 for o in completed if o.kind == "missed")
        orphaned = sum(1 for o in completed if o.kind == "orphaned")
        pending = sum(1 for o in outcomes if o.kind == "pending")
        expected = len(outcomes)
        effectiveness = mina_effectiveness(
            synced=synced, activated=activated, outcomes=outcomes
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=missed,
            missed_secondary_duties=orphaned,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=not synced,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if kind == "unsynced":
            risk = max(risk, 88.0)
        elif kind == "miss":
            risk = max(risk, 70.0)
        elif kind == "orphan":
            risk = max(risk, 42.0)

        operators.append(
            ValidatorStats(
                index=index,
                operator_id=pubkey,
                pubkey=pubkey,
                status=status,
                balance_base_units=1_000 * NANOMINA,
                effective_balance_base_units=1_000 * NANOMINA,
                attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
                proposals=ProposalStats(
                    expected=expected,
                    successful=canonical,
                    missed=missed,
                ),
                duties=[
                    DutyStats(
                        category="block",
                        label="Won slots",
                        expected=expected,
                        successful=canonical,
                        missed=missed,
                        late=orphaned,
                        weight=1.0,
                    )
                ],
                rewards_base_units=coinbase,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="reward_loss",
                protocol_events=_demo_events(
                    kind, canonical, missed, orphaned, pending, expected
                ),
            )
        )
    return operators


def _demo_events(
    kind: str,
    canonical: int,
    missed: int,
    orphaned: int,
    pending: int,
    expected: int,
) -> list[ProtocolEvent]:
    events = [
        ProtocolEvent(
            kind="other",
            severity="info",
            message=(
                f"Locally observed won slots {expected} "
                f"(canonical={canonical} orphaned={orphaned} missed={missed} "
                f"pending={pending}). Effectiveness uses local VRF evidence only. "
                "Mina does not slash producer stake — reward risk only."
            ),
            confirmed=True,
        )
    ]
    if kind == "schedule":
        events.append(
            ProtocolEvent(
                kind="other",
                severity="info",
                message="Next won slot is pending on the local schedule.",
                confirmed=True,
            )
        )
    elif kind == "orphan":
        events.append(
            ProtocolEvent(
                kind="other",
                severity="warning",
                message=(
                    "Produced block orphaned by a competing slot winner "
                    "(short-range fork). Orphaning is moderate reward risk, "
                    "not slashing."
                ),
                confirmed=True,
            )
        )
    elif kind == "miss":
        events.append(
            ProtocolEvent(
                kind="other",
                severity="critical",
                message=(
                    "Missed locally won slots — coinbase not earned. "
                    "High reward risk; Mina does not slash stake."
                ),
                confirmed=True,
            )
        )
    elif kind == "unsynced":
        events.append(
            ProtocolEvent(
                kind="other",
                severity="critical",
                message=(
                    "Daemon not SYNCED within a won slot — cannot produce. "
                    "Near-slot unsynced state is critical reward risk, not slashing."
                ),
                confirmed=True,
            )
        )
    else:
        events.append(
            ProtocolEvent(
                kind="other",
                severity="warning",
                message=(
                    "Recovering: recent canonical blocks after a miss. "
                    "Reward risk is falling; Mina still does not slash stake."
                ),
                confirmed=True,
            )
        )
    return events
