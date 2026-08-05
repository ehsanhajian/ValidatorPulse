from __future__ import annotations

import asyncio
from typing import Any

import httpx

# Well-known Substrate demo keys (Alice / Bob) used in local demos.
_WELL_KNOWN_NAMES: dict[str, str] = {
    "5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY": "Alice",
    "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty": "Bob",
}

# Polkadot parachain id → Subscan network slug
_SUBSCAN_BY_PARA: dict[int, str] = {
    1000: "assethub-polkadot",
    2000: "acala",
    2004: "moonbeam",
    2006: "astar",
    2030: "bifrost",
    2034: "hydration",
    2035: "phala",
    2046: "manta",
    2001: "bifrost-kusama",
    2007: "shiden",
    2023: "moonriver",
}


def subscan_network(parachain_id: int | None) -> str:
    if parachain_id is None:
        return "polkadot"
    return _SUBSCAN_BY_PARA.get(parachain_id, "polkadot")


def _pick_display(payload: dict[str, Any]) -> str | None:
    account = payload.get("account") if isinstance(payload, dict) else None
    if not isinstance(account, dict):
        # Some Subscan endpoints nest under data already unwrapped
        account = payload if isinstance(payload, dict) else {}

    candidates: list[Any] = [
        account.get("display"),
        account.get("nickname"),
        account.get("account_display"),
    ]
    display_obj = account.get("account_display")
    if isinstance(display_obj, dict):
        candidates.extend(
            [
                display_obj.get("display"),
                display_obj.get("display_name"),
                display_obj.get("nickname"),
            ]
        )
        identity = display_obj.get("identity")
        if isinstance(identity, dict):
            candidates.append(identity.get("display"))

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("display") or value.get("display_name")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


async def fetch_subscan_name(
    address: str,
    *,
    parachain_id: int | None = None,
    api_key: str | None = None,
    timeout: float = 6.0,
) -> str | None:
    if address in _WELL_KNOWN_NAMES:
        return _WELL_KNOWN_NAMES[address]

    network = subscan_network(parachain_id)
    url = f"https://{network}.api.subscan.io/api/v2/scan/search"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.post(url, json={"key": address}, headers=headers)
            if res.status_code >= 400:
                # Fallback older account endpoint
                res = await client.post(
                    f"https://{network}.api.subscan.io/api/v2/scan/account",
                    json={"address": address},
                    headers=headers,
                )
            if res.status_code >= 400:
                return None
            body = res.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return None
        return _pick_display(data)
    except Exception:  # noqa: BLE001
        return None


async def fetch_beaconcha_name(
    *,
    index: int | None = None,
    pubkey: str | None = None,
    base_url: str = "https://beaconcha.in",
    timeout: float = 6.0,
) -> str | None:
    target = pubkey or (str(index) if index is not None else None)
    if not target:
        return None
    url = f"{base_url.rstrip('/')}/api/v1/validator/{target}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(url, headers={"Accept": "application/json"})
            if res.status_code >= 400:
                return None
            body = res.json()
        data = body.get("data")
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            return None
        name = data.get("name") or data.get("status")
        if isinstance(name, str) and name.strip() and name.strip().lower() not in {
            "active_online",
            "active_offline",
            "pending",
            "exited",
            "slashed",
        }:
            return name.strip()
        return None
    except Exception:  # noqa: BLE001
        return None


async def enrich_operator_names(
    operators: list[Any],
    *,
    chain: str,
    parachain_id: int | None = None,
    enabled: bool = True,
    subscan_api_key: str | None = None,
    beaconcha_base_url: str = "https://beaconcha.in",
) -> None:
    """Mutates operators in place, setting display_name when resolvable."""
    if not enabled or not operators:
        return

    async def one(op: Any) -> None:
        current = getattr(op, "display_name", None)
        if current:
            return
        name: str | None = None
        if chain == "polkadot" and getattr(op, "pubkey", None):
            name = await fetch_subscan_name(
                op.pubkey,
                parachain_id=parachain_id,
                api_key=subscan_api_key,
            )
        elif chain == "ethereum":
            name = await fetch_beaconcha_name(
                index=getattr(op, "index", None),
                pubkey=getattr(op, "pubkey", None),
                base_url=beaconcha_base_url,
            )
        if name:
            op.display_name = name

    await asyncio.gather(*(one(op) for op in operators))
