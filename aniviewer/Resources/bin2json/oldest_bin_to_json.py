#!/usr/bin/env python3
"""Convert rev1-family MSM BINs to/from rev6-style JSON.

These files predate the legacy BINs handled by `legacy_bin_to_json.py`.  They
still dump the runtime AE structs, but each frame only stores the sprite name
when it actually changes and the per-frame tail block is absent.  This script
replicates the runtime reader so we can surface the data to the viewer.
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path
from dataclasses import replace
from typing import Any, BinaryIO, Dict, List, Optional, Tuple
import re

import json
import legacy_bin_to_json as legacy

MAX_STRING_LEN = 0x1000
FRAME_BLOCK_SIZE = 0x68
LAYER_TAIL_SIZE = 0x30


class OldestReader(legacy.LegacyReader):
    """Extends the legacy reader with a simple peek helper."""

    def peek_bytes(self, size: int) -> bytes:
        pos = self.fp.tell()
        try:
            data = self.fp.read(size)
        finally:
            self.fp.seek(pos)
        return data


def read_aligned_string(reader: OldestReader) -> str:
    """Read the launch-era aligned strings stored after each frame."""
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





def parse_layer(reader: OldestReader, *, expand_sprite_cycles: bool) -> legacy.LegacyLayer:
    name = reader.read_string()
    parent_ref = reader.read_string()

    parent_index = -1
    parent_name = ""
    if ":" in parent_ref:
        prefix, _, remainder = parent_ref.partition(":")
        try:
            parent_index = int(prefix)
        except ValueError:
            parent_index = -1
        parent_name = remainder

    meta_raw = reader.read_bytes(0x10)
    layer_id, layer_type, src_index, blend_raw = legacy.struct.unpack("<4I", meta_raw)
    blend_mode = blend_raw if blend_raw < 8 else 0

    frame_count = reader.read_u32()
    frames: List[legacy.LegacyFrame] = []
    anchor_x = 0.0
    anchor_y = 0.0
    current_sprite = ""

    for idx in range(frame_count):
        block = reader.read_bytes(FRAME_BLOCK_SIZE)
        legacy.immediate_at(block, 0x04)  # legacy immediate not used by viewer
        reader.read_u32()  # post flags (unused)
        sprite_value = read_aligned_string(reader)
        sprite_changed = bool(sprite_value)
        tint_r = reader.read_f32()
        tint_g = reader.read_f32()
        tint_b = reader.read_f32()
        context_string = read_aligned_string(reader)

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

        if sprite_changed:
            current_sprite = sprite_value

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
                sprite_immediate=0 if sprite_changed else -1,
                sprite=current_sprite,
            )
        )

    if expand_sprite_cycles:
        frames = _expand_sprite_cycles(frames)

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


def _split_numeric_suffix(name: str) -> Tuple[str, Optional[int], int]:
    match = re.match(r"^(.*?)(\d+)$", name)
    if not match:
        return name, None, 0
    digits = match.group(2)
    return match.group(1), int(digits), len(digits)


def _expand_sprite_cycles(frames: List[legacy.LegacyFrame]) -> List[legacy.LegacyFrame]:
    """Insert implied flipbook frames when launch-era data only stores endpoints.

    Some layers (e.g., monster_AC's propellor) only toggle between the first and
    last sprite in a numbered sequence. The runtime walks every sprite between
    those endpoints, but the raw BIN only stores the bounding values. When we
    detect a monotonic increase in the numeric suffix, synthesize the missing
    frames so the JSON matches what the game renders.
    """
    if not frames:
        return frames

    expanded: List[legacy.LegacyFrame] = []
    for idx, frame in enumerate(frames):
        expanded.append(frame)
        if idx + 1 >= len(frames):
            continue

        next_frame = frames[idx + 1]
        base_a, num_a, width_a = _split_numeric_suffix(frame.sprite)
        base_b, num_b, width_b = _split_numeric_suffix(next_frame.sprite)
        if (
            base_a != base_b
            or num_a is None
            or num_b is None
            or not base_a.endswith("_")
        ):
            continue

        width = width_a if width_a else width_b
        gap = num_b - num_a
        if gap <= 1:
            continue

        steps = gap - 1
        time_span = next_frame.time - frame.time
        for step in range(steps):
            t = frame.time + time_span * ((step + 1) / (steps + 1)) if steps + 1 else frame.time
            next_index = num_a + step + 1
            if width:
                sprite_name = f"{base_a}{next_index:0{width}d}"
            else:
                sprite_name = f"{base_a}{next_index}"
            expanded.append(
                replace(
                    frame,
                    time=t,
                    sprite=sprite_name,
                    sprite_immediate=0,
                )
            )

    return expanded


def parse_animation(reader: OldestReader, *, expand_sprite_cycles: bool) -> legacy.LegacyAnimation:
    name = reader.read_string()
    packed = reader.read_u32()
    stage_width = packed & 0xFFFF
    stage_height = (packed >> 16) & 0xFFFF
    loop_offset = reader.read_f32()
    centered = reader.read_u32()
    layer_count = reader.read_u32()
    layers = [
        parse_layer(reader, expand_sprite_cycles=expand_sprite_cycles)
        for _ in range(layer_count)
    ]
    return legacy.LegacyAnimation(
        name=name,
        stage_width=stage_width,
        stage_height=stage_height,
        loop_offset=loop_offset,
        centered=centered,
        layers=layers,
    )


def parse_oldest_bin(path: Path, *, expand_sprite_cycles: bool = False) -> legacy.LegacyBin:
    reader = OldestReader(path)
    try:
        version = reader.read_u32()
        if version != 1:
            raise ValueError(f"Unsupported BIN version {version}")
        sheet = reader.read_string()
        reader.read_u32()  # reserved
        reader.read_u32()  # reserved
        anim_count = reader.read_u32()
        animations = [
            parse_animation(reader, expand_sprite_cycles=expand_sprite_cycles)
            for _ in range(anim_count)
        ]
        return legacy.LegacyBin(sheet=sheet, animations=animations)
    finally:
        reader.close()


def _coerce_int(value: Any, default: int = 0) -> int:
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


def _coerce_float(value: Any, default: float = 0.0) -> float:
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


def _coerce_u32(value: Any, default: int = 0) -> int:
    number = _coerce_int(value, default)
    if number < 0:
        return 0
    if number > 0xFFFFFFFF:
        return 0xFFFFFFFF
    return number


def _coerce_immediate(value: Any, default: int = 0) -> int:
    immediate = _coerce_int(value, default)
    if immediate in (-1, 0, 1):
        return immediate
    if 0 <= immediate <= 255:
        signed = immediate if immediate < 128 else immediate - 256
        if signed in (-1, 0, 1):
            return signed
    return default


def _encode_immediate_u32(value: Any, default: int = 0) -> int:
    return _coerce_immediate(value, default) & 0xFF


def _coerce_blend(value: Any) -> int:
    blend = _coerce_int(value, 0)
    if 0 <= blend <= 7:
        return blend
    if blend in (3, 4):
        return 1
    return 0


def _write_u32(handle: BinaryIO, value: int) -> None:
    handle.write(struct.pack("<I", int(value) & 0xFFFFFFFF))


def _write_f32(handle: BinaryIO, value: float) -> None:
    handle.write(struct.pack("<f", float(value)))


def _write_string(handle: BinaryIO, text: str) -> None:
    raw = (text or "").encode("ascii", errors="ignore") + b"\x00"
    _write_u32(handle, len(raw))
    handle.write(raw)
    padding = (4 - (len(raw) % 4)) % 4
    if padding:
        handle.write(b"\x00" * padding)


def _write_aligned_string(handle: BinaryIO, text: str) -> None:
    raw = (text or "").encode("ascii", errors="ignore")
    _write_u32(handle, len(raw))
    if raw:
        handle.write(raw)
        padding = (4 - (len(raw) % 4)) % 4
        if padding:
            handle.write(b"\x00" * padding)


def _encode_parent_ref(layer: Dict[str, Any], layers: List[Dict[str, Any]]) -> str:
    parent_value = layer.get("parent", -1)
    if isinstance(parent_value, str):
        text = parent_value.strip()
        if not text or text == "-1":
            return ""
        if ":" in text:
            prefix, _, remainder = text.partition(":")
            parent_index = _coerce_int(prefix, -1)
            if parent_index < 0:
                return ""
            parent_name = remainder
            if 0 <= parent_index < len(layers):
                parent_name = str(layers[parent_index].get("name", "") or parent_name)
            return f"{parent_index}:{parent_name}"
        parent_index = _coerce_int(text, -1)
    else:
        parent_index = _coerce_int(parent_value, -1)

    if parent_index < 0:
        return ""

    parent_name = ""
    if 0 <= parent_index < len(layers):
        parent_name = str(layers[parent_index].get("name", "") or "")
    return f"{parent_index}:{parent_name}"


def _normalized_tint(value: Any) -> float:
    channel = _coerce_float(value, 255.0)
    if channel <= 1.0:
        return max(0.0, min(1.0, channel))
    return max(0.0, min(1.0, channel / 255.0))


def _write_launch_frame(
    handle: BinaryIO,
    frame: Dict[str, Any],
    anchor_x: float,
    anchor_y: float,
    current_sprite: str,
) -> str:
    pos = frame.get("pos") if isinstance(frame.get("pos"), dict) else {}
    scale = frame.get("scale") if isinstance(frame.get("scale"), dict) else {}
    rotation = frame.get("rotation") if isinstance(frame.get("rotation"), dict) else {}
    opacity = frame.get("opacity") if isinstance(frame.get("opacity"), dict) else {}
    sprite = frame.get("sprite") if isinstance(frame.get("sprite"), dict) else {}
    rgb = frame.get("rgb") if isinstance(frame.get("rgb"), dict) else {}

    block = bytearray(FRAME_BLOCK_SIZE)
    struct.pack_into("<f", block, 0x00, _coerce_float(frame.get("time", 0.0), 0.0))
    struct.pack_into("<I", block, 0x04, _encode_immediate_u32(frame.get("legacy_immediate"), 0))

    struct.pack_into("<f", block, 0x1C, anchor_x)
    struct.pack_into("<f", block, 0x20, anchor_y)

    struct.pack_into("<I", block, 0x24, _encode_immediate_u32(pos.get("immediate"), 0))
    struct.pack_into("<f", block, 0x28, _coerce_float(pos.get("x", 0.0), 0.0))
    struct.pack_into("<f", block, 0x2C, _coerce_float(pos.get("y", 0.0), 0.0))

    struct.pack_into("<I", block, 0x30, _encode_immediate_u32(scale.get("immediate"), 0))
    struct.pack_into("<f", block, 0x34, _coerce_float(scale.get("x", 1.0), 1.0))
    struct.pack_into("<f", block, 0x38, _coerce_float(scale.get("y", 1.0), 1.0))

    struct.pack_into("<I", block, 0x3C, _encode_immediate_u32(rotation.get("immediate"), 0))
    struct.pack_into("<f", block, 0x40, _coerce_float(rotation.get("value", 0.0), 0.0))

    struct.pack_into("<I", block, 0x44, _encode_immediate_u32(opacity.get("immediate"), 0))
    struct.pack_into("<f", block, 0x48, _coerce_float(opacity.get("value", 1.0), 1.0))

    handle.write(block)
    _write_u32(handle, 0)  # post flags

    sprite_name = str(sprite.get("string", "") or "")
    sprite_immediate = _coerce_immediate(
        sprite.get("immediate", 0 if sprite_name else -1),
        0 if sprite_name else -1,
    )

    if sprite_name:
        resolved_sprite = sprite_name
    elif sprite_immediate == -1:
        resolved_sprite = current_sprite
    else:
        resolved_sprite = current_sprite

    sprite_delta = resolved_sprite if resolved_sprite != current_sprite else ""
    _write_aligned_string(handle, sprite_delta)

    _write_f32(handle, _normalized_tint(rgb.get("red", 255.0)))
    _write_f32(handle, _normalized_tint(rgb.get("green", 255.0)))
    _write_f32(handle, _normalized_tint(rgb.get("blue", 255.0)))

    context_value = frame.get("context")
    if isinstance(context_value, dict):
        context_string = str(context_value.get("string", "") or "")
    elif isinstance(context_value, str):
        context_string = context_value
    else:
        context_string = ""
    _write_aligned_string(handle, context_string)

    return resolved_sprite


def _write_layer(handle: BinaryIO, layer: Dict[str, Any], layers: List[Dict[str, Any]]) -> None:
    _write_string(handle, str(layer.get("name", "") or ""))
    _write_string(handle, _encode_parent_ref(layer, layers))

    layer_id = _coerce_u32(layer.get("id", 0), 0)
    layer_type = _coerce_u32(layer.get("type", 1), 1)
    src_index = _coerce_u32(layer.get("src", 0), 0)
    blend_mode = _coerce_u32(_coerce_blend(layer.get("blend", 0)), 0)
    handle.write(struct.pack("<4I", layer_id, layer_type, src_index, blend_mode))

    frames = layer.get("frames") if isinstance(layer.get("frames"), list) else []
    frame_list = [frame for frame in frames if isinstance(frame, dict)]
    _write_u32(handle, len(frame_list))

    default_anchor_x = _coerce_float(layer.get("anchor_x", 0.0), 0.0)
    default_anchor_y = _coerce_float(layer.get("anchor_y", 0.0), 0.0)

    current_sprite = ""
    for frame in frame_list:
        anchor = frame.get("anchor") if isinstance(frame.get("anchor"), dict) else {}
        anchor_x = _coerce_float(anchor.get("x", default_anchor_x), default_anchor_x)
        anchor_y = _coerce_float(anchor.get("y", default_anchor_y), default_anchor_y)
        current_sprite = _write_launch_frame(handle, frame, anchor_x, anchor_y, current_sprite)


def _write_animation(handle: BinaryIO, animation: Dict[str, Any]) -> None:
    _write_string(handle, str(animation.get("name", "") or ""))

    width = _coerce_u32(animation.get("width", 0), 0) & 0xFFFF
    height = _coerce_u32(animation.get("height", 0), 0) & 0xFFFF
    packed = width | (height << 16)
    _write_u32(handle, packed)

    _write_f32(handle, _coerce_float(animation.get("loop_offset", 0.0), 0.0))
    _write_u32(handle, _coerce_u32(animation.get("centered", 0), 0))

    layers = animation.get("layers") if isinstance(animation.get("layers"), list) else []
    layer_list = [layer for layer in layers if isinstance(layer, dict)]
    _write_u32(handle, len(layer_list))
    for layer in layer_list:
        _write_layer(handle, layer, layer_list)


def write_launch_bin(payload: Dict[str, Any], output_path: Path) -> None:
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")

    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    source0 = sources[0] if sources and isinstance(sources[0], dict) else {}
    sheet = str(source0.get("src", "") or "")

    anims = payload.get("anims") if isinstance(payload.get("anims"), list) else []
    anim_list = [anim for anim in anims if isinstance(anim, dict)]
    if not anim_list:
        raise ValueError("Input JSON does not contain any animations")

    with open(output_path, "wb") as handle:
        _write_u32(handle, 1)
        _write_string(handle, sheet)
        _write_u32(handle, 0)
        _write_u32(handle, 0)
        _write_u32(handle, len(anim_list))
        for anim in anim_list:
            _write_animation(handle, anim)


def convert_json(input_path: Path, output_path: Path) -> None:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    write_launch_bin(payload, output_path)


def convert_bin(
    input_path: Path,
    output_path: Path,
    *,
    expand_sprite_cycles: bool = False
) -> None:
    data = parse_oldest_bin(input_path, expand_sprite_cycles=expand_sprite_cycles)
    payload = data.to_json_dict()
    payload["source_format"] = "rev1_launch"
    payload["source_revision"] = 1
    output_path.write_text(json.dumps(payload, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert rev1-family MSM BINs to/from rev6 JSON."
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
        help="Destination path (defaults to <input>.json or <input>.bin)",
    )
    parser.add_argument(
        "--expand-sprite-cycles",
        action="store_true",
        help="Synthesize flipbook frames between numeric sprite endpoints.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mode_or_input = args.mode_or_input

    if mode_or_input in {"d", "b"}:
        mode = mode_or_input
        if not args.path:
            raise SystemExit("Missing input path. Usage: oldest_bin_to_json.py <d|b> <file>")
        src = Path(args.path)
        if not src.is_file():
            raise SystemExit(f"Input file not found: {src}")

        if mode == "d":
            dst = args.output or src.with_suffix(".json")
            convert_bin(src, dst, expand_sprite_cycles=args.expand_sprite_cycles)
            print(f"Converted {src} -> {dst}")
            return

        dst = args.output or src.with_suffix(".bin")
        convert_json(src, dst)
        print(f"Converted {src} -> {dst}")
        return

    # Backward-compatible form: oldest_bin_to_json.py input.bin [-o out.json]
    src = Path(mode_or_input)
    if not src.is_file():
        raise SystemExit(f"Input file not found: {src}")
    dst = args.output or src.with_suffix(".json")
    convert_bin(src, dst, expand_sprite_cycles=args.expand_sprite_cycles)
    print(f"Converted {src} -> {dst}")


if __name__ == "__main__":
    main()
