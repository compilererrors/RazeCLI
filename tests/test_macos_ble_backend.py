import unittest
import os

import razecli.backends.macos_ble_backend as macos_ble_backend_mod
import razecli.ble_probe as ble_probe_mod
from razecli.backends.macos_ble_backend import (
    KEY_BATTERY_RAW_READ,
    KEY_BATTERY_STATUS_READ,
    KEY_DPI_STAGES_READ,
    KEY_DPI_STAGES_WRITE,
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
                capabilities=set(),
                backend_handle={"source": "test"},
            )
        ]


class _FakeVendorCall:
    def __init__(self):
        self.calls = []
        self.poll_code = 0x01

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
        if key_bytes == KEY_DPI_STAGES_WRITE:
            return {"vendor_decode": {"payload_hex": ""}}
        if key_bytes == bytes.fromhex("00850001"):
            return {"vendor_decode": {"payload_hex": f"{self.poll_code:02x}"}}
        if key_bytes == bytes.fromhex("00050001"):
            payload = bytes(value_payload or b"")
            if payload:
                self.poll_code = int(payload[0])
            return {"vendor_decode": {"payload_hex": ""}}
        raise AssertionError(f"unexpected key {key_bytes.hex()}")


class MacOSBleBackendTest(unittest.TestCase):
    def setUp(self):
        self._env_backup = {
            "RAZECLI_BLE_POLL_READ_KEYS": os.environ.get("RAZECLI_BLE_POLL_READ_KEYS"),
            "RAZECLI_BLE_POLL_WRITE_KEYS": os.environ.get("RAZECLI_BLE_POLL_WRITE_KEYS"),
            "RAZECLI_BLE_POLL_CAP": os.environ.get("RAZECLI_BLE_POLL_CAP"),
        }
        os.environ["RAZECLI_BLE_POLL_READ_KEYS"] = "deadbeef"
        os.environ["RAZECLI_BLE_POLL_WRITE_KEYS"] = "feedbeef"
        os.environ.pop("RAZECLI_BLE_POLL_CAP", None)
        self.backend = MacOSBleBackend()
        self.backend._supported = True
        self.backend._rawhid = _FakeRawHid()
        self.backend._profiler = _FakeProfiler()
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

    def test_get_dpi_and_set_dpi_uses_vendor_stage_table(self):
        device = self.backend.detect()[0]

        dpi = self.backend.get_dpi(device)
        self.assertEqual(dpi, (1000, 1000))

        self.backend.set_dpi(device, 1700, 1700)
        write_calls = [row for row in self.fake_vendor.calls if row["key_hex"] == KEY_DPI_STAGES_WRITE.hex()]
        self.assertEqual(len(write_calls), 1)
        write_payload = write_calls[0]["value_payload"]
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

    def test_poll_rate_roundtrip_when_keys_match_device(self):
        os.environ["RAZECLI_BLE_POLL_READ_KEYS"] = "00850001"
        os.environ["RAZECLI_BLE_POLL_WRITE_KEYS"] = "00050001"
        device = self.backend.detect()[0]

        self.assertEqual(self.backend.get_poll_rate(device), 1000)
        self.backend.set_poll_rate(device, 500)
        self.assertEqual(self.backend.get_poll_rate(device), 500)

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


if __name__ == "__main__":
    unittest.main()
