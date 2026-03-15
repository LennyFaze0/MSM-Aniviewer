#!/usr/bin/env python3
"""Convert Monster Choir (Android) animation BINs into rev6-style JSON.

These BINs match a runtime AE dump format similar to Monster choir for iOS,
but with a fixed 0x68-byte frame block and an expanded per-frame tail.
The parser below reconstructs the layer/frame data and outputs the standard
rev6 JSON schema so the viewer can render the animations.
"""
from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List

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

        _ = tint_r, tint_g, tint_b  # unused, but preserved for debugging if needed

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Monster Choir BINs to rev6 JSON."
    )
    parser.add_argument("input", type=Path, help="Path to the Choir BIN file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Destination JSON path (defaults to <input>.json).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    src: Path = args.input
    if not src.is_file():
        raise SystemExit(f"Input file not found: {src}")

    dst: Path = args.output or src.with_suffix(".json")
    data = parse_choir_bin(src)
    with open(dst, "w", encoding="utf-8") as handle:
        json.dump(data.to_json_dict(), handle, indent=2)
    print(f"Wrote {dst}")


if __name__ == "__main__":
    main()
