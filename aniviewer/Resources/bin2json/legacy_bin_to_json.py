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
from typing import Any, BinaryIO, Dict, List, Optional

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
            "source_format": "legacy",
            "source_revision": 1,
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
    handle.write(struct.pack('<I', int(value) & 0xFFFFFFFF))


def _write_f32(handle: BinaryIO, value: float) -> None:
    handle.write(struct.pack('<f', float(value)))


def _write_string(handle: BinaryIO, text: str) -> None:
    raw = (text or '').encode('ascii', errors='ignore') + b'\x00'
    _write_u32(handle, len(raw))
    handle.write(raw)
    padding = (4 - (len(raw) % 4)) % 4
    if padding:
        handle.write(b'\x00' * padding)


def _encode_parent_ref(layer: Dict[str, Any], layers: List[Dict[str, Any]]) -> str:
    parent_value = layer.get('parent', -1)
    if isinstance(parent_value, str):
        text = parent_value.strip()
        if not text or text == '-1':
            return ''
        if ':' in text:
            prefix, _, remainder = text.partition(':')
            try:
                parent_index = int(prefix)
            except (TypeError, ValueError):
                parent_index = -1
            if parent_index >= 0:
                return f"{parent_index}:{remainder}"
            return ''
        try:
            parent_index = int(text)
        except (TypeError, ValueError):
            parent_index = -1
    else:
        parent_index = _coerce_int(parent_value, -1)

    if parent_index < 0:
        return ''

    parent_name = ''
    if 0 <= parent_index < len(layers):
        parent_name = str(layers[parent_index].get('name', '') or '')
    return f"{parent_index}:{parent_name}"


def _write_frame(handle: BinaryIO, frame: Dict[str, Any], anchor_x: float, anchor_y: float) -> None:
    pos = frame.get('pos') if isinstance(frame.get('pos'), dict) else {}
    scale = frame.get('scale') if isinstance(frame.get('scale'), dict) else {}
    rotation = frame.get('rotation') if isinstance(frame.get('rotation'), dict) else {}
    opacity = frame.get('opacity') if isinstance(frame.get('opacity'), dict) else {}
    sprite = frame.get('sprite') if isinstance(frame.get('sprite'), dict) else {}

    block = bytearray(0x68)
    struct.pack_into('<f', block, 0x00, _coerce_float(frame.get('time', 0.0), 0.0))
    struct.pack_into('<f', block, 0x1C, anchor_x)
    struct.pack_into('<f', block, 0x20, anchor_y)

    struct.pack_into('<I', block, 0x24, _encode_immediate_u32(pos.get('immediate'), 0))
    struct.pack_into('<f', block, 0x28, _coerce_float(pos.get('x', 0.0), 0.0))
    struct.pack_into('<f', block, 0x2C, _coerce_float(pos.get('y', 0.0), 0.0))

    struct.pack_into('<I', block, 0x30, _encode_immediate_u32(scale.get('immediate'), 0))
    struct.pack_into('<f', block, 0x34, _coerce_float(scale.get('x', 1.0), 1.0))
    struct.pack_into('<f', block, 0x38, _coerce_float(scale.get('y', 1.0), 1.0))

    struct.pack_into('<I', block, 0x3C, _encode_immediate_u32(rotation.get('immediate'), 0))
    struct.pack_into('<f', block, 0x40, _coerce_float(rotation.get('value', 0.0), 0.0))

    struct.pack_into('<I', block, 0x44, _encode_immediate_u32(opacity.get('immediate'), 0))
    struct.pack_into('<f', block, 0x48, _coerce_float(opacity.get('value', 1.0), 1.0))

    handle.write(block)
    _write_u32(handle, 0)  # unused flags

    sprite_name = str(sprite.get('string', '') or '')
    sprite_immediate = _coerce_immediate(
        sprite.get('immediate', 0 if sprite_name else -1),
        0 if sprite_name else -1,
    )
    if sprite_immediate == -1:
        sprite_name = ''
    _write_string(handle, sprite_name)

    handle.write(b'\x00' * 0x10)  # unused legacy block

    # Preserve the optional extra-block path expected by the parser.
    _write_u32(handle, 0)
    handle.write(b'\x00' * 8)


