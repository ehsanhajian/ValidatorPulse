from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from validator_pulse.chains.bsc.abi import (
    EXPECTED_CHAIN_IDS,
    MAINNET_CHAIN_ID,
    SELECTOR_SH_BASIC,
    SELECTOR_SH_CONSENSUS,
    SELECTOR_SH_CONSENSUS_TO_OP,
    SELECTOR_SH_DESCRIPTION,
    SELECTOR_SH_DOWNTIME_AMOUNT,
    SELECTOR_SH_FELONY_AMOUNT,
    SELECTOR_SH_FELONY_JAIL,
    SELECTOR_SH_GET_VALIDATORS,
    SELECTOR_SH_VOTE,
    SELECTOR_SI_INDICATOR,
    SELECTOR_SI_THRESHOLDS,
    SELECTOR_VS_GET_LIVING,
    SELECTOR_VS_GET_MINING,
    SELECTOR_VS_GET_VALIDATORS,
    SELECTOR_VS_IS_CURRENT,
    SELECTOR_VS_TURN_LENGTH,
    TOPIC_SI_MALICIOUS_VOTE,
    TOPIC_SI_SLASHED,
    TOPIC_STAKEHUB_JAILED,
    TOPIC_STAKEHUB_SLASHED,
    ValidatorBasicInfo,
    ValidatorDescription,
    decode_address,
    decode_address_array,
    decode_basic_info,
    decode_bool,
    decode_description,
    decode_dynamic_bytes,
    decode_stakehub_validator_page,
    decode_two_uints,
    decode_uint,
    decode_words,
    encode_address_call,
    encode_selector_call,
    is_zero_address,
    normalize_address,
    slash_type_label,
    topic_address,
)
from validator_pulse.chains.bsc.state import SlashThresholds
from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus


async def bsc_rpc(
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
        raise RuntimeError(f"BSC RPC {method} returned non-object payload")
    if body.get("error"):
        err = body["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"BSC RPC {method} failed: {message}")
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


async def eth_call(rpc_url: str, to: str, data: str) -> str:
    result = await bsc_rpc(
        rpc_url,
        "eth_call",
        [{"to": to, "data": data}, "latest"],
    )
    return str(result or "0x")


async def fetch_chain_id(rpc_url: str) -> int:
    return _as_int(await bsc_rpc(rpc_url, "eth_chainId"))


async def fetch_working_validators(rpc_url: str, validator_set: str) -> list[str]:
    data = await eth_call(rpc_url, validator_set, encode_selector_call(SELECTOR_VS_GET_VALIDATORS))
    return [normalize_address(a) for a in decode_address_array(data)]


async def fetch_living_validators(rpc_url: str, validator_set: str) -> list[str]:
    raw = await eth_call(rpc_url, validator_set, encode_selector_call(SELECTOR_VS_GET_LIVING))
    words = decode_words(raw)
    if not words:
        return []
    return [normalize_address(a) for a in decode_address_array(raw, offset_bytes=words[0])]


async def fetch_mining_validators(rpc_url: str, validator_set: str) -> list[str]:
    raw = await eth_call(rpc_url, validator_set, encode_selector_call(SELECTOR_VS_GET_MINING))
    words = decode_words(raw)
    if not words:
        return []
    return [normalize_address(a) for a in decode_address_array(raw, offset_bytes=words[0])]


async def fetch_turn_length(rpc_url: str, validator_set: str) -> int:
    value = decode_uint(
        await eth_call(rpc_url, validator_set, encode_selector_call(SELECTOR_VS_TURN_LENGTH))
    )
    return value if value > 0 else 1


async def fetch_is_current_validator(
    rpc_url: str, validator_set: str, consensus: str
) -> bool:
    return decode_bool(
        await eth_call(
            rpc_url,
            validator_set,
            encode_address_call(SELECTOR_VS_IS_CURRENT, consensus),
        )
    )


async def fetch_slash_indicator(
    rpc_url: str, slash_contract: str, consensus: str
) -> tuple[int, int]:
    """Return (height, count) from SlashIndicator.getSlashIndicator."""
    return decode_two_uints(
        await eth_call(
            rpc_url,
            slash_contract,
            encode_address_call(SELECTOR_SI_INDICATOR, consensus),
        )
    )


async def fetch_slash_thresholds(
    rpc_url: str,
    slash_contract: str,
    *,
    misdemeanor_override: int | None = None,
    felony_override: int | None = None,
) -> SlashThresholds:
    """Never hard-code docs' conflicting 50/200/600 vs 333/1000 values."""
    if misdemeanor_override is not None and felony_override is not None:
        return SlashThresholds(
            misdemeanor=misdemeanor_override,
            felony=felony_override,
            source="config",
        )
    misdemeanor, felony = decode_two_uints(
        await eth_call(
            rpc_url, slash_contract, encode_selector_call(SELECTOR_SI_THRESHOLDS)
        )
    )
    if misdemeanor_override is not None:
        misdemeanor = misdemeanor_override
    if felony_override is not None:
        felony = felony_override
    source = "contract"
    if misdemeanor_override is not None or felony_override is not None:
        source = "config+contract"
    return SlashThresholds(misdemeanor=misdemeanor, felony=felony, source=source)


async def fetch_stakehub_amounts(
    rpc_url: str, stake_hub: str
) -> tuple[int | None, int | None, int | None]:
    downtime = felony_time = felony_amount = None
    try:
        downtime = decode_uint(
            await eth_call(
                rpc_url, stake_hub, encode_selector_call(SELECTOR_SH_DOWNTIME_AMOUNT)
            )
        )
    except Exception:  # noqa: BLE001
        downtime = None
    try:
        felony_time = decode_uint(
            await eth_call(
                rpc_url, stake_hub, encode_selector_call(SELECTOR_SH_FELONY_JAIL)
            )
        )
    except Exception:  # noqa: BLE001
        felony_time = None
    try:
        felony_amount = decode_uint(
            await eth_call(
                rpc_url, stake_hub, encode_selector_call(SELECTOR_SH_FELONY_AMOUNT)
            )
        )
    except Exception:  # noqa: BLE001
        felony_amount = None
    return downtime, felony_time, felony_amount


async def fetch_consensus_to_operator(
    rpc_url: str, stake_hub: str, consensus: str
) -> str:
    return normalize_address(
        decode_address(
            await eth_call(
                rpc_url,
                stake_hub,
                encode_address_call(SELECTOR_SH_CONSENSUS_TO_OP, consensus),
            )
        )
    )


async def fetch_validator_consensus(
    rpc_url: str, stake_hub: str, operator: str
) -> str:
    return normalize_address(
        decode_address(
            await eth_call(
                rpc_url,
                stake_hub,
                encode_address_call(SELECTOR_SH_CONSENSUS, operator),
            )
        )
    )


async def fetch_validator_vote(rpc_url: str, stake_hub: str, operator: str) -> bytes:
    return decode_dynamic_bytes(
        await eth_call(
            rpc_url, stake_hub, encode_address_call(SELECTOR_SH_VOTE, operator)
        )
    )


async def fetch_validator_basic(
    rpc_url: str, stake_hub: str, operator: str
) -> ValidatorBasicInfo:
    return decode_basic_info(
        await eth_call(
            rpc_url, stake_hub, encode_address_call(SELECTOR_SH_BASIC, operator)
        )
    )


async def fetch_validator_description(
    rpc_url: str, stake_hub: str, operator: str
) -> ValidatorDescription:
    return decode_description(
        await eth_call(
            rpc_url, stake_hub, encode_address_call(SELECTOR_SH_DESCRIPTION, operator)
        )
    )


async def paginate_stakehub_operators(
    rpc_url: str,
    stake_hub: str,
    *,
    page_size: int = 50,
    max_pages: int = 32,
) -> list[str]:
    """Paginate StakeHub.getValidators — do not assume set size."""
    operators: list[str] = []
    offset = 0
    total = None
    for _ in range(max_pages):
        page = decode_stakehub_validator_page(
            await eth_call(
                rpc_url,
                stake_hub,
                encode_selector_call(SELECTOR_SH_GET_VALIDATORS, offset, page_size),
            )
        )
        operators.extend(normalize_address(a) for a in page.operators)
        total = page.total_length
        if not page.operators:
            break
        offset += len(page.operators)
        if total is not None and offset >= total:
            break
        if len(page.operators) < page_size:
            break
    unique: list[str] = []
    seen: set[str] = set()
    for addr in operators:
        if addr in seen:
            continue
        seen.add(addr)
        unique.append(addr)
    return unique


@dataclass(frozen=True)
class ResolvedValidator:
    operator: str
    consensus: str
    vote: bytes
    basic: ValidatorBasicInfo
    description: ValidatorDescription
    on_stakehub: bool


async def resolve_validator(
    rpc_url: str,
    stake_hub: str,
    raw_address: str,
    stakehub_operators: set[str] | None = None,
) -> ResolvedValidator:
    """Map operator or consensus address through StakeHub identity functions."""
    addr = normalize_address(raw_address)
    mapped_operator = await fetch_consensus_to_operator(rpc_url, stake_hub, addr)
    if not is_zero_address(mapped_operator):
        operator = mapped_operator
        consensus = await fetch_validator_consensus(rpc_url, stake_hub, operator)
        if is_zero_address(consensus):
            consensus = addr
    else:
        operator = addr
        consensus = await fetch_validator_consensus(rpc_url, stake_hub, operator)

    basic = await fetch_validator_basic(rpc_url, stake_hub, operator)
    try:
        vote = await fetch_validator_vote(rpc_url, stake_hub, operator)
    except Exception:  # noqa: BLE001
        vote = b""
    try:
        description = await fetch_validator_description(rpc_url, stake_hub, operator)
    except Exception:  # noqa: BLE001
        description = ValidatorDescription("", "", "", "")
    on_hub = False
    if stakehub_operators is not None:
        on_hub = operator in stakehub_operators
    else:
        on_hub = (not is_zero_address(consensus)) or basic.created_time > 0
    return ResolvedValidator(
        operator=operator,
        consensus=consensus,
        vote=vote,
        basic=basic,
        description=description,
        on_stakehub=on_hub,
    )


@dataclass(frozen=True)
class SlashEvent:
    kind: str
    slash_type: int | None
    amount: int | None
    jail_until: int | None
    tx_block: int | None


async def fetch_recent_slash_events(
    rpc_url: str,
    *,
    stake_hub: str,
    slash_contract: str,
    operator: str,
    consensus: str,
    from_block: int,
    to_block: int,
) -> list[SlashEvent]:
    events: list[SlashEvent] = []
    try:
        logs = await bsc_rpc(
            rpc_url,
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(max(0, from_block)),
                    "toBlock": hex(max(0, to_block)),
                    "address": stake_hub,
                    "topics": [TOPIC_STAKEHUB_SLASHED, topic_address(operator)],
                }
            ],
        )
        for log in logs or []:
            data = str(log.get("data") or "0x")
            words = decode_words(data)
            jail_until = words[0] if words else 0
            amount = words[1] if len(words) > 1 else 0
            slash_type = words[2] if len(words) > 2 else -1
            events.append(
                SlashEvent(
                    kind=slash_type_label(slash_type) if slash_type >= 0 else "slashed",
                    slash_type=slash_type if slash_type >= 0 else None,
                    amount=amount,
                    jail_until=jail_until,
                    tx_block=_as_int(log.get("blockNumber")),
                )
            )
    except Exception:  # noqa: BLE001
        pass
    try:
        jailed_logs = await bsc_rpc(
            rpc_url,
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(max(0, from_block)),
                    "toBlock": hex(max(0, to_block)),
                    "address": stake_hub,
                    "topics": [TOPIC_STAKEHUB_JAILED, topic_address(operator)],
                }
            ],
        )
        for log in jailed_logs or []:
            events.append(
                SlashEvent(
                    kind="jailed",
                    slash_type=None,
                    amount=None,
                    jail_until=None,
                    tx_block=_as_int(log.get("blockNumber")),
                )
            )
    except Exception:  # noqa: BLE001
        pass
    try:
        si_logs = await bsc_rpc(
            rpc_url,
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(max(0, from_block)),
                    "toBlock": hex(max(0, to_block)),
                    "address": slash_contract,
                    "topics": [TOPIC_SI_SLASHED, topic_address(consensus)],
                }
            ],
        )
        for log in si_logs or []:
            events.append(
                SlashEvent(
                    kind="slash-indicator",
                    slash_type=None,
                    amount=None,
                    jail_until=None,
                    tx_block=_as_int(log.get("blockNumber")),
                )
            )
    except Exception:  # noqa: BLE001
        pass
    try:
        vote_logs = await bsc_rpc(
            rpc_url,
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(max(0, from_block)),
                    "toBlock": hex(max(0, to_block)),
                    "address": slash_contract,
                    "topics": [TOPIC_SI_MALICIOUS_VOTE],
                }
            ],
        )
        for log in vote_logs or []:
            events.append(
                SlashEvent(
                    kind="malicious finality vote",
                    slash_type=2,
                    amount=None,
                    jail_until=None,
                    tx_block=_as_int(log.get("blockNumber")),
                )
            )
    except Exception:  # noqa: BLE001
        pass
    return events


