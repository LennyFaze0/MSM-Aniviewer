"""
Island Rendering Utilities

This module provides comprehensive support for rendering MSM islands, including:
- Tile grid rendering (ground tiles)
- Ground/grass animation loading
- Sky texture loading

The game uses an isometric tile system where:
- Tiles are placed on a grid with columns and rows
- Each tile's screen position is calculated using isometric projection:
  iso_x = (col - row) * tile_half_width + origin_x
  iso_y = (col + row) * tile_half_height + origin_y
- The origin_x and origin_y values from the grid header define the offset
"""

from __future__ import annotations

import os
import re
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from core.island_tiles import (
    TilesetData,
    TileGridData,
    TileGridHeader,
    TileGridEntry,
    parse_tileset_file,
    parse_tile_grid_file,
)
from core.texture_atlas import TextureAtlas
from core.data_structures import AnimationData


@dataclass
class IslandRenderConfig:
    """Configuration for island rendering."""
    
    # The island slug (e.g., "island01", "island02_mirror")
    slug: str
    
    # Animation dimensions
    anim_width: int
    anim_height: int
    
    # Whether the animation is centered (0,0 at center)
    centered: bool
    
    # Paths to support files
    tileset_path: Optional[str] = None
    grid_path: Optional[str] = None
    ground_path: Optional[str] = None
    sky_path: Optional[str] = None


@dataclass
class TileRenderInstance:
    """A single tile to be rendered."""
    
    sprite_name: str
    center_x: float
    center_y: float
    scale: float = 1.0
    alpha: float = 1.0
    depth: float = 0.0  # For sorting/layering


def extract_island_number(slug: str) -> Optional[int]:
    """Extract the island number from a slug like 'island01' or 'island15_mirror'."""
    match = re.search(r'island(\d+)', slug.lower())
    if match:
        return int(match.group(1))
    return None


def get_island_ground_suffix(island_num: int) -> str:
    """
    Return the ground file suffix for a given island number.
    
    Different islands use different ground types:
    - island01: grass
    - island02: snow
    - island03: sand (for bird variant)
    - island04: water
    - island05: rock
    - etc.
    """
    # Map of island numbers to their ground suffixes
    ground_map = {
        1: "grass",
        2: "snow",
        3: "sand",
        4: "water",
        5: "rock",
        6: "gold",
        7: "ethereal",
        8: "legendary",
        9: "tribal",
        10: "grass",
        11: "grass",
        12: "grass",
        13: "fire",
        14: "fire",
        15: "ground",
        16: "grass",
        17: "grass",
        18: "ground",
        19: "ground",
        20: "gravel",
        21: "grass",  # Seasonal Shanty
        22: "dirt",
        23: "grass",
        24: "grass",
        25: "ground",
        26: "grass",
        27: "grass",
        31: "grass",
    }
    return ground_map.get(island_num, "grass")


def get_sky_texture_name(island_num: int, variant: Optional[str] = None) -> str:
    """
    Return the sky texture filename for a given island.
    
    Args:
        island_num: The island number (1-31)
        variant: Optional variant suffix (e.g., "hal", "mirror", "veggie")
    
    Returns:
        The sky texture filename (e.g., "sky01.avif", "sky01_hal.avif")
    """
    base = f"sky{island_num:02d}"
    if variant:
        return f"{base}_{variant}.avif"
    return f"{base}.avif"


def build_tile_instances(
    grid_data: TileGridData,
    tileset_data: TilesetData,
    anim_width: int,
    anim_height: int,
    centered: bool,
) -> List[TileRenderInstance]:
    """
    Build tile render instances from grid and tileset data.
    
    This function calculates the screen position for each tile using
    isometric projection, properly aligned with the animation's coordinate system.
    
    Args:
        grid_data: Parsed tile grid data
        tileset_data: Parsed tileset data
        anim_width: Animation width in pixels
        anim_height: Animation height in pixels
        centered: Whether the animation is centered (0,0 at center)
    
    Returns:
        List of TileRenderInstance objects ready for rendering
    """
    header = grid_data.header
    entries = grid_data.entries
    
    if not entries:
        return []
    
    tile_width = float(header.tile_width)
    tile_height = float(header.tile_height)
    tile_half_w = tile_width * 0.5
    tile_half_h = tile_height * 0.5

    grid_rows = float(header.rows)

    valid_sprites = set(tileset_data.sprite_names)
    
    instances: List[TileRenderInstance] = []
    projected: List[Tuple[TileGridEntry, str, float, float]] = []
    for entry in entries:
        sprite_name = entry.sprite_name
        if not sprite_name or sprite_name not in valid_sprites:
            continue

        col = float(entry.column)
        row = float(entry.row)

        # Match game::Grid::Grid placement math from REV6 decomp.
        iso_x = (col + row + 1.0) * tile_half_w
        iso_y = (grid_rows + row - col) * tile_half_h
        projected.append((entry, sprite_name, iso_x, iso_y))
    
    if not projected:
        return instances

    for entry, sprite_name, iso_x, iso_y in projected:
        # Grid coordinates are already authored in the animation's world space.
        # Applying camera/centering offsets here double-shifts terrain.
        screen_x = iso_x
        screen_y = iso_y

        depth = entry.y_value if entry.y_value != 0.0 else iso_y

        instances.append(TileRenderInstance(
            sprite_name=sprite_name,
            center_x=screen_x,
            center_y=screen_y,
            scale=1.0,
            alpha=1.0,
            depth=depth,
        ))
    
    # Sort by depth (back to front)
    instances.sort(key=lambda t: t.depth)
    
    return instances


