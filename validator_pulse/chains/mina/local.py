from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from validator_pulse.chains.mina.state import WonSlot, normalize_public_key

_SLOT_RE = re.compile(
    r"(?:slot-since-genesis|global[_ ]slot|\bslot)\s*[:=]?\s*(\d+)",
    re.IGNORECASE,
)
_NEXT_GENESIS_SLOT_RE = re.compile(
    r"Next block will be produced in:.*?slot-since-genesis:\s*(\d+)",
    re.IGNORECASE,
)
_NEXT_EPOCH_SLOT_RE = re.compile(
    r"Next block will be produced in:.*?slot:\s*(\d+)",
    re.IGNORECASE,
)
_PRODUCER_RE = re.compile(r"(B62[1-9A-HJ-NP-Za-km-z]+)")
_HEIGHT_RE = re.compile(r"Block height:\s*(\d+)", re.IGNORECASE)
_MAX_HEIGHT_RE = re.compile(
    r"Max observed(?: unvalidated)? block height:\s*(\d+)", re.IGNORECASE
)
_PEERS_RE = re.compile(r"^Peers:\s*(\d+)", re.IGNORECASE | re.MULTILINE)
_EPOCH_SLOT_RE = re.compile(r"epoch\s*=\s*(\d+)\s*,\s*slot\s*=\s*(\d+)", re.IGNORECASE)
_STATUS_RE = re.compile(
    r"(?:Sync status|Status):\s*([A-Za-z]+)", re.IGNORECASE
)


@dataclass
class MinaClientStatus:
    sync_status: str = "UNKNOWN"
    block_height: int = 0
    max_height: int = 0
    peers: int = 0
    producers: list[str] = field(default_factory=list)
    next_global_slot: int | None = None
    epoch: int | None = None
    slot: int | None = None
    raw: str = ""


def parse_client_status(text: str) -> MinaClientStatus:
    """Parse `mina client status` text (read-only CLI output)."""
    producers = [normalize_public_key(m) for m in _PRODUCER_RE.findall(text)]
    next_slot = None
    nxt = _NEXT_GENESIS_SLOT_RE.search(text) or _NEXT_EPOCH_SLOT_RE.search(text)
    if nxt:
        next_slot = int(nxt.group(1))
    if "none this epoch" in text.lower():
        next_slot = None
    epoch = slot = None
    # Prefer "Consensus time now" over best tip when both exist.
    now_line = ""
    for line in text.splitlines():
        if "consensus time now" in line.lower():
            now_line = line
            break
    match = _EPOCH_SLOT_RE.search(now_line or text)
    if match:
        epoch, slot = int(match.group(1)), int(match.group(2))
    status_match = _STATUS_RE.search(text)
    sync = (status_match.group(1) if status_match else "UNKNOWN").upper()
    if sync == "STATUS":
        sync = "UNKNOWN"
    height = _HEIGHT_RE.search(text)
    max_h = _MAX_HEIGHT_RE.findall(text)
    peers = _PEERS_RE.search(text)
    return MinaClientStatus(
        sync_status=sync,
        block_height=int(height.group(1)) if height else 0,
        max_height=int(max_h[-1]) if max_h else 0,
        peers=int(peers.group(1)) if peers else 0,
        producers=list(dict.fromkeys(producers)),
        next_global_slot=next_slot,
        epoch=epoch,
        slot=slot,
        raw=text,
    )


def run_mina_client_status(command: str | None) -> str | None:
    """Read-only `mina client status`. Never passes secrets or uses a shell."""
    binary = (command or "").strip() or "mina"
    try:
        proc = subprocess.run(  # noqa: S603
            [binary, "client", "status"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return text if text.strip() else None


def _slot_from_mapping(payload: dict[str, Any]) -> int:
    for key in (
        "global_slot_since_genesis",
        "global_slot",
        "slot_since_genesis",
        "curr_global_slot",
        "slot",
    ):
        value = payload.get(key)
        if isinstance(value, dict):
            value = value.get("slot_number") or value.get("slot")
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0


def parse_mina_log_text(text: str, *, pubkey: str) -> list[WonSlot]:
    """Parse daemon JSON/plain logs for private won slots and produced blocks."""
    target = normalize_public_key(pubkey)
    found: dict[int, WonSlot] = {}

    def record(*, slot: int, produced: bool, state_hash: str | None, epoch: int | None, source: str) -> None:
        if slot <= 0:
            return
        existing = found.get(slot)
        if existing is None:
            found[slot] = WonSlot(
                pubkey=target,
                slot=slot,
                epoch=epoch,
                produced=produced,
                state_hash=state_hash,
                source=source,
            )
            return
        found[slot] = WonSlot(
            pubkey=target,
            slot=slot,
            epoch=epoch if epoch is not None else existing.epoch,
            produced=existing.produced or produced,
            state_hash=state_hash or existing.state_hash,
            source=existing.source,
        )

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                message = str(payload.get("message") or payload.get("msg") or "").lower()
                meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
                creator = normalize_public_key(
                    str(meta.get("block_creator") or meta.get("creator") or "")
                )
                if creator and creator != target:
                    continue
                slot = _slot_from_mapping(meta if isinstance(meta, dict) else {})
                epoch = None
                try:
                    if meta.get("epoch") is not None:
                        epoch = int(str(meta.get("epoch")))
                except (TypeError, ValueError):
                    epoch = None
                produced = any(
                    token in message
                    for token in (
                        "successfully produced",
                        "produced a new block",
                        "generated a new block",
                    )
                )
                won = produced or any(
                    token in message
                    for token in ("won slot", "won a slot", "producing block", "generating new block")
                )
                if won:
                    record(
                        slot=slot,
                        produced=produced,
                        state_hash=str(meta.get("state_hash") or "") or None,
                        epoch=epoch,
                        source="log",
                    )
                continue
        lower = line.lower()
        slot_match = _SLOT_RE.search(line)
        slot = int(slot_match.group(1)) if slot_match else 0
        produced = "successfully produced" in lower or "produced a new block" in lower
        won = produced or "won slot" in lower or "producing block" in lower
        if won and slot:
            record(slot=slot, produced=produced, state_hash=None, epoch=None, source="log")
    return sorted(found.values(), key=lambda w: w.slot)


def load_mina_log(path: str | None, *, pubkey: str) -> list[WonSlot]:
    if not path or not path.strip():
        return []
    file_path = Path(path.strip())
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if not text.strip():
        return []
    return parse_mina_log_text(text, pubkey=pubkey)
