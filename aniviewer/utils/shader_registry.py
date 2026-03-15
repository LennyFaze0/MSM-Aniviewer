"""
Shader registry
Loads shader approximation metadata from JSON and user overrides.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple, Any, List


def _to_tuple(values: Iterable[float], length: int = 3) -> Tuple[float, ...]:
    seq = list(values or [])
    if len(seq) < length:
        seq.extend([1.0] * (length - len(seq)))
    return tuple(float(v) for v in seq[:length])


@dataclass
class _ColorWaveChannel:
    """Per-channel sine wave parameters for shader color modulation."""

    channel: str
    min_value: float
    max_value: float
    frequency: float
    phase: float

    def value_at(self, time_value: float) -> float:
        if math.isclose(self.max_value, self.min_value, rel_tol=1e-5, abs_tol=1e-5):
            return max(0.0, self.max_value)
        if math.isclose(self.frequency, 0.0, abs_tol=1e-8):
            return max(0.0, (self.max_value + self.min_value) * 0.5)
        amplitude = (self.max_value - self.min_value) / 2.0
        center = (self.max_value + self.min_value) / 2.0
        return max(0.0, center + amplitude * math.sin(time_value * self.frequency + self.phase))


@dataclass
class ShaderPreset:
    """Description of a shader resource approximation entry."""

    name: str
    display_name: str
    color_scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    alpha_scale: float = 1.0
    blend_mode: Optional[str] = None
    fragment: Optional[str] = None
    vertex: Optional[str] = None
    lut: Optional[str] = None
    notes: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary describing the preset."""
        payload = {
            "display_name": self.display_name,
            "color_scale": list(self.color_scale),
            "alpha_scale": self.alpha_scale,
            "notes": self.notes,
        }
        if self.blend_mode:
            payload["blend_override"] = self.blend_mode
        if self.fragment:
            payload["fragment"] = self.fragment
        if self.vertex:
            payload["vertex"] = self.vertex
        if self.lut:
            payload["lut"] = self.lut
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


