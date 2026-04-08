"""Handlers for the `rgb` command."""

from __future__ import annotations

import argparse
import time
from typing import Any, Dict, Optional

from razecli.cli_common import emit, resolve_target_device
from razecli.device_service import DeviceService
from razecli.errors import CapabilityUnsupportedError, DeviceSelectionError, RazeCliError
from razecli.feature_scaffolds import get_rgb_scaffold, resolve_feature_store_path, set_rgb_scaffold


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
    if "read_confidence" in hardware_rgb:
        confidence = hardware_rgb.get("read_confidence")
        if isinstance(confidence, dict):
            merged["read_confidence"] = dict(confidence)
    return merged


def _is_unsupported_error(message: str) -> bool:
    text = str(message or "").strip().lower()
    return "unsupported" in text or "not supported" in text


def _device_runtime_signature(device: Any) -> str:
    handle = device.backend_handle if isinstance(device.backend_handle, dict) else {}
    path = str(handle.get("path", "") or "")
    interface = str(handle.get("interface_number", "") or "")
    usage_page = str(handle.get("usage_page", "") or "")
    usage = str(handle.get("usage", "") or "")
    transport = str(handle.get("transport", "") or "")
    return "|".join(
        [
            str(device.identifier),
            str(device.backend),
            path,
            interface,
            usage_page,
            usage,
            transport,
        ]
    )


def _handle_rgb_reapply(service: DeviceService, args: argparse.Namespace) -> int:
    interval = float(args.interval)
    if interval <= 0:
        raise RazeCliError("--interval must be greater than 0")
    duration = args.duration
    if duration is not None and float(duration) <= 0:
        raise RazeCliError("--duration must be greater than 0")
    max_cycles = args.max_cycles
    if max_cycles is not None and int(max_cycles) <= 0:
        raise RazeCliError("--max-cycles must be greater than 0")

    deadline = None if duration is None else (time.monotonic() + float(duration))
    cycles = 0
    applied = 0
    unsupported = 0
    errors = 0
    seen_device = False
    last_device_id: Optional[str] = None
    last_signature: Optional[str] = None
    waiting_announced = False

    if not args.json:
        target_hint = str(getattr(args, "device", None) or getattr(args, "model", None) or "auto")
        print(
            f"[rgb-reapply] watching target={target_hint} interval={interval:.2f}s "
            "(Ctrl+C to stop)"
        )

    while True:
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            break
        if max_cycles is not None and cycles >= int(max_cycles):
            break
        cycles += 1

        try:
            device = resolve_target_device(service, args)
        except DeviceSelectionError:
            if seen_device and not args.json:
                print("[rgb-reapply] target not currently detected; waiting for reconnect")
            seen_device = False
            last_signature = None
            if not waiting_announced and not args.json:
                print("[rgb-reapply] waiting for target device")
                waiting_announced = True
        else:
            waiting_announced = False
            seen_device = True
            backend = service.resolve_backend(device)
            current_signature = _device_runtime_signature(device)

            if current_signature != last_signature:
                rgb = get_rgb_scaffold(model_id=device.model_id, path=args.store_file)
                last_device_id = str(device.identifier)
                try:
                    hardware_rgb = backend.set_rgb(
                        device,
                        mode=str(rgb["mode"]),
                        brightness=int(rgb["brightness"]),
                        color=str(rgb["color"]),
                    )
                    if isinstance(hardware_rgb, dict):
                        rgb = _merge_rgb_state(rgb, hardware_rgb)
                    applied += 1
                    if not args.json:
                        print(
                            "[rgb-reapply] applied "
                            f"id={device.identifier} mode={rgb.get('mode')} "
                            f"brightness={rgb.get('brightness')} color={rgb.get('color')}"
                        )
                except CapabilityUnsupportedError as exc:
                    unsupported += 1
                    if not args.json:
                        print(f"[rgb-reapply] unsupported id={device.identifier}: {exc}")
                    if bool(getattr(args, "stop_on_unsupported", False)):
                        break
                except Exception as exc:
                    errors += 1
                    if not args.json:
                        print(f"[rgb-reapply] apply failed id={device.identifier}: {exc}")
                finally:
                    last_signature = current_signature

        if deadline is not None and time.monotonic() >= deadline:
            break
        if max_cycles is not None and cycles >= int(max_cycles):
            break
        time.sleep(interval)

    if applied > 0:
        status = "ok"
    elif unsupported > 0:
        status = "unsupported"
    elif errors > 0:
        status = "error"
    else:
        status = "idle"

    summary = {
        "status": status,
        "command": "rgb-reapply",
        "cycles": cycles,
        "applied": applied,
        "unsupported": unsupported,
        "errors": errors,
        "seen_device": seen_device,
        "last_device_id": last_device_id,
        "store_path": str(resolve_feature_store_path(args.store_file)),
    }
    emit(summary, as_json=args.json)

    if bool(getattr(args, "stop_on_unsupported", False)) and unsupported > 0:
        return 2
    return 0


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

    if args.rgb_command == "reapply":
        return _handle_rgb_reapply(service, args)

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
        hardware_error: Optional[str] = None
        status = "ok"
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
            except CapabilityUnsupportedError as exc:
                hardware_apply = "fallback-local"
                hardware_error = str(exc)
                if _is_unsupported_error(hardware_error):
                    status = "unsupported"

        rgb["hardware_apply"] = hardware_apply
        rgb["scope"] = "device+local" if hardware_apply == "applied" else "local-scaffold"
        if hardware_error:
            rgb["hardware_error"] = hardware_error
        emit(
            {
                "status": status,
                "id": device.identifier,
                "model": device.model_id,
                "store_path": str(store_path),
                "rgb": rgb,
            },
            as_json=args.json,
        )
        return 0

    raise RazeCliError("Unknown rgb command")
