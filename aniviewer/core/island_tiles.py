"""
Utilities for parsing island tile BIN formats (tilesets + tile grids).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Union

from utils.binary_reader import BinaryReader


PathLike = Union[str, Path]


@dataclass
class TilesetData:
    """Represents a tileset manifest referencing a binary atlas."""

    atlas_path: str
    sprite_names: List[str]
    sprite_offsets: Dict[str, tuple[int, int]]


@dataclass
class TileGridHeader:
    columns: int
    rows: int
    tile_width: int
    tile_height: int
    origin_x: int
    origin_y: int
    bounds_width: int
    bounds_height: int


@dataclass
class TileGridEntry:
    sprite_name: str
    column: int
    row: int
    y_value: float
    flags: int
    tail: int


@dataclass
class TileGridData:
    header: TileGridHeader
    entries: List[TileGridEntry]


def parse_tileset_file(path: PathLike) -> TilesetData:
    """Parse tileset metadata."""
    reader = BinaryReader.from_file(path)
    atlas_rel = reader.read_string()
    sprite_count = reader.read_u32()
    names: List[str] = []
    offsets: Dict[str, tuple[int, int]] = {}
    for _ in range(sprite_count):
        # REV6 tile entries are: sprite_name + 4-byte payload.
        # The payload is two int16 offsets consumed by TileAtlasEntry::set.
        sprite_name = reader.read_string()
        offset_x = reader.read_i16()
        offset_y = reader.read_i16()
        if sprite_name:
            names.append(sprite_name)
            offsets[sprite_name] = (offset_x, offset_y)
    return TilesetData(atlas_rel, names, offsets)


def parse_tile_grid_file(path: PathLike) -> TileGridData:
    """Parse tile grid placements."""
    reader = BinaryReader.from_file(path)
    columns = reader.read_u16()
    rows = reader.read_u16()
    tile_width = reader.read_u16()
    tile_height = reader.read_u16()
    origin_x = reader.read_i16()
    origin_y = reader.read_i16()
    bounds_width = reader.read_u16()
    bounds_height = reader.read_u16()
    # Remaining three u16 pairs are reserved.
    _ = reader.read_u16()
    _ = reader.read_u16()
    _ = reader.read_u16()
    _ = reader.read_u16()
    _ = reader.read_u16()
    _ = reader.read_u16()

    tile_count = reader.read_u32()
    entries: List[TileGridEntry] = []
    for _ in range(tile_count):
        sprite_name = reader.read_string()
        packed = reader.read_u32()
        # REV6 grid packs row/col as two u16s; swapped ordering matches game layout.
        row = packed >> 16
        column = packed & 0xFFFF
        y_value = reader.read_float()
        flags = reader.read_u32()
        reader.read_string()  # secondary string (unused in current builds)
        tail = reader.read_u32()
        entries.append(
            TileGridEntry(
                sprite_name=sprite_name,
                column=column,
                row=row,
                y_value=y_value,
                flags=flags,
                tail=tail,
            )
        )

    if bounds_width <= 0:
        bounds_width = tile_width * columns
    if bounds_height <= 0:
        bounds_height = tile_height * rows

    header = TileGridHeader(
        columns=columns,
        rows=rows,
        tile_width=tile_width,
        tile_height=tile_height,
        origin_x=origin_x,
        origin_y=origin_y,
        bounds_width=bounds_width,
        bounds_height=bounds_height,
    )
    return TileGridData(header=header, entries=entries)
