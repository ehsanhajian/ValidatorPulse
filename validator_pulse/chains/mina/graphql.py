from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from validator_pulse.chains.mina.state import CanonicalBlock, WonSlot, normalize_public_key
from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus

# Read-only. Never send write operations — the full Mina GraphQL endpoint can submit txs.
_SNAPSHOT_QUERY = """
query MinaPulseSnapshot {
  syncStatus
  daemonStatus {
    blockchainLength
    highestBlockLengthReceived
    highestUnvalidatedBlockLengthReceived
    uptimeSecs
    peers { peerId }
    syncStatus
    blockProductionKeys
    coinbaseReceiver
    consensusTimeNow { epoch slot startTime }
    consensusTimeBestTip { epoch slot }
    globalSlotSinceGenesisBestTip
    nextBlockProduction {
      times { epoch slot startTime }
      globalSlotSinceGenesis
    }
  }
  bestChain(maxLength: 290) {
    creator
    stateHash
    protocolState {
      consensusState {
        blockHeight
        slotSinceGenesis
        slot
        epoch
      }
    }
    transactions { coinbase }
  }
}
"""

_MINIMAL_QUERY = """
query MinaPulseMinimal {
  syncStatus
  daemonStatus {
    blockchainLength
    highestBlockLengthReceived
    peers { peerId }
    syncStatus
    blockProductionKeys
    consensusTimeNow { epoch slot }
    globalSlotSinceGenesisBestTip
  }
  bestChain(maxLength: 290) {
    creator
    stateHash
    protocolState {
      consensusState {
        blockHeight
        slotSinceGenesis
        slot
        epoch
      }
    }
    transactions { coinbase }
  }
}
"""


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
        if text.lstrip("-").isdigit():
            return int(text)
        try:
            return int(float(text))
        except ValueError:
            return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def graphql_host_is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


async def graphql_query(graphql_url: str, query: str) -> dict[str, Any]:
    """POST a GraphQL *query*. Never sends write operations or JSON-RPC."""
    url = normalize_rpc_url(graphql_url)
    async with async_rpc_client(timeout=20.0) as client:
        res = await client.post(
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={"query": query},
        )
        res.raise_for_status()
        payload = res.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Mina GraphQL returned non-object payload")
    if payload.get("errors"):
        msgs = "; ".join(
            str(e.get("message") or e) for e in payload["errors"] if isinstance(e, dict)
        )
        raise RuntimeError(f"Mina GraphQL error: {msgs or payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Mina GraphQL response missing data")
    return data


@dataclass
class MinaDaemonSnapshot:
    sync_status: str = "UNKNOWN"
    blockchain_length: int = 0
    highest_received: int = 0
    highest_unvalidated: int = 0
    peers: int = 0
    uptime_secs: int = 0
    production_keys: list[str] = field(default_factory=list)
    coinbase_receiver: str | None = None
    epoch: int = 0
    slot: int = 0
    global_slot: int = 0
    next_slots: list[WonSlot] = field(default_factory=list)
    blocks: list[CanonicalBlock] = field(default_factory=list)
    local_graphql: bool = False


def parse_best_chain(raw: Any) -> list[CanonicalBlock]:
    if not isinstance(raw, list):
        return []
    out: list[CanonicalBlock] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        consensus = {}
        proto = row.get("protocolState")
        if isinstance(proto, dict) and isinstance(proto.get("consensusState"), dict):
            consensus = proto["consensusState"]
        creator = normalize_public_key(
            str(row.get("creator") or "")
            or str((row.get("creatorAccount") or {}).get("publicKey") or "")
        )
        txs = row.get("transactions") if isinstance(row.get("transactions"), dict) else {}
        slot = _as_int(consensus.get("slotSinceGenesis") or consensus.get("slot"))
        if not creator or slot <= 0:
            continue
        out.append(
            CanonicalBlock(
                slot=slot,
                height=_as_int(consensus.get("blockHeight")),
                creator=creator,
                state_hash=str(row.get("stateHash") or ""),
                coinbase=_as_int(txs.get("coinbase")),
            )
        )
    return out


