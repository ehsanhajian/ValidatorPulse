from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LedgerEvidence:
    """Local consensus artifacts — the only source of exact proposal outcomes."""

    authored: int = 0
    missed: int = 0
    last_round: int | None = None
    last_epoch: int | None = None

    @property
    def has_duty_history(self) -> bool:
        return self.authored > 0 or self.missed > 0


_AUTHORED_MESSAGES = frozenset({"proposed_block", "proposed", "authored_block"})
_MISSED_MESSAGES = frozenset(
    {"missed_block", "missed_proposal", "timeout", "skipped_proposal"}
)


def _normalize_author(value: Any) -> str:
    text = str(value or "").strip().lower().removeprefix("0x")
    return text


def _fields(obj: dict[str, Any]) -> dict[str, Any]:
    inner = obj.get("fields")
    if isinstance(inner, dict):
        return inner
    return obj


def parse_ledger_tail_text(text: str, *, secp_pubkey_hex: str) -> LedgerEvidence:
    """Parse NDJSON / JSON array from monad-ledger-tail (read-only)."""
    target = _normalize_author(secp_pubkey_hex)
    if not target:
        return LedgerEvidence()
    authored = 0
    missed = 0
    last_round: int | None = None
    last_epoch: int | None = None
    stripped = text.strip()
    records: list[Any] = []
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            records = parsed
    else:
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    for rec in records:
        if not isinstance(rec, dict):
            continue
        fields = _fields(rec)
        author = _normalize_author(
            fields.get("author") or fields.get("secp") or fields.get("pubkey")
        )
        if author != target:
            continue
        message = str(fields.get("message") or rec.get("message") or "").strip().lower()
        rnd = fields.get("round")
        epoch = fields.get("epoch")
        try:
            if rnd is not None:
                last_round = int(str(rnd))
        except ValueError:
            pass
        try:
            if epoch is not None:
                last_epoch = int(str(epoch))
        except ValueError:
            pass
        if message in _AUTHORED_MESSAGES or (not message and fields.get("seq_num")):
            authored += 1
        elif message in _MISSED_MESSAGES:
            missed += 1
    return LedgerEvidence(
        authored=authored,
        missed=missed,
        last_round=last_round,
        last_epoch=last_epoch,
    )


def load_ledger_tail(path: str | None, *, secp_pubkey_hex: str) -> LedgerEvidence | None:
    if not path or not path.strip():
        return None
    file_path = Path(path.strip())
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return LedgerEvidence()
    return parse_ledger_tail_text(text, secp_pubkey_hex=secp_pubkey_hex)


def parse_status_json(payload: Any) -> dict[str, Any]:
    """Normalize monad-status JSON (optional local health file)."""
    if not isinstance(payload, dict):
        return {}
    consensus = payload.get("consensus") if isinstance(payload.get("consensus"), dict) else {}
    services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
    peers = payload.get("peers") if isinstance(payload.get("peers"), dict) else {}
    status = str(consensus.get("status") or "").strip().lower()
    mode = str(consensus.get("mode") or "").strip().lower()
    try:
        block_diff = int(str(consensus.get("blockDifference") or 0))
    except ValueError:
        block_diff = 0
    try:
        round_ = int(str(consensus.get("round") or 0))
    except ValueError:
        round_ = 0
    bft = str(services.get("monad-bft") or "").strip().lower()
    execution = str(services.get("monad-execution") or "").strip().lower()
    peer_count = 0
    try:
        peer_count = int(str(peers.get("peersNumber") or 0))
    except ValueError:
        peer_count = 0
    in_sync = status in {"in-sync", "insync", "synced", "live"} or mode == "live"
    services_ok = (not bft or bft == "running") and (not execution or execution == "running")
    return {
        "in_sync": in_sync and block_diff <= 0,
        "block_difference": block_diff,
        "round": round_,
        "peer_count": peer_count,
        "services_ok": services_ok,
        "status": status or mode,
    }


def load_status_json(path: str | None) -> dict[str, Any] | None:
    if not path or not path.strip():
        return None
    file_path = Path(path.strip())
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parse_status_json(payload)
