"""
Settings Dialog
Export settings and application preferences
"""

import contextlib
import copy
import fnmatch
import io
import json
import math
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QSpinBox, QDoubleSpinBox,
    QSlider, QCheckBox, QGroupBox, QTabWidget,
    QWidget, QFormLayout, QFrame, QProgressBar, QMessageBox, QScrollArea,
    QSizePolicy, QLineEdit, QFileDialog, QListWidget, QPlainTextEdit,
    QInputDialog, QKeySequenceEdit
)
from PyQt6.QtCore import Qt, QSettings, QThread, QObject, pyqtSignal
from PyQt6.QtGui import QKeySequence

from utils.ffmpeg_installer import (
    install_ffmpeg,
    resolve_ffmpeg_path,
    get_install_root,
    query_ffmpeg_encoders,
)
from utils.shader_registry import ShaderRegistry, ShaderPreset
from utils.keybinds import keybind_actions, default_keybinds, normalize_keybind_sequence


class ExportSettings:
    """Container for export settings"""
    
    def __init__(self):
        self.settings = QSettings('MSMAnimationViewer', 'ExportSettings')
        self.load()
    
    def load(self):
        """Load settings from storage"""
        # PNG settings
        self.png_compression = self.settings.value('png/compression', 6, type=int)
        self.png_full_resolution = self.settings.value('png/full_resolution', False, type=bool)
        self.png_full_scale_multiplier = self.settings.value('png/full_scale_multiplier', 1.0, type=float)
        
        # GIF settings
        self.gif_fps = self.settings.value('gif/fps', 15, type=int)
        self.gif_colors = self.settings.value('gif/colors', 256, type=int)
        self.gif_dither = self.settings.value('gif/dither', True, type=bool)
        self.gif_optimize = self.settings.value('gif/optimize', True, type=bool)
        self.gif_loop = self.settings.value('gif/loop', 0, type=int)  # 0 = infinite
        self.gif_scale = self.settings.value('gif/scale', 100, type=int)  # percentage
        
        # MOV settings
        # Default to prores_ks for best Adobe compatibility
        self.mov_codec = self.settings.value('mov/codec', 'prores_ks', type=str)
        self.mov_quality = self.settings.value('mov/quality', 'high', type=str)
        self.mov_include_audio = self.settings.value('mov/include_audio', True, type=bool)
        self.mov_full_resolution = self.settings.value('mov/full_resolution', False, type=bool)
        self.mov_full_scale_multiplier = self.settings.value('mov/full_scale_multiplier', 1.0, type=float)

        # WEBM settings
        self.webm_codec = self.settings.value('webm/codec', 'libvpx-vp9', type=str)
        self.webm_crf = self.settings.value('webm/crf', 28, type=int)
        self.webm_speed = self.settings.value('webm/speed', 4, type=int)
        self.webm_include_audio = self.settings.value('webm/include_audio', True, type=bool)
        self.webm_full_resolution = self.settings.value('webm/full_resolution', False, type=bool)
        self.webm_full_scale_multiplier = self.settings.value('webm/full_scale_multiplier', 1.0, type=float)

        # MP4 settings
        self.mp4_codec = self.settings.value('mp4/codec', 'libx264', type=str)
        self.mp4_crf = self.settings.value('mp4/crf', 18, type=int)
        self.mp4_preset = self.settings.value('mp4/preset', 'medium', type=str)
        self.mp4_bitrate = self.settings.value('mp4/bitrate_kbps', 0, type=int)
        self.mp4_include_audio = self.settings.value('mp4/include_audio', True, type=bool)
        self.mp4_full_resolution = self.settings.value('mp4/full_resolution', False, type=bool)
        self.mp4_full_scale_multiplier = self.settings.value('mp4/full_scale_multiplier', 1.0, type=float)
        self.mp4_pixel_format = self.settings.value('mp4/pixel_format', 'yuv420p', type=str)
        self.mp4_faststart = self.settings.value('mp4/faststart', True, type=bool)

        # Universal export framing
        self.universal_export_bounds_scope = self.settings.value(
            'export_universal/bounds_scope', 'current', type=str
        )
        if self.universal_export_bounds_scope not in ('current', 'all'):
            self.universal_export_bounds_scope = 'current'
        self.universal_export_explicit_resolution = self.settings.value(
            'export_universal/explicit_resolution', False, type=bool
        )
        self.universal_export_width = max(
            16,
            self.settings.value('export_universal/width', 1920, type=int),
        )
        self.universal_export_height = max(
            16,
            self.settings.value('export_universal/height', 1080, type=int),
        )
        self.universal_export_padding = max(
            0.0,
            self.settings.value('export_universal/padding', 8.0, type=float),
        )

        # Camera/view settings
        self.camera_zoom_to_cursor = self.settings.value('camera/zoom_to_cursor', True, type=bool)
        self.use_barebones_file_browser = self.settings.value('files/use_barebones_browser', False, type=bool)
        self.anchor_debug_logging = self.settings.value('logging/anchor_debug', False, type=bool)
        self.update_source_json_on_save = self.settings.value('save/update_source_json', False, type=bool)
    
        # PSD settings
        self.psd_include_hidden = self.settings.value('psd/include_hidden', False, type=bool)
        self.psd_scale = self.settings.value('psd/scale', 100, type=int)
        self.psd_quality = self.settings.value('psd/quality', 'balanced', type=str)
        self.psd_compression = self.settings.value('psd/compression', 'rle', type=str)
        self.psd_crop_canvas = self.settings.value('psd/crop_canvas', False, type=bool)
        self.psd_match_viewport = self.settings.value('psd/match_viewport', False, type=bool)
        self.psd_preserve_resolution = self.settings.value('psd/preserve_resolution', False, type=bool)
        self.psd_full_res_multiplier = self.settings.value('psd/full_res_multiplier', 1.0, type=float)

        # AE rig settings
        self.ae_rig_mode = self.settings.value('ae/rig_mode', 'auto', type=str)
        if self.settings.contains('ae/scale'):
            self.ae_scale = self.settings.value('ae/scale', 100, type=int)
        else:
            legacy_scale = self.settings.value('ae/output_scale', 1.0, type=float)
            self.ae_scale = int(round(float(legacy_scale) * 100))
        if self.settings.contains('ae/quality'):
            self.ae_quality = self.settings.value('ae/quality', 'balanced', type=str)
        else:
            legacy_quality = self.settings.value('ae/resample_quality', 'lanczos', type=str)
            legacy_quality = str(legacy_quality or 'lanczos').lower()
            quality_map = {
                'nearest': 'fast',
                'bilinear': 'balanced',
                'bicubic': 'high',
                'lanczos': 'maximum',
            }
            self.ae_quality = quality_map.get(legacy_quality, 'balanced')
        self.ae_preserve_resolution = self.settings.value('ae/preserve_resolution', False, type=bool)
        self.ae_full_res_multiplier = self.settings.value('ae/full_res_multiplier', 1.0, type=float)
        self.ae_match_viewport = self.settings.value('ae/match_viewport', True, type=bool)
        self.ae_compression = self.settings.value('ae/compression', 'rle', type=str)
    
    def save(self):
        """Save settings to storage"""
        # PNG settings
        self.settings.setValue('png/compression', self.png_compression)
        self.settings.setValue('png/full_resolution', self.png_full_resolution)
        self.settings.setValue('png/full_scale_multiplier', self.png_full_scale_multiplier)
        
        # GIF settings
        self.settings.setValue('gif/fps', self.gif_fps)
        self.settings.setValue('gif/colors', self.gif_colors)
        self.settings.setValue('gif/dither', self.gif_dither)
        self.settings.setValue('gif/optimize', self.gif_optimize)
        self.settings.setValue('gif/loop', self.gif_loop)
        self.settings.setValue('gif/scale', self.gif_scale)
        
        # MOV settings
        self.settings.setValue('mov/codec', self.mov_codec)
        self.settings.setValue('mov/quality', self.mov_quality)
        self.settings.setValue('mov/include_audio', self.mov_include_audio)
        self.settings.setValue('mov/full_resolution', self.mov_full_resolution)
        self.settings.setValue('mov/full_scale_multiplier', self.mov_full_scale_multiplier)

        # WEBM settings
        self.settings.setValue('webm/codec', self.webm_codec)
        self.settings.setValue('webm/crf', self.webm_crf)
        self.settings.setValue('webm/speed', self.webm_speed)
        self.settings.setValue('webm/include_audio', self.webm_include_audio)
        self.settings.setValue('webm/full_resolution', self.webm_full_resolution)
        self.settings.setValue('webm/full_scale_multiplier', self.webm_full_scale_multiplier)

        # MP4 settings
        self.settings.setValue('mp4/codec', self.mp4_codec)
        self.settings.setValue('mp4/crf', self.mp4_crf)
        self.settings.setValue('mp4/preset', self.mp4_preset)
        self.settings.setValue('mp4/bitrate_kbps', self.mp4_bitrate)
        self.settings.setValue('mp4/include_audio', self.mp4_include_audio)
        self.settings.setValue('mp4/full_resolution', self.mp4_full_resolution)
        self.settings.setValue('mp4/full_scale_multiplier', self.mp4_full_scale_multiplier)
        self.settings.setValue('mp4/pixel_format', self.mp4_pixel_format)
        self.settings.setValue('mp4/faststart', self.mp4_faststart)

        # Universal export framing
        self.settings.setValue('export_universal/bounds_scope', self.universal_export_bounds_scope)
        self.settings.setValue('export_universal/explicit_resolution', self.universal_export_explicit_resolution)
        self.settings.setValue('export_universal/width', self.universal_export_width)
        self.settings.setValue('export_universal/height', self.universal_export_height)
        self.settings.setValue('export_universal/padding', self.universal_export_padding)

        self.settings.setValue('camera/zoom_to_cursor', self.camera_zoom_to_cursor)
        self.settings.setValue('files/use_barebones_browser', self.use_barebones_file_browser)
        self.settings.setValue('logging/anchor_debug', self.anchor_debug_logging)
        self.settings.setValue('save/update_source_json', self.update_source_json_on_save)
        
        # PSD settings
        self.settings.setValue('psd/include_hidden', self.psd_include_hidden)
        self.settings.setValue('psd/scale', self.psd_scale)
        self.settings.setValue('psd/quality', self.psd_quality)
        self.settings.setValue('psd/compression', self.psd_compression)
        self.settings.setValue('psd/crop_canvas', self.psd_crop_canvas)
        self.settings.setValue('psd/match_viewport', self.psd_match_viewport)
        self.settings.setValue('psd/preserve_resolution', self.psd_preserve_resolution)
        self.settings.setValue('psd/full_res_multiplier', self.psd_full_res_multiplier)

        # AE rig settings
        self.settings.setValue('ae/rig_mode', self.ae_rig_mode)
        self.settings.setValue('ae/scale', self.ae_scale)
        self.settings.setValue('ae/quality', self.ae_quality)
        self.settings.setValue('ae/preserve_resolution', self.ae_preserve_resolution)
        self.settings.setValue('ae/full_res_multiplier', self.ae_full_res_multiplier)
        self.settings.setValue('ae/match_viewport', self.ae_match_viewport)
        self.settings.setValue('ae/compression', self.ae_compression)


class FFmpegInstallWorker(QObject):
    """Runs the FFmpeg installer logic off the UI thread."""
    
    statusChanged = pyqtSignal(str)
    progressChanged = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
    
    def run(self):
        try:
            exe_path = install_ffmpeg(
                status_callback=self.statusChanged.emit,
                progress_callback=self.progressChanged.emit
            )
            self.finished.emit(True, exe_path)
        except Exception as exc:  # pragma: no cover - handled via UI message
            self.finished.emit(False, str(exc))


class ShaderSettingsWidget(QWidget):
    """UI for editing shader approximation overrides."""

    def __init__(
        self,
        shader_registry: ShaderRegistry,
        game_path: Optional[str] = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.shader_registry = shader_registry
        self._pending_overrides: Dict[str, Dict[str, Any]] = dict(
            shader_registry.get_override_payloads()
        )
        self.current_shader: Optional[str] = None
        self.game_path = Path(game_path) if game_path else None
        self.shader_dir = self._compute_shader_dir()
        self.texture_dir = self._compute_texture_dir()
        self._build_ui()
        self.refresh_shader_list()

    # ------------------------------------------------------------------ helpers
    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        left_panel = QVBoxLayout()
        self.shader_list = QListWidget()
        self.shader_list.currentItemChanged.connect(self._on_shader_selected)
        left_panel.addWidget(self.shader_list)

        self.add_shader_btn = QPushButton("Add Shader Override")
        self.add_shader_btn.clicked.connect(self._add_shader_entry)
        left_panel.addWidget(self.add_shader_btn)
        layout.addLayout(left_panel, 1)

        form_container = QGroupBox("Shader Override")
        form_layout = QFormLayout(form_container)

        self.display_name_edit = QLineEdit()
        form_layout.addRow("Display Name:", self.display_name_edit)

        self.fragment_edit = QLineEdit()
        fragment_row = QHBoxLayout()
        fragment_row.addWidget(self.fragment_edit)
        self.fragment_browse_btn = QPushButton("Browse…")
        self.fragment_browse_btn.clicked.connect(
            lambda: self._browse_file(
                self.fragment_edit,
                "GLSL Files (*.glsl);;All Files (*)",
                self.shader_dir,
            )
        )
        fragment_row.addWidget(self.fragment_browse_btn)
        form_layout.addRow("Fragment Shader:", fragment_row)

        self.vertex_edit = QLineEdit()
        vertex_row = QHBoxLayout()
        vertex_row.addWidget(self.vertex_edit)
        self.vertex_browse_btn = QPushButton("Browse…")
        self.vertex_browse_btn.clicked.connect(
            lambda: self._browse_file(
                self.vertex_edit,
                "GLSL Files (*.glsl);;All Files (*)",
                self.shader_dir,
            )
        )
        vertex_row.addWidget(self.vertex_browse_btn)
        form_layout.addRow("Vertex Shader:", vertex_row)

        self.lut_edit = QLineEdit()
        lut_row = QHBoxLayout()
        lut_row.addWidget(self.lut_edit)
        self.lut_browse_btn = QPushButton("Browse…")
        self.lut_browse_btn.clicked.connect(
            lambda: self._browse_file(
                self.lut_edit,
                "Images (*.png *.jpg *.avif *.dds *.exr);;All Files (*)",
                self.texture_dir,
            )
        )
        lut_row.addWidget(self.lut_browse_btn)
        form_layout.addRow("LUT/Texture:", lut_row)
        self.texture_hint_label = QLabel(self._texture_hint_text())
        self.texture_hint_label.setStyleSheet("color: gray; font-size: 9pt;")
        form_layout.addRow("", self.texture_hint_label)

        color_row = QHBoxLayout()
        self.color_spins: List[QDoubleSpinBox] = []
        labels = ['R', 'G', 'B']
        for label in labels:
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 4.0)
            spin.setSingleStep(0.05)
            spin.setDecimals(3)
            spin.setSuffix(f" {label}")
            spin.setToolTip(f"{label} channel multiplier")
            color_row.addWidget(spin)
            self.color_spins.append(spin)
        form_layout.addRow("Color Scale:", color_row)

        self.alpha_spin = QDoubleSpinBox()
        self.alpha_spin.setRange(0.0, 4.0)
        self.alpha_spin.setSingleStep(0.05)
        self.alpha_spin.setDecimals(3)
        form_layout.addRow("Alpha Scale:", self.alpha_spin)

        self.blend_combo = QComboBox()
        self.blend_combo.addItem("Follow Layer (default)", "")
        self.blend_combo.addItem("Standard", "STANDARD")
        self.blend_combo.addItem("Premult Alpha", "PREMULT_ALPHA")
        self.blend_combo.addItem("Additive", "ADDITIVE")
        self.blend_combo.addItem("Multiply", "MULTIPLY")
        self.blend_combo.addItem("Screen", "SCREEN")
        self.blend_combo.addItem("Inherit", "INHERIT")
        form_layout.addRow("Blend Override:", self.blend_combo)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("Optional notes about shader usage or replacement GLSL.")
        self.notes_edit.setFixedHeight(80)
        form_layout.addRow("Notes:", self.notes_edit)

        button_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Override")
        self.save_btn.clicked.connect(self._save_current_override)
        button_row.addWidget(self.save_btn)
        self.reset_btn = QPushButton("Reset to Default")
        self.reset_btn.clicked.connect(self._reset_current_override)
        button_row.addWidget(self.reset_btn)
        self.clear_btn = QPushButton("Clear Override")
        self.clear_btn.clicked.connect(self._clear_current_override)
        button_row.addWidget(self.clear_btn)
        form_layout.addRow(button_row)

        right_panel = QVBoxLayout()
        right_panel.addWidget(form_container)
        right_panel.addStretch()
        layout.addLayout(right_panel, 2)

    # ---------------------------------------------------------------- actions
    def refresh_shader_list(self):
        names = set(name.lower() for name in self.shader_registry.list_shader_names())
        names |= set(self._pending_overrides.keys())
        current = self.current_shader
        self.shader_list.clear()
        for name in sorted(names):
            preset = self._effective_payload(name)
            display = preset.get("display_name", name)
            item_text = f"{display} ({name})" if display.lower() != name else name
            self.shader_list.addItem(item_text)
            self.shader_list.item(self.shader_list.count() - 1).setData(Qt.ItemDataRole.UserRole, name)

        if current:
            for idx in range(self.shader_list.count()):
                item = self.shader_list.item(idx)
                if item.data(Qt.ItemDataRole.UserRole) == current:
                    self.shader_list.setCurrentRow(idx)
                    break
        elif self.shader_list.count():
            self.shader_list.setCurrentRow(0)
        else:
            self._load_shader(None)

    def _on_shader_selected(self, current, previous):
        key = current.data(Qt.ItemDataRole.UserRole) if current else None
        self._load_shader(key)

    def _load_shader(self, shader_name: Optional[str]):
        self.current_shader = shader_name
        enabled = shader_name is not None
        for widget in [
            self.display_name_edit,
            self.fragment_edit,
            self.fragment_browse_btn,
            self.vertex_edit,
            self.vertex_browse_btn,
            self.lut_edit,
            self.lut_browse_btn,
            self.alpha_spin,
            self.blend_combo,
            self.notes_edit,
            self.save_btn,
            self.reset_btn,
            self.clear_btn,
        ] + self.color_spins:
            widget.setEnabled(enabled)
        if not shader_name:
            self.display_name_edit.clear()
            self.fragment_edit.clear()
            self.vertex_edit.clear()
            self.lut_edit.clear()
            self.notes_edit.clear()
            for spin in self.color_spins:
                spin.setValue(1.0)
            self.alpha_spin.setValue(1.0)
            self.blend_combo.setCurrentIndex(0)
            return

        payload = self._effective_payload(shader_name)
        self.display_name_edit.setText(payload.get("display_name", shader_name))
        color = payload.get("color_scale", [1.0, 1.0, 1.0])
        for spin, value in zip(self.color_spins, color):
            spin.setValue(float(value))
        self.alpha_spin.setValue(float(payload.get("alpha_scale", 1.0)))
        self.fragment_edit.setText(payload.get("fragment", ""))
        self.vertex_edit.setText(payload.get("vertex", ""))
        self.lut_edit.setText(payload.get("lut", ""))
        blend_value = payload.get("blend_override", "")
        idx = self.blend_combo.findData(blend_value if blend_value is not None else "")
        if idx == -1:
            idx = 0
        self.blend_combo.setCurrentIndex(idx)
        self.notes_edit.setPlainText(payload.get("notes", ""))

    def _effective_payload(self, shader_name: str) -> Dict[str, Any]:
        base = self.shader_registry.get_default_preset(shader_name)
        payload = base.to_dict() if base else {
            "display_name": shader_name,
            "color_scale": [1.0, 1.0, 1.0],
            "alpha_scale": 1.0
        }
        override = self._pending_overrides.get(shader_name.lower())
        if override:
            payload = dict(payload)
            payload.update(override)
        return payload

    def _base_payload(self, shader_name: str) -> Dict[str, Any]:
        base = self.shader_registry.get_default_preset(shader_name)
        if base:
            return base.to_dict()
        return {"display_name": shader_name, "color_scale": [1.0, 1.0, 1.0], "alpha_scale": 1.0}

    def _browse_file(self, line_edit: QLineEdit, filter_str: str, default_dir: Optional[Path] = None):
        start_dir = line_edit.text().strip()
        if not start_dir and default_dir is not None:
            start_dir = str(default_dir)
        if not start_dir:
            start_dir = str(Path.home())
        filename, _ = QFileDialog.getOpenFileName(self, "Select File", start_dir, filter_str)
        if filename:
            line_edit.setText(filename)

    def _collect_form_payload(self) -> Dict[str, Any]:
        if not self.current_shader:
            return {}
        payload: Dict[str, Any] = {}
        base = self._base_payload(self.current_shader)
        display = self.display_name_edit.text().strip() or self.current_shader
        if display != base.get("display_name"):
            payload["display_name"] = display
        color_values = [spin.value() for spin in self.color_spins]
        if list(map(float, color_values)) != [float(v) for v in base.get("color_scale", [1.0, 1.0, 1.0])]:
            payload["color_scale"] = color_values
        alpha = self.alpha_spin.value()
        if float(alpha) != float(base.get("alpha_scale", 1.0)):
            payload["alpha_scale"] = alpha
        fragment = self.fragment_edit.text().strip()
        if fragment:
            payload["fragment"] = fragment
        vertex = self.vertex_edit.text().strip()
        if vertex:
            payload["vertex"] = vertex
        lut = self.lut_edit.text().strip()
        if lut:
            payload["lut"] = lut
        blend_data = self.blend_combo.currentData()
        if blend_data:
            payload["blend_override"] = blend_data
        notes = self.notes_edit.toPlainText().strip()
        if notes:
            payload["notes"] = notes
        return payload

    def _save_current_override(self):
        if not self.current_shader:
            return
        payload = self._collect_form_payload()
        key = self.current_shader.lower()
        if payload:
            self._pending_overrides[key] = payload
        else:
            self._pending_overrides.pop(key, None)
        self.refresh_shader_list()

    def _reset_current_override(self):
        if not self.current_shader:
            return
        self._pending_overrides.pop(self.current_shader.lower(), None)
        self._load_shader(self.current_shader)

    def _clear_current_override(self):
        if not self.current_shader:
            return
        key = self.current_shader.lower()
        if self.shader_registry.get_default_preset(self.current_shader) is None:
            # Custom entry, remove entirely
            self._pending_overrides.pop(key, None)
            self.current_shader = None
            self.refresh_shader_list()
        else:
            self._pending_overrides.pop(key, None)
            self._load_shader(self.current_shader)

    def _add_shader_entry(self):
        name, ok = QInputDialog.getText(self, "Add Shader Override", "Enter shader resource name:")
        if not ok or not name.strip():
            return
        key = name.strip().lower()
        if key in (item.data(Qt.ItemDataRole.UserRole) for item in self._iter_list_items()):
            QMessageBox.information(self, "Shader Override", "Entry already exists.")
            return
        self._pending_overrides[key] = {"display_name": name.strip()}
        self.current_shader = key
        self.refresh_shader_list()

    def _iter_list_items(self):
        for idx in range(self.shader_list.count()):
            yield self.shader_list.item(idx)

    def get_overrides(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._pending_overrides)

    def reset_overrides(self):
        self._pending_overrides = dict(self.shader_registry.get_override_payloads())
        self.refresh_shader_list()

    # -------------------------------------------------------- default dirs/hints

    def _compute_shader_dir(self) -> Path:
        if self.game_path:
            candidate = self.game_path / "data" / "shaders"
            if candidate.exists():
                return candidate
        example = self.shader_registry.project_root / "My Singing Monsters Game Filesystem Example" / "data" / "shaders"
        if example.exists():
            return example
        return Path.home()

    def _compute_texture_dir(self) -> Path:
        if self.game_path:
            costume_dir = self.game_path / "data" / "gfx" / "costumes"
            if costume_dir.exists():
                return costume_dir
            gfx_dir = self.game_path / "data" / "gfx"
            if gfx_dir.exists():
                return gfx_dir
        example = self.shader_registry.project_root / "My Singing Monsters Game Filesystem Example" / "data" / "gfx" / "costumes"
        if example.exists():
            return example
        return Path.home()

    def _texture_hint_text(self) -> str:
        if self.texture_dir and self.texture_dir.exists():
            return f"Hint: Costume shader textures live in '{self.texture_dir}'."
        return "Hint: Costume shader textures are usually under data/gfx/costumes in the game files."


