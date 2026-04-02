"""macOS system_profiler backend for detection without extra deps."""

import json
import platform
import re
import subprocess
from typing import Any, Dict, Iterable, List, Optional

from razecli.backends.base import Backend
from razecli.types import DetectedDevice

HEX_RE = re.compile(r"0x([0-9A-Fa-f]{1,8})")


class MacOSProfilerBackend(Backend):
    name = "macos-profiler"

    def __init__(self) -> None:
        self.last_error = None
        self._supported = platform.system() == "Darwin"

    def _run_profiler(self, data_type: str) -> Dict[str, Any]:
        if not self._supported:
            return {}

        try:
            result = subprocess.run(
                ["system_profiler", data_type, "-json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:  # pragma: no cover - host dependent
            self.last_error = exc
            return {}

        try:
            return json.loads(result.stdout or "{}")
        except Exception as exc:  # pragma: no cover - host dependent
            self.last_error = exc
            return {}

    def _run_profiler_text(self, data_type: str) -> str:
        if not self._supported:
            return ""

        try:
            result = subprocess.run(
                ["system_profiler", data_type],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout or ""
        except Exception as exc:  # pragma: no cover - host dependent
            self.last_error = exc
            return ""

    def _walk(self, node: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from self._walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from self._walk(item)

    @staticmethod
    def _hex_to_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, int):
            return value

        text = str(value)
        match = HEX_RE.search(text)
        if match:
            return int(match.group(1), 16)

        if text.isdigit():
            return int(text)

        return None

    @staticmethod
    def _first(node: Dict[str, Any], *keys: str) -> Optional[str]:
        for key in keys:
            value = node.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        return None

    def _detect_usb(self) -> List[DetectedDevice]:
        data = self._run_profiler("SPUSBDataType")
        devices: List[DetectedDevice] = []
        seen = set()

        for node in self._walk(data.get("SPUSBDataType", [])):
            name = self._first(node, "_name", "name", "device_name")
            vendor_id = self._hex_to_int(node.get("vendor_id"))
            product_id = self._hex_to_int(node.get("product_id"))

            is_razer_name = bool(name and "razer" in name.lower())
            is_razer_vendor = vendor_id == 0x1532
            if not (is_razer_name or is_razer_vendor):
                continue

            if vendor_id is None:
                vendor_id = 0x1532 if is_razer_name else 0
            if product_id is None:
                product_id = 0

            serial = self._first(node, "serial_num", "serial_number", "serial")
            location_id = self._first(node, "location_id")
            identifier = f"macos-usb:{location_id or serial or f'{vendor_id:04X}:{product_id:04X}'}"

            if identifier in seen:
                continue
            seen.add(identifier)

            devices.append(
                DetectedDevice(
                    identifier=identifier,
                    name=name or "Razer device",
                    vendor_id=vendor_id,
                    product_id=product_id,
                    serial=serial,
                    backend=self.name,
                    capabilities=set(),
                    backend_handle=node,
                )
            )

        return devices

    def _detect_bluetooth(self) -> List[DetectedDevice]:
        data = self._run_profiler("SPBluetoothDataType")
        devices: List[DetectedDevice] = []
        seen = set()

        for node in self._walk(data.get("SPBluetoothDataType", [])):
            name = self._first(node, "device_title", "_name", "name")
            vendor_id = self._hex_to_int(node.get("vendor_id"))

            is_razer_name = bool(name and "razer" in name.lower())
            is_razer_vendor = vendor_id == 0x1532
            if not name or not (is_razer_name or is_razer_vendor):
                continue

            vendor_id = vendor_id or 0x1532
            product_id = self._hex_to_int(node.get("product_id")) or 0
            address = self._first(node, "device_address", "address", "bd_addr")
            serial = self._first(node, "serial_num", "serial_number", "serial")

            identifier = f"macos-bt:{address or serial or name.lower().replace(' ', '-')}"
            if identifier in seen:
                continue
            seen.add(identifier)

            devices.append(
                DetectedDevice(
                    identifier=identifier,
                    name=name,
                    vendor_id=vendor_id,
                    product_id=product_id,
                    serial=serial or address,
                    backend=self.name,
                    capabilities=set(),
                    backend_handle=node,
                )
            )

        if devices:
            return devices

        # Fallback for environments where SPBluetoothDataType JSON is sparse.
        raw = self._run_profiler_text("SPBluetoothDataType")
        if not raw:
            return devices

        return self._parse_bluetooth_text(raw)

    def _parse_bluetooth_text(self, raw: str) -> List[DetectedDevice]:
        devices: List[DetectedDevice] = []
        seen = set()
        in_connected = False
        current_name: Optional[str] = None
        current_fields: Dict[str, str] = {}

        def emit_current() -> None:
            nonlocal current_name, current_fields
            if not current_name:
                return

            vendor_id = self._hex_to_int(current_fields.get("vendor id"))
            product_id = self._hex_to_int(current_fields.get("product id")) or 0
            address = current_fields.get("address")

            is_razer_name = "razer" in current_name.lower()
            is_razer_vendor = vendor_id == 0x1532
            if not (is_razer_name or is_razer_vendor):
                current_name = None
                current_fields = {}
                return

            vendor_id = vendor_id or 0x1532
            identifier = f"macos-bt:{address or current_name.lower().replace(' ', '-')}"
            if identifier in seen:
                current_name = None
                current_fields = {}
                return

            seen.add(identifier)
            devices.append(
                DetectedDevice(
                    identifier=identifier,
                    name=current_name,
                    vendor_id=vendor_id,
                    product_id=product_id,
                    serial=address,
                    backend=self.name,
                    capabilities=set(),
                    backend_handle={
                        "source": "system_profiler-text",
                        "name": current_name,
                        "fields": dict(current_fields),
                    },
                )
            )
            current_name = None
            current_fields = {}

        for raw_line in raw.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue

            if stripped == "Connected:":
                in_connected = True
                emit_current()
                continue

            if in_connected and not line.startswith(" "):
                emit_current()
                in_connected = False

            if not in_connected:
                continue

            if stripped.endswith(":"):
                key_candidate = stripped[:-1]
                if key_candidate.lower() not in {
                    "address",
                    "vendor id",
                    "product id",
                    "minor type",
                    "services",
                    "state",
                    "battery level",
                }:
                    emit_current()
                    current_name = key_candidate
                    current_fields = {}
                    continue

            if ":" in stripped and current_name:
                key, value = stripped.split(":", 1)
                current_fields[key.strip().lower()] = value.strip()

        emit_current()
        return devices

    def detect(self) -> List[DetectedDevice]:
        if not self._supported:
            return []

        self.last_error = None

        usb_devices = self._detect_usb()
        bt_devices = self._detect_bluetooth()
        return usb_devices + bt_devices
