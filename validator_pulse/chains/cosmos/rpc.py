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


def _consensus_status(*, reachable: bool, syncing: bool, peer_count: int) -> HealthStatus:
    if not reachable:
        return "critical"
    if syncing:
        return "degraded"
    if peer_count < 3:
        return "degraded"
    return "healthy"


async def _comet_get(rpc_url: str, path: str) -> dict[str, Any]:
    base = normalize_rpc_url(rpc_url).rstrip("/")
    url = f"{base}{path if path.startswith('/') else '/' + path}"
    async with async_rpc_client() as client:
        res = await client.get(url)
        res.raise_for_status()
        body = res.json()
    if isinstance(body, dict) and "result" in body:
        result = body.get("result")
        return result if isinstance(result, dict) else {}
    return body if isinstance(body, dict) else {}


async def collect_comet_consensus(rpc_url: str) -> ConsensusHealth:
    base = normalize_rpc_url(rpc_url)
    try:
        await probe_rpc_endpoint(base)
        status = await _comet_get(base, "/status")
        try:
            net = await _comet_get(base, "/net_info")
        except Exception:  # noqa: BLE001
            net = {}

        sync_info = status.get("sync_info") or {}
        catching_up = bool(sync_info.get("catching_up"))
        height = _as_int(sync_info.get("latest_block_height"))
        peers = net.get("peers") or net.get("n_peers") or []
        if isinstance(peers, list):
            peer_count = len(peers)
        else:
            peer_count = _as_int(peers)

        return ConsensusHealth(
            beacon_reachable=True,
            syncing=catching_up,
            sync_distance=1 if catching_up else 0,
            head_slot=height,
            finalized_epoch=max(0, height - 1),
            justified_epoch=height,
            peer_count=peer_count,
            connected_peers=peer_count,
            status=_consensus_status(
                reachable=True, syncing=catching_up, peer_count=peer_count
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
