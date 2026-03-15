"""
Sprite Workshop Dialog
Provides sprite export/import tooling for custom atlas editing.
"""

import os
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLineEdit,
    QFrame,
    QFileDialog,
    QMessageBox,
    QComboBox,
    QSizePolicy,
    QCheckBox,
)


class SpriteWorkshopDialog(QDialog):
    """UI wrapper for sprite export/import flows."""

    def __init__(self, main_window: "Any"):
        super().__init__(main_window)
        self.setWindowTitle("Sprite Workshop")
        self.resize(960, 560)
        self.main_window = main_window
        self._atlas_entries: List[Dict[str, Any]] = []
        self._current_atlas_index: int = 0
        self._sprite_lookup: Dict[str, Any] = {}

        self._build_ui()
        self.refresh_entries()

    # ------------------------------------------------------------------ UI setup

    def _build_ui(self):
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(QLabel("Atlas:"))

        self.atlas_combo = QComboBox()
        self.atlas_combo.currentIndexChanged.connect(self._on_atlas_changed)
        toolbar.addWidget(self.atlas_combo, 1)

        self.refresh_btn = QPushButton("Reload")
        self.refresh_btn.clicked.connect(self.refresh_entries)
        toolbar.addWidget(self.refresh_btn, 0)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search sprites…")
        self.search_box.textChanged.connect(self._update_sprite_list)
        search_row.addWidget(QLabel("Filter:"))
        search_row.addWidget(self.search_box)
        layout.addLayout(search_row)

        content_row = QHBoxLayout()
        content_row.setSpacing(12)

        self.sprite_list = QListWidget()
        self.sprite_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.sprite_list.itemSelectionChanged.connect(self._update_preview_panel)
        self.sprite_list.currentItemChanged.connect(self._update_preview_panel)
        content_row.addWidget(self.sprite_list, 2)

        side_panel = QVBoxLayout()
        side_panel.setSpacing(8)

        preview_frame = QFrame()
        preview_frame.setFrameShape(QFrame.Shape.StyledPanel)
        preview_frame.setStyleSheet("background: #1d1d1d;")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(8, 8, 8, 8)

        self.preview_label = QLabel("Select a sprite to preview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(260, 260)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_layout.addWidget(self.preview_label)

        self.preview_meta = QLabel("")
        self.preview_meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_meta.setStyleSheet("color: #bbb; font-size: 10pt;")
        preview_layout.addWidget(self.preview_meta)
        side_panel.addWidget(preview_frame, 1)

        button_grid = QVBoxLayout()
        button_grid.setSpacing(6)

        self.replace_btn = QPushButton("Replace Sprite…")
        self.replace_btn.clicked.connect(self._on_replace_clicked)
        button_grid.addWidget(self.replace_btn)

        self.remove_btn = QPushButton("Remove Replacement")
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        button_grid.addWidget(self.remove_btn)

        self.export_selected_btn = QPushButton("Export Selected Sprites…")
        self.export_selected_btn.clicked.connect(self._on_export_selected_clicked)
        button_grid.addWidget(self.export_selected_btn)

        self.export_all_btn = QPushButton("Export Entire Atlas…")
        self.export_all_btn.clicked.connect(self._on_export_all_clicked)
        button_grid.addWidget(self.export_all_btn)

        self.export_sheet_btn = QPushButton("Export Spritesheet + XML…")
        self.export_sheet_btn.clicked.connect(self._on_export_sheet_clicked)
        button_grid.addWidget(self.export_sheet_btn)

        self.import_sheet_btn = QPushButton("Import Spritesheet…")
        self.import_sheet_btn.clicked.connect(self._on_import_sheet_clicked)
        button_grid.addWidget(self.import_sheet_btn)

        side_panel.addLayout(button_grid)

        self.bake_recolor_checkbox = QCheckBox("Bake active recolors when exporting")
        self.bake_recolor_checkbox.setToolTip(
            "If enabled, Rare/Epic recolor tints from the currently loaded animation "
            "are baked into exported sprites and spritesheets."
        )
        side_panel.addWidget(self.bake_recolor_checkbox)

        self.premultiply_checkbox = QCheckBox("Premultiply alpha when exporting PNGs")
        self.premultiply_checkbox.setToolTip(
            "If enabled, exported sprites and sheets will have RGB multiplied by their alpha channel."
        )
        side_panel.addWidget(self.premultiply_checkbox)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #8ab4f8;")
        side_panel.addWidget(self.status_label)

        content_row.addLayout(side_panel, 1)
        layout.addLayout(content_row)

    # ------------------------------------------------------------------ Helpers

    def refresh_entries(self):
        """Reload atlas entries from the main window."""
        entries = self.main_window.get_sprite_workshop_entries()
        self._atlas_entries = entries
        old_key = None
        if 0 <= self._current_atlas_index < len(self._atlas_entries):
            old_key = self._atlas_entries[self._current_atlas_index]["key"]
        self.atlas_combo.blockSignals(True)
        self.atlas_combo.clear()
        for entry in self._atlas_entries:
            label = f"{entry['label']} ({entry['sprite_count']} sprites)"
            if entry["modified"]:
                label += f" – {entry['modified']} modified"
            self.atlas_combo.addItem(label)
        self.atlas_combo.blockSignals(False)
        if old_key:
            for idx, entry in enumerate(self._atlas_entries):
                if entry["key"] == old_key:
                    self._current_atlas_index = idx
                    break
        if self._atlas_entries:
            self.atlas_combo.setCurrentIndex(self._current_atlas_index)
        else:
            self._current_atlas_index = -1
        self._update_sprite_list()

    def _current_atlas_entry(self) -> Optional[Dict[str, Any]]:
        if 0 <= self._current_atlas_index < len(self._atlas_entries):
            return self._atlas_entries[self._current_atlas_index]
        return None

    def _current_atlas(self):
        entry = self._current_atlas_entry()
        return entry["atlas"] if entry else None

    def _on_atlas_changed(self, index: int):
        self._current_atlas_index = index
        self._update_sprite_list()

    def _update_sprite_list(self):
        atlas = self._current_atlas()
        self.sprite_list.clear()
        self._sprite_lookup.clear()
        if not atlas:
            self._update_preview_panel()
            self._update_status_label()
            return
        sprites = self.main_window.list_sprites_for_atlas(atlas)
        query = self.search_box.text().strip().lower()
        for sprite in sprites:
            if query and query not in sprite.name.lower():
                continue
            display = f"{sprite.name} — {int(sprite.w)}x{int(sprite.h)}"
            if sprite.rotated:
                display += " (rotated)"
            if self.main_window.is_sprite_modified(atlas, sprite.name):
                display = "★ " + display
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, sprite.name)
            self.sprite_list.addItem(item)
            self._sprite_lookup[sprite.name] = sprite
        self._update_status_label()
        self._update_preview_panel()

    def _selected_sprite_infos(self) -> List[Any]:
        atlas = self._current_atlas()
        if not atlas:
            return []
        selected = []
        for item in self.sprite_list.selectedItems():
            sprite_name = item.data(Qt.ItemDataRole.UserRole)
            sprite = self._sprite_lookup.get(sprite_name)
            if sprite:
                selected.append(sprite)
        return selected

    def _update_preview_panel(self):
        atlas = self._current_atlas()
        sprites = self._selected_sprite_infos()
        if not atlas or not sprites:
            self.preview_label.clear()
            self.preview_label.setText("Select a sprite to preview")
            self.preview_meta.setText("")
            return
        sprite = sprites[0]
        pixmap = self.main_window.sprite_preview_pixmap(atlas, sprite)
        if pixmap:
            self.preview_label.setPixmap(pixmap)
            self.preview_label.setText("")
        else:
            self.preview_label.clear()
            self.preview_label.setText("Unable to preview sprite.")
        status_bits = [
            f"{sprite.name}",
            f"{int(sprite.w)}x{int(sprite.h)} px",
        ]
        if sprite.rotated:
            status_bits.append("stored rotated")
        if self.main_window.is_sprite_modified(atlas, sprite.name):
            status_bits.append("modified")
        self.preview_meta.setText(" • ".join(status_bits))
        self._update_status_label()

    def _update_status_label(self):
        atlas = self._current_atlas()
        if not atlas:
            self.status_label.setText("No atlases available.")
            self._set_button_state(enabled=False)
            return
        total = len(self._sprite_lookup)
        selected = len(self.sprite_list.selectedItems())
        modified = sum(
            1 for sprite in self._sprite_lookup.values() if self.main_window.is_sprite_modified(atlas, sprite.name)
        )
        parts = [f"{total} sprite{'s' if total != 1 else ''}"]
        if modified:
            parts.append(f"{modified} modified")
        if selected:
            parts.append(f"{selected} selected")
        self.status_label.setText(" • ".join(parts))
        self._set_button_state(enabled=True)

    def _set_button_state(self, enabled: bool):
        for button in (
            self.replace_btn,
            self.remove_btn,
            self.export_selected_btn,
            self.export_all_btn,
            self.export_sheet_btn,
            self.import_sheet_btn,
        ):
            button.setEnabled(enabled)

    # ------------------------------------------------------------------ Actions

    def _on_replace_clicked(self):
        atlas = self._current_atlas()
        sprites = self._selected_sprite_infos()
        if not atlas:
            return
        if len(sprites) != 1:
            QMessageBox.information(self, "Sprite Workshop", "Select exactly one sprite to replace.")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select replacement sprite",
            "",
            "PNG Images (*.png);;All Files (*)",
        )
        if not file_path:
            return
        success, message = self.main_window.replace_sprite_from_file(atlas, sprites[0], file_path)
        if not success:
            QMessageBox.warning(self, "Sprite Workshop", message)
        self._update_sprite_list()

    def _on_remove_clicked(self):
        atlas = self._current_atlas()
        sprites = self._selected_sprite_infos()
        if not atlas or not sprites:
            QMessageBox.information(self, "Sprite Workshop", "Select sprites that have replacements applied.")
            return
        removed = 0
        for sprite in sprites:
            if self.main_window.remove_sprite_replacement(atlas, sprite):
                removed += 1
        if removed == 0:
            QMessageBox.information(self, "Sprite Workshop", "No selected sprites had replacements.")
        self._update_sprite_list()

    def _select_export_destination(self) -> Optional[str]:
        target_dir = QFileDialog.getExistingDirectory(self, "Choose export destination")
        return target_dir or None

    def _on_export_selected_clicked(self):
        atlas = self._current_atlas()
        sprites = self._selected_sprite_infos()
        if not atlas or not sprites:
            QMessageBox.information(self, "Sprite Workshop", "Select one or more sprites first.")
            return
        destination = self._select_export_destination()
        if not destination:
            return
        ok, message = self.main_window.export_sprite_segments(
            atlas,
            [sprite.name for sprite in sprites],
            destination,
            bake_recolor=self.bake_recolor_checkbox.isChecked(),
            premultiply_alpha=self.premultiply_checkbox.isChecked(),
        )
        if not ok:
            QMessageBox.warning(self, "Sprite Workshop", message)

    def _on_export_all_clicked(self):
        atlas = self._current_atlas()
        if not atlas:
            return
        destination = self._select_export_destination()
        if not destination:
            return
        ok, message = self.main_window.export_sprite_segments(
            atlas,
            [],
            destination,
            bake_recolor=self.bake_recolor_checkbox.isChecked(),
            premultiply_alpha=self.premultiply_checkbox.isChecked(),
        )
        if not ok:
            QMessageBox.warning(self, "Sprite Workshop", message)

    def _on_export_sheet_clicked(self):
        atlas = self._current_atlas()
        if not atlas:
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Spritesheet",
            "",
            "PNG Images (*.png)",
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".png"):
            file_path += ".png"
        ok, message = self.main_window.export_modified_spritesheet(
            atlas,
            file_path,
            bake_recolor=self.bake_recolor_checkbox.isChecked(),
            premultiply_alpha=self.premultiply_checkbox.isChecked(),
        )
        if not ok:
            QMessageBox.warning(self, "Sprite Workshop", message)

    def _on_import_sheet_clicked(self):
        atlas = self._current_atlas()
        if not atlas:
            return
        png_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Spritesheet",
            "",
            "PNG Images (*.png);;All Files (*)",
        )
        if not png_path:
            return
        xml_path = os.path.splitext(png_path)[0] + ".xml"
        if not os.path.exists(xml_path):
            xml_path, _ = QFileDialog.getOpenFileName(
                self,
                "Select Spritesheet XML",
                os.path.dirname(png_path),
                "Texture Atlas XML (*.xml);;All Files (*)",
            )
            if not xml_path:
                return
        ok, message = self.main_window.import_spritesheet_into_atlas(atlas, png_path, xml_path)
        if not ok:
            QMessageBox.warning(self, "Sprite Workshop", message)
        else:
            self._update_sprite_list()
