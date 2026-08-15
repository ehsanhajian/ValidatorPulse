from __future__ import annotations

from validator_pulse.chains.multiversx.state import (
    DOCS_JAIL_RATING,
    JailThreshold,
    mx_effectiveness,
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

WEI = 10**18
_JAIL = JailThreshold(rating=DOCS_JAIL_RATING, source="docs-rating")

_DEMO_IDS = {
    "healthy": "a1" + "11" * 95,
    "degrading": "b2" + "22" * 95,
    "jailed": "c3" + "33" * 95,
    "recovery": "d4" + "44" * 95,
}


def demo_bls_key(kind: str) -> str:
    return _DEMO_IDS[kind]


def build_demo_mx_consensus() -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=31_000_000,
        finalized_epoch=2200,
        justified_epoch=31_770_000,
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
    bls_keys: list[str] | None = None,
) -> list[ValidatorStats]:
    kinds = ("healthy", "degrading", "jailed", "recovery")
    if bls_keys:
        planned = [(key, kinds[i % len(kinds)]) for i, key in enumerate(bls_keys)]
    else:
        planned = [(demo_bls_key(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (bls, kind) in enumerate(planned):
        if kind == "healthy":
            rating, temp = 96.0, 97.0
            peer_type, status = "eligible", "active"
            active = True
            lead_ok, lead_fail = 40, 0
            val_ok, val_fail = 8000, 12
            shard = 0
            jailed = slashed = passive = False
        elif kind == "degrading":
            rating, temp = 16.0, 14.5
            peer_type, status = "eligible", "degraded"
            active = True
            lead_ok, lead_fail = 8, 4
            val_ok, val_fail = 2000, 400
            shard = 1
            jailed = slashed = passive = False
        elif kind == "jailed":
            rating, temp = 6.0, 5.0
            peer_type, status = "jailed", "jailed"
            active = False
            lead_ok, lead_fail = 0, 0
            val_ok, val_fail = 0, 0
            shard = 4_294_967_295
            jailed, slashed, passive = True, False, False
        else:
            rating, temp = 50.0, 50.0
            peer_type, status = "waiting", "recovering"
            active = True
            lead_ok, lead_fail = 0, 0
            val_ok, val_fail = 0, 0
            shard = 2
            jailed = slashed = False
            passive = True

        proposal_ratio = lead_ok / (lead_ok + lead_fail) if lead_ok + lead_fail else None
        sig_ratio = val_ok / (val_ok + val_fail) if val_ok + val_fail else None
        effectiveness = mx_effectiveness(
            heartbeat_active=active,
            jailed=jailed,
            slashed=slashed,
            rating=rating,
            jail_threshold=_JAIL.rating,
            proposal_ratio=proposal_ratio,
            signature_ratio=sig_ratio,
            passive=passive,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=lead_fail,
            missed_secondary_duties=min(val_fail, 20),
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=not active,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if jailed:
            risk = max(risk, 92.0)
        elif rating <= 20:
            risk = max(risk, 70.0)
        elif passive:
            risk = max(risk, 35.0)

        operators.append(
            ValidatorStats(
                index=index,
                operator_id=bls,
                pubkey=bls,
                status=status,
                balance_base_units=2500 * WEI,
                effective_balance_base_units=2500 * WEI,
                attestations=AttestationStats(
                    expected=val_ok + val_fail,
                    successful=val_ok,
                    missed=val_fail,
                    late=0,
                ),
                proposals=ProposalStats(
                    expected=lead_ok + lead_fail,
                    successful=lead_ok,
                    missed=lead_fail,
                ),
                duties=[
                    DutyStats(
                        category="proposal",
                        label="Leader proposals",
                        expected=lead_ok + lead_fail if lead_ok + lead_fail else None,
                        successful=lead_ok,
                        missed=lead_fail,
                        late=0,
                        weight=0.5,
                    ),
                    DutyStats(
                        category="vote",
                        label="Consensus signatures",
                        expected=val_ok + val_fail if val_ok + val_fail else None,
                        successful=val_ok,
                        missed=val_fail,
                        late=0,
                        weight=0.5,
                    ),
                ],
                rewards_base_units=0,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="jail",
                protocol_events=_demo_events(
                    kind, peer_type, rating, temp, shard, active
                ),
            )
        )
    return operators


def _demo_events(
    kind: str,
    peer_type: str,
    rating: float,
    temp: float,
    shard: int,
    active: bool,
) -> list[ProtocolEvent]:
    events = [
        ProtocolEvent(
            kind="other",
            severity="info",
            message=(
                f"Heartbeat active={active} peerType={peer_type} shard={shard} "
                f"rating={rating:.1f} tempRating={temp:.1f} "
                f"jail threshold {_JAIL.label()}. Epoch-boundary jail/slash."
            ),
            confirmed=True,
        )
    ]
    if kind == "degrading":
        events.append(
            ProtocolEvent(
                kind="other",
                severity="critical",
                message=(
                    f"Rating {rating:.1f} is near jail threshold {_JAIL.label()}. "
                    "Downtime jail (not serious-offence slash) at epoch end."
                ),
                confirmed=True,
            )
        )
    elif kind == "jailed":
        events.append(
            ProtocolEvent(
                kind="jailed",
                severity="critical",
                message=(
                    "Validator jailed for rating below threshold — downtime jail, "
                    "not stake slashing. Unjail tx required; then passive recovery."
                ),
                confirmed=True,
            )
        )
    elif kind == "recovery":
        events.append(
            ProtocolEvent(
                kind="other",
                severity="warning",
                message=(
                    "Recently unjailed: waiting/passive this epoch while recovering "
                    "(rating reset to 50). Not yet eligible for consensus."
                ),
                confirmed=True,
            )
        )
    return events
