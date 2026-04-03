"""Persistent storage for BLE bank snapshots."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from razecli.ble.constants import DEFAULT_BLE_BANK_SNAPSHOT_PATH


def bank_snapshot_path(path_override: Optional[str] = None) -> str:
    if path_override:
        return os.path.expanduser(str(path_override).strip())
    env_override = str(os.getenv("RAZECLI_BLE_BANK_SNAPSHOT_PATH", "")).strip()
    if env_override:
        return os.path.expanduser(env_override)
    return os.path.expanduser(DEFAULT_BLE_BANK_SNAPSHOT_PATH)


def _load_snapshots(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return []

    if not isinstance(raw, dict):
        return []
    items = raw.get("snapshots")
    if not isinstance(items, list):
        return []

    snapshots: List[Dict[str, Any]] = []
    for row in items:
        if isinstance(row, dict):
            snapshots.append(row)
    return snapshots


def _save_snapshots(path: str, snapshots: List[Dict[str, Any]]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = {
        "snapshots": snapshots,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def append_bank_snapshot(
    *,
    snapshot: Dict[str, Any],
    path_override: Optional[str] = None,
) -> Dict[str, Any]:
    path = bank_snapshot_path(path_override)
    snapshots = _load_snapshots(path)
    snapshots.append(snapshot)
    _save_snapshots(path, snapshots)
    return {
        "path": path,
        "count": len(snapshots),
        "snapshot": snapshot,
    }


def list_bank_snapshots(*, path_override: Optional[str] = None) -> Dict[str, Any]:
    path = bank_snapshot_path(path_override)
    snapshots = _load_snapshots(path)
    return {
        "path": path,
        "count": len(snapshots),
        "snapshots": snapshots,
    }


def get_latest_snapshot_by_label(
    *,
    label: str,
    path_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    wanted = str(label or "").strip()
    if not wanted:
        return None
    payload = list_bank_snapshots(path_override=path_override)
    snapshots = payload.get("snapshots") if isinstance(payload, dict) else []
    if not isinstance(snapshots, list):
        return None
    for row in reversed(snapshots):
        if not isinstance(row, dict):
            continue
        current = str(row.get("label") or "").strip()
        if current == wanted:
            return row
    return None
