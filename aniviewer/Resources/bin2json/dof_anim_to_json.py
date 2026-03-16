"""
DOF ANIMBBB -> MSM Animation Viewer JSON converter.

Converts a Down of the Fare .ANIMBBB.asset into the viewer's JSON format
and generates a TexturePacker-style XML + atlas PNG.
"""

from __future__ import annotations

import argparse
import os
import json
import math
import re
import struct
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
import xml.etree.ElementTree as ET

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover - CLI error path
    yaml = None

try:
    from PIL import Image, ImageChops
except Exception as exc:  # pragma: no cover - CLI error path
    Image = None  # type: ignore
    ImageChops = None  # type: ignore

try:
    import UnityPy  # type: ignore
except Exception:
    UnityPy = None


class UnityYamlLoader(yaml.SafeLoader if yaml else object):
    pass


def _construct_undefined(loader: UnityYamlLoader, node: Any) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


if yaml:
    UnityYamlLoader.add_constructor(None, _construct_undefined)

_GUID_MAP_CACHE: Dict[Path, Dict[str, Path]] = {}
_SHADER_NAME_CACHE: Dict[str, Optional[str]] = {}
_GUID_DISK_CACHE_NAME = ".dof_guid_cache.json"
_LAST_SPRITE_DEFS: Dict[str, Dict[str, Any]] = {}
_LAST_LAYER_DEBUG: List[Dict[str, Any]] = []
_LAST_ALPHA_DEBUG: Dict[str, Any] = {}


def _load_yaml(path: Path, text: Optional[str] = None) -> Dict[str, Any]:
    if not yaml:
        raise RuntimeError("PyYAML is required for DOF conversion.")
    if text is None:
        text = path.read_text(encoding="utf-8")
    data = yaml.load(text, Loader=UnityYamlLoader)
    return data or {}


def _find_assets_root(path: Path) -> Optional[Path]:
    for parent in [path] + list(path.parents):
        if parent.name.lower() == "assets":
            return parent
    return None


def _strip_suffix(name: str, suffix: str) -> str:
    if name.lower().endswith(suffix.lower()):
        return name[: -len(suffix)]
    return name


def _anim_name_from_path(path: Path) -> str:
    name = path.name
    name = _strip_suffix(name, ".asset")
    name = _strip_suffix(name, ".ANIMBBB")
    return name


def _read_guid_from_meta(meta_path: Path) -> Optional[str]:
    try:
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("guid:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def _guid_cache_path(folder: Path) -> Path:
    return folder / _GUID_DISK_CACHE_NAME


def _load_guid_cache(folder: Path) -> Optional[Dict[str, Path]]:
    cache_path = _guid_cache_path(folder)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    guid_map_raw = payload.get("guid_map")
    if not isinstance(guid_map_raw, dict):
        return None
    guid_map: Dict[str, Path] = {}
    for guid, rel_path in guid_map_raw.items():
        if not guid or not isinstance(rel_path, str):
            continue
        guid_map[guid] = (folder / rel_path).resolve()
    return guid_map


def _save_guid_cache(folder: Path, guid_map: Dict[str, Path]) -> None:
    cache_path = _guid_cache_path(folder)
    payload: Dict[str, Any] = {"root": str(folder), "guid_map": {}}
    for guid, asset_path in guid_map.items():
        try:
            rel_path = asset_path.relative_to(folder)
        except ValueError:
            rel_path = asset_path
        payload["guid_map"][guid] = str(rel_path)
    try:
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        return


def _build_guid_map(
    folder: Path,
    *,
    disk_cache: bool = False,
    refresh_cache: bool = False,
) -> Dict[str, Path]:
    folder = folder.resolve()
    cached = _GUID_MAP_CACHE.get(folder)
    if cached is not None and not refresh_cache:
        return cached
    if disk_cache and not refresh_cache:
        cached = _load_guid_cache(folder)
        if cached is not None:
            _GUID_MAP_CACHE[folder] = cached
            return cached
    guid_map: Dict[str, Path] = {}
    if disk_cache:
        print(f"Building GUID cache for {folder} (first run may take a while)...")
        start_time = time.perf_counter()
    for meta_path in folder.rglob("*.meta"):
        guid = _read_guid_from_meta(meta_path)
        if not guid:
            continue
        asset_path = meta_path.with_suffix("")
        guid_map[guid] = asset_path
    _GUID_MAP_CACHE[folder] = guid_map
    if disk_cache:
        _save_guid_cache(folder, guid_map)
        elapsed = time.perf_counter() - start_time
        print(f"GUID cache ready ({len(guid_map)} entries) in {elapsed:.1f}s.")
    return guid_map


def _find_tpp_asset(sprites_dir: Path) -> Optional[Path]:
    candidates = sorted(sprites_dir.glob("*.TPP.asset"))
    if not candidates:
        candidates = sorted(sprites_dir.glob("*.tpp.asset"))
    return candidates[0] if candidates else None


def _parse_tpp_asset(tpp_path: Path) -> Tuple[Optional[str], Optional[str], List[str]]:
    data = _load_yaml(tpp_path)
    mono = data.get("MonoBehaviour", {})
    tex_guid = None
    alpha_guid = None
    sprites: List[str] = []
    tex_entry = mono.get("texture", {})
    if isinstance(tex_entry, dict):
        tex_guid = tex_entry.get("guid")
    alpha_entry = mono.get("alpha", {})
    if isinstance(alpha_entry, dict):
        alpha_guid = alpha_entry.get("guid")
    for sprite_entry in mono.get("sprites", []) or []:
        if isinstance(sprite_entry, dict):
            guid = sprite_entry.get("guid")
            if guid:
                sprites.append(guid)
    return tex_guid, alpha_guid, sprites


def _parse_sprite_asset(
    sprite_asset_path: Path,
    atlas_size: Optional[Tuple[int, int]] = None,
    flip_y: bool = False,
    atlas_flip_y: bool = False,
    mesh_use_offset: bool = True,
) -> Dict[str, Any]:
    raw_text = sprite_asset_path.read_text(encoding="utf-8")
    data = _load_yaml(sprite_asset_path, raw_text)
    sprite = data.get("Sprite", {})
    rect = sprite.get("m_Rect", {}) or {}
    render_data = sprite.get("m_RD", {}) or {}
    texture_rect = render_data.get("textureRect", {}) or rect
    texture_rect_offset = render_data.get("textureRectOffset", {}) or {}
    pivot = sprite.get("m_Pivot", {}) or {}
    offset = sprite.get("m_Offset", {}) or {}
    vertex_data = render_data.get("m_VertexData", {}) or {}
    try:
        vertex_count = int(vertex_data.get("m_VertexCount", 0) or 0)
    except (TypeError, ValueError):
        vertex_count = 0
    index_hex = render_data.get("m_IndexBuffer")
    if not isinstance(index_hex, str) or not index_hex.strip():
        index_hex = _extract_index_buffer_hex(raw_text)
    has_index_buffer = isinstance(index_hex, str) and bool(index_hex.strip())
    mesh_hint = vertex_count > 4 or (has_index_buffer and vertex_count not in (0, 4))
    try:
        rect_w = float(texture_rect.get("width", rect.get("width", 0)))
        rect_h = float(texture_rect.get("height", rect.get("height", 0)))
    except (TypeError, ValueError):
        rect_w = 0.0
        rect_h = 0.0
    trim_offset_x = 0.0
    trim_offset_y = 0.0
    try:
        offset_x = float(offset.get("x", 0.0) or 0.0)
        offset_y = float(offset.get("y", 0.0) or 0.0)
    except (TypeError, ValueError):
        offset_x = 0.0
        offset_y = 0.0
    try:
        original_w = float(rect.get("width", rect_w))
        original_h = float(rect.get("height", rect_h))
    except (TypeError, ValueError):
        original_w = rect_w
        original_h = rect_h
    pivot_x = float(pivot.get("x", 0.5))
    pivot_y = float(pivot.get("y", 0.5))
    mesh_vertices: List[Tuple[float, float]] = []
    mesh_uv: List[Tuple[float, float]] = []
    mesh_triangles: List[int] = []
    mesh_data = _extract_mesh_data(
        sprite,
        render_data,
        raw_text,
        atlas_size,
        flip_y,
        atlas_flip_y,
        mesh_use_offset,
    )
    if mesh_data:
        mesh_vertices, mesh_uv, mesh_triangles = mesh_data
    mesh_bounds = _compute_mesh_bounds_from_vertices(mesh_vertices)
    if mesh_bounds is None:
        mesh_bounds = _extract_mesh_bounds(sprite, render_data, mesh_use_offset)
    # Determine whether mesh vertices are pivot-local or rect-local.
    mesh_anchor_zero = False
    if mesh_bounds and rect_w > 0 and rect_h > 0:
        tol = 0.75
        rect_min_x, rect_min_y = 0.0, 0.0
        rect_max_x, rect_max_y = rect_w, rect_h
        pivot_min_x = -pivot_x * rect_w
        pivot_min_y = -pivot_y * rect_h
        pivot_max_x = (1.0 - pivot_x) * rect_w
        pivot_max_y = (1.0 - pivot_y) * rect_h
        rect_local = (
            abs(mesh_bounds[0] - rect_min_x) <= tol
            and abs(mesh_bounds[1] - rect_min_y) <= tol
            and abs(mesh_bounds[2] - rect_max_x) <= tol
            and abs(mesh_bounds[3] - rect_max_y) <= tol
        )
        pivot_local = (
            abs(mesh_bounds[0] - pivot_min_x) <= tol
            and abs(mesh_bounds[1] - pivot_min_y) <= tol
            and abs(mesh_bounds[2] - pivot_max_x) <= tol
            and abs(mesh_bounds[3] - pivot_max_y) <= tol
        )
        if pivot_local and not rect_local:
            mesh_anchor_zero = True
    if texture_rect_offset:
        try:
            trim_offset_x = float(texture_rect_offset.get("x", 0.0) or 0.0)
            trim_offset_y = float(texture_rect_offset.get("y", 0.0) or 0.0)
        except (TypeError, ValueError):
            trim_offset_x = 0.0
            trim_offset_y = 0.0
    else:
        try:
            rect_x = float(rect.get("x", 0.0) or 0.0)
            rect_y = float(rect.get("y", 0.0) or 0.0)
            tex_x = float(texture_rect.get("x", rect_x) or 0.0)
            tex_y = float(texture_rect.get("y", rect_y) or 0.0)
            trim_offset_x = rect_x - tex_x
            trim_offset_y = rect_y - tex_y
        except (TypeError, ValueError):
            trim_offset_x = 0.0
            trim_offset_y = 0.0
    pivot_outside = (
        pivot_x < -1e-4
        or pivot_x > 1.0 + 1e-4
        or pivot_y < -1e-4
        or pivot_y > 1.0 + 1e-4
    )
    return {
        "name": sprite.get("m_Name", sprite_asset_path.stem),
        "rect": {
            "x": float(texture_rect.get("x", rect.get("x", 0))),
            "y": float(texture_rect.get("y", rect.get("y", 0))),
            "w": rect_w,
            "h": rect_h,
        },
        "pivot": {
            "x": pivot_x,
            "y": pivot_y,
        },
        "offset": {
            "x": offset_x,
            "y": offset_y,
        },
        "trim": {
            "x": trim_offset_x,
            "y": trim_offset_y,
            "w": original_w,
            "h": original_h,
        },
        "pivot_outside": pivot_outside,
        "mesh_vertices": mesh_vertices,
        "mesh_uv": mesh_uv,
        "mesh_triangles": mesh_triangles,
        "mesh_bounds": mesh_bounds,
        "mesh_anchor_zero": mesh_anchor_zero,
        "mesh_vertex_count": vertex_count,
        "mesh_has_index_buffer": has_index_buffer,
        "mesh_hint": mesh_hint,
    }


def _extract_mesh_data_unitypy(
    sprite_obj: Any,
    render_data: Any,
    atlas_size: Optional[Tuple[int, int]],
    flip_y: bool,
    atlas_flip_y: bool,
    use_offset: bool = True,
) -> Optional[Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[int]]]:
    vertex_data = getattr(render_data, "m_VertexData", None)
    if vertex_data is None:
        return None
    raw = getattr(vertex_data, "m_DataSize", None)
    if not raw:
        return None
    if isinstance(raw, list):
        raw = bytes(raw)
    elif isinstance(raw, memoryview):
        raw = raw.tobytes()
    try:
        vertex_count = int(getattr(vertex_data, "m_VertexCount", 0) or 0)
    except (TypeError, ValueError):
        return None
    if vertex_count <= 0:
        return None
    channels = getattr(vertex_data, "m_Channels", None) or []
    stream0 = []
    for channel in channels:
        try:
            stream = int(getattr(channel, "stream", 0) or 0)
            dim = int(getattr(channel, "dimension", 0) or 0)
        except (TypeError, ValueError):
            continue
        if stream == 0 and dim > 0:
            stream0.append(channel)
    if not stream0:
        return None
    format_sizes = {
        0: 4,  # float
        1: 2,  # float16
        2: 1,  # UNorm8
        3: 1,  # SNorm8
        4: 2,  # UNorm16
        5: 2,  # SNorm16
        6: 1,  # UInt8
        7: 1,  # SInt8
        8: 2,  # UInt16
        9: 2,  # SInt16
        10: 4,  # UInt32
        11: 4,  # SInt32
    }
    stride = 0
    pos_channel: Optional[Tuple[int, int, int]] = None
    for channel in stream0:
        try:
            offset = int(getattr(channel, "offset", 0) or 0)
            fmt = int(getattr(channel, "format", 0) or 0)
            dim = int(getattr(channel, "dimension", 0) or 0)
        except (TypeError, ValueError):
            continue
        bytes_per = format_sizes.get(fmt)
        if bytes_per is None:
            return None
        stride = max(stride, offset + bytes_per * dim)
        if dim == 3 and pos_channel is None:
            pos_channel = (offset, fmt, dim)
    if not pos_channel or stride <= 0:
        return None
    pos_offset, pos_format, pos_dim = pos_channel
    if pos_format != 0 or pos_dim != 3:
        return None
    needed = vertex_count * stride
    if len(raw) < needed:
        return None

    try:
        ppu = float(getattr(sprite_obj, "m_PixelsToUnits", 100) or 100)
    except (TypeError, ValueError):
        ppu = 100.0
    offset_x = 0.0
    offset_y = 0.0
    if use_offset:
        offset_info = getattr(sprite_obj, "m_Offset", None)
        try:
            offset_x = float(getattr(offset_info, "x", 0.0) or 0.0)
            offset_y = float(getattr(offset_info, "y", 0.0) or 0.0)
        except (TypeError, ValueError):
            offset_x = 0.0
            offset_y = 0.0

    positions_raw: List[Tuple[float, float]] = []
    for idx in range(vertex_count):
        base = idx * stride + pos_offset
        x, y, _ = struct.unpack_from("<3f", raw, base)
        positions_raw.append((x * ppu, y * ppu))
    if not positions_raw:
        return None

    rect = getattr(sprite_obj, "m_Rect", None)
    rect_x = float(getattr(rect, "x", 0.0) or 0.0)
    rect_y = float(getattr(rect, "y", 0.0) or 0.0)
    rect_w = float(getattr(rect, "width", 0.0) or 0.0)
    rect_h = float(getattr(rect, "height", 0.0) or 0.0)

    positions: List[Tuple[float, float]] = []
    for vx, vy in positions_raw:
        vx += offset_x
        vy += offset_y
        if flip_y:
            vy = -vy
        positions.append((vx, vy))

    index_raw = getattr(render_data, "m_IndexBuffer", None)
    if not index_raw:
        return None
    if isinstance(index_raw, list):
        index_raw = bytes(index_raw)
    triangles = [
        int.from_bytes(index_raw[i : i + 2], byteorder="little", signed=False)
        for i in range(0, len(index_raw), 2)
    ]

    mesh_uv: List[Tuple[float, float]] = []
    if atlas_size and rect_w > 0 and rect_h > 0 and positions:
        atlas_h = float(atlas_size[1])
        rect_y_tex = rect_y
        if atlas_flip_y:
            rect_y_tex = atlas_h - rect_y - rect_h
        min_x = min(vx for vx, _ in positions)
        min_y = min(vy for _, vy in positions)
        for vx, vy in positions:
            rect_space_x = vx - min_x
            rect_space_y = vy - min_y
            uv_x = rect_x + rect_space_x
            uv_y = rect_y_tex + rect_space_y
            mesh_uv.append((uv_x, uv_y))

    return positions, mesh_uv, triangles


def _parse_sprite_unitypy(
    sprite_obj: Any,
    atlas_size: Optional[Tuple[int, int]] = None,
    flip_y: bool = False,
    atlas_flip_y: bool = False,
    mesh_use_offset: bool = True,
) -> Dict[str, Any]:
    rect = getattr(sprite_obj, "m_Rect", None)
    render_data = getattr(sprite_obj, "m_RD", None)
    pivot = getattr(sprite_obj, "m_Pivot", None)
    offset = getattr(sprite_obj, "m_Offset", None)
    try:
        rect_w = float(getattr(render_data.textureRect, "width", getattr(rect, "width", 0)))
        rect_h = float(getattr(render_data.textureRect, "height", getattr(rect, "height", 0)))
    except Exception:
        rect_w = rect_h = 0.0
    trim_offset_x = 0.0
    trim_offset_y = 0.0
    try:
        offset_x = float(getattr(offset, "x", 0.0) or 0.0)
        offset_y = float(getattr(offset, "y", 0.0) or 0.0)
    except (TypeError, ValueError):
        offset_x = 0.0
        offset_y = 0.0
    try:
        original_w = float(getattr(rect, "width", rect_w) or rect_w)
        original_h = float(getattr(rect, "height", rect_h) or rect_h)
    except (TypeError, ValueError):
        original_w = rect_w
        original_h = rect_h
    mesh_vertices: List[Tuple[float, float]] = []
    mesh_uv: List[Tuple[float, float]] = []
    mesh_triangles: List[int] = []
    if render_data is not None:
        mesh_data = _extract_mesh_data_unitypy(
            sprite_obj, render_data, atlas_size, flip_y, atlas_flip_y, mesh_use_offset
        )
        if mesh_data:
            mesh_vertices, mesh_uv, mesh_triangles = mesh_data
    mesh_bounds = _compute_mesh_bounds_from_vertices(mesh_vertices)
    pivot_x_raw = getattr(pivot, "x", None)
    pivot_y_raw = getattr(pivot, "y", None)
    try:
        pivot_x = float(pivot_x_raw) if pivot_x_raw is not None else 0.5
    except (TypeError, ValueError):
        pivot_x = 0.5
    try:
        pivot_y = float(pivot_y_raw) if pivot_y_raw is not None else 0.5
    except (TypeError, ValueError):
        pivot_y = 0.5
    # Determine whether mesh vertices are pivot-local or rect-local.
    mesh_anchor_zero = False
    if mesh_bounds and rect_w > 0 and rect_h > 0:
        tol = 0.75
        rect_min_x, rect_min_y = 0.0, 0.0
        rect_max_x, rect_max_y = rect_w, rect_h
        pivot_min_x = -pivot_x * rect_w
        pivot_min_y = -pivot_y * rect_h
        pivot_max_x = (1.0 - pivot_x) * rect_w
        pivot_max_y = (1.0 - pivot_y) * rect_h
        rect_local = (
            abs(mesh_bounds[0] - rect_min_x) <= tol
            and abs(mesh_bounds[1] - rect_min_y) <= tol
            and abs(mesh_bounds[2] - rect_max_x) <= tol
            and abs(mesh_bounds[3] - rect_max_y) <= tol
        )
        pivot_local = (
            abs(mesh_bounds[0] - pivot_min_x) <= tol
            and abs(mesh_bounds[1] - pivot_min_y) <= tol
            and abs(mesh_bounds[2] - pivot_max_x) <= tol
            and abs(mesh_bounds[3] - pivot_max_y) <= tol
        )
        if pivot_local and not rect_local:
            mesh_anchor_zero = True
    texture_rect_offset = getattr(render_data, "textureRectOffset", None)
    if texture_rect_offset is not None:
        try:
            trim_offset_x = float(getattr(texture_rect_offset, "x", 0.0) or 0.0)
            trim_offset_y = float(getattr(texture_rect_offset, "y", 0.0) or 0.0)
        except (TypeError, ValueError):
            trim_offset_x = 0.0
            trim_offset_y = 0.0
    else:
        try:
            rect_x = float(getattr(rect, "x", 0.0) or 0.0)
            rect_y = float(getattr(rect, "y", 0.0) or 0.0)
            tex_x = float(getattr(render_data.textureRect, "x", rect_x) or 0.0)
            tex_y = float(getattr(render_data.textureRect, "y", rect_y) or 0.0)
            trim_offset_x = rect_x - tex_x
            trim_offset_y = rect_y - tex_y
        except (TypeError, ValueError):
            trim_offset_x = 0.0
            trim_offset_y = 0.0
    # pivot_x/pivot_y already defined above
    pivot_outside = (
        pivot_x < -1e-4
        or pivot_x > 1.0 + 1e-4
        or pivot_y < -1e-4
        or pivot_y > 1.0 + 1e-4
    )
    rect_x = float(getattr(render_data.textureRect, "x", getattr(rect, "x", 0)))
    rect_y = float(getattr(render_data.textureRect, "y", getattr(rect, "y", 0)))
    return {
        "name": getattr(sprite_obj, "m_Name", "Sprite"),
        "rect": {"x": rect_x, "y": rect_y, "w": rect_w, "h": rect_h},
        "pivot": {"x": pivot_x, "y": pivot_y},
        "offset": {"x": offset_x, "y": offset_y},
        "trim": {"x": trim_offset_x, "y": trim_offset_y, "w": original_w, "h": original_h},
        "pivot_outside": pivot_outside,
        "mesh_vertices": mesh_vertices,
        "mesh_uv": mesh_uv,
        "mesh_triangles": mesh_triangles,
        "mesh_bounds": mesh_bounds,
        "mesh_anchor_zero": mesh_anchor_zero,
        "mesh_vertex_count": int(getattr(render_data.m_VertexData, "m_VertexCount", 0) or 0)
        if render_data and getattr(render_data, "m_VertexData", None)
        else 0,
        "mesh_has_index_buffer": bool(getattr(render_data, "m_IndexBuffer", None)) if render_data else False,
        "mesh_hint": False,
    }


