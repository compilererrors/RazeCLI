"""Handlers for the `button-mapping` command."""

from __future__ import annotations

import argparse

from razecli.cli_common import emit, resolve_target_device
from razecli.device_service import DeviceService
from razecli.errors import RazeCliError
from razecli.feature_scaffolds import (
    get_button_mapping_scaffold,
    list_button_mapping_actions,
    reset_button_mapping_scaffold,
    set_button_mapping_scaffold,
)


def handle_button_mapping(service: DeviceService, args: argparse.Namespace) -> int:
    device = resolve_target_device(service, args)

    if args.button_mapping_command == "get":
        state = get_button_mapping_scaffold(model_id=device.model_id, path=args.store_file)
        emit(
            {
                "id": device.identifier,
                "model": device.model_id,
                "button_mapping": state,
            },
            as_json=args.json,
        )
        return 0

    if args.button_mapping_command == "set":
        store_path, state = set_button_mapping_scaffold(
            model_id=device.model_id,
            button=str(args.button),
            action=str(args.action),
            path=args.store_file,
        )
        emit(
            {
                "status": "ok",
                "id": device.identifier,
                "model": device.model_id,
                "store_path": str(store_path),
                "button_mapping": state,
            },
            as_json=args.json,
        )
        return 0

    if args.button_mapping_command == "reset":
        store_path, state = reset_button_mapping_scaffold(
            model_id=device.model_id,
            path=args.store_file,
        )
        emit(
            {
                "status": "ok",
                "id": device.identifier,
                "model": device.model_id,
                "store_path": str(store_path),
                "button_mapping": state,
            },
            as_json=args.json,
        )
        return 0

    if args.button_mapping_command == "actions":
        actions = list_button_mapping_actions(device.model_id)
        emit(
            {
                "id": device.identifier,
                "model": device.model_id,
                "buttons_supported": actions["buttons"],
                "actions_suggested": actions["actions"],
                "scope": "local-scaffold",
            },
            as_json=args.json,
        )
        return 0

    raise RazeCliError("Unknown button-mapping command")
