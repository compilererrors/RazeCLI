import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import razecli.cli_ble as cli_ble_mod
from razecli.cli_battery import handle_battery
from razecli.cli_button_mapping import handle_button_mapping
from razecli.cli_devices import handle_devices
from razecli.cli_rgb import handle_rgb
from razecli.types import DetectedDevice


class _FakeRegistry:
    def get(self, _slug):
        return object()


class _FakeBackend:
    def __init__(self, battery=77):
        self._battery = int(battery)

    def get_battery(self, _device):
        return self._battery


class _FakeService:
    def __init__(self, device: DetectedDevice, backend=None):
        self._device = device
        self._backend = backend or _FakeBackend()
        self.registry = _FakeRegistry()

    def discover_devices(self, model_filter=None, collapse_transports=True):
        _ = (model_filter, collapse_transports)
        return [self._device]

    def resolve_backend(self, _device):
        return self._backend

    def backend_errors(self):
        return {}


class CliJsonContractTest(unittest.TestCase):
    def test_devices_json_contract(self):
        device = DetectedDevice(
            identifier="rawhid:1532:007C",
            name="Razer DeathAdder V2 Pro",
            vendor_id=0x1532,
            product_id=0x007C,
            backend="rawhid",
            serial="000000000000",
            model_id="deathadder-v2-pro",
            model_name="Razer DeathAdder V2 Pro",
            capabilities={"dpi", "battery"},
        )
        service = _FakeService(device)
        args = argparse.Namespace(
            model=None,
            all_transports=False,
            device=None,
            backend="rawhid",
            json=True,
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = handle_devices(service, args)
        self.assertEqual(rc, 0)

        payload = json.loads(buf.getvalue())
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        row = payload[0]
        self.assertEqual(row["id"], "rawhid:1532:007C")
        self.assertEqual(row["usb_id"], "1532:007C")
        self.assertEqual(row["model_id"], "deathadder-v2-pro")
        self.assertEqual(row["backend"], "rawhid")
        self.assertIn("dpi", row["capabilities"])

    def test_battery_json_contract(self):
        device = DetectedDevice(
            identifier="rawhid:1532:007C",
            name="Razer DeathAdder V2 Pro",
            vendor_id=0x1532,
            product_id=0x007C,
            backend="rawhid",
            model_id="deathadder-v2-pro",
            capabilities={"battery"},
        )
        service = _FakeService(device, backend=_FakeBackend(battery=91))
        args = argparse.Namespace(
            battery_command="get",
            model=None,
            device=None,
            json=True,
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = handle_battery(service, args)
        self.assertEqual(rc, 0)

        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["id"], "rawhid:1532:007C")
        self.assertEqual(payload["model"], "deathadder-v2-pro")
        self.assertEqual(payload["battery_percent"], 91)

    def test_ble_scan_json_hint_contract(self):
        original_scan = cli_ble_mod.scan_ble_devices
        cli_ble_mod.scan_ble_devices = lambda timeout: [
            {"name": "DA V2 Pro", "address": "AA-BB", "rssi": None, "source": "bleak"},
            {"name": "Keyboard", "address": "CC-DD", "rssi": -60, "source": "bleak"},
        ]
        args = argparse.Namespace(
            ble_command="scan",
            timeout=4.0,
            name="NoMatch",
            json=True,
        )
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_ble_mod.handle_ble(args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["count"], 0)
            self.assertEqual(payload["devices"], [])
            self.assertIn("hint", payload)
            self.assertIn("available_names", payload)
            self.assertIn("DA V2 Pro", payload["available_names"])
        finally:
            cli_ble_mod.scan_ble_devices = original_scan

    def test_rgb_set_json_contract(self):
        device = DetectedDevice(
            identifier="rawhid:1532:007C",
            name="Razer DeathAdder V2 Pro",
            vendor_id=0x1532,
            product_id=0x007C,
            backend="rawhid",
            model_id="deathadder-v2-pro",
            capabilities={"dpi"},
        )
        service = _FakeService(device)

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = str(Path(tmp_dir) / "feature_store.json")
            args = argparse.Namespace(
                rgb_command="set",
                mode="static",
                brightness=55,
                color="#00ff88",
                store_file=store,
                model=None,
                device=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = handle_rgb(service, args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["id"], "rawhid:1532:007C")
            self.assertEqual(payload["model"], "deathadder-v2-pro")
            self.assertEqual(payload["rgb"]["mode"], "static")
            self.assertEqual(payload["rgb"]["brightness"], 55)
            self.assertEqual(payload["rgb"]["color"], "00ff88")

    def test_button_mapping_set_json_contract(self):
        device = DetectedDevice(
            identifier="rawhid:1532:007C",
            name="Razer DeathAdder V2 Pro",
            vendor_id=0x1532,
            product_id=0x007C,
            backend="rawhid",
            model_id="deathadder-v2-pro",
            capabilities={"dpi"},
        )
        service = _FakeService(device)

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = str(Path(tmp_dir) / "feature_store.json")
            args = argparse.Namespace(
                button_mapping_command="set",
                button="side_1",
                action="mouse:back",
                store_file=store,
                model=None,
                device=None,
                json=True,
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = handle_button_mapping(service, args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["id"], "rawhid:1532:007C")
            self.assertEqual(payload["model"], "deathadder-v2-pro")
            self.assertEqual(payload["button_mapping"]["mapping"]["side_1"], "mouse:back")


if __name__ == "__main__":
    unittest.main()
