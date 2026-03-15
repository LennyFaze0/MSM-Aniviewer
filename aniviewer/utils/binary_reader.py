"""
BinaryReader helper
Lightweight struct reader for custom BIN formats.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Union


class BinaryReader:
    """Convenience wrapper around struct-based binary parsing."""

    def __init__(self, data: Union[bytes, bytearray, memoryview]):
        self._data = memoryview(data)
        self._offset = 0

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "BinaryReader":
        raw_path = Path(path)
        with raw_path.open("rb") as handle:
            blob = handle.read()
        return cls(blob)

    @property
    def offset(self) -> int:
        return self._offset

    def seek(self, position: int) -> None:
        if position < 0 or position > len(self._data):
            raise ValueError("seek position out of range")
        self._offset = position

    def tell(self) -> int:
        return self._offset

    def remaining(self) -> int:
        return len(self._data) - self._offset

    def align(self, multiple: int) -> None:
        if multiple <= 0:
            return
        mask = multiple - 1
        self._offset = (self._offset + mask) & ~mask

    def read(self, size: int) -> bytes:
        if self._offset + size > len(self._data):
            raise EOFError("read exceeds buffer")
        chunk = self._data[self._offset : self._offset + size]
        self._offset += size
        return bytes(chunk)

    def read_u32(self) -> int:
        value = struct.unpack_from("<I", self._data, self._offset)[0]
        self._offset += 4
        return value

    def read_u16(self) -> int:
        value = struct.unpack_from("<H", self._data, self._offset)[0]
        self._offset += 2
        return value

    def read_i16(self) -> int:
        value = struct.unpack_from("<h", self._data, self._offset)[0]
        self._offset += 2
        return value

    def read_float(self) -> float:
        value = struct.unpack_from("<f", self._data, self._offset)[0]
        self._offset += 4
        return value

    def read_string(self) -> str:
        length = self.read_u32()
        blob = self.read(length)
        self.align(4)
        return blob.rstrip(b"\x00").decode("ascii", errors="ignore")

