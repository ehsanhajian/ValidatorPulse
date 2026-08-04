from __future__ import annotations

from threading import Lock

from validator_pulse.models import AlertEvent, PulseSnapshot

_lock = Lock()
_snapshot: PulseSnapshot | None = None
_alert_history: list[AlertEvent] = []


def get_snapshot() -> PulseSnapshot | None:
    with _lock:
        return _snapshot


def set_snapshot(snapshot: PulseSnapshot) -> None:
    global _snapshot, _alert_history
    with _lock:
        _snapshot = snapshot
        _alert_history = [*snapshot.recent_alerts, *_alert_history][:50]


def get_alert_history() -> list[AlertEvent]:
    with _lock:
        return list(_alert_history)
