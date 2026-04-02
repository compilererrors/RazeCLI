import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from razecli.backends.macos_profiler_backend import MacOSProfilerBackend


class MacOSProfilerBackendTest(unittest.TestCase):
    @patch("subprocess.run")
    def test_detects_usb_device(self, run_mock):
        usb_payload = {
            "SPUSBDataType": [
                {
                    "_items": [
                        {
                            "_name": "Razer DeathAdder V2 Pro",
                            "vendor_id": "0x1532",
                            "product_id": "0x007c",
                            "serial_num": "000000000000",
                            "location_id": "0x00124000",
                        }
                    ]
                }
            ]
        }
        bt_payload = {"SPBluetoothDataType": [{}]}

        run_mock.side_effect = [
            SimpleNamespace(stdout=json.dumps(usb_payload)),
            SimpleNamespace(stdout=json.dumps(bt_payload)),
        ]

        backend = MacOSProfilerBackend()
        backend._supported = True

        devices = backend.detect()
        self.assertEqual(len(devices), 1)
        device = devices[0]

        self.assertEqual(device.vendor_id, 0x1532)
        self.assertEqual(device.product_id, 0x007C)
        self.assertEqual(device.model_id, None)
        self.assertEqual(device.backend, "macos-profiler")
        self.assertIn("macos-usb", device.identifier)

    @patch("subprocess.run")
    def test_detects_bluetooth_device_by_name(self, run_mock):
        usb_payload = {"SPUSBDataType": []}
        bt_payload = {
            "SPBluetoothDataType": [
                {
                    "_items": [
                        {
                            "device_title": "Razer DeathAdder V2 Pro",
                            "device_address": "12-34-56-78-90-AB",
                        }
                    ]
                }
            ]
        }

        run_mock.side_effect = [
            SimpleNamespace(stdout=json.dumps(usb_payload)),
            SimpleNamespace(stdout=json.dumps(bt_payload)),
        ]

        backend = MacOSProfilerBackend()
        backend._supported = True

        devices = backend.detect()
        self.assertEqual(len(devices), 1)
        device = devices[0]

        self.assertEqual(device.vendor_id, 0x1532)
        self.assertEqual(device.backend, "macos-profiler")
        self.assertTrue(device.identifier.startswith("macos-bt:"))

    @patch("subprocess.run")
    def test_bluetooth_text_fallback_parses_connected_device(self, run_mock):
        usb_payload = {"SPUSBDataType": []}
        bt_json_payload = {"SPBluetoothDataType": [{}]}
        bt_text = """
Bluetooth Controller:
  Address: 60:3E:5F:51:42:A8
  State: On
  Connected:
  DA V2 Pro:
  Address: 02:11:22:33:44:55
  Vendor ID: 0x1532
  Product ID: 0x008E
  Minor Type: Mouse
  Services: 0x400000 < BLE >
"""

        run_mock.side_effect = [
            SimpleNamespace(stdout=json.dumps(usb_payload)),
            SimpleNamespace(stdout=json.dumps(bt_json_payload)),
            SimpleNamespace(stdout=bt_text),
        ]

        backend = MacOSProfilerBackend()
        backend._supported = True

        devices = backend.detect()
        self.assertEqual(len(devices), 1)
        device = devices[0]

        self.assertEqual(device.name, "DA V2 Pro")
        self.assertEqual(device.vendor_id, 0x1532)
        self.assertEqual(device.product_id, 0x008E)
        self.assertEqual(device.serial, "02:11:22:33:44:55")


if __name__ == "__main__":
    unittest.main()
