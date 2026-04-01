#!/usr/bin/env python3
"""Convert Monster Choir (Android/iOS) animation BINs to/from rev6-style JSON.

This format is a legacy runtime dump used by Monster Choir revisions.
The script supports both:
- BIN -> JSON (`d` mode or legacy positional form)
- JSON -> BIN (`b` mode)
"""
from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Dict, List

import legacy_bin_to_json as legacy

MAX_STRING_LEN = 0x2000
FRAME_BLOCK_SIZE = 0x68
TAIL_EXTRA_U32 = 4
BLEND_VERSION = legacy.BLEND_VERSION


class ChoirReader(legacy.LegacyReader):
    """Legacy reader with a small peek helper."""

    def peek_bytes(self, size: int) -> bytes:
        pos = self.fp.tell()
        try:
            data = self.fp.read(size)
        finally:
            self.fp.seek(pos)
        return data


def read_aligned_string(reader: ChoirReader) -> str:
    """Read aligned, length-prefixed strings (length includes trailing NUL)."""
    length = reader.read_u32()
    if length == 0:
        return ""
    if length > MAX_STRING_LEN:
        raise ValueError(f"String length {length:#x} exceeds limit")
    data = reader.read_bytes(length)
    pad = (4 - (length % 4)) % 4
    if pad:
        reader.read_bytes(pad)
    return data.rstrip(b"\x00").decode("ascii", errors="ignore")


def write_u32(handle: BinaryIO, value: int) -> None:
    handle.write(struct.pack("<I", value & 0xFFFFFFFF))


def write_f32(handle: BinaryIO, value: float) -> None:
    handle.write(struct.pack("<f", float(value)))


def write_aligned_string(handle: BinaryIO, text: str) -> None:
    raw = (text or "").encode("ascii", errors="ignore") + b"\x00"
    write_u32(handle, len(raw))
    handle.write(raw)
    pad = (-len(raw)) & 3
    if pad:
        handle.write(b"\x00" * pad)


def coerce_int(value: Any, default: int = 0) -> int:
    if value is None:
        return int(default)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return int(default)


def coerce_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return float(default)
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float(default)


def coerce_u32(value: Any, default: int = 0) -> int:
    number = coerce_int(value, default)
    if number < 0:
        return 0
    if number > 0xFFFFFFFF:
        return 0xFFFFFFFF
    return number


def coerce_u16(value: Any, default: int = 0) -> int:
    number = coerce_int(value, default)
    if number < 0:
        return 0
    if number > 0xFFFF:
        return 0xFFFF
    return number


def coerce_u8(value: Any, default: int = 0) -> int:
    number = coerce_int(value, default)
    if number < 0:
        return 0
    if number > 0xFF:
        return 0xFF
    return number


def coerce_immediate(value: Any, default: int = 0) -> int:
    immediate = coerce_int(value, default)
    if immediate in (-1, 0, 1):
        return immediate
    if 0 <= immediate <= 255:
        signed = immediate if immediate < 128 else immediate - 256
        if signed in (-1, 0, 1):
            return signed
    return default


def coerce_blend(value: Any) -> int:
    blend = coerce_int(value, 0)
    if 0 <= blend <= 7:
        return blend
    if blend in (3, 4):
        return 1
    return 0


def parse_parent_ref(parent_ref: str) -> tuple[int, str]:
    parent_index = -1
    parent_name = ""
    if ":" in parent_ref:
        prefix, _, remainder = parent_ref.partition(":")
        try:
            parent_index = int(prefix)
        except ValueError:
            parent_index = -1
        parent_name = remainder
    else:
        parent_name = parent_ref
    return parent_index, parent_name


def resolve_parent_indices(layers: List[legacy.LegacyLayer]) -> None:
    """Translate parent IDs into actual layer indices."""
    id_to_index = {layer.layer_id: idx for idx, layer in enumerate(layers)}
    for layer in layers:
        if layer.parent_index < 0:
            continue
        mapped = id_to_index.get(layer.parent_index)
        layer.parent_index = mapped if mapped is not None else -1


