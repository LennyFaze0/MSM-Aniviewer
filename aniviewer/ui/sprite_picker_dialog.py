"""
Sprite selection dialog for assigning sprites to keyframes.
"""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
)
from PyQt6.QtCore import Qt, QItemSelectionModel, QSize
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor
from typing import List, Optional, Tuple


class SpritePickerDialog(QDialog):
    """Simple searchable dialog for choosing a sprite name."""

    def __init__(
        self,
        sprite_entries: List[Tuple[str, Optional[QPixmap]]],
        *,
        current_sprite: Optional[str] = None,
        description: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Select Sprite")
        self.resize(420, 480)
        self._thumbnail_size = QSize(44, 44)
        self._item_size_hint = QSize(0, max(32, self._thumbnail_size.height() + 12))
        self._placeholder_icon = self._build_placeholder_icon()
        self._all_entries = []
        for name, pixmap in sprite_entries:
            icon = self._build_icon(pixmap)
            self._all_entries.append({"name": name, "icon": icon})
        self._current_selection = current_sprite or ""

        layout = QVBoxLayout(self)

        if description:
            desc_label = QLabel(description)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet("color: gray; font-size: 9pt;")
            layout.addWidget(desc_label)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search sprites...")
        self.search_box.textChanged.connect(self._filter_list)
        layout.addWidget(self.search_box)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.setIconSize(self._thumbnail_size)
        self.list_widget.itemDoubleClicked.connect(self._accept_on_double_click)
        layout.addWidget(self.list_widget, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(ok_btn)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

        self._filter_list("")

    def _filter_list(self, text: str):
        """Filter list entries by search text."""
        normalized = (text or "").strip().lower()
        selected_text = self.selected_sprite()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for entry in self._all_entries:
            name = entry["name"]
            if normalized and normalized not in name.lower():
                continue
            item = QListWidgetItem(name)
            icon = entry.get("icon") or self._placeholder_icon
            if isinstance(icon, QIcon):
                item.setIcon(icon)
            item.setSizeHint(self._item_size_hint)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        target = selected_text or self._current_selection
        if target:
            self._select_text(target)
        elif self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _select_text(self, text: str):
        """Select the entry matching text if it exists."""
        for idx in range(self.list_widget.count()):
            item = self.list_widget.item(idx)
            if item and item.text() == text:
                self.list_widget.setCurrentItem(
                    item,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect
                )
                self.list_widget.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
                return

    def _accept_on_double_click(self, _item: QListWidgetItem):
        """Accept dialog on double-click if an item is selected."""
        self.accept()

    def selected_sprite(self) -> Optional[str]:
        """Return the currently selected sprite name."""
        item = self.list_widget.currentItem()
        return item.text() if item else None

    def _build_icon(self, pixmap: Optional[QPixmap]) -> Optional[QIcon]:
        """Render a thumbnail-style icon for a sprite preview."""
        thumbnail = self._render_thumbnail(pixmap)
        return QIcon(thumbnail) if thumbnail else None

    def _build_placeholder_icon(self) -> Optional[QIcon]:
        """Return a placeholder icon used when a sprite preview is unavailable."""
        return self._build_icon(None)

    def _render_thumbnail(self, pixmap: Optional[QPixmap]) -> Optional[QPixmap]:
        """Paint a sprite thumbnail similar to layer previews."""
        if not self._thumbnail_size.isValid():
            return None
        thumb = QPixmap(self._thumbnail_size)
        thumb.fill(Qt.GlobalColor.transparent)
        painter = QPainter(thumb)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg_rect = thumb.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QColor(0, 0, 0, 40))
        painter.setBrush(QColor(0, 0, 0, 20))
        painter.drawRoundedRect(bg_rect, 4, 4)
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                self._thumbnail_size.width() - 8,
                self._thumbnail_size.height() - 8,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self._thumbnail_size.width() - scaled.width()) // 2
            y = (self._thumbnail_size.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.end()
        return thumb

    def accept(self):
        """Ensure a selection exists before accepting."""
        if self.list_widget.currentItem() is None and self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        if self.list_widget.currentItem() is None:
            return
        self._current_selection = self.list_widget.currentItem().text()
        super().accept()
