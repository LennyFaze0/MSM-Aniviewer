"""Helpers for BIN revision auto-detection and converter ordering."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import struct
from typing import Dict, List, Optional

LEGACY_FAMILY = "legacy"
MODERN_FAMILY = "modern"

CONVERTER_DISPLAY_NAMES: Dict[str, str] = {
    "rev6": "Rev6",
    "rev5": "Rev5",
    "rev4": "Rev4",
    "rev3": "Rev3",
    "rev2": "Rev2",
    "rev1": "Rev1 (Legacy Mobile)",
    "rev1_classic": "Rev1",
    "rev1_launch": "Rev1",
    "muppets": "Rev2 (My Muppets Show)",
    "choir": "Rev3",
    # Backward-compatible alias.
    "oldest": "Rev1",
    "legacy": "Rev1 (Legacy Mobile)",
}

SOURCE_FORMAT_REVISION_HINTS: Dict[str, int] = {
    "rev1": 1,
    "rev1_classic": 1,
    "rev1_launch": 1,
    "rev1_launch_build": 1,
    "oldest": 1,
    "legacy": 1,
    "rev1_legacy": 1,
    "rev2": 2,
    "muppets": 2,
    "rev2_muppets": 2,
    "rev3": 3,
    "choir": 3,
    "rev4": 4,
    "rev5": 5,
    "rev6": 6,
}

SOURCE_FORMAT_REVISION_LABELS: Dict[str, str] = {
    "rev1_classic": "Rev 1",
    "rev1_launch": "Rev 1",
    "rev1_launch_build": "Rev 1",
    "oldest": "Rev 1",
    "legacy": "Rev 1 (Legacy Mobile)",
    "rev1_legacy": "Rev 1 (Legacy Mobile)",
    "muppets": "Rev 2 (My Muppets Show)",
    "rev2_muppets": "Rev 2 (My Muppets Show)",
    "choir": "Rev 3",
}


def converter_display_name(key: str) -> str:
    """Return a user-facing converter/parser label."""
    return CONVERTER_DISPLAY_NAMES.get(key, key)


def source_format_revision_hint(source_format: Optional[str]) -> Optional[int]:
    """Map source-format metadata to the historical BIN revision."""
    if not source_format:
        return None
    return SOURCE_FORMAT_REVISION_HINTS.get(source_format.strip().lower())


def source_format_revision_label(source_format: Optional[str]) -> Optional[str]:
    """Return a friendly revision label for source-format metadata when known."""
    if not source_format:
        return None
    key = source_format.strip().lower()
    if key in SOURCE_FORMAT_REVISION_LABELS:
        return SOURCE_FORMAT_REVISION_LABELS[key]
    hint = SOURCE_FORMAT_REVISION_HINTS.get(key)
    if hint is not None:
        return f"Rev {hint}"
    return None


@dataclass(frozen=True)
class BinDetectionHints:
    normalized_path: str
    bin_name: str
    revision_hint: Optional[int]
    family_hint: Optional[str]
    is_muppet_bin: bool
    is_muppets_payload: bool
    is_composer_bin: bool
    is_choir_payload: bool
    is_oldest_payload: bool


def infer_revision_hint(normalized_path: str, bin_name: str = "") -> Optional[int]:
    """Infer a likely revision from path/name hints."""
    text = normalized_path.lower()
    name = bin_name.lower()

    match = re.search(r"\brev\s*[-_ ]?(\d+)\b", text)
    if match:
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            pass

    if "monster choir" in text or "monsterchoirandroid" in text:
        return 3

    if (
        "my singing monsters oldest.app" in text
        or "my singing monsters102.app" in text
        or "my singing monsters 1.0" in text
    ):
        return 1

    if "my singing monsters 1.2" in text:
        return 2

    if "my singing monsters 5." in text:
        return 6

    if "my singing monsters 4." in text:
        return 5

    if "my singing monsers 3." in text or "my singing monsters 3." in text:
        return 4

    if "my muppet show" in text or "my singing muppets" in text:
        return 2

    if "composer" in text or "_composer" in name:
        return 4

    return None


def sniff_bin_family(bin_path: str) -> Optional[str]:
    """Return ``modern`` only when the header is clearly not versioned legacy."""
    try:
        with open(bin_path, "rb") as handle:
            raw = handle.read(4)
    except OSError:
        return None

    if len(raw) < 4:
        return None

    try:
        first_u32 = struct.unpack_from("<I", raw, 0)[0]
    except struct.error:
        return None

    # Most legacy-derived bins start with version=1. Any other value is a safe
    # modern indicator, but 1 is ambiguous across multiple revisions.
    if first_u32 != 1:
        return MODERN_FAMILY
    return None


def collect_bin_detection_hints(bin_path: str) -> BinDetectionHints:
    normalized_path = os.path.normcase(os.path.normpath(bin_path))
    bin_name = os.path.basename(bin_path).lower()
    normalized_lower = normalized_path.lower()
    return BinDetectionHints(
        normalized_path=normalized_path,
        bin_name=bin_name,
        revision_hint=infer_revision_hint(normalized_path, bin_name),
        family_hint=sniff_bin_family(bin_path),
        is_muppet_bin=("muppet_" in bin_name),
        is_muppets_payload=(
            "my singing muppets" in normalized_lower
            or "my muppets show" in normalized_lower
            or "my muppet show" in normalized_lower
        ),
        is_composer_bin="_composer" in bin_name,
        is_choir_payload=(
            "monster choir" in normalized_lower
            or "monsterchoirandroid" in normalized_lower
        ),
        is_oldest_payload=(
            "my singing monsters oldest.app" in normalized_lower
            or "my singing monsters102.app" in normalized_lower
        ),
    )


def build_auto_converter_order(hints: BinDetectionHints) -> List[str]:
    """Build converter order from strongest hints to broad fallbacks."""
    preferred_order: List[str] = []
    queued = set()

    def queue(key: str) -> None:
        if key in queued:
            return
        queued.add(key)
        preferred_order.append(key)

    revision_hint = hints.revision_hint

    # Strong path/name signals first.
    if hints.is_oldest_payload or revision_hint == 1:
        queue("rev1_classic")
    if hints.is_choir_payload or revision_hint == 3:
        queue("choir")
    if hints.is_composer_bin or revision_hint == 4:
        queue("rev4")
    if revision_hint == 2:
        queue("rev2")
    if revision_hint == 5:
        queue("rev5")
        queue("rev6")
    if revision_hint == 6:
        queue("rev6")
        queue("rev5")
    if hints.is_muppets_payload:
        queue("rev2")
        queue("muppets")
    elif hints.is_muppet_bin:
        queue("muppets")

    # Header-informed family preference.
    if hints.family_hint == MODERN_FAMILY:
        for key in ("rev6", "rev5", "rev4", "rev2", "rev1_classic", "legacy", "choir", "muppets"):
            queue(key)

    # Stable final fallback chain.
    for key in ("rev6", "rev5", "rev4", "rev2", "rev1_classic", "legacy", "choir", "muppets"):
        queue(key)

    return preferred_order
