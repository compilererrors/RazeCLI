import unittest
import os
from dataclasses import replace
from typing import Optional

import razecli.backends.macos_ble_backend as macos_ble_backend_mod
import razecli.ble_probe as ble_probe_mod
from razecli.backends.macos_ble_backend import (
    KEY_RGB_FRAME_READ,
    KEY_RGB_FRAME_WRITE,
    KEY_RGB_MODE_READ,
    KEY_RGB_MODE_WRITE,
    KEY_BATTERY_RAW_READ,
    KEY_BATTERY_STATUS_READ,
    KEY_DPI_STAGES_READ,
    MacOSBleBackend,
    PRIMARY_VENDOR_READ_CHAR_UUID,
)
from razecli.errors import CapabilityUnsupportedError, RazeCliError
from razecli.types import DetectedDevice


class _FakeRawHid:
    def __init__(self):
        self.last_error = None

    def detect(self):
        return [
            DetectedDevice(
                identifier="rawhid:1532:007C",
                name="Razer DeathAdder V2 Pro",
                vendor_id=0x1532,
                product_id=0x007C,
                serial="000000000000",
                backend="rawhid",
                model_id="deathadder-v2-pro",
                model_name="Razer DeathAdder V2 Pro",
                capabilities={"dpi", "dpi-stages", "poll-rate", "battery"},
                backend_handle={"path": "usb0", "profile": object()},
            ),
            DetectedDevice(
                identifier="rawhid:1532:008E",
                name="DA V2 Pro",
                vendor_id=0x1532,
                product_id=0x008E,
                serial=None,
                backend="rawhid",
                model_id="deathadder-v2-pro",
                model_name="Razer DeathAdder V2 Pro",
                capabilities={"dpi", "dpi-stages", "poll-rate", "battery"},
                backend_handle={"path": "bt0", "profile": object()},
            ),
        ]


class _FakeProfiler:
    def __init__(self):
        self.last_error = None

    def detect(self):
        return [
            DetectedDevice(
                identifier="macos-bt:02:11:22:33:44:55",
                name="DA V2 Pro",
                vendor_id=0x1532,
                product_id=0x008E,
                serial="02:11:22:33:44:55",
                backend="macos-profiler",
                model_id="deathadder-v2-pro",
                model_name="Razer DeathAdder V2 Pro",
                capabilities=set(),
                backend_handle={"source": "test"},
            )
        ]


