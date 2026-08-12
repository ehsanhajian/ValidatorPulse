from __future__ import annotations

from typing import Any

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.tezos.demo import (
    apply_demo_infrastructure,
    build_demo_tezos_consensus,
    build_demo_validators,
    tezos_effectiveness,
)
from validator_pulse.chains.tezos.rights import RightsWindow
from validator_pulse.chains.tezos.rpc import (
    collect_tezos_consensus,
    fetch_attestation_rights,
    fetch_baking_rights,
    fetch_delegate_info,
    fetch_delegate_participation,
    tezos_get,
    try_fetch_openmetrics,
)
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


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


class TezosAdapter:
    """Tezos baker adapter via Octez protocol RPC."""

    name = "tezos"
    display_name = "Tezos"
    operator_label = "baker"
    risk_kind = "slashing"
    risk_label = "Slashing risk"
    primary_duty_label = "Attestations"
    secondary_duty_label = "Baking rights"
    missed_duty_label = "Missed slots"
    consensus_node_label = "Octez node"

    def __init__(self) -> None:
        self._rights: dict[str, RightsWindow] = {}

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.tezos_rpc_url and settings.tezos_rpc_url.strip())

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
        consensus = build_demo_tezos_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        bakers = settings.tezos_baker_address_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            baker_addresses=bakers,
            remaining_miss_alert=settings.alert_tezos_remaining_misses_below,
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
            rpc_url = (settings.tezos_rpc_url or "").strip()
            bakers = settings.tezos_baker_address_list()
            consensus = await collect_tezos_consensus(rpc_url)

            metrics_url = (settings.tezos_metrics_url or "").strip()
            if metrics_url:
                ok, note = await try_fetch_openmetrics(metrics_url)
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

            if not bakers:
                err = "No TEZOS_BAKER_ADDRESSES configured"
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

            head_hash = ""
            try:
                head = await tezos_get(rpc_url, "chains/main/blocks/head")
                head_hash = str(((head or {}).get("header") or {}).get("hash") or "")
            except Exception:  # noqa: BLE001
                head_hash = ""

            operators = [
                await self._build_live_operator(
                    index,
                    baker,
                    rpc_url,
                    consensus,
                    infrastructure,
                    settings,
                    head_hash=head_hash,
                )
                for index, baker in enumerate(bakers)
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
        baker: str,
        rpc_url: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
        *,
        head_hash: str,
    ) -> ValidatorStats:
        window = self._rights.setdefault(baker, RightsWindow())
        if head_hash:
            window.observe_head(consensus.head_slot, head_hash)

        try:
            info = await fetch_delegate_info(rpc_url, baker)
            participation = await fetch_delegate_participation(rpc_url, baker)
            baking = await fetch_baking_rights(rpc_url, baker)
            attesting = await fetch_attestation_rights(rpc_url, baker)
        except Exception as exc:  # noqa: BLE001
            return self._failed_operator(index, baker, consensus, infrastructure, str(exc))

        window.ingest_baking_rights(baking, head_level=consensus.head_slot)
        window.ingest_attestation_rights(attesting, head_level=consensus.head_slot)

        expected_activity = _as_int(participation.get("expected_cycle_activity"))
        missed_slots = _as_int(participation.get("missed_slots"))
        missed_levels = _as_int(participation.get("missed_levels"))
        remaining = _as_int(participation.get("remaining_allowed_missed_slots"))

        window.apply_participation(
            missed_slots=missed_slots,
            missed_levels=missed_levels,
            expected_activity=expected_activity,
        )
        window._close_past_rights(consensus.head_slot)
        totals = window.totals()

        attest_expected = expected_activity or totals["attest_expected"] or 0
        attest_ok = max(0, attest_expected - missed_slots) if attest_expected else totals["attest_success"]
        bake_expected = totals["bake_expected"]
        bake_ok = totals["bake_success"]
        if bake_expected == 0 and missed_levels:
            bake_expected = missed_levels + bake_ok

        effectiveness = tezos_effectiveness(
            attest_expected=attest_expected,
            attest_ok=attest_ok,
            bake_expected=bake_expected,
            bake_ok=bake_ok,
        )

        forbidden = _as_bool(info.get("deactivated")) or _as_bool(info.get("forbidden"))
        pending_denunciations = _as_bool(info.get("pending_denunciations"))
        events: list[ProtocolEvent] = []
        status = "active"

        if forbidden:
            status = "forbidden"
            events.append(
                ProtocolEvent(
                    kind="slashed",
                    severity="critical",
                    message="Delegate is forbidden/deactivated (double-signing or penalty).",
                    confirmed=True,
                )
            )
            events.append(
                ProtocolEvent(
                    kind="suspended",
                    severity="critical",
                    message="Baker forbidden — cannot participate in consensus.",
                    confirmed=True,
                )
            )
        elif pending_denunciations:
            status = "denounced"
            events.append(
                ProtocolEvent(
                    kind="slashed",
                    severity="critical",
                    message="Pending denunciation evidence against delegate.",
                    confirmed=True,
                )
            )

        if remaining <= settings.alert_tezos_remaining_misses_below and not forbidden:
            status = "near_reward_loss" if status == "active" else status
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning" if remaining > 0 else "critical",
                    message=(
                        f"Only {remaining} allowed attestation misses remain "
                        f"before reward loss (threshold "
                        f"{settings.alert_tezos_remaining_misses_below})."
                    ),
                    confirmed=True,
                )
            )

        if missed_slots > 0 and status == "active":
            status = "degraded"

        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed_slots, 40),
            missed_secondary_duties=min(missed_levels, 20),
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if forbidden or pending_denunciations:
            risk = 100.0
        elif remaining <= settings.alert_tezos_remaining_misses_below:
            risk = max(risk, 70.0)

        stake = _as_int(info.get("full_balance") or info.get("balance"))
        rewards = _as_int(info.get("estimated_shared_pending_payout") or 0)

        return ValidatorStats(
            index=index,
            operator_id=baker,
            operator_index=index,
            pubkey=baker,
            status=status,
            balance_base_units=stake + max(rewards, 0),
            effective_balance_base_units=stake,
            attestations=AttestationStats(
                expected=attest_expected,
                successful=attest_ok,
                missed=max(0, attest_expected - attest_ok),
                late=0,
            ),
            proposals=ProposalStats(
                expected=bake_expected,
                successful=bake_ok,
                missed=max(0, bake_expected - bake_ok),
            ),
            duties=[
                DutyStats(
                    category="attestation",
                    label="Attestations",
                    expected=attest_expected,
                    successful=attest_ok,
                    missed=max(0, attest_expected - attest_ok),
                    late=0,
                    weight=0.65,
                ),
                DutyStats(
                    category="block",
                    label="Baking rights",
                    expected=bake_expected,
                    successful=bake_ok,
                    missed=max(0, bake_expected - bake_ok),
                    weight=0.35,
                ),
            ],
            rewards_base_units=rewards,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="slashing",
            protocol_events=events,
        )

    def _failed_operator(
        self,
        index: int,
        baker: str,
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
            operator_id=baker,
            operator_index=index,
            pubkey=baker,
            status="unreachable",
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(expected=0, successful=0, missed=0),
            duties=[
                DutyStats(
                    category="attestation",
                    label="Attestations",
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
            risk_kind="slashing",
            protocol_events=[
                ProtocolEvent(
                    kind="rpc_error",
                    severity="warning",
                    message=error,
                    confirmed=False,
                )
            ],
        )
