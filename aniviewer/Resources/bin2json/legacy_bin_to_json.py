#!/usr/bin/env python3
"""Convert legacy MSM animation BIN files into rev6-style JSON.

These BINs correspond to an earlier revision of the animation format that
stores the data as a flat dump of the runtime `xml_AE*` structures.  The goal
of this script is to reinterpret that layout and emit JSON that the modern
viewer (and `rev6-2-json.py`) understands.
"""
from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, List, Optional

BLEND_VERSION = 2
IMMEDIATE_MASK = 0xFF


class LegacyReader:
    """Minimal reader that mimics the behaviour of FS::ReaderFile."""

    def __init__(self, path: Path) -> None:
        self.fp: BinaryIO = path.open("rb")
        self.path = path
        self.size = path.stat().st_size

    def close(self) -> None:
        self.fp.close()

    def read_u32(self) -> int:
        return struct.unpack("<I", self.fp.read(4))[0]

    def read_f32(self) -> float:
        return struct.unpack("<f", self.fp.read(4))[0]

    def read_bytes(self, size: int) -> bytes:
        return self.fp.read(size)

    def read_string(self) -> str:
        """Legacy strings are length-prefixed (len includes trailing NUL)."""
        length = self.read_u32()
        if length == 0:
            return ""
        payload = self.fp.read(length)
        text = payload[:-1].decode("ascii", errors="ignore") if length else ""
        padding = (4 - (length % 4)) % 4
        if padding:
            self.fp.read(padding)
        return text

    def read_string_raw(self) -> tuple[str, int]:
        start = self.fp.tell()
        text = self.read_string()
        consumed = self.fp.tell() - start
        return text, consumed

    def tell(self) -> int:
        return self.fp.tell()

    def seek(self, offset: int, whence: int = 0) -> None:
        self.fp.seek(offset, whence)

    def remaining(self) -> int:
        return self.size - self.fp.tell()

    def peek_u32(self) -> int:
        pos = self.fp.tell()
        try:
            value = self.read_u32()
        finally:
            self.fp.seek(pos)
        return value


def decode_immediate(value: int) -> int:
    """Convert the 32-bit legacy immediate to the signed 8-bit enum."""
    raw = value & IMMEDIATE_MASK
    return raw if raw < 0x80 else raw - 0x100


def float_at(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def immediate_at(data: bytes, offset: int) -> int:
    value = struct.unpack_from("<I", data, offset)[0]
    return decode_immediate(value)


@dataclass
class LegacyFrame:
    time: float
    pos_immediate: int
    pos_x: float
    pos_y: float
    scale_immediate: int
    scale_x: float
    scale_y: float
    rotation_immediate: int
    rotation: float
    opacity_immediate: int
    opacity: float
    sprite_immediate: int
    sprite: str

    def to_dict(self) -> dict:
        return {
            "time": self.time,
            "pos": {
                "immediate": self.pos_immediate,
                "x": self.pos_x,
                "y": self.pos_y,
            },
            "scale": {
                "immediate": self.scale_immediate,
                "x": self.scale_x,
                "y": self.scale_y,
            },
            "rotation": {"immediate": self.rotation_immediate, "value": self.rotation},
            "opacity": {"immediate": self.opacity_immediate, "value": self.opacity},
            "sprite": {
                "immediate": self.sprite_immediate,
                "string": self.sprite,
            },
            "rgb": {
                # Legacy dumps do not expose animated colour data; treat as white.
                "immediate": -1,
                "red": 255,
                "green": 255,
                "blue": 255,
            },
        }


@dataclass
class LegacyLayer:
    name: str
    parent_index: int
    parent_name: str
    layer_id: int
    layer_type: int
    src_index: int
    blend_mode: int
    anchor_x: float = 0.0
    anchor_y: float = 0.0
    frames: List[LegacyFrame] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.layer_type,
            "blend": self.blend_mode,
            "parent": self.parent_index,
            "id": self.layer_id,
            "src": self.src_index,
            "width": 0,
            "height": 0,
            "anchor_x": self.anchor_x,
            "anchor_y": self.anchor_y,
            "unk": "",
            "frames": [frame.to_dict() for frame in self.frames],
        }


