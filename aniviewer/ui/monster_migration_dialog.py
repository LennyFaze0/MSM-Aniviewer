"""
Monster Migration Dialog
Simple, powerful workflow for cross-revision monster migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


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

    @property
    def preferred_source_path(self) -> Optional[str]:
        return self.bin_path or self.json_path


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
        self.remap_island_spin.setRange(1, 999)
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
            current_key = (current_source.profile_index, current_source.stem)

        self.source_combo.blockSignals(True)
        self.source_combo.clear()

        for source in self._all_sources:
            haystack = " ".join(
                [
                    source.label,
                    source.token,
                    source.stem,
                    source.profile_name,
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
                key = (data.profile_index, data.stem)
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
            self.source_readout.setText("No source selected.")
            return

        chosen_path = source.preferred_source_path or ""
        revision_text = "unknown"
        if source.revision_hint is not None:
            revision_text = f"rev {source.revision_hint}"

        self.source_readout.setText(
            f"Profile: {source.profile_name}\n"
            f"Stem: {source.stem}\n"
            f"Detected source revision hint: {revision_text}\n"
            f"Source file: {chosen_path}"
        )

        if self._auto_target_token and source.token:
            self.target_token_edit.setText(source.token)

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
    preview_output_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()
    open_legacy_tools_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._all_sources: List[MigrationSourceOption] = []
        self._profiles: List[Dict[str, Any]] = []
        self._available_revisions: List[int] = []
        self._active_profile_index: int = 0
        self._auto_target_token: bool = True
        self._last_output_path: str = ""
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(8)

        title = QLabel("Monster Migration Workspace")
        title.setStyleSheet("font-weight: 600;")
        root.addWidget(title)

        intro = QLabel(
            "Use source/target preview to validate XML + spritesheet compatibility in the live viewport "
            "before and after migration."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray; font-size: 9pt;")
        root.addWidget(intro)

        source_group = QGroupBox("Source Monster")
        source_layout = QVBoxLayout()

        source_search_row = QHBoxLayout()
        source_search_row.addWidget(QLabel("Filter:"))
        self.source_search_edit = QLineEdit()
        self.source_search_edit.setPlaceholderText("name / token / profile...")
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

        source_actions = QHBoxLayout()
        self.refresh_sources_btn = QPushButton("Refresh Pool")
        self.refresh_sources_btn.clicked.connect(self.refresh_requested.emit)
        source_actions.addWidget(self.refresh_sources_btn)
        self.preview_source_btn = QPushButton("Preview Source")
        self.preview_source_btn.clicked.connect(self._emit_preview_source)
        source_actions.addWidget(self.preview_source_btn)
        source_actions.addStretch(1)
        source_layout.addLayout(source_actions)

        source_group.setLayout(source_layout)
        root.addWidget(source_group)

        target_group = QGroupBox("Target")
        target_layout = QFormLayout()

        self.target_profile_combo = QComboBox()
        target_layout.addRow("Target Profile:", self.target_profile_combo)

        self.target_token_edit = QLineEdit()
        self.target_token_edit.setPlaceholderText("monster token (without monster_)")
        self.target_token_edit.textEdited.connect(self._on_target_token_edited)
        target_layout.addRow("Target Token:", self.target_token_edit)

        self.target_revision_combo = QComboBox()
        target_layout.addRow("Target BIN Revision:", self.target_revision_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Create or Replace Target Monster", "replace")
        self.mode_combo.addItem("Merge Animations Into Existing Target", "merge")
        target_layout.addRow("Migration Mode:", self.mode_combo)

        island_row = QHBoxLayout()
        self.remap_island_check = QCheckBox("Rewrite animation island prefix")
        self.remap_island_check.setChecked(True)
        self.remap_island_spin = QSpinBox()
        self.remap_island_spin.setRange(1, 999)
        self.remap_island_spin.setValue(1)
        self.remap_island_spin.setSuffix(" target island")
        self.remap_island_check.toggled.connect(self.remap_island_spin.setEnabled)
        island_row.addWidget(self.remap_island_check)
        island_row.addWidget(self.remap_island_spin)
        island_row.addStretch(1)
        target_layout.addRow("Island Mapping:", island_row)

        self.copy_assets_check = QCheckBox("Copy XML and atlas assets")
        self.copy_assets_check.setChecked(True)
        target_layout.addRow("Assets:", self.copy_assets_check)

        self.write_json_check = QCheckBox("Also write target JSON")
        self.write_json_check.setChecked(False)
        target_layout.addRow("Debug Output:", self.write_json_check)

        target_group.setLayout(target_layout)
        root.addWidget(target_group)

        preview_group = QGroupBox("Viewport Validation")
        preview_layout = QVBoxLayout()
        self.output_readout = QLabel("No migrated output generated yet.")
        self.output_readout.setWordWrap(True)
        self.output_readout.setStyleSheet("color: gray; font-size: 9pt;")
        preview_layout.addWidget(self.output_readout)
        output_row = QHBoxLayout()
        self.preview_output_btn = QPushButton("Preview Last Output")
        self.preview_output_btn.setEnabled(False)
        self.preview_output_btn.clicked.connect(self._emit_preview_output)
        output_row.addWidget(self.preview_output_btn)
        output_row.addStretch(1)
        preview_layout.addLayout(output_row)
        preview_group.setLayout(preview_layout)
        root.addWidget(preview_group)

        button_row = QHBoxLayout()
        self.open_legacy_btn = QPushButton("Open Advanced Bin Converter")
        self.open_legacy_btn.clicked.connect(self.open_legacy_tools_requested.emit)
        button_row.addWidget(self.open_legacy_btn)
        button_row.addStretch(1)
        self.run_btn = QPushButton("Run Migration")
        self.run_btn.clicked.connect(self._emit_run_request)
        button_row.addWidget(self.run_btn)
        root.addLayout(button_row)
        root.addStretch(1)

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

    def _refresh_source_combo(self) -> None:
        query = (self.source_search_edit.text() or "").strip().lower()
        current_key = None
        current_source = self._current_source()
        if current_source:
            current_key = (current_source.profile_index, current_source.stem)

        self.source_combo.blockSignals(True)
        self.source_combo.clear()

        for source in self._all_sources:
            haystack = " ".join(
                [
                    source.label,
                    source.token,
                    source.stem,
                    source.profile_name,
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
                key = (data.profile_index, data.stem)
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
            self.source_readout.setText("No source selected.")
            return

        chosen_path = source.preferred_source_path or ""
        revision_text = "unknown"
        if source.revision_hint is not None:
            revision_text = f"rev {source.revision_hint}"

        self.source_readout.setText(
            f"Profile: {source.profile_name}\n"
            f"Stem: {source.stem}\n"
            f"Detected source revision hint: {revision_text}\n"
            f"Source file: {chosen_path}"
        )

        if self._auto_target_token and source.token:
            self.target_token_edit.setText(source.token)

    def _normalized_target_token(self) -> str:
        raw = (self.target_token_edit.text() or "").strip().lower()
        if raw.startswith("monster_"):
            raw = raw[len("monster_"):]
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
            open_legacy_tools=False,
        )

    def _emit_preview_source(self) -> None:
        source = self._current_source()
        if source is None:
            QMessageBox.warning(self, "Missing Source", "Choose a source monster from the pool.")
            return
        self.preview_source_requested.emit(source)

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

        self.run_requested.emit(request)
