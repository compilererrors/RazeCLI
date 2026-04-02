"""Handlers for the `dpi-stages` command."""

from __future__ import annotations

import argparse

from razecli.cli_common import (
    MAX_DPI_STAGES,
    emit,
    format_dpi_stages_payload,
    mirror_dpi_stages_to_transport_peers,
    parse_dpi_stage,
    persist_autosync_from_device,
    persist_autosync_from_stages,
    resolve_target_device,
    unsafe_stage_activate_enabled,
    validate_dpi_values,
)
from razecli.device_service import DeviceService
from razecli.dpi_stage_presets import (
    delete_dpi_stage_preset,
    list_dpi_stage_presets,
    load_dpi_stage_preset,
    resolve_preset_path,
    save_dpi_stage_preset,
)
from razecli.errors import RazeCliError


def handle_dpi_stages(service: DeviceService, args: argparse.Namespace) -> int:
    if args.dpi_stages_command == "preset":
        return handle_dpi_stages_preset(service, args)

    device = resolve_target_device(service, args)
    backend = service.resolve_backend(device)

    if args.dpi_stages_command == "get":
        active_stage, stages = backend.get_dpi_stages(device)
        emit(format_dpi_stages_payload(device, active_stage, stages), as_json=args.json)
        return 0

    if args.dpi_stages_command == "set":
        stages = [parse_dpi_stage(value) for value in args.stage]
        if len(stages) > MAX_DPI_STAGES:
            raise RazeCliError(f"At most {MAX_DPI_STAGES} DPI profiles are supported")
        if len(stages) < 1:
            raise RazeCliError("At least one DPI profile is required")

        validated = [validate_dpi_values(dpi_x, dpi_y, service, device) for dpi_x, dpi_y in stages]
        active_stage = int(args.active)
        if active_stage < 1 or active_stage > len(validated):
            raise RazeCliError(
                f"Active profile must be between 1 and {len(validated)}"
            )

        backend.set_dpi_stages(device, active_stage, validated)
        mirror_dpi_stages_to_transport_peers(service, device, active_stage, validated)
        persist_autosync_from_stages(device, active_stage, validated)
        emit(
            format_dpi_stages_payload(device, active_stage, validated, status="ok"),
            as_json=args.json,
        )
        return 0

    if args.dpi_stages_command == "add":
        active_stage, stages = backend.get_dpi_stages(device)
        current = list(stages)
        if len(current) >= MAX_DPI_STAGES:
            raise RazeCliError(f"Cannot add more profiles. Max is {MAX_DPI_STAGES}.")

        dpi_x = int(args.x)
        dpi_y = int(args.y) if args.y is not None else dpi_x
        validated = validate_dpi_values(dpi_x, dpi_y, service, device)
        current.append(validated)

        new_active = len(current) if bool(args.active) else int(active_stage)
        backend.set_dpi_stages(device, new_active, current)
        mirror_dpi_stages_to_transport_peers(service, device, new_active, current)
        persist_autosync_from_stages(device, new_active, current)
        emit(
            format_dpi_stages_payload(device, new_active, current, status="ok"),
            as_json=args.json,
        )
        return 0

    if args.dpi_stages_command == "update":
        active_stage, stages = backend.get_dpi_stages(device)
        current = list(stages)

        index = int(args.index)
        if index < 1 or index > len(current):
            raise RazeCliError(f"Profile index must be between 1 and {len(current)}")

        dpi_x = int(args.x)
        dpi_y = int(args.y) if args.y is not None else dpi_x
        current[index - 1] = validate_dpi_values(dpi_x, dpi_y, service, device)

        backend.set_dpi_stages(device, int(active_stage), current)
        mirror_dpi_stages_to_transport_peers(service, device, int(active_stage), current)
        persist_autosync_from_stages(device, int(active_stage), current)
        emit(
            format_dpi_stages_payload(device, int(active_stage), current, status="ok"),
            as_json=args.json,
        )
        return 0

    if args.dpi_stages_command == "remove":
        active_stage, stages = backend.get_dpi_stages(device)
        current = list(stages)

        if len(current) <= 1:
            raise RazeCliError("At least one DPI profile must remain")

        index = int(args.index)
        if index < 1 or index > len(current):
            raise RazeCliError(f"Profile index must be between 1 and {len(current)}")

        del current[index - 1]

        if args.active is not None:
            new_active = int(args.active)
            if new_active < 1 or new_active > len(current):
                raise RazeCliError(
                    f"New active profile must be between 1 and {len(current)}"
                )
        else:
            if active_stage == index:
                new_active = min(index, len(current))
            elif active_stage > index:
                new_active = int(active_stage) - 1
            else:
                new_active = int(active_stage)

        backend.set_dpi_stages(device, new_active, current)
        mirror_dpi_stages_to_transport_peers(service, device, new_active, current)
        persist_autosync_from_stages(device, new_active, current)
        emit(
            format_dpi_stages_payload(device, new_active, current, status="ok"),
            as_json=args.json,
        )
        return 0

    if args.dpi_stages_command == "activate":
        active_stage, stages = backend.get_dpi_stages(device)
        current = list(stages)

        new_active = int(args.index)
        if new_active < 1 or new_active > len(current):
            raise RazeCliError(f"Profile index must be between 1 and {len(current)}")

        wrote_stage_layout = False
        if new_active != int(active_stage):
            if unsafe_stage_activate_enabled():
                backend.set_dpi_stages(device, new_active, current)
                wrote_stage_layout = True
            else:
                # Safe default: avoid rewriting the full stage list just to switch stage.
                # Full rewrites can reset stage values on some firmware/transport combinations.
                target_dpi_x, target_dpi_y = current[new_active - 1]
                backend.set_dpi(device, int(target_dpi_x), int(target_dpi_y))

        if wrote_stage_layout:
            persist_autosync_from_stages(device, new_active, current)
        else:
            persist_autosync_from_device(device, backend)
        emit(
            format_dpi_stages_payload(device, new_active, current, status="ok"),
            as_json=args.json,
        )
        return 0

    raise RazeCliError("Unknown dpi-stages command")


