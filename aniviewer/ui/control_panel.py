"""
Control Panel
Main control panel with file selection, animation controls, and settings
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QComboBox, QPushButton, QSpinBox, QDoubleSpinBox,
    QSlider, QCheckBox, QGroupBox, QScrollArea, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QSizePolicy, QColorDialog, QTabWidget,
    QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt6.QtGui import QPixmap
from typing import Any, List, Tuple, Optional


class FullscreenSafeComboBox(QComboBox):
    """ComboBox whose popup stays visible when the main window is fullscreen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView(self)
        view.setUniformItemSizes(True)
        self.setView(view)

    def showPopup(self):
        view = self.view()
        popup = view.window()
        desired_flags = popup.windowFlags() | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        if popup.windowFlags() != desired_flags:
            popup.setWindowFlags(desired_flags)
            popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        super().showPopup()
        popup.raise_()
        popup.activateWindow()
        view.setFocus(Qt.FocusReason.PopupFocusReason)


class ControlPanel(QWidget):
    """Control panel with all main controls"""
    
    # Signals
    bin_selected = pyqtSignal(int)
    convert_bin_clicked = pyqtSignal()
    convert_dof_clicked = pyqtSignal()
    refresh_files_clicked = pyqtSignal()
    animation_selected = pyqtSignal(int)
    costume_selected = pyqtSignal(int)
    costume_convert_clicked = pyqtSignal()
    scale_changed = pyqtSignal(float)
    fps_changed = pyqtSignal(int)
    position_scale_changed = pyqtSignal(float)
    position_scale_slider_changed = pyqtSignal(int)
    base_world_scale_changed = pyqtSignal(float)
    base_world_scale_slider_changed = pyqtSignal(int)
    reset_camera_clicked = pyqtSignal()
    fit_to_view_clicked = pyqtSignal()
    show_bones_toggled = pyqtSignal(bool)
    reset_offsets_clicked = pyqtSignal()
    export_frame_clicked = pyqtSignal()
    export_frames_sequence_clicked = pyqtSignal()
    export_psd_clicked = pyqtSignal()
    export_ae_rig_clicked = pyqtSignal()
    export_mov_clicked = pyqtSignal()
    export_mp4_clicked = pyqtSignal()
    export_webm_clicked = pyqtSignal()
    export_gif_clicked = pyqtSignal()
    credits_clicked = pyqtSignal()
    file_search_changed = pyqtSignal(str)
    monster_browser_requested = pyqtSignal()
    dof_search_toggled = pyqtSignal(bool)
    translation_sensitivity_changed = pyqtSignal(float)
    rotation_sensitivity_changed = pyqtSignal(float)
    rotation_overlay_size_changed = pyqtSignal(float)
    rotation_gizmo_toggled = pyqtSignal(bool)
    anchor_overlay_toggled = pyqtSignal(bool)
    parent_overlay_toggled = pyqtSignal(bool)
    anchor_drag_precision_changed = pyqtSignal(float)
    anchor_bias_x_changed = pyqtSignal(float)
    anchor_bias_y_changed = pyqtSignal(float)
    anchor_flip_x_changed = pyqtSignal(bool)
    anchor_flip_y_changed = pyqtSignal(bool)
    anchor_scale_x_changed = pyqtSignal(float)
    anchor_scale_y_changed = pyqtSignal(float)
    local_position_multiplier_changed = pyqtSignal(float)
    parent_mix_changed = pyqtSignal(float)
    rotation_bias_changed = pyqtSignal(float)
    scale_bias_x_changed = pyqtSignal(float)
    scale_bias_y_changed = pyqtSignal(float)
    world_offset_x_changed = pyqtSignal(float)
    world_offset_y_changed = pyqtSignal(float)
    particle_origin_offset_x_changed = pyqtSignal(float)
    particle_origin_offset_y_changed = pyqtSignal(float)
    trim_shift_multiplier_changed = pyqtSignal(float)
    antialias_toggled = pyqtSignal(bool)
    audio_enabled_changed = pyqtSignal(bool)
    audio_volume_changed = pyqtSignal(int)
    audio_track_mute_changed = pyqtSignal(str, bool)
    save_offsets_clicked = pyqtSignal()
    load_offsets_clicked = pyqtSignal()
    nudge_x_changed = pyqtSignal(float)
    nudge_y_changed = pyqtSignal(float)
    nudge_rotation_changed = pyqtSignal(float)
    nudge_scale_x_changed = pyqtSignal(float)
    nudge_scale_y_changed = pyqtSignal(float)
    scale_gizmo_toggled = pyqtSignal(bool)
    scale_gizmo_mode_changed = pyqtSignal(str)
    bpm_value_changed = pyqtSignal(float)
    sync_audio_to_bpm_toggled = pyqtSignal(bool)
    pitch_shift_toggled = pyqtSignal(bool)
    bpm_reset_requested = pyqtSignal()
    base_bpm_lock_requested = pyqtSignal()
    metronome_toggled = pyqtSignal(bool)
    metronome_audible_toggled = pyqtSignal(bool)
    time_signature_changed = pyqtSignal(int, int)
    diagnostics_enabled_changed = pyqtSignal(bool)
    diagnostics_refresh_requested = pyqtSignal()
    diagnostics_export_requested = pyqtSignal()
    pose_record_clicked = pyqtSignal()
    pose_mode_changed = pyqtSignal(str)
    pose_reset_clicked = pyqtSignal()
    keyframe_undo_clicked = pyqtSignal()
    keyframe_redo_clicked = pyqtSignal()
    keyframe_delete_others_clicked = pyqtSignal()
    extend_duration_clicked = pyqtSignal()
    save_animation_clicked = pyqtSignal()
    load_animation_clicked = pyqtSignal()
    export_animation_bin_clicked = pyqtSignal()
    solid_bg_enabled_changed = pyqtSignal(bool)
    solid_bg_color_changed = pyqtSignal(int, int, int, int)
    solid_bg_auto_requested = pyqtSignal()
    viewport_bg_enabled_changed = pyqtSignal(bool)
    viewport_bg_image_enabled_changed = pyqtSignal(bool)
    viewport_bg_image_changed = pyqtSignal(str)
    viewport_bg_keep_aspect_changed = pyqtSignal(bool)
    viewport_bg_zoom_fill_changed = pyqtSignal(bool)
    viewport_bg_flip_h_changed = pyqtSignal(bool)
    viewport_bg_flip_v_changed = pyqtSignal(bool)
    viewport_bg_parallax_enabled_changed = pyqtSignal(bool)
    viewport_bg_parallax_zoom_strength_changed = pyqtSignal(float)
    viewport_bg_parallax_pan_strength_changed = pyqtSignal(float)
    viewport_bg_color_mode_changed = pyqtSignal(str)
    export_include_viewport_bg_changed = pyqtSignal(bool)
    sprite_assign_clicked = pyqtSignal()
    constraints_enabled_changed = pyqtSignal(bool)
    constraint_item_toggled = pyqtSignal(str, bool)
    constraint_add_requested = pyqtSignal()
    constraint_edit_requested = pyqtSignal(str)
    constraint_remove_requested = pyqtSignal(str)
    joint_solver_enabled_changed = pyqtSignal(bool)
    joint_solver_iterations_changed = pyqtSignal(int)
    joint_solver_strength_changed = pyqtSignal(float)
    joint_solver_parented_changed = pyqtSignal(bool)
    propagate_user_transforms_changed = pyqtSignal(bool)
    preserve_children_on_record_changed = pyqtSignal(bool)
    joint_solver_capture_requested = pyqtSignal()
    joint_solver_bake_current_requested = pyqtSignal()
    joint_solver_bake_range_requested = pyqtSignal()
    compact_ui_toggled = pyqtSignal(bool)
    
    def __init__(self, parent=None):
        super().__init__(parent)

        self.section_tabs = QTabWidget()
        self.section_tabs.setDocumentMode(True)
        self.section_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.section_tabs.setMovable(False)
        self._section_layouts = {}
        self._build_section_tabs()

        self.init_ui()
        self._updating_constraints_list = False
        self._compact_ui_enabled = False

        # Set up the main layout
        container_layout = QVBoxLayout(self)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(self.section_tabs, stretch=1)
        self._preferred_width = 440
        self._max_width = 600
        size_policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setSizePolicy(size_policy)
        self.setMaximumWidth(self._max_width)

    def sizeHint(self):
        """Provide a comfortable default width without blocking collapse."""
        base_hint = super().sizeHint()
        preferred_width = getattr(self, "_preferred_width", 360)
        max_width = getattr(self, "_max_width", 0)
        if not base_hint.isValid():
            return QSize(preferred_width, 0)
        width = max(base_hint.width(), preferred_width)
        if max_width:
            width = min(width, max_width)
        base_hint.setWidth(width)
        return base_hint

    def _build_section_tabs(self) -> None:
        sections = [
            ("Files", "files"),
            ("Animation", "animation"),
            ("Playback", "playback"),
            ("Viewport", "viewport"),
            ("Audio", "audio"),
            ("Sprites", "sprites"),
            ("Terrain", "terrain"),
            ("Export", "export"),
            ("Rigging", "rigging"),
            ("System", "system"),
        ]
        for label, key in sections:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(8)
            scroll.setWidget(content)
            self.section_tabs.addTab(scroll, label)
            self._section_layouts[key] = layout
    
    def init_ui(self):
        """Initialize the UI"""
        files_section = self._section_layouts["files"]
        animation_section = self._section_layouts["animation"]
        playback_section = self._section_layouts["playback"]
        viewport_section = self._section_layouts["viewport"]
        audio_section = self._section_layouts["audio"]
        sprites_section = self._section_layouts["sprites"]
        terrain_section = self._section_layouts["terrain"]
        export_section = self._section_layouts["export"]
        rigging_section = self._section_layouts["rigging"]
        system_section = self._section_layouts["system"]

        # File selection
        file_group = QGroupBox("File Selection")
        file_layout = QVBoxLayout()

        self.barebones_container = QWidget()
        barebones_layout = QVBoxLayout(self.barebones_container)
        barebones_layout.setContentsMargins(0, 0, 0, 0)

        search_layout = QHBoxLayout()
        search_label = QLabel("Search:")
        search_layout.addWidget(search_label)

        self.file_search_input = QLineEdit()
        self.file_search_input.setPlaceholderText("Filter BIN/JSON files...")
        self.file_search_input.setClearButtonEnabled(True)
        self.file_search_input.textChanged.connect(self.file_search_changed.emit)
        search_layout.addWidget(self.file_search_input)

        barebones_layout.addLayout(search_layout)
        
        self.bin_combo = FullscreenSafeComboBox()
        self.bin_combo.currentIndexChanged.connect(self.bin_selected.emit)
        barebones_layout.addWidget(QLabel("Select BIN/JSON:"))
        barebones_layout.addWidget(self.bin_combo)

        self.file_count_label = QLabel("No files indexed")
        self.file_count_label.setStyleSheet("color: gray; font-size: 8pt;")
        barebones_layout.addWidget(self.file_count_label)

        self.dof_search_toggle = QCheckBox("DOF asset search toggle")
        self.dof_search_toggle.setToolTip(
            "Switch the file list to the selected Down of the Fare Assets folder."
        )
        self.dof_search_toggle.toggled.connect(self.dof_search_toggled.emit)
        barebones_layout.addWidget(self.dof_search_toggle)
        
        self.convert_bin_btn = QPushButton("Convert BIN to JSON")
        self.convert_bin_btn.clicked.connect(self.convert_bin_clicked.emit)
        barebones_layout.addWidget(self.convert_bin_btn)

        self._dof_convert_label = "Convert DOF to JSON"
        self.convert_dof_btn = QPushButton(self._dof_convert_label)
        self.convert_dof_btn.clicked.connect(self.convert_dof_clicked.emit)
        barebones_layout.addWidget(self.convert_dof_btn)

        self.dof_mesh_pivot_checkbox = QCheckBox("DOF: Use pivot-local mesh vertices")
        self.dof_mesh_pivot_checkbox.setToolTip(
            "Skip Sprite.m_Offset when decoding mesh vertices/bounds."
        )
        barebones_layout.addWidget(self.dof_mesh_pivot_checkbox)

        self.dof_include_mesh_xml_checkbox = QCheckBox("DOF: Include mesh data in atlas XML")
        self.dof_include_mesh_xml_checkbox.setToolTip(
            "Adds vertices/verticesUV/triangles blocks to exported atlas XML."
        )
        self.dof_include_mesh_xml_checkbox.setChecked(True)
        barebones_layout.addWidget(self.dof_include_mesh_xml_checkbox)

        self.dof_premultiply_alpha_checkbox = QCheckBox("DOF: Premultiply atlas alpha")
        self.dof_premultiply_alpha_checkbox.setToolTip(
            "Export atlas PNGs with premultiplied alpha."
        )
        barebones_layout.addWidget(self.dof_premultiply_alpha_checkbox)

        self.dof_swap_anchor_report_checkbox = QCheckBox("DOF: Write swap-anchor report JSON")
        self.dof_swap_anchor_report_checkbox.setToolTip(
            "Write a JSON report with swap-frame alignment stats next to the output."
        )
        barebones_layout.addWidget(self.dof_swap_anchor_report_checkbox)

        self.dof_swap_anchor_edge_align_checkbox = QCheckBox(
            "DOF: Swap edge alignment (auto)"
        )
        self.dof_swap_anchor_edge_align_checkbox.setToolTip(
            "Align swap sprites by best-fit edge/center using mesh bounds."
        )
        barebones_layout.addWidget(self.dof_swap_anchor_edge_align_checkbox)

        self.dof_swap_anchor_pivot_offset_checkbox = QCheckBox(
            "DOF: Swap pivot-offset alignment"
        )
        self.dof_swap_anchor_pivot_offset_checkbox.setToolTip(
            "Align swap sprites by Sprite.m_Offset pivot positions (mesh sprites)."
        )
        barebones_layout.addWidget(self.dof_swap_anchor_pivot_offset_checkbox)

        self.dof_swap_anchor_report_override_checkbox = QCheckBox(
            "DOF: Swap report override"
        )
        self.dof_swap_anchor_report_override_checkbox.setToolTip(
            "Use an existing swap-anchor report JSON to override per-node modes."
        )
        barebones_layout.addWidget(self.dof_swap_anchor_report_override_checkbox)
        
        refresh_btn = QPushButton("Refresh File List")
        refresh_btn.clicked.connect(self.refresh_files_clicked.emit)
        barebones_layout.addWidget(refresh_btn)

        file_layout.addWidget(self.barebones_container)

        self.monster_browser_container = QWidget()
        monster_layout = QVBoxLayout(self.monster_browser_container)
        monster_layout.setContentsMargins(0, 0, 0, 0)
        browser_label = QLabel("Use the Monster Browser to visually select monster files.")
        browser_label.setWordWrap(True)
        monster_layout.addWidget(browser_label)
        self.monster_browser_button = QPushButton("Open Monster Browser")
        self.monster_browser_button.clicked.connect(self.monster_browser_requested.emit)
        monster_layout.addWidget(self.monster_browser_button)
        self.monster_browser_hint = QLabel(
            "Portraits load from data/gfx/book (non-silhouette images only)."
        )
        self.monster_browser_hint.setStyleSheet("color: gray; font-size: 8pt;")
        self.monster_browser_hint.setWordWrap(True)
        monster_layout.addWidget(self.monster_browser_hint)
        file_layout.addWidget(self.monster_browser_container)
        
        file_group.setLayout(file_layout)
        files_section.addWidget(file_group)
        self.set_barebones_file_mode(True)
        
        # Animation selection
        anim_group = QGroupBox("Animation")
        anim_layout = QVBoxLayout()
        
        self.anim_combo = FullscreenSafeComboBox()
        self.anim_combo.currentIndexChanged.connect(self.animation_selected.emit)
        anim_layout.addWidget(QLabel("Select Animation:"))
        anim_layout.addWidget(self.anim_combo)

        self.costume_combo = FullscreenSafeComboBox()
        self.costume_combo.currentIndexChanged.connect(self.costume_selected.emit)
        anim_layout.addWidget(QLabel("Select Costume:"))
        anim_layout.addWidget(self.costume_combo)
        self.costume_convert_btn = QPushButton("Convert Costume BIN to JSON")
        self.costume_convert_btn.clicked.connect(self.costume_convert_clicked.emit)
        anim_layout.addWidget(self.costume_convert_btn)

        pose_row = QHBoxLayout()
        pose_row.setSpacing(6)
        pose_row.addWidget(QLabel("Pose Influence:"))
        self.pose_mode_combo = FullscreenSafeComboBox()
        self.pose_mode_combo.addItem("Keyframe Only", "current")
        self.pose_mode_combo.addItem("Propagate Forward", "forward")
        self.pose_mode_combo.currentIndexChanged.connect(
            lambda idx: self.pose_mode_changed.emit(self.pose_mode_combo.itemData(idx))
        )
        pose_row.addWidget(self.pose_mode_combo, 1)
        self.record_pose_btn = QPushButton("Record Pose")
        self.record_pose_btn.setToolTip("Bake current gizmo offsets into animation keyframes")
        self.record_pose_btn.clicked.connect(self.pose_record_clicked.emit)
        pose_row.addWidget(self.record_pose_btn)
        anim_layout.addLayout(pose_row)

        self.preserve_children_on_record_checkbox = QCheckBox("Follow Parent When Recording")
        self.preserve_children_on_record_checkbox.setToolTip(
            "Allow child layers to follow the parent when recording a pose."
        )
        self.preserve_children_on_record_checkbox.setChecked(True)
        self.preserve_children_on_record_checkbox.toggled.connect(
            self.preserve_children_on_record_changed.emit
        )
        anim_layout.addWidget(self.preserve_children_on_record_checkbox)

        pose_actions = QGridLayout()
        pose_actions.setHorizontalSpacing(6)
        pose_actions.setVerticalSpacing(6)
        self.reset_pose_btn = QPushButton("Reset Pose")
        self.reset_pose_btn.setToolTip("Revert selected keyframes to their default animation values")
        self.reset_pose_btn.clicked.connect(self.pose_reset_clicked.emit)
        pose_actions.addWidget(self.reset_pose_btn, 0, 0)
        self.undo_keyframe_btn = QPushButton("Undo Keyframe")
        self.undo_keyframe_btn.setToolTip("Undo the most recent keyframe edit")
        self.undo_keyframe_btn.clicked.connect(self.keyframe_undo_clicked.emit)
        pose_actions.addWidget(self.undo_keyframe_btn, 0, 1)
        self.redo_keyframe_btn = QPushButton("Redo Keyframe")
        self.redo_keyframe_btn.setToolTip("Redo the last undone keyframe edit")
        self.redo_keyframe_btn.clicked.connect(self.keyframe_redo_clicked.emit)
        pose_actions.addWidget(self.redo_keyframe_btn, 1, 0)
        self.delete_other_keyframes_btn = QPushButton("Delete Other Keyframes")
        self.delete_other_keyframes_btn.setToolTip("Remove all keyframes except those at the current time for selected layers")
        self.delete_other_keyframes_btn.clicked.connect(self.keyframe_delete_others_clicked.emit)
        pose_actions.addWidget(self.delete_other_keyframes_btn, 1, 1)
        self.extend_duration_btn = QPushButton("Set Duration…")
        self.extend_duration_btn.setToolTip("Adjust total animation length")
        self.extend_duration_btn.clicked.connect(self.extend_duration_clicked.emit)
        pose_actions.addWidget(self.extend_duration_btn, 2, 0, 1, 2)
        io_row = QGridLayout()
        io_row.setHorizontalSpacing(6)
        io_row.setVerticalSpacing(6)
        self.load_animation_btn = QPushButton("Load Animation…")
        self.load_animation_btn.setToolTip("Load a previously saved animation JSON file")
        self.load_animation_btn.clicked.connect(self.load_animation_clicked.emit)
        io_row.addWidget(self.load_animation_btn, 0, 0)
        self.save_animation_btn = QPushButton("Save Animation…")
        self.save_animation_btn.setToolTip("Save the current animation (layers + keyframes) to a JSON file")
        self.save_animation_btn.clicked.connect(self.save_animation_clicked.emit)
        io_row.addWidget(self.save_animation_btn, 0, 1)
        self.export_bin_btn = QPushButton("Export Animation BIN…")
        self.export_bin_btn.setToolTip("Package the current animation into a BIN file usable by the game")
        self.export_bin_btn.clicked.connect(self.export_animation_bin_clicked.emit)
        io_row.addWidget(self.export_bin_btn, 1, 0, 1, 2)
        anim_layout.addLayout(io_row)
        anim_layout.addLayout(pose_actions)
        
        anim_group.setLayout(anim_layout)
        animation_section.addWidget(anim_group)
        self.update_costume_options([])

        gizmo_toggle_group = QGroupBox("Gizmo Overlays")
        gizmo_toggle_layout = QHBoxLayout()
        gizmo_toggle_layout.setSpacing(8)
        self.rotation_gizmo_checkbox = QCheckBox("Rotation")
        self.rotation_gizmo_checkbox.setToolTip("Display a draggable ring around the selected sprite for rotation.")
        gizmo_toggle_layout.addWidget(self.rotation_gizmo_checkbox)

        self.scale_gizmo_checkbox = QCheckBox("Scale")
        self.scale_gizmo_checkbox.setToolTip("Display the scale gizmo for selected sprites.")
        self.scale_gizmo_checkbox.toggled.connect(self.scale_gizmo_toggled.emit)
        gizmo_toggle_layout.addWidget(self.scale_gizmo_checkbox)

        self.anchor_overlay_checkbox = QCheckBox("Anchor")
        self.anchor_overlay_checkbox.setToolTip("Displays every layer's anchor pivot and lets you drag them live.")
        self.anchor_overlay_checkbox.toggled.connect(self.anchor_overlay_toggled.emit)
        gizmo_toggle_layout.addWidget(self.anchor_overlay_checkbox)

        self.parent_overlay_checkbox = QCheckBox("Parent")
        self.parent_overlay_checkbox.setToolTip("Shows parent-child connectors and lets you drag parent handles to reposition hierarchies.")
        self.parent_overlay_checkbox.toggled.connect(self.parent_overlay_toggled.emit)
        gizmo_toggle_layout.addWidget(self.parent_overlay_checkbox)
        gizmo_toggle_layout.addStretch(1)
        gizmo_toggle_group.setLayout(gizmo_toggle_layout)
        playback_section.addWidget(gizmo_toggle_group)
        
        # Render settings
        render_group = QGroupBox("Render Settings")
        render_layout = QVBoxLayout()
        
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(QLabel("Scale:"))
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setMinimum(0.1)
        self.scale_spin.setMaximum(10.0)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setSingleStep(0.1)
        self.scale_spin.valueChanged.connect(self.scale_changed.emit)
        scale_layout.addWidget(self.scale_spin)
        render_layout.addLayout(scale_layout)
        
        fps_layout = QHBoxLayout()
        fps_layout.addWidget(QLabel("FPS:"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setMinimum(1)
        self.fps_spin.setMaximum(120)
        self.fps_spin.setValue(60)
        self.fps_spin.valueChanged.connect(self.fps_changed.emit)
        fps_layout.addWidget(self.fps_spin)
        render_layout.addLayout(fps_layout)
        
        # Position scale slider
        pos_scale_layout = QHBoxLayout()
        pos_scale_layout.addWidget(QLabel("Position Scale:"))
        self.pos_scale_spin = QDoubleSpinBox()
        self.pos_scale_spin.setMinimum(-1000.0)  # Unlimited range
        self.pos_scale_spin.setMaximum(1000.0)   # Unlimited range
        self.pos_scale_spin.setValue(1.0)
        self.pos_scale_spin.setSingleStep(0.01)
        self.pos_scale_spin.setDecimals(3)
        self.pos_scale_spin.valueChanged.connect(self.position_scale_changed.emit)
        pos_scale_layout.addWidget(self.pos_scale_spin)
        render_layout.addLayout(pos_scale_layout)
        
        self.pos_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.pos_scale_slider.setMinimum(-100000)  # -1000.0
        self.pos_scale_slider.setMaximum(100000)   # 1000.0
        self.pos_scale_slider.setValue(100)        # 1.0
        self.pos_scale_slider.valueChanged.connect(self.position_scale_slider_changed.emit)
        render_layout.addWidget(self.pos_scale_slider)
        
        pos_scale_help = QLabel("Adjusts spacing between sprite segments")
        pos_scale_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        pos_scale_help.setWordWrap(True)
        pos_scale_help.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        render_layout.addWidget(pos_scale_help)
        
        # Base World Scale slider (from Ghidra analysis)
        base_scale_layout = QHBoxLayout()
        base_scale_layout.addWidget(QLabel("Base World Scale:"))
        self.base_scale_spin = QDoubleSpinBox()
        self.base_scale_spin.setMinimum(-20.0)
        self.base_scale_spin.setMaximum(20.0)
        self.base_scale_spin.setValue(1.0)
        self.base_scale_spin.setSingleStep(0.1)
        self.base_scale_spin.setDecimals(2)
        self.base_scale_spin.valueChanged.connect(self.base_world_scale_changed.emit)
        base_scale_layout.addWidget(self.base_scale_spin)
        render_layout.addLayout(base_scale_layout)
        
        self.base_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.base_scale_slider.setMinimum(-2000)  # -20.0
        self.base_scale_slider.setMaximum(2000)   # 20.0
        self.base_scale_slider.setValue(100)      # 1.0
        self.base_scale_slider.valueChanged.connect(self.base_world_scale_slider_changed.emit)
        render_layout.addWidget(self.base_scale_slider)
        
        base_scale_help = QLabel("Converts JSON coordinates to screen space (from Ghidra analysis)")
        base_scale_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        base_scale_help.setWordWrap(True)
        base_scale_help.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        render_layout.addWidget(base_scale_help)

        placement_label = QLabel("Sprite Placement Adjustments")
        placement_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        render_layout.addWidget(placement_label)

        # Drag speed controls
        drag_speed_layout = QHBoxLayout()
        drag_speed_layout.addWidget(QLabel("Drag Speed:"))
        self.translation_spin = QDoubleSpinBox()
        self.translation_spin.setMinimum(0.01)
        self.translation_spin.setMaximum(5.0)
        self.translation_spin.setDecimals(2)
        self.translation_spin.setSingleStep(0.01)
        self.translation_spin.setValue(1.0)
        drag_speed_layout.addWidget(self.translation_spin)
        render_layout.addLayout(drag_speed_layout)

        self.translation_slider = QSlider(Qt.Orientation.Horizontal)
        self.translation_slider.setMinimum(1)   # 0.01
        self.translation_slider.setMaximum(500) # 5.00
        self.translation_slider.setValue(100)   # 1.00
        render_layout.addWidget(self.translation_slider)

        # Rotation drag controls
        rotation_speed_layout = QHBoxLayout()
        rotation_speed_layout.addWidget(QLabel("Rotation Speed:"))
        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setMinimum(0.1)
        self.rotation_spin.setMaximum(20.0)
        self.rotation_spin.setDecimals(1)
        self.rotation_spin.setSingleStep(0.1)
        self.rotation_spin.setValue(1.0)
        rotation_speed_layout.addWidget(self.rotation_spin)
        render_layout.addLayout(rotation_speed_layout)

        self.rotation_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_slider.setMinimum(1)   # 0.1
        self.rotation_slider.setMaximum(200) # 20.0
        self.rotation_slider.setValue(10)    # 1.0
        render_layout.addWidget(self.rotation_slider)

        bpm_header = QLabel("Animation BPM")
        bpm_header.setStyleSheet("font-weight: bold; margin-top: 10px;")
        render_layout.addWidget(bpm_header)

        bpm_layout = QHBoxLayout()
        bpm_layout.addWidget(QLabel("BPM:"))
        self.bpm_spin = QDoubleSpinBox()
        self.bpm_spin.setRange(20.0, 300.0)
        self.bpm_spin.setDecimals(1)
        self.bpm_spin.setSingleStep(0.5)
        self.bpm_spin.setValue(120.0)
        bpm_layout.addWidget(self.bpm_spin)
        render_layout.addLayout(bpm_layout)

        self.bpm_slider = QSlider(Qt.Orientation.Horizontal)
        self.bpm_slider.setMinimum(200)   # 20.0 BPM
        self.bpm_slider.setMaximum(3000)  # 300.0 BPM
        self.bpm_slider.setValue(1200)    # 120.0 BPM
        render_layout.addWidget(self.bpm_slider)

        metronome_layout = QHBoxLayout()
        self.metronome_checkbox = QCheckBox("Enable Metronome")
        self.metronome_checkbox.setToolTip("Flash a beat indicator in sync with the BPM.")
        metronome_layout.addWidget(self.metronome_checkbox)
        metronome_layout.addStretch(1)
        metronome_layout.addWidget(QLabel("Beat:"))
        self.metronome_indicator = QLabel("●")
        self.metronome_indicator.setStyleSheet(
            "color: rgba(255, 255, 255, 0.25); font: bold 16pt;"
        )
        metronome_layout.addWidget(self.metronome_indicator)
        render_layout.addLayout(metronome_layout)
        self._metronome_flash_timer: QTimer | None = None
        self.metronome_audible_checkbox = QCheckBox("Audible Tick")
        self.metronome_audible_checkbox.setToolTip("Play a short click each beat.")
        render_layout.addWidget(self.metronome_audible_checkbox)
        ts_layout = QHBoxLayout()
        ts_layout.addWidget(QLabel("Time Signature:"))
        self.metronome_time_sig_num = QSpinBox()
        self.metronome_time_sig_num.setRange(1, 16)
        self.metronome_time_sig_num.setValue(4)
        self.metronome_time_sig_num.setFixedWidth(60)
        ts_layout.addWidget(self.metronome_time_sig_num)
        slash_label = QLabel("/")
        slash_label.setStyleSheet("font-weight: bold;")
        ts_layout.addWidget(slash_label)
        self.metronome_time_sig_denom = QComboBox()
        for value in (1, 2, 4, 8, 16):
            self.metronome_time_sig_denom.addItem(str(value), value)
        index = self.metronome_time_sig_denom.findData(4)
        if index >= 0:
            self.metronome_time_sig_denom.setCurrentIndex(index)
        self.metronome_time_sig_denom.setFixedWidth(70)
        ts_layout.addWidget(self.metronome_time_sig_denom)
        ts_layout.addStretch(1)
        render_layout.addLayout(ts_layout)
        self.metronome_time_sig_num.valueChanged.connect(self._emit_time_signature_change)
        self.metronome_time_sig_denom.currentIndexChanged.connect(self._emit_time_signature_change)

        bpm_help = QLabel("Derived from the island MIDI tempo. Adjust to fine-tune playback speed.")
        bpm_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        bpm_help.setWordWrap(True)
        bpm_help.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        render_layout.addWidget(bpm_help)

        bpm_toggle_layout = QHBoxLayout()
        self.sync_audio_checkbox = QCheckBox("Sync Audio Speed to BPM")
        bpm_toggle_layout.addWidget(self.sync_audio_checkbox)
        self.pitch_shift_checkbox = QCheckBox("Pitch Shift Audio")
        self.pitch_shift_checkbox.setToolTip("Enable to let the audio pitch rise/fall alongside the BPM tempo.")
        bpm_toggle_layout.addWidget(self.pitch_shift_checkbox)
        render_layout.addLayout(bpm_toggle_layout)

        self.reset_bpm_button = QPushButton("Reset BPM to Default")
        render_layout.addWidget(self.reset_bpm_button)
        self.lock_bpm_button = QPushButton("Lock Base BPM…")
        render_layout.addWidget(self.lock_bpm_button)

        gizmo_adjust_header = QLabel("Gizmo Adjustments")
        gizmo_adjust_header.setStyleSheet("font-weight: bold; margin-top: 10px;")
        render_layout.addWidget(gizmo_adjust_header)

        # Rotation gizmo adjustments
        overlay_layout = QHBoxLayout()
        overlay_layout.addWidget(QLabel("Rotation Gizmo Size:"))
        self.rotation_overlay_spin = QDoubleSpinBox()
        self.rotation_overlay_spin.setMinimum(10.0)
        self.rotation_overlay_spin.setMaximum(500.0)
        self.rotation_overlay_spin.setDecimals(1)
        self.rotation_overlay_spin.setSingleStep(5.0)
        self.rotation_overlay_spin.setValue(120.0)
        overlay_layout.addWidget(self.rotation_overlay_spin)
        render_layout.addLayout(overlay_layout)

        self.rotation_overlay_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_overlay_slider.setMinimum(10)
        self.rotation_overlay_slider.setMaximum(500)
        self.rotation_overlay_slider.setValue(120)
        render_layout.addWidget(self.rotation_overlay_slider)

        rotation_help = QLabel("Use the overlay ring to rotate sprites after selecting them.")
        rotation_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        rotation_help.setWordWrap(True)
        rotation_help.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        render_layout.addWidget(rotation_help)

        # Scale gizmo adjustments
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Scale Mode:"))
        self.scale_mode_combo = QComboBox()
        self.scale_mode_combo.addItems(["Uniform", "Per-Axis"])
        self.scale_mode_combo.currentTextChanged.connect(self.scale_gizmo_mode_changed.emit)
        mode_layout.addWidget(self.scale_mode_combo)
        render_layout.addLayout(mode_layout)

        scale_help = QLabel("Uniform scales evenly; Per-Axis lets you stretch horizontally/vertically.")
        scale_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        scale_help.setWordWrap(True)
        scale_help.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        render_layout.addWidget(scale_help)

        anchor_precision_layout = QHBoxLayout()
        anchor_precision_layout.addWidget(QLabel("Anchor Drag Precision:"))
        self.anchor_precision_spin = QDoubleSpinBox()
        self.anchor_precision_spin.setRange(0.01, 2.0)
        self.anchor_precision_spin.setDecimals(2)
        self.anchor_precision_spin.setSingleStep(0.01)
        self.anchor_precision_spin.setValue(0.25)
        anchor_precision_layout.addWidget(self.anchor_precision_spin)
        render_layout.addLayout(anchor_precision_layout)

        self.anchor_precision_slider = QSlider(Qt.Orientation.Horizontal)
        self.anchor_precision_slider.setRange(1, 200)  # 0.01 to 2.00
        self.anchor_precision_slider.setValue(25)      # 0.25 default
        render_layout.addWidget(self.anchor_precision_slider)

        anchor_precision_help = QLabel("Smaller values move anchors in finer increments while dragging.")
        anchor_precision_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        anchor_precision_help.setWordWrap(True)
        anchor_precision_help.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        render_layout.addWidget(anchor_precision_help)

        # Advanced placement bias controls
        bias_header = QLabel("Advanced Placement Bias")
        bias_header.setStyleSheet("font-weight: bold; margin-top: 10px;")
        render_layout.addWidget(bias_header)

        # Anchor bias X
        anchor_bias_x_layout = QHBoxLayout()
        anchor_bias_x_layout.addWidget(QLabel("Anchor Bias X:"))
        self.anchor_bias_x_spin = QDoubleSpinBox()
        self.anchor_bias_x_spin.setRange(-500.0, 500.0)
        self.anchor_bias_x_spin.setDecimals(2)
        self.anchor_bias_x_spin.setSingleStep(0.1)
        self.anchor_bias_x_spin.setValue(0.0)
        anchor_bias_x_layout.addWidget(self.anchor_bias_x_spin)
        render_layout.addLayout(anchor_bias_x_layout)

        self.anchor_bias_x_slider = QSlider(Qt.Orientation.Horizontal)
        self.anchor_bias_x_slider.setRange(-50000, 50000)  # -500.00 to 500.00
        self.anchor_bias_x_slider.setValue(0)
        render_layout.addWidget(self.anchor_bias_x_slider)

        # Anchor bias Y
        anchor_bias_y_layout = QHBoxLayout()
        anchor_bias_y_layout.addWidget(QLabel("Anchor Bias Y:"))
        self.anchor_bias_y_spin = QDoubleSpinBox()
        self.anchor_bias_y_spin.setRange(-500.0, 500.0)
        self.anchor_bias_y_spin.setDecimals(2)
        self.anchor_bias_y_spin.setSingleStep(0.1)
        self.anchor_bias_y_spin.setValue(0.0)
        anchor_bias_y_layout.addWidget(self.anchor_bias_y_spin)
        render_layout.addLayout(anchor_bias_y_layout)

        self.anchor_bias_y_slider = QSlider(Qt.Orientation.Horizontal)
        self.anchor_bias_y_slider.setRange(-50000, 50000)
        self.anchor_bias_y_slider.setValue(0)
        render_layout.addWidget(self.anchor_bias_y_slider)

        anchor_flip_layout = QHBoxLayout()
        self.anchor_flip_x_checkbox = QCheckBox("Flip Anchor X")
        self.anchor_flip_x_checkbox.setToolTip("Invert anchor X values for all layers.")
        self.anchor_flip_x_checkbox.toggled.connect(self.anchor_flip_x_changed.emit)
        anchor_flip_layout.addWidget(self.anchor_flip_x_checkbox)
        self.anchor_flip_y_checkbox = QCheckBox("Flip Anchor Y")
        self.anchor_flip_y_checkbox.setToolTip("Invert anchor Y values for all layers.")
        self.anchor_flip_y_checkbox.toggled.connect(self.anchor_flip_y_changed.emit)
        anchor_flip_layout.addWidget(self.anchor_flip_y_checkbox)
        render_layout.addLayout(anchor_flip_layout)

        anchor_scale_x_layout = QHBoxLayout()
        anchor_scale_x_layout.addWidget(QLabel("Anchor Scale X:"))
        self.anchor_scale_x_spin = QDoubleSpinBox()
        self.anchor_scale_x_spin.setRange(0.01, 10.0)
        self.anchor_scale_x_spin.setDecimals(2)
        self.anchor_scale_x_spin.setSingleStep(0.01)
        self.anchor_scale_x_spin.setValue(1.0)
        anchor_scale_x_layout.addWidget(self.anchor_scale_x_spin)
        render_layout.addLayout(anchor_scale_x_layout)

        self.anchor_scale_x_slider = QSlider(Qt.Orientation.Horizontal)
        self.anchor_scale_x_slider.setRange(1, 1000)  # 0.01 to 10.00
        self.anchor_scale_x_slider.setValue(100)
        render_layout.addWidget(self.anchor_scale_x_slider)

        anchor_scale_y_layout = QHBoxLayout()
        anchor_scale_y_layout.addWidget(QLabel("Anchor Scale Y:"))
        self.anchor_scale_y_spin = QDoubleSpinBox()
        self.anchor_scale_y_spin.setRange(0.01, 10.0)
        self.anchor_scale_y_spin.setDecimals(2)
        self.anchor_scale_y_spin.setSingleStep(0.01)
        self.anchor_scale_y_spin.setValue(1.0)
        anchor_scale_y_layout.addWidget(self.anchor_scale_y_spin)
        render_layout.addLayout(anchor_scale_y_layout)

        self.anchor_scale_y_slider = QSlider(Qt.Orientation.Horizontal)
        self.anchor_scale_y_slider.setRange(1, 1000)  # 0.01 to 10.00
        self.anchor_scale_y_slider.setValue(100)
        render_layout.addWidget(self.anchor_scale_y_slider)

        # Local position multiplier
        local_pos_layout = QHBoxLayout()
        local_pos_layout.addWidget(QLabel("Local Pos Multiplier:"))
        self.local_pos_spin = QDoubleSpinBox()
        self.local_pos_spin.setRange(0.0, 5.0)
        self.local_pos_spin.setDecimals(2)
        self.local_pos_spin.setSingleStep(0.05)
        self.local_pos_spin.setValue(1.0)
        local_pos_layout.addWidget(self.local_pos_spin)
        render_layout.addLayout(local_pos_layout)

        self.local_pos_slider = QSlider(Qt.Orientation.Horizontal)
        self.local_pos_slider.setRange(0, 500)  # 0.00 to 5.00
        self.local_pos_slider.setValue(100)
        render_layout.addWidget(self.local_pos_slider)

        # Parent mix
        parent_mix_layout = QHBoxLayout()
        parent_mix_layout.addWidget(QLabel("Parent Mix:"))
        self.parent_mix_spin = QDoubleSpinBox()
        self.parent_mix_spin.setRange(0.0, 1.0)
        self.parent_mix_spin.setDecimals(2)
        self.parent_mix_spin.setSingleStep(0.01)
        self.parent_mix_spin.setValue(1.0)
        parent_mix_layout.addWidget(self.parent_mix_spin)
        render_layout.addLayout(parent_mix_layout)

        self.parent_mix_slider = QSlider(Qt.Orientation.Horizontal)
        self.parent_mix_slider.setRange(0, 100)
        self.parent_mix_slider.setValue(100)
        render_layout.addWidget(self.parent_mix_slider)

        # Rotation bias
        rotation_bias_layout = QHBoxLayout()
        rotation_bias_layout.addWidget(QLabel("Rotation Bias (°):"))
        self.rotation_bias_spin = QDoubleSpinBox()
        self.rotation_bias_spin.setRange(-360.0, 360.0)
        self.rotation_bias_spin.setDecimals(1)
        self.rotation_bias_spin.setSingleStep(1.0)
        self.rotation_bias_spin.setValue(0.0)
        rotation_bias_layout.addWidget(self.rotation_bias_spin)
        render_layout.addLayout(rotation_bias_layout)

        self.rotation_bias_slider = QSlider(Qt.Orientation.Horizontal)
        self.rotation_bias_slider.setRange(-3600, 3600)  # -360.0 to 360.0
        self.rotation_bias_slider.setValue(0)
        render_layout.addWidget(self.rotation_bias_slider)

        # Scale bias X
        scale_bias_x_layout = QHBoxLayout()
        scale_bias_x_layout.addWidget(QLabel("Scale Bias X:"))
        self.scale_bias_x_spin = QDoubleSpinBox()
        self.scale_bias_x_spin.setRange(0.0, 5.0)
        self.scale_bias_x_spin.setDecimals(2)
        self.scale_bias_x_spin.setSingleStep(0.05)
        self.scale_bias_x_spin.setValue(1.0)
        scale_bias_x_layout.addWidget(self.scale_bias_x_spin)
        render_layout.addLayout(scale_bias_x_layout)

        self.scale_bias_x_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_bias_x_slider.setRange(0, 500)
        self.scale_bias_x_slider.setValue(100)
        render_layout.addWidget(self.scale_bias_x_slider)

        # Scale bias Y
        scale_bias_y_layout = QHBoxLayout()
        scale_bias_y_layout.addWidget(QLabel("Scale Bias Y:"))
        self.scale_bias_y_spin = QDoubleSpinBox()
        self.scale_bias_y_spin.setRange(0.0, 5.0)
        self.scale_bias_y_spin.setDecimals(2)
        self.scale_bias_y_spin.setSingleStep(0.05)
        self.scale_bias_y_spin.setValue(1.0)
        scale_bias_y_layout.addWidget(self.scale_bias_y_spin)
        render_layout.addLayout(scale_bias_y_layout)

        self.scale_bias_y_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_bias_y_slider.setRange(0, 500)
        self.scale_bias_y_slider.setValue(100)
        render_layout.addWidget(self.scale_bias_y_slider)

        # World offset X
        world_offset_x_layout = QHBoxLayout()
        world_offset_x_layout.addWidget(QLabel("World Offset X:"))
        self.world_offset_x_spin = QDoubleSpinBox()
        self.world_offset_x_spin.setRange(-1000.0, 1000.0)
        self.world_offset_x_spin.setDecimals(2)
        self.world_offset_x_spin.setSingleStep(1.0)
        self.world_offset_x_spin.setValue(0.0)
        world_offset_x_layout.addWidget(self.world_offset_x_spin)
        render_layout.addLayout(world_offset_x_layout)

        self.world_offset_x_slider = QSlider(Qt.Orientation.Horizontal)
        self.world_offset_x_slider.setRange(-100000, 100000)  # -1000.00 to 1000.00
        self.world_offset_x_slider.setValue(0)
        render_layout.addWidget(self.world_offset_x_slider)

        # World offset Y
        world_offset_y_layout = QHBoxLayout()
        world_offset_y_layout.addWidget(QLabel("World Offset Y:"))
        self.world_offset_y_spin = QDoubleSpinBox()
        self.world_offset_y_spin.setRange(-1000.0, 1000.0)
        self.world_offset_y_spin.setDecimals(2)
        self.world_offset_y_spin.setSingleStep(1.0)
        self.world_offset_y_spin.setValue(0.0)
        world_offset_y_layout.addWidget(self.world_offset_y_spin)
        render_layout.addLayout(world_offset_y_layout)

        self.world_offset_y_slider = QSlider(Qt.Orientation.Horizontal)
        self.world_offset_y_slider.setRange(-100000, 100000)
        self.world_offset_y_slider.setValue(0)
        render_layout.addWidget(self.world_offset_y_slider)

        # Particle origin offset X
        particle_origin_x_layout = QHBoxLayout()
        particle_origin_x_layout.addWidget(QLabel("Particle Origin X:"))
        self.particle_origin_x_spin = QDoubleSpinBox()
        self.particle_origin_x_spin.setRange(-1000.0, 1000.0)
        self.particle_origin_x_spin.setDecimals(2)
        self.particle_origin_x_spin.setSingleStep(1.0)
        self.particle_origin_x_spin.setValue(0.0)
        particle_origin_x_layout.addWidget(self.particle_origin_x_spin)
        render_layout.addLayout(particle_origin_x_layout)

        self.particle_origin_x_slider = QSlider(Qt.Orientation.Horizontal)
        self.particle_origin_x_slider.setRange(-100000, 100000)
        self.particle_origin_x_slider.setValue(0)
        render_layout.addWidget(self.particle_origin_x_slider)

        # Particle origin offset Y
        particle_origin_y_layout = QHBoxLayout()
        particle_origin_y_layout.addWidget(QLabel("Particle Origin Y:"))
        self.particle_origin_y_spin = QDoubleSpinBox()
        self.particle_origin_y_spin.setRange(-1000.0, 1000.0)
        self.particle_origin_y_spin.setDecimals(2)
        self.particle_origin_y_spin.setSingleStep(1.0)
        self.particle_origin_y_spin.setValue(0.0)
        particle_origin_y_layout.addWidget(self.particle_origin_y_spin)
        render_layout.addLayout(particle_origin_y_layout)

        self.particle_origin_y_slider = QSlider(Qt.Orientation.Horizontal)
        self.particle_origin_y_slider.setRange(-100000, 100000)
        self.particle_origin_y_slider.setValue(0)
        render_layout.addWidget(self.particle_origin_y_slider)

        # Trim shift multiplier
        trim_shift_layout = QHBoxLayout()
        trim_shift_layout.addWidget(QLabel("Trim Shift Multiplier:"))
        self.trim_shift_spin = QDoubleSpinBox()
        self.trim_shift_spin.setRange(0.0, 5.0)
        self.trim_shift_spin.setDecimals(2)
        self.trim_shift_spin.setSingleStep(0.05)
        self.trim_shift_spin.setValue(1.0)
        trim_shift_layout.addWidget(self.trim_shift_spin)
        render_layout.addLayout(trim_shift_layout)

        self.trim_shift_slider = QSlider(Qt.Orientation.Horizontal)
        self.trim_shift_slider.setRange(0, 500)
        self.trim_shift_slider.setValue(100)
        render_layout.addWidget(self.trim_shift_slider)

        reset_bias_btn = QPushButton("Reset Placement Bias Settings")
        reset_bias_btn.clicked.connect(self.reset_placement_bias_settings)
        render_layout.addWidget(reset_bias_btn)
        
        # Camera control buttons in a row
        camera_btn_layout = QHBoxLayout()
        
        reset_camera_btn = QPushButton("Reset Camera")
        reset_camera_btn.clicked.connect(self.reset_camera_clicked.emit)
        camera_btn_layout.addWidget(reset_camera_btn)
        
        fit_to_view_btn = QPushButton("Fit to View")
        fit_to_view_btn.clicked.connect(self.fit_to_view_clicked.emit)
        fit_to_view_btn.setToolTip("Center and scale to fit the monster in view")
        camera_btn_layout.addWidget(fit_to_view_btn)
        
        render_layout.addLayout(camera_btn_layout)
        
        camera_help = QLabel("Right-click/Middle-click + drag to pan\nScroll wheel to zoom")
        camera_help.setStyleSheet("color: gray; font-size: 9pt;")
        render_layout.addWidget(camera_help)
        
        # Debug/Visualization options
        render_layout.addWidget(QLabel(""))  # Spacer
        viz_label = QLabel("Visualization:")
        viz_label.setStyleSheet("font-weight: bold;")
        render_layout.addWidget(viz_label)
        
        self.show_bones_checkbox = QCheckBox("Show Bone Overlay")
        self.show_bones_checkbox.setToolTip("Display skeleton hierarchy with bones and anchor points")
        self.show_bones_checkbox.toggled.connect(self.show_bones_toggled.emit)
        render_layout.addWidget(self.show_bones_checkbox)

        self.antialias_checkbox = QCheckBox("Enable Anti-Aliasing")
        self.antialias_checkbox.setToolTip("Toggle multi-sample anti-aliasing for smoother edges")
        self.antialias_checkbox.setChecked(True)
        self.antialias_checkbox.toggled.connect(self.antialias_toggled.emit)
        render_layout.addWidget(self.antialias_checkbox)
        
        bones_help = QLabel("Shows parent-child connections and anchor points")
        bones_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        render_layout.addWidget(bones_help)
        
        render_group.setLayout(render_layout)
        playback_section.addWidget(render_group)
        
        # Sprite dragging controls
        drag_group = QGroupBox("Sprite Dragging")
        drag_layout = QVBoxLayout()
        
        drag_help = QLabel("Left-click + drag to move sprites\nSelect a layer to drag only that layer")
        drag_help.setStyleSheet("color: gray; font-size: 9pt;")
        drag_layout.addWidget(drag_help)
        
        reset_offsets_btn = QPushButton("Reset All Offsets")
        reset_offsets_btn.clicked.connect(self.reset_offsets_clicked.emit)
        drag_layout.addWidget(reset_offsets_btn)

        # Pixel-based nudging controls
        nudge_header = QLabel("Pixel Nudging (Selected Layers)")
        nudge_header.setStyleSheet("font-weight: bold; margin-top: 8px;")
        drag_layout.addWidget(nudge_header)

        nudge_help = QLabel("Adjust selected sprite segments by exact pixel amounts")
        nudge_help.setStyleSheet("color: gray; font-size: 8pt; font-style: italic;")
        nudge_help.setWordWrap(True)
        nudge_help.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        drag_layout.addWidget(nudge_help)

        # Nudge step size
        nudge_step_layout = QHBoxLayout()
        nudge_step_layout.addWidget(QLabel("Step Size:"))
        self.nudge_step_spin = QDoubleSpinBox()
        self.nudge_step_spin.setRange(0.1, 100.0)
        self.nudge_step_spin.setDecimals(1)
        self.nudge_step_spin.setSingleStep(0.5)
        self.nudge_step_spin.setValue(1.0)
        self.nudge_step_spin.setToolTip("Amount to nudge per button click (pixels)")
        nudge_step_layout.addWidget(self.nudge_step_spin)
        nudge_step_layout.addWidget(QLabel("px"))
        nudge_step_layout.addStretch()
        drag_layout.addLayout(nudge_step_layout)

        # X/Y Position nudging
        pos_nudge_layout = QHBoxLayout()
        pos_nudge_layout.setSpacing(4)
        pos_nudge_layout.addWidget(QLabel("Position:"))
        
        self.nudge_x_minus_btn = QPushButton("← X")
        self.nudge_x_minus_btn.setFixedWidth(50)
        self.nudge_x_minus_btn.setToolTip("Move selected layers left")
        self.nudge_x_minus_btn.clicked.connect(lambda: self._emit_nudge_x(-1))
        pos_nudge_layout.addWidget(self.nudge_x_minus_btn)
        
        self.nudge_x_plus_btn = QPushButton("X →")
        self.nudge_x_plus_btn.setFixedWidth(50)
        self.nudge_x_plus_btn.setToolTip("Move selected layers right")
        self.nudge_x_plus_btn.clicked.connect(lambda: self._emit_nudge_x(1))
        pos_nudge_layout.addWidget(self.nudge_x_plus_btn)
        
        pos_nudge_layout.addSpacing(8)
        
        self.nudge_y_minus_btn = QPushButton("↑ Y")
        self.nudge_y_minus_btn.setFixedWidth(50)
        self.nudge_y_minus_btn.setToolTip("Move selected layers up")
        self.nudge_y_minus_btn.clicked.connect(lambda: self._emit_nudge_y(-1))
        pos_nudge_layout.addWidget(self.nudge_y_minus_btn)
        
        self.nudge_y_plus_btn = QPushButton("Y ↓")
        self.nudge_y_plus_btn.setFixedWidth(50)
        self.nudge_y_plus_btn.setToolTip("Move selected layers down")
        self.nudge_y_plus_btn.clicked.connect(lambda: self._emit_nudge_y(1))
        pos_nudge_layout.addWidget(self.nudge_y_plus_btn)
        
        pos_nudge_layout.addStretch()
        drag_layout.addLayout(pos_nudge_layout)

        # Rotation nudging
        rot_nudge_layout = QHBoxLayout()
        rot_nudge_layout.setSpacing(4)
        rot_nudge_layout.addWidget(QLabel("Rotation:"))
        
        self.nudge_rot_step_spin = QDoubleSpinBox()
        self.nudge_rot_step_spin.setRange(0.1, 90.0)
        self.nudge_rot_step_spin.setDecimals(1)
        self.nudge_rot_step_spin.setSingleStep(1.0)
        self.nudge_rot_step_spin.setValue(5.0)
        self.nudge_rot_step_spin.setToolTip("Rotation step in degrees")
        self.nudge_rot_step_spin.setFixedWidth(60)
        rot_nudge_layout.addWidget(self.nudge_rot_step_spin)
        rot_nudge_layout.addWidget(QLabel("°"))
        
        self.nudge_rot_minus_btn = QPushButton("↺ CCW")
        self.nudge_rot_minus_btn.setFixedWidth(60)
        self.nudge_rot_minus_btn.setToolTip("Rotate counter-clockwise")
        self.nudge_rot_minus_btn.clicked.connect(lambda: self._emit_nudge_rotation(-1))
        rot_nudge_layout.addWidget(self.nudge_rot_minus_btn)
        
        self.nudge_rot_plus_btn = QPushButton("CW ↻")
        self.nudge_rot_plus_btn.setFixedWidth(60)
        self.nudge_rot_plus_btn.setToolTip("Rotate clockwise")
        self.nudge_rot_plus_btn.clicked.connect(lambda: self._emit_nudge_rotation(1))
        rot_nudge_layout.addWidget(self.nudge_rot_plus_btn)
        
        rot_nudge_layout.addStretch()
        drag_layout.addLayout(rot_nudge_layout)

        # Scale nudging
        scale_nudge_layout = QHBoxLayout()
        scale_nudge_layout.setSpacing(4)
        scale_nudge_layout.addWidget(QLabel("Scale:"))
        
        self.nudge_scale_step_spin = QDoubleSpinBox()
        self.nudge_scale_step_spin.setRange(0.01, 1.0)
        self.nudge_scale_step_spin.setDecimals(2)
        self.nudge_scale_step_spin.setSingleStep(0.01)
        self.nudge_scale_step_spin.setValue(0.05)
        self.nudge_scale_step_spin.setToolTip("Scale step multiplier")
        self.nudge_scale_step_spin.setFixedWidth(60)
        scale_nudge_layout.addWidget(self.nudge_scale_step_spin)
        
        self.nudge_scale_minus_btn = QPushButton("−")
        self.nudge_scale_minus_btn.setFixedWidth(35)
        self.nudge_scale_minus_btn.setToolTip("Decrease scale (uniform)")
        self.nudge_scale_minus_btn.clicked.connect(lambda: self._emit_nudge_scale_uniform(-1))
        scale_nudge_layout.addWidget(self.nudge_scale_minus_btn)
        
        self.nudge_scale_plus_btn = QPushButton("+")
        self.nudge_scale_plus_btn.setFixedWidth(35)
        self.nudge_scale_plus_btn.setToolTip("Increase scale (uniform)")
        self.nudge_scale_plus_btn.clicked.connect(lambda: self._emit_nudge_scale_uniform(1))
        scale_nudge_layout.addWidget(self.nudge_scale_plus_btn)
        
        scale_nudge_layout.addSpacing(8)
        scale_nudge_layout.addWidget(QLabel("X:"))
        
        self.nudge_scale_x_minus_btn = QPushButton("−")
        self.nudge_scale_x_minus_btn.setFixedWidth(30)
        self.nudge_scale_x_minus_btn.setToolTip("Decrease X scale")
        self.nudge_scale_x_minus_btn.clicked.connect(lambda: self._emit_nudge_scale_x(-1))
        scale_nudge_layout.addWidget(self.nudge_scale_x_minus_btn)
        
        self.nudge_scale_x_plus_btn = QPushButton("+")
        self.nudge_scale_x_plus_btn.setFixedWidth(30)
        self.nudge_scale_x_plus_btn.setToolTip("Increase X scale")
        self.nudge_scale_x_plus_btn.clicked.connect(lambda: self._emit_nudge_scale_x(1))
        scale_nudge_layout.addWidget(self.nudge_scale_x_plus_btn)
        
        scale_nudge_layout.addWidget(QLabel("Y:"))
        
        self.nudge_scale_y_minus_btn = QPushButton("−")
        self.nudge_scale_y_minus_btn.setFixedWidth(30)
        self.nudge_scale_y_minus_btn.setToolTip("Decrease Y scale")
        self.nudge_scale_y_minus_btn.clicked.connect(lambda: self._emit_nudge_scale_y(-1))
        scale_nudge_layout.addWidget(self.nudge_scale_y_minus_btn)
        
        self.nudge_scale_y_plus_btn = QPushButton("+")
        self.nudge_scale_y_plus_btn.setFixedWidth(30)
        self.nudge_scale_y_plus_btn.setToolTip("Increase Y scale")
        self.nudge_scale_y_plus_btn.clicked.connect(lambda: self._emit_nudge_scale_y(1))
        scale_nudge_layout.addWidget(self.nudge_scale_y_plus_btn)
        
        scale_nudge_layout.addStretch()
        drag_layout.addLayout(scale_nudge_layout)
        
        drag_layout.addWidget(QLabel("Layer Offsets:"))
        
        # Scrollable area for offset display
        offset_scroll = QScrollArea()
        offset_scroll.setWidgetResizable(True)
        offset_scroll.setMaximumHeight(150)
        
        self.offset_display_widget = QWidget()
        self.offset_display_layout = QVBoxLayout(self.offset_display_widget)
        self.offset_display_layout.addStretch()
        
        offset_scroll.setWidget(self.offset_display_widget)
        drag_layout.addWidget(offset_scroll)
        
        drag_group.setLayout(drag_layout)
        sprites_section.addWidget(drag_group)
        
        # Viewport options
        viewport_bg_group = QGroupBox("Background")
        viewport_bg_layout = QVBoxLayout()

        viewport_hint = QLabel("Set viewport background color and optional image fill.")
        viewport_hint.setStyleSheet("color: gray; font-size: 8pt;")
        viewport_hint.setWordWrap(True)
        viewport_hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        viewport_bg_layout.addWidget(viewport_hint)

        self.viewport_bg_enabled_checkbox = QCheckBox("Enable viewport background")
        self.viewport_bg_enabled_checkbox.setToolTip(
            "When disabled, the viewport background is fully transparent/empty."
        )
        self.viewport_bg_enabled_checkbox.setChecked(True)
        self.viewport_bg_enabled_checkbox.toggled.connect(self._on_viewport_bg_enabled_toggled)
        viewport_bg_layout.addWidget(self.viewport_bg_enabled_checkbox)

        bg_image_block = QHBoxLayout()
        bg_image_block.setSpacing(8)
        bg_image_controls = QVBoxLayout()
        bg_image_controls.setSpacing(4)

        self.viewport_bg_image_checkbox = QCheckBox("Use background image")
        self.viewport_bg_image_checkbox.setToolTip(
            "When enabled, the selected image is drawn behind the animation in the viewport."
        )
        self.viewport_bg_image_checkbox.toggled.connect(
            self.viewport_bg_image_enabled_changed.emit
        )
        bg_image_controls.addWidget(self.viewport_bg_image_checkbox)

        viewport_bg_image_row = QHBoxLayout()
        viewport_bg_image_row.setSpacing(6)
        self.viewport_bg_image_browse_btn = QPushButton("Import Image…")
        self.viewport_bg_image_browse_btn.clicked.connect(self._on_viewport_bg_image_browse)
        viewport_bg_image_row.addWidget(self.viewport_bg_image_browse_btn, 0)
        self.viewport_bg_image_clear_btn = QPushButton("Clear")
        self.viewport_bg_image_clear_btn.clicked.connect(self._on_viewport_bg_image_clear)
        viewport_bg_image_row.addWidget(self.viewport_bg_image_clear_btn, 0)
        viewport_bg_image_row.addStretch()
        bg_image_controls.addLayout(viewport_bg_image_row)

        bg_image_block.addLayout(bg_image_controls, 1)
        self.viewport_bg_thumbnail_label = QLabel("No\nPreview")
        self.viewport_bg_thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.viewport_bg_thumbnail_label.setFixedSize(72, 72)
        self.viewport_bg_thumbnail_label.setStyleSheet(
            "border: 1px solid #555; background: #111; color: #888; font-size: 8pt;"
        )
        bg_image_block.addWidget(
            self.viewport_bg_thumbnail_label,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        viewport_bg_layout.addLayout(bg_image_block)

        self.viewport_bg_image_label = QLabel("No background image selected.")
        self.viewport_bg_image_label.setStyleSheet("color: gray; font-size: 8pt;")
        self.viewport_bg_image_label.setWordWrap(True)
        viewport_bg_layout.addWidget(self.viewport_bg_image_label)

        self.viewport_bg_keep_aspect_checkbox = QCheckBox("Keep image aspect ratio")
        self.viewport_bg_keep_aspect_checkbox.setToolTip(
            "Draw the background image without stretching (letterboxed as needed)."
        )
        self.viewport_bg_keep_aspect_checkbox.setChecked(True)
        self.viewport_bg_keep_aspect_checkbox.toggled.connect(
            self.viewport_bg_keep_aspect_changed.emit
        )
        viewport_bg_layout.addWidget(self.viewport_bg_keep_aspect_checkbox)

        self.viewport_bg_zoom_fill_checkbox = QCheckBox("Zoom to fill viewport")
        self.viewport_bg_zoom_fill_checkbox.setToolTip(
            "Keep aspect ratio and scale image to cover the full viewport (cropping edges)."
        )
        self.viewport_bg_zoom_fill_checkbox.setChecked(False)
        self.viewport_bg_zoom_fill_checkbox.toggled.connect(
            self.viewport_bg_zoom_fill_changed.emit
        )
        viewport_bg_layout.addWidget(self.viewport_bg_zoom_fill_checkbox)

        flip_row = QHBoxLayout()
        flip_row.setSpacing(6)
        flip_row.addWidget(QLabel("Image Flip:"))
        self.viewport_bg_flip_h_btn = QPushButton("Horizontal")
        self.viewport_bg_flip_h_btn.setCheckable(True)
        self.viewport_bg_flip_h_btn.toggled.connect(self.viewport_bg_flip_h_changed.emit)
        flip_row.addWidget(self.viewport_bg_flip_h_btn)
        self.viewport_bg_flip_v_btn = QPushButton("Vertical")
        self.viewport_bg_flip_v_btn.setCheckable(True)
        self.viewport_bg_flip_v_btn.toggled.connect(self.viewport_bg_flip_v_changed.emit)
        flip_row.addWidget(self.viewport_bg_flip_v_btn)
        flip_row.addStretch()
        viewport_bg_layout.addLayout(flip_row)

        self.viewport_bg_parallax_enabled_checkbox = QCheckBox("Enable parallax")
        self.viewport_bg_parallax_enabled_checkbox.setToolTip(
            "Move/scale background with camera at reduced sensitivity for depth."
        )
        self.viewport_bg_parallax_enabled_checkbox.setChecked(True)
        self.viewport_bg_parallax_enabled_checkbox.toggled.connect(
            self.viewport_bg_parallax_enabled_changed.emit
        )
        viewport_bg_layout.addWidget(self.viewport_bg_parallax_enabled_checkbox)

        parallax_zoom_row = QHBoxLayout()
        parallax_zoom_row.setSpacing(6)
        parallax_zoom_row.addWidget(QLabel("Parallax Zoom:"))
        self.viewport_bg_parallax_zoom_spin = QDoubleSpinBox()
        self.viewport_bg_parallax_zoom_spin.setRange(0.0, 2.0)
        self.viewport_bg_parallax_zoom_spin.setDecimals(2)
        self.viewport_bg_parallax_zoom_spin.setSingleStep(0.05)
        self.viewport_bg_parallax_zoom_spin.setValue(0.5)
        self.viewport_bg_parallax_zoom_spin.setFixedWidth(90)
        self.viewport_bg_parallax_zoom_spin.setToolTip(
            "0.5 = half zoom sensitivity versus the main viewport."
        )
        self.viewport_bg_parallax_zoom_spin.valueChanged.connect(
            self.viewport_bg_parallax_zoom_strength_changed.emit
        )
        parallax_zoom_row.addWidget(self.viewport_bg_parallax_zoom_spin)
        parallax_zoom_row.addStretch()
        viewport_bg_layout.addLayout(parallax_zoom_row)

        parallax_pan_row = QHBoxLayout()
        parallax_pan_row.setSpacing(6)
        parallax_pan_row.addWidget(QLabel("Parallax Pan:"))
        self.viewport_bg_parallax_pan_spin = QDoubleSpinBox()
        self.viewport_bg_parallax_pan_spin.setRange(0.0, 2.0)
        self.viewport_bg_parallax_pan_spin.setDecimals(2)
        self.viewport_bg_parallax_pan_spin.setSingleStep(0.05)
        self.viewport_bg_parallax_pan_spin.setValue(0.5)
        self.viewport_bg_parallax_pan_spin.setFixedWidth(90)
        self.viewport_bg_parallax_pan_spin.setToolTip(
            "0.5 = half pan sensitivity versus the main viewport."
        )
        self.viewport_bg_parallax_pan_spin.valueChanged.connect(
            self.viewport_bg_parallax_pan_strength_changed.emit
        )
        parallax_pan_row.addWidget(self.viewport_bg_parallax_pan_spin)
        parallax_pan_row.addStretch()
        viewport_bg_layout.addLayout(parallax_pan_row)

        self.solid_bg_color_row = QHBoxLayout()
        self.solid_bg_color_row.setSpacing(6)
        self.solid_bg_color_btn = QPushButton("Pick Color")
        self.solid_bg_color_btn.setFixedWidth(90)
        self.solid_bg_color_btn.clicked.connect(self._on_solid_bg_pick_color)
        self.solid_bg_color_row.addWidget(self.solid_bg_color_btn, 0)
        self.solid_bg_hex_input = QLineEdit()
        self.solid_bg_hex_input.setPlaceholderText("#RRGGBBAA")
        self.solid_bg_hex_input.setMaxLength(9)
        self.solid_bg_hex_input.editingFinished.connect(self._on_solid_bg_hex_changed)
        self.solid_bg_color_row.addWidget(self.solid_bg_hex_input, 1)
        viewport_bg_layout.addLayout(self.solid_bg_color_row)

        self.solid_bg_rgba_row = QHBoxLayout()
        self.solid_bg_rgba_row.setSpacing(4)
        self.solid_bg_r_spin = self._make_channel_spinbox("R", self._on_solid_bg_spin_changed)
        self.solid_bg_g_spin = self._make_channel_spinbox("G", self._on_solid_bg_spin_changed)
        self.solid_bg_b_spin = self._make_channel_spinbox("B", self._on_solid_bg_spin_changed)
        self.solid_bg_a_spin = self._make_channel_spinbox("A", self._on_solid_bg_spin_changed)
        for label_text, spin in (("R", self.solid_bg_r_spin), ("G", self.solid_bg_g_spin),
                                 ("B", self.solid_bg_b_spin), ("A", self.solid_bg_a_spin)):
            label = QLabel(label_text)
            label.setStyleSheet("color: #666; font-size: 9pt;")
            self.solid_bg_rgba_row.addWidget(label)
            self.solid_bg_rgba_row.addWidget(spin)
        viewport_bg_layout.addLayout(self.solid_bg_rgba_row)

        self.solid_bg_auto_btn = QPushButton("Suggest Unique Color")
        self.solid_bg_auto_btn.setToolTip("Attempts to find a color not present in the current monster's textures.")
        self.solid_bg_auto_btn.clicked.connect(self.solid_bg_auto_requested.emit)
        viewport_bg_layout.addWidget(self.solid_bg_auto_btn, 0, Qt.AlignmentFlag.AlignRight)

        color_mode_row = QHBoxLayout()
        color_mode_row.setSpacing(6)
        color_mode_row.addWidget(QLabel("Solid Color Mode:"))
        self.viewport_bg_color_mode_combo = QComboBox()
        self.viewport_bg_color_mode_combo.addItem("None", "none")
        self.viewport_bg_color_mode_combo.addItem("Replace Background", "replace")
        self.viewport_bg_color_mode_combo.addItem("Overlay - Normal", "overlay_normal")
        self.viewport_bg_color_mode_combo.addItem("Overlay - Multiply", "overlay_multiply")
        self.viewport_bg_color_mode_combo.addItem("Overlay - Screen", "overlay_screen")
        self.viewport_bg_color_mode_combo.addItem("Overlay - Add", "overlay_add")
        self.viewport_bg_color_mode_combo.addItem("Overlay - Hue (Approx)", "overlay_hue")
        self.viewport_bg_color_mode_combo.addItem("Overlay - Color (Approx)", "overlay_color")
        self.viewport_bg_color_mode_combo.currentIndexChanged.connect(
            self._on_viewport_bg_color_mode_changed
        )
        color_mode_row.addWidget(self.viewport_bg_color_mode_combo, 1)
        viewport_bg_layout.addLayout(color_mode_row)

        viewport_bg_group.setLayout(viewport_bg_layout)
        viewport_section.addWidget(viewport_bg_group)

        # Export options
        export_group = QGroupBox("Export")
        export_layout = QVBoxLayout()
        
        export_frame_btn = QPushButton("Export Current Frame (PNG)")
        export_frame_btn.clicked.connect(self.export_frame_clicked.emit)
        export_layout.addWidget(export_frame_btn)

        export_sequence_btn = QPushButton("Export Animation Frames (PNG Folder)")
        export_sequence_btn.clicked.connect(self.export_frames_sequence_clicked.emit)
        export_layout.addWidget(export_sequence_btn)

        export_psd_btn = QPushButton("Export as PSD")
        export_psd_btn.clicked.connect(self.export_psd_clicked.emit)
        export_layout.addWidget(export_psd_btn)

        export_ae_btn = QPushButton("Export AE Rig (After Effects)")
        export_ae_btn.setToolTip("Export an After Effects rig package matching the current viewport.")
        export_ae_btn.clicked.connect(self.export_ae_rig_clicked.emit)
        export_layout.addWidget(export_ae_btn)

        export_mov_btn = QPushButton("Export as MOV")
        export_mov_btn.clicked.connect(self.export_mov_clicked.emit)
        export_layout.addWidget(export_mov_btn)

        export_mp4_btn = QPushButton("Export as MP4")
        export_mp4_btn.clicked.connect(self.export_mp4_clicked.emit)
        export_layout.addWidget(export_mp4_btn)

        export_webm_btn = QPushButton("Export as WEBM")
        export_webm_btn.clicked.connect(self.export_webm_clicked.emit)
        export_layout.addWidget(export_webm_btn)

        export_gif_btn = QPushButton("Export as GIF")
        export_gif_btn.clicked.connect(self.export_gif_clicked.emit)
        export_layout.addWidget(export_gif_btn)

        self.solid_bg_checkbox = QCheckBox("Fill background with solid color")
        self.solid_bg_checkbox.setToolTip("When enabled, exports are composited over a solid color instead of transparency.")
        self.solid_bg_checkbox.toggled.connect(self._on_solid_bg_toggled)
        export_layout.addWidget(self.solid_bg_checkbox)

        self.export_include_viewport_bg_checkbox = QCheckBox("Include viewport background in exports")
        self.export_include_viewport_bg_checkbox.setToolTip(
            "When enabled, exports include the current viewport background image/default pattern "
            "and solid color overlay mode."
        )
        self.export_include_viewport_bg_checkbox.toggled.connect(
            self.export_include_viewport_bg_changed.emit
        )
        export_layout.addWidget(self.export_include_viewport_bg_checkbox)
        
        export_group.setLayout(export_layout)
        export_section.addWidget(export_group)

        # Audio controls
        audio_group = QGroupBox("Audio")
        audio_layout = QVBoxLayout()

        self.audio_enable_checkbox = QCheckBox("Enable Audio")
        self.audio_enable_checkbox.setChecked(True)
        self.audio_enable_checkbox.toggled.connect(self.audio_enabled_changed.emit)
        audio_layout.addWidget(self.audio_enable_checkbox)

        volume_layout = QHBoxLayout()
        volume_layout.addWidget(QLabel("Volume:"))
        self.audio_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.audio_volume_slider.setRange(0, 100)
        self.audio_volume_slider.setValue(80)
        self.audio_volume_slider.valueChanged.connect(self.audio_volume_changed.emit)
        volume_layout.addWidget(self.audio_volume_slider)
        audio_layout.addLayout(volume_layout)

        self.audio_status_label = QLabel("Audio: not loaded")
        self.audio_status_label.setStyleSheet("color: gray; font-size: 8pt;")
        audio_layout.addWidget(self.audio_status_label)

        audio_group.setLayout(audio_layout)
        audio_section.addWidget(audio_group)

        self.audio_tracks_group = QGroupBox("Track Mixer")
        audio_tracks_layout = QVBoxLayout()

        self.audio_tracks_hint_label = QLabel("No multi-track audio for this animation.")
        self.audio_tracks_hint_label.setStyleSheet("color: gray; font-size: 8pt;")
        audio_tracks_layout.addWidget(self.audio_tracks_hint_label)

        self.audio_tracks_list = QListWidget()
        self.audio_tracks_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.audio_tracks_list.itemChanged.connect(self._on_audio_track_item_changed)
        self._audio_tracks_updating = False
        audio_tracks_layout.addWidget(self.audio_tracks_list)

        self.audio_tracks_group.setLayout(audio_tracks_layout)
        self.audio_tracks_group.setVisible(False)
        audio_section.addWidget(self.audio_tracks_group)

        presets_group = QGroupBox("Layer Offset Presets")
        presets_layout = QVBoxLayout()

        info_label = QLabel("Save/load sprite drag offsets for reuse")
        info_label.setStyleSheet("color: gray; font-size: 8pt;")
        presets_layout.addWidget(info_label)

        save_btn = QPushButton("Save Offsets...")
        save_btn.clicked.connect(self.save_offsets_clicked.emit)
        presets_layout.addWidget(save_btn)

        load_btn = QPushButton("Load Offsets...")
        load_btn.clicked.connect(self.load_offsets_clicked.emit)
        presets_layout.addWidget(load_btn)

        presets_group.setLayout(presets_layout)
        sprites_section.addWidget(presets_group)

        terrain_group = QGroupBox("Terrain Adjustments")
        terrain_layout = QVBoxLayout()

        terrain_hint = QLabel("Adjust island terrain rendering and alignment.")
        terrain_hint.setStyleSheet("color: gray; font-size: 8pt;")
        terrain_hint.setWordWrap(True)
        terrain_hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        terrain_layout.addWidget(terrain_hint)

        terrain_render_group = QGroupBox("Render Settings")
        terrain_render_layout = QVBoxLayout()

        terrain_path_layout = QHBoxLayout()
        terrain_path_layout.addWidget(QLabel("Terrain Path:"))
        self.terrain_path_combo = FullscreenSafeComboBox()
        self.terrain_path_combo.addItem("Full Quad", "full_quad")
        self.terrain_path_combo.addItem("Diamond Fan", "diamond_fan")
        self.terrain_path_combo.setToolTip("Island terrain geometry path")
        terrain_path_layout.addWidget(self.terrain_path_combo, 1)
        terrain_render_layout.addLayout(terrain_path_layout)

        terrain_filter_layout = QHBoxLayout()
        terrain_filter_layout.addWidget(QLabel("Tile Filter:"))
        self.terrain_filter_combo = FullscreenSafeComboBox()
        self.terrain_filter_combo.addItem("Nearest", "nearest")
        self.terrain_filter_combo.addItem("Linear", "linear")
        self.terrain_filter_combo.setToolTip("Terrain-pass texture sampling mode")
        terrain_filter_layout.addWidget(self.terrain_filter_combo, 1)
        terrain_render_layout.addLayout(terrain_filter_layout)

        terrain_order_layout = QHBoxLayout()
        terrain_order_layout.addWidget(QLabel("Flag Order:"))
        self.terrain_flag_order_combo = FullscreenSafeComboBox()
        self.terrain_flag_order_combo.addItem("flag0 -> flag1", "flag0_then1")
        self.terrain_flag_order_combo.addItem("flag1 -> flag0", "flag1_then0")
        self.terrain_flag_order_combo.addItem("As Is", "as_is")
        self.terrain_flag_order_combo.setToolTip("Terrain tile draw ordering across tile flags")
        terrain_order_layout.addWidget(self.terrain_flag_order_combo, 1)
        terrain_render_layout.addLayout(terrain_order_layout)

        terrain_flag1_layout = QHBoxLayout()
        terrain_flag1_layout.addWidget(QLabel("Flag1 Transform:"))
        self.terrain_flag1_transform_combo = FullscreenSafeComboBox()
        self.terrain_flag1_transform_combo.addItem("None", "none")
        self.terrain_flag1_transform_combo.addItem("HFlip", "hflip")
        self.terrain_flag1_transform_combo.addItem("VFlip", "vflip")
        self.terrain_flag1_transform_combo.addItem("HVFlip", "hvflip")
        self.terrain_flag1_transform_combo.setToolTip(
            "Transform applied to terrain tiles with non-zero flags"
        )
        terrain_flag1_layout.addWidget(self.terrain_flag1_transform_combo, 1)
        terrain_render_layout.addLayout(terrain_flag1_layout)

        terrain_render_group.setLayout(terrain_render_layout)
        terrain_layout.addWidget(terrain_render_group)

        terrain_global_group = QGroupBox("Global Transform")
        terrain_global_layout = QGridLayout()
        terrain_global_layout.setHorizontalSpacing(8)
        terrain_global_layout.setVerticalSpacing(6)

        self.terrain_global_x_spin = QDoubleSpinBox()
        self.terrain_global_x_spin.setDecimals(2)
        self.terrain_global_x_spin.setRange(-4096.0, 4096.0)
        self.terrain_global_x_spin.setSingleStep(1.0)
        self.terrain_global_x_spin.setToolTip("Global terrain X offset")
        self.terrain_global_x_spin.setPrefix("x ")
        terrain_global_layout.addWidget(QLabel("X Offset:"), 0, 0)
        terrain_global_layout.addWidget(self.terrain_global_x_spin, 0, 1)

        self.terrain_global_y_spin = QDoubleSpinBox()
        self.terrain_global_y_spin.setDecimals(2)
        self.terrain_global_y_spin.setRange(-4096.0, 4096.0)
        self.terrain_global_y_spin.setSingleStep(1.0)
        self.terrain_global_y_spin.setToolTip("Global terrain Y offset")
        self.terrain_global_y_spin.setPrefix("y ")
        terrain_global_layout.addWidget(QLabel("Y Offset:"), 1, 0)
        terrain_global_layout.addWidget(self.terrain_global_y_spin, 1, 1)

        self.terrain_global_rot_spin = QDoubleSpinBox()
        self.terrain_global_rot_spin.setDecimals(2)
        self.terrain_global_rot_spin.setRange(-360.0, 360.0)
        self.terrain_global_rot_spin.setSingleStep(1.0)
        self.terrain_global_rot_spin.setToolTip("Global terrain rotation (degrees)")
        self.terrain_global_rot_spin.setPrefix("r ")
        terrain_global_layout.addWidget(QLabel("Rotation:"), 2, 0)
        terrain_global_layout.addWidget(self.terrain_global_rot_spin, 2, 1)

        self.terrain_global_scale_spin = QDoubleSpinBox()
        self.terrain_global_scale_spin.setDecimals(3)
        self.terrain_global_scale_spin.setRange(0.01, 8.0)
        self.terrain_global_scale_spin.setSingleStep(0.01)
        self.terrain_global_scale_spin.setToolTip("Global terrain scale")
        self.terrain_global_scale_spin.setPrefix("s ")
        terrain_global_layout.addWidget(QLabel("Scale:"), 3, 0)
        terrain_global_layout.addWidget(self.terrain_global_scale_spin, 3, 1)

        terrain_global_group.setLayout(terrain_global_layout)
        terrain_layout.addWidget(terrain_global_group)

        terrain_tile_group = QGroupBox("Selected Tile Transform")
        terrain_tile_layout = QGridLayout()
        terrain_tile_layout.setHorizontalSpacing(8)
        terrain_tile_layout.setVerticalSpacing(6)

        self.terrain_tile_index_spin = QSpinBox()
        self.terrain_tile_index_spin.setRange(-1, -1)
        self.terrain_tile_index_spin.setSpecialValueText("None")
        self.terrain_tile_index_spin.setToolTip("Tile index for per-tile transform")
        terrain_tile_layout.addWidget(QLabel("Tile Index:"), 0, 0)
        terrain_tile_layout.addWidget(self.terrain_tile_index_spin, 0, 1)

        self.terrain_tile_x_spin = QDoubleSpinBox()
        self.terrain_tile_x_spin.setDecimals(2)
        self.terrain_tile_x_spin.setRange(-2048.0, 2048.0)
        self.terrain_tile_x_spin.setSingleStep(1.0)
        self.terrain_tile_x_spin.setToolTip("Selected tile X offset")
        self.terrain_tile_x_spin.setPrefix("x ")
        terrain_tile_layout.addWidget(QLabel("X Offset:"), 1, 0)
        terrain_tile_layout.addWidget(self.terrain_tile_x_spin, 1, 1)

        self.terrain_tile_y_spin = QDoubleSpinBox()
        self.terrain_tile_y_spin.setDecimals(2)
        self.terrain_tile_y_spin.setRange(-2048.0, 2048.0)
        self.terrain_tile_y_spin.setSingleStep(1.0)
        self.terrain_tile_y_spin.setToolTip("Selected tile Y offset")
        self.terrain_tile_y_spin.setPrefix("y ")
        terrain_tile_layout.addWidget(QLabel("Y Offset:"), 2, 0)
        terrain_tile_layout.addWidget(self.terrain_tile_y_spin, 2, 1)

        self.terrain_tile_rot_spin = QDoubleSpinBox()
        self.terrain_tile_rot_spin.setDecimals(2)
        self.terrain_tile_rot_spin.setRange(-360.0, 360.0)
        self.terrain_tile_rot_spin.setSingleStep(1.0)
        self.terrain_tile_rot_spin.setToolTip("Selected tile rotation (degrees)")
        self.terrain_tile_rot_spin.setPrefix("r ")
        terrain_tile_layout.addWidget(QLabel("Rotation:"), 3, 0)
        terrain_tile_layout.addWidget(self.terrain_tile_rot_spin, 3, 1)

        self.terrain_tile_scale_spin = QDoubleSpinBox()
        self.terrain_tile_scale_spin.setDecimals(3)
        self.terrain_tile_scale_spin.setRange(0.01, 8.0)
        self.terrain_tile_scale_spin.setSingleStep(0.01)
        self.terrain_tile_scale_spin.setToolTip("Selected tile scale")
        self.terrain_tile_scale_spin.setPrefix("s ")
        terrain_tile_layout.addWidget(QLabel("Scale:"), 4, 0)
        terrain_tile_layout.addWidget(self.terrain_tile_scale_spin, 4, 1)

        terrain_tile_group.setLayout(terrain_tile_layout)
        terrain_layout.addWidget(terrain_tile_group)

        terrain_group.setLayout(terrain_layout)
        terrain_section.addWidget(terrain_group)

        constraints_group = QGroupBox("Constraints")
        constraints_layout = QVBoxLayout()

        self.constraints_enable_checkbox = QCheckBox("Enable Constraints")
        self.constraints_enable_checkbox.setToolTip("Toggle the constraint system globally.")
        self.constraints_enable_checkbox.toggled.connect(self.constraints_enabled_changed.emit)
        constraints_layout.addWidget(self.constraints_enable_checkbox)

        self.constraints_list = QListWidget()
        self.constraints_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.constraints_list.itemChanged.connect(self._on_constraint_item_changed)
        self.constraints_list.currentItemChanged.connect(self._on_constraint_selection_changed)
        constraints_layout.addWidget(self.constraints_list)

        constraint_button_row = QHBoxLayout()
        self.constraint_add_btn = QPushButton("Add")
        self.constraint_add_btn.clicked.connect(self.constraint_add_requested.emit)
        constraint_button_row.addWidget(self.constraint_add_btn)

        self.constraint_edit_btn = QPushButton("Edit")
        self.constraint_edit_btn.setEnabled(False)
        self.constraint_edit_btn.clicked.connect(self._emit_constraint_edit)
        constraint_button_row.addWidget(self.constraint_edit_btn)

        self.constraint_remove_btn = QPushButton("Remove")
        self.constraint_remove_btn.setEnabled(False)
        self.constraint_remove_btn.clicked.connect(self._emit_constraint_remove)
        constraint_button_row.addWidget(self.constraint_remove_btn)

        constraints_layout.addLayout(constraint_button_row)
        constraints_group.setLayout(constraints_layout)
        rigging_section.addWidget(constraints_group)

        joint_group = QGroupBox("Joint Solver")
        joint_layout = QVBoxLayout()

        self.joint_solver_checkbox = QCheckBox("Enable Joint Solver")
        self.joint_solver_checkbox.setToolTip("Maintain parent-child joint distances while dragging.")
        self.joint_solver_checkbox.toggled.connect(self.joint_solver_enabled_changed.emit)
        joint_layout.addWidget(self.joint_solver_checkbox)

        self.joint_solver_parented_checkbox = QCheckBox("Follow Parent Transforms")
        self.joint_solver_parented_checkbox.setToolTip(
            "Use parent transforms when solving joints so children follow their parents."
        )
        self.joint_solver_parented_checkbox.setChecked(True)
        self.joint_solver_parented_checkbox.toggled.connect(self.joint_solver_parented_changed.emit)
        joint_layout.addWidget(self.joint_solver_parented_checkbox)

        self.propagate_user_transforms_checkbox = QCheckBox("Propagate Offsets to Children")
        self.propagate_user_transforms_checkbox.setToolTip(
            "Apply user position/rotation/scale offsets to child layers."
        )
        self.propagate_user_transforms_checkbox.setChecked(True)
        self.propagate_user_transforms_checkbox.toggled.connect(
            self.propagate_user_transforms_changed.emit
        )
        joint_layout.addWidget(self.propagate_user_transforms_checkbox)

        iter_layout = QHBoxLayout()
        iter_layout.addWidget(QLabel("Iterations:"))
        self.joint_solver_iterations_spin = QSpinBox()
        self.joint_solver_iterations_spin.setRange(1, 50)
        self.joint_solver_iterations_spin.setValue(8)
        self.joint_solver_iterations_spin.valueChanged.connect(self.joint_solver_iterations_changed.emit)
        iter_layout.addWidget(self.joint_solver_iterations_spin)
        joint_layout.addLayout(iter_layout)

        strength_layout = QHBoxLayout()
        strength_layout.addWidget(QLabel("Strength:"))
        self.joint_solver_strength_spin = QDoubleSpinBox()
        self.joint_solver_strength_spin.setRange(0.0, 1.0)
        self.joint_solver_strength_spin.setSingleStep(0.05)
        self.joint_solver_strength_spin.setDecimals(2)
        self.joint_solver_strength_spin.setValue(1.0)
        self.joint_solver_strength_spin.valueChanged.connect(self.joint_solver_strength_changed.emit)
        strength_layout.addWidget(self.joint_solver_strength_spin)
        joint_layout.addLayout(strength_layout)

        joint_button_row = QHBoxLayout()
        self.joint_solver_capture_btn = QPushButton("Capture Rest")
        self.joint_solver_capture_btn.clicked.connect(self.joint_solver_capture_requested.emit)
        joint_button_row.addWidget(self.joint_solver_capture_btn)
        self.joint_solver_bake_current_btn = QPushButton("Bake Current")
        self.joint_solver_bake_current_btn.clicked.connect(self.joint_solver_bake_current_requested.emit)
        joint_button_row.addWidget(self.joint_solver_bake_current_btn)
        self.joint_solver_bake_range_btn = QPushButton("Bake Range")
        self.joint_solver_bake_range_btn.clicked.connect(self.joint_solver_bake_range_requested.emit)
        joint_button_row.addWidget(self.joint_solver_bake_range_btn)
        joint_layout.addLayout(joint_button_row)

        joint_group.setLayout(joint_layout)
        rigging_section.addWidget(joint_group)

        diag_group = QGroupBox("Diagnostics")
        diag_layout = QVBoxLayout()

        self.diag_enable_checkbox = QCheckBox("Enable diagnostics logging")
        self.diag_enable_checkbox.setToolTip("Toggle the runtime diagnostics overlay/logging system.")
        self.diag_enable_checkbox.toggled.connect(self.diagnostics_enabled_changed.emit)
        diag_layout.addWidget(self.diag_enable_checkbox)

        self.diag_refresh_button = QPushButton("Refresh Layer Diagnostics")
        self.diag_refresh_button.clicked.connect(self.diagnostics_refresh_requested.emit)
        diag_layout.addWidget(self.diag_refresh_button)

        self.diag_export_button = QPushButton("Export Diagnostics Log")
        self.diag_export_button.clicked.connect(self.diagnostics_export_requested.emit)
        diag_layout.addWidget(self.diag_export_button)

        diag_hint = QLabel("Configure advanced logging in Settings → Diagnostics.")
        diag_hint.setStyleSheet("color: gray; font-size: 8pt;")
        diag_hint.setWordWrap(True)
        diag_hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        diag_layout.addWidget(diag_hint)

        diag_group.setLayout(diag_layout)
        system_section.addWidget(diag_group)

        interface_group = QGroupBox("Interface")
        interface_layout = QVBoxLayout()

        self.compact_ui_checkbox = QCheckBox("Compact UI")
        self.compact_ui_checkbox.setToolTip("Reduce padding and row heights for smaller screens.")
        self.compact_ui_checkbox.toggled.connect(self.compact_ui_toggled.emit)
        interface_layout.addWidget(self.compact_ui_checkbox)

        interface_group.setLayout(interface_layout)
        system_section.addWidget(interface_group)
        
        # About section
        about_group = QGroupBox("About")
        about_layout = QVBoxLayout()
        
        credits_btn = QPushButton("Credits && Acknowledgments")
        credits_btn.clicked.connect(self.credits_clicked.emit)
        about_layout.addWidget(credits_btn)
        
        about_group.setLayout(about_layout)
        system_section.addWidget(about_group)

        for section_layout in self._section_layouts.values():
            section_layout.addStretch()

        self._solid_bg_color = (0, 0, 0, 255)
        self._solid_bg_update_guard = False
        self._viewport_bg_image_path = ""
        self.set_solid_bg_color(self._solid_bg_color)
        self.set_viewport_bg_color_mode("none")
        self.set_export_include_viewport_bg(False)
        self.set_viewport_bg_image("", False)
        self.set_viewport_bg_keep_aspect(True)
        self.set_viewport_bg_zoom_fill(False)
        self.set_viewport_bg_flips(False, False)
        self.set_viewport_bg_parallax_enabled(True)
        self.set_viewport_bg_parallax_zoom_strength(0.5)
        self.set_viewport_bg_parallax_pan_strength(0.5)
        self.set_viewport_bg_enabled(True)
        self._wire_control_signals()

    def update_audio_status(self, message: str, success: bool = False):
        """Update the inline audio status label."""
        color = "#32a852" if success else ("#d64541" if message else "gray")
        text = message if message else "Audio: not available"
        if not text.lower().startswith("audio"):
            text = f"Audio: {text}"
        self.audio_status_label.setText(text)
        self.audio_status_label.setStyleSheet(f"font-size: 8pt; color: {color};")

    def set_audio_track_options(
        self,
        tracks: List[Tuple[str, str]],
        *,
        muted_track_ids: Optional[set] = None,
        context_label: Optional[str] = None,
    ) -> None:
        """Populate per-track mute controls for multi-track audio."""
        muted = set(muted_track_ids or set())
        self._audio_tracks_updating = True
        try:
            self.audio_tracks_list.clear()
            for track_id, display in tracks:
                if not track_id:
                    continue
                item = QListWidgetItem(display or track_id)
                item.setData(Qt.ItemDataRole.UserRole, track_id)
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(
                    Qt.CheckState.Unchecked if track_id in muted else Qt.CheckState.Checked
                )
                self.audio_tracks_list.addItem(item)
        finally:
            self._audio_tracks_updating = False

        if tracks and len(tracks) > 1:
            if context_label:
                self.audio_tracks_hint_label.setText(f"Muted tracks for {context_label} are skipped.")
            else:
                self.audio_tracks_hint_label.setText("Uncheck a track to mute it.")
            self.audio_tracks_group.setVisible(True)
        else:
            self.audio_tracks_hint_label.setText("No multi-track audio for this animation.")
            self.audio_tracks_group.setVisible(False)

    def _on_audio_track_item_changed(self, item: QListWidgetItem) -> None:
        if self._audio_tracks_updating:
            return
        track_id = item.data(Qt.ItemDataRole.UserRole)
        if not track_id:
            return
        muted = item.checkState() != Qt.CheckState.Checked
        self.audio_track_mute_changed.emit(str(track_id), bool(muted))

    def _wire_control_signals(self) -> None:
        # Placement control signal wiring
        self.translation_spin.valueChanged.connect(self._on_translation_spin_changed)
        self.translation_slider.valueChanged.connect(self._on_translation_slider_changed)
        self.rotation_spin.valueChanged.connect(self._on_rotation_spin_changed)
        self.rotation_slider.valueChanged.connect(self._on_rotation_slider_changed)
        self.rotation_overlay_spin.valueChanged.connect(self._on_rotation_overlay_spin_changed)
        self.rotation_overlay_slider.valueChanged.connect(self._on_rotation_overlay_slider_changed)
        self.rotation_gizmo_checkbox.toggled.connect(self.rotation_gizmo_toggled.emit)
        self.bpm_spin.valueChanged.connect(self._on_bpm_spin_changed)
        self.bpm_slider.valueChanged.connect(self._on_bpm_slider_changed)
        self.sync_audio_checkbox.toggled.connect(self.sync_audio_to_bpm_toggled.emit)
        self.pitch_shift_checkbox.toggled.connect(self.pitch_shift_toggled.emit)
        self.metronome_checkbox.toggled.connect(self.metronome_toggled.emit)
        self.metronome_audible_checkbox.toggled.connect(self.metronome_audible_toggled.emit)
        self.reset_bpm_button.clicked.connect(self.bpm_reset_requested.emit)
        self.lock_bpm_button.clicked.connect(self.base_bpm_lock_requested.emit)
        self.anchor_precision_spin.valueChanged.connect(self._on_anchor_precision_spin_changed)
        self.anchor_precision_slider.valueChanged.connect(self._on_anchor_precision_slider_changed)
        self.anchor_bias_x_spin.valueChanged.connect(self._on_anchor_bias_x_spin_changed)
        self.anchor_bias_x_slider.valueChanged.connect(self._on_anchor_bias_x_slider_changed)
        self.anchor_bias_y_spin.valueChanged.connect(self._on_anchor_bias_y_spin_changed)
        self.anchor_bias_y_slider.valueChanged.connect(self._on_anchor_bias_y_slider_changed)
        self.anchor_scale_x_spin.valueChanged.connect(self._on_anchor_scale_x_spin_changed)
        self.anchor_scale_x_slider.valueChanged.connect(self._on_anchor_scale_x_slider_changed)
        self.anchor_scale_y_spin.valueChanged.connect(self._on_anchor_scale_y_spin_changed)
        self.anchor_scale_y_slider.valueChanged.connect(self._on_anchor_scale_y_slider_changed)
        self.local_pos_spin.valueChanged.connect(self._on_local_pos_spin_changed)
        self.local_pos_slider.valueChanged.connect(self._on_local_pos_slider_changed)
        self.parent_mix_spin.valueChanged.connect(self._on_parent_mix_spin_changed)
        self.parent_mix_slider.valueChanged.connect(self._on_parent_mix_slider_changed)
        self.rotation_bias_spin.valueChanged.connect(self._on_rotation_bias_spin_changed)
        self.rotation_bias_slider.valueChanged.connect(self._on_rotation_bias_slider_changed)
        self.scale_bias_x_spin.valueChanged.connect(self._on_scale_bias_x_spin_changed)
        self.scale_bias_x_slider.valueChanged.connect(self._on_scale_bias_x_slider_changed)
        self.scale_bias_y_spin.valueChanged.connect(self._on_scale_bias_y_spin_changed)
        self.scale_bias_y_slider.valueChanged.connect(self._on_scale_bias_y_slider_changed)
        self.world_offset_x_spin.valueChanged.connect(self._on_world_offset_x_spin_changed)
        self.world_offset_x_slider.valueChanged.connect(self._on_world_offset_x_slider_changed)
        self.world_offset_y_spin.valueChanged.connect(self._on_world_offset_y_spin_changed)
        self.world_offset_y_slider.valueChanged.connect(self._on_world_offset_y_slider_changed)
        self.particle_origin_x_spin.valueChanged.connect(self._on_particle_origin_x_spin_changed)
        self.particle_origin_x_slider.valueChanged.connect(self._on_particle_origin_x_slider_changed)
        self.particle_origin_y_spin.valueChanged.connect(self._on_particle_origin_y_spin_changed)
        self.particle_origin_y_slider.valueChanged.connect(self._on_particle_origin_y_slider_changed)
        self.trim_shift_spin.valueChanged.connect(self._on_trim_shift_spin_changed)
        self.trim_shift_slider.valueChanged.connect(self._on_trim_shift_slider_changed)

    def _make_channel_spinbox(self, name: str, handler):
        spin = QSpinBox()
        spin.setRange(0, 255)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedWidth(55)
        spin.valueChanged.connect(handler)
        spin.setToolTip(f"{name} channel (0-255)")
        return spin

    def _on_solid_bg_toggled(self, enabled: bool):
        self.solid_bg_enabled_changed.emit(enabled)

    def _on_viewport_bg_enabled_toggled(self, enabled: bool):
        self._set_viewport_bg_controls_enabled(enabled)
        self.viewport_bg_enabled_changed.emit(bool(enabled))

    def _set_solid_bg_controls_enabled(self, enabled: bool):
        # Background color controls now live under the Viewport tab and remain editable
        # regardless of export fill toggle state.
        _ = enabled

    def _set_viewport_bg_controls_enabled(self, enabled: bool):
        controls = (
            self.viewport_bg_image_checkbox,
            self.viewport_bg_image_browse_btn,
            self.viewport_bg_image_clear_btn,
            self.viewport_bg_keep_aspect_checkbox,
            self.viewport_bg_zoom_fill_checkbox,
            self.viewport_bg_flip_h_btn,
            self.viewport_bg_flip_v_btn,
            self.viewport_bg_parallax_enabled_checkbox,
            self.viewport_bg_parallax_zoom_spin,
            self.viewport_bg_parallax_pan_spin,
            self.solid_bg_color_btn,
            self.solid_bg_hex_input,
            self.solid_bg_r_spin,
            self.solid_bg_g_spin,
            self.solid_bg_b_spin,
            self.solid_bg_a_spin,
            self.solid_bg_auto_btn,
            self.viewport_bg_color_mode_combo,
        )
        for widget in controls:
            widget.setEnabled(bool(enabled))

    def _on_viewport_bg_color_mode_changed(self, index: int):
        mode = self.viewport_bg_color_mode_combo.itemData(index)
        if mode is None:
            mode = "none"
        self.viewport_bg_color_mode_changed.emit(str(mode))

    def _on_viewport_bg_image_browse(self):
        filters = "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tga *.svg);;All Files (*)"
        image_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Viewport Background Image",
            "",
            filters,
        )
        if not image_path:
            return
        self.set_viewport_bg_image(image_path, self.viewport_bg_image_checkbox.isChecked())
        self.viewport_bg_image_changed.emit(image_path)

    def _on_viewport_bg_image_clear(self):
        self.set_viewport_bg_image("", self.viewport_bg_image_checkbox.isChecked())
        self.viewport_bg_image_changed.emit("")

    def _update_viewport_bg_thumbnail(self, image_path: str):
        if image_path:
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                thumb = pixmap.scaled(
                    self.viewport_bg_thumbnail_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.viewport_bg_thumbnail_label.setPixmap(thumb)
                self.viewport_bg_thumbnail_label.setText("")
                return
        self.viewport_bg_thumbnail_label.setPixmap(QPixmap())
        self.viewport_bg_thumbnail_label.setText("No\nPreview")

    def _on_solid_bg_pick_color(self):
        from PyQt6.QtGui import QColor
        color = QColor(*self._solid_bg_color)
        selected = QColorDialog.getColor(
            color,
            self,
            "Select Background Color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if selected.isValid():
            self._set_solid_bg_color(
                (selected.red(), selected.green(), selected.blue(), selected.alpha()),
                emit=True,
            )

    def _on_solid_bg_hex_changed(self):
        if self._solid_bg_update_guard:
            return
        value = self.solid_bg_hex_input.text().strip().lstrip("#")
        if len(value) == 6:
            value += "FF"
        if len(value) != 8:
            self._set_solid_bg_color(self._solid_bg_color, emit=False)
            return
        try:
            r = int(value[0:2], 16)
            g = int(value[2:4], 16)
            b = int(value[4:6], 16)
            a = int(value[6:8], 16)
        except ValueError:
            self._set_solid_bg_color(self._solid_bg_color, emit=False)
            return
        self._set_solid_bg_color((r, g, b, a), emit=True)

    def _on_solid_bg_spin_changed(self, _value: int):
        if self._solid_bg_update_guard:
            return
        rgba = (
            int(self.solid_bg_r_spin.value()),
            int(self.solid_bg_g_spin.value()),
            int(self.solid_bg_b_spin.value()),
            int(self.solid_bg_a_spin.value()),
        )
        self._set_solid_bg_color(rgba, emit=True)

    def _set_solid_bg_color(self, rgba: Tuple[int, int, int, int], *, emit: bool):
        r = max(0, min(255, int(rgba[0])))
        g = max(0, min(255, int(rgba[1])))
        b = max(0, min(255, int(rgba[2])))
        a = max(0, min(255, int(rgba[3])))
        self._solid_bg_color = (r, g, b, a)
        self._solid_bg_update_guard = True
        self.solid_bg_r_spin.setValue(r)
        self.solid_bg_g_spin.setValue(g)
        self.solid_bg_b_spin.setValue(b)
        self.solid_bg_a_spin.setValue(a)
        hex_value = f"#{r:02X}{g:02X}{b:02X}{a:02X}"
        self.solid_bg_hex_input.setText(hex_value)
        self.solid_bg_color_btn.setStyleSheet(
            f"""
            QPushButton {{
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 6px;
                background-color: rgba({r}, {g}, {b}, {a});
            }}
            QPushButton:disabled {{
                background-color: palette(button);
                color: #999;
            }}
            """
        )
        self._solid_bg_update_guard = False
        if emit:
            self.solid_bg_color_changed.emit(r, g, b, a)

    def set_solid_bg_enabled(self, enabled: bool):
        self.solid_bg_checkbox.blockSignals(True)
        self.solid_bg_checkbox.setChecked(enabled)
        self.solid_bg_checkbox.blockSignals(False)
        self._set_solid_bg_controls_enabled(enabled)

    def set_solid_bg_color(self, rgba: Tuple[int, int, int, int]):
        self._set_solid_bg_color(rgba, emit=False)

    def set_viewport_bg_enabled(self, enabled: bool):
        self.viewport_bg_enabled_checkbox.blockSignals(True)
        self.viewport_bg_enabled_checkbox.setChecked(bool(enabled))
        self.viewport_bg_enabled_checkbox.blockSignals(False)
        self._set_viewport_bg_controls_enabled(bool(enabled))

    def set_viewport_bg_keep_aspect(self, enabled: bool):
        self.viewport_bg_keep_aspect_checkbox.blockSignals(True)
        self.viewport_bg_keep_aspect_checkbox.setChecked(bool(enabled))
        self.viewport_bg_keep_aspect_checkbox.blockSignals(False)

    def set_viewport_bg_zoom_fill(self, enabled: bool):
        self.viewport_bg_zoom_fill_checkbox.blockSignals(True)
        self.viewport_bg_zoom_fill_checkbox.setChecked(bool(enabled))
        self.viewport_bg_zoom_fill_checkbox.blockSignals(False)

    def set_viewport_bg_parallax_enabled(self, enabled: bool):
        self.viewport_bg_parallax_enabled_checkbox.blockSignals(True)
        self.viewport_bg_parallax_enabled_checkbox.setChecked(bool(enabled))
        self.viewport_bg_parallax_enabled_checkbox.blockSignals(False)

    def set_viewport_bg_parallax_zoom_strength(self, value: float):
        self.viewport_bg_parallax_zoom_spin.blockSignals(True)
        self.viewport_bg_parallax_zoom_spin.setValue(max(0.0, min(2.0, float(value))))
        self.viewport_bg_parallax_zoom_spin.blockSignals(False)

    def set_viewport_bg_parallax_pan_strength(self, value: float):
        self.viewport_bg_parallax_pan_spin.blockSignals(True)
        self.viewport_bg_parallax_pan_spin.setValue(max(0.0, min(2.0, float(value))))
        self.viewport_bg_parallax_pan_spin.blockSignals(False)

    def set_viewport_bg_flips(self, flip_h: bool, flip_v: bool):
        self.viewport_bg_flip_h_btn.blockSignals(True)
        self.viewport_bg_flip_h_btn.setChecked(bool(flip_h))
        self.viewport_bg_flip_h_btn.blockSignals(False)
        self.viewport_bg_flip_v_btn.blockSignals(True)
        self.viewport_bg_flip_v_btn.setChecked(bool(flip_v))
        self.viewport_bg_flip_v_btn.blockSignals(False)

    def set_viewport_bg_image(self, path: str, enabled: bool):
        normalized_path = (path or "").strip()
        self._viewport_bg_image_path = normalized_path
        self.viewport_bg_image_checkbox.blockSignals(True)
        self.viewport_bg_image_checkbox.setChecked(bool(enabled))
        self.viewport_bg_image_checkbox.blockSignals(False)
        self._update_viewport_bg_thumbnail(normalized_path)
        viewport_enabled = bool(self.viewport_bg_enabled_checkbox.isChecked())
        if normalized_path:
            display_path = normalized_path
            if len(display_path) > 88:
                display_path = f"...{display_path[-85:]}"
            self.viewport_bg_image_label.setText(display_path)
            self.viewport_bg_image_label.setToolTip(normalized_path)
            self.viewport_bg_image_clear_btn.setEnabled(viewport_enabled)
        else:
            self.viewport_bg_image_label.setText("No background image selected.")
            self.viewport_bg_image_label.setToolTip("")
            self.viewport_bg_image_clear_btn.setEnabled(False)

    def set_viewport_bg_color_mode(self, mode: str):
        normalized = str(mode or "none").strip().lower()
        idx = self.viewport_bg_color_mode_combo.findData(normalized)
        if idx < 0:
            idx = self.viewport_bg_color_mode_combo.findData("none")
        self.viewport_bg_color_mode_combo.blockSignals(True)
        self.viewport_bg_color_mode_combo.setCurrentIndex(max(0, idx))
        self.viewport_bg_color_mode_combo.blockSignals(False)

    def set_export_include_viewport_bg(self, enabled: bool):
        self.export_include_viewport_bg_checkbox.blockSignals(True)
        self.export_include_viewport_bg_checkbox.setChecked(bool(enabled))
        self.export_include_viewport_bg_checkbox.blockSignals(False)

    def reset_placement_bias_settings(self):
        """Reset all placement bias controls to their default values."""
        self.anchor_precision_spin.setValue(0.25)
        self.anchor_bias_x_spin.setValue(0.0)
        self.anchor_bias_y_spin.setValue(0.0)
        self.anchor_flip_x_checkbox.setChecked(False)
        self.anchor_flip_y_checkbox.setChecked(False)
        self.anchor_scale_x_spin.setValue(1.0)
        self.anchor_scale_y_spin.setValue(1.0)
        self.local_pos_spin.setValue(1.0)
        self.parent_mix_spin.setValue(1.0)
        self.rotation_bias_spin.setValue(0.0)
        self.scale_bias_x_spin.setValue(1.0)
        self.scale_bias_y_spin.setValue(1.0)
        self.world_offset_x_spin.setValue(0.0)
        self.world_offset_y_spin.setValue(0.0)
        self.particle_origin_x_spin.setValue(0.0)
        self.particle_origin_y_spin.setValue(0.0)
        self.trim_shift_spin.setValue(1.0)

    def set_bpm_value(self, value: float):
        """Synchronize BPM controls without emitting change signals."""
        clamped = max(20.0, min(300.0, value))
        slider_val = int(clamped * 10)
        self.bpm_spin.blockSignals(True)
        self.bpm_spin.setValue(clamped)
        self.bpm_spin.blockSignals(False)
        self.bpm_slider.blockSignals(True)
        self.bpm_slider.setValue(slider_val)
        self.bpm_slider.blockSignals(False)

    def set_sync_audio_checkbox(self, enabled: bool):
        """Set sync-audio toggle without feedback."""
        self.sync_audio_checkbox.blockSignals(True)
        self.sync_audio_checkbox.setChecked(enabled)
        self.sync_audio_checkbox.blockSignals(False)

    def set_pitch_shift_checkbox(self, enabled: bool):
        """Set pitch-shift toggle without feedback."""
        self.pitch_shift_checkbox.blockSignals(True)
        self.pitch_shift_checkbox.setChecked(enabled)
        self.pitch_shift_checkbox.blockSignals(False)

    def set_metronome_checkbox(self, enabled: bool):
        """Set the metronome toggle without emitting signals."""
        self.metronome_checkbox.blockSignals(True)
        self.metronome_checkbox.setChecked(enabled)
        self.metronome_checkbox.blockSignals(False)

    def set_metronome_audible_checkbox(self, enabled: bool):
        """Set audible tick toggle without feedback."""
        self.metronome_audible_checkbox.blockSignals(True)
        self.metronome_audible_checkbox.setChecked(enabled)
        self.metronome_audible_checkbox.blockSignals(False)

    def pulse_metronome_indicator(self, downbeat: bool = False):
        """Briefly flash the metronome indicator to show a beat."""
        color = "#00f5a0" if downbeat else "#36c1ff"
        self.metronome_indicator.setStyleSheet(f"color: {color}; font: bold 16pt;")
        if self._metronome_flash_timer is None:
            self._metronome_flash_timer = QTimer(self)
            self._metronome_flash_timer.setSingleShot(True)
            self._metronome_flash_timer.timeout.connect(self._reset_metronome_indicator)
        self._metronome_flash_timer.start(120)

    def _reset_metronome_indicator(self):
        self.metronome_indicator.setStyleSheet(
            "color: rgba(255, 255, 255, 0.25); font: bold 16pt;"
        )

    def _emit_time_signature_change(self):
        denom = self.metronome_time_sig_denom.currentData(Qt.ItemDataRole.UserRole)
        if denom is None:
            denom = 4
        self.time_signature_changed.emit(
            self.metronome_time_sig_num.value(),
            int(denom),
        )

    def set_time_signature(self, numerator: int, denominator: int):
        """Update time-signature controls without emitting signals."""
        numerator = max(1, int(numerator))
        idx = self.metronome_time_sig_denom.findData(int(denominator))
        if idx < 0:
            idx = self.metronome_time_sig_denom.findData(4)
        self.metronome_time_sig_num.blockSignals(True)
        self.metronome_time_sig_denom.blockSignals(True)
        self.metronome_time_sig_num.setValue(numerator)
        if idx >= 0:
            self.metronome_time_sig_denom.setCurrentIndex(idx)
        self.metronome_time_sig_num.blockSignals(False)
        self.metronome_time_sig_denom.blockSignals(False)

    def _on_translation_spin_changed(self, value: float):
        """Sync translation slider with spinbox and emit change."""
        self.translation_slider.blockSignals(True)
        self.translation_slider.setValue(int(value * 100))
        self.translation_slider.blockSignals(False)
        self.translation_sensitivity_changed.emit(value)

    def _on_translation_slider_changed(self, slider_value: int):
        """Sync translation spinbox with slider and emit change."""
        value = slider_value / 100.0
        self.translation_spin.blockSignals(True)
        self.translation_spin.setValue(value)
        self.translation_spin.blockSignals(False)
        self.translation_sensitivity_changed.emit(value)

    def _on_rotation_spin_changed(self, value: float):
        """Sync rotation slider with spinbox and emit change."""
        self.rotation_slider.blockSignals(True)
        self.rotation_slider.setValue(int(value * 10))
        self.rotation_slider.blockSignals(False)
        self.rotation_sensitivity_changed.emit(value)

    def _on_rotation_slider_changed(self, slider_value: int):
        """Sync rotation spinbox with slider and emit change."""
        value = slider_value / 10.0
        self.rotation_spin.blockSignals(True)
        self.rotation_spin.setValue(value)
        self.rotation_spin.blockSignals(False)
        self.rotation_sensitivity_changed.emit(value)

    def _on_rotation_overlay_spin_changed(self, value: float):
        """Sync overlay size slider with spinbox and emit change."""
        self.rotation_overlay_slider.blockSignals(True)
        self.rotation_overlay_slider.setValue(int(value))
        self.rotation_overlay_slider.blockSignals(False)
        self.rotation_overlay_size_changed.emit(value)

    def _on_rotation_overlay_slider_changed(self, slider_value: int):
        """Sync overlay size spinbox with slider and emit change."""
        value = float(slider_value)
        self.rotation_overlay_spin.blockSignals(True)
        self.rotation_overlay_spin.setValue(value)
        self.rotation_overlay_spin.blockSignals(False)
        self.rotation_overlay_size_changed.emit(value)

    def _on_bpm_spin_changed(self, value: float):
        """Sync BPM slider with spinbox and emit change."""
        slider_value = int(value * 10)
        self.bpm_slider.blockSignals(True)
        self.bpm_slider.setValue(slider_value)
        self.bpm_slider.blockSignals(False)
        self.bpm_value_changed.emit(value)

    def _on_bpm_slider_changed(self, slider_value: int):
        """Sync BPM spinbox with slider and emit change."""
        value = slider_value / 10.0
        self.bpm_spin.blockSignals(True)
        self.bpm_spin.setValue(value)
        self.bpm_spin.blockSignals(False)
        self.bpm_value_changed.emit(value)

    def _on_anchor_precision_spin_changed(self, value: float):
        """Sync anchor precision slider with spinbox and emit change."""
        self.anchor_precision_slider.blockSignals(True)
        self.anchor_precision_slider.setValue(int(value * 100))
        self.anchor_precision_slider.blockSignals(False)
        self.anchor_drag_precision_changed.emit(value)

    def _on_anchor_precision_slider_changed(self, slider_value: int):
        """Sync anchor precision spinbox with slider and emit change."""
        value = slider_value / 100.0
        self.anchor_precision_spin.blockSignals(True)
        self.anchor_precision_spin.setValue(value)
        self.anchor_precision_spin.blockSignals(False)
        self.anchor_drag_precision_changed.emit(value)

    def _on_anchor_bias_x_spin_changed(self, value: float):
        self.anchor_bias_x_slider.blockSignals(True)
        self.anchor_bias_x_slider.setValue(int(value * 100))
        self.anchor_bias_x_slider.blockSignals(False)
        self.anchor_bias_x_changed.emit(value)

    def _on_anchor_bias_x_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.anchor_bias_x_spin.blockSignals(True)
        self.anchor_bias_x_spin.setValue(value)
        self.anchor_bias_x_spin.blockSignals(False)
        self.anchor_bias_x_changed.emit(value)

    def _on_anchor_bias_y_spin_changed(self, value: float):
        self.anchor_bias_y_slider.blockSignals(True)
        self.anchor_bias_y_slider.setValue(int(value * 100))
        self.anchor_bias_y_slider.blockSignals(False)
        self.anchor_bias_y_changed.emit(value)

    def _on_anchor_bias_y_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.anchor_bias_y_spin.blockSignals(True)
        self.anchor_bias_y_spin.setValue(value)
        self.anchor_bias_y_spin.blockSignals(False)
        self.anchor_bias_y_changed.emit(value)

    def _on_anchor_scale_x_spin_changed(self, value: float):
        self.anchor_scale_x_slider.blockSignals(True)
        self.anchor_scale_x_slider.setValue(int(value * 100))
        self.anchor_scale_x_slider.blockSignals(False)
        self.anchor_scale_x_changed.emit(value)

    def _on_anchor_scale_x_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.anchor_scale_x_spin.blockSignals(True)
        self.anchor_scale_x_spin.setValue(value)
        self.anchor_scale_x_spin.blockSignals(False)
        self.anchor_scale_x_changed.emit(value)

    def _on_anchor_scale_y_spin_changed(self, value: float):
        self.anchor_scale_y_slider.blockSignals(True)
        self.anchor_scale_y_slider.setValue(int(value * 100))
        self.anchor_scale_y_slider.blockSignals(False)
        self.anchor_scale_y_changed.emit(value)

    def _on_anchor_scale_y_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.anchor_scale_y_spin.blockSignals(True)
        self.anchor_scale_y_spin.setValue(value)
        self.anchor_scale_y_spin.blockSignals(False)
        self.anchor_scale_y_changed.emit(value)

    def _on_local_pos_spin_changed(self, value: float):
        self.local_pos_slider.blockSignals(True)
        self.local_pos_slider.setValue(int(value * 100))
        self.local_pos_slider.blockSignals(False)
        self.local_position_multiplier_changed.emit(value)

    def _on_local_pos_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.local_pos_spin.blockSignals(True)
        self.local_pos_spin.setValue(value)
        self.local_pos_spin.blockSignals(False)
        self.local_position_multiplier_changed.emit(value)

    def _on_parent_mix_spin_changed(self, value: float):
        self.parent_mix_slider.blockSignals(True)
        self.parent_mix_slider.setValue(int(value * 100))
        self.parent_mix_slider.blockSignals(False)
        self.parent_mix_changed.emit(value)

    def _on_parent_mix_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.parent_mix_spin.blockSignals(True)
        self.parent_mix_spin.setValue(value)
        self.parent_mix_spin.blockSignals(False)
        self.parent_mix_changed.emit(value)

    def _on_rotation_bias_spin_changed(self, value: float):
        self.rotation_bias_slider.blockSignals(True)
        self.rotation_bias_slider.setValue(int(value * 10))
        self.rotation_bias_slider.blockSignals(False)
        self.rotation_bias_changed.emit(value)

    def _on_rotation_bias_slider_changed(self, slider_value: int):
        value = slider_value / 10.0
        self.rotation_bias_spin.blockSignals(True)
        self.rotation_bias_spin.setValue(value)
        self.rotation_bias_spin.blockSignals(False)
        self.rotation_bias_changed.emit(value)

    def _on_scale_bias_x_spin_changed(self, value: float):
        self.scale_bias_x_slider.blockSignals(True)
        self.scale_bias_x_slider.setValue(int(value * 100))
        self.scale_bias_x_slider.blockSignals(False)
        self.scale_bias_x_changed.emit(value)

    def _on_scale_bias_x_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.scale_bias_x_spin.blockSignals(True)
        self.scale_bias_x_spin.setValue(value)
        self.scale_bias_x_spin.blockSignals(False)
        self.scale_bias_x_changed.emit(value)

    def _on_scale_bias_y_spin_changed(self, value: float):
        self.scale_bias_y_slider.blockSignals(True)
        self.scale_bias_y_slider.setValue(int(value * 100))
        self.scale_bias_y_slider.blockSignals(False)
        self.scale_bias_y_changed.emit(value)

    def _on_scale_bias_y_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.scale_bias_y_spin.blockSignals(True)
        self.scale_bias_y_spin.setValue(value)
        self.scale_bias_y_spin.blockSignals(False)
        self.scale_bias_y_changed.emit(value)

    def _on_world_offset_x_spin_changed(self, value: float):
        self.world_offset_x_slider.blockSignals(True)
        self.world_offset_x_slider.setValue(int(value * 100))
        self.world_offset_x_slider.blockSignals(False)
        self.world_offset_x_changed.emit(value)

    def _on_world_offset_x_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.world_offset_x_spin.blockSignals(True)
        self.world_offset_x_spin.setValue(value)
        self.world_offset_x_spin.blockSignals(False)
        self.world_offset_x_changed.emit(value)

    def _on_world_offset_y_spin_changed(self, value: float):
        self.world_offset_y_slider.blockSignals(True)
        self.world_offset_y_slider.setValue(int(value * 100))
        self.world_offset_y_slider.blockSignals(False)
        self.world_offset_y_changed.emit(value)

    def _on_world_offset_y_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.world_offset_y_spin.blockSignals(True)
        self.world_offset_y_spin.setValue(value)
        self.world_offset_y_spin.blockSignals(False)
        self.world_offset_y_changed.emit(value)

    def _on_particle_origin_x_spin_changed(self, value: float):
        self.particle_origin_x_slider.blockSignals(True)
        self.particle_origin_x_slider.setValue(int(value * 100))
        self.particle_origin_x_slider.blockSignals(False)
        self.particle_origin_offset_x_changed.emit(value)

    def _on_particle_origin_x_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.particle_origin_x_spin.blockSignals(True)
        self.particle_origin_x_spin.setValue(value)
        self.particle_origin_x_spin.blockSignals(False)
        self.particle_origin_offset_x_changed.emit(value)

    def _on_particle_origin_y_spin_changed(self, value: float):
        self.particle_origin_y_slider.blockSignals(True)
        self.particle_origin_y_slider.setValue(int(value * 100))
        self.particle_origin_y_slider.blockSignals(False)
        self.particle_origin_offset_y_changed.emit(value)

    def _on_particle_origin_y_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.particle_origin_y_spin.blockSignals(True)
        self.particle_origin_y_spin.setValue(value)
        self.particle_origin_y_spin.blockSignals(False)
        self.particle_origin_offset_y_changed.emit(value)

    def _on_trim_shift_spin_changed(self, value: float):
        self.trim_shift_slider.blockSignals(True)
        self.trim_shift_slider.setValue(int(value * 100))
        self.trim_shift_slider.blockSignals(False)
        self.trim_shift_multiplier_changed.emit(value)

    def _on_trim_shift_slider_changed(self, slider_value: int):
        value = slider_value / 100.0
        self.trim_shift_spin.blockSignals(True)
        self.trim_shift_spin.setValue(value)
        self.trim_shift_spin.blockSignals(False)
        self.trim_shift_multiplier_changed.emit(value)

    def update_file_count_label(self, shown: int, total: int):
        """
        Update the label displaying how many files are visible
        
        Args:
            shown: Number of files currently shown in the dropdown
            total: Total number of indexed files
        """
        if total == 0:
            text = "No files indexed"
        elif shown == total:
            text = f"Showing {total} files"
        else:
            text = f"Showing {shown} of {total} files"
        self.file_count_label.setText(text)

    def set_barebones_file_mode(self, enabled: bool):
        """Toggle between classic list search and the monster browser shortcut."""
        if hasattr(self, "barebones_container"):
            self.barebones_container.setVisible(enabled)
        if hasattr(self, "monster_browser_container"):
            self.monster_browser_container.setVisible(not enabled)

    def set_dof_search_mode(self, enabled: bool):
        """Update UI hints when DOF asset search is active."""
        if hasattr(self, "dof_search_toggle"):
            self.dof_search_toggle.blockSignals(True)
            self.dof_search_toggle.setChecked(enabled)
            self.dof_search_toggle.blockSignals(False)
        placeholder = "Filter DOF assets..." if enabled else "Filter BIN/JSON files..."
        self.file_search_input.setPlaceholderText(placeholder)

    def update_costume_options(self, entries: List[Tuple[str, Any]], select_index: int = 0):
        """
        Populate the costume combo box.

        Args:
            entries: Sequence of (label, userData) tuples for available costumes.
            select_index: Desired combo index after repopulating (default selects base).
        """
        combo = self.costume_combo
        was_blocked = combo.blockSignals(True)
        combo.clear()
        combo.addItem("No Costume (Base)", None)
        for label, data in entries:
            combo.addItem(label, data)
        combo.blockSignals(was_blocked)

        combo.setEnabled(combo.count() > 1)
        if 0 <= select_index < combo.count():
            combo.setCurrentIndex(select_index)
        else:
            combo.setCurrentIndex(0)
        self.set_costume_convert_enabled(combo.currentData() is not None)

    def set_costume_convert_enabled(self, enabled: bool):
        """Enable/disable the costume conversion button."""
        self.costume_convert_btn.setEnabled(enabled)

    def set_dof_convert_state(self, enabled: bool, label=None):
        """Enable/disable the DOF conversion button and update its label."""
        if not hasattr(self, "convert_dof_btn"):
            return
        if label is None:
            label = self._dof_convert_label if enabled else "Converting DOF..."
        self.convert_dof_btn.setText(label)
        self.convert_dof_btn.setEnabled(enabled)
        if hasattr(self, "dof_mesh_pivot_checkbox"):
            self.dof_mesh_pivot_checkbox.setEnabled(enabled)
        if hasattr(self, "dof_include_mesh_xml_checkbox"):
            self.dof_include_mesh_xml_checkbox.setEnabled(enabled)
        if hasattr(self, "dof_premultiply_alpha_checkbox"):
            self.dof_premultiply_alpha_checkbox.setEnabled(enabled)
        if hasattr(self, "dof_swap_anchor_report_checkbox"):
            self.dof_swap_anchor_report_checkbox.setEnabled(enabled)
        if hasattr(self, "dof_swap_anchor_edge_align_checkbox"):
            self.dof_swap_anchor_edge_align_checkbox.setEnabled(enabled)
        if hasattr(self, "dof_swap_anchor_report_override_checkbox"):
            self.dof_swap_anchor_report_override_checkbox.setEnabled(enabled)

    def set_diagnostics_enabled(self, enabled: bool):
        """Sync the diagnostics checkbox without emitting."""
        self.diag_enable_checkbox.blockSignals(True)
        self.diag_enable_checkbox.setChecked(enabled)
        self.diag_enable_checkbox.blockSignals(False)

    def set_constraints_enabled(self, enabled: bool):
        """Sync the constraints checkbox without emitting."""
        if not hasattr(self, "constraints_enable_checkbox"):
            return
        self.constraints_enable_checkbox.blockSignals(True)
        self.constraints_enable_checkbox.setChecked(bool(enabled))
        self.constraints_enable_checkbox.blockSignals(False)

    def set_joint_solver_enabled(self, enabled: bool):
        if not hasattr(self, "joint_solver_checkbox"):
            return
        self.joint_solver_checkbox.blockSignals(True)
        self.joint_solver_checkbox.setChecked(bool(enabled))
        self.joint_solver_checkbox.blockSignals(False)

    def set_joint_solver_iterations(self, value: int):
        if not hasattr(self, "joint_solver_iterations_spin"):
            return
        self.joint_solver_iterations_spin.blockSignals(True)
        self.joint_solver_iterations_spin.setValue(int(value))
        self.joint_solver_iterations_spin.blockSignals(False)

    def set_joint_solver_strength(self, value: float):
        if not hasattr(self, "joint_solver_strength_spin"):
            return
        self.joint_solver_strength_spin.blockSignals(True)
        self.joint_solver_strength_spin.setValue(float(value))
        self.joint_solver_strength_spin.blockSignals(False)

    def set_joint_solver_parented(self, enabled: bool):
        if not hasattr(self, "joint_solver_parented_checkbox"):
            return
        self.joint_solver_parented_checkbox.blockSignals(True)
        self.joint_solver_parented_checkbox.setChecked(bool(enabled))
        self.joint_solver_parented_checkbox.blockSignals(False)

    def set_propagate_user_transforms(self, enabled: bool):
        if not hasattr(self, "propagate_user_transforms_checkbox"):
            return
        self.propagate_user_transforms_checkbox.blockSignals(True)
        self.propagate_user_transforms_checkbox.setChecked(bool(enabled))
        self.propagate_user_transforms_checkbox.blockSignals(False)

    def set_preserve_children_on_record(self, enabled: bool):
        if not hasattr(self, "preserve_children_on_record_checkbox"):
            return
        self.preserve_children_on_record_checkbox.blockSignals(True)
        self.preserve_children_on_record_checkbox.setChecked(bool(enabled))
        self.preserve_children_on_record_checkbox.blockSignals(False)

    def update_constraints_list(self, entries: List[Tuple[str, bool, str]]):
        """Populate the constraints list (cid, enabled, label)."""
        if not hasattr(self, "constraints_list"):
            return
        self._updating_constraints_list = True
        self.constraints_list.clear()
        for cid, enabled, label in entries:
            item = QListWidgetItem(label)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
            )
            item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            self.constraints_list.addItem(item)
        self._updating_constraints_list = False
        self._update_constraint_buttons()

    def _on_constraint_item_changed(self, item: QListWidgetItem):
        if self._updating_constraints_list:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        if not cid:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        self.constraint_item_toggled.emit(str(cid), bool(enabled))

    def _on_constraint_selection_changed(self, current: Optional[QListWidgetItem], _prev: Optional[QListWidgetItem]):
        self._update_constraint_buttons()

    def _update_constraint_buttons(self):
        has_selection = bool(self.constraints_list.currentItem()) if hasattr(self, "constraints_list") else False
        if hasattr(self, "constraint_edit_btn"):
            self.constraint_edit_btn.setEnabled(has_selection)
        if hasattr(self, "constraint_remove_btn"):
            self.constraint_remove_btn.setEnabled(has_selection)

    def _emit_constraint_edit(self):
        item = self.constraints_list.currentItem() if hasattr(self, "constraints_list") else None
        if not item:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        if cid:
            self.constraint_edit_requested.emit(str(cid))

    def _emit_constraint_remove(self):
        item = self.constraints_list.currentItem() if hasattr(self, "constraints_list") else None
        if not item:
            return
        cid = item.data(Qt.ItemDataRole.UserRole)
        if cid:
            self.constraint_remove_requested.emit(str(cid))

    def set_pose_mode(self, mode: str):
        """Select a pose influence option."""
        if not hasattr(self, "pose_mode_combo"):
            return
        for idx in range(self.pose_mode_combo.count()):
            if self.pose_mode_combo.itemData(idx) == mode:
                self.pose_mode_combo.blockSignals(True)
                self.pose_mode_combo.setCurrentIndex(idx)
                self.pose_mode_combo.blockSignals(False)
                return

    def set_pose_controls_enabled(self, enabled: bool):
        """Enable/disable pose capture UI."""
        if hasattr(self, "record_pose_btn"):
            self.record_pose_btn.setEnabled(enabled)
        if hasattr(self, "pose_mode_combo"):
            self.pose_mode_combo.setEnabled(enabled)
        if hasattr(self, "preserve_children_on_record_checkbox"):
            self.preserve_children_on_record_checkbox.setEnabled(enabled)
        if hasattr(self, "reset_pose_btn"):
            self.reset_pose_btn.setEnabled(enabled)
        if hasattr(self, "undo_keyframe_btn"):
            self.undo_keyframe_btn.setEnabled(enabled)
        if hasattr(self, "redo_keyframe_btn"):
            self.redo_keyframe_btn.setEnabled(enabled)
        if hasattr(self, "delete_other_keyframes_btn"):
            self.delete_other_keyframes_btn.setEnabled(enabled)
        if hasattr(self, "extend_duration_btn"):
            self.extend_duration_btn.setEnabled(enabled)
        if hasattr(self, "save_animation_btn"):
            self.save_animation_btn.setEnabled(enabled)
        if hasattr(self, "load_animation_btn"):
            self.load_animation_btn.setEnabled(True)

    def set_sprite_tools_enabled(self, enabled: bool):
        """Enable or disable sprite variation controls."""
        if hasattr(self, "assign_sprite_btn"):
            self.assign_sprite_btn.setEnabled(enabled)

    def set_keyframe_history_state(self, undo_available: bool, redo_available: bool):
        """Enable/disable undo and redo buttons based on history state."""
        if hasattr(self, "undo_keyframe_btn"):
            self.undo_keyframe_btn.setEnabled(undo_available)
        if hasattr(self, "redo_keyframe_btn"):
            self.redo_keyframe_btn.setEnabled(redo_available)

    def set_compact_ui(self, enabled: bool):
        """Apply compact UI spacing and sizing."""
        self._compact_ui_enabled = bool(enabled)
        if hasattr(self, "compact_ui_checkbox"):
            self.compact_ui_checkbox.blockSignals(True)
            self.compact_ui_checkbox.setChecked(self._compact_ui_enabled)
            self.compact_ui_checkbox.blockSignals(False)
        self._apply_compact_ui_styles()

    def _apply_compact_ui_styles(self):
        spacing = 4 if self._compact_ui_enabled else 8
        margins = (6, 6, 6, 6) if self._compact_ui_enabled else (8, 8, 8, 8)
        for layout in self._section_layouts.values():
            layout.setSpacing(spacing)
            layout.setContentsMargins(*margins)
        for group in self.findChildren(QGroupBox):
            layout = group.layout()
            if layout:
                layout.setSpacing(4 if self._compact_ui_enabled else 6)
                layout.setContentsMargins(*margins)
        if self._compact_ui_enabled:
            self.setStyleSheet(
                "QWidget { font-size: 9pt; }"
                "QPushButton, QToolButton { padding: 2px 6px; }"
                "QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { min-height: 20px; }"
            )
            self.section_tabs.setStyleSheet("QTabBar::tab { padding: 2px 6px; }")
        else:
            self.setStyleSheet("")
            self.section_tabs.setStyleSheet("")
    
    def update_offset_display(self, layer_offsets, get_layer_by_id_func, layer_rotations=None, layer_scales=None):
        """
        Update the offset display
        
        Args:
            layer_offsets: Dictionary of layer_id -> (offset_x, offset_y)
            get_layer_by_id_func: Function to get layer by ID
            layer_rotations: Optional dictionary of rotation offsets
            layer_scales: Optional dictionary of scale multipliers
        """
        # Clear existing labels
        while self.offset_display_layout.count() > 1:
            item = self.offset_display_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        rotation_map = layer_rotations or {}
        scale_map = layer_scales or {}
        layer_ids = set(layer_offsets.keys())
        layer_ids.update(rotation_map.keys())
        layer_ids.update(scale_map.keys())
        
        if not layer_ids:
            no_offsets_label = QLabel("No offsets applied")
            no_offsets_label.setStyleSheet("color: gray; font-style: italic;")
            self.offset_display_layout.insertWidget(0, no_offsets_label)
            return
        
        for layer_id in sorted(layer_ids):
            offset_x, offset_y = layer_offsets.get(layer_id, (0.0, 0.0))
            rotation_value = rotation_map.get(layer_id, 0.0)
            scale_value = scale_map.get(layer_id, (1.0, 1.0))
            layer = get_layer_by_id_func(layer_id)
            if layer:
                details = f"{layer.name}: ({offset_x:.2f}, {offset_y:.2f})"
                if abs(rotation_value) > 0.0001:
                    details += f"  Rot {rotation_value:.1f}°"
                if abs(scale_value[0] - 1.0) > 0.0001 or abs(scale_value[1] - 1.0) > 0.0001:
                    details += f"  Scale {scale_value[0]:.2f}/{scale_value[1]:.2f}"
                offset_label = QLabel(details)
                offset_label.setStyleSheet("font-size: 9pt;")
                self.offset_display_layout.insertWidget(
                    self.offset_display_layout.count() - 1, 
                    offset_label
                )

    # ------------------------------------------------------------------ #
    # Nudging helper methods
    # ------------------------------------------------------------------ #
    def _emit_nudge_x(self, direction: int):
        """Emit X position nudge signal with current step size."""
        step = self.nudge_step_spin.value() * direction
        self.nudge_x_changed.emit(step)

    def _emit_nudge_y(self, direction: int):
        """Emit Y position nudge signal with current step size."""
        step = self.nudge_step_spin.value() * direction
        self.nudge_y_changed.emit(step)

    def _emit_nudge_rotation(self, direction: int):
        """Emit rotation nudge signal with current rotation step."""
        step = self.nudge_rot_step_spin.value() * direction
        self.nudge_rotation_changed.emit(step)

    def _emit_nudge_scale_uniform(self, direction: int):
        """Emit uniform scale nudge (both X and Y)."""
        step = self.nudge_scale_step_spin.value() * direction
        self.nudge_scale_x_changed.emit(step)
        self.nudge_scale_y_changed.emit(step)

    def _emit_nudge_scale_x(self, direction: int):
        """Emit X scale nudge signal."""
        step = self.nudge_scale_step_spin.value() * direction
        self.nudge_scale_x_changed.emit(step)

    def _emit_nudge_scale_y(self, direction: int):
        """Emit Y scale nudge signal."""
        step = self.nudge_scale_step_spin.value() * direction
        self.nudge_scale_y_changed.emit(step)

    def set_nudge_controls_enabled(self, enabled: bool):
        """Enable or disable all nudging controls based on layer selection."""
        for widget in (
            self.nudge_step_spin,
            self.nudge_x_minus_btn,
            self.nudge_x_plus_btn,
            self.nudge_y_minus_btn,
            self.nudge_y_plus_btn,
            self.nudge_rot_step_spin,
            self.nudge_rot_minus_btn,
            self.nudge_rot_plus_btn,
            self.nudge_scale_step_spin,
            self.nudge_scale_minus_btn,
            self.nudge_scale_plus_btn,
            self.nudge_scale_x_minus_btn,
            self.nudge_scale_x_plus_btn,
            self.nudge_scale_y_minus_btn,
            self.nudge_scale_y_plus_btn,
        ):
            widget.setEnabled(enabled)
