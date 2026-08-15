from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


PRE_HELICON_UPTIME_PCT = 80.0
HELICON_UPTIME_PCT = 90.0


@dataclass(frozen=True)
class UptimeThreshold:
    percent: float
    source: str

    def label(self) -> str:
        return f"{self.percent:.0f}% (source={self.source})"


@dataclass(frozen=True)
class RecoveryRunway:
    possible: bool
    slack_seconds: float
    max_final_pct: float
    remaining_seconds: float
    elapsed_seconds: float


def parse_uptime_percent(value: object) -> float | None:
    """Normalize P-Chain ratio (0–1) or percent (0–100) to 0–100."""
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number < 0:
        return 0.0
    if number <= 1.5:
        return round(number * 100.0, 4)
    return round(min(100.0, number), 4)


def parse_rfc3339(value: str | None) -> float | None:
    if not value or not str(value).strip():
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def resolve_uptime_threshold(
    *,
    start_time: int,
    helicon_ts: float | None,
    override: float | None,
) -> UptimeThreshold:
    """ACP-267: 90% after Helicon for new periods; 80% before. Never a single hard-code."""
    if override is not None:
        return UptimeThreshold(percent=float(override), source="config")
    if helicon_ts is None:
        return UptimeThreshold(
            percent=PRE_HELICON_UPTIME_PCT, source="network-default-pre-helicon"
        )
    if start_time >= helicon_ts:
        return UptimeThreshold(percent=HELICON_UPTIME_PCT, source="helicon-upgrade")
    return UptimeThreshold(percent=PRE_HELICON_UPTIME_PCT, source="pre-helicon")


def recovery_runway(
    *,
    uptime_pct: float,
    start_time: int,
    end_time: int,
    now: float,
    requirement_pct: float,
) -> RecoveryRunway:
    """Seconds of remaining downtime still allowed while hitting the uptime requirement."""
    elapsed = max(0.0, now - float(start_time))
    remaining = max(0.0, float(end_time) - now)
    total = elapsed + remaining
    if total <= 0:
        return RecoveryRunway(
            possible=False,
            slack_seconds=0.0,
            max_final_pct=uptime_pct,
            remaining_seconds=0.0,
            elapsed_seconds=elapsed,
        )
    uptime_frac = max(0.0, min(1.0, uptime_pct / 100.0))
    req = max(0.0, min(1.0, requirement_pct / 100.0))
    max_final = (uptime_frac * elapsed + remaining) / total
    needed_online = req * total - uptime_frac * elapsed
    slack = remaining - needed_online
    return RecoveryRunway(
        possible=max_final + 1e-12 >= req,
        slack_seconds=slack,
        max_final_pct=round(max_final * 100.0, 4),
        remaining_seconds=remaining,
        elapsed_seconds=elapsed,
    )


def avalanche_effectiveness(
    *,
    in_set: bool,
    uptime_pct: float | None,
    requirement_pct: float,
    connected: bool,
    rewarding_stake_pct: float | None,
    poll_success_ratio: float | None,
    recovery: RecoveryRunway | None,
) -> float:
    if not in_set:
        return 0.0
    score = 25.0
    if connected:
        score += 10.0
    if uptime_pct is not None and requirement_pct > 0:
        score += min(40.0, (uptime_pct / requirement_pct) * 40.0)
    else:
        score += 15.0
    if rewarding_stake_pct is not None:
        score += min(15.0, rewarding_stake_pct / 100.0 * 15.0)
    elif poll_success_ratio is not None:
        score += poll_success_ratio * 15.0
    else:
        score += 8.0
    if recovery is not None and not recovery.possible:
        score = min(score, 20.0)
    return round(min(100.0, max(0.0, score)), 1)


def now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()
