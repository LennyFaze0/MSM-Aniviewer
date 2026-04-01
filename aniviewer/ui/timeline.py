"""
Timeline Widget
Provides playback controls and timeline scrubbing with keyframe markers.
"""

import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QSlider,
    QLabel,
    QCheckBox,
    QScrollBar,
    QScrollArea,
    QToolButton,
    QSizePolicy,
    QApplication,
    QComboBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QPainterPath, QPixmap

_QINT32_MIN = -2147483648
_QINT32_MAX = 2147483647


class KeyframeMarkerBar(QWidget):
    """Custom bar that renders keyframe markers above the timeline slider."""

    markerClicked = pyqtSignal(float)
    markerRemoveRequested = pyqtSignal(list)
    markerDragRequested = pyqtSignal(list, float)
    selectionChanged = pyqtSignal(list)
    zoomRequested = pyqtSignal(float, float)
    beatMarkerDragged = pyqtSignal(int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._markers: List[float] = []
        self._duration: float = 0.0
        self._current_time: float = 0.0
        self._view_start: float = 0.0
        self._view_duration: float = 0.0
        self._compact_ui = False
        self.setMinimumHeight(24)
        self.setMaximumHeight(28)
        self.setMouseTracking(True)
        self._drag_active = False
        self._drag_moved = False
        self._drag_reference_time = 0.0
        self._drag_preview_delta = 0.0
        self._drag_origin_times: List[float] = []
        self._selected_markers: List[float] = []
        self._box_selecting = False
        self._box_origin = QPointF()
        self._box_current = QPointF()
        self._beat_markers: List[float] = []
        self._beat_grid_enabled: bool = False
        self._beat_edit_enabled: bool = False
        self._beat_drag_active: bool = False
        self._beat_drag_index: int = -1
        self._beat_drag_origin: float = 0.0
        self._beat_drag_preview: float = 0.0

    def set_markers(self, markers: List[float], duration: float):
        self._markers = sorted(markers or [])
        self._duration = max(0.0, float(duration))
        if self._view_duration <= 0.0 or self._view_duration > self._duration:
            self._view_start = 0.0
            self._view_duration = max(self._duration, 1e-6)
        self._prune_selection()
        self.update()

    def set_view_window(self, start: float, duration: float):
        duration = max(1e-6, float(duration))
        if self._duration > 0.0:
            start = min(max(0.0, start), max(0.0, self._duration - duration))
        else:
            start = 0.0
        self._view_start = start
        self._view_duration = duration
        self.update()

    def set_current_time(self, time_value: float):
        self._current_time = max(0.0, float(time_value))
        self.update()

    def set_selected_markers(self, markers: List[float]):
        snapped = self._snap_markers(markers)
        self._update_selection(snapped, emit=False)

    def set_compact_ui(self, enabled: bool):
        self._compact_ui = bool(enabled)
        if self._compact_ui:
            self.setMinimumHeight(18)
            self.setMaximumHeight(22)
        else:
            self.setMinimumHeight(24)
            self.setMaximumHeight(28)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, self.palette().window())

        baseline_y = rect.height() - 6
        pen = QPen(QColor(120, 120, 120))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawLine(4, baseline_y, rect.width() - 4, baseline_y)

        if self._beat_grid_enabled and self._view_duration > 0.0 and self._beat_markers:
            self._paint_beat_grid(painter, rect, baseline_y)

        if self._view_duration <= 0.0 or not self._markers:
            return

        highlight_color = self.palette().color(self.palette().ColorRole.Highlight)
        highlight_pen = QPen(highlight_color)
        highlight_pen.setWidth(2)
        marker_brush = QColor(180, 180, 180)
        highlight_brush = highlight_color
        tolerance = max(1e-6, self._view_duration * 1e-4)

        for marker in self._markers:
            if marker < self._view_start - tolerance or marker > self._view_start + self._view_duration + tolerance:
                continue
            render_time = marker
            if self._drag_active and self._is_drag_target(marker):
                render_time = self._clamp_time(marker + self._drag_preview_delta)
            x = self._time_to_x(render_time)
            selected = self._is_selected(marker)
            painter.setPen(highlight_pen if selected else pen)
            painter.setBrush(highlight_brush if selected else marker_brush)
            path = self._triangle_path(x, baseline_y - 1, 6)
            painter.drawPath(path)

        if self._box_selecting:
            painter.setPen(QPen(highlight_color, 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(highlight_color.red(), highlight_color.green(), highlight_color.blue(), 60))
            rect = self._selection_rect()
            if rect:
                painter.drawRect(rect)

    def mousePressEvent(self, event):
        if not self._markers or self._view_duration <= 0.0:
            return super().mousePressEvent(event)
        click_x = event.position().x()
        if (
            self._beat_grid_enabled
            and self._beat_edit_enabled
            and self._view_duration > 0.0
            and self._beat_markers
            and event.button() == Qt.MouseButton.LeftButton
        ):
            beat_index, beat_distance = self._locate_beat_marker(click_x)
            if beat_index is not None and beat_distance is not None and beat_distance <= 6:
                self._beat_drag_active = True
                self._beat_drag_index = beat_index
                self._beat_drag_origin = self._beat_markers[beat_index]
                self._beat_drag_preview = 0.0
                self.update()
                return
        closest_time, closest_distance = self._locate_marker(click_x)
        modifiers = event.modifiers()
        if closest_time is not None and closest_distance is not None and closest_distance <= 8:
            if event.button() == Qt.MouseButton.RightButton:
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    self._toggle_selection([closest_time])
                    return
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self._add_selection([closest_time])
                    return
                targets = (
                    list(self._selected_markers)
                    if self._is_selected(closest_time) and self._selected_markers
                    else [closest_time]
                )
                self.markerRemoveRequested.emit(targets)
                return
            if event.button() == Qt.MouseButton.LeftButton:
                if modifiers & Qt.KeyboardModifier.ControlModifier:
                    self._toggle_selection([closest_time])
                    return
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self._add_selection([closest_time])
                    return
                if not self._is_selected(closest_time):
                    self._replace_selection([closest_time])
                self._drag_active = True
                self._drag_moved = False
                self._drag_reference_time = closest_time
                self._drag_preview_delta = 0.0
                self._drag_origin_times = list(self._selected_markers) or [closest_time]
                self.update()
                return
        if event.button() == Qt.MouseButton.LeftButton:
            self._box_selecting = True
            self._box_origin = event.position()
            self._box_current = event.position()
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._beat_drag_active and self._view_duration > 0.0:
            new_time = self._clamp_time(self._x_to_time(event.position().x()))
            delta = new_time - self._beat_drag_origin
            if abs(delta - self._beat_drag_preview) > max(1e-6, self._view_duration * 1e-5):
                self._beat_drag_preview = delta
                self.update()
            return
        if self._drag_active and self._view_duration > 0.0:
            new_time = self._clamp_time(self._x_to_time(event.position().x()))
            delta = new_time - self._drag_reference_time
            if abs(delta - self._drag_preview_delta) > max(1e-6, self._view_duration * 1e-5):
                self._drag_preview_delta = delta
                self._drag_moved = True
                self.update()
            return
        if self._box_selecting:
            self._box_current = event.position()
            self.update()
            return
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._beat_drag_active and event.button() == Qt.MouseButton.LeftButton:
            delta = self._beat_drag_preview
            beat_index = self._beat_drag_index
            origin = self._beat_drag_origin
            self._beat_drag_active = False
            self._beat_drag_index = -1
            self._beat_drag_preview = 0.0
            self.update()
            if beat_index >= 0:
                target_time = self._clamp_time(origin + delta)
                self.beatMarkerDragged.emit(beat_index, target_time)
            return
        if self._drag_active and event.button() == Qt.MouseButton.LeftButton:
            delta = self._drag_preview_delta
            moved = self._drag_moved and abs(delta) > max(1e-6, self._view_duration * 1e-5)
            self._drag_active = False
            self._drag_moved = False
            origin_time = self._drag_reference_time
            origin_targets = list(self._drag_origin_times)
            self._drag_origin_times = []
            self._drag_preview_delta = 0.0
            self.update()
            if moved:
                self.markerDragRequested.emit(origin_targets or [origin_time], delta)
            else:
                self.markerClicked.emit(origin_time)
            return
        if self._box_selecting and event.button() == Qt.MouseButton.LeftButton:
            rect = self._selection_rect()
            self._box_selecting = False
            targets = []
            if rect:
                targets = self._markers_in_rect(rect)
            mode = self._selection_mode(event.modifiers())
            if targets or mode == "replace":
                self._apply_selection_change(targets, mode)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self._view_duration <= 0.0 or self._duration <= 0.0:
            return super().wheelEvent(event)
        delta = event.angleDelta().y()
        if delta == 0:
            return super().wheelEvent(event)
        factor = 0.85 if delta > 0 else 1.15
        target_time = self._x_to_time(event.position().x())
        self.zoomRequested.emit(factor, target_time)
        event.accept()

    def _time_to_x(self, time_value: float) -> float:
        usable_width = max(1.0, self.width() - 8.0)
        if self._view_duration <= 0.0:
            return 4.0
        normalized = (time_value - self._view_start) / self._view_duration
        clamped = min(max(normalized, 0.0), 1.0)
        return 4.0 + clamped * usable_width

    def _x_to_time(self, x: float) -> float:
        usable_width = max(1.0, self.width() - 8.0)
        normalized = min(max((x - 4.0) / usable_width, 0.0), 1.0)
        return self._view_start + normalized * max(self._view_duration, 1e-6)

    def _locate_marker(self, click_x: float):
        closest_time = None
        closest_distance = None
        for marker in self._markers:
            if marker < self._view_start or marker > self._view_start + self._view_duration:
                continue
            distance = abs(click_x - self._time_to_x(marker))
            if closest_distance is None or distance < closest_distance:
                closest_distance = distance
                closest_time = marker
        return closest_time, closest_distance

    @staticmethod
    def _triangle_path(center_x: float, base_y: float, size: float):
        half = size / 2.0
        points = [
            QPointF(center_x - half, base_y),
            QPointF(center_x + half, base_y),
            QPointF(center_x, base_y - size),
        ]
        path = QPainterPath()
        path.moveTo(points[0])
        path.lineTo(points[1])
        path.lineTo(points[2])
        path.closeSubpath()
        return path

    def _clamp_time(self, value: float) -> float:
        return min(max(value, 0.0), max(self._duration, 0.0))

    def _time_epsilon(self) -> float:
        return max(1e-5, self._duration * 1e-4)

    def _is_selected(self, time_value: float) -> bool:
        eps = self._time_epsilon()
        return any(abs(time_value - selected) <= eps for selected in self._selected_markers)

    def _is_drag_target(self, time_value: float) -> bool:
        eps = self._time_epsilon()
        return any(abs(time_value - target) <= eps for target in self._drag_origin_times)

    def _selection_rect(self) -> Optional[QRectF]:
        if not self._box_selecting:
            return None
        x1 = self._box_origin.x()
        y1 = self._box_origin.y()
        x2 = self._box_current.x()
        y2 = self._box_current.y()
        if abs(x2 - x1) < 1 and abs(y2 - y1) < 1:
            return None
        left = min(x1, x2)
        right = max(x1, x2)
        top = min(y1, y2)
        bottom = max(y1, y2)
        return QRectF(left, top, right - left, bottom - top)

    def _markers_in_rect(self, rect: QRectF) -> List[float]:
        if rect.width() <= 0 or rect.height() <= 0:
            return []
        selected: List[float] = []
        for marker in self._markers:
            x = self._time_to_x(marker)
            if rect.left() <= x <= rect.right() and rect.top() <= self.height() and rect.bottom() >= 0:
                selected.append(marker)
        return selected

    def _selection_mode(self, modifiers: Qt.KeyboardModifier) -> str:
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            return "toggle"
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            return "add"
        return "replace"

    def _apply_selection_change(self, targets: List[float], mode: str):
        if mode == "replace":
            self._replace_selection(targets)
        elif mode == "add":
            self._add_selection(targets)
        else:
            self._toggle_selection(targets)

    def _replace_selection(self, times: List[float]):
        snapped = self._snap_markers(times)
        self._update_selection(snapped, emit=True)

    def _add_selection(self, times: List[float]):
        new_sel = list(self._selected_markers)
        for time in self._snap_markers(times):
            if not self._is_selected(time):
                new_sel.append(time)
        self._update_selection(new_sel, emit=True)

    def _toggle_selection(self, times: List[float]):
        eps = self._time_epsilon()
        current = list(self._selected_markers)
        for time in self._snap_markers(times):
            removed = False
            for idx, existing in enumerate(current):
                if abs(existing - time) <= eps:
                    current.pop(idx)
                    removed = True
                    break
            if not removed:
                current.append(time)
        self._update_selection(current, emit=True)

    def _update_selection(self, selection: List[float], emit: bool):
        unique: List[float] = []
        eps = self._time_epsilon()
        for value in sorted(selection):
            if any(abs(value - existing) <= eps for existing in unique):
                continue
            if self._snap_to_marker(value) is None:
                continue
            unique.append(self._snap_to_marker(value) or value)
        if len(unique) == len(self._selected_markers) and all(
            abs(a - b) <= eps for a, b in zip(unique, self._selected_markers)
        ):
            return
        self._selected_markers = unique
        if emit:
            self.selectionChanged.emit(list(self._selected_markers))
        self.update()

    def _snap_to_marker(self, target: float) -> Optional[float]:
        eps = self._time_epsilon()
        for marker in self._markers:
            if abs(marker - target) <= eps:
                return marker
        return None

    def _snap_markers(self, times: List[float]) -> List[float]:
        snapped: List[float] = []
        for time in times:
            snapped_time = self._snap_to_marker(time)
            if snapped_time is not None:
                snapped.append(snapped_time)
        return snapped

    def _prune_selection(self):
        if not self._selected_markers:
            return
        snapped = self._snap_markers(self._selected_markers)
        self._update_selection(snapped, emit=True)

    def set_beat_markers(self, beats: List[float], duration: float):
        self._beat_markers = sorted(max(0.0, float(value)) for value in (beats or []))
        if duration > 0.0:
            self._duration = max(self._duration, float(duration))
        self.update()

    def set_beat_grid_enabled(self, enabled: bool):
        self._beat_grid_enabled = bool(enabled)
        if not self._beat_grid_enabled:
            self._beat_drag_active = False
            self._beat_drag_index = -1
        self.update()

    def set_beat_edit_enabled(self, enabled: bool):
        self._beat_edit_enabled = bool(enabled)
        if not self._beat_edit_enabled and self._beat_drag_active:
            self._beat_drag_active = False
            self._beat_drag_index = -1
            self.update()

    def _paint_beat_grid(self, painter: QPainter, rect, baseline_y: float):
        beat_color = QColor(0, 200, 255, 110)
        highlight_color = QColor(0, 255, 200, 180)
        base_pen = QPen(beat_color)
        base_pen.setWidth(1)
        active_pen = QPen(highlight_color)
        active_pen.setWidth(2)
        tolerance = max(1e-6, self._view_duration * 1e-4)
        for idx, beat in enumerate(self._beat_markers):
            if beat < self._view_start - tolerance or beat > self._view_start + self._view_duration + tolerance:
                continue
            render_time = beat
            if self._beat_drag_active and idx == self._beat_drag_index:
                render_time = self._clamp_time(self._beat_drag_origin + self._beat_drag_preview)
            x = self._time_to_x(render_time)
            painter.setPen(active_pen if idx == self._beat_drag_index else base_pen)
            painter.drawLine(int(x), 2, int(x), rect.height() - 2)

    def _locate_beat_marker(self, click_x: float):
        closest_index = None
        closest_distance = None
        if not self._beat_markers:
            return None, None
        for idx, beat in enumerate(self._beat_markers):
            if beat < self._view_start or beat > self._view_start + self._view_duration:
                continue
            distance = abs(click_x - self._time_to_x(beat))
            if closest_distance is None or distance < closest_distance:
                closest_distance = distance
                closest_index = idx
        return closest_index, closest_distance


@dataclass(frozen=True)
class TimelineLaneKey:
    scope: str  # "layer" or "global"
    layer_id: int
    lane_index: int


@dataclass
class TimelineLaneSpec:
    key: TimelineLaneKey
    label: str
    markers: List[float]
    deletable: bool = True


@dataclass
class TimelineGroupSpec:
    key: Tuple[str, int]
    label: str
    lanes: List[TimelineLaneSpec]
    addable: bool = True


class KeyframeLaneBar(KeyframeMarkerBar):
    laneActivated = pyqtSignal(object)

    def __init__(self, lane_key: TimelineLaneKey, parent=None):
        super().__init__(parent)
        self.lane_key = lane_key

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.laneActivated.emit(self.lane_key)
        super().mousePressEvent(event)


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full_text = text or ""
        if text:
            self.setToolTip(text)

    def setText(self, text: str):  # type: ignore[override]
        self.setFullText(text)

    def setFullText(self, text: str):
        self._full_text = text or ""
        if self._full_text:
            self.setToolTip(self._full_text)
        self._update_elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elide()

    def _update_elide(self):
        if not self._full_text:
            super().setText("")
            return
        metrics = self.fontMetrics()
        available = max(0, self.width() - 4)
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, available)
        super().setText(elided)


