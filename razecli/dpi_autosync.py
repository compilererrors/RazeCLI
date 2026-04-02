"""Autosync helpers for keeping DPI stages aligned across transport modes."""

from __future__ import annotations

import os
from typing import Optional, Sequence, Tuple

from razecli.dpi_stage_presets import load_dpi_stage_preset, save_dpi_stage_preset
from razecli.errors import RazeCliError

AUTOSYNC_PRESET_PREFIX = "__autosync__:"


def autosync_enabled() -> bool:
    raw = os.environ.get("RAZECLI_AUTOSYNC", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def autosync_preset_name(model_id: Optional[str]) -> Optional[str]:
    if not model_id:
        return None
    return f"{AUTOSYNC_PRESET_PREFIX}{model_id}"


def save_autosync_settings(
    model_id: Optional[str],
    active_stage: int,
    stages: Sequence[Tuple[int, int]],
) -> bool:
    name = autosync_preset_name(model_id)
    if name is None or not stages:
        return False

    try:
        save_dpi_stage_preset(
            name=name,
            model_id=model_id,
            active_stage=int(active_stage),
            stages=stages,
        )
    except RazeCliError:
        return False
    return True


def load_autosync_settings(model_id: Optional[str]):
    name = autosync_preset_name(model_id)
    if name is None:
        return None

    try:
        return load_dpi_stage_preset(name=name)
    except RazeCliError:
        return None
