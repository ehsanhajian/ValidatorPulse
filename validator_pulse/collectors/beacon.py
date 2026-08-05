from __future__ import annotations

import httpx

from validator_pulse.models import ConsensusHealth, HealthStatus

SLOTS_PER_EPOCH = 32


def _consensus_status(*, reachable: bool, syncing: bool, peer_count: int) -> HealthStatus:
    if not reachable:
        return "critical"
    if syncing:
        return "degraded"
    if peer_count < 5:
        return "degraded"
    return "healthy"


async def collect_consensus(beacon_api_url: str) -> ConsensusHealth:
    base = beacon_api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            health_res = await client.get(f"{base}/eth/v1/node/health")
            syncing_res = await client.get(f"{base}/eth/v1/node/syncing")
            peers_res = await client.get(f"{base}/eth/v1/node/peers")
            finality_res = await client.get(
                f"{base}/eth/v1/beacon/states/head/finality_checkpoints"
            )

        syncing_data = syncing_res.json().get("data", {})
        peers = peers_res.json().get("data", []) or []
        finality = finality_res.json().get("data", {})
        connected = sum(1 for p in peers if p.get("state") == "connected")
        syncing_flag = bool(syncing_data.get("is_syncing"))
        reachable = health_res.status_code < 500

        return ConsensusHealth(
            beacon_reachable=reachable,
            syncing=syncing_flag,
            sync_distance=int(syncing_data.get("sync_distance") or 0),
            head_slot=int(syncing_data.get("head_slot") or 0),
            finalized_epoch=int((finality.get("finalized") or {}).get("epoch") or 0),
            justified_epoch=int(
                (finality.get("current_justified") or {}).get("epoch") or 0
            ),
            peer_count=len(peers),
            connected_peers=connected,
            status=_consensus_status(
                reachable=reachable,
                syncing=syncing_flag,
                peer_count=connected or len(peers),
            ),
        )
    except Exception as exc:  # noqa: BLE001 — surface as consensus critical
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
            last_error=str(exc),
        )


async def collect_validator_balances(
    beacon_api_url: str, validator_ids: list[str]
) -> list[dict]:
    """Fetch validators by index and/or BLS pubkey via Beacon API `id`."""
    if not validator_ids:
        return []
    base = beacon_api_url.rstrip("/")
    # Beacon nodes commonly accept repeated id= or comma-separated values.
    async with httpx.AsyncClient(timeout=8.0) as client:
        res = await client.get(
            f"{base}/eth/v1/beacon/states/head/validators",
            params=[("id", vid) for vid in validator_ids],
        )
        res.raise_for_status()
        data = res.json().get("data", []) or []

    out = []
    for item in data:
        validator = item.get("validator") or {}
        out.append(
            {
                "index": int(item.get("index") or 0),
                "pubkey": validator.get("pubkey"),
                "status": item.get("status") or "unknown",
                "balance_gwei": int(item.get("balance") or 0),
                "effective_balance_gwei": int(validator.get("effective_balance") or 0),
            }
        )
    return out


async def fetch_attester_duties(
    beacon_api_url: str, epoch: int, validator_indices: list[int]
) -> list[dict]:
    """POST /eth/v1/validator/duties/attester/{epoch}."""
    if not validator_indices:
        return []
    base = beacon_api_url.rstrip("/")
    body = [str(i) for i in validator_indices]
    async with httpx.AsyncClient(timeout=12.0) as client:
        res = await client.post(
            f"{base}/eth/v1/validator/duties/attester/{epoch}",
            json=body,
        )
        if res.status_code >= 400:
            return []
        data = res.json().get("data", []) or []
    return [
        {
            "validator_index": int(item.get("validator_index") or 0),
            "slot": int(item.get("slot") or 0),
            "committee_index": int(item.get("committee_index") or 0),
        }
        for item in data
    ]


async def fetch_proposer_duties(beacon_api_url: str, epoch: int) -> list[dict]:
    """GET /eth/v1/validator/duties/proposer/{epoch}."""
    base = beacon_api_url.rstrip("/")
    async with httpx.AsyncClient(timeout=12.0) as client:
        res = await client.get(f"{base}/eth/v1/validator/duties/proposer/{epoch}")
        if res.status_code >= 400:
            return []
        data = res.json().get("data", []) or []
    return [
        {
            "validator_index": int(item.get("validator_index") or 0),
            "slot": int(item.get("slot") or 0),
        }
        for item in data
    ]


async def fetch_attestation_rewards(
    beacon_api_url: str, epoch: int, validator_indices: list[int]
) -> dict[int, dict] | None:
    """
    POST /eth/v1/beacon/rewards/attestations/{epoch}.

    Returns a map of validator_index → reward entry, or None if the endpoint
    is unavailable (so callers can avoid inventing outcomes).
    """
    if not validator_indices:
        return {}
    base = beacon_api_url.rstrip("/")
    body = [str(i) for i in validator_indices]
    async with httpx.AsyncClient(timeout=12.0) as client:
        res = await client.post(
            f"{base}/eth/v1/beacon/rewards/attestations/{epoch}",
            json=body,
        )
        if res.status_code >= 400:
            return None
        payload = res.json().get("data", {}) or {}
        total = payload.get("total_rewards") or []

    out: dict[int, dict] = {}
    for item in total:
        try:
            index = int(item.get("validator_index") or 0)
        except (TypeError, ValueError):
            continue
        out[index] = item
    return out


async def check_block_at_slot(beacon_api_url: str, slot: int) -> bool | None:
    """
    Return True if a block exists at slot, False if missing, None if unknown.

    Uses Beacon API GET /eth/v2/beacon/blocks/{slot} (404 ⇒ missed once the
    slot is in the past).
    """
    base = beacon_api_url.rstrip("/")
    async with httpx.AsyncClient(timeout=8.0) as client:
        res = await client.get(f"{base}/eth/v2/beacon/blocks/{slot}")
        if res.status_code == 404:
            return False
        if res.status_code >= 400:
            return None
        return True