def _extract_mesh_data(
    sprite: Dict[str, Any],
    render_data: Dict[str, Any],
    raw_text: str,
    atlas_size: Optional[Tuple[int, int]],
    flip_y: bool,
    atlas_flip_y: bool,
    use_offset: bool = True,
) -> Optional[Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[int]]]:
    vertex_data = render_data.get("m_VertexData", {}) or {}
    raw_hex = vertex_data.get("_typelessdata")
    if not raw_hex or not isinstance(raw_hex, str):
        return None
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError:
        return None
    try:
        vertex_count = int(vertex_data.get("m_VertexCount", 0) or 0)
    except (TypeError, ValueError):
        return None
    if vertex_count <= 0:
        return None
    channels = vertex_data.get("m_Channels", []) or []
    stream0 = []
    for channel in channels:
        try:
            stream = int(channel.get("stream", 0) or 0)
            dim = int(channel.get("dimension", 0) or 0)
        except (TypeError, ValueError):
            continue
        if stream == 0 and dim > 0:
            stream0.append(channel)
    if not stream0:
        return None
    format_sizes = {
        0: 4,  # float
        1: 2,  # float16
        2: 1,  # UNorm8
        3: 1,  # SNorm8
        4: 2,  # UNorm16
        5: 2,  # SNorm16
        6: 1,  # UInt8
        7: 1,  # SInt8
        8: 2,  # UInt16
        9: 2,  # SInt16
        10: 4,  # UInt32
        11: 4,  # SInt32
    }
    stride = 0
    pos_channel: Optional[Tuple[int, int, int]] = None
    for channel in stream0:
        try:
            offset = int(channel.get("offset", 0) or 0)
            fmt = int(channel.get("format", 0) or 0)
            dim = int(channel.get("dimension", 0) or 0)
        except (TypeError, ValueError):
            continue
        bytes_per = format_sizes.get(fmt)
        if bytes_per is None:
            return None
        stride = max(stride, offset + bytes_per * dim)
        if dim == 3 and pos_channel is None:
            pos_channel = (offset, fmt, dim)
    if not pos_channel or stride <= 0:
        return None
    pos_offset, pos_format, pos_dim = pos_channel
    if pos_format != 0 or pos_dim != 3:
        return None
    needed = vertex_count * stride
    if len(raw) < needed:
        return None

    try:
        ppu = float(sprite.get("m_PixelsToUnits", 100) or 100)
    except (TypeError, ValueError):
        ppu = 100.0
    offset_x = 0.0
    offset_y = 0.0
    if use_offset:
        offset_info = sprite.get("m_Offset", {}) or {}
        try:
            offset_x = float(offset_info.get("x", 0.0) or 0.0)
            offset_y = float(offset_info.get("y", 0.0) or 0.0)
        except (TypeError, ValueError):
            offset_x = 0.0
            offset_y = 0.0
    positions_raw: List[Tuple[float, float]] = []
    for idx in range(vertex_count):
        base = idx * stride + pos_offset
        x, y, _ = struct.unpack_from("<3f", raw, base)
        positions_raw.append((x * ppu, y * ppu))
    if not positions_raw:
        return None

    rect = sprite.get("m_Rect", {}) or {}
    try:
        rect_x = float(rect.get("x", 0.0))
        rect_y = float(rect.get("y", 0.0))
        rect_w = float(rect.get("width", 0.0))
        rect_h = float(rect.get("height", 0.0))
    except (TypeError, ValueError):
        rect_x = rect_y = rect_w = rect_h = 0.0

    positions: List[Tuple[float, float]] = []
    for vx, vy in positions_raw:
        vx += offset_x
        vy += offset_y
        if flip_y:
            vy = -vy
        positions.append((vx, vy))

    index_hex = render_data.get("m_IndexBuffer")
    if not index_hex or not isinstance(index_hex, str):
        index_hex = _extract_index_buffer_hex(raw_text)
    if not index_hex or not isinstance(index_hex, str):
        return None
    try:
        index_raw = bytes.fromhex(index_hex)
    except ValueError:
        return None
    if len(index_raw) < 6:
        return None
    triangles = [
        int.from_bytes(index_raw[i : i + 2], byteorder="little", signed=False)
        for i in range(0, len(index_raw), 2)
    ]

    mesh_uv: List[Tuple[float, float]] = []
    if atlas_size and rect_w > 0 and rect_h > 0 and positions:
        atlas_h = float(atlas_size[1])
        rect_y_tex = rect_y
        if atlas_flip_y:
            rect_y_tex = atlas_h - rect_y - rect_h
        min_x = min(vx for vx, _ in positions)
        min_y = min(vy for _, vy in positions)
        for vx, vy in positions:
            rect_space_x = vx - min_x
            rect_space_y = vy - min_y
            uv_x = rect_x + rect_space_x
            uv_y = rect_y_tex + rect_space_y
            mesh_uv.append((uv_x, uv_y))

    return positions, mesh_uv, triangles


def _extract_index_buffer_hex(raw_text: str) -> Optional[str]:
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("m_IndexBuffer:"):
            value = stripped.split(":", 1)[1].strip()
            if value:
                return value
            return None
    return None


def _extract_mesh_bounds(
    sprite: Dict[str, Any],
    render_data: Dict[str, Any],
    use_offset: bool = True,
) -> Optional[Tuple[float, float, float, float]]:
    vertex_data = render_data.get("m_VertexData", {}) or {}
    raw_hex = vertex_data.get("_typelessdata")
    if not raw_hex or not isinstance(raw_hex, str):
        return None
    try:
        raw = bytes.fromhex(raw_hex)
    except ValueError:
        return None
    try:
        vertex_count = int(vertex_data.get("m_VertexCount", 0) or 0)
    except (TypeError, ValueError):
        return None
    if vertex_count <= 0:
        return None
    channels = vertex_data.get("m_Channels", []) or []
    stream0 = []
    for channel in channels:
        try:
            stream = int(channel.get("stream", 0) or 0)
            dim = int(channel.get("dimension", 0) or 0)
        except (TypeError, ValueError):
            continue
        if stream == 0 and dim > 0:
            stream0.append(channel)
    if not stream0:
        return None
    format_sizes = {
        0: 4,  # float
        1: 2,  # float16
        2: 1,  # UNorm8
        3: 1,  # SNorm8
        4: 2,  # UNorm16
        5: 2,  # SNorm16
        6: 1,  # UInt8
        7: 1,  # SInt8
        8: 2,  # UInt16
        9: 2,  # SInt16
        10: 4,  # UInt32
        11: 4,  # SInt32
    }
    stride = 0
    pos_channel: Optional[Tuple[int, int, int]] = None
    for channel in stream0:
        try:
            offset = int(channel.get("offset", 0) or 0)
            fmt = int(channel.get("format", 0) or 0)
            dim = int(channel.get("dimension", 0) or 0)
        except (TypeError, ValueError):
            continue
        bytes_per = format_sizes.get(fmt)
        if bytes_per is None:
            return None
        stride = max(stride, offset + bytes_per * dim)
        if dim == 3 and pos_channel is None:
            pos_channel = (offset, fmt, dim)
    if not pos_channel or stride <= 0:
        return None
    pos_offset, pos_format, pos_dim = pos_channel
    if pos_format != 0 or pos_dim != 3:
        return None
    needed = vertex_count * stride
    if len(raw) < needed:
        return None
    positions: List[Tuple[float, float]] = []
    for idx in range(vertex_count):
        base = idx * stride + pos_offset
        x, y, _ = struct.unpack_from("<3f", raw, base)
        positions.append((x, y))
    if not positions:
        return None
    try:
        ppu = float(sprite.get("m_PixelsToUnits", 100) or 100)
    except (TypeError, ValueError):
        ppu = 100.0
    offset_x = 0.0
    offset_y = 0.0
    if use_offset:
        offset_info = sprite.get("m_Offset", {}) or {}
        try:
            offset_x = float(offset_info.get("x", 0.0) or 0.0)
            offset_y = float(offset_info.get("y", 0.0) or 0.0)
        except (TypeError, ValueError):
            offset_x = 0.0
            offset_y = 0.0
    rect = sprite.get("m_Rect", {}) or {}
    try:
        rect_w = float(rect.get("width", 0.0))
        rect_h = float(rect.get("height", 0.0))
    except (TypeError, ValueError):
        rect_w = rect_h = 0.0

    positions_px = [(pos[0] * ppu + offset_x, pos[1] * ppu + offset_y) for pos in positions]
    min_x = min(vx for vx, _ in positions_px)
    max_x = max(vx for vx, _ in positions_px)
    min_y = min(vy for _, vy in positions_px)
    max_y = max(vy for _, vy in positions_px)
    return min_x, min_y, max_x, max_y


def _compute_mesh_bounds_from_vertices(
    vertices: List[Tuple[float, float]]
) -> Optional[Tuple[float, float, float, float]]:
    if not vertices:
        return None
    min_x = min(vx for vx, _ in vertices)
    max_x = max(vx for vx, _ in vertices)
    min_y = min(vy for _, vy in vertices)
    max_y = max(vy for _, vy in vertices)
    return min_x, min_y, max_x, max_y


def _compute_sprite_anchor_with_mode(
    sprite_info: Dict[str, Any],
    flip_y: bool,
    center_anchor: bool,
    flip_x: bool,
    use_offset_anchor: bool,
) -> Tuple[float, float, float, float]:
    """
    Compute the anchor point for a sprite using either trim/pivot math or
    Unity m_Offset + pivot math for swap-frame alignment.
    """
    mesh_anchor_zero = bool(sprite_info.get("mesh_anchor_zero"))
    rect = sprite_info["rect"]
    pivot = sprite_info["pivot"]
    trim = sprite_info.get("trim", {})
    offset_anchor_mode = sprite_info.get("offset_anchor_mode")

    if offset_anchor_mode == "pivot_offset" and not center_anchor:
        try:
            offset = sprite_info.get("offset", {}) or {}
            offset_x = float(offset.get("x", 0.0) or 0.0)
            offset_y = float(offset.get("y", 0.0) or 0.0)
        except (TypeError, ValueError):
            offset_x = 0.0
            offset_y = 0.0
        anchor_x = rect["w"] * float(pivot.get("x", 0.0)) - offset_x
        anchor_y = rect["h"] * float(pivot.get("y", 0.0)) - offset_y
        if flip_y:
            anchor_y = -anchor_y
        return anchor_x, anchor_y, 0.0, 0.0

    if mesh_anchor_zero and not center_anchor:
        return 0.0, 0.0, 0.0, 0.0

    pivot_x = pivot["x"]
    pivot_y = pivot["y"]
    pivot_y_flipped = 1.0 - pivot_y if flip_y else pivot_y
    offset = sprite_info.get("offset", {}) or {}

    if center_anchor:
        if mesh_anchor_zero:
            trim_w = float(trim.get("w", rect["w"]))
            trim_h = float(trim.get("h", rect["h"]))
            anchor_x = float(trim.get("x", 0.0)) + trim_w * 0.5
            anchor_y = float(trim.get("y", 0.0)) + trim_h * 0.5
            center_offset_x = -anchor_x
            center_offset_y = -anchor_y
        else:
            anchor_x = float(trim.get("x", 0.0)) + rect["w"] * 0.5
            anchor_y = float(trim.get("y", 0.0)) + rect["h"] * 0.5
            center_offset_x = rect["w"] * (pivot_x - 0.5)
            center_offset_y = rect["h"] * (pivot_y_flipped - 0.5)
    else:
        mesh_bounds = sprite_info.get("mesh_bounds")
        mesh_vertices = sprite_info.get("mesh_vertices") or []
        if mesh_bounds and mesh_vertices and not use_offset_anchor:
            # Anchor at the pivot position within the mesh's own local bounds.
            # This aligns sprites whose mesh vertices are rect-local but offset
            # from the rect origin (common in DOF bundles).
            mesh_min_x = float(mesh_bounds[0])
            mesh_min_y = float(mesh_bounds[1])
            anchor_x = mesh_min_x + rect["w"] * pivot_x
            anchor_y = mesh_min_y + rect["h"] * pivot_y_flipped
        elif use_offset_anchor:
            base_x = float(offset.get("x", 0.0) or 0.0)
            base_y = float(offset.get("y", 0.0) or 0.0)
            anchor_x = base_x + rect["w"] * pivot_x
            anchor_y = base_y + rect["h"] * pivot_y
            if flip_y:
                anchor_y = -anchor_y
        else:
            origin_w = float(trim.get("w", rect["w"]))
            origin_h = float(trim.get("h", rect["h"]))
            trim_x = float(trim.get("x", 0.0))
            trim_y = float(trim.get("y", 0.0))
            anchor_x = pivot_x * origin_w - trim_x
            anchor_y = pivot_y_flipped * origin_h - trim_y
        center_offset_x = 0.0
        center_offset_y = 0.0

    if flip_x and not use_offset_anchor and not mesh_anchor_zero:
        origin_w = float(trim.get("w", 0.0) or rect["w"])
        anchor_x = origin_w - anchor_x
        center_offset_x = -center_offset_x

    return anchor_x, anchor_y, center_offset_x, center_offset_y


def _compute_sprite_anchor(
    sprite_info: Dict[str, Any],
    flip_y: bool,
    center_anchor: bool,
    flip_x: bool,
) -> Tuple[float, float, float, float]:
    use_offset_anchor = bool(sprite_info.get("use_offset_anchor"))
    return _compute_sprite_anchor_with_mode(
        sprite_info,
        flip_y,
        center_anchor,
        flip_x,
        use_offset_anchor,
    )


def _compute_offset_anchor(
    sprite_info: Dict[str, Any],
    flip_y: bool,
) -> Tuple[float, float]:
    """Return anchor from Sprite.m_Offset (viewer coords)."""
    offset = sprite_info.get("offset", {}) or {}
    try:
        anchor_x = float(offset.get("x", 0.0) or 0.0)
        anchor_y = float(offset.get("y", 0.0) or 0.0)
    except (TypeError, ValueError):
        anchor_x = 0.0
        anchor_y = 0.0
    if flip_y:
        anchor_y = -anchor_y
    return anchor_x, anchor_y



def _choose_swap_anchor_mode(
    default_sprite_name: str,
    sprite_names: Iterable[str],
    sprite_defs: Dict[str, Dict[str, Any]],
    flip_y: bool,
    center_anchor: bool,
    flip_x: bool,
    improvement_threshold: float = 1.0,
) -> bool:
    """
    Decide whether sprite swaps should use m_Offset + pivot anchors.

    Returns True to use offset-based anchors, False for trim/pivot anchors.
    """
    if center_anchor:
        return False
    names = [name for name in sprite_names if name]
    if len(names) < 2:
        return False

    def gather(use_offset_anchor: bool) -> Dict[str, Tuple[float, float]]:
        anchors: Dict[str, Tuple[float, float]] = {}
        for name in names:
            info = sprite_defs.get(name)
            if not info:
                continue
            anchor_x, anchor_y, _, _ = _compute_sprite_anchor_with_mode(
                info,
                flip_y,
                center_anchor,
                flip_x,
                use_offset_anchor,
            )
            if not (math.isfinite(anchor_x) and math.isfinite(anchor_y)):
                continue
            anchors[name] = (float(anchor_x), float(anchor_y))
        return anchors

    pivot_anchors = gather(False)
    offset_anchors = gather(True)
    if len(pivot_anchors) < 2 or len(offset_anchors) < 2:
        return False

    default_anchor_pivot = pivot_anchors.get(default_sprite_name)
    default_anchor_offset = offset_anchors.get(default_sprite_name)
    if not default_anchor_pivot or not default_anchor_offset:
        return False

    def score(anchors: Dict[str, Tuple[float, float]], default_anchor: Tuple[float, float]) -> Optional[float]:
        values = list(anchors.values())
        if len(values) < 2:
            return None
        return sum(
            math.hypot(anchor[0] - default_anchor[0], anchor[1] - default_anchor[1])
            for anchor in values
        ) / len(values)

    pivot_score = score(pivot_anchors, default_anchor_pivot)
    offset_score = score(offset_anchors, default_anchor_offset)
    if pivot_score is None or offset_score is None:
        return False

    if pivot_score - offset_score >= improvement_threshold:
        return True
    return False


def _is_mesh_sprite(sprite_info: Dict[str, Any]) -> bool:
    """
    Check if a sprite should be treated as a mesh sprite for anchor handling.

    For DOF, sprites with explicit vertex + triangle data are treated as
    origin-baked (we zero anchors and rely on mesh vertices directly).
    """
    mesh_vertices = sprite_info.get("mesh_vertices") or []
    mesh_triangles = sprite_info.get("mesh_triangles") or []
    if mesh_vertices and mesh_triangles:
        return True
    vertex_count = int(sprite_info.get("mesh_vertex_count", 0) or 0)
    return vertex_count > 4


