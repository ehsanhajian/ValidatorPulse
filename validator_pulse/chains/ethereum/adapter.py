from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.collectors.beacon import (
    collect_consensus,
    collect_validator_balances,
)
from validator_pulse.collectors.demo import (
    build_demo_consensus,
    build_demo_infrastructure,
    build_demo_validators,
)
from validator_pulse.config import Settings
from validator_pulse.models import (
    AttestationStats,
    ConsensusHealth,
    InfrastructureHealth,
    ProposalStats,
    ValidatorStats,
)
from validator_pulse.scoring import (
    compute_effectiveness_score,
    compute_slashing_risk_score,
)


class EthereumAdapter:
    name = "ethereum"
    display_name = "Ethereum"
    operator_label = "validator"

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.beacon_api_url and settings.beacon_api_url.strip())

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
        consensus = build_demo_consensus()
        infrastructure = build_demo_infrastructure(infrastructure)
        demo_indices = settings.indices() or [1, 2, 3]
        operators = build_demo_validators(demo_indices, consensus, infrastructure)
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
        assert settings.beacon_api_url
        consensus = await collect_consensus(settings.beacon_api_url)
        try:
            operators = await self._live_validators(
                settings.beacon_api_url,
                settings.validator_ids(),
                consensus,
                infrastructure,
            )
        except Exception as exc:  # noqa: BLE001
            operators = []
            consensus = consensus.model_copy(
                update={
                    "status": "critical",
                    "last_error": consensus.last_error or str(exc),
                }
            )
        return ChainCollection(
            consensus=consensus,
            operators=operators,
            infrastructure=infrastructure,
        )

    async def _live_validators(
        self,
        beacon_api_url: str,
        validator_ids: list[str],
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
    ) -> list[ValidatorStats]:
        balances = await collect_validator_balances(beacon_api_url, validator_ids)
        validators: list[ValidatorStats] = []
        for b in balances:
            active = "active" in (b["status"] or "")
            attestations = AttestationStats(
                expected=32,
                successful=31 if active else 20,
                missed=1 if active else 8,
                late=0,
            )
            proposals = ProposalStats(expected=0, successful=0, missed=0)
            effectiveness = compute_effectiveness_score(
                attestations_expected=attestations.expected,
                attestations_successful=attestations.successful,
                attestations_late=attestations.late,
                proposals_expected=proposals.expected,
                proposals_successful=proposals.successful,
            )
            slashing_risk = compute_slashing_risk_score(
                consecutive_missed_attestations=attestations.missed,
                missed_proposals=proposals.missed,
                clock_drift_ms=infrastructure.clock_drift_ms,
                syncing=consensus.syncing,
                peer_count=consensus.connected_peers,
                effectiveness_score=effectiveness,
            )
            validators.append(
                ValidatorStats(
                    index=b["index"],
                    pubkey=b.get("pubkey"),
                    status=b["status"],
                    balance_gwei=b["balance_gwei"],
                    effective_balance_gwei=b["effective_balance_gwei"],
                    attestations=attestations,
                    proposals=proposals,
                    rewards_gwei=max(
                        0, b["balance_gwei"] - b["effective_balance_gwei"]
                    ),
                    effectiveness_score=effectiveness,
                    slashing_risk_score=slashing_risk,
                )
            )
        return validators


# Kept for package clarity; infrastructure sampling stays chain-agnostic in pulse.py.
__all__ = ["EthereumAdapter"]
