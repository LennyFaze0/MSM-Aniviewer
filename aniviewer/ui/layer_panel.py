"""
Layer Panel
Displays layer visibility controls and multi-selection management
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QScrollArea, QCheckBox, QPushButton, QFrame, QGroupBox,
    QToolButton, QSizePolicy, QLineEdit, QMenu, QPlainTextEdit,
    QColorDialog, QSpinBox, QApplication, QButtonGroup
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QPoint, QMimeData
from PyQt6.QtGui import QIcon, QAction, QColor, QDrag, QPixmap

from core.data_structures import LayerData


LAYER_DRAG_MIME = "application/x-msm-layer-id"


class LayerListWidget(QWidget):
    """Container widget that forwards drag/drop events back to the layer panel."""

    def __init__(self, panel: "LayerPanel"):
        super().__init__()
        self._panel = panel
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if not self._panel._handle_drag_enter(event):
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if not self._panel._handle_drag_move(event):
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not self._panel._handle_drop(event):
            super().dropEvent(event)


class LayerPanel(QWidget):
    """Panel for controlling layer visibility and selection."""

    layer_visibility_changed = pyqtSignal(LayerData, int)
    layer_selection_changed = pyqtSignal(list, int, bool)
    selection_lock_toggled = pyqtSignal(bool)
    all_layers_deselected = pyqtSignal()
    layer_constraints_toggled = pyqtSignal(int, bool)
    color_changed = pyqtSignal(int, int, int, int)
    color_reset_requested = pyqtSignal()
    color_keyframe_requested = pyqtSignal(int, int, int, int)
    layer_order_changed = pyqtSignal(list)
    reset_layer_order_requested = pyqtSignal()
    reset_layer_visibility_requested = pyqtSignal()
    layer_order_changed = pyqtSignal(list)
    sprite_assign_requested = pyqtSignal(int)
    export_layer_readouts_requested = pyqtSignal()
    attachment_selection_changed = pyqtSignal(int)
    attachment_visibility_changed = pyqtSignal(int, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layer_rows: Dict[int, LayerRow] = {}
        self._layer_status: Dict[int, Tuple[str, str]] = {}
        self.selected_layers: Set[int] = set()
        self._lock_enabled: bool = False
        self._last_selected_layer_id: Optional[int] = None
        self._selection_anchor_id: Optional[int] = None
        self._all_layers: List[LayerData] = []
        self._filter_text: str = ""
        self._updating_color_controls: bool = False
        self._default_hidden_layers: Set[int] = set()
        self._show_placeholder_layers: bool = True
        self._thumbnail_size = QSize(26, 26)
        self._compact_ui = False
        self._variant_layers: Set[int] = set()
        self._readout_fields: Dict[str, QLabel] = {}
        self._readout_text: Optional[QPlainTextEdit] = None
        self._readout_last_text: str = ""
        self._attachment_rows: Dict[int, Tuple[QWidget, QToolButton, QCheckBox]] = {}
        self._attachment_selected_id: Optional[int] = None
        self._attachment_button_group = QButtonGroup(self)
        self._attachment_button_group.setExclusive(True)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Header section
        header_group = QGroupBox("Layers")
        header_layout = QVBoxLayout(header_group)
        header_layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        filter_row.addWidget(QLabel("Filter:"))

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter layers...")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.filter_input, 1)
        header_layout.addLayout(filter_row)

        self.count_label = QLabel("No layers")
        self.count_label.setStyleSheet("color: gray; font-size: 8pt;")
        header_layout.addWidget(self.count_label)
        layout.addWidget(header_group)

        # Quick actions toolbar
        toolbar_group = QGroupBox("Visibility")
        toolbar_layout = QGridLayout(toolbar_group)
        toolbar_layout.setContentsMargins(8, 6, 8, 6)
        toolbar_layout.setHorizontalSpacing(6)
        toolbar_layout.setVerticalSpacing(6)

        def _add_toolbar_button(widget: QPushButton, row: int, column: int):
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            toolbar_layout.addWidget(widget, row, column)

        # Show All button
        self.show_all_btn = QPushButton("Show All")
        self.show_all_btn.setToolTip("Make all layers visible")
        self.show_all_btn.clicked.connect(self._on_show_all)
        _add_toolbar_button(self.show_all_btn, 0, 0)

        # Hide All button
        self.hide_all_btn = QPushButton("Hide All")
        self.hide_all_btn.setToolTip("Hide all layers")
        self.hide_all_btn.clicked.connect(self._on_hide_all)
        _add_toolbar_button(self.hide_all_btn, 0, 1)

        # Invert button
        self.invert_btn = QPushButton("Invert")
        self.invert_btn.setToolTip("Invert layer visibility")
        self.invert_btn.clicked.connect(self._on_invert_visibility)
        _add_toolbar_button(self.invert_btn, 0, 2)

        # Toggle placeholder layers
        self.show_unused_btn = QPushButton("Show Unused")
        self.show_unused_btn.setCheckable(True)
        self.show_unused_btn.setChecked(True)
        self.show_unused_btn.setToolTip("Toggle placeholder/unused layers")
        self.show_unused_btn.toggled.connect(self._on_unused_layers_toggled)
        _add_toolbar_button(self.show_unused_btn, 1, 0)

        # Reset order button
        self.reset_order_btn = QPushButton("Reset Order")
        self.reset_order_btn.setToolTip("Restore the original layer order")
        self.reset_order_btn.clicked.connect(lambda: self.reset_layer_order_requested.emit())
        _add_toolbar_button(self.reset_order_btn, 1, 1)

        # Reset visibility button
        self.reset_visibility_btn = QPushButton("Reset Visibility")
        self.reset_visibility_btn.setToolTip("Restore default layer visibility")
        self.reset_visibility_btn.clicked.connect(lambda: self.reset_layer_visibility_requested.emit())
        _add_toolbar_button(self.reset_visibility_btn, 1, 2)

        layout.addWidget(toolbar_group)

        # Layer list scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layer_widget = LayerListWidget(self)
        self.layer_widget.setStyleSheet("background-color: transparent;")
        self.layer_layout = QVBoxLayout(self.layer_widget)
        self.layer_layout.setContentsMargins(4, 4, 4, 4)
        self.layer_layout.setSpacing(2)
        self.layer_layout.addStretch()

        scroll.setWidget(self.layer_widget)
        layout.addWidget(scroll, stretch=1)

        # Attachment list
        self.attach_group = QGroupBox("Attachments")
        self.attach_group.setVisible(False)
        attach_layout = QVBoxLayout(self.attach_group)
        attach_layout.setContentsMargins(8, 6, 8, 6)
        attach_layout.setSpacing(4)
        self.attachment_layout = attach_layout
        self._attachment_button_group.idClicked.connect(self._on_attachment_clicked)
        layout.addWidget(self.attach_group)

        # Selection controls section
        selection_group = QGroupBox("Selection")
        selection_layout = QVBoxLayout(selection_group)
        selection_layout.setSpacing(6)

        # Selection info row
        self.selection_info = QLabel("No layers selected")
        self.selection_info.setStyleSheet("font-size: 9pt; color: #888;")
        selection_layout.addWidget(self.selection_info)

        # Selection buttons row
        selection_buttons = QHBoxLayout()
        selection_buttons.setSpacing(6)

        self.lock_button = QPushButton("Lock Selection")
        self.lock_button.setCheckable(True)
        self.lock_button.setEnabled(False)
        self.lock_button.toggled.connect(self._on_lock_toggled)
        self.lock_button.setToolTip("Lock selected layers to prevent accidental changes")
        selection_buttons.addWidget(self.lock_button)

        self.deselect_btn = QPushButton("Clear Selection")
        self.deselect_btn.clicked.connect(self._on_deselect_all)
        self.deselect_btn.setToolTip("Deselect all layers")
        selection_buttons.addWidget(self.deselect_btn)

        selection_layout.addLayout(selection_buttons)

        # Tint controls for the active selection
        tint_header = QLabel("Tint (Selected Layers)")
        tint_header.setStyleSheet("font-weight: bold;")
        selection_layout.addWidget(tint_header)

        tint_row = QHBoxLayout()
        tint_row.setSpacing(6)

        self.color_button = QPushButton("Pick")
        self.color_button.setFixedWidth(56)
        self.color_button.clicked.connect(self._on_pick_color)
        tint_row.addWidget(self.color_button)

        self.hex_input = QLineEdit()
        self.hex_input.setPlaceholderText("#RRGGBB or #RRGGBBAA")
        self.hex_input.setMaxLength(9)
        self.hex_input.editingFinished.connect(self._on_hex_changed)
        tint_row.addWidget(self.hex_input, stretch=1)

        self.reset_color_btn = QPushButton("Reset")
        self.reset_color_btn.clicked.connect(self._on_reset_color)
        tint_row.addWidget(self.reset_color_btn)

        self.keyframe_color_btn = QPushButton("Keyframe Tint")
        self.keyframe_color_btn.clicked.connect(self._on_keyframe_tint)
        self.keyframe_color_btn.setToolTip("Bake the current tint into selected keyframes.")
        tint_row.addWidget(self.keyframe_color_btn)

        selection_layout.addLayout(tint_row)

        channel_row = QHBoxLayout()
        channel_row.setSpacing(4)
        self.r_spin, self.g_spin, self.b_spin, self.a_spin = (
            self._make_channel_spinbox("R"),
            self._make_channel_spinbox("G"),
            self._make_channel_spinbox("B"),
            self._make_channel_spinbox("A"),
        )
        for label_text, spin in (("R", self.r_spin), ("G", self.g_spin),
                                 ("B", self.b_spin), ("A", self.a_spin)):
            label = QLabel(label_text)
            label.setStyleSheet("color: #666; font-size: 9pt;")
            channel_row.addWidget(label)
            channel_row.addWidget(spin)
        selection_layout.addLayout(channel_row)

        self._set_color_controls_enabled(False)
        self._apply_color_fields((255, 255, 255, 255), emit=False, allow_mixed=False)
        layout.addWidget(selection_group)

        # Layer readout section
        readout_group = QGroupBox("Layer Readout")
        readout_layout = QVBoxLayout(readout_group)
        readout_layout.setSpacing(6)

        readout_header_row = QHBoxLayout()
        readout_header_row.setSpacing(6)

        readout_hint = QLabel("Active layer details at the current frame.")
        readout_hint.setStyleSheet("color: gray; font-size: 8pt;")
        readout_header_row.addWidget(readout_hint)
        readout_header_row.addStretch()

        self.export_readout_btn = QPushButton("Export Readouts")
        self.export_readout_btn.setToolTip(
            "Export readouts for all visible layers at the current frame."
        )
        self.export_readout_btn.clicked.connect(self.export_layer_readouts_requested.emit)
        readout_header_row.addWidget(self.export_readout_btn)

        readout_layout.addLayout(readout_header_row)

        self._readout_text = QPlainTextEdit()
        self._readout_text.setReadOnly(True)
        self._readout_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._readout_text.setPlainText(
            "Layer: -\n"
            "Sprite: -\n"
            "Local Pos: -\n"
            "World Pos: -\n"
            "Anchor (World): -\n"
            "Rotation: -\n"
            "Scale: -\n"
            "Opacity: -\n"
            "User Offset: -"
        )
        self._readout_last_text = self._readout_text.toPlainText()
        readout_layout.addWidget(self._readout_text)
        layout.addWidget(readout_group)

    def set_compact_ui(self, enabled: bool):
        """Apply compact spacing and row sizing."""
        self._compact_ui = bool(enabled)
        root_margin = 6 if self._compact_ui else 8
        root_spacing = 6 if self._compact_ui else 8
        layout = self.layout()
        if isinstance(layout, QVBoxLayout):
            layout.setContentsMargins(root_margin, root_margin, root_margin, root_margin)
            layout.setSpacing(root_spacing)
        self.layer_layout.setContentsMargins(
            2 if self._compact_ui else 4,
            2 if self._compact_ui else 4,
            2 if self._compact_ui else 4,
            2 if self._compact_ui else 4,
        )
        self.layer_layout.setSpacing(1 if self._compact_ui else 2)
        group_spacing = 4 if self._compact_ui else 6
        group_margins = (6, 6, 6, 6) if self._compact_ui else (8, 6, 8, 6)
        for group in self.findChildren(QGroupBox):
            group_layout = group.layout()
            if group_layout:
                group_layout.setSpacing(group_spacing)
                group_layout.setContentsMargins(*group_margins)
        self._thumbnail_size = QSize(20, 20) if self._compact_ui else QSize(26, 26)
        for row in self.layer_rows.values():
            row.set_compact_ui(self._compact_ui, self.thumbnail_size())

    # ------------------------------------------------------------------ #
    # Drag and drop support
    # ------------------------------------------------------------------ #
    def _handle_drag_enter(self, event) -> bool:
        """Accept drags that carry a valid layer id."""
        layer_id = self._decode_drag_layer_id(event.mimeData())
        if layer_id is None or len(self._all_layers) < 2:
            event.ignore()
            return False
        event.acceptProposedAction()
        return True

    def _handle_drag_move(self, event) -> bool:
        """Continue accepting drag move events for valid payloads."""
        layer_id = self._decode_drag_layer_id(event.mimeData())
        if layer_id is None or len(self._all_layers) < 2:
            event.ignore()
            return False
        event.acceptProposedAction()
        return True

    def _handle_drop(self, event) -> bool:
        """Emit a reorder request when a layer is dropped onto the list."""
        layer_id = self._decode_drag_layer_id(event.mimeData())
        if layer_id is None or len(self._all_layers) < 2:
            event.ignore()
            return False
        drop_pos = event.position().toPoint()
        target_index = self._drop_index_from_pos(drop_pos)
        if target_index is None:
            event.ignore()
            return False
        if self._request_layer_reorder(layer_id, target_index):
            event.acceptProposedAction()
            return True
        event.ignore()
        return False

    def _decode_drag_layer_id(self, mime: QMimeData) -> Optional[int]:
        """Extract a layer id from mime data."""
        if not mime.hasFormat(LAYER_DRAG_MIME):
            return None
        try:
            payload = bytes(mime.data(LAYER_DRAG_MIME)).decode("utf-8").strip()
            return int(payload)
        except (ValueError, TypeError):
            return None

    def _ordered_layer_rows(self) -> List["LayerRow"]:
        """Return rows in their visual order, excluding hidden ones."""
        ordered: List[LayerRow] = []
        for index in range(self.layer_layout.count()):
            item = self.layer_layout.itemAt(index)
            if not item:
                continue
            widget = item.widget()
            if isinstance(widget, LayerRow) and widget.isVisible():
                ordered.append(widget)
        return ordered

    def _drop_index_from_pos(self, local_pos: QPoint) -> Optional[int]:
        """Return the insertion index for a drop coordinate."""
        rows = self._ordered_layer_rows()
        if not rows:
            return None
        order_lookup = {
            layer.layer_id: idx for idx, layer in enumerate(self._all_layers)
        }
        for idx, row in enumerate(rows):
            top = row.pos().y()
            midpoint = top + row.height() / 2
            if local_pos.y() < midpoint:
                return order_lookup.get(row.layer.layer_id, idx)
        return len(self._all_layers)

    def _request_layer_reorder(self, layer_id: int, drop_index: int) -> bool:
        """Build a new layer ordering and emit it if it differs."""
        current_order = [layer.layer_id for layer in self._all_layers]
        if layer_id not in current_order:
            return False
        source_index = current_order.index(layer_id)
        drop_index = max(0, min(drop_index, len(current_order)))
        new_order = current_order[:]
        moving = new_order.pop(source_index)
        if drop_index > source_index:
            drop_index -= 1
        new_order.insert(drop_index, moving)
        if new_order == current_order:
            return False
        layer_lookup = {layer.layer_id: layer for layer in self._all_layers}
        self._all_layers = [layer_lookup[idx] for idx in new_order if idx in layer_lookup]
        self.layer_order_changed.emit(new_order)
        return True

    def _on_filter_changed(self, text: str):
        """Filter layers by name."""
        self._filter_text = text.lower().strip()
        self._apply_filter()

    def _apply_filter(self):
        """Show/hide layer rows based on filter text."""
        effective_rows: List[LayerRow] = []
        for row in self.layer_rows.values():
            if not self._show_placeholder_layers and row.is_placeholder:
                row.setVisible(False)
                continue
            effective_rows.append(row)

        visible_count = 0
        for row in effective_rows:
            if not self._filter_text:
                row.setVisible(True)
                visible_count += 1
            else:
                matches = self._filter_text in row.layer.name.lower()
                row.setVisible(matches)
                if matches:
                    visible_count += 1

        total = len(effective_rows)
        if total == 0:
            self.count_label.setText("No layers")
        elif self._filter_text and visible_count != total:
            self.count_label.setText(f"Showing {visible_count} of {total} layers")
        else:
            self.count_label.setText(f"Showing {total} layers")

    def _on_show_all(self):
        """Make all layers visible."""
        for row in self.layer_rows.values():
            if not row.layer.visible:
                row.checkbox.setChecked(True)

    def _on_hide_all(self):
        """Hide all layers."""
        for row in self.layer_rows.values():
            if row.layer.visible:
                row.checkbox.setChecked(False)

    def _on_invert_visibility(self):
        """Invert visibility of all layers."""
        for row in self.layer_rows.values():
            row.checkbox.setChecked(not row.checkbox.isChecked())

    def _on_unused_layers_toggled(self, checked: bool):
        """Show or hide placeholder layers."""
        self._show_placeholder_layers = checked
        self._apply_filter()

    def set_default_hidden_layers(self, layer_ids: Optional[Set[int]]):
        """Define which layers were hidden by default when the animation loaded."""
        self._default_hidden_layers = set(layer_ids or set())
        self._apply_placeholder_visibility()

    def set_layers_with_sprite_variants(self, layer_ids: Optional[Set[int]]):
        """Mark which layers support sprite swapping."""
        self._variant_layers = set(layer_ids or set())
        for layer_id, row in self.layer_rows.items():
            row.set_sprite_variant_enabled(layer_id in self._variant_layers)

    def _apply_placeholder_visibility(self):
        """Refresh placeholder flags for each row prior to filtering."""
        for row in self.layer_rows.values():
            row.is_placeholder = row.layer.layer_id in self._default_hidden_layers
        self._apply_filter()

    def update_layers(self, layers: List[LayerData]):
        """Rebuild the layer list."""
        # Clear existing rows
        while self.layer_layout.count() > 1:
            item = self.layer_layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()

        self.layer_rows.clear()
        self._all_layers = layers

        for layer in layers:
            row = LayerRow(layer)
            row.visibility_toggled.connect(self.layer_visibility_changed.emit)
            row.select_toggled.connect(self._on_layer_toggle)
            row.sprite_assign_requested.connect(self._on_row_sprite_assign)
            row.constraint_toggled.connect(self._on_constraint_toggle)
            row.is_placeholder = layer.layer_id in self._default_hidden_layers
            row.set_sprite_variant_enabled(layer.layer_id in self._variant_layers)
            row.set_compact_ui(self._compact_ui, self.thumbnail_size())
            self.layer_rows[layer.layer_id] = row
            self.layer_layout.insertWidget(self.layer_layout.count() - 1, row)
            row.set_thumbnail(None, self.thumbnail_size())
            if layer.layer_id in self._layer_status:
                status, severity = self._layer_status[layer.layer_id]
                row.set_status(status, severity)

        # Update count
        self.count_label.setText(str(len(layers)))
        
        # Apply placeholder logic and filter
        self._apply_placeholder_visibility()
        self._refresh_row_styles()
        self._update_selection_info()

    def _clear_attachment_rows(self):
        """Remove all attachment rows from the panel."""
        while self.attachment_layout.count():
            item = self.attachment_layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        for container, button, checkbox in self._attachment_rows.values():
            self._attachment_button_group.removeButton(button)
            button.deleteLater()
            checkbox.deleteLater()
            container.deleteLater()
        self._attachment_rows.clear()
        self._attachment_selected_id = None

    def update_attachments(self, entries: List[Tuple[int, str, bool, str]]):
        """Rebuild the attachment list."""
        self._clear_attachment_rows()
        if not entries:
            self.attach_group.setVisible(False)
            return
        self.attach_group.setVisible(True)
        for attachment_id, name, visible, target in entries:
            row = QHBoxLayout()
            row.setSpacing(6)
            checkbox = QCheckBox()
            checkbox.setChecked(bool(visible))
            checkbox.toggled.connect(
                lambda checked, aid=attachment_id: self.attachment_visibility_changed.emit(aid, checked)
            )
            row.addWidget(checkbox)
            label = name or "attachment"
            if target:
                label = f"{label} ({target})"
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            self._attachment_button_group.addButton(button, attachment_id)
            row.addWidget(button, 1)
            container = QWidget()
            container.setLayout(row)
            self.attachment_layout.addWidget(container)
            self._attachment_rows[attachment_id] = (container, button, checkbox)

    def _on_attachment_clicked(self, attachment_id: int):
        """Handle attachment selection changes."""
        self._attachment_selected_id = attachment_id
        self.attachment_selection_changed.emit(attachment_id)

    def set_attachment_selection(self, attachment_id: Optional[int]):
        """Update attachment selection without emitting signals."""
        self._attachment_selected_id = attachment_id
        for aid, (_, button, _) in self._attachment_rows.items():
            button.blockSignals(True)
            button.setChecked(aid == attachment_id)
            button.blockSignals(False)

    def update_layer_readout(self, info: Optional[Dict[str, str]]):
        """Update the selected-layer readout block."""
        if self._readout_text is None:
            return
        if not info:
            info = {}
        text = (
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
        if text == self._readout_last_text:
            return
        cursor = self._readout_text.textCursor()
        if self._readout_text.hasFocus() and cursor.hasSelection():
            return
        self._readout_text.setPlainText(text)
        self._readout_last_text = text

    def _on_row_sprite_assign(self, layer: LayerData):
        """Emit sprite assignment requests to the host window."""
        if layer and layer.layer_id is not None:
            self.sprite_assign_requested.emit(layer.layer_id)

    def _on_constraint_toggle(self, layer: LayerData, enabled: bool):
        if layer and layer.layer_id is not None:
            self.layer_constraints_toggled.emit(layer.layer_id, bool(enabled))

    def set_layer_constraint_enabled(self, layer_id: int, enabled: bool) -> None:
        row = self.layer_rows.get(layer_id)
        if not row:
            return
        row.set_constraint_enabled(enabled)

    def thumbnail_size(self) -> QSize:
        """Return the preferred thumbnail dimensions for layer rows."""
        return QSize(self._thumbnail_size)

    def set_layer_thumbnail(self, layer_id: int, pixmap: Optional[QPixmap]):
        """Assign a sprite thumbnail to a specific layer row."""
        if row := self.layer_rows.get(layer_id):
            row.set_thumbnail(pixmap, self.thumbnail_size())

    def clear_layer_thumbnails(self):
        """Remove thumbnails from every layer row."""
        size = self.thumbnail_size()
        for row in self.layer_rows.values():
            row.set_thumbnail(None, size)

    def set_selection_state(self, layer_ids: Set[int]):
        """Apply an external selection state (e.g., after loading animation)."""
        self.selected_layers = set(layer_ids)
        self._last_selected_layer_id = next(iter(self.selected_layers), None)
        if self._selection_anchor_id not in self.selected_layers:
            self._selection_anchor_id = self._last_selected_layer_id
        if not self.selected_layers:
            self.lock_button.blockSignals(True)
            self.lock_button.setChecked(False)
            self.lock_button.blockSignals(False)
            self.lock_button.setEnabled(False)
            self._lock_enabled = False
        else:
            self.lock_button.setEnabled(True)
        self._refresh_row_styles()
        self._update_selection_info()

    def highlight_selected_layer(self, layer_id: Optional[int]):
        """
        Backwards-compatibility helper for older code paths expecting single selection.
        """
        if layer_id is None:
            self.set_selection_state(set())
        else:
            self.set_selection_state({layer_id})

    def _on_layer_toggle(self, layer: LayerData, shift_pressed: bool = False, ctrl_pressed: bool = False):
        layer_id = layer.layer_id
        if layer_id is None:
            return

        ordered_rows = self._ordered_layer_rows()
        index_map = {row.layer.layer_id: idx for idx, row in enumerate(ordered_rows)}
        clicked_idx = index_map.get(layer_id)
        anchor_id = self._selection_anchor_id if self._selection_anchor_id in index_map else None

        if shift_pressed and clicked_idx is not None:
            if anchor_id is None:
                anchor_id = layer_id
                self._selection_anchor_id = layer_id
            anchor_idx = index_map.get(anchor_id, clicked_idx)
            start = min(anchor_idx, clicked_idx)
            end = max(anchor_idx, clicked_idx)
            range_ids = {ordered_rows[i].layer.layer_id for i in range(start, end + 1)}
            if ctrl_pressed:
                self.selected_layers.update(range_ids)
            else:
                self.selected_layers = set(range_ids)
            added = True
        else:
            if ctrl_pressed:
                if layer_id in self.selected_layers:
                    self.selected_layers.remove(layer_id)
                    added = False
                else:
                    self.selected_layers.add(layer_id)
                    added = True
                self._selection_anchor_id = layer_id
            else:
                self.selected_layers = {layer_id}
                added = True
                self._selection_anchor_id = layer_id

        self._last_selected_layer_id = layer_id

        if not self.selected_layers:
            self.lock_button.setChecked(False)
            self.lock_button.setEnabled(False)
            self._lock_enabled = False
        else:
            self.lock_button.setEnabled(True)

        self._refresh_row_styles()
        self._update_selection_info()
        self.layer_selection_changed.emit(
            list(self.selected_layers),
            layer_id,
            added
        )

    def _on_deselect_all(self):
        if not self.selected_layers and not self._lock_enabled:
            return
        self.selected_layers.clear()
        self._last_selected_layer_id = None
        self._selection_anchor_id = None
        self.lock_button.setChecked(False)
        self.lock_button.setEnabled(False)
        self._lock_enabled = False
        self._refresh_row_styles()
        self._update_selection_info()
        self.all_layers_deselected.emit()
        self.layer_selection_changed.emit([], -1, False)

    def _on_lock_toggled(self, locked: bool):
        if locked and not self.selected_layers:
            self.lock_button.blockSignals(True)
            self.lock_button.setChecked(False)
            self.lock_button.blockSignals(False)
            return
        self._lock_enabled = locked
        self._refresh_row_styles()
        self._update_selection_info()
        self.selection_lock_toggled.emit(locked)

    def _refresh_row_styles(self):
        has_selection = bool(self.selected_layers)
        for layer_id, row in self.layer_rows.items():
            is_selected = layer_id in self.selected_layers
            # Show red for unselected layers when there's any selection
            show_as_unselected = has_selection and not is_selected
            row.update_state(is_selected, show_as_unselected)
            if layer_id not in self._layer_status:
                row.set_status("", "")

    def _update_selection_info(self):
        """Update the selection info label."""
        count = len(self.selected_layers)
        if count == 0:
            self.selection_info.setText("No layers selected")
            self.selection_info.setStyleSheet("font-size: 9pt; color: #888;")
        elif count == 1:
            # Find the layer name
            layer_id = next(iter(self.selected_layers))
            if row := self.layer_rows.get(layer_id):
                name = row.layer.name
                if len(name) > 25:
                    name = name[:22] + "..."
                self.selection_info.setText(f"Selected: {name}")
            else:
                self.selection_info.setText("1 layer selected")
            self.selection_info.setStyleSheet("font-size: 9pt; color: #0078d4;")
        else:
            self.selection_info.setText(f"{count} layers selected")
            self.selection_info.setStyleSheet("font-size: 9pt; color: #0078d4;")
        
        # Update lock button text
        if self._lock_enabled:
            self.lock_button.setText("Unlock Selection")
        else:
            self.lock_button.setText("Lock Selection")
        self.refresh_color_editor()

    @property
    def lock_enabled(self) -> bool:
        return self._lock_enabled

    def refresh_color_editor(self):
        """Update the tint widgets based on the current selection."""
        has_selection = bool(self.selected_layers)
        self._set_color_controls_enabled(has_selection)
        if not has_selection:
            self._apply_color_fields((255, 255, 255, 255), emit=False, allow_mixed=False)
            return

        colors: List[Tuple[int, int, int, int]] = []
        for layer_id in self.selected_layers:
            row = self.layer_rows.get(layer_id)
            if not row:
                continue
            tint = getattr(row.layer, "color_tint", None)
            colors.append(self._rgba_from_tint(tint))

        if not colors:
            self._apply_color_fields((255, 255, 255, 255), emit=False, allow_mixed=False)
            return

        first = colors[0]
        uniform = all(c == first for c in colors[1:])
        self._apply_color_fields(first, emit=False, allow_mixed=not uniform)

    # ------------------------------------------------------------------ #
    # Color helpers
    # ------------------------------------------------------------------ #
    def _make_channel_spinbox(self, name: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 255)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedWidth(60)
        spin.valueChanged.connect(self._on_spin_color_changed)
        spin.setToolTip(f"{name} channel (0-255)")
        return spin

    def _set_color_controls_enabled(self, enabled: bool):
        for widget in (
            self.color_button,
            self.hex_input,
            self.reset_color_btn,
            self.keyframe_color_btn,
            self.r_spin,
            self.g_spin,
            self.b_spin,
            self.a_spin,
        ):
            widget.setEnabled(enabled)

    def _rgba_from_tint(self, tint: Optional[Tuple[float, float, float, float]]) -> Tuple[int, int, int, int]:
        if not tint:
            return (255, 255, 255, 255)
        r, g, b, a = tint
        return (
            int(max(0, min(255, round(r * 255)))),
            int(max(0, min(255, round(g * 255)))),
            int(max(0, min(255, round(b * 255)))),
            int(max(0, min(255, round(a * 255)))),
        )

    def _apply_color_fields(
        self,
        rgba: Tuple[int, int, int, int],
        *,
        emit: bool,
        allow_mixed: bool
    ):
        """Update widgets to a color and optionally emit change."""
        r, g, b, a = rgba
        self._updating_color_controls = True
        for spin, value in ((self.r_spin, r), (self.g_spin, g), (self.b_spin, b), (self.a_spin, a)):
            spin.setValue(value)
        if allow_mixed:
            self.hex_input.setText("")
            self.hex_input.setPlaceholderText("Mixed values")
        else:
            self.hex_input.setPlaceholderText("#RRGGBB or #RRGGBBAA")
            self.hex_input.setText(self._format_hex(rgba))
        self._set_preview_color(rgba)
        self._updating_color_controls = False
        if emit:
            self.color_changed.emit(r, g, b, a)

    def _set_preview_color(self, rgba: Tuple[int, int, int, int]):
        r, g, b, a = rgba
        self.color_button.setStyleSheet(
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

    def _format_hex(self, rgba: Tuple[int, int, int, int]) -> str:
        r, g, b, a = rgba
        if a == 255:
            return f"#{r:02X}{g:02X}{b:02X}"
        return f"#{r:02X}{g:02X}{b:02X}{a:02X}"

    def _current_rgba(self) -> Tuple[int, int, int, int]:
        return (
            int(self.r_spin.value()),
            int(self.g_spin.value()),
            int(self.b_spin.value()),
            int(self.a_spin.value())
        )

    def _on_spin_color_changed(self, _value: int):
        if self._updating_color_controls:
            return
        rgba = self._current_rgba()
        self._apply_color_fields(rgba, emit=True, allow_mixed=False)

    def _on_hex_changed(self):
        if self._updating_color_controls:
            return
        raw = self.hex_input.text().strip().lstrip("#")
        if len(raw) not in (6, 8):
            # Revert to current fields if invalid
            self._apply_color_fields(self._current_rgba(), emit=False, allow_mixed=False)
            return
        try:
            r = int(raw[0:2], 16)
            g = int(raw[2:4], 16)
            b = int(raw[4:6], 16)
            a = int(raw[6:8], 16) if len(raw) == 8 else 255
        except ValueError:
            self._apply_color_fields(self._current_rgba(), emit=False, allow_mixed=False)
            return
        self._apply_color_fields((r, g, b, a), emit=True, allow_mixed=False)

    def _on_pick_color(self):
        initial = QColor(*self._current_rgba())
        color = QColorDialog.getColor(
            initial,
            self,
            "Select Layer Tint",
            QColorDialog.ColorDialogOption.ShowAlphaChannel
        )
        if color.isValid():
            self._apply_color_fields(
                (color.red(), color.green(), color.blue(), color.alpha()),
                emit=True,
                allow_mixed=False
            )

    def _on_reset_color(self):
        if self._updating_color_controls:
            return
        self.color_reset_requested.emit()
        self._apply_color_fields((255, 255, 255, 255), emit=False, allow_mixed=False)

    def _on_keyframe_tint(self):
        """Emit the current RGBA tint so it can be baked into keyframes."""
        if self._updating_color_controls:
            return
        r, g, b, a = self._current_rgba()
        self.color_keyframe_requested.emit(r, g, b, a)

    def update_layer_status(self, layer_id: int, message: str, severity: str):
        """Update or clear a layer-level diagnostic message."""
        if not message:
            self._layer_status.pop(layer_id, None)
        else:
            self._layer_status[layer_id] = (message, severity)
        if row := self.layer_rows.get(layer_id):
            row.set_status(message, severity)

    def clear_layer_statuses(self):
        """Remove all diagnostic overlays."""
        self._layer_status.clear()
        for row in self.layer_rows.values():
            row.set_status("", "")


class LayerRow(QFrame):
    """Single row showing a layer checkbox and select toggle."""

    visibility_toggled = pyqtSignal(LayerData, int)
    select_toggled = pyqtSignal(LayerData, bool, bool)
    sprite_assign_requested = pyqtSignal(LayerData)
    constraint_toggled = pyqtSignal(LayerData, bool)

    def __init__(self, layer: LayerData, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.layer = layer
        self._selected = False
        self._show_as_unselected = False
        self._drag_start_pos: Optional[QPoint] = None
        self._drag_enabled = False
        self._drag_in_progress = False
        self._pending_selection_toggle = False
        self._pending_shift_select = False
        self._pending_ctrl_select = False
        self.is_placeholder = False
        self._thumbnail_pixmap: Optional[QPixmap] = None

        self.setMinimumHeight(28)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)
        self._row_layout = layout

        # Visibility checkbox (just the checkbox indicator, no text)
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(layer.visible)
        self.checkbox.stateChanged.connect(self._on_visibility)
        self.checkbox.setToolTip("Toggle visibility")
        self.checkbox.setFixedWidth(20)
        layout.addWidget(self.checkbox)

        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(24, 24)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setStyleSheet(
            "QLabel { background-color: rgba(0, 0, 0, 0.05); border-radius: 3px; }"
        )
        layout.addWidget(self.thumbnail_label)

        # Layer name label (clickable for selection)
        self.name_label = QLabel(layer.name)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.name_label.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.name_label, stretch=1)

        # Sprite variant button
        self.sprite_button = QToolButton()
        self.sprite_button.setText("Sprite")
        self.sprite_button.setToolTip("Assign sprites for this layer's keyframes")
        self.sprite_button.setAutoRaise(True)
        self.sprite_button.setVisible(False)
        self.sprite_button.setFixedWidth(52)
        self.sprite_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.sprite_button.clicked.connect(self._on_sprite_button_clicked)
        layout.addWidget(self.sprite_button)

        # Constraints toggle
        self.constraint_toggle = QToolButton()
        self.constraint_toggle.setText("C")
        self.constraint_toggle.setToolTip("Enable constraints for this layer")
        self.constraint_toggle.setCheckable(True)
        self.constraint_toggle.setChecked(True)
        self.constraint_toggle.setAutoRaise(True)
        self.constraint_toggle.setFixedWidth(22)
        self.constraint_toggle.toggled.connect(self._on_constraint_toggle)
        layout.addWidget(self.constraint_toggle)

        # Status indicator (for diagnostics)
        self.status_indicator = QLabel()
        self.status_indicator.setFixedSize(8, 8)
        self.status_indicator.setStyleSheet("background: transparent; border-radius: 4px;")
        self.status_indicator.setVisible(False)
        self.status_indicator.setToolTip("")
        layout.addWidget(self.status_indicator)

        # Apply base style
        self._apply_style()

    def set_compact_ui(self, enabled: bool, thumbnail_size: QSize):
        if enabled:
            self.setMinimumHeight(22)
            self._row_layout.setContentsMargins(4, 1, 4, 1)
            self._row_layout.setSpacing(6)
            self.checkbox.setFixedWidth(18)
            self.sprite_button.setFixedWidth(46)
            self.constraint_toggle.setFixedWidth(20)
            self.name_label.setStyleSheet("font-size: 9pt;")
        else:
            self.setMinimumHeight(28)
            self._row_layout.setContentsMargins(6, 2, 6, 2)
            self._row_layout.setSpacing(8)
            self.checkbox.setFixedWidth(20)
            self.sprite_button.setFixedWidth(52)
            self.constraint_toggle.setFixedWidth(22)
            self.name_label.setStyleSheet("")
        self.thumbnail_label.setFixedSize(thumbnail_size)
        if self._thumbnail_pixmap is not None:
            self.set_thumbnail(self._thumbnail_pixmap, thumbnail_size)

    def mousePressEvent(self, event):
        """Handle click on the row to toggle selection."""
        if event.button() == Qt.MouseButton.LeftButton:
            control_widgets = [self.checkbox, self.constraint_toggle]
            if self.sprite_button.isVisible():
                control_widgets.append(self.sprite_button)
            hit_control = False
            for widget in control_widgets:
                widget_global = widget.mapToParent(widget.rect().topLeft())
                widget_rect = widget.rect().translated(widget_global)
                if widget_rect.contains(event.pos()):
                    hit_control = True
                    break

            if not hit_control:
                self._pending_selection_toggle = True
                self._pending_shift_select = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._pending_ctrl_select = bool(
                    event.modifiers()
                    & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
                )
                self._drag_enabled = True
                self._drag_in_progress = False
                self._drag_start_pos = event.position().toPoint()
                event.accept()
                return
            self._pending_selection_toggle = False
            self._pending_shift_select = False
            self._pending_ctrl_select = False
            self._drag_enabled = False
            self._drag_in_progress = False
            self._drag_start_pos = None
        else:
            self._pending_selection_toggle = False
            self._pending_shift_select = False
            self._pending_ctrl_select = False
            self._drag_enabled = False
            self._drag_in_progress = False
            self._drag_start_pos = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_enabled
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
        ):
            current_pos = event.position().toPoint()
            distance = (current_pos - self._drag_start_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._start_drag(self._drag_start_pos)
                self._drag_enabled = False
                self._drag_in_progress = True
                self._pending_selection_toggle = False
                self._pending_shift_select = False
                self._pending_ctrl_select = False
                self._drag_start_pos = None
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._pending_selection_toggle
            and not self._drag_in_progress
        ):
            self.select_toggled.emit(self.layer, self._pending_shift_select, self._pending_ctrl_select)
            event.accept()
        self._pending_selection_toggle = False
        self._pending_shift_select = False
        self._pending_ctrl_select = False
        self._drag_enabled = False
        self._drag_in_progress = False
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _start_drag(self, hot_spot: QPoint):
        """Start a drag operation containing this layer id."""
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(LAYER_DRAG_MIME, str(self.layer.layer_id).encode("utf-8"))
        drag.setMimeData(mime)
        try:
            pixmap = self.grab()
            drag.setPixmap(pixmap)
            drag.setHotSpot(hot_spot)
        except Exception:
            pass
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_in_progress = False

    def _on_visibility(self, state: int):
        self.visibility_toggled.emit(self.layer, state)
        self._update_visibility_style()

    def _on_sprite_button_clicked(self):
        self.sprite_assign_requested.emit(self.layer)

    def _on_constraint_toggle(self, checked: bool):
        self.constraint_toggled.emit(self.layer, checked)

    def _update_visibility_style(self):
        """Update label style based on visibility."""
        if self.checkbox.isChecked():
            self.name_label.setStyleSheet("")
        else:
            self.name_label.setStyleSheet("QLabel { color: #888; }")

    def update_state(self, selected: bool, show_as_unselected: bool):
        """Update the visual state of the row.
        
        Args:
            selected: Whether this layer is selected (green)
            show_as_unselected: Whether to show as unselected when there's a selection (red)
        """
        self._selected = selected
        self._show_as_unselected = show_as_unselected
        self._apply_style()

    def set_sprite_variant_enabled(self, enabled: bool):
        """Show or hide the sprite assignment button."""
        self.sprite_button.setVisible(enabled)
        self.sprite_button.setEnabled(enabled)

    def set_constraint_enabled(self, enabled: bool):
        """Update the constraint toggle without emitting."""
        self.constraint_toggle.blockSignals(True)
        self.constraint_toggle.setChecked(enabled)
        self.constraint_toggle.blockSignals(False)

    def _apply_style(self):
        """Apply visual style based on selection state."""
        if self._selected:
            # Selected layers are GREEN
            self.setStyleSheet("""
                LayerRow {
                    background-color: rgba(50, 205, 50, 0.25);
                    border: 2px solid rgba(50, 205, 50, 0.8);
                    border-radius: 4px;
                }
                LayerRow:hover {
                    background-color: rgba(50, 205, 50, 0.35);
                }
            """)
        elif self._show_as_unselected:
            # Unselected layers when there's a selection are RED
            self.setStyleSheet("""
                LayerRow {
                    background-color: rgba(220, 60, 60, 0.15);
                    border: 1px solid rgba(220, 60, 60, 0.5);
                    border-radius: 4px;
                }
                LayerRow:hover {
                    background-color: rgba(220, 60, 60, 0.25);
                }
            """)
        else:
            # Normal state - no selection active
            self.setStyleSheet("""
                LayerRow {
                    background-color: transparent;
                    border: 1px solid transparent;
                    border-radius: 4px;
                }
                LayerRow:hover {
                    background-color: rgba(0, 0, 0, 0.05);
                    border-color: rgba(0, 0, 0, 0.1);
                }
            """)
        
        self._update_visibility_style()

    def set_status(self, message: str, severity: str):
        """Set or clear the diagnostic status indicator."""
        if not message:
            self.status_indicator.setVisible(False)
            self.status_indicator.setToolTip("")
            return
        
        color = {
            "ERROR": "#c62828",
            "WARNING": "#f57c00",
            "SUCCESS": "#2e7d32",
            "INFO": "#1976d2",
            "DEBUG": "#888888",
        }.get(severity, "#888888")
        
        self.status_indicator.setStyleSheet(f"background: {color}; border-radius: 4px;")
        self.status_indicator.setToolTip(f"{severity}: {message}")
        self.status_indicator.setVisible(True)

    def set_thumbnail(self, pixmap: Optional[QPixmap], target_size: Optional[QSize] = None):
        """Display a scaled sprite preview inside the row."""
        self._thumbnail_pixmap = pixmap
        if target_size is not None:
            width = max(16, int(target_size.width()))
            height = max(16, int(target_size.height()))
            self.thumbnail_label.setFixedSize(width, height)
        placeholder_style = "QLabel { background-color: rgba(0, 0, 0, 0.05); border-radius: 3px; }"
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                self.thumbnail_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.thumbnail_label.setPixmap(scaled)
            self.thumbnail_label.setStyleSheet("QLabel { background-color: transparent; }")
        else:
            self.thumbnail_label.setPixmap(QPixmap())
            self.thumbnail_label.setStyleSheet(placeholder_style)
