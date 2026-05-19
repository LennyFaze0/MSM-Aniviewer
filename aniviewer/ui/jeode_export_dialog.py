"""
Jeode Mod Export Dialog
Dedicated export flow for packaging current animation output as a Jeode mod.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass
class JeodeOverrideMapping:
    game_path: str
    source_path: str


@dataclass
class JeodeModExportRequest:
    mods_root: str
    mod_id: str
    mod_name: str
    author: str
    version: str
    game_version: str
    enabled: bool
    error_on_game_update: bool
    load_priority: int
    dependencies: List[str]
    entry_script: str
    create_init_script: bool
    auto_override: bool
    manual_overrides: List[JeodeOverrideMapping]
    target_revision: int
    target_stem: str
    copy_assets: bool
    write_json_output: bool


class JeodeModExportDialog(QDialog):
    """Collect Jeode export options for the current animation."""

    def __init__(
        self,
        *,
        default_mods_root: str,
        default_mod_id: str,
        default_mod_name: str,
        default_target_stem: str,
        available_revisions: List[int],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._available_revisions = list(available_revisions or [6])

        self.setWindowTitle("Export as Jeode Mod")
        self.setMinimumWidth(860)
        self.setMinimumHeight(680)

        self._build_ui(
            default_mods_root=default_mods_root,
            default_mod_id=default_mod_id,
            default_mod_name=default_mod_name,
            default_target_stem=default_target_stem,
        )

    def _build_ui(
        self,
        *,
        default_mods_root: str,
        default_mod_id: str,
        default_mod_name: str,
        default_target_stem: str,
    ) -> None:
        root = QVBoxLayout(self)

        intro = QLabel(
            "Export the currently loaded animation as a Jeode-ready mod. "
            "This writes BIN/asset data into a mod folder and generates a manifest.json."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray; font-size: 9pt;")
        root.addWidget(intro)

        path_group = QGroupBox("Destination")
        path_layout = QFormLayout()

        mods_root_row = QHBoxLayout()
        self.mods_root_edit = QLineEdit(default_mods_root or "")
        self.mods_root_edit.setPlaceholderText("/path/to/My Singing Monsters/mods")
        mods_root_row.addWidget(self.mods_root_edit, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_mods_root)
        mods_root_row.addWidget(browse_btn)
        path_layout.addRow("Mods Folder:", mods_root_row)

        self.target_stem_edit = QLineEdit(default_target_stem or "monster_custom")
        self.target_stem_edit.setPlaceholderText("monster_custom")
        path_layout.addRow("Target BIN Stem:", self.target_stem_edit)

        self.target_revision_combo = QComboBox()
        for revision in sorted(set(self._available_revisions), reverse=True):
            self.target_revision_combo.addItem(f"Rev {int(revision)}", int(revision))
        default_index = self.target_revision_combo.findData(6)
        if default_index < 0:
            default_index = 0
        self.target_revision_combo.setCurrentIndex(default_index)
        path_layout.addRow("Target BIN Revision:", self.target_revision_combo)

        self.copy_assets_check = QCheckBox("Copy XML and atlas/image assets referenced by sources")
        self.copy_assets_check.setChecked(True)
        path_layout.addRow("Asset Copy:", self.copy_assets_check)

        self.write_json_check = QCheckBox("Also write JSON next to exported BIN")
        self.write_json_check.setChecked(False)
        path_layout.addRow("Debug Output:", self.write_json_check)

        path_group.setLayout(path_layout)
        root.addWidget(path_group)

        manifest_group = QGroupBox("Manifest")
        manifest_layout = QFormLayout()

        self.mod_id_edit = QLineEdit(default_mod_id or "aniviewer-custom")
        self.mod_id_edit.setPlaceholderText("aniviewer-my-monster")
        manifest_layout.addRow("Mod ID:", self.mod_id_edit)

        self.mod_name_edit = QLineEdit(default_mod_name or "AniViewer Export")
        manifest_layout.addRow("Mod Name:", self.mod_name_edit)

        self.author_edit = QLineEdit("AniViewer")
        manifest_layout.addRow("Author:", self.author_edit)

        self.version_edit = QLineEdit("1.0.0")
        manifest_layout.addRow("Version:", self.version_edit)

        self.game_version_edit = QLineEdit("*")
        manifest_layout.addRow("Game Version:", self.game_version_edit)

        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(True)
        manifest_layout.addRow("State:", self.enabled_check)

        self.error_on_update_check = QCheckBox("Error on game version mismatch")
        self.error_on_update_check.setChecked(False)
        manifest_layout.addRow("Version Gate:", self.error_on_update_check)

        self.load_priority_spin = QSpinBox()
        self.load_priority_spin.setRange(-10000, 10000)
        self.load_priority_spin.setValue(0)
        manifest_layout.addRow("Load Priority:", self.load_priority_spin)

        self.dependencies_edit = QLineEdit()
        self.dependencies_edit.setPlaceholderText("mod-a, mod-b")
        manifest_layout.addRow("Dependencies:", self.dependencies_edit)

        self.entry_script_edit = QLineEdit("init.lua")
        manifest_layout.addRow("Entry Script:", self.entry_script_edit)

        self.create_entry_check = QCheckBox("Create entry script if missing")
        self.create_entry_check.setChecked(True)
        manifest_layout.addRow("Entry File:", self.create_entry_check)

        manifest_group.setLayout(manifest_layout)
        root.addWidget(manifest_group)

        override_group = QGroupBox("Assets Overrides")
        override_layout = QVBoxLayout()

        self.auto_override_check = QCheckBox("Enable assets.auto_override (scan mod/data automatically)")
        self.auto_override_check.setChecked(True)
        override_layout.addWidget(self.auto_override_check)

        override_hint = QLabel(
            "Manual assets.overrides rows are optional. Use them to map custom game paths to specific files. "
            "Source files are copied into this mod under custom_overrides/."
        )
        override_hint.setWordWrap(True)
        override_hint.setStyleSheet("color: gray; font-size: 8pt;")
        override_layout.addWidget(override_hint)

        self.override_table = QTableWidget(0, 2)
        self.override_table.setHorizontalHeaderLabels(["Game Path", "Source File"])
        self.override_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.override_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.override_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.override_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.override_table.setMinimumHeight(190)
        override_layout.addWidget(self.override_table)

        override_actions = QHBoxLayout()
        add_override_btn = QPushButton("Add Mapping")
        add_override_btn.clicked.connect(self._add_override_row)
        override_actions.addWidget(add_override_btn)

        browse_override_btn = QPushButton("Browse Source...")
        browse_override_btn.clicked.connect(self._browse_override_source)
        override_actions.addWidget(browse_override_btn)

        remove_override_btn = QPushButton("Remove Selected")
        remove_override_btn.clicked.connect(self._remove_override_row)
        override_actions.addWidget(remove_override_btn)
        override_actions.addStretch(1)
        override_layout.addLayout(override_actions)

        override_group.setLayout(override_layout)
        root.addWidget(override_group)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        run_btn = QPushButton("Export Jeode Mod")
        run_btn.setDefault(True)
        run_btn.clicked.connect(self.accept)
        button_row.addWidget(run_btn)
        root.addLayout(button_row)

    def _browse_mods_root(self) -> None:
        start_dir = self.mods_root_edit.text().strip() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Select Mods Folder", start_dir)
        if folder:
            self.mods_root_edit.setText(folder)

    def _selected_override_row(self) -> int:
        row = self.override_table.currentRow()
        if row < 0 and self.override_table.rowCount() > 0:
            row = self.override_table.rowCount() - 1
        return row

    def _add_override_row(self) -> None:
        row = self.override_table.rowCount()
        self.override_table.insertRow(row)
        self.override_table.setItem(row, 0, QTableWidgetItem(""))
        self.override_table.setItem(row, 1, QTableWidgetItem(""))
        self.override_table.setCurrentCell(row, 0)

    def _remove_override_row(self) -> None:
        row = self._selected_override_row()
        if row < 0:
            return
        self.override_table.removeRow(row)

    def _browse_override_source(self) -> None:
        row = self._selected_override_row()
        if row < 0:
            QMessageBox.information(self, "Select Mapping", "Add/select a mapping row first.")
            return
        source_item = self.override_table.item(row, 1)
        start_dir = source_item.text().strip() if source_item else ""
        if start_dir and not os.path.exists(start_dir):
            start_dir = os.path.dirname(start_dir)
        if not start_dir:
            start_dir = os.path.expanduser("~")
        filename, _ = QFileDialog.getOpenFileName(self, "Select Source Override File", start_dir, "All Files (*)")
        if not filename:
            return
        if source_item is None:
            source_item = QTableWidgetItem("")
            self.override_table.setItem(row, 1, source_item)
        source_item.setText(filename)

    @staticmethod
    def _sanitize_mod_id(raw: str) -> str:
        value = (raw or "").strip().lower()
        value = re.sub(r"[^a-z0-9_-]+", "-", value)
        value = re.sub(r"[-_]{2,}", "-", value)
        return value.strip("-_")

    @staticmethod
    def _normalize_stem(raw: str) -> str:
        stem = (raw or "").strip().lower()
        if stem.endswith(".bin"):
            stem = stem[:-4]
        stem = re.sub(r"[^a-z0-9_]+", "_", stem).strip("_")
        if not stem:
            return ""
        if not stem.startswith("monster_"):
            stem = f"monster_{stem}"
        return stem

    @staticmethod
    def _normalize_entry_script(raw: str) -> str:
        value = (raw or "").strip().replace("\\", "/")
        while value.startswith("/"):
            value = value[1:]
        return value or "init.lua"

    def _collect_manual_overrides(self) -> List[JeodeOverrideMapping]:
        mappings: List[JeodeOverrideMapping] = []
        for row in range(self.override_table.rowCount()):
            game_item = self.override_table.item(row, 0)
            source_item = self.override_table.item(row, 1)
            game_path = (game_item.text().strip() if game_item else "").replace("\\", "/")
            source_path = source_item.text().strip() if source_item else ""
            if not game_path and not source_path:
                continue
            mappings.append(
                JeodeOverrideMapping(
                    game_path=game_path,
                    source_path=source_path,
                )
            )
        return mappings

    def build_request(self) -> JeodeModExportRequest:
        mod_id = self._sanitize_mod_id(self.mod_id_edit.text())
        stem = self._normalize_stem(self.target_stem_edit.text())
        entry_script = self._normalize_entry_script(self.entry_script_edit.text())

        dep_text = self.dependencies_edit.text() or ""
        dependencies = [chunk.strip() for chunk in re.split(r"[\n,]+", dep_text) if chunk.strip()]

        revision_value = self.target_revision_combo.currentData()
        try:
            target_revision = int(revision_value)
        except (TypeError, ValueError):
            target_revision = 6

        return JeodeModExportRequest(
            mods_root=self.mods_root_edit.text().strip(),
            mod_id=mod_id,
            mod_name=(self.mod_name_edit.text() or "").strip(),
            author=(self.author_edit.text() or "").strip(),
            version=(self.version_edit.text() or "").strip(),
            game_version=(self.game_version_edit.text() or "").strip() or "*",
            enabled=self.enabled_check.isChecked(),
            error_on_game_update=self.error_on_update_check.isChecked(),
            load_priority=int(self.load_priority_spin.value()),
            dependencies=dependencies,
            entry_script=entry_script,
            create_init_script=self.create_entry_check.isChecked(),
            auto_override=self.auto_override_check.isChecked(),
            manual_overrides=self._collect_manual_overrides(),
            target_revision=target_revision,
            target_stem=stem,
            copy_assets=self.copy_assets_check.isChecked(),
            write_json_output=self.write_json_check.isChecked(),
        )

    def accept(self) -> None:
        request = self.build_request()

        if not request.mods_root:
            QMessageBox.warning(self, "Missing Folder", "Choose a destination mods folder.")
            return
        try:
            os.makedirs(request.mods_root, exist_ok=True)
        except Exception as exc:
            QMessageBox.warning(self, "Folder Error", f"Could not create mods folder:\n{exc}")
            return

        if not request.mod_id:
            QMessageBox.warning(
                self,
                "Invalid Mod ID",
                "Enter a valid mod id using letters, numbers, '_' or '-'.",
            )
            return

        if not request.target_stem:
            QMessageBox.warning(self, "Invalid Target", "Enter a valid target BIN stem.")
            return

        if ".." in request.entry_script or os.path.isabs(request.entry_script):
            QMessageBox.warning(
                self,
                "Invalid Entry Script",
                "Entry script must be a relative path inside the mod folder.",
            )
            return

        has_manual = False
        for mapping in request.manual_overrides:
            if not mapping.game_path or not mapping.source_path:
                QMessageBox.warning(
                    self,
                    "Incomplete Override",
                    "Each manual assets.overrides row needs both game path and source file.",
                )
                return
            if not os.path.isfile(mapping.source_path):
                QMessageBox.warning(
                    self,
                    "Missing Override Source",
                    f"Override source file does not exist:\n{mapping.source_path}",
                )
                return
            has_manual = True

        if not request.auto_override and not has_manual:
            QMessageBox.warning(
                self,
                "No Overrides",
                "assets.auto_override is disabled and no manual assets.overrides mappings were provided.",
            )
            return

        if not request.mod_name:
            request.mod_name = request.mod_id
            self.mod_name_edit.setText(request.mod_name)

        if not request.author:
            request.author = "AniViewer"
            self.author_edit.setText(request.author)

        if not request.version:
            request.version = "1.0.0"
            self.version_edit.setText(request.version)

        super().accept()
