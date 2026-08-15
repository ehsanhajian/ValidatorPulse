from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from validator_pulse.chains.multiversx.state import META_SHARD_ID, normalize_bls_key
from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus


def _as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return 0


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def join_url(base: str, path: str) -> str:
    root = normalize_rpc_url(base).rstrip("/")
    return f"{root}/{path.lstrip('/')}"


async def mx_get(base_url: str, path: str) -> Any:
    url = join_url(base_url, path)
    async with async_rpc_client(timeout=20.0) as client:
        res = await client.get(url, headers={"Accept": "application/json"})
        res.raise_for_status()
        payload = res.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"MultiversX {path} returned non-object payload")
    if payload.get("error"):
        raise RuntimeError(f"MultiversX {path} failed: {payload.get('error')}")
    return payload.get("data")


@dataclass(frozen=True)
class Heartbeat:
    public_key: str
    peer_type: str
    is_active: bool | None
    received_shard: int | None
    computed_shard: int | None
    version: str | None
    name: str | None
    identity: str | None
    nonce: int | None
    timestamp: str | None
    num_instances: int | None = None


@dataclass(frozen=True)
class ValidatorStat:
    public_key: str
    rating: float | None
    temp_rating: float | None
    rating_modifier: float | None
    leader_success: int | None
    leader_failure: int | None
    validator_success: int | None
    validator_failure: int | None
    ignored_signatures: int | None
    shard_id: int | None
    validator_status: str | None


@dataclass
class NodeStatus:
    nonce: int = 0
    probable_highest: int = 0
    round: int = 0
    epoch: int = 0
    peers: int = 0
    syncing: bool = False
    version: str | None = None
    shard_id: int | None = None
    peer_type: str | None = None
    public_keys: list[str] = field(default_factory=list)
    count_leader: int | None = None
    count_accepted_blocks: int | None = None
    count_consensus: int | None = None
    count_consensus_accepted: int | None = None


def parse_heartbeats(data: Any) -> dict[str, Heartbeat]:
    rows = data.get("heartbeats") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return {}
    out: dict[str, Heartbeat] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = normalize_bls_key(str(row.get("publicKey") or ""))
        if not key:
            continue
        shard_r = row.get("receivedShardID")
        shard_c = row.get("computedShardID")
        out[key] = Heartbeat(
            public_key=key,
            peer_type=str(row.get("peerType") or "unknown"),
            is_active=_as_bool(row.get("isActive")),
            received_shard=None if shard_r is None else _as_int(shard_r),
            computed_shard=None if shard_c is None else _as_int(shard_c),
            version=str(row.get("versionNumber") or "") or None,
            name=str(row.get("nodeDisplayName") or "") or None,
            identity=str(row.get("identity") or "") or None,
            nonce=_as_int(row.get("nonce")) if row.get("nonce") is not None else None,
            timestamp=str(row.get("timeStamp") or "") or None,
            num_instances=_as_int(row.get("numInstances"))
            if row.get("numInstances") is not None
            else None,
        )
    return out


def parse_validator_statistics(data: Any) -> dict[str, ValidatorStat]:
    stats = data.get("statistics") if isinstance(data, dict) else data
    if not isinstance(stats, dict):
        return {}
    out: dict[str, ValidatorStat] = {}
    for raw_key, row in stats.items():
        if not isinstance(row, dict):
            continue
        key = normalize_bls_key(str(raw_key))
        out[key] = ValidatorStat(
            public_key=key,
            rating=_as_float(row.get("rating")),
            temp_rating=_as_float(row.get("tempRating")),
            rating_modifier=_as_float(row.get("ratingModifier")),
            leader_success=_as_int(row.get("numLeaderSuccess"))
            if row.get("numLeaderSuccess") is not None
            else None,
            leader_failure=_as_int(row.get("numLeaderFailure"))
            if row.get("numLeaderFailure") is not None
            else None,
            validator_success=_as_int(row.get("numValidatorSuccess"))
            if row.get("numValidatorSuccess") is not None
            else None,
            validator_failure=_as_int(row.get("numValidatorFailure"))
            if row.get("numValidatorFailure") is not None
            else None,
            ignored_signatures=_as_int(row.get("numValidatorIgnoredSignatures"))
            if row.get("numValidatorIgnoredSignatures") is not None
            else None,
            shard_id=_as_int(row.get("shardId")) if row.get("shardId") is not None else None,
            validator_status=str(row.get("validatorStatus") or "") or None,
        )
    return out


