#!/usr/bin/env python3
"""Parse MSM Rev5 animation BINs into rev6-style JSON.

Rev5 is a transitional format between Rev4 and Rev6:
- Layer headers differ from Rev6.
- Keyframes are packed in a compact layout.
- Extra inter-layer/inter-animation padding blocks exist in some files.

This converter focuses on robust BIN -> JSON extraction using the Rev5 corpus.
The emitted JSON is rev6-style so the viewer and existing tooling can consume it.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


BLEND_VERSION = 2
MAX_SOURCE_COUNT = 64
MAX_ANIMATION_COUNT = 512
MAX_LAYER_COUNT = 700
MAX_FRAME_COUNT = 10000


def _decode_immediate(value: int) -> int:
    raw = value & 0xFF
    return raw if raw < 0x80 else raw - 0x100


def _clamp_byte(value: int) -> int:
    return max(0, min(255, int(value)))


def _is_printable_ascii(data: bytes) -> bool:
    return bool(data) and all(32 <= b < 127 for b in data)


class Reader:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.size = len(raw)
        self.pos = 0

    def _align(self, size: int) -> None:
        self.pos = (self.pos + (size - 1)) & ~(size - 1)

    def tell(self) -> int:
        return self.pos

    def seek(self, position: int) -> None:
        self.pos = max(0, min(self.size, int(position)))

    def read_u16(self) -> int:
        self._align(2)
        value = struct.unpack_from("<H", self.raw, self.pos)[0]
        self.pos += 2
        return value

    def read_i16(self) -> int:
        self._align(2)
        value = struct.unpack_from("<h", self.raw, self.pos)[0]
        self.pos += 2
        return value

    def read_u32(self) -> int:
        self._align(4)
        value = struct.unpack_from("<I", self.raw, self.pos)[0]
        self.pos += 4
        return value

    def read_i32(self) -> int:
        self._align(4)
        value = struct.unpack_from("<i", self.raw, self.pos)[0]
        self.pos += 4
        return value

    def read_f32(self) -> float:
        self._align(4)
        value = struct.unpack_from("<f", self.raw, self.pos)[0]
        self.pos += 4
        return value

    def read_string(self) -> str:
        length = self.read_u32()
        if length == 0:
            return ""
        if self.pos + length - 1 > self.size:
            raise EOFError("String payload exceeds file bounds")
        text = self.raw[self.pos : self.pos + length - 1].decode(
            "ascii", errors="ignore"
        )
        self.pos += length - 1
        offset = len(text) % 4
        self.pos += (4 - offset) if offset != 0 else 4
        return text


@dataclass
class LayerProbe:
    score: int
    has_prefix: bool
    name: str


def _peek_string(raw: bytes, pos: int, max_len: int = 256) -> Optional[tuple[int, int, str]]:
    aligned = (pos + 3) & ~3
    if aligned + 4 > len(raw):
        return None
    length = struct.unpack_from("<I", raw, aligned)[0]
    if length < 1 or length > max_len:
        return None
    end = aligned + 4 + length - 1
    if end > len(raw):
        return None
    payload = raw[aligned + 4 : end]
    if not _is_printable_ascii(payload):
        return None
    text = payload.decode("ascii", errors="ignore")
    if not text:
        return None
    return aligned, length, text


def _peek_layer(raw: bytes, pos: int) -> Optional[LayerProbe]:
    best: Optional[LayerProbe] = None

    for has_prefix in (False, True):
        test_pos = pos + (8 if has_prefix else 0)
        info = _peek_string(raw, test_pos, max_len=128)
        if not info:
            continue
        string_pos, _, name = info
        if not any(ch.isalpha() for ch in name):
            continue

        header_pos = string_pos + 4 + len(name)
        pad = len(name) % 4
        header_pos += (4 - pad) if pad != 0 else 4
        header_pos = (header_pos + 3) & ~3

        if header_pos + 32 > len(raw):
            continue

        layer_type = struct.unpack_from("<i", raw, header_pos)[0]
        blend = struct.unpack_from("<I", raw, header_pos + 4)[0]
        parent = struct.unpack_from("<h", raw, header_pos + 8)[0]
        frame_count = struct.unpack_from("<I", raw, header_pos + 28)[0]

        if layer_type < -128 or layer_type > 128:
            continue
        if blend > 128:
            continue
        if frame_count > MAX_FRAME_COUNT:
            continue

        score = 0
        if has_prefix:
            score += 1
        if 0 <= blend <= 8:
            score += 2
        if frame_count <= 500:
            score += 2
        if -1024 <= parent <= 1024:
            score += 1
        if "_" in name or " " in name:
            score += 1

        candidate = LayerProbe(score=score, has_prefix=has_prefix, name=name)
        if best is None or candidate.score > best.score:
            best = candidate

    return best


def _find_next_layer_start(raw: bytes, pos: int) -> tuple[Optional[int], Optional[LayerProbe]]:
    direct = _peek_layer(raw, pos)
    if direct:
        return pos, direct

    limit = min(len(raw) - 4, pos + 131072)
    for scan in range((pos + 3) & ~3, limit, 4):
        probe = _peek_layer(raw, scan)
        if probe and probe.score >= 2:
            return scan, probe
    return None, None


def _peek_animation(raw: bytes, pos: int) -> Optional[int]:
    info = _peek_string(raw, pos, max_len=128)
    if not info:
        return None
    string_pos, _, name = info
    if not any(ch.isalpha() for ch in name):
        return None

    header_pos = string_pos + 4 + len(name)
    pad = len(name) % 4
    header_pos += (4 - pad) if pad != 0 else 4
    header_pos = (header_pos + 1) & ~1
    if header_pos + 12 > len(raw):
        return None

    width = struct.unpack_from("<H", raw, header_pos)[0]
    height = struct.unpack_from("<H", raw, header_pos + 2)[0]

    aligned = (header_pos + 4 + 3) & ~3
    if aligned + 12 > len(raw):
        return None
    loop_offset = struct.unpack_from("<f", raw, aligned)[0]
    centered = struct.unpack_from("<I", raw, aligned + 4)[0]
    layer_count = struct.unpack_from("<I", raw, aligned + 8)[0]

    if width == 0 or height == 0 or width > 8192 or height > 8192:
        return None
    if not math.isfinite(loop_offset) or loop_offset < -1000 or loop_offset > 10000:
        return None
    if centered > 16:
        return None
    if layer_count == 0 or layer_count > MAX_LAYER_COUNT:
        return None

    return string_pos


def _find_next_animation_start(raw: bytes, pos: int) -> Optional[int]:
    direct = _peek_animation(raw, pos)
    if direct is not None:
        return direct

    limit = min(len(raw) - 4, pos + 262144)
    for scan in range((pos + 3) & ~3, limit, 4):
        candidate = _peek_animation(raw, scan)
        if candidate is not None:
            return candidate
    return None


def _read_frame(reader: Reader) -> tuple[dict, float, float]:
    time = float(reader.read_f32())

    # Rev5 keyframe preamble (24 bytes): unknown packed channels not required by viewer.
    reader.seek(reader.tell() + 0x18)

    anchor_x = float(reader.read_f32())
    anchor_y = float(reader.read_f32())

    pos_immediate = _decode_immediate(reader.read_u32())
    pos_x = float(reader.read_f32())
    pos_y = float(reader.read_f32())

    scale_immediate = _decode_immediate(reader.read_u32())
    scale_x = float(reader.read_f32())
    scale_y = float(reader.read_f32())

    rotation_immediate = _decode_immediate(reader.read_u32())
    rotation_value = float(reader.read_f32())

    opacity_immediate = _decode_immediate(reader.read_u32())
    opacity_value = float(reader.read_f32())

    sprite_immediate = _decode_immediate(reader.read_u32())
    sprite_name = ""
    # Rev5 frame records always serialize the sprite string payload after the
    # immediate byte (empty strings are encoded as length=1 + alignment pad).
    # If we skip reading it, frame alignment drifts and subsequent keyframes
    # decode as garbage values.
    sprite_pos = reader.tell()
    try:
        sprite_name = reader.read_string()
    except Exception:
        # Keep conversion resilient on malformed/corrupted records.
        reader.seek(sprite_pos)
        sprite_name = ""

    frame = {
        "time": time,
        "pos": {
            "immediate": pos_immediate,
            "x": pos_x,
            "y": pos_y,
        },
        "scale": {
            "immediate": scale_immediate,
            "x": scale_x,
            "y": scale_y,
        },
        "rotation": {
            "immediate": rotation_immediate,
            "value": rotation_value,
        },
        "opacity": {
            "immediate": opacity_immediate,
            "value": opacity_value,
        },
        "sprite": {
            "immediate": sprite_immediate,
            "string": sprite_name,
        },
        "rgb": {
            "immediate": -1,
            "red": 255,
            "green": 255,
            "blue": 255,
        },
    }
    return frame, anchor_x, anchor_y


def _read_layer(reader: Reader, layer_index: int) -> dict:
    if layer_index > 0:
        sync_pos, probe = _find_next_layer_start(reader.raw, reader.tell())
        if sync_pos is None or probe is None:
            raise ValueError(
                f"Unable to locate layer boundary near 0x{reader.tell():X}"
            )
        reader.seek(sync_pos)
        if probe.has_prefix:
            reader.read_u32()
            reader.read_u32()

    name = reader.read_string()
    layer_type = reader.read_i32()
    blend = reader.read_u32()
    parent = reader.read_i16()
    layer_id = reader.read_u16()
    src = reader.read_u16()
    red = _clamp_byte(reader.read_u16())
    green = _clamp_byte(reader.read_u16())
    blue = _clamp_byte(reader.read_u16())

    # Rev5 layer metadata:
    # marker_a / marker_b are opaque in corpus, key_count is the useful frame count.
    marker_a = reader.read_u32()
    marker_b = reader.read_u32()
    key_count = reader.read_u32()
    if marker_a != 1 or marker_b != 0:
        raise ValueError(
            f"Unexpected Rev5 layer markers for '{name}': ({marker_a}, {marker_b})"
        )
    if key_count > MAX_FRAME_COUNT:
        raise ValueError(f"Layer '{name}' has unreasonable key count: {key_count}")

    frames: list[dict] = []
    anchor_x = 0.0
    anchor_y = 0.0
    have_anchor = False

    for _ in range(key_count):
        frame, f_anchor_x, f_anchor_y = _read_frame(reader)
        if not have_anchor and (f_anchor_x != 0.0 or f_anchor_y != 0.0):
            anchor_x = f_anchor_x
            anchor_y = f_anchor_y
            have_anchor = True
        frames.append(frame)

    if not have_anchor and frames:
        # Preserve non-zero defaults if only zero anchors were authored.
        anchor_x = 0.0
        anchor_y = 0.0

    layer = {
        "name": name,
        "type": layer_type,
        "blend": int(blend),
        "parent": parent,
        "id": layer_id,
        "src": src,
        "width": 0,
        "height": 0,
        "anchor_x": anchor_x,
        "anchor_y": anchor_y,
        "unk": "",
        "frames": frames,
        "rgb": {
            "red": red,
            "green": green,
            "blue": blue,
        },
    }
    return layer


def _read_animation(reader: Reader, animation_index: int) -> Optional[dict]:
    if animation_index > 0:
        sync_pos = _find_next_animation_start(reader.raw, reader.tell())
        if sync_pos is None:
            return None
        reader.seek(sync_pos)

    name = reader.read_string()
    width = reader.read_u16()
    height = reader.read_u16()
    loop_offset = reader.read_f32()
    centered = reader.read_u32()
    layer_count = reader.read_u32()

    if layer_count == 0 or layer_count > MAX_LAYER_COUNT:
        return None

    layers: list[dict] = []
    for layer_index in range(layer_count):
        layer = _read_layer(reader, layer_index)
        layers.append(layer)

    return {
        "name": name,
        "width": width,
        "height": height,
        "loop_offset": loop_offset,
        "centered": centered,
        "layers": layers,
        "clone_layers": [],
    }


def parse_rev5_bin(input_path: Path) -> dict:
    raw = input_path.read_bytes()
    reader = Reader(raw)

    source_count = reader.read_u32()
    if source_count > MAX_SOURCE_COUNT:
        raise ValueError(f"Unexpected source count: {source_count}")

    sources: list[dict] = []
    for _ in range(source_count):
        src = reader.read_string()
        source_id = reader.read_u16()
        width = reader.read_u16()
        height = reader.read_u16()
        sources.append(
            {
                "src": src,
                "id": source_id,
                "width": width,
                "height": height,
            }
        )

    animation_count = reader.read_u32()
    if animation_count == 0 or animation_count > MAX_ANIMATION_COUNT:
        raise ValueError(f"Unexpected animation count: {animation_count}")

    animations: list[dict] = []
    for index in range(animation_count):
        animation = _read_animation(reader, index)
        if animation is None:
            print(
                f"[WARN] Rev5 parser stopped early at animation index {index}; "
                f"parsed {len(animations)} / {animation_count}.",
                file=sys.stderr,
            )
            break
        animations.append(animation)

    if not animations:
        raise ValueError("No animations could be parsed from Rev5 BIN.")

    return {
        "rev": 6,
        "blend_version": BLEND_VERSION,
        "sources": sources,
        "anims": animations,
        "source_format": "rev5",
        "source_revision": 5,
    }


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


def _coerce_u16(value: Any, default: int = 0) -> int:
    number = _coerce_int(value, default)
    if number < 0:
        return 0
    if number > 0xFFFF:
        return 0xFFFF
    return number


def _coerce_u32(value: Any, default: int = 0) -> int:
    number = _coerce_int(value, default)
    if number < 0:
        return 0
    if number > 0xFFFFFFFF:
        return 0xFFFFFFFF
    return number


def _coerce_i16(value: Any, default: int = 0) -> int:
    number = _coerce_int(value, default)
    if number < -32768:
        return -32768
    if number > 32767:
        return 32767
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


def _coerce_parent_i16(value: Any, default: int = -1) -> int:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return int(default)
        if ':' in text:
            text = text.split(':', 1)[0].strip()
        try:
            value = int(text)
        except (TypeError, ValueError):
            value = default
    return _coerce_i16(value, default)


def _coerce_blend_rev5(value: Any) -> int:
    blend = _coerce_int(value, 0)
    if 0 <= blend <= 7:
        return blend
    if blend in (3, 4):
        return 1
    return 0


class Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def _align(self, size: int) -> None:
        pad = (-len(self.buf)) & (size - 1)
        if pad:
            self.buf.extend(b"\x00" * pad)

    def write_u16(self, value: int) -> None:
        self._align(2)
        self.buf.extend(struct.pack("<H", int(value) & 0xFFFF))

    def write_i16(self, value: int) -> None:
        self._align(2)
        self.buf.extend(struct.pack("<h", int(value)))

    def write_u32(self, value: int) -> None:
        self._align(4)
        self.buf.extend(struct.pack("<I", int(value) & 0xFFFFFFFF))

    def write_i32(self, value: int) -> None:
        self._align(4)
        self.buf.extend(struct.pack("<i", int(value)))

    def write_f32(self, value: float) -> None:
        self._align(4)
        self.buf.extend(struct.pack("<f", float(value)))

    def write_string(self, text: str) -> None:
        encoded = (text or "").encode("ascii", errors="ignore")
        self.write_u32(len(encoded) + 1)
        self.buf.extend(encoded)
        pad = (4 - (len(encoded) % 4)) if (len(encoded) % 4) else 4
        self.buf.extend(b"\x00" * pad)



def _write_rev5_frame(writer: Writer, frame: dict, anchor_x: float, anchor_y: float) -> None:
    pos = frame.get("pos") if isinstance(frame.get("pos"), dict) else {}
    scale = frame.get("scale") if isinstance(frame.get("scale"), dict) else {}
    rotation = frame.get("rotation") if isinstance(frame.get("rotation"), dict) else {}
    opacity = frame.get("opacity") if isinstance(frame.get("opacity"), dict) else {}
    sprite = frame.get("sprite") if isinstance(frame.get("sprite"), dict) else {}

    writer.write_f32(_coerce_float(frame.get("time", 0.0), 0.0))
    writer.buf.extend(b"\x00" * 0x18)

    writer.write_f32(anchor_x)
    writer.write_f32(anchor_y)

    writer.write_u32(_encode_immediate_u32(pos.get("immediate"), 0))
    writer.write_f32(_coerce_float(pos.get("x", 0.0), 0.0))
    writer.write_f32(_coerce_float(pos.get("y", 0.0), 0.0))

    writer.write_u32(_encode_immediate_u32(scale.get("immediate"), 0))
    writer.write_f32(_coerce_float(scale.get("x", 1.0), 1.0))
    writer.write_f32(_coerce_float(scale.get("y", 1.0), 1.0))

    writer.write_u32(_encode_immediate_u32(rotation.get("immediate"), 0))
    writer.write_f32(_coerce_float(rotation.get("value", 0.0), 0.0))

    writer.write_u32(_encode_immediate_u32(opacity.get("immediate"), 0))
    writer.write_f32(_coerce_float(opacity.get("value", 1.0), 1.0))

    sprite_name = str(sprite.get("string", "") or "")
    sprite_immediate = _coerce_immediate(
        sprite.get("immediate", 0 if sprite_name else -1),
        0 if sprite_name else -1,
    )
    if sprite_immediate == -1:
        sprite_name = ""
    writer.write_u32(_encode_immediate_u32(sprite_immediate, -1 if not sprite_name else 0))
    writer.write_string(sprite_name)


def _write_rev5_layer(writer: Writer, layer: dict) -> None:
    name = str(layer.get("name", "") or "")
    writer.write_string(name)
    writer.write_i32(_coerce_int(layer.get("type", 1), 1))
    writer.write_u32(_coerce_blend_rev5(layer.get("blend", 0)))
    writer.write_i16(_coerce_parent_i16(layer.get("parent", -1), -1))
    writer.write_u16(_coerce_u16(layer.get("id", 0), 0))
    writer.write_u16(_coerce_u16(layer.get("src", 0), 0))

    rgb = layer.get("rgb") if isinstance(layer.get("rgb"), dict) else {}
    writer.write_u16(_coerce_u16(rgb.get("red", 255), 255))
    writer.write_u16(_coerce_u16(rgb.get("green", 255), 255))
    writer.write_u16(_coerce_u16(rgb.get("blue", 255), 255))

    frames = layer.get("frames") if isinstance(layer.get("frames"), list) else []
    frame_list = [frame for frame in frames if isinstance(frame, dict)]

    writer.write_u32(1)
    writer.write_u32(0)
    writer.write_u32(len(frame_list))

    default_anchor_x = _coerce_float(layer.get("anchor_x", 0.0), 0.0)
    default_anchor_y = _coerce_float(layer.get("anchor_y", 0.0), 0.0)

    for frame in frame_list:
        anchor = frame.get("anchor") if isinstance(frame.get("anchor"), dict) else {}
        anchor_x = _coerce_float(anchor.get("x", default_anchor_x), default_anchor_x)
        anchor_y = _coerce_float(anchor.get("y", default_anchor_y), default_anchor_y)
        _write_rev5_frame(writer, frame, anchor_x, anchor_y)


def _write_rev5_animation(writer: Writer, animation: dict) -> None:
    writer.write_string(str(animation.get("name", "") or ""))
    writer.write_u16(_coerce_u16(animation.get("width", 0), 0))
    writer.write_u16(_coerce_u16(animation.get("height", 0), 0))
    writer.write_f32(_coerce_float(animation.get("loop_offset", 0.0), 0.0))
    writer.write_u32(_coerce_u32(animation.get("centered", 0), 0))

    layers = animation.get("layers") if isinstance(animation.get("layers"), list) else []
    layer_list = [layer for layer in layers if isinstance(layer, dict)]
    writer.write_u32(len(layer_list))
    for layer in layer_list:
        _write_rev5_layer(writer, layer)


def write_rev5_bin(payload: dict, output_path: Path) -> None:
    writer = Writer()

    raw_sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    sources = [source for source in raw_sources if isinstance(source, dict)]
    writer.write_u32(len(sources))
    for index, source in enumerate(sources):
        writer.write_string(str(source.get("src", "") or ""))
        writer.write_u16(_coerce_u16(source.get("id", index), index))
        writer.write_u16(_coerce_u16(source.get("width", 0), 0))
        writer.write_u16(_coerce_u16(source.get("height", 0), 0))

    raw_anims = payload.get("anims") if isinstance(payload.get("anims"), list) else []
    anims = [anim for anim in raw_anims if isinstance(anim, dict)]
    if not anims:
        raise ValueError("Input JSON does not contain any animations")

    writer.write_u32(len(anims))
    for anim in anims:
        _write_rev5_animation(writer, anim)

    output_path.write_bytes(writer.buf)


def convert_bin_to_json(input_file: Path, output_file: Path) -> None:
    payload = parse_rev5_bin(input_file)
    output_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def convert_json_to_bin(input_file: Path, output_file: Path) -> None:
    payload = json.loads(input_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    write_rev5_bin(payload, output_file)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert MSM Rev5 animation BIN files to/from rev6-style JSON."
    )
    parser.add_argument("mode", choices=["d", "b"], help="d=bin->json, b=json->bin")
    parser.add_argument("file", help="Input file path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Destination path (defaults to <input>.json or <input>.bin).",
    )
    args = parser.parse_args()

    input_path = Path(args.file)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    if args.mode == "d":
        output_path = args.output or input_path.with_suffix(".json")
        try:
            convert_bin_to_json(input_path, output_path)
        except Exception as exc:
            print(f"Rev5 conversion failed: {exc}", file=sys.stderr)
            return 1
        print(f"Converted Rev5 BIN -> JSON: {output_path}")
        return 0

    output_path = args.output or input_path.with_suffix(".bin")
    try:
        convert_json_to_bin(input_path, output_path)
    except Exception as exc:
        print(f"Rev5 JSON->BIN conversion failed: {exc}", file=sys.stderr)
        return 1
    print(f"Converted Rev5 JSON -> BIN: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
