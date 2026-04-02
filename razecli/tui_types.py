"""Shared TUI state and constants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

DPI_STEP = 100
MAX_DPI_STAGES = 5


@dataclass
class DeviceState:
    dpi: Optional[Tuple[int, int]] = None
    dpi_active_stage: Optional[int] = None
    dpi_stages: Optional[List[Tuple[int, int]]] = None
    poll_rate: Optional[int] = None
    battery: Optional[int] = None

