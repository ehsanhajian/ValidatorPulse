"""Live Ethereum attestation / proposal duty history across poll cycles."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

from validator_pulse.collectors.beacon import (
    SLOTS_PER_EPOCH,
    check_block_at_slot,
    fetch_attestation_rewards,
    fetch_attester_duties,
    fetch_proposer_duties,
)
from validator_pulse.models import (
    AttestationDuty,
    AttestationStats,
    DutyOutcome,
    ProposalDuty,
    ProposalStats,
)

# Match demo window size for comparable effectiveness scoring.
ATTESTATION_WINDOW = 32
PROPOSAL_WINDOW = 64
LOOKBACK_EPOCHS = 4
RECENT_UI = 8
# Inclusion delay >= this counts as late (demo uses delay 2+).
LATE_INCLUSION_DELAY = 2


@dataclass
class ValidatorDutyView:
    attestations: AttestationStats
    proposals: ProposalStats
    recent_attestations: list[AttestationDuty]
    recent_proposals: list[ProposalDuty]
    consecutive_missed_attestations: int
    duty_rewards_gwei: int


@dataclass
class DutyHistoryStore:
    """In-process duty history that persists across live poll cycles."""

    _attestations: dict[int, dict[int, AttestationDuty]] = field(default_factory=dict)
    _proposals: dict[int, dict[int, ProposalDuty]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def clear(self) -> None:
        with self._lock:
            self._attestations.clear()
            self._proposals.clear()

    def upsert_attestation(self, duty: AttestationDuty) -> None:
        with self._lock:
            by_epoch = self._attestations.setdefault(duty.validator_index, {})
            existing = by_epoch.get(duty.epoch)
            # Never downgrade a resolved outcome back to pending.
            if (
                existing
                and existing.outcome != "pending"
                and duty.outcome == "pending"
            ):
                return
            by_epoch[duty.epoch] = duty

    def upsert_proposal(self, duty: ProposalDuty) -> None:
        with self._lock:
            by_slot = self._proposals.setdefault(duty.validator_index, {})
            existing = by_slot.get(duty.slot)
            if (
                existing
                and existing.outcome != "pending"
                and duty.outcome == "pending"
            ):
                return
            by_slot[duty.slot] = duty

    def prune(self, head_epoch: int) -> None:
        min_epoch = head_epoch - ATTESTATION_WINDOW + 1
        min_slot = max(0, (head_epoch - PROPOSAL_WINDOW) * SLOTS_PER_EPOCH)
        with self._lock:
            for index, by_epoch in list(self._attestations.items()):
                self._attestations[index] = {
                    epoch: duty
                    for epoch, duty in by_epoch.items()
                    if epoch >= min_epoch
                }
            for index, by_slot in list(self._proposals.items()):
                self._proposals[index] = {
                    slot: duty for slot, duty in by_slot.items() if slot >= min_slot
                }

    def view_for(self, validator_index: int) -> ValidatorDutyView:
        with self._lock:
            att_map = dict(self._attestations.get(validator_index, {}))
            prop_map = dict(self._proposals.get(validator_index, {}))

        attestations = sorted(
            att_map.values(), key=lambda d: d.epoch, reverse=True
        )[:ATTESTATION_WINDOW]
        proposals = sorted(prop_map.values(), key=lambda d: d.slot, reverse=True)[
            :PROPOSAL_WINDOW
        ]

        successful = missed = late = pending = 0
        duty_rewards = 0
        for duty in attestations:
            if duty.outcome == "success":
                successful += 1
            elif duty.outcome == "missed":
                missed += 1
            elif duty.outcome == "late":
                late += 1
            else:
                pending += 1
            if duty.reward_gwei:
                duty_rewards += duty.reward_gwei

        prop_ok = prop_miss = prop_pending = 0
        for duty in proposals:
            if duty.outcome == "success":
                prop_ok += 1
            elif duty.outcome == "missed":
                prop_miss += 1
            else:
                prop_pending += 1
            if duty.reward_gwei:
                duty_rewards += duty.reward_gwei

        consecutive = 0
        for duty in attestations:
            if duty.outcome == "pending":
                continue
            if duty.outcome == "missed":
                consecutive += 1
                continue
            break

        return ValidatorDutyView(
            attestations=AttestationStats(
                expected=successful + missed + late + pending,
                successful=successful,
                missed=missed,
                late=late,
            ),
            proposals=ProposalStats(
                expected=prop_ok + prop_miss + prop_pending,
                successful=prop_ok,
                missed=prop_miss,
            ),
            recent_attestations=attestations[:RECENT_UI],
            recent_proposals=[p for p in proposals if p.outcome != "pending"][:RECENT_UI],
            consecutive_missed_attestations=consecutive,
            duty_rewards_gwei=duty_rewards,
        )


_STORE = DutyHistoryStore()


def get_duty_store() -> DutyHistoryStore:
    return _STORE


def reset_duty_store() -> None:
    """Test helper — clear process-wide live duty history."""
    _STORE.clear()


def _outcome_from_rewards(entry: dict) -> tuple[DutyOutcome, int | None, int]:
    source = int(entry.get("source") or 0)
    target = int(entry.get("target") or 0)
    head = int(entry.get("head") or 0)
    raw_delay = entry.get("inclusion_delay")
    delay = int(raw_delay) if raw_delay not in (None, "") else None
    reward = max(0, source + target + head)
    if source == 0 and target == 0 and head == 0:
        return "missed", delay, 0
    if delay is not None and delay >= LATE_INCLUSION_DELAY:
        return "late", delay, reward
    return "success", delay, reward


async def refresh_live_duties(
    beacon_api_url: str,
    validator_indices: list[int],
    *,
    head_slot: int,
    store: DutyHistoryStore | None = None,
) -> None:
    """Fetch recent duties from the Beacon API and merge into the history store."""
    if not validator_indices or head_slot <= 0:
        return

    store = store or get_duty_store()
    head_epoch = head_slot // SLOTS_PER_EPOCH
    start_epoch = max(0, head_epoch - LOOKBACK_EPOCHS + 1)

    for epoch in range(start_epoch, head_epoch + 1):
        attester_duties = await fetch_attester_duties(
            beacon_api_url, epoch, validator_indices
        )
        rewards_by_index: dict[int, dict] | None = None
        epoch_complete = head_epoch > epoch
        if epoch_complete:
            rewards_by_index = await fetch_attestation_rewards(
                beacon_api_url, epoch, validator_indices
            )

        for duty in attester_duties:
            index = int(duty["validator_index"])
            slot = int(duty["slot"])
            outcome: DutyOutcome = "pending"
            delay: int | None = None
            reward: int | None = None

            if epoch_complete and rewards_by_index is not None:
                entry = rewards_by_index.get(index)
                if entry is None:
                    outcome = "missed"
                    reward = 0
                else:
                    outcome, delay, reward = _outcome_from_rewards(entry)
            elif epoch_complete and rewards_by_index is None:
                # Rewards endpoint unavailable — leave pending rather than inventing.
                outcome = "pending"

            store.upsert_attestation(
                AttestationDuty(
                    epoch=epoch,
                    slot=slot,
                    validator_index=index,
                    outcome=outcome,
                    inclusion_delay=delay,
                    reward_gwei=reward,
                )
            )

        proposer_duties = await fetch_proposer_duties(beacon_api_url, epoch)
        for duty in proposer_duties:
            index = int(duty["validator_index"])
            if index not in validator_indices:
                continue
            slot = int(duty["slot"])
            if slot >= head_slot:
                outcome = "pending"
            else:
                proposed = await check_block_at_slot(beacon_api_url, slot)
                if proposed is None:
                    outcome = "pending"
                else:
                    outcome = "success" if proposed else "missed"
            store.upsert_proposal(
                ProposalDuty(
                    epoch=epoch,
                    slot=slot,
                    validator_index=index,
                    outcome=outcome,
                    reward_gwei=None,
                )
            )

    store.prune(head_epoch)


def effectiveness_inputs(view: ValidatorDutyView) -> tuple[int, int, int, int, int]:
    """Exclude pending duties from the effectiveness denominator."""
    att = view.attestations
    resolved_att = att.successful + att.missed + att.late
    prop = view.proposals
    resolved_prop = prop.successful + prop.missed
    return (
        resolved_att,
        att.successful,
        att.late,
        resolved_prop,
        prop.successful,
    )
