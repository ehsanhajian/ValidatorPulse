from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AccountEpochCounters:
    epoch_height: int
    expected_blocks: int
    produced_blocks: int
    expected_chunks: int
    produced_chunks: int
    expected_endorsements: int
    produced_endorsements: int


class EpochSnapshotStore:
    """Retain per-account epoch counters so boundaries reset cleanly.

    NEAR RPC already returns epoch-to-date totals. Within an epoch we refuse
    regressions (no negative deltas / double-count glitches). On epoch change
    we replace the snapshot wholesale.
    """

    def __init__(self) -> None:
        self._by_account: dict[str, AccountEpochCounters] = {}

    def observe(
        self,
        account_id: str,
        *,
        epoch_height: int,
        expected_blocks: int,
        produced_blocks: int,
        expected_chunks: int,
        produced_chunks: int,
        expected_endorsements: int,
        produced_endorsements: int,
    ) -> tuple[AccountEpochCounters, bool]:
        incoming = AccountEpochCounters(
            epoch_height=epoch_height,
            expected_blocks=max(0, expected_blocks),
            produced_blocks=max(0, produced_blocks),
            expected_chunks=max(0, expected_chunks),
            produced_chunks=max(0, produced_chunks),
            expected_endorsements=max(0, expected_endorsements),
            produced_endorsements=max(0, produced_endorsements),
        )
        prev = self._by_account.get(account_id)
        if prev is None or prev.epoch_height != epoch_height:
            self._by_account[account_id] = incoming
            return incoming, True

        # Same epoch: counters must be monotonic non-decreasing.
        merged = AccountEpochCounters(
            epoch_height=epoch_height,
            expected_blocks=max(prev.expected_blocks, incoming.expected_blocks),
            produced_blocks=max(prev.produced_blocks, incoming.produced_blocks),
            expected_chunks=max(prev.expected_chunks, incoming.expected_chunks),
            produced_chunks=max(prev.produced_chunks, incoming.produced_chunks),
            expected_endorsements=max(
                prev.expected_endorsements, incoming.expected_endorsements
            ),
            produced_endorsements=max(
                prev.produced_endorsements, incoming.produced_endorsements
            ),
        )
        self._by_account[account_id] = merged
        return merged, False

    def clear(self) -> None:
        self._by_account.clear()
