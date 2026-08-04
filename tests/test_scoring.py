from __future__ import annotations

from validator_pulse.scoring import compute_effectiveness_score, compute_slashing_risk_score


def test_effectiveness_perfect() -> None:
    assert (
        compute_effectiveness_score(
            attestations_expected=32,
            attestations_successful=32,
            attestations_late=0,
            proposals_expected=0,
            proposals_successful=0,
        )
        == 100
    )


def test_effectiveness_late_partial_credit() -> None:
    score = compute_effectiveness_score(
        attestations_expected=10,
        attestations_successful=8,
        attestations_late=2,
        proposals_expected=0,
        proposals_successful=0,
    )
    assert score == 90


def test_slashing_risk_healthy() -> None:
    score = compute_slashing_risk_score(
        consecutive_missed_attestations=0,
        missed_proposals=0,
        clock_drift_ms=10,
        syncing=False,
        peer_count=50,
        effectiveness_score=99,
    )
    assert score < 10


def test_slashing_risk_elevated() -> None:
    score = compute_slashing_risk_score(
        consecutive_missed_attestations=4,
        missed_proposals=1,
        clock_drift_ms=1200,
        syncing=True,
        peer_count=2,
        effectiveness_score=70,
    )
    assert score >= 70
