"""
MSM MIDI Editor Dialog
Focused on timing/segment edits for game MIDI files.
"""

import os
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor, Future
import re
import time
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass
from collections import OrderedDict
from typing import Optional, List, Tuple, Iterable, Dict

import numpy as np
import soundfile as sf
from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QRect, QSize, QTimer, QEvent, QCoreApplication, QEventLoop
from PyQt6.QtGui import QPainter, QColor, QPen, QFontMetrics, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QCheckBox,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QGridLayout,
    QSplitter,
    QDoubleSpinBox,
    QMessageBox,
    QWidget,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QComboBox,
)

from renderer.opengl_widget import OpenGLAnimationWidget
from ui.monster_browser_dialog import ThumbnailLoader
from core.audio_manager import AudioManager, MultiTrackAudioMixer
from core.animation_player import AnimationPlayer
from renderer.sprite_renderer import SpriteRenderer
from utils.midi_utils import MidiFileData, MidiNote, read_midi_file, write_midi_file
from utils.ffmpeg_installer import resolve_ffmpeg_path

LANE_HEIGHT = 28
HEADER_HEIGHT = 22
NAME_COL_WIDTH = 220


def _apply_global_lane_delta_world_states(
    layer_world_states: dict,
    player: AnimationPlayer,
    time_value: float,
    *,
    position_scale: float = 1.0,
    base_world_scale: float = 1.0,
) -> None:
    if not layer_world_states or not player or not player.animation:
        return
    global_delta = player.get_global_lane_delta(time_value)
    if not global_delta:
        return
    pos_x = float(global_delta.get("pos_x", 0.0) or 0.0)
    pos_y = float(global_delta.get("pos_y", 0.0) or 0.0)
    rotation = float(global_delta.get("rotation", 0.0) or 0.0)
    scale_x = float(global_delta.get("scale_x", 0.0) or 0.0)
    scale_y = float(global_delta.get("scale_y", 0.0) or 0.0)
    depth = float(global_delta.get("depth", 0.0) or 0.0)
    opacity_delta = float(global_delta.get("opacity", 0.0) or 0.0)
    has_sprite = bool(global_delta.get("has_sprite", False))
    sprite_name = global_delta.get("sprite_name") if has_sprite else None
    has_rgb = bool(global_delta.get("has_rgb", False))
    rgb_r = global_delta.get("r")
    rgb_g = global_delta.get("g")
    rgb_b = global_delta.get("b")
    rgb_a = global_delta.get("a")

    epsilon = 1e-6
    has_transform = (
        abs(pos_x) > epsilon
        or abs(pos_y) > epsilon
        or abs(rotation) > epsilon
        or abs(scale_x) > epsilon
        or abs(scale_y) > epsilon
    )
    if not (has_transform or abs(depth) > epsilon or abs(opacity_delta) > epsilon or has_sprite or has_rgb):
        return

    pos_x *= base_world_scale * position_scale
    pos_y *= base_world_scale * position_scale

    for state in layer_world_states.values():
        if has_transform:
            m00 = state.get("m00", 1.0)
            m01 = state.get("m01", 0.0)
            m10 = state.get("m10", 0.0)
            m11 = state.get("m11", 1.0)
            state["m00"] = m00 + (scale_x / 100.0) * m00
            state["m01"] = m01 + (scale_x / 100.0) * m01
            state["m10"] = m10 + (scale_y / 100.0) * m10
            state["m11"] = m11 + (scale_y / 100.0) * m11
            if rotation:
                rot = math.radians(rotation)
                cos_r = math.cos(rot)
                sin_r = math.sin(rot)
                nm00 = state["m00"] * cos_r + state["m01"] * -sin_r
                nm01 = state["m00"] * sin_r + state["m01"] * cos_r
                nm10 = state["m10"] * cos_r + state["m11"] * -sin_r
                nm11 = state["m10"] * sin_r + state["m11"] * cos_r
                state["m00"] = nm00
                state["m01"] = nm01
                state["m10"] = nm10
                state["m11"] = nm11
            state["tx"] = state.get("tx", 0.0) + pos_x
            state["ty"] = state.get("ty", 0.0) + pos_y
        if abs(depth) > epsilon:
            state["depth"] = state.get("depth", 0.0) + depth
        if abs(opacity_delta) > epsilon:
            state["world_opacity"] = max(
                0.0, min(1.0, state.get("world_opacity", 1.0) + (opacity_delta / 100.0))
            )
        if has_sprite and sprite_name:
            state["sprite_name"] = sprite_name
        if has_rgb:
            if rgb_r is not None:
                state["r"] = int(rgb_r)
            if rgb_g is not None:
                state["g"] = int(rgb_g)
            if rgb_b is not None:
                state["b"] = int(rgb_b)
            if rgb_a is not None:
                state["a"] = int(rgb_a)


def _compute_world_states_worker(
    animation: "AnimationData",
    time_value: float,
    atlases: list,
    renderer_state: dict,
) -> dict:
    renderer = SpriteRenderer()
    for key, value in renderer_state.items():
        try:
            setattr(renderer, key, value)
        except Exception:
            pass
    player = AnimationPlayer()
    player.animation = animation
    layer_map = {layer.layer_id: layer for layer in animation.layers}
    world_states: dict = {}
    for layer in animation.layers:
        state = renderer.calculate_world_state(
            layer,
            time_value,
            player,
            layer_map,
            world_states,
            atlases,
            None,
            None,
        )
        world_states[layer.layer_id] = state
    _apply_global_lane_delta_world_states(
        world_states,
        player,
        time_value,
        position_scale=renderer.position_scale,
        base_world_scale=renderer.base_world_scale,
    )
    return world_states


def _compute_world_states_sequence_worker(
    animation: "AnimationData",
    duration: float,
    fps: int,
    atlases: list,
    renderer_state: dict,
) -> List[dict]:
    if not animation or duration <= 0.0 or fps <= 0:
        return []
    renderer = SpriteRenderer()
    for key, value in renderer_state.items():
        try:
            setattr(renderer, key, value)
        except Exception:
            pass
    player = AnimationPlayer()
    player.animation = animation
    layer_map = {layer.layer_id: layer for layer in animation.layers}
    frame_count = max(1, int(math.ceil(duration * fps)))
    states_list: List[dict] = []
    for frame_idx in range(frame_count):
        time_value = frame_idx / float(fps)
        world_states: dict = {}
        for layer in animation.layers:
            state = renderer.calculate_world_state(
                layer,
                time_value,
                player,
                layer_map,
                world_states,
                atlases,
                None,
                None,
            )
            world_states[layer.layer_id] = state
        _apply_global_lane_delta_world_states(
            world_states,
            player,
            time_value,
            position_scale=renderer.position_scale,
            base_world_scale=renderer.base_world_scale,
        )
        states_list.append(world_states)
    return states_list


@dataclass
class PreviewSegmentBake:
    start_beats: float
    end_beats: float
    anim_name: str
    animation: Optional[object]
    atlases: list
    duration: float
    audio_path: Optional[str]
    audio_cache: Optional[Tuple[object, int, float]]


@dataclass
class PreviewTrackState:
    track_index: int
    track_name: str
    widget: OpenGLAnimationWidget
    segments: List[Tuple[float, float, str]]
    baked_segments: List[PreviewSegmentBake]
    current_anim_name: Optional[str] = None
    active_segment: Optional[Tuple[int, float, float]] = None
    audio_segment_idx: Optional[int] = None
    idle_anim_name: Optional[str] = None
    track_has_notes: bool = False


class MidiTrackListCanvas(QWidget):
    trackSelected = pyqtSignal(int)
    trackVisibilityToggled = pyqtSignal(int, bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._midi: Optional[MidiFileData] = None
        self._selected_track: Optional[int] = None
        self._track_indices: List[int] = []
        self._track_display_names: Dict[int, str] = {}
        self._loading_progress: dict = {}
        self._hidden_tracks: set = set()
        self._portrait_paths: Dict[int, str] = {}
        self._portrait_pixmaps: Dict[str, QPixmap] = {}
        self._thumbnail_loader: Optional[ThumbnailLoader] = None
        self._portrait_size: int = max(14, LANE_HEIGHT - 8)
        self.setFixedWidth(NAME_COL_WIDTH)
        self.setMinimumHeight(160)

    def set_midi(self, midi: Optional[MidiFileData]) -> None:
        self._midi = midi
        self._selected_track = None
        self._track_indices = []
        self._track_display_names = {}
        self._loading_progress.clear()
        self._recompute_size()
        self.update()

    def set_selected_track(self, index: Optional[int]) -> None:
        self._selected_track = index
        self.update()

    def set_track_indices(self, indices: Optional[List[int]]) -> None:
        self._track_indices = list(indices or [])
        self._recompute_size()
        self.update()

    def set_track_display_names(self, names: Optional[Dict[int, str]]) -> None:
        self._track_display_names = dict(names or {})
        self.update()

    def set_hidden_tracks(self, hidden: Optional[Iterable[int]]) -> None:
        self._hidden_tracks = set(hidden or [])
        self.update()

    def set_thumbnail_loader(self, loader: Optional[ThumbnailLoader]) -> None:
        self._thumbnail_loader = loader

    def set_portrait_paths(self, paths: Optional[Dict[int, str]]) -> None:
        self._portrait_paths = dict(paths or {})
        self.update()

    def set_portrait_pixmap(self, path: str, pixmap: QPixmap) -> None:
        if not path:
            return
        key = os.path.normcase(os.path.abspath(path))
        self._portrait_pixmaps[key] = pixmap
        self.update()

    def set_track_loading(self, track_index: int, progress: Optional[float]) -> None:
        if progress is None:
            if track_index in self._loading_progress:
                self._loading_progress.pop(track_index, None)
                self.update()
            return
        clamped = max(0.0, min(1.0, float(progress)))
        self._loading_progress[track_index] = clamped
        self.update()

    def _visible_tracks(self) -> List[int]:
        if not self._midi:
            return []
        if self._track_indices:
            return [idx for idx in self._track_indices if 0 <= idx < len(self._midi.tracks)]
        return list(range(len(self._midi.tracks)))

    def _recompute_size(self) -> None:
        lane_count = len(self._visible_tracks()) if self._midi else 0
        height = HEADER_HEIGHT + lane_count * LANE_HEIGHT + 2
        self.setMinimumSize(NAME_COL_WIDTH, max(120, height))
        self.resize(NAME_COL_WIDTH, max(120, height))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QColor(28, 28, 28))

        if not self._midi:
            painter.setPen(QColor(170, 170, 170))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Load a MIDI file to view tracks.")
            return

        metrics = QFontMetrics(painter.font())
        for lane_idx, track_idx in enumerate(self._visible_tracks()):
            track = self._midi.tracks[track_idx]
            y = HEADER_HEIGHT + lane_idx * LANE_HEIGHT
            name_rect = (0, y, NAME_COL_WIDTH - 4, LANE_HEIGHT)

            if track_idx == self._selected_track:
                painter.fillRect(0, y, NAME_COL_WIDTH, LANE_HEIGHT, QColor(50, 50, 70))
            elif lane_idx % 2 == 0:
                painter.fillRect(0, y, NAME_COL_WIDTH, LANE_HEIGHT, QColor(34, 34, 34))

            painter.setPen(QColor(60, 60, 60))
            painter.drawLine(0, y, NAME_COL_WIDTH, y)

            icon_size = 12
            icon_x = 4
            icon_y = int(y + (LANE_HEIGHT - icon_size) / 2)
            icon_rect = QRect(icon_x, icon_y, icon_size, icon_size)
            is_visible = track_idx not in self._hidden_tracks
            painter.setPen(QColor(90, 90, 90))
            painter.drawRect(icon_rect)
            if is_visible:
                painter.fillRect(icon_rect.adjusted(1, 1, -1, -1), QColor(80, 160, 90))
            else:
                painter.setPen(QColor(120, 120, 120))
                painter.drawLine(icon_rect.topLeft(), icon_rect.bottomRight())
                painter.drawLine(icon_rect.bottomLeft(), icon_rect.topRight())

            portrait_size = self._portrait_size
            portrait_x = icon_x + icon_size + 6
            portrait_y = int(y + (LANE_HEIGHT - portrait_size) / 2)
            portrait_rect = QRect(portrait_x, portrait_y, portrait_size, portrait_size)
            portrait_path = self._portrait_paths.get(track_idx, "")
            portrait_pixmap = None
            if portrait_path:
                key = os.path.normcase(os.path.abspath(portrait_path))
                portrait_pixmap = self._portrait_pixmaps.get(key)
                if portrait_pixmap is None and self._thumbnail_loader:
                    self._thumbnail_loader.request_thumbnail(portrait_path)
            if portrait_pixmap and not portrait_pixmap.isNull():
                painter.drawPixmap(portrait_rect, portrait_pixmap)
            else:
                painter.fillRect(portrait_rect, QColor(45, 45, 45))
                painter.setPen(QColor(70, 70, 70))
                painter.drawRect(portrait_rect)

            name = self._track_display_names.get(track_idx) or track.name or f"Track {track_idx}"
            text_x = portrait_x + portrait_size + 6
            elided = metrics.elidedText(name, Qt.TextElideMode.ElideRight, NAME_COL_WIDTH - text_x - 6)
            painter.setPen(QColor(220, 220, 220))
            painter.drawText(
                text_x,
                y,
                NAME_COL_WIDTH - text_x - 6,
                name_rect[3],
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                elided,
            )
            progress = self._loading_progress.get(track_idx)
            if progress is not None:
                bar_margin = 6
                bar_height = 6
                bar_width = NAME_COL_WIDTH - bar_margin * 2
                bar_x = bar_margin
                bar_y = y + LANE_HEIGHT - bar_height - 4
                painter.fillRect(bar_x, bar_y, bar_width, bar_height, QColor(45, 45, 45))
                fill_width = max(1, int(bar_width * progress))
                painter.fillRect(bar_x, bar_y, fill_width, bar_height, QColor(90, 170, 90))
                painter.setPen(QColor(70, 70, 70))
                painter.drawRect(bar_x, bar_y, bar_width, bar_height)

    def mousePressEvent(self, event):
        if not self._midi:
            return
        pos = event.position().toPoint()
        if pos.y() < HEADER_HEIGHT:
            return
        lane_index = int((pos.y() - HEADER_HEIGHT) / LANE_HEIGHT)
        visible_tracks = self._visible_tracks()
        if lane_index < 0 or lane_index >= len(visible_tracks):
            return
        track_index = visible_tracks[lane_index]
        y = HEADER_HEIGHT + lane_index * LANE_HEIGHT
        icon_size = 12
        icon_x = 4
        icon_y = int(y + (LANE_HEIGHT - icon_size) / 2)
        icon_rect = QRect(icon_x, icon_y, icon_size, icon_size)
        if icon_rect.contains(pos):
            if track_index in self._hidden_tracks:
                self._hidden_tracks.remove(track_index)
                visible = True
            else:
                self._hidden_tracks.add(track_index)
                visible = False
            self.trackVisibilityToggled.emit(track_index, visible)
            self.update()
            return
        self._selected_track = track_index
        self.trackSelected.emit(track_index)
        self.update()


