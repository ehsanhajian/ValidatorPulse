from __future__ import annotations

import hashlib
from base64 import b64decode
from typing import Any

from validator_pulse.chains.cosmos.bech32 import bech32_encode, retarget_bech32
from validator_pulse.chains.cosmos.profiles import CosmosProfile
from validator_pulse.http_client import async_rpc_client, normalize_rpc_url


def operator_to_consensus_address(valoper: str, profile: CosmosProfile) -> str:
    """Best-effort HRP retarget when operator/consensus share the same payload.

    Prefer ``consensus_address_from_pubkey`` when the LCD validator includes a
    consensus pubkey — operator and consensus addresses are usually different.
    """
    return retarget_bech32(valoper, profile.valcons_prefix)


def consensus_address_from_pubkey(pubkey_b64: str, profile: CosmosProfile) -> str:
    """Derive Tendermint consensus address (SHA-256 of pubkey, first 20 bytes)."""
    raw = b64decode(pubkey_b64)
    digest = hashlib.sha256(raw).digest()[:20]
    return bech32_encode(profile.valcons_prefix, digest)


async def lcd_get(rest_url: str, path: str) -> dict[str, Any]:
    base = normalize_rpc_url(rest_url).rstrip("/")
    url = f"{base}{path if path.startswith('/') else '/' + path}"
    async with async_rpc_client() as client:
        res = await client.get(url)
        res.raise_for_status()
        body = res.json()
    if not isinstance(body, dict):
        raise RuntimeError(f"Unexpected LCD response for {path}")
    return body


async def fetch_slashing_params(rest_url: str) -> dict[str, Any]:
    body = await lcd_get(rest_url, "/cosmos/slashing/v1beta1/params")
    return body.get("params") or {}


async def fetch_validator(rest_url: str, valoper: str) -> dict[str, Any]:
    body = await lcd_get(
        rest_url, f"/cosmos/staking/v1beta1/validators/{valoper}"
    )
    return body.get("validator") or {}


async def fetch_signing_info(rest_url: str, cons_addr: str) -> dict[str, Any]:
    body = await lcd_get(
        rest_url, f"/cosmos/slashing/v1beta1/signing_infos/{cons_addr}"
    )
    return body.get("val_signing_info") or {}
