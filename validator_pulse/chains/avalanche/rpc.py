from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from validator_pulse.chains.avalanche.state import parse_rfc3339, parse_uptime_percent
from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    parse_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus

EXPECTED_NETWORK_IDS = {"mainnet": 1, "fuji": 5, "testnet": 5}


def avalanche_endpoints(rpc_url: str) -> tuple[str, str, str, str]:
    """Map a node base or P-Chain URL to P / info / health / metrics endpoints."""
    parsed = parse_rpc_url(rpc_url)
    origin = parsed.origin
    path = parsed.path or ""
    p_url = f"{origin}{path}" if "/ext/bc/P" in path else f"{origin}/ext/bc/P"
    return p_url, f"{origin}/ext/info", f"{origin}/ext/health", f"{origin}/ext/metrics"


async def avalanche_rpc(
    rpc_url: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float | None = None,
) -> Any:
    payload = {
        "id": 1,
        "jsonrpc": "2.0",
        "method": method,
        "params": params if params is not None else {},
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
        raise RuntimeError(f"Avalanche RPC {method} returned non-object payload")
    if body.get("error"):
        err = body["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"Avalanche RPC {method} failed: {message}")
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
        return int(float(text)) if "." in text else int(text)
    return int(value)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def normalize_node_id(node_id: str) -> str:
    text = (node_id or "").strip()
    if not text:
        return ""
    if text.upper().startswith("NODEID-"):
        return "NodeID-" + text.split("-", 1)[1]
    return f"NodeID-{text}"


@dataclass(frozen=True)
class CurrentValidator:
    node_id: str
    tx_id: str
    start_time: int
    end_time: int
    stake_n_avax: int
    connected: bool
    uptime_pct: float | None
    potential_reward: int
    weight: int


def parse_current_validator(raw: dict[str, Any]) -> CurrentValidator:
    node_id = normalize_node_id(str(raw.get("nodeID") or raw.get("nodeId") or ""))
    return CurrentValidator(
        node_id=node_id,
        tx_id=str(raw.get("txID") or ""),
        start_time=_as_int(raw.get("startTime")),
        end_time=_as_int(raw.get("endTime")),
        stake_n_avax=_as_int(raw.get("stakeAmount") or raw.get("weight")),
        connected=bool(raw.get("connected")),
        uptime_pct=parse_uptime_percent(raw.get("uptime")),
        potential_reward=_as_int(raw.get("potentialReward")),
        weight=_as_int(raw.get("weight") or raw.get("stakeAmount")),
    )


async def fetch_current_validators(
    p_url: str,
    node_ids: list[str] | None = None,
) -> list[CurrentValidator]:
    params: dict[str, Any] = {}
    if node_ids:
        params["nodeIDs"] = [normalize_node_id(n) for n in node_ids if n]
    result = await avalanche_rpc(p_url, "platform.getCurrentValidators", params)
    rows = (result or {}).get("validators") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[CurrentValidator] = []
    for row in rows:
        if isinstance(row, dict):
            parsed = parse_current_validator(row)
            if parsed.node_id:
                out.append(parsed)
    return out


@dataclass(frozen=True)
class LocalUptime:
    rewarding_stake_pct: float | None
    weighted_average_pct: float | None


async def fetch_info_uptime(info_url: str) -> LocalUptime:
    result = await avalanche_rpc(info_url, "info.uptime")
    data = result if isinstance(result, dict) else {}
    return LocalUptime(
        rewarding_stake_pct=parse_uptime_percent(data.get("rewardingStakePercentage")),
        weighted_average_pct=parse_uptime_percent(data.get("weightedAveragePercentage")),
    )


async def fetch_local_node_id(info_url: str) -> str | None:
    result = await avalanche_rpc(info_url, "info.getNodeID")
    if not isinstance(result, dict):
        return None
    node_id = result.get("nodeID")
    return normalize_node_id(str(node_id)) if node_id else None


async def fetch_network_id(info_url: str) -> int | None:
    try:
        result = await avalanche_rpc(info_url, "info.getNetworkID")
    except Exception:  # noqa: BLE001
        return None
    if isinstance(result, dict):
        return _as_int(result.get("networkID"))
    return _as_int(result)


async def fetch_helicon_ts(info_url: str) -> float | None:
    try:
        result = await avalanche_rpc(info_url, "info.upgrades")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(result, dict):
        return None
    return parse_rfc3339(str(result.get("heliconTime") or ""))


async def fetch_peer_view(
    info_url: str, node_id: str
) -> tuple[float | None, list[str]]:
    try:
        result = await avalanche_rpc(
            info_url, "info.peers", {"nodeIDs": [normalize_node_id(node_id)]}
        )
    except Exception:  # noqa: BLE001
        return None, []
    peers = (result or {}).get("peers") if isinstance(result, dict) else None
    if not isinstance(peers, list) or not peers:
        return None, []
    peer = peers[0] if isinstance(peers[0], dict) else {}
    observed = parse_uptime_percent(peer.get("observedUptime"))
    benched = peer.get("benched") if isinstance(peer.get("benched"), list) else []
    return observed, [str(x) for x in benched]


@dataclass(frozen=True)
class NodeHealth:
    healthy: bool
    detail: str | None = None


async def fetch_health(health_url: str) -> NodeHealth | None:
    url = normalize_rpc_url(health_url)
    try:
        async with async_rpc_client(timeout=6.0) as client:
            res = await client.get(url)
            if res.status_code >= 400:
                res = await client.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "health.health"},
                )
            res.raise_for_status()
            body = res.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(body, dict):
        return None
    result = body.get("result") if "result" in body else body
    if not isinstance(result, dict):
        return None
    healthy = bool(result.get("healthy", True))
    return NodeHealth(
        healthy=healthy,
        detail=None if healthy else "health checks failing",
    )