async def sample_recent_miners(
    rpc_url: str, head: int, *, window: int = 16
) -> list[str]:
    miners: list[str] = []
    start = max(0, head - window + 1)
    for height in range(start, head + 1):
        try:
            block = await bsc_rpc(
                rpc_url, "eth_getBlockByNumber", [hex(height), False]
            )
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(block, dict):
            continue
        miner = block.get("miner") or block.get("coinbase")
        if miner:
            miners.append(normalize_address(str(miner)))
    return miners


def _consensus_status(*, reachable: bool, syncing: bool, chain_ok: bool) -> HealthStatus:
    if not reachable or not chain_ok:
        return "critical"
    if syncing:
        return "degraded"
    return "healthy"


async def collect_bsc_consensus(
    rpc_url: str,
    *,
    expected_chain_ids: frozenset[int] = EXPECTED_CHAIN_IDS,
) -> ConsensusHealth:
    base = normalize_rpc_url(rpc_url)
    try:
        await probe_rpc_endpoint(base)
        chain_id = await fetch_chain_id(base)
        chain_ok = chain_id in expected_chain_ids
        block_hex = await bsc_rpc(base, "eth_blockNumber")
        head = _as_int(block_hex)
        syncing_raw = await bsc_rpc(base, "eth_syncing")
        syncing = bool(syncing_raw) and syncing_raw is not False
        err = None if chain_ok else (
            f"Unexpected chain ID {chain_id} (expected BSC {MAINNET_CHAIN_ID} "
            f"or Chapel {97})"
        )
        return ConsensusHealth(
            beacon_reachable=True,
            syncing=syncing or not chain_ok,
            sync_distance=-1 if not chain_ok else (1 if syncing else 0),
            head_slot=head,
            finalized_epoch=0,
            justified_epoch=0,
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
class BscNodeMetrics:
    peers: int | None = None
    head_block: int | None = None


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


def extract_bsc_metrics(metrics: dict[str, float]) -> BscNodeMetrics:
    peers = None
    for key in ("p2p_peers", "p2p_peer_count", "connected_peers"):
        if key in metrics:
            peers = int(metrics[key])
            break
    head = None
    for key in ("chain_head_block", "chain_head", "head_block"):
        if key in metrics:
            head = int(metrics[key])
            break
    return BscNodeMetrics(peers=peers, head_block=head)


async def try_fetch_bsc_metrics(
    metrics_url: str,
) -> tuple[BscNodeMetrics | None, str | None]:
    if not metrics_url or not metrics_url.strip():
        return None, None
    url = normalize_rpc_url(metrics_url)
    try:
        async with async_rpc_client(timeout=6.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            text = res.text or ""
            if not text.strip():
                return None, "BSC Prometheus metrics returned empty body"
            return extract_bsc_metrics(parse_prometheus_text(text)), None
    except Exception as exc:  # noqa: BLE001
        return None, f"BSC metrics enrichment unavailable: {format_transport_error(exc)}"
