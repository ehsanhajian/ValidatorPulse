from __future__ import annotations


def compute_effectiveness_score(
    *,
    attestations_expected: int,
    attestations_successful: int,
    attestations_late: int,
    proposals_expected: int,
    proposals_successful: int,
) -> float:
    """0–100 score; late attestations get partial credit."""
    att_weight = 0.85
    prop_weight = 0.15

    if attestations_expected == 0:
        att_score = 100.0
    else:
        att_score = (
            (attestations_successful + attestations_late * 0.5) / attestations_expected
        ) * 100

    if proposals_expected == 0:
        prop_score = 100.0
    else:
        prop_score = (proposals_successful / proposals_expected) * 100

    if proposals_expected > 0:
        score = att_score * att_weight + prop_score * prop_weight
    else:
        score = att_score

    return round(min(100.0, max(0.0, score)), 1)
