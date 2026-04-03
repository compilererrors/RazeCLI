"""Handlers for the `button-mapping` command."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from razecli.cli_common import emit, resolve_target_device
from razecli.device_service import DeviceService
from razecli.errors import CapabilityUnsupportedError, RazeCliError
from razecli.feature_scaffolds import (
    get_button_mapping_scaffold,
    list_button_mapping_actions,
    reset_button_mapping_scaffold,
    set_button_mapping_scaffold,
)


def _merge_button_mapping_state(local_state: Dict[str, Any], hardware_state: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(local_state)
    for key in ("mapping", "buttons_supported", "actions_suggested"):
        if key in hardware_state:
            merged[key] = hardware_state[key]
    return merged


def handle_button_mapping(service: DeviceService, args: argparse.Namespace) -> int:
    device = resolve_target_device(service, args)
    backend = service.resolve_backend(device)

    if args.button_mapping_command == "get":
        state = get_button_mapping_scaffold(model_id=device.model_id, path=args.store_file)
        hardware_apply = "fallback-local"
        get_mapping = getattr(backend, "get_button_mapping", None)
        if callable(get_mapping):
            try:
                hardware_state = get_mapping(device)
                if isinstance(hardware_state, dict):
                    state = _merge_button_mapping_state(state, hardware_state)
                    hardware_apply = "read"
            except CapabilityUnsupportedError:
                hardware_apply = "fallback-local"

        state["hardware_apply"] = hardware_apply
        state["scope"] = "device+local" if hardware_apply == "read" else "local-scaffold"
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
        hardware_apply = "fallback-local"
        set_mapping = getattr(backend, "set_button_mapping", None)
        if callable(set_mapping):
            try:
                hardware_state = set_mapping(
                    device,
                    button=str(args.button),
                    action=str(args.action),
                )
                if isinstance(hardware_state, dict):
                    state = _merge_button_mapping_state(state, hardware_state)
                hardware_apply = "applied"
            except CapabilityUnsupportedError:
                hardware_apply = "fallback-local"

        state["hardware_apply"] = hardware_apply
        state["scope"] = "device+local" if hardware_apply == "applied" else "local-scaffold"
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
        hardware_apply = "fallback-local"
        reset_mapping = getattr(backend, "reset_button_mapping", None)
        if callable(reset_mapping):
            try:
                hardware_state = reset_mapping(device)
                if isinstance(hardware_state, dict):
                    state = _merge_button_mapping_state(state, hardware_state)
                hardware_apply = "applied"
            except CapabilityUnsupportedError:
                hardware_apply = "fallback-local"

        state["hardware_apply"] = hardware_apply
        state["scope"] = "device+local" if hardware_apply == "applied" else "local-scaffold"
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
        hardware_apply = "fallback-local"
        list_actions = getattr(backend, "list_button_mapping_actions", None)
        if callable(list_actions):
            try:
                hardware_actions = list_actions(device)
                if isinstance(hardware_actions, dict):
                    actions = {
                        "buttons": list(hardware_actions.get("buttons", actions["buttons"])),
                        "actions": list(hardware_actions.get("actions", actions["actions"])),
                    }
                    hardware_apply = "read"
            except CapabilityUnsupportedError:
                hardware_apply = "fallback-local"
        emit(
            {
                "id": device.identifier,
                "model": device.model_id,
                "buttons_supported": actions["buttons"],
                "actions_suggested": actions["actions"],
                "hardware_apply": hardware_apply,
                "scope": "device+local" if hardware_apply == "read" else "local-scaffold",
            },
            as_json=args.json,
        )
        return 0

    raise RazeCliError("Unknown button-mapping command")
