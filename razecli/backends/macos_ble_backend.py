"""Experimental macOS BLE backend for Razer BT endpoints.

Implements vendor-GATT transactions over macOS CoreBluetooth for Bluetooth PID
endpoints. DA V2 Pro (`0x008E`) is field-validated; additional PIDs from
`ModelSpec.ble_endpoint_product_ids` reuse the same OpenSnek-class key catalog
but remain model-gated (`ble_endpoint_experimental`, RGB mode lists, etc.).
"""

from __future__ import annotations

import os
import platform
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from razecli.backends.base import Backend
from razecli.backends.macos_profiler_backend import MacOSProfilerBackend
from razecli.backends.rawhid_backend import RawHidBackend
from razecli.ble.sync_runner import run_ble_sync
from razecli.errors import BackendUnavailableError, CapabilityUnsupportedError
from razecli.model_registry import ModelRegistry
from razecli.types import DetectedDevice

RAZER_VENDOR_ID = 0x1532
# Empty by default; primary source is ModelSpec.ble_endpoint_product_ids.
DEFAULT_BLE_PRODUCT_IDS = frozenset()
BLE_CAPABILITIES = frozenset({"battery", "dpi", "dpi-stages", "rgb", "button-mapping"})

KEY_BATTERY_RAW_READ = bytes.fromhex("05810001")
KEY_BATTERY_STATUS_READ = bytes.fromhex("05800001")
KEY_DPI_STAGES_READ = bytes.fromhex("0B840100")
KEY_DPI_STAGES_WRITE = bytes.fromhex("0B040100")
DEFAULT_DPI_READ_KEYS = (
    # Prefer the writable/readback family first to keep TUI/CLI edits stable.
    "0b840100",
    "0b840101",
    "0b840000",
    "0b840001",
    # Additional observed families (read-only/variant on some firmwares).
    "0b840300",
    "0b840301",
)
DEFAULT_DPI_WRITE_KEYS = ("0b040100", "0b040101", "0b040300", "0b040301", "0b040000", "0b040001")
# Fast path: merge these reads (full list is slow on real BLE). On DA V2 Pro BT, the
# 0x0300 read family often holds the active onboard-bank table while 0x0100/0x0101 can
# expose a shorter or stale slice — merging only [:2] keys caused 2-stage reads to win
# over 4-stage tables after profile-bank switches. Override with
# RAZECLI_BLE_DPI_READ_SCAN_ALL_KEYS=1 when debugging other 0x84 families.
DPI_STAGE_BLE_READ_MERGE_KEYS: frozenset[bytes] = frozenset(
    {
        bytes.fromhex("0b840100"),
        bytes.fromhex("0b840101"),
        bytes.fromhex("0b840300"),
        bytes.fromhex("0b840301"),
    }
)
# Mirror writes to the same varstore families so edits stick for the active bank.
DPI_STAGE_BLE_WRITE_MIRROR_KEYS: frozenset[bytes] = frozenset(
    {
        bytes.fromhex("0b040100"),
        bytes.fromhex("0b040101"),
        bytes.fromhex("0b040300"),
        bytes.fromhex("0b040301"),
    }
)
DEFAULT_POLL_READ_KEYS = ("00850001", "00850000", "00850100", "0b850100", "0b850000")
DEFAULT_POLL_WRITE_KEYS = ("00050001", "00050000", "00050100", "0b050100", "0b050000")
# Model-level BLE poll-rate allowlist.
# Keep empty by default; add slugs only after verified hardware support.
DEFAULT_BLE_POLL_SUPPORTED_MODELS: Tuple[str, ...] = ()
DEFAULT_BLE_RGB_SUPPORTED_MODES: Tuple[str, ...] = ("off", "static")
DEFAULT_RGB_BRIGHTNESS_READ_KEYS = ("10850101", "10850100")
DEFAULT_RGB_BRIGHTNESS_WRITE_KEYS = ("10050100", "10050101")
KEY_RGB_FRAME_READ = bytes.fromhex("10840000")
KEY_RGB_FRAME_WRITE = bytes.fromhex("10040000")
KEY_RGB_MODE_READ = bytes.fromhex("10830000")
KEY_RGB_MODE_WRITE = bytes.fromhex("10030000")
PRIMARY_VENDOR_READ_CHAR_UUID = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"
BATTERY_LEVEL_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
POLL_RATE_TO_CODE = {
    1000: 0x01,
    500: 0x02,
    125: 0x08,
}
CODE_TO_POLL_RATE = {value: key for key, value in POLL_RATE_TO_CODE.items()}
MAX_DPI_STAGES = 5
MIN_PLAUSIBLE_DPI = 100
MAX_PLAUSIBLE_DPI = 30000
DEFAULT_BLE_NAME_QUERY = "Razer"
RGB_MODES = ("off", "static", "breathing", "breathing-single", "breathing-random", "spectrum")
RGB_MODE_SELECTOR_PAYLOADS: Dict[str, bytes] = {
    # Selector 0x03 appears to be the non-animated family:
    # off when brightness is 0, static when brightness > 0.
    "off": bytes([0x03, 0x00, 0x00, 0x00]),
    "static": bytes([0x03, 0x00, 0x00, 0x00]),
    # Validated in BLE captures for legacy mode selector.
    "spectrum": bytes([0x08, 0x00, 0x00, 0x00]),
    # Legacy selector for breathing family. We send full 10-byte payloads for
    # breathing-single/random, but keep this for compatibility.
    "breathing": bytes([0x02, 0x00, 0x00, 0x00]),
    "breathing-single": bytes([0x02, 0x00, 0x00, 0x00]),
    "breathing-random": bytes([0x02, 0x00, 0x00, 0x00]),
}
RGB_MODE_BY_SELECTOR_CODE: Dict[int, str] = {
    # First byte of legacy 1-byte / u32 mode selector readback.
    0x02: "breathing-random",
    0x04: "spectrum",  # MATRIX_EFFECT_SPECTRUM / classic on some firmware
    0x08: "spectrum",
}

BUTTON_SLOT_BY_NAME = {
    "left_click": 0x01,
    "right_click": 0x02,
    "middle_click": 0x03,
    "side_1": 0x04,
    "side_2": 0x05,
    "dpi_cycle": 0x60,
}

MOUSE_ACTION_ID_BY_NAME: Dict[str, int] = {
    "mouse:left": 0x01,
    "mouse:right": 0x02,
    "mouse:middle": 0x03,
    "mouse:back": 0x04,
    "mouse:forward": 0x05,
    "mouse:scroll-up": 0x09,
    "mouse:scroll-down": 0x0A,
    "mouse:scroll-left": 0x68,
    "mouse:scroll-right": 0x69,
}
MOUSE_ACTION_NAME_BY_ID: Dict[int, str] = {value: key for key, value in MOUSE_ACTION_ID_BY_NAME.items()}
# Packet field default used for turbo actions (decimal 142, hex 0x008E).
DEFAULT_BUTTON_TURBO_RATE = 142

DEFAULT_BUTTON_MAPPING = {
    "left_click": "mouse:left",
    "right_click": "mouse:right",
    "middle_click": "mouse:middle",
    "side_1": "mouse:back",
    "side_2": "mouse:forward",
    "dpi_cycle": "dpi:cycle",
}

BUTTON_ACTIONS = (
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
    "disabled",
)

BLE_BUTTON_DECODE_LAYOUT_RAZER_V1 = "razer-v1"
BLE_BUTTON_DECODE_LAYOUT_COMPACT_16 = "compact-16"
BLE_BUTTON_DECODE_LAYOUT_SLOT_BYTE6 = "slot-byte6"
BLE_BUTTON_DECODE_LAYOUTS = (
    BLE_BUTTON_DECODE_LAYOUT_RAZER_V1,
    BLE_BUTTON_DECODE_LAYOUT_COMPACT_16,
    BLE_BUTTON_DECODE_LAYOUT_SLOT_BYTE6,
)


