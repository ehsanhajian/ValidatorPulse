from __future__ import annotations

from validator_pulse.chains.bsc.state import SlashThresholds, bsc_effectiveness
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

_DEMO_ADDRS = {
    "healthy": "0x1111111111111111111111111111111111111101",
    "misses": "0x1111111111111111111111111111111111111102",
    "maintenance": "0x1111111111111111111111111111111111111103",
    "slash": "0x1111111111111111111111111111111111111104",
    "jail": "0x1111111111111111111111111111111111111105",
}


def demo_validator_address(kind: str) -> str:
    return _DEMO_ADDRS[kind]


def build_demo_bsc_consensus(now_block: int = 42_000_000) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_block,
        finalized_epoch=0,
        justified_epoch=0,
        peer_count=64,
        connected_peers=64,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    addresses: list[str] | None = None,
) -> list[ValidatorStats]:
    kinds = ("healthy", "misses", "maintenance", "slash", "jail")
    thresholds = SlashThresholds(misdemeanor=50, felony=150, source="config")
    if addresses:
        planned = [(addr, kinds[i % len(kinds)]) for i, addr in enumerate(addresses)]
    else:
        planned = [(demo_validator_address(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (addr, kind) in enumerate(planned):
        if kind == "healthy":
            slash_count = 2
            jailed = False
            maintaining = False
            in_set = True
            status = "active"
            stake = 10_000 * WEI
            events: list[ProtocolEvent] = [
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"Slash indicator {slash_count}; {thresholds.label()}."
                    ),
                    confirmed=True,
                )
            ]
            double_sign = malicious = False
        elif kind == "misses":
            slash_count = 40
            jailed = False
            maintaining = False
            in_set = True
            status = "degraded"
            stake = 9_500 * WEI
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Slash indicator {slash_count} approaching "
                        f"{thresholds.label()} — missed block turns."
                    ),
                    confirmed=True,
                )
            ]
            double_sign = malicious = False
        elif kind == "maintenance":
            slash_count = 8
            jailed = False
            maintaining = True
            in_set = False
            status = "maintenance"
            stake = 9_000 * WEI
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        "Validator is in maintenance (living set, not working)."
                    ),
                    confirmed=True,
                )
            ]
            double_sign = malicious = False
        elif kind == "slash":
            slash_count = 0
            jailed = True
            maintaining = False
            in_set = False
            status = "slashed"
            stake = 8_000 * WEI
            events = [
                ProtocolEvent(
                    kind="slashed",
                    severity="critical",
                    message=(
                        "Double-sign evidence slashed this validator "
                        "(fast-finality / block header conflict)."
                    ),
                    confirmed=True,
                )
            ]
            double_sign = True
            malicious = False
        else:
            slash_count = 160
            jailed = True
            maintaining = False
            in_set = False
            status = "jailed"
            stake = 8_500 * WEI
            events = [
                ProtocolEvent(
                    kind="jailed",
                    severity="critical",
                    message=(
                        f"Validator jailed after slash indicator {slash_count} "
                        f"exceeded {thresholds.label()}."
                    ),
                    confirmed=True,
                )
            ]
            double_sign = False
            malicious = False

        effectiveness = bsc_effectiveness(
            in_working_set=in_set,
            jailed=jailed,
            maintaining=maintaining,
            slash_count=slash_count,
            misdemeanor=thresholds.misdemeanor,
            double_sign=double_sign,
            malicious_vote=malicious,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(slash_count, 40),
            missed_secondary_duties=8 if maintaining else 0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing or maintaining,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if double_sign or malicious:
            risk = 100.0
        elif jailed:
            risk = max(risk, 95.0)
        elif kind == "misses":
            risk = max(risk, 55.0)
        elif kind == "maintenance":
            risk = max(risk, 45.0)

        missed = slash_count
        produced = 36 if kind == "healthy" else (12 if kind == "misses" else 0)
        expected = produced + missed if kind in {"healthy", "misses"} else None
        operators.append(
            ValidatorStats(
                index=index,
                operator_id=addr,
                pubkey=addr,
                display_name=f"demo-{kind}",
                display_name_source="stakehub",
                status=status,
                balance_base_units=stake,
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=0, successful=0, missed=0, late=0
                ),
                proposals=ProposalStats(
                    expected=expected or 0,
                    successful=produced,
                    missed=missed,
                ),
                duties=[
                    DutyStats(
                        category="block",
                        label="Block turns",
                        expected=expected,
                        successful=produced,
                        missed=missed,
                        late=0,
                        weight=0.7,
                    ),
                    DutyStats(
                        category="vote",
                        label="Finality votes",
                        expected=None if kind == "slash" else (1 if in_set else 0),
                        successful=0 if kind == "slash" else (1 if in_set else 0),
                        missed=1 if kind == "slash" else 0,
                        late=0,
                        weight=0.3,
                    ),
                ],
                rewards_base_units=0,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="slashing",
                protocol_events=events,
            )
        )
    return operators
