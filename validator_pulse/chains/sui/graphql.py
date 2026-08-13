from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
    probe_rpc_endpoint,
)
from validator_pulse.models import ConsensusHealth, HealthStatus

# GraphQL only — never JSON-RPC (deprecated for Sui).
_VALIDATOR_PAGE = """
query ($after: String) {
  epoch {
    epochId
    totalCheckpoints
    totalTransactions
    referenceGasPrice
    systemState { json }
    validatorSet {
      activeValidators(first: 50, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          atRisk
          contents { json }
        }
      }
    }
  }
  checkpoint {
    sequenceNumber
    timestamp
  }
}
"""


@dataclass(frozen=True)
class SuiValidatorInfo:
    address: str
    name: str | None
    voting_power: int
    stake_mist: int
    at_risk: int
    gas_price: int
    commission_rate: int


@dataclass
class SuiChainSnapshot:
    epoch_id: int
    checkpoint: int
    safe_mode: bool
    low_stake_grace_period: int
    validators: list[SuiValidatorInfo] = field(default_factory=list)
    # address (lower) → set of reporter addresses that reported them
    reported_by: dict[str, set[str]] = field(default_factory=dict)


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
    return int(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


async def graphql_post(
    graphql_url: str,
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST a GraphQL document. Never sends JSON-RPC payloads."""
    url = normalize_rpc_url(graphql_url)
    body = {"query": query, "variables": variables or {}}
    async with async_rpc_client(timeout=20.0) as client:
        res = await client.post(
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=body,
        )
        res.raise_for_status()
        payload = res.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Sui GraphQL returned non-object payload")
    if payload.get("errors"):
        msgs = "; ".join(
            str(e.get("message") or e) for e in payload["errors"] if isinstance(e, dict)
        )
        raise RuntimeError(f"Sui GraphQL error: {msgs or payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Sui GraphQL response missing data")
    return data


def parse_validator_node(node: dict[str, Any]) -> SuiValidatorInfo | None:
    contents = node.get("contents") if isinstance(node.get("contents"), dict) else {}
    raw = contents.get("json")
    if not isinstance(raw, dict):
        return None
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    address = str(meta.get("sui_address") or "").strip()
    if not address:
        return None
    pool = raw.get("staking_pool") if isinstance(raw.get("staking_pool"), dict) else {}
    return SuiValidatorInfo(
        address=address,
        name=str(meta.get("name") or "") or None,
        voting_power=_as_int(raw.get("voting_power")),
        stake_mist=_as_int(pool.get("sui_balance")),
        at_risk=_as_int(node.get("atRisk")),
        gas_price=_as_int(raw.get("gas_price")),
        commission_rate=_as_int(raw.get("commission_rate")),
    )


def parse_report_records(system_json: dict[str, Any]) -> dict[str, set[str]]:
    """Map reported validator address → set of reporters."""
    out: dict[str, set[str]] = {}
    records = system_json.get("validator_report_records")
    contents: list[Any] = []
    if isinstance(records, dict):
        contents = records.get("contents") or []
    elif isinstance(records, list):
        contents = records
    for entry in contents:
        if not isinstance(entry, dict):
            continue
        reporter = str(entry.get("key") or "").strip().lower()
        value = entry.get("value")
        reported: list[Any] = []
        if isinstance(value, dict):
            reported = value.get("contents") or []
        elif isinstance(value, list):
            reported = value
        for addr in reported:
            target = str(addr).strip().lower()
            if not target:
                continue
            out.setdefault(target, set()).add(reporter)
    return out


async def fetch_chain_snapshot(graphql_url: str) -> SuiChainSnapshot:
    validators: list[SuiValidatorInfo] = []
    after: str | None = None
    epoch_id = 0
    checkpoint = 0
    safe_mode = False
    grace = 0
    reported_by: dict[str, set[str]] = {}

    for _ in range(20):  # hard cap pages
        data = await graphql_post(
            graphql_url,
            _VALIDATOR_PAGE,
            {"after": after},
        )
        epoch = data.get("epoch") if isinstance(data.get("epoch"), dict) else {}
        epoch_id = _as_int(epoch.get("epochId"))
        cp = data.get("checkpoint") if isinstance(data.get("checkpoint"), dict) else {}
        checkpoint = _as_int(cp.get("sequenceNumber")) or _as_int(
            epoch.get("totalCheckpoints")
        )

        system = epoch.get("systemState") if isinstance(epoch.get("systemState"), dict) else {}
        system_json = system.get("json") if isinstance(system.get("json"), dict) else {}
        if system_json:
            safe_mode = _as_bool(system_json.get("safe_mode"))
            params = (
                system_json.get("parameters")
                if isinstance(system_json.get("parameters"), dict)
                else {}
            )
            grace = _as_int(params.get("validator_low_stake_grace_period")) or grace
            reported_by = parse_report_records(system_json) or reported_by

        vset = epoch.get("validatorSet") if isinstance(epoch.get("validatorSet"), dict) else {}
        conn = (
            vset.get("activeValidators")
            if isinstance(vset.get("activeValidators"), dict)
            else {}
        )
        for node in conn.get("nodes") or []:
            if isinstance(node, dict):
                info = parse_validator_node(node)
                if info:
                    validators.append(info)

        page = conn.get("pageInfo") if isinstance(conn.get("pageInfo"), dict) else {}
        if page.get("hasNextPage") and page.get("endCursor"):
            after = str(page["endCursor"])
            continue
        break

    return SuiChainSnapshot(
        epoch_id=epoch_id,
        checkpoint=checkpoint,
        safe_mode=safe_mode,
        low_stake_grace_period=grace or 7,
        validators=validators,
        reported_by=reported_by,
    )


async def collect_sui_consensus(graphql_url: str) -> ConsensusHealth:
    base = normalize_rpc_url(graphql_url)
    try:
        await probe_rpc_endpoint(base)
        snap = await fetch_chain_snapshot(graphql_url)
        syncing = snap.checkpoint == 0
        status: HealthStatus
        if snap.safe_mode:
            status = "critical"
        elif syncing:
            status = "degraded"
        else:
            status = "healthy"
        return ConsensusHealth(
            beacon_reachable=True,
            syncing=syncing or snap.safe_mode,
            sync_distance=-1 if snap.safe_mode else 0,
            head_slot=snap.checkpoint,
            finalized_epoch=snap.epoch_id,
            justified_epoch=snap.epoch_id,
            peer_count=0,
            connected_peers=0,
            status=status,
            last_error="Network safe mode enabled" if snap.safe_mode else None,
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


async def try_fetch_sui_metrics(metrics_url: str) -> tuple[SuiNodeMetrics | None, str | None]:
    if not metrics_url or not metrics_url.strip():
        return None, None
    url = normalize_rpc_url(metrics_url)
    try:
        async with async_rpc_client(timeout=6.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            text = res.text or ""
            if not text.strip():
                return None, "Sui Prometheus metrics returned empty body"
            return extract_sui_metrics(parse_prometheus_text(text)), None
    except Exception as exc:  # noqa: BLE001
        return None, f"Sui metrics enrichment unavailable: {format_transport_error(exc)}"
