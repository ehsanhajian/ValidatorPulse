from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from validator_pulse.chains.ton.state import (
    MASTERCHAIN_INDEX_LIMIT,
    NANOTON,
    is_adnl,
    normalize_adnl,
    parse_complaint,
)
from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(str(value).strip(), 0)
    except (TypeError, ValueError):
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def join_url(base: str, path: str) -> str:
    root = normalize_rpc_url(base).rstrip("/")
    return f"{root}/{path.lstrip('/')}"


def derive_qos_url(validation_api_url: str | None, qos_url: str | None) -> str | None:
    if qos_url and qos_url.strip():
        return qos_url.strip()
    host = urlparse(normalize_rpc_url(validation_api_url or "")).hostname or ""
    host = host.lower()
    if host in {"elections.toncenter.com"}:
        return "https://toncenter.com"
    if host in {"testnet-elections.toncenter.com"}:
        return "https://testnet.toncenter.com"
    return None


async def ton_get(base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
    url = join_url(base_url, path)
    async with async_rpc_client(timeout=25.0) as client:
        res = await client.get(url, params=params or {}, headers={"Accept": "application/json"})
        res.raise_for_status()
        return res.json()


@dataclass
class CycleValidator:
    adnl: str
    index: int | None
    stake: int | None
    weight: int | None
    pubkey: str | None
    wallet: str | None
    complaints: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ValidationCycle:
    cycle_id: int
    utime_since: int | None
    utime_until: int | None
    total_participants: int | None
    min_stake: int | None
    max_stake: int | None
    validators: dict[str, CycleValidator] = field(default_factory=dict)


@dataclass
class ElectionEntry:
    election_id: int
    finished: bool | None
    elect_close: int | None
    min_stake: int | None
    participants: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class QosRow:
    adnl: str
    cycle_id: int | None
    efficiency: float | None
    efficiency_mc: float | None
    efficiency_wc: float | None
    index: int | None
    stake: int | None
    utime_since: int | None
    utime_until: int | None
    source: str = "qos-cycleScoreboard"


@dataclass
class TonMetrics:
    master_out_of_sync: float | None = None
    shard_out_of_sync: float | None = None
    console_up: bool | None = None
    validator_index: int | None = None
    stake: float | None = None
    validated_ok: int | None = None
    validated_err: int | None = None
    collated_ok: int | None = None
    collated_err: int | None = None
    synced: bool | None = None


def parse_validation_cycles(payload: Any) -> list[ValidationCycle]:
    rows = payload if isinstance(payload, list) else payload.get("cycles") if isinstance(payload, dict) else []
    out: list[ValidationCycle] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        info = item.get("cycle_info") if isinstance(item.get("cycle_info"), dict) else item
        cycle_id = _as_int(item.get("cycle_id") or info.get("utime_since"))
        if cycle_id is None:
            continue
        validators: dict[str, CycleValidator] = {}
        for raw in info.get("validators") or []:
            if not isinstance(raw, dict):
                continue
            adnl = normalize_adnl(
                raw.get("adnl_addr") or raw.get("adnl_address") or raw.get("adnl")
            )
            if not is_adnl(adnl):
                continue
            complaints = [
                parsed
                for parsed in (parse_complaint(c) for c in (raw.get("complaints") or []))
                if parsed
            ]
            validators[adnl] = CycleValidator(
                adnl=adnl,
                index=_as_int(raw.get("index")),
                stake=_as_int(raw.get("stake")),
                weight=_as_int(raw.get("weight")),
                pubkey=raw.get("pubkey"),
                wallet=raw.get("wallet_address") or raw.get("wallet"),
                complaints=complaints,
            )
        out.append(
            ValidationCycle(
                cycle_id=cycle_id,
                utime_since=_as_int(info.get("utime_since")),
                utime_until=_as_int(info.get("utime_until")),
                total_participants=_as_int(
                    info.get("total_participants") or len(validators)
                ),
                min_stake=_as_int(info.get("min_stake")),
                max_stake=_as_int(info.get("max_stake")),
                validators=validators,
            )
        )
    return out


def parse_elections(payload: Any) -> list[ElectionEntry]:
    rows = payload if isinstance(payload, list) else []
    out: list[ElectionEntry] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        election_id = _as_int(item.get("election_id"))
        if election_id is None:
            continue
        participants: dict[str, dict[str, Any]] = {}
        for raw in item.get("participants_list") or []:
            if not isinstance(raw, dict):
                continue
            adnl = normalize_adnl(
                raw.get("adnl_addr") or raw.get("adnl_address") or raw.get("adnl")
            )
            if is_adnl(adnl):
                participants[adnl] = raw
        out.append(
            ElectionEntry(
                election_id=election_id,
                finished=item.get("finished"),
                elect_close=_as_int(item.get("elect_close")),
                min_stake=_as_int(item.get("min_stake")),
                participants=participants,
            )
        )
    return out


def parse_qos_scoreboard(payload: Any) -> list[QosRow]:
    if isinstance(payload, dict):
        rows = payload.get("scoreboard") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    out: list[QosRow] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        adnl = normalize_adnl(
            raw.get("validator_adnl") or raw.get("adnl_addr") or raw.get("adnl")
        )
        if not is_adnl(adnl):
            continue
        out.append(
            QosRow(
                adnl=adnl,
                cycle_id=_as_int(raw.get("cycle_id")),
                efficiency=_as_float(raw.get("efficiency")),
                efficiency_mc=_as_float(raw.get("efficiency_mc")),
                efficiency_wc=_as_float(raw.get("efficiency_wc")),
                index=_as_int(raw.get("idx") if raw.get("idx") is not None else raw.get("index")),
                stake=_as_int(raw.get("stake")),
                utime_since=_as_int(raw.get("utime_since")),
                utime_until=_as_int(raw.get("utime_until")),
            )
        )
    return out


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


def extract_ton_metrics(metrics: dict[str, float]) -> TonMetrics:
    def _first(*names: str) -> float | None:
        for name in names:
            if name in metrics:
                return metrics[name]
        return None

    master = _first("validator_masterchain_out_of_sync_seconds", "mytonctrl_master_out_of_sync")
    shard = _first("validator_shardchain_out_of_sync_blocks", "mytonctrl_shard_out_of_sync")
    synced_flag = _first("mytonctrl_synced", "validator_console_up")
    console = _first("validator_console_up")
    return TonMetrics(
        master_out_of_sync=master,
        shard_out_of_sync=shard,
        console_up=bool(console) if console is not None else None,
        validator_index=_as_int(_first("validator_index")),
        stake=_first("validator_stake"),
        validated_ok=_as_int(_first("validator_blocks_validated_master_ok")),
        validated_err=_as_int(_first("validator_blocks_validated_master_err")),
        collated_ok=_as_int(_first("validator_blocks_collated_master_ok")),
        collated_err=_as_int(_first("validator_blocks_collated_master_err")),
        synced=None if synced_flag is None else bool(synced_flag) and (master or 0) < 30,
    )


def index_role(index: int | None) -> str:
    if index is None:
        return "unknown"
    if index < MASTERCHAIN_INDEX_LIMIT:
        return "masterchain"
    return "shard"


def nanotons_to_label(amount: int | None) -> str:
    if amount is None:
        return "unknown"
    return f"{amount / NANOTON:.4f} GRAM"


async def try_fetch_metrics(metrics_url: str | None) -> tuple[TonMetrics | None, str | None]:
    if not metrics_url or not metrics_url.strip():
        return None, None
    url = normalize_rpc_url(metrics_url)
    try:
        async with async_rpc_client(timeout=6.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            text = res.text or ""
            if not text.strip():
                return None, "TON Prometheus returned empty body"
            return extract_ton_metrics(parse_prometheus_text(text)), None
    except Exception as exc:  # noqa: BLE001
        return None, f"TON Prometheus enrichment unavailable: {format_transport_error(exc)}"


async def collect_ton_snapshot(
    validation_url: str,
    qos_url: str | None,
    adnls: list[str],
) -> tuple[list[ValidationCycle], list[ElectionEntry], dict[str, list[QosRow]], str | None]:
    errors: list[str] = []
    cycles: list[ValidationCycle] = []
    elections: list[ElectionEntry] = []
    qos_by_adnl: dict[str, list[QosRow]] = {normalize_adnl(a): [] for a in adnls}

    try:
        raw_cycles = await ton_get(
            validation_url,
            "/getValidationCycles",
            {"return_participants": True, "offset": 0, "limit": 2},
        )
        cycles = parse_validation_cycles(raw_cycles)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"getValidationCycles: {format_transport_error(exc)}")

    try:
        raw_el = await ton_get(
            validation_url,
            "/getElections",
            {"offset": 0, "limit": 2},
        )
        elections = parse_elections(raw_el)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"getElections: {format_transport_error(exc)}")

    if qos_url:
        cycle_ids = [c.cycle_id for c in cycles[:2]]
        for adnl in adnls:
            for cycle_id in cycle_ids:
                try:
                    payload = await ton_get(
                        qos_url,
                        "/api/qos/cycleScoreboard",
                        {"cycle_id": cycle_id, "validator_adnl": adnl},
                    )
                    qos_by_adnl[adnl].extend(parse_qos_scoreboard(payload))
                except Exception as exc:  # noqa: BLE001
                    errors.append(
                        f"cycleScoreboard {adnl[:8]} cycle {cycle_id}: "
                        f"{format_transport_error(exc)}"
                    )

    return cycles, elections, qos_by_adnl, "; ".join(errors) if errors else None


