from __future__ import annotations

from typing import Any

from validator_pulse.chains.aptos.demo import (
    apply_demo_infrastructure,
    build_demo_aptos_consensus,
    build_demo_validators,
)
from validator_pulse.chains.aptos.epoch import EpochProposalStore
from validator_pulse.chains.aptos.rpc import (
    collect_aptos_consensus,
    fetch_proposal_counts,
    fetch_stake,
    fetch_validator_index,
    fetch_validator_set,
    fetch_validator_state,
    try_fetch_inspection_metrics,
)
from validator_pulse.chains.aptos.scoring import (
    aptos_effectiveness,
    in_current_set,
    index_active_validators,
    parse_stake_tuple,
    parse_view_u64,
    parse_view_u64_pair,
    status_from_code,
)
from validator_pulse.chains.base import ChainCollection
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
from validator_pulse.scoring import compute_slashing_risk_score


def _normalize_pool(address: str) -> str:
    text = address.strip()
    if text and not text.startswith("0x"):
        text = f"0x{text}"
    return text


class AptosAdapter:
    """Aptos validator adapter via fullnode REST + Move view functions."""

    name = "aptos"
    display_name = "Aptos"
    operator_label = "validator"
    risk_kind = "reward_loss"
    risk_label = "Reward risk"
    primary_duty_label = "Proposals"
    secondary_duty_label = "Set membership"
    missed_duty_label = "Failed proposals"
    consensus_node_label = "Aptos fullnode"

    def __init__(self) -> None:
        self._epochs = EpochProposalStore()

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.aptos_rest_url and settings.aptos_rest_url.strip())

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
        consensus = build_demo_aptos_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        pools = settings.aptos_pool_address_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            pool_addresses=pools,
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
            rest_url = (settings.aptos_rest_url or "").strip()
            api_key = (settings.aptos_api_key or "").strip() or None
            pools = [_normalize_pool(p) for p in settings.aptos_pool_address_list()]
            consensus = await collect_aptos_consensus(rest_url, api_key=api_key)

            metrics_url = (settings.aptos_metrics_url or "").strip()
            if metrics_url:
                ok, note = await try_fetch_inspection_metrics(metrics_url)
                if not ok and note:
                    consensus = consensus.model_copy(
                        update={
                            "last_error": (
                                f"{consensus.last_error}; {note}"
                                if consensus.last_error
                                else note
                            )
                        }
                    )

            if not pools:
                err = "No APTOS_POOL_ADDRESSES configured"
                consensus = consensus.model_copy(
                    update={
                        "status": (
                            "degraded"
                            if consensus.status == "healthy"
                            else consensus.status
                        ),
                        "last_error": (
                            f"{consensus.last_error}; {err}"
                            if consensus.last_error
                            else err
                        ),
                    }
                )
                return ChainCollection(
                    consensus=consensus,
                    operators=[],
                    infrastructure=infrastructure,
                )

            index_map: dict[str, int] = {}
            try:
                vset = await fetch_validator_set(rest_url, api_key=api_key)
                index_map = index_active_validators(vset)
            except Exception as exc:  # noqa: BLE001
                note = f"ValidatorSet resource unavailable: {exc}"
                consensus = consensus.model_copy(
                    update={
                        "status": "degraded",
                        "last_error": (
                            f"{consensus.last_error}; {note}"
                            if consensus.last_error
                            else note
                        ),
                    }
                )

            operators = [
                await self._build_live_operator(
                    index,
                    pool,
                    rest_url,
                    api_key,
                    consensus,
                    infrastructure,
                    settings,
                    index_map=index_map,
                )
                for index, pool in enumerate(pools)
            ]
            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    async def _build_live_operator(
        self,
        index: int,
        pool: str,
        rest_url: str,
        api_key: str | None,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
        *,
        index_map: dict[str, int],
    ) -> ValidatorStats:
        try:
            state_raw = await fetch_validator_state(rest_url, pool, api_key=api_key)
            state_code = parse_view_u64(state_raw)
            status = status_from_code(state_code)
            in_set = in_current_set(status)

            validator_index: int | None = index_map.get(pool.lower())
            if validator_index is None and in_set:
                try:
                    validator_index = parse_view_u64(
                        await fetch_validator_index(rest_url, pool, api_key=api_key)
                    )
                except Exception:  # noqa: BLE001
                    validator_index = None

            successful = 0
            failed = 0
            if validator_index is not None and in_set:
                try:
                    successful, failed = parse_view_u64_pair(
                        await fetch_proposal_counts(
                            rest_url, validator_index, api_key=api_key
                        )
                    )
                except Exception:  # noqa: BLE001
                    successful, failed = 0, 0

            counters, _reset = self._epochs.observe(
                pool,
                epoch=consensus.finalized_epoch,
                successful=successful,
                failed=failed,
                validator_index=validator_index,
            )
            successful = counters.successful
            failed = counters.failed

            active, _inactive, _pend_a, pend_i = parse_stake_tuple(
                await fetch_stake(rest_url, pool, api_key=api_key)
            )
            # Current-epoch voting power ≈ active + pending_inactive for set members.
            stake = active + pend_i if in_set else active
        except Exception as exc:  # noqa: BLE001
            return self._failed_operator(
                index, pool, consensus, infrastructure, str(exc)
            )

        expected = successful + failed
        effectiveness = aptos_effectiveness(
            successful=successful,
            failed=failed,
            in_set=in_set,
            syncing=consensus.syncing,
        )

        events: list[ProtocolEvent] = []
        op_status = status
        if not in_set:
            op_status = "inactive"
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message="Pool is inactive — not in the current validator set.",
                    confirmed=True,
                )
            )
        elif failed >= settings.alert_aptos_failed_proposals:
            op_status = "degraded"
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Failed proposals {failed} ≥ "
                        f"{settings.alert_aptos_failed_proposals} this epoch "
                        f"— reward risk (no principal slashing on Aptos)."
                    ),
                    confirmed=True,
                )
            )
        elif status == "pending_inactive":
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="Pool is pending inactive (leaving set next epoch).",
                    confirmed=True,
                )
            )

        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(failed, 40),
            missed_secondary_duties=0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if not in_set:
            risk = 100.0
        elif failed >= settings.alert_aptos_failed_proposals:
            risk = max(risk, 55.0)

        return ValidatorStats(
            index=index,
            operator_id=pool,
            operator_index=validator_index if validator_index is not None else index,
            pubkey=pool,
            status=op_status,
            balance_base_units=stake,
            effective_balance_base_units=stake,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(
                expected=expected,
                successful=successful,
                missed=failed,
            ),
            duties=[
                DutyStats(
                    category="block",
                    label="Proposals",
                    expected=expected if in_set else None,
                    successful=successful,
                    missed=failed,
                    late=0,
                    weight=0.85,
                ),
                DutyStats(
                    category="other",
                    label="Set membership",
                    expected=1 if in_set else 0,
                    successful=1 if in_set else 0,
                    missed=0 if in_set else 1,
                    late=0,
                    weight=0.15,
                ),
            ],
            rewards_base_units=0,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="reward_loss",
            protocol_events=events,
        )

    def _failed_operator(
        self,
        index: int,
        pool: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        error: str,
    ) -> ValidatorStats:
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=10,
            missed_secondary_duties=0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=True,
            peer_count=0,
            effectiveness_score=0.0,
        )
        return ValidatorStats(
            index=index,
            operator_id=pool,
            operator_index=index,
            pubkey=pool,
            status="unreachable",
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(expected=0, successful=0, missed=0),
            duties=[
                DutyStats(
                    category="block",
                    label="Proposals",
                    expected=None,
                    successful=0,
                    missed=0,
                    late=0,
                    weight=1.0,
                )
            ],
            rewards_base_units=0,
            effectiveness_score=0.0,
            risk_score=max(risk, 60.0),
            risk_kind="reward_loss",
            protocol_events=[
                ProtocolEvent(
                    kind="rpc_error",
                    severity="warning",
                    message=error,
                    confirmed=False,
                )
            ],
        )
