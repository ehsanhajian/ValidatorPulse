from __future__ import annotations

from validator_pulse.chains.cardano.metrics import (
    TracerMetrics,
    extract_tracer_metrics,
    parse_prometheus_text,
    tracer_metrics_url,
)
from validator_pulse.http_client import (
    async_rpc_client,
    format_transport_error,
    normalize_rpc_url,
)


async def fetch_tracer_metrics(
    tracer_url: str,
    node_name: str,
) -> tuple[TracerMetrics, str | None]:
    """Fetch Prometheus metrics from cardano-tracer for a named node."""
    url = tracer_metrics_url(tracer_url, node_name)
    try:
        normalize_rpc_url(tracer_url)
        async with async_rpc_client(timeout=10.0) as client:
            res = await client.get(url)
            res.raise_for_status()
            text = res.text or ""
        if not text.strip():
            return TracerMetrics(), "cardano-tracer returned empty metrics body"
        parsed = parse_prometheus_text(text)
        if not parsed:
            return TracerMetrics(), "cardano-tracer metrics could not be parsed"
        return extract_tracer_metrics(parsed), None
    except Exception as exc:  # noqa: BLE001
        return TracerMetrics(), format_transport_error(exc)