def _write_layer(handle: BinaryIO, layer: Dict[str, Any], layers: List[Dict[str, Any]]) -> None:
    name = str(layer.get('name', '') or '')
    parent_ref = _encode_parent_ref(layer, layers)

    _write_string(handle, name)
    _write_string(handle, parent_ref)

    layer_id = _coerce_u32(layer.get('id', 0), 0)
    layer_type = _coerce_u32(layer.get('type', 1), 1)
    src_index = _coerce_u32(layer.get('src', 0), 0)
    blend_mode = _coerce_u32(_coerce_blend(layer.get('blend', 0)), 0)
    handle.write(struct.pack('<4I', layer_id, layer_type, src_index, blend_mode))

    frames = layer.get('frames') if isinstance(layer.get('frames'), list) else []
    frame_list = [frame for frame in frames if isinstance(frame, dict)]
    _write_u32(handle, len(frame_list))

    default_anchor_x = _coerce_float(layer.get('anchor_x', 0.0), 0.0)
    default_anchor_y = _coerce_float(layer.get('anchor_y', 0.0), 0.0)

    for frame in frame_list:
        anchor = frame.get('anchor') if isinstance(frame.get('anchor'), dict) else {}
        anchor_x = _coerce_float(anchor.get('x', default_anchor_x), default_anchor_x)
        anchor_y = _coerce_float(anchor.get('y', default_anchor_y), default_anchor_y)
        _write_frame(handle, frame, anchor_x, anchor_y)


def _write_animation(handle: BinaryIO, animation: Dict[str, Any]) -> None:
    _write_string(handle, str(animation.get('name', '') or ''))

    width = _coerce_u32(animation.get('width', 0), 0) & 0xFFFF
    height = _coerce_u32(animation.get('height', 0), 0) & 0xFFFF
    packed = width | (height << 16)
    _write_u32(handle, packed)

    _write_f32(handle, _coerce_float(animation.get('loop_offset', 0.0), 0.0))
    _write_u32(handle, _coerce_u32(animation.get('centered', 0), 0))

    layers = animation.get('layers') if isinstance(animation.get('layers'), list) else []
    layer_list = [layer for layer in layers if isinstance(layer, dict)]
    _write_u32(handle, len(layer_list))
    for layer in layer_list:
        _write_layer(handle, layer, layer_list)


def write_legacy_bin(payload: Dict[str, Any], output_path: Path) -> None:
    if not isinstance(payload, dict):
        raise ValueError('JSON root must be an object')

    sources = payload.get('sources') if isinstance(payload.get('sources'), list) else []
    source0 = sources[0] if sources and isinstance(sources[0], dict) else {}
    sheet = str(source0.get('src', '') or '')

    anims = payload.get('anims') if isinstance(payload.get('anims'), list) else []
    anim_list = [anim for anim in anims if isinstance(anim, dict)]
    if not anim_list:
        raise ValueError('Input JSON does not contain any animations')

    with open(output_path, 'wb') as handle:
        _write_u32(handle, 1)
        _write_string(handle, sheet)
        _write_u32(handle, 0)
        _write_u32(handle, 0)
        _write_u32(handle, len(anim_list))
        for anim in anim_list:
            _write_animation(handle, anim)


def convert_bin(input_path: Path, output_path: Path) -> None:
    legacy = parse_legacy_bin(input_path)
    output_path.write_text(json.dumps(legacy.to_json_dict(), indent=2))


def convert_json(input_path: Path, output_path: Path) -> None:
    payload = json.loads(input_path.read_text(encoding='utf-8'))
    write_legacy_bin(payload, output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Convert legacy MSM BINs to/from JSON.'
    )
    parser.add_argument(
        'mode_or_input',
        help="Either mode ('d' or 'b') or input BIN path (legacy decode form).",
    )
    parser.add_argument(
        'path',
        nargs='?',
        help='Input path when using explicit mode form.',
    )
    parser.add_argument(
        '-o',
        '--output',
        type=Path,
        default=None,
        help='Destination path (defaults to <input>.json or <input>.bin).',
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mode_or_input = args.mode_or_input

    if mode_or_input in {'d', 'b'}:
        mode = mode_or_input
        if not args.path:
            raise SystemExit('Missing input path. Usage: legacy_bin_to_json.py <d|b> <file>')
        src = Path(args.path)
        if not src.is_file():
            raise SystemExit(f'Input file not found: {src}')

        if mode == 'd':
            dst = args.output or src.with_suffix('.json')
            convert_bin(src, dst)
            print(f'Converted {src} -> {dst}')
            return

        dst = args.output or src.with_suffix('.bin')
        convert_json(src, dst)
        print(f'Converted {src} -> {dst}')
        return

    # Backward-compatible form: legacy_bin_to_json.py input.bin [-o out.json]
    src = Path(mode_or_input)
    if not src.is_file():
        raise SystemExit(f'Input file not found: {src}')
    dst = args.output or src.with_suffix('.json')
    convert_bin(src, dst)
    print(f'Converted {src} -> {dst}')


if __name__ == '__main__':
    main()
