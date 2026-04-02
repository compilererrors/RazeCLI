"""Local scaffold storage for upcoming RGB and button-mapping features."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from razecli.errors import RazeCliError

_DEFAULT_STORE_PATH = Path.home() / ".config" / "razecli" / "feature_scaffolds.json"

_RGB_MODES = ("off", "static", "breathing", "spectrum")
_DEFAULT_RGB = {
    "mode": "off",
    "brightness": 100,
    "color": "00ff00",
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
    "dpi:cycle",
    "dpi:stage:1",
    "dpi:stage:2",
    "dpi:stage:3",
    "poll:125",
    "poll:500",
    "poll:1000",
    "disabled",
)


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
    data.setdefault("button_mapping", {})
    if not isinstance(data["rgb"], dict) or not isinstance(data["button_mapping"], dict):
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
    if model_id == "deathadder-v2-pro":
        return {
            "buttons": list(_DA_V2_PRO_BUTTONS),
            "actions": list(_DA_V2_PRO_ACTIONS),
            "default_mapping": dict(_DA_V2_PRO_DEFAULT_MAPPING),
            "strict_buttons": True,
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
