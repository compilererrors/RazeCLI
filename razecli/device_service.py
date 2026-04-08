"""Device discovery and selection logic."""

import sys
from typing import Dict, List, Optional, Sequence, Tuple

from razecli.backends import (
    Backend,
    HidapiBackend,
    MacOSBleBackend,
    MacOSProfilerBackend,
    RawHidBackend,
)
from razecli.errors import DeviceSelectionError
from razecli.model_registry import ModelRegistry
from razecli.types import DetectedDevice


class DeviceService:
    _CLASS_REGISTRY = ModelRegistry.load()

    def __init__(self, backend_mode: str = "auto") -> None:
        self.registry = ModelRegistry.load()
        self.backends = self._build_backends(backend_mode)
        self.backends_by_name = {backend.name: backend for backend in self.backends}

    @staticmethod
    def _build_backends(mode: str) -> List[Backend]:
        if mode == "rawhid":
            return [RawHidBackend()]
        if mode == "macos-ble":
            return [MacOSBleBackend()]
        if mode == "hidapi":
            return [HidapiBackend()]
        if mode == "macos-profiler":
            return [MacOSProfilerBackend()]

        backends: List[Backend] = [RawHidBackend(), HidapiBackend()]
        if sys.platform == "darwin":
            backends.extend([MacOSBleBackend(), MacOSProfilerBackend()])
        return backends

    @staticmethod
    def _backend_priority(backend_name: str) -> int:
        priorities = {
            "rawhid": 100,
            "macos-ble": 90,
            "hidapi": 30,
            "macos-profiler": 10,
        }
        return priorities.get(backend_name, 0)

    @staticmethod
    def _normalized_serial(serial: Optional[str]) -> Optional[str]:
        if serial is None:
            return None
        text = serial.strip()
        if not text:
            return None
        compact = "".join(ch for ch in text if ch.isalnum())
        if compact and set(compact) == {"0"}:
            return None
        return text

    @classmethod
    def _rawhid_merge_key(cls, device: DetectedDevice) -> Optional[Tuple[str, str, str]]:
        if device.backend != "rawhid":
            return None
        if not device.model_id:
            return None
        serial = cls._normalized_serial(device.serial)
        serial_key = serial if serial is not None else "__unknown__"
        return (device.backend, device.model_id, serial_key)

    @staticmethod
    def _rawhid_transport_rank(model_id: Optional[str], product_id: int) -> int:
        slug = str(model_id or "").strip().lower()
        model = DeviceService._CLASS_REGISTRY.get(slug) if slug else None
        raw = tuple(getattr(model, "rawhid_transport_priority", ()) or ())
        pid_order: Dict[int, int] = {}
        for idx, token in enumerate(raw):
            try:
                pid_order[int(token)] = int(idx)
            except Exception:
                continue
        if not pid_order:
            return 3
        return pid_order.get(int(product_id), len(pid_order) + 3)

    @classmethod
    def _rawhid_preference_key(cls, device: DetectedDevice) -> Tuple[int, int, int, str]:
        # Prefer reliable transport endpoints when one physical mouse appears on multiple links.
        pid_rank = cls._rawhid_transport_rank(device.model_id, device.product_id)
        handle = device.backend_handle if isinstance(device.backend_handle, dict) else {}
        profile = handle.get("profile")
        experimental_rank = 1 if getattr(profile, "experimental", False) else 0
        return (
            experimental_rank,
            pid_rank,
            -len(device.capabilities),
            device.identifier,
        )

    @classmethod
    def _collapse_rawhid_transports(cls, devices: List[DetectedDevice]) -> List[DetectedDevice]:
        collapsed: List[DetectedDevice] = []
        rawhid_index: Dict[Tuple[str, str, str], int] = {}

        for device in devices:
            rawhid_key = cls._rawhid_merge_key(device)
            if rawhid_key is None:
                collapsed.append(device)
                continue

            existing_idx = rawhid_index.get(rawhid_key)
            if existing_idx is None:
                rawhid_index[rawhid_key] = len(collapsed)
                collapsed.append(device)
                continue

            current = collapsed[existing_idx]
            if cls._rawhid_preference_key(device) < cls._rawhid_preference_key(current):
                collapsed[existing_idx] = device

        return collapsed

    @staticmethod
    def _collapse_detect_only_duplicates(devices: List[DetectedDevice]) -> List[DetectedDevice]:
        """Drop detect-only duplicates when a control-capable backend already found the same USB ID."""
        control_usb_ids = {
            (device.vendor_id, device.product_id)
            for device in devices
            if device.capabilities
        }
        detect_only_backends = {"hidapi", "macos-profiler"}
        if not control_usb_ids:
            return devices

        collapsed: List[DetectedDevice] = []
        for device in devices:
            usb_key = (device.vendor_id, device.product_id)
            if device.backend in detect_only_backends and usb_key in control_usb_ids:
                continue
            collapsed.append(device)
        return collapsed

    @classmethod
    def _hidapi_interface_sort_key(cls, device: DetectedDevice) -> Tuple[int, str]:
        """Prefer lower HID interface number, then stable identifier order."""
        handle = device.backend_handle if isinstance(device.backend_handle, dict) else {}
        iface = handle.get("interface_number")
        try:
            iface_i = int(iface) if iface is not None else 999
        except (TypeError, ValueError):
            iface_i = 999
        return (iface_i, device.identifier)

    @classmethod
    def _collapse_hidapi_interface_duplicates(cls, devices: List[DetectedDevice]) -> List[DetectedDevice]:
        """Keep one hidapi row per (VID, PID, serial) when paths differ only by HID interface.

        On macOS, ``hidapi`` often lists multiple collections for one USB device (distinct
        ``path`` / ``DevSrvsID`` values). Detect-only entries would otherwise clutter the list.
        """
        groups: Dict[Tuple[int, int, str], List[DetectedDevice]] = {}
        for device in devices:
            if device.backend != "hidapi" or device.capabilities:
                continue
            serial = cls._normalized_serial(device.serial)
            serial_key = serial if serial is not None else ""
            key = (device.vendor_id, device.product_id, serial_key)
            groups.setdefault(key, []).append(device)

        winners: Dict[Tuple[int, int, str], DetectedDevice] = {}
        for key, group in groups.items():
            if len(group) <= 1:
                winners[key] = group[0]
            else:
                winners[key] = min(group, key=cls._hidapi_interface_sort_key)

        result: List[DetectedDevice] = []
        for device in devices:
            if device.backend != "hidapi" or device.capabilities:
                result.append(device)
                continue
            serial = cls._normalized_serial(device.serial)
            serial_key = serial if serial is not None else ""
            key = (device.vendor_id, device.product_id, serial_key)
            group = groups[key]
            if len(group) <= 1:
                result.append(device)
                continue
            if device is not winners[key]:
                continue
            result.append(device)
        return result

    @staticmethod
    def _collapse_bt_backend_duplicates(devices: List[DetectedDevice]) -> List[DetectedDevice]:
        """
        Prefer macos-ble for Bluetooth endpoints when both macos-ble and rawhid/hidapi
        report the same BT product in auto mode.
        """
        bt_keys_with_macos_ble = {
            (device.vendor_id, device.product_id, device.model_id)
            for device in devices
            if device.backend == "macos-ble"
        }
        if not bt_keys_with_macos_ble:
            return devices

        collapsed: List[DetectedDevice] = []
        for device in devices:
            bt_key = (device.vendor_id, device.product_id, device.model_id)
            if bt_key in bt_keys_with_macos_ble and device.backend != "macos-ble":
                continue
            collapsed.append(device)
        return collapsed

    def backend_errors(self) -> Dict[str, str]:
        errors: Dict[str, str] = {}
        for backend in self.backends:
            error = getattr(backend, "last_error", None)
            if error is not None:
                errors[backend.name] = str(error)
        return errors

    def discover_devices(
        self,
        model_filter: Optional[str] = None,
        *,
        collapse_transports: bool = True,
    ) -> List[DetectedDevice]:
        merged: List[DetectedDevice] = []
        dedupe_index: Dict[Tuple[int, int, str], int] = {}

        for backend in self.backends:
            for device in backend.detect():
                model = self.registry.find_by_usb(device.vendor_id, device.product_id)
                if model is None:
                    model = self.registry.find_by_name(device.name)
                if model is not None:
                    device.model_id = model.slug
                    device.model_name = model.name

                if model_filter and device.model_id != model_filter:
                    continue

                dedupe_key = (
                    device.vendor_id,
                    device.product_id,
                    device.serial or device.identifier,
                )

                if dedupe_key in dedupe_index:
                    existing_idx = dedupe_index[dedupe_key]
                    existing = merged[existing_idx]
                    existing_priority = self._backend_priority(existing.backend)
                    new_priority = self._backend_priority(device.backend)
                    if new_priority > existing_priority:
                        merged[existing_idx] = device
                    elif new_priority == existing_priority and len(device.capabilities) > len(existing.capabilities):
                        merged[existing_idx] = device
                    continue

                dedupe_index[dedupe_key] = len(merged)
                merged.append(device)

        merged = self._collapse_detect_only_duplicates(merged)
        merged = self._collapse_hidapi_interface_duplicates(merged)

        if collapse_transports:
            merged = self._collapse_rawhid_transports(merged)
            merged = self._collapse_bt_backend_duplicates(merged)
            return merged
        return merged

    def resolve_backend(self, device: DetectedDevice) -> Backend:
        return self.backends_by_name[device.backend]


