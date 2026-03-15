#!/usr/bin/env python3
"""Convert the launch-build MSM BINs into rev6-style JSON.

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
from typing import List, Optional, Tuple
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


def convert_bin(
    input_path: Path,
    output_path: Path,
    *,
    expand_sprite_cycles: bool = False
) -> None:
    data = parse_oldest_bin(input_path, expand_sprite_cycles=expand_sprite_cycles)
    output_path.write_text(json.dumps(data.to_json_dict(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert launch-build MSM BINs to rev6 JSON."
    )
    parser.add_argument("input", type=Path, help="Path to the BIN file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Destination JSON path (defaults to <input>.json)",
    )
    parser.add_argument(
        "--expand-sprite-cycles",
        action="store_true",
        help="Synthesize flipbook frames between numeric sprite endpoints.",
    )
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"Input file not found: {args.input}")
    output = args.output or args.input.with_suffix(".json")
    convert_bin(
        args.input,
        output,
        expand_sprite_cycles=args.expand_sprite_cycles,
    )
    print(f"Converted {args.input} -> {output}")


if __name__ == "__main__":
    main()
