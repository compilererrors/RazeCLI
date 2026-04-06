"""Controller orchestration for the curses TUI."""

from __future__ import annotations

import curses
import os
import queue
import threading
import time
from typing import Any, Callable, List, Optional

from razecli.device_service import DeviceService
from razecli.errors import RazeCliError
from razecli.types import DetectedDevice

from razecli.tui_actions import TuiActionsMixin
from razecli.tui_types import DPI_STEP, DeviceState
from razecli.tui_view import TuiViewMixin

# While `_job_label` is set (state refresh, discovery, etc.), most keys are ignored so the
# UI does not stack work. Editors and DPI shortcuts stay allowed so g/b/n still open menus
# immediately while RGB/button lines load in the background.
_TUI_KEYS_ALLOWED_DURING_BACKGROUND_JOB = frozenset(
    {
        curses.KEY_UP,
        curses.KEY_DOWN,
        ord("k"),
        ord("j"),
        ord("?"),
        ord("["),
        ord("]"),
        ord("g"),
        ord("b"),
        ord("n"),
        ord("d"),
        ord("s"),
        ord("p"),
        ord("+"),
        ord("="),
        ord("-"),
    }
)


class TuiController(TuiViewMixin, TuiActionsMixin):
    @staticmethod
    def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
        raw = str(os.getenv(name, "")).strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        return max(minimum, min(maximum, value))

    def __init__(
        self,
        service: DeviceService,
        model_filter: Optional[str] = None,
        preselected_device_id: Optional[str] = None,
        collapse_transports: bool = True,
        startup_editor: Optional[str] = None,
    ) -> None:
        self.service = service
        self.model_filter = model_filter
        self.preselected_device_id = preselected_device_id
        self.collapse_transports = collapse_transports

        self.devices: List[DetectedDevice] = []
        self.selected_index = 0
        self.scroll_offset = 0
        self.status = "Press r to refresh devices"
        self.state = DeviceState()
        self._autosync_attempted: set[str] = set()
        self.split_ratio = self._env_float(
            "RAZECLI_TUI_SPLIT_RATIO",
            default=0.5,
            minimum=0.30,
            maximum=0.70,
        )
        self._dpi_adjust_step = max(
            1,
            int(
                self._env_float(
                    "RAZECLI_TUI_DPI_ADJUST_STEP",
                    default=float(DPI_STEP),
                    minimum=1.0,
                    maximum=5000.0,
                )
            ),
        )
        self._state_cache: dict[str, DeviceState] = {}
        self._state_refreshed_at: dict[str, float] = {}
        self._battery_refreshed_at: dict[str, float] = {}
        self._rgb_cache: dict[str, dict[str, Any]] = {}
        self._button_mapping_cache: dict[str, dict[str, Any]] = {}
        self._refresh_interval_s = self._env_float(
            "RAZECLI_TUI_REFRESH_INTERVAL",
            default=1.5,
            minimum=0.2,
            maximum=10.0,
        )
        self._battery_refresh_interval_s = self._env_float(
            "RAZECLI_TUI_BATTERY_INTERVAL",
            default=20.0,
            minimum=1.0,
            maximum=60.0,
        )
        self._idle_refresh_interval_s = self._env_float(
            "RAZECLI_TUI_IDLE_REFRESH_INTERVAL",
            default=0.0,
            minimum=0.0,
            maximum=20.0,
        )
        self._last_idle_refresh_at = 0.0
        self._status_hold_until = 0.0
        self._env_overrides_backup: dict[str, Optional[str]] = {}
        self._pending_discovery = False
        self._discovery_in_progress = False
        self._pending_state_refresh = False
        self._pending_state_force = False
        self._state_loading_device_id: Optional[str] = None
        self._palette: dict[str, int] = {}
        self._job_label: Optional[str] = None
        self._job_results: "queue.Queue[tuple[bool, Any, Optional[Callable[[Any], None]], Optional[Callable[[Exception], None]]]]" = queue.Queue()
        self._bt_unavailable_fields: dict[str, tuple[str, ...]] = {}
        self._startup_editor = startup_editor if startup_editor in {"rgb", "button-mapping"} else None
        self._startup_editor_pending = bool(self._startup_editor)
        self._job_timeout_s = self._env_float(
            "RAZECLI_TUI_JOB_TIMEOUT",
            default=8.0,
            minimum=1.0,
            maximum=120.0,
        )
        self._state_refresh_timeout_s = self._env_float(
            "RAZECLI_TUI_STATE_REFRESH_TIMEOUT",
            default=10.0,
            minimum=1.0,
            maximum=180.0,
        )
        self._discovery_timeout_s = self._env_float(
            "RAZECLI_TUI_DISCOVERY_TIMEOUT",
            default=8.0,
            minimum=1.0,
            maximum=120.0,
        )
        self._state_refresh_retry_delay_s = self._env_float(
            "RAZECLI_TUI_STATE_REFRESH_RETRY_DELAY",
            default=2.0,
            minimum=0.5,
            maximum=30.0,
        )
        self._state_refresh_retry_max = int(
            self._env_float(
                "RAZECLI_TUI_STATE_REFRESH_RETRY_MAX",
                default=1.0,
                minimum=0.0,
                maximum=5.0,
            )
        )
        self._state_refresh_retry_attempts = 0
        self._state_refresh_retry_due_at = 0.0
        self._feature_prefetch_lock = threading.Lock()
        self._feature_prefetch_inflight_devices: set[str] = set()
        self._feature_prefetch_inflight_features: set[tuple[str, str]] = set()
        self._feature_prefetch_attempted: set[tuple[str, str]] = set()
        self._feature_prefetch_retry_due_at: dict[tuple[str, str], float] = {}
        self._feature_prefetch_retry_delay_s = self._env_float(
            "RAZECLI_TUI_FEATURE_PREFETCH_RETRY_DELAY",
            default=2.0,
            minimum=0.2,
            maximum=30.0,
        )

    def _init_theme(self) -> None:
        self._palette = {}
        if not curses.has_colors():
            return
        try:
            curses.start_color()
            curses.use_default_colors()
        except Exception:
            return

        def _pair(index: int, fg: int, bg: int = -1) -> int:
            try:
                curses.init_pair(index, fg, bg)
                return curses.color_pair(index)
            except Exception:
                return 0

        accent = _pair(1, curses.COLOR_CYAN)
        muted = _pair(2, curses.COLOR_BLUE)
        success = _pair(3, curses.COLOR_GREEN)
        warn = _pair(4, curses.COLOR_YELLOW)
        error = _pair(5, curses.COLOR_RED)
        selected = _pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)
        footer = _pair(7, curses.COLOR_WHITE)

        self._palette = {
            "header": curses.A_BOLD | accent,
            "panel_border": muted or curses.A_DIM,
            "panel_title": curses.A_BOLD | accent,
            "selected": selected or curses.A_REVERSE,
            "muted": muted or curses.A_DIM,
            "footer": curses.A_BOLD | (footer or 0),
            "modal_backdrop": curses.A_DIM,
            "modal_border": curses.A_BOLD | accent,
            "modal_title": curses.A_BOLD | accent,
            "modal_text": 0,
            "modal_footer": curses.A_DIM | (footer or 0),
            "modal_shadow": curses.A_DIM,
            "status": 0,
            "status_ok": curses.A_BOLD | success,
            "status_warn": curses.A_BOLD | warn,
            "status_error": curses.A_BOLD | error,
        }

    def _queue_discovery(self) -> None:
        self._pending_discovery = True
        self._set_status("Loading devices...", hold_seconds=0.5)

    def _queue_state_refresh(self, *, force: bool = False) -> None:
        self._pending_state_refresh = True
        self._pending_state_force = self._pending_state_force or force

    def _start_background_job(
        self,
        *,
        label: str,
        work: Callable[[], Any],
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        timeout_s: Optional[float] = None,
    ) -> bool:
        if self._job_label:
            if not self._status_locked():
                self._set_status(
                    f"{self._job_label} in progress...",
                    hold_seconds=0.8,
                )
            return False

        self._job_label = str(label).strip() or "Working"
        label_text = self._job_label
        timeout_value = float(timeout_s) if timeout_s is not None else float(self._job_timeout_s)
        if not self._status_locked():
            self._set_status(f"{self._job_label}...", hold_seconds=0.4)

        def _runner() -> None:
            done = threading.Event()
            holder: dict[str, Any] = {"result": None, "error": None}

            def _work_runner() -> None:
                try:
                    holder["result"] = work()
                except Exception as exc:  # pragma: no cover - runtime/hardware dependent
                    holder["error"] = exc
                finally:
                    done.set()

            threading.Thread(
                target=_work_runner,
                daemon=True,
                name=f"razecli-work-{label_text.lower().replace(' ', '-')}",
            ).start()

            if timeout_value > 0:
                done.wait(timeout=timeout_value)
                if not done.is_set():
                    self._job_results.put(
                        (
                            False,
                            TimeoutError(
                                f"{label_text} timed out after {timeout_value:.1f}s"
                            ),
                            on_success,
                            on_error,
                        )
                    )
                    return

            try:
                error = holder.get("error")
                if isinstance(error, Exception):
                    self._job_results.put((False, error, on_success, on_error))
                else:
                    self._job_results.put((True, holder.get("result"), on_success, on_error))
            except Exception as exc:  # pragma: no cover - defensive
                self._job_results.put((False, exc, on_success, on_error))

        threading.Thread(
            target=_runner,
            daemon=True,
            name=f"razecli-{self._job_label.lower().replace(' ', '-')}",
        ).start()
        return True

    def _drain_background_job_results(self) -> bool:
        drained = False
        while True:
            try:
                ok, payload, on_success, on_error = self._job_results.get_nowait()
            except queue.Empty:
                break
            drained = True
            completed_label = str(self._job_label or "").strip()
            self._job_label = None
            if ok:
                if on_success is not None:
                    try:
                        on_success(payload)
                    except Exception as exc:  # pragma: no cover - defensive
                        self._set_status(f"Post-action failed: {exc}", hold_seconds=8.0)
                else:
                    # Avoid leaving stale "Refreshing..." text when a background refresh
                    # completed without an explicit success message.
                    status_now = str(self.status).strip().lower()
                    if completed_label and status_now.startswith(completed_label.lower()):
                        selected = self._selected()
                        if selected is not None:
                            self.status = f"Ready | selected: {selected.identifier}"
                        elif self.devices:
                            self.status = f"Found {len(self.devices)} devices"
                        else:
                            self.status = "Ready"
                continue

            exc = payload if isinstance(payload, Exception) else Exception(str(payload))
            if on_error is not None:
                try:
                    on_error(exc)
                except Exception as nested:  # pragma: no cover - defensive
                    self._set_status(f"Post-error handler failed: {nested}", hold_seconds=8.0)
            else:
                self._set_status(f"Operation failed: {exc}", hold_seconds=8.0)
        return drained

    def _run_pending_io(self) -> bool:
        if self._job_label:
            return False
        if self._pending_discovery and not self._discovery_in_progress:
            self._pending_discovery = False

            def _on_discovery_success(_result: Any) -> None:
                if self.devices:
                    self._queue_state_refresh(force=True)

            def _on_discovery_error(exc: Exception) -> None:
                self._set_status(f"Device refresh failed: {exc}", hold_seconds=8.0)

            started = self._start_background_job(
                label="Refreshing devices",
                work=lambda: self._refresh_devices(eager_state=False),
                on_success=_on_discovery_success,
                on_error=_on_discovery_error,
                timeout_s=self._discovery_timeout_s,
            )
            if not started:
                self._pending_discovery = True
                return False
            return True

        if self._pending_state_refresh:
            force = bool(self._pending_state_force)
            self._pending_state_refresh = False
            self._pending_state_force = False

            def _on_state_success(_result: Any) -> None:
                self._state_refresh_retry_attempts = 0
                self._state_refresh_retry_due_at = 0.0

            def _on_state_error(exc: Exception) -> None:
                message = str(exc)
                if isinstance(exc, TimeoutError):
                    if self._state_refresh_retry_attempts < self._state_refresh_retry_max:
                        self._state_refresh_retry_attempts += 1
                        self._state_refresh_retry_due_at = time.monotonic() + float(
                            self._state_refresh_retry_delay_s
                        )
                        self._set_status(
                            "State refresh timed out; "
                            f"retrying in {self._state_refresh_retry_delay_s:.1f}s "
                            f"({self._state_refresh_retry_attempts}/{self._state_refresh_retry_max})...",
                            hold_seconds=max(2.0, float(self._state_refresh_retry_delay_s) + 1.0),
                        )
                        return
                    self._set_status(
                        f"State refresh timed out ({message}). Press r to retry.",
                        hold_seconds=10.0,
                    )
                    return
                self._set_status(f"State refresh failed: {message}", hold_seconds=8.0)

            started = self._start_background_job(
                label="Refreshing device state",
                work=lambda: self._refresh_state(force=force),
                on_success=_on_state_success,
                on_error=_on_state_error,
                timeout_s=self._state_refresh_timeout_s,
            )
            if not started:
                self._pending_state_refresh = True
                self._pending_state_force = self._pending_state_force or force
                return False
            return True

        return False

    def _apply_tui_ble_defaults(self) -> None:
        tuned_defaults = {
            "RAZECLI_BLE_BACKEND_TIMEOUT": "2.4",
            "RAZECLI_BLE_BACKEND_RESPONSE_TIMEOUT": "0.55",
            "RAZECLI_BLE_DPI_READ_ATTEMPTS": "3",
            "RAZECLI_BLE_DPI_READ_RETRY_DELAY": "0.02",
            "RAZECLI_BLE_POLL_READ_ATTEMPTS": "1",
            "RAZECLI_BLE_POLL_READ_RETRY_DELAY": "0.0",
        }
        for key, value in tuned_defaults.items():
            if os.getenv(key):
                continue
            self._env_overrides_backup[key] = None
            os.environ[key] = value

    def _restore_tui_ble_defaults(self) -> None:
        for key, old_value in self._env_overrides_backup.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
        self._env_overrides_backup.clear()

    def _adjust_split(self, delta: float) -> None:
        self.split_ratio = max(0.30, min(0.70, float(self.split_ratio) + float(delta)))
        self.status = f"Split ratio: {int(round(self.split_ratio * 100))}% / {int(round((1.0 - self.split_ratio) * 100))}%"

    def _show_help(self, stdscr) -> None:
        help_text = (
            "Navigation\n"
            "  Up/Down or k/j: Select device\n"
            "  r: Refresh devices\n"
            "\n"
            "Device actions\n"
            f"  + / -: Adjust DPI by {int(self._dpi_adjust_step)} (keyboard)\n"
            "  d: Set custom DPI\n"
            "  g: RGB editor\n"
            "  b: Button-mapping table/editor (manual + capture assist)\n"
            "  s: Next DPI profile\n"
            "  n: DPI levels editor (count, increment, presets)\n"
            "  p: Next poll-rate\n"
            "\n"
            "Layout\n"
            "  [ / ]: Resize split panels\n"
            "  ?: Show this help\n"
            "  q or Esc: Quit"
        )
        self._show_modal_message(
            stdscr,
            title="RazeCLI Help",
            message=help_text,
            footer="Press Enter, Space, Esc, or q to close",
        )

    def _selected(self) -> Optional[DetectedDevice]:
        if not self.devices:
            return None
        if self.selected_index < 0:
            self.selected_index = 0
        if self.selected_index >= len(self.devices):
            self.selected_index = len(self.devices) - 1
        return self.devices[self.selected_index]

    def _maybe_schedule_feature_prefetch(self) -> None:
        if self._startup_editor_pending:
            return
        device = self._selected()
        if device is None:
            return
        if self._is_detect_only_backend(device):
            return

        feature_targets: list[tuple[str, dict[str, dict[str, Any]], str]] = []
        dev_cache_key = str(device.identifier)
        if "rgb" in device.capabilities and dev_cache_key not in self._rgb_cache:
            feature_targets.append(("rgb", self._rgb_cache, "get_rgb"))
        if "button-mapping" in device.capabilities and dev_cache_key not in self._button_mapping_cache:
            feature_targets.append(("button-mapping", self._button_mapping_cache, "get_button_mapping"))
        if not feature_targets:
            return

        device_id = str(device.identifier)
        with self._feature_prefetch_lock:
            if device_id in self._feature_prefetch_inflight_devices:
                return
            now = time.monotonic()
            pending = [
                (feature, cache_ref, method_name)
                for feature, cache_ref, method_name in feature_targets
                if (device_id, feature) not in self._feature_prefetch_attempted
                and now >= float(self._feature_prefetch_retry_due_at.get((device_id, feature), 0.0))
            ]
            if not pending:
                return
            self._feature_prefetch_inflight_devices.add(device_id)
            self._feature_prefetch_inflight_features.update(
                (device_id, feature) for feature, _, _ in pending
            )

        def _worker() -> None:
            try:
                backend = self.service.resolve_backend(device)
                for feature, cache_ref, method_name in pending:
                    cache_key = (device_id, feature)
                    success = False
                    try:
                        getter = getattr(backend, method_name, None)
                        if getter is None:
                            continue
                        payload = getter(device)
                        if isinstance(payload, dict):
                            cache_ref[device_id] = dict(payload)
                            success = True
                    except Exception:
                        pass
                    finally:
                        with self._feature_prefetch_lock:
                            self._feature_prefetch_inflight_features.discard(cache_key)
                            if success:
                                self._feature_prefetch_attempted.add(cache_key)
                                self._feature_prefetch_retry_due_at.pop(cache_key, None)
                            else:
                                self._feature_prefetch_retry_due_at[cache_key] = (
                                    time.monotonic() + float(self._feature_prefetch_retry_delay_s)
                                )
            finally:
                with self._feature_prefetch_lock:
                    self._feature_prefetch_inflight_devices.discard(device_id)

        threading.Thread(
            target=_worker,
            daemon=True,
            name=f"razecli-feature-prefetch-{device_id.lower().replace(':', '').replace('-', '')}",
        ).start()

    def _is_feature_prefetch_inflight(self, device_id: str, feature: str) -> bool:
        key = (str(device_id), str(feature))
        with self._feature_prefetch_lock:
            return key in self._feature_prefetch_inflight_features

    def _has_pending_activity(self) -> bool:
        return bool(
            self._job_label
            or self._pending_discovery
            or self._discovery_in_progress
            or self._pending_state_refresh
            or self._state_loading_device_id
        )

    @staticmethod
    def _hard_exit(stdscr) -> None:
        try:
            curses.nocbreak()
        except Exception:
            pass
        try:
            stdscr.keypad(False)
        except Exception:
            pass
        try:
            curses.echo()
        except Exception:
            pass
        try:
            curses.endwin()
        except Exception:
            pass
        os._exit(0)

    def run(self, stdscr) -> int:
        try:
            curses.set_escdelay(25)
        except Exception:
            pass
        curses.curs_set(0)
        stdscr.keypad(True)
        stdscr.timeout(40)
        self._init_theme()

        self._apply_tui_ble_defaults()

        try:
            self._queue_discovery()
            self._run_pending_io()

            while True:
                self._drain_background_job_results()
                if (
                    self._startup_editor_pending
                    and not self._has_pending_activity()
                ):
                    self._startup_editor_pending = False
                    if self.devices and self._selected() is not None:
                        if self._startup_editor == "rgb":
                            self._edit_rgb(stdscr)
                        elif self._startup_editor == "button-mapping":
                            self._edit_button_mapping(stdscr)
                        self._startup_editor = None
                        continue
                self._render(stdscr)
                key = stdscr.getch()

                if key == -1:
                    if self._drain_background_job_results():
                        continue
                    if self._run_pending_io():
                        continue
                    now = time.monotonic()
                    if (
                        self._state_refresh_retry_due_at > 0.0
                        and now >= self._state_refresh_retry_due_at
                        and not self._job_label
                        and not self._pending_state_refresh
                    ):
                        self._state_refresh_retry_due_at = 0.0
                        self._queue_state_refresh(force=True)
                        continue
                    if self._idle_refresh_interval_s > 0 and (
                        now - self._last_idle_refresh_at >= self._idle_refresh_interval_s
                    ):
                        self._last_idle_refresh_at = now
                        self._queue_state_refresh(force=False)
                    if not self._has_pending_activity():
                        self._maybe_schedule_feature_prefetch()
                    continue

                if key == ord("q"):
                    if self._has_pending_activity():
                        self._hard_exit(stdscr)
                    return 0
                if key == 27:
                    return 0
                if self._job_label and key not in _TUI_KEYS_ALLOWED_DURING_BACKGROUND_JOB:
                    self._set_status(
                        f"{self._job_label} in progress...",
                        hold_seconds=0.8,
                    )
                    continue
                if key in (ord("r"),):
                    self._queue_discovery()
                    continue
                if key in (curses.KEY_UP, ord("k")):
                    self._move_selection(-1)
                    continue
                if key in (curses.KEY_DOWN, ord("j")):
                    self._move_selection(1)
                    continue
                if key in (ord("+"), ord("=")):
                    self._adjust_dpi(int(self._dpi_adjust_step))
                    continue
                if key == ord("-"):
                    self._adjust_dpi(-int(self._dpi_adjust_step))
                    continue
                if key == ord("d"):
                    self._set_custom_dpi(stdscr)
                    continue
                if key == ord("g"):
                    self._edit_rgb(stdscr)
                    continue
                if key == ord("b"):
                    self._edit_button_mapping(stdscr)
                    continue
                if key == ord("p"):
                    self._cycle_poll_rate()
                    continue
                if key == ord("s"):
                    self._cycle_dpi_stage()
                    continue
                if key == ord("n"):
                    self._edit_dpi_levels(stdscr)
                    continue
                if key == ord("?"):
                    self._show_help(stdscr)
                    continue
                if key == ord("["):
                    self._adjust_split(-0.05)
                    continue
                if key == ord("]"):
                    self._adjust_split(+0.05)
                    continue
        finally:
            self._restore_tui_ble_defaults()


def run_tui(
    service: DeviceService,
    model_filter: Optional[str] = None,
    preselected_device_id: Optional[str] = None,
    collapse_transports: bool = True,
    startup_editor: Optional[str] = None,
) -> int:
    controller = TuiController(
        service=service,
        model_filter=model_filter,
        preselected_device_id=preselected_device_id,
        collapse_transports=collapse_transports,
        startup_editor=startup_editor,
    )

    try:
        return curses.wrapper(controller.run)
    except curses.error as exc:
        raise RazeCliError(
            "Could not start TUI. Run in a real terminal with curses support."
        ) from exc
