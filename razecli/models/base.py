"""Model definitions for supported devices."""

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple


UsbId = Tuple[int, int]


@dataclass(frozen=True)
class RawHidPidSpec:
    product_id: int
    capabilities: Sequence[str]
    name_hint: Optional[str] = None
    tx_candidates: Sequence[int] = (0x3F, 0x1F, 0xFF)
    report_id_candidates: Sequence[int] = (0x00,)
    experimental: bool = False
    # For some BT HID nodes, vendor usage pages are more reliable than generic mouse HID.
    prefer_vendor_usage_page: bool = False


@dataclass(frozen=True)
class ModelSpec:
    slug: str
    name: str
    usb_ids: Sequence[UsbId]
    name_aliases: Sequence[str] = ()
    dpi_min: Optional[int] = None
    dpi_max: Optional[int] = None
    supported_poll_rates: Sequence[int] = ()
    # Bluetooth (macos-ble) poll-rate support policy.
    # Keep disabled unless verified on real hardware/firmware.
    ble_poll_rate_supported: bool = False
    ble_supported_poll_rates: Sequence[int] = ()
    # Bluetooth (macos-ble) RGB mode policy.
    # Keep conservative by default; expand per model only after validation.
    ble_supported_rgb_modes: Sequence[str] = ("off", "static")
    # Product IDs that represent Bluetooth endpoints for this model.
    ble_endpoint_product_ids: Sequence[int] = ()
    # Whether BLE control should be treated as experimental for this model.
    ble_endpoint_experimental: bool = False
    # True when BLE cannot reliably expand/create full DPI profile tables yet.
    ble_multi_profile_table_limited: bool = False
    # True when model has a physical onboard profile/bank switch button.
    onboard_profile_bank_switch: bool = False
    # Rawhid PIDs eligible for transport mirror within this model.
    rawhid_mirror_product_ids: Sequence[int] = ()
    # Rawhid endpoint profiles for this model.
    rawhid_pid_specs: Sequence[RawHidPidSpec] = ()
    # Optional rawhid transport priority (first = most preferred) when the same
    # physical device appears on multiple rawhid endpoints.
    rawhid_transport_priority: Sequence[int] = ()
    # Marks which model should be the default CLI/TUI target when --model is omitted.
    cli_default_target: bool = False
    # Model-specific BLE button payload decode layouts (ordered by priority).
    # Supported tags: "razer-v1", "compact-16", "slot-byte6".
    ble_button_decode_layouts: Sequence[str] = ()

    def matches(self, vendor_id: int, product_id: int) -> bool:
        return (vendor_id, product_id) in self.usb_ids

    def matches_name(self, device_name: str) -> bool:
        candidate = device_name.lower()
        if self.name.lower() in candidate:
            return True

        for alias in self.name_aliases:
            if alias.lower() in candidate:
                return True

        return False


def format_usb_id(usb_id: UsbId) -> str:
    vendor_id, product_id = usb_id
    return f"{vendor_id:04X}:{product_id:04X}"


def all_usb_ids(models: Iterable[ModelSpec]) -> Tuple[UsbId, ...]:
    ids = []
    for model in models:
        ids.extend(model.usb_ids)
    return tuple(ids)
