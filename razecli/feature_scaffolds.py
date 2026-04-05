"""Local scaffold storage for upcoming RGB and button-mapping features."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from razecli.errors import RazeCliError

_DEFAULT_STORE_PATH = Path.home() / ".config" / "razecli" / "feature_scaffolds.json"

_RGB_MODES = ("off", "static", "breathing", "breathing-single", "breathing-random", "spectrum")
_DEFAULT_RGB = {
    "mode": "off",
    "brightness": 100,
    "color": "00ff00",
}
_DEFAULT_RGB_PRESETS = {
    "off": {"mode": "off", "brightness": 0, "color": "000000"},
    "static-green": {"mode": "static", "brightness": 60, "color": "00ff00"},
    "breathing-green": {"mode": "breathing-single", "brightness": 60, "color": "00ff00"},
    "breathing-random": {"mode": "breathing-random", "brightness": 60, "color": "00ff00"},
    "breathing-warm": {"mode": "breathing-single", "brightness": 45, "color": "ff5500"},
    "spectrum-medium": {"mode": "spectrum", "brightness": 60, "color": "00ff00"},
}

_DA_V2_PRO_BUTTONS = (
    "left_click",
    "right_click",
    "middle_click",
    "side_1",
    "side_2",
    "dpi_cycle",
)

_DA_V2_PRO_DEFAULT_MAPPING = {
    "left_click": "mouse:left",
    "right_click": "mouse:right",
    "middle_click": "mouse:middle",
    "side_1": "mouse:back",
    "side_2": "mouse:forward",
    "dpi_cycle": "dpi:cycle",
}

_DA_V2_PRO_ACTIONS = (
    "mouse:left",
    "mouse:right",
    "mouse:middle",
    "mouse:back",
    "mouse:forward",
    "mouse:scroll-up",
    "mouse:scroll-down",
    "mouse:scroll-left",
    "mouse:scroll-right",
    "dpi:cycle",
    "keyboard:0x2c",
    "keyboard-turbo:0x2c:142",
    "mouse-turbo:mouse:left:142",
    "dpi:stage:1",
    "dpi:stage:2",
    "dpi:stage:3",
    "poll:125",
    "poll:500",
    "poll:1000",
    "disabled",
)

_MODEL_BUTTON_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "deathadder-v2-pro": {
        "buttons": list(_DA_V2_PRO_BUTTONS),
        "actions": list(_DA_V2_PRO_ACTIONS),
        "default_mapping": dict(_DA_V2_PRO_DEFAULT_MAPPING),
        "strict_buttons": True,
    }
}


def resolve_feature_store_path(path: Optional[str] = None) -> Path:
    if path:
        return Path(path).expanduser()

    env_path = os.environ.get("RAZECLI_FEATURE_STORE_PATH")
    if env_path:
        return Path(env_path).expanduser()

    return _DEFAULT_STORE_PATH


def _default_store() -> Dict[str, Any]:
    return {
        "version": 1,
        "rgb": {},
        "rgb_presets": {},
        "button_mapping": {},
    }


def _read_store(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _default_store()

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RazeCliError(f"Could not read feature store: {path} ({exc})") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RazeCliError(f"Feature store is not valid JSON: {path}") from exc

    if not isinstance(data, dict):
        raise RazeCliError(f"Feature store has invalid format: {path}")

    data.setdefault("rgb", {})
    data.setdefault("rgb_presets", {})
    data.setdefault("button_mapping", {})
    if (
        not isinstance(data["rgb"], dict)
        or not isinstance(data["rgb_presets"], dict)
        or not isinstance(data["button_mapping"], dict)
    ):
        raise RazeCliError(f"Feature store has invalid format: {path}")
    return data


def _write_store(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        raise RazeCliError(f"Could not write feature store: {path} ({exc})") from exc


def _model_key(model_id: Optional[str]) -> str:
    text = str(model_id or "").strip()
    return text or "unknown-model"


def _schema_for_model(model_id: Optional[str]) -> Dict[str, Any]:
    schema = _MODEL_BUTTON_SCHEMAS.get(_model_key(model_id))
    if isinstance(schema, dict):
        return {
            "buttons": list(schema.get("buttons", [])),
            "actions": list(schema.get("actions", [])),
            "default_mapping": dict(schema.get("default_mapping", {})),
            "strict_buttons": bool(schema.get("strict_buttons", False)),
        }

    return {
        "buttons": [],
        "actions": [],
        "default_mapping": {},
        "strict_buttons": False,
    }


def _normalize_rgb_color(value: str) -> str:
    text = value.strip().lower()
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6 or any(ch not in "0123456789abcdef" for ch in text):
        raise RazeCliError("RGB color must be a 6-digit hex value, for example 00ff88")
    return text


def get_rgb_scaffold(model_id: Optional[str], path: Optional[str] = None) -> Dict[str, Any]:
    store_path = resolve_feature_store_path(path)
    store = _read_store(store_path)
    key = _model_key(model_id)
    rgb_raw = store.get("rgb", {}).get(key, {})
    if not isinstance(rgb_raw, dict):
        rgb_raw = {}

    rgb = dict(_DEFAULT_RGB)
    rgb.update(
        {
            "mode": str(rgb_raw.get("mode", _DEFAULT_RGB["mode"])),
            "brightness": int(rgb_raw.get("brightness", _DEFAULT_RGB["brightness"])),
            "color": str(rgb_raw.get("color", _DEFAULT_RGB["color"])),
        }
    )
    if rgb["mode"] not in _RGB_MODES:
        rgb["mode"] = _DEFAULT_RGB["mode"]
    rgb["brightness"] = max(0, min(100, int(rgb["brightness"])))
    rgb["color"] = _normalize_rgb_color(str(rgb["color"]))

    return {
        "mode": rgb["mode"],
        "brightness": rgb["brightness"],
        "color": rgb["color"],
        "modes_supported": list(_RGB_MODES),
        "hardware_apply": "not-implemented",
        "scope": "local-scaffold",
    }


def set_rgb_scaffold(
    model_id: Optional[str],
    *,
    mode: str,
    brightness: Optional[int],
    color: Optional[str],
    path: Optional[str] = None,
) -> Tuple[Path, Dict[str, Any]]:
    mode_value = mode.strip().lower()
    if mode_value not in _RGB_MODES:
        raise RazeCliError(f"Unsupported RGB mode: {mode_value}. Allowed: {', '.join(_RGB_MODES)}")

    if brightness is not None and (brightness < 0 or brightness > 100):
        raise RazeCliError("RGB brightness must be between 0 and 100")

    store_path = resolve_feature_store_path(path)
    store = _read_store(store_path)
    key = _model_key(model_id)
    current = get_rgb_scaffold(model_id=model_id, path=path)

    next_color = current["color"]
    if color is not None:
        next_color = _normalize_rgb_color(color)

    next_brightness = current["brightness"] if brightness is None else int(brightness)
    rgb = {
        "mode": mode_value,
        "brightness": int(next_brightness),
        "color": next_color,
    }

    store["rgb"][key] = rgb
    _write_store(store_path, store)

    payload = dict(rgb)
    payload["modes_supported"] = list(_RGB_MODES)
    payload["hardware_apply"] = "not-implemented"
    payload["scope"] = "local-scaffold"
    return store_path, payload


def get_rgb_presets(model_id: Optional[str], path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    store_path = resolve_feature_store_path(path)
    store = _read_store(store_path)
    key = _model_key(model_id)
    raw = store.get("rgb_presets", {}).get(key, {})
    if not isinstance(raw, dict):
        raw = {}

    merged: Dict[str, Dict[str, Any]] = {}
    for name, preset in _DEFAULT_RGB_PRESETS.items():
        merged[str(name)] = {
            "mode": str(preset.get("mode", "off")),
            "brightness": int(preset.get("brightness", 100)),
            "color": _normalize_rgb_color(str(preset.get("color", "00ff00"))),
        }

    for name, preset in raw.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(preset, dict):
            continue
        mode = str(preset.get("mode", "off")).strip().lower()
        if mode not in _RGB_MODES:
            continue
        try:
            brightness = int(preset.get("brightness", 100))
        except (TypeError, ValueError):
            continue
        if brightness < 0 or brightness > 100:
            continue
        try:
            color = _normalize_rgb_color(str(preset.get("color", "00ff00")))
        except RazeCliError:
            continue
        merged[name.strip()] = {
            "mode": mode,
            "brightness": int(brightness),
            "color": color,
        }
    return merged


def save_rgb_preset(
    model_id: Optional[str],
    *,
    name: str,
    mode: str,
    brightness: int,
    color: str,
    path: Optional[str] = None,
) -> Tuple[Path, Dict[str, Dict[str, Any]]]:
    preset_name = str(name).strip()
    if not preset_name:
        raise RazeCliError("Preset name cannot be empty")

    mode_value = str(mode).strip().lower()
    if mode_value not in _RGB_MODES:
        raise RazeCliError(f"Unsupported RGB mode: {mode_value}. Allowed: {', '.join(_RGB_MODES)}")

    if int(brightness) < 0 or int(brightness) > 100:
        raise RazeCliError("RGB brightness must be between 0 and 100")

    color_value = _normalize_rgb_color(str(color))

    store_path = resolve_feature_store_path(path)
    store = _read_store(store_path)
    key = _model_key(model_id)
    model_store = store["rgb_presets"].setdefault(key, {})
    if not isinstance(model_store, dict):
        model_store = {}
        store["rgb_presets"][key] = model_store

    model_store[preset_name] = {
        "mode": mode_value,
        "brightness": int(brightness),
        "color": color_value,
    }
    _write_store(store_path, store)
    return store_path, get_rgb_presets(model_id=model_id, path=path)


def delete_rgb_preset(
    model_id: Optional[str],
    *,
    name: str,
    path: Optional[str] = None,
) -> Tuple[Path, Dict[str, Dict[str, Any]]]:
    preset_name = str(name).strip()
    if not preset_name:
        raise RazeCliError("Preset name cannot be empty")

    if preset_name in _DEFAULT_RGB_PRESETS:
        raise RazeCliError("Built-in presets cannot be deleted")

    store_path = resolve_feature_store_path(path)
    store = _read_store(store_path)
    key = _model_key(model_id)
    model_store = store.get("rgb_presets", {}).get(key, {})
    if not isinstance(model_store, dict):
        model_store = {}
    model_store.pop(preset_name, None)
    store["rgb_presets"][key] = model_store
    _write_store(store_path, store)
    return store_path, get_rgb_presets(model_id=model_id, path=path)


def get_button_mapping_scaffold(model_id: Optional[str], path: Optional[str] = None) -> Dict[str, Any]:
    store_path = resolve_feature_store_path(path)
    store = _read_store(store_path)
    key = _model_key(model_id)
    schema = _schema_for_model(model_id)

    mapping_raw = store.get("button_mapping", {}).get(key, {})
    if not isinstance(mapping_raw, dict):
        mapping_raw = {}

    mapping = dict(schema["default_mapping"])
    for btn, action in mapping_raw.items():
        if not isinstance(btn, str):
            continue
        if not isinstance(action, str):
            continue
        mapping[btn] = action

    return {
        "mapping": mapping,
        "buttons_supported": list(schema["buttons"]),
        "actions_suggested": list(schema["actions"]),
        "hardware_apply": "not-implemented",
        "scope": "local-scaffold",
    }


def set_button_mapping_scaffold(
    model_id: Optional[str],
    *,
    button: str,
    action: str,
    path: Optional[str] = None,
) -> Tuple[Path, Dict[str, Any]]:
    button_value = button.strip()
    action_value = action.strip()
    if not button_value:
        raise RazeCliError("Button name cannot be empty")
    if not action_value:
        raise RazeCliError("Action cannot be empty")

    schema = _schema_for_model(model_id)
    if schema["strict_buttons"] and button_value not in schema["buttons"]:
        allowed = ", ".join(schema["buttons"])
        raise RazeCliError(f"Unsupported button '{button_value}'. Allowed: {allowed}")

    store_path = resolve_feature_store_path(path)
    store = _read_store(store_path)
    key = _model_key(model_id)
    mapping_store = store["button_mapping"].setdefault(key, {})
    if not isinstance(mapping_store, dict):
        mapping_store = {}
        store["button_mapping"][key] = mapping_store
    mapping_store[button_value] = action_value

    _write_store(store_path, store)
    state = get_button_mapping_scaffold(model_id=model_id, path=path)
    return store_path, state


def reset_button_mapping_scaffold(model_id: Optional[str], path: Optional[str] = None) -> Tuple[Path, Dict[str, Any]]:
    store_path = resolve_feature_store_path(path)
    store = _read_store(store_path)
    key = _model_key(model_id)
    store["button_mapping"][key] = {}
    _write_store(store_path, store)
    state = get_button_mapping_scaffold(model_id=model_id, path=path)
    return store_path, state


def list_button_mapping_actions(model_id: Optional[str]) -> Dict[str, List[str]]:
    schema = _schema_for_model(model_id)
    return {
        "buttons": list(schema["buttons"]),
        "actions": list(schema["actions"]),
    }
