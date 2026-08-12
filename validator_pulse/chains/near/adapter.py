from __future__ import annotations

from typing import Any

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.near.demo import (
    apply_demo_infrastructure,
    build_demo_near_consensus,
    build_demo_validators,
    near_effectiveness,
)
from validator_pulse.chains.near.epoch import EpochSnapshotStore
from validator_pulse.chains.near.rpc import (
    collect_near_consensus,
    fetch_validators,
    format_kickout_reason,
    index_validators,
    try_fetch_near_metrics,
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
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        return int(text.split(".")[0]) if "." in text else int(text)
    return int(value)


def _as_stake(value: Any) -> int:
    """NEAR stake may be string yocto or object with amount."""
    if value is None:
        return 0
    if isinstance(value, dict):
        return _as_stake(value.get("amount") or value.get("stake") or 0)
    return _as_int(value)


class NearAdapter:
    """NEAR validator adapter via JSON-RPC status + validators."""

    name = "near"
    display_name = "NEAR"
    operator_label = "validator"
    risk_kind = "kickout"
    risk_label = "Kickout risk"
    primary_duty_label = "Blocks"
    secondary_duty_label = "Chunks"
    missed_duty_label = "Missed production"
    consensus_node_label = "NEAR RPC"

    def __init__(self) -> None:
        self._epochs = EpochSnapshotStore()

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.near_rpc_url and settings.near_rpc_url.strip())

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
        consensus = build_demo_near_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        accounts = settings.near_validator_account_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            account_ids=accounts,
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
            rpc_url = (settings.near_rpc_url or "").strip()
            consensus = await collect_near_consensus(rpc_url)

            metrics_url = (settings.near_metrics_url or "").strip()
            if metrics_url:
                ok, note = await try_fetch_near_metrics(metrics_url)
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

            accounts = settings.near_validator_account_list()
            if not accounts:
                err = "No NEAR_VALIDATOR_ACCOUNT_IDS configured"
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

            try:
                payload = await fetch_validators(rpc_url)
                indexed = index_validators(payload)
            except Exception as exc:  # noqa: BLE001
                consensus = consensus.model_copy(
                    update={
                        "status": "critical",
                        "last_error": (
                            f"{consensus.last_error}; validators: {exc}"
                            if consensus.last_error
                            else f"validators: {exc}"
                        ),
                    }
                )
                return ChainCollection(
                    consensus=consensus,
                    operators=[
                        self._failed_operator(
                            i, account, consensus, infrastructure, str(exc)
                        )
                        for i, account in enumerate(accounts)
                    ],
                    infrastructure=infrastructure,
                )

            operators = [
                self._build_live_operator(
                    index,
                    account_id,
                    indexed,
                    consensus,
                    infrastructure,
                )
                for index, account_id in enumerate(accounts)
            ]
            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    def _build_live_operator(
        self,
        index: int,
        account_id: str,
        indexed: dict[str, Any],
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
    ) -> ValidatorStats:
        current = indexed["current"].get(account_id)
        next_info = indexed["next"].get(account_id)
        proposal = indexed["proposals"].get(account_id)
        kickout = indexed["kickouts"].get(account_id)
        epoch_height = int(indexed.get("epoch_height") or 0)
        rewards_map = indexed.get("rewards") or {}
        rewards = _as_stake(rewards_map.get(account_id) if isinstance(rewards_map, dict) else 0)

        events: list[ProtocolEvent] = []
        is_slashed = bool(current.get("is_slashed")) if current else False

        if current:
            eb = _as_int(current.get("num_expected_blocks"))
            pb = _as_int(current.get("num_produced_blocks"))
            ec = _as_int(current.get("num_expected_chunks"))
            pc = _as_int(current.get("num_produced_chunks"))
            ee = _as_int(current.get("num_expected_endorsements"))
            pe = _as_int(current.get("num_produced_endorsements"))
            stake = _as_stake(current.get("stake"))
            status = "active"
        elif next_info or proposal:
            eb = pb = ec = pc = ee = pe = 0
            stake = _as_stake((next_info or proposal or {}).get("stake"))
            status = "set_transition"
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message="Account is in next-epoch set or proposals, not current validators.",
                    confirmed=True,
                )
            )
        else:
            eb = pb = ec = pc = ee = pe = 0
            stake = 0
            status = "inactive"

        counters, epoch_reset = self._epochs.observe(
            account_id,
            epoch_height=epoch_height,
            expected_blocks=eb,
            produced_blocks=pb,
            expected_chunks=ec,
            produced_chunks=pc,
            expected_endorsements=ee,
            produced_endorsements=pe,
        )
        del epoch_reset  # used for safe reset; counters already absolute

        eb, pb = counters.expected_blocks, counters.produced_blocks
        ec, pc = counters.expected_chunks, counters.produced_chunks
        ee, pe = counters.expected_endorsements, counters.produced_endorsements

        if is_slashed:
            status = "slashed"
            events.append(
                ProtocolEvent(
                    kind="slashed",
                    severity="critical",
                    message="Validator is_slashed=true (distinct from downtime kickout).",
                    confirmed=True,
                )
            )

        if kickout and not is_slashed:
            reason = format_kickout_reason(kickout.get("reason"))
            events.append(
                ProtocolEvent(
                    kind="kicked",
                    severity="critical",
                    message=f"Prev-epoch kickout: {reason}",
                    confirmed=True,
                )
            )
            if status == "inactive":
                status = "kicked"

        # Current but missing from next → set transition / seat loss risk.
        if current and not next_info and not is_slashed:
            status = "set_transition" if status == "active" else status
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="In current set but absent from next_validators (seat transition).",
                    confirmed=True,
                )
            )

        missed_blocks = max(0, eb - pb)
        missed_chunks = max(0, ec - pc)
        missed_endorsements = max(0, ee - pe)
        effectiveness = near_effectiveness(
            expected_blocks=eb,
            produced_blocks=pb,
            expected_chunks=ec,
            produced_chunks=pc,
            expected_endorsements=ee,
            produced_endorsements=pe,
        )

        # Near-kickout heuristic: completion below ~90% with meaningful expected work.
        completion = effectiveness
        if current and not is_slashed and (eb + ec) > 0 and completion < 90.0:
            if status == "active":
                status = "near_kickout"
            events.append(
                ProtocolEvent(
                    kind="kicked",
                    severity="warning",
                    message=(
                        f"Elevated kickout risk (effectiveness {completion}%). "
                        "Incomplete blocks/chunks/endorsements."
                    ),
                    confirmed=False,
                )
            )

        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed_blocks + missed_chunks // 10, 40),
            missed_secondary_duties=min(missed_endorsements // 50, 20),
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if is_slashed:
            risk = 100.0
        elif any(e.kind == "kicked" and e.confirmed for e in events):
            risk = max(risk, 85.0)
        elif status == "near_kickout":
            risk = max(risk, 70.0)

        return ValidatorStats(
            index=index,
            operator_id=account_id,
            operator_index=index,
            pubkey=str((current or next_info or proposal or {}).get("public_key") or account_id),
            status=status,
            balance_base_units=stake + rewards,
            effective_balance_base_units=stake,
            attestations=AttestationStats(
                expected=ec + ee,
                successful=pc + pe,
                missed=missed_chunks + missed_endorsements,
                late=0,
            ),
            proposals=ProposalStats(
                expected=eb,
                successful=pb,
                missed=missed_blocks,
            ),
            duties=[
                DutyStats(
                    category="block",
                    label="Blocks",
                    expected=eb,
                    successful=pb,
                    missed=missed_blocks,
                    late=0,
                    weight=0.5,
                ),
                DutyStats(
                    category="chunk",
                    label="Chunks",
                    expected=ec,
                    successful=pc,
                    missed=missed_chunks,
                    late=0,
                    weight=0.3,
                ),
                DutyStats(
                    category="endorsement",
                    label="Endorsements",
                    expected=ee,
                    successful=pe,
                    missed=missed_endorsements,
                    late=0,
                    weight=0.2,
                ),
            ],
            rewards_base_units=rewards,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="kickout",
            protocol_events=events,
        )

    def _failed_operator(
        self,
        index: int,
        account_id: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        error: str,
    ) -> ValidatorStats:
        effectiveness = 0.0
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=40,
            missed_secondary_duties=0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=True,
            peer_count=0,
            effectiveness_score=effectiveness,
        )
        return ValidatorStats(
            index=index,
            operator_id=account_id,
            operator_index=index,
            pubkey=account_id,
            status="unreachable",
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(expected=1, successful=0, missed=1, late=0),
            proposals=ProposalStats(expected=0, successful=0, missed=0),
            duties=[
                DutyStats(
                    category="block",
                    label="Blocks",
                    expected=1,
                    successful=0,
                    missed=1,
                    late=0,
                    weight=1.0,
                )
            ],
            rewards_base_units=0,
            effectiveness_score=effectiveness,
            risk_score=max(risk, 80.0),
            risk_kind="kickout",
            protocol_events=[
                ProtocolEvent(
                    kind="rpc_error",
                    severity="warning",
                    message=error,
                    confirmed=False,
                )
            ],
        )