def parse_layer(reader: ChoirReader) -> legacy.LegacyLayer:
    name = read_aligned_string(reader)
    parent_ref = read_aligned_string(reader)
    parent_index, parent_name = parse_parent_ref(parent_ref)

    meta_raw = reader.read_bytes(0x10)
    layer_id, layer_type, src_index, blend_raw = struct.unpack("<4I", meta_raw)
    blend_mode = blend_raw if blend_raw < 8 else 0

    _layer_color = reader.read_u32()
    _layer_tint = reader.read_u32()
    frame_count = reader.read_u32()

    if frame_count > 200000:
        raise ValueError(f"Unreasonable frame count ({frame_count}) in layer '{name}'")

    frames: List[legacy.LegacyFrame] = []
    anchor_x = 0.0
    anchor_y = 0.0
    current_sprite = ""

    for idx in range(frame_count):
        block = reader.read_bytes(FRAME_BLOCK_SIZE)

        time = legacy.float_at(block, 0x00)
        if idx == 0:
            anchor_x = legacy.float_at(block, 0x1C)
            anchor_y = legacy.float_at(block, 0x20)

        pos_immediate = legacy.immediate_at(block, 0x24)
        pos_x = legacy.float_at(block, 0x28)
        pos_y = legacy.float_at(block, 0x2C)

        scale_immediate = legacy.immediate_at(block, 0x30)
        scale_x = legacy.float_at(block, 0x34)
        scale_y = legacy.float_at(block, 0x38)

        rotation_immediate = legacy.immediate_at(block, 0x3C)
        rotation = legacy.float_at(block, 0x40)

        opacity_immediate = legacy.immediate_at(block, 0x44)
        opacity = legacy.float_at(block, 0x48)

        reader.read_u32()  # post flags (unused)
        sprite_value = read_aligned_string(reader)
        tint_r = reader.read_u32()
        tint_g = reader.read_u32()
        tint_b = reader.read_u32()
        _context_string = read_aligned_string(reader)
        for _ in range(TAIL_EXTRA_U32):
            reader.read_u32()

        if sprite_value:
            current_sprite = sprite_value
            sprite_immediate = 0
        else:
            sprite_immediate = -1

        frames.append(
            legacy.LegacyFrame(
                time=time,
                pos_immediate=pos_immediate,
                pos_x=pos_x,
                pos_y=pos_y,
                scale_immediate=scale_immediate,
                scale_x=scale_x,
                scale_y=scale_y,
                rotation_immediate=rotation_immediate,
                rotation=rotation,
                opacity_immediate=opacity_immediate,
                opacity=opacity,
                sprite_immediate=sprite_immediate,
                sprite=current_sprite,
            )
        )

        _ = tint_r, tint_g, tint_b  # parsed for debugging, not currently exposed

    return legacy.LegacyLayer(
        name=name,
        parent_index=parent_index,
        parent_name=parent_name,
        layer_id=layer_id,
        layer_type=layer_type,
        src_index=src_index,
        blend_mode=blend_mode,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        frames=frames,
    )


def parse_animation(reader: ChoirReader) -> legacy.LegacyAnimation:
    name = read_aligned_string(reader)
    packed = reader.read_u32()
    stage_width = packed & 0xFFFF
    stage_height = (packed >> 16) & 0xFFFF
    loop_offset = reader.read_f32()
    centered = reader.read_u32()
    layer_count = reader.read_u32()

    if layer_count > 20000:
        raise ValueError(f"Unreasonable layer count ({layer_count}) in animation '{name}'")

    layers = [parse_layer(reader) for _ in range(layer_count)]
    resolve_parent_indices(layers)
    return legacy.LegacyAnimation(
        name=name,
        stage_width=stage_width,
        stage_height=stage_height,
        loop_offset=loop_offset,
        centered=centered,
        layers=layers,
    )


