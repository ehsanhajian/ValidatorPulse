from __future__ import annotations

from typing import Any

from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus


async def near_rpc(
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
        "params": params if params is not None else [],
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
        raise RuntimeError(f"NEAR RPC {method} failed: {message}")
    return body.get("result")


def _consensus_status(*, reachable: bool, syncing: bool, peers: int) -> HealthStatus:
    if not reachable:
        return "critical"
    if syncing:
        return "degraded"
    if peers and peers < 3:
        return "degraded"
    return "healthy"


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
        return int(text)
    return int(value)


async def collect_near_consensus(rpc_url: str) -> ConsensusHealth:
    base = normalize_rpc_url(rpc_url)
    try:
        await probe_rpc_endpoint(base)
        status = await near_rpc(base, "status") or {}
        sync_info = status.get("sync_info") or {}
        syncing = bool(sync_info.get("syncing"))
        latest = _as_int(sync_info.get("latest_block_height"))
        earliest = _as_int(sync_info.get("earliest_block_height"))
        sync_distance = max(0, latest - earliest) if syncing and latest else 0
        # Peer count is not always present on public RPC status payloads.
        peers = _as_int((status.get("network_info") or {}).get("num_peers") or 0)
        protocol = (status.get("protocol_version") or status.get("latest_protocol_version"))
        version_note = None
        if protocol is not None:
            version_note = f"protocol_version={protocol}"

        return ConsensusHealth(
            beacon_reachable=True,
            syncing=syncing,
            sync_distance=sync_distance,
            head_slot=latest,
            finalized_epoch=_as_int(sync_info.get("latest_block_height")),
            justified_epoch=_as_int(sync_info.get("latest_block_height")),
            peer_count=peers,
            connected_peers=peers,
            status=_consensus_status(reachable=True, syncing=syncing, peers=peers),
            last_error=version_note if syncing else None,
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


async def fetch_validators(rpc_url: str) -> dict[str, Any]:
    """Latest epoch validator set via `validators` (not EXPERIMENTAL_validators_ordered)."""
    result = await near_rpc(rpc_url, "validators", [None])
    if not isinstance(result, dict):
        return {}
    return result


def format_kickout_reason(reason: Any) -> str:
    if reason is None:
        return "unknown"
    if isinstance(reason, str):
        return reason
    if isinstance(reason, dict):
        if len(reason) == 1:
            key = next(iter(reason.keys()))
            detail = reason[key]
            if detail in (None, {}, []):
                return str(key)
            return f"{key}: {detail}"
        return str(reason)
    return str(reason)


def index_validators(payload: dict[str, Any]) -> dict[str, Any]:
    """Build lookup maps from a `validators` RPC response."""
    current = {
        str(v.get("account_id")): v
        for v in (payload.get("current_validators") or [])
        if isinstance(v, dict) and v.get("account_id")
    }
    next_set = {
        str(v.get("account_id")): v
        for v in (payload.get("next_validators") or [])
        if isinstance(v, dict) and v.get("account_id")
    }
    proposals = {
        str(v.get("account_id")): v
        for v in (payload.get("current_proposals") or [])
        if isinstance(v, dict) and v.get("account_id")
    }
    kickouts = {
        str(v.get("account_id")): v
        for v in (payload.get("prev_epoch_kickout") or [])
        if isinstance(v, dict) and v.get("account_id")
    }
    return {
        "epoch_height": _as_int(payload.get("epoch_height")),
        "epoch_start_height": _as_int(payload.get("epoch_start_height")),
        "current": current,
        "next": next_set,
        "proposals": proposals,
        "kickouts": kickouts,
        "rewards": payload.get("validator_reward_paid_prev_epoch") or {},
    }


async def try_fetch_near_metrics(metrics_url: str) -> tuple[bool, str | None]:
    """Optional nearcore Prometheus scrape. Soft-fail when unavailable."""
    url = normalize_rpc_url(metrics_url)
    try:
        async with async_rpc_client(timeout=5.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            text = res.text or ""
            if not text.strip():
                return False, "NEAR metrics endpoint returned empty body"
            return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"NEAR metrics enrichment unavailable: {format_transport_error(exc)}"