class MidiTimelineCanvas(QWidget):
    trackSelected = pyqtSignal(int)
    playheadMoved = pyqtSignal(float)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._midi: Optional[MidiFileData] = None
        self._pixels_per_beat = 48
        self._selected_track: Optional[int] = None
        self._playhead_beats: Optional[float] = None
        self._track_indices: List[int] = []
        self._track_segments: dict = {}
        self._scroll_x: int = 0
        self._viewport_width: int = 0
        self._scrubbing_header: bool = False
        self._scrub_press_pos: Optional[Tuple[int, int]] = None
        self._scrub_dragged: bool = False
        self._scrub_drag_threshold: int = 3
        self.setMinimumHeight(160)

    def set_midi(self, midi: Optional[MidiFileData]) -> None:
        self._midi = midi
        self._selected_track = None
        self._track_indices = []
        self._recompute_size()
        self.update()

    def set_pixels_per_beat(self, value: int) -> None:
        self._pixels_per_beat = max(8, int(value))
        self._recompute_size()
        self.update()

    def set_track_indices(self, indices: Optional[List[int]]) -> None:
        self._track_indices = list(indices or [])
        self._recompute_size()
        self.update()

    def set_segment_map(self, segment_map: Optional[dict]) -> None:
        self._track_segments = segment_map or {}
        self.update()

    def set_viewport_metrics(self, scroll_x: int, viewport_width: int) -> None:
        self._scroll_x = max(0, int(scroll_x or 0))
        self._viewport_width = max(0, int(viewport_width or 0))
        self.update()

    def set_selected_track(self, index: Optional[int]) -> None:
        self._selected_track = index
        self.update()

    def set_playhead_beats(self, beats: Optional[float]) -> None:
        self._playhead_beats = beats
        self.update()

    def _visible_tracks(self) -> List[int]:
        if not self._midi:
            return []
        if self._track_indices:
            return [idx for idx in self._track_indices if 0 <= idx < len(self._midi.tracks)]
        return list(range(len(self._midi.tracks)))

    def _total_beats(self) -> float:
        if not self._midi or not self._midi.tracks:
            return 4.0
        max_tick = 0
        for idx in self._visible_tracks():
            track = self._midi.tracks[idx]
            for note in track.notes:
                if note.end_tick > max_tick:
                    max_tick = note.end_tick
        ticks_per_beat = max(1, int(self._midi.ticks_per_beat))
        beats = max_tick / ticks_per_beat
        return max(4.0, beats)

    def _recompute_size(self) -> None:
        beats = self._total_beats()
        timeline_width = int(beats * self._pixels_per_beat) + 40
        lane_count = len(self._visible_tracks()) if self._midi else 0
        height = HEADER_HEIGHT + lane_count * LANE_HEIGHT + 2
        width = max(400, timeline_width)
        self.setMinimumSize(width, max(120, height))
        self.resize(width, max(120, height))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QColor(32, 32, 32))

        if not self._midi:
            painter.setPen(QColor(170, 170, 170))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Load a MIDI file to view tracks.")
            return

        ticks_per_beat = max(1, int(self._midi.ticks_per_beat))
        beats = self._total_beats()
        total_beats = int(beats) + 1

        # Draw beat grid
        grid_pen = QPen(QColor(55, 55, 55))
        measure_pen = QPen(QColor(80, 80, 80))
        text_pen = QPen(QColor(140, 140, 140))
        painter.setPen(text_pen)
        for beat in range(total_beats + 1):
            x = beat * self._pixels_per_beat
            if x > rect.width():
                break
            if beat % 4 == 0:
                painter.setPen(measure_pen)
            else:
                painter.setPen(grid_pen)
            painter.drawLine(x, HEADER_HEIGHT, x, rect.height())
            if beat % 4 == 0:
                painter.setPen(text_pen)
                painter.drawText(x + 2, HEADER_HEIGHT - 6, str(beat))

        # Lane backgrounds & names
        for lane_idx, track_idx in enumerate(self._visible_tracks()):
            track = self._midi.tracks[track_idx]
            y = HEADER_HEIGHT + lane_idx * LANE_HEIGHT
            lane_rect = (0, y, rect.width(), LANE_HEIGHT)

            if track_idx == self._selected_track:
                painter.fillRect(*lane_rect, QColor(45, 45, 60))
            elif lane_idx % 2 == 0:
                painter.fillRect(*lane_rect, QColor(36, 36, 36))

            painter.setPen(QColor(70, 70, 70))
            painter.drawLine(0, y, rect.width(), y)

            # Draw notes/segments
            segments = self._track_segments.get(track_idx)
            if segments:
                for start_beats, end_beats, track_num in segments:
                    x = start_beats * self._pixels_per_beat
                    width = max(2.0, (end_beats - start_beats) * self._pixels_per_beat)
                    note_rect = QRectF(x, y + 3, width, LANE_HEIGHT - 6)
                    fill = self._segment_color(track_num, track_idx == self._selected_track)
                    painter.fillRect(note_rect, fill)
                    painter.setPen(QColor(30, 30, 30))
                    painter.drawRect(note_rect)
                    self._draw_segment_label(painter, x, width, y, track_num)
            else:
                for note in track.notes:
                    start_beats = note.start_tick / ticks_per_beat
                    end_beats = note.end_tick / ticks_per_beat
                    x = start_beats * self._pixels_per_beat
                    width = max(2.0, (end_beats - start_beats) * self._pixels_per_beat)
                    note_rect = QRectF(x, y + 3, width, LANE_HEIGHT - 6)
                    fill = QColor(90, 160, 255) if track_idx == self._selected_track else QColor(80, 140, 220)
                    painter.fillRect(note_rect, fill)
                    painter.setPen(QColor(30, 30, 30))
                    painter.drawRect(note_rect)

        if self._playhead_beats is not None:
            x = self._playhead_beats * self._pixels_per_beat
            if 0 <= x <= rect.width():
                painter.setPen(QPen(QColor(255, 210, 80), 2))
                painter.drawLine(int(x), HEADER_HEIGHT, int(x), rect.height())
                painter.setPen(QColor(255, 210, 80))
                painter.drawLine(int(x), 0, int(x), HEADER_HEIGHT)

    def mousePressEvent(self, event):
        if not self._midi:
            return
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton:
            self._scrubbing_header = True
            self._scrub_press_pos = (pos.x(), pos.y())
            self._scrub_dragged = False
            beats = max(0.0, pos.x() / max(1.0, float(self._pixels_per_beat)))
            self.playheadMoved.emit(beats)
            self.update()
            return
        if pos.y() < HEADER_HEIGHT:
            return
        lane_index = int((pos.y() - HEADER_HEIGHT) / LANE_HEIGHT)
        visible_tracks = self._visible_tracks()
        if lane_index < 0 or lane_index >= len(visible_tracks):
            return
        track_index = visible_tracks[lane_index]
        self._selected_track = track_index
        self.trackSelected.emit(track_index)
        self.update()

    def mouseMoveEvent(self, event):
        if not self._midi:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._scrubbing_header = False
            return
        if not self._scrubbing_header:
            self._scrubbing_header = True
        pos = event.position().toPoint()
        if self._scrub_press_pos is not None and not self._scrub_dragged:
            dx = abs(pos.x() - self._scrub_press_pos[0])
            dy = abs(pos.y() - self._scrub_press_pos[1])
            if dx > self._scrub_drag_threshold or dy > self._scrub_drag_threshold:
                self._scrub_dragged = True
        beats = max(0.0, pos.x() / max(1.0, float(self._pixels_per_beat)))
        self.playheadMoved.emit(beats)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._scrubbing_header and not self._scrub_dragged:
                pos = event.position().toPoint()
                if pos.y() >= HEADER_HEIGHT:
                    lane_index = int((pos.y() - HEADER_HEIGHT) / LANE_HEIGHT)
                    visible_tracks = self._visible_tracks()
                    if 0 <= lane_index < len(visible_tracks):
                        track_index = visible_tracks[lane_index]
                        self._selected_track = track_index
                        self.trackSelected.emit(track_index)
                        self.update()
            self._scrubbing_header = False
            self._scrub_press_pos = None
            self._scrub_dragged = False

    @staticmethod
    def _segment_color(track_num: Optional[int], selected: bool) -> QColor:
        palette = [
            QColor(88, 172, 255),
            QColor(110, 215, 140),
            QColor(255, 178, 90),
            QColor(235, 120, 120),
            QColor(190, 140, 255),
            QColor(255, 210, 90),
        ]
        if not track_num:
            base = QColor(90, 160, 255)
        else:
            idx = (int(track_num) - 1) % len(palette)
            base = palette[idx]
        if selected:
            return base.lighter(120)
        return base

    def _draw_segment_label(
        self,
        painter: QPainter,
        x: float,
        width: float,
        lane_y: float,
        track_num: Optional[int],
    ) -> None:
        if not track_num:
            return
        text = str(int(track_num))
        metrics = QFontMetrics(painter.font())
        text_w = metrics.horizontalAdvance(text)
        pad = 2
        if width < text_w + pad * 2:
            return
        left_bound = x + pad
        right_bound = x + width - text_w - pad
        if self._viewport_width > 0:
            visible_left = self._scroll_x + pad
            visible_right = self._scroll_x + self._viewport_width - text_w - pad
        else:
            visible_left = pad
            visible_right = max(visible_left, self.width() - text_w - pad)
        if visible_right < visible_left:
            return
        text_x = max(left_bound, visible_left)
        text_x = min(text_x, right_bound, visible_right)
        if text_x < left_bound or text_x > right_bound:
            return
        painter.setPen(QColor(15, 15, 15))
        painter.drawText(
            QRectF(text_x, lane_y + 2, text_w + pad * 2, LANE_HEIGHT - 4),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            text,
        )


