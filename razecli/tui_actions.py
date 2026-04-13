"""State and mutation actions for the curses TUI."""

from __future__ import annotations

import curses
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from razecli.dpi_autosync import (
    AUTOSYNC_PRESET_PREFIX,
    autosync_enabled,
    load_autosync_settings,
    save_autosync_settings,
)
from razecli.dpi_stage_presets import (
    delete_dpi_stage_preset,
    list_dpi_stage_presets,
    load_dpi_stage_preset,
    save_dpi_stage_preset,
)
from razecli.errors import CapabilityUnsupportedError
from razecli.feature_scaffolds import (
    delete_rgb_preset,
    get_button_mapping_scaffold,
    get_rgb_presets,
    get_rgb_scaffold,
    save_rgb_preset,
    set_button_mapping_scaffold,
    set_rgb_scaffold,
)
from razecli.transport_sync import mirror_to_transport_targets
from razecli.types import DetectedDevice

from razecli.tui_types import DeviceState, MAX_DPI_STAGES


class TuiActionsMixin:
    _BUTTON_LABELS = {
        "left_click": "Mouse 1 (Left)",
        "right_click": "Mouse 2 (Right)",
        "middle_click": "Mouse 3 (Middle)",
        "side_1": "Mouse 4 (Rear Side)",
        "side_2": "Mouse 5 (Front Side)",
        "dpi_cycle": "DPI Button",
    }

    @staticmethod
    def _normalize_hex_color(value: str) -> str:
        text = str(value or "").strip().lower()
        if text.startswith("#"):
            text = text[1:]
        if len(text) != 6 or any(ch not in "0123456789abcdef" for ch in text):
            raise ValueError("Color must be a 6-digit hex value, for example 00ff88")
        return text

    @classmethod
    def _button_label(cls, button: str) -> str:
        key = str(button).strip()
        return cls._BUTTON_LABELS.get(key, key)

    @classmethod
    def _ascii_mouse_diagram(cls, selected_button: Optional[str] = None) -> List[str]:
        selected = str(selected_button or "").strip()

        def _mk(button: str, short_label: str, *, pad: bool = False) -> str:
            marker = ">" if selected == button else " "
            label = str(short_label)
            if pad:
                label = label.ljust(3)
            return f"[{marker}{label}]"

        m1 = _mk("left_click", "M1")
        m2 = _mk("right_click", "M2")
        m3 = _mk("middle_click", "M3")
        m4 = _mk("side_1", "M4")
        m5 = _mk("side_2", "M5")
        dpi = _mk("dpi_cycle", "DPI")
        m1_chip = _mk("left_click", "M1", pad=True)
        m2_chip = _mk("right_click", "M2", pad=True)
        m3_chip = _mk("middle_click", "M3", pad=True)
        m4_chip = _mk("side_1", "M4", pad=True)
        m5_chip = _mk("side_2", "M5", pad=True)
        dpi_chip = _mk("dpi_cycle", "DPI", pad=True)
        selected_label = cls._button_label(selected) if selected else "-"

        return [
            "Mouse map:",
            "       ,d88b",
            "    ,8P'     `8,",
            "    8'       __.8.__",
            "   8       .'    |   '.",
            f"         / {m1} | {m2}\\",
            f"         |      {m3}    |",
            f"    {m5}|       |       |",
            f"    {m4}|---------------|",
            "         |               |",
            "         |               |",
            "         |;             .|",
            "         ;\\            /;",
            "          \\\\         //",
            "           \\'._ --_.'/",
            "             '-....-'",
            "",
            f"Buttons: {m1_chip} {m2_chip} {m3_chip} {m4_chip} {m5_chip} {dpi_chip}",
            f"Selected: {selected_label}",
        ]

    @staticmethod
    def _clip_text(line: str, width: int) -> str:
        text = str(line)
        if width <= 0:
            return ""
        if len(text) <= width:
            return text
        if width <= 1:
            return text[:width]
        return text[: width - 1] + "."

    @staticmethod
    def _estimate_modal_text_width(stdscr) -> int:
        _height, width = stdscr.getmaxyx()
        max_box_w = max(50, min(width - 6, 104))
        box_w = max(50, min(max_box_w, width - 4))
        return max(20, box_w - 4)

    def _build_button_mapping_modal_lines(
        self,
        stdscr,
        *,
        buttons: Sequence[str],
        mapping: Dict[str, Any],
        selected_index: int,
    ) -> List[str]:
        selected_button = str(buttons[selected_index])
        left_raw: List[str] = [
            "Up/Down: select button | Enter: edit action | c: capture select | r: reload | q/Esc: close",
            "",
            "Button table",
        ]
        for idx, button in enumerate(buttons):
            marker = ">" if idx == selected_index else " "
            action_value = str(mapping.get(button) or "-")
            left_raw.append(f"{marker} {self._button_label(button):<20} -> {action_value}")

        right_raw = self._ascii_mouse_diagram(selected_button)
        text_w = self._estimate_modal_text_width(stdscr)

        # For narrow terminals, keep a stacked modal to avoid cramped rendering.
        if text_w < 84:
            return left_raw + [""] + right_raw

        left_w = max(36, min(54, int(text_w * 0.56)))
        right_w = max(24, text_w - left_w - 3)
        if left_w + right_w + 3 > text_w:
            left_w = max(30, text_w - right_w - 3)

        left_lines: List[str] = []
        right_lines: List[str] = []
        for line in left_raw:
            if not line:
                left_lines.append("")
                continue
            left_lines.extend(self._wrap_line(line, left_w))
        for line in right_raw:
            if not line:
                right_lines.append("")
                continue
            right_lines.extend(self._wrap_line(line, right_w))

        rows = max(len(left_lines), len(right_lines))
        body: List[str] = []
        for idx in range(rows):
            left = left_lines[idx] if idx < len(left_lines) else ""
            right = right_lines[idx] if idx < len(right_lines) else ""
            body.append(
                f"{self._clip_text(left, left_w):<{left_w}} | "
                f"{self._clip_text(right, right_w):<{right_w}}"
            )
        return body

    def _schedule_state_refresh(self, *, force: bool = True) -> None:
        if hasattr(self, "_queue_state_refresh"):
            self._queue_state_refresh(force=force)
            return
        self._refresh_state(force=force)

    def _set_status(self, message: str, *, hold_seconds: float = 0.0) -> None:
        self.status = str(message)
        if hold_seconds > 0:
            self._status_hold_until = max(
                float(getattr(self, "_status_hold_until", 0.0)),
                time.monotonic() + float(hold_seconds),
            )

    def _status_locked(self) -> bool:
        return time.monotonic() < float(getattr(self, "_status_hold_until", 0.0))

    def _show_action_dialog(self, stdscr, *, title: str, message: str) -> None:
        try:
            self._show_modal_message(stdscr, title=title, message=message)
        except Exception:
            self._set_status(message, hold_seconds=8.0)

    @staticmethod
    def _is_detect_only_backend(device: Optional[DetectedDevice]) -> bool:
        return bool(device and device.backend == "macos-profiler")

    def _model_spec(self, device: Optional[DetectedDevice]):
        if device is None or not device.model_id:
            return None
        service = getattr(self, "service", None)
        registry = getattr(service, "registry", None)
        if registry is None or not hasattr(registry, "get"):
            return None
        try:
            return registry.get(device.model_id)
        except Exception:
            return None

    @staticmethod
    def _usb_pid_label(device: Optional[DetectedDevice]) -> str:
        if not device:
            return "unknown"
        try:
            return f"{int(device.vendor_id):04X}:{int(device.product_id):04X}"
        except Exception:
            return "unknown"

    def _is_ble_endpoint_device(self, device: Optional[DetectedDevice]) -> bool:
        if not device or device.backend not in {"rawhid", "macos-ble"}:
            return False
        model = self._model_spec(device)
        if model is None:
            return False
        endpoint_ids = tuple(getattr(model, "ble_endpoint_product_ids", ()) or ())
        try:
            return int(device.product_id) in {int(pid) for pid in endpoint_ids}
        except Exception:
            return False

    def _is_bt_008e(self, device: Optional[DetectedDevice]) -> bool:
        # Backwards-compatible alias while call sites migrate to generic naming.
        return self._is_ble_endpoint_device(device)

    def _is_experimental_ble_endpoint(self, device: Optional[DetectedDevice]) -> bool:
        if not self._is_ble_endpoint_device(device):
            return False
        model = self._model_spec(device)
        return bool(model and getattr(model, "ble_endpoint_experimental", False))

    def _ble_multi_profile_table_limited(self, device: Optional[DetectedDevice]) -> bool:
        model = self._model_spec(device)
        return bool(model and getattr(model, "ble_multi_profile_table_limited", False))

    def _has_onboard_profile_bank_switch(self, device: Optional[DetectedDevice]) -> bool:
        model = self._model_spec(device)
        return bool(model and getattr(model, "onboard_profile_bank_switch", False))

    @staticmethod
    def _unsafe_stage_activate_enabled() -> bool:
        """Return True when legacy stage activation should rewrite stage layout."""
        value = os.getenv("RAZECLI_UNSAFE_STAGE_ACTIVATE", "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _refresh_devices(self, *, eager_state: bool = True) -> None:
        self._discovery_in_progress = True
        try:
            self.devices = self.service.discover_devices(
                model_filter=self.model_filter,
                collapse_transports=self.collapse_transports,
            )
            self._autosync_attempted.clear()

            active_ids = {device.identifier for device in self.devices}
            self._state_cache = {
                identifier: state
                for identifier, state in self._state_cache.items()
                if identifier in active_ids
            }
            self._state_refreshed_at = {
                identifier: ts
                for identifier, ts in self._state_refreshed_at.items()
                if identifier in active_ids
            }
            self._battery_refreshed_at = {
                identifier: ts
                for identifier, ts in self._battery_refreshed_at.items()
                if identifier in active_ids
            }
            rgb_cache = getattr(self, "_rgb_cache", None)
            if isinstance(rgb_cache, dict):
                self._rgb_cache = {
                    identifier: payload
                    for identifier, payload in rgb_cache.items()
                    if identifier in active_ids
                }
            button_cache = getattr(self, "_button_mapping_cache", None)
            if isinstance(button_cache, dict):
                self._button_mapping_cache = {
                    identifier: payload
                    for identifier, payload in button_cache.items()
                    if identifier in active_ids
                }
            bt_unavailable = getattr(self, "_bt_unavailable_fields", None)
            if isinstance(bt_unavailable, dict):
                self._bt_unavailable_fields = {
                    identifier: tuple(fields)
                    for identifier, fields in bt_unavailable.items()
                    if identifier in active_ids
                }
            prefetch_attempted = getattr(self, "_feature_prefetch_attempted", None)
            prefetch_inflight = getattr(self, "_feature_prefetch_inflight_devices", None)
            prefetch_retry_due = getattr(self, "_feature_prefetch_retry_due_at", None)
            prefetch_lock = getattr(self, "_feature_prefetch_lock", None)
            if (
                isinstance(prefetch_attempted, set)
                or isinstance(prefetch_inflight, set)
                or isinstance(prefetch_retry_due, dict)
            ):
                if hasattr(prefetch_lock, "__enter__") and hasattr(prefetch_lock, "__exit__"):
                    lock_ctx = prefetch_lock
                else:
                    lock_ctx = None
                if lock_ctx is not None:
                    with lock_ctx:
                        if isinstance(prefetch_attempted, set):
                            self._feature_prefetch_attempted = {
                                (identifier, feature)
                                for identifier, feature in prefetch_attempted
                                if identifier in active_ids
                            }
                        if isinstance(prefetch_inflight, set):
                            self._feature_prefetch_inflight_devices = {
                                identifier
                                for identifier in prefetch_inflight
                                if identifier in active_ids
                            }
                        if isinstance(prefetch_retry_due, dict):
                            self._feature_prefetch_retry_due_at = {
                                (identifier, feature): float(due_at)
                                for (identifier, feature), due_at in prefetch_retry_due.items()
                                if identifier in active_ids
                            }
                else:
                    if isinstance(prefetch_attempted, set):
                        self._feature_prefetch_attempted = {
                            (identifier, feature)
                            for identifier, feature in prefetch_attempted
                            if identifier in active_ids
                        }
                    if isinstance(prefetch_inflight, set):
                        self._feature_prefetch_inflight_devices = {
                            identifier for identifier in prefetch_inflight if identifier in active_ids
                        }
                    if isinstance(prefetch_retry_due, dict):
                        self._feature_prefetch_retry_due_at = {
                            (identifier, feature): float(due_at)
                            for (identifier, feature), due_at in prefetch_retry_due.items()
                            if identifier in active_ids
                        }

            if self.preselected_device_id:
                for idx, device in enumerate(self.devices):
                    if device.identifier == self.preselected_device_id:
                        self.selected_index = idx
                        self.preselected_device_id = None
                        break

            if self.selected_index >= len(self.devices):
                self.selected_index = max(0, len(self.devices) - 1)

            if not self.devices:
                if self.model_filter:
                    self.status = (
                        f"No devices matched model filter '{self.model_filter}'. "
                        "Clear the model filter to show all devices."
                    )
                else:
                    self.status = "No devices found"
                errors = self.service.backend_errors()
                if errors:
                    details = "; ".join(f"{backend}: {error}" for backend, error in errors.items())
                    self.status = f"No devices found | {details}"
                self.state = DeviceState()
                return

            selected = self._selected()
            self.status = f"Found {len(self.devices)} devices"
            if selected:
                self.status += f" | selected: {selected.identifier}"

            if eager_state:
                self._refresh_state(force=True)
            else:
                if selected is None:
                    self.state = DeviceState()
                else:
                    cached = self._state_cache.get(selected.identifier)
                    self.state = self._clone_state(cached) if cached is not None else DeviceState()
                    if cached is None:
                        self.status += " | loading device details..."
        finally:
            self._discovery_in_progress = False

    @staticmethod
    def _clone_state(state: DeviceState) -> DeviceState:
        return DeviceState(
            dpi=tuple(state.dpi) if state.dpi is not None else None,
            dpi_active_stage=state.dpi_active_stage,
            dpi_stages=list(state.dpi_stages) if state.dpi_stages is not None else None,
            onboard_bank_signature=state.onboard_bank_signature,
            onboard_bank_match=state.onboard_bank_match,
            poll_rate=state.poll_rate,
            battery=state.battery,
        )

    def _refresh_state(self, *, force: bool = False) -> None:
        device = self._selected()
        if device is None:
            self.state = DeviceState()
            self._state_loading_device_id = None
            return

        now = time.monotonic()
        cached_state = self._state_cache.get(device.identifier)
        last_refresh = self._state_refreshed_at.get(device.identifier, 0.0)
        if (
            not force
            and cached_state is not None
            and (now - last_refresh) < float(self._refresh_interval_s)
        ):
            self.state = self._clone_state(cached_state)
            self._state_loading_device_id = None
            return

        self._state_loading_device_id = device.identifier
        try:
            backend = self.service.resolve_backend(device)
            if autosync_enabled():
                self._maybe_apply_autosync(device, backend)
            state = DeviceState()
            bt_experimental = self._is_experimental_ble_endpoint(device)
            bt_read_failed = False
            bt_failed_fields: List[str] = []

            if "dpi-stages" in device.capabilities:
                try:
                    active, stages = backend.get_dpi_stages(device)
                    stages_list = list(stages)
                    state.dpi_active_stage = int(active)
                    state.dpi_stages = stages_list
                    if 1 <= int(active) <= len(stages_list):
                        selected_stage = stages_list[int(active) - 1]
                        state.dpi = (int(selected_stage[0]), int(selected_stage[1]))
                    if (
                        self._has_onboard_profile_bank_switch(device)
                        and self._is_ble_endpoint_device(device)
                        and stages_list
                    ):
                        try:
                            from razecli.ble.bank_signature import (
                                bank_signature_from_parsed_stages,
                                match_bank_snapshot_labels,
                            )

                            handle = device.backend_handle if isinstance(device.backend_handle, dict) else {}
                            raw_ids = handle.get("ble_stage_ids")
                            ids_list: List[int] = []
                            if isinstance(raw_ids, list):
                                ids_list = [int(x) for x in raw_ids][: len(stages_list)]
                            if len(ids_list) < len(stages_list):
                                ids_list = list(range(1, len(stages_list) + 1))
                            marker = int(handle.get("ble_stage_marker") or 0)
                            sig = bank_signature_from_parsed_stages(
                                int(state.dpi_active_stage or 1),
                                marker,
                                stages_list,
                                ids_list,
                            )
                            state.onboard_bank_signature = sig
                            labels = match_bank_snapshot_labels(sig)
                            if labels:
                                state.onboard_bank_match = ", ".join(labels[:3])
                        except Exception:
                            state.onboard_bank_signature = None
                            state.onboard_bank_match = None
                except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                    if bt_experimental:
                        bt_read_failed = True
                        bt_failed_fields.append("dpi profiles")
                    else:
                        if not self._status_locked():
                            self.status = f"Could not read DPI profiles: {exc}"

            if "dpi" in device.capabilities and state.dpi is None:
                try:
                    state.dpi = backend.get_dpi(device)
                except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                    if bt_experimental:
                        bt_read_failed = True
                        bt_failed_fields.append("dpi")
                    else:
                        if not self._status_locked():
                            self.status = f"Could not read DPI: {exc}"

            if "poll-rate" in device.capabilities:
                previously_unavailable_fields = tuple(
                    getattr(self, "_bt_unavailable_fields", {}).get(device.identifier, tuple())
                )
                skip_poll_read = bt_experimental and ("poll-rate" in previously_unavailable_fields)
                try:
                    if not skip_poll_read:
                        state.poll_rate = backend.get_poll_rate(device)
                    elif cached_state is not None and cached_state.poll_rate is not None:
                        state.poll_rate = int(cached_state.poll_rate)
                    else:
                        bt_read_failed = True
                        bt_failed_fields.append("poll-rate")
                except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                    if bt_experimental:
                        if cached_state is not None and cached_state.poll_rate is not None:
                            state.poll_rate = int(cached_state.poll_rate)
                        # Poll-rate over BT is optional/experimental; avoid noisy status spam
                        # during background refresh when only this field is unavailable.
                    else:
                        if not self._status_locked():
                            self.status = f"Could not read poll-rate: {exc}"

            if "battery" in device.capabilities:
                last_battery = self._battery_refreshed_at.get(device.identifier, 0.0)
                battery_fresh = (now - last_battery) < float(self._battery_refresh_interval_s)
                if (
                    not force
                    and battery_fresh
                    and cached_state is not None
                    and cached_state.battery is not None
                ):
                    state.battery = int(cached_state.battery)
                else:
                    try:
                        state.battery = backend.get_battery(device)
                        self._battery_refreshed_at[device.identifier] = now
                    except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                        if cached_state is not None and cached_state.battery is not None:
                            state.battery = int(cached_state.battery)
                        if bt_experimental:
                            bt_read_failed = True
                            bt_failed_fields.append("battery")
                        else:
                            if not self._status_locked():
                                self.status = f"Could not read battery: {exc}"

            self.state = state
            self._state_cache[device.identifier] = self._clone_state(state)
            self._state_refreshed_at[device.identifier] = now
            current_failed = tuple(sorted(set(bt_failed_fields))) if bt_read_failed else tuple()
            previous_failed = tuple()
            bt_map = getattr(self, "_bt_unavailable_fields", None)
            if isinstance(bt_map, dict):
                previous_failed = tuple(bt_map.get(device.identifier, tuple()))
                if current_failed:
                    bt_map[device.identifier] = current_failed
                else:
                    bt_map.pop(device.identifier, None)
            if bt_read_failed and current_failed != previous_failed and not self._status_locked():
                failed = ", ".join(current_failed)
                pid_label = self._usb_pid_label(device)
                if failed:
                    self.status = (
                        f"Bluetooth mode ({pid_label}) is experimental; "
                        f"could not read: {failed}"
                    )
                else:
                    self.status = f"Bluetooth mode ({pid_label}) is experimental; some values may be unavailable"
        finally:
            if self._state_loading_device_id == device.identifier:
                self._state_loading_device_id = None

    def _persist_autosync(self, device: DetectedDevice, backend) -> None:
        if not autosync_enabled():
            return
        if "dpi-stages" not in device.capabilities:
            return
        try:
            active_stage, stages = backend.get_dpi_stages(device)
        except Exception:
            return
        _ = save_autosync_settings(
            model_id=device.model_id,
            active_stage=int(active_stage),
            stages=stages,
        )

    def _maybe_apply_autosync(self, device: DetectedDevice, backend) -> None:
        if not autosync_enabled():
            return
        if device.identifier in self._autosync_attempted:
            return
        self._autosync_attempted.add(device.identifier)

        if "dpi-stages" not in device.capabilities:
            return

        payload = load_autosync_settings(device.model_id)
        if payload is None:
            return

        stages = list(payload.get("stages", []))
        if not stages:
            return

        active_stage = int(payload.get("active_stage", 1))
        if active_stage < 1 or active_stage > len(stages):
            active_stage = 1

        try:
            current_active, current_stages = backend.get_dpi_stages(device)
            if int(current_active) == int(active_stage) and list(current_stages) == stages:
                return
        except Exception:
            pass

        try:
            backend.set_dpi_stages(device, active_stage, stages)
            self.status = f"Applied autosync DPI profiles for {device.model_id}"
        except Exception:
            if self._is_ble_endpoint_device(device):
                self.status = (
                    "Autosync exists but Bluetooth endpoint "
                    f"({self._usb_pid_label(device)}) could not be updated on this host"
                )

    def _set_dpi(self, dpi_x: int, dpi_y: int) -> None:
        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return

        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); DPI write is unavailable"
            return

        if "dpi" not in device.capabilities:
            self.status = "Selected device does not support DPI"
            return

        model = self.service.registry.get(device.model_id) if device.model_id else None
        if model and model.dpi_min is not None and (dpi_x < model.dpi_min or dpi_y < model.dpi_min):
            self.status = f"DPI must be at least {model.dpi_min}"
            return

        if model and model.dpi_max is not None and (dpi_x > model.dpi_max or dpi_y > model.dpi_max):
            self.status = f"DPI must be <= {model.dpi_max}"
            return

        if dpi_x <= 0 or dpi_y < 0:
            self.status = "Invalid DPI value"
            return

        def _work() -> tuple[int, int]:
            backend = self.service.resolve_backend(device)
            backend.set_dpi(device, dpi_x, dpi_y)
            mirror_ok, mirror_failed = mirror_to_transport_targets(
                self.service,
                device,
                lambda target: self.service.resolve_backend(target).set_dpi(target, dpi_x, dpi_y),
                required_capability="dpi",
            )
            self._persist_autosync(device, backend)
            return int(mirror_ok), int(mirror_failed)

        def _on_success(result: tuple[int, int]) -> None:
            mirror_ok, mirror_failed = result
            if mirror_ok or mirror_failed:
                self.status = (
                    f"DPI set to {dpi_x}:{dpi_y} | mirrored={mirror_ok} failed={mirror_failed}"
                )
            else:
                self.status = f"DPI set to {dpi_x}:{dpi_y}"
            self._schedule_state_refresh(force=True)

        def _on_error(exc: Exception) -> None:
            self._set_status(f"Could not set DPI: {exc}", hold_seconds=8.0)

        if hasattr(self, "_start_background_job"):
            started = self._start_background_job(
                label=f"Applying DPI {dpi_x}:{dpi_y}",
                work=_work,
                on_success=_on_success,
                on_error=_on_error,
            )
            if started:
                return

        try:
            result = _work()
            _on_success(result)
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            _on_error(exc)

    def _adjust_dpi(self, delta: int) -> None:
        device = self._selected()
        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); DPI read/write is unavailable"
            return

        if self.state.dpi is None:
            self.status = "DPI cannot be read for selected device"
            return

        current_x, current_y = self.state.dpi
        self._set_dpi(current_x + delta, current_y + delta)

    def _poll_rate_candidates(self, device: DetectedDevice) -> Sequence[int]:
        backend = self.service.resolve_backend(device)

        try:
            rates = [int(value) for value in backend.get_supported_poll_rates(device)]
            if rates:
                return sorted(set(rates))
        except CapabilityUnsupportedError:
            pass

        model = self.service.registry.get(device.model_id) if device.model_id else None
        if model and model.supported_poll_rates:
            return sorted(set(int(value) for value in model.supported_poll_rates))

        return [125, 500, 1000]

    def _cycle_poll_rate(self) -> None:
        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return

        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); poll-rate control is unavailable"
            return

        if "poll-rate" not in device.capabilities:
            self.status = "Selected device does not support poll-rate"
            return

        backend = self.service.resolve_backend(device)

        current = self.state.poll_rate
        if current is None:
            try:
                current = backend.get_poll_rate(device)
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                self.status = f"Could not read poll-rate: {exc}"
                return

        rates = list(self._poll_rate_candidates(device))
        if not rates:
            self.status = "No poll-rate values available"
            return

        if current not in rates:
            target = rates[0]
        else:
            idx = rates.index(current)
            target = rates[(idx + 1) % len(rates)]

        def _work() -> tuple[int, int]:
            backend.set_poll_rate(device, target)
            mirror_ok, mirror_failed = mirror_to_transport_targets(
                self.service,
                device,
                lambda peer: self.service.resolve_backend(peer).set_poll_rate(peer, target),
                required_capability="poll-rate",
            )
            return int(mirror_ok), int(mirror_failed)

        def _on_success(result: tuple[int, int]) -> None:
            mirror_ok, mirror_failed = result
            if mirror_ok or mirror_failed:
                self.status = (
                    f"Poll-rate set to {target} Hz | mirrored={mirror_ok} failed={mirror_failed}"
                )
            else:
                self.status = f"Poll-rate set to {target} Hz"
            self._schedule_state_refresh(force=True)

        def _on_error(exc: Exception) -> None:
            self._set_status(f"Could not set poll-rate: {exc}", hold_seconds=8.0)

        if hasattr(self, "_start_background_job"):
            started = self._start_background_job(
                label=f"Applying poll-rate {target} Hz",
                work=_work,
                on_success=_on_success,
                on_error=_on_error,
            )
            if started:
                return

        try:
            result = _work()
            _on_success(result)
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            _on_error(exc)

    def _cycle_dpi_stage(self) -> None:
        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return

        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); DPI profile control is unavailable"
            return

        if "dpi-stages" not in device.capabilities:
            self.status = "Selected device does not support DPI profiles"
            return

        backend = self.service.resolve_backend(device)
        active_stage = self.state.dpi_active_stage
        stages = self.state.dpi_stages
        if active_stage is None or not stages:
            try:
                active_stage, stages = backend.get_dpi_stages(device)
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                self.status = f"Could not read DPI profiles: {exc}"
                return

        stages_list = list(stages)
        if not stages_list:
            self.status = "No DPI profiles found on device"
            return
        if len(stages_list) == 1:
            if self._ble_multi_profile_table_limited(device):
                message = (
                    "Only one DPI profile is available over current transport. "
                    "Multi-profile editing is limited on this model over BLE; "
                    "use USB/2.4 for full profile tables."
                )
            else:
                message = "Only one DPI profile is available over current transport."
            self._set_status(message, hold_seconds=8.0)
            return

        if active_stage < 1 or active_stage > len(stages_list):
            target = 1
        else:
            target = (active_stage % len(stages_list)) + 1

        target_dpi = stages_list[target - 1]

        # Safe default: switch effective DPI only.
        # Full stage-list rewrites can reset stage values on some transports/firmware.
        if not self._unsafe_stage_activate_enabled():
            self._set_status(
                f"Switching to DPI stage {target}/{len(stages_list)}...",
                hold_seconds=2.0,
            )
            self._set_dpi(int(target_dpi[0]), int(target_dpi[1]))
            return

        def _work() -> None:
            backend.set_dpi_stages(device, target, stages_list)
            self._persist_autosync(device, backend)

        def _on_success(_: None) -> None:
            self.status = (
                f"DPI profile {target}/{len(stages_list)} active "
                f"({target_dpi[0]}:{target_dpi[1]})"
            )
            self._schedule_state_refresh(force=True)

        def _on_error(exc: Exception) -> None:
            self._set_status(f"Could not switch DPI stage: {exc}", hold_seconds=8.0)

        if hasattr(self, "_start_background_job"):
            started = self._start_background_job(
                label=f"Switching profile {target}/{len(stages_list)}",
                work=_work,
                on_success=_on_success,
                on_error=_on_error,
            )
            if started:
                return

        try:
            _work()
            _on_success(None)
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            _on_error(exc)

    def _default_stage_dpi(
        self,
        active_stage: int,
        stages: Sequence[Tuple[int, int]],
    ) -> Tuple[int, int]:
        if stages:
            if 1 <= int(active_stage) <= len(stages):
                return stages[int(active_stage) - 1]
            return stages[-1]
        if self.state.dpi is not None:
            return self.state.dpi
        return (1000, 1000)

    def _set_dpi_profile_count(self, stdscr) -> None:
        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return

        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); DPI profile control is unavailable"
            return

        if "dpi-stages" not in device.capabilities:
            self.status = "Selected device does not support DPI profiles"
            return

        backend = self.service.resolve_backend(device)
        cached_active = self.state.dpi_active_stage or 1
        cached_current = list(self.state.dpi_stages or [])
        prompt_default = len(cached_current) if cached_current else 1

        self._set_status(
            f"Profile editor | current profiles: {prompt_default} | active: {cached_active}. "
            f"Set target count (1-{MAX_DPI_STAGES})."
        )
        target_count = self._prompt_int(
            stdscr,
            f"Target DPI profile count (1-{MAX_DPI_STAGES})",
            prompt_default,
            help_text="Type a number and press Enter. Press Enter directly to keep current value.",
        )
        if target_count is None:
            return
        if target_count < 1 or target_count > MAX_DPI_STAGES:
            self._show_action_dialog(
                stdscr,
                title="Invalid value",
                message=f"DPI profile count must be between 1 and {MAX_DPI_STAGES}.",
            )
            return

        current = cached_current
        active_stage = int(cached_active)
        if not current:
            self._set_status("Loading current DPI profiles...", hold_seconds=3.0)
            try:
                active_stage, stages = backend.get_dpi_stages(device)
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                self._set_status(f"Could not read DPI profiles: {exc}", hold_seconds=8.0)
                return
            current = list(stages)
            if not current:
                self._set_status("No DPI profiles found on device", hold_seconds=6.0)
                return

        if (
            self._is_ble_endpoint_device(device)
            and device.backend == "macos-ble"
            and self._ble_multi_profile_table_limited(device)
            and len(current) <= 1
            and target_count > len(current)
        ):
            message = (
                "This BLE endpoint currently reports a single DPI profile. "
                "Adding more profiles is not mapped reliably yet. "
                "RazeCLI will try anyway, but USB/2.4 is still recommended."
            )
            self._set_status(message, hold_seconds=8.0)
            self._show_action_dialog(
                stdscr,
                title="BLE caution",
                message=message,
            )

        if target_count == len(current):
            message = f"DPI profile count unchanged ({target_count})."
            self._set_status(message, hold_seconds=6.0)
            self._show_action_dialog(
                stdscr,
                title="No changes",
                message=message,
            )
            return

        new_active = int(active_stage)
        if new_active < 1 or new_active > len(current):
            new_active = 1

        if target_count > len(current):
            default_x, default_y = self._default_stage_dpi(new_active, current)
            add_x = self._prompt_int(
                stdscr,
                "DPI X for added profiles",
                default_x,
                help_text="This value is used for each newly added profile.",
            )
            if add_x is None:
                return
            add_y = self._prompt_int(
                stdscr,
                "DPI Y for added profiles",
                default_y,
                help_text="Use same value as X for symmetric DPI.",
            )
            if add_y is None:
                return

            model = self.service.registry.get(device.model_id) if device.model_id else None
            if model and model.dpi_min is not None and (add_x < model.dpi_min or add_y < model.dpi_min):
                self.status = f"DPI must be at least {model.dpi_min}"
                return
            if model and model.dpi_max is not None and (add_x > model.dpi_max or add_y > model.dpi_max):
                self.status = f"DPI must be <= {model.dpi_max}"
                return
            if add_x <= 0 or add_y <= 0:
                self.status = "Invalid DPI value"
                return

            while len(current) < target_count:
                current.append((int(add_x), int(add_y)))
        else:
            current = current[:target_count]
            if new_active > target_count:
                new_active = target_count

        def _work() -> tuple[int, int]:
            backend.set_dpi_stages(device, new_active, current)
            mirror_ok, mirror_failed = mirror_to_transport_targets(
                self.service,
                device,
                lambda peer: self.service.resolve_backend(peer).set_dpi_stages(peer, new_active, current),
                required_capability="dpi-stages",
            )
            self._persist_autosync(device, backend)
            return int(mirror_ok), int(mirror_failed)

        def _on_success(result: tuple[int, int]) -> None:
            mirror_ok, mirror_failed = result
            if mirror_ok or mirror_failed:
                self.status = (
                    f"DPI profiles updated: {len(current)} total (active {new_active})"
                    f" | mirrored={mirror_ok} failed={mirror_failed}"
                )
            else:
                self.status = f"DPI profiles updated: {len(current)} total (active {new_active})"
            self._schedule_state_refresh(force=True)

        def _on_error(exc: Exception) -> None:
            message = f"Could not set DPI profiles: {exc}"
            self._set_status(message, hold_seconds=8.0)
            self._show_action_dialog(
                stdscr,
                title="Write failed",
                message=message,
            )

        if hasattr(self, "_start_background_job"):
            started = self._start_background_job(
                label=f"Applying {len(current)} DPI profiles",
                work=_work,
                on_success=_on_success,
                on_error=_on_error,
            )
            if started:
                return

        try:
            result = _work()
            _on_success(result)
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            _on_error(exc)

    def _load_current_dpi_profiles(
        self,
        stdscr,
        *,
        device: DetectedDevice,
        backend,
    ) -> Optional[Tuple[int, List[Tuple[int, int]]]]:
        # Prefer a fresh device read, but reuse the last main-panel refresh when it is recent
        # so opening `n` does not block on another full BLE scan (can be many seconds per read).
        device_id = device.identifier
        now = time.monotonic()
        cache_map = getattr(self, "_state_cache", None)
        refreshed_map = getattr(self, "_state_refreshed_at", None)
        refresh_iv = float(getattr(self, "_refresh_interval_s", 1.5))
        max_age = max(8.0, refresh_iv * 4)
        if isinstance(cache_map, dict) and isinstance(refreshed_map, dict):
            last = float(refreshed_map.get(device_id, 0.0))
            cached_state = cache_map.get(device_id)
            if (
                cached_state is not None
                and (now - last) <= max_age
                and cached_state.dpi_stages
            ):
                active = int(cached_state.dpi_active_stage or 1)
                stages = [(int(x), int(y)) for (x, y) in list(cached_state.dpi_stages)]
                if stages and active >= 1 and active <= len(stages):
                    return active, stages
                if stages:
                    return 1, stages

        self._set_status("Loading current DPI profiles...", hold_seconds=3.0)
        try:
            active_stage, stages = backend.get_dpi_stages(device)
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self._set_status(f"Could not read DPI profiles: {exc}", hold_seconds=8.0)
            return None
        current = [(int(x), int(y)) for (x, y) in list(stages)]
        if not current:
            self._set_status("No DPI profiles found on device", hold_seconds=6.0)
            return None
        active = int(active_stage)
        if active < 1 or active > len(current):
            active = 1
        return int(active), current

    def _validate_stage_values(self, device: DetectedDevice, stages: Sequence[Tuple[int, int]]) -> Optional[str]:
        model = self.service.registry.get(device.model_id) if device.model_id else None
        for dpi_x, dpi_y in stages:
            x = int(dpi_x)
            y = int(dpi_y)
            if x <= 0 or y <= 0:
                return "Invalid DPI value"
            if model and model.dpi_min is not None and (x < model.dpi_min or y < model.dpi_min):
                return f"DPI must be at least {model.dpi_min}"
            if model and model.dpi_max is not None and (x > model.dpi_max or y > model.dpi_max):
                return f"DPI must be <= {model.dpi_max}"
        return None

    def _apply_dpi_stage_layout(
        self,
        stdscr,
        *,
        device: DetectedDevice,
        backend,
        active_stage: int,
        stages: Sequence[Tuple[int, int]],
        description: str,
    ) -> bool:
        stages_list = [(int(x), int(y)) for (x, y) in stages]
        if not stages_list:
            self._set_status("At least one DPI profile is required", hold_seconds=6.0)
            return False
        if len(stages_list) > MAX_DPI_STAGES:
            self._set_status(f"At most {MAX_DPI_STAGES} DPI profiles are supported", hold_seconds=6.0)
            return False

        validation_error = self._validate_stage_values(device, stages_list)
        if validation_error:
            self._set_status(validation_error, hold_seconds=6.0)
            return False

        active = int(active_stage)
        if active < 1 or active > len(stages_list):
            active = 1

        def _work() -> tuple[int, int]:
            backend.set_dpi_stages(device, active, stages_list)
            mirror_ok, mirror_failed = mirror_to_transport_targets(
                self.service,
                device,
                lambda peer: self.service.resolve_backend(peer).set_dpi_stages(peer, active, stages_list),
                required_capability="dpi-stages",
            )
            self._persist_autosync(device, backend)
            return int(mirror_ok), int(mirror_failed)

        try:
            mirror_ok, mirror_failed = self._run_with_modal(
                stdscr,
                title="DPI Levels",
                description=str(description),
                work=_work,
                footer="Writing DPI values",
            )
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            message = f"Could not set DPI profiles: {exc}"
            self._set_status(message, hold_seconds=8.0)
            self._show_action_dialog(
                stdscr,
                title="Write failed",
                message=message,
            )
            return False

        if mirror_ok or mirror_failed:
            self.status = (
                f"DPI profiles updated: {len(stages_list)} total (active {active})"
                f" | mirrored={mirror_ok} failed={mirror_failed}"
            )
        else:
            self.status = f"DPI profiles updated: {len(stages_list)} total (active {active})"
        self._schedule_state_refresh(force=True)
        return True

    def _set_dpi_profile_ladder(self, stdscr) -> None:
        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return
        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); DPI profile control is unavailable"
            return
        if "dpi-stages" not in device.capabilities:
            self.status = "Selected device does not support DPI profiles"
            return

        backend = self.service.resolve_backend(device)
        # Same pattern as `_set_dpi_profile_count`: go straight to prompts using main-panel
        # state; defer BLE `get_dpi_stages` until we need it for the BLE caution dialog only.
        cached_active = int(self.state.dpi_active_stage or 1)
        cached_current = list(self.state.dpi_stages or [])

        default_count = len(cached_current) if cached_current else 1
        target_count = self._prompt_int(
            stdscr,
            f"Target DPI profile count (1-{MAX_DPI_STAGES})",
            default_count,
            help_text="How many DPI levels the DPI button should cycle through.",
        )
        if target_count is None:
            return
        if target_count < 1 or target_count > MAX_DPI_STAGES:
            self._show_action_dialog(
                stdscr,
                title="Invalid value",
                message=f"DPI profile count must be between 1 and {MAX_DPI_STAGES}.",
            )
            return

        default_x, default_y = self._default_stage_dpi(cached_active, cached_current)
        base_x = self._prompt_int(
            stdscr,
            "Base DPI X (stage 1)",
            int(default_x),
            help_text="First stage value. Next stages are calculated from this.",
        )
        if base_x is None:
            return
        base_y = self._prompt_int(
            stdscr,
            "Base DPI Y (stage 1)",
            int(default_y),
            help_text="Use same as X for symmetric DPI.",
        )
        if base_y is None:
            return

        default_increment = 400
        if len(cached_current) >= 2:
            try:
                inferred_x = int(cached_current[1][0]) - int(cached_current[0][0])
                inferred_y = int(cached_current[1][1]) - int(cached_current[0][1])
                if inferred_x == inferred_y and inferred_x != 0:
                    default_increment = int(inferred_x)
            except Exception:
                default_increment = 400

        increment = self._prompt_int(
            stdscr,
            "DPI increment per stage (+/-)",
            int(default_increment),
            help_text="Example: 400 => 800/1200/1600...  |  -400 builds descending stages.",
        )
        if increment is None:
            return
        if int(increment) == 0:
            self._show_action_dialog(
                stdscr,
                title="Invalid value",
                message="Increment cannot be 0.",
            )
            return

        default_active = int(cached_active)
        if default_active < 1 or default_active > int(target_count):
            default_active = 1
        new_active = self._prompt_int(
            stdscr,
            f"Active stage index (1-{int(target_count)})",
            int(default_active),
            help_text="Which stage should be active right after apply.",
        )
        if new_active is None:
            return
        if new_active < 1 or new_active > int(target_count):
            self._show_action_dialog(
                stdscr,
                title="Invalid value",
                message=f"Active stage must be between 1 and {int(target_count)}.",
            )
            return

        new_stages: List[Tuple[int, int]] = []
        for index in range(int(target_count)):
            new_stages.append(
                (
                    int(base_x) + (index * int(increment)),
                    int(base_y) + (index * int(increment)),
                )
            )

        ble_limited = (
            self._is_ble_endpoint_device(device)
            and device.backend == "macos-ble"
            and self._ble_multi_profile_table_limited(device)
        )
        current_for_caution = list(cached_current)
        if ble_limited and (
            not current_for_caution
            or (len(current_for_caution) <= 1 and int(target_count) > len(current_for_caution))
        ):
            self._set_status("Loading current DPI profiles...", hold_seconds=3.0)
            try:
                _a, stages = backend.get_dpi_stages(device)
                current_for_caution = [(int(x), int(y)) for (x, y) in list(stages)]
            except Exception:
                pass

        if (
            ble_limited
            and current_for_caution
            and len(current_for_caution) <= 1
            and int(target_count) > len(current_for_caution)
        ):
            self._show_action_dialog(
                stdscr,
                title="BLE caution",
                message=(
                    "This BLE endpoint currently reports a single DPI profile. "
                    "RazeCLI will try writing the full table anyway, but USB/2.4 is still safer."
                ),
            )

        self._apply_dpi_stage_layout(
            stdscr,
            device=device,
            backend=backend,
            active_stage=int(new_active),
            stages=new_stages,
            description=f"Applying {int(target_count)} staged DPI levels",
        )

    def _set_dpi_adjust_step(self, stdscr) -> None:
        current = max(1, int(getattr(self, "_dpi_adjust_step", 100)))
        new_step = self._prompt_int(
            stdscr,
            "DPI +/- adjustment step",
            current,
            help_text=(
                "Keyboard +/- step in the main view. "
                "This does not change the mouse's physical DPI-button stage table."
            ),
        )
        if new_step is None:
            return
        if int(new_step) < 1 or int(new_step) > 5000:
            self._show_action_dialog(
                stdscr,
                title="Invalid value",
                message="DPI +/- step must be between 1 and 5000.",
            )
            return
        self._dpi_adjust_step = int(new_step)
        self._set_status(f"DPI +/- step set to {int(self._dpi_adjust_step)}", hold_seconds=6.0)

    def _list_tui_dpi_presets(self) -> List[Dict[str, Any]]:
        rows = list_dpi_stage_presets()
        filtered: List[Dict[str, Any]] = []
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name or name.startswith(AUTOSYNC_PRESET_PREFIX):
                continue
            filtered.append(dict(row))
        return filtered

    def _save_current_dpi_preset(self, stdscr) -> None:
        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return
        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); DPI profile control is unavailable"
            return
        if "dpi-stages" not in device.capabilities:
            self.status = "Selected device does not support DPI profiles"
            return

        backend = self.service.resolve_backend(device)
        cached_active = int(self.state.dpi_active_stage or 1)
        cached_stages = list(self.state.dpi_stages or [])
        model_slug = str(device.model_id or "dpi").strip() or "dpi"
        default_name = (
            f"{model_slug}-{len(cached_stages)}lvl"
            if cached_stages
            else f"{model_slug}-dpi"
        )
        preset_name = self._prompt_text(
            stdscr,
            "Preset name",
            default_name,
            help_text="Save current DPI stage table under this name.",
            max_len=64,
        )
        if preset_name is None:
            return
        normalized_name = str(preset_name).strip()
        if not normalized_name:
            self._show_action_dialog(
                stdscr,
                title="Invalid preset name",
                message="Preset name cannot be empty.",
            )
            return

        stages = [(int(x), int(y)) for (x, y) in cached_stages]
        active_stage = int(cached_active)
        if not stages:
            loaded = self._load_current_dpi_profiles(stdscr, device=device, backend=backend)
            if loaded is None:
                return
            active_stage, stages = loaded[0], [(int(x), int(y)) for (x, y) in loaded[1]]
        else:
            if active_stage < 1 or active_stage > len(stages):
                active_stage = 1

        try:
            path = save_dpi_stage_preset(
                name=normalized_name,
                model_id=device.model_id,
                active_stage=int(active_stage),
                stages=stages,
            )
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self._set_status(f"Could not save DPI preset: {exc}", hold_seconds=8.0)
            return
        self.status = f"Saved DPI preset '{normalized_name}' to {path}"

    def _load_dpi_preset(self, stdscr) -> None:
        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return
        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); DPI profile control is unavailable"
            return
        if "dpi-stages" not in device.capabilities:
            self.status = "Selected device does not support DPI profiles"
            return

        presets = self._list_tui_dpi_presets()
        if not presets:
            self._set_status("No DPI presets available", hold_seconds=6.0)
            return

        options = []
        for entry in presets:
            options.append(
                f"{entry['name']}  (model={entry.get('model_id') or '-'} | "
                f"stages={int(entry.get('stages_count') or 0)} | active={int(entry.get('active_stage') or 1)})"
            )
        selected_index = self._select_menu(
            stdscr,
            title="DPI Presets",
            description="Select preset to load",
            options=options,
            default_index=0,
            footer="Up/Down select | Enter load | Esc cancel",
        )
        if selected_index is None:
            return
        selected = presets[selected_index]
        preset_name = str(selected["name"])
        try:
            preset = load_dpi_stage_preset(preset_name)
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self._set_status(f"Could not load preset: {exc}", hold_seconds=8.0)
            return

        preset_model = str(preset.get("model_id") or "").strip()
        current_model = str(device.model_id or "").strip()
        if preset_model and current_model and preset_model != current_model:
            confirm = self._select_menu(
                stdscr,
                title="Model mismatch",
                description=(
                    f"Preset model '{preset_model}' differs from selected device '{current_model}'. "
                    "Load anyway?"
                ),
                options=["No", "Yes, load anyway"],
                default_index=0,
                footer="Enter confirm | Esc cancel",
            )
            if confirm != 1:
                return

        stages = [(int(x), int(y)) for (x, y) in list(preset.get("stages") or [])]
        active_stage = int(preset.get("active_stage") or 1)
        backend = self.service.resolve_backend(device)
        self._apply_dpi_stage_layout(
            stdscr,
            device=device,
            backend=backend,
            active_stage=active_stage,
            stages=stages,
            description=f"Loading preset '{preset_name}'",
        )

    def _delete_dpi_preset(self, stdscr) -> None:
        presets = self._list_tui_dpi_presets()
        if not presets:
            self._set_status("No DPI presets available", hold_seconds=6.0)
            return

        options = []
        for entry in presets:
            options.append(
                f"{entry['name']}  (model={entry.get('model_id') or '-'} | stages={int(entry.get('stages_count') or 0)})"
            )
        selected_index = self._select_menu(
            stdscr,
            title="Delete DPI Preset",
            description="Select preset to delete",
            options=options,
            default_index=0,
            footer="Up/Down select | Enter delete | Esc cancel",
        )
        if selected_index is None:
            return

        preset_name = str(presets[selected_index]["name"])
        confirm = self._select_menu(
            stdscr,
            title="Confirm delete",
            description=f"Delete preset '{preset_name}'?",
            options=["Cancel", "Delete"],
            default_index=0,
            footer="Enter confirm | Esc cancel",
        )
        if confirm != 1:
            return
        try:
            delete_dpi_stage_preset(preset_name)
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self._set_status(f"Could not delete preset: {exc}", hold_seconds=8.0)
            return
        self.status = f"Deleted DPI preset '{preset_name}'"

    def _edit_dpi_levels(self, stdscr) -> None:
        """TUI `n` binding: DPI levels editor menu."""
        if not hasattr(stdscr, "getch"):
            # Test/legacy fallback.
            self._set_dpi_profile_count(stdscr)
            return

        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return
        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); DPI profile control is unavailable"
            return
        if "dpi-stages" not in device.capabilities:
            self.status = "Selected device does not support DPI profiles"
            return

        while True:
            choice = self._select_menu(
                stdscr,
                title="DPI Levels",
                description=(
                    "Choose editor for the current onboard profile/bank "
                    "(switch bank first with the underside profile button)."
                ),
                options=[
                    "Set profile count (current editor)",
                    "Build mouse DPI-button ladder (count + increment)",
                    "Set keyboard +/- adjustment step",
                    "Load DPI preset",
                    "Save current as preset",
                    "Delete DPI preset",
                ],
                default_index=1,
                footer="Up/Down select | Enter confirm | Esc back",
            )
            if choice is None:
                return
            if choice == 0:
                self._set_dpi_profile_count(stdscr)
                continue
            if choice == 1:
                self._set_dpi_profile_ladder(stdscr)
                continue
            if choice == 2:
                self._set_dpi_adjust_step(stdscr)
                continue
            if choice == 3:
                self._load_dpi_preset(stdscr)
                continue
            if choice == 4:
                self._save_current_dpi_preset(stdscr)
                continue
            if choice == 5:
                self._delete_dpi_preset(stdscr)
                continue

    def _prompt_int(
        self,
        stdscr,
        label: str,
        default: Optional[int] = None,
        *,
        help_text: Optional[str] = None,
    ) -> Optional[int]:
        prompt = f"{label}"
        if default is not None:
            prompt += f" [{default}]"
        prompt += ": "

        helper = help_text or "Enter a number. Press Enter on empty input to keep the default."
        y, x, box_h, box_w, _text_w = self._draw_modal_box(
            stdscr,
            title="Input",
            body_lines=[helper],
            footer="Press Enter to submit | Esc to cancel",
        )
        input_y = y + box_h - 3
        input_x = x + 2
        self._safe_add(stdscr, input_y, input_x, prompt, curses.A_BOLD)

        field_x = min(input_x + len(prompt), x + box_w - 4)
        field_width = max(1, (x + box_w - 3) - field_x + 1)
        max_len = min(24, field_width)
        value = ""
        cancelled = False

        stdscr.timeout(-1)
        curses.curs_set(1)
        try:
            while True:
                visible = value[-field_width:]
                self._safe_add(stdscr, input_y, field_x, " " * field_width)
                self._safe_add(stdscr, input_y, field_x, visible)
                try:
                    stdscr.move(input_y, field_x + len(visible))
                except curses.error:
                    pass
                stdscr.refresh()

                key = stdscr.get_wch()
                if isinstance(key, str):
                    if key in ("\n", "\r"):
                        break
                    if key == "\x1b":
                        cancelled = True
                        break
                    if key in ("\b", "\x7f"):
                        value = value[:-1]
                        continue
                    if key.isprintable() and len(value) < max_len:
                        value += key
                    continue

                if key in (curses.KEY_ENTER, 10, 13):
                    break
                if key == 27:
                    cancelled = True
                    break
                if key in (curses.KEY_BACKSPACE, 127, 8):
                    value = value[:-1]
        finally:
            curses.curs_set(0)
            stdscr.timeout(60)

        if cancelled:
            return None

        text = value.strip()
        if not text and default is not None:
            return default
        if not text:
            return None

        try:
            return int(text)
        except ValueError:
            self.status = "Invalid number"
            return None

    def _prompt_text(
        self,
        stdscr,
        label: str,
        default: Optional[str] = None,
        *,
        help_text: Optional[str] = None,
        max_len: int = 64,
    ) -> Optional[str]:
        prompt = f"{label}"
        if default is not None and str(default).strip():
            prompt += f" [{default}]"
        prompt += ": "

        helper = help_text or "Type a value and press Enter. Press Enter on empty input to keep default."
        body_lines = str(helper).splitlines() or [""]
        y, x, box_h, box_w, _text_w = self._draw_modal_box(
            stdscr,
            title="Input",
            body_lines=body_lines,
            footer="Press Enter to submit | Esc to cancel",
        )
        input_y = y + box_h - 3
        input_x = x + 2
        self._safe_add(stdscr, input_y, input_x, prompt, curses.A_BOLD)

        field_x = min(input_x + len(prompt), x + box_w - 4)
        field_width = max(1, (x + box_w - 3) - field_x + 1)
        limit = min(max(8, int(max_len)), max(8, field_width * 2))
        value = ""
        cancelled = False

        stdscr.timeout(-1)
        curses.curs_set(1)
        try:
            while True:
                visible = value[-field_width:]
                self._safe_add(stdscr, input_y, field_x, " " * field_width)
                self._safe_add(stdscr, input_y, field_x, visible)
                try:
                    stdscr.move(input_y, field_x + len(visible))
                except curses.error:
                    pass
                stdscr.refresh()

                key = stdscr.get_wch()
                if isinstance(key, str):
                    if key in ("\n", "\r"):
                        break
                    if key == "\x1b":
                        cancelled = True
                        break
                    if key in ("\b", "\x7f"):
                        value = value[:-1]
                        continue
                    if key.isprintable() and len(value) < limit:
                        value += key
                    continue

                if key in (curses.KEY_ENTER, 10, 13):
                    break
                if key == 27:
                    cancelled = True
                    break
                if key in (curses.KEY_BACKSPACE, 127, 8):
                    value = value[:-1]
        finally:
            curses.curs_set(0)
            stdscr.timeout(60)

        if cancelled:
            return None

        text = value.strip()
        if not text and default is not None:
            return str(default).strip()
        if not text:
            return None
        return text

    def _prompt_choice(
        self,
        stdscr,
        *,
        title: str,
        label: str,
        options: Sequence[str],
        default_index: int = 0,
        footer: str = "Type number + Enter | Esc cancels",
    ) -> Optional[int]:
        values = [str(option).strip() for option in options if str(option).strip()]
        if not values:
            return None
        default_index = max(0, min(len(values) - 1, int(default_index)))

        body_lines = [str(label).strip(), ""]
        for idx, value in enumerate(values, start=1):
            marker = " (default)" if (idx - 1) == default_index else ""
            body_lines.append(f"{idx}. {value}{marker}")

        limit = max(2, len(str(len(values))))
        selected = self._prompt_text(
            stdscr,
            "Select option",
            str(default_index + 1),
            help_text="\n".join(body_lines),
            max_len=limit,
        )
        if selected is None:
            return None
        try:
            index = int(str(selected).strip()) - 1
        except ValueError:
            self._show_action_dialog(
                stdscr,
                title="Invalid choice",
                message="Please enter a valid option number.",
            )
            return None
        if index < 0 or index >= len(values):
            self._show_action_dialog(
                stdscr,
                title="Invalid choice",
                message=f"Please select a number between 1 and {len(values)}.",
            )
            return None
        return int(index)

    def _load_with_modal(
        self,
        stdscr,
        *,
        title: str,
        description: str,
        loader: Callable[[], Any],
        footer: str = "Loading...",
    ) -> Optional[Any]:
        done = threading.Event()
        payload: Dict[str, Any] = {"value": None, "error": None}

        def _run_loader() -> None:
            try:
                payload["value"] = loader()
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                payload["error"] = exc
            finally:
                done.set()

        threading.Thread(
            target=_run_loader,
            daemon=True,
            name=f"razecli-modal-load-{title.lower().replace(' ', '-')}",
        ).start()

        stdscr.timeout(80)
        paint_frame = 0
        try:
            while not done.is_set():
                paint_frame += 1
                self._draw_modal_box(
                    stdscr,
                    title=title,
                    body_lines=[
                        f"{description}{self._loading_dots()}",
                        "",
                        f"{self._spinner()} Please wait",
                    ],
                    footer=f"{footer} | Esc cancels",
                    dim_full_screen=(paint_frame == 1),
                )
                key = stdscr.getch()
                if key in (27, ord("q")):
                    self._set_status(f"{title} cancelled", hold_seconds=2.0)
                    return None
            error = payload.get("error")
            if isinstance(error, Exception):
                raise error
            return payload.get("value")
        finally:
            stdscr.timeout(60)

    def _run_with_modal(
        self,
        stdscr,
        *,
        title: str,
        description: str,
        work: Callable[[], Any],
        footer: str = "Working...",
    ) -> Any:
        done = threading.Event()
        payload: Dict[str, Any] = {"value": None, "error": None}

        def _run_work() -> None:
            try:
                payload["value"] = work()
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                payload["error"] = exc
            finally:
                done.set()

        threading.Thread(
            target=_run_work,
            daemon=True,
            name=f"razecli-modal-work-{title.lower().replace(' ', '-')}",
        ).start()

        stdscr.timeout(80)
        paint_frame = 0
        try:
            while not done.is_set():
                paint_frame += 1
                self._draw_modal_box(
                    stdscr,
                    title=title,
                    body_lines=[
                        f"{description}{self._loading_dots()}",
                        "",
                        f"{self._spinner()} Please wait",
                    ],
                    footer=footer,
                    dim_full_screen=(paint_frame == 1),
                )
                _ = stdscr.getch()
            error = payload.get("error")
            if isinstance(error, Exception):
                raise error
            return payload.get("value")
        finally:
            stdscr.timeout(60)

    def _read_rgb_state_for_ui(self, device: DetectedDevice) -> Dict[str, Any]:
        backend = self.service.resolve_backend(device)
        state: Dict[str, Any]
        try:
            payload = backend.get_rgb(device)
            if isinstance(payload, dict):
                state = dict(payload)
            else:
                state = get_rgb_scaffold(model_id=device.model_id)
        except Exception:
            state = get_rgb_scaffold(model_id=device.model_id)

        rgb_cache = getattr(self, "_rgb_cache", None)
        if isinstance(rgb_cache, dict):
            rgb_cache[str(device.identifier)] = dict(state)
        return dict(state)

    def _read_button_mapping_for_ui(self, device: DetectedDevice) -> Dict[str, Any]:
        backend = self.service.resolve_backend(device)
        state: Dict[str, Any]
        try:
            payload = backend.get_button_mapping(device)
            if isinstance(payload, dict):
                state = dict(payload)
            else:
                state = get_button_mapping_scaffold(model_id=device.model_id)
        except Exception:
            state = get_button_mapping_scaffold(model_id=device.model_id)

        mapping_cache = getattr(self, "_button_mapping_cache", None)
        if isinstance(mapping_cache, dict):
            mapping_cache[str(device.identifier)] = dict(state)
        return dict(state)

    def _read_button_actions_for_ui(self, device: DetectedDevice, mapping: Dict[str, Any]) -> List[str]:
        backend = self.service.resolve_backend(device)
        try:
            payload = backend.list_button_mapping_actions(device)
            if isinstance(payload, dict):
                actions = payload.get("actions")
                if isinstance(actions, list):
                    cleaned = [str(item).strip() for item in actions if str(item).strip()]
                    if cleaned:
                        return cleaned
        except Exception:
            pass

        fallback = mapping.get("actions_suggested")
        if isinstance(fallback, list):
            cleaned = [str(item).strip() for item in fallback if str(item).strip()]
            if cleaned:
                return cleaned
        return []

    @staticmethod
    def _button_actions_from_mapping_state(mapping_state: Dict[str, Any]) -> List[str]:
        fallback = mapping_state.get("actions_suggested")
        if isinstance(fallback, list):
            cleaned = [str(item).strip() for item in fallback if str(item).strip()]
            if cleaned:
                return cleaned
        return []

    def _cached_rgb_state(self, device: DetectedDevice) -> Optional[Dict[str, Any]]:
        rgb_cache = getattr(self, "_rgb_cache", None)
        if not isinstance(rgb_cache, dict):
            return None
        for key in (str(device.identifier), device.identifier):
            if key in rgb_cache:
                raw = rgb_cache[key]
                if isinstance(raw, dict) and raw:
                    return dict(raw)
        return None

    def _cached_button_mapping_state(self, device: DetectedDevice) -> Optional[Dict[str, Any]]:
        cache = getattr(self, "_button_mapping_cache", None)
        if not isinstance(cache, dict):
            return None
        for key in (str(device.identifier), device.identifier):
            if key in cache:
                raw = cache[key]
                if isinstance(raw, dict) and raw:
                    return dict(raw)
        return None

    def _schedule_rgb_cache_warm(self, device: DetectedDevice) -> None:
        """Fill ``_rgb_cache`` from hardware without blocking the UI (prefetch may already run)."""
        inflight = getattr(self, "_is_feature_prefetch_inflight", None)
        if callable(inflight) and inflight(str(device.identifier), "rgb"):
            return
        if self._cached_rgb_state(device) is not None:
            return
        target = device

        def _run() -> None:
            try:
                self._read_rgb_state_for_ui(target)
            except Exception:
                pass

        threading.Thread(
            target=_run,
            daemon=True,
            name="razecli-warm-rgb",
        ).start()

    def _schedule_button_mapping_cache_warm(self, device: DetectedDevice) -> None:
        inflight = getattr(self, "_is_feature_prefetch_inflight", None)
        if callable(inflight) and inflight(str(device.identifier), "button-mapping"):
            return
        if self._cached_button_mapping_state(device) is not None:
            return
        target = device

        def _run() -> None:
            try:
                self._read_button_mapping_for_ui(target)
            except Exception:
                pass

        threading.Thread(
            target=_run,
            daemon=True,
            name="razecli-warm-buttons",
        ).start()

    def _apply_rgb_state(
        self,
        device: DetectedDevice,
        *,
        mode: str,
        brightness: Optional[int],
        color: Optional[str],
    ) -> Dict[str, Any]:
        backend = self.service.resolve_backend(device)
        _, local_rgb = set_rgb_scaffold(
            model_id=device.model_id,
            mode=mode,
            brightness=brightness,
            color=color,
        )
        result = dict(local_rgb)
        hardware_apply = "fallback-local"
        try:
            hardware_rgb = backend.set_rgb(
                device,
                mode=mode,
                brightness=brightness,
                color=color,
            )
            if isinstance(hardware_rgb, dict):
                for key in ("mode", "brightness", "color", "modes_supported"):
                    if key in hardware_rgb:
                        result[key] = hardware_rgb[key]
            hardware_apply = "applied"
        except CapabilityUnsupportedError:
            hardware_apply = "fallback-local"
        result["hardware_apply"] = hardware_apply
        rgb_cache = getattr(self, "_rgb_cache", None)
        if isinstance(rgb_cache, dict):
            rgb_cache[str(device.identifier)] = dict(result)
        return result

    def _select_menu(
        self,
        stdscr,
        *,
        title: str,
        description: str,
        options: Sequence[str],
        default_index: int = 0,
        footer: str = "Up/Down select | Enter confirm | Esc cancel",
    ) -> Optional[int]:
        values = [str(item).strip() for item in options if str(item).strip()]
        if not values:
            return None
        index = max(0, min(len(values) - 1, int(default_index)))

        stdscr.timeout(-1)
        menu_frame = 0
        while True:
            body_lines = [str(description).strip(), ""]
            for idx, value in enumerate(values):
                marker = ">" if idx == index else " "
                body_lines.append(f"{marker} {value}")
            menu_frame += 1
            self._draw_modal_box(
                stdscr,
                title=title,
                body_lines=body_lines,
                footer=footer,
                dim_full_screen=(menu_frame == 1),
            )
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                index = (index - 1) % len(values)
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                index = (index + 1) % len(values)
                continue
            if key in (10, 13, curses.KEY_ENTER, ord(" ")):
                stdscr.timeout(60)
                return int(index)
            if key in (27, ord("q")):
                stdscr.timeout(60)
                return None

    @staticmethod
    def _capture_button_with_pynput(timeout_s: float = 4.0) -> Optional[str]:
        try:
            from pynput import mouse  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Capture assist requires 'pynput'. Install with: pip install pynput"
            ) from exc

        mapping = {
            "left": "left_click",
            "right": "right_click",
            "middle": "middle_click",
            "x1": "side_1",
            "x2": "side_2",
            "button8": "side_1",
            "button9": "side_2",
        }
        captured: Dict[str, Optional[str]] = {"button": None}
        done = threading.Event()

        def _on_click(_x, _y, button, pressed):  # pragma: no cover - runtime/hardware dependent
            if not pressed:
                return True
            raw = str(getattr(button, "name", button)).strip().lower()
            if raw.startswith("button."):
                raw = raw.split(".", 1)[1]
            captured["button"] = mapping.get(raw)
            done.set()
            return False

        listener = mouse.Listener(on_click=_on_click)
        listener.start()
        done.wait(timeout=max(0.5, float(timeout_s)))
        try:
            listener.stop()
        except Exception:
            pass
        try:
            listener.join(timeout=1.0)
        except Exception:
            pass
        return captured["button"]

    def _capture_button_assist(
        self,
        stdscr,
        *,
        buttons: Sequence[str],
    ) -> Optional[str]:
        self._draw_modal_box(
            stdscr,
            title="Capture Assist",
            body_lines=[
                "Press the target mouse button now.",
                "Waiting up to 4 seconds for a global mouse click event.",
                "",
                "If this fails: grant macOS Accessibility/Input Monitoring permissions,",
                "or use manual button selection.",
            ],
            footer="Capturing...",
        )
        stdscr.refresh()
        try:
            detected = self._capture_button_with_pynput(timeout_s=4.0)
        except Exception as exc:
            self._show_action_dialog(
                stdscr,
                title="Capture unavailable",
                message=f"{exc}",
            )
            return None

        if detected is None:
            self._show_action_dialog(
                stdscr,
                title="No button captured",
                message="No supported mouse button was captured within the timeout.",
            )
            return None
        if str(detected) not in set(str(item) for item in buttons):
            self._show_action_dialog(
                stdscr,
                title="Unsupported capture",
                message=f"Captured '{detected}', but this model does not expose that slot.",
            )
            return None
        return str(detected)

    def _edit_rgb(self, stdscr) -> None:
        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return
        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); RGB write is unavailable"
            return
        if "rgb" not in device.capabilities:
            self.status = "Selected device does not support RGB"
            return

        try:
            presets_raw = get_rgb_presets(model_id=device.model_id)
        except Exception:
            presets_raw = {}
        presets = dict(presets_raw) if isinstance(presets_raw, dict) else get_rgb_presets(model_id=device.model_id)

        cached_rgb = self._cached_rgb_state(device)
        if cached_rgb is not None:
            current_raw: Dict[str, Any] = dict(cached_rgb)
        else:
            current_raw = dict(get_rgb_scaffold(model_id=device.model_id))
            self._schedule_rgb_cache_warm(device)
        current = dict(current_raw) if isinstance(current_raw, dict) else get_rgb_scaffold(model_id=device.model_id)

        def _sync_from_state(state: Dict[str, Any]) -> Tuple[str, int, str, set[str], Sequence[Any]]:
            mode = str(state.get("mode") or "off").strip().lower()
            brightness = max(0, min(100, int(state.get("brightness", 60))))
            color = str(state.get("color") or "00ff00").strip().lower()
            modes_supported_raw = state.get("modes_supported")
            supported = {
                str(item).strip().lower()
                for item in (modes_supported_raw or [])
                if str(item).strip()
            }
            return mode, brightness, color, supported, modes_supported_raw

        current_mode, current_brightness, current_color, supported_modes, modes_raw = _sync_from_state(current)
        action_default = 0
        while True:
            action_index = self._select_menu(
                stdscr,
                title="RGB Editor",
                description="Choose RGB action",
                options=[
                    "Apply preset",
                    "Manual edit",
                    "Save current as preset",
                    "Delete preset",
                ],
                default_index=action_default,
                footer="Up/Down select | Enter confirm | Esc close editor",
            )
            if action_index is None:
                return
            action_default = int(action_index)

            if action_index == 0:
                while True:
                    preset_names = [
                        name
                        for name in presets.keys()
                        if not supported_modes or str(presets[name].get("mode", "")).strip().lower() in supported_modes
                    ]
                    if not preset_names:
                        self._set_status("No RGB presets available for current BLE mode support", hold_seconds=6.0)
                        break
                    preset_labels = [
                        f"{name}  ({presets[name]['mode']} {presets[name]['brightness']}% #{presets[name]['color']})"
                        for name in preset_names
                    ]
                    selected_preset_index = self._select_menu(
                        stdscr,
                        title="RGB Presets",
                        description="Select preset to apply",
                        options=preset_labels,
                        default_index=0,
                        footer="Up/Down select | Enter apply | Esc back",
                    )
                    if selected_preset_index is None:
                        break
                    preset_name = preset_names[selected_preset_index]
                    preset = presets[preset_name]
                    try:
                        rgb_state = self._run_with_modal(
                            stdscr,
                            title="RGB Editor",
                            description=f"Applying preset '{preset_name}'",
                            footer="Writing RGB values",
                            work=lambda: self._apply_rgb_state(
                                device,
                                mode=str(preset["mode"]),
                                brightness=int(preset["brightness"]),
                                color=str(preset["color"]),
                            ),
                        )
                        if isinstance(rgb_state, dict):
                            current = dict(rgb_state)
                            current_mode, current_brightness, current_color, supported_modes, modes_raw = _sync_from_state(current)
                        apply_state = str(rgb_state.get("hardware_apply") or "unknown")
                        self.status = (
                            f"RGB preset '{preset_name}' applied: "
                            f"{rgb_state.get('mode')} {rgb_state.get('brightness')}% "
                            f"#{rgb_state.get('color')} ({apply_state})"
                        )
                    except Exception as exc:
                        self._set_status(f"Could not apply RGB preset: {exc}", hold_seconds=8.0)
                    break
                continue

            if action_index == 1:
                modes = [str(item).strip().lower() for item in (modes_raw or []) if str(item).strip()]
                if not modes:
                    modes = ["off", "static", "breathing", "breathing-single", "breathing-random", "spectrum"]
                mode_default = modes.index(current_mode) if current_mode in modes else 0
                mode: Optional[str] = None
                brightness: Optional[int] = None
                color: Optional[str] = None
                step = "mode"
                while True:
                    if step == "mode":
                        mode_index = self._select_menu(
                            stdscr,
                            title="RGB Mode",
                            description="Choose RGB mode",
                            options=modes,
                            default_index=mode_default,
                            footer="Up/Down select | Enter confirm | Esc back",
                        )
                        if mode_index is None:
                            break
                        mode_default = int(mode_index)
                        mode = modes[mode_index]
                        if mode == "off":
                            brightness = 0
                            color = current_color
                            step = "apply"
                        else:
                            step = "brightness"
                        continue

                    if step == "brightness":
                        brightness = self._prompt_int(
                            stdscr,
                            "RGB brightness (0-100)",
                            current_brightness,
                            help_text="Set brightness percent for the selected RGB mode.",
                        )
                        if brightness is None:
                            step = "mode"
                            continue
                        if brightness < 0 or brightness > 100:
                            self._show_action_dialog(
                                stdscr,
                                title="Invalid brightness",
                                message="Brightness must be between 0 and 100.",
                            )
                            continue
                        if mode in {"static", "breathing", "breathing-single", "breathing-random"}:
                            step = "color"
                        else:
                            color = current_color
                            step = "apply"
                        continue

                    if step == "color":
                        typed_color = self._prompt_text(
                            stdscr,
                            "RGB color (hex)",
                            current_color,
                            help_text="Example: 00ff88 or #00ff88",
                            max_len=7,
                        )
                        if typed_color is None:
                            step = "brightness"
                            continue
                        try:
                            color = self._normalize_hex_color(typed_color)
                        except ValueError as exc:
                            self._show_action_dialog(
                                stdscr,
                                title="Invalid color",
                                message=str(exc),
                            )
                            continue
                        step = "apply"
                        continue

                    if mode is None:
                        break
                    try:
                        rgb_state = self._run_with_modal(
                            stdscr,
                            title="RGB Editor",
                            description=f"Applying mode '{mode}'",
                            footer="Writing RGB values",
                            work=lambda: self._apply_rgb_state(
                                device,
                                mode=mode,
                                brightness=brightness,
                                color=color,
                            ),
                        )
                        if isinstance(rgb_state, dict):
                            current = dict(rgb_state)
                            current_mode, current_brightness, current_color, supported_modes, modes_raw = _sync_from_state(current)
                        apply_state = str(rgb_state.get("hardware_apply") or "unknown")
                        self.status = (
                            f"RGB updated: {rgb_state.get('mode')} {rgb_state.get('brightness')}% "
                            f"#{rgb_state.get('color')} ({apply_state})"
                        )
                    except Exception as exc:
                        self._set_status(f"Could not set RGB: {exc}", hold_seconds=8.0)
                    break
                continue

            if action_index == 2:
                preset_name = self._prompt_text(
                    stdscr,
                    "Preset name",
                    "my-preset",
                    help_text="Save current RGB state under this name.",
                    max_len=32,
                )
                if preset_name is None:
                    continue
                normalized_name = str(preset_name).strip()
                if not normalized_name:
                    self._show_action_dialog(
                        stdscr,
                        title="Invalid preset name",
                        message="Preset name cannot be empty.",
                    )
                    continue
                try:
                    _store_path, updated_presets = save_rgb_preset(
                        model_id=device.model_id,
                        name=normalized_name,
                        mode=current_mode,
                        brightness=int(current_brightness),
                        color=current_color,
                    )
                    presets = dict(updated_presets)
                    self.status = f"Saved RGB preset '{normalized_name}'"
                except Exception as exc:
                    self._set_status(f"Could not save preset: {exc}", hold_seconds=8.0)
                continue

            preset_names = [
                name
                for name in presets.keys()
                if name
                not in {
                    "off",
                    "static-green",
                    "breathing-green",
                    "breathing-random",
                    "breathing-warm",
                    "spectrum-medium",
                }
            ]
            if not preset_names:
                self._set_status("No custom RGB presets to delete", hold_seconds=6.0)
                continue
            remove_index = self._select_menu(
                stdscr,
                title="Delete Preset",
                description="Select preset to delete",
                options=preset_names,
                default_index=0,
                footer="Up/Down select | Enter delete | Esc back",
            )
            if remove_index is None:
                continue
            preset_name = preset_names[remove_index]
            try:
                delete_rgb_preset(
                    model_id=device.model_id,
                    name=preset_name,
                )
                presets.pop(preset_name, None)
                self.status = f"Deleted RGB preset '{preset_name}'"
            except Exception as exc:
                self._set_status(f"Could not delete preset: {exc}", hold_seconds=8.0)

    def _apply_button_mapping_state(
        self,
        device: DetectedDevice,
        *,
        button: str,
        action: str,
    ) -> Dict[str, Any]:
        backend = self.service.resolve_backend(device)
        _, local_state = set_button_mapping_scaffold(
            model_id=device.model_id,
            button=button,
            action=action,
        )
        result = dict(local_state)
        hardware_apply = "fallback-local"
        try:
            hardware_state = backend.set_button_mapping(
                device,
                button=button,
                action=action,
            )
            if isinstance(hardware_state, dict):
                for key in ("mapping", "buttons_supported", "actions_suggested"):
                    if key in hardware_state:
                        result[key] = hardware_state[key]
            hardware_apply = "applied"
        except CapabilityUnsupportedError:
            hardware_apply = "fallback-local"
        result["hardware_apply"] = hardware_apply
        mapping_cache = getattr(self, "_button_mapping_cache", None)
        if isinstance(mapping_cache, dict):
            mapping_cache[str(device.identifier)] = dict(result)
        return result

    def _edit_button_mapping(self, stdscr) -> None:
        device = self._selected()
        if device is None:
            self.status = "No device selected"
            return
        if self._is_detect_only_backend(device):
            self.status = "Selected backend is detect-only (macos-profiler); button mapping is unavailable"
            return
        if "button-mapping" not in device.capabilities:
            self.status = "Selected device does not support button mapping"
            return

        def _load_button_data() -> Tuple[Dict[str, Any], Dict[str, Any], List[str], List[str]]:
            state = self._read_button_mapping_for_ui(device)
            mapping_local = state.get("mapping")
            mapping_dict = dict(mapping_local) if isinstance(mapping_local, dict) else {}
            buttons_raw_local = state.get("buttons_supported")
            buttons_local = [str(item).strip() for item in (buttons_raw_local or []) if str(item).strip()]
            if not buttons_local:
                buttons_local = list(mapping_dict.keys())
            actions_local = self._read_button_actions_for_ui(device, state)
            return state, mapping_dict, buttons_local, actions_local

        def _button_tuple_from_state(state: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], List[str], List[str]]:
            mapping_local = state.get("mapping")
            mapping_dict = dict(mapping_local) if isinstance(mapping_local, dict) else {}
            buttons_raw_local = state.get("buttons_supported")
            buttons_local = [str(item).strip() for item in (buttons_raw_local or []) if str(item).strip()]
            if not buttons_local:
                buttons_local = list(mapping_dict.keys())
            actions_local = self._button_actions_from_mapping_state(state)
            if not actions_local:
                actions_local = self._read_button_actions_for_ui(device, state)
            return state, mapping_dict, buttons_local, actions_local

        cached_btn = self._cached_button_mapping_state(device)
        if cached_btn is not None:
            loaded: Tuple[Dict[str, Any], Dict[str, Any], List[str], List[str]] = _button_tuple_from_state(cached_btn)
        else:
            scaffold_state = dict(get_button_mapping_scaffold(model_id=device.model_id))
            loaded = _button_tuple_from_state(scaffold_state)
            _st, _mp, buttons_try, _ac = loaded
            if not buttons_try:
                loaded = _button_tuple_from_state(
                    dict(get_button_mapping_scaffold(model_id="deathadder-v2-pro"))
                )
            self._schedule_button_mapping_cache_warm(device)

        mapping_state, mapping, buttons, actions = loaded
        if not buttons:
            self._set_status("No buttons available for mapping on this device", hold_seconds=8.0)
            return
        if not actions:
            actions = ["mouse:left", "mouse:right", "mouse:middle", "mouse:back", "mouse:forward", "dpi:cycle", "disabled"]

        selected_index = 0
        stdscr.timeout(-1)
        while True:
            lines = self._build_button_mapping_modal_lines(
                stdscr,
                buttons=buttons,
                mapping=mapping,
                selected_index=selected_index,
            )
            self._draw_modal_box(
                stdscr,
                title="Button Mapping",
                body_lines=lines,
                footer="Choose button, then map action. Right panel visualizes button slots.",
                dim_full_screen=False,
            )
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                selected_index = (selected_index - 1) % len(buttons)
                continue
            if key in (curses.KEY_DOWN, ord("j")):
                selected_index = (selected_index + 1) % len(buttons)
                continue
            if key in (ord("q"), 27):
                stdscr.timeout(60)
                return
            if key == ord("r"):
                try:
                    reloaded = self._load_with_modal(
                        stdscr,
                        title="Button Mapping",
                        description="Reloading button map",
                        loader=_load_button_data,
                        footer="Refreshing",
                    )
                except Exception as exc:
                    self._set_status(f"Could not reload button mapping: {exc}", hold_seconds=8.0)
                    continue
                if reloaded is None:
                    continue
                mapping_state, mapping, buttons, actions = reloaded
                if not buttons:
                    self._set_status("No buttons available for mapping on this device", hold_seconds=8.0)
                    stdscr.timeout(60)
                    return
                if not actions:
                    actions = [
                        "mouse:left",
                        "mouse:right",
                        "mouse:middle",
                        "mouse:back",
                        "mouse:forward",
                        "dpi:cycle",
                        "disabled",
                    ]
                selected_index = max(0, min(selected_index, len(buttons) - 1))
                continue
            if key == ord("c"):
                captured = self._capture_button_assist(stdscr, buttons=buttons)
                if captured is not None and captured in buttons:
                    selected_index = buttons.index(captured)
                    self._set_status(f"Capture selected {self._button_label(captured)}", hold_seconds=4.0)
                continue
            if key not in (10, 13, curses.KEY_ENTER, ord("e"), ord(" ")):
                continue

            selected_button = buttons[selected_index]
            current_action = str(mapping.get(selected_button) or "").strip().lower()
            action_options = list(actions) + ["custom..."]
            default_idx = action_options.index(current_action) if current_action in action_options else 0
            while True:
                action_idx = self._select_menu(
                    stdscr,
                    title=f"Action for {self._button_label(selected_button)}",
                    description="Choose action",
                    options=action_options,
                    default_index=default_idx,
                    footer="Up/Down select | Enter confirm | Esc back",
                )
                if action_idx is None:
                    break
                default_idx = int(action_idx)
                action = action_options[action_idx]
                if action == "custom...":
                    custom = self._prompt_text(
                        stdscr,
                        "Custom action",
                        current_action or "keyboard:0x2c",
                        help_text=(
                            "Examples:\n"
                            "- mouse:scroll-down\n"
                            "- keyboard:0x2c\n"
                            "- mouse-turbo:mouse:left:142\n"
                            "- keyboard-turbo:0x2c:142"
                        ),
                    )
                    if custom is None:
                        # One-step back: return to action selection.
                        continue
                    action = str(custom).strip().lower()
                    if not action:
                        self._show_action_dialog(
                            stdscr,
                            title="Invalid action",
                            message="Action cannot be empty.",
                        )
                        continue

                try:
                    state = self._run_with_modal(
                        stdscr,
                        title="Button Mapping",
                        description=f"Applying {self._button_label(selected_button)} -> {action}",
                        footer="Writing button map",
                        work=lambda: self._apply_button_mapping_state(
                            device,
                            button=selected_button,
                            action=action,
                        ),
                    )
                    next_mapping = state.get("mapping")
                    if isinstance(next_mapping, dict):
                        mapping = dict(next_mapping)
                    apply_state = str(state.get("hardware_apply") or "unknown")
                    self.status = f"Mapped {self._button_label(selected_button)} -> {action} ({apply_state})"
                except Exception as exc:
                    self._set_status(f"Could not set button mapping: {exc}", hold_seconds=8.0)
                break

    def _set_custom_dpi(self, stdscr) -> None:
        if self.state.dpi is None:
            self.status = "DPI cannot be read for selected device"
            return

        current_x, current_y = self.state.dpi
        dpi_x = self._prompt_int(stdscr, "Set DPI X", current_x)
        if dpi_x is None:
            return

        dpi_y = self._prompt_int(stdscr, "Set DPI Y", current_y)
        if dpi_y is None:
            return

        self._set_dpi(dpi_x, dpi_y)

    def _move_selection(self, step: int) -> None:
        if not self.devices:
            return

        self.selected_index = (self.selected_index + step) % len(self.devices)
        if hasattr(self, "_queue_state_refresh"):
            self._queue_state_refresh(force=False)
        else:
            self._refresh_state(force=False)
