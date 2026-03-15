#!/usr/bin/env python3
"""Convert My Muppets Show animation BIN files into rev6-style JSON."""

from __future__ import annotations

import argparse
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Dict, List

BLEND_VERSION = 2
IMMEDIATE_MASK = 0xFF
FRAME_HEADER_SIZE = 0x4C
INLINE_BLOCK_SIZE = 0x40

_CONTEXT_CACHE: Dict[Path, List[str]] = {}


class MuppetReader:
    """Minimal reader for the muppet AEAnim dump layout."""

    def __init__(self, path: Path) -> None:
        self.fp: BinaryIO = path.open("rb")
        self.size = path.stat().st_size

    def close(self) -> None:
        self.fp.close()

    def read_u32(self) -> int:
        raw = self.fp.read(4)
        if len(raw) != 4:
            raise EOFError("Unexpected EOF while reading <I")
        return struct.unpack("<I", raw)[0]

    def read_f32(self) -> float:
        raw = self.fp.read(4)
        if len(raw) != 4:
            raise EOFError("Unexpected EOF while reading <f")
        return struct.unpack("<f", raw)[0]

    def read_bytes(self, size: int) -> bytes:
        data = self.fp.read(size)
        if len(data) != size:
            raise EOFError(f"Unexpected EOF while reading {size} bytes")
        return data

    def read_pascal_bytes(self) -> bytes:
        length = self.read_u32()
        if length == 0:
            return b""
        payload = self.read_bytes(length)
        padding = (4 - (length % 4)) % 4
        if padding:
            self.read_bytes(padding)
        return payload

    def read_string(self) -> str:
        length = self.read_u32()
        if length == 0:
            return ""
        payload = self.read_bytes(length)
        text = payload[:-1].decode("ascii", errors="ignore")
        padding = (4 - (length % 4)) % 4
        if padding:
            self.fp.read(padding)
        return text

    def tell(self) -> int:
        return self.fp.tell()

    def seek(self, offset: int, whence: int = 0) -> None:
        self.fp.seek(offset, whence)

    def remaining(self) -> int:
        return self.size - self.fp.tell()


def decode_immediate(value: int) -> int:
    raw = value & IMMEDIATE_MASK
    return raw if raw < 0x80 else raw - 0x100


