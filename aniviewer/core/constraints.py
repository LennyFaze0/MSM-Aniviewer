"""
Constraint system for interactive editing.
Applies simple constraints to layer transforms without altering source animation data.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .data_structures import LayerData


@dataclass
class ConstraintSpec:
    """Serializable constraint definition."""

    cid: str
    ctype: str
    enabled: bool = True
    layer_id: Optional[int] = None
    layer_name: Optional[str] = None
    target_layer_id: Optional[int] = None
    target_layer_name: Optional[str] = None
    label: str = ""
    params: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ConstraintSpec":
        return ConstraintSpec(
            cid=str(data.get("cid") or ConstraintSpec.new_id()),
            ctype=str(data.get("ctype") or "position_clamp"),
            enabled=bool(data.get("enabled", True)),
            layer_id=data.get("layer_id"),
            layer_name=data.get("layer_name"),
            target_layer_id=data.get("target_layer_id"),
            target_layer_name=data.get("target_layer_name"),
            label=str(data.get("label") or ""),
            params=dict(data.get("params") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cid": self.cid,
            "ctype": self.ctype,
            "enabled": bool(self.enabled),
            "layer_id": self.layer_id,
            "layer_name": self.layer_name,
            "target_layer_id": self.target_layer_id,
            "target_layer_name": self.target_layer_name,
            "label": self.label,
            "params": dict(self.params or {}),
        }


class ConstraintManager:
    """Applies editing constraints to layer world states or user offsets."""

    TYPE_LABELS = {
        "rotation_clamp": "Rotation Clamp",
        "scale_clamp": "Scale Clamp",
        "position_clamp": "Position Clamp",
        "axis_lock": "Axis Lock",
        "distance": "Distance",
    }

    def __init__(self) -> None:
        self.enabled: bool = True
        self.constraints: List[ConstraintSpec] = []
        self.disabled_layer_names: Set[str] = set()

    def set_constraints(self, constraints: List[ConstraintSpec]) -> None:
        self.constraints = list(constraints)

    def describe(self, spec: ConstraintSpec, layer_map: Dict[int, LayerData]) -> str:
        label = spec.label.strip()
        type_label = self.TYPE_LABELS.get(spec.ctype, spec.ctype)
        layer_name = self._resolve_layer_name(spec, layer_map) or "Unknown Layer"
        if spec.ctype == "distance":
            target = self._resolve_target_name(spec, layer_map) or "Unknown Target"
            base = f"{type_label}: {layer_name} -> {target}"
        else:
            base = f"{type_label}: {layer_name}"
        return label if label else base

    def apply_to_world_states(
        self,
        world_states: Dict[int, Dict[str, Any]],
        layer_map: Dict[int, LayerData],
        layer_offsets: Optional[Dict[int, Tuple[float, float]]] = None,
    ) -> bool:
        """Apply constraints to the world-state dict in-place."""
        if not self.enabled or not self.constraints:
            return False
        offsets = layer_offsets or {}
        name_map = self._build_name_map(layer_map)
        changed = False

        for spec in self.constraints:
            if not spec.enabled:
                continue
            layer_id = self._resolve_layer_id(spec, layer_map, name_map)
            if layer_id is None or layer_id not in world_states:
                continue
            layer = layer_map.get(layer_id)
            if layer and layer.name.lower() in self.disabled_layer_names:
                continue
            state = world_states[layer_id]
            offset_x, offset_y = offsets.get(layer_id, (0.0, 0.0))
            anchor_x = state.get("anchor_world_x", state.get("tx", 0.0)) + offset_x
            anchor_y = state.get("anchor_world_y", state.get("ty", 0.0)) + offset_y

            if spec.ctype == "rotation_clamp":
                changed |= self._apply_rotation_clamp(state, spec)
            elif spec.ctype == "scale_clamp":
                changed |= self._apply_scale_clamp(state, spec)
            elif spec.ctype == "position_clamp":
                dx, dy = self._compute_position_clamp(anchor_x, anchor_y, spec)
                if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                    self._apply_translation_delta(state, dx, dy)
                    changed = True
            elif spec.ctype == "axis_lock":
                dx, dy = self._compute_axis_lock(anchor_x, anchor_y, spec)
                if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                    self._apply_translation_delta(state, dx, dy)
                    changed = True
            elif spec.ctype == "distance":
                target_id = self._resolve_target_id(spec, layer_map, name_map)
                if target_id is None or target_id not in world_states:
                    continue
                target_state = world_states[target_id]
                target_offset = offsets.get(target_id, (0.0, 0.0))
                target_anchor_x = target_state.get("anchor_world_x", target_state.get("tx", 0.0)) + target_offset[0]
                target_anchor_y = target_state.get("anchor_world_y", target_state.get("ty", 0.0)) + target_offset[1]
                dx, dy = self._compute_distance_delta(
                    anchor_x, anchor_y, target_anchor_x, target_anchor_y, spec
                )
                if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                    self._apply_translation_delta(state, dx, dy)
                    changed = True

        return changed

    def apply_to_offsets(
        self,
        world_states: Dict[int, Dict[str, Any]],
        layer_map: Dict[int, LayerData],
        layer_offsets: Dict[int, Tuple[float, float]],
        layer_rotations: Dict[int, float],
        layer_scales: Dict[int, Tuple[float, float]],
    ) -> bool:
        """Apply constraints by mutating user offset dictionaries."""
        if not self.enabled or not self.constraints:
            return False
        name_map = self._build_name_map(layer_map)
        changed = False

        for spec in self.constraints:
            if not spec.enabled:
                continue
            layer_id = self._resolve_layer_id(spec, layer_map, name_map)
            if layer_id is None or layer_id not in world_states:
                continue
            layer = layer_map.get(layer_id)
            if layer and layer.name.lower() in self.disabled_layer_names:
                continue

            if spec.ctype == "rotation_clamp":
                current = float(layer_rotations.get(layer_id, 0.0))
                clamped = self._clamp(current, spec.params.get("min"), spec.params.get("max"))
                if abs(clamped - current) > 1e-6:
                    layer_rotations[layer_id] = clamped
                    changed = True
            elif spec.ctype == "scale_clamp":
                current_scale = layer_scales.get(layer_id, (1.0, 1.0))
                new_scale = self._clamp_scale(current_scale, spec)
                if (
                    abs(new_scale[0] - current_scale[0]) > 1e-6
                    or abs(new_scale[1] - current_scale[1]) > 1e-6
                ):
                    layer_scales[layer_id] = new_scale
                    changed = True
            elif spec.ctype in {"position_clamp", "axis_lock", "distance"}:
                state = world_states[layer_id]
                offset_x, offset_y = layer_offsets.get(layer_id, (0.0, 0.0))
                anchor_x = state.get("anchor_world_x", state.get("tx", 0.0)) + offset_x
                anchor_y = state.get("anchor_world_y", state.get("ty", 0.0)) + offset_y
                if spec.ctype == "position_clamp":
                    dx, dy = self._compute_position_clamp(anchor_x, anchor_y, spec)
                elif spec.ctype == "axis_lock":
                    dx, dy = self._compute_axis_lock(anchor_x, anchor_y, spec)
                else:
                    target_id = self._resolve_target_id(spec, layer_map, name_map)
                    if target_id is None or target_id not in world_states:
                        continue
                    target_state = world_states[target_id]
                    target_offset = layer_offsets.get(target_id, (0.0, 0.0))
                    target_anchor_x = target_state.get("anchor_world_x", target_state.get("tx", 0.0)) + target_offset[0]
                    target_anchor_y = target_state.get("anchor_world_y", target_state.get("ty", 0.0)) + target_offset[1]
                    dx, dy = self._compute_distance_delta(
                        anchor_x, anchor_y, target_anchor_x, target_anchor_y, spec
                    )
                if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                    layer_offsets[layer_id] = (offset_x + dx, offset_y + dy)
                    changed = True

        return changed

    def _resolve_layer_name(self, spec: ConstraintSpec, layer_map: Dict[int, LayerData]) -> Optional[str]:
        if spec.layer_id is not None and spec.layer_id in layer_map:
            return layer_map[spec.layer_id].name
        if spec.layer_name:
            return spec.layer_name
        return None

    def _resolve_target_name(self, spec: ConstraintSpec, layer_map: Dict[int, LayerData]) -> Optional[str]:
        if spec.target_layer_id is not None and spec.target_layer_id in layer_map:
            return layer_map[spec.target_layer_id].name
        if spec.target_layer_name:
            return spec.target_layer_name
        return None

    def _build_name_map(self, layer_map: Dict[int, LayerData]) -> Dict[str, int]:
        name_map: Dict[str, int] = {}
        for layer in layer_map.values():
            if not layer.name:
                continue
            key = layer.name.lower()
            if key not in name_map:
                name_map[key] = layer.layer_id
        return name_map

    def _resolve_layer_id(
        self,
        spec: ConstraintSpec,
        layer_map: Dict[int, LayerData],
        name_map: Dict[str, int],
    ) -> Optional[int]:
        if spec.layer_id is not None and spec.layer_id in layer_map:
            return spec.layer_id
        if spec.layer_name:
            return name_map.get(spec.layer_name.lower())
        return None

    def _resolve_target_id(
        self,
        spec: ConstraintSpec,
        layer_map: Dict[int, LayerData],
        name_map: Dict[str, int],
    ) -> Optional[int]:
        if spec.target_layer_id is not None and spec.target_layer_id in layer_map:
            return spec.target_layer_id
        if spec.target_layer_name:
            return name_map.get(spec.target_layer_name.lower())
        return None

    @staticmethod
    def _clamp(value: float, min_val: Any, max_val: Any) -> float:
        result = float(value)
        if min_val is not None:
            try:
                result = max(result, float(min_val))
            except (TypeError, ValueError):
                pass
        if max_val is not None:
            try:
                result = min(result, float(max_val))
            except (TypeError, ValueError):
                pass
        return result

    def _apply_rotation_clamp(self, state: Dict[str, Any], spec: ConstraintSpec) -> bool:
        current = float(state.get("user_rotation", 0.0))
        clamped = self._clamp(current, spec.params.get("min"), spec.params.get("max"))
        delta = clamped - current
        if abs(delta) <= 1e-6:
            return False
        self._apply_rotation_delta(state, delta)
        state["user_rotation"] = clamped
        return True

    def _apply_scale_clamp(self, state: Dict[str, Any], spec: ConstraintSpec) -> bool:
        current_scale = state.get("user_scale", (1.0, 1.0))
        try:
            sx = float(current_scale[0])
            sy = float(current_scale[1])
        except (TypeError, ValueError, IndexError):
            sx, sy = (1.0, 1.0)
        new_sx, new_sy = self._clamp_scale((sx, sy), spec)
        if abs(new_sx - sx) <= 1e-6 and abs(new_sy - sy) <= 1e-6:
            return False
        scale_x = new_sx / sx if abs(sx) > 1e-6 else 1.0
        scale_y = new_sy / sy if abs(sy) > 1e-6 else 1.0
        self._apply_scale_delta(state, scale_x, scale_y)
        state["user_scale"] = (new_sx, new_sy)
        return True

    def _clamp_scale(
        self, current_scale: Tuple[float, float], spec: ConstraintSpec
    ) -> Tuple[float, float]:
        sx, sy = current_scale
        min_x = spec.params.get("min_x")
        max_x = spec.params.get("max_x")
        min_y = spec.params.get("min_y")
        max_y = spec.params.get("max_y")
        uniform = bool(spec.params.get("uniform", False))
        if uniform:
            base_min = min_x if min_x is not None else min_y
            base_max = max_x if max_x is not None else max_y
            clamped = self._clamp(sx, base_min, base_max)
            return (clamped, clamped)
        new_sx = self._clamp(sx, min_x, max_x)
        new_sy = self._clamp(sy, min_y, max_y)
        return (new_sx, new_sy)

    def _compute_position_clamp(
        self, anchor_x: float, anchor_y: float, spec: ConstraintSpec
    ) -> Tuple[float, float]:
        min_x = spec.params.get("min_x")
        max_x = spec.params.get("max_x")
        min_y = spec.params.get("min_y")
        max_y = spec.params.get("max_y")
        target_x = self._clamp(anchor_x, min_x, max_x)
        target_y = self._clamp(anchor_y, min_y, max_y)
        return target_x - anchor_x, target_y - anchor_y

    def _compute_axis_lock(
        self, anchor_x: float, anchor_y: float, spec: ConstraintSpec
    ) -> Tuple[float, float]:
        dx = dy = 0.0
        if bool(spec.params.get("lock_x", False)) and spec.params.get("x") is not None:
            try:
                dx = float(spec.params.get("x")) - anchor_x
            except (TypeError, ValueError):
                dx = 0.0
        if bool(spec.params.get("lock_y", False)) and spec.params.get("y") is not None:
            try:
                dy = float(spec.params.get("y")) - anchor_y
            except (TypeError, ValueError):
                dy = 0.0
        return dx, dy

    def _compute_distance_delta(
        self,
        anchor_x: float,
        anchor_y: float,
        target_x: float,
        target_y: float,
        spec: ConstraintSpec,
    ) -> Tuple[float, float]:
        try:
            distance = float(spec.params.get("distance", 0.0))
        except (TypeError, ValueError):
            return (0.0, 0.0)
        dx = anchor_x - target_x
        dy = anchor_y - target_y
        current = math.hypot(dx, dy)
        if current <= 1e-6:
            return (distance, 0.0) if distance != 0.0 else (0.0, 0.0)
        scale = distance / current
        target_anchor_x = target_x + dx * scale
        target_anchor_y = target_y + dy * scale
        return target_anchor_x - anchor_x, target_anchor_y - anchor_y

    @staticmethod
    def _apply_translation_delta(state: Dict[str, Any], dx: float, dy: float) -> None:
        state["tx"] = float(state.get("tx", 0.0)) + dx
        state["ty"] = float(state.get("ty", 0.0)) + dy
        if "anchor_world_x" in state:
            state["anchor_world_x"] = float(state.get("anchor_world_x", 0.0)) + dx
        if "anchor_world_y" in state:
            state["anchor_world_y"] = float(state.get("anchor_world_y", 0.0)) + dy

    @staticmethod
    def _apply_rotation_delta(state: Dict[str, Any], delta_deg: float) -> None:
        if abs(delta_deg) <= 1e-6:
            return
        rad = math.radians(delta_deg)
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)
        r00, r01, r10, r11 = cos_r, -sin_r, sin_r, cos_r

        m00 = state["m00"]
        m01 = state["m01"]
        m10 = state["m10"]
        m11 = state["m11"]
        tx = state["tx"]
        ty = state["ty"]
        pivot_x = state.get("anchor_world_x", tx)
        pivot_y = state.get("anchor_world_y", ty)

        state["m00"] = r00 * m00 + r01 * m10
        state["m01"] = r00 * m01 + r01 * m11
        state["m10"] = r10 * m00 + r11 * m10
        state["m11"] = r10 * m01 + r11 * m11

        state["tx"] = r00 * tx + r01 * ty + (pivot_x - (r00 * pivot_x + r01 * pivot_y))
        state["ty"] = r10 * tx + r11 * ty + (pivot_y - (r10 * pivot_x + r11 * pivot_y))

    @staticmethod
    def _apply_scale_delta(state: Dict[str, Any], scale_x: float, scale_y: float) -> None:
        if abs(scale_x - 1.0) <= 1e-6 and abs(scale_y - 1.0) <= 1e-6:
            return
        m00 = state["m00"]
        m01 = state["m01"]
        m10 = state["m10"]
        m11 = state["m11"]
        tx = state["tx"]
        ty = state["ty"]
        pivot_x = state.get("anchor_world_x", tx)
        pivot_y = state.get("anchor_world_y", ty)

        state["m00"] = scale_x * m00
        state["m01"] = scale_x * m01
        state["m10"] = scale_y * m10
        state["m11"] = scale_y * m11

        state["tx"] = scale_x * tx + (pivot_x - scale_x * pivot_x)
        state["ty"] = scale_y * ty + (pivot_y - scale_y * pivot_y)
