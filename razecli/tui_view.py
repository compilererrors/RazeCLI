"""Rendering layer for the curses TUI."""

from __future__ import annotations

import curses


class TuiViewMixin:
    def _safe_add(self, stdscr, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        try:
            stdscr.addnstr(y, x, text, max(0, width - x - 1), attr)
        except curses.error:
            return

    def _panel_split(self, width: int) -> int:
        split = max(34, width // 2)
        split = min(split, max(34, width - 26))
        return split

    def _ensure_visible(self, list_height: int) -> None:
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        if self.selected_index >= self.scroll_offset + list_height:
            self.scroll_offset = self.selected_index - list_height + 1
        if self.scroll_offset < 0:
            self.scroll_offset = 0

    def _render_devices_panel(self, stdscr, start_y: int, end_y: int, left_width: int) -> None:
        list_height = max(1, end_y - start_y + 1)
        self._ensure_visible(list_height)

        visible_devices = self.devices[self.scroll_offset : self.scroll_offset + list_height]
        for row_idx, device in enumerate(visible_devices):
            actual_index = self.scroll_offset + row_idx
            marker = ">" if actual_index == self.selected_index else " "
            short_id = device.identifier
            label = f"{marker} {device.name} [{device.usb_id()}]"

            if len(label) > left_width - 2:
                label = label[: max(0, left_width - 5)] + "..."

            attr = curses.A_REVERSE if actual_index == self.selected_index else 0
            self._safe_add(stdscr, start_y + row_idx, 0, label, attr)

            if actual_index == self.selected_index:
                id_line = f"id={short_id}"
                if len(id_line) > left_width - 2:
                    id_line = id_line[: max(0, left_width - 5)] + "..."
                if start_y + row_idx + 1 <= end_y:
                    self._safe_add(stdscr, start_y + row_idx + 1, 2, id_line, attr)

    def _render_details_panel(self, stdscr, x: int, start_y: int, end_y: int, panel_width: int) -> None:
        selected = self._selected()
        row = start_y

        if selected is None:
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
        if self._is_detect_only_backend(selected):
            lines.extend(
                [
                    "",
                    "Note: macos-profiler is detect-only.",
                    "DPI/profiles/poll-rate/battery control needs a control backend.",
                ]
            )

        for line in lines:
            if row > end_y:
                break
            out = line
            if len(out) > panel_width - 2:
                out = out[: max(0, panel_width - 5)] + "..."
            self._safe_add(stdscr, row, x, out)
            row += 1

    def _render(self, stdscr) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()

        if height < 12 or width < 70:
            self._safe_add(stdscr, 0, 0, "Terminal too small. Resize to at least 70x12.")
            self._safe_add(stdscr, 1, 0, "Press q to quit.")
            stdscr.refresh()
            return

        split_x = self._panel_split(width)

        header = "RazeCLI TUI"
        if self.model_filter:
            header += f" | model filter: {self.model_filter}"
        else:
            header += " | model filter: all"
        self._safe_add(stdscr, 0, 0, header)

        for y in range(2, height - 3):
            self._safe_add(stdscr, y, split_x, "|")

        self._safe_add(stdscr, 2, 0, f"Devices ({len(self.devices)})")
        self._safe_add(stdscr, 2, split_x + 2, "Details")

        list_start_y = 3
        list_end_y = height - 4

        if self.devices:
            self._render_devices_panel(stdscr, list_start_y, list_end_y, split_x)
        else:
            self._safe_add(stdscr, list_start_y, 0, "No devices")

        details_panel_width = width - (split_x + 2)
        self._render_details_panel(
            stdscr,
            x=split_x + 2,
            start_y=3,
            end_y=height - 4,
            panel_width=details_panel_width,
        )

        actions = (
            "Actions: [r]refresh  [up/down or k/j]select  [+/-]dpi step  "
            "[d]custom dpi  [s]next dpi profile  [n]set profile count  [p]next poll-rate  [q]quit"
        )
        self._safe_add(stdscr, height - 3, 0, actions)
        self._safe_add(stdscr, height - 1, 0, self.status)
        stdscr.refresh()