@dataclass(frozen=True)
class AvalancheNodeMetrics:
    peers: int | None = None
    polls_successful: int | None = None
    polls_failed: int | None = None
    connected_stake: float | None = None

    @property
    def poll_success_ratio(self) -> float | None:
        ok = self.polls_successful
        fail = self.polls_failed
        if ok is None and fail is None:
            return None
        total = (ok or 0) + (fail or 0)
        if total <= 0:
            return 1.0 if fail in (None, 0) else 0.0
        return (ok or 0) / total


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


def extract_avalanche_metrics(metrics: dict[str, float]) -> AvalancheNodeMetrics:
    peers = None
    for key in ("avalanche_network_peers", "avalanche_P_network_peers", "peers"):
        if key in metrics:
            peers = int(metrics[key])
            break
    ok = fail = None
    for key in (
        "avalanche_snowman_polls_successful",
        "avalanche_P_snowman_polls_successful",
        "avalanche_network_polls_successful",
    ):
        if key in metrics:
            ok = int(metrics[key])
            break
    for key in (
        "avalanche_snowman_polls_failed",
        "avalanche_P_snowman_polls_failed",
        "avalanche_network_polls_failed",
    ):
        if key in metrics:
            fail = int(metrics[key])
            break
    stake = None
    for key in (
        "avalanche_network_connected_stake_weight",
        "avalanche_P_connected_stake_percent",
        "avalanche_network_connected_percent",
    ):
        if key in metrics:
            stake = float(metrics[key])
            break
    return AvalancheNodeMetrics(
        peers=peers, polls_successful=ok, polls_failed=fail, connected_stake=stake
    )


async def try_fetch_metrics(
    metrics_url: str,
) -> tuple[AvalancheNodeMetrics | None, str | None]:
    if not metrics_url or not metrics_url.strip():
        return None, None
    url = normalize_rpc_url(metrics_url)
    try:
        async with async_rpc_client(timeout=6.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            text = res.text or ""
            if not text.strip():
                return None, "Avalanche metrics returned empty body"
            return extract_avalanche_metrics(parse_prometheus_text(text)), None
    except Exception as exc:  # noqa: BLE001
        return None, (
            "Avalanche metrics enrichment unavailable: "
            f"{format_transport_error(exc)}"
        )


def _consensus_status(*, reachable: bool, healthy: bool | None, peers: int) -> HealthStatus:
    if not reachable:
        return "critical"
    if healthy is False:
        return "degraded"
    if peers and peers < 3:
        return "degraded"
    return "healthy"


async def fetch_p_height(p_url: str) -> int:
    result = await avalanche_rpc(p_url, "platform.getHeight")
    if isinstance(result, dict):
        return _as_int(result.get("height"))
    return _as_int(result)


async def collect_avalanche_consensus(
    rpc_url: str,
    *,
    expected_network: str = "mainnet",
) -> ConsensusHealth:
    p_url, info_url, health_url, _metrics = avalanche_endpoints(rpc_url)
    try:
        await probe_rpc_endpoint(p_url)
        try:
            head = await fetch_p_height(p_url)
        except Exception:  # noqa: BLE001
            head = 0
        network_id = await fetch_network_id(info_url)
        expected_id = EXPECTED_NETWORK_IDS.get(
            (expected_network or "mainnet").strip().lower()
        )
        chain_ok = expected_id is None or network_id is None or network_id == expected_id
        err = None
        if not chain_ok:
            err = (
                f"Unexpected network ID {network_id} "
                f"(AVALANCHE_NETWORK={expected_network})"
            )
        health = await fetch_health(health_url)
        return ConsensusHealth(
            beacon_reachable=True,
            syncing=not chain_ok,
            sync_distance=0 if chain_ok else -1,
            head_slot=head,
            finalized_epoch=0,
            justified_epoch=0,
            peer_count=0,
            connected_peers=0,
            status=_consensus_status(
                reachable=True,
                healthy=None if health is None else health.healthy,
                peers=0,
            ),
            last_error=err
            or (health.detail if health and not health.healthy else None),
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
