import argparse
import json
import struct
from pathlib import Path
from typing import List, Dict, Any, Tuple


class Buffer:
    """Helper that keeps track of the current offset while reading little-endian data."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    @property
    def remaining(self) -> bytes:
        return self._data[self._offset :]

    def tell(self) -> int:
        """Return the current read offset."""
        return self._offset

    def seek(self, offset: int) -> None:
        """Move the read cursor to an absolute offset."""
        if offset < 0 or offset > len(self._data):
            raise ValueError(f"Attempted to seek to invalid offset {offset}.")
        self._offset = offset

    def read_u32(self) -> int:
        if self._offset + 4 > len(self._data):
            raise ValueError("Unexpected end of file while reading uint32.")
        value = struct.unpack_from("<I", self._data, self._offset)[0]
        self._offset += 4
        return value

    def read_u16(self) -> int:
        if self._offset + 2 > len(self._data):
            raise ValueError("Unexpected end of file while reading uint16.")
        value = struct.unpack_from("<H", self._data, self._offset)[0]
        self._offset += 2
        return value

    def read_string(self) -> str:
        length = self.read_u32()
        if self._offset + length > len(self._data):
            raise ValueError("Unexpected end of file while reading string payload.")
        raw = self._data[self._offset : self._offset + length]
        self._offset += ((length + 3) // 4) * 4  # strings are padded to 4-byte boundaries
        return raw.rstrip(b"\x00").decode("utf-8")

    def read_f32(self) -> float:
        if self._offset + 4 > len(self._data):
            raise ValueError("Unexpected end of file while reading float.")
        value = struct.unpack_from("<f", self._data, self._offset)[0]
        self._offset += 4
        return value


def parse_apply_shader(buf: Buffer) -> List[Dict[str, str]]:
    count = buf.read_u32()
    records = []
    for _ in range(count):
        records.append(
            {
                "node": buf.read_string(),
                "resource": buf.read_string(),
            }
        )
    return records


def parse_remaps(buf: Buffer) -> List[Dict[str, Any]]:
    remap_count = buf.read_u32()
    remaps = []
    for _ in range(remap_count):
        mapping_count = buf.read_u32()
        mappings = [
            {"from": buf.read_string(), "to": buf.read_string()}
            for _ in range(mapping_count)
        ]
        remaps.append(
            {
                "display_name": buf.read_string(),
                "resource": buf.read_string(),
                "sheet": buf.read_string(),
                "frame_mappings": mappings,
            }
        )
    return remaps


def _read_signed(value: int) -> int:
    """Return the 32-bit signed representation of an unsigned value."""
    if value & 0x80000000:
        return value - 0x100000000
    return value


def parse_clone_layers(buf: Buffer) -> List[Dict[str, Any]]:
    clone_count = buf.read_u32()
    clones = []
    for _ in range(clone_count):
        source_layer = buf.read_string()
        new_layer = buf.read_string()
        reference_layer = buf.read_string()
        variant_raw = buf.read_u32()
        clones.append(
            {
                # Legacy keys preserved for compatibility
                "name": new_layer,
                "resource": source_layer,
                "sheet": reference_layer,
                "variant_index": variant_raw,  # raw value as stored in the BIN
                # Descriptive aliases for renderer logic
                "new_layer": new_layer,
                "source_layer": source_layer,
                "reference_layer": reference_layer,
                "insert_mode": _read_signed(variant_raw),
            }
        )
    return clones


def parse_set_blend_layers(buf: Buffer) -> List[Dict[str, Any]]:
    layer_count = buf.read_u32()
    layers = []
    for _ in range(layer_count):
        layers.append({"name": buf.read_string(), "blend_value": buf.read_u32()})
    return layers


def parse_ae_anim_layers(buf: Buffer) -> List[Dict[str, Any]]:
    count = buf.read_u32()
    return _parse_attachment_block(buf, count)


def parse_sheet_remaps(buf: Buffer, preset_count: int | None = None) -> List[Dict[str, str]]:
    remap_count = buf.read_u32() if preset_count is None else preset_count
    remaps = []
    for _ in range(remap_count):
        remaps.append({"from": buf.read_string(), "to": buf.read_string()})
    return remaps


def parse_layer_colors(buf: Buffer) -> List[Dict[str, Any]]:
    """Read per-layer tint overrides stored as RGBA16 values."""

    color_count = buf.read_u32()
    colors: List[Dict[str, Any]] = []
    for _ in range(color_count):
        layer_name = buf.read_string()
        rgba16 = {
            "r": buf.read_u16(),
            "g": buf.read_u16(),
            "b": buf.read_u16(),
            "a": buf.read_u16(),
        }
        rgba8 = {channel: min(value, 255) for channel, value in rgba16.items()}
        colors.append(
            {
                "layer": layer_name,
                "rgba16": rgba16,
                "rgba": rgba8,
                "hex": "#{:02X}{:02X}{:02X}{:02X}".format(
                    rgba8["r"], rgba8["g"], rgba8["b"], rgba8["a"]
                ),
            }
        )
    return colors


def _parse_attachment_block(buf: Buffer, count: int) -> List[Dict[str, Any]]:
    """Read attachment records (legacy AEAnim layers)."""
    layers: List[Dict[str, Any]] = []
    for _ in range(max(0, count)):
        attach_to = buf.read_string()
        resource = buf.read_string()
        animation = buf.read_string()
        raw_value = buf.read_f32()
        time_offset, tempo_multiplier = _decode_attachment_time_value(raw_value)
        layers.append(
            {
                "attach_to": attach_to,
                "resource": resource,
                "animation": animation,
                # Expose the raw float under both names for compatibility. The runtime
                # treats this as a time offset added to the parent layer's clock.
                "time_offset": time_offset,
                "time_scale": time_offset,
                "tempo_multiplier": tempo_multiplier,
                "loop": True,
                "raw_time_value": raw_value,
            }
        )
    return layers


def _decode_attachment_time_value(value: float) -> Tuple[float, float]:
    """
    Interpret the serialized attachment timing parameter.

    Attachments authored by the MSM tools occasionally store a tempo multiplier
    instead of a literal offset (most notably the AEAnim teleport screens). The
    exported BIN encodes that case as ~0.001. Treat very small positive values
    as tempo multipliers and clamp them to 10% speed to match the game.
    """
    if value is None:
        return 0.0, 1.0
    # Sentinel emitted by the native pipeline when the field represents a
    # tempo scaler rather than an offset (roughly 1/1000).
    if 0.0 < abs(value) <= 0.0025:
        return 0.0, max(0.1, abs(value) * 100.0)
    return value, 1.0


def parse_costume_file(data: bytes) -> Dict[str, Any]:
    buf = Buffer(data)
    parsed = {
        "apply_shader": parse_apply_shader(buf),
        "remaps": parse_remaps(buf),
        "clone_layers": parse_clone_layers(buf),
        "set_blend_layers": parse_set_blend_layers(buf),
    }

    layer_colors = parse_layer_colors(buf)
    parsed["layer_colors"] = layer_colors
    parsed["layer_color_overrides"] = layer_colors  # Legacy alias

    # Modern BINs store AE attachments before the sheet remap block, while
    # certain legacy BINs write the sheet swaps first. Parse both layouts.
    attachments_checkpoint = buf.tell()
    attachments: List[Dict[str, Any]] = []
    sheet_remaps: List[Dict[str, str]] = []
    fallback_error: ValueError | None = None
    try:
        attachments = parse_ae_anim_layers(buf)
        sheet_count = buf.read_u32()
        sheet_remaps = parse_sheet_remaps(buf, preset_count=sheet_count)
    except ValueError as exc:
        fallback_error = exc
        buf.seek(attachments_checkpoint)
        try:
            sheet_count = buf.read_u32()
            sheet_remaps = parse_sheet_remaps(buf, preset_count=sheet_count)
            attachments = parse_ae_anim_layers(buf)
        except ValueError as final_exc:
            if fallback_error:
                raise ValueError(
                    "Failed to parse costume attachments/sheet remaps block using either "
                    "known layout."
                ) from final_exc
            raise

    parsed["ae_anim_layers"] = attachments

    parsed["sheet_remaps"] = sheet_remaps
    parsed["swaps"] = sheet_remaps  # Legacy key for older JSON consumers

    if any(buf.remaining):
        raise ValueError(
            f"Unparsed trailing bytes detected at offset {len(data) - len(buf.remaining)}."
        )

    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse an MSM costume .bin file and emit a JSON description."
    )
    parser.add_argument("input", type=Path, help="Path to the costume .bin file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Destination JSON path (defaults to input name with .json extension).",
    )
    args = parser.parse_args()

    binary_path: Path = args.input
    if not binary_path.is_file():
        raise SystemExit(f"Input file {binary_path} does not exist.")

    data = binary_path.read_bytes()
    parsed = parse_costume_file(data)

    output_path = args.output or binary_path.with_suffix(".json")
    output_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False))
    print(f"Wrote parsed data to {output_path}")


if __name__ == "__main__":
    main()
