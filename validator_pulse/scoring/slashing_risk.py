from __future__ import annotations


def compute_slashing_risk_score(
    *,
    consecutive_missed_attestations: int,
    missed_proposals: int,
    clock_drift_ms: float,
    syncing: bool,
    peer_count: int,
    effectiveness_score: float,
) -> float:
    """0–100 risk score. Does not scan external security surfaces."""
    risk = 0.0
    risk += min(35.0, consecutive_missed_attestations * 8)
    risk += min(20.0, missed_proposals * 10)

    if clock_drift_ms > 1000:
        risk += 30
    elif clock_drift_ms > 500:
        risk += 18
    elif clock_drift_ms > 200:
        risk += 8

    if syncing:
        risk += 15
    if peer_count < 5:
        risk += 12
    elif peer_count < 20:
        risk += 5

    if effectiveness_score < 80:
        risk += 15
    elif effectiveness_score < 95:
        risk += 6

    return round(min(100.0, max(0.0, risk)), 1)