def float_at(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def immediate_at(data: bytes, offset: int) -> int:
    value = struct.unpack_from("<I", data, offset)[0]
    return decode_immediate(value)


def is_printable_ascii(payload: bytes) -> bool:
    return all(32 <= b < 127 for b in payload)


def try_peek_layer_header(reader: MuppetReader) -> bool:
    """Return True if the stream at the current offset looks like a layer."""

    cursor = reader.tell()
    try:
        name_len = reader.read_u32()
        # Layer names are always at least one visible character plus the NUL.
        if name_len < 2 or name_len > 0x200 or name_len > reader.remaining():
            return False
        name_payload = reader.read_bytes(name_len) if name_len else b""
        if name_len and name_payload[-1] != 0:
            return False
        if name_len and not is_printable_ascii(name_payload[:-1]):
            return False
        pad = (4 - (name_len % 4)) % 4
        if pad:
            reader.read_bytes(pad)

        parent_len = reader.read_u32()
        if parent_len > 0x200 or parent_len > reader.remaining():
            return False
        parent_payload = reader.read_bytes(parent_len) if parent_len else b""
        if parent_len and parent_payload[-1] != 0:
            return False
        if parent_len and not is_printable_ascii(parent_payload[:-1]):
            return False
        pad_parent = (4 - (parent_len % 4)) % 4
        if pad_parent:
            reader.read_bytes(pad_parent)

        meta = reader.read_bytes(0x10)
        layer_id, layer_type, src_index, blend_raw = struct.unpack("<4I", meta)
        frame_count = reader.read_u32()
        if (
            layer_id == 0
            or layer_id > 0x1000
            or src_index > 0x1000
            or frame_count > 0x1000
            or layer_type > 8
            or blend_raw > 0x40
            or frame_count == 0
        ):
            return False
        return True
    except EOFError:
        return False
    finally:
        reader.seek(cursor, 0)


def try_peek_animation_header(reader: MuppetReader) -> bool:
    cursor = reader.tell()
    try:
        name_len = reader.read_u32()
        if name_len < 2 or name_len > 0x400 or name_len > reader.remaining():
            return False
        payload = reader.read_bytes(name_len)
        if payload[-1] != 0:
            return False
        if not is_printable_ascii(payload[:-1]):
            return False
        pad = (4 - (name_len % 4)) % 4
        if pad:
            reader.read_bytes(pad)
        packed = reader.read_u32()
        stage_width = packed & 0xFFFF
        stage_height = (packed >> 16) & 0xFFFF
        if stage_width == 0 or stage_height == 0 or stage_width > 4096 or stage_height > 4096:
            return False
        loop_offset = reader.read_f32()
        if not math.isfinite(loop_offset):
            return False
        centered = reader.read_u32()
        if centered > 8:
            return False
        layer_count = reader.read_u32()
        if layer_count == 0 or layer_count > 0x400:
            return False
        return True
    except EOFError:
        return False
    finally:
        reader.seek(cursor, 0)


def align_to_next_layer(reader: MuppetReader) -> None:
    """Skip the trailing remap/tint table until the next layer header."""

    start_scan = reader.tell()
    while reader.remaining() > 4:
        cursor = reader.tell()
        if try_peek_layer_header(reader):
            reader.seek(cursor, 0)
            return
        reader.seek(cursor + 1, 0)

    reader.seek(start_scan, 0)


def align_to_next_animation(reader: MuppetReader) -> None:
    """Skip padding/context blobs until the next animation header."""

    start_scan = reader.tell()
    while reader.remaining() > 4:
        cursor = reader.tell()
        if try_peek_animation_header(reader):
            reader.seek(cursor, 0)
            return
        reader.seek(cursor + 1, 0)
    reader.seek(start_scan, 0)


def load_context_columns(bin_dir: Path) -> List[str]:
    info_path = bin_dir / "anim_context_info.bin"
    if not info_path.exists():
        return []
    cache_key = info_path.resolve()
    cached = _CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    data = info_path.read_bytes()
    offset = 0
    version, = struct.unpack_from("<I", data, offset)
    offset += 4
    name_len, = struct.unpack_from("<I", data, offset)
    offset += 4
    offset += name_len
    offset = (offset + 3) & ~3
    offset += 8  # reserved
    set_count, entry_count = struct.unpack_from("<2I", data, offset)
    offset += 8

    columns: List[str] = []
    for set_index in range(set_count):
        set_names: List[str] = []
        for entry_index in range(entry_count):
            label_bytes = data[offset:offset + 12]
            offset += 12
            label = label_bytes.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
            label = "".join(ch for ch in label if 32 <= ord(ch) < 127).strip()
            if not label:
                label = f"entry_{set_index}_{entry_index}"
            offset += INLINE_BLOCK_SIZE
            set_names.append(label)
        if not columns:
            columns = set_names

    _CONTEXT_CACHE[cache_key] = columns
    return columns


@dataclass
class Frame:
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
                "immediate": -1,
                "red": 255,
                "green": 255,
                "blue": 255,
            },
        }


@dataclass
class Layer:
    name: str
    parent_index: int
    parent_name: str
    parent_id: int
    layer_id: int
    layer_type: int
    src_index: int
    blend_mode: int
    anchor_x: float = 0.0
    anchor_y: float = 0.0
    frames: List[Frame] = field(default_factory=list)

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
class Animation:
    name: str
    stage_width: int
    stage_height: int
    loop_offset: float
    centered: int
    layers: List[Layer]

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
class MuppetBin:
    sheet: str
    animations: List[Animation]
    context_columns: List[str]

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
            "context_columns": self.context_columns,
            "anims": [anim.to_dict() for anim in self.animations],
        }


