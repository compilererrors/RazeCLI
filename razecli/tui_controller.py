"""Controller orchestration for the curses TUI."""

from __future__ import annotations

import curses
from typing import List, Optional

from razecli.device_service import DeviceService
from razecli.errors import RazeCliError
from razecli.types import DetectedDevice

from razecli.tui_actions import TuiActionsMixin
from razecli.tui_types import DPI_STEP, DeviceState
from razecli.tui_view import TuiViewMixin


class TuiController(TuiViewMixin, TuiActionsMixin):
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

    def _selected(self) -> Optional[DetectedDevice]:
        if not self.devices:
            return None
        if self.selected_index < 0:
            self.selected_index = 0
        if self.selected_index >= len(self.devices):
            self.selected_index = len(self.devices) - 1
        return self.devices[self.selected_index]

    def run(self, stdscr) -> int:
        curses.curs_set(0)
        stdscr.keypad(True)

        self._refresh_devices()

        while True:
            self._render(stdscr)
            key = stdscr.getch()

            if key in (ord("q"), 27):
                return 0
            if key in (ord("r"),):
                self._refresh_devices()
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