class _FakeVendorCall:
    def __init__(self):
        self.calls = []
        # Optional hex payloads for extra 0x0B84 read keys (multi-key merge tests).
        self.extra_dpi_read_payloads: Optional[dict[str, str]] = None
        self.poll_code = 0x01
        self.rgb_brightness = 0x80
        self.rgb_brightness_status = None
        self.rgb_brightness_status_code = None
        self.rgb_color = (0x00, 0xFF, 0x00)
        self.rgb_mode_selector = 0x00
        self.rgb_mode_read_enabled = True
        self.rgb_mode_read_payload_hex: Optional[str] = None
        self.rgb_frame_payload_override = None
        self.button_payloads = {}

    def __call__(
        self,
        *,
        address,
        name_query,
        timeout,
        key,
        value_payload=None,
        response_timeout=1.0,
        write_with_response=True,
        notify_enabled=True,
        service_uuid=None,
        write_char_uuid=None,
        read_char_uuids=None,
    ):
        _ = (
            name_query,
            timeout,
            response_timeout,
            write_with_response,
            notify_enabled,
            service_uuid,
            write_char_uuid,
            read_char_uuids,
        )
        key_bytes = bytes(key)
        self.calls.append(
            {
                "address": address,
                "key_hex": key_bytes.hex(),
                "value_payload": bytes(value_payload or b""),
            }
        )
        if key_bytes == KEY_BATTERY_RAW_READ:
            return {"vendor_decode": {"payload_hex": "e6"}}
        if key_bytes == KEY_BATTERY_STATUS_READ:
            return {"vendor_decode": {"payload_hex": "59"}}
        if key_bytes == KEY_DPI_STAGES_READ:
            # active sid=0x11, two stages: 1000 and 1600 (little-endian)
            payload_hex = "110211e803e803000022400640060003"
            return {"vendor_decode": {"payload_hex": payload_hex}}
        if (
            len(key_bytes) == 4
            and int(key_bytes[0]) == 0x0B
            and int(key_bytes[1]) == 0x84
            and isinstance(self.extra_dpi_read_payloads, dict)
        ):
            extra = self.extra_dpi_read_payloads.get(key_bytes.hex())
            if extra:
                return {"vendor_decode": {"payload_hex": extra}}
        if len(key_bytes) == 4 and int(key_bytes[0]) == 0x0B and int(key_bytes[1]) == 0x84:
            # Other 0x0B84 read keys (e.g. 0b840300) — empty unless overridden above.
            return {"vendor_decode": {"payload_hex": ""}}
        if len(key_bytes) == 4 and int(key_bytes[0]) == 0x0B and int(key_bytes[1]) == 0x04:
            return {"vendor_decode": {"payload_hex": ""}}
        if key_bytes == bytes.fromhex("00850001"):
            return {"vendor_decode": {"payload_hex": f"{self.poll_code:02x}"}}
        if key_bytes == bytes.fromhex("00050001"):
            payload = bytes(value_payload or b"")
            if payload:
                self.poll_code = int(payload[0])
            return {"vendor_decode": {"payload_hex": ""}}
        if key_bytes in (bytes.fromhex("10850101"), bytes.fromhex("10850100")):
            payload = {"payload_hex": f"{self.rgb_brightness:02x}"}
            if self.rgb_brightness_status is not None:
                payload["status"] = str(self.rgb_brightness_status)
            if self.rgb_brightness_status_code is not None:
                payload["status_code"] = int(self.rgb_brightness_status_code)
            return {"vendor_decode": payload}
        if key_bytes in (bytes.fromhex("10050100"), bytes.fromhex("10050101")):
            payload = bytes(value_payload or b"")
            if payload:
                self.rgb_brightness = int(payload[0])
            return {"vendor_decode": {"payload_hex": ""}}
        if key_bytes == KEY_RGB_FRAME_READ:
            if isinstance(self.rgb_frame_payload_override, bytes):
                return {"vendor_decode": {"payload_hex": self.rgb_frame_payload_override.hex()}}
            r, g, b = self.rgb_color
            payload_hex = f"0400000000{r:02x}{g:02x}{b:02x}"
            return {"vendor_decode": {"payload_hex": payload_hex}}
        if key_bytes == KEY_RGB_MODE_READ:
            if not self.rgb_mode_read_enabled:
                raise CapabilityUnsupportedError("mode read unavailable")
            if isinstance(self.rgb_mode_read_payload_hex, str) and self.rgb_mode_read_payload_hex.strip():
                return {
                    "vendor_decode": {
                        "status": "success",
                        "status_code": 2,
                        "payload_hex": self.rgb_mode_read_payload_hex.strip().lower(),
                    }
                }
            return {"vendor_decode": {"payload_hex": f"{int(self.rgb_mode_selector) & 0xFF:02x}"}}
        if key_bytes == KEY_RGB_FRAME_WRITE:
            payload = bytes(value_payload or b"")
            if len(payload) >= 8:
                self.rgb_color = (int(payload[5]), int(payload[6]), int(payload[7]))
            return {"vendor_decode": {"payload_hex": ""}}
        if key_bytes == KEY_RGB_MODE_WRITE:
            payload = bytes(value_payload or b"")
            if len(payload) >= 1:
                self.rgb_mode_selector = int(payload[0])
            return {"vendor_decode": {"payload_hex": ""}}
        if len(key_bytes) == 4 and key_bytes[:3] == bytes.fromhex("080401"):
            slot = int(key_bytes[3])
            self.button_payloads[slot] = bytes(value_payload or b"")
            return {"vendor_decode": {"payload_hex": ""}}
        if len(key_bytes) == 4 and key_bytes[:3] == bytes.fromhex("088401"):
            slot = int(key_bytes[3])
            payload = self.button_payloads.get(
                slot,
                (
                    bytes([0x01, slot, 0x00, 0x06, 0x01, 0x06, 0x00, 0x00, 0x00, 0x00])
                    if slot == 0x60
                    else bytes([0x01, slot, 0x00, 0x01, 0x01, slot, 0x00, 0x00, 0x00, 0x00])
                ),
            )
            return {"vendor_decode": {"payload_hex": payload.hex()}}
        raise AssertionError(f"unexpected key {key_bytes.hex()}")


