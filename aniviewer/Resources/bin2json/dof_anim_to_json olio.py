"""
DOF ANIMBBB -> MSM Animation Viewer JSON converter.

Converts a Down of the Fare .ANIMBBB.asset into the viewer's JSON format
and generates a TexturePacker-style XML + atlas PNG.
"""

from __future__ import annotations

import argparse
import json
import re
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
    pivot = sprite.get("m_Pivot", {}) or {}
    return {
        "name": sprite.get("m_Name", sprite_asset_path.stem),
        "rect": {
            "x": float(rect.get("x", 0)),
            "y": float(rect.get("y", 0)),
            "w": float(rect.get("width", 0)),
            "h": float(rect.get("height", 0)),
        },
        "pivot": {
            "x": float(pivot.get("x", 0.5)),
            "y": float(pivot.get("y", 0.5)),
        },
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
        pivot_y = 1.0 - pivot["y"] if flip_y else pivot["y"]
        attrib = {
            "n": sprite["name"],
            "x": str(x),
            "y": str(y),
            "w": str(w),
            "h": str(h),
            "pX": f"{pivot['x']:.6f}",
            "pY": f"{pivot_y:.6f}",
            "oX": "0",
            "oY": "0",
            "oW": str(w),
            "oH": str(h),
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
    rotation_units: str,
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
        if default_sprite_name and default_sprite_name in sprite_defs:
            sprite_info = sprite_defs[default_sprite_name]
            rect = sprite_info["rect"]
            pivot = sprite_info["pivot"]
            pivot_y = 1.0 - pivot["y"] if flip_y else pivot["y"]
            anchor_x = rect["w"] * pivot["x"]
            anchor_y = rect["h"] * pivot_y

        key_times = _collect_times(channel_map.values())
        frames: List[Dict[str, Any]] = []

        pos_x_keys = _channel_key_map(channel_map["pos_x"])
        pos_y_keys = _channel_key_map(channel_map["pos_y"])
        scale_x_keys = _channel_key_map(channel_map["scale_x"])
        scale_y_keys = _channel_key_map(channel_map["scale_y"])
        rot_keys = _channel_key_map(channel_map["rotation"])
        opacity_keys = _channel_key_map(channel_map["opacity"])
        r_keys = _channel_key_map(channel_map["r"])
        g_keys = _channel_key_map(channel_map["g"])
        b_keys = _channel_key_map(channel_map["b"])

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
                pos_x_val, pos_x_key = pos_x_keys.get(time, (0.0, 0))
                pos_y_val, pos_y_key = pos_y_keys.get(time, (0.0, 0))
                if flip_y:
                    pos_y_val = -pos_y_val
                frame["pos"] = {
                    "immediate": int(pos_x_key) if time in pos_x_keys else int(pos_y_key),
                    "x": float(pos_x_val),
                    "y": float(pos_y_val),
                }

            if time in scale_x_keys or time in scale_y_keys:
                scale_x_val, scale_x_key = scale_x_keys.get(time, (1.0, 0))
                scale_y_val, scale_y_key = scale_y_keys.get(time, (1.0, 0))
                frame["scale"] = {
                    "immediate": int(scale_x_key) if time in scale_x_keys else int(scale_y_key),
                    "x": float(scale_x_val) * 100.0,
                    "y": float(scale_y_val) * 100.0,
                }

            if time in rot_keys:
                rot_val, rot_key = rot_keys[time]
                rot_deg = float(rot_val)
                if rotation_units == "radians":
                    rot_deg = rot_deg * (180.0 / 3.141592653589793)
                if invert_rotation:
                    rot_deg = -rot_deg
                frame["rotation"] = {"immediate": int(rot_key), "value": rot_deg}

            if time in opacity_keys:
                op_val, op_key = opacity_keys[time]
                frame["opacity"] = {"immediate": int(op_key), "value": float(op_val)}

            if time in sprite_keys:
                sprite_index = int(round(sprite_keys[time][0]))
                sprite_name = _resolve_sprite_name(sprite_index, mapping, image_names)
                frame["sprite"] = {"immediate": 1, "string": sprite_name}

            if time in r_keys or time in g_keys or time in b_keys:
                r_val, r_key = r_keys.get(time, (1.0, 0))
                g_val, g_key = g_keys.get(time, (1.0, 0))
                b_val, b_key = b_keys.get(time, (1.0, 0))
                frame["rgb"] = {
                    "immediate": int(r_key) if time in r_keys else int(g_key) if time in g_keys else int(b_key),
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
            }
        )

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
    rotation_group = parser.add_mutually_exclusive_group()
    rotation_group.add_argument(
        "--rotation-degrees",
        dest="rotation_units",
        action="store_const",
        const="degrees",
        help="Treat rotation values as degrees (default).",
    )
    rotation_group.add_argument(
        "--rotation-radians",
        dest="rotation_units",
        action="store_const",
        const="radians",
        help="Treat rotation values as radians and convert to degrees.",
    )
    parser.set_defaults(flip_y=True, invert_rotation=True, rotation_units="degrees")

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
                args.rotation_units,
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
