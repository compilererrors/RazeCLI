"""Handlers for the `ble` command."""

from __future__ import annotations

import argparse
import platform
from typing import Any, Optional

from razecli.backends.macos_ble_backend import MacOSBleBackend
from razecli.ble_probe import (
    DEFAULT_RAZER_BT_READ_CHAR_UUIDS,
    ble_alias_clear,
    ble_alias_list,
    ble_alias_resolve,
    ble_raw_transceive,
    ble_vendor_transceive,
    parse_hex_payload,
    probe_ble_services,
    scan_ble_devices,
)
from razecli.cli_common import emit
from razecli.errors import RazeCliError

DEFAULT_BT_POLL_PROBE_KEYS = (
    "00850001",
    "00850000",
    "00850100",
    "0b850100",
    "0b850000",
)


def _normalize_probe_keys(raw_keys: Optional[list[str]]) -> list[bytes]:
    if raw_keys:
        tokens = [str(token).strip() for token in raw_keys]
    else:
        tokens = list(DEFAULT_BT_POLL_PROBE_KEYS)

    parsed: list[bytes] = []
    seen: set[bytes] = set()
    for token in tokens:
        if not token:
            continue
        key = parse_hex_payload(token)
        if len(key) != 4:
            raise RazeCliError(f"Invalid --key '{token}': expected 4 bytes")
        if key in seen:
            continue
        seen.add(key)
        parsed.append(key)
    if not parsed:
        raise RazeCliError("No valid probe keys resolved")
    return parsed


def _decode_poll_rate_payload_hex(payload_hex: str) -> Optional[int]:
    text = str(payload_hex or "").strip()
    if not text:
        return None
    try:
        payload = bytes.fromhex(text)
    except ValueError:
        return None
    return MacOSBleBackend._decode_poll_rate_payload(payload)


def _is_mac_address(text: Optional[str]) -> bool:
    value = str(text or "").strip()
    parts = value.split(":")
    if len(parts) != 6:
        return False
    for part in parts:
        if len(part) != 2:
            return False
        try:
            int(part, 16)
        except ValueError:
            return False
    return True


