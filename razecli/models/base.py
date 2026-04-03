"""Model definitions for supported devices."""

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple


UsbId = Tuple[int, int]


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
