"""
Constraint editor dialog.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QPushButton,
    QDoubleSpinBox,
    QCheckBox,
    QWidget,
    QStackedWidget,
)
from PyQt6.QtCore import Qt

from core.constraints import ConstraintSpec, ConstraintManager


class ConstraintEditorDialog(QDialog):
    """Simple dialog for creating or editing a constraint."""

    def __init__(
        self,
        layer_entries: List[Tuple[str, int]],
        anchor_positions: Optional[Dict[int, Tuple[float, float]]] = None,
        constraint: Optional[ConstraintSpec] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Constraint Editor")
        self._layer_entries = layer_entries
        self._anchor_positions = anchor_positions or {}
        self._constraint = constraint
        self._type_keys = [
            "rotation_clamp",
            "scale_clamp",
            "position_clamp",
            "axis_lock",
            "distance",
        ]
        self._build_ui()
        if constraint:
            self._load_constraint(constraint)
        else:
            self._set_defaults()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("Optional label (e.g., Wrist Clamp)")
        form.addRow("Label:", self.label_input)

        self.type_combo = QComboBox()
        for key in self._type_keys:
            self.type_combo.addItem(ConstraintManager.TYPE_LABELS.get(key, key), key)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow("Type:", self.type_combo)

        self.layer_combo = QComboBox()
        for name, layer_id in self._layer_entries:
            self.layer_combo.addItem(name, layer_id)
        form.addRow("Layer:", self.layer_combo)

        layout.addLayout(form)

        self.param_stack = QStackedWidget()
        self._param_widgets = {}
        self._build_param_widgets()
        layout.addWidget(self.param_stack)

        button_row = QHBoxLayout()
        button_row.addStretch()
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(ok_btn)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

    def _build_param_widgets(self):
        self._param_widgets["rotation_clamp"] = self._build_rotation_params()
        self._param_widgets["scale_clamp"] = self._build_scale_params()
        self._param_widgets["position_clamp"] = self._build_position_params()
        self._param_widgets["axis_lock"] = self._build_axis_params()
        self._param_widgets["distance"] = self._build_distance_params()

        for key in self._type_keys:
            self.param_stack.addWidget(self._param_widgets[key])

    def _build_rotation_params(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.rot_min_spin = self._make_angle_spin()
        self.rot_max_spin = self._make_angle_spin()
        form.addRow("Min (deg):", self.rot_min_spin)
        form.addRow("Max (deg):", self.rot_max_spin)
        return widget

    def _build_scale_params(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.scale_min_x = self._make_scale_spin()
        self.scale_max_x = self._make_scale_spin()
        self.scale_min_y = self._make_scale_spin()
        self.scale_max_y = self._make_scale_spin()
        self.scale_uniform = QCheckBox("Uniform")
        form.addRow("Min X:", self.scale_min_x)
        form.addRow("Max X:", self.scale_max_x)
        form.addRow("Min Y:", self.scale_min_y)
        form.addRow("Max Y:", self.scale_max_y)
        form.addRow("", self.scale_uniform)
        return widget

    def _build_position_params(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.pos_min_x = self._make_pos_spin()
        self.pos_max_x = self._make_pos_spin()
        self.pos_min_y = self._make_pos_spin()
        self.pos_max_y = self._make_pos_spin()
        form.addRow("Min X:", self.pos_min_x)
        form.addRow("Max X:", self.pos_max_x)
        form.addRow("Min Y:", self.pos_min_y)
        form.addRow("Max Y:", self.pos_max_y)
        return widget

    def _build_axis_params(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.axis_lock_x = QCheckBox("Lock X")
        self.axis_lock_y = QCheckBox("Lock Y")
        self.axis_x_value = self._make_pos_spin()
        self.axis_y_value = self._make_pos_spin()
        form.addRow("", self.axis_lock_x)
        form.addRow("X Value:", self.axis_x_value)
        form.addRow("", self.axis_lock_y)
        form.addRow("Y Value:", self.axis_y_value)
        return widget

    def _build_distance_params(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        self.distance_spin = self._make_pos_spin()
        self.distance_spin.setMinimum(0.0)
        self.distance_spin.setMaximum(100000.0)
        self.target_combo = QComboBox()
        for name, layer_id in self._layer_entries:
            self.target_combo.addItem(name, layer_id)
        form.addRow("Distance:", self.distance_spin)
        form.addRow("Target:", self.target_combo)
        return widget

    def _make_angle_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-360.0, 360.0)
        spin.setDecimals(2)
        spin.setSingleStep(1.0)
        return spin

    def _make_scale_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.01, 100.0)
        spin.setDecimals(3)
        spin.setSingleStep(0.05)
        return spin

    def _make_pos_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-100000.0, 100000.0)
        spin.setDecimals(2)
        spin.setSingleStep(1.0)
        return spin

    def _set_defaults(self):
        self.rot_min_spin.setValue(-45.0)
        self.rot_max_spin.setValue(45.0)
        self.scale_min_x.setValue(0.5)
        self.scale_max_x.setValue(2.0)
        self.scale_min_y.setValue(0.5)
        self.scale_max_y.setValue(2.0)
        self.scale_uniform.setChecked(True)

        anchor = self._current_anchor()
        if anchor:
            ax, ay = anchor
            self.pos_min_x.setValue(ax - 50.0)
            self.pos_max_x.setValue(ax + 50.0)
            self.pos_min_y.setValue(ay - 50.0)
            self.pos_max_y.setValue(ay + 50.0)
            self.axis_lock_x.setChecked(True)
            self.axis_lock_y.setChecked(False)
            self.axis_x_value.setValue(ax)
            self.axis_y_value.setValue(ay)

        self.distance_spin.setValue(0.0)

    def _load_constraint(self, constraint: ConstraintSpec):
        if constraint.label:
            self.label_input.setText(constraint.label)
        type_index = self._type_keys.index(constraint.ctype) if constraint.ctype in self._type_keys else 0
        self.type_combo.setCurrentIndex(type_index)
        if constraint.layer_id is not None:
            idx = self.layer_combo.findData(constraint.layer_id)
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)
        elif constraint.layer_name:
            idx = self.layer_combo.findText(constraint.layer_name)
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)

        params = constraint.params or {}
        if constraint.ctype == "rotation_clamp":
            self.rot_min_spin.setValue(float(params.get("min", -45.0)))
            self.rot_max_spin.setValue(float(params.get("max", 45.0)))
        elif constraint.ctype == "scale_clamp":
            self.scale_min_x.setValue(float(params.get("min_x", 0.5)))
            self.scale_max_x.setValue(float(params.get("max_x", 2.0)))
            self.scale_min_y.setValue(float(params.get("min_y", 0.5)))
            self.scale_max_y.setValue(float(params.get("max_y", 2.0)))
            self.scale_uniform.setChecked(bool(params.get("uniform", False)))
        elif constraint.ctype == "position_clamp":
            self.pos_min_x.setValue(float(params.get("min_x", 0.0)))
            self.pos_max_x.setValue(float(params.get("max_x", 0.0)))
            self.pos_min_y.setValue(float(params.get("min_y", 0.0)))
            self.pos_max_y.setValue(float(params.get("max_y", 0.0)))
        elif constraint.ctype == "axis_lock":
            self.axis_lock_x.setChecked(bool(params.get("lock_x", False)))
            self.axis_lock_y.setChecked(bool(params.get("lock_y", False)))
            if params.get("x") is not None:
                self.axis_x_value.setValue(float(params.get("x")))
            if params.get("y") is not None:
                self.axis_y_value.setValue(float(params.get("y")))
        elif constraint.ctype == "distance":
            self.distance_spin.setValue(float(params.get("distance", 0.0)))
            target_id = constraint.target_layer_id
            if target_id is not None:
                idx = self.target_combo.findData(target_id)
                if idx >= 0:
                    self.target_combo.setCurrentIndex(idx)
            elif constraint.target_layer_name:
                idx = self.target_combo.findText(constraint.target_layer_name)
                if idx >= 0:
                    self.target_combo.setCurrentIndex(idx)

    def _on_type_changed(self, _index: int):
        key = self.type_combo.currentData()
        if key in self._type_keys:
            self.param_stack.setCurrentIndex(self._type_keys.index(key))

    def _current_anchor(self) -> Optional[Tuple[float, float]]:
        layer_id = self.layer_combo.currentData()
        if layer_id is None:
            return None
        return self._anchor_positions.get(int(layer_id))

    def build_constraint(self) -> Optional[ConstraintSpec]:
        ctype = self.type_combo.currentData()
        if not ctype:
            return None
        layer_id = self.layer_combo.currentData()
        layer_name = self.layer_combo.currentText()
        label = self.label_input.text().strip()
        params: Dict[str, float] = {}
        target_layer_id = None
        target_layer_name = None

        if ctype == "rotation_clamp":
            params["min"] = self.rot_min_spin.value()
            params["max"] = self.rot_max_spin.value()
        elif ctype == "scale_clamp":
            params["min_x"] = self.scale_min_x.value()
            params["max_x"] = self.scale_max_x.value()
            params["min_y"] = self.scale_min_y.value()
            params["max_y"] = self.scale_max_y.value()
            params["uniform"] = bool(self.scale_uniform.isChecked())
        elif ctype == "position_clamp":
            params["min_x"] = self.pos_min_x.value()
            params["max_x"] = self.pos_max_x.value()
            params["min_y"] = self.pos_min_y.value()
            params["max_y"] = self.pos_max_y.value()
        elif ctype == "axis_lock":
            params["lock_x"] = bool(self.axis_lock_x.isChecked())
            params["lock_y"] = bool(self.axis_lock_y.isChecked())
            params["x"] = self.axis_x_value.value()
            params["y"] = self.axis_y_value.value()
        elif ctype == "distance":
            params["distance"] = self.distance_spin.value()
            target_layer_id = self.target_combo.currentData()
            target_layer_name = self.target_combo.currentText()

        cid = self._constraint.cid if self._constraint else ConstraintSpec.new_id()
        return ConstraintSpec(
            cid=cid,
            ctype=str(ctype),
            enabled=self._constraint.enabled if self._constraint else True,
            layer_id=int(layer_id) if layer_id is not None else None,
            layer_name=str(layer_name) if layer_name else None,
            target_layer_id=int(target_layer_id) if target_layer_id is not None else None,
            target_layer_name=str(target_layer_name) if target_layer_name else None,
            label=label,
            params=params,
        )
