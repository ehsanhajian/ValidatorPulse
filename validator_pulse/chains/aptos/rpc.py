from __future__ import annotations

from typing import Any

from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus


def _normalize_rest_base(rest_url: str) -> str:
    base = normalize_rpc_url(rest_url).rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _auth_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    return headers


async def aptos_get(
    rest_url: str,
    path: str,
    *,
    api_key: str | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    base = _normalize_rest_base(rest_url)
    url = f"{base}/{path.lstrip('/')}"
    async with async_rpc_client(timeout=12.0) as client:
        res = await client.get(url, headers=_auth_headers(api_key), params=params or {})
        res.raise_for_status()
        return res.json()


async def aptos_view(
    rest_url: str,
    function: str,
    arguments: list[Any],
    *,
    api_key: str | None = None,
    type_arguments: list[str] | None = None,
) -> Any:
    base = _normalize_rest_base(rest_url)
    url = f"{base}/view"
    body = {
        "function": function,
        "type_arguments": type_arguments or [],
        "arguments": arguments,
    }
    async with async_rpc_client(timeout=12.0) as client:
        res = await client.post(url, headers=_auth_headers(api_key), json=body)
        res.raise_for_status()
        return res.json()


async def collect_aptos_consensus(
    rest_url: str,
    *,
    api_key: str | None = None,
) -> ConsensusHealth:
    base = _normalize_rest_base(rest_url)
    try:
        await probe_rpc_endpoint(base)
        ledger = await aptos_get(rest_url, "", api_key=api_key)
        if not isinstance(ledger, dict):
            ledger = {}
        epoch = int(str(ledger.get("epoch") or 0))
        block_height = int(str(ledger.get("block_height") or 0))
        version = int(str(ledger.get("ledger_version") or 0))
        # Fullnodes don't expose peer count on /v1; treat reachable ledger as healthy.
        syncing = version == 0 or block_height == 0
        return ConsensusHealth(
            beacon_reachable=True,
            syncing=syncing,
            sync_distance=0 if not syncing else -1,
            head_slot=block_height,
            finalized_epoch=epoch,
            justified_epoch=epoch,
            peer_count=0,
            connected_peers=0,
            status=_consensus_status(reachable=True, syncing=syncing),
            last_error=None,
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


def _consensus_status(*, reachable: bool, syncing: bool) -> HealthStatus:
    if not reachable:
        return "critical"
    if syncing:
        return "degraded"
    return "healthy"


async def fetch_validator_set(
    rest_url: str,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    data = await aptos_get(
        rest_url,
        "accounts/0x1/resource/0x1::stake::ValidatorSet",
        api_key=api_key,
    )
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else {}


async def fetch_validator_state(
    rest_url: str,
    pool: str,
    *,
    api_key: str | None = None,
) -> Any:
    return await aptos_view(
        rest_url,
        "0x1::stake::get_validator_state",
        [pool],
        api_key=api_key,
    )


async def fetch_validator_index(
    rest_url: str,
    pool: str,
    *,
    api_key: str | None = None,
) -> Any:
    return await aptos_view(
        rest_url,
        "0x1::stake::get_validator_index",
        [pool],
        api_key=api_key,
    )


async def fetch_proposal_counts(
    rest_url: str,
    validator_index: int,
    *,
    api_key: str | None = None,
) -> Any:
    return await aptos_view(
        rest_url,
        "0x1::stake::get_current_epoch_proposal_counts",
        [str(validator_index)],
        api_key=api_key,
    )


async def fetch_stake(
    rest_url: str,
    pool: str,
    *,
    api_key: str | None = None,
) -> Any:
    return await aptos_view(
        rest_url,
        "0x1::stake::get_stake",
        [pool],
        api_key=api_key,
    )


async def try_fetch_inspection_metrics(metrics_url: str) -> tuple[bool, str | None]:
    if not metrics_url or not metrics_url.strip():
        return False, None
    url = normalize_rpc_url(metrics_url)
    try:
        async with async_rpc_client(timeout=6.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            text = res.text or ""
            if not text.strip():
                return False, "Aptos inspection metrics returned empty body"
            # Soft enrichment only — presence of timeout/round series is optional.
            return True, None
    except Exception as exc:  # noqa: BLE001
        return (
            False,
            f"Aptos metrics enrichment unavailable: {format_transport_error(exc)}",
        )
