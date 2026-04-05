import unittest

from razecli.device_service import select_device
from razecli.errors import DeviceSelectionError
from razecli.types import DetectedDevice


class DeviceSelectionTest(unittest.TestCase):
    def test_select_by_id(self):
        devices = [
            DetectedDevice(
                identifier="rawhid:one",
                name="Razer One",
                vendor_id=0x1532,
                product_id=0x007C,
                backend="rawhid",
                model_id="deathadder-v2-pro",
            )
        ]

        selected = select_device(devices, device_id="rawhid:one")
        self.assertEqual(selected.identifier, "rawhid:one")

    def test_multiple_devices_requires_explicit_id(self):
        devices = [
            DetectedDevice(
                identifier="rawhid:one",
                name="Razer One",
                vendor_id=0x1532,
                product_id=0x007C,
                backend="rawhid",
                model_id="deathadder-v2-pro",
            ),
            DetectedDevice(
                identifier="rawhid:two",
                name="Razer Two",
                vendor_id=0x1532,
                product_id=0x007D,
                backend="rawhid",
                model_id="deathadder-v2-pro",
            ),
        ]

        with self.assertRaises(DeviceSelectionError):
            select_device(devices)

    def test_select_by_ble_address_colon_mac(self):
        a = DetectedDevice(
            identifier="macos-ble:1532:008E:bt:F6F20D4ED930",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            serial="F6:F2:0D:4E:D9:30",
            model_id="deathadder-v2-pro",
            capabilities={"dpi-stages"},
            backend_handle={"bt_address": "F6:F2:0D:4E:D9:30"},
        )
        b = DetectedDevice(
            identifier="macos-ble:1532:008E:bt:AABBCCDDEEFF",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            model_id="deathadder-v2-pro",
            capabilities={"dpi-stages"},
        )
        picked = select_device([a, b], ble_address="F6:F2:0D:4E:D9:30", model_id="deathadder-v2-pro")
        self.assertEqual(picked.identifier, a.identifier)

    def test_select_ble_address_unknown_raises(self):
        dev = DetectedDevice(
            identifier="macos-ble:1532:008E:bt:F6F20D4ED930",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            model_id="deathadder-v2-pro",
            capabilities=set(),
        )
        with self.assertRaises(DeviceSelectionError) as ctx:
            select_device([dev], ble_address="AA:BB:CC:DD:EE:FF")
        self.assertIn("No device matches BLE address", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
