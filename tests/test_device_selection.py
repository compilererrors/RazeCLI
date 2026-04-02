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


if __name__ == "__main__":
    unittest.main()
