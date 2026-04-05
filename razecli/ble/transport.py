"""BLE transport helpers for raw/vendor transceive."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Sequence

from razecli.ble.alias_store import _remember_alias
from razecli.ble.common import (
    _bytes_to_hex,
    _format_exc,
    _is_mac_like_address,
    _normalize_uuid,
    _normalize_uuid_list,
    parse_hex_payload,
)
from razecli.ble.constants import (
    DEFAULT_RAZER_BT_READ_CHAR_UUIDS,
    DEFAULT_RAZER_BT_SERVICE_UUID,
    DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
)
from razecli.ble.sync_runner import run_ble_sync
from razecli.ble.discovery import (
    _auto_resolve_corebluetooth_address,
    _candidate_preview,
    _resolve_device_async,
    _retryable_connect_error,
)
from razecli.errors import BackendUnavailableError, RazeCliError

_BLE_VENDOR_REQ_ID = 0x30


def _hex_to_bytes(value: str) -> Optional[bytes]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def _parse_razer_vendor_notify(
    *,
    request_payload: bytes,
    notify_rows: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not request_payload:
        return None
    req_id = int(request_payload[0])
    request_payload_len = int(request_payload[1]) if len(request_payload) > 1 else 0
    key_hex = request_payload[4:8].hex() if len(request_payload) >= 8 else ""
    statuses = {0x02: "success", 0x03: "error", 0x05: "parameter-error"}
    key_labels = {
        "05810001": "battery-raw-read",
        "05800001": "battery-status-read",
        "0b840100": "dpi-stages-read",
        "0b040100": "dpi-stages-write",
    }

    raw_rows: List[bytes] = []
    for row in notify_rows:
        data = _hex_to_bytes(str(row.get("value_hex") or ""))
        if data is None:
            continue
        raw_rows.append(data)

    for index, row in enumerate(raw_rows):
        if len(row) < 8:
            continue
        if int(row[0]) != req_id:
            continue
        status = int(row[7])
        if status not in statuses:
            continue

        payload_len = int(row[1])
        header_len = 20 if len(row) >= 20 else 8
        payload_chunks: List[bytes] = []

        for next_row in raw_rows[index + 1 :]:
            if len(next_row) >= 8 and int(next_row[0]) == req_id and int(next_row[7]) in statuses:
                break
            payload_chunks.append(next_row)

        payload = b"".join(payload_chunks)
        if len(payload) < payload_len and len(row) > 8:
            payload += row[8:]
        payload = payload[:payload_len]

        return {
            "req_id": req_id,
            "request_payload_len": request_payload_len,
            "key_hex": key_hex,
            "key_label": key_labels.get(key_hex, "unknown"),
            "notify_payload_len": payload_len,
            "status_code": status,
            "status": statuses[status],
            "header_len": header_len,
            "payload_hex": payload.hex(),
            "header_hex": row.hex(),
            "continuation_frames": len(payload_chunks),
        }
    return None


def _next_ble_vendor_req_id() -> int:
    global _BLE_VENDOR_REQ_ID
    _BLE_VENDOR_REQ_ID = (_BLE_VENDOR_REQ_ID + 1) & 0xFF
    if _BLE_VENDOR_REQ_ID == 0:
        _BLE_VENDOR_REQ_ID = 1
    return _BLE_VENDOR_REQ_ID


def _normalize_vendor_key(key: Any) -> bytes:
    if isinstance(key, (bytes, bytearray)):
        data = bytes(key)
    else:
        data = parse_hex_payload(str(key))
    if len(data) != 4:
        raise RazeCliError("Vendor key must be exactly 4 bytes (for example 05 81 00 01)")
    return data


def _chunk_bytes(payload: bytes, chunk_size: int) -> List[bytes]:
    if chunk_size <= 0:
        return [payload]
    if not payload:
        return []
    return [payload[idx : idx + chunk_size] for idx in range(0, len(payload), chunk_size)]


def _collect_char_properties(client: Any) -> tuple[set[str], Dict[str, set[str]]]:
    services: set[str] = set()
    char_props: Dict[str, set[str]] = {}
    for service in client.services:
        service_uuid = _normalize_uuid(getattr(service, "uuid", ""))
        if service_uuid:
            services.add(service_uuid)
        for char in getattr(service, "characteristics", []):
            char_uuid = _normalize_uuid(getattr(char, "uuid", ""))
            if not char_uuid:
                continue
            props = {str(prop).lower() for prop in getattr(char, "properties", [])}
            char_props[char_uuid] = props
    return services, char_props


async def _raw_transceive_connected(
    client: Any,
    *,
    service_uuid: str,
    write_char_uuid: str,
    read_char_uuids: Sequence[str],
    payload: bytes,
    extra_write_payloads: Optional[Sequence[bytes]],
    response_timeout: float,
    enable_notify: bool,
    read_after_write: bool,
    write_with_response: bool,
) -> Dict[str, Any]:
    service_uuid = _normalize_uuid(service_uuid)
    write_char_uuid = _normalize_uuid(write_char_uuid)
    read_char_uuids = _normalize_uuid_list(read_char_uuids)

    service_uuids, char_props = _collect_char_properties(client)
    if service_uuid and service_uuid not in service_uuids:
        raise RazeCliError(f"GATT service {service_uuid} was not found on this device")
    if write_char_uuid not in char_props:
        raise RazeCliError(f"GATT write characteristic {write_char_uuid} was not found on this device")

    notify_rows: List[Dict[str, Any]] = []
    notify_errors: List[Dict[str, str]] = []
    read_rows: List[Dict[str, Any]] = []
    subscribed: List[str] = []
    notify_seen = asyncio.Event()
    loop = asyncio.get_running_loop()
    start_time = loop.time()

    def _on_notify(char_uuid: str, data: Any) -> None:
        elapsed_ms = int((loop.time() - start_time) * 1000)
        notify_rows.append(
            {
                "char_uuid": char_uuid,
                "value_hex": _bytes_to_hex(data),
                "t_ms": elapsed_ms,
            }
        )
        notify_seen.set()

    if enable_notify:
        for read_uuid in read_char_uuids:
            props = char_props.get(read_uuid, set())
            if "notify" not in props and "indicate" not in props:
                notify_errors.append(
                    {
                        "char_uuid": read_uuid,
                        "error": "characteristic does not support notify/indicate",
                    }
                )
                continue
            try:
                await client.start_notify(
                    read_uuid,
                    lambda _sender, data, _uuid=read_uuid: _on_notify(_uuid, data),
                )
                subscribed.append(read_uuid)
            except Exception as exc:
                notify_errors.append(
                    {
                        "char_uuid": read_uuid,
                        "error": _format_exc(exc),
                    }
                )

    write_result: Dict[str, Any] = {
        "char_uuid": write_char_uuid,
        "payload_hex": payload.hex(),
        "bytes": len(payload),
        "with_response": bool(write_with_response),
        "status": "ok",
        "chunks": 1,
    }
    try:
        await client.write_gatt_char(write_char_uuid, payload, response=bool(write_with_response))
    except TypeError:
        await client.write_gatt_char(write_char_uuid, payload)
        write_result["with_response"] = "unknown"
    except Exception as exc:
        raise RazeCliError(
            f"BLE write failed for {write_char_uuid}: {_format_exc(exc)}"
        ) from exc

    if extra_write_payloads:
        for chunk in extra_write_payloads:
            if not chunk:
                continue
            try:
                await client.write_gatt_char(write_char_uuid, chunk, response=bool(write_with_response))
                write_result["chunks"] = int(write_result.get("chunks", 1)) + 1
            except TypeError:
                await client.write_gatt_char(write_char_uuid, chunk)
                write_result["with_response"] = "unknown"
                write_result["chunks"] = int(write_result.get("chunks", 1)) + 1
            except Exception as exc:
                raise RazeCliError(
                    f"BLE payload write failed for {write_char_uuid}: {_format_exc(exc)}"
                ) from exc

    if enable_notify and response_timeout > 0:
        wait_timeout = max(0.05, float(response_timeout))
        try:
            await asyncio.wait_for(notify_seen.wait(), timeout=wait_timeout)
            await asyncio.sleep(min(0.08, wait_timeout))
        except asyncio.TimeoutError:
            pass

    if read_after_write:
        for read_uuid in read_char_uuids:
            props = char_props.get(read_uuid, set())
            if "read" not in props:
                read_rows.append(
                    {
                        "char_uuid": read_uuid,
                        "read_error": "characteristic does not support read",
                    }
                )
                continue
            try:
                value = await client.read_gatt_char(read_uuid)
                read_rows.append(
                    {
                        "char_uuid": read_uuid,
                        "value_hex": _bytes_to_hex(value),
                    }
                )
            except Exception as exc:
                read_rows.append(
                    {
                        "char_uuid": read_uuid,
                        "read_error": _format_exc(exc),
                    }
                )

    for read_uuid in subscribed:
        try:
            await client.stop_notify(read_uuid)
        except Exception as exc:
            notify_errors.append(
                {
                    "char_uuid": read_uuid,
                    "error": f"stop_notify: {_format_exc(exc)}",
                }
            )

    result = {
        "service_uuid": service_uuid,
        "write": write_result,
        "notify": notify_rows,
        "notify_errors": notify_errors,
        "reads": read_rows,
    }
    vendor = _parse_razer_vendor_notify(request_payload=payload, notify_rows=notify_rows)
    if vendor is not None:
        result["vendor_decode"] = vendor
    return result


async def _ble_raw_transceive_async(
    *,
    address: Optional[str],
    name_query: Optional[str],
    timeout: float,
    payload: bytes,
    service_uuid: str = DEFAULT_RAZER_BT_SERVICE_UUID,
    write_char_uuid: str = DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
    read_char_uuids: Optional[Sequence[str]] = None,
    extra_write_payloads: Optional[Sequence[bytes]] = None,
    response_timeout: float = 1.5,
    enable_notify: bool = True,
    read_after_write: bool = True,
    write_with_response: bool = True,
) -> Dict[str, Any]:
    try:
        from bleak import BleakClient  # type: ignore
    except Exception as exc:
        raise BackendUnavailableError(
            "BLE probe requires bleak. Install with: pip install 'bleak>=0.22' "
            "or pip install -e '.[ble]'"
        ) from exc

    read_char_uuids = _normalize_uuid_list(read_char_uuids or DEFAULT_RAZER_BT_READ_CHAR_UUIDS)
    device = await _resolve_device_async(address=address, name_query=name_query, timeout=timeout)

    result: Dict[str, Any] = {
        "name": getattr(device, "name", "") or "",
        "address": getattr(device, "address", "") if not isinstance(device, str) else device,
        "request": {
            "service_uuid": _normalize_uuid(service_uuid),
            "write_char_uuid": _normalize_uuid(write_char_uuid),
            "read_char_uuids": list(read_char_uuids),
            "payload_hex": payload.hex(),
            "response_timeout": float(response_timeout),
            "notify_enabled": bool(enable_notify),
            "read_after_write": bool(read_after_write),
            "write_with_response": bool(write_with_response),
        },
    }

    target = str(result.get("address") or "-")
    if isinstance(device, str) and _is_mac_like_address(device):
        connect_error: Optional[str] = None
        try:
            auto = await _auto_resolve_corebluetooth_address(mac_address=device, timeout=timeout)
        except Exception as exc:
            raise RazeCliError(
                f"BLE raw transceive failed for {target}: auto-resolution failed: {_format_exc(exc)}"
            ) from exc
        resolved_address = auto.get("resolved_address")
        resolved_device = auto.get("resolved_device")
        if resolved_address:
            try:
                connect_target: Any = resolved_device or str(resolved_address)
                async with BleakClient(connect_target, timeout=timeout) as client:
                    result["resolved_from"] = device
                    result["address"] = str(resolved_address)
                    raw = await _raw_transceive_connected(
                        client,
                        service_uuid=service_uuid,
                        write_char_uuid=write_char_uuid,
                        read_char_uuids=read_char_uuids,
                        payload=payload,
                        extra_write_payloads=extra_write_payloads,
                        response_timeout=response_timeout,
                        enable_notify=enable_notify,
                        read_after_write=read_after_write,
                        write_with_response=write_with_response,
                    )
                    result.update(raw)
                    result["auto_resolution"] = {
                        "requested_mac": device,
                        "resolved_address": str(resolved_address),
                    }
                    _remember_alias(device, str(resolved_address))
                    return result
            except Exception as exc:
                first_error = _format_exc(exc)
                connect_error = first_error
                if not _retryable_connect_error(first_error):
                    raise RazeCliError(
                        f"BLE raw transceive failed for {target}: {first_error}"
                    ) from exc

                try:
                    refreshed = await _auto_resolve_corebluetooth_address(
                        mac_address=device,
                        timeout=timeout,
                        allow_alias_cache=False,
                    )
                except Exception as refresh_resolve_exc:
                    raise RazeCliError(
                        f"BLE raw transceive failed for {target}: refresh auto-resolution failed: "
                        f"{_format_exc(refresh_resolve_exc)}"
                    ) from refresh_resolve_exc
                refreshed_address = refreshed.get("resolved_address")
                refreshed_device = refreshed.get("resolved_device")
                if refreshed_address:
                    try:
                        connect_target = refreshed_device or str(refreshed_address)
                        async with BleakClient(connect_target, timeout=timeout) as client:
                            result["resolved_from"] = device
                            result["address"] = str(refreshed_address)
                            raw = await _raw_transceive_connected(
                                client,
                                service_uuid=service_uuid,
                                write_char_uuid=write_char_uuid,
                                read_char_uuids=read_char_uuids,
                                payload=payload,
                                extra_write_payloads=extra_write_payloads,
                                response_timeout=response_timeout,
                                enable_notify=enable_notify,
                                read_after_write=read_after_write,
                                write_with_response=write_with_response,
                            )
                            result.update(raw)
                            result["auto_resolution"] = {
                                "requested_mac": device,
                                "resolved_address": str(refreshed_address),
                            }
                            _remember_alias(device, str(refreshed_address))
                            return result
                    except Exception as refresh_exc:
                        connect_error = f"{first_error}; refresh={_format_exc(refresh_exc)}"
                auto = refreshed

        candidates = auto.get("candidates", [])
        preview = _candidate_preview(candidates)
        has_connect_ok = any(bool(row.get("connect_ok")) for row in candidates)
        if has_connect_ok and connect_error:
            raise RazeCliError(
                f"BLE raw transceive failed for {target}: a candidate was found but connection failed ({connect_error}). "
                f"Candidates: {preview}"
            )
        raise RazeCliError(
            f"BLE raw transceive failed for {target}: device with address {device} was not found. "
            "On macOS, CoreBluetooth UUIDs are often used instead of MAC addresses. "
            f"Auto-resolution did not find a clear candidate. Candidates: {preview}"
        )

    try:
        async with BleakClient(device, timeout=timeout) as client:
            raw = await _raw_transceive_connected(
                client,
                service_uuid=service_uuid,
                write_char_uuid=write_char_uuid,
                read_char_uuids=read_char_uuids,
                payload=payload,
                extra_write_payloads=extra_write_payloads,
                response_timeout=response_timeout,
                enable_notify=enable_notify,
                read_after_write=read_after_write,
                write_with_response=write_with_response,
            )
            result.update(raw)
            return result
    except Exception as exc:
        raise RazeCliError(
            f"BLE raw transceive failed for {target}: {_format_exc(exc)}"
        ) from exc


async def _ble_vendor_transceive_async(
    *,
    address: Optional[str],
    name_query: Optional[str],
    timeout: float,
    key: Any,
    value_payload: Optional[bytes] = None,
    response_timeout: float = 1.0,
    write_with_response: bool = True,
    notify_enabled: bool = True,
    service_uuid: str = DEFAULT_RAZER_BT_SERVICE_UUID,
    write_char_uuid: str = DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
    read_char_uuids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    key_bytes = _normalize_vendor_key(key)
    payload = bytes(value_payload or b"")
    if len(payload) > 0xFF:
        raise RazeCliError("Vendor payload must be at most 255 bytes")

    request_id = _next_ble_vendor_req_id()
    header = bytes([request_id, len(payload) & 0xFF, 0x00, 0x00]) + key_bytes
    chunk_size = int(os.getenv("RAZECLI_BLE_VENDOR_CHUNK_SIZE", "20") or "20")
    if chunk_size < 1:
        chunk_size = 20
    chunks = _chunk_bytes(payload, chunk_size)

    result = await _ble_raw_transceive_async(
        address=address,
        name_query=name_query,
        timeout=timeout,
        payload=header,
        service_uuid=service_uuid,
        write_char_uuid=write_char_uuid,
        read_char_uuids=read_char_uuids or DEFAULT_RAZER_BT_READ_CHAR_UUIDS,
        extra_write_payloads=chunks,
        response_timeout=response_timeout,
        enable_notify=notify_enabled,
        # Vendor framing is notification-driven; extra read requests can race with
        # CoreBluetooth callbacks on some macOS hosts and cause InvalidStateError.
        read_after_write=False,
        write_with_response=write_with_response,
    )
    result["vendor_request"] = {
        "request_id": request_id,
        "key_hex": key_bytes.hex(),
        "payload_len": len(payload),
        "chunk_size": chunk_size,
        "chunks": len(chunks),
    }
    return result


def ble_raw_transceive(
    *,
    address: Optional[str],
    name_query: Optional[str],
    timeout: float,
    payload: bytes,
    service_uuid: str = DEFAULT_RAZER_BT_SERVICE_UUID,
    write_char_uuid: str = DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
    read_char_uuids: Optional[Sequence[str]] = None,
    extra_write_payloads: Optional[Sequence[bytes]] = None,
    response_timeout: float = 1.5,
    enable_notify: bool = True,
    read_after_write: bool = True,
    write_with_response: bool = True,
) -> Dict[str, Any]:
    return run_ble_sync(
        _ble_raw_transceive_async(
            address=address,
            name_query=name_query,
            timeout=timeout,
            payload=payload,
            service_uuid=service_uuid,
            write_char_uuid=write_char_uuid,
            read_char_uuids=read_char_uuids,
            extra_write_payloads=extra_write_payloads,
            response_timeout=response_timeout,
            enable_notify=enable_notify,
            read_after_write=read_after_write,
            write_with_response=write_with_response,
        )
    )


def ble_vendor_transceive(
    *,
    address: Optional[str],
    name_query: Optional[str],
    timeout: float,
    key: Any,
    value_payload: Optional[bytes] = None,
    response_timeout: float = 1.0,
    write_with_response: bool = True,
    notify_enabled: bool = True,
    service_uuid: str = DEFAULT_RAZER_BT_SERVICE_UUID,
    write_char_uuid: str = DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
    read_char_uuids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return run_ble_sync(
        _ble_vendor_transceive_async(
            address=address,
            name_query=name_query,
            timeout=timeout,
            key=key,
            value_payload=value_payload,
            response_timeout=response_timeout,
            write_with_response=write_with_response,
            notify_enabled=notify_enabled,
            service_uuid=service_uuid,
            write_char_uuid=write_char_uuid,
            read_char_uuids=read_char_uuids,
        )
    )
