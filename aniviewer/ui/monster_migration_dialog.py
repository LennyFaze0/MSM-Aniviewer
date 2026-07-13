"""
Monster Migration Dialog
Simple, powerful workflow for cross-revision monster migration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from utils.ui_scaling import clamp_ui_scale, scale_float, scale_int


@dataclass
class MigrationSourceOption:
    """One candidate source monster from the pooled profile roots."""

    label: str
    token: str
    stem: str
    profile_index: int
    profile_name: str
    json_path: Optional[str] = None
    bin_path: Optional[str] = None
    revision_hint: Optional[int] = None
    source_kind: str = "monster"
    dof_root: Optional[str] = None
    dof_input_path: Optional[str] = None

    @property
    def preferred_source_path(self) -> Optional[str]:
        if self.source_kind == "dof":
            return self.dof_input_path or self.json_path
        return self.bin_path or self.json_path

    @property
    def is_dof_source(self) -> bool:
        return self.source_kind == "dof"


@dataclass
class MonsterMigrationRequest:
    """Normalized request payload returned by the migration dialog."""

    source: MigrationSourceOption
    target_profile_index: int
    target_token: str
    target_revision: int
    merge_with_existing: bool
    remap_island_prefix: bool
    target_island: int
    copy_assets: bool
    write_json_output: bool
    selected_animation_names: Optional[List[str]] = None
    target_animation_names: Optional[List[str]] = None
    open_legacy_tools: bool = False


class MonsterMigrationDialog(QDialog):
    """Beginner-friendly migration dialog with advanced escape hatch."""

    def __init__(
        self,
        sources: List[MigrationSourceOption],
        profiles: List[Dict[str, Any]],
        available_revisions: List[int],
        active_profile_index: int,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._all_sources = list(sources)
        self._profiles = list(profiles)
        self._available_revisions = list(available_revisions)
        self._active_profile_index = max(0, int(active_profile_index)) if profiles else 0
        if self._active_profile_index >= len(self._profiles):
            self._active_profile_index = max(0, len(self._profiles) - 1)

        self.open_legacy_tools_requested: bool = False
        self._auto_target_token: bool = True

        self.setWindowTitle("Monster Migration")
        self.setMinimumWidth(760)
        self.setMinimumHeight(520)

        self._build_ui()
        self._refresh_source_combo()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        intro = QLabel(
            "Simple mode migrates monsters across revisions with smart defaults. "
            "It auto-detects source BIN format, converts through JSON, and can "
            "copy required XML/atlas assets into the target profile."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray; font-size: 9pt;")
        root.addWidget(intro)

        source_group = QGroupBox("Source Monster (All Profiles Pool)")
        source_layout = QVBoxLayout()

        source_search_row = QHBoxLayout()
        source_search_row.addWidget(QLabel("Filter:"))
        self.source_search_edit = QLineEdit()
        self.source_search_edit.setPlaceholderText("Type monster name, token, stem, or profile...")
        self.source_search_edit.textChanged.connect(self._refresh_source_combo)
        source_search_row.addWidget(self.source_search_edit, 1)
        source_layout.addLayout(source_search_row)

        source_pick_row = QHBoxLayout()
        source_pick_row.addWidget(QLabel("Source:"))
        self.source_combo = QComboBox()
        self.source_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_pick_row.addWidget(self.source_combo, 1)
        source_layout.addLayout(source_pick_row)

        self.source_readout = QLabel("No source selected.")
        self.source_readout.setWordWrap(True)
        self.source_readout.setStyleSheet("color: gray; font-size: 9pt;")
        source_layout.addWidget(self.source_readout)

        source_group.setLayout(source_layout)
        root.addWidget(source_group)

        target_group = QGroupBox("Target")
        target_layout = QFormLayout()

        self.target_profile_combo = QComboBox()
        for idx, profile in enumerate(self._profiles):
            profile_name = str(profile.get("name") or f"Profile {idx + 1}")
            game_path = str(profile.get("game_path") or "")
            downloads_path = str(profile.get("downloads_path") or "")
            suffix = []
            if game_path:
                suffix.append("game")
            if downloads_path:
                suffix.append("downloads")
            profile_label = profile_name
            if suffix:
                profile_label = f"{profile_name} ({', '.join(suffix)})"
            self.target_profile_combo.addItem(profile_label, idx)
        self.target_profile_combo.setCurrentIndex(self._active_profile_index)
        target_layout.addRow("Target Profile:", self.target_profile_combo)

        self.target_token_edit = QLineEdit()
        self.target_token_edit.setPlaceholderText("monster token (without monster_)")
        self.target_token_edit.textEdited.connect(self._on_target_token_edited)
        target_layout.addRow("Target Monster Token:", self.target_token_edit)

        self.target_revision_combo = QComboBox()
        for revision in self._available_revisions:
            self.target_revision_combo.addItem(f"Rev {revision}", revision)
        default_rev_index = self.target_revision_combo.findData(6)
        if default_rev_index < 0:
            default_rev_index = max(0, self.target_revision_combo.count() - 1)
        self.target_revision_combo.setCurrentIndex(default_rev_index)
        target_layout.addRow("Target BIN Revision:", self.target_revision_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Create or Replace Target Monster", "replace")
        self.mode_combo.addItem("Merge Animations Into Existing Target", "merge")
        target_layout.addRow("Migration Mode:", self.mode_combo)

        island_row = QHBoxLayout()
        self.remap_island_check = QCheckBox("Rewrite animation island prefix")
        self.remap_island_check.setChecked(True)
        self.remap_island_spin = QSpinBox()
        self.remap_island_spin.setRange(0, 999)
        self.remap_island_spin.setValue(1)
        self.remap_island_spin.setSuffix(" target island")
        self.remap_island_check.toggled.connect(self.remap_island_spin.setEnabled)
        island_row.addWidget(self.remap_island_check)
        island_row.addWidget(self.remap_island_spin)
        island_row.addStretch(1)
        target_layout.addRow("Island Mapping:", island_row)

        self.copy_assets_check = QCheckBox("Copy XML and atlas assets required by migrated animations")
        self.copy_assets_check.setChecked(True)
        target_layout.addRow("Assets:", self.copy_assets_check)

        self.write_json_check = QCheckBox("Also write target JSON next to target BIN")
        self.write_json_check.setChecked(False)
        target_layout.addRow("Debug Output:", self.write_json_check)

        target_group.setLayout(target_layout)
        root.addWidget(target_group)

        advanced_group = QGroupBox("Advanced")
        advanced_layout = QVBoxLayout()
        advanced_text = QLabel(
            "Need full manual control? Jump to the legacy BIN Converter + Anim Transfer tools."
        )
        advanced_text.setWordWrap(True)
        advanced_text.setStyleSheet("color: gray; font-size: 9pt;")
        advanced_layout.addWidget(advanced_text)

        adv_btn_row = QHBoxLayout()
        adv_btn_row.addStretch(1)
        self.open_legacy_btn = QPushButton("Open Advanced Bin Converter")
        self.open_legacy_btn.clicked.connect(self._on_open_legacy_tools)
        adv_btn_row.addWidget(self.open_legacy_btn)
        advanced_layout.addLayout(adv_btn_row)

        advanced_group.setLayout(advanced_layout)
        root.addWidget(advanced_group)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_btn)
        self.run_btn = QPushButton("Run Migration")
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self.accept)
        button_row.addWidget(self.run_btn)
        root.addLayout(button_row)

    def _on_target_token_edited(self, _text: str) -> None:
        self._auto_target_token = False

    def _refresh_source_combo(self) -> None:
        query = (self.source_search_edit.text() or "").strip().lower()
        current_key = None
        current_source = self._current_source()
        if current_source:
            current_key = (
                current_source.profile_index,
                current_source.source_kind,
                current_source.stem,
                current_source.preferred_source_path,
            )

        self.source_combo.blockSignals(True)
        self.source_combo.clear()

        for source in self._all_sources:
            haystack = " ".join(
                [
                    source.label,
                    source.token,
                    source.stem,
                    source.profile_name,
                    source.source_kind,
                    source.dof_root or "",
                    source.dof_input_path or "",
                    source.bin_path or "",
                    source.json_path or "",
                ]
            ).lower()
            if query and query not in haystack:
                continue
            self.source_combo.addItem(source.label, source)

        if self.source_combo.count() <= 0:
            self.source_combo.addItem("No sources match current filter", None)
            self.source_combo.blockSignals(False)
            self._on_source_changed(-1)
            return

        preferred_index = 0
        if current_key is not None:
            for idx in range(self.source_combo.count()):
                data = self.source_combo.itemData(idx)
                if not isinstance(data, MigrationSourceOption):
                    continue
                key = (
                    data.profile_index,
                    data.source_kind,
                    data.stem,
                    data.preferred_source_path,
                )
                if key == current_key:
                    preferred_index = idx
                    break

        self.source_combo.setCurrentIndex(preferred_index)
        self.source_combo.blockSignals(False)
        self._on_source_changed(preferred_index)

    def _current_source(self) -> Optional[MigrationSourceOption]:
        data = self.source_combo.currentData()
        if isinstance(data, MigrationSourceOption):
            return data
        return None

    def _on_source_changed(self, _index: int) -> None:
        source = self._current_source()
        if source is None:
            self.target_revision_combo.setEnabled(True)
            self.mode_combo.setEnabled(True)
            self.remap_island_check.setEnabled(True)
            self.remap_island_spin.setEnabled(self.remap_island_check.isChecked())
            self.copy_assets_check.setEnabled(True)
            self.source_readout.setText("No source selected.")
            return

        self._apply_source_constraints(source)
        chosen_path = source.preferred_source_path or ""
        if source.is_dof_source:
            revision_text = "DOF source -> rev 6"
        elif source.revision_hint is not None:
            revision_text = f"rev {source.revision_hint}"
        else:
            revision_text = "unknown"

        self.source_readout.setText(
            f"Profile: {source.profile_name}\n"
            f"Type: {'DOF animation' if source.is_dof_source else 'BIN/JSON monster'}\n"
            f"Stem: {source.stem}\n"
            f"Detected source revision hint: {revision_text}\n"
            f"Source file: {chosen_path}"
        )

        if self._auto_target_token and source.token:
            self.target_token_edit.setText(source.token)

    def _apply_source_constraints(self, source: MigrationSourceOption) -> None:
        is_dof = source.is_dof_source
        rev_index = self.target_revision_combo.findData(6)
        if is_dof and rev_index >= 0:
            self.target_revision_combo.setCurrentIndex(rev_index)
        self.target_revision_combo.setEnabled(not is_dof)
        self.mode_combo.setEnabled(True)
        self.remap_island_check.setEnabled(True)
        self.remap_island_spin.setEnabled(self.remap_island_check.isChecked())
        if is_dof:
            self.copy_assets_check.setChecked(True)
        self.copy_assets_check.setEnabled(not is_dof)

    def _on_open_legacy_tools(self) -> None:
        self.open_legacy_tools_requested = True
        self.accept()

    def _normalized_target_token(self) -> str:
        raw = (self.target_token_edit.text() or "").strip().lower()
        if raw.startswith("monster_"):
            raw = raw[len("monster_") :]
        return raw

    def build_request(self) -> Optional[MonsterMigrationRequest]:
        source = self._current_source()
        if source is None:
            return None

        profile_index = self.target_profile_combo.currentData()
        try:
            profile_index = int(profile_index)
        except (TypeError, ValueError):
            profile_index = 0

        target_revision = self.target_revision_combo.currentData()
        try:
            target_revision = int(target_revision)
        except (TypeError, ValueError):
            target_revision = 6

        target_token = self._normalized_target_token()

        return MonsterMigrationRequest(
            source=source,
            target_profile_index=profile_index,
            target_token=target_token,
            target_revision=target_revision,
            merge_with_existing=(self.mode_combo.currentData() == "merge"),
            remap_island_prefix=self.remap_island_check.isChecked(),
            target_island=int(self.remap_island_spin.value()),
            copy_assets=self.copy_assets_check.isChecked(),
            write_json_output=self.write_json_check.isChecked(),
            open_legacy_tools=self.open_legacy_tools_requested,
        )

    def accept(self) -> None:
        if self.open_legacy_tools_requested:
            super().accept()
            return

        request = self.build_request()
        if request is None:
            QMessageBox.warning(self, "Missing Source", "Choose a source monster from the pool.")
            return

        if request.target_profile_index < 0 or request.target_profile_index >= len(self._profiles):
            QMessageBox.warning(self, "Missing Target", "Choose a valid target profile.")
            return

        if not request.target_token:
            QMessageBox.warning(
                self,
                "Missing Target Token",
                "Enter a target monster token (for example: bowgart or tweedle_fire).",
            )
            return

        super().accept()


class MonsterMigrationPanel(QWidget):
    """
    Dockable monster migration workspace intended for side-panel use.
    Emits structured requests to the host window for execution/preview.
    """

    run_requested = pyqtSignal(object)
    preview_source_requested = pyqtSignal(object)
    preview_target_requested = pyqtSignal(object)
    preview_output_requested = pyqtSignal(str)
    source_tracks_requested = pyqtSignal(object)
    target_tracks_requested = pyqtSignal(object)
    source_location_changed = pyqtSignal(object)
    refresh_requested = pyqtSignal()
    open_legacy_tools_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._all_sources: List[MigrationSourceOption] = []
        self._profiles: List[Dict[str, Any]] = []
        self._available_revisions: List[int] = []
        self._active_profile_index: int = 0
        self._auto_target_token: bool = True
        self._source_location_key: str = "all"
        self._last_output_path: str = ""
        self._track_source_key: Optional[tuple] = None
        self._target_track_key: Optional[tuple] = None
        self._ui_scale_factor: float = 1.0
        self._build_ui()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(max(320, hint.width()), hint.height())

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    @staticmethod
    def _configure_wrapping_label(label: QLabel) -> None:
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    @staticmethod
    def _configure_panel_combo(combo: QComboBox, minimum_contents: int = 12) -> None:
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(max(4, int(minimum_contents)))
        combo.setMinimumWidth(0)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _configure_panel_button(button: QPushButton) -> None:
        button.setMinimumWidth(0)
        button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _configure_panel_checkbox(checkbox: QCheckBox) -> None:
        checkbox.setMinimumWidth(0)
        checkbox.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _configure_panel_group(group: QGroupBox) -> None:
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    @staticmethod
    def _configure_track_list(widget: QListWidget) -> None:
        widget.setMinimumWidth(0)
        widget.setProperty("ui_scale_factor", 1.0)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setTextElideMode(Qt.TextElideMode.ElideRight)
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        title = QLabel("Monster Migration Workspace")
        self._configure_wrapping_label(title)
        title.setStyleSheet("font-weight: 600;")
        root.addWidget(title)

        intro = QLabel(
            "Use source/target preview to validate XML + spritesheet compatibility in the live viewport "
            "before and after migration."
        )
        self._configure_wrapping_label(intro)
        intro.setStyleSheet("color: gray; font-size: 9pt;")
        root.addWidget(intro)

        scroll_area = QScrollArea()
        scroll_area.setMinimumWidth(0)
        scroll_area.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scroll_content.setMinimumWidth(0)
        scroll_content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        source_group = QGroupBox("Source Monster")
        self._configure_panel_group(source_group)
        source_layout = QVBoxLayout()

        source_location_row = QHBoxLayout()
        source_location_row.addWidget(QLabel("Location:"))
        self.source_location_combo = QComboBox()
        self._configure_panel_combo(self.source_location_combo, minimum_contents=8)
        self.source_location_combo.currentIndexChanged.connect(self._on_source_location_changed)
        source_location_row.addWidget(self.source_location_combo, 1)
        source_layout.addLayout(source_location_row)

        source_search_row = QHBoxLayout()
        source_search_row.addWidget(QLabel("Filter:"))
        self.source_search_edit = QLineEdit()
        self.source_search_edit.setMinimumWidth(0)
        self.source_search_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.source_search_edit.setPlaceholderText("name / token / profile...")
        self.source_search_edit.textChanged.connect(self._refresh_source_combo)
        source_search_row.addWidget(self.source_search_edit, 1)
        source_layout.addLayout(source_search_row)

        source_pick_row = QHBoxLayout()
        source_pick_row.addWidget(QLabel("Source:"))
        self.source_combo = QComboBox()
        self._configure_panel_combo(self.source_combo, minimum_contents=8)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        source_pick_row.addWidget(self.source_combo, 1)
        source_layout.addLayout(source_pick_row)

        self.source_readout = QLabel("No source selected.")
        self._configure_wrapping_label(self.source_readout)
        self.source_readout.setStyleSheet("color: gray; font-size: 9pt;")
        source_layout.addWidget(self.source_readout)

        track_group = QGroupBox("Track Selection")
        self._configure_panel_group(track_group)
        track_layout = QVBoxLayout()
        track_layout.setSpacing(6)
        self.track_readout = QLabel("Select a source to load tracks.")
        self._configure_wrapping_label(self.track_readout)
        self.track_readout.setStyleSheet("color: gray; font-size: 9pt;")
        track_layout.addWidget(self.track_readout)
        self.track_list = QListWidget()
        self.track_list.setMinimumHeight(54)
        self._configure_track_list(self.track_list)
        self.track_list.itemChanged.connect(self._on_track_item_changed)
        track_layout.addWidget(self.track_list, 0)
        track_group.setLayout(track_layout)
        source_layout.addWidget(track_group)

        track_btn_row = QHBoxLayout()
        track_btn_row.setContentsMargins(0, 0, 0, 0)
        self.select_all_tracks_btn = QPushButton("Select All")
        self._configure_panel_button(self.select_all_tracks_btn)
        self.select_all_tracks_btn.clicked.connect(self._select_all_tracks)
        track_btn_row.addWidget(self.select_all_tracks_btn)
        self.clear_tracks_btn = QPushButton("Clear")
        self._configure_panel_button(self.clear_tracks_btn)
        self.clear_tracks_btn.clicked.connect(self._clear_tracks)
        track_btn_row.addWidget(self.clear_tracks_btn)
        track_btn_row.addStretch(1)
        track_btn_container = QWidget()
        track_btn_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        track_btn_container.setLayout(track_btn_row)
        source_layout.addWidget(track_btn_container)

        source_actions = QHBoxLayout()
        self.refresh_sources_btn = QPushButton("Refresh")
        self._configure_panel_button(self.refresh_sources_btn)
        self.refresh_sources_btn.clicked.connect(self.refresh_requested.emit)
        source_actions.addWidget(self.refresh_sources_btn)
        self.preview_source_btn = QPushButton("Preview Source")
        self._configure_panel_button(self.preview_source_btn)
        self.preview_source_btn.clicked.connect(self._emit_preview_source)
        source_actions.addWidget(self.preview_source_btn)
        source_actions.addStretch(1)
        source_layout.addLayout(source_actions)

        source_group.setLayout(source_layout)
        content_layout.addWidget(source_group)

        target_group = QGroupBox("Target")
        self._configure_panel_group(target_group)
        target_layout = QFormLayout()
        target_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        target_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.target_profile_combo = QComboBox()
        self._configure_panel_combo(self.target_profile_combo, minimum_contents=8)
        self.target_profile_combo.currentIndexChanged.connect(self._request_target_tracks)
        target_layout.addRow("Profile:", self.target_profile_combo)

        self.target_token_edit = QLineEdit()
        self.target_token_edit.setMinimumWidth(0)
        self.target_token_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.target_token_edit.setPlaceholderText("monster token (without monster_)")
        self.target_token_edit.textEdited.connect(self._on_target_token_edited)
        target_layout.addRow("Token:", self.target_token_edit)

        self.target_revision_combo = QComboBox()
        self._configure_panel_combo(self.target_revision_combo, minimum_contents=5)
        target_layout.addRow("Revision:", self.target_revision_combo)

        self.mode_combo = QComboBox()
        self._configure_panel_combo(self.mode_combo, minimum_contents=8)
        self.mode_combo.addItem("Create or Replace Target Monster", "replace")
        self.mode_combo.addItem("Merge Animations Into Existing Target", "merge")
        self.mode_combo.currentIndexChanged.connect(self._request_target_tracks)
        target_layout.addRow("Mode:", self.mode_combo)

        island_row = QHBoxLayout()
        self.remap_island_check = QCheckBox("Rewrite island prefix")
        self._configure_panel_checkbox(self.remap_island_check)
        self.remap_island_check.setChecked(True)
        self.remap_island_spin = QSpinBox()
        self.remap_island_spin.setMinimumWidth(64)
        self.remap_island_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.remap_island_spin.setRange(0, 999)
        self.remap_island_spin.setValue(1)
        self.remap_island_spin.setToolTip("Target island number")
        self.remap_island_check.toggled.connect(self.remap_island_spin.setEnabled)
        self.remap_island_check.toggled.connect(self._request_target_tracks)
        self.remap_island_spin.valueChanged.connect(self._request_target_tracks)
        island_row.addWidget(self.remap_island_check, 1)
        island_row.addWidget(self.remap_island_spin, 0)
        island_row.addStretch(1)
        target_layout.addRow("Island:", island_row)

        self.copy_assets_check = QCheckBox("Copy XML/atlas assets")
        self._configure_panel_checkbox(self.copy_assets_check)
        self.copy_assets_check.setChecked(True)
        target_layout.addRow("Assets:", self.copy_assets_check)

        self.write_json_check = QCheckBox("Write target JSON")
        self._configure_panel_checkbox(self.write_json_check)
        self.write_json_check.setChecked(False)
        target_layout.addRow("Debug:", self.write_json_check)

        target_track_group = QGroupBox("Target Track Overwrite")
        self._configure_panel_group(target_track_group)
        target_track_layout = QVBoxLayout()
        target_track_layout.setSpacing(6)
        self.target_track_readout = QLabel("Set a target token to load overwrite targets.")
        self._configure_wrapping_label(self.target_track_readout)
        self.target_track_readout.setStyleSheet("color: gray; font-size: 9pt;")
        target_track_layout.addWidget(self.target_track_readout)
        self.target_track_list = QListWidget()
        self.target_track_list.setMinimumHeight(54)
        self._configure_track_list(self.target_track_list)
        self.target_track_list.itemChanged.connect(self._on_target_track_item_changed)
        target_track_layout.addWidget(self.target_track_list, 0)
        target_track_group.setLayout(target_track_layout)

        target_track_section = QWidget()
        target_track_section_layout = QVBoxLayout(target_track_section)
        target_track_section_layout.setContentsMargins(0, 0, 0, 0)
        target_track_section_layout.setSpacing(4)
        target_track_section_layout.addWidget(target_track_group)

        target_track_btn_row = QHBoxLayout()
        target_track_btn_row.setContentsMargins(0, 0, 0, 0)
        self.select_all_target_tracks_btn = QPushButton("Select All")
        self._configure_panel_button(self.select_all_target_tracks_btn)
        self.select_all_target_tracks_btn.clicked.connect(self._select_all_target_tracks)
        target_track_btn_row.addWidget(self.select_all_target_tracks_btn)
        self.clear_target_tracks_btn = QPushButton("Clear")
        self._configure_panel_button(self.clear_target_tracks_btn)
        self.clear_target_tracks_btn.clicked.connect(self._clear_target_tracks)
        target_track_btn_row.addWidget(self.clear_target_tracks_btn)
        target_track_btn_row.addStretch(1)
        target_track_btn_container = QWidget()
        target_track_btn_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        target_track_btn_container.setLayout(target_track_btn_row)
        target_track_section_layout.addWidget(target_track_btn_container)

        target_actions = QHBoxLayout()
        target_actions.setContentsMargins(0, 0, 0, 0)
        self.refresh_target_tracks_btn = QPushButton("Refresh Tracks")
        self._configure_panel_button(self.refresh_target_tracks_btn)
        self.refresh_target_tracks_btn.clicked.connect(self._request_target_tracks)
        target_actions.addWidget(self.refresh_target_tracks_btn)
        self.preview_target_btn = QPushButton("Preview Target")
        self._configure_panel_button(self.preview_target_btn)
        self.preview_target_btn.clicked.connect(self._emit_preview_target)
        target_actions.addWidget(self.preview_target_btn)
        target_actions.addStretch(1)
        target_actions_container = QWidget()
        target_actions_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        target_actions_container.setLayout(target_actions)
        target_track_section_layout.addWidget(target_actions_container)
        target_layout.addRow(target_track_section)
        self._update_target_track_button_state()

        target_group.setLayout(target_layout)
        content_layout.addWidget(target_group)

        preview_group = QGroupBox("Viewport Validation")
        self._configure_panel_group(preview_group)
        preview_layout = QVBoxLayout()
        self.output_readout = QLabel("No migrated output generated yet.")
        self._configure_wrapping_label(self.output_readout)
        self.output_readout.setStyleSheet("color: gray; font-size: 9pt;")
        preview_layout.addWidget(self.output_readout)
        output_row = QHBoxLayout()
        self.preview_output_btn = QPushButton("Preview Output")
        self._configure_panel_button(self.preview_output_btn)
        self.preview_output_btn.setEnabled(False)
        self.preview_output_btn.clicked.connect(self._emit_preview_output)
        output_row.addWidget(self.preview_output_btn)
        output_row.addStretch(1)
        preview_layout.addLayout(output_row)
        preview_group.setLayout(preview_layout)
        content_layout.addWidget(preview_group)
        content_layout.addStretch(1)
        scroll_area.setWidget(scroll_content)
        root.addWidget(scroll_area, 1)

        button_row = QHBoxLayout()
        self.open_legacy_btn = QPushButton("Advanced Converter")
        self._configure_panel_button(self.open_legacy_btn)
        self.open_legacy_btn.clicked.connect(self.open_legacy_tools_requested.emit)
        button_row.addWidget(self.open_legacy_btn)
        button_row.addStretch(1)
        self.run_btn = QPushButton("Run Migration")
        self._configure_panel_button(self.run_btn)
        self.run_btn.clicked.connect(self._emit_run_request)
        button_row.addWidget(self.run_btn)
        root.addLayout(button_row)

    def set_ui_scale_factor(self, factor: float) -> None:
        self._ui_scale_factor = clamp_ui_scale(factor)
        root = self.layout()
        if root is not None:
            root.setContentsMargins(
                scale_int(6, self._ui_scale_factor),
                scale_int(6, self._ui_scale_factor),
                scale_int(6, self._ui_scale_factor),
                scale_int(6, self._ui_scale_factor),
            )
            root.setSpacing(scale_int(8, self._ui_scale_factor))

        small_font = scale_float(9, self._ui_scale_factor, minimum=6.0)
        for label in (
            getattr(self, "source_readout", None),
            getattr(self, "track_readout", None),
            getattr(self, "target_track_readout", None),
            getattr(self, "output_readout", None),
        ):
            if label is not None:
                label.setStyleSheet(f"color: gray; font-size: {small_font:.2f}pt;")

        if hasattr(self, "remap_island_spin"):
            self.remap_island_spin.setMinimumWidth(
                scale_int(64, self._ui_scale_factor, minimum=48)
            )
        for widget in (
            getattr(self, "track_list", None),
            getattr(self, "target_track_list", None),
        ):
            if widget is not None:
                widget.setProperty("ui_scale_factor", self._ui_scale_factor)
                self._resize_track_list_to_contents(widget)
        self.updateGeometry()

    def set_context(
        self,
        sources: List[MigrationSourceOption],
        profiles: List[Dict[str, Any]],
        available_revisions: List[int],
        active_profile_index: int,
    ) -> None:
        self._all_sources = list(sources or [])
        self._profiles = list(profiles or [])
        self._available_revisions = list(available_revisions or [])
        self._active_profile_index = max(0, int(active_profile_index or 0))
        if self._active_profile_index >= len(self._profiles):
            self._active_profile_index = max(0, len(self._profiles) - 1)

        self._refresh_source_location_combo()

        current_profile = self.target_profile_combo.currentData()
        current_revision = self.target_revision_combo.currentData()
        token_text = self.target_token_edit.text()

        self.target_profile_combo.blockSignals(True)
        self.target_profile_combo.clear()
        for idx, profile in enumerate(self._profiles):
            profile_name = str(profile.get("name") or f"Profile {idx + 1}")
            game_path = str(profile.get("game_path") or "")
            downloads_path = str(profile.get("downloads_path") or "")
            suffix = []
            if game_path:
                suffix.append("game")
            if downloads_path:
                suffix.append("downloads")
            profile_label = profile_name
            if suffix:
                profile_label = f"{profile_name} ({', '.join(suffix)})"
            self.target_profile_combo.addItem(profile_label, idx)
        self.target_profile_combo.blockSignals(False)

        profile_index = self.target_profile_combo.findData(current_profile)
        if profile_index < 0:
            profile_index = max(0, self._active_profile_index)
        self.target_profile_combo.setCurrentIndex(profile_index)

        self.target_revision_combo.blockSignals(True)
        self.target_revision_combo.clear()
        for revision in self._available_revisions:
            self.target_revision_combo.addItem(f"Rev {revision}", revision)
        self.target_revision_combo.blockSignals(False)
        revision_index = self.target_revision_combo.findData(current_revision)
        if revision_index < 0:
            revision_index = self.target_revision_combo.findData(6)
        if revision_index < 0:
            revision_index = max(0, self.target_revision_combo.count() - 1)
        self.target_revision_combo.setCurrentIndex(revision_index)

        self._refresh_source_combo()
        if token_text and not self._auto_target_token:
            self.target_token_edit.setText(token_text)
        self._request_target_tracks()
        self._update_target_track_button_state()

    def _refresh_source_location_combo(self) -> None:
        current_key = self._source_location_key or "all"
        self.source_location_combo.blockSignals(True)
        self.source_location_combo.clear()
        self.source_location_combo.addItem(
            "All configured program paths",
            {"key": "all", "mode": "all"},
        )
        for idx, profile in enumerate(self._profiles):
            profile_name = str(profile.get("name") or f"Profile {idx + 1}").strip() or f"Profile {idx + 1}"
            paths = {
                "game_path": str(profile.get("game_path") or "").strip(),
                "downloads_path": str(profile.get("downloads_path") or "").strip(),
                "dof_path": str(profile.get("dof_path") or "").strip(),
            }
            available_fields = [field for field, path in paths.items() if path]
            if available_fields:
                self.source_location_combo.addItem(
                    f"{profile_name} - all source paths",
                    {
                        "key": f"profile:{idx}",
                        "mode": "profile",
                        "profile_index": idx,
                    },
                )
            labels = {
                "game_path": "Game",
                "downloads_path": "Downloads",
                "dof_path": "DOF",
            }
            for field in available_fields:
                path_text = paths[field].replace("\\", "/").rstrip("/")
                path_name = path_text.rsplit("/", 1)[-1] if path_text else labels[field]
                self.source_location_combo.addItem(
                    f"{profile_name} - {labels[field]} ({path_name})",
                    {
                        "key": f"path:{idx}:{field}",
                        "mode": "path",
                        "profile_index": idx,
                        "field": field,
                    },
                )
                self.source_location_combo.setItemData(
                    self.source_location_combo.count() - 1,
                    paths[field],
                    Qt.ItemDataRole.ToolTipRole,
                )
        index = self.source_location_combo.findData(
            current_key,
            role=Qt.ItemDataRole.UserRole,
        )
        if index < 0:
            for row in range(self.source_location_combo.count()):
                data = self.source_location_combo.itemData(row)
                if isinstance(data, dict) and data.get("key") == current_key:
                    index = row
                    break
        if index < 0:
            index = 0
            self._source_location_key = "all"
        self.source_location_combo.setCurrentIndex(index)
        self.source_location_combo.blockSignals(False)

    def set_source_location_key(self, key: Optional[str], *, emit: bool = False) -> None:
        target_key = (key or "all").strip() or "all"
        self._source_location_key = target_key
        if not hasattr(self, "source_location_combo"):
            return
        index = -1
        for row in range(self.source_location_combo.count()):
            data = self.source_location_combo.itemData(row)
            if isinstance(data, dict) and data.get("key") == target_key:
                index = row
                break
        if index < 0:
            index = 0
            self._source_location_key = "all"
        self.source_location_combo.blockSignals(True)
        self.source_location_combo.setCurrentIndex(index)
        self.source_location_combo.blockSignals(False)
        if emit:
            self._emit_source_location_changed()

    def _current_source_location(self) -> Dict[str, Any]:
        data = self.source_location_combo.currentData()
        if isinstance(data, dict):
            return dict(data)
        return {"key": "all", "mode": "all"}

    def _emit_source_location_changed(self) -> None:
        data = self._current_source_location()
        self._source_location_key = str(data.get("key") or "all")
        self.source_location_changed.emit(data)

    def _on_source_location_changed(self, _index: int) -> None:
        self._emit_source_location_changed()

    def set_last_output_path(self, path: Optional[str]) -> None:
        normalized = (path or "").strip()
        if normalized:
            self._last_output_path = normalized
            self.output_readout.setText(f"Last output: {normalized}")
            self.preview_output_btn.setEnabled(True)
        else:
            self._last_output_path = ""
            self.output_readout.setText("No migrated output generated yet.")
            self.preview_output_btn.setEnabled(False)

    def focus_source_filter(self) -> None:
        self.source_search_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_target_token_edited(self, _text: str) -> None:
        self._auto_target_token = False
        self._request_target_tracks()

    def _refresh_source_combo(self) -> None:
        query = (self.source_search_edit.text() or "").strip().lower()
        current_key = None
        current_source = self._current_source()
        if current_source:
            current_key = (
                current_source.profile_index,
                current_source.source_kind,
                current_source.stem,
                current_source.preferred_source_path,
            )

        self.source_combo.blockSignals(True)
        self.source_combo.clear()

        for source in self._all_sources:
            haystack = " ".join(
                [
                    source.label,
                    source.token,
                    source.stem,
                    source.profile_name,
                    source.source_kind,
                    source.dof_root or "",
                    source.dof_input_path or "",
                    source.bin_path or "",
                    source.json_path or "",
                ]
            ).lower()
            if query and query not in haystack:
                continue
            self.source_combo.addItem(source.label, source)

        if self.source_combo.count() <= 0:
            self.source_combo.addItem("No sources match current filter", None)
            self.source_combo.blockSignals(False)
            self._on_source_changed(-1)
            return

        preferred_index = 0
        if current_key is not None:
            for idx in range(self.source_combo.count()):
                data = self.source_combo.itemData(idx)
                if not isinstance(data, MigrationSourceOption):
                    continue
                key = (
                    data.profile_index,
                    data.source_kind,
                    data.stem,
                    data.preferred_source_path,
                )
                if key == current_key:
                    preferred_index = idx
                    break

        self.source_combo.setCurrentIndex(preferred_index)
        self.source_combo.blockSignals(False)
        self._on_source_changed(preferred_index)

    def _current_source(self) -> Optional[MigrationSourceOption]:
        data = self.source_combo.currentData()
        if isinstance(data, MigrationSourceOption):
            return data
        return None

    def _on_source_changed(self, _index: int) -> None:
        source = self._current_source()
        has_source = source is not None
        self.preview_source_btn.setEnabled(has_source)
        self.run_btn.setEnabled(has_source and bool(self._profiles))
        if source is None:
            self._track_source_key = None
            self.set_source_track_options(None, [])
            self._resize_track_list_to_contents(self.track_list)
            self.target_revision_combo.setEnabled(True)
            self.mode_combo.setEnabled(True)
            self.remap_island_check.setEnabled(True)
            self.remap_island_spin.setEnabled(self.remap_island_check.isChecked())
            self.copy_assets_check.setEnabled(True)
            self.source_readout.setText("No source selected.")
            return

        self._apply_source_constraints(source)
        chosen_path = source.preferred_source_path or ""
        if source.is_dof_source:
            revision_text = "DOF source -> rev 6"
        elif source.revision_hint is not None:
            revision_text = f"rev {source.revision_hint}"
        else:
            revision_text = "unknown"

        self.source_readout.setText(
            f"Profile: {source.profile_name}\n"
            f"Type: {'DOF animation' if source.is_dof_source else 'BIN/JSON monster'}\n"
            f"Stem: {source.stem}\n"
            f"Detected source revision hint: {revision_text}\n"
            f"Source file: {chosen_path}"
        )

        if self._auto_target_token and source.token:
            self.target_token_edit.setText(source.token)
            self._request_target_tracks()
        self._track_source_key = self._source_key(source)
        self.set_source_track_options(source, None)
        self.source_tracks_requested.emit(source)

    def _apply_source_constraints(self, source: MigrationSourceOption) -> None:
        is_dof = source.is_dof_source
        rev_index = self.target_revision_combo.findData(6)
        if is_dof and rev_index >= 0:
            self.target_revision_combo.setCurrentIndex(rev_index)
        self.target_revision_combo.setEnabled(not is_dof)
        self.mode_combo.setEnabled(True)
        self.remap_island_check.setEnabled(True)
        self.remap_island_spin.setEnabled(self.remap_island_check.isChecked())
        if is_dof:
            self.copy_assets_check.setChecked(True)
        self.copy_assets_check.setEnabled(not is_dof)

    @staticmethod
    def _source_key(source: MigrationSourceOption) -> tuple:
        return (
            source.profile_index,
            source.source_kind,
            source.stem,
            source.preferred_source_path,
        )

    def set_source_track_options(
        self,
        source: Optional[MigrationSourceOption],
        track_names: Optional[List[str]],
    ) -> None:
        if source is not None and self._track_source_key is not None:
            if self._source_key(source) != self._track_source_key:
                return
        self.track_list.blockSignals(True)
        self.track_list.clear()
        if track_names is None:
            self.track_readout.setText("Loading tracks...")
            self.select_all_tracks_btn.setEnabled(False)
            self.clear_tracks_btn.setEnabled(False)
            self.track_list.blockSignals(False)
            self._resize_track_list_to_contents(self.track_list)
            return

        cleaned: List[str] = []
        seen = set()
        for name in track_names or []:
            text = str(name or "").strip()
            if not text or text.lower() in seen:
                continue
            seen.add(text.lower())
            cleaned.append(text)

        for name in cleaned:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.track_list.addItem(item)

        self.track_list.blockSignals(False)
        self._resize_track_list_to_contents(self.track_list)
        self.select_all_tracks_btn.setEnabled(bool(cleaned))
        self.clear_tracks_btn.setEnabled(bool(cleaned))
        if cleaned:
            self.track_readout.setText(f"{len(cleaned)} track(s) selected.")
        else:
            self.track_readout.setText("Track list unavailable; migration will include all source tracks.")

    def _selected_track_names(self) -> Optional[List[str]]:
        if self.track_list.count() <= 0:
            return None
        selected: List[str] = []
        for index in range(self.track_list.count()):
            item = self.track_list.item(index)
            if item and item.checkState() == Qt.CheckState.Checked:
                selected.append(item.text())
        return selected

    def _on_track_item_changed(self, _item: QListWidgetItem) -> None:
        selected = self._selected_track_names()
        total = self.track_list.count()
        if selected is None:
            self.track_readout.setText("Track list unavailable; migration will include all source tracks.")
        else:
            self.track_readout.setText(f"{len(selected)} of {total} track(s) selected.")

    def _select_all_tracks(self) -> None:
        self.track_list.blockSignals(True)
        for index in range(self.track_list.count()):
            item = self.track_list.item(index)
            if item:
                item.setCheckState(Qt.CheckState.Checked)
        self.track_list.blockSignals(False)
        self._on_track_item_changed(QListWidgetItem())

    def _clear_tracks(self) -> None:
        self.track_list.blockSignals(True)
        for index in range(self.track_list.count()):
            item = self.track_list.item(index)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.track_list.blockSignals(False)
        self._on_track_item_changed(QListWidgetItem())

    @staticmethod
    def _resize_track_list_to_contents(
        widget: QListWidget,
        *,
        min_rows: int = 2,
        max_rows: int = 7,
    ) -> None:
        count = max(0, int(widget.count()))
        visible_rows = max(min_rows, min(max_rows, count if count else min_rows))
        factor = clamp_ui_scale(widget.property("ui_scale_factor") or 1.0)
        row_height = widget.sizeHintForRow(0)
        if row_height <= 0:
            row_height = widget.fontMetrics().height() + scale_int(8, factor)
        frame = int(widget.frameWidth()) * 2
        spacing = max(0, int(widget.spacing())) * max(0, visible_rows - 1)
        margins = widget.contentsMargins()
        height = int(
            row_height * visible_rows
            + frame
            + spacing
            + margins.top()
            + margins.bottom()
            + scale_int(8, factor)
        )
        widget.setFixedHeight(
            max(
                scale_int(54, factor, minimum=40),
                min(scale_int(220, factor, minimum=120), height),
            )
        )

    @staticmethod
    def _normalized_token_text(raw: str) -> str:
        token = (raw or "").strip().lower()
        if token.startswith("monster_"):
            token = token[len("monster_"):]
        return token

    def _target_key(self) -> tuple:
        profile_index = self.target_profile_combo.currentData()
        try:
            profile_index = int(profile_index)
        except (TypeError, ValueError):
            profile_index = 0
        target_island = int(self.remap_island_spin.value()) if self.remap_island_check.isChecked() else None
        return (profile_index, self._normalized_target_token(), target_island)

    def _request_target_tracks(self, *_args: object) -> None:
        key = self._target_key()
        self._target_track_key = key
        profile_index, target_token, target_island = key
        if not target_token:
            self.set_target_track_options(profile_index, target_token, target_island, [])
            self._update_target_track_button_state()
            return
        self.set_target_track_options(profile_index, target_token, target_island, None)
        self._update_target_track_button_state()
        self.target_tracks_requested.emit(
            {
                "profile_index": profile_index,
                "target_token": target_token,
                "target_island": target_island,
                "island_mapping_enabled": self.remap_island_check.isChecked(),
            }
        )

    def set_target_track_options(
        self,
        profile_index: int,
        target_token: str,
        target_island: Optional[int],
        track_names: Optional[List[str]],
    ) -> None:
        try:
            island_key = int(target_island) if target_island is not None else None
        except (TypeError, ValueError):
            island_key = None
        key = (int(profile_index), self._normalized_token_text(target_token), island_key)
        if self._target_track_key is not None and key != self._target_track_key:
            return
        self.target_track_list.blockSignals(True)
        self.target_track_list.clear()
        if track_names is None:
            self.target_track_readout.setText("Loading target tracks...")
            self.select_all_target_tracks_btn.setEnabled(False)
            self.clear_target_tracks_btn.setEnabled(False)
            self.target_track_list.blockSignals(False)
            self._resize_track_list_to_contents(self.target_track_list)
            self._update_target_track_button_state()
            return

        cleaned: List[str] = []
        seen = set()
        for name in track_names or []:
            text = str(name or "").strip()
            if not text or text.lower() in seen:
                continue
            seen.add(text.lower())
            cleaned.append(text)

        for name in cleaned:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.target_track_list.addItem(item)

        self.target_track_list.blockSignals(False)
        self._resize_track_list_to_contents(self.target_track_list)
        self.select_all_target_tracks_btn.setEnabled(bool(cleaned))
        self.clear_target_tracks_btn.setEnabled(bool(cleaned))
        self._update_target_track_button_state()
        if island_key == 0:
            island_text = " for idle/sleep tracks"
        elif island_key is not None:
            island_text = f" for island {island_key}"
        else:
            island_text = ""
        if cleaned:
            has_island_prefixed_track = any(
                re.match(r"^[0-9]{1,3}[-_]", name or "") for name in cleaned
            )
            if island_key == 0:
                self.target_track_readout.setText(
                    f"{len(cleaned)} idle/sleep target track(s) available. Check tracks to overwrite."
                )
            elif island_key is not None and not has_island_prefixed_track:
                self.target_track_readout.setText(
                    f"{len(cleaned)} unprefixed main-game target track(s) available. Check tracks to overwrite."
                )
            else:
                self.target_track_readout.setText(
                    f"{len(cleaned)} main-game target track(s){island_text} available. Check tracks to overwrite."
                )
        elif self._normalized_token_text(target_token):
            self.target_track_readout.setText(
                f"No main-game target tracks found for this token{island_text}."
            )
        else:
            self.target_track_readout.setText("Set a target token to load overwrite targets.")

    def _selected_target_track_names(self) -> Optional[List[str]]:
        if self.target_track_list.count() <= 0:
            return None
        selected: List[str] = []
        for index in range(self.target_track_list.count()):
            item = self.target_track_list.item(index)
            if item and item.checkState() == Qt.CheckState.Checked:
                selected.append(item.text())
        return selected

    def _on_target_track_item_changed(self, _item: QListWidgetItem) -> None:
        selected = self._selected_target_track_names()
        total = self.target_track_list.count()
        if selected is None:
            self.target_track_readout.setText("No existing target tracks found for this token.")
        elif selected:
            self.target_track_readout.setText(f"{len(selected)} of {total} target track(s) selected for overwrite.")
        else:
            self.target_track_readout.setText(f"{total} target track(s) available. Check tracks to overwrite.")

    def _select_all_target_tracks(self) -> None:
        self.target_track_list.blockSignals(True)
        for index in range(self.target_track_list.count()):
            item = self.target_track_list.item(index)
            if item:
                item.setCheckState(Qt.CheckState.Checked)
        self.target_track_list.blockSignals(False)
        self._on_target_track_item_changed(QListWidgetItem())

    def _clear_target_tracks(self) -> None:
        self.target_track_list.blockSignals(True)
        for index in range(self.target_track_list.count()):
            item = self.target_track_list.item(index)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.target_track_list.blockSignals(False)
        self._on_target_track_item_changed(QListWidgetItem())

    def _update_target_track_button_state(self) -> None:
        has_token = bool(self._normalized_target_token())
        has_profiles = bool(self._profiles)
        has_tracks = self.target_track_list.count() > 0
        self.select_all_target_tracks_btn.setEnabled(has_tracks)
        self.clear_target_tracks_btn.setEnabled(has_tracks)
        self.refresh_target_tracks_btn.setEnabled(has_token and has_profiles)
        self.preview_target_btn.setEnabled(has_token and has_profiles)

    def _normalized_target_token(self) -> str:
        return self._normalized_token_text(self.target_token_edit.text())

    def build_request(self) -> Optional[MonsterMigrationRequest]:
        source = self._current_source()
        if source is None:
            return None

        profile_index = self.target_profile_combo.currentData()
        try:
            profile_index = int(profile_index)
        except (TypeError, ValueError):
            profile_index = 0

        target_revision = self.target_revision_combo.currentData()
        try:
            target_revision = int(target_revision)
        except (TypeError, ValueError):
            target_revision = 6

        target_token = self._normalized_target_token()

        return MonsterMigrationRequest(
            source=source,
            target_profile_index=profile_index,
            target_token=target_token,
            target_revision=target_revision,
            merge_with_existing=(self.mode_combo.currentData() == "merge"),
            remap_island_prefix=self.remap_island_check.isChecked(),
            target_island=int(self.remap_island_spin.value()),
            copy_assets=self.copy_assets_check.isChecked(),
            write_json_output=self.write_json_check.isChecked(),
            selected_animation_names=self._selected_track_names(),
            target_animation_names=self._selected_target_track_names(),
            open_legacy_tools=False,
        )

    def _emit_preview_source(self) -> None:
        source = self._current_source()
        if source is None:
            QMessageBox.warning(self, "Missing Source", "Choose a source monster from the pool.")
            return
        selected = self._selected_track_names()
        if selected is None or len(selected) != 1:
            QMessageBox.information(
                self,
                "Preview Track Selection",
                "Preview is only available while one source track is selected.",
            )
            return
        self.preview_source_requested.emit(
            {
                "source": source,
                "preferred_animation_names": selected,
            }
        )

    def _emit_preview_target(self) -> None:
        profile_index, target_token, target_island = self._target_key()
        if not target_token:
            QMessageBox.warning(self, "Missing Target", "Enter a target monster token first.")
            return
        selected = self._selected_target_track_names()
        if selected is None or len(selected) != 1:
            QMessageBox.information(
                self,
                "Preview Track Selection",
                "Preview is only available while one target track is selected.",
            )
            return
        self.preview_target_requested.emit(
            {
                "profile_index": profile_index,
                "target_token": target_token,
                "target_island": target_island,
                "island_mapping_enabled": self.remap_island_check.isChecked(),
                "preferred_animation_names": selected,
            }
        )

    def _emit_preview_output(self) -> None:
        if not self._last_output_path:
            QMessageBox.warning(self, "No Output", "No migrated output is available yet.")
            return
        self.preview_output_requested.emit(self._last_output_path)

    def _emit_run_request(self) -> None:
        request = self.build_request()
        if request is None:
            QMessageBox.warning(self, "Missing Source", "Choose a source monster from the pool.")
            return

        if request.target_profile_index < 0 or request.target_profile_index >= len(self._profiles):
            QMessageBox.warning(self, "Missing Target", "Choose a valid target profile.")
            return

        if not request.target_token:
            QMessageBox.warning(
                self,
                "Missing Target Token",
                "Enter a target monster token (for example: bowgart or tweedle_fire).",
            )
            return
        if request.selected_animation_names == []:
            QMessageBox.warning(
                self,
                "No Tracks Selected",
                "Select at least one source track to migrate.",
            )
            return
        if request.target_animation_names:
            if self.mode_combo.currentData() != "merge":
                QMessageBox.warning(
                    self,
                    "Merge Required",
                    "Target track overwrite requires Merge Animations Into Existing Target.",
                )
                return
            source_count = len(request.selected_animation_names or [])
            if source_count <= 0:
                QMessageBox.warning(
                    self,
                    "Missing Source Tracks",
                    "Select the source track(s) that should replace the target track(s).",
                )
                return
            if source_count != len(request.target_animation_names):
                QMessageBox.warning(
                    self,
                    "Track Count Mismatch",
                    "Select the same number of source tracks and target tracks for overwrite.",
                )
                return

        self.run_requested.emit(request)