@dataclass
class LegacyAnimation:
    name: str
    stage_width: int
    stage_height: int
    loop_offset: float
    centered: int
    layers: List[LegacyLayer]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "width": self.stage_width,
            "height": self.stage_height,
            "loop_offset": self.loop_offset,
            "centered": self.centered,
            "layers": [layer.to_dict() for layer in self.layers],
            "clone_layers": [],
        }


@dataclass
class LegacyBin:
    sheet: str
    animations: List[LegacyAnimation]

    def to_json_dict(self) -> dict:
        sources = [
            {
                "src": self.sheet,
                "id": 0,
                "width": 0,
                "height": 0,
            }
        ]
        return {
            "rev": 6,
            "blend_version": BLEND_VERSION,
            "legacy_format": True,
            "sources": sources,
            "anims": [anim.to_dict() for anim in self.animations],
        }


def parse_layer(reader: LegacyReader) -> LegacyLayer:
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
    meta = struct.unpack("<4I", meta_raw)
    layer_id, layer_type, src_index, blend_raw = meta
    blend_mode = blend_raw if blend_raw < 8 else 0

    frame_count = reader.read_u32()
    frames: List[LegacyFrame] = []
    anchor_x = 0.0
    anchor_y = 0.0

    extra_block_known: Optional[bool] = None

    for idx in range(frame_count):
        block = reader.read_bytes(0x68)
        reader.read_bytes(4)  # unused flags
        sprite = reader.read_string()
        reader.read_bytes(0x10)  # unused legacy block

        if extra_block_known is None:
            next_len = reader.peek_u32()
            looks_legacy = 0 <= next_len <= 0x4000 and next_len <= reader.remaining()
            extra_block_known = looks_legacy

        if extra_block_known:
            extra_len = reader.read_u32()
            reader.read_bytes(extra_len)
            padding = (4 - (extra_len % 4)) % 4
            if padding:
                reader.read_bytes(padding)
            reader.read_bytes(8)

        time = float_at(block, 0x00)

        if idx == 0:
            anchor_x = float_at(block, 0x1C)
            anchor_y = float_at(block, 0x20)

        pos_immediate = immediate_at(block, 0x24)
        pos_x = float_at(block, 0x28)
        pos_y = float_at(block, 0x2C)

        scale_immediate = immediate_at(block, 0x30)
        scale_x = float_at(block, 0x34)
        scale_y = float_at(block, 0x38)

        rotation_immediate = immediate_at(block, 0x3C)
        rotation = float_at(block, 0x40)

        opacity_immediate = immediate_at(block, 0x44)
        opacity = float_at(block, 0x48)

        sprite_immediate = 0 if sprite else -1

        frames.append(
            LegacyFrame(
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
                sprite=sprite,
            )
        )

    return LegacyLayer(
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


def parse_animation(reader: LegacyReader) -> LegacyAnimation:
    name = reader.read_string()
    packed = reader.read_u32()
    stage_width = packed & 0xFFFF
    stage_height = (packed >> 16) & 0xFFFF
    loop_offset = reader.read_f32()
    centered = reader.read_u32()
    layer_count = reader.read_u32()
    layers = [parse_layer(reader) for _ in range(layer_count)]
    return LegacyAnimation(
        name=name,
        stage_width=stage_width,
        stage_height=stage_height,
        loop_offset=loop_offset,
        centered=centered,
        layers=layers,
    )


def parse_legacy_bin(path: Path) -> LegacyBin:
    reader = LegacyReader(path)
    try:
        version = reader.read_u32()
        if version != 1:
            raise ValueError(f"Unsupported legacy BIN version {version}")
        sheet = reader.read_string()
        reader.read_u32()  # reserved
        reader.read_u32()  # reserved
        anim_count = reader.read_u32()
        animations = [parse_animation(reader) for _ in range(anim_count)]
        return LegacyBin(sheet=sheet, animations=animations)
    finally:
        reader.close()


def convert_bin(input_path: Path, output_path: Path) -> None:
    legacy = parse_legacy_bin(input_path)
    output_path.write_text(json.dumps(legacy.to_json_dict(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert legacy MSM BINs to rev6 JSON."
    )
    parser.add_argument("input", type=Path, help="Path to the legacy BIN file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Destination JSON path (defaults to <input>.json)",
    )
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"Input file not found: {args.input}")
    output = args.output or args.input.with_suffix(".json")
    convert_bin(args.input, output)
    print(f"Converted {args.input} -> {output}")


if __name__ == "__main__":
    main()
