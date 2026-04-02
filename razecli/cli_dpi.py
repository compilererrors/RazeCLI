"""Handlers for the `dpi` command."""

from __future__ import annotations

import argparse

from razecli.cli_common import (
    emit,
    mirror_dpi_to_transport_peers,
    persist_autosync_from_device,
    resolve_target_device,
    validate_dpi_args,
)
from razecli.device_service import DeviceService


def handle_dpi(service: DeviceService, args: argparse.Namespace) -> int:
    device = resolve_target_device(service, args)
    backend = service.resolve_backend(device)

    if args.dpi_command == "get":
        dpi_x, dpi_y = backend.get_dpi(device)
        emit(
            {
                "id": device.identifier,
                "model": device.model_id,
                "dpi_x": dpi_x,
                "dpi_y": dpi_y,
            },
            as_json=args.json,
        )
        return 0

    dpi_x, dpi_y = validate_dpi_args(args, service, device)
    backend.set_dpi(device, dpi_x, dpi_y)
    mirror_dpi_to_transport_peers(service, device, dpi_x, dpi_y)
    persist_autosync_from_device(device, backend)
    emit(
        {
            "status": "ok",
            "id": device.identifier,
            "model": device.model_id,
            "dpi_x": dpi_x,
            "dpi_y": dpi_y,
        },
        as_json=args.json,
    )
    return 0

