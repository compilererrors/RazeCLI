import unittest
from types import SimpleNamespace

from razecli.device_service import DeviceService
from razecli.types import DetectedDevice


class DeviceServiceTest(unittest.TestCase):
    def test_backend_priority_order(self):
        self.assertGreater(DeviceService._backend_priority("rawhid"), DeviceService._backend_priority("macos-ble"))
        self.assertGreater(DeviceService._backend_priority("macos-ble"), DeviceService._backend_priority("hidapi"))
        self.assertGreater(DeviceService._backend_priority("rawhid"), DeviceService._backend_priority("hidapi"))
        self.assertGreater(DeviceService._backend_priority("hidapi"), DeviceService._backend_priority("macos-profiler"))

    def test_collapse_rawhid_transports_prefers_non_experimental(self):
        dongle = DetectedDevice(
            identifier="rawhid:1532:007D",
            name="Razer DeathAdder V2 Pro",
            vendor_id=0x1532,
            product_id=0x007D,
            backend="rawhid",
            serial="000000000000",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "battery"},
            backend_handle={"profile": SimpleNamespace(experimental=False)},
        )
        bluetooth = DetectedDevice(
            identifier="rawhid:1532:008E",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="rawhid",
            serial=None,
            model_id="deathadder-v2-pro",
            capabilities={"dpi"},
            backend_handle={"profile": SimpleNamespace(experimental=True)},
        )

        collapsed = DeviceService._collapse_rawhid_transports([bluetooth, dongle])
        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0].product_id, 0x007D)

    def test_collapse_bt_backend_duplicates_prefers_macos_ble(self):
        rawhid_bt = DetectedDevice(
            identifier="rawhid:1532:008E",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="rawhid",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "battery"},
        )
        macos_ble_bt = DetectedDevice(
            identifier="macos-ble:1532:008E:bt:F6F20D4ED930",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "battery"},
        )
        hidapi_bt = DetectedDevice(
            identifier="DevSrvsID:4296444892",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="hidapi",
            model_id="deathadder-v2-pro",
            capabilities=set(),
        )

        merged = DeviceService._collapse_detect_only_duplicates([rawhid_bt, macos_ble_bt, hidapi_bt])
        collapsed = DeviceService._collapse_bt_backend_duplicates(merged)

        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0].backend, "macos-ble")


if __name__ == "__main__":
    unittest.main()
