"""Rendering layer for the curses TUI."""

from __future__ import annotations

import curses
import textwrap
import time
from typing import List, Optional, Sequence, Tuple


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

    @staticmethod
    def _loading_dots() -> str:
        return "." * (1 + (int(time.monotonic() * 3.0) % 3))

    def _loading_label(self) -> str:
        dots = self._loading_dots()
        return f"Loading{dots:<3}"

    def _busy_label(self) -> str:
        job_label = str(getattr(self, "_job_label", "") or "").strip()
        if job_label:
            return job_label
        if getattr(self, "_pending_discovery", False) or getattr(self, "_discovery_in_progress", False):
            return "Refreshing devices"
        if getattr(self, "_pending_state_refresh", False) or getattr(self, "_state_loading_device_id", None):
            return "Refreshing device state"
        return ""

    def _is_busy(self) -> bool:
        return bool(self._busy_label())

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
        dim_full_screen: bool = True,
    ) -> Tuple[int, int, int, int, int]:
        height, width = stdscr.getmaxyx()
        max_box_w = max(50, min(width - 6, 104))
        box_w = max(50, min(max_box_w, width - 4))
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

        backdrop_attr = self._ui_attr("modal_backdrop", curses.A_DIM)
        border_attr = self._ui_attr("modal_border", curses.A_BOLD)
        title_attr = self._ui_attr("modal_title", curses.A_BOLD)
        text_attr = self._ui_attr("modal_text", 0)
        footer_attr = self._ui_attr("modal_footer", curses.A_DIM)
        shadow_attr = self._ui_attr("modal_shadow", curses.A_DIM)

        # Full-screen dim backdrop (first paint). Skipping on repeats avoids O(screen)
        # work on every arrow key in `_select_menu`.
        if dim_full_screen:
            fill = " " * max(1, width - 1)
            for row in range(0, max(0, height - 1)):
                self._safe_add(stdscr, row, 0, fill, backdrop_attr)

        # Subtle drop shadow around the modal.
        shadow_x = x + box_w
        if shadow_x < width - 1:
            for row in range(y + 1, min(height - 1, y + box_h + 1)):
                self._safe_add(stdscr, row, shadow_x, " ", shadow_attr)
        shadow_y = y + box_h
        if shadow_y < height - 1:
            shadow_w = max(1, min(box_w, width - x - 1))
            self._safe_add(stdscr, shadow_y, x + 1, " " * shadow_w, shadow_attr)

        # Modal frame.
        horiz = "-" * max(1, box_w - 2)
        self._safe_add(stdscr, y, x, f"+{horiz}+", border_attr)
        for row in range(y + 1, y + box_h - 1):
            self._safe_add(stdscr, row, x, "|", border_attr)
            self._safe_add(stdscr, row, x + 1, " " * max(1, box_w - 2))
            self._safe_add(stdscr, row, x + box_w - 1, "|", border_attr)
        self._safe_add(stdscr, y + box_h - 1, x, f"+{horiz}+", border_attr)

        title_text = f" {title.strip()} "
        if len(title_text) > box_w - 4:
            title_text = title_text[: box_w - 7] + "... "
        self._safe_add(stdscr, y, x + 2, title_text, title_attr)

        row = y + 2
        for line in wrapped:
            if row >= y + box_h - 2:
                break
            self._safe_add(stdscr, row, x + 2, line, text_attr)
            row += 1

        footer_start_y = y + box_h - 1 - len(footer_lines)
        for idx, line in enumerate(footer_lines):
            footer_y = footer_start_y + idx
            if footer_y <= y + 1:
                continue
            self._safe_add(stdscr, footer_y, x + 2, line, footer_attr)

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
                    f"State: loading{self._loading_dots()}",
                    "",
                ]
            )

        caps = ", ".join(sorted(selected.capabilities)) if selected.capabilities else "-"
        lines.append(f"Capabilities: {caps}")

        bt_endpoint = self._is_ble_endpoint_device(selected)
        dpi_text = "-" if self.state.dpi is None else f"{self.state.dpi[0]}:{self.state.dpi[1]}"
        if bt_endpoint and self.state.dpi is None:
            dpi_text = "N/A (BT limited)"
        if self.state.dpi_active_stage is None or not self.state.dpi_stages:
            dpi_profile_text = "-"
        else:
            dpi_profile_text = f"{self.state.dpi_active_stage}/{len(self.state.dpi_stages)}"
        if bt_endpoint and (self.state.dpi_active_stage is None or not self.state.dpi_stages):
            dpi_profile_text = "N/A (BT limited)"
        poll_supported = "poll-rate" in selected.capabilities
        poll_text = "-" if self.state.poll_rate is None else f"{self.state.poll_rate} Hz"
        if self.state.poll_rate is None:
            if bt_endpoint:
                poll_text = "N/A (use USB/2.4 for poll-rate)"
            elif poll_supported and not loading_selected:
                poll_text = "Unavailable"
        battery_text = "-" if self.state.battery is None else f"{self.state.battery}%"
        if bt_endpoint and self.state.battery is None:
            battery_text = "N/A (BT limited)"

        rgb_cache = getattr(self, "_rgb_cache", None)
        rgb_state = None
        if isinstance(rgb_cache, dict):
            rgb_state = rgb_cache.get(selected.identifier) or rgb_cache.get(str(selected.identifier))
        rgb_confidence_label: Optional[str] = None
        rgb_loading = False
        if "rgb" in selected.capabilities:
            inflight_checker = getattr(self, "_is_feature_prefetch_inflight", None)
            if callable(inflight_checker):
                try:
                    rgb_loading = bool(inflight_checker(selected.identifier, "rgb"))
                except Exception:
                    rgb_loading = False
        if isinstance(rgb_state, dict):
            rgb_mode = str(rgb_state.get("mode") or "-")
            rgb_brightness = rgb_state.get("brightness")
            rgb_color = str(rgb_state.get("color") or "------")
            confidence = rgb_state.get("read_confidence")
            if isinstance(confidence, dict):
                overall = str(confidence.get("overall") or "").strip().lower()
                if overall:
                    rgb_confidence_label = overall
            if isinstance(rgb_brightness, int):
                rgb_text = f"{rgb_mode} {rgb_brightness}% #{rgb_color}"
            else:
                rgb_text = f"{rgb_mode} #{rgb_color}"
            if rgb_confidence_label:
                rgb_text = f"{rgb_text} [{rgb_confidence_label}]"
        elif rgb_loading:
            rgb_text = f"{self._loading_label()} (press g to open)"
        elif "rgb" in selected.capabilities:
            rgb_text = "Not loaded (press g)"
        else:
            rgb_text = "-"

        button_cache = getattr(self, "_button_mapping_cache", None)
        button_state = None
        if isinstance(button_cache, dict):
            button_state = button_cache.get(selected.identifier) or button_cache.get(str(selected.identifier))
        button_confidence_label: Optional[str] = None
        button_loading = False
        if "button-mapping" in selected.capabilities:
            inflight_checker = getattr(self, "_is_feature_prefetch_inflight", None)
            if callable(inflight_checker):
                try:
                    button_loading = bool(inflight_checker(selected.identifier, "button-mapping"))
                except Exception:
                    button_loading = False
        button_text = "-"
        if isinstance(button_state, dict):
            mapping = button_state.get("mapping")
            confidence = button_state.get("read_confidence")
            if isinstance(confidence, dict):
                overall = str(confidence.get("overall") or "").strip().lower()
                if overall:
                    button_confidence_label = overall
            if isinstance(mapping, dict) and mapping:
                side_1 = str(mapping.get("side_1") or "-")
                side_2 = str(mapping.get("side_2") or "-")
                button_text = f"side_1={side_1}, side_2={side_2}"
                if button_confidence_label:
                    button_text = f"{button_text} [{button_confidence_label}]"
            elif button_confidence_label:
                button_text = f"- [{button_confidence_label}]"
        elif button_loading:
            button_text = f"{self._loading_label()} (press b to open)"
        elif "button-mapping" in selected.capabilities:
            button_text = "Not loaded (press b)"

        lines.extend(
            [
                "",
                f"DPI: {dpi_text}",
                f"DPI profile: {dpi_profile_text}",
                f"Poll-rate: {poll_text}",
                f"Battery: {battery_text}",
                f"RGB: {rgb_text}",
                f"Buttons: {button_text}",
            ]
        )
        if (
            self._has_onboard_profile_bank_switch(selected)
            and "dpi-stages" in selected.capabilities
            and bt_endpoint
            and self.state.onboard_bank_signature
        ):
            sig = str(self.state.onboard_bank_signature).strip()
            match = str(self.state.onboard_bank_match or "").strip()
            if match:
                bank_line = f"Onboard profile: matches snapshot(s) {match} (fp={sig})"
            else:
                bank_line = (
                    f"Onboard profile fp={sig} (no snapshot match — "
                    f"switch underside bank + ble bank-snapshot --label …)"
                )
            lines.extend(
                [
                    "",
                    bank_line,
                    "Edits here apply to the onboard bank selected on the mouse (underside switch), not a software-only mode.",
                ]
            )
        elif self._has_onboard_profile_bank_switch(selected) and "dpi-stages" in selected.capabilities:
            lines.extend(
                [
                    "",
                    "Tip: Underside profile button switches onboard bank; BLE DPI reads/writes use that active bank.",
                    "Save `ble bank-snapshot --label …` per bank; TUI matches fp when the BLE table matches a snapshot.",
                ]
            )
        if bt_endpoint:
            pid_label = self._usb_pid_label(selected)
            lines.extend(
                [
                    "",
                    f"Bluetooth endpoint detected ({pid_label}).",
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
        busy_label = self._busy_label()
        if busy_label:
            loading_hint = f" | {busy_label.lower()}"
        self._safe_add(
            stdscr,
            0,
            pad_x,
            f"{header}{loading_hint}",
            self._ui_attr("header", curses.A_BOLD),
        )

        devices_title = f"Devices ({len(self.devices)})"
        if getattr(self, "_pending_discovery", False) or getattr(self, "_discovery_in_progress", False):
            devices_title += " - loading..."
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
                self._safe_add(stdscr, list_start_y, left_x + 2, "Loading devices...")
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
        actions_primary = "Nav: [up/down,k/j] select  [r] refresh  [g] RGB  [b] buttons  [?] help  [q] quit"
        selected = self._selected()
        poll_hint = "  [p] poll-rate" if bool(selected and "poll-rate" in selected.capabilities) else ""
        dpi_step = max(1, int(getattr(self, "_dpi_adjust_step", 100)))
        actions_secondary = (
            f"Edit: [+/-] DPI({dpi_step})  [d] custom  [s] next profile  [n] DPI levels{poll_hint}  [[/]] split"
        )
        self._safe_add(stdscr, height - 3, pad_x, actions_primary, self._ui_attr("footer", curses.A_BOLD))
        self._safe_add(stdscr, height - 2, pad_x, actions_secondary, self._ui_attr("footer", curses.A_BOLD))
        status_text = str(self.status)
        busy_label = self._busy_label()
        if busy_label:
            status_raw = status_text.strip()
            busy_text = f"{busy_label}{self._loading_dots()}"
            if status_raw.lower().startswith(busy_label.lower()):
                tail = status_raw[len(busy_label) :].strip().lstrip(". ").strip()
                if tail:
                    status_text = f"{self._spinner()} {busy_text} | {tail}"
                else:
                    status_text = f"{self._spinner()} {busy_text}"
            elif status_raw:
                status_text = f"{self._spinner()} {busy_text} | {status_raw}"
            else:
                status_text = f"{self._spinner()} {busy_text}"
        self._safe_add(stdscr, height - 1, pad_x, f"Status: {status_text}", self._status_attr())
        stdscr.refresh()
