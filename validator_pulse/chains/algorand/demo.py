from __future__ import annotations

from validator_pulse.chains.algorand.keys import algorand_effectiveness
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

MICRO = 10**6

_DEMO_ACCOUNTS = {
    "healthy": "ALGORANDHEALTHYACCOUNTADDRESS0000000000000000000001",
    "key_expiring": "ALGORANDKEYEXPIRINGACCOUNTADDR00000000000000000002",
    "key_missing": "ALGORANDKEYMISSINGACCOUNTADDR00000000000000000003",
    "suspended": "ALGORANDSUSPENDEDACCOUNTADDR000000000000000000004",
}


def demo_account_address(kind: str) -> str:
    return _DEMO_ACCOUNTS[kind]


def build_demo_algorand_consensus(now_round: int = 45_000_000) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_round,
        finalized_epoch=now_round,
        justified_epoch=now_round,
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
    account_addresses: list[str] | None = None,
    partkey_warning_rounds: int = 50_000,
) -> list[ValidatorStats]:
    kinds = ("healthy", "key_expiring", "key_missing", "suspended")
    if account_addresses:
        planned = [(addr, kinds[i % len(kinds)]) for i, addr in enumerate(account_addresses)]
    else:
        planned = [(demo_account_address(kind), kind) for kind in kinds]

    operators: list[ValidatorStats] = []
    for index, (address, kind) in enumerate(planned):
        if kind == "healthy":
            online = True
            eligible = True
            partkey_state = "valid"
            votes_obs, proposals_obs = 128, 3
            remaining = partkey_warning_rounds * 4
            status = "online"
            stake = 2_500_000 * MICRO
            rewards = 12_000 * MICRO
            events: list[ProtocolEvent] = []
            activity = True
        elif kind == "key_expiring":
            online = True
            eligible = True
            partkey_state = "expiring"
            votes_obs, proposals_obs = 90, 1
            remaining = max(1, partkey_warning_rounds // 2)
            status = "key_expiring"
            stake = 1_800_000 * MICRO
            rewards = 4_000 * MICRO
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Participation key expires in {remaining} rounds "
                        f"(warning threshold {partkey_warning_rounds})."
                    ),
                    confirmed=True,
                )
            ]
            activity = True
        elif kind == "key_missing":
            online = True
            eligible = False
            partkey_state = "missing"
            votes_obs, proposals_obs = 0, 0
            remaining = None
            status = "key_missing"
            stake = 900_000 * MICRO
            rewards = 0
            events = [
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message="No participation keys found for account on this algod.",
                    confirmed=True,
                )
            ]
            activity = False
        else:
            online = False
            eligible = False
            partkey_state = "valid"
            votes_obs, proposals_obs = 40, 0
            remaining = partkey_warning_rounds * 2
            status = "offline"
            stake = 1_200_000 * MICRO
            rewards = 0
            events = [
                ProtocolEvent(
                    kind="suspended",
                    severity="critical",
                    message="Account transitioned to Offline — suspension/operational risk.",
                    confirmed=True,
                ),
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message="Incentive eligibility became false.",
                    confirmed=True,
                ),
            ]
            activity = False

        effectiveness = algorand_effectiveness(
            online=online,
            incentive_eligible=eligible,
            partkey_state=partkey_state,  # type: ignore[arg-type]
            activity_advancing=activity,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=0 if online else 20,
            missed_secondary_duties=0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if kind == "suspended":
            risk = 100.0
        elif kind == "key_missing":
            risk = max(risk, 90.0)
        elif kind == "key_expiring":
            risk = max(risk, 55.0)

        rem_label = "n/a" if remaining is None else str(remaining)
        operators.append(
            ValidatorStats(
                index=index,
                operator_id=address,
                operator_index=index,
                pubkey=address,
                status=status,
                balance_base_units=stake + max(rewards, 0),
                effective_balance_base_units=stake,
                # Duty expected stays None — committee selection is private/probabilistic.
                # Legacy attestation/proposal aliases use 0 (not invented miss counts).
                attestations=AttestationStats(
                    expected=0,
                    successful=votes_obs,
                    missed=0,
                    late=0,
                ),
                proposals=ProposalStats(
                    expected=0,
                    successful=proposals_obs,
                    missed=0,
                ),
                duties=[
                    DutyStats(
                        category="attestation",
                        label="Observed votes",
                        expected=None,
                        successful=votes_obs,
                        missed=0,
                        late=0,
                        weight=0.7,
                    ),
                    DutyStats(
                        category="block",
                        label="Observed proposals",
                        expected=None,
                        successful=proposals_obs,
                        missed=0,
                        late=0,
                        weight=0.3,
                    ),
                ],
                rewards_base_units=rewards,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="suspension",
                protocol_events=events,
                display_name=f"Algorand · {kind.replace('_', ' ')} (key rem {rem_label})",
                display_name_source="demo",
            )
        )

    return operators