def _is_device_not_found_error(message: str) -> bool:
    text = str(message or "").lower()
    return "was not found" in text and "device with address" in text


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

    if args.ble_command == "poll-probe":
        keys = _normalize_probe_keys(getattr(args, "key", None))
        attempts = max(1, int(getattr(args, "attempts", 1)))
        rows: list[dict[str, Any]] = []
        first_match_hz: Optional[int] = None
        seen_reject = False
        seen_parameter_error = False
        resolved_from: Optional[str] = None
        resolved_address: Optional[str] = None
        resolution_error: Optional[str] = None

        requested_address = str(getattr(args, "address", "") or "").strip()
        probe_address = requested_address or None
        fallback_to_mac = False
        if _is_mac_address(requested_address):
            try:
                alias_payload = ble_alias_resolve(
                    mac_address=requested_address,
                    timeout=float(args.timeout),
                )
                candidate = str(alias_payload.get("resolved_address") or "").strip()
                if candidate:
                    probe_address = candidate
                    resolved_from = requested_address
                    resolved_address = candidate
            except Exception as exc:
                # Keep probing with the original address; ble_vendor_transceive may still resolve it.
                resolution_error = str(exc)

        for round_idx in range(attempts):
            for key in keys:
                candidate_addresses: list[Optional[str]] = []
                if fallback_to_mac and requested_address:
                    candidate_addresses = [requested_address]
                else:
                    candidate_addresses = [probe_address]
                    if (
                        requested_address
                        and requested_address != probe_address
                        and _is_mac_address(requested_address)
                    ):
                        candidate_addresses.append(requested_address)
                candidate_addresses = [addr for addr in candidate_addresses if addr]
                if not candidate_addresses:
                    candidate_addresses = [None]

                row_emitted = False
                last_error: Optional[str] = None
                for candidate_address in candidate_addresses:
                    used_fallback = bool(
                        _is_mac_address(requested_address)
                        and candidate_address == requested_address
                        and candidate_address != probe_address
                    )
                    try:
                        result = ble_vendor_transceive(
                            address=candidate_address,
                            name_query=getattr(args, "name", None),
                            timeout=float(args.timeout),
                            key=key,
                            value_payload=None,
                            response_timeout=float(args.response_timeout),
                            write_with_response=True,
                            notify_enabled=True,
                        )
                        vendor_decode = result.get("vendor_decode") if isinstance(result, dict) else {}
                        if not isinstance(vendor_decode, dict):
                            vendor_decode = {}
                        payload_hex = str(vendor_decode.get("payload_hex") or "").strip().lower()
                        status = str(vendor_decode.get("status") or "").strip().lower() or None
                        status_code_raw = vendor_decode.get("status_code")
                        try:
                            status_code = int(status_code_raw) if status_code_raw is not None else None
                        except Exception:
                            status_code = None

                        decoded_hz = _decode_poll_rate_payload_hex(payload_hex)
                        if decoded_hz is not None and first_match_hz is None:
                            first_match_hz = int(decoded_hz)
                        if status_code == 3:
                            seen_reject = True
                        if status_code == 5:
                            seen_parameter_error = True

                        if used_fallback:
                            fallback_to_mac = True

                        rows.append(
                            {
                                "round": int(round_idx + 1),
                                "key_hex": key.hex(),
                                "used_address": candidate_address,
                                "used_fallback_address": bool(used_fallback),
                                "status": status,
                                "status_code": status_code,
                                "payload_hex": payload_hex,
                                "payload_len": len(payload_hex) // 2 if payload_hex else 0,
                                "decoded_hz": decoded_hz,
                            }
                        )
                        row_emitted = True
                        break
                    except Exception as exc:
                        last_error = str(exc)
                        if used_fallback:
                            fallback_to_mac = True
                        if _is_device_not_found_error(last_error) and _is_mac_address(requested_address):
                            continue
                        rows.append(
                            {
                                "round": int(round_idx + 1),
                                "key_hex": key.hex(),
                                "used_address": candidate_address,
                                "used_fallback_address": bool(used_fallback),
                                "error": last_error,
                            }
                        )
                        row_emitted = True
                        break

                if not row_emitted:
                    rows.append(
                        {
                            "round": int(round_idx + 1),
                            "key_hex": key.hex(),
                            "used_address": requested_address or probe_address,
                            "used_fallback_address": bool(_is_mac_address(requested_address)),
                            "error": last_error or "Unknown BLE poll probe error",
                        }
                    )

        if first_match_hz is not None:
            result_status = "ok"
        elif seen_reject or seen_parameter_error:
            result_status = "unsupported"
        elif any("error" in row for row in rows):
            result_status = "transport-error"
        else:
            result_status = "error"
        payload = {
            "status": result_status,
            "decoded_hz": first_match_hz,
            "attempts": attempts,
            "keys": [key.hex() for key in keys],
            "results": rows,
            "mac_fallback_active": bool(fallback_to_mac),
        }
        if resolved_from and resolved_address:
            payload["auto_resolution"] = {
                "requested_mac": resolved_from,
                "resolved_address": resolved_address,
            }
        if resolution_error:
            payload["auto_resolution_error"] = resolution_error

        if args.json:
            emit(payload, as_json=True)
            return 0

        print(f"status={payload['status']} decoded_hz={payload.get('decoded_hz')}")
        for row in rows:
            if row.get("error"):
                print(f"round={row['round']} key={row['key_hex']} error={row['error']}")
                continue
            print(
                f"round={row['round']} key={row['key_hex']} status={row.get('status') or '-'} "
                f"status_code={row.get('status_code')} payload_len={row.get('payload_len')} "
                f"payload={row.get('payload_hex') or '-'} decoded_hz={row.get('decoded_hz')}"
            )
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