class TimelineLaneRow(QWidget):
    def __init__(self, lane_spec: TimelineLaneSpec, parent=None):
        super().__init__(parent)
        self.lane_spec = lane_spec
        self._compact_ui = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(2)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self.label = ElidedLabel(lane_spec.label)
        self.label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.label.setMinimumWidth(0)
        self.label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        header.addWidget(self.label)

        header.addStretch(1)

        self.activate_btn = QToolButton()
        self.activate_btn.setText("Target")
        self.activate_btn.setToolTip("Set this lane as the target for new keyframes")
        self.activate_btn.setCheckable(True)
        self.activate_btn.setAutoRaise(True)
        header.addWidget(self.activate_btn)

        self.delete_btn = QToolButton()
        self.delete_btn.setText("x")
        self.delete_btn.setToolTip("Delete this keyframe lane")
        self.delete_btn.setAutoRaise(True)
        self.delete_btn.setVisible(lane_spec.deletable)
        self.delete_btn.setEnabled(lane_spec.deletable)
        header.addWidget(self.delete_btn)

        layout.addLayout(header)

        self.bar = KeyframeLaneBar(lane_spec.key)
        self.bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.bar)

    def set_active(self, active: bool):
        if active:
            self.activate_btn.setChecked(True)
            self.activate_btn.setText("Active")
        else:
            self.activate_btn.setChecked(False)
            self.activate_btn.setText("Target")
        self._update_label_style(active)

    def set_compact_ui(self, enabled: bool):
        self._compact_ui = bool(enabled)
        self._update_label_style(self.activate_btn.isChecked())
        if hasattr(self.bar, "set_compact_ui"):
            self.bar.set_compact_ui(enabled)

    def _update_label_style(self, active: bool):
        parts = []
        if active:
            parts.append("font-weight: bold; color: #dfe8ff;")
        if self._compact_ui:
            parts.append("font-size: 9pt;")
        self.label.setStyleSheet(" ".join(parts))


