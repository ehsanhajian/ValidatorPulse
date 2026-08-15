from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

from validator_pulse.chains.ton.state import is_adnl, normalize_adnl

# Read-only allowlist. Never interpolate operator input into these strings.
_READ_ONLY_COMMANDS: dict[str, str] = {
    "status": "status",
    "vl": "vl --json",
    "vl_past": "vl past --json",
    "cl": "cl --json",
    "cl_past": "cl past --json",
    "el": "el --json",
    "check_ef": "check_ef",
}


def resolve_mytonctrl_binary(command: str | None) -> str | None:
    """Local binary only. Reject shell metacharacters and extra arguments."""
    text = (command or "mytonctrl").strip()
    if not text:
        return None
    if any(ch in text for ch in "|&;`$<>(){}!\n\r\t") or " " in text:
        return None
    return text


def _extract_json(text: str) -> Any:
    blob = (text or "").strip()
    if not blob:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        start = blob.find("{")
        start_list = blob.find("[")
        if start_list >= 0 and (start < 0 or start_list < start):
            start = start_list
        if start < 0:
            return None
        try:
            return json.loads(blob[start:])
        except json.JSONDecodeError:
            return None


def run_mytonctrl(command: str | None, key: str, timeout: float = 12.0) -> str | None:
    """Run a read-only MyTonCtrl console command. Never uses a shell."""
    binary = resolve_mytonctrl_binary(command)
    payload = _READ_ONLY_COMMANDS.get(key)
    if not binary or not payload:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [binary, "-c", payload],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return text if text.strip() else None


@dataclass
class MyTonCtrlRosterRow:
    adnl: str
    index: int | None = None
    efficiency: float | None = None
    online: bool | None = None
    stake: float | None = None
    wallet: str | None = None


@dataclass
class MyTonCtrlSnapshot:
    roster: dict[str, MyTonCtrlRosterRow] = field(default_factory=dict)
    past_roster: dict[str, MyTonCtrlRosterRow] = field(default_factory=dict)
    complaints: list[dict[str, Any]] = field(default_factory=list)
    elections: list[dict[str, Any]] = field(default_factory=list)
    status_text: str = ""
    check_ef_text: str = ""


def _parse_roster(payload: Any) -> dict[str, MyTonCtrlRosterRow]:
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("validators") or payload.get("list") or payload.get("data") or []
    out: dict[str, MyTonCtrlRosterRow] = {}
    if not isinstance(rows, list):
        return out
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        adnl = normalize_adnl(
            raw.get("adnl_addr")
            or raw.get("adnlAddr")
            or raw.get("adnl")
            or raw.get("adnl_address")
        )
        if not is_adnl(adnl):
            continue
        online = raw.get("online")
        if online is None:
            online = raw.get("is_online")
        try:
            efficiency = float(raw["efficiency"]) if raw.get("efficiency") is not None else None
        except (TypeError, ValueError):
            efficiency = None
        try:
            index = int(raw["index"]) if raw.get("index") is not None else None
        except (TypeError, ValueError):
            index = None
        try:
            stake = float(raw["stake"]) if raw.get("stake") is not None else None
        except (TypeError, ValueError):
            stake = None
        out[adnl] = MyTonCtrlRosterRow(
            adnl=adnl,
            index=index,
            efficiency=efficiency,
            online=bool(online) if online is not None else None,
            stake=stake,
            wallet=raw.get("wallet") or raw.get("wallet_address"),
        )
    return out


def load_mytonctrl_snapshot(command: str | None) -> MyTonCtrlSnapshot | None:
    binary = resolve_mytonctrl_binary(command)
    if not binary:
        return None
    status = run_mytonctrl(binary, "status") or ""
    vl = _extract_json(run_mytonctrl(binary, "vl") or "")
    vl_past = _extract_json(run_mytonctrl(binary, "vl_past") or "")
    cl = _extract_json(run_mytonctrl(binary, "cl") or "")
    cl_past = _extract_json(run_mytonctrl(binary, "cl_past") or "")
    el = _extract_json(run_mytonctrl(binary, "el") or "")
    check_ef = run_mytonctrl(binary, "check_ef") or ""
    complaints: list[dict[str, Any]] = []
    for blob in (cl, cl_past):
        rows = blob if isinstance(blob, list) else (blob or {}).get("complaints") if isinstance(blob, dict) else []
        if isinstance(rows, list):
            complaints.extend(row for row in rows if isinstance(row, dict))
    elections: list[dict[str, Any]] = []
    if isinstance(el, list):
        elections = [row for row in el if isinstance(row, dict)]
    elif isinstance(el, dict):
        elections = [el]
    if not any((status, vl, vl_past, complaints, elections, check_ef)):
        return None
    return MyTonCtrlSnapshot(
        roster=_parse_roster(vl),
        past_roster=_parse_roster(vl_past),
        complaints=complaints,
        elections=elections,
        status_text=status,
        check_ef_text=check_ef,
    )
