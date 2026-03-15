"""
Data structures for MSM Animation Viewer
Defines the core data types used throughout the application
"""

from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any, Set


@dataclass
class SpriteInfo:
    """Information about a sprite from the texture atlas"""
    name: str
    x: int
    y: int
    w: int
    h: int
    pivot_x: float = 0.5
    pivot_y: float = 0.5
    offset_x: float = 0.0
    offset_y: float = 0.0
    original_w: float = 0.0
    original_h: float = 0.0
    rotated: bool = False
    vertices: List[Tuple[float, float]] = field(default_factory=list)
    vertices_uv: List[Tuple[float, float]] = field(default_factory=list)
    triangles: List[int] = field(default_factory=list)
    
    # Derived values calculated from original dimensions and offsets
    # These match what the game calculates in FUN_005862a0
    derived_w: float = 0.0  # oW - oX - x (or oW - y - oX if rotated)
    derived_h: float = 0.0  # oH - oY - y (or oH - oY - x if rotated)

    @property
    def has_polygon_mesh(self) -> bool:
        """Return True if this sprite stores polygon mesh data."""
        return (
            len(self.vertices) >= 3
            and len(self.triangles) >= 3
            and len(self.vertices_uv) == len(self.vertices)
        )


@dataclass
class KeyframeLane:
    """Named collection of keyframes used as an additive lane."""
    name: str
    keyframes: List["KeyframeData"] = field(default_factory=list)


@dataclass
class KeyframeData:
    """Keyframe data for animation"""
    time: float
    pos_x: float = 0.0
    pos_y: float = 0.0
    depth: float = 0.0
    scale_x: float = 100.0
    scale_y: float = 100.0
    rotation: float = 0.0
    opacity: float = 100.0
    sprite_name: str = ""
    r: int = 255
    g: int = 255
    b: int = 255
    a: int = 255
    immediate_pos: int = 0
    immediate_depth: int = -1
    immediate_scale: int = 0
    immediate_rotation: int = 0
    immediate_opacity: int = 0
    immediate_sprite: int = 0
    immediate_rgb: int = -1


@dataclass
class LayerData:
    """Layer information"""
    name: str
    layer_id: int
    parent_id: int
    anchor_x: float
    anchor_y: float
    blend_mode: int
    keyframes: List[KeyframeData]
    visible: bool = True
    shader_name: Optional[str] = None
    color_tint: Optional[Tuple[float, float, float, float]] = None
    color_tint_hdr: Optional[Tuple[float, float, float, float]] = None
    color_gradient: Optional[Dict[str, Any]] = None
    color_animator: Optional[Dict[str, Any]] = None
    color_metadata: Optional[Dict[str, Any]] = None
    render_tags: Set[str] = field(default_factory=set)
    mask_role: Optional[str] = None
    mask_key: Optional[str] = None
    sprite_anchor_map: Optional[Dict[str, Tuple[float, float]]] = None
    has_depth: bool = False
    extra_keyframe_lanes: List[KeyframeLane] = field(default_factory=list)


@dataclass
class AnimationData:
    """Animation information"""
    name: str
    width: int
    height: int
    loop_offset: float
    centered: int
    layers: List[LayerData]
    dof_anim_flip_y: bool = False
    global_keyframe_lanes: List[KeyframeLane] = field(default_factory=list)
