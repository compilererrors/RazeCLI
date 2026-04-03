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
from razecli.feature_scaffolds import set_rgb_scaffold
from razecli.types import DetectedDevice


class _FakeRegistry:
    def get(self, _slug):
        return object()


class _FakeBackend:
    def __init__(self, battery=77):
        self._battery = int(battery)

    def get_battery(self, _device):
        return self._battery


class _FakeHardwareBackend(_FakeBackend):
    def __init__(self):
        super().__init__(battery=88)
        self._rgb = {
            "mode": "static",
            "brightness": 65,
            "color": "112233",
            "modes_supported": ["off", "static", "breathing", "spectrum"],
        }
        self._mapping = {
            "left_click": "mouse:left",
            "right_click": "mouse:right",
            "middle_click": "mouse:middle",
            "side_1": "mouse:back",
            "side_2": "mouse:forward",
            "dpi_cycle": "dpi:cycle",
        }

    def get_rgb(self, _device):
        return dict(self._rgb)

    def set_rgb(self, _device, *, mode, brightness=None, color=None):
        self._rgb["mode"] = str(mode)
        if brightness is not None:
            self._rgb["brightness"] = int(brightness)
        if color is not None:
            self._rgb["color"] = str(color).lower().lstrip("#")
        return dict(self._rgb)

    def list_button_mapping_actions(self, _device):
        return {
            "buttons": list(self._mapping.keys()),
            "actions": ["mouse:left", "mouse:right", "mouse:middle", "mouse:back", "mouse:forward", "dpi:cycle"],
        }


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

    def test_ble_poll_probe_json_contract(self):
        original_vendor = cli_ble_mod.ble_vendor_transceive
        original_alias_resolve = cli_ble_mod.ble_alias_resolve
        cli_ble_mod.ble_vendor_transceive = lambda **_kwargs: {
            "vendor_decode": {"status": "success", "status_code": 2, "payload_hex": "01"}
        }
        cli_ble_mod.ble_alias_resolve = lambda **_kwargs: {
            "requested_mac": "02:11:22:33:44:55",
            "resolved_address": "AABBCCDD-0011-2233-4455-66778899AABB",
        }
        args = argparse.Namespace(
            ble_command="poll-probe",
            address="02:11:22:33:44:55",
            name="DA V2 Pro",
            timeout=5.0,
            response_timeout=1.0,
            attempts=1,
            key=["00850001"],
            json=True,
        )
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_ble_mod.handle_ble(args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["decoded_hz"], 1000)
            self.assertEqual(payload["keys"], ["00850001"])
            self.assertEqual(len(payload["results"]), 1)
            self.assertEqual(payload["results"][0]["decoded_hz"], 1000)
            self.assertEqual(
                payload["auto_resolution"]["resolved_address"],
                "AABBCCDD-0011-2233-4455-66778899AABB",
            )
        finally:
            cli_ble_mod.ble_vendor_transceive = original_vendor
            cli_ble_mod.ble_alias_resolve = original_alias_resolve

    def test_ble_poll_probe_reports_unsupported_on_parameter_error(self):
        original_vendor = cli_ble_mod.ble_vendor_transceive
        cli_ble_mod.ble_vendor_transceive = lambda **_kwargs: {
            "vendor_decode": {"status": "parameter-error", "status_code": 5, "payload_hex": ""}
        }
        args = argparse.Namespace(
            ble_command="poll-probe",
            address="AABBCCDD-0011-2233-4455-66778899AABB",
            name="DA V2 Pro",
            timeout=5.0,
            response_timeout=1.0,
            attempts=1,
            key=["00850001"],
            json=True,
        )
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_ble_mod.handle_ble(args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "unsupported")
            self.assertIsNone(payload["decoded_hz"])
        finally:
            cli_ble_mod.ble_vendor_transceive = original_vendor

    def test_ble_poll_probe_falls_back_from_resolved_uuid_to_mac(self):
        original_vendor = cli_ble_mod.ble_vendor_transceive
        original_alias_resolve = cli_ble_mod.ble_alias_resolve
        cli_ble_mod.ble_alias_resolve = lambda **_kwargs: {
            "requested_mac": "02:11:22:33:44:55",
            "resolved_address": "AABBCCDD-0011-2233-4455-66778899AABB",
        }

        calls = {"addresses": []}

        def _vendor(**kwargs):
            address = kwargs.get("address")
            calls["addresses"].append(address)
            if str(address) == "AABBCCDD-0011-2233-4455-66778899AABB":
                raise RuntimeError(
                    "BLE raw transceive failed for AABBCCDD-0011-2233-4455-66778899AABB: "
                    "Device with address AABBCCDD-0011-2233-4455-66778899AABB was not found"
                )
            return {"vendor_decode": {"status": "success", "status_code": 2, "payload_hex": "01"}}

        cli_ble_mod.ble_vendor_transceive = _vendor
        args = argparse.Namespace(
            ble_command="poll-probe",
            address="02:11:22:33:44:55",
            name="DA V2 Pro",
            timeout=5.0,
            response_timeout=1.0,
            attempts=1,
            key=["00850001"],
            json=True,
        )
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_ble_mod.handle_ble(args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["decoded_hz"], 1000)
            self.assertTrue(bool(payload["mac_fallback_active"]))
            self.assertEqual(calls["addresses"][0], "AABBCCDD-0011-2233-4455-66778899AABB")
            self.assertEqual(calls["addresses"][1], "02:11:22:33:44:55")
            self.assertEqual(payload["results"][0]["used_fallback_address"], True)
        finally:
            cli_ble_mod.ble_vendor_transceive = original_vendor
            cli_ble_mod.ble_alias_resolve = original_alias_resolve

    def test_ble_bank_probe_json_contract(self):
        original_vendor = cli_ble_mod.ble_vendor_transceive
        original_alias_resolve = cli_ble_mod.ble_alias_resolve
        cli_ble_mod.ble_vendor_transceive = lambda **_kwargs: {
            "vendor_decode": {
                "status": "success",
                "status_code": 2,
                "payload_hex": "0101dc05dc050000",
            }
        }
        cli_ble_mod.ble_alias_resolve = lambda **_kwargs: {
            "requested_mac": "02:11:22:33:44:55",
            "resolved_address": "AABBCCDD-0011-2233-4455-66778899AABB",
        }
        args = argparse.Namespace(
            ble_command="bank-probe",
            address="02:11:22:33:44:55",
            name="DA V2 Pro",
            timeout=5.0,
            response_timeout=1.0,
            attempts=1,
            key=["0b840100"],
            include_write_keys=False,
            json=True,
        )
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_ble_mod.handle_ble(args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["keys"], ["0b840100"])
            self.assertEqual(len(payload["results"]), 1)
            self.assertEqual(payload["results"][0]["decoded_bank"]["active_stage"], 1)
            self.assertEqual(payload["results"][0]["decoded_bank"]["stages_count"], 1)
            self.assertEqual(payload["results"][0]["decoded_bank"]["stages"][0]["dpi_x"], 1500)
            self.assertEqual(len(payload["signatures"]), 1)
            self.assertEqual(payload["primary_bank_signature"], payload["bank_signature"])
            self.assertEqual(
                payload["auto_resolution"]["resolved_address"],
                "AABBCCDD-0011-2233-4455-66778899AABB",
            )
        finally:
            cli_ble_mod.ble_vendor_transceive = original_vendor
            cli_ble_mod.ble_alias_resolve = original_alias_resolve

    def test_ble_bank_snapshot_json_contract(self):
        original_vendor = cli_ble_mod.ble_vendor_transceive
        cli_ble_mod.ble_vendor_transceive = lambda **_kwargs: {
            "vendor_decode": {
                "status": "success",
                "status_code": 2,
                "payload_hex": "0101dc05dc050000",
            }
        }
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                snapshot_path = str(Path(tmp_dir) / "bank_snapshots.json")
                args = argparse.Namespace(
                    ble_command="bank-snapshot",
                    address="AABBCCDD-0011-2233-4455-66778899AABB",
                    name="DA V2 Pro",
                    timeout=5.0,
                    response_timeout=1.0,
                    attempts=1,
                    key=["0b840100"],
                    include_write_keys=False,
                    label="green-led-bank",
                    path=snapshot_path,
                    json=True,
                )

                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli_ble_mod.handle_ble(args)
                self.assertEqual(rc, 0)
                payload = json.loads(buf.getvalue())
                self.assertEqual(payload["status"], "ok")
                self.assertIn("snapshot", payload)
                self.assertEqual(payload["snapshot"]["path"], snapshot_path)
                self.assertEqual(payload["snapshot"]["count"], 1)
                self.assertEqual(payload["snapshot"]["label"], "green-led-bank")

                with open(snapshot_path, "r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                self.assertIn("snapshots", stored)
                self.assertEqual(len(stored["snapshots"]), 1)
                self.assertEqual(stored["snapshots"][0]["label"], "green-led-bank")
        finally:
            cli_ble_mod.ble_vendor_transceive = original_vendor

    def test_ble_bank_probe_deep_uses_extended_default_keys(self):
        original_vendor = cli_ble_mod.ble_vendor_transceive

        def _vendor(**_kwargs):
            return {
                "vendor_decode": {
                    "status": "parameter-error",
                    "status_code": 5,
                    "payload_hex": "",
                }
            }

        cli_ble_mod.ble_vendor_transceive = _vendor
        args = argparse.Namespace(
            ble_command="bank-probe",
            address="AABBCCDD-0011-2233-4455-66778899AABB",
            name="DA V2 Pro",
            timeout=5.0,
            response_timeout=1.0,
            attempts=2,
            key=None,
            deep=True,
            include_write_keys=False,
            settle_delay=None,
            reconnect_each_round=None,
            json=True,
        )
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_ble_mod.handle_ble(args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload.get("deep"))
            self.assertEqual(payload["status"], "unsupported")
            self.assertIn("0b840100", payload["keys"])
            self.assertNotIn("0b040100", payload["keys"])
            self.assertGreater(len(payload["keys"]), 2)
            self.assertFalse(bool(payload.get("include_write_keys")))
            self.assertFalse(bool(payload.get("write_keys_included")))
            self.assertGreater(payload.get("settle_delay_s", 0.0), 0.0)
            self.assertTrue(bool(payload.get("reconnect_each_round")))
        finally:
            cli_ble_mod.ble_vendor_transceive = original_vendor

    def test_ble_bank_probe_deep_include_write_keys(self):
        original_vendor = cli_ble_mod.ble_vendor_transceive

        def _vendor(**_kwargs):
            return {
                "vendor_decode": {
                    "status": "parameter-error",
                    "status_code": 5,
                    "payload_hex": "",
                }
            }

        cli_ble_mod.ble_vendor_transceive = _vendor
        args = argparse.Namespace(
            ble_command="bank-probe",
            address="AABBCCDD-0011-2233-4455-66778899AABB",
            name="DA V2 Pro",
            timeout=5.0,
            response_timeout=1.0,
            attempts=1,
            key=None,
            deep=True,
            include_write_keys=True,
            settle_delay=None,
            reconnect_each_round=None,
            json=True,
        )
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_ble_mod.handle_ble(args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload.get("deep"))
            self.assertTrue(bool(payload.get("include_write_keys")))
            self.assertIn("0b040100", payload["keys"])
            self.assertTrue(bool(payload.get("write_keys_included")))
        finally:
            cli_ble_mod.ble_vendor_transceive = original_vendor

    def test_ble_bank_compare_json_contract(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_path = Path(tmp_dir) / "bank_snapshots.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshots": [
                            {
                                "captured_at": "2026-04-03T19:00:00+00:00",
                                "label": "bank-a",
                                "status": "ok",
                                "primary_bank_signature": "sig-a",
                                "signatures": [
                                    {
                                        "stages": [
                                            {"index": 1, "dpi_x": 800, "dpi_y": 800, "stage_id": 1}
                                        ]
                                    }
                                ],
                            },
                            {
                                "captured_at": "2026-04-03T19:01:00+00:00",
                                "label": "bank-b",
                                "status": "ok",
                                "primary_bank_signature": "sig-b",
                                "signatures": [
                                    {
                                        "stages": [
                                            {"index": 1, "dpi_x": 2200, "dpi_y": 2200, "stage_id": 1}
                                        ]
                                    }
                                ],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            args = argparse.Namespace(
                ble_command="bank-compare",
                label_a="bank-a",
                label_b="bank-b",
                path=str(snapshot_path),
                json=True,
            )

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_ble_mod.handle_ble(args)
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["status"], "different")
            self.assertFalse(payload["signature_equal"])
            self.assertEqual(payload["snapshot_a"]["primary_bank_signature"], "sig-a")
            self.assertEqual(payload["snapshot_b"]["primary_bank_signature"], "sig-b")
            self.assertEqual(len(payload["stage_differences"]), 1)

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

    def test_rgb_get_prefers_hardware_state_when_available(self):
        device = DetectedDevice(
            identifier="macos-ble:1532:008E:bt:ABC",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "rgb"},
        )
        service = _FakeService(device, backend=_FakeHardwareBackend())
        args = argparse.Namespace(
            rgb_command="get",
            store_file=None,
            model=None,
            device=None,
            json=True,
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = handle_rgb(service, args)
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["rgb"]["mode"], "static")
        self.assertEqual(payload["rgb"]["brightness"], 65)
        self.assertEqual(payload["rgb"]["color"], "112233")
        self.assertEqual(payload["rgb"]["hardware_apply"], "read")

    def test_rgb_get_prefers_local_mode_when_hardware_mode_is_inferred(self):
        device = DetectedDevice(
            identifier="macos-ble:1532:008E:bt:ABC",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "rgb"},
        )
        backend = _FakeHardwareBackend()
        backend._rgb["mode"] = "static"
        backend._rgb["mode_inferred"] = True
        service = _FakeService(device, backend=backend)

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = str(Path(tmp_dir) / "feature_store.json")
            set_rgb_scaffold(
                model_id=device.model_id,
                mode="breathing",
                brightness=50,
                color="00ff88",
                path=store,
            )
            args = argparse.Namespace(
                rgb_command="get",
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
            self.assertEqual(payload["rgb"]["mode"], "breathing")
            self.assertEqual(payload["rgb"]["brightness"], 65)
            self.assertEqual(payload["rgb"]["hardware_apply"], "read")

    def test_button_actions_prefers_hardware_actions_when_available(self):
        device = DetectedDevice(
            identifier="macos-ble:1532:008E:bt:ABC",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "button-mapping"},
        )
        service = _FakeService(device, backend=_FakeHardwareBackend())
        args = argparse.Namespace(
            button_mapping_command="actions",
            model=None,
            device=None,
            json=True,
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = handle_button_mapping(service, args)
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertIn("side_1", payload["buttons_supported"])
        self.assertIn("dpi:cycle", payload["actions_suggested"])
        self.assertEqual(payload["hardware_apply"], "read")

    def test_rgb_menu_starts_tui_with_rgb_editor(self):
        device = DetectedDevice(
            identifier="macos-ble:1532:008E:bt:ABC",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "rgb"},
        )
        service = _FakeService(device)

        import razecli.tui as tui_mod

        original_run_tui = tui_mod.run_tui
        captured: dict[str, object] = {}

        def _fake_run_tui(**kwargs):
            captured.update(kwargs)
            return 0

        tui_mod.run_tui = _fake_run_tui
        args = argparse.Namespace(
            rgb_command="menu",
            model="deathadder-v2-pro",
            device=None,
            all_models=False,
            all_transports=False,
            json=False,
        )
        try:
            rc = handle_rgb(service, args)
            self.assertEqual(rc, 0)
            self.assertEqual(captured["startup_editor"], "rgb")
            self.assertEqual(captured["model_filter"], "deathadder-v2-pro")
            self.assertEqual(captured["collapse_transports"], True)
        finally:
            tui_mod.run_tui = original_run_tui

    def test_button_mapping_menu_starts_tui_with_button_editor(self):
        device = DetectedDevice(
            identifier="macos-ble:1532:008E:bt:ABC",
            name="DA V2 Pro",
            vendor_id=0x1532,
            product_id=0x008E,
            backend="macos-ble",
            model_id="deathadder-v2-pro",
            capabilities={"dpi", "button-mapping"},
        )
        service = _FakeService(device)

        import razecli.tui as tui_mod

        original_run_tui = tui_mod.run_tui
        captured: dict[str, object] = {}

        def _fake_run_tui(**kwargs):
            captured.update(kwargs)
            return 0

        tui_mod.run_tui = _fake_run_tui
        args = argparse.Namespace(
            button_mapping_command="menu",
            model="deathadder-v2-pro",
            device=None,
            all_models=False,
            all_transports=False,
            json=False,
        )
        try:
            rc = handle_button_mapping(service, args)
            self.assertEqual(rc, 0)
            self.assertEqual(captured["startup_editor"], "button-mapping")
            self.assertEqual(captured["model_filter"], "deathadder-v2-pro")
            self.assertEqual(captured["collapse_transports"], True)
        finally:
            tui_mod.run_tui = original_run_tui


if __name__ == "__main__":
    unittest.main()
