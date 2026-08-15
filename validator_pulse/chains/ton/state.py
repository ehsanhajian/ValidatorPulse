from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

# Docs: completed-round efficiency below 90% is malfunctioning / fine risk.
# https://docs.ton.org/nodes/cpp/run-validator
DOCS_EFFICIENCY_THRESHOLD = 90.0
# Masterchain validators occupy indices below config16.max_main_validators (100).
MASTERCHAIN_INDEX_LIMIT = 100
# Skip 0%/null efficiency until the round has been running this long.
ROUND_START_GRACE_SECONDS = 2 * 3600
# Keep rotated ADNL observations for two ~18h validation cycles.
HISTORY_TTL_SECONDS = 48 * 3600
HISTORY_MAX_CYCLES = 8
NANOTON = 10**9


def normalize_adnl(value: str | None) -> str:
    text = (value or "").strip().upper()
    if text.startswith("0X"):
        text = text[2:]
    return text


def is_adnl(value: str | None) -> bool:
    key = normalize_adnl(value)
    return len(key) == 64 and all(c in "0123456789ABCDEF" for c in key)


@dataclass(frozen=True)
class EfficiencyThreshold:
    percent: float
    source: str

    def label(self) -> str:
        return f"{self.percent:.0f}% (source={self.source})"


def resolve_efficiency_threshold(override: float | None) -> EfficiencyThreshold:
    if override is not None:
        return EfficiencyThreshold(percent=float(override), source="config")
    return EfficiencyThreshold(percent=DOCS_EFFICIENCY_THRESHOLD, source="docs-efficiency")


def round_is_complete(utime_until: int | None, now: float | None = None) -> bool:
    if not utime_until:
        return False
    return (now or time.time()) >= utime_until


def round_in_grace(utime_since: int | None, now: float | None = None) -> bool:
    if not utime_since:
        return True
    elapsed = (now or time.time()) - utime_since
    return elapsed < ROUND_START_GRACE_SECONDS


def efficiency_is_actionable(
    efficiency: float | None,
    *,
    utime_since: int | None,
    utime_until: int | None,
    now: float | None = None,
) -> bool:
    """Zero/null efficiency at round start is not an immediate miss."""
    if efficiency is None:
        return False
    if not round_is_complete(utime_until, now) and (
        efficiency <= 0 or round_in_grace(utime_since, now)
    ):
        return False
    return True


def completed_round_below_threshold(
    efficiency: float | None,
    threshold: EfficiencyThreshold,
    *,
    utime_until: int | None,
    now: float | None = None,
) -> bool:
    if efficiency is None:
        return False
    if not round_is_complete(utime_until, now):
        return False
    return efficiency < threshold.percent


def ton_effectiveness(
    *,
    in_set: bool,
    efficiency: float | None,
    efficiency_actionable: bool,
    fined: bool,
    missed_election: bool,
    severe_lag: bool,
    recovering: bool,
) -> float:
    if fined:
        return 8.0
    if severe_lag:
        return 25.0
    if missed_election and not in_set:
        return 35.0
    if recovering:
        return 55.0
    if not in_set:
        return 40.0
    if efficiency_actionable and efficiency is not None:
        return round(max(0.0, min(100.0, efficiency)), 1)
    if in_set:
        return 88.0
    return 50.0


def parse_complaint(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float, str)) and str(raw).strip():
        return {"fine": raw, "passed": True}
    if not isinstance(raw, dict):
        return None
    fine = (
        raw.get("fine")
        or raw.get("fine_value")
        or raw.get("value")
        or raw.get("suggested_fine")
        or raw.get("fineValue")
    )
    passed = raw.get("passed")
    if passed is None:
        passed = raw.get("is_passed")
    if passed is None:
        passed = raw.get("approved")
    return {
        "fine": fine,
        "passed": bool(passed) if passed is not None else False,
        "election_id": raw.get("election_id") or raw.get("electionId") or raw.get("id"),
        "hash": raw.get("hash") or raw.get("complaint_hash"),
        "raw": raw,
    }


def complaints_are_critical(complaints: list[dict[str, Any]]) -> bool:
    return any(item.get("passed") or item.get("fine") for item in complaints)


@dataclass(frozen=True)
class AdnlObservation:
    adnl: str
    cycle_id: int
    index: int | None
    efficiency: float | None
    in_set: bool
    stake: int | None
    seen_at: float
    utime_until: int | None = None


class AdnlHistoryStore:
    """Time-bounded ADNL observations so rotation does not drop prior rounds."""

    def __init__(self, ttl_seconds: float = HISTORY_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._by_adnl: dict[str, deque[AdnlObservation]] = defaultdict(
            lambda: deque(maxlen=HISTORY_MAX_CYCLES)
        )

    def record(self, observation: AdnlObservation) -> None:
        key = normalize_adnl(observation.adnl)
        if not key:
            return
        bucket = self._by_adnl[key]
        for existing in bucket:
            if existing.cycle_id == observation.cycle_id:
                bucket.remove(existing)
                break
        bucket.append(observation)
        self._prune(time.time())

    def history_for(self, adnl: str, now: float | None = None) -> list[AdnlObservation]:
        self._prune(now or time.time())
        return list(self._by_adnl.get(normalize_adnl(adnl), ()))

    def tracked_adnls(self, configured: list[str], now: float | None = None) -> list[str]:
        """Configured keys plus recently observed ADNLs still inside the TTL window."""
        self._prune(now or time.time())
        ordered: list[str] = []
        for key in configured:
            norm = normalize_adnl(key)
            if norm and norm not in ordered:
                ordered.append(norm)
        for key in self._by_adnl:
            if key not in ordered:
                ordered.append(key)
        return ordered

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, rows in self._by_adnl.items()
            if not rows or now - rows[-1].seen_at > self.ttl_seconds
        ]
        for key in stale:
            del self._by_adnl[key]
