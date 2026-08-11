from __future__ import annotations

from validator_pulse.chains.base import ChainCollection
from validator_pulse.chains.solana.demo import (
    apply_demo_infrastructure,
    build_demo_solana_consensus,
    build_demo_validators,
)
from validator_pulse.chains.solana.rpc import (
    collect_solana_consensus,
    fetch_block_production_skip_rate,
    fetch_vote_account,
    fetch_vote_accounts_by_identity,
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
from validator_pulse.scoring import (
    compute_effectiveness_score,
    compute_slashing_risk_score,
)


class SolanaAdapter:
    """Solana validator adapter via JSON-RPC (vote accounts + block production)."""

    name = "solana"
    display_name = "Solana"
    operator_label = "validator"
    risk_kind = "slashing"
    risk_label = "Slashing risk"
    primary_duty_label = "Epoch credits"
    secondary_duty_label = "Leader slots"
    missed_duty_label = "Skipped slots"
    consensus_node_label = "Solana RPC"

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.solana_rpc_url and settings.solana_rpc_url.strip())

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
        consensus = build_demo_solana_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        votes = settings.solana_vote_account_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            vote_accounts=votes,
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
            rpc_url = (settings.solana_rpc_url or "").strip()
            consensus = await collect_solana_consensus(rpc_url)

            vote_accounts = settings.solana_vote_account_list()
            identities = settings.solana_identity_pubkey_list()

            targets: list[tuple[str, dict | None]] = []
            # Prefer explicit vote accounts; fall back to identity → vote lookup.
            if vote_accounts:
                for vote in vote_accounts:
                    try:
                        info = await fetch_vote_account(rpc_url, vote)
                    except Exception as exc:  # noqa: BLE001
                        info = None
                        consensus = consensus.model_copy(
                            update={
                                "last_error": (
                                    f"{consensus.last_error}; {vote}: {exc}"
                                    if consensus.last_error
                                    else f"{vote}: {exc}"
                                )
                            }
                        )
                    targets.append((vote, info))
            elif identities:
                for identity in identities:
                    try:
                        matches = await fetch_vote_accounts_by_identity(rpc_url, identity)
                    except Exception as exc:  # noqa: BLE001
                        matches = []
                        consensus = consensus.model_copy(
                            update={
                                "last_error": (
                                    f"{consensus.last_error}; {identity}: {exc}"
                                    if consensus.last_error
                                    else f"{identity}: {exc}"
                                )
                            }
                        )
                    if matches:
                        for match in matches:
                            targets.append((match["vote_pubkey"], match))
                    else:
                        targets.append((identity, None))
            else:
                err = "No VALIDATOR_VOTE_ACCOUNTS or SOLANA_IDENTITY_PUBKEYS configured"
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

            operators: list[ValidatorStats] = []
            for index, (vote_pubkey, info) in enumerate(targets):
                operators.append(
                    await self._build_live_operator(
                        index,
                        vote_pubkey,
                        info,
                        rpc_url,
                        consensus,
                        infrastructure,
                        settings,
                    )
                )

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
        vote_pubkey: str,
        info: dict | None,
        rpc_url: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
    ) -> ValidatorStats:
        if info is None:
            return self._failed_operator(
                index,
                vote_pubkey,
                consensus,
                infrastructure,
                "Vote account not found via getVoteAccounts",
            )

        identity = str(info.get("node_pubkey") or "")
        delinquent = bool(info.get("delinquent"))
        credits = int(info.get("epoch_credits_earned") or 0)
        # Use a soft expected baseline from recent credits when available.
        expected_credits = max(credits, 1) if credits else 100_000
        # When delinquent, treat most credits as missed relative to a healthy baseline.
        if delinquent:
            expected_credits = max(expected_credits, 100_000)
            credits = min(credits, int(expected_credits * 0.05))

        leader_slots = 0
        produced = 0
        skip_rate = 0.0
        if identity:
            try:
                leader_slots, produced, skip_rate = await fetch_block_production_skip_rate(
                    rpc_url, identity
                )
            except Exception:  # noqa: BLE001
                leader_slots, produced, skip_rate = 0, 0, 0.0

        skipped = max(0, leader_slots - produced)
        if leader_slots == 0:
            # No leader schedule data — score from credits only.
            leader_slots = 0
            produced = 0
            skipped = 0

        missed_credits = max(0, expected_credits - credits)
        effectiveness = compute_effectiveness_score(
            attestations_expected=max(expected_credits, 1),
            attestations_successful=credits,
            attestations_late=0,
            proposals_expected=leader_slots,
            proposals_successful=produced,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=min(missed_credits // 5_000, 40),
            missed_secondary_duties=min(skipped, 20),
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 8),
            effectiveness_score=effectiveness,
        )

        events: list[ProtocolEvent] = []
        status = "active"
        if delinquent:
            risk = 100.0
            status = "delinquent"
            events.append(
                ProtocolEvent(
                    kind="delinquent",
                    severity="critical",
                    message="Vote account is delinquent (last vote lagging cluster tip).",
                    confirmed=True,
                )
            )
        elif skip_rate >= settings.alert_skip_rate_above:
            risk = max(risk, 55.0)
            status = "high_skip"
            events.append(
                ProtocolEvent(
                    kind="high_skip_rate",
                    severity="warning",
                    message=(
                        f"Skip rate {skip_rate:.1f}% is at or above "
                        f"{settings.alert_skip_rate_above}%."
                    ),
                    confirmed=True,
                )
            )

        stake = int(info.get("activated_stake") or 0)
        commission = int(info.get("commission") or 0)
        # Approximate commission-cut rewards placeholder (lamports).
        rewards = int(stake * 0.00001 * max(0, 100 - commission) / 100)

        return ValidatorStats(
            index=index,
            operator_id=vote_pubkey,
            operator_index=index,
            pubkey=identity or vote_pubkey,
            status=status,
            balance_base_units=stake + rewards,
            effective_balance_base_units=stake,
            attestations=AttestationStats(
                expected=max(expected_credits, 1),
                successful=credits,
                missed=missed_credits,
                late=0,
            ),
            proposals=ProposalStats(
                expected=leader_slots,
                successful=produced,
                missed=skipped,
            ),
            duties=[
                DutyStats(
                    category="vote",
                    label="Epoch credits",
                    expected=max(expected_credits, 1),
                    successful=credits,
                    missed=missed_credits,
                    late=0,
                    weight=0.7,
                ),
                DutyStats(
                    category="block",
                    label="Leader slots",
                    expected=leader_slots,
                    successful=produced,
                    missed=skipped,
                    late=0,
                    weight=0.3,
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
        vote_pubkey: str,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        error: str,
    ) -> ValidatorStats:
        effectiveness = compute_effectiveness_score(
            attestations_expected=1,
            attestations_successful=0,
            attestations_late=0,
            proposals_expected=0,
            proposals_successful=0,
        )
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
            operator_id=vote_pubkey,
            operator_index=index,
            pubkey=vote_pubkey,
            status="unreachable",
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(expected=1, successful=0, missed=1, late=0),
            proposals=ProposalStats(expected=0, successful=0, missed=0),
            duties=[
                DutyStats(
                    category="vote",
                    label="Epoch credits",
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
            risk_kind="slashing",
            protocol_events=[
                ProtocolEvent(
                    kind="rpc_error",
                    severity="warning",
                    message=error,
                    confirmed=False,
                )
            ],
            display_name=None,
            display_name_source=None,
        )
