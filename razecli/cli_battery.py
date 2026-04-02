"""Handlers for the `battery` command."""

from __future__ import annotations

import argparse

from razecli.cli_common import emit, resolve_target_device
from razecli.device_service import DeviceService
from razecli.errors import RazeCliError


def handle_battery(service: DeviceService, args: argparse.Namespace) -> int:
    if args.battery_command != "get":
        raise RazeCliError("Unknown battery command")

    device = resolve_target_device(service, args)
    backend = service.resolve_backend(device)
    battery = backend.get_battery(device)

    emit(
        {
            "id": device.identifier,
            "model": device.model_id,
            "battery_percent": battery,
        },
        as_json=args.json,
    )
    return 0

