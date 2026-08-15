from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.mina.archive import try_fetch_archive_blocks
from validator_pulse.chains.mina.demo import (
    apply_demo_infrastructure,
    build_demo_mina_consensus,
    build_demo_validators,
)
from validator_pulse.chains.mina.graphql import (
    MinaDaemonSnapshot,
    collect_mina_consensus,
    graphql_host_is_local,
)
from validator_pulse.chains.mina.local import (
    load_mina_log,
    parse_client_status,
    run_mina_client_status,
)
from validator_pulse.chains.mina.state import (
    CanonicalBlock,
    WonSlot,
    WonSlotStore,
    classify_won_slots,
    mina_effectiveness,
    near_slot_unsynced,
    normalize_public_key,
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


class MinaAdapter:
    """Mina block-producer adapter via local GraphQL + CLI/logs.

    Public GraphQL cannot enumerate another producer's private VRF slots.
    Exact expected duties require local `mina client status` and/or logs.
    Mina does not slash producer stake — risk is operational/reward loss.
    GraphQL is query-only; the full endpoint can submit transactions.
    """

    name = "mina"
    display_name = "Mina"
    operator_label = "block producer"
    risk_kind = "reward_loss"
    risk_label = "Reward risk"
    primary_duty_label = "Won slots"
    secondary_duty_label = "Canonical blocks"
    missed_duty_label = "Missed slots"
    consensus_node_label = "Mina daemon"

    def __init__(self) -> None:
        self._wins = WonSlotStore()

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.mina_graphql_url and settings.mina_graphql_url.strip())

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
        consensus = build_demo_mina_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        keys = settings.mina_producer_public_key_list() or None
        operators = build_demo_validators(
            consensus, infrastructure, public_keys=keys
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
            graphql_url = (settings.mina_graphql_url or "").strip()
            keys = [
                normalize_public_key(k) for k in settings.mina_producer_public_key_list()
            ]
            consensus, snap = await collect_mina_consensus(graphql_url)
            if not graphql_host_is_local(graphql_url):
                note = (
                    "Mina GraphQL is not loopback; the full endpoint can submit "
                    "transactions — prefer localhost or --limited-graphql-port"
                )
                consensus = consensus.model_copy(
                    update={
                        "last_error": (
                            f"{consensus.last_error}; {note}"
                            if consensus.last_error
                            else note
                        )
                    }
                )

            client_status = None
            client_text = run_mina_client_status(settings.mina_client_command)
            if client_text:
                client_status = parse_client_status(client_text)
                if client_status.peers:
                    consensus = consensus.model_copy(
                        update={
                            "peer_count": client_status.peers,
                            "connected_peers": client_status.peers,
                        }
                    )
                if client_status.block_height:
                    consensus = consensus.model_copy(
                        update={"head_slot": client_status.block_height}
                    )

            if not keys:
                err = "No MINA_PRODUCER_PUBLIC_KEYS configured"
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

            operators = [
                self._build_operator(
                    index,
                    pubkey,
                    consensus,
                    infrastructure,
                    settings,
                    snap=snap,
                    client_producers=client_status.producers if client_status else [],
                    client_next_slot=client_status.next_global_slot if client_status else None,
                    client_sync=client_status.sync_status if client_status else None,
                )
                for index, pubkey in enumerate(keys)
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
        pubkey: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
        *,
        snap: MinaDaemonSnapshot | None,
        client_producers: list[str],
        client_next_slot: int | None,
        client_sync: str | None,
    ) -> ValidatorStats:
        events: list[ProtocolEvent] = []
        current_slot = 0
        if snap:
            current_slot = snap.global_slot or snap.blockchain_length
        if not current_slot:
            current_slot = int(consensus.head_slot or 0)

        local_keys = set(snap.production_keys if snap else []) | set(client_producers)
        activated = pubkey in local_keys
        synced = True
        if snap:
            synced = snap.sync_status == "SYNCED"
        if client_sync:
            synced = client_sync in {"SYNCED", "SYNC"}

        trust_private = bool(snap and snap.local_graphql) or pubkey in local_keys
        if snap and not snap.local_graphql and pubkey not in local_keys:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        "GraphQL is not this producer's daemon; ignoring "
                        "nextBlockProduction as private VRF evidence."
                    ),
                    confirmed=True,
                )
            )

        for won in load_mina_log(settings.mina_log_path, pubkey=pubkey):
            self._wins.observe(won)
        if trust_private and snap:
            for item in snap.next_slots:
                self._wins.observe(
                    WonSlot(
                        pubkey=pubkey,
                        slot=item.slot,
                        epoch=item.epoch,
                        produced=False,
                        source="graphql",
                    )
                )
        if trust_private and client_next_slot:
            self._wins.observe(
                WonSlot(
                    pubkey=pubkey,
                    slot=client_next_slot,
                    produced=False,
                    source="cli",
                )
            )

        canonical_by_slot: dict[int, CanonicalBlock] = {}
        if snap:
            for block in snap.blocks:
                canonical_by_slot[block.slot] = block
        archive_rows, archive_note = try_fetch_archive_blocks(
            settings.mina_archive_database_url, pubkey=pubkey
        )
        if archive_note:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=archive_note,
                    confirmed=False,
                )
            )
        if archive_rows:
            for block in archive_rows:
                canonical_by_slot.setdefault(block.slot, block)
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"Archive history contributed {len(archive_rows)} blocks "
                        "for durable canonical matching."
                    ),
                    confirmed=True,
                )
            )

        won = self._wins.for_key(pubkey)
        has_local_evidence = bool(won)
        outcomes = (
            classify_won_slots(
                won=won,
                canonical_by_slot=canonical_by_slot,
                current_slot=current_slot,
                miss_grace_slots=max(0, int(settings.alert_mina_near_slot_slots)),
            )
            if has_local_evidence
            else []
        )

        frontier_ours = [
            b for b in (snap.blocks if snap else []) if b.creator == pubkey
        ]
        coinbase = sum(b.coinbase for b in frontier_ours)
        if archive_rows:
            coinbase = max(coinbase, sum(b.coinbase for b in archive_rows))

        if not has_local_evidence:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        "Won-slot schedule unavailable without local CLI/log evidence. "
                        f"Frontier shows {len(frontier_ours)} canonical blocks for this "
                        "key; expected duties are not invented from public GraphQL. "
                        "Mina does not slash producer stake."
                    ),
                    confirmed=True,
                )
            )

        canonical_n = sum(1 for o in outcomes if o.kind == "canonical")
        missed_n = sum(1 for o in outcomes if o.kind == "missed")
        orphaned_n = sum(1 for o in outcomes if o.kind == "orphaned")
        pending_n = sum(1 for o in outcomes if o.kind == "pending")
        if has_local_evidence:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info",
                    message=(
                        f"Local won slots: canonical={canonical_n} orphaned={orphaned_n} "
                        f"missed={missed_n} pending={pending_n} "
                        f"(source=CLI/logs{'/local GraphQL' if trust_private else ''}). "
                        "Mina does not slash producer stake."
                    ),
                    confirmed=True,
                )
            )
        if orphaned_n:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"{orphaned_n} produced block(s) orphaned by a competing winner. "
                        "Orphaning is moderate reward risk, not slashing."
                    ),
                    confirmed=True,
                )
            )
        if missed_n:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message=(
                        f"{missed_n} locally won slot(s) missed — coinbase not earned. "
                        "High reward risk; Mina does not slash stake."
                    ),
                    confirmed=True,
                )
            )

        near_slots = int(settings.alert_mina_near_slot_slots)
        if near_slot_unsynced(
            synced=synced,
            outcomes=outcomes,
            current_slot=current_slot,
            near_slots=near_slots,
        ) or (not synced and pending_n):
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message=(
                        f"Daemon not SYNCED within {near_slots} slot(s) of a won slot. "
                        "Near-slot unsynced/inactive state is critical reward risk, "
                        "not slashing."
                    ),
                    confirmed=True,
                )
            )
        if snap:
            lag = max(0, snap.highest_received - snap.blockchain_length)
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="info" if lag <= 1 else "warning",
                    message=(
                        f"Tip height={snap.blockchain_length} received={snap.highest_received} "
                        f"peers={snap.peers} sync={snap.sync_status} "
                        f"activated={pubkey in local_keys} freshness_lag={lag}."
                    ),
                    confirmed=True,
                )
            )

        if not activated and local_keys:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"{pubkey} is not in this daemon's blockProductionKeys "
                        f"({', '.join(local_keys)})."
                    ),
                    confirmed=True,
                )
            )

        effectiveness = mina_effectiveness(
            synced=synced, activated=activated or has_local_evidence, outcomes=outcomes or None
        )
        expected = len(outcomes) if has_local_evidence else None
        successful = canonical_n if has_local_evidence else 0
        # Frontier canonical blocks are observations, not expected duties.
        if not has_local_evidence:
            successful = 0

        op_status = "inactive"
        if not synced and (pending_n or near_slot_unsynced(
            synced=synced, outcomes=outcomes, current_slot=current_slot, near_slots=near_slots
        )):
            op_status = "unsynced"
        elif missed_n:
            op_status = "missed"
        elif orphaned_n:
            op_status = "orphaned"
        elif has_local_evidence and canonical_n and missed_n == 0:
            op_status = "active" if pending_n or canonical_n >= 2 else "active"
        elif has_local_evidence and canonical_n and missed_n:
            op_status = "recovering"
        elif activated and synced:
            op_status = "active"
        elif consensus.beacon_reachable:
            op_status = "unknown"

        # Recovery: canonical after a miss in the same window.
        if has_local_evidence and canonical_n and missed_n and synced:
            last_kinds = [o.kind for o in outcomes]
            if last_kinds and last_kinds[-1] == "canonical":
                op_status = "recovering"

        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=missed_n,
            missed_secondary_duties=orphaned_n,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=not synced,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if op_status == "unsynced":
            risk = max(risk, 85.0)
        elif op_status == "missed":
            risk = max(risk, 65.0)
        elif op_status == "orphaned":
            risk = max(risk, 40.0)

        return ValidatorStats(
            index=index,
            operator_id=pubkey,
            pubkey=pubkey,
            status=op_status,
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(
                expected=expected or 0,
                successful=successful,
                missed=missed_n if has_local_evidence else 0,
            ),
            duties=[
                DutyStats(
                    category="block",
                    label="Won slots",
                    expected=expected,
                    successful=successful,
                    missed=missed_n if has_local_evidence else 0,
                    late=orphaned_n,
                    weight=1.0,
                )
            ],
            rewards_base_units=coinbase,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="reward_loss",
            protocol_events=events,
        )