class ShaderRegistry:
    """Central source for shader approximation metadata."""

    def __init__(self, project_root: Path, config_path: Optional[Path] = None):
        self.project_root = Path(project_root)
        if config_path is None:
            config_path = self.project_root / "Resources" / "shaders" / "shader_presets.json"
        self.config_path = Path(config_path)
        self._defaults: Dict[str, ShaderPreset] = {}
        self._overrides: Dict[str, Dict[str, Any]] = {}
        self._dynamic_payloads: Dict[str, Dict[str, Any]] = {}
        self._runtime_overrides: Dict[str, Dict[str, Any]] = {}
        self.game_path: Optional[Path] = None
        self.costume_texture_dir: Optional[Path] = None
        self._costume_texture_index: List[Tuple[str, Path]] = []
        self.behavior_path = self.project_root / "Resources" / "shaders" / "costume_shader_behaviors.json"
        self.behaviors: Dict[str, ShaderBehavior] = {}
        self.load_behaviors()
        self.load_defaults()
        self.set_game_path(None)

    # ------------------------------------------------------------------ loading

    def load_defaults(self) -> None:
        """Load preset data from the shared JSON file."""
        if not self.config_path.exists():
            self._defaults = {}
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        presets: Dict[str, ShaderPreset] = {}
        for key, payload in data.items():
            preset = self._build_preset(key, payload or {})
            presets[preset.name] = preset
        self._defaults = presets

    def set_user_overrides(self, overrides: Optional[Dict[str, Any]]) -> None:
        """Apply user-defined overrides."""
        self._overrides = {}
        if not overrides:
            return
        for name, payload in overrides.items():
            normalized = self._normalize_payload(payload or {})
            if normalized is not None:
                self._overrides[name.lower()] = normalized

    # ---------------------------------------------------------------- lookups

    def list_shader_names(self) -> Iterable[str]:
        """Return all shader keys known in defaults or overrides."""
        keys = (
            set(self._defaults.keys())
            | set(self._overrides.keys())
            | set(self._dynamic_payloads.keys())
            | set(self._runtime_overrides.keys())
        )
        return sorted(keys)

    def get_preset(self, shader_name: Optional[str]) -> Optional[ShaderPreset]:
        """Return the merged preset for a shader resource name."""
        if not shader_name:
            return None
        key = shader_name.lower()
        base = self._defaults.get(key)
        dynamic_payload = None
        if not base:
            dynamic_payload = self._dynamic_payloads.get(key)
        override_payload = self._overrides.get(key)
        if not base and not dynamic_payload and not override_payload:
            return None
        if base:
            base_payload: Dict[str, Any] = base.to_dict()
        elif dynamic_payload:
            base_payload = dict(dynamic_payload)
            if "metadata" in dynamic_payload:
                base_payload["metadata"] = dict(dynamic_payload["metadata"])
        else:
            base_payload = {"display_name": shader_name}
        merged = dict(base_payload)
        runtime_payload = self._runtime_overrides.get(key)
        if runtime_payload:
            merged = self._merge_payloads(merged, runtime_payload)
        if override_payload:
            merged = self._merge_payloads(merged, override_payload)
        return self._build_preset(shader_name, merged)

    def register_costume_shader(
        self,
        shader_name: Optional[str],
        costume_key: Optional[str] = None,
        node: Optional[str] = None,
        texture_path: Optional[str] = None,
    ) -> None:
        """Record shader usage so the UI can expose it even without manual presets."""
        if not shader_name:
            return
        key = shader_name.lower()
        payload = self._dynamic_payloads.setdefault(
            key,
            {
                "display_name": shader_name,
            },
        )
        metadata = payload.setdefault("metadata", {})
        self._append_metadata_value(metadata, "costumes", costume_key)
        self._append_metadata_value(metadata, "nodes", node)
        if texture_path:
            metadata["last_texture"] = texture_path
        payload["metadata"] = metadata
        self._dynamic_payloads[key] = payload

    def get_override_payloads(self) -> Dict[str, Dict[str, Any]]:
        """Return a shallow copy of user overrides for persistence."""
        return dict(self._overrides)

    def ensure_entry(self, shader_name: str) -> ShaderPreset:
        """Return a preset, creating a blank override if unknown."""
        preset = self.get_preset(shader_name)
        if preset:
            return preset
        key = shader_name.lower()
        self._overrides.setdefault(key, {"display_name": shader_name})
        return self._build_preset(shader_name, {"display_name": shader_name})

    def update_override(self, shader_name: str, payload: Dict[str, Any]) -> None:
        """Store/replace a single override entry."""
        normalized = self._normalize_payload(payload or {})
        key = shader_name.lower()
        if not normalized:
            self._overrides.pop(key, None)
            return
        self._overrides[key] = normalized

    def get_default_preset(self, shader_name: str) -> Optional[ShaderPreset]:
        """Return the default preset without overrides."""
        return self._defaults.get(shader_name.lower())

    def build_preset_from_payload(self, shader_name: str, payload: Dict[str, Any]) -> ShaderPreset:
        """Construct a preset from an arbitrary payload (used by UI)."""
        return self._build_preset(shader_name, payload)

    def load_behaviors(self) -> None:
        """Load costume shader behavior metadata."""
        behaviors: Dict[str, ShaderBehavior] = {}
        if not self.behavior_path.exists():
            self.behaviors = behaviors
            return
        try:
            data = json.loads(self.behavior_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for name, payload in data.items():
            try:
                behavior = ShaderBehavior.from_payload(name, payload or {})
                behaviors[behavior.name] = behavior
            except Exception as exc:
                print(f"Warning: failed to parse shader behavior '{name}': {exc}")
        self.behaviors = behaviors

    def get_behavior(self, shader_name: Optional[str]) -> Optional["ShaderBehavior"]:
        if not shader_name:
            return None
        return self.behaviors.get(shader_name.lower())

    def set_runtime_overrides(self, overrides: Dict[str, Dict[str, Any]]) -> None:
        """Replace runtime overrides applied while a costume is active."""
        self._runtime_overrides = {name.lower(): payload for name, payload in overrides.items()}

    def set_runtime_override(self, shader_name: str, payload: Dict[str, Any]) -> None:
        """Set or replace a single runtime override entry."""
        if not shader_name:
            return
        self._runtime_overrides[shader_name.lower()] = payload

    @staticmethod
    def _append_metadata_value(metadata: Dict[str, Any], key: str, value: Optional[str]) -> None:
        if not value:
            return
        bucket = metadata.setdefault(key, [])
        if value not in bucket:
            bucket.append(value)

    # ----------------------------------------------------------- game context

    def set_game_path(self, game_path: Optional[str]):
        """Record the active game path so resources can be located automatically."""
        self.game_path = Path(game_path) if game_path else None
        self.costume_texture_dir = None
        self._costume_texture_index = []
        if not self.game_path:
            return
        costume_dir = self.game_path / "data" / "gfx" / "costumes"
        if costume_dir.exists():
            self.costume_texture_dir = costume_dir
            self._costume_texture_index = [
                (path.name.lower(), path)
                for path in costume_dir.iterdir()
                if path.is_file()
            ]

    # --------------------------------------------------------------- utilities

    def _build_preset(self, shader_name: str, payload: Dict[str, Any]) -> ShaderPreset:
        color_scale = _to_tuple(payload.get("color_scale", (1.0, 1.0, 1.0)))
        alpha_scale = float(payload.get("alpha_scale", 1.0))
        blend_mode = payload.get("blend_override")
        display = payload.get("display_name") or shader_name
        notes = payload.get("notes", "")
        fragment = payload.get("fragment")
        vertex = payload.get("vertex")
        lut = payload.get("lut")
        metadata = payload.get("metadata") or {}
        behavior = self.get_behavior(shader_name)
        if behavior and "behavior" not in metadata:
            metadata["behavior"] = behavior.name
        return ShaderPreset(
            name=shader_name.lower(),
            display_name=display,
            color_scale=color_scale,  # type: ignore[arg-type]
            alpha_scale=alpha_scale,
            blend_mode=blend_mode,
            fragment=fragment,
            vertex=vertex,
            lut=lut,
            notes=notes,
            metadata=metadata,
        )

    def _merge_payloads(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base)
        for key, value in override.items():
            if key == "metadata":
                combined = dict(base.get("metadata", {}))
                combined.update(value or {})
                merged["metadata"] = combined
            else:
                merged[key] = value
        return merged

    def _normalize_payload(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a cleaned-up version suitable for persistence."""
        if not payload:
            return None
        normalized: Dict[str, Any] = {}
        if display := payload.get("display_name"):
            normalized["display_name"] = str(display)
        if "color_scale" in payload:
            normalized["color_scale"] = list(_to_tuple(payload["color_scale"]))
        if "alpha_scale" in payload:
            normalized["alpha_scale"] = float(payload["alpha_scale"])
        if "blend_override" in payload:
            normalized["blend_override"] = payload["blend_override"]
        if "fragment" in payload:
            normalized["fragment"] = payload["fragment"]
        if "vertex" in payload:
            normalized["vertex"] = payload["vertex"]
        if "lut" in payload:
            normalized["lut"] = payload["lut"]
        if "notes" in payload:
            normalized["notes"] = payload["notes"]
        metadata = payload.get("metadata")
        if metadata:
            normalized["metadata"] = metadata
        return normalized or None


@dataclass
class ShaderBehavior:
    """Describes animation metadata for costume shader sequences."""

    name: str
    texture_suffix: str = ""
    replace_base_sprite: bool = False
    sheet_width: float = 1.0
    sheet_height: float = 1.0
    sheet_offset_x: float = 0.0
    sheet_offset_y: float = 0.0
    uv_divisor: float = 1.0
    strip_width: float = 1.0
    strip_height: float = 1.0
    frame_width: float = 1.0
    frame_axis: str = "u"
    cycle_time: float = 1.0
    frame_thresholds: List[float] = field(default_factory=list)
    frame_values: List[float] = field(default_factory=list)
    requires_texture: bool = True
    mapping_mode: str = "strip"
    color_wave_min: float = 1.0
    color_wave_max: float = 1.0
    color_wave_frequency: float = 0.0
    color_wave_phase: float = 0.0
    color_wave_channels: Tuple[str, ...] = field(default_factory=lambda: ("r", "g", "b"))
    color_wave_affect_alpha: bool = False
    color_wave_channel_params: List[_ColorWaveChannel] = field(default_factory=list)

    @classmethod
    def from_payload(cls, name: str, payload: Dict[str, Any]) -> "ShaderBehavior":
        color_wave = payload.get("color_wave") or {}
        wave_params = cls._build_color_wave_params(color_wave)
        channel_tuple: Tuple[str, ...]
        if wave_params:
            channel_tuple = tuple(param.channel for param in wave_params)
        else:
            channel_tuple = cls._parse_wave_channels(color_wave.get("channels"))
        return cls(
            name=name.lower(),
            texture_suffix=payload.get("texture_suffix", ""),
            replace_base_sprite=bool(payload.get("replace_base_sprite", False)),
            sheet_width=float(payload.get("sheet_width", 1.0)),
            sheet_height=float(payload.get("sheet_height", 1.0)),
            sheet_offset_x=float(payload.get("sheet_offset_x", 0.0)),
            sheet_offset_y=float(payload.get("sheet_offset_y", 0.0)),
            uv_divisor=float(payload.get("uv_divisor", 1.0)),
            strip_width=float(payload.get("strip_width", 1.0)),
            strip_height=float(payload.get("strip_height", 1.0)),
            frame_width=float(payload.get("frame_width", 1.0)),
            frame_axis=str(payload.get("frame_axis", "u")).lower(),
            cycle_time=float(payload.get("cycle_time", 1.0)),
            frame_thresholds=list(payload.get("frame_thresholds", [])),
            frame_values=list(payload.get("frame_values", [])),
            requires_texture=bool(payload.get("requires_texture", True)),
            mapping_mode=str(payload.get("mapping_mode", "strip")).lower(),
            color_wave_min=float(color_wave.get("min", 1.0)),
            color_wave_max=float(color_wave.get("max", 1.0)),
            color_wave_frequency=float(color_wave.get("frequency", 0.0)),
            color_wave_phase=float(color_wave.get("phase", 0.0)),
            color_wave_channels=channel_tuple,
            color_wave_affect_alpha=bool(color_wave.get("affect_alpha", False)),
            color_wave_channel_params=wave_params,
        )

    @staticmethod
    def _parse_wave_channels(channels: Any) -> Tuple[str, ...]:
        if channels is None:
            return ("r", "g", "b")
        if isinstance(channels, str):
            values: List[Any] = [channels]
        else:
            values = list(channels)
        normalized: List[str] = []
        for entry in values:
            if isinstance(entry, dict):
                token = str(entry.get("channel") or entry.get("name") or entry.get("id") or "").strip()
                if not token:
                    continue
                letter = token.lower()[0]
                if letter in ("r", "g", "b", "a"):
                    normalized.append(letter)
                continue
            for token in str(entry).split(","):
                token = token.strip()
                if not token:
                    continue
                letter = token.lower()[0]
                if letter in ("r", "g", "b", "a"):
                    normalized.append(letter)
        return tuple(normalized) or ("r", "g", "b")

    @staticmethod
    def _build_color_wave_params(config: Dict[str, Any]) -> List[_ColorWaveChannel]:
        channels = config.get("channels")
        if not channels:
            return []
        base_min = float(config.get("min", 1.0))
        base_max = float(config.get("max", 1.0))
        base_freq = float(config.get("frequency", 0.0))
        base_phase = float(config.get("phase", 0.0))
        params: List[_ColorWaveChannel] = []
        entries = [channels] if isinstance(channels, (str, dict)) else list(channels)
        for entry in entries:
            if isinstance(entry, dict):
                name = entry.get("channel") or entry.get("name") or entry.get("id")
                if not name:
                    continue
                channel = str(name).strip().lower()
                if not channel:
                    continue
                channel_id = channel[0]
                if channel_id not in ("r", "g", "b", "a"):
                    continue
                params.append(
                    _ColorWaveChannel(
                        channel=channel_id,
                        min_value=float(entry.get("min", base_min)),
                        max_value=float(entry.get("max", base_max)),
                        frequency=float(entry.get("frequency", base_freq)),
                        phase=float(entry.get("phase", base_phase)),
                    )
                )
            else:
                for token in str(entry).split(","):
                    token = token.strip()
                    if not token:
                        continue
                    channel_id = token.lower()[0]
                    if channel_id not in ("r", "g", "b", "a"):
                        continue
                    params.append(
                        _ColorWaveChannel(
                            channel=channel_id,
                            min_value=base_min,
                            max_value=base_max,
                            frequency=base_freq,
                            phase=base_phase,
                        )
                    )
        return params

    def compute_frame(self, time_value: float) -> float:
        if self.cycle_time <= 0:
            return 0.0
        t = time_value % self.cycle_time
        for threshold, frame in zip(self.frame_thresholds, self.frame_values):
            if t < threshold:
                return frame
        if len(self.frame_values) > len(self.frame_thresholds):
            return self.frame_values[-1]
        return 0.0

    def frame_count(self) -> int:
        if self.frame_values:
            try:
                max_frame = max(int(round(value)) for value in self.frame_values)
            except (TypeError, ValueError):
                max_frame = 0
            return max(1, max_frame + 1)
        if self.frame_width > 0 and self.strip_width > 0:
            approx = int(round(self.strip_width / self.frame_width))
            return max(1, approx)
        return 1

    def transform_uv(
        self,
        texcoord: Tuple[float, float],
        frame_index: float,
        texture_size: Optional[Tuple[float, float]] = None
    ) -> Tuple[float, float]:
        u, v = texcoord
        sheet_u = u * self.sheet_width
        sheet_v = v * self.sheet_height
        local_u = (sheet_u - self.sheet_offset_x) / self.uv_divisor
        local_v = (sheet_v - self.sheet_offset_y) / self.uv_divisor
        actual_w = texture_size[0] if texture_size and texture_size[0] > 0 else self.strip_width
        actual_h = texture_size[1] if texture_size and texture_size[1] > 0 else self.strip_height
        if actual_w <= 0 or actual_h <= 0 or self.strip_width <= 0 or self.strip_height <= 0:
            return 0.0, 0.0
        scale_u = actual_w / self.strip_width
        scale_v = actual_h / self.strip_height
        pixel_u = local_u * scale_u
        pixel_v = local_v * scale_v
        frame_total = max(1, self.frame_count())
        axis = self.frame_axis.lower()
        if axis == "v":
            pixel_v += frame_index * (self.frame_width / max(1.0, self.strip_height)) * actual_h
        else:
            pixel_u += frame_index * (self.frame_width / max(1.0, self.strip_width)) * actual_w
        seq_u = pixel_u / actual_w
        seq_v = pixel_v / actual_h
        return (
            max(0.0, min(1.0, seq_u)),
            max(0.0, min(1.0, seq_v)),
        )

    def color_wave_multiplier(self, time_value: float) -> Optional[Tuple[float, float, float, float]]:
        if self.color_wave_channel_params:
            multipliers = {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}
            has_alpha = False
            for param in self.color_wave_channel_params:
                key = param.channel.lower()
                if key not in multipliers:
                    continue
                value = param.value_at(time_value)
                multipliers[key] = value
                if key == "a":
                    has_alpha = True
            if not has_alpha:
                multipliers["a"] = 1.0
            return multipliers["r"], multipliers["g"], multipliers["b"], multipliers["a"]

        if (
            not self.color_wave_channels
            or math.isclose(self.color_wave_frequency, 0.0, abs_tol=1e-8)
            or math.isclose(self.color_wave_max, self.color_wave_min, rel_tol=1e-5, abs_tol=1e-5)
        ):
            return None
        amplitude = (self.color_wave_max - self.color_wave_min) / 2.0
        center = (self.color_wave_max + self.color_wave_min) / 2.0
        value = center + amplitude * math.sin(time_value * self.color_wave_frequency + self.color_wave_phase)
        value = max(0.0, value)
        multipliers = {"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}
        for channel in self.color_wave_channels:
            key = channel.lower()[0]
            if key in multipliers:
                multipliers[key] = value
        if self.color_wave_affect_alpha:
            multipliers["a"] = value
        return multipliers["r"], multipliers["g"], multipliers["b"], multipliers["a"]
