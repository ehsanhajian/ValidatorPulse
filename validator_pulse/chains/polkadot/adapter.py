from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.polkadot.demo import (
    apply_demo_infrastructure,
    build_demo_collator_consensus,
    build_demo_collators,
    build_demo_relay_consensus,
    build_demo_relay_validators,
)
from validator_pulse.chains.polkadot.rpc import collect_substrate_consensus
from validator_pulse.chains.polkadot.tokens import resolve_reward_token
from validator_pulse.config import Settings
from validator_pulse.http_client import (
    RpcHttpConfig,
    bind_rpc_http_config,
    reset_rpc_http_config,
)
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

_DEFAULT_DEMO_VALIDATORS = [
    "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY",
    "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty",
]


class PolkadotAdapter:
    """Polkadot adapter for parachain collators or relay NPoS validators.

    Call ``configure(settings)`` (done by ``collect_pulse``) so labels and risk
    semantics match ``POLKADOT_ROLE`` / ``CHAIN=polkadot-relay``.
    """

    name = "polkadot"
    display_name = "Polkadot"
    operator_label = "collator"
    risk_kind = "operational"
    risk_label = "Downtime risk"
    primary_duty_label = "Collations"
    secondary_duty_label = "Blocks"
    missed_duty_label = "Missed collations"
    consensus_node_label = "Substrate node"
    role = "collator"

    def configure(self, settings: Settings) -> None:
        self.apply_role(settings.resolved_polkadot_role())

    def apply_role(self, role: str) -> None:
        role = (role or "collator").strip().lower()
        if role not in {"collator", "validator"}:
            role = "collator"
        self.role = role
        if role == "validator":
            self.display_name = "Polkadot relay"
            self.operator_label = "validator"
            self.risk_kind = "slashing"
            self.risk_label = "Slashing risk"
            self.primary_duty_label = "Era points"
            self.secondary_duty_label = "Blocks"
            self.missed_duty_label = "Missed era points"
            self.consensus_node_label = "Relay node"
        else:
            self.display_name = "Polkadot"
            self.operator_label = "collator"
            self.risk_kind = "operational"
            self.risk_label = "Downtime risk"
            self.primary_duty_label = "Collations"
            self.secondary_duty_label = "Blocks"
            self.missed_duty_label = "Missed collations"
            self.consensus_node_label = "Substrate node"

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.substrate_rpc_url and settings.substrate_rpc_url.strip())

    async def collect(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        self.configure(settings)
        if self.is_demo(settings):
            return self._collect_demo(settings, infrastructure)
        return await self._collect_live(settings, infrastructure)

    def _collect_demo(
        self,
        settings: Settings,
        infrastructure: InfrastructureHealth,
    ) -> ChainCollection:
        infrastructure = apply_demo_infrastructure(infrastructure)
        if self.role == "validator":
            consensus = build_demo_relay_consensus()
            addresses = (
                settings.validator_stash_address_list() or list(_DEFAULT_DEMO_VALIDATORS)
            )
            token = resolve_reward_token(
                chain=self.name,
                parachain_id=None,
                symbol_override=settings.reward_token_symbol,
                decimals_override=settings.reward_token_decimals,
            )
            operators = build_demo_relay_validators(
                addresses,
                consensus,
                infrastructure,
                token_decimals=token.decimals,
            )
        else:
            consensus = build_demo_collator_consensus()
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
        token = bind_rpc_http_config(RpcHttpConfig.from_settings(settings))
        try:
            consensus = await collect_substrate_consensus(settings.substrate_rpc_url)
            if self.role == "validator":
                addresses = settings.validator_stash_address_list()
                missing_msg = "No VALIDATOR_STASH_ADDRESSES configured"
                builders = self._live_relay_validator_stats
            else:
                addresses = settings.collator_address_list()
                missing_msg = "No COLLATOR_ADDRESSES configured"
                builders = self._live_collator_stats

            if not addresses:
                err = missing_msg
                if consensus.last_error:
                    err = f"{missing_msg}; {consensus.last_error}"
                consensus = consensus.model_copy(
                    update={
                        "status": "degraded" if consensus.status == "healthy" else consensus.status,
                        "last_error": err,
                    }
                )
                return ChainCollection(
                    consensus=consensus,
                    operators=[],
                    infrastructure=infrastructure,
                )

            operators = [
                builders(i, address, consensus, infrastructure, settings)
                for i, address in enumerate(addresses)
            ]
            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

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

    def _live_relay_validator_stats(
        self,
        index: int,
        address: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
    ) -> ValidatorStats:
        """Map relay-node health into era-point / block scores for a stash address.

        Full active-set / era-point decoding needs SCALE + staking storage. Until
        then we derive a conservative window from sync/peer/reachability so live
        mode still surfaces offline and sync risk for configured stashes.
        """
        del settings  # reserved for future session-key / sidecar config
        expected_points = 100
        expected_blocks = 2

        if not consensus.beacon_reachable:
            points_ok, late, missed = 0, 0, expected_points
            blocks_ok, blocks_missed = 0, expected_blocks
            status = "unreachable"
        elif consensus.syncing or consensus.sync_distance > 20:
            points_ok, late, missed = 55, 5, 40
            blocks_ok, blocks_missed = 0, expected_blocks
            status = "syncing"
        elif consensus.connected_peers < 5:
            points_ok, late, missed = 70, 4, 26
            blocks_ok, blocks_missed = 1, 1
            status = "offline"
        else:
            points_ok, late, missed = 96, 2, 2
            blocks_ok, blocks_missed = expected_blocks, 0
            status = "active_validator"

        effectiveness = compute_effectiveness_score(
            attestations_expected=expected_points,
            attestations_successful=points_ok,
            attestations_late=late,
            proposals_expected=expected_blocks,
            proposals_successful=blocks_ok,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=missed,
            missed_secondary_duties=blocks_missed,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )
        if status in {"unreachable", "offline"}:
            risk = max(risk, 65.0)

        return ValidatorStats(
            index=index,
            operator_id=address,
            operator_index=index,
            pubkey=address,
            status=status,
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(
                expected=expected_points,
                successful=points_ok,
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
                    category="round",
                    label="Era points",
                    expected=expected_points,
                    successful=points_ok,
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
            risk_kind="slashing",
        )


__all__ = ["PolkadotAdapter"]