def _compute_anchor_span(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return max(values) - min(values)


def _select_best_anchor_span(
    span_map: Dict[str, Optional[float]],
    order: List[str],
) -> Tuple[str, Optional[float]]:
    best_key = ""
    best_span: Optional[float] = None
    for key in order:
        span = span_map.get(key)
        if span is None:
            continue
        if best_span is None or span < best_span - 1e-9:
            best_span = span
            best_key = key
    return best_key, best_span


def _compute_sprite_bounds_for_alignment(
    sprite_info: Dict[str, Any],
    flip_y: bool,
) -> Optional[Tuple[float, float, float, float]]:
    vertices = sprite_info.get("mesh_vertices") or []
    if vertices:
        min_x = min(vx for vx, _ in vertices)
        max_x = max(vx for vx, _ in vertices)
        min_y = min(vy for _, vy in vertices)
        max_y = max(vy for _, vy in vertices)
        return min_x, min_y, max_x, max_y

    rect = sprite_info.get("rect", {}) or {}
    pivot = sprite_info.get("pivot", {}) or {}
    try:
        rect_w = float(rect.get("w", 0.0) or 0.0)
        rect_h = float(rect.get("h", 0.0) or 0.0)
    except (TypeError, ValueError):
        rect_w = 0.0
        rect_h = 0.0
    if rect_w <= 0.0 or rect_h <= 0.0:
        return None
    try:
        pivot_x = float(pivot.get("x", 0.5))
        pivot_y = float(pivot.get("y", 0.5))
    except (TypeError, ValueError):
        pivot_x = 0.5
        pivot_y = 0.5
    pivot_y_eff = 1.0 - pivot_y if flip_y else pivot_y
    min_x = -pivot_x * rect_w
    max_x = (1.0 - pivot_x) * rect_w
    min_y = -pivot_y_eff * rect_h
    max_y = (1.0 - pivot_y_eff) * rect_h
    return min_x, min_y, max_x, max_y


def _compute_swap_edge_alignment(
    sprite_names: Iterable[str],
    sprite_defs: Dict[str, Dict[str, Any]],
    flip_y: bool,
    default_sprite_name: str,
    forced_x: Optional[str] = None,
    forced_y: Optional[str] = None,
) -> Optional[Tuple[float, float, Dict[str, List[float]]]]:
    anchors: Dict[str, Dict[str, float]] = {}
    for name in sprite_names:
        if not name:
            continue
        info = sprite_defs.get(name)
        if not info:
            continue
        bounds = _compute_sprite_bounds_for_alignment(info, flip_y)
        if not bounds:
            continue
        min_x, min_y, max_x, max_y = bounds
        anchors[name] = {
            "left": float(min_x),
            "center": float((min_x + max_x) * 0.5),
            "right": float(max_x),
            "bottom": float(min_y),
            "center_y": float((min_y + max_y) * 0.5),
            "top": float(max_y),
        }

    if len(anchors) < 2:
        return None

    x_spans = {
        "left": _compute_anchor_span([entry["left"] for entry in anchors.values()]),
        "center": _compute_anchor_span([entry["center"] for entry in anchors.values()]),
        "right": _compute_anchor_span([entry["right"] for entry in anchors.values()]),
    }
    y_spans = {
        "bottom": _compute_anchor_span([entry["bottom"] for entry in anchors.values()]),
        "center": _compute_anchor_span([entry["center_y"] for entry in anchors.values()]),
        "top": _compute_anchor_span([entry["top"] for entry in anchors.values()]),
    }
    best_x = None
    best_y = None
    if forced_x in x_spans:
        best_x = forced_x
    if forced_y in y_spans:
        best_y = forced_y
    if not best_x:
        best_x, _ = _select_best_anchor_span(x_spans, ["left", "center", "right"])
    if not best_y:
        best_y, _ = _select_best_anchor_span(y_spans, ["bottom", "center", "top"])
    if not best_x or not best_y:
        return None

    anchor_map: Dict[str, List[float]] = {}
    for name, entry in anchors.items():
        anchor_map[name] = [entry[best_x], entry["center_y" if best_y == "center" else best_y]]

    default_entry = anchors.get(default_sprite_name)
    if default_entry:
        anchor_x = default_entry[best_x]
        anchor_y = default_entry["center_y" if best_y == "center" else best_y]
    else:
        first = next(iter(anchor_map.values()))
        anchor_x = first[0]
        anchor_y = first[1]

    return float(anchor_x), float(anchor_y), anchor_map


def _compute_swap_pivot_offset_map(
    sprite_names: Iterable[str],
    sprite_defs: Dict[str, Dict[str, Any]],
    flip_y: bool,
    default_sprite_name: str,
    anchor_x: float,
    anchor_y: float,
    mesh_use_offset: bool,
) -> Optional[Dict[str, List[float]]]:
    if not mesh_use_offset:
        return None
    pivot_map: Dict[str, Tuple[float, float]] = {}
    for name in sprite_names:
        if not name:
            continue
        info = sprite_defs.get(name)
        if not info:
            continue
        if not _is_mesh_sprite(info):
            continue
        offset = info.get("offset", {}) or {}
        try:
            offset_x = float(offset.get("x", 0.0) or 0.0)
            offset_y = float(offset.get("y", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if flip_y:
            offset_y = -offset_y
        pivot_map[name] = (offset_x, offset_y)
    if len(pivot_map) < 2:
        return None
    default_pivot = pivot_map.get(default_sprite_name)
    if default_pivot is None:
        default_pivot = next(iter(pivot_map.values()))
    delta_x = anchor_x - default_pivot[0]
    delta_y = anchor_y - default_pivot[1]
    anchor_map: Dict[str, List[float]] = {}
    for name, (ox, oy) in pivot_map.items():
        anchor_map[name] = [float(ox + delta_x), float(oy + delta_y)]
    return anchor_map


def _compute_swap_pivot_center_delta_map(
    sprite_names: Iterable[str],
    sprite_defs: Dict[str, Dict[str, Any]],
    flip_y: bool,
    default_sprite_name: str,
) -> Optional[Dict[str, List[float]]]:
    centers: Dict[str, Tuple[float, float]] = {}
    for name in sprite_names:
        if not name:
            continue
        info = sprite_defs.get(name)
        if not info:
            continue
        rect = info.get("rect", {}) or {}
        pivot = info.get("pivot", {}) or {}
        try:
            rect_w = float(rect.get("w", 0.0) or 0.0)
            rect_h = float(rect.get("h", 0.0) or 0.0)
        except (TypeError, ValueError):
            rect_w = rect_h = 0.0
        if rect_w <= 0.0 or rect_h <= 0.0:
            continue
        try:
            pivot_x = float(pivot.get("x", 0.5))
            pivot_y = float(pivot.get("y", 0.5))
        except (TypeError, ValueError):
            continue
        pivot_y_eff = 1.0 - pivot_y if flip_y else pivot_y
        center_x = (pivot_x - 0.5) * rect_w
        center_y = (pivot_y_eff - 0.5) * rect_h
        centers[name] = (center_x, center_y)
    if len(centers) < 2:
        return None
    base_center = centers.get(default_sprite_name)
    if base_center is None:
        base_center = next(iter(centers.values()))
    base_x, base_y = base_center
    anchor_map: Dict[str, List[float]] = {}
    for name, (cx, cy) in centers.items():
        anchor_map[name] = [float(cx - base_x), float(cy - base_y)]
    return anchor_map


def _anchor_map_is_zero(anchor_map: Optional[Dict[str, List[float]]]) -> bool:
    if not anchor_map:
        return True
    for value in anchor_map.values():
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            continue
        try:
            if abs(float(value[0])) > 1e-6 or abs(float(value[1])) > 1e-6:
                return False
        except (TypeError, ValueError):
            continue
    return True


def _resolve_swap_anchor_report_path(
    output_dir: Path,
    anim_name: str,
    report_path: Optional[Path],
) -> Path:
    if report_path:
        if report_path.is_absolute():
            return report_path
        return output_dir / report_path
    return output_dir / f"{anim_name}_swap_anchor_report.json"


def _build_swap_anchor_report(
    anim_name: str,
    nodes: List[Dict[str, Any]],
    node_mappings_raw: List[str],
    image_names: List[str],
    sprite_defs: Dict[str, Dict[str, Any]],
    flip_y: bool,
) -> Dict[str, Any]:
    report_nodes: List[Dict[str, Any]] = []
    summary_x = {"left": 0, "center": 0, "right": 0}
    summary_y = {"bottom": 0, "center": 0, "top": 0}

    for node_index, node in enumerate(nodes):
        if node.get("NodeType") != 1:
            continue
        channels = node.get("BezierFramesTypes", []) or []
        sprite_channel = channels[12] if len(channels) > 12 else {}
        sprite_keys = _channel_key_map(sprite_channel)
        if not sprite_keys:
            continue
        mapping_hex = node_mappings_raw[node_index] if node_index < len(node_mappings_raw) else ""
        mapping = _resolve_node_mapping(mapping_hex, node)
        sprite_names: set[str] = set()
        for sprite_index, _ in sprite_keys.values():
            sprite_name = _resolve_sprite_name(int(round(sprite_index)), mapping, image_names)
            if sprite_name:
                sprite_names.add(sprite_name)
        if len(sprite_names) < 2:
            continue

        sprite_records: List[Dict[str, Any]] = []
        missing: List[str] = []
        x_left_vals: List[float] = []
        x_center_vals: List[float] = []
        x_right_vals: List[float] = []
        y_bottom_vals: List[float] = []
        y_center_vals: List[float] = []
        y_top_vals: List[float] = []

        for sprite_name in sorted(sprite_names):
            sprite_info = sprite_defs.get(sprite_name)
            if not sprite_info:
                missing.append(sprite_name)
                continue
            rect = sprite_info.get("rect", {}) or {}
            pivot = sprite_info.get("pivot", {}) or {}
            try:
                rect_w = float(rect.get("w", 0.0) or 0.0)
                rect_h = float(rect.get("h", 0.0) or 0.0)
            except (TypeError, ValueError):
                rect_w = 0.0
                rect_h = 0.0
            if rect_w <= 0.0 or rect_h <= 0.0:
                missing.append(sprite_name)
                continue
            try:
                pivot_x = float(pivot.get("x", 0.5))
                pivot_y = float(pivot.get("y", 0.5))
            except (TypeError, ValueError):
                pivot_x = 0.5
                pivot_y = 0.5
            pivot_y_eff = 1.0 - pivot_y if flip_y else pivot_y

            left_x = -pivot_x * rect_w
            center_x = (0.5 - pivot_x) * rect_w
            right_x = (1.0 - pivot_x) * rect_w

            bottom_y = -pivot_y_eff * rect_h
            center_y = (0.5 - pivot_y_eff) * rect_h
            top_y = (1.0 - pivot_y_eff) * rect_h

            x_left_vals.append(left_x)
            x_center_vals.append(center_x)
            x_right_vals.append(right_x)
            y_bottom_vals.append(bottom_y)
            y_center_vals.append(center_y)
            y_top_vals.append(top_y)

            sprite_records.append(
                {
                    "sprite": sprite_name,
                    "rect": {"w": rect_w, "h": rect_h},
                    "pivot": {"x": pivot_x, "y": pivot_y, "y_eff": pivot_y_eff},
                    "x": {"left": left_x, "center": center_x, "right": right_x},
                    "y": {"bottom": bottom_y, "center": center_y, "top": top_y},
                    "mesh_anchor_zero": bool(sprite_info.get("mesh_anchor_zero")),
                }
            )

        if len(sprite_records) < 2:
            continue

        x_spans = {
            "left": _compute_anchor_span(x_left_vals),
            "center": _compute_anchor_span(x_center_vals),
            "right": _compute_anchor_span(x_right_vals),
        }
        y_spans = {
            "bottom": _compute_anchor_span(y_bottom_vals),
            "center": _compute_anchor_span(y_center_vals),
            "top": _compute_anchor_span(y_top_vals),
        }
        best_x, best_x_span = _select_best_anchor_span(x_spans, ["left", "center", "right"])
        best_y, best_y_span = _select_best_anchor_span(y_spans, ["bottom", "center", "top"])
        if best_x:
            summary_x[best_x] += 1
        if best_y:
            summary_y[best_y] += 1

        report_nodes.append(
            {
                "node_index": node_index,
                "node_name": node.get("Name", f"Layer_{node_index}"),
                "sprite_names": sorted(sprite_names),
                "missing_sprites": missing,
                "sprite_count": len(sprite_names),
                "valid_sprite_count": len(sprite_records),
                "x_spans": x_spans,
                "y_spans": y_spans,
                "best_x": {"mode": best_x, "span": best_x_span},
                "best_y": {"mode": best_y, "span": best_y_span},
                "sprites": sprite_records,
            }
        )

    return {
        "anim_name": anim_name,
        "flip_y": bool(flip_y),
        "swap_nodes": len(report_nodes),
        "summary_x": summary_x,
        "summary_y": summary_y,
        "nodes": report_nodes,
    }


def _extract_node_mappings(raw_text: str) -> List[str]:
    mappings: List[str] = []
    node_index = -1
    for line in raw_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- NodeType:"):
            node_index += 1
            mappings.append("")
            continue
        if stripped.startswith("ImageIndexLocal2Global:"):
            value = stripped.split(":", 1)[1].strip()
            if node_index >= 0 and node_index < len(mappings):
                mappings[node_index] = value
    return mappings


def _parse_hex_mapping(hex_str: str) -> Optional[List[int]]:
    if not hex_str:
        return None
    if len(hex_str) % 8 != 0:
        return None
    mapping: List[int] = []
    for i in range(0, len(hex_str), 8):
        chunk = hex_str[i : i + 8]
        try:
            raw = bytes.fromhex(chunk)
        except ValueError:
            return None
        value = int.from_bytes(raw, byteorder="little", signed=False)
        mapping.append(value)
    return mapping


def _iter_keyframes(channel: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    return channel.get("Values", []) or []


def _extract_shader_name(shader_path: Path) -> Optional[str]:
    try:
        text = shader_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r'\bShader\s+"([^"]+)"', text)
    if match:
        return match.group(1).strip()
    return None


def _resolve_shader_name(
    shader_entry: Any,
    resolve_guid: Callable[[Optional[str]], Optional[Path]],
) -> Optional[str]:
    if not isinstance(shader_entry, dict):
        return None
    guid = shader_entry.get("guid")
    if not guid:
        return None
    cached = _SHADER_NAME_CACHE.get(guid)
    if cached is not None:
        return cached
    shader_path = resolve_guid(guid)
    if not shader_path or not shader_path.exists():
        _SHADER_NAME_CACHE[guid] = None
        return None
    shader_name = _extract_shader_name(shader_path)
    if not shader_name:
        shader_name = shader_path.stem
    _SHADER_NAME_CACHE[guid] = shader_name
    return shader_name


def _infer_blend_from_shader(shader_name: Optional[str]) -> int:
    if not shader_name:
        return 0
    lowered = shader_name.lower()
    if "additive" in lowered:
        return 2
    if "multiply" in lowered:
        return 6
    if "screen" in lowered:
        return 7
    return 0


def _shader_ref_key(shader_entry: Any) -> Optional[str]:
    if not isinstance(shader_entry, dict):
        return None
    guid = shader_entry.get("guid")
    if isinstance(guid, str) and guid:
        return f"guid:{guid.lower()}"

    file_id = shader_entry.get("m_FileID")
    if file_id is None:
        file_id = shader_entry.get("fileID")
    if file_id is None:
        file_id = shader_entry.get("file_id")
    path_id = shader_entry.get("m_PathID")
    if path_id is None:
        path_id = shader_entry.get("pathID")
    if path_id is None:
        path_id = shader_entry.get("path_id")

    if file_id is None and path_id is None:
        return None
    return f"pptr:{file_id}:{path_id}"


def _layer_name_looks_additive(layer_name: Optional[str]) -> bool:
    if not layer_name:
        return False
    lowered = layer_name.strip().lower()
    if not lowered:
        return False
    if lowered.startswith("sprite_light"):
        return True
    tokens = re.split(r"[^a-z0-9]+", lowered)
    if not tokens:
        return False
    token_set = set(tokens)
    if "light" in token_set or "lights" in token_set:
        return True
    additive_tokens = {
        "glow",
        "glows",
        "glint",
        "glints",
        "spark",
        "sparks",
        "flare",
        "flares",
        "emissive",
        "emission",
        "bloom",
        "shine",
        "shiny",
    }
    return any(token in additive_tokens for token in token_set)


def _collect_additive_shader_refs(nodes: List[Dict[str, Any]]) -> set[str]:
    grouped_names: Dict[str, List[str]] = {}
    for node in nodes:
        if node.get("NodeType") != 1:
            continue
        shader_key = _shader_ref_key(node.get("RenderTypeShader"))
        if not shader_key:
            continue
        grouped_names.setdefault(shader_key, []).append(
            str(node.get("Name", "") or "")
        )

    additive_refs: set[str] = set()
    for shader_key, layer_names in grouped_names.items():
        if not layer_names:
            continue
        if all(_layer_name_looks_additive(name) for name in layer_names):
            additive_refs.add(shader_key)
    return additive_refs


def _infer_layer_blend(
    shader_name: Optional[str],
    layer_name: Optional[str],
    shader_entry: Any,
    additive_shader_refs: set[str],
) -> int:
    blend = _infer_blend_from_shader(shader_name)
    if blend != 0:
        return blend

    shader_key = _shader_ref_key(shader_entry)
    if shader_key and shader_key in additive_shader_refs:
        return 2

    # Fallback for bundles where shader refs are stripped/missing but light layers
    # are still named consistently (e.g. Sprite_light_*).
    if _layer_name_looks_additive(layer_name):
        return 2

    return 0


def _score_alpha_alignment(
    alpha_channel: Image.Image,
    base_img: Image.Image,
    sprites: Optional[List[Dict[str, Any]]],
    flip_y: bool,
) -> Tuple[float, float]:
    if not sprites:
        return (0.0, 0.0)
    base_w, base_h = base_img.size
    alpha_w, alpha_h = alpha_channel.size
    if base_w <= 0 or base_h <= 0 or alpha_w <= 0 or alpha_h <= 0:
        return (0.0, 0.0)
    scale_x = alpha_w / base_w
    scale_y = alpha_h / base_h
    base_pixels = base_img.convert("RGB").load()
    alpha_pixels = alpha_channel.load()
    visible_sum = 0.0
    hidden_sum = 0.0
    samples = 0

    for sprite in sprites:
        rect = sprite.get("rect", {}) or {}
        try:
            x = float(rect.get("x", 0.0) or 0.0)
            y = float(rect.get("y", 0.0) or 0.0)
            w = float(rect.get("w", 0.0) or 0.0)
            h = float(rect.get("h", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        # sample a small grid inside the rect
        steps_x = 3
        steps_y = 3
        for iy in range(steps_y):
            for ix in range(steps_x):
                px = int(x + (ix + 0.5) * w / steps_x)
                py = int(y + (iy + 0.5) * h / steps_y)
                if px < 0 or py < 0 or px >= base_w or py >= base_h:
                    continue
                ax = int(px * scale_x)
                ay = int(py * scale_y)
                if flip_y:
                    ay = alpha_h - ay - 1
                if ax < 0 or ay < 0 or ax >= alpha_w or ay >= alpha_h:
                    continue
                r, g, b = base_pixels[px, py]
                lum = (0.2126 * r + 0.7152 * g + 0.0722 * b)
                a = alpha_pixels[ax, ay]
                if lum > 20.0:
                    visible_sum += a
                else:
                    hidden_sum += a
                samples += 1
        if samples >= 120:
            break
    if samples == 0:
        return (0.0, 0.0)
    return (visible_sum / samples, hidden_sum / samples)


def _choose_alpha_variant(
    alpha_channel: Image.Image,
    base_img: Image.Image,
    sprites: Optional[List[Dict[str, Any]]],
) -> Tuple[Image.Image, bool]:
    """
    Auto-align split alpha to the color atlas.

    Some DOF monsters ship alpha atlases at half-res and/or vertically flipped.
    Evaluate a small candidate set (flip/invert/resample/offset) and keep the
    one with the strongest visible-vs-hidden separation over sprite rects.
    """
    base_w, base_h = base_img.size
    candidates: List[Tuple[Image.Image, str]] = []
    if alpha_channel.size == (base_w, base_h):
        candidates.append((alpha_channel, "native"))
    else:
        candidates.append(
            (alpha_channel.resize((base_w, base_h), Image.Resampling.NEAREST), "nearest")
        )
        candidates.append(
            (alpha_channel.resize((base_w, base_h), Image.Resampling.BILINEAR), "bilinear")
        )

    def find_best(allow_invert: bool) -> Tuple[Image.Image, bool, float, float, float]:
        best_channel_local = candidates[0][0]
        best_invert_local = False
        best_score_local = float("-inf")
        best_vis_local = 0.0
        best_hid_local = 0.0
        shift_range = (-2, -1, 0, 1, 2)
        invert_modes = (False, True) if allow_invert else (False,)
        for resized_channel, _method in candidates:
            for flip_y in (False, True):
                variant = (
                    resized_channel.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                    if flip_y
                    else resized_channel
                )
                for invert in invert_modes:
                    test_channel = Image.eval(variant, lambda v: 255 - v) if invert else variant
                    for dx in shift_range:
                        for dy in shift_range:
                            if dx == 0 and dy == 0:
                                shifted = test_channel
                            elif ImageChops is not None:
                                shifted = ImageChops.offset(test_channel, dx, dy)
                            else:
                                shifted = test_channel
                            vis, hid = _score_alpha_alignment(shifted, base_img, sprites, False)
                            score = vis - hid
                            if score > best_score_local or (
                                abs(score - best_score_local) <= 1e-6 and vis > best_vis_local
                            ):
                                best_score_local = score
                                best_vis_local = vis
                                best_hid_local = hid
                                best_channel_local = shifted
                                best_invert_local = invert
        return (
            best_channel_local,
            best_invert_local,
            best_score_local,
            best_vis_local,
            best_hid_local,
        )

    best_channel, best_invert, best_score, best_vis, best_hid = find_best(False)
    # Inversion is a last resort only when non-inverted variants are effectively unusable.
    if best_score < 1.0 and best_vis < 1.0:
        best_channel, best_invert, best_score, best_vis, best_hid = find_best(True)

    return best_channel, best_invert


def _apply_alpha_texture(
    base_img: Image.Image,
    alpha_img: Image.Image,
    sprites: Optional[List[Dict[str, Any]]] = None,
    alpha_flip_x: bool = False,
    alpha_flip_y: bool = False,
    alpha_channel_override: Optional[Any] = None,
    alpha_hardness: float = 0.0,
) -> Image.Image:
    if base_img.mode != "RGBA":
        base_img = base_img.convert("RGBA")
    if alpha_channel_override is not None:
        alpha_channel = alpha_channel_override
    else:
        if alpha_img.mode != "RGBA":
            alpha_img = alpha_img.convert("RGBA")
        alpha_channel = alpha_img.getchannel("A")
        if alpha_flip_x:
            alpha_channel = alpha_channel.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if alpha_flip_y:
            alpha_channel = alpha_channel.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    # DOF split-alpha is authored to overlay the color atlas directly.
    # Keep mapping literal (no flip/invert/offset guessing), but use a
    # high-quality resize for size mismatches so low-res alpha masks do not
    # produce staircase edges after upscaling.
    if alpha_channel.size != base_img.size:
        src_w, src_h = alpha_channel.size
        dst_w, dst_h = base_img.size
        scale_x = float(dst_w) / float(max(1, src_w))
        scale_y = float(dst_h) / float(max(1, src_h))
        upscale = scale_x > 1.001 or scale_y > 1.001
        resample_mode = (
            Image.Resampling.LANCZOS if upscale else Image.Resampling.BILINEAR
        )
        alpha_channel = alpha_channel.resize(base_img.size, resample_mode)
    try:
        hardness_value = float(alpha_hardness)
    except (TypeError, ValueError):
        hardness_value = 0.0
    hardness_value = max(0.0, min(2.0, hardness_value))
    if hardness_value > 1e-6:
        contrast = 1.0 + hardness_value
        midpoint = 127.5
        alpha_channel = alpha_channel.point(
            lambda v: int(max(0.0, min(255.0, round((float(v) - midpoint) * contrast + midpoint))))
        )
    base_img.putalpha(alpha_channel)
    return base_img


def _premultiply_alpha(image: Image.Image) -> Image.Image:
    if ImageChops is None:
        return image
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    r, g, b, a = image.split()
    r = ImageChops.multiply(r, a)
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (r, g, b, a))


def _build_texture(
    texture_path: Path,
    alpha_path: Optional[Path],
    output_path: Path,
    sprites: Optional[List[Dict[str, Any]]] = None,
    alpha_flip_x: bool = False,
    alpha_flip_y: bool = False,
    premultiply_alpha: bool = False,
    alpha_hardness: float = 0.0,
) -> Tuple[int, int]:
    if Image is None:
        raise RuntimeError("Pillow is required for DOF conversion.")
    base_img = Image.open(texture_path).convert("RGBA")
    alpha_applied = False
    base_alpha = base_img.getchannel("A")
    base_min, base_max = base_alpha.getextrema()
    base_has_variation = base_min != base_max
    if alpha_path and alpha_path.exists():
        alpha_img = Image.open(alpha_path)
        if "A" not in alpha_img.getbands():
            alpha_img = alpha_img.convert("RGBA")
        alpha_channel = alpha_img.getchannel("A")
        alpha_min, alpha_max = alpha_channel.getextrema()
        if alpha_max == 0 and alpha_img.mode in ("RGB", "RGBA"):
            fallback = alpha_img.convert("L")
            fmin, fmax = fallback.getextrema()
            if fmax > 0:
                alpha_img = fallback.convert("RGBA")
                alpha_channel = alpha_img.getchannel("A")
                alpha_min, alpha_max = alpha_channel.getextrema()
        if alpha_max > 0:
            base_img = _apply_alpha_texture(
                base_img,
                alpha_img,
                sprites,
                alpha_flip_x=alpha_flip_x,
                alpha_flip_y=alpha_flip_y,
                alpha_hardness=alpha_hardness,
            )
            alpha_applied = True
    if not alpha_applied:
        base_img = base_img.convert("RGBA")
    if premultiply_alpha:
        base_img = _premultiply_alpha(base_img)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_img.save(output_path)
    return base_img.size


def _build_texture_from_unitypy(
    texture_obj: Any,
    alpha_obj: Optional[Any],
    output_path: Path,
    sprites: Optional[List[Dict[str, Any]]] = None,
    alpha_flip_x: bool = False,
    alpha_flip_y: bool = False,
    alpha_channel_override: Optional[Any] = None,
    premultiply_alpha: bool = False,
    alpha_hardness: float = 0.0,
) -> Tuple[int, int]:
    if Image is None:
        raise RuntimeError("Pillow is required for DOF conversion.")
    if texture_obj is None:
        raise RuntimeError("Missing Unity texture object.")
    base_img = texture_obj.image
    if base_img is None:
        raise RuntimeError("Unity texture has no image data.")
    base_img = base_img.convert("RGBA")
    alpha_applied = False
    base_alpha = base_img.getchannel("A")
    base_min, base_max = base_alpha.getextrema()
    base_has_variation = base_min != base_max
    if alpha_channel_override is not None:
        base_img = _apply_alpha_texture(
            base_img,
            base_img,
            sprites,
            alpha_flip_x=False,
            alpha_flip_y=False,
            alpha_channel_override=alpha_channel_override,
            alpha_hardness=alpha_hardness,
        )
        alpha_applied = True
    elif alpha_obj is not None and getattr(alpha_obj, "image", None) is not None:
        alpha_img = alpha_obj.image
        if "A" not in alpha_img.getbands():
            alpha_img = alpha_img.convert("RGBA")
        alpha_channel = alpha_img.getchannel("A")
        alpha_min, alpha_max = alpha_channel.getextrema()
        if alpha_max > 0:
            base_img = _apply_alpha_texture(
                base_img,
                alpha_img,
                sprites,
                alpha_flip_x=alpha_flip_x,
                alpha_flip_y=alpha_flip_y,
                alpha_hardness=alpha_hardness,
            )
            alpha_applied = True
    if not alpha_applied:
        base_img = base_img.convert("RGBA")
    if premultiply_alpha:
        base_img = _premultiply_alpha(base_img)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_img.save(output_path)
    return base_img.size


def _detect_atlas_flip_y(
    image_path: Path,
    sprites: List[Dict[str, Any]],
    sample_limit: int = 12,
) -> bool:
    if Image is None or not image_path.exists():
        return False
    try:
        img = Image.open(image_path).convert("RGBA")
    except Exception:
        return False
    width, height = img.size
    if width <= 0 or height <= 0:
        return False
    alpha = img.getchannel("A")
    total_normal = 0
    total_flipped = 0
    count = 0
    for sprite in sprites:
        rect = sprite.get("rect", {}) or {}
        try:
            x = int(round(float(rect.get("x", 0.0) or 0.0)))
            y = int(round(float(rect.get("y", 0.0) or 0.0)))
            w = int(round(float(rect.get("w", 0.0) or 0.0)))
            h = int(round(float(rect.get("h", 0.0) or 0.0)))
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = min(w, width - x)
        h = min(h, height - y)
        if w <= 0 or h <= 0:
            continue
        region = alpha.crop((x, y, x + w, y + h))
        total_normal += sum(region.getdata())
        y_flip = height - y - h
        if y_flip < 0:
            y_flip = 0
        if y_flip + h > height:
            y_flip = height - h
        region_flip = alpha.crop((x, y_flip, x + w, y_flip + h))
        total_flipped += sum(region_flip.getdata())
        count += 1
        if count >= sample_limit:
            break
    if count == 0:
        return False
    return total_flipped > total_normal


def _write_atlas_xml(
    xml_path: Path,
    image_path: str,
    image_size: Tuple[int, int],
    sprites: List[Dict[str, Any]],
    flip_y: bool,
    mesh_sprite_names: Optional[set[str]] = None,
    position_scale: float = 1.0,
    hires: Optional[bool] = None,
    pivot_mode: Optional[str] = None,
    include_mesh: bool = True,
) -> None:
    def _fmt_float(value: float, decimals: int = 6) -> str:
        text = f"{value:.{decimals}f}"
        text = text.rstrip("0").rstrip(".")
        if text == "-0":
            text = "0"
        return text

    has_mesh = False
    if include_mesh:
        for sprite in sprites:
            sprite_key = Path(sprite["name"]).name
            mesh_vertices = sprite.get("mesh_vertices") or []
            mesh_uv = sprite.get("mesh_uv") or []
            mesh_triangles = sprite.get("mesh_triangles") or []
            if mesh_sprite_names is not None and sprite_key not in mesh_sprite_names:
                continue
            if mesh_vertices and mesh_uv and mesh_triangles:
                has_mesh = True
                break

    hires_value = bool(hires) if hires is not None else False
    root_attrib = {
        "imagePath": image_path,
        "width": str(int(image_size[0])),
        "height": str(int(image_size[1])),
        "hires": "true" if hires_value else "false",
    }
    if has_mesh:
        root_attrib["version"] = "1"

    root = ET.Element("TextureAtlas", root_attrib)
    tex_h = float(image_size[1])
    for sprite in sprites:
        rect = sprite["rect"]
        x = int(round(rect["x"]))
        y = int(round(rect["y"]))
        w = int(round(rect["w"]))
        h = int(round(rect["h"]))
        if flip_y:
            y = int(round(tex_h - rect["y"] - rect["h"]))
        pivot = sprite["pivot"]
        pivot_x = pivot["x"]
        pivot_y = pivot["y"]
        pivot_y = 1.0 - pivot_y if flip_y else pivot_y
        trim = sprite.get("trim", {}) or {}
        # Always emit trim attributes so XML matches game formatting expectations.
        o_x = float(trim.get("x", 0.0) or 0.0)
        o_y = float(trim.get("y", 0.0) or 0.0)
        o_w = float(trim.get("w", w) or w)
        o_h = float(trim.get("h", h) or h)
        sprite_name = Path(sprite["name"]).name
        attrib = {
            "n": sprite_name,
            "x": str(x),
            "y": str(y),
            "w": str(w),
            "h": str(h),
            "pX": _fmt_float(pivot_x),
            "pY": _fmt_float(pivot_y),
        }
        attrib["oX"] = _fmt_float(o_x)
        attrib["oY"] = _fmt_float(o_y)
        attrib["oW"] = _fmt_float(o_w)
        attrib["oH"] = _fmt_float(o_h)
        sprite_elem = ET.SubElement(root, "sprite", attrib)
        sprite_key = sprite_name
        mesh_vertices = sprite.get("mesh_vertices") or []
        mesh_uv = sprite.get("mesh_uv") or []
        mesh_triangles = sprite.get("mesh_triangles") or []
        if mesh_sprite_names is not None and sprite_key not in mesh_sprite_names:
            mesh_vertices = []
            mesh_uv = []
            mesh_triangles = []
        if include_mesh and mesh_vertices and mesh_uv and mesh_triangles:
            vertices_text = " ".join(f"{_fmt_float(vx)} {_fmt_float(vy)}" for vx, vy in mesh_vertices)
            uv_text = " ".join(f"{_fmt_float(ux)} {_fmt_float(uy)}" for ux, uy in mesh_uv)
            tri_text = " ".join(str(int(idx)) for idx in mesh_triangles)
            ET.SubElement(sprite_elem, "vertices").text = vertices_text
            ET.SubElement(sprite_elem, "verticesUV").text = uv_text
            ET.SubElement(sprite_elem, "triangles").text = tri_text
    tree = ET.ElementTree(root)

    # Pretty-print in a TexturePacker-style format to better match game XML.
    def _indent(elem: ET.Element, level: int = 0) -> None:
        i = "\n" + ("    " * level)
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "    "
            for child in elem:
                _indent(child, level + 1)
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i

    _indent(root)

    header_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!-- Created with TexturePacker http://www.codeandweb.com/texturepacker-->',
        '<!--Format:',
        'n  => name of the sprite',
        'x  => sprite x pos in texture',
        'y  => sprite y pos in texture',
        'w  => sprite width (may be trimmed)',
        'h  => sprite height (may be trimmed)',
        'pX => x pos of the pivot point (relative to sprite width)',
        'pY => y pos of the pivot point (relative to sprite height)',
        'oX => sprite\'s x-corner offset (only available if trimmed)',
        'oY => sprite\'s y-corner offset (only available if trimmed)',
        'oW => sprite\'s original width (only available if trimmed)',
        'oH => sprite\'s original height (only available if trimmed)',
        "r => 'y' only set if sprite is rotated",
        'with polygon mode enabled:',
        'vertices   => points in sprite coordinate system (x0,y0,x1,y1,x2,y2, ...)',
        'verticesUV => points in sheet coordinate system (x0,y0,x1,y1,x2,y2, ...)',
        'triangles  => sprite triangulation, 3 vertex indices per triangle',
        '-->',
    ]

    xml_body = ET.tostring(root, encoding="unicode")
    xml_text = "\n".join(header_lines) + "\n" + xml_body.strip() + "\n"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml_text, encoding="utf-8")


def _channel_key_map(channel: Dict[str, Any]) -> Dict[float, Tuple[float, int]]:
    keys: Dict[float, Tuple[float, int]] = {}
    for entry in _iter_keyframes(channel):
        try:
            time = float(entry.get("time", 0.0))
            value = float(entry.get("value", 0.0))
            keytype = int(entry.get("keytype", 0))
        except (TypeError, ValueError):
            continue
        keys[time] = (value, keytype)
    return keys


def _dof_keytype_to_immediate(keytype: int) -> int:
    """Map DOF keytype to viewer immediate values (0=linear, 1=hold)."""
    if keytype == 1:
        return 0
    if keytype == 0:
        return 1
    return int(keytype)


def _sorted_key_list(
    keys: Dict[float, Tuple[float, int]]
) -> List[Tuple[float, float, int]]:
    return sorted((time, value, keytype) for time, (value, keytype) in keys.items())


def _evaluate_channel_value(
    keys: List[Tuple[float, float, int]],
    time: float,
    default: float,
) -> float:
    """Evaluate a channel at time using DOF keytypes (linear/hold)."""
    if not keys:
        return default
    prev = None
    for entry in keys:
        if entry[0] <= time:
            prev = entry
        else:
            break
    if prev is None:
        return keys[0][1]

    if _dof_keytype_to_immediate(prev[2]) == 1:
        return prev[1]

    next_entry = None
    for entry in keys:
        if entry[0] > time:
            next_entry = entry
            break
    if next_entry is None:
        return prev[1]

    time_diff = next_entry[0] - prev[0]
    if time_diff <= 0:
        return prev[1]
    t = (time - prev[0]) / time_diff
    return prev[1] + (next_entry[1] - prev[1]) * t


def _evaluate_channel_keytype(
    keys: List[Tuple[float, float, int]],
    time: float,
    default: int,
) -> int:
    """Return the last keytype at or before time (or first if none)."""
    if not keys:
        return default
    prev = None
    for entry in keys:
        if entry[0] <= time:
            prev = entry
        else:
            break
    if prev is None:
        return keys[0][2]
    return prev[2]


def _collect_times(channels: Iterable[Dict[str, Any]]) -> List[float]:
    times: set[float] = set()
    for channel in channels:
        for entry in _iter_keyframes(channel):
            try:
                times.add(float(entry.get("time", 0.0)))
            except (TypeError, ValueError):
                continue
    return sorted(times)


def _resolve_sprite_name(
    local_index: int,
    mapping: Optional[List[int]],
    images: List[str],
) -> str:
    idx = local_index
    if mapping and 0 <= local_index < len(mapping):
        idx = mapping[local_index]
    if 0 <= idx < len(images):
        return images[idx]
    return ""


def _resolve_node_mapping(
    mapping_hex: Optional[str],
    node: Dict[str, Any],
) -> Optional[List[int]]:
    if mapping_hex:
        mapping = _parse_hex_mapping(mapping_hex)
        if mapping:
            return mapping
    local_map = node.get("ImageIndexLocal2Global")
    if isinstance(local_map, list) and local_map:
        resolved: List[int] = []
        for item in local_map:
            try:
                resolved.append(int(item))
            except (TypeError, ValueError):
                resolved.append(-1)
        return resolved
    return None


def _coerce_sprite_index(raw_value: Any) -> Optional[int]:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    rounded = int(round(value))
    if abs(value - rounded) > 1e-3:
        return None
    return rounded


def _first_valid_sprite_name(
    mapping: Optional[List[int]],
    images: List[str],
) -> str:
    if mapping:
        for idx in mapping:
            try:
                idx_int = int(idx)
            except (TypeError, ValueError):
                continue
            if 0 <= idx_int < len(images):
                name = images[idx_int]
                if name:
                    return name
    for name in images:
        if name:
            return name
    return ""


def _resolve_sprite_name_safe(
    raw_value: Any,
    mapping: Optional[List[int]],
    images: List[str],
    fallback: str,
) -> str:
    idx = _coerce_sprite_index(raw_value)
    if idx is None:
        return fallback or ""
    if mapping is not None and not (0 <= idx < len(mapping)):
        return fallback or ""
    if mapping is None and not (0 <= idx < len(images)):
        return fallback or ""
    name = _resolve_sprite_name(idx, mapping, images)
    if name:
        return name
    return fallback or ""


_SPRITE_NUMERIC_SUFFIX_PATTERN = re.compile(r"^(.*?)(\d+)$")


def _expand_numeric_sprite_range(start_name: str, end_name: str) -> List[str]:
    """Return all numeric-suffix sprite names between two endpoints (inclusive)."""
    if not start_name or not end_name:
        return []
    m_start = _SPRITE_NUMERIC_SUFFIX_PATTERN.match(start_name)
    m_end = _SPRITE_NUMERIC_SUFFIX_PATTERN.match(end_name)
    if not m_start or not m_end:
        return []
    prefix_start, idx_start_raw = m_start.groups()
    prefix_end, idx_end_raw = m_end.groups()
    if prefix_start != prefix_end:
        return []
    try:
        idx_start = int(idx_start_raw)
        idx_end = int(idx_end_raw)
    except ValueError:
        return []
    width = max(len(idx_start_raw), len(idx_end_raw))
    step = 1 if idx_end >= idx_start else -1
    names: List[str] = []
    for idx in range(idx_start, idx_end + step, step):
        names.append(f"{prefix_start}{str(idx).zfill(width)}")
    return names


def _collect_layer_sprite_names(
    sprite_keys: Dict[float, Tuple[float, int]],
    mapping: Optional[List[int]],
    image_names: List[str],
    default_sprite_name: str,
) -> set[str]:
    """
    Gather sprite names used by a layer, including linear interpolation ranges.

    DOF sprite channels can be linear between two numbered sprites (e.g. eye_04 -> eye_06),
    and runtime interpolation may emit intermediate names (eye_05). Those names must be
    present in sprite_anchor_map to keep swapped/interpolated sprites aligned.
    """
    sprite_names: set[str] = set()
    if not sprite_keys:
        return sprite_names
    resolved_sorted: List[Tuple[float, str, int]] = []
    known_names = {name for name in image_names if name}

    for time_value, (sprite_index, key_type) in sorted(sprite_keys.items()):
        sprite_name = _resolve_sprite_name_safe(
            sprite_index, mapping, image_names, default_sprite_name
        )
        if not sprite_name:
            continue
        sprite_names.add(sprite_name)
        resolved_sorted.append((time_value, sprite_name, key_type))

    # Expand numeric interpolation ranges for linear sprite keyframes.
    for idx in range(len(resolved_sorted) - 1):
        _, prev_name, prev_key_type = resolved_sorted[idx]
        _, next_name, _ = resolved_sorted[idx + 1]
        if _dof_keytype_to_immediate(prev_key_type) != 0:
            continue
        for expanded in _expand_numeric_sprite_range(prev_name, next_name):
            if expanded in known_names:
                sprite_names.add(expanded)

    return sprite_names


def _build_anim_json(
    anim_name: str,
    anim_width: int,
    anim_height: int,
    nodes: List[Dict[str, Any]],
    particle_nodes: List[Dict[str, Any]],
    properties: Dict[str, Any],
    image_names: List[str],
    sprite_defs: Dict[str, Dict[str, Any]],
    node_mappings_raw: List[str],
    anim_flip_y: bool,
    invert_rotation: bool,
    sprite_flip_y: bool,
    center_anchor: bool,
    position_scale: float,
    mesh_use_offset: bool,
    dof_anchor_offset: bool,
    dof_anchor_center: bool,
    swap_anchor_report: bool,
    swap_anchor_report_path: Optional[Path],
    swap_anchor_edge_align: bool,
    swap_anchor_pivot_offset: bool,
    dof_disable_offset_anchor: bool,
    swap_override_by_index: Dict[int, Tuple[str, str]],
    swap_override_by_name: Dict[str, Tuple[str, str]],
    end_time: Optional[Any],
    resolve_guid: Callable[[Optional[str]], Optional[Path]],
    output_dir: Path,
    source_name: str = "atlas",
    source_width: int = 0,
    source_height: int = 0,
) -> Dict[str, Any]:
    global _LAST_LAYER_DEBUG
    _LAST_LAYER_DEBUG = []
    unique_sprites = []
    seen_names = set()
    for name in image_names:
        if name and name in sprite_defs and name not in seen_names:
            unique_sprites.append(sprite_defs[name])
            seen_names.add(name)

    swap_sprite_names: set[str] = set()
    for node_index, node in enumerate(nodes):
        if node.get("NodeType") != 1:
            continue
        channels = node.get("BezierFramesTypes", []) or []
        sprite_channel = channels[12] if len(channels) > 12 else {}
        sprite_keys = _channel_key_map(sprite_channel)
        if not sprite_keys:
            continue
        mapping_hex = node_mappings_raw[node_index] if node_index < len(node_mappings_raw) else ""
        mapping = _resolve_node_mapping(mapping_hex, node)
        default_swap_sprite = _first_valid_sprite_name(mapping, image_names)
        sprite_names = _collect_layer_sprite_names(
            sprite_keys, mapping, image_names, default_swap_sprite
        )
        if len(sprite_names) > 1:
            swap_sprite_names.update(sprite_names)

    # Wing swap frames still use pivot-based anchors. The pivot already encodes
    # the hinge/socket; do not add m_Offset to avoid swapping mirrored sprites.
    if swap_anchor_report:
        try:
            report = _build_swap_anchor_report(
                anim_name,
                nodes,
                node_mappings_raw,
                image_names,
                sprite_defs,
                sprite_flip_y,
            )
            report_path = _resolve_swap_anchor_report_path(
                output_dir,
                anim_name,
                swap_anchor_report_path,
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Swap anchor report: {report_path}")
        except Exception as exc:
            print(f"Swap anchor report failed: {exc}")

    additive_shader_refs = _collect_additive_shader_refs(nodes)
    layers: List[Dict[str, Any]] = []
    for node_index, node in enumerate(nodes):
        if node.get("NodeType") != 1:
            continue
        name = node.get("Name", f"Layer_{node_index}")
        offset_info = node.get("OffsetPos", {}) or {}
        try:
            node_offset_x = float(offset_info.get("x", 0.0) or 0.0)
            node_offset_y = float(offset_info.get("y", 0.0) or 0.0)
            node_offset_z = float(offset_info.get("z", 0.0) or 0.0)
        except (TypeError, ValueError):
            node_offset_x = 0.0
            node_offset_y = 0.0
            node_offset_z = 0.0
        try:
            image_scale = float(node.get("ImageScale", 1.0) or 1.0)
        except (TypeError, ValueError):
            image_scale = 1.0
        shader_entry = node.get("RenderTypeShader")
        shader_name = _resolve_shader_name(shader_entry, resolve_guid)
        blend_value = _infer_layer_blend(
            shader_name,
            name,
            shader_entry,
            additive_shader_refs,
        )
        channels = node.get("BezierFramesTypes", []) or []
        channel_map = {
            "pos_x": channels[0] if len(channels) > 0 else {},
            "pos_y": channels[1] if len(channels) > 1 else {},
            "depth": channels[2] if len(channels) > 2 else {},
            "scale_x": channels[3] if len(channels) > 3 else {},
            "scale_y": channels[4] if len(channels) > 4 else {},
            "rotation": channels[5] if len(channels) > 5 else {},
            "r": channels[8] if len(channels) > 8 else {},
            "g": channels[9] if len(channels) > 9 else {},
            "b": channels[10] if len(channels) > 10 else {},
            "opacity": channels[11] if len(channels) > 11 else {},
            "sprite": channels[12] if len(channels) > 12 else {},
            "flip_x": channels[13] if len(channels) > 13 else {},
        }
        scale_x_keys = _channel_key_map(channel_map["scale_x"])
        scale_y_keys = _channel_key_map(channel_map["scale_y"])
        flip_x_keys = _channel_key_map(channel_map["flip_x"])

        mapping_hex = node_mappings_raw[node_index] if node_index < len(node_mappings_raw) else ""
        mapping = _resolve_node_mapping(mapping_hex, node)

        sprite_keys = _channel_key_map(channel_map["sprite"])
        default_sprite_name = ""
        sprite_names: set[str] = set()
        if sprite_keys:
            for _, (sprite_index, _) in sorted(sprite_keys.items()):
                default_sprite_name = _resolve_sprite_name_safe(
                    sprite_index, mapping, image_names, ""
                )
                if default_sprite_name:
                    break
            sprite_names.update(
                _collect_layer_sprite_names(
                    sprite_keys, mapping, image_names, default_sprite_name
                )
            )
        if not default_sprite_name:
            default_sprite_name = _first_valid_sprite_name(mapping, image_names)
        if not sprite_names and default_sprite_name:
            sprite_names.add(default_sprite_name)
        flip_x = False
        anchor_flip_x = False
        use_offset_anchor = _choose_swap_anchor_mode(
            default_sprite_name,
            sprite_names,
            sprite_defs,
            sprite_flip_y,
            center_anchor,
            anchor_flip_x,
        )
        if dof_disable_offset_anchor:
            use_offset_anchor = False

        anchor_x = 0.0
        anchor_y = 0.0
        center_offset_x = 0.0
        center_offset_y = 0.0
        sprite_anchor_map: Optional[Dict[str, List[float]]] = None
        default_is_mesh = False
        edge_alignment = None
        override_modes: Optional[Tuple[str, str]] = None
        if swap_override_by_index:
            override_modes = swap_override_by_index.get(node_index)
        if override_modes is None and swap_override_by_name:
            override_modes = swap_override_by_name.get(name)
        use_edge_alignment = bool(override_modes or swap_anchor_edge_align)
        if swap_anchor_pivot_offset:
            use_edge_alignment = False
        if (
            use_edge_alignment
            and not center_anchor
            and sprite_names
            and len(sprite_names) > 1
        ):
            edge_alignment = _compute_swap_edge_alignment(
                sprite_names,
                sprite_defs,
                sprite_flip_y,
                default_sprite_name,
                override_modes[0] if override_modes else None,
                override_modes[1] if override_modes else None,
            )

        use_dof_anchor_offset = bool(dof_anchor_offset and not center_anchor)
        if edge_alignment:
            anchor_x, anchor_y, sprite_anchor_map = edge_alignment
        else:
            if default_sprite_name and default_sprite_name in sprite_defs:
                sprite_info = sprite_defs[default_sprite_name]
                default_is_mesh = _is_mesh_sprite(sprite_info)
                if default_is_mesh:
                    use_dof_anchor_offset = False
                (
                    anchor_x,
                    anchor_y,
                    center_offset_x,
                    center_offset_y,
                ) = _compute_sprite_anchor_with_mode(
                    sprite_info, sprite_flip_y, center_anchor, anchor_flip_x, use_offset_anchor
                )

            if sprite_names:
                anchor_map: Dict[str, List[float]] = {}
                for sprite_name in sprite_names:
                    sprite_info = sprite_defs.get(sprite_name)
                    if not sprite_info:
                        continue
                    map_anchor_x, map_anchor_y, _, _ = _compute_sprite_anchor_with_mode(
                        sprite_info, sprite_flip_y, center_anchor, anchor_flip_x, use_offset_anchor
                    )
                    try:
                        if not (math.isfinite(map_anchor_x) and math.isfinite(map_anchor_y)):
                            continue
                    except Exception:
                        continue
                    anchor_map[sprite_name] = [float(map_anchor_x), float(map_anchor_y)]
                if anchor_map:
                    sprite_anchor_map = anchor_map

        if (
            sprite_names
            and len(sprite_names) > 1
            and _anchor_map_is_zero(sprite_anchor_map)
            and not default_is_mesh
        ):
            pivot_center_map = _compute_swap_pivot_center_delta_map(
                sprite_names,
                sprite_defs,
                sprite_flip_y,
                default_sprite_name,
            )
            if pivot_center_map:
                sprite_anchor_map = pivot_center_map
        if default_is_mesh and _anchor_map_is_zero(sprite_anchor_map):
            sprite_anchor_map = None

        if use_dof_anchor_offset and sprite_names:
            default_offset: Optional[Tuple[float, float]] = None
            if default_sprite_name and default_sprite_name in sprite_defs:
                default_offset = _compute_offset_anchor(
                    sprite_defs[default_sprite_name], sprite_flip_y
                )
                anchor_x, anchor_y = default_offset
            offset_map: Dict[str, List[float]] = {}
            for sprite_name in sprite_names:
                sprite_info = sprite_defs.get(sprite_name)
                if not sprite_info:
                    continue
                offset_x, offset_y = _compute_offset_anchor(sprite_info, sprite_flip_y)
                offset_map[sprite_name] = [float(offset_x), float(offset_y)]
            if offset_map:
                sprite_anchor_map = offset_map

        if swap_anchor_pivot_offset and sprite_names and len(sprite_names) > 1:
            pivot_anchor_map = _compute_swap_pivot_offset_map(
                sprite_names,
                sprite_defs,
                sprite_flip_y,
                default_sprite_name,
                anchor_x,
                anchor_y,
                mesh_use_offset,
            )
            if pivot_anchor_map:
                sprite_anchor_map = pivot_anchor_map

        if dof_anchor_center and sprite_names:
            center_map: Dict[str, List[float]] = {}
            if default_sprite_name and default_sprite_name in sprite_defs:
                sprite_info = sprite_defs[default_sprite_name]
                anchor_x, anchor_y, _, _ = _compute_sprite_anchor_with_mode(
                    sprite_info, sprite_flip_y, True, anchor_flip_x, False
                )
            for sprite_name in sprite_names:
                sprite_info = sprite_defs.get(sprite_name)
                if not sprite_info:
                    continue
                map_anchor_x, map_anchor_y, _, _ = _compute_sprite_anchor_with_mode(
                    sprite_info, sprite_flip_y, True, anchor_flip_x, False
                )
                try:
                    if not (math.isfinite(map_anchor_x) and math.isfinite(map_anchor_y)):
                        continue
                except Exception:
                    continue
                center_map[sprite_name] = [float(map_anchor_x), float(map_anchor_y)]
            if center_map:
                sprite_anchor_map = center_map

        depth_keys = _channel_key_map(channel_map["depth"])
        pos_x_keys = _channel_key_map(channel_map["pos_x"])
        pos_y_keys = _channel_key_map(channel_map["pos_y"])
        rot_keys = _channel_key_map(channel_map["rotation"])
        opacity_keys = _channel_key_map(channel_map["opacity"])
        r_keys = _channel_key_map(channel_map["r"])
        g_keys = _channel_key_map(channel_map["g"])
        b_keys = _channel_key_map(channel_map["b"])

        key_times = _collect_times(
            [
                channel_map["pos_x"],
                channel_map["pos_y"],
                channel_map["scale_x"],
                channel_map["scale_y"],
                channel_map["rotation"],
                channel_map["opacity"],
                channel_map["sprite"],
                channel_map["depth"],
                channel_map["r"],
                channel_map["g"],
                channel_map["b"],
                channel_map["flip_x"],
            ]
        )
        if not key_times and end_time is not None:
            try:
                key_times = [float(end_time)]
            except (TypeError, ValueError):
                key_times = []

        pos_x_list = _sorted_key_list(pos_x_keys)
        pos_y_list = _sorted_key_list(pos_y_keys)
        scale_x_list = _sorted_key_list(scale_x_keys)
        scale_y_list = _sorted_key_list(scale_y_keys)
        rot_list = _sorted_key_list(rot_keys)
        opacity_list = _sorted_key_list(opacity_keys)
        depth_list = _sorted_key_list(depth_keys)
        sprite_list = _sorted_key_list(sprite_keys)
        flip_x_list = _sorted_key_list(flip_x_keys)
        r_list = _sorted_key_list(r_keys)
        g_list = _sorted_key_list(g_keys)
        b_list = _sorted_key_list(b_keys)

        first_frame_time = key_times[0] if key_times else 0.0
        default_depth_value = None
        default_depth_immediate = 1
        if depth_keys:
            depth_keys_list = _sorted_key_list(depth_keys)
            if depth_keys_list:
                default_depth_value = depth_keys_list[0][1]
                default_depth_immediate = _dof_keytype_to_immediate(depth_keys_list[0][2])
        elif abs(node_offset_z) > 1e-9:
            default_depth_value = float(node_offset_z)
            default_depth_immediate = 1

        effective_invert_rotation = bool(invert_rotation) ^ bool(anim_flip_y)
        frames: List[Dict[str, Any]] = []
        for time in key_times:
            frame: Dict[str, Any] = {
                "time": float(time),
                "pos": {"immediate": -1, "x": 0.0, "y": 0.0},
                "scale": {"immediate": -1, "x": 0.0, "y": 0.0},
                "rotation": {"immediate": -1, "value": 0.0},
                "opacity": {"immediate": -1, "value": 0.0},
                "sprite": {"immediate": -1, "string": ""},
                "rgb": {"immediate": -1, "red": 255, "green": 255, "blue": 255},
            }

            if time in pos_x_keys or time in pos_y_keys:
                pos_x_val, pos_x_key = pos_x_keys.get(time, (None, None))
                pos_y_val, pos_y_key = pos_y_keys.get(time, (None, None))
                if pos_x_val is None:
                    pos_x_val = _evaluate_channel_value(pos_x_list, time, 0.0)
                if pos_y_val is None:
                    pos_y_val = _evaluate_channel_value(pos_y_list, time, 0.0)
                pos_x_keytype = (
                    pos_x_key
                    if pos_x_key is not None
                    else _evaluate_channel_keytype(pos_x_list, time, 0)
                )
                pos_y_keytype = (
                    pos_y_key
                    if pos_y_key is not None
                    else _evaluate_channel_keytype(pos_y_list, time, 0)
                )
                pos_immediate = 1
                if (
                    _dof_keytype_to_immediate(pos_x_keytype) == 0
                    or _dof_keytype_to_immediate(pos_y_keytype) == 0
                ):
                    pos_immediate = 0
                pos_x_val += node_offset_x
                pos_y_val += node_offset_y
                if anim_flip_y:
                    pos_y_val = -pos_y_val
                if center_anchor:
                    pos_x_val += center_offset_x
                    pos_y_val += center_offset_y
                pos_x_val *= position_scale
                pos_y_val *= position_scale
                frame["pos"] = {
                    "immediate": pos_immediate,
                    "x": float(pos_x_val),
                    "y": float(pos_y_val),
                }

            if time in depth_keys:
                depth_val, depth_key = depth_keys[time]
                frame["depth"] = {
                    "immediate": _dof_keytype_to_immediate(depth_key),
                    "value": float(depth_val) + node_offset_z,
                }
            elif default_depth_value is not None and time == first_frame_time:
                frame["depth"] = {
                    "immediate": default_depth_immediate,
                    "value": default_depth_value,
                }

            if time in scale_x_keys or time in scale_y_keys or time in flip_x_keys:
                scale_x_val, scale_x_key = scale_x_keys.get(time, (None, None))
                scale_y_val, scale_y_key = scale_y_keys.get(time, (None, None))
                if scale_x_val is None:
                    scale_x_val = _evaluate_channel_value(scale_x_list, time, 1.0)
                if scale_y_val is None:
                    scale_y_val = _evaluate_channel_value(scale_y_list, time, 1.0)
                if image_scale not in (1.0, 1):
                    scale_x_val *= image_scale
                    scale_y_val *= image_scale
                if flip_x_keys:
                    flip_x_active = _evaluate_channel_value(flip_x_list, time, 0.0) >= 0.5
                    if flip_x_active:
                        scale_x_val = -abs(scale_x_val)
                    else:
                        scale_x_val = abs(scale_x_val)
                elif flip_x and scale_x_val >= 0:
                    scale_x_val = -scale_x_val
                scale_x_keytype = (
                    scale_x_key
                    if scale_x_key is not None
                    else _evaluate_channel_keytype(scale_x_list, time, 0)
                )
                scale_y_keytype = (
                    scale_y_key
                    if scale_y_key is not None
                    else _evaluate_channel_keytype(scale_y_list, time, 0)
                )
                scale_immediate = 1
                if (
                    _dof_keytype_to_immediate(scale_x_keytype) == 0
                    or _dof_keytype_to_immediate(scale_y_keytype) == 0
                ):
                    scale_immediate = 0
                if (
                    scale_immediate == 0
                    and time in flip_x_keys
                    and time not in scale_x_keys
                    and time not in scale_y_keys
                ):
                    scale_immediate = 1
                frame["scale"] = {
                    "immediate": scale_immediate,
                    "x": float(scale_x_val) * 100.0,
                    "y": float(scale_y_val) * 100.0,
                }

            if time in rot_keys:
                rot_val, rot_key = rot_keys[time]
                rot_deg = float(rot_val) * (180.0 / 3.141592653589793)
                if effective_invert_rotation:
                    rot_deg = -rot_deg
                frame["rotation"] = {
                    "immediate": _dof_keytype_to_immediate(rot_key),
                    "value": rot_deg,
                }

            if time in opacity_keys:
                op_val, op_key = opacity_keys[time]
                frame["opacity"] = {
                    "immediate": _dof_keytype_to_immediate(op_key),
                    "value": float(op_val),
                }

            if time in sprite_keys:
                sprite_val, sprite_key = sprite_keys[time]
                sprite_name = _resolve_sprite_name_safe(
                    sprite_val, mapping, image_names, default_sprite_name
                )
                frame["sprite"] = {
                    "immediate": _dof_keytype_to_immediate(sprite_key),
                    "string": sprite_name,
                }

            if time in r_keys or time in g_keys or time in b_keys:
                r_val, r_key = r_keys.get(time, (None, None))
                g_val, g_key = g_keys.get(time, (None, None))
                b_val, b_key = b_keys.get(time, (None, None))
                if r_val is None:
                    r_val = _evaluate_channel_value(r_list, time, 1.0)
                if g_val is None:
                    g_val = _evaluate_channel_value(g_list, time, 1.0)
                if b_val is None:
                    b_val = _evaluate_channel_value(b_list, time, 1.0)
                rgb_immediate = 1
                if r_key is not None and _dof_keytype_to_immediate(r_key) == 0:
                    rgb_immediate = 0
                if g_key is not None and _dof_keytype_to_immediate(g_key) == 0:
                    rgb_immediate = 0
                if b_key is not None and _dof_keytype_to_immediate(b_key) == 0:
                    rgb_immediate = 0
                frame["rgb"] = {
                    "immediate": rgb_immediate,
                    "red": int(round(float(r_val) * 255.0)),
                    "green": int(round(float(g_val) * 255.0)),
                    "blue": int(round(float(b_val) * 255.0)),
                }

            frames.append(frame)

        # Ensure anchor maps reflect the sprites actually used by frames.
        frame_sprite_names: set[str] = set()
        for frame in frames:
            sprite_entry = frame.get("sprite")
            if isinstance(sprite_entry, dict):
                sprite_name = sprite_entry.get("string")
                if isinstance(sprite_name, str) and sprite_name:
                    frame_sprite_names.add(sprite_name)
        if frame_sprite_names:
            map_keys = set(sprite_anchor_map.keys()) if sprite_anchor_map else set()
            if not map_keys.issuperset(frame_sprite_names):
                # If the computed default isn't used in frames, recompute anchor from a real sprite.
                if default_sprite_name not in frame_sprite_names:
                    fallback_name = sorted(frame_sprite_names)[0]
                    sprite_info = sprite_defs.get(fallback_name)
                    if sprite_info:
                        anchor_x, anchor_y, center_offset_x, center_offset_y = _compute_sprite_anchor_with_mode(
                            sprite_info, sprite_flip_y, center_anchor, anchor_flip_x, use_offset_anchor
                        )
                        default_sprite_name = fallback_name
                anchor_map: Dict[str, List[float]] = {}
                for sprite_name in frame_sprite_names:
                    sprite_info = sprite_defs.get(sprite_name)
                    if not sprite_info:
                        continue
                    map_anchor_x, map_anchor_y, _, _ = _compute_sprite_anchor_with_mode(
                        sprite_info, sprite_flip_y, center_anchor, anchor_flip_x, use_offset_anchor
                    )
                    try:
                        if not (math.isfinite(map_anchor_x) and math.isfinite(map_anchor_y)):
                            continue
                    except Exception:
                        continue
                    anchor_map[sprite_name] = [float(map_anchor_x), float(map_anchor_y)]
                if anchor_map:
                    sprite_anchor_map = anchor_map

        _LAST_LAYER_DEBUG.append(
            {
                "layer": name,
                "default_sprite": default_sprite_name,
                "sprite_names": sorted(sprite_names),
                "frame_sprites": sorted(frame_sprite_names),
                "anchor_map_keys": sorted(sprite_anchor_map.keys()) if sprite_anchor_map else [],
                "anchor": [float(anchor_x), float(anchor_y)],
                "use_offset_anchor": bool(use_offset_anchor),
                "center_anchor": bool(center_anchor),
            }
        )

        layer_dict = {
            "name": name,
            "type": 1,
            "blend": int(blend_value),
            "parent": None,
            "id": int(node_index),
            "src": 0,
            "width": 0,
            "height": 0,
            "anchor_x": float(anchor_x),
            "anchor_y": float(anchor_y),
            "unk": "",
            "shader": shader_name or "Anim2D/Normal+Alpha",
            "frames": frames,
            "sprite_anchor_map": sprite_anchor_map,
            "_node_index": int(node_index),
            "_depth": None,
            "_order": int(node_index),
        }
        parent_raw = node.get("Parent", -1)
        try:
            parent_id = int(parent_raw)
        except (TypeError, ValueError):
            parent_id = -1
        layer_dict["parent"] = parent_id
        depth_value = node_offset_z
        if depth_keys:
            try:
                first_time = min(depth_keys.keys())
                depth_value = float(depth_keys[first_time][0]) + node_offset_z
            except Exception:
                depth_value = node_offset_z
        layer_dict["_depth"] = float(depth_value)
        layers.append(layer_dict)

    # Convert DOF depth ordering into MSM-style front-to-back layer order.
    layers.sort(key=lambda layer: (-layer.get("_depth", 0.0), layer.get("_order", 0)))
    id_map = {int(layer.get("id", idx)): idx for idx, layer in enumerate(layers)}
    for idx, layer in enumerate(layers):
        old_id = int(layer.get("id", idx))
        layer["id"] = idx
        parent_id = layer.get("parent", -1)
        if isinstance(parent_id, int) and parent_id in id_map:
            layer["parent"] = id_map[parent_id]
        else:
            layer["parent"] = -1
        layer.pop("_node_index", None)
        layer.pop("_depth", None)
        layer.pop("_order", None)

    anim_json: Dict[str, Any] = {
        "rev": 6,
        "blend_version": 2,
        "dof_meta": {
            "anim_flip_y": bool(anim_flip_y),
            "sprite_flip_y": bool(sprite_flip_y),
        },
        "sources": [
            {
                "src": source_name,
                "id": 0,
                "width": int(source_width),
                "height": int(source_height),
            }
        ],
        "anims": [
            {
                "name": anim_name,
                "width": anim_width,
                "height": anim_height,
                "loop_offset": -1.0,
                "centered": 1,
                "layers": layers,
            }
        ],
    }

    if end_time is not None:
        try:
            anim_json["anims"][0]["duration"] = float(end_time)
        except (TypeError, ValueError):
            pass
    return anim_json


def _write_dof_debug_report(
    output_dir: Path,
    anim_name: str,
    sprite_defs: Dict[str, Dict[str, Any]],
    sprite_flip_y: bool,
    dof_anchor_offset: bool,
    dof_anchor_center: bool,
) -> None:
    report: List[Dict[str, Any]] = []
    for name, info in sorted(sprite_defs.items(), key=lambda item: item[0]):
        rect = info.get("rect", {}) or {}
        trim = info.get("trim", {}) or {}
        pivot = info.get("pivot", {}) or {}
        offset = info.get("offset", {}) or {}
        mesh_vertices = info.get("mesh_vertices") or []
        mesh_bounds = info.get("mesh_bounds")
        mesh_anchor_zero = bool(info.get("mesh_anchor_zero"))
        mesh_min = mesh_max = None
        if mesh_vertices:
            try:
                mesh_min = (
                    min(vx for vx, _ in mesh_vertices),
                    min(vy for _, vy in mesh_vertices),
                )
                mesh_max = (
                    max(vx for vx, _ in mesh_vertices),
                    max(vy for _, vy in mesh_vertices),
                )
            except Exception:
                mesh_min = None
                mesh_max = None
        use_offset_anchor = bool(dof_anchor_offset)
        anchor_x, anchor_y, center_dx, center_dy = _compute_sprite_anchor_with_mode(
            info,
            sprite_flip_y,
            bool(dof_anchor_center),
            False,
            use_offset_anchor,
        )
        report.append(
            {
                "sprite": name,
                "rect": dict(rect),
                "trim": dict(trim),
                "pivot": dict(pivot),
                "offset": dict(offset),
                "mesh_bounds": mesh_bounds,
                "mesh_min": mesh_min,
                "mesh_max": mesh_max,
                "mesh_anchor_zero": mesh_anchor_zero,
                "computed_anchor": [float(anchor_x), float(anchor_y)],
                "center_offset": [float(center_dx), float(center_dy)],
            }
        )
    report_path = output_dir / f"{anim_name}_dof_debug.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"DOF debug report: {report_path}")
    layer_debug = globals().get("_LAST_LAYER_DEBUG", [])
    if layer_debug:
        layer_path = output_dir / f"{anim_name}_dof_layer_debug.json"
        layer_path.write_text(json.dumps(layer_debug, indent=2), encoding="utf-8")
        print(f"DOF layer debug report: {layer_path}")
    alpha_debug = globals().get("_LAST_ALPHA_DEBUG", {})
    if alpha_debug:
        alpha_path = output_dir / f"{anim_name}_dof_alpha_debug.json"
        alpha_path.write_text(json.dumps(alpha_debug, indent=2), encoding="utf-8")
        print(f"DOF alpha debug report: {alpha_path}")


def convert_anim(
    anim_path: Path,
    output_dir: Path,
    assets_root: Optional[Path],
    flip_y: bool,
    invert_rotation: bool,
    center_anchor: bool,
    position_scale: float,
    use_guid_cache: bool,
    refresh_guid_cache: bool,
    mesh_use_offset: bool = True,
    include_mesh_xml: bool = False,
    premultiply_alpha: bool = False,
    alpha_hardness: float = 0.0,
    dof_anchor_offset: bool = False,
    dof_anchor_center: bool = False,
    swap_anchor_report: bool = False,
    swap_anchor_report_path: Optional[Path] = None,
    swap_anchor_edge_align: bool = False,
    swap_anchor_pivot_offset: bool = False,
    swap_anchor_report_override: bool = False,
    swap_anchor_report_override_path: Optional[Path] = None,
    hires_override: Optional[bool] = None,
) -> Path:
    global _LAST_SPRITE_DEFS, _LAST_ALPHA_DEBUG
    _LAST_ALPHA_DEBUG = {}
    raw_text = anim_path.read_text(encoding="utf-8")
    data = _load_yaml(anim_path, raw_text)
    mono = data.get("MonoBehaviour", {})
    node_mappings_raw = _extract_node_mappings(raw_text)
    anim_flip_y = bool(flip_y)
    sprite_flip_y = False

    images = mono.get("Images", []) or []
    image_guids: List[str] = []
    for image in images:
        if isinstance(image, dict):
            sprite_entry = image.get("sprite", {})
            if isinstance(sprite_entry, dict):
                guid = sprite_entry.get("guid")
                if guid:
                    image_guids.append(guid)

    size = mono.get("size", {}) or {}
    anim_width = int(round(float(size.get("x", 0) or 0)))
    anim_height = int(round(float(size.get("y", 0) or 0)))
    anim_name = _anim_name_from_path(anim_path)
    end_time = mono.get("EndTime", None)

    anim_dir = anim_path.parent
    variant = anim_dir.name
    display_dir = anim_dir.parent.parent if anim_dir.parent else anim_dir
    sprites_dir = display_dir / "sprites" / variant
    if not sprites_dir.is_dir():
        raise FileNotFoundError(f"Sprite folder not found: {sprites_dir}")

    tpp_path = _find_tpp_asset(sprites_dir)
    if not tpp_path:
        raise FileNotFoundError(f"No .TPP.asset found in {sprites_dir}")

    tex_guid, alpha_guid, _tpp_sprites = _parse_tpp_asset(tpp_path)
    guid_map = _build_guid_map(sprites_dir)
    fallback_map: Dict[str, Path] = {}

    def resolve_guid(guid: Optional[str]) -> Optional[Path]:
        if not guid:
            return None
        if guid in guid_map:
            return guid_map[guid]
        if assets_root:
            nonlocal fallback_map
            if not fallback_map:
                fallback_map = _build_guid_map(
                    assets_root,
                    disk_cache=use_guid_cache,
                    refresh_cache=refresh_guid_cache,
                )
            return fallback_map.get(guid)
        return None

    texture_path = resolve_guid(tex_guid)
    alpha_path = resolve_guid(alpha_guid)
    if not texture_path or not texture_path.exists():
        raise FileNotFoundError("Texture PNG not found for TPP asset.")

    atlas_base = _strip_suffix(tpp_path.stem, ".TPP")
    output_is_xml_resources = output_dir.name.lower() == "xml_resources"
    if output_is_xml_resources:
        atlas_filename = f"{atlas_base}.png"
    else:
        atlas_filename = f"{atlas_base}_texture.png"
    atlas_png = output_dir / atlas_filename
    atlas_xml = output_dir / f"{atlas_base}.xml"
    image_path = atlas_png.name
    texture_output = atlas_png
    if output_is_xml_resources:
        data_root = output_dir.parent
        gfx_monsters = data_root / "gfx" / "monsters"
        gfx_monsters.mkdir(parents=True, exist_ok=True)
        texture_output = gfx_monsters / atlas_filename
        image_path = f"gfx/monsters/{atlas_filename}"
    sprite_defs: Dict[str, Dict[str, Any]] = {}
    image_size = _build_texture(
        texture_path,
        alpha_path,
        texture_output,
        [],
        premultiply_alpha=premultiply_alpha,
        alpha_hardness=alpha_hardness,
    )
    image_names: List[str] = []
    for guid in image_guids:
        asset_path = resolve_guid(guid)
        if not asset_path:
            image_names.append("")
            continue
        sprite_data = _parse_sprite_asset(
            asset_path,
            image_size,
            sprite_flip_y,
            False,
            mesh_use_offset,
        )
        sprite_name = Path(sprite_data["name"]).name
        sprite_defs[sprite_name] = sprite_data
        image_names.append(sprite_name)

    atlas_flip_y = _detect_atlas_flip_y(texture_output, list(sprite_defs.values()))

    if atlas_flip_y and image_names:
        sprite_defs = {}
        image_names = []
        for guid in image_guids:
            asset_path = resolve_guid(guid)
            if not asset_path:
                image_names.append("")
                continue
            sprite_data = _parse_sprite_asset(
                asset_path,
                image_size,
                sprite_flip_y,
                atlas_flip_y,
                mesh_use_offset,
            )
            sprite_name = Path(sprite_data["name"]).name
            sprite_defs[sprite_name] = sprite_data
            image_names.append(sprite_name)
    _LAST_SPRITE_DEFS = dict(sprite_defs)

    swap_override_by_index: Dict[int, Tuple[str, str]] = {}
    swap_override_by_name: Dict[str, Tuple[str, str]] = {}
    if swap_anchor_report_override:
        override_path = _resolve_swap_anchor_report_path(
            output_dir,
            anim_name,
            swap_anchor_report_override_path,
        )
        if override_path.exists():
            try:
                override_payload = json.loads(override_path.read_text(encoding="utf-8"))
                for entry in override_payload.get("nodes", []) or []:
                    try:
                        node_idx = int(entry.get("node_index"))
                    except (TypeError, ValueError):
                        node_idx = None
                    node_name = entry.get("node_name")
                    best_x = (entry.get("best_x") or {}).get("mode")
                    best_y = (entry.get("best_y") or {}).get("mode")
                    if isinstance(best_x, str) and isinstance(best_y, str):
                        if node_idx is not None:
                            swap_override_by_index[node_idx] = (best_x, best_y)
                        if isinstance(node_name, str) and node_name:
                            swap_override_by_name[node_name] = (best_x, best_y)
                print(f"Swap anchor override: {override_path}")
            except Exception as exc:
                print(f"Swap anchor override failed: {exc}")
        else:
            print(f"Swap anchor override missing: {override_path}")

    unique_sprites = []
    seen_names = set()
    for name in image_names:
        if name and name in sprite_defs and name not in seen_names:
            unique_sprites.append(sprite_defs[name])
            seen_names.add(name)

    swap_sprite_names: set[str] = set()
    nodes = mono.get("Nodes", []) or []
    for node_index, node in enumerate(nodes):
        if node.get("NodeType") != 1:
            continue
        channels = node.get("BezierFramesTypes", []) or []
        sprite_channel = channels[12] if len(channels) > 12 else {}
        sprite_keys = _channel_key_map(sprite_channel)
        if not sprite_keys:
            continue
        mapping_hex = node_mappings_raw[node_index] if node_index < len(node_mappings_raw) else ""
        mapping = _resolve_node_mapping(mapping_hex, node)
        sprite_names: set[str] = set()
        for sprite_index, _ in sprite_keys.values():
            sprite_name = _resolve_sprite_name(int(round(sprite_index)), mapping, image_names)
            if sprite_name:
                sprite_names.add(sprite_name)
        if len(sprite_names) > 1:
            swap_sprite_names.update(sprite_names)

    # Wing swap frames still use pivot-based anchors. The pivot already encodes
    # the hinge/socket; do not add m_Offset to avoid swapping mirrored sprites.
    if swap_anchor_report:
        try:
            report = _build_swap_anchor_report(
                anim_name,
                nodes,
                node_mappings_raw,
                image_names,
                sprite_defs,
                sprite_flip_y,
            )
            report_path = _resolve_swap_anchor_report_path(
                output_dir,
                anim_name,
                swap_anchor_report_path,
            )
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"Swap anchor report: {report_path}")
        except Exception as exc:
            print(f"Swap anchor report failed: {exc}")

    hires_value = hires_override if hires_override is not None else output_is_xml_resources
    _write_atlas_xml(
        atlas_xml,
        image_path,
        image_size,
        unique_sprites,
        atlas_flip_y,
        mesh_sprite_names=None,
        position_scale=position_scale,
        hires=hires_value,
        include_mesh=bool(include_mesh_xml),
    )

    additive_shader_refs = _collect_additive_shader_refs(nodes)
    layers: List[Dict[str, Any]] = []
    for node_index, node in enumerate(nodes):
        if node.get("NodeType") != 1:
            continue
        name = node.get("Name", f"Layer_{node_index}")
        offset_info = node.get("OffsetPos", {}) or {}
        try:
            node_offset_x = float(offset_info.get("x", 0.0) or 0.0)
            node_offset_y = float(offset_info.get("y", 0.0) or 0.0)
            node_offset_z = float(offset_info.get("z", 0.0) or 0.0)
        except (TypeError, ValueError):
            node_offset_x = 0.0
            node_offset_y = 0.0
            node_offset_z = 0.0
        try:
            image_scale = float(node.get("ImageScale", 1.0) or 1.0)
        except (TypeError, ValueError):
            image_scale = 1.0
        shader_entry = node.get("RenderTypeShader")
        shader_name = _resolve_shader_name(shader_entry, resolve_guid)
        blend_value = _infer_layer_blend(
            shader_name,
            name,
            shader_entry,
            additive_shader_refs,
        )
        channels = node.get("BezierFramesTypes", []) or []
        channel_map = {
            "pos_x": channels[0] if len(channels) > 0 else {},
            "pos_y": channels[1] if len(channels) > 1 else {},
            "depth": channels[2] if len(channels) > 2 else {},
            "scale_x": channels[3] if len(channels) > 3 else {},
            "scale_y": channels[4] if len(channels) > 4 else {},
            "rotation": channels[5] if len(channels) > 5 else {},
            "r": channels[8] if len(channels) > 8 else {},
            "g": channels[9] if len(channels) > 9 else {},
            "b": channels[10] if len(channels) > 10 else {},
            "opacity": channels[11] if len(channels) > 11 else {},
            "sprite": channels[12] if len(channels) > 12 else {},
            "flip_x": channels[13] if len(channels) > 13 else {},
        }
        scale_x_keys = _channel_key_map(channel_map["scale_x"])
        scale_y_keys = _channel_key_map(channel_map["scale_y"])
        flip_x_keys = _channel_key_map(channel_map["flip_x"])

        mapping_hex = node_mappings_raw[node_index] if node_index < len(node_mappings_raw) else ""
        mapping = _resolve_node_mapping(mapping_hex, node)

        sprite_keys = _channel_key_map(channel_map["sprite"])
        default_sprite_name = ""
        sprite_names: set[str] = set()
        if sprite_keys:
            for _, (sprite_index, _) in sorted(sprite_keys.items()):
                default_sprite_name = _resolve_sprite_name_safe(
                    sprite_index, mapping, image_names, ""
                )
                if default_sprite_name:
                    break
            for sprite_index, _ in sprite_keys.values():
                sprite_name = _resolve_sprite_name_safe(
                    sprite_index, mapping, image_names, default_sprite_name
                )
                if sprite_name:
                    sprite_names.add(sprite_name)
        if not default_sprite_name:
            default_sprite_name = _first_valid_sprite_name(mapping, image_names)
        if not sprite_names and default_sprite_name:
            sprite_names.add(default_sprite_name)
        flip_x = False
        anchor_flip_x = False
        use_offset_anchor = _choose_swap_anchor_mode(
            default_sprite_name,
            sprite_names,
            sprite_defs,
            sprite_flip_y,
            center_anchor,
            anchor_flip_x,
        )

        anchor_x = 0.0
        anchor_y = 0.0
        center_offset_x = 0.0
        center_offset_y = 0.0
        sprite_anchor_map: Optional[Dict[str, List[float]]] = None
        default_is_mesh = False
        edge_alignment = None
        override_modes: Optional[Tuple[str, str]] = None
        if swap_override_by_index:
            override_modes = swap_override_by_index.get(node_index)
        if override_modes is None and swap_override_by_name:
            override_modes = swap_override_by_name.get(name)
        use_edge_alignment = bool(override_modes or swap_anchor_edge_align)
        if swap_anchor_pivot_offset:
            use_edge_alignment = False
        if (
            use_edge_alignment
            and not center_anchor
            and sprite_names
            and len(sprite_names) > 1
        ):
            edge_alignment = _compute_swap_edge_alignment(
                sprite_names,
                sprite_defs,
                sprite_flip_y,
                default_sprite_name,
                override_modes[0] if override_modes else None,
                override_modes[1] if override_modes else None,
            )

        use_dof_anchor_offset = bool(dof_anchor_offset and not center_anchor)
        if edge_alignment:
            anchor_x, anchor_y, sprite_anchor_map = edge_alignment
        else:
            if default_sprite_name and default_sprite_name in sprite_defs:
                sprite_info = sprite_defs[default_sprite_name]
                default_is_mesh = _is_mesh_sprite(sprite_info)
                if default_is_mesh:
                    # DOF sprites already store pivot-local vertices; m_Offset anchors
                    # shift the sprite away from its true pivot.
                    use_dof_anchor_offset = False
                (
                    anchor_x,
                    anchor_y,
                    center_offset_x,
                    center_offset_y,
                ) = _compute_sprite_anchor_with_mode(
                    sprite_info, sprite_flip_y, center_anchor, anchor_flip_x, use_offset_anchor
                )

            if sprite_names:
                anchor_map: Dict[str, List[float]] = {}
                for sprite_name in sprite_names:
                    sprite_info = sprite_defs.get(sprite_name)
                    if not sprite_info:
                        continue
                    map_anchor_x, map_anchor_y, _, _ = _compute_sprite_anchor_with_mode(
                        sprite_info, sprite_flip_y, center_anchor, anchor_flip_x, use_offset_anchor
                    )
                    # Skip invalid anchors (e.g., EMPTY_SPRITE with NaN pivots).
                    try:
                        if not (math.isfinite(map_anchor_x) and math.isfinite(map_anchor_y)):
                            continue
                    except Exception:
                        continue
                    anchor_map[sprite_name] = [float(map_anchor_x), float(map_anchor_y)]
                if anchor_map:
                    sprite_anchor_map = anchor_map

        if (
            sprite_names
            and len(sprite_names) > 1
            and _anchor_map_is_zero(sprite_anchor_map)
            and not default_is_mesh
        ):
            pivot_center_map = _compute_swap_pivot_center_delta_map(
                sprite_names,
                sprite_defs,
                sprite_flip_y,
                default_sprite_name,
            )
            if pivot_center_map:
                sprite_anchor_map = pivot_center_map
        if default_is_mesh and _anchor_map_is_zero(sprite_anchor_map):
            sprite_anchor_map = None

        if use_dof_anchor_offset and sprite_names:
            default_offset: Optional[Tuple[float, float]] = None
            if default_sprite_name and default_sprite_name in sprite_defs:
                default_offset = _compute_offset_anchor(
                    sprite_defs[default_sprite_name], sprite_flip_y
                )
                anchor_x, anchor_y = default_offset
            offset_map: Dict[str, List[float]] = {}
            for sprite_name in sprite_names:
                sprite_info = sprite_defs.get(sprite_name)
                if not sprite_info:
                    continue
                offset_x, offset_y = _compute_offset_anchor(sprite_info, sprite_flip_y)
                offset_map[sprite_name] = [float(offset_x), float(offset_y)]
            if offset_map:
                sprite_anchor_map = offset_map

        if swap_anchor_pivot_offset and sprite_names and len(sprite_names) > 1:
            pivot_anchor_map = _compute_swap_pivot_offset_map(
                sprite_names,
                sprite_defs,
                sprite_flip_y,
                default_sprite_name,
                anchor_x,
                anchor_y,
                mesh_use_offset,
            )
            if pivot_anchor_map:
                sprite_anchor_map = pivot_anchor_map

        if dof_anchor_center and sprite_names:
            if default_sprite_name and default_sprite_name in sprite_defs:
                sprite_info = sprite_defs[default_sprite_name]
                anchor_x, anchor_y, _, _ = _compute_sprite_anchor_with_mode(
                    sprite_info, sprite_flip_y, True, anchor_flip_x, False
                )
            center_map: Dict[str, List[float]] = {}
            for sprite_name in sprite_names:
                sprite_info = sprite_defs.get(sprite_name)
                if not sprite_info:
                    continue
                map_anchor_x, map_anchor_y, _, _ = _compute_sprite_anchor_with_mode(
                    sprite_info, sprite_flip_y, True, anchor_flip_x, False
                )
                try:
                    if not (math.isfinite(map_anchor_x) and math.isfinite(map_anchor_y)):
                        continue
                except Exception:
                    continue
                center_map[sprite_name] = [float(map_anchor_x), float(map_anchor_y)]
            if center_map:
                sprite_anchor_map = center_map

        key_times = _collect_times(channel_map.values())
        try:
            end_time_value = float(end_time)
        except (TypeError, ValueError):
            end_time_value = None
        if end_time_value is not None and math.isfinite(end_time_value):
            key_times.append(end_time_value)
            key_times = sorted(set(key_times))
        frames: List[Dict[str, Any]] = []

        pos_x_keys = _channel_key_map(channel_map["pos_x"])
        pos_y_keys = _channel_key_map(channel_map["pos_y"])
        depth_keys = _channel_key_map(channel_map["depth"])
        rot_keys = _channel_key_map(channel_map["rotation"])
        opacity_keys = _channel_key_map(channel_map["opacity"])
        r_keys = _channel_key_map(channel_map["r"])
        g_keys = _channel_key_map(channel_map["g"])
        b_keys = _channel_key_map(channel_map["b"])

        pos_x_list = _sorted_key_list(pos_x_keys)
        pos_y_list = _sorted_key_list(pos_y_keys)
        scale_x_list = _sorted_key_list(scale_x_keys)
        scale_y_list = _sorted_key_list(scale_y_keys)
        flip_x_list = _sorted_key_list(flip_x_keys)
        r_list = _sorted_key_list(r_keys)
        g_list = _sorted_key_list(g_keys)
        b_list = _sorted_key_list(b_keys)

        depth_value = node_offset_z
        if depth_keys:
            first_time = min(depth_keys.keys())
            depth_value = float(depth_keys[first_time][0]) + node_offset_z

        if not key_times:
            key_times = [0.0]
        first_frame_time = key_times[0] if key_times else 0.0
        default_depth_value: Optional[float] = None
        default_depth_immediate = -1
        if not depth_keys and abs(node_offset_z) > 1e-9:
            default_depth_value = float(node_offset_z)
            default_depth_immediate = 1

        effective_invert_rotation = bool(invert_rotation) ^ bool(anim_flip_y)
        for time in key_times:
            frame: Dict[str, Any] = {
                "time": float(time),
                "pos": {"immediate": -1, "x": 0.0, "y": 0.0},
                "scale": {"immediate": -1, "x": 0.0, "y": 0.0},
                "rotation": {"immediate": -1, "value": 0.0},
                "opacity": {"immediate": -1, "value": 0.0},
                "sprite": {"immediate": -1, "string": ""},
                "rgb": {"immediate": -1, "red": 255, "green": 255, "blue": 255},
            }

            if time in pos_x_keys or time in pos_y_keys:
                pos_x_val, pos_x_key = pos_x_keys.get(time, (None, None))
                pos_y_val, pos_y_key = pos_y_keys.get(time, (None, None))
                if pos_x_val is None:
                    pos_x_val = _evaluate_channel_value(pos_x_list, time, 0.0)
                if pos_y_val is None:
                    pos_y_val = _evaluate_channel_value(pos_y_list, time, 0.0)
                pos_x_keytype = (
                    pos_x_key
                    if pos_x_key is not None
                    else _evaluate_channel_keytype(pos_x_list, time, 0)
                )
                pos_y_keytype = (
                    pos_y_key
                    if pos_y_key is not None
                    else _evaluate_channel_keytype(pos_y_list, time, 0)
                )
                pos_immediate = 1
                if (
                    _dof_keytype_to_immediate(pos_x_keytype) == 0
                    or _dof_keytype_to_immediate(pos_y_keytype) == 0
                ):
                    pos_immediate = 0
                pos_x_val += node_offset_x
                pos_y_val += node_offset_y
                if anim_flip_y:
                    pos_y_val = -pos_y_val
                if center_anchor:
                    pos_x_val += center_offset_x
                    pos_y_val += center_offset_y
                # Scale positions by position_scale to match the coordinate system
                pos_x_val *= position_scale
                pos_y_val *= position_scale
                frame["pos"] = {
                    "immediate": pos_immediate,
                    "x": float(pos_x_val),
                    "y": float(pos_y_val),
                }

            if time in depth_keys:
                depth_val, depth_key = depth_keys[time]
                frame["depth"] = {
                    "immediate": _dof_keytype_to_immediate(depth_key),
                    "value": float(depth_val) + node_offset_z,
                }
            elif default_depth_value is not None and time == first_frame_time:
                frame["depth"] = {
                    "immediate": default_depth_immediate,
                    "value": default_depth_value,
                }

            if time in scale_x_keys or time in scale_y_keys or time in flip_x_keys:
                scale_x_val, scale_x_key = scale_x_keys.get(time, (None, None))
                scale_y_val, scale_y_key = scale_y_keys.get(time, (None, None))
                if scale_x_val is None:
                    scale_x_val = _evaluate_channel_value(scale_x_list, time, 1.0)
                if scale_y_val is None:
                    scale_y_val = _evaluate_channel_value(scale_y_list, time, 1.0)
                if image_scale not in (1.0, 1):
                    scale_x_val *= image_scale
                    scale_y_val *= image_scale
                if flip_x_keys:
                    flip_x_active = _evaluate_channel_value(flip_x_list, time, 0.0) >= 0.5
                    if flip_x_active:
                        scale_x_val = -abs(scale_x_val)
                    else:
                        scale_x_val = abs(scale_x_val)
                elif flip_x and scale_x_val >= 0:
                    scale_x_val = -scale_x_val
                scale_x_keytype = (
                    scale_x_key
                    if scale_x_key is not None
                    else _evaluate_channel_keytype(scale_x_list, time, 0)
                )
                scale_y_keytype = (
                    scale_y_key
                    if scale_y_key is not None
                    else _evaluate_channel_keytype(scale_y_list, time, 0)
                )
                scale_immediate = 1
                if (
                    _dof_keytype_to_immediate(scale_x_keytype) == 0
                    or _dof_keytype_to_immediate(scale_y_keytype) == 0
                ):
                    scale_immediate = 0
                if (
                    scale_immediate == 0
                    and time in flip_x_keys
                    and time not in scale_x_keys
                    and time not in scale_y_keys
                ):
                    scale_immediate = 1
                frame["scale"] = {
                    "immediate": scale_immediate,
                    "x": float(scale_x_val) * 100.0,
                    "y": float(scale_y_val) * 100.0,
                }

            if time in rot_keys:
                rot_val, rot_key = rot_keys[time]
                rot_deg = float(rot_val) * (180.0 / 3.141592653589793)
                if effective_invert_rotation:
                    rot_deg = -rot_deg
                frame["rotation"] = {
                    "immediate": _dof_keytype_to_immediate(rot_key),
                    "value": rot_deg,
                }

            if time in opacity_keys:
                op_val, op_key = opacity_keys[time]
                frame["opacity"] = {
                    "immediate": _dof_keytype_to_immediate(op_key),
                    "value": float(op_val),
                }

            if time in sprite_keys:
                sprite_index, sprite_key = sprite_keys[time]
                sprite_name = _resolve_sprite_name_safe(
                    sprite_index, mapping, image_names, default_sprite_name
                )
                frame["sprite"] = {
                    "immediate": _dof_keytype_to_immediate(sprite_key),
                    "string": sprite_name,
                }

            if time in r_keys or time in g_keys or time in b_keys:
                r_val, r_key = r_keys.get(time, (None, None))
                g_val, g_key = g_keys.get(time, (None, None))
                b_val, b_key = b_keys.get(time, (None, None))
                if r_val is None:
                    r_val = _evaluate_channel_value(r_list, time, 1.0)
                if g_val is None:
                    g_val = _evaluate_channel_value(g_list, time, 1.0)
                if b_val is None:
                    b_val = _evaluate_channel_value(b_list, time, 1.0)
                r_keytype = (
                    r_key if r_key is not None else _evaluate_channel_keytype(r_list, time, 0)
                )
                g_keytype = (
                    g_key if g_key is not None else _evaluate_channel_keytype(g_list, time, 0)
                )
                b_keytype = (
                    b_key if b_key is not None else _evaluate_channel_keytype(b_list, time, 0)
                )
                rgb_immediate = 1
                if (
                    _dof_keytype_to_immediate(r_keytype) == 0
                    or _dof_keytype_to_immediate(g_keytype) == 0
                    or _dof_keytype_to_immediate(b_keytype) == 0
                ):
                    rgb_immediate = 0
                frame["rgb"] = {
                    "immediate": rgb_immediate,
                    "red": int(round(float(r_val) * 255)),
                    "green": int(round(float(g_val) * 255)),
                    "blue": int(round(float(b_val) * 255)),
                }

            frames.append(frame)

        layer_payload = {
            "name": name,
            "type": 1,
            "blend": int(blend_value),
            "parent": -1,
            "id": len(layers),
            "src": 0,
            "width": 0,
            "height": 0,
            "anchor_x": float(anchor_x),
            "anchor_y": float(anchor_y),
            "unk": "",
            "shader": shader_name,
            "frames": frames,
            "sprite_anchor_map": sprite_anchor_map,
            "_depth": depth_value,
            "_order": node_index,
        }
        if not shader_name:
            layer_payload.pop("shader", None)
        layers.append(layer_payload)

    layers.sort(key=lambda layer: (-layer.get("_depth", 0.0), layer.get("_order", 0)))
    for idx, layer in enumerate(layers):
        layer["id"] = idx
        layer.pop("_depth", None)
        layer.pop("_order", None)

    blend_version = 2 if any(layer.get("blend", 0) >= 2 for layer in layers) else 1

    output_json = output_dir / f"{anim_name}.json"
    payload = {
        "rev": 6,
        "blend_version": blend_version,
        "sources": [{"src": atlas_xml.name, "id": 0, "width": 0, "height": 0}],
        "anims": [
            {
                "name": anim_name,
                "width": anim_width,
                "height": anim_height,
                "loop_offset": -1.0,
                "centered": 1,
                "layers": layers,
            }
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_json


def convert_anim_bundle(
    bundle_root: Path,
    anim_name: str,
    output_dir: Path,
    flip_y: bool,
    invert_rotation: bool,
    center_anchor: bool,
    position_scale: float,
    mesh_use_offset: bool = True,
    include_mesh_xml: bool = False,
    premultiply_alpha: bool = False,
    alpha_hardness: float = 0.0,
    dof_anchor_offset: bool = False,
    dof_anchor_center: bool = False,
    swap_anchor_report: bool = False,
    swap_anchor_report_path: Optional[Path] = None,
    swap_anchor_edge_align: bool = False,
    swap_anchor_pivot_offset: bool = False,
    swap_anchor_report_override: bool = False,
    swap_anchor_report_override_path: Optional[Path] = None,
    hires_override: Optional[bool] = None,
) -> Path:
    global _LAST_SPRITE_DEFS, _LAST_ALPHA_DEBUG
    if UnityPy is None:
        raise RuntimeError("UnityPy is required for bundle-based DOF conversion.")
    anim_flip_y = bool(flip_y)
    sprite_flip_y = False
    target_name = anim_name
    if not target_name.lower().endswith(".animbbb"):
        target_name = f"{target_name}.ANIMBBB"
    anim_obj = None
    anim_data = None
    env = None

    cached_bundle = _load_bundle_path_from_cache(bundle_root, target_name)
    bundle_paths: List[Path] = []
    if cached_bundle:
        # Fast path: try the known bundle first and avoid scanning the whole tree.
        bundle_paths = [cached_bundle]
    else:
        bundle_paths = _resolve_bundle_paths(bundle_root)
        if not bundle_paths:
            raise FileNotFoundError(f"No Unity bundles found under: {bundle_root}")

    def _scan_paths(paths: List[Path]) -> bool:
        nonlocal env, anim_obj, anim_data
        for bundle_path in paths:
            try:
                env = UnityPy.load(str(bundle_path))
            except Exception:
                continue
            for obj in env.objects:
                if getattr(obj.type, "name", None) != "MonoBehaviour":
                    continue
                try:
                    data = obj.read()
                    name = getattr(data, "m_Name", "") or ""
                except Exception:
                    continue
                if name == target_name or name.endswith(target_name):
                    anim_obj = obj
                    anim_data = data
                    _update_bundle_path_cache(bundle_root, target_name, bundle_path)
                    return True
        return False

    if cached_bundle:
        found = _scan_paths(bundle_paths)
    else:
        bundle_paths = _prioritize_bundle_paths_for_anim(bundle_paths, target_name)
        found = _scan_paths(bundle_paths)
    if not found and cached_bundle:
        # Cache miss/stale entry: fall back to full scan and keep behavior robust.
        full_paths = _resolve_bundle_paths(bundle_root)
        if not full_paths:
            raise FileNotFoundError(f"No Unity bundles found under: {bundle_root}")
        if cached_bundle in full_paths:
            full_paths = [cached_bundle] + [p for p in full_paths if p != cached_bundle]
        full_paths = _prioritize_bundle_paths_for_anim(full_paths, target_name)
        found = _scan_paths(full_paths)
    if anim_obj is None or anim_data is None:
        raise FileNotFoundError(f"ANIMBBB not found in bundles: {target_name}")

    mono = anim_obj.read_typetree()
    node_mappings_raw = [""] * len(mono.get("Nodes", []) or [])

    images = getattr(anim_data, "Images", None) or []
    image_names: List[str] = []
    sprite_defs: Dict[str, Dict[str, Any]] = {}
    texture_obj = None
    alpha_obj = None
    alpha_candidates: List[str] = []
    alpha_candidate_scores: List[Dict[str, Any]] = []
    alpha_selected_flip_x = False
    alpha_selected_flip_y = False
    alpha_channel_override = None

    for image in images:
        sprite_ptr = getattr(image, "sprite", None)
        if sprite_ptr is None:
            image_names.append("")
            continue
        try:
            sprite_obj = sprite_ptr.read()
        except Exception:
            sprite_obj = None
        if sprite_obj is None:
            image_names.append("")
            continue
        if texture_obj is None:
            try:
                rd = getattr(sprite_obj, "m_RD", None)
                tex_ptr = getattr(rd, "texture", None)
                texture_obj = tex_ptr.read() if tex_ptr else None
                alpha_ptr = getattr(rd, "alphaTexture", None)
                if alpha_ptr and getattr(alpha_ptr, "path_id", 0):
                    alpha_obj = alpha_ptr.read()
            except Exception:
                pass
            if texture_obj is not None:
                alpha_candidates = _alpha_name_candidates(getattr(texture_obj, "m_Name", "") or "")
        sprite_data = _parse_sprite_unitypy(
            sprite_obj, None, sprite_flip_y, False, mesh_use_offset
        )
        sprite_data["offset_anchor_mode"] = "pivot_offset"
        sprite_name = Path(sprite_data["name"]).name
        sprite_defs[sprite_name] = sprite_data
        image_names.append(sprite_name)
    _LAST_SPRITE_DEFS = dict(sprite_defs)

    if texture_obj is None:
        raise FileNotFoundError("Texture not found for bundle animation.")
    if alpha_obj is None and alpha_candidates:
        alpha_options: List[Any] = []
        alpha_options.extend(_find_textures_in_env(env, alpha_candidates))
        if bundle_root:
            for tex in _find_textures_in_bundles(bundle_root, alpha_candidates):
                name = (getattr(tex, "m_Name", "") or "").lower()
                if name and all((getattr(existing, "m_Name", "") or "").lower() != name for existing in alpha_options):
                    alpha_options.append(tex)
        if alpha_options:
            base_size = texture_obj.image.size if getattr(texture_obj, "image", None) is not None else (0, 0)
            scored: List[Tuple[float, bool, bool, Any, Any]] = []
            for option in alpha_options:
                option_img = getattr(option, "image", None)
                if option_img is None:
                    continue
                transforms = [
                    (False, False, option_img),
                    (
                        True,
                        False,
                        option_img.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
                    ),
                    (
                        False,
                        True,
                        option_img.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
                    ),
                    (
                        True,
                        True,
                        option_img.transpose(Image.Transpose.FLIP_LEFT_RIGHT).transpose(
                            Image.Transpose.FLIP_TOP_BOTTOM
                        ),
                    ),
                ]
                best_score = float("-inf")
                best_flip_x = False
                best_flip_y = False
                best_alpha_channel = None
                for flip_x, flip_y, transformed in transforms:
                    score = _alpha_overlay_score(transformed, base_size, list(sprite_defs.values()))
                    if score > best_score:
                        best_score = score
                        best_flip_x = flip_x
                        best_flip_y = flip_y
                        try:
                            channel = transformed.convert("RGBA").getchannel("A")
                            if channel.size != base_size:
                                channel = channel.resize(base_size, Image.Resampling.NEAREST)
                            best_alpha_channel = channel
                        except Exception:
                            best_alpha_channel = None
                if best_alpha_channel is not None:
                    scored.append((best_score, best_flip_x, best_flip_y, option, best_alpha_channel))
            if scored:
                scored.sort(key=lambda pair: pair[0], reverse=True)
                alpha_candidate_scores = [
                    {
                        "name": getattr(option, "m_Name", "") or "",
                        "score": float(score),
                        "flip_x": bool(flip_x),
                        "flip_y": bool(flip_y),
                        "size": list(getattr(option, "image", None).size)
                        if getattr(option, "image", None) is not None
                        else [0, 0],
                    }
                    for score, flip_x, flip_y, option, _channel in scored
                ]
                alpha_selected_flip_x = bool(scored[0][1])
                alpha_selected_flip_y = bool(scored[0][2])
                alpha_obj = scored[0][3]
                # Keep a single globally selected alpha texture/orientation.
                # Per-sprite composite picking caused opaque rectangular chunks
                # on some layers (e.g. Z02 keyboard halves) when a local winner
                # had higher mean alpha but incorrect cutout geometry.
                top_channels = [
                    ((getattr(option, "m_Name", "") or ""), channel)
                    for _score, _fx, _fy, option, channel in scored[:2]
                ]
                _merged_alpha_unused, winner_counts = _compose_alpha_from_candidates(
                    top_channels,
                    sprite_defs,
                    base_size,
                )
                if winner_counts:
                    for entry in alpha_candidate_scores:
                        name = entry.get("name") or ""
                        entry["sprite_wins"] = int(winner_counts.get(str(name), 0))
            else:
                alpha_obj = alpha_options[0]
        if alpha_obj is not None:
            alpha_name = getattr(alpha_obj, "m_Name", "") or ""
            if alpha_name:
                if alpha_selected_flip_x and alpha_selected_flip_y:
                    flip_suffix = " (flipXY)"
                elif alpha_selected_flip_x:
                    flip_suffix = " (flipX)"
                elif alpha_selected_flip_y:
                    flip_suffix = " (flipY)"
                else:
                    flip_suffix = ""
                print(f"Using alpha texture: {alpha_name}{flip_suffix}")

    size = mono.get("size", {}) or {}
    anim_width = int(round(float(size.get("x", 0) or 0)))
    anim_height = int(round(float(size.get("y", 0) or 0)))
    anim_base = _strip_suffix(Path(target_name).stem, ".ANIMBBB")
    anim_name_out = anim_base or Path(target_name).stem
    end_time = mono.get("EndTime", None)

    atlas_base = anim_base or "atlas"
    output_is_xml_resources = output_dir.name.lower() == "xml_resources"
    if output_is_xml_resources:
        atlas_filename = f"{atlas_base}.png"
    else:
        atlas_filename = f"{atlas_base}_texture.png"
    atlas_png = output_dir / atlas_filename
    atlas_xml = output_dir / f"{atlas_base}.xml"
    image_path = atlas_png.name
    texture_output = atlas_png
    if output_is_xml_resources:
        data_root = output_dir.parent
        gfx_monsters = data_root / "gfx" / "monsters"
        gfx_monsters.mkdir(parents=True, exist_ok=True)
        texture_output = gfx_monsters / atlas_filename
        image_path = f"gfx/monsters/{atlas_filename}"
    image_size = _build_texture_from_unitypy(
        texture_obj,
        alpha_obj,
        texture_output,
        list(sprite_defs.values()),
        alpha_flip_x=alpha_selected_flip_x,
        alpha_flip_y=alpha_selected_flip_y,
        alpha_channel_override=alpha_channel_override,
        premultiply_alpha=premultiply_alpha,
        alpha_hardness=alpha_hardness,
    )
    atlas_flip_y = _detect_atlas_flip_y(texture_output, list(sprite_defs.values()))

    # Re-parse sprites now that atlas size is known.
    sprite_defs = {}
    image_names = []
    for image in images:
        sprite_ptr = getattr(image, "sprite", None)
        if sprite_ptr is None:
            image_names.append("")
            continue
        try:
            sprite_obj = sprite_ptr.read()
        except Exception:
            sprite_obj = None
        if sprite_obj is None:
            image_names.append("")
            continue
        sprite_data = _parse_sprite_unitypy(
            sprite_obj, image_size, sprite_flip_y, atlas_flip_y, mesh_use_offset
        )
        sprite_data["offset_anchor_mode"] = "pivot_offset"
        sprite_name = Path(sprite_data["name"]).name
        sprite_defs[sprite_name] = sprite_data
        image_names.append(sprite_name)
    _LAST_SPRITE_DEFS = dict(sprite_defs)
    _LAST_ALPHA_DEBUG = {}
    if alpha_obj is not None and getattr(alpha_obj, "image", None) is not None:
        chosen_alpha_name = getattr(alpha_obj, "m_Name", "") or ""
        report = _build_alpha_coverage_report(
            texture_obj.image,
            alpha_obj.image,
            sprite_defs,
            chosen_alpha_name,
            alpha_selected_flip_x,
            alpha_selected_flip_y,
            alpha_candidate_scores,
        )
        if report:
            _LAST_ALPHA_DEBUG = report

    swap_override_by_index: Dict[int, Tuple[str, str]] = {}
    swap_override_by_name: Dict[str, Tuple[str, str]] = {}
    if swap_anchor_report_override:
        override_path = _resolve_swap_anchor_report_path(
            output_dir,
            anim_name_out,
            swap_anchor_report_override_path,
        )
        if override_path.exists():
            try:
                override_payload = json.loads(override_path.read_text(encoding="utf-8"))
                swap_override_by_index = {
                    int(k): tuple(v)  # type: ignore[arg-type]
                    for k, v in override_payload.get("override_by_index", {}).items()
                }
                swap_override_by_name = {
                    str(k): tuple(v)  # type: ignore[arg-type]
                    for k, v in override_payload.get("override_by_name", {}).items()
                }
                print(f"Swap anchor override: {override_path}")
            except Exception as exc:
                print(f"Swap anchor override failed: {exc}")
        else:
            print(f"Swap anchor override missing: {override_path}")

    nodes = mono.get("Nodes", []) or []
    particle_nodes = mono.get("ParticleNodes", []) or []
    properties = mono.get("Properties", {}) or {}

    hires_value = hires_override if hires_override is not None else output_is_xml_resources
    _write_atlas_xml(
        atlas_xml,
        image_path,
        image_size,
        list(sprite_defs.values()),
        atlas_flip_y,
        mesh_sprite_names=None,
        position_scale=position_scale,
        hires=hires_value,
        include_mesh=bool(include_mesh_xml),
    )

    anim_json = _build_anim_json(
        anim_name_out,
        anim_width,
        anim_height,
        nodes,
        particle_nodes,
        properties,
        image_names,
        sprite_defs,
        node_mappings_raw,
        anim_flip_y,
        invert_rotation,
        sprite_flip_y,
        center_anchor,
        position_scale,
        mesh_use_offset,
        dof_anchor_offset,
        dof_anchor_center,
        swap_anchor_report,
        swap_anchor_report_path,
        swap_anchor_edge_align,
        swap_anchor_pivot_offset,
        True,
        swap_override_by_index,
        swap_override_by_name,
        end_time,
        lambda _guid: None,
        output_dir,
        atlas_xml.name,
        0,
        0,
    )

    output_json = output_dir / f"{anim_name_out}.json"
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(anim_json, ensure_ascii=True, indent=2), encoding="utf-8")
    return output_json


def _iter_anim_assets(path: Path) -> Iterable[Path]:
    if path.is_dir():
        yield from path.rglob("*.ANIMBBB.asset")
        yield from path.rglob("*.animbbb.asset")
    else:
        yield path


def _resolve_bundle_paths(bundle_root: Path) -> List[Path]:
    if bundle_root.is_file():
        return [bundle_root]
    bundle_paths: List[Path] = []
    for root, _, files in os.walk(bundle_root):
        if "__data" not in files:
            continue
        candidate = Path(root) / "__data"
        try:
            with candidate.open("rb") as handle:
                magic = handle.read(8)
        except OSError:
            continue
        if magic.startswith((b"UnityFS", b"UnityRaw", b"UnityWeb")):
            bundle_paths.append(candidate)
    return bundle_paths


def _load_bundle_path_from_cache(bundle_root: Path, anim_name: str) -> Optional[Path]:
    output_root = bundle_root / "Output"
    cache_path = output_root / "_bundle_index.json"
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    cached_root = payload.get("root")
    if isinstance(cached_root, str):
        root_norm = os.path.normcase(os.path.normpath(str(bundle_root.resolve())))
        cached_norm = os.path.normcase(os.path.normpath(str(Path(cached_root).resolve())))
        if cached_norm != root_norm:
            return None
    else:
        return None
    bundle_map = payload.get("bundle_map", {})
    if not isinstance(bundle_map, dict):
        return None
    entry = bundle_map.get(anim_name)
    if not entry:
        entry = bundle_map.get(anim_name.lower())
    if not entry or not isinstance(entry, str):
        return None
    path = Path(entry)
    return path if path.exists() else None


def _update_bundle_path_cache(bundle_root: Path, anim_name: str, bundle_path: Path) -> None:
    output_root = bundle_root / "Output"
    cache_path = output_root / "_bundle_index.json"
    payload: Dict[str, Any] = {}
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    bundle_map = payload.get("bundle_map")
    if not isinstance(bundle_map, dict):
        bundle_map = {}
    bundle_path_str = str(bundle_path)
    bundle_map[anim_name] = bundle_path_str
    bundle_map[anim_name.lower()] = bundle_path_str
    payload["bundle_map"] = bundle_map
    payload["root"] = str(bundle_root.resolve())
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def _prioritize_bundle_paths_for_anim(bundle_paths: List[Path], anim_name: str) -> List[Path]:
    token = _dof_anim_token(anim_name)
    if not token:
        return bundle_paths
    token_lower = token.lower()
    static_hint = f"staticinfo_{token_lower}"
    preferred: List[Path] = []
    others: List[Path] = []
    for path in bundle_paths:
        path_lower = str(path).lower()
        if static_hint in path_lower or token_lower in path_lower:
            preferred.append(path)
        else:
            others.append(path)
    if preferred:
        return preferred + others
    return bundle_paths


def _dof_anim_token(anim_name: str) -> str:
    stem = Path(anim_name).stem.lower()
    if not stem:
        return ""
    parts = [part for part in stem.split("_") if part]
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}"
    return parts[0] if parts else ""


def _alpha_name_candidates(base_name: str) -> List[str]:
    if not base_name:
        return []
    candidates: List[str] = []
    def _add(name: str) -> None:
        if name and name not in candidates:
            candidates.append(name)

    _add(base_name.replace("_texture", "_alpha"))
    _add(base_name.replace("_texture", "_alphaTexture"))
    _add(base_name.replace("_texture", "_alphatex"))
    _add(base_name.replace("_texture", "_alpha_tex"))
    _add(base_name + "_alpha")
    _add(base_name + "_alphaTexture")
    _add(base_name + "_alphatex")
    _add(base_name.replace("texture", "alpha"))
    # Also try the family-level alpha sheet (e.g. monster_xxx_alpha) when the
    # color sheet is variant-specific (e.g. monster_xxx_adult_texture).
    family_match = re.match(r"^(.*)_[^_]+_texture$", base_name, re.IGNORECASE)
    if family_match:
        family = family_match.group(1)
        _add(family + "_alpha")
        _add(family + "_alphaTexture")
    return candidates


def _find_texture_in_env(env: Any, name_candidates: List[str]) -> Optional[Any]:
    if not env or not name_candidates:
        return None
    candidate_set = {name.lower() for name in name_candidates if name}
    if not candidate_set:
        return None
    for obj in env.objects:
        if getattr(obj.type, "name", None) != "Texture2D":
            continue
        try:
            tex = obj.read()
        except Exception:
            continue
        name = getattr(tex, "m_Name", "") or ""
        if name.lower() in candidate_set:
            return tex
    return None


def _find_textures_in_env(env: Any, name_candidates: List[str]) -> List[Any]:
    if not env or not name_candidates:
        return []
    candidate_set = {name.lower() for name in name_candidates if name}
    if not candidate_set:
        return []
    found: List[Any] = []
    seen: set[str] = set()
    for obj in env.objects:
        if getattr(obj.type, "name", None) != "Texture2D":
            continue
        try:
            tex = obj.read()
        except Exception:
            continue
        name = (getattr(tex, "m_Name", "") or "").lower()
        if name in candidate_set and name not in seen:
            found.append(tex)
            seen.add(name)
    return found


def _find_texture_in_bundles(
    bundle_root: Path,
    name_candidates: List[str],
) -> Optional[Any]:
    if UnityPy is None or not name_candidates:
        return None
    bundle_paths = _resolve_bundle_paths(bundle_root)
    for bundle_path in bundle_paths:
        try:
            env = UnityPy.load(str(bundle_path))
        except Exception:
            continue
        tex = _find_texture_in_env(env, name_candidates)
        if tex is not None:
            return tex
    return None


def _find_textures_in_bundles(
    bundle_root: Path,
    name_candidates: List[str],
) -> List[Any]:
    if UnityPy is None or not name_candidates:
        return []
    found: List[Any] = []
    seen: set[str] = set()
    bundle_paths = _resolve_bundle_paths(bundle_root)
    for bundle_path in bundle_paths:
        try:
            env = UnityPy.load(str(bundle_path))
        except Exception:
            continue
        for tex in _find_textures_in_env(env, name_candidates):
            name = (getattr(tex, "m_Name", "") or "").lower()
            if name and name not in seen:
                found.append(tex)
                seen.add(name)
    return found


def _alpha_overlay_score(
    alpha_img: Any,
    base_size: Tuple[int, int],
    sprites: List[Dict[str, Any]],
) -> float:
    """Score how well an alpha sheet overlays sprite rects (higher is better)."""
    if Image is None:
        return float("-inf")
    try:
        img = alpha_img.convert("RGBA") if getattr(alpha_img, "mode", None) != "RGBA" else alpha_img
        channel = img.getchannel("A")
    except Exception:
        return float("-inf")
    if channel.size != base_size:
        channel = channel.resize(base_size, Image.Resampling.NEAREST)
    width, height = base_size
    if width <= 0 or height <= 0:
        return float("-inf")
    px = channel.load()
    inside_vals: List[int] = []
    for sprite in sprites:
        rect = sprite.get("rect", {}) or {}
        box = _rect_to_texture_box(rect, base_size, rect_y_origin_bottom=True)
        if not box:
            continue
        x0, y0, x1, y1 = box
        bw = x1 - x0
        bh = y1 - y0
        if bw <= 1 or bh <= 1:
            continue
        for sx in (0.3, 0.7):
            for sy in (0.3, 0.7):
                tx = int(x0 + bw * sx)
                ty = int(y0 + bh * sy)
                if tx >= x1:
                    tx = x1 - 1
                if ty >= y1:
                    ty = y1 - 1
                if 0 <= tx < width and 0 <= ty < height:
                    inside_vals.append(int(px[tx, ty]))
    if not inside_vals:
        return float("-inf")
    outside_pts = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (0, height // 2),
        (width - 1, height // 2),
        (width // 2, height - 1),
    ]
    outside_vals = [int(px[x, y]) for x, y in outside_pts]
    inside_mean = sum(inside_vals) / float(len(inside_vals))
    outside_mean = sum(outside_vals) / float(len(outside_vals))
    return inside_mean - outside_mean


def _sprite_rect_alpha_mean(
    alpha_channel: Any,
    rect: Dict[str, Any],
    base_size: Tuple[int, int],
) -> float:
    box = _rect_to_texture_box(rect, base_size, rect_y_origin_bottom=True)
    if not box:
        return 0.0
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return 0.0
    region = alpha_channel.crop((x0, y0, x1, y1))
    hist = region.histogram()
    total = max(1, region.size[0] * region.size[1])
    return sum(val * count for val, count in enumerate(hist)) / float(total)


def _compose_alpha_from_candidates(
    candidate_channels: List[Tuple[str, Any]],
    sprite_defs: Dict[str, Dict[str, Any]],
    base_size: Tuple[int, int],
) -> Tuple[Optional[Any], Dict[str, int]]:
    if Image is None or len(candidate_channels) < 2:
        return None, {}
    width, height = base_size
    if width <= 0 or height <= 0:
        return None, {}
    winner_counts: Dict[str, int] = {name: 0 for name, _ in candidate_channels}
    per_sprite_winner: Dict[str, int] = {}
    for sprite_name, info in sprite_defs.items():
        rect = info.get("rect", {}) or {}
        best_idx = 0
        best_mean = float("-inf")
        for idx, (_, channel) in enumerate(candidate_channels):
            mean = _sprite_rect_alpha_mean(channel, rect, base_size)
            if mean > best_mean:
                best_mean = mean
                best_idx = idx
        per_sprite_winner[sprite_name] = best_idx
        winner_counts[candidate_channels[best_idx][0]] += 1

    if len([count for count in winner_counts.values() if count > 0]) <= 1:
        return None, winner_counts

    merged = Image.new("L", (width, height), 0)
    for sprite_name, idx in per_sprite_winner.items():
        info = sprite_defs.get(sprite_name, {})
        rect = info.get("rect", {}) or {}
        box = _rect_to_texture_box(rect, base_size, rect_y_origin_bottom=True)
        if not box:
            continue
        x0, y0, x1, y1 = box
        if x1 <= x0 or y1 <= y0:
            continue
        channel = candidate_channels[idx][1]
        patch = channel.crop((x0, y0, x1, y1))
        merged.paste(patch, (x0, y0))
    return merged, winner_counts


def _build_alpha_coverage_report(
    base_img: Any,
    alpha_img: Any,
    sprite_defs: Dict[str, Dict[str, Any]],
    chosen_alpha_name: str,
    chosen_alpha_flip_x: bool,
    chosen_alpha_flip_y: bool,
    candidate_scores: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if Image is None:
        return {}
    try:
        base_rgba = base_img.convert("RGBA")
        alpha_rgba = alpha_img.convert("RGBA")
    except Exception:
        return {}

    alpha_channel = alpha_rgba.getchannel("A")
    if chosen_alpha_flip_x:
        alpha_channel = alpha_channel.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if chosen_alpha_flip_y:
        alpha_channel = alpha_channel.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if alpha_channel.size != base_rgba.size:
        alpha_channel = alpha_channel.resize(base_rgba.size, Image.Resampling.NEAREST)

    report: Dict[str, Any] = {
        "chosen_alpha": chosen_alpha_name,
        "chosen_alpha_flip_x": bool(chosen_alpha_flip_x),
        "chosen_alpha_flip_y": bool(chosen_alpha_flip_y),
        "base_size": [int(base_rgba.size[0]), int(base_rgba.size[1])],
        "alpha_size": [int(alpha_rgba.size[0]), int(alpha_rgba.size[1])],
        "candidate_scores": candidate_scores,
        "sprites": [],
    }

    width, height = alpha_channel.size
    for sprite_name, info in sorted(sprite_defs.items(), key=lambda item: item[0]):
        rect = info.get("rect", {}) or {}
        box = _rect_to_texture_box(rect, (width, height), rect_y_origin_bottom=True)
        if not box:
            continue
        x0, y0, x1, y1 = box
        if x1 <= x0 or y1 <= y0:
            continue
        region = alpha_channel.crop((x0, y0, x1, y1))
        hist = region.histogram()
        total = max(1, region.size[0] * region.size[1])
        nonzero = total - int(hist[0])
        full = int(hist[255])
        extrema = region.getextrema()
        mean = sum(val * count for val, count in enumerate(hist)) / float(total)
        cx = x0 + (x1 - x0) // 2
        cy = y0 + (y1 - y0) // 2
        center = int(alpha_channel.getpixel((cx, cy)))
        report["sprites"].append(
            {
                "sprite": sprite_name,
                "rect": {
                    "x": int(rect.get("x", 0) or 0),
                    "y": int(rect.get("y", 0) or 0),
                    "w": int(rect.get("w", 0) or 0),
                    "h": int(rect.get("h", 0) or 0),
                },
                "region": {"x0": x0, "y0": y0, "x1": x1, "y1": y1},
                "alpha_mean": float(mean),
                "alpha_min": int(extrema[0]),
                "alpha_max": int(extrema[1]),
                "alpha_nonzero_ratio": float(nonzero / float(total)),
                "alpha_full_ratio": float(full / float(total)),
                "alpha_center": center,
            }
        )
    return report


def _rect_to_texture_box(
    rect: Dict[str, Any],
    base_size: Tuple[int, int],
    rect_y_origin_bottom: bool,
) -> Optional[Tuple[int, int, int, int]]:
    """Convert sprite rect coordinates into image-space crop box."""
    width, height = base_size
    try:
        x = int(round(float(rect.get("x", 0.0) or 0.0)))
        y = int(round(float(rect.get("y", 0.0) or 0.0)))
        w = int(round(float(rect.get("w", 0.0) or 0.0)))
        h = int(round(float(rect.get("h", 0.0) or 0.0)))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    x0 = max(0, min(width, x))
    x1 = max(0, min(width, x + w))
    if rect_y_origin_bottom:
        y_top = height - y - h
    else:
        y_top = y
    y0 = max(0, min(height, y_top))
    y1 = max(0, min(height, y_top + h))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert DOF ANIMBBB assets to viewer JSON.")
    parser.add_argument("input", help="ANIMBBB.asset file or a directory containing them.")
    parser.add_argument(
        "--output",
        help="Output directory for JSON/XML/PNG (default: input folder).",
    )
    parser.add_argument(
        "--assets-root",
        help="Root Assets directory (default: inferred from input).",
    )
    parser.add_argument(
        "--bundle-root",
        help="Unity bundle root to load (for UnityFS layouts).",
    )
    parser.add_argument(
        "--bundle-anim",
        help="ANIMBBB name to extract from bundles (e.g. Foo.ANIMBBB).",
    )
    parser.add_argument(
        "--position-scale",
        type=float,
        default=2.0,
        help="Scale applied to position keyframes (default: 2.0).",
    )
    parser.add_argument(
        "--no-guid-cache",
        dest="use_guid_cache",
        action="store_false",
        help="Disable on-disk GUID cache for Assets root.",
    )
    parser.add_argument(
        "--refresh-guid-cache",
        dest="refresh_guid_cache",
        action="store_true",
        help="Rebuild the on-disk GUID cache for Assets root.",
    )
    flip_group = parser.add_mutually_exclusive_group()
    flip_group.add_argument(
        "--flip-y",
        dest="flip_y",
        action="store_true",
        help="Flip texture Y axis + pivot to match top-left coordinates.",
    )
    flip_group.add_argument(
        "--no-flip-y",
        dest="flip_y",
        action="store_false",
        help="Disable Y flipping (use Unity's original coordinate space).",
    )
    rot_group = parser.add_mutually_exclusive_group()
    rot_group.add_argument(
        "--invert-rotation",
        dest="invert_rotation",
        action="store_true",
        help="Invert rotation direction for Y-down coordinate systems.",
    )
    rot_group.add_argument(
        "--no-invert-rotation",
        dest="invert_rotation",
        action="store_false",
        help="Keep rotation direction as-authored.",
    )
    anchor_group = parser.add_mutually_exclusive_group()
    anchor_group.add_argument(
        "--anchor-center",
        dest="center_anchor",
        action="store_true",
        help="Anchor sprites to their geometric centers instead of the sprite pivot.",
    )
    anchor_group.add_argument(
        "--anchor-pivot",
        dest="center_anchor",
        action="store_false",
        help="Anchor sprites to their authored pivot (default).",
    )
    parser.add_argument(
        "--mesh-pivot-local",
        dest="mesh_pivot_local",
        action="store_true",
        help="Use pivot-local mesh vertices (ignore Sprite.m_Offset).",
    )
    parser.add_argument(
        "--include-mesh-xml",
        dest="include_mesh_xml",
        action="store_true",
        help="Include vertices/verticesUV/triangles in the atlas XML.",
    )
    parser.add_argument(
        "--strip-mesh-xml",
        dest="strip_mesh_xml",
        action="store_true",
        help="(Deprecated) Omit vertices/verticesUV/triangles from the atlas XML.",
    )
    parser.add_argument(
        "--premultiply-alpha",
        dest="premultiply_alpha",
        action="store_true",
        help="Premultiply atlas alpha when exporting PNGs.",
    )
    parser.add_argument(
        "--alpha-hardness",
        dest="alpha_hardness",
        type=float,
        default=0.0,
        help=(
            "Additional alpha edge hardening after split-alpha resize "
            "(0.0-2.0, default 0.0)."
        ),
    )
    hires_group = parser.add_mutually_exclusive_group()
    hires_group.add_argument(
        "--hires-xml",
        dest="hires_xml",
        action="store_true",
        help="Force hires=\"true\" in the atlas XML.",
    )
    hires_group.add_argument(
        "--no-hires-xml",
        dest="hires_xml",
        action="store_false",
        help="Force hires=\"false\" in the atlas XML.",
    )
    parser.add_argument(
        "--swap-anchor-report",
        dest="swap_anchor_report",
        action="store_true",
        help="Write swap-anchor alignment report JSON next to the output.",
    )
    parser.add_argument(
        "--swap-anchor-report-path",
        dest="swap_anchor_report_path",
        help="Optional path for the swap-anchor report JSON.",
    )
    parser.add_argument(
        "--swap-anchor-edge-align",
        dest="swap_anchor_edge_align",
        action="store_true",
        help="Align swap sprites by best-fit edge/center using mesh bounds.",
    )
    parser.add_argument(
        "--swap-anchor-pivot-offset",
        dest="swap_anchor_pivot_offset",
        action="store_true",
        help="Align swap sprites by Sprite.m_Offset pivot positions (mesh sprites).",
    )
    parser.add_argument(
        "--dof-debug-report",
        dest="dof_debug_report",
        action="store_true",
        help="Write a per-sprite DOF debug report JSON next to the output.",
    )
    parser.add_argument(
        "--swap-anchor-report-override",
        dest="swap_anchor_report_override",
        action="store_true",
        help="Use an existing swap-anchor report to override per-node modes.",
    )
    parser.add_argument(
        "--swap-anchor-report-override-path",
        dest="swap_anchor_report_override_path",
        help="Optional path to the swap-anchor report JSON for overrides.",
    )
    dof_anchor_group = parser.add_mutually_exclusive_group()
    dof_anchor_group.add_argument(
        "--dof-anchor-center",
        dest="dof_anchor_center",
        action="store_true",
        help="Force DOF sprites to center on their anchors (debug alignment).",
    )
    dof_anchor_group.add_argument(
        "--no-dof-anchor-center",
        dest="dof_anchor_center",
        action="store_false",
        help="Disable DOF anchor centering.",
    )
    dof_offset_group = parser.add_mutually_exclusive_group()
    dof_offset_group.add_argument(
        "--dof-anchor-offset",
        dest="dof_anchor_offset",
        action="store_true",
        help="Anchor DOF layers by Sprite.m_Offset and build swap maps from offsets.",
    )
    dof_offset_group.add_argument(
        "--no-dof-anchor-offset",
        dest="dof_anchor_offset",
        action="store_false",
        help="Disable DOF anchor offset mapping.",
    )
    parser.set_defaults(
        flip_y=True,
        invert_rotation=False,
        center_anchor=False,
        use_guid_cache=True,
        refresh_guid_cache=False,
        mesh_pivot_local=True,
        include_mesh_xml=False,
        premultiply_alpha=False,
        swap_anchor_report=False,
        swap_anchor_edge_align=False,
        swap_anchor_pivot_offset=False,
        swap_anchor_report_override=False,
        dof_anchor_offset=False,
        dof_anchor_center=False,
        dof_debug_report=False,
        hires_xml=None,
    )

    args = parser.parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output) if args.output else None
    assets_root = Path(args.assets_root) if args.assets_root else _find_assets_root(input_path)

    bundle_root = Path(args.bundle_root) if args.bundle_root else None
    bundle_anim = args.bundle_anim
    if isinstance(args.input, str) and args.input.lower().startswith("bundle://"):
        bundle_anim = args.input.split("bundle://", 1)[1].strip()
        bundle_root = bundle_root or assets_root

    if bundle_root is None and not input_path.exists():
        print(f"Input not found: {input_path}")
        return 1

    converted: List[Path] = []
    swap_anchor_report = bool(args.swap_anchor_report or args.swap_anchor_report_path)
    report_path = Path(args.swap_anchor_report_path) if args.swap_anchor_report_path else None
    mesh_use_offset = not bool(args.mesh_pivot_local)
    swap_anchor_report_override = bool(
        args.swap_anchor_report_override or args.swap_anchor_report_override_path
    )
    override_path = (
        Path(args.swap_anchor_report_override_path)
        if args.swap_anchor_report_override_path
        else None
    )
    include_mesh_xml = bool(args.include_mesh_xml)
    if bool(args.strip_mesh_xml):
        include_mesh_xml = False
    premultiply_alpha = bool(args.premultiply_alpha)
    try:
        alpha_hardness = float(args.alpha_hardness)
    except (TypeError, ValueError):
        alpha_hardness = 0.0
    alpha_hardness = max(0.0, min(2.0, alpha_hardness))
    hires_override = args.hires_xml if args.hires_xml is not None else None

    if bundle_root is not None and bundle_anim:
        target_dir = output_dir or input_path.parent
        try:
            output_json = convert_anim_bundle(
                bundle_root,
                bundle_anim,
                target_dir,
                args.flip_y,
                args.invert_rotation,
                args.center_anchor,
                args.position_scale,
                mesh_use_offset,
                include_mesh_xml,
                premultiply_alpha,
                alpha_hardness,
                bool(args.dof_anchor_offset),
                args.dof_anchor_center,
                swap_anchor_report,
                report_path,
                bool(args.swap_anchor_edge_align),
                bool(args.swap_anchor_pivot_offset),
                swap_anchor_report_override,
                override_path,
                hires_override,
            )
            converted.append(output_json)
            print(f"Converted: {output_json}")
            if args.dof_debug_report and output_json.exists():
                try:
                    debug_dir = output_json.parent
                    debug_name = output_json.stem
                    _write_dof_debug_report(
                        debug_dir,
                        debug_name,
                        globals().get("_LAST_SPRITE_DEFS", {}),
                        False,
                        bool(args.dof_anchor_offset),
                        bool(args.dof_anchor_center),
                    )
                except Exception as exc:
                    print(f"DOF debug report failed: {exc}")
        except Exception as exc:
            print(f"Failed: bundle {bundle_anim} -> {exc}")
    else:
        for anim_path in _iter_anim_assets(input_path):
            if not anim_path.exists():
                continue
            target_dir = output_dir or anim_path.parent
            try:
                output_json = convert_anim(
                    anim_path,
                    target_dir,
                    assets_root,
                    args.flip_y,
                    args.invert_rotation,
                    args.center_anchor,
                    args.position_scale,
                    args.use_guid_cache,
                    args.refresh_guid_cache,
                    mesh_use_offset,
                    include_mesh_xml,
                    premultiply_alpha,
                    alpha_hardness,
                    bool(args.dof_anchor_offset),
                    args.dof_anchor_center,
                    swap_anchor_report,
                    report_path,
                    bool(args.swap_anchor_edge_align),
                    bool(args.swap_anchor_pivot_offset),
                    swap_anchor_report_override,
                    override_path,
                    hires_override,
                )
                converted.append(output_json)
                print(f"Converted: {output_json}")
                if args.dof_debug_report and output_json.exists():
                    try:
                        debug_dir = output_json.parent
                        debug_name = output_json.stem
                        _write_dof_debug_report(
                            debug_dir,
                            debug_name,
                            globals().get("_LAST_SPRITE_DEFS", {}),
                            False,
                            bool(args.dof_anchor_offset),
                            bool(args.dof_anchor_center),
                        )
                    except Exception as exc:
                        print(f"DOF debug report failed: {exc}")
            except Exception as exc:
                print(f"Failed: {anim_path} -> {exc}")
    if not converted:
        print("No animations converted.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
