from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.multiversx.api import (
    Heartbeat,
    NodeStatus,
    ValidatorStat,
    collect_mx_consensus,
    parse_heartbeats,
    parse_validator_statistics,
    try_get,
)
from validator_pulse.chains.multiversx.demo import (
    apply_demo_infrastructure,
    build_demo_mx_consensus,
    build_demo_validators,
)
from validator_pulse.chains.multiversx.state import (
    JailThreshold,
    is_jailed,
    is_passive_recovery,
    is_slashed,
    mx_effectiveness,
    normalize_bls_key,
    parse_peer_type,
    rating_near_jail,
    resolve_jail_threshold,
    shard_label,
    success_ratio,
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


class MultiversXAdapter:
    """MultiversX validator adapter via local node APIs + gateway heartbeat.

    Each BLS key is scored independently (multikey hosts). Jail from low rating
    is distinct from serious-offence stake slashing. Unjail recovery is passive.
    """

    name = "multiversx"
    display_name = "MultiversX"
    operator_label = "validator"
    risk_kind = "jail"
    risk_label = "Jail risk"
    primary_duty_label = "Leader proposals"
    secondary_duty_label = "Consensus signatures"
    missed_duty_label = "Failed signatures"
    consensus_node_label = "MultiversX node"

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        node = bool(settings.multiversx_node_api_url and settings.multiversx_node_api_url.strip())
        gateway = bool(settings.multiversx_gateway_url and settings.multiversx_gateway_url.strip())
        return not (node or gateway)

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
        consensus = build_demo_mx_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        keys = settings.multiversx_bls_key_list() or None
        operators = build_demo_validators(consensus, infrastructure, bls_keys=keys)
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
            node_url = (settings.multiversx_node_api_url or "").strip() or None
            gateway_url = (settings.multiversx_gateway_url or "").strip() or None
            keys = [normalize_bls_key(k) for k in settings.multiversx_bls_key_list()]
            consensus, node_status, _net = await collect_mx_consensus(
                node_url=node_url,
                gateway_url=gateway_url,
                shard_id=settings.multiversx_shard_id,
            )

            hb_source = gateway_url or node_url
            heartbeats: dict[str, Heartbeat] = {}
            stats: dict[str, ValidatorStat] = {}
            ratings_cfg = None
            if hb_source:
                raw, err = await try_get(hb_source, "/node/heartbeatstatus")
                if err:
                    consensus = _append_error(consensus, err)
                elif raw is not None:
                    heartbeats = parse_heartbeats(raw)
            if gateway_url:
                raw, err = await try_get(gateway_url, "/validator/statistics")
                if err:
                    consensus = _append_error(consensus, f"statistics {err}")
                elif raw is not None:
                    stats = parse_validator_statistics(raw)
                raw, err = await try_get(gateway_url, "/network/ratings")
                if not err and isinstance(raw, dict):
                    ratings_cfg = raw.get("config") if isinstance(raw.get("config"), dict) else raw

            jail = resolve_jail_threshold(
                override=settings.multiversx_jail_rating_threshold,
                ratings_config=ratings_cfg if isinstance(ratings_cfg, dict) else None,
            )

            if not keys:
                consensus = _append_error(consensus, "No MULTIVERSX_VALIDATOR_BLS_KEYS configured")
                if consensus.status == "healthy":
                    consensus = consensus.model_copy(update={"status": "degraded"})
                return ChainCollection(
                    consensus=consensus,
                    operators=[],
                    infrastructure=infrastructure,
                )

            version = None
            if node_status and node_status.version:
                version = node_status.version
            elif heartbeats:
                version = next(iter(heartbeats.values())).version

            operators = [
                self._build_operator(
                    index,
                    bls,
                    consensus,
                    infrastructure,
                    settings,
                    heartbeat=heartbeats.get(bls),
                    stat=stats.get(bls),
                    node_status=node_status,
                    jail=jail,
                    version=version,
                )
                for index, bls in enumerate(keys)
            ]
            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token)

    def _build_operator(
        self,
        index: int,
        bls: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
        *,
        heartbeat: Heartbeat | None,
        stat: ValidatorStat | None,
        node_status: NodeStatus | None,
        jail: JailThreshold,
        version: str | None,
    ) -> ValidatorStats:
        events: list[ProtocolEvent] = []
        peer_type = parse_peer_type(
            (stat.validator_status if stat else None)
            or (heartbeat.peer_type if heartbeat else None)
            or (node_status.peer_type if node_status else None)
        )
        status_raw = stat.validator_status if stat else heartbeat.peer_type if heartbeat else None
        jailed = is_jailed(peer_type, status_raw)
        slashed = is_slashed(peer_type, status_raw)
        passive = is_passive_recovery(peer_type, status_raw)
        active = heartbeat.is_active if heartbeat else None
        shard = None
        if stat and stat.shard_id is not None:
            shard = stat.shard_id
        elif heartbeat and heartbeat.computed_shard is not None:
            shard = heartbeat.computed_shard
        elif node_status:
            shard = node_status.shard_id
        rating = stat.rating if stat else None
        temp = stat.temp_rating if stat else None

        lead_ok = stat.leader_success if stat else None
        lead_fail = stat.leader_failure if stat else None
        val_ok = stat.validator_success if stat else None
        val_fail = stat.validator_failure if stat else None
        local_keys = set(node_status.public_keys) if node_status else set()
        if node_status and bls in local_keys:
            if lead_ok is None and node_status.count_accepted_blocks is not None:
                lead_ok = node_status.count_accepted_blocks
            if lead_fail is None and node_status.count_leader is not None:
                lead_fail = max(0, node_status.count_leader - (lead_ok or 0))
            if val_ok is None and node_status.count_consensus_accepted is not None:
                val_ok = node_status.count_consensus_accepted
            if val_fail is None and node_status.count_consensus is not None:
                val_fail = max(0, node_status.count_consensus - (val_ok or 0))

        proposal_ratio = success_ratio(lead_ok, lead_fail)
        sig_ratio = success_ratio(val_ok, val_fail)
        hb_version = heartbeat.version if heartbeat else version
        freshness = "ok"
        if heartbeat and heartbeat.nonce is not None and consensus.head_slot:
            gap = int(consensus.head_slot) - heartbeat.nonce
            if gap > 50:
                freshness = f"stale nonce gap={gap}"

        events.append(
            ProtocolEvent(
                kind="other",
                severity="info",
                message=(
                    f"Heartbeat active={active} peerType={peer_type} "
                    f"shard={shard_label(shard)} syncing={consensus.syncing} "
                    f"epoch={consensus.finalized_epoch} round={consensus.justified_epoch} "
                    f"peers={consensus.connected_peers} version={hb_version or 'n/a'} "
                    f"freshness={freshness}."
                ),
                confirmed=True,
            )
        )
        if rating is not None:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"Rating {rating:.2f} temp={temp} modifier={stat.rating_modifier if stat else None} "
                        f"jail threshold {jail.label()} "
                        f"(proposals {lead_ok}/{lead_fail} sigs {val_ok}/{val_fail})."
                    ),
                    confirmed=True,
                )
            )
        elif lead_ok is None and val_ok is None:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        "Proposal/signature counters and rating not exposed for this key "
                        "(need /validator/statistics or local /node/status)."
                    ),
                    confirmed=False,
                )
            )

        if heartbeat and heartbeat.num_instances and heartbeat.num_instances > 1:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"Multikey host reports {heartbeat.num_instances} instances; "
                        "this BLS key is scored independently."
                    ),
                    confirmed=True,
                )
            )

        if slashed:
            events.append(
                ProtocolEvent(
                    kind="slashed",
                    severity="critical",
                    message=(
                        "Serious-offence slash indicator on this BLS key — distinct from "
                        "downtime jail. Stake slashing / validator removal."
                    ),
                    confirmed=True,
                )
            )
        elif jailed:
            events.append(
                ProtocolEvent(
                    kind="jailed",
                    severity="critical",
                    message=(
                        f"Validator jailed (rating {rating}, threshold {jail.label()}). "
                        "Downtime jail at epoch boundary, not serious-offence slash. "
                        "Unjail tx required."
                    ),
                    confirmed=True,
                )
            )
        elif rating is not None and rating_near_jail(
            rating, jail, settings.alert_multiversx_rating_below
        ):
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message=(
                        f"Rating {rating:.2f} is near/below jail threshold {jail.label()} "
                        f"(warn below {settings.alert_multiversx_rating_below:.0f}). "
                        "Jail (not slash) applies at epoch end if unrecovered."
                    ),
                    confirmed=True,
                )
            )
        if passive and not jailed:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Status '{peer_type}' is waiting/queued/new — passive recovery "
                        "after unjail or shuffle; not yet earning consensus rewards."
                    ),
                    confirmed=True,
                )
            )
        if active is False and not jailed:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message="Heartbeat inactive for this BLS key.",
                    confirmed=True,
                )
            )
        if not heartbeat and not stat:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=f"BLS key not found in heartbeat or validator statistics.",
                    confirmed=True,
                )
            )

        effectiveness = mx_effectiveness(
            heartbeat_active=active,
            jailed=jailed,
            slashed=slashed,
            rating=rating,
            jail_threshold=jail.rating,
            proposal_ratio=proposal_ratio,
            signature_ratio=sig_ratio,
            passive=passive,
        )
        lead_expected = (
            (lead_ok or 0) + (lead_fail or 0)
            if lead_ok is not None or lead_fail is not None
            else None
        )
        sig_expected = (
            (val_ok or 0) + (val_fail or 0)
            if val_ok is not None or val_fail is not None
            else None
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(lead_fail or 0, 12),
            missed_secondary_duties=min(val_fail or 0, 20),
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing or active is False,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        op_status = "unknown"
        if slashed:
            op_status = "slashed"
            risk = max(risk, 95.0)
        elif jailed:
            op_status = "jailed"
            risk = max(risk, 90.0)
        elif rating is not None and rating_near_jail(
            rating, jail, settings.alert_multiversx_rating_below
        ):
            op_status = "degraded"
            risk = max(risk, 65.0)
        elif passive:
            op_status = "recovering"
            risk = max(risk, 30.0)
        elif active is False:
            op_status = "inactive"
            risk = max(risk, 50.0)
        elif peer_type == "eligible" and active:
            op_status = "active"

        return ValidatorStats(
            index=index,
            operator_id=bls,
            pubkey=bls,
            status=op_status,
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(
                expected=sig_expected or 0,
                successful=val_ok or 0,
                missed=val_fail or 0,
                late=stat.ignored_signatures if stat and stat.ignored_signatures else 0,
            ),
            proposals=ProposalStats(
                expected=lead_expected or 0,
                successful=lead_ok or 0,
                missed=lead_fail or 0,
            ),
            duties=[
                DutyStats(
                    category="proposal",
                    label="Leader proposals",
                    expected=lead_expected,
                    successful=lead_ok or 0,
                    missed=lead_fail or 0,
                    late=0,
                    weight=0.5,
                ),
                DutyStats(
                    category="vote",
                    label="Consensus signatures",
                    expected=sig_expected,
                    successful=val_ok or 0,
                    missed=val_fail or 0,
                    late=stat.ignored_signatures if stat and stat.ignored_signatures else 0,
                    weight=0.5,
                ),
            ],
            rewards_base_units=0,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="jail",
            protocol_events=events,
        )


def _append_error(consensus: ConsensusHealth, note: str) -> ConsensusHealth:
    return consensus.model_copy(
        update={
            "last_error": f"{consensus.last_error}; {note}" if consensus.last_error else note
        }
    )
