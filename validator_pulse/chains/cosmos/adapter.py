from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.cosmos.demo import (
    apply_demo_infrastructure,
    build_demo_comet_consensus,
    build_demo_validators,
)
from validator_pulse.chains.cosmos.lcd import (
    consensus_address_from_pubkey,
    fetch_signing_info,
    fetch_slashing_params,
    fetch_validator,
    operator_to_consensus_address,
)
from validator_pulse.chains.cosmos.profiles import CosmosProfile, get_profile
from validator_pulse.chains.cosmos.rpc import collect_comet_consensus
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
    ProtocolEvent,
    ValidatorStats,
)
from validator_pulse.scoring import (
    compute_effectiveness_score,
    compute_slashing_risk_score,
)


class CosmosAdapter:
    """Cosmos SDK / CometBFT validator adapter with Bech32 chain profiles."""

    name = "cosmos"
    display_name = "Cosmos Hub"
    operator_label = "validator"
    risk_kind = "slashing"
    risk_label = "Slashing risk"
    primary_duty_label = "Signed blocks"
    secondary_duty_label = "Voting power"
    missed_duty_label = "Missed blocks"
    consensus_node_label = "CometBFT node"
    profile_name = "cosmoshub"

    def configure(self, settings: Settings) -> None:
        profile = get_profile(settings.cosmos_profile)
        self.profile_name = profile.name
        self.display_name = profile.display_name

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        has_rest = bool(settings.cosmos_rest_url and settings.cosmos_rest_url.strip())
        has_rpc = bool(settings.cosmos_rpc_url and settings.cosmos_rpc_url.strip())
        return not (has_rest or has_rpc)

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
        profile = get_profile(settings.cosmos_profile)
        consensus = build_demo_comet_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        addresses = settings.cosmos_validator_address_list() or None
        operators = build_demo_validators(
            profile,
            consensus,
            infrastructure,
            operator_addresses=addresses,
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
        token = bind_rpc_http_config(RpcHttpConfig.from_settings(settings))
        try:
            profile = get_profile(settings.cosmos_profile)
            rpc_url = (settings.cosmos_rpc_url or "").strip() or None
            rest_url = (settings.cosmos_rest_url or "").strip() or None

            if rpc_url:
                consensus = await collect_comet_consensus(rpc_url)
            else:
                consensus = ConsensusHealth(
                    beacon_reachable=False,
                    syncing=True,
                    sync_distance=-1,
                    head_slot=0,
                    finalized_epoch=0,
                    justified_epoch=0,
                    peer_count=0,
                    connected_peers=0,
                    status="critical",
                    last_error="No COSMOS_RPC_URL configured",
                )

            addresses = settings.cosmos_validator_address_list()
            if not addresses:
                err = "No COSMOS_VALIDATOR_OPERATOR_ADDRESSES configured"
                if consensus.last_error:
                    err = f"{err}; {consensus.last_error}"
                consensus = consensus.model_copy(
                    update={
                        "status": (
                            "degraded"
                            if consensus.status == "healthy"
                            else consensus.status
                        ),
                        "last_error": err,
                    }
                )
                return ChainCollection(
                    consensus=consensus,
                    operators=[],
                    infrastructure=infrastructure,
                )

            window = 10_000
            min_signed = 0.05
            if rest_url:
                try:
                    params = await fetch_slashing_params(rest_url)
                    window = int(params.get("signed_blocks_window") or window)
                    raw_min = params.get("min_signed_per_window")
                    if raw_min is not None:
                        min_signed = float(raw_min)
                except Exception as exc:  # noqa: BLE001
                    note = f"slashing params unavailable: {exc}"
                    consensus = consensus.model_copy(
                        update={
                            "last_error": (
                                f"{consensus.last_error}; {note}"
                                if consensus.last_error
                                else note
                            )
                        }
                    )

            operators: list[ValidatorStats] = []
            for index, valoper in enumerate(addresses):
                if rest_url:
                    op = await self._live_validator_from_lcd(
                        index,
                        valoper,
                        rest_url,
                        profile,
                        consensus,
                        infrastructure,
                        signed_blocks_window=window,
                        min_signed_per_window=min_signed,
                    )
                else:
                    op = self._live_validator_from_node_health(
                        index,
                        valoper,
                        profile,
                        consensus,
                        infrastructure,
                        signed_blocks_window=window,
                    )
                operators.append(op)

            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    async def _live_validator_from_lcd(
        self,
        index: int,
        valoper: str,
        rest_url: str,
        profile: CosmosProfile,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        *,
        signed_blocks_window: int,
        min_signed_per_window: float,
    ) -> ValidatorStats:
        try:
            validator = await fetch_validator(rest_url, valoper)
        except Exception as exc:  # noqa: BLE001
            return self._failed_operator(
                index, valoper, consensus, infrastructure, str(exc)
            )

        jailed = bool(validator.get("jailed"))
        status = str(validator.get("status") or "unknown")
        tokens = int(validator.get("tokens") or 0)
        pubkey = ((validator.get("consensus_pubkey") or {}).get("key")) or None
        cons_addr = None
        if pubkey:
            try:
                cons_addr = consensus_address_from_pubkey(pubkey, profile)
            except Exception:  # noqa: BLE001
                cons_addr = None
        if cons_addr is None:
            try:
                cons_addr = operator_to_consensus_address(valoper, profile)
            except Exception:  # noqa: BLE001
                cons_addr = None

        missed = 0
        tombstoned = False
        if cons_addr:
            try:
                signing = await fetch_signing_info(rest_url, cons_addr)
                missed = int(signing.get("missed_blocks_counter") or 0)
                tombstoned = bool(signing.get("tombstoned"))
            except Exception:  # noqa: BLE001
                pass

        return self._build_operator_stats(
            index=index,
            valoper=valoper,
            cons_addr=cons_addr,
            status=status,
            jailed=jailed,
            tombstoned=tombstoned,
            missed=missed,
            tokens=tokens,
            signed_blocks_window=signed_blocks_window,
            min_signed_per_window=min_signed_per_window,
            consensus=consensus,
            infrastructure=infrastructure,
        )

    def _live_validator_from_node_health(
        self,
        index: int,
        valoper: str,
        profile: CosmosProfile,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        *,
        signed_blocks_window: int,
    ) -> ValidatorStats:
        """Fallback when only CometBFT RPC is configured (Polkadot-style heuristic)."""
        del profile
        window = signed_blocks_window
        if not consensus.beacon_reachable:
            missed, status = window, "unreachable"
        elif consensus.syncing:
            missed, status = int(window * 0.2), "syncing"
        elif consensus.connected_peers < 3:
            missed, status = int(window * 0.08), "low_peers"
        else:
            missed, status = int(window * 0.01), "BOND_STATUS_BONDED"
        return self._build_operator_stats(
            index=index,
            valoper=valoper,
            cons_addr=None,
            status=status,
            jailed=False,
            tombstoned=False,
            missed=missed,
            tokens=0,
            signed_blocks_window=window,
            min_signed_per_window=0.05,
            consensus=consensus,
            infrastructure=infrastructure,
        )

    def _failed_operator(
        self,
        index: int,
        valoper: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        error: str,
    ) -> ValidatorStats:
        return self._build_operator_stats(
            index=index,
            valoper=valoper,
            cons_addr=None,
            status=f"error:{error[:80]}",
            jailed=False,
            tombstoned=False,
            missed=0,
            tokens=0,
            signed_blocks_window=10_000,
            min_signed_per_window=0.05,
            consensus=consensus,
            infrastructure=infrastructure,
        )

    def _build_operator_stats(
        self,
        *,
        index: int,
        valoper: str,
        cons_addr: str | None,
        status: str,
        jailed: bool,
        tombstoned: bool,
        missed: int,
        tokens: int,
        signed_blocks_window: int,
        min_signed_per_window: float,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
    ) -> ValidatorStats:
        window = max(1, signed_blocks_window)
        missed = max(0, min(missed, window))
        successful = max(0, window - missed)
        effectiveness = compute_effectiveness_score(
            attestations_expected=window,
            attestations_successful=successful,
            attestations_late=0,
            proposals_expected=0,
            proposals_successful=0,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed, 40),
            missed_secondary_duties=1 if jailed or tombstoned else 0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=consensus.connected_peers,
            effectiveness_score=effectiveness,
        )
        # Escalate when approaching downtime slash threshold.
        downtime_ratio = missed / window
        threshold = max(0.0, 1.0 - float(min_signed_per_window or 0.05))
        if downtime_ratio >= threshold * 0.8 and not (jailed or tombstoned):
            risk = max(risk, 50.0 + 40.0 * min(1.0, downtime_ratio / max(threshold, 1e-6)))

        events: list[ProtocolEvent] = []
        display_status = status
        if tombstoned:
            risk = 100.0
            display_status = "tombstoned"
            events.append(
                ProtocolEvent(
                    kind="tombstoned",
                    severity="critical",
                    message="Validator is tombstoned (double-sign evidence).",
                    confirmed=True,
                )
            )
        elif jailed:
            risk = max(risk, 85.0)
            display_status = "jailed"
            events.append(
                ProtocolEvent(
                    kind="jailed",
                    severity="critical",
                    message="Validator is jailed.",
                    confirmed=True,
                )
            )

        return ValidatorStats(
            index=index,
            operator_id=valoper,
            operator_index=index,
            pubkey=cons_addr,
            status=display_status,
            balance_base_units=tokens,
            effective_balance_base_units=tokens,
            attestations=AttestationStats(
                expected=window,
                successful=successful,
                missed=missed,
                late=0,
            ),
            proposals=ProposalStats(expected=0, successful=0, missed=0),
            duties=[
                DutyStats(
                    category="block",
                    label="Signed blocks",
                    expected=window,
                    successful=successful,
                    missed=missed,
                    weight=1.0,
                )
            ],
            rewards_base_units=0,
            effectiveness_score=effectiveness,
            risk_score=round(risk, 1),
            risk_kind="slashing",
            protocol_events=events,
        )


__all__ = ["CosmosAdapter"]