class SettingsDialog(QDialog):
    """Settings dialog with export options"""
    
    def __init__(
        self,
        export_settings: ExportSettings,
        app_settings: QSettings,
        shader_registry: ShaderRegistry,
        game_path: Optional[str],
        parent=None,
    ):
        super().__init__(parent)
        self.export_settings = export_settings
        self.app_settings = app_settings
        self.game_path = Path(game_path) if game_path else None
        self._game_path_str = game_path
        self.shader_registry = shader_registry
        self._bin_converter_paths = self._discover_bin_converters()
        self._dof_converter_path = self._discover_dof_converter()
        self._dof_input_cache_root: Optional[str] = None
        self._dof_input_cache_entries: List[Tuple[str, str]] = []
        self.anim_transfer_source_payload: Optional[Dict[str, Any]] = None
        self.anim_transfer_target_payload: Optional[Dict[str, Any]] = None
        self.anim_transfer_source_path: Optional[str] = None
        self.anim_transfer_target_path: Optional[str] = None
        self._reset_viewport_bg_to_default_on_save: bool = False
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)
        self.setMinimumHeight(450)
        self.ffmpeg_thread: QThread | None = None
        self.ffmpeg_worker: FFmpegInstallWorker | None = None
        self.ffmpeg_install_running = False
        
        self.init_ui()
        self.load_current_settings()

    def _sync_dof_particle_cap_slider(self, value: int) -> None:
        if not hasattr(self, "dof_particle_cap_slider"):
            return
        slider_value = max(
            self.dof_particle_cap_slider.minimum(),
            min(self.dof_particle_cap_slider.maximum(), int(value)),
        )
        if self.dof_particle_cap_slider.value() == slider_value:
            return
        self.dof_particle_cap_slider.blockSignals(True)
        try:
            self.dof_particle_cap_slider.setValue(slider_value)
        finally:
            self.dof_particle_cap_slider.blockSignals(False)

    def _sync_dof_particle_sensitivity_slider(self, value: float) -> None:
        if not hasattr(self, "dof_particle_sensitivity_slider"):
            return
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            numeric_value = 0.5
        slider_value = max(
            self.dof_particle_sensitivity_slider.minimum(),
            min(self.dof_particle_sensitivity_slider.maximum(), int(round(numeric_value * 100))),
        )
        if self.dof_particle_sensitivity_slider.value() == slider_value:
            return
        self.dof_particle_sensitivity_slider.blockSignals(True)
        try:
            self.dof_particle_sensitivity_slider.setValue(slider_value)
        finally:
            self.dof_particle_sensitivity_slider.blockSignals(False)

    def _update_universal_export_controls(self) -> None:
        enabled = bool(
            getattr(self, "universal_export_explicit_resolution_check", None)
            and self.universal_export_explicit_resolution_check.isChecked()
        )
        for widget in (
            getattr(self, "universal_export_preset_combo", None),
            getattr(self, "universal_export_width_spin", None),
            getattr(self, "universal_export_height_spin", None),
        ):
            if widget is not None:
                widget.setEnabled(enabled)
        if hasattr(self, "universal_export_aspect_label"):
            self.universal_export_aspect_label.setEnabled(enabled)

    def _update_universal_export_aspect_label(self) -> None:
        if not hasattr(self, "universal_export_aspect_label"):
            return
        width = max(1, int(self.universal_export_width_spin.value()))
        height = max(1, int(self.universal_export_height_spin.value()))
        divisor = math.gcd(width, height) if width and height else 1
        ratio_w = width // divisor if divisor else width
        ratio_h = height // divisor if divisor else height
        ratio_value = width / float(height) if height else 0.0
        self.universal_export_aspect_label.setText(
            f"Aspect ratio: {ratio_w}:{ratio_h} ({ratio_value:.3f}:1)"
        )

    def _set_universal_export_preset_index(self, index: int) -> None:
        if not hasattr(self, "universal_export_preset_combo"):
            return
        if index < 0 or index >= self.universal_export_preset_combo.count():
            index = 0
        if self.universal_export_preset_combo.currentIndex() == index:
            return
        self.universal_export_preset_combo.blockSignals(True)
        try:
            self.universal_export_preset_combo.setCurrentIndex(index)
        finally:
            self.universal_export_preset_combo.blockSignals(False)

    def _sync_universal_export_preset_from_resolution(self) -> None:
        if not hasattr(self, "universal_export_preset_combo"):
            return
        width = int(self.universal_export_width_spin.value())
        height = int(self.universal_export_height_spin.value())
        matched_index = 0
        for idx in range(1, self.universal_export_preset_combo.count()):
            data = self.universal_export_preset_combo.itemData(idx)
            if isinstance(data, tuple) and len(data) == 2 and data[0] == width and data[1] == height:
                matched_index = idx
                break
        self._set_universal_export_preset_index(matched_index)

    def _on_universal_export_resolution_changed(self) -> None:
        self._update_universal_export_aspect_label()
        self._sync_universal_export_preset_from_resolution()

    def _apply_universal_export_preset(self) -> None:
        if not hasattr(self, "universal_export_preset_combo"):
            return
        preset = self.universal_export_preset_combo.currentData()
        if not (isinstance(preset, tuple) and len(preset) == 2):
            self._update_universal_export_aspect_label()
            return
        width, height = int(preset[0]), int(preset[1])
        for widget, value in (
            (self.universal_export_width_spin, width),
            (self.universal_export_height_spin, height),
        ):
            widget.blockSignals(True)
            try:
                widget.setValue(value)
            finally:
                widget.blockSignals(False)
        self._update_universal_export_aspect_label()

    def _wrap_scrollable_tab(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        return scroll
    
    def init_ui(self):
        """Initialize the UI"""
        layout = QVBoxLayout(self)
        
        # Tab widget for different settings categories
        self.tab_widget = QTabWidget()
        
        # PNG Settings
        png_group = QGroupBox("PNG Export Settings")
        png_layout = QFormLayout()
        
        self.png_compression_spin = QSpinBox()
        self.png_compression_spin.setRange(0, 9)
        self.png_compression_spin.setToolTip("0 = No compression (fastest, largest)\n9 = Maximum compression (slowest, smallest)")
        png_layout.addRow("Compression Level (0-9):", self.png_compression_spin)

        self.png_full_res_check = QCheckBox("Enable")
        self.png_full_res_check.setToolTip(
            "When enabled, PNG exports render using the raw sprite bounds so no details are lost."
        )
        self.png_full_res_check.stateChanged.connect(self._update_png_full_res_controls)
        png_layout.addRow("Full Resolution Output:", self.png_full_res_check)

        self.png_full_res_multiplier_spin = QDoubleSpinBox()
        self.png_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.png_full_res_multiplier_spin.setSingleStep(0.25)
        self.png_full_res_multiplier_spin.setDecimals(2)
        self.png_full_res_multiplier_spin.setToolTip(
            "Additional scale multiplier applied when PNG full-resolution mode is enabled."
        )
        self.png_full_res_multiplier_spin.setEnabled(False)
        png_layout.addRow("Full Res Scale Multiplier:", self.png_full_res_multiplier_spin)
        
        png_info = QLabel("Higher compression = smaller file but slower export")
        png_info.setStyleSheet("color: gray; font-size: 9pt;")
        png_layout.addRow("", png_info)
        
        png_group.setLayout(png_layout)

        png_tab = QWidget()
        png_tab_layout = QVBoxLayout(png_tab)
        png_tab_layout.addWidget(png_group)
        png_tab_layout.addStretch()
        self.tab_widget.addTab(self._wrap_scrollable_tab(png_tab), "PNG")

        # GIF Settings
        gif_group = QGroupBox("GIF Export Settings")
        gif_layout = QFormLayout()
        
        self.gif_fps_spin = QSpinBox()
        self.gif_fps_spin.setRange(1, 60)
        self.gif_fps_spin.setToolTip("Frames per second for GIF animation (up to 60 FPS)")
        self.gif_fps_spin.valueChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("FPS:", self.gif_fps_spin)
        
        self.gif_colors_combo = QComboBox()
        self.gif_colors_combo.addItems(['16', '32', '64', '128', '256'])
        self.gif_colors_combo.setToolTip("Number of colors in the GIF palette\nMore colors = better quality but larger file")
        self.gif_colors_combo.currentTextChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("Colors:", self.gif_colors_combo)
        
        self.gif_scale_spin = QSpinBox()
        self.gif_scale_spin.setRange(10, 200)
        self.gif_scale_spin.setSuffix("%")
        self.gif_scale_spin.setToolTip("Scale the output GIF (100% = original size)")
        self.gif_scale_spin.valueChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("Output Scale:", self.gif_scale_spin)
        
        self.gif_dither_check = QCheckBox()
        self.gif_dither_check.setToolTip("Apply dithering to reduce color banding")
        self.gif_dither_check.stateChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("Dithering:", self.gif_dither_check)
        
        self.gif_optimize_check = QCheckBox()
        self.gif_optimize_check.setToolTip("Optimize GIF for smaller file size")
        self.gif_optimize_check.stateChanged.connect(self.update_gif_estimate)
        gif_layout.addRow("Optimize:", self.gif_optimize_check)
        
        self.gif_loop_spin = QSpinBox()
        self.gif_loop_spin.setRange(0, 100)
        self.gif_loop_spin.setSpecialValueText("Infinite")
        self.gif_loop_spin.setToolTip("0 = Loop forever, 1+ = specific number of loops")
        gif_layout.addRow("Loop Count:", self.gif_loop_spin)
        
        # GIF size estimate
        estimate_frame = QFrame()
        estimate_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        estimate_layout = QVBoxLayout(estimate_frame)
        
        self.gif_estimate_label = QLabel("Estimated file size: ~0 KB")
        self.gif_estimate_label.setStyleSheet("font-weight: bold;")
        estimate_layout.addWidget(self.gif_estimate_label)
        
        self.gif_quality_label = QLabel("Quality: High")
        self.gif_quality_label.setStyleSheet("color: green;")
        estimate_layout.addWidget(self.gif_quality_label)
        
        gif_layout.addRow("", estimate_frame)
        
        gif_group.setLayout(gif_layout)

        gif_tab = QWidget()
        gif_tab_layout = QVBoxLayout(gif_tab)
        gif_tab_layout.addWidget(gif_group)
        gif_tab_layout.addStretch()
        self.tab_widget.addTab(self._wrap_scrollable_tab(gif_tab), "GIF")

        # MOV Settings
        mov_group = QGroupBox("MOV/Video Export Settings")
        mov_layout = QFormLayout()
        
        self.mov_codec_combo = QComboBox()
        self.mov_codec_combo.addItem('prores_ks - ProRes 4444 (Best for Adobe)', 'prores_ks')
        self.mov_codec_combo.addItem('png - PNG Codec (Good Alpha)', 'png')
        self.mov_codec_combo.addItem('qtrle - QuickTime Animation (Legacy)', 'qtrle')
        self.mov_codec_combo.addItem('libx264 - H.264 (CPU, No Alpha, Smallest)', 'libx264')
        self.mov_codec_combo.addItem('h264_nvenc - H.264 NVENC (NVIDIA GPU, No Alpha)', 'h264_nvenc')
        self.mov_codec_combo.addItem('hevc_nvenc - HEVC NVENC (NVIDIA GPU, No Alpha)', 'hevc_nvenc')
        self.mov_codec_combo.setToolTip(
            "Video codec for MOV export.\n"
            "ProRes 4444 is recommended for Adobe Premiere/After Effects.\n"
            "NVENC options require an NVIDIA GPU and an FFmpeg build with NVENC enabled."
        )
        self.mov_codec_combo.currentTextChanged.connect(self.update_mov_estimate)
        mov_layout.addRow("Codec:", self.mov_codec_combo)
        
        self.mov_quality_combo = QComboBox()
        self.mov_quality_combo.addItems(['Low', 'Medium', 'High', 'Lossless'])
        self.mov_quality_combo.setCurrentText('High')
        self.mov_quality_combo.currentTextChanged.connect(self.update_mov_estimate)
        mov_layout.addRow("Quality:", self.mov_quality_combo)

        self.mov_include_audio_check = QCheckBox("Embed audio track if available")
        self.mov_include_audio_check.setToolTip("Include the loaded monster audio in the exported MOV when available")
        mov_layout.addRow("", self.mov_include_audio_check)

        full_res_row = QHBoxLayout()
        self.mov_full_res_check = QCheckBox("Enable")
        self.mov_full_res_check.setToolTip(
            "When enabled, MOV exports render at the raw sprite bounds for every frame, "
            "so no per-layer detail is lost."
        )
        self.mov_full_res_check.stateChanged.connect(self._update_mov_full_res_controls)
        full_res_row.addWidget(self.mov_full_res_check)

        self.mov_full_res_multiplier_spin = QDoubleSpinBox()
        self.mov_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.mov_full_res_multiplier_spin.setSingleStep(0.25)
        self.mov_full_res_multiplier_spin.setDecimals(2)
        self.mov_full_res_multiplier_spin.setToolTip(
            "Additional scale multiplier applied when MOV full-resolution mode is enabled."
        )
        self.mov_full_res_multiplier_spin.setEnabled(False)
        full_res_row.addWidget(self.mov_full_res_multiplier_spin)

        mov_layout.addRow("Full Resolution Output:", full_res_row)
        
        # MOV size estimate
        mov_estimate_frame = QFrame()
        mov_estimate_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        mov_estimate_layout = QVBoxLayout(mov_estimate_frame)
        
        self.mov_estimate_label = QLabel("Estimated file size: ~0 MB")
        self.mov_estimate_label.setStyleSheet("font-weight: bold;")
        mov_estimate_layout.addWidget(self.mov_estimate_label)
        
        self.mov_alpha_label = QLabel("Alpha: Supported")
        self.mov_alpha_label.setStyleSheet("color: green;")
        mov_estimate_layout.addWidget(self.mov_alpha_label)
        
        mov_layout.addRow("", mov_estimate_frame)
        
        mov_group.setLayout(mov_layout)

        mov_tab = QWidget()
        mov_tab_layout = QVBoxLayout(mov_tab)
        mov_tab_layout.addWidget(mov_group)
        mov_tab_layout.addStretch()
        self.tab_widget.addTab(self._wrap_scrollable_tab(mov_tab), "MOV")

        # WEBM Settings
        webm_group = QGroupBox("WEBM Export Settings")
        webm_layout = QFormLayout()

        self.webm_codec_combo = QComboBox()
        self.webm_codec_combo.addItems([
            'libvpx-vp9 - VP9 (Alpha, Discord Recommended)',
            'libaom-av1 - AV1 (Experimental, Alpha)',
            'libvpx - VP8 (Legacy, No Alpha)'
        ])
        self.webm_codec_combo.currentTextChanged.connect(self.update_webm_estimate)
        webm_layout.addRow("Codec:", self.webm_codec_combo)

        self.webm_crf_spin = QSpinBox()
        self.webm_crf_spin.setRange(0, 63)
        self.webm_crf_spin.setValue(28)
        self.webm_crf_spin.setToolTip("Quality/CRF (lower = better quality, higher = smaller files)")
        self.webm_crf_spin.valueChanged.connect(self.update_webm_estimate)
        webm_layout.addRow("Quality (CRF):", self.webm_crf_spin)

        self.webm_speed_spin = QSpinBox()
        self.webm_speed_spin.setRange(0, 8)
        self.webm_speed_spin.setValue(4)
        self.webm_speed_spin.setToolTip("Encoding speed (lower = slower encode but potentially smaller files)")
        webm_layout.addRow("Encoder Speed:", self.webm_speed_spin)

        self.webm_include_audio_check = QCheckBox("Embed audio track if available")
        webm_layout.addRow("", self.webm_include_audio_check)

        webm_full_res_row = QHBoxLayout()
        self.webm_full_res_check = QCheckBox("Enable")
        self.webm_full_res_check.setToolTip(
            "When enabled, WEBM exports render at the raw sprite bounds for every frame to preserve detail."
        )
        self.webm_full_res_check.stateChanged.connect(self._update_webm_full_res_controls)
        webm_full_res_row.addWidget(self.webm_full_res_check)

        self.webm_full_res_multiplier_spin = QDoubleSpinBox()
        self.webm_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.webm_full_res_multiplier_spin.setSingleStep(0.25)
        self.webm_full_res_multiplier_spin.setDecimals(2)
        self.webm_full_res_multiplier_spin.setToolTip(
            "Additional scale multiplier applied when WEBM full-resolution mode is enabled."
        )
        self.webm_full_res_multiplier_spin.setEnabled(False)
        webm_full_res_row.addWidget(self.webm_full_res_multiplier_spin)

        webm_layout.addRow("Full Resolution Output:", webm_full_res_row)

        # WEBM size/alpha estimate
        webm_estimate_frame = QFrame()
        webm_estimate_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        webm_estimate_layout = QVBoxLayout(webm_estimate_frame)

        self.webm_estimate_label = QLabel("Estimated file size: ~0 MB")
        self.webm_estimate_label.setStyleSheet("font-weight: bold;")
        webm_estimate_layout.addWidget(self.webm_estimate_label)

        self.webm_alpha_label = QLabel("Alpha: Supported")
        self.webm_alpha_label.setStyleSheet("color: green;")
        webm_estimate_layout.addWidget(self.webm_alpha_label)

        webm_layout.addRow("", webm_estimate_frame)
        
        webm_group.setLayout(webm_layout)

        webm_tab = QWidget()
        webm_tab_layout = QVBoxLayout(webm_tab)
        webm_tab_layout.addWidget(webm_group)
        webm_tab_layout.addStretch()
        self.tab_widget.addTab(self._wrap_scrollable_tab(webm_tab), "WEBM")

        # MP4 Settings
        mp4_group = QGroupBox("MP4 Export Settings")
        mp4_layout = QFormLayout()

        self.mp4_codec_combo = QComboBox()
        self.mp4_codec_combo.addItem("libx264 - H.264 (CPU, Most compatible)", "libx264")
        self.mp4_codec_combo.addItem("libx265 - H.265 / HEVC (CPU, Smaller files, slower)", "libx265")
        self.mp4_codec_combo.addItem("h264_nvenc - H.264 NVENC (NVIDIA GPU)", "h264_nvenc")
        self.mp4_codec_combo.addItem("hevc_nvenc - HEVC NVENC (NVIDIA GPU)", "hevc_nvenc")
        self.mp4_codec_combo.currentTextChanged.connect(self.update_mp4_estimate)
        mp4_layout.addRow("Codec:", self.mp4_codec_combo)

        self.mp4_crf_spin = QSpinBox()
        self.mp4_crf_spin.setRange(0, 51)
        self.mp4_crf_spin.setValue(18)
        self.mp4_crf_spin.setToolTip("Quality/CRF (lower = higher quality, higher = smaller files)")
        self.mp4_crf_spin.valueChanged.connect(self.update_mp4_estimate)
        mp4_layout.addRow("Quality (CRF):", self.mp4_crf_spin)

        self.mp4_preset_combo = QComboBox()
        self.mp4_preset_combo.addItems([
            "ultrafast", "superfast", "veryfast", "faster", "fast",
            "medium", "slow", "slower", "veryslow",
        ])
        self.mp4_preset_combo.setCurrentText("medium")
        self.mp4_preset_combo.currentTextChanged.connect(self.update_mp4_estimate)
        mp4_layout.addRow("Encoder Preset:", self.mp4_preset_combo)

        self.mp4_bitrate_spin = QSpinBox()
        self.mp4_bitrate_spin.setRange(0, 200000)
        self.mp4_bitrate_spin.setSingleStep(500)
        self.mp4_bitrate_spin.setToolTip("Optional video bitrate cap in kbps (0 = use CRF only)")
        self.mp4_bitrate_spin.valueChanged.connect(self.update_mp4_estimate)
        mp4_layout.addRow("Bitrate Cap (kbps):", self.mp4_bitrate_spin)

        self.mp4_include_audio_check = QCheckBox("Embed audio track if available")
        mp4_layout.addRow("", self.mp4_include_audio_check)

        pixel_fmt_row = QHBoxLayout()
        self.mp4_pixel_format_combo = QComboBox()
        self.mp4_pixel_format_combo.addItems([
            "yuv420p - Maximum compatibility",
            "yuv444p - 4:4:4 chroma (limited support)",
        ])
        self.mp4_pixel_format_combo.currentTextChanged.connect(self.update_mp4_estimate)
        pixel_fmt_row.addWidget(self.mp4_pixel_format_combo)
        mp4_layout.addRow("Pixel Format:", pixel_fmt_row)

        self.mp4_faststart_check = QCheckBox("Optimize for streaming (faststart)")
        self.mp4_faststart_check.setToolTip("Moves MP4 metadata to the start of the file for faster playback on the web.")
        mp4_layout.addRow("", self.mp4_faststart_check)

        mp4_full_res_row = QHBoxLayout()
        self.mp4_full_res_check = QCheckBox("Enable")
        self.mp4_full_res_check.setToolTip(
            "When enabled, MP4 exports render at the raw sprite bounds each frame to maximize detail."
        )
        self.mp4_full_res_check.stateChanged.connect(self._update_mp4_full_res_controls)
        mp4_full_res_row.addWidget(self.mp4_full_res_check)

        self.mp4_full_res_multiplier_spin = QDoubleSpinBox()
        self.mp4_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.mp4_full_res_multiplier_spin.setSingleStep(0.25)
        self.mp4_full_res_multiplier_spin.setDecimals(2)
        self.mp4_full_res_multiplier_spin.setToolTip("Additional scale multiplier when MP4 full-resolution mode is enabled.")
        self.mp4_full_res_multiplier_spin.setEnabled(False)
        mp4_full_res_row.addWidget(self.mp4_full_res_multiplier_spin)
        mp4_layout.addRow("Full Resolution Output:", mp4_full_res_row)

        mp4_estimate_frame = QFrame()
        mp4_estimate_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        mp4_estimate_layout = QVBoxLayout(mp4_estimate_frame)

        self.mp4_estimate_label = QLabel("Estimated file size: ~0 MB")
        self.mp4_estimate_label.setStyleSheet("font-weight: bold;")
        mp4_estimate_layout.addWidget(self.mp4_estimate_label)

        self.mp4_alpha_label = QLabel("Alpha: Not Supported (opaque output)")
        self.mp4_alpha_label.setStyleSheet("color: red;")
        mp4_estimate_layout.addWidget(self.mp4_alpha_label)

        mp4_layout.addRow("", mp4_estimate_frame)

        mp4_group.setLayout(mp4_layout)

        mp4_tab = QWidget()
        mp4_tab_layout = QVBoxLayout(mp4_tab)
        mp4_tab_layout.addWidget(mp4_group)
        mp4_tab_layout.addStretch()
        self.tab_widget.addTab(self._wrap_scrollable_tab(mp4_tab), "MP4")

        # PSD Settings
        psd_group = QGroupBox("PSD Export Settings")
        psd_layout = QFormLayout()
        
        self.psd_hidden_check = QCheckBox()
        self.psd_hidden_check.setToolTip("Include hidden layers in PSD export")
        psd_layout.addRow("Include Hidden Layers:", self.psd_hidden_check)

        self.psd_full_res_check = QCheckBox("Preserve sprite-native resolution")
        self.psd_full_res_check.setToolTip(
            "Ignores the PSD scale and viewer zoom so each sprite layer is exported at its native resolution."
        )
        self.psd_full_res_check.stateChanged.connect(self._update_psd_full_res_controls)
        psd_layout.addRow("Full Resolution Output:", self.psd_full_res_check)

        self.psd_full_res_multiplier_spin = QDoubleSpinBox()
        self.psd_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.psd_full_res_multiplier_spin.setSingleStep(0.25)
        self.psd_full_res_multiplier_spin.setDecimals(2)
        self.psd_full_res_multiplier_spin.setToolTip(
            "Additional scale multiplier to apply on top of the native sprite resolution "
            "when 'Full Resolution Output' is enabled."
        )
        self.psd_full_res_multiplier_spin.setEnabled(False)
        psd_layout.addRow("Full Res Scale Multiplier:", self.psd_full_res_multiplier_spin)
        
        self.psd_quality_combo = QComboBox()
        self.psd_quality_combo.addItem("Fast (Nearest)", "fast")
        self.psd_quality_combo.addItem("Balanced (Bilinear)", "balanced")
        self.psd_quality_combo.addItem("High (Bicubic)", "high")
        self.psd_quality_combo.addItem("Maximum (Lanczos)", "maximum")
        self.psd_quality_combo.setToolTip("Higher quality uses more advanced filtering when transforming sprites")
        psd_layout.addRow("Layer Quality:", self.psd_quality_combo)
        
        self.psd_scale_spin = QSpinBox()
        self.psd_scale_spin.setRange(25, 400)
        self.psd_scale_spin.setSuffix("%")
        self.psd_scale_spin.setToolTip("Scale exported PSD relative to the current viewport size")
        psd_layout.addRow("Resolution Scale:", self.psd_scale_spin)
        
        self.psd_compression_combo = QComboBox()
        self.psd_compression_combo.addItem("Uncompressed (Largest, Fastest)", "raw")
        self.psd_compression_combo.addItem("RLE Compression (Smaller, Slower)", "rle")
        self.psd_compression_combo.setToolTip("RLE compression creates smaller PSDs at the cost of a slightly slower export")
        psd_layout.addRow("Channel Compression:", self.psd_compression_combo)
        
        self.psd_crop_check = QCheckBox()
        self.psd_crop_check.setToolTip("Trim extra transparent pixels so the PSD canvas only covers visible content")
        psd_layout.addRow("Crop Canvas to Content:", self.psd_crop_check)
        
        self.psd_match_viewport_check = QCheckBox()
        self.psd_match_viewport_check.setToolTip("When enabled, PSD exports follow the viewer zoom/pan.\nWhen disabled, layers export at their full sprite resolution.")
        psd_layout.addRow("Match Viewer Zoom/Pan:", self.psd_match_viewport_check)
        
        psd_info = QLabel("Tweaking quality and compression affects export time and PSD size.")
        psd_info.setStyleSheet("color: gray; font-size: 9pt;")
        psd_layout.addRow("", psd_info)
        
        psd_group.setLayout(psd_layout)

        psd_tab = QWidget()
        psd_tab_layout = QVBoxLayout(psd_tab)
        psd_tab_layout.addWidget(psd_group)
        psd_tab_layout.addStretch()
        self.tab_widget.addTab(self._wrap_scrollable_tab(psd_tab), "PSD")

        # AE Rig Settings
        ae_group = QGroupBox("After Effects Rig Export")
        ae_layout = QFormLayout()

        self.ae_rig_mode_combo = QComboBox()
        self.ae_rig_mode_combo.addItem("Auto (recommended)", "auto")
        self.ae_rig_mode_combo.addItem("Rig + per-layer bakes (keep precomps)", "rig")
        self.ae_rig_mode_combo.addItem("Mesh rig (triangle layers)", "mesh")
        self.ae_rig_mode_combo.addItem("Composite bake only (fastest)", "composite")
        self.ae_rig_mode_combo.setToolTip(
            "Auto keeps the normal rig/precomp export and switches to a single composite bake "
            "when polygon mesh layers dominate. Rig mode forces per-layer bakes (slow) to keep "
            "segments. Mesh rig converts polygon sprites into triangle layers. Composite mode "
            "always outputs one baked sequence."
        )
        ae_layout.addRow("Export Mode:", self.ae_rig_mode_combo)

        self.ae_full_res_check = QCheckBox("Preserve sprite-native resolution")
        self.ae_full_res_check.setToolTip(
            "Ignores the AE resolution scale and exports at the sprite's native resolution."
        )
        self.ae_full_res_check.stateChanged.connect(self._update_ae_full_res_controls)
        ae_layout.addRow("Full Resolution Output:", self.ae_full_res_check)

        self.ae_full_res_multiplier_spin = QDoubleSpinBox()
        self.ae_full_res_multiplier_spin.setRange(1.0, 8.0)
        self.ae_full_res_multiplier_spin.setSingleStep(0.25)
        self.ae_full_res_multiplier_spin.setDecimals(2)
        self.ae_full_res_multiplier_spin.setToolTip(
            "Additional scale multiplier to apply on top of the native sprite resolution "
            "when 'Full Resolution Output' is enabled."
        )
        self.ae_full_res_multiplier_spin.setEnabled(False)
        ae_layout.addRow("Full Res Scale Multiplier:", self.ae_full_res_multiplier_spin)

        self.ae_quality_combo = QComboBox()
        self.ae_quality_combo.addItem("Fast (Nearest)", "fast")
        self.ae_quality_combo.addItem("Balanced (Bilinear)", "balanced")
        self.ae_quality_combo.addItem("High (Bicubic)", "high")
        self.ae_quality_combo.addItem("Maximum (Lanczos)", "maximum")
        self.ae_quality_combo.setToolTip(
            "Higher quality uses more advanced filtering when resampling sprite PNGs."
        )
        ae_layout.addRow("Layer Quality:", self.ae_quality_combo)

        self.ae_scale_spin = QSpinBox()
        self.ae_scale_spin.setRange(25, 400)
        self.ae_scale_spin.setSuffix("%")
        self.ae_scale_spin.setToolTip("Scale exported AE rig relative to the current viewport size")
        ae_layout.addRow("Resolution Scale:", self.ae_scale_spin)

        self.ae_compression_combo = QComboBox()
        self.ae_compression_combo.addItem("Uncompressed (Largest, Fastest)", "raw")
        self.ae_compression_combo.addItem("RLE Compression (Smaller, Slower)", "rle")
        self.ae_compression_combo.setToolTip(
            "RLE compression creates smaller PNGs at the cost of a slightly slower export."
        )
        ae_layout.addRow("Channel Compression:", self.ae_compression_combo)

        self.ae_match_viewport_check = QCheckBox()
        self.ae_match_viewport_check.setToolTip(
            "When enabled, AE rigs follow the viewer zoom/pan.\n"
            "When disabled, camera/zoom is reset for a neutral framing."
        )
        ae_layout.addRow("Match Viewer Zoom/Pan:", self.ae_match_viewport_check)

        ae_info = QLabel(
            "Polygon mesh sprites cannot be rebuilt as editable layers in AE. "
            "Mesh rig mode approximates them with triangle layers (heavy but editable). "
            "Resolution Scale and Full Resolution Output control the final rig resolution."
        )
        ae_info.setWordWrap(True)
        ae_info.setStyleSheet("color: gray; font-size: 9pt;")
        ae_layout.addRow("", ae_info)

        ae_group.setLayout(ae_layout)

        ae_tab = QWidget()
        ae_tab_layout = QVBoxLayout(ae_tab)
        ae_tab_layout.addWidget(ae_group)
        ae_tab_layout.addStretch()
        self.tab_widget.addTab(self._wrap_scrollable_tab(ae_tab), "AE Rig")

        # Shader settings tab
        shader_game_path = str(self.game_path) if self.game_path else self._game_path_str
        self.shader_tab = ShaderSettingsWidget(
            self.shader_registry,
            shader_game_path,
        )
        self.tab_widget.addTab(self._wrap_scrollable_tab(self.shader_tab), "Shaders")

        # BIN converter tab
        bin_tab = QWidget()
        bin_layout = QVBoxLayout(bin_tab)

        converter_group = QGroupBox("BIN Revision Converter")
        converter_form = QFormLayout()

        self.bin_convert_mode_combo = QComboBox()
        self.bin_convert_mode_combo.addItem("BIN -> JSON", "bin_to_json")
        self.bin_convert_mode_combo.addItem("JSON -> BIN", "json_to_bin")
        self.bin_convert_mode_combo.addItem("BIN -> BIN (Change Revision)", "bin_to_bin")
        self.bin_convert_mode_combo.addItem("DOF ANIMBBB -> JSON/XML/PNG", "dof_to_json")
        self.bin_convert_mode_combo.currentIndexChanged.connect(self._update_bin_convert_controls)
        converter_form.addRow("Conversion Mode:", self.bin_convert_mode_combo)

        input_row = QHBoxLayout()
        self.bin_convert_input_edit = QLineEdit()
        self.bin_convert_input_edit.setPlaceholderText("Select a .bin or .json file")
        input_row.addWidget(self.bin_convert_input_edit)
        self.bin_convert_input_browse = QPushButton("Browse...")
        self.bin_convert_input_browse.clicked.connect(self._browse_bin_convert_input)
        input_row.addWidget(self.bin_convert_input_browse)
        converter_form.addRow("Input File:", input_row)

        output_row = QHBoxLayout()
        self.bin_convert_output_edit = QLineEdit()
        self.bin_convert_output_edit.setPlaceholderText("Output file path")
        output_row.addWidget(self.bin_convert_output_edit)
        self.bin_convert_output_browse = QPushButton("Browse...")
        self.bin_convert_output_browse.clicked.connect(self._browse_bin_convert_output)
        output_row.addWidget(self.bin_convert_output_browse)
        converter_form.addRow("Output File:", output_row)

        self.bin_source_combo = QComboBox()
        self.bin_source_combo.addItem("Auto (recommended)", "auto")
        self.bin_source_combo.addItem("Rev6", "rev6")
        self.bin_source_combo.addItem("Rev4", "rev4")
        self.bin_source_combo.addItem("Rev2", "rev2")
        self.bin_source_combo.addItem("Legacy (early mobile)", "legacy")
        self.bin_source_combo.addItem("Choir (Monster Choir)", "choir")
        self.bin_source_combo.addItem("Muppets", "muppets")
        self.bin_source_combo.addItem("Oldest (launch build)", "oldest")
        converter_form.addRow("BIN Source Converter:", self.bin_source_combo)

        self.bin_target_combo = QComboBox()
        self.bin_target_combo.addItem("Rev6", 6)
        self.bin_target_combo.addItem("Rev4", 4)
        self.bin_target_combo.addItem("Rev2", 2)
        self.bin_target_combo.currentIndexChanged.connect(self._update_bin_convert_controls)
        converter_form.addRow("Target BIN Revision:", self.bin_target_combo)

        self.bin_upgrade_blend_check = QCheckBox("Upgrade legacy blend values when targeting Rev6")
        self.bin_upgrade_blend_check.setToolTip(
            "Remaps blend=1 to blend=2 and sets blend_version=2 for older exports."
        )
        converter_form.addRow("", self.bin_upgrade_blend_check)

        self.bin_keep_json_check = QCheckBox("Keep intermediate JSON when converting BIN -> BIN")
        converter_form.addRow("", self.bin_keep_json_check)

        self.bin_copy_xml_check = QCheckBox("Copy XML resources (and textures) to output")
        self.bin_copy_xml_check.setToolTip(
            "Copies referenced XML sheets (and their image files) alongside the exported BIN."
        )
        converter_form.addRow("", self.bin_copy_xml_check)

        converter_group.setLayout(converter_form)
        bin_layout.addWidget(converter_group)

        self.dof_convert_group = QGroupBox("DOF Conversion Options")
        dof_form = QFormLayout()

        dof_root_row = QHBoxLayout()
        self.dof_assets_root_edit = QLineEdit()
        self.dof_assets_root_edit.setPlaceholderText("Select DOF assets root (folder containing msmdata/)")
        dof_root_row.addWidget(self.dof_assets_root_edit)
        self.dof_assets_root_browse = QPushButton("Browse...")
        self.dof_assets_root_browse.clicked.connect(self._browse_dof_assets_root)
        dof_root_row.addWidget(self.dof_assets_root_browse)
        dof_form.addRow("DOF Assets Root:", dof_root_row)
        self.dof_assets_root_edit.editingFinished.connect(self._refresh_dof_input_options)

        dof_input_row = QHBoxLayout()
        self.dof_input_combo = QComboBox()
        self.dof_input_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.dof_input_combo.currentIndexChanged.connect(self._on_dof_input_selected)
        dof_input_row.addWidget(self.dof_input_combo, 1)
        self.dof_input_refresh_btn = QPushButton("Refresh")
        self.dof_input_refresh_btn.clicked.connect(lambda: self._refresh_dof_input_options(force=True))
        dof_input_row.addWidget(self.dof_input_refresh_btn)
        dof_form.addRow("Auto Input:", dof_input_row)

        self.dof_mesh_pivot_checkbox = QCheckBox("Use pivot-local mesh vertices")
        self.dof_mesh_pivot_checkbox.setToolTip(
            "Skip Sprite.m_Offset when decoding mesh vertices/bounds."
        )
        dof_form.addRow("", self.dof_mesh_pivot_checkbox)

        self.dof_include_mesh_xml_checkbox = QCheckBox("Include mesh data in atlas XML")
        self.dof_include_mesh_xml_checkbox.setToolTip(
            "Adds vertices/verticesUV/triangles blocks to exported atlas XML."
        )
        dof_form.addRow("", self.dof_include_mesh_xml_checkbox)

        self.dof_premultiply_alpha_checkbox = QCheckBox("Premultiply atlas alpha")
        self.dof_premultiply_alpha_checkbox.setToolTip(
            "Export atlas PNGs with premultiplied alpha (like Sprite Workshop)."
        )
        self.dof_premultiply_alpha_checkbox.stateChanged.connect(
            self._sync_dof_premultiply_from_converter
        )
        dof_form.addRow("", self.dof_premultiply_alpha_checkbox)

        self.dof_alpha_hardness_slider = QSlider(Qt.Orientation.Horizontal)
        self.dof_alpha_hardness_slider.setRange(0, 200)
        self.dof_alpha_hardness_slider.setValue(0)
        self.dof_alpha_hardness_spin = QDoubleSpinBox()
        self.dof_alpha_hardness_spin.setRange(0.0, 2.0)
        self.dof_alpha_hardness_spin.setDecimals(2)
        self.dof_alpha_hardness_spin.setSingleStep(0.05)
        self.dof_alpha_hardness_spin.setValue(0.0)
        self.dof_alpha_hardness_slider.valueChanged.connect(
            lambda v: self.dof_alpha_hardness_spin.setValue(float(v) / 100.0)
        )
        self.dof_alpha_hardness_spin.valueChanged.connect(
            lambda v: self.dof_alpha_hardness_slider.setValue(int(round(float(v) * 100.0)))
        )
        self.dof_alpha_hardness_spin.setToolTip(
            "Hardens split-alpha edges after resize. "
            "0.0 = no hardening, higher values tighten feathered edges."
        )
        hardness_row = QHBoxLayout()
        hardness_row.addWidget(self.dof_alpha_hardness_slider, 1)
        hardness_row.addWidget(self.dof_alpha_hardness_spin, 0)
        dof_form.addRow("Alpha Edge Hardness:", hardness_row)

        self.dof_hires_xml_checkbox = QCheckBox("Set hires attribute in atlas XML")
        self.dof_hires_xml_checkbox.setToolTip(
            "When enabled, exported XML uses hires=\"true\". Disable to force hires=\"false\"."
        )
        self.dof_hires_xml_checkbox.stateChanged.connect(
            self._sync_dof_hires_from_converter
        )
        dof_form.addRow("", self.dof_hires_xml_checkbox)

        self.dof_swap_anchor_report_checkbox = QCheckBox("Write swap-anchor report JSON")
        self.dof_swap_anchor_report_checkbox.setToolTip(
            "Write a JSON report with swap-frame alignment stats next to the output."
        )
        dof_form.addRow("", self.dof_swap_anchor_report_checkbox)

        self.dof_swap_anchor_edge_align_checkbox = QCheckBox("Swap edge alignment (auto)")
        self.dof_swap_anchor_edge_align_checkbox.setToolTip(
            "Align swap sprites by best-fit edge/center using mesh bounds."
        )
        dof_form.addRow("", self.dof_swap_anchor_edge_align_checkbox)

        self.dof_swap_anchor_pivot_offset_checkbox = QCheckBox("Swap pivot-offset alignment")
        self.dof_swap_anchor_pivot_offset_checkbox.setToolTip(
            "Align swap sprites by Sprite.m_Offset pivot positions (mesh sprites)."
        )
        dof_form.addRow("", self.dof_swap_anchor_pivot_offset_checkbox)

        self.dof_swap_anchor_report_override_checkbox = QCheckBox("Swap report override")
        self.dof_swap_anchor_report_override_checkbox.setToolTip(
            "Use an existing swap-anchor report JSON to override per-node modes."
        )
        dof_form.addRow("", self.dof_swap_anchor_report_override_checkbox)

        self.dof_bundle_anim_edit = QLineEdit()
        self.dof_bundle_anim_edit.setPlaceholderText("e.g. A_tweedle_adult_03_cloud_01.ANIMBBB")
        self.dof_bundle_anim_edit.setToolTip(
            "Required when converting a Unity bundle (__data) instead of a .ANIMBBB.asset."
        )
        dof_form.addRow("Bundle ANIMBBB:", self.dof_bundle_anim_edit)

        dof_hint = QLabel("Tip: use an input like bundle://SomeAnim.ANIMBBB for bundle entries.")
        dof_hint.setWordWrap(True)
        dof_hint.setStyleSheet("color: gray; font-size: 9pt;")
        dof_form.addRow("", dof_hint)

        self.dof_convert_group.setLayout(dof_form)
        self.dof_convert_group.setVisible(False)
        bin_layout.addWidget(self.dof_convert_group)

        status_group = QGroupBox("Converter Log")
        status_layout = QVBoxLayout()
        self.bin_convert_log = QPlainTextEdit()
        self.bin_convert_log.setReadOnly(True)
        self.bin_convert_log.setFixedHeight(140)
        status_layout.addWidget(self.bin_convert_log)
        self.bin_convert_run_btn = QPushButton("Run Conversion")
        self.bin_convert_run_btn.clicked.connect(self._run_bin_revision_conversion)
        status_layout.addWidget(self.bin_convert_run_btn)
        availability_hint = QLabel(self._bin_converter_availability_text())
        availability_hint.setWordWrap(True)
        availability_hint.setStyleSheet("color: gray; font-size: 9pt;")
        status_layout.addWidget(availability_hint)
        status_group.setLayout(status_layout)
        bin_layout.addWidget(status_group)
        bin_layout.addStretch()
        self.tab_widget.addTab(self._wrap_scrollable_tab(bin_tab), "BIN Converter")

        # Animation transfer tab
        anim_tab = QWidget()
        anim_layout = QVBoxLayout(anim_tab)

        source_group = QGroupBox("Source Animation File")
        source_layout = QFormLayout()
        source_row = QHBoxLayout()
        self.anim_transfer_source_edit = QLineEdit()
        self.anim_transfer_source_edit.setPlaceholderText("Select a source .bin or .json file")
        source_row.addWidget(self.anim_transfer_source_edit)
        self.anim_transfer_source_browse = QPushButton("Browse...")
        self.anim_transfer_source_browse.clicked.connect(self._browse_anim_transfer_source)
        source_row.addWidget(self.anim_transfer_source_browse)
        source_layout.addRow("Source File:", source_row)

        source_pick_row = QHBoxLayout()
        self.anim_transfer_source_use_dof = QCheckBox("DOF")
        self.anim_transfer_source_use_dof.setToolTip("Toggle to list DOF output animations instead of game BIN/JSON.")
        self.anim_transfer_source_use_dof.stateChanged.connect(
            lambda _state: self._refresh_anim_transfer_source_options()
        )
        source_pick_row.addWidget(self.anim_transfer_source_use_dof)
        self.anim_transfer_source_combo = QComboBox()
        self.anim_transfer_source_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.anim_transfer_source_combo.currentIndexChanged.connect(
            lambda _idx: self._apply_anim_transfer_source_pick()
        )
        source_pick_row.addWidget(self.anim_transfer_source_combo, 1)
        self.anim_transfer_source_refresh = QPushButton("Refresh")
        self.anim_transfer_source_refresh.clicked.connect(self._refresh_anim_transfer_source_options)
        source_pick_row.addWidget(self.anim_transfer_source_refresh)
        source_layout.addRow("Quick Pick:", source_pick_row)

        self.anim_transfer_source_label = QLabel("No source loaded.")
        self.anim_transfer_source_label.setStyleSheet("color: gray; font-size: 9pt;")
        source_layout.addRow("", self.anim_transfer_source_label)

        self.anim_transfer_source_list = QListWidget()
        source_layout.addRow("Animations:", self.anim_transfer_source_list)

        self.anim_transfer_load_source_btn = QPushButton("Load Source Animations")
        self.anim_transfer_load_source_btn.clicked.connect(self._load_anim_transfer_source)
        source_layout.addRow("", self.anim_transfer_load_source_btn)
        source_group.setLayout(source_layout)
        anim_layout.addWidget(source_group)

        target_group = QGroupBox("Target Animation File")
        target_layout = QFormLayout()
        target_row = QHBoxLayout()
        self.anim_transfer_target_edit = QLineEdit()
        self.anim_transfer_target_edit.setPlaceholderText("Select a target .bin or .json file")
        target_row.addWidget(self.anim_transfer_target_edit)
        self.anim_transfer_target_browse = QPushButton("Browse...")
        self.anim_transfer_target_browse.clicked.connect(self._browse_anim_transfer_target)
        target_row.addWidget(self.anim_transfer_target_browse)
        target_layout.addRow("Target File:", target_row)

        target_pick_row = QHBoxLayout()
        self.anim_transfer_target_use_dof = QCheckBox("DOF")
        self.anim_transfer_target_use_dof.setToolTip("Toggle to list DOF output animations instead of game BIN/JSON.")
        self.anim_transfer_target_use_dof.stateChanged.connect(
            lambda _state: self._refresh_anim_transfer_target_options()
        )
        target_pick_row.addWidget(self.anim_transfer_target_use_dof)
        self.anim_transfer_target_combo = QComboBox()
        self.anim_transfer_target_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.anim_transfer_target_combo.currentIndexChanged.connect(
            lambda _idx: self._apply_anim_transfer_target_pick()
        )
        target_pick_row.addWidget(self.anim_transfer_target_combo, 1)
        self.anim_transfer_target_refresh = QPushButton("Refresh")
        self.anim_transfer_target_refresh.clicked.connect(self._refresh_anim_transfer_target_options)
        target_pick_row.addWidget(self.anim_transfer_target_refresh)
        target_layout.addRow("Quick Pick:", target_pick_row)

        self.anim_transfer_target_label = QLabel("No target loaded.")
        self.anim_transfer_target_label.setStyleSheet("color: gray; font-size: 9pt;")
        target_layout.addRow("", self.anim_transfer_target_label)

        self.anim_transfer_target_list = QListWidget()
        target_layout.addRow("Animations:", self.anim_transfer_target_list)

        self.anim_transfer_load_target_btn = QPushButton("Load Target Animations")
        self.anim_transfer_load_target_btn.clicked.connect(self._load_anim_transfer_target)
        target_layout.addRow("", self.anim_transfer_load_target_btn)
        target_group.setLayout(target_layout)
        anim_layout.addWidget(target_group)

        options_group = QGroupBox("Transfer Options")
        options_layout = QFormLayout()

        self.anim_transfer_select_mode = QComboBox()
        self.anim_transfer_select_mode.addItem("All animations", "all")
        self.anim_transfer_select_mode.addItem("By name list", "name")
        self.anim_transfer_select_mode.addItem("By index list", "index")
        self.anim_transfer_select_mode.addItem("By wildcard", "wildcard")
        options_layout.addRow("Copy Subset:", self.anim_transfer_select_mode)

        self.anim_transfer_select_input = QLineEdit()
        self.anim_transfer_select_input.setPlaceholderText("e.g. Idle, Dance, Sleep, Store OR Island number (01-infinity)")
        options_layout.addRow("Subset Filter:", self.anim_transfer_select_input)

        self.anim_transfer_rename_mode = QComboBox()
        self.anim_transfer_rename_mode.addItem("None", "none")
        self.anim_transfer_rename_mode.addItem("Exact match", "exact")
        self.anim_transfer_rename_mode.addItem("Prefix", "prefix")
        self.anim_transfer_rename_mode.addItem("Suffix", "suffix")
        self.anim_transfer_rename_mode.addItem("Regex", "regex")
        options_layout.addRow("Rename Mode:", self.anim_transfer_rename_mode)

        rename_row = QHBoxLayout()
        self.anim_transfer_rename_find = QLineEdit()
        self.anim_transfer_rename_find.setPlaceholderText("Find / Match")
        rename_row.addWidget(self.anim_transfer_rename_find)
        self.anim_transfer_rename_replace = QLineEdit()
        self.anim_transfer_rename_replace.setPlaceholderText("Replace / New")
        rename_row.addWidget(self.anim_transfer_rename_replace)
        options_layout.addRow("Rename Rule:", rename_row)

        self.anim_transfer_duplicate_mode = QComboBox()
        self.anim_transfer_duplicate_mode.addItem("Overwrite duplicates", "overwrite")
        self.anim_transfer_duplicate_mode.addItem("Skip duplicates", "skip")
        options_layout.addRow("Duplicates:", self.anim_transfer_duplicate_mode)

        self.anim_transfer_merge_mode = QComboBox()
        self.anim_transfer_merge_mode.addItem("Merge with target", "merge")
        self.anim_transfer_merge_mode.addItem("Replace target list", "replace")
        options_layout.addRow("Merge Strategy:", self.anim_transfer_merge_mode)

        self.anim_transfer_order_mode = QComboBox()
        self.anim_transfer_order_mode.addItem("Keep target ordering", "keep")
        self.anim_transfer_order_mode.addItem("Append source after target", "append")
        options_layout.addRow("Ordering:", self.anim_transfer_order_mode)

        self.anim_transfer_preserve_target = QCheckBox("Preserve target animation settings on name match")
        options_layout.addRow("", self.anim_transfer_preserve_target)

        meta_row = QHBoxLayout()
        self.anim_transfer_rev_check = QCheckBox("Set rev")
        meta_row.addWidget(self.anim_transfer_rev_check)
        self.anim_transfer_rev_spin = QSpinBox()
        self.anim_transfer_rev_spin.setRange(1, 10)
        self.anim_transfer_rev_spin.setValue(6)
        meta_row.addWidget(self.anim_transfer_rev_spin)
        options_layout.addRow("Metadata:", meta_row)

        blend_row = QHBoxLayout()
        self.anim_transfer_blend_check = QCheckBox("Set blend_version")
        blend_row.addWidget(self.anim_transfer_blend_check)
        self.anim_transfer_blend_spin = QSpinBox()
        self.anim_transfer_blend_spin.setRange(1, 3)
        self.anim_transfer_blend_spin.setValue(2)
        blend_row.addWidget(self.anim_transfer_blend_spin)
        options_layout.addRow("", blend_row)

        size_row = QHBoxLayout()
        self.anim_transfer_size_check = QCheckBox("Set width/height")
        size_row.addWidget(self.anim_transfer_size_check)
        self.anim_transfer_width_spin = QSpinBox()
        self.anim_transfer_width_spin.setRange(1, 8192)
        self.anim_transfer_width_spin.setValue(512)
        size_row.addWidget(self.anim_transfer_width_spin)
        self.anim_transfer_height_spin = QSpinBox()
        self.anim_transfer_height_spin.setRange(1, 8192)
        self.anim_transfer_height_spin.setValue(512)
        size_row.addWidget(self.anim_transfer_height_spin)
        options_layout.addRow("", size_row)

        centered_row = QHBoxLayout()
        self.anim_transfer_centered_check = QCheckBox("Set centered")
        centered_row.addWidget(self.anim_transfer_centered_check)
        self.anim_transfer_centered_combo = QComboBox()
        self.anim_transfer_centered_combo.addItem("0", 0)
        self.anim_transfer_centered_combo.addItem("1", 1)
        self.anim_transfer_centered_combo.addItem("2", 2)
        self.anim_transfer_centered_combo.addItem("3", 3)
        centered_row.addWidget(self.anim_transfer_centered_combo)
        options_layout.addRow("", centered_row)

        remap_row = QHBoxLayout()
        self.anim_transfer_reindex_layers = QCheckBox("Reindex layer ids")
        remap_row.addWidget(self.anim_transfer_reindex_layers)
        self.anim_transfer_normalize_src = QCheckBox("Normalize src indices to int16")
        self.anim_transfer_normalize_src.setChecked(True)
        remap_row.addWidget(self.anim_transfer_normalize_src)
        options_layout.addRow("Remap:", remap_row)

        dof_header = QLabel("DOF Deploy")
        dof_header.setStyleSheet("font-weight: bold;")
        options_layout.addRow("", dof_header)

        dof_input_row = QHBoxLayout()
        self.dof_deploy_input_edit = QLineEdit()
        self.dof_deploy_input_edit.setPlaceholderText(
            "Select a .ANIMBBB asset, __data bundle, or bundle://Name"
        )
        dof_input_row.addWidget(self.dof_deploy_input_edit)
        self.dof_deploy_input_browse = QPushButton("Browse...")
        self.dof_deploy_input_browse.clicked.connect(self._browse_dof_deploy_input)
        dof_input_row.addWidget(self.dof_deploy_input_browse)
        options_layout.addRow("DOF Input:", dof_input_row)

        dof_assets_row = QHBoxLayout()
        self.dof_deploy_assets_root_edit = QLineEdit()
        self.dof_deploy_assets_root_edit.setPlaceholderText(
            "Select DOF assets root (folder containing msmdata/)"
        )
        dof_assets_row.addWidget(self.dof_deploy_assets_root_edit)
        self.dof_deploy_assets_root_browse = QPushButton("Browse...")
        self.dof_deploy_assets_root_browse.clicked.connect(self._browse_dof_deploy_assets_root)
        dof_assets_row.addWidget(self.dof_deploy_assets_root_browse)
        options_layout.addRow("DOF Assets Root:", dof_assets_row)

        dof_game_row = QHBoxLayout()
        self.dof_deploy_game_root_edit = QLineEdit()
        self.dof_deploy_game_root_edit.setPlaceholderText(
            "Select game root (folder containing data/)"
        )
        dof_game_row.addWidget(self.dof_deploy_game_root_edit)
        self.dof_deploy_game_root_browse = QPushButton("Browse...")
        self.dof_deploy_game_root_browse.clicked.connect(self._browse_dof_deploy_game_root)
        dof_game_row.addWidget(self.dof_deploy_game_root_browse)
        options_layout.addRow("Game Root:", dof_game_row)

        dof_existing_row = QHBoxLayout()
        self.dof_deploy_existing_edit = QLineEdit()
        self.dof_deploy_existing_edit.setPlaceholderText(
            "Optional: pick existing .bin/.xml/.png to reuse its name"
        )
        dof_existing_row.addWidget(self.dof_deploy_existing_edit)
        self.dof_deploy_existing_browse = QPushButton("Browse...")
        self.dof_deploy_existing_browse.clicked.connect(self._browse_dof_deploy_existing_target)
        dof_existing_row.addWidget(self.dof_deploy_existing_browse)
        options_layout.addRow("Existing Target:", dof_existing_row)

        self.dof_deploy_bin_name_edit = QLineEdit()
        self.dof_deploy_bin_name_edit.setPlaceholderText("Optional (default: converter output)")
        options_layout.addRow("BIN Name:", self.dof_deploy_bin_name_edit)

        self.dof_deploy_xml_name_edit = QLineEdit()
        self.dof_deploy_xml_name_edit.setPlaceholderText("Optional (default: converter output)")
        options_layout.addRow("XML Name:", self.dof_deploy_xml_name_edit)

        self.dof_deploy_png_name_edit = QLineEdit()
        self.dof_deploy_png_name_edit.setPlaceholderText("Optional (default: converter output)")
        options_layout.addRow("PNG Name:", self.dof_deploy_png_name_edit)

        self.dof_deploy_premultiply_alpha_checkbox = QCheckBox("Premultiply atlas alpha")
        self.dof_deploy_premultiply_alpha_checkbox.setToolTip(
            "Export atlas PNGs with premultiplied alpha (like Sprite Workshop)."
        )
        self.dof_deploy_premultiply_alpha_checkbox.stateChanged.connect(
            self._sync_dof_premultiply_from_deploy
        )
        options_layout.addRow("", self.dof_deploy_premultiply_alpha_checkbox)

        self.dof_deploy_hires_xml_checkbox = QCheckBox("Set hires attribute in atlas XML")
        self.dof_deploy_hires_xml_checkbox.setToolTip(
            "When enabled, exported XML uses hires=\"true\". Disable to force hires=\"false\"."
        )
        self.dof_deploy_hires_xml_checkbox.stateChanged.connect(
            self._sync_dof_hires_from_deploy
        )
        options_layout.addRow("", self.dof_deploy_hires_xml_checkbox)

        self.dof_deploy_replace_check = QCheckBox("Replace without backup")
        options_layout.addRow("", self.dof_deploy_replace_check)

        dof_deploy_hint = QLabel("XML imagePath will be set to gfx/monsters/<PNG> automatically.")
        dof_deploy_hint.setWordWrap(True)
        dof_deploy_hint.setStyleSheet("color: gray; font-size: 9pt;")
        options_layout.addRow("", dof_deploy_hint)

        options_group.setLayout(options_layout)
        anim_layout.addWidget(options_group)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.dof_deploy_run_btn = QPushButton("Deploy DOF Animation")
        self.dof_deploy_run_btn.clicked.connect(self._run_dof_deploy)
        action_row.addWidget(self.dof_deploy_run_btn)
        self.anim_transfer_apply_btn = QPushButton("Transfer Animations")
        self.anim_transfer_apply_btn.clicked.connect(self._apply_anim_transfer)
        action_row.addWidget(self.anim_transfer_apply_btn)
        anim_layout.addLayout(action_row)

        transfer_log_group = QGroupBox("Transfer Log")
        transfer_log_layout = QVBoxLayout()
        self.anim_transfer_log = QPlainTextEdit()
        self.anim_transfer_log.setReadOnly(True)
        self.anim_transfer_log.setFixedHeight(120)
        transfer_log_layout.addWidget(self.anim_transfer_log)
        transfer_log_group.setLayout(transfer_log_layout)
        anim_layout.addWidget(transfer_log_group)

        anim_layout.addStretch()
        self.tab_widget.addTab(self._wrap_scrollable_tab(anim_tab), "Anim Transfer")

        keybind_tab = self._build_keybinds_tab()
        self.tab_widget.addTab(self._wrap_scrollable_tab(keybind_tab), "Keybinds")

        # Application settings tab
        app_tab = QWidget()
        app_layout = QVBoxLayout(app_tab)

        camera_group = QGroupBox("Camera & View Settings")
        camera_form = QFormLayout()

        self.camera_zoom_cursor_check = QCheckBox("Zoom towards mouse cursor")
        self.camera_zoom_cursor_check.setToolTip("When enabled, scroll zooms around the cursor position instead of the animation center")
        camera_form.addRow(self.camera_zoom_cursor_check)

        camera_group.setLayout(camera_form)
        app_layout.addWidget(camera_group)

        export_group = QGroupBox("Universal Export Framing")
        export_form = QFormLayout()
        self.universal_export_scope_combo = QComboBox()
        self.universal_export_scope_combo.addItem("Current animation", "current")
        self.universal_export_scope_combo.addItem("All loaded animations", "all")
        self.universal_export_scope_combo.setToolTip(
            "Controls whether export framing uses only the current animation or the union "
            "of every loaded animation for the current monster."
        )
        export_form.addRow("Bounds Scope:", self.universal_export_scope_combo)

        self.universal_export_explicit_resolution_check = QCheckBox("Force explicit output resolution")
        self.universal_export_explicit_resolution_check.setToolTip(
            "When enabled, exports are fit into the exact width/height below while preserving aspect ratio."
        )
        self.universal_export_explicit_resolution_check.toggled.connect(
            self._update_universal_export_controls
        )
        export_form.addRow(self.universal_export_explicit_resolution_check)

        self.universal_export_preset_combo = QComboBox()
        self.universal_export_preset_combo.addItem("Custom", None)
        self.universal_export_preset_combo.addItem("1:1 Square (1080x1080)", (1080, 1080))
        self.universal_export_preset_combo.addItem("4:3 Standard (1440x1080)", (1440, 1080))
        self.universal_export_preset_combo.addItem("16:9 HD (1920x1080)", (1920, 1080))
        self.universal_export_preset_combo.addItem("21:9 Ultrawide (2560x1080)", (2560, 1080))
        self.universal_export_preset_combo.addItem("3:2 Photo (1800x1200)", (1800, 1200))
        self.universal_export_preset_combo.addItem("9:16 Vertical (1080x1920)", (1080, 1920))
        self.universal_export_preset_combo.addItem("3:4 Portrait (1080x1440)", (1080, 1440))
        self.universal_export_preset_combo.currentIndexChanged.connect(
            self._apply_universal_export_preset
        )
        self.universal_export_preset_combo.setToolTip(
            "Quick resolution presets for common export aspect ratios."
        )
        export_form.addRow("Resolution Preset:", self.universal_export_preset_combo)

        resolution_row = QHBoxLayout()
        self.universal_export_width_spin = QSpinBox()
        self.universal_export_width_spin.setRange(16, 16384)
        self.universal_export_width_spin.setValue(1920)
        self.universal_export_width_spin.setSuffix(" w")
        self.universal_export_width_spin.valueChanged.connect(self._on_universal_export_resolution_changed)
        resolution_row.addWidget(self.universal_export_width_spin)
        self.universal_export_height_spin = QSpinBox()
        self.universal_export_height_spin.setRange(16, 16384)
        self.universal_export_height_spin.setValue(1080)
        self.universal_export_height_spin.setSuffix(" h")
        self.universal_export_height_spin.valueChanged.connect(self._on_universal_export_resolution_changed)
        resolution_row.addWidget(self.universal_export_height_spin)
        export_form.addRow("Resolution:", resolution_row)

        self.universal_export_aspect_label = QLabel("Aspect ratio: 16:9")
        self.universal_export_aspect_label.setStyleSheet("color: gray; font-size: 9pt;")
        export_form.addRow("", self.universal_export_aspect_label)

        self.universal_export_padding_spin = QDoubleSpinBox()
        self.universal_export_padding_spin.setRange(0.0, 256.0)
        self.universal_export_padding_spin.setDecimals(1)
        self.universal_export_padding_spin.setSingleStep(1.0)
        self.universal_export_padding_spin.setSuffix(" px")
        self.universal_export_padding_spin.setToolTip(
            "Extra transparent padding applied around computed export bounds before framing."
        )
        export_form.addRow("Bounds Padding:", self.universal_export_padding_spin)

        export_hint = QLabel(
            "Use 'All loaded animations' to keep one monster aligned across multiple exports without recentering each animation separately."
        )
        export_hint.setWordWrap(True)
        export_hint.setStyleSheet("color: gray; font-size: 9pt;")
        export_form.addRow("", export_hint)
        export_group.setLayout(export_form)
        app_layout.addWidget(export_group)

        viewport_group = QGroupBox("Viewport Rendering")
        viewport_form = QFormLayout()
        self.sprite_filter_combo = QComboBox()
        self.sprite_filter_combo.addItem("Nearest (Pixel)", "nearest")
        self.sprite_filter_combo.addItem("Bilinear (Smooth)", "bilinear")
        self.sprite_filter_combo.addItem("Nearest Mipmap Nearest", "nearest_mipmap_nearest")
        self.sprite_filter_combo.addItem("Linear Mipmap Nearest", "linear_mipmap_nearest")
        self.sprite_filter_combo.addItem("Nearest Mipmap Linear", "nearest_mipmap_linear")
        self.sprite_filter_combo.addItem("Trilinear (Mipmapped)", "trilinear")
        self.sprite_filter_combo.addItem("Bicubic (Mitchell)", "bicubic")
        self.sprite_filter_combo.addItem("Lanczos (Approx)", "lanczos")
        self.sprite_filter_combo.addItem("Anisotropic 2x", "anisotropic_2x")
        self.sprite_filter_combo.addItem("Anisotropic 4x", "anisotropic_4x")
        self.sprite_filter_combo.addItem("Anisotropic 8x", "anisotropic_8x")
        self.sprite_filter_combo.addItem("Anisotropic 16x", "anisotropic_16x")
        self.sprite_filter_combo.setToolTip(
            "Texture sampling mode used for sprite segments in the viewport. "
            "Bicubic/Lanczos are approximated via mipmapped linear sampling."
        )
        viewport_form.addRow("Sprite Resampling:", self.sprite_filter_combo)
        self.sprite_filter_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.sprite_filter_strength_slider.setRange(0, 100)
        self.sprite_filter_strength_slider.setValue(100)
        self.sprite_filter_strength_slider.setSingleStep(1)
        self.sprite_filter_strength_slider.setPageStep(10)
        self.sprite_filter_strength_slider.setToolTip(
            "Blend between bilinear (0%) and the selected bicubic/lanczos filter (100%)."
        )
        self.sprite_filter_strength_spin = QSpinBox()
        self.sprite_filter_strength_spin.setRange(0, 100)
        self.sprite_filter_strength_spin.setSuffix("%")
        self.sprite_filter_strength_spin.setValue(100)
        self.sprite_filter_strength_spin.setToolTip(
            "Filter blend strength for bicubic/lanczos resampling."
        )
        self.sprite_filter_strength_slider.valueChanged.connect(self.sprite_filter_strength_spin.setValue)
        self.sprite_filter_strength_spin.valueChanged.connect(self.sprite_filter_strength_slider.setValue)
        strength_row = QHBoxLayout()
        strength_row.addWidget(self.sprite_filter_strength_slider, 1)
        strength_row.addWidget(self.sprite_filter_strength_spin, 0)
        viewport_form.addRow("Filter Strength:", strength_row)
        self.dof_alpha_smoothing_check = QCheckBox("Enable DOF alpha edge smoothing")
        self.dof_alpha_smoothing_check.setToolTip(
            "Applies extra alpha-edge smoothing for DOF sprites to reduce harsh outlines "
            "while preserving interior detail."
        )
        viewport_form.addRow("", self.dof_alpha_smoothing_check)
        self.dof_alpha_smoothing_mode_combo = QComboBox()
        self.dof_alpha_smoothing_mode_combo.addItem("Normal", "normal")
        self.dof_alpha_smoothing_mode_combo.addItem("Strong", "strong")
        self.dof_alpha_smoothing_mode_combo.setToolTip(
            "Normal uses moderate edge smoothing. Strong increases smoothing radius for tougher jagged edges."
        )
        viewport_form.addRow("DOF Smoothing Mode:", self.dof_alpha_smoothing_mode_combo)
        self.dof_alpha_smoothing_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.dof_alpha_smoothing_strength_slider.setRange(0, 100)
        self.dof_alpha_smoothing_strength_slider.setValue(50)
        self.dof_alpha_smoothing_strength_slider.setSingleStep(1)
        self.dof_alpha_smoothing_strength_slider.setPageStep(5)
        self.dof_alpha_smoothing_strength_slider.setToolTip(
            "DOF alpha smoothing intensity (linear 0-100%). Higher values soften edges more."
        )
        self.dof_alpha_smoothing_strength_spin = QSpinBox()
        self.dof_alpha_smoothing_strength_spin.setRange(0, 100)
        self.dof_alpha_smoothing_strength_spin.setSuffix("%")
        self.dof_alpha_smoothing_strength_spin.setValue(50)
        self.dof_alpha_smoothing_strength_spin.setToolTip(
            "DOF alpha smoothing intensity (linear 0-100%)."
        )
        self.dof_alpha_smoothing_strength_slider.valueChanged.connect(
            self.dof_alpha_smoothing_strength_spin.setValue
        )
        self.dof_alpha_smoothing_strength_spin.valueChanged.connect(
            self.dof_alpha_smoothing_strength_slider.setValue
        )
        self.dof_alpha_smoothing_check.toggled.connect(
            self.dof_alpha_smoothing_strength_slider.setEnabled
        )
        self.dof_alpha_smoothing_check.toggled.connect(
            self.dof_alpha_smoothing_strength_spin.setEnabled
        )
        self.dof_alpha_smoothing_check.toggled.connect(
            self.dof_alpha_smoothing_mode_combo.setEnabled
        )
        dof_alpha_strength_row = QHBoxLayout()
        dof_alpha_strength_row.addWidget(self.dof_alpha_smoothing_strength_slider, 1)
        dof_alpha_strength_row.addWidget(self.dof_alpha_smoothing_strength_spin, 0)
        viewport_form.addRow("DOF Edge Strength:", dof_alpha_strength_row)
        self.viewport_post_aa_check = QCheckBox("Enable post-process AA (FinalPass FXAA)")
        self.viewport_post_aa_check.setToolTip(
            "Applies a fullscreen FinalPass-style FXAA resolve after rendering the viewport."
        )
        viewport_form.addRow("", self.viewport_post_aa_check)
        self.viewport_post_aa_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.viewport_post_aa_strength_slider.setRange(0, 100)
        self.viewport_post_aa_strength_slider.setValue(50)
        self.viewport_post_aa_strength_slider.setSingleStep(1)
        self.viewport_post_aa_strength_slider.setPageStep(5)
        self.viewport_post_aa_strength_slider.setToolTip(
            "Strength of the post-process FXAA blend."
        )
        self.viewport_post_aa_strength_spin = QSpinBox()
        self.viewport_post_aa_strength_spin.setRange(0, 100)
        self.viewport_post_aa_strength_spin.setSuffix("%")
        self.viewport_post_aa_strength_spin.setValue(50)
        self.viewport_post_aa_strength_spin.setToolTip(
            "Strength of the post-process FXAA blend."
        )
        self.viewport_post_aa_strength_slider.valueChanged.connect(
            self.viewport_post_aa_strength_spin.setValue
        )
        self.viewport_post_aa_strength_spin.valueChanged.connect(
            self.viewport_post_aa_strength_slider.setValue
        )
        self.viewport_post_aa_check.toggled.connect(
            self.viewport_post_aa_strength_slider.setEnabled
        )
        self.viewport_post_aa_check.toggled.connect(
            self.viewport_post_aa_strength_spin.setEnabled
        )
        post_aa_strength_row = QHBoxLayout()
        post_aa_strength_row.addWidget(self.viewport_post_aa_strength_slider, 1)
        post_aa_strength_row.addWidget(self.viewport_post_aa_strength_spin, 0)
        viewport_form.addRow("Post AA Strength:", post_aa_strength_row)
        self.viewport_post_aa_mode_combo = QComboBox()
        self.viewport_post_aa_mode_combo.addItem("FinalPass FXAA", "fxaa")
        self.viewport_post_aa_mode_combo.addItem("SMAA Approximation", "smaa")
        self.viewport_post_aa_mode_combo.setToolTip(
            "AA mode for the viewport post-process pass."
        )
        viewport_form.addRow("Post AA Mode:", self.viewport_post_aa_mode_combo)
        self.viewport_post_motion_blur_check = QCheckBox("Enable Motion Blur")
        self.viewport_post_motion_blur_check.setToolTip(
            "Blends each frame with the previous frame in viewport post-processing."
        )
        viewport_form.addRow("", self.viewport_post_motion_blur_check)
        self.viewport_post_motion_blur_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.viewport_post_motion_blur_strength_slider.setRange(0, 100)
        self.viewport_post_motion_blur_strength_slider.setValue(35)
        self.viewport_post_motion_blur_strength_slider.setSingleStep(1)
        self.viewport_post_motion_blur_strength_slider.setPageStep(5)
        self.viewport_post_motion_blur_strength_slider.setToolTip(
            "Motion blur intensity (previous-frame blend amount)."
        )
        self.viewport_post_motion_blur_strength_spin = QSpinBox()
        self.viewport_post_motion_blur_strength_spin.setRange(0, 100)
        self.viewport_post_motion_blur_strength_spin.setSuffix("%")
        self.viewport_post_motion_blur_strength_spin.setValue(35)
        self.viewport_post_motion_blur_strength_spin.setToolTip(
            "Motion blur intensity (previous-frame blend amount)."
        )
        self.viewport_post_motion_blur_strength_slider.valueChanged.connect(
            self.viewport_post_motion_blur_strength_spin.setValue
        )
        self.viewport_post_motion_blur_strength_spin.valueChanged.connect(
            self.viewport_post_motion_blur_strength_slider.setValue
        )
        self.viewport_post_motion_blur_check.toggled.connect(
            self.viewport_post_motion_blur_strength_slider.setEnabled
        )
        self.viewport_post_motion_blur_check.toggled.connect(
            self.viewport_post_motion_blur_strength_spin.setEnabled
        )
        motion_blur_strength_row = QHBoxLayout()
        motion_blur_strength_row.addWidget(self.viewport_post_motion_blur_strength_slider, 1)
        motion_blur_strength_row.addWidget(self.viewport_post_motion_blur_strength_spin, 0)
        viewport_form.addRow("Motion Blur Strength:", motion_blur_strength_row)

        self.viewport_post_bloom_check = QCheckBox("Enable Bloom")
        self.viewport_post_bloom_check.setToolTip(
            "Adds a lightweight fullscreen bloom pass (Uber subset)."
        )
        viewport_form.addRow("", self.viewport_post_bloom_check)
        self.viewport_post_bloom_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.viewport_post_bloom_strength_slider.setRange(0, 200)
        self.viewport_post_bloom_strength_slider.setValue(15)
        self.viewport_post_bloom_strength_spin = QSpinBox()
        self.viewport_post_bloom_strength_spin.setRange(0, 200)
        self.viewport_post_bloom_strength_spin.setSuffix("%")
        self.viewport_post_bloom_strength_spin.setValue(15)
        self.viewport_post_bloom_strength_slider.valueChanged.connect(
            self.viewport_post_bloom_strength_spin.setValue
        )
        self.viewport_post_bloom_strength_spin.valueChanged.connect(
            self.viewport_post_bloom_strength_slider.setValue
        )
        bloom_strength_row = QHBoxLayout()
        bloom_strength_row.addWidget(self.viewport_post_bloom_strength_slider, 1)
        bloom_strength_row.addWidget(self.viewport_post_bloom_strength_spin, 0)
        viewport_form.addRow("Bloom Strength:", bloom_strength_row)
        self.viewport_post_bloom_threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.viewport_post_bloom_threshold_slider.setRange(0, 200)
        self.viewport_post_bloom_threshold_slider.setValue(60)
        self.viewport_post_bloom_threshold_spin = QDoubleSpinBox()
        self.viewport_post_bloom_threshold_spin.setRange(0.0, 2.0)
        self.viewport_post_bloom_threshold_spin.setDecimals(2)
        self.viewport_post_bloom_threshold_spin.setSingleStep(0.01)
        self.viewport_post_bloom_threshold_spin.setValue(0.60)
        self.viewport_post_bloom_threshold_slider.valueChanged.connect(
            lambda v: self.viewport_post_bloom_threshold_spin.setValue(float(v) / 100.0)
        )
        self.viewport_post_bloom_threshold_spin.valueChanged.connect(
            lambda v: self.viewport_post_bloom_threshold_slider.setValue(int(round(float(v) * 100.0)))
        )
        bloom_threshold_row = QHBoxLayout()
        bloom_threshold_row.addWidget(self.viewport_post_bloom_threshold_slider, 1)
        bloom_threshold_row.addWidget(self.viewport_post_bloom_threshold_spin, 0)
        viewport_form.addRow("Bloom Threshold:", bloom_threshold_row)
        self.viewport_post_bloom_radius_slider = QSlider(Qt.Orientation.Horizontal)
        self.viewport_post_bloom_radius_slider.setRange(10, 800)
        self.viewport_post_bloom_radius_slider.setValue(150)
        self.viewport_post_bloom_radius_spin = QDoubleSpinBox()
        self.viewport_post_bloom_radius_spin.setRange(0.1, 8.0)
        self.viewport_post_bloom_radius_spin.setDecimals(2)
        self.viewport_post_bloom_radius_spin.setSingleStep(0.1)
        self.viewport_post_bloom_radius_spin.setValue(1.5)
        self.viewport_post_bloom_radius_slider.valueChanged.connect(
            lambda v: self.viewport_post_bloom_radius_spin.setValue(float(v) / 100.0)
        )
        self.viewport_post_bloom_radius_spin.valueChanged.connect(
            lambda v: self.viewport_post_bloom_radius_slider.setValue(int(round(float(v) * 100.0)))
        )
        bloom_radius_row = QHBoxLayout()
        bloom_radius_row.addWidget(self.viewport_post_bloom_radius_slider, 1)
        bloom_radius_row.addWidget(self.viewport_post_bloom_radius_spin, 0)
        viewport_form.addRow("Bloom Radius:", bloom_radius_row)

        self.viewport_post_vignette_check = QCheckBox("Enable Vignette")
        viewport_form.addRow("", self.viewport_post_vignette_check)
        self.viewport_post_vignette_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.viewport_post_vignette_strength_slider.setRange(0, 100)
        self.viewport_post_vignette_strength_slider.setValue(25)
        self.viewport_post_vignette_strength_spin = QSpinBox()
        self.viewport_post_vignette_strength_spin.setRange(0, 100)
        self.viewport_post_vignette_strength_spin.setSuffix("%")
        self.viewport_post_vignette_strength_spin.setValue(25)
        self.viewport_post_vignette_strength_slider.valueChanged.connect(
            self.viewport_post_vignette_strength_spin.setValue
        )
        self.viewport_post_vignette_strength_spin.valueChanged.connect(
            self.viewport_post_vignette_strength_slider.setValue
        )
        vignette_strength_row = QHBoxLayout()
        vignette_strength_row.addWidget(self.viewport_post_vignette_strength_slider, 1)
        vignette_strength_row.addWidget(self.viewport_post_vignette_strength_spin, 0)
        viewport_form.addRow("Vignette Strength:", vignette_strength_row)

        self.viewport_post_grain_check = QCheckBox("Enable Grain")
        viewport_form.addRow("", self.viewport_post_grain_check)
        self.viewport_post_grain_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.viewport_post_grain_strength_slider.setRange(0, 100)
        self.viewport_post_grain_strength_slider.setValue(20)
        self.viewport_post_grain_strength_spin = QSpinBox()
        self.viewport_post_grain_strength_spin.setRange(0, 100)
        self.viewport_post_grain_strength_spin.setSuffix("%")
        self.viewport_post_grain_strength_spin.setValue(20)
        self.viewport_post_grain_strength_slider.valueChanged.connect(
            self.viewport_post_grain_strength_spin.setValue
        )
        self.viewport_post_grain_strength_spin.valueChanged.connect(
            self.viewport_post_grain_strength_slider.setValue
        )
        grain_strength_row = QHBoxLayout()
        grain_strength_row.addWidget(self.viewport_post_grain_strength_slider, 1)
        grain_strength_row.addWidget(self.viewport_post_grain_strength_spin, 0)
        viewport_form.addRow("Grain Strength:", grain_strength_row)

        self.viewport_post_ca_check = QCheckBox("Enable Chromatic Aberration")
        viewport_form.addRow("", self.viewport_post_ca_check)
        self.viewport_post_ca_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.viewport_post_ca_strength_slider.setRange(0, 100)
        self.viewport_post_ca_strength_slider.setValue(25)
        self.viewport_post_ca_strength_spin = QSpinBox()
        self.viewport_post_ca_strength_spin.setRange(0, 100)
        self.viewport_post_ca_strength_spin.setSuffix("%")
        self.viewport_post_ca_strength_spin.setValue(25)
        self.viewport_post_ca_strength_slider.valueChanged.connect(
            self.viewport_post_ca_strength_spin.setValue
        )
        self.viewport_post_ca_strength_spin.valueChanged.connect(
            self.viewport_post_ca_strength_slider.setValue
        )
        ca_strength_row = QHBoxLayout()
        ca_strength_row.addWidget(self.viewport_post_ca_strength_slider, 1)
        ca_strength_row.addWidget(self.viewport_post_ca_strength_spin, 0)
        viewport_form.addRow("CA Strength:", ca_strength_row)
        self.viewport_post_bloom_check.toggled.connect(
            self.viewport_post_bloom_strength_slider.setEnabled
        )
        self.viewport_post_bloom_check.toggled.connect(
            self.viewport_post_bloom_strength_spin.setEnabled
        )
        self.viewport_post_bloom_check.toggled.connect(
            self.viewport_post_bloom_threshold_slider.setEnabled
        )
        self.viewport_post_bloom_check.toggled.connect(
            self.viewport_post_bloom_threshold_spin.setEnabled
        )
        self.viewport_post_bloom_check.toggled.connect(
            self.viewport_post_bloom_radius_slider.setEnabled
        )
        self.viewport_post_bloom_check.toggled.connect(
            self.viewport_post_bloom_radius_spin.setEnabled
        )
        self.viewport_post_vignette_check.toggled.connect(
            self.viewport_post_vignette_strength_slider.setEnabled
        )
        self.viewport_post_vignette_check.toggled.connect(
            self.viewport_post_vignette_strength_spin.setEnabled
        )
        self.viewport_post_grain_check.toggled.connect(
            self.viewport_post_grain_strength_slider.setEnabled
        )
        self.viewport_post_grain_check.toggled.connect(
            self.viewport_post_grain_strength_spin.setEnabled
        )
        self.viewport_post_ca_check.toggled.connect(
            self.viewport_post_ca_strength_slider.setEnabled
        )
        self.viewport_post_ca_check.toggled.connect(
            self.viewport_post_ca_strength_spin.setEnabled
        )
        self.dof_sprite_shader_mode_combo = QComboBox()
        self.dof_sprite_shader_mode_combo.addItem("Auto (From Animation Data)", "auto")
        self.dof_sprite_shader_mode_combo.addItem("Anim2D/Normal+Alpha", "anim2d")
        self.dof_sprite_shader_mode_combo.addItem("DawnOfFire/UnlitShader (Experimental)", "dawnoffire_unlit")
        self.dof_sprite_shader_mode_combo.addItem("Sprites/Default (Experimental)", "sprites_default")
        self.dof_sprite_shader_mode_combo.addItem("Unlit/Transparent (Experimental)", "unlit_transparent")
        self.dof_sprite_shader_mode_combo.addItem(
            "Unlit/Transparent Masked (Experimental)",
            "unlit_transparent_masked",
        )
        self.dof_sprite_shader_mode_combo.setToolTip(
            "Experimental DOF sprite shader emulation override. "
            "Use this to A/B compare sprite rendering paths."
        )
        viewport_form.addRow("DOF Sprite Shader:", self.dof_sprite_shader_mode_combo)
        viewport_group.setLayout(viewport_form)
        app_layout.addWidget(viewport_group)

        particle_group = QGroupBox("DOF Particles")
        particle_form = QFormLayout()
        self.dof_particles_world_space_check = QCheckBox("Force world-space simulation")
        self.dof_particles_world_space_check.setToolTip(
            "Assume DOF particle emitters simulate in world space even when the bundle "
            "does not expose simulationSpace. Recommended for DOF monsters."
        )
        particle_form.addRow(self.dof_particles_world_space_check)
        self.dof_particle_cap_slider = QSlider(Qt.Orientation.Horizontal)
        self.dof_particle_cap_slider.setRange(0, 1000)
        self.dof_particle_cap_slider.setSingleStep(1)
        self.dof_particle_cap_slider.setPageStep(50)
        self.dof_particle_cap_slider.setValue(1000)
        self.dof_particle_cap_slider.setToolTip(
            "Upper cap for active DOF particles drawn in the viewport. "
            "Slider covers 0-1000. Type a larger value in the box to bypass the slider limit."
        )
        self.dof_particle_cap_spin = QSpinBox()
        self.dof_particle_cap_spin.setRange(0, 1000000)
        self.dof_particle_cap_spin.setSingleStep(50)
        self.dof_particle_cap_spin.setSuffix(" particles")
        self.dof_particle_cap_spin.setValue(1000)
        self.dof_particle_cap_spin.setToolTip(
            "Upper cap for active DOF particles drawn in the viewport. "
            "Values above 1000 are allowed here even though the slider stops at 1000."
        )
        self.dof_particle_cap_slider.valueChanged.connect(self.dof_particle_cap_spin.setValue)
        self.dof_particle_cap_spin.valueChanged.connect(self._sync_dof_particle_cap_slider)
        particle_cap_row = QHBoxLayout()
        particle_cap_row.addWidget(self.dof_particle_cap_slider, 1)
        particle_cap_row.addWidget(self.dof_particle_cap_spin, 0)
        particle_form.addRow("Viewport Particle Cap:", particle_cap_row)
        self.dof_particle_sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.dof_particle_sensitivity_slider.setRange(0, 200)
        self.dof_particle_sensitivity_slider.setSingleStep(1)
        self.dof_particle_sensitivity_slider.setPageStep(10)
        self.dof_particle_sensitivity_slider.setValue(50)
        self.dof_particle_sensitivity_slider.setToolTip(
            "Scales DOF rate-over-distance particle sensitivity in the viewport. "
            "Slider covers 0.00-2.00. Type a larger value in the box to exceed it."
        )
        self.dof_particle_sensitivity_spin = QDoubleSpinBox()
        self.dof_particle_sensitivity_spin.setRange(0.0, 100.0)
        self.dof_particle_sensitivity_spin.setDecimals(2)
        self.dof_particle_sensitivity_spin.setSingleStep(0.05)
        self.dof_particle_sensitivity_spin.setValue(0.50)
        self.dof_particle_sensitivity_spin.setToolTip(
            "Multiplier applied to DOF rate-over-distance particle emission sensitivity."
        )
        self.dof_particle_sensitivity_slider.valueChanged.connect(
            lambda value: self.dof_particle_sensitivity_spin.setValue(value / 100.0)
        )
        self.dof_particle_sensitivity_spin.valueChanged.connect(self._sync_dof_particle_sensitivity_slider)
        particle_sensitivity_row = QHBoxLayout()
        particle_sensitivity_row.addWidget(self.dof_particle_sensitivity_slider, 1)
        particle_sensitivity_row.addWidget(self.dof_particle_sensitivity_spin, 0)
        particle_form.addRow("Distance Sensitivity:", particle_sensitivity_row)
        particle_group.setLayout(particle_form)
        app_layout.addWidget(particle_group)

        playback_group = QGroupBox("Playback && Metronome")
        playback_layout = QVBoxLayout()
        self.metronome_default_check = QCheckBox("Enable metronome when playback starts")
        self.metronome_default_check.setToolTip(
            "If enabled, the metronome will automatically tick whenever the animation is playing."
        )
        playback_layout.addWidget(self.metronome_default_check)
        self.metronome_audible_check = QCheckBox("Enable audible metronome tick")
        self.metronome_audible_check.setToolTip("Control whether the metronome emits a click sound each beat.")
        playback_layout.addWidget(self.metronome_audible_check)
        ts_layout = QHBoxLayout()
        ts_layout.addWidget(QLabel("Default time signature:"))
        self.metronome_time_sig_num = QSpinBox()
        self.metronome_time_sig_num.setRange(1, 16)
        self.metronome_time_sig_num.setValue(4)
        ts_layout.addWidget(self.metronome_time_sig_num)
        slash = QLabel("/")
        slash.setStyleSheet("font-weight: bold;")
        ts_layout.addWidget(slash)
        self.metronome_time_sig_denom = QComboBox()
        for value in (1, 2, 4, 8, 16):
            self.metronome_time_sig_denom.addItem(str(value), value)
        idx = self.metronome_time_sig_denom.findData(4)
        if idx >= 0:
            self.metronome_time_sig_denom.setCurrentIndex(idx)
        ts_layout.addWidget(self.metronome_time_sig_denom)
        ts_layout.addStretch(1)
        playback_layout.addLayout(ts_layout)
        self.show_beat_grid_check = QCheckBox("Show beat grid in timeline")
        self.show_beat_grid_check.setToolTip("Overlay beat division lines on the timeline slider.")
        playback_layout.addWidget(self.show_beat_grid_check)
        self.beat_edit_check = QCheckBox("Allow dragging beat markers")
        self.beat_edit_check.setToolTip("Permit beat lines in the timeline to be dragged to create custom timing.")
        playback_layout.addWidget(self.beat_edit_check)
        playback_group.setLayout(playback_layout)
        app_layout.addWidget(playback_group)

        file_browser_group = QGroupBox("File Browsing")
        file_browser_layout = QVBoxLayout()
        self.barebones_browser_check = QCheckBox("Barebones search for BIN/JSON files")
        self.barebones_browser_check.setToolTip(
            "Enable to keep the classic text list. Disable to use the Monster Browser grid with portraits."
        )
        file_browser_layout.addWidget(self.barebones_browser_check)
        browser_hint = QLabel("Monster Browser loads portraits from data/gfx/book and supports auto conversion.")
        browser_hint.setWordWrap(True)
        browser_hint.setStyleSheet("color: gray; font-size: 9pt;")
        file_browser_layout.addWidget(browser_hint)
        file_browser_group.setLayout(file_browser_layout)
        app_layout.addWidget(file_browser_group)

        bin_group = QGroupBox("BIN Export")
        bin_layout = QVBoxLayout()
        self.update_source_json_check = QCheckBox("Update original JSON when saving animations")
        self.update_source_json_check.setToolTip(
            "When enabled, saving an animation also overwrites the currently loaded JSON file "
            "with the merged result."
        )
        bin_layout.addWidget(self.update_source_json_check)
        bin_hint = QLabel("Keeps the source JSON in sync without running a separate export.")
        bin_hint.setWordWrap(True)
        bin_hint.setStyleSheet("color: gray; font-size: 9pt;")
        bin_layout.addWidget(bin_hint)
        bin_group.setLayout(bin_layout)
        app_layout.addWidget(bin_group)

        ffmpeg_group = QGroupBox("FFmpeg Tools")
        ffmpeg_layout = QVBoxLayout()

        self.ffmpeg_status_label = QLabel()
        self.ffmpeg_status_label.setWordWrap(True)
        ffmpeg_layout.addWidget(self.ffmpeg_status_label)

        self.ffmpeg_progress = QProgressBar()
        self.ffmpeg_progress.setRange(0, 100)
        self.ffmpeg_progress.setVisible(False)
        ffmpeg_layout.addWidget(self.ffmpeg_progress)

        self.ffmpeg_install_button = QPushButton("Install FFmpeg")
        self.ffmpeg_install_button.clicked.connect(self.start_ffmpeg_install)
        ffmpeg_layout.addWidget(self.ffmpeg_install_button)

        ffmpeg_hint = QLabel(
            "MOV exports rely on FFmpeg. Click the button above for a "
            "one-click install that downloads and wires everything up."
        )
        ffmpeg_hint.setStyleSheet("color: gray; font-size: 9pt;")
        ffmpeg_hint.setWordWrap(True)
        ffmpeg_layout.addWidget(ffmpeg_hint)

        install_dir_label = QLabel(f"Install location: {get_install_root()}")
        install_dir_label.setStyleSheet("color: gray; font-size: 8pt;")
        install_dir_label.setWordWrap(True)
        ffmpeg_layout.addWidget(install_dir_label)

        ffmpeg_group.setLayout(ffmpeg_layout)
        app_layout.addWidget(ffmpeg_group)

        diag_group = self._build_diagnostics_group()
        app_layout.addWidget(diag_group)

        app_layout.addStretch()
        
        self.tab_widget.addTab(self._wrap_scrollable_tab(app_tab), "Application")

        self._update_bin_convert_controls()

        layout.addWidget(self.tab_widget)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.reset_btn = QPushButton("Reset to Defaults")
        self.reset_btn.clicked.connect(self.reset_to_defaults)
        button_layout.addWidget(self.reset_btn)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save_settings)
        self.save_btn.setDefault(True)
        button_layout.addWidget(self.save_btn)
        
        layout.addLayout(button_layout)

    def _build_keybinds_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.keybind_edits: Dict[str, QKeySequenceEdit] = {}

        group_boxes: Dict[str, QGroupBox] = {}
        group_layouts: Dict[str, QFormLayout] = {}

        for action in keybind_actions():
            group = action.group or "General"
            if group not in group_boxes:
                group_box = QGroupBox(f"{group} Shortcuts")
                form = QFormLayout()
                group_box.setLayout(form)
                layout.addWidget(group_box)
                group_boxes[group] = group_box
                group_layouts[group] = form

            row = QHBoxLayout()
            edit = QKeySequenceEdit()
            if hasattr(edit, "setClearButtonEnabled"):
                edit.setClearButtonEnabled(True)
            default_text = action.default or "None"
            tooltip = action.description.strip() if action.description else ""
            if tooltip:
                tooltip = f"{tooltip}\nDefault: {default_text}"
            else:
                tooltip = f"Default: {default_text}"
            edit.setToolTip(tooltip)
            row.addWidget(edit)

            clear_btn = QPushButton("Clear")
            clear_btn.clicked.connect(edit.clear)
            row.addWidget(clear_btn)

            default_btn = QPushButton("Default")
            default_btn.clicked.connect(lambda _, key=action.key: self._set_keybind_default(key))
            row.addWidget(default_btn)

            group_layouts[group].addRow(f"{action.label}:", row)
            self.keybind_edits[action.key] = edit

        hint = QLabel("Leave a field blank to disable the shortcut.")
        hint.setStyleSheet("color: gray; font-size: 9pt;")
        layout.addWidget(hint)

        reset_row = QHBoxLayout()
        reset_row.addStretch()
        reset_btn = QPushButton("Reset Keybinds to Defaults")
        reset_btn.clicked.connect(self._reset_keybinds_to_defaults)
        reset_row.addWidget(reset_btn)
        layout.addLayout(reset_row)

        layout.addStretch()
        return tab
    
    def _build_diagnostics_group(self) -> QGroupBox:
        group = QGroupBox("Diagnostics & Logging")
        layout = QFormLayout()

        self.diag_enable_check = QCheckBox("Enable diagnostics logging")
        self.diag_enable_check.toggled.connect(self._update_diag_controls)
        layout.addRow(self.diag_enable_check)

        self.diag_highlight_check = QCheckBox("Highlight problem layers in the list")
        layout.addRow(self.diag_highlight_check)

        self.diag_throttle_check = QCheckBox("Throttle layer status updates")
        layout.addRow(self.diag_throttle_check)

        self.diag_clone_check = QCheckBox("Log costume clone operations")
        layout.addRow(self.diag_clone_check)

        self.diag_canonical_check = QCheckBox("Log canonical/base clone seeding")
        layout.addRow(self.diag_canonical_check)

        self.diag_remap_check = QCheckBox("Log remap/swaps")
        layout.addRow(self.diag_remap_check)

        self.diag_sheet_check = QCheckBox("Log sheet alias activity")
        layout.addRow(self.diag_sheet_check)

        self.anchor_debug_check = QCheckBox("Enable anchor_debug.txt exports")
        self.anchor_debug_check.setToolTip(
            "When enabled, the viewer records detailed anchor metadata to anchor_debug.txt after loads. "
            "Disable if the extra logging causes slowdowns."
        )
        layout.addRow(self.anchor_debug_check)

        self.diag_visibility_check = QCheckBox("Log visibility toggles")
        layout.addRow(self.diag_visibility_check)

        self.diag_shader_check = QCheckBox("Log shader overrides")
        layout.addRow(self.diag_shader_check)

        self.diag_color_check = QCheckBox("Log tint/layer color overrides")
        layout.addRow(self.diag_color_check)

        self.diag_attachment_check = QCheckBox("Log attachment loading")
        layout.addRow(self.diag_attachment_check)

        self.diag_debug_payload_check = QCheckBox("Include debug payloads in log file")
        layout.addRow(self.diag_debug_payload_check)

        self.diag_min_severity_combo = QComboBox()
        self.diag_min_severity_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        layout.addRow("Minimum Severity:", self.diag_min_severity_combo)

        self.diag_update_interval_spin = QSpinBox()
        self.diag_update_interval_spin.setRange(50, 5000)
        self.diag_update_interval_spin.setSuffix(" ms")
        layout.addRow("Layer Status Refresh Interval:", self.diag_update_interval_spin)

        self.diag_status_duration_spin = QDoubleSpinBox()
        self.diag_status_duration_spin.setRange(1.0, 60.0)
        self.diag_status_duration_spin.setDecimals(1)
        self.diag_status_duration_spin.setSuffix(" sec")
        layout.addRow("Layer Status Lifetime:", self.diag_status_duration_spin)

        self.diag_rate_limit_spin = QSpinBox()
        self.diag_rate_limit_spin.setRange(10, 1000)
        layout.addRow("Max Events Per Second:", self.diag_rate_limit_spin)

        self.diag_max_entries_spin = QSpinBox()
        self.diag_max_entries_spin.setRange(100, 10000)
        layout.addRow("Log History Size:", self.diag_max_entries_spin)

        self.diag_auto_export_check = QCheckBox("Automatically export diagnostics log")
        self.diag_auto_export_check.toggled.connect(self._update_diag_export_controls)
        layout.addRow(self.diag_auto_export_check)

        self.diag_auto_export_interval_spin = QSpinBox()
        self.diag_auto_export_interval_spin.setRange(5, 3600)
        self.diag_auto_export_interval_spin.setSuffix(" sec")
        layout.addRow("Auto-Export Interval:", self.diag_auto_export_interval_spin)

        path_row = QHBoxLayout()
        self.diag_export_path_edit = QLineEdit()
        path_row.addWidget(self.diag_export_path_edit)
        self.diag_export_browse_btn = QPushButton("Browse…")
        self.diag_export_browse_btn.clicked.connect(self._browse_diag_export_path)
        path_row.addWidget(self.diag_export_browse_btn)
        layout.addRow("Export Location:", path_row)

        group.setLayout(layout)
        return group
    
    def load_current_settings(self):
        """Load current settings into UI"""
        # PNG
        self.png_compression_spin.setValue(self.export_settings.png_compression)
        self.png_full_res_check.setChecked(self.export_settings.png_full_resolution)
        self.png_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'png_full_scale_multiplier', 1.0)))
        )
        self._update_png_full_res_controls()
        
        # GIF
        self.gif_fps_spin.setValue(self.export_settings.gif_fps)
        
        colors_index = ['16', '32', '64', '128', '256'].index(str(self.export_settings.gif_colors))
        self.gif_colors_combo.setCurrentIndex(colors_index)
        
        self.gif_scale_spin.setValue(self.export_settings.gif_scale)
        self.gif_dither_check.setChecked(self.export_settings.gif_dither)
        self.gif_optimize_check.setChecked(self.export_settings.gif_optimize)
        self.gif_loop_spin.setValue(self.export_settings.gif_loop)
        
        mov_codec_index = self.mov_codec_combo.findData(self.export_settings.mov_codec)
        if mov_codec_index < 0:
            mov_codec_index = self.mov_codec_combo.findData('prores_ks')
        if mov_codec_index >= 0:
            self.mov_codec_combo.setCurrentIndex(mov_codec_index)
        self.mov_quality_combo.setCurrentText(self.export_settings.mov_quality.capitalize())
        self.mov_include_audio_check.setChecked(self.export_settings.mov_include_audio)
        self.mov_full_res_check.setChecked(self.export_settings.mov_full_resolution)
        self.mov_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'mov_full_scale_multiplier', 1.0)))
        )
        self._update_mov_full_res_controls()

        # WEBM
        webm_codec_text = getattr(self.export_settings, 'webm_codec', 'libvpx-vp9')
        for idx in range(self.webm_codec_combo.count()):
            if self.webm_codec_combo.itemText(idx).startswith(webm_codec_text):
                self.webm_codec_combo.setCurrentIndex(idx)
                break
        self.webm_crf_spin.setValue(int(getattr(self.export_settings, 'webm_crf', 28)))
        self.webm_speed_spin.setValue(int(getattr(self.export_settings, 'webm_speed', 4)))
        self.webm_include_audio_check.setChecked(getattr(self.export_settings, 'webm_include_audio', True))
        self.webm_full_res_check.setChecked(getattr(self.export_settings, 'webm_full_resolution', False))
        self.webm_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'webm_full_scale_multiplier', 1.0)))
        )
        self._update_webm_full_res_controls()

        # MP4
        mp4_codec = getattr(self.export_settings, 'mp4_codec', 'libx264')
        mp4_codec_index = self.mp4_codec_combo.findData(mp4_codec)
        if mp4_codec_index < 0:
            mp4_codec_index = self.mp4_codec_combo.findData('libx264')
        if mp4_codec_index >= 0:
            self.mp4_codec_combo.setCurrentIndex(mp4_codec_index)
        self.mp4_crf_spin.setValue(int(getattr(self.export_settings, 'mp4_crf', 18)))
        preset_value = getattr(self.export_settings, 'mp4_preset', 'medium').lower()
        preset_index = self.mp4_preset_combo.findText(preset_value, Qt.MatchFlag.MatchFixedString | Qt.MatchFlag.MatchCaseSensitive)
        if preset_index < 0:
            preset_index = self.mp4_preset_combo.findText(preset_value.capitalize())
        if preset_index < 0:
            preset_index = self.mp4_preset_combo.findText("medium", Qt.MatchFlag.MatchFixedString)
        if preset_index >= 0:
            self.mp4_preset_combo.setCurrentIndex(preset_index)
        self.mp4_bitrate_spin.setValue(int(getattr(self.export_settings, 'mp4_bitrate', 0)))
        self.mp4_include_audio_check.setChecked(getattr(self.export_settings, 'mp4_include_audio', True))
        self.mp4_full_res_check.setChecked(getattr(self.export_settings, 'mp4_full_resolution', False))
        self.mp4_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'mp4_full_scale_multiplier', 1.0)))
        )
        pixel_fmt_value = getattr(self.export_settings, 'mp4_pixel_format', 'yuv420p')
        for idx in range(self.mp4_pixel_format_combo.count()):
            if self.mp4_pixel_format_combo.itemText(idx).startswith(pixel_fmt_value):
                self.mp4_pixel_format_combo.setCurrentIndex(idx)
                break
        self.mp4_faststart_check.setChecked(getattr(self.export_settings, 'mp4_faststart', True))
        self._update_mp4_full_res_controls()
        self.update_mp4_estimate()

        universal_scope = getattr(self.export_settings, 'universal_export_bounds_scope', 'current')
        scope_index = self.universal_export_scope_combo.findData(universal_scope)
        if scope_index < 0:
            scope_index = self.universal_export_scope_combo.findData('current')
        if scope_index >= 0:
            self.universal_export_scope_combo.setCurrentIndex(scope_index)
        self.universal_export_explicit_resolution_check.setChecked(
            bool(getattr(self.export_settings, 'universal_export_explicit_resolution', False))
        )
        self.universal_export_width_spin.setValue(
            int(getattr(self.export_settings, 'universal_export_width', 1920))
        )
        self.universal_export_height_spin.setValue(
            int(getattr(self.export_settings, 'universal_export_height', 1080))
        )
        self.universal_export_padding_spin.setValue(
            float(getattr(self.export_settings, 'universal_export_padding', 8.0))
        )
        self._update_universal_export_controls()
        self._on_universal_export_resolution_changed()

        # Camera
        self.camera_zoom_cursor_check.setChecked(self.export_settings.camera_zoom_to_cursor)
        if hasattr(self, "sprite_filter_combo"):
            sprite_filter = self.app_settings.value(
                "viewport/sprite_filter", "bilinear", type=str
            )
            sprite_filter = (sprite_filter or "bilinear").strip().lower()
            idx = self.sprite_filter_combo.findData(sprite_filter)
            if idx < 0:
                idx = self.sprite_filter_combo.findData("bilinear")
            if idx >= 0:
                self.sprite_filter_combo.setCurrentIndex(idx)
        if hasattr(self, "sprite_filter_strength_slider"):
            strength = self.app_settings.value(
                "viewport/sprite_filter_strength", 1.0, type=float
            )
            try:
                strength_value = float(strength)
            except (TypeError, ValueError):
                strength_value = 1.0
            strength_value = max(0.0, min(1.0, strength_value))
            self.sprite_filter_strength_slider.setValue(int(round(strength_value * 100.0)))
        if hasattr(self, "dof_alpha_smoothing_check"):
            self.dof_alpha_smoothing_check.setChecked(
                self.app_settings.value("dof/alpha_edge_smoothing_enabled", False, type=bool)
            )
        if hasattr(self, "dof_alpha_smoothing_mode_combo"):
            mode = self.app_settings.value("dof/alpha_edge_smoothing_mode", "normal", type=str) or "normal"
            mode_idx = self.dof_alpha_smoothing_mode_combo.findData(str(mode).strip().lower())
            if mode_idx < 0:
                mode_idx = self.dof_alpha_smoothing_mode_combo.findData("normal")
            if mode_idx >= 0:
                self.dof_alpha_smoothing_mode_combo.setCurrentIndex(mode_idx)
        if hasattr(self, "dof_alpha_smoothing_strength_slider"):
            dof_alpha_strength = self.app_settings.value(
                "dof/alpha_edge_smoothing_strength", 0.5, type=float
            )
            try:
                dof_alpha_strength_value = float(dof_alpha_strength)
            except (TypeError, ValueError):
                dof_alpha_strength_value = 0.5
            dof_alpha_strength_value = max(0.0, min(1.0, dof_alpha_strength_value))
            self.dof_alpha_smoothing_strength_slider.setValue(
                int(round(dof_alpha_strength_value * 100.0))
            )
        if hasattr(self, "dof_alpha_smoothing_check"):
            enabled = self.dof_alpha_smoothing_check.isChecked()
            self.dof_alpha_smoothing_strength_slider.setEnabled(enabled)
            self.dof_alpha_smoothing_strength_spin.setEnabled(enabled)
            if hasattr(self, "dof_alpha_smoothing_mode_combo"):
                self.dof_alpha_smoothing_mode_combo.setEnabled(enabled)
        if hasattr(self, "viewport_post_aa_check"):
            self.viewport_post_aa_check.setChecked(
                self.app_settings.value("viewport/post_aa_enabled", False, type=bool)
            )
        if hasattr(self, "viewport_post_aa_strength_slider"):
            post_aa_strength = self.app_settings.value(
                "viewport/post_aa_strength", 0.5, type=float
            )
            try:
                post_aa_strength_value = float(post_aa_strength)
            except (TypeError, ValueError):
                post_aa_strength_value = 0.5
            post_aa_strength_value = max(0.0, min(1.0, post_aa_strength_value))
            self.viewport_post_aa_strength_slider.setValue(
                int(round(post_aa_strength_value * 100.0))
            )
        if hasattr(self, "viewport_post_aa_check"):
            enabled = self.viewport_post_aa_check.isChecked()
            self.viewport_post_aa_strength_slider.setEnabled(enabled)
            self.viewport_post_aa_strength_spin.setEnabled(enabled)
        if hasattr(self, "viewport_post_aa_mode_combo"):
            mode = self.app_settings.value("viewport/post_aa_mode", "fxaa", type=str) or "fxaa"
            idx = self.viewport_post_aa_mode_combo.findData(str(mode).strip().lower())
            if idx < 0:
                idx = self.viewport_post_aa_mode_combo.findData("fxaa")
            if idx >= 0:
                self.viewport_post_aa_mode_combo.setCurrentIndex(idx)
        if hasattr(self, "viewport_post_motion_blur_check"):
            self.viewport_post_motion_blur_check.setChecked(
                self.app_settings.value("viewport/post_motion_blur_enabled", False, type=bool)
            )
            self.viewport_post_motion_blur_strength_slider.setValue(
                int(
                    round(
                        max(
                            0.0,
                            min(
                                1.0,
                                float(
                                    self.app_settings.value(
                                        "viewport/post_motion_blur_strength",
                                        0.35,
                                        type=float,
                                    )
                                ),
                            ),
                        )
                        * 100.0
                    )
                )
            )
            mb_enabled = self.viewport_post_motion_blur_check.isChecked()
            self.viewport_post_motion_blur_strength_slider.setEnabled(mb_enabled)
            self.viewport_post_motion_blur_strength_spin.setEnabled(mb_enabled)
        if hasattr(self, "viewport_post_bloom_check"):
            self.viewport_post_bloom_check.setChecked(
                self.app_settings.value("viewport/post_bloom_enabled", False, type=bool)
            )
            self.viewport_post_bloom_strength_slider.setValue(
                int(round(max(0.0, min(2.0, float(self.app_settings.value("viewport/post_bloom_strength", 0.15, type=float)))) * 100.0))
            )
            self.viewport_post_bloom_threshold_slider.setValue(
                int(round(max(0.0, min(2.0, float(self.app_settings.value("viewport/post_bloom_threshold", 0.6, type=float)))) * 100.0))
            )
            self.viewport_post_bloom_radius_slider.setValue(
                int(round(max(0.1, min(8.0, float(self.app_settings.value("viewport/post_bloom_radius", 1.5, type=float)))) * 100.0))
            )
            b_enabled = self.viewport_post_bloom_check.isChecked()
            self.viewport_post_bloom_strength_slider.setEnabled(b_enabled)
            self.viewport_post_bloom_strength_spin.setEnabled(b_enabled)
            self.viewport_post_bloom_threshold_slider.setEnabled(b_enabled)
            self.viewport_post_bloom_threshold_spin.setEnabled(b_enabled)
            self.viewport_post_bloom_radius_slider.setEnabled(b_enabled)
            self.viewport_post_bloom_radius_spin.setEnabled(b_enabled)
        if hasattr(self, "viewport_post_vignette_check"):
            self.viewport_post_vignette_check.setChecked(
                self.app_settings.value("viewport/post_vignette_enabled", False, type=bool)
            )
            self.viewport_post_vignette_strength_slider.setValue(
                int(round(max(0.0, min(1.0, float(self.app_settings.value("viewport/post_vignette_strength", 0.25, type=float)))) * 100.0))
            )
            v_enabled = self.viewport_post_vignette_check.isChecked()
            self.viewport_post_vignette_strength_slider.setEnabled(v_enabled)
            self.viewport_post_vignette_strength_spin.setEnabled(v_enabled)
        if hasattr(self, "viewport_post_grain_check"):
            self.viewport_post_grain_check.setChecked(
                self.app_settings.value("viewport/post_grain_enabled", False, type=bool)
            )
            self.viewport_post_grain_strength_slider.setValue(
                int(round(max(0.0, min(1.0, float(self.app_settings.value("viewport/post_grain_strength", 0.2, type=float)))) * 100.0))
            )
            g_enabled = self.viewport_post_grain_check.isChecked()
            self.viewport_post_grain_strength_slider.setEnabled(g_enabled)
            self.viewport_post_grain_strength_spin.setEnabled(g_enabled)
        if hasattr(self, "viewport_post_ca_check"):
            self.viewport_post_ca_check.setChecked(
                self.app_settings.value("viewport/post_ca_enabled", False, type=bool)
            )
            self.viewport_post_ca_strength_slider.setValue(
                int(round(max(0.0, min(1.0, float(self.app_settings.value("viewport/post_ca_strength", 0.25, type=float)))) * 100.0))
            )
            c_enabled = self.viewport_post_ca_check.isChecked()
            self.viewport_post_ca_strength_slider.setEnabled(c_enabled)
            self.viewport_post_ca_strength_spin.setEnabled(c_enabled)
        if hasattr(self, "dof_sprite_shader_mode_combo"):
            shader_mode = self.app_settings.value("dof/sprite_shader_mode", "auto", type=str) or "auto"
            mode_idx = self.dof_sprite_shader_mode_combo.findData(str(shader_mode).strip().lower())
            if mode_idx < 0:
                mode_idx = self.dof_sprite_shader_mode_combo.findData("auto")
            if mode_idx >= 0:
                self.dof_sprite_shader_mode_combo.setCurrentIndex(mode_idx)
        self.metronome_default_check.setChecked(
            self.app_settings.value('metronome/enabled', False, type=bool)
        )
        self.metronome_audible_check.setChecked(
            self.app_settings.value('metronome/audible', True, type=bool)
        )
        num = self.app_settings.value('metronome/time_signature_numerator', 4, type=int)
        denom = self.app_settings.value('metronome/time_signature_denom', 4, type=int)
        try:
            num = int(num)
        except (TypeError, ValueError):
            num = 4
        if num <= 0:
            num = 4
        self.metronome_time_sig_num.setValue(num)
        idx = self.metronome_time_sig_denom.findData(int(denom))
        if idx < 0:
            idx = self.metronome_time_sig_denom.findData(4)
        if idx >= 0:
            self.metronome_time_sig_denom.setCurrentIndex(idx)
        self.show_beat_grid_check.setChecked(
            self.app_settings.value('timeline/show_beat_grid', False, type=bool)
        )
        self.beat_edit_check.setChecked(
            self.app_settings.value('timeline/allow_beat_edit', False, type=bool)
        )
        if hasattr(self, "dof_particles_world_space_check"):
            self.dof_particles_world_space_check.setChecked(
                self.app_settings.value("dof/particles_world_space", True, type=bool)
            )
        if hasattr(self, "dof_particle_cap_slider"):
            particle_cap = self.app_settings.value("dof/viewport_particle_cap", 1000, type=int)
            try:
                particle_cap_value = int(particle_cap)
            except (TypeError, ValueError):
                particle_cap_value = 1000
            particle_cap_value = max(0, min(1000000, particle_cap_value))
            self.dof_particle_cap_spin.setValue(particle_cap_value)
            self._sync_dof_particle_cap_slider(particle_cap_value)
        if hasattr(self, "dof_particle_sensitivity_slider"):
            particle_sensitivity = self.app_settings.value(
                "dof/particle_distance_sensitivity",
                0.5,
                type=float,
            )
            try:
                particle_sensitivity_value = float(particle_sensitivity)
            except (TypeError, ValueError):
                particle_sensitivity_value = 0.5
            particle_sensitivity_value = max(0.0, min(100.0, particle_sensitivity_value))
            self.dof_particle_sensitivity_spin.setValue(particle_sensitivity_value)
            self._sync_dof_particle_sensitivity_slider(particle_sensitivity_value)
        self.barebones_browser_check.setChecked(self.export_settings.use_barebones_file_browser)
        self.anchor_debug_check.setChecked(self.export_settings.anchor_debug_logging)
        self.update_source_json_check.setChecked(getattr(self.export_settings, 'update_source_json_on_save', False))
        
        # PSD
        self.psd_hidden_check.setChecked(self.export_settings.psd_include_hidden)
        self.psd_full_res_check.setChecked(self.export_settings.psd_preserve_resolution)
        self.psd_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'psd_full_res_multiplier', 1.0)))
        )
        self.psd_scale_spin.setValue(self.export_settings.psd_scale)
        
        quality_index = self.psd_quality_combo.findData(self.export_settings.psd_quality)
        if quality_index == -1:
            quality_index = 1  # Balanced default
        self.psd_quality_combo.setCurrentIndex(quality_index)
        
        compression_index = self.psd_compression_combo.findData(self.export_settings.psd_compression)
        if compression_index == -1:
            compression_index = 1  # RLE default
        self.psd_compression_combo.setCurrentIndex(compression_index)
        
        self.psd_crop_check.setChecked(self.export_settings.psd_crop_canvas)
        self.psd_match_viewport_check.setChecked(self.export_settings.psd_match_viewport)

        # AE Rig
        ae_mode = getattr(self.export_settings, 'ae_rig_mode', 'auto')
        ae_index = self.ae_rig_mode_combo.findData(ae_mode)
        if ae_index == -1:
            ae_index = 0
        self.ae_rig_mode_combo.setCurrentIndex(ae_index)
        self.ae_full_res_check.setChecked(
            bool(getattr(self.export_settings, 'ae_preserve_resolution', False))
        )
        self.ae_full_res_multiplier_spin.setValue(
            max(1.0, float(getattr(self.export_settings, 'ae_full_res_multiplier', 1.0)))
        )
        self.ae_scale_spin.setValue(int(getattr(self.export_settings, 'ae_scale', 100)))
        ae_quality = getattr(self.export_settings, 'ae_quality', 'balanced')
        ae_quality_index = self.ae_quality_combo.findData(ae_quality)
        if ae_quality_index == -1:
            ae_quality_index = 1
        self.ae_quality_combo.setCurrentIndex(ae_quality_index)
        ae_compression = getattr(self.export_settings, 'ae_compression', 'rle')
        ae_compression_index = self.ae_compression_combo.findData(ae_compression)
        if ae_compression_index == -1:
            ae_compression_index = 1
        self.ae_compression_combo.setCurrentIndex(ae_compression_index)
        self.ae_match_viewport_check.setChecked(
            bool(getattr(self.export_settings, 'ae_match_viewport', True))
        )
        self._update_ae_full_res_controls()

        # DOF converter defaults
        if hasattr(self, "dof_assets_root_edit"):
            dof_root = self.app_settings.value("dof_path", "", type=str) or ""
            self.dof_assets_root_edit.setText(dof_root)
            self.dof_mesh_pivot_checkbox.setChecked(
                self.app_settings.value("dof/mesh_pivot_local", False, type=bool)
            )
            if hasattr(self, "dof_include_mesh_xml_checkbox"):
                include_mesh_raw = self.app_settings.value("dof/include_mesh_xml", None)
                if include_mesh_raw is None:
                    strip_raw = self.app_settings.value("dof/strip_mesh_xml", None)
                    if strip_raw is None:
                        include_mesh = False
                    else:
                        include_mesh = not self.app_settings.value(
                            "dof/strip_mesh_xml", False, type=bool
                        )
                else:
                    include_mesh = self.app_settings.value(
                        "dof/include_mesh_xml", False, type=bool
                    )
                self.dof_include_mesh_xml_checkbox.setChecked(bool(include_mesh))
            if hasattr(self, "dof_premultiply_alpha_checkbox"):
                self.dof_premultiply_alpha_checkbox.setChecked(
                    self.app_settings.value("dof/premultiply_alpha", False, type=bool)
                )
            if hasattr(self, "dof_alpha_hardness_slider"):
                alpha_hardness = self.app_settings.value("dof/alpha_hardness", 0.0, type=float)
                try:
                    alpha_hardness_value = float(alpha_hardness)
                except (TypeError, ValueError):
                    alpha_hardness_value = 0.0
                alpha_hardness_value = max(0.0, min(2.0, alpha_hardness_value))
                self.dof_alpha_hardness_slider.setValue(
                    int(round(alpha_hardness_value * 100.0))
                )
            if hasattr(self, "dof_deploy_premultiply_alpha_checkbox"):
                self.dof_deploy_premultiply_alpha_checkbox.setChecked(
                    self.app_settings.value("dof/premultiply_alpha", False, type=bool)
                )
            if hasattr(self, "dof_hires_xml_checkbox"):
                self.dof_hires_xml_checkbox.setChecked(
                    self.app_settings.value("dof/hires_xml", False, type=bool)
                )
            if hasattr(self, "dof_deploy_hires_xml_checkbox"):
                self.dof_deploy_hires_xml_checkbox.setChecked(
                    self.app_settings.value("dof/hires_xml", False, type=bool)
                )
            self.dof_swap_anchor_report_checkbox.setChecked(
                self.app_settings.value("dof/swap_anchor_report", False, type=bool)
            )
            self.dof_swap_anchor_edge_align_checkbox.setChecked(
                self.app_settings.value("dof/swap_anchor_edge_align", False, type=bool)
            )
            self.dof_swap_anchor_pivot_offset_checkbox.setChecked(
                self.app_settings.value("dof/swap_anchor_pivot_offset", False, type=bool)
            )
            self.dof_swap_anchor_report_override_checkbox.setChecked(
                self.app_settings.value("dof/swap_anchor_report_override", False, type=bool)
            )
            if hasattr(self, "dof_bundle_anim_edit"):
                self.dof_bundle_anim_edit.setText(
                    self.app_settings.value("dof/bundle_anim_name", "", type=str) or ""
                )
            self._refresh_dof_input_options()
        if hasattr(self, "bin_copy_xml_check"):
            self.bin_copy_xml_check.setChecked(
                self.app_settings.value("bin_converter/copy_xml_resources", False, type=bool)
            )
        if hasattr(self, "anim_transfer_source_use_dof"):
            self.anim_transfer_source_use_dof.setChecked(
                self.app_settings.value("anim_transfer/source_use_dof", False, type=bool)
            )
            self.anim_transfer_target_use_dof.setChecked(
                self.app_settings.value("anim_transfer/target_use_dof", False, type=bool)
            )
            self._refresh_anim_transfer_source_options()
            self._refresh_anim_transfer_target_options()
        if hasattr(self, "dof_deploy_input_edit"):
            self.dof_deploy_input_edit.setText(
                self.app_settings.value("dof/deploy_input", "", type=str) or ""
            )
            deploy_assets_root = self.app_settings.value(
                "dof/deploy_assets_root", "", type=str
            ) or ""
            if not deploy_assets_root:
                deploy_assets_root = self.app_settings.value("dof_path", "", type=str) or ""
            self.dof_deploy_assets_root_edit.setText(deploy_assets_root)
            deploy_game_root = self.app_settings.value(
                "dof/deploy_game_root", "", type=str
            ) or ""
            if not deploy_game_root:
                if self.game_path:
                    deploy_game_root = str(self.game_path)
                elif self._game_path_str:
                    deploy_game_root = self._game_path_str
            self.dof_deploy_game_root_edit.setText(deploy_game_root)
            self.dof_deploy_existing_edit.setText(
                self.app_settings.value("dof/deploy_existing_target", "", type=str) or ""
            )
            self.dof_deploy_bin_name_edit.setText(
                self.app_settings.value("dof/deploy_bin_name", "", type=str) or ""
            )
            self.dof_deploy_xml_name_edit.setText(
                self.app_settings.value("dof/deploy_xml_name", "", type=str) or ""
            )
            self.dof_deploy_png_name_edit.setText(
                self.app_settings.value("dof/deploy_png_name", "", type=str) or ""
            )
            self.dof_deploy_replace_check.setChecked(
                self.app_settings.value(
                    "dof/deploy_replace_without_backup", False, type=bool
                )
            )

        # Update estimates
        self._load_diagnostics_settings()
        self.update_gif_estimate()
        self.update_mov_estimate()
        self.update_webm_estimate()
        self.update_mp4_estimate()
        self.update_ffmpeg_status()
        self._update_psd_full_res_controls()
        self._update_psd_full_res_controls()
        self._update_psd_full_res_controls()
        self._load_keybind_settings()

    def _load_diagnostics_settings(self):
        s = self.app_settings
        get_bool = lambda key, default: s.value(f"diagnostics/{key}", default, type=bool)
        get_int = lambda key, default: s.value(f"diagnostics/{key}", default, type=int)
        get_float = lambda key, default: s.value(f"diagnostics/{key}", default, type=float)
        get_str = lambda key, default: s.value(f"diagnostics/{key}", default, type=str)

        self.diag_enable_check.setChecked(get_bool("enabled", False))
        self.diag_highlight_check.setChecked(get_bool("highlight_layers", True))
        self.diag_throttle_check.setChecked(get_bool("throttle_updates", True))
        self.diag_clone_check.setChecked(get_bool("log_clone_events", True))
        self.diag_canonical_check.setChecked(get_bool("log_canonical_events", True))
        self.diag_remap_check.setChecked(get_bool("log_remap_events", False))
        self.diag_sheet_check.setChecked(get_bool("log_sheet_events", False))
        self.diag_visibility_check.setChecked(get_bool("log_visibility_events", False))
        self.diag_shader_check.setChecked(get_bool("log_shader_events", False))
        self.diag_color_check.setChecked(get_bool("log_color_events", False))
        self.diag_attachment_check.setChecked(get_bool("log_attachment_events", False))
        self.diag_debug_payload_check.setChecked(get_bool("include_debug_payloads", False))
        severity = get_str("minimum_severity", "INFO")
        idx = self.diag_min_severity_combo.findText(severity)
        if idx == -1:
            idx = 1
        self.diag_min_severity_combo.setCurrentIndex(idx)
        self.diag_update_interval_spin.setValue(get_int("update_interval_ms", 500))
        self.diag_status_duration_spin.setValue(get_float("layer_status_duration_sec", 6.0))
        self.diag_rate_limit_spin.setValue(get_int("rate_limit_per_sec", 120))
        self.diag_max_entries_spin.setValue(get_int("max_entries", 2000))
        self.diag_auto_export_check.setChecked(get_bool("auto_export_enabled", False))
        self.diag_auto_export_interval_spin.setValue(get_int("auto_export_interval_sec", 120))
        self.diag_export_path_edit.setText(get_str("export_path", ""))
        self._update_diag_controls()
        self._update_diag_export_controls()

    def _load_keybind_settings(self):
        if not hasattr(self, "keybind_edits"):
            return
        defaults = default_keybinds()
        for action in keybind_actions():
            edit = self.keybind_edits.get(action.key)
            if not edit:
                continue
            stored = self.app_settings.value(
                f"keybinds/{action.key}", defaults.get(action.key, ""), type=str
            )
            stored = normalize_keybind_sequence(stored)
            edit.setKeySequence(QKeySequence(stored))

    def _reset_keybinds_to_defaults(self):
        if not hasattr(self, "keybind_edits"):
            return
        defaults = default_keybinds()
        for action in keybind_actions():
            edit = self.keybind_edits.get(action.key)
            if not edit:
                continue
            default_value = normalize_keybind_sequence(defaults.get(action.key, ""))
            edit.setKeySequence(QKeySequence(default_value))

    def _set_keybind_default(self, action_key: str):
        if not hasattr(self, "keybind_edits"):
            return
        edit = self.keybind_edits.get(action_key)
        if not edit:
            return
        default_value = normalize_keybind_sequence(default_keybinds().get(action_key, ""))
        edit.setKeySequence(QKeySequence(default_value))

    def _save_keybind_settings(self):
        if not hasattr(self, "keybind_edits"):
            return
        seen: Dict[str, List[str]] = {}
        for action in keybind_actions():
            edit = self.keybind_edits.get(action.key)
            if not edit:
                continue
            seq_text = edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            seq_text = normalize_keybind_sequence(seq_text)
            self.app_settings.setValue(f"keybinds/{action.key}", seq_text)
            if seq_text:
                seen.setdefault(seq_text, []).append(action.label)
        conflicts = {seq: labels for seq, labels in seen.items() if len(labels) > 1}
        if conflicts:
            details = []
            for seq, labels in conflicts.items():
                details.append(f"{seq}: {', '.join(labels)}")
            message = (
                "Some shortcuts are assigned to multiple actions:\n\n"
                + "\n".join(details)
                + "\n\nAll shortcuts were saved, but only one may trigger depending on focus."
            )
            QMessageBox.warning(self, "Keybind Conflicts", message)

    def _update_diag_controls(self):
        enabled = self.diag_enable_check.isChecked()
        for widget in [
            self.diag_highlight_check,
            self.diag_throttle_check,
            self.diag_clone_check,
            self.diag_canonical_check,
            self.diag_remap_check,
            self.diag_sheet_check,
            self.diag_visibility_check,
            self.diag_shader_check,
            self.diag_color_check,
            self.diag_attachment_check,
            self.diag_debug_payload_check,
            self.diag_min_severity_combo,
            self.diag_update_interval_spin,
            self.diag_status_duration_spin,
            self.diag_rate_limit_spin,
            self.diag_max_entries_spin,
            self.diag_auto_export_check,
            self.diag_auto_export_interval_spin,
            self.diag_export_path_edit,
        ]:
            widget.setEnabled(enabled)
        self._update_diag_export_controls()

    def _update_diag_export_controls(self):
        enabled = (
            self.diag_enable_check.isChecked() and
            self.diag_auto_export_check.isChecked()
        )
        self.diag_auto_export_interval_spin.setEnabled(enabled)
        self.diag_export_path_edit.setEnabled(enabled)
        self.diag_export_browse_btn.setEnabled(enabled)

    def _browse_diag_export_path(self):
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Choose Diagnostics Log Destination",
            self.diag_export_path_edit.text() or str(Path.home() / "diagnostics.log"),
            "Log Files (*.log *.txt);;All Files (*)"
        )
        if filename:
            self.diag_export_path_edit.setText(filename)

    def _save_diagnostics_settings(self):
        s = self.app_settings
        s.setValue("diagnostics/enabled", self.diag_enable_check.isChecked())
        s.setValue("diagnostics/highlight_layers", self.diag_highlight_check.isChecked())
        s.setValue("diagnostics/throttle_updates", self.diag_throttle_check.isChecked())
        s.setValue("diagnostics/log_clone_events", self.diag_clone_check.isChecked())
        s.setValue("diagnostics/log_canonical_events", self.diag_canonical_check.isChecked())
        s.setValue("diagnostics/log_remap_events", self.diag_remap_check.isChecked())
        s.setValue("diagnostics/log_sheet_events", self.diag_sheet_check.isChecked())
        s.setValue("diagnostics/log_visibility_events", self.diag_visibility_check.isChecked())
        s.setValue("diagnostics/log_shader_events", self.diag_shader_check.isChecked())
        s.setValue("diagnostics/log_color_events", self.diag_color_check.isChecked())
        s.setValue("diagnostics/log_attachment_events", self.diag_attachment_check.isChecked())
        s.setValue("diagnostics/include_debug_payloads", self.diag_debug_payload_check.isChecked())
        s.setValue("diagnostics/minimum_severity", self.diag_min_severity_combo.currentText())
        s.setValue("diagnostics/update_interval_ms", self.diag_update_interval_spin.value())
        s.setValue("diagnostics/layer_status_duration_sec", self.diag_status_duration_spin.value())
        s.setValue("diagnostics/rate_limit_per_sec", self.diag_rate_limit_spin.value())
        s.setValue("diagnostics/max_entries", self.diag_max_entries_spin.value())
        s.setValue("diagnostics/auto_export_enabled", self.diag_auto_export_check.isChecked())
        s.setValue("diagnostics/auto_export_interval_sec", self.diag_auto_export_interval_spin.value())
        s.setValue("diagnostics/export_path", self.diag_export_path_edit.text().strip())
        self._load_diagnostics_settings()
    
    def update_gif_estimate(self):
        """Update GIF file size estimate"""
        fps = self.gif_fps_spin.value()
        colors = int(self.gif_colors_combo.currentText())
        scale = self.gif_scale_spin.value() / 100.0
        dither = self.gif_dither_check.isChecked()
        optimize = self.gif_optimize_check.isChecked()
        
        # Base estimate: assume 500x500 animation, 3 seconds
        # This is a rough estimate - actual size depends on content
        base_frame_size = 500 * 500 * scale * scale  # pixels
        
        # Color depth factor (fewer colors = smaller)
        color_factor = colors / 256.0
        
        # Frames
        frames = fps * 3  # Assume 3 second animation
        
        # Base size in KB
        base_size = (base_frame_size * color_factor * frames) / 1024 / 8
        
        # Optimization factor
        if optimize:
            base_size *= 0.7
        
        # Dithering adds some overhead
        if dither:
            base_size *= 1.1
        
        # Clamp to reasonable range
        estimated_kb = max(50, min(base_size, 50000))
        
        if estimated_kb > 1024:
            self.gif_estimate_label.setText(f"Estimated file size: ~{estimated_kb/1024:.1f} MB")
        else:
            self.gif_estimate_label.setText(f"Estimated file size: ~{estimated_kb:.0f} KB")
        
        # Quality indicator
        if colors >= 256 and scale >= 100:
            quality_text = "Quality: High"
            quality_style = "color: green;"
        elif colors >= 128 and scale >= 75:
            quality_text = "Quality: Medium"
            quality_style = "color: orange;"
        else:
            quality_text = "Quality: Low"
            quality_style = "color: red;"
        
        # Add FPS warning for high frame rates
        if fps > 50:
            quality_text += " (Note: Some viewers may not display >50 FPS correctly)"
            quality_style = "color: orange;"
        
        self.gif_quality_label.setText(quality_text)
        self.gif_quality_label.setStyleSheet(quality_style)
    
    def update_mov_estimate(self):
        """Update MOV file size estimate"""
        quality = self.mov_quality_combo.currentText().lower()
        codec = self.mov_codec_combo.currentData() or 'prores_ks'
        
        # Base estimate for 500x500, 3 second, 30fps video
        base_mb = 5.0
        
        # Codec factors
        codec_factors = {
            'qtrle': 3.0,      # Large but lossless with alpha
            'png': 2.5,        # Large with alpha
            'prores_ks': 4.0,  # Very large, professional
            'libx264': 0.5,    # Small, no alpha
            'h264_nvenc': 0.55,
            'hevc_nvenc': 0.4,
        }
        
        # Quality factors
        quality_factors = {
            'low': 0.5,
            'medium': 0.75,
            'high': 1.0,
            'lossless': 2.0
        }
        
        estimated_mb = base_mb * codec_factors.get(codec, 1.0) * quality_factors.get(quality, 1.0)
        
        self.mov_estimate_label.setText(f"Estimated file size: ~{estimated_mb:.1f} MB")
        
        # Alpha support indicator
        if codec in ['qtrle', 'png', 'prores_ks']:
            self.mov_alpha_label.setText("Alpha: Supported (transparency preserved)")
            self.mov_alpha_label.setStyleSheet("color: green;")
        else:
            self.mov_alpha_label.setText("Alpha: Not Supported (opaque output)")
            self.mov_alpha_label.setStyleSheet("color: red;")

    def update_webm_estimate(self):
        """Update WEBM file size and alpha estimate."""
        codec_text = self.webm_codec_combo.currentText()
        codec = codec_text.split(' - ')[0] if ' - ' in codec_text else codec_text
        crf = self.webm_crf_spin.value()

        # Rough estimate values
        base_mb = 3.0
        codec_factors = {
            'libvpx-vp9': 0.8,
            'libaom-av1': 0.6,
            'libvpx': 1.0,
        }
        quality_factor = max(0.2, (40 - crf) / 40.0)
        estimated_mb = base_mb * codec_factors.get(codec, 1.0) * quality_factor
        self.webm_estimate_label.setText(f"Estimated file size: ~{estimated_mb:.1f} MB")

        if codec in ('libvpx-vp9', 'libaom-av1'):
            self.webm_alpha_label.setText("Alpha: Supported (transparency preserved)")
            self.webm_alpha_label.setStyleSheet("color: green;")
        else:
            self.webm_alpha_label.setText("Alpha: Not Supported (opaque output)")
            self.webm_alpha_label.setStyleSheet("color: red;")

    def update_mp4_estimate(self):
        """Estimate MP4 file size / quality."""
        codec = self.mp4_codec_combo.currentData() or 'libx264'
        crf = self.mp4_crf_spin.value()
        bitrate = self.mp4_bitrate_spin.value()
        preset = self.mp4_preset_combo.currentText().lower()

        base_mb = 4.0
        codec_factors = {
            'libx264': 1.0,
            'libx265': 0.7,
            'h264_nvenc': 0.95,
            'hevc_nvenc': 0.65,
        }
        preset_factors = {
            'ultrafast': 1.4,
            'superfast': 1.3,
            'veryfast': 1.2,
            'faster': 1.1,
            'fast': 1.0,
            'medium': 1.0,
            'slow': 0.9,
            'slower': 0.85,
            'veryslow': 0.8,
        }
        quality_factor = max(0.3, (40 - crf) / 40.0)
        estimated_mb = base_mb * codec_factors.get(codec, 1.0) * preset_factors.get(preset, 1.0) * quality_factor
        if bitrate > 0:
            estimated_mb = max(estimated_mb, bitrate / 8000.0 * 3.0)

        self.mp4_estimate_label.setText(f"Estimated file size: ~{estimated_mb:.1f} MB")
        self.mp4_alpha_label.setText("Alpha: Not Supported (opaque output)")
        self.mp4_alpha_label.setStyleSheet("color: red;")
    
    def reset_to_defaults(self):
        """Reset all settings to defaults"""
        # PNG
        self.png_compression_spin.setValue(6)
        self.png_full_res_check.setChecked(False)
        self.png_full_res_multiplier_spin.setValue(1.0)
        self._update_png_full_res_controls()
        
        # GIF
        self.gif_fps_spin.setValue(15)
        self.gif_colors_combo.setCurrentText('256')
        self.gif_scale_spin.setValue(100)
        self.gif_dither_check.setChecked(True)
        self.gif_optimize_check.setChecked(True)
        self.gif_loop_spin.setValue(0)
        
        # MOV
        self.mov_codec_combo.setCurrentIndex(0)
        self.mov_quality_combo.setCurrentText('High')
        self.mov_include_audio_check.setChecked(True)
        self.mov_full_res_check.setChecked(False)
        self.mov_full_res_multiplier_spin.setValue(1.0)
        self._update_mov_full_res_controls()

        # WEBM
        self.webm_codec_combo.setCurrentIndex(0)
        self.webm_crf_spin.setValue(28)
        self.webm_speed_spin.setValue(4)
        self.webm_include_audio_check.setChecked(True)
        self.webm_full_res_check.setChecked(False)
        self.webm_full_res_multiplier_spin.setValue(1.0)
        self._update_webm_full_res_controls()

        # MP4
        self.mp4_codec_combo.setCurrentIndex(0)
        self.mp4_crf_spin.setValue(18)
        self.mp4_preset_combo.setCurrentText("medium")
        self.mp4_bitrate_spin.setValue(0)
        self.mp4_include_audio_check.setChecked(True)
        self.mp4_full_res_check.setChecked(False)
        self.mp4_full_res_multiplier_spin.setValue(1.0)
        self.mp4_pixel_format_combo.setCurrentIndex(0)
        self.mp4_faststart_check.setChecked(True)
        self._update_mp4_full_res_controls()

        scope_index = self.universal_export_scope_combo.findData("current")
        if scope_index >= 0:
            self.universal_export_scope_combo.setCurrentIndex(scope_index)
        self.universal_export_explicit_resolution_check.setChecked(False)
        self.universal_export_width_spin.setValue(1920)
        self.universal_export_height_spin.setValue(1080)
        self.universal_export_padding_spin.setValue(8.0)
        self._update_universal_export_controls()
        self._on_universal_export_resolution_changed()

        self.camera_zoom_cursor_check.setChecked(True)
        if hasattr(self, "sprite_filter_combo"):
            idx = self.sprite_filter_combo.findData("bilinear")
            if idx >= 0:
                self.sprite_filter_combo.setCurrentIndex(idx)
        if hasattr(self, "sprite_filter_strength_slider"):
            self.sprite_filter_strength_slider.setValue(100)
        if hasattr(self, "viewport_post_aa_check"):
            self.viewport_post_aa_check.setChecked(False)
        if hasattr(self, "viewport_post_aa_strength_slider"):
            self.viewport_post_aa_strength_slider.setValue(50)
        if hasattr(self, "viewport_post_aa_mode_combo"):
            idx = self.viewport_post_aa_mode_combo.findData("fxaa")
            if idx >= 0:
                self.viewport_post_aa_mode_combo.setCurrentIndex(idx)
        if hasattr(self, "viewport_post_motion_blur_check"):
            self.viewport_post_motion_blur_check.setChecked(False)
            self.viewport_post_motion_blur_strength_slider.setValue(35)
        if hasattr(self, "viewport_post_bloom_check"):
            self.viewport_post_bloom_check.setChecked(False)
            self.viewport_post_bloom_strength_slider.setValue(15)
            self.viewport_post_bloom_threshold_slider.setValue(60)
            self.viewport_post_bloom_radius_slider.setValue(150)
        if hasattr(self, "viewport_post_vignette_check"):
            self.viewport_post_vignette_check.setChecked(False)
            self.viewport_post_vignette_strength_slider.setValue(25)
        if hasattr(self, "viewport_post_grain_check"):
            self.viewport_post_grain_check.setChecked(False)
            self.viewport_post_grain_strength_slider.setValue(20)
        if hasattr(self, "viewport_post_ca_check"):
            self.viewport_post_ca_check.setChecked(False)
            self.viewport_post_ca_strength_slider.setValue(25)
        self.metronome_default_check.setChecked(False)
        self.metronome_audible_check.setChecked(True)
        self.metronome_time_sig_num.setValue(4)
        idx = self.metronome_time_sig_denom.findData(4)
        if idx >= 0:
            self.metronome_time_sig_denom.setCurrentIndex(idx)
        self.show_beat_grid_check.setChecked(False)
        self.beat_edit_check.setChecked(False)
        if hasattr(self, "dof_particles_world_space_check"):
            self.dof_particles_world_space_check.setChecked(True)
        if hasattr(self, "dof_particle_cap_slider"):
            self.dof_particle_cap_spin.setValue(1000)
            self._sync_dof_particle_cap_slider(1000)
        if hasattr(self, "dof_particle_sensitivity_slider"):
            self.dof_particle_sensitivity_spin.setValue(0.5)
            self._sync_dof_particle_sensitivity_slider(0.5)
        if hasattr(self, "dof_sprite_shader_mode_combo"):
            idx = self.dof_sprite_shader_mode_combo.findData("auto")
            if idx >= 0:
                self.dof_sprite_shader_mode_combo.setCurrentIndex(idx)
        self.barebones_browser_check.setChecked(False)
        self.update_source_json_check.setChecked(False)
        
        # PSD
        self.psd_hidden_check.setChecked(False)
        self.psd_full_res_check.setChecked(False)
        self.psd_full_res_multiplier_spin.setValue(1.0)
        self.psd_scale_spin.setValue(100)
        self.psd_quality_combo.setCurrentIndex(1)
        self.psd_compression_combo.setCurrentIndex(1)
        self.psd_crop_check.setChecked(False)
        self.psd_match_viewport_check.setChecked(False)

        # AE Rig
        self.ae_rig_mode_combo.setCurrentIndex(0)
        self.ae_full_res_check.setChecked(False)
        self.ae_full_res_multiplier_spin.setValue(1.0)
        self.ae_quality_combo.setCurrentIndex(1)
        self.ae_scale_spin.setValue(100)
        self.ae_compression_combo.setCurrentIndex(1)
        self.ae_match_viewport_check.setChecked(True)
        self._update_ae_full_res_controls()

        if hasattr(self, "dof_assets_root_edit"):
            dof_root = self.app_settings.value("dof_path", "", type=str) or ""
            self.dof_assets_root_edit.setText(dof_root)
            self.dof_mesh_pivot_checkbox.setChecked(False)
            if hasattr(self, "dof_include_mesh_xml_checkbox"):
                self.dof_include_mesh_xml_checkbox.setChecked(False)
            if hasattr(self, "dof_premultiply_alpha_checkbox"):
                self.dof_premultiply_alpha_checkbox.setChecked(False)
            if hasattr(self, "dof_alpha_hardness_slider"):
                self.dof_alpha_hardness_slider.setValue(0)
            if hasattr(self, "dof_deploy_premultiply_alpha_checkbox"):
                self.dof_deploy_premultiply_alpha_checkbox.setChecked(False)
            if hasattr(self, "dof_hires_xml_checkbox"):
                self.dof_hires_xml_checkbox.setChecked(False)
            if hasattr(self, "dof_deploy_hires_xml_checkbox"):
                self.dof_deploy_hires_xml_checkbox.setChecked(False)
            self.dof_swap_anchor_report_checkbox.setChecked(False)
            self.dof_swap_anchor_edge_align_checkbox.setChecked(False)
            self.dof_swap_anchor_pivot_offset_checkbox.setChecked(False)
            self.dof_swap_anchor_report_override_checkbox.setChecked(False)
            if hasattr(self, "dof_bundle_anim_edit"):
                self.dof_bundle_anim_edit.setText("")
        if hasattr(self, "bin_copy_xml_check"):
            self.bin_copy_xml_check.setChecked(False)
        if hasattr(self, "anim_transfer_source_use_dof"):
            self.anim_transfer_source_use_dof.setChecked(False)
            self.anim_transfer_target_use_dof.setChecked(False)
            self._refresh_anim_transfer_source_options()
            self._refresh_anim_transfer_target_options()
        if hasattr(self, "dof_deploy_input_edit"):
            self.dof_deploy_input_edit.setText("")
            deploy_root = self.app_settings.value("dof_path", "", type=str) or ""
            self.dof_deploy_assets_root_edit.setText(deploy_root)
            if self.game_path:
                self.dof_deploy_game_root_edit.setText(str(self.game_path))
            elif self._game_path_str:
                self.dof_deploy_game_root_edit.setText(self._game_path_str)
            else:
                self.dof_deploy_game_root_edit.setText("")
            self.dof_deploy_existing_edit.setText("")
            self.dof_deploy_bin_name_edit.setText("")
            self.dof_deploy_xml_name_edit.setText("")
            self.dof_deploy_png_name_edit.setText("")
            self.dof_deploy_replace_check.setChecked(False)
            if hasattr(self, "dof_deploy_log"):
                self.dof_deploy_log.clear()

        self.diag_enable_check.setChecked(False)
        self.diag_highlight_check.setChecked(True)
        self.diag_throttle_check.setChecked(True)
        self.diag_clone_check.setChecked(True)
        self.diag_canonical_check.setChecked(True)
        self.diag_remap_check.setChecked(False)
        self.diag_sheet_check.setChecked(False)
        self.diag_visibility_check.setChecked(False)
        self.diag_shader_check.setChecked(False)
        self.diag_color_check.setChecked(False)
        self.diag_attachment_check.setChecked(False)
        self.diag_debug_payload_check.setChecked(False)
        self.diag_min_severity_combo.setCurrentText("INFO")
        self.diag_update_interval_spin.setValue(500)
        self.diag_status_duration_spin.setValue(6.0)
        self.diag_rate_limit_spin.setValue(120)
        self.diag_max_entries_spin.setValue(2000)
        self.diag_auto_export_check.setChecked(False)
        self.diag_auto_export_interval_spin.setValue(120)
        self.diag_export_path_edit.setText("")
        self._update_diag_controls()
        self.anchor_debug_check.setChecked(False)

        self._reset_keybinds_to_defaults()

        self.update_gif_estimate()
        self.update_mov_estimate()
        self.update_webm_estimate()
        self.update_ffmpeg_status()
        self.shader_tab.reset_overrides()
        self._reset_viewport_bg_to_default_on_save = True
    
    def save_settings(self):
        """Save settings and close dialog"""
        if self.ffmpeg_install_running:
            QMessageBox.warning(self, "FFmpeg Installation",
                                "Please wait for the FFmpeg install to finish.")
            return
        # PNG
        self.export_settings.png_compression = self.png_compression_spin.value()
        self.export_settings.png_full_resolution = self.png_full_res_check.isChecked()
        self.export_settings.png_full_scale_multiplier = self.png_full_res_multiplier_spin.value()
        
        # GIF
        self.export_settings.gif_fps = self.gif_fps_spin.value()
        self.export_settings.gif_colors = int(self.gif_colors_combo.currentText())
        self.export_settings.gif_scale = self.gif_scale_spin.value()
        self.export_settings.gif_dither = self.gif_dither_check.isChecked()
        self.export_settings.gif_optimize = self.gif_optimize_check.isChecked()
        self.export_settings.gif_loop = self.gif_loop_spin.value()
        
        # MOV
        self.export_settings.mov_codec = self.mov_codec_combo.currentData() or 'prores_ks'
        self.export_settings.mov_quality = self.mov_quality_combo.currentText().lower()
        self.export_settings.mov_include_audio = self.mov_include_audio_check.isChecked()
        self.export_settings.mov_full_resolution = self.mov_full_res_check.isChecked()
        self.export_settings.mov_full_scale_multiplier = self.mov_full_res_multiplier_spin.value()

        # WEBM
        webm_codec_text = self.webm_codec_combo.currentText()
        self.export_settings.webm_codec = (
            webm_codec_text.split(' - ')[0] if ' - ' in webm_codec_text else webm_codec_text
        )
        self.export_settings.webm_crf = self.webm_crf_spin.value()
        self.export_settings.webm_speed = self.webm_speed_spin.value()
        self.export_settings.webm_include_audio = self.webm_include_audio_check.isChecked()
        self.export_settings.webm_full_resolution = self.webm_full_res_check.isChecked()
        self.export_settings.webm_full_scale_multiplier = self.webm_full_res_multiplier_spin.value()

        # MP4
        self.export_settings.mp4_codec = self.mp4_codec_combo.currentData() or 'libx264'
        self.export_settings.mp4_crf = self.mp4_crf_spin.value()
        self.export_settings.mp4_preset = self.mp4_preset_combo.currentText().lower()
        self.export_settings.mp4_bitrate = self.mp4_bitrate_spin.value()
        self.export_settings.mp4_include_audio = self.mp4_include_audio_check.isChecked()
        self.export_settings.mp4_full_resolution = self.mp4_full_res_check.isChecked()
        self.export_settings.mp4_full_scale_multiplier = self.mp4_full_res_multiplier_spin.value()
        pixel_fmt_text = self.mp4_pixel_format_combo.currentText()
        self.export_settings.mp4_pixel_format = (
            pixel_fmt_text.split(' - ')[0] if ' - ' in pixel_fmt_text else pixel_fmt_text
        )
        self.export_settings.mp4_faststart = self.mp4_faststart_check.isChecked()

        self.export_settings.universal_export_bounds_scope = (
            self.universal_export_scope_combo.currentData() or 'current'
        )
        self.export_settings.universal_export_explicit_resolution = (
            self.universal_export_explicit_resolution_check.isChecked()
        )
        self.export_settings.universal_export_width = self.universal_export_width_spin.value()
        self.export_settings.universal_export_height = self.universal_export_height_spin.value()
        self.export_settings.universal_export_padding = self.universal_export_padding_spin.value()

        self.export_settings.camera_zoom_to_cursor = self.camera_zoom_cursor_check.isChecked()
        if hasattr(self, "sprite_filter_combo"):
            self.app_settings.setValue(
                "viewport/sprite_filter",
                self.sprite_filter_combo.currentData() or "bilinear",
            )
        if hasattr(self, "sprite_filter_strength_slider"):
            strength_value = float(self.sprite_filter_strength_slider.value()) / 100.0
            self.app_settings.setValue("viewport/sprite_filter_strength", strength_value)
        if hasattr(self, "dof_alpha_smoothing_check"):
            self.app_settings.setValue(
                "dof/alpha_edge_smoothing_enabled",
                self.dof_alpha_smoothing_check.isChecked(),
            )
        if hasattr(self, "dof_alpha_smoothing_mode_combo"):
            self.app_settings.setValue(
                "dof/alpha_edge_smoothing_mode",
                self.dof_alpha_smoothing_mode_combo.currentData() or "normal",
            )
        if hasattr(self, "dof_alpha_smoothing_strength_slider"):
            dof_alpha_strength = float(self.dof_alpha_smoothing_strength_slider.value()) / 100.0
            self.app_settings.setValue("dof/alpha_edge_smoothing_strength", dof_alpha_strength)
        if hasattr(self, "viewport_post_aa_check"):
            self.app_settings.setValue(
                "viewport/post_aa_enabled",
                self.viewport_post_aa_check.isChecked(),
            )
        if hasattr(self, "viewport_post_aa_strength_slider"):
            post_aa_strength = float(self.viewport_post_aa_strength_slider.value()) / 100.0
            self.app_settings.setValue("viewport/post_aa_strength", post_aa_strength)
        if hasattr(self, "viewport_post_aa_mode_combo"):
            self.app_settings.setValue(
                "viewport/post_aa_mode",
                self.viewport_post_aa_mode_combo.currentData() or "fxaa",
            )
        if hasattr(self, "viewport_post_motion_blur_check"):
            self.app_settings.setValue(
                "viewport/post_motion_blur_enabled",
                self.viewport_post_motion_blur_check.isChecked(),
            )
            self.app_settings.setValue(
                "viewport/post_motion_blur_strength",
                float(self.viewport_post_motion_blur_strength_slider.value()) / 100.0,
            )
        if hasattr(self, "viewport_post_bloom_check"):
            self.app_settings.setValue(
                "viewport/post_bloom_enabled",
                self.viewport_post_bloom_check.isChecked(),
            )
            self.app_settings.setValue(
                "viewport/post_bloom_strength",
                float(self.viewport_post_bloom_strength_slider.value()) / 100.0,
            )
            self.app_settings.setValue(
                "viewport/post_bloom_threshold",
                float(self.viewport_post_bloom_threshold_slider.value()) / 100.0,
            )
            self.app_settings.setValue(
                "viewport/post_bloom_radius",
                float(self.viewport_post_bloom_radius_slider.value()) / 100.0,
            )
        if hasattr(self, "viewport_post_vignette_check"):
            self.app_settings.setValue(
                "viewport/post_vignette_enabled",
                self.viewport_post_vignette_check.isChecked(),
            )
            self.app_settings.setValue(
                "viewport/post_vignette_strength",
                float(self.viewport_post_vignette_strength_slider.value()) / 100.0,
            )
        if hasattr(self, "viewport_post_grain_check"):
            self.app_settings.setValue(
                "viewport/post_grain_enabled",
                self.viewport_post_grain_check.isChecked(),
            )
            self.app_settings.setValue(
                "viewport/post_grain_strength",
                float(self.viewport_post_grain_strength_slider.value()) / 100.0,
            )
        if hasattr(self, "viewport_post_ca_check"):
            self.app_settings.setValue(
                "viewport/post_ca_enabled",
                self.viewport_post_ca_check.isChecked(),
            )
            self.app_settings.setValue(
                "viewport/post_ca_strength",
                float(self.viewport_post_ca_strength_slider.value()) / 100.0,
            )
        if hasattr(self, "dof_sprite_shader_mode_combo"):
            self.app_settings.setValue(
                "dof/sprite_shader_mode",
                self.dof_sprite_shader_mode_combo.currentData() or "auto",
            )
        self.app_settings.setValue('metronome/enabled', self.metronome_default_check.isChecked())
        self.app_settings.setValue('metronome/audible', self.metronome_audible_check.isChecked())
        self.app_settings.setValue('metronome/time_signature_numerator', self.metronome_time_sig_num.value())
        denom = self.metronome_time_sig_denom.currentData(Qt.ItemDataRole.UserRole)
        self.app_settings.setValue('metronome/time_signature_denom', int(denom) if denom is not None else 4)
        self.app_settings.setValue('timeline/show_beat_grid', self.show_beat_grid_check.isChecked())
        self.app_settings.setValue('timeline/allow_beat_edit', self.beat_edit_check.isChecked())
        if hasattr(self, "dof_particles_world_space_check"):
            self.app_settings.setValue(
                "dof/particles_world_space",
                self.dof_particles_world_space_check.isChecked(),
            )
        if hasattr(self, "dof_particle_cap_spin"):
            self.app_settings.setValue(
                "dof/viewport_particle_cap",
                int(self.dof_particle_cap_spin.value()),
            )
        if hasattr(self, "dof_particle_sensitivity_spin"):
            self.app_settings.setValue(
                "dof/particle_distance_sensitivity",
                float(self.dof_particle_sensitivity_spin.value()),
            )
        self.export_settings.use_barebones_file_browser = self.barebones_browser_check.isChecked()
        self.export_settings.anchor_debug_logging = self.anchor_debug_check.isChecked()
        self.export_settings.update_source_json_on_save = self.update_source_json_check.isChecked()

        # PSD
        self.export_settings.psd_include_hidden = self.psd_hidden_check.isChecked()
        self.export_settings.psd_scale = self.psd_scale_spin.value()
        self.export_settings.psd_quality = self.psd_quality_combo.currentData()
        self.export_settings.psd_compression = self.psd_compression_combo.currentData()
        self.export_settings.psd_crop_canvas = self.psd_crop_check.isChecked()
        self.export_settings.psd_match_viewport = self.psd_match_viewport_check.isChecked()
        self.export_settings.psd_preserve_resolution = self.psd_full_res_check.isChecked()
        self.export_settings.psd_full_res_multiplier = self.psd_full_res_multiplier_spin.value()

        # AE Rig
        self.export_settings.ae_rig_mode = self.ae_rig_mode_combo.currentData()
        self.export_settings.ae_preserve_resolution = self.ae_full_res_check.isChecked()
        self.export_settings.ae_full_res_multiplier = self.ae_full_res_multiplier_spin.value()
        self.export_settings.ae_quality = self.ae_quality_combo.currentData()
        self.export_settings.ae_scale = self.ae_scale_spin.value()
        self.export_settings.ae_compression = self.ae_compression_combo.currentData()
        self.export_settings.ae_match_viewport = self.ae_match_viewport_check.isChecked()

        # DOF converter settings
        if hasattr(self, "dof_assets_root_edit"):
            self.app_settings.setValue("dof_path", self.dof_assets_root_edit.text().strip())
            self.app_settings.setValue("dof/mesh_pivot_local", self.dof_mesh_pivot_checkbox.isChecked())
            if hasattr(self, "dof_include_mesh_xml_checkbox"):
                self.app_settings.setValue(
                    "dof/include_mesh_xml",
                    self.dof_include_mesh_xml_checkbox.isChecked(),
                )
            if hasattr(self, "dof_premultiply_alpha_checkbox"):
                self.app_settings.setValue(
                    "dof/premultiply_alpha",
                    self.dof_premultiply_alpha_checkbox.isChecked(),
                )
            if hasattr(self, "dof_alpha_hardness_slider"):
                self.app_settings.setValue(
                    "dof/alpha_hardness",
                    float(self.dof_alpha_hardness_slider.value()) / 100.0,
                )
            if hasattr(self, "dof_hires_xml_checkbox"):
                self.app_settings.setValue(
                    "dof/hires_xml",
                    self.dof_hires_xml_checkbox.isChecked(),
                )
            self.app_settings.setValue("dof/swap_anchor_report", self.dof_swap_anchor_report_checkbox.isChecked())
            self.app_settings.setValue("dof/swap_anchor_edge_align", self.dof_swap_anchor_edge_align_checkbox.isChecked())
            self.app_settings.setValue("dof/swap_anchor_pivot_offset", self.dof_swap_anchor_pivot_offset_checkbox.isChecked())
            self.app_settings.setValue(
                "dof/swap_anchor_report_override",
                self.dof_swap_anchor_report_override_checkbox.isChecked(),
            )
            if hasattr(self, "dof_bundle_anim_edit"):
                self.app_settings.setValue("dof/bundle_anim_name", self.dof_bundle_anim_edit.text().strip())
        if hasattr(self, "bin_copy_xml_check"):
            self.app_settings.setValue(
                "bin_converter/copy_xml_resources",
                self.bin_copy_xml_check.isChecked(),
            )
        if hasattr(self, "anim_transfer_source_use_dof"):
            self.app_settings.setValue(
                "anim_transfer/source_use_dof",
                self.anim_transfer_source_use_dof.isChecked(),
            )
            self.app_settings.setValue(
                "anim_transfer/target_use_dof",
                self.anim_transfer_target_use_dof.isChecked(),
            )
        if hasattr(self, "dof_deploy_input_edit"):
            self.app_settings.setValue(
                "dof/deploy_input",
                self.dof_deploy_input_edit.text().strip(),
            )
            deploy_assets_root = self.dof_deploy_assets_root_edit.text().strip()
            self.app_settings.setValue("dof/deploy_assets_root", deploy_assets_root)
            if deploy_assets_root:
                self.app_settings.setValue("dof_path", deploy_assets_root)
            self.app_settings.setValue(
                "dof/deploy_game_root",
                self.dof_deploy_game_root_edit.text().strip(),
            )
            self.app_settings.setValue(
                "dof/deploy_existing_target",
                self.dof_deploy_existing_edit.text().strip(),
            )
            self.app_settings.setValue(
                "dof/deploy_bin_name",
                self.dof_deploy_bin_name_edit.text().strip(),
            )
            self.app_settings.setValue(
                "dof/deploy_xml_name",
                self.dof_deploy_xml_name_edit.text().strip(),
            )
            self.app_settings.setValue(
                "dof/deploy_png_name",
                self.dof_deploy_png_name_edit.text().strip(),
            )
        self.app_settings.setValue(
                "dof/deploy_replace_without_backup",
                self.dof_deploy_replace_check.isChecked(),
            )

        if self._reset_viewport_bg_to_default_on_save:
            default_viewport_bg_asset = os.path.abspath(os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "assets",
                "Viewport_Background_Default.svg",
            ))
            if os.path.isfile(default_viewport_bg_asset):
                self.app_settings.setValue("viewport/background_image_path", default_viewport_bg_asset)
                self.app_settings.setValue("viewport/background_image_enabled", True)
            else:
                self.app_settings.remove("viewport/background_image_path")
                self.app_settings.remove("viewport/background_image_enabled")
            self.app_settings.setValue("viewport/background_enabled", True)
            self.app_settings.setValue("viewport/background_keep_aspect", True)
            self.app_settings.setValue("viewport/background_zoom_fill", False)
            self.app_settings.setValue("viewport/background_parallax_enabled", True)
            self.app_settings.setValue("viewport/background_parallax_zoom_strength", 0.5)
            self.app_settings.setValue("viewport/background_parallax_pan_strength", 0.5)
            self.app_settings.setValue("viewport/background_flip_h", False)
            self.app_settings.setValue("viewport/background_flip_v", False)
            self.app_settings.setValue("viewport/background_color_mode", "none")
            self.app_settings.setValue("export/include_viewport_background", False)
            self._reset_viewport_bg_to_default_on_save = False

        # Shader overrides
        shader_overrides = self.shader_tab.get_overrides()
        try:
            overrides_blob = json.dumps(shader_overrides)
        except Exception:
            overrides_blob = "{}"
        self.app_settings.setValue("shaders/overrides", overrides_blob)
        self.shader_registry.set_user_overrides(shader_overrides)

        self._save_keybind_settings()
        self.export_settings.save()
        self._save_diagnostics_settings()
        self.accept()

    def update_ffmpeg_status(self):
        """Report FFmpeg availability and button text."""
        stored_path = self.app_settings.value('ffmpeg/path', '', type=str)
        ffmpeg_path = resolve_ffmpeg_path(stored_path)

        if stored_path and not ffmpeg_path:
            self.app_settings.remove('ffmpeg/path')

        if ffmpeg_path:
            path_text = Path(ffmpeg_path)
            encoder_set = query_ffmpeg_encoders(ffmpeg_path)
            nvenc_support = []
            if 'h264_nvenc' in encoder_set:
                nvenc_support.append('H.264 NVENC')
            if 'hevc_nvenc' in encoder_set:
                nvenc_support.append('HEVC NVENC')
            if nvenc_support:
                nvenc_text = ", ".join(nvenc_support)
                self.ffmpeg_status_label.setText(
                    f"FFmpeg ready at: {path_text}\nGPU encoders available: {nvenc_text}"
                )
            else:
                self.ffmpeg_status_label.setText(
                    f"FFmpeg ready at: {path_text}\nGPU encoders available: none detected"
                )
            self.ffmpeg_install_button.setText("Reinstall FFmpeg")
        else:
            self.ffmpeg_status_label.setText(
                "FFmpeg not detected. MOV/Video exports will remain disabled until it is installed."
            )
            self.ffmpeg_install_button.setText("Install FFmpeg")

        if not self.ffmpeg_install_running:
            self.ffmpeg_install_button.setEnabled(True)

    def start_ffmpeg_install(self):
        """Begin installing FFmpeg in the background."""
        if self.ffmpeg_install_running:
            return

        self.ffmpeg_install_running = True
        self.ffmpeg_progress.setValue(0)
        self.ffmpeg_progress.setVisible(True)
        self.ffmpeg_status_label.setText("Starting FFmpeg download...")
        self.ffmpeg_install_button.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.save_btn.setEnabled(False)

        self.ffmpeg_worker = FFmpegInstallWorker()
        self.ffmpeg_thread = QThread(self)
        self.ffmpeg_worker.moveToThread(self.ffmpeg_thread)

        self.ffmpeg_thread.started.connect(self.ffmpeg_worker.run)
        self.ffmpeg_worker.statusChanged.connect(self.ffmpeg_status_label.setText)
        self.ffmpeg_worker.progressChanged.connect(self.ffmpeg_progress.setValue)
        self.ffmpeg_worker.finished.connect(self.on_ffmpeg_install_finished)
        self.ffmpeg_worker.finished.connect(self.ffmpeg_thread.quit)
        self.ffmpeg_thread.finished.connect(self._cleanup_ffmpeg_thread)
        self.ffmpeg_thread.start()

    def on_ffmpeg_install_finished(self, success: bool, payload: str):
        """Handle completion of the FFmpeg installer."""
        self.ffmpeg_install_running = False
        self.ffmpeg_progress.setVisible(False)
        self.reset_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.save_btn.setEnabled(True)

        if success:
            self.app_settings.setValue('ffmpeg/path', payload)
            self.ffmpeg_status_label.setText(f"FFmpeg installed at: {payload}")
        else:
            self.ffmpeg_status_label.setText(f"FFmpeg install failed: {payload}")

        self.update_ffmpeg_status()

    def _cleanup_ffmpeg_thread(self):
        """Release worker/thread once the install process exits."""
        if self.ffmpeg_worker:
            self.ffmpeg_worker.deleteLater()
            self.ffmpeg_worker = None
        if self.ffmpeg_thread:
            self.ffmpeg_thread.deleteLater()
            self.ffmpeg_thread = None

    def reject(self):
        if self.ffmpeg_install_running:
            QMessageBox.warning(self, "FFmpeg Installation",
                                "Please wait for the FFmpeg install to finish.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.ffmpeg_install_running:
            QMessageBox.warning(self, "FFmpeg Installation",
                                "Please wait for the FFmpeg install to finish.")
            event.ignore()
            return
        super().closeEvent(event)

    def _update_psd_full_res_controls(self):
        """Enable/disable PSD multiplier control based on checkbox."""
        enabled = self.psd_full_res_check.isChecked()
        self.psd_full_res_multiplier_spin.setEnabled(enabled)

    def _update_png_full_res_controls(self):
        """Enable/disable PNG multiplier control based on checkbox."""
        enabled = self.png_full_res_check.isChecked()
        self.png_full_res_multiplier_spin.setEnabled(enabled)

    def _update_mov_full_res_controls(self):
        """Enable/disable MOV multiplier control based on checkbox."""
        enabled = self.mov_full_res_check.isChecked()
        self.mov_full_res_multiplier_spin.setEnabled(enabled)

    def _update_webm_full_res_controls(self):
        """Enable/disable WEBM multiplier control based on checkbox."""
        enabled = self.webm_full_res_check.isChecked()
        self.webm_full_res_multiplier_spin.setEnabled(enabled)

    def _update_mp4_full_res_controls(self):
        """Enable/disable MP4 multiplier control based on checkbox."""
        enabled = self.mp4_full_res_check.isChecked()
        self.mp4_full_res_multiplier_spin.setEnabled(enabled)

    def _update_ae_full_res_controls(self):
        """Enable/disable AE full-resolution multiplier control."""
        enabled = self.ae_full_res_check.isChecked()
        self.ae_full_res_multiplier_spin.setEnabled(enabled)

    # ------------------------------------------------------------------
    # BIN revision converter helpers
    def _discover_bin_converters(self) -> Dict[str, Optional[str]]:
        root = self.shader_registry.project_root
        bin_dir = root / "Resources" / "bin2json"
        converters = {
            "rev6": bin_dir / "rev6-2-json.py",
            "rev4": bin_dir / "rev4-2-json.py",
            "rev2": bin_dir / "rev2-2-json.py",
            "legacy": bin_dir / "legacy_bin_to_json.py",
            "choir": bin_dir / "choir_bin_to_json.py",
            "muppets": bin_dir / "muppets_bin_to_json.py",
            "oldest": bin_dir / "oldest_bin_to_json.py",
        }
        resolved: Dict[str, Optional[str]] = {}
        for key, path in converters.items():
            resolved[key] = str(path) if path.exists() else None
        return resolved

    def _discover_dof_converter(self) -> Optional[str]:
        root = self.shader_registry.project_root
        script = root / "Resources" / "bin2json" / "dof_anim_to_json.py"
        return str(script) if script.exists() else None

    def _bin_converter_availability_text(self) -> str:
        available = [key for key, path in self._bin_converter_paths.items() if path]
        missing = [key for key, path in self._bin_converter_paths.items() if not path]
        available_label = ", ".join(available) if available else "none"
        missing_label = ", ".join(missing) if missing else "none"
        dof_status = "available" if self._dof_converter_path else "missing"
        return f"Available converters: {available_label}. Missing: {missing_label}. DOF converter: {dof_status}."

    def _log_bin_convert(self, message: str, level: str = "INFO") -> None:
        if not hasattr(self, "bin_convert_log"):
            return
        prefix = f"[{level}] "
        self.bin_convert_log.appendPlainText(prefix + message)

    def _update_bin_convert_controls(self) -> None:
        if not hasattr(self, "bin_convert_mode_combo"):
            return
        mode = self.bin_convert_mode_combo.currentData()
        dof_mode = mode == "dof_to_json"
        bin_input = mode in ("bin_to_json", "bin_to_bin")
        self.bin_source_combo.setEnabled(bin_input)
        self.bin_target_combo.setEnabled(mode in ("json_to_bin", "bin_to_bin"))
        self.bin_keep_json_check.setEnabled(mode == "bin_to_bin")
        if hasattr(self, "bin_copy_xml_check"):
            self.bin_copy_xml_check.setEnabled(mode in ("json_to_bin", "bin_to_bin"))
        target_rev = self.bin_target_combo.currentData()
        self.bin_upgrade_blend_check.setEnabled(
            mode in ("json_to_bin", "bin_to_bin") and target_rev == 6
        )
        if dof_mode:
            self.bin_convert_input_edit.setPlaceholderText("Select a .ANIMBBB.asset file or bundle:// entry")
            self.bin_convert_output_edit.setPlaceholderText("Output folder")
        elif mode == "bin_to_json":
            self.bin_convert_input_edit.setPlaceholderText("Select a .bin file")
            self.bin_convert_output_edit.setPlaceholderText("Output .json path")
        elif mode == "json_to_bin":
            self.bin_convert_input_edit.setPlaceholderText("Select a .json file")
            self.bin_convert_output_edit.setPlaceholderText("Output .bin path")
        else:
            self.bin_convert_input_edit.setPlaceholderText("Select a .bin file")
            self.bin_convert_output_edit.setPlaceholderText("Output .bin path")
        if hasattr(self, "dof_convert_group"):
            self.dof_convert_group.setVisible(dof_mode)
        if dof_mode:
            self._refresh_dof_input_options()
        if not self.bin_convert_output_edit.text().strip():
            input_path = self.bin_convert_input_edit.text().strip()
            if input_path:
                suggestion = (
                    self._suggest_dof_output_dir(input_path)
                    if dof_mode
                    else self._suggest_bin_output_path(input_path, mode)
                )
                if suggestion:
                    self.bin_convert_output_edit.setText(suggestion)

    def _suggest_bin_output_path(self, input_path: str, mode: str) -> Optional[str]:
        try:
            stem = str(Path(input_path).with_suffix(""))
        except Exception:
            return None
        if mode == "bin_to_json":
            return f"{stem}.json"
        if mode == "json_to_bin":
            return f"{stem}.bin"
        if mode == "bin_to_bin":
            target_rev = self.bin_target_combo.currentData()
            return f"{stem}_rev{target_rev}.bin"
        return None

    def _get_dof_assets_root(self) -> Optional[str]:
        if hasattr(self, "dof_assets_root_edit"):
            value = self.dof_assets_root_edit.text().strip()
            if value:
                return value
        stored = self.app_settings.value("dof_path", "", type=str) or ""
        stored = stored.strip()
        return stored or None

    def _suggest_dof_output_dir(self, input_path: str) -> Optional[str]:
        if not input_path:
            return None
        dof_root = self._get_dof_assets_root()
        if input_path.lower().startswith("bundle://"):
            if dof_root:
                return os.path.join(dof_root, "Output")
            return None
        if os.path.isfile(input_path) and self._is_unity_bundle_file(input_path):
            if dof_root:
                return os.path.join(dof_root, "Output")
            return os.path.join(os.path.dirname(input_path), "Output")
        try:
            asset_dir = str(Path(input_path).resolve().parent)
        except Exception:
            return None
        if dof_root:
            try:
                rel = os.path.relpath(asset_dir, dof_root)
                output_root = os.path.join(dof_root, "Output")
                return os.path.join(output_root, rel)
            except Exception:
                pass
        return os.path.join(asset_dir, "Output")

    def _browse_bin_convert_input(self) -> None:
        mode = self.bin_convert_mode_combo.currentData()
        if mode == "dof_to_json":
            filter_text = "DOF ANIMBBB (*.ANIMBBB.asset *.animbbb.asset);;All Files (*)"
        elif mode == "json_to_bin":
            filter_text = "Animation JSON (*.json);;All Files (*)"
        else:
            filter_text = "Animation BIN (*.bin);;All Files (*)"
        start_dir = Path(self.bin_convert_input_edit.text() or Path.home())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select Input File",
            str(start_dir),
            filter_text,
        )
        if filename:
            self.bin_convert_input_edit.setText(filename)
            if not self.bin_convert_output_edit.text().strip():
                suggestion = (
                    self._suggest_dof_output_dir(filename)
                    if mode == "dof_to_json"
                    else self._suggest_bin_output_path(filename, mode)
                )
                if suggestion:
                    self.bin_convert_output_edit.setText(suggestion)

    def _browse_bin_convert_output(self) -> None:
        mode = self.bin_convert_mode_combo.currentData()
        if mode == "dof_to_json":
            start_dir = Path(self.bin_convert_output_edit.text() or Path.home())
            folder = QFileDialog.getExistingDirectory(
                self,
                "Select Output Folder",
                str(start_dir),
            )
            if folder:
                self.bin_convert_output_edit.setText(folder)
            return
        if mode == "bin_to_json":
            filter_text = "Animation JSON (*.json);;All Files (*)"
        else:
            filter_text = "Animation BIN (*.bin);;All Files (*)"
        start_dir = Path(self.bin_convert_output_edit.text() or Path.home())
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output File",
            str(start_dir),
            filter_text,
        )
        if filename:
            self.bin_convert_output_edit.setText(filename)

    def _browse_dof_assets_root(self) -> None:
        start_dir = self.dof_assets_root_edit.text().strip() or self.app_settings.value("dof_path", "", type=str)
        if not start_dir:
            start_dir = str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select DOF Assets Root",
            str(start_dir),
        )
        if folder:
            self.dof_assets_root_edit.setText(folder)
            self.app_settings.setValue("dof_path", folder)
            self._refresh_dof_input_options(force=True)
            if self.bin_convert_mode_combo.currentData() == "dof_to_json":
                if not self.bin_convert_output_edit.text().strip():
                    suggestion = self._suggest_dof_output_dir(self.bin_convert_input_edit.text().strip())
                    if suggestion:
                        self.bin_convert_output_edit.setText(suggestion)

    def _run_bin_revision_conversion(self) -> None:
        mode = self.bin_convert_mode_combo.currentData()
        input_path = self.bin_convert_input_edit.text().strip()
        output_path = self.bin_convert_output_edit.text().strip()
        dof_mode = mode == "dof_to_json"
        if not input_path:
            QMessageBox.warning(self, "Missing Input", "Please select an input file.")
            return
        is_bundle_ref = dof_mode and input_path.lower().startswith("bundle://")
        if not os.path.exists(input_path) and not is_bundle_ref:
            QMessageBox.warning(self, "Missing File", "The selected input file no longer exists.")
            return
        if not output_path:
            suggestion = (
                self._suggest_dof_output_dir(input_path)
                if dof_mode
                else self._suggest_bin_output_path(input_path, mode)
            )
            if suggestion:
                output_path = suggestion
                self.bin_convert_output_edit.setText(output_path)
            else:
                QMessageBox.warning(self, "Missing Output", "Please select an output file path.")
                return

        self.bin_convert_log.clear()
        self._log_bin_convert(f"Mode: {mode}", "INFO")
        self._log_bin_convert(f"Input: {input_path}", "INFO")
        self._log_bin_convert(f"Output: {output_path}", "INFO")

        if mode == "dof_to_json":
            success = self._convert_dof_to_json(input_path, output_path)
            if success:
                QMessageBox.information(self, "Conversion Complete", "DOF conversion complete.")
            return

        if mode == "bin_to_json":
            converter_key = self.bin_source_combo.currentData()
            success = self._convert_bin_to_json(input_path, output_path, converter_key)
            if success:
                QMessageBox.information(self, "Conversion Complete", "BIN -> JSON conversion complete.")
            return

        if mode == "json_to_bin":
            target_rev = int(self.bin_target_combo.currentData())
            success = self._convert_json_to_bin(
                input_path,
                output_path,
                target_rev,
                self.bin_upgrade_blend_check.isChecked(),
            )
            if success:
                if getattr(self, "bin_copy_xml_check", None) and self.bin_copy_xml_check.isChecked():
                    self._copy_xml_resources_from_json(input_path, output_path)
                QMessageBox.information(self, "Conversion Complete", "JSON -> BIN conversion complete.")
            return

        target_rev = int(self.bin_target_combo.currentData())
        keep_json = self.bin_keep_json_check.isChecked()
        converter_key = self.bin_source_combo.currentData()
        intermediate_path = None
        temp_file = None
        try:
            if keep_json:
                intermediate_path = str(Path(output_path).with_suffix(".json"))
            else:
                handle, intermediate_path = tempfile.mkstemp(suffix=".json")
                os.close(handle)
                temp_file = intermediate_path

            if not self._convert_bin_to_json(input_path, intermediate_path, converter_key):
                return

            success = self._convert_json_to_bin(
                intermediate_path,
                output_path,
                target_rev,
                self.bin_upgrade_blend_check.isChecked(),
            )
            if success:
                if getattr(self, "bin_copy_xml_check", None) and self.bin_copy_xml_check.isChecked():
                    self._copy_xml_resources_from_json(intermediate_path, output_path)
                QMessageBox.information(self, "Conversion Complete", "BIN -> BIN conversion complete.")
        finally:
            if temp_file and os.path.exists(temp_file):
                with contextlib.suppress(Exception):
                    os.remove(temp_file)

    def _convert_bin_to_json(self, bin_path: str, json_path: str, converter_key: str) -> bool:
        if converter_key == "auto":
            return self._convert_bin_to_json_auto(bin_path, json_path)
        script_path = self._bin_converter_paths.get(converter_key)
        if not script_path:
            self._log_bin_convert(f"Converter '{converter_key}' is not available.", "ERROR")
            QMessageBox.warning(self, "Missing Converter", f"Converter '{converter_key}' is not available.")
            return False
        cmd, cwd = self._build_bin_to_json_command(converter_key, script_path, bin_path, json_path)
        produced_json = json_path
        if converter_key in ("rev2", "rev4", "rev6"):
            produced_json = str(Path(bin_path).with_suffix(".json"))
        if not self._run_converter(cmd, cwd, produced_json):
            return False
        if os.path.normcase(os.path.normpath(produced_json)) != os.path.normcase(os.path.normpath(json_path)):
            try:
                shutil.move(produced_json, json_path)
            except Exception as exc:
                self._log_bin_convert(f"Failed to move JSON output: {exc}", "ERROR")
                return False
        return True

    def _convert_bin_to_json_auto(self, bin_path: str, json_path: str) -> bool:
        bin_name = os.path.basename(bin_path).lower()
        normalized_path = os.path.normcase(os.path.normpath(bin_path))
        is_muppet_bin = bin_name.startswith("muppet_")
        is_my_singing_muppets = "my singing muppets.app" in normalized_path
        is_composer_bin = "_composer" in bin_name

        if is_muppet_bin and not is_my_singing_muppets and self._bin_converter_paths.get("muppets"):
            self._log_bin_convert("Detected muppet_* BIN; using muppets converter.", "INFO")
            script = self._bin_converter_paths["muppets"]
            cmd, cwd = self._build_bin_to_json_command("muppets", script, bin_path, json_path)
            if self._run_converter(cmd, cwd, json_path):
                self._log_bin_convert("Converted using muppets converter.", "SUCCESS")
                return True
            self._log_bin_convert("Muppets converter failed; stopping auto-detect.", "ERROR")
            QMessageBox.warning(self, "Conversion Failed", "Muppets converter failed for this BIN.")
            return False

        attempts: List[Tuple[str, List[str], Optional[str]]] = []

        def queue(key: str) -> None:
            script = self._bin_converter_paths.get(key)
            if not script:
                self._log_bin_convert(f"Converter '{key}' is missing; skipping.", "WARNING")
                return
            cmd, cwd = self._build_bin_to_json_command(key, script, bin_path, json_path)
            attempts.append((key, cmd, cwd))

        if is_muppet_bin and is_my_singing_muppets:
            queue("rev2")
        if is_muppet_bin and not is_my_singing_muppets:
            queue("muppets")
        if is_composer_bin:
            queue("rev4")
        queue("legacy")
        queue("choir")
        if not (is_muppet_bin and is_my_singing_muppets):
            queue("rev2")
        if not is_composer_bin:
            queue("rev4")
        queue("oldest")
        queue("rev6")

        if not attempts:
            self._log_bin_convert("No BIN converters available for auto-detect.", "ERROR")
            QMessageBox.warning(self, "Missing Converters", "No BIN converters are available.")
            return False

        for key, cmd, cwd in attempts:
            self._log_bin_convert(f"Trying {key} converter...", "INFO")
            expected_json = json_path
            if key in ("rev2", "rev4", "rev6"):
                expected_json = str(Path(bin_path).with_suffix(".json"))
            if self._run_converter(cmd, cwd, expected_json, show_success=False):
                if os.path.normcase(os.path.normpath(expected_json)) != os.path.normcase(os.path.normpath(json_path)):
                    with contextlib.suppress(Exception):
                        shutil.move(expected_json, json_path)
                self._log_bin_convert(f"Converted using {key}.", "SUCCESS")
                return True
        self._log_bin_convert("All converters failed. See log for details.", "ERROR")
        QMessageBox.warning(self, "Conversion Failed", "Auto conversion failed for all converters.")
        return False

    def _convert_dof_to_json(self, input_path: str, output_dir: str) -> bool:
        if not self._dof_converter_path or not os.path.exists(self._dof_converter_path):
            self._log_bin_convert("DOF converter script is not available.", "ERROR")
            QMessageBox.warning(self, "Missing Converter", "DOF converter script was not found.")
            return False
        dof_root = self._get_dof_assets_root()
        if not dof_root or not os.path.isdir(dof_root):
            self._log_bin_convert("DOF assets root is missing or invalid.", "ERROR")
            QMessageBox.warning(self, "Missing DOF Path", "Please set a valid DOF assets root.")
            return False
        if not output_dir:
            self._log_bin_convert("Output folder is missing.", "ERROR")
            QMessageBox.warning(self, "Missing Output", "Please select an output folder.")
            return False
        os.makedirs(output_dir, exist_ok=True)

        is_bundle_ref = input_path.lower().startswith("bundle://")
        is_bundle_file = os.path.isfile(input_path) and self._is_unity_bundle_file(input_path)
        bundle_anim = None
        bundle_root = None

        if is_bundle_ref:
            bundle_anim = input_path.split("bundle://", 1)[1].strip()
            bundle_root = dof_root
        elif is_bundle_file:
            bundle_anim = getattr(self, "dof_bundle_anim_edit", None)
            bundle_anim = bundle_anim.text().strip() if bundle_anim else ""
            if not bundle_anim:
                self._log_bin_convert("Bundle ANIMBBB name is required for __data bundle inputs.", "ERROR")
                QMessageBox.warning(
                    self,
                    "Missing Bundle Name",
                    "Enter the ANIMBBB name (e.g., A_tweedle_adult_03_cloud_01.ANIMBBB) when converting a bundle.",
                )
                return False
            bundle_root = input_path

        cmd_input = f"bundle://{bundle_anim}" if bundle_anim else input_path
        cmd = self._build_python_command(self._dof_converter_path) + [
            cmd_input,
            "--output",
            output_dir,
        ]
        if bundle_anim:
            cmd += ["--bundle-root", str(bundle_root)]
        else:
            cmd += ["--assets-root", dof_root]

        if getattr(self, "dof_mesh_pivot_checkbox", None) and self.dof_mesh_pivot_checkbox.isChecked():
            cmd.append("--mesh-pivot-local")
        if (
            getattr(self, "dof_include_mesh_xml_checkbox", None)
            and self.dof_include_mesh_xml_checkbox.isChecked()
        ):
            cmd.append("--include-mesh-xml")
        premultiply_alpha = False
        if hasattr(self, "dof_deploy_premultiply_alpha_checkbox"):
            premultiply_alpha = self.dof_deploy_premultiply_alpha_checkbox.isChecked()
        elif (
            getattr(self, "dof_premultiply_alpha_checkbox", None)
            and self.dof_premultiply_alpha_checkbox.isChecked()
        ):
            premultiply_alpha = True
        if premultiply_alpha:
            cmd.append("--premultiply-alpha")
        alpha_hardness = 0.0
        if hasattr(self, "dof_alpha_hardness_spin"):
            try:
                alpha_hardness = float(self.dof_alpha_hardness_spin.value())
            except (TypeError, ValueError):
                alpha_hardness = 0.0
        alpha_hardness = max(0.0, min(2.0, alpha_hardness))
        if alpha_hardness > 1e-6:
            cmd += ["--alpha-hardness", f"{alpha_hardness:.3f}"]
        hires_state = None
        if hasattr(self, "dof_deploy_hires_xml_checkbox"):
            hires_state = self.dof_deploy_hires_xml_checkbox.isChecked()
        elif hasattr(self, "dof_hires_xml_checkbox"):
            hires_state = self.dof_hires_xml_checkbox.isChecked()
        if hires_state is not None:
            cmd.append("--hires-xml" if hires_state else "--no-hires-xml")
        if getattr(self, "dof_swap_anchor_report_checkbox", None) and self.dof_swap_anchor_report_checkbox.isChecked():
            cmd.append("--swap-anchor-report")
        if getattr(self, "dof_swap_anchor_edge_align_checkbox", None) and self.dof_swap_anchor_edge_align_checkbox.isChecked():
            cmd.append("--swap-anchor-edge-align")
        if getattr(self, "dof_swap_anchor_pivot_offset_checkbox", None) and self.dof_swap_anchor_pivot_offset_checkbox.isChecked():
            cmd.append("--swap-anchor-pivot-offset")
        if getattr(self, "dof_swap_anchor_report_override_checkbox", None) and self.dof_swap_anchor_report_override_checkbox.isChecked():
            cmd.append("--swap-anchor-report-override")

        expected_json = None
        target_name = input_path
        if bundle_anim:
            target_name = bundle_anim
        base = os.path.basename(target_name)
        lowered = base.lower()
        for suffix in (".animbbb.asset", ".animbbb"):
            if lowered.endswith(suffix):
                base = base[: -len(suffix)]
                break
        if base:
            expected_json = os.path.join(output_dir, f"{base}.json")

        self._log_bin_convert("Running DOF converter...", "INFO")
        result = self._run_converter_command(cmd, os.path.dirname(self._dof_converter_path))
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip() or "Unknown error"
            self._log_bin_convert(f"DOF converter failed: {error_text}", "ERROR")
            return False

        if expected_json and not os.path.exists(expected_json):
            self._log_bin_convert("DOF conversion completed but output JSON was not found.", "WARNING")
        else:
            self._log_bin_convert("DOF conversion completed successfully.", "SUCCESS")
        stdout = (result.stdout or "").strip()
        if stdout:
            self._log_bin_convert(stdout, "INFO")
        return True

    def _build_bin_to_json_command(
        self,
        converter_key: str,
        script_path: str,
        bin_path: str,
        json_path: str,
    ) -> Tuple[List[str], Optional[str]]:
        if converter_key in ("legacy", "choir", "muppets", "oldest"):
            cmd = self._build_python_command(script_path) + [bin_path, "-o", json_path]
        else:
            cmd = self._build_python_command(script_path) + ["d", bin_path]
        return cmd, os.path.dirname(script_path)

    def _convert_json_to_bin(
        self,
        json_path: str,
        output_bin_path: str,
        target_rev: int,
        upgrade_blend: bool,
    ) -> bool:
        script_key = f"rev{target_rev}"
        script_path = self._bin_converter_paths.get(script_key)
        if not script_path:
            self._log_bin_convert(f"Converter '{script_key}' is not available.", "ERROR")
            QMessageBox.warning(self, "Missing Converter", f"Converter '{script_key}' is not available.")
            return False

        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self._log_bin_convert(f"Failed to read JSON: {exc}", "ERROR")
            QMessageBox.warning(self, "Invalid JSON", f"Could not read JSON:\n{exc}")
            return False

        rev_value = payload.get("rev")
        rev_int = None
        if isinstance(rev_value, int):
            rev_int = rev_value
        elif isinstance(rev_value, str):
            try:
                rev_int = int(rev_value.strip())
            except (TypeError, ValueError):
                rev_int = None
        if target_rev in (2, 4) and rev_int != target_rev:
            self._log_bin_convert(
                f"JSON rev is {rev_value}; cannot export as rev{target_rev} without a proper down-conversion.",
                "ERROR",
            )
            QMessageBox.warning(
                self,
                "Revision Mismatch",
                f"JSON rev is {rev_value}. Exporting a rev{target_rev} BIN is not supported.",
            )
            return False

        working_json = json_path
        temp_json = None
        try:
            target_stem = Path(output_bin_path).with_suffix("").name
            target_dir = Path(output_bin_path).parent
            target_json_path = str(target_dir / f"{target_stem}.json")

            if target_rev == 6:
                upgraded = self._upgrade_json_for_rev6(payload, upgrade_blend)
                target_dir.mkdir(parents=True, exist_ok=True)
                with open(target_json_path, "w", encoding="utf-8") as handle:
                    json.dump(upgraded, handle, indent=2)
                working_json = target_json_path
                temp_json = target_json_path
                self._log_bin_convert("Prepared JSON payload for rev6 export.", "INFO")
            elif os.path.normcase(os.path.normpath(json_path)) != os.path.normcase(os.path.normpath(target_json_path)):
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(json_path, target_json_path)
                working_json = target_json_path
                temp_json = target_json_path

            cmd = self._build_python_command(script_path) + ["b", working_json]
            produced_bin = str(Path(working_json).with_suffix(".bin"))
            if not self._run_converter(cmd, os.path.dirname(script_path), produced_bin):
                return False
            if os.path.normcase(os.path.normpath(produced_bin)) != os.path.normcase(os.path.normpath(output_bin_path)):
                shutil.move(produced_bin, output_bin_path)
            self._log_bin_convert(f"Wrote BIN: {output_bin_path}", "SUCCESS")
            return True
        finally:
            if temp_json and os.path.exists(temp_json):
                with contextlib.suppress(Exception):
                    os.remove(temp_json)

    def _upgrade_json_for_rev6(self, payload: Dict[str, Any], upgrade_blend: bool) -> Dict[str, Any]:
        updated = dict(payload)
        updated["rev"] = 6
        if "blend_version" not in updated:
            updated["blend_version"] = 2 if upgrade_blend else 1
        if upgrade_blend:
            for anim in updated.get("anims", []):
                for layer in anim.get("layers", []):
                    if layer.get("blend", 0) == 1:
                        layer["blend"] = 2
            updated["blend_version"] = 2
        normalized, adjustments = self._normalize_rev6_payload_fields(updated)
        if adjustments:
            self._log_bin_convert(
                f"Adjusted {adjustments} layer indices to fit rev6 int16 constraints.",
                "WARNING",
            )
        return normalized

    def _normalize_rev6_payload_fields(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        updated = dict(payload)
        adjustments = 0
        warnings: List[str] = []

        def normalize_int16(value: Any, label: str) -> Any:
            nonlocal adjustments
            if value is None:
                return value
            if isinstance(value, bool):
                return int(value)
            if not isinstance(value, int):
                try:
                    value = int(str(value).strip())
                except (TypeError, ValueError):
                    if len(warnings) < 5:
                        warnings.append(f"{label} value '{value}' is not numeric; set to 0.")
                    adjustments += 1
                    return 0
            if -32768 <= value <= 32767:
                return value
            if 32768 <= value <= 65535:
                adjustments += 1
                return value - 65536
            adjustments += 1
            if len(warnings) < 5:
                warnings.append(f"{label} value {value} out of int16 range; clamped.")
            return max(-32768, min(32767, value))

        for anim in updated.get("anims", []):
            for layer in anim.get("layers", []):
                if isinstance(layer, dict):
                    layer["parent"] = normalize_int16(layer.get("parent"), "layer.parent")
                    layer["id"] = normalize_int16(layer.get("id"), "layer.id")
                    layer["src"] = normalize_int16(layer.get("src"), "layer.src")

        for warning in warnings:
            self._log_bin_convert(warning, "WARNING")
        return updated, adjustments

    def _run_converter(
        self,
        cmd: List[str],
        cwd: Optional[str],
        expected_output: Optional[str],
        show_success: bool = True,
    ) -> bool:
        try:
            result = self._run_converter_command(cmd, cwd)
        except Exception as exc:
            self._log_bin_convert(f"Converter raised {exc}", "ERROR")
            return False
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip() or "Unknown error"
            self._log_bin_convert(f"Converter failed: {error_text}", "ERROR")
            return False
        if expected_output and not os.path.exists(expected_output):
            self._log_bin_convert("Converter completed but output was not created.", "ERROR")
            return False
        if show_success:
            self._log_bin_convert("Converter completed successfully.", "SUCCESS")
        stdout = (result.stdout or "").strip()
        if stdout:
            self._log_bin_convert(stdout, "INFO")
        return True

    @staticmethod
    def _is_unity_bundle_file(path: str) -> bool:
        try:
            with open(path, "rb") as handle:
                magic = handle.read(8)
        except OSError:
            return False
        return magic.startswith((b"UnityFS", b"UnityRaw", b"UnityWeb"))

    def _refresh_dof_input_options(self, force: bool = False) -> None:
        if not hasattr(self, "dof_input_combo"):
            return
        root = self._get_dof_assets_root()
        if not root or not os.path.isdir(root):
            self._dof_input_cache_root = None
            self._dof_input_cache_entries = []
            self.dof_input_combo.blockSignals(True)
            self.dof_input_combo.clear()
            self.dof_input_combo.addItem("Set DOF assets root to list animations", "")
            self.dof_input_combo.blockSignals(False)
            return

        root_norm = os.path.normcase(os.path.normpath(root))
        if not force and self._dof_input_cache_root == root_norm and self._dof_input_cache_entries:
            self._populate_dof_input_combo(self._dof_input_cache_entries)
            return

        entries: List[Tuple[str, str]] = []
        asset_paths = self._scan_dof_anim_assets(root)
        for asset_path in asset_paths:
            try:
                rel = os.path.relpath(asset_path, root).replace("\\", "/")
            except Exception:
                rel = os.path.basename(asset_path)
            entries.append((rel, asset_path))

        bundle_names = self._get_dof_bundle_anim_names(root)
        for name in bundle_names:
            label = f"bundle://{name}"
            entries.append((label, label))

        entries.sort(key=lambda item: item[0].lower())
        self._dof_input_cache_root = root_norm
        self._dof_input_cache_entries = entries
        self._populate_dof_input_combo(entries)

    def _populate_dof_input_combo(self, entries: List[Tuple[str, str]]) -> None:
        self.dof_input_combo.blockSignals(True)
        self.dof_input_combo.clear()
        if not entries:
            self.dof_input_combo.addItem("No DOF animations found", "")
            self.dof_input_combo.blockSignals(False)
            return
        for label, value in entries:
            self.dof_input_combo.addItem(label, value)
        self.dof_input_combo.blockSignals(False)

        current_input = self.bin_convert_input_edit.text().strip()
        if current_input:
            idx = self.dof_input_combo.findData(current_input)
            if idx >= 0:
                self.dof_input_combo.setCurrentIndex(idx)
                return
        self.dof_input_combo.setCurrentIndex(0)
        self._apply_dof_input_selection(self.dof_input_combo.currentData())

    def _on_dof_input_selected(self, index: int) -> None:
        if index < 0:
            return
        data = self.dof_input_combo.currentData()
        if not data:
            return
        self._apply_dof_input_selection(str(data))

    def _apply_dof_input_selection(self, value: str) -> None:
        if not value:
            return
        self.bin_convert_input_edit.setText(value)
        if hasattr(self, "dof_bundle_anim_edit"):
            if value.lower().startswith("bundle://"):
                anim_name = value.split("bundle://", 1)[1].strip()
                self.dof_bundle_anim_edit.setText(anim_name)
            else:
                self.dof_bundle_anim_edit.setText("")
        if not self.bin_convert_output_edit.text().strip():
            suggestion = self._suggest_dof_output_dir(value)
            if suggestion:
                self.bin_convert_output_edit.setText(suggestion)

    def _scan_dof_anim_assets(self, root: str) -> List[str]:
        paths: List[str] = []
        root_path = Path(root)
        for pattern in ("*.ANIMBBB.asset", "*.animbbb.asset"):
            for path in root_path.rglob(pattern):
                if path.is_file():
                    paths.append(str(path))
        return paths

    def _get_dof_bundle_anim_names(self, root: str) -> List[str]:
        cache_path = self._bundle_index_cache_path(root)
        cached = self._read_bundle_index_cache(cache_path, root)
        if cached:
            return cached

        try:
            import UnityPy  # type: ignore
        except Exception:
            self._log_bin_convert(
                "UnityPy is required to scan bundle animations. Install it to list DOF bundle files.",
                "WARNING",
            )
            return []

        self._log_bin_convert("Scanning Unity bundles for ANIMBBB entries (this can take a while)...", "INFO")
        anim_names: List[str] = []
        anim_set: set[str] = set()
        bundle_map: Dict[str, str] = {}
        data_files = self._find_unity_bundle_data_files(root)
        for idx, data_path in enumerate(data_files):
            if idx and idx % 50 == 0:
                self._log_bin_convert(f"Scanning bundles... ({idx}/{len(data_files)})", "INFO")
            try:
                env = UnityPy.load(data_path)
            except Exception:
                continue
            for obj in env.objects:
                if getattr(obj.type, "name", None) != "MonoBehaviour":
                    continue
                try:
                    data = obj.read()
                    name = getattr(data, "m_Name", "") or ""
                except Exception:
                    continue
                if not name:
                    continue
                lower = name.lower()
                if lower.endswith(".animbbb") and name not in anim_set:
                    anim_set.add(name)
                    anim_names.append(name)
                    bundle_map.setdefault(name, data_path)
                    bundle_map.setdefault(lower, data_path)

        anim_names.sort(key=lambda value: value.lower())
        self._write_bundle_index_cache(cache_path, root, anim_names, bundle_map)
        return anim_names

    def _bundle_index_cache_path(self, root: str) -> str:
        return os.path.join(root, "Output", "_bundle_index.json")

    def _read_bundle_index_cache(self, cache_path: str, root: str) -> List[str]:
        if not cache_path or not os.path.exists(cache_path):
            return []
        try:
            payload = json.loads(Path(cache_path).read_text(encoding="utf-8"))
        except Exception:
            return []
        cached_root = payload.get("root")
        if not cached_root:
            return []
        if os.path.normcase(os.path.normpath(str(cached_root))) != os.path.normcase(os.path.normpath(root)):
            return []
        names = payload.get("anim_names", [])
        if not isinstance(names, list):
            return []
        return [str(name) for name in names if isinstance(name, str) and name]

    def _write_bundle_index_cache(
        self,
        cache_path: str,
        root: str,
        anim_names: List[str],
        bundle_map: Optional[Dict[str, str]] = None,
    ) -> None:
        if not cache_path:
            return
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            payload = {
                "root": root,
                "anim_names": list(anim_names),
                "bundle_map": bundle_map or {},
            }
            Path(cache_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            return

    def _find_unity_bundle_data_files(self, root: str) -> List[str]:
        data_files: List[str] = []
        for base, _, files in os.walk(root):
            if "__data" not in files:
                continue
            candidate = os.path.join(base, "__data")
            if self._is_unity_bundle_file(candidate):
                data_files.append(candidate)
        return data_files

    def _copy_xml_resources_from_json(self, json_path: str, output_bin_path: str) -> None:
        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self._log_bin_convert(f"Failed to read JSON for XML copy: {exc}", "ERROR")
            return
        sources = payload.get("sources", [])
        if not sources:
            self._log_bin_convert("No sources found in JSON; skipping XML copy.", "WARNING")
            return

        xml_root, data_root = self._determine_output_data_roots(output_bin_path)
        os.makedirs(xml_root, exist_ok=True)
        copied_xml = 0
        copied_images = 0

        for source in sources:
            if not isinstance(source, dict):
                continue
            src = source.get("src")
            if not src:
                continue
            xml_path = self._resolve_xml_source_path(src, json_path)
            if not xml_path:
                self._log_bin_convert(f"XML source not found: {src}", "WARNING")
                continue
            target_xml = self._target_xml_path(xml_root, src)
            os.makedirs(os.path.dirname(target_xml), exist_ok=True)
            try:
                shutil.copy2(xml_path, target_xml)
                copied_xml += 1
                self._log_bin_convert(f"Copied XML: {src}", "INFO")
            except Exception as exc:
                self._log_bin_convert(f"Failed to copy XML '{src}': {exc}", "ERROR")
                continue

            image_ref = self._extract_xml_image_path(xml_path)
            if not image_ref:
                continue
            image_path = self._resolve_image_source_path(image_ref, xml_path)
            if not image_path:
                self._log_bin_convert(f"Image not found for XML '{src}': {image_ref}", "WARNING")
                continue
            target_image = self._target_image_path(image_ref, target_xml, data_root)
            os.makedirs(os.path.dirname(target_image), exist_ok=True)
            try:
                shutil.copy2(image_path, target_image)
                copied_images += 1
                self._log_bin_convert(f"Copied image: {image_ref}", "INFO")
            except Exception as exc:
                self._log_bin_convert(f"Failed to copy image '{image_ref}': {exc}", "ERROR")

        self._log_bin_convert(
            f"XML copy complete (XML: {copied_xml}, images: {copied_images}).",
            "SUCCESS",
        )

    def _determine_output_data_roots(self, output_bin_path: str) -> Tuple[str, str]:
        output_dir = os.path.dirname(output_bin_path)
        if os.path.basename(output_dir).lower() == "xml_bin":
            data_root = os.path.dirname(output_dir)
            xml_root = os.path.join(data_root, "xml_resources")
        else:
            data_root = output_dir
            xml_root = os.path.join(output_dir, "xml_resources")
        return xml_root, data_root

    def _target_xml_path(self, xml_root: str, src: str) -> str:
        rel = str(src).replace("\\", "/").lstrip("/")
        lower_rel = rel.lower()
        if lower_rel.startswith("xml_resources/"):
            rel = rel[len("xml_resources/"):]
        parts = [p for p in rel.split("/") if p]
        return os.path.join(xml_root, *parts) if parts else os.path.join(xml_root, os.path.basename(rel))

    def _resolve_xml_source_path(self, src: str, json_path: str) -> Optional[str]:
        if not src:
            return None
        if os.path.isabs(src) and os.path.exists(src):
            return src
        norm = str(src).replace("\\", "/")
        json_dir = os.path.dirname(json_path)
        candidates: List[str] = []

        candidates.append(os.path.join(json_dir, norm))
        if os.path.basename(json_dir).lower() == "xml_bin":
            data_root = os.path.dirname(json_dir)
            candidates.append(os.path.join(data_root, norm))
            candidates.append(os.path.join(data_root, "xml_resources", os.path.basename(norm)))
        candidates.append(os.path.join(json_dir, "xml_resources", norm))
        if norm.lower().startswith("xml_resources/"):
            stripped = norm[len("xml_resources/"):]
            candidates.append(os.path.join(json_dir, stripped))
            candidates.append(os.path.join(json_dir, "..", norm))
        if self.game_path:
            data_root = os.path.join(str(self.game_path), "data")
            candidates.append(os.path.join(data_root, norm))
            if not norm.lower().startswith("xml_resources/"):
                candidates.append(os.path.join(data_root, "xml_resources", norm))

        for candidate in candidates:
            candidate = os.path.normpath(candidate)
            if os.path.exists(candidate):
                return candidate
        return None

    def _extract_xml_image_path(self, xml_path: str) -> Optional[str]:
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except Exception:
            return None
        if root is None:
            return None
        return root.get("imagePath") or root.get("image")

    def _resolve_image_source_path(self, image_ref: str, xml_path: str) -> Optional[str]:
        if not image_ref:
            return None
        if os.path.isabs(image_ref) and os.path.exists(image_ref):
            return image_ref
        xml_dir = os.path.dirname(xml_path)
        candidates = [os.path.join(xml_dir, image_ref)]
        if os.path.basename(xml_dir).lower() == "xml_resources":
            data_root = os.path.dirname(xml_dir)
            candidates.append(os.path.join(data_root, image_ref))
        if self.game_path:
            candidates.append(os.path.join(str(self.game_path), "data", image_ref))
        for candidate in candidates:
            candidate = os.path.normpath(candidate)
            if os.path.exists(candidate):
                return candidate
        return None

    def _target_image_path(self, image_ref: str, target_xml_path: str, data_root: str) -> str:
        rel = str(image_ref).replace("\\", "/").lstrip("/")
        if "/" in rel:
            parts = [p for p in rel.split("/") if p]
            return os.path.join(data_root, *parts)
        return os.path.join(os.path.dirname(target_xml_path), rel)

    @staticmethod
    def _build_python_command(script_path: str) -> List[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--run-script", script_path]
        return [sys.executable, script_path]

    def _run_converter_command(self, cmd: List[str], cwd: Optional[str]) -> subprocess.CompletedProcess:
        run_kwargs = {
            "capture_output": True,
            "text": True,
            "cwd": cwd,
        }
        if getattr(sys, "frozen", False) and len(cmd) >= 3 and cmd[0] == sys.executable and cmd[1] == "--run-script":
            script_path = cmd[2]
            script_args = cmd[3:]
            return self._run_embedded_script(script_path, script_args, cwd or os.path.dirname(script_path))
        return subprocess.run(cmd, **run_kwargs)

    def _run_embedded_script(
        self,
        script_path: str,
        args: List[str],
        working_dir: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        previous_cwd = os.getcwd()
        previous_argv = sys.argv[:]
        return_code = 0
        try:
            if working_dir:
                os.chdir(working_dir)
            sys.argv = [script_path] + list(args)
            script_dir = os.path.dirname(script_path)
            inserted_path = False
            if script_dir and script_dir not in sys.path:
                sys.path.insert(0, script_dir)
                inserted_path = True
            with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
                try:
                    runpy.run_path(script_path, run_name="__main__")
                except SystemExit as exc:
                    code = exc.code
                    if isinstance(code, int):
                        return_code = code
                    else:
                        buf_err.write(str(code))
                        return_code = 1
        except Exception as exc:
            buf_err.write(str(exc))
            return_code = 1
        finally:
            sys.argv = previous_argv
            os.chdir(previous_cwd)
            if inserted_path:
                with contextlib.suppress(ValueError):
                    sys.path.remove(script_dir)
        return subprocess.CompletedProcess(
            args=[script_path] + list(args),
            returncode=return_code,
            stdout=buf_out.getvalue(),
            stderr=buf_err.getvalue(),
        )

    # ------------------------------------------------------------------
    # Anim transfer helpers
    def _log_anim_transfer(self, message: str, level: str = "INFO") -> None:
        if not hasattr(self, "anim_transfer_log"):
            return
        self.anim_transfer_log.appendPlainText(f"[{level}] {message}")

    def _sync_dof_premultiply_from_deploy(self, state: int) -> None:
        if hasattr(self, "dof_premultiply_alpha_checkbox"):
            self.dof_premultiply_alpha_checkbox.setChecked(bool(state))

    def _sync_dof_premultiply_from_converter(self, state: int) -> None:
        if hasattr(self, "dof_deploy_premultiply_alpha_checkbox"):
            self.dof_deploy_premultiply_alpha_checkbox.setChecked(bool(state))

    def _sync_dof_hires_from_deploy(self, state: int) -> None:
        if hasattr(self, "dof_hires_xml_checkbox"):
            self.dof_hires_xml_checkbox.setChecked(bool(state))

    def _sync_dof_hires_from_converter(self, state: int) -> None:
        if hasattr(self, "dof_deploy_hires_xml_checkbox"):
            self.dof_deploy_hires_xml_checkbox.setChecked(bool(state))

    def _anim_transfer_list_root(self, use_dof: bool) -> Optional[Path]:
        if use_dof:
            dof_root = self._get_dof_assets_root()
            if not dof_root:
                return None
            output_root = Path(dof_root) / "Output"
            return output_root if output_root.exists() else Path(dof_root)

        game_root = None
        if self.game_path:
            game_root = str(self.game_path)
        elif self._game_path_str:
            game_root = self._game_path_str
        data_root = self._resolve_game_data_root(game_root) if game_root else None
        if not data_root:
            return None
        xml_bin = data_root / "xml_bin"
        return xml_bin if xml_bin.exists() else data_root

    @staticmethod
    def _scan_anim_transfer_files(root: Path) -> List[Path]:
        results: List[Path] = []
        if not root or not root.exists():
            return results
        for pattern in ("*.bin", "*.json"):
            for path in root.rglob(pattern):
                if not path.is_file():
                    continue
                name_lower = path.name.lower()
                if name_lower.endswith(".layers.json") or name_lower.endswith(".keylayers.json"):
                    continue
                results.append(path)
        results.sort(key=lambda p: str(p).lower())
        return results

    def _populate_anim_transfer_combo(
        self,
        combo: QComboBox,
        root: Optional[Path],
        use_dof: bool,
        current_path: str,
    ) -> int:
        combo.blockSignals(True)
        combo.clear()

        if not root or not root.exists():
            placeholder = (
                "Set DOF assets root to list animations"
                if use_dof
                else "Set game path to list animations"
            )
            combo.addItem(placeholder, "")
            combo.blockSignals(False)
            return 0

        files = self._scan_anim_transfer_files(root)
        if not files:
            combo.addItem("No animations found", "")
            combo.blockSignals(False)
            return 0

        root_norm = os.path.normcase(os.path.normpath(str(root)))
        current_norm = os.path.normcase(os.path.normpath(current_path)) if current_path else ""
        index_by_norm: Dict[str, int] = {}
        combo.addItem("Select animation...", "")
        for path in files:
            try:
                label = os.path.relpath(path, root).replace("\\", "/")
            except Exception:
                label = path.name
            combo.addItem(label, str(path))
            idx = combo.count() - 1
            index_by_norm[os.path.normcase(os.path.normpath(str(path)))] = idx
        if current_norm in index_by_norm:
            combo.setCurrentIndex(index_by_norm[current_norm])
        combo.blockSignals(False)
        return len(files)

    def _populate_anim_transfer_combo_entries(
        self,
        combo: QComboBox,
        entries: List[Tuple[str, str]],
        current_path: str,
        placeholder: str,
    ) -> int:
        combo.blockSignals(True)
        combo.clear()
        if not entries:
            combo.addItem(placeholder, "")
            combo.blockSignals(False)
            return 0
        combo.addItem("Select animation...", "")
        index_by_norm: Dict[str, int] = {}
        current_norm = os.path.normcase(os.path.normpath(current_path)) if current_path else ""
        for label, value in entries:
            combo.addItem(label, value)
            idx = combo.count() - 1
            index_by_norm[os.path.normcase(os.path.normpath(str(value)))] = idx
        if current_norm in index_by_norm:
            combo.setCurrentIndex(index_by_norm[current_norm])
        combo.blockSignals(False)
        return len(entries)

    def _build_dof_transfer_entries(self) -> Tuple[List[Tuple[str, str]], Dict[str, int]]:
        entries: List[Tuple[str, str]] = []
        counts = {"assets": 0, "bundles": 0, "output": 0}
        root = self._get_dof_assets_root()
        if not root or not os.path.isdir(root):
            return entries, counts

        asset_paths = self._scan_dof_anim_assets(root)
        for asset_path in asset_paths:
            try:
                rel = os.path.relpath(asset_path, root).replace("\\", "/")
            except Exception:
                rel = os.path.basename(asset_path)
            entries.append((f"asset/{rel}", str(asset_path)))
            counts["assets"] += 1

        bundle_names = self._get_dof_bundle_anim_names(root)
        for name in bundle_names:
            label = f"bundle://{name}"
            entries.append((label, label))
            counts["bundles"] += 1

        output_root = Path(root) / "Output"
        if output_root.exists():
            for path in self._scan_anim_transfer_files(output_root):
                try:
                    rel = os.path.relpath(path, output_root).replace("\\", "/")
                except Exception:
                    rel = path.name
                entries.append((f"output/{rel}", str(path)))
                counts["output"] += 1

        entries.sort(key=lambda item: item[0].lower())
        return entries, counts

    def _refresh_anim_transfer_source_options(self) -> None:
        if not hasattr(self, "anim_transfer_source_combo"):
            return
        use_dof = self.anim_transfer_source_use_dof.isChecked()
        if use_dof:
            entries, counts = self._build_dof_transfer_entries()
            placeholder = "Set DOF assets root to list animations"
            count = self._populate_anim_transfer_combo_entries(
                self.anim_transfer_source_combo,
                entries,
                self.anim_transfer_source_edit.text().strip(),
                placeholder,
            )
            total = counts["assets"] + counts["bundles"] + counts["output"]
            if total:
                self._log_anim_transfer(
                    f"Source list loaded {total} item(s) (assets {counts['assets']}, bundles {counts['bundles']}, output {counts['output']}).",
                    "INFO",
                )
        else:
            root = self._anim_transfer_list_root(use_dof)
            count = self._populate_anim_transfer_combo(
                self.anim_transfer_source_combo,
                root,
                use_dof,
                self.anim_transfer_source_edit.text().strip(),
            )
            if root:
                self._log_anim_transfer(
                    f"Source list loaded {count} file(s) from {root}.",
                    "INFO" if count else "WARNING",
                )

    def _refresh_anim_transfer_target_options(self) -> None:
        if not hasattr(self, "anim_transfer_target_combo"):
            return
        use_dof = self.anim_transfer_target_use_dof.isChecked()
        if use_dof:
            entries, counts = self._build_dof_transfer_entries()
            placeholder = "Set DOF assets root to list animations"
            count = self._populate_anim_transfer_combo_entries(
                self.anim_transfer_target_combo,
                entries,
                self.anim_transfer_target_edit.text().strip(),
                placeholder,
            )
            total = counts["assets"] + counts["bundles"] + counts["output"]
            if total:
                self._log_anim_transfer(
                    f"Target list loaded {total} item(s) (assets {counts['assets']}, bundles {counts['bundles']}, output {counts['output']}).",
                    "INFO",
                )
        else:
            root = self._anim_transfer_list_root(use_dof)
            count = self._populate_anim_transfer_combo(
                self.anim_transfer_target_combo,
                root,
                use_dof,
                self.anim_transfer_target_edit.text().strip(),
            )
            if root:
                self._log_anim_transfer(
                    f"Target list loaded {count} file(s) from {root}.",
                    "INFO" if count else "WARNING",
                )

    def _apply_anim_transfer_source_pick(self) -> None:
        if not hasattr(self, "anim_transfer_source_combo"):
            return
        path = self.anim_transfer_source_combo.currentData()
        if path:
            self.anim_transfer_source_edit.setText(str(path))
            if self._is_dof_anim_reference(str(path)) and hasattr(self, "dof_deploy_input_edit"):
                self.dof_deploy_input_edit.setText(str(path))

    def _apply_anim_transfer_target_pick(self) -> None:
        if not hasattr(self, "anim_transfer_target_combo"):
            return
        path = self.anim_transfer_target_combo.currentData()
        if path:
            self.anim_transfer_target_edit.setText(str(path))
            if self._is_dof_anim_reference(str(path)) and hasattr(self, "dof_deploy_input_edit"):
                self.dof_deploy_input_edit.setText(str(path))

    def _browse_anim_transfer_source(self) -> None:
        start_dir = Path(self.anim_transfer_source_edit.text() or Path.home())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select Source Animation File",
            str(start_dir),
            "Animation BIN/JSON (*.bin *.json);;All Files (*)",
        )
        if filename:
            self.anim_transfer_source_edit.setText(filename)

    def _browse_anim_transfer_target(self) -> None:
        start_dir = Path(self.anim_transfer_target_edit.text() or Path.home())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select Target Animation File",
            str(start_dir),
            "Animation BIN/JSON (*.bin *.json);;All Files (*)",
        )
        if filename:
            self.anim_transfer_target_edit.setText(filename)

    def _load_anim_transfer_source(self) -> None:
        path = self.anim_transfer_source_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Missing File", "Select a source BIN/JSON file.")
            return
        if self._is_dof_anim_reference(path) and hasattr(self, "dof_deploy_input_edit"):
            self.dof_deploy_input_edit.setText(path)
        payload = self._load_anim_transfer_file(path, self.anim_transfer_source_list, self.anim_transfer_source_label)
        if payload is not None:
            self.anim_transfer_source_payload = payload
            self.anim_transfer_source_path = path

    def _load_anim_transfer_target(self) -> None:
        path = self.anim_transfer_target_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Missing File", "Select a target BIN/JSON file.")
            return
        payload = self._load_anim_transfer_file(path, self.anim_transfer_target_list, self.anim_transfer_target_label)
        if payload is not None:
            self.anim_transfer_target_payload = payload
            self.anim_transfer_target_path = path

    def _load_anim_transfer_file(
        self,
        path: str,
        list_widget: QListWidget,
        status_label: QLabel,
    ) -> Optional[Dict[str, Any]]:
        list_widget.clear()
        if self._is_dof_anim_reference(path):
            payload = self._load_dof_anim_transfer_payload(path)
            if payload is None:
                status_label.setText("Failed to load DOF animation.")
                return None
            self._populate_anim_transfer_list(list_widget, status_label, payload, path, True)
            return payload
        if not os.path.exists(path):
            status_label.setText("File not found.")
            self._log_anim_transfer(f"Missing file: {path}", "ERROR")
            return None
        try:
            payload = self._read_animation_payload(path)
        except Exception as exc:
            status_label.setText("Failed to load.")
            self._log_anim_transfer(f"Failed to load {path}: {exc}", "ERROR")
            return None
        self._populate_anim_transfer_list(list_widget, status_label, payload, path, False)
        return payload

    def _populate_anim_transfer_list(
        self,
        list_widget: QListWidget,
        status_label: QLabel,
        payload: Dict[str, Any],
        path: str,
        is_dof: bool,
    ) -> None:
        anims = payload.get("anims", [])
        if not anims:
            status_label.setText("No animations found.")
            self._log_anim_transfer(f"No animations found in {path}.", "WARNING")
            return
        for anim in anims:
            name = anim.get("name", "Unnamed")
            layer_count = len(anim.get("layers", []))
            list_widget.addItem(f"{name} ({layer_count} layers)")
        status_label.setText(f"Loaded {len(anims)} animation(s).")
        label = "DOF source" if is_dof else "file"
        self._log_anim_transfer(f"Loaded {len(anims)} animation(s) from {label}: {path}.", "INFO")

    def _read_animation_payload(self, path: str) -> Dict[str, Any]:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".json":
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        if ext == ".bin":
            temp_path = None
            try:
                handle, temp_path = tempfile.mkstemp(suffix=".json")
                os.close(handle)
                if not self._convert_bin_to_json_auto(path, temp_path):
                    raise RuntimeError("BIN conversion failed.")
                with open(temp_path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            finally:
                if temp_path and os.path.exists(temp_path):
                    with contextlib.suppress(Exception):
                        os.remove(temp_path)
        raise RuntimeError("Unsupported file type (must be .bin or .json).")

    @staticmethod
    def _is_dof_anim_reference(path: str) -> bool:
        if not path:
            return False
        lowered = path.strip().lower()
        if lowered.startswith("bundle://"):
            return True
        if lowered.endswith(".animbbb") or lowered.endswith(".animbbb.asset"):
            return True
        if os.path.isfile(path):
            return SettingsDialog._is_unity_bundle_file(path)
        return False

    def _load_dof_anim_transfer_payload(self, path: str) -> Optional[Dict[str, Any]]:
        dof_root = self._get_dof_assets_root()
        if not dof_root or not os.path.isdir(dof_root):
            self._log_anim_transfer("DOF assets root is missing or invalid.", "ERROR")
            QMessageBox.warning(self, "Missing DOF Path", "Please set a valid DOF assets root.")
            return None

        temp_root = Path(tempfile.mkdtemp(prefix="dof_transfer_"))
        try:
            if not self._convert_dof_to_json(path, str(temp_root)):
                return None
            json_files = [
                p for p in temp_root.glob("*.json")
                if not p.name.lower().endswith((".layers.json", ".keylayers.json"))
            ]
            if not json_files:
                self._log_anim_transfer("DOF conversion completed but no JSON was produced.", "ERROR")
                return None
            json_path = json_files[0]
            with open(json_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def _apply_anim_transfer(self) -> None:
        if not self.anim_transfer_source_payload or not self.anim_transfer_source_path:
            self._load_anim_transfer_source()
        if not self.anim_transfer_target_payload or not self.anim_transfer_target_path:
            self._load_anim_transfer_target()
        if not self.anim_transfer_source_payload or not self.anim_transfer_target_payload:
            return

        self.anim_transfer_log.clear()
        source_payload = copy.deepcopy(self.anim_transfer_source_payload)
        target_payload = copy.deepcopy(self.anim_transfer_target_payload)

        source_anims = source_payload.get("anims", [])
        if not source_anims:
            self._log_anim_transfer("Source contains no animations.", "ERROR")
            return

        selected_anims = self._select_transfer_anims(source_anims)
        if not selected_anims:
            self._log_anim_transfer("No animations matched the selection filter.", "WARNING")
            return

        renamed_anims = self._apply_anim_renames(selected_anims)
        if renamed_anims is None:
            return

        transformed_anims = self._apply_anim_metadata_transforms(renamed_anims)
        if self.anim_transfer_rev_check.isChecked():
            target_payload["rev"] = int(self.anim_transfer_rev_spin.value())
        if self.anim_transfer_blend_check.isChecked():
            target_payload["blend_version"] = int(self.anim_transfer_blend_spin.value())

        target_anims = target_payload.get("anims", []) or []
        merged_anims = self._merge_animation_lists(target_anims, transformed_anims)
        target_payload["anims"] = merged_anims

        target_path = self.anim_transfer_target_path
        if not target_path:
            self._log_anim_transfer("Target file path is missing.", "ERROR")
            return

        try:
            self._write_anim_transfer_target(target_payload, target_path)
        except Exception as exc:
            self._log_anim_transfer(f"Failed to write target: {exc}", "ERROR")
            QMessageBox.warning(self, "Transfer Failed", f"Could not write target file:\n{exc}")
            return

        self._log_anim_transfer("Transfer complete.", "SUCCESS")
        self._load_anim_transfer_target()

    def _select_transfer_anims(self, anims: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        mode = self.anim_transfer_select_mode.currentData()
        raw_filter = self.anim_transfer_select_input.text().strip()
        if mode == "all" or not raw_filter:
            return [copy.deepcopy(anim) for anim in anims]

        names = [str(anim.get("name", "")) for anim in anims]
        name_map = {name.lower(): idx for idx, name in enumerate(names)}

        if mode == "name":
            tokens = [token.strip() for token in raw_filter.split(",") if token.strip()]
            indices = []
            for token in tokens:
                idx = name_map.get(token.lower())
                if idx is None:
                    self._log_anim_transfer(f"Name not found: {token}", "WARNING")
                    continue
                indices.append(idx)
            return [copy.deepcopy(anims[idx]) for idx in indices]

        if mode == "index":
            indices = self._parse_index_list(raw_filter, len(anims))
            return [copy.deepcopy(anims[idx]) for idx in indices]

        if mode == "wildcard":
            patterns = [token.strip().lower() for token in raw_filter.split(",") if token.strip()]
            matched = []
            for idx, name in enumerate(names):
                lowered = name.lower()
                if any(fnmatch.fnmatchcase(lowered, pattern) for pattern in patterns):
                    matched.append(copy.deepcopy(anims[idx]))
            return matched

        return [copy.deepcopy(anim) for anim in anims]

    def _parse_index_list(self, raw: str, max_len: int) -> List[int]:
        indices: List[int] = []
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        for part in parts:
            if "-" in part:
                start_str, _, end_str = part.partition("-")
                try:
                    start = int(start_str)
                    end = int(end_str)
                except ValueError:
                    self._log_anim_transfer(f"Invalid index range: {part}", "WARNING")
                    continue
                if end < start:
                    start, end = end, start
                for idx in range(start, end + 1):
                    if 0 <= idx < max_len:
                        indices.append(idx)
                    else:
                        self._log_anim_transfer(f"Index out of range: {idx}", "WARNING")
            else:
                try:
                    idx = int(part)
                except ValueError:
                    self._log_anim_transfer(f"Invalid index: {part}", "WARNING")
                    continue
                if 0 <= idx < max_len:
                    indices.append(idx)
                else:
                    self._log_anim_transfer(f"Index out of range: {idx}", "WARNING")
        return indices

    def _apply_anim_renames(self, anims: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        mode = self.anim_transfer_rename_mode.currentData()
        if mode == "none":
            return anims
        find_text = self.anim_transfer_rename_find.text()
        replace_text = self.anim_transfer_rename_replace.text()

        if mode == "regex":
            try:
                pattern = re.compile(find_text)
            except re.error as exc:
                self._log_anim_transfer(f"Invalid regex: {exc}", "ERROR")
                QMessageBox.warning(self, "Invalid Regex", f"Regex error:\n{exc}")
                return None
            for anim in anims:
                name = str(anim.get("name", ""))
                anim["name"] = pattern.sub(replace_text, name)
            return anims

        for anim in anims:
            name = str(anim.get("name", ""))
            if mode == "exact":
                if name == find_text:
                    anim["name"] = replace_text
            elif mode == "prefix":
                if name.startswith(find_text):
                    anim["name"] = replace_text + name[len(find_text):]
            elif mode == "suffix":
                if name.endswith(find_text):
                    anim["name"] = name[:len(name) - len(find_text)] + replace_text
        return anims

    def _apply_anim_metadata_transforms(self, anims: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        set_size = self.anim_transfer_size_check.isChecked()
        width = int(self.anim_transfer_width_spin.value())
        height = int(self.anim_transfer_height_spin.value())
        set_centered = self.anim_transfer_centered_check.isChecked()
        centered_value = int(self.anim_transfer_centered_combo.currentData())
        reindex_layers = self.anim_transfer_reindex_layers.isChecked()
        normalize_src = self.anim_transfer_normalize_src.isChecked()

        for anim in anims:
            if set_size:
                anim["width"] = width
                anim["height"] = height
            if set_centered:
                anim["centered"] = centered_value

            layers = anim.get("layers", []) or []
            if reindex_layers:
                self._reindex_layer_ids(layers)
            if normalize_src:
                self._normalize_layer_src(layers)
        return anims

    def _reindex_layer_ids(self, layers: List[Dict[str, Any]]) -> None:
        mapping: Dict[int, int] = {}
        next_id = 0
        for layer in layers:
            try:
                old_id = int(layer.get("id", next_id))
            except (TypeError, ValueError):
                old_id = next_id
            if old_id not in mapping:
                mapping[old_id] = next_id
                next_id += 1
        for layer in layers:
            try:
                old_id = int(layer.get("id", 0))
            except (TypeError, ValueError):
                old_id = 0
            layer["id"] = mapping.get(old_id, old_id)
            try:
                parent = int(layer.get("parent", -1))
            except (TypeError, ValueError):
                parent = -1
            if parent in mapping:
                layer["parent"] = mapping[parent]

    def _normalize_layer_src(self, layers: List[Dict[str, Any]]) -> None:
        for layer in layers:
            value = layer.get("src", 0)
            normalized = self._normalize_int16(value)
            if normalized is not None:
                layer["src"] = normalized

    def _normalize_int16(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool):
            value = int(value)
        if not isinstance(value, int):
            try:
                value = int(str(value).strip())
            except (TypeError, ValueError):
                return 0
        if -32768 <= value <= 32767:
            return value
        if 32768 <= value <= 65535:
            return value - 65536
        return max(-32768, min(32767, value))

    def _merge_animation_lists(
        self,
        target_anims: List[Dict[str, Any]],
        incoming_anims: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merge_mode = self.anim_transfer_merge_mode.currentData()
        dup_mode = self.anim_transfer_duplicate_mode.currentData()
        order_mode = self.anim_transfer_order_mode.currentData()

        if merge_mode == "replace":
            return incoming_anims

        incoming_by_key = {str(anim.get("name", "")).lower(): anim for anim in incoming_anims}
        target_keys = [str(anim.get("name", "")).lower() for anim in target_anims]
        target_by_key = {key: anim for key, anim in zip(target_keys, target_anims)}

        result: List[Dict[str, Any]] = []

        if order_mode == "keep":
            for key, anim in zip(target_keys, target_anims):
                if key in incoming_by_key:
                    if dup_mode == "overwrite":
                        new_anim = incoming_by_key[key]
                        if self.anim_transfer_preserve_target.isChecked():
                            new_anim = self._preserve_target_settings(anim, new_anim)
                        result.append(new_anim)
                    else:
                        result.append(anim)
                else:
                    result.append(anim)
            for key, anim in incoming_by_key.items():
                if key not in target_by_key:
                    result.append(anim)
            return result

        if order_mode == "append":
            if dup_mode == "overwrite":
                for key, anim in zip(target_keys, target_anims):
                    if key not in incoming_by_key:
                        result.append(anim)
                for key, anim in incoming_by_key.items():
                    if key in target_by_key and self.anim_transfer_preserve_target.isChecked():
                        anim = self._preserve_target_settings(target_by_key[key], anim)
                    result.append(anim)
                return result
            for anim in target_anims:
                result.append(anim)
            for key, anim in incoming_by_key.items():
                if key in target_by_key:
                    continue
                result.append(anim)
            return result

        return target_anims + incoming_anims

    def _preserve_target_settings(self, target: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        preserved = copy.deepcopy(incoming)
        for key in ("width", "height", "centered", "loop_offset"):
            if key in target:
                preserved[key] = target[key]
        if "clone_layers" in target and "clone_layers" in preserved:
            preserved["clone_layers"] = target["clone_layers"]
        return preserved

    def _write_anim_transfer_target(self, payload: Dict[str, Any], target_path: str) -> None:
        ext = os.path.splitext(target_path)[1].lower()
        if ext == ".json":
            with open(target_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            return
        if ext != ".bin":
            raise RuntimeError("Target file must be .bin or .json.")

        rev_value = payload.get("rev")
        try:
            rev_int = int(rev_value)
        except (TypeError, ValueError):
            rev_int = 6
        if rev_int not in (2, 4, 6):
            raise RuntimeError(f"Unsupported target rev {rev_int} for BIN export.")

        handle, temp_path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        try:
            with open(temp_path, "w", encoding="utf-8") as handle_out:
                json.dump(payload, handle_out, indent=2)
            if not self._convert_json_to_bin(temp_path, target_path, rev_int, False):
                raise RuntimeError("BIN export failed.")
        finally:
            if os.path.exists(temp_path):
                with contextlib.suppress(Exception):
                    os.remove(temp_path)

    def _log_dof_deploy(self, message: str, level: str = "INFO") -> None:
        log_widget = None
        if hasattr(self, "dof_deploy_log"):
            log_widget = self.dof_deploy_log
        elif hasattr(self, "anim_transfer_log"):
            log_widget = self.anim_transfer_log
        if not log_widget:
            return
        log_widget.appendPlainText(f"[{level}] {message}")

    def _browse_dof_deploy_input(self) -> None:
        start_dir = (
            self.dof_deploy_input_edit.text().strip()
            or self.dof_deploy_assets_root_edit.text().strip()
            or self.app_settings.value("dof_path", "", type=str)
            or str(Path.home())
        )
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select DOF Animation",
            str(start_dir),
            "DOF Animation (*.ANIMBBB.asset *.ANIMBBB *.animbbb.asset *.animbbb);;All Files (*)",
        )
        if filename:
            self.dof_deploy_input_edit.setText(filename)

    def _browse_dof_deploy_assets_root(self) -> None:
        start_dir = self.dof_deploy_assets_root_edit.text().strip() or self.app_settings.value(
            "dof_path", "", type=str
        )
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select DOF Assets Root",
            start_dir or str(Path.home()),
        )
        if folder:
            self.dof_deploy_assets_root_edit.setText(folder)
            if hasattr(self, "dof_assets_root_edit"):
                self.dof_assets_root_edit.setText(folder)
            self.app_settings.setValue("dof_path", folder)

    def _browse_dof_deploy_game_root(self) -> None:
        start_dir = self.dof_deploy_game_root_edit.text().strip()
        if not start_dir and self.game_path:
            start_dir = str(self.game_path)
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Game Root",
            start_dir or str(Path.home()),
        )
        if folder:
            self.dof_deploy_game_root_edit.setText(folder)

    def _browse_dof_deploy_existing_target(self) -> None:
        start_dir = self.dof_deploy_game_root_edit.text().strip() or str(Path.home())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select Existing Target File",
            start_dir,
            "BIN/XML/PNG (*.bin *.xml *.png);;All Files (*)",
        )
        if not filename:
            return
        target_path = Path(filename)
        self.dof_deploy_existing_edit.setText(str(target_path))

        ext = target_path.suffix.lower()
        if ext == ".bin":
            self.dof_deploy_bin_name_edit.setText(target_path.name)
        elif ext == ".xml":
            self.dof_deploy_xml_name_edit.setText(target_path.name)
            try:
                tree = ET.parse(target_path)
                root = tree.getroot()
                image_path = root.attrib.get("imagePath", "")
                if image_path:
                    self.dof_deploy_png_name_edit.setText(os.path.basename(image_path))
            except Exception:
                pass
        elif ext == ".png":
            self.dof_deploy_png_name_edit.setText(target_path.name)

        data_root = self._guess_data_root_from_path(target_path)
        if data_root:
            self.dof_deploy_game_root_edit.setText(str(data_root))

    @staticmethod
    def _guess_data_root_from_path(path: Path) -> Optional[Path]:
        for parent in [path.parent] + list(path.parents):
            if parent.name.lower() == "data":
                return parent
            if (parent / "xml_bin").exists() or (parent / "xml_resources").exists():
                return parent
        return None

    @staticmethod
    def _normalize_export_name(name: str, ext: str) -> str:
        cleaned = (name or "").strip()
        if not cleaned:
            return ""
        cleaned = os.path.basename(cleaned.replace("\\", "/"))
        if not cleaned.lower().endswith(ext.lower()):
            cleaned = f"{cleaned}{ext}"
        return cleaned

    @staticmethod
    def _resolve_game_data_root(path: str) -> Optional[Path]:
        if not path:
            return None
        candidate = Path(path)
        if candidate.is_file():
            candidate = candidate.parent
        for parent in [candidate] + list(candidate.parents):
            if parent.name.lower() == "data":
                return parent
        if (candidate / "data").is_dir():
            return candidate / "data"
        if (candidate / "xml_bin").exists() or (candidate / "xml_resources").exists():
            return candidate
        return candidate

    @staticmethod
    def _backup_existing(path: Path) -> Optional[Path]:
        if not path.exists():
            return None
        backup_path = Path(f"{path}.bak")
        counter = 1
        while backup_path.exists():
            backup_path = Path(f"{path}.bak{counter}")
            counter += 1
        shutil.copy2(path, backup_path)
        return backup_path

    @staticmethod
    def _rewrite_xml_image_path(xml_path: Path, image_path: str) -> None:
        try:
            text = xml_path.read_text(encoding="utf-8")
        except Exception:
            text = ""
        if text:
            updated, count = re.subn(
                r'imagePath="[^"]*"',
                f'imagePath="{image_path}"',
                text,
                count=1,
            )
            if count:
                xml_path.write_text(updated, encoding="utf-8")
                return
        tree = ET.parse(xml_path)
        root = tree.getroot()
        root.set("imagePath", image_path)
        tree.write(xml_path, encoding="utf-8")

    @staticmethod
    def _rewrite_json_sources(json_path: Path, xml_name: str) -> None:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        sources = payload.get("sources", [])
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict):
                    source["src"] = xml_name
        payload["sources"] = sources
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _run_dof_deploy(self) -> None:
        log_widget = None
        if hasattr(self, "dof_deploy_log"):
            log_widget = self.dof_deploy_log
        elif hasattr(self, "anim_transfer_log"):
            log_widget = self.anim_transfer_log
        if log_widget:
            log_widget.clear()
        if not self._dof_converter_path or not os.path.exists(self._dof_converter_path):
            self._log_dof_deploy("DOF converter script is not available.", "ERROR")
            QMessageBox.warning(self, "Missing Converter", "DOF converter script was not found.")
            return

        input_path = self.dof_deploy_input_edit.text().strip()
        if not input_path:
            self._log_dof_deploy("Select a DOF input (.ANIMBBB.asset or bundle://...).", "ERROR")
            QMessageBox.warning(self, "Missing Input", "Select a DOF input to deploy.")
            return

        assets_root = self.dof_deploy_assets_root_edit.text().strip() or self._get_dof_assets_root()
        if not assets_root or not os.path.isdir(assets_root):
            self._log_dof_deploy("DOF assets root is missing or invalid.", "ERROR")
            QMessageBox.warning(self, "Missing DOF Path", "Please set a valid DOF assets root.")
            return

        game_root = self.dof_deploy_game_root_edit.text().strip()
        if not game_root and self.game_path:
            game_root = str(self.game_path)
        if not game_root and self._game_path_str:
            game_root = self._game_path_str
        if not game_root:
            self._log_dof_deploy("Game root is missing.", "ERROR")
            QMessageBox.warning(self, "Missing Game Path", "Please select a game root folder.")
            return

        data_root = self._resolve_game_data_root(game_root)
        if not data_root:
            self._log_dof_deploy("Could not resolve a data/ folder from the game root.", "ERROR")
            QMessageBox.warning(self, "Invalid Game Path", "Could not find the game data folder.")
            return

        is_bundle_ref = input_path.lower().startswith("bundle://")
        is_bundle_file = os.path.isfile(input_path) and self._is_unity_bundle_file(input_path)
        bundle_anim = None
        bundle_root = None

        if is_bundle_ref:
            bundle_anim = input_path.split("bundle://", 1)[1].strip()
            if not bundle_anim:
                self._log_dof_deploy("Bundle ANIMBBB name is missing.", "ERROR")
                QMessageBox.warning(self, "Missing Bundle Name", "Bundle input requires a name.")
                return
            bundle_root = assets_root
        elif is_bundle_file:
            bundle_anim = getattr(self, "dof_bundle_anim_edit", None)
            bundle_anim = bundle_anim.text().strip() if bundle_anim else ""
            if not bundle_anim:
                self._log_dof_deploy("Bundle ANIMBBB name is required for __data inputs.", "ERROR")
                QMessageBox.warning(
                    self,
                    "Missing Bundle Name",
                    "Enter the ANIMBBB name (e.g., A_tweedle_adult_03_cloud_01.ANIMBBB).",
                )
                return
            bundle_root = input_path
        elif not os.path.exists(input_path):
            self._log_dof_deploy(f"Input file not found: {input_path}", "ERROR")
            QMessageBox.warning(self, "Missing Input", "Selected DOF input was not found.")
            return

        temp_root = Path(tempfile.mkdtemp(prefix="dof_deploy_"))
        output_dir = temp_root / "xml_resources"
        output_dir.mkdir(parents=True, exist_ok=True)
        cleanup_temp = True

        cmd_input = f"bundle://{bundle_anim}" if bundle_anim else input_path
        cmd = self._build_python_command(self._dof_converter_path) + [
            cmd_input,
            "--output",
            str(output_dir),
        ]
        if bundle_anim:
            cmd += ["--bundle-root", str(bundle_root)]
        else:
            cmd += ["--assets-root", assets_root]

        if getattr(self, "dof_mesh_pivot_checkbox", None) and self.dof_mesh_pivot_checkbox.isChecked():
            cmd.append("--mesh-pivot-local")
        if (
            getattr(self, "dof_include_mesh_xml_checkbox", None)
            and self.dof_include_mesh_xml_checkbox.isChecked()
        ):
            cmd.append("--include-mesh-xml")
        if (
            getattr(self, "dof_premultiply_alpha_checkbox", None)
            and self.dof_premultiply_alpha_checkbox.isChecked()
        ):
            cmd.append("--premultiply-alpha")
        alpha_hardness = 0.0
        if hasattr(self, "dof_alpha_hardness_spin"):
            try:
                alpha_hardness = float(self.dof_alpha_hardness_spin.value())
            except (TypeError, ValueError):
                alpha_hardness = 0.0
        alpha_hardness = max(0.0, min(2.0, alpha_hardness))
        if alpha_hardness > 1e-6:
            cmd += ["--alpha-hardness", f"{alpha_hardness:.3f}"]
        if hasattr(self, "dof_hires_xml_checkbox"):
            cmd.append("--hires-xml" if self.dof_hires_xml_checkbox.isChecked() else "--no-hires-xml")
        if getattr(self, "dof_swap_anchor_report_checkbox", None) and self.dof_swap_anchor_report_checkbox.isChecked():
            cmd.append("--swap-anchor-report")
        if getattr(self, "dof_swap_anchor_edge_align_checkbox", None) and self.dof_swap_anchor_edge_align_checkbox.isChecked():
            cmd.append("--swap-anchor-edge-align")
        if getattr(self, "dof_swap_anchor_pivot_offset_checkbox", None) and self.dof_swap_anchor_pivot_offset_checkbox.isChecked():
            cmd.append("--swap-anchor-pivot-offset")
        if getattr(self, "dof_swap_anchor_report_override_checkbox", None) and self.dof_swap_anchor_report_override_checkbox.isChecked():
            cmd.append("--swap-anchor-report-override")

        self._log_dof_deploy("Running DOF converter...", "INFO")
        result = self._run_converter_command(cmd, os.path.dirname(self._dof_converter_path))
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip() or "Unknown error"
            self._log_dof_deploy(f"DOF converter failed: {error_text}", "ERROR")
            cleanup_temp = False
            QMessageBox.warning(self, "Conversion Failed", error_text)
            return
        stdout = (result.stdout or "").strip()
        if stdout:
            self._log_dof_deploy(stdout, "INFO")

        try:
            json_files = sorted(output_dir.glob("*.json"))
            xml_files = sorted(output_dir.glob("*.xml"))
            if not json_files:
                raise RuntimeError("No JSON output produced.")
            if not xml_files:
                raise RuntimeError("No XML output produced.")

            json_path = json_files[0]
            xml_path = xml_files[0]
            if len(json_files) > 1:
                self._log_dof_deploy("Multiple JSON outputs found; using the first.", "WARNING")
            if len(xml_files) > 1:
                self._log_dof_deploy("Multiple XML outputs found; using the first.", "WARNING")

            png_candidates = list((temp_root / "gfx" / "monsters").glob("*.png"))
            if not png_candidates:
                png_candidates = list(output_dir.glob("*.png"))
            if not png_candidates:
                raise RuntimeError("No PNG atlas output produced.")
            xml_base = xml_path.stem.lower()
            png_path = next((p for p in png_candidates if p.stem.lower() == xml_base), png_candidates[0])

            xml_name = self._normalize_export_name(
                self.dof_deploy_xml_name_edit.text(), ".xml"
            ) or xml_path.name
            png_name = self._normalize_export_name(
                self.dof_deploy_png_name_edit.text(), ".png"
            ) or png_path.name
            bin_name = self._normalize_export_name(
                self.dof_deploy_bin_name_edit.text(), ".bin"
            ) or f"{json_path.stem}.bin"

            xml_dir = data_root / "xml_resources"
            bin_dir = data_root / "xml_bin"
            png_dir = data_root / "gfx" / "monsters"
            xml_dir.mkdir(parents=True, exist_ok=True)
            bin_dir.mkdir(parents=True, exist_ok=True)
            png_dir.mkdir(parents=True, exist_ok=True)

            image_path = f"gfx/monsters/{png_name}"
            self._rewrite_xml_image_path(xml_path, image_path)
            self._rewrite_json_sources(json_path, xml_name)

            xml_target = xml_dir / xml_name
            png_target = png_dir / png_name
            bin_target = bin_dir / bin_name

            replace_without_backup = self.dof_deploy_replace_check.isChecked()
            if not replace_without_backup:
                for target in (xml_target, png_target, bin_target):
                    backup = self._backup_existing(target)
                    if backup:
                        self._log_dof_deploy(f"Backup created: {backup}", "INFO")

            shutil.copy2(xml_path, xml_target)
            shutil.copy2(png_path, png_target)

            if not self._convert_json_to_bin(str(json_path), str(bin_target), 6, False):
                raise RuntimeError("BIN export failed.")

            self._log_dof_deploy("DOF deploy complete.", "SUCCESS")
            self._log_dof_deploy(f"XML: {xml_target}", "INFO")
            self._log_dof_deploy(f"PNG: {png_target}", "INFO")
            self._log_dof_deploy(f"BIN: {bin_target}", "INFO")
        except Exception as exc:
            cleanup_temp = False
            self._log_dof_deploy(f"Deploy failed: {exc}", "ERROR")
            QMessageBox.warning(self, "Deploy Failed", str(exc))
        finally:
            if cleanup_temp:
                shutil.rmtree(temp_root, ignore_errors=True)
            else:
                self._log_dof_deploy(f"Temp output kept at: {temp_root}", "INFO")
