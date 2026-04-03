import unittest
import subprocess
import sys

from razecli.cli import DEFAULT_MODEL, build_parser


class CliParserTest(unittest.TestCase):
    def test_tui_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["tui"])

        self.assertEqual(args.command, "tui")
        self.assertEqual(args.model, DEFAULT_MODEL)
        self.assertFalse(args.all_models)
        self.assertIsNone(args.device)

    def test_tui_all_models_flag(self):
        parser = build_parser()
        args = parser.parse_args(["tui", "--all-models"])

        self.assertEqual(args.command, "tui")
        self.assertTrue(args.all_models)

    def test_global_backend_rawhid(self):
        parser = build_parser()
        args = parser.parse_args(["--backend", "rawhid", "devices"])
        self.assertEqual(args.backend, "rawhid")
        self.assertEqual(args.command, "devices")

    def test_global_backend_macos_ble(self):
        parser = build_parser()
        args = parser.parse_args(["--backend", "macos-ble", "devices"])
        self.assertEqual(args.backend, "macos-ble")
        self.assertEqual(args.command, "devices")

    def test_dpi_stages_set_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "dpi-stages",
                "set",
                "--active",
                "2",
                "--stage",
                "800:800",
                "--stage",
                "1600:1600",
            ]
        )
        self.assertEqual(args.command, "dpi-stages")
        self.assertEqual(args.dpi_stages_command, "set")
        self.assertEqual(args.active, 2)
        self.assertEqual(args.stage, ["800:800", "1600:1600"])

    def test_quick_set_parse(self):
        parser = build_parser()
        args = parser.parse_args(["set", "1500", "--model", "deathadder-v2-pro"])
        self.assertEqual(args.command, "set")
        self.assertEqual(args.dpi, 1500)
        self.assertEqual(args.model, "deathadder-v2-pro")

    def test_dpi_stages_preset_load_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "dpi-stages",
                "preset",
                "load",
                "--name",
                "fps3",
                "--active",
                "1",
                "--force",
            ]
        )
        self.assertEqual(args.command, "dpi-stages")
        self.assertEqual(args.dpi_stages_command, "preset")
        self.assertEqual(args.dpi_stages_preset_command, "load")
        self.assertEqual(args.name, "fps3")
        self.assertEqual(args.active, 1)
        self.assertTrue(args.force)

    def test_ble_scan_parse(self):
        parser = build_parser()
        args = parser.parse_args(["ble", "scan", "--timeout", "5", "--name", "Razer"])
        self.assertEqual(args.command, "ble")
        self.assertEqual(args.ble_command, "scan")
        self.assertEqual(args.timeout, 5.0)
        self.assertEqual(args.name, "Razer")

    def test_ble_services_parse(self):
        parser = build_parser()
        args = parser.parse_args(["ble", "services", "--address", "02:11:22:33:44:55", "--read"])
        self.assertEqual(args.command, "ble")
        self.assertEqual(args.ble_command, "services")
        self.assertEqual(args.address, "02:11:22:33:44:55")
        self.assertTrue(args.read)

    def test_ble_raw_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "ble",
                "raw",
                "--name",
                "DA V2 Pro",
                "--payload",
                "00 ff 01",
                "--write-char",
                "52401524-f97c-7f90-0e7f-6c6f4e36db1c",
                "--read-char",
                "52401525-f97c-7f90-0e7f-6c6f4e36db1c",
                "--read-char",
                "52401526-f97c-7f90-0e7f-6c6f4e36db1c",
                "--response-timeout",
                "2.0",
                "--no-response",
            ]
        )
        self.assertEqual(args.command, "ble")
        self.assertEqual(args.ble_command, "raw")
        self.assertEqual(args.name, "DA V2 Pro")
        self.assertEqual(args.payload, "00 ff 01")
        self.assertEqual(
            args.read_char,
            [
                "52401525-f97c-7f90-0e7f-6c6f4e36db1c",
                "52401526-f97c-7f90-0e7f-6c6f4e36db1c",
            ],
        )
        self.assertEqual(args.response_timeout, 2.0)
        self.assertTrue(args.no_response)

    def test_ble_poll_probe_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "ble",
                "poll-probe",
                "--address",
                "02:11:22:33:44:55",
                "--attempts",
                "2",
                "--key",
                "00850001",
                "--key",
                "0b850100",
            ]
        )
        self.assertEqual(args.command, "ble")
        self.assertEqual(args.ble_command, "poll-probe")
        self.assertEqual(args.address, "02:11:22:33:44:55")
        self.assertEqual(args.attempts, 2)
        self.assertEqual(args.key, ["00850001", "0b850100"])

    def test_ble_alias_list_parse(self):
        parser = build_parser()
        args = parser.parse_args(["ble", "alias", "list"])
        self.assertEqual(args.command, "ble")
        self.assertEqual(args.ble_command, "alias")
        self.assertEqual(args.ble_alias_command, "list")

    def test_ble_alias_clear_parse(self):
        parser = build_parser()
        args = parser.parse_args(["ble", "alias", "clear", "--address", "02:11:22:33:44:55"])
        self.assertEqual(args.command, "ble")
        self.assertEqual(args.ble_command, "alias")
        self.assertEqual(args.ble_alias_command, "clear")
        self.assertEqual(args.address, "02:11:22:33:44:55")

    def test_ble_alias_resolve_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            ["ble", "alias", "resolve", "--address", "02:11:22:33:44:55", "--timeout", "12"]
        )
        self.assertEqual(args.command, "ble")
        self.assertEqual(args.ble_command, "alias")
        self.assertEqual(args.ble_alias_command, "resolve")
        self.assertEqual(args.address, "02:11:22:33:44:55")
        self.assertEqual(args.timeout, 12.0)

    def test_rgb_set_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "rgb",
                "set",
                "--mode",
                "static",
                "--brightness",
                "40",
                "--color",
                "#33aa11",
            ]
        )
        self.assertEqual(args.command, "rgb")
        self.assertEqual(args.rgb_command, "set")
        self.assertEqual(args.mode, "static")
        self.assertEqual(args.brightness, 40)
        self.assertEqual(args.color, "#33aa11")

    def test_rgb_menu_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "rgb",
                "menu",
                "--all-models",
                "--all-transports",
            ]
        )
        self.assertEqual(args.command, "rgb")
        self.assertEqual(args.rgb_command, "menu")
        self.assertTrue(args.all_models)
        self.assertTrue(args.all_transports)

    def test_button_mapping_set_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "button-mapping",
                "set",
                "--button",
                "side_1",
                "--action",
                "mouse:back",
            ]
        )
        self.assertEqual(args.command, "button-mapping")
        self.assertEqual(args.button_mapping_command, "set")
        self.assertEqual(args.button, "side_1")
        self.assertEqual(args.action, "mouse:back")

    def test_button_mapping_menu_parse(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "button-mapping",
                "menu",
                "--all-models",
                "--all-transports",
            ]
        )
        self.assertEqual(args.command, "button-mapping")
        self.assertEqual(args.button_mapping_command, "menu")
        self.assertTrue(args.all_models)
        self.assertTrue(args.all_transports)

    def test_cli_module_import_is_lazy(self):
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import razecli.cli; "
                    "print(int('razecli.device_service' in sys.modules)); "
                    "print(int('razecli.ble_probe' in sys.modules)); "
                    "print(int('razecli.tui' in sys.modules))"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        flags = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(flags, ["0", "0", "0"])


if __name__ == "__main__":
    unittest.main()
