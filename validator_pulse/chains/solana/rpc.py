from __future__ import annotations

from typing import Any

from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus


async def solana_rpc(
    rpc_url: str,
    method: str,
    params: list[Any] | None = None,
    *,
    timeout: float | None = None,
) -> Any:
    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": method,
        "params": params or [],
    }
    url = normalize_rpc_url(rpc_url)
    async with async_rpc_client(timeout=timeout) as client:
        res = await client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        res.raise_for_status()
        body = res.json()
    if "error" in body and body["error"]:
        err = body["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"Solana RPC {method} failed: {message}")
    return body.get("result")


def _consensus_status(*, reachable: bool, syncing: bool) -> HealthStatus:
    if not reachable:
        return "critical"
    if syncing:
        return "degraded"
    return "healthy"


async def collect_solana_consensus(rpc_url: str) -> ConsensusHealth:
    base = normalize_rpc_url(rpc_url)
    try:
        await probe_rpc_endpoint(base)
        health = await solana_rpc(base, "getHealth")
        slot = int(await solana_rpc(base, "getSlot") or 0)
        epoch_info = await solana_rpc(base, "getEpochInfo") or {}
        # getHealth returns "ok" string when healthy; object/error otherwise.
        healthy = health == "ok" or health is True
        absolute_slot = int(epoch_info.get("absoluteSlot") or slot or 0)
        epoch = int(epoch_info.get("epoch") or 0)
        slot_index = int(epoch_info.get("slotIndex") or 0)
        slots_in_epoch = int(epoch_info.get("slotsInEpoch") or 0)
        syncing = not healthy
        sync_distance = max(0, slots_in_epoch - slot_index) if syncing else 0

        return ConsensusHealth(
            beacon_reachable=True,
            syncing=syncing,
            sync_distance=sync_distance,
            head_slot=absolute_slot or slot,
            finalized_epoch=epoch,
            justified_epoch=epoch,
            peer_count=0,
            connected_peers=0,
            status=_consensus_status(reachable=True, syncing=syncing),
            last_error=None if healthy else f"getHealth={health!r}",
        )
    except Exception as exc:  # noqa: BLE001
        return ConsensusHealth(
            beacon_reachable=False,
            syncing=True,
            sync_distance=-1,
            head_slot=0,
            finalized_epoch=0,
            justified_epoch=0,
            peer_count=0,
            connected_peers=0,
            status="critical",
            last_error=format_transport_error(exc),
        )


def _normalize_vote_entry(raw: dict[str, Any], *, delinquent: bool) -> dict[str, Any]:
    epoch_credits = raw.get("epochCredits") or []
    credits_earned = 0
    current_epoch = 0
    if epoch_credits:
        last = epoch_credits[-1]
        if isinstance(last, (list, tuple)) and len(last) >= 3:
            current_epoch = int(last[0])
            credits = int(last[1])
            previous = int(last[2])
            credits_earned = max(0, credits - previous)
        elif isinstance(last, (list, tuple)) and len(last) >= 2:
            current_epoch = int(last[0])
            credits_earned = int(last[1])

    return {
        "vote_pubkey": str(raw.get("votePubkey") or ""),
        "node_pubkey": str(raw.get("nodePubkey") or ""),
        "activated_stake": int(raw.get("activatedStake") or 0),
        "commission": int(raw.get("commission") or 0),
        "last_vote": int(raw.get("lastVote") or 0),
        "root_slot": int(raw.get("rootSlot") or 0),
        "epoch_vote_account": bool(raw.get("epochVoteAccount")),
        "epoch_credits_earned": credits_earned,
        "epoch": current_epoch,
        "delinquent": delinquent,
    }


async def fetch_vote_account(
    rpc_url: str,
    vote_pubkey: str,
) -> dict[str, Any] | None:
    """Return a single vote account (current or delinquent), or None if missing."""
    result = await solana_rpc(
        rpc_url,
        "getVoteAccounts",
        [{"votePubkey": vote_pubkey, "keepUnstakedDelinquents": True}],
    )
    if not isinstance(result, dict):
        return None
    for entry in result.get("current") or []:
        if isinstance(entry, dict) and entry.get("votePubkey") == vote_pubkey:
            return _normalize_vote_entry(entry, delinquent=False)
    for entry in result.get("delinquent") or []:
        if isinstance(entry, dict) and entry.get("votePubkey") == vote_pubkey:
            return _normalize_vote_entry(entry, delinquent=True)
    return None


async def fetch_vote_accounts_by_identity(
    rpc_url: str,
    identity: str,
) -> list[dict[str, Any]]:
    """Find vote accounts whose nodePubkey matches the validator identity."""
    result = await solana_rpc(
        rpc_url,
        "getVoteAccounts",
        [{"keepUnstakedDelinquents": True}],
    )
    if not isinstance(result, dict):
        return []
    matches: list[dict[str, Any]] = []
    for entry in result.get("current") or []:
        if isinstance(entry, dict) and entry.get("nodePubkey") == identity:
            matches.append(_normalize_vote_entry(entry, delinquent=False))
    for entry in result.get("delinquent") or []:
        if isinstance(entry, dict) and entry.get("nodePubkey") == identity:
            matches.append(_normalize_vote_entry(entry, delinquent=True))
    return matches


async def fetch_block_production_skip_rate(
    rpc_url: str,
    identity: str,
) -> tuple[int, int, float]:
    """Return (leader_slots, blocks_produced, skip_rate_percent) for an identity."""
    if not identity:
        return 0, 0, 0.0
    result = await solana_rpc(
        rpc_url,
        "getBlockProduction",
        [{"identity": identity}],
    )
    if not isinstance(result, dict):
        return 0, 0, 0.0
    value = result.get("value") if "value" in result else result
    if not isinstance(value, dict):
        return 0, 0, 0.0
    by_identity = value.get("byIdentity") or {}
    stats = by_identity.get(identity)
    if not isinstance(stats, (list, tuple)) or len(stats) < 2:
        return 0, 0, 0.0
    leader_slots = int(stats[0] or 0)
    blocks_produced = int(stats[1] or 0)
    if leader_slots <= 0:
        return 0, 0, 0.0
    skipped = max(0, leader_slots - blocks_produced)
    skip_rate = (skipped / leader_slots) * 100.0
    return leader_slots, blocks_produced, skip_rate
