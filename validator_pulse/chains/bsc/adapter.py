from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.bsc.abi import (
    EXPECTED_CHAIN_IDS,
    SLASH_DOUBLE_SIGN,
    SLASH_INDICATOR,
    SLASH_MALICIOUS_VOTE,
    STAKE_HUB,
    VALIDATOR_SET,
    is_zero_address,
    normalize_address,
    slash_type_label,
)
from validator_pulse.chains.bsc.demo import (
    apply_demo_infrastructure,
    build_demo_bsc_consensus,
    build_demo_validators,
)
from validator_pulse.chains.bsc.rpc import (
    collect_bsc_consensus,
    fetch_is_current_validator,
    fetch_living_validators,
    fetch_mining_validators,
    fetch_recent_slash_events,
    fetch_slash_indicator,
    fetch_slash_thresholds,
    fetch_turn_length,
    fetch_working_validators,
    resolve_validator,
    sample_recent_miners,
    try_fetch_bsc_metrics,
)
from validator_pulse.chains.bsc.state import ValidatorSetStore, bsc_effectiveness
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

# Recent-log window in blocks (not a slash threshold).
_SLASH_LOG_WINDOW = 2048
_MINER_SAMPLE = 16


class BscAdapter:
    """BNB Smart Chain validator adapter via JSON-RPC system contracts.

    SlashIndicator + StakeHub + BSCValidatorSet. Downtime thresholds come from
    the contract (or explicit config) — never from conflicting public docs.
    """

    name = "bsc"
    display_name = "BNB Smart Chain"
    operator_label = "validator"
    risk_kind = "slashing"
    risk_label = "Slashing risk"
    primary_duty_label = "Block turns"
    secondary_duty_label = "Finality votes"
    missed_duty_label = "Missed turns"
    consensus_node_label = "BSC RPC"

    def __init__(self) -> None:
        self._sets = ValidatorSetStore()

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.bsc_rpc_url and settings.bsc_rpc_url.strip())

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
        consensus = build_demo_bsc_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        addrs = settings.bsc_validator_address_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            addresses=addrs,
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
            rpc_url = (settings.bsc_rpc_url or "").strip()
            slash_contract = (
                settings.bsc_slash_contract or SLASH_INDICATOR
            ).strip() or SLASH_INDICATOR
            stake_hub = (
                settings.bsc_stake_hub_contract or STAKE_HUB
            ).strip() or STAKE_HUB
            validator_set = VALIDATOR_SET
            configured = settings.bsc_validator_address_list()
            consensus = await collect_bsc_consensus(
                rpc_url, expected_chain_ids=EXPECTED_CHAIN_IDS
            )

            metrics_url = (settings.bsc_metrics_url or "").strip()
            node_metrics = None
            if metrics_url:
                node_metrics, note = await try_fetch_bsc_metrics(metrics_url)
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
                if node_metrics and node_metrics.peers is not None:
                    consensus = consensus.model_copy(
                        update={
                            "peer_count": node_metrics.peers,
                            "connected_peers": node_metrics.peers,
                        }
                    )

            if not configured:
                err = "No BSC_VALIDATOR_ADDRESSES configured"
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

            working: list[str] = []
            living: list[str] = []
            mining: list[str] = []
            turn_length = 1
            thresholds = None
            miners: list[str] = []
            if consensus.beacon_reachable:
                try:
                    working = await fetch_working_validators(rpc_url, validator_set)
                    living = await fetch_living_validators(rpc_url, validator_set)
                    try:
                        mining = await fetch_mining_validators(rpc_url, validator_set)
                    except Exception:  # noqa: BLE001
                        mining = list(working)
                    turn_length = await fetch_turn_length(rpc_url, validator_set)
                    thresholds = await fetch_slash_thresholds(
                        rpc_url,
                        slash_contract,
                        misdemeanor_override=settings.bsc_misdemeanor_threshold,
                        felony_override=settings.bsc_felony_threshold,
                    )
                    try:
                        miners = await sample_recent_miners(
                            rpc_url,
                            consensus.head_slot,
                            window=max(_MINER_SAMPLE, turn_length),
                        )
                    except Exception:  # noqa: BLE001
                        miners = []
                except Exception as exc:  # noqa: BLE001
                    consensus = consensus.model_copy(
                        update={
                            "status": "degraded",
                            "last_error": (
                                f"{consensus.last_error}; system contracts: {exc}"
                                if consensus.last_error
                                else f"system contracts: {exc}"
                            ),
                        }
                    )

            joined, left, first = self._sets.observe(working)
            if first and working:
                consensus = consensus.model_copy(
                    update={
                        "last_error": (
                            f"{consensus.last_error}; working set n={len(working)}, "
                            f"turnLength={turn_length}"
                            if consensus.last_error
                            else (
                                f"Working set n={len(working)}, "
                                f"turnLength={turn_length}"
                            )
                        )
                    }
                )

            living_set = set(living)
            mining_set = set(mining or working)
            operators = [
                await self._build_operator(
                    index,
                    raw,
                    rpc_url,
                    slash_contract,
                    stake_hub,
                    validator_set,
                    consensus,
                    infrastructure,
                    working=set(working),
                    living=living_set,
                    mining=mining_set,
                    thresholds=thresholds,
                    miners=miners,
                    turn_length=turn_length,
                )
                for index, raw in enumerate(configured)
            ]
            # Recompute join/leave against resolved consensus addresses.
            resolved_ops: list[ValidatorStats] = []
            for op in operators:
                consensus_addr = (op.withdrawal_address or "").lower()
                was_joined = consensus_addr in joined
                was_left = consensus_addr in left
                if was_joined or was_left:
                    events = list(op.protocol_events)
                    if was_left:
                        events.insert(
                            0,
                            ProtocolEvent(
                                kind="other",
                                severity="warning",
                                message=(
                                    f"Consensus {consensus_addr} left the working "
                                    "validator set (set change)."
                                ),
                                confirmed=True,
                            ),
                        )
                    elif was_joined:
                        events.insert(
                            0,
                            ProtocolEvent(
                                kind="other",
                                severity="info",
                                message=(
                                    f"Consensus {consensus_addr} joined the working "
                                    "validator set."
                                ),
                                confirmed=True,
                            ),
                        )
                    op = op.model_copy(update={"protocol_events": events})
                resolved_ops.append(op)
            return ChainCollection(
                consensus=consensus,
                operators=resolved_ops,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    async def _build_operator(
        self,
        index: int,
        raw: str,
        rpc_url: str,
        slash_contract: str,
        stake_hub: str,
        validator_set: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        *,
        working: set[str],
        living: set[str],
        mining: set[str],
        thresholds,
        miners: list[str],
        turn_length: int,
    ) -> ValidatorStats:
        del turn_length
        try:
            resolved = await resolve_validator(
                rpc_url, stake_hub, raw, stakehub_operators=None
            )
        except Exception as exc:  # noqa: BLE001
            return self._failed_operator(index, raw, consensus, infrastructure, str(exc))

        operator = resolved.operator
        cons = resolved.consensus
        basic = resolved.basic
        jailed = bool(basic.jailed)
        in_working = False
        if cons and not is_zero_address(cons):
            if cons in working:
                in_working = True
            elif consensus.beacon_reachable:
                try:
                    in_working = await fetch_is_current_validator(
                        rpc_url, validator_set, cons
                    )
                except Exception:  # noqa: BLE001
                    in_working = cons in working
        in_living = cons in living if cons else False
        in_mining = cons in mining if cons else False
        maintaining = in_living and not in_working and not jailed

        slash_height = slash_count = 0
        if cons and not is_zero_address(cons) and consensus.beacon_reachable:
            try:
                slash_height, slash_count = await fetch_slash_indicator(
                    rpc_url, slash_contract, cons
                )
            except Exception:  # noqa: BLE001
                slash_height, slash_count = 0, 0

        misdemeanor = thresholds.misdemeanor if thresholds else 0
        felony = thresholds.felony if thresholds else 0
        produced = miners.count(cons) if cons else 0

        events: list[ProtocolEvent] = []
        if not resolved.on_stakehub and is_zero_address(cons) and basic.created_time == 0:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Address {normalize_address(raw)} did not resolve through "
                        "StakeHub (unknown operator/consensus)."
                    ),
                    confirmed=True,
                )
            )

        if resolved.on_stakehub or not is_zero_address(cons):
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"StakeHub identity operator={operator} consensus={cons} "
                        f"vote=0x{resolved.vote.hex()[:16] + '…' if resolved.vote else '0'}."
                    ),
                    confirmed=True,
                )
            )

        if thresholds is not None:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"Slash indicator height={slash_height} count={slash_count}; "
                        f"{thresholds.label()}."
                    ),
                    confirmed=True,
                )
            )

        double_sign = False
        malicious_vote = False
        if consensus.beacon_reachable and not is_zero_address(operator):
            head = consensus.head_slot
            try:
                recent = await fetch_recent_slash_events(
                    rpc_url,
                    stake_hub=stake_hub,
                    slash_contract=slash_contract,
                    operator=operator,
                    consensus=cons,
                    from_block=max(0, head - _SLASH_LOG_WINDOW),
                    to_block=head,
                )
            except Exception:  # noqa: BLE001
                recent = []
            for ev in recent:
                if ev.slash_type == SLASH_DOUBLE_SIGN or ev.kind == "double-sign":
                    double_sign = True
                    events.append(
                        ProtocolEvent(
                            kind="slashed",
                            severity="critical",
                            message=(
                                "Double-sign slash event "
                                f"(amount={ev.amount}, jailUntil={ev.jail_until})."
                            ),
                            confirmed=True,
                        )
                    )
                elif (
                    ev.slash_type == SLASH_MALICIOUS_VOTE
                    or ev.kind == "malicious finality vote"
                ):
                    malicious_vote = True
                    events.append(
                        ProtocolEvent(
                            kind="slashed",
                            severity="critical",
                            message=(
                                "Malicious fast-finality vote slash event "
                                f"(type={slash_type_label(ev.slash_type or 2)})."
                            ),
                            confirmed=True,
                        )
                    )
                elif ev.kind == "jailed":
                    events.append(
                        ProtocolEvent(
                            kind="jailed",
                            severity="critical",
                            message="StakeHub ValidatorJailed event in the recent window.",
                            confirmed=True,
                        )
                    )
                elif ev.kind == "downtime":
                    events.append(
                        ProtocolEvent(
                            kind="other",
                            severity="warning",
                            message=(
                                f"Downtime slash event amount={ev.amount} "
                                f"jailUntil={ev.jail_until}."
                            ),
                            confirmed=True,
                        )
                    )

        if jailed:
            events.append(
                ProtocolEvent(
                    kind="jailed",
                    severity="critical",
                    message=(
                        f"Validator is jailed (jailUntil={basic.jail_until})."
                    ),
                    confirmed=True,
                )
            )
        elif maintaining:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        "Validator is in maintenance (living set, not working)."
                    ),
                    confirmed=True,
                )
            )
        elif not in_working:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        "Validator is not in the current working set "
                        "(pending set change or ineligible)."
                    ),
                    confirmed=True,
                )
            )

        if thresholds is not None and slash_count >= felony > 0:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message=(
                        f"Slash indicator {slash_count} reached felony threshold "
                        f"{felony} ({thresholds.source})."
                    ),
                    confirmed=True,
                )
            )
        elif thresholds is not None and slash_count >= misdemeanor > 0:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Slash indicator {slash_count} reached misdemeanor "
                        f"threshold {misdemeanor} ({thresholds.source})."
                    ),
                    confirmed=True,
                )
            )

        op_status = "active"
        if double_sign or malicious_vote:
            op_status = "slashed"
        elif jailed:
            op_status = "jailed"
        elif maintaining:
            op_status = "maintenance"
        elif not in_working:
            op_status = "inactive"
        elif thresholds is not None and slash_count >= misdemeanor > 0:
            op_status = "degraded"

        effectiveness = bsc_effectiveness(
            in_working_set=in_working,
            jailed=jailed,
            maintaining=maintaining,
            slash_count=slash_count,
            misdemeanor=misdemeanor or 1,
            double_sign=double_sign,
            malicious_vote=malicious_vote,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(slash_count, 40),
            missed_secondary_duties=8 if maintaining else 0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing or maintaining,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if double_sign or malicious_vote:
            risk = 100.0
        elif jailed:
            risk = max(risk, 95.0)
        elif thresholds is not None and slash_count >= felony > 0:
            risk = max(risk, 90.0)
        elif thresholds is not None and slash_count >= misdemeanor > 0:
            risk = max(risk, 55.0)

        missed = slash_count
        expected: int | None
        if in_mining or in_working:
            expected = produced + missed if (produced or missed) else None
        else:
            expected = None

        moniker = (resolved.description.moniker or "").strip() or None
        return ValidatorStats(
            index=index,
            operator_id=operator,
            pubkey=cons if cons else operator,
            withdrawal_address=cons,
            display_name=moniker,
            display_name_source="stakehub" if moniker else None,
            status=op_status,
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(
                expected=expected or 0,
                successful=produced,
                missed=missed,
            ),
            duties=[
                DutyStats(
                    category="block",
                    label="Block turns",
                    expected=expected,
                    successful=produced,
                    missed=missed,
                    late=0,
                    weight=0.7,
                ),
                DutyStats(
                    category="vote",
                    label="Finality votes",
                    expected=None if not in_working else 1,
                    successful=0 if malicious_vote else (1 if in_working else 0),
                    missed=1 if malicious_vote else 0,
                    late=0,
                    weight=0.3,
                ),
            ],
            rewards_base_units=0,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="slashing",
            protocol_events=events,
        )

    def _failed_operator(
        self,
        index: int,
        raw: str,
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
            operator_id=normalize_address(raw),
            pubkey=normalize_address(raw),
            status="unreachable",
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(expected=0, successful=0, missed=0),
            duties=[
                DutyStats(
                    category="block",
                    label="Block turns",
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
