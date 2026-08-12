from __future__ import annotations

import re
from dataclasses import dataclass

from validator_pulse.models import ConsensusHealth, HealthStatus


@dataclass(frozen=True)
class TracerMetrics:
    blocks_forged: int | None = None
    slots_missed: int | None = None
    leader_opportunities: int | None = None
    cannot_forge: int | None = None
    remaining_kes_periods: int | None = None
    forging_enabled: int | None = None
    epoch: int | None = None
    slot: int | None = None
    active_peers: int | None = None
    gsm_state: int | None = None


def parse_prometheus_text(text: str) -> dict[str, float]:
    """Parse a minimal Prometheus text exposition into name → value."""
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # metric{name="x"} 123 or metric 123
        token = line.split()[0]
        name = token.split("{", 1)[0]
        try:
            out[name] = float(line.split()[-1])
        except (ValueError, IndexError):
            continue
    return out


def _find_metric(metrics: dict[str, float], *needles: str) -> float | None:
    for key, value in metrics.items():
        for needle in needles:
            if needle in key:
                return value
    return None


def extract_tracer_metrics(metrics: dict[str, float]) -> TracerMetrics:
    """Map cardano-tracer / node 10.2+ Prometheus names to duty fields."""
    forged = _find_metric(
        metrics,
        "blocksForged_int",
        "blocksForged",
        "blocksForgedNum_int",
        "Forge_forged_counter",
        "Forge_forged",
    )
    missed = _find_metric(metrics, "slotsMissed_int", "slotsMissed")
    opportunities = _find_metric(
        metrics,
        "Forge_about_to_lead_counter",
        "Forge_about_to_lead",
        "nodeIsLeader_int",
        "nodeIsLeader",
        "Forge_node_is_leader_counter",
        "Forge_node_is_leader",
    )
    cannot = _find_metric(metrics, "nodeCannotForge_int", "nodeCannotForge")
    kes = _find_metric(
        metrics,
        "remainingKESPeriods_int",
        "remainingKESPeriods",
    )
    forging = _find_metric(metrics, "forging_enabled_int", "forging_enabled")
    epoch = _find_metric(metrics, "metrics_epoch_int", "metrics_epoch")
    slot = _find_metric(metrics, "slotNum_int", "slotNum")
    peers = _find_metric(
        metrics,
        "peerSelection_ActivePeers_int",
        "peerSelection_ActivePeers",
    )
    gsm = _find_metric(metrics, "GSM_state_int", "GSM_state")

    def as_int(value: float | None) -> int | None:
        if value is None:
            return None
        return int(value)

    return TracerMetrics(
        blocks_forged=as_int(forged),
        slots_missed=as_int(missed),
        leader_opportunities=as_int(opportunities),
        cannot_forge=as_int(cannot),
        remaining_kes_periods=as_int(kes),
        forging_enabled=as_int(forging),
        epoch=as_int(epoch),
        slot=as_int(slot),
        active_peers=as_int(peers),
        gsm_state=as_int(gsm),
    )


def slugify_node_name(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-") or "block-producer"


def tracer_metrics_url(base_url: str, node_name: str) -> str:
    base = base_url.rstrip("/")
    slug = slugify_node_name(node_name)
    return f"{base}/{slug}"


def consensus_from_tracer(
    metrics: TracerMetrics,
    *,
    reachable: bool,
    last_error: str | None = None,
) -> ConsensusHealth:
    syncing = False
    if metrics.gsm_state is not None and metrics.gsm_state not in {1, 2}:
        # GSM: 1=Idle, 2=Syncing (approximate; treat non-idle as syncing).
        syncing = metrics.gsm_state >= 2
    peers = metrics.active_peers or 0
    head = metrics.slot or 0
    epoch = metrics.epoch or 0

    status: HealthStatus = "healthy"
    if not reachable:
        status = "critical"
    elif syncing:
        status = "degraded"
    elif peers < 3:
        status = "degraded"

    return ConsensusHealth(
        beacon_reachable=reachable,
        syncing=syncing,
        sync_distance=0 if not syncing else 1,
        head_slot=head,
        finalized_epoch=epoch,
        justified_epoch=epoch,
        peer_count=peers,
        connected_peers=peers,
        status=status,
        last_error=last_error,
    )