class TimelineGroupWidget(QWidget):
    addRequested = pyqtSignal(object)

    def __init__(self, group_spec: TimelineGroupSpec, collapsed: bool = False, parent=None):
        super().__init__(parent)
        self.group_spec = group_spec
        self._collapsed = bool(collapsed)
        self._compact_ui = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(6, 0, 6, 0)
        header.setSpacing(6)

        self.toggle_btn = QToolButton()
        self.toggle_btn.setText(">" if self._collapsed else "v")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(not self._collapsed)
        self.toggle_btn.clicked.connect(self._toggle_collapsed)
        header.addWidget(self.toggle_btn)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(16, 16)
        self.thumb_label.setScaledContents(False)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setVisible(False)
        header.addWidget(self.thumb_label)

        self.title_label = QLabel(group_spec.label)
        self.title_label.setStyleSheet("font-weight: bold;")
        header.addWidget(self.title_label)
        header.addStretch()

        self.add_btn = QToolButton()
        self.add_btn.setText("+")
        self.add_btn.setToolTip("Add keyframe lane")
        self.add_btn.setEnabled(group_spec.addable)
        self.add_btn.clicked.connect(lambda: self.addRequested.emit(group_spec.key))
        header.addWidget(self.add_btn)

        layout.addLayout(header)

        self.lanes_container = QWidget()
        self.lanes_layout = QVBoxLayout(self.lanes_container)
        self.lanes_layout.setContentsMargins(0, 0, 0, 0)
        self.lanes_layout.setSpacing(2)
        layout.addWidget(self.lanes_container)

        if self._collapsed:
            self.lanes_container.hide()

    def _toggle_collapsed(self):
        self._collapsed = not self._collapsed
        self.toggle_btn.setText(">" if self._collapsed else "v")
        self.lanes_container.setVisible(not self._collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_compact_ui(self, enabled: bool):
        self._compact_ui = bool(enabled)
        thumb_size = 12 if self._compact_ui else 16
        self.thumb_label.setFixedSize(thumb_size, thumb_size)
        if not self.thumb_label.pixmap():
            self.thumb_label.setVisible(False)

    def set_thumbnail(self, pixmap: Optional[QPixmap]):
        if pixmap:
            size = self.thumb_label.size()
            scaled = pixmap.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.thumb_label.setPixmap(scaled)
            self.thumb_label.setVisible(True)
        else:
            self.thumb_label.clear()
            self.thumb_label.setVisible(False)

class TimelineWidget(QWidget):
    """Timeline widget with playback controls."""

    play_toggled = pyqtSignal()
    loop_toggled = pyqtSignal(int)
    time_changed = pyqtSignal(int)
    keyframe_marker_clicked = pyqtSignal(float)
    keyframe_marker_remove_requested = pyqtSignal(list)
    keyframe_marker_dragged = pyqtSignal(list, float)
    keyframe_selection_changed = pyqtSignal(list)
    beat_marker_dragged = pyqtSignal(int, float)
    keyframe_lane_add_requested = pyqtSignal(object)
    keyframe_lane_remove_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms: float = 0.0
        self._view_start_ms: float = 0.0
        self._view_duration_ms: float = 0.0
        self._current_time_ms: float = 0.0
        self._min_view_ms: float = 100.0
        self._lane_rows: Dict[TimelineLaneKey, TimelineLaneRow] = {}
        self._lane_selections: Dict[TimelineLaneKey, List[float]] = {}
        self._group_collapsed: Dict[Tuple[str, int], bool] = {}
        self._group_widgets: Dict[Tuple[str, int], TimelineGroupWidget] = {}
        self._active_lane: Optional[TimelineLaneKey] = None
        self._lane_combo_keys: List[TimelineLaneKey] = []
        self._tracks_collapsed: bool = False
        self._beat_markers: List[float] = []
        self._beat_grid_enabled: bool = False
        self._beat_edit_enabled: bool = False
        self._compact_ui: bool = False
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.play_toggled.emit)
        controls.addWidget(self.play_btn)

        self.loop_checkbox = QCheckBox("Loop")
        self.loop_checkbox.setChecked(True)
        self.loop_checkbox.stateChanged.connect(self.loop_toggled.emit)
        controls.addWidget(self.loop_checkbox)

        self.time_label = QLabel("0.00 / 0.00s")
        controls.addWidget(self.time_label)
        self.beat_info_label = QLabel("")
        self.beat_info_label.setStyleSheet("color: #00f5a0; font-weight: bold;")
        self.beat_info_label.setVisible(False)
        controls.addWidget(self.beat_info_label)
        self.lane_target_label = QLabel("Target Lane")
        controls.addWidget(self.lane_target_label)
        self.lane_target_combo = QComboBox()
        self.lane_target_combo.setMinimumWidth(220)
        self.lane_target_combo.currentIndexChanged.connect(self._on_lane_combo_changed)
        controls.addWidget(self.lane_target_combo)
        self.collapse_btn = QToolButton()
        self.collapse_btn.setText("Collapse")
        self.collapse_btn.clicked.connect(self._toggle_tracks_collapsed)
        controls.addWidget(self.collapse_btn)
        controls.addStretch()

        layout.addLayout(controls)

        self.tracks_scroll = QScrollArea()
        self.tracks_scroll.setWidgetResizable(True)
        self.tracks_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tracks_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tracks_container = QWidget()
        self.tracks_layout = QVBoxLayout(self.tracks_container)
        self.tracks_layout.setContentsMargins(0, 0, 0, 0)
        self.tracks_layout.setSpacing(6)
        self.tracks_scroll.setWidget(self.tracks_container)
        layout.addWidget(self.tracks_scroll)

        self.timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_slider.setMinimum(0)
        self.timeline_slider.setMaximum(1)
        self.timeline_slider.valueChanged.connect(self._on_slider_value_changed)
        layout.addWidget(self.timeline_slider)

        self.timeline_scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.timeline_scrollbar.setVisible(False)
        self.timeline_scrollbar.valueChanged.connect(self._on_scrollbar_value_changed)
        layout.addWidget(self.timeline_scrollbar)

    def set_compact_ui(self, enabled: bool):
        self._compact_ui = bool(enabled)
        spacing = 4 if self._compact_ui else 6
        self.tracks_layout.setSpacing(spacing)
        if self._compact_ui:
            self.setStyleSheet("QLabel { font-size: 9pt; } QPushButton, QToolButton { padding: 2px 6px; }")
        else:
            self.setStyleSheet("")
        for group in self._group_widgets.values():
            group.set_compact_ui(self._compact_ui)
        for row in self._lane_rows.values():
            row.set_compact_ui(self._compact_ui)

    def set_play_button_text(self, text: str):
        self.play_btn.setText(text)

    def set_time_label(self, text: str):
        self.time_label.setText(text)

    @staticmethod
    def _clamp_qint32(value: float, *, minimum: int = 0) -> int:
        """Clamp a numeric value to Qt's signed 32-bit integer range."""
        if not math.isfinite(value):
            value = float(minimum)
        clamped = min(float(_QINT32_MAX), max(float(_QINT32_MIN), float(value)))
        clamped_int = int(clamped)
        if clamped_int < minimum:
            return minimum
        return clamped_int

    @staticmethod
    def _sanitize_ms(value_ms: float, *, default_ms: float = 1.0) -> float:
        """Normalize a millisecond duration into a finite Qt-safe range."""
        if not math.isfinite(value_ms):
            value_ms = default_ms
        return min(float(_QINT32_MAX), max(0.0, float(value_ms)))

    def set_slider_maximum(self, maximum: int):
        duration_ms = self._sanitize_ms(float(maximum), default_ms=1.0)
        duration_seconds = max(0.0, duration_ms / 1000.0)
        self.set_timeline_duration(duration_seconds)

    def set_timeline_duration(self, duration_seconds: float):
        try:
            duration_value = float(duration_seconds)
        except (TypeError, ValueError):
            duration_value = 0.0
        if not math.isfinite(duration_value) or duration_value < 0.0:
            duration_value = 0.0
        self._duration_ms = max(1.0, self._sanitize_ms(duration_value * 1000.0, default_ms=1.0))
        if self._view_duration_ms <= 0.0 or self._view_duration_ms > self._duration_ms:
            self._view_duration_ms = self._duration_ms
            self._view_start_ms = 0.0
        else:
            max_start = max(0.0, self._duration_ms - self._view_duration_ms)
            self._view_start_ms = min(max(0.0, self._view_start_ms), max_start)
        self._update_scrollbar()
        self._update_slider_range()
        self._update_lane_view_window()

    def set_lane_groups(self, groups: List[TimelineGroupSpec]):
        for idx in reversed(range(self.tracks_layout.count())):
            item = self.tracks_layout.takeAt(idx)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._lane_rows.clear()
        self._group_widgets.clear()

        for group in groups:
            collapsed = self._group_collapsed.get(group.key, False)
            group_widget = TimelineGroupWidget(group, collapsed=collapsed)
            group_widget.addRequested.connect(self.keyframe_lane_add_requested.emit)
            group_widget.toggle_btn.clicked.connect(
                lambda _, key=group.key, widget=group_widget: self._store_group_state(key, widget)
            )
            group_widget.set_compact_ui(self._compact_ui)
            self.tracks_layout.addWidget(group_widget)
            self._group_widgets[group.key] = group_widget

            for lane_spec in group.lanes:
                row = TimelineLaneRow(lane_spec)
                row.bar.markerClicked.connect(
                    lambda time, key=lane_spec.key: self._on_lane_marker_clicked(key, time)
                )
                row.bar.markerRemoveRequested.connect(
                    lambda times, key=lane_spec.key: self._on_lane_marker_remove_requested(key, times)
                )
                row.bar.markerDragRequested.connect(
                    lambda times, delta, key=lane_spec.key: self._on_lane_marker_drag_requested(key, times, delta)
                )
                row.bar.selectionChanged.connect(
                    lambda selection, key=lane_spec.key: self._on_lane_selection_changed(key, selection)
                )
                row.bar.zoomRequested.connect(self._on_zoom_requested)
                row.bar.beatMarkerDragged.connect(self._on_beat_marker_dragged)
                row.activate_btn.clicked.connect(lambda _, key=lane_spec.key: self.set_active_lane(key))
                if lane_spec.deletable:
                    row.delete_btn.clicked.connect(
                        lambda _, key=lane_spec.key: self.keyframe_lane_remove_requested.emit(key)
                    )
                row.bar.set_markers(lane_spec.markers, self._duration_ms / 1000.0)
                row.bar.set_view_window(self._view_start_ms / 1000.0, self._view_duration_ms / 1000.0)
                row.bar.set_beat_markers(self._beat_markers, self._duration_ms / 1000.0)
                row.bar.set_beat_grid_enabled(self._beat_grid_enabled)
                row.bar.set_beat_edit_enabled(self._beat_edit_enabled)
                selection = self._lane_selections.get(lane_spec.key, [])
                if selection:
                    row.bar.set_selected_markers(selection)
                row.set_active(self._active_lane == lane_spec.key)
                row.set_compact_ui(self._compact_ui)
                self._lane_rows[lane_spec.key] = row
                group_widget.lanes_layout.addWidget(row)

        self.tracks_layout.addStretch()
        self._refresh_lane_target_combo(groups)

    def set_lane_thumbnail(self, lane_key: TimelineLaneKey, pixmap: Optional[QPixmap]):
        row = self._lane_rows.get(lane_key)
        if row and hasattr(row, "set_thumbnail"):
            row.set_thumbnail(pixmap)

    def set_group_thumbnail(self, group_key: Tuple[str, int], pixmap: Optional[QPixmap]):
        group = self._group_widgets.get(group_key)
        if group:
            group.set_thumbnail(pixmap)

    def set_beat_markers(self, beats: List[float], duration: float):
        self._beat_markers = list(beats or [])
        for row in self._lane_rows.values():
            row.bar.set_beat_markers(beats, duration)
            row.bar.set_view_window(self._view_start_ms / 1000.0, self._view_duration_ms / 1000.0)

    def set_beat_grid_visible(self, enabled: bool):
        self._beat_grid_enabled = bool(enabled)
        for row in self._lane_rows.values():
            row.bar.set_beat_grid_enabled(enabled)

    def set_beat_edit_enabled(self, enabled: bool):
        self._beat_edit_enabled = bool(enabled)
        for row in self._lane_rows.values():
            row.bar.set_beat_edit_enabled(enabled)

    def set_marker_selection(self, markers: List[Tuple[TimelineLaneKey, float]]):
        self._lane_selections = {}
        for lane_key, time_value in markers or []:
            self._lane_selections.setdefault(lane_key, []).append(time_value)
        for lane_key, row in self._lane_rows.items():
            row.bar.set_selected_markers(self._lane_selections.get(lane_key, []))

    def set_current_time(self, time_value: float):
        self._current_time_ms = max(0.0, min(time_value * 1000.0, self._duration_ms))
        self._ensure_time_visible(self._current_time_ms)
        self._update_slider_range()
        for row in self._lane_rows.values():
            row.bar.set_current_time(self._current_time_ms / 1000.0)

    def set_beat_bpm_display(self, bpm: Optional[float], variable: bool = False):
        if bpm is None:
            self.beat_info_label.hide()
            return
        suffix = " (var)" if variable else ""
        self.beat_info_label.setText(f"{bpm:.1f} BPM{suffix}")
        self.beat_info_label.show()

    def _on_slider_value_changed(self, value: int):
        actual_ms = self._view_start_ms + float(value)
        actual_ms = max(0.0, min(actual_ms, self._duration_ms))
        self._current_time_ms = actual_ms
        self._ensure_time_visible(actual_ms)
        for row in self._lane_rows.values():
            row.bar.set_current_time(actual_ms / 1000.0)
        self.time_changed.emit(int(actual_ms))

    def _on_scrollbar_value_changed(self, value: int):
        self._view_start_ms = float(value)
        self._update_slider_range()
        self._update_lane_view_window()

    def _update_slider_range(self):
        view_ms = max(1.0, self._sanitize_ms(self._view_duration_ms, default_ms=1.0))
        slider_float = min(max(self._current_time_ms - self._view_start_ms, 0.0), view_ms)
        slider_value = self._clamp_qint32(slider_float, minimum=0)
        self.timeline_slider.blockSignals(True)
        self.timeline_slider.setMaximum(self._clamp_qint32(view_ms, minimum=1))
        self.timeline_slider.setValue(slider_value)
        self.timeline_slider.blockSignals(False)

    def _update_scrollbar(self):
        if self._tracks_collapsed:
            self.timeline_scrollbar.hide()
            self.timeline_scrollbar.setRange(0, 0)
            return
        if self._view_duration_ms >= self._duration_ms or self._duration_ms <= 0.0:
            self.timeline_scrollbar.hide()
            self.timeline_scrollbar.setRange(0, 0)
        else:
            max_start = self._clamp_qint32(max(0.0, self._duration_ms - self._view_duration_ms), minimum=0)
            page_step = self._clamp_qint32(max(1.0, self._view_duration_ms), minimum=1)
            single_step = self._clamp_qint32(max(1.0, self._view_duration_ms * 0.1), minimum=1)
            current_value = self._clamp_qint32(self._view_start_ms, minimum=0)
            self.timeline_scrollbar.blockSignals(True)
            self.timeline_scrollbar.setRange(0, max_start)
            self.timeline_scrollbar.setPageStep(page_step)
            self.timeline_scrollbar.setSingleStep(single_step)
            self.timeline_scrollbar.setValue(min(current_value, max_start))
            self.timeline_scrollbar.blockSignals(False)
            self.timeline_scrollbar.show()

    def _ensure_time_visible(self, time_ms: float):
        if time_ms < self._view_start_ms:
            self._view_start_ms = time_ms - self._view_duration_ms * 0.1
            if self._view_start_ms < 0.0:
                self._view_start_ms = 0.0
            self._update_scrollbar()
            self._update_lane_view_window()
        elif time_ms > self._view_start_ms + self._view_duration_ms:
            self._view_start_ms = time_ms - self._view_duration_ms * 0.9
            max_start = max(0.0, self._duration_ms - self._view_duration_ms)
            if self._view_start_ms > max_start:
                self._view_start_ms = max_start
            self._update_scrollbar()
            self._update_lane_view_window()
        self._update_slider_range()

    def _on_zoom_requested(self, factor: float, anchor_time: float):
        if self._duration_ms <= 0.0:
            return
        anchor_ms = max(0.0, min(anchor_time * 1000.0, self._duration_ms))
        new_span = self._view_duration_ms * factor
        new_span = max(self._min_view_ms, min(new_span, self._duration_ms))
        if new_span >= self._duration_ms:
            self._view_start_ms = 0.0
        else:
            ratio = 0.0
            if self._view_duration_ms > 0:
                ratio = (anchor_ms - self._view_start_ms) / self._view_duration_ms
                ratio = min(max(ratio, 0.0), 1.0)
            new_start = anchor_ms - ratio * new_span
            max_start = max(0.0, self._duration_ms - new_span)
            self._view_start_ms = min(max(0.0, new_start), max_start)
        self._view_duration_ms = new_span
        self._update_scrollbar()
        self._update_lane_view_window()
        self._update_slider_range()

    def _on_beat_marker_dragged(self, index: int, new_time: float):
        self.beat_marker_dragged.emit(index, new_time)

    def _update_lane_view_window(self):
        for row in self._lane_rows.values():
            row.bar.set_view_window(self._view_start_ms / 1000.0, self._view_duration_ms / 1000.0)

    def _store_group_state(self, key: Tuple[str, int], widget: TimelineGroupWidget):
        self._group_collapsed[key] = widget.is_collapsed()

    def set_active_lane(self, lane_key: TimelineLaneKey):
        self._active_lane = lane_key
        for key, row in self._lane_rows.items():
            row.set_active(key == lane_key)
        if self._lane_combo_keys and lane_key in self._lane_combo_keys:
            idx = self._lane_combo_keys.index(lane_key)
            self.lane_target_combo.blockSignals(True)
            self.lane_target_combo.setCurrentIndex(idx)
            self.lane_target_combo.blockSignals(False)

    def get_active_lane(self) -> Optional[TimelineLaneKey]:
        return self._active_lane

    def get_selected_markers(self) -> List[Tuple[TimelineLaneKey, float]]:
        refs: List[Tuple[TimelineLaneKey, float]] = []
        for lane_key, times in self._lane_selections.items():
            for time_value in times:
                refs.append((lane_key, time_value))
        return refs

    def _refresh_lane_target_combo(self, groups: List[TimelineGroupSpec]):
        self._lane_combo_keys = []
        labels: List[str] = []
        for group in groups:
            for lane in group.lanes:
                self._lane_combo_keys.append(lane.key)
                labels.append(f"{group.label} - {lane.label}")
        self.lane_target_combo.blockSignals(True)
        self.lane_target_combo.clear()
        if labels:
            self.lane_target_combo.addItems(labels)
            if self._active_lane in self._lane_combo_keys:
                idx = self._lane_combo_keys.index(self._active_lane)
            else:
                idx = 0
            self.lane_target_combo.setCurrentIndex(idx)
            self.lane_target_combo.setEnabled(True)
        else:
            self.lane_target_combo.setEnabled(False)
        self.lane_target_combo.blockSignals(False)

    def _on_lane_combo_changed(self, index: int):
        if index < 0 or index >= len(self._lane_combo_keys):
            return
        self.set_active_lane(self._lane_combo_keys[index])

    def _toggle_tracks_collapsed(self):
        self._tracks_collapsed = not self._tracks_collapsed
        self.tracks_scroll.setVisible(not self._tracks_collapsed)
        self.timeline_slider.setVisible(not self._tracks_collapsed)
        self.timeline_scrollbar.setVisible(not self._tracks_collapsed and self.timeline_scrollbar.maximum() > 0)
        self.collapse_btn.setText("Expand" if self._tracks_collapsed else "Collapse")

    def _on_lane_marker_clicked(self, lane_key: TimelineLaneKey, time_value: float):
        self.keyframe_marker_clicked.emit(time_value)

    def _on_lane_marker_remove_requested(self, lane_key: TimelineLaneKey, targets: List[float]):
        if not targets:
            return
        selected = self.get_selected_markers()
        if selected and any(key == lane_key and time in targets for key, time in selected):
            self.keyframe_marker_remove_requested.emit(selected)
        else:
            self.keyframe_marker_remove_requested.emit([(lane_key, time) for time in targets])

    def _on_lane_marker_drag_requested(self, lane_key: TimelineLaneKey, targets: List[float], delta: float):
        if not targets:
            return
        selected = self.get_selected_markers()
        if selected:
            self.keyframe_marker_dragged.emit(selected, float(delta))
        else:
            self.keyframe_marker_dragged.emit([(lane_key, time) for time in targets], float(delta))

    def _on_lane_selection_changed(self, lane_key: TimelineLaneKey, selection: List[float]):
        modifiers = QApplication.keyboardModifiers()
        replace = not (modifiers & Qt.KeyboardModifier.ControlModifier or modifiers & Qt.KeyboardModifier.ShiftModifier)
        if replace:
            for other_key, row in self._lane_rows.items():
                if other_key == lane_key:
                    continue
                row.bar.set_selected_markers([])
                self._lane_selections[other_key] = []
        self._lane_selections[lane_key] = list(selection)
        self.keyframe_selection_changed.emit(self.get_selected_markers())
