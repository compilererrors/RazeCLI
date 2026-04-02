"""hidapi-based backend for hardware detection only."""

from typing import Any, Dict, List

from razecli.types import DetectedDevice

from razecli.backends.base import Backend


class HidapiBackend(Backend):
    name = "hidapi"

    def __init__(self) -> None:
        self._hid = None
        self.last_error = None
        try:
            import hid  # type: ignore

            self._hid = hid
        except Exception as exc:  # pragma: no cover - platform dependent
            self.last_error = exc

    def detect(self) -> List[DetectedDevice]:
        if self._hid is None:
            return []

        devices: List[DetectedDevice] = []
        for info in self._hid.enumerate(0x1532, 0):
            device_info: Dict[str, Any] = dict(info)
            serial = device_info.get("serial_number")
            path = device_info.get("path")

            if isinstance(path, (bytes, bytearray)):
                identifier = path.decode(errors="ignore")
            elif path:
                identifier = str(path)
            elif serial:
                identifier = str(serial)
            else:
                identifier = f"hid-{device_info.get('vendor_id', 0):04x}-{device_info.get('product_id', 0):04x}"

            product = device_info.get("product_string") or "Razer device"

            devices.append(
                DetectedDevice(
                    identifier=identifier,
                    name=str(product),
                    vendor_id=int(device_info.get("vendor_id", 0)),
                    product_id=int(device_info.get("product_id", 0)),
                    serial=str(serial) if serial else None,
                    backend=self.name,
                    capabilities=set(),
                    backend_handle=device_info,
                )
            )

        return devices
