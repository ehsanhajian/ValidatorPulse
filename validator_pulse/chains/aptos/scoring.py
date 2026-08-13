from __future__ import annotations

from typing import Any, Literal

# 0x1::stake validator status codes
VALIDATOR_STATUS_PENDING_ACTIVE = 1
VALIDATOR_STATUS_ACTIVE = 2
VALIDATOR_STATUS_PENDING_INACTIVE = 3
VALIDATOR_STATUS_INACTIVE = 4

ValidatorSetStatus = Literal[
    "pending_active",
    "active",
    "pending_inactive",
    "inactive",
    "unknown",
]


def status_from_code(code: int) -> ValidatorSetStatus:
    if code == VALIDATOR_STATUS_PENDING_ACTIVE:
        return "pending_active"
    if code == VALIDATOR_STATUS_ACTIVE:
        return "active"
    if code == VALIDATOR_STATUS_PENDING_INACTIVE:
        return "pending_inactive"
    if code == VALIDATOR_STATUS_INACTIVE:
        return "inactive"
    return "unknown"


def in_current_set(status: ValidatorSetStatus) -> bool:
    """Active and pending-inactive still participate in the current epoch."""
    return status in {"active", "pending_inactive"}


def aptos_effectiveness(
    *,
    successful: int,
    failed: int,
    in_set: bool,
    syncing: bool,
) -> float:
    """Weight proposal success highest, then sync liveness and set membership."""
    if not in_set:
        return 0.0
    total = max(0, successful) + max(0, failed)
    proposal = 100.0 if total == 0 else (max(0, successful) / total) * 100.0
    liveness = 0.0 if syncing else 100.0
    membership = 100.0
    return round(proposal * 0.70 + liveness * 0.15 + membership * 0.15, 1)


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        if text.lstrip("-").isdigit():
            return int(text)
        try:
            return int(float(text))
        except ValueError:
            return 0
    return int(value)


def parse_view_u64_pair(payload: Any) -> tuple[int, int]:
    if isinstance(payload, list) and len(payload) >= 2:
        return _as_int(payload[0]), _as_int(payload[1])
    return 0, 0


def parse_view_u64(payload: Any) -> int:
    if isinstance(payload, list) and payload:
        return _as_int(payload[0])
    return _as_int(payload)


def parse_stake_tuple(payload: Any) -> tuple[int, int, int, int]:
    """(active, inactive, pending_active, pending_inactive) in octas."""
    if isinstance(payload, list) and len(payload) >= 4:
        return (
            _as_int(payload[0]),
            _as_int(payload[1]),
            _as_int(payload[2]),
            _as_int(payload[3]),
        )
    return 0, 0, 0, 0


def index_active_validators(validator_set: dict[str, Any]) -> dict[str, int]:
    """Map pool address (lower) → validator_index from ValidatorSet resource."""
    out: dict[str, int] = {}
    for entry in validator_set.get("active_validators") or []:
        if not isinstance(entry, dict):
            continue
        addr = str(entry.get("addr") or "").strip().lower()
        if not addr:
            continue
        cfg = entry.get("config") if isinstance(entry.get("config"), dict) else {}
        idx = _as_int(cfg.get("validator_index"))
        out[addr] = idx
    for entry in validator_set.get("pending_inactive") or []:
        if not isinstance(entry, dict):
            continue
        addr = str(entry.get("addr") or "").strip().lower()
        if not addr or addr in out:
            continue
        cfg = entry.get("config") if isinstance(entry.get("config"), dict) else {}
        out[addr] = _as_int(cfg.get("validator_index"))
    return out
