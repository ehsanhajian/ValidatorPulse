from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PoolEpochCounters:
    epoch: int
    successful: int
    failed: int
    validator_index: int | None


class EpochProposalStore:
    """Per-pool epoch proposal totals with monotonic same-epoch merges.

    Aptos view functions return epoch-to-date counters. Within an epoch we
    refuse regressions (no negative deltas). On epoch change we replace
    wholesale and re-bind validator_index.
    """

    def __init__(self) -> None:
        self._by_pool: dict[str, PoolEpochCounters] = {}

    def observe(
        self,
        pool_address: str,
        *,
        epoch: int,
        successful: int,
        failed: int,
        validator_index: int | None,
    ) -> tuple[PoolEpochCounters, bool]:
        key = pool_address.strip().lower()
        incoming = PoolEpochCounters(
            epoch=epoch,
            successful=max(0, successful),
            failed=max(0, failed),
            validator_index=validator_index,
        )
        prev = self._by_pool.get(key)
        if prev is None or prev.epoch != epoch:
            self._by_pool[key] = incoming
            return incoming, True

        merged = PoolEpochCounters(
            epoch=epoch,
            successful=max(prev.successful, incoming.successful),
            failed=max(prev.failed, incoming.failed),
            validator_index=(
                incoming.validator_index
                if incoming.validator_index is not None
                else prev.validator_index
            ),
        )
        self._by_pool[key] = merged
        return merged, False

    def clear(self) -> None:
        self._by_pool.clear()
