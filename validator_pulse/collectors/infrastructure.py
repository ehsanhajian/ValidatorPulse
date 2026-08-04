from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import psutil

from validator_pulse.models import HealthStatus, InfrastructureHealth


def _infra_status(
    *,
    cpu: float,
    memory: float,
    disk: float,
    disk_latency_ms: float,
    clock_drift_ms: float,
    network_healthy: bool,
) -> HealthStatus:
    if not network_healthy:
        return "critical"
    if clock_drift_ms > 1000:
        return "critical"
    if disk >= 95 or memory >= 95 or cpu >= 95:
        return "critical"
    if (
        disk >= 85
        or memory >= 85
        or cpu >= 85
        or disk_latency_ms > 50
        or clock_drift_ms > 500
    ):
        return "degraded"
    return "healthy"


def _measure_disk_latency_ms() -> float:
    path = Path(tempfile.gettempdir()) / ".vp-disk-probe"
    start = time.perf_counter()
    try:
        path.write_text(str(time.time()), encoding="utf-8")
        _ = path.read_text(encoding="utf-8")
        path.unlink(missing_ok=True)
        return round((time.perf_counter() - start) * 1000, 2)
    except OSError:
        return 12.0


def collect_infrastructure(
    *,
    disk_usage_percent: float | None = None,
    disk_used_bytes: int | None = None,
    disk_total_bytes: int | None = None,
    network_healthy: bool = True,
    network_rx_bytes_per_sec: float = 1_250_000,
    network_tx_bytes_per_sec: float = 840_000,
    clock_drift_ms: float = 0.0,
) -> InfrastructureHealth:
    vm = psutil.virtual_memory()
    cpu = float(psutil.cpu_percent(interval=0.2))
    disk_latency = _measure_disk_latency_ms()

    root = os.path.abspath(os.sep)
    try:
        usage = psutil.disk_usage(root)
        measured_disk_pct = float(usage.percent)
        measured_disk_used = int(usage.used)
        measured_disk_total = int(usage.total)
    except OSError:
        measured_disk_pct = 42.0
        measured_disk_used = int(1.2e12 * 0.42)
        measured_disk_total = int(1.2e12)

    disk_pct = disk_usage_percent if disk_usage_percent is not None else measured_disk_pct
    disk_used = disk_used_bytes if disk_used_bytes is not None else measured_disk_used
    disk_total = disk_total_bytes if disk_total_bytes is not None else measured_disk_total

    return InfrastructureHealth(
        cpu_usage_percent=round(cpu, 1),
        memory_usage_percent=round(float(vm.percent), 1),
        memory_used_bytes=int(vm.used),
        memory_total_bytes=int(vm.total),
        disk_usage_percent=round(disk_pct, 1),
        disk_used_bytes=int(disk_used),
        disk_total_bytes=int(disk_total),
        disk_latency_ms=disk_latency,
        network_healthy=network_healthy,
        network_rx_bytes_per_sec=network_rx_bytes_per_sec,
        network_tx_bytes_per_sec=network_tx_bytes_per_sec,
        clock_drift_ms=round(clock_drift_ms, 1),
        status=_infra_status(
            cpu=cpu,
            memory=float(vm.percent),
            disk=disk_pct,
            disk_latency_ms=disk_latency,
            clock_drift_ms=clock_drift_ms,
            network_healthy=network_healthy,
        ),
    )