def find_island_support_files(
    slug: str,
    xml_bin_file_map: Dict[str, str],
    gfx_path: Optional[str] = None,
) -> IslandRenderConfig:
    """
    Locate all support files for an island animation.
    
    Args:
        slug: The island slug (e.g., "island01", "island02_mirror")
        xml_bin_file_map: Map of lowercase filenames to full paths
        gfx_path: Path to the gfx folder for sky textures
    
    Returns:
        IslandRenderConfig with paths to all found support files
    """
    config = IslandRenderConfig(
        slug=slug,
        anim_width=0,
        anim_height=0,
        centered=True,
    )
    
    # Extract island number and variant
    island_num = extract_island_number(slug)
    if island_num is None:
        return config
    
    # Determine variant suffix (e.g., "mirror", "hal", "veggie")
    variant = None
    slug_lower = slug.lower()
    if "_mirror" in slug_lower:
        variant = "mirror"
    elif "_hal" in slug_lower:
        variant = "hal"
    elif "_veggie" in slug_lower:
        variant = "veggie"
    elif "_temple" in slug_lower:
        variant = "temple"
    elif "_xmas" in slug_lower:
        variant = "xmas"
    elif "_summer" in slug_lower:
        variant = "summer"
    elif "_sand" in slug_lower:
        variant = "sand"
    elif "_bird" in slug_lower:
        variant = "bird"
    elif "_fish" in slug_lower:
        variant = "fish"
    elif "_easter" in slug_lower:
        variant = "easter"
    elif "_val" in slug_lower:
        variant = "val"
    elif "_ann" in slug_lower:
        variant = "ann"
    elif "_lifeformula" in slug_lower:
        variant = "lifeformula"
    elif "_mindboggle" in slug_lower:
        variant = "mindboggle"
    elif "_clover" in slug_lower:
        variant = "clover"
    elif "_bh" in slug_lower:
        variant = "bh"
    elif "_skypainting" in slug_lower:
        variant = "skypainting"
    elif "_newyear" in slug_lower:
        variant = "newyear"
    elif "_echosofeco" in slug_lower or "_echos_of_eco" in slug_lower:
        variant = "echos_of_eco"
    elif "_perplexplore" in slug_lower:
        variant = "perplexplore"
    elif "_thanks" in slug_lower:
        variant = "thanks"
    
    # Find tileset file
    tileset_candidates = [
        f"tileset_island{island_num:02d}_{variant}.bin" if variant else None,
        f"tileset_island{island_num:02d}.bin",
    ]
    for candidate in tileset_candidates:
        if candidate and candidate.lower() in xml_bin_file_map:
            config.tileset_path = xml_bin_file_map[candidate.lower()]
            break
    
    # Find grid file
    grid_candidates = [
        f"island{island_num:02d}_{variant}_grid.bin" if variant else None,
        f"island{island_num:02d}_mirror_grid.bin" if "mirror" in slug_lower else None,
        f"island{island_num:02d}_grid.bin",
    ]
    for candidate in grid_candidates:
        if candidate and candidate.lower() in xml_bin_file_map:
            config.grid_path = xml_bin_file_map[candidate.lower()]
            break
    
    # Find ground file
    ground_suffix = get_island_ground_suffix(island_num)
    ground_candidates = [
        f"island{island_num:02d}_{variant}_{ground_suffix}.bin" if variant else None,
        f"island{island_num:02d}_{ground_suffix}_{variant}.bin" if variant else None,
        f"island{island_num:02d}_{ground_suffix}.bin",
        f"island{island_num:02d}_ground.bin",
    ]
    for candidate in ground_candidates:
        if candidate and candidate.lower() in xml_bin_file_map:
            config.ground_path = xml_bin_file_map[candidate.lower()]
            break
    
    # Find sky texture
    if gfx_path:
        sky_candidates = [
            get_sky_texture_name(island_num, variant) if variant else None,
            get_sky_texture_name(island_num, "MIRROR") if "mirror" in slug_lower else None,
            get_sky_texture_name(island_num),
        ]
        for candidate in sky_candidates:
            if candidate:
                sky_path = os.path.join(gfx_path, candidate)
                if os.path.exists(sky_path):
                    config.sky_path = sky_path
                    break
    
    return config


def resolve_tileset_atlas_path(
    tileset_data: TilesetData,
    data_root: str,
) -> Optional[str]:
    """
    Resolve the full path to the tileset's texture atlas.
    
    Args:
        tileset_data: Parsed tileset data containing the relative atlas path
        data_root: Root data directory
    
    Returns:
        Full path to the atlas file, or None if not found
    """
    relative_path = tileset_data.atlas_path
    if not relative_path:
        return None
    
    # Clean up the path
    cleaned = relative_path.replace("\\", "/").lstrip("./")
    if not cleaned:
        return None
    
    # Try various path combinations
    candidates = []
    
    # Direct path
    candidates.append(os.path.join(data_root, cleaned))
    
    # Remove "data/" prefix if present
    if cleaned.startswith("data/"):
        candidates.append(os.path.join(data_root, cleaned[5:]))
    
    # Try with different extensions
    base, ext = os.path.splitext(cleaned)
    for alt_ext in [".bin", ".png", ".avif", ".pvr"]:
        if alt_ext != ext:
            candidates.append(os.path.join(data_root, base + alt_ext))
            if cleaned.startswith("data/"):
                candidates.append(os.path.join(data_root, base[5:] + alt_ext))
    
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    
    return None
