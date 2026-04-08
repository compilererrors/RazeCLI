import os
import unittest
from types import SimpleNamespace

from razecli.backends.rawhid_backend import (
    PID_PROFILES,
    RazerCommand,
    RawHidBackend,
    _LED_BACKLIGHT,
    _build_report,
    _extract_response_fields,
    _normalize_feature_response,
)
from razecli.errors import CapabilityUnsupportedError


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

    def test_with_varstore_rewrites_first_argument(self):
        backend = RawHidBackend()
        command = RazerCommand(command_class=0x03, command_id=0x81, data_size=0x05, arguments=(0x01, 0x05))
        rewritten = backend._with_varstore(command, 0x00)
        self.assertEqual(rewritten.command_class, 0x03)
        self.assertEqual(rewritten.command_id, 0x81)
        self.assertEqual(rewritten.arguments[0], 0x00)
        self.assertEqual(rewritten.arguments[1], 0x05)

    def test_rgb_varstores_from_env(self):
        backend = RawHidBackend()
        previous = os.environ.get("RAZECLI_RAWHID_RGB_VARSTORES")
        try:
            os.environ["RAZECLI_RAWHID_RGB_VARSTORES"] = "0x01,0x00,0x01"
            self.assertEqual(backend._rgb_varstores(), (0x01, 0x00))
        finally:
            if previous is None:
                os.environ.pop("RAZECLI_RAWHID_RGB_VARSTORES", None)
            else:
                os.environ["RAZECLI_RAWHID_RGB_VARSTORES"] = previous

    def test_chroma_extended_matrix_static_command_layout(self):
        backend = RawHidBackend()
        cmd = backend._chroma_extended_matrix_static_command(_LED_BACKLIGHT, 0x00, 0xFF, 0x00)
        self.assertEqual(cmd.command_class, 0x0F)
        self.assertEqual(cmd.command_id, 0x02)
        self.assertEqual(cmd.data_size, 0x09)
        self.assertEqual(cmd.arguments[1], _LED_BACKLIGHT)
        self.assertEqual(cmd.arguments[6], 0x00)
        self.assertEqual(cmd.arguments[7], 0xFF)
        self.assertEqual(cmd.arguments[8], 0x00)

    def test_chroma_mouse_extended_static_command_layout(self):
        backend = RawHidBackend()
        cmd = backend._chroma_mouse_extended_static_command(_LED_BACKLIGHT, 0x00, 0xFF, 0x00)
        self.assertEqual(cmd.command_class, 0x03)
        self.assertEqual(cmd.command_id, 0x0D)
        self.assertEqual(cmd.data_size, 0x09)
        self.assertEqual(cmd.arguments[1], _LED_BACKLIGHT)
        self.assertEqual(cmd.arguments[6], 0x00)
        self.assertEqual(cmd.arguments[7], 0xFF)
        self.assertEqual(cmd.arguments[8], 0x00)

    def test_chroma_mouse_standard_static_command_layout(self):
        backend = RawHidBackend()
        cmd = backend._chroma_mouse_standard_static_command(_LED_BACKLIGHT, 0x00, 0xFF, 0x00)
        self.assertEqual(cmd.command_class, 0x03)
        self.assertEqual(cmd.command_id, 0x0A)
        self.assertEqual(cmd.data_size, 0x09)
        self.assertEqual(cmd.arguments[1], _LED_BACKLIGHT)
        self.assertEqual(cmd.arguments[6], 0x00)
        self.assertEqual(cmd.arguments[7], 0xFF)
        self.assertEqual(cmd.arguments[8], 0x00)

    def test_set_rgb_falls_back_to_mouse_extended_protocol(self):
        backend = RawHidBackend()
        calls = []

        def _fake_transceive(_device, command):
            calls.append((int(command.command_class), int(command.command_id)))
            if int(command.command_class) == 0x0F:
                raise RuntimeError("extended-matrix unsupported")
            return {"arguments": bytes(80)}

        backend._transceive = _fake_transceive  # type: ignore[method-assign]
        device = SimpleNamespace(
            capabilities={"rgb"},
            product_id=0x0084,
            usb_id=lambda: "1532:0084",
        )

        result = backend.set_rgb(device=device, mode="static", brightness=100, color="00ff00")  # type: ignore[arg-type]
        self.assertEqual(result["hardware_apply"], "applied")
        self.assertIn("mouse-extended", result["write_protocols"])
        self.assertTrue(any(cls == 0x03 and cid == 0x0D for cls, cid in calls))

    def test_set_rgb_static_forces_led_state_and_full_brightness(self):
        backend = RawHidBackend()
        calls = []

        def _fake_transceive(_device, command):
            calls.append(
                (
                    int(command.command_class),
                    int(command.command_id),
                    tuple(int(value) for value in command.arguments),
                )
            )
            return {"arguments": bytes(80)}

        def _fake_get_rgb(_device):
            return {
                "mode": "static",
                "brightness": 100,
                "color": "00ff00",
                "read_confidence": {"overall": "verified"},
            }

        backend._transceive = _fake_transceive  # type: ignore[method-assign]
        backend.get_rgb = _fake_get_rgb  # type: ignore[method-assign]
        device = SimpleNamespace(
            capabilities={"rgb"},
            product_id=0x0084,
            usb_id=lambda: "1532:0084",
        )

        result = backend.set_rgb(device=device, mode="static", brightness=100, color="00ff00")  # type: ignore[arg-type]
        self.assertEqual(result["hardware_apply"], "applied")
        self.assertTrue(any(cls == 0x03 and cid == 0x00 and len(args) >= 3 and args[2] == 0x01 for cls, cid, args in calls))
        self.assertTrue(any(cls == 0x03 and cid == 0x03 and len(args) >= 3 and args[2] == 0xFF for cls, cid, args in calls))

    def test_get_rgb_reports_inferred_when_reads_fail(self):
        backend = RawHidBackend()

        def _fake_transceive(_device, _command):
            raise RuntimeError("read not available")

        backend._transceive = _fake_transceive  # type: ignore[method-assign]
        device = SimpleNamespace(
            capabilities={"rgb"},
            product_id=0x0084,
            usb_id=lambda: "1532:0084",
        )

        payload = backend.get_rgb(device=device)  # type: ignore[arg-type]
        confidence = payload.get("read_confidence", {})
        self.assertEqual(confidence.get("overall"), "inferred")
        self.assertEqual(payload.get("color"), "00ff00")

    def test_set_rgb_raises_when_verification_is_inferred(self):
        backend = RawHidBackend()

        def _fake_transceive(_device, _command):
            return {"arguments": bytes(80)}

        def _fake_get_rgb(_device):
            return {
                "mode": "static",
                "brightness": 100,
                "color": "00ff00",
                "read_confidence": {"overall": "inferred"},
            }

        backend._transceive = _fake_transceive  # type: ignore[method-assign]
        backend.get_rgb = _fake_get_rgb  # type: ignore[method-assign]
        device = SimpleNamespace(
            capabilities={"rgb"},
            product_id=0x0084,
            usb_id=lambda: "1532:0084",
        )

        with self.assertRaises(CapabilityUnsupportedError):
            backend.set_rgb(device=device, mode="static", brightness=100, color="00ff00")  # type: ignore[arg-type]


class RawHidMappingTest(unittest.TestCase):
    def test_pid_profiles_include_extended_models(self):
        self.assertIn(0x007C, PID_PROFILES)
        self.assertIn(0x008E, PID_PROFILES)
        self.assertIn(0x0084, PID_PROFILES)
        self.assertIn(0x008C, PID_PROFILES)
        self.assertIn(0x0083, PID_PROFILES)
        self.assertIn(0x0099, PID_PROFILES)
        self.assertIn(0x00AA, PID_PROFILES)
        self.assertIn(0x00B9, PID_PROFILES)
        self.assertIn(0x005C, PID_PROFILES)
        self.assertIn(0x00B6, PID_PROFILES)
        self.assertIn(0x00C0, PID_PROFILES)

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
