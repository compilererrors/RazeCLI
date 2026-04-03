"""Handlers for the `ble` command."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from datetime import datetime, timezone
from typing import Any, Optional

from razecli.backends.macos_ble_backend import MacOSBleBackend
from razecli.ble.bank_snapshot_store import (
    append_bank_snapshot,
    get_latest_snapshot_by_label,
    list_bank_snapshots,
)
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

DEFAULT_BT_BANK_PROBE_KEYS = (
    "0b840100",
    "0b840000",
)

DEFAULT_BT_BANK_PROBE_DEEP_KEYS = (
    "0b840100",
    "0b840000",
    "0b840101",
    "0b840001",
    "0b840200",
    "0b840201",
    "0b840300",
    "0b840301",
    "00840100",
    "00840000",
    "00840101",
    "00840001",
)

DEFAULT_BT_BANK_PROBE_DEEP_WRITE_KEYS = (
    "0b040100",
    "0b040000",
    "0b040101",
    "0b040001",
)


def _normalize_probe_keys(raw_keys: Optional[list[str]], *, default_keys: tuple[str, ...]) -> list[bytes]:
    if raw_keys:
        tokens = [str(token).strip() for token in raw_keys]
    else:
        tokens = list(default_keys)

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


def _decode_bank_payload_hex(payload_hex: str) -> Optional[dict[str, Any]]:
    text = str(payload_hex or "").strip()
    if not text:
        return None
    try:
        payload = bytes.fromhex(text)
    except ValueError:
        return None

    try:
        active_stage, stages, stage_ids, marker = MacOSBleBackend._parse_stages_payload(payload)
    except Exception:
        return None

    stage_rows = []
    for idx, (dpi_x, dpi_y) in enumerate(stages, start=1):
        stage_rows.append(
            {
                "index": int(idx),
                "stage_id": int(stage_ids[idx - 1]) if idx - 1 < len(stage_ids) else int(idx),
                "dpi_x": int(dpi_x),
                "dpi_y": int(dpi_y),
            }
        )

    signature_seed = {
        "active_stage": int(active_stage),
        "marker": int(marker),
        "stages": stage_rows,
    }
    signature = hashlib.sha1(
        json.dumps(signature_seed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]

    return {
        "active_stage": int(active_stage),
        "stages_count": len(stage_rows),
        "stages": stage_rows,
        "marker": int(marker),
        "bank_signature": signature,
    }


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


def _resolve_probe_target(*, requested_address: str, timeout: float) -> dict[str, Optional[str]]:
    probe_address = requested_address or None
    resolved_from: Optional[str] = None
    resolved_address: Optional[str] = None
    resolution_error: Optional[str] = None
    if _is_mac_address(requested_address):
        try:
            alias_payload = ble_alias_resolve(
                mac_address=requested_address,
                timeout=float(timeout),
            )
            candidate = str(alias_payload.get("resolved_address") or "").strip()
            if candidate:
                probe_address = candidate
                resolved_from = requested_address
                resolved_address = candidate
        except Exception as exc:
            # Keep probing with the original address; ble_vendor_transceive may still resolve it.
            resolution_error = str(exc)
    return {
        "probe_address": probe_address,
        "resolved_from": resolved_from,
        "resolved_address": resolved_address,
        "resolution_error": resolution_error,
    }


def _candidate_probe_addresses(
    *,
    requested_address: str,
    probe_address: Optional[str],
    fallback_to_mac: bool,
) -> list[Optional[str]]:
    if fallback_to_mac and requested_address:
        candidates = [requested_address]
    else:
        candidates = [probe_address]
        if requested_address and requested_address != probe_address and _is_mac_address(requested_address):
            candidates.append(requested_address)
    normalized = [addr for addr in candidates if addr]
    if normalized:
        return normalized
    return [None]


def _snapshot_stage_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    signatures = snapshot.get("signatures")
    if isinstance(signatures, list) and signatures:
        first = signatures[0]
        if isinstance(first, dict):
            rows = first.get("stages")
            if isinstance(rows, list):
                cleaned: list[dict[str, Any]] = []
                for row in rows:
                    if isinstance(row, dict):
                        cleaned.append(dict(row))
                if cleaned:
                    return cleaned

    results = snapshot.get("results")
    if isinstance(results, list):
        for row in results:
            if not isinstance(row, dict):
                continue
            decoded = row.get("decoded_bank")
            if not isinstance(decoded, dict):
                continue
            stages = decoded.get("stages")
            if not isinstance(stages, list):
                continue
            cleaned = [dict(item) for item in stages if isinstance(item, dict)]
            if cleaned:
                return cleaned
    return []


def _index_stage_rows(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for idx, row in enumerate(rows, start=1):
        try:
            index = int(row.get("index") or idx)
        except Exception:
            index = idx
        indexed[index] = row
    return indexed


def _build_snapshot_compare_payload(
    *,
    label_a: str,
    label_b: str,
    snapshot_a: dict[str, Any],
    snapshot_b: dict[str, Any],
    path: str,
) -> dict[str, Any]:
    sig_a = str(snapshot_a.get("primary_bank_signature") or "").strip() or None
    sig_b = str(snapshot_b.get("primary_bank_signature") or "").strip() or None

    rows_a = _snapshot_stage_rows(snapshot_a)
    rows_b = _snapshot_stage_rows(snapshot_b)
    indexed_a = _index_stage_rows(rows_a)
    indexed_b = _index_stage_rows(rows_b)

    all_indices = sorted(set(indexed_a.keys()) | set(indexed_b.keys()))
    stage_diffs: list[dict[str, Any]] = []
    for index in all_indices:
        row_a = indexed_a.get(index) or {}
        row_b = indexed_b.get(index) or {}
        a_x = row_a.get("dpi_x")
        a_y = row_a.get("dpi_y")
        b_x = row_b.get("dpi_x")
        b_y = row_b.get("dpi_y")
        if (a_x, a_y) == (b_x, b_y):
            continue
        stage_diffs.append(
            {
                "index": int(index),
                "a": {"dpi_x": a_x, "dpi_y": a_y},
                "b": {"dpi_x": b_x, "dpi_y": b_y},
            }
        )

    signature_equal = bool(sig_a and sig_b and sig_a == sig_b)
    status = "same" if signature_equal and not stage_diffs else "different"

    return {
        "status": status,
        "path": path,
        "label_a": label_a,
        "label_b": label_b,
        "snapshot_a": {
            "captured_at": snapshot_a.get("captured_at"),
            "status": snapshot_a.get("status"),
            "primary_bank_signature": sig_a,
            "stages": rows_a,
        },
        "snapshot_b": {
            "captured_at": snapshot_b.get("captured_at"),
            "status": snapshot_b.get("status"),
            "primary_bank_signature": sig_b,
            "stages": rows_b,
        },
        "signature_equal": signature_equal,
        "stage_differences": stage_diffs,
    }


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
        keys = _normalize_probe_keys(
            getattr(args, "key", None),
            default_keys=DEFAULT_BT_POLL_PROBE_KEYS,
        )
        attempts = max(1, int(getattr(args, "attempts", 1)))
        rows: list[dict[str, Any]] = []
        first_match_hz: Optional[int] = None
        seen_reject = False
        seen_parameter_error = False

        requested_address = str(getattr(args, "address", "") or "").strip()
        resolution = _resolve_probe_target(requested_address=requested_address, timeout=float(args.timeout))
        probe_address = resolution["probe_address"]
        resolved_from = resolution["resolved_from"]
        resolved_address = resolution["resolved_address"]
        resolution_error = resolution["resolution_error"]
        fallback_to_mac = False

        for round_idx in range(attempts):
            for key in keys:
                candidate_addresses = _candidate_probe_addresses(
                    requested_address=requested_address,
                    probe_address=probe_address,
                    fallback_to_mac=fallback_to_mac,
                )

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

    if args.ble_command in {"bank-probe", "bank-snapshot"}:
        deep_probe = bool(getattr(args, "deep", False))
        include_write_keys = bool(getattr(args, "include_write_keys", False))
        default_bank_keys = DEFAULT_BT_BANK_PROBE_DEEP_KEYS if deep_probe else DEFAULT_BT_BANK_PROBE_KEYS
        if deep_probe and include_write_keys:
            merged = list(default_bank_keys) + list(DEFAULT_BT_BANK_PROBE_DEEP_WRITE_KEYS)
            default_bank_keys = tuple(dict.fromkeys(merged))
        keys = _normalize_probe_keys(
            getattr(args, "key", None),
            default_keys=default_bank_keys,
        )
        write_key_set = {
            token.strip().lower()
            for token in DEFAULT_BT_BANK_PROBE_DEEP_WRITE_KEYS
            if token and token.strip()
        }
        write_keys_included = any(key.hex() in write_key_set for key in keys)
        attempts = max(1, int(getattr(args, "attempts", 1)))
        settle_delay_raw = getattr(args, "settle_delay", None)
        if settle_delay_raw is None:
            settle_delay_s = 0.35 if deep_probe and attempts > 1 else 0.0
        else:
            settle_delay_s = max(0.0, float(settle_delay_raw))
        reconnect_each_round_arg = getattr(args, "reconnect_each_round", None)
        if reconnect_each_round_arg is None:
            reconnect_each_round = bool(deep_probe and attempts > 1)
        else:
            reconnect_each_round = bool(reconnect_each_round_arg)
        rows: list[dict[str, Any]] = []
        seen_reject = False
        seen_parameter_error = False
        signature_rows: dict[str, dict[str, Any]] = {}
        round_resolution_errors: list[dict[str, Any]] = []

        requested_address = str(getattr(args, "address", "") or "").strip()
        resolution = _resolve_probe_target(requested_address=requested_address, timeout=float(args.timeout))
        probe_address = resolution["probe_address"]
        resolved_from = resolution["resolved_from"]
        resolved_address = resolution["resolved_address"]
        resolution_error = resolution["resolution_error"]
        fallback_to_mac = False
        if resolution_error:
            round_resolution_errors.append({"round": 0, "error": resolution_error})

        for round_idx in range(attempts):
            if round_idx > 0:
                if settle_delay_s > 0:
                    time.sleep(settle_delay_s)
                if reconnect_each_round and requested_address:
                    round_resolution = _resolve_probe_target(
                        requested_address=requested_address,
                        timeout=float(args.timeout),
                    )
                    next_probe_address = round_resolution.get("probe_address")
                    if isinstance(next_probe_address, str) and next_probe_address.strip():
                        probe_address = next_probe_address.strip()
                    next_resolved_from = round_resolution.get("resolved_from")
                    next_resolved_address = round_resolution.get("resolved_address")
                    if (
                        isinstance(next_resolved_from, str)
                        and next_resolved_from
                        and isinstance(next_resolved_address, str)
                        and next_resolved_address
                    ):
                        resolved_from = next_resolved_from
                        resolved_address = next_resolved_address
                    next_resolution_error = str(round_resolution.get("resolution_error") or "").strip()
                    if next_resolution_error:
                        round_resolution_errors.append(
                            {
                                "round": int(round_idx + 1),
                                "error": next_resolution_error,
                            }
                        )
            for key in keys:
                candidate_addresses = _candidate_probe_addresses(
                    requested_address=requested_address,
                    probe_address=probe_address,
                    fallback_to_mac=fallback_to_mac,
                )

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

                        decoded_bank = _decode_bank_payload_hex(payload_hex)
                        if status_code == 3:
                            seen_reject = True
                        if status_code == 5:
                            seen_parameter_error = True

                        if used_fallback:
                            fallback_to_mac = True

                        row: dict[str, Any] = {
                            "round": int(round_idx + 1),
                            "key_hex": key.hex(),
                            "used_address": candidate_address,
                            "used_fallback_address": bool(used_fallback),
                            "status": status,
                            "status_code": status_code,
                            "payload_hex": payload_hex,
                            "payload_len": len(payload_hex) // 2 if payload_hex else 0,
                        }
                        if isinstance(decoded_bank, dict):
                            signature = str(decoded_bank.get("bank_signature") or "").strip()
                            row["decoded_bank"] = decoded_bank
                            row["bank_signature"] = signature

                            if signature:
                                bucket = signature_rows.get(signature)
                                if bucket is None:
                                    bucket = {
                                        "bank_signature": signature,
                                        "active_stage": int(decoded_bank.get("active_stage") or 1),
                                        "stages_count": int(decoded_bank.get("stages_count") or 0),
                                        "stages": decoded_bank.get("stages") or [],
                                        "marker": int(decoded_bank.get("marker") or 0),
                                        "count": 0,
                                    }
                                    signature_rows[signature] = bucket
                                bucket["count"] = int(bucket.get("count") or 0) + 1

                        rows.append(row)
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
                            "error": last_error or "Unknown BLE bank probe error",
                        }
                    )

        signatures = sorted(
            signature_rows.values(),
            key=lambda row: (-int(row.get("count") or 0), str(row.get("bank_signature") or "")),
        )
        primary_bank_signature = str(signatures[0]["bank_signature"]) if signatures else None
        bank_signature = primary_bank_signature if len(signatures) == 1 else None

        if signatures:
            result_status = "ok"
        elif seen_reject or seen_parameter_error:
            result_status = "unsupported"
        elif any("error" in row for row in rows):
            result_status = "transport-error"
        else:
            result_status = "error"

        payload = {
            "status": result_status,
            "attempts": attempts,
            "deep": deep_probe,
            "include_write_keys": include_write_keys,
            "write_keys_included": write_keys_included,
            "settle_delay_s": settle_delay_s,
            "reconnect_each_round": reconnect_each_round,
            "keys": [key.hex() for key in keys],
            "results": rows,
            "signatures": signatures,
            "primary_bank_signature": primary_bank_signature,
            "bank_signature": bank_signature,
            "mac_fallback_active": bool(fallback_to_mac),
        }
        if resolved_from and resolved_address:
            payload["auto_resolution"] = {
                "requested_mac": resolved_from,
                "resolved_address": resolved_address,
            }
        if resolution_error:
            payload["auto_resolution_error"] = resolution_error
        if round_resolution_errors:
            payload["round_resolution_errors"] = round_resolution_errors

        if args.ble_command == "bank-snapshot":
            snapshot = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "label": str(getattr(args, "label", "") or "").strip() or None,
                "status": payload["status"],
                "requested_address": requested_address or None,
                "primary_bank_signature": payload.get("primary_bank_signature"),
                "bank_signature": payload.get("bank_signature"),
                "signatures": payload.get("signatures", []),
                "results": payload.get("results", []),
            }
            if payload.get("auto_resolution"):
                snapshot["auto_resolution"] = payload["auto_resolution"]
            try:
                saved = append_bank_snapshot(
                    snapshot=snapshot,
                    path_override=getattr(args, "path", None),
                )
            except Exception as exc:
                raise RazeCliError(f"Could not save BLE bank snapshot: {exc}") from exc
            payload["snapshot"] = {
                "path": saved.get("path"),
                "count": saved.get("count"),
                "captured_at": snapshot["captured_at"],
                "label": snapshot.get("label"),
            }

        if args.json:
            emit(payload, as_json=True)
            return 0

        print(
            f"status={payload['status']} primary_signature={payload.get('primary_bank_signature') or '-'} "
            f"signatures={len(payload.get('signatures') or [])}"
        )
        for row in rows:
            if row.get("error"):
                print(f"round={row['round']} key={row['key_hex']} error={row['error']}")
                continue
            decoded = row.get("decoded_bank") or {}
            print(
                f"round={row['round']} key={row['key_hex']} status={row.get('status') or '-'} "
                f"status_code={row.get('status_code')} payload_len={row.get('payload_len')} "
                f"active={decoded.get('active_stage')} count={decoded.get('stages_count')} "
                f"signature={row.get('bank_signature') or '-'}"
            )
        if args.ble_command == "bank-snapshot":
            snapshot_meta = payload.get("snapshot") or {}
            print(
                f"snapshot_path={snapshot_meta.get('path') or '-'} "
                f"snapshot_count={snapshot_meta.get('count')}"
            )
        return 0

    if args.ble_command == "bank-compare":
        label_a = str(getattr(args, "label_a", "") or "").strip()
        label_b = str(getattr(args, "label_b", "") or "").strip()
        if not label_a or not label_b:
            raise RazeCliError("Both --label-a and --label-b are required")

        snapshot_a = get_latest_snapshot_by_label(
            label=label_a,
            path_override=getattr(args, "path", None),
        )
        snapshot_b = get_latest_snapshot_by_label(
            label=label_b,
            path_override=getattr(args, "path", None),
        )
        listing = list_bank_snapshots(path_override=getattr(args, "path", None))
        snapshot_path = str(listing.get("path") or "-")

        if not isinstance(snapshot_a, dict):
            raise RazeCliError(f"No bank snapshot found for label '{label_a}' in {snapshot_path}")
        if not isinstance(snapshot_b, dict):
            raise RazeCliError(f"No bank snapshot found for label '{label_b}' in {snapshot_path}")

        payload = _build_snapshot_compare_payload(
            label_a=label_a,
            label_b=label_b,
            snapshot_a=snapshot_a,
            snapshot_b=snapshot_b,
            path=snapshot_path,
        )

        if args.json:
            emit(payload, as_json=True)
            return 0

        print(
            f"status={payload['status']} signature_equal={payload.get('signature_equal')} "
            f"a={payload.get('snapshot_a', {}).get('primary_bank_signature') or '-'} "
            f"b={payload.get('snapshot_b', {}).get('primary_bank_signature') or '-'}"
        )
        diffs = payload.get("stage_differences")
        if not isinstance(diffs, list) or not diffs:
            print("No stage differences")
            return 0
        for row in diffs:
            if not isinstance(row, dict):
                continue
            index = row.get("index")
            a = row.get("a") if isinstance(row.get("a"), dict) else {}
            b = row.get("b") if isinstance(row.get("b"), dict) else {}
            print(
                f"stage={index} "
                f"a={a.get('dpi_x')}:{a.get('dpi_y')} "
                f"b={b.get('dpi_x')}:{b.get('dpi_y')}"
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
