from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.monad.abi import EXPECTED_CHAIN_ID, ValidatorView
from validator_pulse.chains.monad.demo import (
    apply_demo_infrastructure,
    build_demo_monad_consensus,
    build_demo_validators,
)
from validator_pulse.chains.monad.local import load_ledger_tail, load_status_json
from validator_pulse.chains.monad.rpc import (
    collect_monad_consensus,
    fetch_consensus_validator_set,
    fetch_epoch,
    fetch_proposer_val_id,
    fetch_validator,
    try_fetch_monad_metrics,
)
from validator_pulse.chains.monad.state import (
    EpochSetStore,
    ProposalDeltaStore,
    monad_effectiveness,
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


class MonadAdapter:
    """Monad validator adapter via EVM RPC staking precompile 0x1000.

    Exact missed-duty claims require local ledger-tail / metrics evidence.
    Automated slashing is not implemented on Monad — risk is reward/eligibility.
    """

    name = "monad"
    display_name = "Monad"
    operator_label = "validator"
    risk_kind = "reward_loss"
    risk_label = "Reward risk"
    primary_duty_label = "Proposals"
    secondary_duty_label = "Set membership"
    missed_duty_label = "Missed proposals"
    consensus_node_label = "Monad RPC"

    def __init__(self) -> None:
        self._sets = EpochSetStore()
        self._proposals = ProposalDeltaStore()

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.monad_rpc_url and settings.monad_rpc_url.strip())

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
        consensus = build_demo_monad_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        ids = settings.monad_validator_id_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            validator_ids=ids,
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
            rpc_url = (settings.monad_rpc_url or "").strip()
            val_ids = settings.monad_validator_id_list()
            consensus = await collect_monad_consensus(
                rpc_url, expected_chain_id=EXPECTED_CHAIN_ID
            )

            metrics_url = (settings.monad_metrics_url or "").strip()
            node_metrics = None
            if metrics_url:
                node_metrics, note = await try_fetch_monad_metrics(metrics_url)
                if note:
                    consensus = consensus.model_copy(
                        update={
                            "last_error": (
                                f"{consensus.last_error}; {note}"
                                if consensus.last_error
                                else note
                            )
                        }
                    )
                if node_metrics and node_metrics.connected_peers is not None:
                    consensus = consensus.model_copy(
                        update={
                            "peer_count": node_metrics.connected_peers,
                            "connected_peers": node_metrics.connected_peers,
                        }
                    )

            status_info = load_status_json(settings.monad_status_path)
            if status_info:
                peers = int(status_info.get("peer_count") or 0)
                if peers:
                    consensus = consensus.model_copy(
                        update={"peer_count": peers, "connected_peers": peers}
                    )
                if not status_info.get("in_sync", True):
                    consensus = consensus.model_copy(
                        update={
                            "syncing": True,
                            "sync_distance": max(
                                1, int(status_info.get("block_difference") or 1)
                            ),
                            "status": (
                                "degraded"
                                if consensus.status == "healthy"
                                else consensus.status
                            ),
                        }
                    )

            if not val_ids:
                err = "No MONAD_VALIDATOR_IDS configured"
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

            consensus_ids: list[int] = []
            epoch = consensus.finalized_epoch
            proposer: int | None = None
            if consensus.beacon_reachable:
                try:
                    epoch_info = await fetch_epoch(rpc_url)
                    epoch = epoch_info.epoch
                    consensus = consensus.model_copy(
                        update={
                            "finalized_epoch": epoch,
                            "justified_epoch": epoch,
                        }
                    )
                    if epoch_info.in_epoch_delay_period:
                        consensus = consensus.model_copy(
                            update={
                                "last_error": (
                                    f"{consensus.last_error}; epoch delay period"
                                    if consensus.last_error
                                    else "Epoch delay / boundary period"
                                )
                            }
                        )
                    consensus_ids = await fetch_consensus_validator_set(rpc_url)
                    try:
                        proposer = await fetch_proposer_val_id(rpc_url)
                    except Exception:  # noqa: BLE001
                        proposer = None
                except Exception as exc:  # noqa: BLE001
                    consensus = consensus.model_copy(
                        update={
                            "status": "degraded",
                            "last_error": (
                                f"{consensus.last_error}; staking precompile: {exc}"
                                if consensus.last_error
                                else f"staking precompile: {exc}"
                            ),
                        }
                    )

            joined, left, _reset = self._sets.observe(epoch, consensus_ids)
            consensus_set = set(consensus_ids)

            operators = [
                await self._build_operator(
                    index,
                    vid,
                    rpc_url,
                    consensus,
                    infrastructure,
                    settings,
                    in_consensus=vid in consensus_set,
                    joined=vid in joined,
                    left=vid in left,
                    proposer=proposer,
                    node_metrics=node_metrics,
                    status_info=status_info,
                )
                for index, vid in enumerate(val_ids)
            ]
            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    async def _build_operator(
        self,
        index: int,
        vid: int,
        rpc_url: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
        *,
        in_consensus: bool,
        joined: bool,
        left: bool,
        proposer: int | None,
        node_metrics,
        status_info: dict | None,
    ) -> ValidatorStats:
        view: ValidatorView | None = None
        try:
            view = await fetch_validator(rpc_url, vid)
        except Exception as exc:  # noqa: BLE001
            return self._failed_operator(index, vid, consensus, infrastructure, str(exc))

        secp = view.secp_pubkey_hex
        evidence = load_ledger_tail(settings.monad_ledger_tail_path, secp_pubkey_hex=secp)
        metrics_authored = (
            node_metrics.proposed_blocks
            if node_metrics and node_metrics.proposed_blocks is not None
            else None
        )
        local_evidence = bool(
            (evidence and evidence.has_duty_history) or metrics_authored is not None
        )

        authored = 0
        missed = 0
        if evidence and evidence.has_duty_history:
            deltas = self._proposals.observe(
                vid, authored=evidence.authored, missed=evidence.missed
            )
            authored = evidence.authored
            missed = evidence.missed
            del deltas
        elif metrics_authored is not None:
            deltas = self._proposals.observe(vid, authored=metrics_authored, missed=0)
            authored = metrics_authored
            missed = 0
            del deltas

        lagging = bool(consensus.syncing)
        if status_info and not status_info.get("in_sync", True):
            lagging = True
        if status_info and not status_info.get("services_ok", True):
            lagging = True

        events: list[ProtocolEvent] = []
        op_status = "active" if in_consensus else "inactive"

        if left:
            op_status = "left_set"
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Validator {vid} left the consensus leader set this epoch "
                        "(set transition)."
                    ),
                    confirmed=True,
                )
            )
        elif joined:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=f"Validator {vid} joined the consensus leader set this epoch.",
                    confirmed=True,
                )
            )
        elif not in_consensus:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Validator {vid} is not in the current consensus leader set "
                        "(pending set transition or ineligible)."
                    ),
                    confirmed=True,
                )
            )

        if not view.eligible:
            op_status = "ineligible" if op_status == "active" else op_status
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message=(
                        f"Validator {vid} stake/flags indicate ineligibility "
                        "(reward opportunity loss — automated slashing is not implemented)."
                    ),
                    confirmed=True,
                )
            )

        if lagging and in_consensus:
            op_status = "lagging" if op_status == "active" else op_status
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="Consensus lag or local service/sync degraded.",
                    confirmed=True,
                )
            )

        if local_evidence and missed > 0:
            op_status = "degraded" if op_status == "active" else op_status
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Local ledger/metrics evidence: {missed} missed / "
                        f"{authored} authored proposals — reward risk, not slashing."
                    ),
                    confirmed=True,
                )
            )
        elif not local_evidence:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        "Exact duty history unavailable without local ledger-tail "
                        "or Prometheus evidence (EVM RPC alone is insufficient)."
                    ),
                    confirmed=False,
                )
            )

        if proposer is not None and proposer == vid and in_consensus:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=f"Current proposer val id is {vid}.",
                    confirmed=True,
                )
            )

        effectiveness = monad_effectiveness(
            in_consensus_set=in_consensus,
            eligible=view.eligible,
            local_evidence=local_evidence,
            authored=authored,
            missed=missed,
            lagging=lagging,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed, 40),
            missed_secondary_duties=8 if lagging else 0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing or lagging,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if not in_consensus or not view.eligible:
            risk = max(risk, 80.0)
        elif missed > 0:
            risk = max(risk, 55.0)

        expected = authored + missed if local_evidence and missed > 0 else (
            authored if local_evidence and authored > 0 and missed == 0 else None
        )
        # Authored-only local counts still do not prove expected/missed schedule.
        if local_evidence and missed == 0:
            expected = None

        return ValidatorStats(
            index=index,
            operator_id=str(vid),
            operator_index=vid,
            pubkey=view.auth_address,
            status=op_status,
            balance_base_units=view.stake,
            effective_balance_base_units=view.consensus_stake or view.stake,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(
                expected=expected or 0,
                successful=authored,
                missed=missed,
            ),
            duties=[
                DutyStats(
                    category="block",
                    label="Proposals",
                    expected=expected,
                    successful=authored,
                    missed=missed,
                    late=0,
                    weight=0.75,
                ),
                DutyStats(
                    category="other",
                    label="Set membership",
                    expected=1 if in_consensus else 0,
                    successful=1 if in_consensus else 0,
                    missed=0 if in_consensus else 1,
                    late=0,
                    weight=0.25,
                ),
            ],
            rewards_base_units=view.unclaimed_rewards,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="reward_loss",
            protocol_events=events,
        )

    def _failed_operator(
        self,
        index: int,
        vid: int,
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
            operator_id=str(vid),
            operator_index=vid,
            pubkey=str(vid),
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
