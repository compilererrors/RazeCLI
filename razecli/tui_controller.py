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
        self._state_cache: dict[str, DeviceState] = {}
        self._state_refreshed_at: dict[str, float] = {}
        self._battery_refreshed_at: dict[str, float] = {}
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
    ) -> bool:
        if self._job_label:
            self._set_status(
                f"{self._job_label} in progress... {self._spinner()}",
                hold_seconds=0.8,
            )
            return False

        self._job_label = str(label).strip() or "Working"
        self._set_status(f"{self._job_label}... {self._spinner()}", hold_seconds=0.4)

        def _runner() -> None:
            try:
                result = work()
                self._job_results.put((True, result, on_success, on_error))
            except Exception as exc:  # pragma: no cover - runtime/hardware dependent
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
            self._job_label = None
            if ok:
                if on_success is not None:
                    try:
                        on_success(payload)
                    except Exception as exc:  # pragma: no cover - defensive
                        self._set_status(f"Post-action failed: {exc}", hold_seconds=8.0)
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
            )
            if not started:
                self._pending_discovery = True
                return False
            return True

        if self._pending_state_refresh:
            force = bool(self._pending_state_force)
            self._pending_state_refresh = False
            self._pending_state_force = False

            def _on_state_error(exc: Exception) -> None:
                self._set_status(f"State refresh failed: {exc}", hold_seconds=8.0)

            started = self._start_background_job(
                label="Refreshing device state",
                work=lambda: self._refresh_state(force=force),
                on_error=_on_state_error,
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
            "RAZECLI_BLE_DPI_READ_ATTEMPTS": "1",
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
            "  + / -: Adjust DPI step\n"
            "  d: Set custom DPI\n"
            "  s: Next DPI profile\n"
            "  n: DPI profile editor\n"
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
                self._render(stdscr)
                key = stdscr.getch()

                if key == -1:
                    if self._drain_background_job_results():
                        continue
                    if self._run_pending_io():
                        continue
                    now = time.monotonic()
                    if self._idle_refresh_interval_s > 0 and (
                        now - self._last_idle_refresh_at >= self._idle_refresh_interval_s
                    ):
                        self._last_idle_refresh_at = now
                        self._queue_state_refresh(force=False)
                    continue

                if key == ord("q"):
                    if self._has_pending_activity():
                        self._hard_exit(stdscr)
                    return 0
                if key == 27:
                    return 0
                if self._job_label and key not in (
                    curses.KEY_UP,
                    ord("k"),
                    curses.KEY_DOWN,
                    ord("j"),
                    ord("?"),
                    ord("["),
                    ord("]"),
                ):
                    self._set_status(
                        f"{self._job_label} in progress... {self._spinner()}",
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
                    self._adjust_dpi(DPI_STEP)
                    continue
                if key == ord("-"):
                    self._adjust_dpi(-DPI_STEP)
                    continue
                if key == ord("d"):
                    self._set_custom_dpi(stdscr)
                    continue
                if key == ord("p"):
                    self._cycle_poll_rate()
                    continue
                if key == ord("s"):
                    self._cycle_dpi_stage()
                    continue
                if key == ord("n"):
                    self._set_dpi_profile_count(stdscr)
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
) -> int:
    controller = TuiController(
        service=service,
        model_filter=model_filter,
        preselected_device_id=preselected_device_id,
        collapse_transports=collapse_transports,
    )

    try:
        return curses.wrapper(controller.run)
    except curses.error as exc:
        raise RazeCliError(
            "Could not start TUI. Run in a real terminal with curses support."
        ) from exc
