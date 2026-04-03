"""State and mutation actions for the curses TUI."""

from __future__ import annotations

import curses
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from razecli.dpi_autosync import autosync_enabled, load_autosync_settings, save_autosync_settings
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

        def _mk(button: str, short_label: str) -> str:
            marker = ">" if selected == button else " "
            return f"[{marker}{short_label}]"

        m1 = _mk("left_click", "M1")
        m2 = _mk("right_click", "M2")
        m3 = _mk("middle_click", "M3")
        m4 = _mk("side_1", "M4")
        m5 = _mk("side_2", "M5")
        dpi = _mk("dpi_cycle", "DPI")
        selected_label = cls._button_label(selected) if selected else "-"

        # Shape from user reference (asii2.txt); slots filled with live markers.
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
            f"Buttons: {m1} {m2} {m3} {m4} {m5} {dpi}",
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
        return text[: width - 1] + "…"

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

    @staticmethod
    def _is_bt_008e(device: Optional[DetectedDevice]) -> bool:
        return bool(
            device
            and device.product_id == 0x008E
            and device.backend in {"rawhid", "macos-ble"}
        )

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

            if self.preselected_device_id:
                for idx, device in enumerate(self.devices):
                    if device.identifier == self.preselected_device_id:
                        self.selected_index = idx
                        self.preselected_device_id = None
                        break

            if self.selected_index >= len(self.devices):
                self.selected_index = max(0, len(self.devices) - 1)

            if not self.devices:
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
            bt_experimental = self._is_bt_008e(device)
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
                if failed:
                    self.status = (
                        "Bluetooth mode (1532:008E) is experimental; "
                        f"could not read: {failed}"
                    )
                else:
                    self.status = "Bluetooth mode (1532:008E) is experimental; some values may be unavailable"
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
            if self._is_bt_008e(device):
                self.status = "Autosync exists but Bluetooth endpoint (008E) could not be updated on this host"

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
            self._set_status(
                "Only one DPI profile is available over current transport. "
                "On BLE (1532:008E), multi-profile editing is limited; use USB/2.4 for full profile tables.",
                hold_seconds=8.0,
            )
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
            self._render(stdscr)
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
            self._is_bt_008e(device)
            and device.backend == "macos-ble"
            and len(current) <= 1
            and target_count > len(current)
        ):
            message = (
                "This BLE endpoint currently reports a single DPI profile. "
                "Adding more profiles is not mapped reliably yet. "
                "Use USB/2.4 to create the profile table first."
            )
            self._set_status(message, hold_seconds=8.0)
            self._show_action_dialog(
                stdscr,
                title="BLE limitation",
                message=message,
            )
            return

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
        try:
            while not done.is_set():
                self._draw_modal_box(
                    stdscr,
                    title=title,
                    body_lines=[
                        f"{description}{self._loading_dots()}",
                        "",
                        f"{self._spinner()} Please wait",
                    ],
                    footer=f"{footer} | Esc cancels",
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
        try:
            while not done.is_set():
                self._draw_modal_box(
                    stdscr,
                    title=title,
                    body_lines=[
                        f"{description}{self._loading_dots()}",
                        "",
                        f"{self._spinner()} Please wait",
                    ],
                    footer=footer,
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
        try:
            state = backend.get_rgb(device)
            if isinstance(state, dict):
                return dict(state)
        except Exception:
            pass
        return get_rgb_scaffold(model_id=device.model_id)

    def _read_button_mapping_for_ui(self, device: DetectedDevice) -> Dict[str, Any]:
        backend = self.service.resolve_backend(device)
        try:
            state = backend.get_button_mapping(device)
            if isinstance(state, dict):
                return dict(state)
        except Exception:
            pass
        return get_button_mapping_scaffold(model_id=device.model_id)

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
            rgb_cache[device.identifier] = dict(result)
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
        while True:
            body_lines = [str(description).strip(), ""]
            for idx, value in enumerate(values):
                marker = ">" if idx == index else " "
                body_lines.append(f"{marker} {value}")
            self._draw_modal_box(
                stdscr,
                title=title,
                body_lines=body_lines,
                footer=footer,
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
            loaded = self._load_with_modal(
                stdscr,
                title="RGB Editor",
                description="Loading RGB state",
                loader=lambda: (
                    self._read_rgb_state_for_ui(device),
                    get_rgb_presets(model_id=device.model_id),
                ),
                footer="Preparing RGB editor",
            )
        except Exception as exc:
            self._set_status(f"Could not load RGB state: {exc}", hold_seconds=8.0)
            return
        if loaded is None:
            return
        current_raw, presets_raw = loaded
        current = dict(current_raw) if isinstance(current_raw, dict) else get_rgb_scaffold(model_id=device.model_id)
        presets = dict(presets_raw) if isinstance(presets_raw, dict) else get_rgb_presets(model_id=device.model_id)

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
                    modes = ["off", "static", "breathing", "spectrum"]
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
                        if mode in {"static", "breathing"}:
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
                if name not in {"off", "static-green", "breathing-warm", "spectrum-medium"}
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
            mapping_cache[device.identifier] = dict(result)
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

        try:
            loaded = self._load_with_modal(
                stdscr,
                title="Button Mapping",
                description="Loading button map",
                loader=_load_button_data,
                footer="Preparing button editor",
            )
        except Exception as exc:
            self._set_status(f"Could not load button mapping: {exc}", hold_seconds=8.0)
            return
        if loaded is None:
            return

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
