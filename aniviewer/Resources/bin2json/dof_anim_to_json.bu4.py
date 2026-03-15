"""
DOF ANIMBBB -> MSM Animation Viewer JSON converter.

Converts a Down of the Fare .ANIMBBB.asset into the viewer's JSON format
and generates a TexturePacker-style XML + atlas PNG.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import xml.etree.ElementTree as ET

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover - CLI error path
    yaml = None

try:
    from PIL import Image
except Exception as exc:  # pragma: no cover - CLI error path
    Image = None  # type: ignore


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


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not yaml:
        raise RuntimeError("PyYAML is required for DOF conversion.")
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=UnityYamlLoader)
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


def _build_guid_map(folder: Path) -> Dict[str, Path]:
    guid_map: Dict[str, Path] = {}
    for meta_path in folder.rglob("*.meta"):
        guid = _read_guid_from_meta(meta_path)
        if not guid:
            continue
        asset_path = meta_path.with_suffix("")
        guid_map[guid] = asset_path
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


def _parse_sprite_asset(sprite_asset_path: Path) -> Dict[str, Any]:
    data = _load_yaml(sprite_asset_path)
    sprite = data.get("Sprite", {})
    rect = sprite.get("m_Rect", {}) or {}
    render_data = sprite.get("m_RD", {}) or {}
    texture_rect = render_data.get("textureRect", {}) or rect
    texture_rect_offset = render_data.get("textureRectOffset", {}) or {}
    pivot = sprite.get("m_Pivot", {}) or {}
    try:
        rect_w = float(texture_rect.get("width", rect.get("width", 0)))
        rect_h = float(texture_rect.get("height", rect.get("height", 0)))
    except (TypeError, ValueError):
        rect_w = 0.0
        rect_h = 0.0
    try:
        trim_offset_x = float(texture_rect_offset.get("x", 0))
        trim_offset_y = float(texture_rect_offset.get("y", 0))
    except (TypeError, ValueError):
        trim_offset_x = 0.0
        trim_offset_y = 0.0
    original_w = rect_w + 2.0 * abs(trim_offset_x)
    original_h = rect_h + 2.0 * abs(trim_offset_y)
    mesh_anchor_zero = False
    mesh_bounds = _extract_mesh_bounds(sprite, render_data)
    if mesh_bounds:
        min_x, min_y, max_x, max_y = mesh_bounds
        trim_offset_x = min_x
        trim_offset_y = min_y
        original_w = max_x - min_x
        original_h = max_y - min_y
        mesh_anchor_zero = True
    return {
        "name": sprite.get("m_Name", sprite_asset_path.stem),
        "rect": {
            "x": float(texture_rect.get("x", rect.get("x", 0))),
            "y": float(texture_rect.get("y", rect.get("y", 0))),
            "w": rect_w,
            "h": rect_h,
        },
        "pivot": {
            "x": float(pivot.get("x", 0.5)),
            "y": float(pivot.get("y", 0.5)),
        },
        "trim": {
            "x": trim_offset_x,
            "y": trim_offset_y,
            "w": original_w,
            "h": original_h,
        },
        "mesh_anchor_zero": mesh_anchor_zero,
    }


def _extract_mesh_bounds(
    sprite: Dict[str, Any],
    render_data: Dict[str, Any],
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
    offset_info = sprite.get("m_Offset", {}) or {}
    try:
        offset_x = float(offset_info.get("x", 0.0))
        offset_y = float(offset_info.get("y", 0.0))
    except (TypeError, ValueError):
        offset_x = 0.0
        offset_y = 0.0
    xs = [(pos[0] * ppu) + offset_x for pos in positions]
    ys = [(pos[1] * ppu) + offset_y for pos in positions]
    return min(xs), min(ys), max(xs), max(ys)


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


def _build_texture(
    texture_path: Path,
    alpha_path: Optional[Path],
    output_path: Path,
) -> Tuple[int, int]:
    if Image is None:
        raise RuntimeError("Pillow is required for DOF conversion.")
    base_img = Image.open(texture_path).convert("RGBA")
    alpha_applied = False
    if alpha_path and alpha_path.exists():
        alpha_img = Image.open(alpha_path)
        if "A" in alpha_img.getbands():
            alpha_channel = alpha_img.getchannel("A")
        else:
            alpha_channel = alpha_img.convert("L")
        if alpha_channel.size != base_img.size:
            alpha_channel = alpha_channel.resize(base_img.size, Image.Resampling.NEAREST)
        alpha_min, alpha_max = alpha_channel.getextrema()
        if alpha_max == 0 and alpha_img.mode in ("RGB", "RGBA"):
            fallback = alpha_img.convert("L")
            if fallback.size != base_img.size:
                fallback = fallback.resize(base_img.size, Image.Resampling.NEAREST)
            fmin, fmax = fallback.getextrema()
            if fmax > 0:
                alpha_channel = fallback
                alpha_min, alpha_max = fmin, fmax
        if alpha_max > 0:
            base_img.putalpha(alpha_channel)
            alpha_applied = True
    if not alpha_applied:
        base_img = base_img.convert("RGBA")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base_img.save(output_path)
    return base_img.size


def _write_atlas_xml(
    xml_path: Path,
    image_path: str,
    image_size: Tuple[int, int],
    sprites: List[Dict[str, Any]],
    flip_y: bool,
) -> None:
    root = ET.Element(
        "TextureAtlas",
        {
            "imagePath": image_path,
            "width": str(int(image_size[0])),
            "height": str(int(image_size[1])),
            "hires": "false",
        },
    )
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
        if sprite.get("mesh_anchor_zero"):
            pivot_x = 0.5
            pivot_y = 0.5
        pivot_y = 1.0 - pivot_y if flip_y else pivot_y
        trim = sprite.get("trim", {})
        o_w = int(round(trim.get("w", w)))
        o_h = int(round(trim.get("h", h)))
        o_x = int(round(trim.get("x", 0.0)))
        o_y = int(round(trim.get("y", 0.0)))
        attrib = {
            "n": sprite["name"],
            "x": str(x),
            "y": str(y),
            "w": str(w),
            "h": str(h),
            "pX": f"{pivot_x:.6f}",
            "pY": f"{pivot_y:.6f}",
            "oX": str(o_x),
            "oY": str(o_y),
            "oW": str(o_w),
            "oH": str(o_h),
        }
        ET.SubElement(root, "sprite", attrib)

    tree = ET.ElementTree(root)
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)


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


def convert_anim(
    anim_path: Path,
    output_dir: Path,
    assets_root: Optional[Path],
    flip_y: bool,
    invert_rotation: bool,
    center_anchor: bool,
    position_scale: float,
) -> Path:
    data = _load_yaml(anim_path)
    mono = data.get("MonoBehaviour", {})
    raw_text = anim_path.read_text(encoding="utf-8")
    node_mappings_raw = _extract_node_mappings(raw_text)

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
                fallback_map = _build_guid_map(assets_root)
            return fallback_map.get(guid)
        return None

    texture_path = resolve_guid(tex_guid)
    alpha_path = resolve_guid(alpha_guid)
    if not texture_path or not texture_path.exists():
        raise FileNotFoundError("Texture PNG not found for TPP asset.")

    atlas_base = _strip_suffix(tpp_path.stem, ".TPP")
    atlas_filename = f"{atlas_base}_texture.png"
    atlas_png = output_dir / atlas_filename
    atlas_xml = output_dir / f"{atlas_base}.xml"
    image_path = atlas_png.name
    texture_output = atlas_png
    if output_dir.name.lower() == "xml_resources":
        data_root = output_dir.parent
        gfx_monsters = data_root / "gfx" / "monsters"
        if gfx_monsters.is_dir():
            texture_output = gfx_monsters / atlas_filename
            image_path = f"gfx/monsters/{atlas_filename}"
    image_size = _build_texture(texture_path, alpha_path, texture_output)

    sprite_defs: Dict[str, Dict[str, Any]] = {}
    image_names: List[str] = []
    for guid in image_guids:
        asset_path = resolve_guid(guid)
        if not asset_path:
            image_names.append("")
            continue
        sprite_data = _parse_sprite_asset(asset_path)
        sprite_name = Path(sprite_data["name"]).stem
        sprite_defs[sprite_name] = sprite_data
        image_names.append(sprite_name)

    unique_sprites = []
    seen_names = set()
    for name in image_names:
        if name and name in sprite_defs and name not in seen_names:
            unique_sprites.append(sprite_defs[name])
            seen_names.add(name)

    _write_atlas_xml(atlas_xml, image_path, image_size, unique_sprites, flip_y)

    layers: List[Dict[str, Any]] = []
    nodes = mono.get("Nodes", []) or []
    for node_index, node in enumerate(nodes):
        if node.get("NodeType") != 1:
            continue
        name = node.get("Name", f"Layer_{node_index}")
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
        }

        mapping_hex = node_mappings_raw[node_index] if node_index < len(node_mappings_raw) else ""
        mapping = _parse_hex_mapping(mapping_hex) if mapping_hex else None

        sprite_keys = _channel_key_map(channel_map["sprite"])
        default_sprite_name = ""
        if sprite_keys:
            first_time = min(sprite_keys.keys())
            sprite_index = int(round(sprite_keys[first_time][0]))
            default_sprite_name = _resolve_sprite_name(sprite_index, mapping, image_names)

        anchor_x = 0.0
        anchor_y = 0.0
        center_offset_x = 0.0
        center_offset_y = 0.0
        if default_sprite_name and default_sprite_name in sprite_defs:
            sprite_info = sprite_defs[default_sprite_name]
            rect = sprite_info["rect"]
            pivot = sprite_info["pivot"]
            trim = sprite_info.get("trim", {})
            if sprite_info.get("mesh_anchor_zero"):
                anchor_x = 0.0
                anchor_y = 0.0
            else:
                pivot_y = 1.0 - pivot["y"] if flip_y else pivot["y"]
                if center_anchor:
                    anchor_x = float(trim.get("x", 0.0)) + rect["w"] * 0.5
                    anchor_y = float(trim.get("y", 0.0)) + rect["h"] * 0.5
                    center_offset_x = rect["w"] * (pivot["x"] - 0.5)
                    center_offset_y = rect["h"] * (pivot_y - 0.5)
                else:
                    anchor_x = float(trim.get("x", 0.0)) + rect["w"] * pivot["x"]
                    anchor_y = float(trim.get("y", 0.0)) + rect["h"] * pivot_y

        key_times = _collect_times(channel_map.values())
        frames: List[Dict[str, Any]] = []

        pos_x_keys = _channel_key_map(channel_map["pos_x"])
        pos_y_keys = _channel_key_map(channel_map["pos_y"])
        scale_x_keys = _channel_key_map(channel_map["scale_x"])
        scale_y_keys = _channel_key_map(channel_map["scale_y"])
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
        r_list = _sorted_key_list(r_keys)
        g_list = _sorted_key_list(g_keys)
        b_list = _sorted_key_list(b_keys)

        depth_value = 0.0
        if depth_keys:
            first_time = min(depth_keys.keys())
            depth_value = float(depth_keys[first_time][0])

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
                if flip_y:
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

            if time in scale_x_keys or time in scale_y_keys:
                scale_x_val, scale_x_key = scale_x_keys.get(time, (None, None))
                scale_y_val, scale_y_key = scale_y_keys.get(time, (None, None))
                if scale_x_val is None:
                    scale_x_val = _evaluate_channel_value(scale_x_list, time, 1.0)
                if scale_y_val is None:
                    scale_y_val = _evaluate_channel_value(scale_y_list, time, 1.0)
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
                frame["scale"] = {
                    "immediate": scale_immediate,
                    "x": float(scale_x_val) * 100.0,
                    "y": float(scale_y_val) * 100.0,
                }

            if time in rot_keys:
                rot_val, rot_key = rot_keys[time]
                rot_deg = float(rot_val) * (180.0 / 3.141592653589793)
                if invert_rotation:
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
                sprite_index = int(round(sprite_index))
                sprite_name = _resolve_sprite_name(sprite_index, mapping, image_names)
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

        layers.append(
            {
                "name": name,
                "type": 1,
                "blend": 0,
                "parent": -1,
                "id": len(layers),
                "src": 0,
                "width": 0,
                "height": 0,
                "anchor_x": float(anchor_x),
                "anchor_y": float(anchor_y),
                "unk": "",
                "frames": frames,
                "_depth": depth_value,
                "_order": node_index,
            }
        )

    layers.sort(key=lambda layer: (-layer.get("_depth", 0.0), layer.get("_order", 0)))
    for idx, layer in enumerate(layers):
        layer["id"] = idx
        layer.pop("_depth", None)
        layer.pop("_order", None)

    output_json = output_dir / f"{anim_name}.json"
    payload = {
        "rev": 6,
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


def _iter_anim_assets(path: Path) -> Iterable[Path]:
    if path.is_dir():
        yield from path.rglob("*.ANIMBBB.asset")
        yield from path.rglob("*.animbbb.asset")
    else:
        yield path


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
        "--position-scale",
        type=float,
        default=2.0,
        help="Scale applied to position keyframes (default: 2.0).",
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
    parser.set_defaults(flip_y=True, invert_rotation=True, center_anchor=False)

    args = parser.parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output) if args.output else None
    assets_root = Path(args.assets_root) if args.assets_root else _find_assets_root(input_path)

    if not input_path.exists():
        print(f"Input not found: {input_path}")
        return 1

    converted: List[Path] = []
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
            )
            converted.append(output_json)
            print(f"Converted: {output_json}")
        except Exception as exc:
            print(f"Failed: {anim_path} -> {exc}")
    if not converted:
        print("No animations converted.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