class MidiEditorDialog(QDialog):
    def __init__(self, main_window: "Optional[object]" = None, initial_path: Optional[str] = None):
        super().__init__(main_window)
        self.setWindowTitle("MSM MIDI Editor")
        self.resize(1100, 640)
        self.main_window = main_window
        self.midi_data: Optional[MidiFileData] = None
        self.current_track_index: Optional[int] = None
        self._visible_track_indices: List[int] = []
        self._world_number: Optional[int] = None
        self._animation_cache: dict = {}
        self.preview_audio = AudioManager(self)
        self.preview_audio_mixer = MultiTrackAudioMixer(self)
        self._preview_last_time: float = 0.0
        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(16)
        self._preview_timer.timeout.connect(self._on_preview_tick)
        self._preview_playing: bool = False
        self._preview_last_tick: Optional[float] = None
        self._preview_frame_accum: float = 0.0
        self._preview_playhead_seconds: float = 0.0
        self._preview_global_sync: bool = True
        self._preview_global_seconds: float = 0.0
        self._preview_active_segment: Optional[Tuple[int, float, float]] = None
        self._preview_segments: List[Tuple[float, float, str]] = []
        self._preview_baked_segments: List[PreviewSegmentBake] = []
        self._preview_current_anim_name: Optional[str] = None
        self._preview_anim_cache: dict = {}
        self._preview_atlas_cache: dict = {}
        self._preview_anim_duration: dict = {}
        self._preview_idle_anim_name: Optional[str] = None
        self._preview_audio_path: Optional[str] = None
        self._preview_audio_anim_name: Optional[str] = None
        self._preview_audio_resync_at: float = 0.0
        self._preview_audio_last_target: Optional[float] = None
        self._preview_audio_segment_idx: Optional[int] = None
        self._preview_audio_cache: dict = {}
        self._preview_audio_path_cache: dict = {}
        self._preview_track_has_notes: bool = False
        self._preview_multi_enabled: bool = False
        self._audio_only_preview: bool = False
        self._fast_preview_enabled: bool = False
        self._preview_texture_scale: float = 1.0
        self._prebake_world_states_enabled: bool = False
        self._prebake_fps: int = 60
        self._prebake_frame_cap: int = 7200
        self._preview_track_states: List[PreviewTrackState] = []
        self._preview_extra_widgets: List[OpenGLAnimationWidget] = []
        self._preview_tile_by_track: dict = {}
        self._audio_only_placeholder_widgets: List[QWidget] = []
        self._audio_only_dummy_widget: Optional[OpenGLAnimationWidget] = None
        self._multi_preview_same_viewport: bool = True
        self._loading_notes = False
        self._suspend_preview_updates = False
        self._preview_bake_queue: List[PreviewTrackState] = []
        self._preview_bake_active = False
        self._reexport_queue: List[Tuple[int, str, Optional[str]]] = []
        self._reexport_active = False
        self._reexport_process: Optional[subprocess.Popen] = None
        self._reexport_current: Optional[Tuple[int, str, Optional[str], str]] = None
        self._audio_preload_queue: List[Tuple[int, str]] = []
        self._audio_preload_active: bool = False
        self._audio_preload_pending_play: bool = False
        self._audio_preload_bypass: bool = False
        self._audio_preload_track_totals: dict = {}
        self._audio_preload_track_done: dict = {}
        self._track_bin_cache: dict = {}
        self._animation_resource_cache: "OrderedDict[str, Tuple[dict, dict]]" = OrderedDict()
        self._animation_resource_cache_limit = 24
        self._active_loading_track: Optional[int] = None
        self._hidden_track_indices: set = set()
        self._world_state_cache: dict = {}
        self._world_state_lock = threading.Lock()
        max_workers = max(2, (os.cpu_count() or 2) - 1)
        self._world_state_executor = ThreadPoolExecutor(max_workers=max_workers)
        self._prebaked_world_states_cache: dict = {}
        self._prebaked_world_states_futures: dict = {}
        self._portrait_loader: Optional[ThumbnailLoader] = None

        if self.main_window and getattr(self.main_window, "settings", None):
            try:
                self._fast_preview_enabled = bool(
                    self.main_window.settings.value("midi_editor/fast_preview", False, type=bool)
                )
            except Exception:
                self._fast_preview_enabled = False
            try:
                stored_scale = self.main_window.settings.value(
                    "midi_editor/texture_scale", 1.0, type=float
                )
                self._preview_texture_scale = float(stored_scale)
            except Exception:
                self._preview_texture_scale = 1.0
            try:
                self._prebake_world_states_enabled = bool(
                    self.main_window.settings.value("midi_editor/prebake_world_states", True, type=bool)
                )
            except Exception:
                self._prebake_world_states_enabled = True
        self._preview_texture_scale = max(0.25, min(1.0, float(self._preview_texture_scale)))

        self._build_ui()
        self.name_canvas.trackVisibilityToggled.connect(self._on_track_visibility_toggled)
        self._init_portrait_loader()

        if initial_path:
            self._load_midi(initial_path)

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        layout = QVBoxLayout(self)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("MIDI File:"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select a .mid/.midi file...")
        file_row.addWidget(self.path_edit, 1)
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(self.browse_btn)
        self.reload_btn = QPushButton("Reload")
        self.reload_btn.clicked.connect(self._reload_file)
        file_row.addWidget(self.reload_btn)
        layout.addLayout(file_row)

        info_row = QHBoxLayout()
        info_row.addWidget(QLabel("BPM:"))
        self.bpm_spin = QDoubleSpinBox()
        self.bpm_spin.setRange(1.0, 400.0)
        self.bpm_spin.setDecimals(3)
        self.bpm_spin.valueChanged.connect(self._on_bpm_changed)
        info_row.addWidget(self.bpm_spin)
        self.ticks_label = QLabel("Ticks/Beat: -")
        info_row.addWidget(self.ticks_label)
        info_row.addStretch(1)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self._save_file)
        info_row.addWidget(self.save_btn)
        self.save_as_btn = QPushButton("Save As...")
        self.save_as_btn.clicked.connect(self._save_file_as)
        info_row.addWidget(self.save_as_btn)
        self.export_mp3_btn = QPushButton("Export MP3")
        self.export_mp3_btn.clicked.connect(self._export_mp3)
        info_row.addWidget(self.export_mp3_btn)
        layout.addLayout(info_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        timeline_panel = QWidget()
        timeline_layout = QVBoxLayout(timeline_panel)
        timeline_toolbar = QHBoxLayout()
        timeline_toolbar.addWidget(QLabel("Timeline Zoom:"))
        self.timeline_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_zoom_slider.setRange(16, 160)
        self.timeline_zoom_slider.setValue(48)
        self.timeline_zoom_slider.valueChanged.connect(self._on_timeline_zoom_changed)
        timeline_toolbar.addWidget(self.timeline_zoom_slider, 1)
        self.preview_multi_checkbox = QCheckBox("Multi-track Preview")
        self.preview_multi_checkbox.toggled.connect(self._on_preview_multi_toggled)
        timeline_toolbar.addWidget(self.preview_multi_checkbox)
        self.audio_only_checkbox = QCheckBox("Audio Only")
        self.audio_only_checkbox.toggled.connect(self._on_audio_only_toggled)
        timeline_toolbar.addWidget(self.audio_only_checkbox)
        self.fast_preview_checkbox = QCheckBox("Fast Preview")
        self.fast_preview_checkbox.toggled.connect(self._on_fast_preview_toggled)
        self.fast_preview_checkbox.setChecked(self._fast_preview_enabled)
        timeline_toolbar.addWidget(self.fast_preview_checkbox)
        self.prebake_checkbox = QCheckBox("Prebake Frames")
        self.prebake_checkbox.toggled.connect(self._on_prebake_toggled)
        self.prebake_checkbox.setChecked(self._prebake_world_states_enabled)
        timeline_toolbar.addWidget(self.prebake_checkbox)
        timeline_toolbar.addWidget(QLabel("Texture Scale:"))
        self.texture_scale_combo = QComboBox()
        self.texture_scale_combo.addItem("100%", 1.0)
        self.texture_scale_combo.addItem("75%", 0.75)
        self.texture_scale_combo.addItem("50%", 0.5)
        self.texture_scale_combo.addItem("25%", 0.25)
        current_idx = 0
        for idx in range(self.texture_scale_combo.count()):
            if abs(float(self.texture_scale_combo.itemData(idx)) - self._preview_texture_scale) < 1e-3:
                current_idx = idx
                break
        self.texture_scale_combo.setCurrentIndex(current_idx)
        self.texture_scale_combo.currentIndexChanged.connect(self._on_texture_scale_changed)
        timeline_toolbar.addWidget(self.texture_scale_combo)
        self.preview_play_btn = QPushButton("Play")
        self.preview_play_btn.clicked.connect(self._toggle_preview_playback)
        timeline_toolbar.addWidget(self.preview_play_btn)
        self.preview_stop_btn = QPushButton("Stop")
        self.preview_stop_btn.clicked.connect(self._stop_preview_playback)
        timeline_toolbar.addWidget(self.preview_stop_btn)
        self.reexport_btn = QPushButton("Re-export JSONs")
        self.reexport_btn.clicked.connect(self._reexport_monster_jsons)
        timeline_toolbar.addWidget(self.reexport_btn)
        self.preview_time_label = QLabel("Time: 0.00s")
        timeline_toolbar.addWidget(self.preview_time_label)
        timeline_layout.addLayout(timeline_toolbar)

        timeline_stack = QWidget()
        timeline_stack_layout = QHBoxLayout(timeline_stack)
        timeline_stack_layout.setContentsMargins(0, 0, 0, 0)
        timeline_stack_layout.setSpacing(0)

        self.name_scroll = QScrollArea()
        self.name_scroll.setWidgetResizable(False)
        self.name_canvas = MidiTrackListCanvas()
        self.name_canvas.trackSelected.connect(self._on_timeline_track_selected)
        self.name_scroll.setWidget(self.name_canvas)
        self.name_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.name_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        timeline_stack_layout.addWidget(self.name_scroll)

        self.timeline_scroll = QScrollArea()
        self.timeline_scroll.setWidgetResizable(False)
        self.timeline_canvas = MidiTimelineCanvas()
        self.timeline_canvas.trackSelected.connect(self._on_timeline_track_selected)
        self.timeline_canvas.playheadMoved.connect(self._on_playhead_scrubbed)
        self.timeline_scroll.setWidget(self.timeline_canvas)
        self.timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.timeline_scroll.horizontalScrollBar().valueChanged.connect(self._on_timeline_hscroll)
        self.timeline_scroll.viewport().installEventFilter(self)
        timeline_stack_layout.addWidget(self.timeline_scroll, 1)

        self._scroll_syncing = False
        self.name_scroll.verticalScrollBar().valueChanged.connect(self._sync_timeline_scroll)
        self.timeline_scroll.verticalScrollBar().valueChanged.connect(self._sync_name_scroll)

        timeline_layout.addWidget(timeline_stack, 1)

        self.preview_gl = OpenGLAnimationWidget(
            shader_registry=getattr(self.main_window, "shader_registry", None)
        )
        self.preview_gl.setMinimumHeight(220)
        self.preview_gl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preview_gl.animation_time_changed.connect(self._on_preview_time_changed)
        self._apply_fast_preview_to_widget(self.preview_gl)

        self.preview_container = QWidget()
        self.preview_container_layout = QGridLayout(self.preview_container)
        self.preview_container_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_container_layout.setSpacing(6)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_scroll.setWidget(self.preview_container)
        self.preview_scroll.viewport().installEventFilter(self)
        timeline_layout.addWidget(self.preview_scroll)

        self.preview_primary_label = QLabel("Preview Track")
        self.preview_primary_label.setStyleSheet("color: #ddd; font-size: 9pt;")
        self.preview_primary_container = QWidget()
        primary_layout = QVBoxLayout(self.preview_primary_container)
        primary_layout.setContentsMargins(0, 0, 0, 0)
        primary_layout.setSpacing(4)
        primary_layout.addWidget(self.preview_primary_label)
        primary_layout.addWidget(self.preview_gl)
        self.preview_container_layout.addWidget(self.preview_primary_container, 0, 0)
        self._preview_tile_containers: List[QWidget] = [self.preview_primary_container]

        self.preview_status_label = QLabel("Preview: select a monster track.")
        self.preview_status_label.setStyleSheet("color: #bbb; font-size: 9pt;")
        timeline_layout.addWidget(self.preview_status_label)
        splitter.addWidget(timeline_panel)

        if self.main_window and getattr(self.main_window, "control_panel", None):
            self._sync_preview_audio_settings()

        right_panel = QVBoxLayout()
        right_container = QWidget()
        right_container.setLayout(right_panel)
        splitter.addWidget(right_container)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        header_row = QHBoxLayout()
        self.track_label = QLabel("Track: (none)")
        header_row.addWidget(self.track_label)
        header_row.addStretch(1)
        self.add_note_btn = QPushButton("Add Segment")
        self.add_note_btn.clicked.connect(self._add_note)
        header_row.addWidget(self.add_note_btn)
        self.delete_note_btn = QPushButton("Delete Segment")
        self.delete_note_btn.clicked.connect(self._delete_note)
        header_row.addWidget(self.delete_note_btn)
        right_panel.addLayout(header_row)

        self.notes_table = QTableWidget(0, 5)
        self.notes_table.setHorizontalHeaderLabels(
            ["Start (beats)", "Length (beats)", "Note", "Channel", "Velocity"]
        )
        self.notes_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.notes_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.notes_table.itemChanged.connect(self._on_note_item_changed)
        self.notes_table.itemSelectionChanged.connect(self._on_note_selection_changed)
        self.notes_table.horizontalHeader().setStretchLastSection(True)
        right_panel.addWidget(self.notes_table, 1)

        self.status_label = QLabel("Segments represent monster play windows, not musical pitch.")
        self.status_label.setStyleSheet("color: #bbb; font-size: 9pt;")
        right_panel.addWidget(self.status_label)

        self._set_enabled(False)
        self._update_timeline_viewport_metrics()

    def _init_portrait_loader(self) -> None:
        if self._portrait_loader is not None:
            return
        thumb_size = QSize(self.name_canvas._portrait_size, self.name_canvas._portrait_size)
        self._portrait_loader = ThumbnailLoader(thumb_size, parent=self)
        self._portrait_loader.thumbnail_ready.connect(self._on_portrait_ready)
        self.name_canvas.set_thumbnail_loader(self._portrait_loader)

    def _on_portrait_ready(self, image_path: str, pixmap: QPixmap) -> None:
        if not image_path or pixmap.isNull():
            return
        self.name_canvas.set_portrait_pixmap(image_path, pixmap)

    # ------------------------------------------------------------------ Loading / Saving

    def _browse_file(self):
        start_dir = None
        if self.main_window:
            settings = getattr(self.main_window, "settings", None)
            if settings:
                stored_dir = settings.value("midi_editor/last_midi_dir", "", type=str) or ""
                if stored_dir and os.path.isdir(stored_dir):
                    start_dir = stored_dir
            downloads_path = getattr(self.main_window, "downloads_path", None)
            game_path = getattr(self.main_window, "game_path", None)
            candidate_dirs: List[str] = []
            if downloads_path:
                candidate_dirs.extend(
                    [
                        os.path.join(downloads_path, "audio", "music"),
                        os.path.join(downloads_path, "data", "audio", "music"),
                    ]
                )
            if game_path:
                candidate_dirs.append(os.path.join(game_path, "data", "audio", "music"))
            if not start_dir:
                for candidate in candidate_dirs:
                    if os.path.isdir(candidate):
                        start_dir = candidate
                        break
        if not start_dir or not os.path.exists(start_dir):
            start_dir = os.path.expanduser("~")
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select MIDI File",
            start_dir,
            "MIDI Files (*.mid *.midi);;All Files (*)",
        )
        if filename:
            self._load_midi(filename)

    def _set_track_loading_progress(self, track_index: int, progress: Optional[float], *, process_events: bool = False) -> None:
        if not hasattr(self, "name_canvas") or not self.name_canvas:
            return
        try:
            self.name_canvas.set_track_loading(track_index, progress)
        except Exception:
            return
        if process_events:
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def _start_audio_preload(self, track_indices: List[int]) -> bool:
        if not self.midi_data or not self.main_window:
            return False
        self._audio_preload_queue = []
        self._audio_preload_track_totals = {}
        self._audio_preload_track_done = {}
        seen_global: set = set()
        for track_index in track_indices:
            track = self.midi_data.tracks[track_index]
            track_name = (track.name or "").strip()
            if not track_name or not track_name.endswith("_Monster"):
                continue
            token_hint = self._token_hint_for_track_name(track_name)
            segments = self._build_preview_segments(track, track_index)
            anim_names = [seg[2] for seg in segments if seg[2]]
            if not anim_names:
                continue
            seen_local = set()
            unique = [name for name in anim_names if not (name in seen_local or seen_local.add(name))]
            for anim_name in unique:
                cache_key = self._preview_cache_key(anim_name, token_hint=token_hint)
                if cache_key in seen_global:
                    continue
                seen_global.add(cache_key)
                self._audio_preload_queue.append((track_index, anim_name, token_hint))
            self._audio_preload_track_totals[track_index] = len(unique)
            self._audio_preload_track_done[track_index] = 0
            if len(unique) > 0:
                self._set_track_loading_progress(track_index, 0.0, process_events=True)
        if not self._audio_preload_queue:
            return False
        if not self._audio_preload_active:
            self._audio_preload_active = True
            QTimer.singleShot(0, self._step_audio_preload)
        return True

    def _step_audio_preload(self) -> None:
        if not self._audio_preload_queue:
            self._audio_preload_active = False
            for track_index in list(self._audio_preload_track_totals.keys()):
                self._set_track_loading_progress(track_index, None, process_events=True)
            if self._audio_preload_pending_play:
                self._audio_preload_pending_play = False
                self.preview_status_label.setText("Preview: ready.")
                self._audio_preload_bypass = True
                try:
                    self._set_preview_playing(True)
                finally:
                    self._audio_preload_bypass = False
            return
        track_index, anim_name, token_hint = self._audio_preload_queue.pop(0)
        if token_hint is None and self.midi_data and 0 <= track_index < len(self.midi_data.tracks):
            track = self.midi_data.tracks[track_index]
            token_hint = self._token_hint_for_track_name(track.name or "")
        self._prewarm_preview_audio({anim_name}, token_hint=token_hint)
        if track_index in self._audio_preload_track_done:
            self._audio_preload_track_done[track_index] += 1
            total = max(1, self._audio_preload_track_totals.get(track_index, 1))
            done = self._audio_preload_track_done[track_index]
            self._set_track_loading_progress(track_index, min(0.95, done / float(total)), process_events=True)
        QTimer.singleShot(0, self._step_audio_preload)

    def _enqueue_preview_bake(self, state: PreviewTrackState) -> None:
        if state in self._preview_bake_queue:
            return
        self._preview_bake_queue.append(state)
        if not self._preview_bake_active:
            self._preview_bake_active = True
            QTimer.singleShot(0, self._step_preview_bake)

    def _step_preview_bake(self) -> None:
        if not self._preview_bake_queue:
            self._preview_bake_active = False
            return
        state = self._preview_bake_queue.pop(0)
        self._bake_preview_state(state)
        QTimer.singleShot(0, self._step_preview_bake)

    def _bake_preview_state(self, state: PreviewTrackState) -> None:
        if not self.midi_data:
            return
        try:
            track = self.midi_data.tracks[state.track_index]
        except Exception:
            return
        track_name = track.name or f"Track {state.track_index}"
        token_hint = self._token_hint_for_track_name(track_name)
        state.track_name = track_name
        state.track_has_notes = bool(track.notes)
        self._set_track_loading_progress(state.track_index, 0.05, process_events=True)
        segments = self._build_preview_segments(track, state.track_index)
        self._set_track_loading_progress(state.track_index, 0.35, process_events=True)
        idle_name = None if self._audio_only_preview else self._resolve_track_idle_anim(track, state.track_index, segments)
        self._set_track_loading_progress(state.track_index, 0.6, process_events=True)
        baked_segments = self._bake_segments_for_track(
            segments,
            idle_name,
            token_hint=token_hint,
        )
        self._set_track_loading_progress(state.track_index, 0.9, process_events=True)
        state.segments = segments
        state.baked_segments = baked_segments
        state.idle_anim_name = idle_name
        if baked_segments:
            first_segment = baked_segments[0]
            state.current_anim_name = first_segment.anim_name
            if not (
                self._preview_multi_enabled
                and self._multi_preview_same_viewport
                and state.track_index != self.current_track_index
            ):
                self._apply_preview_baked_segment(first_segment, widget=state.widget, set_current=False)
                state.widget.set_time(0.0)
        self._set_track_loading_progress(state.track_index, None, process_events=False)
        if self._preview_multi_enabled and self._multi_preview_same_viewport:
            self._update_preview_for_playhead()

    def _reexport_monster_jsons(self) -> None:
        if not self.midi_data or not self.main_window:
            QMessageBox.information(self, "Re-export JSONs", "Load a MIDI file first.")
            return
        if not getattr(self.main_window, "bin2json_path", ""):
            QMessageBox.warning(self, "Re-export JSONs", "bin2json script not found; cannot re-export.")
            return
        track_indices = list(self._visible_track_indices or [])
        if not track_indices:
            QMessageBox.information(self, "Re-export JSONs", "No monster tracks found to export.")
            return

        self._reexport_queue = []
        for track_index in track_indices:
            track = self.midi_data.tracks[track_index]
            track_name = (track.name or "").strip()
            if not track_name or not track_name.endswith("_Monster"):
                continue
            token_hint = track_name.replace("_Monster", "")
            anim_names: List[str] = []
            segments = self._build_preview_segments(track, track_index)
            anim_names.extend([seg[2] for seg in segments if seg[2]])
            base_name = self._build_animation_name(track_name, track_index)
            if base_name:
                anim_names.append(base_name)
            idle_name = self._resolve_track_idle_anim(track, track_index, segments)
            if idle_name:
                anim_names.append(idle_name)
            seen = set()
            anim_names = [name for name in anim_names if not (name in seen or seen.add(name))]
            for anim_name in anim_names:
                self._reexport_queue.append((track_index, anim_name, token_hint))

        if not self._reexport_queue:
            QMessageBox.information(self, "Re-export JSONs", "No animations found to export.")
            return

        self._reexport_active = True
        self._reexport_stats = {"converted": 0, "skipped": 0, "missing": 0, "failures": []}
        self.reexport_btn.setEnabled(False)
        QTimer.singleShot(0, self._step_reexport_jsons)

    def _step_reexport_jsons(self) -> None:
        if self._reexport_process is not None:
            if self._reexport_process.poll() is None:
                QTimer.singleShot(50, self._step_reexport_jsons)
                return
            return_code = self._reexport_process.returncode
            track_index, anim_name, token_hint, bin_path = self._reexport_current or (None, None, None, None)
            self._reexport_process = None
            self._reexport_current = None
            if return_code == 0:
                output_json = os.path.splitext(bin_path)[0] + ".json"
                if os.path.exists(output_json):
                    self._reexport_stats["converted"] += 1
                else:
                    self._reexport_stats["failures"].append(os.path.basename(bin_path))
            else:
                if bin_path:
                    self._reexport_stats["failures"].append(os.path.basename(bin_path))
            if track_index is not None:
                if not any(item[0] == track_index for item in self._reexport_queue):
                    self._set_track_loading_progress(track_index, None, process_events=True)
            QTimer.singleShot(0, self._step_reexport_jsons)
            return

        if not self._reexport_queue:
            self._reexport_active = False
            self.reexport_btn.setEnabled(True)
            converted = self._reexport_stats["converted"]
            skipped = self._reexport_stats["skipped"]
            missing = self._reexport_stats["missing"]
            failures = self._reexport_stats["failures"]
            summary = f"Re-exported {converted} JSON(s)."
            if skipped:
                summary += f" Skipped {skipped} (no BIN)."
            if missing:
                summary += f" Missing {missing} animation(s)."
            if failures:
                summary += f" Failed {len(failures)}."
            QMessageBox.information(self, "Re-export JSONs", summary)
            return

        track_index, anim_name, token_hint = self._reexport_queue.pop(0)
        # Estimate progress for this track
        remaining_for_track = [item for item in self._reexport_queue if item[0] == track_index]
        total_for_track = 1 + len(remaining_for_track)
        done_for_track = max(1, total_for_track - len(remaining_for_track))
        self._set_track_loading_progress(
            track_index, min(0.95, done_for_track / max(1.0, total_for_track)), process_events=True
        )

        found = self._find_animation_in_bin(anim_name, token_hint=token_hint)
        if not found:
            self._reexport_stats["missing"] += 1
            QTimer.singleShot(0, self._step_reexport_jsons)
            return
        source_path, _data, _anim = found
        bin_path = None
        if source_path.lower().endswith(".bin"):
            bin_path = source_path
        else:
            candidate = os.path.splitext(source_path)[0] + ".bin"
            if os.path.exists(candidate):
                bin_path = candidate
        if not bin_path:
            self._reexport_stats["skipped"] += 1
            QTimer.singleShot(0, self._step_reexport_jsons)
            return
        script_path = getattr(self.main_window, "bin2json_path", "") if self.main_window else ""
        if not script_path:
            self._reexport_stats["failures"].append(os.path.basename(bin_path))
            QTimer.singleShot(0, self._step_reexport_jsons)
            return
        try:
            cmd = self.main_window._build_python_command(script_path) + ["d", bin_path]
            cwd = os.path.dirname(script_path) if script_path else None
            self._reexport_current = (track_index, anim_name, token_hint, bin_path)
            self._reexport_process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self._reexport_stats["failures"].append(os.path.basename(bin_path))
            self._reexport_process = None
            self._reexport_current = None
            QTimer.singleShot(0, self._step_reexport_jsons)

    def _reload_file(self):
        path = self.path_edit.text().strip()
        if path:
            self._load_midi(path)

    def _load_midi(self, path: str):
        try:
            midi = read_midi_file(path)
        except Exception as exc:
            QMessageBox.warning(self, "MIDI Load Failed", f"Failed to load MIDI:\n{exc}")
            return
        self.midi_data = midi
        self._reset_preview_caches()
        self.path_edit.setText(path)
        self._world_number = self._parse_world_number(path)
        self.ticks_label.setText(f"Ticks/Beat: {midi.ticks_per_beat}")
        bpm = midi.get_bpm() or 120.0
        self.bpm_spin.blockSignals(True)
        self.bpm_spin.setValue(float(bpm))
        self.bpm_spin.blockSignals(False)
        self._populate_tracks()
        self._set_enabled(True)
        if self.main_window and getattr(self.main_window, "settings", None):
            try:
                self.main_window.settings.setValue(
                    "midi_editor/last_midi_dir",
                    os.path.dirname(path),
                )
            except Exception:
                pass

    def _save_file(self):
        if not self.midi_data:
            return
        path = self.path_edit.text().strip()
        if not path:
            self._save_file_as()
            return
        try:
            write_midi_file(self.midi_data, path)
            self.status_label.setText(f"Saved: {os.path.basename(path)}")
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", f"Failed to save MIDI:\n{exc}")

    def _save_file_as(self):
        if not self.midi_data:
            return
        start_dir = os.path.dirname(self.path_edit.text().strip() or os.path.expanduser("~"))
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save MIDI As",
            start_dir,
            "MIDI Files (*.mid *.midi);;All Files (*)",
        )
        if not filename:
            return
        if not filename.lower().endswith((".mid", ".midi")):
            filename += ".mid"
        self.path_edit.setText(filename)
        self._save_file()

    # ------------------------------------------------------------------ Tracks / Notes

    def _populate_tracks(self):
        if not self.midi_data:
            return
        self._hidden_track_indices = set()
        self._visible_track_indices = self._compute_visible_track_indices()
        self.timeline_canvas.set_midi(self.midi_data)
        self.timeline_canvas.set_track_indices(self._visible_track_indices)
        self._refresh_timeline_segment_map()
        self.name_canvas.set_midi(self.midi_data)
        self.name_canvas.set_track_indices(self._visible_track_indices)
        self.name_canvas.set_hidden_tracks(self._hidden_track_indices)
        self._refresh_track_display_names()
        self._update_track_portraits()
        if self.midi_data.tracks:
            first = self._visible_track_indices[0] if self._visible_track_indices else 0
            self._select_track(first, load_preview=False)

    @staticmethod
    def _normalize_track_token_for_name_lookup(token_hint: str) -> str:
        raw = (token_hint or "").strip().lower()
        if not raw:
            return ""
        raw = raw.replace("-", "_")
        raw = re.sub(r"^\d{2}_", "", raw)
        raw = raw.strip("_")
        return raw

    def _resolve_common_name_for_track(self, track_name: str) -> Optional[str]:
        if not self.main_window or not track_name:
            return None
        token_hint = self._token_hint_for_track_name(track_name)
        if not token_hint:
            return None
        normalized_token = self._normalize_track_token_for_name_lookup(token_hint)
        if not normalized_token:
            return None
        resolver = getattr(self.main_window, "resolve_common_name_for_monster_token", None)
        if callable(resolver):
            try:
                return resolver(normalized_token)
            except Exception:
                return None
        return None

    def _display_label_for_track(self, track_name: str, track_index: int) -> str:
        base_name = (track_name or "").strip() or f"Track {track_index}"
        resolved = self._resolve_common_name_for_track(base_name)
        if resolved:
            return resolved
        return base_name

    def _refresh_track_display_names(self) -> None:
        if not self.midi_data:
            self.name_canvas.set_track_display_names({})
            return
        labels: Dict[int, str] = {}
        for idx, track in enumerate(self.midi_data.tracks):
            labels[idx] = self._display_label_for_track(track.name or "", idx)
        self.name_canvas.set_track_display_names(labels)

    def _update_track_portraits(self) -> None:
        if not self.main_window:
            return
        book_dirs: List[Path] = []
        downloads_path = getattr(self.main_window, "downloads_path", "") or ""
        game_path = getattr(self.main_window, "game_path", "") or ""
        if downloads_path:
            for candidate in (
                Path(downloads_path) / "gfx" / "book",
                Path(downloads_path) / "data" / "gfx" / "book",
            ):
                if candidate.exists():
                    book_dirs.append(candidate)
        if game_path:
            game_book = Path(game_path) / "data" / "gfx" / "book"
            if game_book.exists():
                book_dirs.append(game_book)
        if not book_dirs:
            fallback = (
                Path(self.main_window.project_root)
                / "My Singing Monsters Game Filesystem Example"
                / "data"
                / "gfx"
                / "book"
            )
            if fallback.exists():
                book_dirs.append(fallback)
        if not book_dirs:
            return
        try:
            if not getattr(self.main_window, "monster_file_lookup", None):
                self.main_window.refresh_file_list()
        except Exception:
            pass
        try:
            entries = self.main_window._build_monster_browser_entries(book_dirs)
        except Exception:
            entries = []
        token_map: Dict[str, str] = {}
        for entry in entries:
            if entry.image_path:
                token_map[(entry.token or "").lower()] = entry.image_path
        portrait_paths: Dict[int, str] = {}
        for idx in self._visible_track_indices:
            track = self.midi_data.tracks[idx]
            name = (track.name or "").strip()
            if not name.endswith("_Monster"):
                continue
            token = name.replace("_Monster", "").lower()
            image_path = token_map.get(token, "")
            if image_path:
                portrait_paths[idx] = image_path
        self.name_canvas.set_portrait_paths(portrait_paths)

    def _reset_preview_caches(self) -> None:
        self._preview_anim_cache.clear()
        self._preview_atlas_cache.clear()
        self._preview_anim_duration.clear()
        self._preview_audio_cache.clear()
        self._preview_audio_path_cache.clear()
        self._animation_cache.clear()
        self._track_bin_cache.clear()
        self._animation_resource_cache.clear()
        with self._world_state_lock:
            self._world_state_cache.clear()
            self._prebaked_world_states_cache.clear()
            self._prebaked_world_states_futures.clear()

    def _select_track(self, idx: int, *, load_preview: bool = True):
        if not self.midi_data:
            return
        if idx < 0 or idx >= len(self.midi_data.tracks):
            return
        self.current_track_index = idx
        track = self.midi_data.tracks[idx]
        name = self._display_label_for_track(track.name or "", idx)
        self.track_label.setText(f"Track: {name}")
        self.timeline_canvas.set_selected_track(idx)
        self.name_canvas.set_selected_track(idx)
        previous_suspend = self._suspend_preview_updates
        # Avoid transient preview/audio updates while switching tracks; we apply
        # the new track state in one deterministic pass after notes load.
        self._suspend_preview_updates = True
        self._load_notes(track.notes)
        self._suspend_preview_updates = previous_suspend
        if not load_preview:
            return

        self._preview_track_has_notes = bool(track.notes)
        self._preview_segments = self._build_preview_segments(track, idx)
        self._rebake_preview_segments()

        if self._preview_multi_enabled:
            self._rebuild_preview_tracks()
        elif not self._audio_only_preview:
            self._load_preview_for_track(track, idx)

        self._update_preview_for_playhead()

    def _sync_preview_audio_settings(self) -> None:
        if not self.main_window:
            return
        control_panel = getattr(self.main_window, "control_panel", None)
        if not control_panel:
            return
        try:
            enabled = bool(control_panel.audio_enable_checkbox.isChecked())
            volume = int(control_panel.audio_volume_slider.value())
        except Exception:
            return
        try:
            if self.preview_audio:
                self.preview_audio.set_enabled(enabled)
                self.preview_audio.set_volume(volume)
            if self.preview_audio_mixer:
                self.preview_audio_mixer.set_enabled(enabled)
                self.preview_audio_mixer.set_volume(volume)
        except Exception:
            return

    def _apply_fast_preview_to_widget(self, widget: Optional[OpenGLAnimationWidget]) -> None:
        if not widget:
            return
        if hasattr(widget, "set_fast_preview_enabled"):
            widget.set_fast_preview_enabled(self._fast_preview_enabled)

    def _apply_preview_texture_scale_to_atlas(self, atlas: object) -> None:
        if not atlas:
            return
        try:
            atlas.downscale_factor = float(self._preview_texture_scale)
        except Exception:
            pass

    def _prebake_cache_key(self, animation: "AnimationData", atlases: list) -> Tuple:
        atlas_keys = []
        for atlas in atlases or []:
            key = getattr(atlas, "xml_path", None) or getattr(atlas, "source_name", None) or ""
            if key:
                atlas_keys.append(os.path.normcase(os.path.normpath(str(key))))
        return (id(animation), tuple(atlas_keys), int(self._prebake_fps))

    def _ensure_prebaked_world_states(
        self,
        animation: Optional["AnimationData"],
        duration: float,
        atlases: list,
    ) -> None:
        if not self._prebake_world_states_enabled:
            return
        if not animation or duration <= 0.0:
            return
        fps = max(1, int(self._prebake_fps))
        frame_count = int(math.ceil(duration * fps))
        if frame_count > self._prebake_frame_cap:
            return
        key = self._prebake_cache_key(animation, atlases)
        with self._world_state_lock:
            if key in self._prebaked_world_states_cache:
                return
            if key in self._prebaked_world_states_futures:
                return
            renderer_state = self._renderer_state_snapshot()
            future = self._world_state_executor.submit(
                _compute_world_states_sequence_worker,
                animation,
                duration,
                fps,
                atlases,
                renderer_state,
            )
            self._prebaked_world_states_futures[key] = future

    def _get_prebaked_world_states(
        self,
        animation: Optional["AnimationData"],
        local_time: float,
        duration: float,
        atlases: list,
    ) -> Optional[dict]:
        if not self._prebake_world_states_enabled or not animation:
            return None
        if duration <= 0.0:
            return None
        key = self._prebake_cache_key(animation, atlases)
        with self._world_state_lock:
            states_list = self._prebaked_world_states_cache.get(key)
            future = self._prebaked_world_states_futures.get(key)
        if states_list is None and isinstance(future, Future) and future.done():
            try:
                states_list = future.result()
            except Exception:
                states_list = None
            with self._world_state_lock:
                self._prebaked_world_states_futures.pop(key, None)
                if states_list:
                    self._prebaked_world_states_cache[key] = states_list
        if not states_list:
            self._ensure_prebaked_world_states(animation, duration, atlases)
            return None
        fps = max(1, int(self._prebake_fps))
        idx = int(local_time * fps) % len(states_list)
        return states_list[idx]

    def _renderer_state_snapshot(self) -> dict:
        if not hasattr(self, "preview_gl"):
            return {}
        renderer = self.preview_gl.renderer
        snapshot = {
            "position_scale": renderer.position_scale,
            "base_world_scale": renderer.base_world_scale,
            "anchor_bias_x": renderer.anchor_bias_x,
            "anchor_bias_y": renderer.anchor_bias_y,
            "anchor_flip_x": renderer.anchor_flip_x,
            "anchor_flip_y": renderer.anchor_flip_y,
            "anchor_scale_x": renderer.anchor_scale_x,
            "anchor_scale_y": renderer.anchor_scale_y,
            "local_position_multiplier": renderer.local_position_multiplier,
            "parent_mix": renderer.parent_mix,
            "rotation_bias": renderer.rotation_bias,
            "scale_bias_x": renderer.scale_bias_x,
            "scale_bias_y": renderer.scale_bias_y,
            "world_offset_x": renderer.world_offset_x,
            "world_offset_y": renderer.world_offset_y,
            "trim_shift_multiplier": renderer.trim_shift_multiplier,
            "anchor_overrides": dict(renderer.anchor_overrides),
            "costume_pivot_adjustment_enabled": renderer.costume_pivot_adjustment_enabled,
        }
        return snapshot

    def _request_world_states(
        self,
        animation: Optional["AnimationData"],
        time_value: float,
        atlases: list,
    ) -> Optional[dict]:
        if not animation:
            return None
        time_key = round(float(time_value) * 60.0) / 60.0
        cache_key = id(animation)
        with self._world_state_lock:
            entry = self._world_state_cache.get(cache_key, {})
            states = entry.get("states")
            future = entry.get("future")
            if entry.get("time_key") == time_key and states is not None:
                return states
            if isinstance(future, Future) and not future.done():
                # Don't reuse stale states for a new time slice.
                return None
            renderer_state = self._renderer_state_snapshot()
            future = self._world_state_executor.submit(
                _compute_world_states_worker,
                animation,
                time_key,
                atlases,
                renderer_state,
            )
            entry = {"time_key": time_key, "future": future, "states": states}
            self._world_state_cache[cache_key] = entry
        if future.done():
            try:
                states = future.result()
            except Exception:
                states = None
            if states is not None:
                with self._world_state_lock:
                    self._world_state_cache[cache_key] = {
                        "time_key": time_key,
                        "future": None,
                        "states": states,
                    }
                return states
        return states

    def _apply_fast_preview_mode(self) -> None:
        if hasattr(self, "preview_gl"):
            self._apply_fast_preview_to_widget(self.preview_gl)
        for widget in self._preview_extra_widgets:
            self._apply_fast_preview_to_widget(widget)
        if self._audio_only_dummy_widget:
            self._apply_fast_preview_to_widget(self._audio_only_dummy_widget)

    def _on_fast_preview_toggled(self, enabled: bool) -> None:
        self._fast_preview_enabled = bool(enabled)
        if self.main_window and getattr(self.main_window, "settings", None):
            try:
                self.main_window.settings.setValue(
                    "midi_editor/fast_preview", self._fast_preview_enabled
                )
            except Exception:
                pass
        self._apply_fast_preview_mode()
        self._update_preview_for_playhead()

    def _on_prebake_toggled(self, enabled: bool) -> None:
        self._prebake_world_states_enabled = bool(enabled)
        if self.main_window and getattr(self.main_window, "settings", None):
            try:
                self.main_window.settings.setValue(
                    "midi_editor/prebake_world_states", self._prebake_world_states_enabled
                )
            except Exception:
                pass
        with self._world_state_lock:
            self._prebaked_world_states_cache.clear()
            self._prebaked_world_states_futures.clear()
        self._update_preview_for_playhead()

    def _on_texture_scale_changed(self, index: int) -> None:
        try:
            scale = float(self.texture_scale_combo.itemData(index))
        except Exception:
            scale = 1.0
        self._preview_texture_scale = max(0.25, min(1.0, scale))
        if self.main_window and getattr(self.main_window, "settings", None):
            try:
                self.main_window.settings.setValue(
                    "midi_editor/texture_scale", self._preview_texture_scale
                )
            except Exception:
                pass
        # Reset preview caches so atlases reload at the new scale.
        self._preview_anim_cache.clear()
        self._preview_atlas_cache.clear()
        self._preview_anim_duration.clear()
        self._preview_baked_segments = []
        with self._world_state_lock:
            self._prebaked_world_states_cache.clear()
            self._prebaked_world_states_futures.clear()
        if hasattr(self, "preview_gl") and self.preview_gl:
            self.preview_gl.texture_atlases = []
        if self.midi_data and self.current_track_index is not None:
            track = self.midi_data.tracks[self.current_track_index]
            self._preview_segments = self._build_preview_segments(track, self.current_track_index)
            if not self._audio_only_preview:
                self._load_preview_for_track(track, self.current_track_index)
                self._rebake_preview_segments()
            if self._preview_multi_enabled:
                self._rebuild_preview_tracks()
        self._update_preview_for_playhead()

    def _is_track_hidden(self, track_index: int) -> bool:
        return track_index in self._hidden_track_indices

    def _on_track_visibility_toggled(self, track_index: int, visible: bool) -> None:
        if visible:
            self._hidden_track_indices.discard(track_index)
        else:
            self._hidden_track_indices.add(track_index)
        self.name_canvas.set_hidden_tracks(self._hidden_track_indices)
        if self._preview_multi_enabled and not self._multi_preview_same_viewport:
            container = self._preview_tile_by_track.get(track_index)
            if container is not None:
                container.setVisible(track_index not in self._hidden_track_indices)
        self._update_preview_for_playhead()

    def _on_timeline_track_selected(self, idx: int):
        self._select_track(idx)

    def _load_notes(self, notes: List[MidiNote]):
        self._loading_notes = True
        self.notes_table.setRowCount(0)
        if not self.midi_data:
            self._loading_notes = False
            return
        ticks_per_beat = max(1, int(self.midi_data.ticks_per_beat))
        for note in notes:
            row = self.notes_table.rowCount()
            self.notes_table.insertRow(row)
            start_beats = note.start_tick / ticks_per_beat
            length_beats = max(0.0, (note.end_tick - note.start_tick) / ticks_per_beat)
            self._set_table_item(row, 0, f"{start_beats:.3f}")
            self._set_table_item(row, 1, f"{length_beats:.3f}")
            self._set_table_item(row, 2, str(note.note))
            self._set_table_item(row, 3, str(note.channel))
            self._set_table_item(row, 4, str(note.velocity))
        self._loading_notes = False
        self.status_label.setText(f"{len(notes)} segments loaded.")
        self._refresh_timeline_segment_map()
        self.timeline_canvas.update()
        self.name_canvas.update()
        if self._suspend_preview_updates:
            return
        if self.current_track_index is not None and self.midi_data:
            track = self.midi_data.tracks[self.current_track_index]
            self._preview_track_has_notes = bool(track.notes)
            self._preview_segments = self._build_preview_segments(track, self.current_track_index)
            self._rebake_preview_segments()
            if self._preview_multi_enabled:
                self._rebuild_preview_tracks()
            self._update_preview_for_playhead()

    def _set_table_item(self, row: int, col: int, text: str):
        item = QTableWidgetItem(text)
        if col in (0, 1):
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.notes_table.setItem(row, col, item)

    def _add_note(self):
        if self.current_track_index is None or not self.midi_data:
            return
        track = self.midi_data.tracks[self.current_track_index]
        ticks_per_beat = max(1, int(self.midi_data.ticks_per_beat))
        note = MidiNote(start_tick=0, end_tick=ticks_per_beat, note=60, channel=0, velocity=100)
        track.notes.append(note)
        track.notes.sort(key=lambda n: (n.start_tick, n.end_tick))
        self._load_notes(track.notes)

    def _delete_note(self):
        if self.current_track_index is None or not self.midi_data:
            return
        row = self.notes_table.currentRow()
        if row < 0:
            return
        track = self.midi_data.tracks[self.current_track_index]
        if row >= len(track.notes):
            return
        del track.notes[row]
        self._load_notes(track.notes)

    def _on_note_item_changed(self, item: QTableWidgetItem):
        if self._loading_notes or self.current_track_index is None or not self.midi_data:
            return
        row = item.row()
        track = self.midi_data.tracks[self.current_track_index]
        if row < 0 or row >= len(track.notes):
            return
        note = track.notes[row]
        ticks_per_beat = max(1, int(self.midi_data.ticks_per_beat))
        try:
            start = float(self.notes_table.item(row, 0).text())
            length = float(self.notes_table.item(row, 1).text())
            note_num = int(float(self.notes_table.item(row, 2).text()))
            channel = int(float(self.notes_table.item(row, 3).text()))
            velocity = int(float(self.notes_table.item(row, 4).text()))
        except Exception:
            return

        start = max(0.0, start)
        length = max(0.0, length)
        note.start_tick = int(round(start * ticks_per_beat))
        note.end_tick = int(round((start + length) * ticks_per_beat))
        note.note = max(0, min(127, note_num))
        note.channel = max(0, min(15, channel))
        note.velocity = max(0, min(127, velocity))
        track.notes.sort(key=lambda n: (n.start_tick, n.end_tick))
        self._load_notes(track.notes)

    def _on_note_selection_changed(self) -> None:
        if self._loading_notes or not self.midi_data:
            return
        row = self.notes_table.currentRow()
        if row < 0:
            return
        start_item = self.notes_table.item(row, 0)
        if not start_item:
            return
        try:
            start_beats = float(start_item.text())
        except Exception:
            return
        self._preview_playhead_seconds = max(0.0, self._seconds_from_beats(start_beats))
        self._update_preview_for_playhead()

    # ------------------------------------------------------------------ Helpers

    def _on_bpm_changed(self, value: float):
        if self.midi_data:
            self.midi_data.set_bpm(value)
        if self.preview_gl and self.preview_gl.player.animation:
            self._update_preview_for_playhead()

    def _export_mp3(self) -> None:
        if not self.midi_data:
            QMessageBox.information(self, "Export MP3", "Load a MIDI file first.")
            return
        start_dir = os.path.dirname(self.path_edit.text().strip() or os.path.expanduser("~"))
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Audio as MP3",
            start_dir,
            "MP3 Audio (*.mp3);;All Files (*)",
        )
        if not filename:
            return
        if not filename.lower().endswith(".mp3"):
            filename += ".mp3"

        ffmpeg_path = self._resolve_ffmpeg_path_for_export()
        if not ffmpeg_path:
            QMessageBox.warning(
                self,
                "Export MP3",
                "FFmpeg was not found. Install it from Settings > Application > FFmpeg Tools.",
            )
            return

        try:
            mix = self._render_midi_audio_mix()
        except Exception as exc:
            QMessageBox.warning(self, "Export MP3", f"Failed to render audio:\n{exc}")
            return

        if mix is None:
            QMessageBox.information(self, "Export MP3", "No audio sources found to export.")
            return

        audio, sample_rate = mix
        tmp_dir = Path(tempfile.mkdtemp(prefix="msm_midi_audio_"))
        wav_path = tmp_dir / "midi_mix.wav"
        try:
            sf.write(str(wav_path), audio, sample_rate, subtype="PCM_16")
            ffmpeg_cmd = [
                ffmpeg_path,
                "-y",
                "-i",
                str(wav_path),
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                str(filename),
            ]
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "FFmpeg failed.")
            self.status_label.setText(f"Exported MP3: {os.path.basename(filename)}")
        except Exception as exc:
            QMessageBox.warning(self, "Export MP3", f"Failed to export MP3:\n{exc}")
        finally:
            try:
                if wav_path.exists():
                    wav_path.unlink()
                tmp_dir.rmdir()
            except Exception:
                pass

    def _set_enabled(self, enabled: bool):
        self.timeline_scroll.setEnabled(enabled)
        self.name_scroll.setEnabled(enabled)
        self.notes_table.setEnabled(enabled)
        self.add_note_btn.setEnabled(enabled)
        self.delete_note_btn.setEnabled(enabled)
        self.save_btn.setEnabled(enabled)
        self.save_as_btn.setEnabled(enabled)
        self.export_mp3_btn.setEnabled(enabled)
        self.bpm_spin.setEnabled(enabled)
        self.preview_play_btn.setEnabled(enabled)
        self.preview_stop_btn.setEnabled(enabled)
        self.preview_multi_checkbox.setEnabled(enabled)
        self.audio_only_checkbox.setEnabled(enabled)

    def _has_any_preview_animation(self) -> bool:
        if self._preview_multi_enabled:
            return any(
                state.widget.player.animation
                for state in self._preview_track_states
                if state and state.widget
            )
        return bool(self.preview_gl.player.animation)

    def _resolve_ffmpeg_path_for_export(self) -> Optional[str]:
        if self.main_window and hasattr(self.main_window, "_resolve_ffmpeg_path"):
            try:
                return self.main_window._resolve_ffmpeg_path()
            except Exception:
                pass
        stored_path = ""
        if self.main_window and getattr(self.main_window, "settings", None):
            try:
                stored_path = self.main_window.settings.value("ffmpeg/path", "", type=str)
            except Exception:
                stored_path = ""
        return resolve_ffmpeg_path(stored_path)

    @staticmethod
    def _ensure_channel_count(audio: np.ndarray, channels: int) -> np.ndarray:
        if audio.ndim == 1:
            audio = audio[:, None]
        if audio.shape[1] == channels:
            return audio
        if channels == 1:
            return np.mean(audio, axis=1, keepdims=True)
        if audio.shape[1] == 1 and channels == 2:
            return np.repeat(audio, 2, axis=1)
        if audio.shape[1] > channels:
            return audio[:, :channels]
        extra = channels - audio.shape[1]
        pad = np.zeros((audio.shape[0], extra), dtype=audio.dtype)
        return np.concatenate((audio, pad), axis=1)

    @staticmethod
    def _resample_audio(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if src_rate == dst_rate or audio.size == 0:
            return audio
        ratio = float(dst_rate) / float(src_rate)
        new_len = max(1, int(round(len(audio) * ratio)))
        positions = np.linspace(0, len(audio) - 1, new_len)
        idx_lower = np.floor(positions).astype(int)
        idx_upper = np.clip(idx_lower + 1, 0, len(audio) - 1)
        frac = (positions - idx_lower)[:, None]
        resampled = audio[idx_lower] * (1.0 - frac) + audio[idx_upper] * frac
        return resampled.astype(np.float32, copy=False)

    def _render_midi_audio_mix(self) -> Optional[Tuple[np.ndarray, int]]:
        total_seconds = self._total_timeline_seconds()
        if total_seconds <= 0.0:
            return None
        total_frames = 0
        target_rate: Optional[int] = None
        target_channels: int = 1

        if self._preview_multi_enabled:
            track_indices = list(self._visible_track_indices)
        else:
            track_indices = [self.current_track_index] if self.current_track_index is not None else []

        if not track_indices:
            return None

        # Build segment lists for tracks to match preview playback.
        track_segments: List[Tuple[int, List[PreviewSegmentBake]]] = []
        for idx in track_indices:
            track = self.midi_data.tracks[idx]
            track_name = track.name or ""
            token_hint = self._token_hint_for_track_name(track_name)
            segments = self._build_preview_segments(track, idx)
            idle_name = self._resolve_track_idle_anim(track, idx, segments)
            baked = self._bake_segments_for_track(segments, idle_name, token_hint=token_hint)
            if baked:
                track_segments.append((idx, baked))

        if not track_segments:
            return None

        # Determine sample rate / channels from first available audio cache.
        for _idx, baked in track_segments:
            for segment in baked:
                audio_cache = segment.audio_cache
                if audio_cache is None and segment.audio_path:
                    audio_cache = self._preview_audio_cache.get(segment.audio_path)
                if audio_cache is None and segment.anim_name:
                    self._load_preview_audio(
                        segment.anim_name,
                        audio_path=segment.audio_path,
                        cache_entry=segment.audio_cache,
                        playback=False,
                    )
                    audio_cache = self._preview_audio_cache.get(segment.audio_path)
                if audio_cache:
                    target_rate = int(audio_cache[1])
                    audio_data = audio_cache[0]
                    target_channels = audio_data.shape[1] if audio_data.ndim > 1 else 1
                    break
            if target_rate:
                break

        if not target_rate:
            return None

        total_frames = int(total_seconds * target_rate) + 1
        mix = np.zeros((total_frames, target_channels), dtype=np.float32)

        for _idx, baked in track_segments:
            for segment in baked:
                audio_cache = segment.audio_cache
                if audio_cache is None and segment.audio_path:
                    audio_cache = self._preview_audio_cache.get(segment.audio_path)
                if audio_cache is None and segment.anim_name:
                    self._load_preview_audio(
                        segment.anim_name,
                        audio_path=segment.audio_path,
                        cache_entry=segment.audio_cache,
                        playback=False,
                    )
                    audio_cache = self._preview_audio_cache.get(segment.audio_path)
                if audio_cache is None:
                    continue
                audio, sample_rate, _active_duration = audio_cache
                if audio is None or sample_rate <= 0:
                    continue
                if sample_rate != target_rate:
                    audio = self._resample_audio(audio, int(sample_rate), int(target_rate))
                audio = self._ensure_channel_count(audio, target_channels)

                start_seconds = self._seconds_from_beats(segment.start_beats)
                end_seconds = self._seconds_from_beats(segment.end_beats)
                if end_seconds <= start_seconds:
                    continue
                start_frame = int(round(start_seconds * target_rate))
                end_frame = min(total_frames, int(round(end_seconds * target_rate)))
                if end_frame <= start_frame:
                    continue
                frames_needed = end_frame - start_frame
                if len(audio) <= 0:
                    continue
                frames_available = min(frames_needed, len(audio))
                if frames_available <= 0:
                    continue
                mix[start_frame:start_frame + frames_available] += audio[:frames_available]

        peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        if peak > 1.0:
            mix *= (0.98 / peak)
        return mix, target_rate

    def _on_preview_multi_toggled(self, enabled: bool) -> None:
        self._preview_multi_enabled = bool(enabled)
        self._sync_preview_audio_settings()
        if self._preview_multi_enabled and self._multi_preview_same_viewport:
            self.preview_gl.player.animation = None
            self.preview_gl.player.duration = 0.0
            self.preview_gl.set_time(0.0)
            self.preview_gl.set_extra_render_entries([])
        self._rebuild_preview_tracks()
        self._update_preview_for_playhead()
        if self._preview_multi_enabled:
            if self.preview_audio:
                self.preview_audio.pause()
        else:
            if self.preview_audio_mixer:
                self.preview_audio_mixer.clear()
            if self._multi_preview_same_viewport:
                self.preview_gl.set_extra_render_entries([])
                if self.midi_data and self.current_track_index is not None and not self._audio_only_preview:
                    track = self.midi_data.tracks[self.current_track_index]
                    self._preview_segments = self._build_preview_segments(track, self.current_track_index)
                    self._load_preview_for_track(track, self.current_track_index)
                    self._rebake_preview_segments()

    def _on_audio_only_toggled(self, enabled: bool) -> None:
        self._audio_only_preview = bool(enabled)
        # Clear animation state when in audio-only mode.
        self.preview_gl.player.animation = None
        self.preview_gl.player.duration = 0.0
        self.preview_gl.set_time(0.0)
        self.preview_gl.set_extra_render_entries([])
        self._preview_current_anim_name = None
        self._preview_anim_cache.clear()
        self._preview_atlas_cache.clear()
        self._preview_anim_duration.clear()
        self._audio_preload_queue = []
        self._audio_preload_active = False
        self._audio_preload_pending_play = False
        if self.midi_data and self.current_track_index is not None:
            track = self.midi_data.tracks[self.current_track_index]
            self._preview_segments = self._build_preview_segments(track, self.current_track_index)
            if not self._audio_only_preview:
                self._load_preview_for_track(track, self.current_track_index)
            else:
                self._preview_idle_anim_name = None
                self.preview_status_label.setText("Preview: audio-only mode.")
            self._rebake_preview_segments()
            if self._preview_multi_enabled:
                self._rebuild_preview_tracks()
            self._update_preview_for_playhead()

    def _clear_preview_extra_widgets(self) -> None:
        for widget in self._preview_extra_widgets:
            try:
                widget.setParent(None)
                widget.deleteLater()
            except Exception:
                pass
        self._preview_extra_widgets = []
        for widget in self._audio_only_placeholder_widgets:
            try:
                widget.setParent(None)
                widget.deleteLater()
            except Exception:
                pass
        self._audio_only_placeholder_widgets = []
        # Remove any extra labels/containers from the layout (keep primary container)
        layout = self.preview_container_layout
        keep = {self.preview_primary_container}
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            widget = item.widget()
            if widget and widget not in keep:
                layout.takeAt(i)
                widget.setParent(None)
                widget.deleteLater()
        self._preview_tile_containers = [self.preview_primary_container]
        self._reflow_preview_tiles()

    def _rebuild_preview_tracks(self) -> None:
        self._clear_preview_extra_widgets()
        self._preview_track_states = []
        self._preview_tile_by_track = {}
        self._preview_bake_queue = []
        if not self.midi_data:
            return
        if self._audio_only_preview and self._audio_only_dummy_widget is None:
            self._audio_only_dummy_widget = OpenGLAnimationWidget(
                shader_registry=getattr(self.main_window, "shader_registry", None)
            )
            self._apply_fast_preview_to_widget(self._audio_only_dummy_widget)
        track_indices: List[int] = []
        if self._preview_multi_enabled:
            track_indices = list(self._visible_track_indices)
        elif self.current_track_index is not None:
            track_indices = [self.current_track_index]

        for idx in track_indices:
            track = self.midi_data.tracks[idx]
            track_name = track.name or f"Track {idx}"
            display_name = self._display_label_for_track(track_name, idx)
            is_primary = (idx == self.current_track_index)
            if is_primary:
                widget = self.preview_gl
                label = self.preview_primary_label
                label.setText(display_name)
                tile_container = self.preview_primary_container
                self._preview_tile_by_track[idx] = tile_container
                self._apply_fast_preview_to_widget(widget)
            else:
                if self._preview_multi_enabled and self._multi_preview_same_viewport:
                    widget = self.preview_gl
                    self._apply_fast_preview_to_widget(widget)
                else:
                    label = QLabel(display_name)
                    label.setStyleSheet("color: #ddd; font-size: 9pt;")
                    if self._audio_only_preview:
                        widget = self._audio_only_dummy_widget
                        placeholder = QLabel("Audio only")
                        placeholder.setStyleSheet("color: #888; font-size: 8pt;")
                        tile_container = self._build_preview_tile(label, placeholder)
                        self._audio_only_placeholder_widgets.append(placeholder)
                    else:
                        widget = OpenGLAnimationWidget(
                            shader_registry=getattr(self.main_window, "shader_registry", None)
                        )
                        widget.setMinimumHeight(200)
                        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                        self._apply_fast_preview_to_widget(widget)
                        tile_container = self._build_preview_tile(label, widget)
                        self._preview_extra_widgets.append(widget)
                    self._preview_tile_containers.append(tile_container)
                    if self._preview_multi_enabled and not self._multi_preview_same_viewport:
                        tile_container.setVisible(not self._is_track_hidden(idx))
                    self._preview_tile_by_track[idx] = tile_container

            if is_primary:
                state = PreviewTrackState(
                    track_index=idx,
                    track_name=track_name,
                    widget=widget,
                    segments=list(self._preview_segments),
                    baked_segments=list(self._preview_baked_segments),
                    current_anim_name=self._preview_current_anim_name,
                    active_segment=self._preview_active_segment,
                    audio_segment_idx=None,
                    idle_anim_name=self._preview_idle_anim_name,
                    track_has_notes=self._preview_track_has_notes,
                )
            else:
                if self._audio_only_preview:
                    state = PreviewTrackState(
                        track_index=idx,
                        track_name=track_name,
                        widget=self._audio_only_dummy_widget,
                        segments=[],
                        baked_segments=[],
                        current_anim_name=None,
                        active_segment=None,
                        audio_segment_idx=None,
                        idle_anim_name=None,
                        track_has_notes=bool(track.notes),
                    )
                else:
                    state = PreviewTrackState(
                        track_index=idx,
                        track_name=track_name,
                        widget=widget,
                        segments=[],
                        baked_segments=[],
                        current_anim_name=None,
                        active_segment=None,
                        audio_segment_idx=None,
                        idle_anim_name=None,
                        track_has_notes=bool(track.notes),
                    )
            self._preview_track_states.append(state)
        self._reflow_preview_tiles()
        if self._preview_multi_enabled and self._multi_preview_same_viewport and not self._audio_only_preview:
            for state in self._preview_track_states:
                if state.baked_segments:
                    continue
                self._enqueue_preview_bake(state)

    def _build_preview_tile(self, label: QLabel, widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(label)
        layout.addWidget(widget)
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return container

    def _preview_grid_columns(self) -> int:
        if not self._preview_multi_enabled:
            return 1
        viewport = self.preview_scroll.viewport() if self.preview_scroll else None
        width = viewport.width() if viewport else 0
        target = 320
        if width <= 0:
            return 2
        return max(1, min(4, int(width / max(1, target))))

    def _reflow_preview_tiles(self) -> None:
        layout = self.preview_container_layout
        if not layout or not self._preview_tile_containers:
            return
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            if item:
                layout.takeAt(i)
        columns = self._preview_grid_columns()
        for idx, container in enumerate(self._preview_tile_containers):
            row = idx // columns
            col = idx % columns
            layout.addWidget(container, row, col)

    def _build_preview_state_for_track(
        self, track: "MidiTrackData", track_index: int, widget: OpenGLAnimationWidget
    ) -> PreviewTrackState:
        track_name = track.name or f"Track {track_index}"
        self._set_track_loading_progress(track_index, 0.05, process_events=True)
        segments = self._build_preview_segments(track, track_index)
        self._set_track_loading_progress(track_index, 0.35, process_events=True)
        idle_name = self._resolve_track_idle_anim(track, track_index, segments)
        self._set_track_loading_progress(track_index, 0.6, process_events=True)
        token_hint = self._token_hint_for_track_name(track_name)
        baked_segments = self._bake_segments_for_track(segments, idle_name, token_hint=token_hint)
        self._set_track_loading_progress(track_index, 0.9, process_events=True)
        current_anim_name = None
        if baked_segments:
            first_segment = baked_segments[0]
            current_anim_name = first_segment.anim_name
            self._apply_preview_baked_segment(first_segment, widget=widget, set_current=False)
            widget.set_time(0.0)
        self._set_track_loading_progress(track_index, None, process_events=False)
        return PreviewTrackState(
            track_index=track_index,
            track_name=track_name,
            widget=widget,
            segments=segments,
            baked_segments=baked_segments,
            current_anim_name=current_anim_name,
            active_segment=None,
            audio_segment_idx=None,
            idle_anim_name=idle_name,
            track_has_notes=bool(track.notes),
        )

    def _resolve_track_idle_anim(
        self,
        track: "MidiTrackData",
        track_index: int,
        segments: List[Tuple[float, float, str]],
    ) -> Optional[str]:
        if self._audio_only_preview:
            return None
        track_name = (track.name or "").strip()
        if not track_name or not track_name.endswith("_Monster"):
            return None
        anim_name = None
        for segment in segments:
            if segment[2]:
                anim_name = segment[2]
                break
        if not anim_name:
            anim_name = self._build_animation_name(track_name, track_index)
        if not anim_name:
            return None
        token_hint = track_name.replace("_Monster", "")
        found = self._find_animation_in_bin(anim_name, token_hint=token_hint)
        if not found:
            return None
        _json_path, data, _anim_dict = found
        return self._resolve_idle_animation_name(data)

    def _prewarm_assets_for_segments(
        self,
        segments: List[Tuple[float, float, str]],
        idle_anim_name: Optional[str],
        *,
        token_hint: Optional[str] = None,
    ) -> None:
        unique_anims = {segment[2] for segment in segments if segment[2]}
        if idle_anim_name:
            unique_anims.add(idle_anim_name)
        for anim_name in sorted(unique_anims):
            self._ensure_preview_animation(anim_name, token_hint=token_hint)
        self._prewarm_preview_audio(unique_anims, token_hint=token_hint)

    def _bake_segments_for_track(
        self,
        segments: List[Tuple[float, float, str]],
        idle_anim_name: Optional[str],
        *,
        token_hint: Optional[str] = None,
    ) -> List[PreviewSegmentBake]:
        baked: List[PreviewSegmentBake] = []
        if not segments and not idle_anim_name:
            return baked
        total_beats = self._total_timeline_beats()
        if total_beats <= 0.0:
            total_beats = 4.0
        if not self._audio_only_preview:
            self._prewarm_assets_for_segments(segments, idle_anim_name, token_hint=token_hint)

        raw_segments = sorted(segments, key=lambda seg: (seg[0], seg[1]))
        cursor = 0.0
        baked_inputs: List[Tuple[float, float, str]] = []
        for start_beats, end_beats, anim_name in raw_segments:
            start = max(0.0, float(start_beats))
            end = max(start, float(end_beats))
            if start > cursor and idle_anim_name:
                baked_inputs.append((cursor, start, idle_anim_name))
            resolved_name = anim_name or idle_anim_name or ""
            if resolved_name:
                baked_inputs.append((start, end, resolved_name))
            cursor = max(cursor, end)
        if cursor < total_beats and idle_anim_name:
            baked_inputs.append((cursor, total_beats, idle_anim_name))
        if not baked_inputs and idle_anim_name:
            baked_inputs.append((0.0, total_beats, idle_anim_name))

        for start_beats, end_beats, anim_name in baked_inputs:
            resolved_name = anim_name or idle_anim_name or ""
            animation = None
            atlases = []
            duration = 0.0
            if resolved_name and not self._audio_only_preview:
                cached = self._preview_anim_cache_get(resolved_name, token_hint=token_hint)
                if cached:
                    animation = cached[0]
                    atlases = cached[1]
                duration = self._preview_anim_duration_get(resolved_name, token_hint=token_hint)
                if duration is None and animation:
                    duration = self._compute_animation_duration(animation)
                    self._preview_anim_duration_set(resolved_name, duration, token_hint=token_hint)
            cache_key = self._preview_cache_key(resolved_name, token_hint=token_hint) if resolved_name else ""
            audio_path = self._preview_audio_path_cache.get(cache_key) if resolved_name else None
            audio_cache = self._preview_audio_cache.get(audio_path) if audio_path else None
            baked.append(
                PreviewSegmentBake(
                    start_beats=start_beats,
                    end_beats=end_beats,
                    anim_name=resolved_name,
                    animation=animation,
                    atlases=atlases,
                    duration=float(duration or 0.0),
                    audio_path=audio_path,
                    audio_cache=audio_cache,
                )
            )
        return baked

    @staticmethod
    def _active_segment_for_beats_in(
        segments: List[PreviewSegmentBake],
        beats: float,
    ) -> Optional[Tuple[int, float, float]]:
        for idx, segment in enumerate(segments):
            if segment.start_beats <= beats < segment.end_beats:
                return (idx, segment.start_beats, segment.end_beats)
        return None

    def _update_preview_state_for_playhead(
        self,
        state: PreviewTrackState,
        beats: float,
    ) -> None:
        if self._audio_only_preview:
            return
        if not state.baked_segments:
            if state.track_has_notes and self._preview_multi_enabled:
                if not self._preview_playing:
                    return
                self._enqueue_preview_bake(state)
            if not state.track_has_notes and state.idle_anim_name:
                token_hint = self._token_hint_for_track_name(state.track_name)
                segment = PreviewSegmentBake(
                    start_beats=0.0,
                    end_beats=beats,
                    anim_name=state.idle_anim_name,
                    animation=None,
                    atlases=[],
                    duration=0.0,
                    audio_path=None,
                    audio_cache=None,
                )
                if state.idle_anim_name:
                    cached = self._preview_anim_cache_get(state.idle_anim_name, token_hint=token_hint)
                    if cached:
                        segment.animation = cached[0]
                        segment.atlases = cached[1]
                if segment.animation:
                    self._apply_preview_baked_segment(
                        segment,
                        widget=state.widget,
                        set_current=(state.widget is self.preview_gl),
                    )
                    state.current_anim_name = segment.anim_name
                    state.widget.set_time(0.0)
            return

        segment = self._active_segment_for_beats_in(state.baked_segments, beats)
        if not segment:
            state.active_segment = None
            if not state.track_has_notes and state.idle_anim_name:
                state.widget.set_time(0.0)
            return
        idx, start_beats, end_beats = segment
        baked_segment = state.baked_segments[idx]
        if baked_segment.anim_name and baked_segment.anim_name != state.current_anim_name:
            self._apply_preview_baked_segment(
                baked_segment,
                widget=state.widget,
                set_current=(state.widget is self.preview_gl),
            )
            state.current_anim_name = baked_segment.anim_name
        local_beats = beats - start_beats
        local_seconds = self._seconds_from_beats(local_beats)
        duration = float(state.widget.player.duration or 0.0)
        if duration > 0.0:
            local_anim_time = local_seconds % duration
        else:
            local_anim_time = 0.0
        state.widget.set_time(local_anim_time)
        state.active_segment = (idx, start_beats, end_beats)

    def _multi_view_offsets(self, count: int) -> List[float]:
        if count <= 1:
            return [0.0]
        width = float(self.preview_gl.width() or 1)
        column_width = width / max(1.0, float(count))
        offsets: List[float] = []
        for idx in range(count):
            target_center = (idx + 0.5) * column_width
            offsets.append(target_center - width / 2.0)
        return offsets

    def _preview_entry_for_state(
        self, state: PreviewTrackState, beats: float, offset_x: float
    ) -> Optional[dict]:
        if self._audio_only_preview:
            return None
        token_hint = self._token_hint_for_track_name(state.track_name)
        if not state.baked_segments:
            if state.track_has_notes and self._preview_multi_enabled and self._preview_playing:
                self._enqueue_preview_bake(state)
            if not state.track_has_notes and state.idle_anim_name:
                cached = self._preview_anim_cache_get(state.idle_anim_name, token_hint=token_hint)
                if not cached:
                    return None
                duration = self._preview_anim_duration_get(state.idle_anim_name, token_hint=token_hint)
                if duration is None and cached[0]:
                    duration = self._compute_animation_duration(cached[0])
                    self._preview_anim_duration_set(
                        state.idle_anim_name,
                        duration,
                        token_hint=token_hint,
                    )
                state.current_anim_name = state.idle_anim_name
                state.active_segment = (0, 0.0, beats)
                world_states = self._get_prebaked_world_states(
                    cached[0], 0.0, float(duration or 0.0), cached[1]
                )
                if world_states is None:
                    world_states = self._request_world_states(cached[0], 0.0, cached[1])
                return {
                    "animation": cached[0],
                    "atlases": cached[1],
                    "time": 0.0,
                    "duration": float(duration or 0.0),
                    "offset_x": offset_x,
                    "offset_y": 0.0,
                    "world_states": world_states,
                }
            return None

        segment = self._active_segment_for_beats_in(state.baked_segments, beats)
        if not segment:
            state.active_segment = None
            return None
        idx, start_beats, end_beats = segment
        baked_segment = state.baked_segments[idx]
        if not baked_segment.animation:
            return None
        state.active_segment = (idx, start_beats, end_beats)
        state.current_anim_name = baked_segment.anim_name
        local_beats = beats - start_beats
        local_seconds = self._seconds_from_beats(local_beats)
        duration = float(baked_segment.duration or 0.0)
        if duration > 0.0:
            local_anim_time = local_seconds % duration
        else:
            local_anim_time = 0.0
        world_states = self._get_prebaked_world_states(
            baked_segment.animation,
            local_anim_time,
            float(duration or 0.0),
            baked_segment.atlases,
        )
        if world_states is None:
            world_states = self._request_world_states(
                baked_segment.animation, local_anim_time, baked_segment.atlases
            )
        return {
            "animation": baked_segment.animation,
            "atlases": baked_segment.atlases,
            "time": local_anim_time,
            "duration": duration,
            "offset_x": offset_x,
            "offset_y": 0.0,
            "world_states": world_states,
        }

    def _on_timeline_zoom_changed(self, value: int):
        self.timeline_canvas.set_pixels_per_beat(value)
        self._update_timeline_viewport_metrics()

    def _sync_timeline_scroll(self, value: int):
        if self._scroll_syncing:
            return
        self._scroll_syncing = True
        self.timeline_scroll.verticalScrollBar().setValue(value)
        self._scroll_syncing = False

    def _on_timeline_hscroll(self, value: int) -> None:
        self._update_timeline_viewport_metrics()

    def _update_timeline_viewport_metrics(self) -> None:
        if not self.timeline_scroll or not self.timeline_scroll.viewport():
            return
        scroll_x = self.timeline_scroll.horizontalScrollBar().value()
        viewport_width = self.timeline_scroll.viewport().width()
        self.timeline_canvas.set_viewport_metrics(scroll_x, viewport_width)

    def eventFilter(self, obj, event):
        if self.timeline_scroll and obj is self.timeline_scroll.viewport():
            if event.type() == QEvent.Type.Resize:
                self._update_timeline_viewport_metrics()
        if self.preview_scroll and obj is self.preview_scroll.viewport():
            if event.type() == QEvent.Type.Resize:
                self._reflow_preview_tiles()
        return super().eventFilter(obj, event)

    def _sync_name_scroll(self, value: int):
        if self._scroll_syncing:
            return
        self._scroll_syncing = True
        self.name_scroll.verticalScrollBar().setValue(value)
        self._scroll_syncing = False

    def _parse_world_number(self, path: str) -> Optional[int]:
        base = os.path.basename(path).lower()
        match = re.search(r"world(\d+)", base)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _compute_visible_track_indices(self) -> List[int]:
        if not self.midi_data:
            return []
        monster_indices = [
            idx
            for idx, track in enumerate(self.midi_data.tracks)
            if (track.name or "").endswith("_Monster")
        ]
        if monster_indices:
            return monster_indices
        return list(range(len(self.midi_data.tracks)))

    def _refresh_timeline_segment_map(self) -> None:
        if not self.midi_data:
            self.timeline_canvas.set_segment_map({})
            return
        segment_map = {}
        for idx in self._visible_track_indices:
            track = self.midi_data.tracks[idx]
            segments = self._build_timeline_segments(track, idx)
            if segments:
                segment_map[idx] = segments
        self.timeline_canvas.set_segment_map(segment_map)

    def _build_timeline_segments(
        self, track: "MidiTrackData", track_index: int
    ) -> List[Tuple[float, float, Optional[int]]]:
        if not self.midi_data:
            return []
        track_name = (track.name or "").strip()
        if not track_name or not track_name.endswith("_Monster"):
            return []
        ticks_per_beat = max(1, int(self.midi_data.ticks_per_beat))
        anim_track = self._animation_track_for_monster(track_name)
        anim_notes = anim_track.notes if anim_track else []
        segments: List[Tuple[float, float, Optional[int]]] = []
        for note in track.notes:
            start_beats = note.start_tick / ticks_per_beat
            end_beats = note.end_tick / ticks_per_beat
            track_num = None
            if anim_notes:
                overlap = None
                for anim_note in anim_notes:
                    if anim_note.start_tick <= note.start_tick < anim_note.end_tick:
                        overlap = anim_note
                        break
                if not overlap:
                    for anim_note in anim_notes:
                        if not (anim_note.end_tick <= note.start_tick or anim_note.start_tick >= note.end_tick):
                            overlap = anim_note
                            break
                if overlap:
                    track_num = self._track_number_from_note(overlap.note)
            if track_num is None:
                track_num = self._track_number_from_monster_note(note.note)
            if track_num is None:
                track_num = 1
            segments.append((start_beats, end_beats, track_num))
        return segments

    def _track_occurrence_index(self, track_name: str, track_index: int) -> int:
        if not self.midi_data:
            return 1
        matches = [
            idx
            for idx, track in enumerate(self.midi_data.tracks)
            if (track.name or "") == track_name
        ]
        if track_index in matches:
            return matches.index(track_index) + 1
        return 1

    def _build_animation_name(self, track_name: str, track_index: int) -> Optional[str]:
        if self._world_number is None:
            return None
        occurrence = self._track_occurrence_index(track_name, track_index)
        return f"{self._world_number:02d}-{track_name}_{occurrence:02d}"

    def _animation_track_for_monster(self, monster_track_name: str) -> Optional["MidiTrackData"]:
        if not self.midi_data:
            return None
        if not monster_track_name.endswith("_Monster"):
            return None
        anim_name = monster_track_name.replace("_Monster", "_Animation")
        for track in self.midi_data.tracks:
            if (track.name or "") == anim_name:
                return track
        return None

    def _track_number_from_note(self, note_value: int) -> int:
        # MSM MIDI animation tracks commonly use 72 -> track 01, 73 -> track 02, etc.
        try:
            value = int(note_value)
        except (TypeError, ValueError):
            return 1
        return max(1, value - 71)

    def _track_number_from_monster_note(self, note_value: int) -> Optional[int]:
        try:
            value = int(note_value)
        except (TypeError, ValueError):
            return None
        track_num = value - 71
        if track_num <= 0:
            return None
        return track_num

    @staticmethod
    def _token_hint_for_track_name(track_name: str) -> Optional[str]:
        if not track_name:
            return None
        if track_name.endswith("_Monster"):
            return track_name.replace("_Monster", "")
        return None

    def _preview_cache_key(self, anim_name: str, *, token_hint: Optional[str] = None) -> str:
        if not anim_name:
            return anim_name
        base = anim_name.strip()
        if token_hint:
            return f"{token_hint}::{base}"
        return base

    def _preview_anim_cache_get(self, anim_name: str, *, token_hint: Optional[str] = None):
        key = self._preview_cache_key(anim_name, token_hint=token_hint)
        return self._preview_anim_cache.get(key)

    def _preview_anim_cache_set(
        self, anim_name: str, value: Tuple[object, list, str, dict], *, token_hint: Optional[str] = None
    ) -> None:
        key = self._preview_cache_key(anim_name, token_hint=token_hint)
        self._preview_anim_cache[key] = value

    def _preview_anim_duration_get(self, anim_name: str, *, token_hint: Optional[str] = None):
        key = self._preview_cache_key(anim_name, token_hint=token_hint)
        return self._preview_anim_duration.get(key)

    def _preview_anim_duration_set(
        self, anim_name: str, duration: float, *, token_hint: Optional[str] = None
    ) -> None:
        key = self._preview_cache_key(anim_name, token_hint=token_hint)
        self._preview_anim_duration[key] = duration

    def _load_track_bin_map(self, track_name: str) -> Optional[dict]:
        if self._world_number is None or not self.main_window:
            return None
        token = track_name.replace("_Monster", "")
        key = (self._world_number, token)
        if key in self._track_bin_cache:
            return self._track_bin_cache[key]
        target_name = f"{self._world_number:03d}_{token}.bin"
        bin_path = None
        xml_roots: List[str] = []
        if hasattr(self.main_window, "_all_xml_bin_roots"):
            try:
                xml_roots = list(self.main_window._all_xml_bin_roots())
            except Exception:
                xml_roots = []
        if not xml_roots:
            game_path = getattr(self.main_window, "game_path", None)
            if game_path:
                xml_roots = [os.path.join(game_path, "data", "xml_bin")]
        lower_target = target_name.lower()
        for root in xml_roots:
            if not os.path.isdir(root):
                continue
            candidate = os.path.join(root, target_name)
            if os.path.exists(candidate):
                bin_path = candidate
                break
            for entry in os.scandir(root):
                if not entry.is_file():
                    continue
                if entry.name.lower() == lower_target:
                    bin_path = entry.path
                    break
            if bin_path:
                break
        if not bin_path or not os.path.exists(bin_path):
            self._track_bin_cache[key] = None
            return None
        try:
            data = Path(bin_path).read_bytes()
        except Exception:
            self._track_bin_cache[key] = None
            return None
        strings = [s.decode("utf-8", "ignore") for s in re.findall(rb"[ -~]{3,}", data)]
        prefix = f"{self._world_number:02d}-{token}_monster_".lower()
        mapping: dict = {}
        for s in strings:
            cleaned = s.strip()
            lower = cleaned.lower()
            if not lower.startswith(prefix):
                continue
            suffix = cleaned.rsplit("_", 1)[-1]
            if suffix.isdigit():
                mapping[int(suffix)] = cleaned
        if not mapping:
            self._track_bin_cache[key] = None
            return None
        self._track_bin_cache[key] = mapping
        return mapping

    def _resolve_segment_anim_name(self, track_name: str, track_num: int) -> Optional[str]:
        if self._world_number is None:
            return None
        token_hint = track_name.replace("_Monster", "")
        if self._audio_only_preview:
            track_map = self._load_track_bin_map(track_name)
            if track_map and track_num in track_map:
                return track_map[track_num]
            return f"{self._world_number:02d}-{track_name}_{track_num:02d}"
        track_map = self._load_track_bin_map(track_name)
        if track_map and track_num in track_map:
            mapped_name = track_map[track_num]
            if self._find_animation_in_bin(mapped_name, token_hint=token_hint):
                return mapped_name
        candidate = f"{self._world_number:02d}-{track_name}_{track_num:02d}"
        if self._find_animation_in_bin(candidate, token_hint=token_hint):
            return candidate
        fallback = f"{self._world_number:02d}-{track_name}_01"
        if fallback != candidate and self._find_animation_in_bin(fallback, token_hint=token_hint):
            return fallback
        return None

    def _build_preview_segments(
        self, track: "MidiTrackData", track_index: int
    ) -> List[Tuple[float, float, str]]:
        if not self.midi_data:
            return []
        track_name = (track.name or "").strip()
        if not track_name or not track_name.endswith("_Monster"):
            return []
        ticks_per_beat = max(1, int(self.midi_data.ticks_per_beat))
        anim_track = self._animation_track_for_monster(track_name)
        anim_notes = anim_track.notes if anim_track else []
        segments: List[Tuple[float, float, str]] = []
        for note in track.notes:
            start_beats = note.start_tick / ticks_per_beat
            end_beats = note.end_tick / ticks_per_beat
            track_num = None
            if anim_notes:
                overlap = None
                for anim_note in anim_notes:
                    if anim_note.start_tick <= note.start_tick < anim_note.end_tick:
                        overlap = anim_note
                        break
                if not overlap:
                    # Fallback: any overlapping note
                    for anim_note in anim_notes:
                        if not (anim_note.end_tick <= note.start_tick or anim_note.start_tick >= note.end_tick):
                            overlap = anim_note
                            break
                if overlap:
                    track_num = self._track_number_from_note(overlap.note)
            if track_num is None:
                track_num = self._track_number_from_monster_note(note.note)
            if track_num is None:
                track_num = 1
            anim_name = self._resolve_segment_anim_name(track_name, track_num)
            segments.append((start_beats, end_beats, anim_name or ""))
        return segments

    def _find_animation_in_bin(self, anim_name: str, *, token_hint: Optional[str] = None):
        xml_roots: List[str] = []
        if self.main_window and hasattr(self.main_window, "_all_xml_bin_roots"):
            try:
                xml_roots = list(self.main_window._all_xml_bin_roots())
            except Exception:
                xml_roots = []
        if not xml_roots:
            game_path = getattr(self.main_window, "game_path", None)
            if game_path:
                xml_roots = [os.path.join(game_path, "data", "xml_bin")]
        if not token_hint:
            match = re.search(r"-([A-Za-z0-9]+)_Monster", anim_name)
            if match:
                token_hint = match.group(1)
            else:
                match = re.search(r"([A-Za-z0-9]+)_Monster", anim_name)
                if match:
                    token_hint = match.group(1)
        allow_bin_scan = bool(token_hint)
        root_key = tuple(os.path.normcase(os.path.normpath(p)) for p in xml_roots if p)
        cache_key = (anim_name, token_hint or "", root_key)
        if cache_key in self._animation_cache:
            return self._animation_cache[cache_key]
        if not xml_roots:
            self._animation_cache[cache_key] = None
            return None

        index_entries = None
        if self.main_window is not None:
            index_entries = getattr(self.main_window, "file_index", None)
            if index_entries is not None:
                index_entries = [
                    entry for entry in index_entries
                    if entry.full_path.lower().endswith((".json", ".bin"))
                ]

        def _scan_file(path: str):
            try:
                cached = self._get_cached_animation_resource(path, anim_name=anim_name)
                if not cached:
                    return None
                data, anim_index = cached
            except Exception:
                return None
            anim = anim_index.get(anim_name)
            if anim:
                return (path, data, anim)
            return None

        def _collect_entries() -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
            json_paths: List[Tuple[str, str]] = []
            bin_paths: List[Tuple[str, str]] = []
            if index_entries is not None:
                for entry in index_entries:
                    lower = entry.full_path.lower()
                    name = os.path.basename(entry.full_path).lower()
                    if lower.endswith(".json"):
                        json_paths.append((entry.full_path, name))
                    elif lower.endswith(".bin"):
                        bin_paths.append((entry.full_path, name))
                return json_paths, bin_paths
            for root in xml_roots:
                if not os.path.isdir(root):
                    continue
                for entry in os.scandir(root):
                    if not entry.is_file():
                        continue
                    lower = entry.name.lower()
                    if lower.endswith(".json"):
                        json_paths.append((entry.path, lower))
                    elif lower.endswith(".bin"):
                        bin_paths.append((entry.path, lower))
            return json_paths, bin_paths

        json_entries, bin_entries = _collect_entries()

        def _iter_entries(include_bins: bool):
            for path, name in json_entries:
                yield path, name
            if include_bins:
                for path, name in bin_entries:
                    yield path, name

        # Prefer files that match the monster token when possible
        if token_hint:
            token = token_hint.lower()
            prefix = f"monster_{token}"
            exact_names = {f"{prefix}.json", f"{prefix}.bin"}
            for path, name in _iter_entries(True):
                if name in exact_names:
                    result = _scan_file(path)
                    if result:
                        self._animation_cache[cache_key] = result
                        return result
            for path, name in _iter_entries(True):
                if not name.startswith(prefix):
                    continue
                result = _scan_file(path)
                if result:
                    self._animation_cache[cache_key] = result
                    return result
            # Final fallback: only scan files that contain the token in the filename.
            filtered = [(p, n) for p, n in _iter_entries(allow_bin_scan) if token in n]
            for path, _name in filtered:
                result = _scan_file(path)
                if result:
                    self._animation_cache[cache_key] = result
                    return result
            self._animation_cache[cache_key] = None
            return None

        for path, _name in _iter_entries(allow_bin_scan):
            result = _scan_file(path)
            if result:
                self._animation_cache[cache_key] = result
                return result

        self._animation_cache[cache_key] = None
        return None

    def _get_cached_animation_resource(
        self, path: str, *, anim_name: Optional[str] = None
    ) -> Optional[Tuple[dict, dict]]:
        if not path:
            return None
        track_index = self._active_loading_track
        norm = os.path.normcase(os.path.normpath(path))
        cached = self._animation_resource_cache.get(norm)
        if cached:
            self._animation_resource_cache.move_to_end(norm)
            return cached
        data = None
        if path.lower().endswith(".json"):
            if track_index is not None:
                self._set_track_loading_progress(track_index, 0.35, process_events=True)
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            if anim_name and anim_name not in text:
                return None
            data = json.loads(text)
        else:
            scan_cb = None
            parse_cb = None
            if track_index is not None:
                scan_cb = self._make_loading_progress_callback(track_index, 0.05, 0.45)
                parse_cb = self._make_loading_progress_callback(track_index, 0.55, 0.40)
            if anim_name and not self._bin_contains_anim_name(path, anim_name, progress_cb=scan_cb):
                return None
            if not self.main_window:
                return None
            data = self.main_window._load_animation_resource_dict(
                None,
                path,
                progress_callback=parse_cb,
            )
        if not data:
            return None
        anim_index: dict = {}
        for anim in data.get("anims", []):
            name = anim.get("name")
            if isinstance(name, str) and name:
                anim_index[name] = anim
        self._animation_resource_cache[norm] = (data, anim_index)
        if len(self._animation_resource_cache) > self._animation_resource_cache_limit:
            self._animation_resource_cache.popitem(last=False)
        return self._animation_resource_cache[norm]

    def _make_loading_progress_callback(
        self, track_index: int, base: float, span: float
    ):
        def _cb(fraction: float) -> None:
            if track_index is None:
                return
            try:
                value = max(0.0, min(1.0, float(fraction)))
            except (TypeError, ValueError):
                return
            self._set_track_loading_progress(
                track_index,
                base + value * span,
                process_events=True,
            )

        return _cb

    def _bin_contains_anim_name(
        self,
        path: str,
        anim_name: str,
        *,
        progress_cb=None,
    ) -> bool:
        try:
            needle = anim_name.encode("utf-8", "ignore")
        except Exception:
            return True
        if not needle:
            return True
        try:
            with open(path, "rb") as handle:
                tail = b""
                size = os.path.getsize(path)
                read_total = 0
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    read_total += len(chunk)
                    data = tail + chunk
                    if needle in data:
                        if progress_cb:
                            progress_cb(1.0)
                        return True
                    if progress_cb and size > 0:
                        progress_cb(min(1.0, read_total / size))
                    if len(needle) > 1:
                        tail = data[-len(needle) :]
                    else:
                        tail = b""
        except Exception:
            return True
        return False

    def _load_preview_for_track(self, track: "MidiTrackData", track_index: int) -> None:
        if not self.main_window:
            self._clear_preview("Preview: unavailable in this mode.")
            return
        track_name = (track.name or "").strip()
        if not track_name or not track_name.endswith("_Monster"):
            self._clear_preview("Preview: select a monster track.")
            return
        self.preview_primary_label.setText(track_name)
        if self._preview_multi_enabled and self._multi_preview_same_viewport:
            self.preview_gl.player.animation = None
            self.preview_gl.player.duration = 0.0
            self.preview_gl.set_time(0.0)
            return
        if self._audio_only_preview:
            self.preview_gl.player.animation = None
            self.preview_gl.player.duration = 0.0
            self.preview_gl.set_time(0.0)
            self._preview_current_anim_name = None
            self._preview_idle_anim_name = None
            self.preview_status_label.setText("Preview: audio-only mode.")
            return
        self._set_track_loading_progress(track_index, 0.02, process_events=True)
        anim_name = None
        if self._preview_segments:
            for segment in self._preview_segments:
                candidate = segment[2]
                if candidate:
                    anim_name = candidate
                    break
        if not anim_name:
            anim_name = self._build_animation_name(track_name, track_index)
        if not anim_name:
            self._clear_preview("Preview: world number not detected.")
            self._set_track_loading_progress(track_index, None, process_events=False)
            return
        self._active_loading_track = track_index
        try:
            if not self._set_preview_animation(anim_name):
                self._clear_preview(f"Preview: animation not found ({anim_name}).")
                self._set_track_loading_progress(track_index, None, process_events=False)
                return
            token_hint = self._token_hint_for_track_name(track_name)
            cached = self._preview_anim_cache_get(anim_name, token_hint=token_hint)
            data = cached[3] if cached else None
            self.preview_gl.set_time(0.0)
            self._preview_last_time = 0.0
            self._preview_playhead_seconds = 0.0
            self._preview_active_segment = None
            self._preview_audio_last_target = None
            self._preview_audio_segment_idx = None
            self.timeline_canvas.set_playhead_beats(0.0)
            self.preview_play_btn.setText("Play")
            self._load_preview_audio(anim_name)
            self.preview_status_label.setText(f"Preview: {anim_name}")
            self._preview_current_anim_name = anim_name
            self._preview_idle_anim_name = self._resolve_idle_animation_name(data or {})
            self._ensure_preview_textures()
            self._set_track_loading_progress(track_index, None, process_events=False)
        except Exception as exc:
            self._clear_preview(f"Preview load failed: {exc}")
            self._set_track_loading_progress(track_index, None, process_events=False)
        finally:
            self._active_loading_track = None

    def _clear_preview(self, message: str) -> None:
        self.preview_gl.player.playing = False
        self.preview_gl.player.animation = None
        self.preview_gl.player.duration = 0.0
        self.preview_gl.set_time(0.0)
        self._preview_last_time = 0.0
        self._preview_playhead_seconds = 0.0
        self._preview_active_segment = None
        self._preview_segments = []
        self._preview_baked_segments = []
        self._preview_current_anim_name = None
        self._preview_anim_cache.clear()
        self._preview_atlas_cache.clear()
        self._preview_anim_duration.clear()
        self._preview_idle_anim_name = None
        self._preview_audio_path = None
        self._preview_audio_anim_name = None
        self._preview_audio_last_target = None
        self._preview_audio_segment_idx = None
        self._preview_audio_cache.clear()
        self._preview_audio_path_cache.clear()
        self._preview_track_has_notes = False
        self._preview_track_states = []
        with self._world_state_lock:
            self._world_state_cache.clear()
            self._prebaked_world_states_cache.clear()
            self._prebaked_world_states_futures.clear()
        self._clear_preview_extra_widgets()
        self.preview_primary_label.setText("Preview Track")
        self.preview_play_btn.setText("Play")
        self.preview_status_label.setText(message)
        self.timeline_canvas.set_playhead_beats(0.0)
        self.preview_gl.set_extra_render_entries([])
        if self.preview_audio:
            self.preview_audio.clear()
        if self.preview_audio_mixer:
            self.preview_audio_mixer.clear()

    def _toggle_preview_playback(self) -> None:
        if not self.midi_data:
            return
        playing = not self._preview_playing
        self._set_preview_playing(playing)

    def _stop_preview_playback(self) -> None:
        if not self.midi_data:
            return
        self._set_preview_playing(False, reset_time=True)

    def _seconds_from_beats(self, beats: float) -> float:
        bpm = float(self.bpm_spin.value() or 120.0)
        if bpm <= 0.0:
            bpm = 120.0
        return beats * 60.0 / bpm

    def _beats_from_seconds(self, seconds: float) -> float:
        bpm = float(self.bpm_spin.value() or 120.0)
        if bpm <= 0.0:
            bpm = 120.0
        return seconds * bpm / 60.0

    def _on_preview_time_changed(self, current_time: float, duration: float) -> None:
        if self._preview_global_sync:
            self.preview_time_label.setText(f"Time: {self._preview_global_seconds:.2f}s")
            return
        beats = self._beats_from_seconds(current_time)
        self.timeline_canvas.set_playhead_beats(beats)
        self.preview_time_label.setText(f"Time: {current_time:.2f}s")
        if self.preview_audio and self.preview_audio.is_ready and self.preview_gl.player.playing:
            if current_time + 1e-4 < self._preview_last_time:
                self.preview_audio.restart()
            elif not self.preview_audio.is_playing():
                self.preview_audio.play(current_time)
        self._preview_last_time = current_time

    def _on_playhead_scrubbed(self, beats: float) -> None:
        if not self.midi_data:
            return
        seconds = self._seconds_from_beats(beats)
        self._preview_playhead_seconds = max(0.0, seconds)
        self._update_preview_for_playhead()
        if self._preview_multi_enabled:
            self._sync_preview_audio_multi(force_seek=True)
        else:
            self._sync_preview_audio(force_seek=True)

    def _resolve_preview_audio_path(self, anim_name: str) -> Optional[str]:
        if not self.main_window or not anim_name:
            return None
        try:
            # MIDI editor previews game-world tracks; do not inherit DOF context from
            # whatever is loaded in the main viewer.
            result = self.main_window._find_audio_for_animation(anim_name, force_dof=False)
        except Exception:
            return None
        if not isinstance(result, tuple) or not result:
            return None
        audio_path = result[0]
        return audio_path if isinstance(audio_path, str) and audio_path else None

    def _load_preview_audio(
        self,
        anim_name: str,
        *,
        audio_path: Optional[str] = None,
        cache_entry: Optional[Tuple[object, int, float]] = None,
        playback: bool = True,
    ) -> None:
        if not self.main_window:
            return
        token_hint = None
        if self.current_track_index is not None and self.midi_data:
            track = self.midi_data.tracks[self.current_track_index]
            token_hint = self._token_hint_for_track_name(track.name or "")
        cache_key = self._preview_cache_key(anim_name, token_hint=token_hint)
        if audio_path is None:
            audio_path = self._preview_audio_path_cache.get(cache_key)
            if audio_path is None and cache_key not in self._preview_audio_path_cache:
                audio_path = self._resolve_preview_audio_path(anim_name)
                self._preview_audio_path_cache[cache_key] = audio_path
        elif anim_name and cache_key not in self._preview_audio_path_cache:
            self._preview_audio_path_cache[cache_key] = audio_path
        if not audio_path:
            if self.preview_audio:
                self.preview_audio.clear()
            self._preview_audio_path = None
            self._preview_audio_anim_name = None
            return
        if audio_path == self._preview_audio_path and self.preview_audio.is_ready:
            self._preview_audio_anim_name = anim_name
            return
        if cache_entry is None:
            cache_entry = self._preview_audio_cache.get(audio_path)
        if cache_entry is None:
            cache_entry = AudioManager.build_cache_entry(audio_path)
            if cache_entry:
                self._preview_audio_cache[audio_path] = cache_entry
        loaded = False
        if cache_entry:
            loaded = self.preview_audio.load_cache_entry(cache_entry, file_path=audio_path)
        if not loaded:
            loaded = self.preview_audio.load_file(audio_path)
        if loaded:
            self._preview_audio_path = audio_path
            self._preview_audio_anim_name = anim_name
            self._preview_audio_last_target = None
            self._preview_audio_segment_idx = None
            if playback and not self._preview_multi_enabled:
                if self._preview_playing:
                    self.preview_audio.play(self._preview_global_seconds)
                else:
                    self.preview_audio.seek(self._preview_global_seconds)
        else:
            self.preview_audio.clear()
            self._preview_audio_path = None
            self._preview_audio_anim_name = None

    def _resolve_idle_animation_name(self, data: dict) -> Optional[str]:
        for anim in data.get("anims", []):
            name = anim.get("name", "")
            if isinstance(name, str) and name.lower() == "idle":
                return name
        return None

    def _ensure_preview_animation(
        self, anim_name: str, *, token_hint: Optional[str] = None
    ) -> Optional[Tuple[object, list]]:
        if self._audio_only_preview:
            return None
        if token_hint is None and self.current_track_index is not None and self.midi_data:
            track = self.midi_data.tracks[self.current_track_index]
            token_hint = self._token_hint_for_track_name(track.name or "")
        cached = self._preview_anim_cache_get(anim_name, token_hint=token_hint)
        if cached:
            return cached[0], cached[1]
        found = self._find_animation_in_bin(anim_name, token_hint=token_hint)
        if not found:
            return None
        json_path, data, anim_dict = found
        try:
            sources = data.get("sources", [])
            json_dir = os.path.dirname(json_path)
            use_cache = self._preview_texture_scale >= 0.999
            atlases = self.main_window._load_texture_atlases_for_sources(
                sources, json_dir=json_dir, use_cache=use_cache
            )
            blend_version = int(data.get("blend_version") or 1)
            animation = self.main_window._build_animation_struct(
                anim_dict,
                blend_version,
                json_path,
                resource_dict=data,
            )
            duration = self._preview_anim_duration_get(anim_name, token_hint=token_hint)
            if duration is None:
                duration = self._compute_animation_duration(animation)
                self._preview_anim_duration_set(anim_name, duration, token_hint=token_hint)
        except Exception:
            return None
        resolved_atlases = []
        for atlas in atlases:
            key = atlas.xml_path or atlas.source_name or ""
            norm = os.path.normcase(os.path.normpath(key)) if key else ""
            if norm:
                norm = f"{norm}|scale:{self._preview_texture_scale:.3f}"
            cached_atlas = self._preview_atlas_cache.get(norm) if norm else None
            if cached_atlas:
                cached_atlas.force_unpremultiply = atlas.force_unpremultiply
                cached_atlas.source_id = atlas.source_id
                self._apply_preview_texture_scale_to_atlas(cached_atlas)
                resolved_atlases.append(cached_atlas)
            else:
                self._apply_preview_texture_scale_to_atlas(atlas)
                resolved_atlases.append(atlas)
                if norm:
                    self._preview_atlas_cache[norm] = atlas
        self._preview_anim_cache_set(
            anim_name,
            (animation, resolved_atlases, json_path, data),
            token_hint=token_hint,
        )
        if duration:
            self._ensure_prebaked_world_states(animation, duration, resolved_atlases)
        return animation, resolved_atlases

    def _set_preview_animation(self, anim_name: str) -> bool:
        if self._audio_only_preview:
            return False
        if not anim_name:
            return False
        if anim_name == self._preview_current_anim_name:
            return True
        token_hint = None
        if self.current_track_index is not None and self.midi_data:
            track = self.midi_data.tracks[self.current_track_index]
            token_hint = self._token_hint_for_track_name(track.name or "")
        loaded = self._ensure_preview_animation(anim_name, token_hint=token_hint)
        if not loaded:
            return False
        animation, atlases = loaded
        self.preview_gl.texture_atlases = atlases
        self._apply_preview_animation(animation, anim_name, token_hint=token_hint)
        self._preview_current_anim_name = anim_name
        self._ensure_preview_textures()
        return True

    def _apply_preview_animation(
        self, animation: "AnimationData", anim_name: str, *, token_hint: Optional[str] = None
    ) -> None:
        duration = self._preview_anim_duration_get(anim_name, token_hint=token_hint)
        if duration is None:
            duration = self._compute_animation_duration(animation)
            self._preview_anim_duration_set(anim_name, duration, token_hint=token_hint)
        player = self.preview_gl.player
        player.animation = animation
        player.current_time = 0.0
        player.duration = duration
        player.loop = False
        player.playing = False

    def _apply_preview_baked_segment(
        self,
        segment: PreviewSegmentBake,
        *,
        widget: Optional[OpenGLAnimationWidget] = None,
        set_current: bool = True,
    ) -> None:
        if not segment or not segment.animation:
            return
        if widget is None:
            widget = self.preview_gl
        duration = float(segment.duration or 0.0)
        if duration <= 0.0:
            duration = self._compute_animation_duration(segment.animation)
            segment.duration = duration
            if segment.anim_name:
                self._preview_anim_duration[segment.anim_name] = duration
        widget.texture_atlases = segment.atlases
        player = widget.player
        player.animation = segment.animation
        player.current_time = 0.0
        player.duration = duration
        player.loop = False
        player.playing = False
        if set_current and widget is self.preview_gl:
            self._preview_current_anim_name = segment.anim_name
        self._ensure_widget_textures(widget)

    @staticmethod
    def _compute_animation_duration(animation: "AnimationData") -> float:
        if not animation:
            return 0.0
        max_time = 0.0
        global_lanes = getattr(animation, "global_keyframe_lanes", []) or []
        for lane in global_lanes:
            if lane and getattr(lane, "keyframes", None):
                last_keyframe = max(lane.keyframes, key=lambda k: k.time)
                max_time = max(max_time, last_keyframe.time)
        for layer in animation.layers:
            if layer.keyframes:
                last_keyframe = max(layer.keyframes, key=lambda k: k.time)
                max_time = max(max_time, last_keyframe.time)
            extra_lanes = getattr(layer, "extra_keyframe_lanes", []) or []
            for lane in extra_lanes:
                if lane and getattr(lane, "keyframes", None):
                    last_keyframe = max(lane.keyframes, key=lambda k: k.time)
                    max_time = max(max_time, last_keyframe.time)
        return max_time

    def _ensure_preview_textures(self) -> None:
        self._ensure_widget_textures(self.preview_gl)

    @staticmethod
    def _ensure_widget_textures(widget: OpenGLAnimationWidget) -> None:
        if widget.context() is None:
            return
        needs_upload = any(not atlas.texture_id for atlas in widget.texture_atlases)
        if not needs_upload:
            return
        widget.makeCurrent()
        for atlas in widget.texture_atlases:
            if not atlas.texture_id:
                atlas.load_texture()
        widget.doneCurrent()

    def _rebake_preview_segments(self) -> None:
        """Pre-bake segment assets so animation/audio switching is instant."""
        self._preview_baked_segments = []
        if not self._preview_segments and not self._preview_idle_anim_name:
            return
        total_beats = self._total_timeline_beats()
        if total_beats <= 0.0:
            total_beats = 4.0
        token_hint = None
        if self.current_track_index is not None and self.midi_data:
            track = self.midi_data.tracks[self.current_track_index]
            token_hint = self._token_hint_for_track_name(track.name or "")

        # Ensure caches are warm before building baked segment entries.
        self._prewarm_preview_assets()

        raw_segments = sorted(self._preview_segments, key=lambda seg: (seg[0], seg[1]))
        cursor = 0.0
        baked_inputs: List[Tuple[float, float, str]] = []
        for start_beats, end_beats, anim_name in raw_segments:
            start = max(0.0, float(start_beats))
            end = max(start, float(end_beats))
            if start > cursor and self._preview_idle_anim_name:
                baked_inputs.append((cursor, start, self._preview_idle_anim_name))
            resolved_name = anim_name or self._preview_idle_anim_name or ""
            if resolved_name:
                baked_inputs.append((start, end, resolved_name))
            cursor = max(cursor, end)
        if cursor < total_beats and self._preview_idle_anim_name:
            baked_inputs.append((cursor, total_beats, self._preview_idle_anim_name))
        if not baked_inputs and self._preview_idle_anim_name:
            baked_inputs.append((0.0, total_beats, self._preview_idle_anim_name))

        for start_beats, end_beats, anim_name in baked_inputs:
            resolved_name = anim_name or self._preview_idle_anim_name or ""
            animation = None
            atlases = []
            duration = 0.0
            if resolved_name:
                cached = self._preview_anim_cache_get(resolved_name, token_hint=token_hint)
                if cached:
                    animation = cached[0]
                    atlases = cached[1]
                duration = self._preview_anim_duration_get(resolved_name, token_hint=token_hint)
                if duration is None and animation:
                    duration = self._compute_animation_duration(animation)
                    self._preview_anim_duration_set(resolved_name, duration, token_hint=token_hint)
            cache_key = self._preview_cache_key(resolved_name, token_hint=token_hint) if resolved_name else ""
            audio_path = self._preview_audio_path_cache.get(cache_key) if resolved_name else None
            audio_cache = self._preview_audio_cache.get(audio_path) if audio_path else None
            self._preview_baked_segments.append(
                PreviewSegmentBake(
                    start_beats=start_beats,
                    end_beats=end_beats,
                    anim_name=resolved_name,
                    animation=animation,
                    atlases=atlases,
                    duration=float(duration or 0.0),
                    audio_path=audio_path,
                    audio_cache=audio_cache,
                )
            )

    def _prewarm_preview_assets(self) -> None:
        if not self._preview_segments and not self._preview_idle_anim_name:
            return
        unique_anims = {segment[2] for segment in self._preview_segments if segment[2]}
        if self._preview_idle_anim_name:
            unique_anims.add(self._preview_idle_anim_name)
        token_hint = None
        if self.current_track_index is not None and self.midi_data:
            track = self.midi_data.tracks[self.current_track_index]
            token_hint = self._token_hint_for_track_name(track.name or "")
        for anim_name in sorted(unique_anims):
            self._ensure_preview_animation(anim_name, token_hint=token_hint)
        self._prewarm_preview_audio(unique_anims, token_hint=token_hint)
        if self.preview_gl.context() is None:
            return
        if not self._preview_atlas_cache:
            return
        self.preview_gl.makeCurrent()
        for atlas in self._preview_atlas_cache.values():
            if not atlas.texture_id:
                atlas.load_texture()
        self.preview_gl.doneCurrent()

    def _prewarm_preview_audio(self, anim_names: set, *, token_hint: Optional[str] = None) -> None:
        if not self.main_window:
            return
        for anim_name in sorted(anim_names):
            cache_key = self._preview_cache_key(anim_name, token_hint=token_hint)
            audio_path = self._preview_audio_path_cache.get(cache_key)
            if audio_path is None and cache_key not in self._preview_audio_path_cache:
                audio_path = self._resolve_preview_audio_path(anim_name)
                self._preview_audio_path_cache[cache_key] = audio_path
            if not audio_path:
                continue
            if audio_path in self._preview_audio_cache:
                continue
            entry = AudioManager.build_cache_entry(audio_path)
            if entry:
                self._preview_audio_cache[audio_path] = entry

    def _set_preview_playing(self, playing: bool, *, reset_time: bool = False) -> None:
        if not self.midi_data:
            return
        self._sync_preview_audio_settings()
        if playing and self._audio_only_preview and not self._audio_preload_bypass:
            if not self._audio_preload_active:
                track_indices = list(self._visible_track_indices or [])
                if self._start_audio_preload(track_indices):
                    self._audio_preload_pending_play = True
                    self.preview_status_label.setText("Preloading audio...")
                    return
            else:
                self._audio_preload_pending_play = True
                self.preview_status_label.setText("Preloading audio...")
                return
        self._preview_playing = bool(playing)
        if reset_time:
            self._preview_playhead_seconds = 0.0
            self._preview_active_segment = None
            self._preview_audio_last_target = None
        if self._preview_playing:
            self._preview_last_tick = time.perf_counter()
            if not self._preview_timer.isActive():
                self._preview_timer.start()
            if self._audio_only_preview and self._preview_multi_enabled:
                for state in self._preview_track_states:
                    if state.track_has_notes and not state.baked_segments:
                        self._enqueue_preview_bake(state)
        else:
            if self._preview_timer.isActive():
                self._preview_timer.stop()
            if self._preview_multi_enabled:
                if self.preview_audio_mixer:
                    self.preview_audio_mixer.set_playing(False)
            else:
                if self.preview_audio and self.preview_audio.is_ready:
                    self.preview_audio.pause()
        if self._preview_multi_enabled:
            if self.preview_audio_mixer:
                self.preview_audio_mixer.set_playing(self._preview_playing)
        self.preview_play_btn.setText("Pause" if self._preview_playing else "Play")
        self._update_preview_for_playhead()
        if self._preview_multi_enabled:
            self._sync_preview_audio_multi(force_seek=True)
        else:
            self._sync_preview_audio(force_seek=True)

    def _on_preview_tick(self) -> None:
        if not self._preview_playing:
            return
        now = time.perf_counter()
        last = self._preview_last_tick or now
        delta = max(0.0, now - last)
        self._preview_last_tick = now
        if delta <= 0.0:
            return
        total_seconds = self._total_timeline_seconds()
        if total_seconds <= 0.0:
            return
        self._preview_playhead_seconds += delta
        if self._preview_playhead_seconds >= total_seconds:
            self._preview_playhead_seconds = self._preview_playhead_seconds % total_seconds
            self._preview_active_segment = None
            if self.preview_audio and self.preview_audio.is_ready:
                self.preview_audio.restart()
        self._preview_frame_accum += delta
        min_dt = 1.0 / max(1.0, float(self._preview_target_fps()))
        if self._preview_frame_accum < min_dt:
            return
        self._preview_frame_accum = 0.0
        self._update_preview_for_playhead()
        if self._preview_multi_enabled:
            self._sync_preview_audio_multi()
        else:
            self._sync_preview_audio()

    def _preview_target_fps(self) -> int:
        return 60

    def _total_timeline_seconds(self) -> float:
        if not self.midi_data:
            return 0.0
        max_tick = 0
        for track in self.midi_data.tracks:
            for note in track.notes:
                if note.end_tick > max_tick:
                    max_tick = note.end_tick
        if max_tick <= 0:
            return 0.0
        ticks_per_beat = max(1, int(self.midi_data.ticks_per_beat))
        beats = max_tick / ticks_per_beat
        return self._seconds_from_beats(beats)

    def _total_timeline_beats(self) -> float:
        if not self.midi_data:
            return 0.0
        max_tick = 0
        for track in self.midi_data.tracks:
            for note in track.notes:
                if note.end_tick > max_tick:
                    max_tick = note.end_tick
        ticks_per_beat = max(1, int(self.midi_data.ticks_per_beat))
        if max_tick <= 0:
            return 0.0
        return max_tick / ticks_per_beat

    def _active_segment_for_beats(self, beats: float) -> Optional[Tuple[int, float, float]]:
        segments = self._preview_baked_segments if self._preview_baked_segments else self._preview_segments
        for idx, segment in enumerate(segments):
            if isinstance(segment, PreviewSegmentBake):
                start = segment.start_beats
                end = segment.end_beats
            else:
                start, end, _anim = segment
            if start <= beats < end:
                return (idx, start, end)
        return None

    def _update_preview_for_playhead(self) -> None:
        if not self.midi_data:
            return
        beats = self._beats_from_seconds(self._preview_playhead_seconds)
        self._preview_global_seconds = self._preview_playhead_seconds
        self.timeline_canvas.set_playhead_beats(beats)
        self.preview_time_label.setText(f"Time: {self._preview_global_seconds:.2f}s")

        if self._preview_multi_enabled and self._preview_track_states:
            if self._multi_preview_same_viewport:
                visible_states = [
                    state for state in self._preview_track_states
                    if not self._is_track_hidden(state.track_index)
                ]
                offsets = self._multi_view_offsets(len(visible_states))
                entries: List[dict] = []
                primary_state = None
                for idx, state in enumerate(visible_states):
                    if state.track_index == self.current_track_index and primary_state is None:
                        primary_state = state
                    entry = self._preview_entry_for_state(state, beats, offsets[idx])
                    if entry:
                        entries.append(entry)
                self.preview_gl.set_extra_render_entries(entries)
                if primary_state:
                    self._preview_active_segment = primary_state.active_segment
                return
            primary_state = None
            for state in self._preview_track_states:
                self._update_preview_state_for_playhead(state, beats)
                if state.widget is self.preview_gl:
                    primary_state = state
            # Sync primary state back to audio tracking
            if primary_state:
                self._preview_active_segment = primary_state.active_segment
                if primary_state.active_segment:
                    idx = primary_state.active_segment[0]
                    if 0 <= idx < len(primary_state.baked_segments):
                        baked_segment = primary_state.baked_segments[idx]
                        anim_name = baked_segment.anim_name
                        if anim_name and anim_name != self._preview_audio_anim_name:
                            self._load_preview_audio(
                                anim_name,
                                audio_path=baked_segment.audio_path,
                                cache_entry=baked_segment.audio_cache,
                                playback=False,
                            )
            return

        if self.preview_gl.extra_render_entries:
            self.preview_gl.set_extra_render_entries([])

        if self._audio_only_preview:
            segment = self._active_segment_for_beats(beats)
            if not segment:
                self._preview_active_segment = None
                return
            idx, start_beats, end_beats = segment
            anim_name = None
            baked_segment = None
            if self._preview_baked_segments and 0 <= idx < len(self._preview_baked_segments):
                baked_segment = self._preview_baked_segments[idx]
                anim_name = baked_segment.anim_name
                if anim_name and anim_name != self._preview_audio_anim_name:
                    self._load_preview_audio(
                        anim_name,
                        audio_path=baked_segment.audio_path,
                        cache_entry=baked_segment.audio_cache,
                    )
            else:
                if 0 <= idx < len(self._preview_segments):
                    anim_name = self._preview_segments[idx][2]
                if anim_name and anim_name != self._preview_audio_anim_name:
                    self._load_preview_audio(anim_name)
            self._preview_active_segment = (idx, start_beats, end_beats)
            return

        if not self.preview_gl.player.animation:
            return

        segment = self._active_segment_for_beats(beats)
        if not segment:
            if not self._preview_track_has_notes:
                if self._preview_idle_anim_name:
                    self._set_preview_animation(self._preview_idle_anim_name)
                self.preview_gl.set_time(0.0)
            self._preview_active_segment = None
            return

        idx, start_beats, end_beats = segment
        anim_name = None
        baked_segment = None
        if self._preview_baked_segments and 0 <= idx < len(self._preview_baked_segments):
            baked_segment = self._preview_baked_segments[idx]
            anim_name = baked_segment.anim_name
            if anim_name and anim_name != self._preview_current_anim_name:
                self._apply_preview_baked_segment(baked_segment)
            if anim_name and anim_name != self._preview_audio_anim_name:
                self._load_preview_audio(
                    anim_name,
                    audio_path=baked_segment.audio_path,
                    cache_entry=baked_segment.audio_cache,
                )
        else:
            if 0 <= idx < len(self._preview_segments):
                anim_name = self._preview_segments[idx][2]
            if anim_name:
                self._set_preview_animation(anim_name)
                if anim_name != self._preview_audio_anim_name:
                    self._load_preview_audio(anim_name)
        local_beats = beats - start_beats
        local_seconds = self._seconds_from_beats(local_beats)
        duration = float(self.preview_gl.player.duration or 0.0)
        if duration > 0.0:
            local_anim_time = local_seconds % duration
        else:
            local_anim_time = 0.0
        self.preview_gl.set_time(local_anim_time)

        self._preview_active_segment = (idx, start_beats, end_beats)

    def _sync_preview_audio(self, *, force_seek: bool = False) -> None:
        if not self.preview_audio or not self.preview_audio.is_ready:
            return
        beats = self._beats_from_seconds(self._preview_global_seconds)
        segment = self._active_segment_for_beats(beats)
        if not segment:
            if self._preview_playing:
                self.preview_audio.pause()
            return
        idx, start_beats, _end_beats = segment
        local_seconds = self._seconds_from_beats(beats - start_beats)

        if not self._preview_playing:
            if (
                force_seek
                or self._preview_audio_last_target is None
                or abs(local_seconds - self._preview_audio_last_target) > 1e-3
            ):
                self.preview_audio.seek(local_seconds)
                self._preview_audio_last_target = local_seconds
                self._preview_audio_segment_idx = idx
            return

        segment_changed = (self._preview_audio_segment_idx != idx)
        if force_seek or segment_changed or not self.preview_audio.is_playing():
            self.preview_audio.play(local_seconds)
            self._preview_audio_last_target = local_seconds
            self._preview_audio_segment_idx = idx
            self._preview_audio_resync_at = time.perf_counter() + 0.25
            return

        now = time.perf_counter()
        if now >= self._preview_audio_resync_at:
            audio_pos = self.preview_audio.current_position
            if abs(audio_pos - local_seconds) >= 0.06:
                self.preview_audio.seek(local_seconds)
                self._preview_audio_resync_at = now + 0.25
            self._preview_audio_last_target = local_seconds

    def _sync_preview_audio_multi(self, *, force_seek: bool = False) -> None:
        if not self.preview_audio_mixer:
            return
        beats = self._beats_from_seconds(self._preview_global_seconds)
        active_ids: set = set()
        for state in self._preview_track_states:
            if not state.baked_segments:
                if self._audio_only_preview and state.track_has_notes:
                    self._enqueue_preview_bake(state)
                self.preview_audio_mixer.remove_voice(state.track_index)
                continue
            segment = self._active_segment_for_beats_in(state.baked_segments, beats)
            if not segment:
                self.preview_audio_mixer.remove_voice(state.track_index)
                state.audio_segment_idx = None
                continue
            idx, start_beats, _end_beats = segment
            if idx < 0 or idx >= len(state.baked_segments):
                self.preview_audio_mixer.remove_voice(state.track_index)
                state.audio_segment_idx = None
                continue
            baked_segment = state.baked_segments[idx]
            audio_cache = baked_segment.audio_cache
            if audio_cache is None and baked_segment.audio_path:
                audio_cache = self._preview_audio_cache.get(baked_segment.audio_path)
            if audio_cache is None and baked_segment.anim_name:
                self._load_preview_audio(
                    baked_segment.anim_name,
                    audio_path=baked_segment.audio_path,
                    cache_entry=baked_segment.audio_cache,
                    playback=False,
                )
                audio_cache = self._preview_audio_cache.get(baked_segment.audio_path)
            if audio_cache is None:
                self.preview_audio_mixer.remove_voice(state.track_index)
                state.audio_segment_idx = None
                continue
            local_seconds = self._seconds_from_beats(beats - start_beats)
            segment_changed = (state.audio_segment_idx != idx)
            ok = self.preview_audio_mixer.update_voice(
                state.track_index,
                audio_cache[0],
                audio_cache[1],
                local_seconds,
                self._preview_playing,
                force_seek=force_seek or segment_changed,
            )
            if ok:
                active_ids.add(state.track_index)
                state.audio_segment_idx = idx
            else:
                self.preview_audio_mixer.remove_voice(state.track_index)
                state.audio_segment_idx = None
        self.preview_audio_mixer.prune_voices(active_ids)

    def closeEvent(self, event):
        if self.preview_audio:
            self.preview_audio.clear()
        if self.preview_audio_mixer:
            self.preview_audio_mixer.clear()
        if self._preview_timer.isActive():
            self._preview_timer.stop()
        if self._portrait_loader:
            try:
                self._portrait_loader.shutdown()
            except Exception:
                pass
        if getattr(self, "_world_state_executor", None):
            try:
                self._world_state_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        return super().closeEvent(event)
