"""Command-line interface for RazeCLI."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Optional

from razecli.ble.constants import (
    DEFAULT_RAZER_BT_SERVICE_UUID,
    DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
)
from razecli.errors import CapabilityUnsupportedError, DeviceSelectionError, RazeCliError
from razecli.model_registry import ModelRegistry

if TYPE_CHECKING:
    from razecli.device_service import DeviceService

DEFAULT_MODEL = ModelRegistry.load().default_cli_model_slug()
DEFAULT_BLE_NAME_QUERY = "Razer"


def _emit(payload: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(payload)


def _add_target_args(parser: argparse.ArgumentParser, default_model: bool = True) -> None:
    if default_model and DEFAULT_MODEL:
        example_help = f"Example: --model {DEFAULT_MODEL}. "
    else:
        example_help = ""
    parser.add_argument(
        "--device",
        help="Target device id (from `razecli devices`)",
    )
    parser.add_argument(
        "--address",
        metavar="MAC_OR_UUID",
        help=(
            "Bluetooth MAC (e.g. F6:F2:0D:4E:D9:30) or UUID fragment. "
            "With macos-ble, picks the device whose address matches (same value as `ble ... --address`)."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Optional model slug to narrow the target device. "
            "If omitted, any detected device may be used; specify --device or --address if several match. "
            f"{example_help}"
            "Use `razecli models` for available slugs."
        ),
    )


def _add_preset_file_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset-file",
        help=(
            "Path to preset JSON file. "
            "Defaults to $RAZECLI_PRESET_PATH or ~/.config/razecli/dpi_stage_presets.json"
        ),
    )


def _add_feature_store_file_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store-file",
        help=(
            "Path to local scaffold feature store JSON. "
            "Defaults to $RAZECLI_FEATURE_STORE_PATH or ~/.config/razecli/feature_scaffolds.json"
        ),
    )


def _add_ble_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--address",
        help="BLE MAC (F6:F2:0D:4E:D9:30) or CoreBluetooth UUID from `razecli ble scan` / devices",
    )
    parser.add_argument(
        "--name",
        default=DEFAULT_BLE_NAME_QUERY,
        help=(
            "Fallback name filter when --address is omitted "
            f"(default: {DEFAULT_BLE_NAME_QUERY})"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="razecli",
        description="CLI for identifying and configuring Razer mice",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument(
        "--backend",
        choices=("auto", "rawhid", "macos-ble", "hidapi", "macos-profiler"),
        default="auto",
        help="Backend selection (default: auto)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    devices_parser = subparsers.add_parser("devices", help="List detected Razer devices")
    devices_parser.add_argument(
        "--all-transports",
        action="store_true",
        help="Show all transport endpoints (do not collapse rawhid USB/dongle/Bluetooth variants)",
    )
    _add_target_args(devices_parser, default_model=False)

    subparsers.add_parser("models", help="List supported model modules")

    dpi_parser = subparsers.add_parser("dpi", help="Read or change DPI")
    dpi_sub = dpi_parser.add_subparsers(dest="dpi_command", required=True)
    dpi_get = dpi_sub.add_parser("get", help="Read current DPI")
    _add_target_args(dpi_get)

    dpi_set = dpi_sub.add_parser("set", help="Set DPI")
    dpi_set.add_argument("--x", type=int, required=True, help="X-axis DPI")
    dpi_set.add_argument("--y", type=int, help="Y-axis DPI (defaults to X)")
    _add_target_args(dpi_set)

    quick_set = subparsers.add_parser(
        "set",
        help="Quick-set DPI on both axes",
    )
    quick_set.add_argument(
        "dpi",
        type=int,
        help="DPI value applied to both X and Y axes",
    )
    _add_target_args(quick_set)

    dpi_stages_parser = subparsers.add_parser("dpi-stages", help="Read or edit DPI profiles")
    dpi_stages_sub = dpi_stages_parser.add_subparsers(dest="dpi_stages_command", required=True)

    dpi_stages_get = dpi_stages_sub.add_parser("get", help="Read active DPI profile and profile list")
    _add_target_args(dpi_stages_get)

    dpi_stages_set = dpi_stages_sub.add_parser("set", help="Replace all DPI profiles")
    dpi_stages_set.add_argument("--active", type=int, required=True, help="Active profile number (1-based)")
    dpi_stages_set.add_argument(
        "--stage",
        action="append",
        required=True,
        help="Profile in X:Y format. Repeat flag to add more, e.g. --stage 800:800 --stage 1600:1600",
    )
    _add_target_args(dpi_stages_set)

    dpi_stages_add = dpi_stages_sub.add_parser("add", help="Add one DPI profile")
    dpi_stages_add.add_argument("--x", type=int, required=True, help="X-axis DPI")
    dpi_stages_add.add_argument("--y", type=int, help="Y-axis DPI (defaults to X)")
    dpi_stages_add.add_argument(
        "--active",
        action="store_true",
        help="Make new profile active after add",
    )
    _add_target_args(dpi_stages_add)

    dpi_stages_update = dpi_stages_sub.add_parser("update", help="Update one DPI profile by index")
    dpi_stages_update.add_argument("--index", type=int, required=True, help="Profile number (1-based)")
    dpi_stages_update.add_argument("--x", type=int, required=True, help="X-axis DPI")
    dpi_stages_update.add_argument("--y", type=int, help="Y-axis DPI (defaults to X)")
    _add_target_args(dpi_stages_update)

    dpi_stages_remove = dpi_stages_sub.add_parser("remove", help="Remove one DPI profile by index")
    dpi_stages_remove.add_argument("--index", type=int, required=True, help="Profile number (1-based)")
    dpi_stages_remove.add_argument(
        "--active",
        type=int,
        help="Optional active profile number after removal (1-based)",
    )
    _add_target_args(dpi_stages_remove)

    dpi_stages_activate = dpi_stages_sub.add_parser(
        "activate",
        help=(
            "Switch active DPI profile. Safe default writes DPI only; "
            "set RAZECLI_UNSAFE_STAGE_ACTIVATE=1 for full stage-table rewrite."
        ),
    )
    dpi_stages_activate.add_argument("--index", type=int, required=True, help="Profile number (1-based)")
    _add_target_args(dpi_stages_activate)

    dpi_stages_preset = dpi_stages_sub.add_parser(
        "preset",
        help="Save/load DPI profile presets",
    )
    dpi_stages_preset_sub = dpi_stages_preset.add_subparsers(
        dest="dpi_stages_preset_command",
        required=True,
    )

    dpi_stages_preset_list = dpi_stages_preset_sub.add_parser("list", help="List saved presets")
    _add_preset_file_arg(dpi_stages_preset_list)

    dpi_stages_preset_save = dpi_stages_preset_sub.add_parser("save", help="Save current DPI profiles as preset")
    dpi_stages_preset_save.add_argument("--name", required=True, help="Preset name")
    _add_preset_file_arg(dpi_stages_preset_save)
    _add_target_args(dpi_stages_preset_save)

    dpi_stages_preset_load = dpi_stages_preset_sub.add_parser("load", help="Load preset to current device")
    dpi_stages_preset_load.add_argument("--name", required=True, help="Preset name")
    dpi_stages_preset_load.add_argument(
        "--active",
        type=int,
        help="Override active profile number when loading (1-based)",
    )
    dpi_stages_preset_load.add_argument(
        "--force",
        action="store_true",
        help="Allow loading preset even if model_id in preset differs from selected device",
    )
    _add_preset_file_arg(dpi_stages_preset_load)
    _add_target_args(dpi_stages_preset_load)

    dpi_stages_preset_delete = dpi_stages_preset_sub.add_parser("delete", help="Delete preset")
    dpi_stages_preset_delete.add_argument("--name", required=True, help="Preset name")
    _add_preset_file_arg(dpi_stages_preset_delete)

    poll_parser = subparsers.add_parser("poll-rate", help="Read or change poll-rate")
    poll_sub = poll_parser.add_subparsers(dest="poll_command", required=True)
    poll_get = poll_sub.add_parser("get", help="Read poll-rate in Hz")
    _add_target_args(poll_get)

    poll_set = poll_sub.add_parser("set", help="Set poll-rate in Hz")
    poll_set.add_argument("--hz", type=int, required=True, help="Poll-rate, e.g. 125/500/1000")
    _add_target_args(poll_set)

    battery_parser = subparsers.add_parser("battery", help="Read battery level")
    battery_sub = battery_parser.add_subparsers(dest="battery_command", required=True)
    battery_get = battery_sub.add_parser("get", help="Read battery percentage")
    _add_target_args(battery_get)

    rgb_parser = subparsers.add_parser(
        "rgb",
        help="RGB control (hardware when supported, local fallback otherwise)",
    )
    rgb_sub = rgb_parser.add_subparsers(dest="rgb_command", required=True)
    rgb_get = rgb_sub.add_parser("get", help="Read RGB state (hardware-first, then local fallback)")
    _add_feature_store_file_arg(rgb_get)
    _add_target_args(rgb_get)

    rgb_set = rgb_sub.add_parser("set", help="Set RGB state (hardware-first, then local fallback)")
    rgb_set.add_argument(
        "--mode",
        required=True,
        choices=("off", "static", "breathing", "breathing-single", "breathing-random", "spectrum"),
        help="RGB mode",
    )
    rgb_set.add_argument(
        "--brightness",
        type=int,
        help="Brightness 0-100 (optional; keeps existing value when omitted)",
    )
    rgb_set.add_argument(
        "--color",
        help="Hex color (RRGGBB or #RRGGBB), optional",
    )
    _add_feature_store_file_arg(rgb_set)
    _add_target_args(rgb_set)

    rgb_menu = rgb_sub.add_parser(
        "menu",
        help="Open TUI RGB editor directly",
    )
    _add_target_args(rgb_menu)
    rgb_menu.add_argument(
        "--all-models",
        action="store_true",
        help="Show all detected models in device list (ignore --model filter)",
    )
    rgb_menu.add_argument(
        "--all-transports",
        action="store_true",
        help="Show all transport endpoints (do not collapse rawhid USB/dongle/Bluetooth variants)",
    )

    button_mapping_parser = subparsers.add_parser(
        "button-mapping",
        help="Button mapping (hardware when supported, local fallback otherwise)",
    )
    button_mapping_sub = button_mapping_parser.add_subparsers(
        dest="button_mapping_command",
        required=True,
    )

    button_get = button_mapping_sub.add_parser(
        "get",
        help="Read button mapping (hardware-first, then local fallback)",
    )
    _add_feature_store_file_arg(button_get)
    _add_target_args(button_get)

    button_set = button_mapping_sub.add_parser(
        "set",
        help="Set one button mapping (hardware-first, then local fallback)",
    )
    button_set.add_argument("--button", required=True, help="Button key, for example side_1")
    button_set.add_argument("--action", required=True, help="Action key, for example mouse:back")
    _add_feature_store_file_arg(button_set)
    _add_target_args(button_set)

    button_reset = button_mapping_sub.add_parser(
        "reset",
        help="Reset button mapping to defaults (hardware-first, then local fallback)",
    )
    _add_feature_store_file_arg(button_reset)
    _add_target_args(button_reset)

    button_actions = button_mapping_sub.add_parser("actions", help="List suggested buttons/actions for model")
    _add_target_args(button_actions)

    button_menu = button_mapping_sub.add_parser(
        "menu",
        help="Open TUI button-mapping editor directly",
    )
    _add_target_args(button_menu)
    button_menu.add_argument(
        "--all-models",
        action="store_true",
        help="Show all detected models in device list (ignore --model filter)",
    )
    button_menu.add_argument(
        "--all-transports",
        action="store_true",
        help="Show all transport endpoints (do not collapse rawhid USB/dongle/Bluetooth variants)",
    )

    ble_parser = subparsers.add_parser("ble", help="BLE probing tools for reverse engineering")
    ble_sub = ble_parser.add_subparsers(dest="ble_command", required=True)

    ble_scan = ble_sub.add_parser("scan", help="Scan nearby BLE devices")
    ble_scan.add_argument("--timeout", type=float, default=8.0, help="Scan timeout in seconds")
    ble_scan.add_argument("--name", help="Optional case-insensitive name filter")

    ble_services = ble_sub.add_parser("services", help="List BLE services and characteristics")
    _add_ble_target_args(ble_services)
    ble_services.add_argument("--timeout", type=float, default=8.0, help="Probe timeout in seconds")
    ble_services.add_argument(
        "--read",
        action="store_true",
        help="Read values from readable characteristics (best effort)",
    )

    ble_raw = ble_sub.add_parser(
        "raw",
        help="Experimental raw GATT transceive (write/read/notify) for BLE reverse engineering",
    )
    _add_ble_target_args(ble_raw)
    ble_raw.add_argument(
        "--payload",
        required=True,
        help="Hex payload, e.g. '00ff01' or '00 ff 01'",
    )
    ble_raw.add_argument(
        "--service",
        default=DEFAULT_RAZER_BT_SERVICE_UUID,
        help=f"GATT service UUID (default: {DEFAULT_RAZER_BT_SERVICE_UUID})",
    )
    ble_raw.add_argument(
        "--write-char",
        default=DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
        help=f"GATT write characteristic UUID (default: {DEFAULT_RAZER_BT_WRITE_CHAR_UUID})",
    )
    ble_raw.add_argument(
        "--read-char",
        action="append",
        default=None,
        help=(
            "GATT read/notify characteristic UUID (repeat flag for multiple). "
            "Default: both Razer response chars."
        ),
    )
    ble_raw.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Connect/resolve timeout in seconds",
    )
    ble_raw.add_argument(
        "--response-timeout",
        type=float,
        default=1.5,
        help="Wait time in seconds for notifications after write",
    )
    ble_raw.add_argument(
        "--no-notify",
        action="store_true",
        help="Skip start_notify on read characteristics",
    )
    ble_raw.add_argument(
        "--no-read",
        action="store_true",
        help="Skip direct read_gatt_char after write",
    )
    ble_raw.add_argument(
        "--no-response",
        action="store_true",
        help="Use write without response (best effort)",
    )

    ble_poll_probe = ble_sub.add_parser(
        "poll-probe",
        help="Probe Bluetooth poll-rate keys and show detailed decode diagnostics",
    )
    _add_ble_target_args(ble_poll_probe)
    ble_poll_probe.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connect/resolve timeout in seconds",
    )
    ble_poll_probe.add_argument(
        "--response-timeout",
        type=float,
        default=1.5,
        help="Wait time in seconds for response notifications",
    )
    ble_poll_probe.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="How many full key rounds to run (default: 1)",
    )
    ble_poll_probe.add_argument(
        "--key",
        action="append",
        default=None,
        help=(
            "Override poll probe key (4 bytes hex, repeat flag for multiple). "
            "Example: --key 00850001 --key 0b850100"
        ),
    )

    ble_rgb_probe = ble_sub.add_parser(
        "rgb-probe",
        help="Probe BLE RGB read keys (brightness/frame/mode) with decode diagnostics",
    )
    _add_ble_target_args(ble_rgb_probe)
    ble_rgb_probe.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connect/resolve timeout in seconds",
    )
    ble_rgb_probe.add_argument(
        "--response-timeout",
        type=float,
        default=1.5,
        help="Wait time in seconds for response notifications",
    )
    ble_rgb_probe.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="How many full key rounds to run (default: 1)",
    )
    ble_rgb_probe.add_argument(
        "--brightness-key",
        action="append",
        default=None,
        help=(
            "Override brightness read key (4 bytes hex, repeat flag for multiple). "
            "Example: --brightness-key 10850101"
        ),
    )
    ble_rgb_probe.add_argument(
        "--frame-key",
        action="append",
        default=None,
        help=(
            "Override RGB frame read key (4 bytes hex, repeat flag for multiple). "
            "Example: --frame-key 10840000"
        ),
    )
    ble_rgb_probe.add_argument(
        "--mode-key",
        action="append",
        default=None,
        help=(
            "Override RGB mode read key (4 bytes hex, repeat flag for multiple). "
            "Example: --mode-key 10830000"
        ),
    )

    ble_button_probe = ble_sub.add_parser(
        "button-probe",
        help="Probe BLE button-mapping read keys with decode diagnostics",
    )
    _add_ble_target_args(ble_button_probe)
    ble_button_probe.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connect/resolve timeout in seconds",
    )
    ble_button_probe.add_argument(
        "--response-timeout",
        type=float,
        default=1.5,
        help="Wait time in seconds for response notifications",
    )
    ble_button_probe.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="How many full key rounds to run (default: 1)",
    )
    ble_button_probe.add_argument(
        "--key",
        action="append",
        default=None,
        help=(
            "Override button read key (4 bytes hex, repeat flag for multiple). "
            "Example: --key 08840104"
        ),
    )

    ble_bank_probe = ble_sub.add_parser(
        "bank-probe",
        help="Probe BLE DPI-stage keys and fingerprint the currently active onboard bank",
    )
    _add_ble_target_args(ble_bank_probe)
    ble_bank_probe.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connect/resolve timeout in seconds",
    )
    ble_bank_probe.add_argument(
        "--response-timeout",
        type=float,
        default=1.5,
        help="Wait time in seconds for response notifications",
    )
    ble_bank_probe.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="How many full key rounds to run (default: 1)",
    )
    ble_bank_probe.add_argument(
        "--key",
        action="append",
        default=None,
        help=(
            "Override bank probe key (4 bytes hex, repeat flag for multiple). "
            "Example: --key 0b840100 --key 0b840000"
        ),
    )
    ble_bank_probe.add_argument(
        "--deep",
        action="store_true",
        help="Run deeper key sweep to look for explicit bank-id/selector responses",
    )
    ble_bank_probe.add_argument(
        "--include-write-keys",
        action="store_true",
        help=(
            "Include 0x04 bank keys in deep probe. "
            "Unsafe: may mutate device state/profile table on some firmware."
        ),
    )
    ble_bank_probe.add_argument(
        "--settle-delay",
        type=float,
        default=None,
        help=(
            "Optional delay in seconds between probe rounds. "
            "When omitted, deep mode uses a short settle delay automatically."
        ),
    )
    ble_bank_probe.add_argument(
        "--reconnect-each-round",
        dest="reconnect_each_round",
        action="store_true",
        default=None,
        help=(
            "Re-resolve/reconnect target between rounds. "
            "Enabled automatically for deep mode unless explicitly disabled."
        ),
    )
    ble_bank_probe.add_argument(
        "--no-reconnect-each-round",
        dest="reconnect_each_round",
        action="store_false",
        help="Disable reconnect/re-resolve between rounds.",
    )

    ble_bank_snapshot = ble_sub.add_parser(
        "bank-snapshot",
        help="Capture and persist a BLE bank fingerprint snapshot",
    )
    _add_ble_target_args(ble_bank_snapshot)
    ble_bank_snapshot.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Connect/resolve timeout in seconds",
    )
    ble_bank_snapshot.add_argument(
        "--response-timeout",
        type=float,
        default=1.5,
        help="Wait time in seconds for response notifications",
    )
    ble_bank_snapshot.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="How many full key rounds to run (default: 1)",
    )
    ble_bank_snapshot.add_argument(
        "--key",
        action="append",
        default=None,
        help=(
            "Override bank probe key (4 bytes hex, repeat flag for multiple). "
            "Example: --key 0b840100 --key 0b840000"
        ),
    )
    ble_bank_snapshot.add_argument(
        "--deep",
        action="store_true",
        help="Run deeper key sweep before storing snapshot",
    )
    ble_bank_snapshot.add_argument(
        "--include-write-keys",
        action="store_true",
        help=(
            "Include 0x04 bank keys in deep snapshot probe. "
            "Unsafe: may mutate device state/profile table on some firmware."
        ),
    )
    ble_bank_snapshot.add_argument(
        "--settle-delay",
        type=float,
        default=None,
        help=(
            "Optional delay in seconds between probe rounds. "
            "When omitted, deep mode uses a short settle delay automatically."
        ),
    )
    ble_bank_snapshot.add_argument(
        "--reconnect-each-round",
        dest="reconnect_each_round",
        action="store_true",
        default=None,
        help=(
            "Re-resolve/reconnect target between rounds. "
            "Enabled automatically for deep mode unless explicitly disabled."
        ),
    )
    ble_bank_snapshot.add_argument(
        "--no-reconnect-each-round",
        dest="reconnect_each_round",
        action="store_false",
        help="Disable reconnect/re-resolve between rounds.",
    )
    ble_bank_snapshot.add_argument(
        "--label",
        help="Optional snapshot label (for example 'green-led-bank')",
    )
    ble_bank_snapshot.add_argument(
        "--path",
        help=(
            "Snapshot store JSON path. Defaults to $RAZECLI_BLE_BANK_SNAPSHOT_PATH "
            "or ~/.config/razecli/ble_bank_snapshots.json"
        ),
    )

    ble_bank_compare = ble_sub.add_parser(
        "bank-compare",
        help="Compare two saved BLE bank snapshots by label",
    )
    ble_bank_compare.add_argument(
        "--label-a",
        required=True,
        help="First snapshot label (latest matching label is used)",
    )
    ble_bank_compare.add_argument(
        "--label-b",
        required=True,
        help="Second snapshot label (latest matching label is used)",
    )
    ble_bank_compare.add_argument(
        "--path",
        help=(
            "Snapshot store JSON path. Defaults to $RAZECLI_BLE_BANK_SNAPSHOT_PATH "
            "or ~/.config/razecli/ble_bank_snapshots.json"
        ),
    )

    ble_alias = ble_sub.add_parser("alias", help="Manage BLE MAC->UUID alias cache")
    ble_alias_sub = ble_alias.add_subparsers(dest="ble_alias_command", required=True)
    ble_alias_sub.add_parser("list", help="List cached MAC->CoreBluetooth UUID aliases")
    ble_alias_clear_cmd = ble_alias_sub.add_parser("clear", help="Clear alias cache entries")
    ble_alias_clear_cmd.add_argument(
        "--address",
        help="Clear one MAC address alias (for example 02:11:22:33:44:55). "
        "If omitted with --all, clears the full cache.",
    )
    ble_alias_clear_cmd.add_argument(
        "--all",
        action="store_true",
        help="Clear all cached aliases",
    )
    ble_alias_resolve_cmd = ble_alias_sub.add_parser(
        "resolve",
        help="Force-refresh MAC->CoreBluetooth UUID alias",
    )
    ble_alias_resolve_cmd.add_argument(
        "--address",
        required=True,
        help="MAC address, for example 02:11:22:33:44:55",
    )
    ble_alias_resolve_cmd.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Resolve timeout in seconds",
    )

    tui_parser = subparsers.add_parser("tui", help="Start interactive terminal UI")
    tui_parser.add_argument(
        "--device",
        help="Preselect device id (from `razecli devices`)",
    )
    tui_parser.add_argument(
        "--model",
        default=None,
        help=(
            (
                "Optional model slug filter. If omitted, all detected models are shown "
                f"(example filter: {DEFAULT_MODEL})."
            )
            if DEFAULT_MODEL
            else "Optional model slug filter. If omitted, all detected models are shown."
        ),
    )
    tui_parser.add_argument(
        "--all-models",
        action="store_true",
        help="Show all detected models in TUI (ignore --model filter)",
    )
    tui_parser.add_argument(
        "--all-transports",
        action="store_true",
        help="Show all transport endpoints (do not collapse rawhid USB/dongle/Bluetooth variants)",
    )

    return parser



def _handle_models(service: DeviceService, as_json: bool) -> int:
    from razecli.models.base import format_usb_id

    models = service.registry.list()
    if as_json:
        payload = [
            {
                "slug": model.slug,
                "name": model.name,
                "usb_ids": [format_usb_id(usb_id) for usb_id in model.usb_ids],
                "dpi_min": model.dpi_min,
                "dpi_max": model.dpi_max,
                "supported_poll_rates": list(model.supported_poll_rates),
                "ble_poll_rate_supported": bool(model.ble_poll_rate_supported),
                "ble_supported_poll_rates": list(model.ble_supported_poll_rates),
                "ble_supported_rgb_modes": list(model.ble_supported_rgb_modes),
                "ble_endpoint_product_ids": list(model.ble_endpoint_product_ids),
                "ble_endpoint_experimental": bool(model.ble_endpoint_experimental),
                "ble_multi_profile_table_limited": bool(model.ble_multi_profile_table_limited),
                "onboard_profile_bank_switch": bool(model.onboard_profile_bank_switch),
                "rawhid_mirror_product_ids": list(model.rawhid_mirror_product_ids),
                "rawhid_transport_priority": list(model.rawhid_transport_priority),
                "cli_default_target": bool(model.cli_default_target),
            }
            for model in models
        ]
        _emit(payload, as_json=True)
        return 0

    if not models:
        print("No registered models")
        return 0

    for model in models:
        print(f"{model.slug} ({model.name})")
        print(f"  USB: {', '.join(format_usb_id(usb_id) for usb_id in model.usb_ids)}")
        if model.ble_endpoint_product_ids:
            ble_endpoints = ", ".join(f"{pid:04X}" for pid in model.ble_endpoint_product_ids)
            print(f"  BLE endpoint PIDs: {ble_endpoints}")
        if model.dpi_min is not None or model.dpi_max is not None:
            print(f"  DPI range: {model.dpi_min or '?'}-{model.dpi_max or '?'}")
        if model.supported_poll_rates:
            print(f"  Poll rates: {', '.join(str(rate) for rate in model.supported_poll_rates)}")
        print(f"  BLE poll-rate: {'yes' if model.ble_poll_rate_supported else 'no'}")
        if model.ble_supported_poll_rates:
            print(f"  BLE poll rates: {', '.join(str(rate) for rate in model.ble_supported_poll_rates)}")
        if model.ble_supported_rgb_modes:
            print(f"  BLE RGB modes: {', '.join(model.ble_supported_rgb_modes)}")
        if model.rawhid_transport_priority:
            order = ", ".join(f"{pid:04X}" for pid in model.rawhid_transport_priority)
            print(f"  Rawhid transport priority: {order}")
        if model.cli_default_target:
            print("  CLI default target: yes")
        print()

    return 0


def _handle_tui(service: DeviceService, args: argparse.Namespace) -> int:
    from razecli.tui import run_tui

    if args.json:
        raise RazeCliError("--json is not supported in interactive TUI mode")

    model_filter = None if (args.all_models or args.model is None) else args.model
    if model_filter and service.registry.get(model_filter) is None:
        raise DeviceSelectionError(f"Unknown model: {model_filter}")

    return run_tui(
        service=service,
        model_filter=model_filter,
        preselected_device_id=args.device,
        collapse_transports=not bool(args.all_transports),
    )


def run(args: argparse.Namespace) -> int:
    if args.command == "ble":
        from razecli.cli_ble import handle_ble

        return handle_ble(args)

    from razecli.device_service import DeviceService

    service = DeviceService(backend_mode=args.backend)

    if args.command == "models":
        return _handle_models(service, args.json)

    if args.command == "devices":
        from razecli.cli_devices import handle_devices

        return handle_devices(service, args)

    if args.command == "dpi":
        from razecli.cli_dpi import handle_dpi

        return handle_dpi(service, args)

    if args.command == "set":
        from razecli.cli_dpi import handle_dpi_quick_set

        return handle_dpi_quick_set(service, args)

    if args.command == "dpi-stages":
        from razecli.cli_dpi_stages import handle_dpi_stages

        return handle_dpi_stages(service, args)

    if args.command == "poll-rate":
        from razecli.cli_poll_rate import handle_poll_rate

        return handle_poll_rate(service, args)

    if args.command == "battery":
        from razecli.cli_battery import handle_battery

        return handle_battery(service, args)

    if args.command == "rgb":
        from razecli.cli_rgb import handle_rgb

        return handle_rgb(service, args)

    if args.command == "button-mapping":
        from razecli.cli_button_mapping import handle_button_mapping

        return handle_button_mapping(service, args)

    if args.command == "tui":
        return _handle_tui(service, args)

    raise RazeCliError(f"Unknown command: {args.command}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return run(args)
    except (RazeCliError, DeviceSelectionError, CapabilityUnsupportedError) as exc:
        if getattr(args, "json", False):
            _emit({"status": "error", "message": str(exc)}, as_json=True)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
