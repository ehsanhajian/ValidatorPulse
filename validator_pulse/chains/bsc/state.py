from __future__ import annotations

from dataclasses import dataclass


class ValidatorSetStore:
    """Track working-set membership across Parlia set changes (no assumed size)."""

    def __init__(self) -> None:
        self.fingerprint: tuple[str, ...] | None = None
        self.addrs: frozenset[str] = frozenset()

    def observe(self, addrs: list[str]) -> tuple[frozenset[str], frozenset[str], bool]:
        incoming = frozenset(a.lower() for a in addrs)
        fingerprint = tuple(sorted(incoming))
        prev = self.addrs
        first = self.fingerprint is None
        joined = incoming - prev if not first else frozenset()
        left = prev - incoming if not first else frozenset()
        self.fingerprint = fingerprint
        self.addrs = incoming
        return joined, left, first


@dataclass(frozen=True)
class SlashThresholds:
    misdemeanor: int
    felony: int
    source: str  # "contract" | "config"

    def label(self) -> str:
        return (
            f"misdemeanor {self.misdemeanor} / felony {self.felony} "
            f"(source={self.source})"
        )


def bsc_effectiveness(
    *,
    in_working_set: bool,
    jailed: bool,
    maintaining: bool,
    slash_count: int,
    misdemeanor: int,
    double_sign: bool,
    malicious_vote: bool,
) -> float:
    if double_sign or malicious_vote or jailed:
        return 0.0
    if not in_working_set and not maintaining:
        return 0.0
    score = 35.0
    if in_working_set:
        score += 25.0
    if not maintaining:
        score += 15.0
    if misdemeanor > 0:
        ratio = min(1.0, slash_count / misdemeanor)
        score += (1.0 - ratio) * 25.0
    else:
        score += 15.0
    return round(min(100.0, max(0.0, score)), 1)
