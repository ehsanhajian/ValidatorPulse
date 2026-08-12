from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RightKind = Literal["bake", "attest"]
RightStatus = Literal["pending", "success", "missed"]


@dataclass
class BakerRight:
    kind: RightKind
    level: int
    round: int
    cycle: int | None = None
    status: RightStatus = "pending"


@dataclass
class RightsWindow:
    """Tracks delegate rights across polls with reorg-safe head progression."""

    head_level: int = 0
    head_hash: str = ""
    rights: dict[tuple[RightKind, int, int], BakerRight] = field(default_factory=dict)

    def observe_head(self, level: int, block_hash: str) -> bool:
        """Return True when a reorganization is detected (level/hash regression)."""
        reorg = False
        if self.head_hash and block_hash != self.head_hash:
            if level < self.head_level:
                reorg = True
                # Drop pending rights at or above the new head (canonicality reset).
                self.rights = {
                    key: right
                    for key, right in self.rights.items()
                    if right.level < level
                }
        self.head_level = level
        self.head_hash = block_hash
        return reorg

    def ingest_baking_rights(self, payload: Any, *, head_level: int) -> int:
        count = 0
        for entry in _flatten_rights(payload):
            level = _as_int(entry.get("level"))
            if level is None:
                continue
            rnd = _as_int(entry.get("round")) or 0
            cycle = _as_int(entry.get("cycle"))
            key = ("bake", level, rnd)
            self.rights[key] = BakerRight(
                kind="bake",
                level=level,
                round=rnd,
                cycle=cycle,
                status="pending",
            )
            count += 1
        return count

    def ingest_attestation_rights(self, payload: Any, *, head_level: int) -> int:
        count = 0
        for group in _flatten_rights(payload):
            if isinstance(group, dict) and "rights" in group:
                delegate_rights = group.get("rights") or []
                for entry in delegate_rights:
                    level = _as_int(group.get("level") or entry.get("level"))
                    if level is None:
                        continue
                    rnd = _as_int(entry.get("first_slot") or entry.get("slot") or 0) or 0
                    cycle = _as_int(entry.get("cycle"))
                    key = ("attest", level, rnd)
                    self.rights[key] = BakerRight(
                        kind="attest",
                        level=level,
                        round=rnd,
                        cycle=cycle,
                        status="pending",
                    )
                    count += 1
            else:
                level = _as_int(group.get("level"))
                if level is None:
                    continue
                rnd = _as_int(group.get("first_slot") or group.get("slot") or 0) or 0
                cycle = _as_int(group.get("cycle"))
                key = ("attest", level, rnd)
                self.rights[key] = BakerRight(
                    kind="attest",
                    level=level,
                    round=rnd,
                    cycle=cycle,
                    status="pending",
                )
                count += 1
        return count

    def _close_past_rights(self, head_level: int) -> None:
        """Mark stale pending rights below the current head as missed."""
        for right in self.rights.values():
            if right.level < head_level and right.status == "pending":
                right.status = "missed"

    def apply_participation(
        self,
        *,
        missed_slots: int,
        missed_levels: int,
        expected_activity: int,
    ) -> None:
        """Reconcile attestation misses from chain participation counters."""
        attest_rights = [r for r in self.rights.values() if r.kind == "attest"]
        attest_rights.sort(key=lambda r: (r.level, r.round))
        missed_left = max(0, missed_slots)
        for right in attest_rights:
            if right.status == "pending" and right.level < self.head_level:
                if missed_left > 0:
                    right.status = "missed"
                    missed_left -= 1
                else:
                    right.status = "success"
        bake_rights = [r for r in self.rights.values() if r.kind == "bake" and r.level < self.head_level]
        missed_bakes = max(0, missed_levels)
        for right in sorted(bake_rights, key=lambda r: (r.level, r.round), reverse=True):
            if missed_bakes > 0:
                right.status = "missed"
                missed_bakes -= 1
            else:
                right.status = "success"
        del expected_activity  # participation expected used directly in adapter

    def totals(self) -> dict[str, int]:
        bake = [r for r in self.rights.values() if r.kind == "bake"]
        attest = [r for r in self.rights.values() if r.kind == "attest"]
        return {
            "bake_expected": len(bake),
            "bake_success": sum(1 for r in bake if r.status == "success"),
            "bake_missed": sum(1 for r in bake if r.status == "missed"),
            "bake_pending": sum(1 for r in bake if r.status == "pending"),
            "attest_expected": len(attest),
            "attest_success": sum(1 for r in attest if r.status == "success"),
            "attest_missed": sum(1 for r in attest if r.status == "missed"),
            "attest_pending": sum(1 for r in attest if r.status == "pending"),
        }


def _flatten_rights(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], list):
            return [x for x in payload["data"] if isinstance(x, dict)]
        return [payload]
    return []


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
