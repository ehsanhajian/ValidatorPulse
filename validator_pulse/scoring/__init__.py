from __future__ import annotations

from validator_pulse.models import FleetMetrics, ValidatorStats
from validator_pulse.scoring.effectiveness import compute_effectiveness_score
from validator_pulse.scoring.slashing_risk import compute_slashing_risk_score

__all__ = [
    "aggregate_fleet_metrics",
    "compute_effectiveness_score",
    "compute_slashing_risk_score",
]


def aggregate_fleet_metrics(validators: list[ValidatorStats]) -> FleetMetrics:
    if not validators:
        return FleetMetrics(
            validator_effectiveness_score=0.0,
            validator_missed_attestations_total=0,
            validator_slashing_risk_score=0.0,
        )

    effectiveness = sum(v.effectiveness_score for v in validators) / len(validators)
    slashing_risk = sum(v.slashing_risk_score for v in validators) / len(validators)
    missed = sum(v.attestations.missed for v in validators)

    return FleetMetrics(
        validator_effectiveness_score=round(effectiveness, 1),
        validator_missed_attestations_total=missed,
        validator_slashing_risk_score=round(slashing_risk, 1),
    )