async def collect_ton_consensus(
    validation_url: str | None,
    metrics: TonMetrics | None,
    cycles: list[ValidationCycle],
    last_error: str | None,
) -> ConsensusHealth:
    reachable = False
    if validation_url:
        try:
            await probe_rpc_endpoint(validation_url)
            reachable = True
        except Exception as exc:  # noqa: BLE001
            reachable = False
            probe_err = format_transport_error(exc)
            last_error = f"{last_error}; {probe_err}" if last_error else probe_err
    current = cycles[0] if cycles else None
    lag = int(metrics.master_out_of_sync) if metrics and metrics.master_out_of_sync else 0
    syncing = bool(metrics and metrics.synced is False) or lag > 30
    status: HealthStatus = "unknown"
    if reachable and current:
        status = "degraded" if syncing else "healthy"
        if metrics and metrics.master_out_of_sync and metrics.master_out_of_sync > 60:
            status = "critical"
    elif last_error:
        status = "unknown"
    return ConsensusHealth(
        beacon_reachable=reachable,
        syncing=syncing,
        sync_distance=lag,
        head_slot=current.cycle_id if current else 0,
        finalized_epoch=current.utime_since if current and current.utime_since else 0,
        justified_epoch=current.utime_since if current and current.utime_since else 0,
        peer_count=0,
        connected_peers=0,
        last_error=last_error,
        status=status,
    )
