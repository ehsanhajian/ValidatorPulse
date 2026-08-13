from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SuiNodeMetrics:
    proposed_blocks: int | None = None
    highest_synced_checkpoint: int | None = None
    last_executed_checkpoint: int | None = None
    connected_peers: int | None = None
    uptime_seconds: float | None = None


def parse_prometheus_text(text: str) -> dict[str, float]:
    """Parse Prometheus text exposition into metric_name → value (labels stripped)."""
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
        # Sum labeled series (e.g. consensus_proposed_blocks{force=...}).
        out[name] = out.get(name, 0.0) + value
    return out


def _find(metrics: dict[str, float], *names: str) -> float | None:
    for name in names:
        if name in metrics:
            return metrics[name]
    return None


def extract_sui_metrics(metrics: dict[str, float]) -> SuiNodeMetrics:
    proposed = _find(
        metrics,
        "consensus_proposed_blocks",
        "proposed_blocks",
    )
    synced = _find(
        metrics,
        "highest_synced_checkpoint",
        "last_executed_checkpoint",
    )
    executed = _find(metrics, "last_executed_checkpoint")
    peers = _find(metrics, "connected_peers", "network_peers", "sui_network_peers")
    uptime = _find(metrics, "uptime")
    return SuiNodeMetrics(
        proposed_blocks=int(proposed) if proposed is not None else None,
        highest_synced_checkpoint=int(synced) if synced is not None else None,
        last_executed_checkpoint=int(executed) if executed is not None else None,
        connected_peers=int(peers) if peers is not None else None,
        uptime_seconds=float(uptime) if uptime is not None else None,
    )


def sui_effectiveness(
    *,
    in_set: bool,
    proposals_delta: int | None,
    checkpoint_advancing: bool | None,
    at_risk_epochs: int,
    reported: bool,
    safe_mode: bool,
    metrics_available: bool,
) -> float:
    if safe_mode or reported:
        return 0.0
    if not in_set:
        return 0.0
    score = 55.0
    if at_risk_epochs <= 0:
        score += 15.0
    else:
        score += max(0.0, 15.0 - min(at_risk_epochs, 5) * 3.0)
    if metrics_available:
        if proposals_delta is not None and proposals_delta > 0:
            score += 15.0
        elif proposals_delta == 0:
            score += 5.0
        if checkpoint_advancing:
            score += 15.0
        elif checkpoint_advancing is False:
            score += 0.0
        else:
            score += 5.0
    else:
        # On-chain only — healthy active membership still scores well.
        score += 20.0
    return round(min(100.0, max(0.0, score)), 1)