def normalize_ble_address_query(raw: Optional[str]) -> Optional[str]:
    """Normalize user MAC/UUID text for matching against device ids and serials."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text or text in {"...", "…"}:
        return None
    return "".join(ch for ch in text if ch.isalnum())


def device_matches_ble_address(device: DetectedDevice, query_compact: str) -> bool:
    """True if ``query_compact`` (from :func:`normalize_ble_address_query`) identifies this device."""
    if not query_compact:
        return False
    ident_compact = "".join(ch for ch in (device.identifier or "").lower() if ch.isalnum())
    if query_compact in ident_compact:
        return True
    serial_compact = normalize_ble_address_query(device.serial)
    if serial_compact and serial_compact == query_compact:
        return True
    handle = device.backend_handle if isinstance(device.backend_handle, dict) else {}
    bt = handle.get("bt_address")
    if isinstance(bt, str):
        bt_q = normalize_ble_address_query(bt)
        if bt_q and bt_q == query_compact:
            return True
    return False


def select_device(
    devices: Sequence[DetectedDevice],
    device_id: Optional[str] = None,
    model_id: Optional[str] = None,
    ble_address: Optional[str] = None,
) -> DetectedDevice:
    matches = list(devices)

    if ble_address:
        query = normalize_ble_address_query(ble_address)
        if query:
            matches = [device for device in matches if device_matches_ble_address(device, query)]
            if not matches:
                raise DeviceSelectionError(
                    f"No device matches BLE address '{ble_address}'. "
                    "Use `razecli devices` for the exact id, or check MAC/UUID spelling."
                )

    if device_id:
        matches = [device for device in matches if device.identifier == device_id]
        if not matches:
            raise DeviceSelectionError(f"No device with id '{device_id}' was found")

    if model_id:
        matches = [device for device in matches if device.model_id == model_id]
        if not matches:
            raise DeviceSelectionError(f"No connected device matches model '{model_id}'")

    if len(matches) == 1:
        return matches[0]

    if not matches:
        raise DeviceSelectionError("No Razer devices were found")

    device_ids = ", ".join(device.identifier for device in matches)
    raise DeviceSelectionError(
        f"Multiple devices match. Specify --device or --address. Available ids: {device_ids}"
    )