@dataclass
class ChoirBin:
    sources: List[dict]
    animations: List[legacy.LegacyAnimation]

    def to_json_dict(self) -> dict:
        return {
            "rev": 6,
            "blend_version": BLEND_VERSION,
            "legacy_format": True,
            "source_format": "choir",
            "source_revision": 3,
            "sources": self.sources,
            "anims": [anim.to_dict() for anim in self.animations],
        }


def parse_choir_bin(path: Path) -> ChoirBin:
    reader = ChoirReader(path)
    try:
        source_count = reader.read_u32()
        if source_count > 64:
            raise ValueError(f"Unreasonable source count ({source_count})")
        sources: List[dict] = []
        for idx in range(source_count):
            src = read_aligned_string(reader)
            unk0 = reader.read_u32()
            unk1 = reader.read_u32()
            src_id = unk0 if unk0 else idx
            sources.append(
                {
                    "src": src,
                    "id": src_id,
                    "width": 0,
                    "height": 0,
                }
            )
            _ = unk1

        anim_count = reader.read_u32()
        if anim_count > 5000:
            raise ValueError(f"Unreasonable animation count ({anim_count})")

        animations = [parse_animation(reader) for _ in range(anim_count)]

        remaining = reader.remaining()
        if remaining:
            tail = reader.read_bytes(remaining)
            if any(b != 0 for b in tail):
                raise ValueError(f"Unexpected trailing data ({remaining} bytes)")

        return ChoirBin(sources=sources, animations=animations)
    finally:
        reader.close()


