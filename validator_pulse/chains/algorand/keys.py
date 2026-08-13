from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PartKeyState = Literal["missing", "expired", "expiring", "valid"]


@dataclass(frozen=True)
class ParticipationKeyView:
    address: str
    vote_first_valid: int
    vote_last_valid: int
    effective_first_valid: int | None
    effective_last_valid: int | None
    last_vote: int
    last_proposal: int
    last_state_proof: int
    key_id: str | None = None


@dataclass(frozen=True)
class PartKeyHealth:
    state: PartKeyState
    rounds_remaining: int | None
    key: ParticipationKeyView | None
    message: str


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return 0


def parse_participation_keys(payload: Any) -> list[ParticipationKeyView]:
    if not isinstance(payload, list):
        return []
    out: list[ParticipationKeyView] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        address = str(entry.get("address") or "").strip()
        if not address:
            continue
        key_obj = entry.get("key") if isinstance(entry.get("key"), dict) else {}
        vote_first = _as_int(key_obj.get("vote-first-valid") or entry.get("vote-first-valid"))
        vote_last = _as_int(key_obj.get("vote-last-valid") or entry.get("vote-last-valid"))
        out.append(
            ParticipationKeyView(
                address=address,
                vote_first_valid=vote_first,
                vote_last_valid=vote_last,
                effective_first_valid=_as_int(entry.get("effective-first-valid")) or None,
                effective_last_valid=_as_int(entry.get("effective-last-valid")) or None,
                last_vote=_as_int(entry.get("last-vote")),
                last_proposal=_as_int(entry.get("last-block-proposal")),
                last_state_proof=_as_int(entry.get("last-state-proof")),
                key_id=str(entry.get("id") or "") or None,
            )
        )
    return out


def keys_for_address(
    keys: list[ParticipationKeyView],
    address: str,
) -> list[ParticipationKeyView]:
    target = address.strip()
    return [k for k in keys if k.address == target]


def evaluate_partkey_health(
    keys: list[ParticipationKeyView],
    *,
    current_round: int,
    warning_rounds: int,
) -> PartKeyHealth:
    if not keys:
        return PartKeyHealth(
            state="missing",
            rounds_remaining=None,
            key=None,
            message="No participation keys found for account on this algod.",
        )

    # Prefer the key covering the current round; else the one with latest last-valid.
    covering = [
        k
        for k in keys
        if k.vote_first_valid <= current_round <= k.vote_last_valid
    ]
    if covering:
        best = max(covering, key=lambda k: k.vote_last_valid)
    else:
        best = max(keys, key=lambda k: k.vote_last_valid)

    remaining = best.vote_last_valid - current_round
    if remaining < 0:
        return PartKeyHealth(
            state="expired",
            rounds_remaining=remaining,
            key=best,
            message=(
                f"Participation key expired {abs(remaining)} rounds ago "
                f"(valid through round {best.vote_last_valid})."
            ),
        )
    if remaining <= max(0, warning_rounds):
        return PartKeyHealth(
            state="expiring",
            rounds_remaining=remaining,
            key=best,
            message=(
                f"Participation key expires in {remaining} rounds "
                f"(warning threshold {warning_rounds})."
            ),
        )
    return PartKeyHealth(
        state="valid",
        rounds_remaining=remaining,
        key=best,
        message=f"Participation key valid for {remaining} more rounds.",
    )


def algorand_effectiveness(
    *,
    online: bool,
    incentive_eligible: bool,
    partkey_state: PartKeyState,
    activity_advancing: bool,
) -> float:
    """Score from observable health — never from inferred missed committee duties."""
    if not online or partkey_state in {"missing", "expired"}:
        return 0.0
    score = 55.0
    if incentive_eligible:
        score += 15.0
    if partkey_state == "valid":
        score += 20.0
    elif partkey_state == "expiring":
        score += 8.0
    if activity_advancing:
        score += 10.0
    return round(min(100.0, max(0.0, score)), 1)
