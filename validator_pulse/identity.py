from __future__ import annotations

import asyncio
import time
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

_DISPLAY_CACHE_TTL_SECONDS = 3600
_display_cache: dict[str, tuple[float, str | None, str | None]] = {}


def reset_identity_cache() -> None:
    """Clear the process-local identity cache (primarily for tests)."""
    _display_cache.clear()


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
    api_key: str | None = None,
    timeout: float = 6.0,
) -> str | None:
    target = pubkey or (str(index) if index is not None else None)
    if not target:
        return None
    url = f"{base_url.rstrip('/')}/api/v1/validator/{target}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(url, headers=headers)
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


def _label_from_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("displayName", "display_name", "name", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return None


async def fetch_rated_operator_name(
    *,
    index: int | None = None,
    pubkey: str | None = None,
    api_key: str | None = None,
    base_url: str = "https://api.rated.network",
    network: str = "mainnet",
    timeout: float = 6.0,
) -> str | None:
    """Resolve a validator to a Rated node operator, DVT operator, or pool."""
    target = pubkey or (str(index) if index is not None else None)
    if not target or not api_key:
        return None
    url = f"{base_url.rstrip('/')}/v1/eth/validators/{target}/mappings"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "X-Rated-Network": network,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(url, headers=headers)
            if res.status_code >= 400:
                return None
            body = res.json()
    except Exception:  # noqa: BLE001
        return None

    data: Any = body
    if isinstance(body, dict):
        data = body.get("data", body.get("result", body))
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None

    for key in ("nodeOperators", "dvtOperators"):
        values = data.get(key)
        if isinstance(values, list):
            for value in values:
                label = _label_from_value(value)
                if label:
                    return label
    for key in ("pool", "subpool"):
        label = _label_from_value(data.get(key))
        if label and label.lower() not in {"unknown", "none"}:
            return label
    return None


async def fetch_ens_primary_name(
    address: str | None,
    *,
    enabled: bool = False,
    api_key: str | None = None,
    base_url: str = "https://api.enswhois.com",
    timeout: float = 6.0,
) -> str | None:
    """Resolve an execution withdrawal address to its indexed ENS primary name."""
    if not enabled or not address:
        return None
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    url = f"{base_url.rstrip('/')}/address/{address}/primary-name"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.get(url, headers=headers)
            if res.status_code >= 400:
                return None
            body = res.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(body, dict):
        return None
    current = body.get("current")
    if isinstance(current, dict):
        body = current
    for key in ("display", "primary_name", "primaryName", "name"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def decode_graffiti(value: Any) -> str | None:
    """Decode a Beacon block graffiti value into a safe, short label."""
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = bytes.fromhex(value[2:] if value.startswith("0x") else value)
        text = raw.rstrip(b"\x00").decode("utf-8", errors="ignore")
    except (ValueError, TypeError):
        return None
    text = " ".join(text.split()).strip()
    if not text or not all(char.isprintable() for char in text):
        return None
    return text[:32]


async def fetch_recent_graffiti(
    beacon_api_url: str | None,
    proposal_slots: list[int],
    *,
    timeout: float = 6.0,
) -> str | None:
    """Return graffiti from the newest successful proposal available locally."""
    if not beacon_api_url:
        return None
    base = beacon_api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for slot in proposal_slots[:3]:
                res = await client.get(f"{base}/eth/v2/beacon/blocks/{slot}")
                if res.status_code >= 400:
                    continue
                body = res.json()
                data = body.get("data", {}) if isinstance(body, dict) else {}
                message = data.get("message", {}) if isinstance(data, dict) else {}
                block_body = (
                    message.get("body", {}) if isinstance(message, dict) else {}
                )
                graffiti = (
                    block_body.get("graffiti")
                    if isinstance(block_body, dict)
                    else None
                )
                label = decode_graffiti(graffiti)
                if label:
                    return label
    except Exception:  # noqa: BLE001
        return None
    return None


async def enrich_operator_names(
    operators: list[Any],
    *,
    chain: str,
    parachain_id: int | None = None,
    enabled: bool = True,
    subscan_api_key: str | None = None,
    beaconcha_base_url: str = "https://beaconcha.in",
    beaconcha_api_key: str | None = None,
    beacon_api_url: str | None = None,
    rated_api_key: str | None = None,
    rated_api_base_url: str = "https://api.rated.network",
    rated_network: str = "mainnet",
    ens_lookup_enabled: bool = False,
    ens_api_key: str | None = None,
    ens_api_base_url: str = "https://api.enswhois.com",
) -> None:
    """Mutates operators in place, setting display_name when resolvable."""
    if not enabled or not operators:
        return

    semaphore = asyncio.Semaphore(5)

    async def one(op: Any) -> None:
        current = getattr(op, "display_name", None)
        if current:
            return
        cache_key = ":".join(
            [
                chain,
                str(getattr(op, "index", "")),
                str(getattr(op, "pubkey", "")),
                str(getattr(op, "withdrawal_address", "")),
                str(bool(rated_api_key)),
                str(ens_lookup_enabled),
            ]
        )
        cached = _display_cache.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] < _DISPLAY_CACHE_TTL_SECONDS:
            _, name, source = cached
            if name:
                op.display_name = name
                op.display_name_source = source
            return

        name: str | None = None
        source: str | None = None
        async with semaphore:
            if chain == "polkadot" and getattr(op, "pubkey", None):
                name = await fetch_subscan_name(
                    op.pubkey,
                    parachain_id=parachain_id,
                    api_key=subscan_api_key,
                )
                source = "subscan" if name else None
            elif chain == "ethereum":
                index = getattr(op, "index", None)
                pubkey = getattr(op, "pubkey", None)
                name = await fetch_beaconcha_name(
                    index=index,
                    pubkey=pubkey,
                    base_url=beaconcha_base_url,
                    api_key=beaconcha_api_key,
                )
                source = "beaconcha.in" if name else None

                if not name:
                    name = await fetch_rated_operator_name(
                        index=index,
                        pubkey=pubkey,
                        api_key=rated_api_key,
                        base_url=rated_api_base_url,
                        network=rated_network,
                    )
                    source = "rated" if name else None

                if not name:
                    name = await fetch_ens_primary_name(
                        getattr(op, "withdrawal_address", None),
                        enabled=ens_lookup_enabled,
                        api_key=ens_api_key,
                        base_url=ens_api_base_url,
                    )
                    source = "ens" if name else None

                if not name:
                    slots = [
                        int(duty.slot)
                        for duty in getattr(op, "recent_proposals", [])
                        if getattr(duty, "outcome", None) == "success"
                    ]
                    graffiti = await fetch_recent_graffiti(beacon_api_url, slots)
                    if graffiti:
                        name = f"Graffiti: {graffiti}"
                        source = "graffiti"

        _display_cache[cache_key] = (now, name, source)
        if name:
            op.display_name = name
            op.display_name_source = source

    await asyncio.gather(*(one(op) for op in operators))