def normalize_sources(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = payload.get("sources")
    if not isinstance(raw, list) or not raw:
        return [{"src": "", "id": 0, "width": 0, "height": 0}]
    result: List[Dict[str, Any]] = []
    for idx, source in enumerate(raw):
        if not isinstance(source, dict):
            continue
        src = str(source.get("src", "") or "")
        src_id = coerce_u32(source.get("id", idx), idx)
        width = coerce_u16(source.get("width", 0), 0)
        height = coerce_u16(source.get("height", 0), 0)
        result.append({"src": src, "id": src_id, "width": width, "height": height})
    if not result:
        result.append({"src": "", "id": 0, "width": 0, "height": 0})
    return result


def normalize_layer_ids(layers: List[Dict[str, Any]]) -> List[int]:
    used: set[int] = set()
    layer_ids: List[int] = []
    next_id = 0
    for idx, layer in enumerate(layers):
        candidate = coerce_int(layer.get("id", idx), idx)
        if candidate < 0:
            candidate = idx
        if candidate in used:
            while next_id in used:
                next_id += 1
            candidate = next_id
        used.add(candidate)
        layer_ids.append(candidate)
    return layer_ids


def encode_parent_ref(
    parent_value: Any,
    layers: List[Dict[str, Any]],
    layer_ids: List[int],
) -> str:
    id_to_index = {layer_id: idx for idx, layer_id in enumerate(layer_ids)}

    if isinstance(parent_value, str):
        text = parent_value.strip()
        if not text or text == "-1":
            return ""
        if ":" in text:
            prefix, _, remainder = text.partition(":")
            parent_id = coerce_int(prefix, -1)
            if parent_id >= 0:
                return f"{parent_id}:{remainder}"
        else:
            parsed = coerce_int(text, -1)
            if parsed >= 0:
                parent_value = parsed
            else:
                return ""

    parent_num = coerce_int(parent_value, -1)
    if parent_num < 0:
        return ""

    parent_index = -1
    if 0 <= parent_num < len(layers):
        parent_index = parent_num
    elif parent_num in id_to_index:
        parent_index = id_to_index[parent_num]

    if parent_index < 0:
        return ""

    parent_id = layer_ids[parent_index]
    parent_name = str(layers[parent_index].get("name", "") or "")
    return f"{parent_id}:{parent_name}"


def encode_immediate_u32(value: Any, default: int = 0) -> int:
    immediate = coerce_immediate(value, default)
    return immediate & 0xFF


def write_frame_block(handle: BinaryIO, frame: Dict[str, Any], anchor_x: float, anchor_y: float) -> None:
    block = bytearray(FRAME_BLOCK_SIZE)

    def get_node(name: str) -> Dict[str, Any]:
        node = frame.get(name)
        return node if isinstance(node, dict) else {}

    pos = get_node("pos")
    scale = get_node("scale")
    rotation = get_node("rotation")
    opacity = get_node("opacity")

    struct.pack_into("<f", block, 0x00, coerce_float(frame.get("time", 0.0), 0.0))
    struct.pack_into("<f", block, 0x1C, anchor_x)
    struct.pack_into("<f", block, 0x20, anchor_y)

    struct.pack_into("<I", block, 0x24, encode_immediate_u32(pos.get("immediate"), 0))
    struct.pack_into("<f", block, 0x28, coerce_float(pos.get("x", 0.0), 0.0))
    struct.pack_into("<f", block, 0x2C, coerce_float(pos.get("y", 0.0), 0.0))

    struct.pack_into("<I", block, 0x30, encode_immediate_u32(scale.get("immediate"), 0))
    struct.pack_into("<f", block, 0x34, coerce_float(scale.get("x", 1.0), 1.0))
    struct.pack_into("<f", block, 0x38, coerce_float(scale.get("y", 1.0), 1.0))

    struct.pack_into("<I", block, 0x3C, encode_immediate_u32(rotation.get("immediate"), 0))
    struct.pack_into("<f", block, 0x40, coerce_float(rotation.get("value", 0.0), 0.0))

    struct.pack_into("<I", block, 0x44, encode_immediate_u32(opacity.get("immediate"), 0))
    struct.pack_into("<f", block, 0x48, coerce_float(opacity.get("value", 1.0), 1.0))

    handle.write(block)


def write_layer(
    handle: BinaryIO,
    layer: Dict[str, Any],
    layers: List[Dict[str, Any]],
    layer_ids: List[int],
    layer_index: int,
) -> None:
    name = str(layer.get("name", "") or "")
    parent_ref = encode_parent_ref(layer.get("parent", -1), layers, layer_ids)

    write_aligned_string(handle, name)
    write_aligned_string(handle, parent_ref)

    layer_id = coerce_u32(layer_ids[layer_index], layer_index)
    layer_type = coerce_u32(layer.get("type", 1), 1)
    src_index = coerce_u32(layer.get("src", 0), 0)
    blend_mode = coerce_u32(coerce_blend(layer.get("blend", 0)), 0)
    handle.write(struct.pack("<4I", layer_id, layer_type, src_index, blend_mode))

    # Preserve a stable packed layer color for debugging parity.
    rgb = layer.get("rgb") if isinstance(layer.get("rgb"), dict) else {}
    layer_r = coerce_u8(rgb.get("red", 255), 255)
    layer_g = coerce_u8(rgb.get("green", 255), 255)
    layer_b = coerce_u8(rgb.get("blue", 255), 255)
    layer_color = (layer_r << 16) | (layer_g << 8) | layer_b
    write_u32(handle, layer_color)
    write_u32(handle, 0)

    frames = layer.get("frames")
    frame_list: List[Dict[str, Any]] = [frame for frame in frames if isinstance(frame, dict)] if isinstance(frames, list) else []
    write_u32(handle, len(frame_list))

    anchor_x = coerce_float(layer.get("anchor_x", 0.0), 0.0)
    anchor_y = coerce_float(layer.get("anchor_y", 0.0), 0.0)
    current_sprite = ""

    for frame in frame_list:
        write_frame_block(handle, frame, anchor_x, anchor_y)

        # Reserved tail marker.
        write_u32(handle, 0)

        sprite_node = frame.get("sprite") if isinstance(frame.get("sprite"), dict) else {}
        sprite_value = str(sprite_node.get("string", "") or "")
        sprite_immediate = coerce_immediate(sprite_node.get("immediate", 0), 0 if sprite_value else -1)

        if sprite_immediate == -1:
            sprite_to_write = ""
        else:
            sprite_to_write = sprite_value
            if sprite_to_write:
                current_sprite = sprite_to_write
            elif current_sprite:
                sprite_to_write = current_sprite

        write_aligned_string(handle, sprite_to_write)

        rgb_node = frame.get("rgb") if isinstance(frame.get("rgb"), dict) else {}
        write_u32(handle, coerce_u8(rgb_node.get("red", 255), 255))
        write_u32(handle, coerce_u8(rgb_node.get("green", 255), 255))
        write_u32(handle, coerce_u8(rgb_node.get("blue", 255), 255))

        write_aligned_string(handle, "")
        for _ in range(TAIL_EXTRA_U32):
            write_u32(handle, 0)


def write_animation(handle: BinaryIO, anim: Dict[str, Any]) -> None:
    name = str(anim.get("name", "") or "")
    width = coerce_u16(anim.get("width", 0), 0)
    height = coerce_u16(anim.get("height", 0), 0)
    packed = (width & 0xFFFF) | ((height & 0xFFFF) << 16)
    loop_offset = coerce_float(anim.get("loop_offset", 0.0), 0.0)
    centered = coerce_u32(anim.get("centered", 0), 0)

    layers_raw = anim.get("layers")
    layers: List[Dict[str, Any]] = [layer for layer in layers_raw if isinstance(layer, dict)] if isinstance(layers_raw, list) else []
    layer_ids = normalize_layer_ids(layers)

    write_aligned_string(handle, name)
    write_u32(handle, packed)
    write_f32(handle, loop_offset)
    write_u32(handle, centered)
    write_u32(handle, len(layers))

    for idx, layer in enumerate(layers):
        write_layer(handle, layer, layers, layer_ids, idx)


def write_choir_bin(payload: Dict[str, Any], output_path: Path) -> None:
    sources = normalize_sources(payload)
    anims_raw = payload.get("anims")
    animations: List[Dict[str, Any]] = [anim for anim in anims_raw if isinstance(anim, dict)] if isinstance(anims_raw, list) else []

    with output_path.open("wb") as handle:
        write_u32(handle, len(sources))
        for idx, source in enumerate(sources):
            write_aligned_string(handle, str(source.get("src", "") or ""))
            source_id = coerce_u32(source.get("id", idx), idx)
            # Parser treats 0 as "implicit index", so only keep 0 for index 0.
            if source_id == 0 and idx != 0:
                source_id = idx
            write_u32(handle, source_id)
            write_u32(handle, 0)

        write_u32(handle, len(animations))
        for anim in animations:
            write_animation(handle, anim)


def convert_bin(input_path: Path, output_path: Path) -> None:
    data = parse_choir_bin(input_path)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(data.to_json_dict(), handle, indent=2)


def convert_json(input_path: Path, output_path: Path) -> None:
    with open(input_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    write_choir_bin(payload, output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Monster Choir BINs to/from rev6 JSON."
    )
    parser.add_argument(
        "mode_or_input",
        help="Either mode ('d' or 'b') or input BIN path (legacy decode form).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Input path when using explicit mode form.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Destination path (defaults to <input>.json or <input>.bin).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mode_or_input = args.mode_or_input

    if mode_or_input in {"d", "b"}:
        mode = mode_or_input
        if not args.path:
            raise SystemExit("Missing input path. Usage: choir_bin_to_json.py <d|b> <file>")
        src = Path(args.path)
        if not src.is_file():
            raise SystemExit(f"Input file not found: {src}")
        if mode == "d":
            dst = args.output or src.with_suffix(".json")
            convert_bin(src, dst)
            print(f"Converted {src} -> {dst}")
            return
        dst = args.output or src.with_suffix(".bin")
        convert_json(src, dst)
        print(f"Converted {src} -> {dst}")
        return

    # Backward-compatible form: choir_bin_to_json.py input.bin [-o out.json]
    src = Path(mode_or_input)
    if not src.is_file():
        raise SystemExit(f"Input file not found: {src}")
    dst = args.output or src.with_suffix(".json")
    convert_bin(src, dst)
    print(f"Wrote {dst}")


if __name__ == "__main__":
    main()
