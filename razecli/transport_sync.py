"""Helpers to mirror settings between stable transport endpoints."""

from __future__ import annotations

import os
from typing import Callable, Iterable, Optional, Tuple

from razecli.device_service import DeviceService
from razecli.types import DetectedDevice

def transport_mirror_enabled() -> bool:
    value = os.getenv("RAZECLI_TRANSPORT_MIRROR", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _is_experimental(device: DetectedDevice) -> bool:
    handle = device.backend_handle if isinstance(device.backend_handle, dict) else {}
    profile = handle.get("profile")
    return bool(getattr(profile, "experimental", False))


def _mirror_pid_set(service: DeviceService, model_id: Optional[str]) -> frozenset[int]:
    if not model_id:
        return frozenset()
    registry = getattr(service, "registry", None)
    if registry is None or not hasattr(registry, "get"):
        return frozenset()
    try:
        model = registry.get(model_id)
    except Exception:
        model = None
    if model is None:
        return frozenset()
    raw = tuple(getattr(model, "rawhid_mirror_product_ids", ()) or ())
    values: set[int] = set()
    for item in raw:
        try:
            pid = int(item)
        except Exception:
            continue
        if 0 <= pid <= 0xFFFF:
            values.add(pid)
    return frozenset(values)


def iter_transport_mirror_targets(
    service: DeviceService,
    source: DetectedDevice,
    *,
    required_capability: Optional[str] = None,
) -> Iterable[DetectedDevice]:
    model_id = source.model_id
    if source.backend != "rawhid" or not model_id:
        return ()

    pid_set = _mirror_pid_set(service, model_id)
    if not pid_set or source.product_id not in pid_set:
        return ()

    candidates = service.discover_devices(model_filter=model_id, collapse_transports=False)
    targets = []
    seen: set[str] = set()
    for device in candidates:
        if device.backend != source.backend:
            continue
        if device.identifier == source.identifier:
            continue
        if device.product_id not in pid_set:
            continue
        if _is_experimental(device):
            continue
        if required_capability and required_capability not in device.capabilities:
            continue
        if device.identifier in seen:
            continue
        seen.add(device.identifier)
        targets.append(device)
    return targets


def mirror_to_transport_targets(
    service: DeviceService,
    source: DetectedDevice,
    writer: Callable[[DetectedDevice], None],
    *,
    required_capability: Optional[str] = None,
) -> Tuple[int, int]:
    if not transport_mirror_enabled():
        return 0, 0

    ok = 0
    failed = 0
    targets = iter_transport_mirror_targets(
        service,
        source,
        required_capability=required_capability,
    )
    for target in targets:
        try:
            writer(target)
            ok += 1
        except Exception:
            failed += 1
    return ok, failed
