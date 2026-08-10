from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.polkadot.demo import (
    apply_demo_infrastructure,
    build_demo_collator_consensus,
    build_demo_collators,
)
from validator_pulse.chains.polkadot.rpc import collect_substrate_consensus
from validator_pulse.chains.polkadot.tokens import resolve_reward_token
from validator_pulse.config import Settings
from validator_pulse.models import (
    AttestationStats,
    ConsensusHealth,
    DutyStats,
    InfrastructureHealth,
    ProposalStats,
    ValidatorStats,
)
from validator_pulse.scoring import (
    compute_effectiveness_score,
    compute_slashing_risk_score,
)

_DEFAULT_DEMO_COLLATORS = [
    "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
    "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
]


class PolkadotAdapter:
    name = "polkadot"
    display_name = "Polkadot"
    operator_label = "collator"
    risk_kind = "operational"
    risk_label = "Downtime risk"
    primary_duty_label = "Collations"
    secondary_duty_label = "Blocks"
    missed_duty_label = "Missed collations"
    consensus_node_label = "Substrate node"

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.substrate_rpc_url and settings.substrate_rpc_url.strip())

    async def collect(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        if self.is_demo(settings):
            return self._collect_demo(settings, infrastructure)
        return await self._collect_live(settings, infrastructure)

    def _collect_demo(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        consensus = build_demo_collator_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        addresses = settings.collator_address_list() or list(_DEFAULT_DEMO_COLLATORS)
        token = resolve_reward_token(
            chain=self.name,
            parachain_id=settings.parachain_id,
            symbol_override=settings.reward_token_symbol,
            decimals_override=settings.reward_token_decimals,
        )
        operators = build_demo_collators(
            addresses,
            consensus,
            infrastructure,
            parachain_id=settings.parachain_id,
            token_decimals=token.decimals,
        )
        return ChainCollection(
            consensus=consensus,
            operators=operators,
            infrastructure=infrastructure,
        )

    async def _collect_live(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        assert settings.substrate_rpc_url
        consensus = await collect_substrate_consensus(settings.substrate_rpc_url)
        addresses = settings.collator_address_list()
        if not addresses:
            consensus = consensus.model_copy(
                update={
                    "status": "degraded" if consensus.status == "healthy" else consensus.status,
                    "last_error": consensus.last_error
                    or "No COLLATOR_ADDRESSES configured",
                }
            )
            return ChainCollection(
                consensus=consensus,
                operators=[],
                infrastructure=infrastructure,
            )

        operators = [
            self._live_collator_stats(i, address, consensus, infrastructure, settings)
            for i, address in enumerate(addresses)
        ]
        return ChainCollection(
            consensus=consensus,
            operators=operators,
            infrastructure=infrastructure,
        )

    def _live_collator_stats(
        self,
        index: int,
        address: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
    ) -> ValidatorStats:
        """Map node health into collation/block-production scores for an SS58 address.

        Full author decoding needs SCALE codecs; until then we derive a conservative
        window from sync/peer/clock signals for the collator's node.
        """
        expected_rounds = 24
        expected_blocks = 4

        if not consensus.beacon_reachable:
            successful, late, missed = 0, 0, expected_rounds
            blocks_ok, blocks_missed = 0, expected_blocks
            status = "unreachable"
        elif consensus.syncing or consensus.sync_distance > 20:
            successful, late, missed = 16, 2, 6
            blocks_ok, blocks_missed = 2, 2
            status = "syncing"
        elif consensus.connected_peers < 3:
            successful, late, missed = 20, 1, 3
            blocks_ok, blocks_missed = 3, 1
            status = "low_peers"
        else:
            successful, late, missed = 23, 1, 0
            blocks_ok, blocks_missed = 4, 0
            status = "active_collator"

        if settings.parachain_id is not None and status.startswith("active"):
            status = f"{status}_para_{settings.parachain_id}"

        effectiveness = compute_effectiveness_score(
            attestations_expected=expected_rounds,
            attestations_successful=successful,
            attestations_late=late,
            proposals_expected=expected_blocks,
            proposals_successful=blocks_ok,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_attestations=missed,
            missed_proposals=blocks_missed,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )

        return ValidatorStats(
            index=index,
            operator_id=address,
            operator_index=index,
            pubkey=address,
            status=status,
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(
                expected=expected_rounds,
                successful=successful,
                missed=missed,
                late=late,
            ),
            proposals=ProposalStats(
                expected=expected_blocks,
                successful=blocks_ok,
                missed=blocks_missed,
            ),
            duties=[
                DutyStats(
                    category="collation",
                    label="Collations",
                    expected=expected_rounds,
                    successful=successful,
                    missed=missed,
                    late=late,
                    weight=0.85,
                ),
                DutyStats(
                    category="block",
                    label="Blocks",
                    expected=expected_blocks,
                    successful=blocks_ok,
                    missed=blocks_missed,
                    weight=0.15 if expected_blocks else 0.0,
                ),
            ],
            rewards_base_units=0,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="operational",
        )


__all__ = ["PolkadotAdapter"]
