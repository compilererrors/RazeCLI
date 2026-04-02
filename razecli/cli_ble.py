"""Handlers for the `ble` command."""

from __future__ import annotations

import argparse
import platform
from typing import Any

from razecli.ble_probe import (
    DEFAULT_RAZER_BT_READ_CHAR_UUIDS,
    ble_alias_clear,
    ble_alias_list,
    ble_alias_resolve,
    ble_raw_transceive,
    parse_hex_payload,
    probe_ble_services,
    scan_ble_devices,
)
from razecli.cli_common import emit
from razecli.errors import RazeCliError


def handle_ble(args: argparse.Namespace) -> int:
    if platform.system() != "Darwin":
        raise RazeCliError("BLE probing is currently intended for macOS only")

    if args.ble_command == "scan":
        all_rows = scan_ble_devices(timeout=float(args.timeout))
        rows = all_rows
        if args.name:
            query = str(args.name).strip().lower()
            rows = [row for row in rows if query in str(row.get("name", "")).lower()]

        if args.json:
            payload: dict[str, Any] = {"count": len(rows), "devices": rows}
            if args.name and not rows and all_rows:
                payload["hint"] = "The filter returned 0 matches. Run without --name or try --name 'DA'."
                payload["available_names"] = sorted(
                    {str(row.get("name") or "") for row in all_rows if str(row.get("name") or "").strip()}
                )
            emit(payload, as_json=True)
            return 0

        if not rows:
            print("No BLE devices found")
            if args.name and all_rows:
                names = ", ".join(
                    sorted({str(row.get("name") or "") for row in all_rows if str(row.get("name") or "").strip()})
                )
                if names:
                    print(f"Hint: filter '{args.name}' matched nothing. Available names: {names}")
            return 0
        for row in rows:
            print(
                f"name={row.get('name') or '-'} address={row.get('address') or '-'} "
                f"rssi={row.get('rssi')} source={row.get('source') or '-'}"
            )
        return 0

    if args.ble_command == "services":
        payload = probe_ble_services(
            address=getattr(args, "address", None),
            name_query=getattr(args, "name", None),
            timeout=float(args.timeout),
            read_values=bool(getattr(args, "read", False)),
        )
        if args.json:
            emit(payload, as_json=True)
            return 0

        print(f"name={payload.get('name') or '-'} address={payload.get('address') or '-'}")
        services = payload.get("services", [])
        if not services:
            print("No GATT services discovered")
            return 0
        for service in services:
            print(f"service {service.get('uuid')} ({service.get('description') or '-'})")
            for char in service.get("characteristics", []):
                props = ",".join(char.get("properties", [])) or "-"
                line = f"  char {char.get('uuid')} props={props}"
                if char.get("value_hex"):
                    line += f" value={char['value_hex']}"
                if char.get("read_error"):
                    line += f" read_error={char['read_error']}"
                print(line)
        return 0

    if args.ble_command == "raw":
        read_chars = args.read_char if args.read_char else list(DEFAULT_RAZER_BT_READ_CHAR_UUIDS)
        payload = ble_raw_transceive(
            address=getattr(args, "address", None),
            name_query=getattr(args, "name", None),
            timeout=float(args.timeout),
            payload=parse_hex_payload(str(args.payload)),
            service_uuid=str(args.service),
            write_char_uuid=str(args.write_char),
            read_char_uuids=read_chars,
            response_timeout=float(args.response_timeout),
            enable_notify=not bool(args.no_notify),
            read_after_write=not bool(args.no_read),
            write_with_response=not bool(args.no_response),
        )
        if args.json:
            emit(payload, as_json=True)
            return 0

        print(
            f"name={payload.get('name') or '-'} address={payload.get('address') or '-'} "
            f"tx={((payload.get('write') or {}).get('payload_hex') or '-')}"
        )
        if payload.get("resolved_from"):
            print(f"resolved_from={payload['resolved_from']}")
        write = payload.get("write") or {}
        print(
            f"write char={write.get('char_uuid') or '-'} bytes={write.get('bytes')} "
            f"with_response={write.get('with_response')}"
        )
        for row in payload.get("notify", []):
            print(
                f"notify char={row.get('char_uuid')} t_ms={row.get('t_ms')} "
                f"value={row.get('value_hex')}"
            )
        for row in payload.get("notify_errors", []):
            print(f"notify_error char={row.get('char_uuid')} err={row.get('error')}")
        for row in payload.get("reads", []):
            if row.get("value_hex"):
                print(f"read char={row.get('char_uuid')} value={row.get('value_hex')}")
            else:
                print(f"read_error char={row.get('char_uuid')} err={row.get('read_error')}")
        return 0

    if args.ble_command == "alias":
        if args.ble_alias_command == "list":
            payload = ble_alias_list()
            if args.json:
                emit(payload, as_json=True)
                return 0

            print(f"path={payload.get('path')}")
            aliases = payload.get("aliases", [])
            if not aliases:
                print("No cached aliases")
                return 0
            for row in aliases:
                print(
                    f"mac={row.get('mac_address') or '-'} "
                    f"uuid={row.get('resolved_address') or '-'} "
                    f"updated_at={row.get('updated_at') or '-'}"
                )
            return 0

        if args.ble_alias_command == "clear":
            if not args.all and not args.address:
                raise RazeCliError("Specify --all to clear all aliases or --address to clear one alias")
            payload = ble_alias_clear(mac_address=None if args.all else args.address)
            if args.json:
                emit(payload, as_json=True)
                return 0
            print(
                f"path={payload.get('path')} removed={payload.get('removed')} "
                f"remaining={payload.get('remaining')}"
            )
            return 0

        if args.ble_alias_command == "resolve":
            payload = ble_alias_resolve(
                mac_address=str(args.address),
                timeout=float(args.timeout),
            )
            if args.json:
                emit(payload, as_json=True)
                return 0
            print(
                f"mac={payload.get('requested_mac') or '-'} "
                f"uuid={payload.get('resolved_address') or '-'} "
                f"path={payload.get('path') or '-'}"
            )
            return 0

        raise RazeCliError("Unknown ble alias command")

    raise RazeCliError("Unknown ble command")

