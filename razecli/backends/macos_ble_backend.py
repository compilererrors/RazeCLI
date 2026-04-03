"""Experimental macOS BLE backend for Razer BT endpoints.

Implements vendor-GATT transactions over macOS CoreBluetooth for Bluetooth PID
endpoints (currently validated for DA V2 Pro `0x008E`).
"""

from __future__ import annotations

import asyncio
import os
import platform
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from razecli.backends.base import Backend
from razecli.backends.macos_profiler_backend import MacOSProfilerBackend
from razecli.backends.rawhid_backend import RawHidBackend
from razecli.errors import BackendUnavailableError, CapabilityUnsupportedError
from razecli.types import DetectedDevice

RAZER_VENDOR_ID = 0x1532
DEFAULT_BLE_PRODUCT_IDS = frozenset({0x008E, 0x0083})
BLE_CAPABILITIES = frozenset({"battery", "dpi", "dpi-stages"})

KEY_BATTERY_RAW_READ = bytes.fromhex("05810001")
KEY_BATTERY_STATUS_READ = bytes.fromhex("05800001")
KEY_DPI_STAGES_READ = bytes.fromhex("0B840100")
KEY_DPI_STAGES_WRITE = bytes.fromhex("0B040100")
DEFAULT_DPI_READ_KEYS = ("0b840100", "0b840000")
DEFAULT_DPI_WRITE_KEYS = ("0b040100", "0b040000")
DEFAULT_POLL_READ_KEYS = ("00850001", "00850000", "0b850100")
DEFAULT_POLL_WRITE_KEYS = ("00050001", "00050000", "0b050100")
PRIMARY_VENDOR_READ_CHAR_UUID = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"
BATTERY_LEVEL_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
POLL_RATE_TO_CODE = {
    1000: 0x01,
    500: 0x02,
    125: 0x08,
}
CODE_TO_POLL_RATE = {value: key for key, value in POLL_RATE_TO_CODE.items()}
MAX_DPI_STAGES = 5
MIN_PLAUSIBLE_DPI = 100
MAX_PLAUSIBLE_DPI = 30000
DEFAULT_NAME_QUERY = "DA V2 Pro"


