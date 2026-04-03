"""Handlers for the `rgb` command."""

from __future__ import annotations

import argparse
from typing import Any, Dict

from razecli.cli_common import emit, resolve_target_device
from razecli.device_service import DeviceService
from razecli.errors import CapabilityUnsupportedError, DeviceSelectionError, RazeCliError
from razecli.feature_scaffolds import get_rgb_scaffold, set_rgb_scaffold


def _merge_rgb_state(local_rgb: Dict[str, Any], hardware_rgb: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(local_rgb)
    mode_inferred = bool(hardware_rgb.get("mode_inferred", False))
    for key in ("brightness", "color", "modes_supported"):
        if key in hardware_rgb:
            merged[key] = hardware_rgb[key]

    if "mode" in hardware_rgb:
        if mode_inferred:
            local_mode = str(local_rgb.get("mode") or "").strip().lower()
            supported = [str(item).strip().lower() for item in merged.get("modes_supported", [])]
            if local_mode and local_mode != "off" and (not supported or local_mode in supported):
                merged["mode"] = local_mode
            else:
                merged["mode"] = hardware_rgb["mode"]
        else:
            merged["mode"] = hardware_rgb["mode"]

    if "mode_inferred" in hardware_rgb:
        merged["mode_inferred"] = bool(hardware_rgb["mode_inferred"])
    return merged


def handle_rgb(service: DeviceService, args: argparse.Namespace) -> int:
    if args.rgb_command == "menu":
        if args.json:
            raise RazeCliError("--json is not supported in interactive TUI mode")

        from razecli.tui import run_tui

        model_filter = None if bool(getattr(args, "all_models", False)) else args.model
        if model_filter and service.registry.get(model_filter) is None:
            raise DeviceSelectionError(f"Unknown model: {model_filter}")

        return run_tui(
            service=service,
            model_filter=model_filter,
            preselected_device_id=args.device,
            collapse_transports=not bool(getattr(args, "all_transports", False)),
            startup_editor="rgb",
        )

    device = resolve_target_device(service, args)
    backend = service.resolve_backend(device)

    if args.rgb_command == "get":
        rgb = get_rgb_scaffold(model_id=device.model_id, path=args.store_file)
        hardware_apply = "fallback-local"
        get_rgb = getattr(backend, "get_rgb", None)
        if callable(get_rgb):
            try:
                hardware_rgb = get_rgb(device)
                if isinstance(hardware_rgb, dict):
                    rgb = _merge_rgb_state(rgb, hardware_rgb)
                    hardware_apply = "read"
            except CapabilityUnsupportedError:
                hardware_apply = "fallback-local"

        rgb["hardware_apply"] = hardware_apply
        rgb["scope"] = "device+local" if hardware_apply == "read" else "local-scaffold"
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
        hardware_apply = "fallback-local"
        set_rgb = getattr(backend, "set_rgb", None)
        if callable(set_rgb):
            try:
                hardware_rgb = set_rgb(
                    device,
                    mode=str(args.mode),
                    brightness=brightness,
                    color=args.color,
                )
                if isinstance(hardware_rgb, dict):
                    rgb = _merge_rgb_state(rgb, hardware_rgb)
                hardware_apply = "applied"
            except CapabilityUnsupportedError:
                hardware_apply = "fallback-local"

        rgb["hardware_apply"] = hardware_apply
        rgb["scope"] = "device+local" if hardware_apply == "applied" else "local-scaffold"
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
