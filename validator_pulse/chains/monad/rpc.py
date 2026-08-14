from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from validator_pulse.chains.monad.abi import (
    EXPECTED_CHAIN_ID,
    SELECTOR_GET_CONSENSUS_SET,
    SELECTOR_GET_EPOCH,
    SELECTOR_GET_PROPOSER,
    SELECTOR_GET_VALIDATOR,
    STAKING_PRECOMPILE,
    EpochInfo,
    ValidatorSetPage,
    ValidatorView,
    decode_epoch,
    decode_proposer_val_id,
    decode_validator,
    decode_validator_set_page,
    encode_selector_call,
)
from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus


async def monad_rpc(
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
    if not isinstance(body, dict):
        raise RuntimeError(f"Monad RPC {method} returned non-object payload")
    if body.get("error"):
        err = body["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"Monad RPC {method} failed: {message}")
    return body.get("result")


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
        if text.startswith("0x"):
            return int(text, 16)
        if text.lstrip("-").isdigit():
            return int(text)
    return int(value)


async def eth_call(rpc_url: str, data: str) -> str:
    result = await monad_rpc(
        rpc_url,
        "eth_call",
        [{"to": STAKING_PRECOMPILE, "data": data}, "latest"],
    )
    return str(result or "0x")


async def fetch_chain_id(rpc_url: str) -> int:
    return _as_int(await monad_rpc(rpc_url, "eth_chainId"))


async def fetch_epoch(rpc_url: str) -> EpochInfo:
    return decode_epoch(await eth_call(rpc_url, encode_selector_call(SELECTOR_GET_EPOCH)))


async def fetch_proposer_val_id(rpc_url: str) -> int:
    return decode_proposer_val_id(
        await eth_call(rpc_url, encode_selector_call(SELECTOR_GET_PROPOSER))
    )


async def fetch_validator(rpc_url: str, validator_id: int) -> ValidatorView:
    data = encode_selector_call(SELECTOR_GET_VALIDATOR, validator_id)
    return decode_validator(await eth_call(rpc_url, data))


async def fetch_consensus_validator_set(
    rpc_url: str,
    *,
    max_pages: int = 64,
) -> list[int]:
    """Paginate getConsensusValidatorSet until isDone (do not assume set size)."""
    ids: list[int] = []
    start = 0
    seen_indexes: set[int] = set()
    for _ in range(max_pages):
        if start in seen_indexes:
            break
        seen_indexes.add(start)
        data = encode_selector_call(SELECTOR_GET_CONSENSUS_SET, start)
        page: ValidatorSetPage = decode_validator_set_page(await eth_call(rpc_url, data))
        ids.extend(page.val_ids)
        if page.is_done:
            break
        start = page.next_index
    # Preserve order, drop duplicates from overlapping pages.
    unique: list[int] = []
    seen: set[int] = set()
    for vid in ids:
        if vid in seen:
            continue
        seen.add(vid)
        unique.append(vid)
    return unique


def _consensus_status(*, reachable: bool, syncing: bool, chain_ok: bool) -> HealthStatus:
    if not reachable or not chain_ok:
        return "critical"
    if syncing:
        return "degraded"
    return "healthy"


async def collect_monad_consensus(
    rpc_url: str,
    *,
    expected_chain_id: int = EXPECTED_CHAIN_ID,
) -> ConsensusHealth:
    base = normalize_rpc_url(rpc_url)
    try:
        await probe_rpc_endpoint(base)
        chain_id = await fetch_chain_id(base)
        chain_ok = chain_id == expected_chain_id
        block_hex = await monad_rpc(base, "eth_blockNumber")
        head = _as_int(block_hex)
        syncing_raw = await monad_rpc(base, "eth_syncing")
        syncing = bool(syncing_raw) and syncing_raw is not False
        epoch = EpochInfo(epoch=0, in_epoch_delay_period=False)
        try:
            epoch = await fetch_epoch(base)
        except Exception:  # noqa: BLE001
            epoch = EpochInfo(epoch=0, in_epoch_delay_period=False)
        err = None if chain_ok else (
            f"Unexpected chain ID {chain_id} (expected Monad {expected_chain_id})"
        )
        return ConsensusHealth(
            beacon_reachable=True,
            syncing=syncing or not chain_ok,
            sync_distance=-1 if not chain_ok else (1 if syncing else 0),
            head_slot=head,
            finalized_epoch=epoch.epoch,
            justified_epoch=epoch.epoch,
            peer_count=0,
            connected_peers=0,
            status=_consensus_status(
                reachable=True, syncing=syncing, chain_ok=chain_ok
            ),
            last_error=err,
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


@dataclass(frozen=True)
class MonadNodeMetrics:
    proposed_blocks: int | None = None
    round: int | None = None
    connected_peers: int | None = None


def parse_prometheus_text(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        token = line.split()[0]
        name = token.split("{", 1)[0]
        try:
            value = float(line.split()[-1])
        except (ValueError, IndexError):
            continue
        out[name] = out.get(name, 0.0) + value
    return out


def extract_monad_metrics(metrics: dict[str, float]) -> MonadNodeMetrics:
    proposed = None
    for key in ("consensus_proposed_blocks", "proposed_blocks", "monad_proposed_blocks"):
        if key in metrics:
            proposed = int(metrics[key])
            break
    rnd = None
    for key in ("consensus_round", "monad_consensus_round", "current_round"):
        if key in metrics:
            rnd = int(metrics[key])
            break
    peers = None
    for key in ("connected_peers", "monad_peers", "peer_count"):
        if key in metrics:
            peers = int(metrics[key])
            break
    return MonadNodeMetrics(
        proposed_blocks=proposed, round=rnd, connected_peers=peers
    )


async def try_fetch_monad_metrics(
    metrics_url: str,
) -> tuple[MonadNodeMetrics | None, str | None]:
    if not metrics_url or not metrics_url.strip():
        return None, None
    url = normalize_rpc_url(metrics_url)
    try:
        async with async_rpc_client(timeout=6.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            text = res.text or ""
            if not text.strip():
                return None, "Monad Prometheus metrics returned empty body"
            return extract_monad_metrics(parse_prometheus_text(text)), None
    except Exception as exc:  # noqa: BLE001
        return None, f"Monad metrics enrichment unavailable: {format_transport_error(exc)}"
