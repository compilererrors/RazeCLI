"""BLE alias CLI operations."""

from __future__ import annotations

from typing import Any, Dict

from razecli.ble.alias_store import (
    _alias_path,
    _format_alias_mac_key,
    _remember_alias,
    ble_alias_clear,
    ble_alias_list,
)
from razecli.ble.common import _is_mac_like_address
from razecli.ble.discovery import _auto_resolve_corebluetooth_address, _candidate_preview
from razecli.ble.sync_runner import run_ble_sync
from razecli.errors import RazeCliError


async def _ble_alias_resolve_async(*, mac_address: str, timeout: float) -> Dict[str, Any]:
    candidate = str(mac_address or "").strip()
    if not _is_mac_like_address(candidate):
        raise RazeCliError("Expected MAC address in format XX:XX:XX:XX:XX:XX")

    resolution = await _auto_resolve_corebluetooth_address(
        mac_address=candidate,
        timeout=float(timeout),
        allow_alias_cache=False,
    )
    resolved = str(resolution.get("resolved_address") or "").strip()
    if not resolved:
        candidates = resolution.get("candidates", [])
        preview = _candidate_preview(candidates)
        raise RazeCliError(
            f"Could not resolve MAC {candidate} to a CoreBluetooth UUID. "
            f"Candidates: {preview}"
        )

    _remember_alias(candidate, resolved)
    return {
        "path": _alias_path(),
        "requested_mac": _format_alias_mac_key(candidate),
        "resolved_address": resolved,
        "candidates": resolution.get("candidates", []),
    }


def ble_alias_resolve(*, mac_address: str, timeout: float = 8.0) -> Dict[str, Any]:
    return run_ble_sync(_ble_alias_resolve_async(mac_address=mac_address, timeout=float(timeout)))
