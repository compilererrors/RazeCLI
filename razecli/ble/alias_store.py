"""BLE alias cache storage."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from razecli.ble.common import _normalize_address
from razecli.ble.constants import DEFAULT_BLE_ALIAS_PATH


def _alias_path() -> str:
    override = str(os.getenv("RAZECLI_BLE_ALIAS_PATH", "")).strip()
    if override:
        return os.path.expanduser(override)
    return os.path.expanduser(DEFAULT_BLE_ALIAS_PATH)


def _load_alias_cache() -> Dict[str, Dict[str, str]]:
    path = _alias_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}
    aliases = raw.get("aliases")
    if not isinstance(aliases, dict):
        return {}

    normalized: Dict[str, Dict[str, str]] = {}
    for key, value in aliases.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        resolved = str(value.get("resolved_address", "")).strip()
        if not resolved:
            continue
        normalized[_normalize_address(key)] = {
            "resolved_address": resolved,
            "updated_at": str(value.get("updated_at", "")).strip(),
        }
    return normalized


def _save_alias_cache(aliases: Dict[str, Dict[str, str]]) -> None:
    path = _alias_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = {"aliases": aliases}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def _remember_alias(mac_address: str, resolved_address: str) -> None:
    if not mac_address or not resolved_address:
        return
    key = _normalize_address(mac_address)
    if not key:
        return
    aliases = _load_alias_cache()
    aliases[key] = {
        "resolved_address": resolved_address,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _save_alias_cache(aliases)
    except Exception:
        # Cache write failure should not block BLE probing.
        pass


def _format_alias_mac_key(value: str) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isalnum()).upper()
    if len(text) == 12:
        return ":".join(text[idx : idx + 2] for idx in range(0, 12, 2))
    return text


def ble_alias_list() -> Dict[str, Any]:
    aliases = _load_alias_cache()
    rows = []
    for mac_key in sorted(aliases.keys()):
        row = aliases.get(mac_key, {})
        rows.append(
            {
                "mac_address": _format_alias_mac_key(mac_key),
                "resolved_address": str(row.get("resolved_address") or ""),
                "updated_at": str(row.get("updated_at") or ""),
            }
        )
    return {
        "path": _alias_path(),
        "count": len(rows),
        "aliases": rows,
    }


def ble_alias_clear(*, mac_address: Optional[str] = None) -> Dict[str, Any]:
    aliases = _load_alias_cache()
    removed = 0

    if mac_address:
        key = _normalize_address(mac_address)
        if key in aliases:
            removed = 1
            del aliases[key]
    else:
        removed = len(aliases)
        aliases = {}

    try:
        _save_alias_cache(aliases)
    except Exception:
        # Alias cache clear should be best-effort and never block CLI usage.
        pass

    return {
        "path": _alias_path(),
        "removed": removed,
        "remaining": len(aliases),
        "cleared_all": bool(mac_address is None),
        "target_mac": _format_alias_mac_key(mac_address or "") if mac_address else None,
    }
