from __future__ import annotations


def compute_slashing_risk_score(
    *,
    consecutive_missed_attestations: int = 0,
    consecutive_missed_primary_duties: int | None = None,
    missed_proposals: int = 0,
    missed_secondary_duties: int | None = None,
    clock_drift_ms: float,
    syncing: bool,
    peer_count: int,
    effectiveness_score: float,
) -> float:
    """0–100 operational/penalty risk score. Does not scan external security surfaces.

    Parameter names keep Eth attestation/proposal aliases; prefer
    ``consecutive_missed_primary_duties`` / ``missed_secondary_duties`` for
    heterogeneous chains.
    """
    missed_primary = (
        consecutive_missed_attestations
        if consecutive_missed_primary_duties is None
        else consecutive_missed_primary_duties
    )
    missed_secondary = (
        missed_proposals if missed_secondary_duties is None else missed_secondary_duties
    )

    risk = 0.0
    risk += min(35.0, missed_primary * 8)
    risk += min(20.0, missed_secondary * 10)

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