class MacOSBleBackend(Backend):
    name = "macos-ble"

    def __init__(self) -> None:
        self.last_error = None
        self._supported = platform.system() == "Darwin"
        self._ble_product_ids = self._load_product_ids()
        self._poll_capability_enabled = self._env_flag("RAZECLI_BLE_POLL_CAP", default=False)
        self._rawhid = RawHidBackend()
        self._profiler = MacOSProfilerBackend()
        if not self._supported:
            self.last_error = BackendUnavailableError("macos-ble backend is supported on macOS only")
        elif self._rawhid.last_error is not None and self._profiler.last_error is not None:
            self.last_error = self._rawhid.last_error

    @staticmethod
    def _env_flag(name: str, *, default: bool = False) -> bool:
        raw = str(os.getenv(name, "")).strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_hex_key_list(raw: str, *, default_tokens: Sequence[str]) -> List[bytes]:
        text = str(raw or "").strip()
        if not text:
            tokens = [str(token).strip() for token in default_tokens]
        else:
            tokens = [token.strip() for token in text.split(",")]

        parsed: List[bytes] = []
        seen: Set[bytes] = set()
        for token in tokens:
            cleaned = token.lower().replace("0x", "")
            for separator in (" ", "\t", "\n", "\r", ":", "-", "_"):
                cleaned = cleaned.replace(separator, "")
            if len(cleaned) != 8:
                continue
            try:
                key = bytes.fromhex(cleaned)
            except ValueError:
                continue
            if key in seen:
                continue
            seen.add(key)
            parsed.append(key)
        return parsed

    @staticmethod
    def _parse_pid_list(raw: str) -> List[int]:
        tokens = [token.strip() for token in str(raw or "").split(",")]
        values: List[int] = []
        seen: Set[int] = set()
        for token in tokens:
            if not token:
                continue
            try:
                value = int(token, 16) if token.lower().startswith("0x") else int(token, 10)
            except ValueError:
                continue
            if value < 0 or value > 0xFFFF:
                continue
            if value in seen:
                continue
            seen.add(value)
            values.append(value)
        return values

    def _load_product_ids(self) -> Set[int]:
        from_env = self._parse_pid_list(str(os.getenv("RAZECLI_BLE_PRODUCT_IDS", "")).strip())
        if from_env:
            return set(from_env)
        return set(DEFAULT_BLE_PRODUCT_IDS)

    def _detect_capabilities(self) -> Set[str]:
        caps = set(BLE_CAPABILITIES)
        if self._poll_capability_enabled:
            caps.add("poll-rate")
        return caps

    @staticmethod
    def _is_mac_address(value: Optional[str]) -> bool:
        text = str(value or "").strip()
        parts = text.split(":")
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

    @staticmethod
    def _normalize_identifier(
        *,
        vendor_id: int,
        product_id: int,
        serial: Optional[str],
        bt_address: Optional[str],
    ) -> str:
        if bt_address:
            compact_addr = "".join(ch for ch in bt_address if ch.isalnum())
            return f"macos-ble:{vendor_id:04X}:{product_id:04X}:bt:{compact_addr}"

        serial = "".join(ch for ch in (serial or "") if ch.isalnum())
        if serial:
            return f"macos-ble:{vendor_id:04X}:{product_id:04X}:sn:{serial}"
        return f"macos-ble:{vendor_id:04X}:{product_id:04X}"

    def _is_ble_candidate(self, device: DetectedDevice) -> bool:
        if device.vendor_id != RAZER_VENDOR_ID:
            return False
        return device.product_id in self._ble_product_ids

    @staticmethod
    def _copy_handle(device: DetectedDevice) -> Dict[str, object]:
        if isinstance(device.backend_handle, dict):
            return dict(device.backend_handle)
        return {}

    @staticmethod
    def _name_match_score(left: str, right: str) -> int:
        a = (left or "").strip().lower()
        b = (right or "").strip().lower()
        if not a or not b:
            return 0
        if a == b:
            return 100
        score = 0
        for token in ("deathadder", "da v2", "v2 pro", "razer"):
            if token in a and token in b:
                score += 30
        if a in b or b in a:
            score += 15
        return score

    def _profiler_bt_rows(self) -> List[DetectedDevice]:
        rows: List[DetectedDevice] = []
        for device in self._profiler.detect():
            if device.backend != "macos-profiler":
                continue
            if not device.identifier.startswith("macos-bt:"):
                continue
            if device.vendor_id != RAZER_VENDOR_ID:
                continue
            if device.product_id not in self._ble_product_ids:
                continue
            rows.append(device)
        return rows

    def _poll_read_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_POLL_READ_KEYS", "")).strip(),
            default_tokens=DEFAULT_POLL_READ_KEYS,
        )
        pinned_hex = str(handle.get("ble_poll_read_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    def _poll_write_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_POLL_WRITE_KEYS", "")).strip(),
            default_tokens=DEFAULT_POLL_WRITE_KEYS,
        )
        pinned_hex = str(handle.get("ble_poll_write_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    def _dpi_read_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_DPI_READ_KEYS", "")).strip(),
            default_tokens=DEFAULT_DPI_READ_KEYS,
        )
        pinned_hex = str(handle.get("ble_dpi_read_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    def _dpi_write_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_DPI_WRITE_KEYS", "")).strip(),
            default_tokens=DEFAULT_DPI_WRITE_KEYS,
        )
        pinned_hex = str(handle.get("ble_dpi_write_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    @staticmethod
    def _decode_poll_rate_payload(payload: bytes) -> Optional[int]:
        if not payload:
            return None

        candidates: List[int] = []
        if payload:
            candidates.append(int(payload[0]))
            candidates.append(int(payload[-1]))
        candidates.extend(int(value) for value in payload[:4])

        for code in candidates:
            rate = CODE_TO_POLL_RATE.get(code)
            if rate is not None:
                return int(rate)

        if len(payload) >= 2:
            little = int.from_bytes(payload[:2], byteorder="little", signed=False)
            big = int.from_bytes(payload[:2], byteorder="big", signed=False)
            if little in POLL_RATE_TO_CODE:
                return int(little)
            if big in POLL_RATE_TO_CODE:
                return int(big)

        if len(payload) >= 4:
            little_32 = int.from_bytes(payload[:4], byteorder="little", signed=False)
            big_32 = int.from_bytes(payload[:4], byteorder="big", signed=False)
            if little_32 in POLL_RATE_TO_CODE:
                return int(little_32)
            if big_32 in POLL_RATE_TO_CODE:
                return int(big_32)
        return None

    @staticmethod
    def _poll_write_payload_candidates(hz: int) -> List[bytes]:
        code = POLL_RATE_TO_CODE[int(hz)]
        values = [
            bytes([code]),
            bytes([code, 0x00]),
            int(hz).to_bytes(2, byteorder="little", signed=False),
            int(hz).to_bytes(2, byteorder="big", signed=False),
            bytes([code, 0x00, 0x00, 0x00]),
            int(hz).to_bytes(4, byteorder="little", signed=False),
        ]
        unique: List[bytes] = []
        seen: Set[bytes] = set()
        for payload in values:
            if payload in seen:
                continue
            seen.add(payload)
            unique.append(payload)
        return unique

    def _match_profiler_address(self, device: DetectedDevice, profiler_rows: Sequence[DetectedDevice]) -> Optional[str]:
        best_score = -1
        best_address: Optional[str] = None
        for row in profiler_rows:
            address = row.serial if self._is_mac_address(row.serial) else None
            if not address:
                continue
            score = 0
            if row.product_id == device.product_id:
                score += 50
            score += self._name_match_score(device.name, row.name)
            if score > best_score:
                best_score = score
                best_address = address
        return best_address

    @staticmethod
    def _backend_timeout() -> float:
        raw = str(os.getenv("RAZECLI_BLE_BACKEND_TIMEOUT", "12")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 12.0
        return max(3.0, min(45.0, value))

    @staticmethod
    def _response_timeout() -> float:
        raw = str(os.getenv("RAZECLI_BLE_BACKEND_RESPONSE_TIMEOUT", "1.5")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 1.0
        return max(0.2, min(4.0, value))

    @staticmethod
    def _dpi_read_attempts() -> int:
        raw = str(os.getenv("RAZECLI_BLE_DPI_READ_ATTEMPTS", "3")).strip()
        try:
            value = int(raw)
        except ValueError:
            value = 3
        return max(1, min(8, value))

    @staticmethod
    def _dpi_read_retry_delay() -> float:
        raw = str(os.getenv("RAZECLI_BLE_DPI_READ_RETRY_DELAY", "0.18")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 0.18
        return max(0.0, min(1.5, value))

    def _detect_from_rawhid(self, profiler_rows: Sequence[DetectedDevice]) -> List[DetectedDevice]:
        devices: List[DetectedDevice] = []
        for device in self._rawhid.detect():
            if not self._is_ble_candidate(device):
                continue

            handle = self._copy_handle(device)
            handle["backend"] = self.name
            bt_address = self._match_profiler_address(device, profiler_rows)
            if bt_address:
                handle["bt_address"] = bt_address

            devices.append(
                DetectedDevice(
                    identifier=self._normalize_identifier(
                        vendor_id=device.vendor_id,
                        product_id=device.product_id,
                        serial=device.serial,
                        bt_address=bt_address,
                    ),
                    name=device.name,
                    vendor_id=device.vendor_id,
                    product_id=device.product_id,
                    backend=self.name,
                    serial=bt_address or device.serial,
                    model_id=device.model_id,
                    model_name=device.model_name,
                    capabilities=self._detect_capabilities(),
                    backend_handle=handle,
                )
            )
        return devices

    def _detect_from_profiler_only(self, profiler_rows: Sequence[DetectedDevice]) -> List[DetectedDevice]:
        devices: List[DetectedDevice] = []
        for row in profiler_rows:
            bt_address = row.serial if self._is_mac_address(row.serial) else None
            handle = self._copy_handle(row)
            handle["backend"] = self.name
            if bt_address:
                handle["bt_address"] = bt_address

            devices.append(
                DetectedDevice(
                    identifier=self._normalize_identifier(
                        vendor_id=row.vendor_id,
                        product_id=row.product_id,
                        serial=row.serial,
                        bt_address=bt_address,
                    ),
                    name=row.name or "Razer Bluetooth device",
                    vendor_id=row.vendor_id,
                    product_id=row.product_id,
                    backend=self.name,
                    serial=bt_address or row.serial,
                    model_id=row.model_id,
                    model_name=row.model_name,
                    capabilities=self._detect_capabilities(),
                    backend_handle=handle,
                )
            )
        return devices

    def detect(self) -> List[DetectedDevice]:
        if not self._supported:
            return []

        profiler_rows = self._profiler_bt_rows()
        devices = self._detect_from_rawhid(profiler_rows)
        if devices:
            return devices
        return self._detect_from_profiler_only(profiler_rows)

    def _device_handle(self, device: DetectedDevice) -> Dict[str, object]:
        if isinstance(device.backend_handle, dict):
            return device.backend_handle
        handle: Dict[str, object] = {}
        device.backend_handle = handle
        return handle

    def _resolve_target(self, device: DetectedDevice) -> Tuple[Optional[str], str]:
        handle = self._device_handle(device)
        bt_address = str(handle.get("bt_address") or "").strip()
        if self._is_mac_address(bt_address):
            return bt_address, device.name or DEFAULT_NAME_QUERY
        if self._is_mac_address(device.serial):
            return str(device.serial), device.name or DEFAULT_NAME_QUERY
        return None, device.name or device.model_name or DEFAULT_NAME_QUERY

    @staticmethod
    def _vendor_path_from_handle(handle: Dict[str, object]) -> Optional[Dict[str, object]]:
        service_uuid = str(handle.get("ble_service_uuid") or "").strip().lower()
        write_char_uuid = str(handle.get("ble_write_char_uuid") or "").strip().lower()
        read_chars_raw = handle.get("ble_read_char_uuids")
        if not service_uuid or not write_char_uuid:
            return None
        if not isinstance(read_chars_raw, list) or not read_chars_raw:
            return None
        read_char_uuids = [str(item).strip().lower() for item in read_chars_raw if str(item).strip()]
        if not read_char_uuids:
            return None
        return {
            "service_uuid": service_uuid,
            "write_char_uuid": write_char_uuid,
            "read_char_uuids": read_char_uuids,
        }

    @staticmethod
    def _store_vendor_path(handle: Dict[str, object], path: Dict[str, object]) -> None:
        handle["ble_service_uuid"] = str(path.get("service_uuid") or "").strip().lower()
        handle["ble_write_char_uuid"] = str(path.get("write_char_uuid") or "").strip().lower()
        read_chars = path.get("read_char_uuids")
        if isinstance(read_chars, list):
            handle["ble_read_char_uuids"] = [str(item).strip().lower() for item in read_chars if str(item).strip()]

    @staticmethod
    def _clear_vendor_path(handle: Dict[str, object]) -> None:
        handle.pop("ble_service_uuid", None)
        handle.pop("ble_write_char_uuid", None)
        handle.pop("ble_read_char_uuids", None)

    @staticmethod
    def _missing_vendor_path_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "gatt service" in text and "not found" in text
        ) or (
            "gatt write-char" in text and "not found" in text
        ) or (
            "was not found" in text and "gatt" in text
        ) or (
            "hittades inte" in text and "gatt" in text
        )

    def _vendor_call(
        self,
        *,
        device: DetectedDevice,
        key: bytes,
        value_payload: Optional[bytes] = None,
    ) -> Dict[str, object]:
        from razecli.ble_probe import (
            DEFAULT_RAZER_BT_READ_CHAR_UUIDS,
            DEFAULT_RAZER_BT_SERVICE_UUID,
            DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
            ble_vendor_transceive,
            discover_vendor_gatt_path,
        )

        address, name_query = self._resolve_target(device)
        timeout = self._backend_timeout()
        response_timeout = self._response_timeout()
        handle = self._device_handle(device)
        pinned_path = self._vendor_path_from_handle(handle)

        def _tx(path: Optional[Dict[str, object]]) -> Dict[str, object]:
            kwargs: Dict[str, object] = {}
            if path:
                kwargs = {
                    "service_uuid": str(path.get("service_uuid") or DEFAULT_RAZER_BT_SERVICE_UUID),
                    "write_char_uuid": str(path.get("write_char_uuid") or DEFAULT_RAZER_BT_WRITE_CHAR_UUID),
                    "read_char_uuids": list(path.get("read_char_uuids") or DEFAULT_RAZER_BT_READ_CHAR_UUIDS),
                }
            return ble_vendor_transceive(
                address=address,
                name_query=name_query,
                timeout=timeout,
                key=key,
                value_payload=value_payload,
                response_timeout=response_timeout,
                write_with_response=True,
                notify_enabled=True,
                **kwargs,
            )

        try:
            return _tx(pinned_path)
        except Exception as first_exc:
            if pinned_path is None and not self._missing_vendor_path_error(first_exc):
                raise

            try:
                discovered = discover_vendor_gatt_path(
                    address=address,
                    name_query=name_query,
                    timeout=min(timeout, 10.0),
                )
            except Exception:
                raise first_exc

            self._store_vendor_path(handle, discovered)
            return _tx(discovered)

    @staticmethod
    def _decode_payload_bytes(result: Dict[str, object], *, key: bytes) -> bytes:
        vendor = result.get("vendor_decode")
        if not isinstance(vendor, dict):
            raise CapabilityUnsupportedError(f"No vendor response for key {key.hex()}")
        payload_hex = str(vendor.get("payload_hex") or "").strip()
        payload = b""
        if payload_hex:
            try:
                payload = bytes.fromhex(payload_hex)
            except ValueError as exc:
                raise CapabilityUnsupportedError(
                    f"Invalid payload_hex in vendor response for key {key.hex()}: {payload_hex}"
                ) from exc

        if len(payload) >= 2:
            return payload

        notify_inferred = MacOSBleBackend._infer_payload_from_notify_rows(result)
        if notify_inferred is not None:
            return notify_inferred

        # Read-row fallback is only valid for DPI stage responses.
        if key == KEY_DPI_STAGES_READ:
            inferred = MacOSBleBackend._infer_payload_from_read_rows(result)
            if inferred is not None:
                return inferred
        return payload

    @staticmethod
    def _infer_payload_from_notify_rows(result: Dict[str, object]) -> Optional[bytes]:
        notify = result.get("notify")
        if not isinstance(notify, list) or not notify:
            return None

        vendor_req = result.get("vendor_request")
        request_id: Optional[int] = None
        if isinstance(vendor_req, dict):
            raw = vendor_req.get("request_id")
            if isinstance(raw, int):
                request_id = int(raw) & 0xFF

        rows: List[bytes] = []
        for row in notify:
            if not isinstance(row, dict):
                continue
            char_uuid = str(row.get("char_uuid") or "").strip().lower()
            if char_uuid and char_uuid != PRIMARY_VENDOR_READ_CHAR_UUID:
                continue
            value_hex = str(row.get("value_hex") or "").strip()
            if not value_hex:
                continue
            try:
                data = bytes.fromhex(value_hex)
            except ValueError:
                continue
            if data:
                rows.append(data)

        if not rows:
            return None

        def _is_header(blob: bytes) -> bool:
            if len(blob) < 2:
                return False
            if request_id is not None and int(blob[0]) != request_id:
                return False
            return True

        for index, header in enumerate(rows):
            if not _is_header(header):
                continue
            payload_len = int(header[1]) if len(header) >= 2 else 0
            chunks: List[bytes] = []
            if len(header) > 8:
                chunks.append(header[8:])

            for nxt in rows[index + 1 :]:
                if _is_header(nxt):
                    break
                chunks.append(nxt)

            payload = b"".join(chunks)
            if payload_len > 0:
                if len(payload) >= payload_len:
                    return payload[:payload_len]
                continue
            if payload:
                return payload

        return None

    @staticmethod
    def _infer_payload_from_read_rows(result: Dict[str, object]) -> Optional[bytes]:
        reads = result.get("reads")
        if not isinstance(reads, list):
            return None

        candidates: List[bytes] = []
        for row in reads:
            if not isinstance(row, dict):
                continue
            char_uuid = str(row.get("char_uuid") or "").strip().lower()
            if char_uuid and char_uuid != PRIMARY_VENDOR_READ_CHAR_UUID:
                continue
            value_hex = str(row.get("value_hex") or "").strip()
            if not value_hex:
                continue
            try:
                value = bytes.fromhex(value_hex)
            except ValueError:
                continue
            if not value:
                continue
            candidates.append(value)

        def _slice_candidate(blob: bytes) -> Optional[bytes]:
            if len(blob) >= 2:
                count = int(blob[1])
                if 1 <= count <= MAX_DPI_STAGES:
                    needed = 2 + (7 * count)
                    if len(blob) >= needed:
                        return blob[:needed]

            if len(blob) >= 3:
                count = int(blob[2])
                if 1 <= count <= MAX_DPI_STAGES:
                    needed = 3 + (7 * count)
                    if len(blob) >= needed:
                        return blob[:needed]
            return None

        for blob in candidates:
            exact = _slice_candidate(blob)
            if exact is not None:
                return exact
            if len(blob) > 1:
                trimmed = _slice_candidate(blob[1:])
                if trimmed is not None:
                    return trimmed

        return None

    @staticmethod
    def _parse_stages_payload(payload: bytes) -> Tuple[int, List[Tuple[int, int]], List[int], int]:
        if len(payload) < 2:
            raise CapabilityUnsupportedError("Invalid DPI stage payload (too short)")

        # Some hosts/devices occasionally return a compact single-stage payload over BLE.
        # Accept it as a fallback to avoid hard failures when full stage table is missing.
        def _parse_compact_single_stage(blob: bytes) -> Optional[Tuple[int, List[Tuple[int, int]], List[int], int]]:
            def _validate_single_stage(
                *,
                active_raw: int,
                dpi_x: int,
                dpi_y: int,
                marker: int,
            ) -> Optional[Tuple[int, List[Tuple[int, int]], List[int], int]]:
                if (
                    dpi_x < MIN_PLAUSIBLE_DPI
                    or dpi_y < MIN_PLAUSIBLE_DPI
                    or dpi_x > MAX_PLAUSIBLE_DPI
                    or dpi_y > MAX_PLAUSIBLE_DPI
                ):
                    return None
                if dpi_x != dpi_y:
                    return None
                if marker > 0x20:
                    return None
                stage_id = active_raw if active_raw not in {0x00, 0xFF} else 0x01
                return 1, [(dpi_x, dpi_y)], [stage_id], marker

            if len(blob) == 8:
                # Layout A: [active, count, dpi_x_l, dpi_x_h, dpi_y_l, dpi_y_h, pad, marker]
                if int(blob[1]) == 1:
                    parsed = _validate_single_stage(
                        active_raw=int(blob[0]),
                        dpi_x=(int(blob[2]) | (int(blob[3]) << 8)),
                        dpi_y=(int(blob[4]) | (int(blob[5]) << 8)),
                        marker=int(blob[7]),
                    )
                    if parsed is not None:
                        return parsed

                # Layout B: [varstore, active, count, dpi_x_l, dpi_x_h, dpi_y_l, dpi_y_h, marker]
                if int(blob[2]) == 1:
                    parsed = _validate_single_stage(
                        active_raw=int(blob[1]),
                        dpi_x=(int(blob[3]) | (int(blob[4]) << 8)),
                        dpi_y=(int(blob[5]) | (int(blob[6]) << 8)),
                        marker=int(blob[7]),
                    )
                    if parsed is not None:
                        return parsed

            if len(blob) == 9:
                # [varstore, active, count, dpi_x_l, dpi_x_h, dpi_y_l, dpi_y_h, pad, marker]
                count = int(blob[2])
                if count != 1:
                    return None
                dpi_x = int(blob[3]) | (int(blob[4]) << 8)
                dpi_y = int(blob[5]) | (int(blob[6]) << 8)
                marker = int(blob[8])
                return _validate_single_stage(
                    active_raw=int(blob[1]),
                    dpi_x=dpi_x,
                    dpi_y=dpi_y,
                    marker=marker,
                )
            return None

        compact = _parse_compact_single_stage(payload)
        if compact is not None:
            return compact

        def _try_layout(
            *,
            active_index: int,
            count_index: int,
            stage_offset: int,
        ) -> Optional[Tuple[int, List[Tuple[int, int]], List[int], int]]:
            if active_index >= len(payload) or count_index >= len(payload):
                return None

            active_raw = int(payload[active_index])
            declared = int(payload[count_index])
            max_by_len = max(0, (len(payload) - stage_offset) // 7)
            if max_by_len <= 0:
                return None

            if declared <= 0 or declared > MAX_DPI_STAGES:
                declared_count = min(MAX_DPI_STAGES, max_by_len)
            else:
                declared_count = min(declared, MAX_DPI_STAGES, max_by_len)
            if declared_count <= 0:
                return None

            stages: List[Tuple[int, int]] = []
            stage_ids: List[int] = []
            marker = 0
            offset = stage_offset
            for _ in range(declared_count):
                if offset + 6 >= len(payload):
                    break
                stage_id = int(payload[offset])
                dpi_x = int(payload[offset + 1]) | (int(payload[offset + 2]) << 8)
                dpi_y = int(payload[offset + 3]) | (int(payload[offset + 4]) << 8)
                marker = int(payload[offset + 6])

                # Reject clearly invalid decodes.
                if (
                    dpi_x < MIN_PLAUSIBLE_DPI
                    or dpi_y < MIN_PLAUSIBLE_DPI
                    or dpi_x > MAX_PLAUSIBLE_DPI
                    or dpi_y > MAX_PLAUSIBLE_DPI
                ):
                    return None
                stage_ids.append(stage_id)
                stages.append((dpi_x, dpi_y))
                offset += 7

            if not stages:
                return None

            active_stage = 1
            for idx, stage_id in enumerate(stage_ids, start=1):
                if stage_id == active_raw:
                    active_stage = idx
                    break
            else:
                if 1 <= active_raw <= len(stages):
                    active_stage = int(active_raw)

            return active_stage, stages, stage_ids, marker

        for layout in (
            {"active_index": 0, "count_index": 1, "stage_offset": 2},
            {"active_index": 1, "count_index": 2, "stage_offset": 3},
        ):
            parsed = _try_layout(**layout)
            if parsed is not None:
                return parsed

        raise CapabilityUnsupportedError("Device returned no DPI profiles over BLE")

    @staticmethod
    def _build_stages_write_payload(
        *,
        active_stage: int,
        stages: Sequence[Tuple[int, int]],
        stage_ids_hint: Sequence[int],
        marker: int,
    ) -> bytes:
        if not stages:
            raise CapabilityUnsupportedError("At least one DPI profile is required")
        if len(stages) > MAX_DPI_STAGES:
            raise CapabilityUnsupportedError(f"At most {MAX_DPI_STAGES} DPI profiles are supported")
        if active_stage < 1 or active_stage > len(stages):
            raise CapabilityUnsupportedError(
                f"Active DPI profile must be between 1 and {len(stages)}"
            )

        stage_ids: List[int] = []
        used: set[int] = set()
        for idx in range(len(stages)):
            value = int(stage_ids_hint[idx]) if idx < len(stage_ids_hint) else (idx + 1)
            value &= 0xFF
            while value in used:
                value = (value + 1) & 0xFF
                if value == 0:
                    value = 1
            used.add(value)
            stage_ids.append(value)

        active_token = stage_ids[active_stage - 1]
        payload = bytearray([active_token & 0xFF, len(stages) & 0xFF])
        for idx in range(len(stages)):
            dpi_x, dpi_y = stages[idx]
            payload.append(stage_ids[idx] & 0xFF)
            payload.append(int(dpi_x) & 0xFF)
            payload.append((int(dpi_x) >> 8) & 0xFF)
            payload.append(int(dpi_y) & 0xFF)
            payload.append((int(dpi_y) >> 8) & 0xFF)
            payload.append(0x00)
            payload.append((marker & 0xFF) if idx == len(stages) - 1 else 0x00)
        return bytes(payload)

    def _read_dpi_stages_with_metadata(
        self,
        device: DetectedDevice,
    ) -> Tuple[int, List[Tuple[int, int]], List[int], int]:
        handle = self._device_handle(device)
        attempts = self._dpi_read_attempts()
        retry_delay = self._dpi_read_retry_delay()
        last_exc: Optional[Exception] = None
        read_keys = self._dpi_read_keys(handle)
        diagnostics: List[str] = []

        for attempt in range(attempts):
            for key in read_keys:
                try:
                    result = self._vendor_call(device=device, key=key)
                    payload = self._decode_payload_bytes(result, key=key)
                    preview = payload[:16].hex() if payload else "-"
                    diagnostics.append(f"{key.hex()}:len={len(payload)}:hex={preview}")
                    active_stage, stages, stage_ids, marker = self._parse_stages_payload(payload)
                    handle["ble_dpi_read_key"] = key.hex()
                    handle["ble_stage_ids"] = list(stage_ids)
                    handle["ble_stage_marker"] = int(marker)
                    handle["ble_cached_stages"] = [[int(x), int(y)] for (x, y) in stages]
                    handle["ble_cached_active_stage"] = int(active_stage)
                    return active_stage, stages, stage_ids, marker
                except Exception as exc:
                    diagnostics.append(f"{key.hex()}:err={exc}")
                    last_exc = exc
                    continue

            # On repeated parse failures, force GATT path rediscovery in case pinned path is stale.
            self._clear_vendor_path(handle)
            if attempt + 1 < attempts and retry_delay > 0:
                time.sleep(retry_delay * float(attempt + 1))

        if isinstance(last_exc, Exception):
            # fall through to cache fallback below
            pass

        cached_stages_raw = handle.get("ble_cached_stages")
        cached_active_raw = handle.get("ble_cached_active_stage")
        cached_stage_ids_raw = handle.get("ble_stage_ids")
        cached_marker_raw = handle.get("ble_stage_marker")
        if isinstance(cached_stages_raw, list) and cached_stages_raw:
            stages: List[Tuple[int, int]] = []
            for row in cached_stages_raw:
                if not isinstance(row, (list, tuple)) or len(row) != 2:
                    continue
                stages.append((int(row[0]), int(row[1])))
            if stages:
                active_stage = int(cached_active_raw or 1)
                if active_stage < 1 or active_stage > len(stages):
                    active_stage = 1
                stage_ids: List[int] = []
                if isinstance(cached_stage_ids_raw, list):
                    stage_ids = [int(value) for value in cached_stage_ids_raw][: len(stages)]
                if not stage_ids:
                    stage_ids = [idx + 1 for idx in range(len(stages))]
                marker = int(cached_marker_raw or 0)
                return active_stage, stages, stage_ids, marker

        if isinstance(last_exc, Exception):
            preview = ", ".join(diagnostics[-8:])
            raise CapabilityUnsupportedError(
                "Could not decode DPI profiles over BLE. "
                f"Recent attempts: {preview}"
            ) from last_exc
        raise CapabilityUnsupportedError("Could not read DPI profiles over BLE")

    def get_dpi(self, device: DetectedDevice) -> Tuple[int, int]:
        active_stage, stages, _stage_ids, _marker = self._read_dpi_stages_with_metadata(device)
        if active_stage < 1 or active_stage > len(stages):
            raise CapabilityUnsupportedError(f"Invalid active DPI profile over BLE: {active_stage}")
        return stages[active_stage - 1]

    def set_dpi(self, device: DetectedDevice, dpi_x: int, dpi_y: int) -> None:
        active_stage, stages, _stage_ids, _marker = self._read_dpi_stages_with_metadata(device)
        stages[active_stage - 1] = (int(dpi_x), int(dpi_y))
        self.set_dpi_stages(device, active_stage, stages)

    def get_dpi_stages(self, device: DetectedDevice) -> Tuple[int, Sequence[Tuple[int, int]]]:
        active_stage, stages, _stage_ids, _marker = self._read_dpi_stages_with_metadata(device)
        return active_stage, stages

    def set_dpi_stages(self, device: DetectedDevice, active_stage: int, stages: Sequence[Tuple[int, int]]) -> None:
        stages_list = list(stages)
        handle = self._device_handle(device)
        if int(device.product_id) == 0x008E and len(stages_list) > 1:
            current_count = 0
            cached = handle.get("ble_cached_stages")
            if isinstance(cached, list):
                current_count = len(cached)
            if current_count <= 0:
                try:
                    _active_cur, current_stages, _stage_ids_cur, _marker_cur = self._read_dpi_stages_with_metadata(device)
                    current_count = len(list(current_stages))
                except Exception:
                    current_count = 0
            if current_count <= 1:
                raise CapabilityUnsupportedError(
                    "Device currently reports a single BLE DPI profile. "
                    "Adding extra profiles is not mapped reliably on 1532:008E over BLE yet. "
                    "Use USB/2.4 mode to create multi-profile tables, then return to BLE."
                )

        stage_ids_hint = handle.get("ble_stage_ids")
        marker = int(handle.get("ble_stage_marker") or 0)
        if not isinstance(stage_ids_hint, list) or not stage_ids_hint:
            try:
                _active, _stages, stage_ids_hint, marker = self._read_dpi_stages_with_metadata(device)
            except Exception:
                stage_ids_hint = [idx + 1 for idx in range(len(stages))]
                marker = 0

        payload = self._build_stages_write_payload(
            active_stage=int(active_stage),
            stages=stages_list,
            stage_ids_hint=[int(value) for value in stage_ids_hint],
            marker=marker,
        )
        write_keys = self._dpi_write_keys(handle)
        attempted: List[str] = []
        last_exc: Optional[Exception] = None
        for key in write_keys:
            attempted.append(key.hex())
            try:
                _ = self._vendor_call(
                    device=device,
                    key=key,
                    value_payload=payload,
                )
                handle["ble_dpi_write_key"] = key.hex()
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                continue
        if isinstance(last_exc, Exception):
            raise CapabilityUnsupportedError(
                f"DPI profile write over BLE failed. Tried keys: {attempted}"
            ) from last_exc
        handle["ble_stage_ids"] = [int(value) for value in stage_ids_hint][: len(stages_list)]
        handle["ble_stage_marker"] = int(marker)
        handle["ble_cached_stages"] = [[int(x), int(y)] for (x, y) in stages_list]
        handle["ble_cached_active_stage"] = int(active_stage)

    def get_poll_rate(self, device: DetectedDevice) -> int:
        handle = self._device_handle(device)
        read_keys = self._poll_read_keys(handle)
        attempted: List[str] = []
        for key in read_keys:
            attempted.append(key.hex())
            try:
                result = self._vendor_call(device=device, key=key)
                payload = self._decode_payload_bytes(result, key=key)
            except Exception:
                continue
            decoded = self._decode_poll_rate_payload(payload)
            if decoded is not None:
                handle["ble_poll_read_key"] = key.hex()
                return int(decoded)
        raise CapabilityUnsupportedError(
            "Poll-rate over Bluetooth is not mapped for this device/host. "
            f"Tried read keys: {attempted}"
        )

    def set_poll_rate(self, device: DetectedDevice, hz: int) -> None:
        hz = int(hz)
        if hz not in POLL_RATE_TO_CODE:
            raise CapabilityUnsupportedError("Poll-rate must be one of: 125, 500, 1000")

        handle = self._device_handle(device)
        write_keys = self._poll_write_keys(handle)
        payload_candidates = self._poll_write_payload_candidates(hz)
        attempted: List[str] = []

        for key in write_keys:
            for payload in payload_candidates:
                attempted.append(f"{key.hex()}:{payload.hex()}")
                try:
                    _ = self._vendor_call(device=device, key=key, value_payload=payload)
                except Exception:
                    continue
                try:
                    current = self.get_poll_rate(device)
                except Exception:
                    continue
                if int(current) == hz:
                    handle["ble_poll_write_key"] = key.hex()
                    return

        raise CapabilityUnsupportedError(
            "Poll-rate write over Bluetooth failed or could not be verified. "
            f"Tried: {attempted}"
        )

    def get_supported_poll_rates(self, device: DetectedDevice) -> Sequence[int]:
        _ = device
        return [125, 500, 1000]

    async def _read_standard_battery_level_async(
        self,
        *,
        address: Optional[str],
        name_query: str,
        timeout: float,
    ) -> Optional[int]:
        try:
            from bleak import BleakClient  # type: ignore
            from razecli.ble_probe import _auto_resolve_corebluetooth_address, _resolve_device_async
        except Exception:
            return None

        connect_target: Any = None
        if address and self._is_mac_address(address):
            try:
                auto = await _auto_resolve_corebluetooth_address(
                    mac_address=address,
                    timeout=timeout,
                )
            except Exception:
                auto = {}
            resolved_device = auto.get("resolved_device")
            resolved_address = str(auto.get("resolved_address") or "").strip()
            if resolved_device is not None:
                connect_target = resolved_device
            elif resolved_address:
                connect_target = resolved_address
            else:
                connect_target = address
        else:
            try:
                connect_target = await _resolve_device_async(
                    address=address,
                    name_query=name_query,
                    timeout=timeout,
                )
            except Exception:
                return None

        if connect_target is None:
            return None

        try:
            async with BleakClient(connect_target, timeout=timeout) as client:
                value = await client.read_gatt_char(BATTERY_LEVEL_CHAR_UUID)
        except Exception:
            return None

        data = bytes(value or b"")
        if not data:
            return None
        level = int(data[0])
        if 0 <= level <= 100:
            return int(level)
        return None

    def _read_standard_battery_level(self, device: DetectedDevice) -> Optional[int]:
        address, name_query = self._resolve_target(device)
        timeout = self._backend_timeout()
        try:
            return asyncio.run(
                self._read_standard_battery_level_async(
                    address=address,
                    name_query=name_query,
                    timeout=timeout,
                )
            )
        except Exception:
            return None

    def get_battery(self, device: DetectedDevice) -> int:
        standard_level = self._read_standard_battery_level(device)
        if standard_level is not None:
            return int(standard_level)

        attempts: List[Tuple[bytes, str]] = [
            (KEY_BATTERY_RAW_READ, "raw"),
            (KEY_BATTERY_STATUS_READ, "status"),
        ]
        last_err: Optional[Exception] = None

        for key, mode in attempts:
            try:
                result = self._vendor_call(device=device, key=key)
                payload = self._decode_payload_bytes(result, key=key)
                if not payload:
                    continue
                value = int(payload[0])
                if mode == "status":
                    if 0 <= value <= 100:
                        return int(value)
                    continue
                # Raw battery endpoint reports 0-255 scale.
                return int(round((value / 255.0) * 100.0))
            except Exception as exc:
                last_err = exc
                continue

        if isinstance(last_err, Exception):
            raise CapabilityUnsupportedError("Battery response over BLE does not include payload") from last_err
        raise CapabilityUnsupportedError("Battery response over BLE does not include payload")


__all__ = ["MacOSBleBackend"]
