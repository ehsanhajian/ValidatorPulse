from __future__ import annotations

from typing import Any

from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text.startswith("0x"):
            return int(text, 16)
        return int(text)
    return int(value)


async def substrate_rpc(
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
        raise RuntimeError(f"Substrate RPC {method} failed: {message}")
    return body.get("result")


def _consensus_status(*, reachable: bool, syncing: bool, peer_count: int) -> HealthStatus:
    if not reachable:
        return "critical"
    if syncing:
        return "degraded"
    if peer_count < 3:
        return "degraded"
    return "healthy"


async def collect_substrate_consensus(rpc_url: str) -> ConsensusHealth:
    base = normalize_rpc_url(rpc_url)
    try:
        await probe_rpc_endpoint(base)
        health, sync_state, header = await _gather_node(base)
        peers = int(health.get("peers") or 0)
        syncing = bool(health.get("isSyncing"))
        current = _as_int(sync_state.get("currentBlock"))
        highest = _as_int(sync_state.get("highestBlock"))
        if current == 0 and header:
            current = _as_int(header.get("number"))
        sync_distance = max(0, highest - current) if highest else 0
        # Reuse epoch fields for finalized/best block numbers (parachain has no ETH epochs).
        finalized = current - min(current, 2) if current else 0

        return ConsensusHealth(
            beacon_reachable=True,
            syncing=syncing,
            sync_distance=sync_distance,
            head_slot=current,
            finalized_epoch=finalized,
            justified_epoch=current,
            peer_count=peers,
            connected_peers=peers,
            status=_consensus_status(
                reachable=True, syncing=syncing, peer_count=peers
            ),
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


async def _gather_node(rpc_url: str) -> tuple[dict, dict, dict | None]:
    health = await substrate_rpc(rpc_url, "system_health")
    try:
        sync_state = await substrate_rpc(rpc_url, "system_syncState")
    except Exception:  # noqa: BLE001
        sync_state = {}
    header = None
    try:
        header = await substrate_rpc(rpc_url, "chain_getHeader")
    except Exception:  # noqa: BLE001
        header = None
    return health or {}, sync_state or {}, header
