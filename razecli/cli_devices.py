"""Handlers for the `devices` command."""

from __future__ import annotations

import argparse

from razecli.cli_common import emit
from razecli.device_service import DeviceService, device_matches_ble_address, normalize_ble_address_query


def handle_devices(service: DeviceService, args: argparse.Namespace) -> int:
    devices = service.discover_devices(
        model_filter=args.model,
        collapse_transports=not (bool(args.all_transports) or bool(args.device)),
    )
    if args.device:
        devices = [device for device in devices if device.identifier == args.device]

    addr_q = normalize_ble_address_query(getattr(args, "address", None))
    if addr_q:
        devices = [device for device in devices if device_matches_ble_address(device, addr_q)]

    if args.json:
        emit([device.to_dict() for device in devices], as_json=True)
    else:
        if not devices:
            print("No Razer devices found")
            if args.backend == "macos-ble":
                endpoint_pids = set()
                for model in service.registry.iter():
                    for token in tuple(getattr(model, "ble_endpoint_product_ids", ()) or ()):
                        try:
                            endpoint_pids.add(int(token))
                        except Exception:
                            continue
                pid_list = ", ".join(f"1532:{pid:04X}" for pid in sorted(endpoint_pids))
                print(
                    "Hint: macos-ble is Bluetooth-only"
                    + (f" (known BT endpoint PIDs: {pid_list}). " if pid_list else ". ")
                    + "Switch the mouse to BT mode and confirm it is connected in macOS."
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
