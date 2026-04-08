"""Raw HID backend using direct Razer HID packet framing.

Supports device control for selected Razer mice without external daemons.
Currently targets selected V2-era models, with DA V2 Pro as primary validated path.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from razecli.backends.base import Backend
from razecli.errors import BackendUnavailableError, CapabilityUnsupportedError
from razecli.model_registry import ModelRegistry
from razecli.types import DetectedDevice

RAZER_VENDOR_ID = 0x1532
MAX_TRANSCEIVE_ATTEMPTS = 4
MAX_DPI_STAGES = 5
_DEVSRVS_PATH_RE = re.compile(r"^DevSrvsID:\d+$")


@dataclass(frozen=True)
class PidSupportProfile:
    product_id: int
    name_hint: str
    capabilities: frozenset[str]
    tx_candidates: Tuple[int, ...] = (0x3F, 0x1F, 0xFF)
    report_id_candidates: Tuple[int, ...] = (0x00,)
    experimental: bool = False
    prefer_vendor_usage_page: bool = False


def _build_pid_profiles_from_models() -> Dict[int, PidSupportProfile]:
    registry = ModelRegistry.load()
    profiles: Dict[int, PidSupportProfile] = {}
    for model in registry.iter():
        for raw_spec in tuple(getattr(model, "rawhid_pid_specs", ()) or ()):
            try:
                product_id = int(getattr(raw_spec, "product_id", -1))
            except Exception:
                continue
            if product_id < 0 or product_id > 0xFFFF:
                continue

            capabilities = frozenset(
                str(value).strip().lower()
                for value in tuple(getattr(raw_spec, "capabilities", ()) or ())
                if str(value).strip()
            )
            if not capabilities:
                continue

            tx_candidates = tuple(
                int(value) & 0xFF
                for value in tuple(getattr(raw_spec, "tx_candidates", (0x3F, 0x1F, 0xFF)) or ())
            ) or (0x3F, 0x1F, 0xFF)
            report_id_candidates = tuple(
                int(value) & 0xFF
                for value in tuple(getattr(raw_spec, "report_id_candidates", (0x00,)) or ())
            ) or (0x00,)
            name_hint = str(getattr(raw_spec, "name_hint", "") or "").strip() or str(model.name).strip()
            experimental = bool(getattr(raw_spec, "experimental", False))
            prefer_vendor_usage_page = bool(getattr(raw_spec, "prefer_vendor_usage_page", False))

            profiles[product_id] = PidSupportProfile(
                product_id=product_id,
                name_hint=name_hint,
                capabilities=capabilities,
                tx_candidates=tx_candidates,
                report_id_candidates=report_id_candidates,
                experimental=experimental,
                prefer_vendor_usage_page=prefer_vendor_usage_page,
            )
    return profiles


# Rawhid support is model-driven via ModelSpec.rawhid_pid_specs.
PID_PROFILES: Dict[int, PidSupportProfile] = _build_pid_profiles_from_models()

POLL_RATE_TO_CODE = {
    1000: 0x01,
    500: 0x02,
    125: 0x08,
}
CODE_TO_POLL_RATE = {value: key for key, value in POLL_RATE_TO_CODE.items()}

# Chroma (OpenRazer razerchromacommon extended matrix + standard LED helpers)
_VARSTORE = 0x01
_LED_SCROLL = 0x00
_LED_LOGO = 0x04
_LED_BACKLIGHT = 0x05
# Probe multiple zones by default; older mice sometimes expose RGB on 0x00.
_RAWHID_RGB_ZONE_LEDS: Tuple[int, ...] = (_LED_SCROLL, _LED_LOGO, _LED_BACKLIGHT)
_RAWHID_RGB_PROTOCOLS: Tuple[str, ...] = ("extended-matrix", "mouse-standard", "mouse-extended")


@dataclass(frozen=True)
class RazerCommand:
    command_class: int
    command_id: int
    data_size: int
    arguments: Tuple[int, ...] = ()


def _calculate_crc(report_bytes: bytearray) -> int:
    crc = 0
    for index in range(2, 88):
        crc ^= report_bytes[index]
    return crc


def _build_report(command: RazerCommand, transaction_id: int) -> bytes:
    report = bytearray(90)
    report[0] = 0x00  # status (new command)
    report[1] = transaction_id & 0xFF
    report[2] = 0x00  # remaining_packets (big-endian)
    report[3] = 0x00
    report[4] = 0x00  # protocol_type
    report[5] = command.data_size & 0xFF
    report[6] = command.command_class & 0xFF
    report[7] = command.command_id & 0xFF

    for idx, value in enumerate(command.arguments[:80]):
        report[8 + idx] = value & 0xFF

    report[88] = _calculate_crc(report)
    report[89] = 0x00  # reserved
    return bytes(report)


def _normalize_feature_response(raw: Any) -> Optional[bytes]:
    if raw is None:
        return None

    if isinstance(raw, (bytes, bytearray)):
        payload = bytes(raw)
    else:
        try:
            payload = bytes(raw)
        except TypeError:
            return None

    if len(payload) == 91:
        # hidapi includes report-id byte first
        return payload[1:]
    if len(payload) == 90:
        return payload
    if len(payload) > 90:
        return payload[-90:]
    return None


def _extract_response_fields(response: bytes) -> Dict[str, Any]:
    args = response[8:88]
    return {
        "status": response[0],
        "transaction_id": response[1],
        "remaining_packets": (response[2] << 8) | response[3],
        "protocol_type": response[4],
        "data_size": response[5],
        "command_class": response[6],
        "command_id": response[7],
        "arguments": args,
        "crc": response[88],
        "reserved": response[89],
    }


class RawHidBackend(Backend):
    name = "rawhid"

    def __init__(self) -> None:
        self.last_error = None
        self._hid = None
        try:
            import hid  # type: ignore

            self._hid = hid
        except Exception as exc:  # pragma: no cover - host dependent
            self.last_error = exc

    def _ensure_hid(self) -> None:
        if self._hid is None:
            raise BackendUnavailableError(
                "hidapi saknas. Installera med: pip install 'hidapi>=0.14'"
            )

    @staticmethod
    def _debug_enabled() -> bool:
        value = str(os.getenv("RAZECLI_RAWHID_DEBUG", "")).strip().lower()
        return value in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_flag(name: str, *, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if not text:
            return bool(default)
        return text in {"1", "true", "yes", "on"}

    @staticmethod
    def _is_terminal_rgb_error(exc: Exception) -> bool:
        text = str(exc or "").strip().lower()
        if not text:
            return False
        return (
            "device status=0x03" in text
            or "device status=0x04" in text
            or "device status=0x05" in text
            or "command not supported" in text
            or "response command mismatch" in text
            or "terminal " in text
        )

    def _debug(self, message: str) -> None:
        if self._debug_enabled():
            print(f"[rawhid] {message}", file=sys.stderr)

    @staticmethod
    def _hid_error_text(hid_dev: Any) -> str:
        try:
            error_fn = getattr(hid_dev, "error", None)
            if callable(error_fn):
                value = error_fn()
                if value:
                    return str(value)
        except Exception:
            pass
        return ""

    @staticmethod
    def _parse_int_token(raw: str) -> Optional[int]:
        text = raw.strip().lower()
        if not text:
            return None
        try:
            if text.startswith("0x"):
                return int(text, 16)
            return int(text, 10)
        except ValueError:
            return None

    @classmethod
    def _override_candidates_from_env(
        cls,
        env_var: str,
        default_values: Tuple[int, ...],
    ) -> Tuple[int, ...]:
        raw = os.getenv(env_var)
        if raw is None:
            return default_values
        parts = [part.strip() for part in raw.split(",")]
        parsed: List[int] = []
        for part in parts:
            value = cls._parse_int_token(part)
            if value is None:
                continue
            parsed.append(value & 0xFF)
        if parsed:
            return tuple(parsed)
        return default_values

    @staticmethod
    def _path_to_text(path: Any) -> str:
        if isinstance(path, (bytes, bytearray)):
            return path.decode(errors="ignore")
        return str(path)

    @staticmethod
    def _normalized_serial(serial: Optional[str]) -> Optional[str]:
        if serial is None:
            return None
        text = serial.strip()
        if not text:
            return None
        compact = "".join(ch for ch in text if ch.isalnum())
        if compact and set(compact) == {"0"}:
            return None
        return text

    @classmethod
    def _build_identifier(
        cls,
        *,
        vendor_id: int,
        product_id: int,
        path_text: str,
        serial_text: Optional[str],
    ) -> str:
        serial_key = cls._normalized_serial(serial_text)
        if serial_key:
            serial_compact = "".join(ch for ch in serial_key if ch.isalnum())
            return f"rawhid:{vendor_id:04X}:{product_id:04X}:sn:{serial_compact}"
        if path_text and not _DEVSRVS_PATH_RE.match(path_text):
            return f"rawhid:path:{path_text}"
        return f"rawhid:{vendor_id:04X}:{product_id:04X}"

    @staticmethod
    def _profile_for_pid(product_id: int) -> Optional[PidSupportProfile]:
        return PID_PROFILES.get(product_id)

    @classmethod
    def _is_supported_pid(cls, product_id: int) -> bool:
        return cls._profile_for_pid(product_id) is not None

    @classmethod
    def _transaction_candidates_for_pid(cls, product_id: int) -> Tuple[int, ...]:
        profile = cls._profile_for_pid(product_id)
        if profile is None:
            return (0x3F, 0x1F, 0xFF)
        return profile.tx_candidates

    @classmethod
    def _report_id_candidates_for_pid(cls, product_id: int) -> Tuple[int, ...]:
        profile = cls._profile_for_pid(product_id)
        if profile is None:
            return (0x00,)
        return profile.report_id_candidates

    @classmethod
    def _capabilities_for_pid(cls, product_id: int) -> Set[str]:
        profile = cls._profile_for_pid(product_id)
        if profile is None:
            return set()
        return set(profile.capabilities)

    def _require_capability(self, device: DetectedDevice, capability: str) -> None:
        if capability in device.capabilities:
            return
        profile_caps = self._capabilities_for_pid(device.product_id)
        if capability in profile_caps:
            return
        raise CapabilityUnsupportedError(
            f"Capability '{capability}' is not supported for {device.usb_id()} in rawhid backend"
        )

    def detect(self) -> List[DetectedDevice]:
        if self._hid is None:
            return []

        devices: List[DetectedDevice] = []
        seen: set[str] = set()

        for info in self._hid.enumerate(RAZER_VENDOR_ID, 0):
            entry: Dict[str, Any] = dict(info)

            vendor_id = int(entry.get("vendor_id", 0) or 0)
            product_id = int(entry.get("product_id", 0) or 0)
            if vendor_id != RAZER_VENDOR_ID:
                continue
            if not self._is_supported_pid(product_id):
                continue

            path = entry.get("path")
            if path is None:
                continue

            path_text = self._path_to_text(path)
            if path_text in seen:
                continue
            seen.add(path_text)

            profile = self._profile_for_pid(product_id)
            assert profile is not None

            product = str(entry.get("product_string") or profile.name_hint)
            serial = entry.get("serial_number")
            serial_text = str(serial) if serial else None

            caps = set(profile.capabilities)
            identifier = self._build_identifier(
                vendor_id=vendor_id,
                product_id=product_id,
                path_text=path_text,
                serial_text=serial_text,
            )

            devices.append(
                DetectedDevice(
                    identifier=identifier,
                    name=product,
                    vendor_id=vendor_id,
                    product_id=product_id,
                    backend=self.name,
                    serial=serial_text,
                    capabilities=caps,
                    backend_handle={
                        "path": path,
                        "path_text": path_text,
                        "interface_number": entry.get("interface_number"),
                        "usage_page": entry.get("usage_page"),
                        "usage": entry.get("usage"),
                        "profile": profile,
                    },
                )
            )

        return devices

    def _candidate_paths(
        self,
        device: DetectedDevice,
        *,
        prefer_usage: Optional[int] = None,
        avoid_usage: Optional[int] = None,
    ) -> List[Any]:
        self._ensure_hid()
        assert self._hid is not None

        initial = None
        handle = device.backend_handle if isinstance(device.backend_handle, dict) else {}
        if handle:
            initial = handle.get("path")

        candidates: List[Tuple[int, Any, Any, Any, Any]] = []
        if initial is not None:
            candidates.append((0, initial, None, None, None))

        for info in self._hid.enumerate(device.vendor_id, device.product_id):
            entry: Dict[str, Any] = dict(info)
            path = entry.get("path")
            if path is None:
                continue

            score = 50
            if entry.get("interface_number") == 0:
                score -= 30
            if entry.get("usage_page") in (0x0001, 0xFF00):
                score -= 5
            usage = entry.get("usage")
            try:
                usage_int = int(usage) if usage is not None else None
            except Exception:
                usage_int = None
            if prefer_usage is not None and usage_int == int(prefer_usage):
                score -= 20
            if avoid_usage is not None and usage_int == int(avoid_usage):
                score += 20
            profile = self._profile_for_pid(device.product_id)
            if profile and profile.prefer_vendor_usage_page:
                usage_page = int(entry.get("usage_page", 0) or 0)
                usage = int(entry.get("usage", 0) or 0)
                # Some Bluetooth endpoints expose both generic mouse and vendor HID nodes.
                # Prefer vendor page candidates for feature-report traffic.
                if usage_page == 0xFF00:
                    score -= 30
                elif usage_page == 0x0001 and usage == 0x0002:
                    score += 20
            serial = entry.get("serial_number")
            if device.serial and serial and str(serial) == device.serial:
                score -= 5

            candidates.append(
                (
                    score,
                    path,
                    entry.get("interface_number"),
                    entry.get("usage_page"),
                    entry.get("usage"),
                )
            )

        deduped: List[Any] = []
        seen: set[str] = set()
        debug_rows: List[str] = []
        for score, path, interface_number, usage_page, usage in sorted(candidates, key=lambda pair: pair[0]):
            key = self._path_to_text(path)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
            if self._debug_enabled():
                debug_rows.append(
                    f"{key}(score={score},if={interface_number},up={usage_page},u={usage})"
                )

        if debug_rows:
            self._debug("candidate paths=" + ", ".join(debug_rows))

        return deduped

    def _send_command_payload(
        self,
        hid_dev: Any,
        report_id: int,
        request: bytes,
        payload: bytes,
        *,
        experimental: bool,
    ) -> Tuple[bool, str]:
        sent = hid_dev.send_feature_report(payload)
        if sent > 0:
            return True, "feature+rid"

        if report_id == 0x00:
            # Some hosts expect no explicit RID prefix for feature report ID 0x00.
            sent = hid_dev.send_feature_report(request)
            if sent > 0:
                return True, "feature-no-rid"

        if not experimental:
            return False, "feature-failed"

        # Experimental fallbacks for BT endpoints where feature reports are blocked.
        output_fn = getattr(hid_dev, "send_output_report", None)
        if callable(output_fn):
            try:
                sent = output_fn(payload)
            except Exception:
                sent = -1
            if sent > 0:
                return True, "output+rid"

        write_fn = getattr(hid_dev, "write", None)
        if callable(write_fn):
            try:
                sent = write_fn(payload)
            except Exception:
                sent = -1
            if sent > 0:
                return True, "write+rid"
            if report_id == 0x00:
                try:
                    sent = write_fn(request)
                except Exception:
                    sent = -1
                if sent > 0:
                    return True, "write-no-rid"

        return False, "send-failed"

    def _transceive(self, device: DetectedDevice, command: RazerCommand) -> Dict[str, Any]:
        self._ensure_hid()
        assert self._hid is not None

        profile = self._profile_for_pid(device.product_id)
        rgb_command = int(command.command_class) in {0x03, 0x0F}
        if profile is not None and profile.experimental:
            raw_attempts = os.getenv("RAZECLI_RAWHID_EXPERIMENTAL_ATTEMPTS", "4")
            parsed_attempts = self._parse_int_token(raw_attempts)
            attempts = parsed_attempts if parsed_attempts is not None else 4
            attempts = max(1, min(20, attempts))
        else:
            attempts = MAX_TRANSCEIVE_ATTEMPTS
        if rgb_command:
            # Fast-fail RGB by default to avoid multi-minute retries when an endpoint
            # consistently responds with unsupported/status errors.
            raw_rgb_attempts = os.getenv("RAZECLI_RAWHID_RGB_ATTEMPTS", "1")
            parsed_rgb_attempts = self._parse_int_token(raw_rgb_attempts)
            attempts = parsed_rgb_attempts if parsed_rgb_attempts is not None else 1
            attempts = max(1, min(6, attempts))

        tx_candidates = self._override_candidates_from_env(
            "RAZECLI_RAWHID_TX_IDS",
            self._transaction_candidates_for_pid(device.product_id),
        )
        if (
            rgb_command
            and int(device.product_id) in {0x007A, 0x007B}
            and self._env_flag("RAZECLI_RAWHID_RGB_TX_FIRST_ONLY", default=True)
            and tx_candidates
        ):
            tx_candidates = (int(tx_candidates[0]) & 0xFF,)
        report_id_candidates = self._override_candidates_from_env(
            "RAZECLI_RAWHID_REPORT_IDS",
            self._report_id_candidates_for_pid(device.product_id),
        )
        experimental = bool(profile is not None and profile.experimental)
        if rgb_command and self._env_flag("RAZECLI_RAWHID_RGB_EXPERIMENTAL_SEND", default=True):
            # RGB on macOS sometimes rejects feature reports on one collection but accepts
            # output/write style transport on another.
            experimental = True
        last_error: Optional[str] = None

        for _attempt in range(attempts):
            prefer_usage: Optional[int] = None
            avoid_usage: Optional[int] = None
            # RGB feature reports can live on non-mouse HID collections on some models.
            if rgb_command:
                raw_prefer_usage = os.getenv("RAZECLI_RAWHID_RGB_PREFER_USAGE", "0x06")
                parsed_usage = self._parse_int_token(str(raw_prefer_usage))
                if parsed_usage is not None:
                    prefer_usage = int(parsed_usage) & 0xFFFF
                raw_avoid_usage = os.getenv("RAZECLI_RAWHID_RGB_AVOID_USAGE", "0x02")
                parsed_avoid = self._parse_int_token(str(raw_avoid_usage))
                if parsed_avoid is not None:
                    avoid_usage = int(parsed_avoid) & 0xFFFF

            paths = self._candidate_paths(
                device,
                prefer_usage=prefer_usage,
                avoid_usage=avoid_usage,
            )
            if (
                rgb_command
                and int(device.product_id) in {0x007A, 0x007B}
                and self._env_flag("RAZECLI_RAWHID_RGB_PRIMARY_PATH_ONLY", default=True)
                and paths
            ):
                paths = [paths[0]]
            if not paths:
                last_error = "no candidate HID paths"
                time.sleep(0.04)
                continue

            if (
                rgb_command
                and len(paths) > 1
                and self._env_flag("RAZECLI_RAWHID_RGB_REORDER_PATHS", default=False)
            ):
                # The detect-time path is often generic mouse collection; try scored RGB
                # candidates first and keep detect path as last fallback.
                handle = device.backend_handle if isinstance(device.backend_handle, dict) else {}
                initial_path = handle.get("path")
                if initial_path is not None:
                    initial_key = self._path_to_text(initial_path)
                    front: List[Any] = []
                    tail: List[Any] = []
                    for candidate in paths:
                        if self._path_to_text(candidate) == initial_key:
                            tail.append(candidate)
                        else:
                            front.append(candidate)
                    if tail and front:
                        paths = front + tail
                        self._debug(
                            "rgb reordered paths="
                            + ", ".join(self._path_to_text(candidate) for candidate in paths)
                        )

            for path in paths:
                hid_dev = self._hid.device()
                try:
                    hid_dev.open_path(path)
                    # Avoid hanging in case device stalls.
                    try:
                        hid_dev.set_nonblocking(0)
                    except Exception:
                        pass

                    for report_id in report_id_candidates:
                        for tx in tx_candidates:
                            request = _build_report(command, tx)
                            payload = bytes([report_id]) + request

                            self._debug(
                                f"tx attempt usb={device.usb_id()} path={self._path_to_text(path)} "
                                f"rid=0x{report_id:02X} tx=0x{tx:02X} payload_len={len(payload)}"
                            )
                            sent_ok, send_mode = self._send_command_payload(
                                hid_dev,
                                report_id,
                                request,
                                payload,
                                experimental=experimental,
                            )
                            if not sent_ok:
                                hid_err = self._hid_error_text(hid_dev)
                                last_error = (
                                    "send_feature_report failed "
                                    f"(rid=0x{report_id:02X} tx=0x{tx:02X})"
                                )
                                if hid_err:
                                    last_error += f" hid_error={hid_err}"
                                self._debug(last_error)
                                continue
                            self._debug(
                                f"send ok via {send_mode} (rid=0x{report_id:02X} tx=0x{tx:02X})"
                            )

                            time.sleep(0.006)

                            raw_response = hid_dev.get_feature_report(report_id, 91)
                            response = _normalize_feature_response(raw_response)
                            if response is None:
                                raw_response = hid_dev.get_feature_report(report_id, 90)
                                response = _normalize_feature_response(raw_response)
                            if response is None:
                                last_error = (
                                    "invalid response length "
                                    f"(rid=0x{report_id:02X} tx=0x{tx:02X})"
                                )
                                continue

                            fields = _extract_response_fields(response)

                            if fields["command_class"] != command.command_class:
                                last_error = (
                                    "response command mismatch "
                                    f"expected class {command.command_class:02X}, "
                                    f"got {fields['command_class']:02X}"
                                )
                                continue
                            resp_cid = int(fields["command_id"])
                            req_cid = int(command.command_id)
                            cmd_ok = resp_cid == req_cid
                            # Chroma GET requests use 0x8x; some firmware echoes 0x0x in the response.
                            if (
                                not cmd_ok
                                and int(command.command_class) == 0x03
                                and (req_cid & 0x80)
                                and resp_cid == (req_cid & 0x7F)
                            ):
                                cmd_ok = True
                            if not cmd_ok:
                                last_error = (
                                    "response command mismatch "
                                    f"expected {command.command_class:02X}:{command.command_id:02X}, "
                                    f"got {fields['command_class']:02X}:{fields['command_id']:02X}"
                                )
                                continue

                            if fields["transaction_id"] != tx:
                                last_error = (
                                    "transaction mismatch "
                                    f"expected 0x{tx:02X}, got 0x{fields['transaction_id']:02X}"
                                )
                                continue

                            if fields["status"] == 0x01:
                                # Busy, retry same command quickly a few times.
                                busy_ok = False
                                for _ in range(3):
                                    time.sleep(0.01)
                                    raw_response = hid_dev.get_feature_report(report_id, 91)
                                    response = _normalize_feature_response(raw_response)
                                    if response is None:
                                        continue
                                    fields = _extract_response_fields(response)
                                    if fields["status"] == 0x02:
                                        busy_ok = True
                                        break
                                if not busy_ok and fields["status"] != 0x02:
                                    last_error = f"device busy status=0x{fields['status']:02X}"
                                    self._debug(last_error)
                                    continue

                            if fields["status"] != 0x02:
                                last_error = f"device status=0x{fields['status']:02X}"
                                self._debug(last_error)
                                if (
                                    rgb_command
                                    and self._env_flag("RAZECLI_RAWHID_RGB_STATUS_FAST_FAIL", default=True)
                                    and int(fields["status"]) in {0x03, 0x04, 0x05}
                                ):
                                    raise BackendUnavailableError(
                                        f"Raw HID communication failed for {device.usb_id()}: {last_error}"
                                    )
                                continue

                            # Keep latest working path to reduce stale-path open failures.
                            if isinstance(device.backend_handle, dict):
                                device.backend_handle["path"] = path
                                device.backend_handle["path_text"] = self._path_to_text(path)

                            return fields
                except Exception as exc:  # pragma: no cover - host dependent
                    last_error = str(exc)
                    self._debug(
                        f"path failed usb={device.usb_id()} path={self._path_to_text(path)} error={last_error}"
                    )
                finally:
                    try:
                        hid_dev.close()
                    except Exception:
                        pass

            time.sleep(0.08)

        profile = self._profile_for_pid(device.product_id)
        if profile is not None and profile.experimental:
            raise CapabilityUnsupportedError(
                f"Raw HID support for PID 0x{device.product_id:04X} is experimental and failed on this host. "
                f"Last error: {last_error or 'unknown'}. "
                "Set RAZECLI_RAWHID_DEBUG=1 for probe logs."
            )

        raise BackendUnavailableError(
            f"Raw HID communication failed for {device.usb_id()}: {last_error or 'unknown'}"
        )

    @staticmethod
    def _dpi_command_set(dpi_x: int, dpi_y: int) -> RazerCommand:
        if dpi_x < 100 or dpi_y < 100:
            raise CapabilityUnsupportedError("DPI must be at least 100")
        if dpi_x > 20000 or dpi_y > 20000:
            raise CapabilityUnsupportedError("DPI must be at most 20000")

        return RazerCommand(
            command_class=0x04,
            command_id=0x05,
            data_size=0x07,
            arguments=(
                0x01,  # VARSTORE for this command family
                (dpi_x >> 8) & 0xFF,
                dpi_x & 0xFF,
                (dpi_y >> 8) & 0xFF,
                dpi_y & 0xFF,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _dpi_command_get() -> RazerCommand:
        return RazerCommand(
            command_class=0x04,
            command_id=0x85,
            data_size=0x07,
            arguments=(0x00,),  # NOSTORE
        )

    @staticmethod
    def _dpi_stages_get_command() -> RazerCommand:
        return RazerCommand(
            command_class=0x04,
            command_id=0x86,
            data_size=0x26,
            arguments=(0x01,),  # VARSTORE
        )

    @staticmethod
    def _dpi_stages_set_command(active_stage: int, stages: Sequence[Tuple[int, int]]) -> RazerCommand:
        if not stages:
            raise CapabilityUnsupportedError("At least one DPI profile is required")
        if len(stages) > MAX_DPI_STAGES:
            raise CapabilityUnsupportedError(f"At most {MAX_DPI_STAGES} DPI profiles are supported")
        if active_stage < 1 or active_stage > len(stages):
            raise CapabilityUnsupportedError(
                f"Active DPI profile must be between 1 and {len(stages)}"
            )

        arguments: List[int] = [0x01, int(active_stage), len(stages)]
        for index, (dpi_x, dpi_y) in enumerate(stages):
            if dpi_x < 100 or dpi_y < 100:
                raise CapabilityUnsupportedError("DPI must be at least 100")
            if dpi_x > 20000 or dpi_y > 20000:
                raise CapabilityUnsupportedError("DPI must be at most 20000")

            arguments.extend(
                [
                    index & 0xFF,  # stage number
                    (int(dpi_x) >> 8) & 0xFF,
                    int(dpi_x) & 0xFF,
                    (int(dpi_y) >> 8) & 0xFF,
                    int(dpi_y) & 0xFF,
                    0x00,
                    0x00,
                ]
            )

        return RazerCommand(
            command_class=0x04,
            command_id=0x06,
            data_size=0x26,
            arguments=tuple(arguments),
        )

    @staticmethod
    def _poll_set_command(hz: int) -> RazerCommand:
        code = POLL_RATE_TO_CODE.get(hz)
        if code is None:
            raise CapabilityUnsupportedError("Poll-rate must be one of: 125, 500, 1000")

        return RazerCommand(command_class=0x00, command_id=0x05, data_size=0x01, arguments=(code,))

    @staticmethod
    def _poll_get_command() -> RazerCommand:
        return RazerCommand(command_class=0x00, command_id=0x85, data_size=0x01)

    @staticmethod
    def _battery_get_command() -> RazerCommand:
        return RazerCommand(command_class=0x07, command_id=0x80, data_size=0x02)

    @staticmethod
    def _rawhid_parse_rgb_hex(color: Optional[str]) -> Tuple[int, int, int]:
        text = (color or "00ff00").strip().lower()
        if text.startswith("#"):
            text = text[1:]
        if len(text) != 6:
            raise ValueError("RGB color must be 6 hex digits")
        raw = bytes.fromhex(text)
        return int(raw[0]), int(raw[1]), int(raw[2])

    @staticmethod
    def _rawhid_scale_rgb_brightness(r: int, g: int, b: int, brightness_percent: int) -> Tuple[int, int, int]:
        p = max(0, min(100, int(brightness_percent)))
        factor = p / 100.0
        return (
            int(round(r * factor)),
            int(round(g * factor)),
            int(round(b * factor)),
        )

    @classmethod
    def _rgb_zone_leds(cls) -> Tuple[int, ...]:
        leds = cls._override_candidates_from_env("RAZECLI_RAWHID_RGB_LEDS", _RAWHID_RGB_ZONE_LEDS)
        deduped: List[int] = []
        for led in leds:
            value = int(led) & 0xFF
            if value in deduped:
                continue
            deduped.append(value)
        return tuple(deduped) if deduped else _RAWHID_RGB_ZONE_LEDS

    def _rgb_protocols(self) -> Tuple[str, ...]:
        raw = str(os.getenv("RAZECLI_RAWHID_RGB_PROTOCOLS", "") or "").strip().lower()
        if not raw:
            return _RAWHID_RGB_PROTOCOLS
        requested: List[str] = []
        for token in raw.split(","):
            name = str(token).strip().lower()
            if name not in _RAWHID_RGB_PROTOCOLS:
                continue
            if name in requested:
                continue
            requested.append(name)
        return tuple(requested) if requested else _RAWHID_RGB_PROTOCOLS

    @classmethod
    def _rgb_varstores(cls) -> Tuple[int, ...]:
        raw = str(os.getenv("RAZECLI_RAWHID_RGB_VARSTORES", "0x01,0x00") or "").strip().lower()
        if not raw:
            return (_VARSTORE,)
        parsed: List[int] = []
        for token in raw.split(","):
            value = cls._parse_int_token(str(token))
            if value is None:
                continue
            value_u8 = int(value) & 0xFF
            if value_u8 in parsed:
                continue
            parsed.append(value_u8)
        return tuple(parsed) if parsed else (_VARSTORE,)

    @staticmethod
    def _with_varstore(command: RazerCommand, varstore: int) -> RazerCommand:
        if not command.arguments:
            return command
        args = list(command.arguments)
        args[0] = int(varstore) & 0xFF
        return RazerCommand(
            command_class=int(command.command_class),
            command_id=int(command.command_id),
            data_size=int(command.data_size),
            arguments=tuple(args),
        )

    @staticmethod
    def _chroma_extended_matrix_static_command(led_id: int, r: int, g: int, b: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x0F,
            command_id=0x02,
            data_size=0x09,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x01,
                0x00,
                0x00,
                0x01,
                r & 0xFF,
                g & 0xFF,
                b & 0xFF,
            ),
        )

    @staticmethod
    def _chroma_extended_matrix_none_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x0F,
            command_id=0x02,
            data_size=0x06,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x00,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_extended_matrix_spectrum_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x0F,
            command_id=0x02,
            data_size=0x06,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x03,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_extended_matrix_breathing_single_command(led_id: int, r: int, g: int, b: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x0F,
            command_id=0x02,
            data_size=0x09,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x02,
                0x01,
                0x00,
                0x01,
                r & 0xFF,
                g & 0xFF,
                b & 0xFF,
            ),
        )

    @staticmethod
    def _chroma_extended_matrix_breathing_random_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x0F,
            command_id=0x02,
            data_size=0x06,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x02,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_extended_matrix_mode_switch_command(
        led_id: int,
        *,
        mode_code: int = 0x08,
        varstore: int = 0x00,
    ) -> RazerCommand:
        return RazerCommand(
            command_class=0x0F,
            command_id=0x02,
            data_size=0x06,
            arguments=(
                int(varstore) & 0xFF,
                int(led_id) & 0xFF,
                int(mode_code) & 0xFF,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_mouse_extended_static_command(led_id: int, r: int, g: int, b: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0D,
            data_size=0x09,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x01,
                0x00,
                0x00,
                0x01,
                r & 0xFF,
                g & 0xFF,
                b & 0xFF,
            ),
        )

    @staticmethod
    def _chroma_mouse_extended_none_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0D,
            data_size=0x06,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x00,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_mouse_extended_spectrum_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0D,
            data_size=0x06,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x03,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_mouse_extended_breathing_single_command(led_id: int, r: int, g: int, b: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0D,
            data_size=0x09,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x02,
                0x01,
                0x00,
                0x01,
                r & 0xFF,
                g & 0xFF,
                b & 0xFF,
            ),
        )

    @staticmethod
    def _chroma_mouse_extended_breathing_random_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0D,
            data_size=0x06,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x02,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_mouse_standard_static_command(led_id: int, r: int, g: int, b: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0A,
            data_size=0x09,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x01,
                0x00,
                0x00,
                0x01,
                r & 0xFF,
                g & 0xFF,
                b & 0xFF,
            ),
        )

    @staticmethod
    def _chroma_mouse_standard_none_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0A,
            data_size=0x06,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x00,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_mouse_standard_spectrum_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0A,
            data_size=0x06,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x03,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_mouse_standard_breathing_single_command(led_id: int, r: int, g: int, b: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0A,
            data_size=0x09,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x02,
                0x01,
                0x00,
                0x01,
                r & 0xFF,
                g & 0xFF,
                b & 0xFF,
            ),
        )

    @staticmethod
    def _chroma_mouse_standard_breathing_random_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x0A,
            data_size=0x06,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x02,
                0x00,
                0x00,
                0x00,
            ),
        )

    @staticmethod
    def _chroma_standard_set_led_state_command(led_id: int, enabled: bool) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x00,
            data_size=0x03,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                0x01 if enabled else 0x00,
            ),
        )

    @staticmethod
    def _chroma_standard_set_device_mode_command(mode: int, param: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x00,
            command_id=0x04,
            data_size=0x02,
            arguments=(
                int(mode) & 0xFF,
                int(param) & 0xFF,
            ),
        )

    @staticmethod
    def _chroma_standard_set_led_brightness_command(led_id: int, brightness_u8: int) -> RazerCommand:
        bu8 = max(0, min(255, int(brightness_u8)))
        return RazerCommand(
            command_class=0x03,
            command_id=0x03,
            data_size=0x03,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
                bu8,
            ),
        )

    @staticmethod
    def _chroma_standard_get_led_rgb_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x81,
            data_size=0x05,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
            ),
        )

    @staticmethod
    def _chroma_standard_get_led_brightness_command(led_id: int) -> RazerCommand:
        return RazerCommand(
            command_class=0x03,
            command_id=0x83,
            data_size=0x03,
            arguments=(
                _VARSTORE,
                led_id & 0xFF,
            ),
        )

    def _rgb_effect_command(
        self,
        *,
        protocol: str,
        mode: str,
        led_id: int,
        r: int,
        g: int,
        b: int,
    ) -> RazerCommand:
        if protocol == "mouse-standard":
            if mode == "off":
                return self._chroma_mouse_standard_none_command(led_id)
            if mode == "static":
                return self._chroma_mouse_standard_static_command(led_id, r, g, b)
            if mode == "spectrum":
                return self._chroma_mouse_standard_spectrum_command(led_id)
            if mode == "breathing-single":
                return self._chroma_mouse_standard_breathing_single_command(led_id, r, g, b)
            if mode == "breathing-random":
                return self._chroma_mouse_standard_breathing_random_command(led_id)
            raise CapabilityUnsupportedError(f"Unknown RGB mode '{mode}' for protocol {protocol}")

        if protocol == "mouse-extended":
            if mode == "off":
                return self._chroma_mouse_extended_none_command(led_id)
            if mode == "static":
                return self._chroma_mouse_extended_static_command(led_id, r, g, b)
            if mode == "spectrum":
                return self._chroma_mouse_extended_spectrum_command(led_id)
            if mode == "breathing-single":
                return self._chroma_mouse_extended_breathing_single_command(led_id, r, g, b)
            if mode == "breathing-random":
                return self._chroma_mouse_extended_breathing_random_command(led_id)
            raise CapabilityUnsupportedError(f"Unknown RGB mode '{mode}' for protocol {protocol}")

        if mode == "off":
            return self._chroma_extended_matrix_none_command(led_id)
        if mode == "static":
            return self._chroma_extended_matrix_static_command(led_id, r, g, b)
        if mode == "spectrum":
            return self._chroma_extended_matrix_spectrum_command(led_id)
        if mode == "breathing-single":
            return self._chroma_extended_matrix_breathing_single_command(led_id, r, g, b)
        if mode == "breathing-random":
            return self._chroma_extended_matrix_breathing_random_command(led_id)
        raise CapabilityUnsupportedError(f"Unknown RGB mode '{mode}' for protocol {protocol}")

    def _rgb_preamble_commands(self, *, device: DetectedDevice, led_id: int) -> Tuple[RazerCommand, ...]:
        if int(device.product_id) not in {0x007A, 0x007B}:
            return ()
        if self._env_flag("RAZECLI_RAWHID_RGB_SKIP_MODE_SWITCH", default=False):
            return ()
        # Inspired by OpenRazer: keep mouse in driver mode and prime mode switch
        # sequence before RGB writes on Viper Ultimate family.
        return (
            self._chroma_standard_set_device_mode_command(0x03, 0x00),
            self._chroma_extended_matrix_mode_switch_command(led_id, mode_code=0x08, varstore=0x00),
            self._chroma_extended_matrix_mode_switch_command(0x00, mode_code=0x08, varstore=0x00),
            self._chroma_extended_matrix_mode_switch_command(led_id, mode_code=0x08, varstore=0x01),
        )

    def _rgb_known_unsupported(self, device: DetectedDevice) -> bool:
        # Viper Ultimate wired endpoint (007A) frequently rejects all RGB write/read
        # commands on macOS rawhid with status 0x03/0x05. Keep attempts opt-in.
        if int(device.product_id) == 0x007A:
            return not self._env_flag("RAZECLI_RAWHID_RGB_FORCE_007A", default=False)
        return False

    def get_rgb(self, device: DetectedDevice) -> Dict[str, Any]:
        self._require_capability(device, "rgb")
        if self._rgb_known_unsupported(device):
            raise CapabilityUnsupportedError(
                "Onboard RGB read is unsupported for 1532:007A on this host/backend. "
                "Use Synapse for live RGB control, or set RAZECLI_RAWHID_RGB_FORCE_007A=1 "
                "to force low-level rawhid probing."
            )
        modes_supported = [
            "off",
            "static",
            "spectrum",
            "breathing",
            "breathing-single",
            "breathing-random",
        ]
        zone_leds = self._rgb_zone_leds()
        varstores = self._rgb_varstores()
        r, g, b = 0, 255, 0
        brightness = 100
        color_confidence = "inferred-default"
        brightness_confidence = "inferred-default"
        color_led: Optional[int] = None
        brightness_led: Optional[int] = None
        fast_fail = self._env_flag("RAZECLI_RAWHID_RGB_FAST_FAIL", default=True)
        terminal_failure = False

        for led in zone_leds:
            for varstore in varstores:
                try:
                    fields = self._transceive(
                        device,
                        self._with_varstore(self._chroma_standard_get_led_rgb_command(led), varstore),
                    )
                    args = fields["arguments"]
                    r = int(args[2]) & 0xFF
                    g = int(args[3]) & 0xFF
                    b = int(args[4]) & 0xFF
                    color_confidence = "verified"
                    color_led = int(led) & 0xFF
                    break
                except Exception as exc:
                    self._debug(
                        "rgb read color failed "
                        f"usb={device.usb_id()} led=0x{int(led) & 0xFF:02X} "
                        f"varstore=0x{int(varstore) & 0xFF:02X}: {exc}"
                    )
                    if fast_fail and self._is_terminal_rgb_error(exc):
                        terminal_failure = True
                        break
                    continue
            if terminal_failure:
                break
            if color_confidence == "verified":
                break

        if not terminal_failure:
            for led in zone_leds:
                for varstore in varstores:
                    try:
                        fields_b = self._transceive(
                            device,
                            self._with_varstore(self._chroma_standard_get_led_brightness_command(led), varstore),
                        )
                        bu8 = int(fields_b["arguments"][2]) & 0xFF
                        brightness = int(max(0, min(100, round(bu8 * 100.0 / 255.0))))
                        brightness_confidence = "verified"
                        brightness_led = int(led) & 0xFF
                        break
                    except Exception as exc:
                        self._debug(
                            "rgb read brightness failed "
                            f"usb={device.usb_id()} led=0x{int(led) & 0xFF:02X} "
                            f"varstore=0x{int(varstore) & 0xFF:02X}: {exc}"
                        )
                        if fast_fail and self._is_terminal_rgb_error(exc):
                            terminal_failure = True
                            break
                        continue
                if terminal_failure:
                    break
                if brightness_confidence == "verified":
                    break

        if color_confidence == "verified" and brightness_confidence == "verified":
            overall_confidence = "verified"
        elif color_confidence == "verified" or brightness_confidence == "verified":
            overall_confidence = "mixed"
        else:
            overall_confidence = "inferred"
        mode = "off" if int(brightness) <= 0 else "static"
        return {
            "mode": mode,
            "brightness": brightness,
            "color": f"{r:02x}{g:02x}{b:02x}",
            "modes_supported": modes_supported,
            "read_confidence": {
                "overall": overall_confidence,
                "mode": "inferred",
                "color": color_confidence,
                "brightness": brightness_confidence,
                "color_led": f"0x{color_led:02X}" if color_led is not None else None,
                "brightness_led": f"0x{brightness_led:02X}" if brightness_led is not None else None,
            },
        }

    def set_rgb(
        self,
        device: DetectedDevice,
        *,
        mode: str,
        brightness: Optional[int] = None,
        color: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._require_capability(device, "rgb")
        if self._rgb_known_unsupported(device):
            raise CapabilityUnsupportedError(
                "Onboard RGB write is unsupported for 1532:007A on this host/backend. "
                "Use Synapse for live RGB control, or set RAZECLI_RAWHID_RGB_FORCE_007A=1 "
                "to force low-level rawhid writes."
            )
        requested = str(mode).strip().lower()
        if requested == "breathing":
            requested = "breathing-single"
        allowed = {
            "off",
            "static",
            "spectrum",
            "breathing-single",
            "breathing-random",
        }
        if requested not in allowed:
            raise CapabilityUnsupportedError(
                f"Raw HID RGB mode '{mode}' is not implemented. Supported: {', '.join(sorted(allowed))}"
            )
        try:
            br, bg, bb = self._rawhid_parse_rgb_hex(color)
        except ValueError as exc:
            raise CapabilityUnsupportedError(str(exc)) from exc

        brightness_pct = 100 if brightness is None else max(0, min(100, int(brightness)))
        if requested == "off":
            brightness_pct = 0

        sr, sg, sb = self._rawhid_scale_rgb_brightness(br, bg, bb, brightness_pct)
        brightness_u8 = max(0, min(255, int(round(brightness_pct * 255.0 / 100.0))))
        zone_leds = self._rgb_zone_leds()
        protocols = self._rgb_protocols()
        varstores = self._rgb_varstores()
        applied_protocols: List[str] = []
        applied_varstores: List[int] = []
        last_error: Optional[Exception] = None
        fast_fail = self._env_flag("RAZECLI_RAWHID_RGB_FAST_FAIL", default=True)

        for protocol in protocols:
            protocol_hits = 0
            terminal_protocol_error = False
            for led in zone_leds:
                led_applied = False
                for varstore in varstores:
                    try:
                        if protocol == "extended-matrix":
                            for preamble in self._rgb_preamble_commands(device=device, led_id=led):
                                try:
                                    self._transceive(device, preamble)
                                except Exception as exc:
                                    self._debug(
                                        "rgb preamble failed "
                                        f"usb={device.usb_id()} protocol={protocol} "
                                        f"led=0x{int(led) & 0xFF:02X} "
                                        f"varstore=0x{int(varstore) & 0xFF:02X}: {exc}"
                                    )

                        try:
                            self._transceive(
                                device,
                                self._with_varstore(
                                    self._chroma_standard_set_led_state_command(
                                        led,
                                        enabled=requested != "off",
                                    ),
                                    varstore,
                                ),
                            )
                        except Exception as exc:
                            self._debug(
                                "rgb led-state write failed "
                                f"usb={device.usb_id()} protocol={protocol} "
                                f"led=0x{int(led) & 0xFF:02X} "
                                f"varstore=0x{int(varstore) & 0xFF:02X}: {exc}"
                            )

                        cmd = self._with_varstore(
                            self._rgb_effect_command(
                                protocol=protocol,
                                mode=requested,
                                led_id=led,
                                r=sr,
                                g=sg,
                                b=sb,
                            ),
                            varstore,
                        )
                        self._transceive(device, cmd)
                        if (
                            protocol == "extended-matrix"
                            and requested == "static"
                            and int(device.product_id) in {0x007A, 0x007B}
                        ):
                            # OpenRazer uses a mode switch between static writes on some
                            # devices to avoid stale/queued color state.
                            try:
                                self._transceive(
                                    device,
                                    self._chroma_extended_matrix_mode_switch_command(
                                        led_id=led,
                                        mode_code=0x08,
                                        varstore=int(varstore) & 0xFF,
                                    ),
                                )
                            except Exception as exc:
                                self._debug(
                                    "rgb static mode-switch failed "
                                    f"usb={device.usb_id()} protocol={protocol} "
                                    f"led=0x{int(led) & 0xFF:02X} "
                                    f"varstore=0x{int(varstore) & 0xFF:02X}: {exc}"
                                )
                            self._transceive(device, cmd)
                        should_write_brightness = (
                            requested not in {"static"}
                            or int(brightness_pct) in {0, 100}
                        )
                        if should_write_brightness:
                            try:
                                self._transceive(
                                    device,
                                    self._with_varstore(
                                        self._chroma_standard_set_led_brightness_command(led, brightness_u8),
                                        varstore,
                                    ),
                                )
                            except Exception as exc:
                                last_error = exc
                                self._debug(
                                    "rgb brightness write failed "
                                f"usb={device.usb_id()} protocol={protocol} "
                                f"led=0x{int(led) & 0xFF:02X} "
                                f"varstore=0x{int(varstore) & 0xFF:02X}: {exc}"
                            )
                        if int(varstore) not in applied_varstores:
                            applied_varstores.append(int(varstore))
                        led_applied = True
                        break
                    except Exception as exc:
                        last_error = exc
                        self._debug(
                            "rgb write failed "
                            f"usb={device.usb_id()} protocol={protocol} "
                            f"led=0x{int(led) & 0xFF:02X} "
                            f"varstore=0x{int(varstore) & 0xFF:02X}: {exc}"
                        )
                        if fast_fail and self._is_terminal_rgb_error(exc):
                            terminal_protocol_error = True
                            break
                        continue
                if led_applied:
                    protocol_hits += 1
                if terminal_protocol_error:
                    break
            if protocol_hits > 0:
                applied_protocols.append(protocol)
            if terminal_protocol_error:
                # Fail-fast within this protocol, then continue to next protocol family.
                continue

        if not applied_protocols:
            if isinstance(last_error, Exception):
                raise CapabilityUnsupportedError(
                    f"Could not apply RGB over rawhid for {device.usb_id()} (no working protocol/zone)"
                ) from last_error
            raise CapabilityUnsupportedError(
                f"Could not apply RGB over rawhid for {device.usb_id()} (no working protocol/zone)"
            )

        # Prevent false-positive "applied" when firmware ACKs writes but RGB state
        # cannot be read back on this endpoint (common on unsupported dongle nodes).
        if self._env_flag("RAZECLI_RAWHID_RGB_VERIFY_WRITE", default=True):
            try:
                verified_state = self.get_rgb(device)
            except Exception as exc:
                raise CapabilityUnsupportedError(
                    f"RGB write could not be verified for {device.usb_id()}"
                ) from exc

            confidence = verified_state.get("read_confidence")
            overall = ""
            if isinstance(confidence, dict):
                overall = str(confidence.get("overall") or "").strip().lower()
            if overall in {"", "unknown", "inferred"}:
                raise CapabilityUnsupportedError(
                    f"RGB write is unverified on {device.usb_id()} (read confidence={overall or 'unknown'})"
                )

        return {
            "mode": requested,
            "brightness": brightness_pct,
            "color": f"{sr:02x}{sg:02x}{sb:02x}",
            "modes_supported": list(allowed),
            "hardware_apply": "applied",
            "write_protocols": list(applied_protocols),
            "write_zones": [f"0x{int(led) & 0xFF:02X}" for led in zone_leds],
            "write_varstores": [f"0x{int(varstore) & 0xFF:02X}" for varstore in applied_varstores],
        }

    def get_dpi(self, device: DetectedDevice) -> Tuple[int, int]:
        self._require_capability(device, "dpi")
        fields = self._transceive(device, self._dpi_command_get())
        args = fields["arguments"]
        dpi_x = (args[1] << 8) | args[2]
        dpi_y = (args[3] << 8) | args[4]
        return int(dpi_x), int(dpi_y)

    def set_dpi(self, device: DetectedDevice, dpi_x: int, dpi_y: int) -> None:
        self._require_capability(device, "dpi")
        self._transceive(device, self._dpi_command_set(int(dpi_x), int(dpi_y)))

    def get_dpi_stages(self, device: DetectedDevice) -> Tuple[int, Sequence[Tuple[int, int]]]:
        self._require_capability(device, "dpi-stages")
        fields = self._transceive(device, self._dpi_stages_get_command())
        args = fields["arguments"]

        active_stage = int(args[1])
        stages_count = int(args[2])
        stages: List[Tuple[int, int]] = []

        offset = 4
        for _ in range(min(stages_count, MAX_DPI_STAGES)):
            if offset + 3 >= len(args):
                break
            dpi_x = (int(args[offset]) << 8) | int(args[offset + 1])
            dpi_y = (int(args[offset + 2]) << 8) | int(args[offset + 3])
            stages.append((dpi_x, dpi_y))
            offset += 7

        if not stages:
            raise CapabilityUnsupportedError("Device returned no DPI profiles")
        if active_stage < 1 or active_stage > len(stages):
            raise CapabilityUnsupportedError(
                f"Device returned invalid active DPI profile: {active_stage}"
            )

        return active_stage, stages

    def set_dpi_stages(self, device: DetectedDevice, active_stage: int, stages: Sequence[Tuple[int, int]]) -> None:
        self._require_capability(device, "dpi-stages")
        self._transceive(device, self._dpi_stages_set_command(int(active_stage), stages))

    def get_poll_rate(self, device: DetectedDevice) -> int:
        self._require_capability(device, "poll-rate")
        fields = self._transceive(device, self._poll_get_command())
        code = int(fields["arguments"][0])
        rate = CODE_TO_POLL_RATE.get(code)
        if rate is None:
            raise CapabilityUnsupportedError(f"Unknown poll-rate code in response: 0x{code:02X}")
        return rate

    def set_poll_rate(self, device: DetectedDevice, hz: int) -> None:
        self._require_capability(device, "poll-rate")
        self._transceive(device, self._poll_set_command(int(hz)))

    def get_supported_poll_rates(self, device: DetectedDevice) -> Sequence[int]:
        self._require_capability(device, "poll-rate")
        _ = device
        return [125, 500, 1000]

    def get_battery(self, device: DetectedDevice) -> int:
        self._require_capability(device, "battery")
        fields = self._transceive(device, self._battery_get_command())
        raw_level = int(fields["arguments"][1])
        if raw_level < 0:
            return -1
        return int(round((raw_level / 255.0) * 100.0))


__all__ = [
    "RawHidBackend",
    "RazerCommand",
    "_build_report",
    "_extract_response_fields",
    "_normalize_feature_response",
]
