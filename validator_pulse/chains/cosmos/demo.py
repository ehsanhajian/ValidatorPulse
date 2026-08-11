from __future__ import annotations

from validator_pulse.chains.cosmos.bech32 import bech32_encode
from validator_pulse.chains.cosmos.profiles import CosmosProfile
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
from validator_pulse.scoring import compute_effectiveness_score, compute_slashing_risk_score

# Fixed 20-byte payloads → valid Bech32 for offline demos (profile HRP applied at build).
_DEMO_KEYS = {
    "healthy": bytes([1] * 19 + [10]),
    "near_jail": bytes([2] * 19 + [11]),
    "jailed": bytes([3] * 19 + [12]),
    "tombstoned": bytes([4] * 19 + [13]),
}


def demo_operator_address(kind: str, profile: CosmosProfile) -> str:
    raw = _DEMO_KEYS[kind]
    return bech32_encode(profile.valoper_prefix, raw)


def demo_consensus_address(kind: str, profile: CosmosProfile) -> str:
    raw = _DEMO_KEYS[kind]
    return bech32_encode(profile.valcons_prefix, raw)


def build_demo_comet_consensus(now_height: int = 12_345_678) -> ConsensusHealth:
    return ConsensusHealth(
        beacon_reachable=True,
        syncing=False,
        sync_distance=0,
        head_slot=now_height,
        finalized_epoch=now_height - 1,
        justified_epoch=now_height,
        peer_count=40,
        connected_peers=38,
        status="healthy",
    )


def apply_demo_infrastructure(base: InfrastructureHealth) -> InfrastructureHealth:
    return build_demo_infrastructure(base)


def build_demo_validators(
    profile: CosmosProfile,
    consensus: ConsensusHealth,
    infrastructure: InfrastructureHealth,
    *,
    operator_addresses: list[str] | None = None,
    signed_blocks_window: int = 10_000,
) -> list[ValidatorStats]:
    """Profile-aware demo: healthy, near-jail, jailed, tombstoned scenarios."""
    kinds = ("healthy", "near_jail", "jailed", "tombstoned")
    if operator_addresses:
        # Map provided addresses onto the four scenarios in order (cycle if needed).
        planned = [
            (addr, kinds[i % len(kinds)]) for i, addr in enumerate(operator_addresses)
        ]
    else:
        planned = [(demo_operator_address(kind, profile), kind) for kind in kinds]

    unit = 10 ** max(profile.token_decimals, 0)
    operators: list[ValidatorStats] = []

    for index, (address, kind) in enumerate(planned):
        if kind == "healthy":
            missed, signed = 12, signed_blocks_window - 12
            status = "BOND_STATUS_BONDED"
            jail = False
            tomb = False
            voting_power = 1_250_000
        elif kind == "near_jail":
            # Just under the typical 5% downtime threshold for a 10k window.
            missed, signed = 480, signed_blocks_window - 480
            status = "BOND_STATUS_BONDED"
            jail = False
            tomb = False
            voting_power = 900_000
        elif kind == "jailed":
            missed, signed = 900, signed_blocks_window - 900
            status = "BOND_STATUS_BONDED"
            jail = True
            tomb = False
            voting_power = 0
        else:  # tombstoned
            missed, signed = signed_blocks_window, 0
            status = "BOND_STATUS_UNBONDED"
            jail = True
            tomb = True
            voting_power = 0

        expected = signed_blocks_window
        successful = signed
        late = 0
        effectiveness = compute_effectiveness_score(
            attestations_expected=expected,
            attestations_successful=successful,
            attestations_late=late,
            proposals_expected=0,
            proposals_successful=0,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed, 40),
            missed_secondary_duties=1 if jail or tomb else 0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )
        events: list[ProtocolEvent] = []
        if tomb:
            risk = 100.0
            events.append(
                ProtocolEvent(
                    kind="tombstoned",
                    severity="critical",
                    message="Validator is tombstoned (double-sign evidence).",
                    confirmed=True,
                )
            )
            status = "tombstoned"
        elif jail:
            risk = max(risk, 85.0)
            events.append(
                ProtocolEvent(
                    kind="jailed",
                    severity="critical",
                    message="Validator is jailed for downtime.",
                    confirmed=True,
                )
            )
            status = "jailed"
        elif kind == "near_jail":
            risk = max(risk, 55.0)
            status = "active_near_jail"

        cons = demo_consensus_address(kind, profile)
        rewards = int(unit * (0.15 if kind == "healthy" else 0.02))
        operators.append(
            ValidatorStats(
                index=index,
                operator_id=address,
                operator_index=index,
                pubkey=cons,
                status=status,
                balance_base_units=voting_power * unit // 1_000_000 + rewards,
                effective_balance_base_units=voting_power * unit // 1_000_000,
                attestations=AttestationStats(
                    expected=expected,
                    successful=successful,
                    missed=missed,
                    late=late,
                ),
                proposals=ProposalStats(expected=0, successful=0, missed=0),
                duties=[
                    DutyStats(
                        category="block",
                        label="Signed blocks",
                        expected=expected,
                        successful=successful,
                        missed=missed,
                        late=late,
                        weight=1.0,
                    )
                ],
                rewards_base_units=rewards,
                effectiveness_score=effectiveness,
                risk_score=risk,
                risk_kind="slashing",
                protocol_events=events,
                display_name=f"{profile.display_name} · {kind.replace('_', ' ')}",
                display_name_source="demo",
            )
        )

    return operators
