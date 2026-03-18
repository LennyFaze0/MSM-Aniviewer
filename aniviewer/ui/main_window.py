"""
Main Window
The main application window that ties everything together
"""

import sys
import os
import re
import json
import math
import hashlib
import subprocess
import tempfile
import shutil
import importlib
import importlib.util
import types
import faulthandler
import copy
import difflib
import struct
import random
import contextlib
import io
import runpy
import xml.etree.ElementTree as ET
import zlib
from glob import glob
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple, Any

import numpy as np
from dataclasses import dataclass, replace
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox,
    QSplitter, QProgressDialog, QDialog, QInputDialog, QToolButton,
    QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox
)
from PyQt6.QtCore import Qt, QSettings, QTimer, QEvent, QProcess
from PyQt6.QtGui import QSurfaceFormat, QColor, QShortcut, QKeySequence, QPixmap, QImage, QIcon, QCursor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect
from PIL import Image, ImageChops, ImageDraw
from OpenGL.GL import *
import soundfile as sf

from core.data_structures import AnimationData, LayerData, KeyframeData, KeyframeLane, SpriteInfo
from core.constraints import ConstraintManager, ConstraintSpec
from core.animation_player import AnimationPlayer
from core.texture_atlas import TextureAtlas
from core.island_tiles import (
    TilesetData,
    TileGridData,
    TileGridHeader,
    TileGridEntry,
    parse_tileset_file,
    parse_tile_grid_file,
)
from core.audio_manager import AudioManager
from core.metronome import Metronome
from utils.buddy_manifest import BuddyManifest
from renderer.opengl_widget import (
    OpenGLAnimationWidget,
    TileBatch,
    TileInstance,
    TerrainComposite,
    ParticleRenderEntry,
)
from renderer.sprite_renderer import BlendMode
from .log_widget import LogWidget
from .timeline import TimelineWidget, TimelineLaneKey, TimelineGroupSpec, TimelineLaneSpec
from .control_panel import ControlPanel
from .layer_panel import LayerPanel
from .settings_dialog import SettingsDialog, ExportSettings
from .monster_browser_dialog import (
    MonsterBrowserDialog,
    MonsterBrowserEntry,
    MonsterVariantOption,
)
from .sprite_workshop_dialog import SpriteWorkshopDialog
from .midi_editor_dialog import MidiEditorDialog
from .sprite_picker_dialog import SpritePickerDialog
from .constraints_dialog import ConstraintEditorDialog
from utils.diagnostics import DiagnosticsManager, DiagnosticsConfig
from utils.ffmpeg_installer import resolve_ffmpeg_path, query_ffmpeg_encoders
from utils.pytoshop_installer import PytoshopInstaller, PythonPackageInstaller
from utils.ae_rig_exporter import AERigExporter
from utils.shader_registry import ShaderRegistry
from utils.dof_particles import (
    extract_particle_nodes,
    extract_control_points,
    extract_source_nodes,
    build_particle_library,
    DofParticleLibrary,
    DofControlPoint,
    DofAnimNode,
)
from utils.midi_utils import read_midi_file
from utils.keybinds import keybind_actions, default_keybinds, normalize_keybind_sequence

try:
    import UnityPy  # type: ignore
except Exception:
    UnityPy = None


def _detect_project_root() -> Path:
    """Return the runtime project root regardless of whether we are frozen."""
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        candidate = bundle_root / "aniviewer"
        if candidate.exists():
            return candidate
        return bundle_root
    return Path(__file__).resolve().parent.parent


@dataclass
class SpriteReplacementRecord:
    """Tracks a custom sprite override applied in the Sprite Workshop."""
    atlas_key: str
    sprite_name: str
    source_path: Optional[str]
    applied_at: str
from Resources.bin2json.parse_costume_bin import parse_costume_file


@dataclass
class AnimationFileEntry:
    """Metadata for indexed BIN/JSON files."""
    name: str
    relative_path: str
    full_path: str

    @property
    def is_json(self) -> bool:
        return self.full_path.lower().endswith('.json')

    @property
    def is_bin(self) -> bool:
        return self.full_path.lower().endswith('.bin')

    def normalized_path(self) -> str:
        """Return a normalized absolute path for quick comparisons."""
        return os.path.normcase(os.path.normpath(self.full_path))


@dataclass
class MonsterFileRecord:
    """Aggregated BIN/JSON paths for a specific monster stem (e.g., monster_bowgart_fire)."""

    stem: str
    relative_path: str
    json_path: Optional[str] = None
    bin_path: Optional[str] = None

    def has_source(self) -> bool:
        return bool(self.json_path or self.bin_path)


@dataclass
class CostumeEntry:
    """Metadata for a costume definition and its backing files."""
    key: str
    display_name: str
    bin_path: Optional[str] = None
    json_path: Optional[str] = None
    bin_priority: int = 1_000_000
    json_priority: int = 1_000_000
    legacy_sheet_path: Optional[str] = None
    legacy_source_name: Optional[str] = None

    @property
    def source_path(self) -> Optional[str]:
        return self.json_path or self.bin_path


class MSMAnimationViewer(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()

        self.project_root = _detect_project_root()
        self._faulthandler_stream = None
        if sys.stderr is not None:
            faulthandler.enable()
        else:
            crash_log = self.project_root / "msm_viewer_crash.log"
            try:
                crash_log.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                crash_log = Path.cwd() / "msm_viewer_crash.log"
            try:
                self._faulthandler_stream = crash_log.open("w", buffering=1)
                faulthandler.enable(self._faulthandler_stream)
            except Exception:
                # Fallback to default behavior; if it still fails the viewer can continue without faulthandler.
                pass
        self.settings = QSettings('MSMAnimationViewer', 'Settings')
        self.shader_registry = ShaderRegistry(self.project_root)
        shader_blob = self.settings.value('shaders/overrides', '', type=str)
        shader_overrides: Dict[str, Any] = {}
        if shader_blob:
            try:
                shader_overrides = json.loads(shader_blob)
            except (TypeError, ValueError):
                shader_overrides = {}
        self.shader_registry.set_user_overrides(shader_overrides)
        self.game_path: str = self.settings.value('game_path', '')
        self.downloads_path: str = self.settings.value('downloads_path', '')
        self.dof_path: str = self.settings.value('dof_path', '')
        self.dof_search_enabled: bool = bool(self.settings.value('dof/search_enabled', False, type=bool))
        self.dof_particles_world_space: bool = bool(
            self.settings.value('dof/particles_world_space', True, type=bool)
        )
        self.dof_particle_viewport_cap: int = max(
            0,
            min(1000000, int(self.settings.value('dof/viewport_particle_cap', 1000, type=int))),
        )
        self.dof_particle_distance_sensitivity: float = max(
            0.0,
            float(self.settings.value('dof/particle_distance_sensitivity', 0.5, type=float)),
        )
        self.dof_alpha_edge_smoothing_enabled: bool = bool(
            self.settings.value('dof/alpha_edge_smoothing_enabled', False, type=bool)
        )
        self.dof_alpha_edge_smoothing_strength: float = max(
            0.0,
            min(
                1.0,
                float(self.settings.value('dof/alpha_edge_smoothing_strength', 0.5, type=float)),
            ),
        )
        dof_alpha_mode_value = (
            self.settings.value('dof/alpha_edge_smoothing_mode', 'normal', type=str) or 'normal'
        )
        dof_alpha_mode = str(dof_alpha_mode_value).strip().lower()
        if dof_alpha_mode not in {'normal', 'strong'}:
            dof_alpha_mode = 'normal'
        self.dof_alpha_edge_smoothing_mode: str = dof_alpha_mode
        dof_shader_mode_value = (
            self.settings.value('dof/sprite_shader_mode', 'auto', type=str) or 'auto'
        )
        dof_shader_mode = str(dof_shader_mode_value).strip().lower()
        if dof_shader_mode not in {
            'auto',
            'anim2d',
            'dawnoffire_unlit',
            'sprites_default',
            'unlit_transparent',
            'unlit_transparent_masked',
        }:
            dof_shader_mode = 'auto'
        self.dof_sprite_shader_mode: str = dof_shader_mode
        self.viewport_post_aa_enabled: bool = bool(
            self.settings.value('viewport/post_aa_enabled', False, type=bool)
        )
        self.viewport_post_aa_strength: float = max(
            0.0,
            min(
                1.0,
                float(self.settings.value('viewport/post_aa_strength', 0.5, type=float)),
            ),
        )
        viewport_post_aa_mode = (
            self.settings.value('viewport/post_aa_mode', 'fxaa', type=str) or 'fxaa'
        )
        viewport_post_aa_mode = str(viewport_post_aa_mode).strip().lower()
        if viewport_post_aa_mode not in {'fxaa', 'smaa'}:
            viewport_post_aa_mode = 'fxaa'
        self.viewport_post_aa_mode: str = viewport_post_aa_mode
        self.viewport_post_motion_blur_enabled: bool = bool(
            self.settings.value('viewport/post_motion_blur_enabled', False, type=bool)
        )
        self.viewport_post_motion_blur_strength: float = max(
            0.0,
            min(
                1.0,
                float(self.settings.value('viewport/post_motion_blur_strength', 0.35, type=float)),
            ),
        )
        self.viewport_post_bloom_enabled: bool = bool(
            self.settings.value('viewport/post_bloom_enabled', False, type=bool)
        )
        self.viewport_post_bloom_strength: float = max(
            0.0,
            min(
                2.0,
                float(self.settings.value('viewport/post_bloom_strength', 0.15, type=float)),
            ),
        )
        self.viewport_post_bloom_threshold: float = max(
            0.0,
            min(
                2.0,
                float(self.settings.value('viewport/post_bloom_threshold', 0.6, type=float)),
            ),
        )
        self.viewport_post_bloom_radius: float = max(
            0.1,
            min(
                8.0,
                float(self.settings.value('viewport/post_bloom_radius', 1.5, type=float)),
            ),
        )
        self.viewport_post_vignette_enabled: bool = bool(
            self.settings.value('viewport/post_vignette_enabled', False, type=bool)
        )
        self.viewport_post_vignette_strength: float = max(
            0.0,
            min(
                1.0,
                float(self.settings.value('viewport/post_vignette_strength', 0.25, type=float)),
            ),
        )
        self.viewport_post_grain_enabled: bool = bool(
            self.settings.value('viewport/post_grain_enabled', False, type=bool)
        )
        self.viewport_post_grain_strength: float = max(
            0.0,
            min(
                1.0,
                float(self.settings.value('viewport/post_grain_strength', 0.2, type=float)),
            ),
        )
        self.viewport_post_ca_enabled: bool = bool(
            self.settings.value('viewport/post_ca_enabled', False, type=bool)
        )
        self.viewport_post_ca_strength: float = max(
            0.0,
            min(
                1.0,
                float(self.settings.value('viewport/post_ca_strength', 0.25, type=float)),
            ),
        )
        self.shader_registry.set_game_path(self.game_path or None)
        self.sync_audio_to_bpm: bool = True
        self.pitch_shift_enabled: bool = False
        self.chipmunk_mode: bool = False
        self.metronome_enabled: bool = bool(self.settings.value('metronome/enabled', False, type=bool))
        self.metronome_audible: bool = bool(self.settings.value('metronome/audible', True, type=bool))
        ts_num = self.settings.value('metronome/time_signature_numerator', 4, type=int)
        ts_denom = self.settings.value('metronome/time_signature_denom', 4, type=int)
        self.time_signature_num, self.time_signature_denom = self._sanitize_time_signature(ts_num, ts_denom)
        self.show_beat_grid: bool = bool(self.settings.value('timeline/show_beat_grid', False, type=bool))
        self.allow_beat_edit: bool = bool(self.settings.value('timeline/allow_beat_edit', False, type=bool))
        self._load_audio_preferences_from_storage()
        self.bin2json_path: str = ""
        self.legacy_tokenizer_path: str = ""
        self.legacy_bin2json_path: Optional[str] = ""
        self.choir_bin2json_path: Optional[str] = ""
        self.muppets_bin2json_path: Optional[str] = ""
        self.oldest_bin2json_path: Optional[str] = ""
        self.composer_bin2json_path: Optional[str] = ""
        self.rev4_bin2json_path: Optional[str] = ""
        self.rev2_bin2json_path: Optional[str] = ""
        self.dof_anim_to_json_path: Optional[str] = ""
        self._dof_convert_process: Optional[QProcess] = None
        self._dof_convert_asset_path: Optional[str] = None
        self._dof_convert_output_json: Optional[str] = None
        self._dof_convert_stdout_buffer: str = ""
        self._dof_convert_stderr_buffer: str = ""
        self._dof_bundle_mode: bool = False
        self._dof_bundle_cache_path: Optional[str] = None
        self._dof_bundle_anim_cache: List[str] = []
        self._dof_particle_library_root: Optional[str] = None
        self._dof_particle_library: Optional[DofParticleLibrary] = None
        self._dof_particle_entry_cache: Dict[Tuple[str, str], List[ParticleRenderEntry]] = {}
        self._dof_control_point_cache: Dict[Tuple[str, str], Dict[str, DofControlPoint]] = {}
        self._dof_source_node_cache: Dict[Tuple[str, str], Dict[str, DofAnimNode]] = {}
        # When packaged as an EXE, repeated QMessageBox warnings for failed bin parses
        # become disruptive. Suppress those modals in frozen builds and rely on the log.
        self._suppress_bin_error_popups: bool = bool(getattr(sys, "frozen", False))
        
        self.current_json_data: Optional[Dict] = None
        self.original_json_data: Optional[Dict] = None
        self.current_blend_version: int = 1
        self.current_animation_index: int = 0
        self.file_index: List[AnimationFileEntry] = []
        self.filtered_file_index: List[AnimationFileEntry] = []
        self.dof_file_index: List[AnimationFileEntry] = []
        self.filtered_dof_file_index: List[AnimationFileEntry] = []
        self.monster_file_lookup: Dict[str, MonsterFileRecord] = {}
        self._monster_name_rosetta_path: Path = self.project_root / "docs" / "monster_name_file_rosetta.txt"
        self._monster_name_metadata_path: Path = self.project_root / "docs" / "monster_name_file_rosetta_metadata.txt"
        self._monster_name_rosetta_mtime: Optional[float] = None
        self._monster_name_metadata_mtime: Optional[float] = None
        self._monster_common_name_by_stem: Dict[str, str] = {}
        self._monster_resolution_meta_by_stem: Dict[str, Dict[str, Any]] = {}
        self._island_name_by_id: Dict[int, str] = {}
        self.current_search_text: str = ""
        self._downloads_xml_bin_roots: List[str] = []
        
        # Export settings
        self.export_settings = ExportSettings()
        self.anchor_debug_enabled: bool = bool(self.export_settings.anchor_debug_logging)
        self.constraints_enabled: bool = bool(self.settings.value('constraints/enabled', True, type=bool))
        self.constraints: List[ConstraintSpec] = self._load_constraints_from_settings()
        self.constraint_manager = ConstraintManager()
        self.constraint_manager.enabled = self.constraints_enabled
        self.constraint_manager.set_constraints(self.constraints)
        self.constraint_manager.disabled_layer_names = set(
            self._load_constraint_layer_disables()
        )
        self.joint_solver_enabled: bool = bool(self.settings.value('joint_solver/enabled', False, type=bool))
        self.joint_solver_iterations: int = int(self.settings.value('joint_solver/iterations', 8, type=int))
        self.joint_solver_strength: float = float(self.settings.value('joint_solver/strength', 1.0, type=float))
        self.joint_solver_parented: bool = bool(self.settings.value('joint_solver/parented', True, type=bool))
        self.propagate_user_transforms: bool = bool(
            self.settings.value('pose/propagate_user_transforms', True, type=bool)
        )
        self.preserve_children_on_record: bool = bool(
            self.settings.value('pose/preserve_children_on_record', True, type=bool)
        )
        self._dof_anchor_override_active: bool = False
        self._last_non_dof_anchor_flips: Tuple[bool, bool] = (False, False)
        self.audio_library: Dict[str, List[str]] = {}
        self.dof_audio_library: Dict[str, List[str]] = {}
        self._game_music_dirs: List[str] = []
        self._dof_music_dirs: List[str] = []
        self._dof_bundle_audio_cache: Dict[str, str] = {}
        self._dof_anim_bundle_cache: Dict[str, str] = {}
        self._x18_mix_audio_cache: Dict[str, str] = {}
        self._special_audio_mix_cache: Dict[str, str] = {}
        self._audio_track_mutes_by_scope: Dict[str, Set[str]] = {}
        self._current_audio_track_scope: Optional[str] = None
        self._current_audio_track_defs: List[Tuple[str, str]] = []
        self.current_audio_path: Optional[str] = None
        self.current_animation_name: Optional[str] = None
        self.current_animation_revision: Optional[int] = None
        self.current_json_path: Optional[str] = None
        self.animation_time_scale: float = 1.0
        self.current_audio_from_manifest: bool = False
        self.legacy_animation_active: bool = False
        self.active_legacy_sheet_key: Optional[str] = None
        self.legacy_sheet_overrides: Dict[str, str] = {}
        self.costume_entries: List["CostumeEntry"] = []
        self.costume_entry_map: Dict[str, "CostumeEntry"] = {}
        self.costume_cache: Dict[str, Dict[str, Any]] = {}
        self.attachment_animation_cache: Dict[str, Dict[str, Any]] = {}
        self.canonical_clone_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.current_animation_embedded_clones: Optional[List[Dict[str, Any]]] = None
        self.canonical_layer_names: Set[str] = set()
        self.active_costume_key: Optional[str] = None
        self.base_layer_cache: Optional[List[LayerData]] = None
        self.base_texture_atlases: List[TextureAtlas] = []
        self.costume_atlas_cache: Dict[str, TextureAtlas] = {}
        self.active_costume_attachments: List[Dict[str, Any]] = []
        self.costume_sheet_aliases: Dict[str, List[str]] = {}
        self.current_base_bpm: float = 120.0
        self.current_bpm: float = 120.0
        self.animation_bpm_overrides: Dict[str, float] = {}
        self.monster_base_bpm_overrides: Dict[str, float] = {}
        self._audio_loop_multiplier: int = 1
        self._audio_loop_index: int = 0
        self._load_base_bpm_overrides()
        self.layer_visibility_cache: Dict[str, Dict[int, bool]] = {}
        self._default_layer_order: List[int] = []
        self._default_layer_visibility: Dict[int, bool] = {}
        self._default_hidden_layer_ids: Set[int] = set()
        stored_pose_mode = self.settings.value('pose/influence_mode', 'current', type=str) or 'current'
        if not isinstance(stored_pose_mode, str):
            stored_pose_mode = 'current'
        stored_pose_mode = stored_pose_mode.lower()
        if stored_pose_mode not in {"current", "forward"}:
            stored_pose_mode = "current"
        self.pose_influence_mode: str = stored_pose_mode
        self.layer_source_lookup: Dict[int, Dict[str, Any]] = {}
        self.source_atlas_lookup: Dict[Any, TextureAtlas] = {}
        self._pose_baseline_player: Optional[AnimationPlayer] = None
        self._pose_baseline_lookup: Dict[int, LayerData] = {}
        self._history_stack: List[Dict[str, Any]] = []
        self._history_redo_stack: List[Dict[str, Any]] = []
        self._pending_keyframe_action: Optional[Dict[str, Any]] = None
        self._timeline_user_scrubbing: bool = False
        self._resume_audio_after_scrub: bool = False
        self.solid_bg_enabled: bool = self.settings.value('export/solid_bg_enabled', False, type=bool)
        solid_bg_hex = (
            self.settings.value('viewport/background_color', '', type=str)
            or self.settings.value('export/solid_bg_color', '#000000FF', type=str)
            or '#000000FF'
        )
        self.solid_bg_color: Tuple[int, int, int, int] = self._parse_rgba_hex(solid_bg_hex, (0, 0, 0, 255))
        self.viewport_bg_color_mode: str = self._normalize_viewport_bg_color_mode(
            self.settings.value('viewport/background_color_mode', 'none', type=str)
        )
        self.export_include_viewport_background: bool = self.settings.value(
            'export/include_viewport_background',
            False,
            type=bool,
        )
        self.viewport_bg_enabled: bool = self.settings.value(
            'viewport/background_enabled',
            True,
            type=bool,
        )
        default_viewport_bg_asset = self._default_viewport_background_asset_path()
        stored_viewport_bg_path = (
            self.settings.value('viewport/background_image_path', '', type=str) or ''
        ).strip()
        auto_applied_default_bg = False
        if default_viewport_bg_asset and (
            not stored_viewport_bg_path
            or not os.path.isfile(stored_viewport_bg_path)
        ):
            self.viewport_bg_image_path = default_viewport_bg_asset
            auto_applied_default_bg = True
        else:
            self.viewport_bg_image_path = stored_viewport_bg_path
        default_viewport_bg_image_enabled = bool(default_viewport_bg_asset)
        if self.settings.contains('viewport/background_image_enabled'):
            self.viewport_bg_image_enabled = self.settings.value(
                'viewport/background_image_enabled',
                default_viewport_bg_image_enabled,
                type=bool,
            )
            if auto_applied_default_bg and not self.viewport_bg_image_enabled:
                self.viewport_bg_image_enabled = True
        else:
            self.viewport_bg_image_enabled = default_viewport_bg_image_enabled
        if auto_applied_default_bg:
            self.settings.setValue('viewport/background_image_path', self.viewport_bg_image_path)
            self.settings.setValue('viewport/background_image_enabled', self.viewport_bg_image_enabled)
        self.viewport_bg_keep_aspect: bool = self.settings.value(
            'viewport/background_keep_aspect',
            True,
            type=bool,
        )
        self.viewport_bg_zoom_fill: bool = self.settings.value(
            'viewport/background_zoom_fill',
            False,
            type=bool,
        )
        self.viewport_bg_parallax_enabled: bool = self.settings.value(
            'viewport/background_parallax_enabled',
            True,
            type=bool,
        )
        self.viewport_bg_parallax_zoom_strength: float = float(
            self.settings.value(
                'viewport/background_parallax_zoom_strength',
                0.5,
                type=float,
            )
        )
        self.viewport_bg_parallax_pan_strength: float = float(
            self.settings.value(
                'viewport/background_parallax_pan_strength',
                0.5,
                type=float,
            )
        )
        self.viewport_bg_flip_h: bool = self.settings.value(
            'viewport/background_flip_h',
            False,
            type=bool,
        )
        self.viewport_bg_flip_v: bool = self.settings.value(
            'viewport/background_flip_v',
            False,
            type=bool,
        )
        self._layer_thumbnail_cache: Dict[object, Optional[QPixmap]] = {}
        self._atlas_image_cache: Dict[str, Optional[Image.Image]] = {}
        self._layer_sprite_preview_state: Dict[int, Tuple[str, int, int, int, int]] = {}
        self._selected_marker_refs: Set[Tuple["TimelineLaneKey", float]] = set()
        self._atlas_original_image_cache: Dict[str, Optional[Image.Image]] = {}
        self._atlas_modified_images: Dict[str, Image.Image] = {}
        self._sprite_replacements: Dict[Tuple[str, str], SpriteReplacementRecord] = {}
        self._atlas_dirty_flags: Dict[str, bool] = {}
        self.animation_beat_overrides: Dict[str, List[float]] = {}
        self._beat_manual_overrides: Set[str] = set()
        self._tempo_segments: List[Tuple[float, float, float]] = []
        self._active_metronome_bpm: float = self.current_bpm
        self.xml_bin_file_map: Dict[str, str] = {}
        self._tileset_cache: Dict[str, TilesetData] = {}
        self._grid_cache: Dict[str, TileGridData] = {}
        self._binary_atlas_cache: Dict[str, TextureAtlas] = {}
        self._last_tile_render_signature: Optional[str] = None
        self._last_terrain_alignment_signature: Optional[str] = None
        self._sprite_workshop_dialog: Optional[SpriteWorkshopDialog] = None
        self._keyframe_clipboard: Optional[Dict[str, Any]] = None
        self._hang_watchdog_active: bool = False
        self.buddy_audio_tracks: Dict[str, str] = {}
        self.buddy_audio_tracks_normalized: Dict[str, str] = {}
        self.buddy_audio_blocked_tracks: Set[str] = set()
        self.buddy_audio_blocked_tracks_normalized: Set[str] = set()
        self.build_version: str = "b0.6"
        self._windowed_geometry: Optional[bytes] = None
        self._fullscreen_active = False
        self._pytoshop = None
        self._rev6_anim_module = None
        self._diagnostics_config = DiagnosticsConfig()
        self.diagnostics: Optional[DiagnosticsManager] = None
        self._left_panel_visible: bool = True
        self._right_panel_visible: bool = True
        self._focus_mode_enabled: bool = False
        self._splitter_last_sizes: Optional[List[int]] = None
        self._panel_toggle_guard: bool = False
        self._compact_ui_enabled: bool = False
        self._focus_restore: Optional[Tuple[bool, bool]] = None
        self._timeline_visible: bool = True
        self._content_splitter_last_sizes: Optional[List[int]] = None
        self._log_visible: bool = True
        self._log_splitter_last_sizes: Optional[List[int]] = None
        self.viewport_tool_mode: str = "cursor"
        self.control_panel_host: Optional[QWidget] = None
        self.viewport_tool_buttons: List[QToolButton] = []
        self.viewport_tool_cursor_btn: Optional[QToolButton] = None
        self.viewport_tool_zoom_btn: Optional[QToolButton] = None
        self._viewport_tool_icon_cursor_selected: Optional[QIcon] = None
        self._viewport_tool_icon_cursor_unselected: Optional[QIcon] = None
        self._viewport_tool_icon_zoom_selected: Optional[QIcon] = None
        self._viewport_tool_icon_zoom_unselected: Optional[QIcon] = None
        self._viewport_zoom_cursor: Optional[QCursor] = None

        self.init_ui()
        self._apply_anchor_logging_preferences()
        self._setup_shortcuts()
        self.audio_manager = AudioManager(self)
        self.audio_manager.set_volume(self.control_panel.audio_volume_slider.value())
        self.audio_manager.set_enabled(self.control_panel.audio_enable_checkbox.isChecked())
        self._apply_audio_preferences_to_controls()
        self.metronome = Metronome(self)
        self.metronome.tick.connect(self._on_metronome_tick)
        self.metronome.set_time_signature(self.time_signature_num, self.time_signature_denom)
        self._update_metronome_state()
        self.selected_layer_ids: Set[int] = set()
        self.primary_selected_layer_id: Optional[int] = None
        self.selected_attachment_id: Optional[int] = None
        self.selection_lock_enabled: bool = False
        self.viewer_splitter: Optional[QSplitter] = None
        self.multi_view_widgets: List[OpenGLAnimationWidget] = []
        self.multi_view_containers: List[QWidget] = []
        self.multi_view_labels: List[QLabel] = []
        self.load_settings()
        
        # Find bin2json script
        self.find_bin2json()
    
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("My Singing Monsters Animation Viewer")
        self.setGeometry(100, 100, 1400, 900)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        
        # Top toolbar
        toolbar_layout = QHBoxLayout()
        
        self.path_label = QLabel("Game Path: Not Set")
        toolbar_layout.addWidget(self.path_label)
        
        browse_btn = QPushButton("Browse Game Path")
        browse_btn.clicked.connect(self.browse_game_path)
        toolbar_layout.addWidget(browse_btn)

        dof_browse_btn = QPushButton("Browse DOF Game Path")
        dof_browse_btn.clicked.connect(self.browse_dof_path)
        toolbar_layout.addWidget(dof_browse_btn)

        downloads_browse_btn = QPushButton("Browse Downloads Path")
        downloads_browse_btn.clicked.connect(self.browse_downloads_path)
        toolbar_layout.addWidget(downloads_browse_btn)

        toolbar_layout.addStretch()

        self.controls_toggle_btn = QToolButton()
        self.controls_toggle_btn.setText("Controls")
        self.controls_toggle_btn.setCheckable(True)
        self.controls_toggle_btn.setChecked(True)
        self.controls_toggle_btn.setToolTip("Show/hide the control panel")
        self.controls_toggle_btn.toggled.connect(self._on_controls_toggle)
        toolbar_layout.addWidget(self.controls_toggle_btn)

        self.layers_toggle_btn = QToolButton()
        self.layers_toggle_btn.setText("Layers")
        self.layers_toggle_btn.setCheckable(True)
        self.layers_toggle_btn.setChecked(True)
        self.layers_toggle_btn.setToolTip("Show/hide the layer panel")
        self.layers_toggle_btn.toggled.connect(self._on_layers_toggle)
        toolbar_layout.addWidget(self.layers_toggle_btn)

        self.timeline_toggle_btn = QToolButton()
        self.timeline_toggle_btn.setText("Timeline")
        self.timeline_toggle_btn.setCheckable(True)
        self.timeline_toggle_btn.setChecked(True)
        self.timeline_toggle_btn.setToolTip("Show/hide the timeline panel")
        self.timeline_toggle_btn.toggled.connect(self._on_timeline_toggle)
        toolbar_layout.addWidget(self.timeline_toggle_btn)

        self.log_toggle_btn = QToolButton()
        self.log_toggle_btn.setText("Log")
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.setChecked(True)
        self.log_toggle_btn.setToolTip("Show/hide the log panel")
        self.log_toggle_btn.toggled.connect(self._on_log_toggle)
        toolbar_layout.addWidget(self.log_toggle_btn)

        self.focus_mode_btn = QToolButton()
        self.focus_mode_btn.setText("Focus Mode")
        self.focus_mode_btn.setCheckable(True)
        self.focus_mode_btn.setToolTip("Hide both side panels")
        self.focus_mode_btn.toggled.connect(self._on_focus_mode_toggled)
        toolbar_layout.addWidget(self.focus_mode_btn)
        
        sprite_workshop_btn = QPushButton("Sprite Workshop")
        sprite_workshop_btn.clicked.connect(self.show_sprite_workshop)
        toolbar_layout.addWidget(sprite_workshop_btn)

        midi_editor_btn = QPushButton("MIDI Editor")
        midi_editor_btn.clicked.connect(self.show_midi_editor)
        toolbar_layout.addWidget(midi_editor_btn)

        self.multi_view_btn = QPushButton("Multi View…")
        self.multi_view_btn.setToolTip("Load additional animations side-by-side in the viewer")
        self.multi_view_btn.clicked.connect(self.load_multi_view_animations)
        toolbar_layout.addWidget(self.multi_view_btn)

        self.clear_multi_view_btn = QPushButton("Clear Views")
        self.clear_multi_view_btn.setToolTip("Remove all additional viewer panes")
        self.clear_multi_view_btn.setEnabled(False)
        self.clear_multi_view_btn.clicked.connect(self.clear_multi_view_animations)
        toolbar_layout.addWidget(self.clear_multi_view_btn)

        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.show_settings)
        toolbar_layout.addWidget(settings_btn)
        
        main_layout.addLayout(toolbar_layout)
        
        # Splitter for main content
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setHandleWidth(2)
        
        # Left panel - Controls
        self.control_panel = ControlPanel()
        self.control_panel.set_bpm_value(self.current_bpm)
        self.connect_control_panel_signals()
        self.control_panel.set_pose_controls_enabled(False)
        self.control_panel.set_sprite_tools_enabled(False)
        self.control_panel.set_solid_bg_enabled(self.solid_bg_enabled)
        self.control_panel.set_solid_bg_color(self.solid_bg_color)
        self.control_panel.set_viewport_bg_color_mode(self.viewport_bg_color_mode)
        self.control_panel.set_export_include_viewport_bg(self.export_include_viewport_background)
        self.control_panel.set_viewport_bg_enabled(self.viewport_bg_enabled)
        self.control_panel.set_viewport_bg_keep_aspect(self.viewport_bg_keep_aspect)
        self.control_panel.set_viewport_bg_zoom_fill(self.viewport_bg_zoom_fill)
        self.control_panel.set_viewport_bg_parallax_enabled(self.viewport_bg_parallax_enabled)
        self.control_panel.set_viewport_bg_parallax_zoom_strength(self.viewport_bg_parallax_zoom_strength)
        self.control_panel.set_viewport_bg_parallax_pan_strength(self.viewport_bg_parallax_pan_strength)
        self.control_panel.set_viewport_bg_flips(
            self.viewport_bg_flip_h,
            self.viewport_bg_flip_v,
        )
        self.control_panel.set_viewport_bg_image(
            self.viewport_bg_image_path,
            self.viewport_bg_image_enabled,
        )
        self.control_panel.set_metronome_checkbox(self.metronome_enabled)
        self.control_panel.set_metronome_audible_checkbox(self.metronome_audible)
        self.control_panel.set_time_signature(self.time_signature_num, self.time_signature_denom)
        self.control_panel.set_pose_mode(self.pose_influence_mode)
        self.control_panel.set_constraints_enabled(self.constraints_enabled)
        self.control_panel.set_joint_solver_enabled(self.joint_solver_enabled)
        self.control_panel.set_joint_solver_iterations(self.joint_solver_iterations)
        self.control_panel.set_joint_solver_strength(self.joint_solver_strength)
        self.control_panel.set_joint_solver_parented(self.joint_solver_parented)
        self.control_panel.set_propagate_user_transforms(self.propagate_user_transforms)
        self.control_panel.set_preserve_children_on_record(self.preserve_children_on_record)
        self.control_panel_host = QWidget()
        control_panel_host_layout = QVBoxLayout(self.control_panel_host)
        control_panel_host_layout.setContentsMargins(0, 0, 0, 0)
        control_panel_host_layout.setSpacing(0)
        control_panel_host_layout.addWidget(self.control_panel, 1)
        control_panel_host_layout.addWidget(self._build_viewport_tool_strip(), 0)
        self.main_splitter.addWidget(self.control_panel_host)
        
        # Center - OpenGL viewer
        self.gl_widget = OpenGLAnimationWidget(shader_registry=self.shader_registry)
        self.gl_widget.set_costume_pivot_adjustment_enabled(False)
        self.gl_widget.set_constraint_manager(self.constraint_manager)
        self.gl_widget.set_joint_solver_enabled(self.joint_solver_enabled)
        self.gl_widget.set_joint_solver_iterations(self.joint_solver_iterations)
        self.gl_widget.set_joint_solver_strength(self.joint_solver_strength)
        self.gl_widget.set_joint_solver_parented(self.joint_solver_parented)
        self.gl_widget.set_propagate_user_transforms(self.propagate_user_transforms)
        self.gl_widget.set_zoom_to_cursor(self.export_settings.camera_zoom_to_cursor)
        self.gl_widget.set_particle_world_space_override(self.dof_particles_world_space)
        self.gl_widget.set_particle_viewport_cap(self.dof_particle_viewport_cap)
        self.gl_widget.set_particle_distance_sensitivity(self.dof_particle_distance_sensitivity)
        self.gl_widget.set_dof_alpha_smoothing_enabled(self.dof_alpha_edge_smoothing_enabled)
        self.gl_widget.set_dof_alpha_smoothing_strength(self.dof_alpha_edge_smoothing_strength)
        self.gl_widget.set_dof_alpha_smoothing_mode(self.dof_alpha_edge_smoothing_mode)
        self.gl_widget.set_dof_sprite_shader_mode(self.dof_sprite_shader_mode)
        self._apply_postfx_settings_to_widget(self.gl_widget)
        self.gl_widget.set_viewport_background_enabled(self.viewport_bg_enabled)
        self.gl_widget.set_background_color_rgba(self.solid_bg_color)
        self.gl_widget.set_viewport_background_color_mode(self.viewport_bg_color_mode)
        self.gl_widget.set_viewport_background_keep_aspect(self.viewport_bg_keep_aspect)
        self.gl_widget.set_viewport_background_zoom_fill(self.viewport_bg_zoom_fill)
        self.gl_widget.set_viewport_background_parallax_enabled(self.viewport_bg_parallax_enabled)
        self.gl_widget.set_viewport_background_parallax_zoom_sensitivity(
            self.viewport_bg_parallax_zoom_strength
        )
        self.gl_widget.set_viewport_background_parallax_pan_sensitivity(
            self.viewport_bg_parallax_pan_strength
        )
        self.gl_widget.set_viewport_background_flips(
            self.viewport_bg_flip_h,
            self.viewport_bg_flip_v,
        )
        self.gl_widget.set_viewport_background_image_enabled(self.viewport_bg_image_enabled)
        self.gl_widget.set_viewport_background_image_path(self.viewport_bg_image_path)
        self._refresh_viewport_tool_buttons()
        self.gl_widget.animation_time_changed.connect(self.on_animation_time_changed)
        self.gl_widget.animation_looped.connect(self.on_animation_looped)
        self.gl_widget.playback_state_changed.connect(self.on_playback_state_changed)
        self.gl_widget.transform_action_committed.connect(self._record_transform_action)
        self.gl_widget.tile_render_stats.connect(self._on_tile_render_stats)
        stored_tile_path = self.settings.value("terrain/tile_render_path", "", type=str) or ""
        if stored_tile_path:
            self.gl_widget.set_tile_render_path(stored_tile_path)
        stored_tile_filter = self.settings.value("terrain/tile_filter_mode", "", type=str) or ""
        if stored_tile_filter:
            self.gl_widget.set_tile_filter_mode(stored_tile_filter)
        stored_tile_flag_order = self.settings.value("terrain/tile_flag_order_mode", "", type=str) or ""
        if stored_tile_flag_order:
            self.gl_widget.set_tile_flag_order_mode(stored_tile_flag_order)
        stored_flag1_transform = self.settings.value("terrain/tile_flag1_transform_mode", "", type=str) or ""
        if stored_flag1_transform:
            self.gl_widget.set_tile_flag1_transform_mode(stored_flag1_transform)
        terrain_global_x = float(self.settings.value("terrain/global_offset_x", 0.0, type=float))
        terrain_global_y = float(self.settings.value("terrain/global_offset_y", 0.0, type=float))
        terrain_global_rot = float(self.settings.value("terrain/global_rotation_deg", 0.0, type=float))
        terrain_global_scale = float(self.settings.value("terrain/global_scale", 1.0, type=float))
        self.control_panel.terrain_global_x_spin.setValue(terrain_global_x)
        self.control_panel.terrain_global_y_spin.setValue(terrain_global_y)
        self.control_panel.terrain_global_rot_spin.setValue(terrain_global_rot)
        self.control_panel.terrain_global_scale_spin.setValue(terrain_global_scale)
        self.gl_widget.set_tile_global_transform(
            terrain_global_x,
            terrain_global_y,
            terrain_global_rot,
            terrain_global_scale,
        )
        terrain_tile_index = int(self.settings.value("terrain/selected_tile_index", -1, type=int))
        terrain_tile_x = float(self.settings.value("terrain/selected_tile_offset_x", 0.0, type=float))
        terrain_tile_y = float(self.settings.value("terrain/selected_tile_offset_y", 0.0, type=float))
        terrain_tile_rot = float(self.settings.value("terrain/selected_tile_rotation_deg", 0.0, type=float))
        terrain_tile_scale = float(self.settings.value("terrain/selected_tile_scale", 1.0, type=float))
        self.gl_widget.set_tile_selected_index(terrain_tile_index)
        self.gl_widget.set_tile_selected_transform(
            terrain_tile_x,
            terrain_tile_y,
            terrain_tile_rot,
            terrain_tile_scale,
        )
        self.viewer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.viewer_splitter.setHandleWidth(2)
        self.viewer_splitter.addWidget(self.gl_widget)
        self.main_splitter.addWidget(self.viewer_splitter)
        self._refresh_constraints_ui()

        self._sync_tile_debug_controls_from_gl()
        self.control_panel.terrain_path_combo.currentIndexChanged.connect(self._on_terrain_path_changed)
        self.control_panel.terrain_filter_combo.currentIndexChanged.connect(self._on_terrain_filter_changed)
        self.control_panel.terrain_flag_order_combo.currentIndexChanged.connect(self._on_terrain_flag_order_changed)
        self.control_panel.terrain_flag1_transform_combo.currentIndexChanged.connect(
            self._on_terrain_flag1_transform_changed
        )
        self.control_panel.terrain_global_x_spin.valueChanged.connect(self._on_terrain_global_transform_changed)
        self.control_panel.terrain_global_y_spin.valueChanged.connect(self._on_terrain_global_transform_changed)
        self.control_panel.terrain_global_rot_spin.valueChanged.connect(self._on_terrain_global_transform_changed)
        self.control_panel.terrain_global_scale_spin.valueChanged.connect(self._on_terrain_global_transform_changed)
        self.control_panel.terrain_tile_index_spin.valueChanged.connect(self._on_terrain_tile_index_changed)
        self.control_panel.terrain_tile_x_spin.valueChanged.connect(self._on_terrain_tile_transform_changed)
        self.control_panel.terrain_tile_y_spin.valueChanged.connect(self._on_terrain_tile_transform_changed)
        self.control_panel.terrain_tile_rot_spin.valueChanged.connect(self._on_terrain_tile_transform_changed)
        self.control_panel.terrain_tile_scale_spin.valueChanged.connect(self._on_terrain_tile_transform_changed)
        
        # Right panel - Layer visibility
        self.layer_panel = LayerPanel()
        self.connect_layer_panel_signals()
        
        # Set size constraints for right panel
        self.layer_panel.setMinimumWidth(200)
        self.layer_panel.setMaximumWidth(350)
        
        self.main_splitter.addWidget(self.layer_panel)
        
        self.main_splitter.setCollapsible(0, True)
        self.main_splitter.setCollapsible(1, False)
        self.main_splitter.setCollapsible(2, True)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([520, 860, 320])
        self.main_splitter.splitterMoved.connect(self._on_main_splitter_moved)
        
        # Timeline and log inside a vertical splitter
        self.timeline = TimelineWidget()
        self.connect_timeline_signals()
        self.timeline.set_beat_grid_visible(self.show_beat_grid)
        self.timeline.set_beat_edit_enabled(self.show_beat_grid and self.allow_beat_edit)
        self.log_widget = LogWidget()
        self._init_diagnostics()
        
        self.content_splitter = QSplitter(Qt.Orientation.Vertical)
        self.content_splitter.setHandleWidth(2)
        self.content_splitter.addWidget(self.main_splitter)
        self.content_splitter.addWidget(self.timeline)
        self.content_splitter.setStretchFactor(0, 3)
        self.content_splitter.setStretchFactor(1, 1)
        self.content_splitter.setCollapsible(1, True)
        self.content_splitter.setSizes([700, 200])
        self.content_splitter.splitterMoved.connect(self._on_content_splitter_moved)
        
        self.log_splitter = QSplitter(Qt.Orientation.Vertical)
        self.log_splitter.addWidget(self.content_splitter)
        self.log_splitter.addWidget(self.log_widget)
        self.log_splitter.setStretchFactor(0, 3)
        self.log_splitter.setStretchFactor(1, 1)
        self.log_splitter.setCollapsible(1, True)
        self.log_splitter.splitterMoved.connect(self._on_log_splitter_moved)
        
        main_layout.addWidget(self.log_splitter, stretch=1)
        
        self.log_widget.log("Application started", "INFO")
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def _load_splitter_sizes(self, key: str) -> Optional[List[int]]:
        value = self.settings.value(key)
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            try:
                sizes = [int(v) for v in value]
                return sizes
            except (TypeError, ValueError):
                return None
        if isinstance(value, str):
            parts = [p for p in re.split(r"[,\s]+", value.strip()) if p]
            try:
                return [int(p) for p in parts]
            except (TypeError, ValueError):
                return None
        return None

    def _apply_ui_preferences(self):
        compact = bool(self.settings.value("ui/compact_ui", False, type=bool))
        self._set_compact_ui(compact, save=False)
        sizes = self._load_splitter_sizes("ui/main_splitter_sizes")
        if sizes and len(sizes) == 3:
            self._splitter_last_sizes = sizes
        content_sizes = self._load_splitter_sizes("ui/content_splitter_sizes")
        if content_sizes and len(content_sizes) == 2:
            self._content_splitter_last_sizes = content_sizes
        log_sizes = self._load_splitter_sizes("ui/log_splitter_sizes")
        if log_sizes and len(log_sizes) == 2:
            self._log_splitter_last_sizes = log_sizes
        left_visible = bool(self.settings.value("ui/left_panel_visible", True, type=bool))
        right_visible = bool(self.settings.value("ui/right_panel_visible", True, type=bool))
        focus_mode = bool(self.settings.value("ui/focus_mode", False, type=bool))
        timeline_visible = bool(self.settings.value("ui/timeline_visible", True, type=bool))
        log_visible = bool(self.settings.value("ui/log_visible", True, type=bool))
        self._left_panel_visible = left_visible
        self._right_panel_visible = right_visible
        self._timeline_visible = timeline_visible
        self._log_visible = log_visible
        if focus_mode:
            self._set_focus_mode(True, save=False)
        else:
            self._set_panel_visibility(left_visible, right_visible, save=False)
        self._set_timeline_visibility(timeline_visible, save=False)
        self._set_log_visibility(log_visible, save=False)

    def _apply_control_panel_preferences(self):
        if not hasattr(self, "control_panel"):
            return
        checkbox = getattr(self.control_panel, "dof_include_mesh_xml_checkbox", None)
        if checkbox is None:
            return
        stored = self.settings.value("ui/control_panel/dof_include_mesh_xml", None)
        if stored is None:
            checked = True
        elif isinstance(stored, bool):
            checked = stored
        elif isinstance(stored, (int, float)):
            checked = bool(stored)
        else:
            checked = str(stored).strip().lower() in ("1", "true", "yes", "on")
        checkbox.blockSignals(True)
        checkbox.setChecked(bool(checked))
        checkbox.blockSignals(False)

    def _update_last_splitter_sizes(self):
        if not hasattr(self, "main_splitter"):
            return
        sizes = self.main_splitter.sizes()
        if len(sizes) != 3:
            return
        if sizes[0] > 0 and sizes[2] > 0:
            self._splitter_last_sizes = [int(s) for s in sizes]
            self.settings.setValue("ui/main_splitter_sizes", self._splitter_last_sizes)

    def _apply_splitter_visibility(self, left_visible: bool, right_visible: bool):
        if not hasattr(self, "main_splitter"):
            return
        if self.control_panel_host is not None:
            self.control_panel_host.setVisible(left_visible)
        else:
            self.control_panel.setVisible(left_visible)
        self.layer_panel.setVisible(right_visible)
        sizes = self.main_splitter.sizes()
        total = sum(sizes) if sizes else max(1, self.width())
        total = max(1, total)
        if left_visible and right_visible:
            if self._splitter_last_sizes and len(self._splitter_last_sizes) == 3:
                self.main_splitter.setSizes(self._splitter_last_sizes)
            return
        if not left_visible and not right_visible:
            self.main_splitter.setSizes([0, total, 0])
            return
        if left_visible:
            left = 0
            if self._splitter_last_sizes and len(self._splitter_last_sizes) == 3:
                left = max(1, int(self._splitter_last_sizes[0]))
            if left <= 0:
                left = max(200, int(total * 0.25))
            self.main_splitter.setSizes([left, max(1, total - left), 0])
            return
        right = 0
        if self._splitter_last_sizes and len(self._splitter_last_sizes) == 3:
            right = max(1, int(self._splitter_last_sizes[2]))
        if right <= 0:
            right = max(200, int(total * 0.25))
        self.main_splitter.setSizes([0, max(1, total - right), right])

    def _update_panel_toggle_buttons(self):
        for btn, value in (
            (self.controls_toggle_btn, self._left_panel_visible),
            (self.layers_toggle_btn, self._right_panel_visible),
            (self.timeline_toggle_btn, self._timeline_visible),
            (self.log_toggle_btn, self._log_visible),
            (self.focus_mode_btn, self._focus_mode_enabled),
        ):
            btn.blockSignals(True)
            btn.setChecked(value)
            btn.blockSignals(False)

    def _set_panel_visibility(self, left_visible: bool, right_visible: bool, *, save: bool = True):
        prev_left = self._left_panel_visible
        prev_right = self._right_panel_visible
        if prev_left and prev_right and (not left_visible or not right_visible):
            self._update_last_splitter_sizes()
        self._left_panel_visible = bool(left_visible)
        self._right_panel_visible = bool(right_visible)
        self._apply_splitter_visibility(self._left_panel_visible, self._right_panel_visible)
        self._update_panel_toggle_buttons()
        if save:
            self.settings.setValue("ui/left_panel_visible", self._left_panel_visible)
            self.settings.setValue("ui/right_panel_visible", self._right_panel_visible)

    def _set_focus_mode(self, enabled: bool, *, save: bool = True):
        enabled = bool(enabled)
        if self._focus_mode_enabled == enabled:
            return
        self._focus_mode_enabled = enabled
        if self._focus_mode_enabled:
            self._focus_restore = (self._left_panel_visible, self._right_panel_visible)
            self._set_panel_visibility(False, False, save=False)
        else:
            left, right = self._focus_restore or (True, True)
            self._set_panel_visibility(left, right, save=False)
        self._update_panel_toggle_buttons()
        if save:
            self.settings.setValue("ui/focus_mode", self._focus_mode_enabled)

    def _set_timeline_visibility(self, visible: bool, *, save: bool = True):
        visible = bool(visible)
        if self._timeline_visible == visible:
            return
        self._timeline_visible = visible
        if not hasattr(self, "content_splitter"):
            return
        sizes = self.content_splitter.sizes()
        total = sum(sizes) if sizes else max(1, self.height())
        total = max(1, total)
        if not visible:
            if sizes and sizes[1] > 0:
                self._content_splitter_last_sizes = [int(s) for s in sizes]
                self.settings.setValue("ui/content_splitter_sizes", self._content_splitter_last_sizes)
            self.timeline.setVisible(False)
            self.content_splitter.setSizes([total, 0])
        else:
            self.timeline.setVisible(True)
            if self._content_splitter_last_sizes and len(self._content_splitter_last_sizes) == 2:
                self.content_splitter.setSizes(self._content_splitter_last_sizes)
            else:
                self.content_splitter.setSizes([int(total * 0.8), max(1, int(total * 0.2))])
        self._update_panel_toggle_buttons()
        if save:
            self.settings.setValue("ui/timeline_visible", self._timeline_visible)

    def _on_timeline_toggle(self, checked: bool):
        self._set_timeline_visibility(bool(checked), save=True)

    def _set_log_visibility(self, visible: bool, *, save: bool = True):
        visible = bool(visible)
        if self._log_visible == visible:
            return
        self._log_visible = visible
        if not hasattr(self, "log_splitter"):
            return
        sizes = self.log_splitter.sizes()
        total = sum(sizes) if sizes else max(1, self.height())
        total = max(1, total)
        if not visible:
            if sizes and sizes[1] > 0:
                self._log_splitter_last_sizes = [int(s) for s in sizes]
                self.settings.setValue("ui/log_splitter_sizes", self._log_splitter_last_sizes)
            self.log_widget.setVisible(False)
            self.log_splitter.setSizes([total, 0])
        else:
            self.log_widget.setVisible(True)
            if self._log_splitter_last_sizes and len(self._log_splitter_last_sizes) == 2:
                self.log_splitter.setSizes(self._log_splitter_last_sizes)
            else:
                self.log_splitter.setSizes([int(total * 0.8), max(1, int(total * 0.2))])
        self._update_panel_toggle_buttons()
        if save:
            self.settings.setValue("ui/log_visible", self._log_visible)

    def _on_log_toggle(self, checked: bool):
        self._set_log_visibility(bool(checked), save=True)

    def _on_controls_toggle(self, checked: bool):
        if self._focus_mode_enabled:
            self._set_focus_mode(False, save=True)
        self._set_panel_visibility(bool(checked), self._right_panel_visible, save=True)

    def _on_layers_toggle(self, checked: bool):
        if self._focus_mode_enabled:
            self._set_focus_mode(False, save=True)
        self._set_panel_visibility(self._left_panel_visible, bool(checked), save=True)

    def _on_focus_mode_toggled(self, checked: bool):
        self._set_focus_mode(bool(checked), save=True)

    def _on_main_splitter_moved(self, _pos: int, _index: int):
        if self._focus_mode_enabled:
            return
        sizes = self.main_splitter.sizes()
        if len(sizes) == 3:
            left_visible = sizes[0] > 0
            right_visible = sizes[2] > 0
            if left_visible != self._left_panel_visible or right_visible != self._right_panel_visible:
                self._left_panel_visible = left_visible
                self._right_panel_visible = right_visible
                self._update_panel_toggle_buttons()
                self.settings.setValue("ui/left_panel_visible", self._left_panel_visible)
                self.settings.setValue("ui/right_panel_visible", self._right_panel_visible)
        if self._left_panel_visible and self._right_panel_visible:
            self._update_last_splitter_sizes()

    def _load_viewport_tool_icon(self, filename: str) -> Optional[QIcon]:
        path = self.project_root / "assets" / filename
        if not path.exists():
            return None
        pix = QPixmap(str(path))
        if pix.isNull():
            return None
        return QIcon(pix)

    def _build_system_zoom_cursor(self) -> QCursor:
        """Best-effort native zoom cursor, with crosshair fallback."""
        zoom_shape = getattr(Qt.CursorShape, "ZoomInCursor", None)
        if zoom_shape is not None:
            try:
                return QCursor(zoom_shape)
            except Exception:
                pass

        if os.name == "nt":
            win_root = os.environ.get("SystemRoot", r"C:\Windows")
            cursor_dir = Path(win_root) / "Cursors"
            candidates = (
                "aero_zoomin.cur",
                "zoomin.cur",
                "Aero_zoomin.cur",
            )
            for name in candidates:
                cur_path = cursor_dir / name
                if not cur_path.exists():
                    continue
                pix = QPixmap(str(cur_path))
                if pix.isNull():
                    continue
                hot_x = max(0, min(pix.width() - 1, int(round(pix.width() * 0.45))))
                hot_y = max(0, min(pix.height() - 1, int(round(pix.height() * 0.20))))
                return QCursor(pix, hot_x, hot_y)

        return QCursor(Qt.CursorShape.CrossCursor)

    def _build_viewport_tool_strip(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(6)
        highlight = self.palette().color(self.palette().ColorRole.Highlight)
        checked_bg = f"rgba({highlight.red()}, {highlight.green()}, {highlight.blue()}, 70)"
        checked_border = highlight.lighter(120).name()

        base_style = (
            "QToolButton {"
            " background-color: #2a2a2a;"
            " border: 1px solid #4a4a4a;"
            " border-radius: 4px;"
            " padding: 2px;"
            "}"
            "QToolButton:hover {"
            " background-color: #353535;"
            "}"
            "QToolButton:pressed {"
            " background-color: #3b3b3b;"
            "}"
            "QToolButton:checked {"
            f" background-color: {checked_bg};"
            f" border: 1px solid {checked_border};"
            "}"
        )

        def _make_button() -> QToolButton:
            btn = QToolButton()
            btn.setCheckable(True)
            btn.setAutoRaise(False)
            btn.setFixedSize(38, 38)
            btn.setIconSize(QPixmap(25, 25).size())
            btn.setStyleSheet(base_style)
            return btn

        self._viewport_tool_icon_cursor_selected = self._load_viewport_tool_icon("Cursor Icon Selected.png")
        self._viewport_tool_icon_cursor_unselected = self._load_viewport_tool_icon("Cursor Icon Unselected.png")
        self._viewport_tool_icon_zoom_selected = self._load_viewport_tool_icon("Zoom Icon Selected.png")
        self._viewport_tool_icon_zoom_unselected = self._load_viewport_tool_icon("Zoom Icon Unselected.png")

        self._viewport_zoom_cursor = self._build_system_zoom_cursor()

        self.viewport_tool_cursor_btn = _make_button()
        self.viewport_tool_cursor_btn.setToolTip("Cursor Tool")
        self.viewport_tool_cursor_btn.clicked.connect(lambda: self._set_viewport_tool_mode("cursor"))
        self.viewport_tool_buttons.append(self.viewport_tool_cursor_btn)
        layout.addWidget(self.viewport_tool_cursor_btn)

        self.viewport_tool_zoom_btn = _make_button()
        self.viewport_tool_zoom_btn.setToolTip("Zoom Tool")
        self.viewport_tool_zoom_btn.clicked.connect(lambda: self._set_viewport_tool_mode("zoom"))
        self.viewport_tool_buttons.append(self.viewport_tool_zoom_btn)
        layout.addWidget(self.viewport_tool_zoom_btn)

        for _ in range(5):
            placeholder_btn = _make_button()
            placeholder_btn.setCheckable(False)
            placeholder_btn.setToolTip("Reserved")
            self.viewport_tool_buttons.append(placeholder_btn)
            layout.addWidget(placeholder_btn)

        layout.addStretch(1)
        self._refresh_viewport_tool_buttons()
        return container

    def _refresh_viewport_tool_buttons(self) -> None:
        if self.viewport_tool_cursor_btn is not None:
            self.viewport_tool_cursor_btn.blockSignals(True)
            self.viewport_tool_cursor_btn.setChecked(self.viewport_tool_mode == "cursor")
            icon = (
                self._viewport_tool_icon_cursor_selected
                if self.viewport_tool_mode == "cursor"
                else self._viewport_tool_icon_cursor_unselected
            )
            if icon is not None:
                self.viewport_tool_cursor_btn.setIcon(icon)
            self.viewport_tool_cursor_btn.blockSignals(False)

        if self.viewport_tool_zoom_btn is not None:
            self.viewport_tool_zoom_btn.blockSignals(True)
            self.viewport_tool_zoom_btn.setChecked(self.viewport_tool_mode == "zoom")
            icon = (
                self._viewport_tool_icon_zoom_selected
                if self.viewport_tool_mode == "zoom"
                else self._viewport_tool_icon_zoom_unselected
            )
            if icon is not None:
                self.viewport_tool_zoom_btn.setIcon(icon)
            self.viewport_tool_zoom_btn.blockSignals(False)

        if hasattr(self, "gl_widget"):
            zoom_cursor = self._viewport_zoom_cursor or QCursor(Qt.CursorShape.CrossCursor)
            self.gl_widget.set_interaction_cursors(
                default_cursor=QCursor(Qt.CursorShape.ArrowCursor),
                zoom_cursor=zoom_cursor,
            )
            self.gl_widget.set_interaction_tool(self.viewport_tool_mode)

    def _set_viewport_tool_mode(self, mode: str) -> None:
        normalized = "zoom" if str(mode or "").strip().lower() == "zoom" else "cursor"
        self.viewport_tool_mode = normalized
        self._refresh_viewport_tool_buttons()

    def _on_content_splitter_moved(self, _pos: int, _index: int):
        if not hasattr(self, "content_splitter"):
            return
        sizes = self.content_splitter.sizes()
        if len(sizes) != 2:
            return
        timeline_visible = sizes[1] > 0
        if timeline_visible != self._timeline_visible:
            self._timeline_visible = timeline_visible
            self._update_panel_toggle_buttons()
            self.settings.setValue("ui/timeline_visible", self._timeline_visible)
        if timeline_visible:
            self._content_splitter_last_sizes = [int(s) for s in sizes]
            self.settings.setValue("ui/content_splitter_sizes", self._content_splitter_last_sizes)

    def _on_log_splitter_moved(self, _pos: int, _index: int):
        if not hasattr(self, "log_splitter"):
            return
        sizes = self.log_splitter.sizes()
        if len(sizes) != 2:
            return
        log_visible = sizes[1] > 0
        if log_visible != self._log_visible:
            self._log_visible = log_visible
            self._update_panel_toggle_buttons()
            self.settings.setValue("ui/log_visible", self._log_visible)
        if log_visible:
            self._log_splitter_last_sizes = [int(s) for s in sizes]
            self.settings.setValue("ui/log_splitter_sizes", self._log_splitter_last_sizes)

    def _set_compact_ui(self, enabled: bool, *, save: bool = True):
        self._compact_ui_enabled = bool(enabled)
        if hasattr(self, "control_panel"):
            self.control_panel.set_compact_ui(self._compact_ui_enabled)
        if hasattr(self, "layer_panel"):
            self.layer_panel.set_compact_ui(self._compact_ui_enabled)
        if hasattr(self, "timeline"):
            self.timeline.set_compact_ui(self._compact_ui_enabled)
        if save:
            self.settings.setValue("ui/compact_ui", self._compact_ui_enabled)

    def on_compact_ui_toggled(self, enabled: bool):
        self._set_compact_ui(enabled, save=True)

    def _setup_shortcuts(self):
        """Configure application-wide shortcuts."""
        self._keybind_shortcuts: Dict[str, QShortcut] = {}
        self._keybind_handlers: Dict[str, Any] = {
            "fullscreen": self.toggle_fullscreen,
            "undo": self._handle_undo_shortcut,
            "redo": self._handle_redo_shortcut,
            "redo_alt": self._handle_redo_shortcut,
            "copy_keyframes": self.copy_selected_keyframes,
            "paste_keyframes": self.paste_copied_keyframes,
            "move_tool": self._activate_move_tool_shortcut,
            "free_transform": self._activate_free_transform_shortcut,
            "record_pose": self._handle_record_pose_shortcut,
            "toggle_playback": self._handle_toggle_playback_shortcut,
            "toggle_loop": self._handle_toggle_loop_shortcut,
            "toggle_rotation_gizmo": self._handle_toggle_rotation_gizmo_shortcut,
            "toggle_scale_gizmo": self._handle_toggle_scale_gizmo_shortcut,
            "toggle_controls_panel": lambda: self._toggle_panel_shortcut(self.controls_toggle_btn),
            "toggle_layers_panel": lambda: self._toggle_panel_shortcut(self.layers_toggle_btn),
            "toggle_timeline_panel": lambda: self._toggle_panel_shortcut(self.timeline_toggle_btn),
            "toggle_log_panel": lambda: self._toggle_panel_shortcut(self.log_toggle_btn),
            "toggle_focus_mode": lambda: self._toggle_panel_shortcut(self.focus_mode_btn),
        }
        self._redo_alt_override_enabled = False
        self._apply_keybind_shortcuts()

    def _get_keybind_sequence(self, action_key: str) -> str:
        defaults = default_keybinds()
        stored = self.settings.value(
            f"keybinds/{action_key}", defaults.get(action_key, ""), type=str
        )
        return normalize_keybind_sequence(stored)

    def _apply_keybind_shortcuts(self) -> None:
        for shortcut in self._keybind_shortcuts.values():
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._keybind_shortcuts.clear()

        sequences: Dict[str, str] = {}
        for action in keybind_actions():
            seq_text = self._get_keybind_sequence(action.key)
            sequences[action.key] = seq_text
            if not seq_text:
                continue
            seq = QKeySequence(seq_text)
            if seq.isEmpty():
                continue
            handler = self._keybind_handlers.get(action.key)
            if not handler:
                continue
            shortcut = QShortcut(seq, self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(handler)
            self._keybind_shortcuts[action.key] = shortcut

        self._keybind_sequences = sequences
        self._redo_alt_override_enabled = sequences.get("redo_alt", "") == "Ctrl+Shift+Z"

    def _toggle_panel_shortcut(self, button: Optional[QToolButton]) -> None:
        if self._focus_accepts_text():
            return
        if button:
            button.toggle()

    def _handle_record_pose_shortcut(self) -> None:
        if self._focus_accepts_text():
            return
        self.on_record_pose_clicked()

    def _handle_toggle_playback_shortcut(self) -> None:
        if self._focus_accepts_text():
            return
        self.toggle_playback()

    def _handle_toggle_loop_shortcut(self) -> None:
        if self._focus_accepts_text():
            return
        checkbox = getattr(self.timeline, "loop_checkbox", None)
        if checkbox:
            checkbox.toggle()

    def _handle_toggle_rotation_gizmo_shortcut(self) -> None:
        if self._focus_accepts_text():
            return
        checkbox = getattr(self.control_panel, "rotation_gizmo_checkbox", None)
        current = bool(checkbox.isChecked()) if checkbox else False
        self._set_rotation_gizmo_state(not current)

    def _handle_toggle_scale_gizmo_shortcut(self) -> None:
        if self._focus_accepts_text():
            return
        checkbox = getattr(self.control_panel, "scale_gizmo_checkbox", None)
        current = bool(checkbox.isChecked()) if checkbox else False
        self._set_scale_gizmo_state(not current)

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and self._redo_alt_override_enabled
            and self._is_ctrl_shift_z(event)
        ):
            event.accept()
            self._handle_redo_shortcut()
            return True
        return super().eventFilter(watched, event)

    def _handle_undo_shortcut(self):
        """Undo the most recent edit action."""
        if not self._undo_history_action():
            self.log_widget.log("Nothing to undo.", "INFO")

    def _handle_redo_shortcut(self):
        """Redo the most recently undone edit action."""
        if not self._redo_history_action():
            self.log_widget.log("Nothing to redo.", "INFO")

    def _focus_accepts_text(self) -> bool:
        widget = QApplication.focusWidget()
        if widget is None:
            return False
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            return True
        if isinstance(widget, QComboBox) and widget.isEditable():
            return True
        return False

    def _activate_move_tool_shortcut(self) -> None:
        if self._focus_accepts_text():
            return
        self._set_transform_mode("move")

    def _activate_free_transform_shortcut(self) -> None:
        if self._focus_accepts_text():
            return
        self._set_transform_mode("transform")

    def _set_transform_mode(self, mode: str) -> None:
        normalized = (mode or "").strip().lower()
        if normalized == "move":
            self._set_rotation_gizmo_state(False)
            self._set_scale_gizmo_state(False)
        elif normalized == "transform":
            self._set_rotation_gizmo_state(True)
            self._set_scale_gizmo_state(True)
            self._set_scale_mode_state("Per-Axis")

    def _set_rotation_gizmo_state(self, enabled: bool) -> None:
        checkbox = getattr(self.control_panel, "rotation_gizmo_checkbox", None)
        if checkbox:
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(enabled))
            checkbox.blockSignals(False)
        self.toggle_rotation_gizmo(bool(enabled))

    def _set_scale_gizmo_state(self, enabled: bool) -> None:
        checkbox = getattr(self.control_panel, "scale_gizmo_checkbox", None)
        if checkbox:
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(enabled))
            checkbox.blockSignals(False)
        self.toggle_scale_gizmo(bool(enabled))

    def _set_scale_mode_state(self, mode: str) -> None:
        combo = getattr(self.control_panel, "scale_mode_combo", None)
        if combo:
            idx = combo.findText(mode)
            if idx >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
        self.gl_widget.set_scale_gizmo_mode(mode)

    def _is_ctrl_shift_z(self, event) -> bool:
        if event.key() != Qt.Key.Key_Z:
            return False
        modifiers = event.modifiers()
        if not (modifiers & Qt.KeyboardModifier.ControlModifier):
            return False
        if not (modifiers & Qt.KeyboardModifier.ShiftModifier):
            return False
        if modifiers & Qt.KeyboardModifier.AltModifier:
            return False
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            return False
        return True

    def _undo_history_action(self, required_type: Optional[str] = None) -> bool:
        if not self._history_stack:
            return False
        action = self._history_stack[-1]
        if required_type and action['type'] != required_type:
            return False
        action = self._history_stack.pop()
        self._apply_history_action(action, undo=True)
        self._history_redo_stack.append(action)
        self._update_keyframe_history_controls()
        return True

    def _redo_history_action(self, required_type: Optional[str] = None) -> bool:
        if not self._history_redo_stack:
            return False
        action = self._history_redo_stack[-1]
        if required_type and action['type'] != required_type:
            return False
        action = self._history_redo_stack.pop()
        self._apply_history_action(action, undo=False)
        self._history_stack.append(action)
        self._update_keyframe_history_controls()
        return True

    def _apply_history_action(self, action: Dict[str, Any], *, undo: bool):
        action_type = action.get('type')
        label = action.get('label') or action_type or "edit"
        if action_type == 'transform':
            state = action['before'] if undo else action['after']
            self.gl_widget.apply_transform_snapshot(state)
            self.update_offset_display()
            message = "Undid" if undo else "Redid"
            self.log_widget.log(f"{message} {label}", "INFO")
            return
        snapshot = action['before'] if undo else action['after']
        self._apply_keyframe_snapshot(snapshot)
        self._refresh_timeline_keyframes()
        message = "Undid" if undo else "Redid"
        self.log_widget.log(f"{message} {label}", "INFO")

    def _push_history_action(self, action: Dict[str, Any]):
        self._history_stack.append(action)
        self._history_redo_stack.clear()
        self._update_keyframe_history_controls()

    def _record_transform_action(self, action: Dict[str, Any]):
        if not action:
            return
        payload = dict(action)
        payload['type'] = 'transform'
        payload.setdefault('label', "sprite transform")
        self._push_history_action(payload)

    def _apply_anchor_logging_preferences(self):
        """Toggle renderer anchor logging based on user preference."""
        self.anchor_debug_enabled = bool(self.export_settings.anchor_debug_logging)
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_anchor_logging_enabled(self.anchor_debug_enabled)

    def toggle_fullscreen(self):
        """Toggle between windowed and borderless fullscreen modes."""
        if self._fullscreen_active:
            self._exit_fullscreen()
        else:
            self._enter_fullscreen()

    def _enter_fullscreen(self):
        if self._fullscreen_active:
            return
        self._windowed_geometry = self.saveGeometry()
        self._fullscreen_active = True
        self.showFullScreen()

    def _exit_fullscreen(self):
        if not self._fullscreen_active:
            return
        self._fullscreen_active = False
        self.showNormal()
        if self._windowed_geometry:
            self.restoreGeometry(self._windowed_geometry)

    def _on_user_toggle_diagnostics(self, enabled: bool):
        self._diagnostics_config.enabled = enabled
        self.settings.setValue('diagnostics/enabled', enabled)
        if self.diagnostics:
            self.diagnostics.apply_config(self._diagnostics_config)
        state = "enabled" if enabled else "disabled"
        level = "SUCCESS" if enabled else "INFO"
        self.log_widget.log(f"Diagnostics logging {state}", level)

    def _refresh_diagnostics_overlay(self):
        if hasattr(self, "diagnostics"):
            self.diagnostics.refresh_layer_statuses()
            self.log_widget.log("Diagnostics overlay refreshed", "INFO")

    def _export_diagnostics_log(self):
        if not hasattr(self, "diagnostics"):
            return
        target = self._diagnostics_config.export_path
        if not target:
            target, _ = QFileDialog.getSaveFileName(
                self,
                "Export Diagnostics Log",
                str(Path.home() / "diagnostics.log"),
                "Log Files (*.log *.txt);;All Files (*)"
            )
        if not target:
            return
        success, message = self.diagnostics.export_to_file(target)
        level = "SUCCESS" if success else "ERROR"
        self.log_widget.log(message, level)
        if success:
            self._diagnostics_config.export_path = target
            self.settings.setValue('diagnostics/export_path', target)
    
    def connect_control_panel_signals(self):
        """Connect control panel signals"""
        self.control_panel.bin_selected.connect(self.on_bin_selected)
        self.control_panel.convert_bin_clicked.connect(self.convert_bin_to_json)
        self.control_panel.convert_dof_clicked.connect(self.convert_dof_to_json)
        self.control_panel.refresh_files_clicked.connect(self.refresh_active_file_list)
        self.control_panel.file_search_changed.connect(self.on_file_search_changed)
        self.control_panel.dof_search_toggled.connect(self.on_dof_search_toggled)
        self.control_panel.animation_selected.connect(self.on_animation_selected)
        self.control_panel.costume_selected.connect(self.on_costume_selected)
        self.control_panel.costume_convert_clicked.connect(self.convert_selected_costume)
        self.control_panel.scale_changed.connect(self.on_scale_changed)
        self.control_panel.fps_changed.connect(self.on_fps_changed)
        self.control_panel.position_scale_changed.connect(self.on_position_scale_changed)
        self.control_panel.position_scale_slider_changed.connect(self.on_position_scale_slider_changed)
        self.control_panel.base_world_scale_changed.connect(self.on_base_world_scale_changed)
        self.control_panel.base_world_scale_slider_changed.connect(self.on_base_world_scale_slider_changed)
        self.control_panel.translation_sensitivity_changed.connect(self.on_translation_sensitivity_changed)
        self.control_panel.rotation_sensitivity_changed.connect(self.on_rotation_sensitivity_changed)
        self.control_panel.rotation_overlay_size_changed.connect(self.on_rotation_overlay_size_changed)
        self.control_panel.rotation_gizmo_toggled.connect(self.toggle_rotation_gizmo)
        self.control_panel.anchor_overlay_toggled.connect(self.toggle_anchor_overlay)
        self.control_panel.parent_overlay_toggled.connect(self.toggle_parent_overlay)
        self.control_panel.anchor_drag_precision_changed.connect(self.on_anchor_drag_precision_changed)
        self.control_panel.bpm_value_changed.connect(self.on_bpm_value_changed)
        self.control_panel.sync_audio_to_bpm_toggled.connect(self.on_sync_audio_to_bpm_toggled)
        self.control_panel.pitch_shift_toggled.connect(self.on_pitch_shift_toggled)
        self.control_panel.metronome_toggled.connect(self.on_metronome_toggled)
        self.control_panel.metronome_audible_toggled.connect(self.on_metronome_audible_toggled)
        self.control_panel.time_signature_changed.connect(self.on_time_signature_changed)
        self.control_panel.bpm_reset_requested.connect(self.on_reset_bpm_requested)
        self.control_panel.base_bpm_lock_requested.connect(self.on_lock_base_bpm_requested)
        self.control_panel.anchor_bias_x_changed.connect(self.on_anchor_bias_x_changed)
        self.control_panel.anchor_bias_y_changed.connect(self.on_anchor_bias_y_changed)
        self.control_panel.anchor_flip_x_changed.connect(self.on_anchor_flip_x_changed)
        self.control_panel.anchor_flip_y_changed.connect(self.on_anchor_flip_y_changed)
        self.control_panel.anchor_scale_x_changed.connect(self.on_anchor_scale_x_changed)
        self.control_panel.anchor_scale_y_changed.connect(self.on_anchor_scale_y_changed)
        self.control_panel.local_position_multiplier_changed.connect(self.on_local_position_multiplier_changed)
        self.control_panel.parent_mix_changed.connect(self.on_parent_mix_changed)
        self.control_panel.rotation_bias_changed.connect(self.on_rotation_bias_changed)
        self.control_panel.scale_bias_x_changed.connect(self.on_scale_bias_x_changed)
        self.control_panel.scale_bias_y_changed.connect(self.on_scale_bias_y_changed)
        self.control_panel.world_offset_x_changed.connect(self.on_world_offset_x_changed)
        self.control_panel.world_offset_y_changed.connect(self.on_world_offset_y_changed)
        self.control_panel.particle_origin_offset_x_changed.connect(self.on_particle_origin_offset_x_changed)
        self.control_panel.particle_origin_offset_y_changed.connect(self.on_particle_origin_offset_y_changed)
        self.control_panel.trim_shift_multiplier_changed.connect(self.on_trim_shift_multiplier_changed)
        self.control_panel.reset_camera_clicked.connect(self.reset_camera)
        self.control_panel.fit_to_view_clicked.connect(self.fit_to_view)
        self.control_panel.show_bones_toggled.connect(self.toggle_bone_overlay)
        self.control_panel.reset_offsets_clicked.connect(self.reset_sprite_offsets)
        self.control_panel.export_frame_clicked.connect(self.export_current_frame)
        self.control_panel.export_frames_sequence_clicked.connect(self.export_animation_frames_as_png)
        self.control_panel.export_psd_clicked.connect(self.export_as_psd)
        self.control_panel.export_ae_rig_clicked.connect(self.export_as_ae_rig)
        self.control_panel.export_mov_clicked.connect(self.export_as_mov)
        self.control_panel.export_mp4_clicked.connect(self.export_as_mp4)
        self.control_panel.export_webm_clicked.connect(self.export_as_webm)
        self.control_panel.export_gif_clicked.connect(self.export_as_gif)
        self.control_panel.credits_clicked.connect(self.show_credits)
        self.control_panel.monster_browser_requested.connect(self.open_monster_browser)
        self.control_panel.solid_bg_enabled_changed.connect(self.on_solid_bg_enabled_changed)
        self.control_panel.solid_bg_color_changed.connect(self.on_solid_bg_color_changed)
        self.control_panel.solid_bg_auto_requested.connect(self.on_auto_background_color_requested)
        self.control_panel.viewport_bg_enabled_changed.connect(
            self.on_viewport_bg_enabled_changed
        )
        self.control_panel.viewport_bg_keep_aspect_changed.connect(
            self.on_viewport_bg_keep_aspect_changed
        )
        self.control_panel.viewport_bg_zoom_fill_changed.connect(
            self.on_viewport_bg_zoom_fill_changed
        )
        self.control_panel.viewport_bg_parallax_enabled_changed.connect(
            self.on_viewport_bg_parallax_enabled_changed
        )
        self.control_panel.viewport_bg_parallax_zoom_strength_changed.connect(
            self.on_viewport_bg_parallax_zoom_strength_changed
        )
        self.control_panel.viewport_bg_parallax_pan_strength_changed.connect(
            self.on_viewport_bg_parallax_pan_strength_changed
        )
        self.control_panel.viewport_bg_flip_h_changed.connect(
            self.on_viewport_bg_flip_h_changed
        )
        self.control_panel.viewport_bg_flip_v_changed.connect(
            self.on_viewport_bg_flip_v_changed
        )
        self.control_panel.viewport_bg_image_enabled_changed.connect(
            self.on_viewport_bg_image_enabled_changed
        )
        self.control_panel.viewport_bg_image_changed.connect(self.on_viewport_bg_image_changed)
        self.control_panel.viewport_bg_color_mode_changed.connect(
            self.on_viewport_bg_color_mode_changed
        )
        self.control_panel.export_include_viewport_bg_changed.connect(
            self.on_export_include_viewport_bg_changed
        )
        self.control_panel.audio_enabled_changed.connect(self.on_audio_enabled_changed)
        self.control_panel.audio_volume_changed.connect(self.on_audio_volume_changed)
        self.control_panel.audio_track_mute_changed.connect(self.on_audio_track_mute_changed)
        self.control_panel.antialias_toggled.connect(self.toggle_antialiasing)
        self.control_panel.save_offsets_clicked.connect(self.save_layer_offsets)
        self.control_panel.load_offsets_clicked.connect(self.load_layer_offsets)
        self.control_panel.nudge_x_changed.connect(self.on_nudge_x)
        self.control_panel.nudge_y_changed.connect(self.on_nudge_y)
        self.control_panel.nudge_rotation_changed.connect(self.on_nudge_rotation)
        self.control_panel.nudge_scale_x_changed.connect(self.on_nudge_scale_x)
        self.control_panel.nudge_scale_y_changed.connect(self.on_nudge_scale_y)
        self.control_panel.scale_gizmo_toggled.connect(self.toggle_scale_gizmo)
        self.control_panel.scale_gizmo_mode_changed.connect(self.on_scale_mode_changed)
        self.control_panel.diagnostics_enabled_changed.connect(self._on_user_toggle_diagnostics)
        self.control_panel.diagnostics_refresh_requested.connect(self._refresh_diagnostics_overlay)
        self.control_panel.diagnostics_export_requested.connect(self._export_diagnostics_log)
        self.control_panel.pose_record_clicked.connect(self.on_record_pose_clicked)
        self.control_panel.pose_mode_changed.connect(self.on_pose_influence_changed)
        self.control_panel.pose_reset_clicked.connect(self.on_reset_pose_clicked)
        self.control_panel.keyframe_undo_clicked.connect(self.undo_keyframe_action)
        self.control_panel.keyframe_redo_clicked.connect(self.redo_keyframe_action)
        self.control_panel.keyframe_delete_others_clicked.connect(self.delete_other_keyframes)
        self.control_panel.extend_duration_clicked.connect(self.extend_animation_duration_dialog)
        self.control_panel.save_animation_clicked.connect(self.save_animation_to_file)
        self.control_panel.export_animation_bin_clicked.connect(self.export_animation_to_bin)
        self.control_panel.load_animation_clicked.connect(self.load_saved_animation)
        self.control_panel.sprite_assign_clicked.connect(self.assign_sprite_to_keyframes)
        self.control_panel.constraints_enabled_changed.connect(self.on_constraints_enabled_changed)
        self.control_panel.constraint_item_toggled.connect(self.on_constraint_item_toggled)
        self.control_panel.constraint_add_requested.connect(self.on_constraint_add_requested)
        self.control_panel.constraint_edit_requested.connect(self.on_constraint_edit_requested)
        self.control_panel.constraint_remove_requested.connect(self.on_constraint_remove_requested)
        self.control_panel.joint_solver_enabled_changed.connect(self.on_joint_solver_enabled_changed)
        self.control_panel.joint_solver_iterations_changed.connect(self.on_joint_solver_iterations_changed)
        self.control_panel.joint_solver_strength_changed.connect(self.on_joint_solver_strength_changed)
        self.control_panel.joint_solver_parented_changed.connect(self.on_joint_solver_parented_changed)
        self.control_panel.propagate_user_transforms_changed.connect(
            self.on_propagate_user_transforms_changed
        )
        self.control_panel.preserve_children_on_record_changed.connect(
            self.on_preserve_children_on_record_changed
        )
        self.control_panel.joint_solver_capture_requested.connect(self.on_joint_solver_capture_requested)
        self.control_panel.joint_solver_bake_current_requested.connect(self.on_joint_solver_bake_current_requested)
        self.control_panel.joint_solver_bake_range_requested.connect(self.on_joint_solver_bake_range_requested)
        self.control_panel.set_barebones_file_mode(self.export_settings.use_barebones_file_browser)
        self._update_keyframe_history_controls()
        self.control_panel.compact_ui_toggled.connect(self.on_compact_ui_toggled)
        if hasattr(self.control_panel, "dof_include_mesh_xml_checkbox"):
            self.control_panel.dof_include_mesh_xml_checkbox.toggled.connect(
                self._on_control_panel_dof_include_mesh_xml_toggled
            )

    
    def connect_layer_panel_signals(self):
        """Connect layer panel signals"""
        self.layer_panel.layer_visibility_changed.connect(self.toggle_layer_visibility)
        self.layer_panel.layer_visibility_changed.connect(self._on_layer_visibility_logged)
        self.layer_panel.layer_selection_changed.connect(self.on_layer_selection_changed)
        self.layer_panel.attachment_selection_changed.connect(self.on_attachment_selection_changed)
        self.layer_panel.attachment_visibility_changed.connect(self.on_attachment_visibility_changed)
        self.layer_panel.selection_lock_toggled.connect(self.on_selection_lock_toggled)
        self.layer_panel.all_layers_deselected.connect(self.on_layer_selection_cleared)
        self.layer_panel.color_changed.connect(self.on_layer_color_changed)
        self.layer_panel.color_reset_requested.connect(self.on_layer_color_reset)
        self.layer_panel.color_keyframe_requested.connect(self.on_layer_color_keyframe_requested)
        self.layer_panel.layer_order_changed.connect(self.on_layer_order_changed)
        self.layer_panel.reset_layer_order_requested.connect(self.reset_layer_order_to_default)
        self.layer_panel.reset_layer_visibility_requested.connect(self.reset_layer_visibility_to_default)
        self.layer_panel.export_layer_readouts_requested.connect(self.export_layer_readouts)
        self.layer_panel.layer_constraints_toggled.connect(self.on_layer_constraints_toggled)
        self.layer_panel.sprite_assign_requested.connect(
            lambda layer_id: self.assign_sprite_to_keyframes([layer_id])
        )

    def _on_layer_visibility_logged(self, layer: LayerData, state: int):
        if not hasattr(self, "diagnostics"):
            return
        if state == Qt.CheckState.Checked:
            text = "visible"
        elif state == Qt.CheckState.PartiallyChecked:
            text = "partially visible"
        else:
            text = "hidden"
        self.diagnostics.log_visibility(
            f"Layer '{layer.name}' set {text}", layer_id=layer.layer_id
        )
    
    def connect_timeline_signals(self):
        """Connect timeline signals"""
        self.timeline.play_toggled.connect(self.toggle_playback)
        self.timeline.loop_toggled.connect(self.toggle_loop)
        self.timeline.time_changed.connect(self.on_timeline_changed)
        self.timeline.keyframe_marker_clicked.connect(self.on_keyframe_marker_clicked)
        self.timeline.keyframe_marker_remove_requested.connect(self.on_keyframe_marker_remove_requested)
        self.timeline.keyframe_marker_dragged.connect(self.on_keyframe_marker_dragged)
        self.timeline.keyframe_selection_changed.connect(self.on_keyframe_selection_changed)
        self.timeline.beat_marker_dragged.connect(self.on_beat_marker_dragged)
        self.timeline.timeline_slider.sliderPressed.connect(self.on_timeline_slider_pressed)
        self.timeline.timeline_slider.sliderReleased.connect(self.on_timeline_slider_released)
        self.timeline.keyframe_lane_add_requested.connect(self.on_keyframe_lane_add_requested)
        self.timeline.keyframe_lane_remove_requested.connect(self.on_keyframe_lane_remove_requested)

    def _init_diagnostics(self):
        self.diagnostics = DiagnosticsManager(self.layer_panel, self.log_widget, self)
        self._diagnostics_config = DiagnosticsConfig()
        self._load_diagnostics_settings()

    def _load_diagnostics_settings(self):
        s = self.settings
        get_bool = lambda key, default: s.value(f"diagnostics/{key}", default, type=bool)
        get_int = lambda key, default: s.value(f"diagnostics/{key}", default, type=int)
        get_float = lambda key, default: s.value(f"diagnostics/{key}", default, type=float)
        get_str = lambda key, default: s.value(f"diagnostics/{key}", default, type=str)

        cfg = DiagnosticsConfig(
            enabled=get_bool("enabled", False),
            highlight_layers=get_bool("highlight_layers", True),
            throttle_updates=get_bool("throttle_updates", True),
            log_clone_events=get_bool("log_clone_events", True),
            log_canonical_events=get_bool("log_canonical_events", True),
            log_remap_events=get_bool("log_remap_events", False),
            log_sheet_events=get_bool("log_sheet_events", False),
            log_visibility_events=get_bool("log_visibility_events", False),
            log_shader_events=get_bool("log_shader_events", False),
            log_color_events=get_bool("log_color_events", False),
            log_attachment_events=get_bool("log_attachment_events", False),
            include_debug_payloads=get_bool("include_debug_payloads", False),
            max_entries=get_int("max_entries", 2000),
            update_interval_ms=get_int("update_interval_ms", 500),
            layer_status_duration_sec=get_float("layer_status_duration_sec", 6.0),
            rate_limit_per_sec=get_int("rate_limit_per_sec", 120),
            minimum_severity=get_str("minimum_severity", "INFO"),
            auto_export_enabled=get_bool("auto_export_enabled", False),
            auto_export_interval_sec=get_int("auto_export_interval_sec", 120),
            export_path=get_str("export_path", ""),
        )
        self._diagnostics_config = cfg
        self.diagnostics.apply_config(cfg)
        self.control_panel.set_diagnostics_enabled(cfg.enabled)
    
    def find_bin2json(self):
        """Locate converter utilities shipped with the viewer."""
        script_dir = self.project_root
        resources_dir = script_dir / "Resources"
        bin2json_path = resources_dir / "bin2json" / "rev6-2-json.py"
        legacy_tool_path = resources_dir / "bin2json" / "tokenize_legacy_bin.py"
        legacy_converter_path = resources_dir / "bin2json" / "legacy_bin_to_json.py"
        choir_converter_path = resources_dir / "bin2json" / "choir_bin_to_json.py"
        muppets_converter_path = resources_dir / "bin2json" / "muppets_bin_to_json.py"
        oldest_converter_path = resources_dir / "bin2json" / "oldest_bin_to_json.py"
        composer_converter_path = resources_dir / "bin2json" / "composer_bin_to_json.py"
        rev4_converter_path = resources_dir / "bin2json" / "rev4-2-json.py"
        rev2_converter_path = resources_dir / "bin2json" / "rev2-2-json.py"
        dof_converter_path = resources_dir / "bin2json" / "dof_anim_to_json.py"

        if bin2json_path.exists():
            self.bin2json_path = str(bin2json_path)
            self.log_widget.log(f"Found bin2json script: {self.bin2json_path}", "SUCCESS")
        else:
            self.log_widget.log("bin2json script not found", "WARNING")

        if legacy_tool_path.exists():
            self.legacy_tokenizer_path = str(legacy_tool_path)
            self.log_widget.log(
                f"Legacy BIN tokenizer available: {self.legacy_tokenizer_path}",
                "INFO",
            )
        else:
            self.log_widget.log(
                "Legacy BIN tokenizer missing; legacy files cannot be dumped automatically.",
                "WARNING",
            )

        if legacy_converter_path.exists():
            self.legacy_bin2json_path = str(legacy_converter_path)
            self.log_widget.log(
                f"Legacy BIN converter available: {self.legacy_bin2json_path}",
                "INFO",
            )
        else:
            self.log_widget.log(
                "Legacy BIN converter missing; older BINs cannot be parsed automatically.",
                "WARNING",
            )
        if choir_converter_path.exists():
            self.choir_bin2json_path = str(choir_converter_path)
            self.log_widget.log(
                f"Choir BIN converter available: {self.choir_bin2json_path}",
                "INFO",
            )
        else:
            self.choir_bin2json_path = None
            self.log_widget.log(
                "Choir BIN converter missing; Monster Choir BINs require manual conversion.",
                "INFO",
            )
        if muppets_converter_path.exists():
            self.muppets_bin2json_path = str(muppets_converter_path)
            self.log_widget.log(
                f"Muppets BIN converter available: {self.muppets_bin2json_path}",
                "INFO",
            )
        else:
            self.muppets_bin2json_path = None
            self.log_widget.log(
                "Muppets BIN converter missing; muppet_* BINs require manual conversion.",
                "INFO",
            )
        if oldest_converter_path.exists():
            self.oldest_bin2json_path = str(oldest_converter_path)
            self.log_widget.log(
                f"Oldest BIN converter available: {self.oldest_bin2json_path}",
                "INFO",
            )
        else:
            self.oldest_bin2json_path = None
            self.log_widget.log(
                "Oldest BIN converter missing; launch-build BINs require manual conversion.",
                "INFO",
            )
        if composer_converter_path.exists():
            self.composer_bin2json_path = str(composer_converter_path)
            self.log_widget.log(
                f"Composer BIN converter available: {self.composer_bin2json_path}",
                "INFO",
            )
        else:
            self.composer_bin2json_path = ""
            self.log_widget.log(
                "Composer BIN converter missing; composer_* BINs require manual conversion.",
                "INFO",
            )
        if rev4_converter_path.exists():
            self.rev4_bin2json_path = str(rev4_converter_path)
            self.log_widget.log(
                f"Rev4 BIN converter available: {self.rev4_bin2json_path}",
                "INFO",
            )
        else:
            self.rev4_bin2json_path = None
            self.log_widget.log(
                "Rev4 BIN converter missing; classic app builds require manual conversion.",
                "INFO",
            )
        if rev2_converter_path.exists():
            self.rev2_bin2json_path = str(rev2_converter_path)
            self.log_widget.log(
                f"Rev2 BIN converter available: {self.rev2_bin2json_path}",
                "INFO",
            )
        else:
            self.rev2_bin2json_path = None
            self.log_widget.log(
                "Rev2 BIN converter missing; My Singing Muppets files require manual conversion.",
                "INFO",
            )
        if dof_converter_path.exists():
            self.dof_anim_to_json_path = str(dof_converter_path)
            self.log_widget.log(
                f"DOF converter available: {self.dof_anim_to_json_path}",
                "INFO",
            )
        else:
            self.dof_anim_to_json_path = None
            self.log_widget.log(
                "DOF converter missing; Down of the Fare exports cannot be converted.",
                "INFO",
            )

    def build_audio_library(self):
        """Index audio/music files under the selected game path."""
        self.audio_library.clear()
        self._game_music_dirs = []
        candidate_dirs: List[str] = []
        if self.downloads_path:
            candidate_dirs.extend(
                [
                    os.path.join(self.downloads_path, "audio", "music"),
                    os.path.join(self.downloads_path, "data", "audio", "music"),
                ]
            )
        if self.game_path:
            candidate_dirs.append(os.path.join(self.game_path, "data", "audio", "music"))
        for candidate in candidate_dirs:
            if os.path.isdir(candidate):
                norm = os.path.normpath(candidate)
                if norm not in self._game_music_dirs:
                    self._game_music_dirs.append(norm)

        if not self._game_music_dirs:
            if self.game_path:
                self.log_widget.log(
                    f"Music folder not found: {os.path.join(self.game_path, 'data', 'audio', 'music')}",
                    "WARNING",
                )
            if self.downloads_path:
                self.log_widget.log(
                    "Music folder not found under downloads path.",
                    "WARNING",
                )
            return

        total_files = self._index_audio_files(self._game_music_dirs, self.audio_library)
        self.log_widget.log(f"Indexed {total_files} music clips", "INFO")
        if self.current_animation_name:
            self.load_audio_for_animation(self.current_animation_name)
        self._load_buddy_audio_tracks()

    def build_dof_audio_library(self):
        """Index DOF audio files under the selected DOF root."""
        self.dof_audio_library.clear()
        self._dof_music_dirs = []
        if not self.dof_path:
            return
        candidate_dirs = [
            os.path.join(self.dof_path, "data", "audio", "music"),
            os.path.join(self.dof_path, "audio", "music"),
            os.path.join(self.dof_path, "msmdata", "audio", "music"),
            os.path.join(self.dof_path, "audiomusic"),
        ]
        for candidate in candidate_dirs:
            if os.path.isdir(candidate):
                norm = os.path.normpath(candidate)
                if norm not in self._dof_music_dirs:
                    self._dof_music_dirs.append(norm)
        if not self._dof_music_dirs:
            self.log_widget.log(
                f"DOF music folder not found under: {self.dof_path}",
                "INFO",
            )
            return
        total_files = self._index_audio_files(self._dof_music_dirs, self.dof_audio_library)
        self.log_widget.log(f"Indexed {total_files} DOF music clips", "INFO")
        if self.current_animation_name and self._is_active_dof_audio_context():
            self.load_audio_for_animation(self.current_animation_name)

    def _index_audio_files(self, music_dirs: List[str], target_library: Dict[str, List[str]]) -> int:
        total_files = 0
        seen_paths: Set[str] = set()
        for music_dir in music_dirs:
            if not os.path.isdir(music_dir):
                continue
            for root, _, files in os.walk(music_dir):
                for file in files:
                    lower = file.lower()
                    if not (lower.endswith(".ogg") or lower.endswith(".wav") or lower.endswith(".mp3")):
                        continue
                    key = self._normalize_audio_key(Path(file).stem)
                    if not key:
                        continue
                    full_path = os.path.normpath(os.path.join(root, file))
                    norm_key = os.path.normcase(full_path)
                    if norm_key in seen_paths:
                        continue
                    seen_paths.add(norm_key)
                    paths = target_library.setdefault(key, [])
                    if full_path not in paths:
                        paths.append(full_path)
                        total_files += 1
        return total_files

    def _load_buddy_audio_tracks(self):
        """Parse buddy manifests (001_*.bin) to map animations directly to audio files."""
        self.buddy_audio_tracks.clear()
        self.buddy_audio_tracks_normalized.clear()
        self.buddy_audio_blocked_tracks.clear()
        self.buddy_audio_blocked_tracks_normalized.clear()
        if not self.game_path and not self.downloads_path:
            return
        xml_bin_roots = self._all_xml_bin_roots()
        if not xml_bin_roots:
            if self.game_path:
                xml_bin_dir = os.path.join(self.game_path, "data", "xml_bin")
                self.log_widget.log(f"Buddy manifest folder missing: {xml_bin_dir}", "WARNING")
            return

        def is_buddy_manifest(path: str) -> bool:
            try:
                with open(path, "rb") as handle:
                    header = handle.read(4)
                    if len(header) < 4:
                        return False
                    (length,) = struct.unpack("<I", header)
                    if length <= 0 or length > 0x100:
                        return False
                    sig_bytes = handle.read(max(length - 1, 0))
                    signature = sig_bytes.decode("ascii", errors="ignore").strip().lower()
                    return signature == "budd"
            except Exception:
                return False

        def add_track_mapping(track_name: str, abs_path: str) -> bool:
            normalized_name = self._normalize_audio_key(track_name)
            if track_name in self.buddy_audio_tracks:
                if normalized_name and normalized_name not in self.buddy_audio_tracks_normalized:
                    self.buddy_audio_tracks_normalized[normalized_name] = abs_path
                return False
            self.buddy_audio_tracks[track_name] = abs_path
            if normalized_name and normalized_name not in self.buddy_audio_tracks_normalized:
                self.buddy_audio_tracks_normalized[normalized_name] = abs_path
            return True

        def next_incremented_alias(track_name: str) -> Optional[str]:
            match = re.match(r"^(.*?)(\d+)$", track_name)
            if not match:
                return None
            prefix, number_text = match.groups()
            width = len(number_text)
            try:
                base_number = int(number_text)
            except ValueError:
                return None
            for candidate_number in range(base_number + 1, base_number + 100):
                alias = f"{prefix}{candidate_number:0{width}d}"
                if alias not in self.buddy_audio_tracks:
                    return alias
            return None

        manifest_entries: List[Tuple[str, str]] = []
        for xml_bin_dir in xml_bin_roots:
            if not os.path.isdir(xml_bin_dir):
                continue
            data_root = str(Path(xml_bin_dir).parent)
            manifest_paths = sorted(
                set(
                    glob(os.path.join(xml_bin_dir, "[0-9][0-9][0-9]_*.bin"))
                    + glob(os.path.join(xml_bin_dir, "[0-9][0-9][0-9]-*.bin"))
                )
            )
            manifest_paths = [path for path in manifest_paths if is_buddy_manifest(path)]
            if not manifest_paths:
                manifest_paths = [
                    path for path in sorted(glob(os.path.join(xml_bin_dir, "*.bin")))
                    if is_buddy_manifest(path)
                ]
            for manifest_path in manifest_paths:
                manifest_entries.append((manifest_path, data_root))

        if not manifest_entries:
            self.log_widget.log("No buddy manifest files found in xml_bin", "INFO")
            return

        total_tracks = 0
        parsed_files = 0
        for manifest_path, data_root in manifest_entries:
            try:
                manifest = BuddyManifest.from_file(manifest_path)
            except Exception as exc:
                self.log_widget.log(
                    f"Failed to parse {os.path.basename(manifest_path)}: {exc}",
                    "WARNING"
                )
                continue

            parsed_files += 1
            for track_name, rel_audio in manifest.iter_audio_links():
                normalized_name = self._normalize_audio_key(track_name)
                if not rel_audio:
                    self.buddy_audio_blocked_tracks.add(track_name)
                    if normalized_name:
                        self.buddy_audio_blocked_tracks_normalized.add(normalized_name)
                    continue
                abs_path = os.path.join(data_root, rel_audio.replace("/", os.sep))
                if add_track_mapping(track_name, abs_path):
                    total_tracks += 1
                    continue

                existing_path = self.buddy_audio_tracks.get(track_name)
                if existing_path and os.path.normcase(existing_path) == os.path.normcase(abs_path):
                    continue
                if "_monster_" not in normalized_name:
                    continue
                alias_name = next_incremented_alias(track_name)
                if alias_name and add_track_mapping(alias_name, abs_path):
                    total_tracks += 1

        if parsed_files:
            self.log_widget.log(
                f"Loaded {total_tracks} buddy audio links from {parsed_files} manifests",
                "INFO"
            )

    def _update_path_label(self) -> None:
        """Update the toolbar label to reflect the current game/downloads paths."""
        if self.game_path:
            label = f"Game Path: {self.game_path}"
        else:
            label = "Game Path: Not Set"
        if self.downloads_path:
            label = f"{label} (Downloads: set)"
        self.path_label.setText(label)
        if self.downloads_path:
            self.path_label.setToolTip(f"Downloads: {self.downloads_path}")
        else:
            self.path_label.setToolTip("")
    
    def browse_game_path(self):
        """Browse for game path"""
        path = QFileDialog.getExistingDirectory(self, "Select My Singing Monsters Game Folder")
        if path:
            # Check if it's a valid game path
            data_path = os.path.join(path, "data")
            if os.path.exists(data_path):
                self.game_path = path
                self.shader_registry.set_game_path(self.game_path)
                self.settings.setValue('game_path', path)
                self._update_path_label()
                self.log_widget.log(f"Game path set to: {path}", "SUCCESS")
                self.build_audio_library()
                self.refresh_file_list()
            else:
                QMessageBox.warning(self, "Invalid Path", 
                                  "Selected folder doesn't contain a 'data' subfolder. "
                                  "Please select the root game folder.")
                self.log_widget.log("Invalid game path selected", "ERROR")

    def browse_dof_path(self):
        """Browse for Down of the Fare Assets path."""
        start_dir = self.dof_path or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select DOF Assets Folder", start_dir)
        if not folder:
            return
        if not os.path.isdir(folder):
            QMessageBox.warning(self, "Invalid Path", "Selected folder is not a directory.")
            return
        msmdata = os.path.join(folder, "msmdata")
        bundle_mode = self._is_dof_bundle_root(folder)
        if not os.path.isdir(msmdata) and not bundle_mode:
            self.log_widget.log(
                "Selected DOF path does not contain an msmdata folder; conversion may fail.",
                "WARNING",
            )
        if bundle_mode:
            self.log_widget.log("Detected Unity bundle layout for DOF assets.", "INFO")
        self.dof_path = folder
        self.settings.setValue('dof_path', folder)
        self._dof_particle_library_root = None
        self._dof_particle_library = None
        self._dof_particle_entry_cache.clear()
        self._dof_control_point_cache.clear()
        self._dof_source_node_cache.clear()
        self.log_widget.log(f"DOF Assets path set to: {folder}", "SUCCESS")
        self.build_dof_audio_library()
        self.refresh_dof_file_list()

    def browse_downloads_path(self) -> None:
        """Browse for the game downloads folder."""
        start_dir = self.downloads_path or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select MSM Downloads Folder", start_dir)
        if not folder:
            return
        if not os.path.isdir(folder):
            QMessageBox.warning(self, "Invalid Path", "Selected folder is not a directory.")
            return
        self.downloads_path = folder
        self.settings.setValue('downloads_path', folder)
        self._downloads_xml_bin_roots = self._find_downloads_xml_bin_roots()
        self._update_path_label()
        self.log_widget.log(f"Downloads path set to: {folder}", "SUCCESS")
        if self.downloads_path and not self._downloads_xml_bin_roots:
            self.log_widget.log("No xml_bin folders found under downloads path.", "WARNING")
        self.build_audio_library()
        self.refresh_file_list()

    def _get_downloads_xml_bin_roots(self) -> List[str]:
        """Return cached downloads xml_bin roots, refreshing if needed."""
        if self.downloads_path and not self._downloads_xml_bin_roots:
            self._downloads_xml_bin_roots = self._find_downloads_xml_bin_roots()
        return list(self._downloads_xml_bin_roots)

    def _find_downloads_xml_bin_roots(self) -> List[str]:
        """Locate xml_bin folders within the downloads path."""
        roots: List[str] = []
        if not self.downloads_path or not os.path.isdir(self.downloads_path):
            return roots

        seen: Set[str] = set()

        def add_root(path: str) -> None:
            if not path:
                return
            if not os.path.isdir(path):
                return
            norm = os.path.normcase(os.path.normpath(path))
            if norm in seen:
                return
            seen.add(norm)
            roots.append(path)

        direct_candidates = [
            self.downloads_path,
            os.path.join(self.downloads_path, "xml_bin"),
            os.path.join(self.downloads_path, "data", "xml_bin"),
        ]
        for candidate in direct_candidates:
            if os.path.basename(candidate).lower() == "xml_bin":
                add_root(candidate)

        max_depth = 6
        for root, dirs, _ in os.walk(self.downloads_path):
            rel = os.path.relpath(root, self.downloads_path)
            depth = 0 if rel == "." else len(rel.split(os.sep))
            if depth > max_depth:
                dirs[:] = []
                continue
            if os.path.basename(root).lower() == "xml_bin":
                add_root(root)
                dirs[:] = []

        roots.sort(key=lambda value: value.lower())
        return roots

    def _all_xml_bin_roots(self) -> List[str]:
        """Return all xml_bin roots in priority order (downloads first)."""
        roots: List[str] = []
        seen: Set[str] = set()
        for path in self._get_downloads_xml_bin_roots():
            norm = os.path.normcase(os.path.normpath(path))
            if norm in seen:
                continue
            seen.add(norm)
            roots.append(path)
        if self.game_path:
            game_root = os.path.join(self.game_path, "data", "xml_bin")
            if os.path.isdir(game_root):
                norm = os.path.normcase(os.path.normpath(game_root))
                if norm not in seen:
                    roots.append(game_root)
        return roots

    def _resolve_xml_bin_relative_path(self, relative_path: str) -> Optional[str]:
        """Resolve a relative xml_bin path across downloads + game roots."""
        if not relative_path:
            return None
        cleaned = relative_path.replace("\\", "/").lstrip("./")
        if os.path.isabs(cleaned) and os.path.exists(cleaned):
            return cleaned
        for root in self._all_xml_bin_roots():
            candidate = os.path.normpath(os.path.join(root, cleaned))
            if os.path.exists(candidate):
                return candidate
        if self.game_path:
            return os.path.normpath(os.path.join(self.game_path, "data", "xml_bin", cleaned))
        return None

    def _relative_xml_bin_display(self, path: str) -> str:
        """Return a friendly display path relative to a known xml_bin root."""
        if not path:
            return ""
        for root in self._all_xml_bin_roots():
            try:
                rel = os.path.relpath(path, root)
            except ValueError:
                continue
            if not rel.startswith(".."):
                return rel.replace("\\", "/")
        return os.path.basename(path)

    def refresh_active_file_list(self):
        """Refresh the active file list depending on the search mode."""
        if self.dof_search_enabled:
            self.refresh_dof_file_list()
        else:
            self.refresh_file_list()
    
    def refresh_file_list(self):
        """Refresh the list of available BIN/JSON files"""
        if not self.game_path and not self.downloads_path:
            self.log_widget.log("No game or downloads path set", "WARNING")
            return

        game_xml_bin: Optional[str] = None
        if self.game_path:
            candidate = os.path.join(self.game_path, "data", "xml_bin")
            if os.path.isdir(candidate):
                game_xml_bin = candidate
            else:
                self.log_widget.log(f"xml_bin folder not found: {candidate}", "ERROR")

        if self.downloads_path:
            self._downloads_xml_bin_roots = self._find_downloads_xml_bin_roots()
        else:
            self._downloads_xml_bin_roots = []

        if self.downloads_path and not self._downloads_xml_bin_roots:
            self.log_widget.log("No xml_bin folders found under downloads path.", "WARNING")

        xml_bin_roots = list(self._downloads_xml_bin_roots)
        if game_xml_bin:
            xml_bin_roots.append(game_xml_bin)

        if not xml_bin_roots:
            self.log_widget.log("No xml_bin folders available for indexing.", "ERROR")
            return

        entries_by_relative: Dict[str, AnimationFileEntry] = {}
        self.xml_bin_file_map = {}
        overrides = 0
        for base_root in xml_bin_roots:
            for root, _, files in os.walk(base_root):
                for file in files:
                    lower = file.lower()
                    if not (lower.endswith('.bin') or lower.endswith('.json')):
                        continue
                    full_path = os.path.normpath(os.path.join(root, file))
                    relative_path = os.path.relpath(full_path, base_root).replace("\\", "/")
                    rel_key = relative_path.lower()
                    if rel_key in entries_by_relative:
                        overrides += 1
                        continue
                    entry = AnimationFileEntry(
                        name=file,
                        relative_path=relative_path,
                        full_path=full_path
                    )
                    entries_by_relative[rel_key] = entry
                    if entry.is_bin:
                        lower_name = entry.name.lower()
                        if lower_name not in self.xml_bin_file_map:
                            self.xml_bin_file_map[lower_name] = entry.full_path

        indexed_files = sorted(entries_by_relative.values(), key=lambda entry: entry.relative_path.lower())
        self.file_index = indexed_files
        self._tileset_cache.clear()
        self._grid_cache.clear()
        self._binary_atlas_cache.clear()
        self._rebuild_monster_lookup(indexed_files)
        if overrides:
            self.log_widget.log(
                f"Indexed {len(indexed_files)} BIN/JSON files (downloads overrides: {overrides})",
                "INFO",
            )
        else:
            self.log_widget.log(f"Indexed {len(indexed_files)} BIN/JSON files", "INFO")
        self.apply_file_filter()

    def refresh_dof_file_list(self):
        """Refresh the list of available DOF animation assets + outputs."""
        if not self.dof_path:
            self.dof_file_index = []
            self.filtered_dof_file_index = []
            if self.dof_search_enabled:
                self.apply_file_filter()
            return
        if not os.path.isdir(self.dof_path):
            self.log_widget.log(f"DOF Assets folder not found: {self.dof_path}", "ERROR")
            self.dof_file_index = []
            self.filtered_dof_file_index = []
            if self.dof_search_enabled:
                self.apply_file_filter()
            return

        bundle_mode = self._is_dof_bundle_root(self.dof_path)
        self._dof_bundle_mode = bundle_mode
        indexed_files: List[AnimationFileEntry] = []
        if bundle_mode:
            anim_names = self._get_dof_bundle_anim_names(self.dof_path)
            for anim_name in anim_names:
                bundle_path = f"bundle://{anim_name}"
                indexed_files.append(
                    AnimationFileEntry(
                        name=anim_name,
                        relative_path=bundle_path,
                        full_path=bundle_path,
                    )
                )
        else:
            for root, _, files in os.walk(self.dof_path):
                for file in files:
                    lower = file.lower()
                    if lower.endswith(".animbbb.asset"):
                        full_path = os.path.normpath(os.path.join(root, file))
                        relative_path = os.path.relpath(full_path, self.dof_path).replace("\\", "/")
                        indexed_files.append(
                            AnimationFileEntry(
                                name=file,
                                relative_path=relative_path,
                                full_path=full_path,
                            )
                        )

        output_root = os.path.join(self.dof_path, "Output")
        if os.path.isdir(output_root):
            for root, _, files in os.walk(output_root):
                for file in files:
                    if not file.lower().endswith(".json"):
                        continue
                    full_path = os.path.normpath(os.path.join(root, file))
                    relative_path = os.path.relpath(full_path, self.dof_path).replace("\\", "/")
                    indexed_files.append(
                        AnimationFileEntry(
                            name=file,
                            relative_path=relative_path,
                            full_path=full_path,
                        )
                    )

        indexed_files.sort(key=lambda entry: entry.relative_path.lower())
        self.dof_file_index = indexed_files
        if self.dof_search_enabled:
            mode_label = "bundle" if bundle_mode else "asset"
            self.log_widget.log(f"Indexed {len(indexed_files)} DOF {mode_label} entries", "INFO")
            self.apply_file_filter()

    @staticmethod
    def _looks_like_unity_bundle(path: str) -> bool:
        try:
            with open(path, "rb") as handle:
                magic = handle.read(8)
        except OSError:
            return False
        return magic.startswith(b"UnityFS") or magic.startswith(b"UnityRaw") or magic.startswith(b"UnityWeb")

    def _find_unity_bundle_data_files(self, root: str) -> List[str]:
        data_files: List[str] = []
        for dirpath, _, filenames in os.walk(root):
            if "__data" not in filenames:
                continue
            candidate = os.path.join(dirpath, "__data")
            if self._looks_like_unity_bundle(candidate):
                data_files.append(candidate)
        return data_files

    def _is_dof_bundle_root(self, root: str) -> bool:
        if not root or not os.path.isdir(root):
            return False
        msmdata_dir = os.path.join(root, "msmdata")
        if os.path.isdir(msmdata_dir):
            return False
        return bool(self._find_unity_bundle_data_files(root))

    def _get_dof_bundle_anim_names(self, root: str) -> List[str]:
        if self._dof_bundle_cache_path == root and self._dof_bundle_anim_cache:
            return list(self._dof_bundle_anim_cache)

        try:
            import UnityPy  # type: ignore
        except Exception:
            self.log_widget.log(
                "UnityPy is required to scan bundle animations. Install it to list DOF bundle files.",
                "WARNING",
            )
            self._dof_bundle_cache_path = root
            self._dof_bundle_anim_cache = []
            return []

        cache_path = self._dof_bundle_index_cache_path(root)
        cached = self._read_bundle_index_cache(cache_path, root)
        if cached:
            self._dof_bundle_cache_path = root
            self._dof_bundle_anim_cache = cached
            self.log_widget.log(
                f"Loaded {len(cached)} bundle animations from cache.",
                "INFO",
            )
            return list(cached)

        self.log_widget.log(
            "Scanning Unity bundles for ANIMBBB entries (this can take a while)...",
            "INFO",
        )
        anim_names: Set[str] = set()
        bundle_map: Dict[str, str] = {}
        data_files = self._find_unity_bundle_data_files(root)
        for idx, data_path in enumerate(data_files):
            if idx and idx % 50 == 0:
                self.log_widget.log(
                    f"Scanning bundles... ({idx}/{len(data_files)})",
                    "INFO",
                )
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
                if lower.endswith(".animbbb"):
                    anim_names.add(name)
                    bundle_map.setdefault(name, data_path)
                    bundle_map.setdefault(lower, data_path)

        sorted_names = sorted(anim_names, key=lambda value: value.lower())
        self._dof_bundle_cache_path = root
        self._dof_bundle_anim_cache = sorted_names
        self._write_bundle_index_cache(cache_path, root, sorted_names, bundle_map)
        return list(sorted_names)

    def _dof_bundle_index_cache_path(self, root: str) -> str:
        output_root = os.path.join(root, "Output")
        return os.path.join(output_root, "_bundle_index.json")

    def _read_bundle_index_cache(self, cache_path: str, root: str) -> List[str]:
        if not cache_path or not os.path.exists(cache_path):
            return []
        try:
            payload = json.loads(Path(cache_path).read_text(encoding="utf-8"))
        except Exception:
            return []
        if payload.get("root") != root:
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
                "generated": datetime.utcnow().isoformat() + "Z",
            }
            Path(cache_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            return

    @staticmethod
    def _monster_name_preference_score(name: str) -> Tuple[int, int]:
        """Return a sort score for duplicate common names (lower is preferred)."""
        lowered = (name or "").strip().lower()
        if not lowered:
            return (1000, 1000)
        penalty = 0
        if " island" in lowered:
            penalty += 2
        if "composer" in lowered:
            penalty += 1
        return (penalty, len(lowered))

    @staticmethod
    def _humanize_island_name_token(token: str) -> str:
        """Convert metadata tokens like ISLAND_GOLD / ISLAND_1 into display labels."""
        raw = (token or "").strip()
        if not raw or raw == "-":
            return ""
        lowered = raw.lower()
        if lowered.startswith("island_"):
            raw = raw[len("island_") :]
        numeric_names = {
            1: "Plant",
            2: "Cold",
            3: "Air",
            4: "Water",
            5: "Earth",
            6: "Gold",
            7: "Ethereal",
            8: "Shugabush",
            9: "Tribal",
            10: "Wublin",
            11: "Composer",
            12: "Celestial",
            13: "Fire Haven",
            14: "Fire Oasis",
            15: "Psychic",
            16: "Faerie",
            17: "Bone",
            18: "Mythical",
            19: "Sanctum",
            20: "Colossingum",
            21: "Seasonal Shanty",
            22: "Amber",
        }
        named_tokens = {
            "gold": "Gold",
            "ethereal": "Ethereal",
            "shuga": "Shugabush",
            "tribal": "Tribal",
            "underling": "Wublin",
            "composer": "Composer",
            "celestial": "Celestial",
            "fire": "Fire Haven",
            "battle": "Colossingum",
        }
        if raw.isdigit():
            island_id = int(raw)
            return numeric_names.get(island_id, f"Island {island_id}")
        named = named_tokens.get(raw.strip().lower())
        if named:
            return named
        return raw.replace("_", " ").strip().title()

    def _ensure_monster_name_rosetta_loaded(self, force: bool = False) -> None:
        """Load common-name mappings from docs rosetta + metadata files."""
        rosetta_path = self._monster_name_rosetta_path
        metadata_path = self._monster_name_metadata_path

        def _safe_mtime(path: Path) -> Optional[float]:
            if not path.exists() or not path.is_file():
                return None
            try:
                return path.stat().st_mtime
            except OSError:
                return None

        rosetta_mtime = _safe_mtime(rosetta_path)
        metadata_mtime = _safe_mtime(metadata_path)
        if (
            not force
            and rosetta_mtime == self._monster_name_rosetta_mtime
            and metadata_mtime == self._monster_name_metadata_mtime
        ):
            return

        mapping: Dict[str, str] = {}
        resolution_meta: Dict[str, Dict[str, Any]] = {}
        island_name_by_id: Dict[int, str] = {}

        def _register_mapping(common_name: str, file_name: str) -> None:
            common = (common_name or "").strip()
            file_value = (file_name or "").strip()
            if not common or not file_value:
                return
            stem = Path(file_value.strip('"').strip("'")).stem.lower()
            if not stem:
                return
            existing = mapping.get(stem)
            if existing is None:
                mapping[stem] = common
                return
            if self._monster_name_preference_score(common) < self._monster_name_preference_score(existing):
                mapping[stem] = common

        if rosetta_mtime is not None:
            try:
                raw_text = rosetta_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                raw_text = ""
            for raw_line in raw_text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                common_name, file_name = [part.strip() for part in line.split("=", 1)]
                _register_mapping(common_name, file_name)

        if metadata_mtime is not None:
            try:
                metadata_text = metadata_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                metadata_text = ""
            current_section = ""
            for raw_line in metadata_text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current_section = line.upper()
                    continue
                if line.startswith("#"):
                    continue
                if current_section == "[ISLAND_LEGEND]":
                    if "=" not in line:
                        continue
                    id_part, payload = [part.strip() for part in line.split("=", 1)]
                    try:
                        island_id = int(id_part)
                    except ValueError:
                        continue
                    fields: Dict[str, str] = {}
                    for segment in payload.split("|"):
                        segment_clean = segment.strip()
                        if ":" not in segment_clean:
                            continue
                        key, value = [part.strip() for part in segment_clean.split(":", 1)]
                        fields[key.lower()] = value
                    raw_name = fields.get("name", "")
                    friendly_name = self._humanize_island_name_token(raw_name)
                    island_name_by_id[island_id] = friendly_name or f"Island {island_id}"
                    continue
                if current_section != "[MONSTER_RESOLUTION]":
                    continue
                row: Dict[str, str] = {}
                for segment in line.split("|"):
                    if "=" not in segment:
                        continue
                    key, value = [part.strip() for part in segment.split("=", 1)]
                    row[key.lower()] = value
                common_name = row.get("common_name", "")
                file_name = row.get("file", "")
                _register_mapping(common_name, file_name)
                stem = (row.get("stem") or Path(file_name).stem).strip().lower()
                if not stem:
                    continue
                islands: List[int] = []
                islands_text = row.get("islands", "").strip()
                if islands_text and islands_text != "-":
                    for token in islands_text.split(","):
                        token_clean = token.strip()
                        if not token_clean:
                            continue
                        try:
                            islands.append(int(token_clean))
                        except ValueError:
                            continue
                gene_graphics_text = row.get("gene_graphics", "").strip()
                gene_graphics: List[str] = []
                if gene_graphics_text and gene_graphics_text != "-":
                    gene_graphics = [
                        value.strip()
                        for value in gene_graphics_text.split(",")
                        if value.strip() and not value.strip().startswith("?")
                    ]
                candidate = {
                    "entity_type": row.get("entity_type", "").strip().lower(),
                    "class_name": row.get("class", "").strip(),
                    "fam": row.get("fam", "").strip(),
                    "genes": row.get("genes", "").strip(),
                    "gene_graphics": gene_graphics,
                    "islands": sorted(set(islands)),
                }
                existing = resolution_meta.get(stem)
                if existing is None:
                    resolution_meta[stem] = candidate
                else:
                    # Merge conservatively across duplicate stems.
                    if not existing.get("entity_type") and candidate["entity_type"]:
                        existing["entity_type"] = candidate["entity_type"]
                    if not existing.get("class_name") and candidate["class_name"]:
                        existing["class_name"] = candidate["class_name"]
                    if not existing.get("fam") and candidate["fam"]:
                        existing["fam"] = candidate["fam"]
                    if not existing.get("genes") and candidate["genes"]:
                        existing["genes"] = candidate["genes"]
                    existing_genes = set(existing.get("gene_graphics") or [])
                    existing_genes.update(candidate["gene_graphics"])
                    existing["gene_graphics"] = sorted(existing_genes)
                    existing_islands = set(existing.get("islands") or [])
                    existing_islands.update(candidate["islands"])
                    existing["islands"] = sorted(existing_islands)

        self._monster_name_rosetta_mtime = rosetta_mtime
        self._monster_name_metadata_mtime = metadata_mtime
        self._monster_common_name_by_stem = mapping
        self._monster_resolution_meta_by_stem = resolution_meta
        self._island_name_by_id = island_name_by_id

    @staticmethod
    def _prettify_monster_stem(stem_or_token: str) -> str:
        """Return a readable fallback label when no common-name mapping exists."""
        raw = Path(stem_or_token or "").stem
        if not raw:
            return ""
        lowered = raw.lower()
        if lowered.startswith("monster_"):
            raw = raw[len("monster_") :]
        pretty = raw.replace("_", " ").strip()
        return pretty.title() if pretty else raw

    def _resolve_common_monster_name(self, stem_or_path_or_token: str) -> Optional[str]:
        """Resolve a common monster name via rosetta mapping using stem/path/token input."""
        raw = (stem_or_path_or_token or "").strip()
        if not raw:
            return None
        stem = Path(raw).stem.lower()
        if not stem:
            return None
        direct = self._monster_common_name_by_stem.get(stem)
        if direct:
            return direct
        if not stem.startswith("monster_"):
            prefixed = self._monster_common_name_by_stem.get(f"monster_{stem}")
            if prefixed:
                return prefixed
        return None

    def resolve_common_name_for_monster_token(self, token: str) -> Optional[str]:
        """Resolve a human-readable monster name for token strings (e.g., A, ABN, 18-X18)."""
        self._ensure_monster_name_rosetta_loaded()
        raw = (token or "").strip().lower()
        if not raw:
            return None
        raw = raw.replace("-", "_")
        raw = re.sub(r"^\d{2}[_]", "", raw)
        if raw.startswith("monster_"):
            raw = raw[len("monster_") :]
        raw = raw.strip("_")
        if not raw:
            return None

        candidates: List[str] = []
        candidates.append(raw)
        candidates.append(f"monster_{raw}")
        if "_" in raw:
            head = raw.split("_", 1)[0]
            if head:
                candidates.append(head)
                candidates.append(f"monster_{head}")

        seen: Set[str] = set()
        for candidate in candidates:
            key = candidate.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            resolved = self._resolve_common_monster_name(key)
            if resolved:
                return resolved
        return None

    def resolve_monster_resolution_metadata(self, stem_or_path_or_token: str) -> Dict[str, Any]:
        """Resolve parsed metadata row by monster stem/path/token."""
        self._ensure_monster_name_rosetta_loaded()
        raw = (stem_or_path_or_token or "").strip()
        if not raw:
            return {}
        stem = Path(raw).stem.lower()
        if not stem:
            return {}
        direct = self._monster_resolution_meta_by_stem.get(stem)
        if direct:
            return dict(direct)
        if not stem.startswith("monster_"):
            prefixed = self._monster_resolution_meta_by_stem.get(f"monster_{stem}")
            if prefixed:
                return dict(prefixed)
        return {}

    def resolve_island_display_name(self, island_id: int) -> str:
        """Resolve island display label from metadata, falling back to Island N."""
        self._ensure_monster_name_rosetta_loaded()
        name = self._island_name_by_id.get(int(island_id))
        if name:
            return name
        return f"Island {int(island_id)}"

    def _monster_browser_display_name(
        self,
        *,
        source_path: Optional[str] = None,
        token: Optional[str] = None,
        fallback: Optional[str] = None,
    ) -> str:
        """Resolve the user-facing monster name used by Monster Browser entries."""
        for candidate in (source_path, fallback, token):
            if not candidate:
                continue
            resolved = self._resolve_common_monster_name(candidate)
            if resolved:
                return resolved
        # Fallback: preserve the browser's previous behavior when no rosetta
        # name resolves, so unresolved entries stay identical to legacy labels.
        if source_path:
            return Path(source_path).stem
        if fallback:
            return Path(fallback).stem
        if token:
            return Path(token).stem
        return ""

    def _rebuild_monster_lookup(self, entries: List[AnimationFileEntry]) -> None:
        """Build quick lookup for monster files keyed by base name."""
        lookup: Dict[str, MonsterFileRecord] = {}
        for entry in entries:
            stem = Path(entry.name).stem.lower()
            if not stem or not stem.startswith("monster_"):
                continue
            if self._is_excluded_monster_stem(stem):
                continue
            record = lookup.get(stem)
            if not record:
                record = MonsterFileRecord(stem=stem, relative_path=entry.relative_path)
                lookup[stem] = record
            if entry.is_json:
                record.json_path = entry.full_path
                record.relative_path = entry.relative_path
            elif entry.is_bin:
                if not record.bin_path:
                    record.bin_path = entry.full_path
                if not record.relative_path:
                    record.relative_path = entry.relative_path
        self.monster_file_lookup = lookup

    def _build_monster_browser_entries(self, book_dirs: List[Path]) -> List[MonsterBrowserEntry]:
        """Return MonsterBrowserEntry rows by matching portraits to indexed files."""
        self._ensure_monster_name_rosetta_loaded()
        valid_dirs = [path for path in book_dirs if path and path.exists()]
        if not valid_dirs:
            return []

        entries: List[MonsterBrowserEntry] = []
        seen_tokens: Set[str] = set()
        portrait_map: Dict[str, str] = {}
        prefix = "monster_portrait_square_"
        for book_dir in valid_dirs:
            for path in sorted(book_dir.glob("*")):
                if path.is_dir():
                    continue
                suffix = path.suffix.lower()
                if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".avif"):
                    continue
                stem = path.stem.lower()
                if "black" in stem:
                    continue
                if not stem.startswith(prefix):
                    continue
                token = stem[len(prefix):]
                if not token or token in seen_tokens:
                    continue
                if token not in portrait_map:
                    portrait_map[token] = str(path)
                base_record, variant_options = self._gather_monster_variants(token)
                if not base_record or not base_record.has_source():
                    continue
                json_path = base_record.json_path
                bin_path = base_record.bin_path
                display_path = base_record.relative_path or os.path.basename(json_path or bin_path or token)
                display_name = self._monster_browser_display_name(
                    source_path=json_path or bin_path,
                    token=token,
                    fallback=base_record.stem,
                )
                entries.append(
                    MonsterBrowserEntry(
                        token=token,
                        display_name=display_name,
                        relative_path=display_path,
                        image_path=str(path),
                        json_path=json_path,
                        bin_path=bin_path,
                        variants=variant_options,
                    )
                )
                seen_tokens.add(token)

        if self.monster_file_lookup:
            portrait_prefixes = {f"monster_{token}".lower() for token in seen_tokens}

            def _is_variant_of_portrait(stem: str) -> bool:
                for prefix in portrait_prefixes:
                    if self._is_variant_stem(prefix, stem) or self._is_infix_variant_stem(prefix, stem):
                        return True
                return False

            for stem, record in sorted(self.monster_file_lookup.items()):
                if not record.has_source():
                    continue
                if stem in portrait_prefixes:
                    continue
                if _is_variant_of_portrait(stem):
                    continue
                raw_token = stem[len("monster_"):] if stem.startswith("monster_") else stem
                token = self._canonical_monster_browser_token(raw_token)
                if not token or token in seen_tokens:
                    continue
                base_record, variant_options = self._gather_monster_variants(token)
                if not base_record:
                    base_record = record
                    variant_options = []
                if not base_record.has_source():
                    continue
                display_path = base_record.relative_path or os.path.basename(
                    base_record.json_path or base_record.bin_path or base_record.stem or token
                )
                display_name = self._monster_browser_display_name(
                    source_path=base_record.json_path or base_record.bin_path,
                    token=token,
                    fallback=base_record.stem or base_record.relative_path,
                )
                portrait_path = self._resolve_monster_browser_portrait(token, portrait_map)
                entries.append(
                    MonsterBrowserEntry(
                        token=token,
                        display_name=display_name,
                        relative_path=display_path,
                        image_path=portrait_path,
                        json_path=base_record.json_path,
                        bin_path=base_record.bin_path,
                        variants=variant_options,
                    )
                )
                seen_tokens.add(token)

        self._annotate_monster_browser_entry_islands(entries)
        self._annotate_monster_browser_entry_metadata(entries)
        entries.sort(key=lambda item: item.display_name.lower())
        return entries

    @staticmethod
    def _canonical_monster_browser_token(raw_token: str) -> str:
        """Normalize monster-browser grouping token (e.g., o_island1 -> o)."""
        token = (raw_token or "").strip().lower()
        if not token:
            return ""
        island_match = re.match(r"^(.*)_island\d+$", token)
        if island_match and island_match.group(1):
            token = island_match.group(1)
        return token

    @staticmethod
    def _normalize_monster_track_token(token: str) -> str:
        """Normalize a monster token so it can be matched against track-bin names."""
        raw = (token or "").strip().lower()
        if not raw:
            return ""
        if raw.startswith("monster_"):
            raw = raw[len("monster_") :]
        parts = [part for part in raw.split("_") if part]
        if not parts:
            return ""
        while parts and parts[0] in {"lgn", "rare", "epic", "amber", "composer"}:
            parts = parts[1:]
        if not parts:
            return ""
        while len(parts) > 1 and parts[-1] in {"rare", "epic", "adult", "baby"}:
            parts = parts[:-1]
        key = re.sub(r"[^a-z0-9]", "", parts[0])
        return key

    @staticmethod
    def _parse_island_track_bin_name(name: str) -> Optional[Tuple[int, str]]:
        """Parse bins like 002_A.bin or 006-EPIC_F_PHASE1.bin into island/token parts."""
        stem = Path(name or "").stem
        match = re.match(r"^(\d{3})[-_]([A-Za-z0-9]+)", stem)
        if not match:
            return None
        try:
            island = int(match.group(1))
        except ValueError:
            return None
        token = re.sub(r"[^a-z0-9]", "", match.group(2).lower())
        if island <= 0 or not token:
            return None
        return island, token

    def _annotate_monster_browser_entry_islands(self, entries: List[MonsterBrowserEntry]) -> None:
        """Attach island numbers to browser entries using indexed track bins."""
        if not entries or not self.file_index:
            return

        token_to_entry_ids: Dict[str, Set[int]] = {}
        entry_by_id: Dict[int, MonsterBrowserEntry] = {}
        for entry in entries:
            entry_id = id(entry)
            entry_by_id[entry_id] = entry

            candidate_tokens: Set[str] = set()
            candidate_tokens.add(self._normalize_monster_track_token(entry.token))
            candidate_tokens.add(self._normalize_monster_track_token(entry.display_name))
            if entry.json_path:
                candidate_tokens.add(self._normalize_monster_track_token(Path(entry.json_path).stem))
            if entry.bin_path:
                candidate_tokens.add(self._normalize_monster_track_token(Path(entry.bin_path).stem))
            for variant in entry.variants:
                candidate_tokens.add(self._normalize_monster_track_token(variant.stem))
                if variant.json_path:
                    candidate_tokens.add(self._normalize_monster_track_token(Path(variant.json_path).stem))
                if variant.bin_path:
                    candidate_tokens.add(self._normalize_monster_track_token(Path(variant.bin_path).stem))

            for token in candidate_tokens:
                if not token:
                    continue
                token_to_entry_ids.setdefault(token, set()).add(entry_id)

        if not token_to_entry_ids:
            return

        island_by_entry_id: Dict[int, Set[int]] = {}
        for indexed_entry in self.file_index:
            if not indexed_entry.is_bin:
                continue
            parsed = self._parse_island_track_bin_name(indexed_entry.name)
            if not parsed:
                continue
            island, token = parsed
            entry_ids = token_to_entry_ids.get(token)
            if not entry_ids:
                continue
            for entry_id in entry_ids:
                island_by_entry_id.setdefault(entry_id, set()).add(island)

        for entry_id, entry in entry_by_id.items():
            islands = sorted(island_by_entry_id.get(entry_id, set()))
            entry.island_numbers = islands
            entry.rebuild_search_blob()

    def _infer_monster_variant_types(self, entry: MonsterBrowserEntry) -> List[str]:
        """Infer high-level variant tags for browser filtering."""
        haystacks = [
            (entry.token or "").lower(),
            (entry.display_name or "").lower(),
            (entry.relative_path or "").lower(),
            (entry.json_path or "").lower(),
            (entry.bin_path or "").lower(),
        ]
        haystacks.extend((variant.stem or "").lower() for variant in (entry.variants or []))
        haystacks.extend((variant.relative_path or "").lower() for variant in (entry.variants or []))
        combined = " ".join(haystacks)

        tags: Set[str] = set()
        if "rare" in combined:
            tags.add("rare")
        if "epic" in combined:
            tags.add("epic")
        if "seasonal" in combined or re.search(r"(?:^|[_-])s\d{1,2}(?:$|[_-])", combined):
            tags.add("seasonal")
        if "composer" in combined:
            tags.add("composer")
        if "amber" in combined:
            tags.add("amber")
        if "monster_lgn_" in combined or (entry.token or "").lower().startswith("lgn_"):
            tags.add("lgn")
        if (entry.entity_type or "").lower() == "box_monster":
            tags.add("box_monster")
        if not tags:
            tags.add("normal")
        return sorted(tags)

    def _annotate_monster_browser_entry_metadata(self, entries: List[MonsterBrowserEntry]) -> None:
        """Attach metadata-derived class/fam/genes/entity/source flags for browser filters."""
        if not entries:
            return
        self._ensure_monster_name_rosetta_loaded()

        downloads_roots = [
            os.path.normcase(os.path.normpath(root))
            for root in self._get_downloads_xml_bin_roots()
            if root
        ]
        game_root = (
            os.path.normcase(os.path.normpath(os.path.join(self.game_path, "data", "xml_bin")))
            if self.game_path
            else ""
        )

        def _path_in_root(path_value: str, root_value: str) -> bool:
            if not path_value or not root_value:
                return False
            try:
                common = os.path.commonpath([path_value, root_value])
            except ValueError:
                return False
            return os.path.normcase(common) == os.path.normcase(root_value)

        for entry in entries:
            metadata: Dict[str, Any] = {}
            metadata_candidates: List[str] = []
            metadata_candidates.extend([entry.json_path or "", entry.bin_path or "", entry.token or ""])
            for variant in entry.variants or []:
                metadata_candidates.extend([variant.json_path or "", variant.bin_path or "", variant.stem or ""])
            for candidate in metadata_candidates:
                if not candidate:
                    continue
                resolved = self.resolve_monster_resolution_metadata(candidate)
                if resolved:
                    metadata = resolved
                    break

            entry.class_name = str(metadata.get("class_name") or "")
            entry.fam_name = str(metadata.get("fam") or "")
            entry.genes = str(metadata.get("genes") or "")
            entry.entity_type = str(metadata.get("entity_type") or "monster")

            gene_graphics = metadata.get("gene_graphics") or []
            if isinstance(gene_graphics, list):
                entry.gene_graphics = [str(value) for value in gene_graphics if str(value).strip()]
            else:
                entry.gene_graphics = []

            metadata_islands = metadata.get("islands") or []
            normalized_islands: Set[int] = set(entry.island_numbers or [])
            for value in metadata_islands:
                try:
                    island_id = int(value)
                except (TypeError, ValueError):
                    continue
                if island_id > 0:
                    normalized_islands.add(island_id)
            entry.island_numbers = sorted(normalized_islands)
            entry.island_labels = [self.resolve_island_display_name(island_id) for island_id in entry.island_numbers]

            source_paths: List[str] = []
            for path_value in (entry.json_path, entry.bin_path):
                if path_value:
                    source_paths.append(os.path.normcase(os.path.normpath(path_value)))
            for variant in entry.variants or []:
                for path_value in (variant.json_path, variant.bin_path):
                    if path_value:
                        source_paths.append(os.path.normcase(os.path.normpath(path_value)))

            has_downloads = False
            has_game = False
            for source_path in source_paths:
                if any(_path_in_root(source_path, root) for root in downloads_roots):
                    has_downloads = True
                if game_root and _path_in_root(source_path, game_root):
                    has_game = True
            entry.has_downloads_source = has_downloads
            entry.has_game_source = has_game

            entry.variant_types = self._infer_monster_variant_types(entry)
            entry.rebuild_search_blob()

    def _gather_monster_variants(
        self, token: str
    ) -> Tuple[Optional[MonsterFileRecord], List[MonsterVariantOption]]:
        """Return the primary record and extra variants for a monster token."""
        prefix_key = f"monster_{token}".lower()
        if not self.monster_file_lookup:
            return None, []

        primary: Optional[MonsterFileRecord] = None
        variants: List[MonsterVariantOption] = []
        for stem, record in self.monster_file_lookup.items():
            if not record.has_source():
                continue
            if stem == prefix_key:
                primary = record
                continue
            if not self._is_variant_stem(prefix_key, stem) and not self._is_infix_variant_stem(prefix_key, stem):
                continue
            label = self._format_monster_variant_label(prefix_key, stem)
            display_name = self._monster_browser_display_name(
                source_path=record.json_path or record.bin_path,
                fallback=record.stem or record.relative_path,
            )
            variants.append(
                MonsterVariantOption(
                    display_name=display_name,
                    relative_path=record.relative_path,
                    json_path=record.json_path,
                    bin_path=record.bin_path,
                    variant_label=label,
                    stem=record.stem,
                )
            )
        variants.sort(key=lambda variant: variant.variant_label.lower())
        if primary is None:
            if variants:
                fallback = variants[0]
                primary = MonsterFileRecord(
                    stem=fallback.stem,
                    relative_path=fallback.relative_path,
                    json_path=fallback.json_path,
                    bin_path=fallback.bin_path,
                )
                variants = variants[1:]
            else:
                return None, []
        return primary, variants

    @staticmethod
    def _format_monster_variant_label(prefix_key: str, stem: str) -> str:
        """Return a user-friendly label for a monster variant stem."""
        if not stem.startswith(prefix_key):
            prefix_tokens = [token for token in prefix_key[len("monster_") :].split("_") if token]
            stem_tokens = [token for token in stem[len("monster_") :].split("_") if token]
            if (
                len(prefix_tokens) >= 2
                and len(stem_tokens) > len(prefix_tokens)
                and prefix_tokens[-1] in {"rare", "epic"}
                and stem_tokens[: len(prefix_tokens) - 1] == prefix_tokens[:-1]
                and stem_tokens[-1] == prefix_tokens[-1]
            ):
                inserted_tokens = stem_tokens[len(prefix_tokens) - 1 : -1]
                if inserted_tokens and all(token in {"amber"} for token in inserted_tokens):
                    return " ".join(token.capitalize() for token in inserted_tokens) or "Variant"
            return Path(stem).stem
        suffix = stem[len(prefix_key) :].lstrip("_")
        if not suffix:
            return "Default"
        tokens = [token for token in suffix.split("_") if token]
        return " ".join(token.capitalize() for token in tokens) or "Variant"

    @staticmethod
    def _resolve_monster_browser_portrait(token: str, portrait_map: Dict[str, str]) -> str:
        """Resolve portrait art for token; supports LGN variants mapping to base tokens."""
        key = (token or "").strip().lower()
        if not key:
            return ""
        candidates: List[str] = [key]

        parts = [part for part in key.split("_") if part]
        if parts and parts[0] == "lgn" and len(parts) > 1:
            candidates.append("_".join(parts[1:]))

        if "amber" in parts:
            no_amber = [part for part in parts if part != "amber"]
            if no_amber:
                candidates.append("_".join(no_amber))
                parts = no_amber

        if len(parts) > 1:
            if parts[0] in {"rare", "epic"}:
                candidates.append("_".join(parts[1:] + [parts[0]]))
            if parts[-1] in {"rare", "epic"}:
                candidates.append("_".join([parts[-1]] + parts[:-1]))

        # Explicit portrait aliases where gameplay token and portrait token differ.
        # Example: Rare Wubbox uses monster_o* files but portrait token is f_rare.
        portrait_aliases = {
            "o": "f_rare",
        }
        alias = portrait_aliases.get(key)
        if alias:
            candidates.append(alias)

        seen: Set[str] = set()
        for candidate in candidates:
            normalized = candidate.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            resolved = portrait_map.get(normalized, "")
            if resolved:
                return resolved
        return ""

    @staticmethod
    def _is_excluded_monster_stem(stem: str) -> bool:
        """Return True if the given monster stem corresponds to costume/track data."""
        if not stem:
            return True
        if not stem.startswith("monster_"):
            return True
        remainder = stem[len("monster_") :]
        tokens = [token for token in remainder.split("_") if token]
        # Keep costume/track helper files hidden, but allow rare/epic stems to surface
        # as full-fledged browser entries rather than being filtered entirely.
        forbidden = {"costume", "costumes", "track", "tracks"}
        return any(token.lower() in forbidden for token in tokens)

    @staticmethod
    def _is_variant_stem(prefix_key: str, candidate_stem: str) -> bool:
        """Return True if the candidate stem is a valid variant of the prefix stem."""
        if not candidate_stem.startswith(prefix_key):
            return False
        suffix = candidate_stem[len(prefix_key) :]
        if not suffix:
            return False  # exact match handled elsewhere
        if not suffix.startswith("_"):
            return False  # avoid collisions like monster_abd vs monster_abdn
        tokens = [token for token in suffix.split("_") if token]
        if not tokens:
            return False
        blocked = {"rare", "epic", "costume", "costumes", "track", "tracks"}
        return not any(token.lower() in blocked for token in tokens)

    @staticmethod
    def _is_infix_variant_stem(prefix_key: str, candidate_stem: str) -> bool:
        """
        Return True when candidate inserts a recognized variant token before a
        trailing qualifier of prefix_key.
        Example: monster_abn_rare -> monster_abn_amber_rare.
        """
        if not prefix_key or not candidate_stem:
            return False
        if prefix_key == candidate_stem:
            return False
        if not prefix_key.startswith("monster_") or not candidate_stem.startswith("monster_"):
            return False

        prefix_tokens = [token for token in prefix_key[len("monster_") :].split("_") if token]
        candidate_tokens = [token for token in candidate_stem[len("monster_") :].split("_") if token]
        if len(prefix_tokens) < 2 or len(candidate_tokens) <= len(prefix_tokens):
            return False

        qualifier = prefix_tokens[-1]
        if qualifier not in {"rare", "epic"}:
            return False

        base_tokens = prefix_tokens[:-1]
        if candidate_tokens[: len(base_tokens)] != base_tokens:
            return False
        if candidate_tokens[-1] != qualifier:
            return False

        inserted_tokens = candidate_tokens[len(base_tokens) : -1]
        if not inserted_tokens:
            return False

        return all(token in {"amber"} for token in inserted_tokens)

    def open_monster_browser(self):
        """Launch the Monster Browser dialog for visual monster selection."""
        if not self.game_path:
            QMessageBox.warning(self, "Game Path Required", "Set the game path before using the Monster Browser.")
            return
        # Ensure index stays in sync with game + downloads roots.
        self.refresh_file_list()
        if not self.file_index:
            QMessageBox.warning(self, "No Files Indexed", "Unable to index BIN/JSON files. Check your game path.")
            return

        book_dirs: List[Path] = []
        if self.downloads_path:
            for candidate in (
                Path(self.downloads_path) / "gfx" / "book",
                Path(self.downloads_path) / "data" / "gfx" / "book",
            ):
                if candidate.exists():
                    book_dirs.append(candidate)

        game_book = Path(self.game_path) / "data" / "gfx" / "book"
        if game_book.exists():
            book_dirs.append(game_book)

        if not book_dirs:
            fallback = self.project_root / "My Singing Monsters Game Filesystem Example" / "data" / "gfx" / "book"
            if fallback.exists():
                book_dirs.append(fallback)

        if not book_dirs:
            QMessageBox.warning(self, "Book Art Missing", "Could not locate data/gfx/book for portraits.")
            return

        entries = self._build_monster_browser_entries(book_dirs)
        if not entries:
            QMessageBox.information(self, "No Monsters Found", "No monsters with portraits were found in the indexed files.")
            return

        badge_data_roots: List[Path] = []
        if self.downloads_path:
            for candidate in (
                Path(self.downloads_path) / "data",
                Path(self.downloads_path),
            ):
                if candidate.exists():
                    badge_data_roots.append(candidate)
        game_data = Path(self.game_path) / "data"
        if game_data.exists():
            badge_data_roots.append(game_data)
        fallback_data = self.project_root / "My Singing Monsters Game Filesystem Example" / "data"
        if fallback_data.exists():
            badge_data_roots.append(fallback_data)

        stored_columns = self.settings.value('monster_browser/columns', 3, type=int)
        dialog = MonsterBrowserDialog(
            entries,
            initial_columns=max(1, int(stored_columns or 3)),
            badge_data_roots=badge_data_roots,
            parent=self,
        )
        result = dialog.exec()
        self.settings.setValue('monster_browser/columns', dialog.column_count())
        if result == QDialog.DialogCode.Accepted and dialog.selected_entry:
            self._handle_monster_browser_selection(dialog.selected_entry, dialog.force_reexport())

    def _handle_monster_browser_selection(self, entry: MonsterBrowserEntry, force_reexport: bool):
        """Load the selected monster entry, converting BINs as needed."""
        json_path = entry.json_path if entry.json_path and os.path.exists(entry.json_path) else None
        bin_path = entry.bin_path if entry.bin_path and os.path.exists(entry.bin_path) else None

        if force_reexport or not json_path:
            if not bin_path:
                QMessageBox.warning(self, "Missing BIN", f"No BIN available to convert for {entry.display_name}.")
                return
            json_path = self._convert_bin_file(bin_path, force=True, announce=True)
            if not json_path:
                return
        elif not os.path.exists(json_path):
            self.log_widget.log(f"JSON file missing for {entry.display_name}, attempting to rebuild.", "WARNING")
            if not bin_path:
                QMessageBox.warning(self, "Missing JSON", f"JSON for {entry.display_name} not found.")
                return
            json_path = self._convert_bin_file(bin_path, force=True, announce=True)
            if not json_path:
                return

        if not self.select_file_by_path(json_path):
            self.refresh_file_list()
            self.select_file_by_path(json_path)
        self.load_json_file(json_path)

    def on_file_search_changed(self, text: str):
        """Handle search text changes from the control panel."""
        self.current_search_text = text or ""
        self.apply_file_filter()

    def on_dof_search_toggled(self, enabled: bool):
        """Switch the file list between BIN/JSON and DOF assets."""
        self.dof_search_enabled = bool(enabled)
        self.settings.setValue('dof/search_enabled', self.dof_search_enabled)
        self.control_panel.set_dof_search_mode(self.dof_search_enabled)
        if self.dof_search_enabled:
            if not self.dof_path:
                self.log_widget.log("Set a DOF Assets path to browse DOF files.", "INFO")
            if not self.dof_file_index:
                self.refresh_dof_file_list()
            else:
                self.apply_file_filter()
        else:
            self.apply_file_filter()

    def _active_search_root(self) -> Optional[str]:
        if self.dof_search_enabled:
            return self.dof_path or None
        if self.game_path:
            game_root = os.path.join(self.game_path, "data", "xml_bin")
            if os.path.isdir(game_root):
                return game_root
        downloads_roots = self._get_downloads_xml_bin_roots()
        if downloads_roots:
            return downloads_roots[0]
        return None

    def _get_active_file_index(self) -> List[AnimationFileEntry]:
        return self.dof_file_index if self.dof_search_enabled else self.file_index

    def _get_filtered_file_index(self) -> List[AnimationFileEntry]:
        return self.filtered_dof_file_index if self.dof_search_enabled else self.filtered_file_index

    def _set_filtered_file_index(self, entries: List[AnimationFileEntry]) -> None:
        if self.dof_search_enabled:
            self.filtered_dof_file_index = entries
        else:
            self.filtered_file_index = entries

    def apply_file_filter(self):
        """Filter indexed files based on the search text and update the UI."""
        source_index = self._get_active_file_index()
        if not source_index:
            self._set_filtered_file_index([])
            self.update_file_combo([])
            self.control_panel.update_file_count_label(0, 0)
            return

        tokens = [token for token in self.current_search_text.lower().split() if token]
        if tokens:
            filtered = [
                entry for entry in source_index
                if all(token in entry.relative_path.lower() for token in tokens)
            ]
        else:
            filtered = list(source_index)

        self._set_filtered_file_index(filtered)
        self.update_file_combo(filtered)
        self.control_panel.update_file_count_label(len(filtered), len(source_index))

        if tokens and not filtered:
            label = "DOF assets" if self.dof_search_enabled else "BIN/JSON files"
            self.log_widget.log(f"No {label} match the current search", "WARNING")

    def update_file_combo(self, entries: List[AnimationFileEntry]):
        """Populate the combo box with the provided file entries."""
        combo = self.control_panel.bin_combo

        previous_data = combo.currentData()
        previous_text = combo.currentText()
        previous_normalized = None
        if previous_data:
            previous_normalized = os.path.normcase(os.path.normpath(previous_data))
        elif previous_text:
            root = self._active_search_root()
            if root:
                fallback_path = os.path.join(root, previous_text)
                previous_normalized = os.path.normcase(os.path.normpath(fallback_path))

        signals_blocked = combo.blockSignals(True)
        combo.clear()
        for entry in entries:
            display_text = entry.relative_path.replace("\\", "/")
            combo.addItem(display_text, entry.full_path)
        combo.blockSignals(signals_blocked)

        if previous_normalized:
            for idx, entry in enumerate(entries):
                if entry.normalized_path() == previous_normalized:
                    combo.setCurrentIndex(idx)
                    break
            else:
                if entries:
                    combo.setCurrentIndex(0)
        elif entries:
            combo.setCurrentIndex(0)

    def select_file_by_path(self, target_path: str) -> bool:
        """
        Attempt to select an entry in the combo box matching the given path.
        
        Args:
            target_path: Absolute path of the file to select
        
        Returns:
            True if the file was selected, False otherwise
        """
        normalized_target = os.path.normcase(os.path.normpath(target_path))
        combo = self.control_panel.bin_combo
        for idx, entry in enumerate(self._get_filtered_file_index()):
            if entry.normalized_path() == normalized_target:
                combo.setCurrentIndex(idx)
                return True

        # Fallback in case filtered list is outdated relative to combo contents
        for idx in range(combo.count()):
            data_path = combo.itemData(idx)
            if not data_path:
                continue
            if os.path.normcase(os.path.normpath(data_path)) == normalized_target:
                combo.setCurrentIndex(idx)
                return True
        return False

    def _current_monster_token(self) -> Optional[str]:
        """Return the monster token derived from the active JSON filename."""
        if not self.current_json_path:
            return None
        stem = Path(self.current_json_path).stem
        if not stem:
            return None
        if stem.lower().startswith("monster_"):
            stem = stem[8:]
        return stem or None

    def _scan_costume_entries(self) -> List[CostumeEntry]:
        """Return all costume files that match the current monster token."""
        if self.legacy_animation_active:
            return []
        token = self._current_monster_token()
        if not token or not self.game_path:
            return []

        prefix_lower = f"costume_{token.lower()}_"
        entries: Dict[str, CostumeEntry] = {}

        xml_bin_dir = os.path.join(self.game_path, "data", "xml_bin")
        self._collect_costumes_in_dir(
            xml_bin_dir, prefix_lower, entries,
            priority=0,
            allow_bins=True, allow_json=True
        )

        extra_dirs: List[str] = []
        if self.current_json_path:
            extra_dirs.append(os.path.dirname(self.current_json_path))
        project_dir = str(self.project_root)
        if project_dir:
            extra_dirs.append(project_dir)

        priority_counter = 1
        for directory in extra_dirs:
            if not directory:
                continue
            if os.path.normcase(directory) == os.path.normcase(xml_bin_dir):
                continue
            self._collect_costumes_in_dir(
                directory, prefix_lower, entries,
                priority=priority_counter,
                allow_bins=False, allow_json=True
            )
            priority_counter += 1

        sorted_entries = [
            entry for entry in sorted(entries.values(), key=lambda e: e.key)
            if entry.source_path
        ]
        return sorted_entries

    def _scan_legacy_sheet_entries(self) -> List[CostumeEntry]:
        """Discover legacy VIP spritesheet variants for the active animation."""
        if not self.legacy_animation_active or not self.current_json_data:
            return []
        sources = self.current_json_data.get("sources") or []
        if not sources:
            return []
        json_dir = os.path.dirname(self.current_json_path) if self.current_json_path else None
        legacy_entries: List[CostumeEntry] = []
        seen_paths: Set[str] = set()
        for source in sources:
            source_name = (source.get("src") or "").strip()
            if not source_name:
                continue
            variants = self._collect_legacy_variants_for_source(source_name, json_dir)
            for label, path in variants:
                norm = os.path.normcase(os.path.normpath(path))
                if norm in seen_paths:
                    continue
                seen_paths.add(norm)
                entry = CostumeEntry(
                    key=f"legacy_sheet::{source_name}::{os.path.basename(path)}",
                    display_name=label,
                    legacy_sheet_path=path,
                    legacy_source_name=source_name,
                )
                legacy_entries.append(entry)
        legacy_entries.sort(key=lambda entry: entry.display_name.lower())
        return legacy_entries

    def _collect_legacy_variants_for_source(
        self,
        source_name: str,
        json_dir: Optional[str]
    ) -> List[Tuple[str, str]]:
        """Return filesystem paths for alternate spritesheet XMLs."""
        base_filename = os.path.basename(source_name)
        if not base_filename or not base_filename.endswith("_sheet.xml"):
            return []
        base_root = base_filename[:-len("_sheet.xml")]
        candidate_dirs: Set[Path] = set()
        base_path = self._resolve_source_xml_path(source_name, json_dir)
        if base_path:
            candidate_dirs.add(Path(base_path).parent)
        if json_dir:
            json_dir_path = Path(json_dir)
            candidate_dirs.add(json_dir_path)
            if (json_dir_path / "xml_resources").is_dir():
                candidate_dirs.add(json_dir_path / "xml_resources")
        pattern = f"{base_root}_*_sheet.xml"
        entries: List[Tuple[str, str]] = []
        seen: Set[str] = set()
        for directory in candidate_dirs:
            if not directory.is_dir():
                continue
            for candidate in directory.glob(pattern):
                if candidate.name == base_filename:
                    continue
                suffix = self._legacy_variant_suffix(base_root, candidate.stem)
                if not suffix:
                    continue
                norm = os.path.normcase(str(candidate))
                if norm in seen:
                    continue
                seen.add(norm)
                label = f"VIP Sheet ({suffix})"
                entries.append((label, str(candidate)))
        entries.sort(key=lambda item: item[0].lower())
        return entries

    @staticmethod
    def _legacy_variant_suffix(base_root: str, candidate_stem: str) -> Optional[str]:
        """Return the portion of the candidate stem that identifies the VIP variant."""
        if not candidate_stem.endswith("_sheet"):
            return None
        trimmed = candidate_stem[:-len("_sheet")]
        expected_prefix = f"{base_root}_"
        if not trimmed.startswith(expected_prefix):
            return None
        suffix = trimmed[len(expected_prefix):].strip("_")
        return suffix or None

    @staticmethod
    def _legacy_override_aliases(source_name: Optional[str]) -> Set[str]:
        """Return the different string aliases that may refer to a source entry."""
        aliases: Set[str] = set()
        if not source_name:
            return aliases
        aliases.add(source_name)
        base = os.path.basename(source_name)
        if base:
            aliases.add(base)
        return aliases

    def _lookup_legacy_sheet_override(self, xml_file: str) -> Optional[str]:
        """Return an override XML path for the requested source, if configured."""
        if not self.legacy_sheet_overrides:
            return None
        override = self.legacy_sheet_overrides.get(xml_file)
        if not override:
            override = self.legacy_sheet_overrides.get(os.path.basename(xml_file))
        if override and os.path.exists(override):
            return override
        return None

    def _apply_legacy_sheet_variant(
        self,
        entry: Optional[CostumeEntry],
        *,
        force_reload: bool = False
    ) -> bool:
        """Switch the active spritesheet when working with legacy VIP assets."""
        if not self.legacy_animation_active:
            return False
        changed = False
        if entry is None:
            if self.legacy_sheet_overrides or force_reload:
                self.legacy_sheet_overrides.clear()
                self.active_legacy_sheet_key = None
                changed = True
        else:
            aliases = self._legacy_override_aliases(entry.legacy_source_name)
            sheet_path = entry.legacy_sheet_path
            if not aliases or not sheet_path:
                return False
            needs_update = force_reload or any(
                self.legacy_sheet_overrides.get(alias) != sheet_path
                for alias in aliases
            )
            if needs_update:
                for alias in aliases:
                    self.legacy_sheet_overrides[alias] = sheet_path
                self.active_legacy_sheet_key = entry.key
                changed = True
        if changed:
            self._reload_base_atlases_for_sources()
            if entry and entry.legacy_sheet_path:
                self.log_widget.log(
                    f"Applied VIP sheet '{os.path.basename(entry.legacy_sheet_path)}'",
                    "INFO",
                )
            else:
                self.log_widget.log("Reverted to base spritesheet", "INFO")
        return changed

    def _reload_base_atlases_for_sources(self) -> None:
        """Reload base texture atlases using the current override state."""
        if not self.current_json_data:
            return
        sources = self.current_json_data.get('sources', [])
        json_dir = os.path.dirname(self.current_json_path) if self.current_json_path else None
        atlases = self._load_texture_atlases_for_sources(
            sources,
            json_dir=json_dir,
            use_cache=False
        )
        self.gl_widget.texture_atlases = atlases
        self._rebuild_source_atlas_lookup(sources, atlases)
        need_context = bool(atlases)
        if need_context:
            self.gl_widget.makeCurrent()
        try:
            for atlas in atlases:
                if getattr(atlas, "texture_id", None):
                    continue
                if not atlas.load_texture():
                    self.log_widget.log(
                        f"Failed to upload texture for {os.path.basename(atlas.image_path)}",
                        "ERROR",
                    )
        finally:
            if need_context:
                self.gl_widget.doneCurrent()
        self.base_texture_atlases = list(atlases)
        self.gl_widget.set_layer_atlas_overrides({})
        self.gl_widget.update()

    def _refresh_costume_list(self):
        """Discover costumes for the current animation and update the UI."""
        self._invalidate_current_canonical_clones()
        costume_entries = self._scan_costume_entries()
        legacy_entries: List[CostumeEntry] = []
        if self.legacy_animation_active:
            legacy_entries = self._scan_legacy_sheet_entries()
        entries = costume_entries + legacy_entries
        self.costume_entries = entries
        self.costume_entry_map = {entry.key: entry for entry in entries}
        combo_items = [(entry.display_name, entry.key) for entry in entries]
        self.control_panel.update_costume_options(combo_items, select_index=0)
        self.control_panel.set_costume_convert_enabled(False)

        if costume_entries or legacy_entries:
            detected = len(costume_entries) + len(legacy_entries)
            self.log_widget.log(f"Detected {detected} appearance variant(s)", "INFO")
        else:
            self.log_widget.log("No costumes detected for this monster", "INFO")
        if self.legacy_animation_active and self.active_legacy_sheet_key:
            preserved_entry = self.costume_entry_map.get(self.active_legacy_sheet_key)
            if preserved_entry:
                self._restore_costume_selection(preserved_entry.key)
                force_reload = not bool(self.legacy_sheet_overrides)
                self._apply_legacy_sheet_variant(preserved_entry, force_reload=force_reload)

    def _get_current_costume_entry(self) -> Optional[CostumeEntry]:
        """Return the CostumeEntry for the currently selected dropdown item."""
        key = self.control_panel.costume_combo.currentData()
        if not key:
            return None
        return self.costume_entry_map.get(key)

    def _restore_costume_selection(self, key: Optional[str]):
        """Set the costume combo box back to a specific entry without firing signals."""
        combo = self.control_panel.costume_combo
        was_blocked = combo.blockSignals(True)
        if key:
            for idx in range(combo.count()):
                if combo.itemData(idx) == key:
                    combo.setCurrentIndex(idx)
                    break
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(was_blocked)
        self.control_panel.set_costume_convert_enabled(key is not None)

    def convert_selected_costume(self):
        """Convert the currently selected costume BIN file to JSON."""
        entry = self._get_current_costume_entry()
        if not entry:
            self.log_widget.log("No costume selected for conversion.", "WARNING")
            return
        if entry.json_path and os.path.exists(entry.json_path):
            rel = os.path.basename(entry.json_path)
            self.log_widget.log(f"Costume already has JSON: {rel}", "INFO")
            return
        if not entry.bin_path or not os.path.exists(entry.bin_path):
            self.log_widget.log("Selected costume has no BIN file to convert.", "ERROR")
            return

        output_path = os.path.splitext(entry.bin_path)[0] + '.json'
        if os.path.exists(output_path):
            self.log_widget.log(
                f"Target JSON already exists: {os.path.basename(output_path)}",
                "WARNING"
            )
            entry.json_path = output_path
            return

        try:
            with open(entry.bin_path, 'rb') as f:
                parsed = parse_costume_file(f.read())
            with open(output_path, 'w', encoding='utf-8') as out_f:
                json.dump(parsed, out_f, indent=2, ensure_ascii=False)
            entry.json_path = output_path
            norm_bin = os.path.normcase(os.path.normpath(entry.bin_path))
            self.costume_cache.pop(norm_bin, None)
            rel = os.path.relpath(output_path, os.path.dirname(entry.bin_path))
            self.log_widget.log(f"Converted costume BIN to JSON: {rel}", "SUCCESS")
        except Exception as exc:
            self.log_widget.log(f"Costume conversion failed: {exc}", "ERROR")
            return

        previous_key = entry.key
        self._refresh_costume_list()
        self._restore_costume_selection(previous_key)

    def _collect_costumes_in_dir(
        self,
        directory: str,
        prefix_lower: str,
        entries: Dict[str, CostumeEntry],
        priority: int,
        allow_bins: bool,
        allow_json: bool
    ):
        """Populate entries with costume files from a directory."""
        if not directory or not os.path.isdir(directory):
            return
        try:
            for name in os.listdir(directory):
                stem, ext = os.path.splitext(name)
                lower_stem = stem.lower()
                if not lower_stem.startswith(prefix_lower):
                    continue
                ext_lower = ext.lower()
                if ext_lower == '.bin' and not allow_bins:
                    continue
                if ext_lower == '.json' and not allow_json:
                    continue
                if ext_lower not in ('.json', '.bin'):
                    continue
                key = lower_stem
                entry = entries.get(key)
                if not entry:
                    entry = CostumeEntry(
                        key=key,
                        display_name=self._format_costume_display_name(stem)
                    )
                    entries[key] = entry
                full_path = os.path.join(directory, name)
                if ext_lower == '.bin':
                    if entry.bin_path is None or priority < entry.bin_priority:
                        entry.bin_path = full_path
                        entry.bin_priority = priority
                else:
                    if entry.json_path is None or priority < entry.json_priority:
                        entry.json_path = full_path
                        entry.json_priority = priority
        except FileNotFoundError:
            return
        except OSError as exc:
            self.log_widget.log(f"Failed to scan '{directory}' for costumes: {exc}", "WARNING")

    def _format_costume_display_name(self, stem: str) -> str:
        """Return a user-friendly label for a costume stem."""
        pretty = stem
        if stem.lower().startswith("costume_"):
            pretty = stem[8:]
        pretty = pretty.replace('_', ' ').strip()
        return pretty.title() if pretty else stem

    def _load_costume_definition(self, entry: CostumeEntry) -> Optional[Dict[str, Any]]:
        """Load costume data from JSON or BIN."""
        source = entry.source_path
        if not source:
            return None
        cache_key = os.path.normcase(os.path.normpath(source))
        if cache_key in self.costume_cache:
            return self.costume_cache[cache_key]
        try:
            if source.lower().endswith('.json'):
                with open(source, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                with open(source, 'rb') as f:
                    blob = f.read()
                data = parse_costume_file(blob)
            self.costume_cache[cache_key] = data
            return data
        except Exception as exc:
            self.log_widget.log(f"Failed to load costume '{entry.display_name}': {exc}", "ERROR")
            return None

    def _embedded_clone_defs(self) -> Optional[List[Dict[str, Any]]]:
        """
        Return clone metadata embedded directly in the currently loaded animation JSON.

        When the rev6 converter exports CloneData alongside the base animation it is safer
        to use those canonical records instead of inferring clone placement from costumes.
        The returned list is always a shallow copy so downstream normalization can mutate
        the entries without polluting the cached JSON data.
        """
        if self.current_animation_embedded_clones is None:
            return None
        clones: List[Dict[str, Any]] = []
        for entry in self.current_animation_embedded_clones:
            if isinstance(entry, dict):
                clones.append(dict(entry))
        return clones

    def _extract_embedded_clone_defs(
        self,
        anim_data: Optional[Dict[str, Any]]
    ) -> Optional[List[Dict[str, Any]]]:
        """Inspect the active animation JSON for baked-in clone metadata."""
        if not anim_data:
            return None

        def _coerce_clone_list(value: Any) -> Optional[List[Dict[str, Any]]]:
            if isinstance(value, list):
                return value
            return None

        direct = _coerce_clone_list(anim_data.get('clone_layers'))
        if direct is not None:
            return direct

        metadata = anim_data.get('metadata')
        meta_clones = _coerce_clone_list(metadata.get('clone_layers')) if isinstance(metadata, dict) else None
        if meta_clones is not None:
            return meta_clones

        anim_name = anim_data.get('name')
        root = self.current_json_data or {}
        if anim_name:
            per_anim = root.get('clone_layers_by_anim')
            if isinstance(per_anim, dict):
                direct_match = _coerce_clone_list(per_anim.get(anim_name))
                if direct_match is not None:
                    return direct_match
                lower_match = _coerce_clone_list(per_anim.get(anim_name.lower()))
                if lower_match is not None:
                    return lower_match

        shared = root.get('clone_layers')
        clones = _coerce_clone_list(shared)
        if clones is not None:
            return clones

        return None

    def _current_animation_clone_key(self) -> Optional[str]:
        """Return a stable cache key for canonical clone detection."""
        source = self.current_json_path or ""
        name = self.current_animation_name or ""
        if not source and not name:
            return None
        return f"{os.path.normcase(source)}|{name}"

    def _invalidate_current_canonical_clones(self):
        """Drop cached canonical clones for the active animation."""
        key = self._current_animation_clone_key()
        if key:
            self.canonical_clone_cache.pop(key, None)

    def _get_canonical_clone_defs(self) -> List[Dict[str, Any]]:
        """Return canonical clone definitions aggregated from all costumes."""
        key = self._current_animation_clone_key()
        if not key:
            return []
        embedded = self._embedded_clone_defs()
        if embedded is not None:
            self.canonical_clone_cache[key] = embedded
            return embedded
        if key in self.canonical_clone_cache:
            return self.canonical_clone_cache[key]
        clones = self._collect_canonical_clone_defs()
        self.canonical_clone_cache[key] = clones
        return clones

    def _collect_canonical_clone_defs(self) -> List[Dict[str, Any]]:
        """Gather the first clone definition for each alias across costumes."""
        entries = self._scan_costume_entries()
        canonical: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            data = self._load_costume_definition(entry)
            if not data:
                continue
            for clone in data.get('clone_layers', []):
                new_name = clone.get('new_layer') or clone.get('name')
                normalized = self._normalize_layer_label(new_name)
                if not normalized:
                    continue
                lower = normalized.lower()
                if lower in canonical:
                    continue
                canonical[lower] = clone
        return list(canonical.values())

    def _apply_canonical_clones_to_base(self, layers: List[LayerData]):
        """Insert canonical layer duplicates before any costume is applied."""
        clone_defs = self._filter_canonical_clone_defs(layers)
        if not clone_defs:
            return
        remap_map: Dict[str, Dict[str, Any]] = {}
        sheet_names: Set[str] = set()
        layer_remap_overrides: Dict[int, Dict[str, Any]] = {}
        self._apply_clone_layers(
            layers,
            clone_defs,
            remap_map,
            sheet_names,
            layer_remap_overrides,
            label="canonical"
        )
        lookup = {layer.name.lower(): layer for layer in layers}
        for entry in clone_defs:
            new_name = entry.get('new_layer') or entry.get('name')
            if not new_name:
                continue
            normalized = new_name.lower()
            target = lookup.get(normalized)
            if target:
                # Keep canonical clones hidden until a costume remaps them.
                target.visible = False
                self.diagnostics.log_canonical(
                    f"Seeded canonical clone '{new_name}' from '{entry.get('source_layer') or entry.get('resource')}'",
                    layer_id=target.layer_id,
                    extra={"reference": entry.get('reference_layer') or entry.get('sheet')}
                )
            self.canonical_layer_names.add(normalized)

    def _filter_canonical_clone_defs(self, layers: List[LayerData]) -> List[Dict[str, Any]]:
        """Return canonical clone entries that aren't already present in layers."""
        clone_defs = self._get_canonical_clone_defs()
        if not clone_defs:
            return []
        existing = {layer.name.lower() for layer in layers}
        filtered: List[Dict[str, Any]] = []
        for entry in clone_defs:
            normalized_entry = self._normalize_canonical_clone_entry(entry, existing)
            new_name = normalized_entry.get('new_layer') or normalized_entry.get('name')
            if not new_name:
                continue
            lower = new_name.lower()
            if lower in existing:
                continue
            filtered.append(normalized_entry)
            existing.add(lower)
        return filtered

    def _normalize_canonical_clone_entry(
        self,
        entry: Dict[str, Any],
        existing_names: Set[str]
    ) -> Dict[str, Any]:
        """
        Return a clone entry that uses the canonical new/source ordering.

        Early JSON exports (and the legacy parser) swapped the first two strings in the BIN,
        which made `new_layer` point at the base sprite while `source_layer` carried the alias.
        When that happens we remap the entry so canonical clone seeding uses the alias that
        does *not* exist in the base layer cache yet.
        """
        new_name = (entry.get('new_layer') or entry.get('name') or "").strip()
        source_name = (entry.get('source_layer') or entry.get('resource') or "").strip()
        if not source_name:
            return entry

        lower_new = new_name.lower()
        lower_source = source_name.lower()

        needs_swap = (
            (not new_name or lower_new in existing_names) and
            source_name and lower_source not in existing_names
        )
        if not needs_swap:
            return entry

        normalized = dict(entry)
        normalized['new_layer'] = source_name
        normalized['name'] = source_name
        normalized['source_layer'] = new_name
        normalized['resource'] = new_name
        return normalized

    def _update_canonical_clone_visibility(
        self,
        layers: List[LayerData],
        remap_map: Dict[str, Dict[str, Any]],
        layer_remap_overrides: Dict[int, Dict[str, Any]]
    ):
        """
        Mirror the runtime behavior by ensuring canonical clones only render once
        a costume remaps them onto a real resource.
        """
        if not self.canonical_layer_names:
            return
        for layer in layers:
            normalized = layer.name.lower()
            if normalized not in self.canonical_layer_names:
                continue
            remap_info = layer_remap_overrides.get(layer.layer_id) or remap_map.get(normalized)
            if not remap_info:
                layer.visible = False
                continue
            resource = (remap_info.get("resource") or "").strip().lower()
            layer.visible = resource != "empty"

    def _clone_layers(self, layers: List[LayerData]) -> List[LayerData]:
        """Deep-copy layer structures so they can be safely mutated."""
        def _clone_lanes(lanes: List[KeyframeLane]) -> List[KeyframeLane]:
            return [
                KeyframeLane(name=lane.name, keyframes=[replace(kf) for kf in lane.keyframes])
                for lane in (lanes or [])
            ]

        return [
            replace(
                layer,
                keyframes=[replace(kf) for kf in layer.keyframes],
                extra_keyframe_lanes=_clone_lanes(getattr(layer, "extra_keyframe_lanes", [])),
            )
            for layer in layers
        ]

    def _duplicate_layer(
        self,
        layer: LayerData,
        *,
        new_id: int,
        new_name: str,
        anchor_layer: Optional[LayerData] = None,
        anchor_override: Optional[Tuple[float, float]] = None
    ) -> LayerData:
        """Return a deep copy of a layer with a new id/name.

        Args:
            layer: The source layer to copy keyframes and other properties from.
            new_id: The new layer ID.
            new_name: The new layer name.
            anchor_layer: Optional layer to copy anchor positions from. If None,
                         uses the source layer's anchors. CloneObjectAbove/BelowLayer
                         generates the clone entity from the source layer data, so
                         anchors should generally come from the source unless a
                         costume explicitly overrides them.
            anchor_override: Optional explicit (x, y) anchor values to use instead of
                           copying from anchor_layer or layer.
        """
        # Use explicit override if provided, otherwise anchor_layer, otherwise source layer
        if anchor_override is not None:
            anchor_x, anchor_y = anchor_override
        else:
            anchor_source = anchor_layer if anchor_layer is not None else layer
            anchor_x = anchor_source.anchor_x
            anchor_y = anchor_source.anchor_y
        return LayerData(
            name=new_name,
            layer_id=new_id,
            parent_id=layer.parent_id,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            blend_mode=layer.blend_mode,
            keyframes=[replace(kf) for kf in layer.keyframes],
            visible=layer.visible,
            shader_name=layer.shader_name,
            color_tint=copy.deepcopy(layer.color_tint),
            color_tint_hdr=copy.deepcopy(getattr(layer, "color_tint_hdr", None)),
            color_gradient=copy.deepcopy(getattr(layer, "color_gradient", None)),
            color_animator=copy.deepcopy(getattr(layer, "color_animator", None)),
            color_metadata=copy.deepcopy(getattr(layer, "color_metadata", None)),
            render_tags=set(layer.render_tags),
            sprite_anchor_map=copy.deepcopy(getattr(layer, "sprite_anchor_map", None)),
            has_depth=getattr(layer, "has_depth", False),
            extra_keyframe_lanes=[
                KeyframeLane(name=lane.name, keyframes=[replace(kf) for kf in lane.keyframes])
                for lane in getattr(layer, "extra_keyframe_lanes", [])
            ],
        )

    def _apply_clone_layers(
        self,
        layers: List[LayerData],
        clone_defs: List[Dict[str, Any]],
        remap_map: Dict[str, Dict[str, Any]],
        sheet_names: Set[str],
        layer_remap_overrides: Dict[int, Dict[str, Any]],
        *,
        label: str = "clone"
    ):
        """Insert cloned layers defined by costume metadata."""
        if not clone_defs:
            return

        def _coerce_int(value: Any) -> Optional[int]:
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return None
                try:
                    return int(stripped, 0)
                except ValueError:
                    return None
            return None

        def _signed_variant(value: Any) -> Optional[int]:
            coerced = _coerce_int(value)
            if coerced is None:
                return None
            coerced &= 0xFFFFFFFF
            return coerced if coerced < 0x80000000 else coerced - 0x100000000

        def _resolve_insert_mode(entry: Dict[str, Any]) -> Tuple[int, Optional[int]]:
            variant_signed = _signed_variant(entry.get('variant_index'))
            insert_raw = _coerce_int(entry.get('insert_mode'))
            if insert_raw is None:
                insert_raw = variant_signed if variant_signed is not None else 1
            resolved_mode = -1 if insert_raw < 0 else 1
            entry['_resolved_variant_index'] = variant_signed
            entry['_resolved_insert_mode'] = resolved_mode
            return resolved_mode, variant_signed

        layer_lookup = {layer.name.lower(): layer for layer in layers}
        next_id = max((layer.layer_id for layer in layers), default=-1)

        for entry in clone_defs:
            new_name = entry.get('new_layer') or entry.get('name')
            source_name = entry.get('source_layer') or entry.get('resource')
            reference_name = entry.get('reference_layer') or entry.get('sheet')
            insert_mode, variant_signed = _resolve_insert_mode(entry)

            if not new_name or not source_name:
                continue

            normalized_new = (new_name or "").strip().lower()
            normalized_source = (source_name or "").strip().lower()
            source_exact_exists = bool(normalized_source and normalized_source in layer_lookup)
            new_exact_exists = bool(normalized_new and normalized_new in layer_lookup)
            canonical_aliases = self.canonical_layer_names or set()
            source_is_canonical = normalized_source in canonical_aliases
            new_is_canonical = normalized_new in canonical_aliases

            should_swap = (
                label != "canonical"
                and new_name and source_name
                and (
                    (not source_exact_exists and new_exact_exists)
                    or (source_is_canonical and not new_is_canonical)
                )
            )
            if should_swap:
                source_name, new_name = new_name, source_name
                normalized_new, normalized_source = normalized_source, normalized_new
                entry['new_layer'] = new_name
                entry['name'] = new_name
                entry['source_layer'] = source_name
                entry['resource'] = source_name
                self.diagnostics.log_clone(
                    f"{label.title()} swapped legacy clone entry to '{new_name}' from '{source_name}'",
                    severity="DEBUG"
                )

            alias_exists = bool(normalized_new and normalized_new in layer_lookup)
            source_candidates = self._layer_name_variants(
                source_name,
                new_name,
                reference_name
            )
            source_layer = self._find_layer_in_lookup(layer_lookup, source_candidates)

            if not source_layer and source_name and new_name:
                # Legacy exports swapped new/source fields. Retry with swapped labels.
                legacy_candidates = self._layer_name_variants(
                    new_name,
                    source_name,
                    reference_name
                )
                source_layer = self._find_layer_in_lookup(layer_lookup, legacy_candidates)
                if source_layer:
                    source_name, new_name = new_name, source_name
                    entry['new_layer'] = new_name
                    entry['name'] = new_name
                    entry['source_layer'] = source_name
                    entry['resource'] = source_name

            if alias_exists and normalized_new in self.canonical_layer_names:
                self.diagnostics.log_clone(
                    f"{label.title()} skipped '{new_name}' because canonical clone already exists",
                    layer_id=layer_lookup[normalized_new].layer_id if normalized_new in layer_lookup else None,
                    severity="DEBUG"
                )
                # Already seeded from canonical clones.
                continue

            if not source_layer:
                self.diagnostics.log_clone(
                    f"{label.title()} source layer missing for '{new_name}' (source: {source_name})",
                    severity="WARNING"
                )
                self.log_widget.log(
                    f"{label.title()} source layer missing for '{new_name}' "
                    f"(source: {source_name})",
                    "WARNING"
                )
                continue

            reference_candidates = self._layer_name_variants(reference_name)
            reference_layer = None
            if reference_candidates:
                reference_layer = self._find_layer_in_lookup(layer_lookup, reference_candidates)
            if not reference_layer:
                self.diagnostics.log_clone(
                    f"{label.title()} reference layer missing for '{new_name}' (reference: {reference_name})",
                    severity="WARNING"
                )
                self.log_widget.log(
                    f"{label.title()} reference layer missing for '{new_name}' "
                    f"(reference: {reference_name})",
                    "WARNING"
                )
                continue

            entry['_resolved_reference_layer'] = reference_layer.name
            next_id += 1
            # Clone uses the source layer's anchor. The runtime's clone helper builds a
            # fresh entity from the source layer data, then inserts it above/below the
            # reference. Our insert ordering still honors the resolved reference layer.
            new_layer = self._duplicate_layer(
                source_layer,
                new_id=next_id,
                new_name=new_name,
                anchor_layer=source_layer
            )

            order_reference = self._resolve_overlay_reference(reference_layer, layer_lookup)
            ref_index = layers.index(order_reference or reference_layer)
            insert_idx = ref_index
            if insert_mode is not None:
                if insert_mode > 0:
                    insert_idx = max(0, ref_index)
                else:
                    insert_idx = min(len(layers), ref_index + 1)
            layers.insert(insert_idx, new_layer)
            normalized_name = new_name.lower()
            name_conflict = normalized_name in layer_lookup
            layer_lookup[normalized_name] = new_layer
            overlay_anchor = (
                order_reference
                if order_reference and order_reference is not reference_layer
                else None
            )
            force_opaque = False
            if overlay_anchor and overlay_anchor.name:
                new_layer.render_tags.add(f"overlay_ref:{overlay_anchor.name.lower()}")
                if reference_layer and reference_layer.name:
                    new_layer.render_tags.add(f"overlay_ref_source:{reference_layer.name.lower()}")

            # Costumes often clone shade/mask layers (low-opacity or tinted) to create opaque overlays.
            source_label = (source_layer.name or source_name or "").lower() if source_layer else ""
            new_label = (new_name or "").lower()
            shade_keywords = (" shade", "shadow", " mask")
            source_is_shade = any(keyword in source_label for keyword in shade_keywords)
            new_is_shade = any(keyword in new_label for keyword in shade_keywords)
            if source_is_shade and not new_is_shade:
                force_opaque = True

            # Allow remap lookups so costume sprites are applied to the clone.
            if alias_exists:
                remap_candidates = self._layer_name_variants(
                    source_name,
                    reference_name
                )
            else:
                remap_candidates = self._layer_name_variants(
                    new_name,
                    source_name,
                    reference_name
                )
            remap_info = self._alias_remap_entry(
                remap_map,
                new_name,
                *remap_candidates,
                update_map=not name_conflict
            )
            if remap_info:
                layer_remap_overrides[new_layer.layer_id] = remap_info
                if source_layer:
                    source_layer.render_tags.add("neutral_color")
                if self._remap_targets_costume_sheet(remap_info):
                    base_opacity = self._layer_default_opacity(source_layer)
                    if base_opacity is not None and base_opacity < 99.5:
                        force_opaque = True

            if force_opaque:
                new_layer.render_tags.add("overlay_force_opaque")

            if reference_name and reference_name.lower().endswith('.xml'):
                sheet_names.add(reference_name)

            self.diagnostics.log_clone(
                f"{label.title()} inserted '{new_name}' from '{source_name}' near '{reference_layer.name}'",
                layer_id=new_layer.layer_id,
                extra={
                    "reference": reference_layer.name,
                    "mode": "above" if (insert_mode and insert_mode > 0) else "below",
                    "variant": variant_signed
                }
            )

    def _alias_remap_entry(
        self,
        remap_map: Dict[str, Dict[str, Any]],
        alias_name: Optional[str],
        *candidates: Optional[str],
        update_map: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Map alias_name to the same remap info as the first candidate found."""
        if not alias_name:
            return None
        alias_key = alias_name.lower()
        for candidate in candidates:
            if not candidate:
                continue
            info = remap_map.get(candidate.lower())
            if info:
                if update_map and alias_key not in remap_map:
                    remap_map[alias_key] = info
                return info
        return None

    def _find_layer_in_lookup(
        self,
        lookup: Dict[str, LayerData],
        candidates: Tuple[Optional[str], ...]
    ) -> Optional[LayerData]:
        """Return first matching layer from a list of candidate names."""
        for candidate in candidates:
            if candidate and candidate.lower() in lookup:
                return lookup[candidate.lower()]
        return None

    def _normalize_layer_label(self, name: Optional[str]) -> Optional[str]:
        """Return a normalized layer label with trimmed whitespace and suffixes removed."""
        if not name:
            return None
        normalized = name.strip()
        if not normalized:
            return None
        normalized = re.sub(r"\s+", " ", normalized)
        simplified = re.sub(r"\s*\(\d+\)$", "", normalized).strip()
        return simplified or normalized

    def _layer_name_variants(self, *names: Optional[str]) -> Tuple[Optional[str], ...]:
        """Return candidate layer names including normalized fallbacks."""
        variants: List[Optional[str]] = []
        seen: Set[str] = set()
        for entry in names:
            for candidate in (entry, self._normalize_layer_label(entry)):
                if not candidate:
                    continue
                lower = candidate.lower()
                if lower in seen:
                    continue
                seen.add(lower)
                variants.append(candidate)
        return tuple(variants)

    def _apply_shader_overrides(
        self,
        layers: List[LayerData],
        shader_defs: List[Dict[str, str]]
    ):
        """Attach shader metadata to layers."""
        if not shader_defs:
            return

        lookup = {layer.name.lower(): layer for layer in layers}
        for shader in shader_defs:
            node = shader.get('node')
            shader_name = shader.get('resource')
            if not node or not shader_name:
                continue
            layer = self._match_layer_by_name(lookup, node)
            if not layer:
                self.log_widget.log(
                    f"Shader target not found: {node}",
                    "WARNING"
                )
                continue
            layer.shader_name = shader_name

    def _match_layer_by_name(
        self,
        lookup: Dict[str, LayerData],
        node_name: str
    ) -> Optional[LayerData]:
        """Return the best matching layer for a shader node string."""
        normalized = node_name.lower()
        if normalized in lookup:
            return lookup[normalized]

        stripped = normalized
        if '.' in stripped:
            stripped = stripped.rsplit('.', 1)[0]
        if stripped in lookup:
            return lookup[stripped]

        stripped = re.sub(r'\s+\d+$', '', stripped)
        return lookup.get(stripped)

    def _reset_costume_runtime_state(
        self,
        layers: Optional[List[LayerData]] = None
    ) -> None:
        """Clear costume attachment and alias state between animation loads."""
        animation_layers = layers
        if animation_layers is None:
            animation = getattr(self.gl_widget.player, "animation", None)
            animation_layers = animation.layers if animation else []
        self.active_costume_key = None
        self.active_costume_attachments = []
        self.costume_sheet_aliases.clear()
        self.gl_widget.set_costume_attachments([], animation_layers or [])

    def _refresh_attachment_panel(self) -> None:
        """Sync the layer panel attachment list with the active costume attachments."""
        if not hasattr(self, "layer_panel") or not hasattr(self, "gl_widget"):
            return
        entries = self.gl_widget.get_attachment_entries() if self.gl_widget else []
        self.layer_panel.update_attachments(entries)
        if not entries:
            self.selected_attachment_id = None
            self.layer_panel.set_attachment_selection(None)
            if self.gl_widget:
                self.gl_widget.set_attachment_selection(None)
            return
        entry_ids = {entry[0] for entry in entries}
        if self.selected_attachment_id not in entry_ids:
            self.selected_attachment_id = None
            self.layer_panel.set_attachment_selection(None)
            if self.gl_widget:
                self.gl_widget.set_attachment_selection(None)

    def _apply_costume_to_animation(self, entry: Optional[CostumeEntry]):
        """Apply or remove a costume by rebuilding layer data and texture atlases."""
        animation = self.gl_widget.player.animation
        if not animation or not self.base_layer_cache:
            return

        if entry is None:
            animation.layers = self._clone_layers(self.base_layer_cache)
            self.gl_widget.texture_atlases = list(self.base_texture_atlases)
            self.gl_widget.set_layer_atlas_overrides({})
            self.gl_widget.set_layer_pivot_context({})
            self._reset_costume_runtime_state(animation.layers)
            self._configure_costume_shaders(None, None)
            self._refresh_attachment_panel()
            self.gl_widget.update()
            self.update_layer_panel()
            self._capture_pose_baseline()
            self._reset_edit_history()
            self._refresh_timeline_keyframes()
            return

        costume_data = self._load_costume_definition(entry)
        if not costume_data:
            return

        attachments = costume_data.get('ae_anim_layers', [])
        self.active_costume_attachments = self._prepare_costume_attachments(attachments)
        attachment_payloads = self._build_attachment_payloads(self.active_costume_attachments)
        if attachments and not attachment_payloads:
            self.log_widget.log(
                f"Costume defines {len(attachments)} attachment(s) but none could be loaded.",
                "WARNING"
            )

        layers = self._clone_layers(self.base_layer_cache)
        remap_map, sheet_names = self._build_remap_map(costume_data.get('remaps', []))
        sheet_alias, alias_targets = self._normalize_sheet_remaps(
            costume_data.get('sheet_remaps') or costume_data.get('swaps', [])
        )
        layer_remap_overrides: Dict[int, Dict[str, Any]] = {}
        self.costume_sheet_aliases = sheet_alias
        sheet_names.update(alias_targets)
        self._apply_clone_layers(
            layers,
            costume_data.get('clone_layers', []),
            remap_map,
            sheet_names,
            layer_remap_overrides,
            label="clone"
        )
        self._apply_remaps_to_layers(layers, remap_map, layer_remap_overrides)
        self._update_canonical_clone_visibility(layers, remap_map, layer_remap_overrides)
        self._apply_shader_overrides(layers, costume_data.get('apply_shader', []))
        explicit_blend_layer_ids = self._apply_blend_overrides(
            layers,
            costume_data.get('set_blend_layers', [])
        )
        self._normalize_costume_layer_blends(
            layers,
            remap_map,
            layer_remap_overrides,
            explicit_blend_layer_ids=explicit_blend_layer_ids,
        )
        layer_color_data = (
            costume_data.get('layer_colors')
            or costume_data.get('layer_color_overrides')
        )
        self._apply_layer_color_overrides(layers, layer_color_data)
        self._enforce_costume_overlay_order(layers)
        self._assign_attachment_targets(attachment_payloads, layers)

        costume_atlases = self._load_costume_atlases(sheet_names)
        base_atlases = self._apply_sheet_aliases_to_base_atlases(self.base_texture_atlases, sheet_alias)
        combined_atlases: List[TextureAtlas] = []
        for atlas in costume_atlases:
            if atlas not in combined_atlases:
                combined_atlases.append(atlas)
        for atlas in base_atlases:
            if atlas not in combined_atlases:
                combined_atlases.append(atlas)
        self.gl_widget.texture_atlases = combined_atlases

        animation.layers = layers
        self._record_layer_defaults(animation.layers)
        self.active_costume_key = entry.key
        overrides, pivot_context = self._build_layer_atlas_overrides(
            layers,
            remap_map,
            layer_remap_overrides,
            costume_atlases,
            sheet_alias,
        )
        self.gl_widget.set_layer_atlas_overrides(overrides)
        self.gl_widget.set_layer_pivot_context(pivot_context)
        self._configure_costume_shaders(entry, costume_data)
        self.gl_widget.set_costume_attachments(attachment_payloads, layers)
        self._refresh_attachment_panel()
        self.gl_widget.update()
        self.update_layer_panel()
        self.gl_widget.set_anchor_logging_enabled(self.anchor_debug_enabled)
        if self.anchor_debug_enabled:
            QTimer.singleShot(500, lambda: self._dump_anchor_debug())
        self._capture_pose_baseline()
        self._reset_edit_history()
        self._refresh_timeline_keyframes()

    def _build_remap_map(
        self, remaps: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Dict[str, Any]], Set[str]]:
        """Return per-layer remap information and the atlas sheets it needs."""
        remap_dict: Dict[str, Dict[str, Any]] = {}
        sheets: Set[str] = set()
        for entry in remaps or []:
            name = entry.get('display_name')
            if not name:
                continue
            frame_mappings = entry.get('frame_mappings', [])
            frame_exact: Dict[str, str] = {}
            frame_lower: Dict[str, str] = {}
            for mapping in frame_mappings:
                src = mapping.get('from')
                dst = mapping.get('to')
                if not src or not dst:
                    continue
                frame_exact[src] = dst
                frame_lower[src.lower()] = dst
            remap_info = {
                'display_name': entry.get('display_name', ''),
                'resource': entry.get('resource', ''),
                'frame_exact': frame_exact,
                'frame_lower': frame_lower,
                'sheet': entry.get('sheet', '')
            }
            remap_dict[name.lower()] = remap_info
            sheet_name = entry.get('sheet')
            if sheet_name:
                sheets.add(sheet_name)
        return remap_dict, sheets

    def _build_layer_atlas_overrides(
        self,
        layers: List[LayerData],
        remap_map: Dict[str, Dict[str, Any]],
        layer_remap_overrides: Dict[int, Dict[str, Any]],
        costume_atlases: List[TextureAtlas],
        sheet_aliases: Dict[str, List[str]]
    ) -> Tuple[Dict[int, List[TextureAtlas]], Dict[int, bool]]:
        """Map layer ids to the costume atlases they should search first."""
        overrides: Dict[int, List[TextureAtlas]] = {}
        pivot_context: Dict[int, bool] = {}
        if (not remap_map and not layer_remap_overrides) or not costume_atlases:
            return overrides, pivot_context
        atlas_lookup: Dict[str, List[TextureAtlas]] = {}
        for atlas in costume_atlases:
            keys = self._canonical_sheet_keys(getattr(atlas, "source_name", None) or atlas.image_path)
            for key in keys:
                atlas_lookup.setdefault(key, []).append(atlas)
        def _has_valid_sheet(value: Optional[str]) -> bool:
            if not value:
                return False
            normalized = value.strip().lower()
            if not normalized:
                return False
            return normalized not in {"empty", "empty.xml"}

        def _has_sprite_resource(info: Optional[Dict[str, Any]]) -> bool:
            if not info:
                return False
            resource = (info.get('resource') or '').strip().lower()
            if not resource:
                return False
            return resource not in {"empty"}

        for layer in layers:
            info = layer_remap_overrides.get(layer.layer_id)
            if not info:
                info = remap_map.get(layer.name.lower())
            sheet = (info or {}).get('sheet')
            if _has_valid_sheet(sheet) and _has_sprite_resource(info):
                pivot_context[layer.layer_id] = True
            keys = self._canonical_sheet_keys(sheet)
            matched = self._resolve_atlases_for_keys(keys, atlas_lookup)
            if not matched and keys:
                alias_targets: Set[str] = set()
                for key in keys:
                    for alias_target in sheet_aliases.get(key, []):
                        alias_targets.add(alias_target)
                alias_keys: Set[str] = set()
                for alias_name in alias_targets:
                    alias_keys.update(self._canonical_sheet_keys(alias_name))
                matched = self._resolve_atlases_for_keys(alias_keys, atlas_lookup)
            if matched:
                overrides[layer.layer_id] = matched
        return overrides, pivot_context

    def _resolve_atlases_for_keys(
        self,
        keys: Set[str],
        atlas_lookup: Dict[str, List[TextureAtlas]]
    ) -> Optional[List[TextureAtlas]]:
        for key in keys:
            bucket = atlas_lookup.get(key)
            if bucket:
                return bucket
        return None

    def _canonical_sheet_keys(self, sheet: Optional[str]) -> Set[str]:
        """Return normalized identifiers for a sheet path."""
        keys: Set[str] = set()
        if not sheet:
            return keys
        normalized = sheet.replace("\\", "/").strip()
        lowered = normalized.lower()
        if lowered:
            keys.add(lowered)
        try:
            path = Path(normalized)
        except Exception:
            path = Path(normalized.replace(":", "", 1))
        name = path.name.lower()
        if name:
            keys.add(name)
            stem = Path(name).stem.lower()
            if stem:
                keys.add(stem)
            base = self._sheet_base_name(name)
            if base:
                keys.add(base.lower())
        parent = path.parent
        if parent and parent.name:
            parent_name = parent.name.lower()
            if parent_name:
                if name:
                    keys.add(f"{parent_name}/{name}")
                stem = Path(name).stem.lower() if name else ""
                if stem:
                    keys.add(f"{parent_name}/{stem}")
        if ":" in lowered:
            suffix = lowered.split(":", 1)[1].lstrip("/")
            if suffix:
                keys.add(suffix)
        return {key for key in keys if key}

    def _normalize_sheet_remaps(
        self, remaps: List[Dict[str, str]]
    ) -> Tuple[Dict[str, List[str]], Set[str]]:
        """Normalize sheet remap entries into alias maps and target sheet names."""
        alias: Dict[str, List[str]] = {}
        targets: Set[str] = set()
        for entry in remaps or []:
            source = entry.get('from')
            target = entry.get('to')
            if not source or not target:
                continue
            for source_key in self._canonical_sheet_keys(source):
                bucket = alias.setdefault(source_key, [])
                if target not in bucket:
                    bucket.append(target)
            targets.add(target)
        return alias, targets

    def _apply_remaps_to_layers(
        self,
        layers: List[LayerData],
        remap_map: Dict[str, Dict[str, Any]],
        layer_remap_overrides: Dict[int, Dict[str, Any]]
    ):
        """Mutate keyframes according to per-layer remap definitions."""
        for layer in layers:
            render_tags = getattr(layer, "render_tags", set())
            force_full_opacity = (
                isinstance(render_tags, set)
                and "overlay_force_opaque" in render_tags
            )
            remap_info = layer_remap_overrides.get(layer.layer_id)
            if not remap_info:
                remap_info = remap_map.get(layer.name.lower())
            if not remap_info:
                continue
            has_custom_color = self._layer_has_costume_color(layer)
            if not has_custom_color:
                render_tags.add("neutral_color")
            for keyframe in layer.keyframes:
                sprite_name = keyframe.sprite_name or ""
                remapped = self._remap_sprite(sprite_name, remap_info)
                changed = remapped != sprite_name
                if changed:
                    keyframe.sprite_name = remapped
                if not has_custom_color and (changed or force_full_opacity):
                    self._neutralize_keyframe_color(keyframe)
                if force_full_opacity and (changed or remap_info.get("resource")):
                    self._force_keyframe_opacity(keyframe)

    def _remap_sprite(self, sprite_name: str, remap_info: Dict[str, Any]) -> str:
        """Return a sprite name after applying frame-based remaps."""
        if not sprite_name:
            return remap_info.get('resource') or sprite_name
        mapping = remap_info.get('frame_exact', {})
        lowered = remap_info.get('frame_lower', {})
        new_name = mapping.get(sprite_name)
        if new_name is None:
            new_name = lowered.get(sprite_name.lower())
        if new_name is None:
            fallback = remap_info.get('resource')
            return fallback if fallback else sprite_name
        return new_name

    @staticmethod
    def _neutralize_keyframe_color(keyframe: KeyframeData) -> None:
        """Reset RGB multipliers so costume sprites render with authored colours."""
        keyframe.r = 255
        keyframe.g = 255
        keyframe.b = 255
        if keyframe.immediate_rgb is None or keyframe.immediate_rgb < 0:
            keyframe.immediate_rgb = 0

    @staticmethod
    def _force_keyframe_opacity(
        keyframe: KeyframeData,
        value: float = 100.0
    ) -> None:
        """Clamp opacity to an explicit value so overlays stay opaque."""
        keyframe.opacity = value
        if keyframe.immediate_opacity is None or keyframe.immediate_opacity < 0:
            keyframe.immediate_opacity = 0

    def _resolve_blend_override_value(self, override: Dict[str, Any]) -> Optional[int]:
        """Parse a costume blend override from int or string payloads."""
        raw_value = (
            override.get("blend_value")
            if "blend_value" in override
            else override.get("blend")
        )
        if raw_value is None:
            raw_value = override.get("blend_mode")
        if raw_value is None:
            raw_value = override.get("mode")
        if raw_value is None:
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            pass
        lowered = str(raw_value).strip().lower()
        if not lowered:
            return None
        if lowered in {"0", "standard", "normal"}:
            return BlendMode.STANDARD
        if lowered in {"1", "add", "additive"}:
            return 1
        if lowered in {"premult", "premult_alpha", "alpha"}:
            return BlendMode.PREMULT_ALPHA
        if lowered in {"premult_alpha_alt", "premult_alt"}:
            return BlendMode.PREMULT_ALPHA_ALT
        if lowered in {"premult_alpha_alt2", "premult_alt2"}:
            return BlendMode.PREMULT_ALPHA_ALT2
        if lowered in {"2", "multiply"}:
            return BlendMode.MULTIPLY
        if lowered in {"screen"}:
            return BlendMode.SCREEN
        if lowered in {"inherit"}:
            return BlendMode.INHERIT
        return None

    def _apply_blend_overrides(
        self, layers: List[LayerData], overrides: List[Dict[str, Any]]
    ) -> Set[int]:
        """Update blend modes specified by the costume definition."""
        applied: Set[int] = set()
        if not overrides:
            return applied
        lookup = {layer.name.lower(): layer for layer in layers}
        for override in overrides:
            layer_name = override.get("name") or override.get("layer") or override.get("node")
            if not layer_name:
                continue
            layer = self._match_layer_by_name(lookup, str(layer_name))
            if not layer:
                continue
            raw_value = self._resolve_blend_override_value(override)
            if raw_value is None:
                continue
            layer.blend_mode = self._normalize_blend_value(raw_value, self.current_blend_version or 1)
            applied.add(layer.layer_id)
        return applied

    def _apply_layer_color_overrides(
        self,
        layers: List[LayerData],
        overrides: Optional[List[Dict[str, Any]]]
    ):
        """Attach per-layer color tint overrides emitted by the costume parser."""
        if not overrides:
            return
        lookup = {layer.name.lower(): layer for layer in layers}
        for entry in overrides:
            layer_name = entry.get("layer") or entry.get("name")
            if not layer_name:
                continue
            layer = self._match_layer_by_name(lookup, layer_name)
            if not layer:
                continue
            profile = self._build_layer_color_profile(entry)
            if not profile:
                continue
            gradient_info = self._build_gradient_definition(entry)
            animation_info = self._build_color_animation_definition(entry)

            base_tint = profile.get("srgb")
            hdr_tint = profile.get("hdr")

            has_dynamic = bool(gradient_info or animation_info)
            if base_tint:
                if not has_dynamic and self._color_tuple_is_identity(base_tint):
                    layer.color_tint = None
                else:
                    layer.color_tint = base_tint
            if hdr_tint:
                layer.color_tint_hdr = hdr_tint

            if gradient_info:
                layer.color_gradient = gradient_info
            if animation_info:
                layer.color_animator = animation_info

            metadata = dict(entry)
            metadata["_color_profile"] = profile
            layer.color_metadata = metadata

    def _build_layer_color_profile(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return both SDR and HDR-friendly tuples for a layer color entry."""
        rgba8 = entry.get("rgba") or {}
        rgba16 = entry.get("rgba16") or {}
        srgb: List[float] = []
        hdr: List[float] = []

        def _extract_value(payload: Dict[str, Any], key: str) -> Optional[float]:
            value = payload.get(key)
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        for channel in ("r", "g", "b", "a"):
            eight_bit = _extract_value(rgba8, channel)
            sixteen_bit = _extract_value(rgba16, channel)

            # SDR color prioritizes 8-bit data, then falls back to 16-bit precision.
            source = eight_bit if eight_bit is not None else sixteen_bit
            scale = 255.0 if eight_bit is not None else 65535.0
            if source is None or scale <= 0:
                srgb.append(1.0)
            else:
                srgb.append(max(0.0, min(scale, source)) / scale)

            # HDR preview prioritizes 16-bit data to preserve range/precision.
            hdr_source = sixteen_bit if sixteen_bit is not None else eight_bit
            hdr_scale = 65535.0 if sixteen_bit is not None else 255.0
            if hdr_source is None or hdr_scale <= 0:
                hdr.append(1.0)
            else:
                hdr.append(max(0.0, hdr_source) / hdr_scale)

        profile = {
            "srgb": tuple(srgb),
            "hdr": tuple(hdr),
            "rgba": rgba8,
            "rgba16": rgba16,
            "hex": entry.get("hex"),
        }
        return profile

    def _color_tuple_is_identity(self, tint: Tuple[float, float, float, float]) -> bool:
        return all(abs(component - 1.0) < 1e-4 for component in tint)

    def _layer_has_costume_color(self, layer: LayerData) -> bool:
        """Return True if the costume authored a tint/gradient for this layer."""
        if getattr(layer, "color_gradient", None) or getattr(layer, "color_animator", None):
            return True
        metadata = getattr(layer, "color_metadata", None)
        if not isinstance(metadata, dict):
            return False
        profile = metadata.get("_color_profile")
        if not isinstance(profile, dict):
            return False
        srgb = profile.get("srgb")
        if isinstance(srgb, (tuple, list)) and len(srgb) == 4:
            if not self._color_tuple_is_identity(tuple(srgb)):
                return True
        return False

    @staticmethod
    def _layer_default_opacity(layer: Optional[LayerData]) -> Optional[float]:
        """Return the first authored opacity for a layer, if available."""
        if not layer or not layer.keyframes:
            return None
        for keyframe in layer.keyframes:
            if keyframe.immediate_opacity != -1:
                return keyframe.opacity
        return layer.keyframes[0].opacity

    def _resolve_overlay_reference(
        self,
        reference_layer: LayerData,
        layer_lookup: Dict[str, LayerData]
    ) -> Optional[LayerData]:
        """
        Return an alternate ordering anchor when the reference layer is a shade/mask.
        Costume overlays often reference the shading layer to inherit transforms but
        should render above the primary sprite (e.g., apron over body shade). Strip
        known suffixes to locate the base layer when available.
        """
        name = (reference_layer.name or "").strip()
        if not name:
            return None
        lowered = name.lower()
        for suffix in (" shade", " shadow", " mask"):
            if lowered.endswith(suffix):
                candidate_name = name[: -len(suffix)].strip()
                if candidate_name:
                    candidate = layer_lookup.get(candidate_name.lower())
                    if candidate:
                        return candidate
        return None

    @staticmethod
    def _remap_targets_costume_sheet(remap_info: Dict[str, Any]) -> bool:
        """Detect if the remap swaps sprites using a dedicated costume atlas."""
        sheet = (remap_info.get("sheet") or "").strip().lower()
        if not sheet:
            return False
        if "costume" in sheet:
            return True
        # Normalize to basename if path-like
        if "/" in sheet:
            sheet = sheet.rsplit("/", 1)[-1]
        return "costume" in sheet

    @staticmethod
    def _overlay_anchor_name(layer: LayerData) -> Optional[str]:
        """Return the overlay anchor stored in render tags, if any."""
        for tag in getattr(layer, "render_tags", set()):
            if tag.startswith("overlay_ref:"):
                anchor = tag.split(":", 1)[1].strip().lower()
                if anchor:
                    return anchor
        return None

    @staticmethod
    def _overlay_reference_name(layer: LayerData) -> Optional[str]:
        """Return the shading/mask reference stored in render tags, if any."""
        for tag in getattr(layer, "render_tags", set()):
            if tag.startswith("overlay_ref_source:"):
                ref = tag.split(":", 1)[1].strip().lower()
                if ref:
                    return ref
        return None

    def _enforce_costume_overlay_order(self, layers: List[LayerData]) -> None:
        """Ensure costume overlays render in front of their base sprites."""
        if not layers:
            return
        name_lookup = {
            (layer.name or "").strip().lower(): layer
            for layer in layers
            if layer.name
        }
        for layer in list(layers):
            anchor_name = self._overlay_anchor_name(layer)
            reference_name = self._overlay_reference_name(layer)
            if not anchor_name and not reference_name:
                continue
            anchor = name_lookup.get(anchor_name) if anchor_name else None
            reference = name_lookup.get(reference_name) if reference_name else None
            # Determine the earliest index we must precede.
            candidate_indices: List[int] = []
            if anchor and anchor is not layer:
                candidate_indices.append(layers.index(anchor))
            if reference and reference is not layer:
                candidate_indices.append(layers.index(reference))
            if not candidate_indices:
                continue
            target_index = min(candidate_indices)
            current_index = layers.index(layer)
            if current_index < target_index:
                continue
            # Move overlay directly before the earliest dependency so it draws last
            # once the renderer reverses the layer list.
            layers.insert(target_index, layers.pop(current_index))

    def _normalize_costume_layer_blends(
        self,
        layers: List[LayerData],
        remap_map: Dict[str, Dict[str, Any]],
        layer_remap_overrides: Dict[int, Dict[str, Any]],
        explicit_blend_layer_ids: Optional[Set[int]] = None,
    ) -> None:
        """Normalize remapped clone blends while preserving explicitly authored overrides."""
        explicit_ids = set(explicit_blend_layer_ids or set())
        for layer in layers:
            if layer.layer_id in explicit_ids:
                continue
            remap_info = layer_remap_overrides.get(layer.layer_id)
            if not remap_info:
                remap_info = remap_map.get(layer.name.lower())
            if not remap_info:
                continue
            if not self._remap_targets_costume_sheet(remap_info):
                continue
            tags = set(getattr(layer, "render_tags", set()) or set())
            should_force_standard = (
                "overlay_force_opaque" in tags
                or any(tag.startswith("overlay_ref:") for tag in tags)
            )
            if should_force_standard:
                layer.blend_mode = BlendMode.STANDARD

    def _build_gradient_definition(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a normalized gradient definition if the entry specifies one."""
        gradient_source = self._extract_gradient_source(entry)
        if not gradient_source:
            return None
        stops = self._normalize_color_stops(gradient_source)
        if len(stops) < 2:
            return None
        info = {
            "stops": stops,
            "mode": (entry.get("gradient_mode") or entry.get("mode") or "loop").lower(),
            "loop": entry.get("loop"),
            "period": self._coerce_float(
                entry.get("gradient_period")
                or entry.get("period")
                or entry.get("duration")
            ),
            "offset": self._coerce_float(
                entry.get("gradient_offset") or entry.get("start_time") or entry.get("offset")
            ) or 0.0,
            "speed": self._coerce_float(
                entry.get("gradient_speed") or entry.get("speed") or entry.get("tempo_multiplier")
            ) or 1.0,
            "ping_pong": bool(entry.get("ping_pong")),
            "metadata": entry,
        }
        return info

    def _extract_gradient_source(self, entry: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Return a list of gradient stops if one is defined."""
        candidates = [
            entry.get("gradient"),
            entry.get("gradient_stops"),
            entry.get("color_ramp"),
            entry.get("ramp"),
        ]
        for candidate in candidates:
            if isinstance(candidate, dict):
                nested = candidate.get("stops") or candidate.get("colors")
                if isinstance(nested, list):
                    return nested
            elif isinstance(candidate, list):
                return candidate
        if entry.get("mode") in {"gradient", "ramp"} and isinstance(entry.get("stops"), list):
            return entry.get("stops")
        return None

    def _normalize_color_stops(self, stop_entries: List[Any]) -> List[Dict[str, Any]]:
        """Normalize gradient stop payloads into sorted tuples."""
        stops: List[Dict[str, Any]] = []
        max_time: Optional[float] = None
        for idx, stop in enumerate(stop_entries):
            if not isinstance(stop, dict):
                continue
            profile = self._build_layer_color_profile(stop)
            if not profile:
                continue
            position = self._coerce_float(
                stop.get("position")
                or stop.get("offset")
                or stop.get("t")
                or stop.get("percent")
            )
            absolute_time = self._coerce_float(stop.get("time") or stop.get("frame"))
            if position is None and absolute_time is None:
                if len(stop_entries) > 1:
                    position = idx / float(len(stop_entries) - 1)
                else:
                    position = 0.0
            if absolute_time is not None:
                max_time = max(max_time or absolute_time, absolute_time)
            stops.append(
                {
                    "position": position,
                    "time": absolute_time,
                    "color": profile["srgb"],
                    "hdr": profile["hdr"],
                    "hex": profile.get("hex"),
                    "interpolation": stop.get("interpolation") or stop.get("mode"),
                    "source": stop,
                }
            )
        if not stops:
            return []
        if all(stop["position"] is None for stop in stops):
            if max_time and max_time > 0:
                for stop in stops:
                    if stop["time"] is not None:
                        stop["position"] = max(0.0, min(1.0, stop["time"] / max_time))
            if all(stop["position"] is None for stop in stops):
                for idx, stop in enumerate(stops):
                    stop["position"] = idx / float(max(1, len(stops) - 1))
        stops.sort(key=lambda item: item["position"])
        return stops

    def _build_color_animation_definition(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a normalized color animation curve if one is defined."""
        frames, frame_source = self._extract_animation_frames(entry)
        if not frames:
            return None
        keyframes = self._normalize_color_keyframes(frames)
        if len(keyframes) < 2:
            return None
        duration = self._coerce_float(
            entry.get("animation_duration")
            or (frame_source.get("duration") if isinstance(frame_source, dict) else None)
            or entry.get("duration")
        )
        if not duration:
            duration = keyframes[-1]["time"]
        if duration is None or duration <= 0:
            duration = max(keyframes[-1]["time"], 0.001)
        info = {
            "keyframes": keyframes,
            "duration": duration,
            "loop": entry.get("loop", True),
            "mode": (entry.get("animation_mode") or entry.get("mode") or "loop").lower(),
            "offset": self._coerce_float(entry.get("start_time") or entry.get("offset")) or 0.0,
            "speed": self._coerce_float(
                entry.get("animation_speed") or entry.get("speed") or entry.get("tempo_multiplier")
            ) or 1.0,
            "metadata": entry,
        }
        return info

    def _extract_animation_frames(
        self, entry: Dict[str, Any]
    ) -> Tuple[Optional[List[Any]], Optional[Dict[str, Any]]]:
        """Return a timeline list from various schema permutations."""
        for key in ("animation", "timeline", "keyframes", "frames"):
            payload = entry.get(key)
            if isinstance(payload, dict):
                seq = payload.get("keyframes") or payload.get("frames")
                if isinstance(seq, list):
                    return seq, payload
            elif isinstance(payload, list):
                return payload, entry
        animated = entry.get("animated") or entry.get("anim")
        if isinstance(animated, dict):
            seq = animated.get("keyframes") or animated.get("frames")
            if isinstance(seq, list):
                return seq, animated
        return None, None

    def _normalize_color_keyframes(self, frames: List[Any]) -> List[Dict[str, Any]]:
        """Normalize animation keyframes and align their timeline to start at 0."""
        keyframes: List[Dict[str, Any]] = []
        for idx, frame in enumerate(frames):
            if not isinstance(frame, dict):
                continue
            profile = self._build_layer_color_profile(frame)
            if not profile:
                continue
            time_value = self._coerce_float(frame.get("time") or frame.get("t") or frame.get("frame"))
            if time_value is None:
                time_value = float(idx)
            keyframes.append(
                {
                    "time": time_value,
                    "color": profile["srgb"],
                    "hdr": profile["hdr"],
                    "hex": profile.get("hex"),
                    "interpolation": frame.get("interpolation") or frame.get("mode"),
                    "source": frame,
                }
            )
        if not keyframes:
            return []
        keyframes.sort(key=lambda item: item["time"])
        base_time = keyframes[0]["time"]
        for keyframe in keyframes:
            keyframe["time"] -= base_time
        return keyframes

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return float(stripped)
            except ValueError:
                return None
        return None

    def _load_costume_atlases(self, sheet_names: Set[str]) -> List[TextureAtlas]:
        """Load texture atlases referenced by a costume, reusing cached textures when possible."""
        atlases: List[TextureAtlas] = []
        if not sheet_names:
            return atlases
        need_context = bool(sheet_names)
        if need_context:
            self.gl_widget.makeCurrent()
        try:
            for sheet in sheet_names:
                xml_path = self._resolve_costume_sheet_path(sheet)
                if not xml_path:
                    self.log_widget.log(f"Costume atlas not found: {sheet}", "WARNING")
                    continue
                norm = os.path.normcase(os.path.normpath(xml_path))
                atlas = self.costume_atlas_cache.get(norm)
                desired_unpremult = bool(self.legacy_animation_active)
                if not atlas:
                    atlas = self._load_texture_atlas_document(xml_path)
                    if not atlas:
                        self.log_widget.log(f"Failed to parse costume atlas: {sheet}", "ERROR")
                        continue
                    atlas.fuzzy_lookup_enabled = False
                    atlas.force_unpremultiply = desired_unpremult
                    if not atlas.load_texture():
                        self.log_widget.log(f"Failed to upload costume atlas texture: {sheet}", "ERROR")
                        continue
                    atlas.source_name = sheet
                    self.costume_atlas_cache[norm] = atlas
                else:
                    prev_flag = bool(getattr(atlas, "force_unpremultiply", False))
                    atlas.force_unpremultiply = desired_unpremult
                    atlas.fuzzy_lookup_enabled = False
                    if prev_flag != desired_unpremult and getattr(atlas, "texture_id", None):
                        if need_context:
                            atlas.load_texture()
                atlases.append(atlas)
        finally:
            if need_context:
                self.gl_widget.doneCurrent()
        return atlases


    def _resolve_costume_sheet_path(self, sheet: str) -> Optional[str]:
        """Resolve a costume XML path relative to multiple search roots."""
        if not sheet:
            return None
        if os.path.isabs(sheet) and os.path.exists(sheet):
            return sheet
        relative_variants = self._resource_relative_variants(sheet)
        candidates: List[str] = []
        for data_root in self._candidate_data_roots():
            for variant in relative_variants:
                candidates.append(os.path.join(data_root, *variant.split("/")))
        if self.current_json_path:
            base_dir = os.path.dirname(self.current_json_path)
            for variant in relative_variants:
                candidates.append(os.path.join(base_dir, *variant.split("/")))
        candidates.append(os.path.join(str(self.project_root), sheet))
        seen: Set[str] = set()
        for candidate in candidates:
            norm = os.path.normcase(os.path.normpath(candidate))
            if norm in seen:
                continue
            seen.add(norm)
            if os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def _resource_relative_variants(resource: str, *, include_xml_resources: bool = True) -> List[str]:
        """Return normalized relative paths to probe for an XML manifest."""
        variants: List[str] = []
        if not resource:
            return variants
        cleaned = resource.replace("\\", "/").strip()
        if not cleaned:
            return variants
        cleaned = cleaned.lstrip("./")

        def add_variant(value: str) -> None:
            if value and value not in variants:
                variants.append(value)

        add_variant(cleaned)
        lowered = cleaned.lower()
        if lowered.startswith("data/"):
            add_variant(cleaned[5:])
        basename = os.path.basename(cleaned)
        add_variant(basename)
        if include_xml_resources and basename and not lowered.startswith("xml_resources/"):
            add_variant(f"xml_resources/{basename}")
        return variants

    @staticmethod
    def _infer_data_root_from_path(xml_path: str) -> Optional[str]:
        """Best-effort inference of the data directory for a given XML path."""
        try:
            resolved = Path(xml_path).resolve()
        except OSError:
            return None
        parent = resolved.parent
        if parent.name.lower() in {"xml_resources", "xml_resources_dof"}:
            return str(parent.parent) if parent.parent else str(parent)
        for parent in resolved.parents:
            if parent.name.lower() == "data":
                return str(parent)
        return str(resolved.parent) if resolved.parent else None

    def _candidate_data_roots(self, xml_path: Optional[str] = None) -> List[str]:
        """Collect potential data directories for resolving XML + texture references."""
        roots: List[str] = []
        seen: Set[str] = set()

        def add_root(path: Optional[str]) -> None:
            if not path:
                return
            norm = os.path.normcase(os.path.normpath(path))
            if norm in seen:
                return
            if os.path.isdir(path):
                seen.add(norm)
                roots.append(path)

        if xml_path:
            inferred = self._infer_data_root_from_path(xml_path)
            add_root(inferred)

        for xml_bin_root in self._get_downloads_xml_bin_roots():
            parent = Path(xml_bin_root).parent
            add_root(str(parent))
            if parent.name.lower() == "data":
                add_root(str(parent.parent))
            data_child = parent / "data"
            if data_child.is_dir():
                add_root(str(data_child))

        if self.game_path:
            game_path = Path(self.game_path)
            if (game_path / "xml_resources").is_dir() or (game_path / "gfx").is_dir():
                add_root(str(game_path))
            add_root(str(game_path / "data"))

        if self.dof_path:
            add_root(self.dof_path)

        repo_root = self.project_root.parent
        if repo_root and repo_root.is_dir():
            for entry in repo_root.iterdir():
                try:
                    if not entry.is_dir():
                        continue
                    data_dir = entry / "data"
                    xml_dir = data_dir / "xml_resources"
                    if data_dir.is_dir() and xml_dir.is_dir():
                        add_root(str(data_dir))
                except OSError:
                    # Skip directories (e.g., AppX sandboxes) that have broken ACLs.
                    continue

        return roots

    def _load_texture_atlas_document(self, xml_path: str) -> Optional[TextureAtlas]:
        """Parse an atlas XML manifest, probing multiple texture roots if needed."""
        data_roots = self._candidate_data_roots(xml_path)
        if not data_roots and self.game_path:
            data_roots = [os.path.join(self.game_path, "data")]
        if not data_roots:
            return None

        resolved_xml = Path(xml_path)
        containing_root = self._infer_data_root_from_path(xml_path)
        relative_xml: Optional[str] = None
        if containing_root:
            try:
                relative_xml = str(resolved_xml.resolve().relative_to(Path(containing_root).resolve()))
            except (ValueError, OSError):
                relative_xml = None

        attempted: Set[Tuple[str, str]] = set()
        for data_root in data_roots:
            if containing_root and os.path.normcase(os.path.normpath(containing_root)) == os.path.normcase(os.path.normpath(data_root)):
                candidate_path = xml_path
            elif relative_xml:
                candidate_path = os.path.join(data_root, relative_xml)
            else:
                candidate_path = xml_path
            norm_pair = (
                os.path.normcase(os.path.normpath(candidate_path)),
                os.path.normcase(os.path.normpath(data_root)),
            )
            if norm_pair in attempted:
                continue
            attempted.add(norm_pair)
            if not os.path.exists(candidate_path):
                continue
            atlas = TextureAtlas()
            if atlas.load_from_xml(candidate_path, data_root):
                return atlas
        return None

    def _resolve_source_xml_path(self, xml_file: str, json_dir: Optional[str]) -> Optional[str]:
        """Return the absolute XML path for an animation source entry."""
        if not xml_file:
            return None
        override = self._lookup_legacy_sheet_override(xml_file)
        if override:
            return override
        if os.path.isabs(xml_file) and os.path.exists(xml_file):
            return xml_file
        relative_variants = self._resource_relative_variants(xml_file)
        candidates: List[str] = []
        for data_root in self._candidate_data_roots():
            for variant in relative_variants:
                candidates.append(os.path.join(data_root, *variant.split("/")))
        if json_dir:
            json_root = Path(json_dir)
            json_roots: List[Path] = [json_root]
            saw_data_dir = False
            for parent in json_root.parents:
                json_roots.append(parent)
                if parent.name.lower() == "data":
                    saw_data_dir = True
                    continue
                if saw_data_dir:
                    # Include the .app root so resource lookups like
                    # xml_resources/foo.xml resolve when JSONs sit in data/xml_bin.
                    break
            for root in json_roots:
                for variant in relative_variants:
                    candidates.append(
                        os.path.join(str(root), *variant.split("/"))
                    )
        candidates.append(os.path.join(str(self.project_root), xml_file))
        seen: Set[str] = set()
        for candidate in candidates:
            norm = os.path.normcase(os.path.normpath(candidate))
            if norm in seen:
                continue
            seen.add(norm)
            if os.path.exists(candidate):
                return candidate
        return None

    def _load_texture_atlases_for_sources(
        self,
        sources: List[Dict[str, Any]],
        *,
        json_dir: Optional[str],
        use_cache: bool
    ) -> List[TextureAtlas]:
        """Create TextureAtlas objects for a set of source descriptors."""
        atlases: List[TextureAtlas] = []
        if not sources or (not self.game_path and not self.downloads_path):
            return atlases
        dof_context = bool(self.dof_search_enabled)
        if not dof_context and json_dir and self.dof_path:
            dof_context = self._path_is_under(json_dir, self.dof_path)
        for source in sources:
            xml_file = source.get("src")
            xml_path = self._resolve_source_xml_path(xml_file, json_dir)
            if not xml_path:
                self.log_widget.log(f"XML file not found: {xml_file}", "ERROR")
                continue
            if not dof_context and self.dof_path:
                dof_context = self._path_is_under(xml_path, self.dof_path)
            norm = os.path.normcase(os.path.normpath(xml_path))
            atlas = self.costume_atlas_cache.get(norm) if use_cache else None
            created = False
            if not atlas:
                atlas = self._load_texture_atlas_document(xml_path)
                if not atlas:
                    self.log_widget.log(f"Failed to load texture atlas: {os.path.basename(xml_path)}", "ERROR")
                    continue
                atlas.source_name = source.get("src") or os.path.basename(xml_path)
                created = True
                if use_cache:
                    self.costume_atlas_cache[norm] = atlas
            if dof_context and not getattr(atlas, "pivot_mode", None):
                atlas.pivot_mode = "dof"
            atlas.force_unpremultiply = bool(self.legacy_animation_active)
            source_id_value = source.get("id")
            if source_id_value is not None:
                try:
                    atlas.source_id = int(source_id_value)
                except (TypeError, ValueError):
                    atlas.source_id = None
            else:
                atlas.source_id = None
            atlases.append(atlas)
            if not use_cache or created:
                self.log_widget.log(f"Loaded texture atlas: {os.path.basename(xml_path)}", "SUCCESS")
        return atlases

    def _rebuild_source_atlas_lookup(
        self,
        sources: List[Dict[str, Any]],
        atlases: List[TextureAtlas]
    ) -> None:
        """Map animation source ids/names to their loaded TextureAtlas objects."""
        mapping: Dict[Any, TextureAtlas] = {}
        for atlas in atlases:
            source_id = getattr(atlas, "source_id", None)
            if source_id is not None and source_id not in mapping:
                mapping[source_id] = atlas
            source_name = getattr(atlas, "source_name", None)
            if source_name:
                lower = source_name.lower()
                mapping.setdefault(lower, atlas)
        if len(atlases) == len(sources):
            for idx, source in enumerate(sources):
                key = source.get("id")
                if key is None:
                    key = idx
                if key not in mapping and idx < len(atlases):
                    mapping[key] = atlases[idx]
        self.source_atlas_lookup = mapping

    def _prepare_costume_attachments(
        self, attachments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Resolve attachment metadata so future renderer hooks can consume it."""
        prepared: List[Dict[str, Any]] = []
        if not attachments:
            return prepared
        for entry in attachments:
            target = entry.get("attach_to", "")
            resource = entry.get("resource", "")
            animation = entry.get("animation", "")
            raw_value = entry.get("time_offset", entry.get("time_scale", 0.0))
            time_offset, tempo_multiplier, loop_flag = self._extract_attachment_metadata(entry, raw_value)
            resolved = self._resolve_attachment_resource(resource)
            prepared.append(
                {
                    "attach_to": target,
                    "resource": resource,
                    "animation": animation,
                    "time_offset": time_offset,
                    # Keep legacy key for downstream consumers that still expect it.
                    "time_scale": time_offset,
                    "tempo_multiplier": tempo_multiplier,
                    "loop": loop_flag,
                    "root_layer": entry.get("root_layer") or entry.get("root_socket"),
                    "raw_time_value": entry.get("raw_time_value", raw_value),
                    **resolved,
                }
            )
        return prepared

    def _extract_attachment_metadata(
        self,
        entry: Dict[str, Any],
        default_time: Optional[float]
    ) -> Tuple[float, float, bool]:
        """Return sanitized (time_offset, tempo_multiplier, loop_flag)."""
        raw_value = entry.get("raw_time_value", default_time)
        try:
            time_offset = float(default_time)
        except (TypeError, ValueError):
            time_offset = 0.0
        tempo_multiplier = entry.get("tempo_multiplier")
        loop_flag = entry.get("loop")
        if tempo_multiplier is None and isinstance(raw_value, (int, float)):
            if 0.0 < abs(raw_value) <= 0.0025:
                tempo_multiplier = max(0.1, abs(raw_value) * 100.0)
                time_offset = 0.0
        if tempo_multiplier is None:
            tempo_multiplier = 1.0
        try:
            tempo_multiplier = float(tempo_multiplier)
        except (TypeError, ValueError):
            tempo_multiplier = 1.0
        tempo_multiplier = max(0.1, tempo_multiplier)
        if loop_flag is None:
            loop_flag = True
        else:
            loop_flag = bool(loop_flag)
        return time_offset, tempo_multiplier, loop_flag

    def _resolve_attachment_resource(self, resource: str) -> Dict[str, Optional[str]]:
        """Resolve candidate paths for an attachment resource."""
        if not resource:
            return {"bin_path": None, "json_path": None}
        raw = Path(resource)
        candidates: List[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        if self.downloads_path:
            downloads_root = Path(self.downloads_path)
            candidates.append(downloads_root / resource)
            candidates.append(downloads_root / "data" / resource)
            candidates.append(downloads_root / "xml_bin" / raw.name)
        if self.game_path:
            game_root = Path(self.game_path)
            candidates.append(game_root / resource)
            candidates.append(game_root / "data" / resource)
            candidates.append(game_root / "data" / "xml_bin" / raw.name)
        candidates.append(Path(str(self.project_root)) / resource)
        candidates.append(Path(str(self.project_root)) / "Resources" / resource)

        bin_path: Optional[Path] = None
        json_path: Optional[Path] = None
        for cand in candidates:
            if cand.exists():
                if cand.suffix.lower() == ".json":
                    json_path = cand
                else:
                    bin_path = cand
                break

        if bin_path and not json_path:
            converted = bin_path.with_suffix(".json")
            if converted.exists():
                json_path = converted

        if not bin_path and resource.lower().endswith(".bin"):
            if raw.exists():
                bin_path = raw

        return {
            "bin_path": str(bin_path) if bin_path else None,
            "json_path": str(json_path) if json_path else None,
        }

    def _build_animation_struct(
        self,
        anim_dict: Dict[str, Any],
        blend_version: int,
        source_path: Optional[str] = None,
        resource_dict: Optional[Dict[str, Any]] = None
    ) -> AnimationData:
        """Convert a raw animation dictionary into AnimationData."""

        def _parse_color_tuple(raw_value: Any) -> Optional[Tuple[float, float, float, float]]:
            if isinstance(raw_value, (list, tuple)) and len(raw_value) == 4:
                try:
                    return tuple(float(component) for component in raw_value)
                except (TypeError, ValueError):
                    return None
            return None

        def _coerce_render_tags(raw_value: Any) -> Set[str]:
            tags: Set[str] = set()
            if isinstance(raw_value, (list, tuple, set)):
                for entry in raw_value:
                    if isinstance(entry, str) and entry.strip():
                        tags.add(entry.strip())
            elif isinstance(raw_value, str) and raw_value.strip():
                tags.add(raw_value.strip())
            return tags

        legacy_payload = False
        if resource_dict is not None:
            legacy_payload = self._is_legacy_payload(resource_dict, source_path)
        else:
            legacy_payload = bool(self.legacy_animation_active)

        def _derive_layer_anchor(layer_dict: Dict[str, Any], frames: List[Dict[str, Any]]) -> Tuple[float, float]:
            raw_x = layer_dict.get("anchor_x")
            raw_y = layer_dict.get("anchor_y")
            if isinstance(raw_x, (int, float)) and isinstance(raw_y, (int, float)):
                return float(raw_x), float(raw_y)
            for frame in frames:
                anchor = frame.get("anchor")
                if not isinstance(anchor, dict):
                    continue
                ax = anchor.get("x")
                ay = anchor.get("y")
                if isinstance(ax, (int, float)) and isinstance(ay, (int, float)):
                    return float(ax), float(ay)
            return float(raw_x or 0.0), float(raw_y or 0.0)

        def _coerce_anchor_value(raw_value: Any) -> Optional[Tuple[float, float]]:
            if isinstance(raw_value, (list, tuple)) and len(raw_value) >= 2:
                try:
                    return float(raw_value[0]), float(raw_value[1])
                except (TypeError, ValueError):
                    return None
            if isinstance(raw_value, dict):
                ax = raw_value.get("x")
                ay = raw_value.get("y")
                if isinstance(ax, (int, float)) and isinstance(ay, (int, float)):
                    return float(ax), float(ay)
            return None

        def _expand_anchor_keys(raw_key: str) -> List[str]:
            key = raw_key.strip()
            if not key:
                return []
            candidates = [key]
            base_name = Path(key).name
            if base_name and base_name not in candidates:
                candidates.append(base_name)
            stem = Path(base_name).stem
            if stem and stem not in candidates:
                candidates.append(stem)
            return candidates

        layers: List[LayerData] = []
        source_token = self._token_from_path(source_path) or self._current_monster_token()
        for layer_data in anim_dict.get('layers', []):
            keyframes: List[KeyframeData] = []
            layer_has_depth = False
            frame_entries = layer_data.get('frames', [])
            if frame_entries:
                indexed = enumerate(frame_entries)
                sorted_frames = [
                    frame for _, frame in sorted(
                        indexed,
                        key=lambda pair: (pair[1].get('time', 0.0), pair[0])
                    )
                ]
            else:
                sorted_frames = frame_entries
            for frame_data in sorted_frames:
                sprite_info = frame_data.get('sprite', {}) or {}
                sprite_name = sprite_info.get('string', '')
                sprite_immediate = sprite_info.get('immediate', 0)
                if legacy_payload and sprite_name and sprite_immediate <= 0:
                    sprite_immediate = 1
                depth_value = 0.0
                depth_immediate = -1
                depth_entry = frame_data.get('depth')
                if isinstance(depth_entry, dict):
                    if 'value' in depth_entry:
                        depth_value = depth_entry.get('value', 0.0)
                        depth_immediate = depth_entry.get('immediate', 0)
                elif isinstance(depth_entry, (int, float)):
                    depth_value = depth_entry
                    depth_immediate = 0
                if depth_immediate != -1:
                    layer_has_depth = True
                keyframes.append(
                    KeyframeData(
                        time=frame_data.get('time', 0.0),
                        pos_x=frame_data.get('pos', {}).get('x', 0),
                        pos_y=frame_data.get('pos', {}).get('y', 0),
                        depth=depth_value,
                        scale_x=frame_data.get('scale', {}).get('x', 100),
                        scale_y=frame_data.get('scale', {}).get('y', 100),
                        rotation=frame_data.get('rotation', {}).get('value', 0),
                        opacity=frame_data.get('opacity', {}).get('value', 100),
                        sprite_name=sprite_name,
                        r=frame_data.get('rgb', {}).get('red', 255),
                        g=frame_data.get('rgb', {}).get('green', 255),
                        b=frame_data.get('rgb', {}).get('blue', 255),
                        a=frame_data.get('rgb', {}).get('alpha', 255),
                        immediate_pos=frame_data.get('pos', {}).get('immediate', 0),
                        immediate_depth=depth_immediate,
                        immediate_scale=frame_data.get('scale', {}).get('immediate', 0),
                        immediate_rotation=frame_data.get('rotation', {}).get('immediate', 0),
                        immediate_opacity=frame_data.get('opacity', {}).get('immediate', 0),
                        immediate_sprite=sprite_immediate,
                        immediate_rgb=frame_data.get('rgb', {}).get('immediate', -1)
                    )
                )
            blend_value = self._normalize_blend_value(layer_data.get('blend', 0), blend_version)
            if self._should_promote_light_layer_blend(
                layer_data.get('name', ''),
                layer_data.get('shader'),
                blend_value,
            ):
                blend_value = BlendMode.ADDITIVE
            if self._should_force_standard_blend(
                source_token,
                layer_data.get('name', ''),
                blend_value
            ):
                blend_value = BlendMode.STANDARD
            color_tint = _parse_color_tuple(layer_data.get('color_tint'))
            color_tint_hdr = _parse_color_tuple(layer_data.get('color_tint_hdr'))
            gradient_data = layer_data.get('color_gradient')
            animator_data = layer_data.get('color_animator')
            metadata = layer_data.get('color_metadata')
            render_tags = _coerce_render_tags(layer_data.get('render_tags'))
            mask_role = layer_data.get('mask_role')
            mask_key = layer_data.get('mask_key')
            raw_anchor_map = layer_data.get('sprite_anchor_map')
            sprite_anchor_map = None
            if isinstance(raw_anchor_map, dict):
                sprite_anchor_map = {}
                for key, value in raw_anchor_map.items():
                    if not isinstance(key, str):
                        continue
                    anchor = _coerce_anchor_value(value)
                    if anchor is None:
                        continue
                    for candidate in _expand_anchor_keys(key):
                        sprite_anchor_map.setdefault(candidate, anchor)
            elif isinstance(raw_anchor_map, list):
                sprite_anchor_map = {}
                for entry in raw_anchor_map:
                    if not isinstance(entry, dict):
                        continue
                    key = entry.get("sprite") or entry.get("name") or entry.get("key")
                    if not isinstance(key, str):
                        continue
                    anchor = _coerce_anchor_value(entry.get("anchor", entry))
                    if anchor is None:
                        continue
                    for candidate in _expand_anchor_keys(key):
                        sprite_anchor_map.setdefault(candidate, anchor)
            if sprite_anchor_map == {}:
                sprite_anchor_map = None
            layers.append(
                LayerData(
                    name=layer_data.get('name', ''),
                    layer_id=layer_data.get('id', 0),
            parent_id=self._normalize_parent_id(layer_data.get('parent', -1)),
                    anchor_x=layer_data.get('anchor_x', 0.0),
                    anchor_y=layer_data.get('anchor_y', 0.0),
                    blend_mode=blend_value,
                    keyframes=keyframes,
                    visible=layer_data.get('visible', True),
                    shader_name=layer_data.get('shader'),
                    color_tint=color_tint,
                    color_tint_hdr=color_tint_hdr,
                    color_gradient=copy.deepcopy(gradient_data) if isinstance(gradient_data, dict) else None,
            color_animator=copy.deepcopy(animator_data) if isinstance(animator_data, dict) else None,
            color_metadata=copy.deepcopy(metadata) if isinstance(metadata, dict) else None,
            render_tags=render_tags,
            mask_role=str(mask_role) if isinstance(mask_role, str) and mask_role else None,
            mask_key=str(mask_key) if isinstance(mask_key, str) and mask_key else None,
            sprite_anchor_map=sprite_anchor_map,
            has_depth=layer_has_depth,
                )
            )
            anchor_x, anchor_y = _derive_layer_anchor(layer_data, sorted_frames)
            layers[-1].anchor_x = anchor_x
            layers[-1].anchor_y = anchor_y
        animation = AnimationData(
            name=anim_dict.get('name', ''),
            width=anim_dict.get('width', 0),
            height=anim_dict.get('height', 0),
            loop_offset=anim_dict.get('loop_offset', 0.0),
            centered=anim_dict.get('centered', 0),
            layers=layers
        )
        dof_meta = anim_dict.get("dof_meta") or {}
        anim_flip = anim_dict.get("dof_anim_flip_y")
        if isinstance(dof_meta, dict):
            anim_flip = dof_meta.get("anim_flip_y", anim_flip)
        if isinstance(anim_flip, bool):
            animation.dof_anim_flip_y = anim_flip
        self._apply_monster_layer_overrides(layers, source_token, resource_dict, source_path)
        return animation

    def _apply_monster_layer_overrides(
        self,
        layers: List[LayerData],
        source_token: Optional[str],
        resource_dict: Optional[Dict[str, Any]],
        source_path: Optional[str]
    ) -> None:
        """Inject data-driven layer tweaks that the stock JSON export omits."""
        if not layers:
            return
        self._apply_auto_shadow_cutout_masks(layers)

    def _apply_auto_shadow_cutout_masks(
        self,
        layers: List[LayerData],
        *,
        log_changes: bool = True,
    ) -> None:
        """
        Infer missing cutout masks from layer relationships.

        Observed pattern in game assets:
        - a child layer uses additive blend (blend=2)
        - its parent is a shadow layer
        - no explicit mask metadata is present

        In that case, treat the additive child as stencil source and its shadow parent as
        stencil consumer, then enforce source-before-consumer render order.
        """
        if any(getattr(layer, "mask_role", None) for layer in layers):
            # Respect explicit metadata if present.
            return

        layer_by_id = {layer.layer_id: layer for layer in layers}
        pairs: List[Tuple[LayerData, LayerData]] = []
        for layer in layers:
            if layer.blend_mode != BlendMode.ADDITIVE:
                continue
            parent = layer_by_id.get(layer.parent_id)
            if parent is None:
                continue
            parent_name = (parent.name or "").strip().lower()
            if "shadow" not in parent_name:
                continue
            pairs.append((layer, parent))

        if not pairs:
            return

        for source_layer, shadow_layer in pairs:
            mask_key = f"auto_shadow_cutout_{source_layer.layer_id}_{shadow_layer.layer_id}"
            source_layer.mask_role = "mask_source"
            source_layer.mask_key = mask_key
            shadow_layer.mask_role = "mask_consumer"
            shadow_layer.mask_key = mask_key

            source_idx = next((idx for idx, item in enumerate(layers) if item.layer_id == source_layer.layer_id), -1)
            shadow_idx = next((idx for idx, item in enumerate(layers) if item.layer_id == shadow_layer.layer_id), -1)

            # Runtime renderer iterates reversed(animation.layers). Ensure source appears
            # before consumer in that reversed order by keeping source at a higher index.
            if source_idx >= 0 and shadow_idx >= 0 and source_idx < shadow_idx:
                layers[source_idx], layers[shadow_idx] = layers[shadow_idx], layers[source_idx]

            if log_changes:
                self.log_widget.log(
                    f"Auto-applied shadow cutout mask: source '{source_layer.name}' "
                    f"(id {source_layer.layer_id}) -> consumer '{shadow_layer.name}' "
                    f"(id {shadow_layer.layer_id}).",
                    "INFO",
                )

    def _load_rev6_animation_module(self):
        """Dynamically import the rev6-2-json converter so we can parse BIN files."""
        if self._rev6_anim_module is not None:
            return self._rev6_anim_module
        script_path = self.project_root / "Resources" / "bin2json" / "rev6-2-json.py"
        if not script_path.exists():
            self.log_widget.log("rev6-2-json script missing; attachment animations cannot be converted.", "ERROR")
            return None
        spec = importlib.util.spec_from_file_location("msm_rev6_anim", script_path)
        if not spec or not spec.loader:
            self.log_widget.log("Failed to load rev6-2-json script.", "ERROR")
            return None
        script_dir = str(script_path.parent)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
        self._rev6_anim_module = module
        return module

    def _load_animation_resource_dict(
        self,
        json_path: Optional[str],
        bin_path: Optional[str],
        *,
        progress_callback=None,
    ) -> Optional[Dict[str, Any]]:
        """Return a parsed animation dictionary from either JSON or BIN paths."""
        source_path = json_path or bin_path
        if not source_path:
            return None
        norm = os.path.normcase(os.path.normpath(source_path))
        cached = self.attachment_animation_cache.get(norm)
        if cached:
            return cached
        data: Optional[Dict[str, Any]] = None
        try:
            if json_path and os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            elif bin_path and os.path.exists(bin_path):
                module = self._load_rev6_animation_module()
                if not module:
                    return None
                prev_cb = None
                if progress_callback is not None and hasattr(module, "BinFile"):
                    prev_cb = getattr(module.BinFile, "progress_callback", None)
                    module.BinFile.progress_callback = progress_callback
                try:
                    bin_anim = module.BinAnim.from_file(str(bin_path))
                    data = bin_anim.to_dict()
                except Exception as exc:
                    token_path = self._create_legacy_token_dump(bin_path)
                    hint = (
                        f" Legacy token dump: {token_path}"
                        if token_path
                        else " Legacy tokenizer unavailable."
                    )
                    self.log_widget.log(
                        f"Failed to parse BIN {os.path.basename(bin_path)} ({exc}).{hint}",
                        "ERROR",
                    )
                    return None
                finally:
                    if progress_callback is not None and hasattr(module, "BinFile"):
                        module.BinFile.progress_callback = prev_cb
        except Exception as exc:
            self.log_widget.log(f"Failed to load attachment animation from {source_path}: {exc}", "ERROR")
            return None
        if data is None:
            self.log_widget.log(f"Attachment animation source not found: {source_path}", "WARNING")
            return None
        self.attachment_animation_cache[norm] = data
        return data

    def _build_island_tile_batches(
        self,
        anim_dict: Dict[str, Any],
        animation: AnimationData,
    ) -> Tuple[List[TileBatch], Optional[TerrainComposite]]:
        """Construct tile overlays for island_* animations."""
        if not self.game_path or not self.current_json_path:
            return [], None
        slug = Path(self.current_json_path).stem.lower()
        if not slug.startswith("island"):
            return [], None
        tileset_path = self._locate_island_support_file(slug, "tileset_", ".bin")
        grid_path = self._locate_island_support_file(slug, "", "_grid.bin")
        if not tileset_path or not grid_path:
            return [], None
        try:
            tileset = self._tileset_cache.get(tileset_path)
            if not tileset:
                tileset = parse_tileset_file(tileset_path)
                self._tileset_cache[tileset_path] = tileset
            grid_data = self._grid_cache.get(grid_path)
            if not grid_data:
                grid_data = parse_tile_grid_file(grid_path)
                self._grid_cache[grid_path] = grid_data
        except Exception as exc:
            self.log_widget.log(
                f"Failed to parse island terrain files: {exc}",
                "WARNING",
            )
            return [], None
        atlas_manifest = self._resolve_tileset_atlas_path(tileset.atlas_path)
        if not atlas_manifest:
            self.log_widget.log(
                f"Tileset atlas '{tileset.atlas_path}' not found for {slug}",
                "WARNING",
            )
            return [], None
        atlas = self._binary_atlas_cache.get(atlas_manifest)
        if not atlas:
            atlas = TextureAtlas()
            data_root = os.path.join(self.game_path, "data")
            if not atlas.load_from_binary_manifest(atlas_manifest, data_root):
                self.log_widget.log(
                    f"Failed to load tile atlas {os.path.basename(atlas_manifest)}",
                    "WARNING",
                )
                return [], None
            self._binary_atlas_cache[atlas_manifest] = atlas

        header = grid_data.header
        tile_width = float(header.tile_width)
        tile_height = float(header.tile_height)
        tile_half_w = tile_width * 0.5
        tile_half_h = tile_height * 0.5
        grid_rows = float(header.rows)

        raw_samples: List[Tuple[str, float, float, float, int, int, int, int, int]] = []
        missing_sprites: Set[str] = set()
        for entry in grid_data.entries:
            sprite_name = entry.sprite_name
            if sprite_name not in atlas.sprites:
                missing_sprites.add(sprite_name)
                continue
            offset_x, offset_y = tileset.sprite_offsets.get(sprite_name, (0, 0))
            grid_col = float(entry.column)
            grid_row = float(entry.row)
            # Match game::Grid::Grid placement math from FunctionsAllREV6:
            # x = (col + row + 1) * (tile_w * 0.5)
            # y = (rows + row - col) * (tile_h * 0.5)
            iso_x = (grid_col + grid_row + 1.0) * tile_half_w
            iso_y = (grid_rows + grid_row - grid_col) * tile_half_h
            depth = entry.y_value if entry.y_value != 0.0 else iso_y
            raw_samples.append(
                (
                    sprite_name,
                    iso_x,
                    iso_y,
                    depth,
                    entry.row,
                    entry.column,
                    int(entry.flags),
                    int(offset_x),
                    int(offset_y),
                )
            )

        if not raw_samples:
            return [], None
        terrain_scale = self._resolve_island_terrain_scale(animation, header)
        # Keep source order from *_grid.bin to mirror game-side terrain construction.
        grid_mirrored = "mirror" in os.path.basename(grid_path).lower() or "_mirror" in slug
        self._terrain_grid_mirrored = grid_mirrored
        raw_samples = self._align_island_tile_samples(
            raw_samples, atlas, animation, header, grid_mirrored, terrain_scale
        )
        self.gl_widget.set_tile_grid_mirrored(bool(grid_mirrored))
        self._log_island_terrain_alignment(
            slug,
            grid_path,
            grid_data.header,
            animation,
            terrain_scale,
            raw_samples,
        )

        pos_scale = self.gl_widget.renderer.position_scale
        terrain_composite = self._build_island_terrain_composite(atlas, raw_samples, terrain_scale)
        instances: List[TileInstance] = []
        for sprite_name, iso_x, iso_y, _depth, _row, _col, _flags, _off_x, _off_y in raw_samples:
            # Tile-grid coordinates are already authored in world space.
            # Additional camera/centering offsets push terrain far off target.
            screen_x = (iso_x + float(_off_x)) * pos_scale
            screen_y = (iso_y + float(_off_y)) * pos_scale
            instances.append(
                TileInstance(
                    sprite_name=sprite_name,
                    center_x=screen_x,
                    center_y=screen_y,
                    scale=pos_scale * terrain_scale,  # Base 0.5 scale + user scale
                    flag=int(_flags),
                    depth=float(_depth),
                    row=int(_row),
                    column=int(_col),
                )
            )

        if not instances:
            return [], terrain_composite
        if missing_sprites:
            preview = ", ".join(sorted(missing_sprites)[:4])
            extra = "" if len(missing_sprites) <= 4 else "..."
            self.log_widget.log(
                f"Tileset missing {len(missing_sprites)} sprite(s): {preview}{extra}",
                "WARNING",
            )

        self.log_widget.log(
            f"Loaded {len(instances)} terrain tiles from {os.path.basename(grid_path)}",
            "INFO",
        )
        return [TileBatch(atlas=atlas, instances=instances)], terrain_composite

    def _align_island_tile_samples(
        self,
        raw_samples: List[Tuple[str, float, float, float, int, int, int, int, int]],
        atlas: TextureAtlas,
        animation: AnimationData,
        header: TileGridHeader,
        grid_mirrored: bool = False,
        terrain_scale: float = 1.0,
    ) -> List[Tuple[str, float, float, float, int, int, int, int, int]]:
        """Align tile samples to the island animation coordinate space."""
        if not raw_samples:
            return raw_samples
        grid_center_x = (float(header.origin_x) + float(header.bounds_width) * 0.5) * terrain_scale
        grid_center_y = (float(header.origin_y) + float(header.bounds_height) * 0.5) * terrain_scale
        if header.bounds_width <= 0 or header.bounds_height <= 0:
            min_x = float("inf")
            min_y = float("inf")
            max_x = float("-inf")
            max_y = float("-inf")
            for sprite_name, iso_x, iso_y, _depth, _row, _col, _flags, off_x, off_y in raw_samples:
                sprite = atlas.get_sprite(sprite_name)
                if not sprite:
                    continue
                cx = (iso_x + float(off_x)) * terrain_scale
                cy = (iso_y + float(off_y)) * terrain_scale
                x0 = cx - (float(sprite.w) * 0.5 * terrain_scale)
                x1 = cx + (float(sprite.w) * 0.5 * terrain_scale)
                y0 = cy - (float(sprite.h) * 0.5 * terrain_scale)
                y1 = cy + (float(sprite.h) * 0.5 * terrain_scale)
                min_x = min(min_x, x0)
                min_y = min(min_y, y0)
                max_x = max(max_x, x1)
                max_y = max(max_y, y1)
            if min_x == float("inf"):
                return raw_samples
            grid_center_x = (min_x + max_x) * 0.5
            grid_center_y = (min_y + max_y) * 0.5
        if animation.centered:
            target_center_x = float(animation.width) * 0.5
            target_center_y = float(animation.height) * 0.5
        else:
            target_center_x = 0.0
            target_center_y = 0.0
        shift_x = target_center_x - grid_center_x
        shift_y = target_center_y - grid_center_y
        aligned: List[Tuple[str, float, float, float, int, int, int, int, int]] = []
        for sprite_name, iso_x, iso_y, depth, row, col, flags, off_x, off_y in raw_samples:
            cx = (iso_x + float(off_x)) * terrain_scale
            cy = (iso_y + float(off_y)) * terrain_scale
            if grid_mirrored:
                # Mirror horizontally around the grid center for mirror-grid layouts.
                cx = (2.0 * grid_center_x) - cx
            cx += shift_x
            cy += shift_y
            aligned.append(
                (
                    sprite_name,
                    cx,
                    cy,
                    depth * terrain_scale,
                    row,
                    col,
                    flags,
                    0,
                    0,
                )
            )
        return aligned

    def _resolve_island_terrain_scale(
        self,
        animation: AnimationData,
        header: TileGridHeader,
    ) -> float:
        """Derive terrain scale from animation dimensions and grid bounds."""
        default_scale = 0.5
        if not animation or not animation.centered:
            return default_scale
        anim_w = float(animation.width or 0.0)
        anim_h = float(animation.height or 0.0)
        bounds_w = float(header.bounds_width or 0.0)
        bounds_h = float(header.bounds_height or 0.0)
        if anim_w <= 0.0 or anim_h <= 0.0 or bounds_w <= 0.0 or bounds_h <= 0.0:
            return default_scale
        scale_x = anim_w / bounds_w
        scale_y = anim_h / bounds_h
        if not (math.isfinite(scale_x) and math.isfinite(scale_y)):
            return default_scale
        if scale_x <= 0.0 or scale_y <= 0.0:
            return default_scale
        if 0.1 <= scale_x <= 2.0 and 0.1 <= scale_y <= 2.0:
            if abs(scale_x - scale_y) <= 0.05:
                return (scale_x + scale_y) * 0.5
            return scale_x if abs(scale_x - 1.0) < abs(scale_y - 1.0) else scale_y
        return default_scale

    def _build_island_terrain_composite(
        self,
        atlas: TextureAtlas,
        raw_samples: List[Tuple[str, float, float, float, int, int, int, int, int]],
        terrain_scale: float,
    ) -> Optional[TerrainComposite]:
        """Stitch terrain tiles into a single RGBA texture and world-space quad."""
        if not raw_samples:
            return None
        try:
            atlas_image = atlas._load_texture_image(atlas.image_path).convert("RGBA")
            mask_cache = getattr(self, "_tile_diamond_mask_cache", None)
            if mask_cache is None:
                mask_cache = {}
                self._tile_diamond_mask_cache = mask_cache
            tile_positions: List[Tuple[str, int, float, float, float, float]] = []
            min_x = float("inf")
            min_y = float("inf")
            max_x = float("-inf")
            max_y = float("-inf")
            flag0 = 0
            flag1 = 0
            for sprite_name, iso_x, iso_y, _depth, _row, _col, flags, off_x, off_y in raw_samples:
                sprite = atlas.get_sprite(sprite_name)
                if not sprite:
                    continue
                cx = (iso_x + float(off_x)) * terrain_scale
                cy = (iso_y + float(off_y)) * terrain_scale
                width = float(sprite.w) * terrain_scale
                height = float(sprite.h) * terrain_scale
                x0 = cx - (width * 0.5)
                y0 = cy - (height * 0.5)
                x1 = x0 + width
                y1 = y0 + height
                tile_positions.append((sprite_name, int(flags), x0, y0, width, height))
                min_x = min(min_x, x0)
                min_y = min(min_y, y0)
                max_x = max(max_x, x1)
                max_y = max(max_y, y1)
                if int(flags) == 0:
                    flag0 += 1
                else:
                    flag1 += 1
            if not tile_positions:
                return None
            comp_w = int(math.ceil(max_x - min_x))
            comp_h = int(math.ceil(max_y - min_y))
            if comp_w <= 0 or comp_h <= 0:
                return None
            stitched = Image.new("RGBA", (comp_w, comp_h), (0, 0, 0, 0))
            for sprite_name, flags, x0, y0, width, height in tile_positions:
                sprite = atlas.get_sprite(sprite_name)
                if not sprite:
                    continue
                tile = atlas_image.crop((sprite.x, sprite.y, sprite.x + sprite.w, sprite.y + sprite.h))
                if int(flags) != 0:
                    tile = self._apply_tile_flag_transform(tile)
                mask_key = (tile.width, tile.height)
                mask = mask_cache.get(mask_key)
                if mask is None:
                    mask = Image.new("L", mask_key, 0)
                    draw = ImageDraw.Draw(mask)
                    w, h = mask_key
                    draw.polygon(
                        [
                            (w * 0.5, 0.0),
                            (w - 1.0, h * 0.5),
                            (w * 0.5, h - 1.0),
                            (0.0, h * 0.5),
                        ],
                        fill=255,
                    )
                    mask_cache[mask_key] = mask
                if tile.mode != "RGBA":
                    tile = tile.convert("RGBA")
                if tile.size == mask.size:
                    alpha = tile.getchannel("A")
                    combined = ImageChops.multiply(alpha, mask)
                    tile.putalpha(combined)
                dest_x = int(round(x0 - min_x))
                dest_y = int(round(y0 - min_y))
                stitched.alpha_composite(tile, (dest_x, dest_y))
            return TerrainComposite(
                width=comp_w,
                height=comp_h,
                origin_x=min_x,
                origin_y=min_y,
                rgba_bytes=stitched.tobytes("raw", "RGBA"),
                tile_count=len(tile_positions),
                flag0_count=flag0,
                flag1_count=flag1,
            )
        except Exception as exc:
            self.log_widget.log(f"Failed to build stitched terrain texture: {exc}", "WARNING")
            return None

    def _apply_tile_flag_transform(self, tile: Image.Image) -> Image.Image:
        """Apply configured transform to terrain tiles with non-zero flags."""
        mode = str(getattr(self.gl_widget, "tile_flag1_transform_mode", "none") or "none").lower()
        if mode == "hflip":
            return tile.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if mode == "vflip":
            return tile.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if mode == "hvflip":
            return tile.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        return tile

    def _log_island_terrain_alignment(
        self,
        slug: str,
        grid_path: str,
        header: TileGridHeader,
        animation: AnimationData,
        terrain_scale: float,
        samples: List[Tuple[str, float, float, float, int, int, int, int, int]],
    ) -> None:
        """Emit a one-shot alignment summary to help reconcile per-island offsets."""
        if not samples:
            return
        signature = f"{slug}|{os.path.basename(grid_path)}|{terrain_scale}"
        if signature == self._last_terrain_alignment_signature:
            return
        self._last_terrain_alignment_signature = signature

        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")
        for _sprite, cx, cy, _depth, *_rest in samples:
            min_x = min(min_x, cx)
            min_y = min(min_y, cy)
            max_x = max(max_x, cx)
            max_y = max(max_y, cy)
        center_x = (min_x + max_x) * 0.5
        center_y = (min_y + max_y) * 0.5
        header_center_x = (float(header.origin_x) + float(header.bounds_width) * 0.5) * terrain_scale
        header_center_y = (float(header.origin_y) + float(header.bounds_height) * 0.5) * terrain_scale
        if animation.centered:
            target_x = float(animation.width) * 0.5
            target_y = float(animation.height) * 0.5
        else:
            target_x = 0.0
            target_y = 0.0
        delta_x = target_x - center_x
        delta_y = target_y - center_y
        grid_delta_x = target_x - header_center_x
        grid_delta_y = target_y - header_center_y
        self.log_widget.log(
            "Terrain alignment: "
            f"grid={os.path.basename(grid_path)}, "
            f"rows={header.rows}, cols={header.columns}, "
            f"tile={header.tile_width}x{header.tile_height}, "
            f"origin=({header.origin_x},{header.origin_y}), "
            f"scale={terrain_scale:.3f}, "
            f"bounds=({min_x:.1f},{min_y:.1f})-({max_x:.1f},{max_y:.1f}), "
            f"sample_center=({center_x:.1f},{center_y:.1f}), "
            f"grid_center=({header_center_x:.1f},{header_center_y:.1f}), "
            f"target=({target_x:.1f},{target_y:.1f}), "
            f"sample_delta=({delta_x:.1f},{delta_y:.1f}), "
            f"grid_delta=({grid_delta_x:.1f},{grid_delta_y:.1f})",
            "INFO",
        )

    def _candidate_island_names(self, slug: str) -> List[str]:
        parts = [part for part in slug.lower().split("_") if part]
        candidates: List[str] = []
        for end in range(len(parts), 0, -1):
            candidate = "_".join(parts[:end])
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _locate_island_support_file(self, slug: str, prefix: str, suffix: str) -> Optional[str]:
        if not self.xml_bin_file_map:
            return None
        for candidate in self._candidate_island_names(slug):
            key = f"{prefix}{candidate}{suffix}".lower()
            path = self.xml_bin_file_map.get(key)
            if path:
                return path
        return None

    def _resolve_tileset_atlas_path(self, relative_path: str) -> Optional[str]:
        if not relative_path:
            return None
        cleaned = relative_path.replace("\\", "/").lstrip("./")
        if os.path.isabs(cleaned) and os.path.exists(cleaned):
            return cleaned
        candidates: List[str] = []
        xml_bin_roots = self._all_xml_bin_roots()
        search_roots = set(self._candidate_data_roots())
        if self.game_path:
            search_roots.add(self.game_path)
        for root in xml_bin_roots:
            search_roots.add(root)
        trimmed = cleaned
        if trimmed.startswith("data/"):
            trimmed = trimmed[5:]
        if trimmed.startswith("xml_bin/"):
            rel = trimmed[8:]
            for root in xml_bin_roots:
                candidates.append(os.path.join(root, rel))
        for root in search_roots:
            candidates.append(os.path.join(root, trimmed))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def _create_legacy_token_dump(self, bin_path: str) -> Optional[str]:
        """Invoke the legacy tokenizer so engineers can inspect unparsed BIN files."""
        if not self.legacy_tokenizer_path:
            self.log_widget.log(
                "Cannot dump legacy BIN tokens because the tokenizer script is missing.",
                "WARNING",
            )
            return None

        bin_file = Path(bin_path)
        output = bin_file.with_suffix(bin_file.suffix + ".tokens.json")
        try:
            if output.exists() and output.stat().st_mtime >= bin_file.stat().st_mtime:
                self.log_widget.log(
                    f"Legacy token dump already exists: {output}",
                    "INFO",
                )
                return str(output)
        except OSError:
            pass

        cmd = self._build_python_command(self.legacy_tokenizer_path) + [
            str(bin_file),
            "-o",
            str(output),
        ]
        try:
            result = self._run_converter_command(cmd, os.path.dirname(self.legacy_tokenizer_path))
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Legacy tokenizer failed.")
            if result.stdout and result.stdout.strip():
                self.log_widget.log(result.stdout.strip(), "INFO")
            self.log_widget.log(
                f"Created legacy token dump: {output}",
                "SUCCESS",
            )
            return str(output)
        except Exception as exc:
            message = str(exc).strip()
            self.log_widget.log(
                f"Legacy tokenizer failed for {bin_file.name}: {message}",
                "ERROR",
            )
        return None

    def _build_attachment_payloads(
        self,
        attachments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert attachment metadata into payloads for the renderer."""
        payloads: List[Dict[str, Any]] = []
        if not attachments:
            return payloads
        for entry in attachments:
            anim_dict = self._load_animation_resource_dict(entry.get("json_path"), entry.get("bin_path"))
            if not anim_dict:
                continue
            anim_name = entry.get("animation") or ""
            target_anim = None
            for candidate in anim_dict.get("anims", []):
                if not anim_name or candidate.get("name") == anim_name:
                    target_anim = candidate
                    break
            if target_anim is None:
                self.log_widget.log(
                    f"Attachment animation '{anim_name}' not found in {entry.get('resource')}",
                    "WARNING"
                )
                continue
            blend_version = anim_dict.get("blend_version", self.current_blend_version or 1)
            animation_data = self._build_animation_struct(
                target_anim,
                blend_version,
                entry.get("json_path") or entry.get("bin_path"),
                resource_dict=anim_dict
            )
            json_dir = os.path.dirname(entry.get("json_path")) if entry.get("json_path") else None
            atlases = self._load_texture_atlases_for_sources(
                anim_dict.get("sources", []),
                json_dir=json_dir,
                use_cache=True
            )
            if not atlases:
                self.log_widget.log(
                    f"Attachment '{anim_name or entry.get('attach_to')}' has no texture atlases; skipping.",
                    "WARNING"
                )
                continue
            raw_offset = entry.get("time_offset", entry.get("time_scale", 0.0))
            try:
                offset_value = float(raw_offset)
            except (TypeError, ValueError):
                offset_value = 0.0
            if not math.isfinite(offset_value):
                offset_value = 0.0
            tempo_multiplier = entry.get("tempo_multiplier", 1.0)
            try:
                tempo_multiplier = float(tempo_multiplier)
            except (TypeError, ValueError):
                tempo_multiplier = 1.0
            tempo_multiplier = max(0.1, tempo_multiplier)
            loop_flag = bool(entry.get("loop", True))
            root_layer_name = self._determine_attachment_root(entry.get("root_layer"), animation_data)
            payloads.append(
                {
                    "name": anim_name or entry.get("attach_to") or "attachment",
                    "target_layer": entry.get("attach_to", ""),
                    "target_layer_id": None,
                    "animation": animation_data,
                    "atlases": atlases,
                    "time_offset": offset_value,
                    "time_scale": offset_value,
                    "tempo_multiplier": tempo_multiplier,
                    "loop": loop_flag,
                    "root_layer": root_layer_name,
                }
            )
        return payloads

    def _determine_attachment_root(
        self,
        preferred: Optional[str],
        animation: AnimationData
    ) -> Optional[str]:
        """Return the attachment root layer name, preferring user-authored sockets."""
        if not animation or not animation.layers:
            return preferred
        if preferred:
            lowered = preferred.lower()
            for layer in animation.layers:
                if layer.name.lower() == lowered:
                    return layer.name
        return None

    def _assign_attachment_targets(
        self,
        payloads: List[Dict[str, Any]],
        layers: List[LayerData]
    ) -> None:
        """Resolve attachment target names to actual layer ids."""
        if not payloads:
            return
        lookup = {layer.name.lower(): layer for layer in layers}
        for payload in payloads:
            target = payload.get("target_layer", "")
            if not target:
                continue
            layer = self._find_layer_in_lookup(lookup, (target,))
            if layer:
                payload["target_layer_id"] = layer.layer_id
            else:
                self.log_widget.log(
                    f"Attachment target '{target}' not found; attachment '{payload.get('name')}' will be skipped.",
                    "WARNING"
                )
    
    def convert_bin_to_json(self):
        """Convert selected BIN file to JSON"""
        current_data = self.control_panel.bin_combo.currentData()
        current_text = self.control_panel.bin_combo.currentText()

        if current_data:
            bin_path = current_data
        elif current_text:
            bin_path = self._resolve_xml_bin_relative_path(current_text) or ""
        else:
            bin_path = ""

        if not bin_path or not bin_path.lower().endswith('.bin'):
            QMessageBox.warning(self, "Error", "Please select a .bin file")
            return
        
        if not os.path.exists(bin_path):
            QMessageBox.warning(self, "Error", "Selected BIN file no longer exists")
            self.log_widget.log(f"Missing BIN file: {bin_path}", "ERROR")
            return

        json_path = self._convert_bin_file(bin_path, announce=True)
        if json_path and not self.select_file_by_path(json_path):
            self.log_widget.log("Could not auto-select converted JSON file", "WARNING")

    def _convert_bin_file(self, bin_path: str, *, force: bool = False, announce: bool = True) -> Optional[str]:
        """Convert a specific BIN file via bin2json, optionally forcing re-export."""
        if not self.bin2json_path:
            if announce:
                self._warn_bin_conversion("Error", "bin2json script not found")
            self.log_widget.log("bin2json script not found; conversion skipped.", "ERROR")
            return None

        if not os.path.exists(bin_path):
            self.log_widget.log(f"BIN file not found: {bin_path}", "ERROR")
            if announce:
                self._warn_bin_conversion("Error", "Selected BIN file no longer exists")
            return None

        json_path = os.path.splitext(bin_path)[0] + '.json'
        bin_name = os.path.basename(bin_path).lower()
        normalized_path = os.path.normcase(os.path.normpath(bin_path))
        is_muppet_bin = bin_name.startswith("muppet_")
        is_my_singing_muppets = "my singing muppets.app" in normalized_path
        is_composer_bin = "_composer" in bin_name

        relative_display = self._relative_xml_bin_display(bin_path)
        action = "Re-exporting" if force else "Converting"
        self.log_widget.log(f"{action} {relative_display} to JSON...", "INFO")

        attempts: List[Tuple[str, str, List[str], str]] = []

        def queue_attempt(
            label: str,
            friendly: str,
            script_path: Optional[str],
            args: List[str],
            missing_msg: Optional[str] = None,
        ) -> None:
            if not script_path:
                if missing_msg:
                    self.log_widget.log(missing_msg, "WARNING")
                return
            attempts.append(
                (
                    label,
                    friendly,
                    self._build_python_command(script_path) + args,
                    os.path.dirname(script_path),
                )
            )

        if is_muppet_bin and is_my_singing_muppets:
            queue_attempt(
                "rev2",
                "Rev2 BIN parser",
                self.rev2_bin2json_path,
                ["d", bin_path],
                "Rev2 BIN converter missing; cannot auto-parse My Singing Muppets files.",
            )

        if is_muppet_bin and not is_my_singing_muppets:
            queue_attempt(
                "muppets",
                "Muppets BIN parser",
                self.muppets_bin2json_path,
                [bin_path, "-o", json_path],
                "Muppets BIN converter missing; cannot auto-parse muppet_* files.",
            )

        rev4_queued = False
        if is_composer_bin:
            if self.rev4_bin2json_path:
                queue_attempt(
                    "rev4",
                    "Rev4 BIN parser",
                    self.rev4_bin2json_path,
                    ["d", bin_path],
                    "Rev4 BIN converter missing; cannot auto-parse classic app files.",
                )
                rev4_queued = True
            else:
                self.log_widget.log(
                    "Composer BIN detected but Rev4 converter missing; cannot auto-parse composer files.",
                    "WARNING",
                )

        queue_attempt(
            "legacy",
            "Legacy BIN parser",
            self.legacy_bin2json_path,
            [bin_path, "-o", json_path],
            "Legacy BIN converter missing; cannot auto-parse early mobile files.",
        )
        queue_attempt(
            "choir",
            "Choir BIN parser",
            self.choir_bin2json_path,
            [bin_path, "-o", json_path],
            "Choir BIN converter missing; cannot auto-parse Monster Choir files.",
        )
        if not is_muppet_bin or not is_my_singing_muppets:
            queue_attempt(
                "rev2",
                "Rev2 BIN parser",
                self.rev2_bin2json_path,
                ["d", bin_path],
                "Rev2 BIN converter missing; cannot auto-parse My Singing Muppets files.",
            )
        if not rev4_queued:
            queue_attempt(
                "rev4",
                "Rev4 BIN parser",
                self.rev4_bin2json_path,
                ["d", bin_path],
                "Rev4 BIN converter missing; cannot auto-parse classic app files.",
            )
        queue_attempt(
            "oldest",
            "Oldest BIN parser",
            self.oldest_bin2json_path,
            [bin_path, "-o", json_path],
            "Oldest BIN converter missing; cannot auto-parse launch-build files.",
        )
        queue_attempt(
            "rev6",
            "Primary BIN parser",
            self.bin2json_path,
            ["d", bin_path],
        )

        if not attempts:
            self.log_widget.log(
                "No BIN converters are available; cannot perform conversion.",
                "ERROR",
            )
            if announce:
                self._warn_bin_conversion("Error", "No bin2json converters available.")
            return None

        error_messages: List[str] = []

        for label, friendly, cmd, cwd in attempts:
            self.log_widget.log(f"Attempting {friendly}...", "INFO")
            try:
                result = self._run_converter_command(cmd, cwd)
            except Exception as exc:
                error_text = f"{friendly} raised {exc}"
                self.log_widget.log(error_text, "ERROR")
                error_messages.append(error_text)
                continue

            if result.returncode == 0:
                stdout = (result.stdout or "").strip()
                if stdout:
                    self.log_widget.log(stdout, "INFO")
                self.log_widget.log(
                    f"Converted {os.path.basename(bin_path)} via {friendly}",
                    "SUCCESS",
                )
                self.refresh_file_list()
                return json_path

            error_text = (result.stderr or result.stdout or "").strip() or "Unknown error"
            error_messages.append(f"{friendly}: {error_text}")
            self.log_widget.log(f"{friendly} failed: {error_text}", "WARNING")

        failure_reason = error_messages[-1] if error_messages else "Unknown error"
        if announce:
            self._warn_bin_conversion("Conversion Failed", failure_reason)
        return None

    @staticmethod
    def _build_python_command(script_path: str) -> List[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--run-script", script_path]
        return [sys.executable, script_path]

    def _run_converter_command(self, cmd: List[str], cwd: Optional[str]) -> subprocess.CompletedProcess:
        """Execute a converter command, supporting embedded scripts in frozen builds."""
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
        """Execute a helper script inside the current interpreter when frozen."""
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

    def _warn_bin_conversion(self, title: str, message: str) -> None:
        """Show or suppress modal warnings triggered during BIN conversions."""
        if not self._suppress_bin_error_popups:
            QMessageBox.warning(self, title, message)

    @staticmethod
    def _dof_anim_name_from_path(asset_path: str) -> str:
        if asset_path.lower().startswith("bundle://"):
            asset_path = asset_path.split("bundle://", 1)[1].strip()
        name = Path(asset_path).name
        lower = name.lower()
        if lower.endswith(".asset"):
            name = name[:-6]
            lower = name.lower()
        if lower.endswith(".animbbb"):
            name = name[:-8]
            lower = name.lower()
        if lower.endswith(".json"):
            name = name[:-5]
        return name

    def _dof_output_root(self) -> Optional[str]:
        if not self.dof_path:
            return None
        return os.path.join(self.dof_path, "Output")

    def _dof_output_dir_for_asset(self, asset_path: str) -> Optional[str]:
        output_root = self._dof_output_root()
        if not output_root:
            return None
        asset_dir = os.path.dirname(asset_path)
        try:
            relative_dir = os.path.relpath(asset_dir, self.dof_path)
        except ValueError:
            relative_dir = ""
        return os.path.normpath(os.path.join(output_root, relative_dir))

    def _dof_output_dir_for_bundle_anim(self, bundle_path: str) -> Optional[str]:
        output_root = self._dof_output_root()
        if not output_root:
            return None
        anim_name = self._dof_anim_name_from_path(bundle_path)
        safe_name = anim_name.replace("/", "_").replace("\\", "_") or "bundle_anim"
        return os.path.normpath(os.path.join(output_root, "bundles", safe_name))

    @staticmethod
    def _path_is_under(path: str, root: str) -> bool:
        try:
            path_abs = os.path.abspath(path)
            root_abs = os.path.abspath(root)
            return os.path.commonpath([path_abs, root_abs]) == root_abs
        except (ValueError, OSError):
            return False

    def _is_dof_json_payload(self, json_path: Optional[str], payload: Optional[Dict[str, Any]] = None) -> bool:
        if not json_path or not self.dof_path:
            return False
        return self._path_is_under(json_path, self.dof_path)

    def _set_anchor_flip_state(self, flip_x: bool, flip_y: bool) -> None:
        # Keep UI and renderer in sync without retriggering signals.
        if hasattr(self, "control_panel") and self.control_panel:
            self.control_panel.anchor_flip_x_checkbox.blockSignals(True)
            self.control_panel.anchor_flip_y_checkbox.blockSignals(True)
            self.control_panel.anchor_flip_x_checkbox.setChecked(bool(flip_x))
            self.control_panel.anchor_flip_y_checkbox.setChecked(bool(flip_y))
            self.control_panel.anchor_flip_x_checkbox.blockSignals(False)
            self.control_panel.anchor_flip_y_checkbox.blockSignals(False)
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.renderer.anchor_flip_x = bool(flip_x)
            self.gl_widget.renderer.anchor_flip_y = bool(flip_y)

    def _apply_dof_anchor_flip_defaults(self, json_path: Optional[str], payload: Optional[Dict[str, Any]] = None) -> None:
        is_dof = bool(self.dof_search_enabled) or self._is_dof_json_payload(json_path, payload)
        if is_dof:
            # DOF anchor flips are now baked into exported JSON. Keep UI flips off.
            self._set_anchor_flip_state(False, False)

    def _set_dof_conversion_active(self, active: bool) -> None:
        """Toggle UI elements while a DOF conversion is running."""
        if hasattr(self, "control_panel") and hasattr(self.control_panel, "set_dof_convert_state"):
            self.control_panel.set_dof_convert_state(not active)

    def _update_dof_index_for_paths(self, paths: List[str]) -> None:
        """Insert or update specific DOF assets/outputs without rescanning the full tree."""
        if not self.dof_path:
            return
        entries = list(self.dof_file_index)
        normalized_lookup = {entry.normalized_path(): entry for entry in entries}
        added = 0

        for path in paths:
            if not path or not os.path.exists(path):
                continue
            relative_path = os.path.relpath(path, self.dof_path).replace("\\", "/")
            entry = AnimationFileEntry(
                name=os.path.basename(path),
                relative_path=relative_path,
                full_path=path,
            )
            key = entry.normalized_path()
            existing = normalized_lookup.get(key)
            if existing:
                existing.name = entry.name
                existing.relative_path = entry.relative_path
                existing.full_path = entry.full_path
            else:
                entries.append(entry)
                normalized_lookup[key] = entry
                added += 1

        if added:
            entries.sort(key=lambda item: item.relative_path.lower())
            self.dof_file_index = entries

        if self.dof_search_enabled:
            if added:
                self.log_widget.log(f"Updated DOF index (+{added})", "INFO")
            self.apply_file_filter()

    def _start_dof_conversion(
        self,
        cmd: List[str],
        cwd: Optional[str],
        asset_path: str,
        output_json: str,
    ) -> None:
        """Kick off a DOF conversion in a background process."""
        if self._dof_convert_process and self._dof_convert_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "Conversion Running", "A DOF conversion is already in progress.")
            return

        process = QProcess(self)
        process.setProgram(cmd[0])
        process.setArguments(cmd[1:])
        if cwd:
            process.setWorkingDirectory(cwd)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.readyReadStandardOutput.connect(self._on_dof_conversion_stdout)
        process.readyReadStandardError.connect(self._on_dof_conversion_stderr)
        process.finished.connect(self._on_dof_conversion_finished)
        process.errorOccurred.connect(self._on_dof_conversion_error)

        self._dof_convert_process = process
        self._dof_convert_asset_path = asset_path
        self._dof_convert_output_json = output_json
        self._dof_convert_stdout_buffer = ""
        self._dof_convert_stderr_buffer = ""
        self._set_dof_conversion_active(True)

        process.start()

    def _drain_dof_conversion_output(self, *, is_error: bool) -> None:
        process = self._dof_convert_process
        if not process:
            return
        data = process.readAllStandardError() if is_error else process.readAllStandardOutput()
        if not data:
            return
        text = bytes(data).decode("utf-8", errors="replace")
        if not text:
            return

        if is_error:
            buffer = self._dof_convert_stderr_buffer + text
        else:
            buffer = self._dof_convert_stdout_buffer + text

        normalized = buffer.replace("\r\n", "\n").replace("\r", "\n")
        if normalized.endswith("\n"):
            lines = normalized.split("\n")
            buffer = ""
        else:
            lines = normalized.split("\n")
            buffer = lines.pop() if lines else ""

        level = "WARNING" if is_error else "INFO"
        for line in lines:
            stripped = line.strip()
            if stripped:
                self.log_widget.log(f"[DOF] {stripped}", level)

        if is_error:
            self._dof_convert_stderr_buffer = buffer
        else:
            self._dof_convert_stdout_buffer = buffer

    def _flush_dof_conversion_buffers(self) -> Tuple[str, str]:
        out_text = self._dof_convert_stdout_buffer.strip()
        err_text = self._dof_convert_stderr_buffer.strip()
        if out_text:
            self.log_widget.log(f"[DOF] {out_text}", "INFO")
        if err_text:
            self.log_widget.log(f"[DOF] {err_text}", "WARNING")
        self._dof_convert_stdout_buffer = ""
        self._dof_convert_stderr_buffer = ""
        return out_text, err_text

    def _on_dof_conversion_stdout(self) -> None:
        if self.sender() is not self._dof_convert_process:
            return
        self._drain_dof_conversion_output(is_error=False)

    def _on_dof_conversion_stderr(self) -> None:
        if self.sender() is not self._dof_convert_process:
            return
        self._drain_dof_conversion_output(is_error=True)

    def _finalize_dof_conversion_state(self) -> Tuple[Optional[str], Optional[str]]:
        asset_path = self._dof_convert_asset_path
        output_json = self._dof_convert_output_json
        self._dof_convert_process = None
        self._dof_convert_asset_path = None
        self._dof_convert_output_json = None
        self._set_dof_conversion_active(False)
        return asset_path, output_json

    def _on_dof_conversion_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        process = self.sender()
        if process is not self._dof_convert_process:
            return
        self._drain_dof_conversion_output(is_error=False)
        self._drain_dof_conversion_output(is_error=True)
        out_text, err_text = self._flush_dof_conversion_buffers()
        asset_path, output_json = self._finalize_dof_conversion_state()
        process.deleteLater()

        success = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        if not success:
            message = err_text or out_text or f"DOF conversion exited with code {exit_code}."
            self.log_widget.log(f"DOF conversion failed: {message}", "ERROR")
            QMessageBox.warning(self, "Conversion Failed", message)
            return

        if output_json and os.path.exists(output_json):
            self.log_widget.log(f"DOF JSON created: {output_json}", "SUCCESS")
        else:
            self.log_widget.log("DOF conversion completed but JSON was not found.", "WARNING")

        self._update_dof_index_for_paths([asset_path or "", output_json or ""])
        if output_json and os.path.exists(output_json):
            if not self.select_file_by_path(output_json):
                if self.dof_path:
                    self.control_panel.bin_combo.setCurrentText(os.path.relpath(output_json, self.dof_path))
            self.load_json_file(output_json)

    def _on_dof_conversion_error(self, error: QProcess.ProcessError) -> None:
        process = self.sender()
        if process is not self._dof_convert_process:
            return
        self._drain_dof_conversion_output(is_error=False)
        self._drain_dof_conversion_output(is_error=True)
        out_text, err_text = self._flush_dof_conversion_buffers()
        asset_path, output_json = self._finalize_dof_conversion_state()
        process.deleteLater()

        if asset_path or output_json:
            self._update_dof_index_for_paths([asset_path or "", output_json or ""])

        error_label = getattr(error, "name", None)
        if not error_label:
            error_label = str(error)

        message = err_text or out_text or f"DOF conversion error: {error_label}"
        self.log_widget.log(f"DOF conversion failed: {message}", "ERROR")
        QMessageBox.warning(self, "Conversion Failed", message)
    
    def on_bin_selected(self, index: int):
        """Handle BIN/JSON file selection"""
        if index < 0:
            return
        
        selected_path = self.control_panel.bin_combo.currentData()
        display_name = self.control_panel.bin_combo.currentText()
        
        if not selected_path and display_name:
            selected_path = self._resolve_xml_bin_relative_path(display_name)
        
        if not selected_path:
            return
        
        lower_path = selected_path.lower()
        if self.dof_search_enabled:
            if lower_path.endswith('.json'):
                self.load_json_file(selected_path)
            elif lower_path.endswith('.animbbb.asset'):
                self.log_widget.log("Select 'Convert DOF to JSON' to use this asset.", "INFO")
            elif lower_path.startswith('bundle://'):
                self.log_widget.log("Select 'Convert DOF to JSON' to use this bundle animation.", "INFO")
            else:
                self.log_widget.log("Unsupported DOF selection; choose a .ANIMBBB.asset or .json.", "WARNING")
            return

        if lower_path.endswith('.json'):
            self.load_json_file(selected_path)
        elif lower_path.endswith('.bin'):
            self.log_widget.log("Please convert BIN to JSON first", "WARNING")
    
    def convert_dof_to_json(self):
        """Convert selected DOF .ANIMBBB.asset to JSON"""
        if not self.dof_anim_to_json_path or not os.path.exists(self.dof_anim_to_json_path):
            QMessageBox.warning(self, "Missing Tool", "DOF converter script was not found.")
            return
        if not self.dof_path:
            QMessageBox.warning(self, "Missing Path", "Set the DOF Assets path before converting.")
            return
        if self._dof_convert_process and self._dof_convert_process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(self, "Conversion Running", "A DOF conversion is already in progress.")
            return

        current_data = self.control_panel.bin_combo.currentData()
        current_text = self.control_panel.bin_combo.currentText()
        if current_data:
            asset_path = current_data
        elif current_text:
            asset_path = os.path.join(self.dof_path, current_text)
        else:
            asset_path = ""

        is_bundle = asset_path.lower().startswith("bundle://")
        if not asset_path or (not is_bundle and not asset_path.lower().endswith(".animbbb.asset")):
            QMessageBox.warning(self, "Error", "Please select a DOF .ANIMBBB asset or bundle entry")
            return

        if not is_bundle and not os.path.exists(asset_path):
            QMessageBox.warning(self, "Error", "Selected DOF asset file no longer exists")
            self.log_widget.log(f"Missing DOF asset file: {asset_path}", "ERROR")
            return

        output_dir = (
            self._dof_output_dir_for_bundle_anim(asset_path)
            if is_bundle
            else self._dof_output_dir_for_asset(asset_path)
        )
        if not output_dir:
            QMessageBox.warning(self, "Error", "Unable to determine DOF output directory.")
            return
        os.makedirs(output_dir, exist_ok=True)

        if is_bundle:
            display = asset_path
            self.log_widget.log(f"Converting DOF bundle {display} to JSON...", "INFO")
            bundle_anim = asset_path.split("bundle://", 1)[1].strip()
            cmd = self._build_python_command(self.dof_anim_to_json_path) + [
                f"bundle://{bundle_anim}",
                "--output",
                output_dir,
                "--bundle-root",
                self.dof_path,
            ]
        else:
            display = os.path.relpath(asset_path, self.dof_path).replace("\\", "/")
            self.log_widget.log(f"Converting DOF asset {display} to JSON...", "INFO")
            cmd = self._build_python_command(self.dof_anim_to_json_path) + [
                asset_path,
                "--output",
                output_dir,
                "--assets-root",
                self.dof_path,
            ]
        if getattr(self.control_panel, "dof_mesh_pivot_checkbox", None) and self.control_panel.dof_mesh_pivot_checkbox.isChecked():
            cmd.append("--mesh-pivot-local")
        if (
            getattr(self.control_panel, "dof_include_mesh_xml_checkbox", None)
            and self.control_panel.dof_include_mesh_xml_checkbox.isChecked()
        ):
            cmd.append("--include-mesh-xml")
        if (
            getattr(self.control_panel, "dof_premultiply_alpha_checkbox", None)
            and self.control_panel.dof_premultiply_alpha_checkbox.isChecked()
        ):
            cmd.append("--premultiply-alpha")
        try:
            alpha_hardness = float(self.settings.value("dof/alpha_hardness", 0.0, type=float))
        except (TypeError, ValueError):
            alpha_hardness = 0.0
        alpha_hardness = max(0.0, min(2.0, alpha_hardness))
        if alpha_hardness > 1e-6:
            cmd += ["--alpha-hardness", f"{alpha_hardness:.3f}"]
        if getattr(self.control_panel, "dof_swap_anchor_report_checkbox", None) and self.control_panel.dof_swap_anchor_report_checkbox.isChecked():
            cmd.append("--swap-anchor-report")
        if getattr(self.control_panel, "dof_swap_anchor_edge_align_checkbox", None) and self.control_panel.dof_swap_anchor_edge_align_checkbox.isChecked():
            cmd.append("--swap-anchor-edge-align")
        if getattr(self.control_panel, "dof_swap_anchor_pivot_offset_checkbox", None) and self.control_panel.dof_swap_anchor_pivot_offset_checkbox.isChecked():
            cmd.append("--swap-anchor-pivot-offset")
        if getattr(self.control_panel, "dof_swap_anchor_report_override_checkbox", None) and self.control_panel.dof_swap_anchor_report_override_checkbox.isChecked():
            cmd.append("--swap-anchor-report-override")
        anim_name = self._dof_anim_name_from_path(asset_path)
        output_json = os.path.join(output_dir, f"{anim_name}.json")
        self._start_dof_conversion(cmd, os.path.dirname(self.dof_anim_to_json_path), asset_path, output_json)

    def load_json_file(self, json_path: str):
        """Load animation data from JSON file"""
        try:
            with open(json_path, 'r') as f:
                payload = json.load(f)
            self._apply_json_payload(json_path, payload)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(tb)
            self.log_widget.log(f"Error loading JSON: {e}", "ERROR")
            self.log_widget.log(tb, "ERROR")

    def _apply_json_payload(self, json_path: str, payload: Dict[str, Any], announce: bool = True) -> None:
        """Apply a parsed JSON payload to the UI and animation combo."""
        # Reset animation selection state early so combo updates cannot persist
        # the previously loaded animation into the new payload.
        self.current_animation_index = -1
        self.current_animation_name = None
        self.current_json_path = json_path
        self.current_json_data = payload
        self.original_json_data = copy.deepcopy(payload)
        self.legacy_animation_active = self._is_legacy_payload(payload, json_path)
        self.active_legacy_sheet_key = None
        self.legacy_sheet_overrides.clear()
        self.current_blend_version = self._determine_blend_version(payload)
        self.current_animation_revision = self._determine_animation_revision(
            payload,
            self.legacy_animation_active,
            json_path
        )
        self._apply_dof_anchor_flip_defaults(json_path, payload)
        self.source_atlas_lookup = {}
        if announce:
            display_name = os.path.basename(json_path) if json_path else "animation data"
            self.log_widget.log(f"Loaded JSON file: {display_name}", "SUCCESS")
            self.log_widget.log(
                f"Detected blend mapping version {self.current_blend_version}",
                "INFO"
            )
            if self.current_animation_revision is not None:
                suffix = " (Muppet Revision)" if self._is_muppet_payload() else ""
                self.log_widget.log(
                    f"Detected BIN revision {self.current_animation_revision}{suffix}",
                    "INFO"
                )
            else:
                self.log_widget.log(
                    "BIN revision could not be determined from this export.",
                    "INFO"
                )

        self.control_panel.anim_combo.clear()
        if 'anims' in payload:
            anim_names = [anim.get('name', f"Animation {idx + 1}") for idx, anim in enumerate(payload['anims'])]
            if anim_names:
                self.control_panel.anim_combo.addItems(anim_names)
                if announce:
                    self.log_widget.log(f"Found {len(anim_names)} animations", "INFO")
        self.current_animation_index = -1

    @staticmethod
    def _is_legacy_payload(payload: Dict[str, Any], json_path: Optional[str]) -> bool:
        """Heuristically determine whether an animation payload came from a legacy BIN."""
        if payload.get("legacy_format"):
            return True
        rev_value = payload.get("rev")
        try:
            rev_int = int(rev_value)
        except (TypeError, ValueError):
            rev_int = None
        if rev_int in (2, 4):
            return True
        if json_path:
            norm_path = os.path.normcase(json_path)
            markers = [
                os.path.normcase(os.path.join("My Singing Monsters.app", "data")),
                os.path.normcase(os.path.join("My Singing Monsters OLDEST.app", "data")),
                os.path.normcase(os.path.join("My Singing Muppets.app", "data")),
                os.path.normcase(os.path.join("My Muppets Show.app", "data")),
                os.path.normcase(os.path.join("Monsters Composer.app", "data")),
                os.path.normcase(os.path.join("Composer", "data")),
            ]
            for marker in markers:
                if marker in norm_path:
                    return True
        return False

    def _normalize_animation_file_payload(self, payload: Any) -> Optional[Dict[str, Any]]:
        """Coerce arbitrary animation exports into the canonical JSON schema."""
        if not isinstance(payload, dict):
            return None
        anims = payload.get("anims")
        if isinstance(anims, list):
            return self._coerce_rev2_payload(payload)
        layers = payload.get("layers")
        if isinstance(layers, list):
            anim_copy = copy.deepcopy(payload)
            sources = anim_copy.pop("sources", payload.get("sources", []))
            blend_version = anim_copy.pop("blend_version", payload.get("blend_version"))
            rev_value = anim_copy.pop("rev", payload.get("rev"))
            container = {
                "anims": [anim_copy],
                "sources": sources if isinstance(sources, list) else []
            }
            if blend_version is not None:
                container["blend_version"] = blend_version
            else:
                container["blend_version"] = self.current_blend_version or 1
            if rev_value is not None:
                container["rev"] = rev_value
            return self._coerce_rev2_payload(container)
        return None

    def _coerce_rev2_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Translate rev2 (My Muppets) exports into our canonical schema."""
        if not isinstance(payload, dict):
            return payload
        rev = payload.get("rev")
        try:
            rev_value = int(rev)
        except (TypeError, ValueError):
            rev_value = None
        if rev_value != 2:
            return payload

        anims = payload.get("anims")
        if not isinstance(anims, list):
            return payload

        for anim in anims:
            if not isinstance(anim, dict):
                continue
            layers = anim.get("layers")
            if not isinstance(layers, list):
                continue
            for layer in layers:
                if not isinstance(layer, dict):
                    continue
                layer["parent"] = self._normalize_parent_id(layer.get("parent", -1))
        return payload

    @staticmethod
    def _normalize_parent_id(raw_value: Any) -> int:
        """Coerce rev2-style parent identifiers into integers."""
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if text:
                prefix = text.split(":", 1)[0]
                try:
                    return int(prefix)
                except ValueError:
                    pass
        return -1

    def save_animation_to_file(self):
        """Export the currently loaded animation to a standalone JSON file."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before saving.")
            return
        self._persist_current_animation_edits()
        default_path = self.settings.value('animation/last_save_path', '', type=str) or ''
        if not default_path:
            base_dir = os.path.dirname(self.current_json_path) if self.current_json_path else str(Path.home())
            base_name = (animation.name or "animation").strip() or "animation"
            safe_name = re.sub(r'[\\\\/:"*?<>|]+', "_", base_name)
            default_path = os.path.join(base_dir, f"{safe_name}.json")
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Animation",
            default_path,
            "Animation JSON (*.json);;All Files (*)"
        )
        if not filename:
            return
        if not filename.lower().endswith(".json"):
            filename += ".json"
        if self.current_json_data:
            payload = copy.deepcopy(self.current_json_data)
        else:
            payload = {
                "blend_version": self.current_blend_version or 1,
                "sources": [],
                "anims": [self._export_animation_dict(animation)],
                "rev": 6,
            }
        try:
            with open(filename, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception as exc:
            self.log_widget.log(f"Failed to save animation: {exc}", "ERROR")
            QMessageBox.warning(self, "Save Failed", f"Could not save animation:\n{exc}")
            return
        self.settings.setValue('animation/last_save_path', filename)
        self.log_widget.log(f"Saved animation to {os.path.basename(filename)}", "SUCCESS")
        self._save_keyframe_layers_sidecar(filename, animation)
        if getattr(self.export_settings, "update_source_json_on_save", False) and self.current_json_path:
            try:
                with open(self.current_json_path, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                self.original_json_data = copy.deepcopy(payload)
                self.log_widget.log(
                    f"Updated source JSON: {os.path.basename(self.current_json_path)}",
                    "INFO",
                )
                self._save_keyframe_layers_sidecar(self.current_json_path, animation)
            except Exception as exc:
                self.log_widget.log(
                    f"Failed to update source JSON '{self.current_json_path}': {exc}",
                    "WARNING",
                )

    def export_animation_to_bin(self):
        """Convert the current animation into a BIN file using the bin2json script."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before exporting a BIN.")
            return
        self._persist_current_animation_edits()
        if not self.bin2json_path or not os.path.exists(self.bin2json_path):
            QMessageBox.warning(self, "Missing Tool", "bin2json script was not found; cannot export BIN.")
            return
        default_path = self.settings.value('animation/last_bin_export', '', type=str) or ''
        if not default_path:
            base_dir = os.path.dirname(self.current_json_path) if self.current_json_path else str(Path.home())
            base_name = (animation.name or "animation").strip() or "animation"
            safe_name = re.sub(r'[\\/:\"*?<>|]+', "_", base_name)
            default_path = os.path.join(base_dir, f"{safe_name}.bin")
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Animation BIN",
            default_path,
            "Animation BIN (*.bin);;All Files (*)"
        )
        if not filename:
            return
        if not filename.lower().endswith(".bin"):
            filename += ".bin"
        def _payload_has_dof_meta(payload: Optional[Dict[str, Any]]) -> bool:
            if not isinstance(payload, dict):
                return False
            return isinstance(payload.get("dof_meta"), dict)

        is_dof_export = bool(
            self.dof_search_enabled
            or _payload_has_dof_meta(self.current_json_data)
            or self._is_dof_json_payload(self.current_json_path, self.current_json_data)
        )

        has_pending_edits = self._has_pending_json_edits()
        passthrough_json: Optional[str] = None
        if (
            not has_pending_edits
            and self.current_json_path
            and os.path.exists(self.current_json_path)
        ):
            passthrough_json = self.current_json_path
        if is_dof_export:
            # Force a re-export to bake the viewer render order into the payload.
            passthrough_json = None

        payload: Optional[Dict[str, Any]] = None
        if not passthrough_json:
            if self.current_json_data:
                payload = copy.deepcopy(self.current_json_data)
            elif self.current_json_path and os.path.exists(self.current_json_path):
                try:
                    with open(self.current_json_path, "r", encoding="utf-8") as handle:
                        payload = json.load(handle)
                except Exception:
                    payload = None
            if not payload:
                payload = {
                    "blend_version": self.current_blend_version or 1,
                    "sources": [],
                    "anims": [],
                }
            export_animation = animation
            if is_dof_export and hasattr(self, "gl_widget"):
                try:
                    layer_world_states = self.gl_widget._build_layer_world_states()
                    render_layers = self.gl_widget._get_render_layers(layer_world_states)
                    if render_layers:
                        export_animation = copy.copy(animation)
                        export_animation.layers = list(render_layers)
                        self.log_widget.log(
                            "Baked viewer render order into export payload.",
                            "INFO",
                        )
                except Exception as exc:
                    self.log_widget.log(
                        f"Failed to bake render order; exporting original layer list ({exc})",
                        "WARNING",
                    )
            self._inject_animation_into_payload(payload, export_animation)
            self.log_widget.log(
                "Merged current edits into export payload.",
                "DEBUG"
            )
        else:
            self.log_widget.log(
                "No edits detected; exporting original JSON payload.",
                "DEBUG"
            )

        tmp_dir: Optional[tempfile.TemporaryDirectory] = None
        try:
            tmp_dir = tempfile.TemporaryDirectory()
            temp_json = os.path.join(tmp_dir.name, "animation.json")
            if passthrough_json:
                shutil.copy2(passthrough_json, temp_json)
            else:
                with open(temp_json, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
            cmd = self._build_python_command(self.bin2json_path) + ["b", temp_json]
            result = self._run_converter_command(cmd, os.path.dirname(self.bin2json_path))
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "bin2json conversion failed.")
            temp_bin = os.path.splitext(temp_json)[0] + ".bin"
            if not os.path.exists(temp_bin):
                raise RuntimeError("bin2json did not produce a BIN file.")
            os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
            shutil.move(temp_bin, filename)
        except Exception as exc:
            if tmp_dir:
                tmp_dir.cleanup()
            self.log_widget.log(f"Failed to export animation BIN: {exc}", "ERROR")
            QMessageBox.warning(self, "Export Failed", f"Could not create animation BIN:\n{exc}")
            return
        if tmp_dir:
            tmp_dir.cleanup()
        self.settings.setValue('animation/last_bin_export', filename)
        self.log_widget.log(f"Exported animation BIN to {os.path.basename(filename)}", "SUCCESS")

    def load_saved_animation(self):
        """Load an animation JSON exported from the viewer or the game."""
        last_load = self.settings.value('animation/last_load_path', '', type=str) or ''
        if not last_load:
            last_load = self.settings.value('animation/last_save_path', '', type=str) or ''
        if not last_load:
            last_load = self.current_json_path or str(Path.home())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Load Animation",
            last_load,
            "Animation JSON (*.json);;All Files (*)"
        )
        if not filename:
            return
        try:
            with open(filename, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            self.log_widget.log(f"Failed to open animation file: {exc}", "ERROR")
            QMessageBox.warning(self, "Load Failed", f"Could not read animation file:\n{exc}")
            return
        normalized = self._normalize_animation_file_payload(payload)
        if not normalized:
            self.log_widget.log("Selected file does not contain animation data.", "ERROR")
            QMessageBox.warning(self, "Invalid File", "Selected file does not contain animation data.")
            return
        self._apply_json_payload(filename, normalized)
        self.settings.setValue('animation/last_load_path', filename)
        self.settings.setValue('animation/last_save_path', filename)
        combo = self.control_panel.anim_combo
        if combo.count() == 0:
            self.log_widget.log("Loaded file contains no animations.", "WARNING")
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
        self.on_animation_selected(0)

    def _determine_blend_version(self, json_data: Dict) -> int:
        """
        Decide which blend mapping the source JSON uses.
        Older exports only included blend ids 0/1 where 1 represented additive layers.
        Newer exports (bin2json rev6-2) tag additive layers as 2 and include a
        'blend_version' metadata field. We need to support both so existing libraries
        render correctly.
        """
        version = json_data.get('blend_version')
        has_high_blend = False
        for anim in json_data.get('anims', []):
            for layer in anim.get('layers', []):
                try:
                    blend_val = int(layer.get('blend', 0))
                except (TypeError, ValueError):
                    blend_val = 0
                if blend_val >= 2:
                    has_high_blend = True
                    break
            if has_high_blend:
                break
        if isinstance(version, int) and version >= 2 and has_high_blend:
            return version
        return 1

    @staticmethod
    def _determine_animation_revision(
        payload: Dict[str, Any],
        legacy_active: bool,
        json_path: Optional[str]
    ) -> Optional[int]:
        """
        Infer the BIN revision by inspecting payload metadata. Modern exports include
        an explicit 'rev' field; older legacy files omit it, so we tag them as the
        historical revision used by those BINs (rev 4).
        """
        rev_value = payload.get("rev")
        if isinstance(rev_value, int):
            return rev_value
        if isinstance(rev_value, str):
            try:
                return int(rev_value.strip())
            except (TypeError, ValueError):
                pass
        if legacy_active:
            return 4
        return None

    @staticmethod
    def _normalize_blend_value(raw_value: int, version: int) -> int:
        """
        Convert raw blend ids from JSON into the canonical renderer mapping.
        Version 1 (legacy) used 0=standard, 1=additive.
        Version 2+ matches the game's BlendType enum directly.
        """
        try:
            blend = int(raw_value)
        except (TypeError, ValueError):
            blend = 0

        valid_modes = {
            BlendMode.STANDARD,
            BlendMode.PREMULT_ALPHA,
            BlendMode.ADDITIVE,
            BlendMode.PREMULT_ALPHA_ALT,
            BlendMode.PREMULT_ALPHA_ALT2,
            BlendMode.INHERIT,
            BlendMode.MULTIPLY,
            BlendMode.SCREEN,
        }
        if version >= 2:
            return blend if blend in valid_modes else BlendMode.STANDARD
        # Legacy exports only distinguished between the default blend (0) and a single
        # alternate mode (1). In practice, files authored with this format used `1` for
        # additive-style glows, so map it to the additive id in the modern enum.
        # Hybrid safety: some files are missing blend_version metadata but still carry
        # modern ids (2/6/7). Preserve those ids when present.
        if blend >= 2 and blend in valid_modes:
            return blend
        return BlendMode.ADDITIVE if blend == 1 else BlendMode.STANDARD

    def _current_json_cache_key(self) -> Optional[str]:
        """Return normalized path key for per-JSON caches."""
        if not self.current_json_path:
            return None
        return os.path.normcase(os.path.normpath(self.current_json_path))

    def _has_pending_json_edits(self) -> bool:
        """Return True if the in-memory JSON differs from the original baseline."""
        if self.current_json_data is None:
            return False
        if self.original_json_data is None:
            return True
        try:
            return self.current_json_data != self.original_json_data
        except Exception:
            return True

    @staticmethod
    def _token_from_path(source_path: Optional[str]) -> Optional[str]:
        """Return the monster token inferred from a JSON/BIN path."""
        if not source_path:
            return None
        try:
            stem = Path(source_path).stem
        except Exception:
            return None
        if not stem:
            return None
        token = stem.lower()
        if token.startswith("monster_"):
            token = token[8:]
        return token or None

    def _should_force_standard_blend(
        self,
        token: Optional[str],
        layer_name: str,
        blend_mode: int
    ) -> bool:
        """Return True if a layer's blend mode should be overridden for compatibility."""
        return False

    @staticmethod
    def _should_promote_light_layer_blend(
        layer_name: str,
        shader_name: Optional[str],
        blend_mode: int,
    ) -> bool:
        """
        Return True when a DOF light layer should default to additive.

        Some legacy/stale converted DOF JSONs still carry blend=0 on Sprite_light
        segments while using Anim2D/Normal+Alpha. Promote those to additive at
        load-time to preserve expected glow behavior.
        """
        if blend_mode != BlendMode.STANDARD:
            return False
        if not layer_name:
            return False
        lowered_name = layer_name.strip().lower()
        if not lowered_name.startswith("sprite_light"):
            return False
        lowered_shader = (shader_name or "").strip().lower()
        return lowered_shader in {"", "anim2d/normal+alpha"}

    def _apply_cached_layer_visibility(self, layers: List[LayerData]):
        """Apply stored layer visibility values for the active JSON."""
        cache_key = self._current_json_cache_key()
        if not cache_key:
            return
        visibility_map = self.layer_visibility_cache.get(cache_key)
        if not visibility_map:
            return
        for layer in layers:
            if layer.layer_id in visibility_map:
                layer.visible = visibility_map[layer.layer_id]

    def _remember_layer_visibility(self, layer: LayerData):
        """Persist a layer's visibility so other animations reuse it."""
        cache_key = self._current_json_cache_key()
        if not cache_key:
            return
        visibility_map = self.layer_visibility_cache.setdefault(cache_key, {})
        visibility_map[layer.layer_id] = layer.visible

    def _record_layer_defaults(self, layers: List[LayerData]):
        """Capture the default ordering and visibility for the current animation state."""
        self._default_layer_order = [layer.layer_id for layer in layers]
        self._default_layer_visibility = {layer.layer_id: layer.visible for layer in layers}
        self._default_hidden_layer_ids = {layer.layer_id for layer in layers if not layer.visible}
        self.layer_panel.set_default_hidden_layers(self._default_hidden_layer_ids)

    def _capture_pose_baseline(self):
        """Snapshot the current animation layers for pose reset/undo reference."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            self._pose_baseline_player = None
            self._pose_baseline_lookup = {}
            return
        baseline_layers = self._clone_layers(animation.layers)
        baseline_anim = AnimationData(
            animation.name,
            animation.width,
            animation.height,
            animation.loop_offset,
            animation.centered,
            baseline_layers,
        )
        self._pose_baseline_player = AnimationPlayer()
        self._pose_baseline_player.load_animation(baseline_anim)
        self._pose_baseline_lookup = {layer.layer_id: layer for layer in baseline_layers}

    def _get_pose_baseline_state(self, layer_id: int, time_value: float) -> Optional[Dict[str, Any]]:
        """Return the baseline layer local state for a given time."""
        if not self._pose_baseline_player:
            return None
        layer = self._pose_baseline_lookup.get(layer_id)
        if not layer:
            return None
        return self._pose_baseline_player.get_layer_state(layer, time_value)

    def _load_audio_preferences_from_storage(self):
        """Populate audio preference flags from QSettings."""
        self.sync_audio_to_bpm = self.settings.value('audio/sync_to_bpm', True, type=bool)
        self.pitch_shift_enabled = self.settings.value('audio/pitch_shift_enabled', False, type=bool)
        self.chipmunk_mode = self.settings.value('audio/chipmunk_mode', False, type=bool)

    def _load_constraints_from_settings(self) -> List[ConstraintSpec]:
        """Load constraints from QSettings (global, not per animation)."""
        blob = self.settings.value('constraints/global', '[]', type=str) or '[]'
        try:
            data = json.loads(blob)
        except Exception:
            data = []
        constraints: List[ConstraintSpec] = []
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict):
                    constraints.append(ConstraintSpec.from_dict(entry))
        return constraints

    def _save_constraints_to_settings(self) -> None:
        """Persist constraints to QSettings."""
        payload = [spec.to_dict() for spec in self.constraints]
        try:
            blob = json.dumps(payload)
        except Exception:
            blob = "[]"
        self.settings.setValue('constraints/global', blob)

    def _load_constraint_layer_disables(self) -> List[str]:
        blob = self.settings.value('constraints/disabled_layers', '[]', type=str) or '[]'
        try:
            data = json.loads(blob)
        except Exception:
            data = []
        if isinstance(data, list):
            return [str(item) for item in data if item]
        return []

    def _save_constraint_layer_disables(self) -> None:
        disabled = sorted(self.constraint_manager.disabled_layer_names)
        try:
            blob = json.dumps(disabled)
        except Exception:
            blob = "[]"
        self.settings.setValue('constraints/disabled_layers', blob)

    def _load_base_bpm_overrides(self) -> None:
        """Load persisted base BPM overrides per monster token."""
        blob = self.settings.value('audio/base_bpm_overrides', '{}', type=str) or '{}'
        overrides: Dict[str, float] = {}
        try:
            data = json.loads(blob)
        except Exception:
            data = {}
        if isinstance(data, dict):
            for key, value in data.items():
                try:
                    overrides[str(key).lower()] = float(value)
                except (TypeError, ValueError):
                    continue
        self.monster_base_bpm_overrides = overrides

    def _save_base_bpm_overrides(self) -> None:
        """Persist monster base BPM overrides to QSettings."""
        try:
            blob = json.dumps(self.monster_base_bpm_overrides)
        except Exception:
            blob = "{}"
        self.settings.setValue('audio/base_bpm_overrides', blob)

    def _apply_audio_preferences_to_controls(self):
        """Sync control panel toggles and audio engine with stored preferences."""
        if hasattr(self, 'control_panel'):
            self.control_panel.set_sync_audio_checkbox(self.sync_audio_to_bpm)
            self.control_panel.set_pitch_shift_checkbox(self.pitch_shift_enabled)
            self.control_panel.set_metronome_checkbox(self.metronome_enabled)
            self.control_panel.set_metronome_audible_checkbox(self.metronome_audible)
        self._update_audio_speed()

    def _set_current_bpm(self, value: float, *, update_ui: bool = True, store_override: bool = False):
        """Apply BPM changes, optionally updating UI and storing overrides."""
        clamped = max(20.0, min(300.0, float(value)))
        self.current_bpm = clamped
        if update_ui:
            self.control_panel.set_bpm_value(clamped)
        self._start_hang_watchdog("update_bpm", timeout=12.0)
        self._apply_playback_speed()
        self._update_metronome_state()
        self._stop_hang_watchdog()
        self._refresh_timeline_beats(force_regenerate=True)
        if store_override and self.current_animation_name:
            self.animation_bpm_overrides[self.current_animation_name] = clamped
        self._update_audio_speed()

    def _apply_playback_speed(self) -> None:
        """Apply the combined BPM + muppet scaling to the animation player."""
        player = getattr(self.gl_widget, "player", None)
        if not player:
            return
        base = max(1e-3, self.current_base_bpm)
        tempo_scale = self.current_bpm / base
        anim_scale = max(0.25, min(4.0, float(self.animation_time_scale or 1.0)))
        player.set_playback_speed(tempo_scale * anim_scale)

    def _handle_audio_cleared(self) -> None:
        """Reset animation scaling when no audio clip is available."""
        self.current_audio_from_manifest = False
        self._reset_audio_loop_state()
        if self.animation_time_scale != 1.0:
            self.animation_time_scale = 1.0
            self._apply_playback_speed()

    def _normalize_audio_scope(self, scope: Optional[str]) -> Optional[str]:
        normalized = self._normalize_audio_key(scope or "")
        return normalized or None

    def _normalize_audio_track_id(self, track_id: str) -> str:
        normalized = self._normalize_audio_key(track_id)
        if normalized:
            return normalized
        return str(track_id or "").strip().lower()

    def _get_muted_tracks_for_scope(self, scope: Optional[str]) -> Set[str]:
        key = self._normalize_audio_scope(scope)
        if not key:
            return set()
        return set(self._audio_track_mutes_by_scope.get(key, set()))

    def _set_audio_track_controls(
        self,
        scope: Optional[str],
        tracks: List[Tuple[str, str]],
    ) -> None:
        normalized_scope = self._normalize_audio_scope(scope)
        self._current_audio_track_scope = normalized_scope
        self._current_audio_track_defs = list(tracks or [])
        muted_ids: Set[str] = set()
        if normalized_scope:
            muted_ids = self._get_muted_tracks_for_scope(normalized_scope)
        track_pairs: List[Tuple[str, str]] = []
        for track_id, label in self._current_audio_track_defs:
            normalized_id = self._normalize_audio_track_id(track_id)
            if not normalized_id:
                continue
            track_pairs.append((normalized_id, label or track_id))
        context_label = self.current_animation_name if normalized_scope else None
        self.control_panel.set_audio_track_options(
            track_pairs,
            muted_track_ids=muted_ids,
            context_label=context_label,
        )

    def _all_tracks_muted_for_scope(
        self,
        scope: Optional[str],
        tracks: List[Tuple[str, str]],
    ) -> bool:
        normalized_scope = self._normalize_audio_scope(scope)
        if not normalized_scope or not tracks:
            return False
        muted = self._get_muted_tracks_for_scope(normalized_scope)
        if not muted:
            return False
        normalized_tracks = {
            self._normalize_audio_track_id(track_id)
            for track_id, _label in tracks
            if track_id
        }
        if not normalized_tracks:
            return False
        return normalized_tracks.issubset(muted)

    def _reset_audio_loop_state(self) -> None:
        """Reset audio loop alignment tracking."""
        self._audio_loop_multiplier = 1
        self._audio_loop_index = 0

    @staticmethod
    def _detect_audio_loop_multiplier(ratio: float) -> int:
        """Return the loop multiplier when audio spans multiple animation cycles."""
        if not math.isfinite(ratio) or ratio < 1.5:
            return 1
        candidate = int(round(ratio))
        if candidate < 2 or candidate > 4:
            return 1
        if abs(ratio - candidate) <= 0.12:
            return candidate
        return 1

    def _get_audio_sync_time(self, animation_time: float) -> float:
        """Map animation time to audio time, accounting for multi-loop audio."""
        if not self.audio_manager.is_ready:
            return animation_time
        if self._audio_loop_multiplier <= 1:
            return animation_time
        player = getattr(self.gl_widget, "player", None)
        anim_duration = float(getattr(player, "duration", 0.0) or 0.0)
        if anim_duration <= 1e-6:
            return animation_time
        offset = self._audio_loop_index * anim_duration
        audio_time = animation_time + offset
        audio_duration = float(getattr(self.audio_manager, "active_duration", 0.0) or 0.0)
        if audio_duration <= 0.0:
            audio_duration = self.audio_manager.duration
        return max(0.0, min(audio_time, audio_duration))

    def _update_animation_time_scale(self) -> None:
        """Scale muppet animation timing so it matches the loaded audio length."""
        target_scale = 1.0
        if self.audio_manager.is_ready and self._should_scale_animation_time():
            player = getattr(self.gl_widget, "player", None)
            if player and player.duration > 1e-3:
                audio_duration = float(getattr(self.audio_manager, "active_duration", 0.0) or 0.0)
                if audio_duration > 1e-3:
                    ratio = audio_duration / player.duration
                    loop_multiplier = self._detect_audio_loop_multiplier(ratio)
                    target_scale = max(
                        0.25,
                        min(4.0, (player.duration * loop_multiplier) / audio_duration),
                    )
                    self._audio_loop_multiplier = loop_multiplier
                    self._audio_loop_index = 0
        else:
            self._reset_audio_loop_state()

        if abs(target_scale - self.animation_time_scale) < 1e-4:
            return

        self.animation_time_scale = target_scale
        self._apply_playback_speed()
        if self._audio_loop_multiplier > 1:
            self.log_widget.log(
                f"Audio spans {self._audio_loop_multiplier} loops; "
                f"scaled animation tempo {target_scale:.3f}x to align.",
                "INFO",
            )
        elif abs(target_scale - 1.0) > 1e-3:
            self.log_widget.log(
                f"Scaled animation tempo {target_scale:.3f}x to match muppet audio length.",
                "INFO",
            )

    def _is_muppet_payload(self) -> bool:
        """Return True when the current JSON belongs to the Muppets build."""
        if not self.current_json_path:
            return False
        basename = os.path.basename(self.current_json_path).lower()
        if basename.startswith("muppet_"):
            return True
        normalized = os.path.normcase(os.path.normpath(self.current_json_path))
        return "my muppets show.app" in normalized

    def _is_oldest_payload(self) -> bool:
        """Return True when the current JSON belongs to the launch build."""
        if not self.current_json_path:
            return False
        normalized = os.path.normcase(os.path.normpath(self.current_json_path))
        return "my singing monsters oldest.app" in normalized

    def _should_scale_animation_time(self) -> bool:
        """Heuristic to decide if animation scaling is warranted."""
        if not self._is_muppet_payload():
            return False
        if not self.current_audio_from_manifest:
            return False
        return True

    def _configure_animation_bpm(self):
        """Detect and apply BPM for the current animation."""
        detected = self._detect_bpm_for_current_animation()
        token = self._current_monster_token()
        token_key = token.lower() if token else None
        override_base = self.monster_base_bpm_overrides.get(token_key) if token_key else None
        if override_base:
            self.current_base_bpm = float(override_base)
            if token:
                self.log_widget.log(
                    f"Using locked BPM {self.current_base_bpm:.1f} for {token}.",
                    "INFO",
                )
        elif detected:
            self.current_base_bpm = detected
            self.log_widget.log(f"Detected BPM {detected:.1f}", "INFO")
        else:
            self.current_base_bpm = 120.0
            self.log_widget.log("BPM detection failed, defaulting to 120", "WARNING")

        initial_value = self.current_base_bpm
        if self.current_animation_name:
            initial_value = self.animation_bpm_overrides.get(self.current_animation_name, initial_value)
        if initial_value is None or initial_value <= 0.0:
            initial_value = 120.0
            self.log_widget.log("Falling back to default BPM 120.", "INFO")
        self._set_current_bpm(initial_value, update_ui=True, store_override=False)

    def _update_audio_speed(self):
        """Sync audio playback speed with current BPM settings."""
        if not self.audio_manager.is_ready:
            return
        if self.sync_audio_to_bpm:
            speed = self.current_bpm / max(1e-3, self.current_base_bpm)
        else:
            speed = 1.0

        if not self.sync_audio_to_bpm or abs(speed - 1.0) < 1e-3 or not self.pitch_shift_enabled:
            pitch_mode = "time_stretch"
        else:
            pitch_mode = "chipmunk" if self.chipmunk_mode else "pitch_shift"

        self._start_hang_watchdog("update_audio_speed", timeout=12.0)
        self.audio_manager.configure_playback(speed, pitch_mode)
        self._stop_hang_watchdog()

    def _get_export_playback_speed(self) -> float:
        """Return playback multiplier used for exports (mirrors UI BPM)."""
        player = getattr(self.gl_widget, "player", None)
        speed = getattr(player, "playback_speed", 1.0) if player else 1.0
        if speed <= 1e-3:
            base = max(1e-3, self.current_base_bpm)
            speed = self.current_bpm / base if self.current_bpm > 0 else 1.0
        return max(1e-3, float(speed))

    def _get_export_real_duration(self) -> float:
        """Return animation duration adjusted for the export playback speed."""
        player = getattr(self.gl_widget, "player", None)
        duration = getattr(player, "duration", 0.0) if player else 0.0
        speed = self._get_export_playback_speed()
        return duration / speed if speed > 1e-6 else duration

    def _get_export_frame_time(self, frame_index: int, fps: float) -> float:
        """Map the export frame index to the underlying animation time."""
        player = getattr(self.gl_widget, "player", None)
        if not player or fps <= 0:
            return 0.0
        base_duration = getattr(player, "duration", 0.0) or 0.0
        video_time = frame_index / float(fps)
        animation_time = video_time * self._get_export_playback_speed()
        if base_duration <= 0:
            return animation_time
        return min(base_duration, animation_time)

    def _get_audio_export_config(self) -> Tuple[float, str]:
        """Return (speed, pitch_mode) to mirror audio playback settings for exports."""
        if self.sync_audio_to_bpm:
            speed = self.current_bpm / max(1e-3, self.current_base_bpm)
        else:
            speed = 1.0
        if not self.sync_audio_to_bpm or abs(speed - 1.0) < 1e-3 or not self.pitch_shift_enabled:
            pitch_mode = "time_stretch"
        else:
            pitch_mode = "chipmunk" if self.chipmunk_mode else "pitch_shift"
        return speed, pitch_mode

    def _start_hang_watchdog(self, label: str, timeout: float = 12.0):
        """Arm faulthandler watchdog to print stack traces if we hang."""
        if self._hang_watchdog_active:
            return
        try:
            faulthandler.dump_traceback_later(timeout, repeat=True)
            self._hang_watchdog_active = True
            print(f"[WATCHDOG] Armed for {label} ({timeout}s)")
        except Exception as exc:  # pragma: no cover
            print(f"[WATCHDOG] Failed to arm for {label}: {exc}")

    def _stop_hang_watchdog(self):
        """Disarm hang watchdog if active."""
        if not self._hang_watchdog_active:
            return
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception as exc:  # pragma: no cover
            print(f"[WATCHDOG] Failed to cancel: {exc}")
        finally:
            self._hang_watchdog_active = False

    def _detect_bpm_for_current_animation(self) -> Optional[float]:
        """Return BPM derived from the island MIDI file, if any."""
        midi_path = self._resolve_midi_path_for_current_animation()
        if not midi_path:
            return None
        return self._read_midi_bpm(midi_path)

    def _resolve_midi_path_for_current_animation(self) -> Optional[str]:
        """Find the MIDI file associated with the current island."""
        candidate_dirs: List[str] = []
        if self.downloads_path:
            candidate_dirs.extend(
                [
                    os.path.join(self.downloads_path, "audio", "music"),
                    os.path.join(self.downloads_path, "data", "audio", "music"),
                ]
            )
        if self.game_path:
            candidate_dirs.append(os.path.join(self.game_path, "data", "audio", "music"))
        candidate_dirs = [d for d in candidate_dirs if os.path.isdir(d)]
        if not candidate_dirs:
            return None
        for code in self._build_island_code_candidates():
            if code is None:
                continue
            for music_dir in candidate_dirs:
                midi_path = self._find_midi_for_code(music_dir, code)
                if midi_path:
                    return midi_path
        return None

    def _build_island_code_candidates(self) -> List[Optional[int]]:
        """Compile likely numeric prefixes for MIDI lookup."""
        candidates: List[Optional[int]] = []
        raw_code: Optional[int] = None
        if self.current_audio_path:
            raw_code = self._extract_numeric_prefix(os.path.basename(self.current_audio_path))
        if raw_code is None and self.current_animation_name:
            raw_code = self._extract_numeric_prefix(os.path.basename(self.current_animation_name))

        if raw_code is not None:
            candidates.append(raw_code)
            if raw_code >= 100:
                base_code = raw_code % 100
                if base_code != raw_code:
                    candidates.append(base_code)
        if not candidates:
            candidates.append(None)
        return candidates

    @staticmethod
    def _extract_numeric_prefix(value: str) -> Optional[int]:
        """Return the leading integer from a string (or world### pattern)."""
        if not value:
            return None
        match = re.match(r'^(\d+)', value)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        match = re.search(r'world\s*([0-9]+)', value, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    @staticmethod
    def _find_midi_for_code(music_dir: str, code: int) -> Optional[str]:
        """Check common filename variants for island/stage MIDI files."""
        names: Set[str] = set()
        world_variants = ("world", "World", "WORLD")
        stage_variants = ("stage", "Stage", "STAGE")
        for variant in world_variants:
            names.add(f"{variant}{code}")
            names.add(f"{variant}{code:02d}")
            names.add(f"{variant}{code:03d}")
        for variant in stage_variants:
            names.add(f"{variant}{code}")
            names.add(f"{variant}{code:02d}")
        for name in names:
            for ext in (".mid", ".midi"):
                midi_path = os.path.join(music_dir, f"{name}{ext}")
                if os.path.exists(midi_path):
                    return midi_path
        return None

    @staticmethod
    def _read_midi_bpm(midi_path: str) -> Optional[float]:
        """Parse a MIDI file and return the first tempo event's BPM."""
        try:
            with open(midi_path, 'rb') as f:
                data = f.read()
        except OSError:
            return None
        if len(data) < 14 or data[0:4] != b'MThd':
            return None
        header_len = int.from_bytes(data[4:8], 'big')
        offset = 8 + header_len
        if offset > len(data):
            return None

        def read_varlen(chunk: bytes, idx: int) -> Tuple[int, int]:
            value = 0
            while idx < len(chunk):
                byte = chunk[idx]
                idx += 1
                value = (value << 7) | (byte & 0x7F)
                if not (byte & 0x80):
                    break
            return value, idx

        while offset + 8 <= len(data):
            if data[offset:offset + 4] != b'MTrk':
                # Unknown chunk
                chunk_len = int.from_bytes(data[offset + 4:offset + 8], 'big')
                offset += 8 + chunk_len
                continue

            track_len = int.from_bytes(data[offset + 4:offset + 8], 'big')
            offset += 8
            track_data = data[offset:offset + track_len]
            offset += track_len

            idx = 0
            running_status = None
            while idx < len(track_data):
                delta, idx = read_varlen(track_data, idx)
                if idx >= len(track_data):
                    break
                status = track_data[idx]
                if status >= 0x80:
                    idx += 1
                    running_status = status
                else:
                    status = running_status
                if status is None:
                    break

                if status == 0xFF:
                    if idx >= len(track_data):
                        break
                    meta_type = track_data[idx]
                    idx += 1
                    length, idx = read_varlen(track_data, idx)
                    meta_data = track_data[idx:idx + length]
                    idx += length
                    if meta_type == 0x51 and length == 3:
                        tempo_micro = int.from_bytes(meta_data, 'big')
                        if tempo_micro > 0:
                            return 60000000.0 / tempo_micro
                        return None
                elif status in (0xF0, 0xF7):
                    length, idx = read_varlen(track_data, idx)
                    idx += length
                else:
                    event_type = status & 0xF0
                    if event_type in (0xC0, 0xD0):
                        idx += 1
                    else:
                        idx += 2
        return None
    
    def on_animation_selected(self, index: int):
        """Handle animation selection"""
        if index < 0 or not self.current_json_data:
            return
        current_anim = getattr(self.gl_widget.player, "animation", None)
        if index == self.current_animation_index and current_anim is not None:
            return
        self._persist_current_animation_edits()
        self.current_animation_index = index
        self.load_animation(index)

    def on_costume_selected(self, index: int):
        """Handle costume dropdown changes."""
        if not self.gl_widget.player.animation:
            return
        combo = self.control_panel.costume_combo
        if index < 0 or index >= combo.count():
            return
        key = combo.itemData(index)
        if not key:
            self.control_panel.set_costume_convert_enabled(False)
            self._apply_legacy_sheet_variant(None)
            if self.active_costume_key is not None:
                self.log_widget.log("Reverted to base appearance", "INFO")
                self._apply_costume_to_animation(None)
            return
        entry = self.costume_entry_map.get(key)
        if not entry:
            self.log_widget.log("Selected costume is unavailable, refreshing list...", "WARNING")
            self._refresh_costume_list()
            entry = self.costume_entry_map.get(key)
            if not entry:
                self.log_widget.log("Unable to resolve costume selection.", "ERROR")
                return
            was_blocked = combo.blockSignals(True)
            for idx in range(combo.count()):
                if combo.itemData(idx) == entry.key:
                    combo.setCurrentIndex(idx)
                    break
            combo.blockSignals(was_blocked)
        self.control_panel.set_costume_convert_enabled(bool(entry.bin_path))
        if entry.legacy_sheet_path:
            if self.active_costume_key is not None:
                self._apply_costume_to_animation(None)
            self._apply_legacy_sheet_variant(entry)
            return
        self._apply_legacy_sheet_variant(None)
        if self.active_costume_key == entry.key:
            return
        self.log_widget.log(f"Applying costume: {entry.display_name}", "INFO")
        self._apply_costume_to_animation(entry)

    def load_animation(self, anim_index: int):
        """Load and display an animation"""
        if not self.current_json_data or 'anims' not in self.current_json_data:
            return

        preserved_costume_entry: Optional[CostumeEntry] = None
        previous_costume_key = self.active_costume_key
        self.layer_source_lookup = {}

        self.control_panel.set_pose_controls_enabled(False)
        self.gl_widget.reset_layer_offsets()
        self.update_offset_display()
        self._start_hang_watchdog("load_animation")
        try:
            self.current_animation_embedded_clones = None
            anim_data = self.current_json_data['anims'][anim_index]
            self.current_animation_embedded_clones = self._extract_embedded_clone_defs(anim_data)
            sources = self.current_json_data.get('sources', [])
            raw_layers = anim_data.get('layers', [])
            self.layer_source_lookup = {
                layer.get('id', idx): layer for idx, layer in enumerate(raw_layers)
            }
            
            self.log_widget.log(f"Loading animation: {anim_data['name']}", "INFO")
            self.current_animation_name = anim_data.get('name')
            
            json_dir = os.path.dirname(self.current_json_path) if self.current_json_path else None
            self.gl_widget.texture_atlases = self._load_texture_atlases_for_sources(
                sources,
                json_dir=json_dir,
                use_cache=False
            )
            self._rebuild_source_atlas_lookup(sources, self.gl_widget.texture_atlases)
            
            # Parse animation data
            blend_version = self.current_blend_version or 1
            animation = self._build_animation_struct(
                anim_data,
                blend_version,
                self.current_json_path,
                resource_dict=self.current_json_data
            )
            layers = animation.layers
            self._dump_mask_debug_raw_layers(raw_layers, animation)
            self._load_keyframe_layers_sidecar(self.current_json_path, animation)

            self.canonical_layer_names = set()
            # Ensure costume metadata (and any inferred clone aliases) are cached
            # before we attempt to seed canonical clones.
            self._refresh_costume_list()
            if previous_costume_key:
                preserved_costume_entry = self.costume_entry_map.get(previous_costume_key)
            self._apply_canonical_clones_to_base(layers)
            self._apply_cached_layer_visibility(layers)
            self.base_layer_cache = self._clone_layers(layers)
            self._configure_costume_shaders(None, None)

            self.gl_widget.player.load_animation(animation)
            self._dump_mask_debug_layer_layout(animation)
            self.gl_widget.invalidate_animation_cache()
            self._record_layer_defaults(animation.layers)
            self.gl_widget.set_layer_atlas_overrides({})
            self.gl_widget.set_layer_pivot_context({})
            self._reset_costume_runtime_state(animation.layers)
            self.base_texture_atlases = list(self.gl_widget.texture_atlases)
            self.costume_atlas_cache.clear()
            self.update_layer_panel()
            self.selected_layer_ids.clear()
            self.primary_selected_layer_id = None
            self.selection_lock_enabled = False
            self.layer_panel.set_selection_state(self.selected_layer_ids)
            self.apply_selection_state()
            self.control_panel.set_pose_controls_enabled(True)
            self.control_panel.set_sprite_tools_enabled(bool(layers))
            self._reset_edit_history()
            self.update_timeline()
            if self.current_animation_name:
                self.load_audio_for_animation(self.current_animation_name)
            self._configure_animation_bpm()
            tile_batches, terrain_composite = self._build_island_tile_batches(anim_data, animation)
            self.gl_widget.set_terrain_composite(terrain_composite)
            self.gl_widget.set_tile_batches(tile_batches)
            self._update_terrain_tile_index_range()

            # Reinitialize GL to load textures
            self.gl_widget.makeCurrent()
            self.gl_widget.initializeGL()
            self._restore_sprite_workshop_edits()
            self.gl_widget.doneCurrent()

            self._refresh_dof_particle_entries(animation)

            self.log_widget.log(f"Animation loaded successfully with {len(layers)} layers", "SUCCESS")
            self.gl_widget.set_anchor_logging_enabled(self.anchor_debug_enabled)
            if self.anchor_debug_enabled:
                # Schedule an anchor debug dump after the first frame to capture pivot math
                QTimer.singleShot(500, lambda: self._dump_anchor_debug())

            if preserved_costume_entry:
                self.log_widget.log(
                    f"Reapplying costume '{preserved_costume_entry.display_name}' to animation '{self.current_animation_name}'",
                    "INFO"
                )
                self._restore_costume_selection(preserved_costume_entry.key)
                self._apply_costume_to_animation(preserved_costume_entry)
            else:
                self._restore_costume_selection(None)
            self._refresh_timeline_keyframes()
            self._capture_pose_baseline()

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(tb)
            self.log_widget.log(f"Error loading animation: {e}", "ERROR")
            self.log_widget.log(tb, "ERROR")
            self.gl_widget.set_terrain_composite(None)
            self.gl_widget.set_tile_batches([])
            self._update_terrain_tile_index_range()
        finally:
            self._stop_hang_watchdog()

    def _mask_debug_enabled(self) -> bool:
        raw = os.environ.get("ANIVIEWER_MASK_DEBUG", "1").strip().lower()
        return raw not in {"0", "false", "off", "no"}

    def _mask_debug_target(self) -> str:
        return os.environ.get("ANIVIEWER_MASK_DEBUG_TARGET", "gjlm").strip().lower()

    def _should_emit_mask_debug_layout(self, animation: Optional[AnimationData]) -> bool:
        if not self._mask_debug_enabled():
            return False
        if animation is None:
            return False
        if any(getattr(layer, "mask_role", None) or getattr(layer, "mask_key", None) for layer in (animation.layers or [])):
            return True

        target = self._mask_debug_target()
        if not target:
            return True

        probes = [
            (self.current_json_path or "").lower(),
            (self.current_animation_name or "").lower(),
            (animation.name or "").lower(),
        ]
        if any(target in probe for probe in probes if probe):
            return True

        for layer in animation.layers or []:
            layer_name = (layer.name or "").lower()
            shader_name = (layer.shader_name or "").lower()
            if target in layer_name or target in shader_name:
                return True
            for keyframe in layer.keyframes[:2]:
                sprite_name = (keyframe.sprite_name or "").lower()
                if target in sprite_name:
                    return True
        return False

    def _dump_mask_debug_layer_layout(self, animation: Optional[AnimationData]) -> None:
        """
        Emit a one-shot runtime layer order + shader assignment dump for mask debugging.
        """
        if not self._should_emit_mask_debug_layout(animation):
            return
        if animation is None:
            return

        try:
            layer_world_states = self.gl_widget._build_layer_world_states_base(
                self.gl_widget.player.current_time,
                apply_global=True,
            )
            render_layers = self.gl_widget._get_render_layers(layer_world_states)
            src_index = {
                layer.layer_id: idx
                for idx, layer in enumerate(animation.layers or [])
            }
            print(
                "[MaskDebugLayout] "
                f"begin animation='{animation.name}' "
                f"layers={len(animation.layers or [])} render_layers={len(render_layers)}"
            )
            for draw_idx, layer in enumerate(render_layers, start=1):
                state = layer_world_states.get(layer.layer_id, {})
                sprite_name = str(state.get("sprite_name") or "-").replace("'", "\\'")
                shader_name = str(state.get("shader") or layer.shader_name or "-").replace("'", "\\'")
                layer_name = (layer.name or "").replace("'", "\\'")
                role = layer.mask_role or "-"
                key = layer.mask_key or "-"
                print(
                    "[MaskDebugLayout] "
                    f"draw_order={draw_idx} "
                    f"src_index={src_index.get(layer.layer_id, -1)} "
                    f"layer='{layer_name}' "
                    f"id={layer.layer_id} "
                    f"parent={layer.parent_id} "
                    f"sprite='{sprite_name}' "
                    f"shader='{shader_name}' "
                    f"blend={layer.blend_mode} "
                    f"mask_role={role} "
                    f"mask_key={key}"
                )
            print("[MaskDebugLayout] end")
        except Exception as exc:
            print(f"[MaskDebugLayout] failed: {exc}")

    def _dump_mask_debug_raw_layers(
        self,
        raw_layers: Optional[List[Dict[str, Any]]],
        animation: Optional[AnimationData],
    ) -> None:
        """
        Dump raw layer fields for mask-relevant layers to mine hidden metadata from BIN->JSON output.
        """
        if not self._should_emit_mask_debug_layout(animation):
            return
        if not raw_layers or animation is None:
            return

        try:
            mask_ids = {
                layer.layer_id
                for layer in (animation.layers or [])
                if getattr(layer, "mask_role", None) or getattr(layer, "mask_key", None)
            }
            if not mask_ids:
                return

            common_keys = {
                "name", "id", "parent", "anchor_x", "anchor_y", "blend", "visible",
                "shader", "keyframes", "color_tint", "color_tint_hdr", "color_gradient",
                "color_animator", "color_metadata", "render_tags", "mask_role", "mask_key",
                "sprite_anchor_map",
            }
            for payload in raw_layers:
                if not isinstance(payload, dict):
                    continue
                layer_id = payload.get("id")
                if layer_id not in mask_ids:
                    continue
                layer_name = str(payload.get("name") or "")
                shader_name = str(payload.get("shader") or "-")
                blend_value = payload.get("blend", "-")
                parent_id = payload.get("parent", "-")
                keyframe_count = len(payload.get("keyframes") or [])
                keys = sorted(payload.keys())
                extra_keys = [key for key in keys if key not in common_keys]
                print(
                    "[MaskDebugRaw] "
                    f"id={layer_id} name='{layer_name}' parent={parent_id} "
                    f"blend={blend_value} shader='{shader_name}' keyframes={keyframe_count} "
                    f"keys={keys}"
                )
                if extra_keys:
                    print(
                        "[MaskDebugRaw] "
                        f"id={layer_id} extra_keys={extra_keys}"
                    )
                    for key in extra_keys:
                        value = payload.get(key)
                        if isinstance(value, dict):
                            summary = f"dict(keys={sorted(value.keys())})"
                        elif isinstance(value, list):
                            summary = f"list(len={len(value)})"
                        else:
                            summary = repr(value)
                        print(f"[MaskDebugRaw] id={layer_id} {key}={summary}")
        except Exception as exc:
            print(f"[MaskDebugRaw] failed: {exc}")

    def _dump_anchor_debug(self, attempt: int = 0):
        """Attempt to dump renderer anchor logs; retry briefly if empty."""
        if not self.anchor_debug_enabled:
            return
        try:
            renderer = getattr(self.gl_widget, "renderer", None)
            if renderer is None:
                return
            if renderer.log_data:
                renderer.write_log_to_file("anchor_debug.txt")
                return
            if attempt >= 4:
                renderer.write_log_to_file("anchor_debug.txt")
                return
            QTimer.singleShot(500, lambda: self._dump_anchor_debug(attempt + 1))
        except Exception:
            return

    def load_audio_for_animation(self, animation_name: str):
        """Load and sync the audio clip that matches the selected animation."""
        if not animation_name:
            self._set_audio_track_controls(None, [])
            self.current_audio_path = None
            self.current_audio_from_manifest = False
            self.audio_manager.clear()
            self._handle_audio_cleared()
            self.control_panel.update_audio_status("Audio: not available", False)
            return
        if not self.game_path and not self.dof_path and not self.downloads_path:
            self._set_audio_track_controls(None, [])
            self.current_audio_path = None
            self.current_audio_from_manifest = False
            self.audio_manager.clear()
            self._handle_audio_cleared()
            self.control_panel.update_audio_status("Audio: not available", False)
            return
        self._start_hang_watchdog("load_audio")
        try:
            audio_path, from_manifest, audio_scope, audio_tracks = self._find_audio_for_animation(animation_name)
            self._set_audio_track_controls(audio_scope, audio_tracks)
            intentionally_muted = self._all_tracks_muted_for_scope(audio_scope, audio_tracks)

            if not audio_path:
                self.current_audio_path = None
                self.current_audio_from_manifest = False
                self.audio_manager.clear()
                self._handle_audio_cleared()
                if intentionally_muted:
                    self.control_panel.update_audio_status(f"{animation_name}: all tracks muted", False)
                    self.log_widget.log(
                        f"Audio muted for animation '{animation_name}' (all active tracks disabled).",
                        "INFO",
                    )
                else:
                    self.control_panel.update_audio_status(f"{animation_name}: missing", False)
                    self.log_widget.log(f"No audio clip found for animation '{animation_name}'", "WARNING")
                return

            self.current_audio_path = audio_path
            self.current_audio_from_manifest = from_manifest
            if self.audio_manager.load_file(audio_path):
                current_time = self.gl_widget.player.current_time
                self._update_animation_time_scale()
                self._update_audio_speed()
                audio_time = self._get_audio_sync_time(current_time)
                if self.gl_widget.player.playing:
                    self.audio_manager.play(audio_time)
                else:
                    self.audio_manager.seek(audio_time)
                rel_path = self._audio_display_path(audio_path)
                self.control_panel.update_audio_status(f"{animation_name} -> {rel_path}", True)
                self.log_widget.log(f"Loaded audio clip: {rel_path}", "SUCCESS")
            else:
                self.audio_manager.clear()
                self.current_audio_from_manifest = False
                self._handle_audio_cleared()
                self.control_panel.update_audio_status("Audio: failed to load", False)
                self.log_widget.log(f"Failed to load audio file: {audio_path}", "ERROR")
        finally:
            self._stop_hang_watchdog()

    def _find_audio_for_animation(
        self,
        animation_name: str,
        *,
        force_dof: Optional[bool] = None,
    ) -> Tuple[Optional[str], bool, Optional[str], List[Tuple[str, str]]]:
        """
        Return an absolute path to the audio clip for a given animation name along with
        a flag indicating whether it came from the buddy manifest, plus optional
        multi-track metadata (scope + track list).
        """
        if not animation_name:
            return None, False, None, []

        is_dof = self._is_active_dof_audio_context() if force_dof is None else bool(force_dof)

        if not is_dof:
            if self._should_use_monster_f_activate_sfx(animation_name):
                sfx_path = self._resolve_sfx_clip_path("box_monster_open")
                if sfx_path:
                    return sfx_path, False, None, []
                # Do not fall through into generic music matching for this case.
                return None, False, None, []

            x15_audio, x15_scope, x15_tracks = self._resolve_x15_special_audio(animation_name)
            if x15_audio or x15_scope:
                return x15_audio, False, x15_scope, x15_tracks

            x16_audio, x16_scope, x16_tracks = self._resolve_x16_special_audio(animation_name)
            if x16_audio or x16_scope:
                return x16_audio, False, x16_scope, x16_tracks

            x18_audio, x18_scope, x18_tracks = self._resolve_x18_special_audio(animation_name)
            if x18_audio:
                return x18_audio, False, x18_scope, x18_tracks
            if x18_scope:
                return None, False, x18_scope, x18_tracks

            if self._should_skip_non_dof_audio(animation_name):
                return None, False, None, []

        buddy_override = self._lookup_buddy_audio(animation_name)
        if buddy_override and not is_dof:
            return buddy_override, True, None, []
        buddy_blocked = self._is_buddy_audio_blocked(animation_name) and not is_dof

        if self._is_muppet_payload():
            return None, False, None, []

        monster_token = self._current_monster_token()
        dof_asset_name: Optional[str] = None
        if is_dof and self.current_json_path:
            dof_asset_name = self._dof_anim_name_from_path(self.current_json_path)
        if is_dof and self._should_skip_dof_audio(animation_name, dof_asset_name):
            return None, False, None, []
        dof_token_source = dof_asset_name or animation_name
        if is_dof:
            dof_token = self._derive_dof_audio_token(dof_token_source)
            if dof_token:
                monster_token = dof_token
        audio_base = dof_asset_name or animation_name
        raw_candidates = self._build_audio_name_candidates(
            audio_base,
            monster_token=monster_token
        )
        if is_dof and not dof_asset_name:
            dof_token = self._derive_dof_audio_token(animation_name)
            if dof_token:
                raw_candidates.extend(
                    self._build_audio_name_candidates(animation_name, monster_token=dof_token)
                )

        normalized_keys: List[str] = []
        for candidate in raw_candidates:
            normalized = self._normalize_audio_key(candidate)
            if normalized:
                normalized_keys.extend(self._expand_audio_key_variants(normalized))

        if is_dof:
            bundle_lookup_name = dof_asset_name or animation_name
            bundle_audio = self._find_dof_bundle_audio_for_animation(
                animation_name,
                normalized_keys=normalized_keys,
                monster_token=monster_token,
                anim_source_name=dof_asset_name,
                bundle_path=self._resolve_dof_bundle_for_current_animation(bundle_lookup_name),
            )
            if bundle_audio:
                return bundle_audio, False, None, []
            # DOF audio must come from the bundle; do not fall back to other sources.
            return None, False, None, []

        music_dirs: List[str] = []
        libraries: List[Tuple[str, Dict[str, List[str]]]] = []
        if is_dof:
            if self._dof_music_dirs:
                music_dirs.extend(self._dof_music_dirs)
            if self.dof_audio_library:
                libraries.append(("DOF", self.dof_audio_library))
        else:
            if self._game_music_dirs:
                music_dirs.extend(self._game_music_dirs)
            if self.audio_library:
                libraries.append(("Game", self.audio_library))
            if self._dof_music_dirs:
                music_dirs.extend(self._dof_music_dirs)
            if self.dof_audio_library:
                libraries.append(("DOF", self.dof_audio_library))
        if not music_dirs and self.game_path:
            fallback_game_music = os.path.join(self.game_path, "data", "audio", "music")
            if os.path.isdir(fallback_game_music):
                music_dirs.append(fallback_game_music)
        if not music_dirs and self.downloads_path:
            fallback_downloads_music = os.path.join(self.downloads_path, "audio", "music")
            if os.path.isdir(fallback_downloads_music):
                music_dirs.append(fallback_downloads_music)
            fallback_downloads_music = os.path.join(self.downloads_path, "data", "audio", "music")
            if os.path.isdir(fallback_downloads_music):
                music_dirs.append(fallback_downloads_music)

        extensions = ['.ogg', '.wav', '.mp3']
        memory_candidates: List[str] = []
        if not is_dof:
            # Buddy manifests sometimes intentionally omit explicit audio paths for
            # track names; prefer token-memory clips before generic matching.
            memory_candidates.extend(self._build_buddy_memory_candidates(animation_name))
        seen_memory: Set[str] = set()
        for candidate in memory_candidates:
            if not candidate or candidate in seen_memory:
                continue
            seen_memory.add(candidate)
            base = os.path.splitext(candidate)[0]
            for music_dir in music_dirs:
                for ext in extensions:
                    check_path = os.path.normpath(os.path.join(music_dir, base + ext))
                    if os.path.exists(check_path):
                        return check_path, False, None, []
        if buddy_blocked:
            self.log_widget.log(
                f"Buddy manifest marks '{animation_name}' as having no direct clip; no memory fallback found.",
                "WARNING",
            )
            return None, False, None, []
        for candidate in raw_candidates:
            base = os.path.splitext(candidate)[0]
            for music_dir in music_dirs:
                for ext in extensions:
                    check_path = os.path.normpath(os.path.join(music_dir, base + ext))
                    if os.path.exists(check_path):
                        return check_path, False, None, []

        if not libraries and not is_dof:
            return None, False, None, []

        seen_keys: Set[str] = set()
        for key in normalized_keys:
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            for _label, library in libraries:
                paths = library.get(key)
                if paths:
                    return paths[0], False, None, []

        if self._should_attempt_fuzzy_audio(animation_name):
            if buddy_blocked:
                return None, False, None, []
            for label, library in libraries:
                fallback_path = self._fuzzy_audio_library_lookup(
                    normalized_keys,
                    monster_token=monster_token,
                    animation_name=animation_name,
                    library=library,
                    source_label=label,
                )
                if fallback_path:
                    return fallback_path, False, None, []
        if is_dof:
            # If we're in DOF mode and no bundle was found earlier, do not
            # auto-fallback to other libraries; wrong audio is worse than no audio.
            return None, False, None, []
        return None, False, None, []

    def _build_buddy_memory_candidates(self, animation_name: str) -> List[str]:
        """
        Build memory-track audio candidates for buddy manifests that list tracks
        but omit explicit sample paths (for example, 018_SOUL_* / X18 tracks).
        """
        normalized = self._normalize_audio_key(animation_name)
        if not normalized:
            return []
        token = ""
        monster_match = re.match(r"^(?:\d{2}_)?([a-z0-9_]+)_monster_\d+$", normalized)
        if monster_match:
            token = monster_match.group(1)
        else:
            dance_match = re.match(r"^(?:\d{2}_)?([a-z0-9_]+)_dance_\d+$", normalized)
            if dance_match:
                token = dance_match.group(1)
        if not token:
            return []
        # Keep this narrow: buddy-blocked tracks should resolve to token memory.
        return [f"{token}-Memory", f"{token}_Memory"]

    def _resolve_music_clip_path(self, clip_stem: str, music_dirs: List[str]) -> Optional[str]:
        """Resolve a clip basename (without extension) across indexed music roots."""
        if not clip_stem:
            return None
        base = os.path.splitext(os.path.basename(clip_stem))[0]
        extensions = (".ogg", ".wav", ".mp3")
        for music_dir in music_dirs:
            for ext in extensions:
                candidate = os.path.normpath(os.path.join(music_dir, base + ext))
                if os.path.exists(candidate):
                    return candidate
        normalized = self._normalize_audio_key(base)
        if not normalized:
            return None
        paths = self.audio_library.get(normalized) or []
        if paths:
            return paths[0]
        return None

    def _resolve_world_midi_path(self, world_number: int, music_dirs: List[str]) -> Optional[str]:
        filename = f"world{int(world_number)}.mid"
        for music_dir in music_dirs:
            candidate = os.path.normpath(os.path.join(music_dir, filename))
            if os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def _resample_audio_linear(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if src_rate <= 0 or dst_rate <= 0 or src_rate == dst_rate:
            return audio
        if audio.size == 0:
            return audio
        src_len = int(audio.shape[0])
        dst_len = max(1, int(round(src_len * (float(dst_rate) / float(src_rate)))))
        if src_len == 1:
            return np.repeat(audio, dst_len, axis=0)
        positions = np.linspace(0, src_len - 1, dst_len, dtype=np.float64)
        idx0 = np.floor(positions).astype(np.int64)
        idx1 = np.minimum(idx0 + 1, src_len - 1)
        frac = (positions - idx0)[:, None].astype(np.float32)
        out = audio[idx0] * (1.0 - frac) + audio[idx1] * frac
        return np.ascontiguousarray(out, dtype=np.float32)

    @staticmethod
    def _midi_tick_to_seconds(tick: int, ticks_per_beat: int, tempo_points: List[Tuple[int, int]]) -> float:
        if tick <= 0:
            return 0.0
        tpb = max(1, int(ticks_per_beat))
        points = sorted((max(0, int(t)), max(1, int(micro))) for t, micro in tempo_points)
        if not points or points[0][0] != 0:
            points.insert(0, (0, 500000))

        elapsed = 0.0
        prev_tick = 0
        current_micro = points[0][1]
        for point_tick, point_micro in points[1:]:
            if point_tick <= prev_tick:
                current_micro = point_micro
                continue
            if tick <= point_tick:
                elapsed += ((tick - prev_tick) / float(tpb)) * (current_micro / 1_000_000.0)
                return elapsed
            elapsed += ((point_tick - prev_tick) / float(tpb)) * (current_micro / 1_000_000.0)
            prev_tick = point_tick
            current_micro = point_micro
        if tick > prev_tick:
            elapsed += ((tick - prev_tick) / float(tpb)) * (current_micro / 1_000_000.0)
        return elapsed

    @staticmethod
    def _format_audio_track_label(track_id: str) -> str:
        cleaned = str(track_id or "").replace("_", " ").replace("-", " ").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.title() if cleaned else str(track_id or "")

    def _resolve_special_music_dirs(self) -> List[str]:
        music_dirs = list(self._game_music_dirs)
        if not music_dirs:
            # Fallback probing when the library hasn't indexed yet.
            for root in (
                os.path.join(self.downloads_path, "audio", "music") if self.downloads_path else "",
                os.path.join(self.downloads_path, "data", "audio", "music") if self.downloads_path else "",
                os.path.join(self.game_path, "data", "audio", "music") if self.game_path else "",
            ):
                if root and os.path.isdir(root) and root not in music_dirs:
                    music_dirs.append(root)
        return music_dirs

    def _resolve_x15_special_audio(
        self,
        animation_name: str,
    ) -> Tuple[Optional[str], Optional[str], List[Tuple[str, str]]]:
        normalized = self._normalize_audio_key(animation_name)
        if not normalized:
            return None, None, []
        match = re.match(r"^15_x15_monster_(\d+)$", normalized)
        if not match:
            return None, None, []

        monster_idx = int(match.group(1))
        music_dirs = self._resolve_special_music_dirs()
        if not music_dirs:
            return None, None, []

        tracks_by_idx: Dict[int, List[str]] = {
            1: ["15-prop_meteors_01"],
            2: ["15-prop_tentacles_01"],
            3: ["15-prop_eyes_01"],
            4: [
                "15-prop_meteors_01",
                "15-prop_eyes_01",
                "15-prop_tentacles_02",
            ],
        }
        clip_stems = tracks_by_idx.get(monster_idx)
        if not clip_stems:
            return None, None, []

        scope = f"15_x15_monster_{monster_idx:02d}"
        tracks = [(stem, self._format_audio_track_label(stem)) for stem in clip_stems]
        muted = self._get_muted_tracks_for_scope(scope)

        if len(clip_stems) == 1:
            clip_stem = clip_stems[0]
            if self._normalize_audio_track_id(clip_stem) in muted:
                return None, scope, tracks
            return self._resolve_music_clip_path(clip_stem, music_dirs), scope, tracks

        mix_path = self._build_layered_clip_mix_audio(
            cache_prefix=f"x15_monster_{monster_idx:02d}",
            clip_stems=clip_stems,
            music_dirs=music_dirs,
            muted_stems=muted,
        )
        return mix_path, scope, tracks

    def _resolve_x16_special_audio(
        self,
        animation_name: str,
    ) -> Tuple[Optional[str], Optional[str], List[Tuple[str, str]]]:
        normalized = self._normalize_audio_key(animation_name)
        if not normalized:
            return None, None, []
        match = re.match(r"^16_x16_monster_(\d+)$", normalized)
        if not match:
            return None, None, []

        monster_idx = int(match.group(1))
        music_dirs = self._resolve_special_music_dirs()
        if not music_dirs:
            return None, None, []

        stems_by_idx: Dict[int, str] = {
            1: "16-prop_earclouds_01",
            2: "16-prop_facevines_01",
            3: "16-prop_mushrooms_01",
            4: "16-prop_facevines_01",
        }
        clip_stem = stems_by_idx.get(monster_idx)
        if not clip_stem:
            return None, None, []

        scope = f"16_x16_monster_{monster_idx:02d}"
        tracks = [(clip_stem, self._format_audio_track_label(clip_stem))]
        muted = self._get_muted_tracks_for_scope(scope)
        if self._normalize_audio_track_id(clip_stem) in muted:
            return None, scope, tracks
        return self._resolve_music_clip_path(clip_stem, music_dirs), scope, tracks

    def _resolve_x18_special_audio(
        self,
        animation_name: str,
    ) -> Tuple[Optional[str], Optional[str], List[Tuple[str, str]]]:
        """
        Special-case world 18/X18 props:
        - 18-X18_Monster_01 -> aurora clip
        - 18-X18_Monster_02 -> fireflies clip
        - 18-X18_Monster_03/04 -> timed MIDI-driven clip mix from world18.mid
        """
        normalized = self._normalize_audio_key(animation_name)
        if not normalized:
            return None, None, []
        match = re.match(r"^18_x18_monster_(\d+)$", normalized)
        if not match:
            return None, None, []

        monster_idx = int(match.group(1))
        music_dirs = self._resolve_special_music_dirs()
        if not music_dirs:
            return None, None, []

        scope = f"18_x18_monster_{monster_idx:02d}"
        muted = self._get_muted_tracks_for_scope(scope)

        if monster_idx == 1:
            stems = ["18-prop_aurora_01"]
            tracks = [(stem, self._format_audio_track_label(stem)) for stem in stems]
            if self._normalize_audio_track_id(stems[0]) in muted:
                return None, scope, tracks
            return self._resolve_music_clip_path(stems[0], music_dirs), scope, tracks

        if monster_idx == 2:
            stems = ["18-prop_fireflies_01"]
            tracks = [(stem, self._format_audio_track_label(stem)) for stem in stems]
            if self._normalize_audio_track_id(stems[0]) in muted:
                return None, scope, tracks
            return self._resolve_music_clip_path(stems[0], music_dirs), scope, tracks

        if monster_idx in (3, 4):
            source_tracks = self._x18_source_track_map(monster_idx)
            stems = sorted({stem for stems_group in source_tracks.values() for stem in stems_group})
            tracks = [(stem, self._format_audio_track_label(stem)) for stem in stems]
            mix_path = self._build_x18_timed_mix_audio(
                monster_idx,
                music_dirs,
                muted_stems=muted,
            )
            return mix_path, scope, tracks
        return None, None, []

    def _x18_source_track_map(self, monster_idx: int) -> Dict[str, Tuple[str, ...]]:
        if monster_idx == 3:
            return {
                "prop_aurora_solo_Monster": ("18-prop_aurora_01",),
                "prop_fireflies_solo_Monster": ("18-prop_fireflies_01",),
                "prop_lamp_01_Monster": ("18-prop_lamp_9", "18-prop_lamp_10"),
                "prop_lamp_02_Monster": ("18-prop_lamp_15", "18-prop_lamp_17"),
                "prop_lamp_03_Monster": ("18-prop_lamp_21", "18-prop_lamp_22"),
                "prop_lamp_04_Monster": ("18-prop_lamp_27", "18-prop_lamp_29"),
                "prop_lamp_05_Monster": ("18-prop_lamp_33", "18-prop_lamp_34"),
            }
        if monster_idx == 4:
            return {
                "prop_aurora_finale_Monster": ("18-prop_aurora_01",),
                "prop_fireflies_finale_Monster": ("18-prop_fireflies_01",),
                "prop_lamp_finale_01_Monster": ("18-prop_lamp_9", "18-prop_lamp_10"),
                "prop_lamp_finale_02_Monster": ("18-prop_lamp_15", "18-prop_lamp_17"),
                "prop_lamp_finale_03_Monster": ("18-prop_lamp_21", "18-prop_lamp_22"),
                "prop_lamp_finale_04_Monster": ("18-prop_lamp_27", "18-prop_lamp_29"),
                "prop_lamp_finale_05_Monster": ("18-prop_lamp_33", "18-prop_lamp_34"),
            }
        return {}

    def _build_x18_timed_mix_audio(
        self,
        monster_idx: int,
        music_dirs: List[str],
        *,
        muted_stems: Optional[Set[str]] = None,
    ) -> Optional[str]:
        midi_path = self._resolve_world_midi_path(18, music_dirs)
        if not midi_path or not os.path.exists(midi_path):
            self.log_widget.log("X18 audio: world18.mid not found.", "WARNING")
            return None

        muted = {self._normalize_audio_track_id(stem) for stem in (muted_stems or set()) if stem}

        if monster_idx == 3:
            control_track_name = "X18_level3_Animation"
            control_note = 62
            source_tracks = self._x18_source_track_map(monster_idx)
        elif monster_idx == 4:
            control_track_name = "X18_level4_Animation"
            control_note = 63
            source_tracks = self._x18_source_track_map(monster_idx)
        else:
            return None

        try:
            midi = read_midi_file(midi_path)
        except Exception as exc:
            self.log_widget.log(f"X18 audio: failed to parse world18.mid ({exc})", "WARNING")
            return None

        tracks_by_name = {
            (track.name or "").strip().lower(): track
            for track in midi.tracks
            if (track.name or "").strip()
        }
        control_track = tracks_by_name.get(control_track_name.lower())
        if not control_track or not control_track.notes:
            self.log_widget.log(
                f"X18 audio: control track '{control_track_name}' not found in world18.mid.",
                "WARNING",
            )
            return None

        active_windows: List[Tuple[int, int]] = []
        for note in control_track.notes:
            if int(note.note) != int(control_note):
                continue
            start_tick = int(note.start_tick)
            end_tick = int(note.end_tick)
            if end_tick > start_tick:
                active_windows.append((start_tick, end_tick))
        if not active_windows:
            self.log_widget.log(
                f"X18 audio: no active windows for {control_track_name} note {control_note}.",
                "WARNING",
            )
            return None

        window_start_tick = min(start for start, _ in active_windows)
        window_end_tick = max(end for _, end in active_windows)

        def tick_in_windows(tick: int) -> bool:
            for start, end in active_windows:
                if start <= tick < end:
                    return True
            return False

        tempo_points: List[Tuple[int, int]] = []
        for track in midi.tracks:
            for event in track.events:
                if event.kind == "meta" and int(event.status) == 0x51 and len(event.data) == 3:
                    tempo_micro = int.from_bytes(event.data, "big")
                    if tempo_micro > 0:
                        tempo_points.append((int(event.tick), tempo_micro))
        if not tempo_points:
            tempo_points = [(0, 500000)]

        source_events: List[Tuple[float, str]] = []
        for track_name, clip_candidates in source_tracks.items():
            source_track = tracks_by_name.get(track_name.lower())
            if not source_track or not source_track.notes:
                continue
            note_to_clip: Dict[int, str] = {}
            if len(clip_candidates) == 1:
                for n in source_track.notes:
                    note_to_clip[int(n.note)] = clip_candidates[0]
            else:
                unique_notes = sorted({int(n.note) for n in source_track.notes})
                if unique_notes:
                    note_to_clip[unique_notes[0]] = clip_candidates[0]
                if len(unique_notes) >= 2:
                    note_to_clip[unique_notes[-1]] = clip_candidates[1]

            for note in source_track.notes:
                start_tick = int(note.start_tick)
                if not tick_in_windows(start_tick):
                    continue
                clip_name = note_to_clip.get(int(note.note), clip_candidates[0])
                if self._normalize_audio_track_id(clip_name) in muted:
                    continue
                start_sec_abs = self._midi_tick_to_seconds(start_tick, midi.ticks_per_beat, tempo_points)
                source_events.append((start_sec_abs, clip_name))

        if not source_events:
            if muted:
                self.log_widget.log(
                    f"X18 audio: all source events muted for 18-X18_Monster_{monster_idx:02d}.",
                    "INFO",
                )
            else:
                self.log_widget.log(
                    f"X18 audio: no source events resolved for 18-X18_Monster_{monster_idx:02d}.",
                    "WARNING",
                )
            return None

        window_start_sec = self._midi_tick_to_seconds(window_start_tick, midi.ticks_per_beat, tempo_points)
        window_end_sec = self._midi_tick_to_seconds(window_end_tick, midi.ticks_per_beat, tempo_points)
        if window_end_sec <= window_start_sec:
            window_end_sec = window_start_sec + 0.01

        resolved_clip_paths: Dict[str, str] = {}
        for _event_time, clip_name in source_events:
            if clip_name in resolved_clip_paths:
                continue
            clip_path = self._resolve_music_clip_path(clip_name, music_dirs)
            if clip_path:
                resolved_clip_paths[clip_name] = clip_path

        if not resolved_clip_paths:
            self.log_widget.log("X18 audio: no source clip files were found.", "WARNING")
            return None

        cache_sig_parts = [
            f"x18_{monster_idx}",
            os.path.normcase(os.path.normpath(midi_path)),
            str(os.path.getmtime(midi_path)),
            str(window_start_tick),
            str(window_end_tick),
            "muted:" + ",".join(sorted(muted)),
        ]
        for clip_name in sorted(resolved_clip_paths):
            clip_path = resolved_clip_paths[clip_name]
            cache_sig_parts.append(os.path.normcase(os.path.normpath(clip_path)))
            try:
                cache_sig_parts.append(str(os.path.getmtime(clip_path)))
            except OSError:
                cache_sig_parts.append("0")
        cache_key_raw = "|".join(cache_sig_parts)
        cache_key = hashlib.md5(cache_key_raw.encode("utf-8", errors="ignore")).hexdigest()  # nosec B324
        cache_hit = self._x18_mix_audio_cache.get(cache_key)
        if cache_hit and os.path.exists(cache_hit):
            return cache_hit

        clip_data_cache: Dict[str, Tuple[np.ndarray, int]] = {}
        target_rate: Optional[int] = None
        target_channels: int = 1
        for clip_name, clip_path in resolved_clip_paths.items():
            try:
                audio, sample_rate = sf.read(clip_path, always_2d=True, dtype="float32")
            except Exception as exc:
                self.log_widget.log(
                    f"X18 audio: failed reading clip '{os.path.basename(clip_path)}' ({exc})",
                    "WARNING",
                )
                continue
            if target_rate is None:
                target_rate = int(sample_rate)
            elif int(sample_rate) != int(target_rate):
                audio = self._resample_audio_linear(audio, int(sample_rate), int(target_rate))
            target_channels = max(target_channels, int(audio.shape[1]))
            clip_data_cache[clip_name] = (np.ascontiguousarray(audio, dtype=np.float32), int(target_rate))

        if not clip_data_cache or target_rate is None:
            return None

        base_duration = max(0.0, window_end_sec - window_start_sec)
        total_frames = max(1, int(math.ceil(base_duration * float(target_rate))))
        events_resolved: List[Tuple[int, np.ndarray]] = []
        for event_time_abs, clip_name in source_events:
            payload = clip_data_cache.get(clip_name)
            if not payload:
                continue
            clip_audio, _rate = payload
            rel_time = max(0.0, event_time_abs - window_start_sec)
            start_frame = max(0, int(round(rel_time * float(target_rate))))
            clip_buffer = clip_audio
            if clip_buffer.shape[1] < target_channels:
                if clip_buffer.shape[1] == 1:
                    clip_buffer = np.repeat(clip_buffer, target_channels, axis=1)
                else:
                    pad = np.zeros((clip_buffer.shape[0], target_channels), dtype=np.float32)
                    pad[:, :clip_buffer.shape[1]] = clip_buffer
                    clip_buffer = pad
            events_resolved.append((start_frame, clip_buffer))
            total_frames = max(total_frames, start_frame + int(clip_buffer.shape[0]))

        if not events_resolved:
            return None

        mix = np.zeros((total_frames, target_channels), dtype=np.float32)
        for start_frame, clip_buffer in events_resolved:
            end_frame = min(total_frames, start_frame + clip_buffer.shape[0])
            if end_frame <= start_frame:
                continue
            length = end_frame - start_frame
            mix[start_frame:end_frame, :] += clip_buffer[:length, :]

        peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        if peak > 1.0:
            mix /= peak

        cache_dir = os.path.join(tempfile.gettempdir(), "MSMAnimationViewer", "audio_cache")
        os.makedirs(cache_dir, exist_ok=True)
        out_path = os.path.normpath(
            os.path.join(cache_dir, f"x18_monster_{monster_idx:02d}_{cache_key}.wav")
        )
        try:
            sf.write(out_path, mix, int(target_rate), subtype="PCM_16")
        except Exception as exc:
            self.log_widget.log(f"X18 audio: failed writing cache clip ({exc})", "WARNING")
            return None

        self._x18_mix_audio_cache[cache_key] = out_path
        self.log_widget.log(
            f"X18 audio: built timed mix for 18-X18_Monster_{monster_idx:02d} ({os.path.basename(out_path)})",
            "INFO",
        )
        return out_path

    def _build_layered_clip_mix_audio(
        self,
        *,
        cache_prefix: str,
        clip_stems: List[str],
        music_dirs: List[str],
        muted_stems: Optional[Set[str]] = None,
    ) -> Optional[str]:
        muted = {self._normalize_audio_track_id(stem) for stem in (muted_stems or set()) if stem}
        active_stems = [
            stem for stem in clip_stems
            if self._normalize_audio_track_id(stem) not in muted
        ]
        if not active_stems:
            return None

        resolved_clip_paths: Dict[str, str] = {}
        for stem in active_stems:
            clip_path = self._resolve_music_clip_path(stem, music_dirs)
            if clip_path:
                resolved_clip_paths[stem] = clip_path

        if not resolved_clip_paths:
            return None

        cache_sig_parts: List[str] = [
            cache_prefix,
            "muted:" + ",".join(sorted(muted)),
        ]
        for stem in sorted(resolved_clip_paths):
            clip_path = resolved_clip_paths[stem]
            cache_sig_parts.append(stem)
            cache_sig_parts.append(os.path.normcase(os.path.normpath(clip_path)))
            try:
                cache_sig_parts.append(str(os.path.getmtime(clip_path)))
            except OSError:
                cache_sig_parts.append("0")

        cache_key_raw = "|".join(cache_sig_parts)
        cache_key = hashlib.md5(cache_key_raw.encode("utf-8", errors="ignore")).hexdigest()  # nosec B324
        cache_hit = self._special_audio_mix_cache.get(cache_key)
        if cache_hit and os.path.exists(cache_hit):
            return cache_hit

        clip_data_cache: Dict[str, Tuple[np.ndarray, int]] = {}
        target_rate: Optional[int] = None
        target_channels = 1
        max_frames = 0
        for stem, clip_path in resolved_clip_paths.items():
            try:
                audio, sample_rate = sf.read(clip_path, always_2d=True, dtype="float32")
            except Exception as exc:
                self.log_widget.log(
                    f"Audio mix: failed reading '{os.path.basename(clip_path)}' ({exc})",
                    "WARNING",
                )
                continue
            if target_rate is None:
                target_rate = int(sample_rate)
            elif int(sample_rate) != int(target_rate):
                audio = self._resample_audio_linear(audio, int(sample_rate), int(target_rate))
            target_channels = max(target_channels, int(audio.shape[1]))
            clip_buffer = np.ascontiguousarray(audio, dtype=np.float32)
            clip_data_cache[stem] = (clip_buffer, int(target_rate))
            max_frames = max(max_frames, int(clip_buffer.shape[0]))

        if target_rate is None or not clip_data_cache or max_frames <= 0:
            return None

        mix = np.zeros((max_frames, target_channels), dtype=np.float32)
        for stem, (clip_buffer, _sample_rate) in clip_data_cache.items():
            if clip_buffer.shape[1] < target_channels:
                if clip_buffer.shape[1] == 1:
                    clip_buffer = np.repeat(clip_buffer, target_channels, axis=1)
                else:
                    pad = np.zeros((clip_buffer.shape[0], target_channels), dtype=np.float32)
                    pad[:, :clip_buffer.shape[1]] = clip_buffer
                    clip_buffer = pad
            mix[:clip_buffer.shape[0], :] += clip_buffer

        peak = float(np.max(np.abs(mix))) if mix.size else 0.0
        if peak > 1.0:
            mix /= peak

        cache_dir = os.path.join(tempfile.gettempdir(), "MSMAnimationViewer", "audio_cache")
        os.makedirs(cache_dir, exist_ok=True)
        out_path = os.path.normpath(
            os.path.join(cache_dir, f"{cache_prefix}_{cache_key}.wav")
        )
        try:
            sf.write(out_path, mix, int(target_rate), subtype="PCM_16")
        except Exception as exc:
            self.log_widget.log(f"Audio mix: failed writing cache clip ({exc})", "WARNING")
            return None

        self._special_audio_mix_cache[cache_key] = out_path
        return out_path

    def _is_active_dof_audio_context(self) -> bool:
        if self.dof_search_enabled:
            return True
        return self._is_dof_json_payload(self.current_json_path, self.current_json_data)

    def _should_skip_dof_audio(
        self,
        animation_name: str,
        dof_asset_name: Optional[str] = None,
    ) -> bool:
        """Return True when DOF animation names imply silence (idle/mute)."""
        name_source = dof_asset_name or animation_name
        normalized = self._normalize_audio_key(name_source)
        tokens = set(self._audio_key_tokens(normalized))
        if not tokens:
            return False
        block_tokens = {
            "idle", "idle1", "idle2", "idleloop", "pose", "breath", "blink",
            "mute", "muted", "silent", "silence", "quiet", "rest", "stand",
            "sleep", "still", "calm",
        }
        return bool(tokens & block_tokens)

    def _derive_dof_audio_token(self, animation_name: str) -> Optional[str]:
        normalized = self._normalize_audio_key(animation_name)
        if not normalized:
            return None
        parts = [part for part in normalized.split("_") if part]
        if len(parts) >= 2 and re.fullmatch(r"[a-z]+\d*", parts[0]) and re.fullmatch(r"[a-z][a-z0-9]*", parts[1]):
            return f"{parts[0]}_{parts[1]}"
        return None

    def _audio_display_path(self, audio_path: str) -> str:
        roots: List[str] = []
        if self.game_path:
            roots.append(os.path.join(self.game_path, "data"))
            roots.append(self.game_path)
        if self.downloads_path:
            roots.append(self.downloads_path)
            roots.append(os.path.join(self.downloads_path, "data"))
        if self.dof_path:
            roots.append(self.dof_path)
        norm_audio = os.path.normpath(audio_path)
        for root in roots:
            if not root:
                continue
            try:
                rel_path = os.path.relpath(norm_audio, root)
            except (ValueError, OSError):
                continue
            if not rel_path.startswith(".."):
                return rel_path
        return norm_audio

    def _find_dof_bundle_audio_for_animation(
        self,
        animation_name: str,
        *,
        normalized_keys: List[str],
        monster_token: Optional[str],
        anim_source_name: Optional[str] = None,
        bundle_path: Optional[str] = None,
    ) -> Optional[str]:
        audio_key_name = anim_source_name or animation_name
        resolved_bundle = bundle_path or self._resolve_dof_bundle_for_current_animation(audio_key_name)
        if not resolved_bundle or not os.path.exists(resolved_bundle):
            self.log_widget.log(
                f"DOF audio: no bundle found for '{audio_key_name}'",
                "WARNING",
            )
            return None
        bundle_path = resolved_bundle
        cache_key = (
            os.path.normcase(os.path.normpath(bundle_path))
            + "|"
            + self._normalize_audio_key(audio_key_name)
        )
        cached = self._dof_bundle_audio_cache.get(cache_key)
        if cached and os.path.exists(cached):
            self.log_widget.log(
                f"DOF audio: cache hit for '{audio_key_name}' -> {self._audio_display_path(cached)}",
                "INFO",
            )
            return cached
        if UnityPy is None:
            self.log_widget.log(
                "DOF audio: UnityPy not available; cannot scan bundle audio.",
                "WARNING",
            )
            return None
        try:
            env = UnityPy.load(bundle_path)
        except Exception as exc:
            self.log_widget.log(f"DOF audio bundle load failed: {exc}", "WARNING")
            return None

        key_set: Set[str] = set()
        primary_key = self._normalize_audio_key(audio_key_name)
        if primary_key:
            key_set.update(self._expand_audio_key_variants(primary_key))
        for key in normalized_keys:
            if key:
                key_set.update(self._expand_audio_key_variants(key))
        dof_candidates = self._build_dof_audio_name_candidates(audio_key_name, monster_token)
        for candidate in dof_candidates:
            normalized = self._normalize_audio_key(candidate)
            if normalized:
                key_set.update(self._expand_audio_key_variants(normalized))
        if not key_set:
            self.log_widget.log(
                f"DOF audio: no key candidates for '{audio_key_name}'",
                "WARNING",
            )
            return None
        self.log_widget.log(
            f"DOF audio: scanning bundle '{os.path.basename(bundle_path)}' for '{audio_key_name}' "
            f"(keys={len(key_set)})",
            "INFO",
        )

        primary_tokens = set(self._audio_key_tokens(primary_key)) if primary_key else set()
        primary_numbers = self._audio_numeric_tokens(primary_key)
        primary_match_seen = False
        best_rank: Optional[Tuple[int, int, int, int, int, int, int]] = None
        best_clip_name: Optional[str] = None
        best_sample_name: Optional[str] = None
        best_payload: Optional[bytes] = None
        fallback_clip_name: Optional[str] = None
        fallback_sample_name: Optional[str] = None
        fallback_payload: Optional[bytes] = None
        best_numeric_rank: Optional[Tuple[int, int, int]] = None
        best_numeric_clip_name: Optional[str] = None
        best_numeric_sample_name: Optional[str] = None
        best_numeric_payload: Optional[bytes] = None
        clip_count = 0
        sample_count = 0
        for obj in env.objects:
            if getattr(obj.type, "name", None) != "AudioClip":
                continue
            try:
                clip = obj.read()
                clip_name = getattr(clip, "m_Name", "") or ""
                samples = getattr(clip, "samples", None) or {}
            except Exception:
                continue
            if not isinstance(samples, dict) or not samples:
                continue
            clip_count += 1
            clip_norm = self._normalize_audio_key(clip_name)
            for sample_name, sample_bytes in samples.items():
                if not isinstance(sample_bytes, (bytes, bytearray)):
                    continue
                sample_count += 1
                sample_norm = self._normalize_audio_key(sample_name)
                clip_tokens = set(self._audio_key_tokens(clip_norm)) | set(self._audio_key_tokens(sample_norm))
                clip_numbers = self._audio_numeric_tokens(clip_norm) | self._audio_numeric_tokens(sample_norm)
                if fallback_payload is None:
                    fallback_clip_name = clip_name or str(sample_name)
                    fallback_sample_name = str(sample_name)
                    fallback_payload = bytes(sample_bytes)
                primary_exact = 1 if primary_key and (clip_norm == primary_key or sample_norm == primary_key) else 0
                primary_prefix = 1 if primary_key and (
                    clip_norm.startswith(primary_key) or sample_norm.startswith(primary_key)
                ) else 0
                primary_contains = 1 if primary_key and (primary_key in clip_norm or primary_key in sample_norm) else 0
                if primary_exact or primary_prefix or primary_contains:
                    primary_match_seen = True
                primary_overlap = 0
                if primary_tokens:
                    primary_overlap = len([tok for tok in clip_tokens & primary_tokens if not tok.isdigit()])

                key_exact = 1 if (clip_norm in key_set or sample_norm in key_set) else 0
                key_contains = 0
                key_token_overlap = 0
                for key in key_set:
                    if not key:
                        continue
                    if key in clip_norm or key in sample_norm:
                        key_contains += 1
                    key_tokens = set(self._audio_key_tokens(key))
                    overlap = clip_tokens & key_tokens
                    if overlap:
                        key_token_overlap = max(
                            key_token_overlap,
                            len([tok for tok in overlap if not tok.isdigit()])
                        )

                has_match = (
                    primary_exact
                    or primary_prefix
                    or primary_contains
                    or primary_overlap
                    or key_exact
                    or key_contains
                    or key_token_overlap
                )
                if not has_match:
                    continue

                rank = (
                    primary_exact,
                    primary_prefix,
                    primary_contains,
                    primary_overlap,
                    key_exact,
                    key_contains,
                    key_token_overlap,
                )
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best_clip_name = clip_name or str(sample_name)
                    best_sample_name = str(sample_name)
                    best_payload = bytes(sample_bytes)
                if primary_numbers and clip_numbers:
                    overlap_nums = primary_numbers & clip_numbers
                    if overlap_nums:
                        numeric_rank = (
                            len(overlap_nums),
                            -len(clip_numbers - primary_numbers),
                            -len(clip_numbers),
                        )
                        if best_numeric_rank is None or numeric_rank > best_numeric_rank:
                            best_numeric_rank = numeric_rank
                            best_numeric_clip_name = clip_name or str(sample_name)
                            best_numeric_sample_name = str(sample_name)
                            best_numeric_payload = bytes(sample_bytes)
        if (
            best_numeric_payload
            and primary_numbers
            and not primary_match_seen
            and (clip_count > 1 or sample_count > 1)
        ):
            best_payload = best_numeric_payload
            best_clip_name = best_numeric_clip_name
            best_sample_name = best_numeric_sample_name
            self.log_widget.log(
                f"DOF audio: matched by numeric token for '{audio_key_name}'",
                "INFO",
            )
        if not best_payload or not best_clip_name:
            if fallback_payload and (clip_count == 1 or sample_count == 1):
                best_payload = fallback_payload
                best_clip_name = fallback_clip_name
                best_sample_name = fallback_sample_name
                self.log_widget.log(
                    f"DOF audio: using sole bundle clip for '{audio_key_name}'",
                    "INFO",
                )
        if not best_payload or not best_clip_name:
            self.log_widget.log(
                f"DOF audio: no AudioClip match for '{audio_key_name}' "
                f"(clips={clip_count}, samples={sample_count})",
                "WARNING",
            )
            return None

        cache_dir = os.path.join(self.dof_path or "", "Output", "audio_cache")
        os.makedirs(cache_dir, exist_ok=True)
        preferred_name = best_sample_name or best_clip_name
        preferred_base = os.path.basename(preferred_name)
        preferred_stem, preferred_ext = os.path.splitext(preferred_base)
        ext = preferred_ext.lower()
        if not ext:
            ext = self._infer_audio_extension_from_bytes(best_payload)
        if not ext:
            ext = ".ogg"
        safe_name = re.sub(r"[^0-9A-Za-z._-]+", "_", preferred_stem or best_clip_name).strip("._")
        if not safe_name:
            safe_name = "dof_audio"
        if not safe_name.lower().endswith(ext):
            safe_name += ext
        out_path = os.path.normpath(os.path.join(cache_dir, safe_name))
        try:
            with open(out_path, "wb") as handle:
                handle.write(best_payload)
        except OSError as exc:
            self.log_widget.log(f"DOF audio cache write failed: {exc}", "WARNING")
            return None
        self._dof_bundle_audio_cache[cache_key] = out_path
        self.log_widget.log(
            f"Extracted DOF bundle audio '{best_clip_name}' ({os.path.basename(safe_name)}) -> {self._audio_display_path(out_path)}",
            "INFO",
        )
        return out_path

    @staticmethod
    def _infer_audio_extension_from_bytes(payload: bytes) -> str:
        if not payload:
            return ""
        if payload.startswith(b"OggS"):
            return ".ogg"
        if payload.startswith(b"RIFF") and len(payload) >= 12 and payload[8:12] == b"WAVE":
            return ".wav"
        if payload[:3] == b"ID3" or payload[:2] == b"\xff\xfb":
            return ".mp3"
        return ""

    def _resolve_dof_bundle_for_current_animation(self, animation_name: str) -> Optional[str]:
        if not self.dof_path:
            return None
        anim_stem = Path(animation_name).stem
        anim_key = f"{anim_stem}.ANIMBBB"
        cache_hit = self._dof_anim_bundle_cache.get(anim_key.lower())
        if cache_hit and os.path.exists(cache_hit):
            return cache_hit
        output_index_path = os.path.join(self.dof_path, "Output", "_bundle_index.json")
        bundle_map: Dict[str, str] = {}
        payload: Dict[str, Any] = {}
        if os.path.exists(output_index_path):
            try:
                payload = json.loads(Path(output_index_path).read_text(encoding="utf-8"))
                bundle_map = payload.get("bundle_map", {})
                if not isinstance(bundle_map, dict):
                    bundle_map = {}
            except Exception:
                bundle_map = {}
        else:
            self.log_widget.log(
                "DOF audio: bundle index not found; will scan bundles on demand.",
                "INFO",
            )
        for candidate in (anim_key, anim_key.lower(), anim_key.upper()):
            bundle_path = bundle_map.get(candidate)
            if isinstance(bundle_path, str) and bundle_path and os.path.exists(bundle_path):
                normalized = os.path.normpath(bundle_path)
                self._dof_anim_bundle_cache[anim_key.lower()] = normalized
                self.log_widget.log(
                    f"DOF audio: resolved bundle for '{animation_name}' via index -> {os.path.basename(normalized)}",
                    "INFO",
                )
                return normalized
        if self.current_json_path:
            stem = Path(self.current_json_path).stem
            alt_key = f"{stem}.ANIMBBB"
            for candidate in (alt_key, alt_key.lower(), alt_key.upper()):
                bundle_path = bundle_map.get(candidate)
                if isinstance(bundle_path, str) and bundle_path and os.path.exists(bundle_path):
                    normalized = os.path.normpath(bundle_path)
                    self._dof_anim_bundle_cache[anim_key.lower()] = normalized
                    self.log_widget.log(
                        f"DOF audio: resolved bundle for '{animation_name}' via json stem -> {os.path.basename(normalized)}",
                        "INFO",
                    )
                    return normalized
        scanned = self._scan_bundle_for_anim_name(anim_key)
        if scanned:
            self._dof_anim_bundle_cache[anim_key.lower()] = scanned
            self.log_widget.log(
                f"DOF audio: resolved bundle for '{animation_name}' via scan -> {os.path.basename(scanned)}",
                "INFO",
            )
            if output_index_path:
                try:
                    os.makedirs(os.path.dirname(output_index_path), exist_ok=True)
                    bundle_map[anim_key] = scanned
                    bundle_map[anim_key.lower()] = scanned
                    payload["bundle_map"] = bundle_map
                    if "root" not in payload:
                        payload["root"] = self.dof_path
                    Path(output_index_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
                except Exception:
                    pass
            return scanned
        return None

    def _get_dof_particle_library(self) -> Optional[DofParticleLibrary]:
        """Load and cache particle materials/textures for DOF bundles."""
        if not self.dof_path:
            return None
        if UnityPy is None:
            self.log_widget.log(
                "DOF particles: UnityPy not available; cannot load particle assets.",
                "WARNING",
            )
            return None
        particles_root = os.path.join(self.dof_path, "particles")
        if not os.path.isdir(particles_root):
            return None
        if self._dof_particle_library and self._dof_particle_library_root == particles_root:
            return self._dof_particle_library
        library = build_particle_library(particles_root)
        self._dof_particle_library_root = particles_root
        self._dof_particle_library = library
        return library

    def _material_color_from_info(self, material) -> Tuple[float, float, float, float]:
        if not material:
            return (1.0, 1.0, 1.0, 1.0)
        color = [1.0, 1.0, 1.0, 1.0]
        for key in ("_TintColor", "_Color", "_MainColor", "_BaseColor", "_EmissionColor"):
            value = material.colors.get(key)
            if value:
                color[0] *= float(value[0])
                color[1] *= float(value[1])
                color[2] *= float(value[2])
                color[3] *= float(value[3])
        intensity = material.floats.get("_Intensity")
        if intensity is None:
            intensity = material.floats.get("_IntensityTint")
        if intensity is None:
            intensity = material.floats.get("_Glow")
        if intensity is not None:
            try:
                intensity_val = float(intensity)
                color[0] *= intensity_val
                color[1] *= intensity_val
                color[2] *= intensity_val
            except Exception:
                pass
        return (color[0], color[1], color[2], color[3])

    @staticmethod
    def _estimate_control_socket_offset(
        node_channels: Dict[int, List[Tuple[float, float, int]]],
        control_channels: Dict[int, List[Tuple[float, float, int]]],
    ) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
        def _to_key_map(channel: List[Tuple[float, float, int]]) -> Dict[float, float]:
            return {round(float(t), 6): float(v) for t, v, _ in channel}

        node_x = _to_key_map(node_channels.get(0, []))
        node_y = _to_key_map(node_channels.get(1, []))
        ctrl_x = _to_key_map(control_channels.get(0, []))
        ctrl_y = _to_key_map(control_channels.get(1, []))
        if not node_x or not node_y or not ctrl_x or not ctrl_y:
            return None
        common_times = sorted(set(node_x) & set(node_y) & set(ctrl_x) & set(ctrl_y))
        if not common_times:
            return None
        ctrl_rot = _to_key_map(control_channels.get(5, []))
        node_rot = _to_key_map(node_channels.get(5, []))

        local_xs: List[float] = []
        local_ys: List[float] = []
        for t in common_times:
            dx = node_x[t] - ctrl_x[t]
            dy = node_y[t] - ctrl_y[t]
            rot = ctrl_rot.get(t, node_rot.get(t, 0.0))
            cos_r = math.cos(-rot)
            sin_r = math.sin(-rot)
            local_xs.append(dx * cos_r - dy * sin_r)
            local_ys.append(dx * sin_r + dy * cos_r)

        if not local_xs or not local_ys:
            return None
        mean_x = sum(local_xs) / len(local_xs)
        mean_y = sum(local_ys) / len(local_ys)
        std_x = math.sqrt(sum((value - mean_x) ** 2 for value in local_xs) / len(local_xs))
        std_y = math.sqrt(sum((value - mean_y) ** 2 for value in local_ys) / len(local_ys))
        return ((mean_x, mean_y), (std_x, std_y))

    @staticmethod
    def _select_particle_source_control_point(
        node_channels: Dict[int, List[Tuple[float, float, int]]],
        control_points: Dict[str, DofControlPoint],
    ) -> Tuple[Optional[DofControlPoint], Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        def _sorted_channel(channel: List[Tuple[float, float, int]]) -> List[Tuple[float, float]]:
            return sorted((float(t), float(v)) for t, v, _ in channel)

        def _eval_channel(channel: List[Tuple[float, float]], t: float, default: float = 0.0) -> float:
            if not channel:
                return default
            if t <= channel[0][0]:
                return channel[0][1]
            for idx in range(1, len(channel)):
                t1, v1 = channel[idx]
                t0, v0 = channel[idx - 1]
                if t <= t1:
                    if abs(t1 - t0) < 1e-8:
                        return v1
                    factor = (t - t0) / (t1 - t0)
                    return v0 + (v1 - v0) * factor
            return channel[-1][1]

        best_control: Optional[DofControlPoint] = None
        best_offset: Optional[Tuple[float, float]] = None
        best_std: Optional[Tuple[float, float]] = None
        best_score: Optional[Tuple[int, float, float]] = None

        node_x = _sorted_channel(node_channels.get(0, []))
        node_y = _sorted_channel(node_channels.get(1, []))
        if not node_x or not node_y:
            return None, None, None

        for control_point in control_points.values():
            control_x = _sorted_channel(control_point.channels.get(0, []))
            control_y = _sorted_channel(control_point.channels.get(1, []))
            control_z = _sorted_channel(control_point.channels.get(2, []))
            control_rot = _sorted_channel(control_point.channels.get(5, []))
            if not control_x or not control_y:
                continue

            sample_times = sorted(
                {
                    round(float(t), 6)
                    for channel in (
                        node_x,
                        node_y,
                        control_x,
                        control_y,
                        control_z,
                        control_rot,
                    )
                    for t, _ in channel
                }
            )
            if not sample_times:
                continue
            expanded_times: List[float] = []
            for idx, time_value in enumerate(sample_times):
                expanded_times.append(time_value)
                if idx + 1 < len(sample_times):
                    expanded_times.append((time_value + sample_times[idx + 1]) * 0.5)

            local_xs: List[float] = []
            local_ys: List[float] = []
            source_heights: List[float] = []
            for time_value in expanded_times:
                px = _eval_channel(node_x, time_value, 0.0)
                py = _eval_channel(node_y, time_value, 0.0)
                cx = _eval_channel(control_x, time_value, 0.0)
                cy = _eval_channel(control_y, time_value, 0.0)
                cz = _eval_channel(control_z, time_value, 0.0)
                rot = _eval_channel(control_rot, time_value, 0.0)
                dx = px - cx
                dy = py - cy
                cos_r = math.cos(-rot)
                sin_r = math.sin(-rot)
                local_xs.append(dx * cos_r - dy * sin_r)
                local_ys.append(dx * sin_r + dy * cos_r)
                source_heights.append(cy - cz)

            if not local_xs or not local_ys or not source_heights:
                continue

            mean_x = sum(local_xs) / len(local_xs)
            mean_y = sum(local_ys) / len(local_ys)
            std_x = math.sqrt(sum((value - mean_x) ** 2 for value in local_xs) / len(local_xs))
            std_y = math.sqrt(sum((value - mean_y) ** 2 for value in local_ys) / len(local_ys))
            local_offset = (mean_x, mean_y)
            local_std = (std_x, std_y)
            max_std = max(local_std[0], local_std[1])
            is_stable = 1 if max_std <= 0.5 else 0
            mean_source_height = sum(source_heights) / len(source_heights)
            score = (
                is_stable,
                mean_source_height,
                -(local_std[0] + local_std[1]),
            )
            if best_score is None or score > best_score:
                best_score = score
                best_control = control_point
                best_offset = local_offset
                best_std = local_std

        return best_control, best_offset, best_std

    def _match_particle_source_layer(
        self,
        node,
        prefab,
        animation: AnimationData,
        source_nodes: Optional[Dict[str, DofAnimNode]] = None,
    ) -> Tuple[
        Optional[LayerData],
        Optional[Tuple[float, float]],
        Optional[Tuple[float, float]],
        Optional[Tuple[float, float]],
    ]:
        if not animation or not getattr(animation, "layers", None):
            return None, None, None, None

        renderer = self.gl_widget.renderer
        temp_player = AnimationPlayer()
        temp_player.load_animation(animation)
        layer_map = {layer.layer_id: layer for layer in animation.layers}
        pos_scale = (
            renderer.local_position_multiplier
            * renderer.base_world_scale
            * renderer.position_scale
        )
        flip_y = bool(getattr(animation, "dof_anim_flip_y", False))

        def _eval_channel(
            channel_map: Dict[int, List[Tuple[float, float, int]]],
            channel_idx: int,
            sample_time: float,
            default: float = 0.0,
        ) -> float:
            entries = sorted(channel_map.get(channel_idx, []), key=lambda item: item[0])
            if not entries:
                return default
            if sample_time <= entries[0][0]:
                return float(entries[0][1])
            for idx in range(1, len(entries)):
                t1, v1, _k1 = entries[idx]
                t0, v0, _k0 = entries[idx - 1]
                if sample_time <= t1:
                    if abs(t1 - t0) < 1e-8:
                        return float(v1)
                    factor = (sample_time - t0) / (t1 - t0)
                    return float(v0) + (float(v1) - float(v0)) * factor
            return float(entries[-1][1])

        sample_times = sorted(
            {
                round(float(t), 6)
                for channel_idx in (0, 1, 2)
                for t, _value, _kind in node.channels.get(channel_idx, [])
            }
        )
        if not sample_times:
            sample_times = [0.0]
        expanded_times: List[float] = []
        for idx, time_value in enumerate(sample_times):
            expanded_times.append(time_value)
            if idx + 1 < len(sample_times):
                expanded_times.append((time_value + sample_times[idx + 1]) * 0.5)
        if len(expanded_times) > 48:
            step = max(1, len(expanded_times) // 48)
            expanded_times = expanded_times[::step]
            if expanded_times[-1] != sample_times[-1]:
                expanded_times.append(sample_times[-1])

        def _collect_sample_times(
            channel_maps: List[Dict[int, List[Tuple[float, float, int]]]],
            channel_ids: Tuple[int, ...],
        ) -> List[float]:
            collected = sorted(
                {
                    round(float(t), 6)
                    for channel_map in channel_maps
                    for channel_idx in channel_ids
                    for t, _value, _kind in channel_map.get(channel_idx, [])
                }
            )
            if not collected:
                return [0.0]
            expanded: List[float] = []
            for idx, time_value in enumerate(collected):
                expanded.append(time_value)
                if idx + 1 < len(collected):
                    expanded.append((time_value + collected[idx + 1]) * 0.5)
            if len(expanded) > 48:
                step = max(1, len(expanded) // 48)
                expanded = expanded[::step]
                if expanded[-1] != collected[-1]:
                    expanded.append(collected[-1])
            return expanded

        def _estimate_layer_sprite_area(layer: LayerData) -> float:
            sprite_name = ""
            for keyframe in layer.keyframes:
                if keyframe.sprite_name:
                    sprite_name = keyframe.sprite_name
                    break
            if not sprite_name:
                return 0.0
            override_atlases = self.gl_widget.layer_atlas_overrides.get(layer.layer_id)
            atlas_chain = (
                list(override_atlases) + self.gl_widget.texture_atlases
                if override_atlases
                else self.gl_widget.texture_atlases
            )
            for atlas in atlas_chain:
                sprite = atlas.get_sprite(sprite_name)
                if not sprite:
                    continue
                width = float(sprite.original_w if sprite.original_w > 0 else sprite.w)
                height = float(sprite.original_h if sprite.original_h > 0 else sprite.h)
                return max(0.0, width * height)
            return 0.0

        def _eval_raw_node_channel(
            raw_channels: Dict[int, List[Tuple[float, float, int]]],
            channel_idx: int,
            sample_time: float,
            default: float,
            offset_value: float = 0.0,
        ) -> float:
            return _eval_channel(raw_channels, channel_idx, sample_time, default) + offset_value

        def _sample_particle_node(sample_time: float) -> Tuple[float, float]:
            px = _eval_channel(node.channels, 0, sample_time, 0.0)
            py = _eval_channel(node.channels, 1, sample_time, 0.0)
            pz = _eval_channel(node.channels, 2, sample_time, 0.0)
            px += float(node.offset[0]) + float(prefab.transform_pos[0])
            py += float(node.offset[1]) + float(prefab.transform_pos[1])
            pz += float(node.offset[2]) + float(prefab.transform_pos[2])
            py -= pz
            if flip_y:
                py = -py
            px = px * pos_scale + renderer.world_offset_x
            py = py * pos_scale + renderer.world_offset_y
            return px, py

        best_layer: Optional[LayerData] = None
        best_mean_local: Optional[Tuple[float, float]] = None
        best_std_local: Optional[Tuple[float, float]] = None
        best_surface_direction: Optional[Tuple[float, float]] = None
        best_score: Optional[Tuple[float, float]] = None

        if source_nodes:
            candidate_rows: List[
                Tuple[
                    float,
                    float,
                    Optional[LayerData],
                    Tuple[float, float],
                    Tuple[float, float],
                    Tuple[float, float],
                ]
            ] = []
            for source_node in source_nodes.values():
                layer = next(
                    (candidate for candidate in animation.layers if candidate.name == source_node.name),
                    None,
                )
                if not layer:
                    continue
                raw_times = _collect_sample_times(
                    [node.channels, source_node.channels],
                    (0, 1, 3, 4, 5),
                )
                local_offsets: List[Tuple[float, float]] = []
                dx_values: List[float] = []
                dy_values: List[float] = []
                scale_x_values: List[float] = []
                scale_y_values: List[float] = []
                rot_values: List[float] = []
                for sample_time in raw_times:
                    particle_x = _eval_raw_node_channel(
                        node.channels,
                        0,
                        sample_time,
                        0.0,
                        float(node.offset[0]) + float(prefab.transform_pos[0]),
                    )
                    particle_y = _eval_raw_node_channel(
                        node.channels,
                        1,
                        sample_time,
                        0.0,
                        float(node.offset[1]) + float(prefab.transform_pos[1]),
                    )
                    source_x = _eval_raw_node_channel(
                        source_node.channels,
                        0,
                        sample_time,
                        0.0,
                        float(source_node.offset[0]),
                    )
                    source_y = _eval_raw_node_channel(
                        source_node.channels,
                        1,
                        sample_time,
                        0.0,
                        float(source_node.offset[1]),
                    )
                    delta_x = particle_x - source_x
                    delta_y = particle_y - source_y
                    local_offsets.append((delta_x * pos_scale, delta_y * pos_scale))
                    dx_values.append(delta_x)
                    dy_values.append(delta_y)
                    scale_x_values.append(
                        _eval_raw_node_channel(node.channels, 3, sample_time, 1.0)
                        - _eval_raw_node_channel(source_node.channels, 3, sample_time, 1.0)
                    )
                    scale_y_values.append(
                        _eval_raw_node_channel(node.channels, 4, sample_time, 1.0)
                        - _eval_raw_node_channel(source_node.channels, 4, sample_time, 1.0)
                    )
                    rot_values.append(
                        _eval_raw_node_channel(node.channels, 5, sample_time, 0.0)
                        - _eval_raw_node_channel(source_node.channels, 5, sample_time, 0.0)
                    )
                if len(local_offsets) < 2:
                    continue

                mean_local_x = sum(value[0] for value in local_offsets) / len(local_offsets)
                mean_local_y = sum(value[1] for value in local_offsets) / len(local_offsets)
                std_local_x = math.sqrt(
                    sum((value[0] - mean_local_x) ** 2 for value in local_offsets) / len(local_offsets)
                )
                std_local_y = math.sqrt(
                    sum((value[1] - mean_local_y) ** 2 for value in local_offsets) / len(local_offsets)
                )
                def _rms(values: List[float]) -> float:
                    return math.sqrt(sum(value * value for value in values) / len(values))
                metric = (
                    _rms(dx_values)
                    + _rms(dy_values)
                    + 25.0 * _rms(scale_x_values)
                    + 25.0 * _rms(scale_y_values)
                    + 10.0 * _rms(rot_values)
                )
                direction_x = -mean_local_x
                direction_y = -mean_local_y
                direction_len = math.hypot(direction_x, direction_y)
                if direction_len > 1e-6:
                    source_direction = (direction_x / direction_len, direction_y / direction_len)
                else:
                    source_direction = (0.0, -1.0)
                candidate_rows.append(
                    (
                        metric,
                        _estimate_layer_sprite_area(layer),
                        layer,
                        (mean_local_x, mean_local_y),
                        (std_local_x, std_local_y),
                        source_direction,
                    )
                )
            if candidate_rows:
                candidate_rows.sort(key=lambda item: item[0])
                best_metric_value = candidate_rows[0][0]
                shortlist = [
                    row
                    for row in candidate_rows
                    if row[0] <= (best_metric_value * 1.5 + 0.5)
                ]
                shortlist.sort(key=lambda item: (-item[1], item[0]))
                chosen_metric, _chosen_area, chosen_layer, chosen_mean, chosen_std, chosen_direction = shortlist[0]
                if chosen_layer is not None:
                    return chosen_layer, chosen_mean, chosen_std, chosen_direction

        for layer in animation.layers:
            layer_states_by_time: Dict[float, Dict[str, Any]] = {}
            local_offsets: List[Tuple[float, float]] = []
            world_distances: List[float] = []
            for sample_time in expanded_times:
                state = layer_states_by_time.get(sample_time)
                if state is None:
                    time_cache: Dict[int, Dict[str, Any]] = {}
                    state = renderer.calculate_world_state(
                        layer,
                        sample_time,
                        temp_player,
                        layer_map,
                        time_cache,
                        self.gl_widget.texture_atlases,
                        self.gl_widget.layer_atlas_overrides,
                        self.gl_widget.layer_pivot_context,
                    )
                    layer_states_by_time[sample_time] = state
                if not state or not state.get("sprite_name"):
                    continue
                node_x, node_y = _sample_particle_node(sample_time)
                anchor_x = float(state.get("anchor_world_x", state.get("tx", 0.0)))
                anchor_y = float(state.get("anchor_world_y", state.get("ty", 0.0)))
                dx = node_x - anchor_x
                dy = node_y - anchor_y
                m00 = float(state.get("m00", 1.0))
                m01 = float(state.get("m01", 0.0))
                m10 = float(state.get("m10", 0.0))
                m11 = float(state.get("m11", 1.0))
                det = m00 * m11 - m01 * m10
                if abs(det) < 1e-8:
                    continue
                local_x = (m11 * dx - m01 * dy) / det
                local_y = (-m10 * dx + m00 * dy) / det
                local_offsets.append((local_x, local_y))
                world_distances.append(math.hypot(dx, dy))

            if len(local_offsets) < 2:
                continue

            mean_local_x = sum(value[0] for value in local_offsets) / len(local_offsets)
            mean_local_y = sum(value[1] for value in local_offsets) / len(local_offsets)
            std_local_x = math.sqrt(
                sum((value[0] - mean_local_x) ** 2 for value in local_offsets) / len(local_offsets)
            )
            std_local_y = math.sqrt(
                sum((value[1] - mean_local_y) ** 2 for value in local_offsets) / len(local_offsets)
            )
            std_sum = std_local_x + std_local_y
            mean_distance = sum(world_distances) / len(world_distances)
            score = (std_sum, mean_distance)
            if best_score is None or score < best_score:
                direction_x = -mean_local_x
                direction_y = -mean_local_y
                direction_len = math.hypot(direction_x, direction_y)
                if direction_len > 1e-6:
                    source_direction = (direction_x / direction_len, direction_y / direction_len)
                else:
                    source_direction = (0.0, -1.0)
                best_score = score
                best_layer = layer
                best_mean_local = (mean_local_x, mean_local_y)
                best_std_local = (std_local_x, std_local_y)
                best_surface_direction = source_direction

        return best_layer, best_mean_local, best_std_local, best_surface_direction

    def _build_particle_entries(
        self,
        nodes,
        library: DofParticleLibrary,
        animation: AnimationData,
        control_points: Optional[Dict[str, DofControlPoint]] = None,
        source_nodes: Optional[Dict[str, DofAnimNode]] = None,
    ) -> List[ParticleRenderEntry]:
        entries: List[ParticleRenderEntry] = []
        for node in nodes:
            prefab = library.prefabs.get(node.prefab_path_id)
            if not prefab:
                continue
            material = library.materials.get(prefab.material_path_id or 0)
            if not material:
                continue
            texture = library.textures.get(material.texture_path_id or 0)
            if not texture:
                continue
            base_w = float(texture.image.width)
            base_h = float(texture.image.height)
            blend_mode = self.gl_widget.renderer.get_blend_mode_for_shader(material.shader_name)
            mat_color = self._material_color_from_info(material)
            seed_payload = f"{node.name}|{prefab.path_id}".encode("utf-8", errors="ignore")
            seed_base = int(zlib.crc32(seed_payload))
            source_control = None
            control_offset = None
            control_offset_std = None
            source_layer = None
            source_layer_offset = None
            source_layer_offset_std = None
            source_surface_direction = None
            if control_points:
                source_control, control_offset, control_offset_std = (
                    self._select_particle_source_control_point(node.channels, control_points)
                )
            source_layer, source_layer_offset, source_layer_offset_std, source_surface_direction = (
                self._match_particle_source_layer(node, prefab, animation, source_nodes=source_nodes)
            )
            entries.append(
                ParticleRenderEntry(
                    name=node.name,
                    texture_image=texture.image,
                    texture_id=None,
                    texture_width=texture.image.width,
                    texture_height=texture.image.height,
                    base_width=base_w,
                    base_height=base_h,
                    blend_mode=blend_mode,
                    offset=node.offset,
                    parallax=node.parallax,
                    image_scale=node.image_scale,
                    prefab_pos=prefab.transform_pos,
                    prefab_rot=prefab.transform_rot,
                    simulation_space=prefab.simulation_space,
                    custom_space_pos=prefab.custom_space_pos,
                    custom_space_rot=prefab.custom_space_rot,
                    shape_position=prefab.shape_position,
                    shape_rotation=prefab.shape_rotation,
                    shape_scale=prefab.shape_scale,
                    channels=node.channels,
                    material_color=mat_color,
                    material_floats=material.floats,
                    seed_base=seed_base,
                    emission_rate_range=prefab.emission_rate_range,
                    emission_distance_range=prefab.emission_distance_range,
                    lifetime_range=prefab.start_lifetime_range,
                    speed_range=prefab.start_speed_range,
                    start_rotation_range=prefab.start_rotation_range,
                    size_range=prefab.start_size_range,
                    size_y_range=prefab.start_size_y_range,
                    color_range=prefab.start_color_range,
                    color_over_lifetime_keys=prefab.color_over_lifetime_keys,
                    alpha_over_lifetime_keys=prefab.alpha_over_lifetime_keys,
                    size_over_lifetime_keys=prefab.size_over_lifetime_keys,
                    size_over_lifetime_y_keys=prefab.size_over_lifetime_y_keys,
                    rotation_over_lifetime_keys=prefab.rotation_over_lifetime_keys,
                    rotation_over_lifetime_range=prefab.rotation_over_lifetime_range,
                    move_with_transform=prefab.move_with_transform,
                    velocity_over_lifetime_range=prefab.velocity_over_lifetime_range,
                    velocity_in_world_space=prefab.velocity_in_world_space,
                    velocity_module_enabled=prefab.velocity_module_enabled,
                    emitter_velocity_mode=prefab.emitter_velocity_mode,
                    gravity_modifier_range=prefab.gravity_modifier_range,
                    shape_type=prefab.shape_type,
                    shape_placement_mode=prefab.shape_placement_mode,
                    shape_radius=prefab.shape_radius,
                    shape_radius_thickness=prefab.shape_radius_thickness,
                    shape_angle=prefab.shape_angle,
                    shape_length=prefab.shape_length,
                    shape_box=prefab.shape_box,
                    control_name=source_control.name if source_control else None,
                    control_offset=source_control.offset if source_control else (0.0, 0.0, 0.0),
                    control_channels=source_control.channels if source_control else None,
                    control_local_offset=control_offset,
                    control_local_offset_std=control_offset_std,
                    source_layer_id=source_layer.layer_id if source_layer else None,
                    source_layer_name=source_layer.name if source_layer else None,
                    source_layer_offset_local=source_layer_offset,
                    source_layer_offset_std=source_layer_offset_std,
                    source_surface_direction=source_surface_direction,
                )
            )
        return entries

    def _refresh_dof_particle_entries(self, animation: AnimationData) -> None:
        if not self.dof_path:
            self.gl_widget.set_particle_entries([])
            return
        dof_context = bool(self.dof_search_enabled)
        if not dof_context:
            dof_context = self._is_dof_json_payload(self.current_json_path, self.current_json_data)
        if not dof_context and isinstance(self.current_json_data, dict):
            dof_context = isinstance(self.current_json_data.get("dof_meta"), dict)
        if not dof_context:
            self.gl_widget.set_particle_entries([])
            return
        if UnityPy is None:
            self.gl_widget.set_particle_entries([])
            return
        anim_name = animation.name or self.current_animation_name or ""
        bundle_path = self._resolve_dof_bundle_for_current_animation(anim_name)
        if not bundle_path:
            self.gl_widget.set_particle_entries([])
            return
        cache_key = (bundle_path, anim_name)
        cached = self._dof_particle_entry_cache.get(cache_key)
        if cached is not None:
            needs_rebuild = any(
                entry.texture_id is None and entry.texture_image is None for entry in cached
            )
            if needs_rebuild:
                nodes = extract_particle_nodes(bundle_path, anim_name)
                library = self._get_dof_particle_library()
                if nodes and library:
                    control_cache_key = (bundle_path, anim_name)
                    control_points = self._dof_control_point_cache.get(control_cache_key)
                    source_nodes = self._dof_source_node_cache.get(control_cache_key)
                    if control_points is None:
                        control_points = extract_control_points(
                            bundle_path,
                            anim_name,
                            names=[
                                "controlpoint_fx",
                                "controlpoint_accessory",
                                "controlpoint_effectcenter",
                            ],
                        )
                        self._dof_control_point_cache[control_cache_key] = control_points
                    if source_nodes is None:
                        source_nodes = extract_source_nodes(bundle_path, anim_name)
                        self._dof_source_node_cache[control_cache_key] = source_nodes
                    cached = self._build_particle_entries(
                        nodes,
                        library,
                        animation,
                        control_points=control_points,
                        source_nodes=source_nodes,
                    )
                    self._dof_particle_entry_cache[cache_key] = cached
                    self.log_widget.log(
                        f"DOF particles: rebuilt emitter textures for {anim_name}.",
                        "INFO",
                    )
                else:
                    cached = []
                    self._dof_particle_entry_cache[cache_key] = cached
                    self.log_widget.log(
                        f"DOF particles: unable to rebuild emitter textures for {anim_name}.",
                        "WARNING",
                    )
        if cached is None:
            nodes = extract_particle_nodes(bundle_path, anim_name)
            if not nodes:
                self.log_widget.log(
                    f"DOF particles: no particle nodes found for {anim_name}.",
                    "INFO",
                )
                self._dof_particle_entry_cache[cache_key] = []
                self.gl_widget.set_particle_entries([])
                return
            library = self._get_dof_particle_library()
            if not library:
                self.log_widget.log(
                    "DOF particles: particle library missing (check DOF/particles folder).",
                    "WARNING",
                )
                self._dof_particle_entry_cache[cache_key] = []
                self.gl_widget.set_particle_entries([])
                return
            control_cache_key = (bundle_path, anim_name)
            control_points = self._dof_control_point_cache.get(control_cache_key)
            source_nodes = self._dof_source_node_cache.get(control_cache_key)
            if control_points is None:
                control_points = extract_control_points(
                    bundle_path,
                    anim_name,
                    names=[
                        "controlpoint_fx",
                        "controlpoint_accessory",
                        "controlpoint_effectcenter",
                    ],
                )
                self._dof_control_point_cache[control_cache_key] = control_points
            if source_nodes is None:
                source_nodes = extract_source_nodes(bundle_path, anim_name)
                self._dof_source_node_cache[control_cache_key] = source_nodes
            cached = self._build_particle_entries(
                nodes,
                library,
                animation,
                control_points=control_points,
                source_nodes=source_nodes,
            )
            self._dof_particle_entry_cache[cache_key] = cached
            self.log_widget.log(
                f"DOF particles: loaded {len(cached)} emitters for {anim_name}.",
                "INFO",
            )
        # Particle nodes are authored in Unity Y-up space; respect the DOF animation's
        # flip flag so gravity/velocity behave correctly in the viewer's Y-down space.
        particle_flip_y = bool(getattr(animation, "dof_anim_flip_y", False))
        self.gl_widget.set_particle_entries(cached, flip_y=particle_flip_y)

    def _scan_bundle_for_anim_name(self, anim_name: str) -> Optional[str]:
        if not self.dof_path or UnityPy is None:
            return None
        target = anim_name.lower()
        data_files = self._find_unity_bundle_data_files(self.dof_path)
        for data_path in data_files:
            try:
                env = UnityPy.load(data_path)
            except Exception:
                continue
            for obj in env.objects:
                if getattr(obj.type, "name", None) != "MonoBehaviour":
                    continue
                try:
                    data = obj.read()
                    name = (getattr(data, "m_Name", "") or "").lower()
                except Exception:
                    continue
                if name == target or name.endswith(target):
                    return os.path.normpath(data_path)
        return None

    def _build_dof_audio_name_candidates(
        self,
        animation_name: str,
        monster_token: Optional[str],
    ) -> List[str]:
        candidates: List[str] = []
        name_norm = self._normalize_audio_key(animation_name)
        if not name_norm:
            return candidates
        parts = [part for part in name_norm.split("_") if part]
        if len(parts) >= 5 and parts[2] in {"adult", "baby", "blue", "green", "orange", "purple", "red", "yellow"}:
            monster = "_".join(parts[:2])
            island = "_".join(parts[3:-1]) if len(parts) > 4 else ""
            variant = parts[-1]
            if island:
                candidates.append(f"{island}_{monster}_{variant}")
                candidates.append(f"{island}_{monster}")
        if monster_token:
            candidates.append(monster_token)
        return candidates

    @staticmethod
    def _normalize_audio_key(value: str) -> str:
        """Normalize strings so they can be matched against music filenames."""
        if not value:
            return ""
        base = os.path.splitext(os.path.basename(value))[0]
        base = base.replace('-', '_').replace(' ', '_').lower()
        base = re.sub(r'[^0-9a-z_]+', '', base)
        base = re.sub(r'_+', '_', base)
        return base.strip('_')

    def _build_audio_name_candidates(
        self,
        animation_name: str,
        *,
        monster_token: Optional[str] = None
    ) -> List[str]:
        """
        Build a list of plausible filename bases for a given animation name.
        This yields the raw values we try before normalizing them.
        """
        candidates: List[str] = []
        seen: Set[str] = set()

        def add(value: str):
            value = value.strip()
            if value and value not in seen:
                seen.add(value)
                candidates.append(value)

        normalized_path = animation_name.replace("\\", "/").strip()
        add(normalized_path)

        parts = [segment for segment in normalized_path.split("/") if segment]
        for part in reversed(parts):
            add(part)

        if len(parts) >= 2:
            add(f"{parts[-2]}_{parts[-1]}")

        if monster_token:
            token_clean = monster_token.strip()
            add(token_clean)
            add(f"monster_{token_clean}")
            add(f"{token_clean}_monster")
            if parts:
                add(f"{token_clean}_{parts[-1]}")
                add(f"{parts[-1]}_{token_clean}")
        snapshot = list(candidates)
        for value in snapshot:
            stripped = re.sub(r'^[0-9]+[_-]*', '', value)
            if stripped:
                add(stripped)
            trimmed_suffix = re.sub(r'[_-]*[0-9]+$', '', value)
            if trimmed_suffix:
                add(trimmed_suffix)
            # Some assets prefix the filename with an extra digit (e.g., 117- vs 17-).
            if value and value[0].isdigit():
                add(f"1{value}")
            if monster_token:
                add(f"{monster_token}_{value}")
                add(f"{value}_{monster_token}")

        extra = self._build_downloads_audio_candidates(animation_name)
        for value in extra:
            add(value)

        return candidates

    def _build_downloads_audio_candidates(self, animation_name: str) -> List[str]:
        """Build extra audio filename guesses for downloads-style assets."""
        if not animation_name or not self.current_json_path:
            return []
        stem = Path(self.current_json_path).stem
        if not stem.lower().startswith("monster_"):
            return []
        stem_body = stem[len("monster_"):]
        match = re.search(r"_island(\d+)$", stem_body, re.IGNORECASE)
        if not match:
            return []
        island = match.group(1)
        token = stem_body[: match.start()]
        token = token.strip("_")
        if not token:
            return []
        track = None
        lead_match = re.match(r"^(\d+)", animation_name.strip())
        if lead_match:
            track = lead_match.group(1)
        if not track:
            tail_match = re.search(r"(\d+)$", animation_name.strip())
            if tail_match:
                track = tail_match.group(1)
        candidates: List[str] = []
        if track:
            candidates.extend(
                [
                    f"{island}-{token}_Monster_{track}",
                    f"{island}-{token}-Monster-{track}",
                    f"{island}_{token}_Monster_{track}",
                ]
            )
        else:
            candidates.extend([f"{island}-{token}", f"{island}_{token}"])
        return candidates

    def _expand_audio_key_variants(self, base_key: str) -> List[str]:
        """
        Expand a normalized key into additional variants by removing numeric prefixes,
        suffixes, and common descriptors. This helps match inconsistent naming.
        """
        variants: List[str] = []
        synonym_map = {
            'min': ['minor'],
            'maj': ['major'],
        }

        def add(value: str):
            value = value.strip('_')
            if value and value not in variants:
                variants.append(value)
                add_synonym_variants(value)

        def add_synonym_variants(value: str):
            tokens_local = [token for token in value.split('_') if token]
            if not tokens_local:
                return
            for idx, token in enumerate(tokens_local):
                if token in synonym_map:
                    for alt in synonym_map[token]:
                        new_tokens = list(tokens_local)
                        new_tokens[idx] = alt
                        add('_'.join(new_tokens))

        add(base_key)
        if not base_key:
            return variants

        tokens = [token for token in base_key.split('_') if token]
        if not tokens:
            return variants

        # Remove numeric prefixes
        prefix_tokens = tokens[:]
        while prefix_tokens and prefix_tokens[0].isdigit():
            prefix_tokens = prefix_tokens[1:]
            if prefix_tokens:
                add('_'.join(prefix_tokens))

        # Remove numeric suffixes
        suffix_tokens = tokens[:]
        while suffix_tokens and suffix_tokens[-1].isdigit():
            suffix_tokens = suffix_tokens[:-1]
            if suffix_tokens:
                add('_'.join(suffix_tokens))

        # Remove rarity descriptors at the front
        descriptor_prefix = tokens[:]
        while descriptor_prefix and descriptor_prefix[0] in {'common', 'rare', 'epic'}:
            descriptor_prefix = descriptor_prefix[1:]
            if descriptor_prefix:
                add('_'.join(descriptor_prefix))

        # Remove descriptors at the end (loop, intro, idle, song)
        descriptor_suffix = tokens[:]
        while descriptor_suffix and descriptor_suffix[-1] in {'loop', 'intro', 'idle', 'song'}:
            descriptor_suffix = descriptor_suffix[:-1]
            if descriptor_suffix:
                add('_'.join(descriptor_suffix))

        # Add each individual token and pairs for broader matching
        for token in tokens:
            add(token)

        if len(tokens) >= 2:
            for idx in range(len(tokens) - 1):
                pair = '_'.join(tokens[idx:idx + 2])
                add(pair)

        return variants

    def _lookup_buddy_audio(self, animation_name: str) -> Optional[str]:
        """Return an audio path resolved via the buddy manifests, if available."""
        if not animation_name:
            return None
        direct = self.buddy_audio_tracks.get(animation_name)
        if direct:
            return direct
        normalized = self._normalize_audio_key(animation_name)
        if normalized:
            return self.buddy_audio_tracks_normalized.get(normalized)
        return None

    def _is_buddy_audio_blocked(self, animation_name: str) -> bool:
        if not animation_name:
            return False
        if animation_name in self.buddy_audio_blocked_tracks:
            return True
        normalized = self._normalize_audio_key(animation_name)
        return bool(normalized and normalized in self.buddy_audio_blocked_tracks_normalized)

    def _should_attempt_fuzzy_audio(self, animation_name: str) -> bool:
        """
        Determine whether fuzzy audio matching should be attempted for a particular
        animation. Idle/dance/pose style animations typically have no audio, so we
        avoid auto-matching clips for them.
        """
        # Keep fuzzy behavior aligned with the hard non-DOF audio blocks.
        if self._should_skip_non_dof_audio(animation_name):
            return False

        normalized = self._normalize_audio_key(animation_name)
        if "dance" in normalized:
            # Dance names are too collision-prone for fuzzy matching; only allow
            # explicit/direct resolution paths.
            return False

        tokens = set(self._audio_key_tokens(normalized))
        if not tokens:
            return True

        allow_tokens = {
            "song", "sing", "singer", "verse", "chorus", "vox", "vocal",
            "music", "melody", "lead", "track", "performance"
        }
        block_tokens = {
            "idle", "idle1", "idle2", "idleloop", "pose", "breath",
            "blink", "walk", "pace", "cam", "camera", "intro", "outro",
            "celebrate", "gesture", "sleep", "stand", "rest", "sit", "hype",
            "emote", "store", "shop", "market"
        }

        if tokens & allow_tokens:
            return True
        if tokens & block_tokens:
            return False
        return True

    def _should_skip_non_dof_audio(self, animation_name: str) -> bool:
        """
        Hard block audio lookup for non-DOF utility animations that should be silent.
        Exception: monster_f dance animations still use audio.
        """
        normalized = self._normalize_audio_key(animation_name)
        tokens = set(self._audio_key_tokens(normalized))
        if not tokens:
            return False

        always_silent = {
            "sleep", "sleeping", "idle", "idle1", "idle2", "idleloop",
            "inactive", "store", "shop", "market", "stand", "rest",
        }
        if tokens & always_silent:
            return True

        # Dance clips should be silent unless this is monster_f.
        if "dance" not in normalized:
            return False

        monster_token = self._normalize_audio_key(self._current_monster_token() or "")
        is_monster_f = monster_token == "f"
        if not is_monster_f and normalized:
            name_for_inference = normalized
            if name_for_inference.startswith("monster_"):
                name_for_inference = name_for_inference[len("monster_"):]
            # Only the canonical "f_dance_*" naming should be treated as
            # the monster_f exception. Variants like "f_epic_dance_*" stay blocked.
            is_monster_f = bool(re.match(r"^(?:\d{2}_)?f_dance_\d+$", name_for_inference))
        return not is_monster_f

    def _should_use_monster_f_activate_sfx(self, animation_name: str) -> bool:
        """Use box opening SFX on activate animations for monster_f / monster_o assets."""
        normalized = self._normalize_audio_key(animation_name)
        if "activate" not in normalized:
            return False
        if not self.current_json_path:
            return False
        stem = Path(self.current_json_path).stem.lower()
        return stem.startswith("monster_f") or stem.startswith("monster_o")

    def _resolve_sfx_clip_path(self, clip_stem: str) -> Optional[str]:
        """Resolve a clip basename from audio/sfx roots (downloads first)."""
        if not clip_stem:
            return None

        sfx_dirs: List[str] = []
        seen_dirs: Set[str] = set()

        def add_dir(path: str) -> None:
            if not path or not os.path.isdir(path):
                return
            norm = os.path.normcase(os.path.normpath(path))
            if norm in seen_dirs:
                return
            seen_dirs.add(norm)
            sfx_dirs.append(path)

        if self.downloads_path:
            add_dir(os.path.join(self.downloads_path, "audio", "sfx"))
            add_dir(os.path.join(self.downloads_path, "data", "audio", "sfx"))
        if self.game_path:
            add_dir(os.path.join(self.game_path, "data", "audio", "sfx"))
            add_dir(os.path.join(self.game_path, "audio", "sfx"))

        for sfx_dir in sfx_dirs:
            for ext in (".wav", ".ogg", ".mp3"):
                candidate = os.path.normpath(os.path.join(sfx_dir, clip_stem + ext))
                if os.path.exists(candidate):
                    return candidate
        return None

    def _fuzzy_audio_library_lookup(
        self,
        normalized_keys: List[str],
        *,
        monster_token: Optional[str],
        animation_name: str,
        library: Optional[Dict[str, List[str]]] = None,
        source_label: str = "library",
    ) -> Optional[str]:
        """
        Attempt to match an animation's audio using relaxed token comparisons so
        we can handle clips whose filenames only loosely resemble the animation id.
        """
        active_library = library if library is not None else self.audio_library
        if not active_library:
            return None

        candidate_entries: List[Tuple[str, Set[str]]] = []
        seen_candidates: Set[str] = set()
        for key in normalized_keys:
            if not key or key in seen_candidates:
                continue
            seen_candidates.add(key)
            tokens = set(self._audio_key_tokens(key))
            if tokens:
                candidate_entries.append((key, tokens))
        if not candidate_entries:
            return None

        monster_tokens: Set[str] = set()
        if monster_token:
            normalized_monster = self._normalize_audio_key(monster_token)
            monster_tokens = set(self._audio_key_tokens(normalized_monster))

        best_score = 0.0
        best_path: Optional[str] = None
        best_key: Optional[str] = None
        token_cache: Dict[str, Set[str]] = {}

        for lib_key, paths in active_library.items():
            if not paths:
                continue
            cached = token_cache.get(lib_key)
            if cached is None:
                cached = set(self._audio_key_tokens(lib_key))
                token_cache[lib_key] = cached
            if not cached:
                continue

            for candidate_key, candidate_tokens in candidate_entries:
                overlap = cached & candidate_tokens
                meaningful_overlap = [tok for tok in overlap if not tok.isdigit()]
                if not meaningful_overlap:
                    continue
                overlap_score = sum(self._audio_token_weight(tok) for tok in overlap)
                similarity = difflib.SequenceMatcher(None, candidate_key, lib_key).ratio()
                score = overlap_score + (similarity * 0.75)

                if candidate_tokens <= cached:
                    score += 0.3
                if cached <= candidate_tokens:
                    score += 0.2
                if monster_tokens:
                    if monster_tokens <= cached:
                        score += 0.4
                    elif monster_tokens & cached:
                        score += 0.2
                if len(overlap) >= 2:
                    score += 0.25 * (len(overlap) - 1)

                if score > best_score:
                    best_score = score
                    best_path = paths[0]
                    best_key = lib_key

        if best_path and best_score >= 1.75:
            rel_path = best_path
            if self.game_path:
                try:
                    rel_path = os.path.relpath(
                        best_path,
                        os.path.join(self.game_path, "data")
                    )
                except ValueError:
                    rel_path = best_path
            self.log_widget.log(
                f"Audio fallback matched '{animation_name}' -> '{rel_path}' via fuzzy tokens (score {best_score:.2f})",
                "INFO"
            )
            if source_label:
                self.log_widget.log(
                    f"Audio source: {source_label}",
                    "DEBUG"
                )
            return best_path
        return None

    @staticmethod
    def _audio_key_tokens(value: Optional[str]) -> List[str]:
        if not value:
            return []
        return [token for token in value.split('_') if token]

    @staticmethod
    def _audio_numeric_tokens(value: Optional[str]) -> Set[str]:
        if not value:
            return set()
        tokens = re.findall(r"\d+", value)
        normalized: Set[str] = set()
        for token in tokens:
            stripped = token.lstrip("0")
            normalized.add(stripped if stripped else "0")
        return normalized

    @staticmethod
    def _audio_token_weight(token: str) -> float:
        if not token:
            return 0.0
        lowered = token.lower()
        if lowered.isdigit():
            return 0.05
        low_signal = {
            "monster", "song", "loop", "intro", "outro", "idle", "verse",
            "chorus", "vox", "vocal", "voice", "mix", "stem", "track",
            "main", "alt", "part", "rare", "epic", "common"
        }
        if lowered in low_signal:
            return 0.2
        if len(lowered) == 1:
            return 0.35
        return 1.0
    
    def update_layer_panel(self):
        """Update the layer visibility panel"""
        animation = self.gl_widget.player.animation
        self._reset_layer_thumbnail_cache()
        if animation:
            self.layer_panel.set_default_hidden_layers(self._default_hidden_layer_ids)
            self.layer_panel.update_layers(animation.layers)
            variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
            self.layer_panel.set_layers_with_sprite_variants(variant_layers)
            self.layer_panel.set_selection_state(self.selected_layer_ids)
            self._refresh_constraints_ui()
            if self.joint_solver_enabled:
                self.gl_widget.capture_joint_rest_lengths()
            self._refresh_layer_thumbnails()
        else:
            self.layer_panel.set_default_hidden_layers(set())
            self.layer_panel.update_layers([])
            self.layer_panel.set_layers_with_sprite_variants(set())
            self.layer_panel.set_selection_state(set())

    def _sync_layer_constraint_toggles(self) -> None:
        """Update per-layer constraint toggles from stored disable list."""
        if not hasattr(self, "gl_widget") or not self.gl_widget:
            return
        animation = self.gl_widget.player.animation
        if not animation or not hasattr(self, "layer_panel"):
            return
        disabled = {name.lower() for name in self.constraint_manager.disabled_layer_names}
        for layer in animation.layers:
            enabled = layer.name.lower() not in disabled if layer.name else True
            self.layer_panel.set_layer_constraint_enabled(layer.layer_id, enabled)

    def _refresh_constraints_ui(self) -> None:
        """Refresh constraint list and toggles."""
        layer_map: Dict[int, LayerData] = {}
        animation = getattr(self.gl_widget.player, "animation", None) if hasattr(self, "gl_widget") else None
        if animation:
            layer_map = {layer.layer_id: layer for layer in animation.layers}
        entries: List[Tuple[str, bool, str]] = []
        for spec in self.constraints:
            label = self.constraint_manager.describe(spec, layer_map)
            entries.append((spec.cid, bool(spec.enabled), label))
        self.control_panel.update_constraints_list(entries)
        self._sync_layer_constraint_toggles()

    def _reset_layer_thumbnail_cache(self):
        """Clear cached sprite previews so rows rebuild cleanly."""
        self._layer_thumbnail_cache.clear()
        self._atlas_image_cache.clear()
        self._layer_sprite_preview_state.clear()
        if hasattr(self, "layer_panel") and self.layer_panel:
            self.layer_panel.clear_layer_thumbnails()

    # --- Sprite workshop helpers -------------------------------------------------

    def _atlas_cache_key(self, atlas: TextureAtlas) -> Optional[str]:
        """Return a stable identifier for an atlas image."""
        path = getattr(atlas, "image_path", None)
        if path:
            return os.path.normcase(os.path.abspath(path))
        source = getattr(atlas, "source_name", None)
        if source:
            return source.lower()
        return None

    def _atlas_display_name(self, atlas: TextureAtlas) -> str:
        """Friendly label for an atlas."""
        if getattr(atlas, "source_name", None):
            return atlas.source_name
        path = getattr(atlas, "image_path", None)
        if path:
            return os.path.basename(path)
        return f"Atlas {id(atlas)}"

    def _sprite_workshop_key(self, atlas: TextureAtlas, sprite_name: str) -> Tuple[str, str]:
        """Return a dict key for sprite replacement bookkeeping."""
        atlas_key = self._atlas_cache_key(atlas) or f"atlas_{id(atlas)}"
        return (atlas_key, sprite_name.lower())

    def _ensure_mutable_atlas_bitmap(self, atlas: TextureAtlas) -> Optional[Image.Image]:
        """Return a mutable PIL image for an atlas, cloning the source if needed."""
        key = self._atlas_cache_key(atlas)
        if not key:
            return None
        image = self._atlas_modified_images.get(key)
        if image is not None:
            return image
        base = self._load_atlas_image(atlas)
        if base is None:
            return None
        mutable = base.copy()
        self._atlas_modified_images[key] = mutable
        return mutable

    def _original_atlas_bitmap(self, atlas: TextureAtlas) -> Optional[Image.Image]:
        """Return the pristine atlas bitmap saved when it was first loaded."""
        key = self._atlas_cache_key(atlas)
        if not key:
            return None
        original = self._atlas_original_image_cache.get(key)
        if original is not None:
            return original
        active = self._load_atlas_image(atlas)
        if active is None:
            return None
        backup = active.copy()
        self._atlas_original_image_cache[key] = backup
        return backup

    def _extract_sprite_bitmap(self, atlas: TextureAtlas, sprite: SpriteInfo) -> Optional[Image.Image]:
        """Return a PIL image for a sprite, un-rotated for editing."""
        atlas_image = self._load_atlas_image(atlas)
        if not atlas_image:
            return None
        box = (
            int(sprite.x),
            int(sprite.y),
            int(sprite.x + sprite.w),
            int(sprite.y + sprite.h),
        )
        cropped = atlas_image.crop(box)
        if sprite.rotated:
            cropped = cropped.rotate(90, expand=True)
        return cropped

    def _apply_color_factors_to_image(
        self,
        image: Image.Image,
        factors: Tuple[float, float, float],
    ) -> Image.Image:
        """Return a copy of ``image`` with RGB multiplied by the supplied factors."""
        if image is None:
            return image
        r_mul, g_mul, b_mul = factors
        if all(abs(val - 1.0) <= 1e-4 for val in (r_mul, g_mul, b_mul)):
            return image
        data = np.asarray(image.convert("RGBA"), dtype=np.float32)
        data[..., 0] = np.clip(data[..., 0] * r_mul, 0, 255)
        data[..., 1] = np.clip(data[..., 1] * g_mul, 0, 255)
        data[..., 2] = np.clip(data[..., 2] * b_mul, 0, 255)
        baked = Image.fromarray(data.astype(np.uint8), "RGBA")
        return baked

    def _collect_active_recolor_maps(self) -> Dict[str, Dict[str, Tuple[str, Tuple[float, float, float]]]]:
        """
        Build a lookup of atlas->(sprite name -> (original name, RGB multipliers)).

        Returns:
            Dict mapping atlas cache keys to dicts keyed by lowercase sprite name.
        """
        player = getattr(self.gl_widget, "player", None)
        renderer = getattr(self.gl_widget, "renderer", None)
        if not player or not renderer or not player.animation:
            return {}
        sample_time = getattr(player, "current_time", 0.0)
        atlas_maps: Dict[str, Dict[str, Tuple[str, Tuple[float, float, float]]]] = {}
        for layer in player.animation.layers:
            if not layer.visible:
                continue
            state = player.get_layer_state(layer, sample_time)
            sprite_name = state.get('sprite_name')
            if not sprite_name:
                continue
            sprite, atlas = self._find_sprite_in_atlases(sprite_name)
            if not sprite or not atlas:
                continue
            atlas_key = self._atlas_cache_key(atlas)
            if not atlas_key:
                continue
            r = int(state.get('r', 255))
            g = int(state.get('g', 255))
            b = int(state.get('b', 255))
            tint = renderer._resolve_layer_color(layer, sample_time)
            if tint:
                try:
                    tr, tg, tb, _ = tint
                except (TypeError, ValueError):
                    tr = tg = tb = 1.0
                r = int(max(0, min(255, round(r * tr))))
                g = int(max(0, min(255, round(g * tg))))
                b = int(max(0, min(255, round(b * tb))))
            factors = (
                max(0.0, min(1.0, r / 255.0)),
                max(0.0, min(1.0, g / 255.0)),
                max(0.0, min(1.0, b / 255.0)),
            )
            if all(abs(val - 1.0) <= 1e-4 for val in factors):
                continue
            atlas_entry = atlas_maps.setdefault(atlas_key, {})
            key = sprite.name.lower()
            atlas_entry.setdefault(key, (sprite.name, factors))
        return atlas_maps

    def _lookup_recolor_entry(
        self,
        mapping: Dict[str, Tuple[str, Tuple[float, float, float]]],
        sprite_name: str,
    ) -> Optional[Tuple[str, Tuple[float, float, float]]]:
        if not mapping or not sprite_name:
            return None
        return mapping.get(sprite_name.lower())

    def _apply_recolor_map_to_sheet(
        self,
        sheet_image: Image.Image,
        atlas: TextureAtlas,
        tint_lookup: Dict[str, Tuple[str, Tuple[float, float, float]]],
    ) -> int:
        """Apply recolor multipliers to atlas regions in-place."""
        if sheet_image is None or not tint_lookup:
            return 0
        tinted = 0
        for _, (sprite_name, factors) in tint_lookup.items():
            sprite = atlas.sprites.get(sprite_name)
            if not sprite:
                continue
            region = (
                int(sprite.x),
                int(sprite.y),
                int(sprite.x + sprite.w),
                int(sprite.y + sprite.h),
            )
            patch = sheet_image.crop(region)
            recolored = self._apply_color_factors_to_image(patch, factors)
            if recolored:
                sheet_image.paste(recolored, (region[0], region[1]))
                tinted += 1
        return tinted

    def _coerce_patch_dimensions(
        self,
        image: Image.Image,
        expected_size: Tuple[int, int],
        sprite_name: str,
        tolerance: int = 2,
    ) -> Tuple[Optional[Image.Image], Optional[str]]:
        """Return a copy sized to the expected dimensions within a pixel tolerance."""
        width, height = image.size
        target_w, target_h = expected_size
        if width == target_w and height == target_h:
            return image, None
        within_tolerance = (
            abs(width - target_w) <= tolerance
            and abs(height - target_h) <= tolerance
        )
        if within_tolerance:
            canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            crop_box = (0, 0, min(width, target_w), min(height, target_h))
            canvas.paste(image.crop(crop_box), (0, 0))
            return canvas, None
        return None, (
            f"Sprite '{sprite_name}' expects {target_w}x{target_h} pixels (±{tolerance}), "
            f"but got {width}x{height}."
        )

    def _prepare_patch_for_sprite(
        self,
        sprite: SpriteInfo,
        edited_image: Image.Image,
    ) -> Tuple[Optional[Image.Image], Optional[str]]:
        """Return an atlas-oriented patch for a sprite edit, or an error message."""
        image = edited_image.convert("RGBA")
        expected_w = int(sprite.w)
        expected_h = int(sprite.h)
        tolerance_px = 2
        if sprite.rotated:
            expected_input = (int(sprite.h), int(sprite.w))
            normalized, error = self._coerce_patch_dimensions(
                image,
                expected_input,
                sprite.name,
                tolerance=tolerance_px,
            )
            if error:
                return None, error
            patch = normalized.rotate(-90, expand=True)
        else:
            normalized, error = self._coerce_patch_dimensions(
                image,
                (expected_w, expected_h),
                sprite.name,
                tolerance=tolerance_px,
            )
            if error:
                return None, error
            patch = normalized
        patch = patch.crop((0, 0, expected_w, expected_h))
        return patch, None

    def _upload_sprite_patch(self, atlas: TextureAtlas, sprite: SpriteInfo, patch: Image.Image):
        """Upload a sprite region patch to the GPU texture."""
        if not patch:
            return
        if not atlas.texture_id:
            self.gl_widget.makeCurrent()
            try:
                atlas.load_texture()
            finally:
                self.gl_widget.doneCurrent()
        if not atlas.texture_id:
            return
        arr = np.array(patch, dtype=np.float32) / 255.0
        alpha = arr[..., 3:4]
        arr[..., :3] *= alpha
        arr = (arr * 255.0).astype(np.uint8)
        self.gl_widget.makeCurrent()
        try:
            glBindTexture(GL_TEXTURE_2D, atlas.texture_id)
            glTexSubImage2D(
                GL_TEXTURE_2D,
                0,
                int(sprite.x),
                int(sprite.y),
                patch.width,
                patch.height,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                arr.tobytes(),
            )
        finally:
            self.gl_widget.doneCurrent()

    def _apply_sprite_patch(
        self,
        atlas: TextureAtlas,
        sprite: SpriteInfo,
        patch: Image.Image,
        source_path: Optional[str] = None,
    ) -> bool:
        """Paste a prepared patch into the atlas bitmap and upload it."""
        atlas_bitmap = self._ensure_mutable_atlas_bitmap(atlas)
        if atlas_bitmap is None or patch is None:
            return False
        atlas_bitmap.paste(patch, (int(sprite.x), int(sprite.y)))
        self._upload_sprite_patch(atlas, sprite, patch)
        key = self._sprite_workshop_key(atlas, sprite.name)
        atlas_key = key[0]
        self._atlas_dirty_flags[atlas_key] = True
        self._sprite_replacements[key] = SpriteReplacementRecord(
            atlas_key=atlas_key,
            sprite_name=sprite.name,
            source_path=os.path.abspath(source_path) if source_path else None,
            applied_at=datetime.now().isoformat(timespec="seconds"),
        )
        self._reset_layer_thumbnail_cache()
        self.gl_widget.update()
        return True

    def replace_sprite_from_file(
        self,
        atlas: TextureAtlas,
        sprite: SpriteInfo,
        file_path: str,
    ) -> Tuple[bool, str]:
        """Replace a sprite region with pixels loaded from disk."""
        try:
            edited = Image.open(file_path).convert("RGBA")
        except Exception as exc:
            return False, f"Failed to load image: {exc}"
        patch, error = self._prepare_patch_for_sprite(sprite, edited)
        if error:
            return False, error
        if not self._apply_sprite_patch(atlas, sprite, patch, source_path=file_path):
            return False, "Unable to update atlas texture."
        self.log_widget.log(
            f"Sprite '{sprite.name}' updated using '{os.path.basename(file_path)}'.",
            "SUCCESS",
        )
        return True, ""

    def remove_sprite_replacement(self, atlas: TextureAtlas, sprite: SpriteInfo) -> bool:
        """Restore a sprite region to its original pixels."""
        key = self._sprite_workshop_key(atlas, sprite.name)
        if key not in self._sprite_replacements:
            return False
        original = self._original_atlas_bitmap(atlas)
        target = self._ensure_mutable_atlas_bitmap(atlas)
        if original is None or target is None:
            return False
        region = (
            int(sprite.x),
            int(sprite.y),
            int(sprite.x + sprite.w),
            int(sprite.y + sprite.h),
        )
        patch = original.crop(region)
        target.paste(patch, (region[0], region[1]))
        self._upload_sprite_patch(atlas, sprite, patch)
        del self._sprite_replacements[key]
        atlas_key = key[0]
        still_dirty = any(k[0] == atlas_key for k in self._sprite_replacements.keys())
        if not still_dirty:
            self._atlas_dirty_flags.pop(atlas_key, None)
        self._reset_layer_thumbnail_cache()
        self.gl_widget.update()
        self.log_widget.log(
            f"Sprite '{sprite.name}' restored to atlas defaults.",
            "INFO",
        )
        return True

    def is_sprite_modified(self, atlas: TextureAtlas, sprite_name: str) -> bool:
        """Return True if a sprite currently has an override applied."""
        key = self._sprite_workshop_key(atlas, sprite_name)
        return key in self._sprite_replacements

    def sprite_preview_pixmap(self, atlas: TextureAtlas, sprite: SpriteInfo, max_edge: int = 256) -> Optional[QPixmap]:
        """Return a scaled pixmap preview for workshop UI."""
        image = self._extract_sprite_bitmap(atlas, sprite)
        if not image:
            return None
        pixmap = self._pil_image_to_qpixmap(image)
        if pixmap is None:
            return None
        if max(pixmap.width(), pixmap.height()) > max_edge:
            pixmap = pixmap.scaled(
                max_edge,
                max_edge,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pixmap

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Return a filesystem-safe version of a sprite or atlas label."""
        safe = re.sub(r"[^0-9a-zA-Z_.-]+", "_", name or "").strip("_")
        return safe or "sprite"

    def export_sprite_segments(
        self,
        atlas: TextureAtlas,
        sprite_names: List[str],
        destination: str,
        bake_recolor: bool = False,
        premultiply_alpha: bool = False,
    ) -> Tuple[bool, str]:
        """Export selected sprites as standalone PNGs plus a manifest."""
        if not sprite_names:
            sprite_names = sorted(atlas.sprites.keys())
        if not sprite_names:
            return False, "Atlas has no sprites to export."
        atlas_label = self._atlas_display_name(atlas)
        atlas_dir = os.path.join(destination, self._sanitize_filename(atlas_label))
        os.makedirs(atlas_dir, exist_ok=True)
        recolor_maps: Dict[str, Dict[str, Tuple[str, Tuple[float, float, float]]]] = {}
        active_tints: Dict[str, Tuple[str, Tuple[float, float, float]]] = {}
        atlas_key = self._atlas_cache_key(atlas)
        if bake_recolor:
            recolor_maps = self._collect_active_recolor_maps()
            if atlas_key:
                active_tints = recolor_maps.get(atlas_key, {})
            if not active_tints:
                self.log_widget.log(
                    "Recolor bake requested but no active tint data matched this atlas. Exporting base pixels.",
                    "WARNING",
                )
        manifest_entries: List[Dict[str, Any]] = []
        exported = 0
        tinted_sprites = 0
        for name in sprite_names:
            sprite = atlas.sprites.get(name)
            if not sprite:
                continue
            image = self._extract_sprite_bitmap(atlas, sprite)
            if not image:
                continue
            if bake_recolor and active_tints:
                entry = self._lookup_recolor_entry(active_tints, sprite.name)
                if entry:
                    _, factors = entry
                    image = self._apply_color_factors_to_image(image, factors)
                    tinted_sprites += 1
            if premultiply_alpha:
                image = self._premultiply_image(image)
            filename = f"{self._sanitize_filename(sprite.name)}.png"
            export_path = os.path.join(atlas_dir, filename)
            image.save(export_path, "PNG")
            exported += 1
            manifest_entries.append(
                {
                    "name": sprite.name,
                    "file": filename,
                    "size": [image.width, image.height],
                    "atlas_region": {
                        "x": int(sprite.x),
                        "y": int(sprite.y),
                        "w": int(sprite.w),
                        "h": int(sprite.h),
                        "rotated": bool(sprite.rotated),
                    },
                    "offset": [sprite.offset_x, sprite.offset_y],
                    "original_size": [sprite.original_w, sprite.original_h],
                    "pivot": [sprite.pivot_x, sprite.pivot_y],
                }
            )
        if exported == 0:
            return False, "No sprites could be exported."
        manifest = {
            "atlas": atlas_label,
            "image_size": [atlas.image_width, atlas.image_height],
            "sprites": manifest_entries,
        }
        manifest_path = os.path.join(atlas_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        self.log_widget.log(
            f"Exported {exported} sprite{'s' if exported != 1 else ''} from {atlas_label} to {atlas_dir}.",
            "SUCCESS",
        )
        if tinted_sprites:
            self.log_widget.log(
                f"Baked recolor data into {tinted_sprites} sprite{'s' if tinted_sprites != 1 else ''}.",
                "INFO",
            )
        return True, atlas_dir

    def _build_atlas_xml_tree(self, atlas: TextureAtlas, image_name: str) -> ET.ElementTree:
        """Return an ElementTree representing the atlas layout."""
        root = ET.Element(
            "TextureAtlas",
            {
                "imagePath": image_name,
                "width": str(atlas.image_width),
                "height": str(atlas.image_height),
            },
        )
        if atlas.is_hires:
            root.set("hires", "true")
        for sprite in sorted(atlas.sprites.values(), key=lambda info: info.name.lower()):
            elem = ET.SubElement(
                root,
                "sprite",
                {
                    "n": sprite.name,
                    "x": str(int(sprite.x)),
                    "y": str(int(sprite.y)),
                    "w": str(int(sprite.w)),
                    "h": str(int(sprite.h)),
                    "pX": f"{float(sprite.pivot_x):.6f}",
                    "pY": f"{float(sprite.pivot_y):.6f}",
                    "oX": f"{float(sprite.offset_x):.6f}",
                    "oY": f"{float(sprite.offset_y):.6f}",
                    "oW": f"{float(sprite.original_w):.6f}",
                    "oH": f"{float(sprite.original_h):.6f}",
                },
            )
            if sprite.rotated:
                elem.set("r", "y")
            if sprite.vertices:
                verts = " ".join(f"{x:.6f} {y:.6f}" for x, y in sprite.vertices)
                ET.SubElement(elem, "vertices").text = verts
            if sprite.vertices_uv:
                # Convert normalized UV data back to pixel coordinates.
                uv_pairs = []
                for u, v in sprite.vertices_uv:
                    uv_pairs.append(f"{u * atlas.image_width:.6f} {v * atlas.image_height:.6f}")
                ET.SubElement(elem, "verticesUV").text = " ".join(uv_pairs)
            if sprite.triangles:
                ET.SubElement(elem, "triangles").text = " ".join(str(idx) for idx in sprite.triangles)
        return ET.ElementTree(root)

    def export_modified_spritesheet(
        self,
        atlas: TextureAtlas,
        output_png: str,
        bake_recolor: bool = False,
        premultiply_alpha: bool = False,
    ) -> Tuple[bool, str]:
        """Write the current atlas bitmap plus an updated XML manifest."""
        os.makedirs(os.path.dirname(output_png) or ".", exist_ok=True)
        key = self._atlas_cache_key(atlas)
        atlas_image = self._atlas_modified_images.get(key) or self._load_atlas_image(atlas)
        if atlas_image is None:
            return False, "Unable to load atlas pixels."
        recolor_maps: Dict[str, Dict[str, Tuple[str, Tuple[float, float, float]]]] = {}
        active_tints: Dict[str, Tuple[str, Tuple[float, float, float]]] = {}
        sheet_image = atlas_image
        tinted_patches = 0
        if bake_recolor:
            recolor_maps = self._collect_active_recolor_maps()
            if key:
                active_tints = recolor_maps.get(key, {})
            if active_tints:
                sheet_image = atlas_image.copy()
                tinted_patches = self._apply_recolor_map_to_sheet(sheet_image, atlas, active_tints)
            else:
                self.log_widget.log(
                    "Recolor bake requested but no active tint data matched this atlas. Exporting base pixels.",
                    "WARNING",
                )
        if premultiply_alpha:
            if sheet_image is atlas_image:
                sheet_image = atlas_image.copy()
            sheet_image = self._premultiply_image(sheet_image)
        sheet_image.save(output_png, "PNG")
        xml_output = os.path.splitext(output_png)[0] + ".xml"
        existing_xml = getattr(atlas, "xml_path", None)
        xml_tree: Optional[ET.ElementTree] = None
        if existing_xml and os.path.exists(existing_xml):
            try:
                xml_tree = ET.parse(existing_xml)
                xml_root = xml_tree.getroot()
                xml_root.set("imagePath", os.path.basename(output_png))
                xml_root.set("width", str(atlas.image_width))
                xml_root.set("height", str(atlas.image_height))
                if atlas.is_hires:
                    xml_root.set("hires", "true")
            except Exception:
                xml_tree = None
        if xml_tree is None:
            xml_tree = self._build_atlas_xml_tree(atlas, os.path.basename(output_png))
        xml_tree.write(xml_output, encoding="utf-8", xml_declaration=True)
        self.log_widget.log(
            f"Exported spritesheet '{self._atlas_display_name(atlas)}' to {output_png} and {xml_output}.",
            "SUCCESS",
        )
        if tinted_patches:
            self.log_widget.log(
                f"Baked recolors into {tinted_patches} atlas region{'s' if tinted_patches != 1 else ''}.",
                "INFO",
            )
        return True, xml_output

    def import_spritesheet_into_atlas(
        self,
        atlas: TextureAtlas,
        image_path: str,
        xml_path: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Apply a spritesheet + XML manifest onto an existing atlas."""
        if not atlas:
            return False, "No atlas is currently active."
        if not image_path or not os.path.exists(image_path):
            return False, "Spritesheet image could not be found."
        inferred_xml = xml_path or os.path.splitext(image_path)[0] + ".xml"
        if not os.path.exists(inferred_xml):
            return False, "Matching spritesheet XML could not be found."

        import_atlas = TextureAtlas()
        atlas_root = os.path.dirname(image_path) or "."
        if not import_atlas.load_from_xml(inferred_xml, atlas_root):
            return False, "Failed to parse spritesheet XML."

        try:
            with Image.open(import_atlas.image_path) as raw_sheet:
                sheet_image = raw_sheet.convert("RGBA")
        except Exception as exc:
            return False, f"Failed to open spritesheet image: {exc}"

        imported = 0
        skipped: List[str] = []
        for sprite_name, source_sprite in import_atlas.sprites.items():
            target_sprite = atlas.sprites.get(sprite_name)
            if not target_sprite:
                continue
            region = (
                int(source_sprite.x),
                int(source_sprite.y),
                int(source_sprite.x + source_sprite.w),
                int(source_sprite.y + source_sprite.h),
            )
            try:
                patch = sheet_image.crop(region)
            except Exception:
                skipped.append(sprite_name)
                continue
            expected_size = (int(target_sprite.w), int(target_sprite.h))
            if patch.size != expected_size:
                skipped.append(sprite_name)
                continue
            if not self._apply_sprite_patch(atlas, target_sprite, patch, source_path=image_path):
                skipped.append(sprite_name)
                continue
            imported += 1

        if imported == 0:
            return False, "No matching sprites from the spritesheet could be imported."

        if skipped:
            self.log_widget.log(
                f"Imported {imported} sprite(s) from {os.path.basename(image_path)} "
                f"(skipped {len(skipped)} mismatch(es)).",
                "WARNING",
            )
        else:
            self.log_widget.log(
                f"Imported {imported} sprite(s) from {os.path.basename(image_path)}.",
                "SUCCESS",
            )
        return True, ""

    def get_sprite_workshop_entries(self) -> List[Dict[str, Any]]:
        """Return a deduplicated list of atlases that can be edited."""
        entries: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for atlas in self._iter_active_atlases():
            key = self._atlas_cache_key(atlas) or f"atlas_{id(atlas)}"
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "key": key,
                    "label": self._atlas_display_name(atlas),
                    "atlas": atlas,
                    "sprite_count": len(atlas.sprites),
                    "modified": sum(
                        1 for sprite_key in self._sprite_replacements.keys() if sprite_key[0] == key
                    ),
                }
            )
        return entries

    def list_sprites_for_atlas(self, atlas: TextureAtlas) -> List[SpriteInfo]:
        """Return sprites sorted alphabetically for UI display."""
        return sorted(atlas.sprites.values(), key=lambda sprite: sprite.name.lower())

    def show_sprite_workshop(self):
        """Display the Sprite Workshop dialog."""
        if not list(self._iter_active_atlases()):
            QMessageBox.information(
                self,
                "Sprite Workshop",
                "Load an animation first so its sprites can be customized.",
            )
            return
        if not self._sprite_workshop_dialog:
            self._sprite_workshop_dialog = SpriteWorkshopDialog(self)
        self._sprite_workshop_dialog.refresh_entries()
        self._sprite_workshop_dialog.show()
        self._sprite_workshop_dialog.raise_()
        self._sprite_workshop_dialog.activateWindow()

    def show_midi_editor(self):
        """Display the MSM MIDI editor dialog."""
        initial_path = self._resolve_midi_path_for_current_animation()
        dialog = MidiEditorDialog(self, initial_path=initial_path)
        dialog.exec()

    def _apply_solid_bg_color(self, rgba: Tuple[int, int, int, int], *, announce: bool):
        """Persist and apply the active viewport/export background color."""
        r = max(0, min(255, int(rgba[0])))
        g = max(0, min(255, int(rgba[1])))
        b = max(0, min(255, int(rgba[2])))
        a = max(0, min(255, int(rgba[3])))
        self.solid_bg_color = (r, g, b, a)
        self.settings.setValue('export/solid_bg_color', self._rgba_to_hex(self.solid_bg_color))
        self.settings.setValue('viewport/background_color', self._rgba_to_hex(self.solid_bg_color))
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_background_color_rgba(self.solid_bg_color)
        if announce:
            self.log_widget.log(
                f"Background color set to {self._rgba_to_hex(self.solid_bg_color)}.",
                "SUCCESS",
            )

    @staticmethod
    def _rgba_to_hex(rgba: Tuple[int, int, int, int]) -> str:
        r, g, b, a = rgba
        return f"#{r:02X}{g:02X}{b:02X}{a:02X}"

    @staticmethod
    def _parse_rgba_hex(value: str, fallback: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        text = (value or "").strip().lstrip("#")
        if len(text) == 6:
            text += "FF"
        if len(text) != 8:
            return fallback
        try:
            r = int(text[0:2], 16)
            g = int(text[2:4], 16)
            b = int(text[4:6], 16)
            a = int(text[6:8], 16)
            return (r, g, b, a)
        except ValueError:
            return fallback

    def _active_background_color(self) -> Optional[Tuple[int, int, int, int]]:
        """Return the currently configured export background color, if enabled."""
        if getattr(self, "export_include_viewport_background", False):
            return None
        if getattr(self, "solid_bg_enabled", False):
            return getattr(self, "solid_bg_color", (0, 0, 0, 255))
        return None

    @staticmethod
    def _normalize_viewport_bg_color_mode(mode: Optional[str]) -> str:
        allowed = {
            "none",
            "replace",
            "overlay_normal",
            "overlay_multiply",
            "overlay_screen",
            "overlay_add",
            "overlay_hue",
            "overlay_color",
        }
        normalized = str(mode or "none").strip().lower()
        return normalized if normalized in allowed else "none"

    def _suggest_unused_background_color(self) -> Optional[Tuple[int, int, int, int]]:
        """Return a color that does not appear in the active atlases, if possible."""
        atlas_arrays: List[np.ndarray] = []
        for atlas in self._iter_active_atlases():
            image = self._load_atlas_image(atlas)
            if image is None:
                continue
            try:
                atlas_arrays.append(np.asarray(image, dtype=np.uint8))
            except Exception:
                continue
        if not atlas_arrays:
            return (255, 0, 255, 255)

        def color_exists(rgb: Tuple[int, int, int]) -> bool:
            target = np.array(rgb, dtype=np.uint8)
            for arr in atlas_arrays:
                if arr.ndim < 3 or arr.shape[2] < 3:
                    continue
                if np.any(np.all(arr[..., :3] == target, axis=-1)):
                    return True
            return False

        preferred_colors = [
            (255, 0, 255),
            (0, 255, 0),
            (0, 255, 255),
            (255, 255, 0),
            (0, 128, 255),
            (255, 128, 0),
            (0, 255, 180),
            (255, 0, 180),
        ]
        for rgb in preferred_colors:
            if not color_exists(rgb):
                return (rgb[0], rgb[1], rgb[2], 255)

        rng = random.Random()
        rng.seed(len(atlas_arrays))
        for _ in range(96):
            rgb = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
            if not color_exists(rgb):
                return (rgb[0], rgb[1], rgb[2], 255)
        return None

    def _refresh_layer_thumbnails(self):
        """Update per-layer sprite thumbnails based on the current time."""
        if not hasattr(self, "layer_panel") or not self.layer_panel:
            return
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            self.layer_panel.clear_layer_thumbnails()
            self._layer_sprite_preview_state.clear()
            return
        current_time = self.gl_widget.player.current_time
        for layer in animation.layers:
            if layer.layer_id is None:
                continue
            sprite_name = ""
            tint = (255, 255, 255, 255)
            try:
                state = self.gl_widget.player.get_layer_state(layer, current_time)
                sprite_name = state.get("sprite_name") or ""
                tint = (
                    int(state.get("r", 255)),
                    int(state.get("g", 255)),
                    int(state.get("b", 255)),
                    int(state.get("a", 255)),
                )
            except Exception:
                sprite_name = ""
            preview_key = (sprite_name, *tint)
            previous = self._layer_sprite_preview_state.get(layer.layer_id)
            if previous == preview_key:
                continue
            pixmap = self._get_layer_thumbnail_pixmap(sprite_name, tint=tint)
            self.layer_panel.set_layer_thumbnail(layer.layer_id, pixmap)
            self._layer_sprite_preview_state[layer.layer_id] = preview_key
        self._refresh_timeline_lane_thumbnails()

    def _refresh_timeline_lane_thumbnails(self) -> None:
        if not hasattr(self, "timeline"):
            return
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            return
        current_time = self.gl_widget.player.current_time
        for layer in animation.layers:
            if layer.layer_id is None:
                continue
            sprite_name = ""
            tint = (255, 255, 255, 255)
            try:
                state = self.gl_widget.player.get_layer_state(layer, current_time)
                sprite_name = state.get("sprite_name") or ""
                tint = (
                    int(state.get("r", 255)),
                    int(state.get("g", 255)),
                    int(state.get("b", 255)),
                    int(state.get("a", 255)),
                )
            except Exception:
                sprite_name = ""
            pixmap = self._get_layer_thumbnail_pixmap(sprite_name, tint=tint)
            self.timeline.set_group_thumbnail(("layer", layer.layer_id), pixmap)
        global_lanes = getattr(animation, "global_keyframe_lanes", []) or []
        if global_lanes:
            self.timeline.set_group_thumbnail(("global", -1), None)

    def _get_layer_thumbnail_pixmap(
        self,
        sprite_name: Optional[str],
        *,
        tint: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[QPixmap]:
        """Return a cached pixmap for a sprite, loading it if necessary."""
        if not sprite_name:
            return None
        tint_key = None
        if tint is not None:
            r, g, b, a = (max(0, min(255, int(val))) for val in tint)
            if (r, g, b, a) != (255, 255, 255, 255):
                tint_key = (r, g, b, a)
        cache_key = sprite_name if tint_key is None else (sprite_name, tint_key)
        if cache_key in self._layer_thumbnail_cache:
            return self._layer_thumbnail_cache[cache_key]
        resolved = self._resolve_sprite_asset(sprite_name)
        if not resolved:
            self._layer_thumbnail_cache[cache_key] = None
            return None
        sprite, atlas = resolved
        atlas_image = self._load_atlas_image(atlas)
        if atlas_image is None:
            self._layer_thumbnail_cache[cache_key] = None
            return None
        crop_box = (sprite.x, sprite.y, sprite.x + sprite.w, sprite.y + sprite.h)
        try:
            sprite_image = atlas_image.crop(crop_box)
        except Exception:
            self._layer_thumbnail_cache[cache_key] = None
            return None
        if getattr(sprite, "rotated", False):
            sprite_image = sprite_image.rotate(90, expand=True)
        if tint_key is not None:
            sprite_image = self._apply_sprite_tint(sprite_image, tint_key)
        pixmap = self._pil_image_to_qpixmap(sprite_image)
        self._layer_thumbnail_cache[cache_key] = pixmap
        return pixmap

    def _apply_sprite_tint(
        self,
        sprite_image: Image.Image,
        tint: Tuple[int, int, int, int],
    ) -> Image.Image:
        """Apply the per-layer color multiplier to a sprite thumbnail."""
        r, g, b, a = tint
        if (r, g, b, a) == (255, 255, 255, 255):
            return sprite_image
        if sprite_image.mode != "RGBA":
            sprite_image = sprite_image.convert("RGBA")
        tint_image = Image.new("RGBA", sprite_image.size, (r, g, b, a))
        try:
            return ImageChops.multiply(sprite_image, tint_image)
        except Exception:
            return sprite_image

    def _pil_image_to_qpixmap(self, image: Optional[Image.Image]) -> Optional[QPixmap]:
        """Convert a PIL Image into a QPixmap without relying on ImageQt (Pillow 10+ compatibility)."""
        if image is None:
            return None
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        width, height = image.size
        if width == 0 or height == 0:
            return None
        try:
            buffer = image.tobytes("raw", "BGRA")
            qimage = QImage(buffer, width, height, QImage.Format.Format_ARGB32)
            return QPixmap.fromImage(qimage.copy())
        except Exception:
            return None

    def _load_atlas_image(self, atlas: TextureAtlas) -> Optional[Image.Image]:
        """Load and cache the atlas image backing a sprite."""
        path = getattr(atlas, "image_path", None)
        if not path:
            return None
        key = os.path.normcase(os.path.abspath(path))
        modified = self._atlas_modified_images.get(key)
        if modified is not None:
            return modified
        cached = self._atlas_image_cache.get(key)
        if cached is not None:
            return cached
        try:
            source = atlas._load_texture_image(path)
            atlas_image = source.convert("RGBA")
            atlas_image.load()
            if getattr(atlas, "force_unpremultiply", False):
                looks_premultiplied = False
                try:
                    looks_premultiplied = bool(atlas._image_looks_premultiplied(atlas_image))
                except Exception:
                    looks_premultiplied = True
                if looks_premultiplied:
                    atlas_image = self._unpremultiply_image(atlas_image)
        except Exception as exc:
            self._atlas_image_cache[key] = None
            self.log_widget.log(
                f"Failed to load atlas preview '{os.path.basename(path)}': {exc}",
                "WARNING",
            )
            return None
        if key not in self._atlas_original_image_cache:
            self._atlas_original_image_cache[key] = atlas_image.copy()
        self._atlas_image_cache[key] = atlas_image
        return atlas_image

    def _resolve_sprite_asset(
        self, sprite_name: str
    ) -> Optional[Tuple[Any, TextureAtlas]]:
        """Locate the sprite/atlas pair for a given sprite name."""
        for atlas in self._iter_active_atlases():
            sprite = atlas.get_sprite(sprite_name)
            if sprite:
                return sprite, atlas
        return None

    def _iter_active_atlases(self):
        """Yield every atlas currently assigned to the renderer."""
        seen: Set[int] = set()
        for atlas in getattr(self.gl_widget, "texture_atlases", []) or []:
            if atlas and id(atlas) not in seen:
                seen.add(id(atlas))
                yield atlas
        overrides = getattr(self.gl_widget, "layer_atlas_overrides", {}) or {}
        for chain in overrides.values():
            if not chain:
                continue
            for atlas in chain:
                if atlas and id(atlas) not in seen:
                    seen.add(id(atlas))
                    yield atlas

    def _restore_sprite_workshop_edits(self) -> None:
        """Reupload Sprite Workshop overrides after atlases are rebuilt."""
        if not self._sprite_replacements:
            return
        atlas_map: Dict[str, List[TextureAtlas]] = {}
        for atlas in self._iter_active_atlases():
            key = self._atlas_cache_key(atlas)
            if key:
                atlas_map.setdefault(key, []).append(atlas)
        if not atlas_map:
            return
        for (atlas_key, sprite_name) in list(self._sprite_replacements.keys()):
            patched_bitmap = self._atlas_modified_images.get(atlas_key)
            if patched_bitmap is None:
                continue
            atlases = atlas_map.get(atlas_key)
            if not atlases:
                continue
            for atlas in atlases:
                sprite = atlas.sprites.get(sprite_name)
                if not sprite:
                    continue
                region = (
                    int(sprite.x),
                    int(sprite.y),
                    int(sprite.x + sprite.w),
                    int(sprite.y + sprite.h),
                )
                try:
                    patch = patched_bitmap.crop(region)
                except Exception:
                    continue
                self._upload_sprite_patch(atlas, sprite, patch)

    # ------------------------------------------------------------------ #
    # Sprite assignment helpers
    # ------------------------------------------------------------------ #

    def _atlas_for_layer(self, layer: LayerData) -> Optional[TextureAtlas]:
        """Return the atlas associated with a layer."""
        source_entry = self.layer_source_lookup.get(layer.layer_id)
        if source_entry:
            src_key = source_entry.get("src")
            if src_key in self.source_atlas_lookup:
                return self.source_atlas_lookup[src_key]
            if isinstance(src_key, str):
                atlas = self.source_atlas_lookup.get(src_key.lower())
                if atlas:
                    return atlas
        for keyframe in layer.keyframes:
            name = keyframe.sprite_name
            if not name:
                continue
            _, atlas = self._find_sprite_in_atlases(name)
            if atlas:
                return atlas
        return None

    def _collect_available_sprites_for_layers(self, layers: List[LayerData]) -> List[str]:
        """Return a sorted list of sprite names available for the provided layers."""
        sprites: Set[str] = set()
        for layer in layers:
            atlas = self._atlas_for_layer(layer)
            if not atlas:
                continue
            sprites.update(atlas.sprites.keys())
        return sorted(sprites, key=lambda value: value.lower())

    def _detect_layers_with_sprite_variants(self, layers: List[LayerData]) -> Set[int]:
        """Return layer ids whose keyframes already swap between multiple sprites."""
        variant_ids: Set[int] = set()
        for layer in layers:
            if layer.layer_id is None:
                continue
            sprite_names = {
                frame.sprite_name
                for frame in layer.keyframes
                if frame.sprite_name
            }
            if len(sprite_names) > 1:
                variant_ids.add(layer.layer_id)
        return variant_ids

    def _gather_keyframes_for_times(
        self,
        layers: List[LayerData],
        times: List[float],
    ) -> List[Tuple[LayerData, KeyframeData]]:
        """Return keyframes from layers that fall on the provided timestamps."""
        tolerance = self._marker_time_tolerance()
        matches: List[Tuple[LayerData, KeyframeData]] = []
        seen: Set[Tuple[int, float]] = set()
        for layer in layers:
            for frame in layer.keyframes:
                for target in times:
                    if abs(frame.time - target) <= tolerance:
                        key = (layer.layer_id, frame.time)
                        if key in seen:
                            continue
                        seen.add(key)
                        matches.append((layer, frame))
                        break
        return matches

    def _gather_keyframes_for_marker_refs(
        self,
        marker_refs: List[Tuple[TimelineLaneKey, float]],
    ) -> List[Tuple[TimelineLaneKey, Optional[LayerData], KeyframeData]]:
        """Return keyframes from specific lanes/times."""
        tolerance = self._marker_time_tolerance()
        matches: List[Tuple[TimelineLaneKey, Optional[LayerData], KeyframeData]] = []
        seen: Set[Tuple[str, int, int, float]] = set()
        for lane_key, time_value in marker_refs:
            keyframes = self._get_lane_keyframes(lane_key)
            if keyframes is None:
                continue
            layer = None
            if lane_key.scope == "layer":
                layer = self.gl_widget.get_layer_by_id(lane_key.layer_id)
            for frame in keyframes:
                if abs(frame.time - time_value) <= tolerance:
                    sig = (lane_key.scope, lane_key.layer_id, lane_key.lane_index, frame.time)
                    if sig in seen:
                        continue
                    seen.add(sig)
                    matches.append((lane_key, layer, frame))
        return matches

    def assign_sprite_to_keyframes(self, layer_ids: Optional[List[int]] = None):
        """Assign a sprite name to the selected keyframes."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            self.log_widget.log("Load an animation before assigning sprites.", "WARNING")
            return

        explicit_layers = layer_ids is not None
        if explicit_layers:
            requested_ids = {
                layer_id for layer_id in (layer_ids or [])
                if layer_id is not None
            }
        elif self.selected_layer_ids:
            requested_ids = set(self.selected_layer_ids)
        else:
            requested_ids = set()

        if requested_ids:
            target_layers = [
                layer for layer in animation.layers
                if layer.layer_id in requested_ids
            ]
            if explicit_layers and not target_layers:
                self.log_widget.log("Selected layer is unavailable for sprite assignment.", "WARNING")
                return
        else:
            target_layers = list(animation.layers)

        if not target_layers:
            self.log_widget.log("Select at least one layer to assign sprites.", "INFO")
            return
        selected_refs = sorted(
            self._selected_marker_refs,
            key=lambda item: (item[0].scope, item[0].layer_id, item[0].lane_index, item[1]),
        )
        implicit_time = False
        if selected_refs:
            lane_matches = self._gather_keyframes_for_marker_refs(selected_refs)
        else:
            implicit_time = True
            target_times = [float(self.gl_widget.player.current_time)]
            base_matches = self._gather_keyframes_for_times(target_layers, target_times)
            lane_matches = [
                (TimelineLaneKey("layer", layer.layer_id, 0), layer, frame)
                for layer, frame in base_matches
            ]

        if not lane_matches:
            if implicit_time:
                self.log_widget.log(
                    "No keyframes found at the current time. Select keyframe markers on the timeline first.",
                    "INFO",
                )
            else:
                self.log_widget.log("No keyframes matched the selected markers.", "INFO")
            return

        unique_layers: List[LayerData] = []
        seen_layers: Set[int] = set()
        for _lane_key, layer, _frame in lane_matches:
            if not layer or layer.layer_id in seen_layers:
                continue
            seen_layers.add(layer.layer_id)
            unique_layers.append(layer)
        if not unique_layers:
            unique_layers = list(target_layers)
        sprite_options = self._collect_available_sprites_for_layers(unique_layers)
        if not sprite_options:
            self.log_widget.log(
                "Could not locate any sprites for the selected layers' atlases.",
                "WARNING",
            )
            return
        atlas_labels: Set[str] = set()
        for layer in unique_layers:
            atlas = self._atlas_for_layer(layer)
            if not atlas:
                continue
            label = getattr(atlas, "source_name", None)
            if label:
                atlas_labels.add(label)
        description = None
        if atlas_labels:
            if len(atlas_labels) == 1:
                only = next(iter(atlas_labels))
                description = f"Sprites from {only} ({len(sprite_options)} available)."
            else:
                joined = ", ".join(sorted(atlas_labels))
                description = f"Sprites from {joined} ({len(sprite_options)} available)."
        sprite_entries: List[Tuple[str, Optional[QPixmap]]] = []
        for name in sprite_options:
            pixmap = self._get_layer_thumbnail_pixmap(name)
            sprite_entries.append((name, pixmap))
        current_sprite = next((frame.sprite_name for _, _layer, frame in lane_matches if frame.sprite_name), None)
        picker = SpritePickerDialog(
            sprite_entries,
            current_sprite=current_sprite,
            description=description,
            parent=self,
        )
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        sprite_name = picker.selected_sprite()
        if not sprite_name:
            return
        layer_ids = sorted({layer.layer_id for _lane_key, layer, _ in lane_matches if layer is not None})
        include_global = any(lane_key.scope == "global" for lane_key, _layer, _frame in lane_matches)
        if not layer_ids and not include_global:
            self.log_widget.log("Unable to determine which layers to update.", "ERROR")
            return
        self._begin_keyframe_action(layer_ids, include_global=include_global)
        touched_layers: Set[int] = set()
        for lane_key, layer, frame in lane_matches:
            if frame.sprite_name == sprite_name:
                continue
            frame.sprite_name = sprite_name
            if frame.immediate_sprite <= 0:
                frame.immediate_sprite = 1
            if layer and self._is_base_lane(lane_key):
                touched_layers.add(layer.layer_id)
        for layer_id in touched_layers:
            synced_layer = self.gl_widget.get_layer_by_id(layer_id)
            if synced_layer:
                self._sync_layer_source_frames(synced_layer)
        self._finalize_keyframe_action("assign_sprite")
        if touched_layers:
            self.gl_widget.update()
            self._refresh_layer_thumbnails()
            variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
            self.layer_panel.set_layers_with_sprite_variants(variant_layers)
            self.log_widget.log(
                f"Assigned sprite '{sprite_name}' to {len(lane_matches)} keyframe(s).",
                "SUCCESS",
            )
        else:
            self.log_widget.log("The selected keyframes already use that sprite.", "INFO")

    def toggle_layer_visibility(self, layer: LayerData, state: int):
        """Toggle layer visibility"""
        layer.visible = (state == Qt.CheckState.Checked.value)
        self._remember_layer_visibility(layer)
        self.gl_widget.update()

    def reset_layer_visibility_to_default(self):
        """Restore all layer visibilities to their recorded defaults."""
        animation = self.gl_widget.player.animation
        if not animation or not self._default_layer_visibility:
            return
        cache_key = self._current_json_cache_key()
        if cache_key and cache_key in self.layer_visibility_cache:
            self.layer_visibility_cache.pop(cache_key, None)
        for layer in animation.layers:
            if layer.layer_id in self._default_layer_visibility:
                layer.visible = self._default_layer_visibility[layer.layer_id]
        self.layer_panel.set_default_hidden_layers(self._default_hidden_layer_ids)
        self.layer_panel.update_layers(animation.layers)
        variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
        self.layer_panel.set_layers_with_sprite_variants(variant_layers)
        self.layer_panel.set_selection_state(self.selected_layer_ids)
        self._reset_layer_thumbnail_cache()
        self._refresh_layer_thumbnails()
        self.gl_widget.update()

    def reset_layer_order_to_default(self):
        """Restore layer ordering to the recorded default order."""
        animation = self.gl_widget.player.animation
        if not animation or not self._default_layer_order:
            return
        id_to_layer = {layer.layer_id: layer for layer in animation.layers}
        new_layers: List[LayerData] = []
        for layer_id in self._default_layer_order:
            layer = id_to_layer.get(layer_id)
            if layer:
                new_layers.append(layer)
        for layer_id, layer in id_to_layer.items():
            if layer_id not in self._default_layer_order:
                new_layers.append(layer)
        if not new_layers:
            return
        animation.layers = new_layers
        self.layer_panel.update_layers(animation.layers)
        variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
        self.layer_panel.set_layers_with_sprite_variants(variant_layers)
        self.layer_panel.set_selection_state(self.selected_layer_ids)
        self._reset_layer_thumbnail_cache()
        self._refresh_layer_thumbnails()
        self.gl_widget.update()

    def on_layer_order_changed(self, ordered_ids: List[int]):
        """Reorder animation layers to match the drag/drop order from the UI."""
        animation = self.gl_widget.player.animation
        if not animation:
            return
        current_layers = animation.layers or []
        if len(ordered_ids) != len(current_layers):
            return
        id_to_layer = {layer.layer_id: layer for layer in current_layers}
        try:
            new_layers = [id_to_layer[layer_id] for layer_id in ordered_ids]
        except KeyError:
            return
        if new_layers == current_layers:
            return
        animation.layers = new_layers
        self.layer_panel.update_layers(animation.layers)
        variant_layers = self._detect_layers_with_sprite_variants(animation.layers)
        self.layer_panel.set_layers_with_sprite_variants(variant_layers)
        self.layer_panel.set_selection_state(self.selected_layer_ids)
        self._reset_layer_thumbnail_cache()
        self._refresh_layer_thumbnails()
        self.gl_widget.update()
    
    def update_timeline(self):
        """Update timeline slider range"""
        if self.gl_widget.player.animation:
            duration = self.gl_widget.player.duration
            slider_max = max(1, int(duration * 1000))
            self.timeline.set_slider_maximum(slider_max)
            self.timeline.set_time_label(f"{self.gl_widget.player.current_time:.2f} / {duration:.2f}s")
            self.timeline.set_current_time(self.gl_widget.player.current_time)
            self._refresh_timeline_keyframes()
            self._update_timeline_beat_display()
            self._refresh_timeline_beats()
        else:
            self.timeline.set_slider_maximum(1)
            self.timeline.set_time_label("0.00 / 0.00s")
            self.timeline.set_current_time(0.0)
            self.timeline.set_lane_groups([])
            self._selected_marker_refs = set()
            self.timeline.set_beat_markers([], 0.0)
            self._tempo_segments = []
            self.timeline.set_beat_bpm_display(None, False)
            self.timeline.set_beat_markers([], 0.0)

    def _refresh_timeline_keyframes(self):
        """Update timeline markers to reflect current keyframes."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            self.timeline.set_lane_groups([])
            return
        duration = max(0.0, self.gl_widget.player.duration)
        if self.selected_layer_ids:
            target_ids = set(self.selected_layer_ids)
        else:
            target_ids = {layer.layer_id for layer in animation.layers}

        groups: List[TimelineGroupSpec] = []

        global_lanes = getattr(animation, "global_keyframe_lanes", []) or []
        global_specs: List[TimelineLaneSpec] = []
        for idx, lane in enumerate(global_lanes):
            label = (lane.name or f"Global {idx + 1}").strip() or f"Global {idx + 1}"
            markers = sorted({max(0.0, float(kf.time)) for kf in (lane.keyframes or [])})
            global_specs.append(
                TimelineLaneSpec(TimelineLaneKey("global", -1, idx), label, markers, deletable=True)
            )
        groups.append(
            TimelineGroupSpec(("global", -1), "Global Keyframes", global_specs, addable=True)
        )

        for layer in animation.layers:
            if layer.layer_id not in target_ids:
                continue
            layer_label = layer.name or f"Layer {layer.layer_id}"
            layer_specs: List[TimelineLaneSpec] = []
            base_markers = sorted({max(0.0, float(kf.time)) for kf in (layer.keyframes or [])})
            layer_specs.append(
                TimelineLaneSpec(
                    TimelineLaneKey("layer", layer.layer_id, 0),
                    "Base",
                    base_markers,
                    deletable=False,
                )
            )
            extra_lanes = getattr(layer, "extra_keyframe_lanes", []) or []
            for idx, lane in enumerate(extra_lanes, start=1):
                label = (lane.name or f"Lane {idx}").strip() or f"Lane {idx}"
                markers = sorted({max(0.0, float(kf.time)) for kf in (lane.keyframes or [])})
                layer_specs.append(
                    TimelineLaneSpec(TimelineLaneKey("layer", layer.layer_id, idx), label, markers, deletable=True)
                )
            groups.append(
                TimelineGroupSpec(("layer", layer.layer_id), layer_label, layer_specs, addable=True)
            )

        self.timeline.set_timeline_duration(duration)
        self.timeline.set_lane_groups(groups)
        self._refresh_timeline_lane_thumbnails()

        available_markers: List[Tuple[TimelineLaneKey, float]] = []
        for group in groups:
            for lane in group.lanes:
                for time_value in lane.markers:
                    available_markers.append((lane.key, time_value))
        self._sync_marker_selection(available_markers)

        active_lane = self.timeline.get_active_lane()
        if active_lane is None or active_lane not in {lane.key for group in groups for lane in group.lanes}:
            for group in groups:
                if group.lanes:
                    self.timeline.set_active_lane(group.lanes[0].key)
                    break

    def _normalize_beat_sequence(self, beats: List[float], duration: float) -> List[float]:
        cleaned: List[float] = []
        seen = set()
        for value in beats or []:
            clamped = max(0.0, float(value))
            rounded = round(clamped, 6)
            if rounded in seen:
                continue
            cleaned.append(rounded)
            seen.add(rounded)
        cleaned.sort()
        if not cleaned:
            cleaned = [0.0]
        if cleaned[0] > 1e-4:
            cleaned[0] = 0.0
        else:
            cleaned[0] = 0.0
        if duration > 0.0:
            if not cleaned or abs(duration - cleaned[-1]) > 1e-4:
                cleaned.append(duration)
            else:
                cleaned[-1] = duration
        return cleaned

    def _sanitize_time_signature(self, numerator: Any, denominator: Any) -> Tuple[int, int]:
        try:
            num = int(numerator)
        except (TypeError, ValueError):
            num = 4
        if num <= 0:
            num = 4
        allowed = {1, 2, 4, 8, 16, 32}
        try:
            den = int(denominator)
        except (TypeError, ValueError):
            den = 4
        if den not in allowed:
            den = 4
        return num, den

    def _beat_marker_key(self) -> Optional[str]:
        token = self._current_monster_token()
        if not token or not self.current_animation_name:
            return None
        return f"{token}|{self.current_animation_name}"

    def _generate_default_beats(self, duration: float) -> List[float]:
        duration = max(0.0, float(duration))
        if duration <= 0.0 or self.current_bpm <= 0.0:
            return []
        note_scale = 4.0 / float(max(1, self.time_signature_denom))
        step = (60.0 / max(1e-3, float(self.current_bpm))) * note_scale
        beats: List[float] = []
        t = 0.0
        while t <= duration + 1e-5:
            beats.append(round(t, 6))
            t += step
        if not beats:
            beats = [0.0, duration] if duration > 0.0 else [0.0]
        return self._normalize_beat_sequence(beats, duration)

    def _resolve_active_beats(self, duration: float, regenerate: bool = False) -> List[float]:
        key = self._beat_marker_key()
        beats: Optional[List[float]] = None
        if key and not regenerate:
            beats = self.animation_beat_overrides.get(key)
        if beats is None or regenerate:
            beats = self._generate_default_beats(duration)
            if key:
                self.animation_beat_overrides[key] = list(beats)
                if regenerate:
                    self._beat_manual_overrides.discard(key)
        if not beats:
            return []
        clipped: List[float] = []
        for value in beats:
            if value < 0.0:
                continue
            if duration >= 0.0 and value > duration:
                continue
            if clipped and abs(value - clipped[-1]) <= 1e-4:
                continue
            clipped.append(round(value, 6))
        if not clipped and duration > 0.0:
            clipped = [0.0, duration]
        return clipped

    def _refresh_timeline_beats(self, force_regenerate: bool = False):
        player = getattr(self.gl_widget, "player", None)
        animation = getattr(player, "animation", None) if player else None
        duration = max(0.0, getattr(player, "duration", 0.0)) if player else 0.0
        self.timeline.set_beat_grid_visible(self.show_beat_grid)
        self.timeline.set_beat_edit_enabled(self.show_beat_grid and self.allow_beat_edit)
        if not animation or not self.show_beat_grid or duration <= 0.0:
            self.timeline.set_beat_markers([], duration)
            self._tempo_segments = []
            self.timeline.set_beat_bpm_display(None, False)
            self._update_metronome_tempo_for_time()
            return
        key = self._beat_marker_key()
        regenerate = force_regenerate and (key not in self._beat_manual_overrides if key else True)
        beats = self._resolve_active_beats(duration, regenerate=regenerate)
        if key:
            self.animation_beat_overrides[key] = list(beats)
        self.timeline.set_beat_markers(beats, duration)
        self._rebuild_tempo_segments(beats, duration)
        self._update_timeline_beat_display()
        self._update_metronome_tempo_for_time()

    def _rebuild_tempo_segments(self, beats: List[float], duration: float):
        normalized = self._normalize_beat_sequence(beats, duration) if beats else [0.0, duration]
        segments: List[Tuple[float, float, float]] = []
        if not normalized or len(normalized) < 2:
            self._tempo_segments = segments
            return
        note_scale = 4.0 / float(max(1, self.time_signature_denom))
        for idx in range(len(normalized) - 1):
            start = normalized[idx]
            end = normalized[idx + 1]
            span = max(1e-4, end - start)
            bpm = (60.0 / span) * note_scale
            segments.append((start, end, bpm))
        self._tempo_segments = segments

    def _tempo_bpm_at_time(self, time_value: float) -> float:
        if self.show_beat_grid and self._tempo_segments:
            epsilon = 1e-4
            for start, end, bpm in self._tempo_segments:
                if start - epsilon <= time_value <= end + epsilon:
                    return bpm
        return self.current_bpm

    def _update_timeline_beat_display(self):
        if not hasattr(self, "timeline"):
            return
        if not self.show_beat_grid or not self._tempo_segments:
            self.timeline.set_beat_bpm_display(None, False)
            return
        current_time = getattr(self.gl_widget.player, "current_time", 0.0)
        bpm = self._tempo_bpm_at_time(current_time)
        self.timeline.set_beat_bpm_display(bpm, True)

    def _update_metronome_tempo_for_time(self, time_value: Optional[float] = None):
        if not hasattr(self, "metronome"):
            return
        player = getattr(self.gl_widget, "player", None)
        if time_value is None and player:
            time_value = getattr(player, "current_time", 0.0)
        elif time_value is None:
            time_value = 0.0
        bpm = self._tempo_bpm_at_time(time_value)
        if abs(bpm - self._active_metronome_bpm) > 1e-3:
            self._active_metronome_bpm = bpm
            self.metronome.set_bpm(bpm)

    def _marker_time_tolerance(self) -> float:
        return 1.0 / 600.0

    def _sync_marker_selection(self, available_markers: List[Tuple[TimelineLaneKey, float]]):
        if not hasattr(self, "timeline"):
            return
        tolerance = self._marker_time_tolerance()
        retained: List[Tuple[TimelineLaneKey, float]] = []
        for lane_key, selected_time in sorted(self._selected_marker_refs, key=lambda item: (item[0].scope, item[0].layer_id, item[0].lane_index, item[1])):
            match = next(
                (
                    marker
                    for marker in available_markers
                    if marker[0] == lane_key and abs(marker[1] - selected_time) <= tolerance
                ),
                None,
            )
            if match is not None and not any(
                existing[0] == match[0] and abs(existing[1] - match[1]) <= tolerance
                for existing in retained
            ):
                retained.append(match)
        self._selected_marker_refs = set(retained)
        self.timeline.set_marker_selection(retained)

    def _replace_marker_selection(self, markers: List[Tuple[TimelineLaneKey, float]]):
        normalized: List[Tuple[TimelineLaneKey, float]] = []
        tolerance = self._marker_time_tolerance()
        for lane_key, time_value in sorted(markers, key=lambda item: (item[0].scope, item[0].layer_id, item[0].lane_index, item[1])):
            clamped = max(0.0, float(time_value))
            if normalized and normalized[-1][0] == lane_key and abs(clamped - normalized[-1][1]) <= tolerance:
                continue
            normalized.append((lane_key, clamped))
        self._selected_marker_refs = set(normalized)
        if hasattr(self, "timeline"):
            self.timeline.set_marker_selection(normalized)

    def _remove_marker_selection_refs(self, markers: List[Tuple[TimelineLaneKey, float]]):
        if not self._selected_marker_refs:
            return
        tolerance = self._marker_time_tolerance()
        remaining: List[Tuple[TimelineLaneKey, float]] = []
        for existing in sorted(self._selected_marker_refs, key=lambda item: (item[0].scope, item[0].layer_id, item[0].lane_index, item[1])):
            if any(
                existing[0] == target[0] and abs(existing[1] - target[1]) <= tolerance
                for target in markers
            ):
                continue
            remaining.append(existing)
        self._selected_marker_refs = set(remaining)
        if hasattr(self, "timeline"):
            self.timeline.set_marker_selection(remaining)
    
    def _set_player_playing(self, playing: bool, *, sync_audio: bool = False) -> None:
        """Centralize updates to AnimationPlayer.playing."""
        player = getattr(self.gl_widget, "player", None)
        if not player:
            return
        new_state = bool(playing)
        previous = bool(player.playing)
        player.playing = new_state
        if sync_audio:
            self._sync_audio_playback(new_state)
        if previous != new_state:
            self._update_metronome_state()

    def toggle_playback(self):
        """Toggle animation playback"""
        new_state = not self.gl_widget.player.playing
        self._set_player_playing(new_state, sync_audio=True)
        self.timeline.set_play_button_text("Pause" if new_state else "Play")
    
    def toggle_loop(self, state: int):
        """Toggle animation looping"""
        self.gl_widget.player.loop = (state == Qt.CheckState.Checked.value)
    
    def on_timeline_changed(self, value: int):
        """Handle timeline slider change"""
        if not self.gl_widget.player.animation:
            return

        time = value / 1000.0
        self.gl_widget.set_time(time)

        if self.audio_manager.is_ready:
            self.audio_manager.seek(self._get_audio_sync_time(time))

        duration = self.gl_widget.player.duration
        self.timeline.set_time_label(f"{time:.2f} / {duration:.2f}s")
        self.timeline.set_current_time(time)
        self._refresh_layer_thumbnails()
        self._update_timeline_beat_display()
        self._update_metronome_tempo_for_time(time)

    def on_timeline_slider_pressed(self):
        """Mark that the user is scrubbing the timeline."""
        self._timeline_user_scrubbing = True
        if self.gl_widget.player.playing and self.audio_manager.is_ready:
            self._resume_audio_after_scrub = True
            self.audio_manager.pause()
        else:
            self._resume_audio_after_scrub = False

    def on_timeline_slider_released(self):
        """Resume playback if the user was scrubbing."""
        self._timeline_user_scrubbing = False
        if self._resume_audio_after_scrub and self.audio_manager.is_ready:
            self.audio_manager.play(self._get_audio_sync_time(self.gl_widget.player.current_time))
        self._resume_audio_after_scrub = False

    def on_keyframe_marker_clicked(self, time_value: float):
        """Jump to a keyframe marker when the user clicks the marker bar."""
        if not self.gl_widget.player.animation:
            return
        duration = max(0.0, self.gl_widget.player.duration)
        clamped = max(0.0, min(time_value, duration))
        slider = self.timeline.timeline_slider
        slider.blockSignals(True)
        slider.setValue(int(clamped * 1000))
        slider.blockSignals(False)
        self.gl_widget.set_time(clamped)
        if self.audio_manager.is_ready:
            self.audio_manager.seek(self._get_audio_sync_time(clamped))
        self.timeline.set_time_label(f"{clamped:.2f} / {duration:.2f}s")
        self.timeline.set_current_time(clamped)
        self._refresh_layer_thumbnails()

    def on_keyframe_marker_remove_requested(self, marker_refs: List[Tuple[TimelineLaneKey, float]]):
        """Remove keyframes shared at the given time."""
        animation = self.gl_widget.player.animation
        if not animation:
            return
        sanitized = sorted(
            {
                (lane_key, max(0.0, float(time_value)))
                for lane_key, time_value in (marker_refs or [])
            },
            key=lambda item: (item[0].scope, item[0].layer_id, item[0].lane_index, item[1]),
        )
        if not sanitized:
            return
        lane_times: Dict[TimelineLaneKey, List[float]] = {}
        for lane_key, time_value in sanitized:
            lane_times.setdefault(lane_key, []).append(time_value)

        layer_ids: Set[int] = set()
        include_global = False
        for lane_key in lane_times:
            if lane_key.scope == "global":
                include_global = True
            else:
                layer_ids.add(lane_key.layer_id)
        if not layer_ids and not include_global:
            return

        self._begin_keyframe_action(sorted(layer_ids), include_global=include_global)
        removed = 0
        tolerance = self._marker_time_tolerance()

        for lane_key, times in lane_times.items():
            keyframes = self._get_lane_keyframes(lane_key)
            if keyframes is None:
                continue
            original_count = len(keyframes)
            kept_frames = [
                frame for frame in keyframes
                if all(abs(frame.time - value) > tolerance for value in times)
            ]
            if len(kept_frames) != original_count:
                self._set_lane_keyframes(lane_key, kept_frames)
                removed += original_count - len(kept_frames)
                if self._is_base_lane(lane_key):
                    layer = self.gl_widget.get_layer_by_id(lane_key.layer_id)
                    if layer:
                        self._sync_layer_source_frames(layer)

        self._finalize_keyframe_action("delete_keyframe")
        if removed:
            self.gl_widget.player.calculate_duration()
            self.update_timeline()
            self.gl_widget.update()
            self.log_widget.log(f"Removed {removed} keyframe(s).", "SUCCESS")
            self._remove_marker_selection_refs(sanitized)
        else:
            self.log_widget.log("No keyframes found at the selected time to remove.", "INFO")

    def on_keyframe_marker_dragged(self, marker_refs: List[Tuple[TimelineLaneKey, float]], delta: float):
        """Move selected keyframes to a new timestamp by dragging markers."""
        animation = self.gl_widget.player.animation
        if not animation:
            return
        if not marker_refs:
            return
        if abs(delta) < 1e-6:
            return

        tolerance = self._marker_time_tolerance()
        duration = max(0.0, self.gl_widget.player.duration)
        lane_pairs: Dict[TimelineLaneKey, List[Tuple[float, float]]] = {}
        for lane_key, value in marker_refs:
            old_time = max(0.0, float(value))
            proposed = old_time + float(delta)
            new_time = min(max(proposed, 0.0), duration)
            lane_pairs.setdefault(lane_key, []).append((old_time, new_time))

        layer_ids: Set[int] = set()
        include_global = False
        for lane_key in lane_pairs:
            if lane_key.scope == "global":
                include_global = True
            else:
                layer_ids.add(lane_key.layer_id)
        if not layer_ids and not include_global:
            return

        self._begin_keyframe_action(sorted(layer_ids), include_global=include_global)
        moved = 0
        updated_markers: List[Tuple[TimelineLaneKey, float]] = []
        for lane_key, pairs in lane_pairs.items():
            keyframes = self._get_lane_keyframes(lane_key)
            if keyframes is None:
                continue
            updated = False
            for frame in keyframes:
                for old_time, target_time in pairs:
                    if abs(frame.time - old_time) <= tolerance:
                        frame.time = target_time
                        updated = True
                        updated_markers.append((lane_key, target_time))
            if updated:
                keyframes.sort(key=lambda frame: frame.time)
                self._set_lane_keyframes(lane_key, keyframes)
                if self._is_base_lane(lane_key):
                    layer = self.gl_widget.get_layer_by_id(lane_key.layer_id)
                    if layer:
                        self._sync_layer_source_frames(layer)
                moved += 1

        self._finalize_keyframe_action("move_keyframe")
        if moved:
            self.gl_widget.player.calculate_duration()
            self.update_timeline()
            self.gl_widget.update()
            self._replace_marker_selection(updated_markers)
            self.log_widget.log(
                f"Moved {sum(len(pairs) for pairs in lane_pairs.values())} keyframe time(s) by {delta:.3f}s",
                "SUCCESS",
            )
        else:
            self.log_widget.log("No keyframes moved for the selected lanes.", "INFO")

    def on_keyframe_selection_changed(self, selected_markers: List[Tuple[TimelineLaneKey, float]]):
        """Store the current marker selection from the timeline widget."""
        normalized: List[Tuple[TimelineLaneKey, float]] = []
        tolerance = self._marker_time_tolerance()
        for lane_key, value in sorted(
            selected_markers,
            key=lambda item: (item[0].scope, item[0].layer_id, item[0].lane_index, item[1]),
        ):
            clamped = max(0.0, float(value))
            if (
                normalized
                and normalized[-1][0] == lane_key
                and abs(clamped - normalized[-1][1]) <= tolerance
            ):
                continue
            normalized.append((lane_key, clamped))
        self._selected_marker_refs = set(normalized)

    def on_keyframe_lane_add_requested(self, group_key: Tuple[str, int]):
        """Add a new keyframe lane to a layer or the global group."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            return
        scope, layer_id = group_key
        if scope == "global":
            self._add_global_keyframe_lane(animation)
            lane_index = len(getattr(animation, "global_keyframe_lanes", [])) - 1
            lane_key = TimelineLaneKey("global", -1, max(0, lane_index))
        else:
            layer = self.gl_widget.get_layer_by_id(layer_id)
            if not layer:
                return
            self._add_layer_keyframe_lane(layer)
            lane_index = len(getattr(layer, "extra_keyframe_lanes", []))
            lane_key = TimelineLaneKey("layer", layer.layer_id, max(1, lane_index))
        self._refresh_timeline_keyframes()
        self.timeline.set_active_lane(lane_key)

    def on_keyframe_lane_remove_requested(self, lane_key: TimelineLaneKey):
        """Remove a keyframe lane from the timeline."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            return
        if lane_key.scope == "layer" and lane_key.lane_index == 0:
            self.log_widget.log("The base lane cannot be deleted.", "INFO")
            return

        affected_layers: List[int] = []
        removed_name = ""
        removed_keyframes = 0

        if lane_key.scope == "global":
            lanes = getattr(animation, "global_keyframe_lanes", []) or []
            if lane_key.lane_index < 0 or lane_key.lane_index >= len(lanes):
                return
            self._begin_keyframe_action(affected_layers, include_global=True)
            lane = lanes.pop(lane_key.lane_index)
            removed_name = lane.name or f"Global {lane_key.lane_index + 1}"
            removed_keyframes = len(lane.keyframes or [])
            remaining_lanes = lanes
        else:
            layer = self.gl_widget.get_layer_by_id(lane_key.layer_id)
            if not layer:
                return
            extra = getattr(layer, "extra_keyframe_lanes", []) or []
            lane_idx = lane_key.lane_index - 1
            if lane_idx < 0 or lane_idx >= len(extra):
                return
            affected_layers.append(layer.layer_id)
            self._begin_keyframe_action(affected_layers, include_global=False)
            lane = extra.pop(lane_idx)
            removed_name = lane.name or f"Lane {lane_key.lane_index}"
            removed_keyframes = len(lane.keyframes or [])
            remaining_lanes = extra

        self._finalize_keyframe_action("delete_keyframe_lane")

        self._selected_marker_refs = self._remap_marker_refs_after_lane_removal(lane_key)
        new_active = self._remap_active_lane_after_removal(lane_key)
        if new_active is None:
            if lane_key.scope == "global":
                if remaining_lanes:
                    idx = min(lane_key.lane_index, len(remaining_lanes) - 1)
                    new_active = TimelineLaneKey("global", -1, max(0, idx))
            else:
                idx = min(lane_key.lane_index, len(remaining_lanes))
                new_active = TimelineLaneKey("layer", lane_key.layer_id, max(0, idx))
        if new_active is not None:
            self.timeline.set_active_lane(new_active)
        self.gl_widget.player.calculate_duration()
        self.update_timeline()
        self._refresh_timeline_keyframes()
        self.gl_widget.update()
        label = removed_name or "Lane"
        self.log_widget.log(
            f"Removed {label} ({removed_keyframes} keyframe(s)).",
            "SUCCESS",
        )

    def on_beat_marker_dragged(self, index: int, new_time: float):
        """Handle beat grid adjustments coming from the timeline widget."""
        if not self.show_beat_grid:
            return
        player = getattr(self.gl_widget, "player", None)
        if not player or not getattr(player, "animation", None):
            return
        duration = max(0.0, player.duration)
        key = self._beat_marker_key()
        beats = []
        if key and key in self.animation_beat_overrides:
            beats = list(self.animation_beat_overrides[key])
        else:
            beats = self._resolve_active_beats(duration)
        if not beats or index < 0 or index >= len(beats):
            return
        new_time = max(0.0, min(float(new_time), duration))
        if index == 0:
            new_time = 0.0
        if index == len(beats) - 1:
            new_time = duration
        epsilon = 1e-4
        if index > 0:
            new_time = max(new_time, beats[index - 1] + epsilon)
        if index < len(beats) - 1:
            new_time = min(new_time, beats[index + 1] - epsilon)
        beats[index] = new_time
        if key:
            self.animation_beat_overrides[key] = list(beats)
            self._beat_manual_overrides.add(key)
        self.timeline.set_beat_markers(beats, duration)
        self._rebuild_tempo_segments(beats, duration)
        self._update_timeline_beat_display()
        self._update_metronome_tempo_for_time(new_time)
        self.log_widget.log(f"Beat {index + 1} adjusted to {new_time:.3f}s", "INFO")

    def _clipboard_layer_source_key(
        self,
        layer_id: Optional[int],
        source_entry: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Return a normalized identifier for a layer's source entry."""
        entry = source_entry if source_entry is not None else self.layer_source_lookup.get(layer_id)
        if not isinstance(entry, dict):
            return None
        parts: List[str] = []
        for key in ("src", "resource", "sheet", "node"):
            value = entry.get(key)
            if isinstance(value, str):
                stripped = value.strip().lower()
                if stripped:
                    parts.append(stripped)
        identifier = entry.get("id")
        if not parts and isinstance(identifier, (int, str)):
            parts.append(str(identifier).strip().lower())
        if not parts:
            return None
        return "::".join(parts)

    def copy_selected_keyframes(self):
        """Copy keyframes anchored at the currently selected marker times."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before copying keyframes.", "WARNING")
            return
        selected_markers = sorted(
            self._selected_marker_refs,
            key=lambda item: (item[0].scope, item[0].layer_id, item[0].lane_index, item[1]),
        )
        if not selected_markers:
            self.log_widget.log("Select keyframes in the timeline before copying.", "INFO")
            return
        tolerance = self._marker_time_tolerance()
        base_time = min(time for _, time in selected_markers)

        markers_by_lane: Dict[TimelineLaneKey, List[float]] = {}
        for lane_key, time_value in selected_markers:
            markers_by_lane.setdefault(lane_key, []).append(time_value)

        clipboard_entries: List[Dict[str, Any]] = []
        total_frames = 0
        for lane_key, marker_times in markers_by_lane.items():
            keyframes = self._get_lane_keyframes(lane_key)
            if keyframes is None:
                continue
            matches: List[Dict[str, Any]] = []
            for frame in keyframes:
                if any(abs(frame.time - marker) <= tolerance for marker in marker_times):
                    matches.append(
                        {
                            "time_offset": float(frame.time - base_time),
                            "data": replace(frame),
                        }
                    )
            if not matches:
                continue
            entry: Dict[str, Any] = {
                "scope": lane_key.scope,
                "lane_index": lane_key.lane_index,
                "lane_name": self._lane_label(lane_key),
                "keyframes": matches,
            }
            if lane_key.scope == "layer":
                layer = self.gl_widget.get_layer_by_id(lane_key.layer_id)
                entry["layer_name"] = layer.name if layer else ""
                entry["layer_id"] = lane_key.layer_id
                entry["source_key"] = self._clipboard_layer_source_key(lane_key.layer_id)
            clipboard_entries.append(entry)
            total_frames += len(matches)

        if not clipboard_entries:
            self.log_widget.log("No keyframes matched the current selection to copy.", "INFO")
            return
        self._keyframe_clipboard = {
            "lanes": clipboard_entries,
            "copied_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.log_widget.log(
            f"Copied {total_frames} keyframe(s) from {len(clipboard_entries)} lane(s).",
            "SUCCESS",
        )

    def paste_copied_keyframes(self):
        """Paste keyframes from the clipboard at the current timeline position."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before pasting keyframes.", "WARNING")
            return
        if not self._keyframe_clipboard:
            self.log_widget.log("Copy keyframes before attempting to paste.", "INFO")
            return
        clipboard_lanes = self._keyframe_clipboard.get("lanes") or []
        clipboard_layers = self._keyframe_clipboard.get("layers") or []
        if not clipboard_lanes and not clipboard_layers:
            self.log_widget.log("Clipboard is empty; copy keyframes before pasting.", "INFO")
            return
        layer_lookup = {layer.name.lower(): layer for layer in animation.layers if layer.name}
        layer_id_lookup = {layer.layer_id: layer for layer in animation.layers if layer.layer_id is not None}
        source_lookup: Dict[str, LayerData] = {}
        for layer in animation.layers:
            key = self._clipboard_layer_source_key(layer.layer_id)
            if key and key not in source_lookup:
                source_lookup[key] = layer

        def _resolve_target_layer(entry: Dict[str, Any]) -> Optional[LayerData]:
            layer_id_hint = entry.get("layer_id")
            target_layer = None
            if isinstance(layer_id_hint, int):
                target_layer = layer_id_lookup.get(layer_id_hint)
            layer_name_raw = entry.get("layer_name") or ""
            layer_name = layer_name_raw.lower()
            if not target_layer and layer_name:
                target_layer = layer_lookup.get(layer_name)
            if not target_layer and layer_name_raw:
                target_layer = self._match_layer_by_name(layer_lookup, layer_name_raw)
            if not target_layer and layer_name_raw:
                for variant in self._layer_name_variants(layer_name_raw):
                    if variant:
                        candidate = layer_lookup.get(variant.lower())
                        if candidate:
                            target_layer = candidate
                            break
            if not target_layer:
                source_key = entry.get("source_key")
                if isinstance(source_key, str):
                    target_layer = source_lookup.get(source_key.lower())
            return target_layer
        candidate_layers: Dict[int, LayerData] = {}
        include_global = False
        total_candidate_frames = 0

        if clipboard_lanes:
            for entry in clipboard_lanes:
                frames = entry.get("keyframes") or []
                if not frames:
                    continue
                if entry.get("scope") == "global":
                    include_global = True
                    total_candidate_frames += len(frames)
                    continue
                target_layer = _resolve_target_layer(entry)
                if not target_layer or target_layer.layer_id is None:
                    continue
                candidate_layers[target_layer.layer_id] = target_layer
                total_candidate_frames += len(frames)
        else:
            for entry in clipboard_layers:
                frames = entry.get("keyframes") or []
                if not frames:
                    continue
                target_layer = _resolve_target_layer(entry)
                if not target_layer or target_layer.layer_id is None:
                    continue
                candidate_layers[target_layer.layer_id] = target_layer
                total_candidate_frames += len(frames)

        if not candidate_layers and not include_global:
            self.log_widget.log(
                "Copied keyframes do not match any lanes in this animation.",
                "WARNING",
            )
            return
        self._begin_keyframe_action(list(candidate_layers.keys()), include_global=include_global)
        inserted = 0
        new_marker_refs: List[Tuple[TimelineLaneKey, float]] = []
        target_start = max(0.0, float(self.gl_widget.player.current_time))

        if clipboard_lanes:
            for entry in clipboard_lanes:
                frames = entry.get("keyframes") or []
                if not frames:
                    continue
                scope = entry.get("scope") or "layer"
                lane_index = int(entry.get("lane_index", 0))
                lane_name = entry.get("lane_name") or ""
                if scope == "global":
                    lane = self._ensure_global_lane_index(animation, lane_index, lane_name)
                    for payload in frames:
                        frame_data = payload.get("data")
                        if not isinstance(frame_data, KeyframeData):
                            continue
                        offset = float(payload.get("time_offset", 0.0))
                        frame_copy = replace(frame_data)
                        frame_copy.time = max(0.0, target_start + offset)
                        lane.keyframes.append(frame_copy)
                        inserted += 1
                        new_marker_refs.append((TimelineLaneKey("global", -1, lane_index), frame_copy.time))
                    lane.keyframes.sort(key=lambda frame: frame.time)
                    continue

                target_layer = _resolve_target_layer(entry)
                if not target_layer or target_layer.layer_id is None:
                    continue
                if lane_index <= 0:
                    keyframes = target_layer.keyframes
                else:
                    lane = self._ensure_layer_lane_index(target_layer, lane_index, lane_name)
                    keyframes = lane.keyframes
                for payload in frames:
                    frame_data = payload.get("data")
                    if not isinstance(frame_data, KeyframeData):
                        continue
                    offset = float(payload.get("time_offset", 0.0))
                    frame_copy = replace(frame_data)
                    frame_copy.time = max(0.0, target_start + offset)
                    keyframes.append(frame_copy)
                    inserted += 1
                    new_marker_refs.append(
                        (TimelineLaneKey("layer", target_layer.layer_id, lane_index), frame_copy.time)
                    )
                keyframes.sort(key=lambda frame: frame.time)
                if lane_index == 0:
                    self._sync_layer_source_frames(target_layer)
        else:
            for entry in clipboard_layers:
                frames = entry.get("keyframes") or []
                if not frames:
                    continue
                target_layer = _resolve_target_layer(entry)
                if not target_layer or target_layer.layer_id is None:
                    continue
                for payload in frames:
                    frame_copy: KeyframeData = replace(payload.get("data"))
                    offset = float(payload.get("time_offset", 0.0))
                    frame_copy.time = max(0.0, target_start + offset)
                    target_layer.keyframes.append(frame_copy)
                    inserted += 1
                    new_marker_refs.append(
                        (TimelineLaneKey("layer", target_layer.layer_id, 0), frame_copy.time)
                    )
                target_layer.keyframes.sort(key=lambda frame: frame.time)
                self._sync_layer_source_frames(target_layer)
        if inserted == 0:
            self._pending_keyframe_action = None
            self._update_keyframe_history_controls()
            self.log_widget.log("Pasting failed because no keyframes could be inserted.", "WARNING")
            return
        self._finalize_keyframe_action("paste_keyframes")
        self.gl_widget.player.calculate_duration()
        self.update_timeline()
        self.gl_widget.update()
        self._refresh_timeline_keyframes()
        if new_marker_refs:
            self._replace_marker_selection(new_marker_refs)
        self.log_widget.log(
            f"Pasted {inserted} keyframe(s) into {len(candidate_layers)} lane group(s).",
            "SUCCESS",
        )

    def on_animation_time_changed(self, current: float, duration: float):
        """Update the timeline UI when the renderer advances."""
        if not self.gl_widget.player.animation:
            return
        if not self._timeline_user_scrubbing:
            slider = self.timeline.timeline_slider
            slider.blockSignals(True)
            slider.setValue(int(current * 1000))
            slider.blockSignals(False)
        duration = duration if duration > 0 else self.gl_widget.player.duration
        self.timeline.set_time_label(f"{current:.2f} / {duration:.2f}s")
        self.timeline.set_current_time(current)
        if not self.gl_widget.player.playing and not self._timeline_user_scrubbing:
            self._refresh_layer_thumbnails()
        self._update_timeline_beat_display()
        self._update_metronome_tempo_for_time(current)
        self._sync_multi_view_widgets(current)

    def on_animation_looped(self):
        """Keep audio aligned with animation loops."""
        if self.audio_manager.is_ready and self.gl_widget.player.playing:
            if self._audio_loop_multiplier > 1:
                self._audio_loop_index += 1
                if self._audio_loop_index >= self._audio_loop_multiplier:
                    self._audio_loop_index = 0
                    self.audio_manager.restart()
                elif not self.audio_manager.is_playing():
                    self._audio_loop_index = 0
                    self.audio_manager.play(self._get_audio_sync_time(0.0))
            else:
                self.audio_manager.restart()

    def on_playback_state_changed(self, playing: bool):
        """Handle automatic playback state changes (e.g., reaching the end)."""
        self.timeline.set_play_button_text("Pause" if playing else "Play")
        if not playing and self.audio_manager.is_ready:
            self.audio_manager.pause()
        self._update_metronome_state()

    def load_multi_view_animations(self) -> None:
        start_dir = (
            self.settings.value('animation/last_load_path', '', type=str)
            or self.current_json_path
            or str(Path.home())
        )
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Load Additional Animations",
            start_dir,
            "Animation Files (*.json *.bin);;All Files (*)",
        )
        if not filenames:
            return
        for filename in filenames:
            self._add_multi_view_animation(filename)

    def clear_multi_view_animations(self) -> None:
        if not self.viewer_splitter:
            return
        for container in self.multi_view_containers:
            try:
                self.viewer_splitter.widget(self.viewer_splitter.indexOf(container))
            except Exception:
                pass
            container.setParent(None)
            container.deleteLater()
        self.multi_view_widgets.clear()
        self.multi_view_containers.clear()
        self.multi_view_labels.clear()
        if hasattr(self, "clear_multi_view_btn"):
            self.clear_multi_view_btn.setEnabled(False)

    def _add_multi_view_animation(self, filename: str) -> None:
        if not self.viewer_splitter:
            return
        payload = self._load_animation_payload_from_file(filename)
        if not payload:
            self.log_widget.log(f"Multi-view load failed: {os.path.basename(filename)}", "ERROR")
            return
        anims = payload.get("anims", [])
        if not anims:
            self.log_widget.log(f"No animations found in {os.path.basename(filename)}", "WARNING")
            return
        anim_dict = anims[0]
        anim_name = anim_dict.get("name") or Path(filename).stem
        container, widget, label = self._create_multi_view_widget(str(anim_name))
        if not widget:
            return
        try:
            sources = payload.get("sources", [])
            json_dir = os.path.dirname(filename)
            atlases = self._load_texture_atlases_for_sources(
                sources,
                json_dir=json_dir,
                use_cache=True,
            )
            blend_version = self._determine_blend_version(payload)
            animation = self._build_animation_struct(
                anim_dict,
                blend_version,
                filename,
                resource_dict=payload,
            )
            widget.texture_atlases = atlases
            widget.player.load_animation(animation)
            widget.player.loop = bool(self.gl_widget.player.loop)
            widget.render_scale = self.gl_widget.render_scale
            widget.camera_x = self.gl_widget.camera_x
            widget.camera_y = self.gl_widget.camera_y
            widget.set_time(self.gl_widget.player.current_time)
            if widget.context() is not None:
                widget.makeCurrent()
                widget.initializeGL()
                widget.doneCurrent()
        except Exception as exc:
            self.log_widget.log(f"Multi-view load failed ({anim_name}): {exc}", "ERROR")
            container.setParent(None)
            container.deleteLater()
            return
        self.viewer_splitter.addWidget(container)
        self.multi_view_widgets.append(widget)
        self.multi_view_containers.append(container)
        self.multi_view_labels.append(label)
        if hasattr(self, "clear_multi_view_btn"):
            self.clear_multi_view_btn.setEnabled(True)
        self.log_widget.log(f"Loaded side-by-side view: {anim_name}", "SUCCESS")

    def _load_animation_payload_from_file(self, filename: str) -> Optional[Dict[str, Any]]:
        if not filename:
            return None
        lower = filename.lower()
        if lower.endswith(".bin"):
            return self._load_animation_resource_dict(None, filename)
        if lower.endswith(".json"):
            try:
                with open(filename, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception as exc:
                self.log_widget.log(f"Failed to read animation file: {exc}", "ERROR")
                return None
            normalized = self._normalize_animation_file_payload(payload)
            if normalized:
                return normalized
            if isinstance(payload, dict) and isinstance(payload.get("anims"), list):
                return payload
            return None
        return None

    def _create_multi_view_widget(
        self, title: str
    ) -> Tuple[QWidget, OpenGLAnimationWidget, QLabel]:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setStyleSheet("color: #bbb; font-size: 8pt;")
        layout.addWidget(label, 0)
        widget = OpenGLAnimationWidget(shader_registry=self.shader_registry)
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._configure_multi_view_widget(widget)
        layout.addWidget(widget, 1)
        return container, widget, label

    def _apply_postfx_settings_to_widget(self, widget: OpenGLAnimationWidget) -> None:
        """Apply viewport post AA + Uber-subset post effects to a GL widget."""
        widget.set_post_aa_enabled(self.viewport_post_aa_enabled)
        widget.set_post_aa_strength(self.viewport_post_aa_strength)
        widget.set_post_aa_mode(self.viewport_post_aa_mode)
        widget.set_post_motion_blur_enabled(self.viewport_post_motion_blur_enabled)
        widget.set_post_motion_blur_strength(self.viewport_post_motion_blur_strength)
        widget.set_post_bloom_enabled(self.viewport_post_bloom_enabled)
        widget.set_post_bloom_strength(self.viewport_post_bloom_strength)
        widget.set_post_bloom_threshold(self.viewport_post_bloom_threshold)
        widget.set_post_bloom_radius(self.viewport_post_bloom_radius)
        widget.set_post_vignette_enabled(self.viewport_post_vignette_enabled)
        widget.set_post_vignette_strength(self.viewport_post_vignette_strength)
        widget.set_post_grain_enabled(self.viewport_post_grain_enabled)
        widget.set_post_grain_strength(self.viewport_post_grain_strength)
        widget.set_post_ca_enabled(self.viewport_post_ca_enabled)
        widget.set_post_ca_strength(self.viewport_post_ca_strength)

    def _configure_multi_view_widget(self, widget: OpenGLAnimationWidget) -> None:
        widget.set_constraint_manager(self.constraint_manager)
        widget.set_joint_solver_enabled(self.joint_solver_enabled)
        widget.set_joint_solver_iterations(self.joint_solver_iterations)
        widget.set_joint_solver_strength(self.joint_solver_strength)
        widget.set_joint_solver_parented(self.joint_solver_parented)
        widget.set_propagate_user_transforms(self.propagate_user_transforms)
        widget.set_zoom_to_cursor(self.export_settings.camera_zoom_to_cursor)
        widget.set_costume_pivot_adjustment_enabled(False)
        widget.set_particle_viewport_cap(self.dof_particle_viewport_cap)
        widget.set_particle_distance_sensitivity(self.dof_particle_distance_sensitivity)
        widget.set_dof_alpha_smoothing_enabled(self.dof_alpha_edge_smoothing_enabled)
        widget.set_dof_alpha_smoothing_strength(self.dof_alpha_edge_smoothing_strength)
        widget.set_dof_alpha_smoothing_mode(self.dof_alpha_edge_smoothing_mode)
        widget.set_dof_sprite_shader_mode(self.dof_sprite_shader_mode)
        self._apply_postfx_settings_to_widget(widget)
        sprite_filter = self.settings.value("viewport/sprite_filter", "bilinear", type=str)
        widget.set_sprite_filter_mode(sprite_filter)
        sprite_filter_strength = self.settings.value("viewport/sprite_filter_strength", 1.0, type=float)
        widget.set_sprite_filter_strength(sprite_filter_strength)

    def _sync_multi_view_widgets(self, current_time: float) -> None:
        if not self.multi_view_widgets:
            return
        for widget in self.multi_view_widgets:
            if not widget.player.animation:
                continue
            widget.render_scale = self.gl_widget.render_scale
            widget.camera_x = self.gl_widget.camera_x
            widget.camera_y = self.gl_widget.camera_y
            widget.set_time(current_time)

    def _sync_tile_debug_controls_from_gl(self) -> None:
        """Sync terrain debug combos to the active GL widget modes."""
        if not hasattr(self, "gl_widget"):
            return
        self._update_terrain_tile_index_range()
        path_mode = str(getattr(self.gl_widget, "tile_render_path", "full_quad"))
        filter_mode = str(getattr(self.gl_widget, "tile_filter_mode", "nearest"))
        flag_order_mode = str(getattr(self.gl_widget, "tile_flag_order_mode", "flag0_then1"))
        flag1_transform_mode = str(getattr(self.gl_widget, "tile_flag1_transform_mode", "none"))

        for combo, value in (
            (self.control_panel.terrain_path_combo, path_mode),
            (self.control_panel.terrain_filter_combo, filter_mode),
            (self.control_panel.terrain_flag_order_combo, flag_order_mode),
            (self.control_panel.terrain_flag1_transform_combo, flag1_transform_mode),
        ):
            idx = combo.findData(value)
            if idx < 0:
                idx = 0
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        for spin, value in (
            (self.control_panel.terrain_global_x_spin, float(getattr(self.gl_widget, "tile_global_offset_x", 0.0))),
            (self.control_panel.terrain_global_y_spin, float(getattr(self.gl_widget, "tile_global_offset_y", 0.0))),
            (self.control_panel.terrain_global_rot_spin, float(getattr(self.gl_widget, "tile_global_rotation_deg", 0.0))),
            (self.control_panel.terrain_global_scale_spin, float(getattr(self.gl_widget, "tile_global_scale", 1.0))),
            (self.control_panel.terrain_tile_x_spin, float(getattr(self.gl_widget, "tile_selected_offset_x", 0.0))),
            (self.control_panel.terrain_tile_y_spin, float(getattr(self.gl_widget, "tile_selected_offset_y", 0.0))),
            (self.control_panel.terrain_tile_rot_spin, float(getattr(self.gl_widget, "tile_selected_rotation_deg", 0.0))),
            (self.control_panel.terrain_tile_scale_spin, float(getattr(self.gl_widget, "tile_selected_scale", 1.0))),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

        tile_idx = int(getattr(self.gl_widget, "tile_selected_index", -1))
        tile_idx = max(-1, min(tile_idx, self.control_panel.terrain_tile_index_spin.maximum()))
        self.control_panel.terrain_tile_index_spin.blockSignals(True)
        self.control_panel.terrain_tile_index_spin.setValue(tile_idx)
        self.control_panel.terrain_tile_index_spin.blockSignals(False)

    def _update_terrain_tile_index_range(self) -> None:
        """Update per-tile index control based on currently loaded tile batches."""
        if not hasattr(self, "control_panel") or not hasattr(self, "gl_widget"):
            return
        tile_total = 0
        for batch in getattr(self.gl_widget, "tile_batches", []) or []:
            tile_total += len(getattr(batch, "instances", []) or [])
        max_idx = max(-1, tile_total - 1)
        current_value = self.control_panel.terrain_tile_index_spin.value()
        if current_value > max_idx:
            current_value = -1
            self.gl_widget.set_tile_selected_index(-1)
        self.control_panel.terrain_tile_index_spin.blockSignals(True)
        self.control_panel.terrain_tile_index_spin.setRange(-1, max_idx)
        self.control_panel.terrain_tile_index_spin.setSpecialValueText("None")
        self.control_panel.terrain_tile_index_spin.setValue(current_value)
        self.control_panel.terrain_tile_index_spin.blockSignals(False)

    def _on_terrain_path_changed(self, _index: int) -> None:
        mode = str(self.control_panel.terrain_path_combo.currentData() or "full_quad")
        self.gl_widget.set_tile_render_path(mode)
        self.settings.setValue("terrain/tile_render_path", mode)
        self.log_widget.log(f"Terrain path set: {mode}", "INFO")

    def _on_terrain_filter_changed(self, _index: int) -> None:
        mode = str(self.control_panel.terrain_filter_combo.currentData() or "nearest")
        self.gl_widget.set_tile_filter_mode(mode)
        self.settings.setValue("terrain/tile_filter_mode", mode)
        self.log_widget.log(f"Terrain tile filter set: {mode}", "INFO")

    def _on_terrain_flag_order_changed(self, _index: int) -> None:
        mode = str(self.control_panel.terrain_flag_order_combo.currentData() or "flag0_then1")
        self.gl_widget.set_tile_flag_order_mode(mode)
        self.settings.setValue("terrain/tile_flag_order_mode", mode)
        self.log_widget.log(f"Terrain flag order set: {mode}", "INFO")

    def _on_terrain_flag1_transform_changed(self, _index: int) -> None:
        mode = str(self.control_panel.terrain_flag1_transform_combo.currentData() or "none")
        self.gl_widget.set_tile_flag1_transform_mode(mode)
        self.settings.setValue("terrain/tile_flag1_transform_mode", mode)
        self._refresh_island_terrain_debug_surfaces()
        self.log_widget.log(f"Terrain flag1 transform set: {mode}", "INFO")

    def _on_terrain_global_transform_changed(self, _value: float) -> None:
        self.gl_widget.set_tile_global_transform(
            self.control_panel.terrain_global_x_spin.value(),
            self.control_panel.terrain_global_y_spin.value(),
            self.control_panel.terrain_global_rot_spin.value(),
            self.control_panel.terrain_global_scale_spin.value(),
        )
        self.settings.setValue("terrain/global_offset_x", self.control_panel.terrain_global_x_spin.value())
        self.settings.setValue("terrain/global_offset_y", self.control_panel.terrain_global_y_spin.value())
        self.settings.setValue("terrain/global_rotation_deg", self.control_panel.terrain_global_rot_spin.value())
        self.settings.setValue("terrain/global_scale", self.control_panel.terrain_global_scale_spin.value())

    def _on_terrain_tile_index_changed(self, value: int) -> None:
        self.gl_widget.set_tile_selected_index(value)
        self.settings.setValue("terrain/selected_tile_index", int(value))

    def _on_terrain_tile_transform_changed(self, _value: float) -> None:
        self.gl_widget.set_tile_selected_transform(
            self.control_panel.terrain_tile_x_spin.value(),
            self.control_panel.terrain_tile_y_spin.value(),
            self.control_panel.terrain_tile_rot_spin.value(),
            self.control_panel.terrain_tile_scale_spin.value(),
        )
        self.settings.setValue("terrain/selected_tile_offset_x", self.control_panel.terrain_tile_x_spin.value())
        self.settings.setValue("terrain/selected_tile_offset_y", self.control_panel.terrain_tile_y_spin.value())
        self.settings.setValue("terrain/selected_tile_rotation_deg", self.control_panel.terrain_tile_rot_spin.value())
        self.settings.setValue("terrain/selected_tile_scale", self.control_panel.terrain_tile_scale_spin.value())

    def _refresh_island_terrain_debug_surfaces(self) -> None:
        """Rebuild island terrain batches/composite for live debug option changes."""
        if not self.current_json_data:
            return
        anims = self.current_json_data.get("anims")
        if not isinstance(anims, list) or not anims:
            return
        idx = self.control_panel.anim_combo.currentIndex()
        if idx < 0 or idx >= len(anims):
            return
        animation = self.gl_widget.player.animation
        if animation is None:
            return
        try:
            anim_dict = anims[idx]
            tile_batches, terrain_composite = self._build_island_tile_batches(anim_dict, animation)
            self.gl_widget.set_terrain_composite(terrain_composite)
            self.gl_widget.set_tile_batches(tile_batches)
            self._update_terrain_tile_index_range()
        except Exception as exc:
            self.log_widget.log(f"Failed to refresh island terrain debug surfaces: {exc}", "WARNING")

    def _on_tile_render_stats(self, stats: Dict[str, Any]) -> None:
        """Log terrain renderer instrumentation for island tile diagnostics."""
        if not isinstance(stats, dict):
            return
        path = str(stats.get("path") or "unknown")
        filter_mode = str(stats.get("filter") or "unknown")
        flag_order = str(stats.get("flag_order") or "unknown")
        flag1_transform = str(stats.get("flag1_transform") or "none")
        try:
            tile_count = int(stats.get("tile_count", 0))
        except (TypeError, ValueError):
            tile_count = 0
        try:
            flag0_count = int(stats.get("flag0_count", 0))
        except (TypeError, ValueError):
            flag0_count = 0
        try:
            flag1_count = int(stats.get("flag1_count", 0))
        except (TypeError, ValueError):
            flag1_count = 0
        try:
            elapsed_ms = float(stats.get("ms", 0.0))
        except (TypeError, ValueError):
            elapsed_ms = 0.0
        signature = f"{path}|{filter_mode}|{flag_order}|{flag1_transform}"
        if signature != self._last_tile_render_signature:
            self._last_tile_render_signature = signature
            self.log_widget.log(
                (
                    f"Terrain path active: {path}, filter: {filter_mode}, "
                    f"flag order: {flag_order}, flag1 transform: {flag1_transform}"
                ),
                "INFO",
            )
        self.log_widget.log(
            (
                f"Terrain pass: {elapsed_ms:.2f} ms, tiles: {tile_count}, "
                f"flag0: {flag0_count}, flag1+: {flag1_count}, "
                f"path: {path}, filter: {filter_mode}, order: {flag_order}, "
                f"flag1_xform: {flag1_transform}"
            ),
            "DEBUG",
        )

    # ------------------------------------------------------------------ #
    # Pose recording helpers
    # ------------------------------------------------------------------ #

    def on_pose_influence_changed(self, mode: Optional[str]) -> None:
        """Update how recorded poses propagate to future keyframes."""
        if mode not in {"current", "forward"}:
            mode = "current"
        self.pose_influence_mode = mode
        self.settings.setValue('pose/influence_mode', mode)

    def on_record_pose_clicked(self) -> None:
        """Bake the current gizmo offsets into animation data."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before recording poses.", "WARNING")
            return
        if not self.selected_layer_ids:
            self.log_widget.log("Select at least one layer to record a pose.", "WARNING")
            return

        layer_ids = set(self.selected_layer_ids)
        if not layer_ids:
            return
        active_lane = self.timeline.get_active_lane() if hasattr(self, "timeline") else None
        use_global_lane = bool(active_lane and active_lane.scope == "global")
        lane_index = active_lane.lane_index if active_lane and active_lane.scope == "layer" else 0
        if use_global_lane and active_lane is None:
            use_global_lane = False
        base_states_map, final_states_map = self._gather_pose_state_maps(layer_ids)
        desired_states_map = self._apply_offsets_to_state_map(final_states_map)
        time_value = round(self.gl_widget.player.current_time, 5)
        influence = self.pose_influence_mode or "current"
        use_compensation = (not self.preserve_children_on_record) and not use_global_lane and lane_index == 0
        compensation_ids: Set[int] = set()
        if use_compensation:
            compensation_ids = self._collect_compensation_ids(layer_ids, layer_ids)
        applied = 0
        include_global = use_global_lane
        self._begin_keyframe_action(list(layer_ids | compensation_ids), include_global=include_global)
        changed_parents: Set[int] = set()
        if use_global_lane and active_lane is not None:
            target_layer_id = self.primary_selected_layer_id or next(iter(layer_ids))
            if target_layer_id not in layer_ids:
                target_layer_id = next(iter(layer_ids))
            if self._record_pose_for_layer(
                target_layer_id,
                time_value,
                influence,
                base_states_map,
                final_states_map,
                desired_states_map,
                force=True,
                lane_key=TimelineLaneKey("global", -1, active_lane.lane_index),
            ):
                applied += 1
                changed_parents.add(target_layer_id)
        else:
            for layer_id in layer_ids:
                if lane_index > 0:
                    layer = self.gl_widget.get_layer_by_id(layer_id)
                    if layer:
                        self._ensure_layer_lane_index(layer, lane_index)
                lane_key = TimelineLaneKey("layer", layer_id, lane_index)
                if self._record_pose_for_layer(
                    layer_id,
                    time_value,
                    influence,
                    base_states_map,
                    final_states_map,
                    desired_states_map,
                    force=True,
                    lane_key=lane_key,
                ):
                    applied += 1
                    changed_parents.add(layer_id)

        compensated_ids: Set[int] = set()
        if use_compensation and changed_parents:
            child_influence = influence
            desired_states_for_comp = desired_states_map
            if compensation_ids:
                desired_states_for_comp = dict(desired_states_map)
                for child_id in compensation_ids:
                    base_state = base_states_map.get(child_id)
                    if base_state:
                        desired_states_for_comp[child_id] = base_state
            post_states_map, _ = self._gather_pose_state_maps(layer_ids | compensation_ids)
            compensated_ids = self._apply_record_compensation(
                changed_parents,
                time_value,
                child_influence,
                post_states_map,
                desired_states_for_comp,
                layer_ids,
            )

        if not applied and not compensated_ids:
            self.log_widget.log("No gizmo offsets detected; nothing to record.", "INFO")
            self._finalize_keyframe_action("record_pose")
            return

        self._clear_user_offsets_for_layers(self.selected_layer_ids | compensated_ids)
        if (
            not use_compensation
            and getattr(self.gl_widget, "joint_solver_enabled", False)
        ):
            self.gl_widget.refresh_joint_solver_after_pose_record(layer_ids)
        self.update_offset_display()
        self.gl_widget.player.calculate_duration()
        self.update_timeline()
        self.gl_widget.update()
        scope = "propagated" if influence == "forward" else "local"
        self._finalize_keyframe_action("record_pose")
        self.log_widget.log(
            f"Recorded pose for {applied} layer(s) ({scope}).",
            "SUCCESS"
        )

    def on_reset_pose_clicked(self) -> None:
        """Reset selected keyframes back to their baseline values."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before resetting poses.", "WARNING")
            return
        if not self.selected_layer_ids:
            self.log_widget.log("Select at least one layer to reset.", "WARNING")
            return
        if not self._pose_baseline_player:
            self.log_widget.log("Baseline unavailable; reload the animation to reset keyframes.", "WARNING")
            return
        time_value = round(self.gl_widget.player.current_time, 5)
        influence = self.pose_influence_mode or "current"
        layer_ids = sorted(self.selected_layer_ids)
        self._begin_keyframe_action(layer_ids)
        applied = 0
        for layer_id in layer_ids:
            if self._reset_pose_for_layer(layer_id, time_value, influence):
                applied += 1
        self._finalize_keyframe_action("reset_pose")
        if not applied:
            self.log_widget.log("No matching keyframes found to reset.", "INFO")
            return
        self.gl_widget.player.calculate_duration()
        self.update_timeline()
        self.gl_widget.update()
        scope = "propagated" if influence == "forward" else "local"
        self.log_widget.log(
            f"Reset {applied} keyframe(s) to defaults ({scope}).",
            "SUCCESS"
        )

    def _record_pose_for_layer(
        self,
        layer_id: int,
        time_value: float,
        influence: str,
        base_states: Dict[int, Dict[str, Any]],
        final_states: Dict[int, Dict[str, Any]],
        desired_states: Optional[Dict[int, Dict[str, Any]]] = None,
        tolerance: float = 1e-4,
        force: bool = False,
        lane_key: Optional[TimelineLaneKey] = None,
    ) -> bool:
        """Capture gizmo offsets for a single layer."""
        layer = self.gl_widget.get_layer_by_id(layer_id)
        if not layer:
            return False
        if lane_key is None:
            lane_key = TimelineLaneKey("layer", layer_id, 0)
        lane_keyframes = self._get_lane_keyframes(lane_key)
        if lane_keyframes is None:
            return False
        if lane_key.scope == "global":
            return self._record_pose_for_global_lane(
                layer_id,
                time_value,
                influence,
                base_states=base_states,
                final_states=final_states,
                lane_key=lane_key,
                tolerance=tolerance,
                force=force,
            )
        is_base_lane = self._is_base_lane(lane_key)
        anchor_override = self.gl_widget.layer_anchor_overrides.get(layer_id)
        anchor_captured = False
        local_state = self.gl_widget.player.get_layer_state(layer, time_value, include_global=False)
        base_state = base_states.get(layer_id, {})
        final_state = final_states.get(layer_id, {})
        if not base_state or not final_state:
            base_state = {}
            final_state = {}
        base_pos_x = float(local_state.get("pos_x", 0.0))
        base_pos_y = float(local_state.get("pos_y", 0.0))
        base_rot = float(local_state.get("rotation", 0.0))
        base_scale_x = float(local_state.get("scale_x", 100.0))
        base_scale_y = float(local_state.get("scale_y", 100.0))

        candidate_ids = list(self.selected_layer_ids) if self.selected_layer_ids else [layer_id]
        offset_sum_x = 0.0
        offset_sum_y = 0.0
        offset_count = 0
        rot_sum = 0.0
        rot_count = 0
        scale_sum_x = 0.0
        scale_sum_y = 0.0
        scale_count = 0
        for candidate_id in candidate_ids:
            dx, dy = self.gl_widget.layer_offsets.get(candidate_id, (0.0, 0.0))
            if abs(dx) > tolerance or abs(dy) > tolerance:
                offset_sum_x += dx
                offset_sum_y += dy
                offset_count += 1
            rot_val = self.gl_widget.layer_rotations.get(candidate_id, 0.0)
            if abs(rot_val) > tolerance:
                rot_sum += rot_val
                rot_count += 1
            scale_x, scale_y = self.gl_widget.layer_scale_offsets.get(candidate_id, (1.0, 1.0))
            if abs(scale_x - 1.0) > tolerance or abs(scale_y - 1.0) > tolerance:
                scale_sum_x += scale_x
                scale_sum_y += scale_y
                scale_count += 1
        offset_x = (offset_sum_x / offset_count) if offset_count else 0.0
        offset_y = (offset_sum_y / offset_count) if offset_count else 0.0
        rot_offset = (rot_sum / rot_count) if rot_count else 0.0
        avg_scale_x = (scale_sum_x / scale_count) if scale_count else 1.0
        avg_scale_y = (scale_sum_y / scale_count) if scale_count else 1.0
        scale_offset_x = avg_scale_x
        scale_offset_y = avg_scale_y

        base_anchor_x = float(base_state.get("anchor_world_x", base_state.get("tx", 0.0)))
        base_anchor_y = float(base_state.get("anchor_world_y", base_state.get("ty", 0.0)))
        final_anchor_x = float(final_state.get("anchor_world_x", base_anchor_x))
        final_anchor_y = float(final_state.get("anchor_world_y", base_anchor_y))
        target_pos_x = base_pos_x
        target_pos_y = base_pos_y
        target_rot = base_rot
        target_scale_x = base_scale_x
        target_scale_y = base_scale_y
        computed_from_world = False

        desired_state = desired_states.get(layer_id) if desired_states else None
        if desired_state:
            parent_id = layer.parent_id
            local_mat: Optional[Tuple[float, float, float, float, float, float]] = None
            if parent_id is not None and parent_id >= 0:
                parent_state = desired_states.get(parent_id) if desired_states else None
                if parent_state:
                    local_mat = self._compute_local_matrix(parent_state, desired_state)
            else:
                local_mat = self._state_to_affine(desired_state)
                global_mat = self._global_lane_matrix(time_value)
                if global_mat:
                    inv_global = self._invert_affine_matrix(*global_mat)
                    if inv_global:
                        local_mat = self._mul_affine_matrices(inv_global, local_mat)
                renderer = self.gl_widget.renderer
                local_mat = (
                    local_mat[0],
                    local_mat[1],
                    local_mat[2],
                    local_mat[3],
                    local_mat[4] - float(renderer.world_offset_x),
                    local_mat[5] - float(renderer.world_offset_y),
                )
            if local_mat:
                target = self._decompose_local_matrix_to_keyframe(layer_id, local_mat)
                if target:
                    target_pos_x, target_pos_y, target_rot, target_scale_x, target_scale_y = target
                    computed_from_world = True

        if not computed_from_world:
            world_delta_x = (final_anchor_x - base_anchor_x) + offset_x
            world_delta_y = (final_anchor_y - base_anchor_y) + offset_y
            local_delta_x = 0.0
            local_delta_y = 0.0
            state_map = base_states
            if (
                getattr(self.gl_widget, "joint_solver_enabled", False)
                and getattr(self.gl_widget, "joint_solver_parented", False)
            ):
                state_map = final_states
            local_delta_x, local_delta_y = self._world_delta_to_local(
                layer,
                state_map,
                world_delta_x,
                world_delta_y,
            )
            target_pos_x = base_pos_x + local_delta_x
            target_pos_y = base_pos_y + local_delta_y
            target_rot = base_rot + rot_offset
            target_scale_x = base_scale_x * scale_offset_x
            target_scale_y = base_scale_y * scale_offset_y

        has_translation = (
            abs(target_pos_x - base_pos_x) > tolerance
            or abs(target_pos_y - base_pos_y) > tolerance
        )
        has_rotation = abs(target_rot - base_rot) > tolerance
        has_scale = (
            abs(target_scale_x - base_scale_x) > tolerance
            or abs(target_scale_y - base_scale_y) > tolerance
        )

        if anchor_override is not None:
            anchor_captured = self._update_layer_anchor(layer, anchor_override)

        changes_requested = has_translation or has_rotation or has_scale or anchor_captured
        if not changes_requested and not force:
            return False

        eval_state = self.gl_widget.player.get_layer_state(
            layer,
            self.gl_widget.player.current_time,
            include_global=False,
        )
        keyframes = self._find_keyframes_at_time_in_lane(lane_key, time_value)
        created = False
        if not keyframes:
            keyframe = KeyframeData(time=time_value)
            lane_keyframes.append(keyframe)
            keyframes = [keyframe]
            created = True
            if is_base_lane:
                keyframe.pos_x = base_pos_x
                keyframe.pos_y = base_pos_y
                keyframe.scale_x = base_scale_x
                keyframe.scale_y = base_scale_y
                keyframe.rotation = base_rot
                keyframe.opacity = float(eval_state.get("opacity", keyframe.opacity))
                snapshot_sprite = eval_state.get("sprite_name")
                if snapshot_sprite:
                    keyframe.sprite_name = snapshot_sprite
                keyframe.immediate_sprite = -1
                keyframe.r = int(eval_state.get("r", keyframe.r))
                keyframe.g = int(eval_state.get("g", keyframe.g))
                keyframe.b = int(eval_state.get("b", keyframe.b))
            else:
                keyframe.pos_x = 0.0
                keyframe.pos_y = 0.0
                keyframe.scale_x = 0.0
                keyframe.scale_y = 0.0
                keyframe.rotation = 0.0
                keyframe.opacity = 0.0
                keyframe.immediate_sprite = -1
                keyframe.immediate_rgb = -1

        reference_state = None
        if not is_base_lane:
            reference_state = self.gl_widget.player.get_layer_state(
                layer,
                time_value,
                include_additive=True,
                include_global=False,
                exclude_lane=lane_key,
            )
        if reference_state is None:
            reference_state = local_state

        for keyframe in keyframes:
            keyframe.time = time_value
            if has_translation:
                if is_base_lane:
                    keyframe.pos_x = target_pos_x
                    keyframe.pos_y = target_pos_y
                else:
                    keyframe.pos_x = target_pos_x - float(reference_state.get("pos_x", 0.0))
                    keyframe.pos_y = target_pos_y - float(reference_state.get("pos_y", 0.0))
                keyframe.immediate_pos = 0
            if has_rotation:
                if is_base_lane:
                    keyframe.rotation = target_rot
                else:
                    keyframe.rotation = target_rot - float(reference_state.get("rotation", 0.0))
                keyframe.immediate_rotation = 0
            if has_scale:
                if is_base_lane:
                    keyframe.scale_x = target_scale_x
                    keyframe.scale_y = target_scale_y
                else:
                    keyframe.scale_x = target_scale_x - float(reference_state.get("scale_x", 100.0))
                    keyframe.scale_y = target_scale_y - float(reference_state.get("scale_y", 100.0))
                keyframe.immediate_scale = 0

        if created:
            lane_keyframes.sort(key=lambda frame: frame.time)

        if influence == "forward" and changes_requested:
            delta_x = target_pos_x - base_pos_x if has_translation else 0.0
            delta_y = target_pos_y - base_pos_y if has_translation else 0.0
            delta_rot = target_rot - base_rot if has_rotation else 0.0
            forward_tol = 1.0 / 600.0
            if is_base_lane:
                factor_x = (
                    target_scale_x / base_scale_x if has_scale and abs(base_scale_x) > tolerance else 1.0
                )
                factor_y = (
                    target_scale_y / base_scale_y if has_scale and abs(base_scale_y) > tolerance else 1.0
                )
                for frame in lane_keyframes:
                    if frame.time <= time_value + forward_tol:
                        continue
                    if has_translation:
                        frame.pos_x += delta_x
                        frame.pos_y += delta_y
                    if has_rotation:
                        frame.rotation += delta_rot
                    if has_scale:
                        frame.scale_x *= factor_x
                        frame.scale_y *= factor_y
            else:
                delta_scale_x = target_scale_x - base_scale_x if has_scale else 0.0
                delta_scale_y = target_scale_y - base_scale_y if has_scale else 0.0
                for frame in lane_keyframes:
                    if frame.time <= time_value + forward_tol:
                        continue
                    if has_translation:
                        frame.pos_x += delta_x
                        frame.pos_y += delta_y
                    if has_rotation:
                        frame.rotation += delta_rot
                    if has_scale:
                        frame.scale_x += delta_scale_x
                        frame.scale_y += delta_scale_y

        if is_base_lane:
            self._sync_layer_source_frames(layer)
        return True

    def _record_pose_for_global_lane(
        self,
        layer_id: int,
        time_value: float,
        influence: str,
        *,
        base_states: Optional[Dict[int, Dict[str, Any]]] = None,
        final_states: Optional[Dict[int, Dict[str, Any]]] = None,
        lane_key: TimelineLaneKey,
        tolerance: float = 1e-4,
        force: bool = False,
    ) -> bool:
        """Capture gizmo offsets for a global keyframe lane."""
        animation = self.gl_widget.player.animation
        if not animation:
            return False
        lane_keyframes = self._get_lane_keyframes(lane_key)
        if lane_keyframes is None:
            return False

        renderer = self.gl_widget.renderer
        pos_scale = renderer.local_position_multiplier * renderer.base_world_scale * renderer.position_scale
        if abs(pos_scale) < 1e-8:
            pos_scale = 1.0
        dof_flip = bool(getattr(animation, "dof_anim_flip_y", False))

        total_delta = self.gl_widget.player.get_global_lane_delta(time_value)
        ref_delta = self.gl_widget.player.get_global_lane_delta(time_value, exclude_lane=lane_key)

        def _lane_value(key: str) -> float:
            return float(total_delta.get(key, 0.0) or 0.0) - float(ref_delta.get(key, 0.0) or 0.0)

        current_lane_pos_x = _lane_value("pos_x")
        current_lane_pos_y = _lane_value("pos_y")
        current_lane_rot = _lane_value("rotation")
        current_lane_scale_x = _lane_value("scale_x")
        current_lane_scale_y = _lane_value("scale_y")

        target_total_pos_x = float(total_delta.get("pos_x", 0.0) or 0.0)
        target_total_pos_y = float(total_delta.get("pos_y", 0.0) or 0.0)
        target_total_rot = float(total_delta.get("rotation", 0.0) or 0.0)
        target_total_scale_x = float(total_delta.get("scale_x", 0.0) or 0.0)
        target_total_scale_y = float(total_delta.get("scale_y", 0.0) or 0.0)

        reference_id = None
        if base_states:
            if self.primary_selected_layer_id in base_states:
                reference_id = self.primary_selected_layer_id
            elif layer_id in base_states:
                reference_id = layer_id
            elif self.selected_layer_ids:
                for candidate_id in self.selected_layer_ids:
                    if candidate_id in base_states:
                        reference_id = candidate_id
                        break
            if reference_id is None and base_states:
                reference_id = next(iter(base_states.keys()))

        delta_mat = None
        if reference_id is not None and base_states and final_states:
            base_state = base_states.get(reference_id)
            final_state = final_states.get(reference_id)
            if base_state and final_state:
                base_mat = self._state_to_affine(base_state)
                inv_base = self._invert_affine_matrix(*base_mat)
                if inv_base:
                    final_mat = self._state_to_affine(final_state)
                    delta_mat = self._mul_affine_matrices(final_mat, inv_base)

        if delta_mat:
            g_pos_x = float(total_delta.get("pos_x", 0.0) or 0.0)
            g_pos_y = float(total_delta.get("pos_y", 0.0) or 0.0)
            g_rot = float(total_delta.get("rotation", 0.0) or 0.0)
            g_scale_x = float(total_delta.get("scale_x", 0.0) or 0.0)
            g_scale_y = float(total_delta.get("scale_y", 0.0) or 0.0)
            if dof_flip:
                g_pos_y = -g_pos_y
                g_rot = -g_rot
            g_tx = g_pos_x * pos_scale
            g_ty = g_pos_y * pos_scale
            g_rot_rad = math.radians(g_rot)
            g_cos = math.cos(g_rot_rad)
            g_sin = math.sin(g_rot_rad)
            g_sx = 1.0 + (g_scale_x / 100.0)
            g_sy = 1.0 + (g_scale_y / 100.0)
            g_mat = (g_cos * g_sx, -g_sin * g_sy, g_sin * g_sx, g_cos * g_sy, g_tx, g_ty)

            new_mat = self._mul_affine_matrices(delta_mat, g_mat)
            m00, m01, m10, m11, tx, ty = new_mat
            scale_x = math.hypot(m00, m10)
            det = m00 * m11 - m01 * m10
            if abs(scale_x) < 1e-8:
                scale_x = 0.0
                scale_y = 0.0
                rot_deg = 0.0
            else:
                scale_y = det / scale_x
                rot_deg = math.degrees(math.atan2(m10, m00))
            if dof_flip:
                ty = -ty
                rot_deg = -rot_deg
            target_total_pos_x = tx / pos_scale
            target_total_pos_y = ty / pos_scale
            target_total_rot = rot_deg
            target_total_scale_x = (scale_x - 1.0) * 100.0
            target_total_scale_y = (scale_y - 1.0) * 100.0
        else:
            offset_x, offset_y = self.gl_widget.layer_offsets.get(layer_id, (0.0, 0.0))
            rot_offset = self.gl_widget.layer_rotations.get(layer_id, 0.0)
            scale_offset_x, scale_offset_y = self.gl_widget.layer_scale_offsets.get(layer_id, (1.0, 1.0))
            delta_pos_x = offset_x / pos_scale
            delta_pos_y = offset_y / pos_scale
            delta_rot = rot_offset
            delta_scale_x = (scale_offset_x - 1.0) * 100.0
            delta_scale_y = (scale_offset_y - 1.0) * 100.0
            if dof_flip:
                delta_pos_y = -delta_pos_y
                delta_rot = -delta_rot
            target_total_pos_x += delta_pos_x
            target_total_pos_y += delta_pos_y
            target_total_rot += delta_rot
            target_total_scale_x += delta_scale_x
            target_total_scale_y += delta_scale_y

        target_pos_x = target_total_pos_x - float(ref_delta.get("pos_x", 0.0) or 0.0)
        target_pos_y = target_total_pos_y - float(ref_delta.get("pos_y", 0.0) or 0.0)
        target_rot = target_total_rot - float(ref_delta.get("rotation", 0.0) or 0.0)
        target_scale_x = target_total_scale_x - float(ref_delta.get("scale_x", 0.0) or 0.0)
        target_scale_y = target_total_scale_y - float(ref_delta.get("scale_y", 0.0) or 0.0)

        delta_pos_x = target_pos_x - current_lane_pos_x
        delta_pos_y = target_pos_y - current_lane_pos_y
        delta_rot = target_rot - current_lane_rot
        delta_scale_x = target_scale_x - current_lane_scale_x
        delta_scale_y = target_scale_y - current_lane_scale_y

        has_translation = abs(delta_pos_x) > tolerance or abs(delta_pos_y) > tolerance
        has_rotation = abs(delta_rot) > tolerance
        has_scale = abs(delta_scale_x) > tolerance or abs(delta_scale_y) > tolerance
        changes_requested = has_translation or has_rotation or has_scale
        if not changes_requested and not force:
            return False

        keyframes = self._find_keyframes_at_time_in_lane(lane_key, time_value)
        created = False
        if not keyframes:
            keyframe = KeyframeData(time=time_value)
            keyframe.pos_x = current_lane_pos_x
            keyframe.pos_y = current_lane_pos_y
            keyframe.scale_x = current_lane_scale_x
            keyframe.scale_y = current_lane_scale_y
            keyframe.rotation = current_lane_rot
            keyframe.opacity = _lane_value("opacity")
            keyframe.immediate_sprite = -1
            keyframe.immediate_rgb = -1
            lane_keyframes.append(keyframe)
            keyframes = [keyframe]
            created = True

        for keyframe in keyframes:
            keyframe.time = time_value
            if has_translation or force:
                keyframe.pos_x = target_pos_x
                keyframe.pos_y = target_pos_y
                keyframe.immediate_pos = 0
            if has_rotation or force:
                keyframe.rotation = target_rot
                keyframe.immediate_rotation = 0
            if has_scale or force:
                keyframe.scale_x = target_scale_x
                keyframe.scale_y = target_scale_y
                keyframe.immediate_scale = 0

        if created:
            lane_keyframes.sort(key=lambda frame: frame.time)

        if influence == "forward" and changes_requested:
            forward_tol = 1.0 / 600.0
            for frame in lane_keyframes:
                if frame.time <= time_value + forward_tol:
                    continue
                if has_translation:
                    frame.pos_x += delta_pos_x
                    frame.pos_y += delta_pos_y
                if has_rotation:
                    frame.rotation += delta_rot
                if has_scale:
                    frame.scale_x += delta_scale_x
                    frame.scale_y += delta_scale_y

        return True

    def _reset_pose_for_layer(
        self,
        layer_id: int,
        time_value: float,
        influence: str,
        tolerance: float = 1e-4
    ) -> bool:
        """Reset an individual layer's keyframe to its baseline state."""
        layer = self.gl_widget.get_layer_by_id(layer_id)
        if not layer:
            return False
        keyframe = self._find_keyframe_at_time(layer, time_value)
        if not keyframe:
            return False
        baseline_state = self._get_pose_baseline_state(layer_id, time_value)
        if not baseline_state:
            return False

        target_pos_x = float(baseline_state.get("pos_x", keyframe.pos_x))
        target_pos_y = float(baseline_state.get("pos_y", keyframe.pos_y))
        target_rot = float(baseline_state.get("rotation", keyframe.rotation))
        target_scale_x = float(baseline_state.get("scale_x", keyframe.scale_x))
        target_scale_y = float(baseline_state.get("scale_y", keyframe.scale_y))

        current_pos_x = float(keyframe.pos_x)
        current_pos_y = float(keyframe.pos_y)
        current_rot = float(keyframe.rotation)
        current_scale_x = float(keyframe.scale_x)
        current_scale_y = float(keyframe.scale_y)

        changed = False
        delta_pos_x = current_pos_x - target_pos_x
        delta_pos_y = current_pos_y - target_pos_y
        delta_rot = current_rot - target_rot
        factor_x = (current_scale_x / target_scale_x) if abs(target_scale_x) > tolerance else 1.0
        factor_y = (current_scale_y / target_scale_y) if abs(target_scale_y) > tolerance else 1.0

        if abs(delta_pos_x) > tolerance or abs(delta_pos_y) > tolerance:
            keyframe.pos_x = target_pos_x
            keyframe.pos_y = target_pos_y
            changed = True
        else:
            delta_pos_x = 0.0
            delta_pos_y = 0.0
        if abs(delta_rot) > tolerance:
            keyframe.rotation = target_rot
            changed = True
        else:
            delta_rot = 0.0
        if abs(current_scale_x - target_scale_x) > tolerance or abs(current_scale_y - target_scale_y) > tolerance:
            keyframe.scale_x = target_scale_x
            keyframe.scale_y = target_scale_y
            changed = True
        else:
            factor_x = 1.0
            factor_y = 1.0

        if not changed:
            return False

        keyframe.time = time_value
        layer.keyframes.sort(key=lambda frame: frame.time)

        if influence == "forward":
            forward_tol = 1.0 / 600.0
            for frame in layer.keyframes:
                if frame.time <= time_value + forward_tol:
                    continue
                if delta_pos_x or delta_pos_y:
                    frame.pos_x -= delta_pos_x
                    frame.pos_y -= delta_pos_y
                if delta_rot:
                    frame.rotation -= delta_rot
                if abs(factor_x - 1.0) > tolerance:
                    frame.scale_x = frame.scale_x / factor_x if abs(factor_x) > tolerance else frame.scale_x
                if abs(factor_y - 1.0) > tolerance:
                    frame.scale_y = frame.scale_y / factor_y if abs(factor_y) > tolerance else frame.scale_y

        self._sync_layer_source_frames(layer)
        return True

    def _clear_user_offsets_for_layers(self, layer_ids: Set[int]) -> None:
        """Remove gizmo offsets after they have been baked into keyframes."""
        for layer_id in layer_ids:
            self.gl_widget.layer_offsets.pop(layer_id, None)
            self.gl_widget.layer_rotations.pop(layer_id, None)
            self.gl_widget.layer_scale_offsets.pop(layer_id, None)
            self.gl_widget.layer_anchor_overrides.pop(layer_id, None)

    def _capture_keyframe_state(
        self,
        layer_ids: List[int],
        *,
        include_global: bool = False
    ) -> Dict[str, Any]:
        """Return deep copies of keyframes and anchor data for the provided layers."""
        snapshot: Dict[str, Any] = {"layers": {}}
        for layer_id in layer_ids:
            layer = self.gl_widget.get_layer_by_id(layer_id)
            if not layer:
                continue
            extra_lanes = [
                KeyframeLane(name=lane.name, keyframes=[replace(kf) for kf in lane.keyframes])
                for lane in getattr(layer, "extra_keyframe_lanes", [])
            ]
            snapshot["layers"][layer_id] = {
                "keyframes": [replace(kf) for kf in layer.keyframes],
                "extra_lanes": extra_lanes,
                "anchor": (float(layer.anchor_x), float(layer.anchor_y)),
            }
        if include_global:
            animation = getattr(self.gl_widget.player, "animation", None)
            global_lanes = []
            if animation:
                global_lanes = [
                    KeyframeLane(name=lane.name, keyframes=[replace(kf) for kf in lane.keyframes])
                    for lane in getattr(animation, "global_keyframe_lanes", [])
                ]
            snapshot["global_lanes"] = global_lanes
        return snapshot

    def _begin_keyframe_action(self, layer_ids: List[int], *, include_global: bool = False):
        unique = sorted({layer_id for layer_id in layer_ids if layer_id is not None})
        if not unique and not include_global:
            self._pending_keyframe_action = None
            return
        self._pending_keyframe_action = {
            'layer_ids': unique,
            'before': self._capture_keyframe_state(unique, include_global=include_global),
            'include_global': include_global,
        }

    def _finalize_keyframe_action(self, label: str):
        if not self._pending_keyframe_action:
            return
        layer_ids = self._pending_keyframe_action['layer_ids']
        before_state = self._pending_keyframe_action['before']
        include_global = bool(self._pending_keyframe_action.get('include_global', False))
        after_state = self._capture_keyframe_state(layer_ids, include_global=include_global)
        self._pending_keyframe_action = None
        if before_state == after_state:
            return
        action = {
            'label': label,
            'layer_ids': layer_ids,
            'before': before_state,
            'after': after_state,
            'type': 'keyframe',
        }
        self._push_history_action(action)

    def _apply_keyframe_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Replace layer keyframes/anchors with the provided snapshot."""
        changed = False
        layer_snapshots = snapshot.get("layers") if isinstance(snapshot, dict) else None
        if layer_snapshots is None:
            layer_snapshots = snapshot  # Backwards compatibility

        for layer_id, payload in (layer_snapshots or {}).items():
            layer = self.gl_widget.get_layer_by_id(layer_id)
            if not layer:
                continue
            frames: List[KeyframeData]
            anchor_value = None
            extra_lanes: Optional[List[KeyframeLane]] = None
            if isinstance(payload, dict):
                frames = payload.get("keyframes", [])
                anchor_value = payload.get("anchor")
                extra_lanes = payload.get("extra_lanes")
            else:
                frames = payload
            layer.keyframes = [replace(kf) for kf in frames]
            layer.keyframes.sort(key=lambda frame: frame.time)
            if extra_lanes is not None:
                layer.extra_keyframe_lanes = [
                    KeyframeLane(name=lane.name, keyframes=[replace(kf) for kf in lane.keyframes])
                    for lane in extra_lanes
                ]
            self._sync_layer_source_frames(layer)
            if anchor_value is not None:
                self._update_layer_anchor(layer, anchor_value)
            changed = True

        if isinstance(snapshot, dict) and "global_lanes" in snapshot:
            animation = getattr(self.gl_widget.player, "animation", None)
            if animation is not None:
                global_lanes = snapshot.get("global_lanes") or []
                animation.global_keyframe_lanes = [
                    KeyframeLane(name=lane.name, keyframes=[replace(kf) for kf in lane.keyframes])
                    for lane in global_lanes
                ]
                changed = True
        if changed:
            self.gl_widget.player.calculate_duration()
            self.update_timeline()
            self._refresh_timeline_keyframes()
            self.gl_widget.update()

    def _update_layer_anchor(
        self,
        layer: LayerData,
        anchor: Optional[Tuple[float, float]],
        tolerance: float = 1e-4,
    ) -> bool:
        """Apply a new anchor value to the layer and mirror it to JSON/cache."""
        if not layer or anchor is None:
            return False
        target_x = float(anchor[0])
        target_y = float(anchor[1])
        if (
            abs(layer.anchor_x - target_x) < tolerance
            and abs(layer.anchor_y - target_y) < tolerance
        ):
            return False
        layer.anchor_x = target_x
        layer.anchor_y = target_y
        source = self.layer_source_lookup.get(layer.layer_id)
        if source is not None:
            source["anchor_x"] = target_x
            source["anchor_y"] = target_y
            anchor_block = source.get("anchor")
            if isinstance(anchor_block, dict):
                anchor_block["x"] = target_x
                anchor_block["y"] = target_y
        if self.base_layer_cache:
            for cached in self.base_layer_cache:
                if cached.layer_id == layer.layer_id:
                    cached.anchor_x = target_x
                    cached.anchor_y = target_y
                    break
        return True

    def _reset_edit_history(self):
        """Clear undo/redo stacks for all edits."""
        self._history_stack.clear()
        self._history_redo_stack.clear()
        self._pending_keyframe_action = None
        self._update_keyframe_history_controls()

    def _update_keyframe_history_controls(self):
        """Update control panel buttons based on undo stack state."""
        undo_available = bool(self._history_stack) and self._history_stack[-1].get('type') == 'keyframe'
        redo_available = bool(self._history_redo_stack) and self._history_redo_stack[-1].get('type') == 'keyframe'
        self.control_panel.set_keyframe_history_state(undo_available, redo_available)

    def undo_keyframe_action(self) -> bool:
        """Undo the most recent keyframe edit."""
        if self._undo_history_action(required_type='keyframe'):
            return True
        self.log_widget.log("No keyframe edits to undo.", "INFO")
        return False

    def redo_keyframe_action(self) -> bool:
        """Redo the last undone keyframe edit."""
        if self._redo_history_action(required_type='keyframe'):
            return True
        self.log_widget.log("No keyframe edits to redo.", "INFO")
        return False

    def delete_other_keyframes(self):
        """Flatten the animation so only the current pose remains for every layer."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before modifying keyframes.", "WARNING")
            return
        target_layer_ids: List[int]
        if self.selected_layer_ids:
            target_layer_ids = [
                layer.layer_id
                for layer in animation.layers
                if layer.layer_id in self.selected_layer_ids
            ]
            if not target_layer_ids:
                self.log_widget.log(
                    "Selected layers are not available in this animation.",
                    "WARNING"
                )
                return
        else:
            target_layer_ids = [
                layer.layer_id for layer in animation.layers if layer.layer_id is not None
            ]
            if not target_layer_ids:
                self.log_widget.log("This animation has no editable layers.", "WARNING")
                return
        layer_ids = target_layer_ids
        target_layer_set = set(layer_ids)

        # Build lookup tables for hierarchy traversal
        layer_lookup: Dict[int, LayerData] = {}
        for layer in animation.layers:
            if layer.layer_id is not None:
                layer_lookup[layer.layer_id] = layer

        children_map: Dict[int, List[int]] = {}
        root_layer_ids: List[int] = []
        for layer in animation.layers:
            if layer.layer_id is None:
                continue
            if layer.layer_id not in target_layer_set:
                continue
            parent_id = layer.parent_id
            if parent_id is None or parent_id < 0 or parent_id not in layer_lookup:
                root_layer_ids.append(layer.layer_id)
            else:
                children_map.setdefault(parent_id, []).append(layer.layer_id)

        current_time = round(self.gl_widget.player.current_time, 5)
        original_time = self.gl_widget.player.current_time
        self.gl_widget.player.current_time = current_time
        self._begin_keyframe_action(layer_ids)

        # Only layers with pending gizmo offsets/overrides need pose baking
        pose_layer_ids = {
            layer_id for layer_id in layer_ids
            if layer_id is not None and layer_id in layer_lookup
        }
        pose_bake_required = bool(pose_layer_ids)
        evaluated_world_state = self.gl_widget._build_layer_world_states(current_time)
        pose_state_cache: Dict[
            Tuple[int, ...],
            Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]
        ] = {}

        def pose_state_key(layer_id: int) -> Tuple[int, ...]:
            chain: List[int] = []
            current = layer_lookup.get(layer_id)
            visited: Set[int] = set()
            while current:
                lid = current.layer_id
                if lid in visited or lid is None:
                    break
                visited.add(lid)
                chain.append(lid)
                parent_id = current.parent_id
                current = layer_lookup.get(parent_id)
            chain.sort()
            return tuple(chain) if chain else (layer_id,)

        captured = 0
        for layer in animation.layers:
            if layer.layer_id is None:
                continue
            if layer.layer_id not in target_layer_set:
                continue
            if pose_bake_required and layer.layer_id in pose_layer_ids:
                cache_key = pose_state_key(layer.layer_id)
                base_states_map, final_states_map = pose_state_cache.get(cache_key, ({}, {}))
                if not base_states_map or not final_states_map:
                    ids = set(cache_key)
                    base_states_map, final_states_map = self._gather_pose_state_maps(ids)
                    pose_state_cache[cache_key] = (base_states_map, final_states_map)
                if self._record_pose_for_layer(
                    layer.layer_id,
                    current_time,
                    "current",
                    base_states_map,
                    final_states_map,
                    force=True,
                ):
                    captured += 1
            render_snapshot = evaluated_world_state.get(layer.layer_id) if evaluated_world_state else None
            layer_state = self.gl_widget.player.get_layer_state(layer, current_time)
            keyframe = self._find_keyframe_at_time(layer, current_time, tolerance=1e-4)
            snapshot_sprite = None
            if render_snapshot:
                snapshot_sprite = render_snapshot.get('sprite_name')
            if not snapshot_sprite:
                snapshot_sprite = layer_state.get('sprite_name')
            if keyframe:
                if not snapshot_sprite:
                    snapshot_sprite = keyframe.sprite_name
                if snapshot_sprite:
                    keyframe.sprite_name = snapshot_sprite
                    if keyframe.immediate_sprite == -1:
                        keyframe.immediate_sprite = 1
                layer.keyframes = [replace(keyframe)]
            else:
                # Capture the interpolated state at current time before clearing keyframes
                layer.keyframes = [KeyframeData(
                    time=current_time,
                    pos_x=float(layer_state.get('pos_x', 0.0)),
                    pos_y=float(layer_state.get('pos_y', 0.0)),
                    scale_x=float(layer_state.get('scale_x', 100.0)),
                    scale_y=float(layer_state.get('scale_y', 100.0)),
                    rotation=float(layer_state.get('rotation', 0.0)),
                    opacity=float(layer_state.get('opacity', 100.0)),
                    sprite_name=layer_state.get('sprite_name', ''),
                    r=int(layer_state.get('r', 255)),
                    g=int(layer_state.get('g', 255)),
                    b=int(layer_state.get('b', 255)),
                    immediate_pos=1,  # NONE interpolation - hold values
                    immediate_scale=1,
                    immediate_rotation=1,
                    immediate_opacity=1,
                    immediate_sprite=1,
                    immediate_rgb=1
                )]
            layer.keyframes.sort(key=lambda frame: frame.time)
            self._sync_layer_source_frames(layer)
        self._clear_user_offsets_for_layers(set(layer_ids))
        self._finalize_keyframe_action("delete_other_keyframes")
        self.gl_widget.player.current_time = original_time
        self.gl_widget.player.calculate_duration()
        self.update_timeline()
        self._refresh_timeline_keyframes()
        self.gl_widget.update()
        self.log_widget.log(
            "Captured the current pose for every layer and removed all other keyframes.",
            "SUCCESS"
        )

    def extend_animation_duration_dialog(self):
        """Prompt the user for a new animation duration."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before extending it.", "WARNING")
            return
        current_duration = float(max(self.gl_widget.player.duration, 0.0))
        suggested = max(current_duration, 0.1)
        minimum = 1e-3
        new_duration, ok = QInputDialog.getDouble(
            self,
            "Set Animation Duration",
            "New total duration (seconds):",
            suggested,
            minimum,
            3600.0,
            3
        )
        if not ok:
            return
        self._set_animation_duration(float(new_duration))

    def _set_animation_duration(self, new_duration: float):
        """Adjust animation length, extending or trimming keyframes."""
        animation = self.gl_widget.player.animation
        if not animation:
            self.log_widget.log("Load an animation before extending it.", "WARNING")
            return
        current_duration = float(max(self.gl_widget.player.duration, 0.0))
        if new_duration <= 0.0:
            self.log_widget.log("Duration must be greater than zero seconds.", "WARNING")
            return
        if abs(new_duration - current_duration) <= 1e-6:
            self.log_widget.log("Duration unchanged.", "INFO")
            return
        layer_ids = [layer.layer_id for layer in animation.layers if layer.layer_id is not None]
        if not layer_ids:
            self.log_widget.log("This animation has no editable layers.", "WARNING")
            return
        self._begin_keyframe_action(layer_ids)
        trimmed_snapshots: Dict[int, Dict[str, Any]] = {}
        shortening = new_duration < current_duration
        tolerance = 1e-4
        if shortening:
            for layer in animation.layers:
                if layer.layer_id is None:
                    continue
                trimmed_snapshots[layer.layer_id] = self.gl_widget.player.get_layer_state(layer, new_duration)
        for layer in animation.layers:
            if layer.layer_id is None:
                continue
            if not shortening:
                if layer.keyframes:
                    last_keyframe = max(layer.keyframes, key=lambda frame: frame.time)
                    duplicated = replace(last_keyframe)
                else:
                    duplicated = KeyframeData(time=new_duration)
                duplicated.time = new_duration
                layer.keyframes.append(duplicated)
            else:
                snapshots = trimmed_snapshots.get(layer.layer_id, {})
                preserved: List[KeyframeData] = []
                for keyframe in layer.keyframes:
                    if keyframe.time < new_duration - tolerance:
                        preserved.append(replace(keyframe))
                    elif abs(keyframe.time - new_duration) <= tolerance:
                        clone = replace(keyframe)
                        clone.time = new_duration
                        preserved.append(clone)
                    # Keyframes after the new duration are discarded
                if not preserved or preserved[-1].time < new_duration - tolerance:
                    preserved.append(
                        KeyframeData(
                            time=new_duration,
                            pos_x=float(snapshots.get('pos_x', 0.0)),
                            pos_y=float(snapshots.get('pos_y', 0.0)),
                            scale_x=float(snapshots.get('scale_x', 100.0)),
                            scale_y=float(snapshots.get('scale_y', 100.0)),
                            rotation=float(snapshots.get('rotation', 0.0)),
                            opacity=float(snapshots.get('opacity', 100.0)),
                            sprite_name=snapshots.get('sprite_name', ''),
                            r=int(snapshots.get('r', 255)),
                            g=int(snapshots.get('g', 255)),
                            b=int(snapshots.get('b', 255)),
                            immediate_pos=1,
                            immediate_scale=1,
                            immediate_rotation=1,
                            immediate_opacity=1,
                            immediate_sprite=1,
                            immediate_rgb=1
                        )
                    )
                layer.keyframes = preserved
            layer.keyframes.sort(key=lambda frame: frame.time)
            self._sync_layer_source_frames(layer)
        action_label = "shrink_duration" if shortening else "extend_duration"
        self._finalize_keyframe_action(action_label)
        self.gl_widget.player.calculate_duration()
        if self.gl_widget.player.current_time > new_duration:
            self.gl_widget.player.current_time = new_duration
        self.update_timeline()
        self._refresh_timeline_keyframes()
        self.gl_widget.update()
        verb = "Shortened" if shortening else "Extended"
        self.log_widget.log(f"{verb} animation to {new_duration:.3f} seconds.", "SUCCESS")

    def _gather_pose_state_maps(
        self,
        target_ids: Set[int]
    ) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
        """Return (base_states_without_offsets, final_states_with_offsets)."""
        if not target_ids:
            return {}, {}
        final_raw = self.gl_widget._build_layer_world_states(apply_constraints=False)
        final_map = copy.deepcopy(final_raw)
        snapshots: Dict[int, Dict[str, Any]] = {}
        for layer_id in target_ids:
            snapshots[layer_id] = {
                "offset": self.gl_widget.layer_offsets.get(layer_id),
                "rotation": self.gl_widget.layer_rotations.get(layer_id),
                "scale": self.gl_widget.layer_scale_offsets.get(layer_id),
                "anchor": self.gl_widget.layer_anchor_overrides.get(layer_id),
            }
            self.gl_widget.layer_offsets[layer_id] = (0.0, 0.0)
            self.gl_widget.layer_rotations[layer_id] = 0.0
            self.gl_widget.layer_scale_offsets[layer_id] = (1.0, 1.0)
            if layer_id in self.gl_widget.layer_anchor_overrides:
                self.gl_widget.layer_anchor_overrides.pop(layer_id, None)
        base_raw = copy.deepcopy(self.gl_widget._build_layer_world_states(apply_constraints=False))
        for layer_id, snapshot in snapshots.items():
            offset_val = snapshot.get("offset")
            rot_val = snapshot.get("rotation")
            scale_val = snapshot.get("scale")
            anchor_val = snapshot.get("anchor")
            if offset_val is None:
                self.gl_widget.layer_offsets.pop(layer_id, None)
            else:
                self.gl_widget.layer_offsets[layer_id] = offset_val
            if rot_val is None:
                self.gl_widget.layer_rotations.pop(layer_id, None)
            else:
                self.gl_widget.layer_rotations[layer_id] = rot_val
            if scale_val is None:
                self.gl_widget.layer_scale_offsets.pop(layer_id, None)
            else:
                self.gl_widget.layer_scale_offsets[layer_id] = scale_val
            if anchor_val is None:
                self.gl_widget.layer_anchor_overrides.pop(layer_id, None)
            else:
                self.gl_widget.layer_anchor_overrides[layer_id] = anchor_val
        self.gl_widget._build_layer_world_states(apply_constraints=False)
        return base_raw, final_map

    def _world_delta_to_local(
        self,
        layer: LayerData,
        state_map: Dict[int, Dict[str, Any]],
        world_dx: float,
        world_dy: float,
    ) -> Tuple[float, float]:
        """Convert a world-space offset into the parent's local coordinates."""
        parent_id = layer.parent_id
        if parent_id is None or parent_id < 0:
            return world_dx, world_dy
        parent_state = state_map.get(parent_id)
        if not parent_state:
            return world_dx, world_dy
        pm00 = float(parent_state.get("m00", 1.0))
        pm01 = float(parent_state.get("m01", 0.0))
        pm10 = float(parent_state.get("m10", 0.0))
        pm11 = float(parent_state.get("m11", 1.0))
        det = pm00 * pm11 - pm01 * pm10
        if abs(det) < 1e-6:
            return world_dx, world_dy
        inv00 = pm11 / det
        inv01 = -pm01 / det
        inv10 = -pm10 / det
        inv11 = pm00 / det
        local_x = inv00 * world_dx + inv01 * world_dy
        local_y = inv10 * world_dx + inv11 * world_dy
        return local_x, local_y

    def _build_children_map(self) -> Dict[int, List[int]]:
        """Return a parent -> [children] mapping for the current animation."""
        animation = self.gl_widget.player.animation
        if not animation:
            return {}
        children: Dict[int, List[int]] = {}
        for layer in animation.layers:
            if layer.parent_id is None or layer.parent_id < 0:
                continue
            children.setdefault(layer.parent_id, []).append(layer.layer_id)
        return children

    def _collect_compensation_ids(
        self,
        parent_ids: Set[int],
        selected_ids: Set[int],
    ) -> Set[int]:
        """Collect descendant ids to preserve (skip selected nodes and their subtrees)."""
        if not parent_ids:
            return set()
        children_map = self._build_children_map()
        result: Set[int] = set()
        queue = list(parent_ids)
        while queue:
            parent_id = queue.pop()
            for child_id in children_map.get(parent_id, []):
                if child_id in selected_ids:
                    continue
                if child_id in result:
                    continue
                result.add(child_id)
                queue.append(child_id)
        return result

    def _collect_compensation_order(
        self,
        parent_ids: Set[int],
        selected_ids: Set[int],
    ) -> List[int]:
        """Return descendant ids in parent-first order (skips selected nodes/subtrees)."""
        if not parent_ids:
            return []
        children_map = self._build_children_map()
        ordered: List[int] = []
        queue: List[int] = list(parent_ids)
        seen: Set[int] = set()
        while queue:
            parent_id = queue.pop(0)
            for child_id in children_map.get(parent_id, []):
                if child_id in selected_ids:
                    continue
                if child_id in seen:
                    continue
                seen.add(child_id)
                ordered.append(child_id)
                queue.append(child_id)
        return ordered

    def _apply_offsets_to_state_map(
        self,
        state_map: Dict[int, Dict[str, Any]]
    ) -> Dict[int, Dict[str, Any]]:
        """Return a copy of state_map with translation offsets applied to tx/ty/anchor."""
        if not state_map:
            return {}
        updated: Dict[int, Dict[str, Any]] = {}
        for layer_id, state in state_map.items():
            offset = self.gl_widget.layer_offsets.get(layer_id)
            if not offset or (abs(offset[0]) < 1e-6 and abs(offset[1]) < 1e-6):
                updated[layer_id] = state
                continue
            dx, dy = offset
            new_state = dict(state)
            if "tx" in new_state:
                new_state["tx"] = float(new_state.get("tx", 0.0)) + dx
            if "ty" in new_state:
                new_state["ty"] = float(new_state.get("ty", 0.0)) + dy
            if "anchor_world_x" in new_state:
                new_state["anchor_world_x"] = float(new_state.get("anchor_world_x", 0.0)) + dx
            if "anchor_world_y" in new_state:
                new_state["anchor_world_y"] = float(new_state.get("anchor_world_y", 0.0)) + dy
            updated[layer_id] = new_state
        return updated

    def _invert_affine_matrix(
        self,
        m00: float,
        m01: float,
        m10: float,
        m11: float,
        tx: float,
        ty: float,
    ) -> Optional[Tuple[float, float, float, float, float, float]]:
        det = m00 * m11 - m01 * m10
        if abs(det) < 1e-8:
            return None
        inv00 = m11 / det
        inv01 = -m01 / det
        inv10 = -m10 / det
        inv11 = m00 / det
        inv_tx = -(inv00 * tx + inv01 * ty)
        inv_ty = -(inv10 * tx + inv11 * ty)
        return inv00, inv01, inv10, inv11, inv_tx, inv_ty

    def _mul_affine_matrices(
        self,
        a: Tuple[float, float, float, float, float, float],
        b: Tuple[float, float, float, float, float, float],
    ) -> Tuple[float, float, float, float, float, float]:
        a00, a01, a10, a11, atx, aty = a
        b00, b01, b10, b11, btx, bty = b
        m00 = a00 * b00 + a01 * b10
        m01 = a00 * b01 + a01 * b11
        m10 = a10 * b00 + a11 * b10
        m11 = a10 * b01 + a11 * b11
        tx = a00 * btx + a01 * bty + atx
        ty = a10 * btx + a11 * bty + aty
        return m00, m01, m10, m11, tx, ty

    def _state_to_affine(self, state: Dict[str, Any]) -> Tuple[float, float, float, float, float, float]:
        return (
            float(state.get("m00", 1.0)),
            float(state.get("m01", 0.0)),
            float(state.get("m10", 0.0)),
            float(state.get("m11", 1.0)),
            float(state.get("tx", 0.0)),
            float(state.get("ty", 0.0)),
        )

    def _global_lane_matrix(
        self,
        time_value: float,
    ) -> Optional[Tuple[float, float, float, float, float, float]]:
        """Return the global lane transform matrix in world units for the given time."""
        player = getattr(self.gl_widget, "player", None)
        if not player or not player.animation:
            return None
        delta = player.get_global_lane_delta(time_value)
        if not delta:
            return None
        pos_x = float(delta.get("pos_x", 0.0) or 0.0)
        pos_y = float(delta.get("pos_y", 0.0) or 0.0)
        rotation = float(delta.get("rotation", 0.0) or 0.0)
        scale_x = float(delta.get("scale_x", 0.0) or 0.0)
        scale_y = float(delta.get("scale_y", 0.0) or 0.0)
        epsilon = 1e-6
        if (
            abs(pos_x) <= epsilon
            and abs(pos_y) <= epsilon
            and abs(rotation) <= epsilon
            and abs(scale_x) <= epsilon
            and abs(scale_y) <= epsilon
        ):
            return None
        if getattr(player.animation, "dof_anim_flip_y", False):
            pos_y = -pos_y
            rotation = -rotation
        renderer = self.gl_widget.renderer
        pos_scale = renderer.local_position_multiplier * renderer.base_world_scale * renderer.position_scale
        if abs(pos_scale) < 1e-8:
            pos_scale = 1.0
        tx = pos_x * pos_scale
        ty = pos_y * pos_scale
        rot_rad = math.radians(rotation)
        cos_r = math.cos(rot_rad)
        sin_r = math.sin(rot_rad)
        sx = 1.0 + (scale_x / 100.0)
        sy = 1.0 + (scale_y / 100.0)
        m00 = sx * cos_r
        m01 = -sy * sin_r
        m10 = sx * sin_r
        m11 = sy * cos_r
        return (m00, m01, m10, m11, tx, ty)

    def _compute_local_matrix(
        self,
        parent_state: Dict[str, Any],
        child_state: Dict[str, Any],
    ) -> Optional[Tuple[float, float, float, float, float, float]]:
        parent_mat = self._state_to_affine(parent_state)
        inv_parent = self._invert_affine_matrix(*parent_mat)
        if not inv_parent:
            return None
        child_mat = self._state_to_affine(child_state)
        return self._mul_affine_matrices(inv_parent, child_mat)

    def _get_anchor_world_units(self, layer_id: int) -> Tuple[float, float]:
        layer = self.gl_widget.get_layer_by_id(layer_id)
        if not layer:
            return (0.0, 0.0)
        anchor_local = self.gl_widget.layer_anchor_overrides.get(
            layer_id, (layer.anchor_x, layer.anchor_y)
        )
        renderer = self.gl_widget.renderer
        anchor_x = float(anchor_local[0]) * renderer.anchor_scale_x
        anchor_y = float(anchor_local[1]) * renderer.anchor_scale_y
        if renderer.anchor_flip_x:
            anchor_x = -anchor_x
        if renderer.anchor_flip_y:
            anchor_y = -anchor_y
        anchor_x = (anchor_x + renderer.anchor_bias_x) * renderer.base_world_scale * renderer.position_scale
        anchor_y = (anchor_y + renderer.anchor_bias_y) * renderer.base_world_scale * renderer.position_scale
        return anchor_x, anchor_y

    def _decompose_local_matrix_to_keyframe(
        self,
        layer_id: int,
        local_mat: Tuple[float, float, float, float, float, float],
    ) -> Optional[Tuple[float, float, float, float, float]]:
        renderer = self.gl_widget.renderer
        m00, m01, m10, m11, tx, ty = local_mat
        anchor_x, anchor_y = self._get_anchor_world_units(layer_id)
        pos_world_x = tx + (m00 * anchor_x + m01 * anchor_y)
        pos_world_y = ty + (m10 * anchor_x + m11 * anchor_y)
        pos_scale = renderer.local_position_multiplier * renderer.base_world_scale * renderer.position_scale
        if abs(pos_scale) < 1e-8:
            pos_scale = 1.0
        pos_x = pos_world_x / pos_scale
        pos_y = pos_world_y / pos_scale

        scale_x = math.hypot(m00, m10)
        det = m00 * m11 - m01 * m10
        if abs(scale_x) < 1e-8:
            scale_x = 0.0
            scale_y = 0.0
            rotation = 0.0
        else:
            scale_y = det / scale_x
            rotation = math.degrees(math.atan2(m10, m00))
        rot_key = rotation - renderer.rotation_bias
        scale_key_x = (
            (scale_x / renderer.scale_bias_x) * 100.0
            if abs(renderer.scale_bias_x) > 1e-8
            else 0.0
        )
        scale_key_y = (
            (scale_y / renderer.scale_bias_y) * 100.0
            if abs(renderer.scale_bias_y) > 1e-8
            else 0.0
        )
        return pos_x, pos_y, rot_key, scale_key_x, scale_key_y

    def _apply_child_compensation(
        self,
        parent_state: Dict[str, Any],
        child_id: int,
        child_state: Dict[str, Any],
        post_child_state: Optional[Dict[str, Any]],
        time_value: float,
        influence: str,
        tolerance: float = 1e-4,
        translation_only: bool = False,
    ) -> bool:
        layer = self.gl_widget.get_layer_by_id(child_id)
        if not layer:
            return False
        if translation_only:
            parent_id = layer.parent_id
            if parent_id is None or parent_id < 0:
                return False
            current_state = post_child_state or {}
            desired_anchor_x = float(child_state.get("anchor_world_x", child_state.get("tx", 0.0)))
            desired_anchor_y = float(child_state.get("anchor_world_y", child_state.get("ty", 0.0)))
            current_anchor_x = float(current_state.get("anchor_world_x", current_state.get("tx", 0.0)))
            current_anchor_y = float(current_state.get("anchor_world_y", current_state.get("ty", 0.0)))
            world_dx = desired_anchor_x - current_anchor_x
            world_dy = desired_anchor_y - current_anchor_y
            if abs(world_dx) <= tolerance and abs(world_dy) <= tolerance:
                return False

            local_dx, local_dy = self._world_delta_to_local(
                layer,
                {parent_id: parent_state},
                world_dx,
                world_dy,
            )
            local_state = self.gl_widget.player.get_layer_state(layer, time_value, include_global=False)
            base_pos_x = float(local_state.get("pos_x", 0.0))
            base_pos_y = float(local_state.get("pos_y", 0.0))
            target_pos_x = base_pos_x + local_dx
            target_pos_y = base_pos_y + local_dy
            delta_pos_x = target_pos_x - base_pos_x
            delta_pos_y = target_pos_y - base_pos_y
            if abs(delta_pos_x) <= tolerance and abs(delta_pos_y) <= tolerance:
                return False

            keyframes = self._find_keyframes_at_time(layer, time_value)
            created = False
            if not keyframes:
                keyframe = self._create_keyframe_from_state(layer, time_value)
                layer.keyframes.append(keyframe)
                keyframes = [keyframe]
                created = True
            for keyframe in keyframes:
                keyframe.time = time_value
                keyframe.pos_x = target_pos_x
                keyframe.pos_y = target_pos_y
                keyframe.immediate_pos = 0

            if created:
                layer.keyframes.sort(key=lambda frame: frame.time)

            if influence == "forward":
                forward_tol = 1.0 / 600.0
                for frame in layer.keyframes:
                    if frame.time <= time_value + forward_tol:
                        continue
                    frame.pos_x += delta_pos_x
                    frame.pos_y += delta_pos_y

            self._sync_layer_source_frames(layer)
            return True
        local_mat = self._compute_local_matrix(parent_state, child_state)
        if not local_mat:
            return False
        target = self._decompose_local_matrix_to_keyframe(child_id, local_mat)
        if not target:
            return False
        target_pos_x, target_pos_y, target_rot, target_scale_x, target_scale_y = target

        local_state = self.gl_widget.player.get_layer_state(layer, time_value, include_global=False)
        base_pos_x = float(local_state.get("pos_x", 0.0))
        base_pos_y = float(local_state.get("pos_y", 0.0))
        base_rot = float(local_state.get("rotation", 0.0))
        base_scale_x = float(local_state.get("scale_x", 100.0))
        base_scale_y = float(local_state.get("scale_y", 100.0))

        delta_pos_x = target_pos_x - base_pos_x
        delta_pos_y = target_pos_y - base_pos_y
        delta_rot = target_rot - base_rot
        has_translation = abs(delta_pos_x) > tolerance or abs(delta_pos_y) > tolerance
        has_rotation = abs(delta_rot) > tolerance
        has_scale = (
            abs(target_scale_x - base_scale_x) > tolerance
            or abs(target_scale_y - base_scale_y) > tolerance
        )
        if not (has_translation or has_rotation or has_scale):
            return False

        keyframes = self._find_keyframes_at_time(layer, time_value)
        created = False
        if not keyframes:
            keyframe = self._create_keyframe_from_state(layer, time_value)
            layer.keyframes.append(keyframe)
            keyframes = [keyframe]
            created = True
        for keyframe in keyframes:
            keyframe.time = time_value
            if has_translation:
                keyframe.pos_x = target_pos_x
                keyframe.pos_y = target_pos_y
                keyframe.immediate_pos = 0
            if has_rotation:
                keyframe.rotation = target_rot
                keyframe.immediate_rotation = 0
            if has_scale:
                keyframe.scale_x = target_scale_x
                keyframe.scale_y = target_scale_y
                keyframe.immediate_scale = 0

        if created:
            layer.keyframes.sort(key=lambda frame: frame.time)

        if influence == "forward":
            forward_tol = 1.0 / 600.0
            factor_x = (
                target_scale_x / base_scale_x if abs(base_scale_x) > tolerance else 1.0
            )
            factor_y = (
                target_scale_y / base_scale_y if abs(base_scale_y) > tolerance else 1.0
            )
            for frame in layer.keyframes:
                if frame.time <= time_value + forward_tol:
                    continue
                if has_translation:
                    frame.pos_x += delta_pos_x
                    frame.pos_y += delta_pos_y
                if has_rotation:
                    frame.rotation += delta_rot
                if has_scale:
                    frame.scale_x = frame.scale_x * factor_x if abs(factor_x) > tolerance else frame.scale_x
                    frame.scale_y = frame.scale_y * factor_y if abs(factor_y) > tolerance else frame.scale_y

        self._sync_layer_source_frames(layer)
        return True

    def _apply_record_compensation(
        self,
        parent_ids: Set[int],
        time_value: float,
        influence: str,
        parent_states: Dict[int, Dict[str, Any]],
        desired_states: Dict[int, Dict[str, Any]],
        selected_ids: Set[int],
        tolerance: float = 1e-4,
        translation_only: bool = False,
    ) -> Set[int]:
        if not parent_ids or not parent_states or not desired_states:
            return set()
        animation = self.gl_widget.player.animation
        if not animation:
            return set()
        layer_map = {layer.layer_id: layer for layer in animation.layers}
        ordered_ids = self._collect_compensation_order(parent_ids, selected_ids)
        if not ordered_ids:
            return set()
        compensation_ids = set(ordered_ids)
        applied_ids: Set[int] = set()
        parent_states_effective = dict(parent_states)
        for child_id in ordered_ids:
            layer = layer_map.get(child_id)
            if not layer:
                continue
            parent_id = layer.parent_id
            if parent_id is None or parent_id < 0:
                continue
            parent_state = parent_states_effective.get(parent_id)
            child_state = desired_states.get(child_id)
            if not parent_state or not child_state:
                continue
            parent_ref = parent_state
            child_local = self._compute_local_matrix(parent_ref, child_state)
            if child_local:
                if self._apply_child_compensation_local(
                    child_id,
                    child_local,
                    time_value,
                    influence,
                    tolerance,
                ):
                    applied_ids.add(child_id)
                parent_states_effective[child_id] = child_state
        return applied_ids

    def _apply_child_compensation_local(
        self,
        child_id: int,
        local_mat: Tuple[float, float, float, float, float, float],
        time_value: float,
        influence: str,
        tolerance: float = 1e-4,
    ) -> bool:
        layer = self.gl_widget.get_layer_by_id(child_id)
        if not layer:
            return False
        target = self._decompose_local_matrix_to_keyframe(child_id, local_mat)
        if not target:
            return False
        target_pos_x, target_pos_y, target_rot, target_scale_x, target_scale_y = target

        local_state = self.gl_widget.player.get_layer_state(layer, time_value, include_global=False)
        base_pos_x = float(local_state.get("pos_x", 0.0))
        base_pos_y = float(local_state.get("pos_y", 0.0))
        base_rot = float(local_state.get("rotation", 0.0))
        base_scale_x = float(local_state.get("scale_x", 100.0))
        base_scale_y = float(local_state.get("scale_y", 100.0))

        delta_pos_x = target_pos_x - base_pos_x
        delta_pos_y = target_pos_y - base_pos_y
        delta_rot = target_rot - base_rot
        has_translation = abs(delta_pos_x) > tolerance or abs(delta_pos_y) > tolerance
        has_rotation = abs(delta_rot) > tolerance
        has_scale = (
            abs(target_scale_x - base_scale_x) > tolerance
            or abs(target_scale_y - base_scale_y) > tolerance
        )
        if not (has_translation or has_rotation or has_scale):
            return False

        keyframes = self._find_keyframes_at_time(layer, time_value)
        created = False
        if not keyframes:
            keyframe = self._create_keyframe_from_state(layer, time_value)
            layer.keyframes.append(keyframe)
            keyframes = [keyframe]
            created = True
        for keyframe in keyframes:
            keyframe.time = time_value
            if has_translation:
                keyframe.pos_x = target_pos_x
                keyframe.pos_y = target_pos_y
                keyframe.immediate_pos = 0
            if has_rotation:
                keyframe.rotation = target_rot
                keyframe.immediate_rotation = 0
            if has_scale:
                keyframe.scale_x = target_scale_x
                keyframe.scale_y = target_scale_y
                keyframe.immediate_scale = 0

        if created:
            layer.keyframes.sort(key=lambda frame: frame.time)

        if influence == "forward":
            forward_tol = 1.0 / 600.0
            factor_x = (
                target_scale_x / base_scale_x if abs(base_scale_x) > tolerance else 1.0
            )
            factor_y = (
                target_scale_y / base_scale_y if abs(base_scale_y) > tolerance else 1.0
            )
            for frame in layer.keyframes:
                if frame.time <= time_value + forward_tol:
                    continue
                if has_translation:
                    frame.pos_x += delta_pos_x
                    frame.pos_y += delta_pos_y
                if has_rotation:
                    frame.rotation += delta_rot
                if has_scale:
                    frame.scale_x = frame.scale_x * factor_x if abs(factor_x) > tolerance else frame.scale_x
                    frame.scale_y = frame.scale_y * factor_y if abs(factor_y) > tolerance else frame.scale_y

        self._sync_layer_source_frames(layer)
        return True
    def _find_keyframe_at_time(
        self,
        layer: LayerData,
        time_value: float,
        tolerance: float = 1.0 / 600.0
    ) -> Optional[KeyframeData]:
        """Return the first keyframe whose timestamp is within tolerance."""
        for keyframe in layer.keyframes:
            if abs(keyframe.time - time_value) <= tolerance:
                return keyframe
        return None

    def _get_lane_keyframes(self, lane_key: TimelineLaneKey) -> Optional[List[KeyframeData]]:
        animation = getattr(self.gl_widget.player, "animation", None)
        if lane_key.scope == "global":
            if not animation:
                return None
            lanes = getattr(animation, "global_keyframe_lanes", []) or []
            if 0 <= lane_key.lane_index < len(lanes):
                return lanes[lane_key.lane_index].keyframes
            return None
        layer = self.gl_widget.get_layer_by_id(lane_key.layer_id)
        if not layer:
            return None
        if lane_key.lane_index == 0:
            return layer.keyframes
        extra = getattr(layer, "extra_keyframe_lanes", []) or []
        idx = lane_key.lane_index - 1
        if 0 <= idx < len(extra):
            return extra[idx].keyframes
        return None

    def _set_lane_keyframes(self, lane_key: TimelineLaneKey, keyframes: List[KeyframeData]) -> None:
        animation = getattr(self.gl_widget.player, "animation", None)
        if lane_key.scope == "global":
            if not animation:
                return
            lanes = getattr(animation, "global_keyframe_lanes", []) or []
            if 0 <= lane_key.lane_index < len(lanes):
                lanes[lane_key.lane_index].keyframes = keyframes
            return
        layer = self.gl_widget.get_layer_by_id(lane_key.layer_id)
        if not layer:
            return
        if lane_key.lane_index == 0:
            layer.keyframes = keyframes
            return
        extra = getattr(layer, "extra_keyframe_lanes", []) or []
        idx = lane_key.lane_index - 1
        if 0 <= idx < len(extra):
            extra[idx].keyframes = keyframes

    def _get_lane_object(self, lane_key: TimelineLaneKey) -> Optional[KeyframeLane]:
        animation = getattr(self.gl_widget.player, "animation", None)
        if lane_key.scope == "global":
            if not animation:
                return None
            lanes = getattr(animation, "global_keyframe_lanes", []) or []
            if 0 <= lane_key.lane_index < len(lanes):
                return lanes[lane_key.lane_index]
            return None
        layer = self.gl_widget.get_layer_by_id(lane_key.layer_id)
        if not layer:
            return None
        if lane_key.lane_index <= 0:
            return None
        extra = getattr(layer, "extra_keyframe_lanes", []) or []
        idx = lane_key.lane_index - 1
        if 0 <= idx < len(extra):
            return extra[idx]
        return None

    def _lane_label(self, lane_key: TimelineLaneKey) -> str:
        if lane_key.scope == "global":
            lane = self._get_lane_object(lane_key)
            label = lane.name if lane else ""
            return label.strip() or f"Global {lane_key.lane_index + 1}"
        if lane_key.lane_index == 0:
            return "Base"
        lane = self._get_lane_object(lane_key)
        label = lane.name if lane else ""
        return label.strip() or f"Lane {lane_key.lane_index}"

    def _add_layer_keyframe_lane(self, layer: LayerData, name: Optional[str] = None) -> KeyframeLane:
        extra = getattr(layer, "extra_keyframe_lanes", None)
        if extra is None:
            layer.extra_keyframe_lanes = []
            extra = layer.extra_keyframe_lanes
        lane_number = len(extra) + 1
        label = (name or "").strip() or f"Lane {lane_number}"
        lane = KeyframeLane(name=label, keyframes=[])
        extra.append(lane)
        return lane

    def _add_global_keyframe_lane(self, animation: AnimationData, name: Optional[str] = None) -> KeyframeLane:
        lanes = getattr(animation, "global_keyframe_lanes", None)
        if lanes is None:
            animation.global_keyframe_lanes = []
            lanes = animation.global_keyframe_lanes
        lane_number = len(lanes) + 1
        label = (name or "").strip() or f"Global {lane_number}"
        lane = KeyframeLane(name=label, keyframes=[])
        lanes.append(lane)
        return lane

    def _ensure_layer_lane_index(self, layer: LayerData, lane_index: int, name: Optional[str] = None) -> KeyframeLane:
        if lane_index <= 0:
            raise ValueError("lane_index must be > 0 for extra lanes")
        extra = getattr(layer, "extra_keyframe_lanes", None)
        if extra is None:
            layer.extra_keyframe_lanes = []
            extra = layer.extra_keyframe_lanes
        while len(extra) < lane_index:
            next_name = name if len(extra) + 1 == lane_index else None
            self._add_layer_keyframe_lane(layer, next_name)
        return extra[lane_index - 1]

    def _ensure_global_lane_index(self, animation: AnimationData, lane_index: int, name: Optional[str] = None) -> KeyframeLane:
        lanes = getattr(animation, "global_keyframe_lanes", None)
        if lanes is None:
            animation.global_keyframe_lanes = []
            lanes = animation.global_keyframe_lanes
        while len(lanes) <= lane_index:
            next_name = name if len(lanes) == lane_index else None
            self._add_global_keyframe_lane(animation, next_name)
        return lanes[lane_index]

    @staticmethod
    def _is_base_lane(lane_key: TimelineLaneKey) -> bool:
        return lane_key.scope == "layer" and lane_key.lane_index == 0

    @staticmethod
    def _remap_lane_key_after_removal(
        lane_key: TimelineLaneKey,
        removed_key: TimelineLaneKey,
    ) -> Optional[TimelineLaneKey]:
        if lane_key.scope != removed_key.scope or lane_key.layer_id != removed_key.layer_id:
            return lane_key
        if lane_key.lane_index == removed_key.lane_index:
            return None
        if lane_key.lane_index > removed_key.lane_index:
            return TimelineLaneKey(lane_key.scope, lane_key.layer_id, lane_key.lane_index - 1)
        return lane_key

    def _remap_marker_refs_after_lane_removal(
        self,
        removed_key: TimelineLaneKey,
    ) -> Set[Tuple[TimelineLaneKey, float]]:
        updated: Set[Tuple[TimelineLaneKey, float]] = set()
        for lane_key, time_value in self._selected_marker_refs:
            new_key = self._remap_lane_key_after_removal(lane_key, removed_key)
            if new_key is None:
                continue
            updated.add((new_key, time_value))
        return updated

    def _remap_active_lane_after_removal(
        self,
        removed_key: TimelineLaneKey,
    ) -> Optional[TimelineLaneKey]:
        active_lane = self.timeline.get_active_lane() if hasattr(self, "timeline") else None
        if active_lane is None:
            return None
        return self._remap_lane_key_after_removal(active_lane, removed_key)

    def _find_keyframes_at_time(
        self,
        layer: LayerData,
        time_value: float,
        tolerance: float = 1.0 / 600.0
    ) -> List[KeyframeData]:
        """Return all keyframes whose timestamp is within tolerance."""
        return [
            keyframe for keyframe in layer.keyframes
            if abs(keyframe.time - time_value) <= tolerance
        ]

    def _find_keyframes_at_time_in_lane(
        self,
        lane_key: TimelineLaneKey,
        time_value: float,
        tolerance: float = 1.0 / 600.0
    ) -> List[KeyframeData]:
        keyframes = self._get_lane_keyframes(lane_key) or []
        return [
            keyframe for keyframe in keyframes
            if abs(keyframe.time - time_value) <= tolerance
        ]

    def _create_keyframe_from_state(self, layer: LayerData, time_value: float) -> KeyframeData:
        """Create a new keyframe by sampling the player's evaluated layer state."""
        player = getattr(self.gl_widget, "player", None)
        state: Dict[str, Any] = {}
        if player:
            try:
                state = player.get_layer_state(layer, time_value, include_global=False) or {}
            except Exception:
                state = {}

        def _float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _clamp_channel(value: Any) -> int:
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                ivalue = 255
            return max(0, min(255, ivalue))

        keyframe = KeyframeData(time=time_value)
        keyframe.pos_x = _float(state.get("pos_x"), keyframe.pos_x)
        keyframe.pos_y = _float(state.get("pos_y"), keyframe.pos_y)
        keyframe.depth = _float(state.get("depth"), getattr(keyframe, "depth", 0.0))
        keyframe.scale_x = _float(state.get("scale_x"), keyframe.scale_x)
        keyframe.scale_y = _float(state.get("scale_y"), keyframe.scale_y)
        keyframe.rotation = _float(state.get("rotation"), keyframe.rotation)
        keyframe.opacity = _float(state.get("opacity"), keyframe.opacity)
        keyframe.r = _clamp_channel(state.get("r", keyframe.r))
        keyframe.g = _clamp_channel(state.get("g", keyframe.g))
        keyframe.b = _clamp_channel(state.get("b", keyframe.b))
        keyframe.a = _clamp_channel(state.get("a", keyframe.a))
        sprite_name = state.get("sprite_name")
        if sprite_name:
            keyframe.sprite_name = str(sprite_name)
            keyframe.immediate_sprite = 1
        else:
            keyframe.immediate_sprite = -1
        keyframe.immediate_pos = 0
        keyframe.immediate_scale = 0
        keyframe.immediate_rotation = 0
        keyframe.immediate_opacity = 0
        keyframe.immediate_rgb = 0
        return keyframe

    def _ensure_keyframe_at_time(
        self,
        layer: LayerData,
        time_value: float,
        tolerance: float = 1.0 / 600.0
    ) -> Tuple[KeyframeData, bool]:
        """Return an existing keyframe or create one if absent."""
        existing = self._find_keyframe_at_time(layer, time_value, tolerance=tolerance)
        if existing:
            return existing, False
        keyframe = self._create_keyframe_from_state(layer, time_value)
        layer.keyframes.append(keyframe)
        layer.keyframes.sort(key=lambda frame: frame.time)
        return keyframe, True

    def _ensure_keyframe_at_time_in_lane(
        self,
        lane_key: TimelineLaneKey,
        time_value: float,
        *,
        base_state: Optional[Dict[str, Any]] = None,
        tolerance: float = 1.0 / 600.0
    ) -> Tuple[KeyframeData, bool]:
        """Return an existing keyframe in a specific lane or create one if absent."""
        keyframes = self._get_lane_keyframes(lane_key)
        if keyframes is None:
            raise ValueError("Lane does not exist")
        existing = next((kf for kf in keyframes if abs(kf.time - time_value) <= tolerance), None)
        if existing:
            return existing, False
        if self._is_base_lane(lane_key):
            layer = self.gl_widget.get_layer_by_id(lane_key.layer_id)
            if not layer:
                raise ValueError("Layer not found")
            keyframe = self._create_keyframe_from_state(layer, time_value)
        else:
            keyframe = KeyframeData(time=time_value)
            keyframe.pos_x = 0.0
            keyframe.pos_y = 0.0
            keyframe.depth = 0.0
            keyframe.scale_x = 0.0
            keyframe.scale_y = 0.0
            keyframe.rotation = 0.0
            keyframe.opacity = 0.0
            keyframe.immediate_pos = 0
            keyframe.immediate_scale = 0
            keyframe.immediate_rotation = 0
            keyframe.immediate_opacity = 0
            keyframe.immediate_sprite = -1
            keyframe.immediate_rgb = -1
            if base_state:
                keyframe.r = int(base_state.get("r", keyframe.r))
                keyframe.g = int(base_state.get("g", keyframe.g))
                keyframe.b = int(base_state.get("b", keyframe.b))
                keyframe.a = int(base_state.get("a", keyframe.a))
        keyframes.append(keyframe)
        keyframes.sort(key=lambda frame: frame.time)
        return keyframe, True

    def _sync_layer_source_frames(self, layer: LayerData) -> None:
        """Mirror dataclass keyframes back to the source JSON structure."""
        source = self.layer_source_lookup.get(layer.layer_id)
        if source is None:
            return
        serialized = [self._serialize_keyframe(keyframe) for keyframe in layer.keyframes]
        source["frames"] = serialized
        if self.base_layer_cache:
            for cached in self.base_layer_cache:
                if cached.layer_id == layer.layer_id:
                    cached.keyframes = [replace(kf) for kf in layer.keyframes]
                    break

    def _serialize_keyframe(self, keyframe: KeyframeData) -> Dict[str, Any]:
        """Convert a KeyframeData instance back into the JSON frame schema."""
        def _int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        frame = {
            "time": float(keyframe.time),
            "pos": {
                "x": float(keyframe.pos_x),
                "y": float(keyframe.pos_y),
                "immediate": _int(keyframe.immediate_pos),
            },
            "scale": {
                "x": float(keyframe.scale_x),
                "y": float(keyframe.scale_y),
                "immediate": _int(keyframe.immediate_scale),
            },
            "rotation": {
                "value": float(keyframe.rotation),
                "immediate": _int(keyframe.immediate_rotation),
            },
            "opacity": {
                "value": float(keyframe.opacity),
                "immediate": _int(keyframe.immediate_opacity),
            },
            "sprite": {
                "string": keyframe.sprite_name or "",
                "immediate": _int(keyframe.immediate_sprite),
            },
            "rgb": {
                "red": int(keyframe.r),
                "green": int(keyframe.g),
                "blue": int(keyframe.b),
                "alpha": int(keyframe.a),
                "immediate": _int(keyframe.immediate_rgb, -1),
            },
        }
        if getattr(keyframe, "immediate_depth", -1) != -1:
            frame["depth"] = {
                "value": float(getattr(keyframe, "depth", 0.0)),
                "immediate": _int(getattr(keyframe, "immediate_depth", -1), -1),
            }
        return frame

    def _flatten_layer_keyframes(
        self,
        layer: LayerData,
        animation: AnimationData
    ) -> List[KeyframeData]:
        """Bake additive/global lanes into a flattened keyframe list."""
        times: Set[float] = set()
        for frame in layer.keyframes:
            times.add(max(0.0, float(frame.time)))
        for lane in getattr(layer, "extra_keyframe_lanes", []) or []:
            for frame in lane.keyframes:
                times.add(max(0.0, float(frame.time)))
        for lane in getattr(animation, "global_keyframe_lanes", []) or []:
            for frame in lane.keyframes:
                times.add(max(0.0, float(frame.time)))
        if not times:
            return []
        player = getattr(self.gl_widget, "player", None)
        baked: List[KeyframeData] = []
        for time_value in sorted(times):
            state = player.get_layer_state(layer, time_value) if player else {}
            keyframe = KeyframeData(time=time_value)
            keyframe.pos_x = float(state.get("pos_x", 0.0))
            keyframe.pos_y = float(state.get("pos_y", 0.0))
            keyframe.depth = float(state.get("depth", 0.0))
            keyframe.scale_x = float(state.get("scale_x", 100.0))
            keyframe.scale_y = float(state.get("scale_y", 100.0))
            keyframe.rotation = float(state.get("rotation", 0.0))
            keyframe.opacity = float(state.get("opacity", 100.0))
            keyframe.r = int(state.get("r", 255))
            keyframe.g = int(state.get("g", 255))
            keyframe.b = int(state.get("b", 255))
            keyframe.a = int(state.get("a", 255))
            sprite_name = state.get("sprite_name") or ""
            keyframe.sprite_name = str(sprite_name)
            keyframe.immediate_pos = 0
            keyframe.immediate_scale = 0
            keyframe.immediate_rotation = 0
            keyframe.immediate_opacity = 0
            keyframe.immediate_rgb = 0
            keyframe.immediate_sprite = 1 if sprite_name else -1
            keyframe.immediate_depth = 0 if getattr(layer, "has_depth", False) else -1
            baked.append(keyframe)
        return baked

    def _export_animation_dict(self, animation: AnimationData) -> Dict[str, Any]:
        """Serialize the active AnimationData and edited layer metadata."""
        exported = {
            "name": animation.name,
            "width": int(animation.width),
            "height": int(animation.height),
            "loop_offset": float(animation.loop_offset),
            "centered": int(animation.centered),
            "layers": []
        }
        skipped_layers: List[str] = []
        for layer in animation.layers:
            source = self.layer_source_lookup.get(layer.layer_id)
            if source is None:
                label = layer.name or f"Layer {layer.layer_id}"
                skipped_layers.append(label)
                continue
            layer_dict = copy.deepcopy(source) if source else {}
            layer_dict["name"] = layer.name
            layer_dict["id"] = layer.layer_id
            layer_dict["parent"] = layer.parent_id
            layer_dict["anchor_x"] = float(layer.anchor_x)
            layer_dict["anchor_y"] = float(layer.anchor_y)
            layer_dict["blend"] = int(layer.blend_mode)
            layer_dict["visible"] = bool(layer.visible)
            if layer.shader_name:
                layer_dict["shader"] = layer.shader_name
            else:
                layer_dict.pop("shader", None)

            def _assign_color(field_name: str, value: Optional[Tuple[float, float, float, float]]):
                if value is None:
                    layer_dict.pop(field_name, None)
                    return
                layer_dict[field_name] = [float(component) for component in value]

            _assign_color("color_tint", getattr(layer, "color_tint", None))
            _assign_color("color_tint_hdr", getattr(layer, "color_tint_hdr", None))

            gradient = getattr(layer, "color_gradient", None)
            if gradient:
                layer_dict["color_gradient"] = copy.deepcopy(gradient)
            else:
                layer_dict.pop("color_gradient", None)

            animator = getattr(layer, "color_animator", None)
            if animator:
                layer_dict["color_animator"] = copy.deepcopy(animator)
            else:
                layer_dict.pop("color_animator", None)

            metadata = getattr(layer, "color_metadata", None)
            if metadata:
                layer_dict["color_metadata"] = copy.deepcopy(metadata)
            else:
                layer_dict.pop("color_metadata", None)

            render_tags = getattr(layer, "render_tags", set())
            if render_tags:
                layer_dict["render_tags"] = sorted(tag for tag in render_tags if isinstance(tag, str))
            else:
                layer_dict.pop("render_tags", None)

            mask_role = getattr(layer, "mask_role", None)
            mask_key = getattr(layer, "mask_key", None)
            if mask_role:
                layer_dict["mask_role"] = mask_role
            else:
                layer_dict.pop("mask_role", None)
            if mask_key:
                layer_dict["mask_key"] = mask_key
            else:
                layer_dict.pop("mask_key", None)

            keyframes_to_export = layer.keyframes
            if (
                getattr(layer, "extra_keyframe_lanes", None)
                or getattr(animation, "global_keyframe_lanes", None)
            ):
                baked = self._flatten_layer_keyframes(layer, animation)
                if baked:
                    keyframes_to_export = baked
            layer_dict["frames"] = [self._serialize_keyframe(keyframe) for keyframe in keyframes_to_export]
            exported["layers"].append(layer_dict)
        if skipped_layers and self.log_widget:
            preview = ", ".join(skipped_layers[:3])
            if len(skipped_layers) > 3:
                preview += ", ..."
            self.log_widget.log(
                f"Skipped {len(skipped_layers)} transient layer(s) without JSON metadata during export: {preview}",
                "DEBUG",
            )
        return exported

    def _ensure_payload_defaults(self, payload: Dict[str, Any]) -> None:
        """Guarantee payload has required top-level fields."""
        blend = payload.get("blend_version")
        if not isinstance(blend, int) or blend <= 0:
            payload["blend_version"] = self.current_blend_version or 1
        rev_value = payload.get("rev")
        if not isinstance(rev_value, int) or rev_value <= 0:
            payload["rev"] = 6
        if not isinstance(payload.get("sources"), list):
            payload["sources"] = []
        if not isinstance(payload.get("anims"), list):
            payload["anims"] = []

    def _persist_current_animation_edits(self) -> None:
        """Sync the in-memory animation edits into the JSON payload."""
        if (
            not self.current_json_data
            or 'anims' not in self.current_json_data
            or not isinstance(self.current_json_data['anims'], list)
        ):
            return
        player = getattr(self.gl_widget, "player", None)
        animation = player.animation if player else None
        if not animation:
            return
        idx = self.current_animation_index
        anims = self.current_json_data['anims']
        if 0 <= idx < len(anims):
            anims[idx] = self._export_animation_dict(animation)

    def _keyframe_layers_sidecar_path(self, json_path: Optional[str]) -> Optional[str]:
        if not json_path:
            return None
        try:
            path = Path(json_path)
        except Exception:
            return None
        return str(path.with_name(f"{path.stem}.layers.json"))

    def _parse_keyframes_from_frames(self, frames: List[Dict[str, Any]]) -> List[KeyframeData]:
        keyframes: List[KeyframeData] = []
        if not frames:
            return keyframes
        indexed = enumerate(frames)
        sorted_frames = [
            frame for _, frame in sorted(
                indexed,
                key=lambda pair: (pair[1].get('time', 0.0), pair[0])
            )
        ]
        for frame_data in sorted_frames:
            sprite_info = frame_data.get('sprite', {}) or {}
            sprite_name = sprite_info.get('string', '')
            sprite_immediate = sprite_info.get('immediate', 0)
            depth_value = 0.0
            depth_immediate = -1
            depth_entry = frame_data.get('depth')
            if isinstance(depth_entry, dict):
                if 'value' in depth_entry:
                    depth_value = depth_entry.get('value', 0.0)
                    depth_immediate = depth_entry.get('immediate', 0)
            elif isinstance(depth_entry, (int, float)):
                depth_value = depth_entry
                depth_immediate = 0
            keyframes.append(
                KeyframeData(
                    time=frame_data.get('time', 0.0),
                    pos_x=frame_data.get('pos', {}).get('x', 0),
                    pos_y=frame_data.get('pos', {}).get('y', 0),
                    depth=depth_value,
                    scale_x=frame_data.get('scale', {}).get('x', 100),
                    scale_y=frame_data.get('scale', {}).get('y', 100),
                    rotation=frame_data.get('rotation', {}).get('value', 0),
                    opacity=frame_data.get('opacity', {}).get('value', 100),
                    sprite_name=sprite_name,
                    r=frame_data.get('rgb', {}).get('red', 255),
                    g=frame_data.get('rgb', {}).get('green', 255),
                    b=frame_data.get('rgb', {}).get('blue', 255),
                    a=frame_data.get('rgb', {}).get('alpha', 255),
                    immediate_pos=frame_data.get('pos', {}).get('immediate', 0),
                    immediate_depth=depth_immediate,
                    immediate_scale=frame_data.get('scale', {}).get('immediate', 0),
                    immediate_rotation=frame_data.get('rotation', {}).get('immediate', 0),
                    immediate_opacity=frame_data.get('opacity', {}).get('immediate', 0),
                    immediate_sprite=sprite_immediate,
                    immediate_rgb=frame_data.get('rgb', {}).get('immediate', -1)
                )
            )
        return keyframes

    def _load_keyframe_layers_sidecar(self, json_path: Optional[str], animation: AnimationData) -> None:
        sidecar_path = self._keyframe_layers_sidecar_path(json_path)
        if not sidecar_path or not os.path.exists(sidecar_path):
            return
        try:
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        anims = payload.get("anims")
        if not isinstance(anims, dict):
            return
        anim_key = self.current_animation_name or str(self.current_animation_index)
        anim_blob = anims.get(anim_key)
        if not isinstance(anim_blob, dict):
            return

        layer_blob = anim_blob.get("layers") if isinstance(anim_blob, dict) else None
        if isinstance(layer_blob, dict):
            for layer in animation.layers:
                lane_defs = layer_blob.get(str(layer.layer_id)) or layer_blob.get(layer.layer_id)
                if not isinstance(lane_defs, list):
                    continue
                layer.extra_keyframe_lanes = []
                for idx, lane_entry in enumerate(lane_defs):
                    if not isinstance(lane_entry, dict):
                        continue
                    name = lane_entry.get("name") or f"Lane {idx + 1}"
                    frames = lane_entry.get("frames") or lane_entry.get("keyframes") or []
                    keyframes = self._parse_keyframes_from_frames(frames)
                    layer.extra_keyframe_lanes.append(KeyframeLane(name=str(name), keyframes=keyframes))

        global_defs = anim_blob.get("global") if isinstance(anim_blob, dict) else None
        if isinstance(global_defs, list):
            animation.global_keyframe_lanes = []
            for idx, lane_entry in enumerate(global_defs):
                if not isinstance(lane_entry, dict):
                    continue
                name = lane_entry.get("name") or f"Global {idx + 1}"
                frames = lane_entry.get("frames") or lane_entry.get("keyframes") or []
                keyframes = self._parse_keyframes_from_frames(frames)
                animation.global_keyframe_lanes.append(KeyframeLane(name=str(name), keyframes=keyframes))

    def _save_keyframe_layers_sidecar(self, json_path: Optional[str], animation: AnimationData) -> None:
        sidecar_path = self._keyframe_layers_sidecar_path(json_path)
        if not sidecar_path:
            return
        payload: Dict[str, Any] = {"version": 1, "anims": {}}
        if os.path.exists(sidecar_path):
            try:
                with open(sidecar_path, "r", encoding="utf-8") as handle:
                    existing = json.load(handle)
                if isinstance(existing, dict):
                    payload.update(existing)
                    if "anims" not in payload or not isinstance(payload["anims"], dict):
                        payload["anims"] = {}
            except Exception:
                payload = {"version": 1, "anims": {}}
        anim_key = self.current_animation_name or str(self.current_animation_index)
        anim_blob: Dict[str, Any] = {"layers": {}, "global": []}
        for layer in animation.layers:
            extra_lanes = getattr(layer, "extra_keyframe_lanes", []) or []
            if not extra_lanes:
                continue
            lane_entries: List[Dict[str, Any]] = []
            for lane in extra_lanes:
                lane_entries.append(
                    {
                        "name": lane.name,
                        "frames": [self._serialize_keyframe(frame) for frame in lane.keyframes],
                    }
                )
            anim_blob["layers"][str(layer.layer_id)] = lane_entries
        global_lanes = getattr(animation, "global_keyframe_lanes", []) or []
        for lane in global_lanes:
            anim_blob["global"].append(
                {
                    "name": lane.name,
                    "frames": [self._serialize_keyframe(frame) for frame in lane.keyframes],
                }
            )
        payload["anims"][anim_key] = anim_blob
        try:
            with open(sidecar_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception as exc:
            self.log_widget.log(f"Failed to save keyframe lane data: {exc}", "WARNING")

    def _inject_animation_into_payload(self, payload: Dict[str, Any], animation: AnimationData) -> None:
        """Merge exported animation data into an existing payload."""
        self._ensure_payload_defaults(payload)
        exported = self._export_animation_dict(animation)
        anims = payload.get("anims") or []
        idx = self.current_animation_index
        if 0 <= idx < len(anims) and isinstance(anims[idx], dict):
            anims[idx] = exported
            payload["anims"] = anims
            return
        if animation.name:
            target_name = animation.name.lower()
            for pos, entry in enumerate(anims):
                if isinstance(entry, dict) and (entry.get("name") or "").lower() == target_name:
                    anims[pos] = exported
                    payload["anims"] = anims
                    return
        anims.append(exported)
        payload["anims"] = anims
    
    def on_scale_changed(self, value: float):
        """Handle render scale change"""
        self.gl_widget.render_scale = value
        self.gl_widget.update()
    
    def on_fps_changed(self, value: int):
        """Handle FPS change"""
        interval = int(1000 / value)
        self.gl_widget.timer.setInterval(interval)
    
    def on_position_scale_changed(self, value: float):
        """Handle position scale spinbox change"""
        self.gl_widget.position_scale = value
        # Update slider without triggering its signal
        self.control_panel.pos_scale_slider.blockSignals(True)
        self.control_panel.pos_scale_slider.setValue(int(value * 100))
        self.control_panel.pos_scale_slider.blockSignals(False)
        self.gl_widget.update()
    
    def on_position_scale_slider_changed(self, value: int):
        """Handle position scale slider change"""
        scale_value = value / 100.0
        # Update spinbox without triggering its signal
        self.control_panel.pos_scale_spin.blockSignals(True)
        self.control_panel.pos_scale_spin.setValue(scale_value)
        self.control_panel.pos_scale_spin.blockSignals(False)
        self.gl_widget.position_scale = scale_value
        self.gl_widget.update()
    
    def on_base_world_scale_changed(self, value: float):
        """Handle base world scale spinbox change"""
        self.gl_widget.renderer.base_world_scale = value
        # Update slider without triggering its signal
        self.control_panel.base_scale_slider.blockSignals(True)
        self.control_panel.base_scale_slider.setValue(int(value * 100))
        self.control_panel.base_scale_slider.blockSignals(False)
        self.gl_widget.update()
    
    def on_base_world_scale_slider_changed(self, value: int):
        """Handle base world scale slider change"""
        scale_value = value / 100.0
        # Update spinbox without triggering its signal
        self.control_panel.base_scale_spin.blockSignals(True)
        self.control_panel.base_scale_spin.setValue(scale_value)
        self.control_panel.base_scale_spin.blockSignals(False)
        self.gl_widget.renderer.base_world_scale = scale_value
        self.gl_widget.update()
    
    def on_translation_sensitivity_changed(self, value: float):
        """Adjust sprite drag translation speed multiplier."""
        self.gl_widget.drag_translation_multiplier = max(0.01, value)
    
    def on_rotation_sensitivity_changed(self, value: float):
        """Adjust sprite rotation sensitivity multiplier."""
        self.gl_widget.drag_rotation_multiplier = max(0.1, value)
    
    def on_rotation_overlay_size_changed(self, value: float):
        """Adjust the visual radius of the rotation gizmo."""
        self.gl_widget.rotation_overlay_radius = max(5.0, value)
        self.gl_widget.update()
    
    def toggle_rotation_gizmo(self, enabled: bool):
        """Toggle visibility of the rotation gizmo overlay."""
        self.gl_widget.rotation_gizmo_enabled = enabled
        if enabled and not self.selected_layer_ids and self.gl_widget.player.animation:
            first_layer = self.gl_widget.player.animation.layers[0]
            self.selected_layer_ids = {first_layer.layer_id}
            self.primary_selected_layer_id = first_layer.layer_id
            self.selection_lock_enabled = False
            self.layer_panel.set_selection_state(self.selected_layer_ids)
            self.apply_selection_state()
        self.gl_widget.update()

    def on_audio_enabled_changed(self, enabled: bool):
        """Enable or mute audio playback."""
        self.audio_manager.set_enabled(enabled)
        if enabled and self.gl_widget.player.playing and self.audio_manager.is_ready:
            self.audio_manager.play(self._get_audio_sync_time(self.gl_widget.player.current_time))
        elif not enabled:
            self.audio_manager.pause()
        state = "enabled" if enabled else "muted"
        self.log_widget.log(f"Audio {state}", "INFO")

    def on_audio_volume_changed(self, value: int):
        """Adjust playback volume."""
        self.audio_manager.set_volume(value)

    def on_audio_track_mute_changed(self, track_id: str, muted: bool):
        """Toggle mute state for a special multi-track audio source and reload audio."""
        scope = self._current_audio_track_scope
        if not scope or not track_id:
            return
        scope_key = self._normalize_audio_scope(scope)
        track_key = self._normalize_audio_track_id(track_id)
        if not scope_key or not track_key:
            return

        muted_set = set(self._audio_track_mutes_by_scope.get(scope_key, set()))
        changed = False
        if muted:
            if track_key not in muted_set:
                muted_set.add(track_key)
                changed = True
        else:
            if track_key in muted_set:
                muted_set.remove(track_key)
                changed = True

        if not changed:
            return

        if muted_set:
            self._audio_track_mutes_by_scope[scope_key] = muted_set
        else:
            self._audio_track_mutes_by_scope.pop(scope_key, None)

        if self.current_animation_name:
            self.load_audio_for_animation(self.current_animation_name)

    def toggle_antialiasing(self, enabled: bool):
        """Enable or disable multi-sample anti-aliasing in the OpenGL view."""
        self.gl_widget.set_antialiasing_enabled(enabled)
        self.gl_widget.update()

    def toggle_scale_gizmo(self, enabled: bool):
        """Toggle the scaling gizmo overlay."""
        self.gl_widget.set_scale_gizmo_enabled(enabled)
        self.gl_widget.update()

    def on_constraints_enabled_changed(self, enabled: bool):
        """Toggle the constraint system globally."""
        self.constraints_enabled = bool(enabled)
        self.constraint_manager.enabled = self.constraints_enabled
        self.settings.setValue('constraints/enabled', self.constraints_enabled)
        self.gl_widget.update()

    def on_constraint_item_toggled(self, cid: str, enabled: bool):
        spec = self._find_constraint_by_id(cid)
        if not spec:
            return
        spec.enabled = bool(enabled)
        self._save_constraints_to_settings()
        self.constraint_manager.set_constraints(self.constraints)
        self.gl_widget.update()

    def on_constraint_add_requested(self):
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before adding constraints.")
            return
        layer_entries = [(layer.name, layer.layer_id) for layer in self.gl_widget.player.animation.layers]
        anchor_positions = self._get_layer_anchor_positions()
        dialog = ConstraintEditorDialog(layer_entries, anchor_positions, parent=self)
        selected = self.primary_selected_layer_id or self.gl_widget.selected_layer_id
        if selected is not None:
            idx = dialog.layer_combo.findData(selected)
            if idx >= 0:
                dialog.layer_combo.setCurrentIndex(idx)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        spec = dialog.build_constraint()
        if not spec:
            return
        self.constraints.append(spec)
        self.constraint_manager.set_constraints(self.constraints)
        self._save_constraints_to_settings()
        self._refresh_constraints_ui()
        self.gl_widget.update()

    def on_constraint_edit_requested(self, cid: str):
        spec = self._find_constraint_by_id(cid)
        if not spec:
            return
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before editing constraints.")
            return
        layer_entries = [(layer.name, layer.layer_id) for layer in self.gl_widget.player.animation.layers]
        anchor_positions = self._get_layer_anchor_positions()
        dialog = ConstraintEditorDialog(layer_entries, anchor_positions, constraint=spec, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.build_constraint()
        if not updated:
            return
        updated.enabled = spec.enabled
        for idx, existing in enumerate(self.constraints):
            if existing.cid == cid:
                self.constraints[idx] = updated
                break
        self.constraint_manager.set_constraints(self.constraints)
        self._save_constraints_to_settings()
        self._refresh_constraints_ui()
        self.gl_widget.update()

    def on_constraint_remove_requested(self, cid: str):
        before = len(self.constraints)
        self.constraints = [spec for spec in self.constraints if spec.cid != cid]
        if len(self.constraints) == before:
            return
        self.constraint_manager.set_constraints(self.constraints)
        self._save_constraints_to_settings()
        self._refresh_constraints_ui()
        self.gl_widget.update()

    def on_layer_constraints_toggled(self, layer_id: int, enabled: bool):
        layer = self.gl_widget.get_layer_by_id(layer_id)
        if not layer or not layer.name:
            return
        key = layer.name.lower()
        if enabled:
            self.constraint_manager.disabled_layer_names.discard(key)
        else:
            self.constraint_manager.disabled_layer_names.add(key)
        self._save_constraint_layer_disables()
        self.gl_widget.update()

    def _find_constraint_by_id(self, cid: str) -> Optional[ConstraintSpec]:
        for spec in self.constraints:
            if spec.cid == cid:
                return spec
        return None

    def on_joint_solver_enabled_changed(self, enabled: bool) -> None:
        self.joint_solver_enabled = bool(enabled)
        self.settings.setValue('joint_solver/enabled', self.joint_solver_enabled)
        self.gl_widget.set_joint_solver_enabled(self.joint_solver_enabled)
        self.gl_widget.update()

    def on_joint_solver_iterations_changed(self, value: int) -> None:
        self.joint_solver_iterations = max(1, int(value))
        self.settings.setValue('joint_solver/iterations', self.joint_solver_iterations)
        self.gl_widget.set_joint_solver_iterations(self.joint_solver_iterations)

    def on_joint_solver_strength_changed(self, value: float) -> None:
        self.joint_solver_strength = max(0.0, min(1.0, float(value)))
        self.settings.setValue('joint_solver/strength', self.joint_solver_strength)
        self.gl_widget.set_joint_solver_strength(self.joint_solver_strength)

    def on_joint_solver_parented_changed(self, enabled: bool) -> None:
        self.joint_solver_parented = bool(enabled)
        self.settings.setValue('joint_solver/parented', self.joint_solver_parented)
        self.gl_widget.set_joint_solver_parented(self.joint_solver_parented)
        self.gl_widget.update()

    def on_propagate_user_transforms_changed(self, enabled: bool) -> None:
        self.propagate_user_transforms = bool(enabled)
        self.settings.setValue('pose/propagate_user_transforms', self.propagate_user_transforms)
        self.gl_widget.set_propagate_user_transforms(self.propagate_user_transforms)
        self.gl_widget.update()

    def on_preserve_children_on_record_changed(self, enabled: bool) -> None:
        self.preserve_children_on_record = bool(enabled)
        self.settings.setValue('pose/preserve_children_on_record', self.preserve_children_on_record)

    def on_joint_solver_capture_requested(self) -> None:
        self.gl_widget.capture_joint_rest_lengths()
        self.log_widget.log("Captured joint rest pose from current frame.", "INFO")

    def on_joint_solver_bake_current_requested(self) -> None:
        if not self.gl_widget.player.animation:
            self.log_widget.log("Load an animation before baking joints.", "WARNING")
            return
        layer_ids = self._resolve_joint_bake_layer_ids()
        if not layer_ids:
            self.log_widget.log("No layers available for joint bake.", "WARNING")
            return
        if self.joint_solver_enabled:
            self.gl_widget._apply_joint_solver_to_offsets()
        applied = self._bake_pose_for_layers(layer_ids, self.gl_widget.player.current_time, label="joint_bake")
        if applied > 0:
            self.log_widget.log(f"Joint bake applied to {applied} layer(s).", "SUCCESS")
        else:
            self.log_widget.log("No joint offsets detected; nothing to bake.", "INFO")

    def on_joint_solver_bake_range_requested(self) -> None:
        if not self.gl_widget.player.animation:
            self.log_widget.log("Load an animation before baking joints.", "WARNING")
            return
        duration = float(self.gl_widget.player.duration or 0.0)
        if duration <= 0.0:
            self.log_widget.log("Animation duration is zero; cannot bake a range.", "WARNING")
            return
        start_default = float(self.gl_widget.player.current_time)
        start_time, ok = QInputDialog.getDouble(
            self,
            "Bake Range",
            "Start time (seconds):",
            start_default,
            0.0,
            max(duration, 0.001),
            3,
        )
        if not ok:
            return
        end_default = min(duration, start_time + 1.0)
        end_time, ok = QInputDialog.getDouble(
            self,
            "Bake Range",
            "End time (seconds):",
            end_default,
            0.0,
            max(duration, 0.001),
            3,
        )
        if not ok:
            return
        if end_time < start_time:
            start_time, end_time = end_time, start_time
        fps = max(1, int(self.control_panel.fps_spin.value()))
        layer_ids = self._resolve_joint_bake_layer_ids()
        if not layer_ids:
            self.log_widget.log("No layers available for joint bake.", "WARNING")
            return
        if self.joint_solver_enabled:
            self.gl_widget._apply_joint_solver_to_offsets()
        applied = self._bake_pose_over_range(layer_ids, start_time, end_time, fps, label="joint_bake_range")
        if applied > 0:
            self.log_widget.log(
                f"Joint bake applied to {applied} keyframe(s) from {start_time:.3f}s to {end_time:.3f}s.",
                "SUCCESS",
            )
        else:
            self.log_widget.log("No joint offsets detected; nothing to bake.", "INFO")

    def _resolve_joint_bake_layer_ids(self) -> Set[int]:
        if self.selected_layer_ids:
            return set(self.selected_layer_ids)
        animation = self.gl_widget.player.animation
        if not animation:
            return set()
        return {layer.layer_id for layer in animation.layers}

    def _bake_pose_for_layers(
        self,
        layer_ids: Set[int],
        time_value: float,
        *,
        label: str = "pose_bake",
        clear_offsets: bool = True,
    ) -> int:
        if not layer_ids:
            return 0
        base_states_map, final_states_map = self._gather_pose_state_maps(layer_ids)
        desired_states_map = self._apply_offsets_to_state_map(final_states_map)
        influence = "current"
        applied = 0
        use_compensation = not self.preserve_children_on_record
        compensation_ids: Set[int] = set()
        if use_compensation:
            compensation_ids = self._collect_compensation_ids(layer_ids, layer_ids)
        self._begin_keyframe_action(list(layer_ids | compensation_ids))
        changed_parents: Set[int] = set()
        for layer_id in layer_ids:
            if self._record_pose_for_layer(
                layer_id,
                round(float(time_value), 5),
                influence,
                base_states_map,
                final_states_map,
                desired_states_map,
                force=True,
            ):
                applied += 1
                changed_parents.add(layer_id)
        compensated_ids: Set[int] = set()
        if use_compensation and changed_parents:
            child_influence = influence
            desired_states_for_comp = desired_states_map
            if compensation_ids:
                desired_states_for_comp = dict(desired_states_map)
                for child_id in compensation_ids:
                    base_state = base_states_map.get(child_id)
                    if base_state:
                        desired_states_for_comp[child_id] = base_state
            post_states_map, _ = self._gather_pose_state_maps(layer_ids | compensation_ids)
            compensated_ids = self._apply_record_compensation(
                changed_parents,
                round(float(time_value), 5),
                child_influence,
                post_states_map,
                desired_states_for_comp,
                layer_ids,
            )
        self._finalize_keyframe_action(label)
        if applied and clear_offsets:
            self._clear_user_offsets_for_layers(layer_ids | compensated_ids)
            if (
                not use_compensation
                and getattr(self.gl_widget, "joint_solver_enabled", False)
            ):
                self.gl_widget.refresh_joint_solver_after_pose_record(layer_ids)
            self.update_offset_display()
        if applied:
            self.gl_widget.player.calculate_duration()
            self.update_timeline()
            self.gl_widget.update()
        return applied

    def _bake_pose_over_range(
        self,
        layer_ids: Set[int],
        start_time: float,
        end_time: float,
        fps: int,
        *,
        label: str = "pose_bake_range",
    ) -> int:
        if not layer_ids:
            return 0
        fps = max(1, int(fps))
        step = 1.0 / fps
        start_time = max(0.0, float(start_time))
        end_time = max(start_time, float(end_time))
        total_steps = int(round((end_time - start_time) / step)) + 1
        if total_steps <= 0:
            return 0

        original_time = float(self.gl_widget.player.current_time)
        applied = 0
        influence = "current"
        use_compensation = not self.preserve_children_on_record
        compensation_ids: Set[int] = set()
        if use_compensation:
            compensation_ids = self._collect_compensation_ids(layer_ids, layer_ids)
        self._begin_keyframe_action(list(layer_ids | compensation_ids))
        compensated_ids: Set[int] = set()
        try:
            for idx in range(total_steps):
                t = start_time + idx * step
                if t > end_time + 1e-6:
                    break
                self.gl_widget.player.current_time = t
                base_states_map, final_states_map = self._gather_pose_state_maps(layer_ids)
                desired_states_map = self._apply_offsets_to_state_map(final_states_map)
                changed_parents: Set[int] = set()
                for layer_id in layer_ids:
                    if self._record_pose_for_layer(
                        layer_id,
                        round(float(t), 5),
                        influence,
                        base_states_map,
                        final_states_map,
                        desired_states_map,
                        force=True,
                    ):
                        applied += 1
                        changed_parents.add(layer_id)
                if use_compensation and changed_parents:
                    child_influence = influence
                    desired_states_for_comp = desired_states_map
                    if compensation_ids:
                        desired_states_for_comp = dict(desired_states_map)
                        for child_id in compensation_ids:
                            base_state = base_states_map.get(child_id)
                            if base_state:
                                desired_states_for_comp[child_id] = base_state
                    post_states_map, _ = self._gather_pose_state_maps(layer_ids | compensation_ids)
                    compensated_ids |= self._apply_record_compensation(
                        changed_parents,
                        round(float(t), 5),
                        child_influence,
                        post_states_map,
                        desired_states_for_comp,
                        layer_ids,
                    )
        finally:
            self.gl_widget.player.current_time = original_time
        self._finalize_keyframe_action(label)
        if applied:
            self._clear_user_offsets_for_layers(layer_ids | compensated_ids)
            if (
                not use_compensation
                and getattr(self.gl_widget, "joint_solver_enabled", False)
            ):
                self.gl_widget.refresh_joint_solver_after_pose_record(layer_ids)
            self.update_offset_display()
            self.gl_widget.player.calculate_duration()
            self.update_timeline()
            self.gl_widget.update()
        return applied

    def _get_layer_anchor_positions(self) -> Dict[int, Tuple[float, float]]:
        positions: Dict[int, Tuple[float, float]] = {}
        animation = self.gl_widget.player.animation
        if not animation:
            return positions
        states = self.gl_widget._build_layer_world_states(apply_constraints=False)
        for layer in animation.layers:
            state = states.get(layer.layer_id)
            if not state:
                continue
            ax = state.get("anchor_world_x", state.get("tx", 0.0))
            ay = state.get("anchor_world_y", state.get("ty", 0.0))
            positions[layer.layer_id] = (float(ax), float(ay))
        return positions

    def on_scale_mode_changed(self, mode: str):
        """Change scale gizmo mode (uniform/per-axis)."""
        self.gl_widget.set_scale_gizmo_mode(mode)

    def _sync_audio_playback(self, playing: bool):
        """Start or pause the audio player to match animation playback."""
        if not self.audio_manager.is_ready:
            return
        if playing:
            self.audio_manager.play(self._get_audio_sync_time(self.gl_widget.player.current_time))
        else:
            self.audio_manager.pause()
    
    def on_anchor_bias_x_changed(self, value: float):
        self.gl_widget.renderer.anchor_bias_x = value
        self.gl_widget.update()
    
    def on_anchor_bias_y_changed(self, value: float):
        self.gl_widget.renderer.anchor_bias_y = value
        self.gl_widget.update()

    def on_anchor_flip_x_changed(self, enabled: bool):
        self.gl_widget.renderer.anchor_flip_x = bool(enabled)
        self.gl_widget.update()

    def on_anchor_flip_y_changed(self, enabled: bool):
        self.gl_widget.renderer.anchor_flip_y = bool(enabled)
        self.gl_widget.update()

    def on_anchor_scale_x_changed(self, value: float):
        self.gl_widget.renderer.anchor_scale_x = max(0.0, float(value))
        self.gl_widget.update()

    def on_anchor_scale_y_changed(self, value: float):
        self.gl_widget.renderer.anchor_scale_y = max(0.0, float(value))
        self.gl_widget.update()
    
    def on_local_position_multiplier_changed(self, value: float):
        self.gl_widget.renderer.local_position_multiplier = max(0.0, value)
        self.gl_widget.update()
    
    def on_parent_mix_changed(self, value: float):
        self.gl_widget.renderer.parent_mix = max(0.0, min(1.0, value))
        self.gl_widget.update()
    
    def on_rotation_bias_changed(self, value: float):
        self.gl_widget.renderer.rotation_bias = value
        self.gl_widget.update()
    
    def on_scale_bias_x_changed(self, value: float):
        self.gl_widget.renderer.scale_bias_x = max(0.0, value)
        self.gl_widget.update()
    
    def on_scale_bias_y_changed(self, value: float):
        self.gl_widget.renderer.scale_bias_y = max(0.0, value)
        self.gl_widget.update()
    
    def on_world_offset_x_changed(self, value: float):
        self.gl_widget.renderer.world_offset_x = value
        self.gl_widget.update()
    
    def on_world_offset_y_changed(self, value: float):
        self.gl_widget.renderer.world_offset_y = value
        self.gl_widget.update()

    def on_particle_origin_offset_x_changed(self, value: float):
        self.gl_widget.particle_origin_offset_x = float(value)
        self.gl_widget.update()

    def on_particle_origin_offset_y_changed(self, value: float):
        self.gl_widget.particle_origin_offset_y = float(value)
        self.gl_widget.update()
    
    def on_trim_shift_multiplier_changed(self, value: float):
        self.gl_widget.renderer.trim_shift_multiplier = max(0.0, value)
        self.gl_widget.update()
    
    def reset_camera(self):
        """Reset camera to default position"""
        self.gl_widget.reset_camera()
        self.control_panel.scale_spin.setValue(1.0)
    
    def fit_to_view(self):
        """Fit the animation to the viewport"""
        if self.gl_widget.fit_to_view():
            # Update the scale spinbox to reflect the new scale
            self.control_panel.scale_spin.blockSignals(True)
            self.control_panel.scale_spin.setValue(self.gl_widget.render_scale)
            self.control_panel.scale_spin.blockSignals(False)
            self.log_widget.log("Fitted animation to view", "SUCCESS")
        else:
            self.log_widget.log("No animation to fit", "WARNING")
    
    def toggle_bone_overlay(self, enabled: bool):
        """Toggle the bone/skeleton overlay"""
        self.gl_widget.show_bones = enabled
        self.gl_widget.update()
    
    def toggle_anchor_overlay(self, enabled: bool):
        """Toggle the anchor overlay/editor."""
        self.gl_widget.set_anchor_overlay_enabled(enabled)

    def toggle_parent_overlay(self, enabled: bool):
        """Toggle the parent overlay/editor."""
        self.gl_widget.set_parent_overlay_enabled(enabled)

    def on_anchor_drag_precision_changed(self, value: float):
        """Update how sensitive anchor dragging is."""
        self.gl_widget.set_anchor_drag_precision(value)

    def on_bpm_value_changed(self, value: float):
        """Handle BPM slider/spin edits."""
        self._set_current_bpm(value, update_ui=False, store_override=True)
        self._update_audio_speed()

    def on_sync_audio_to_bpm_toggled(self, enabled: bool):
        """Toggle whether audio speed follows BPM."""
        self.sync_audio_to_bpm = enabled
        self.settings.setValue('audio/sync_to_bpm', enabled)
        self._update_audio_speed()

    def on_pitch_shift_toggled(self, enabled: bool):
        """Toggle pitch shifting for audio playback."""
        self.pitch_shift_enabled = enabled
        self.settings.setValue('audio/pitch_shift_enabled', enabled)
        self._update_audio_speed()

    def on_metronome_toggled(self, enabled: bool):
        """Enable or disable the BPM metronome."""
        self.metronome_enabled = bool(enabled)
        self.settings.setValue('metronome/enabled', self.metronome_enabled)
        self._update_metronome_state()
        if hasattr(self, "control_panel"):
            self.control_panel.set_metronome_checkbox(self.metronome_enabled)

    def on_metronome_audible_toggled(self, audible: bool):
        """Toggle whether the metronome emits an audible click."""
        self.metronome_audible = bool(audible)
        self.settings.setValue('metronome/audible', self.metronome_audible)
        self._update_metronome_state()
        if hasattr(self, "control_panel"):
            self.control_panel.set_metronome_audible_checkbox(self.metronome_audible)

    def on_time_signature_changed(self, numerator: int, denominator: int):
        """Apply a new time signature for the metronome/beat grid."""
        numerator, denominator = self._sanitize_time_signature(numerator, denominator)
        if (
            numerator == self.time_signature_num
            and denominator == self.time_signature_denom
        ):
            return
        self.time_signature_num = numerator
        self.time_signature_denom = denominator
        self.settings.setValue('metronome/time_signature_numerator', numerator)
        self.settings.setValue('metronome/time_signature_denom', denominator)
        if hasattr(self, "metronome"):
            self.metronome.set_time_signature(numerator, denominator)
        self._refresh_timeline_beats(force_regenerate=True)

    def _on_metronome_tick(self, downbeat: bool):
        if hasattr(self, "control_panel"):
            self.control_panel.pulse_metronome_indicator(downbeat)

    def _update_metronome_state(self):
        """Ensure the metronome matches the current BPM and playback state."""
        if not hasattr(self, "metronome"):
            return
        player = getattr(self.gl_widget, "player", None)
        is_playing = bool(player and getattr(player, "playing", False))
        should_run = self.metronome_enabled and is_playing
        current_time = getattr(player, "current_time", 0.0) if player else 0.0
        bpm = self._tempo_bpm_at_time(current_time)
        self._active_metronome_bpm = bpm
        self.metronome.set_bpm(bpm)
        self.metronome.set_time_signature(self.time_signature_num, self.time_signature_denom)
        self.metronome.set_audible(self.metronome_audible)
        self.metronome.set_enabled(should_run)

    def on_solid_bg_enabled_changed(self, enabled: bool):
        """Handle background fill checkbox toggles."""
        self.solid_bg_enabled = bool(enabled)
        self.settings.setValue('export/solid_bg_enabled', self.solid_bg_enabled)

    def on_solid_bg_color_changed(self, r: int, g: int, b: int, a: int):
        """Update the stored export background color."""
        self._apply_solid_bg_color((r, g, b, a), announce=False)

    def on_viewport_bg_enabled_changed(self, enabled: bool):
        """Enable/disable viewport background rendering entirely."""
        self.viewport_bg_enabled = bool(enabled)
        self.settings.setValue(
            'viewport/background_enabled',
            self.viewport_bg_enabled,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_enabled(self.viewport_bg_enabled)

    def on_viewport_bg_keep_aspect_changed(self, enabled: bool):
        """Toggle viewport background image aspect-preserving fit."""
        self.viewport_bg_keep_aspect = bool(enabled)
        self.settings.setValue(
            'viewport/background_keep_aspect',
            self.viewport_bg_keep_aspect,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_keep_aspect(
                self.viewport_bg_keep_aspect
            )

    def on_viewport_bg_zoom_fill_changed(self, enabled: bool):
        """Toggle viewport background image cover mode while preserving aspect ratio."""
        self.viewport_bg_zoom_fill = bool(enabled)
        self.settings.setValue(
            'viewport/background_zoom_fill',
            self.viewport_bg_zoom_fill,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_zoom_fill(
                self.viewport_bg_zoom_fill
            )

    def on_viewport_bg_parallax_enabled_changed(self, enabled: bool):
        """Toggle viewport background parallax effect."""
        self.viewport_bg_parallax_enabled = bool(enabled)
        self.settings.setValue(
            'viewport/background_parallax_enabled',
            self.viewport_bg_parallax_enabled,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_parallax_enabled(
                self.viewport_bg_parallax_enabled
            )

    def on_viewport_bg_parallax_zoom_strength_changed(self, value: float):
        """Set viewport background parallax zoom sensitivity."""
        self.viewport_bg_parallax_zoom_strength = max(0.0, min(2.0, float(value)))
        self.settings.setValue(
            'viewport/background_parallax_zoom_strength',
            self.viewport_bg_parallax_zoom_strength,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_parallax_zoom_sensitivity(
                self.viewport_bg_parallax_zoom_strength
            )

    def on_viewport_bg_parallax_pan_strength_changed(self, value: float):
        """Set viewport background parallax pan sensitivity."""
        self.viewport_bg_parallax_pan_strength = max(0.0, min(2.0, float(value)))
        self.settings.setValue(
            'viewport/background_parallax_pan_strength',
            self.viewport_bg_parallax_pan_strength,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_parallax_pan_sensitivity(
                self.viewport_bg_parallax_pan_strength
            )

    def on_viewport_bg_flip_h_changed(self, enabled: bool):
        """Toggle horizontal flip for viewport background image."""
        self.viewport_bg_flip_h = bool(enabled)
        self.settings.setValue(
            'viewport/background_flip_h',
            self.viewport_bg_flip_h,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_flips(
                self.viewport_bg_flip_h,
                self.viewport_bg_flip_v,
            )

    def on_viewport_bg_flip_v_changed(self, enabled: bool):
        """Toggle vertical flip for viewport background image."""
        self.viewport_bg_flip_v = bool(enabled)
        self.settings.setValue(
            'viewport/background_flip_v',
            self.viewport_bg_flip_v,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_flips(
                self.viewport_bg_flip_h,
                self.viewport_bg_flip_v,
            )

    def on_viewport_bg_image_enabled_changed(self, enabled: bool):
        """Toggle viewport background image rendering."""
        self.viewport_bg_image_enabled = bool(enabled)
        self.settings.setValue(
            'viewport/background_image_enabled',
            self.viewport_bg_image_enabled,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_image_enabled(
                self.viewport_bg_image_enabled
            )

    def on_viewport_bg_color_mode_changed(self, mode: str):
        """Set how solid viewport color is applied to the background image/pattern."""
        self.viewport_bg_color_mode = self._normalize_viewport_bg_color_mode(mode)
        self.settings.setValue(
            'viewport/background_color_mode',
            self.viewport_bg_color_mode,
        )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_color_mode(
                self.viewport_bg_color_mode
            )

    def on_export_include_viewport_bg_changed(self, enabled: bool):
        """Toggle whether exports include the viewport background image/pattern."""
        self.export_include_viewport_background = bool(enabled)
        self.settings.setValue(
            'export/include_viewport_background',
            self.export_include_viewport_background,
        )

    def _default_viewport_background_asset_path(self) -> str:
        default_path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'assets',
            'Viewport_Background_Default.svg',
        ))
        return default_path if os.path.isfile(default_path) else ""

    def on_viewport_bg_image_changed(self, image_path: str):
        """Set or clear the viewport background image path."""
        normalized = (image_path or "").strip()
        if not normalized:
            fallback = self._default_viewport_background_asset_path()
            if fallback:
                normalized = fallback
        self.viewport_bg_image_path = normalized
        self.settings.setValue('viewport/background_image_path', normalized)
        if hasattr(self, "control_panel"):
            self.control_panel.set_viewport_bg_image(
                normalized,
                self.viewport_bg_image_enabled,
            )
        if hasattr(self, "gl_widget") and self.gl_widget:
            self.gl_widget.set_viewport_background_image_path(normalized)
        if normalized:
            self.log_widget.log(
                f"Viewport background image set: {os.path.basename(normalized)}",
                "SUCCESS",
            )
        else:
            self.log_widget.log("Viewport background image cleared.", "INFO")

    def on_auto_background_color_requested(self):
        """Attempt to find a color not present in current sprite textures."""
        suggestion = self._suggest_unused_background_color()
        if suggestion:
            self._apply_solid_bg_color(suggestion, announce=True)
            self.control_panel.set_solid_bg_color(suggestion)
            self.log_widget.log(
                "Unique background color suggestion is based on the current sprite atlas pixels.",
                "INFO",
            )
        else:
            self.log_widget.log(
                "Unable to find a unique background color; try selecting one manually.",
                "WARNING",
            )

    def on_reset_bpm_requested(self):
        """Reset BPM to detected base for current animation."""
        if self.current_animation_name and self.current_animation_name in self.animation_bpm_overrides:
            del self.animation_bpm_overrides[self.current_animation_name]
        token = self._current_monster_token()
        token_key = token.lower() if token else None
        if token_key and token_key in self.monster_base_bpm_overrides:
            del self.monster_base_bpm_overrides[token_key]
            self._save_base_bpm_overrides()
            if token:
                self.log_widget.log(f"Cleared locked BPM for {token}.", "INFO")
        self._configure_animation_bpm()

    def on_lock_base_bpm_requested(self):
        """Prompt the user to lock the base BPM for the current monster."""
        token = self._current_monster_token()
        if not token:
            self.log_widget.log("Load a monster before locking its BPM.", "WARNING")
            return
        initial = max(20.0, min(300.0, float(self.current_base_bpm or 120.0)))
        value, ok = QInputDialog.getDouble(
            self,
            "Lock Base BPM",
            f"Set the base BPM for {token}:",
            initial,
            20.0,
            300.0,
            1,
        )
        if not ok:
            return
        locked_value = max(20.0, min(300.0, float(value)))
        previous_base = max(1e-3, self.current_base_bpm)
        playback_ratio = self.current_bpm / previous_base if previous_base > 0 else 1.0
        self.current_base_bpm = locked_value
        new_bpm = max(20.0, min(300.0, playback_ratio * locked_value))
        token_key = token.lower()
        self.monster_base_bpm_overrides[token_key] = locked_value
        self._save_base_bpm_overrides()
        self._set_current_bpm(new_bpm, update_ui=True, store_override=False)
        self.log_widget.log(
            f"Locked base BPM for {token} at {locked_value:.1f}.",
            "SUCCESS",
        )

    def render_frame_to_image(
        self,
        width: int,
        height: int,
        *,
        camera_override: Optional[Tuple[float, float]] = None,
        render_scale_override: Optional[float] = None,
        apply_centering: bool = True,
        background_color: Optional[Tuple[int, int, int, int]] = None,
        include_viewport_background: bool = False,
        motion_blur_frame_dt: Optional[float] = None,
    ) -> Optional[Image.Image]:
        """
        Render the current frame to a PIL Image.
        """
        rgba = self.render_frame_to_rgba_array(
            width,
            height,
            camera_override=camera_override,
            render_scale_override=render_scale_override,
            apply_centering=apply_centering,
            background_color=background_color,
            include_viewport_background=include_viewport_background,
            motion_blur_frame_dt=motion_blur_frame_dt,
        )
        if rgba is None:
            return None
        return Image.fromarray(rgba, 'RGBA')

    def render_frame_to_rgba_array(
        self,
        width: int,
        height: int,
        *,
        camera_override: Optional[Tuple[float, float]] = None,
        render_scale_override: Optional[float] = None,
        apply_centering: bool = True,
        background_color: Optional[Tuple[int, int, int, int]] = None,
        include_viewport_background: bool = False,
        motion_blur_frame_dt: Optional[float] = None,
    ) -> Optional[np.ndarray]:
        """
        Render the current frame to a top-down straight-alpha RGBA numpy array.
        """
        fbo = None
        texture = None
        accum_fbo = None
        accum_texture = None
        default_fbo = None
        viewport_before = (0, 0, self.gl_widget.width(), self.gl_widget.height())
        projection_pushed = False
        modelview_pushed = False
        try:
            self.gl_widget.makeCurrent()
            default_fbo = self.gl_widget.defaultFramebufferObject()
            try:
                viewport_vec = glGetIntegerv(GL_VIEWPORT)
                if viewport_vec is not None and len(viewport_vec) >= 4:
                    viewport_before = (
                        int(viewport_vec[0]),
                        int(viewport_vec[1]),
                        int(viewport_vec[2]),
                        int(viewport_vec[3]),
                    )
                else:
                    viewport_before = (0, 0, self.gl_widget.width(), self.gl_widget.height())
            except Exception:
                viewport_before = (0, 0, self.gl_widget.width(), self.gl_widget.height())
            fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            texture = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, texture)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0)
            if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
                self.log_widget.log("Framebuffer not complete", "ERROR")
                return None
            glViewport(0, 0, width, height)
            glMatrixMode(GL_PROJECTION)
            glPushMatrix()
            projection_pushed = True
            glLoadIdentity()
            glOrtho(0, width, height, 0, -1, 1)
            glMatrixMode(GL_MODELVIEW)
            glPushMatrix()
            modelview_pushed = True
            glClearColor(0.0, 0.0, 0.0, 0.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glEnable(GL_BLEND)
            glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_TEXTURE_2D)
            camera_x = camera_override[0] if camera_override else self.gl_widget.camera_x
            camera_y = camera_override[1] if camera_override else self.gl_widget.camera_y
            render_scale = render_scale_override if render_scale_override is not None else self.gl_widget.render_scale

            def _render_export_scene(sample_time: float) -> None:
                glBindFramebuffer(GL_FRAMEBUFFER, int(fbo))
                glViewport(0, 0, width, height)
                # Keep renderer-side mask passes bound to this export FBO instead of
                # falling back to the default window framebuffer.
                self.gl_widget.renderer.viewport_size_hint = (max(1, int(width)), max(1, int(height)))
                self.gl_widget.renderer.framebuffer_binding_hint = int(fbo)
                glClearColor(0.0, 0.0, 0.0, 0.0)
                glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
                glEnable(GL_BLEND)
                glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
                glEnable(GL_TEXTURE_2D)
                if include_viewport_background:
                    self.gl_widget._render_viewport_background_image(
                        view_width=width,
                        view_height=height,
                        camera_x=camera_x,
                        camera_y=camera_y,
                        render_scale=render_scale,
                    )
                if self.gl_widget.player.animation:
                    glLoadIdentity()
                    glTranslatef(camera_x, camera_y, 0)
                    glScalef(render_scale, render_scale, 1.0)
                    if apply_centering and self.gl_widget.player.animation.centered:
                        glTranslatef(width / 2, height / 2, 0)
                    self.gl_widget._render_tile_batches()
                    self.gl_widget.render_all_layers(
                        float(sample_time),
                        apply_constraints=False,
                        render_particles=True,
                    )

            def _accumulate_scene_texture(weight: float, additive: bool) -> None:
                glBindFramebuffer(GL_FRAMEBUFFER, int(accum_fbo))
                glViewport(0, 0, width, height)
                if additive:
                    glEnable(GL_BLEND)
                    glBlendFunc(GL_ONE, GL_ONE)
                else:
                    glDisable(GL_BLEND)
                glLoadIdentity()
                glBindTexture(GL_TEXTURE_2D, int(texture))
                w = max(0.0, min(1.0, float(weight)))
                glColor4f(w, w, w, w)
                # Match post resolve orientation for FBO textures.
                glBegin(GL_QUADS)
                glTexCoord2f(0.0, 1.0)
                glVertex2f(0.0, 0.0)
                glTexCoord2f(1.0, 1.0)
                glVertex2f(float(width), 0.0)
                glTexCoord2f(1.0, 0.0)
                glVertex2f(float(width), float(height))
                glTexCoord2f(0.0, 0.0)
                glVertex2f(0.0, float(height))
                glEnd()
                glColor4f(1.0, 1.0, 1.0, 1.0)
                glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
                glEnable(GL_BLEND)

            source_texture = int(texture)
            current_time = float(self.gl_widget.player.current_time)
            motion_blur_strength = max(
                0.0,
                min(1.0, float(getattr(self.gl_widget, "post_motion_blur_strength", 0.0))),
            )
            motion_blur_enabled = bool(
                getattr(self.gl_widget, "post_motion_blur_enabled", False)
                and motion_blur_strength > 1e-4
                and self.gl_widget.player.animation is not None
            )
            frame_dt = None
            try:
                if motion_blur_frame_dt is not None:
                    frame_dt = float(motion_blur_frame_dt)
            except Exception:
                frame_dt = None
            if frame_dt is None or frame_dt <= 0.0:
                frame_dt = 1.0 / max(1.0, float(self.control_panel.fps_spin.value()))
            frame_dt = max(1.0 / 240.0, min(1.0 / 20.0, frame_dt))
            shutter_span = frame_dt * motion_blur_strength

            if motion_blur_enabled and shutter_span > 1e-5:
                accum_fbo = glGenFramebuffers(1)
                glBindFramebuffer(GL_FRAMEBUFFER, accum_fbo)
                accum_texture = glGenTextures(1)
                glBindTexture(GL_TEXTURE_2D, accum_texture)
                glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
                glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, accum_texture, 0)
                if glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE:
                    sample_count = max(2, min(12, int(round(2.0 + motion_blur_strength * 10.0))))
                    step = shutter_span / float(sample_count)
                    start = -0.5 * shutter_span + 0.5 * step
                    sample_weight = 1.0 / float(sample_count)
                    duration = float(self.gl_widget.player.duration or 0.0)
                    loop_enabled = bool(self.gl_widget.player.loop)

                    glBindFramebuffer(GL_FRAMEBUFFER, int(accum_fbo))
                    glViewport(0, 0, width, height)
                    glClearColor(0.0, 0.0, 0.0, 0.0)
                    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

                    for sample_index in range(sample_count):
                        sample_offset = start + float(sample_index) * step
                        sample_time = float(current_time + sample_offset)
                        if duration > 1e-6:
                            if loop_enabled:
                                sample_time = float(sample_time % duration)
                            else:
                                sample_time = float(max(0.0, min(duration, sample_time)))
                        _render_export_scene(sample_time)
                        _accumulate_scene_texture(sample_weight, additive=(sample_index > 0))
                    source_texture = int(accum_texture)
                else:
                    if accum_fbo is not None:
                        glDeleteFramebuffers(1, [int(accum_fbo)])
                    if accum_texture is not None:
                        glDeleteTextures(1, [int(accum_texture)])
                    accum_fbo = None
                    accum_texture = None

            if source_texture == int(texture):
                _render_export_scene(current_time)

            read_fbo = int(accum_fbo) if (accum_fbo and source_texture == int(accum_texture)) else int(fbo)
            if bool(getattr(self.gl_widget, "is_post_pass_enabled", lambda: False)()):
                try:
                    post_fbo, _post_texture = self.gl_widget.resolve_post_aa_texture(
                        int(source_texture),
                        int(width),
                        int(height),
                    )
                    if post_fbo:
                        read_fbo = int(post_fbo)
                except Exception as exc:
                    self.log_widget.log(
                        f"Post AA resolve failed for export frame: {exc}",
                        "WARNING",
                    )
            glBindFramebuffer(GL_FRAMEBUFFER, read_fbo)
            glReadBuffer(GL_COLOR_ATTACHMENT0)
            pixels = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
            rgba = np.frombuffer(pixels, dtype=np.uint8).reshape((height, width, 4))
            rgba = np.flipud(rgba).copy()
            rgba = self._unpremultiply_rgba_array(rgba)
            if background_color:
                rgba = self._composite_background_array(rgba, background_color)
            return rgba
        except Exception as e:
            self.log_widget.log(f"Error rendering frame: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            return None
        finally:
            target_fbo = default_fbo if default_fbo is not None else 0
            glBindFramebuffer(GL_FRAMEBUFFER, target_fbo)
            if fbo is not None:
                glDeleteFramebuffers(1, [fbo])
            if texture is not None:
                glDeleteTextures(1, [texture])
            if accum_fbo is not None:
                glDeleteFramebuffers(1, [accum_fbo])
            if accum_texture is not None:
                glDeleteTextures(1, [accum_texture])
            if projection_pushed:
                glMatrixMode(GL_PROJECTION)
                glPopMatrix()
            if modelview_pushed:
                glMatrixMode(GL_MODELVIEW)
                glPopMatrix()
            if viewport_before is not None:
                glViewport(*viewport_before)
            else:
                glViewport(0, 0, self.gl_widget.width(), self.gl_widget.height())
            self.gl_widget.doneCurrent()
            self.gl_widget.update()

    def _find_sprite_in_atlases(self, sprite_name: str):
        """Return (sprite, atlas) for a sprite name."""
        for atlas in self.gl_widget.texture_atlases:
            sprite = atlas.get_sprite(sprite_name)
            if sprite:
                return sprite, atlas
        return None, None

    def _compute_frame_bounds(self, time: float, include_hidden: bool = False) -> Optional[Tuple[float, float, float, float]]:
        """
        Compute world-space bounds for all visible layers at a specific time.
        """
        animation = self.gl_widget.player.animation
        if not animation:
            return None

        layer_states = self.gl_widget._build_layer_world_states(anim_time=time, apply_constraints=False)
        renderer = self.gl_widget.renderer

        min_x = math.inf
        min_y = math.inf
        max_x = -math.inf
        max_y = -math.inf
        any_layer = False

        for layer in animation.layers:
            if not include_hidden and not layer.visible:
                continue
            state = layer_states.get(layer.layer_id)
            if not state:
                continue

            sprite_name = state.get('sprite_name')
            if not sprite_name:
                continue

            sprite, atlas = self._find_sprite_in_atlases(sprite_name)
            if not sprite or not atlas:
                continue

            local_vertices = renderer.compute_local_vertices(sprite, atlas)
            if not local_vertices:
                continue
            user_offset_x, user_offset_y = self.gl_widget.layer_offsets.get(layer.layer_id, (0, 0))

            m00 = state['m00']
            m01 = state['m01']
            m10 = state['m10']
            m11 = state['m11']
            tx = state['tx'] + user_offset_x
            ty = state['ty'] + user_offset_y

            for lx, ly in local_vertices:
                wx = m00 * lx + m01 * ly + tx
                wy = m10 * lx + m11 * ly + ty
                min_x = min(min_x, wx)
                min_y = min(min_y, wy)
                max_x = max(max_x, wx)
                max_y = max(max_y, wy)
                any_layer = True

        if not any_layer:
            return None

        return (min_x, min_y, max_x, max_y)

    def _merge_bounds(
        self,
        existing: Optional[Tuple[float, float, float, float]],
        new_bounds: Optional[Tuple[float, float, float, float]]
    ) -> Optional[Tuple[float, float, float, float]]:
        if not new_bounds:
            return existing
        if not existing:
            return new_bounds
        min_x = min(existing[0], new_bounds[0])
        min_y = min(existing[1], new_bounds[1])
        max_x = max(existing[2], new_bounds[2])
        max_y = max(existing[3], new_bounds[3])
        return (min_x, min_y, max_x, max_y)

    def _compute_animation_bounds(self, fps: float, include_hidden: bool = False) -> Optional[Tuple[float, float, float, float]]:
        """
        Compute aggregate bounds for the entire animation by sampling each frame at the export FPS.
        """
        animation = self.gl_widget.player.animation
        if not animation or fps <= 0.0:
            return None

        duration = self.gl_widget.player.duration
        total_frames = max(1, int(math.ceil(duration * fps)))
        aggregated = None

        for frame_index in range(total_frames + 1):
            frame_time = min(duration, frame_index / fps)
            bounds = self._compute_frame_bounds(frame_time, include_hidden)
            aggregated = self._merge_bounds(aggregated, bounds)

        return aggregated

    def _compute_animation_bounds_for_animation(
        self,
        animation: AnimationData,
        fps: float,
        *,
        include_hidden: bool = False,
        layer_offsets_override: Optional[Dict[int, Tuple[float, float]]] = None,
    ) -> Optional[Tuple[float, float, float, float]]:
        """Compute aggregate bounds for an arbitrary animation without changing the loaded UI state."""
        if not animation or fps <= 0.0:
            return None
        temp_player = AnimationPlayer()
        temp_player.load_animation(animation)
        duration = temp_player.duration
        total_frames = max(1, int(math.ceil(duration * fps)))
        aggregated = None

        prev_animation = self.gl_widget.player.animation
        prev_duration = self.gl_widget.player.duration
        prev_time = self.gl_widget.player.current_time
        prev_offsets = self.gl_widget.layer_offsets
        try:
            self.gl_widget.player.animation = animation
            self.gl_widget.player.duration = duration
            self.gl_widget.layer_offsets = dict(layer_offsets_override or {})
            for frame_index in range(total_frames + 1):
                frame_time = min(duration, frame_index / fps)
                self.gl_widget.player.current_time = frame_time
                bounds = self._compute_frame_bounds(frame_time, include_hidden)
                aggregated = self._merge_bounds(aggregated, bounds)
        finally:
            self.gl_widget.player.animation = prev_animation
            self.gl_widget.player.duration = prev_duration
            self.gl_widget.player.current_time = prev_time
            self.gl_widget.layer_offsets = prev_offsets

        return aggregated

    def _compute_export_scope_bounds(
        self,
        fps: float,
        *,
        scope: str = "current",
        include_hidden: bool = False,
    ) -> Optional[Tuple[float, float, float, float]]:
        """Compute export framing bounds for the current animation or every loaded animation."""
        if fps <= 0.0 or not self.gl_widget.player.animation:
            return None

        normalized_scope = str(scope or "current").strip().lower()
        if normalized_scope != "all":
            return self._compute_animation_bounds(fps, include_hidden=include_hidden)

        self._persist_current_animation_edits()
        payload = self.current_json_data or {}
        anim_dicts = payload.get("anims")
        if not isinstance(anim_dicts, list) or not anim_dicts:
            return self._compute_animation_bounds(fps, include_hidden=include_hidden)

        blend_version = int(payload.get("blend_version", self.current_blend_version or 1) or 1)
        aggregated: Optional[Tuple[float, float, float, float]] = None
        current_animation = self.gl_widget.player.animation

        for index, anim_dict in enumerate(anim_dicts):
            if not isinstance(anim_dict, dict):
                continue
            if index == self.current_animation_index and current_animation is not None:
                animation_obj = current_animation
                layer_offsets_override = dict(self.gl_widget.layer_offsets)
            else:
                try:
                    animation_obj = self._build_animation_struct(
                        anim_dict,
                        blend_version,
                        source_path=self.current_json_path,
                        resource_dict=payload,
                    )
                except Exception as exc:
                    self.log_widget.log(
                        f"Skipped animation bounds for '{anim_dict.get('name', f'Animation {index + 1}')}' during export framing: {exc}",
                        "WARNING",
                    )
                    continue
                layer_offsets_override = {}

            bounds = self._compute_animation_bounds_for_animation(
                animation_obj,
                fps,
                include_hidden=include_hidden,
                layer_offsets_override=layer_offsets_override,
            )
            aggregated = self._merge_bounds(aggregated, bounds)

        return aggregated

    def _compute_export_render_plan(
        self,
        fps: float,
        *,
        fallback_width: int,
        fallback_height: int,
        use_full_res: bool,
        extra_scale: float = 1.0,
        include_hidden: bool = False,
    ) -> Tuple[int, int, Optional[Tuple[float, float]], Optional[float], bool]:
        """Return export canvas and camera settings for the active universal framing configuration."""
        width_fallback = max(1, int(fallback_width))
        height_fallback = max(1, int(fallback_height))
        width_fallback = width_fallback if width_fallback % 2 == 0 else width_fallback + 1
        height_fallback = height_fallback if height_fallback % 2 == 0 else height_fallback + 1

        explicit_resolution = bool(
            getattr(self.export_settings, "universal_export_explicit_resolution", False)
        )
        bounds_scope = str(
            getattr(self.export_settings, "universal_export_bounds_scope", "current") or "current"
        ).strip().lower()
        if bounds_scope not in {"current", "all"}:
            bounds_scope = "current"
        bounds_padding = max(
            0.0, float(getattr(self.export_settings, "universal_export_padding", 8.0) or 0.0)
        )

        should_compute_bounds = use_full_res or explicit_resolution or bounds_scope == "all"
        bounds = None
        if should_compute_bounds:
            bounds = self._compute_export_scope_bounds(
                fps,
                scope=bounds_scope,
                include_hidden=include_hidden,
            )

        if not bounds:
            return width_fallback, height_fallback, None, None, True

        min_x, min_y, max_x, max_y = bounds
        min_x -= bounds_padding * 0.5
        min_y -= bounds_padding * 0.5
        max_x += bounds_padding * 0.5
        max_y += bounds_padding * 0.5
        width_units = max(1e-3, max_x - min_x)
        height_units = max(1e-3, max_y - min_y)
        center_x = min_x + width_units * 0.5
        center_y = min_y + height_units * 0.5

        if explicit_resolution:
            export_width = max(
                16,
                int(getattr(self.export_settings, "universal_export_width", width_fallback) or width_fallback),
            )
            export_height = max(
                16,
                int(getattr(self.export_settings, "universal_export_height", height_fallback) or height_fallback),
            )
            export_width = export_width if export_width % 2 == 0 else export_width + 1
            export_height = export_height if export_height % 2 == 0 else export_height + 1
            render_scale_override = min(
                export_width / width_units,
                export_height / height_units,
            )
        elif use_full_res:
            full_res_scale = self._get_full_resolution_scale() * max(1.0, float(extra_scale or 1.0))
            render_scale_override = full_res_scale
            export_width = max(1, int(math.ceil(width_units * render_scale_override)))
            export_height = max(1, int(math.ceil(height_units * render_scale_override)))
            export_width = export_width if export_width % 2 == 0 else export_width + 1
            export_height = export_height if export_height % 2 == 0 else export_height + 1
        else:
            export_width = width_fallback
            export_height = height_fallback
            render_scale_override = min(
                export_width / width_units,
                export_height / height_units,
            )

        camera_override = (
            export_width * 0.5 - render_scale_override * center_x,
            export_height * 0.5 - render_scale_override * center_y,
        )
        return export_width, export_height, camera_override, render_scale_override, False

    @staticmethod
    def _unpremultiply_image(image: Image.Image) -> Image.Image:
        """Convert a premultiplied-alpha image to straight alpha."""
        if image.mode != 'RGBA':
            return image
        arr = np.array(image, dtype=np.float32)
        alpha = arr[..., 3:4]
        # Legacy atlases often store stray color data in texels with almost zero
        # alpha. Treat sub-1 alpha as fully transparent to avoid bright outlines.
        mask = alpha > 1.0
        safe_alpha = np.where(mask, alpha, 1.0)
        rgb = np.where(mask, arr[..., :3] * 255.0 / safe_alpha, 0.0)
        arr[..., :3] = np.clip(rgb, 0.0, 255.0)
        arr[..., 3:4] = np.where(mask, alpha, 0.0)
        return Image.fromarray(arr.astype(np.uint8), 'RGBA')

    @staticmethod
    def _premultiply_image(image: Image.Image) -> Image.Image:
        """Convert an RGBA image to premultiplied alpha."""
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        arr = np.array(image, dtype=np.float32)
        alpha = arr[..., 3:4] / 255.0
        arr[..., :3] = np.clip(arr[..., :3] * alpha, 0.0, 255.0)
        return Image.fromarray(arr.astype(np.uint8), 'RGBA')

    @staticmethod
    def _unpremultiply_rgba_array(arr: np.ndarray) -> np.ndarray:
        """Convert a premultiplied RGBA uint8 array to straight alpha."""
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8, copy=False)
        work = arr.astype(np.float32, copy=True)
        alpha = work[..., 3:4]
        mask = alpha > 1.0
        safe_alpha = np.where(mask, alpha, 1.0)
        rgb = np.where(mask, work[..., :3] * 255.0 / safe_alpha, 0.0)
        work[..., :3] = np.clip(rgb, 0.0, 255.0)
        work[..., 3:4] = np.where(mask, alpha, 0.0)
        return work.astype(np.uint8)

    @staticmethod
    def _composite_background(image: Image.Image, color: Tuple[int, int, int, int]) -> Image.Image:
        """Composite an RGBA image over an opaque background color."""
        if image.mode != 'RGBA':
            return image
        base = Image.new('RGBA', image.size, color)
        return Image.alpha_composite(base, image)

    @staticmethod
    def _composite_background_array(arr: np.ndarray, color: Tuple[int, int, int, int]) -> np.ndarray:
        """Composite an RGBA uint8 array over a solid background color."""
        if arr.dtype != np.uint8:
            arr = arr.astype(np.uint8, copy=False)
        bg = np.empty_like(arr)
        bg[..., 0] = int(color[0])
        bg[..., 1] = int(color[1])
        bg[..., 2] = int(color[2])
        bg[..., 3] = int(color[3])
        src = arr.astype(np.float32) / 255.0
        dst = bg.astype(np.float32) / 255.0
        src_a = src[..., 3:4]
        dst_a = dst[..., 3:4]
        out_a = src_a + dst_a * (1.0 - src_a)
        safe_out_a = np.where(out_a > 1e-6, out_a, 1.0)
        out_rgb = (
            src[..., :3] * src_a
            + dst[..., :3] * dst_a * (1.0 - src_a)
        ) / safe_out_a
        out = np.empty_like(src)
        out[..., :3] = np.clip(out_rgb, 0.0, 1.0)
        out[..., 3:4] = np.clip(out_a, 0.0, 1.0)
        return np.clip(out * 255.0, 0.0, 255.0).astype(np.uint8)

    def _create_unique_export_folder(self, root: str, base_name: str) -> str:
        """Create a unique directory inside root with base_name."""
        safe_name = re.sub(r'[^0-9a-zA-Z_-]+', '_', base_name).strip('_') or "animation"
        root = os.path.abspath(root)
        os.makedirs(root, exist_ok=True)

        candidate = os.path.join(root, safe_name)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(root, f"{safe_name}_{counter:02d}")
            counter += 1
        os.makedirs(candidate, exist_ok=True)
        return candidate

    def _get_full_resolution_scale(self) -> float:
        """
        Determine the multiplier needed to restore native sprite resolution.

        Returns 2.0 if any loaded atlas is marked as hi-res (downscaled by 0.5),
        otherwise returns 1.0.
        """
        scale = 1.0
        for atlas in self.gl_widget.texture_atlases:
            if atlas.is_hires:
                scale = max(scale, 2.0)
        return scale
    
    def export_current_frame(self):
        """Export current frame as transparent PNG"""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        
        export_params = self._compute_png_export_params()
        if not export_params:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        export_width, export_height, camera_override, render_scale_override, apply_centering = export_params
        if camera_override:
            self.log_widget.log(
                f"PNG full-resolution bounds: {export_width}x{export_height}", "INFO"
            )
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Frame", "", "PNG Image (*.png)"
        )
        
        if filename:
            try:
                background_color = self._active_background_color()
                include_viewport_bg = bool(self.export_include_viewport_background)
                image = self.render_frame_to_image(
                    export_width,
                    export_height,
                    camera_override=camera_override,
                    render_scale_override=render_scale_override,
                    apply_centering=apply_centering,
                    background_color=background_color,
                    include_viewport_background=include_viewport_bg,
                    motion_blur_frame_dt=1.0 / max(1.0, float(self.control_panel.fps_spin.value())),
                )
                
                if image:
                    image.save(filename, 'PNG')
                    self.log_widget.log(f"Frame exported to: {filename}", "SUCCESS")
                else:
                    self.log_widget.log("Failed to render frame", "ERROR")
            
            except Exception as e:
                self.log_widget.log(f"Error exporting frame: {e}", "ERROR")
                import traceback
                traceback.print_exc()

    def export_animation_frames_as_png(self):
        """Export every frame of the current animation as PNG files."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return

        target_dir = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder", ""
        )
        if not target_dir:
            return

        export_params = self._compute_png_export_params()
        if not export_params:
            QMessageBox.warning(self, "Error", "Unable to prepare export settings")
            return
        width, height, camera_override, render_scale_override, apply_centering = export_params

        animation_name = self.gl_widget.player.animation.name or "animation"
        sanitized_name = re.sub(r'[^0-9a-zA-Z_-]+', '_', animation_name).strip('_') or "animation"
        export_root = self._create_unique_export_folder(target_dir, sanitized_name)

        fps = float(max(1, self.control_panel.fps_spin.value()))
        real_duration = self._get_export_real_duration()
        total_frames = max(1, int(real_duration * fps))

        progress = QProgressDialog("Exporting frames...", "Cancel", 0, total_frames, self)
        progress.setWindowTitle("PNG Frames Export")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        original_time = self.gl_widget.player.current_time
        original_playing = self.gl_widget.player.playing
        self._set_player_playing(False)

        exported = 0
        try:
            background_color = self._active_background_color()
            include_viewport_bg = bool(self.export_include_viewport_background)
            for frame_idx in range(total_frames):
                if progress.wasCanceled():
                    self.log_widget.log("Frame export cancelled by user", "WARNING")
                    break

                frame_time = self._get_export_frame_time(frame_idx, fps)
                self.gl_widget.player.current_time = frame_time
                image = self.render_frame_to_image(
                    width,
                    height,
                    camera_override=camera_override,
                    render_scale_override=render_scale_override,
                    apply_centering=apply_centering,
                    background_color=background_color,
                    include_viewport_background=include_viewport_bg,
                    motion_blur_frame_dt=1.0 / max(1e-6, float(fps)),
                )
                if image:
                    filename = os.path.join(
                        export_root, f"{sanitized_name}_{frame_idx + 1:05d}.png"
                    )
                    image.save(filename, "PNG")
                    exported += 1
                else:
                    self.log_widget.log(f"Failed to render frame {frame_idx}", "WARNING")

                progress.setValue(frame_idx + 1)
                progress.setLabelText(f"Rendering frame {frame_idx + 1} of {total_frames}...")
                QApplication.processEvents()
        finally:
            progress.close()
            self.gl_widget.player.current_time = original_time
            self._set_player_playing(original_playing, sync_audio=True)
            self.gl_widget.update()

        if exported > 0:
            QMessageBox.information(
                self,
                "Frames Exported",
                f"Saved {exported} frames to:\n{export_root}"
            )
            self.log_widget.log(
                f"PNG frames exported to {export_root} ({exported} files)", "SUCCESS"
            )
        else:
            shutil.rmtree(export_root, ignore_errors=True)
            QMessageBox.warning(self, "Export Aborted", "No frames were exported.")

    def export_as_ae_rig(self):
        """Export an After Effects rig package matching the current viewport."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return

        target_dir = QFileDialog.getExistingDirectory(
            self, "Select AE Export Folder", ""
        )
        if not target_dir:
            return

        self._start_hang_watchdog("export_ae_rig", timeout=20.0)
        try:
            exporter = AERigExporter(self)
            success, message = exporter.export(target_dir)
        except Exception as exc:
            self.log_widget.log(f"AE rig export failed: {exc}", "ERROR")
            QMessageBox.warning(self, "Export Error", f"Failed to export AE rig: {exc}")
            return
        finally:
            self._stop_hang_watchdog()

        if success:
            self.log_widget.log(message, "SUCCESS")
            QMessageBox.information(self, "AE Rig Exported", message)
        else:
            self.log_widget.log(message, "ERROR")
            QMessageBox.warning(self, "Export Error", message)
    
    def export_as_psd(self):
        """Export current frame as PSD with individual sprite layers"""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        
        # Check for pytoshop
        pytoshop_module = self._ensure_pytoshop_available()
        if pytoshop_module is None:
            return
        psd_layers = pytoshop_module.layers
        ColorMode = pytoshop_module.enums.ColorMode
        GenericTaggedBlock = pytoshop_module.tagged_block.GenericTaggedBlock
        PsdFile = pytoshop_module.PsdFile
        packbits_ready = self._ensure_packbits_available()
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export PSD", "", "Photoshop Document (*.psd)"
        )
        
        if not filename:
            return
        
        # Ensure .psd extension
        if not filename.lower().endswith('.psd'):
            filename += '.psd'
        
        self._start_hang_watchdog("export_psd", timeout=15.0)
        try:
            
            animation = self.gl_widget.player.animation
            current_time = self.gl_widget.player.current_time
            include_hidden = self.export_settings.psd_include_hidden
            scale_percent = max(25, min(400, getattr(self.export_settings, 'psd_scale', 100)))
            export_scale = scale_percent / 100.0
            quality_mode = (getattr(self.export_settings, 'psd_quality', 'balanced') or 'balanced').lower()
            compression_mode = (getattr(self.export_settings, 'psd_compression', 'rle') or 'rle').lower()
            if compression_mode == 'rle' and not packbits_ready:
                self.log_widget.log(
                    "PackBits compression unavailable; falling back to RAW PSD channels.",
                    "WARNING"
                )
                compression_mode = 'raw'
            crop_canvas = getattr(self.export_settings, 'psd_crop_canvas', False)
            match_viewport = getattr(self.export_settings, 'psd_match_viewport', False)
            preserve_full_res = getattr(self.export_settings, 'psd_preserve_resolution', False)
            full_res_multiplier = max(1.0, float(getattr(self.export_settings, 'psd_full_res_multiplier', 1.0)))
            full_res_scale = self._get_full_resolution_scale() if preserve_full_res else 1.0
            native_scale_factor = full_res_scale * full_res_multiplier if preserve_full_res else 1.0
            if preserve_full_res:
                match_viewport = False
                export_scale = 1.0
                scale_percent = 100
            transform_filter, resize_filter = self.get_psd_resample_filters(quality_mode)
            compression_value = 1 if compression_mode == 'rle' else 0
            render_scale_for_export = self.gl_widget.render_scale if match_viewport else 1.0
            camera_x_for_export = self.gl_widget.camera_x if match_viewport else 0.0
            camera_y_for_export = self.gl_widget.camera_y if match_viewport else 0.0
            scale_factor = render_scale_for_export * export_scale
            
            viewport_width = self.gl_widget.width()
            viewport_height = self.gl_widget.height()
            # Scaled canvas dimensions default to viewport; they may be overridden later when
            # match_viewport is disabled or when cropping trims the canvas.
            if match_viewport:
                scaled_canvas_width = max(1, int(round(max(1, viewport_width) * export_scale * native_scale_factor)))
                scaled_canvas_height = max(1, int(round(max(1, viewport_height) * export_scale * native_scale_factor)))
            else:
                scaled_canvas_width = 0
                scaled_canvas_height = 0
            
            native_info = ""
            if preserve_full_res:
                native_info = f", native_scale={native_scale_factor:.2f}x"
            self.log_widget.log(
                f"Exporting PSD (scale={scale_percent}%, quality={quality_mode}, compression={compression_mode}, "
                f"full_res={'Yes' if preserve_full_res else 'No'}{native_info})",
                "INFO"
            )
            self.log_widget.log(
                f"Include hidden layers: {'Yes' if include_hidden else 'No'}, "
                f"crop canvas: {'Yes' if crop_canvas else 'No'}, "
                f"match viewer zoom: {'Yes' if match_viewport else 'No'}",
                "INFO"
            )
            
            # Utility helpers for coordinate conversions/metadata
            def _world_to_canvas(wx: float, wy: float) -> Tuple[float, float]:
                """Map world coordinates to PSD canvas space before cropping."""
                canvas_x = wx
                canvas_y = wy
                if match_viewport and animation.centered:
                    canvas_x += viewport_width / 2
                    canvas_y += viewport_height / 2
                canvas_x = canvas_x * render_scale_for_export + camera_x_for_export
                canvas_y = canvas_y * render_scale_for_export + camera_y_for_export
                canvas_x *= export_scale
                canvas_y *= export_scale
                if preserve_full_res and native_scale_factor != 1.0:
                    canvas_x *= native_scale_factor
                    canvas_y *= native_scale_factor
                return canvas_x, canvas_y

            def _offset_polygon_canvas(layer_info: Dict, dx: float, dy: float) -> None:
                """Shift stored polygon canvas coordinates when the canvas origin changes."""
                metadata_ref = layer_info.get('metadata')
                if not metadata_ref:
                    return
                sprite_meta = metadata_ref.get('sprite')
                if not sprite_meta:
                    return
                polygon_meta = sprite_meta.get('polygon')
                if not polygon_meta:
                    return

                def _shift_points(points: Optional[List[Dict[str, float]]]) -> None:
                    if not points:
                        return
                    for point in points:
                        point['x'] -= dx
                        point['y'] -= dy

                _shift_points(polygon_meta.get('vertices_canvas'))
                segments = polygon_meta.get('segments') or []
                for segment in segments:
                    _shift_points(segment.get('canvas'))
            
            def _render_polygon_sprite_layer(
                local_vertices: List[Tuple[float, float]],
                texcoords: List[Tuple[float, float]],
                triangles: List[int],
                world_matrix: Tuple[float, float, float, float, float, float],
                atlas_pixels: np.ndarray,
                color_tint: Tuple[float, float, float],
                opacity_value: float
            ) -> Optional[Dict[str, Any]]:
                """
                Rasterize a polygon sprite into layer space using barycentric sampling.
                Returns None if data is invalid.
                """
                if (
                    not local_vertices
                    or not texcoords
                    or not triangles
                    or len(local_vertices) != len(texcoords)
                    or atlas_pixels is None
                ):
                    return None
                
                m00, m01, m10, m11, tx, ty = world_matrix
                atlas_height, atlas_width = atlas_pixels.shape[:2]
                texcoords_px = [
                    (u * atlas_width, v * atlas_height)
                    for u, v in texcoords
                ]
                
                world_vertices = []
                canvas_vertices = []
                for lvx, lvy in local_vertices:
                    wx = m00 * lvx + m01 * lvy + tx
                    wy = m10 * lvx + m11 * lvy + ty
                    world_vertices.append({'x': wx, 'y': wy})
                    cx, cy = _world_to_canvas(wx, wy)
                    canvas_vertices.append({'x': cx, 'y': cy})
                
                if not canvas_vertices:
                    return None
                
                canvas_xs = [pt['x'] for pt in canvas_vertices]
                canvas_ys = [pt['y'] for pt in canvas_vertices]
                min_canvas_x = min(canvas_xs)
                max_canvas_x = max(canvas_xs)
                min_canvas_y = min(canvas_ys)
                max_canvas_y = max(canvas_ys)
                
                width = max(1, int(math.ceil(max_canvas_x - min_canvas_x)))
                height = max(1, int(math.ceil(max_canvas_y - min_canvas_y)))
                if width <= 0 or height <= 0:
                    return None
                
                layer_buffer = np.zeros((height, width, 4), dtype=np.float32)
                vertex_layer_coords = [
                    (pt['x'] - min_canvas_x, pt['y'] - min_canvas_y)
                    for pt in canvas_vertices
                ]
                epsilon = 1e-5
                
                texcoords_count = len(texcoords_px)
                vertex_count = len(vertex_layer_coords)
                
                for tri_start in range(0, len(triangles), 3):
                    idx0 = triangles[tri_start]
                    idx1 = triangles[tri_start + 1] if tri_start + 1 < len(triangles) else None
                    idx2 = triangles[tri_start + 2] if tri_start + 2 < len(triangles) else None
                    if (
                        idx0 is None or idx1 is None or idx2 is None
                        or idx0 >= vertex_count or idx1 >= vertex_count or idx2 >= vertex_count
                        or idx0 >= texcoords_count or idx1 >= texcoords_count or idx2 >= texcoords_count
                        or idx0 < 0 or idx1 < 0 or idx2 < 0
                    ):
                        continue
                    
                    (dx0, dy0) = vertex_layer_coords[idx0]
                    (dx1, dy1) = vertex_layer_coords[idx1]
                    (dx2, dy2) = vertex_layer_coords[idx2]
                    (sx0, sy0) = texcoords_px[idx0]
                    (sx1, sy1) = texcoords_px[idx1]
                    (sx2, sy2) = texcoords_px[idx2]
                    
                    tri_min_x = max(0, int(math.floor(min(dx0, dx1, dx2))))
                    tri_max_x = min(width, int(math.ceil(max(dx0, dx1, dx2))))
                    tri_min_y = max(0, int(math.floor(min(dy0, dy1, dy2))))
                    tri_max_y = min(height, int(math.ceil(max(dy0, dy1, dy2))))
                    
                    if tri_max_x <= tri_min_x or tri_max_y <= tri_min_y:
                        continue
                    
                    xs = np.arange(tri_min_x, tri_max_x)
                    ys = np.arange(tri_min_y, tri_max_y)
                    if xs.size == 0 or ys.size == 0:
                        continue
                    
                    grid_x_int, grid_y_int = np.meshgrid(xs, ys)
                    grid_x = grid_x_int.astype(np.float32)
                    grid_y = grid_y_int.astype(np.float32)
                    sample_x = grid_x + 0.5
                    sample_y = grid_y + 0.5
                    
                    denom = (dy1 - dy2) * (dx0 - dx2) + (dx2 - dx1) * (dy0 - dy2)
                    if abs(denom) < epsilon:
                        continue
                    
                    w0 = ((dy1 - dy2) * (sample_x - dx2) + (dx2 - dx1) * (sample_y - dy2)) / denom
                    w1 = ((dy2 - dy0) * (sample_x - dx2) + (dx0 - dx2) * (sample_y - dy2)) / denom
                    w2 = 1.0 - w0 - w1
                    mask = (w0 >= -epsilon) & (w1 >= -epsilon) & (w2 >= -epsilon)
                    if not np.any(mask):
                        continue
                    
                    mask_idx = np.nonzero(mask)
                    dest_x_vals = grid_x_int[mask_idx]
                    dest_y_vals = grid_y_int[mask_idx]
                    w0_vals = w0[mask_idx]
                    w1_vals = w1[mask_idx]
                    w2_vals = w2[mask_idx]
                    
                    src_x_vals = w0_vals * sx0 + w1_vals * sx1 + w2_vals * sx2
                    src_y_vals = w0_vals * sy0 + w1_vals * sy1 + w2_vals * sy2
                    
                    src_x_vals = np.clip(src_x_vals, 0.0, atlas_width - 1.0)
                    src_y_vals = np.clip(src_y_vals, 0.0, atlas_height - 1.0)
                    
                    x0_idx = np.floor(src_x_vals).astype(np.int32)
                    y0_idx = np.floor(src_y_vals).astype(np.int32)
                    x1_idx = np.clip(x0_idx + 1, 0, atlas_width - 1)
                    y1_idx = np.clip(y0_idx + 1, 0, atlas_height - 1)
                    
                    wx = (src_x_vals - x0_idx).astype(np.float32)
                    wy = (src_y_vals - y0_idx).astype(np.float32)
                    
                    top = atlas_pixels[y0_idx, x0_idx] * (1.0 - wx)[:, None] + atlas_pixels[y0_idx, x1_idx] * wx[:, None]
                    bottom = atlas_pixels[y1_idx, x0_idx] * (1.0 - wx)[:, None] + atlas_pixels[y1_idx, x1_idx] * wx[:, None]
                    samples = top * (1.0 - wy)[:, None] + bottom * wy[:, None]
                    
                    layer_buffer[dest_y_vals, dest_x_vals] = samples
                
                if color_tint:
                    tint_r, tint_g, tint_b = color_tint
                    if tint_r != 1.0:
                        layer_buffer[:, :, 0] *= tint_r
                    if tint_g != 1.0:
                        layer_buffer[:, :, 1] *= tint_g
                    if tint_b != 1.0:
                        layer_buffer[:, :, 2] *= tint_b
                if opacity_value < 1.0:
                    layer_buffer[:, :, 3] *= opacity_value
                
                layer_bytes = np.clip(layer_buffer, 0.0, 1.0)
                layer_bytes = (layer_bytes * 255.0 + 0.5).astype(np.uint8)
                layer_image = Image.fromarray(layer_bytes, 'RGBA')
                return {
                    'image': layer_image,
                    'origin_x': min_canvas_x,
                    'origin_y': min_canvas_y,
                    'canvas_vertices': canvas_vertices,
                    'world_vertices': world_vertices
                }

            # Build layer map and calculate world states
            layer_map = {layer.layer_id: layer for layer in animation.layers}
            layer_world_states = {}
            
            for layer in animation.layers:
                state = self.gl_widget.renderer.calculate_world_state(
                    layer, current_time, self.gl_widget.player, layer_map, 
                    layer_world_states, self.gl_widget.texture_atlases
                )
                layer_world_states[layer.layer_id] = self.gl_widget.apply_user_transforms(
                    layer.layer_id, state
                )
            
            # Cache loaded atlas images and pixel arrays
            atlas_images = {}
            atlas_pixel_arrays = {}
            
            # Collect layer data for PSD
            psd_layer_data = []
            
            # Process layers in reverse order (back to front, like rendering)
            for layer in reversed(animation.layers):
                if not include_hidden and not layer.visible:
                    continue
                
                world_state = layer_world_states[layer.layer_id]
                sprite_name = world_state['sprite_name']
                
                if not sprite_name:
                    continue
                
                # Find sprite in atlases
                sprite = None
                atlas = None
                for atl in self.gl_widget.texture_atlases:
                    sprite = atl.get_sprite(sprite_name)
                    if sprite:
                        atlas = atl
                        break
                
                if not sprite or not atlas:
                    continue
                
                # Load atlas image if not cached
                if atlas.image_path not in atlas_images:
                    try:
                        atlas_img = Image.open(atlas.image_path)
                        atlas_img = atlas_img.convert('RGBA')
                        atlas_images[atlas.image_path] = atlas_img
                        atlas_pixel_arrays[atlas.image_path] = np.asarray(atlas_img, dtype=np.float32) / 255.0
                    except Exception as e:
                        self.log_widget.log(f"Failed to load atlas: {e}", "WARNING")
                        continue
                elif atlas.image_path not in atlas_pixel_arrays:
                    atlas_pixel_arrays[atlas.image_path] = np.asarray(atlas_images[atlas.image_path], dtype=np.float32) / 255.0
                
                atlas_img = atlas_images[atlas.image_path]
                atlas_pixels = atlas_pixel_arrays.get(atlas.image_path)
                
                # Extract sprite from atlas for quad-based fallback rendering
                sprite_img = atlas_img.crop((
                    sprite.x, sprite.y,
                    sprite.x + sprite.w, sprite.y + sprite.h
                ))
                if sprite.rotated:
                    sprite_img = sprite_img.rotate(90, expand=True)
                orig_sprite_w, orig_sprite_h = sprite_img.size
                hires_scale = 0.5 if atlas.is_hires else 1.0
                
                # Transformation matrix and color information
                m00 = world_state['m00']
                m01 = world_state['m01']
                m10 = world_state['m10']
                m11 = world_state['m11']
                tx = world_state['tx']
                ty = world_state['ty']
                opacity = world_state['world_opacity']
                r = world_state['r'] / 255.0
                g = world_state['g'] / 255.0
                b = world_state['b'] / 255.0
                
                # Apply user offsets
                user_offset_x, user_offset_y = self.gl_widget.layer_offsets.get(layer.layer_id, (0, 0))
                tx += user_offset_x
                ty += user_offset_y
                
                # Attempt polygon-aware rasterization if geometry is available
                polygon_local_vertices: List[Tuple[float, float]] = []
                polygon_texcoords: List[Tuple[float, float]] = []
                polygon_triangles: List[int] = []
                polygon_world_vertices: List[Dict[str, float]] = []
                polygon_canvas_vertices: List[Dict[str, float]] = []
                polygon_render_result: Optional[Dict[str, Any]] = None
                
                if sprite.has_polygon_mesh:
                    try:
                        geometry = self.gl_widget.renderer._build_polygon_geometry(sprite, atlas)
                    except Exception as geom_exc:  # pragma: no cover - defensive
                        self.log_widget.log(
                            f"Failed to build polygon geometry for {layer.name}: {geom_exc}",
                            "WARNING"
                        )
                        geometry = None
                    if geometry:
                        polygon_local_vertices, polygon_texcoords, polygon_triangles = geometry
                        if atlas_pixels is not None:
                            try:
                                polygon_render_result = _render_polygon_sprite_layer(
                                    polygon_local_vertices,
                                    polygon_texcoords,
                                    polygon_triangles,
                                    (m00, m01, m10, m11, tx, ty),
                                    atlas_pixels,
                                    (r, g, b),
                                    opacity
                                )
                            except Exception as raster_exc:  # pragma: no cover - defensive
                                self.log_widget.log(
                                    f"Polygon rasterization failed for {layer.name}: {raster_exc}",
                                    "WARNING"
                                )
                                polygon_render_result = None
                        if polygon_render_result:
                            polygon_world_vertices = polygon_render_result['world_vertices']
                            polygon_canvas_vertices = polygon_render_result['canvas_vertices']
                
                transformed_img = None
                final_x = 0.0
                final_y = 0.0
                polygon_layer_used = polygon_render_result is not None
                
                if polygon_layer_used:
                    transformed_img = polygon_render_result['image']
                    final_x = polygon_render_result['origin_x']
                    final_y = polygon_render_result['origin_y']
                else:
                    # Fall back to quad-based affine transform rendering
                    trim_multiplier = self.gl_widget.renderer.trim_shift_multiplier
                    sprite_offset_x = sprite.offset_x * hires_scale * trim_multiplier * self.gl_widget.position_scale
                    sprite_offset_y = sprite.offset_y * hires_scale * trim_multiplier * self.gl_widget.position_scale
                    scaled_w = orig_sprite_w * hires_scale * self.gl_widget.position_scale
                    scaled_h = orig_sprite_h * hires_scale * self.gl_widget.position_scale
                    corners_local = [
                        (sprite_offset_x, sprite_offset_y),
                        (sprite_offset_x + scaled_w, sprite_offset_y),
                        (sprite_offset_x + scaled_w, sprite_offset_y + scaled_h),
                        (sprite_offset_x, sprite_offset_y + scaled_h),
                    ]
                    corners_world = []
                    for lx, ly in corners_local:
                        wx = m00 * lx + m01 * ly + tx
                        wy = m10 * lx + m11 * ly + ty
                        corners_world.append((wx, wy))
                    world_xs = [c[0] for c in corners_world]
                    world_ys = [c[1] for c in corners_world]
                    world_min_x = min(world_xs)
                    world_max_x = max(world_xs)
                    world_min_y = min(world_ys)
                    world_max_y = max(world_ys)
                    bbox_w = int(math.ceil(world_max_x - world_min_x))
                    bbox_h = int(math.ceil(world_max_y - world_min_y))
                    if bbox_w <= 0 or bbox_h <= 0:
                        continue
                    det = m00 * m11 - m01 * m10
                    if abs(det) < 1e-10:
                        continue
                    inv_m00 = m11 / det
                    inv_m01 = -m01 / det
                    inv_m10 = -m10 / det
                    inv_m11 = m00 / det
                    offset_x = world_min_x - tx
                    offset_y = world_min_y - ty
                    inv_tx = inv_m00 * offset_x + inv_m01 * offset_y
                    inv_ty = inv_m10 * offset_x + inv_m11 * offset_y
                    inv_tx -= sprite_offset_x
                    inv_ty -= sprite_offset_y
                    scale_to_img_x = orig_sprite_w / scaled_w if scaled_w > 0 else 1
                    scale_to_img_y = orig_sprite_h / scaled_h if scaled_h > 0 else 1
                    final_a = inv_m00 * scale_to_img_x
                    final_b = inv_m01 * scale_to_img_x
                    final_c = inv_tx * scale_to_img_x
                    final_d = inv_m10 * scale_to_img_y
                    final_e = inv_m11 * scale_to_img_y
                    final_f = inv_ty * scale_to_img_y
                    image_scale = scale_factor
                    if preserve_full_res:
                        image_scale *= native_scale_factor
                    if image_scale <= 0:
                        image_scale = 1.0
                    target_w = max(1, int(math.ceil(bbox_w * image_scale)))
                    target_h = max(1, int(math.ceil(bbox_h * image_scale)))
                    transformed_img = sprite_img.transform(
                        (target_w, target_h),
                        Image.Transform.AFFINE,
                        (
                            final_a / image_scale,
                            final_b / image_scale,
                            final_c,
                            final_d / image_scale,
                            final_e / image_scale,
                            final_f
                        ),
                        resample=transform_filter
                    )
                    if r != 1.0 or g != 1.0 or b != 1.0:
                        img_r, img_g, img_b, img_a = transformed_img.split()
                        img_r = img_r.point(lambda x: int(x * r))
                        img_g = img_g.point(lambda x: int(x * g))
                        img_b = img_b.point(lambda x: int(x * b))
                        transformed_img = Image.merge('RGBA', (img_r, img_g, img_b, img_a))
                    if opacity < 1.0:
                        img_r, img_g, img_b, img_a = transformed_img.split()
                        img_a = img_a.point(lambda x: int(x * opacity))
                        transformed_img = Image.merge('RGBA', (img_r, img_g, img_b, img_a))
                    final_x = world_min_x
                    final_y = world_min_y
                    if match_viewport and animation.centered:
                        final_x += viewport_width / 2
                        final_y += viewport_height / 2
                    final_x = final_x * render_scale_for_export + camera_x_for_export
                    final_y = final_y * render_scale_for_export + camera_y_for_export
                    final_x *= export_scale
                    final_y *= export_scale
                    if preserve_full_res and native_scale_factor != 1.0:
                        final_x *= native_scale_factor
                        final_y *= native_scale_factor
                
                if transformed_img is None:
                    continue
                
                # Store layer data
                psd_blend_mode = self._map_psd_blend_mode(layer.blend_mode)
                atlas_rel = atlas.image_path
                if self.game_path:
                    try:
                        atlas_rel = os.path.relpath(atlas.image_path, os.path.join(self.game_path, "data"))
                    except Exception:
                        pass
                metadata: Dict[str, Any] = {
                    'blend_mode': {
                        'engine_id': layer.blend_mode,
                        'engine_label': self._describe_engine_blend_mode(layer.blend_mode),
                        'psd_mode': psd_blend_mode.name if psd_blend_mode else None
                    },
                    'sprite': {
                        'name': sprite.name,
                        'atlas_path': atlas_rel,
                        'rect': {'x': sprite.x, 'y': sprite.y, 'w': sprite.w, 'h': sprite.h},
                        'offset': {'x': sprite.offset_x, 'y': sprite.offset_y},
                        'original_size': {'w': sprite.original_w, 'h': sprite.original_h},
                        'rotated': sprite.rotated,
                        'has_polygon_mesh': sprite.has_polygon_mesh
                    }
                }
                if sprite.has_polygon_mesh:
                    atlas_w = atlas.image_width or (atlas_img.width if atlas_img else 0)
                    atlas_h = atlas.image_height or (atlas_img.height if atlas_img else 0)
                    polygon_meta: Dict[str, Any] = {
                        'vertices': sprite.vertices,
                        'vertices_uv': sprite.vertices_uv,
                        'triangles': sprite.triangles,
                        'vertex_space': 'trimmed_local',
                        'uv_space': 'atlas_normalized'
                    }
                    if sprite.vertices_uv and atlas_w > 0 and atlas_h > 0:
                        polygon_meta['vertices_uv_pixels'] = [
                            {'u': uv_x * atlas_w, 'v': uv_y * atlas_h}
                            for uv_x, uv_y in sprite.vertices_uv
                        ]
                    renderer = self.gl_widget.renderer
                    local_vertices_for_meta: List[Tuple[float, float]] = polygon_local_vertices.copy()
                    if not local_vertices_for_meta and renderer and hasattr(renderer, 'compute_local_vertices'):
                        try:
                            local_vertices_for_meta = renderer.compute_local_vertices(sprite, atlas)
                        except Exception as vertex_exc:  # pragma: no cover - defensive
                            self.log_widget.log(
                                f"Failed to derive polygon vertices for {layer.name}: {vertex_exc}",
                                "WARNING"
                            )
                            local_vertices_for_meta = []
                    if local_vertices_for_meta:
                        polygon_meta['local_vertices_scaled'] = local_vertices_for_meta
                    if not polygon_world_vertices and local_vertices_for_meta:
                        computed_world: List[Dict[str, float]] = []
                        computed_canvas: List[Dict[str, float]] = []
                        for lvx, lvy in local_vertices_for_meta:
                            world_x = m00 * lvx + m01 * lvy + tx
                            world_y = m10 * lvx + m11 * lvy + ty
                            canvas_x, canvas_y = _world_to_canvas(world_x, world_y)
                            computed_world.append({'x': world_x, 'y': world_y})
                            computed_canvas.append({'x': canvas_x, 'y': canvas_y})
                        polygon_world_vertices = computed_world
                        polygon_canvas_vertices = computed_canvas
                    if polygon_world_vertices:
                        polygon_meta['vertices_world'] = [dict(pt) for pt in polygon_world_vertices]
                    if polygon_canvas_vertices:
                        polygon_meta['vertices_canvas'] = [dict(pt) for pt in polygon_canvas_vertices]
                    if (
                        local_vertices_for_meta
                        and polygon_world_vertices
                        and polygon_canvas_vertices
                    ):
                        segments: List[Dict[str, Any]] = []
                        vertex_count = len(local_vertices_for_meta)
                        uv_entries = sprite.vertices_uv or []
                        uv_pixels = polygon_meta.get('vertices_uv_pixels') or []
                        triangles = sprite.triangles or []
                        for tri_start in range(0, len(triangles), 3):
                            tri_indices = triangles[tri_start:tri_start + 3]
                            if len(tri_indices) < 3:
                                continue
                            if any(idx < 0 or idx >= vertex_count for idx in tri_indices):
                                continue
                            seg_entry: Dict[str, Any] = {
                                'indices': tri_indices,
                                'world': [dict(polygon_world_vertices[idx]) for idx in tri_indices],
                                'canvas': [dict(polygon_canvas_vertices[idx]) for idx in tri_indices]
                            }
                            if uv_entries and all(idx < len(uv_entries) for idx in tri_indices):
                                seg_entry['uv_normalized'] = [
                                    {'u': uv_entries[idx][0], 'v': uv_entries[idx][1]}
                                    for idx in tri_indices
                                ]
                            if uv_pixels and all(idx < len(uv_pixels) for idx in tri_indices):
                                seg_entry['uv_pixels'] = [
                                    {'u': uv_pixels[idx]['u'], 'v': uv_pixels[idx]['v']}
                                    for idx in tri_indices
                                ]
                            segments.append(seg_entry)
                        if segments:
                            polygon_meta['segments'] = segments
                    metadata['sprite']['polygon'] = polygon_meta
                psd_layer_data.append({
                    'name': layer.name,
                    'image': transformed_img,
                    'x': int(round(final_x)),
                    'y': int(round(final_y)),
                    'opacity': int(max(0.0, min(1.0, opacity)) * 255),
                    'width': transformed_img.width,
                    'height': transformed_img.height,
                    'blend_mode': layer.blend_mode,
                    'psd_blend_mode': psd_blend_mode,
                    'metadata': metadata
                })
            
            self.log_widget.log(f"Processed {len(psd_layer_data)} visible layers", "INFO")
            
            # Adjust canvas bounds
            crop_left = 0
            crop_top = 0
            if psd_layer_data:
                content_left = min(layer_info['x'] for layer_info in psd_layer_data)
                content_top = min(layer_info['y'] for layer_info in psd_layer_data)
                content_right = max(layer_info['x'] + layer_info['width'] for layer_info in psd_layer_data)
                content_bottom = max(layer_info['y'] + layer_info['height'] for layer_info in psd_layer_data)
                
                if not match_viewport:
                    # Always expand to include the full sprite content regardless of camera zoom
                    crop_left = int(math.floor(content_left))
                    crop_top = int(math.floor(content_top))
                    scaled_canvas_width = max(1, int(math.ceil(content_right - content_left)))
                    scaled_canvas_height = max(1, int(math.ceil(content_bottom - content_top)))
                    
                    for layer_info in psd_layer_data:
                        layer_info['x'] -= crop_left
                        layer_info['y'] -= crop_top
                        _offset_polygon_canvas(layer_info, crop_left, crop_top)
                    
                    self.log_widget.log(
                        f"Canvas set to full content bounds: {scaled_canvas_width}x{scaled_canvas_height}", "INFO"
                    )
                elif crop_canvas:
                    visible_left = scaled_canvas_width
                    visible_top = scaled_canvas_height
                    visible_right = 0
                    visible_bottom = 0
                    
                    for layer_info in psd_layer_data:
                        left = max(0, min(scaled_canvas_width, layer_info['x']))
                        top = max(0, min(scaled_canvas_height, layer_info['y']))
                        right = max(left, min(scaled_canvas_width, layer_info['x'] + layer_info['width']))
                        bottom = max(top, min(scaled_canvas_height, layer_info['y'] + layer_info['height']))
                        
                        if right <= left or bottom <= top:
                            continue
                        
                        visible_left = min(visible_left, left)
                        visible_top = min(visible_top, top)
                        visible_right = max(visible_right, right)
                        visible_bottom = max(visible_bottom, bottom)
                    
                    if visible_right > visible_left and visible_bottom > visible_top:
                        crop_left = int(visible_left)
                        crop_top = int(visible_top)
                        scaled_canvas_width = max(1, int(visible_right - visible_left))
                        scaled_canvas_height = max(1, int(visible_bottom - visible_top))
                        
                        for layer_info in psd_layer_data:
                            layer_info['x'] -= crop_left
                            layer_info['y'] -= crop_top
                            _offset_polygon_canvas(layer_info, crop_left, crop_top)
                        
                        self.log_widget.log(
                            f"Cropped PSD canvas to {scaled_canvas_width}x{scaled_canvas_height}", "INFO"
                        )
            
            if scaled_canvas_width <= 0 or scaled_canvas_height <= 0:
                scaled_canvas_width = max(1, int(round(max(1, viewport_width) * export_scale * (native_scale_factor if preserve_full_res else 1.0))))
                scaled_canvas_height = max(1, int(round(max(1, viewport_height) * export_scale * (native_scale_factor if preserve_full_res else 1.0))))
            
            self.log_widget.log(
                f"Final PSD canvas: {scaled_canvas_width}x{scaled_canvas_height}", "INFO"
            )

            bg_color = self._active_background_color()
            if bg_color:
                background_layer = Image.new('RGBA', (scaled_canvas_width, scaled_canvas_height), bg_color)
                psd_layer_data.insert(0, {
                    'name': "Background Color",
                    'image': background_layer,
                    'x': 0,
                    'y': 0,
                    'opacity': 255,
                    'width': scaled_canvas_width,
                    'height': scaled_canvas_height,
                    'blend_mode': BlendMode.STANDARD,
                    'psd_blend_mode': self._map_psd_blend_mode(BlendMode.STANDARD),
                    'metadata': {'background_fill': True, 'color': {'r': bg_color[0], 'g': bg_color[1], 'b': bg_color[2], 'a': bg_color[3]}},
                })
            
            # Determine color mode enum (older pytoshop versions use lowercase names)
            psd_color_mode = getattr(ColorMode, 'RGB', None)
            if psd_color_mode is None:
                psd_color_mode = getattr(ColorMode, 'rgb', None)
            if psd_color_mode is None:
                try:
                    psd_color_mode = ColorMode(3)  # RGB fallback
                except Exception:
                    psd_color_mode = None
            
            psd_kwargs = dict(
                num_channels=4,
                height=scaled_canvas_height,
                width=scaled_canvas_width
            )
            if psd_color_mode is not None:
                psd_kwargs['color_mode'] = psd_color_mode
            
            # Create PSD file using pytoshop
            psd = PsdFile(**psd_kwargs)
            
            # Add each layer
            for layer_info in psd_layer_data:
                img = layer_info['image']
                x = layer_info['x']
                y = layer_info['y']
                name = layer_info['name']
                layer_opacity = layer_info['opacity']
                
                # Convert PIL image to numpy array
                img_array = np.array(img)
                
                # Get layer dimensions
                layer_h, layer_w = img_array.shape[:2]
                
                # Calculate layer bounds (clipped to canvas)
                left = max(0, x)
                top = max(0, y)
                right = min(scaled_canvas_width, x + layer_w)
                bottom = min(scaled_canvas_height, y + layer_h)
                
                # Skip if layer is completely outside canvas
                if left >= right or top >= bottom:
                    continue
                
                # Calculate the portion of the image that's visible
                img_left = left - x
                img_top = top - y
                img_right = img_left + (right - left)
                img_bottom = img_top + (bottom - top)
                
                # Crop image to visible portion
                visible_img = img_array[img_top:img_bottom, img_left:img_right]
                
                if visible_img.size == 0:
                    continue
                
                # Split into channels (R, G, B, A)
                if len(visible_img.shape) == 3 and visible_img.shape[2] == 4:
                    alpha_channel = visible_img[:, :, 3]
                    red_channel = visible_img[:, :, 0]
                    green_channel = visible_img[:, :, 1]
                    blue_channel = visible_img[:, :, 2]
                elif len(visible_img.shape) == 3 and visible_img.shape[2] == 3:
                    alpha_channel = np.full((visible_img.shape[0], visible_img.shape[1]), 255, dtype=np.uint8)
                    red_channel = visible_img[:, :, 0]
                    green_channel = visible_img[:, :, 1]
                    blue_channel = visible_img[:, :, 2]
                else:
                    continue
                
                # Create channel image data objects
                alpha_data = psd_layers.ChannelImageData(image=alpha_channel, compression=compression_value)
                red_data = psd_layers.ChannelImageData(image=red_channel, compression=compression_value)
                green_data = psd_layers.ChannelImageData(image=green_channel, compression=compression_value)
                blue_data = psd_layers.ChannelImageData(image=blue_channel, compression=compression_value)
                
                # Create layer record with channels and metadata blocks
                blend_kwargs = {}
                psd_blend_mode = layer_info.get('psd_blend_mode')
                if psd_blend_mode is not None:
                    blend_kwargs['blend_mode'] = psd_blend_mode
                blocks = []
                metadata_payload = layer_info.get('metadata')
                if metadata_payload:
                    try:
                        metadata_bytes = json.dumps(
                            metadata_payload,
                            ensure_ascii=False,
                            separators=(',', ':')
                        ).encode('utf-8')
                        blocks.append(GenericTaggedBlock(code=b'mETA', data=metadata_bytes))
                    except Exception as exc:
                        self.log_widget.log(
                            f"Failed to encode PSD metadata for {layer_info['name']}: {exc}",
                            "WARNING"
                        )
                layer_record = psd_layers.LayerRecord(
                    top=top,
                    left=left,
                    bottom=bottom,
                    right=right,
                    name=name[:31],
                    opacity=layer_opacity,
                    channels={
                        -1: alpha_data,
                        0: red_data,
                        1: green_data,
                        2: blue_data,
                    },
                    blocks=blocks or None,
                    **blend_kwargs
                )
                
                psd.layer_and_mask_info.layer_info.layer_records.append(layer_record)
            
            # Write PSD file
            with open(filename, 'wb') as f:
                psd.write(f)
            
            file_size = os.path.getsize(filename)
            self.log_widget.log(f"PSD exported to: {filename} ({file_size} bytes)", "SUCCESS")
            
            QMessageBox.information(
                self, "Export Complete",
                f"PSD exported successfully!\n\n"
                f"File: {filename}\n"
                f"Layers: {len(psd_layer_data)}\n"
                f"Size: {scaled_canvas_width}x{scaled_canvas_height}"
            )
            
        except Exception as e:
            self.log_widget.log(f"Error exporting PSD: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Export Error", f"Failed to export PSD: {e}")
        finally:
            self._stop_hang_watchdog()
    
    def get_psd_resample_filters(self, quality: str):
        """Return (transform_filter, resize_filter) for PSD export quality modes"""
        quality = (quality or 'balanced').lower()
        if quality == 'fast':
            return Image.Resampling.NEAREST, Image.Resampling.NEAREST
        if quality == 'high':
            return Image.Resampling.BICUBIC, Image.Resampling.BICUBIC
        if quality == 'maximum':
            # PIL transform only accepts NEAREST/BILINEAR/BICUBIC; keep transform bicubic but
            # allow final resize to use higher quality Lanczos filtering.
            return Image.Resampling.BICUBIC, Image.Resampling.LANCZOS
        # balanced / default
        return Image.Resampling.BILINEAR, Image.Resampling.BILINEAR

    def _map_psd_blend_mode(self, blend_mode: int):
        """Map internal blend modes to Photoshop equivalents."""
        module = self._ensure_pytoshop_available()
        if module is None:
            return None
        try:
            PSDBlendMode = module.enums.BlendMode
        except Exception:
            return None

        if blend_mode == BlendMode.ADDITIVE:
            return PSDBlendMode.linear_dodge
        if blend_mode == BlendMode.MULTIPLY:
            return PSDBlendMode.multiply
        if blend_mode == BlendMode.SCREEN:
            return PSDBlendMode.screen
        return PSDBlendMode.normal

    def _describe_engine_blend_mode(self, blend_mode: int) -> str:
        labels = {
            BlendMode.STANDARD: "Standard",
            BlendMode.PREMULT_ALPHA: "Premultiplied Alpha",
            BlendMode.ADDITIVE: "Additive",
            BlendMode.PREMULT_ALPHA_ALT: "Premultiplied Alpha (Alt)",
            BlendMode.PREMULT_ALPHA_ALT2: "Premultiplied Alpha (Alt2)",
            BlendMode.INHERIT: "Inherit",
            BlendMode.MULTIPLY: "Multiply",
            BlendMode.SCREEN: "Screen",
        }
        return labels.get(blend_mode, f"Unknown({blend_mode})")
    
    def _resolve_ffmpeg_path(self) -> Optional[str]:
        """Return a working FFmpeg path, updating cached value as needed."""
        stored_path = self.settings.value('ffmpeg/path', '', type=str)
        ffmpeg_path = resolve_ffmpeg_path(stored_path)

        if ffmpeg_path:
            if stored_path != ffmpeg_path:
                self.settings.setValue('ffmpeg/path', ffmpeg_path)
            return ffmpeg_path

        if stored_path:
            self.settings.remove('ffmpeg/path')
        return None

    @staticmethod
    def _ffmpeg_thread_args(max_threads: int = 0) -> List[str]:
        """Return FFmpeg thread arguments when multiple cores are available."""
        if max_threads <= 0:
            cpu_count = os.cpu_count() or 1
            max_threads = cpu_count
        thread_count = max(1, min(32, int(max_threads)))
        if thread_count <= 1:
            return []
        return ['-threads', str(thread_count)]

    def _ffmpeg_supports_encoder(self, ffmpeg_path: str, encoder_name: str) -> bool:
        """Return True if the active FFmpeg build reports support for the requested encoder."""
        if not ffmpeg_path or not encoder_name:
            return False
        return encoder_name in query_ffmpeg_encoders(ffmpeg_path)

    @staticmethod
    def _map_nvenc_preset(preset_name: str) -> str:
        """Map UI preset names to broadly supported NVENC presets."""
        preset = (preset_name or "medium").strip().lower()
        preset_map = {
            "ultrafast": "p1",
            "superfast": "p1",
            "veryfast": "p2",
            "faster": "p3",
            "fast": "p4",
            "medium": "p5",
            "slow": "p6",
            "slower": "p6",
            "veryslow": "p7",
        }
        return preset_map.get(preset, "p5")

    def _build_nvenc_video_args(
        self,
        codec: str,
        *,
        quality_value: int,
        preset_name: str,
        pix_fmt: Optional[str] = None,
        bitrate_kbps: int = 0,
        lossless: bool = False,
    ) -> List[str]:
        """Build FFmpeg NVENC arguments for H.264/HEVC exports."""
        args: List[str] = ['-c:v', codec, '-preset', self._map_nvenc_preset(preset_name)]
        if pix_fmt:
            args += ['-pix_fmt', pix_fmt]
        if lossless:
            args += ['-tune', 'lossless', '-rc', 'constqp', '-qp', '0']
        else:
            args += ['-cq', str(max(0, int(quality_value)))]
            if bitrate_kbps > 0:
                args += ['-b:v', f'{int(bitrate_kbps)}k', '-maxrate', f'{int(bitrate_kbps)}k']
            else:
                args += ['-b:v', '0']
        return args

    def _render_video_frames(
        self,
        fps: int,
        include_audio: bool,
        use_full_res: bool,
        extra_scale: float,
        *,
        export_label: str = "Video",
    ) -> Optional[Dict[str, Any]]:
        """Render animation frames (and optional audio) for a video export."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return None

        real_duration = self._get_export_real_duration()
        total_frames = int(real_duration * fps)
        if total_frames <= 0:
            QMessageBox.warning(self, "Error", "Animation has no frames to export")
            return None

        use_full_res = bool(use_full_res)
        extra_scale = max(1.0, float(extra_scale or 1.0))
        width, height, camera_override, render_scale_override, apply_centering = (
            self._compute_export_render_plan(
                fps,
                fallback_width=self.gl_widget.width(),
                fallback_height=self.gl_widget.height(),
                use_full_res=use_full_res,
                extra_scale=extra_scale,
            )
        )

        self.log_widget.log(
            f"{export_label} export dimensions: {width}x{height} at {fps} FPS",
            "INFO",
        )

        temp_dir = tempfile.mkdtemp(prefix='msm_export_')
        self.log_widget.log(f"{export_label} temp directory: {temp_dir}", "INFO")

        original_time = self.gl_widget.player.current_time
        original_playing = self.gl_widget.player.playing
        self._set_player_playing(False)

        progress = QProgressDialog(
            f"Exporting {export_label} frames...",
            "Cancel",
            0,
            total_frames,
            self,
        )
        progress.setWindowTitle("Export Progress")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        frame_files: List[str] = []
        was_canceled = False
        try:
            background_color = self._active_background_color()
            include_viewport_bg = bool(self.export_include_viewport_background)
            for frame_num in range(total_frames):
                if progress.wasCanceled():
                    was_canceled = True
                    self.log_widget.log(f"{export_label} export cancelled by user", "WARNING")
                    break

                frame_time = self._get_export_frame_time(frame_num, fps)
                self.gl_widget.player.current_time = frame_time

                image = self.render_frame_to_image(
                    width,
                    height,
                    camera_override=camera_override,
                    render_scale_override=render_scale_override,
                    apply_centering=apply_centering,
                    background_color=background_color,
                    include_viewport_background=include_viewport_bg,
                    motion_blur_frame_dt=1.0 / max(1e-6, float(fps)),
                )
                if image:
                    frame_path = os.path.join(temp_dir, f"frame_{frame_num:06d}.png")
                    image.save(frame_path, 'PNG')
                    frame_files.append(frame_path)
                else:
                    self.log_widget.log(f"Failed to render frame {frame_num}", "WARNING")

                progress.setValue(frame_num + 1)
                progress.setLabelText(f"Rendering frame {frame_num + 1} of {total_frames}...")

                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()
        finally:
            progress.close()

        if was_canceled or len(frame_files) == 0:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.gl_widget.player.current_time = original_time
            self._set_player_playing(original_playing)
            self.gl_widget.update()
            return None

        audio_track_path = None
        if include_audio:
            if self.audio_manager.is_ready:
                audio_speed, audio_mode = self._get_audio_export_config()
                audio_segment = self.audio_manager.export_audio_segment(
                    real_duration,
                    speed=audio_speed,
                    pitch_mode=audio_mode,
                )
                if audio_segment:
                    samples, sample_rate = audio_segment
                    audio_track_path = os.path.join(temp_dir, "audio_track.wav")
                    try:
                        sf.write(audio_track_path, samples, sample_rate)
                        audio_duration = len(samples) / sample_rate if sample_rate else 0.0
                        self.log_widget.log(
                            f"Prepared audio track ({sample_rate} Hz, {audio_duration:.2f}s) "
                            f"mode={audio_mode}, speed={audio_speed:.3f}",
                            "INFO",
                        )
                    except Exception as audio_error:
                        audio_track_path = None
                        self.log_widget.log(f"Failed to write audio track: {audio_error}", "WARNING")
                else:
                    self.log_widget.log("Audio track unavailable for export", "WARNING")
            else:
                self.log_widget.log("Audio export requested but no audio loaded", "WARNING")

        self.log_widget.log(
            f"Rendered {len(frame_files)} frames for {export_label} export, ready for encoding...",
            "INFO",
        )

        return {
            "temp_dir": temp_dir,
            "frame_files": frame_files,
            "input_pattern": os.path.join(temp_dir, "frame_%06d.png").replace('\\', '/'),
            "audio_path": audio_track_path,
            "original_time": original_time,
            "original_playing": original_playing,
            "width": width,
            "height": height,
            "fps": fps,
            "duration": real_duration,
        }

    def export_as_mov(self):
        """Export animation as transparent MOV video"""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        
        # Check for ffmpeg (supports auto-installed copy from settings)
        ffmpeg_path = self._resolve_ffmpeg_path()
        if not ffmpeg_path:
            QMessageBox.warning(
                self,
                "FFmpeg Required",
                "FFmpeg is required for MOV export.\n\n"
                "Use Settings > Application > FFmpeg Tools to perform the one-click install, "
                "or install FFmpeg manually and add it to PATH."
            )
            self.log_widget.log("FFmpeg not available on PATH or managed install", "ERROR")
            return
        
        self.log_widget.log(f"Found FFmpeg at: {ffmpeg_path}", "INFO")
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Animation", "", "MOV Video (*.mov)"
        )
        
        if not filename:
            return
        
        # Ensure .mov extension
        if not filename.lower().endswith('.mov'):
            filename += '.mov'

        mov_codec = self.export_settings.mov_codec
        mov_quality = getattr(self.export_settings, 'mov_quality', 'high') or 'high'
        output_file = filename.replace('\\', '/')
        
        fps = self.control_panel.fps_spin.value()
        mov_extra_scale = max(1.0, float(getattr(self.export_settings, 'mov_full_scale_multiplier', 1.0)))
        frame_info = self._render_video_frames(
            fps,
            include_audio=self.export_settings.mov_include_audio,
            use_full_res=self.export_settings.mov_full_resolution,
            extra_scale=mov_extra_scale,
            export_label="MOV",
        )
        if not frame_info:
            return

        temp_dir = frame_info["temp_dir"]
        audio_track_path = frame_info["audio_path"]
        input_pattern = frame_info["input_pattern"]
        output_file = filename.replace('\\', '/')
        mov_codec = self.export_settings.mov_codec

        self.log_widget.log(f"Input pattern: {input_pattern}", "INFO")
        self.log_widget.log(f"Output file: {output_file}", "INFO")
        self.log_widget.log(f"Using codec: {mov_codec}", "INFO")

        def build_ffmpeg_cmd(extra_args, audio_codec='pcm_s16le'):
            cmd = [
                ffmpeg_path,
                '-y',
                '-framerate', str(frame_info["fps"]),
                '-i', input_pattern,
            ]
            if audio_track_path:
                cmd += ['-i', audio_track_path]
            cmd += extra_args
            if audio_track_path:
                cmd += ['-c:a', audio_codec, '-shortest']
            return cmd

        def build_mov_codec_args(codec_name: str) -> List[str]:
            normalized = (codec_name or "").strip().lower()
            if normalized in {"h264_nvenc", "hevc_nvenc"}:
                quality_to_cq = {
                    "low": 28,
                    "medium": 24,
                    "high": 19,
                    "lossless": 0,
                }
                quality_to_preset = {
                    "low": "faster",
                    "medium": "fast",
                    "high": "medium",
                    "lossless": "veryslow",
                }
                lossless = mov_quality == "lossless"
                args = self._build_nvenc_video_args(
                    normalized,
                    quality_value=quality_to_cq.get(mov_quality, 19),
                    preset_name=quality_to_preset.get(mov_quality, "medium"),
                    pix_fmt="yuv420p",
                    lossless=lossless,
                )
                return args
            if normalized in {"prores_ks", "prores"}:
                return [
                    '-c:v', 'prores_ks',
                    '-profile:v', '4444',
                    '-pix_fmt', 'yuva444p10le',
                    '-vendor', 'apl0',
                ]
            if normalized == "png":
                return ['-c:v', 'png', '-pix_fmt', 'rgba']
            if normalized == "qtrle":
                return ['-c:v', 'qtrle', '-pix_fmt', 'argb']
            return ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18']

        if mov_codec == 'h264_nvenc' and not self._ffmpeg_supports_encoder(ffmpeg_path, 'h264_nvenc'):
            self.log_widget.log("Selected MOV codec h264_nvenc is unavailable in this FFmpeg build; falling back to libx264.", "WARNING")
            mov_codec = 'libx264'
        elif mov_codec == 'hevc_nvenc' and not self._ffmpeg_supports_encoder(ffmpeg_path, 'hevc_nvenc'):
            self.log_widget.log("Selected MOV codec hevc_nvenc is unavailable in this FFmpeg build; falling back to libx264.", "WARNING")
            mov_codec = 'libx264'

        export_success = False

        try:
            # Try ProRes 4444 first - best Adobe compatibility with alpha
            if mov_codec == 'prores_ks' or mov_codec == 'prores':
                self.log_widget.log("Trying ProRes 4444 (best Adobe compatibility)...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd(build_mov_codec_args('prores_ks') + [output_file])
                
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(f"Animation exported (ProRes 4444) to: {filename} ({file_size} bytes)", "SUCCESS")
                    export_success = True
            
            # Try PNG codec - good compatibility, lossless with alpha
            if not export_success and (mov_codec == 'png' or mov_codec == 'prores_ks'):
                self.log_widget.log("Trying PNG codec...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd(build_mov_codec_args('png') + [output_file])
                
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(f"Animation exported (PNG codec) to: {filename} ({file_size} bytes)", "SUCCESS")
                    export_success = True
            
            # Try QuickTime Animation (qtrle) with rgba pixel format
            if not export_success and mov_codec == 'qtrle':
                self.log_widget.log("Trying QuickTime Animation codec...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd(build_mov_codec_args('qtrle') + [output_file])
                
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(f"Animation exported (QuickTime Animation) to: {filename} ({file_size} bytes)", "SUCCESS")
                    export_success = True

            if not export_success and mov_codec in {'libx264', 'h264_nvenc', 'hevc_nvenc'}:
                self.log_widget.log(f"Trying {mov_codec} MOV export...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd(build_mov_codec_args(mov_codec) + [output_file], audio_codec='aac')
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(f"Animation exported ({mov_codec}) to: {filename} ({file_size} bytes)", "SUCCESS")
                    export_success = True
            
            # Fallback chain if preferred codec failed
            if not export_success:
                self.log_widget.log("Preferred codec failed, trying fallback chain...", "WARNING")
                
                # Try ProRes 4444 as first fallback
                self.log_widget.log("Fallback: Trying ProRes 4444...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd(build_mov_codec_args('prores_ks') + [output_file])
                
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(f"Animation exported (ProRes 4444 fallback) to: {filename} ({file_size} bytes)", "SUCCESS")
                    export_success = True
                else:
                    # Try PNG as second fallback
                    self.log_widget.log("Fallback: Trying PNG codec...", "INFO")
                    ffmpeg_cmd = build_ffmpeg_cmd(build_mov_codec_args('png') + [output_file])
                    
                    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                    
                    if result.returncode == 0 and os.path.exists(filename):
                        file_size = os.path.getsize(filename)
                        self.log_widget.log(f"Animation exported (PNG fallback) to: {filename} ({file_size} bytes)", "SUCCESS")
                        export_success = True
                    else:
                        # Final fallback - H.264 without alpha
                        self.log_widget.log("All alpha codecs failed, exporting without transparency...", "WARNING")
                        
                        mp4_file = filename.replace('.mov', '.mp4')
                        ffmpeg_cmd = build_ffmpeg_cmd(build_mov_codec_args('libx264') + [mp4_file], audio_codec='aac')
                        
                        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                        
                        if result.returncode == 0 and os.path.exists(mp4_file):
                            file_size = os.path.getsize(mp4_file)
                            self.log_widget.log(f"Animation exported (no alpha) to: {mp4_file} ({file_size} bytes)", "SUCCESS")
                            export_success = True
                        else:
                            self.log_widget.log(f"All encoding attempts failed. Error: {result.stderr}", "ERROR")
            
        except Exception as e:
            self.log_widget.log(f"Error exporting animation: {e}", "ERROR")
            import traceback
            traceback.print_exc()
        finally:
            self.log_widget.log("Cleaning up temporary files...", "INFO")
            shutil.rmtree(temp_dir, ignore_errors=True)
        self.gl_widget.player.current_time = frame_info["original_time"]
        self._set_player_playing(frame_info["original_playing"])
        self.gl_widget.update()

    def export_as_mp4(self):
        """Export animation as MP4 video."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return

        ffmpeg_path = self._resolve_ffmpeg_path()
        if not ffmpeg_path:
            QMessageBox.warning(
                self,
                "FFmpeg Required",
                "FFmpeg is required for MP4 export.\n\n"
                "Use Settings > Application > FFmpeg Tools to install it, "
                "or install FFmpeg manually and add it to PATH.",
            )
            self.log_widget.log("FFmpeg not available; MP4 export aborted.", "ERROR")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Animation", "", "MP4 Video (*.mp4)"
        )
        if not filename:
            return
        if not filename.lower().endswith('.mp4'):
            filename += '.mp4'

        codec = getattr(self.export_settings, 'mp4_codec', 'libx264') or 'libx264'
        crf = int(getattr(self.export_settings, 'mp4_crf', 18))
        preset = getattr(self.export_settings, 'mp4_preset', 'medium') or 'medium'
        bitrate = int(getattr(self.export_settings, 'mp4_bitrate', 0))
        pix_fmt = getattr(self.export_settings, 'mp4_pixel_format', 'yuv420p') or 'yuv420p'
        faststart = bool(getattr(self.export_settings, 'mp4_faststart', True))
        fps = self.control_panel.fps_spin.value()
        mp4_extra_scale = max(1.0, float(getattr(self.export_settings, 'mp4_full_scale_multiplier', 1.0)))
        frame_info = self._render_video_frames(
            fps,
            include_audio=getattr(self.export_settings, 'mp4_include_audio', True),
            use_full_res=getattr(self.export_settings, 'mp4_full_resolution', False),
            extra_scale=mp4_extra_scale,
            export_label="MP4",
        )
        if not frame_info:
            return

        temp_dir = frame_info["temp_dir"]
        audio_track_path = frame_info["audio_path"]
        input_pattern = frame_info["input_pattern"]
        output_file = filename.replace('\\', '/')
        thread_args = self._ffmpeg_thread_args()

        codec = getattr(self.export_settings, 'mp4_codec', 'libx264') or 'libx264'
        crf = int(getattr(self.export_settings, 'mp4_crf', 18))
        preset = getattr(self.export_settings, 'mp4_preset', 'medium') or 'medium'
        bitrate = int(getattr(self.export_settings, 'mp4_bitrate', 0))
        pix_fmt = getattr(self.export_settings, 'mp4_pixel_format', 'yuv420p') or 'yuv420p'
        faststart = bool(getattr(self.export_settings, 'mp4_faststart', True))

        if codec == 'h264_nvenc' and not self._ffmpeg_supports_encoder(ffmpeg_path, 'h264_nvenc'):
            self.log_widget.log("Selected MP4 codec h264_nvenc is unavailable in this FFmpeg build; falling back to libx264.", "WARNING")
            codec = 'libx264'
        elif codec == 'hevc_nvenc' and not self._ffmpeg_supports_encoder(ffmpeg_path, 'hevc_nvenc'):
            self.log_widget.log("Selected MP4 codec hevc_nvenc is unavailable in this FFmpeg build; falling back to libx265.", "WARNING")
            codec = 'libx265'

        self.log_widget.log(f"MP4 codec: {codec}, preset={preset}, CRF={crf}", "INFO")
        self.log_widget.log(f"Input pattern: {input_pattern}", "INFO")
        self.log_widget.log(f"Output file: {output_file}", "INFO")

        cmd = [
            ffmpeg_path,
            '-y',
            '-framerate', str(frame_info["fps"]),
            '-i', input_pattern,
        ]
        cmd += thread_args
        if audio_track_path:
            cmd += ['-i', audio_track_path]

        if codec in {'h264_nvenc', 'hevc_nvenc'}:
            cmd += self._build_nvenc_video_args(
                codec,
                quality_value=crf,
                preset_name=preset,
                pix_fmt=pix_fmt,
                bitrate_kbps=bitrate,
                lossless=False,
            )
        else:
            cmd += ['-c:v', codec, '-preset', preset, '-crf', str(crf)]
            if bitrate > 0:
                cmd += ['-b:v', f"{bitrate}k"]
            if pix_fmt:
                cmd += ['-pix_fmt', pix_fmt]
        if codec in {'libx265', 'hevc_nvenc'}:
            cmd += ['-tag:v', 'hvc1']
        if faststart:
            cmd += ['-movflags', '+faststart']

        if audio_track_path:
            cmd += ['-c:a', 'aac', '-b:a', '192k', '-shortest']
        else:
            cmd += ['-an']

        cmd.append(output_file)

        export_success = False
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and os.path.exists(filename):
                file_size = os.path.getsize(filename)
                self.log_widget.log(f"Animation exported (MP4) to: {filename} ({file_size} bytes)", "SUCCESS")
                export_success = True
            else:
                message = result.stderr.strip() or "Unknown error"
                self.log_widget.log(f"MP4 encoding failed: {message}", "ERROR")
        except Exception as exc:
            self.log_widget.log(f"Error exporting MP4: {exc}", "ERROR")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.gl_widget.player.current_time = frame_info["original_time"]
            self._set_player_playing(frame_info["original_playing"])
            self.gl_widget.update()

        if not export_success:
            QMessageBox.warning(
                self,
                "MP4 Export Failed",
                "FFmpeg was unable to encode the MP4 file. Check the log for details.",
            )

    def export_as_webm(self):
        """Export animation as WEBM video (supports alpha via VP9/AV1)."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return

        ffmpeg_path = self._resolve_ffmpeg_path()
        if not ffmpeg_path:
            QMessageBox.warning(
                self,
                "FFmpeg Required",
                "FFmpeg is required for WEBM export.\n\n"
                "Use Settings > Application > FFmpeg Tools to perform the one-click install, "
                "or install FFmpeg manually and add it to PATH.",
            )
            self.log_widget.log("FFmpeg not available on PATH or managed install", "ERROR")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self, "Export Animation", "", "WEBM Video (*.webm)"
        )
        if not filename:
            return
        if not filename.lower().endswith('.webm'):
            filename += '.webm'

        codec_pref = getattr(self.export_settings, 'webm_codec', 'libvpx-vp9')
        crf = int(getattr(self.export_settings, 'webm_crf', 28))
        speed = int(getattr(self.export_settings, 'webm_speed', 4))

        fps = self.control_panel.fps_spin.value()
        webm_extra_scale = max(1.0, float(getattr(self.export_settings, 'webm_full_scale_multiplier', 1.0)))
        frame_info = self._render_video_frames(
            fps,
            include_audio=self.export_settings.webm_include_audio,
            use_full_res=getattr(self.export_settings, 'webm_full_resolution', False),
            extra_scale=webm_extra_scale,
            export_label="WEBM",
        )
        if not frame_info:
            return

        temp_dir = frame_info["temp_dir"]
        audio_track_path = frame_info["audio_path"]
        input_pattern = frame_info["input_pattern"]
        codec_pref = getattr(self.export_settings, 'webm_codec', 'libvpx-vp9')
        crf = int(getattr(self.export_settings, 'webm_crf', 28))
        speed = int(getattr(self.export_settings, 'webm_speed', 4))
        output_file = filename.replace('\\', '/')
        thread_args = self._ffmpeg_thread_args()

        self.log_widget.log(f"Input pattern: {input_pattern}", "INFO")
        self.log_widget.log(f"Output file: {output_file}", "INFO")
        self.log_widget.log(f"Preferred WEBM codec: {codec_pref}", "INFO")

        def build_video_args(codec_name: str) -> Tuple[List[str], bool]:
            normalized = codec_name.lower()
            supports_alpha = normalized in ('libvpx-vp9', 'libaom-av1')
            pix_fmt = 'yuva420p' if supports_alpha else 'yuv420p'
            args = ['-c:v', normalized, '-pix_fmt', pix_fmt]
            if normalized == 'libvpx-vp9':
                args += ['-b:v', '0', '-crf', str(crf), '-row-mt', '1', '-tile-columns', '2', '-frame-parallel', '1', '-speed', str(speed)]
                if supports_alpha:
                    args += ['-auto-alt-ref', '0']
            elif normalized == 'libaom-av1':
                args += ['-b:v', '0', '-crf', str(crf), '-cpu-used', str(speed), '-row-mt', '1']
            else:
                args += ['-b:v', '0', '-crf', str(crf), '-quality', 'good', '-cpu-used', str(speed)]
            return args, supports_alpha

        def build_ffmpeg_cmd(video_args: List[str], audio_codec: str = 'libopus') -> List[str]:
            cmd = [
                ffmpeg_path,
                '-y',
                '-framerate', str(frame_info["fps"]),
                '-i', input_pattern,
            ]
            cmd += thread_args
            if audio_track_path:
                cmd += ['-i', audio_track_path]
            cmd += video_args
            if audio_track_path:
                cmd += ['-c:a', audio_codec, '-b:a', '160k', '-shortest']
            else:
                cmd += ['-an']
            cmd.append(output_file)
            return cmd

        encode_order: List[str] = [codec_pref]
        if 'libvpx-vp9' not in [c.lower() for c in encode_order]:
            encode_order.append('libvpx-vp9')
        if 'libvpx' not in [c.lower() for c in encode_order]:
            encode_order.append('libvpx')

        export_success = False
        try:
            for codec_name in encode_order:
                video_args, supports_alpha = build_video_args(codec_name)
                if not supports_alpha:
                    self.log_widget.log(
                        f"Codec '{codec_name}' does not support alpha; output will be opaque.",
                        "WARNING",
                    )
                self.log_widget.log(f"Encoding WEBM using {codec_name}...", "INFO")
                ffmpeg_cmd = build_ffmpeg_cmd(video_args)
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                if result.returncode == 0 and os.path.exists(filename):
                    file_size = os.path.getsize(filename)
                    self.log_widget.log(
                        f"Animation exported ({codec_name}) to: {filename} ({file_size} bytes)",
                        "SUCCESS",
                    )
                    export_success = True
                    break
                else:
                    self.log_widget.log(
                        f"{codec_name} encoding failed: {result.stderr.strip()}",
                        "WARNING",
                    )

            if not export_success:
                self.log_widget.log(
                    "WEBM encoding failed, exporting fallback MP4 without alpha.",
                    "WARNING",
                )
                mp4_file = filename.replace('.webm', '.mp4')
                ffmpeg_cmd = [
                    ffmpeg_path,
                    '-y',
                    '-framerate', str(frame_info["fps"]),
                    '-i', input_pattern,
                ]
                ffmpeg_cmd += thread_args
                if audio_track_path:
                    ffmpeg_cmd += ['-i', audio_track_path]
                ffmpeg_cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', mp4_file.replace('\\', '/')]
                if audio_track_path:
                    ffmpeg_cmd += ['-c:a', 'aac', '-b:a', '160k', '-shortest']
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                if result.returncode == 0 and os.path.exists(mp4_file):
                    file_size = os.path.getsize(mp4_file)
                    self.log_widget.log(
                        f"Animation exported (MP4 fallback) to: {mp4_file} ({file_size} bytes)",
                        "SUCCESS",
                    )
                    export_success = True
                else:
                    self.log_widget.log(
                        f"MP4 fallback also failed: {result.stderr.strip()}",
                        "ERROR",
                    )
        finally:
            self.log_widget.log("Cleaning up temporary files...", "INFO")
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.gl_widget.player.current_time = frame_info["original_time"]
            self._set_player_playing(frame_info["original_playing"])
            self.gl_widget.update()

    def show_settings(self):
        """Show settings dialog"""
        dialog = SettingsDialog(
            self.export_settings,
            self.settings,
            self.shader_registry,
            self.game_path,
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.log_widget.log("Settings saved", "SUCCESS")
            self.gl_widget.set_zoom_to_cursor(self.export_settings.camera_zoom_to_cursor)
            sprite_filter = self.settings.value("viewport/sprite_filter", "bilinear", type=str)
            self.gl_widget.set_sprite_filter_mode(sprite_filter)
            sprite_filter_strength = self.settings.value("viewport/sprite_filter_strength", 1.0, type=float)
            self.gl_widget.set_sprite_filter_strength(sprite_filter_strength)
            self.viewport_post_aa_enabled = bool(
                self.settings.value('viewport/post_aa_enabled', self.viewport_post_aa_enabled, type=bool)
            )
            self.viewport_post_aa_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('viewport/post_aa_strength', self.viewport_post_aa_strength, type=float)),
                ),
            )
            viewport_post_aa_mode = (
                self.settings.value('viewport/post_aa_mode', self.viewport_post_aa_mode, type=str) or self.viewport_post_aa_mode
            )
            viewport_post_aa_mode = str(viewport_post_aa_mode).strip().lower()
            if viewport_post_aa_mode not in {'fxaa', 'smaa'}:
                viewport_post_aa_mode = 'fxaa'
            self.viewport_post_aa_mode = viewport_post_aa_mode
            self.viewport_post_motion_blur_enabled = bool(
                self.settings.value(
                    'viewport/post_motion_blur_enabled',
                    self.viewport_post_motion_blur_enabled,
                    type=bool,
                )
            )
            self.viewport_post_motion_blur_strength = max(
                0.0,
                min(
                    1.0,
                    float(
                        self.settings.value(
                            'viewport/post_motion_blur_strength',
                            self.viewport_post_motion_blur_strength,
                            type=float,
                        )
                    ),
                ),
            )
            self.viewport_post_bloom_enabled = bool(
                self.settings.value('viewport/post_bloom_enabled', self.viewport_post_bloom_enabled, type=bool)
            )
            self.viewport_post_bloom_strength = max(
                0.0,
                min(
                    2.0,
                    float(self.settings.value('viewport/post_bloom_strength', self.viewport_post_bloom_strength, type=float)),
                ),
            )
            self.viewport_post_bloom_threshold = max(
                0.0,
                min(
                    2.0,
                    float(self.settings.value('viewport/post_bloom_threshold', self.viewport_post_bloom_threshold, type=float)),
                ),
            )
            self.viewport_post_bloom_radius = max(
                0.1,
                min(
                    8.0,
                    float(self.settings.value('viewport/post_bloom_radius', self.viewport_post_bloom_radius, type=float)),
                ),
            )
            self.viewport_post_vignette_enabled = bool(
                self.settings.value('viewport/post_vignette_enabled', self.viewport_post_vignette_enabled, type=bool)
            )
            self.viewport_post_vignette_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('viewport/post_vignette_strength', self.viewport_post_vignette_strength, type=float)),
                ),
            )
            self.viewport_post_grain_enabled = bool(
                self.settings.value('viewport/post_grain_enabled', self.viewport_post_grain_enabled, type=bool)
            )
            self.viewport_post_grain_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('viewport/post_grain_strength', self.viewport_post_grain_strength, type=float)),
                ),
            )
            self.viewport_post_ca_enabled = bool(
                self.settings.value('viewport/post_ca_enabled', self.viewport_post_ca_enabled, type=bool)
            )
            self.viewport_post_ca_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('viewport/post_ca_strength', self.viewport_post_ca_strength, type=float)),
                ),
            )
            self._apply_postfx_settings_to_widget(self.gl_widget)
            for widget in self.multi_view_widgets:
                self._apply_postfx_settings_to_widget(widget)
            self.viewport_bg_enabled = bool(
                self.settings.value('viewport/background_enabled', self.viewport_bg_enabled, type=bool)
            )
            self.viewport_bg_image_enabled = bool(
                self.settings.value(
                    'viewport/background_image_enabled',
                    self.viewport_bg_image_enabled,
                    type=bool,
                )
            )
            self.viewport_bg_color_mode = self._normalize_viewport_bg_color_mode(
                self.settings.value(
                    'viewport/background_color_mode',
                    self.viewport_bg_color_mode,
                    type=str,
                )
            )
            self.export_include_viewport_background = bool(
                self.settings.value(
                    'export/include_viewport_background',
                    self.export_include_viewport_background,
                    type=bool,
                )
            )
            self.viewport_bg_keep_aspect = bool(
                self.settings.value('viewport/background_keep_aspect', self.viewport_bg_keep_aspect, type=bool)
            )
            self.viewport_bg_zoom_fill = bool(
                self.settings.value('viewport/background_zoom_fill', self.viewport_bg_zoom_fill, type=bool)
            )
            self.viewport_bg_parallax_enabled = bool(
                self.settings.value(
                    'viewport/background_parallax_enabled',
                    self.viewport_bg_parallax_enabled,
                    type=bool,
                )
            )
            self.viewport_bg_parallax_zoom_strength = float(
                self.settings.value(
                    'viewport/background_parallax_zoom_strength',
                    self.viewport_bg_parallax_zoom_strength,
                    type=float,
                )
            )
            self.viewport_bg_parallax_pan_strength = float(
                self.settings.value(
                    'viewport/background_parallax_pan_strength',
                    self.viewport_bg_parallax_pan_strength,
                    type=float,
                )
            )
            self.viewport_bg_flip_h = bool(
                self.settings.value('viewport/background_flip_h', self.viewport_bg_flip_h, type=bool)
            )
            self.viewport_bg_flip_v = bool(
                self.settings.value('viewport/background_flip_v', self.viewport_bg_flip_v, type=bool)
            )
            self.viewport_bg_image_path = (
                self.settings.value('viewport/background_image_path', self.viewport_bg_image_path, type=str)
                or ''
            )
            self.control_panel.set_viewport_bg_enabled(self.viewport_bg_enabled)
            self.control_panel.set_viewport_bg_color_mode(self.viewport_bg_color_mode)
            self.control_panel.set_export_include_viewport_bg(self.export_include_viewport_background)
            self.control_panel.set_viewport_bg_keep_aspect(self.viewport_bg_keep_aspect)
            self.control_panel.set_viewport_bg_zoom_fill(self.viewport_bg_zoom_fill)
            self.control_panel.set_viewport_bg_parallax_enabled(self.viewport_bg_parallax_enabled)
            self.control_panel.set_viewport_bg_parallax_zoom_strength(self.viewport_bg_parallax_zoom_strength)
            self.control_panel.set_viewport_bg_parallax_pan_strength(self.viewport_bg_parallax_pan_strength)
            self.control_panel.set_viewport_bg_flips(self.viewport_bg_flip_h, self.viewport_bg_flip_v)
            self.control_panel.set_viewport_bg_image(
                self.viewport_bg_image_path,
                self.viewport_bg_image_enabled,
            )
            self.gl_widget.set_viewport_background_enabled(self.viewport_bg_enabled)
            self.gl_widget.set_viewport_background_color_mode(self.viewport_bg_color_mode)
            self.gl_widget.set_viewport_background_keep_aspect(self.viewport_bg_keep_aspect)
            self.gl_widget.set_viewport_background_zoom_fill(self.viewport_bg_zoom_fill)
            self.gl_widget.set_viewport_background_parallax_enabled(self.viewport_bg_parallax_enabled)
            self.gl_widget.set_viewport_background_parallax_zoom_sensitivity(
                self.viewport_bg_parallax_zoom_strength
            )
            self.gl_widget.set_viewport_background_parallax_pan_sensitivity(
                self.viewport_bg_parallax_pan_strength
            )
            self.gl_widget.set_viewport_background_flips(
                self.viewport_bg_flip_h,
                self.viewport_bg_flip_v,
            )
            self.gl_widget.set_viewport_background_image_enabled(self.viewport_bg_image_enabled)
            self.gl_widget.set_viewport_background_image_path(self.viewport_bg_image_path)
            self.gl_widget.set_shader_registry(self.shader_registry)
            self.control_panel.set_barebones_file_mode(self.export_settings.use_barebones_file_browser)
            self._apply_keybind_shortcuts()
            self.dof_path = self.settings.value('dof_path', self.dof_path, type=str)
            self._load_audio_preferences_from_storage()
            self._apply_audio_preferences_to_controls()
            self.metronome_enabled = bool(self.settings.value('metronome/enabled', self.metronome_enabled, type=bool))
            self.metronome_audible = bool(self.settings.value('metronome/audible', self.metronome_audible, type=bool))
            self.control_panel.set_metronome_checkbox(self.metronome_enabled)
            self.control_panel.set_metronome_audible_checkbox(self.metronome_audible)
            ts_num = self.settings.value('metronome/time_signature_numerator', self.time_signature_num, type=int)
            ts_denom = self.settings.value('metronome/time_signature_denom', self.time_signature_denom, type=int)
            self.time_signature_num, self.time_signature_denom = self._sanitize_time_signature(ts_num, ts_denom)
            self.control_panel.set_time_signature(self.time_signature_num, self.time_signature_denom)
            self._update_metronome_state()
            self.show_beat_grid = bool(self.settings.value('timeline/show_beat_grid', self.show_beat_grid, type=bool))
            self.allow_beat_edit = bool(self.settings.value('timeline/allow_beat_edit', self.allow_beat_edit, type=bool))
            self._refresh_timeline_beats(force_regenerate=True)
            self._load_diagnostics_settings()
            self._apply_anchor_logging_preferences()
            self.dof_particles_world_space = bool(
                self.settings.value('dof/particles_world_space', True, type=bool)
            )
            self.dof_particle_viewport_cap = max(
                0,
                min(1000000, int(self.settings.value('dof/viewport_particle_cap', 1000, type=int))),
            )
            self.dof_particle_distance_sensitivity = max(
                0.0,
                float(self.settings.value('dof/particle_distance_sensitivity', 0.5, type=float)),
            )
            self.dof_alpha_edge_smoothing_enabled = bool(
                self.settings.value('dof/alpha_edge_smoothing_enabled', False, type=bool)
            )
            self.dof_alpha_edge_smoothing_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('dof/alpha_edge_smoothing_strength', 0.5, type=float)),
                ),
            )
            dof_alpha_mode_value = (
                self.settings.value('dof/alpha_edge_smoothing_mode', 'normal', type=str) or 'normal'
            )
            dof_alpha_mode = str(dof_alpha_mode_value).strip().lower()
            if dof_alpha_mode not in {'normal', 'strong'}:
                dof_alpha_mode = 'normal'
            self.dof_alpha_edge_smoothing_mode = dof_alpha_mode
            dof_shader_mode_value = (
                self.settings.value('dof/sprite_shader_mode', 'auto', type=str) or 'auto'
            )
            dof_shader_mode = str(dof_shader_mode_value).strip().lower()
            if dof_shader_mode not in {
                'auto',
                'anim2d',
                'dawnoffire_unlit',
                'sprites_default',
                'unlit_transparent',
                'unlit_transparent_masked',
            }:
                dof_shader_mode = 'auto'
            self.dof_sprite_shader_mode = dof_shader_mode
            self.gl_widget.set_particle_world_space_override(self.dof_particles_world_space)
            self.gl_widget.set_particle_viewport_cap(self.dof_particle_viewport_cap)
            self.gl_widget.set_particle_distance_sensitivity(self.dof_particle_distance_sensitivity)
            self.gl_widget.set_dof_alpha_smoothing_enabled(self.dof_alpha_edge_smoothing_enabled)
            self.gl_widget.set_dof_alpha_smoothing_strength(self.dof_alpha_edge_smoothing_strength)
            self.gl_widget.set_dof_alpha_smoothing_mode(self.dof_alpha_edge_smoothing_mode)
            self.gl_widget.set_dof_sprite_shader_mode(self.dof_sprite_shader_mode)
            for widget in self.multi_view_widgets:
                widget.set_particle_viewport_cap(self.dof_particle_viewport_cap)
                widget.set_particle_distance_sensitivity(self.dof_particle_distance_sensitivity)
                widget.set_dof_alpha_smoothing_enabled(self.dof_alpha_edge_smoothing_enabled)
                widget.set_dof_alpha_smoothing_strength(self.dof_alpha_edge_smoothing_strength)
                widget.set_dof_alpha_smoothing_mode(self.dof_alpha_edge_smoothing_mode)
                widget.set_dof_sprite_shader_mode(self.dof_sprite_shader_mode)
    
    def export_as_gif(self):
        """Export animation as animated GIF"""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "Error", "No animation loaded")
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export GIF", "", "GIF Animation (*.gif)"
        )
        
        if not filename:
            return
        
        # Ensure .gif extension
        if not filename.lower().endswith('.gif'):
            filename += '.gif'
        
        try:
            # Get settings from export_settings
            gif_fps = self.export_settings.gif_fps
            gif_colors = self.export_settings.gif_colors
            gif_scale = self.export_settings.gif_scale / 100.0
            gif_dither = self.export_settings.gif_dither
            gif_optimize = self.export_settings.gif_optimize
            gif_loop = self.export_settings.gif_loop
            
            # Get animation parameters
            real_duration = self._get_export_real_duration()
            total_frames = int(real_duration * gif_fps)
            
            self.log_widget.log(f"GIF Export: {total_frames} frames at {gif_fps} FPS", "INFO")
            self.log_widget.log(f"Settings: {gif_colors} colors, {int(gif_scale*100)}% scale, dither={gif_dither}", "INFO")
            
            if total_frames <= 0:
                QMessageBox.warning(self, "Error", "Animation has no frames to export")
                return
            
            base_width, base_height, camera_override, render_scale_override, apply_centering = (
                self._compute_export_render_plan(
                    gif_fps,
                    fallback_width=self.gl_widget.width(),
                    fallback_height=self.gl_widget.height(),
                    use_full_res=False,
                )
            )
            output_width = int(base_width * gif_scale)
            output_height = int(base_height * gif_scale)
            
            # Ensure even dimensions
            output_width = output_width if output_width % 2 == 0 else output_width + 1
            output_height = output_height if output_height % 2 == 0 else output_height + 1
            
            self.log_widget.log(f"Output dimensions: {output_width}x{output_height}", "INFO")
            
            # Store original state
            original_time = self.gl_widget.player.current_time
            original_playing = self.gl_widget.player.playing
            self._set_player_playing(False)
            
            # Create progress dialog
            progress = QProgressDialog("Exporting GIF...", "Cancel", 0, total_frames + 1, self)
            progress.setWindowTitle("GIF Export Progress")
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.show()
            
            # Render frames
            frames = []
            was_canceled = False
            background_color = self._active_background_color()
            include_viewport_bg = bool(self.export_include_viewport_background)
            
            for frame_num in range(total_frames):
                if progress.wasCanceled():
                    self.log_widget.log("Export cancelled by user", "WARNING")
                    was_canceled = True
                    break
                
                # Set animation time
                frame_time = self._get_export_frame_time(frame_num, gif_fps)
                self.gl_widget.player.current_time = frame_time
                
                # Render frame at base size
                image = self.render_frame_to_image(
                    base_width,
                    base_height,
                    camera_override=camera_override,
                    render_scale_override=render_scale_override,
                    apply_centering=apply_centering,
                    background_color=background_color,
                    include_viewport_background=include_viewport_bg,
                    motion_blur_frame_dt=1.0 / max(1e-6, float(gif_fps)),
                )
                
                if image:
                    # Scale if needed
                    if gif_scale != 1.0:
                        image = image.resize((output_width, output_height), Image.Resampling.LANCZOS)
                    
                    # Convert RGBA to palette mode with proper transparency handling
                    # GIF only supports 1-bit transparency (fully transparent or fully opaque)
                    
                    # Get alpha channel
                    alpha = image.split()[3]
                    
                    # Create a mask for transparent pixels (alpha < 128 = transparent)
                    # This threshold can be adjusted - 128 is a good middle ground
                    transparency_threshold = 128
                    
                    # Convert to RGB first (drop alpha temporarily)
                    rgb_image = image.convert('RGB')
                    
                    # Convert to palette mode
                    if gif_dither:
                        palette_image = rgb_image.convert('P', palette=Image.Palette.ADAPTIVE, 
                                                         colors=gif_colors - 1,  # Reserve one color for transparency
                                                         dither=Image.Dither.FLOYDSTEINBERG)
                    else:
                        palette_image = rgb_image.convert('P', palette=Image.Palette.ADAPTIVE, 
                                                         colors=gif_colors - 1,
                                                         dither=Image.Dither.NONE)
                    
                    # Create a new palette image with transparency
                    # We need to set transparent pixels to a specific palette index
                    # First, find or create a transparent color index
                    
                    # Get the palette
                    palette = palette_image.getpalette()
                    
                    # Add a transparent color at the end of the palette (we reserved space)
                    # Use a color that's unlikely to appear in the image (magenta)
                    transparent_index = gif_colors - 1
                    if palette:
                        # Extend palette if needed
                        while len(palette) < transparent_index * 3 + 3:
                            palette.extend([0, 0, 0])
                        palette[transparent_index * 3] = 255      # R
                        palette[transparent_index * 3 + 1] = 0    # G  
                        palette[transparent_index * 3 + 2] = 255  # B (magenta)
                        palette_image.putpalette(palette)
                    
                    # Now apply transparency mask
                    # Convert palette image to array for manipulation
                    palette_array = np.array(palette_image)
                    alpha_array = np.array(alpha)
                    
                    # Set transparent pixels to the transparent index
                    palette_array[alpha_array < transparency_threshold] = transparent_index
                    
                    # Create new palette image from array
                    final_image = Image.fromarray(palette_array, mode='P')
                    final_image.putpalette(palette)
                    
                    # Store the transparent index for this frame
                    final_image.info['transparency'] = transparent_index
                    
                    frames.append(final_image)
                else:
                    self.log_widget.log(f"Failed to render frame {frame_num}", "WARNING")
                
                progress.setValue(frame_num + 1)
                progress.setLabelText(f"Rendering frame {frame_num + 1} of {total_frames}...")
                
                # Process events
                from PyQt6.QtWidgets import QApplication
                QApplication.processEvents()
            
            if was_canceled or len(frames) == 0:
                self.log_widget.log(f"Export aborted. Frames rendered: {len(frames)}", "WARNING")
                self.gl_widget.player.current_time = original_time
                self._set_player_playing(original_playing)
                progress.close()
                return
            
            progress.setLabelText("Encoding GIF...")
            progress.setValue(total_frames)
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
            
            # Calculate frame duration in milliseconds
            frame_duration = int(1000 / gif_fps)
            
            # Save as GIF
            self.log_widget.log(f"Saving GIF with {len(frames)} frames...", "INFO")
            
            # Get the transparent index from the first frame
            transparent_index = gif_colors - 1
            
            # Use the first frame as base, append the rest
            frames[0].save(
                filename,
                save_all=True,
                append_images=frames[1:] if len(frames) > 1 else [],
                duration=frame_duration,
                loop=gif_loop,
                optimize=gif_optimize,
                transparency=transparent_index,
                disposal=2  # Restore to background between frames
            )
            
            progress.close()
            
            # Get file size
            file_size = os.path.getsize(filename)
            if file_size > 1024 * 1024:
                size_str = f"{file_size / (1024*1024):.2f} MB"
            else:
                size_str = f"{file_size / 1024:.1f} KB"
            
            self.log_widget.log(f"GIF exported to: {filename} ({size_str})", "SUCCESS")
            
            # Restore original state
            self.gl_widget.player.current_time = original_time
            self._set_player_playing(original_playing)
            self.gl_widget.update()
            
            QMessageBox.information(
                self, "Export Complete",
                f"GIF exported successfully!\n\n"
                f"File: {filename}\n"
                f"Size: {size_str}\n"
                f"Frames: {len(frames)}\n"
                f"Dimensions: {output_width}x{output_height}"
            )
            
        except Exception as e:
            self.log_widget.log(f"Error exporting GIF: {e}", "ERROR")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "Export Error", f"Failed to export GIF: {e}")
    
    def show_credits(self):
        """Show credits and acknowledgments dialog"""
        credits_dialog = QDialog(self)
        credits_dialog.setWindowTitle("Credits & Acknowledgments")
        credits_dialog.setMinimumWidth(450)
        credits_dialog.setMinimumHeight(400)
        
        layout = QVBoxLayout(credits_dialog)
        
        # Title with glow effect
        title_label = QLabel("MSM Animation Viewer")
        title_label.setStyleSheet("font-size: 18pt; font-weight: bold; color: white;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # Add white glow effect
        glow_effect = QGraphicsDropShadowEffect()
        glow_effect.setBlurRadius(20)
        glow_effect.setColor(QColor(255, 255, 255))
        glow_effect.setOffset(0, 0)
        title_label.setGraphicsEffect(glow_effect)
        
        layout.addWidget(title_label)
        
        # Created by
        created_label = QLabel("Created by <b>LennyFaze</b> (MSM Green Screens)")
        created_label.setStyleSheet("font-size: 10pt; color: #aaa;")
        created_label.setTextFormat(Qt.TextFormat.RichText)
        created_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(created_label)
        
        # Subtitle
        subtitle_label = QLabel(f"Credits & Acknowledgments — Build {self.build_version}")
        subtitle_label.setStyleSheet("font-size: 12pt; color: #666;")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle_label)
        
        layout.addSpacing(20)
        
        # Credits content
        credits_text = """
<div style="font-size: 10pt;">

<p style="font-weight: bold; color: #4a90d9; font-size: 11pt;">Special Thanks</p>

<p><b>iestyn129</b><br/>
<i>For the bin2json parsing script that made this project possible,<br/>
alpha testing, and valuable feedback</i></p>

<p><b>wubbox64</b><br/>
<i>Alpha testing and valuable feedback</i></p>

<hr style="border: 1px solid #ddd; margin: 15px 0;"/>

<p><b>The MSM Community</b><br/>
<i>For their continued support and enthusiasm for this project!</i></p>

<hr style="border: 1px solid #ddd; margin: 15px 0;"/>

<p style="font-weight: bold; color: #4a90d9; font-size: 11pt;">Legal</p>

<p style="color: #888; font-size: 9pt;">
<b>My Singing Monsters</b> is a registered trademark of<br/>
<b>Big Blue Bubble Inc.</b><br/><br/>
This tool is a fan-made project and is not affiliated with,<br/>
endorsed by, or connected to Big Blue Bubble Inc.<br/><br/>
All game assets and content are owned by Big Blue Bubble Inc.
</p>

</div>
"""
        
        credits_label = QLabel(credits_text)
        credits_label.setWordWrap(True)
        credits_label.setTextFormat(Qt.TextFormat.RichText)
        credits_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(credits_label)
        
        layout.addStretch()
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(credits_dialog.accept)
        close_btn.setStyleSheet("padding: 8px 30px;")
        
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        credits_dialog.exec()
    
    def load_settings(self):
        """Load saved settings"""
        self.control_panel.set_dof_search_mode(self.dof_search_enabled)
        sprite_filter = self.settings.value("viewport/sprite_filter", "bilinear", type=str)
        if hasattr(self, "gl_widget"):
            self.gl_widget.set_sprite_filter_mode(sprite_filter)
            sprite_filter_strength = self.settings.value("viewport/sprite_filter_strength", 1.0, type=float)
            self.gl_widget.set_sprite_filter_strength(sprite_filter_strength)
            self.viewport_post_aa_enabled = bool(
                self.settings.value('viewport/post_aa_enabled', self.viewport_post_aa_enabled, type=bool)
            )
            self.viewport_post_aa_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('viewport/post_aa_strength', self.viewport_post_aa_strength, type=float)),
                ),
            )
            viewport_post_aa_mode = (
                self.settings.value('viewport/post_aa_mode', self.viewport_post_aa_mode, type=str) or self.viewport_post_aa_mode
            )
            viewport_post_aa_mode = str(viewport_post_aa_mode).strip().lower()
            if viewport_post_aa_mode not in {'fxaa', 'smaa'}:
                viewport_post_aa_mode = 'fxaa'
            self.viewport_post_aa_mode = viewport_post_aa_mode
            self.viewport_post_motion_blur_enabled = bool(
                self.settings.value(
                    'viewport/post_motion_blur_enabled',
                    self.viewport_post_motion_blur_enabled,
                    type=bool,
                )
            )
            self.viewport_post_motion_blur_strength = max(
                0.0,
                min(
                    1.0,
                    float(
                        self.settings.value(
                            'viewport/post_motion_blur_strength',
                            self.viewport_post_motion_blur_strength,
                            type=float,
                        )
                    ),
                ),
            )
            self.viewport_post_bloom_enabled = bool(
                self.settings.value('viewport/post_bloom_enabled', self.viewport_post_bloom_enabled, type=bool)
            )
            self.viewport_post_bloom_strength = max(
                0.0,
                min(
                    2.0,
                    float(self.settings.value('viewport/post_bloom_strength', self.viewport_post_bloom_strength, type=float)),
                ),
            )
            self.viewport_post_bloom_threshold = max(
                0.0,
                min(
                    2.0,
                    float(self.settings.value('viewport/post_bloom_threshold', self.viewport_post_bloom_threshold, type=float)),
                ),
            )
            self.viewport_post_bloom_radius = max(
                0.1,
                min(
                    8.0,
                    float(self.settings.value('viewport/post_bloom_radius', self.viewport_post_bloom_radius, type=float)),
                ),
            )
            self.viewport_post_vignette_enabled = bool(
                self.settings.value('viewport/post_vignette_enabled', self.viewport_post_vignette_enabled, type=bool)
            )
            self.viewport_post_vignette_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('viewport/post_vignette_strength', self.viewport_post_vignette_strength, type=float)),
                ),
            )
            self.viewport_post_grain_enabled = bool(
                self.settings.value('viewport/post_grain_enabled', self.viewport_post_grain_enabled, type=bool)
            )
            self.viewport_post_grain_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('viewport/post_grain_strength', self.viewport_post_grain_strength, type=float)),
                ),
            )
            self.viewport_post_ca_enabled = bool(
                self.settings.value('viewport/post_ca_enabled', self.viewport_post_ca_enabled, type=bool)
            )
            self.viewport_post_ca_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('viewport/post_ca_strength', self.viewport_post_ca_strength, type=float)),
                ),
            )
            self._apply_postfx_settings_to_widget(self.gl_widget)
            self.dof_alpha_edge_smoothing_enabled = bool(
                self.settings.value('dof/alpha_edge_smoothing_enabled', False, type=bool)
            )
            self.dof_alpha_edge_smoothing_strength = max(
                0.0,
                min(
                    1.0,
                    float(self.settings.value('dof/alpha_edge_smoothing_strength', 0.5, type=float)),
                ),
            )
            dof_alpha_mode_value = (
                self.settings.value('dof/alpha_edge_smoothing_mode', 'normal', type=str) or 'normal'
            )
            dof_alpha_mode = str(dof_alpha_mode_value).strip().lower()
            if dof_alpha_mode not in {'normal', 'strong'}:
                dof_alpha_mode = 'normal'
            self.dof_alpha_edge_smoothing_mode = dof_alpha_mode
            dof_shader_mode_value = (
                self.settings.value('dof/sprite_shader_mode', 'auto', type=str) or 'auto'
            )
            dof_shader_mode = str(dof_shader_mode_value).strip().lower()
            if dof_shader_mode not in {
                'auto',
                'anim2d',
                'dawnoffire_unlit',
                'sprites_default',
                'unlit_transparent',
                'unlit_transparent_masked',
            }:
                dof_shader_mode = 'auto'
            self.dof_sprite_shader_mode = dof_shader_mode
            self.gl_widget.set_dof_alpha_smoothing_enabled(self.dof_alpha_edge_smoothing_enabled)
            self.gl_widget.set_dof_alpha_smoothing_strength(self.dof_alpha_edge_smoothing_strength)
            self.gl_widget.set_dof_alpha_smoothing_mode(self.dof_alpha_edge_smoothing_mode)
            self.gl_widget.set_dof_sprite_shader_mode(self.dof_sprite_shader_mode)
            for widget in self.multi_view_widgets:
                self._apply_postfx_settings_to_widget(widget)
                widget.set_dof_alpha_smoothing_enabled(self.dof_alpha_edge_smoothing_enabled)
                widget.set_dof_alpha_smoothing_strength(self.dof_alpha_edge_smoothing_strength)
                widget.set_dof_alpha_smoothing_mode(self.dof_alpha_edge_smoothing_mode)
                widget.set_dof_sprite_shader_mode(self.dof_sprite_shader_mode)
        self._update_path_label()
        if self.game_path or self.downloads_path:
            self.build_audio_library()
            self.refresh_file_list()
        if self.dof_path:
            self.build_dof_audio_library()
        if self.dof_search_enabled and self.dof_path:
            self.refresh_dof_file_list()
        self._apply_control_panel_preferences()
        self._apply_ui_preferences()

    def _on_control_panel_dof_include_mesh_xml_toggled(self, enabled: bool):
        self.settings.setValue("ui/control_panel/dof_include_mesh_xml", bool(enabled))
    
    def on_layer_selection_changed(self, layer_ids: List[int], last_layer_id: int, added: bool):
        """Handle layer selection toggles from the layer panel."""
        self.selected_layer_ids = set(layer_ids)
        if not self.selected_layer_ids:
            self.primary_selected_layer_id = None
        else:
            if added and last_layer_id in self.selected_layer_ids:
                self.primary_selected_layer_id = last_layer_id
            elif self.primary_selected_layer_id not in self.selected_layer_ids:
                self.primary_selected_layer_id = next(iter(self.selected_layer_ids))
        self.layer_panel.set_selection_state(self.selected_layer_ids)
        self.apply_selection_state()
        if self.selected_layer_ids:
            self.selected_attachment_id = None
            self.layer_panel.set_attachment_selection(None)
            self.gl_widget.set_attachment_selection(None)
        self._refresh_timeline_keyframes()
        self._update_nudge_controls_state()
        self.update_layer_readout()

    def on_attachment_selection_changed(self, attachment_id: int):
        """Handle attachment selection from the layer panel."""
        if self.selected_layer_ids:
            self.selected_layer_ids.clear()
            self.primary_selected_layer_id = None
            self.selection_lock_enabled = False
            self.layer_panel.set_selection_state(self.selected_layer_ids)
            self.apply_selection_state()
            self._refresh_timeline_keyframes()
            self._update_nudge_controls_state()
            self.update_layer_readout()
        self.selected_attachment_id = attachment_id
        self.gl_widget.set_attachment_selection(attachment_id)

    def on_attachment_visibility_changed(self, attachment_id: int, visible: bool):
        """Show/hide a costume attachment from the layer panel."""
        self.gl_widget.set_attachment_visibility(attachment_id, visible)
        if not visible and self.selected_attachment_id == attachment_id:
            self.selected_attachment_id = None
            self.layer_panel.set_attachment_selection(None)
            self.gl_widget.set_attachment_selection(None)
    
    def on_selection_lock_toggled(self, locked: bool):
        """Handle lock/unlock requests from the layer panel."""
        if locked and not self.selected_layer_ids:
            locked = False
        self.selection_lock_enabled = locked
        self.apply_selection_state()
    
    def on_layer_selection_cleared(self):
        """Handle deselect-all events from the layer panel."""
        if not self.selected_layer_ids and not self.selection_lock_enabled:
            return
        self.selected_layer_ids.clear()
        self.primary_selected_layer_id = None
        self.selection_lock_enabled = False
        self.apply_selection_state()
        self._refresh_timeline_keyframes()
        self._update_nudge_controls_state()
        self.update_layer_readout()
    
    def apply_selection_state(self):
        """Push current selection info to the GL widget."""
        self.gl_widget.set_selection_state(
            self.selected_layer_ids,
            self.primary_selected_layer_id,
            self.selection_lock_enabled
        )

    def on_layer_color_changed(self, r: int, g: int, b: int, a: int):
        """Apply a tint override to all selected layers."""
        animation = self.gl_widget.player.animation
        if not animation or not self.selected_layer_ids:
            return

        def _clamp(value: int) -> int:
            return max(0, min(255, int(value)))

        rgba = tuple(_clamp(v) for v in (r, g, b, a))
        tint = tuple(channel / 255.0 for channel in rgba)
        reset = rgba == (255, 255, 255, 255)
        updated = 0

        for layer in animation.layers:
            if layer.layer_id not in self.selected_layer_ids:
                continue
            layer.color_tint = None if reset else tint
            updated += 1
            if hasattr(self, "diagnostics") and self.diagnostics:
                if reset:
                    self.diagnostics.log_color("Cleared tint override", layer_id=layer.layer_id)
                else:
                    hex_value = f"#{rgba[0]:02X}{rgba[1]:02X}{rgba[2]:02X}{rgba[3]:02X}"
                    self.diagnostics.log_color(
                        f"Applied tint {hex_value}", layer_id=layer.layer_id
                    )

        if updated:
            self.gl_widget.update()
            self.layer_panel.refresh_color_editor()
            if reset:
                self.log_widget.log(f"Cleared tint on {updated} layer(s)", "INFO")
            else:
                self.log_widget.log(
                    f"Set tint for {updated} layer(s) to "
                    f"{rgba[0]:02d},{rgba[1]:02d},{rgba[2]:02d},{rgba[3]:02d}",
                    "INFO"
                )

    def on_layer_color_reset(self):
        """Remove tint overrides for selected layers."""
        self.on_layer_color_changed(255, 255, 255, 255)

    def on_layer_color_keyframe_requested(self, r: int, g: int, b: int, a: int):
        """Bake the current tint overrides into RGB keyframes."""
        player = getattr(self.gl_widget, "player", None)
        animation = player.animation if player else None
        if not animation:
            self.log_widget.log("Load an animation before keyframing tints.", "WARNING")
            return

        active_lane = self.timeline.get_active_lane() if hasattr(self, "timeline") else None
        use_global_lane = bool(active_lane and active_lane.scope == "global")
        lane_index = active_lane.lane_index if active_lane and active_lane.scope == "layer" else 0

        if self.selected_layer_ids:
            target_layer_ids = sorted(self.selected_layer_ids)
        elif self.primary_selected_layer_id is not None:
            target_layer_ids = [self.primary_selected_layer_id]
        else:
            self.log_widget.log("Select at least one layer to keyframe tint.", "WARNING")
            return

        marker_times = (
            sorted({float(time) for _lane_key, time in self._selected_marker_refs})
            if self._selected_marker_refs
            else []
        )
        if not marker_times:
            if not player:
                self.log_widget.log("Unable to determine timeline position for tint.", "WARNING")
                return
            marker_times = [float(player.current_time)]

        def _clamp(value: int) -> int:
            return max(0, min(255, int(value)))

        rgba = (_clamp(r), _clamp(g), _clamp(b), _clamp(a))

        created_frames = 0
        updated_frames = 0

        if use_global_lane and active_lane is not None:
            lane_key = TimelineLaneKey("global", -1, active_lane.lane_index)
            self._begin_keyframe_action([], include_global=True)
            for time_value in marker_times:
                keyframe, created = self._ensure_keyframe_at_time_in_lane(lane_key, time_value)
                if created:
                    created_frames += 1
                updated = created
                if (keyframe.r, keyframe.g, keyframe.b, keyframe.a) != rgba:
                    keyframe.r, keyframe.g, keyframe.b, keyframe.a = rgba
                    updated = True
                if keyframe.immediate_rgb != 0:
                    keyframe.immediate_rgb = 0
                    updated = True
                if updated:
                    updated_frames += 1
            for layer_id in target_layer_ids:
                layer = self.gl_widget.get_layer_by_id(layer_id)
                if layer:
                    layer.color_tint = None
            if updated_frames == 0:
                self._pending_keyframe_action = None
                self.log_widget.log("No tint changes were applied; nothing to bake.", "INFO")
                return
            self._finalize_keyframe_action("tint keyframe")
            self.layer_panel.refresh_color_editor()
            self._refresh_timeline_keyframes()
            self.gl_widget.update()
            message = f"Baked tint into {updated_frames} keyframe(s) on the global lane"
            if created_frames:
                message += f" ({created_frames} new keyframe(s))"
            self.log_widget.log(message, "SUCCESS")
            return

        self._begin_keyframe_action(target_layer_ids)
        touched_layers: Set[int] = set()
        for layer_id in target_layer_ids:
            layer = self.gl_widget.get_layer_by_id(layer_id)
            if not layer:
                continue
            if lane_index > 0:
                self._ensure_layer_lane_index(layer, lane_index)
            lane_key = TimelineLaneKey("layer", layer.layer_id, lane_index)
            layer_changed = False
            for time_value in marker_times:
                keyframe, created = self._ensure_keyframe_at_time_in_lane(lane_key, time_value)
                if created:
                    created_frames += 1
                updated = created
                if (keyframe.r, keyframe.g, keyframe.b, keyframe.a) != rgba:
                    keyframe.r, keyframe.g, keyframe.b, keyframe.a = rgba
                    updated = True
                if keyframe.immediate_rgb != 0:
                    keyframe.immediate_rgb = 0
                    updated = True
                if updated:
                    updated_frames += 1
                    layer_changed = True
            if layer_changed:
                layer.color_tint = None
                touched_layers.add(layer.layer_id)
                if lane_index == 0:
                    self._sync_layer_source_frames(layer)

        if not touched_layers:
            self._pending_keyframe_action = None
            self.log_widget.log("No tint changes were applied; nothing to bake.", "INFO")
            return

        self._finalize_keyframe_action("tint keyframe")
        self.layer_panel.refresh_color_editor()
        self._refresh_timeline_keyframes()
        self.gl_widget.update()
        total_layers = len(touched_layers)
        message = (
            f"Baked tint into {updated_frames} keyframe(s) across {total_layers} layer(s)"
        )
        if created_frames:
            message += f" ({created_frames} new keyframe(s))"
        self.log_widget.log(message, "SUCCESS")

    def save_layer_offsets(self):
        """Save current layer offsets/rotations to a text file."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before saving offsets.")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save Layer Offsets",
            "",
            "Layer Preset (*.txt);;All Files (*.*)"
        )
        if not filename:
            return
        if not filename.lower().endswith(".txt"):
            filename += ".txt"

        animation = self.gl_widget.player.animation
        data = {
            "animation": animation.name,
            "saved_at": datetime.now().isoformat(),
            "layers": []
        }
        for layer in animation.layers:
            offset_x, offset_y = self.gl_widget.layer_offsets.get(layer.layer_id, (0.0, 0.0))
            rotation = self.gl_widget.layer_rotations.get(layer.layer_id, 0.0)
            scale_x, scale_y = self.gl_widget.layer_scale_offsets.get(layer.layer_id, (1.0, 1.0))
            data["layers"].append({
                "id": layer.layer_id,
                "name": layer.name,
                "offset_x": offset_x,
                "offset_y": offset_y,
                "rotation": rotation,
                "scale_x": scale_x,
                "scale_y": scale_y
            })
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.log_widget.log(f"Saved {len(data['layers'])} layer offsets to {filename}", "SUCCESS")
        except Exception as exc:
            self.log_widget.log(f"Failed to save offsets: {exc}", "ERROR")
            QMessageBox.warning(self, "Save Error", f"Could not save offsets:\n{exc}")

    def load_layer_offsets(self):
        """Load previously saved layer offsets and apply them to the animation."""
        if not self.gl_widget.player.animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before applying offsets.")
            return

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Load Layer Offsets",
            "",
            "Layer Preset (*.txt);;All Files (*.*)"
        )
        if not filename:
            return

        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            self.log_widget.log(f"Failed to read offsets: {exc}", "ERROR")
            QMessageBox.warning(self, "Load Error", f"Could not read offsets:\n{exc}")
            return

        entries = data.get("layers")
        if not isinstance(entries, list):
            QMessageBox.warning(self, "Invalid File", "Selected file does not contain layer offset data.")
            return

        current_animation = self.gl_widget.player.animation
        if data.get("animation") and data["animation"] != current_animation.name:
            self.log_widget.log("Preset animation mismatch; applying anyway", "WARNING")

        layer_map = {layer.layer_id: layer for layer in current_animation.layers}

        self.gl_widget.layer_offsets.clear()
        self.gl_widget.layer_rotations.clear()

        applied = 0
        for entry in entries:
            layer_id = entry.get("id")
            if layer_id not in layer_map:
                continue
            offset_x = float(entry.get("offset_x", 0.0))
            offset_y = float(entry.get("offset_y", 0.0))
            rotation = float(entry.get("rotation", 0.0))
            scale_x = float(entry.get("scale_x", 1.0))
            scale_y = float(entry.get("scale_y", 1.0))

            if abs(offset_x) > 1e-6 or abs(offset_y) > 1e-6:
                self.gl_widget.layer_offsets[layer_id] = (offset_x, offset_y)
            if abs(rotation) > 1e-6:
                self.gl_widget.layer_rotations[layer_id] = rotation
            if abs(scale_x - 1.0) > 1e-6 or abs(scale_y - 1.0) > 1e-6:
                self.gl_widget.layer_scale_offsets[layer_id] = (scale_x, scale_y)
            applied += 1

        self.update_offset_display()
        self.gl_widget.update()
        self.log_widget.log(f"Applied offsets to {applied} layers from {os.path.basename(filename)}", "SUCCESS")
    
    def reset_sprite_offsets(self):
        """Reset all sprite offsets to default"""
        self.gl_widget.reset_layer_offsets()
        self.update_offset_display()
        self.log_widget.log("Reset all sprite offsets to default", "SUCCESS")
    
    def update_offset_display(self):
        """Update the offset display with current values"""
        self.control_panel.update_offset_display(
            self.gl_widget.layer_offsets,
            self.gl_widget.get_layer_by_id,
            self.gl_widget.layer_rotations,
            self.gl_widget.layer_scale_offsets
        )
        self.update_layer_readout()
        
        # Start a timer to update offsets periodically
        if not hasattr(self, 'offset_update_timer'):
            self.offset_update_timer = QTimer(self)
            self.offset_update_timer.timeout.connect(self.update_offset_display)
            self.offset_update_timer.start(100)  # Update every 100ms

    def _build_layer_readout_info(
        self,
        layer: LayerData,
        anim_time: float,
        world_states: Optional[Dict[int, Dict]] = None,
    ) -> Dict[str, str]:
        """Build a layer readout info dict for a specific layer/time."""
        local_state: Optional[Dict[str, Any]] = None
        try:
            local_state = self.gl_widget.player.get_layer_state(layer, anim_time)
        except Exception:
            local_state = None

        world_state: Optional[Dict[str, Any]] = None
        if isinstance(world_states, dict):
            world_state = world_states.get(layer.layer_id)
        if world_state is None:
            last_states = getattr(self.gl_widget, "_last_layer_world_states", None)
            if isinstance(last_states, dict):
                world_state = last_states.get(layer.layer_id)
        if world_state is None:
            try:
                world_state = self.gl_widget._build_layer_world_states(anim_time).get(layer.layer_id)
            except Exception:
                world_state = None

        def _fmt(value: Optional[float], digits: int = 2) -> str:
            if value is None:
                return "-"
            return f"{value:.{digits}f}"

        def _fmt_pair(x: Optional[float], y: Optional[float], digits: int = 2) -> str:
            if x is None or y is None:
                return "-"
            return f"{x:.{digits}f}, {y:.{digits}f}"

        local_pos = None
        local_rot = None
        local_scale_x = None
        local_scale_y = None
        local_opacity = None
        local_sprite = None
        if local_state:
            local_pos = (local_state.get("pos_x"), local_state.get("pos_y"))
            local_rot = local_state.get("rotation")
            local_scale_x = local_state.get("scale_x")
            local_scale_y = local_state.get("scale_y")
            local_opacity = local_state.get("opacity")
            local_sprite = local_state.get("sprite_name")

        world_pos = None
        world_anchor = None
        world_rot = None
        world_scale_x = None
        world_scale_y = None
        world_opacity = None
        world_sprite = None
        if world_state:
            world_pos = (world_state.get("tx"), world_state.get("ty"))
            world_anchor = (world_state.get("anchor_world_x"), world_state.get("anchor_world_y"))
            world_rot = world_state.get("rotation")
            world_scale_x = world_state.get("scale_x")
            world_scale_y = world_state.get("scale_y")
            world_opacity = world_state.get("world_opacity")
            world_sprite = world_state.get("sprite_name")

        offset_x, offset_y = self.gl_widget.layer_offsets.get(layer.layer_id, (0.0, 0.0))

        return {
            "layer": f"{layer.name} (id {layer.layer_id})",
            "sprite": world_sprite or local_sprite or "-",
            "local_pos": _fmt_pair(
                local_pos[0] if local_pos else None,
                local_pos[1] if local_pos else None,
                2,
            ),
            "world_pos": _fmt_pair(
                world_pos[0] if world_pos else None,
                world_pos[1] if world_pos else None,
                2,
            ),
            "anchor_world": _fmt_pair(
                world_anchor[0] if world_anchor else None,
                world_anchor[1] if world_anchor else None,
                2,
            ),
            "rotation": _fmt(world_rot if world_rot is not None else local_rot, 2) + " deg",
            "scale": _fmt_pair(
                world_scale_x if world_scale_x is not None else local_scale_x,
                world_scale_y if world_scale_y is not None else local_scale_y,
                3,
            ),
            "opacity": (
                _fmt((world_opacity or 0.0) * 100.0, 1) + "%"
                if world_opacity is not None
                else (_fmt(local_opacity, 1) + "%" if local_opacity is not None else "-")
            ),
            "user_offset": _fmt_pair(offset_x, offset_y, 2),
        }

    @staticmethod
    def _format_layer_readout_text(info: Dict[str, str]) -> str:
        """Render a layer readout info dict into a text block."""
        return (
            f"Layer: {info.get('layer', '-')}\n"
            f"Sprite: {info.get('sprite', '-')}\n"
            f"Local Pos: {info.get('local_pos', '-')}\n"
            f"World Pos: {info.get('world_pos', '-')}\n"
            f"Anchor (World): {info.get('anchor_world', '-')}\n"
            f"Rotation: {info.get('rotation', '-')}\n"
            f"Scale: {info.get('scale', '-')}\n"
            f"Opacity: {info.get('opacity', '-')}\n"
            f"User Offset: {info.get('user_offset', '-')}"
        )

    def update_layer_readout(self):
        """Update the layer panel readout for the primary selected layer."""
        if not hasattr(self, "layer_panel"):
            return
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation or self.primary_selected_layer_id is None:
            self.layer_panel.update_layer_readout(None)
            return

        layer = self.gl_widget.get_layer_by_id(self.primary_selected_layer_id)
        if not layer:
            self.layer_panel.update_layer_readout(None)
            return

        anim_time = self.gl_widget.player.current_time
        last_states = getattr(self.gl_widget, "_last_layer_world_states", None)
        info = self._build_layer_readout_info(layer, anim_time, last_states)
        self.layer_panel.update_layer_readout(info)

    def export_layer_readouts(self):
        """Export readouts for all visible layers at the current time."""
        animation = getattr(self.gl_widget.player, "animation", None)
        if not animation:
            QMessageBox.warning(self, "No Animation", "Load an animation before exporting readouts.")
            return

        anim_time = self.gl_widget.player.current_time
        try:
            world_states = self.gl_widget._build_layer_world_states(anim_time)
        except Exception:
            world_states = {}

        layers = [layer for layer in animation.layers if layer.visible]
        if not layers:
            QMessageBox.warning(self, "No Layers", "No visible layers found to export.")
            return

        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Layer Readouts",
            "",
            "Text Files (*.txt);;All Files (*.*)"
        )
        if not filename:
            return
        if not filename.lower().endswith(".txt"):
            filename += ".txt"

        header = [
            f"Animation: {animation.name}",
            f"Time: {anim_time:.8f}",
            f"Visible layers: {len(layers)}",
            "",
        ]

        blocks = []
        for layer in layers:
            info = self._build_layer_readout_info(layer, anim_time, world_states)
            blocks.append(self._format_layer_readout_text(info))

        output = "\n".join(header) + "\n\n".join(blocks)
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(output)
            self.log_widget.log(f"Exported {len(layers)} layer readouts to {filename}", "SUCCESS")
        except Exception as exc:
            self.log_widget.log(f"Failed to export layer readouts: {exc}", "ERROR")
            QMessageBox.warning(self, "Export Error", f"Could not export readouts:\n{exc}")

    # ------------------------------------------------------------------ #
    # Pixel Nudging Handlers
    # ------------------------------------------------------------------ #
    def on_nudge_x(self, delta: float):
        """Nudge selected layers by delta pixels in X direction."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_x, old_y = self.gl_widget.layer_offsets.get(layer_id, (0.0, 0.0))
            self.gl_widget.layer_offsets[layer_id] = (old_x + delta, old_y)
        self.gl_widget.update()
        self.update_offset_display()

    def on_nudge_y(self, delta: float):
        """Nudge selected layers by delta pixels in Y direction."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_x, old_y = self.gl_widget.layer_offsets.get(layer_id, (0.0, 0.0))
            self.gl_widget.layer_offsets[layer_id] = (old_x, old_y + delta)
        self.gl_widget.update()
        self.update_offset_display()

    def on_nudge_rotation(self, delta: float):
        """Nudge selected layers by delta degrees in rotation."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_rot = self.gl_widget.layer_rotations.get(layer_id, 0.0)
            self.gl_widget.layer_rotations[layer_id] = old_rot + delta
        self.gl_widget.update()
        self.update_offset_display()

    def on_nudge_scale_x(self, delta: float):
        """Nudge selected layers by delta in X scale."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_sx, old_sy = self.gl_widget.layer_scale_offsets.get(layer_id, (1.0, 1.0))
            new_sx = max(0.01, old_sx + delta)  # Prevent negative/zero scale
            self.gl_widget.layer_scale_offsets[layer_id] = (new_sx, old_sy)
        self.gl_widget.update()
        self.update_offset_display()

    def on_nudge_scale_y(self, delta: float):
        """Nudge selected layers by delta in Y scale."""
        targets = self._get_nudge_targets()
        if not targets:
            self.log_widget.log("No layers selected for nudging", "WARNING")
            return
        for layer_id in targets:
            old_sx, old_sy = self.gl_widget.layer_scale_offsets.get(layer_id, (1.0, 1.0))
            new_sy = max(0.01, old_sy + delta)  # Prevent negative/zero scale
            self.gl_widget.layer_scale_offsets[layer_id] = (old_sx, new_sy)
        self.gl_widget.update()
        self.update_offset_display()

    def _get_nudge_targets(self) -> List[int]:
        """Return the list of layer IDs to apply nudging to."""
        if self.selected_layer_ids:
            return list(self.selected_layer_ids)
        if self.primary_selected_layer_id is not None:
            return [self.primary_selected_layer_id]
        return []

    def _update_nudge_controls_state(self):
        """Enable/disable nudge controls based on current layer selection."""
        has_selection = bool(self.selected_layer_ids) or self.primary_selected_layer_id is not None
        self.control_panel.set_nudge_controls_enabled(has_selection)

    def closeEvent(self, event):
        """Handle window close"""
        self.settings.setValue('game_path', self.game_path)

        # Stop watchdogs/faulthandler streams so crash log file isn't locked
        try:
            if self._hang_watchdog_active:
                faulthandler.cancel_dump_traceback_later()
                self._hang_watchdog_active = False
        except Exception:
            pass
        try:
            faulthandler.disable()
        except Exception:
            pass
        if getattr(self, "_faulthandler_stream", None):
            try:
                self._faulthandler_stream.close()
            except Exception:
                pass
            self._faulthandler_stream = None

        # Shut down audio/metronome resources to avoid lingering processes/locks
        if getattr(self, "metronome", None):
            try:
                self.metronome.set_enabled(False)
            except Exception:
                pass
            try:
                self.metronome.deleteLater()
            except Exception:
                pass
        if getattr(self, "audio_manager", None):
            try:
                self.audio_manager.set_enabled(False)
            except Exception:
                pass
            try:
                self.audio_manager.clear()
            except Exception:
                pass

        # Ensure the process terminates even if non-daemon threads linger
        def _force_terminate():
            try:
                os._exit(0)
            except Exception:
                # Fallback; if _exit is unavailable just quit normally
                QApplication.instance().quit()

        QTimer.singleShot(0, _force_terminate)

        # Stop offset update timer
        if hasattr(self, 'offset_update_timer'):
            self.offset_update_timer.stop()

        event.accept()

    def _compute_png_export_params(self):
        """Return common settings for PNG exports."""
        if not self.gl_widget.player.animation:
            return None

        use_full_res = getattr(self.export_settings, 'png_full_resolution', False)
        png_extra_scale = max(1.0, float(getattr(self.export_settings, 'png_full_scale_multiplier', 1.0)))
        fps = max(1, self.control_panel.fps_spin.value())
        return self._compute_export_render_plan(
            fps,
            fallback_width=self.gl_widget.width(),
            fallback_height=self.gl_widget.height(),
            use_full_res=bool(use_full_res),
            extra_scale=png_extra_scale,
        )

    def _ensure_pytoshop_available(self):
        """Return the pytoshop module, installing it automatically if needed."""
        if self._pytoshop is not None:
            return self._pytoshop
        try:
            module = importlib.import_module("pytoshop")
            self._pytoshop = module
            return module
        except ImportError:
            self.log_widget.log(
                "pytoshop is not installed for this interpreter. Attempting automatic install...",
                "WARNING"
            )
            installer = PytoshopInstaller(
                log_fn=lambda msg, level="INFO": self.log_widget.log(msg, level)
            )
            if not installer.install_latest():
                if not self._install_python_package("pytoshop>=1.2.1"):
                    self._show_pytoshop_install_help()
                    return None
            try:
                importlib.invalidate_caches()
                module = importlib.import_module("pytoshop")
                self._pytoshop = module
                return module
            except ImportError:
                self._show_pytoshop_install_help()
                return None
            except Exception as exc:
                self.log_widget.log(f"pytoshop import failed after install: {exc}", "ERROR")
                self._show_pytoshop_install_help(extra_detail=str(exc))
                return None

    def _install_python_package(self, package_spec: str) -> bool:
        """Install a package using pip for the current interpreter."""
        if getattr(sys, "frozen", False):
            self.log_widget.log(
                f"Cannot install '{package_spec}' from a packaged build; run the installer in a Python environment.",
                "ERROR",
            )
            return False
        python_exe = sys.executable or "python"
        try:
            subprocess.check_call([python_exe, "-m", "pip", "install", package_spec])
            self.log_widget.log(f"Installed dependency: {package_spec}", "SUCCESS")
            return True
        except Exception as exc:
            self.log_widget.log(f"Failed to install {package_spec}: {exc}", "ERROR")
            return False

    def _show_pytoshop_install_help(self, extra_detail: str = ""):
        """Display guidance on installing pytoshop manually."""
        python_exe = sys.executable or "python"
        message = (
            "PSD export requires the 'pytoshop' package but it could not be imported.\n\n"
            f"Install it for this interpreter with:\n  \"{python_exe}\" -m pip install pytoshop\n"
        )
        if extra_detail:
            message += f"\nDetails: {extra_detail}"
        QMessageBox.warning(self, "Missing pytoshop", message)
        self.log_widget.log("PSD export aborted because pytoshop is unavailable.", "ERROR")

    def _ensure_packbits_available(self) -> bool:
        """Ensure the standalone 'packbits' module used by pytoshop is importable."""
        packbits_module = None
        try:
            import packbits as packbits_import  # type: ignore
            packbits_module = packbits_import
        except ImportError:
            self.log_widget.log(
                "'packbits' dependency missing; installing now...",
                "WARNING"
            )
            installer = PythonPackageInstaller(
                "packbits",
                "packbits>=0.1.0",
                log_fn=lambda msg, level="INFO": self.log_widget.log(msg, level),
            )
            if installer.install_latest():
                try:
                    importlib.invalidate_caches()
                    import packbits as packbits_import  # type: ignore
                    packbits_module = packbits_import
                    self.log_widget.log("packbits installed successfully.", "SUCCESS")
                except ImportError as exc:
                    self.log_widget.log(f"packbits import failed after install: {exc}", "WARNING")
            if packbits_module is None:
                self.log_widget.log(
                    "Falling back to built-in packbits implementation.",
                    "WARNING"
                )
                packbits_module = self._create_packbits_fallback()

        if packbits_module is None:
            QMessageBox.warning(
                self,
                "Missing packbits",
                "Unable to load or emulate the 'packbits' module required for PSD export."
            )
            self.log_widget.log("packbits unavailable; PSD export aborted.", "ERROR")
            return False

        sys.modules['packbits'] = packbits_module  # type: ignore
        try:
            codecs_module = importlib.import_module("pytoshop.codecs")
        except Exception as exc:
            self.log_widget.log(f"Failed to import pytoshop.codecs: {exc}", "ERROR")
            return False
        setattr(codecs_module, "packbits", packbits_module)  # type: ignore
        try:
            importlib.reload(codecs_module)
        except Exception as exc:
            self.log_widget.log(f"Failed to reload pytoshop codecs after packbits install: {exc}", "WARNING")
        return True

    @staticmethod
    def _create_packbits_fallback():
        """Return a pure-python fallback for packbits encode/decode."""
        module = types.ModuleType("packbits_fallback")

        def decode(data):
            buffer = bytearray(data)
            result = bytearray()
            pos = 0
            length = len(buffer)
            while pos < length:
                header = buffer[pos]
                if header > 127:
                    header -= 256
                pos += 1
                if 0 <= header <= 127:
                    count = header + 1
                    result.extend(buffer[pos:pos + count])
                    pos += count
                elif header == -128:
                    continue
                else:
                    count = 1 - header
                    if pos < length:
                        result.extend([buffer[pos]] * count)
                        pos += 1
            return bytes(result)

        def encode(data):
            data = bytes(data)
            length = len(data)
            if length <= 1:
                return b'\x00' + data if length == 1 else data
            idx = 0
            result = bytearray()
            raw_buf = bytearray()

            def flush_raw():
                if not raw_buf:
                    return
                result.append(len(raw_buf) - 1)
                result.extend(raw_buf)
                raw_buf[:] = b''

            while idx < length:
                run_start = idx
                run_byte = data[idx]
                idx += 1
                while idx < length and data[idx] == run_byte and idx - run_start < 128:
                    idx += 1
                run_length = idx - run_start
                if run_length >= 3:
                    flush_raw()
                    result.append(257 - run_length)
                    result.append(run_byte)
                else:
                    raw_buf.extend(data[run_start:run_start + run_length])
                    while len(raw_buf) >= 128:
                        result.append(127)
                        result.extend(raw_buf[:128])
                        raw_buf = raw_buf[128:]
            flush_raw()
            return bytes(result)

        module.encode = encode
        module.decode = decode
        return module
    def _apply_sheet_aliases_to_base_atlases(
        self,
        base_atlases: List[TextureAtlas],
        aliases: Dict[str, List[str]]
    ) -> List[TextureAtlas]:
        """Reorder base atlases so alias-targeted sheets remain available as a fallback."""
        if not aliases:
            return list(base_atlases)
        prioritized: List[TextureAtlas] = []
        remaining: List[TextureAtlas] = []
        alias_keys = set(aliases.keys())
        for atlas in base_atlases:
            sheet_name = getattr(atlas, "source_name", None) or atlas.image_path
            keys = self._canonical_sheet_keys(sheet_name)
            if keys and alias_keys.intersection(keys):
                prioritized.append(atlas)
            else:
                remaining.append(atlas)
        return prioritized + remaining
    def _configure_costume_shaders(self, entry: Optional[CostumeEntry], costume_data: Optional[Dict[str, Any]]):
        """Automatic shader texture overrides derived from costume metadata."""
        if not entry or not costume_data:
            self.shader_registry.set_runtime_overrides({})
            return
        shader_defs = costume_data.get('apply_shader') or []
        if not shader_defs:
            self.shader_registry.set_runtime_overrides({})
            return

        layer_sheet_lookup, fallback_sheets = self._build_shader_sheet_lookup(costume_data)
        overrides: Dict[str, Dict[str, Any]] = {}
        for shader in shader_defs:
            node = (shader or {}).get('node')
            resource = (shader or {}).get('resource')
            if not resource:
                continue
            behavior = self.shader_registry.get_behavior(resource)
            texture_path: Optional[str] = None
            if not behavior or behavior.requires_texture:
                texture_path = self._resolve_shader_texture_path(
                    entry,
                    behavior,
                    node,
                    layer_sheet_lookup,
                    fallback_sheets,
                )
            self.shader_registry.register_costume_shader(
                resource,
                costume_key=entry.key,
                node=node,
                texture_path=texture_path,
            )
            if not behavior:
                self.log_widget.log(
                    f"No shader behavior metadata for '{resource}'. "
                    "Add an entry to costume_shader_behaviors.json to animate it.",
                    "INFO",
                )
                continue
            if behavior.requires_texture and not texture_path:
                self.log_widget.log(
                    f"Unable to resolve texture for shader '{resource}' (costume {entry.display_name}).",
                    "WARNING"
                )
                continue
            metadata = {"behavior": behavior.name}
            if texture_path:
                metadata["sequence_texture"] = texture_path
            overrides[resource.lower()] = {
                "metadata": metadata
            }
        self.shader_registry.set_runtime_overrides(overrides)

    def _build_shader_sheet_lookup(
        self, costume_data: Dict[str, Any]
    ) -> Tuple[Dict[str, List[str]], List[str]]:
        """Return layer->sheet mappings and fallback sheet bases for shader lookups."""
        lookup: Dict[str, List[str]] = {}
        fallbacks: List[str] = []

        def _add_layer_mapping(layer_name: Optional[str], sheet_name: Optional[str]):
            base = self._sheet_base_name(sheet_name)
            if not layer_name or not base:
                return
            for variant in self._layer_name_variants(layer_name):
                if not variant:
                    continue
                key = variant.lower()
                slots = lookup.setdefault(key, [])
                if base not in slots:
                    slots.append(base)

        for remap in costume_data.get('remaps', []):
            _add_layer_mapping(remap.get('display_name'), remap.get('sheet'))

        sheet_swaps = costume_data.get('sheet_remaps') or costume_data.get('swaps') or []
        for swap in sheet_swaps:
            base = self._sheet_base_name(swap.get('to'))
            if base:
                self._append_unique(fallbacks, base)

        for source in costume_data.get('sources', []):
            base = self._sheet_base_name(source.get('src'))
            if base:
                self._append_unique(fallbacks, base)

        for alias_targets in self.costume_sheet_aliases.values():
            for alias in alias_targets:
                base = self._sheet_base_name(alias)
                if base:
                    self._append_unique(fallbacks, base)

        return lookup, fallbacks

    def _match_sheet_candidates_for_node(
        self,
        node_name: Optional[str],
        sheet_lookup: Dict[str, List[str]]
    ) -> List[str]:
        if not node_name:
            return []
        matches: List[str] = []
        for variant in self._layer_name_variants(node_name):
            if not variant:
                continue
            key = variant.lower()
            options = sheet_lookup.get(key)
            if not options:
                continue
            for candidate in options:
                if candidate not in matches:
                    matches.append(candidate)
        return matches

    @staticmethod
    def _sheet_base_name(sheet: Optional[str]) -> Optional[str]:
        if not sheet:
            return None
        stem = Path(sheet).stem
        lowered = stem.lower()
        if lowered.endswith("_sheet"):
            stem = stem[: -len("_sheet")]
        return stem or None

    @staticmethod
    def _append_unique(collection: List[str], value: Optional[str]) -> None:
        if not value:
            return
        if value not in collection:
            collection.append(value)

    def _locate_costume_texture(self, base_name: Optional[str]) -> Optional[str]:
        if not base_name or not self.game_path:
            return None
        costume_dir = Path(self.game_path) / "data" / "gfx" / "costumes"
        if not costume_dir.exists():
            return None
        for ext in (".avif", ".png", ".dds", ".jpg", ".jpeg", ".tga", ".bmp"):
            candidate = costume_dir / f"{base_name}{ext}"
            if candidate.exists():
                return str(candidate)
        return None

    def _resolve_shader_texture_path(
        self,
        entry: CostumeEntry,
        behavior,
        node_name: Optional[str],
        sheet_lookup: Dict[str, List[str]],
        fallback_sheets: List[str],
    ) -> Optional[str]:
        if not self.game_path:
            return None

        candidates: List[str] = []
        candidates.extend(self._match_sheet_candidates_for_node(node_name, sheet_lookup))
        candidates.extend(fallback_sheets)
        prefix = self._costume_texture_prefix(entry.key)
        if prefix:
            candidates.append(prefix)

        ordered_bases: List[str] = []
        for candidate in candidates:
            if candidate and candidate not in ordered_bases:
                ordered_bases.append(candidate)

        suffixes: List[str] = []
        if behavior and behavior.texture_suffix:
            suffixes.append(behavior.texture_suffix)
        suffixes.extend(["_sequence", ""])
        dedup_suffixes: List[str] = []
        for suffix in suffixes:
            if suffix not in dedup_suffixes:
                dedup_suffixes.append(suffix)

        for base in ordered_bases:
            for suffix in dedup_suffixes:
                target = base
                if suffix and not base.lower().endswith(suffix.lower()):
                    target = f"{base}{suffix}"
                path = self._locate_costume_texture(target)
                if path:
                    return path
        return None

    @staticmethod
    def _costume_texture_prefix(entry_key: str) -> Optional[str]:
        if not entry_key.startswith("costume_"):
            return None
        parts = entry_key.split("_")[1:]
        if len(parts) < 2:
            return None
        index = parts[-1]
        token = "_".join(parts[:-1]).upper()
        return f"monster_{token}_costume_{index}"
