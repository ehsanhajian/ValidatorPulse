from __future__ import annotations

import time

from validator_pulse.chains.ton.state import (
    DOCS_EFFICIENCY_THRESHOLD,
    EfficiencyThreshold,
    NANOTON,
    is_adnl,
    ton_effectiveness,
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

_THRESHOLD = EfficiencyThreshold(percent=DOCS_EFFICIENCY_THRESHOLD, source="docs-efficiency")

_DEMO_IDS = {
    "healthy": "A1" + "11" * 31,
    "degrading": "B2" + "22" * 31,
    "fined": "C3" + "33" * 31,
    "recovery": "D4" + "44" * 31,
}


def demo_adnl(kind: str) -> str:
    return _DEMO_IDS[kind]


def build_demo_ton_consensus(now: float | None = None) -> ConsensusHealth:
    now_s = int(now or time.time())
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=2,
        head_slot=now_s,
        finalized_epoch=now_s // 65536,
        justified_epoch=now_s // 65536,
        peer_count=12,
        connected_peers=12,
        last_error=None,
        status="healthy",
    )


def apply_demo_infrastructure(infrastructure: InfrastructureHealth) -> InfrastructureHealth:
    return infrastructure


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    now: float | None = None,
) -> list[ValidatorStats]:
    now_s = now or time.time()
    cycle_id = int(now_s) // 65536 * 65536
    operators: list[ValidatorStats] = []
    for kind, adnl in _DEMO_IDS.items():
        assert is_adnl(adnl)
        in_set = kind != "recovery"
        fined = kind == "fined"
        missed_election = kind == "recovery"
        if kind == "healthy":
            efficiency = 99.2
            index = 12
            status = "active"
            stake = 1_200_000 * NANOTON
        elif kind == "degrading":
            efficiency = 81.0
            index = 140
            status = "degraded"
            stake = 800_000 * NANOTON
        elif kind == "fined":
            efficiency = 62.0
            index = 8
            status = "fined"
            stake = 1_000_000 * NANOTON
        else:
            efficiency = None
            index = None
            status = "recovering"
            stake = 0
        actionable = kind in {"healthy", "degrading", "fined"}
        effectiveness = ton_effectiveness(
            in_set=in_set,
            efficiency=efficiency,
            efficiency_actionable=actionable,
            fined=fined,
            missed_election=missed_election,
            severe_lag=False,
            recovering=kind == "recovery",
        )
        events: list[ProtocolEvent] = [
            ProtocolEvent(
                kind="other",
                severity="info",
                message=(
                    f"ADNL {adnl} cycle={cycle_id} index={index if index is not None else 'n/a'} "
                    f"role={'masterchain' if index is not None and index < 100 else 'shard' if index else 'none'} "
                    f"in_set={in_set} election={'submitted' if in_set else 'missed'} "
                    f"freshness=ok. Prior-window history retained for rotated ADNLs."
                ),
                confirmed=True,
            )
        ]
        if efficiency is not None:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning" if kind == "degrading" else "info",
                    message=(
                        f"Catchain efficiency {efficiency:.1f}% "
                        f"(threshold {_THRESHOLD.label()}). "
                        f"Completed round {'below' if kind == 'degrading' else 'above'} policy."
                    ),
                    confirmed=True,
                )
            )
        if kind == "fined":
            events.append(
                ProtocolEvent(
                    kind="fined",
                    severity="critical",
                    message=(
                        "Confirmed complaint/fine 101 GRAM for low catchain participation. "
                        "Operational fine risk, not Ethereum-style principal slashing."
                    ),
                    confirmed=True,
                )
            )
        if kind == "recovery":
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        "Missed the latest election window; recovering stake timing. "
                        "Prior ADNL round history is still in the TTL window."
                    ),
                    confirmed=True,
                )
            )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=0 if kind == "healthy" else 3,
            missed_secondary_duties=0 if kind != "degrading" else 2,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=False,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if fined:
            risk = max(risk, 92.0)
        elif kind == "degrading":
            risk = max(risk, 55.0)
        elif kind == "recovery":
            risk = max(risk, 40.0)
        operators.append(
            ValidatorStats(
                index=index or 0,
                operator_id=adnl,
                pubkey=adnl,
                status=status,
                balance_base_units=stake,
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=100 if efficiency is not None else 0,
                    successful=int(efficiency or 0),
                    missed=max(0, 100 - int(efficiency or 0)),
                    late=0,
                ),
                proposals=ProposalStats(
                    expected=1 if in_set else 0,
                    successful=1 if kind == "healthy" else 0,
                    missed=0 if kind == "healthy" else 1,
                ),
                duties=[
                    DutyStats(
                        category="round",
                        label="Validation rounds",
                        expected=1 if in_set else 0,
                        successful=1 if kind == "healthy" else 0,
                        missed=1 if kind == "degrading" else 0,
                        weight=1.0,
                    ),
                    DutyStats(
                        category="other",
                        label="Catchain efficiency",
                        expected=100 if efficiency is not None else None,
                        successful=int(efficiency or 0),
                        missed=max(0, 100 - int(efficiency or 0)),
                        weight=0.8,
                    ),
                ],
                rewards_base_units=0,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="operational",
                protocol_events=events,
            )
        )
    return operators


def build_demo_collection(
    infrastructure: InfrastructureHealth | None = None,
    now: float | None = None,
):
    from validator_pulse.chains.base import ChainCollection

    infra = infrastructure or build_demo_infrastructure()
    consensus = build_demo_ton_consensus(now)
    return ChainCollection(
        consensus=consensus,
        operators=build_demo_validators(consensus, infra, now),
        infrastructure=apply_demo_infrastructure(infra),
    )
