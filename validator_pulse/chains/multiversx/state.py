from __future__ import annotations

from dataclasses import dataclass
from typing import Any

META_SHARD_ID = 4_294_967_295
# Displayed rating 0–100. Docs: jail below 10. Also derivable from /network/ratings.
DOCS_JAIL_RATING = 10.0
START_RATING = 50.0


def normalize_bls_key(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return text


def is_bls_key(value: str) -> bool:
    key = normalize_bls_key(value)
    return len(key) == 192 and all(c in "0123456789abcdef" for c in key)


def shard_label(shard_id: int | None) -> str:
    if shard_id is None:
        return "unknown"
    if shard_id == META_SHARD_ID:
        return "metachain"
    return str(shard_id)


def parse_peer_type(value: str | None) -> str:
    text = (value or "").strip().lower()
    return text or "unknown"


def is_jailed(peer_type: str, validator_status: str | None = None) -> bool:
    blob = f"{peer_type} {validator_status or ''}".lower()
    return "jail" in blob


def is_slashed(peer_type: str, validator_status: str | None = None) -> bool:
    blob = f"{peer_type} {validator_status or ''}".lower()
    return "slash" in blob or "blacklisted" in blob


def is_passive_recovery(peer_type: str, validator_status: str | None = None) -> bool:
    blob = f"{peer_type} {validator_status or ''}".lower()
    if is_jailed(peer_type, validator_status) or is_slashed(peer_type, validator_status):
        return False
    return any(token in blob for token in ("waiting", "queued", "new", "inactive"))


@dataclass(frozen=True)
class JailThreshold:
    rating: float
    source: str

    def label(self) -> str:
        return f"{self.rating:.0f} (source={self.source})"


def resolve_jail_threshold(
    *,
    override: float | None,
    ratings_config: dict[str, Any] | None,
) -> JailThreshold:
    if override is not None:
        return JailThreshold(rating=float(override), source="config")
    derived = jail_threshold_from_ratings(ratings_config)
    if derived is not None:
        return JailThreshold(rating=derived, source="network-ratings")
    return JailThreshold(rating=DOCS_JAIL_RATING, source="docs-rating")


def jail_threshold_from_ratings(config: dict[str, Any] | None) -> float | None:
    """Map /network/ratings selection chances to displayed 0–100 jail rating."""
    if not isinstance(config, dict):
        return None
    try:
        max_rating = float(config.get("erd_ratings_general_max_rating") or 0)
    except (TypeError, ValueError):
        return None
    if max_rating <= 0:
        return None
    chances = config.get("erd_ratings_general_selection_chances")
    if not isinstance(chances, list):
        return None
    zeros: list[float] = []
    for item in chances:
        if not isinstance(item, dict):
            continue
        try:
            chance = float(item.get("erd_chance_percent") or 0)
            threshold = float(item.get("erd_max_threshold") or 0)
        except (TypeError, ValueError):
            continue
        if chance == 0 and threshold > 0:
            zeros.append(threshold / max_rating * 100.0)
    if not zeros:
        return None
    return round(min(zeros), 4)


def rating_near_jail(rating: float | None, threshold: JailThreshold, warn_below: float) -> bool:
    if rating is None:
        return False
    return rating <= max(threshold.rating, warn_below)


def success_ratio(ok: int | None, fail: int | None) -> float | None:
    if ok is None and fail is None:
        return None
    total = (ok or 0) + (fail or 0)
    if total <= 0:
        return 1.0 if (fail or 0) == 0 else 0.0
    return (ok or 0) / total


def mx_effectiveness(
    *,
    heartbeat_active: bool | None,
    jailed: bool,
    slashed: bool,
    rating: float | None,
    jail_threshold: float,
    proposal_ratio: float | None,
    signature_ratio: float | None,
    passive: bool,
) -> float:
    if slashed:
        return 0.0
    if jailed:
        return 8.0
    score = 20.0
    if heartbeat_active:
        score += 15.0
    elif heartbeat_active is False:
        score += 0.0
    else:
        score += 8.0
    if rating is not None:
        score += min(25.0, max(0.0, rating / 100.0 * 25.0))
    else:
        score += 12.0
    if proposal_ratio is not None and signature_ratio is not None:
        score += proposal_ratio * 20.0 + signature_ratio * 20.0
    elif signature_ratio is not None:
        score += signature_ratio * 30.0
    elif proposal_ratio is not None:
        score += proposal_ratio * 30.0
    else:
        score += 15.0
    if rating is not None and rating <= jail_threshold:
        score = min(score, 18.0)
    if passive:
        score = min(score, 55.0)
    if heartbeat_active is False:
        score = min(score, 35.0)
    return round(min(100.0, max(0.0, score)), 1)
