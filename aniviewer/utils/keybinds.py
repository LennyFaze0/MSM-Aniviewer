"""
Keybind definitions and helpers.
"""

from dataclasses import dataclass
from typing import Dict, List

from PyQt6.QtGui import QKeySequence


@dataclass(frozen=True)
class KeybindAction:
    key: str
    label: str
    default: str
    group: str
    description: str = ""


_KEYBIND_ACTIONS: List[KeybindAction] = [
    KeybindAction(
        key="toggle_playback",
        label="Play / Pause",
        default="Space",
        group="Playback",
        description="Toggle animation playback.",
    ),
    KeybindAction(
        key="toggle_loop",
        label="Toggle Loop",
        default="",
        group="Playback",
        description="Toggle looping on the current animation.",
    ),
    KeybindAction(
        key="record_pose",
        label="Record Pose",
        default="R",
        group="Keyframes",
        description="Record the current pose to the active keyframe lane.",
    ),
    KeybindAction(
        key="undo",
        label="Undo",
        default="Ctrl+Z",
        group="Edit",
        description="Undo the most recent edit action.",
    ),
    KeybindAction(
        key="redo",
        label="Redo",
        default="Ctrl+Y",
        group="Edit",
        description="Redo the most recently undone edit action.",
    ),
    KeybindAction(
        key="redo_alt",
        label="Redo (Alt)",
        default="Ctrl+Shift+Z",
        group="Edit",
        description="Alternative redo shortcut.",
    ),
    KeybindAction(
        key="copy_keyframes",
        label="Copy Keyframes",
        default="Ctrl+C",
        group="Edit",
        description="Copy selected keyframes.",
    ),
    KeybindAction(
        key="paste_keyframes",
        label="Paste Keyframes",
        default="Ctrl+V",
        group="Edit",
        description="Paste copied keyframes.",
    ),
    KeybindAction(
        key="move_tool",
        label="Move Tool",
        default="V",
        group="Tools",
        description="Activate the move gizmo.",
    ),
    KeybindAction(
        key="free_transform",
        label="Free Transform",
        default="Ctrl+T",
        group="Tools",
        description="Activate the combined scale + rotation gizmos.",
    ),
    KeybindAction(
        key="toggle_rotation_gizmo",
        label="Toggle Rotation Gizmo",
        default="",
        group="Tools",
        description="Enable/disable the rotation gizmo.",
    ),
    KeybindAction(
        key="toggle_scale_gizmo",
        label="Toggle Scale Gizmo",
        default="",
        group="Tools",
        description="Enable/disable the scale gizmo.",
    ),
    KeybindAction(
        key="fullscreen",
        label="Fullscreen",
        default="F11",
        group="View",
        description="Toggle fullscreen mode.",
    ),
    KeybindAction(
        key="toggle_controls_panel",
        label="Toggle Controls Panel",
        default="",
        group="Panels",
        description="Show or hide the controls panel.",
    ),
    KeybindAction(
        key="toggle_layers_panel",
        label="Toggle Layer Panel",
        default="",
        group="Panels",
        description="Show or hide the layer list.",
    ),
    KeybindAction(
        key="toggle_timeline_panel",
        label="Toggle Timeline",
        default="",
        group="Panels",
        description="Show or hide the timeline panel.",
    ),
    KeybindAction(
        key="toggle_log_panel",
        label="Toggle Log",
        default="",
        group="Panels",
        description="Show or hide the log panel.",
    ),
    KeybindAction(
        key="toggle_focus_mode",
        label="Toggle Focus Mode",
        default="",
        group="Panels",
        description="Toggle focus mode (hide side panels).",
    ),
]


def keybind_actions() -> List[KeybindAction]:
    return list(_KEYBIND_ACTIONS)


def default_keybinds() -> Dict[str, str]:
    return {action.key: action.default for action in _KEYBIND_ACTIONS}


def normalize_keybind_sequence(sequence: str) -> str:
    text = (sequence or "").strip()
    if not text:
        return ""
    normalized = QKeySequence(text).toString(QKeySequence.SequenceFormat.PortableText)
    return normalized.strip()
