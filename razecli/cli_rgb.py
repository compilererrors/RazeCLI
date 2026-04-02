"""Handlers for the `rgb` command."""

from __future__ import annotations

import argparse

from razecli.cli_common import emit, resolve_target_device
from razecli.device_service import DeviceService
from razecli.errors import RazeCliError
from razecli.feature_scaffolds import get_rgb_scaffold, set_rgb_scaffold


def handle_rgb(service: DeviceService, args: argparse.Namespace) -> int:
    device = resolve_target_device(service, args)

    if args.rgb_command == "get":
        rgb = get_rgb_scaffold(model_id=device.model_id, path=args.store_file)
        emit(
            {
                "id": device.identifier,
                "model": device.model_id,
                "rgb": rgb,
            },
            as_json=args.json,
        )
        return 0

    if args.rgb_command == "set":
        brightness = None if args.brightness is None else int(args.brightness)
        store_path, rgb = set_rgb_scaffold(
            model_id=device.model_id,
            mode=str(args.mode),
            brightness=brightness,
            color=args.color,
            path=args.store_file,
        )
        emit(
            {
                "status": "ok",
                "id": device.identifier,
                "model": device.model_id,
                "store_path": str(store_path),
                "rgb": rgb,
            },
            as_json=args.json,
        )
        return 0

    raise RazeCliError("Unknown rgb command")
