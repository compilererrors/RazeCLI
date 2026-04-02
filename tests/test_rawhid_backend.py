import unittest
from types import SimpleNamespace

from razecli.backends.rawhid_backend import (
    PID_PROFILES,
    RazerCommand,
    RawHidBackend,
    _build_report,
    _extract_response_fields,
    _normalize_feature_response,
)


class RawHidReportTest(unittest.TestCase):
    def test_build_report_layout_and_crc(self):
        command = RazerCommand(command_class=0x00, command_id=0x85, data_size=0x01)
        report = _build_report(command, transaction_id=0x3F)

        self.assertEqual(len(report), 90)
        self.assertEqual(report[0], 0x00)
        self.assertEqual(report[1], 0x3F)
        self.assertEqual(report[5], 0x01)
        self.assertEqual(report[6], 0x00)
        self.assertEqual(report[7], 0x85)
        self.assertEqual(report[89], 0x00)

        expected_crc = 0
        for index in range(2, 88):
            expected_crc ^= report[index]
        self.assertEqual(report[88], expected_crc)

    def test_normalize_feature_response(self):
        payload_90 = bytes(range(90))
        payload_91 = bytes([0x00]) + payload_90

        self.assertEqual(_normalize_feature_response(payload_90), payload_90)
        self.assertEqual(_normalize_feature_response(payload_91), payload_90)

    def test_extract_response_fields(self):
        report = bytearray(90)
        report[0] = 0x02
        report[1] = 0x3F
        report[5] = 0x01
        report[6] = 0x00
        report[7] = 0x85
        report[8] = 0x01

        fields = _extract_response_fields(bytes(report))
        self.assertEqual(fields["status"], 0x02)
        self.assertEqual(fields["transaction_id"], 0x3F)
        self.assertEqual(fields["command_class"], 0x00)
        self.assertEqual(fields["command_id"], 0x85)
        self.assertEqual(fields["arguments"][0], 0x01)


class RawHidMappingTest(unittest.TestCase):
    def test_pid_profiles_include_extended_models(self):
        self.assertIn(0x007C, PID_PROFILES)
        self.assertIn(0x008E, PID_PROFILES)
        self.assertIn(0x0084, PID_PROFILES)
        self.assertIn(0x008C, PID_PROFILES)
        self.assertIn(0x0083, PID_PROFILES)

        self.assertIn("dpi", PID_PROFILES[0x0084].capabilities)
        self.assertNotIn("battery", PID_PROFILES[0x0084].capabilities)
        self.assertIn("dpi-stages", PID_PROFILES[0x008C].capabilities)

    def test_poll_mapping(self):
        backend = RawHidBackend()
        command = backend._poll_set_command(1000)
        self.assertEqual(command.arguments[0], 0x01)

        command = backend._poll_set_command(500)
        self.assertEqual(command.arguments[0], 0x02)

        command = backend._poll_set_command(125)
        self.assertEqual(command.arguments[0], 0x08)

    def test_dpi_stages_set_command(self):
        backend = RawHidBackend()
        command = backend._dpi_stages_set_command(
            2,
            [(800, 800), (1600, 1600), (3200, 3200)],
        )

        self.assertEqual(command.command_class, 0x04)
        self.assertEqual(command.command_id, 0x06)
        self.assertEqual(command.data_size, 0x26)

        # Header fields
        self.assertEqual(command.arguments[0], 0x01)  # varstore
        self.assertEqual(command.arguments[1], 2)  # active stage
        self.assertEqual(command.arguments[2], 3)  # count

        # Stage 1 values start at arg[3]
        self.assertEqual(command.arguments[3], 0)  # stage number
        self.assertEqual(command.arguments[4], 0x03)
        self.assertEqual(command.arguments[5], 0x20)
        self.assertEqual(command.arguments[6], 0x03)
        self.assertEqual(command.arguments[7], 0x20)

    def test_get_dpi_stages_parse(self):
        backend = RawHidBackend()

        def _fake_transceive(_device, _command):
            report = bytearray(90)
            report[0] = 0x02
            report[1] = 0x3F
            report[5] = 0x26
            report[6] = 0x04
            report[7] = 0x86

            # varstore / active stage / count
            report[8] = 0x01
            report[9] = 0x02
            report[10] = 0x03

            # stage 1 block
            report[11] = 0x01
            report[12] = 0x03
            report[13] = 0x20
            report[14] = 0x03
            report[15] = 0x20

            # stage 2 block
            report[18] = 0x02
            report[19] = 0x06
            report[20] = 0x40
            report[21] = 0x06
            report[22] = 0x40

            # stage 3 block
            report[25] = 0x03
            report[26] = 0x0C
            report[27] = 0x80
            report[28] = 0x0C
            report[29] = 0x80

            return _extract_response_fields(bytes(report))

        backend._transceive = _fake_transceive  # type: ignore[method-assign]
        device = SimpleNamespace(
            capabilities={"dpi-stages"},
            product_id=0x007C,
            usb_id=lambda: "1532:007C",
        )
        active_stage, stages = backend.get_dpi_stages(device=device)  # type: ignore[arg-type]
        self.assertEqual(active_stage, 2)
        self.assertEqual(stages, [(800, 800), (1600, 1600), (3200, 3200)])

    def test_detect_identifier_stable_for_macos_devsrvs_paths(self):
        class _FakeHid:
            def __init__(self):
                self.calls = 0

            def enumerate(self, _vendor_id, _product_id):
                self.calls += 1
                suffix = 1000 + self.calls
                return [
                    {
                        "vendor_id": 0x1532,
                        "product_id": 0x008E,
                        "path": f"DevSrvsID:{suffix}",
                        "product_string": "DA V2 Pro",
                        "serial_number": None,
                        "interface_number": 0,
                        "usage_page": 1,
                        "usage": 2,
                    }
                ]

        backend = RawHidBackend()
        backend._hid = _FakeHid()

        first = backend.detect()
        second = backend.detect()

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(first[0].identifier, "rawhid:1532:008E")
        self.assertEqual(second[0].identifier, "rawhid:1532:008E")


if __name__ == "__main__":
    unittest.main()
