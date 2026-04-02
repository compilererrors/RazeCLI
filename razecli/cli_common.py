"""Shared CLI command utilities."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional, Sequence, Tuple

from razecli.device_service import DeviceService, select_device
from razecli.dpi_autosync import autosync_enabled, save_autosync_settings
from razecli.errors import CapabilityUnsupportedError, DeviceSelectionError, RazeCliError
from razecli.transport_sync import mirror_to_transport_targets

MAX_DPI_STAGES = 5


def emit(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
        return

    if isinstance(data, list):
        for row in data:
            print(row)
        return

    if isinstance(data, dict):
        for key, value in data.items():
            print(f"{key}: {value}")
        return

    print(data)


def resolve_target_device(service: DeviceService, args: argparse.Namespace):
    model_filter: Optional[str] = getattr(args, "model", None)
    if model_filter and service.registry.get(model_filter) is None:
        raise DeviceSelectionError(f"Unknown model: {model_filter}")

    collapse_transports = not bool(getattr(args, "device", None))
    devices = service.discover_devices(
        model_filter=model_filter,
        collapse_transports=collapse_transports,
    )
    return select_device(
        devices,
        device_id=getattr(args, "device", None),
        model_id=model_filter,
    )


def validate_dpi_values(
    dpi_x: int,
    dpi_y: int,
    service: DeviceService,
    device,
) -> tuple[int, int]:
    model = service.registry.get(device.model_id) if device.model_id else None
    if model and model.dpi_min is not None and (dpi_x < model.dpi_min or dpi_y < model.dpi_min):
        raise RazeCliError(f"DPI must be at least {model.dpi_min} for model {model.slug}")
    if model and model.dpi_max is not None and (dpi_x > model.dpi_max or dpi_y > model.dpi_max):
        raise RazeCliError(f"DPI must be at most {model.dpi_max} for model {model.slug}")

    if dpi_x <= 0 or dpi_y <= 0:
        raise RazeCliError("DPI must be positive integers")

    return dpi_x, dpi_y


def validate_dpi_args(args: argparse.Namespace, service: DeviceService, device) -> tuple[int, int]:
    dpi_x = int(args.x)
    dpi_y = int(args.y) if args.y is not None else dpi_x
    return validate_dpi_values(dpi_x, dpi_y, service, device)


def parse_dpi_stage(value: str) -> Tuple[int, int]:
    text = value.strip()
    parts = text.split(":", 1)
    if len(parts) != 2:
        raise RazeCliError(
            f"Invalid --stage value '{value}'. Use format X:Y, for example 800:800."
        )
    try:
        dpi_x = int(parts[0].strip())
        dpi_y = int(parts[1].strip())
    except ValueError as exc:
        raise RazeCliError(
            f"Invalid --stage value '{value}'. X and Y must be integers."
        ) from exc
    return dpi_x, dpi_y


def format_dpi_stages_payload(
    device,
    active_stage: int,
    stages: Sequence[Tuple[int, int]],
    *,
    status: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": device.identifier,
        "model": device.model_id,
        "active_stage": int(active_stage),
        "stages_count": len(stages),
        "stages": [
            {"index": idx, "dpi_x": int(dpi_x), "dpi_y": int(dpi_y)}
            for idx, (dpi_x, dpi_y) in enumerate(stages, start=1)
        ],
    }
    if status is not None:
        payload["status"] = status
    return payload


def persist_autosync_from_stages(
    device,
    active_stage: int,
    stages: Sequence[Tuple[int, int]],
) -> None:
    if not autosync_enabled():
        return
    _ = save_autosync_settings(
        model_id=device.model_id,
        active_stage=int(active_stage),
        stages=stages,
    )


def persist_autosync_from_device(device, backend) -> None:
    if not autosync_enabled():
        return
    if "dpi-stages" not in device.capabilities:
        return
    try:
        active_stage, stages = backend.get_dpi_stages(device)
    except Exception:
        return
    persist_autosync_from_stages(device, active_stage, stages)


def emit_mirror_warning(ok: int, failed: int) -> None:
    if ok <= 0 and failed <= 0:
        return
    if failed > 0:
        print(
            f"Warning: transport sync partially failed (ok={ok}, failed={failed})",
            file=sys.stderr,
        )


def unsafe_stage_activate_enabled() -> bool:
    """Return True when legacy stage activation should rewrite the stage table."""
    value = os.getenv("RAZECLI_UNSAFE_STAGE_ACTIVATE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def mirror_dpi_to_transport_peers(service: DeviceService, source_device, dpi_x: int, dpi_y: int) -> None:
    def _writer(target_device) -> None:
        target_backend = service.resolve_backend(target_device)
        target_backend.set_dpi(target_device, dpi_x, dpi_y)

    ok, failed = mirror_to_transport_targets(
        service,
        source_device,
        _writer,
        required_capability="dpi",
    )
    emit_mirror_warning(ok, failed)


def mirror_dpi_stages_to_transport_peers(
    service: DeviceService,
    source_device,
    active_stage: int,
    stages: Sequence[Tuple[int, int]],
) -> None:
    def _writer(target_device) -> None:
        target_backend = service.resolve_backend(target_device)
        target_backend.set_dpi_stages(target_device, active_stage, stages)

    ok, failed = mirror_to_transport_targets(
        service,
        source_device,
        _writer,
        required_capability="dpi-stages",
    )
    emit_mirror_warning(ok, failed)


def mirror_poll_rate_to_transport_peers(service: DeviceService, source_device, hz: int) -> None:
    def _writer(target_device) -> None:
        target_backend = service.resolve_backend(target_device)
        target_backend.set_poll_rate(target_device, hz)

    ok, failed = mirror_to_transport_targets(
        service,
        source_device,
        _writer,
        required_capability="poll-rate",
    )
    emit_mirror_warning(ok, failed)


def validate_poll_rate(args: argparse.Namespace, service: DeviceService, device, backend) -> int:
    hz = int(args.hz)
    if hz <= 0:
        raise RazeCliError("Poll rate must be a positive integer")

    model = service.registry.get(device.model_id) if device.model_id else None
    if model and model.supported_poll_rates and hz not in model.supported_poll_rates:
        raise RazeCliError(
            f"Poll rate {hz} is not supported by {model.slug}. Allowed: {list(model.supported_poll_rates)}"
        )

    try:
        backend_rates = list(backend.get_supported_poll_rates(device))
    except CapabilityUnsupportedError:
        backend_rates = []

    if backend_rates and hz not in backend_rates:
        raise RazeCliError(f"Poll rate {hz} is not supported by this device. Allowed: {backend_rates}")

    return hz

