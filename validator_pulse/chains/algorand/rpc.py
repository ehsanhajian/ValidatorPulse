from __future__ import annotations

from typing import Any

from validator_pulse.chains.algorand.auth import redact_secrets
from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus

_TOKEN_HEADER = "X-Algo-API-Token"


def _auth_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {_TOKEN_HEADER: token}


async def algod_get(
    algod_url: str,
    path: str,
    *,
    token: str | None,
    params: dict[str, Any] | None = None,
) -> Any:
    base = normalize_rpc_url(algod_url).rstrip("/")
    url = f"{base}/{path.lstrip('/')}"
    async with async_rpc_client(timeout=12.0) as client:
        res = await client.get(url, headers=_auth_headers(token), params=params or {})
        res.raise_for_status()
        return res.json()


async def collect_algod_consensus(
    algod_url: str,
    *,
    token: str | None,
) -> ConsensusHealth:
    base = normalize_rpc_url(algod_url)
    try:
        await probe_rpc_endpoint(base)
        status = await algod_get(algod_url, "v2/status", token=token)
        if not isinstance(status, dict):
            status = {}
        last_round = int(status.get("last-round") or 0)
        catchup_ns = int(status.get("catchup-time") or 0)
        time_since_ns = int(status.get("time-since-last-round") or 0)
        stopped = bool(status.get("stopped-at-unsupported-round"))
        catching_up = catchup_ns > 0 or bool(status.get("catchpoint"))
        # ~2.8s per round; flag stall when no new round for > ~30s.
        stalled = time_since_ns > 30_000_000_000
        syncing = catching_up or stalled or stopped or last_round == 0
        sync_distance = -1 if stalled or stopped else (1 if catching_up else 0)
        return ConsensusHealth(
            beacon_reachable=True,
            syncing=syncing,
            sync_distance=sync_distance,
            head_slot=last_round,
            finalized_epoch=last_round,
            justified_epoch=last_round,
            peer_count=0,
            connected_peers=0,
            status=_consensus_status(
                reachable=True,
                syncing=syncing,
                stalled=stalled,
                stopped=stopped,
            ),
            last_error=(
                "Node stopped at unsupported round"
                if stopped
                else ("Round progress stalled" if stalled else None)
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raw = format_transport_error(exc)
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
            last_error=redact_secrets(raw, token),
        )


def _consensus_status(
    *,
    reachable: bool,
    syncing: bool,
    stalled: bool,
    stopped: bool,
) -> HealthStatus:
    if not reachable or stopped:
        return "critical"
    if stalled or syncing:
        return "degraded"
    return "healthy"


async def fetch_account(algod_url: str, address: str, *, token: str | None) -> dict[str, Any]:
    data = await algod_get(
        algod_url,
        f"v2/accounts/{address}",
        token=token,
        params={"exclude": "all"},
    )
    return data if isinstance(data, dict) else {}


async def fetch_participation_keys(algod_url: str, *, token: str | None) -> Any:
    return await algod_get(algod_url, "v2/participation", token=token)


async def try_fetch_algod_metrics(metrics_url: str) -> tuple[bool, str | None]:
    if not metrics_url or not metrics_url.strip():
        return False, None
    url = normalize_rpc_url(metrics_url)
    try:
        async with async_rpc_client(timeout=6.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            if not (res.text or "").strip():
                return False, "Algod Prometheus metrics returned empty body"
        return True, None
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            f"Algod metrics enrichment unavailable: {format_transport_error(exc)}",
        )
