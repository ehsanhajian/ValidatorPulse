from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProposalCounters:
    authored: int
    missed: int


@dataclass
class ProposalDeltas:
    authored: int
    missed: int
    reset: bool


class EpochSetStore:
    """Track consensus-set membership across epochs without assuming set size."""

    def __init__(self) -> None:
        self.epoch: int | None = None
        self.ids: frozenset[int] = frozenset()

    def observe(self, epoch: int, ids: list[int]) -> tuple[frozenset[int], frozenset[int], bool]:
        incoming = frozenset(ids)
        prev = self.ids
        reset = self.epoch is None or self.epoch != epoch
        joined = incoming - prev if not reset else frozenset()
        left = prev - incoming if not reset else frozenset()
        self.epoch = epoch
        self.ids = incoming
        return joined, left, reset


class ProposalDeltaStore:
    """Monotonic poll-to-poll proposal counters from local ledger evidence."""

    def __init__(self) -> None:
        self._prev: dict[int, ProposalCounters] = {}

    def observe(self, val_id: int, *, authored: int, missed: int) -> ProposalDeltas:
        incoming = ProposalCounters(authored=max(0, authored), missed=max(0, missed))
        prev = self._prev.get(val_id)
        if prev is None:
            self._prev[val_id] = incoming
            return ProposalDeltas(
                authored=incoming.authored, missed=incoming.missed, reset=True
            )
        if incoming.authored < prev.authored or incoming.missed < prev.missed:
            self._prev[val_id] = incoming
            return ProposalDeltas(
                authored=incoming.authored, missed=incoming.missed, reset=True
            )
        deltas = ProposalDeltas(
            authored=incoming.authored - prev.authored,
            missed=incoming.missed - prev.missed,
            reset=False,
        )
        self._prev[val_id] = incoming
        return deltas


def monad_effectiveness(
    *,
    in_consensus_set: bool,
    eligible: bool,
    local_evidence: bool,
    authored: int,
    missed: int,
    lagging: bool,
) -> float:
    if not in_consensus_set:
        return 0.0
    score = 40.0
    if eligible:
        score += 20.0
    if not lagging:
        score += 15.0
    if local_evidence:
        total = authored + missed
        if total > 0:
            score += (authored / total) * 25.0
        else:
            score += 10.0
    else:
        score += 15.0
    return round(min(100.0, max(0.0, score)), 1)
