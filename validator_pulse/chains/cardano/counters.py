from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CounterSnapshot:
    blocks_forged: int = 0
    slots_missed: int = 0
    leader_opportunities: int = 0
    cannot_forge: int = 0


@dataclass
class CounterDelta:
    forged: int = 0
    missed: int = 0
    opportunities: int = 0
    cannot_forge: int = 0
    reset: bool = False


class CounterSnapshotStore:
    """Track Prometheus counter snapshots to compute poll-to-poll deltas."""

    def __init__(self) -> None:
        self._by_pool: dict[str, CounterSnapshot] = {}

    def observe(
        self,
        pool_id: str,
        *,
        blocks_forged: int,
        slots_missed: int,
        leader_opportunities: int,
        cannot_forge: int,
    ) -> CounterDelta:
        incoming = CounterSnapshot(
            blocks_forged=max(0, blocks_forged),
            slots_missed=max(0, slots_missed),
            leader_opportunities=max(0, leader_opportunities),
            cannot_forge=max(0, cannot_forge),
        )
        prev = self._by_pool.get(pool_id)
        if prev is None:
            self._by_pool[pool_id] = incoming
            return CounterDelta(reset=True)

        def delta(new: int, old: int) -> int:
            if new >= old:
                return new - old
            # Counter reset (epoch boundary / tracer restart).
            return new

        result = CounterDelta(
            forged=delta(incoming.blocks_forged, prev.blocks_forged),
            missed=delta(incoming.slots_missed, prev.slots_missed),
            opportunities=delta(incoming.leader_opportunities, prev.leader_opportunities),
            cannot_forge=delta(incoming.cannot_forge, prev.cannot_forge),
            reset=False,
        )
        self._by_pool[pool_id] = incoming
        return result

    def clear(self) -> None:
        self._by_pool.clear()