class MacOSBleBackend(Backend):
    name = "macos-ble"

    def __init__(self) -> None:
        self.last_error = None
        self._supported = platform.system() == "Darwin"
        self._model_registry = ModelRegistry.load()
        self._ble_product_ids = self._load_product_ids()
        self._poll_capability_enabled = self._env_flag("RAZECLI_BLE_POLL_CAP", default=False)
        self._ble_poll_supported_models = self._load_ble_poll_supported_models()
        self._poll_unavailable_targets: Set[str] = set()
        self._rawhid = RawHidBackend()
        self._profiler = MacOSProfilerBackend()
        if not self._supported:
            self.last_error = BackendUnavailableError("macos-ble backend is supported on macOS only")
        elif self._rawhid.last_error is not None and self._profiler.last_error is not None:
            self.last_error = self._rawhid.last_error

    @staticmethod
    def _env_flag(name: str, *, default: bool = False) -> bool:
        raw = str(os.getenv(name, "")).strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_hex_key_list(raw: str, *, default_tokens: Sequence[str]) -> List[bytes]:
        text = str(raw or "").strip()
        if not text:
            tokens = [str(token).strip() for token in default_tokens]
        else:
            tokens = [token.strip() for token in text.split(",")]

        parsed: List[bytes] = []
        seen: Set[bytes] = set()
        for token in tokens:
            cleaned = token.lower().replace("0x", "")
            for separator in (" ", "\t", "\n", "\r", ":", "-", "_"):
                cleaned = cleaned.replace(separator, "")
            if len(cleaned) != 8:
                continue
            try:
                key = bytes.fromhex(cleaned)
            except ValueError:
                continue
            if key in seen:
                continue
            seen.add(key)
            parsed.append(key)
        return parsed

    @staticmethod
    def _parse_pid_list(raw: str) -> List[int]:
        tokens = [token.strip() for token in str(raw or "").split(",")]
        values: List[int] = []
        seen: Set[int] = set()
        for token in tokens:
            if not token:
                continue
            try:
                value = int(token, 16) if token.lower().startswith("0x") else int(token, 10)
            except ValueError:
                continue
            if value < 0 or value > 0xFFFF:
                continue
            if value in seen:
                continue
            seen.add(value)
            values.append(value)
        return values

    def _load_product_ids(self) -> Set[int]:
        from_env = self._parse_pid_list(str(os.getenv("RAZECLI_BLE_PRODUCT_IDS", "")).strip())
        if from_env:
            return set(from_env)
        default_ids: Set[int] = set(DEFAULT_BLE_PRODUCT_IDS)
        for model in self._model_registry.iter():
            raw = tuple(getattr(model, "ble_endpoint_product_ids", ()) or ())
            for item in raw:
                try:
                    pid = int(item)
                except Exception:
                    continue
                if 0 <= pid <= 0xFFFF:
                    default_ids.add(pid)
        return default_ids

    def _model_spec(self, model_id: Optional[str]):
        slug = str(model_id or "").strip().lower()
        if not slug:
            return None
        return self._model_registry.get(slug)

    def _model_ble_multi_profile_table_limited(self, model_id: Optional[str]) -> bool:
        model = self._model_spec(model_id)
        return bool(model and getattr(model, "ble_multi_profile_table_limited", False))

    def _model_ble_button_decode_layouts(self, model_id: Optional[str]) -> Tuple[str, ...]:
        model = self._model_spec(model_id)
        raw_layouts = tuple(getattr(model, "ble_button_decode_layouts", ()) or ()) if model else ()
        normalized: List[str] = []
        for raw in raw_layouts:
            layout = str(raw).strip().lower()
            if layout in BLE_BUTTON_DECODE_LAYOUTS and layout not in normalized:
                normalized.append(layout)
        # Conservative default for unknown/new models: try strict legacy layout first.
        if not normalized:
            return (BLE_BUTTON_DECODE_LAYOUT_RAZER_V1,)
        return tuple(normalized)

    @staticmethod
    def _load_ble_poll_supported_models() -> Set[str]:
        raw = str(os.getenv("RAZECLI_BLE_POLL_SUPPORTED_MODELS", "")).strip()
        if not raw:
            return set(DEFAULT_BLE_POLL_SUPPORTED_MODELS)
        values: Set[str] = set()
        for token in raw.split(","):
            slug = str(token).strip().lower()
            if slug:
                values.add(slug)
        return values

    def _model_supports_ble_poll(self, model_id: Optional[str]) -> bool:
        if self._env_flag("RAZECLI_BLE_POLL_FORCE", default=False):
            return True
        slug = str(model_id or "").strip().lower()
        if not slug:
            return False
        if slug in self._ble_poll_supported_models:
            return True
        model = self._model_registry.get(slug)
        return bool(model and model.ble_poll_rate_supported)

    def _model_supported_ble_rgb_modes(self, model_id: Optional[str]) -> Tuple[str, ...]:
        if self._env_flag("RAZECLI_BLE_RGB_FORCE_ALL_MODES", default=False):
            return tuple(RGB_MODES)

        slug = str(model_id or "").strip().lower()
        model = self._model_registry.get(slug) if slug else None
        raw_modes = tuple(getattr(model, "ble_supported_rgb_modes", ()) or ())

        normalized: List[str] = []
        for raw in raw_modes:
            mode = str(raw).strip().lower()
            if mode in RGB_MODES and mode not in normalized:
                normalized.append(mode)
        if not normalized:
            normalized = list(DEFAULT_BLE_RGB_SUPPORTED_MODES)

        if "off" not in normalized:
            normalized.insert(0, "off")
        if "static" not in normalized:
            normalized.append("static")
        if "breathing" in normalized:
            if "breathing-single" not in normalized:
                normalized.append("breathing-single")
            if "breathing-random" not in normalized:
                normalized.append("breathing-random")
        return tuple(mode for mode in normalized if mode in RGB_MODES)

    def _detect_capabilities(
        self,
        *,
        handle: Optional[Dict[str, object]] = None,
        target_key: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> Set[str]:
        caps = set(BLE_CAPABILITIES)
        blocked_by_handle = bool((handle or {}).get("ble_poll_unavailable", False))
        blocked_by_target = bool(target_key and target_key in self._poll_unavailable_targets)
        if (
            self._poll_capability_enabled
            and self._model_supports_ble_poll(model_id)
            and not blocked_by_handle
            and not blocked_by_target
        ):
            caps.add("poll-rate")
        return caps

    @staticmethod
    def _poll_debug_enabled() -> bool:
        raw = str(os.getenv("RAZECLI_BLE_POLL_DEBUG", "")).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _poll_debug(self, message: str) -> None:
        if self._poll_debug_enabled():
            print(f"[macos-ble poll] {message}", file=sys.stderr)

    @staticmethod
    def _poll_target_key_from_parts(*, bt_address: Optional[str], serial: Optional[str], identifier: str) -> str:
        if bt_address and MacOSBleBackend._is_mac_address(bt_address):
            return str(bt_address).upper()
        if serial and MacOSBleBackend._is_mac_address(serial):
            return str(serial).upper()
        return str(identifier)

    @staticmethod
    def _is_mac_address(value: Optional[str]) -> bool:
        text = str(value or "").strip()
        parts = text.split(":")
        if len(parts) != 6:
            return False
        for part in parts:
            if len(part) != 2:
                return False
            try:
                int(part, 16)
            except ValueError:
                return False
        return True

    @staticmethod
    def _normalize_identifier(
        *,
        vendor_id: int,
        product_id: int,
        serial: Optional[str],
        bt_address: Optional[str],
    ) -> str:
        if bt_address:
            compact_addr = "".join(ch for ch in bt_address if ch.isalnum())
            return f"macos-ble:{vendor_id:04X}:{product_id:04X}:bt:{compact_addr}"

        serial = "".join(ch for ch in (serial or "") if ch.isalnum())
        if serial:
            return f"macos-ble:{vendor_id:04X}:{product_id:04X}:sn:{serial}"
        return f"macos-ble:{vendor_id:04X}:{product_id:04X}"

    def _is_ble_candidate(self, device: DetectedDevice) -> bool:
        if device.vendor_id != RAZER_VENDOR_ID:
            return False
        return device.product_id in self._ble_product_ids

    @staticmethod
    def _copy_handle(device: DetectedDevice) -> Dict[str, object]:
        if isinstance(device.backend_handle, dict):
            return dict(device.backend_handle)
        return {}

    @staticmethod
    def _name_match_score(left: str, right: str) -> int:
        a = (left or "").strip().lower()
        b = (right or "").strip().lower()
        if not a or not b:
            return 0
        if a == b:
            return 100
        score = 0
        for token in ("deathadder", "da v2", "v2 pro", "razer"):
            if token in a and token in b:
                score += 30
        if a in b or b in a:
            score += 15
        return score

    def _profiler_bt_rows(self) -> List[DetectedDevice]:
        rows: List[DetectedDevice] = []
        for device in self._profiler.detect():
            if device.backend != "macos-profiler":
                continue
            if not device.identifier.startswith("macos-bt:"):
                continue
            if device.vendor_id != RAZER_VENDOR_ID:
                continue
            if device.product_id not in self._ble_product_ids:
                continue
            rows.append(device)
        return rows

    def _poll_read_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_POLL_READ_KEYS", "")).strip(),
            default_tokens=DEFAULT_POLL_READ_KEYS,
        )
        pinned_hex = str(handle.get("ble_poll_read_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    def _poll_write_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_POLL_WRITE_KEYS", "")).strip(),
            default_tokens=DEFAULT_POLL_WRITE_KEYS,
        )
        pinned_hex = str(handle.get("ble_poll_write_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    def _dpi_read_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_DPI_READ_KEYS", "")).strip(),
            default_tokens=DEFAULT_DPI_READ_KEYS,
        )
        pinned_hex = str(handle.get("ble_dpi_read_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    def _dpi_write_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_DPI_WRITE_KEYS", "")).strip(),
            default_tokens=DEFAULT_DPI_WRITE_KEYS,
        )
        pinned_hex = str(handle.get("ble_dpi_write_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    def _dpi_read_keys_for_stage_probe(self, handle: Dict[str, object]) -> List[bytes]:
        full = self._dpi_read_keys(handle)
        if MacOSBleBackend._env_flag("RAZECLI_BLE_DPI_READ_SCAN_ALL_KEYS", default=False):
            return full
        merged = [key for key in full if key in DPI_STAGE_BLE_READ_MERGE_KEYS]
        if merged:
            return merged
        return full[: min(2, len(full))]

    def _dpi_write_keys_for_stage_mirror(self, handle: Dict[str, object]) -> List[bytes]:
        full = self._dpi_write_keys(handle)
        if MacOSBleBackend._env_flag("RAZECLI_BLE_DPI_WRITE_ALL_KEYS", default=False):
            return full
        mirror = [key for key in full if key in DPI_STAGE_BLE_WRITE_MIRROR_KEYS]
        if mirror:
            return mirror
        return full[: min(2, len(full))]

    def _rgb_brightness_read_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_RGB_READ_KEYS", "")).strip(),
            default_tokens=DEFAULT_RGB_BRIGHTNESS_READ_KEYS,
        )
        pinned_hex = str(handle.get("ble_rgb_read_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    def _rgb_brightness_write_keys(self, handle: Dict[str, object]) -> List[bytes]:
        keys = self._parse_hex_key_list(
            str(os.getenv("RAZECLI_BLE_RGB_WRITE_KEYS", "")).strip(),
            default_tokens=DEFAULT_RGB_BRIGHTNESS_WRITE_KEYS,
        )
        pinned_hex = str(handle.get("ble_rgb_write_key") or "").strip().lower()
        if pinned_hex:
            try:
                pinned = bytes.fromhex(pinned_hex)
            except ValueError:
                pinned = b""
            if len(pinned) == 4:
                ordered = [pinned]
                ordered.extend(key for key in keys if key != pinned)
                return ordered
        return keys

    @staticmethod
    def _normalize_color_hex(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text.startswith("#"):
            text = text[1:]
        if len(text) != 6 or any(ch not in "0123456789abcdef" for ch in text):
            raise CapabilityUnsupportedError("RGB color must be a 6-digit hex value, for example 00ff88")
        return text

    @staticmethod
    def _rgb_percent_to_u8(value: int) -> int:
        clamped = max(0, min(100, int(value)))
        return int(round((clamped / 100.0) * 255.0))

    @staticmethod
    def _rgb_u8_to_percent(value: int) -> int:
        clamped = max(0, min(255, int(value)))
        return int(round((clamped / 255.0) * 100.0))

    @staticmethod
    def _slot_from_button_name(button: str) -> int:
        slot = BUTTON_SLOT_BY_NAME.get(str(button).strip())
        if slot is None:
            supported = ", ".join(sorted(BUTTON_SLOT_BY_NAME))
            raise CapabilityUnsupportedError(f"Unsupported button '{button}'. Supported: {supported}")
        return int(slot)

    @staticmethod
    def _button_name_from_slot(slot: int) -> Optional[str]:
        for name, value in BUTTON_SLOT_BY_NAME.items():
            if int(value) == int(slot):
                return name
        return None

    @staticmethod
    def _parse_int_token(token: str, *, field: str, minimum: int, maximum: int) -> int:
        text = str(token).strip().lower()
        if not text:
            raise CapabilityUnsupportedError(f"{field} cannot be empty")
        try:
            value = int(text, 16) if text.startswith("0x") else int(text, 10)
        except ValueError as exc:
            raise CapabilityUnsupportedError(
                f"Invalid {field} '{token}'. Use decimal or 0x-prefixed hex."
            ) from exc
        if value < int(minimum) or value > int(maximum):
            raise CapabilityUnsupportedError(
                f"Invalid {field} '{token}'. Allowed range: {minimum}-{maximum}."
            )
        return int(value)

    @staticmethod
    def _parse_turbo_rate(token: Optional[str]) -> int:
        if token is None or not str(token).strip():
            return int(DEFAULT_BUTTON_TURBO_RATE)
        return MacOSBleBackend._parse_int_token(
            str(token),
            field="turbo rate",
            minimum=1,
            maximum=0x00FF,
        )

    @staticmethod
    def _build_ble_button_payload(slot: int, action: str) -> bytes:
        action_value = str(action).strip().lower()
        mouse_button_id = MOUSE_ACTION_ID_BY_NAME.get(action_value)
        if mouse_button_id is not None:
            return bytes([0x01, slot, 0x00, 0x01, 0x01, int(mouse_button_id), 0x00, 0x00, 0x00, 0x00])
        if action_value == "dpi:cycle":
            return bytes([0x01, slot, 0x00, 0x06, 0x01, 0x06, 0x00, 0x00, 0x00, 0x00])
        if action_value.startswith("keyboard:"):
            key_token = action_value[len("keyboard:") :].strip()
            hid_key = MacOSBleBackend._parse_int_token(
                key_token,
                field="keyboard HID code",
                minimum=0,
                maximum=0x00FF,
            )
            return bytes([0x01, slot, 0x00, 0x02, 0x02, 0x00, int(hid_key), 0x00, 0x00, 0x00])
        if action_value.startswith("keyboard-turbo:"):
            rest = action_value[len("keyboard-turbo:") :].strip()
            key_token, rate_token = (rest.split(":", 1) + [None])[:2]
            hid_key = MacOSBleBackend._parse_int_token(
                key_token,
                field="keyboard HID code",
                minimum=0,
                maximum=0x00FF,
            )
            turbo_rate = MacOSBleBackend._parse_turbo_rate(rate_token)
            return bytes(
                [0x01, slot, 0x00, 0x0D, 0x04, 0x00, int(hid_key), 0x00, int(turbo_rate & 0xFF), int((turbo_rate >> 8) & 0xFF)]
            )
        if action_value.startswith("mouse-turbo:"):
            rest = action_value[len("mouse-turbo:") :].strip()
            mouse_action = rest
            rate_token: Optional[str] = None
            if rest not in MOUSE_ACTION_ID_BY_NAME and ":" in rest:
                candidate_action, candidate_rate = rest.rsplit(":", 1)
                if candidate_action in MOUSE_ACTION_ID_BY_NAME:
                    mouse_action = candidate_action
                    rate_token = candidate_rate
            mouse_button_id = MOUSE_ACTION_ID_BY_NAME.get(mouse_action)
            if mouse_button_id is None:
                allowed = ", ".join(sorted(MOUSE_ACTION_ID_BY_NAME))
                raise CapabilityUnsupportedError(
                    f"Unsupported mouse-turbo action '{rest}'. Supported mouse actions: {allowed}"
                )
            turbo_rate = MacOSBleBackend._parse_turbo_rate(rate_token)
            p0 = ((int(mouse_button_id) - 1) << 8) | 0x0003
            return bytes(
                [
                    0x01,
                    slot,
                    0x00,
                    0x0E,
                    int(p0 & 0xFF),
                    int((p0 >> 8) & 0xFF),
                    int(turbo_rate & 0xFF),
                    int((turbo_rate >> 8) & 0xFF),
                    0x00,
                    0x00,
                ]
            )
        if action_value == "disabled":
            # Layer clear/default payload observed in BLE captures.
            return bytes([0x01, slot, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        raise CapabilityUnsupportedError(
            f"Unsupported action '{action}'. Supported: {', '.join(BUTTON_ACTIONS)}"
        )

    @staticmethod
    def _decode_ble_button_payload_razer_v1(slot: int, payload: bytes) -> Optional[str]:
        if len(payload) < 10:
            return None
        if int(payload[0]) != 0x01 or int(payload[1]) != int(slot):
            return None

        action_class = int(payload[3])
        p0 = int(payload[4]) | (int(payload[5]) << 8)
        p1 = int(payload[6]) | (int(payload[7]) << 8)
        p2 = int(payload[8]) | (int(payload[9]) << 8)

        if action_class == 0x00:
            return "disabled"
        if action_class == 0x01 and (p0 & 0x00FF) == 0x01:
            button_id = int((p0 >> 8) & 0x00FF)
            return MOUSE_ACTION_NAME_BY_ID.get(button_id)
        if action_class == 0x06 and p0 == 0x0601:
            return "dpi:cycle"
        if action_class == 0x02 and p0 == 0x0002:
            hid_key = int(p1 & 0x00FF)
            return f"keyboard:0x{hid_key:02x}"
        if action_class == 0x0D and p0 == 0x0004:
            hid_key = int(p1 & 0x00FF)
            turbo_rate = int(p2 & 0xFFFF)
            return f"keyboard-turbo:0x{hid_key:02x}:{turbo_rate}"
        if action_class == 0x0E and (p0 & 0x00FF) == 0x03:
            button_id = int(((p0 >> 8) & 0x00FF) + 1)
            mouse_action = MOUSE_ACTION_NAME_BY_ID.get(button_id)
            if mouse_action is None:
                return None
            turbo_rate = int(p1 & 0xFFFF)
            return f"mouse-turbo:{mouse_action}:{turbo_rate}"
        return None

    @staticmethod
    def _decode_ble_button_payload_compact_16(slot: int, payload: bytes) -> Optional[str]:
        # Compact 16-byte layout observed on DA V2 Pro BLE reads.
        # Example side_1/back: 04000101010104040000000000000000
        # action id is byte 6, slot mirrors at byte 7.
        if len(payload) < 8:
            return None
        if int(payload[0]) != int(slot):
            return None
        if len(payload) >= 8 and int(payload[7]) != int(slot):
            return None
        action_id = int(payload[6])
        if action_id == 0x00:
            return "disabled"
        return MOUSE_ACTION_NAME_BY_ID.get(action_id)

    @staticmethod
    def _decode_ble_button_payload_slot_byte6(slot: int, payload: bytes) -> Optional[str]:
        if len(payload) < 8 or int(payload[0]) != int(slot):
            return None
        action_id = int(payload[6])
        if action_id == 0x00:
            return "disabled"
        return MOUSE_ACTION_NAME_BY_ID.get(action_id)

    @staticmethod
    def _decode_ble_button_payload(
        slot: int,
        payload: bytes,
        *,
        layouts: Optional[Sequence[str]] = None,
    ) -> Optional[str]:
        ordered = tuple(str(item).strip().lower() for item in (layouts or BLE_BUTTON_DECODE_LAYOUTS) if str(item).strip())
        for layout in ordered:
            if layout == BLE_BUTTON_DECODE_LAYOUT_RAZER_V1:
                decoded = MacOSBleBackend._decode_ble_button_payload_razer_v1(slot, payload)
            elif layout == BLE_BUTTON_DECODE_LAYOUT_COMPACT_16:
                decoded = MacOSBleBackend._decode_ble_button_payload_compact_16(slot, payload)
            elif layout == BLE_BUTTON_DECODE_LAYOUT_SLOT_BYTE6:
                decoded = MacOSBleBackend._decode_ble_button_payload_slot_byte6(slot, payload)
            else:
                continue
            if decoded is not None:
                return decoded
        return None

    @staticmethod
    def _rgb_mode_selector_payload(mode: str) -> Optional[bytes]:
        return RGB_MODE_SELECTOR_PAYLOADS.get(str(mode).strip().lower())

    @staticmethod
    def _normalize_rgb_mode_for_write(mode: str) -> str:
        mode_value = str(mode).strip().lower()
        if mode_value == "breathing":
            # Default breathing to single-color behavior.
            return "breathing-single"
        return mode_value

    @staticmethod
    def _rgb_mode_supported_over_ble(*, requested_mode: str, supported_modes: Sequence[str]) -> bool:
        mode_value = str(requested_mode).strip().lower()
        supported = {str(item).strip().lower() for item in supported_modes if str(item).strip()}
        if mode_value in supported:
            return True
        if mode_value == "breathing" and (
            "breathing-single" in supported or "breathing-random" in supported
        ):
            return True
        if mode_value in {"breathing-single", "breathing-random"} and "breathing" in supported:
            return True
        return False

    @staticmethod
    def _rgb_mode_write_payload(*, mode: str, color_hex: str) -> Optional[bytes]:
        mode_value = MacOSBleBackend._normalize_rgb_mode_for_write(mode)
        if mode_value == "breathing-single":
            rgb = bytes.fromhex(color_hex)
            # Matches observed 10-byte readback family on 10830000:
            # [mode=0x02, variant=0x01, 0x00, color_count=0x01, R,G,B, 0x00,0x00,0x00]
            return bytes([0x02, 0x01, 0x00, 0x01, rgb[0], rgb[1], rgb[2], 0x00, 0x00, 0x00])
        if mode_value == "breathing-random":
            rgb = bytes.fromhex(color_hex)
            # Keep seed color in payload while selecting random breathing profile.
            return bytes([0x02, 0x00, 0x00, 0x00, 0x00, rgb[0], rgb[1], rgb[2], 0x00, 0x00])
        # V3 Pro–class BLE (OpenSnek): one 10-byte row to 10030000 — zone 0x01 (scroll), effect in byte 3
        # (0x01 static, 0x04 spectrum per matrix-style ids). Legacy 4-byte 0x03 + 1084 frame alone
        # did not apply static on DA V2 Pro BT; 4-byte 0x08 for spectrum was ignored → stayed static green.
        if mode_value == "static":
            rgb = bytes.fromhex(color_hex)
            return bytes([0x01, 0x00, 0x00, 0x01, rgb[0], rgb[1], rgb[2], 0x00, 0x00, 0x00])
        if mode_value == "spectrum":
            return bytes([0x01, 0x00, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        if mode_value == "off":
            return bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        return MacOSBleBackend._rgb_mode_selector_payload(mode_value)

    @staticmethod
    def _rgb_modes_equivalent_for_verify(*, expected: str, actual: str) -> bool:
        expected_mode = str(expected).strip().lower()
        actual_mode = str(actual).strip().lower()
        if expected_mode == actual_mode:
            return True
        if expected_mode in {"breathing-single", "breathing-random"} and actual_mode == "breathing":
            return True
        if expected_mode == "breathing" and actual_mode in {"breathing-single", "breathing-random"}:
            return True
        return False

    @staticmethod
    def _rgb_color_from_payload(payload: bytes) -> Optional[str]:
        if len(payload) >= 8 and int(payload[0]) == 0x04:
            r = int(payload[5])
            g = int(payload[6])
            b = int(payload[7])
            return f"{r:02x}{g:02x}{b:02x}"
        if len(payload) == 4 and int(payload[0]) == 0x04:
            r = int(payload[1])
            g = int(payload[2])
            b = int(payload[3])
            return f"{r:02x}{g:02x}{b:02x}"
        if len(payload) == 3:
            r = int(payload[0])
            g = int(payload[1])
            b = int(payload[2])
            return f"{r:02x}{g:02x}{b:02x}"
        return None

    @staticmethod
    def _rgb_mode_color_from_1083_zone_payload(
        payload: bytes,
        *,
        brightness_percent: int,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Parse 10-byte ``10:83`` zone state (OpenSnek V3 Pro–class BLE).

        Layout: ``[zone][0][0][effect][R][G][B][pad...]`` — effect at index 3 matches
        OpenRazer matrix-style ids (spectrum=0x04, breathing=0x03, static=0x06, …).
        """
        if len(payload) < 10:
            return None, None
        try:
            effect = int(payload[3])
            r, g, b = int(payload[4]), int(payload[5]), int(payload[6])
        except (IndexError, ValueError):
            return None, None
        if any(component < 0 or component > 255 for component in (r, g, b)):
            return None, None
        color = f"{r:02x}{g:02x}{b:02x}"
        if effect in (0x04, 0x08):
            return "spectrum", color
        if effect in (0x02, 0x03):
            return "breathing-single", color
        if effect in (0x00, 0x01, 0x06):
            if int(brightness_percent) <= 0:
                return "off", color
            return "static", color
        return None, None

    @staticmethod
    def _rgb_mode_from_selector_payload(payload: bytes, *, brightness_percent: int) -> Optional[str]:
        if not payload:
            return None
        first = int(payload[0])
        if first in {0x00, 0x03}:
            return "off" if int(brightness_percent) <= 0 else "static"
        if first == 0x02:
            if len(payload) >= 2:
                variant = int(payload[1])
                if variant == 0x01:
                    return "breathing-single"
                if variant == 0x00:
                    return "breathing-random"
            return "breathing"
        mapped = RGB_MODE_BY_SELECTOR_CODE.get(first)
        if mapped is not None:
            return str(mapped)
        return None

    @staticmethod
    def _vendor_decode_is_success(result: Dict[str, object]) -> bool:
        vendor = result.get("vendor_decode")
        if not isinstance(vendor, dict):
            return True
        status_raw = str(vendor.get("status") or "").strip().lower()
        status_code_raw = vendor.get("status_code")
        status_code: Optional[int] = None
        try:
            status_code = int(status_code_raw) if status_code_raw is not None else None
        except Exception:
            status_code = None
        if status_code is not None:
            return int(status_code) == 2
        if status_raw:
            return status_raw == "success"
        return True

    @staticmethod
    def _read_confidence_overall(levels: Sequence[str]) -> str:
        normalized = [str(level).strip().lower() for level in levels if str(level).strip()]
        if not normalized:
            return "unknown"
        verified_count = sum(level == "verified" for level in normalized)
        if verified_count == len(normalized):
            return "verified"
        if verified_count > 0:
            return "mixed"
        return "inferred"

    @staticmethod
    def _decode_poll_rate_payload(payload: bytes) -> Optional[int]:
        if not payload:
            return None

        # Some BLE paths prepend a small header/varstore byte before the poll code.
        if len(payload) >= 2 and int(payload[0]) in {0x00, 0x01, 0x02}:
            prefixed = CODE_TO_POLL_RATE.get(int(payload[1]))
            if prefixed is not None:
                return int(prefixed)

        candidates: List[int] = []
        if payload:
            candidates.append(int(payload[0]))
            candidates.append(int(payload[-1]))
        candidates.extend(int(value) for value in payload[:4])

        for code in candidates:
            rate = CODE_TO_POLL_RATE.get(code)
            if rate is not None:
                return int(rate)

        if len(payload) >= 2:
            little = int.from_bytes(payload[:2], byteorder="little", signed=False)
            big = int.from_bytes(payload[:2], byteorder="big", signed=False)
            if little in POLL_RATE_TO_CODE:
                return int(little)
            if big in POLL_RATE_TO_CODE:
                return int(big)

        if len(payload) >= 4:
            little_32 = int.from_bytes(payload[:4], byteorder="little", signed=False)
            big_32 = int.from_bytes(payload[:4], byteorder="big", signed=False)
            if little_32 in POLL_RATE_TO_CODE:
                return int(little_32)
            if big_32 in POLL_RATE_TO_CODE:
                return int(big_32)
        return None

    @staticmethod
    def _poll_write_payload_candidates(hz: int) -> List[bytes]:
        code = POLL_RATE_TO_CODE[int(hz)]
        values = [
            bytes([code]),
            bytes([code, 0x00]),
            int(hz).to_bytes(2, byteorder="little", signed=False),
            int(hz).to_bytes(2, byteorder="big", signed=False),
            bytes([code, 0x00, 0x00, 0x00]),
            int(hz).to_bytes(4, byteorder="little", signed=False),
        ]
        unique: List[bytes] = []
        seen: Set[bytes] = set()
        for payload in values:
            if payload in seen:
                continue
            seen.add(payload)
            unique.append(payload)
        return unique

    def _match_profiler_address(self, device: DetectedDevice, profiler_rows: Sequence[DetectedDevice]) -> Optional[str]:
        best_score = -1
        best_address: Optional[str] = None
        for row in profiler_rows:
            address = row.serial if self._is_mac_address(row.serial) else None
            if not address:
                continue
            score = 0
            if row.product_id == device.product_id:
                score += 50
            score += self._name_match_score(device.name, row.name)
            if score > best_score:
                best_score = score
                best_address = address
        return best_address

    @staticmethod
    def _backend_timeout() -> float:
        raw = str(os.getenv("RAZECLI_BLE_BACKEND_TIMEOUT", "12")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 12.0
        return max(3.0, min(45.0, value))

    @staticmethod
    def _response_timeout() -> float:
        raw = str(os.getenv("RAZECLI_BLE_BACKEND_RESPONSE_TIMEOUT", "1.5")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 1.0
        return max(0.2, min(4.0, value))

    @staticmethod
    def _dpi_read_attempts() -> int:
        raw = str(os.getenv("RAZECLI_BLE_DPI_READ_ATTEMPTS", "3")).strip()
        try:
            value = int(raw)
        except ValueError:
            value = 3
        return max(1, min(8, value))

    @staticmethod
    def _dpi_read_retry_delay() -> float:
        raw = str(os.getenv("RAZECLI_BLE_DPI_READ_RETRY_DELAY", "0.18")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 0.18
        return max(0.0, min(1.5, value))

    @staticmethod
    def _poll_read_attempts() -> int:
        raw = str(os.getenv("RAZECLI_BLE_POLL_READ_ATTEMPTS", "1")).strip()
        try:
            value = int(raw)
        except ValueError:
            value = 1
        return max(1, min(8, value))

    @staticmethod
    def _poll_read_retry_delay() -> float:
        raw = str(os.getenv("RAZECLI_BLE_POLL_READ_RETRY_DELAY", "0.12")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 0.12
        return max(0.0, min(1.5, value))

    @staticmethod
    def _rgb_write_attempts() -> int:
        raw = str(os.getenv("RAZECLI_BLE_RGB_WRITE_ATTEMPTS", "2")).strip()
        try:
            value = int(raw)
        except ValueError:
            value = 2
        return max(1, min(6, value))

    @staticmethod
    def _rgb_write_retry_delay() -> float:
        raw = str(os.getenv("RAZECLI_BLE_RGB_WRITE_RETRY_DELAY", "0.12")).strip()
        try:
            value = float(raw)
        except ValueError:
            value = 0.12
        return max(0.0, min(1.5, value))

    @staticmethod
    def _rgb_verify_write() -> bool:
        return MacOSBleBackend._env_flag("RAZECLI_BLE_RGB_VERIFY_WRITE", default=True)

    def _detect_from_rawhid(self, profiler_rows: Sequence[DetectedDevice]) -> List[DetectedDevice]:
        devices: List[DetectedDevice] = []
        for device in self._rawhid.detect():
            if not self._is_ble_candidate(device):
                continue

            handle = self._copy_handle(device)
            handle["backend"] = self.name
            bt_address = self._match_profiler_address(device, profiler_rows)
            if bt_address:
                handle["bt_address"] = bt_address
            target_key = self._poll_target_key_from_parts(
                bt_address=bt_address,
                serial=device.serial,
                identifier=device.identifier,
            )

            devices.append(
                DetectedDevice(
                    identifier=self._normalize_identifier(
                        vendor_id=device.vendor_id,
                        product_id=device.product_id,
                        serial=device.serial,
                        bt_address=bt_address,
                    ),
                    name=device.name,
                    vendor_id=device.vendor_id,
                    product_id=device.product_id,
                    backend=self.name,
                    serial=bt_address or device.serial,
                    model_id=device.model_id,
                    model_name=device.model_name,
                    capabilities=self._detect_capabilities(
                        handle=handle,
                        target_key=target_key,
                        model_id=device.model_id,
                    ),
                    backend_handle=handle,
                )
            )
        return devices

    def _detect_from_profiler_only(self, profiler_rows: Sequence[DetectedDevice]) -> List[DetectedDevice]:
        devices: List[DetectedDevice] = []
        for row in profiler_rows:
            bt_address = row.serial if self._is_mac_address(row.serial) else None
            handle = self._copy_handle(row)
            handle["backend"] = self.name
            if bt_address:
                handle["bt_address"] = bt_address
            target_key = self._poll_target_key_from_parts(
                bt_address=bt_address,
                serial=row.serial,
                identifier=row.identifier,
            )

            devices.append(
                DetectedDevice(
                    identifier=self._normalize_identifier(
                        vendor_id=row.vendor_id,
                        product_id=row.product_id,
                        serial=row.serial,
                        bt_address=bt_address,
                    ),
                    name=row.name or "Razer Bluetooth device",
                    vendor_id=row.vendor_id,
                    product_id=row.product_id,
                    backend=self.name,
                    serial=bt_address or row.serial,
                    model_id=row.model_id,
                    model_name=row.model_name,
                    capabilities=self._detect_capabilities(
                        handle=handle,
                        target_key=target_key,
                        model_id=row.model_id,
                    ),
                    backend_handle=handle,
                )
            )
        return devices

    def detect(self) -> List[DetectedDevice]:
        if not self._supported:
            return []

        profiler_rows = self._profiler_bt_rows()
        devices = self._detect_from_rawhid(profiler_rows)
        if devices:
            return devices
        return self._detect_from_profiler_only(profiler_rows)

    def _device_handle(self, device: DetectedDevice) -> Dict[str, object]:
        if isinstance(device.backend_handle, dict):
            return device.backend_handle
        handle: Dict[str, object] = {}
        device.backend_handle = handle
        return handle

    def _poll_target_key(self, device: DetectedDevice) -> str:
        handle = self._device_handle(device)
        bt_address = str(handle.get("bt_address") or "").strip()
        return self._poll_target_key_from_parts(
            bt_address=bt_address,
            serial=device.serial,
            identifier=device.identifier,
        )

    @staticmethod
    def _poll_diagnostic_preview(rows: Sequence[str], *, limit: int = 10) -> str:
        if not rows:
            return "-"
        return ", ".join(str(item) for item in rows[-max(1, int(limit)) :])

    def _resolve_target(self, device: DetectedDevice) -> Tuple[Optional[str], str]:
        handle = self._device_handle(device)
        bt_address = str(handle.get("bt_address") or "").strip()
        if self._is_mac_address(bt_address):
            return bt_address, device.name or DEFAULT_BLE_NAME_QUERY
        if self._is_mac_address(device.serial):
            return str(device.serial), device.name or DEFAULT_BLE_NAME_QUERY
        return None, device.name or device.model_name or DEFAULT_BLE_NAME_QUERY

    @staticmethod
    def _vendor_path_from_handle(handle: Dict[str, object]) -> Optional[Dict[str, object]]:
        service_uuid = str(handle.get("ble_service_uuid") or "").strip().lower()
        write_char_uuid = str(handle.get("ble_write_char_uuid") or "").strip().lower()
        read_chars_raw = handle.get("ble_read_char_uuids")
        if not service_uuid or not write_char_uuid:
            return None
        if not isinstance(read_chars_raw, list) or not read_chars_raw:
            return None
        read_char_uuids = [str(item).strip().lower() for item in read_chars_raw if str(item).strip()]
        if not read_char_uuids:
            return None
        return {
            "service_uuid": service_uuid,
            "write_char_uuid": write_char_uuid,
            "read_char_uuids": read_char_uuids,
        }

    @staticmethod
    def _store_vendor_path(handle: Dict[str, object], path: Dict[str, object]) -> None:
        handle["ble_service_uuid"] = str(path.get("service_uuid") or "").strip().lower()
        handle["ble_write_char_uuid"] = str(path.get("write_char_uuid") or "").strip().lower()
        read_chars = path.get("read_char_uuids")
        if isinstance(read_chars, list):
            handle["ble_read_char_uuids"] = [str(item).strip().lower() for item in read_chars if str(item).strip()]

    @staticmethod
    def _clear_vendor_path(handle: Dict[str, object]) -> None:
        handle.pop("ble_service_uuid", None)
        handle.pop("ble_write_char_uuid", None)
        handle.pop("ble_read_char_uuids", None)

    @staticmethod
    def _missing_vendor_path_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "gatt service" in text and "not found" in text
        ) or (
            "gatt write-char" in text and "not found" in text
        ) or (
            "was not found" in text and "gatt" in text
        ) or (
            "hittades inte" in text and "gatt" in text
        )

    def _vendor_call(
        self,
        *,
        device: DetectedDevice,
        key: bytes,
        value_payload: Optional[bytes] = None,
    ) -> Dict[str, object]:
        from razecli.ble_probe import (
            DEFAULT_RAZER_BT_READ_CHAR_UUIDS,
            DEFAULT_RAZER_BT_SERVICE_UUID,
            DEFAULT_RAZER_BT_WRITE_CHAR_UUID,
            ble_vendor_transceive,
            discover_vendor_gatt_path,
        )

        address, name_query = self._resolve_target(device)
        timeout = self._backend_timeout()
        response_timeout = self._response_timeout()
        handle = self._device_handle(device)
        pinned_path = self._vendor_path_from_handle(handle)

        def _tx(path: Optional[Dict[str, object]]) -> Dict[str, object]:
            kwargs: Dict[str, object] = {}
            if path:
                kwargs = {
                    "service_uuid": str(path.get("service_uuid") or DEFAULT_RAZER_BT_SERVICE_UUID),
                    "write_char_uuid": str(path.get("write_char_uuid") or DEFAULT_RAZER_BT_WRITE_CHAR_UUID),
                    "read_char_uuids": list(path.get("read_char_uuids") or DEFAULT_RAZER_BT_READ_CHAR_UUIDS),
                }
            return ble_vendor_transceive(
                address=address,
                name_query=name_query,
                timeout=timeout,
                key=key,
                value_payload=value_payload,
                response_timeout=response_timeout,
                write_with_response=True,
                notify_enabled=True,
                **kwargs,
            )

        try:
            return _tx(pinned_path)
        except Exception as first_exc:
            if pinned_path is None and not self._missing_vendor_path_error(first_exc):
                raise

            try:
                discovered = discover_vendor_gatt_path(
                    address=address,
                    name_query=name_query,
                    timeout=min(timeout, 10.0),
                )
            except Exception:
                raise first_exc

            self._store_vendor_path(handle, discovered)
            return _tx(discovered)

    @staticmethod
    def _select_best_dpi_stages_payload(
        result: Dict[str, object], key: bytes, vendor_payload: bytes
    ) -> bytes:
        """Pick vendor/notify/read candidate that parses to the richest stage table.

        Some firmwares put a compact single-stage snapshot in ``payload_hex`` while the
        full profile list arrives only via notify chunks or a primary GATT read row.
        Preferring the parse with the **most stages** keeps TUI refresh and dpi-stages
        in sync with on-device cycling after writes that pin alternate 0x0B84 families.
        """
        _ = key
        candidates: List[bytes] = []
        if vendor_payload:
            candidates.append(vendor_payload)
        notify_inferred = MacOSBleBackend._infer_payload_from_notify_rows(result)
        if notify_inferred:
            candidates.append(notify_inferred)
        read_inferred = MacOSBleBackend._infer_payload_from_read_rows(result)
        if read_inferred:
            candidates.append(read_inferred)

        best_blob: Optional[bytes] = None
        best_score: Optional[Tuple[int, int]] = None

        for cand in candidates:
            try:
                _active, stages, _ids, _marker = MacOSBleBackend._parse_stages_payload(cand)
            except Exception:
                continue
            score = (len(stages), len(cand))
            if best_score is None or score > best_score:
                best_score = score
                best_blob = cand

        if best_blob is not None:
            return best_blob

        if len(vendor_payload) >= 2:
            return vendor_payload
        if notify_inferred is not None:
            return notify_inferred
        if read_inferred is not None:
            return read_inferred
        return vendor_payload

    @staticmethod
    def _decode_payload_bytes(result: Dict[str, object], *, key: bytes) -> bytes:
        vendor = result.get("vendor_decode")
        if not isinstance(vendor, dict):
            raise CapabilityUnsupportedError(f"No vendor response for key {key.hex()}")
        payload_hex = str(vendor.get("payload_hex") or "").strip()
        payload = b""
        if payload_hex:
            try:
                payload = bytes.fromhex(payload_hex)
            except ValueError as exc:
                raise CapabilityUnsupportedError(
                    f"Invalid payload_hex in vendor response for key {key.hex()}: {payload_hex}"
                ) from exc

        if len(key) == 4 and int(key[0]) == 0x0B and int(key[1]) == 0x84:
            return MacOSBleBackend._select_best_dpi_stages_payload(result, key, payload)

        if len(payload) >= 2:
            return payload

        notify_inferred = MacOSBleBackend._infer_payload_from_notify_rows(result)
        if notify_inferred is not None:
            return notify_inferred

        # Read-row fallback is only valid for DPI stage responses.
        # Accept all known 0x0B84 read families, not only 0B840100.
        if len(key) == 4 and int(key[0]) == 0x0B and int(key[1]) == 0x84:
            inferred = MacOSBleBackend._infer_payload_from_read_rows(result)
            if inferred is not None:
                return inferred
        return payload

    @staticmethod
    def _infer_payload_from_notify_rows(result: Dict[str, object]) -> Optional[bytes]:
        notify = result.get("notify")
        if not isinstance(notify, list) or not notify:
            return None

        vendor_req = result.get("vendor_request")
        request_id: Optional[int] = None
        if isinstance(vendor_req, dict):
            raw = vendor_req.get("request_id")
            if isinstance(raw, int):
                request_id = int(raw) & 0xFF

        rows: List[bytes] = []
        for row in notify:
            if not isinstance(row, dict):
                continue
            char_uuid = str(row.get("char_uuid") or "").strip().lower()
            if char_uuid and char_uuid != PRIMARY_VENDOR_READ_CHAR_UUID:
                continue
            value_hex = str(row.get("value_hex") or "").strip()
            if not value_hex:
                continue
            try:
                data = bytes.fromhex(value_hex)
            except ValueError:
                continue
            if data:
                rows.append(data)

        if not rows:
            return None

        def _is_header(blob: bytes) -> bool:
            if len(blob) < 2:
                return False
            if request_id is not None and int(blob[0]) != request_id:
                return False
            return True

        for index, header in enumerate(rows):
            if not _is_header(header):
                continue
            payload_len = int(header[1]) if len(header) >= 2 else 0
            chunks: List[bytes] = []
            if len(header) > 8:
                chunks.append(header[8:])

            for nxt in rows[index + 1 :]:
                if _is_header(nxt):
                    break
                chunks.append(nxt)

            payload = b"".join(chunks)
            if payload_len > 0:
                if len(payload) >= payload_len:
                    return payload[:payload_len]
                continue
            if payload:
                return payload

        return None

    @staticmethod
    def _infer_payload_from_read_rows(result: Dict[str, object]) -> Optional[bytes]:
        reads = result.get("reads")
        if not isinstance(reads, list):
            return None

        candidates: List[bytes] = []
        for row in reads:
            if not isinstance(row, dict):
                continue
            char_uuid = str(row.get("char_uuid") or "").strip().lower()
            if char_uuid and char_uuid != PRIMARY_VENDOR_READ_CHAR_UUID:
                continue
            value_hex = str(row.get("value_hex") or "").strip()
            if not value_hex:
                continue
            try:
                value = bytes.fromhex(value_hex)
            except ValueError:
                continue
            if not value:
                continue
            candidates.append(value)

        def _slice_candidate(blob: bytes) -> Optional[bytes]:
            if len(blob) >= 2:
                count = int(blob[1])
                if 1 <= count <= MAX_DPI_STAGES:
                    needed = 2 + (7 * count)
                    if len(blob) >= needed:
                        return blob[:needed]

            if len(blob) >= 3:
                count = int(blob[2])
                if 1 <= count <= MAX_DPI_STAGES:
                    needed = 3 + (7 * count)
                    if len(blob) >= needed:
                        return blob[:needed]
            return None

        for blob in candidates:
            exact = _slice_candidate(blob)
            if exact is not None:
                return exact
            if len(blob) > 1:
                trimmed = _slice_candidate(blob[1:])
                if trimmed is not None:
                    return trimmed

        return None

    @staticmethod
    def _parse_stages_payload(payload: bytes) -> Tuple[int, List[Tuple[int, int]], List[int], int]:
        if len(payload) < 2:
            raise CapabilityUnsupportedError("Invalid DPI stage payload (too short)")

        # Some hosts/devices occasionally return a compact single-stage payload over BLE.
        # Accept it as a fallback to avoid hard failures when full stage table is missing.
        def _parse_compact_single_stage(blob: bytes) -> Optional[Tuple[int, List[Tuple[int, int]], List[int], int]]:
            def _validate_single_stage(
                *,
                active_raw: int,
                dpi_x: int,
                dpi_y: int,
                marker: int,
            ) -> Optional[Tuple[int, List[Tuple[int, int]], List[int], int]]:
                if (
                    dpi_x < MIN_PLAUSIBLE_DPI
                    or dpi_y < MIN_PLAUSIBLE_DPI
                    or dpi_x > MAX_PLAUSIBLE_DPI
                    or dpi_y > MAX_PLAUSIBLE_DPI
                ):
                    return None
                if dpi_x != dpi_y:
                    return None
                if marker > 0x20:
                    return None
                stage_id = active_raw if active_raw not in {0x00, 0xFF} else 0x01
                return 1, [(dpi_x, dpi_y)], [stage_id], marker

            if len(blob) == 8:
                # Layout A: [active, count, dpi_x_l, dpi_x_h, dpi_y_l, dpi_y_h, pad, marker]
                if int(blob[1]) == 1:
                    parsed = _validate_single_stage(
                        active_raw=int(blob[0]),
                        dpi_x=(int(blob[2]) | (int(blob[3]) << 8)),
                        dpi_y=(int(blob[4]) | (int(blob[5]) << 8)),
                        marker=int(blob[7]),
                    )
                    if parsed is not None:
                        return parsed

                # Layout B: [varstore, active, count, dpi_x_l, dpi_x_h, dpi_y_l, dpi_y_h, marker]
                if int(blob[2]) == 1:
                    parsed = _validate_single_stage(
                        active_raw=int(blob[1]),
                        dpi_x=(int(blob[3]) | (int(blob[4]) << 8)),
                        dpi_y=(int(blob[5]) | (int(blob[6]) << 8)),
                        marker=int(blob[7]),
                    )
                    if parsed is not None:
                        return parsed

            if len(blob) == 9:
                # [varstore, active, count, dpi_x_l, dpi_x_h, dpi_y_l, dpi_y_h, pad, marker]
                count = int(blob[2])
                if count != 1:
                    return None
                dpi_x = int(blob[3]) | (int(blob[4]) << 8)
                dpi_y = int(blob[5]) | (int(blob[6]) << 8)
                marker = int(blob[8])
                return _validate_single_stage(
                    active_raw=int(blob[1]),
                    dpi_x=dpi_x,
                    dpi_y=dpi_y,
                    marker=marker,
                )
            return None

        compact = _parse_compact_single_stage(payload)
        if compact is not None:
            return compact

        def _try_layout(
            *,
            active_index: int,
            count_index: int,
            stage_offset: int,
        ) -> Optional[Tuple[int, List[Tuple[int, int]], List[int], int]]:
            if active_index >= len(payload) or count_index >= len(payload):
                return None

            active_raw = int(payload[active_index])
            declared = int(payload[count_index])
            max_by_len = max(0, (len(payload) - stage_offset) // 7)
            if max_by_len <= 0:
                return None

            if declared <= 0 or declared > MAX_DPI_STAGES:
                declared_count = min(MAX_DPI_STAGES, max_by_len)
            else:
                declared_count = min(declared, MAX_DPI_STAGES, max_by_len)
            if declared_count <= 0:
                return None

            stages: List[Tuple[int, int]] = []
            stage_ids: List[int] = []
            marker = 0
            offset = stage_offset
            for _ in range(declared_count):
                if offset + 6 >= len(payload):
                    break
                stage_id = int(payload[offset])
                dpi_x = int(payload[offset + 1]) | (int(payload[offset + 2]) << 8)
                dpi_y = int(payload[offset + 3]) | (int(payload[offset + 4]) << 8)
                marker = int(payload[offset + 6])

                # Reject clearly invalid decodes.
                if (
                    dpi_x < MIN_PLAUSIBLE_DPI
                    or dpi_y < MIN_PLAUSIBLE_DPI
                    or dpi_x > MAX_PLAUSIBLE_DPI
                    or dpi_y > MAX_PLAUSIBLE_DPI
                ):
                    return None
                stage_ids.append(stage_id)
                stages.append((dpi_x, dpi_y))
                offset += 7

            if not stages:
                return None

            active_stage = 1
            for idx, stage_id in enumerate(stage_ids, start=1):
                if stage_id == active_raw:
                    active_stage = idx
                    break
            else:
                if 1 <= active_raw <= len(stages):
                    active_stage = int(active_raw)

            return active_stage, stages, stage_ids, marker

        for layout in (
            {"active_index": 0, "count_index": 1, "stage_offset": 2},
            {"active_index": 1, "count_index": 2, "stage_offset": 3},
        ):
            parsed = _try_layout(**layout)
            if parsed is not None:
                return parsed

        raise CapabilityUnsupportedError("Device returned no DPI profiles over BLE")

    @staticmethod
    def _build_stages_write_payload(
        *,
        active_stage: int,
        stages: Sequence[Tuple[int, int]],
        stage_ids_hint: Sequence[int],
        marker: int,
    ) -> bytes:
        if not stages:
            raise CapabilityUnsupportedError("At least one DPI profile is required")
        if len(stages) > MAX_DPI_STAGES:
            raise CapabilityUnsupportedError(f"At most {MAX_DPI_STAGES} DPI profiles are supported")
        if active_stage < 1 or active_stage > len(stages):
            raise CapabilityUnsupportedError(
                f"Active DPI profile must be between 1 and {len(stages)}"
            )

        stage_ids: List[int] = []
        used: set[int] = set()
        for idx in range(len(stages)):
            value = int(stage_ids_hint[idx]) if idx < len(stage_ids_hint) else (idx + 1)
            value &= 0xFF
            while value in used:
                value = (value + 1) & 0xFF
                if value == 0:
                    value = 1
            used.add(value)
            stage_ids.append(value)

        active_token = stage_ids[active_stage - 1]
        payload = bytearray([active_token & 0xFF, len(stages) & 0xFF])
        for idx in range(len(stages)):
            dpi_x, dpi_y = stages[idx]
            payload.append(stage_ids[idx] & 0xFF)
            payload.append(int(dpi_x) & 0xFF)
            payload.append((int(dpi_x) >> 8) & 0xFF)
            payload.append(int(dpi_y) & 0xFF)
            payload.append((int(dpi_y) >> 8) & 0xFF)
            payload.append(0x00)
            payload.append((marker & 0xFF) if idx == len(stages) - 1 else 0x00)
        return bytes(payload)

    def _read_dpi_stages_with_metadata(
        self,
        device: DetectedDevice,
    ) -> Tuple[int, List[Tuple[int, int]], List[int], int]:
        handle = self._device_handle(device)
        attempts = self._dpi_read_attempts()
        retry_delay = self._dpi_read_retry_delay()
        last_exc: Optional[Exception] = None
        read_keys = self._dpi_read_keys_for_stage_probe(handle)
        diagnostics: List[str] = []

        for attempt in range(attempts):
            best_tuple: Optional[Tuple[int, List[Tuple[int, int]], List[int], int]] = None
            best_key_hex: Optional[str] = None
            best_score: Optional[Tuple[int, int]] = None

            for key in read_keys:
                try:
                    result = self._vendor_call(device=device, key=key)
                    payload = self._decode_payload_bytes(result, key=key)
                    preview = payload[:16].hex() if payload else "-"
                    diagnostics.append(f"{key.hex()}:len={len(payload)}:hex={preview}")
                    active_stage, stages, stage_ids, marker = self._parse_stages_payload(payload)
                    score = (len(stages), len(payload))
                    if best_score is None or score > best_score:
                        best_score = score
                        best_key_hex = key.hex()
                        best_tuple = (active_stage, stages, stage_ids, marker)
                except Exception as exc:
                    diagnostics.append(f"{key.hex()}:err={exc}")
                    last_exc = exc
                    continue

            if best_tuple is not None and best_key_hex is not None:
                active_stage, stages, stage_ids, marker = best_tuple
                handle["ble_dpi_read_key"] = best_key_hex
                handle["ble_stage_ids"] = list(stage_ids)
                handle["ble_stage_marker"] = int(marker)
                handle["ble_cached_stages"] = [[int(x), int(y)] for (x, y) in stages]
                handle["ble_cached_active_stage"] = int(active_stage)
                return active_stage, stages, stage_ids, marker

            # On repeated parse failures, force GATT path rediscovery in case pinned path is stale.
            self._clear_vendor_path(handle)
            if attempt + 1 < attempts and retry_delay > 0:
                time.sleep(retry_delay * float(attempt + 1))

        if isinstance(last_exc, Exception):
            # fall through to cache fallback below
            pass

        cached_stages_raw = handle.get("ble_cached_stages")
        cached_active_raw = handle.get("ble_cached_active_stage")
        cached_stage_ids_raw = handle.get("ble_stage_ids")
        cached_marker_raw = handle.get("ble_stage_marker")
        if isinstance(cached_stages_raw, list) and cached_stages_raw:
            stages: List[Tuple[int, int]] = []
            for row in cached_stages_raw:
                if not isinstance(row, (list, tuple)) or len(row) != 2:
                    continue
                stages.append((int(row[0]), int(row[1])))
            if stages:
                active_stage = int(cached_active_raw or 1)
                if active_stage < 1 or active_stage > len(stages):
                    active_stage = 1
                stage_ids: List[int] = []
                if isinstance(cached_stage_ids_raw, list):
                    stage_ids = [int(value) for value in cached_stage_ids_raw][: len(stages)]
                if not stage_ids:
                    stage_ids = [idx + 1 for idx in range(len(stages))]
                marker = int(cached_marker_raw or 0)
                return active_stage, stages, stage_ids, marker

        if isinstance(last_exc, Exception):
            preview = ", ".join(diagnostics[-8:])
            raise CapabilityUnsupportedError(
                "Could not decode DPI profiles over BLE. "
                f"Recent attempts: {preview}"
            ) from last_exc
        raise CapabilityUnsupportedError("Could not read DPI profiles over BLE")

    def get_dpi(self, device: DetectedDevice) -> Tuple[int, int]:
        active_stage, stages, _stage_ids, _marker = self._read_dpi_stages_with_metadata(device)
        if active_stage < 1 or active_stage > len(stages):
            raise CapabilityUnsupportedError(f"Invalid active DPI profile over BLE: {active_stage}")
        return stages[active_stage - 1]

    def set_dpi(self, device: DetectedDevice, dpi_x: int, dpi_y: int) -> None:
        active_stage, stages, _stage_ids, _marker = self._read_dpi_stages_with_metadata(device)
        stages[active_stage - 1] = (int(dpi_x), int(dpi_y))
        self.set_dpi_stages(device, active_stage, stages)

    def get_dpi_stages(self, device: DetectedDevice) -> Tuple[int, Sequence[Tuple[int, int]]]:
        active_stage, stages, _stage_ids, _marker = self._read_dpi_stages_with_metadata(device)
        return active_stage, stages

    def set_dpi_stages(self, device: DetectedDevice, active_stage: int, stages: Sequence[Tuple[int, int]]) -> None:
        stages_list = list(stages)
        handle = self._device_handle(device)

        stage_ids_hint = handle.get("ble_stage_ids")
        marker = int(handle.get("ble_stage_marker") or 0)
        if not isinstance(stage_ids_hint, list) or not stage_ids_hint:
            try:
                _active, _stages, stage_ids_hint, marker = self._read_dpi_stages_with_metadata(device)
            except Exception:
                stage_ids_hint = [idx + 1 for idx in range(len(stages))]
                marker = 0

        hint_list = [int(value) for value in stage_ids_hint]
        env_ids = str(os.getenv("RAZECLI_BLE_DPI_STAGE_IDS", "")).strip().lower()
        if env_ids in {"sequential", "1", "yes", "true"}:
            hint_list = list(range(1, len(stages_list) + 1))
        elif len(hint_list) < len(stages_list):
            # Mixing firmware tokens (e.g. 0x11, 0x22) with appended 3,4,5 breaks multi-stage
            # tables on some BLE endpoints; match USB-style contiguous slot ids 1..N instead.
            hint_list = list(range(1, len(stages_list) + 1))

        payload = self._build_stages_write_payload(
            active_stage=int(active_stage),
            stages=stages_list,
            stage_ids_hint=hint_list,
            marker=marker,
        )
        write_keys = self._dpi_write_keys_for_stage_mirror(handle)
        attempted: List[str] = []
        last_exc: Optional[Exception] = None
        successes = 0
        for key in write_keys:
            attempted.append(key.hex())
            try:
                _ = self._vendor_call(
                    device=device,
                    key=key,
                    value_payload=payload,
                )
                successes += 1
                if successes == 1:
                    handle["ble_dpi_write_key"] = key.hex()
                last_exc = None
            except Exception as exc:
                last_exc = exc
                continue
        if successes == 0:
            raise CapabilityUnsupportedError(
                f"DPI profile write over BLE failed. Tried keys: {attempted}"
            ) from last_exc
        try:
            self._read_dpi_stages_with_metadata(device)
        except Exception:
            handle["ble_stage_ids"] = hint_list[: len(stages_list)]
            handle["ble_stage_marker"] = int(marker)
            handle["ble_cached_stages"] = [[int(x), int(y)] for (x, y) in stages_list]
            handle["ble_cached_active_stage"] = int(active_stage)

    def get_poll_rate(self, device: DetectedDevice) -> int:
        if not self._poll_capability_enabled:
            raise CapabilityUnsupportedError(
                "Bluetooth poll-rate probing is disabled by default. "
                "Set RAZECLI_BLE_POLL_CAP=1 to enable experimental probing."
            )
        if not self._model_supports_ble_poll(device.model_id):
            device.capabilities.discard("poll-rate")
            raise CapabilityUnsupportedError(
                f"Poll-rate over Bluetooth is disabled for model '{device.model_id or 'unknown'}'. "
                "Use USB/2.4 for poll-rate. "
                "To test a model anyway, add it to RAZECLI_BLE_POLL_SUPPORTED_MODELS "
                "or set RAZECLI_BLE_POLL_FORCE=1."
            )
        handle = self._device_handle(device)
        target_key = self._poll_target_key(device)
        if bool(handle.get("ble_poll_unavailable", False)) or target_key in self._poll_unavailable_targets:
            device.capabilities.discard("poll-rate")
            cached = handle.get("ble_poll_rate_hz")
            try:
                cached_hz = int(cached) if cached is not None else None
            except Exception:
                cached_hz = None
            if cached_hz in (125, 500, 1000):
                return int(cached_hz)
            raise CapabilityUnsupportedError(
                "Poll-rate is not exposed by this Bluetooth endpoint on this host. Use USB/2.4 for poll-rate."
            )
        read_keys = self._poll_read_keys(handle)
        diagnostics: List[str] = []
        read_attempts = self._poll_read_attempts()
        retry_delay = self._poll_read_retry_delay()
        explicit_rejects = 0
        observed_vendor_replies = 0
        for _ in range(read_attempts):
            for key in read_keys:
                status_code: Optional[int] = None
                try:
                    result = self._vendor_call(device=device, key=key)
                    if isinstance(result, dict):
                        vendor_decode = result.get("vendor_decode")
                        if isinstance(vendor_decode, dict):
                            status_code_raw = vendor_decode.get("status_code")
                            try:
                                status_code = int(status_code_raw)
                            except Exception:
                                status_code = None
                            if status_code is not None:
                                observed_vendor_replies += 1
                                if status_code == 3:
                                    explicit_rejects += 1
                    payload = self._decode_payload_bytes(result, key=key)
                    decoded = self._decode_poll_rate_payload(payload)
                    preview = payload[:16].hex() if payload else "-"
                    diagnostics.append(
                        f"{key.hex()}:sc={status_code if status_code is not None else '-'}"
                        f":len={len(payload)}:hex={preview}:hz={decoded if decoded is not None else '-'}"
                    )
                    self._poll_debug(diagnostics[-1])
                except Exception as exc:
                    diagnostics.append(f"{key.hex()}:err={exc}")
                    self._poll_debug(diagnostics[-1])
                    continue
                if decoded is not None:
                    handle["ble_poll_read_key"] = key.hex()
                    handle["ble_poll_rate_hz"] = int(decoded)
                    handle["ble_poll_unavailable"] = False
                    self._poll_unavailable_targets.discard(target_key)
                    device.capabilities.add("poll-rate")
                    return int(decoded)
            if retry_delay > 0:
                time.sleep(retry_delay)

        cached = handle.get("ble_poll_rate_hz")
        try:
            cached_hz = int(cached) if cached is not None else None
        except Exception:
            cached_hz = None
        if cached_hz in (125, 500, 1000):
            return int(cached_hz)

        if observed_vendor_replies > 0 and explicit_rejects == observed_vendor_replies:
            handle["ble_poll_unavailable"] = True
            self._poll_unavailable_targets.add(target_key)
            device.capabilities.discard("poll-rate")
            preview = self._poll_diagnostic_preview(diagnostics)
            raise CapabilityUnsupportedError(
                "Poll-rate is not exposed by this Bluetooth endpoint on this host. "
                f"Recent probe attempts: {preview}. Use USB/2.4 for poll-rate."
            )
        preview = self._poll_diagnostic_preview(diagnostics)
        raise CapabilityUnsupportedError(
            "Poll-rate over Bluetooth is not mapped for this device/host. "
            f"Recent probe attempts: {preview}"
        )

    def set_poll_rate(self, device: DetectedDevice, hz: int) -> None:
        hz = int(hz)
        if hz not in POLL_RATE_TO_CODE:
            raise CapabilityUnsupportedError("Poll-rate must be one of: 125, 500, 1000")
        if not self._model_supports_ble_poll(device.model_id):
            device.capabilities.discard("poll-rate")
            raise CapabilityUnsupportedError(
                f"Poll-rate over Bluetooth is disabled for model '{device.model_id or 'unknown'}'. "
                "Use USB/2.4 for poll-rate. "
                "To test a model anyway, add it to RAZECLI_BLE_POLL_SUPPORTED_MODELS "
                "or set RAZECLI_BLE_POLL_FORCE=1."
            )

        handle = self._device_handle(device)
        write_keys = self._poll_write_keys(handle)
        payload_candidates = self._poll_write_payload_candidates(hz)
        attempted: List[str] = []

        for key in write_keys:
            for payload in payload_candidates:
                attempted.append(f"{key.hex()}:{payload.hex()}")
                try:
                    _ = self._vendor_call(device=device, key=key, value_payload=payload)
                except Exception:
                    continue
                try:
                    current = self.get_poll_rate(device)
                except Exception:
                    continue
                if int(current) == hz:
                    handle["ble_poll_write_key"] = key.hex()
                    handle["ble_poll_rate_hz"] = int(hz)
                    handle["ble_poll_unavailable"] = False
                    self._poll_unavailable_targets.discard(self._poll_target_key(device))
                    device.capabilities.add("poll-rate")
                    return

        raise CapabilityUnsupportedError(
            "Poll-rate write over Bluetooth failed or could not be verified. "
            f"Tried: {attempted}"
        )

    def get_supported_poll_rates(self, device: DetectedDevice) -> Sequence[int]:
        model = self._model_registry.get(str(device.model_id or "").strip().lower())
        if model is not None:
            if model.ble_supported_poll_rates:
                return sorted(set(int(value) for value in model.ble_supported_poll_rates))
            if model.supported_poll_rates:
                return sorted(set(int(value) for value in model.supported_poll_rates))
        return [125, 500, 1000]

    async def _read_standard_battery_level_async(
        self,
        *,
        address: Optional[str],
        name_query: str,
        timeout: float,
    ) -> Optional[int]:
        try:
            from bleak import BleakClient  # type: ignore
            from razecli.ble_probe import _auto_resolve_corebluetooth_address, _resolve_device_async
        except Exception:
            return None

        connect_target: Any = None
        if address and self._is_mac_address(address):
            try:
                auto = await _auto_resolve_corebluetooth_address(
                    mac_address=address,
                    timeout=timeout,
                )
            except Exception:
                auto = {}
            resolved_device = auto.get("resolved_device")
            resolved_address = str(auto.get("resolved_address") or "").strip()
            if resolved_device is not None:
                connect_target = resolved_device
            elif resolved_address:
                connect_target = resolved_address
            else:
                connect_target = address
        else:
            try:
                connect_target = await _resolve_device_async(
                    address=address,
                    name_query=name_query,
                    timeout=timeout,
                )
            except Exception:
                return None

        if connect_target is None:
            return None

        try:
            async with BleakClient(connect_target, timeout=timeout) as client:
                value = await client.read_gatt_char(BATTERY_LEVEL_CHAR_UUID)
        except Exception:
            return None

        data = bytes(value or b"")
        if not data:
            return None
        level = int(data[0])
        if 0 <= level <= 100:
            return int(level)
        return None

    def _read_standard_battery_level(self, device: DetectedDevice) -> Optional[int]:
        address, name_query = self._resolve_target(device)
        timeout = self._backend_timeout()
        try:
            return run_ble_sync(
                self._read_standard_battery_level_async(
                    address=address,
                    name_query=name_query,
                    timeout=timeout,
                )
            )
        except Exception:
            return None

    def get_battery(self, device: DetectedDevice) -> int:
        standard_level = self._read_standard_battery_level(device)
        if standard_level is not None:
            return int(standard_level)

        attempts: List[Tuple[bytes, str]] = [
            (KEY_BATTERY_RAW_READ, "raw"),
            (KEY_BATTERY_STATUS_READ, "status"),
        ]
        last_err: Optional[Exception] = None

        for key, mode in attempts:
            try:
                result = self._vendor_call(device=device, key=key)
                payload = self._decode_payload_bytes(result, key=key)
                if not payload:
                    continue
                value = int(payload[0])
                if mode == "status":
                    if 0 <= value <= 100:
                        return int(value)
                    continue
                # Raw battery endpoint reports 0-255 scale.
                return int(round((value / 255.0) * 100.0))
            except Exception as exc:
                last_err = exc
                continue

        if isinstance(last_err, Exception):
            raise CapabilityUnsupportedError("Battery response over BLE does not include payload") from last_err
        raise CapabilityUnsupportedError("Battery response over BLE does not include payload")

    def get_rgb(self, device: DetectedDevice) -> Dict[str, Any]:
        handle = self._device_handle(device)
        supported_modes = self._model_supported_ble_rgb_modes(device.model_id)
        read_keys = self._rgb_brightness_read_keys(handle)

        brightness_percent: Optional[int] = None
        brightness_confidence = "inferred"
        for key in read_keys:
            try:
                result = self._vendor_call(device=device, key=key)
                if not self._vendor_decode_is_success(result):
                    continue
                payload = self._decode_payload_bytes(result, key=key)
            except Exception:
                continue
            if not payload:
                continue
            brightness_percent = self._rgb_u8_to_percent(int(payload[0]))
            brightness_confidence = "verified"
            handle["ble_rgb_read_key"] = key.hex()
            break

        if brightness_percent is None:
            cached_brightness = handle.get("ble_rgb_brightness")
            try:
                brightness_percent = max(0, min(100, int(cached_brightness)))
            except Exception:
                brightness_percent = 100
                brightness_confidence = "inferred-default"
            else:
                brightness_confidence = "inferred-cache"

        color_hex: Optional[str] = None
        color_confidence = "inferred"
        try:
            frame_result = self._vendor_call(device=device, key=KEY_RGB_FRAME_READ)
            if not self._vendor_decode_is_success(frame_result):
                frame_result = {}
            frame_payload = self._decode_payload_bytes(frame_result, key=KEY_RGB_FRAME_READ)
            color_hex = self._rgb_color_from_payload(frame_payload)
            if color_hex is not None:
                color_confidence = "verified"
        except Exception:
            color_hex = None

        if color_hex is None:
            cached_color = self._normalize_color_hex(str(handle.get("ble_rgb_color") or "").strip() or None)
            if cached_color is not None:
                color_hex = cached_color
                color_confidence = "inferred-cache"
            else:
                color_hex = "00ff00"
                color_confidence = "inferred-default"

        # ``10830000`` read returns either a 1-byte legacy selector or a 10-byte V3 Pro–class
        # zone block (OpenSnek). Skipping it hid real modes (e.g. spectrum). Opt out only if the
        # read causes problems on your mouse: RAZECLI_BLE_RGB_SKIP_MODE_READ=1.
        selector_mode: Optional[str] = None
        mode_selector_code: Optional[int] = None
        if not MacOSBleBackend._env_flag("RAZECLI_BLE_RGB_SKIP_MODE_READ", default=False):
            try:
                mode_result = self._vendor_call(device=device, key=KEY_RGB_MODE_READ)
                if not self._vendor_decode_is_success(mode_result):
                    mode_result = {}
                mode_payload = self._decode_payload_bytes(mode_result, key=KEY_RGB_MODE_READ)
                if mode_payload:
                    zone_mode, zone_color = MacOSBleBackend._rgb_mode_color_from_1083_zone_payload(
                        mode_payload,
                        brightness_percent=int(brightness_percent),
                    )
                    if zone_mode:
                        selector_mode = zone_mode
                        try:
                            mode_selector_code = int(mode_payload[3])
                        except (IndexError, ValueError):
                            mode_selector_code = None
                        if zone_color:
                            # Frame read is a single instant; for animated modes prefer the zone snapshot.
                            prefer_zone_color = color_confidence != "verified" or zone_mode in (
                                "spectrum",
                                "breathing",
                                "breathing-single",
                                "breathing-random",
                            )
                            if prefer_zone_color:
                                color_hex = zone_color
                                color_confidence = "verified"
                    else:
                        mode_selector_code = int(mode_payload[0])
                        selector_mode = self._rgb_mode_from_selector_payload(
                            mode_payload,
                            brightness_percent=int(brightness_percent),
                        )
            except Exception:
                selector_mode = None

        cached_mode = str(handle.get("ble_rgb_mode") or "").strip().lower()
        mode_inferred = False
        mode_confidence = "inferred"
        if selector_mode in RGB_MODES:
            mode = str(selector_mode)
            mode_confidence = "verified"
        elif brightness_percent <= 0:
            if cached_mode in RGB_MODES and cached_mode != "off":
                mode = cached_mode
                mode_inferred = True
            else:
                mode = "off"
                mode_inferred = True
        else:
            if cached_mode in RGB_MODES and cached_mode != "off":
                mode = cached_mode
                mode_inferred = True
            else:
                # BLE mode-read is not fully mapped on this device profile yet.
                # Preserve compatibility by returning "static" as a safe fallback,
                # but mark the value as inferred so callers can prefer local intent.
                mode = "static"
                mode_inferred = True

        if mode not in supported_modes:
            if brightness_percent <= 0 and "off" in supported_modes:
                mode = "off"
            elif "static" in supported_modes:
                mode = "static"
            else:
                mode = str(supported_modes[0] if supported_modes else "off")
            mode_inferred = True
            mode_confidence = "inferred"

        handle["ble_rgb_mode"] = mode
        handle["ble_rgb_color"] = color_hex
        handle["ble_rgb_brightness"] = int(brightness_percent)
        read_confidence: Dict[str, Any] = {
            "overall": self._read_confidence_overall(
                (brightness_confidence, color_confidence, mode_confidence)
            ),
            "brightness": brightness_confidence,
            "color": color_confidence,
            "mode": mode_confidence,
        }
        if mode_selector_code is not None:
            read_confidence["mode_selector"] = int(mode_selector_code)

        return {
            "mode": mode,
            "mode_inferred": bool(mode_inferred),
            "brightness": int(brightness_percent),
            "color": color_hex,
            "modes_supported": list(supported_modes),
            "read_confidence": read_confidence,
        }

    def set_rgb(
        self,
        device: DetectedDevice,
        *,
        mode: str,
        brightness: Optional[int] = None,
        color: Optional[str] = None,
    ) -> Dict[str, Any]:
        handle = self._device_handle(device)
        requested_mode = str(mode).strip().lower()
        mode_value = self._normalize_rgb_mode_for_write(requested_mode)
        reported_mode = requested_mode if requested_mode == "breathing" else mode_value
        supported_modes = self._model_supported_ble_rgb_modes(device.model_id)
        if requested_mode not in RGB_MODES:
            raise CapabilityUnsupportedError(
                f"Unsupported RGB mode '{mode}'. Supported: {', '.join(RGB_MODES)}"
            )
        if not self._rgb_mode_supported_over_ble(requested_mode=requested_mode, supported_modes=supported_modes):
            raise CapabilityUnsupportedError(
                "RGB mode write over Bluetooth is not mapped for this model yet. "
                f"Requested '{requested_mode}'. Supported over BLE: {', '.join(supported_modes)}"
            )

        current: Dict[str, Any] = {}
        try:
            current = self.get_rgb(device)
        except Exception:
            current = {
                "mode": "off",
                "brightness": 100,
                "color": "00ff00",
                "modes_supported": list(supported_modes),
            }

        color_hex = self._normalize_color_hex(color) or str(current.get("color") or "00ff00")
        if brightness is None:
            brightness_percent = int(current.get("brightness", 100))
        else:
            brightness_percent = max(0, min(100, int(brightness)))
        if mode_value == "off":
            brightness_percent = 0

        attempts = self._rgb_write_attempts()
        retry_delay = self._rgb_write_retry_delay()
        verify_write = self._rgb_verify_write()
        brightness_u8 = self._rgb_percent_to_u8(brightness_percent)
        write_keys = self._rgb_brightness_write_keys(handle)

        last_err: Optional[Exception] = None
        write_ok = False
        for attempt in range(attempts):
            try:
                selector_payload = self._rgb_mode_write_payload(mode=mode_value, color_hex=color_hex)
                mode_write_ok = False

                # Legacy 1084 frame + breathing 10-byte mode is validated on DA V2 Pro BT; static uses
                # 10-byte zone row only (see _rgb_mode_write_payload). Spectrum/off must not get a
                # static color frame or they snap back to solid green.
                if mode_value == "breathing-single":
                    rgb = bytes.fromhex(color_hex)
                    frame_payload = bytes([0x04, 0x00, 0x00, 0x00, 0x00, rgb[0], rgb[1], rgb[2]])
                    frame_result = self._vendor_call(
                        device=device,
                        key=KEY_RGB_FRAME_WRITE,
                        value_payload=frame_payload,
                    )
                    if not self._vendor_decode_is_success(frame_result):
                        raise CapabilityUnsupportedError("Could not write RGB frame over BLE")

                if selector_payload is not None:
                    mode_result = self._vendor_call(
                        device=device,
                        key=KEY_RGB_MODE_WRITE,
                        value_payload=selector_payload,
                    )
                    mode_write_ok = self._vendor_decode_is_success(mode_result)
                    if not mode_write_ok:
                        raise CapabilityUnsupportedError("Could not write RGB mode selector over BLE")
                else:
                    mode_write_ok = True

                wrote_brightness = False
                brightness_err: Optional[Exception] = None
                for key in write_keys:
                    try:
                        brightness_result = self._vendor_call(
                            device=device,
                            key=key,
                            value_payload=bytes([brightness_u8]),
                        )
                        if not self._vendor_decode_is_success(brightness_result):
                            continue
                        handle["ble_rgb_write_key"] = key.hex()
                        wrote_brightness = True
                        break
                    except Exception as exc:
                        brightness_err = exc
                        continue

                handle["ble_rgb_mode"] = reported_mode
                handle["ble_rgb_color"] = color_hex
                handle["ble_rgb_brightness"] = int(brightness_percent)

                if verify_write:
                    try:
                        verified = self.get_rgb(device)
                    except Exception as exc:
                        if mode_write_ok:
                            verified = {}
                        else:
                            raise CapabilityUnsupportedError("Could not verify RGB write over BLE") from exc

                    read_confidence = verified.get("read_confidence", {}) if isinstance(verified, dict) else {}
                    mode_confidence = str(read_confidence.get("mode") or "").strip().lower()
                    if mode_confidence == "verified":
                        verified_mode = str(verified.get("mode") or "").strip().lower()
                        if verified_mode and not self._rgb_modes_equivalent_for_verify(
                            expected=mode_value,
                            actual=verified_mode,
                        ):
                            raise CapabilityUnsupportedError(
                                "RGB write verification mismatch over BLE "
                                f"(expected mode {mode_value}, got {verified_mode})"
                            )

                    brightness_confidence = str(read_confidence.get("brightness") or "").strip().lower()
                    if wrote_brightness and brightness_confidence == "verified":
                        try:
                            actual_brightness = int(verified.get("brightness"))
                        except Exception:
                            actual_brightness = -1
                        expected_brightness = int(brightness_percent)
                        if abs(actual_brightness - expected_brightness) > 2:
                            raise CapabilityUnsupportedError(
                                "RGB write verification mismatch over BLE "
                                f"(expected brightness {expected_brightness}, got {actual_brightness})"
                            )

                write_ok = True
                break
            except Exception as exc:
                last_err = exc
                self._clear_vendor_path(handle)
                if attempt + 1 < attempts and retry_delay > 0:
                    time.sleep(retry_delay * float(attempt + 1))
                continue

        if not write_ok:
            if isinstance(last_err, Exception):
                raise CapabilityUnsupportedError(
                    f"Could not apply RGB over BLE reliably after {attempts} attempts"
                ) from last_err
            raise CapabilityUnsupportedError(f"Could not apply RGB over BLE reliably after {attempts} attempts")

        return {
            "mode": reported_mode,
            "mode_inferred": False,
            "brightness": int(brightness_percent),
            "color": color_hex,
            "modes_supported": list(supported_modes),
        }

    def get_button_mapping(self, device: DetectedDevice) -> Dict[str, Any]:
        mapping: Dict[str, str] = {}
        decode_layouts = self._model_ble_button_decode_layouts(device.model_id)
        verified_buttons: List[str] = []
        for button, slot in BUTTON_SLOT_BY_NAME.items():
            key = bytes([0x08, 0x84, 0x01, int(slot)])
            try:
                result = self._vendor_call(device=device, key=key)
                payload = self._decode_payload_bytes(result, key=key)
            except Exception:
                continue
            action = self._decode_ble_button_payload(int(slot), payload, layouts=decode_layouts)
            if action:
                button_name = str(button)
                mapping[button_name] = action
                verified_buttons.append(button_name)

        if not mapping:
            raise CapabilityUnsupportedError("Button mapping read over BLE is not available on this host/device")

        inferred_buttons: List[str] = []
        for button, default_action in DEFAULT_BUTTON_MAPPING.items():
            if button not in mapping:
                mapping[button] = default_action
                inferred_buttons.append(str(button))

        read_confidence = {
            "overall": "verified" if not inferred_buttons else "mixed",
            "verified_buttons": sorted(set(verified_buttons)),
            "inferred_buttons": sorted(set(inferred_buttons)),
        }

        return {
            "mapping": mapping,
            "buttons_supported": list(BUTTON_SLOT_BY_NAME.keys()),
            "actions_suggested": list(BUTTON_ACTIONS),
            "read_confidence": read_confidence,
        }

    def set_button_mapping(
        self,
        device: DetectedDevice,
        *,
        button: str,
        action: str,
    ) -> Dict[str, Any]:
        slot = self._slot_from_button_name(button)
        payload = self._build_ble_button_payload(slot, action)
        key = bytes([0x08, 0x04, 0x01, slot])
        _ = self._vendor_call(
            device=device,
            key=key,
            value_payload=payload,
        )

        try:
            state = self.get_button_mapping(device)
            state["mapping"][str(button).strip()] = str(action).strip().lower()
            return state
        except CapabilityUnsupportedError:
            mapping = dict(DEFAULT_BUTTON_MAPPING)
            mapping[str(button).strip()] = str(action).strip().lower()
            return {
                "mapping": mapping,
                "buttons_supported": list(BUTTON_SLOT_BY_NAME.keys()),
                "actions_suggested": list(BUTTON_ACTIONS),
                "read_confidence": {
                    "overall": "inferred",
                    "verified_buttons": [],
                    "inferred_buttons": sorted(BUTTON_SLOT_BY_NAME.keys()),
                },
            }

    def reset_button_mapping(self, device: DetectedDevice) -> Dict[str, Any]:
        for button, action in DEFAULT_BUTTON_MAPPING.items():
            slot = self._slot_from_button_name(button)
            payload = self._build_ble_button_payload(slot, action)
            key = bytes([0x08, 0x04, 0x01, slot])
            _ = self._vendor_call(
                device=device,
                key=key,
                value_payload=payload,
            )
        return {
            "mapping": dict(DEFAULT_BUTTON_MAPPING),
            "buttons_supported": list(BUTTON_SLOT_BY_NAME.keys()),
            "actions_suggested": list(BUTTON_ACTIONS),
        }

    def list_button_mapping_actions(self, device: DetectedDevice) -> Dict[str, Any]:
        _ = device
        return {
            "buttons": list(BUTTON_SLOT_BY_NAME.keys()),
            "actions": list(BUTTON_ACTIONS),
        }


__all__ = ["MacOSBleBackend"]