class MacOSBleBackendTest(unittest.TestCase):
    def setUp(self):
        self._env_backup = {
            "RAZECLI_BLE_POLL_READ_KEYS": os.environ.get("RAZECLI_BLE_POLL_READ_KEYS"),
            "RAZECLI_BLE_POLL_WRITE_KEYS": os.environ.get("RAZECLI_BLE_POLL_WRITE_KEYS"),
            "RAZECLI_BLE_POLL_CAP": os.environ.get("RAZECLI_BLE_POLL_CAP"),
            "RAZECLI_BLE_RGB_SKIP_MODE_READ": os.environ.get("RAZECLI_BLE_RGB_SKIP_MODE_READ"),
        }
        os.environ["RAZECLI_BLE_POLL_READ_KEYS"] = "deadbeef"
        os.environ["RAZECLI_BLE_POLL_WRITE_KEYS"] = "feedbeef"
        os.environ.pop("RAZECLI_BLE_POLL_CAP", None)
        os.environ.pop("RAZECLI_BLE_RGB_SKIP_MODE_READ", None)
        self.backend = MacOSBleBackend()
        self.backend._supported = True
        self.backend._rawhid = _FakeRawHid()
        self.backend._profiler = _FakeProfiler()
        # Keep tests deterministic and offline; battery GATT path is tested via explicit stubs.
        self.backend._read_standard_battery_level = lambda _device: None
        self.fake_vendor = _FakeVendorCall()
        self._orig_vendor_call = ble_probe_mod.ble_vendor_transceive
        self._orig_discover_vendor_path = ble_probe_mod.discover_vendor_gatt_path
        ble_probe_mod.ble_vendor_transceive = self.fake_vendor
        ble_probe_mod.discover_vendor_gatt_path = lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("discover_vendor_gatt_path should not be called in this test")
        )

    def tearDown(self):
        ble_probe_mod.ble_vendor_transceive = self._orig_vendor_call
        ble_probe_mod.discover_vendor_gatt_path = self._orig_discover_vendor_path
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_detect_filters_to_bt_pid_and_sets_ble_capabilities(self):
        devices = self.backend.detect()

        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device.product_id, 0x008E)
        self.assertEqual(device.backend, "macos-ble")
        self.assertEqual(device.serial, "02:11:22:33:44:55")
        self.assertIn("dpi", device.capabilities)
        self.assertIn("dpi-stages", device.capabilities)
        self.assertIn("battery", device.capabilities)
        self.assertNotIn("poll-rate", device.capabilities)

    def test_detect_does_not_expose_poll_rate_when_model_not_allowlisted(self):
        self.backend._poll_capability_enabled = True
        self.backend._ble_poll_supported_models = set()
        devices = self.backend.detect()
        self.assertEqual(len(devices), 1)
        self.assertNotIn("poll-rate", devices[0].capabilities)

    def test_get_dpi_and_set_dpi_uses_vendor_stage_table(self):
        device = self.backend.detect()[0]

        dpi = self.backend.get_dpi(device)
        self.assertEqual(dpi, (1000, 1000))

        self.backend.set_dpi(device, 1700, 1700)
        write_calls = [row for row in self.fake_vendor.calls if row["key_hex"].startswith("0b04")]
        self.assertEqual(len(write_calls), 4)
        for row in write_calls:
            write_payload = row["value_payload"]
            self.assertEqual(len(write_payload), 16)
            # Active stage id preserved from read (0x11)
            self.assertEqual(write_payload[0], 0x11)
            # Stage count remains 2
            self.assertEqual(write_payload[1], 2)
            # First stage updated to 1700 (0x06A4 -> A4 06 little-endian)
            self.assertEqual(write_payload[3], 0xA4)
            self.assertEqual(write_payload[4], 0x06)
            self.assertEqual(write_payload[5], 0xA4)
            self.assertEqual(write_payload[6], 0x06)

    def test_read_dpi_merges_keys_prefers_richest_stage_table(self):
        device = self.backend.detect()[0]
        self.fake_vendor.extra_dpi_read_payloads = {
            # Three stages @ 1000 / 1600 / 2400 (varstore-prefixed layout)
            "0b840101": "01010301e803e80300000240064006000303600960090003",
        }
        active, stages = self.backend.get_dpi_stages(device)
        self.assertEqual(active, 1)
        self.assertEqual(len(stages), 3)
        self.assertEqual(stages[0], (1000, 1000))
        self.assertEqual(stages[1], (1600, 1600))
        self.assertEqual(stages[2], (2400, 2400))
        self.fake_vendor.extra_dpi_read_payloads = None

    def test_read_dpi_merges_prefers_840300_when_it_has_more_stages(self):
        device = self.backend.detect()[0]
        self.fake_vendor.extra_dpi_read_payloads = {
            # Richer table on the 0x0300 read family (onboard bank B on some firmware).
            "0b840300": (
                "01010401e803e80300000240064006000003d007d007000004980898080003"
            ),
        }
        active, stages = self.backend.get_dpi_stages(device)
        self.assertEqual(active, 1)
        self.assertEqual(len(stages), 4)
        self.assertEqual(stages[3], (2200, 2200))
        self.fake_vendor.extra_dpi_read_payloads = None

    def test_set_dpi_stages_ble_expansion_resequences_stage_ids(self):
        device = self.backend.detect()[0]
        self.backend.get_dpi_stages(device)
        self.fake_vendor.calls.clear()

        five = [
            (800, 800),
            (1200, 1200),
            (1600, 1600),
            (2000, 2000),
            (2400, 2400),
        ]
        self.backend.set_dpi_stages(device, 1, five)
        write_calls = [row for row in self.fake_vendor.calls if row["key_hex"].startswith("0b04")]
        self.assertEqual(len(write_calls), 4)
        wp = write_calls[0]["value_payload"]
        for row in write_calls:
            self.assertEqual(row["value_payload"], wp)
        self.assertEqual(len(wp), 37)
        self.assertEqual(wp[1], 5)
        self.assertEqual(wp[0], 1)
        offset = 2
        for i in range(5):
            self.assertEqual(wp[offset], i + 1)
            offset += 7

    def test_parse_dpi_stages_with_varstore_prefixed_layout(self):
        # Layout: [varstore, active, count, stage blocks...]
        payload = bytes.fromhex("01110211e803e803000022400640060003")
        active, stages, stage_ids, marker = self.backend._parse_stages_payload(payload)
        self.assertEqual(active, 1)
        self.assertEqual(stages, [(1000, 1000), (1600, 1600)])
        self.assertEqual(stage_ids, [0x11, 0x22])
        self.assertEqual(marker, 0x03)

    def test_decode_payload_falls_back_to_read_rows(self):
        result = {
            "vendor_decode": {"payload_hex": "e6"},
            "reads": [
                {"char_uuid": PRIMARY_VENDOR_READ_CHAR_UUID, "value_hex": "01110211e803e803000022400640060003"},
            ],
        }
        payload = self.backend._decode_payload_bytes(result, key=KEY_DPI_STAGES_READ)
        self.assertEqual(payload, bytes.fromhex("01110211e803e803000022400640060003"))

    def test_decode_dpi_stages_prefers_full_table_over_compact_vendor_hex(self):
        result = {
            "vendor_decode": {"payload_hex": "1101dc05dc050003"},
            "reads": [
                {"char_uuid": PRIMARY_VENDOR_READ_CHAR_UUID, "value_hex": "01110211e803e803000022400640060003"},
            ],
        }
        payload = self.backend._decode_payload_bytes(result, key=KEY_DPI_STAGES_READ)
        self.assertEqual(payload, bytes.fromhex("01110211e803e803000022400640060003"))

    def test_decode_payload_falls_back_to_notify_rows(self):
        result = {
            "vendor_decode": {"payload_hex": ""},
            "vendor_request": {"request_id": 0x30},
            "notify": [
                # header row: req=0x30 payload_len=8 status=0x02
                {"char_uuid": PRIMARY_VENDOR_READ_CHAR_UUID, "value_hex": "3008000000000002"},
                # continuation row containing compact single-stage payload
                {"char_uuid": PRIMARY_VENDOR_READ_CHAR_UUID, "value_hex": "1101dc05dc050003"},
            ],
        }
        payload = self.backend._decode_payload_bytes(result, key=KEY_DPI_STAGES_READ)
        self.assertEqual(payload, bytes.fromhex("1101dc05dc050003"))

    def test_parse_compact_single_stage_payload(self):
        payload = bytes.fromhex("1101dc05dc050003")
        active, stages, stage_ids, marker = self.backend._parse_stages_payload(payload)
        self.assertEqual(active, 1)
        self.assertEqual(stages, [(1500, 1500)])
        self.assertEqual(stage_ids, [0x11])
        self.assertEqual(marker, 0x03)

    def test_parse_compact_single_stage_payload_varstore_8byte_layout(self):
        payload = bytes.fromhex("010101e803e80300")
        active, stages, stage_ids, marker = self.backend._parse_stages_payload(payload)
        self.assertEqual(active, 1)
        self.assertEqual(stages, [(1000, 1000)])
        self.assertEqual(stage_ids, [0x01])
        self.assertEqual(marker, 0x00)

    def test_read_row_fallback_ignores_non_primary_char(self):
        result = {
            "vendor_decode": {"payload_hex": ""},
            "reads": [
                {"char_uuid": "52401526-f97c-7f90-0e7f-6c6f4e36db1c", "value_hex": "1101dc05dc050003"},
            ],
        }
        payload = self.backend._decode_payload_bytes(result, key=KEY_DPI_STAGES_READ)
        self.assertEqual(payload, b"")

    def test_get_battery_scales_raw_level(self):
        device = self.backend.detect()[0]
        battery = self.backend.get_battery(device)
        self.assertEqual(battery, 90)
        self.assertEqual(self.fake_vendor.calls[-1]["address"], "02:11:22:33:44:55")

    def test_get_battery_prefers_standard_battery_service(self):
        device = self.backend.detect()[0]
        self.backend._read_standard_battery_level = lambda _device: 89
        battery = self.backend.get_battery(device)
        self.assertEqual(battery, 89)
        self.assertEqual(self.fake_vendor.calls, [])

    def test_get_battery_ignores_read_row_noise_for_raw_key(self):
        device = self.backend.detect()[0]

        def _battery_noise_vendor(
            *,
            address,
            name_query,
            timeout,
            key,
            value_payload=None,
            response_timeout=1.0,
            write_with_response=True,
            notify_enabled=True,
            service_uuid=None,
            write_char_uuid=None,
            read_char_uuids=None,
        ):
            _ = (
                address,
                name_query,
                timeout,
                value_payload,
                response_timeout,
                write_with_response,
                notify_enabled,
                service_uuid,
                write_char_uuid,
                read_char_uuids,
            )
            if bytes(key) == KEY_BATTERY_RAW_READ:
                return {
                    "vendor_decode": {"payload_hex": ""},
                    "reads": [
                        {
                            "char_uuid": PRIMARY_VENDOR_READ_CHAR_UUID,
                            "value_hex": "95f45a0ad87b5f9ef8a4199b",
                        }
                    ],
                }
            if bytes(key) == KEY_BATTERY_STATUS_READ:
                return {"vendor_decode": {"payload_hex": "59"}}
            raise AssertionError("unexpected key")

        ble_probe_mod.ble_vendor_transceive = _battery_noise_vendor
        battery = self.backend.get_battery(device)
        self.assertEqual(battery, 89)

    def test_poll_rate_is_unsupported_on_ble_backend(self):
        device = self.backend.detect()[0]
        with self.assertRaises(CapabilityUnsupportedError):
            self.backend.get_poll_rate(device)
        with self.assertRaises(CapabilityUnsupportedError):
            self.backend.set_poll_rate(device, 1000)

    def test_poll_rate_is_model_gated_without_allowlist(self):
        self.backend._poll_capability_enabled = True
        self.backend._ble_poll_supported_models = set()
        device = self.backend.detect()[0]
        with self.assertRaises(CapabilityUnsupportedError) as ctx:
            self.backend.get_poll_rate(device)
        self.assertIn("disabled for model 'deathadder-v2-pro'", str(ctx.exception))
        self.assertEqual(len(self.fake_vendor.calls), 0)

    def test_poll_rate_roundtrip_when_keys_match_device(self):
        os.environ["RAZECLI_BLE_POLL_READ_KEYS"] = "00850001"
        os.environ["RAZECLI_BLE_POLL_WRITE_KEYS"] = "00050001"
        self.backend._poll_capability_enabled = True
        self.backend._ble_poll_supported_models = {"deathadder-v2-pro"}
        device = self.backend.detect()[0]

        self.assertEqual(self.backend.get_poll_rate(device), 1000)
        self.backend.set_poll_rate(device, 500)
        self.assertEqual(self.backend.get_poll_rate(device), 500)

    def test_poll_rate_marks_bt_endpoint_unavailable_on_explicit_reject(self):
        os.environ["RAZECLI_BLE_POLL_READ_KEYS"] = "00850001"
        self.backend._poll_capability_enabled = True
        self.backend._ble_poll_supported_models = {"deathadder-v2-pro"}
        device = self.backend.detect()[0]
        self.assertIn("poll-rate", device.capabilities)

        calls = {"count": 0}

        def _reject_vendor(
            *,
            address,
            name_query,
            timeout,
            key,
            value_payload=None,
            response_timeout=1.0,
            write_with_response=True,
            notify_enabled=True,
            service_uuid=None,
            write_char_uuid=None,
            read_char_uuids=None,
        ):
            _ = (
                address,
                name_query,
                timeout,
                key,
                value_payload,
                response_timeout,
                write_with_response,
                notify_enabled,
                service_uuid,
                write_char_uuid,
                read_char_uuids,
            )
            calls["count"] += 1
            return {"vendor_decode": {"status": "error", "status_code": 3, "payload_hex": ""}}

        ble_probe_mod.ble_vendor_transceive = _reject_vendor

        with self.assertRaises(CapabilityUnsupportedError):
            self.backend.get_poll_rate(device)
        self.assertTrue(bool(device.backend_handle.get("ble_poll_unavailable", False)))
        self.assertNotIn("poll-rate", device.capabilities)
        first_calls = int(calls["count"])

        with self.assertRaises(CapabilityUnsupportedError):
            self.backend.get_poll_rate(device)
        self.assertEqual(int(calls["count"]), first_calls)

        refreshed = self.backend.detect()[0]
        self.assertNotIn("poll-rate", refreshed.capabilities)

    def test_decode_poll_rate_prefixed_payload(self):
        payload = bytes([0x01, 0x02])
        self.assertEqual(self.backend._decode_poll_rate_payload(payload), 500)

    def test_poll_rate_uses_cached_value_when_read_temporarily_fails(self):
        os.environ["RAZECLI_BLE_POLL_READ_KEYS"] = "deadbeef"
        self.backend._poll_capability_enabled = True
        self.backend._ble_poll_supported_models = {"deathadder-v2-pro"}
        device = self.backend.detect()[0]
        if isinstance(device.backend_handle, dict):
            device.backend_handle["ble_poll_rate_hz"] = 1000
        self.assertEqual(self.backend.get_poll_rate(device), 1000)

    def test_vendor_call_falls_back_to_discovered_gatt_path(self):
        device = self.backend.detect()[0]

        def _fallback_vendor(
            *,
            address,
            name_query,
            timeout,
            key,
            value_payload=None,
            response_timeout=1.0,
            write_with_response=True,
            notify_enabled=True,
            service_uuid=None,
            write_char_uuid=None,
            read_char_uuids=None,
        ):
            _ = (
                address,
                name_query,
                timeout,
                key,
                value_payload,
                response_timeout,
                write_with_response,
                notify_enabled,
                write_char_uuid,
                read_char_uuids,
            )
            if service_uuid == "99999999-9999-9999-9999-999999999999":
                return {"vendor_decode": {"payload_hex": "e6"}}
            raise RazeCliError("GATT service 52401523-f97c-7f90-0e7f-6c6f4e36db1c hittades inte på enheten")

        ble_probe_mod.ble_vendor_transceive = _fallback_vendor
        ble_probe_mod.discover_vendor_gatt_path = lambda **_kwargs: {
            "service_uuid": "99999999-9999-9999-9999-999999999999",
            "write_char_uuid": "99999998-9999-9999-9999-999999999999",
            "read_char_uuids": ["99999997-9999-9999-9999-999999999999"],
        }

        battery = self.backend.get_battery(device)
        self.assertEqual(battery, 90)
        self.assertEqual(
            device.backend_handle.get("ble_service_uuid"),
            "99999999-9999-9999-9999-999999999999",
        )

    def test_rgb_get_and_set_roundtrip(self):
        device = self.backend.detect()[0]

        before = self.backend.get_rgb(device)
        self.assertEqual(before["mode"], "static")
        self.assertEqual(before["color"], "00ff00")
        self.assertEqual(before["brightness"], 50)

        after = self.backend.set_rgb(
            device,
            mode="static",
            brightness=55,
            color="#11aa22",
        )
        self.assertEqual(after["mode"], "static")
        self.assertEqual(after["brightness"], 55)
        self.assertEqual(after["color"], "11aa22")

        current = self.backend.get_rgb(device)
        self.assertEqual(current["color"], "11aa22")
        self.assertEqual(current["brightness"], 55)

    def test_button_mapping_set_and_get_roundtrip(self):
        device = self.backend.detect()[0]
        state = self.backend.set_button_mapping(
            device,
            button="side_1",
            action="mouse:back",
        )
        self.assertEqual(state["mapping"]["side_1"], "mouse:back")
        self.assertEqual(state["read_confidence"]["overall"], "verified")

        current = self.backend.get_button_mapping(device)
        self.assertEqual(current["mapping"]["side_1"], "mouse:back")
        self.assertIn("left_click", current["mapping"])
        self.assertEqual(current["read_confidence"]["overall"], "verified")
        self.assertEqual(current["read_confidence"]["inferred_buttons"], [])

    def test_rgb_spectrum_mode_writes_mode_selector(self):
        device = self.backend.detect()[0]
        model = self.backend._model_registry.get("deathadder-v2-pro")
        self.assertIsNotNone(model)
        self.backend._model_registry._models["deathadder-v2-pro"] = replace(
            model,
            ble_supported_rgb_modes=("off", "static", "breathing", "spectrum"),
        )
        state = self.backend.set_rgb(device, mode="spectrum", brightness=60, color=None)
        self.assertEqual(state["mode"], "spectrum")
        self.assertEqual(self.fake_vendor.rgb_mode_selector, 0x08)

        mode_calls = [row for row in self.fake_vendor.calls if row["key_hex"] == KEY_RGB_MODE_WRITE.hex()]
        self.assertTrue(mode_calls)
        self.assertEqual(mode_calls[-1]["value_payload"], bytes.fromhex("08000000"))

    def test_rgb_breathing_mode_writes_mode_selector_and_color(self):
        device = self.backend.detect()[0]
        model = self.backend._model_registry.get("deathadder-v2-pro")
        self.assertIsNotNone(model)
        self.backend._model_registry._models["deathadder-v2-pro"] = replace(
            model,
            ble_supported_rgb_modes=("off", "static", "breathing", "spectrum"),
        )
        state = self.backend.set_rgb(device, mode="breathing", brightness=40, color="224466")
        self.assertEqual(state["mode"], "breathing")
        self.assertEqual(self.fake_vendor.rgb_mode_selector, 0x02)
        self.assertEqual(self.fake_vendor.rgb_color, (0x22, 0x44, 0x66))

    def test_get_rgb_uses_mode_selector_when_brightness_is_zero(self):
        device = self.backend.detect()[0]
        model = self.backend._model_registry.get("deathadder-v2-pro")
        self.assertIsNotNone(model)
        self.backend._model_registry._models["deathadder-v2-pro"] = replace(
            model,
            ble_supported_rgb_modes=("off", "static", "breathing", "spectrum"),
        )
        self.fake_vendor.rgb_brightness = 0x00
        self.fake_vendor.rgb_mode_selector = 0x02
        state = self.backend.get_rgb(device)
        self.assertEqual(state["brightness"], 0)
        self.assertEqual(state["mode"], "breathing")
        self.assertFalse(state["mode_inferred"])
        self.assertEqual(state["read_confidence"]["overall"], "verified")
        self.assertEqual(state["read_confidence"]["brightness"], "verified")
        self.assertEqual(state["read_confidence"]["mode"], "verified")
        self.assertEqual(state["read_confidence"]["mode_selector"], 0x02)

    def test_get_rgb_infers_cached_mode_when_mode_read_missing_and_brightness_zero(self):
        device = self.backend.detect()[0]
        self.fake_vendor.rgb_brightness = 0x00
        self.fake_vendor.rgb_mode_read_enabled = False
        if isinstance(device.backend_handle, dict):
            device.backend_handle["ble_rgb_mode"] = "breathing"
        state = self.backend.get_rgb(device)
        self.assertEqual(state["brightness"], 0)
        self.assertEqual(state["mode"], "breathing")
        self.assertTrue(state["mode_inferred"])
        self.assertEqual(state["read_confidence"]["mode"], "inferred")

    def test_get_rgb_ignores_unrecognized_frame_payload_and_keeps_cached_color(self):
        device = self.backend.detect()[0]
        if isinstance(device.backend_handle, dict):
            device.backend_handle["ble_rgb_color"] = "11aa22"
        self.fake_vendor.rgb_frame_payload_override = bytes.fromhex("01000560")
        state = self.backend.get_rgb(device)
        self.assertEqual(state["color"], "11aa22")

    def test_get_rgb_ignores_error_brightness_payload(self):
        device = self.backend.detect()[0]
        self.fake_vendor.rgb_brightness = 0x04
        self.fake_vendor.rgb_brightness_status = "error"
        self.fake_vendor.rgb_brightness_status_code = 3
        state = self.backend.get_rgb(device)
        self.assertEqual(state["brightness"], 100)
        self.assertEqual(state["read_confidence"]["brightness"], "inferred-default")

    def test_get_rgb_skips_mode_read_when_skip_env_set(self):
        os.environ["RAZECLI_BLE_RGB_SKIP_MODE_READ"] = "1"
        self.fake_vendor.calls.clear()
        try:
            device = self.backend.detect()[0]
            self.backend.get_rgb(device)
            mode_reads = [c for c in self.fake_vendor.calls if c["key_hex"] == KEY_RGB_MODE_READ.hex()]
            self.assertEqual(len(mode_reads), 0)
        finally:
            os.environ.pop("RAZECLI_BLE_RGB_SKIP_MODE_READ", None)

    def test_get_rgb_detects_spectrum_from_1083_zone_payload(self):
        device = self.backend.detect()[0]
        self.fake_vendor.rgb_mode_read_payload_hex = "01000004aabbcc000000"
        try:
            state = self.backend.get_rgb(device)
            self.assertEqual(state["mode"], "spectrum")
            self.assertEqual(state["color"], "aabbcc")
            self.assertFalse(state["mode_inferred"])
            self.assertEqual(state["read_confidence"]["mode"], "verified")
        finally:
            self.fake_vendor.rgb_mode_read_payload_hex = None

    def test_rgb_spectrum_write_when_supported_by_model(self):
        device = self.backend.detect()[0]
        state = self.backend.set_rgb(device, mode="spectrum", brightness=60, color=None)
        self.assertEqual(state["mode"], "spectrum")
        self.assertEqual(self.fake_vendor.rgb_mode_selector, 0x08)

    def test_button_mapping_supports_scroll_and_keyboard_actions(self):
        device = self.backend.detect()[0]
        self.backend.set_button_mapping(device, button="side_2", action="mouse:scroll-down")
        self.backend.set_button_mapping(device, button="side_1", action="keyboard:0x2c")

        current = self.backend.get_button_mapping(device)
        self.assertEqual(current["mapping"]["side_2"], "mouse:scroll-down")
        self.assertEqual(current["mapping"]["side_1"], "keyboard:0x2c")

    def test_button_mapping_decodes_compact_16byte_ble_layout(self):
        device = self.backend.detect()[0]
        # Compact layout observed in live BLE probes:
        # [slot, profile?, 0x01, 0x01, 0x01, 0x01, action_id, slot, ...]
        self.fake_vendor.button_payloads[0x04] = bytes.fromhex("04000101010104040000000000000000")
        self.fake_vendor.button_payloads[0x05] = bytes.fromhex("05000101010105050000000000000000")
        self.fake_vendor.button_payloads[0x60] = bytes.fromhex("60000101010100000000000000000000")

        current = self.backend.get_button_mapping(device)
        self.assertEqual(current["mapping"]["side_1"], "mouse:back")
        self.assertEqual(current["mapping"]["side_2"], "mouse:forward")
        self.assertEqual(current["mapping"]["dpi_cycle"], "disabled")
        self.assertEqual(current["read_confidence"]["overall"], "verified")


if __name__ == "__main__":
    unittest.main()