def parse_layer(reader: MuppetReader, context_columns: List[str]) -> Layer:
    name = reader.read_string()
    parent_ref = reader.read_string()

    parent_index = -1
    parent_name = ""
    parent_id = -1
    if ":" in parent_ref:
        prefix, _, remainder = parent_ref.partition(":")
        try:
            parent_id = int(prefix)
            parent_index = parent_id
        except ValueError:
            parent_id = -1
            parent_index = -1
        parent_name = remainder
    else:
        parent_name = parent_ref

    meta_raw = reader.read_bytes(0x10)
    layer_id, layer_type, src_index, blend_raw = struct.unpack("<4I", meta_raw)
    blend_mode = blend_raw if blend_raw < 8 else 0

    frame_count = reader.read_u32()
    frames: List[Frame] = []
    last_time = 0.0
    synthetic_step = 1.0 / 30.0
    synthetic_time_used = False
    anchor_x = 0.0
    anchor_y = 0.0

    for idx in range(frame_count):
        try:
            block = reader.read_bytes(FRAME_HEADER_SIZE)
        except EOFError:
            print(
                f"[WARNING] Layer '{name}' truncated after {idx} frame(s) "
                f"of {frame_count}"
            )
            break

        sprite_immediate_raw = reader.read_u32()
        sprite_name = reader.read_string()

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

        raw_time = float_at(block, 0x00)
        time_value = raw_time
        if (
            not math.isfinite(raw_time)
            or abs(raw_time) > 1_000_000
            or (frames and raw_time + 1e-4 < last_time)
        ):
            time_value = last_time + (synthetic_step if frames else 0.0)
            synthetic_time_used = True

        last_time = time_value

        frames.append(
            Frame(
                time=time_value,
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
                sprite_immediate=decode_immediate(sprite_immediate_raw),
                sprite=sprite_name,
            )
        )

    if synthetic_time_used:
        print(
            f"[WARNING] Layer '{name}' contains invalid frame times; "
            "generated fallback timeline."
        )

    return Layer(
        name=name,
        parent_index=parent_index,
        parent_name=parent_name,
        parent_id=parent_id,
        layer_id=layer_id,
        layer_type=layer_type,
        src_index=src_index,
        blend_mode=blend_mode,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        frames=frames,
    )


def parse_animation(reader: MuppetReader, context_columns: List[str]) -> Animation:
    name = reader.read_string()
    packed = reader.read_u32()
    stage_width = packed & 0xFFFF
    stage_height = (packed >> 16) & 0xFFFF
    loop_offset = reader.read_f32()
    centered = reader.read_u32()
    layer_count = reader.read_u32()
    layers: List[Layer] = []
    for idx in range(layer_count):
        try:
            layers.append(parse_layer(reader, context_columns))
        except EOFError:
            print(
                f"[WARNING] Truncated animation '{name}' while reading layer "
                f"{idx + 1}/{layer_count}"
            )
            break
    resolve_parent_indices(layers)
    return Animation(
        name=name,
        stage_width=stage_width,
        stage_height=stage_height,
        loop_offset=loop_offset,
        centered=centered,
        layers=layers,
    )


def resolve_parent_indices(layers: List[Layer]) -> None:
    """Translate parent IDs into actual layer indices."""

    id_to_index: Dict[int, int] = {}
    for idx, layer in enumerate(layers):
        id_to_index[layer.layer_id] = idx

    for layer in layers:
        if layer.parent_id < 0:
            layer.parent_index = -1
            continue
        mapped = id_to_index.get(layer.parent_id)
        layer.parent_index = mapped if mapped is not None else -1


def parse_muppet_bin(path: Path) -> MuppetBin:
    context_columns = load_context_columns(path.parent)
    reader = MuppetReader(path)
    try:
        version = reader.read_u32()
        if version != 1:
            raise ValueError(f"Unsupported BIN version {version}")
        sheet = reader.read_string()
        reader.read_u32()  # reserved
        reader.read_u32()  # reserved
        anim_count = reader.read_u32()
        animations: List[Animation] = []
        for idx in range(anim_count):
            try:
                animations.append(parse_animation(reader, context_columns))
            except EOFError:
                print(
                    f"[WARNING] Truncated BIN while reading animation "
                    f"{idx + 1}/{anim_count}"
                )
                break
        return MuppetBin(sheet=sheet, animations=animations, context_columns=context_columns)
    finally:
        reader.close()


def convert_bin(input_path: Path, output_path: Path) -> None:
    muppet = parse_muppet_bin(input_path)
    output_path.write_text(json.dumps(muppet.to_json_dict(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert My Muppets Show BINs to rev6 JSON."
    )
    parser.add_argument("input", type=Path, help="Path to the muppet BIN file")
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
