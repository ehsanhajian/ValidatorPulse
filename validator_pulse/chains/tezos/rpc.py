from __future__ import annotations

from typing import Any

from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus


async def tezos_get(rpc_url: str, path: str, *, params: dict[str, Any] | None = None) -> Any:
    base = normalize_rpc_url(rpc_url).rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    async with async_rpc_client(timeout=12.0) as client:
        res = await client.get(url, params=params or {})
        res.raise_for_status()
        return res.json()


async def collect_tezos_consensus(rpc_url: str) -> ConsensusHealth:
    base = normalize_rpc_url(rpc_url)
    try:
        await probe_rpc_endpoint(base)
        head = await tezos_get(rpc_url, "chains/main/blocks/head")
        header = (head or {}).get("header") or {}
        level = int(header.get("level") or 0)
        hash_ = str(header.get("hash") or "")
        chain_id = str((head or {}).get("chain_id") or "")
        # Octez does not expose peer count on head; use shell health endpoint when present.
        peers = 0
        try:
            conn = await tezos_get(rpc_url, "network/connections")
            if isinstance(conn, list):
                peers = len(conn)
        except Exception:  # noqa: BLE001
            peers = 0
        syncing = level == 0
        return ConsensusHealth(
            beacon_reachable=True,
            syncing=syncing,
            sync_distance=0,
            head_slot=level,
            finalized_epoch=level,
            justified_epoch=level,
            peer_count=peers,
            connected_peers=peers,
            status=_consensus_status(reachable=True, syncing=syncing, peers=peers),
            last_error=None if chain_id else None,
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


def _consensus_status(*, reachable: bool, syncing: bool, peers: int) -> HealthStatus:
    if not reachable:
        return "critical"
    if syncing:
        return "degraded"
    if peers and peers < 3:
        return "degraded"
    return "healthy"


async def fetch_delegate_info(rpc_url: str, baker: str) -> dict[str, Any]:
    path = f"chains/main/blocks/head/context/delegates/{baker}"
    data = await tezos_get(rpc_url, path)
    return data if isinstance(data, dict) else {}


async def fetch_delegate_participation(rpc_url: str, baker: str) -> dict[str, Any]:
    path = f"chains/main/blocks/head/context/delegates/{baker}/participation"
    data = await tezos_get(rpc_url, path)
    return data if isinstance(data, dict) else {}


async def fetch_baking_rights(
    rpc_url: str,
    baker: str,
    *,
    cycle: int | None = None,
) -> Any:
    params: dict[str, Any] = {"delegate": baker}
    if cycle is not None:
        params["cycle"] = cycle
    return await tezos_get(
        rpc_url,
        "chains/main/blocks/head/helpers/baking_rights",
        params=params,
    )


async def fetch_attestation_rights(
    rpc_url: str,
    baker: str,
    *,
    cycle: int | None = None,
) -> Any:
    params: dict[str, Any] = {"delegate": baker}
    if cycle is not None:
        params["cycle"] = cycle
    # Tenderbake renamed endorsing → attestation; try attestation first.
    for path in (
        "chains/main/blocks/head/helpers/attestation_rights",
        "chains/main/blocks/head/helpers/endorsing_rights",
    ):
        try:
            return await tezos_get(rpc_url, path, params=params)
        except Exception:  # noqa: BLE001
            continue
    return []


async def try_fetch_openmetrics(metrics_url: str) -> tuple[bool, str | None]:
    if not metrics_url or not metrics_url.strip():
        return False, None
    url = normalize_rpc_url(metrics_url)
    try:
        async with async_rpc_client(timeout=6.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            if not (res.text or "").strip():
                return False, "Tezos OpenMetrics endpoint returned empty body"
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"Tezos metrics enrichment unavailable: {format_transport_error(exc)}"
