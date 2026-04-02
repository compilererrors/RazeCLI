"""Persistence helpers for DPI stage presets."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from razecli.errors import RazeCliError

_DEFAULT_PRESET_PATH = Path.home() / ".config" / "razecli" / "dpi_stage_presets.json"


def resolve_preset_path(path: Optional[str] = None) -> Path:
    if path:
        return Path(path).expanduser()

    env_path = os.environ.get("RAZECLI_PRESET_PATH")
    if env_path:
        return Path(env_path).expanduser()

    return _DEFAULT_PRESET_PATH


def _read_store(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "presets": {}}

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RazeCliError(f"Could not read preset file: {path} ({exc})") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RazeCliError(f"Preset file is not valid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise RazeCliError(f"Preset file has invalid format: {path}")

    presets = data.get("presets")
    if not isinstance(presets, dict):
        data["presets"] = {}

    return data


def _write_store(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        raise RazeCliError(f"Could not write preset file: {path} ({exc})") from exc


def _normalize_stages(stages: Sequence[Tuple[int, int]]) -> List[List[int]]:
    normalized: List[List[int]] = []
    for dpi_x, dpi_y in stages:
        normalized.append([int(dpi_x), int(dpi_y)])
    return normalized


def list_dpi_stage_presets(path: Optional[str] = None) -> List[Dict[str, Any]]:
    preset_path = resolve_preset_path(path)
    store = _read_store(preset_path)
    presets = store.get("presets", {})

    rows: List[Dict[str, Any]] = []
    for name in sorted(presets.keys()):
        entry = presets.get(name, {})
        if not isinstance(entry, dict):
            continue

        stages_raw = entry.get("stages", [])
        stage_count = len(stages_raw) if isinstance(stages_raw, list) else 0
        rows.append(
            {
                "name": name,
                "model_id": entry.get("model_id"),
                "active_stage": int(entry.get("active_stage", 1) or 1),
                "stages_count": stage_count,
                "updated_at": entry.get("updated_at"),
            }
        )

    return rows


def save_dpi_stage_preset(
    name: str,
    model_id: Optional[str],
    active_stage: int,
    stages: Sequence[Tuple[int, int]],
    path: Optional[str] = None,
) -> Path:
    preset_name = name.strip()
    if not preset_name:
        raise RazeCliError("Preset name cannot be empty")
    if not stages:
        raise RazeCliError("Cannot save preset without DPI profiles")

    preset_path = resolve_preset_path(path)
    store = _read_store(preset_path)
    presets = store.setdefault("presets", {})
    if not isinstance(presets, dict):
        presets = {}
        store["presets"] = presets

    presets[preset_name] = {
        "model_id": model_id,
        "active_stage": int(active_stage),
        "stages": _normalize_stages(stages),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    _write_store(preset_path, store)
    return preset_path


def load_dpi_stage_preset(name: str, path: Optional[str] = None) -> Dict[str, Any]:
    preset_name = name.strip()
    if not preset_name:
        raise RazeCliError("Preset name cannot be empty")

    preset_path = resolve_preset_path(path)
    store = _read_store(preset_path)
    presets = store.get("presets", {})
    if not isinstance(presets, dict) or preset_name not in presets:
        raise RazeCliError(f"Preset '{preset_name}' does not exist in {preset_path}")

    entry = presets[preset_name]
    if not isinstance(entry, dict):
        raise RazeCliError(f"Preset '{preset_name}' has invalid format")

    stages_raw = entry.get("stages", [])
    if not isinstance(stages_raw, list) or not stages_raw:
        raise RazeCliError(f"Preset '{preset_name}' is missing valid DPI profiles")

    stages: List[Tuple[int, int]] = []
    for item in stages_raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise RazeCliError(f"Preset '{preset_name}' contains an invalid DPI profile")
        stages.append((int(item[0]), int(item[1])))

    return {
        "name": preset_name,
        "model_id": entry.get("model_id"),
        "active_stage": int(entry.get("active_stage", 1) or 1),
        "stages": stages,
        "updated_at": entry.get("updated_at"),
        "path": str(preset_path),
    }


def delete_dpi_stage_preset(name: str, path: Optional[str] = None) -> Path:
    preset_name = name.strip()
    if not preset_name:
        raise RazeCliError("Preset name cannot be empty")

    preset_path = resolve_preset_path(path)
    store = _read_store(preset_path)
    presets = store.get("presets", {})
    if not isinstance(presets, dict) or preset_name not in presets:
        raise RazeCliError(f"Preset '{preset_name}' does not exist in {preset_path}")

    del presets[preset_name]
    _write_store(preset_path, store)
    return preset_path
