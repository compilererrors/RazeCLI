"""State and mutation actions for the curses TUI."""

from __future__ import annotations

import curses
import os
from typing import Optional, Sequence, Tuple

from razecli.dpi_autosync import autosync_enabled, load_autosync_settings, save_autosync_settings
from razecli.errors import CapabilityUnsupportedError
from razecli.transport_sync import mirror_to_transport_targets
from razecli.types import DetectedDevice

from razecli.tui_types import DeviceState, MAX_DPI_STAGES


class TuiActionsMixin:
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

    def _refresh_devices(self) -> None:
        self.devices = self.service.discover_devices(
            model_filter=self.model_filter,
            collapse_transports=self.collapse_transports,
        )
        self._autosync_attempted.clear()

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
        self._refresh_state()

    def _refresh_state(self) -> None:
        device = self._selected()
        if device is None:
            self.state = DeviceState()
            return

        backend = self.service.resolve_backend(device)
        if autosync_enabled():
            self._maybe_apply_autosync(device, backend)
        state = DeviceState()
        bt_experimental = self._is_bt_008e(device)
        bt_read_failed = False

        if "dpi" in device.capabilities:
            try:
                state.dpi = backend.get_dpi(device)
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                if bt_experimental:
                    bt_read_failed = True
                else:
                    self.status = f"Could not read DPI: {exc}"

        if "dpi-stages" in device.capabilities:
            try:
                active, stages = backend.get_dpi_stages(device)
                state.dpi_active_stage = int(active)
                state.dpi_stages = list(stages)
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                if bt_experimental:
                    bt_read_failed = True
                else:
                    self.status = f"Could not read DPI profiles: {exc}"

        if "poll-rate" in device.capabilities:
            try:
                state.poll_rate = backend.get_poll_rate(device)
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                if bt_experimental:
                    bt_read_failed = True
                else:
                    self.status = f"Could not read poll-rate: {exc}"

        if "battery" in device.capabilities:
            try:
                state.battery = backend.get_battery(device)
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                if bt_experimental:
                    bt_read_failed = True
                else:
                    self.status = f"Could not read battery: {exc}"

        self.state = state
        if bt_read_failed:
            self.status = "Bluetooth mode (1532:008E) is experimental; some values may be unavailable"

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

        backend = self.service.resolve_backend(device)
        try:
            backend.set_dpi(device, dpi_x, dpi_y)
            mirror_ok, mirror_failed = mirror_to_transport_targets(
                self.service,
                device,
                lambda target: self.service.resolve_backend(target).set_dpi(target, dpi_x, dpi_y),
                required_capability="dpi",
            )
            self._persist_autosync(device, backend)
            if mirror_ok or mirror_failed:
                self.status = (
                    f"DPI set to {dpi_x}:{dpi_y} | mirrored={mirror_ok} failed={mirror_failed}"
                )
            else:
                self.status = f"DPI set to {dpi_x}:{dpi_y}"
            self._refresh_state()
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self.status = f"Could not set DPI: {exc}"

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

        try:
            backend.set_poll_rate(device, target)
            mirror_ok, mirror_failed = mirror_to_transport_targets(
                self.service,
                device,
                lambda peer: self.service.resolve_backend(peer).set_poll_rate(peer, target),
                required_capability="poll-rate",
            )
            if mirror_ok or mirror_failed:
                self.status = (
                    f"Poll-rate set to {target} Hz | mirrored={mirror_ok} failed={mirror_failed}"
                )
            else:
                self.status = f"Poll-rate set to {target} Hz"
            self._refresh_state()
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self.status = f"Could not set poll-rate: {exc}"

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
        try:
            active_stage, stages = backend.get_dpi_stages(device)
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self.status = f"Could not read DPI profiles: {exc}"
            return

        stages_list = list(stages)
        if not stages_list:
            self.status = "No DPI profiles found on device"
            return

        if active_stage < 1 or active_stage > len(stages_list):
            target = 1
        else:
            target = (active_stage % len(stages_list)) + 1

        target_dpi = stages_list[target - 1]
        try:
            # Safe default: switch effective DPI only.
            # Full stage-list rewrites can reset stage values on some transports/firmware.
            if self._unsafe_stage_activate_enabled():
                backend.set_dpi_stages(device, target, stages_list)
                self._persist_autosync(device, backend)
                self.status = (
                    f"DPI profile {target}/{len(stages_list)} active "
                    f"({target_dpi[0]}:{target_dpi[1]})"
                )
            else:
                backend.set_dpi(device, int(target_dpi[0]), int(target_dpi[1]))
                self.status = (
                    f"DPI switched to stage {target}/{len(stages_list)} value "
                    f"({target_dpi[0]}:{target_dpi[1]})"
                )
            self._refresh_state()
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self.status = f"Could not switch DPI stage: {exc}"

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
        try:
            active_stage, stages = backend.get_dpi_stages(device)
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self.status = f"Could not read DPI profiles: {exc}"
            return

        current = list(stages)
        if not current:
            self.status = "No DPI profiles found on device"
            return

        target_count = self._prompt_int(stdscr, f"Set DPI profile count (1-{MAX_DPI_STAGES})", len(current))
        if target_count is None:
            return
        if target_count < 1 or target_count > MAX_DPI_STAGES:
            self.status = f"DPI profile count must be between 1 and {MAX_DPI_STAGES}"
            return

        if target_count == len(current):
            self.status = f"DPI profile count unchanged ({target_count})"
            return

        new_active = int(active_stage)
        if new_active < 1 or new_active > len(current):
            new_active = 1

        if target_count > len(current):
            default_x, default_y = self._default_stage_dpi(new_active, current)
            add_x = self._prompt_int(stdscr, "New profile DPI X", default_x)
            if add_x is None:
                return
            add_y = self._prompt_int(stdscr, "New profile DPI Y", default_y)
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

        try:
            backend.set_dpi_stages(device, new_active, current)
            mirror_ok, mirror_failed = mirror_to_transport_targets(
                self.service,
                device,
                lambda peer: self.service.resolve_backend(peer).set_dpi_stages(peer, new_active, current),
                required_capability="dpi-stages",
            )
            self._persist_autosync(device, backend)
            if mirror_ok or mirror_failed:
                self.status = (
                    f"DPI profiles updated: {len(current)} total (active {new_active})"
                    f" | mirrored={mirror_ok} failed={mirror_failed}"
                )
            else:
                self.status = f"DPI profiles updated: {len(current)} total (active {new_active})"
            self._refresh_state()
        except Exception as exc:  # pragma: no cover - runtime/hardware dependent
            self.status = f"Could not set DPI profiles: {exc}"

    def _prompt_int(self, stdscr, label: str, default: Optional[int] = None) -> Optional[int]:
        height, width = stdscr.getmaxyx()
        prompt = f"{label}"
        if default is not None:
            prompt += f" [{default}]"
        prompt += ": "

        self._safe_add(stdscr, height - 2, 0, " " * max(1, width - 1))
        self._safe_add(stdscr, height - 2, 0, prompt)

        curses.echo()
        curses.curs_set(1)
        stdscr.refresh()
        raw = stdscr.getstr(height - 2, min(len(prompt), width - 2), 16)
        curses.noecho()
        curses.curs_set(0)

        text = raw.decode(errors="ignore").strip()
        if not text and default is not None:
            return default
        if not text:
            return None

        try:
            return int(text)
        except ValueError:
            self.status = "Invalid number"
            return None

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
        self._refresh_state()

