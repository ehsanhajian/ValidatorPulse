from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DutyCounters:
    proposals: int
    checkpoint: int


@dataclass
class DutyDeltas:
    proposals: int
    checkpoint: int
    reset: bool


class MetricsDeltaStore:
    """Monotonic poll-to-poll deltas for local Prometheus counters."""

    def __init__(self) -> None:
        self._prev: dict[str, DutyCounters] = {}

    def observe(
        self,
        key: str,
        *,
        proposals: int | None,
        checkpoint: int | None,
    ) -> DutyDeltas:
        if proposals is None and checkpoint is None:
            return DutyDeltas(proposals=0, checkpoint=0, reset=False)

        incoming = DutyCounters(
            proposals=max(0, proposals or 0),
            checkpoint=max(0, checkpoint or 0),
        )
        prev = self._prev.get(key)
        if prev is None:
            self._prev[key] = incoming
            return DutyDeltas(
                proposals=incoming.proposals,
                checkpoint=incoming.checkpoint,
                reset=True,
            )

        # Counter reset / restart → treat as new baseline (no negative delta).
        if (
            incoming.proposals < prev.proposals
            or incoming.checkpoint < prev.checkpoint
        ):
            self._prev[key] = incoming
            return DutyDeltas(
                proposals=incoming.proposals,
                checkpoint=incoming.checkpoint,
                reset=True,
            )

        deltas = DutyDeltas(
            proposals=incoming.proposals - prev.proposals,
            checkpoint=incoming.checkpoint - prev.checkpoint,
            reset=False,
        )
        self._prev[key] = incoming
        return deltas

    def clear(self) -> None:
        self._prev.clear()
