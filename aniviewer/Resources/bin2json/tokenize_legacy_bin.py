#!/usr/bin/env python3
"""
Legacy BIN tokenizer
--------------------

Older MSM builds (such as the JP/Choir distribution in this repo) ship an
earlier revision of the animation BIN format.  The modern `rev6-2-json.py`
parser cannot consume those files, but we still need to introspect them so the
viewer team can gradually reverse engineer the structure.

This helper walks the binary sequentially, classifying every 32-bit word as
either a raw integer/float or a potential length-prefixed C-string.  The result
is streamed to JSON so very large BINs can be inspected without loading the
entire file into memory.

Example:
    python tokenize_legacy_bin.py path/to/monster_e.bin \
        -o monster_e.tokens.json
"""

from __future__ import annotations

import argparse
import io
import json
import os
import struct
from pathlib import Path
from typing import BinaryIO

MAX_STRING_LEN = 0x2000  # 8 KB guard so bogus lengths do not explode


def looks_like_c_string(payload: bytes) -> bool:
    """True if payload resembles a padded ASCII string."""
    if not payload or payload[-1] != 0:
        return False
    core = payload[:-1]
    if not core:
        return True
    return all(32 <= b <= 126 for b in core)


class JsonTokenWriter:
    """Stream JSON objects to disk without buffering the entire list."""

    def __init__(self, dst_path: Path, source: Path, size: int) -> None:
        self.dst_path = dst_path
        self.fp: BinaryIO = open(dst_path, "w", encoding="utf-8")
        self._written = False
        header = {
            "source": str(source),
            "size": size,
            "tokens": [],
        }
        # Manually seed the JSON array so we can append entries incrementally.
        self.fp.write('{"source": ')
        json.dump(str(source), self.fp)
        self.fp.write(', "size": ')
        self.fp.write(str(size))
        self.fp.write(', "tokens": [\n')
        self._first = True

    def _emit(self, obj: dict) -> None:
        if not self._first:
            self.fp.write(",\n")
        json.dump(obj, self.fp, ensure_ascii=False)
        self._first = False
        self._written = True

    def write_number(self, offset: int, value: int) -> None:
        float_value = struct.unpack("<f", struct.pack("<I", value))[0]
        self._emit(
            {
                "offset": offset,
                "type": "u32",
                "value": value,
                "float": float_value,
            }
        )

    def write_string(self, offset: int, length: int, text: str) -> None:
        self._emit(
            {
                "offset": offset,
                "type": "string",
                "length": length,
                "value": text,
            }
        )

    def close(self) -> None:
        if not self._written:
            self.fp.write("]\n}")
        else:
            self.fp.write("\n]\n}")
        self.fp.close()


class LegacyTokenizer:
    """Tokenize a legacy BIN file without loading it entirely."""

    def __init__(self, src_path: Path) -> None:
        self.src_path = src_path

    def process(self, writer: JsonTokenWriter) -> None:
        with open(self.src_path, "rb") as fh:
            while True:
                offset = fh.tell()
                chunk = fh.read(4)
                if len(chunk) < 4:
                    break
                value = struct.unpack("<I", chunk)[0]

                if 0 < value <= MAX_STRING_LEN:
                    blob = fh.read(value)
                    if len(blob) == value and looks_like_c_string(blob):
                        text = blob.rstrip(b"\x00").decode("ascii", errors="replace")
                        writer.write_string(offset, value, text)
                        pad = (4 - (value % 4)) % 4
                        if pad:
                            fh.read(pad)
                        continue
                    # Not a string; rewind to reprocess the bytes as raw ints.
                    fh.seek(-len(blob), os.SEEK_CUR)

                writer.write_number(offset, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tokenize a legacy MSM animation BIN into a JSON dump."
    )
    parser.add_argument("input", type=Path, help="Path to the legacy BIN file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Destination JSON path (defaults to <input>.tokens.json).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    src: Path = args.input
    if not src.is_file():
        raise SystemExit(f"Input file not found: {src}")

    dst: Path = args.output or src.with_suffix(src.suffix + ".tokens.json")
    tokenizer = LegacyTokenizer(src)
    writer = JsonTokenWriter(dst, src, src.stat().st_size)
    try:
        tokenizer.process(writer)
    finally:
        writer.close()
    print(f"Wrote {dst}")


if __name__ == "__main__":
    main()
