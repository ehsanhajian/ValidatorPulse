from __future__ import annotations

from typing import Any

from validator_pulse.chains.algorand.auth import redact_secrets, resolve_algod_token
from validator_pulse.chains.algorand.demo import (
    apply_demo_infrastructure,
    build_demo_algorand_consensus,
    build_demo_validators,
)
from validator_pulse.chains.algorand.keys import (
    algorand_effectiveness,
    evaluate_partkey_health,
    keys_for_address,
    parse_participation_keys,
)
from validator_pulse.chains.algorand.rpc import (
    collect_algod_consensus,
    fetch_account,
    fetch_participation_keys,
    try_fetch_algod_metrics,
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


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return 0


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


class AlgorandAdapter:
    """Algorand participation-node adapter via local authenticated algod REST."""

    name = "algorand"
    display_name = "Algorand"
    operator_label = "participation node"
    risk_kind = "suspension"
    risk_label = "Operational risk"
    primary_duty_label = "Observed votes"
    secondary_duty_label = "Observed proposals"
    missed_duty_label = "Unobservable misses"
    consensus_node_label = "algod"

    def __init__(self) -> None:
        # Previous poll snapshots for transition detection (online / incentive).
        self._prev_online: dict[str, bool] = {}
        self._prev_eligible: dict[str, bool] = {}
        # Observed activity counters — accumulate advances only, never invent misses.
        self._vote_obs: dict[str, int] = {}
        self._proposal_obs: dict[str, int] = {}
        self._last_vote_round: dict[str, int] = {}
        self._last_proposal_round: dict[str, int] = {}

    def is_demo(self, settings: Settings) -> bool:
        if settings.demo_mode:
            return True
        return not bool(settings.algorand_algod_url and settings.algorand_algod_url.strip())

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
        consensus = build_demo_algorand_consensus()
        infrastructure = apply_demo_infrastructure(infrastructure)
        accounts = settings.algorand_account_address_list() or None
        operators = build_demo_validators(
            consensus,
            infrastructure,
            account_addresses=accounts,
            partkey_warning_rounds=settings.alert_algorand_partkey_warning_rounds,
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
        token_bind = bind_rpc_http_config(RpcHttpConfig.from_settings(settings))
        api_token = resolve_algod_token(settings)
        try:
            algod_url = (settings.algorand_algod_url or "").strip()
            accounts = settings.algorand_account_address_list()
            consensus = await collect_algod_consensus(algod_url, token=api_token)

            metrics_url = (settings.algorand_metrics_url or "").strip()
            if metrics_url:
                ok, note = await try_fetch_algod_metrics(metrics_url)
                if not ok and note:
                    note = redact_secrets(note, api_token)
                    consensus = consensus.model_copy(
                        update={
                            "last_error": (
                                f"{consensus.last_error}; {note}"
                                if consensus.last_error
                                else note
                            )
                        }
                    )

            if not accounts:
                err = "No ALGORAND_ACCOUNT_ADDRESSES configured"
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

            try:
                raw_keys = await fetch_participation_keys(algod_url, token=api_token)
                all_keys = parse_participation_keys(raw_keys)
            except Exception as exc:  # noqa: BLE001
                msg = redact_secrets(str(exc), api_token)
                consensus = consensus.model_copy(
                    update={
                        "status": "degraded",
                        "last_error": (
                            f"{consensus.last_error}; participation keys unavailable: {msg}"
                            if consensus.last_error
                            else f"participation keys unavailable: {msg}"
                        ),
                    }
                )
                all_keys = []

            operators = [
                await self._build_live_operator(
                    index,
                    address,
                    algod_url,
                    api_token,
                    consensus,
                    infrastructure,
                    settings,
                    all_keys=all_keys,
                )
                for index, address in enumerate(accounts)
            ]
            return ChainCollection(
                consensus=consensus,
                operators=operators,
                infrastructure=infrastructure,
            )
        finally:
            reset_rpc_http_config(token_bind)

    async def _build_live_operator(
        self,
        index: int,
        address: str,
        algod_url: str,
        api_token: str | None,
        consensus: ConsensusHealth,
        infrastructure: InfrastructureHealth,
        settings: Settings,
        *,
        all_keys: list,
    ) -> ValidatorStats:
        try:
            account = await fetch_account(algod_url, address, token=api_token)
        except Exception as exc:  # noqa: BLE001
            return self._failed_operator(
                index,
                address,
                consensus,
                infrastructure,
                redact_secrets(str(exc), api_token),
            )

        status_raw = str(account.get("status") or "").strip()
        online = status_raw.lower() == "online"
        eligible = _as_bool(account.get("incentive-eligible"))
        amount = _as_int(account.get("amount"))
        rewards = _as_int(account.get("pending-rewards") or account.get("rewards"))
        last_heartbeat = _as_int(account.get("last-heartbeat"))
        last_proposed_acct = _as_int(account.get("last-proposed"))

        account_keys = keys_for_address(all_keys, address)
        health = evaluate_partkey_health(
            account_keys,
            current_round=consensus.head_slot,
            warning_rounds=settings.alert_algorand_partkey_warning_rounds,
        )

        # Observed activity from local participation keys (not expected duties).
        key = health.key
        last_vote = key.last_vote if key else 0
        last_proposal = max(
            key.last_proposal if key else 0,
            last_proposed_acct,
        )
        votes_obs = self._bump_observed(self._vote_obs, self._last_vote_round, address, last_vote)
        proposals_obs = self._bump_observed(
            self._proposal_obs,
            self._last_proposal_round,
            address,
            last_proposal,
        )
        activity_advancing = votes_obs > 0 or proposals_obs > 0 or last_vote > 0

        events: list[ProtocolEvent] = []
        op_status = "online" if online else status_raw.lower() or "offline"

        prev_online = self._prev_online.get(address)
        prev_eligible = self._prev_eligible.get(address)
        if prev_online is True and not online:
            op_status = "offline"
            events.append(
                ProtocolEvent(
                    kind="suspended",
                    severity="critical",
                    message="Account transitioned from Online to Offline.",
                    confirmed=True,
                )
            )
        if prev_eligible is True and not eligible:
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message="Incentive eligibility became false.",
                    confirmed=True,
                )
            )
        self._prev_online[address] = online
        self._prev_eligible[address] = eligible

        if not online and not any(e.kind == "suspended" for e in events):
            op_status = "offline"
            events.append(
                ProtocolEvent(
                    kind="suspended",
                    severity="critical",
                    message=f"Account status is {status_raw or 'Offline'} — operational risk.",
                    confirmed=True,
                )
            )

        if health.state == "missing":
            op_status = "key_missing"
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message=health.message,
                    confirmed=True,
                )
            )
        elif health.state == "expired":
            op_status = "key_expired"
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="critical",
                    message=health.message,
                    confirmed=True,
                )
            )
        elif health.state == "expiring":
            op_status = "key_expiring" if op_status == "online" else op_status
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=health.message,
                    confirmed=True,
                )
            )

        # Heartbeat safety: online accounts should keep heartbeat near head.
        heartbeat_gap = (
            consensus.head_slot - last_heartbeat
            if last_heartbeat > 0
            else None
        )
        if (
            online
            and heartbeat_gap is not None
            and heartbeat_gap > settings.alert_algorand_heartbeat_gap_rounds
        ):
            events.append(
                ProtocolEvent(
                    kind="other",
                    severity="warning",
                    message=(
                        f"Last heartbeat {heartbeat_gap} rounds behind head "
                        f"(threshold {settings.alert_algorand_heartbeat_gap_rounds})."
                    ),
                    confirmed=True,
                )
            )

        effectiveness = algorand_effectiveness(
            online=online,
            incentive_eligible=eligible,
            partkey_state=health.state,
            activity_advancing=activity_advancing,
        )
        risk = compute_slashing_risk_score(
            consecutive_missed_primary_duties=0 if online else 15,
            missed_secondary_duties=0,
            clock_drift_ms=infrastructure.clock_drift_ms,
            syncing=consensus.syncing,
            peer_count=max(consensus.connected_peers, 1),
            effectiveness_score=effectiveness,
        )
        if not online or health.state in {"missing", "expired"}:
            risk = 100.0
        elif health.state == "expiring":
            risk = max(risk, 60.0)
        elif prev_eligible is True and not eligible:
            risk = max(risk, 95.0)

        return ValidatorStats(
            index=index,
            operator_id=address,
            operator_index=index,
            pubkey=address,
            status=op_status,
            balance_base_units=amount,
            effective_balance_base_units=amount,
            attestations=AttestationStats(
                expected=0,
                successful=votes_obs,
                missed=0,
                late=0,
            ),
            proposals=ProposalStats(
                expected=0,
                successful=proposals_obs,
                missed=0,
            ),
            duties=[
                DutyStats(
                    category="attestation",
                    label="Observed votes",
                    expected=None,
                    successful=votes_obs,
                    missed=0,
                    late=0,
                    weight=0.7,
                ),
                DutyStats(
                    category="block",
                    label="Observed proposals",
                    expected=None,
                    successful=proposals_obs,
                    missed=0,
                    late=0,
                    weight=0.3,
                ),
            ],
            rewards_base_units=rewards,
            effectiveness_score=effectiveness,
            risk_score=risk,
            risk_kind="suspension",
            protocol_events=events,
        )

    def _bump_observed(
        self,
        totals: dict[str, int],
        last_rounds: dict[str, int],
        address: str,
        observed_round: int,
    ) -> int:
        prev = last_rounds.get(address, 0)
        if observed_round > prev:
            totals[address] = totals.get(address, 0) + 1
            last_rounds[address] = observed_round
        return totals.get(address, 0)

    def _failed_operator(
        self,
        index: int,
        address: str,
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
            operator_id=address,
            operator_index=index,
            pubkey=address,
            status="unreachable",
            balance_base_units=0,
            effective_balance_base_units=0,
            attestations=AttestationStats(expected=0, successful=0, missed=0, late=0),
            proposals=ProposalStats(expected=0, successful=0, missed=0),
            duties=[
                DutyStats(
                    category="attestation",
                    label="Observed votes",
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
            risk_kind="suspension",
            protocol_events=[
                ProtocolEvent(
                    kind="rpc_error",
                    severity="warning",
                    message=error,
                    confirmed=False,
                )
            ],
        )
