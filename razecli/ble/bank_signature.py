"""Onboard bank fingerprint (same algorithm as `ble bank-probe` / bank-snapshot)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from razecli.ble.bank_snapshot_store import list_bank_snapshots


def bank_signature_from_parsed_stages(
    active_stage: int,
    marker: int,
    stages: Sequence[Tuple[int, int]],
    stage_ids: Sequence[int],
) -> str:
    """16-char hex SHA1 prefix; must stay in sync with ``cli_ble._decode_bank_payload_hex``."""
    stage_rows: List[Dict[str, Any]] = []
    for idx, (dpi_x, dpi_y) in enumerate(stages, start=1):
        sid = int(stage_ids[idx - 1]) if idx - 1 < len(stage_ids) else int(idx)
        stage_rows.append(
            {
                "index": int(idx),
                "stage_id": sid,
                "dpi_x": int(dpi_x),
                "dpi_y": int(dpi_y),
            }
        )
    signature_seed = {
        "active_stage": int(active_stage),
        "marker": int(marker),
        "stages": stage_rows,
    }
    return hashlib.sha1(
        json.dumps(signature_seed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def match_bank_snapshot_labels(
    signature: str,
    *,
    path_override: Optional[str] = None,
) -> List[str]:
    """Return snapshot labels (newest first) whose ``primary_bank_signature`` matches."""
    wanted = str(signature or "").strip().lower()
    if not wanted:
        return []
    payload = list_bank_snapshots(path_override=path_override)
    snaps = payload.get("snapshots") if isinstance(payload, dict) else []
    if not isinstance(snaps, list):
        return []
    out: List[str] = []
    for row in reversed(snaps):
        if not isinstance(row, dict):
            continue
        ps = str(row.get("primary_bank_signature") or "").strip().lower()
        if ps != wanted:
            continue
        lab = str(row.get("label") or "").strip()
        if lab and lab not in out:
            out.append(lab)
    return out
