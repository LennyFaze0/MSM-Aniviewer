"""
DOF particle helpers
Extract particle nodes and material/texture data from Unity bundles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import UnityPy  # type: ignore
except Exception:
    UnityPy = None


def _ensure_unitypy():
    """Import UnityPy lazily so runtime installs are picked up."""
    global UnityPy
    if UnityPy is not None:
        return UnityPy
    try:
        import UnityPy as _UnityPy  # type: ignore
    except Exception:
        return None
    UnityPy = _UnityPy
    return UnityPy

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None


@dataclass
class DofParticleNode:
    name: str
    prefab_path_id: int
    offset: Tuple[float, float, float]
    parallax: Tuple[float, float]
    image_scale: float
    channels: Dict[int, List[Tuple[float, float, int]]] = field(default_factory=dict)


@dataclass
class DofControlPoint:
    name: str
    offset: Tuple[float, float, float]
    channels: Dict[int, List[Tuple[float, float, int]]] = field(default_factory=dict)


@dataclass
class DofAnimNode:
    name: str
    offset: Tuple[float, float, float]
    image_scale: float
    channels: Dict[int, List[Tuple[float, float, int]]] = field(default_factory=dict)


@dataclass
class DofParticleMaterial:
    path_id: int
    name: str
    shader_name: str
    colors: Dict[str, Tuple[float, float, float, float]]
    floats: Dict[str, float]
    texture_path_id: Optional[int]


@dataclass
class DofParticleTexture:
    path_id: int
    name: str
    image: "Image.Image"


@dataclass
class DofParticlePrefab:
    path_id: int
    name: str
    material_path_id: Optional[int]
    start_size: float
    transform_pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    transform_rot: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    simulation_space: int = 0
    custom_space_pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    custom_space_rot: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    shape_position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    shape_rotation: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    shape_scale: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    start_size_y: Optional[float] = None
    start_size_range: Tuple[float, float] = (1.0, 1.0)
    start_size_y_range: Optional[Tuple[float, float]] = None
    start_speed_range: Tuple[float, float] = (0.0, 0.0)
    start_rotation_range: Tuple[float, float] = (0.0, 0.0)
    start_lifetime_range: Tuple[float, float] = (0.0, 0.0)
    start_color_range: Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float]] = (
        (1.0, 1.0, 1.0, 1.0),
        (1.0, 1.0, 1.0, 1.0),
    )
    color_over_lifetime_keys: List[Tuple[float, Tuple[float, float, float]]] = field(default_factory=list)
    alpha_over_lifetime_keys: List[Tuple[float, float]] = field(default_factory=list)
    size_over_lifetime_keys: List[Tuple[float, float]] = field(default_factory=list)
    size_over_lifetime_y_keys: List[Tuple[float, float]] = field(default_factory=list)
    rotation_over_lifetime_keys: List[Tuple[float, float]] = field(default_factory=list)
    rotation_over_lifetime_range: Tuple[float, float] = (0.0, 0.0)
    move_with_transform: bool = True
    velocity_over_lifetime_range: Tuple[Tuple[float, float], Tuple[float, float]] = (
        (0.0, 0.0),
        (0.0, 0.0),
    )
    velocity_in_world_space: bool = False
    velocity_module_enabled: bool = False
    emitter_velocity_mode: int = 0
    gravity_modifier_range: Tuple[float, float] = (0.0, 0.0)
    emission_rate_range: Tuple[float, float] = (0.0, 0.0)
    emission_distance_range: Tuple[float, float] = (0.0, 0.0)
    shape_type: int = 0
    shape_placement_mode: int = 0
    shape_radius: float = 0.0
    shape_radius_thickness: float = 1.0
    shape_angle: float = 0.0
    shape_length: float = 0.0
    shape_box: Tuple[float, float] = (0.0, 0.0)


@dataclass
class DofParticleLibrary:
    prefabs: Dict[int, DofParticlePrefab] = field(default_factory=dict)
    materials: Dict[int, DofParticleMaterial] = field(default_factory=dict)
    textures: Dict[int, DofParticleTexture] = field(default_factory=dict)


def _shader_display_name(shader_obj) -> str:
    if not shader_obj:
        return ""
    for attr in ("m_Name", "name"):
        name = getattr(shader_obj, attr, "")
        if name:
            return name
    parsed = getattr(shader_obj, "m_ParsedForm", None)
    if parsed:
        name = getattr(parsed, "m_Name", "")
        if name:
            return name
    return ""


def _curve_scalar(curve, default: float = 1.0) -> float:
    if curve is None:
        return default


def _curve_range(curve, default: float = 1.0) -> Tuple[float, float]:
    if curve is None:
        return (default, default)
    try:
        min_max_state = getattr(curve, "minMaxState", None)
        scalar = float(getattr(curve, "scalar", default))
        min_scalar = float(getattr(curve, "minScalar", scalar))
        if min_max_state == 3:
            lo = min(min_scalar, scalar)
            hi = max(min_scalar, scalar)
            return (lo, hi)
        return (scalar, scalar)
    except Exception:
        return (default, default)


def _gradient_range(gradient, default=(1.0, 1.0, 1.0, 1.0)) -> Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float]]:
    if gradient is None:
        return (default, default)
    try:
        min_color = getattr(gradient, "minColor", None)
        max_color = getattr(gradient, "maxColor", None)
        if min_color is None and max_color is None:
            min_color = getattr(gradient, "maxColor", None)
            max_color = getattr(gradient, "maxColor", None)
        if min_color is None:
            min_color = max_color
        if max_color is None:
            max_color = min_color
        if min_color is None:
            return (default, default)
        min_tuple = (
            float(getattr(min_color, "r", default[0])),
            float(getattr(min_color, "g", default[1])),
            float(getattr(min_color, "b", default[2])),
            float(getattr(min_color, "a", default[3])),
        )
        max_tuple = (
            float(getattr(max_color, "r", default[0])),
            float(getattr(max_color, "g", default[1])),
            float(getattr(max_color, "b", default[2])),
            float(getattr(max_color, "a", default[3])),
        )
        return (min_tuple, max_tuple)
    except Exception:
        return (default, default)
    try:
        min_max_state = getattr(curve, "minMaxState", None)
        scalar = float(getattr(curve, "scalar", default))
        min_scalar = float(getattr(curve, "minScalar", scalar))
        if min_max_state == 3:
            return (scalar + min_scalar) * 0.5
        return scalar
    except Exception:
        return default


def _extract_gradient_keys(gradient) -> Tuple[List[Tuple[float, Tuple[float, float, float]]], List[Tuple[float, float]]]:
    """Extract color/alpha keys from a Unity Gradient object."""
    color_keys: List[Tuple[float, Tuple[float, float, float]]] = []
    alpha_keys: List[Tuple[float, float]] = []
    if gradient is None:
        return color_keys, alpha_keys
    num_color = getattr(gradient, "m_NumColorKeys", None)
    num_alpha = getattr(gradient, "m_NumAlphaKeys", None)
    try:
        num_color = int(num_color) if num_color is not None else 0
    except Exception:
        num_color = 0
    try:
        num_alpha = int(num_alpha) if num_alpha is not None else 0
    except Exception:
        num_alpha = 0
    for idx in range(num_color):
        try:
            t_raw = getattr(gradient, f"ctime{idx}", None)
            key = getattr(gradient, f"key{idx}", None)
        except Exception:
            continue
        if t_raw is None or key is None:
            continue
        try:
            t = float(t_raw) / 65535.0
        except Exception:
            t = 0.0
        color_keys.append(
            (
                max(0.0, min(1.0, t)),
                (
                    float(getattr(key, "r", 1.0)),
                    float(getattr(key, "g", 1.0)),
                    float(getattr(key, "b", 1.0)),
                ),
            )
        )
        if num_alpha == 0:
            alpha_keys.append((max(0.0, min(1.0, t)), float(getattr(key, "a", 1.0))))
    for idx in range(num_alpha):
        try:
            t_raw = getattr(gradient, f"atime{idx}", None)
            key = getattr(gradient, f"key{idx}", None)
        except Exception:
            continue
        if t_raw is None or key is None:
            continue
        try:
            t = float(t_raw) / 65535.0
        except Exception:
            t = 0.0
        alpha_keys.append((max(0.0, min(1.0, t)), float(getattr(key, "a", 1.0))))
    color_keys.sort(key=lambda item: item[0])
    alpha_keys.sort(key=lambda item: item[0])
    return color_keys, alpha_keys


def _extract_curve_keys(curve) -> List[Tuple[float, float]]:
    """Extract (time, value) keys from a Unity MinMaxCurve."""
    if curve is None:
        return []
    try:
        max_curve = getattr(curve, "maxCurve", None)
        min_curve = getattr(curve, "minCurve", None)
    except Exception:
        max_curve = None
        min_curve = None
    for src in (max_curve, min_curve):
        if not src:
            continue
        keys = getattr(src, "m_Curve", None)
        if not keys:
            continue
        out: List[Tuple[float, float]] = []
        for key in keys:
            try:
                t = float(getattr(key, "time", 0.0) or 0.0)
                v = float(getattr(key, "value", 0.0) or 0.0)
            except Exception:
                continue
            out.append((t, v))
        if out:
            return out
    return []


def _select_gradient(minmax_gradient) -> Optional[object]:
    if minmax_gradient is None:
        return None
    max_grad = getattr(minmax_gradient, "maxGradient", None)
    min_grad = getattr(minmax_gradient, "minGradient", None)
    if max_grad is not None:
        return max_grad
    if min_grad is not None:
        return min_grad
    # Some UnityPy builds surface a Gradient directly.
    if hasattr(minmax_gradient, "m_NumColorKeys"):
        return minmax_gradient
    return None


def extract_particle_nodes(bundle_path: str, anim_name: str) -> List[DofParticleNode]:
    unitypy_mod = _ensure_unitypy()
    if unitypy_mod is None:
        return []
    try:
        env = unitypy_mod.load(str(bundle_path))
    except Exception:
        return []
    target_name = anim_name
    if not target_name.lower().endswith(".animbbb"):
        target_name = f"{target_name}.ANIMBBB"
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        name = getattr(data, "m_Name", "") or ""
        if name != target_name and not name.endswith(target_name):
            continue
        particle_nodes = getattr(data, "ParticleNodes", None) or []
        nodes: List[DofParticleNode] = []
        for node in particle_nodes:
            node_name = getattr(node, "Name", "") or "Particle"
            offset = getattr(node, "OffsetPos", None)
            offset_x = float(getattr(offset, "x", 0.0)) if offset else 0.0
            offset_y = float(getattr(offset, "y", 0.0)) if offset else 0.0
            offset_z = float(getattr(offset, "z", 0.0)) if offset else 0.0
            parallax_x = float(getattr(node, "Parallax_X", 0.0) or 0.0)
            parallax_y = float(getattr(node, "Parallax_Y", 0.0) or 0.0)
            image_scale = float(getattr(node, "ImageScale", 1.0) or 1.0)
            prefab = getattr(node, "ParticleSystemPrefab", None)
            prefab_path = int(getattr(prefab, "m_PathID", 0) or 0) if prefab else 0
            channels: Dict[int, List[Tuple[float, float, int]]] = {}
            frame_arrays = getattr(node, "BezierFramesTypes", None) or []
            for idx, frame_array in enumerate(frame_arrays):
                values = getattr(frame_array, "Values", None) or []
                if not values:
                    continue
                keys = []
                for entry in values:
                    try:
                        time = float(getattr(entry, "time", 0.0))
                        value = float(getattr(entry, "value", 0.0))
                        keytype = int(getattr(entry, "keytype", 0))
                    except Exception:
                        continue
                    keys.append((time, value, keytype))
                if keys:
                    keys.sort(key=lambda item: item[0])
                    channels[idx] = keys
            nodes.append(
                DofParticleNode(
                    name=node_name,
                    prefab_path_id=prefab_path,
                    offset=(offset_x, offset_y, offset_z),
                    parallax=(parallax_x, parallax_y),
                    image_scale=image_scale,
                    channels=channels,
                )
            )
        return nodes
    return []


def extract_control_points(
    bundle_path: str,
    anim_name: str,
    names: Optional[List[str]] = None,
) -> Dict[str, DofControlPoint]:
    unitypy_mod = _ensure_unitypy()
    if unitypy_mod is None:
        return {}
    try:
        env = unitypy_mod.load(str(bundle_path))
    except Exception:
        return {}
    target_name = anim_name
    if not target_name.lower().endswith(".animbbb"):
        target_name = f"{target_name}.ANIMBBB"
    wanted = {name for name in (names or []) if name}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        name = getattr(data, "m_Name", "") or ""
        if name != target_name and not name.endswith(target_name):
            continue
        result: Dict[str, DofControlPoint] = {}
        for node in getattr(data, "Nodes", None) or []:
            try:
                node_type = int(getattr(node, "NodeType", -1))
            except Exception:
                node_type = -1
            if node_type != 0:
                continue
            node_name = getattr(node, "Name", "") or "ControlPoint"
            if wanted and node_name not in wanted:
                continue
            offset = getattr(node, "OffsetPos", None)
            offset_x = float(getattr(offset, "x", 0.0)) if offset else 0.0
            offset_y = float(getattr(offset, "y", 0.0)) if offset else 0.0
            offset_z = float(getattr(offset, "z", 0.0)) if offset else 0.0
            channels: Dict[int, List[Tuple[float, float, int]]] = {}
            frame_arrays = getattr(node, "BezierFramesTypes", None) or []
            for idx, frame_array in enumerate(frame_arrays):
                values = getattr(frame_array, "Values", None) or []
                if not values:
                    continue
                keys = []
                for entry in values:
                    try:
                        time = float(getattr(entry, "time", 0.0))
                        value = float(getattr(entry, "value", 0.0))
                        keytype = int(getattr(entry, "keytype", 0))
                    except Exception:
                        continue
                    keys.append((time, value, keytype))
                if keys:
                    keys.sort(key=lambda item: item[0])
                    channels[idx] = keys
            result[node_name] = DofControlPoint(
                name=node_name,
                offset=(offset_x, offset_y, offset_z),
                channels=channels,
            )
        return result
    return {}


def extract_source_nodes(
    bundle_path: str,
    anim_name: str,
    names: Optional[List[str]] = None,
) -> Dict[str, DofAnimNode]:
    unitypy_mod = _ensure_unitypy()
    if unitypy_mod is None:
        return {}
    try:
        env = unitypy_mod.load(str(bundle_path))
    except Exception:
        return {}
    target_name = anim_name
    if not target_name.lower().endswith(".animbbb"):
        target_name = f"{target_name}.ANIMBBB"
    wanted = {name for name in (names or []) if name}
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            data = obj.read()
        except Exception:
            continue
        name = getattr(data, "m_Name", "") or ""
        if name != target_name and not name.endswith(target_name):
            continue
        result: Dict[str, DofAnimNode] = {}
        for node in getattr(data, "Nodes", None) or []:
            try:
                node_type = int(getattr(node, "NodeType", -1))
            except Exception:
                node_type = -1
            if node_type != 1:
                continue
            node_name = getattr(node, "Name", "") or "Layer"
            if wanted and node_name not in wanted:
                continue
            offset = getattr(node, "OffsetPos", None)
            offset_x = float(getattr(offset, "x", 0.0)) if offset else 0.0
            offset_y = float(getattr(offset, "y", 0.0)) if offset else 0.0
            offset_z = float(getattr(offset, "z", 0.0)) if offset else 0.0
            try:
                image_scale = float(getattr(node, "ImageScale", 1.0) or 1.0)
            except Exception:
                image_scale = 1.0
            channels: Dict[int, List[Tuple[float, float, int]]] = {}
            frame_arrays = getattr(node, "BezierFramesTypes", None) or []
            for idx, frame_array in enumerate(frame_arrays):
                values = getattr(frame_array, "Values", None) or []
                if not values:
                    continue
                keys = []
                for entry in values:
                    try:
                        time = float(getattr(entry, "time", 0.0))
                        value = float(getattr(entry, "value", 0.0))
                        keytype = int(getattr(entry, "keytype", 0))
                    except Exception:
                        continue
                    keys.append((time, value, keytype))
                if keys:
                    keys.sort(key=lambda item: item[0])
                    channels[idx] = keys
            result[node_name] = DofAnimNode(
                name=node_name,
                offset=(offset_x, offset_y, offset_z),
                image_scale=image_scale,
                channels=channels,
            )
        return result
    return {}


def build_particle_library(particles_root: str) -> DofParticleLibrary:
    library = DofParticleLibrary()
    unitypy_mod = _ensure_unitypy()
    if unitypy_mod is None or Image is None:
        return library
    try:
        bundle_paths = list(Path(particles_root).rglob("__data"))
    except Exception:
        bundle_paths = []
    if not bundle_paths:
        return library
    for bundle_path in bundle_paths:
        try:
            env = unitypy_mod.load(str(bundle_path))
        except Exception:
            continue
        for obj in env.objects:
            obj_type = obj.type.name
            if obj_type == "Material":
                try:
                    mat = obj.read()
                except Exception:
                    continue
                name = getattr(mat, "m_Name", "") or ""
                shader = getattr(mat, "m_Shader", None)
                shader_obj = shader.read() if shader else None
                shader_name = _shader_display_name(shader_obj)
                props = getattr(mat, "m_SavedProperties", None)
                colors: Dict[str, Tuple[float, float, float, float]] = {}
                floats: Dict[str, float] = {}
                texture_path: Optional[int] = None
                if props:
                    for entry in getattr(props, "m_Colors", []) or []:
                        try:
                            key, color = entry
                        except Exception:
                            continue
                        if not isinstance(key, str):
                            continue
                        colors[key] = (
                            float(getattr(color, "r", 1.0)),
                            float(getattr(color, "g", 1.0)),
                            float(getattr(color, "b", 1.0)),
                            float(getattr(color, "a", 1.0)),
                        )
                    for entry in getattr(props, "m_Floats", []) or []:
                        try:
                            key, value = entry
                        except Exception:
                            continue
                        if not isinstance(key, str):
                            continue
                        try:
                            floats[key] = float(value)
                        except Exception:
                            continue
                    for entry in getattr(props, "m_TexEnvs", []) or []:
                        try:
                            key, tex_env = entry
                        except Exception:
                            continue
                        if key != "_MainTex":
                            continue
                        texture_ptr = getattr(tex_env, "m_Texture", None)
                        if texture_ptr is not None:
                            try:
                                texture_path = int(getattr(texture_ptr, "m_PathID", 0) or 0)
                            except Exception:
                                texture_path = None
                library.materials[obj.path_id] = DofParticleMaterial(
                    path_id=obj.path_id,
                    name=name or f"Material_{obj.path_id}",
                    shader_name=shader_name,
                    colors=colors,
                    floats=floats,
                    texture_path_id=texture_path if texture_path else None,
                )
            elif obj_type == "Texture2D":
                try:
                    tex = obj.read()
                    name = getattr(tex, "m_Name", "") or f"Texture_{obj.path_id}"
                    image = tex.image
                    if image.mode != "RGBA":
                        image = image.convert("RGBA")
                except Exception:
                    continue
                library.textures[obj.path_id] = DofParticleTexture(
                    path_id=obj.path_id,
                    name=name,
                    image=image,
                )
            elif obj_type == "GameObject":
                try:
                    go = obj.read()
                except Exception:
                    continue
                material_path: Optional[int] = None
                transform_pos = (0.0, 0.0, 0.0)
                transform_rot = (0.0, 0.0, 0.0, 1.0)
                simulation_space = 0
                custom_space_pos = (0.0, 0.0, 0.0)
                custom_space_rot = (0.0, 0.0, 0.0, 1.0)
                shape_position = (0.0, 0.0, 0.0)
                shape_rotation = (0.0, 0.0, 0.0)
                shape_scale = (1.0, 1.0, 1.0)
                start_size: float = 1.0
                start_size_y: Optional[float] = None
                start_size_range = (1.0, 1.0)
                start_size_y_range: Optional[Tuple[float, float]] = None
                start_speed_range = (0.0, 0.0)
                start_rotation_range = (0.0, 0.0)
                start_lifetime_range = (0.0, 0.0)
                start_color_range = (
                    (1.0, 1.0, 1.0, 1.0),
                    (1.0, 1.0, 1.0, 1.0),
                )
                color_over_lifetime_keys: List[Tuple[float, Tuple[float, float, float]]] = []
                alpha_over_lifetime_keys: List[Tuple[float, float]] = []
                size_over_lifetime_keys: List[Tuple[float, float]] = []
                size_over_lifetime_y_keys: List[Tuple[float, float]] = []
                rotation_over_lifetime_keys: List[Tuple[float, float]] = []
                rotation_over_lifetime_range: Tuple[float, float] = (0.0, 0.0)
                move_with_transform = True
                velocity_range = ((0.0, 0.0), (0.0, 0.0))
                velocity_in_world = False
                velocity_module_enabled = False
                emitter_velocity_mode = 0
                gravity_modifier_range = (0.0, 0.0)
                emission_rate_range = (0.0, 0.0)
                emission_distance_range = (0.0, 0.0)
                shape_type = 0
                shape_placement_mode = 0
                shape_radius = 0.0
                shape_radius_thickness = 1.0
                shape_angle = 0.0
                shape_length = 0.0
                shape_box = (0.0, 0.0)
                for comp in getattr(go, "m_Component", []) or []:
                    comp_ptr = getattr(comp, "component", None) or getattr(comp, "m_Component", None)
                    if not comp_ptr:
                        continue
                    try:
                        comp_data = comp_ptr.read()
                    except Exception:
                        continue
                    if comp_ptr.type.name == "Transform":
                        pos = getattr(comp_data, "m_LocalPosition", None)
                        rot = getattr(comp_data, "m_LocalRotation", None)
                        if pos is not None:
                            transform_pos = (
                                float(getattr(pos, "x", 0.0) or 0.0),
                                float(getattr(pos, "y", 0.0) or 0.0),
                                float(getattr(pos, "z", 0.0) or 0.0),
                            )
                        if rot is not None:
                            transform_rot = (
                                float(getattr(rot, "x", 0.0) or 0.0),
                                float(getattr(rot, "y", 0.0) or 0.0),
                                float(getattr(rot, "z", 0.0) or 0.0),
                                float(getattr(rot, "w", 1.0) or 1.0),
                            )
                        continue
                    if comp_ptr.type.name == "ParticleSystemRenderer":
                        mats = getattr(comp_data, "m_Materials", []) or []
                        for mat_ptr in mats:
                            try:
                                mat_path = int(getattr(mat_ptr, "m_PathID", 0) or 0)
                            except Exception:
                                continue
                            if mat_path:
                                material_path = mat_path
                                break
                    elif comp_ptr.type.name == "ParticleSystem":
                        for attr_name in ("simulationSpace", "m_SimulationSpace", "SimulationSpace"):
                            try:
                                sim_val = getattr(comp_data, attr_name)
                            except Exception:
                                sim_val = None
                            if sim_val is None:
                                continue
                            try:
                                simulation_space = int(sim_val)
                            except Exception:
                                pass
                            break
                        try:
                            custom_ptr = getattr(comp_data, "m_CustomSimulationSpace", None)
                        except Exception:
                            custom_ptr = None
                        if custom_ptr and getattr(custom_ptr, "m_PathID", 0):
                            try:
                                custom_obj = custom_ptr.read()
                            except Exception:
                                custom_obj = None
                            if custom_obj and getattr(custom_ptr, "type", None) and custom_ptr.type.name == "Transform":
                                pos = getattr(custom_obj, "m_LocalPosition", None)
                                rot = getattr(custom_obj, "m_LocalRotation", None)
                                if pos is not None:
                                    custom_space_pos = (
                                        float(getattr(pos, "x", 0.0) or 0.0),
                                        float(getattr(pos, "y", 0.0) or 0.0),
                                        float(getattr(pos, "z", 0.0) or 0.0),
                                    )
                                if rot is not None:
                                    custom_space_rot = (
                                        float(getattr(rot, "x", 0.0) or 0.0),
                                        float(getattr(rot, "y", 0.0) or 0.0),
                                        float(getattr(rot, "z", 0.0) or 0.0),
                                        float(getattr(rot, "w", 1.0) or 1.0),
                                    )
                        try:
                            init = getattr(comp_data, "InitialModule", None)
                            if init:
                                start_size_range = _curve_range(getattr(init, "startSize", None), start_size)
                                start_size = (start_size_range[0] + start_size_range[1]) * 0.5
                                if getattr(init, "size3D", False):
                                    start_size_y_range = _curve_range(getattr(init, "startSizeY", None), start_size)
                                    start_size_y = (start_size_y_range[0] + start_size_y_range[1]) * 0.5
                                start_speed_range = _curve_range(getattr(init, "startSpeed", None), 0.0)
                                start_rotation_range = _curve_range(getattr(init, "startRotation", None), 0.0)
                                start_lifetime_range = _curve_range(getattr(init, "startLifetime", None), 0.0)
                                start_color_range = _gradient_range(getattr(init, "startColor", None))
                                gravity_modifier_range = _curve_range(
                                    getattr(init, "gravityModifier", None),
                                    0.0,
                                )
                        except Exception:
                            continue
                        try:
                            color_module = getattr(comp_data, "ColorModule", None)
                            if color_module and getattr(color_module, "enabled", False):
                                gradient = _select_gradient(getattr(color_module, "gradient", None))
                                if gradient is not None:
                                    color_over_lifetime_keys, alpha_over_lifetime_keys = _extract_gradient_keys(gradient)
                        except Exception:
                            pass
                        try:
                            emission = getattr(comp_data, "EmissionModule", None)
                            if emission and getattr(emission, "enabled", True):
                                emission_rate_range = _curve_range(
                                    getattr(emission, "rateOverTime", None),
                                    0.0,
                                )
                                emission_distance_range = _curve_range(
                                    getattr(emission, "rateOverDistance", None),
                                    0.0,
                                )
                        except Exception:
                            pass
                        try:
                            move_with_transform = bool(getattr(comp_data, "moveWithTransform", move_with_transform))
                        except Exception:
                            pass
                        try:
                            for attr_name in ("emitterVelocityMode", "m_EmitterVelocityMode"):
                                if hasattr(comp_data, attr_name):
                                    emitter_velocity_mode = int(getattr(comp_data, attr_name) or 0)
                                    break
                        except Exception:
                            pass
                        try:
                            velocity = getattr(comp_data, "VelocityModule", None)
                            if velocity and getattr(velocity, "enabled", False):
                                velocity_module_enabled = True
                                velocity_range = (
                                    _curve_range(getattr(velocity, "x", None), 0.0),
                                    _curve_range(getattr(velocity, "y", None), 0.0),
                                )
                                velocity_in_world = bool(getattr(velocity, "inWorldSpace", False))
                        except Exception:
                            pass
                        try:
                            shape = getattr(comp_data, "ShapeModule", None)
                            if shape and getattr(shape, "enabled", True):
                                shape_type = int(getattr(shape, "type", 0) or 0)
                                shape_placement_mode = int(getattr(shape, "placementMode", 0) or 0)
                                radius_param = getattr(shape, "radius", None)
                                shape_radius = float(getattr(radius_param, "value", 0.0) or 0.0)
                                shape_radius_thickness = float(
                                    getattr(shape, "radiusThickness", 1.0) or 1.0
                                )
                                shape_angle = float(getattr(shape, "angle", 0.0) or 0.0)
                                shape_length = float(getattr(shape, "length", 0.0) or 0.0)
                                shape_box = (
                                    float(getattr(shape, "boxX", 0.0) or 0.0),
                                    float(getattr(shape, "boxY", 0.0) or 0.0),
                                )
                                pos = getattr(shape, "m_Position", None)
                                rot = getattr(shape, "m_Rotation", None)
                                scl = getattr(shape, "m_Scale", None)
                                if pos is not None:
                                    shape_position = (
                                        float(getattr(pos, "x", 0.0) or 0.0),
                                        float(getattr(pos, "y", 0.0) or 0.0),
                                        float(getattr(pos, "z", 0.0) or 0.0),
                                    )
                                if rot is not None:
                                    shape_rotation = (
                                        float(getattr(rot, "x", 0.0) or 0.0),
                                        float(getattr(rot, "y", 0.0) or 0.0),
                                        float(getattr(rot, "z", 0.0) or 0.0),
                                    )
                                if scl is not None:
                                    shape_scale = (
                                        float(getattr(scl, "x", 1.0) or 1.0),
                                        float(getattr(scl, "y", 1.0) or 1.0),
                                        float(getattr(scl, "z", 1.0) or 1.0),
                                    )
                        except Exception:
                            pass
                        try:
                            size_module = getattr(comp_data, "SizeModule", None)
                            if size_module and getattr(size_module, "enabled", False):
                                separate_axes = bool(getattr(size_module, "separateAxes", False))
                                if separate_axes:
                                    size_curve = getattr(size_module, "x", None)
                                    if size_curve is None:
                                        size_curve = getattr(size_module, "curve", None)
                                    if size_curve is None:
                                        size_curve = getattr(size_module, "size", None)
                                else:
                                    size_curve = getattr(size_module, "curve", None)
                                    if size_curve is None:
                                        size_curve = getattr(size_module, "x", None)
                                    if size_curve is None:
                                        size_curve = getattr(size_module, "size", None)
                                    if size_curve is None:
                                        size_curve = getattr(size_module, "y", None)
                                if size_curve is not None:
                                    size_over_lifetime_keys = _extract_curve_keys(size_curve)
                                if separate_axes:
                                    size_y_curve = getattr(size_module, "y", None)
                                    if size_y_curve is not None:
                                        size_over_lifetime_y_keys = _extract_curve_keys(size_y_curve)
                        except Exception:
                            pass
                        try:
                            rotation_module = getattr(comp_data, "RotationModule", None)
                            if rotation_module and getattr(rotation_module, "enabled", False):
                                rot_curve = getattr(rotation_module, "z", None)
                                if rot_curve is None:
                                    rot_curve = getattr(rotation_module, "rotation", None)
                                if rot_curve is not None:
                                    rotation_over_lifetime_keys = _extract_curve_keys(rot_curve)
                                    rotation_over_lifetime_range = _curve_range(rot_curve, 0.0)
                        except Exception:
                            pass
                library.prefabs[obj.path_id] = DofParticlePrefab(
                    path_id=obj.path_id,
                    name=getattr(go, "m_Name", "") or f"Prefab_{obj.path_id}",
                    material_path_id=material_path,
                    transform_pos=transform_pos,
                    transform_rot=transform_rot,
                    simulation_space=simulation_space,
                    custom_space_pos=custom_space_pos,
                    custom_space_rot=custom_space_rot,
                    shape_position=shape_position,
                    shape_rotation=shape_rotation,
                    shape_scale=shape_scale,
                    start_size=start_size,
                    start_size_y=start_size_y,
                    start_size_range=start_size_range,
                    start_size_y_range=start_size_y_range,
                    start_speed_range=start_speed_range,
                    start_rotation_range=start_rotation_range,
                    start_lifetime_range=start_lifetime_range,
                    start_color_range=start_color_range,
                    color_over_lifetime_keys=color_over_lifetime_keys,
                    alpha_over_lifetime_keys=alpha_over_lifetime_keys,
                    size_over_lifetime_keys=size_over_lifetime_keys,
                    size_over_lifetime_y_keys=size_over_lifetime_y_keys,
                    rotation_over_lifetime_keys=rotation_over_lifetime_keys,
                    rotation_over_lifetime_range=rotation_over_lifetime_range,
                    move_with_transform=move_with_transform,
                    velocity_over_lifetime_range=velocity_range,
                    velocity_in_world_space=velocity_in_world,
                    velocity_module_enabled=velocity_module_enabled,
                    emitter_velocity_mode=emitter_velocity_mode,
                    gravity_modifier_range=gravity_modifier_range,
                    emission_rate_range=emission_rate_range,
                    emission_distance_range=emission_distance_range,
                    shape_type=shape_type,
                    shape_placement_mode=shape_placement_mode,
                    shape_radius=shape_radius,
                    shape_radius_thickness=shape_radius_thickness,
                    shape_angle=shape_angle,
                    shape_length=shape_length,
                    shape_box=shape_box,
                )
    return library