def parse_node_metrics(metrics: Any) -> NodeStatus:
    if not isinstance(metrics, dict):
        metrics = {}
    inner = metrics.get("metrics") if isinstance(metrics.get("metrics"), dict) else metrics
    keys_raw = inner.get("erd_public_key_block_sign")
    keys: list[str] = []
    if isinstance(keys_raw, list):
        keys = [normalize_bls_key(str(k)) for k in keys_raw if k]
    elif keys_raw:
        keys = [
            normalize_bls_key(part)
            for part in str(keys_raw).replace(";", ",").split(",")
            if part.strip()
        ]
    return NodeStatus(
        nonce=_as_int(inner.get("erd_nonce")),
        probable_highest=_as_int(inner.get("erd_probable_highest_nonce")),
        round=_as_int(inner.get("erd_current_round") or inner.get("erd_synchronized_round")),
        epoch=_as_int(inner.get("erd_epoch_number")),
        peers=_as_int(inner.get("erd_num_connected_peers")),
        syncing=bool(_as_int(inner.get("erd_is_syncing"))),
        version=str(inner.get("erd_app_version") or "") or None,
        shard_id=_as_int(inner.get("erd_shard_id")) if inner.get("erd_shard_id") is not None else None,
        peer_type=str(inner.get("erd_peer_type") or "") or None,
        public_keys=keys,
        count_leader=_as_int(inner.get("erd_count_leader"))
        if inner.get("erd_count_leader") is not None
        else None,
        count_accepted_blocks=_as_int(inner.get("erd_count_accepted_blocks"))
        if inner.get("erd_count_accepted_blocks") is not None
        else None,
        count_consensus=_as_int(inner.get("erd_count_consensus"))
        if inner.get("erd_count_consensus") is not None
        else None,
        count_consensus_accepted=_as_int(inner.get("erd_count_consensus_accepted_blocks"))
        if inner.get("erd_count_consensus_accepted_blocks") is not None
        else None,
    )


def parse_network_status(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    status = data.get("status") if isinstance(data.get("status"), dict) else data
    return status if isinstance(status, dict) else {}


def parse_p2p_peer_count(data: Any) -> int:
    metrics = data.get("metrics") if isinstance(data, dict) else data
    if not isinstance(metrics, dict):
        return 0
    for key in ("erd_num_connected_peers", "erd_p2p_num_connected_peers"):
        if key in metrics:
            return _as_int(metrics[key])
    info = data.get("info") if isinstance(data, dict) else None
    if isinstance(info, list):
        return len(info)
    return 0


async def try_get(base: str | None, path: str) -> tuple[Any | None, str | None]:
    if not base or not base.strip():
        return None, None
    try:
        return await mx_get(base, path), None
    except Exception as exc:  # noqa: BLE001
        return None, f"{path}: {format_transport_error(exc)}"


async def collect_mx_consensus(
    *,
    node_url: str | None,
    gateway_url: str | None,
    shard_id: int | None,
) -> tuple[ConsensusHealth, NodeStatus | None, dict[str, Any]]:
    notes: list[str] = []
    node_status: NodeStatus | None = None
    net_status: dict[str, Any] = {}
    reachable = False
    probe_url = (node_url or gateway_url or "").strip()
    try:
        if probe_url:
            await probe_rpc_endpoint(probe_url)
            reachable = True
    except Exception as exc:  # noqa: BLE001
        notes.append(format_transport_error(exc))

    if node_url:
        raw, err = await try_get(node_url, "/node/status")
        if err:
            notes.append(f"local {err}")
        elif isinstance(raw, dict):
            node_status = parse_node_metrics(raw)
        p2p, p2p_err = await try_get(node_url, "/node/p2pstatus")
        if p2p_err:
            notes.append(f"local {p2p_err}")
        elif node_status is not None:
            peers = parse_p2p_peer_count(p2p)
            if peers:
                node_status = NodeStatus(**{**node_status.__dict__, "peers": peers})
        peerinfo, peer_err = await try_get(node_url, "/node/peerinfo")
        if peer_err:
            notes.append(f"local {peer_err}")
        elif node_status is not None and not node_status.peers:
            count = parse_p2p_peer_count(peerinfo if isinstance(peerinfo, dict) else {})
            if count:
                node_status = NodeStatus(**{**node_status.__dict__, "peers": count})

    shard = shard_id if shard_id is not None else (
        node_status.shard_id if node_status and node_status.shard_id is not None else 0
    )
    if gateway_url:
        raw, err = await try_get(gateway_url, f"/network/status/{shard}")
        if err and shard != META_SHARD_ID:
            raw, err = await try_get(gateway_url, "/network/status/0")
        if err:
            notes.append(f"gateway {err}")
        elif isinstance(raw, dict):
            net_status = parse_network_status(raw)
            reachable = True

    nonce = node_status.nonce if node_status else _as_int(net_status.get("erd_nonce"))
    highest = (
        node_status.probable_highest
        if node_status and node_status.probable_highest
        else _as_int(net_status.get("erd_highest_final_nonce") or net_status.get("erd_nonce"))
    )
    epoch = node_status.epoch if node_status and node_status.epoch else _as_int(
        net_status.get("erd_epoch_number")
    )
    round_ = node_status.round if node_status and node_status.round else _as_int(
        net_status.get("erd_current_round")
    )
    peers = node_status.peers if node_status else 0
    syncing = bool(node_status.syncing) if node_status else False
    lag = max(0, highest - nonce) if highest and nonce else 0
    if lag > 2:
        syncing = True
    status: HealthStatus = "healthy"
    if not reachable:
        status = "critical"
    elif syncing or lag > 5:
        status = "degraded"
    elif peers and peers < 3:
        status = "degraded"
    return (
        ConsensusHealth(
            beacon_reachable=reachable,
            syncing=syncing,
            sync_distance=lag,
            head_slot=nonce or round_,
            finalized_epoch=epoch,
            justified_epoch=round_,
            peer_count=peers,
            connected_peers=peers,
            status=status,
            last_error="; ".join(notes) if notes else None,
        ),
        node_status,
        net_status,
    )
