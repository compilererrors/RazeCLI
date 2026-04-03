"""Command-line interface for RazeCLI."""

import argparse
import sys
from typing import Optional

from razecli.ble_probe import (
    DEFAULT_RAZER_BT_SERVICE_UUID,
    DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
)
from razecli.cli_battery import handle_battery
from razecli.cli_ble import handle_ble
from razecli.cli_button_mapping import handle_button_mapping
from razecli.cli_common import emit
from razecli.cli_devices import handle_devices
from razecli.cli_dpi import handle_dpi
from razecli.cli_dpi_stages import handle_dpi_stages
from razecli.cli_poll_rate import handle_poll_rate
from razecli.cli_rgb import handle_rgb
from razecli.device_service import DeviceService
from razecli.errors import CapabilityUnsupportedError, DeviceSelectionError, RazeCliError
from razecli.models.base import format_usb_id
from razecli.tui import run_tui

DEFAULT_MODEL = "deathadder-v2-pro"


def _add_target_args(parser: argparse.ArgumentParser, default_model: bool = True) -> None:
    parser.add_argument(
        "--device",
        help="Target device id (from `razecli devices`)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL if default_model else None,
        help=(
            "Model slug to target. Defaults to deathadder-v2-pro for settings commands. "
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
        choices=("off", "static", "breathing", "spectrum"),
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

    ble_parser = subparsers.add_parser("ble", help="BLE probing tools for reverse engineering")
    ble_sub = ble_parser.add_subparsers(dest="ble_command", required=True)

    ble_scan = ble_sub.add_parser("scan", help="Scan nearby BLE devices")
    ble_scan.add_argument("--timeout", type=float, default=8.0, help="Scan timeout in seconds")
    ble_scan.add_argument("--name", help="Optional case-insensitive name filter")

    ble_services = ble_sub.add_parser("services", help="List BLE services and characteristics")
    ble_services.add_argument("--address", help="BLE address/id from `razecli ble scan`")
    ble_services.add_argument(
        "--name",
        default="DA V2 Pro",
        help="Fallback name filter when --address is omitted (default: DA V2 Pro)",
    )
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
    ble_raw.add_argument("--address", help="BLE address/id from `razecli ble scan`")
    ble_raw.add_argument(
        "--name",
        default="DA V2 Pro",
        help="Fallback name filter when --address is omitted (default: DA V2 Pro)",
    )
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
        default=DEFAULT_MODEL,
        help="Model slug filter for the TUI (default: deathadder-v2-pro)",
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
            }
            for model in models
        ]
        emit(payload, as_json=True)
        return 0

    if not models:
        print("No registered models")
        return 0

    for model in models:
        print(f"{model.slug} ({model.name})")
        print(f"  USB: {', '.join(format_usb_id(usb_id) for usb_id in model.usb_ids)}")
        if model.dpi_min is not None or model.dpi_max is not None:
            print(f"  DPI range: {model.dpi_min or '?'}-{model.dpi_max or '?'}")
        if model.supported_poll_rates:
            print(f"  Poll rates: {', '.join(str(rate) for rate in model.supported_poll_rates)}")

    return 0


def _handle_tui(service: DeviceService, args: argparse.Namespace) -> int:
    if args.json:
        raise RazeCliError("--json is not supported in interactive TUI mode")

    model_filter = None if args.all_models else args.model
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
        return handle_ble(args)

    service = DeviceService(backend_mode=args.backend)

    if args.command == "models":
        return _handle_models(service, args.json)

    if args.command == "devices":
        return handle_devices(service, args)

    if args.command == "dpi":
        return handle_dpi(service, args)

    if args.command == "dpi-stages":
        return handle_dpi_stages(service, args)

    if args.command == "poll-rate":
        return handle_poll_rate(service, args)

    if args.command == "battery":
        return handle_battery(service, args)

    if args.command == "rgb":
        return handle_rgb(service, args)

    if args.command == "button-mapping":
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
            emit({"status": "error", "message": str(exc)}, as_json=True)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
