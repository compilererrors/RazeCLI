"""Rendering layer for the curses TUI."""

from __future__ import annotations

import curses
import textwrap
import time
from typing import List, Sequence, Tuple


class TuiViewMixin:
    def _ui_attr(self, key: str, fallback: int = 0) -> int:
        palette = getattr(self, "_palette", None)
        if isinstance(palette, dict):
            value = palette.get(key)
            if isinstance(value, int):
                return value
        return fallback

    @staticmethod
    def _spinner() -> str:
        frames = "|/-\\"
        return frames[int(time.monotonic() * 8.0) % len(frames)]

    def _status_attr(self) -> int:
        text = str(getattr(self, "status", "")).lower()
        if any(token in text for token in ("error", "failed", "invalid", "could not", "unavailable")):
            return self._ui_attr("status_error", curses.A_BOLD)
        if any(token in text for token in ("loading", "refresh", "sync", "scanning")):
            return self._ui_attr("status_warn", curses.A_BOLD)
        if any(token in text for token in ("set to", "updated", "found", "applied")):
            return self._ui_attr("status_ok", curses.A_BOLD)
        return self._ui_attr("status", 0)

    def _safe_add(self, stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        try:
            stdscr.addnstr(y, x, text, max(0, width - x - 1), attr)
        except curses.error:
            return

    def _panel_split(self, width: int) -> int:
        # Keep a symmetric split by default, but allow runtime adjustment.
        ratio = float(getattr(self, "split_ratio", 0.5))
        ratio = max(0.30, min(0.70, ratio))
        left_width = int(width * ratio)
        left_width = max(34, left_width)
        left_width = min(left_width, max(34, width - 36))
        return left_width

    @staticmethod
    def _wrap_line(line: str, width: int) -> List[str]:
        if width <= 1:
            return [line[:1]]
        wrapped = textwrap.wrap(
            str(line),
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
            drop_whitespace=False,
        )
        return wrapped or [""]

    def _draw_modal_box(
        self,
        stdscr,
        *,
        title: str,
        body_lines: Sequence[str],
        footer: str,
    ) -> Tuple[int, int, int, int, int]:
        height, width = stdscr.getmaxyx()
        max_box_w = max(44, min(width - 6, 92))
        box_w = max(44, min(max_box_w, width - 4))
        text_w = max(20, box_w - 4)

        wrapped: List[str] = []
        for line in body_lines:
            wrapped.extend(self._wrap_line(line, text_w))

        footer_lines = self._wrap_line(footer, text_w)
        max_body_rows = max(3, height - 9)
        if len(wrapped) > max_body_rows:
            wrapped = wrapped[: max_body_rows - 1] + ["..."]

        needed_h = 4 + len(wrapped) + len(footer_lines)
        box_h = max(9, min(height - 4, needed_h))
        y = max(1, (height - box_h) // 2)
        x = max(2, (width - box_w) // 2)

        # Dimmed backdrop stripe behind modal.
        for row in range(y - 1, min(height - 1, y + box_h + 1)):
            self._safe_add(stdscr, row, max(0, x - 1), " " * min(width - max(0, x - 1) - 1, box_w + 2), curses.A_DIM)

        # Modal frame.
        horiz = "-" * max(1, box_w - 2)
        self._safe_add(stdscr, y, x, f"+{horiz}+", curses.A_BOLD)
        for row in range(y + 1, y + box_h - 1):
            self._safe_add(stdscr, row, x, "|", curses.A_BOLD)
            self._safe_add(stdscr, row, x + 1, " " * max(1, box_w - 2))
            self._safe_add(stdscr, row, x + box_w - 1, "|", curses.A_BOLD)
        self._safe_add(stdscr, y + box_h - 1, x, f"+{horiz}+", curses.A_BOLD)

        title_text = f" {title.strip()} "
        if len(title_text) > box_w - 4:
            title_text = title_text[: box_w - 7] + "... "
        self._safe_add(stdscr, y, x + 2, title_text, curses.A_BOLD)

        row = y + 2
        for line in wrapped:
            if row >= y + box_h - 2:
                break
            self._safe_add(stdscr, row, x + 2, line)
            row += 1

        footer_start_y = y + box_h - 1 - len(footer_lines)
        for idx, line in enumerate(footer_lines):
            footer_y = footer_start_y + idx
            if footer_y <= y + 1:
                continue
            self._safe_add(stdscr, footer_y, x + 2, line, curses.A_DIM)

        stdscr.refresh()
        return y, x, box_h, box_w, text_w

    def _show_modal_message(
        self,
        stdscr,
        *,
        title: str,
        message: str,
        footer: str = "Press Enter to continue",
    ) -> None:
        body_lines = str(message).splitlines() or [""]
        self._draw_modal_box(
            stdscr,
            title=title,
            body_lines=body_lines,
            footer=footer,
        )
        stdscr.timeout(-1)
        try:
            while True:
                key = stdscr.getch()
                if key in (10, 13, 27, ord("q"), ord(" ")):
                    break
        finally:
            stdscr.timeout(60)

    def _draw_panel_box(
        self,
        stdscr,
        *,
        top: int,
        left: int,
        height: int,
        width: int,
        title: str,
    ) -> None:
        if height < 3 or width < 4:
            return

        border_attr = self._ui_attr("panel_border", curses.A_DIM)
        title_attr = self._ui_attr("panel_title", curses.A_BOLD)
        horizontal = "-" * max(1, width - 2)
        self._safe_add(stdscr, top, left, f"+{horizontal}+", border_attr)
        for row in range(top + 1, top + height - 1):
            self._safe_add(stdscr, row, left, "|", border_attr)
            self._safe_add(stdscr, row, left + 1, " " * max(1, width - 2))
            self._safe_add(stdscr, row, left + width - 1, "|", border_attr)
        self._safe_add(stdscr, top + height - 1, left, f"+{horizontal}+", border_attr)

        title_text = f" {title.strip()} "
        if len(title_text) > width - 4:
            title_text = title_text[: max(1, width - 7)] + "... "
        self._safe_add(stdscr, top, left + 2, title_text, title_attr)

    def _ensure_visible(self, list_height: int) -> None:
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        if self.selected_index >= self.scroll_offset + list_height:
            self.scroll_offset = self.selected_index - list_height + 1
        if self.scroll_offset < 0:
            self.scroll_offset = 0

    def _render_devices_panel(
        self,
        stdscr,
        start_y: int,
        end_y: int,
        panel_x: int,
        panel_width: int,
    ) -> None:
        list_height = max(1, end_y - start_y + 1)
        self._ensure_visible(list_height)

        visible_devices = self.devices[self.scroll_offset : self.scroll_offset + list_height]
        for row_idx, device in enumerate(visible_devices):
            actual_index = self.scroll_offset + row_idx
            marker = ">" if actual_index == self.selected_index else " "
            short_id = device.identifier
            label = f"{marker} {device.name} [{device.usb_id()}]"

            if len(label) > panel_width - 2:
                label = label[: max(0, panel_width - 5)] + "..."

            attr = self._ui_attr("selected", curses.A_REVERSE) if actual_index == self.selected_index else 0
            self._safe_add(stdscr, start_y + row_idx, panel_x, label, attr)

            if actual_index == self.selected_index:
                id_line = f"id={short_id}"
                if len(id_line) > panel_width - 2:
                    id_line = id_line[: max(0, panel_width - 5)] + "..."
                if start_y + row_idx + 1 <= end_y:
                    self._safe_add(stdscr, start_y + row_idx + 1, panel_x + 2, id_line, attr)

    def _render_details_panel(self, stdscr, x: int, start_y: int, end_y: int, panel_width: int) -> None:
        selected = self._selected()
        row = start_y

        if selected is None:
            if getattr(self, "_pending_discovery", False) or getattr(self, "_discovery_in_progress", False):
                self._safe_add(stdscr, row, x, f"Loading devices... {self._spinner()}")
                row += 1
            self._safe_add(stdscr, row, x, "No device selected")
            row += 1
            errors = self.service.backend_errors()
            if errors and row <= end_y:
                self._safe_add(stdscr, row, x, "Backend status:")
                row += 1
                for backend, error in errors.items():
                    if row > end_y:
                        break
                    line = f"{backend}: {error}"
                    if len(line) > panel_width - 2:
                        line = line[: max(0, panel_width - 5)] + "..."
                    self._safe_add(stdscr, row, x + 2, line)
                    row += 1
            return

        lines = [
            f"Name: {selected.name}",
            f"ID: {selected.identifier}",
            f"USB: {selected.usb_id()}",
            f"Model: {selected.model_id or 'unknown'}",
            f"Backend: {selected.backend}",
        ]
        loading_selected = bool(
            getattr(self, "_state_loading_device_id", None) == selected.identifier
            or (
                getattr(self, "_pending_state_refresh", False)
                and (
                    self.state.dpi is None
                    and self.state.poll_rate is None
                    and self.state.battery is None
                    and not self.state.dpi_stages
                )
            )
        )
        if loading_selected:
            lines.extend(
                [
                    f"State: loading {self._spinner()}",
                    "",
                ]
            )

        caps = ", ".join(sorted(selected.capabilities)) if selected.capabilities else "-"
        lines.append(f"Capabilities: {caps}")

        bt_008e = self._is_bt_008e(selected)
        dpi_text = "-" if self.state.dpi is None else f"{self.state.dpi[0]}:{self.state.dpi[1]}"
        if bt_008e and self.state.dpi is None:
            dpi_text = "N/A (BT limited)"
        if self.state.dpi_active_stage is None or not self.state.dpi_stages:
            dpi_profile_text = "-"
        else:
            dpi_profile_text = f"{self.state.dpi_active_stage}/{len(self.state.dpi_stages)}"
        if bt_008e and (self.state.dpi_active_stage is None or not self.state.dpi_stages):
            dpi_profile_text = "N/A (BT limited)"
        poll_text = "-" if self.state.poll_rate is None else f"{self.state.poll_rate} Hz"
        if bt_008e and self.state.poll_rate is None:
            poll_text = "N/A (BT limited)"
        battery_text = "-" if self.state.battery is None else f"{self.state.battery}%"
        if bt_008e and self.state.battery is None:
            battery_text = "N/A (BT limited)"

        lines.extend(
            [
                "",
                f"DPI: {dpi_text}",
                f"DPI profile: {dpi_profile_text}",
                f"Poll-rate: {poll_text}",
                f"Battery: {battery_text}",
            ]
        )
        if selected.model_id == "deathadder-v2-pro" and "dpi-stages" in selected.capabilities:
            lines.extend(
                [
                    "",
                    "Tip: Underside profile button can switch onboard profile bank.",
                    "Different banks can have different DPI stage lists.",
                ]
            )
        if bt_008e:
            lines.extend(
                [
                    "",
                    "Bluetooth endpoint detected (1532:008E).",
                    "Control/reads are experimental on macOS; USB/2.4 is recommended.",
                ]
            )
            if self.state.dpi_stages is not None and len(self.state.dpi_stages) <= 1:
                lines.extend(
                    [
                        "Profile bank switching from the underside button may not expose full",
                        "multi-profile tables over BLE on this host.",
                    ]
                )
        if self._is_detect_only_backend(selected):
            lines.extend(
                [
                    "",
                    "Note: macos-profiler is detect-only.",
                    "DPI/profiles/poll-rate/battery control needs a control backend.",
                ]
            )

        text_width = max(10, panel_width - 2)
        for line in lines:
            if row > end_y:
                break
            wrapped = self._wrap_line(line, text_width) if line else [""]
            for out in wrapped:
                if row > end_y:
                    break
                self._safe_add(stdscr, row, x, out)
                row += 1

    def _render(self, stdscr) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        if height < 13 or width < 74:
            self._safe_add(stdscr, 0, 0, "Terminal too small. Resize to at least 74x13.")
            self._safe_add(stdscr, 1, 0, "Press q to quit.")
            stdscr.refresh()
            return

        pad_x = 2
        content_top = 2
        content_bottom = height - 5
        panel_height = max(3, content_bottom - content_top + 1)
        gap = 2
        inner_x = pad_x
        inner_width = max(10, width - (pad_x * 2))
        split_width = max(10, inner_width - gap)
        left_width = self._panel_split(split_width)
        right_width = split_width - left_width
        if right_width < 34:
            right_width = 34
            left_width = max(34, split_width - right_width)
        if left_width < 34:
            left_width = 34
            right_width = max(34, split_width - left_width)
        left_x = inner_x
        right_x = left_x + left_width + gap

        header = "RazeCLI TUI"
        if self.model_filter:
            header += f" | model filter: {self.model_filter}"
        else:
            header += " | model filter: all"
        loading_hint = ""
        if getattr(self, "_pending_discovery", False) or getattr(self, "_discovery_in_progress", False):
            loading_hint = f" | discovering {self._spinner()}"
        elif getattr(self, "_job_label", None):
            loading_hint = f" | {str(getattr(self, '_job_label')).lower()} {self._spinner()}"
        elif getattr(self, "_pending_state_refresh", False) or getattr(self, "_state_loading_device_id", None):
            loading_hint = f" | syncing {self._spinner()}"
        self._safe_add(
            stdscr,
            0,
            pad_x,
            f"{header}{loading_hint}",
            self._ui_attr("header", curses.A_BOLD),
        )

        devices_title = f"Devices ({len(self.devices)})"
        if getattr(self, "_pending_discovery", False) or getattr(self, "_discovery_in_progress", False):
            devices_title += f" - loading {self._spinner()}"
        self._draw_panel_box(
            stdscr,
            top=content_top,
            left=left_x,
            height=panel_height,
            width=left_width,
            title=devices_title,
        )
        self._draw_panel_box(
            stdscr,
            top=content_top,
            left=right_x,
            height=panel_height,
            width=right_width,
            title="Details",
        )

        list_start_y = content_top + 1
        list_end_y = content_bottom - 1

        if self.devices:
            self._render_devices_panel(
                stdscr,
                list_start_y,
                list_end_y,
                left_x + 2,
                max(10, left_width - 4),
            )
        else:
            if getattr(self, "_pending_discovery", False) or getattr(self, "_discovery_in_progress", False):
                self._safe_add(stdscr, list_start_y, left_x + 2, f"Loading devices... {self._spinner()}")
            else:
                self._safe_add(stdscr, list_start_y, left_x + 2, "No devices")

        details_panel_width = max(10, right_width - 4)
        self._render_details_panel(
            stdscr,
            x=right_x + 2,
            start_y=list_start_y,
            end_y=list_end_y,
            panel_width=details_panel_width,
        )

        self._safe_add(
            stdscr,
            height - 4,
            pad_x,
            "-" * max(1, width - (pad_x * 2) - 1),
            self._ui_attr("panel_border", curses.A_DIM),
        )
        actions_primary = "Nav: [up/down,k/j] select  [r] refresh  [?] help  [q] quit"
        actions_secondary = "Edit: [+/-] DPI  [d] custom  [s] next profile  [n] profile count  [p] poll-rate  [[/]] split"
        self._safe_add(stdscr, height - 3, pad_x, actions_primary, self._ui_attr("footer", curses.A_BOLD))
        self._safe_add(stdscr, height - 2, pad_x, actions_secondary, self._ui_attr("footer", curses.A_BOLD))
        status_text = str(self.status)
        if getattr(self, "_job_label", None):
            status_text = f"{self._spinner()} {status_text}"
        self._safe_add(stdscr, height - 1, pad_x, f"Status: {status_text}", self._status_attr())
        stdscr.refresh()
