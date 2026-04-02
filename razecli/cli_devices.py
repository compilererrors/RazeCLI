"""Handlers for the `devices` command."""

from __future__ import annotations

import argparse

from razecli.cli_common import emit
from razecli.device_service import DeviceService


def handle_devices(service: DeviceService, args: argparse.Namespace) -> int:
    devices = service.discover_devices(
        model_filter=args.model,
        collapse_transports=not (bool(args.all_transports) or bool(args.device)),
    )
    if args.device:
        devices = [device for device in devices if device.identifier == args.device]

    if args.json:
        emit([device.to_dict() for device in devices], as_json=True)
    else:
        if not devices:
            print("No Razer devices found")
            if args.backend == "macos-ble":
                print(
                    "Hint: macos-ble is Bluetooth-only (PID 1532:008E). "
                    "Switch the mouse to BT mode and confirm it is connected in macOS."
                )
            backend_errors = service.backend_errors()
            if backend_errors:
                print("Backend status:")
                for backend, error in backend_errors.items():
                    print(f"  {backend}: {error}")
            return 1

        for device in devices:
            capabilities = ",".join(sorted(device.capabilities)) or "-"
            model = device.model_id or "unknown"
            print(
                f"id={device.identifier} usb={device.usb_id()} model={model} "
                f"backend={device.backend} caps={capabilities} name={device.name}"
            )

    return 0

