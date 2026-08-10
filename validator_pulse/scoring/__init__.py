from __future__ import annotations

from validator_pulse.models import FleetMetrics, ValidatorStats
from validator_pulse.scoring.effectiveness import compute_effectiveness_score
from validator_pulse.scoring.slashing_risk import compute_slashing_risk_score

# Prefer the generic name; keep the Eth-era alias for callers and tests.
compute_risk_score = compute_slashing_risk_score

__all__ = [
    "aggregate_fleet_metrics",
    "compute_effectiveness_score",
    "compute_risk_score",
    "compute_slashing_risk_score",
]


def _missed_primary_duties(operator: ValidatorStats) -> int:
    if operator.duties:
        primary = max(operator.duties, key=lambda d: d.weight)
        return primary.missed
    return operator.attestations.missed


def aggregate_fleet_metrics(validators: list[ValidatorStats]) -> FleetMetrics:
    if not validators:
        return FleetMetrics(
            validator_effectiveness_score=0.0,
            validator_missed_attestations_total=0,
            validator_slashing_risk_score=0.0,
        )

    effectiveness = sum(v.effectiveness_score for v in validators) / len(validators)
    risk = sum((v.risk_score if v.risk_score is not None else v.slashing_risk_score) for v in validators) / len(
        validators
    )
    missed = sum(_missed_primary_duties(v) for v in validators)

    return FleetMetrics(
        validator_effectiveness_score=round(effectiveness, 1),
        validator_missed_attestations_total=missed,
        validator_slashing_risk_score=round(risk, 1),
        effectiveness_score=round(effectiveness, 1),
        missed_primary_duties_total=missed,
        risk_score=round(risk, 1),
    )
