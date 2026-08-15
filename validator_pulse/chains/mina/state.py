from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SlotKind = Literal["canonical", "orphaned", "missed", "pending"]

# Default grace after a won slot before classifying a miss (≈ two 3-minute slots).
DEFAULT_MISS_GRACE_SLOTS = 2


def normalize_public_key(value: str | None) -> str:
    text = (value or "").strip()
    if text.startswith("B62") or text.startswith("b62"):
        return "B62" + text[3:]
    return text


@dataclass(frozen=True)
class WonSlot:
    """Locally observed private VRF win — never inferred from a public API."""

    pubkey: str
    slot: int
    epoch: int | None = None
    produced: bool = False
    state_hash: str | None = None
    source: str = "log"


@dataclass(frozen=True)
class CanonicalBlock:
    slot: int
    height: int
    creator: str
    state_hash: str
    coinbase: int = 0


@dataclass(frozen=True)
class SlotOutcome:
    slot: int
    kind: SlotKind
    epoch: int | None = None
    state_hash: str | None = None
    canonical_creator: str | None = None


@dataclass
class WonSlotStore:
    """Remember locally observed wins across polls (GraphQL frontier is short)."""

    _slots: dict[tuple[str, int], WonSlot] = field(default_factory=dict)

    def observe(self, won: WonSlot) -> None:
        key = (won.pubkey, won.slot)
        existing = self._slots.get(key)
        if existing is None:
            self._slots[key] = won
            return
        produced = existing.produced or won.produced
        state_hash = won.state_hash or existing.state_hash
        epoch = won.epoch if won.epoch is not None else existing.epoch
        source = existing.source if existing.source != "graphql" else won.source
        self._slots[key] = WonSlot(
            pubkey=won.pubkey,
            slot=won.slot,
            epoch=epoch,
            produced=produced,
            state_hash=state_hash,
            source=source,
        )

    def for_key(self, pubkey: str) -> list[WonSlot]:
        target = normalize_public_key(pubkey)
        return sorted(
            (w for w in self._slots.values() if w.pubkey == target),
            key=lambda w: w.slot,
        )


def classify_won_slots(
    *,
    won: list[WonSlot],
    canonical_by_slot: dict[int, CanonicalBlock],
    current_slot: int,
    miss_grace_slots: int = DEFAULT_MISS_GRACE_SLOTS,
) -> list[SlotOutcome]:
    """Map locally observed wins to canonical / orphaned / missed / pending."""
    grace = max(0, miss_grace_slots)
    outcomes: list[SlotOutcome] = []
    for item in won:
        canonical = canonical_by_slot.get(item.slot)
        if item.slot > current_slot:
            kind: SlotKind = "pending"
        elif canonical is not None and canonical.creator == item.pubkey:
            kind = "canonical"
        elif item.produced:
            if canonical is None and item.slot + grace >= current_slot:
                kind = "pending"
            else:
                kind = "orphaned"
        elif item.slot + grace >= current_slot:
            kind = "pending"
        else:
            kind = "missed"
        outcomes.append(
            SlotOutcome(
                slot=item.slot,
                kind=kind,
                epoch=item.epoch,
                state_hash=item.state_hash or (canonical.state_hash if canonical else None),
                canonical_creator=canonical.creator if canonical else None,
            )
        )
    return outcomes


def mina_effectiveness(
    *,
    synced: bool,
    activated: bool,
    outcomes: list[SlotOutcome] | None,
) -> float:
    """Canonical production / locally observed won slots. Never invents expected duties."""
    if not activated:
        return 10.0 if synced else 0.0
    if not outcomes:
        return 55.0 if synced else 15.0
    completed = [o for o in outcomes if o.kind != "pending"]
    if not completed:
        return 90.0 if synced else 25.0
    canonical = sum(1 for o in completed if o.kind == "canonical")
    score = (canonical / len(completed)) * 100.0
    if not synced:
        score = min(score, 40.0)
    orphans = sum(1 for o in completed if o.kind == "orphaned")
    misses = sum(1 for o in completed if o.kind == "missed")
    if orphans:
        score = min(score, 75.0)
    if misses:
        score = min(score, 45.0)
    return round(min(100.0, max(0.0, score)), 1)


def near_slot_unsynced(
    *,
    synced: bool,
    outcomes: list[SlotOutcome],
    current_slot: int,
    near_slots: int,
) -> bool:
    if synced or near_slots < 0:
        return False
    for item in outcomes:
        if item.kind in {"pending", "missed"} and abs(item.slot - current_slot) <= near_slots:
            return True
        if item.kind == "canonical" and current_slot - item.slot <= near_slots:
            return True
    return any(abs(o.slot - current_slot) <= near_slots for o in outcomes)
