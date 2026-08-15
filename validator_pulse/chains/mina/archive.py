from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_B62_RE = re.compile(r"^B62[1-9A-HJ-NP-Za-km-z]+$")

from validator_pulse.chains.mina.state import CanonicalBlock, normalize_public_key

_ARCHIVE_SQL = """
SELECT b.height,
       COALESCE(b.global_slot_since_genesis, b.global_slot) AS slot,
       b.state_hash,
       pk.value AS creator
FROM blocks b
JOIN public_keys pk ON b.creator_id = pk.id
WHERE pk.value = %s
ORDER BY b.height DESC
LIMIT 500;
"""


def _as_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def parse_archive_rows(rows: Any, *, pubkey: str) -> list[CanonicalBlock]:
    target = normalize_public_key(pubkey)
    if not isinstance(rows, list):
        return []
    out: list[CanonicalBlock] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        creator = normalize_public_key(
            str(row.get("creator") or row.get("public_key") or row.get("value") or "")
        )
        if creator and creator != target:
            continue
        slot = _as_int(
            row.get("slot")
            or row.get("global_slot_since_genesis")
            or row.get("global_slot")
        )
        if slot <= 0:
            continue
        out.append(
            CanonicalBlock(
                slot=slot,
                height=_as_int(row.get("height") or row.get("block_height")),
                creator=creator or target,
                state_hash=str(row.get("state_hash") or row.get("stateHash") or ""),
                coinbase=_as_int(row.get("coinbase")),
            )
        )
    return out


def load_archive_json(path: str, *, pubkey: str) -> list[CanonicalBlock]:
    file_path = Path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return parse_archive_rows(payload, pubkey=pubkey)


def _psql_archive(url: str, pubkey: str) -> list[CanonicalBlock] | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        return None
    safe = normalize_public_key(pubkey)
    if not _B62_RE.match(safe):
        return None
    args = ["psql", url, "-At", "-F", "\t", "-c", _ARCHIVE_SQL.replace("%s", f"'{safe}'")]
    try:
        proc = subprocess.run(  # noqa: S603
            args,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            env=None,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        rows.append(
            {
                "height": parts[0],
                "slot": parts[1],
                "state_hash": parts[2],
                "creator": parts[3],
            }
        )
    return parse_archive_rows(rows, pubkey=pubkey)


def try_fetch_archive_blocks(
    archive_url: str | None, *, pubkey: str
) -> tuple[list[CanonicalBlock] | None, str | None]:
    """Optional durable canonical history. Soft-fails; never required for live mode."""
    if not archive_url or not archive_url.strip():
        return None, None
    raw = archive_url.strip()
    if raw.startswith("file://"):
        path = raw.removeprefix("file://")
        return load_archive_json(path, pubkey=pubkey), None
    if raw.endswith(".json") or Path(raw).is_file():
        return load_archive_json(raw, pubkey=pubkey), None
    if raw.startswith("postgres"):
        rows = _psql_archive(raw, pubkey)
        if rows is None:
            return None, (
                "Mina archive enrichment unavailable (psql failed or not installed)"
            )
        return rows, None
    return None, "Mina archive URL is not a JSON file or postgres:// DSN"
