"""State and mutation actions for the curses TUI."""

from __future__ import annotations

import curses
import os
import time
from typing import Optional, Sequence, Tuple

from razecli.dpi_autosync import autosync_enabled, load_autosync_settings, save_autosync_settings
from razecli.errors import CapabilityUnsupportedError
from razecli.transport_sync import mirror_to_transport_targets
from razecli.types import DetectedDevice

from razecli.tui_types import DeviceState, MAX_DPI_STAGES


class TuiActionsMixin:
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
                    else:
                        if not self._status_locked():
                            self.status = f"Could not read DPI profiles: {exc}"

            if "dpi" in device.capabilities and state.dpi is None:
                try:
                    state.dpi = backend.get_dpi(device)
                except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                    if bt_experimental:
                        bt_read_failed = True
                    else:
                        if not self._status_locked():
                            self.status = f"Could not read DPI: {exc}"

            if "poll-rate" in device.capabilities:
                try:
                    state.poll_rate = backend.get_poll_rate(device)
                except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                    if bt_experimental:
                        bt_read_failed = True
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
                        else:
                            if not self._status_locked():
                                self.status = f"Could not read battery: {exc}"

            self.state = state
            self._state_cache[device.identifier] = self._clone_state(state)
            self._state_refreshed_at[device.identifier] = now
            if bt_read_failed and not self._status_locked():
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