def handle_dpi_stages_preset(service: DeviceService, args: argparse.Namespace) -> int:
    command = args.dpi_stages_preset_command
    preset_file = getattr(args, "preset_file", None)
    preset_path = resolve_preset_path(preset_file)

    if command == "list":
        presets = list_dpi_stage_presets(preset_file)
        if args.json:
            emit(
                {
                    "path": str(preset_path),
                    "count": len(presets),
                    "presets": presets,
                },
                as_json=True,
            )
            return 0

        print(f"Preset file: {preset_path}")
        if not presets:
            print("No presets saved")
            return 0

        for entry in presets:
            print(
                f"{entry['name']} model={entry.get('model_id') or '-'} "
                f"active={entry.get('active_stage')} stages={entry.get('stages_count')}"
            )
        return 0

    if command == "delete":
        path = delete_dpi_stage_preset(args.name, preset_file)
        emit(
            {
                "status": "ok",
                "preset": args.name,
                "path": str(path),
            },
            as_json=args.json,
        )
        return 0

    device = resolve_target_device(service, args)
    backend = service.resolve_backend(device)

    if command == "save":
        active_stage, stages = backend.get_dpi_stages(device)
        persist_autosync_from_stages(device, active_stage, stages)
        path = save_dpi_stage_preset(
            name=args.name,
            model_id=device.model_id,
            active_stage=active_stage,
            stages=stages,
            path=preset_file,
        )
        emit(
            {
                "status": "ok",
                "preset": args.name,
                "path": str(path),
                "model": device.model_id,
                "active_stage": int(active_stage),
                "stages_count": len(stages),
            },
            as_json=args.json,
        )
        return 0

    if command == "load":
        preset = load_dpi_stage_preset(args.name, preset_file)
        preset_model = preset.get("model_id")
        if (
            preset_model
            and device.model_id
            and preset_model != device.model_id
            and not bool(args.force)
        ):
            raise RazeCliError(
                f"Preset '{args.name}' is for model '{preset_model}', but selected device is '{device.model_id}'. "
                "Use --force to load anyway."
            )

        stages = list(preset["stages"])
        if len(stages) > MAX_DPI_STAGES:
            raise RazeCliError(
                f"Preset '{args.name}' contains too many profiles ({len(stages)} > {MAX_DPI_STAGES})"
            )
        validated = [validate_dpi_values(dpi_x, dpi_y, service, device) for dpi_x, dpi_y in stages]

        active_stage = int(args.active) if args.active is not None else int(preset["active_stage"])
        if active_stage < 1 or active_stage > len(validated):
            raise RazeCliError(
                f"Active profile must be between 1 and {len(validated)}"
            )

        backend.set_dpi_stages(device, active_stage, validated)
        mirror_dpi_stages_to_transport_peers(service, device, active_stage, validated)
        persist_autosync_from_stages(device, active_stage, validated)
        emit(
            {
                "status": "ok",
                "preset": args.name,
                "path": preset["path"],
                "id": device.identifier,
                "model": device.model_id,
                "active_stage": active_stage,
                "stages_count": len(validated),
                "stages": [
                    {"index": idx, "dpi_x": int(dpi_x), "dpi_y": int(dpi_y)}
                    for idx, (dpi_x, dpi_y) in enumerate(validated, start=1)
                ],
            },
            as_json=args.json,
        )
        return 0

    raise RazeCliError("Unknown dpi-stages preset command")
