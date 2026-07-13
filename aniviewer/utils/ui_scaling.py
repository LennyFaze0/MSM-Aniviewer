"""Shared helpers for viewer UI scale settings."""

from __future__ import annotations

import math
import re
from typing import Optional


DEFAULT_UI_SCALE = 1.0
MIN_UI_SCALE = 0.01
MAX_UI_SCALE = 10.0
UI_SCALE_SLIDER_MIN = 0.75
UI_SCALE_SLIDER_MAX = 1.75
UI_SCALE_MANUAL_MIN_PERCENT = 1
UI_SCALE_MANUAL_MAX_PERCENT = 1000


def clamp_ui_scale(value: object, default: float = DEFAULT_UI_SCALE) -> float:
    """Return a finite UI scale factor within the supported range."""
    try:
        factor = float(value)
    except (TypeError, ValueError):
        factor = float(default)
    if not math.isfinite(factor) or factor <= 0.0:
        factor = float(default)
    return max(MIN_UI_SCALE, min(MAX_UI_SCALE, factor))


def ui_scale_percent_to_factor(value: object) -> float:
    """Convert a percentage control value into a clamped scale factor."""
    try:
        percent = int(round(float(value)))
    except (TypeError, ValueError):
        percent = 100
    return clamp_ui_scale(percent / 100.0)


def ui_scale_factor_to_percent(value: object) -> int:
    """Convert a stored scale factor into the nearest supported percent."""
    return int(round(clamp_ui_scale(value) * 100.0))


def clamp_ui_scale_percent(value: object) -> int:
    """Clamp a user-entered scale percentage to the manual override range."""
    try:
        percent = int(round(float(value)))
    except (TypeError, ValueError):
        percent = 100
    return max(UI_SCALE_MANUAL_MIN_PERCENT, min(UI_SCALE_MANUAL_MAX_PERCENT, percent))


def clamp_ui_scale_slider_percent(value: object) -> int:
    """Clamp a scale percentage to the normal slider range."""
    try:
        percent = int(round(float(value)))
    except (TypeError, ValueError):
        percent = 100
    return max(int(UI_SCALE_SLIDER_MIN * 100), min(int(UI_SCALE_SLIDER_MAX * 100), percent))


def scale_int(
    value: object,
    factor: object,
    *,
    minimum: int = 1,
    maximum: Optional[int] = None,
) -> int:
    """Scale a pixel metric and clamp it to practical integer bounds."""
    try:
        base = float(value)
    except (TypeError, ValueError):
        base = 0.0
    scaled = int(round(base * clamp_ui_scale(factor)))
    if maximum is not None:
        scaled = min(int(maximum), scaled)
    return max(int(minimum), scaled)


def scale_float(value: object, factor: object, *, minimum: float = 0.0) -> float:
    """Scale a point/pixel metric while preserving fractional values."""
    try:
        base = float(value)
    except (TypeError, ValueError):
        base = 0.0
    return max(float(minimum), base * clamp_ui_scale(factor))


_FONT_SIZE_RE = re.compile(
    r"(font-size\s*:\s*)([0-9]+(?:\.[0-9]+)?)(pt|px)",
    re.IGNORECASE,
)


def scale_stylesheet_font_sizes(stylesheet: object, factor: object) -> str:
    """Scale only font-size declarations in a Qt stylesheet."""
    text = str(stylesheet or "")
    scale = clamp_ui_scale(factor)
    if not text or abs(scale - 1.0) < 1e-6:
        return text

    def _replace(match: re.Match[str]) -> str:
        prefix, raw_value, unit = match.groups()
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return match.group(0)
        scaled = max(1.0, value * scale)
        return f"{prefix}{scaled:.2f}{unit}"

    return _FONT_SIZE_RE.sub(_replace, text)
