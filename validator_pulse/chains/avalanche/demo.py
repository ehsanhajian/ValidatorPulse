from __future__ import annotations

from validator_pulse.chains.avalanche.state import (
    RecoveryRunway,
    UptimeThreshold,
    avalanche_effectiveness,
    recovery_runway,
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

NAVAX = 10**9

_DEMO_IDS = {
    "healthy": "NodeID-Healthy111111111111111111111",
    "threshold": "NodeID-Threshold11111111111111111",
    "forfeiture": "NodeID-Forfeit1111111111111111111",
}


def demo_node_id(kind: str) -> str:
    return _DEMO_IDS[kind]


def build_demo_avalanche_consensus() -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=12_000_000,
        finalized_epoch=0,
        justified_epoch=0,
        peer_count=32,
        connected_peers=32,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    node_ids: list[str] | None = None,
    now: float = 1_800_000_000.0,
    runway_warn_hours: float = 24.0,
) -> list[ValidatorStats]:
    kinds = ("healthy", "threshold", "forfeiture")
    if node_ids:
        planned = [(nid, kinds[i % len(kinds)]) for i, nid in enumerate(node_ids)]
    else:
        planned = [(demo_node_id(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (node_id, kind) in enumerate(planned):
        start = int(now - 20 * 86400)
        end = int(now + 10 * 86400)
        requirement = UptimeThreshold(percent=80.0, source="config")
        if kind == "healthy":
            uptime = 99.2
            connected = True
            rewarding = 100.0
            weighted = 99.0
            polls_ok, polls_fail = 400, 1
            status = "active"
            stake = 2_000 * NAVAX
        elif kind == "threshold":
            uptime = 81.5
            connected = True
            rewarding = 62.0
            weighted = 81.0
            polls_ok, polls_fail = 200, 40
            status = "degraded"
            stake = 2_000 * NAVAX
        else:
            uptime = 40.0
            connected = False
            rewarding = 5.0
            weighted = 38.0
            polls_ok, polls_fail = 10, 90
            status = "forfeiture"
            stake = 2_000 * NAVAX
            end = int(now + 2 * 86400)

        recovery = recovery_runway(
            uptime_pct=uptime,
            start_time=start,
            end_time=end,
            now=now,
            requirement_pct=requirement.percent,
        )
        poll_ratio = polls_ok / (polls_ok + polls_fail)
        events = _demo_events(
            kind, requirement, recovery, rewarding, weighted, runway_warn_hours
        )
        effectiveness = avalanche_effectiveness(
            in_set=True,
            uptime_pct=uptime,
            requirement_pct=requirement.percent,
            connected=connected,
            rewarding_stake_pct=rewarding,
            poll_success_ratio=poll_ratio,
            recovery=recovery,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=0 if kind == "healthy" else 8,
            missed_secondary_duties=polls_fail if kind != "healthy" else 0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=not connected,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if kind == "forfeiture":
            risk = max(risk, 90.0)
        elif kind == "threshold":
            risk = max(risk, 55.0)

        missed = 0 if kind == "healthy" else (1 if kind == "threshold" else 8)
        operators.append(
            ValidatorStats(
                index=index,
                operator_id=node_id,
                pubkey=node_id,
                status=status,
                balance_base_units=stake,
                effective_balance_base_units=stake,
                attestations=AttestationStats(
                    expected=0, successful=0, missed=0, late=0
                ),
                proposals=ProposalStats(
                    expected=polls_ok + polls_fail,
                    successful=polls_ok,
                    missed=polls_fail,
                ),
                duties=[
                    DutyStats(
                        category="other",
                        label="Uptime",
                        expected=100,
                        successful=int(round(uptime)),
                        missed=max(0, 100 - int(round(uptime))),
                        late=0,
                        weight=0.7,
                    ),
                    DutyStats(
                        category="poll",
                        label="Consensus polls",
                        expected=polls_ok + polls_fail,
                        successful=polls_ok,
                        missed=polls_fail,
                        late=0,
                        weight=0.3,
                    ),
                ],
                rewards_base_units=0,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="reward_loss",
                protocol_events=events,
            )
        )
    return operators


def _demo_events(
    kind: str,
    requirement: UptimeThreshold,
    recovery: RecoveryRunway,
    rewarding: float,
    weighted: float,
    runway_warn_hours: float,
) -> list[ProtocolEvent]:
    events = [
        ProtocolEvent(
            kind="other",
            severity="info",
            message=(
                f"Local uptime: rewardingStake={rewarding:.1f}% vs "
                f"weightedAverage={weighted:.1f}% (distinct; this node only). "
                f"Requirement {requirement.label()}."
            ),
            confirmed=True,
        )
    ]
    if kind == "threshold":
        events.append(
            ProtocolEvent(
                kind="other",
                severity="warning",
                message=(
                    f"Uptime near reward-eligibility threshold "
                    f"{requirement.label()}; recovery slack "
                    f"{recovery.slack_seconds / 3600:.1f}h "
                    f"(warn below {runway_warn_hours:.0f}h) — reward "
                    "forfeiture risk, not slashing."
                ),
                confirmed=True,
            )
        )
    elif kind == "forfeiture":
        events.append(
            ProtocolEvent(
                kind="other",
                severity="critical",
                message=(
                    "Recovery is impossible before the staking period ends "
                    f"(max final uptime {recovery.max_final_pct:.1f}% vs "
                    f"{requirement.label()}). Reward forfeiture, not principal "
                    "slashing."
                ),
                confirmed=True,
            )
        )
    return events
