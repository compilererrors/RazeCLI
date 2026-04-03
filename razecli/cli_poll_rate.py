"""Handlers for the `poll-rate` command."""

from __future__ import annotations

import argparse

from razecli.cli_common import (
    emit,
    mirror_poll_rate_to_transport_peers,
    resolve_target_device,
    validate_poll_rate,
)
from razecli.device_service import DeviceService
from razecli.errors import RazeCliError


def handle_poll_rate(service: DeviceService, args: argparse.Namespace) -> int:
    device = resolve_target_device(service, args)
    if "poll-rate" not in device.capabilities:
        raise RazeCliError(
            "Selected device/transport does not expose poll-rate. "
            "Use USB/2.4. "
            "For experimental Bluetooth probing, set RAZECLI_BLE_POLL_CAP=1 and allow the model via "
            "RAZECLI_BLE_POLL_SUPPORTED_MODELS (or force with RAZECLI_BLE_POLL_FORCE=1)."
        )
    backend = service.resolve_backend(device)

    if args.poll_command == "get":
        hz = backend.get_poll_rate(device)
        emit(
            {
                "id": device.identifier,
                "model": device.model_id,
                "poll_rate_hz": hz,
            },
            as_json=args.json,
        )
        return 0

    hz = validate_poll_rate(args, service, device, backend)
    backend.set_poll_rate(device, hz)
    mirror_poll_rate_to_transport_peers(service, device, hz)
    emit(
        {
            "status": "ok",
            "id": device.identifier,
            "model": device.model_id,
            "poll_rate_hz": hz,
        },
        as_json=args.json,
    )
    return 0
