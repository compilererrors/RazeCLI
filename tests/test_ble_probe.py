import unittest
import asyncio
import os
import tempfile

import razecli.ble.alias as ble_alias_mod
from razecli.ble_probe import (
    _remember_alias,
    _chunk_bytes,
    _normalize_vendor_key,
    _parse_razer_vendor_notify,
    _resolve_device_async,
    _select_vendor_gatt_path,
    ble_alias_clear,
    ble_alias_list,
    ble_alias_resolve,
    parse_hex_payload,
)
from razecli.errors import RazeCliError


class BleProbeTest(unittest.TestCase):
    def test_parse_hex_payload_compact(self):
        self.assertEqual(parse_hex_payload("00ff01"), bytes.fromhex("00ff01"))

    def test_parse_hex_payload_with_separators(self):
        self.assertEqual(parse_hex_payload("0x00 ff:01-02_03"), bytes.fromhex("00ff010203"))

    def test_parse_hex_payload_rejects_empty(self):
        with self.assertRaises(RazeCliError):
            parse_hex_payload("   ")

    def test_parse_hex_payload_rejects_odd_length(self):
        with self.assertRaises(RazeCliError):
            parse_hex_payload("abc")

    def test_parse_hex_payload_rejects_invalid_chars(self):
        with self.assertRaises(RazeCliError):
            parse_hex_payload("00gg11")

    def test_parse_razer_vendor_notify_8byte_header_with_continuation(self):
        decoded = _parse_razer_vendor_notify(
            request_payload=bytes.fromhex("30 00 00 00 05 81 00 01"),
            notify_rows=[
                {"value_hex": "3001000000000002"},
                {"value_hex": "55"},
            ],
        )
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["req_id"], 0x30)
        self.assertEqual(decoded["status_code"], 0x02)
        self.assertEqual(decoded["key_hex"], "05810001")
        self.assertEqual(decoded["key_label"], "battery-raw-read")
        self.assertEqual(decoded["payload_hex"], "55")

    def test_parse_razer_vendor_notify_20byte_fallback_payload(self):
        decoded = _parse_razer_vendor_notify(
            request_payload=bytes.fromhex("31 00 00 00 05 80 00 01"),
            notify_rows=[
                {"value_hex": "3101000000000002990000000000000000000000"},
            ],
        )
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["header_len"], 20)
        self.assertEqual(decoded["payload_hex"], "99")

    def test_resolve_device_keeps_mac_as_mac(self):
        mac = "02:11:22:33:44:55"
        resolved = asyncio.run(
            _resolve_device_async(
                address=mac,
                name_query=None,
                timeout=1.0,
            )
        )
        self.assertEqual(resolved, mac)

    def test_normalize_vendor_key(self):
        self.assertEqual(_normalize_vendor_key("05 81 00 01"), bytes.fromhex("05810001"))
        self.assertEqual(_normalize_vendor_key(bytes.fromhex("0b840100")), bytes.fromhex("0b840100"))
        with self.assertRaises(RazeCliError):
            _normalize_vendor_key("05 81 00")

    def test_chunk_bytes(self):
        chunks = _chunk_bytes(bytes.fromhex("001122334455"), 2)
        self.assertEqual(chunks, [bytes.fromhex("0011"), bytes.fromhex("2233"), bytes.fromhex("4455")])
        self.assertEqual(_chunk_bytes(b"", 20), [])

    def test_ble_alias_list_and_clear(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            alias_path = os.path.join(temp_dir, "ble_aliases.json")
            previous = os.environ.get("RAZECLI_BLE_ALIAS_PATH")
            os.environ["RAZECLI_BLE_ALIAS_PATH"] = alias_path
            try:
                _remember_alias("02:11:22:33:44:55", "11111111-2222-3333-4444-555555555555")
                listed = ble_alias_list()
                self.assertEqual(listed["count"], 1)
                self.assertEqual(listed["aliases"][0]["mac_address"], "02:11:22:33:44:55")
                self.assertEqual(
                    listed["aliases"][0]["resolved_address"],
                    "11111111-2222-3333-4444-555555555555",
                )

                cleared_one = ble_alias_clear(mac_address="02:11:22:33:44:55")
                self.assertEqual(cleared_one["removed"], 1)
                self.assertEqual(cleared_one["remaining"], 0)

                _remember_alias("AA:BB:CC:DD:EE:FF", "11111111-2222-3333-4444-555555555555")
                cleared_all = ble_alias_clear()
                self.assertEqual(cleared_all["removed"], 1)
                self.assertEqual(cleared_all["remaining"], 0)
                self.assertTrue(cleared_all["cleared_all"])
            finally:
                if previous is None:
                    os.environ.pop("RAZECLI_BLE_ALIAS_PATH", None)
                else:
                    os.environ["RAZECLI_BLE_ALIAS_PATH"] = previous

    def test_select_vendor_gatt_path_prefers_razer_default_shape(self):
        services = [
            {
                "uuid": "0000180f-0000-1000-8000-00805f9b34fb",
                "characteristics": [
                    {"uuid": "00002a19-0000-1000-8000-00805f9b34fb", "properties": ["read", "notify"]}
                ],
            },
            {
                "uuid": "52401523-f97c-7f90-0e7f-6c6f4e36db1c",
                "characteristics": [
                    {"uuid": "52401524-f97c-7f90-0e7f-6c6f4e36db1c", "properties": ["write"]},
                    {"uuid": "52401525-f97c-7f90-0e7f-6c6f4e36db1c", "properties": ["read", "notify"]},
                    {"uuid": "52401526-f97c-7f90-0e7f-6c6f4e36db1c", "properties": ["read", "notify"]},
                ],
            },
        ]
        picked = _select_vendor_gatt_path(services)
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked["service_uuid"], "52401523-f97c-7f90-0e7f-6c6f4e36db1c")
        self.assertEqual(picked["write_char_uuid"], "52401524-f97c-7f90-0e7f-6c6f4e36db1c")

    def test_ble_alias_resolve_refreshes_cache(self):
        async def _fake_resolve(**_kwargs):
            return {
                "requested_mac": "02:11:22:33:44:55",
                "resolved_address": "11111111-2222-3333-4444-555555555555",
                "candidates": [],
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            alias_path = os.path.join(temp_dir, "ble_aliases.json")
            previous_path = os.environ.get("RAZECLI_BLE_ALIAS_PATH")
            original_resolve = ble_alias_mod._auto_resolve_corebluetooth_address
            os.environ["RAZECLI_BLE_ALIAS_PATH"] = alias_path
            ble_alias_mod._auto_resolve_corebluetooth_address = _fake_resolve
            try:
                payload = ble_alias_resolve(mac_address="02:11:22:33:44:55", timeout=1.0)
                self.assertEqual(payload["resolved_address"], "11111111-2222-3333-4444-555555555555")
                listed = ble_alias_list()
                self.assertEqual(listed["count"], 1)
            finally:
                ble_alias_mod._auto_resolve_corebluetooth_address = original_resolve
                if previous_path is None:
                    os.environ.pop("RAZECLI_BLE_ALIAS_PATH", None)
                else:
                    os.environ["RAZECLI_BLE_ALIAS_PATH"] = previous_path


if __name__ == "__main__":
    unittest.main()
