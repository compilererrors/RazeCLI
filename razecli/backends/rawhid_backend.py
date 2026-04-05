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

    def _candidate_paths(self, device: DetectedDevice) -> List[Any]:
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
        if profile is not None and profile.experimental:
            raw_attempts = os.getenv("RAZECLI_RAWHID_EXPERIMENTAL_ATTEMPTS", "4")
            parsed_attempts = self._parse_int_token(raw_attempts)
            attempts = parsed_attempts if parsed_attempts is not None else 4
            attempts = max(1, min(20, attempts))
        else:
            attempts = MAX_TRANSCEIVE_ATTEMPTS

        tx_candidates = self._override_candidates_from_env(
            "RAZECLI_RAWHID_TX_IDS",
            self._transaction_candidates_for_pid(device.product_id),
        )
        report_id_candidates = self._override_candidates_from_env(
            "RAZECLI_RAWHID_REPORT_IDS",
            self._report_id_candidates_for_pid(device.product_id),
        )
        experimental = bool(profile is not None and profile.experimental)
        last_error: Optional[str] = None

        for _attempt in range(attempts):
            paths = self._candidate_paths(device)
            if not paths:
                last_error = "no candidate HID paths"
                time.sleep(0.04)
                continue

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

                            if (
                                fields["command_class"] != command.command_class
                                or fields["command_id"] != command.command_id
                            ):
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
                                continue

                            # Keep latest working path to reduce stale-path open failures.
                            if isinstance(device.backend_handle, dict):
                                device.backend_handle["path"] = path
                                device.backend_handle["path_text"] = self._path_to_text(path)

                            return fields
                except Exception as exc:  # pragma: no cover - host dependent
                    last_error = str(exc)
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