def parse_next_production(daemon: dict[str, Any], *, pubkey: str | None = None) -> list[WonSlot]:
    nxt = daemon.get("nextBlockProduction")
    if not isinstance(nxt, dict):
        return []
    times = nxt.get("times") if isinstance(nxt.get("times"), list) else []
    globals_ = nxt.get("globalSlotSinceGenesis")
    global_list = globals_ if isinstance(globals_, list) else []
    key = normalize_public_key(pubkey) if pubkey else ""
    out: list[WonSlot] = []
    for index, item in enumerate(times):
        if not isinstance(item, dict):
            continue
        slot = _as_int(item.get("slot"))
        global_slot = _as_int(global_list[index] if index < len(global_list) else 0)
        use_slot = global_slot or slot
        if use_slot <= 0:
            continue
        out.append(
            WonSlot(
                pubkey=key,
                slot=use_slot,
                epoch=_as_int(item.get("epoch")) or None,
                produced=False,
                source="graphql",
            )
        )
    if not out:
        for value in global_list:
            slot = _as_int(value)
            if slot > 0:
                out.append(WonSlot(pubkey=key, slot=slot, produced=False, source="graphql"))
    return out


def parse_daemon_snapshot(data: dict[str, Any], *, graphql_url: str = "") -> MinaDaemonSnapshot:
    daemon = data.get("daemonStatus") if isinstance(data.get("daemonStatus"), dict) else {}
    sync = str(data.get("syncStatus") or daemon.get("syncStatus") or "UNKNOWN").upper()
    now = daemon.get("consensusTimeNow") if isinstance(daemon.get("consensusTimeNow"), dict) else {}
    keys_raw = daemon.get("blockProductionKeys")
    keys = [
        normalize_public_key(str(k))
        for k in (keys_raw if isinstance(keys_raw, list) else [])
        if k
    ]
    peers_raw = daemon.get("peers")
    peers = len(peers_raw) if isinstance(peers_raw, list) else 0
    return MinaDaemonSnapshot(
        sync_status=sync,
        blockchain_length=_as_int(daemon.get("blockchainLength")),
        highest_received=_as_int(daemon.get("highestBlockLengthReceived")),
        highest_unvalidated=_as_int(daemon.get("highestUnvalidatedBlockLengthReceived")),
        peers=peers,
        uptime_secs=_as_int(daemon.get("uptimeSecs")),
        production_keys=keys,
        coinbase_receiver=normalize_public_key(str(daemon.get("coinbaseReceiver") or ""))
        or None,
        epoch=_as_int(now.get("epoch")),
        slot=_as_int(now.get("slot")),
        global_slot=_as_int(daemon.get("globalSlotSinceGenesisBestTip") or now.get("slot")),
        next_slots=parse_next_production(daemon),
        blocks=parse_best_chain(data.get("bestChain")),
        local_graphql=graphql_host_is_local(graphql_url),
    )


async def fetch_mina_snapshot(graphql_url: str) -> MinaDaemonSnapshot:
    try:
        data = await graphql_query(graphql_url, _SNAPSHOT_QUERY)
    except Exception:
        data = await graphql_query(graphql_url, _MINIMAL_QUERY)
    return parse_daemon_snapshot(data, graphql_url=graphql_url)


def _consensus_status(*, reachable: bool, synced: bool, peers: int, lag: int) -> HealthStatus:
    if not reachable:
        return "critical"
    if not synced:
        return "degraded" if reachable else "critical"
    if lag > 2:
        return "degraded"
    if peers and peers < 3:
        return "degraded"
    return "healthy"


async def collect_mina_consensus(graphql_url: str) -> tuple[ConsensusHealth, MinaDaemonSnapshot | None]:
    try:
        await probe_rpc_endpoint(graphql_url)
        snap = await fetch_mina_snapshot(graphql_url)
    except Exception as exc:  # noqa: BLE001
        return (
            ConsensusHealth(
                beacon_reachable=False,
                syncing=True,
                sync_distance=-1,
                head_slot=0,
                finalized_epoch=0,
                justified_epoch=0,
                peer_count=0,
                connected_peers=0,
                status="critical",
                last_error=f"Mina GraphQL: {format_transport_error(exc)}",
            ),
            None,
        )
    synced = snap.sync_status == "SYNCED"
    lag = max(0, snap.highest_received - snap.blockchain_length)
    return (
        ConsensusHealth(
            beacon_reachable=True,
            syncing=not synced,
            sync_distance=lag if not synced else 0,
            head_slot=snap.blockchain_length or snap.global_slot,
            finalized_epoch=snap.epoch,
            justified_epoch=snap.epoch,
            peer_count=snap.peers,
            connected_peers=snap.peers,
            status=_consensus_status(
                reachable=True, synced=synced, peers=snap.peers, lag=lag
            ),
            last_error=None if synced else f"Mina daemon syncStatus={snap.sync_status}",
        ),
        snap,
    )
