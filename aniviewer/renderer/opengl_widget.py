"""
OpenGL Animation Widget
Qt widget that handles OpenGL rendering, camera controls, and user interaction
"""

import os
os.environ.setdefault('QT_OPENGL', 'desktop')

import time
import math
import bisect
from dataclasses import dataclass, field
from typing import Any, List, Dict, Tuple, Optional, Set

import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRectF
from PyQt6.QtGui import QSurfaceFormat, QPainter, QFont, QColor, QPalette, QImage
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL.GL import *
from OpenGL.GLU import *

from core.data_structures import AnimationData, LayerData
from core.constraints import ConstraintManager
from core.animation_player import AnimationPlayer
from core.texture_atlas import TextureAtlas
from .sprite_renderer import SpriteRenderer, reset_blend_mode, set_blend_mode

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency
    Image = None
try:
    from PyQt6.QtSvg import QSvgRenderer
except Exception:  # pragma: no cover - optional dependency
    QSvgRenderer = None
from utils.shader_registry import ShaderRegistry

_POST_AA_VERTEX_SHADER = """
#version 120
void main()
{
    gl_Position = ftransform();
    gl_TexCoord[0] = gl_MultiTexCoord0;
}
"""

# Hidden/PostProcessing/FinalPass-style FXAA pass for the fixed-function
# viewport pipeline. This is a lightweight approximation of the game's
# fullscreen final AA behavior and keeps source alpha intact.
_POST_AA_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_texture;
uniform sampler2D u_prevTexture;
uniform vec2 u_texelSize;
uniform float u_strength;
uniform int u_aaMode;          // 0=FXAA (FinalPass style), 1=SMAA-like approximation
uniform int u_aaEnabled;
uniform int u_bloomEnabled;
uniform float u_bloomStrength;
uniform float u_bloomThreshold;
uniform float u_bloomRadius;
uniform int u_vignetteEnabled;
uniform float u_vignetteStrength;
uniform int u_grainEnabled;
uniform float u_grainStrength;
uniform int u_caEnabled;
uniform float u_caStrength;
uniform int u_motionBlurEnabled;
uniform float u_motionBlurStrength;
uniform float u_time;

float luma(vec3 c)
{
    return dot(c, vec3(0.299, 0.587, 0.114));
}

float hash12(vec2 p)
{
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

vec3 apply_smaa_like(vec2 uv, vec3 src)
{
    vec4 c = vec4(src, 1.0);
    vec4 l = texture2D(u_texture, uv + vec2(-u_texelSize.x, 0.0));
    vec4 r = texture2D(u_texture, uv + vec2( u_texelSize.x, 0.0));
    vec4 t = texture2D(u_texture, uv + vec2(0.0, -u_texelSize.y));
    vec4 b = texture2D(u_texture, uv + vec2(0.0,  u_texelSize.y));
    vec4 d1 = texture2D(u_texture, uv + vec2(-u_texelSize.x, -u_texelSize.y));
    vec4 d2 = texture2D(u_texture, uv + vec2( u_texelSize.x, -u_texelSize.y));
    vec4 d3 = texture2D(u_texture, uv + vec2(-u_texelSize.x,  u_texelSize.y));
    vec4 d4 = texture2D(u_texture, uv + vec2( u_texelSize.x,  u_texelSize.y));

    float lc = luma(c.rgb);
    float edge_h = abs(luma(l.rgb) - lc) + abs(luma(r.rgb) - lc);
    float edge_v = abs(luma(t.rgb) - lc) + abs(luma(b.rgb) - lc);
    float edge_d = abs(luma(d1.rgb) - lc) + abs(luma(d2.rgb) - lc)
                 + abs(luma(d3.rgb) - lc) + abs(luma(d4.rgb) - lc);

    float edge = clamp((edge_h + edge_v + 0.5 * edge_d) * 0.65, 0.0, 1.0);
    float w = clamp(edge * clamp(u_strength, 0.0, 1.0), 0.0, 1.0);

    vec3 axial = (l.rgb + r.rgb + t.rgb + b.rgb) * 0.25;
    vec3 diag = (d1.rgb + d2.rgb + d3.rgb + d4.rgb) * 0.25;
    vec3 neighborhood = mix(axial, diag, 0.35);
    return mix(c.rgb, neighborhood, w * 0.72);
}

vec3 apply_fxaa(vec2 uv, vec3 src)
{
    vec3 rgbNW = texture2D(u_texture, uv + vec2(-u_texelSize.x, -u_texelSize.y)).rgb;
    vec3 rgbNE = texture2D(u_texture, uv + vec2( u_texelSize.x, -u_texelSize.y)).rgb;
    vec3 rgbSW = texture2D(u_texture, uv + vec2(-u_texelSize.x,  u_texelSize.y)).rgb;
    vec3 rgbSE = texture2D(u_texture, uv + vec2( u_texelSize.x,  u_texelSize.y)).rgb;
    vec3 rgbM  = src;

    float lumaNW = luma(rgbNW);
    float lumaNE = luma(rgbNE);
    float lumaSW = luma(rgbSW);
    float lumaSE = luma(rgbSE);
    float lumaM  = luma(rgbM);

    float lumaMin = min(lumaM, min(min(lumaNW, lumaNE), min(lumaSW, lumaSE)));
    float lumaMax = max(lumaM, max(max(lumaNW, lumaNE), max(lumaSW, lumaSE)));
    float edge = max(0.0, lumaMax - lumaMin);
    float edgeWeight = clamp((edge - 0.015) * 8.0, 0.0, 1.0);

    vec2 dir;
    dir.x = -((lumaNW + lumaNE) - (lumaSW + lumaSE));
    dir.y =  ((lumaNW + lumaSW) - (lumaNE + lumaSE));

    const float FXAA_REDUCE_MIN = 1.0 / 128.0;
    const float FXAA_REDUCE_MUL = 1.0 / 8.0;
    const float FXAA_SPAN_MAX = 8.0;

    float dirReduce = max(
        (lumaNW + lumaNE + lumaSW + lumaSE) * (0.25 * FXAA_REDUCE_MUL),
        FXAA_REDUCE_MIN
    );
    float rcpDirMin = 1.0 / (min(abs(dir.x), abs(dir.y)) + dirReduce);
    float strength = clamp(u_strength, 0.0, 1.0);
    dir = clamp(dir * rcpDirMin, vec2(-FXAA_SPAN_MAX), vec2(FXAA_SPAN_MAX));
    dir *= u_texelSize * mix(0.35, 1.0, strength);

    vec3 rgbA = 0.5 * (
        texture2D(u_texture, uv + dir * (1.0 / 3.0 - 0.5)).rgb +
        texture2D(u_texture, uv + dir * (2.0 / 3.0 - 0.5)).rgb
    );
    vec3 rgbB = rgbA * 0.5 + 0.25 * (
        texture2D(u_texture, uv + dir * -0.5).rgb +
        texture2D(u_texture, uv + dir *  0.5).rgb
    );

    float lumaB = luma(rgbB);
    vec3 fxaaColor = ((lumaB < lumaMin) || (lumaB > lumaMax)) ? rgbA : rgbB;
    float blend = clamp(strength * edgeWeight, 0.0, 1.0);
    return mix(rgbM, fxaaColor, blend);
}

float bright_weight(vec3 c, float threshold)
{
    float m = max(max(c.r, c.g), c.b);
    return max(m - threshold, 0.0);
}

vec3 apply_bloom(vec2 uv, vec3 src)
{
    if (u_bloomEnabled == 0 || u_bloomStrength <= 0.0001)
        return src;

    vec2 stepv = u_texelSize * max(u_bloomRadius, 0.5);
    vec3 s0 = texture2D(u_texture, uv + vec2( stepv.x, 0.0)).rgb;
    vec3 s1 = texture2D(u_texture, uv + vec2(-stepv.x, 0.0)).rgb;
    vec3 s2 = texture2D(u_texture, uv + vec2(0.0,  stepv.y)).rgb;
    vec3 s3 = texture2D(u_texture, uv + vec2(0.0, -stepv.y)).rgb;
    vec3 s4 = texture2D(u_texture, uv + vec2( stepv.x,  stepv.y)).rgb;
    vec3 s5 = texture2D(u_texture, uv + vec2(-stepv.x,  stepv.y)).rgb;
    vec3 s6 = texture2D(u_texture, uv + vec2( stepv.x, -stepv.y)).rgb;
    vec3 s7 = texture2D(u_texture, uv + vec2(-stepv.x, -stepv.y)).rgb;

    float w0 = bright_weight(s0, u_bloomThreshold);
    float w1 = bright_weight(s1, u_bloomThreshold);
    float w2 = bright_weight(s2, u_bloomThreshold);
    float w3 = bright_weight(s3, u_bloomThreshold);
    float w4 = bright_weight(s4, u_bloomThreshold);
    float w5 = bright_weight(s5, u_bloomThreshold);
    float w6 = bright_weight(s6, u_bloomThreshold);
    float w7 = bright_weight(s7, u_bloomThreshold);

    float wsum = w0 + w1 + w2 + w3 + w4 + w5 + w6 + w7;
    vec3 bloom = vec3(0.0);
    if (wsum > 0.0001) {
        bloom = (
            s0 * w0 + s1 * w1 + s2 * w2 + s3 * w3 +
            s4 * w4 + s5 * w5 + s6 * w6 + s7 * w7
        ) / wsum;
    }

    return src + bloom * clamp(u_bloomStrength, 0.0, 2.0);
}

vec3 apply_chromatic_aberration(vec2 uv, vec3 src)
{
    if (u_caEnabled == 0 || u_caStrength <= 0.0001)
        return src;
    vec2 fromCenter = uv - vec2(0.5, 0.5);
    float dist = length(fromCenter);
    vec2 off = fromCenter * dist * u_caStrength * 2.0 * u_texelSize;
    float r = texture2D(u_texture, uv + off).r;
    float g = src.g;
    float b = texture2D(u_texture, uv - off).b;
    return vec3(r, g, b);
}

vec3 apply_vignette(vec2 uv, vec3 src)
{
    if (u_vignetteEnabled == 0 || u_vignetteStrength <= 0.0001)
        return src;
    vec2 p = (uv - vec2(0.5, 0.5)) * 2.0;
    float radial = dot(p, p);
    float v = 1.0 - smoothstep(0.35, 1.45, radial) * clamp(u_vignetteStrength, 0.0, 1.0);
    return src * max(v, 0.0);
}

vec3 apply_grain(vec2 uv, vec3 src)
{
    if (u_grainEnabled == 0 || u_grainStrength <= 0.0001)
        return src;
    float n = hash12(uv * vec2(1920.0, 1080.0) + vec2(u_time * 37.0, u_time * 17.0));
    float g = (n - 0.5) * clamp(u_grainStrength, 0.0, 1.0) * 0.12;
    return src + vec3(g);
}

void main()
{
    vec2 uv = gl_TexCoord[0].xy;
    vec4 c = texture2D(u_texture, uv);
    vec3 out_rgb = c.rgb;
    if (u_aaEnabled != 0) {
        out_rgb = (u_aaMode == 1)
            ? apply_smaa_like(uv, c.rgb)
            : apply_fxaa(uv, c.rgb);
    }
    out_rgb = apply_bloom(uv, out_rgb);
    out_rgb = apply_chromatic_aberration(uv, out_rgb);
    out_rgb = apply_vignette(uv, out_rgb);
    out_rgb = apply_grain(uv, out_rgb);
    if (u_motionBlurEnabled != 0 && u_motionBlurStrength > 0.0001) {
        vec3 prev_rgb = texture2D(u_prevTexture, uv).rgb;
        out_rgb = mix(out_rgb, prev_rgb, clamp(u_motionBlurStrength, 0.0, 0.95));
    }
    out_rgb = clamp(out_rgb, 0.0, 1.0);

    // Match Unity FinalPass "keep alpha" behavior for transparent workflows.
    gl_FragColor = vec4(out_rgb, c.a);
}
"""


@dataclass
class AttachmentInstance:
    """Runtime state for an attached animation."""
    instance_id: int
    name: str
    target_layer: str
    target_layer_id: Optional[int]
    player: AnimationPlayer
    atlases: List[TextureAtlas]
    time_offset: float
    tempo_multiplier: float = 1.0
    loop: bool = True
    root_layer_name: Optional[str] = None
    allow_base_fallback: bool = False
    visible: bool = True


@dataclass
class TileInstance:
    """Static tile placement rendered outside the animation layer stack."""

    sprite_name: str
    center_x: float
    center_y: float
    scale: float = 1.0
    alpha: float = 1.0
    flag: int = 0
    depth: float = 0.0
    row: int = 0
    column: int = 0


@dataclass
class TileBatch:
    """Group of tiles sharing a TextureAtlas."""

    atlas: TextureAtlas
    instances: List[TileInstance]
    alpha: float = 1.0
    depth: float = -0.5


@dataclass
class TerrainComposite:
    """Pre-stitched island terrain texture rendered as one quad."""

    width: int
    height: int
    origin_x: float
    origin_y: float
    rgba_bytes: bytes
    tile_count: int = 0
    flag0_count: int = 0
    flag1_count: int = 0
    alpha: float = 1.0
    depth: float = -0.5


@dataclass
class ParticleRenderEntry:
    """Runtime particle rendering data for DOF particle nodes."""

    name: str
    texture_image: Optional["Image.Image"]
    texture_id: Optional[int]
    texture_width: int
    texture_height: int
    base_width: float
    base_height: float
    blend_mode: int
    offset: Tuple[float, float, float]
    parallax: Tuple[float, float]
    image_scale: float
    prefab_pos: Tuple[float, float, float]
    prefab_rot: Tuple[float, float, float, float]
    simulation_space: int
    custom_space_pos: Tuple[float, float, float]
    custom_space_rot: Tuple[float, float, float, float]
    shape_position: Tuple[float, float, float]
    shape_rotation: Tuple[float, float, float]
    shape_scale: Tuple[float, float, float]
    channels: Dict[int, List[Tuple[float, float, int]]]
    material_color: Tuple[float, float, float, float]
    material_floats: Dict[str, float]
    seed_base: int
    emission_rate_range: Tuple[float, float]
    emission_distance_range: Tuple[float, float]
    lifetime_range: Tuple[float, float]
    speed_range: Tuple[float, float]
    start_rotation_range: Tuple[float, float]
    size_range: Tuple[float, float]
    size_y_range: Optional[Tuple[float, float]]
    color_range: Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float]]
    color_over_lifetime_keys: List[Tuple[float, Tuple[float, float, float]]]
    alpha_over_lifetime_keys: List[Tuple[float, float]]
    size_over_lifetime_keys: List[Tuple[float, float]]
    size_over_lifetime_y_keys: List[Tuple[float, float]]
    rotation_over_lifetime_keys: List[Tuple[float, float]]
    rotation_over_lifetime_range: Tuple[float, float]
    move_with_transform: bool
    velocity_over_lifetime_range: Tuple[Tuple[float, float], Tuple[float, float]]
    velocity_in_world_space: bool
    velocity_module_enabled: bool
    emitter_velocity_mode: int
    gravity_modifier_range: Tuple[float, float]
    shape_type: int
    shape_placement_mode: int
    shape_radius: float
    shape_radius_thickness: float
    shape_angle: float
    shape_length: float
    shape_box: Tuple[float, float]
    control_name: Optional[str] = None
    control_offset: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    control_channels: Optional[Dict[int, List[Tuple[float, float, int]]]] = None
    control_local_offset: Optional[Tuple[float, float]] = None
    control_local_offset_std: Optional[Tuple[float, float]] = None
    source_layer_id: Optional[int] = None
    source_layer_name: Optional[str] = None
    source_layer_offset_local: Optional[Tuple[float, float]] = None
    source_layer_offset_std: Optional[Tuple[float, float]] = None
    source_surface_direction: Optional[Tuple[float, float]] = None


@dataclass
class ParticleDraw:
    depth: float
    texture_id: int
    blend_mode: int
    color: Tuple[float, float, float, float]
    x: float
    y: float
    rotation: float
    scale_x: float
    scale_y: float
    base_width: float
    base_height: float
    authoritative_depth: bool = False


@dataclass
class ParticleDebugSample:
    emitter_name: str
    emitter_origin: Tuple[float, float]
    spawn_point: Tuple[float, float]
    cone_axis: Tuple[float, float]
    velocity: Tuple[float, float]
    gravity: Tuple[float, float]
    particle_pos: Tuple[float, float]
    age: float
    life: float
    control_origin: Optional[Tuple[float, float]] = None
    derived_socket: Optional[Tuple[float, float]] = None
    node_xy_origin: Optional[Tuple[float, float]] = None
    node_alt_depth_origin: Optional[Tuple[float, float]] = None


@dataclass
class ParticleActiveState:
    idx: int
    emit_time: float
    life: float


@dataclass
class ParticleEmitterRuntimeState:
    spawn_budget: float = 0.0
    next_idx: int = 0
    prev_source_pos: Optional[Tuple[float, float, float]] = None
    active: List[ParticleActiveState] = field(default_factory=list)


class OpenGLAnimationWidget(QOpenGLWidget):
    """
    OpenGL widget for rendering animations
    Handles rendering, camera controls, and user interaction
    """

    animation_time_changed = pyqtSignal(float, float)
    animation_looped = pyqtSignal()
    playback_state_changed = pyqtSignal(bool)
    transform_action_committed = pyqtSignal(dict)
    tile_render_stats = pyqtSignal(dict)
    
    def __init__(self, parent: Optional[QWidget] = None, shader_registry: Optional[ShaderRegistry] = None):
        super().__init__(parent)
        
        # Core components
        self.texture_atlases: List[TextureAtlas] = []
        self.player = AnimationPlayer()
        self.renderer = SpriteRenderer()
        self.renderer.set_shader_registry(shader_registry)
        self.renderer.set_costume_pivot_adjustment_enabled(False)
        self.antialias_enabled: bool = True
        self.zoom_to_cursor: bool = True
        self.attachment_instances: List[AttachmentInstance] = []
        self.attachment_offsets: Dict[int, Tuple[float, float]] = {}
        self.selected_attachment_id: Optional[int] = None
        self._attachment_bounds: Dict[int, Tuple[float, float, float, float]] = {}
        self._attachment_world_states: Dict[int, Dict[int, Dict[str, float]]] = {}
        self._attachment_atlas_chains: Dict[int, List[TextureAtlas]] = {}
        self.dragging_attachment: bool = False
        self.dragged_attachment_id: Optional[int] = None
        self.layer_atlas_overrides: Dict[int, List[TextureAtlas]] = {}
        self.layer_pivot_context: Dict[int, bool] = {}
        self.tile_batches: List[TileBatch] = []
        self.terrain_composite: Optional[TerrainComposite] = None
        self._terrain_composite_texture_id: Optional[int] = None
        configured_tile_path = os.environ.get("ANIVIEWER_TILE_RENDER_PATH", "full_quad").strip().lower()
        if configured_tile_path not in {"diamond_fan", "full_quad"}:
            configured_tile_path = "full_quad"
        self.tile_render_path: str = configured_tile_path
        configured_tile_filter = os.environ.get("ANIVIEWER_TILE_FILTER", "nearest").strip().lower()
        if configured_tile_filter not in {"nearest", "linear"}:
            configured_tile_filter = "nearest"
        self.tile_filter_mode: str = configured_tile_filter
        self.sprite_filter_mode: str = "bilinear"
        self.sprite_filter_strength: float = 1.0
        self.dof_alpha_smoothing_enabled: bool = False
        self.dof_alpha_smoothing_strength: float = 0.5
        self.dof_alpha_smoothing_mode: str = "normal"
        self.dof_sprite_shader_mode: str = "auto"
        self.renderer.set_texture_filter_mode(self.sprite_filter_mode)
        self.renderer.set_texture_filter_strength(self.sprite_filter_strength)
        self.renderer.set_dof_alpha_smoothing_enabled(self.dof_alpha_smoothing_enabled)
        self.renderer.set_dof_alpha_smoothing_strength(self.dof_alpha_smoothing_strength)
        self.renderer.set_dof_alpha_smoothing_mode(self.dof_alpha_smoothing_mode)
        self.renderer.set_dof_sprite_shader_mode(self.dof_sprite_shader_mode)
        configured_flag_order = os.environ.get("ANIVIEWER_TILE_FLAG_ORDER", "flag0_then1").strip().lower()
        if configured_flag_order not in {"as_is", "flag0_then1", "flag1_then0"}:
            configured_flag_order = "flag0_then1"
        self.tile_flag_order_mode: str = configured_flag_order
        configured_flag1_transform = os.environ.get("ANIVIEWER_TILE_FLAG1_TRANSFORM", "none").strip().lower()
        if configured_flag1_transform not in {"none", "hflip", "vflip", "hvflip"}:
            configured_flag1_transform = "none"
        self.tile_flag1_transform_mode: str = configured_flag1_transform
        self.tile_grid_mirrored: bool = False
        self.tile_global_offset_x: float = 0.0
        self.tile_global_offset_y: float = 0.0
        self.tile_global_rotation_deg: float = 0.0
        self.tile_global_scale: float = 1.0
        self.tile_selected_index: int = -1
        self.tile_selected_offset_x: float = 0.0
        self.tile_selected_offset_y: float = 0.0
        self.tile_selected_rotation_deg: float = 0.0
        self.tile_selected_scale: float = 1.0
        self._tile_stats_emit_interval_s: float = 1.0
        self._tile_stats_last_emit: float = 0.0
        self._tile_last_signature: str = ""
        self._tile_last_stats: Dict[str, Any] = {"path": self.tile_render_path, "ms": 0.0, "tile_count": 0}

        # Optional extra animation entries to render in the same viewport.
        # Each entry: {"animation": AnimationData, "atlases": List[TextureAtlas],
        #              "time": float, "offset_x": float, "offset_y": float,
        #              "duration": float}
        self.extra_render_entries: List[Dict[str, Any]] = []
        self._animation_layer_cache: Dict[int, Dict[str, Any]] = {}
        self.fast_preview_enabled: bool = False

        # DOF particle rendering
        self.particle_entries: List[ParticleRenderEntry] = []
        self._particle_flip_y: bool = False
        self.particle_origin_offset_x: float = 0.0
        self.particle_origin_offset_y: float = 0.0
        self._particle_texture_failures: Set[str] = set()
        self.particle_force_world_space: bool = False
        self.particle_viewport_cap: int = 1000
        self.particle_distance_sensitivity: float = 0.5
        self._particle_emission_cache: Dict[Tuple[Any, ...], List[float]] = {}
        self._particle_sample_cache: Dict[Tuple[int, int], Tuple[Any, ...]] = {}
        self._particle_atlas_alpha_cache: Dict[Tuple[str, float], Optional[np.ndarray]] = {}
        self._particle_debug_samples: List[ParticleDebugSample] = []
        self._particle_debug_info: Dict[str, Any] = {}
        self._particle_runtime_signature: Optional[Tuple[Any, ...]] = None
        self._particle_runtime_time: float = 0.0
        self._particle_runtime_states: Dict[int, ParticleEmitterRuntimeState] = {}
        
        # Rendering settings
        self.render_scale: float = 1.0
        self.background_color = (0.2, 0.2, 0.2, 1.0)
        self.post_aa_enabled: bool = False
        self.post_aa_mode: str = "fxaa"  # fxaa | smaa
        self.post_aa_strength: float = 0.5
        self.post_bloom_enabled: bool = False
        self.post_bloom_strength: float = 0.15
        self.post_bloom_threshold: float = 0.6
        self.post_bloom_radius: float = 1.5
        self.post_vignette_enabled: bool = False
        self.post_vignette_strength: float = 0.25
        self.post_grain_enabled: bool = False
        self.post_grain_strength: float = 0.2
        self.post_ca_enabled: bool = False
        self.post_ca_strength: float = 0.25
        self.post_motion_blur_enabled: bool = False
        self.post_motion_blur_strength: float = 0.35
        self._post_aa_program: int = 0
        self._post_aa_uniform_texture: int = -1
        self._post_aa_uniform_prev_texture: int = -1
        self._post_aa_uniform_texel_size: int = -1
        self._post_aa_uniform_strength: int = -1
        self._post_aa_uniform_mode: int = -1
        self._post_aa_uniform_aa_enabled: int = -1
        self._post_aa_uniform_bloom_enabled: int = -1
        self._post_aa_uniform_bloom_strength: int = -1
        self._post_aa_uniform_bloom_threshold: int = -1
        self._post_aa_uniform_bloom_radius: int = -1
        self._post_aa_uniform_vignette_enabled: int = -1
        self._post_aa_uniform_vignette_strength: int = -1
        self._post_aa_uniform_grain_enabled: int = -1
        self._post_aa_uniform_grain_strength: int = -1
        self._post_aa_uniform_ca_enabled: int = -1
        self._post_aa_uniform_ca_strength: int = -1
        self._post_aa_uniform_motion_blur_enabled: int = -1
        self._post_aa_uniform_motion_blur_strength: int = -1
        self._post_aa_uniform_time: int = -1
        self._post_aa_scene_fbo: Optional[int] = None
        self._post_aa_scene_texture: Optional[int] = None
        self._post_aa_history_fbo: Optional[int] = None
        self._post_aa_history_texture: Optional[int] = None
        self._post_aa_history_valid: bool = False
        self._post_aa_scene_size: Tuple[int, int] = (0, 0)
        self.viewport_background_color_mode: str = "none"
        self.viewport_background_enabled: bool = True
        self.viewport_background_image_enabled: bool = False
        self.viewport_background_keep_aspect: bool = True
        self.viewport_background_zoom_fill: bool = False
        self.viewport_background_flip_h: bool = False
        self.viewport_background_flip_v: bool = False
        self.viewport_background_image_path: str = ""
        self._viewport_background_texture_id: Optional[int] = None
        self._viewport_background_image: Optional["Image.Image"] = None
        self._viewport_background_is_svg: bool = False
        self._viewport_background_svg_raster_size: Tuple[int, int] = (0, 0)
        self._default_viewport_background_texture_id: Optional[int] = None
        self._default_viewport_background_image_path = self._resolve_default_viewport_background_asset_path()
        self._default_viewport_background_is_svg: bool = self._default_viewport_background_image_path.lower().endswith(".svg")
        self._default_viewport_background_image = self._load_viewport_background_image(
            self._default_viewport_background_image_path
        )
        if self._default_viewport_background_image is not None:
            self._default_viewport_background_svg_raster_size = self._default_viewport_background_image.size
        else:
            self._default_viewport_background_svg_raster_size = (0, 0)
        self.viewport_background_parallax_enabled: bool = True
        self.viewport_background_parallax_zoom_sensitivity: float = 0.5
        self.viewport_background_parallax_pan_sensitivity: float = 0.5
        self.viewport_background_parallax_origin_x: float = 0.0
        self.viewport_background_parallax_origin_y: float = 0.0
        self._viewport_background_parallax_origin_initialized: bool = False
        # Runtime SVG rerasterization can stall UI while zooming; keep disabled by default.
        self.viewport_background_svg_dynamic_quality: bool = False
        self._gl_max_texture_size: int = 8192
        self.show_bones: bool = False
        
        # Camera controls
        self.camera_x: float = 0.0
        self.camera_y: float = 0.0
        self.dragging_camera: bool = False
        self.last_mouse_x: int = 0
        self.last_mouse_y: int = 0
        self.interaction_tool: str = "cursor"
        self._tool_cursor_default = Qt.CursorShape.ArrowCursor
        self._tool_cursor_zoom = Qt.CursorShape.CrossCursor
        self._zoom_scrub_active: bool = False
        self._zoom_scrub_start_x: float = 0.0
        self._zoom_scrub_start_scale: float = 1.0
        self._zoom_scrub_anchor_world: Optional[Tuple[float, float]] = None
        self._zoom_scrub_anchor_screen: Optional[Tuple[float, float]] = None
        
        # Sprite dragging / selection
        self.dragging_sprite: bool = False
        self.selected_layer_id: Optional[int] = None  # Primary selection
        self.selected_layer_ids: Set[int] = set()
        self.selection_group_lock: bool = False
        self.dragged_layer_id: Optional[int] = None
        self.layer_offsets: Dict[int, Tuple[float, float]] = {}
        self.layer_rotations: Dict[int, float] = {}
        self.layer_scale_offsets: Dict[int, Tuple[float, float]] = {}
        self.drag_translation_multiplier: float = 1.0
        self.drag_rotation_multiplier: float = 1.0
        self.rotation_gizmo_enabled: bool = False
        self.rotation_overlay_radius: float = 120.0
        self.rotation_dragging: bool = False
        self.rotation_drag_last_angle: float = 0.0
        self.rotation_drag_accum: float = 0.0
        self.rotation_initial_values: Dict[int, float] = {}
        self.scale_gizmo_enabled: bool = False
        self.scale_mode: str = "Uniform"
        self.scale_dragging: bool = False
        self.scale_drag_axis: str = "uniform"
        self.scale_drag_initials: Dict[int, Tuple[float, float]] = {}
        self.transform_overlay_enabled: bool = True
        self.rotation_snap_increment: float = 15.0
        self._rotation_snap_active: bool = False
        self._scale_uniform_active: bool = False
        self.scale_drag_start: float = 0.0
        self.scale_drag_center: Tuple[float, float] = (0.0, 0.0)
        self.scale_drag_start_vec: Tuple[float, float] = (1.0, 1.0)
        self._scale_handle_positions: Dict[str, Tuple[float, float]] = {}
        self._last_layer_world_states: Dict[int, Dict] = {}
        self.anchor_overlay_enabled: bool = False
        self.parent_overlay_enabled: bool = False
        self.attachment_debug_overlay_enabled: bool = False
        self.particle_debug_overlay_enabled: bool = True
        self.layer_anchor_overrides: Dict[int, Tuple[float, float]] = {}
        self.renderer.anchor_overrides = self.layer_anchor_overrides
        self._anchor_handle_positions: Dict[int, Tuple[float, float]] = {}
        self._parent_handle_positions: Dict[int, Tuple[float, float]] = {}
        self._anchor_hover_layer_id: Optional[int] = None
        self.anchor_dragging: bool = False
        self.anchor_drag_layer_id: Optional[int] = None
        self.parent_dragging: bool = False
        self.parent_drag_layer_id: Optional[int] = None
        self.anchor_drag_last_world: Tuple[float, float] = (0.0, 0.0)
        self.parent_drag_last_world: Tuple[float, float] = (0.0, 0.0)
        self.anchor_drag_precision: float = 0.25
        self._layer_order_map: Dict[int, int] = {}
        self._active_transform_ids: List[int] = []
        self._active_transform_snapshot: Optional[Dict] = None
        self._current_drag_targets: List[int] = []
        self.constraint_manager: Optional[ConstraintManager] = None
        self.joint_solver_enabled: bool = False
        self.joint_solver_iterations: int = 8
        self.joint_solver_strength: float = 1.0
        self.joint_solver_parented: bool = True
        self.propagate_user_transforms: bool = False
        self.joint_rest_lengths: Dict[int, float] = {}
        self.joint_rest_vectors: Dict[int, Tuple[float, float]] = {}
        self._attachment_debug_snapshots: Dict[int, Dict[str, Any]] = {}
        
        # Timing
        self.last_update_time: Optional[float] = None
        self._motion_blur_frame_dt: float = 1.0 / 60.0
        
        # Set OpenGL format
        fmt = QSurfaceFormat()
        fmt.setVersion(2, 1)
        # Request a compatibility profile so legacy OpenGL calls (glBegin/glEnd) work,
        # especially on macOS where CoreProfile contexts forbid fixed-function APIs.
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        fmt.setSamples(4)
        self.setFormat(fmt)
        
        # Timer for animation updates
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(16)  # ~60 FPS
        self._svg_background_quality_timer = QTimer(self)
        self._svg_background_quality_timer.setSingleShot(True)
        self._svg_background_quality_timer.setInterval(300)
        self._svg_background_quality_timer.timeout.connect(self._refresh_svg_background_quality)

        # Anchor logging is controlled by the main window preferences
        self.renderer.enable_logging = False
        
        # Enable mouse tracking
        self.setMouseTracking(True)
        
        # Enable keyboard focus
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    
    @property
    def position_scale(self) -> float:
        """Get position scale from renderer"""
        return self.renderer.position_scale
    
    @position_scale.setter
    def position_scale(self, value: float):
        """Set position scale in renderer"""
        self.renderer.position_scale = value
    
    def initializeGL(self):
        """Initialize OpenGL"""
        glEnable(GL_BLEND)
        # Use premultiplied alpha blending like the MSM game engine
        # The game uses glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        # This expects textures with premultiplied alpha (RGB * A)
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        try:
            self._gl_max_texture_size = int(glGetIntegerv(GL_MAX_TEXTURE_SIZE))
        except Exception:
            self._gl_max_texture_size = 8192
        
        # Load textures
        for atlas in self.texture_atlases:
            atlas.load_texture()
        for batch in self.tile_batches:
            if batch.atlas:
                batch.atlas.load_texture()
        self._upload_terrain_composite_texture()
        self._upload_particle_textures()
        self._upload_viewport_background_texture()
        self._upload_default_viewport_background_texture()
        self._clear_post_aa_resources()
        self._post_aa_program = 0
        self._post_aa_uniform_texture = -1
        self._post_aa_uniform_prev_texture = -1
        self._post_aa_uniform_texel_size = -1
        self._post_aa_uniform_strength = -1
        self._post_aa_uniform_mode = -1
        self._post_aa_uniform_aa_enabled = -1
        self._post_aa_uniform_bloom_enabled = -1
        self._post_aa_uniform_bloom_strength = -1
        self._post_aa_uniform_bloom_threshold = -1
        self._post_aa_uniform_bloom_radius = -1
        self._post_aa_uniform_vignette_enabled = -1
        self._post_aa_uniform_vignette_strength = -1
        self._post_aa_uniform_grain_enabled = -1
        self._post_aa_uniform_grain_strength = -1
        self._post_aa_uniform_ca_enabled = -1
        self._post_aa_uniform_ca_strength = -1
        self._post_aa_uniform_motion_blur_enabled = -1
        self._post_aa_uniform_motion_blur_strength = -1
        self._post_aa_uniform_time = -1
        
        self._apply_antialiasing_state()
    
    def resizeGL(self, w: int, h: int):
        """Handle resize"""
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        # Use Y-down coordinates like Pygame (origin at top-left)
        # This matches the JSON data format
        glOrtho(0, w, h, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
    
    def paintGL(self):
        """Render the animation"""
        self._apply_antialiasing_state()
        view_w, view_h = self._current_framebuffer_size()

        if (
            self._is_post_pass_required()
            and self._ensure_post_aa_program()
            and self._ensure_post_aa_resources(view_w, view_h)
            and self._post_aa_scene_fbo
            and self._post_aa_scene_texture
        ):
            default_fbo = int(self.defaultFramebufferObject())
            source_texture = int(self._post_aa_scene_texture)
            if self._should_use_subframe_motion_blur():
                source_texture = int(self._render_subframe_motion_blur(view_w, view_h))
            else:
                glBindFramebuffer(GL_FRAMEBUFFER, int(self._post_aa_scene_fbo))
                glViewport(0, 0, view_w, view_h)
                self._render_scene_contents(view_w, view_h)
            glBindFramebuffer(GL_FRAMEBUFFER, default_fbo)
            glViewport(0, 0, view_w, view_h)
            pass_ok = self._draw_post_aa_pass(
                source_texture=source_texture,
                width=view_w,
                height=view_h,
            )
            if pass_ok:
                return
            # Graceful fallback: if the post pass fails, draw scene directly.
            self._render_scene_contents(view_w, view_h)
            return

        self._render_scene_contents(view_w, view_h)

    def _current_framebuffer_size(self) -> Tuple[int, int]:
        """Return current framebuffer pixel size (handles HiDPI correctly)."""
        try:
            vp = glGetIntegerv(GL_VIEWPORT)
            if vp is not None and len(vp) >= 4:
                w = max(1, int(vp[2]))
                h = max(1, int(vp[3]))
                if w > 0 and h > 0:
                    return w, h
        except Exception:
            pass
        try:
            dpr = float(self.devicePixelRatioF())
        except Exception:
            dpr = 1.0
        if dpr <= 0.0:
            dpr = 1.0
        return max(1, int(round(float(self.width()) * dpr))), max(
            1, int(round(float(self.height()) * dpr))
        )

    def _should_use_subframe_motion_blur(self) -> bool:
        """Return whether AE-like subframe motion blur should be applied this frame."""
        if not self.post_motion_blur_enabled:
            return False
        if self.post_motion_blur_strength <= 1e-4:
            return False
        if not self.player.animation and not self.extra_render_entries:
            return False
        return True

    def _resolve_sample_time(self, base_time: float, delta: float, duration: float) -> float:
        t = float(base_time + delta)
        if duration > 1e-6:
            if self.player.loop:
                t = float(t % duration)
            else:
                t = float(max(0.0, min(duration, t)))
        return t

    def _draw_weighted_texture(
        self,
        source_texture: int,
        width: int,
        height: int,
        weight: float,
        *,
        additive: bool,
    ) -> None:
        """Draw source texture to bound target with weighted accumulation."""
        if not source_texture:
            return
        try:
            prev_program = glGetIntegerv(GL_CURRENT_PROGRAM)
        except Exception:
            prev_program = 0
        try:
            prev_active = glGetIntegerv(GL_ACTIVE_TEXTURE)
        except Exception:
            prev_active = GL_TEXTURE0
        try:
            glActiveTexture(GL_TEXTURE0)
            prev_tex = glGetIntegerv(GL_TEXTURE_BINDING_2D)
        except Exception:
            prev_tex = 0

        blend_enabled = bool(glIsEnabled(GL_BLEND))

        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, width, height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()

        glUseProgram(0)
        if additive:
            glEnable(GL_BLEND)
            glBlendFunc(GL_ONE, GL_ONE)
        else:
            glDisable(GL_BLEND)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, int(source_texture))
        w = max(0.0, min(1.0, float(weight)))
        glColor4f(w, w, w, w)
        # Keep orientation identical to post-pass resolve.
        glBegin(GL_QUADS)
        glTexCoord2f(0.0, 1.0)
        glVertex2f(0.0, 0.0)
        glTexCoord2f(1.0, 1.0)
        glVertex2f(float(width), 0.0)
        glTexCoord2f(1.0, 0.0)
        glVertex2f(float(width), float(height))
        glTexCoord2f(0.0, 0.0)
        glVertex2f(0.0, float(height))
        glEnd()
        glColor4f(1.0, 1.0, 1.0, 1.0)

        glUseProgram(int(prev_program or 0))
        glBindTexture(GL_TEXTURE_2D, int(prev_tex))
        glActiveTexture(int(prev_active))

        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)

        if blend_enabled:
            glEnable(GL_BLEND)
        else:
            glDisable(GL_BLEND)
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)

    def _render_subframe_motion_blur(self, view_w: int, view_h: int) -> int:
        """
        Render AE-like shutter accumulation into history texture and return it.
        Falls back to single scene texture if resources are missing.
        """
        if not (
            self._post_aa_scene_fbo
            and self._post_aa_scene_texture
            and self._post_aa_history_fbo
            and self._post_aa_history_texture
        ):
            return int(self._post_aa_scene_texture or 0)

        strength = max(0.0, min(1.0, float(self.post_motion_blur_strength)))
        frame_dt = max(1.0 / 240.0, min(1.0 / 20.0, float(getattr(self, "_motion_blur_frame_dt", 1.0 / 60.0))))
        shutter_span = frame_dt * strength
        sample_count = max(2, min(12, int(round(2.0 + strength * 10.0))))
        if shutter_span <= 1e-5 or sample_count <= 1:
            glBindFramebuffer(GL_FRAMEBUFFER, int(self._post_aa_scene_fbo))
            glViewport(0, 0, view_w, view_h)
            self._render_scene_contents(view_w, view_h)
            return int(self._post_aa_scene_texture)

        base_time = float(self.player.current_time)
        duration = float(self.player.duration or 0.0)
        step = shutter_span / float(sample_count)
        start = -0.5 * shutter_span + 0.5 * step
        weight = 1.0 / float(sample_count)

        glBindFramebuffer(GL_FRAMEBUFFER, int(self._post_aa_history_fbo))
        glViewport(0, 0, view_w, view_h)
        glClearColor(0.0, 0.0, 0.0, 0.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        for idx in range(sample_count):
            offset = start + float(idx) * step
            sample_time = self._resolve_sample_time(base_time, offset, duration)
            glBindFramebuffer(GL_FRAMEBUFFER, int(self._post_aa_scene_fbo))
            glViewport(0, 0, view_w, view_h)
            self._render_scene_contents(
                view_w,
                view_h,
                main_time=sample_time,
                extra_time_offset=(sample_time - base_time),
            )
            glBindFramebuffer(GL_FRAMEBUFFER, int(self._post_aa_history_fbo))
            glViewport(0, 0, view_w, view_h)
            self._draw_weighted_texture(
                int(self._post_aa_scene_texture),
                view_w,
                view_h,
                weight,
                additive=(idx > 0),
            )

        return int(self._post_aa_history_texture)

    def _render_scene_contents(
        self,
        view_w: int,
        view_h: int,
        *,
        main_time: Optional[float] = None,
        extra_time_offset: float = 0.0,
    ) -> None:
        """Render the current scene into the currently bound framebuffer."""
        if self.viewport_background_enabled:
            glClearColor(*self.background_color)
        else:
            glClearColor(*self._theme_background_rgba())
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if self._viewport_background_image is not None and self._viewport_background_texture_id is None:
            self._upload_viewport_background_texture()
        if (
            self._default_viewport_background_image is not None
            and self._default_viewport_background_texture_id is None
        ):
            self._upload_default_viewport_background_texture()
        self._render_viewport_background_image(view_width=view_w, view_height=view_h)
        if not self.player.animation and not self.extra_render_entries:
            return

        if self.particle_entries and any(
            entry.texture_id is None and entry.texture_image is not None
            for entry in self.particle_entries
        ):
            self._upload_particle_textures()

        glLoadIdentity()

        # Apply camera offset
        glTranslatef(self.camera_x, self.camera_y, 0)
        glScalef(self.render_scale, self.render_scale, 1.0)

        if self.player.animation:
            fast_preview = bool(self.fast_preview_enabled)
            current_time = float(self.player.current_time if main_time is None else main_time)
            self._render_animation_entry(
                self.player.animation,
                current_time,
                self.texture_atlases,
                duration=float(self.player.duration or 0.0),
                offset_x=0.0,
                offset_y=0.0,
                render_overlays=not fast_preview,
                render_tiles=not fast_preview,
                apply_constraints=not fast_preview,
                render_attachments=not fast_preview,
                render_particles=not fast_preview,
                world_states=None,
            )

        if self.extra_render_entries:
            fast_preview = bool(self.fast_preview_enabled)
            for entry in self.extra_render_entries:
                animation = entry.get("animation")
                if not animation:
                    continue
                entry_time = float(entry.get("time", 0.0)) + float(extra_time_offset)
                entry_duration = float(entry.get("duration", 0.0))
                if entry_duration > 1e-6:
                    if self.player.loop:
                        entry_time = float(entry_time % entry_duration)
                    else:
                        entry_time = float(max(0.0, min(entry_duration, entry_time)))
                self._render_animation_entry(
                    animation,
                    entry_time,
                    entry.get("atlases", []) or [],
                    duration=entry_duration,
                    offset_x=float(entry.get("offset_x", 0.0)),
                    offset_y=float(entry.get("offset_y", 0.0)),
                    render_overlays=False,
                    render_tiles=False,
                    apply_constraints=not fast_preview,
                    render_attachments=not fast_preview,
                    render_particles=False,
                    world_states=entry.get("world_states"),
                )

    def _compile_post_aa_program(self) -> int:
        """Compile the optional post-process anti-aliasing shader."""
        try:
            vert = glCreateShader(GL_VERTEX_SHADER)
            glShaderSource(vert, _POST_AA_VERTEX_SHADER)
            glCompileShader(vert)
            if glGetShaderiv(vert, GL_COMPILE_STATUS) != GL_TRUE:
                info = glGetShaderInfoLog(vert).decode("utf-8", "ignore")
                print(f"[PostAA] Vertex shader compile failed: {info}")
                glDeleteShader(vert)
                return 0

            frag = glCreateShader(GL_FRAGMENT_SHADER)
            glShaderSource(frag, _POST_AA_FRAGMENT_SHADER)
            glCompileShader(frag)
            if glGetShaderiv(frag, GL_COMPILE_STATUS) != GL_TRUE:
                info = glGetShaderInfoLog(frag).decode("utf-8", "ignore")
                print(f"[PostAA] Fragment shader compile failed: {info}")
                glDeleteShader(vert)
                glDeleteShader(frag)
                return 0

            program = glCreateProgram()
            glAttachShader(program, vert)
            glAttachShader(program, frag)
            glLinkProgram(program)
            if glGetProgramiv(program, GL_LINK_STATUS) != GL_TRUE:
                info = glGetProgramInfoLog(program).decode("utf-8", "ignore")
                print(f"[PostAA] Program link failed: {info}")
                glDeleteShader(vert)
                glDeleteShader(frag)
                glDeleteProgram(program)
                return 0

            glDeleteShader(vert)
            glDeleteShader(frag)
            self._post_aa_uniform_texture = glGetUniformLocation(program, "u_texture")
            self._post_aa_uniform_prev_texture = glGetUniformLocation(program, "u_prevTexture")
            self._post_aa_uniform_texel_size = glGetUniformLocation(program, "u_texelSize")
            self._post_aa_uniform_strength = glGetUniformLocation(program, "u_strength")
            self._post_aa_uniform_mode = glGetUniformLocation(program, "u_aaMode")
            self._post_aa_uniform_aa_enabled = glGetUniformLocation(program, "u_aaEnabled")
            self._post_aa_uniform_bloom_enabled = glGetUniformLocation(program, "u_bloomEnabled")
            self._post_aa_uniform_bloom_strength = glGetUniformLocation(program, "u_bloomStrength")
            self._post_aa_uniform_bloom_threshold = glGetUniformLocation(program, "u_bloomThreshold")
            self._post_aa_uniform_bloom_radius = glGetUniformLocation(program, "u_bloomRadius")
            self._post_aa_uniform_vignette_enabled = glGetUniformLocation(program, "u_vignetteEnabled")
            self._post_aa_uniform_vignette_strength = glGetUniformLocation(program, "u_vignetteStrength")
            self._post_aa_uniform_grain_enabled = glGetUniformLocation(program, "u_grainEnabled")
            self._post_aa_uniform_grain_strength = glGetUniformLocation(program, "u_grainStrength")
            self._post_aa_uniform_ca_enabled = glGetUniformLocation(program, "u_caEnabled")
            self._post_aa_uniform_ca_strength = glGetUniformLocation(program, "u_caStrength")
            self._post_aa_uniform_motion_blur_enabled = glGetUniformLocation(program, "u_motionBlurEnabled")
            self._post_aa_uniform_motion_blur_strength = glGetUniformLocation(program, "u_motionBlurStrength")
            self._post_aa_uniform_time = glGetUniformLocation(program, "u_time")
            return int(program)
        except Exception as exc:
            print(f"[PostAA] Shader compile exception: {exc}")
            return 0

    def _ensure_post_aa_program(self) -> bool:
        if self._post_aa_program:
            return True
        self._post_aa_program = self._compile_post_aa_program()
        return bool(self._post_aa_program)

    def _clear_post_aa_resources(self) -> None:
        """Delete post-process AA GL resources."""
        if self._post_aa_scene_fbo:
            try:
                glDeleteFramebuffers(1, [int(self._post_aa_scene_fbo)])
            except Exception:
                pass
            self._post_aa_scene_fbo = None
        if self._post_aa_history_fbo:
            try:
                glDeleteFramebuffers(1, [int(self._post_aa_history_fbo)])
            except Exception:
                pass
            self._post_aa_history_fbo = None
        if self._post_aa_scene_texture:
            try:
                glDeleteTextures(1, [int(self._post_aa_scene_texture)])
            except Exception:
                pass
            self._post_aa_scene_texture = None
        if self._post_aa_history_texture:
            try:
                glDeleteTextures(1, [int(self._post_aa_history_texture)])
            except Exception:
                pass
            self._post_aa_history_texture = None
        self._post_aa_history_valid = False
        self._post_aa_scene_size = (0, 0)

    def _ensure_post_aa_resources(self, width: int, height: int) -> bool:
        """Ensure the offscreen scene target exists for post-process AA."""
        target_w = max(1, int(width))
        target_h = max(1, int(height))
        if (
            self._post_aa_scene_fbo
            and self._post_aa_scene_texture
            and self._post_aa_history_fbo
            and self._post_aa_history_texture
            and self._post_aa_scene_size == (target_w, target_h)
        ):
            return True

        self._clear_post_aa_resources()
        try:
            prev_fbo = int(glGetIntegerv(GL_FRAMEBUFFER_BINDING))
            prev_tex = int(glGetIntegerv(GL_TEXTURE_BINDING_2D))
            tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(
                GL_TEXTURE_2D,
                0,
                GL_RGBA,
                target_w,
                target_h,
                0,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                None,
            )
            history_tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, history_tex)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(
                GL_TEXTURE_2D,
                0,
                GL_RGBA,
                target_w,
                target_h,
                0,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                None,
            )
            history_fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, history_fbo)
            glFramebufferTexture2D(
                GL_FRAMEBUFFER,
                GL_COLOR_ATTACHMENT0,
                GL_TEXTURE_2D,
                history_tex,
                0,
            )
            if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
                print("[PostAA] History framebuffer is not complete.")
                glDeleteFramebuffers(1, [history_fbo])
                glDeleteTextures(1, [history_tex])
                glDeleteTextures(1, [tex])
                glBindFramebuffer(GL_FRAMEBUFFER, prev_fbo)
                glBindTexture(GL_TEXTURE_2D, prev_tex)
                return False
            fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            glFramebufferTexture2D(
                GL_FRAMEBUFFER,
                GL_COLOR_ATTACHMENT0,
                GL_TEXTURE_2D,
                tex,
                0,
            )
            if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
                print("[PostAA] Offscreen framebuffer is not complete.")
                glDeleteFramebuffers(1, [fbo])
                glDeleteFramebuffers(1, [history_fbo])
                glDeleteTextures(1, [tex])
                glDeleteTextures(1, [history_tex])
                glBindFramebuffer(GL_FRAMEBUFFER, prev_fbo)
                glBindTexture(GL_TEXTURE_2D, prev_tex)
                return False
            glBindFramebuffer(GL_FRAMEBUFFER, prev_fbo)
            glBindTexture(GL_TEXTURE_2D, prev_tex)
            self._post_aa_scene_fbo = int(fbo)
            self._post_aa_scene_texture = int(tex)
            self._post_aa_history_fbo = int(history_fbo)
            self._post_aa_history_texture = int(history_tex)
            self._post_aa_history_valid = False
            self._post_aa_scene_size = (target_w, target_h)
            return True
        except Exception as exc:
            print(f"[PostAA] Failed to create resources: {exc}")
            self._clear_post_aa_resources()
            return False

    def _draw_post_aa_pass(self, source_texture: int, width: int, height: int) -> bool:
        """Draw the post-process AA pass from source texture to the bound framebuffer."""
        if not source_texture:
            return False
        if not self._ensure_post_aa_program():
            return False
        program = int(self._post_aa_program or 0)
        if not program:
            return False

        try:
            prev_program = glGetIntegerv(GL_CURRENT_PROGRAM)
        except Exception:
            prev_program = 0
        try:
            prev_active = glGetIntegerv(GL_ACTIVE_TEXTURE)
        except Exception:
            prev_active = GL_TEXTURE0
        try:
            glActiveTexture(GL_TEXTURE0)
            prev_tex0 = glGetIntegerv(GL_TEXTURE_BINDING_2D)
        except Exception:
            prev_tex0 = 0
        try:
            glActiveTexture(GL_TEXTURE1)
            prev_tex1 = glGetIntegerv(GL_TEXTURE_BINDING_2D)
        except Exception:
            prev_tex1 = 0
        blend_enabled = bool(glIsEnabled(GL_BLEND))
        if blend_enabled:
            glDisable(GL_BLEND)

        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, width, height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()

        glUseProgram(program)
        aa_enabled = bool(self.post_aa_enabled)
        motion_blur_enabled = False
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, int(source_texture))
        if self._post_aa_uniform_texture >= 0:
            glUniform1i(self._post_aa_uniform_texture, 0)
        if self._post_aa_uniform_prev_texture >= 0:
            glUniform1i(self._post_aa_uniform_prev_texture, 1)
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(
            GL_TEXTURE_2D,
            int(self._post_aa_history_texture if motion_blur_enabled else source_texture),
        )
        glActiveTexture(GL_TEXTURE0)
        if self._post_aa_uniform_texel_size >= 0:
            glUniform2f(
                self._post_aa_uniform_texel_size,
                1.0 / max(1.0, float(width)),
                1.0 / max(1.0, float(height)),
            )
        if self._post_aa_uniform_strength >= 0:
            glUniform1f(
                self._post_aa_uniform_strength,
                max(0.0, min(1.0, float(self.post_aa_strength if aa_enabled else 0.0))),
            )
        if self._post_aa_uniform_mode >= 0:
            glUniform1i(self._post_aa_uniform_mode, 1 if str(self.post_aa_mode).lower() == "smaa" else 0)
        if self._post_aa_uniform_aa_enabled >= 0:
            glUniform1i(self._post_aa_uniform_aa_enabled, 1 if aa_enabled else 0)
        if self._post_aa_uniform_bloom_enabled >= 0:
            glUniform1i(self._post_aa_uniform_bloom_enabled, 1 if (aa_enabled and self.post_bloom_enabled) else 0)
        if self._post_aa_uniform_bloom_strength >= 0:
            glUniform1f(self._post_aa_uniform_bloom_strength, max(0.0, min(2.0, float(self.post_bloom_strength))))
        if self._post_aa_uniform_bloom_threshold >= 0:
            glUniform1f(self._post_aa_uniform_bloom_threshold, max(0.0, min(2.0, float(self.post_bloom_threshold))))
        if self._post_aa_uniform_bloom_radius >= 0:
            glUniform1f(self._post_aa_uniform_bloom_radius, max(0.1, min(8.0, float(self.post_bloom_radius))))
        if self._post_aa_uniform_vignette_enabled >= 0:
            glUniform1i(
                self._post_aa_uniform_vignette_enabled,
                1 if (aa_enabled and self.post_vignette_enabled) else 0,
            )
        if self._post_aa_uniform_vignette_strength >= 0:
            glUniform1f(self._post_aa_uniform_vignette_strength, max(0.0, min(1.0, float(self.post_vignette_strength))))
        if self._post_aa_uniform_grain_enabled >= 0:
            glUniform1i(self._post_aa_uniform_grain_enabled, 1 if (aa_enabled and self.post_grain_enabled) else 0)
        if self._post_aa_uniform_grain_strength >= 0:
            glUniform1f(self._post_aa_uniform_grain_strength, max(0.0, min(1.0, float(self.post_grain_strength))))
        if self._post_aa_uniform_ca_enabled >= 0:
            glUniform1i(self._post_aa_uniform_ca_enabled, 1 if (aa_enabled and self.post_ca_enabled) else 0)
        if self._post_aa_uniform_ca_strength >= 0:
            glUniform1f(self._post_aa_uniform_ca_strength, max(0.0, min(1.0, float(self.post_ca_strength))))
        if self._post_aa_uniform_motion_blur_enabled >= 0:
            glUniform1i(self._post_aa_uniform_motion_blur_enabled, 1 if motion_blur_enabled else 0)
        if self._post_aa_uniform_motion_blur_strength >= 0:
            glUniform1f(
                self._post_aa_uniform_motion_blur_strength,
                0.0,
            )
        if self._post_aa_uniform_time >= 0:
            glUniform1f(self._post_aa_uniform_time, float(self.player.current_time))

        glColor4f(1.0, 1.0, 1.0, 1.0)
        # FBO textures are bottom-origin; viewport space here is y-down.
        # Flip V so the resolved frame keeps the same orientation as the scene.
        glBegin(GL_QUADS)
        glTexCoord2f(0.0, 1.0)
        glVertex2f(0.0, 0.0)
        glTexCoord2f(1.0, 1.0)
        glVertex2f(float(width), 0.0)
        glTexCoord2f(1.0, 0.0)
        glVertex2f(float(width), float(height))
        glTexCoord2f(0.0, 0.0)
        glVertex2f(0.0, float(height))
        glEnd()

        glUseProgram(int(prev_program or 0))
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(GL_TEXTURE_2D, int(prev_tex1))
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, int(prev_tex0))
        glActiveTexture(int(prev_active))

        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)

        if blend_enabled:
            glEnable(GL_BLEND)
        else:
            glDisable(GL_BLEND)
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        return True

    def _capture_post_history(self, width: int, height: int) -> None:
        """Capture the currently bound framebuffer into post history texture."""
        if not self._post_aa_history_texture:
            self._post_aa_history_valid = False
            return
        target_w = max(1, int(width))
        target_h = max(1, int(height))
        try:
            prev_active = glGetIntegerv(GL_ACTIVE_TEXTURE)
        except Exception:
            prev_active = GL_TEXTURE0
        try:
            glActiveTexture(GL_TEXTURE0)
            prev_tex = glGetIntegerv(GL_TEXTURE_BINDING_2D)
        except Exception:
            prev_tex = 0
        try:
            glBindTexture(GL_TEXTURE_2D, int(self._post_aa_history_texture))
            glCopyTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 0, 0, target_w, target_h)
            self._post_aa_history_valid = True
        except Exception:
            self._post_aa_history_valid = False
        finally:
            glBindTexture(GL_TEXTURE_2D, int(prev_tex))
            glActiveTexture(int(prev_active))

    def _is_post_pass_required(self) -> bool:
        return bool(self.post_aa_enabled or self.post_motion_blur_enabled)

    def is_post_pass_enabled(self) -> bool:
        return self._is_post_pass_required()

    def resolve_post_aa_texture(self, source_texture: int, width: int, height: int) -> Tuple[int, int]:
        """
        Resolve a source color texture through the post AA pass.

        Returns:
            (fbo_id, texture_id) for the resolved result, or (0, source_texture)
            if post AA is disabled/unavailable.
        """
        if not self._is_post_pass_required():
            return 0, int(source_texture or 0)
        if not source_texture:
            return 0, 0
        if not self._ensure_post_aa_program():
            return 0, int(source_texture)
        if not self._ensure_post_aa_resources(width, height):
            return 0, int(source_texture)
        target_fbo = int(self._post_aa_scene_fbo or 0)
        target_texture = int(self._post_aa_scene_texture or 0)
        if not target_fbo or not target_texture or target_texture == int(source_texture):
            return 0, int(source_texture)

        prev_fbo = int(glGetIntegerv(GL_FRAMEBUFFER_BINDING))
        prev_viewport = glGetIntegerv(GL_VIEWPORT)
        try:
            glBindFramebuffer(GL_FRAMEBUFFER, target_fbo)
            glViewport(0, 0, int(width), int(height))
            glClearColor(0.0, 0.0, 0.0, 0.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            ok = self._draw_post_aa_pass(int(source_texture), int(width), int(height))
            if not ok:
                return 0, int(source_texture)
            return target_fbo, target_texture
        finally:
            glBindFramebuffer(GL_FRAMEBUFFER, prev_fbo)
            glViewport(*prev_viewport)

    def _theme_background_rgba(self) -> Tuple[float, float, float, float]:
        """Return the widget's theme background color as normalized RGBA."""
        try:
            palette = self.palette()
            color = palette.color(self.backgroundRole())
            if not color.isValid():
                color = palette.color(QPalette.ColorRole.Window)
            if color.isValid():
                return (float(color.redF()), float(color.greenF()), float(color.blueF()), 1.0)
        except Exception:
            pass
        return (0.2, 0.2, 0.2, 1.0)

    def set_extra_render_entries(self, entries: Optional[List[Dict[str, Any]]]) -> None:
        self.extra_render_entries = list(entries or [])
        self.update()

    def invalidate_animation_cache(self) -> None:
        """Drop cached layer maps/order when the active animation changes."""
        self._animation_layer_cache.clear()

    def set_fast_preview_enabled(self, enabled: bool) -> None:
        self.fast_preview_enabled = bool(enabled)
        try:
            self.renderer.set_fast_preview_enabled(self.fast_preview_enabled)
        except Exception:
            pass
        self.update()

    def set_particle_entries(
        self,
        entries: Optional[List[ParticleRenderEntry]],
        *,
        flip_y: bool = False
    ) -> None:
        """Install DOF particle rendering data."""
        ctx = self.context()
        if ctx and ctx.isValid():
            self.makeCurrent()
            try:
                self._particle_texture_failures.clear()
                self._particle_emission_cache.clear()
                self._particle_sample_cache.clear()
                self._clear_particle_textures()
                self.particle_entries = list(entries or [])
                self._particle_flip_y = bool(flip_y)
                self._upload_particle_textures()
            finally:
                self.doneCurrent()
        else:
            self._particle_texture_failures.clear()
            self._particle_emission_cache.clear()
            self._particle_sample_cache.clear()
            self._clear_particle_textures()
            self.particle_entries = list(entries or [])
            self._particle_flip_y = bool(flip_y)
            self._reset_particle_runtime()
        self.update()

    def _reset_particle_runtime(self) -> None:
        self._particle_runtime_signature = None
        self._particle_runtime_time = 0.0
        self._particle_runtime_states = {}

    def set_particle_world_space_override(self, enabled: bool) -> None:
        """Force DOF particles to simulate in world space (override bundle metadata)."""
        self.particle_force_world_space = bool(enabled)
        self._reset_particle_runtime()
        self.update()

    def set_particle_viewport_cap(self, count: int) -> None:
        """Set the maximum active DOF particles drawn in the viewport."""
        try:
            value = int(count)
        except (TypeError, ValueError):
            value = 1000
        self.particle_viewport_cap = max(0, min(1000000, value))
        self.update()

    def set_particle_distance_sensitivity(self, value: float) -> None:
        """Set the DOF rate-over-distance sensitivity multiplier."""
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            numeric_value = 0.5
        self.particle_distance_sensitivity = max(0.0, numeric_value)
        self._reset_particle_runtime()
        self.update()

    def set_background_color_rgba(self, rgba: Tuple[int, int, int, int]) -> None:
        """Set viewport clear color from 8-bit RGBA."""
        r = max(0, min(255, int(rgba[0])))
        g = max(0, min(255, int(rgba[1])))
        b = max(0, min(255, int(rgba[2])))
        a = max(0, min(255, int(rgba[3])))
        self.background_color = (r / 255.0, g / 255.0, b / 255.0, a / 255.0)
        self.update()

    @staticmethod
    def _normalize_viewport_background_color_mode(mode: Optional[str]) -> str:
        allowed = {
            "none",
            "replace",
            "overlay_normal",
            "overlay_multiply",
            "overlay_screen",
            "overlay_add",
            "overlay_hue",
            "overlay_color",
        }
        normalized = str(mode or "none").strip().lower()
        return normalized if normalized in allowed else "none"

    def set_viewport_background_color_mode(self, mode: str) -> None:
        self.viewport_background_color_mode = self._normalize_viewport_background_color_mode(mode)
        self.update()

    def set_viewport_background_enabled(self, enabled: bool) -> None:
        """Enable/disable viewport background rendering entirely."""
        self.viewport_background_enabled = bool(enabled)
        self.update()

    def set_viewport_background_keep_aspect(self, enabled: bool) -> None:
        """Toggle whether the image preserves aspect ratio (no stretch)."""
        self.viewport_background_keep_aspect = bool(enabled)
        self._schedule_svg_background_quality_refresh()
        self.update()

    def set_viewport_background_zoom_fill(self, enabled: bool) -> None:
        """Toggle cover mode for aspect-preserving viewport background rendering."""
        self.viewport_background_zoom_fill = bool(enabled)
        self._schedule_svg_background_quality_refresh()
        self.update()

    def set_viewport_background_parallax_enabled(self, enabled: bool) -> None:
        self.viewport_background_parallax_enabled = bool(enabled)
        if self.viewport_background_parallax_enabled:
            self.reset_viewport_background_parallax_origin()
        self._schedule_svg_background_quality_refresh()
        self.update()

    def set_viewport_background_parallax_zoom_sensitivity(self, value: float) -> None:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            numeric_value = 0.5
        self.viewport_background_parallax_zoom_sensitivity = max(0.0, min(2.0, numeric_value))
        self._schedule_svg_background_quality_refresh()
        self.update()

    def set_viewport_background_parallax_pan_sensitivity(self, value: float) -> None:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            numeric_value = 0.5
        self.viewport_background_parallax_pan_sensitivity = max(0.0, min(2.0, numeric_value))
        self._schedule_svg_background_quality_refresh()
        self.update()

    def reset_viewport_background_parallax_origin(
        self,
        origin_x: Optional[float] = None,
        origin_y: Optional[float] = None,
    ) -> None:
        """Re-center background parallax to the current (or provided) camera position."""
        try:
            base_x = float(self.camera_x if origin_x is None else origin_x)
        except (TypeError, ValueError):
            base_x = 0.0
        try:
            base_y = float(self.camera_y if origin_y is None else origin_y)
        except (TypeError, ValueError):
            base_y = 0.0
        self.viewport_background_parallax_origin_x = base_x
        self.viewport_background_parallax_origin_y = base_y
        self._viewport_background_parallax_origin_initialized = True
        self.update()

    def set_viewport_background_flips(self, flip_h: bool, flip_v: bool) -> None:
        """Set background image horizontal/vertical flip toggles."""
        self.viewport_background_flip_h = bool(flip_h)
        self.viewport_background_flip_v = bool(flip_v)
        self.update()

    def set_viewport_background_image_enabled(self, enabled: bool) -> None:
        self.viewport_background_image_enabled = bool(enabled)
        self.update()

    def set_viewport_background_image_path(self, image_path: Optional[str]) -> None:
        normalized_path = (image_path or "").strip()
        self.viewport_background_image_path = normalized_path
        self._viewport_background_is_svg = normalized_path.lower().endswith(".svg")
        loaded_image = self._load_viewport_background_image(normalized_path)
        self._viewport_background_image = loaded_image
        if loaded_image is not None:
            self._viewport_background_svg_raster_size = loaded_image.size
        else:
            self._viewport_background_svg_raster_size = (0, 0)
        ctx = self.context()
        if ctx and ctx.isValid():
            self.makeCurrent()
            try:
                self._upload_viewport_background_texture()
            finally:
                self.doneCurrent()
        else:
            self._viewport_background_texture_id = None
        self._schedule_svg_background_quality_refresh()
        self.update()

    def _schedule_svg_background_quality_refresh(self) -> None:
        if not self.viewport_background_svg_dynamic_quality:
            return
        has_svg = bool(self._default_viewport_background_is_svg or self._viewport_background_is_svg)
        if not has_svg:
            return
        self._svg_background_quality_timer.start()

    def _refresh_svg_background_quality(self) -> None:
        if not (self._default_viewport_background_is_svg or self._viewport_background_is_svg):
            return
        ctx = self.context()
        if not (ctx and ctx.isValid()):
            return
        view_w = max(1, int(self.width()))
        view_h = max(1, int(self.height()))
        self.makeCurrent()
        try:
            if self._default_viewport_background_texture_id and self._default_viewport_background_image is not None:
                try:
                    img_w, img_h = self._default_viewport_background_image.size
                except Exception:
                    img_w, img_h = 0, 0
                self._maybe_refresh_svg_background_texture(
                    active_texture_id=self._default_viewport_background_texture_id,
                    view_w=view_w,
                    view_h=view_h,
                    img_w=img_w,
                    img_h=img_h,
                )
            if self._viewport_background_texture_id and self._viewport_background_image is not None:
                try:
                    img_w, img_h = self._viewport_background_image.size
                except Exception:
                    img_w, img_h = 0, 0
                self._maybe_refresh_svg_background_texture(
                    active_texture_id=self._viewport_background_texture_id,
                    view_w=view_w,
                    view_h=view_h,
                    img_w=img_w,
                    img_h=img_h,
                )
        finally:
            self.doneCurrent()
        self.update()

    def _resolve_default_viewport_background_asset_path(self) -> str:
        default_path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "assets",
            "Viewport_Background_Default.svg",
        ))
        return default_path if os.path.isfile(default_path) else ""

    def _load_viewport_background_image(self, image_path: str) -> Optional["Image.Image"]:
        if not image_path:
            return None
        if Image is not None:
            try:
                return Image.open(image_path).convert("RGBA")
            except Exception:
                pass
        if image_path.lower().endswith(".svg"):
            return self._load_svg_background_as_pil(image_path)
        return None

    def _load_svg_background_as_pil(
        self,
        image_path: str,
        target_size: Optional[Tuple[int, int]] = None,
    ) -> Optional["Image.Image"]:
        if QSvgRenderer is None or Image is None:
            return None
        renderer = QSvgRenderer(image_path)
        if not renderer.isValid():
            return None
        if target_size is not None:
            width = max(1, int(target_size[0]))
            height = max(1, int(target_size[1]))
        else:
            size = renderer.defaultSize()
            width = max(1, int(size.width())) if size.isValid() else 0
            height = max(1, int(size.height())) if size.isValid() else 0
            if width <= 0 or height <= 0:
                width, height = 1920, 1080
            max_dim = max(width, height)
            target_floor = 6144.0
            if max_dim < target_floor:
                scale = target_floor / float(max_dim)
                width = max(1, int(round(width * scale)))
                height = max(1, int(round(height * scale)))
            max_dim = max(width, height)
            if max_dim > 8192:
                scale = 8192.0 / float(max_dim)
                width = max(1, int(round(width * scale)))
                height = max(1, int(round(height * scale)))

        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(0)
        painter = QPainter(image)
        try:
            renderer.render(painter)
        finally:
            painter.end()
        return self._qimage_to_pil_rgba(image)

    def _qimage_to_pil_rgba(self, image: QImage) -> Optional["Image.Image"]:
        if Image is None or image.isNull():
            return None
        qimage = image.convertToFormat(QImage.Format.Format_RGBA8888)
        width = int(qimage.width())
        height = int(qimage.height())
        if width <= 0 or height <= 0:
            return None
        ptr = qimage.bits()
        ptr.setsize(width * height * 4)
        raw = bytes(ptr)
        return Image.frombytes("RGBA", (width, height), raw)

    def _upload_viewport_background_texture(self) -> None:
        self._clear_viewport_background_texture()
        if self._viewport_background_image is None:
            return
        texture_id = self._upload_texture_from_image(self._viewport_background_image)
        if texture_id:
            self._viewport_background_texture_id = texture_id

    def _upload_default_viewport_background_texture(self) -> None:
        self._clear_default_viewport_background_texture()
        if self._default_viewport_background_image is None:
            return
        texture_id = self._upload_texture_from_image(self._default_viewport_background_image)
        if texture_id:
            self._default_viewport_background_texture_id = texture_id

    def _clear_viewport_background_texture(self) -> None:
        if not self._viewport_background_texture_id:
            return
        try:
            glDeleteTextures(1, [self._viewport_background_texture_id])
        except Exception:
            pass
        self._viewport_background_texture_id = None

    def _clear_default_viewport_background_texture(self) -> None:
        if not self._default_viewport_background_texture_id:
            return
        try:
            glDeleteTextures(1, [self._default_viewport_background_texture_id])
        except Exception:
            pass
        self._default_viewport_background_texture_id = None

    def _render_viewport_background_image(
        self,
        *,
        view_width: Optional[int] = None,
        view_height: Optional[int] = None,
        camera_x: Optional[float] = None,
        camera_y: Optional[float] = None,
        render_scale: Optional[float] = None,
    ) -> None:
        mode = self._normalize_viewport_background_color_mode(
            getattr(self, "viewport_background_color_mode", "none")
        )
        if mode == "replace":
            self._draw_viewport_background_color_quad(
                0.0,
                0.0,
                float(max(1, int(view_width if view_width is not None else self.width()))),
                float(max(1, int(view_height if view_height is not None else self.height()))),
                mode,
            )
            return

        active_texture_id: Optional[int] = None
        if self.viewport_background_image_enabled and self._viewport_background_texture_id:
            active_texture_id = self._viewport_background_texture_id
        elif self._default_viewport_background_texture_id:
            active_texture_id = self._default_viewport_background_texture_id
        elif self._viewport_background_texture_id:
            active_texture_id = self._viewport_background_texture_id
        view_w = max(1, int(view_width if view_width is not None else self.width()))
        view_h = max(1, int(view_height if view_height is not None else self.height()))
        if active_texture_id is None:
            if mode.startswith("overlay_"):
                self._draw_viewport_background_color_quad(
                    0.0,
                    0.0,
                    float(view_w),
                    float(view_h),
                    mode,
                )
            return
        img_w = 0
        img_h = 0
        active_image = self._viewport_background_image if active_texture_id == self._viewport_background_texture_id else self._default_viewport_background_image
        if active_image is not None:
            try:
                img_w, img_h = active_image.size
            except Exception:
                img_w, img_h = 0, 0

        if self.viewport_background_keep_aspect and img_w > 0 and img_h > 0:
            if self.viewport_background_zoom_fill:
                scale = max(float(view_w) / float(img_w), float(view_h) / float(img_h))
            else:
                scale = min(float(view_w) / float(img_w), float(view_h) / float(img_h))
            draw_w = max(1.0, float(img_w) * scale)
            draw_h = max(1.0, float(img_h) * scale)
            x0 = (float(view_w) - draw_w) * 0.5
            y0 = (float(view_h) - draw_h) * 0.5
            x1 = x0 + draw_w
            y1 = y0 + draw_h
        else:
            x0 = 0.0
            y0 = 0.0
            x1 = float(view_w)
            y1 = float(view_h)

        zoom_strength = (
            float(self.viewport_background_parallax_zoom_sensitivity)
            if self.viewport_background_parallax_enabled
            else 0.0
        )
        pan_strength = (
            float(self.viewport_background_parallax_pan_sensitivity)
            if self.viewport_background_parallax_enabled
            else 0.0
        )
        cam_x = float(self.camera_x if camera_x is None else camera_x)
        cam_y = float(self.camera_y if camera_y is None else camera_y)
        if not self._viewport_background_parallax_origin_initialized:
            self.viewport_background_parallax_origin_x = cam_x
            self.viewport_background_parallax_origin_y = cam_y
            self._viewport_background_parallax_origin_initialized = True
        zoom = max(0.05, float(self.render_scale if render_scale is None else render_scale))
        # Keep a small baseline zoom margin so panning is responsive at center,
        # even when viewport and background aspect match exactly.
        baseline_margin = 1.0 + (0.08 * pan_strength)
        parallax_zoom = max(0.05, baseline_margin, 1.0 + ((zoom - 1.0) * zoom_strength))
        parallax_pan_x = (cam_x - self.viewport_background_parallax_origin_x) * pan_strength
        parallax_pan_y = (cam_y - self.viewport_background_parallax_origin_y) * pan_strength

        draw_w = max(1.0, (x1 - x0) * parallax_zoom)
        draw_h = max(1.0, (y1 - y0) * parallax_zoom)
        coverage_scale = max(
            float(view_w) / max(1.0, draw_w),
            float(view_h) / max(1.0, draw_h),
            1.0,
        )
        if coverage_scale > 1.0:
            draw_w *= coverage_scale
            draw_h *= coverage_scale
        center_x = (float(view_w) * 0.5) + parallax_pan_x
        center_y = (float(view_h) * 0.5) + parallax_pan_y
        half_w = draw_w * 0.5
        half_h = draw_h * 0.5
        center_x = min(half_w, max(float(view_w) - half_w, center_x))
        center_y = min(half_h, max(float(view_h) - half_h, center_y))
        x0 = center_x - half_w
        y0 = center_y - half_h
        x1 = center_x + half_w
        y1 = center_y + half_h

        # Baseline mapping fixes imported image orientation in the viewport.
        # Extra flip toggles can invert either axis on demand.
        u_left = 1.0
        u_right = 0.0
        v_top = 0.0
        v_bottom = 1.0
        if self.viewport_background_flip_h:
            u_left, u_right = u_right, u_left
        if self.viewport_background_flip_v:
            v_top, v_bottom = v_bottom, v_top

        prev_binding = glGetIntegerv(GL_TEXTURE_BINDING_2D)
        glPushMatrix()
        glLoadIdentity()
        glEnable(GL_TEXTURE_2D)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glBindTexture(GL_TEXTURE_2D, active_texture_id)
        glBegin(GL_QUADS)
        glTexCoord2f(u_left, v_top)
        glVertex2f(x0, y0)
        glTexCoord2f(u_right, v_top)
        glVertex2f(x1, y0)
        glTexCoord2f(u_right, v_bottom)
        glVertex2f(x1, y1)
        glTexCoord2f(u_left, v_bottom)
        glVertex2f(x0, y1)
        glEnd()
        glBindTexture(GL_TEXTURE_2D, prev_binding)
        glPopMatrix()

        if mode.startswith("overlay_"):
            self._draw_viewport_background_color_quad(
                x0,
                y0,
                x1,
                y1,
                mode,
            )

    def _draw_viewport_background_color_quad(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        mode: str,
    ) -> None:
        r, g, b, a = self.background_color
        # Hue/Color are approximations in fixed-function OpenGL.
        normalized_mode = self._normalize_viewport_background_color_mode(mode)
        if normalized_mode in {"overlay_hue", "overlay_color", "replace", "overlay_normal"}:
            src_factor = GL_SRC_ALPHA
            dst_factor = GL_ONE_MINUS_SRC_ALPHA
        elif normalized_mode == "overlay_multiply":
            src_factor = GL_DST_COLOR
            dst_factor = GL_ONE_MINUS_SRC_ALPHA
        elif normalized_mode == "overlay_screen":
            src_factor = GL_ONE
            dst_factor = GL_ONE_MINUS_SRC_COLOR
        elif normalized_mode == "overlay_add":
            src_factor = GL_SRC_ALPHA
            dst_factor = GL_ONE
        else:
            return

        prev_binding = glGetIntegerv(GL_TEXTURE_BINDING_2D)
        texture_enabled = bool(glIsEnabled(GL_TEXTURE_2D))
        blend_enabled = bool(glIsEnabled(GL_BLEND))

        if texture_enabled:
            glDisable(GL_TEXTURE_2D)
        if not blend_enabled:
            glEnable(GL_BLEND)
        glBlendFunc(src_factor, dst_factor)

        glPushMatrix()
        glLoadIdentity()
        glColor4f(r, g, b, a)
        glBegin(GL_QUADS)
        glVertex2f(x0, y0)
        glVertex2f(x1, y0)
        glVertex2f(x1, y1)
        glVertex2f(x0, y1)
        glEnd()
        glPopMatrix()

        glBindTexture(GL_TEXTURE_2D, prev_binding)
        if texture_enabled:
            glEnable(GL_TEXTURE_2D)
        else:
            glDisable(GL_TEXTURE_2D)
        if blend_enabled:
            glEnable(GL_BLEND)
        else:
            glDisable(GL_BLEND)
        # Restore the blend state expected by layer rendering.
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)

    def _maybe_refresh_svg_background_texture(
        self,
        *,
        active_texture_id: Optional[int],
        view_w: int,
        view_h: int,
        img_w: int,
        img_h: int,
    ) -> None:
        if active_texture_id == self._default_viewport_background_texture_id:
            source_path = self._default_viewport_background_image_path
            is_svg = self._default_viewport_background_is_svg
            current_size = self._default_viewport_background_svg_raster_size
            is_default = True
        elif active_texture_id == self._viewport_background_texture_id:
            source_path = self.viewport_background_image_path
            is_svg = self._viewport_background_is_svg
            current_size = self._viewport_background_svg_raster_size
            is_default = False
        else:
            return
        if not is_svg or not source_path:
            return
        desired_w, desired_h = self._desired_svg_background_raster_size(
            view_w=view_w,
            view_h=view_h,
            img_w=img_w,
            img_h=img_h,
        )
        cur_w = max(1, int(current_size[0])) if current_size else 1
        cur_h = max(1, int(current_size[1])) if current_size else 1
        if desired_w <= int(cur_w * 1.15) and desired_h <= int(cur_h * 1.15):
            return
        updated = self._load_svg_background_as_pil(source_path, target_size=(desired_w, desired_h))
        if updated is None:
            return
        if is_default:
            self._default_viewport_background_image = updated
            self._default_viewport_background_svg_raster_size = updated.size
            self._upload_default_viewport_background_texture()
        else:
            self._viewport_background_image = updated
            self._viewport_background_svg_raster_size = updated.size
            self._upload_viewport_background_texture()

    def _desired_svg_background_raster_size(
        self,
        *,
        view_w: int,
        view_h: int,
        img_w: int,
        img_h: int,
    ) -> Tuple[int, int]:
        if img_w <= 0 or img_h <= 0:
            img_w = max(1, view_w)
            img_h = max(1, view_h)
        if self.viewport_background_keep_aspect:
            if self.viewport_background_zoom_fill:
                fit_scale = max(float(view_w) / float(img_w), float(view_h) / float(img_h))
            else:
                fit_scale = min(float(view_w) / float(img_w), float(view_h) / float(img_h))
            base_draw_w = max(1.0, float(img_w) * fit_scale)
            base_draw_h = max(1.0, float(img_h) * fit_scale)
        else:
            base_draw_w = float(view_w)
            base_draw_h = float(view_h)
        zoom_strength = (
            float(self.viewport_background_parallax_zoom_sensitivity)
            if self.viewport_background_parallax_enabled
            else 0.0
        )
        zoom = max(0.05, float(self.render_scale))
        parallax_zoom = max(0.05, 1.0 + ((zoom - 1.0) * zoom_strength))
        draw_w = base_draw_w * parallax_zoom
        draw_h = base_draw_h * parallax_zoom
        oversample = 1.35
        max_tex = max(1024, int(self._gl_max_texture_size or 8192))
        desired_w = max(1, min(max_tex, int(math.ceil(draw_w * oversample))))
        desired_h = max(1, min(max_tex, int(math.ceil(draw_h * oversample))))
        return desired_w, desired_h

    def _upload_particle_textures(self) -> None:
        """Upload any pending particle textures to OpenGL."""
        if Image is None:
            return
        for entry in self.particle_entries:
            if entry.texture_id or entry.texture_image is None:
                if entry.texture_id is None and entry.texture_image is None:
                    key = f"{entry.name}:missing_image"
                    if key not in self._particle_texture_failures:
                        print(f"[Particles] Missing texture image for emitter '{entry.name}'.")
                        self._particle_texture_failures.add(key)
                continue
            texture_id = self._upload_texture_from_image(entry.texture_image)
            if texture_id:
                entry.texture_id = texture_id
            else:
                key = f"{entry.name}:upload_failed"
                if key not in self._particle_texture_failures:
                    print(f"[Particles] Texture upload failed for emitter '{entry.name}'.")
                    self._particle_texture_failures.add(key)

    def _clear_particle_textures(self) -> None:
        """Delete old particle textures to avoid leaks."""
        if not self.particle_entries:
            return
        ids = [entry.texture_id for entry in self.particle_entries if entry.texture_id]
        if ids:
            try:
                glDeleteTextures(ids)
            except Exception:
                pass
        for entry in self.particle_entries:
            entry.texture_id = None

    def _upload_texture_from_image(self, image: "Image.Image") -> Optional[int]:
        """Create an OpenGL texture from a PIL image (premultiplied alpha)."""
        try:
            prev_unpack = glGetIntegerv(GL_UNPACK_ALIGNMENT)
            prev_active = glGetIntegerv(GL_ACTIVE_TEXTURE)
            prev_binding = glGetIntegerv(GL_TEXTURE_BINDING_2D)
            img = image.convert("RGBA")
            img_data = np.array(img, dtype=np.float32) / 255.0
            alpha = img_data[:, :, 3:4]
            img_data[:, :, 0:3] *= alpha
            img_data = (img_data * 255.0).astype(np.uint8)
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            tex_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(
                GL_TEXTURE_2D,
                0,
                GL_RGBA,
                img.width,
                img.height,
                0,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                img_data,
            )
            glBindTexture(GL_TEXTURE_2D, prev_binding)
            glActiveTexture(prev_active)
            glPixelStorei(GL_UNPACK_ALIGNMENT, prev_unpack)
            return tex_id
        except Exception as exc:
            try:
                import traceback

                print(f"[Particles] Texture upload failed: {exc}")
                traceback.print_exc()
            except Exception:
                pass
            return None

    def _get_animation_layer_cache(self, animation: Optional["AnimationData"]) -> Tuple[Dict[int, LayerData], Dict[int, int], List[LayerData], bool]:
        if not animation:
            return {}, {}, [], False
        key = id(animation)
        signature = tuple(layer.layer_id for layer in animation.layers)
        cached = self._animation_layer_cache.get(key)
        if cached and cached.get("signature") == signature:
            return (
                cached.get("layer_map", {}),
                cached.get("order_map", {}),
                cached.get("render_layers", []),
                bool(cached.get("has_depth", False)),
            )
        layer_map = {layer.layer_id: layer for layer in animation.layers}
        order_map = {layer.layer_id: idx for idx, layer in enumerate(animation.layers)}
        render_layers = list(reversed(animation.layers))
        has_depth = any(getattr(layer, "has_depth", False) for layer in animation.layers)
        self._animation_layer_cache[key] = {
            "signature": signature,
            "layer_map": layer_map,
            "order_map": order_map,
            "render_layers": render_layers,
            "has_depth": has_depth,
        }
        return layer_map, order_map, render_layers, has_depth

    def _render_animation_entry(
        self,
        animation: "AnimationData",
        time_value: float,
        atlases: List["TextureAtlas"],
        *,
        duration: float,
        offset_x: float,
        offset_y: float,
        render_overlays: bool,
        render_tiles: bool,
        apply_constraints: bool,
        render_attachments: bool,
        render_particles: bool,
        world_states: Optional[Dict[int, Dict]] = None,
    ) -> None:
        if not animation:
            return
        prev_animation = self.player.animation
        prev_duration = self.player.duration
        prev_atlases = self.texture_atlases
        try:
            self.player.animation = animation
            self.player.duration = duration or 0.0
            self.texture_atlases = list(atlases or [])
            glPushMatrix()
            if animation.centered:
                w = self.width()
                h = self.height()
                glTranslatef(w / 2, h / 2, 0)
            if offset_x or offset_y:
                glTranslatef(offset_x, offset_y, 0)
            if render_tiles:
                self._render_tile_batches()
            if world_states is None:
                layer_world_states = self.render_all_layers(
                    time_value,
                    apply_constraints=apply_constraints,
                    render_attachments=render_attachments,
                    render_particles=render_particles,
                )
            else:
                layer_world_states = self.render_layers_from_states(
                    world_states,
                    render_attachments=render_attachments,
                    render_particles=render_particles,
                )
            if render_overlays:
                if self.selected_layer_ids:
                    self.render_selection_outlines(layer_world_states)
                if self.anchor_overlay_enabled or self.parent_overlay_enabled:
                    self.render_anchor_parent_overlay(layer_world_states)
                if self.rotation_gizmo_enabled:
                    self.render_rotation_gizmo(layer_world_states)
                if self.scale_gizmo_enabled:
                    self.render_scale_gizmo(layer_world_states)
                if self.transform_overlay_enabled and (self.rotation_gizmo_enabled or self.scale_gizmo_enabled):
                    self._render_transform_overlay(layer_world_states)
                if self.attachment_debug_overlay_enabled:
                    self._render_attachment_debug_overlay()
                if self.particle_debug_overlay_enabled:
                    self._render_particle_debug_overlay()
                if self.show_bones:
                    self.render_bone_overlay(time_value)
            glPopMatrix()
        finally:
            self.player.animation = prev_animation
            self.player.duration = prev_duration
            self.texture_atlases = prev_atlases

    def set_tile_batches(self, batches: Optional[List[TileBatch]]) -> None:
        """Install static tile overlays sourced from island terrain data."""
        self.tile_batches = batches or []
        ctx = self.context()
        if ctx and ctx.isValid():
            self.makeCurrent()
            for batch in self.tile_batches:
                if batch.atlas and not batch.atlas.texture_id:
                    batch.atlas.load_texture()
            self.doneCurrent()
        self.update()

    def set_terrain_composite(self, composite: Optional[TerrainComposite]) -> None:
        """Install stitched island terrain texture rendered in one draw call."""
        self.terrain_composite = composite
        ctx = self.context()
        if ctx and ctx.isValid():
            self.makeCurrent()
            self._upload_terrain_composite_texture()
            self.doneCurrent()
        self.update()

    def _upload_terrain_composite_texture(self) -> None:
        """Upload or clear the stitched terrain texture."""
        if self._terrain_composite_texture_id:
            glDeleteTextures(1, [self._terrain_composite_texture_id])
            self._terrain_composite_texture_id = None
        if not self.terrain_composite:
            return
        composite = self.terrain_composite
        if composite.width <= 0 or composite.height <= 0 or not composite.rgba_bytes:
            return
        texture_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, texture_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glTexImage2D(
            GL_TEXTURE_2D,
            0,
            GL_RGBA,
            composite.width,
            composite.height,
            0,
            GL_RGBA,
            GL_UNSIGNED_BYTE,
            composite.rgba_bytes,
        )
        self._terrain_composite_texture_id = texture_id

    def set_tile_render_path(self, mode: str) -> None:
        """Set terrain tile geometry path and refresh."""
        normalized = (mode or "").strip().lower()
        if normalized not in {"diamond_fan", "full_quad"}:
            normalized = "full_quad"
        if self.tile_render_path == normalized:
            return
        self.tile_render_path = normalized
        self.update()

    def set_tile_filter_mode(self, mode: str) -> None:
        """Set terrain-only texture filtering mode and refresh."""
        normalized = (mode or "").strip().lower()
        if normalized not in {"nearest", "linear"}:
            normalized = "nearest"
        if self.tile_filter_mode == normalized:
            return
        self.tile_filter_mode = normalized
        self.update()

    def set_sprite_filter_mode(self, mode: str) -> None:
        """Set sprite texture filtering mode and refresh."""
        normalized = self.renderer.set_texture_filter_mode(mode)
        if self.sprite_filter_mode == normalized:
            return
        self.sprite_filter_mode = normalized
        self.update()

    def set_sprite_filter_strength(self, strength: float) -> None:
        """Set bicubic/lanczos filter blend strength and refresh."""
        normalized = self.renderer.set_texture_filter_strength(strength)
        if self.sprite_filter_strength == normalized:
            return
        self.sprite_filter_strength = normalized
        self.update()

    def set_dof_alpha_smoothing_enabled(self, enabled: bool) -> None:
        """Enable/disable DOF alpha edge smoothing."""
        normalized = self.renderer.set_dof_alpha_smoothing_enabled(enabled)
        if self.dof_alpha_smoothing_enabled == normalized:
            return
        self.dof_alpha_smoothing_enabled = normalized
        self.update()

    def set_dof_alpha_smoothing_strength(self, strength: float) -> None:
        """Set DOF alpha edge smoothing strength (0..1)."""
        normalized = self.renderer.set_dof_alpha_smoothing_strength(strength)
        if self.dof_alpha_smoothing_strength == normalized:
            return
        self.dof_alpha_smoothing_strength = normalized
        self.update()

    def set_dof_alpha_smoothing_mode(self, mode: str) -> None:
        """Set DOF alpha edge smoothing mode (normal/strong)."""
        normalized = self.renderer.set_dof_alpha_smoothing_mode(mode)
        if self.dof_alpha_smoothing_mode == normalized:
            return
        self.dof_alpha_smoothing_mode = normalized
        self.update()

    def set_dof_sprite_shader_mode(self, mode: str) -> None:
        """Set experimental DOF sprite shader emulation mode."""
        normalized = self.renderer.set_dof_sprite_shader_mode(mode)
        if self.dof_sprite_shader_mode == normalized:
            return
        self.dof_sprite_shader_mode = normalized
        self.update()

    def set_tile_flag_order_mode(self, mode: str) -> None:
        """Set terrain draw ordering policy for tile flags and refresh."""
        normalized = (mode or "").strip().lower()
        if normalized not in {"as_is", "flag0_then1", "flag1_then0"}:
            normalized = "flag0_then1"
        if self.tile_flag_order_mode == normalized:
            return
        self.tile_flag_order_mode = normalized
        self.update()

    def set_tile_flag1_transform_mode(self, mode: str) -> None:
        """Set transform mode for tiles where flag != 0."""
        normalized = (mode or "").strip().lower()
        if normalized not in {"none", "hflip", "vflip", "hvflip"}:
            normalized = "none"
        if self.tile_flag1_transform_mode == normalized:
            return
        self.tile_flag1_transform_mode = normalized
        self.update()

    def set_tile_grid_mirrored(self, mirrored: bool) -> None:
        """Set whether the grid coordinates have been mirrored during alignment."""
        self.tile_grid_mirrored = bool(mirrored)
        self.update()

    def set_tile_global_transform(
        self,
        offset_x: float,
        offset_y: float,
        rotation_deg: float,
        scale: float,
    ) -> None:
        """Set global transform applied to the full terrain pass."""
        safe_scale = max(0.01, float(scale))
        self.tile_global_offset_x = float(offset_x)
        self.tile_global_offset_y = float(offset_y)
        self.tile_global_rotation_deg = float(rotation_deg)
        self.tile_global_scale = safe_scale
        self.update()

    def set_tile_selected_index(self, index: int) -> None:
        """Set the selected tile index for per-tile transform overrides."""
        self.tile_selected_index = int(index)
        self.update()

    def set_tile_selected_transform(
        self,
        offset_x: float,
        offset_y: float,
        rotation_deg: float,
        scale: float,
    ) -> None:
        """Set per-tile transform applied only to the selected tile index."""
        safe_scale = max(0.01, float(scale))
        self.tile_selected_offset_x = float(offset_x)
        self.tile_selected_offset_y = float(offset_y)
        self.tile_selected_rotation_deg = float(rotation_deg)
        self.tile_selected_scale = safe_scale
        self.update()

    def _render_tile_batches(self) -> None:
        """Draw precomputed tile quads beneath standard animation layers."""
        active_path = self.tile_render_path if self.tile_render_path in {"diamond_fan", "full_quad"} else "full_quad"
        active_filter = self.tile_filter_mode if self.tile_filter_mode in {"nearest", "linear"} else "nearest"
        active_flag_order = (
            self.tile_flag_order_mode
            if self.tile_flag_order_mode in {"as_is", "flag0_then1", "flag1_then0"}
            else "flag0_then1"
        )
        active_flag1_transform = (
            self.tile_flag1_transform_mode
            if self.tile_flag1_transform_mode in {"none", "hflip", "vflip", "hvflip"}
            else "none"
        )
        if self.tile_grid_mirrored:
            if active_flag1_transform == "vflip":
                active_flag1_transform = "hflip"
            elif active_flag1_transform == "hflip":
                active_flag1_transform = "vflip"
        stats_start = time.perf_counter()
        tile_count = 0
        flag0_count = 0
        flag1_count = 0
        per_tile_override_active = (
            self.tile_selected_index >= 0
            and (
                abs(self.tile_selected_offset_x) > 1e-6
                or abs(self.tile_selected_offset_y) > 1e-6
                or abs(self.tile_selected_rotation_deg) > 1e-6
                or abs(self.tile_selected_scale - 1.0) > 1e-6
            )
        )
        global_rad = math.radians(self.tile_global_rotation_deg)
        global_cos = math.cos(global_rad)
        global_sin = math.sin(global_rad)
        global_scale = max(0.01, float(self.tile_global_scale))

        def _global_pivot() -> Tuple[float, float]:
            if self.terrain_composite:
                comp = self.terrain_composite
                return (comp.origin_x + (comp.width * 0.5), comp.origin_y + (comp.height * 0.5))
            min_x = float("inf")
            min_y = float("inf")
            max_x = float("-inf")
            max_y = float("-inf")
            for _batch in self.tile_batches:
                for _instance in _batch.instances:
                    min_x = min(min_x, _instance.center_x)
                    min_y = min(min_y, _instance.center_y)
                    max_x = max(max_x, _instance.center_x)
                    max_y = max(max_y, _instance.center_y)
            if min_x == float("inf"):
                return (0.0, 0.0)
            return ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)

        pivot_x, pivot_y = _global_pivot()

        def _apply_global_transform(x: float, y: float) -> Tuple[float, float]:
            dx = (x - pivot_x) * global_scale
            dy = (y - pivot_y) * global_scale
            rx = (dx * global_cos) - (dy * global_sin)
            ry = (dx * global_sin) + (dy * global_cos)
            return (
                pivot_x + rx + self.tile_global_offset_x,
                pivot_y + ry + self.tile_global_offset_y,
            )

        def _draw_textured_quad_rotated(
            cx: float,
            cy: float,
            half_w: float,
            half_h: float,
            u0: float,
            v0: float,
            u1: float,
            v1: float,
            z: float,
            rotation_deg: float,
        ) -> None:
            angle = math.radians(rotation_deg)
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            local = (
                (-half_w, -half_h),
                (half_w, -half_h),
                (half_w, half_h),
                (-half_w, half_h),
            )
            uv = ((u0, v0), (u1, v0), (u1, v1), (u0, v1))
            glBegin(GL_QUADS)
            for (lx, ly), (u, v) in zip(local, uv):
                px = (lx * cos_a) - (ly * sin_a) + cx
                py = (lx * sin_a) + (ly * cos_a) + cy
                glTexCoord2f(u, v)
                glVertex3f(px, py, z)
            glEnd()

        def _draw_textured_diamond_fan_rotated(
            cx: float,
            cy: float,
            half_w: float,
            half_h: float,
            u0: float,
            v0: float,
            u1: float,
            v1: float,
            z: float,
            rotation_deg: float,
        ) -> None:
            u_mid = (u0 + u1) * 0.5
            v_mid = (v0 + v1) * 0.5
            angle = math.radians(rotation_deg)
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)

            def _rot(lx: float, ly: float) -> Tuple[float, float]:
                return (
                    (lx * cos_a) - (ly * sin_a) + cx,
                    (lx * sin_a) + (ly * cos_a) + cy,
                )

            top = _rot(0.0, -half_h)
            right = _rot(half_w, 0.0)
            bottom = _rot(0.0, half_h)
            left = _rot(-half_w, 0.0)
            center = (cx, cy)
            glBegin(GL_TRIANGLES)
            glTexCoord2f(u_mid, v0)
            glVertex3f(top[0], top[1], z)
            glTexCoord2f(u1, v_mid)
            glVertex3f(right[0], right[1], z)
            glTexCoord2f(u_mid, v_mid)
            glVertex3f(center[0], center[1], z)

            glTexCoord2f(u1, v_mid)
            glVertex3f(right[0], right[1], z)
            glTexCoord2f(u_mid, v1)
            glVertex3f(bottom[0], bottom[1], z)
            glTexCoord2f(u_mid, v_mid)
            glVertex3f(center[0], center[1], z)

            glTexCoord2f(u_mid, v1)
            glVertex3f(bottom[0], bottom[1], z)
            glTexCoord2f(u0, v_mid)
            glVertex3f(left[0], left[1], z)
            glTexCoord2f(u_mid, v_mid)
            glVertex3f(center[0], center[1], z)

            glTexCoord2f(u0, v_mid)
            glVertex3f(left[0], left[1], z)
            glTexCoord2f(u_mid, v0)
            glVertex3f(top[0], top[1], z)
            glTexCoord2f(u_mid, v_mid)
            glVertex3f(center[0], center[1], z)
            glEnd()

        if self.terrain_composite and not per_tile_override_active and active_path == "full_quad":
            if not self._terrain_composite_texture_id:
                self._upload_terrain_composite_texture()
            if self._terrain_composite_texture_id and self.terrain_composite.width > 0 and self.terrain_composite.height > 0:
                composite = self.terrain_composite
                active_path = "stitched_single_texture"
                glPushAttrib(
                    GL_ENABLE_BIT | GL_COLOR_BUFFER_BIT | GL_CURRENT_BIT | GL_TEXTURE_BIT | GL_DEPTH_BUFFER_BIT
                )
                reset_blend_mode()
                glDisable(GL_DEPTH_TEST)
                glDepthMask(GL_FALSE)
                glBindTexture(GL_TEXTURE_2D, self._terrain_composite_texture_id)
                filter_enum = GL_NEAREST if active_filter == "nearest" else GL_LINEAR
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filter_enum)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filter_enum)
                glColor4f(1.0, 1.0, 1.0, composite.alpha)
                pos_scale = max(self.renderer.position_scale, 1e-6)
                x0 = composite.origin_x * pos_scale
                y0 = composite.origin_y * pos_scale
                x1 = (composite.origin_x + composite.width) * pos_scale
                y1 = (composite.origin_y + composite.height) * pos_scale
                z = composite.depth
                cx = (x0 + x1) * 0.5
                cy = (y0 + y1) * 0.5
                cx, cy = _apply_global_transform(cx, cy)
                half_w = (composite.width * 0.5) * global_scale
                half_h = (composite.height * 0.5) * global_scale
                _draw_textured_quad_rotated(
                    cx,
                    cy,
                    half_w,
                    half_h,
                    0.0,
                    0.0,
                    1.0,
                    1.0,
                    z,
                    self.tile_global_rotation_deg,
                )
                glDepthMask(GL_TRUE)
                glPopAttrib()
                tile_count = int(composite.tile_count)
                flag0_count = int(composite.flag0_count)
                flag1_count = int(composite.flag1_count)
            elapsed_ms = (time.perf_counter() - stats_start) * 1000.0
            stats = {
                "path": active_path,
                "filter": active_filter,
                "flag_order": active_flag_order,
                "flag1_transform": active_flag1_transform,
                "ms": elapsed_ms,
                "tile_count": tile_count,
                "flag0_count": flag0_count,
                "flag1_count": flag1_count,
            }
            self._tile_last_stats = stats
            now = time.perf_counter()
            signature = f"{active_path}|{active_filter}|{active_flag_order}|{active_flag1_transform}"
            if (
                signature != self._tile_last_signature
                or (now - self._tile_stats_last_emit) >= self._tile_stats_emit_interval_s
            ):
                self._tile_last_signature = signature
                self._tile_stats_last_emit = now
                self.tile_render_stats.emit(dict(stats))
            return
        if not self.tile_batches:
            stats = {
                "path": active_path,
                "filter": active_filter,
                "flag_order": active_flag_order,
                "flag1_transform": active_flag1_transform,
                "ms": 0.0,
                "tile_count": 0,
                "flag0_count": 0,
                "flag1_count": 0,
            }
            self._tile_last_stats = stats
            return

        glPushAttrib(
            GL_ENABLE_BIT | GL_COLOR_BUFFER_BIT | GL_CURRENT_BIT | GL_TEXTURE_BIT | GL_DEPTH_BUFFER_BIT
        )
        reset_blend_mode()
        # Terrain is strictly a 2D compositing pass; depth testing/writes can cause
        # overlapping tile fragments to be rejected at equal Z and appear "cut".
        glDisable(GL_DEPTH_TEST)
        glDepthMask(GL_FALSE)
        for batch in self.tile_batches:
            atlas = batch.atlas
            if not atlas or not atlas.sprites:
                continue
            if not atlas.texture_id:
                atlas.load_texture()
            if not atlas.texture_id or atlas.image_width <= 0 or atlas.image_height <= 0:
                continue
            glBindTexture(GL_TEXTURE_2D, atlas.texture_id)
            filter_enum = GL_NEAREST if active_filter == "nearest" else GL_LINEAR
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filter_enum)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filter_enum)
            glColor4f(1.0, 1.0, 1.0, batch.alpha)
            instances = batch.instances
            if active_path == "full_quad":
                if active_flag_order == "flag0_then1":
                    instances = sorted(
                        batch.instances,
                        key=lambda inst: (
                            0 if int(getattr(inst, "flag", 0)) == 0 else 1,
                            float(getattr(inst, "depth", 0.0)),
                            int(getattr(inst, "row", 0)),
                            int(getattr(inst, "column", 0)),
                        ),
                    )
                elif active_flag_order == "flag1_then0":
                    instances = sorted(
                        batch.instances,
                        key=lambda inst: (
                            0 if int(getattr(inst, "flag", 0)) != 0 else 1,
                            float(getattr(inst, "depth", 0.0)),
                            int(getattr(inst, "row", 0)),
                            int(getattr(inst, "column", 0)),
                        ),
                    )
                else:
                    instances = sorted(
                        batch.instances,
                        key=lambda inst: (
                            float(getattr(inst, "depth", 0.0)),
                            int(getattr(inst, "row", 0)),
                            int(getattr(inst, "column", 0)),
                        ),
                    )
            else:
                if active_flag_order == "flag0_then1":
                    instances = sorted(
                        batch.instances,
                        key=lambda inst: (0 if int(getattr(inst, "flag", 0)) == 0 else 1),
                    )
                elif active_flag_order == "flag1_then0":
                    instances = sorted(
                        batch.instances,
                        key=lambda inst: (0 if int(getattr(inst, "flag", 0)) != 0 else 1),
                    )
            for instance in instances:
                sprite = atlas.get_sprite(instance.sprite_name)
                if not sprite:
                    continue
                is_selected = tile_count == self.tile_selected_index
                width = sprite.w * instance.scale * global_scale
                height = sprite.h * instance.scale * global_scale
                if is_selected:
                    width *= self.tile_selected_scale
                    height *= self.tile_selected_scale
                cx, cy = _apply_global_transform(instance.center_x, instance.center_y)
                if is_selected:
                    cx += self.tile_selected_offset_x
                    cy += self.tile_selected_offset_y
                half_w = width * 0.5
                half_h = height * 0.5
                if atlas.image_width <= 0 or atlas.image_height <= 0:
                    continue
                inv_w = 1.0 / float(atlas.image_width)
                inv_h = 1.0 / float(atlas.image_height)
                # Tile atlas regions touch/overlap in MSM island sheets; insetting UVs by
                # half a texel prevents bilinear sampling from pulling neighboring texels.
                u0_px = float(sprite.x) + 0.5
                v0_px = float(sprite.y) + 0.5
                u1_px = float(sprite.x + sprite.w) - 0.5
                v1_px = float(sprite.y + sprite.h) - 0.5
                if u1_px <= u0_px:
                    u0_px = float(sprite.x)
                    u1_px = float(sprite.x + sprite.w)
                if v1_px <= v0_px:
                    v0_px = float(sprite.y)
                    v1_px = float(sprite.y + sprite.h)
                u0 = u0_px * inv_w
                v0 = v0_px * inv_h
                u1 = u1_px * inv_w
                v1 = v1_px * inv_h
                if int(getattr(instance, "flag", 0)) != 0:
                    if active_flag1_transform in {"hflip", "hvflip"}:
                        u0, u1 = u1, u0
                    if active_flag1_transform in {"vflip", "hvflip"}:
                        v0, v1 = v1, v0
                z = batch.depth
                tile_rotation = self.tile_global_rotation_deg
                if is_selected:
                    tile_rotation += self.tile_selected_rotation_deg
                if active_path == "full_quad":
                    _draw_textured_quad_rotated(
                        cx,
                        cy,
                        half_w,
                        half_h,
                        u0,
                        v0,
                        u1,
                        v1,
                        z,
                        tile_rotation,
                    )
                else:
                    _draw_textured_diamond_fan_rotated(
                        cx,
                        cy,
                        half_w,
                        half_h,
                        u0,
                        v0,
                        u1,
                        v1,
                        z,
                        tile_rotation,
                    )
                tile_count += 1
                if int(getattr(instance, "flag", 0)) == 0:
                    flag0_count += 1
                else:
                    flag1_count += 1
        glDepthMask(GL_TRUE)
        glPopAttrib()
        elapsed_ms = (time.perf_counter() - stats_start) * 1000.0
        stats = {
            "path": active_path,
            "filter": active_filter,
            "flag_order": active_flag_order,
            "flag1_transform": active_flag1_transform,
            "ms": elapsed_ms,
            "tile_count": tile_count,
            "flag0_count": flag0_count,
            "flag1_count": flag1_count,
        }
        self._tile_last_stats = stats
        now = time.perf_counter()
        signature = f"{active_path}|{active_filter}|{active_flag_order}|{active_flag1_transform}"
        if (
            signature != self._tile_last_signature
            or (now - self._tile_stats_last_emit) >= self._tile_stats_emit_interval_s
        ):
            self._tile_last_signature = signature
            self._tile_stats_last_emit = now
            self.tile_render_stats.emit(dict(stats))
    
    def render_all_layers(
        self,
        time: float,
        *,
        apply_constraints: bool = True,
        render_attachments: bool = True,
        render_particles: bool = False,
    ):
        """
        Render all layers in correct order with hierarchy

        Args:
            time: Current animation time
        """
        self.renderer.current_time = time
        self.renderer.animation_duration = self.player.duration or 0.0
        # Calculate world positions for all layers first
        layer_world_states = self._build_layer_world_states(time, apply_constraints=apply_constraints)
        self._last_layer_world_states = layer_world_states
        attachment_map = self._group_attachments_by_layer() if render_attachments else {}
        self.renderer.reset_layer_masks()

        _layer_map, _order_map, _render_layers, has_depth = self._get_animation_layer_cache(
            self.player.animation
        )
        render_layers = self._get_render_layers(layer_world_states)
        particle_draws = self._build_particle_draws(self.player.animation, time) if render_particles else []
        if particle_draws and layer_world_states:
            depths = [state.get("depth", 0.0) for state in layer_world_states.values()]
            if depths:
                layer_min = min(depths)
                layer_max = max(depths)
                particle_depths = [
                    draw.depth for draw in particle_draws
                    if not getattr(draw, "authoritative_depth", False)
                ]
                if particle_depths:
                    p_min = min(particle_depths)
                    p_max = max(particle_depths)
                    if (p_max - p_min) > 1e-6 and (p_min < layer_min or p_max > layer_max):
                        scale = (layer_max - layer_min) / (p_max - p_min)
                        for draw in particle_draws:
                            if getattr(draw, "authoritative_depth", False):
                                continue
                            draw.depth = layer_min + (draw.depth - p_min) * scale
                for draw in particle_draws:
                    if getattr(draw, "authoritative_depth", False):
                        continue
                    if draw.depth < layer_min and -draw.depth > layer_max:
                        draw.depth = -draw.depth
        if has_depth and particle_draws:
            particle_draws.sort(key=lambda item: item.depth)
        particle_idx = 0

        for layer in render_layers:
            if layer.visible:
                world_state = layer_world_states[layer.layer_id]
                if has_depth and particle_draws:
                    layer_depth = world_state.get("depth", 0.0)
                    while particle_idx < len(particle_draws) and particle_draws[particle_idx].depth <= layer_depth:
                        self._render_particle_draw(particle_draws[particle_idx])
                        particle_idx += 1
                override_atlases = self.layer_atlas_overrides.get(layer.layer_id)
                atlas_chain = (
                    list(override_atlases) + self.texture_atlases
                    if override_atlases
                    else self.texture_atlases
                )
                self.renderer.render_layer(
                    layer, world_state, atlas_chain, self.layer_offsets
                )
                if attachment_map:
                    for instance in attachment_map.get(layer.layer_id, []):
                        self._render_attachment_layers(instance, world_state)
        if particle_draws:
            for idx in range(particle_idx, len(particle_draws)):
                self._render_particle_draw(particle_draws[idx])
            reset_blend_mode()
        
        return layer_world_states

    def render_layers_from_states(
        self,
        layer_world_states: Dict[int, Dict],
        *,
        render_attachments: bool = True,
        render_particles: bool = False,
    ) -> Dict[int, Dict]:
        """Render layers using precomputed world states (skip compute)."""
        if not self.player.animation:
            return layer_world_states
        self._last_layer_world_states = layer_world_states
        attachment_map = self._group_attachments_by_layer() if render_attachments else {}
        self.renderer.reset_layer_masks()
        _layer_map, _order_map, _render_layers, has_depth = self._get_animation_layer_cache(
            self.player.animation
        )
        render_layers = self._get_render_layers(layer_world_states)
        particle_draws = self._build_particle_draws(self.player.animation, self.player.current_time) if render_particles else []
        if particle_draws and layer_world_states:
            depths = [state.get("depth", 0.0) for state in layer_world_states.values()]
            if depths:
                layer_min = min(depths)
                layer_max = max(depths)
                particle_depths = [
                    draw.depth for draw in particle_draws
                    if not getattr(draw, "authoritative_depth", False)
                ]
                if particle_depths:
                    p_min = min(particle_depths)
                    p_max = max(particle_depths)
                    if (p_max - p_min) > 1e-6 and (p_min < layer_min or p_max > layer_max):
                        scale = (layer_max - layer_min) / (p_max - p_min)
                        for draw in particle_draws:
                            if getattr(draw, "authoritative_depth", False):
                                continue
                            draw.depth = layer_min + (draw.depth - p_min) * scale
                for draw in particle_draws:
                    if getattr(draw, "authoritative_depth", False):
                        continue
                    if draw.depth < layer_min and -draw.depth > layer_max:
                        draw.depth = -draw.depth
        if has_depth and particle_draws:
            particle_draws.sort(key=lambda item: item.depth)
        particle_idx = 0
        for layer in render_layers:
            if not layer.visible:
                continue
            world_state = layer_world_states.get(layer.layer_id)
            if not world_state:
                continue
            if has_depth and particle_draws:
                layer_depth = world_state.get("depth", 0.0)
                while particle_idx < len(particle_draws) and particle_draws[particle_idx].depth <= layer_depth:
                    self._render_particle_draw(particle_draws[particle_idx])
                    particle_idx += 1
            override_atlases = self.layer_atlas_overrides.get(layer.layer_id)
            atlas_chain = (
                list(override_atlases) + self.texture_atlases
                if override_atlases
                else self.texture_atlases
            )
            self.renderer.render_layer(
                layer, world_state, atlas_chain, self.layer_offsets
            )
            if attachment_map:
                for instance in attachment_map.get(layer.layer_id, []):
                    self._render_attachment_layers(instance, world_state)
        if particle_draws:
            for idx in range(particle_idx, len(particle_draws)):
                self._render_particle_draw(particle_draws[idx])
            reset_blend_mode()
        return layer_world_states

    def _eval_particle_channel(
        self,
        keys: List[Tuple[float, float, int]],
        time_value: float,
        default: float,
        *,
        allow_linear_keytype1: bool = True,
    ) -> float:
        if not keys:
            return default
        if time_value <= keys[0][0]:
            return keys[0][1]
        for idx in range(1, len(keys)):
            t0, v0, k0 = keys[idx - 1]
            t1, v1, _ = keys[idx]
            if time_value < t1:
                if k0 not in (0, 1) or (k0 == 1 and not allow_linear_keytype1):
                    return v0
                if t1 == t0:
                    return v1
                alpha = (time_value - t0) / (t1 - t0)
                return v0 + (v1 - v0) * alpha
        return keys[-1][1]

    @staticmethod
    def _rand01(seed: float) -> float:
        return (math.sin(seed * 12.9898) * 43758.5453) % 1.0

    def _rand_range(self, seed: float, min_val: float, max_val: float) -> float:
        return min_val + (max_val - min_val) * self._rand01(seed)

    def _get_particle_emission_times(
        self,
        entry: ParticleRenderEntry,
        duration: float,
        base_rate: float,
    ) -> List[float]:
        keys = entry.channels.get(12, [])
        if duration <= 0.0 or base_rate <= 0.0 or len(keys) <= 1:
            return []
        cache_key = (
            entry.name,
            base_rate,
            duration,
            tuple(keys),
        )
        cached = self._particle_emission_cache.get(cache_key)
        if cached is not None:
            return cached
        dt = 1.0 / 60.0
        total_time = max(duration, 0.0)
        times: List[float] = []
        t = 0.0
        cumulative = 0.0
        while t <= total_time + dt:
            rate_scale = self._eval_particle_channel(
                keys,
                t,
                1.0,
                allow_linear_keytype1=True,
            )
            try:
                rate_scale = float(rate_scale)
            except Exception:
                rate_scale = 1.0
            if rate_scale < 0.0:
                rate_scale = 0.0
            rate = base_rate * rate_scale
            cumulative_next = cumulative + rate * dt
            if cumulative_next > cumulative:
                start_int = int(math.floor(cumulative))
                end_int = int(math.floor(cumulative_next))
                if end_int > start_int:
                    span = cumulative_next - cumulative
                    for count in range(start_int + 1, end_int + 1):
                        frac = (count - cumulative) / span if span > 0.0 else 0.0
                        emit_time = t + frac * dt
                        times.append(emit_time)
            cumulative = cumulative_next
            t += dt
            if len(times) > 50000:
                break
        self._particle_emission_cache[cache_key] = times
        return times

    def _build_particle_draws(self, animation: "AnimationData", time_value: float) -> List[ParticleDraw]:
        self._particle_debug_samples = []
        self._particle_debug_info = {}
        if not self.particle_entries:
            return []
        draws: List[ParticleDraw] = []
        base_scale = (
            self.renderer.base_world_scale
            * self.renderer.position_scale
            * self.renderer.local_position_multiplier
        )
        unit_scale = base_scale * 100.0
        flip_y = bool(self._particle_flip_y)
        if self.particle_viewport_cap <= 0:
            return []
        max_particles_per_emitter = 2000
        max_total_particles = int(self.particle_viewport_cap)
        if self.fast_preview_enabled:
            max_total_particles = min(max_total_particles, max(1, max_total_particles // 3))
        adaptive_budget_per_emitter = max(
            1,
            int(math.ceil(max_total_particles / float(max(1, len(self.particle_entries))))),
        )
        animation_layer_map: Dict[int, LayerData] = {}
        if animation and getattr(animation, "layers", None):
            animation_layer_map = {layer.layer_id: layer for layer in animation.layers}
        matched_layer_state_cache: Dict[Tuple[int, int], Optional[Dict[str, Any]]] = {}
        matched_layer_point_cache: Dict[Tuple[int, int], Optional[Tuple[float, float]]] = {}
        emitter_origin_cache: Dict[Tuple[int, int, bool, bool], Tuple[float, float, float]] = {}

        def rotate_by_quat(x: float, y: float, z: float, qx: float, qy: float, qz: float, qw: float) -> Tuple[float, float, float]:
            # t = 2 * cross(q.xyz, v)
            tx = 2.0 * (qy * z - qz * y)
            ty = 2.0 * (qz * x - qx * z)
            tz = 2.0 * (qx * y - qy * x)
            # v' = v + qw * t + cross(q.xyz, t)
            rx = x + qw * tx + (qy * tz - qz * ty)
            ry = y + qw * ty + (qz * tx - qx * tz)
            rz = z + qw * tz + (qx * ty - qy * tx)
            return rx, ry, rz

        def rotate_by_quat_2d(x: float, y: float, qx: float, qy: float, qz: float, qw: float) -> Tuple[float, float]:
            rx, _ry, rz = rotate_by_quat(x, y, 0.0, qx, qy, qz, qw)
            return rx, rz

        def project_prefab_vec_to_plane(
            x: float,
            y: float,
            z: float,
            entry: ParticleRenderEntry,
        ) -> Tuple[float, float]:
            if entry.prefab_rot:
                qx, qy, qz, qw = entry.prefab_rot
                if abs(qx) > 1e-4 or abs(qy) > 1e-4 or abs(qz) > 1e-4:
                    rx, ry, _rz = rotate_by_quat(x, y, z, qx, qy, qz, qw)
                    return rx, -ry
            return x, -y

        def _eval_color_keys(keys: List[Tuple[float, Tuple[float, float, float]]], t: float) -> Tuple[float, float, float]:
            if not keys:
                return (1.0, 1.0, 1.0)
            if t <= keys[0][0]:
                return keys[0][1]
            for idx in range(1, len(keys)):
                t1, c1 = keys[idx]
                t0, c0 = keys[idx - 1]
                if t <= t1:
                    denom = (t1 - t0) if t1 != t0 else 1.0
                    f = (t - t0) / denom
                    return (
                        c0[0] + (c1[0] - c0[0]) * f,
                        c0[1] + (c1[1] - c0[1]) * f,
                        c0[2] + (c1[2] - c0[2]) * f,
                    )
            return keys[-1][1]

        def _eval_alpha_keys(keys: List[Tuple[float, float]], t: float) -> float:
            if not keys:
                return 1.0
            if t <= keys[0][0]:
                return keys[0][1]
            for idx in range(1, len(keys)):
                t1, a1 = keys[idx]
                t0, a0 = keys[idx - 1]
                if t <= t1:
                    denom = (t1 - t0) if t1 != t0 else 1.0
                    f = (t - t0) / denom
                    return a0 + (a1 - a0) * f
            return keys[-1][1]

        def _eval_curve_keys(keys: List[Tuple[float, float]], t: float) -> float:
            if not keys:
                return 1.0
            if t <= keys[0][0]:
                return keys[0][1]
            for idx in range(1, len(keys)):
                t1, v1 = keys[idx]
                t0, v0 = keys[idx - 1]
                if t <= t1:
                    denom = (t1 - t0) if t1 != t0 else 1.0
                    f = (t - t0) / denom
                    return v0 + (v1 - v0) * f
            return keys[-1][1]

        def _sample_authored_node_pos(
            channels: Optional[Dict[int, List[Tuple[float, float, int]]]],
            sample_time: float,
            *,
            offset: Tuple[float, float, float] = (0.0, 0.0, 0.0),
            prefab_pos: Tuple[float, float, float] = (0.0, 0.0, 0.0),
            fold_depth: bool = True,
            apply_flip: bool = False,
            apply_scale: bool = False,
        ) -> Tuple[float, float, float]:
            channel_map = channels or {}
            px = self._eval_particle_channel(channel_map.get(0, []), sample_time, 0.0)
            py = self._eval_particle_channel(channel_map.get(1, []), sample_time, 0.0)
            pz = self._eval_particle_channel(channel_map.get(2, []), sample_time, 0.0)
            px += offset[0] + prefab_pos[0]
            py += offset[1] + prefab_pos[1]
            pz += offset[2] + prefab_pos[2]
            if fold_depth:
                py -= pz
            if apply_flip and flip_y:
                py = -py
            if apply_scale:
                px *= base_scale
                py *= base_scale
            return px, py, pz

        def _sample_emitter_origin(
            entry: ParticleRenderEntry,
            sample_time: float,
            *,
            apply_flip: bool,
            apply_scale: bool,
            use_surface_projection: bool = True,
        ) -> Tuple[float, float, float]:
            time_key = int(round(sample_time * 240.0))
            origin_cache_key = (
                entry.seed_base,
                time_key,
                bool(apply_flip),
                bool(apply_scale),
                bool(use_surface_projection),
            )
            cached_origin = emitter_origin_cache.get(origin_cache_key)
            if cached_origin is not None:
                return cached_origin
            # Keep the particle-node Z as the authored elevation term, but allow the
            # source X/Y to come from a stable controlpoint socket when one is available.
            node_x, node_y, node_z = _sample_authored_node_pos(
                entry.channels,
                sample_time,
                offset=entry.offset,
                prefab_pos=entry.prefab_pos,
                fold_depth=False,
                apply_flip=False,
                apply_scale=False,
            )

            source_x = node_x
            source_y = node_y
            source_z = node_z
            if entry.control_channels:
                control_x, control_y, control_z = _sample_authored_node_pos(
                    entry.control_channels,
                    sample_time,
                    offset=entry.control_offset,
                    fold_depth=False,
                    apply_flip=False,
                    apply_scale=False,
                )
                source_x = control_x
                source_y = control_y
                source_z = control_z

            source_y -= source_z
            if apply_flip and flip_y:
                source_y = -source_y
            if apply_scale:
                source_x *= base_scale
                source_y *= base_scale
                source_x += self.renderer.world_offset_x
                source_y += self.renderer.world_offset_y
                if use_surface_projection:
                    matched_source = _sample_matched_layer_surface(entry, sample_time)
                    if matched_source is not None:
                        source_x, source_y = matched_source
                source_x += self.particle_origin_offset_x
                source_y += self.particle_origin_offset_y
            result = (source_x, source_y, source_z)
            emitter_origin_cache[origin_cache_key] = result
            return result

        def _sample_emitter_pos_raw(entry: ParticleRenderEntry, sample_time: float) -> Tuple[float, float]:
            px, py, _pz = _sample_emitter_origin(
                entry,
                sample_time,
                apply_flip=False,
                apply_scale=False,
            )
            return px, py

        def _sample_emission_distance_pos(
            entry: ParticleRenderEntry,
            sample_time: float,
        ) -> Tuple[float, float, float]:
            # Rate-over-distance should be driven by the authored emitter
            # transform, not the viewer-only projected sprite-surface source.
            # Using the resolved surface point overcounts travel when the matched
            # layer rotates or changes scale, which can keep heavy emitters like
            # Fireboss artificially saturated even during low-motion periods.
            use_control_source = bool(entry.control_channels and entry.source_layer_id is None)
            channels = entry.control_channels if use_control_source else entry.channels
            offset = entry.control_offset if use_control_source else entry.offset
            prefab_pos = (0.0, 0.0, 0.0) if use_control_source else entry.prefab_pos
            return _sample_authored_node_pos(
                channels,
                sample_time,
                offset=offset,
                prefab_pos=prefab_pos,
                fold_depth=False,
                apply_flip=False,
                apply_scale=False,
            )

        def _get_matched_layer_state(
            layer_id: int,
            sample_time: float,
        ) -> Optional[Dict[str, Any]]:
            if layer_id is None or not self.player or not animation_layer_map:
                return None
            time_key = int(round(sample_time * 240.0))
            cache_key = (layer_id, time_key)
            state = matched_layer_state_cache.get(cache_key)
            if state is None:
                layer = animation_layer_map.get(layer_id)
                if not layer:
                    matched_layer_state_cache[cache_key] = None
                    return None
                time_state_cache: Dict[int, Dict[str, Any]] = {}
                state = self.renderer.calculate_world_state(
                    layer,
                    sample_time,
                    self.player,
                    animation_layer_map,
                    time_state_cache,
                    self.texture_atlases,
                    self.layer_atlas_overrides,
                    self.layer_pivot_context,
                )
                if state:
                    state = self.apply_user_transforms(layer_id, dict(state))
                matched_layer_state_cache[cache_key] = state
            return state

        def _get_atlas_alpha_pixels(atlas: TextureAtlas) -> Optional[np.ndarray]:
            image_path = getattr(atlas, "image_path", "") or ""
            if not image_path or Image is None:
                return None
            try:
                mtime = float(os.path.getmtime(image_path))
            except OSError:
                mtime = 0.0
            cache_key = (image_path, mtime)
            cached_alpha = self._particle_atlas_alpha_cache.get(cache_key)
            if cached_alpha is not None:
                return cached_alpha
            try:
                atlas_image = atlas._load_texture_image(image_path).convert("RGBA")
                alpha_pixels = np.asarray(atlas_image, dtype=np.uint8)[:, :, 3]
            except Exception:
                alpha_pixels = None
            self._particle_atlas_alpha_cache[cache_key] = alpha_pixels
            return alpha_pixels

        def _sample_local_sprite_alpha(
            sprite,
            atlas: TextureAtlas,
            local_vertices: List[Tuple[float, float]],
            local_x: float,
            local_y: float,
        ) -> float:
            alpha_pixels = _get_atlas_alpha_pixels(atlas)
            if alpha_pixels is None or atlas.image_width <= 0 or atlas.image_height <= 0:
                return 0.0
            tex_u: Optional[float] = None
            tex_v: Optional[float] = None
            if sprite.has_polygon_mesh:
                geometry = self.renderer._build_polygon_geometry(sprite, atlas)
                if geometry:
                    vertices, texcoords, triangles = geometry
                    vert_count = len(vertices)
                    for tri_idx in range(0, len(triangles), 3):
                        idx0 = triangles[tri_idx]
                        idx1 = triangles[tri_idx + 1] if tri_idx + 1 < len(triangles) else None
                        idx2 = triangles[tri_idx + 2] if tri_idx + 2 < len(triangles) else None
                        if idx1 is None or idx2 is None:
                            break
                        if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                            continue
                        v0 = vertices[idx0]
                        v1 = vertices[idx1]
                        v2 = vertices[idx2]
                        if not self.renderer._point_in_triangle(local_x, local_y, v0, v1, v2):
                            continue
                        x0, y0 = v0
                        x1, y1 = v1
                        x2, y2 = v2
                        denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
                        if abs(denom) < 1e-8:
                            continue
                        w0 = ((y1 - y2) * (local_x - x2) + (x2 - x1) * (local_y - y2)) / denom
                        w1 = ((y2 - y0) * (local_x - x2) + (x0 - x2) * (local_y - y2)) / denom
                        w2 = 1.0 - w0 - w1
                        uv0 = texcoords[idx0]
                        uv1 = texcoords[idx1]
                        uv2 = texcoords[idx2]
                        tex_u = uv0[0] * w0 + uv1[0] * w1 + uv2[0] * w2
                        tex_v = uv0[1] * w0 + uv1[1] * w1 + uv2[1] * w2
                        break
            else:
                if len(local_vertices) < 4:
                    return 0.0
                left = float(local_vertices[0][0])
                top = float(local_vertices[0][1])
                right = float(local_vertices[2][0])
                bottom = float(local_vertices[2][1])
                width = right - left
                height = bottom - top
                if width <= 1e-6 or height <= 1e-6:
                    return 0.0
                u_local = (local_x - left) / width
                v_local = (local_y - top) / height
                if u_local < 0.0 or u_local > 1.0 or v_local < 0.0 or v_local > 1.0:
                    return 0.0
                tx1 = sprite.x / float(atlas.image_width)
                ty1 = sprite.y / float(atlas.image_height)
                tx2 = (sprite.x + sprite.w) / float(atlas.image_width)
                ty2 = (sprite.y + sprite.h) / float(atlas.image_height)
                if sprite.rotated:
                    tex_u = tx2 + u_local * (tx1 - tx2)
                    tex_v = ty1 + v_local * (ty2 - ty1)
                else:
                    tex_u = tx1 + u_local * (tx2 - tx1)
                    tex_v = ty1 + v_local * (ty2 - ty1)
            if tex_u is None or tex_v is None:
                return 0.0
            px = int(round(tex_u * max(0, atlas.image_width - 1)))
            py = int(round(tex_v * max(0, atlas.image_height - 1)))
            px = max(0, min(alpha_pixels.shape[1] - 1, px))
            py = max(0, min(alpha_pixels.shape[0] - 1, py))
            return float(alpha_pixels[py, px]) / 255.0

        def _project_point_to_visible_alpha(
            sprite,
            atlas: TextureAtlas,
            local_vertices: List[Tuple[float, float]],
            start_x: float,
            start_y: float,
            direction: Tuple[float, float],
        ) -> Optional[Tuple[float, float]]:
            alpha_threshold = 8.0 / 255.0
            start_alpha = _sample_local_sprite_alpha(sprite, atlas, local_vertices, start_x, start_y)
            if start_alpha >= alpha_threshold:
                return (start_x, start_y)
            dir_len = math.hypot(direction[0], direction[1])
            if dir_len <= 1e-6:
                return None
            dir_x = direction[0] / dir_len
            dir_y = direction[1] / dir_len
            xs = [value[0] for value in local_vertices]
            ys = [value[1] for value in local_vertices]
            max_distance = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) + 8.0
            step = max(
                0.5,
                self.renderer._get_hires_scale(atlas) * self.renderer.position_scale * 0.75,
            )
            distance = step
            while distance <= max_distance:
                test_x = start_x + dir_x * distance
                test_y = start_y + dir_y * distance
                alpha_value = _sample_local_sprite_alpha(sprite, atlas, local_vertices, test_x, test_y)
                if alpha_value >= alpha_threshold:
                    low = max(0.0, distance - step)
                    high = distance
                    for _ in range(6):
                        mid = (low + high) * 0.5
                        mid_x = start_x + dir_x * mid
                        mid_y = start_y + dir_y * mid
                        if _sample_local_sprite_alpha(sprite, atlas, local_vertices, mid_x, mid_y) >= alpha_threshold:
                            high = mid
                        else:
                            low = mid
                    return (start_x + dir_x * high, start_y + dir_y * high)
                distance += step
            return None

        def _sample_matched_layer_surface(
            entry: ParticleRenderEntry,
            sample_time: float,
        ) -> Optional[Tuple[float, float]]:
            layer_id = getattr(entry, "source_layer_id", None)
            direction = getattr(entry, "source_surface_direction", None)
            local_offset = getattr(entry, "source_layer_offset_local", None)
            local_offset_std = getattr(entry, "source_layer_offset_std", None)
            if layer_id is None or not self.player or not animation_layer_map:
                return None
            time_key = int(round(sample_time * 240.0))
            cache_key = (layer_id, time_key)
            if cache_key in matched_layer_point_cache:
                return matched_layer_point_cache[cache_key]
            layer = animation_layer_map.get(layer_id)
            if not layer:
                matched_layer_point_cache[cache_key] = None
                return None
            state = _get_matched_layer_state(layer_id, sample_time)
            if not state:
                matched_layer_point_cache[cache_key] = None
                return None

            sprite_name = state.get("sprite_name", "")
            if not sprite_name:
                matched_layer_point_cache[cache_key] = None
                return None

            sprite = None
            atlas = None
            override_atlases = self.layer_atlas_overrides.get(layer_id)
            atlas_chain = (
                list(override_atlases) + self.texture_atlases
                if override_atlases
                else self.texture_atlases
            )
            for atl in atlas_chain:
                sprite = atl.get_sprite(sprite_name)
                if sprite:
                    atlas = atl
                    break
            if not sprite or not atlas:
                matched_layer_point_cache[cache_key] = None
                return None

            local_vertices = self.renderer.compute_local_vertices(sprite, atlas)
            if not local_vertices:
                matched_layer_point_cache[cache_key] = None
                return None

            anchor_offset = state.get("sprite_anchor_offset")
            if anchor_offset:
                anchor_dx = anchor_offset[0] * self.renderer.base_world_scale * self.renderer.position_scale
                anchor_dy = anchor_offset[1] * self.renderer.base_world_scale * self.renderer.position_scale
                local_vertices = [(lx + anchor_dx, ly + anchor_dy) for lx, ly in local_vertices]
            else:
                anchor_dx = 0.0
                anchor_dy = 0.0

            local_xs = [lx for lx, _ly in local_vertices]
            local_ys = [ly for _lx, ly in local_vertices]
            min_x = min(local_xs)
            max_x = max(local_xs)
            min_y = min(local_ys)
            max_y = max(local_ys)
            anchor_local_x = (
                layer.anchor_x * self.renderer.base_world_scale * self.renderer.position_scale
            ) + anchor_dx
            anchor_local_y = (
                layer.anchor_y * self.renderer.base_world_scale * self.renderer.position_scale
            ) + anchor_dy

            point_local_x: float
            point_local_y: float
            if direction:
                if not local_offset:
                    matched_layer_point_cache[cache_key] = None
                    return None
                offset_x = float(local_offset[0])
                offset_y = float(local_offset[1])
                authored_local_x = anchor_local_x + offset_x
                authored_local_y = anchor_local_y + offset_y
                local_width = max_x - min_x
                local_height = max_y - min_y
                center_lock_threshold = max(4.0, min(local_width, local_height) * 0.18)
                center_stability_threshold = max(1.5, min(local_width, local_height) * 0.08)
                offset_radius = math.hypot(offset_x, offset_y)
                offset_std_radius = (
                    math.hypot(float(local_offset_std[0]), float(local_offset_std[1]))
                    if local_offset_std is not None
                    else 0.0
                )
                if (
                    offset_radius <= center_lock_threshold
                    and offset_std_radius <= center_stability_threshold
                ):
                    point_local_x = anchor_local_x
                    point_local_y = anchor_local_y
                else:
                    alpha_projected = _project_point_to_visible_alpha(
                        sprite,
                        atlas,
                        local_vertices,
                        authored_local_x,
                        authored_local_y,
                        direction,
                    )
                    if alpha_projected is not None:
                        point_local_x, point_local_y = alpha_projected
                    else:
                        # Fallback to quad/mesh bounds if the atlas image is unavailable or
                        # the ray never intersects visible alpha.
                        if abs(offset_y) >= abs(offset_x):
                            point_local_x = min(max(authored_local_x, min_x), max_x)
                            point_local_y = min_y if offset_y >= 0.0 else max_y
                        else:
                            point_local_x = min_x if offset_x >= 0.0 else max_x
                            point_local_y = min(max(authored_local_y, min_y), max_y)
            else:
                matched_layer_point_cache[cache_key] = None
                return None
            offset_x, offset_y = self.layer_offsets.get(layer_id, (0.0, 0.0))
            source_x = (
                state["m00"] * point_local_x
                + state["m01"] * point_local_y
                + state["tx"]
                + offset_x
            )
            source_y = (
                state["m10"] * point_local_x
                + state["m11"] * point_local_y
                + state["ty"]
                + offset_y
            )
            matched_layer_point_cache[cache_key] = (source_x, source_y)
            return matched_layer_point_cache[cache_key]

        debug_entry_name: Optional[str] = None
        debug_sample_limit = 8

        def _get_particle_emission_schedule(
            entry: ParticleRenderEntry,
            duration: float,
            base_rate_time: float,
            base_rate_distance: float,
        ) -> List[float]:
            if duration <= 0.0 or (base_rate_time <= 0.0 and base_rate_distance <= 0.0):
                return []
            keys = entry.channels.get(12, [])
            cache_key = (
                entry.name,
                "combined",
                base_rate_time,
                base_rate_distance,
                duration,
                tuple(keys),
                tuple(entry.channels.get(0, [])),
                tuple(entry.channels.get(1, [])),
                tuple(entry.channels.get(2, [])),
                entry.source_layer_id,
                entry.control_name,
            )
            cached = self._particle_emission_cache.get(cache_key)
            if cached is not None:
                return cached
            dt = 1.0 / 60.0
            total_time = max(duration, 0.0)
            times: List[float] = []
            t = 0.0
            cumulative = 0.0
            prev_x, prev_y, prev_z = _sample_emission_distance_pos(entry, 0.0)
            while t < total_time - 1e-6:
                t_next = min(total_time, t + dt)
                if t_next <= t:
                    break
                rate_scale = self._eval_particle_channel(
                    keys,
                    t,
                    1.0,
                    allow_linear_keytype1=True,
                )
                try:
                    rate_scale = float(rate_scale)
                except Exception:
                    rate_scale = 1.0
                if rate_scale < 0.0:
                    rate_scale = 0.0
                next_x, next_y, next_z = _sample_emission_distance_pos(entry, t_next)
                travel = math.sqrt(
                    (next_x - prev_x) * (next_x - prev_x)
                    + (next_y - prev_y) * (next_y - prev_y)
                    + (next_z - prev_z) * (next_z - prev_z)
                )
                cumulative_next = cumulative
                cumulative_next += base_rate_time * rate_scale * (t_next - t)
                cumulative_next += base_rate_distance * rate_scale * travel
                if cumulative_next > cumulative:
                    start_int = int(math.floor(cumulative))
                    end_int = int(math.floor(cumulative_next))
                    if end_int > start_int:
                        span = cumulative_next - cumulative
                        step_dt = t_next - t
                        for count in range(start_int + 1, end_int + 1):
                            frac = (count - cumulative) / span if span > 0.0 else 0.0
                            emit_time = t + frac * step_dt
                            times.append(emit_time)
                cumulative = cumulative_next
                prev_x, prev_y, prev_z = next_x, next_y, next_z
                t = t_next
                if len(times) > 50000:
                    break
            self._particle_emission_cache[cache_key] = times
            return times

        def _resolve_active_particle_pool(
            entry: ParticleRenderEntry,
            emit_times: Optional[List[float]],
            emission_rate: float,
            max_life_window: float,
            pool_cap: int,
        ) -> List[Tuple[int, float, float]]:
            if pool_cap <= 0:
                return []
            if emit_times is not None:
                window_start = max(0.0, time_value - max_life_window)
                range_start = bisect.bisect_left(emit_times, window_start)
                range_stop = bisect.bisect_right(emit_times, time_value)
                raw_indices = range(range_start, range_stop)
            else:
                if emission_rate <= 0.0:
                    return []
                window_start = max(0.0, time_value - max_life_window)
                range_start = max(0, int(math.floor(window_start * emission_rate)) - 2)
                range_stop = int(math.floor(time_value * emission_rate)) + 2
                raw_indices = range(range_start, range_stop)
            expire_epsilon = 1e-6
            active_candidates: List[Tuple[int, float, float]] = []

            for idx in raw_indices:
                sample = _get_particle_sample(entry, idx)
                jitter = sample[0]
                life = sample[1]
                if life <= 0.0:
                    continue
                if emit_times is not None:
                    emit_time = emit_times[idx]
                else:
                    emit_time = (idx + jitter) / emission_rate
                if emit_time > time_value + expire_epsilon:
                    break

                age = time_value - emit_time
                if age < -expire_epsilon or age > life + expire_epsilon:
                    continue
                active_candidates.append((idx, emit_time, life))

            count = len(active_candidates)
            if count <= pool_cap:
                return active_candidates

            if pool_cap == 1:
                return [active_candidates[-1]]

            selected_positions: List[int] = []
            last_pos = -1
            for slot_idx in range(pool_cap):
                pos = int(round(slot_idx * (count - 1) / float(pool_cap - 1)))
                pos = max(0, min(count - 1, pos))
                if pos <= last_pos:
                    pos = min(count - 1, last_pos + 1)
                if pos >= count:
                    break
                selected_positions.append(pos)
                last_pos = pos

            return [active_candidates[pos] for pos in selected_positions]

        def _get_particle_sample(
            entry: ParticleRenderEntry,
            idx: int,
        ) -> Tuple[Any, ...]:
            cache_key = (entry.seed_base, idx)
            cached = self._particle_sample_cache.get(cache_key)
            if cached is not None:
                return cached

            seed = entry.seed_base + idx * 1315423911
            jitter = self._rand01(seed + 0.1)
            life = self._rand_range(seed + 0.2, entry.lifetime_range[0], entry.lifetime_range[1])
            speed = self._rand_range(seed + 0.3, entry.speed_range[0], entry.speed_range[1])
            size = self._rand_range(seed + 0.4, entry.size_range[0], entry.size_range[1])
            if entry.size_y_range:
                size_y = self._rand_range(seed + 0.5, entry.size_y_range[0], entry.size_y_range[1])
            else:
                size_y = size

            col_min, col_max = entry.color_range
            color_r = self._rand_range(seed + 0.6, col_min[0], col_max[0])
            color_g = self._rand_range(seed + 0.7, col_min[1], col_max[1])
            color_b = self._rand_range(seed + 0.8, col_min[2], col_max[2])
            color_a = self._rand_range(seed + 0.9, col_min[3], col_max[3])

            shape_angle = entry.shape_angle or 360.0
            shape_radius = entry.shape_radius
            shape_type = entry.shape_type
            shape_placement_mode = int(getattr(entry, "shape_placement_mode", 0) or 0)
            radius_thickness = max(
                0.0, min(1.0, float(getattr(entry, "shape_radius_thickness", 1.0) or 1.0))
            )
            local_spawn_x = 0.0
            local_spawn_y = 0.0
            local_dir_x = 0.0
            local_dir_y = 1.0
            shape_handled_3d = False
            shape_unit_scale = base_scale

            if shape_type in (4, 2, 3):  # cone-like
                shape_handled_3d = True
                half_angle = math.radians(shape_angle * 0.5)
                azimuth = self._rand_range(seed + 1.1, 0.0, math.pi * 2.0)
                inner_radius = shape_radius * (1.0 - radius_thickness)
                if radius_thickness <= 1e-6:
                    radial_u = 1.0
                else:
                    radial_u = math.sqrt(
                        self._rand_range(
                            seed + 1.2,
                            inner_radius * inner_radius,
                            shape_radius * shape_radius,
                        )
                    ) / max(shape_radius, 1e-6)
                axial = 0.0
                if shape_placement_mode in (1, 3) and entry.shape_length:
                    axial = self._rand_range(seed + 1.25, 0.0, entry.shape_length) * shape_unit_scale
                shell_radius = shape_radius
                if shape_placement_mode in (0, 1):
                    shell_radius = inner_radius + (shape_radius - inner_radius) * radial_u
                spawn_radius = shell_radius * shape_unit_scale
                if shape_placement_mode in (1, 3) and half_angle > 1e-6 and axial > 0.0:
                    spawn_radius += axial * math.tan(half_angle)
                local_spawn_x = math.cos(azimuth) * spawn_radius
                local_spawn_y = math.sin(azimuth) * spawn_radius
                local_spawn_z = axial
                local_dir_theta = self._rand_range(seed + 1.3, 0.0, half_angle) if half_angle > 1e-6 else 0.0
                local_dir3_x = math.cos(azimuth) * math.sin(local_dir_theta)
                local_dir3_y = math.sin(azimuth) * math.sin(local_dir_theta)
                local_dir3_z = math.cos(local_dir_theta)
                shape_scale_x = entry.shape_scale[0] if entry.shape_scale else 1.0
                shape_scale_y = entry.shape_scale[1] if entry.shape_scale else 1.0
                shape_scale_z = entry.shape_scale[2] if entry.shape_scale else 1.0
                local_spawn_x *= shape_scale_x
                local_spawn_y *= shape_scale_y
                local_spawn_z *= shape_scale_z
                local_dir3_x *= shape_scale_x
                local_dir3_y *= shape_scale_y
                local_dir3_z *= shape_scale_z
                if entry.shape_position:
                    local_spawn_x += entry.shape_position[0] * shape_unit_scale
                    local_spawn_y += entry.shape_position[1] * shape_unit_scale
                    local_spawn_z += entry.shape_position[2] * shape_unit_scale
                local_spawn_x, local_spawn_y = project_prefab_vec_to_plane(
                    local_spawn_x,
                    local_spawn_y,
                    local_spawn_z,
                    entry,
                )
                local_dir_x, local_dir_y = project_prefab_vec_to_plane(
                    local_dir3_x,
                    local_dir3_y,
                    local_dir3_z,
                    entry,
                )
                dir_len = math.hypot(local_dir_x, local_dir_y)
                if dir_len > 1e-6:
                    local_dir_x /= dir_len
                    local_dir_y /= dir_len
                else:
                    local_dir_x = 0.0
                    local_dir_y = 1.0
            else:
                spawn_angle = self._rand_range(seed + 1.1, 0.0, math.pi * 2.0)
                if shape_type in (0, 1):  # sphere/hemisphere
                    spawn_radius = self._rand_range(seed + 1.2, 0.0, shape_radius) * shape_unit_scale
                    local_spawn_x = math.cos(spawn_angle) * spawn_radius
                    local_spawn_y = math.sin(spawn_angle) * spawn_radius
                elif shape_type in (5, 6):  # box-like
                    box_x, box_y = entry.shape_box
                    local_spawn_x = self._rand_range(seed + 1.1, -box_x * 0.5, box_x * 0.5) * shape_unit_scale
                    local_spawn_y = self._rand_range(seed + 1.2, -box_y * 0.5, box_y * 0.5) * shape_unit_scale
                    spawn_angle = self._rand_range(seed + 1.3, 0.0, math.pi * 2.0)
                local_dir_x = math.cos(spawn_angle)
                local_dir_y = math.sin(spawn_angle)

            if not shape_handled_3d:
                shape_scale_x = entry.shape_scale[0] if entry.shape_scale else 1.0
                shape_scale_y = entry.shape_scale[1] if entry.shape_scale else 1.0
                local_spawn_x *= shape_scale_x
                local_spawn_y *= shape_scale_y
                local_dir_x *= shape_scale_x
                local_dir_y *= shape_scale_y
                shape_rot = 0.0
                if entry.shape_rotation:
                    shape_rot = math.radians(entry.shape_rotation[2])
                if abs(shape_rot) > 1e-6:
                    rot_spawn_x = local_spawn_x * math.cos(shape_rot) - local_spawn_y * math.sin(shape_rot)
                    rot_spawn_y = local_spawn_x * math.sin(shape_rot) + local_spawn_y * math.cos(shape_rot)
                    local_spawn_x, local_spawn_y = rot_spawn_x, rot_spawn_y
                    rot_dir_x = local_dir_x * math.cos(shape_rot) - local_dir_y * math.sin(shape_rot)
                    rot_dir_y = local_dir_x * math.sin(shape_rot) + local_dir_y * math.cos(shape_rot)
                    local_dir_x, local_dir_y = rot_dir_x, rot_dir_y
                if entry.shape_position:
                    local_spawn_x += entry.shape_position[0] * shape_unit_scale
                    local_spawn_y += entry.shape_position[1] * shape_unit_scale

            sample = (
                jitter,
                life,
                speed,
                size,
                size_y,
                color_r,
                color_g,
                color_b,
                color_a,
                self._rand_range(seed + 1.4, entry.velocity_over_lifetime_range[0][0], entry.velocity_over_lifetime_range[0][1]),
                self._rand_range(seed + 1.5, entry.velocity_over_lifetime_range[1][0], entry.velocity_over_lifetime_range[1][1]),
                self._rand_range(seed + 1.55, entry.start_rotation_range[0], entry.start_rotation_range[1]),
                self._rand_range(seed + 1.6, entry.gravity_modifier_range[0], entry.gravity_modifier_range[1]),
                self._rand_range(seed + 1.65, entry.rotation_over_lifetime_range[0], entry.rotation_over_lifetime_range[1]),
                local_spawn_x,
                local_spawn_y,
                local_dir_x,
                local_dir_y,
            )
            self._particle_sample_cache[cache_key] = sample
            return sample

        runtime_signature = (
            id(animation) if animation is not None else 0,
            tuple(
                (
                    entry.seed_base,
                    entry.name,
                    entry.source_layer_id,
                    entry.control_name,
                    round(float(entry.offset[0]), 4),
                    round(float(entry.offset[1]), 4),
                    round(float(entry.offset[2]), 4),
                )
                for entry in self.particle_entries
            ),
            bool(self._particle_flip_y),
            bool(self.particle_force_world_space),
            round(float(self.particle_origin_offset_x), 4),
            round(float(self.particle_origin_offset_y), 4),
            round(float(self.particle_distance_sensitivity), 4),
        )

        if (
            self._particle_runtime_signature != runtime_signature
            or time_value < self._particle_runtime_time - 1e-6
        ):
            self._particle_runtime_signature = runtime_signature
            self._particle_runtime_time = 0.0
            self._particle_runtime_states = {}
            for entry in self.particle_entries:
                self._particle_runtime_states[entry.seed_base] = ParticleEmitterRuntimeState(
                    prev_source_pos=_sample_emission_distance_pos(entry, 0.0),
                )

        sim_dt = 1.0 / 60.0
        sim_time = self._particle_runtime_time
        while sim_time < time_value - 1e-6:
            t_next = min(time_value, sim_time + sim_dt)
            step_dt = max(0.0, t_next - sim_time)
            for entry in self.particle_entries:
                state = self._particle_runtime_states.setdefault(
                    entry.seed_base,
                    ParticleEmitterRuntimeState(
                        prev_source_pos=_sample_emission_distance_pos(entry, sim_time),
                    ),
                )
                if state.prev_source_pos is None:
                    state.prev_source_pos = _sample_emission_distance_pos(entry, sim_time)

                rate_min, rate_max = entry.emission_rate_range
                emission_rate = max(0.0, (rate_min + rate_max) * 0.5)
                distance_rate_min, distance_rate_max = entry.emission_distance_range
                emission_distance_rate = max(0.0, (distance_rate_min + distance_rate_max) * 0.5)
                emission_keys = entry.channels.get(12, [])
                emission_scale = self._eval_particle_channel(
                    emission_keys,
                    sim_time,
                    1.0,
                    allow_linear_keytype1=True,
                )
                try:
                    emission_scale = float(emission_scale)
                except Exception:
                    emission_scale = 1.0
                if emission_scale < 0.0:
                    emission_scale = 0.0

                next_pos = _sample_emission_distance_pos(entry, t_next)
                prev_x, prev_y, prev_z = state.prev_source_pos
                next_x, next_y, next_z = next_pos
                travel = math.sqrt(
                    (next_x - prev_x) * (next_x - prev_x)
                    + (next_y - prev_y) * (next_y - prev_y)
                    + (next_z - prev_z) * (next_z - prev_z)
                )
                # Scale authored DOF emitter travel into the viewer's effective
                # rate-over-distance sensitivity.
                travel *= self.particle_distance_sensitivity
                budget_delta = (
                    emission_rate * emission_scale * step_dt
                    + emission_distance_rate * emission_scale * travel
                )
                budget_before = state.spawn_budget
                budget_after = budget_before + budget_delta
                if budget_after > budget_before + 1e-9:
                    start_int = int(math.floor(budget_before))
                    end_int = int(math.floor(budget_after))
                    if end_int > start_int:
                        span = budget_after - budget_before
                        for count in range(start_int + 1, end_int + 1):
                            frac = (count - budget_before) / span if span > 1e-9 else 1.0
                            emit_time = sim_time + frac * step_dt
                            sample = _get_particle_sample(entry, state.next_idx)
                            life = float(sample[1])
                            if life > 0.0:
                                state.active.append(
                                    ParticleActiveState(
                                        idx=state.next_idx,
                                        emit_time=emit_time,
                                        life=life,
                                    )
                                )
                            state.next_idx += 1
                state.spawn_budget = budget_after - math.floor(budget_after)
                state.prev_source_pos = next_pos
                cutoff = t_next - 1e-6
                if state.active:
                    state.active = [
                        particle
                        for particle in state.active
                        if (particle.emit_time + particle.life) > cutoff
                    ]
            sim_time = t_next

        self._particle_runtime_time = time_value

        for entry in self.particle_entries:
            if not entry.texture_id:
                continue
            state = self._particle_runtime_states.get(entry.seed_base)
            if state is None:
                continue
            rate_min, rate_max = entry.emission_rate_range
            emission_rate = max(0.0, (rate_min + rate_max) * 0.5)
            distance_rate_min, distance_rate_max = entry.emission_distance_range
            emission_distance_rate = max(0.0, (distance_rate_min + distance_rate_max) * 0.5)
            emission_keys = entry.channels.get(12, [])
            emission_scale = self._eval_particle_channel(
                emission_keys,
                time_value,
                1.0,
                allow_linear_keytype1=False,
            )
            try:
                emission_scale = float(emission_scale)
            except Exception:
                emission_scale = 1.0
            if emission_scale < 0.0:
                emission_scale = 0.0
            emission_rate *= emission_scale
            emission_distance_rate *= emission_scale
            if not state.active and emission_rate <= 0.0 and emission_distance_rate <= 0.0:
                continue
            emitter_particle_cap = min(max_particles_per_emitter, adaptive_budget_per_emitter)
            active_particles: List[Tuple[int, float, float]] = [
                (particle.idx, particle.emit_time, particle.life)
                for particle in state.active
            ]
            active_count = len(active_particles)
            if active_count <= 0:
                continue
            if max_total_particles > 0 and active_count > emitter_particle_cap:
                if emitter_particle_cap == 1:
                    active_particles = [active_particles[-1]]
                else:
                    selected_positions: List[int] = []
                    last_pos = -1
                    for slot_idx in range(emitter_particle_cap):
                        pos = int(round(slot_idx * (active_count - 1) / float(emitter_particle_cap - 1)))
                        pos = max(0, min(active_count - 1, pos))
                        if pos <= last_pos:
                            pos = min(active_count - 1, last_pos + 1)
                        if pos >= active_count:
                            break
                        selected_positions.append(pos)
                        last_pos = pos
                    active_particles = [active_particles[pos] for pos in selected_positions]
            life_min, life_max = entry.lifetime_range
            lifetime = max(0.01, (life_min + life_max) * 0.5)

            if debug_entry_name is None:
                debug_entry_name = entry.name
                self._particle_debug_info = {
                    "emitter_name": entry.name,
                    "control_name": entry.control_name,
                    "source_layer_name": entry.source_layer_name,
                    "source_layer_offset_local": entry.source_layer_offset_local,
                    "source_layer_offset_std": entry.source_layer_offset_std,
                    "source_surface_direction": entry.source_surface_direction,
                    "source_mode": "layer_surface_projection" if entry.source_layer_id is not None else "raw_node",
                    "shape_type": entry.shape_type,
                    "shape_placement_mode": entry.shape_placement_mode,
                    "shape_angle": entry.shape_angle,
                    "shape_radius": entry.shape_radius,
                    "shape_radius_thickness": entry.shape_radius_thickness,
                    "shape_length": entry.shape_length,
                    "simulation_space": int(entry.simulation_space or 0),
                    "move_with_transform": bool(entry.move_with_transform),
                    "velocity_module_enabled": bool(entry.velocity_module_enabled),
                    "velocity_world_space": bool(entry.velocity_in_world_space),
                    "emitter_velocity_mode": int(entry.emitter_velocity_mode or 0),
                    "emission_rate": emission_rate,
                    "emission_distance_rate": emission_distance_rate,
                    "lifetime": lifetime,
                    "runtime_active_particles": int(len(state.active)),
                    "control_local_offset": entry.control_local_offset,
                    "control_local_offset_std": entry.control_local_offset_std,
                    "particle_origin_offset": (
                        float(self.particle_origin_offset_x),
                        float(self.particle_origin_offset_y),
                    ),
                    "particle_distance_sensitivity": float(self.particle_distance_sensitivity),
                    "viewport_particle_cap": int(max_total_particles),
                    "emitter_particle_cap": int(emitter_particle_cap),
                }

            matched_layer_depth = None
            if entry.source_layer_id is not None:
                matched_source_state = _get_matched_layer_state(entry.source_layer_id, time_value)
                if matched_source_state:
                    try:
                        matched_layer_depth = float(matched_source_state.get("depth", 0.0))
                    except Exception:
                        matched_layer_depth = None
            raw_depth = self._eval_particle_channel(entry.channels.get(2, []), time_value, 0.0)
            if entry.offset:
                raw_depth += entry.offset[2]
            use_authoritative_depth = bool(
                matched_layer_depth is not None and raw_depth > (matched_layer_depth + 1.0)
            )
            if use_authoritative_depth:
                depth = raw_depth
            elif matched_layer_depth is not None:
                depth = matched_layer_depth + 1e-4
            else:
                depth = raw_depth

            sim_space = int(entry.simulation_space or 0)
            sim_world = sim_space == 1
            sim_custom = sim_space == 2
            if self.particle_force_world_space:
                sim_world = True
                sim_custom = False
            pos_x_now, pos_y_now, pos_z_now = _sample_emitter_origin(
                entry,
                time_value,
                apply_flip=True,
                apply_scale=True,
            )
            if debug_entry_name == entry.name:
                self._particle_debug_info["current_source"] = (pos_x_now, pos_y_now)
            scale_x_now = self._eval_particle_channel(entry.channels.get(3, []), time_value, 1.0)
            scale_y_now = self._eval_particle_channel(entry.channels.get(4, []), time_value, 1.0)
            image_scale = entry.image_scale if entry.image_scale not in (0.0, 0) else 1.0
            scale_x_now *= image_scale * self.renderer.scale_bias_x
            scale_y_now *= image_scale * self.renderer.scale_bias_y

            rotation_now = self._eval_particle_channel(entry.channels.get(5, []), time_value, 0.0)
            rot_deg_now = rotation_now * (180.0 / math.pi) + self.renderer.rotation_bias
            if flip_y:
                rot_deg_now = -rot_deg_now

            opacity = self._eval_particle_channel(entry.channels.get(11, []), time_value, 100.0)
            opacity = max(0.0, min(1.0, float(opacity) / 100.0))
            r = self._eval_particle_channel(entry.channels.get(8, []), time_value, 1.0)
            g = self._eval_particle_channel(entry.channels.get(9, []), time_value, 1.0)
            b = self._eval_particle_channel(entry.channels.get(10, []), time_value, 1.0)
            r = max(0.0, min(1.0, r))
            g = max(0.0, min(1.0, g))
            b = max(0.0, min(1.0, b))
            mat_r, mat_g, mat_b, mat_a = entry.material_color

            for idx, emit_time, life in active_particles:
                (
                    jitter,
                    _life_sample,
                    speed,
                    size,
                    size_y,
                    color_r,
                    color_g,
                    color_b,
                    color_a,
                    vel_x,
                    vel_y,
                    start_rot,
                    grav,
                    rot_speed,
                    cached_spawn_x,
                    cached_spawn_y,
                    cached_dir_x,
                    cached_dir_y,
                ) = _get_particle_sample(entry, idx)
                age = time_value - emit_time
                if age < 0.0:
                    continue
                if age > life or life <= 0.0:
                    continue

                life_t = age / life if life > 1e-6 else 0.0
                life_t = max(0.0, min(1.0, life_t))
                if entry.color_over_lifetime_keys:
                    life_r, life_g, life_b = _eval_color_keys(entry.color_over_lifetime_keys, life_t)
                    color_r *= life_r
                    color_g *= life_g
                    color_b *= life_b
                if entry.alpha_over_lifetime_keys:
                    color_a *= _eval_alpha_keys(entry.alpha_over_lifetime_keys, life_t)
                if entry.size_over_lifetime_keys:
                    size *= _eval_curve_keys(entry.size_over_lifetime_keys, life_t)
                    if not entry.size_over_lifetime_y_keys:
                        size_y *= _eval_curve_keys(entry.size_over_lifetime_keys, life_t)
                if entry.size_over_lifetime_y_keys:
                    size_y *= _eval_curve_keys(entry.size_over_lifetime_y_keys, life_t)

                # Determine emitter transform based on simulation space.
                # For controlpoint-driven DOF emitters, lock the source transform to
                # the particle's emit-time so the socket can follow the monster between
                # emissions without dragging previously-emitted particles around.
                source_locked_to_emit_time = bool(entry.control_channels)
                use_transform = bool(
                    entry.move_with_transform
                    and not sim_world
                    and not source_locked_to_emit_time
                )
                origin_sample_time = time_value
                if use_transform:
                    pos_x = pos_x_now
                    pos_y = pos_y_now
                    scale_x = scale_x_now
                    scale_y = scale_y_now
                    rot_deg = rot_deg_now
                else:
                    sample_time = emit_time if (sim_world or source_locked_to_emit_time) else time_value
                    origin_sample_time = sample_time
                    pos_x, pos_y, pos_z = _sample_emitter_origin(
                        entry,
                        sample_time,
                        apply_flip=True,
                        apply_scale=True,
                    )
                    scale_x = self._eval_particle_channel(entry.channels.get(3, []), sample_time, 1.0)
                    scale_y = self._eval_particle_channel(entry.channels.get(4, []), sample_time, 1.0)
                    scale_x *= image_scale * self.renderer.scale_bias_x
                    scale_y *= image_scale * self.renderer.scale_bias_y
                    rotation = self._eval_particle_channel(entry.channels.get(5, []), sample_time, 0.0)
                    rot_deg = rotation * (180.0 / math.pi) + self.renderer.rotation_bias
                    if flip_y:
                        rot_deg = -rot_deg

                particle_rot_deg = rot_deg
                start_rot_min, start_rot_max = entry.start_rotation_range
                if start_rot_min or start_rot_max:
                    start_rot_deg = start_rot * (180.0 / math.pi)
                    if flip_y:
                        start_rot_deg = -start_rot_deg
                    particle_rot_deg += start_rot_deg

                rot_speed_min, rot_speed_max = entry.rotation_over_lifetime_range
                if rot_speed_min or rot_speed_max or entry.rotation_over_lifetime_keys:
                    if entry.rotation_over_lifetime_keys:
                        rot_speed *= _eval_curve_keys(entry.rotation_over_lifetime_keys, life_t)
                    particle_rot_deg += rot_speed * age

                rot_rad = math.radians(rot_deg)
                cos_r = math.cos(rot_rad)
                sin_r = math.sin(rot_rad)

                if sim_custom:
                    pos_x += entry.custom_space_pos[0] * base_scale
                    custom_y = entry.custom_space_pos[1]
                    if flip_y:
                        custom_y = -custom_y
                    pos_y += custom_y * base_scale

                axis_x, axis_y = project_prefab_vec_to_plane(0.0, 0.0, 1.0, entry)
                axis_len = math.hypot(axis_x, axis_y)
                if axis_len > 1e-6:
                    axis_x /= axis_len
                    axis_y /= axis_len
                else:
                    axis_x, axis_y = 0.0, 1.0
                rotated_axis_x = axis_x * cos_r - axis_y * sin_r
                rotated_axis_y = axis_x * sin_r + axis_y * cos_r

                spawn_x = cached_spawn_x
                spawn_y = cached_spawn_y
                dir_x = cached_dir_x
                dir_y = cached_dir_y

                # Apply emitter scale in local space before rotation.
                spawn_x *= scale_x
                spawn_y *= scale_y
                dir_x *= scale_x
                dir_y *= scale_y

                # Apply emitter rotation to spawn and direction
                rotated_spawn_x = spawn_x * cos_r - spawn_y * sin_r
                rotated_spawn_y = spawn_x * sin_r + spawn_y * cos_r
                rotated_dir_x = dir_x * cos_r - dir_y * sin_r
                rotated_dir_y = dir_x * sin_r + dir_y * cos_r

                vel_local_x = rotated_dir_x * speed * unit_scale
                vel_local_y = rotated_dir_y * speed * unit_scale

                vel_curve_x = vel_x * scale_x
                vel_curve_y = vel_y * scale_y
                if entry.velocity_in_world_space:
                    vel_world_x = vel_curve_x * unit_scale
                    vel_world_y = vel_curve_y * unit_scale
                else:
                    vel_curve_rot_x = vel_curve_x * cos_r - vel_curve_y * sin_r
                    vel_curve_rot_y = vel_curve_x * sin_r + vel_curve_y * cos_r
                    vel_local_x += vel_curve_rot_x * unit_scale
                    vel_local_y += vel_curve_rot_y * unit_scale
                    vel_world_x = 0.0
                    vel_world_y = 0.0

                if entry.emitter_velocity_mode == 0 and entry.velocity_module_enabled:
                    dt_vel = 1.0 / 60.0
                    t0 = emit_time - dt_vel
                    if t0 < 0.0:
                        t0 = 0.0
                    ex0, ey0 = _sample_emitter_pos_raw(entry, t0)
                    ex1, ey1 = _sample_emitter_pos_raw(entry, emit_time)
                    emitter_vel_x = (ex1 - ex0) / dt_vel * base_scale
                    emitter_vel_y = (ey1 - ey0) / dt_vel * base_scale
                    if sim_world or entry.velocity_in_world_space:
                        vel_world_x += emitter_vel_x
                        vel_world_y += emitter_vel_y
                    else:
                        if abs(rot_rad) > 1e-6:
                            local_vx = emitter_vel_x * cos_r + emitter_vel_y * sin_r
                            local_vy = -emitter_vel_x * sin_r + emitter_vel_y * cos_r
                        else:
                            local_vx = emitter_vel_x
                            local_vy = emitter_vel_y
                        vel_local_x += local_vx
                        vel_local_y += local_vy

                particle_x = pos_x + rotated_spawn_x + (vel_local_x * age)
                particle_y = pos_y + rotated_spawn_y + (vel_local_y * age)
                if entry.velocity_in_world_space:
                    particle_x += vel_world_x * age
                    particle_y += vel_world_y * age

                grav_x = 0.0
                grav_y = 0.0
                if grav:
                    # Keep particle simulation in the viewer's screen-space convention:
                    # positive Y is downward after the DOF flip has already been applied
                    # to the emitter channels above.
                    grav_y = 9.81 * grav * unit_scale
                    particle_x += 0.5 * grav_x * age * age
                    particle_y += 0.5 * grav_y * age * age

                if debug_entry_name == entry.name and len(self._particle_debug_samples) < debug_sample_limit:
                    sample_origin_x = pos_x
                    sample_origin_y = pos_y
                    sample_spawn_x = pos_x + rotated_spawn_x
                    sample_spawn_y = pos_y + rotated_spawn_y
                    sample_axis_x = rotated_axis_x
                    sample_axis_y = rotated_axis_y
                    sample_vel_x = vel_local_x + vel_world_x
                    sample_vel_y = vel_local_y + vel_world_y
                    sample_grav_x = grav_x
                    sample_grav_y = grav_y
                    sample_particle_x = particle_x
                    sample_particle_y = particle_y
                    sample_control_origin = None
                    sample_derived_socket = None
                    sample_node_xy_origin = None
                    sample_node_alt_depth_origin = None
                    node_raw_x, node_raw_y, node_raw_z = _sample_authored_node_pos(
                        entry.channels,
                        origin_sample_time,
                        offset=entry.offset,
                        prefab_pos=entry.prefab_pos,
                        fold_depth=False,
                        apply_flip=False,
                        apply_scale=False,
                    )
                    node_xy_screen_y = node_raw_y
                    node_alt_depth_screen_y = node_raw_y - node_raw_z
                    if flip_y:
                        node_xy_screen_y = -node_xy_screen_y
                        node_alt_depth_screen_y = -node_alt_depth_screen_y
                    sample_node_xy_origin = (
                        node_raw_x * base_scale + self.renderer.world_offset_x,
                        node_xy_screen_y * base_scale + self.renderer.world_offset_y,
                    )
                    sample_node_alt_depth_origin = (
                        node_raw_x * base_scale + self.renderer.world_offset_x,
                        node_alt_depth_screen_y * base_scale + self.renderer.world_offset_y,
                    )
                    if entry.control_channels:
                        control_raw_x, control_raw_y, control_raw_z = _sample_authored_node_pos(
                            entry.control_channels,
                            origin_sample_time,
                            offset=entry.control_offset,
                            fold_depth=False,
                            apply_flip=False,
                            apply_scale=False,
                        )
                        control_screen_y = control_raw_y - control_raw_z
                        if flip_y:
                            control_screen_y = -control_screen_y
                        sample_control_origin = (
                            control_raw_x * base_scale + self.renderer.world_offset_x,
                            control_screen_y * base_scale + self.renderer.world_offset_y,
                        )
                        if entry.control_local_offset:
                            control_rot = self._eval_particle_channel(
                                entry.control_channels.get(5, []),
                                origin_sample_time,
                                0.0,
                            )
                            cos_c = math.cos(control_rot)
                            sin_c = math.sin(control_rot)
                            local_dx, local_dy = entry.control_local_offset
                            derived_raw_x = control_raw_x + (local_dx * cos_c - local_dy * sin_c)
                            derived_raw_y = control_raw_y + (local_dx * sin_c + local_dy * cos_c)
                            derived_screen_y = derived_raw_y - node_raw_z
                            if flip_y:
                                derived_screen_y = -derived_screen_y
                            sample_derived_socket = (
                                derived_raw_x * base_scale + self.renderer.world_offset_x,
                                derived_screen_y * base_scale + self.renderer.world_offset_y,
                            )
                    self._particle_debug_samples.append(
                        ParticleDebugSample(
                            emitter_name=entry.name,
                            emitter_origin=(sample_origin_x, sample_origin_y),
                            spawn_point=(sample_spawn_x, sample_spawn_y),
                            cone_axis=(sample_axis_x, sample_axis_y),
                            velocity=(sample_vel_x, sample_vel_y),
                            gravity=(sample_grav_x, sample_grav_y),
                            particle_pos=(sample_particle_x, sample_particle_y),
                            age=age,
                            life=life,
                            control_origin=sample_control_origin,
                            derived_socket=sample_derived_socket,
                            node_xy_origin=sample_node_xy_origin,
                            node_alt_depth_origin=sample_node_alt_depth_origin,
                        )
                    )

                out_r = r * mat_r * color_r
                out_g = g * mat_g * color_g
                out_b = b * mat_b * color_b
                out_a = opacity * mat_a * color_a
                out_r *= out_a
                out_g *= out_a
                out_b *= out_a

                width = entry.base_width if entry.base_width > 1e-6 else 1.0
                height = entry.base_height if entry.base_height > 1e-6 else 1.0
                size_px_x = size * scale_x * unit_scale
                size_px_y = size_y * scale_y * unit_scale
                draws.append(
                    ParticleDraw(
                        depth=depth,
                        texture_id=entry.texture_id,
                        blend_mode=entry.blend_mode,
                        color=(out_r, out_g, out_b, out_a),
                        x=particle_x,
                        y=particle_y,
                        rotation=particle_rot_deg,
                        scale_x=size_px_x / width,
                        scale_y=size_px_y / height,
                        base_width=width,
                        base_height=height,
                        authoritative_depth=use_authoritative_depth,
                    )
                )
        if self._particle_debug_info:
            self._particle_debug_info["particle_count"] = len(draws)
            if draws:
                self._particle_debug_info["first_particle"] = (draws[0].x, draws[0].y)
        return draws

    def _render_particle_draw(self, draw: ParticleDraw) -> None:
        glEnable(GL_BLEND)
        glEnable(GL_TEXTURE_2D)
        set_blend_mode(draw.blend_mode)
        glBindTexture(GL_TEXTURE_2D, draw.texture_id)
        glColor4f(*draw.color)
        glPushMatrix()
        glTranslatef(draw.x, draw.y, 0.0)
        if abs(draw.rotation) > 1e-6:
            glRotatef(draw.rotation, 0.0, 0.0, 1.0)
        glScalef(draw.scale_x, draw.scale_y, 1.0)
        half_w = draw.base_width * 0.5
        half_h = draw.base_height * 0.5
        glBegin(GL_QUADS)
        glTexCoord2f(0.0, 0.0)
        glVertex2f(-half_w, -half_h)
        glTexCoord2f(1.0, 0.0)
        glVertex2f(half_w, -half_h)
        glTexCoord2f(1.0, 1.0)
        glVertex2f(half_w, half_h)
        glTexCoord2f(0.0, 1.0)
        glVertex2f(-half_w, half_h)
        glEnd()
        glPopMatrix()

    def _get_render_layers(self, layer_world_states: Dict[int, Dict]) -> List[LayerData]:
        """Return layers ordered for rendering (back to front)."""
        if not self.player.animation:
            return []
        layers = self.player.animation.layers
        _layer_map, order_map, render_layers, has_depth = self._get_animation_layer_cache(
            self.player.animation
        )
        if has_depth:
            # Depth values are evaluated per frame; lower depth renders first (back to front).
            return sorted(
                layers,
                key=lambda layer: (
                    layer_world_states.get(layer.layer_id, {}).get("depth", 0.0),
                    order_map.get(layer.layer_id, 0),
                ),
            )
        # Render all layers in REVERSE order (back to front)
        # In most animation systems, layers are listed from back to front,
        # so we need to render them in reverse to get the correct Z-order
        return render_layers

    def _build_layer_world_states(
        self,
        anim_time: Optional[float] = None,
        *,
        apply_constraints: bool = True,
        propagate_user_transforms: Optional[bool] = None,
    ) -> Dict[int, Dict]:
        """
        Calculate world states for all layers, applying user rotation offsets.
        """
        if not self.player.animation:
            return {}

        if anim_time is None:
            anim_time = self.player.current_time

        layer_map, order_map, _render_layers, _has_depth = self._get_animation_layer_cache(
            self.player.animation
        )
        self._layer_order_map = order_map
        propagate = (
            self.propagate_user_transforms
            if propagate_user_transforms is None
            else bool(propagate_user_transforms)
        )
        layer_world_states: Dict[int, Dict] = {}
        if propagate:
            for layer in self.player.animation.layers:
                state = self.renderer.calculate_world_state(
                    layer,
                    anim_time,
                    self.player,
                    layer_map,
                    layer_world_states,
                    self.texture_atlases,
                    self.layer_atlas_overrides,
                    self.layer_pivot_context,
                )
                layer_world_states[layer.layer_id] = self.apply_user_transforms(layer.layer_id, state)
        else:
            base_states = self._build_layer_world_states_base(anim_time, apply_global=False)
            for layer in self.player.animation.layers:
                base_state = base_states.get(layer.layer_id)
                if not base_state:
                    continue
                layer_world_states[layer.layer_id] = self.apply_user_transforms(
                    layer.layer_id, dict(base_state)
                )

        if apply_constraints and self.constraint_manager and self.constraint_manager.enabled:
            self.constraint_manager.apply_to_world_states(
                layer_world_states, layer_map, self.layer_offsets
            )

        self._apply_global_lane_delta(layer_world_states, anim_time)

        self._last_layer_world_states = layer_world_states
        return layer_world_states

    def _build_layer_world_states_base(
        self,
        anim_time: Optional[float] = None,
        *,
        apply_global: bool = True,
    ) -> Dict[int, Dict]:
        """Calculate world states for all layers without applying user transforms."""
        if not self.player.animation:
            return {}
        if anim_time is None:
            anim_time = self.player.current_time
        layer_map, order_map, _render_layers, _has_depth = self._get_animation_layer_cache(
            self.player.animation
        )
        self._layer_order_map = order_map
        base_states: Dict[int, Dict] = {}
        for layer in self.player.animation.layers:
            state = self.renderer.calculate_world_state(
                layer,
                anim_time,
                self.player,
                layer_map,
                base_states,
                self.texture_atlases,
                self.layer_atlas_overrides,
                self.layer_pivot_context,
            )
            base_states[layer.layer_id] = state
        if apply_global:
            self._apply_global_lane_delta(base_states, anim_time)
        return base_states

    def _apply_global_lane_delta(
        self,
        layer_world_states: Dict[int, Dict],
        anim_time: Optional[float],
        *,
        player: Optional[AnimationPlayer] = None,
    ) -> None:
        """Apply global keyframe lane deltas as a world-space adjustment."""
        if not layer_world_states:
            return
        active_player = player or self.player
        if not active_player or not active_player.animation:
            return
        time_value = active_player.current_time if anim_time is None else anim_time
        global_delta = active_player.get_global_lane_delta(time_value)
        if not global_delta:
            return

        pos_x = float(global_delta.get("pos_x", 0.0) or 0.0)
        pos_y = float(global_delta.get("pos_y", 0.0) or 0.0)
        rotation = float(global_delta.get("rotation", 0.0) or 0.0)
        scale_x = float(global_delta.get("scale_x", 0.0) or 0.0)
        scale_y = float(global_delta.get("scale_y", 0.0) or 0.0)
        depth = float(global_delta.get("depth", 0.0) or 0.0)
        opacity_delta = float(global_delta.get("opacity", 0.0) or 0.0)
        has_sprite = bool(global_delta.get("has_sprite", False))
        sprite_name = global_delta.get("sprite_name") if has_sprite else None
        has_rgb = bool(global_delta.get("has_rgb", False))
        rgb_r = global_delta.get("r")
        rgb_g = global_delta.get("g")
        rgb_b = global_delta.get("b")
        rgb_a = global_delta.get("a")

        epsilon = 1e-6
        has_transform = (
            abs(pos_x) > epsilon
            or abs(pos_y) > epsilon
            or abs(rotation) > epsilon
            or abs(scale_x) > epsilon
            or abs(scale_y) > epsilon
        )
        has_depth = abs(depth) > epsilon
        has_opacity = abs(opacity_delta) > epsilon

        if not (has_transform or has_depth or has_opacity or has_sprite or has_rgb):
            return

        if getattr(active_player.animation, "dof_anim_flip_y", False):
            pos_y = -pos_y
            rotation = -rotation

        renderer = self.renderer
        pos_scale = renderer.local_position_multiplier * renderer.base_world_scale * renderer.position_scale
        if abs(pos_scale) < epsilon:
            pos_scale = 1.0
        world_pos_x = pos_x * pos_scale
        world_pos_y = pos_y * pos_scale

        scale_factor_x = 1.0 + (scale_x / 100.0)
        scale_factor_y = 1.0 + (scale_y / 100.0)
        rot_rad = math.radians(rotation)
        cos_r = math.cos(rot_rad)
        sin_r = math.sin(rot_rad)
        g_m00 = scale_factor_x * cos_r
        g_m01 = -scale_factor_y * sin_r
        g_m10 = scale_factor_x * sin_r
        g_m11 = scale_factor_y * cos_r

        def _clamp_byte(value: Optional[float]) -> int:
            try:
                ivalue = int(value) if value is not None else 255
            except (TypeError, ValueError):
                ivalue = 255
            return max(0, min(255, ivalue))

        for state in layer_world_states.values():
            if has_transform:
                m00 = float(state.get("m00", 1.0))
                m01 = float(state.get("m01", 0.0))
                m10 = float(state.get("m10", 0.0))
                m11 = float(state.get("m11", 1.0))
                tx = float(state.get("tx", 0.0))
                ty = float(state.get("ty", 0.0))
                anchor_x = float(state.get("anchor_world_x", tx))
                anchor_y = float(state.get("anchor_world_y", ty))

                state["m00"] = g_m00 * m00 + g_m01 * m10
                state["m01"] = g_m00 * m01 + g_m01 * m11
                state["m10"] = g_m10 * m00 + g_m11 * m10
                state["m11"] = g_m10 * m01 + g_m11 * m11
                state["tx"] = g_m00 * tx + g_m01 * ty + world_pos_x
                state["ty"] = g_m10 * tx + g_m11 * ty + world_pos_y
                state["anchor_world_x"] = g_m00 * anchor_x + g_m01 * anchor_y + world_pos_x
                state["anchor_world_y"] = g_m10 * anchor_x + g_m11 * anchor_y + world_pos_y

            if has_depth:
                state["depth"] = float(state.get("depth", 0.0)) + depth

            if has_sprite and sprite_name is not None:
                state["sprite_name"] = str(sprite_name)

            if has_rgb or has_opacity:
                opacity_raw = float(state.get("opacity_raw", 100.0))
                local_alpha = float(state.get("local_alpha", 1.0))
                tint_alpha = float(state.get("tint_alpha", 1.0))
                if has_rgb:
                    r_val = _clamp_byte(rgb_r)
                    g_val = _clamp_byte(rgb_g)
                    b_val = _clamp_byte(rgb_b)
                    a_val = _clamp_byte(rgb_a)
                    state["r"] = r_val
                    state["g"] = g_val
                    state["b"] = b_val
                    state["a"] = a_val
                    local_alpha = max(0.0, min(1.0, a_val / 255.0))
                if has_opacity:
                    opacity_raw += opacity_delta
                opacity_raw = max(0.0, min(100.0, opacity_raw))
                state["opacity_raw"] = opacity_raw
                state["local_alpha"] = local_alpha
                state["world_opacity"] = max(
                    0.0,
                    min(1.0, (opacity_raw / 100.0) * local_alpha * tint_alpha),
                )

    def set_constraint_manager(self, manager: Optional[ConstraintManager]) -> None:
        self.constraint_manager = manager

    def _apply_constraints_to_offsets(self) -> None:
        if not self.constraint_manager or not self.constraint_manager.enabled:
            if self.joint_solver_enabled:
                self._apply_joint_solver_to_offsets()
            return
        if not self.player.animation:
            return
        layer_map = {layer.layer_id: layer for layer in self.player.animation.layers}
        world_states = self._build_layer_world_states(
            apply_constraints=False, propagate_user_transforms=self.propagate_user_transforms
        )
        self.constraint_manager.apply_to_offsets(
            world_states,
            layer_map,
            self.layer_offsets,
            self.layer_rotations,
            self.layer_scale_offsets,
        )
        if self.joint_solver_enabled:
            self._apply_joint_solver_to_offsets()

    def set_joint_solver_enabled(self, enabled: bool) -> None:
        self.joint_solver_enabled = bool(enabled)
        if self.joint_solver_enabled:
            self.capture_joint_rest_lengths()

    def set_joint_solver_iterations(self, value: int) -> None:
        self.joint_solver_iterations = max(1, int(value))

    def set_joint_solver_strength(self, value: float) -> None:
        self.joint_solver_strength = max(0.0, min(1.0, float(value)))

    def set_joint_solver_parented(self, enabled: bool) -> None:
        self.joint_solver_parented = bool(enabled)
        if self.joint_solver_enabled:
            self.capture_joint_rest_lengths()

    def set_propagate_user_transforms(self, enabled: bool) -> None:
        self.propagate_user_transforms = bool(enabled)

    def capture_joint_rest_lengths(self) -> None:
        """Capture rest lengths from the current pose (anchors with offsets)."""
        animation = self.player.animation
        if not animation:
            self.joint_rest_lengths = {}
            self.joint_rest_vectors = {}
            return
        base_states = self._build_layer_world_states_base()
        parent_states = base_states
        if self.joint_solver_parented and self.propagate_user_transforms:
            parent_states = self._build_layer_world_states(apply_constraints=False)
        rest_lengths: Dict[int, float] = {}
        rest_vectors: Dict[int, Tuple[float, float]] = {}
        for layer in animation.layers:
            if layer.parent_id is None or layer.parent_id < 0:
                continue
            parent_state = parent_states.get(layer.parent_id)
            parent_anchor = base_states.get(layer.parent_id)
            child_anchor = base_states.get(layer.layer_id)
            if not parent_state or not parent_anchor or not child_anchor:
                continue
            parent_offset = (0.0, 0.0)
            if self.propagate_user_transforms:
                parent_offset = self.layer_offsets.get(layer.parent_id, (0.0, 0.0))
            child_offset = self.layer_offsets.get(layer.layer_id, (0.0, 0.0))
            parent_x = float(parent_anchor.get("anchor_world_x", parent_anchor.get("tx", 0.0))) + parent_offset[0]
            parent_y = float(parent_anchor.get("anchor_world_y", parent_anchor.get("ty", 0.0))) + parent_offset[1]
            child_x = float(child_anchor.get("anchor_world_x", child_anchor.get("tx", 0.0))) + child_offset[0]
            child_y = float(child_anchor.get("anchor_world_y", child_anchor.get("ty", 0.0))) + child_offset[1]
            dx = child_x - parent_x
            dy = child_y - parent_y
            rest_lengths[layer.layer_id] = max(0.0, math.hypot(dx, dy))
            m00 = float(parent_state.get("m00", 1.0))
            m01 = float(parent_state.get("m01", 0.0))
            m10 = float(parent_state.get("m10", 0.0))
            m11 = float(parent_state.get("m11", 1.0))
            det = m00 * m11 - m01 * m10
            if abs(det) > 1e-8:
                inv00 = m11 / det
                inv01 = -m01 / det
                inv10 = -m10 / det
                inv11 = m00 / det
                local_x = inv00 * dx + inv01 * dy
                local_y = inv10 * dx + inv11 * dy
                rest_vectors[layer.layer_id] = (local_x, local_y)
        self.joint_rest_lengths = rest_lengths
        self.joint_rest_vectors = rest_vectors

    def _apply_joint_solver_to_offsets(self, skip_ids: Optional[Set[int]] = None) -> None:
        """Apply joint length constraints to layer offsets."""
        animation = self.player.animation
        if not self.joint_solver_enabled or not animation:
            return
        if not self.joint_rest_lengths and not self.joint_rest_vectors:
            return

        layer_map = {layer.layer_id: layer for layer in animation.layers}
        base_states = self._build_layer_world_states_base()
        states = base_states
        if self.joint_solver_parented and self.propagate_user_transforms:
            states = self._build_layer_world_states(apply_constraints=False)
        base_anchors: Dict[int, Tuple[float, float]] = {}
        for layer_id, state in base_states.items():
            base_anchors[layer_id] = (
                float(state.get("anchor_world_x", state.get("tx", 0.0))),
                float(state.get("anchor_world_y", state.get("ty", 0.0))),
            )

        solve_ids = self._get_joint_solver_targets(layer_map)
        if not solve_ids:
            return

        skip_ids_set: Set[int] = set(skip_ids or set())
        if self.dragging_sprite:
            if self._current_drag_targets:
                skip_ids_set.update(self._current_drag_targets)
            elif self.dragged_layer_id is not None:
                skip_ids_set.add(self.dragged_layer_id)
        if self.parent_dragging and self.parent_drag_layer_id is not None:
            skip_ids_set.add(self.parent_drag_layer_id)

        order = self._joint_solver_order(layer_map)
        iterations = max(1, int(self.joint_solver_iterations))
        strength = max(0.0, min(1.0, float(self.joint_solver_strength)))
        if strength <= 0.0:
            return

        for _ in range(iterations):
            for layer_id in order:
                if layer_id not in solve_ids:
                    continue
                if layer_id in skip_ids_set:
                    continue
                layer = layer_map.get(layer_id)
                if not layer or layer.parent_id is None or layer.parent_id < 0:
                    continue
                parent_anchor = base_anchors.get(layer.parent_id)
                child_anchor = base_anchors.get(layer_id)
                if not parent_anchor or not child_anchor:
                    continue
                parent_offset = (0.0, 0.0)
                if self.propagate_user_transforms:
                    parent_offset = self.layer_offsets.get(layer.parent_id, (0.0, 0.0))
                child_offset = self.layer_offsets.get(layer_id, (0.0, 0.0))
                parent_x = parent_anchor[0] + parent_offset[0]
                parent_y = parent_anchor[1] + parent_offset[1]
                child_x = child_anchor[0] + child_offset[0]
                child_y = child_anchor[1] + child_offset[1]

                target_x: Optional[float] = None
                target_y: Optional[float] = None
                if self.joint_solver_parented:
                    rest_vec = self.joint_rest_vectors.get(layer_id)
                    parent_state = states.get(layer.parent_id)
                    if rest_vec is not None and parent_state is not None:
                        m00 = float(parent_state.get("m00", 1.0))
                        m01 = float(parent_state.get("m01", 0.0))
                        m10 = float(parent_state.get("m10", 0.0))
                        m11 = float(parent_state.get("m11", 1.0))
                        world_dx = m00 * rest_vec[0] + m01 * rest_vec[1]
                        world_dy = m10 * rest_vec[0] + m11 * rest_vec[1]
                        target_x = parent_x + world_dx
                        target_y = parent_y + world_dy

                if target_x is None or target_y is None:
                    rest = self.joint_rest_lengths.get(layer_id)
                    if rest is None:
                        continue
                    dx = child_x - parent_x
                    dy = child_y - parent_y
                    dist = math.hypot(dx, dy)
                    if dist < 1e-6:
                        continue
                    target_x = parent_x + (dx / dist) * rest
                    target_y = parent_y + (dy / dist) * rest

                delta_x = (target_x - child_x) * strength
                delta_y = (target_y - child_y) * strength
                if abs(delta_x) > 1e-6 or abs(delta_y) > 1e-6:
                    self.layer_offsets[layer_id] = (
                        child_offset[0] + delta_x,
                        child_offset[1] + delta_y,
                    )

    def refresh_joint_solver_after_pose_record(self, fixed_layer_ids: Optional[Set[int]] = None) -> None:
        """Re-run joint solver after pose recording so children stay aligned."""
        if not self.joint_solver_enabled:
            return
        self._apply_joint_solver_to_offsets(skip_ids=fixed_layer_ids)

    def _get_joint_solver_targets(self, layer_map: Dict[int, LayerData]) -> Set[int]:
        if self.selected_layer_ids:
            roots = set(self.selected_layer_ids)
        elif self.selected_layer_id is not None:
            roots = {self.selected_layer_id}
        else:
            return set(layer_map.keys())
        children_map: Dict[int, List[int]] = {}
        for layer in layer_map.values():
            if layer.parent_id is None or layer.parent_id < 0:
                continue
            children_map.setdefault(layer.parent_id, []).append(layer.layer_id)
        result: Set[int] = set()
        stack = list(roots)
        while stack:
            current = stack.pop()
            if current in result:
                continue
            result.add(current)
            for child in children_map.get(current, []):
                if child not in result:
                    stack.append(child)
        return result

    def _joint_solver_order(self, layer_map: Dict[int, LayerData]) -> List[int]:
        order: List[int] = []
        visited: Set[int] = set()

        def visit(layer_id: int, stack: Set[int]) -> None:
            if layer_id in visited:
                return
            if layer_id in stack:
                return
            stack.add(layer_id)
            layer = layer_map.get(layer_id)
            if layer and layer.parent_id is not None and layer.parent_id >= 0:
                visit(layer.parent_id, stack)
            visited.add(layer_id)
            order.append(layer_id)
            stack.remove(layer_id)

        for lid in layer_map.keys():
            visit(lid, set())
        return order

    def _group_attachments_by_layer(self) -> Dict[int, List[AttachmentInstance]]:
        """Return attachment instances grouped by their target layer id."""
        grouping: Dict[int, List[AttachmentInstance]] = {}
        for instance in self.attachment_instances:
            if not instance.visible:
                continue
            if instance.target_layer_id is None:
                continue
            grouping.setdefault(instance.target_layer_id, []).append(instance)
        return grouping

    def _get_rendered_anchor_from_state(
        self,
        state: Dict[str, float],
        atlases: Optional[List[TextureAtlas]] = None,
        include_sprite_offset: bool = False,
    ) -> Tuple[float, float]:
        """Return the world-space anchor including sprite pivot/trim offsets."""
        ax = state.get('anchor_world_x', state.get('tx', 0.0))
        ay = state.get('anchor_world_y', state.get('ty', 0.0))
        anchor_offset = state.get('sprite_anchor_offset')
        if anchor_offset:
            scale = self.renderer.base_world_scale * self.renderer.position_scale
            dx = anchor_offset[0] * scale
            dy = anchor_offset[1] * scale
            m00 = state.get('m00', 1.0)
            m01 = state.get('m01', 0.0)
            m10 = state.get('m10', 0.0)
            m11 = state.get('m11', 1.0)
            ax += m00 * dx + m01 * dy
            ay += m10 * dx + m11 * dy
        if include_sprite_offset and atlases:
            sprite_name = state.get('sprite_name', '')
            if sprite_name:
                sprite, atlas, _ = self.renderer._find_sprite_in_atlases(sprite_name, atlases)
                if sprite and atlas:
                    trim_shift = self.renderer.trim_shift_multiplier
                    if sprite.has_polygon_mesh:
                        offset_x = sprite.offset_x * trim_shift - sprite.offset_x
                        offset_y = sprite.offset_y * trim_shift - sprite.offset_y
                    else:
                        if getattr(atlas, "pivot_mode", None) == "dof":
                            offset_x = 0.0
                            offset_y = 0.0
                        else:
                            offset_x = sprite.offset_x * trim_shift
                            offset_y = sprite.offset_y * trim_shift
                    hires_scale = 0.5 if atlas.is_hires else 1.0
                    scale = hires_scale * self.renderer.position_scale
                    dx = offset_x * scale
                    dy = offset_y * scale
                    m00 = state.get('m00', 1.0)
                    m01 = state.get('m01', 0.0)
                    m10 = state.get('m10', 0.0)
                    m11 = state.get('m11', 1.0)
                    ax += m00 * dx + m01 * dy
                    ay += m10 * dx + m11 * dy
        return (ax, ay)

    def _get_attachment_root_anchor(
        self,
        instance: AttachmentInstance,
        animation: Optional[AnimationData],
        world_states: Dict[int, Dict[str, float]],
        atlas_chain: Optional[List[TextureAtlas]] = None,
        parent_state: Optional[Dict[str, float]] = None,
    ) -> Tuple[Tuple[float, float], Dict[str, Any]]:
        """
        Return the anchor in attachment space that should align with the parent anchor.
        """
        debug: Dict[str, Any] = {
            "reason": "fallback",
            "root_layer_id": None,
            "root_layer_name": None,
            "root_layers": [],
        }
        if not animation or not world_states:
            return (0.0, 0.0), debug

        def _anchor_for_layer(layer: LayerData) -> Optional[Tuple[float, float]]:
            state = world_states.get(layer.layer_id)
            if not state:
                return None
            return self._get_rendered_anchor_from_state(
                state,
                atlases=atlas_chain,
                include_sprite_offset=True,
            )

        preferred = (instance.root_layer_name or "").lower()
        if preferred:
            for layer in animation.layers:
                if layer.name.lower() == preferred:
                    anchor = _anchor_for_layer(layer)
                    if anchor:
                        debug["reason"] = "explicit"
                        debug["root_layer_id"] = layer.layer_id
                        debug["root_layer_name"] = layer.name
                        return anchor, debug

        root_layers = [layer for layer in animation.layers if layer.parent_id < 0]
        if root_layers:
            for layer in root_layers:
                state = world_states.get(layer.layer_id)
                if not state:
                    continue
                debug["root_layers"].append(
                    {
                        "layer_id": layer.layer_id,
                        "name": layer.name,
                        "anchor": _anchor_for_layer(layer),
                        "anchor_raw": (
                            state.get('anchor_world_x', state.get('tx', 0.0)),
                            state.get('anchor_world_y', state.get('ty', 0.0)),
                        ),
                        "tx": (state.get('tx', 0.0), state.get('ty', 0.0)),
                    }
                )
            # If the attachment uses stacked root layers (fg/mg/bg), prefer the bottom-most anchor.
            stacked_names = {"fg", "mg", "bg"}
            stacked_layers = [
                layer for layer in root_layers
                if layer.name and layer.name.lower() in stacked_names
            ]
            if len(stacked_layers) > 1:
                best_layer = None
                best_anchor = None
                best_y = None
                for layer in stacked_layers:
                    anchor = _anchor_for_layer(layer)
                    if not anchor:
                        continue
                    ay = anchor[1]
                    if best_y is None or ay > best_y:
                        best_y = ay
                        best_anchor = anchor
                        best_layer = layer
                if best_anchor is not None and best_layer is not None:
                    debug["reason"] = "stacked_roots_bottom"
                    debug["root_layer_id"] = best_layer.layer_id
                    debug["root_layer_name"] = best_layer.name
                    return best_anchor, debug
            # Prefer a semantic root when possible (common in costume attachments).
            preferred_names = ("root", "main", "mid", "mg", "middle")
            for name in preferred_names:
                for layer in root_layers:
                    if layer.name.lower() == name:
                        anchor = _anchor_for_layer(layer)
                        if anchor:
                            debug["reason"] = "semantic"
                            debug["root_layer_id"] = layer.layer_id
                            debug["root_layer_name"] = layer.name
                            return anchor, debug

            # If multiple roots exist, choose the one closest to the parent anchor.
            if parent_state and len(root_layers) > 1:
                parent_anchor = (
                    parent_state.get('anchor_world_x', parent_state.get('tx', 0.0)),
                    parent_state.get('anchor_world_y', parent_state.get('ty', 0.0)),
                )
                closest_anchor: Optional[Tuple[float, float]] = None
                closest_dist = None
                closest_layer: Optional[LayerData] = None
                for layer in root_layers:
                    anchor = _anchor_for_layer(layer)
                    if not anchor:
                        continue
                    dx = anchor[0] - parent_anchor[0]
                    dy = anchor[1] - parent_anchor[1]
                    dist = dx * dx + dy * dy
                    if closest_dist is None or dist < closest_dist:
                        closest_dist = dist
                        closest_anchor = anchor
                        closest_layer = layer
                if closest_anchor is not None:
                    debug["reason"] = "closest"
                    if closest_layer:
                        debug["root_layer_id"] = closest_layer.layer_id
                        debug["root_layer_name"] = closest_layer.name
                    return closest_anchor, debug

            # Default to the first available root.
            for layer in root_layers:
                anchor = _anchor_for_layer(layer)
                if anchor:
                    debug["reason"] = "first_root"
                    debug["root_layer_id"] = layer.layer_id
                    debug["root_layer_name"] = layer.name
                    return anchor, debug

        # Fallback to the first available layer if no explicit root could be resolved.
        sample_state = next(iter(world_states.values()), None)
        if not sample_state:
            return (0.0, 0.0), debug
        return self._get_rendered_anchor_from_state(
            sample_state,
            atlases=atlas_chain,
            include_sprite_offset=True,
        ), debug

    def _render_attachment_layers(
        self,
        instance: AttachmentInstance,
        parent_state: Dict[str, float]
    ) -> None:
        """Render a single attachment animation relative to its parent layer."""
        player = instance.player
        animation = player.animation
        if not animation:
            return
        player.current_time = self._compute_attachment_time(instance)
        previous_time = self.renderer.current_time
        self.renderer.current_time = player.current_time
        layer_map = {layer.layer_id: layer for layer in animation.layers}
        world_states: Dict[int, Dict] = {}
        atlas_chain = list(instance.atlases)
        if instance.allow_base_fallback:
            atlas_chain += self.texture_atlases
        pivot_context = {layer.layer_id: True for layer in animation.layers}
        for layer in animation.layers:
            state = self.renderer.calculate_world_state(
                layer,
                player.current_time,
                player,
                layer_map,
                world_states,
                atlas_chain,
                None,
                pivot_context,
            )
            world_states[layer.layer_id] = state
        self._apply_global_lane_delta(world_states, player.current_time, player=player)
        root_anchor, root_debug = self._get_attachment_root_anchor(
            instance,
            animation,
            world_states,
            atlas_chain,
            parent_state=parent_state,
        )
        combined_states = {
            layer_id: self._combine_attachment_transform(parent_state, state, root_anchor)
            for layer_id, state in world_states.items()
        }
        parent_atlases = self.texture_atlases
        if instance.target_layer_id is not None:
            override_atlases = self.layer_atlas_overrides.get(instance.target_layer_id)
            if override_atlases:
                parent_atlases = list(override_atlases) + self.texture_atlases
        auto_offset: Optional[Tuple[float, float]] = None
        root_layer_id = root_debug.get("root_layer_id") if isinstance(root_debug, dict) else None
        if root_layer_id is not None:
            root_state = combined_states.get(root_layer_id)
            if root_state:
                combined_root_anchor = self._get_rendered_anchor_from_state(
                    root_state,
                    atlases=atlas_chain,
                    include_sprite_offset=True,
                )
                parent_anchor = self._get_rendered_anchor_from_state(
                    parent_state,
                    atlases=parent_atlases,
                    include_sprite_offset=True,
                )
                dx = parent_anchor[0] - combined_root_anchor[0]
                dy = parent_anchor[1] - combined_root_anchor[1]
                if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                    auto_offset = (dx, dy)
                    combined_states = {
                        layer_id: self._apply_attachment_offset_to_state(state, auto_offset)
                        for layer_id, state in combined_states.items()
                    }
        offset = self.attachment_offsets.get(instance.instance_id, (0.0, 0.0))
        if abs(offset[0]) > 1e-6 or abs(offset[1]) > 1e-6:
            combined_states = {
                layer_id: self._apply_attachment_offset_to_state(state, offset)
                for layer_id, state in combined_states.items()
            }
        self._attachment_world_states[instance.instance_id] = combined_states
        self._attachment_atlas_chains[instance.instance_id] = atlas_chain
        self._cache_attachment_debug_snapshot(
            instance,
            parent_state,
            root_anchor,
            root_debug,
            world_states,
            combined_states,
            offset,
            auto_offset,
            atlas_chain=atlas_chain,
            parent_atlases=parent_atlases,
        )
        bounds = self._compute_attachment_bounds(animation, combined_states, atlas_chain)
        if bounds:
            self._attachment_bounds[instance.instance_id] = bounds
        elif instance.instance_id in self._attachment_bounds:
            self._attachment_bounds.pop(instance.instance_id, None)
        for layer in reversed(animation.layers):
            if not layer.visible:
                continue
            state = combined_states.get(layer.layer_id)
            if not state:
                continue
            self.renderer.render_layer(layer, state, atlas_chain, {})
        self.renderer.current_time = previous_time

    def _cache_attachment_debug_snapshot(
        self,
        instance: AttachmentInstance,
        parent_state: Dict[str, float],
        root_anchor: Tuple[float, float],
        root_debug: Dict[str, Any],
        attachment_states: Dict[int, Dict[str, float]],
        combined_states: Dict[int, Dict[str, float]],
        offset: Tuple[float, float],
        auto_offset: Optional[Tuple[float, float]] = None,
        atlas_chain: Optional[List[TextureAtlas]] = None,
        parent_atlases: Optional[List[TextureAtlas]] = None,
    ) -> None:
        """Capture a detailed debug snapshot for attachment alignment troubleshooting."""
        if not self.attachment_debug_overlay_enabled:
            return
        parent_anchor = self._get_rendered_anchor_from_state(
            parent_state,
            atlases=parent_atlases,
            include_sprite_offset=True,
        )
        root_layers = []
        for entry in root_debug.get("root_layers", []):
            layer_id = entry.get("layer_id")
            combined_anchor = None
            if layer_id is not None:
                combined_state = combined_states.get(layer_id)
                if combined_state:
                    combined_anchor = self._get_rendered_anchor_from_state(
                        combined_state,
                        atlases=atlas_chain,
                        include_sprite_offset=True,
                    )
            root_layers.append(
                {
                    **entry,
                    "combined_anchor": combined_anchor,
                }
            )
        selected_root_id = root_debug.get("root_layer_id")
        combined_root_anchor = None
        if selected_root_id is not None and selected_root_id in combined_states:
            selected_state = combined_states.get(selected_root_id)
            if selected_state:
                combined_root_anchor = self._get_rendered_anchor_from_state(
                    selected_state,
                    atlases=atlas_chain,
                    include_sprite_offset=True,
                )
        delta = None
        if combined_root_anchor is not None:
            delta = (
                combined_root_anchor[0] - parent_anchor[0],
                combined_root_anchor[1] - parent_anchor[1],
            )
        effective_offset = offset
        if auto_offset is not None:
            effective_offset = (
                auto_offset[0] + offset[0],
                auto_offset[1] + offset[1],
            )
        self._attachment_debug_snapshots[instance.instance_id] = {
            "instance_id": instance.instance_id,
            "name": instance.name,
            "target_layer": instance.target_layer,
            "target_layer_id": instance.target_layer_id,
            "root_layer_name": instance.root_layer_name,
            "reason": root_debug.get("reason"),
            "root_layer_id": selected_root_id,
            "root_layer_name_selected": root_debug.get("root_layer_name"),
            "root_anchor": root_anchor,
            "parent_anchor": parent_anchor,
            "parent_matrix": (
                parent_state.get("m00", 0.0),
                parent_state.get("m01", 0.0),
                parent_state.get("m10", 0.0),
                parent_state.get("m11", 0.0),
            ),
            "offset": offset,
            "auto_offset": auto_offset,
            "effective_offset": effective_offset,
            "time": float(self.player.current_time),
            "attachment_time": float(instance.player.current_time),
            "duration": float(instance.player.duration or 0.0),
            "root_layers": root_layers,
            "combined_root_anchor": combined_root_anchor,
            "combined_root_delta": delta,
            "attachment_state_count": len(attachment_states),
            "combined_state_count": len(combined_states),
        }

    def _compute_attachment_time(self, instance: AttachmentInstance) -> float:
        """Derive attachment playback time from the master animation clock."""
        if not self.player.animation:
            return 0.0
        animation = instance.player.animation
        if not animation:
            return 0.0
        master_time = self.player.current_time
        speed = max(0.1, float(instance.tempo_multiplier or 1.0))
        local_time = master_time * speed + instance.time_offset
        duration = instance.player.duration or 0.0
        if duration > 0:
            if instance.player.loop:
                local_time = math.fmod(local_time, duration)
                if local_time < 0:
                    local_time += duration
            else:
                local_time = max(0.0, min(local_time, duration))
        return max(0.0, local_time)

    def _combine_attachment_transform(
        self,
        parent_state: Dict[str, float],
        child_state: Dict[str, float],
        root_anchor: Tuple[float, float]
    ) -> Dict[str, float]:
        """Return a child transform composed with its parent's world matrix."""
        result = dict(child_state)
        pm00 = parent_state['m00']
        pm01 = parent_state['m01']
        pm10 = parent_state['m10']
        pm11 = parent_state['m11']
        ptx = parent_state.get('anchor_world_x', parent_state['tx'])
        pty = parent_state.get('anchor_world_y', parent_state['ty'])

        cm00 = child_state['m00']
        cm01 = child_state['m01']
        cm10 = child_state['m10']
        cm11 = child_state['m11']
        root_x, root_y = root_anchor
        ctx = child_state['tx'] - root_x
        cty = child_state['ty'] - root_y

        result['m00'] = pm00 * cm00 + pm01 * cm10
        result['m01'] = pm00 * cm01 + pm01 * cm11
        result['m10'] = pm10 * cm00 + pm11 * cm10
        result['m11'] = pm10 * cm01 + pm11 * cm11
        result['tx'] = pm00 * ctx + pm01 * cty + ptx
        result['ty'] = pm10 * ctx + pm11 * cty + pty

        anchor_x = child_state.get('anchor_world_x', child_state['tx']) - root_x
        anchor_y = child_state.get('anchor_world_y', child_state['ty']) - root_y
        result['anchor_world_x'] = pm00 * anchor_x + pm01 * anchor_y + ptx
        result['anchor_world_y'] = pm10 * anchor_x + pm11 * anchor_y + pty
        return result

    @staticmethod
    def _apply_attachment_offset_to_state(
        state: Dict[str, float],
        offset: Tuple[float, float]
    ) -> Dict[str, float]:
        """Return a copy of the state with a translation offset applied."""
        dx, dy = offset
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            return state
        updated = dict(state)
        updated['tx'] = updated.get('tx', 0.0) + dx
        updated['ty'] = updated.get('ty', 0.0) + dy
        if 'anchor_world_x' in updated:
            updated['anchor_world_x'] = updated.get('anchor_world_x', 0.0) + dx
        if 'anchor_world_y' in updated:
            updated['anchor_world_y'] = updated.get('anchor_world_y', 0.0) + dy
        return updated

    def _compute_attachment_bounds(
        self,
        animation: AnimationData,
        world_states: Dict[int, Dict[str, float]],
        atlas_chain: List[TextureAtlas],
    ) -> Optional[Tuple[float, float, float, float]]:
        """Return the world-space bounds for an attachment animation."""
        min_x = min_y = max_x = max_y = None
        if not animation or not world_states:
            return None
        for layer in animation.layers:
            if not layer.visible:
                continue
            state = world_states.get(layer.layer_id)
            if not state:
                continue
            sprite_name = state.get('sprite_name', '')
            if not sprite_name:
                continue
            sprite = None
            atlas = None
            for atl in atlas_chain:
                sprite = atl.get_sprite(sprite_name)
                if sprite:
                    atlas = atl
                    break
            if not sprite or not atlas:
                continue
            corners_local = self.renderer.compute_local_vertices(sprite, atlas)
            if (
                not sprite.has_polygon_mesh
                and sprite.original_w > 0
                and sprite.original_h > 0
                and (sprite.original_w > sprite.w or sprite.original_h > sprite.h)
            ):
                hires_scale = 0.5 if atlas.is_hires else 1.0
                scale = hires_scale * self.renderer.position_scale
                ow = float(sprite.original_w) * scale
                oh = float(sprite.original_h) * scale
                corners_local = [(0.0, 0.0), (ow, 0.0), (ow, oh), (0.0, oh)]
            if not corners_local or len(corners_local) < 4:
                continue
            anchor_offset = state.get('sprite_anchor_offset')
            if anchor_offset:
                anchor_dx = anchor_offset[0] * self.renderer.base_world_scale * self.renderer.position_scale
                anchor_dy = anchor_offset[1] * self.renderer.base_world_scale * self.renderer.position_scale
                corners_local = [(lx + anchor_dx, ly + anchor_dy) for lx, ly in corners_local]
            m00 = state['m00']
            m01 = state['m01']
            m10 = state['m10']
            m11 = state['m11']
            tx = state['tx']
            ty = state['ty']
            for lx, ly in corners_local:
                wx = m00 * lx + m01 * ly + tx
                wy = m10 * lx + m11 * ly + ty
                if min_x is None:
                    min_x = max_x = wx
                    min_y = max_y = wy
                else:
                    min_x = min(min_x, wx)
                    max_x = max(max_x, wx)
                    min_y = min(min_y, wy)
                    max_y = max(max_y, wy)
        if min_x is None:
            return None
        return (min_x, min_y, max_x, max_y)

    def apply_user_transforms(self, layer_id: int, state: Dict) -> Dict:
        """Apply user-driven rotation and scaling offsets to a layer."""
        rotation = self.layer_rotations.get(layer_id, 0.0)
        scale_x, scale_y = self.layer_scale_offsets.get(layer_id, (1.0, 1.0))

        needs_scale = (abs(scale_x - 1.0) > 1e-6) or (abs(scale_y - 1.0) > 1e-6)
        needs_rotation = abs(rotation) > 1e-6

        # Preserve original matrix components for multiplication
        base_m00 = state['m00']
        base_m01 = state['m01']
        base_m10 = state['m10']
        base_m11 = state['m11']
        base_tx = state['tx']
        base_ty = state['ty']

        if needs_scale or needs_rotation:
            # Build the user transform (scale -> rotate) around the layer's world pivot.
            rot_rad = math.radians(rotation)
            cos_r = math.cos(rot_rad)
            sin_r = math.sin(rot_rad)

            user_m00 = cos_r * scale_x
            user_m01 = -sin_r * scale_y
            user_m10 = sin_r * scale_x
            user_m11 = cos_r * scale_y

            center = self._get_layer_center_from_state(state, layer_id)
            offset_x, offset_y = self.layer_offsets.get(layer_id, (0.0, 0.0))
            if center:
                pivot_x = center[0] - offset_x
                pivot_y = center[1] - offset_y
            else:
                pivot_x = base_tx
                pivot_y = base_ty

            user_tx = pivot_x - (user_m00 * pivot_x + user_m01 * pivot_y)
            user_ty = pivot_y - (user_m10 * pivot_x + user_m11 * pivot_y)

            # Left-multiply the existing affine matrix with the user transform matrix.
            m00 = user_m00 * base_m00 + user_m01 * base_m10
            m01 = user_m00 * base_m01 + user_m01 * base_m11
            m10 = user_m10 * base_m00 + user_m11 * base_m10
            m11 = user_m10 * base_m01 + user_m11 * base_m11
            tx = user_m00 * base_tx + user_m01 * base_ty + user_tx
            ty = user_m10 * base_tx + user_m11 * base_ty + user_ty
        else:
            m00 = base_m00
            m01 = base_m01
            m10 = base_m10
            m11 = base_m11
            tx = base_tx
            ty = base_ty

        state['m00'] = m00
        state['m01'] = m01
        state['m10'] = m10
        state['m11'] = m11
        state['tx'] = tx
        state['ty'] = ty
        state['user_rotation'] = rotation
        state['user_scale'] = (scale_x, scale_y)
        return state

    def _get_anchor_world_position(self, state: Dict, layer_id: int) -> Tuple[float, float]:
        """Return world-space anchor for a layer including user offsets."""
        anchor_x = state.get('anchor_world_x', state['tx'])
        anchor_y = state.get('anchor_world_y', state['ty'])
        offset_x, offset_y = self.layer_offsets.get(layer_id, (0.0, 0.0))
        return anchor_x + offset_x, anchor_y + offset_y

    def _get_layer_center_from_state(self, state: Dict, layer_id: int) -> Tuple[float, float]:
        """Return world-space center for a layer including user offsets."""
        return self._get_anchor_world_position(state, layer_id)

    def get_layer_center(self, layer_id: Optional[int]) -> Optional[Tuple[float, float]]:
        """Get cached center position for a layer."""
        if layer_id is None or not self.player.animation:
            return None
        state = self._last_layer_world_states.get(layer_id)
        if state is None:
            states = self._build_layer_world_states()
            state = states.get(layer_id)
            if state:
                self._last_layer_world_states = states
        if not state:
            return None
        return self._get_layer_center_from_state(state, layer_id)

    def render_rotation_gizmo(self, layer_world_states: Dict[int, Dict]):
        """Draw the rotation overlay for the selected layer."""
        if not self.rotation_gizmo_enabled or self.selected_layer_id is None:
            return
        state = layer_world_states.get(self.selected_layer_id)
        if not state:
            return
        center = self._get_layer_center_from_state(state, self.selected_layer_id)
        cx, cy = center
        radius = max(5.0, self.rotation_overlay_radius)
        segments = 48

        glDisable(GL_TEXTURE_2D)
        glLineWidth(2.0)
        glColor4f(0.1, 0.9, 1.0, 0.85)
        glBegin(GL_LINE_LOOP)
        for i in range(segments):
            angle = 2 * math.pi * i / segments
            glVertex2f(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius)
        glEnd()

        tick_count = 24
        tick_long = max(10.0 / self.render_scale, radius * 0.1)
        tick_short = max(6.0 / self.render_scale, radius * 0.06)
        glColor4f(0.1, 0.9, 1.0, 0.65)
        glBegin(GL_LINES)
        for i in range(tick_count):
            angle = 2 * math.pi * i / tick_count
            tick_len = tick_long if (i % 6 == 0) else tick_short
            inner = radius - tick_len
            glVertex2f(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner)
            glVertex2f(cx + math.cos(angle) * radius, cy + math.sin(angle) * radius)
        glEnd()

        # Draw rotation handle showing current offset
        current_angle = math.radians(self.layer_rotations.get(self.selected_layer_id, 0.0))
        handle_x = cx + math.cos(current_angle) * radius
        handle_y = cy + math.sin(current_angle) * radius
        glBegin(GL_LINES)
        glVertex2f(cx, cy)
        glVertex2f(handle_x, handle_y)
        glEnd()
        glPointSize(6.0)
        glBegin(GL_POINTS)
        glVertex2f(handle_x, handle_y)
        glEnd()
        glPointSize(1.0)
        glLineWidth(1.0)
        glEnable(GL_TEXTURE_2D)

    def render_selection_outlines(self, layer_world_states: Dict[int, Dict]):
        """Draw green outlines around selected layer sprites."""
        if not self.player.animation and self.selected_attachment_id is None:
            return
        if not self.selected_layer_ids and self.selected_attachment_id is None:
            return
        
        glDisable(GL_TEXTURE_2D)
        glLineWidth(2.5)
        # Bright green color for selection outline
        glColor4f(0.2, 0.85, 0.4, 0.9)
        
        for layer in self.player.animation.layers:
            if layer.layer_id not in self.selected_layer_ids:
                continue
            if not layer.visible:
                continue
            
            world_state = layer_world_states.get(layer.layer_id)
            if not world_state:
                continue
            
            sprite_name = world_state.get('sprite_name', '')
            if not sprite_name:
                continue
            
            # Find sprite in atlases
            sprite = None
            atlas = None
            override_atlases = self.layer_atlas_overrides.get(layer.layer_id)
            atlas_chain = (
                list(override_atlases) + self.texture_atlases
                if override_atlases
                else self.texture_atlases
            )
            for atl in atlas_chain:
                sprite = atl.get_sprite(sprite_name)
                if sprite:
                    atlas = atl
                    break
            
            if not sprite or not atlas:
                continue
            
            # Get local vertices
            corners_local = self.renderer.compute_local_vertices(sprite, atlas)
            if not corners_local or len(corners_local) < 4:
                continue

            anchor_offset = world_state.get('sprite_anchor_offset')
            if anchor_offset:
                anchor_dx = anchor_offset[0] * self.renderer.base_world_scale * self.renderer.position_scale
                anchor_dy = anchor_offset[1] * self.renderer.base_world_scale * self.renderer.position_scale
                corners_local = [(lx + anchor_dx, ly + anchor_dy) for lx, ly in corners_local]
            
            # Transform corners to world space
            m00 = world_state['m00']
            m01 = world_state['m01']
            m10 = world_state['m10']
            m11 = world_state['m11']
            tx = world_state['tx']
            ty = world_state['ty']
            
            # Apply user offset
            user_offset_x, user_offset_y = self.layer_offsets.get(layer.layer_id, (0, 0))
            
            world_corners = []
            for lx, ly in corners_local:
                wx = m00 * lx + m01 * ly + tx + user_offset_x
                wy = m10 * lx + m11 * ly + ty + user_offset_y
                world_corners.append((wx, wy))
            
            # Draw outline as a line loop
            glBegin(GL_LINE_LOOP)
            for wx, wy in world_corners:
                glVertex2f(wx, wy)
            glEnd()

        if self.selected_attachment_id is not None:
            bounds = self._attachment_bounds.get(self.selected_attachment_id)
            if bounds:
                min_x, min_y, max_x, max_y = bounds
                glBegin(GL_LINE_LOOP)
                glVertex2f(min_x, min_y)
                glVertex2f(max_x, min_y)
                glVertex2f(max_x, max_y)
                glVertex2f(min_x, max_y)
                glEnd()
        
        glLineWidth(1.0)
        glEnable(GL_TEXTURE_2D)

    def _get_layer_world_corners(
        self,
        layer_world_states: Dict[int, Dict],
        layer_id: int,
    ) -> Optional[List[Tuple[float, float]]]:
        """Return sprite quad corners in world space for a layer."""
        if not self.player.animation:
            return None
        layer = self.get_layer_by_id(layer_id)
        if not layer or not layer.visible:
            return None
        world_state = layer_world_states.get(layer_id)
        if not world_state:
            return None
        sprite_name = world_state.get('sprite_name', '')
        if not sprite_name:
            return None
        sprite = None
        atlas = None
        override_atlases = self.layer_atlas_overrides.get(layer_id)
        atlas_chain = (
            list(override_atlases) + self.texture_atlases
            if override_atlases
            else self.texture_atlases
        )
        for atl in atlas_chain:
            sprite = atl.get_sprite(sprite_name)
            if sprite:
                atlas = atl
                break
        if not sprite or not atlas:
            return None
        corners_local = self.renderer.compute_local_vertices(sprite, atlas)
        if not corners_local or len(corners_local) < 4:
            return None

        anchor_offset = world_state.get('sprite_anchor_offset')
        if anchor_offset:
            anchor_dx = anchor_offset[0] * self.renderer.base_world_scale * self.renderer.position_scale
            anchor_dy = anchor_offset[1] * self.renderer.base_world_scale * self.renderer.position_scale
            corners_local = [(lx + anchor_dx, ly + anchor_dy) for lx, ly in corners_local]

        m00 = world_state['m00']
        m01 = world_state['m01']
        m10 = world_state['m10']
        m11 = world_state['m11']
        tx = world_state['tx']
        ty = world_state['ty']
        user_offset_x, user_offset_y = self.layer_offsets.get(layer_id, (0, 0))

        world_corners = []
        for lx, ly in corners_local:
            wx = m00 * lx + m01 * ly + tx + user_offset_x
            wy = m10 * lx + m11 * ly + ty + user_offset_y
            world_corners.append((wx, wy))
        return world_corners

    def render_scale_gizmo(self, layer_world_states: Dict[int, Dict]):
        """Draw scale handles for the selected layer."""
        if not self.scale_gizmo_enabled or self.selected_layer_id is None:
            return
        state = layer_world_states.get(self.selected_layer_id)
        if not state:
            return
        center = self._get_layer_center_from_state(state, self.selected_layer_id)
        if not center:
            return
        cx, cy = center
        self._scale_handle_positions.clear()

        corners = self._get_layer_world_corners(layer_world_states, self.selected_layer_id)
        if corners:
            xs = [pt[0] for pt in corners]
            ys = [pt[1] for pt in corners]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
        else:
            handle_len = max(60.0 / self.render_scale, 25.0)
            min_x = cx - handle_len
            max_x = cx + handle_len
            min_y = cy - handle_len
            max_y = cy + handle_len

        self._scale_handle_positions['nw'] = (min_x, min_y)
        self._scale_handle_positions['ne'] = (max_x, min_y)
        self._scale_handle_positions['se'] = (max_x, max_y)
        self._scale_handle_positions['sw'] = (min_x, max_y)
        self._scale_handle_positions['n'] = ((min_x + max_x) * 0.5, min_y)
        self._scale_handle_positions['s'] = ((min_x + max_x) * 0.5, max_y)
        self._scale_handle_positions['e'] = (max_x, (min_y + max_y) * 0.5)
        self._scale_handle_positions['w'] = (min_x, (min_y + max_y) * 0.5)

        glDisable(GL_TEXTURE_2D)
        glLineWidth(2.0)
        glColor4f(0.85, 0.8, 0.2, 0.9)
        glBegin(GL_LINE_LOOP)
        glVertex2f(min_x, min_y)
        glVertex2f(max_x, min_y)
        glVertex2f(max_x, max_y)
        glVertex2f(min_x, max_y)
        glEnd()

        square = max(6.0 / self.render_scale, 3.0)
        glBegin(GL_QUADS)
        for hx, hy in self._scale_handle_positions.values():
            glVertex2f(hx - square, hy - square)
            glVertex2f(hx + square, hy - square)
            glVertex2f(hx + square, hy + square)
            glVertex2f(hx - square, hy + square)
        glEnd()

        glLineWidth(1.0)
        glEnable(GL_TEXTURE_2D)

    def _render_transform_overlay(self, layer_world_states: Dict[int, Dict]) -> None:
        """Draw a readout of rotation/scale near the selected layer."""
        if self.selected_layer_id is None:
            return
        state = layer_world_states.get(self.selected_layer_id)
        if not state:
            return
        center = self._get_layer_center_from_state(state, self.selected_layer_id)
        if not center:
            return
        rotation = float(self.layer_rotations.get(self.selected_layer_id, 0.0))
        scale_x, scale_y = self.layer_scale_offsets.get(self.selected_layer_id, (1.0, 1.0))

        lines: List[str] = []
        if self.rotation_gizmo_enabled:
            lines.append(f"Rot: {rotation:+.1f}°")
        if self.scale_gizmo_enabled:
            lines.append(f"Scale: {scale_x:.2f} x {scale_y:.2f}")
        if self.rotation_gizmo_enabled and self._rotation_snap_active:
            lines.append(f"Snap: {int(self.rotation_snap_increment)}°")
        if not lines:
            return

        screen_x, screen_y = self.world_to_screen(center[0], center[1])
        padding = 6
        font = QFont("Segoe UI", 9)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_width = max(metrics.horizontalAdvance(line) for line in lines)
        line_height = metrics.height()
        text_height = line_height * len(lines) + max(0, (len(lines) - 1) * 2)
        box_width = text_width + padding * 2
        box_height = text_height + padding * 2

        x = screen_x + 16
        y = screen_y + 16
        if x + box_width > self.width() - 4:
            x = max(4, self.width() - box_width - 4)
        if y + box_height > self.height() - 4:
            y = max(4, self.height() - box_height - 4)

        rect = QRectF(x, y, box_width, box_height)
        painter.setPen(QColor(0, 0, 0, 160))
        painter.setBrush(QColor(20, 20, 20, 190))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor(235, 235, 235))
        text_y = y + padding + line_height
        for line in lines:
            painter.drawText(int(x + padding), int(text_y), line)
            text_y += line_height + 2
        painter.end()

    def _render_attachment_debug_overlay(self) -> None:
        """Draw a detailed overlay for attachment alignment debugging."""
        if not self.attachment_debug_overlay_enabled:
            return
        if not self.attachment_instances or not self._attachment_debug_snapshots:
            return

        visible_ids = [inst.instance_id for inst in self.attachment_instances if inst.visible]
        if self.selected_attachment_id is not None:
            instance_ids = [self.selected_attachment_id] if self.selected_attachment_id in visible_ids else []
        else:
            instance_ids = visible_ids
        if not instance_ids:
            return

        palette = [
            (0.95, 0.7, 0.2, 0.9),
            (0.2, 0.75, 1.0, 0.9),
            (0.5, 0.95, 0.4, 0.9),
            (0.95, 0.4, 0.6, 0.9),
        ]

        glDisable(GL_TEXTURE_2D)
        glLineWidth(1.3)
        marker = max(7.0 / max(self.render_scale, 1e-3), 4.0)
        for idx, instance_id in enumerate(instance_ids):
            dbg = self._attachment_debug_snapshots.get(instance_id)
            if not dbg:
                continue
            color = palette[idx % len(palette)]
            parent_anchor = dbg.get("parent_anchor", (0.0, 0.0))
            combined_root = dbg.get("combined_root_anchor")

            # Parent anchor cross
            glColor4f(1.0, 1.0, 1.0, 0.9)
            glBegin(GL_LINES)
            glVertex2f(parent_anchor[0] - marker, parent_anchor[1])
            glVertex2f(parent_anchor[0] + marker, parent_anchor[1])
            glVertex2f(parent_anchor[0], parent_anchor[1] - marker)
            glVertex2f(parent_anchor[0], parent_anchor[1] + marker)
            glEnd()

            # Link parent anchor to chosen root anchor
            if combined_root:
                glColor4f(color[0], color[1], color[2], 0.75)
                glBegin(GL_LINES)
                glVertex2f(parent_anchor[0], parent_anchor[1])
                glVertex2f(combined_root[0], combined_root[1])
                glEnd()

            # Root candidate anchors
            for root in dbg.get("root_layers", []):
                combined_anchor = root.get("combined_anchor")
                if not combined_anchor:
                    continue
                is_selected = root.get("layer_id") == dbg.get("root_layer_id")
                if is_selected:
                    glColor4f(color[0], color[1], color[2], 0.95)
                else:
                    glColor4f(0.65, 0.65, 0.65, 0.7)
                half = marker * (1.0 if is_selected else 0.7)
                glBegin(GL_LINE_LOOP)
                glVertex2f(combined_anchor[0] - half, combined_anchor[1] - half)
                glVertex2f(combined_anchor[0] + half, combined_anchor[1] - half)
                glVertex2f(combined_anchor[0] + half, combined_anchor[1] + half)
                glVertex2f(combined_anchor[0] - half, combined_anchor[1] + half)
                glEnd()

            # Attachment bounds
            bounds = self._attachment_bounds.get(instance_id)
            if bounds:
                min_x, min_y, max_x, max_y = bounds
                glColor4f(color[0], color[1], color[2], 0.5)
                glBegin(GL_LINE_LOOP)
                glVertex2f(min_x, min_y)
                glVertex2f(max_x, min_y)
                glVertex2f(max_x, max_y)
                glVertex2f(min_x, max_y)
                glEnd()

        glLineWidth(1.0)
        glEnable(GL_TEXTURE_2D)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        metrics = painter.fontMetrics()

        def draw_panel(x: float, y: float, lines: List[str], header_color: QColor) -> float:
            if not lines:
                return y
            padding = 6
            line_height = metrics.height()
            text_width = max(metrics.horizontalAdvance(line) for line in lines)
            text_height = line_height * len(lines) + max(0, (len(lines) - 1) * 2)
            box_width = text_width + padding * 2
            box_height = text_height + padding * 2
            rect = QRectF(x, y, box_width, box_height)
            painter.setPen(QColor(0, 0, 0, 170))
            painter.setBrush(QColor(20, 20, 20, 210))
            painter.drawRoundedRect(rect, 4, 4)
            text_y = y + padding + line_height
            for idx_line, line in enumerate(lines):
                painter.setPen(header_color if idx_line == 0 else QColor(235, 235, 235))
                painter.drawText(int(x + padding), int(text_y), line)
                text_y += line_height + 2
            return y + box_height + 10

        def draw_label(world_pos: Tuple[float, float], text: str, color: QColor) -> None:
            sx, sy = self.world_to_screen(world_pos[0], world_pos[1])
            label_padding = 3
            label_width = metrics.horizontalAdvance(text)
            label_height = metrics.height()
            rect = QRectF(
                sx + 8,
                sy - label_height - 8,
                label_width + label_padding * 2,
                label_height + label_padding,
            )
            painter.setPen(QColor(0, 0, 0, 160))
            painter.setBrush(QColor(30, 30, 30, 200))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(color)
            painter.drawText(int(rect.x() + label_padding), int(rect.y() + label_height), text)

        panel_x = 10
        panel_y = 10
        for idx, instance_id in enumerate(instance_ids):
            dbg = self._attachment_debug_snapshots.get(instance_id)
            if not dbg:
                continue
            color = palette[idx % len(palette)]
            header_color = QColor(
                int(color[0] * 255),
                int(color[1] * 255),
                int(color[2] * 255),
            )
            parent_anchor = dbg.get("parent_anchor", (0.0, 0.0))
            draw_label(
                parent_anchor,
                f"{dbg.get('name', 'attachment')} [{instance_id}]",
                header_color,
            )

            lines = [
                f"Attachment {dbg.get('name', 'unknown')} (id {instance_id})",
                f"Target: {dbg.get('target_layer')} (layer {dbg.get('target_layer_id')})",
                f"Root pick: {dbg.get('reason')} | {dbg.get('root_layer_name_selected')} (id {dbg.get('root_layer_id')})",
                f"Parent anchor: ({parent_anchor[0]:.2f}, {parent_anchor[1]:.2f})",
                f"Root anchor (attachment): ({dbg.get('root_anchor', (0.0, 0.0))[0]:.2f}, "
                f"{dbg.get('root_anchor', (0.0, 0.0))[1]:.2f})",
            ]
            combined_root = dbg.get("combined_root_anchor")
            combined_delta = dbg.get("combined_root_delta")
            if combined_root:
                delta_str = ""
                if combined_delta:
                    delta_str = f" Δ({combined_delta[0]:+.2f}, {combined_delta[1]:+.2f})"
                lines.append(
                    f"Combined root: ({combined_root[0]:.2f}, {combined_root[1]:.2f}){delta_str}"
                )
            offset = dbg.get("offset", (0.0, 0.0))
            auto_offset = dbg.get("auto_offset")
            effective_offset = dbg.get("effective_offset", offset)
            if auto_offset:
                lines.append(
                    f"Auto offset: ({auto_offset[0]:+.2f}, {auto_offset[1]:+.2f})"
                )
            lines.append(f"Attachment offset: ({offset[0]:+.2f}, {offset[1]:+.2f})")
            if effective_offset and effective_offset != offset:
                lines.append(
                    f"Effective offset: ({effective_offset[0]:+.2f}, {effective_offset[1]:+.2f})"
                )
            lines.append(
                f"Time: {dbg.get('attachment_time', 0.0):.3f}s / {dbg.get('duration', 0.0):.3f}s"
            )
            root_layers = dbg.get("root_layers", [])
            if root_layers:
                lines.append("Root candidates:")
                for root in root_layers:
                    combined = root.get("combined_anchor")
                    delta = ""
                    if combined:
                        delta = (
                            f" c=({combined[0]:.2f},{combined[1]:.2f})"
                            f" Δ=({combined[0]-parent_anchor[0]:+.2f},{combined[1]-parent_anchor[1]:+.2f})"
                        )
                    lines.append(
                        f"- {root.get('name')} id={root.get('layer_id')} "
                        f"a=({root.get('anchor', (0.0, 0.0))[0]:.2f},{root.get('anchor', (0.0, 0.0))[1]:.2f}) "
                        f"tx=({root.get('tx', (0.0, 0.0))[0]:.2f},{root.get('tx', (0.0, 0.0))[1]:.2f})"
                        f"{delta}"
                    )

            panel_y = draw_panel(panel_x, panel_y, lines, header_color)

        painter.end()

    def _render_particle_debug_overlay(self) -> None:
        """Render a small on-screen panel with particle debug info."""
        if not self.particle_debug_overlay_enabled:
            return
        if not self.particle_entries:
            return
        if not self._particle_debug_info:
            animation = self.player.animation
            if not animation:
                return
            self._build_particle_draws(animation, self.player.current_time)
            if not self._particle_debug_info:
                return

        missing_textures = sum(1 for entry in self.particle_entries if not entry.texture_id)
        missing_images = sum(
            1 for entry in self.particle_entries if entry.texture_id is None and entry.texture_image is None
        )
        info = self._particle_debug_info
        count = int(info.get("particle_count", 0))
        first_pos = info.get("first_particle")
        current_source = info.get("current_source")
        emission_rate = float(info.get("emission_rate", 0.0))
        emission_distance_rate = float(info.get("emission_distance_rate", 0.0))
        lifetime = float(info.get("lifetime", 0.0))
        approx_count = int(max(0.0, emission_rate * lifetime))
        sample_count = len(self._particle_debug_samples)
        lines = [
            "Particles Debug",
            f"Emitters: {len(self.particle_entries)}",
            f"Emitter: {info.get('emitter_name', '?')}",
            f"Control: {info.get('control_name', '-')}",
            f"Viewport cap: {int(info.get('viewport_particle_cap', 0))}",
            f"Emitter cap: {int(info.get('emitter_particle_cap', 0))}",
            f"Missing textures: {missing_textures}",
            f"Missing images: {missing_images}",
            f"Particles this frame: {count}",
            f"Runtime active: {int(info.get('runtime_active_particles', 0))}",
            f"Approx emit count: {approx_count}",
            f"Rate/distance/life: {emission_rate:.1f} / {emission_distance_rate:.1f} / {lifetime:.2f}",
            (
                "Shape: "
                f"type {info.get('shape_type', '?')} "
                f"place {info.get('shape_placement_mode', '?')} "
                f"angle {float(info.get('shape_angle', 0.0)):.1f} "
                f"radius {float(info.get('shape_radius', 0.0)):.2f} "
                f"thick {float(info.get('shape_radius_thickness', 1.0)):.2f} "
                f"length {float(info.get('shape_length', 0.0)):.2f}"
            ),
            (
                "Sim: "
                f"space {info.get('simulation_space', '?')} "
                f"move {'Y' if info.get('move_with_transform') else 'N'} "
                f"velMod {'Y' if info.get('velocity_module_enabled') else 'N'} "
                f"velWS {'Y' if info.get('velocity_world_space') else 'N'} "
                f"emitVel {info.get('emitter_velocity_mode', '?')}"
            ),
            f"Debug samples: {sample_count}",
        ]
        if first_pos:
            lines.append(f"First particle: ({first_pos[0]:.2f}, {first_pos[1]:.2f})")
        if current_source:
            lines.append(f"Current source: ({current_source[0]:.2f}, {current_source[1]:.2f})")
        if self._particle_debug_samples:
            sample = self._particle_debug_samples[0]
            lines.extend(
                [
                    f"Emit origin: ({sample.emitter_origin[0]:.2f}, {sample.emitter_origin[1]:.2f})",
                    f"Spawn: ({sample.spawn_point[0]:.2f}, {sample.spawn_point[1]:.2f})",
                    f"Axis: ({sample.cone_axis[0]:.2f}, {sample.cone_axis[1]:.2f})",
                    f"Vel: ({sample.velocity[0]:.2f}, {sample.velocity[1]:.2f})",
                    f"Gravity: ({sample.gravity[0]:.2f}, {sample.gravity[1]:.2f})",
                    f"Age/Life: {sample.age:.3f} / {sample.life:.3f}",
                ]
            )
            if sample.control_origin:
                lines.append(
                    f"Ctrl: ({sample.control_origin[0]:.2f}, {sample.control_origin[1]:.2f})"
                )
            if sample.derived_socket:
                lines.append(
                    f"Socket: ({sample.derived_socket[0]:.2f}, {sample.derived_socket[1]:.2f})"
                )
            if sample.node_xy_origin:
                lines.append(
                    f"Node XY: ({sample.node_xy_origin[0]:.2f}, {sample.node_xy_origin[1]:.2f})"
                )
            if sample.node_alt_depth_origin:
                lines.append(
                    f"Node Y-Z: ({sample.node_alt_depth_origin[0]:.2f}, {sample.node_alt_depth_origin[1]:.2f})"
                )
        control_offset = info.get("control_local_offset")
        control_offset_std = info.get("control_local_offset_std")
        source_layer_name = info.get("source_layer_name")
        source_layer_offset = info.get("source_layer_offset_local")
        source_layer_offset_std = info.get("source_layer_offset_std")
        source_surface_direction = info.get("source_surface_direction")
        source_mode = info.get("source_mode")
        particle_origin_offset = info.get("particle_origin_offset")
        particle_distance_sensitivity = info.get("particle_distance_sensitivity")
        current_source = info.get("current_source")
        if control_offset:
            lines.append(
                f"Local socket: ({control_offset[0]:.2f}, {control_offset[1]:.2f})"
            )
        if control_offset_std:
            lines.append(
                f"Socket std: ({control_offset_std[0]:.3f}, {control_offset_std[1]:.3f})"
            )
        if source_layer_name:
            lines.append(f"Source layer: {source_layer_name}")
        if source_mode:
            lines.append(f"Source mode: {source_mode}")
        if source_layer_offset:
            lines.append(
                f"Layer offset: ({source_layer_offset[0]:.2f}, {source_layer_offset[1]:.2f})"
            )
        if source_layer_offset_std:
            lines.append(
                f"Layer std: ({source_layer_offset_std[0]:.3f}, {source_layer_offset_std[1]:.3f})"
            )
        if source_surface_direction:
            lines.append(
                f"Surface dir: ({source_surface_direction[0]:.2f}, {source_surface_direction[1]:.2f})"
            )
        if particle_origin_offset:
            lines.append(
                f"Origin offset: ({float(particle_origin_offset[0]):.2f}, {float(particle_origin_offset[1]):.2f})"
            )
        if particle_distance_sensitivity is not None:
            lines.append(f"Distance sensitivity: {float(particle_distance_sensitivity):.2f}")

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        padding = 6
        line_height = metrics.height()
        text_width = max(metrics.horizontalAdvance(line) for line in lines)
        text_height = line_height * len(lines) + max(0, (len(lines) - 1) * 2)
        box_width = text_width + padding * 2
        box_height = text_height + padding * 2
        x = max(10, self.width() - box_width - 10)
        y = 10
        rect = QRectF(x, y, box_width, box_height)
        painter.setPen(QColor(0, 0, 0, 170))
        painter.setBrush(QColor(20, 20, 20, 210))
        painter.drawRoundedRect(rect, 4, 4)
        text_y = y + padding + line_height
        for idx_line, line in enumerate(lines):
            painter.setPen(QColor(255, 210, 120) if idx_line == 0 else QColor(235, 235, 235))
            painter.drawText(int(x + padding), int(text_y), line)
            text_y += line_height + 2

        if self._particle_debug_samples:
            axis_len_px = 45.0
            vel_scale = 0.06
            grav_scale = 0.02
            for idx, sample in enumerate(self._particle_debug_samples):
                ox, oy = self.world_to_screen(*sample.emitter_origin)
                sx, sy = self.world_to_screen(*sample.spawn_point)
                px, py = self.world_to_screen(*sample.particle_pos)
                emitter_color = QColor(255, 208, 64, 220)
                spawn_color = QColor(110, 220, 255, 220)
                axis_color = QColor(255, 160, 64, 220)
                vel_color = QColor(96, 255, 144, 220)
                grav_color = QColor(255, 96, 96, 220)
                trail_color = QColor(255, 255, 255, 120)
                control_color = QColor(210, 120, 255, 220)
                socket_color = QColor(255, 120, 210, 220)
                node_xy_color = QColor(120, 180, 255, 220)
                node_alt_color = QColor(255, 120, 120, 220)
                current_source_color = QColor(255, 255, 64, 220)
                if idx > 0:
                    alpha = max(80, 220 - idx * 18)
                    emitter_color.setAlpha(alpha)
                    spawn_color.setAlpha(alpha)
                    axis_color.setAlpha(alpha)
                    vel_color.setAlpha(alpha)
                    grav_color.setAlpha(alpha)
                    trail_color.setAlpha(max(60, alpha - 80))
                    control_color.setAlpha(alpha)
                    socket_color.setAlpha(alpha)
                    node_xy_color.setAlpha(alpha)
                    node_alt_color.setAlpha(alpha)
                    current_source_color.setAlpha(alpha)
                if sample.node_xy_origin:
                    nx, ny = self.world_to_screen(*sample.node_xy_origin)
                    painter.setPen(node_xy_color)
                    painter.drawEllipse(QRectF(nx - 3.0, ny - 3.0, 6.0, 6.0))
                if sample.node_alt_depth_origin:
                    ax, ay = self.world_to_screen(*sample.node_alt_depth_origin)
                    painter.setPen(node_alt_color)
                    painter.drawRect(QRectF(ax - 3.0, ay - 3.0, 6.0, 6.0))
                if sample.control_origin:
                    cx, cy = self.world_to_screen(*sample.control_origin)
                    painter.setPen(control_color)
                    painter.drawLine(int(cx - 4), int(cy), int(cx + 4), int(cy))
                    painter.drawLine(int(cx), int(cy - 4), int(cx), int(cy + 4))
                    painter.drawLine(int(cx), int(cy), int(ox), int(oy))
                if sample.derived_socket:
                    dx, dy = self.world_to_screen(*sample.derived_socket)
                    painter.setPen(socket_color)
                    painter.drawLine(int(dx - 4), int(dy - 4), int(dx + 4), int(dy + 4))
                    painter.drawLine(int(dx - 4), int(dy + 4), int(dx + 4), int(dy - 4))
                    if sample.control_origin:
                        cx, cy = self.world_to_screen(*sample.control_origin)
                        painter.drawLine(int(cx), int(cy), int(dx), int(dy))
                if idx == 0 and current_source:
                    qx, qy = self.world_to_screen(*current_source)
                    painter.setPen(current_source_color)
                    painter.drawLine(int(qx - 6), int(qy), int(qx + 6), int(qy))
                    painter.drawLine(int(qx), int(qy - 6), int(qx), int(qy + 6))
                    painter.drawRect(QRectF(qx - 3.0, qy - 3.0, 6.0, 6.0))
                painter.setPen(emitter_color)
                painter.drawLine(int(ox - 5), int(oy), int(ox + 5), int(oy))
                painter.drawLine(int(ox), int(oy - 5), int(ox), int(oy + 5))
                painter.setPen(trail_color)
                painter.drawLine(int(ox), int(oy), int(sx), int(sy))
                painter.setPen(spawn_color)
                painter.drawEllipse(QRectF(sx - 3.0, sy - 3.0, 6.0, 6.0))
                painter.setPen(axis_color)
                painter.drawLine(
                    int(sx),
                    int(sy),
                    int(sx + sample.cone_axis[0] * axis_len_px),
                    int(sy + sample.cone_axis[1] * axis_len_px),
                )
                painter.setPen(vel_color)
                painter.drawLine(
                    int(sx),
                    int(sy),
                    int(sx + sample.velocity[0] * vel_scale),
                    int(sy + sample.velocity[1] * vel_scale),
                )
                painter.setPen(grav_color)
                painter.drawLine(
                    int(sx),
                    int(sy),
                    int(sx + sample.gravity[0] * grav_scale),
                    int(sy + sample.gravity[1] * grav_scale),
                )
                painter.setPen(trail_color)
                painter.drawEllipse(QRectF(px - 2.0, py - 2.0, 4.0, 4.0))
        painter.end()
    
    def render_anchor_parent_overlay(self, layer_world_states: Dict[int, Dict]):
        """Draw anchor/parent overlays with draggable handles."""
        if not self.player.animation:
            return
        if not (self.anchor_overlay_enabled or self.parent_overlay_enabled):
            return
        
        anchor_radius = max(6.0 / max(self.render_scale, 1e-3), 4.0)
        parent_half = anchor_radius * 1.4
        self._anchor_handle_positions.clear()
        self._parent_handle_positions.clear()
        
        children_map: Dict[int, List[int]] = {}
        if self.parent_overlay_enabled:
            for layer in self.player.animation.layers:
                if layer.parent_id >= 0:
                    children_map.setdefault(layer.parent_id, []).append(layer.layer_id)
        
        glDisable(GL_TEXTURE_2D)
        glLineWidth(1.5)
        
        for layer in self.player.animation.layers:
            if not layer.visible:
                continue
            state = layer_world_states.get(layer.layer_id)
            if not state:
                continue
            ax, ay = self._get_anchor_world_position(state, layer.layer_id)
            
            if self.parent_overlay_enabled and layer.layer_id in children_map:
                self._parent_handle_positions[layer.layer_id] = (ax, ay)
                glColor4f(0.2, 0.7, 1.0, 0.85)
                for child_id in children_map[layer.layer_id]:
                    child_state = layer_world_states.get(child_id)
                    if not child_state:
                        continue
                    cx, cy = self._get_anchor_world_position(child_state, child_id)
                    glBegin(GL_LINES)
                    glVertex2f(ax, ay)
                    glVertex2f(cx, cy)
                    glEnd()
                glBegin(GL_LINE_LOOP)
                glVertex2f(ax - parent_half, ay - parent_half)
                glVertex2f(ax + parent_half, ay - parent_half)
                glVertex2f(ax + parent_half, ay + parent_half)
                glVertex2f(ax - parent_half, ay + parent_half)
                glEnd()
            
            if self.anchor_overlay_enabled:
                self._anchor_handle_positions[layer.layer_id] = (ax, ay)
                axis_len = anchor_radius * 2.2
                m00 = state.get("m00", 0.0)
                m10 = state.get("m10", 0.0)
                m01 = state.get("m01", 0.0)
                m11 = state.get("m11", 0.0)
                x_len = math.hypot(m00, m10)
                y_len = math.hypot(m01, m11)
                if x_len > 1e-6:
                    glColor4f(0.9, 0.2, 0.2, 0.65)
                    glBegin(GL_LINES)
                    glVertex2f(ax, ay)
                    glVertex2f(ax + (m00 / x_len) * axis_len, ay + (m10 / x_len) * axis_len)
                    glEnd()
                if y_len > 1e-6:
                    glColor4f(0.2, 0.9, 0.2, 0.65)
                    glBegin(GL_LINES)
                    glVertex2f(ax, ay)
                    glVertex2f(ax + (m01 / y_len) * axis_len, ay + (m11 / y_len) * axis_len)
                    glEnd()

                is_hovered = layer.layer_id == self._anchor_hover_layer_id
                missing_anchor = bool(state.get("anchor_map_missing"))
                if is_hovered:
                    glColor4f(1.0, 1.0, 1.0, 0.95)
                elif missing_anchor:
                    glColor4f(1.0, 0.25, 0.25, 0.95)
                else:
                    glColor4f(0.95, 0.6, 0.2, 0.9)
                glBegin(GL_LINES)
                glVertex2f(ax - anchor_radius, ay)
                glVertex2f(ax + anchor_radius, ay)
                glVertex2f(ax, ay - anchor_radius)
                glVertex2f(ax, ay + anchor_radius)
                glEnd()
                glBegin(GL_LINE_LOOP)
                glVertex2f(ax - anchor_radius * 0.6, ay)
                glVertex2f(ax, ay + anchor_radius * 0.6)
                glVertex2f(ax + anchor_radius * 0.6, ay)
                glVertex2f(ax, ay - anchor_radius * 0.6)
                glEnd()
        
        glLineWidth(1.0)
        glEnable(GL_TEXTURE_2D)

    def _is_point_on_rotation_handle(self, world_x: float, world_y: float) -> bool:
        """Check if a point lies on the rotation gizmo ring."""
        if not self.rotation_gizmo_enabled or self.selected_layer_id is None:
            return False
        center = self.get_layer_center(self.selected_layer_id)
        if not center:
            return False
        cx, cy = center
        radius = max(5.0, self.rotation_overlay_radius)
        dx = world_x - cx
        dy = world_y - cy
        distance = math.hypot(dx, dy)
        tolerance = max(6.0, radius * 0.15)
        return abs(distance - radius) <= tolerance

    def _scale_handle_hit(self, world_x: float, world_y: float) -> Optional[str]:
        """Return which scale handle (if any) was hit."""
        if not self.scale_gizmo_enabled or self.selected_layer_id is None:
            return None
        size = max(12.0 / self.render_scale, 6.0)
        for key, (hx, hy) in self._scale_handle_positions.items():
            if abs(world_x - hx) <= size and abs(world_y - hy) <= size:
                return key
        return None
    
    def _hit_anchor_handle(self, world_x: float, world_y: float) -> Optional[int]:
        """Return the layer id of the anchor handle hit, if any."""
        if not self.anchor_overlay_enabled:
            return None
        tolerance = max(10.0 / max(self.render_scale, 1e-3), 6.0)
        candidates: List[Tuple[int, float]] = []
        for layer_id, (hx, hy) in self._anchor_handle_positions.items():
            dist = math.hypot(world_x - hx, world_y - hy)
            if dist <= tolerance:
                candidates.append((layer_id, dist))
        if not candidates:
            return None

        primary_id = self.selected_layer_id
        selected_ids = self.selected_layer_ids or set()
        order_map = self._layer_order_map

        def sort_key(item: Tuple[int, float]):
            layer_id, dist = item
            primary_rank = 0 if primary_id is not None and layer_id == primary_id else 1
            selection_rank = 0 if layer_id in selected_ids else 1
            order_rank = order_map.get(layer_id, 0)
            return (primary_rank, selection_rank, dist, order_rank)

        candidates.sort(key=sort_key)
        return candidates[0][0]
    
    def _hit_parent_handle(self, world_x: float, world_y: float) -> Optional[int]:
        """Return the parent handle layer id if the point overlaps one."""
        if not self.parent_overlay_enabled:
            return None
        half = max(12.0 / max(self.render_scale, 1e-3), 7.0)
        for layer_id, (hx, hy) in self._parent_handle_positions.items():
            if abs(world_x - hx) <= half and abs(world_y - hy) <= half:
                return layer_id
        return None

    def _begin_scale_drag(self, axis: str, world_x: float, world_y: float):
        """Start interactive scaling for the selected layer."""
        layer_id = self.selected_layer_id
        if layer_id is None:
            return
        targets = self._get_drag_targets(layer_id)
        if not targets:
            return
        self.scale_dragging = True
        self.scale_drag_axis = axis
        self._current_drag_targets = targets
        self.scale_drag_initials = {
            target_id: self.layer_scale_offsets.get(target_id, (1.0, 1.0))
            for target_id in targets
        }
        center = self.get_layer_center(layer_id)
        self.scale_drag_center = center if center else (world_x, world_y)
        cx, cy = self.scale_drag_center
        dx = world_x - cx
        dy = world_y - cy
        if abs(dx) < 1e-4:
            dx = 1e-4 if dx >= 0 else -1e-4
        if abs(dy) < 1e-4:
            dy = 1e-4 if dy >= 0 else -1e-4
        self.scale_drag_start_vec = (dx, dy)
        self._begin_transform_action(targets)

    def _update_scale_drag(self, world_x: float, world_y: float):
        """Update scaling while the user drags a scale handle."""
        layer_id = self.selected_layer_id
        if layer_id is None or not self.scale_dragging:
            return
        cx, cy = self.scale_drag_center
        min_scale = 0.05
        max_scale = float("inf")
        targets = self._current_drag_targets or [layer_id]
        initial_map = self.scale_drag_initials or {
            layer_id: self.layer_scale_offsets.get(layer_id, (1.0, 1.0))
        }

        def clamp(value: float) -> float:
            return max(min_scale, value) if math.isinf(max_scale) else min(max_scale, max(min_scale, value))

        start_dx, start_dy = self.scale_drag_start_vec
        current_dx = world_x - cx
        current_dy = world_y - cy
        ratio_x = abs(current_dx) / abs(start_dx) if abs(start_dx) > 1e-6 else 1.0
        ratio_y = abs(current_dy) / abs(start_dy) if abs(start_dy) > 1e-6 else 1.0
        handle = (self.scale_drag_axis or "").lower()
        if self._scale_uniform_active:
            if handle in {"e", "w"}:
                ratio = ratio_x
            elif handle in {"n", "s"}:
                ratio = ratio_y
            else:
                ratio = max(ratio_x, ratio_y)
            for target in targets:
                init_sx, init_sy = initial_map.get(target, (1.0, 1.0))
                new_sx = clamp(init_sx * ratio)
                new_sy = clamp(init_sy * ratio)
                self.layer_scale_offsets[target] = (new_sx, new_sy)
        else:
            for target in targets:
                init_sx, init_sy = initial_map.get(target, (1.0, 1.0))
                if handle in {"e", "w"}:
                    new_sx = clamp(init_sx * ratio_x)
                    new_sy = init_sy
                elif handle in {"n", "s"}:
                    new_sx = init_sx
                    new_sy = clamp(init_sy * ratio_y)
                else:
                    new_sx = clamp(init_sx * ratio_x)
                    new_sy = clamp(init_sy * ratio_y)
                self.layer_scale_offsets[target] = (new_sx, new_sy)
        self._apply_constraints_to_offsets()

    def _update_rotation_drag(self, world_x: float, world_y: float):
        """Update rotation offset while dragging the gizmo."""
        if self.dragged_layer_id is None:
            return
        center = self.get_layer_center(self.dragged_layer_id)
        if not center:
            return
        cx, cy = center
        angle = math.degrees(math.atan2(world_y - cy, world_x - cx))
        delta = angle - self.rotation_drag_last_angle
        # Normalize delta to [-180, 180] to avoid jumps
        while delta > 180.0:
            delta -= 360.0
        while delta < -180.0:
            delta += 360.0
        self.rotation_drag_accum += delta * self.drag_rotation_multiplier
        self.rotation_drag_last_angle = angle
        targets = self._current_drag_targets or (
            [self.dragged_layer_id] if self.dragged_layer_id is not None else []
        )
        if not targets:
            return
        snap_enabled = bool(self._rotation_snap_active)
        snap_step = max(1.0, float(self.rotation_snap_increment))
        for layer_id in targets:
            initial = self.rotation_initial_values.get(
                layer_id, self.layer_rotations.get(layer_id, 0.0)
            )
            value = initial + self.rotation_drag_accum
            if snap_enabled:
                value = round(value / snap_step) * snap_step
            self.layer_rotations[layer_id] = value
        self._apply_constraints_to_offsets()
        self.update()

    def _get_layer_anchor_value(self, layer_id: int) -> Optional[Tuple[float, float]]:
        """Return the effective anchor value (override or original) for a layer."""
        if layer_id in self.layer_anchor_overrides:
            return self.layer_anchor_overrides[layer_id]
        layer = self.get_layer_by_id(layer_id)
        if not layer:
            return None
        return (layer.anchor_x, layer.anchor_y)

    def _set_layer_anchor_override(self, layer_id: int, anchor: Tuple[float, float]):
        """Set or clear the anchor override for a layer."""
        layer = self.get_layer_by_id(layer_id)
        if not layer:
            return
        if (
            abs(anchor[0] - layer.anchor_x) < 1e-4
            and abs(anchor[1] - layer.anchor_y) < 1e-4
        ):
            self.layer_anchor_overrides.pop(layer_id, None)
        else:
            self.layer_anchor_overrides[layer_id] = (anchor[0], anchor[1])

    def _capture_transform_state(self, layer_ids: List[int]) -> Dict:
        offsets = {}
        rotations = {}
        scales = {}
        anchors = {}
        for layer_id in layer_ids:
            offsets[layer_id] = tuple(self.layer_offsets.get(layer_id, (0.0, 0.0)))
            rotations[layer_id] = float(self.layer_rotations.get(layer_id, 0.0))
            scales[layer_id] = tuple(self.layer_scale_offsets.get(layer_id, (1.0, 1.0)))
            if layer_id in self.layer_anchor_overrides:
                anchors[layer_id] = tuple(self.layer_anchor_overrides[layer_id])
            else:
                anchors[layer_id] = None
        return {'offsets': offsets, 'rotations': rotations, 'scales': scales, 'anchors': anchors}

    def _begin_transform_action(self, layer_ids: List[int]):
        unique = sorted(set(layer_ids))
        if not unique:
            self._active_transform_ids = []
            self._active_transform_snapshot = None
            return
        self._active_transform_ids = unique
        self._active_transform_snapshot = self._capture_transform_state(unique)

    def _end_transform_action(self):
        if not self._active_transform_snapshot:
            self._active_transform_ids = []
            self._current_drag_targets = []
            return
        after_state = self._capture_transform_state(self._active_transform_ids)
        if after_state != self._active_transform_snapshot:
            action = {
                'layer_ids': tuple(self._active_transform_ids),
                'before': self._active_transform_snapshot,
                'after': after_state,
            }
            self.transform_action_committed.emit(action)
        self._active_transform_snapshot = None
        self._active_transform_ids = []
        self._current_drag_targets = []

    def apply_transform_snapshot(self, state: Dict):
        for layer_id, offset in state['offsets'].items():
            if abs(offset[0]) < 1e-6 and abs(offset[1]) < 1e-6:
                self.layer_offsets.pop(layer_id, None)
            else:
                self.layer_offsets[layer_id] = offset
        for layer_id, rotation in state['rotations'].items():
            if abs(rotation) < 1e-6:
                self.layer_rotations.pop(layer_id, None)
            else:
                self.layer_rotations[layer_id] = rotation
        for layer_id, scale in state.get('scales', {}).items():
            if abs(scale[0] - 1.0) < 1e-6 and abs(scale[1] - 1.0) < 1e-6:
                self.layer_scale_offsets.pop(layer_id, None)
            else:
                self.layer_scale_offsets[layer_id] = scale
        for layer_id, anchor in state.get('anchors', {}).items():
            if anchor is None:
                self.layer_anchor_overrides.pop(layer_id, None)
            else:
                self.layer_anchor_overrides[layer_id] = tuple(anchor)
        self.update()
    
    def _begin_anchor_drag(self, layer_id: int):
        """Start dragging an anchor handle."""
        if not self.player.animation:
            return
        state = self._last_layer_world_states.get(layer_id)
        if not state:
            return
        self.anchor_dragging = True
        self.anchor_drag_layer_id = layer_id
        self.anchor_drag_last_world = self._get_anchor_world_position(state, layer_id)
        self._begin_transform_action([layer_id])
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _update_anchor_drag(self, world_x: float, world_y: float):
        """Update anchor overrides while dragging."""
        if not self.anchor_dragging or self.anchor_drag_layer_id is None:
            return
        state = self._last_layer_world_states.get(self.anchor_drag_layer_id)
        if not state:
            return
        current_anchor = self._get_anchor_world_position(state, self.anchor_drag_layer_id)
        delta_world_x = world_x - current_anchor[0]
        delta_world_y = world_y - current_anchor[1]
        if abs(delta_world_x) < 1e-3 and abs(delta_world_y) < 1e-3:
            return
        m00 = state['m00']
        m01 = state['m01']
        m10 = state['m10']
        m11 = state['m11']
        det = m00 * m11 - m01 * m10
        if abs(det) < 1e-8:
            return
        inv00 = m11 / det
        inv01 = -m01 / det
        inv10 = -m10 / det
        inv11 = m00 / det
        delta_local_x = inv00 * delta_world_x + inv01 * delta_world_y
        delta_local_y = inv10 * delta_world_x + inv11 * delta_world_y
        scale_factor = max(self.renderer.base_world_scale * self.renderer.position_scale, 1e-6)
        delta_json_x = delta_local_x / scale_factor
        delta_json_y = delta_local_y / scale_factor
        precision = max(0.001, self.anchor_drag_precision)
        delta_json_x *= precision
        delta_json_y *= precision
        anchor_value = self._get_layer_anchor_value(self.anchor_drag_layer_id)
        if anchor_value is None:
            return
        new_anchor = (anchor_value[0] + delta_json_x, anchor_value[1] + delta_json_y)
        self._set_layer_anchor_override(self.anchor_drag_layer_id, new_anchor)
        self.anchor_drag_last_world = (world_x, world_y)
        self.update()

    def _end_anchor_drag(self):
        """Finish anchor dragging."""
        if not self.anchor_dragging:
            return
        self.anchor_dragging = False
        self.anchor_drag_layer_id = None
        self._end_transform_action()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _begin_parent_drag(self, layer_id: int, world_x: float, world_y: float):
        """Start dragging a parent handle."""
        self.parent_dragging = True
        self.parent_drag_layer_id = layer_id
        self.parent_drag_last_world = (world_x, world_y)
        self._begin_transform_action([layer_id])
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def _update_parent_drag(self, world_x: float, world_y: float):
        """Update parent offset while dragging."""
        if not self.parent_dragging or self.parent_drag_layer_id is None:
            return
        dx = (world_x - self.parent_drag_last_world[0]) * self.drag_translation_multiplier
        dy = (world_y - self.parent_drag_last_world[1]) * self.drag_translation_multiplier
        if abs(dx) < 1e-4 and abs(dy) < 1e-4:
            return
        layer_id = self.parent_drag_layer_id
        old_x, old_y = self.layer_offsets.get(layer_id, (0.0, 0.0))
        self.layer_offsets[layer_id] = (old_x + dx, old_y + dy)
        self.parent_drag_last_world = (world_x, world_y)
        self.update()

    def _end_parent_drag(self):
        """Finish parent dragging."""
        if not self.parent_dragging:
            return
        self.parent_dragging = False
        self.parent_drag_layer_id = None
        self._end_transform_action()
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _get_drag_targets(self, base_layer_id: int) -> List[int]:
        if self.selection_group_lock and base_layer_id in self.selected_layer_ids:
            return list(self.selected_layer_ids)
        return [base_layer_id]
    
    def render_bone_overlay(self, anim_time: float):
        """
        Render bone/skeleton overlay showing layer hierarchy
        
        This draws:
        - Lines connecting parent layers to child layers (bones)
        - Circles at each layer's anchor point (joints)
        - Different colors for different hierarchy depths
        
        Args:
            anim_time: Current animation time
        """
        if not self.player.animation:
            return
        
        # Disable texturing for line/point drawing
        glDisable(GL_TEXTURE_2D)
        
        layer_world_states = self._build_layer_world_states(anim_time)
        layer_map = {layer.layer_id: layer for layer in self.player.animation.layers}
        
        # Colors for different hierarchy depths (rainbow-ish)
        depth_colors = [
            (1.0, 0.2, 0.2, 1.0),   # Red - root
            (1.0, 0.6, 0.2, 1.0),   # Orange
            (1.0, 1.0, 0.2, 1.0),   # Yellow
            (0.2, 1.0, 0.2, 1.0),   # Green
            (0.2, 1.0, 1.0, 1.0),   # Cyan
            (0.2, 0.2, 1.0, 1.0),   # Blue
            (1.0, 0.2, 1.0, 1.0),   # Magenta
            (1.0, 1.0, 1.0, 1.0),   # White
        ]
        
        # Calculate hierarchy depth for each layer
        depth_cache: Dict[int, int] = {}
        
        def get_depth(layer_id: int, visited: set = None) -> int:
            if layer_id in depth_cache:
                return depth_cache[layer_id]
            if visited is None:
                visited = set()
            if layer_id in visited:
                return 0  # Prevent infinite loops
            visited.add(layer_id)
            
            layer = layer_map.get(layer_id)
            if not layer or layer.parent_id < 0 or layer.parent_id not in layer_map:
                depth_cache[layer_id] = 0
                return 0
            depth = 1 + get_depth(layer.parent_id, visited)
            depth_cache[layer_id] = depth
            return depth
        
        # Get world position of a layer's anchor point
        def get_layer_world_pos(layer: LayerData) -> Tuple[float, float]:
            world_state = layer_world_states[layer.layer_id]
            return self._get_anchor_world_position(world_state, layer.layer_id)
        
        def draw_local_axes(layer: LayerData, base_pos: Tuple[float, float]):
            """Draw local X/Y axes for the layer to show orientation."""
            world_state = layer_world_states[layer.layer_id]
            axis_length = 25.0 / max(0.001, self.render_scale)
            m00 = world_state['m00']
            m01 = world_state['m01']
            m10 = world_state['m10']
            m11 = world_state['m11']
            
            # X axis (red)
            x_end = (
                base_pos[0] + m00 * axis_length,
                base_pos[1] + m10 * axis_length
            )
            glColor4f(1.0, 0.3, 0.3, 0.9)
            glBegin(GL_LINES)
            glVertex2f(base_pos[0], base_pos[1])
            glVertex2f(x_end[0], x_end[1])
            glEnd()
            
            # Y axis (green)
            y_end = (
                base_pos[0] + m01 * axis_length,
                base_pos[1] + m11 * axis_length
            )
            glColor4f(0.3, 1.0, 0.3, 0.9)
            glBegin(GL_LINES)
            glVertex2f(base_pos[0], base_pos[1])
            glVertex2f(y_end[0], y_end[1])
            glEnd()
        
        # Draw bones (lines from parent to child)
        glLineWidth(2.0)
        glBegin(GL_LINES)
        
        for layer in self.player.animation.layers:
            if layer.parent_id >= 0 and layer.parent_id in layer_map:
                parent_layer = layer_map[layer.parent_id]
                
                # Get positions
                child_pos = get_layer_world_pos(layer)
                parent_pos = get_layer_world_pos(parent_layer)
                
                # Get color based on child's depth
                depth = get_depth(layer.layer_id)
                color = depth_colors[depth % len(depth_colors)]
                
                # Draw line from parent to child
                glColor4f(*color)
                glVertex2f(parent_pos[0], parent_pos[1])
                glVertex2f(child_pos[0], child_pos[1])
        
        glEnd()
        
        # Draw joints (circles at anchor points)
        # We'll draw small squares since circles require more vertices
        joint_size = 6.0 / self.render_scale  # Size in world units
        
        for layer in self.player.animation.layers:
            pos = get_layer_world_pos(layer)
            depth = get_depth(layer.layer_id)
            color = depth_colors[depth % len(depth_colors)]
            
            # Draw filled square for joint
            glColor4f(*color)
            glBegin(GL_QUADS)
            glVertex2f(pos[0] - joint_size, pos[1] - joint_size)
            glVertex2f(pos[0] + joint_size, pos[1] - joint_size)
            glVertex2f(pos[0] + joint_size, pos[1] + joint_size)
            glVertex2f(pos[0] - joint_size, pos[1] + joint_size)
            glEnd()
            
            # Draw outline
            glColor4f(0.0, 0.0, 0.0, 1.0)
            glLineWidth(1.0)
            glBegin(GL_LINE_LOOP)
            glVertex2f(pos[0] - joint_size, pos[1] - joint_size)
            glVertex2f(pos[0] + joint_size, pos[1] - joint_size)
            glVertex2f(pos[0] + joint_size, pos[1] + joint_size)
            glVertex2f(pos[0] - joint_size, pos[1] + joint_size)
            glEnd()
            
            # Draw a smaller inner circle for root layers (no parent)
            if layer.parent_id < 0:
                inner_size = joint_size * 0.5
                glColor4f(1.0, 1.0, 1.0, 1.0)
                glBegin(GL_QUADS)
                glVertex2f(pos[0] - inner_size, pos[1] - inner_size)
                glVertex2f(pos[0] + inner_size, pos[1] - inner_size)
                glVertex2f(pos[0] + inner_size, pos[1] + inner_size)
                glVertex2f(pos[0] - inner_size, pos[1] + inner_size)
                glEnd()
            
            # Draw local axes to indicate orientation for this layer
            draw_local_axes(layer, pos)
        
        # Draw origin marker (crosshair at 0,0)
        origin_size = 20.0 / self.render_scale
        glColor4f(1.0, 1.0, 1.0, 0.5)
        glLineWidth(1.0)
        glBegin(GL_LINES)
        # Horizontal line
        glVertex2f(-origin_size, 0)
        glVertex2f(origin_size, 0)
        # Vertical line
        glVertex2f(0, -origin_size)
        glVertex2f(0, origin_size)
        glEnd()
        
        # Re-enable texturing
        glEnable(GL_TEXTURE_2D)
    
    def update_animation(self):
        """Update animation state with proper delta time"""
        current_time = time.time()

        if self.last_update_time is not None:
            delta_time = current_time - self.last_update_time
            delta_time = min(delta_time, 0.1)
        else:
            delta_time = 0.016

        self.last_update_time = current_time
        self._motion_blur_frame_dt = max(1.0 / 240.0, min(1.0 / 20.0, float(delta_time)))

        was_playing = self.player.playing
        previous_time = self.player.current_time

        if self.player.playing:
            self.player.update(delta_time)
            if self.player.animation:
                self.animation_time_changed.emit(self.player.current_time, self.player.duration)
                if self.player.loop and self.player.current_time + 1e-5 < previous_time:
                    self.animation_looped.emit()
            self.update()

        if was_playing != self.player.playing:
            self.playback_state_changed.emit(self.player.playing)
            if self.player.animation:
                self.animation_time_changed.emit(self.player.current_time, self.player.duration)
    
    def set_time(self, time: float):
        """
        Set current animation time
        
        Args:
            time: Time to set (in seconds)
        """
        self.player.current_time = time
        if self.player.animation:
            self.animation_time_changed.emit(self.player.current_time, self.player.duration)
        self.update()

    def set_antialiasing_enabled(self, enabled: bool):
        """Enable or disable OpenGL multisample anti-aliasing."""
        self.antialias_enabled = enabled
        self._apply_antialiasing_state()
        self.update()

    def set_post_aa_enabled(self, enabled: bool) -> None:
        """Enable or disable the post-process AA/effects pass."""
        normalized = bool(enabled)
        if self.post_aa_enabled == normalized:
            return
        self.post_aa_enabled = normalized
        self._post_aa_history_valid = False
        if not self._is_post_pass_required():
            ctx = self.context()
            if ctx and ctx.isValid():
                try:
                    self.makeCurrent()
                    self._clear_post_aa_resources()
                finally:
                    self.doneCurrent()
            else:
                self._post_aa_scene_fbo = None
                self._post_aa_scene_texture = None
                self._post_aa_history_fbo = None
                self._post_aa_history_texture = None
                self._post_aa_history_valid = False
                self._post_aa_scene_size = (0, 0)
        self.update()

    def set_post_aa_strength(self, strength: float) -> None:
        """Set post-process AA blend strength (0..1)."""
        try:
            value = float(strength)
        except (TypeError, ValueError):
            value = 0.5
        value = max(0.0, min(1.0, value))
        if abs(self.post_aa_strength - value) < 1e-6:
            return
        self.post_aa_strength = value
        if self._is_post_pass_required():
            self.update()

    def set_post_aa_mode(self, mode: str) -> None:
        """Set post-process AA mode: 'fxaa' or 'smaa'."""
        normalized = str(mode or "fxaa").strip().lower()
        if normalized not in {"fxaa", "smaa"}:
            normalized = "fxaa"
        if self.post_aa_mode == normalized:
            return
        self.post_aa_mode = normalized
        if self._is_post_pass_required():
            self.update()

    def set_post_bloom_enabled(self, enabled: bool) -> None:
        self.post_bloom_enabled = bool(enabled)
        if self._is_post_pass_required():
            self.update()

    def set_post_bloom_strength(self, value: float) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.15
        self.post_bloom_strength = max(0.0, min(2.0, v))
        if self._is_post_pass_required():
            self.update()

    def set_post_bloom_threshold(self, value: float) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.6
        self.post_bloom_threshold = max(0.0, min(2.0, v))
        if self._is_post_pass_required():
            self.update()

    def set_post_bloom_radius(self, value: float) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 1.5
        self.post_bloom_radius = max(0.1, min(8.0, v))
        if self._is_post_pass_required():
            self.update()

    def set_post_vignette_enabled(self, enabled: bool) -> None:
        self.post_vignette_enabled = bool(enabled)
        if self._is_post_pass_required():
            self.update()

    def set_post_vignette_strength(self, value: float) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.25
        self.post_vignette_strength = max(0.0, min(1.0, v))
        if self._is_post_pass_required():
            self.update()

    def set_post_grain_enabled(self, enabled: bool) -> None:
        self.post_grain_enabled = bool(enabled)
        if self._is_post_pass_required():
            self.update()

    def set_post_grain_strength(self, value: float) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.2
        self.post_grain_strength = max(0.0, min(1.0, v))
        if self._is_post_pass_required():
            self.update()

    def set_post_ca_enabled(self, enabled: bool) -> None:
        self.post_ca_enabled = bool(enabled)
        if self._is_post_pass_required():
            self.update()

    def set_post_ca_strength(self, value: float) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.25
        self.post_ca_strength = max(0.0, min(1.0, v))
        if self._is_post_pass_required():
            self.update()

    def set_post_motion_blur_enabled(self, enabled: bool) -> None:
        normalized = bool(enabled)
        if self.post_motion_blur_enabled == normalized:
            return
        self.post_motion_blur_enabled = normalized
        self._post_aa_history_valid = False
        if not self._is_post_pass_required():
            ctx = self.context()
            if ctx and ctx.isValid():
                try:
                    self.makeCurrent()
                    self._clear_post_aa_resources()
                finally:
                    self.doneCurrent()
            else:
                self._post_aa_scene_fbo = None
                self._post_aa_scene_texture = None
                self._post_aa_history_fbo = None
                self._post_aa_history_texture = None
                self._post_aa_history_valid = False
                self._post_aa_scene_size = (0, 0)
        self.update()

    def set_post_motion_blur_strength(self, strength: float) -> None:
        try:
            value = float(strength)
        except (TypeError, ValueError):
            value = 0.35
        value = max(0.0, min(1.0, value))
        if abs(self.post_motion_blur_strength - value) < 1e-6:
            return
        self.post_motion_blur_strength = value
        if self._is_post_pass_required():
            self.update()

    def set_scale_gizmo_enabled(self, enabled: bool):
        """Toggle the scale gizmo overlay."""
        self.scale_gizmo_enabled = enabled
        if not self.scale_gizmo_enabled:
            self.scale_dragging = False
        self.update()

    def set_anchor_overlay_enabled(self, enabled: bool):
        """Toggle anchor overlay visibility/editing."""
        self.anchor_overlay_enabled = enabled
        if not enabled and self.anchor_dragging:
            self._end_anchor_drag()
        if not enabled and self._anchor_hover_layer_id is not None:
            self._anchor_hover_layer_id = None
        self.update()

    def set_parent_overlay_enabled(self, enabled: bool):
        """Toggle parent overlay visibility/editing."""
        self.parent_overlay_enabled = enabled
        if not enabled and self.parent_dragging:
            self._end_parent_drag()
        self.update()

    def set_anchor_drag_precision(self, value: float):
        """Adjust how strongly mouse movement affects anchor edits."""
        clamped = max(0.001, min(5.0, float(value)))
        self.anchor_drag_precision = clamped

    def set_scale_gizmo_mode(self, mode: str):
        """Set how scaling is applied (uniform/per-axis)."""
        self.scale_mode = mode or "Uniform"
        self.update()

    def _apply_antialiasing_state(self):
        """Apply current antialiasing flag to the GL context."""
        try:
            if self.antialias_enabled:
                glEnable(GL_MULTISAMPLE)
            else:
                glDisable(GL_MULTISAMPLE)
        except Exception:
            # Some platforms may not expose GL_MULTISAMPLE; ignore failures
            pass

    def set_zoom_to_cursor(self, enabled: bool):
        """Enable or disable zooming towards the mouse cursor."""
        self.zoom_to_cursor = enabled

    def set_interaction_cursors(self, default_cursor=None, zoom_cursor=None) -> None:
        """Set cursor visuals used by the viewport interaction tools."""
        if default_cursor is not None:
            self._tool_cursor_default = default_cursor
        if zoom_cursor is not None:
            self._tool_cursor_zoom = zoom_cursor
        self._apply_interaction_tool_cursor()

    def set_interaction_tool(self, tool: str) -> None:
        """Set interaction mode: 'cursor' or 'zoom'."""
        normalized = "zoom" if str(tool or "").strip().lower() == "zoom" else "cursor"
        if self.interaction_tool == normalized:
            self._apply_interaction_tool_cursor()
            return
        self.interaction_tool = normalized
        self._zoom_scrub_active = False
        self._apply_interaction_tool_cursor()

    def _apply_interaction_tool_cursor(self) -> None:
        if self.interaction_tool == "zoom":
            self.setCursor(self._tool_cursor_zoom)
        else:
            self.setCursor(self._tool_cursor_default)

    def set_anchor_logging_enabled(self, enabled: bool):
        """Toggle renderer anchor logging for diagnostics."""
        self.renderer.enable_logging = enabled
        self.attachment_debug_overlay_enabled = enabled
        if not enabled:
            self.renderer.log_data.clear()
            self._attachment_debug_snapshots.clear()
    
    # ========== Mouse Event Handlers ==========
    
    def mousePressEvent(self, event):
        """Handle mouse press for camera dragging or sprite dragging"""
        try:
            if self.interaction_tool == "zoom":
                if event.button() == Qt.MouseButton.LeftButton:
                    self._zoom_scrub_active = True
                    self._zoom_scrub_start_x = float(event.position().x())
                    self._zoom_scrub_start_scale = float(self.render_scale)
                    self._zoom_scrub_anchor_screen = (
                        float(event.position().x()),
                        float(event.position().y()),
                    )
                    # Zoom tool is always mouse-anchored to match scrub-zoom behavior.
                    self._zoom_scrub_anchor_world = self.screen_to_world(
                        event.position().x(), event.position().y()
                    )
                    event.accept()
                    return
                event.ignore()
                return

            if event.button() == Qt.MouseButton.LeftButton:
                self._current_drag_targets = []
                self._scale_uniform_active = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                # Left click - try to drag sprite
                mouse_x = event.position().x()
                mouse_y = event.position().y()
                
                # Convert mouse position to world space
                world_x, world_y = self.screen_to_world(mouse_x, mouse_y)

                if self.anchor_overlay_enabled:
                    anchor_hit = self._hit_anchor_handle(world_x, world_y)
                    if anchor_hit is not None:
                        self._begin_anchor_drag(anchor_hit)
                        event.accept()
                        return

                if self.parent_overlay_enabled:
                    parent_hit = self._hit_parent_handle(world_x, world_y)
                    if parent_hit is not None:
                        self._begin_parent_drag(parent_hit, world_x, world_y)
                        event.accept()
                        return

                # Check if rotation gizmo is active and clicked
                if self.rotation_gizmo_enabled and self.selected_layer_id is not None:
                    if self._is_point_on_rotation_handle(world_x, world_y):
                        center = self.get_layer_center(self.selected_layer_id)
                        if center:
                            cx, cy = center
                            self.rotation_dragging = True
                            self.dragging_sprite = False
                            self.dragged_layer_id = self.selected_layer_id
                            self.rotation_drag_last_angle = math.degrees(math.atan2(world_y - cy, world_x - cx))
                            self.rotation_drag_accum = 0.0
                            targets = self._get_drag_targets(self.dragged_layer_id)
                            self._current_drag_targets = targets
                            self.rotation_initial_values = {
                                layer_id: self.layer_rotations.get(layer_id, 0.0)
                                for layer_id in targets
                            }
                            self.setCursor(Qt.CursorShape.CrossCursor)
                            self._begin_transform_action(targets)
                            event.accept()
                            return

                if self.scale_gizmo_enabled and self.selected_layer_id is not None:
                    handle_hit = self._scale_handle_hit(world_x, world_y)
                    if handle_hit:
                        self._begin_scale_drag(handle_hit, world_x, world_y)
                        event.accept()
                        return

                hit_attachment_id = None
                if self.selected_attachment_id is not None:
                    if self._check_attachment_hit(world_x, world_y, self.selected_attachment_id):
                        hit_attachment_id = self.selected_attachment_id
                if hit_attachment_id is None and self.attachment_instances:
                    for inst in reversed(self.attachment_instances):
                        if not inst.visible:
                            continue
                        if self._check_attachment_hit(world_x, world_y, inst.instance_id):
                            hit_attachment_id = inst.instance_id
                            break
                if hit_attachment_id is not None:
                    if self.selected_attachment_id != hit_attachment_id:
                        self.selected_attachment_id = hit_attachment_id
                    self.dragging_attachment = True
                    self.dragged_attachment_id = hit_attachment_id
                    self.last_mouse_x = mouse_x
                    self.last_mouse_y = mouse_y
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    event.accept()
                    return

                # Determine which layer is under the cursor
                hit_layer = None
                allowed_ids = self.selected_layer_ids if self.selected_layer_ids else None
                
                if allowed_ids:
                    # If layers are selected, prioritize them for dragging
                    # First check if we hit any selected layer precisely
                    primary_layer = self.get_layer_by_id(self.selected_layer_id) if self.selected_layer_id else None
                    if primary_layer and primary_layer.layer_id in allowed_ids and self._check_layer_hit(world_x, world_y, primary_layer):
                        hit_layer = primary_layer
                    
                    if not hit_layer:
                        for layer_id in allowed_ids:
                            if self.selected_layer_id == layer_id:
                                continue
                            layer = self.get_layer_by_id(layer_id)
                            if layer and self._check_layer_hit(world_x, world_y, layer):
                                hit_layer = layer
                                break
                    
                    # If no precise hit but we have selected layers, use the primary selected layer
                    # This allows dragging selected layers from anywhere on screen
                    if not hit_layer and primary_layer and primary_layer.layer_id in allowed_ids:
                        hit_layer = primary_layer
                    elif not hit_layer and allowed_ids:
                        # Fall back to first selected layer if no primary
                        first_selected_id = next(iter(allowed_ids), None)
                        if first_selected_id is not None:
                            hit_layer = self.get_layer_by_id(first_selected_id)
                else:
                    hit_layer = self.find_layer_at_position(world_x, world_y)
                
                if hit_layer:
                    self.dragging_sprite = True
                    self.dragged_layer_id = hit_layer.layer_id
                    self.last_mouse_x = mouse_x
                    self.last_mouse_y = mouse_y
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                    self._current_drag_targets = self._get_drag_targets(self.dragged_layer_id)
                    self._begin_transform_action(self._current_drag_targets)
                    event.accept()
                else:
                    event.ignore()
                    
            elif event.button() == Qt.MouseButton.MiddleButton or event.button() == Qt.MouseButton.RightButton:
                # Right/middle click - camera drag
                self.dragging_camera = True
                self.last_mouse_x = event.position().x()
                self.last_mouse_y = event.position().y()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
            else:
                event.ignore()
        except Exception as e:
            print(f"Error in mousePressEvent: {e}")
            event.ignore()
    
    def mouseReleaseEvent(self, event):
        """Handle mouse release"""
        try:
            if self.interaction_tool == "zoom":
                if event.button() == Qt.MouseButton.LeftButton and self._zoom_scrub_active:
                    self._zoom_scrub_active = False
                    self._zoom_scrub_anchor_world = None
                    self._zoom_scrub_anchor_screen = None
                    self._apply_interaction_tool_cursor()
                    event.accept()
                    return
                event.ignore()
                return

            self._rotation_snap_active = False
            self._scale_uniform_active = False
            if event.button() == Qt.MouseButton.LeftButton:
                if self.anchor_dragging:
                    self._end_anchor_drag()
                    event.accept()
                    return
                if self.parent_dragging:
                    self._end_parent_drag()
                    event.accept()
                    return
                if self.scale_dragging:
                    self.scale_dragging = False
                    self._end_transform_action()
                    self.scale_drag_initials.clear()
                    event.accept()
                    return
                if self.rotation_dragging:
                    self.rotation_dragging = False
                    self._end_transform_action()
                    self.rotation_initial_values.clear()
                    self.dragged_layer_id = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    event.accept()
                elif self.dragging_sprite:
                    self.dragging_sprite = False
                    self._end_transform_action()
                    self.dragged_layer_id = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    event.accept()
                elif self.dragging_attachment:
                    self.dragging_attachment = False
                    self.dragged_attachment_id = None
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    event.accept()
                else:
                    event.ignore()
                    
            elif event.button() == Qt.MouseButton.MiddleButton or event.button() == Qt.MouseButton.RightButton:
                if self.dragging_camera:
                    self.dragging_camera = False
                    self.setCursor(Qt.CursorShape.ArrowCursor)
                    event.accept()
                else:
                    event.ignore()
            else:
                event.ignore()
        except Exception as e:
            print(f"Error in mouseReleaseEvent: {e}")
            event.ignore()
    
    def mouseMoveEvent(self, event):
        """Handle mouse move for camera panning or sprite dragging"""
        try:
            current_x = event.position().x()
            current_y = event.position().y()

            if self.interaction_tool == "zoom" and self._zoom_scrub_active:
                delta_x = float(current_x - self._zoom_scrub_start_x)
                scale_factor = max(0.05, 1.0 + (delta_x * 0.01))
                old_scale = float(self.render_scale)
                self.render_scale = max(0.001, float(self._zoom_scrub_start_scale) * scale_factor)
                self._schedule_svg_background_quality_refresh()
                anchor_world = self._zoom_scrub_anchor_world
                if anchor_world:
                    before = self.world_to_screen(*anchor_world)
                    target = (
                        self._zoom_scrub_anchor_screen
                        if self._zoom_scrub_anchor_screen is not None
                        else (float(current_x), float(current_y))
                    )
                    self.camera_x += target[0] - before[0]
                    self.camera_y += target[1] - before[1]
                elif old_scale > 0.0:
                    ratio = self.render_scale / old_scale
                    self.camera_x *= ratio
                    self.camera_y *= ratio
                self.update()
                event.accept()
                return

            world_x, world_y = self.screen_to_world(current_x, current_y)
            self._rotation_snap_active = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._scale_uniform_active = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

            hover_layer = self._hit_anchor_handle(world_x, world_y) if self.anchor_overlay_enabled else None
            if hover_layer != self._anchor_hover_layer_id:
                self._anchor_hover_layer_id = hover_layer
                self.update()

            if self.anchor_dragging and self.anchor_drag_layer_id is not None:
                self._update_anchor_drag(world_x, world_y)
                event.accept()
                return

            if self.parent_dragging and self.parent_drag_layer_id is not None:
                self._update_parent_drag(world_x, world_y)
                event.accept()
                return

            if self.scale_dragging and self.selected_layer_id is not None:
                self._update_scale_drag(world_x, world_y)
                self.update()
                event.accept()
                return
            
            if self.rotation_dragging and self.dragged_layer_id is not None:
                self._update_rotation_drag(world_x, world_y)
                event.accept()
            
            elif self.dragging_sprite and self.dragged_layer_id is not None:
                # Dragging a sprite
                dx = (current_x - self.last_mouse_x) / self.render_scale
                dy = (current_y - self.last_mouse_y) / self.render_scale
                dx *= self.drag_translation_multiplier
                dy *= self.drag_translation_multiplier
                
                move_targets = self._current_drag_targets or self._get_drag_targets(self.dragged_layer_id)

                for layer_id in move_targets:
                    old_x, old_y = self.layer_offsets.get(layer_id, (0.0, 0.0))
                    self.layer_offsets[layer_id] = (old_x + dx, old_y + dy)

                self._apply_constraints_to_offsets()
                
                self.last_mouse_x = current_x
                self.last_mouse_y = current_y
                
                self.update()
                event.accept()

            elif self.dragging_attachment and self.dragged_attachment_id is not None:
                dx = (current_x - self.last_mouse_x) / self.render_scale
                dy = (current_y - self.last_mouse_y) / self.render_scale
                dx *= self.drag_translation_multiplier
                dy *= self.drag_translation_multiplier

                old_x, old_y = self.attachment_offsets.get(self.dragged_attachment_id, (0.0, 0.0))
                self.attachment_offsets[self.dragged_attachment_id] = (old_x + dx, old_y + dy)

                self.last_mouse_x = current_x
                self.last_mouse_y = current_y

                self.update()
                event.accept()
                
            elif self.dragging_camera:
                # Dragging camera
                dx = current_x - self.last_mouse_x
                dy = current_y - self.last_mouse_y
                
                self.camera_x += dx
                self.camera_y += dy
                
                self.last_mouse_x = current_x
                self.last_mouse_y = current_y
                
                self.update()
                event.accept()
            else:
                event.ignore()
        except Exception as e:
            print(f"Error in mouseMoveEvent: {e}")
            event.ignore()
    
    def wheelEvent(self, event):
        """Handle mouse wheel for zooming - keep animation centered."""
        center_world = None
        if self.player.animation:
            if getattr(self, 'zoom_to_cursor', False):
                cursor = event.position()
                center_world = self.screen_to_world(cursor.x(), cursor.y())
            else:
                center_world = self.get_animation_center()
        delta = event.angleDelta().y()
        if delta > 0:
            self.render_scale *= 1.1
        else:
            self.render_scale *= 0.9

        self.render_scale = max(0.001, self.render_scale)
        self._schedule_svg_background_quality_refresh()

        if center_world:
            center_screen = self.world_to_screen(*center_world)
            target_screen = (self.width() / 2, self.height() / 2)
            dx = target_screen[0] - center_screen[0]
            dy = target_screen[1] - center_screen[1]
            self.camera_x += dx
            self.camera_y += dy
        else:
            if delta > 0:
                self.camera_x *= 1.1
                self.camera_y *= 1.1
            else:
                self.camera_x *= 0.9
                self.camera_y *= 0.9

        self.update()
    
    def keyPressEvent(self, event):
        """Handle keyboard input"""
        try:
            if event.key() == Qt.Key.Key_L:
                # Enable logging for next frame
                print("=== L KEY PRESSED - LOGGING ENABLED ===")
                self.renderer.enable_logging = True
                self.renderer.log_data.clear()
                # Force immediate repaint (not just schedule it)
                self.repaint()
                # Now write the log
                print(f"Log data collected: {len(self.renderer.log_data)} entries")
                self.renderer.write_log_to_file("sprite_positions_NEW.txt")
                event.accept()
            else:
                event.ignore()
        except Exception as e:
            print(f"Error in keyPressEvent: {e}")
            import traceback
            traceback.print_exc()
            event.ignore()
    
    # ========== Helper Methods ==========
    
    def screen_to_world(self, screen_x: float, screen_y: float) -> Tuple[float, float]:
        """
        Convert screen coordinates to world coordinates
        
        The GL transform chain in paintGL is:
        1. Translate by (camera_x, camera_y)
        2. Scale by render_scale
        3. Translate by (w/2, h/2) if centered
        
        To invert, we reverse the order:
        1. Remove camera offset
        2. Divide by scale (to get into scaled space)
        3. Remove centering offset (which is in world space, applied after scale)
        
        Args:
            screen_x: X coordinate in screen space
            screen_y: Y coordinate in screen space
        
        Returns:
            Tuple of (world_x, world_y)
        """
        w = self.width()
        h = self.height()
        
        # Step 1: Remove camera offset (applied first in GL, so removed first here)
        sx = screen_x - self.camera_x
        sy = screen_y - self.camera_y
        
        # Step 2: Divide by scale to get into scaled space
        sx = sx / self.render_scale
        sy = sy / self.render_scale
        
        # Step 3: Remove centering (applied last in GL after scale, so it's in world units)
        # The centering translates by (w/2, h/2) in world space AFTER scaling
        # So we need to subtract (w/2, h/2) in world space
        if self.player.animation and self.player.animation.centered:
            sx -= w / 2
            sy -= h / 2
        
        return sx, sy

    def world_to_screen(self, world_x: float, world_y: float) -> Tuple[float, float]:
        """
        Convert world coordinates to screen coordinates.
        """
        w = self.width()
        h = self.height()

        sx = world_x
        sy = world_y
        if self.player.animation and self.player.animation.centered:
            sx += w / 2
            sy += h / 2

        sx *= self.render_scale
        sy *= self.render_scale

        sx += self.camera_x
        sy += self.camera_y
        return sx, sy

    def get_animation_center(self) -> Tuple[float, float]:
        """
        Estimate the center of the current animation by averaging layer positions.
        """
        if not self.player.animation:
            return (0.0, 0.0)

        layer_states = self._build_layer_world_states(self.player.current_time)
        if not layer_states:
            return (0.0, 0.0)

        xs = []
        ys = []
        for state in layer_states.values():
            xs.append(state.get('tx', 0.0))
            ys.append(state.get('ty', 0.0))

        min_x = min(xs)
        max_x = max(xs)
        min_y = min(ys)
        max_y = max(ys)

        return ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)
    
    def find_layer_at_position(self, world_x: float, world_y: float) -> Optional[LayerData]:
        """
        Find which layer is at the given world position
        
        Args:
            world_x: X coordinate in world space
            world_y: Y coordinate in world space
        
        Returns:
            LayerData if found, None otherwise
        """
        if not self.player.animation:
            return None
        
        layer_world_states = self._build_layer_world_states()
        render_layers = self._get_render_layers(layer_world_states)
        # Check layers in reverse render order (front to back) to find topmost hit
        for layer in reversed(render_layers):
            if layer.visible:
                world_state = layer_world_states[layer.layer_id]
                if self.renderer.is_point_in_layer(
                    world_x, world_y, layer, world_state,
                    self.texture_atlases, self.layer_offsets
                ):
                    return layer
        
        return None
    
    def _check_layer_hit(self, world_x: float, world_y: float, layer: LayerData, use_bounds_fallback: bool = True) -> bool:
        """
        Check if a point hits a specific layer
        
        Args:
            world_x: X coordinate in world space
            world_y: Y coordinate in world space
            layer: Layer to check
            use_bounds_fallback: If True, use bounding box check as fallback for selected layers
        
        Returns:
            True if point hits layer
        """
        # Use cached world states if available, otherwise build them
        if self._last_layer_world_states:
            world_state = self._last_layer_world_states.get(layer.layer_id)
        else:
            layer_world_states = self._build_layer_world_states()
            world_state = layer_world_states.get(layer.layer_id)
        
        if not world_state:
            return False
        
        # First try precise hit detection
        if self.renderer.is_point_in_layer(
            world_x, world_y, layer, world_state, 
            self.texture_atlases, self.layer_offsets
        ):
            return True
        
        # For selected layers, use bounding box as fallback with tolerance
        if use_bounds_fallback and layer.layer_id in self.selected_layer_ids:
            return self._check_layer_bounds_hit(world_x, world_y, layer, world_state)
        
        return False
    
    def _check_layer_bounds_hit(self, world_x: float, world_y: float, layer: LayerData, world_state: Dict) -> bool:
        """
        Check if a point is within the bounding box of a layer's sprite.
        This is more forgiving than pixel-perfect hit detection.
        
        Args:
            world_x: X coordinate in world space
            world_y: Y coordinate in world space
            layer: Layer to check
            world_state: Pre-calculated world state for the layer
        
        Returns:
            True if point is within layer bounds
        """
        sprite_name = world_state.get('sprite_name', '')
        if not sprite_name:
            return False
        
        # Find sprite in atlases
        sprite = None
        atlas = None
        override_atlases = self.layer_atlas_overrides.get(layer.layer_id)
        atlas_chain = (
            list(override_atlases) + self.texture_atlases
            if override_atlases
            else self.texture_atlases
        )
        for atl in atlas_chain:
            sprite = atl.get_sprite(sprite_name)
            if sprite:
                atlas = atl
                break
        
        if not sprite or not atlas:
            return False
        
        # Get local vertices
        corners_local = self.renderer.compute_local_vertices(sprite, atlas)
        if not corners_local or len(corners_local) < 4:
            return False

        anchor_offset = world_state.get('sprite_anchor_offset')
        if anchor_offset:
            anchor_dx = anchor_offset[0] * self.renderer.base_world_scale * self.renderer.position_scale
            anchor_dy = anchor_offset[1] * self.renderer.base_world_scale * self.renderer.position_scale
            corners_local = [(lx + anchor_dx, ly + anchor_dy) for lx, ly in corners_local]
        
        # Transform corners to world space
        m00 = world_state['m00']
        m01 = world_state['m01']
        m10 = world_state['m10']
        m11 = world_state['m11']
        tx = world_state['tx']
        ty = world_state['ty']
        
        # Apply user offset
        user_offset_x, user_offset_y = self.layer_offsets.get(layer.layer_id, (0, 0))
        
        world_corners = []
        for lx, ly in corners_local:
            wx = m00 * lx + m01 * ly + tx + user_offset_x
            wy = m10 * lx + m11 * ly + ty + user_offset_y
            world_corners.append((wx, wy))
        
        # Calculate bounding box with tolerance
        min_x = min(c[0] for c in world_corners)
        max_x = max(c[0] for c in world_corners)
        min_y = min(c[1] for c in world_corners)
        max_y = max(c[1] for c in world_corners)
        
        # Add tolerance based on render scale (larger tolerance when zoomed out)
        tolerance = max(10.0 / max(self.render_scale, 0.1), 5.0)
        min_x -= tolerance
        max_x += tolerance
        min_y -= tolerance
        max_y += tolerance
        
        return min_x <= world_x <= max_x and min_y <= world_y <= max_y

    def _check_attachment_hit(self, world_x: float, world_y: float, attachment_id: int) -> bool:
        """Return True if a point hits the cached bounds for an attachment."""
        bounds = self._attachment_bounds.get(attachment_id)
        if not bounds:
            return False
        min_x, min_y, max_x, max_y = bounds
        tolerance = max(10.0 / max(self.render_scale, 0.1), 5.0)
        min_x -= tolerance
        max_x += tolerance
        min_y -= tolerance
        max_y += tolerance
        return min_x <= world_x <= max_x and min_y <= world_y <= max_y
    
    def get_layer_by_id(self, layer_id: int) -> Optional[LayerData]:
        """
        Get a layer by its ID
        
        Args:
            layer_id: ID of layer to find
        
        Returns:
            LayerData if found, None otherwise
        """
        if not self.player.animation:
            return None
        
        for layer in self.player.animation.layers:
            if layer.layer_id == layer_id:
                return layer
        return None
    
    # ========== Public Control Methods ==========
    
    def reset_camera(self):
        """Reset camera to default position"""
        self.camera_x = 0.0
        self.camera_y = 0.0
        self.render_scale = 1.0
        self.reset_viewport_background_parallax_origin()
        self.update()
    
    def fit_to_view(self, padding: float = 0.1) -> bool:
        """
        Center and scale the view to fit all visible sprites
        
        This calculates the bounding box of all visible sprites at the current
        animation time and adjusts the camera position and scale to fit them
        perfectly within the viewport.
        
        Args:
            padding: Extra padding as a fraction of the viewport (0.1 = 10% padding)
        
        Returns:
            True if successful, False if no animation loaded
        """
        if not self.player.animation:
            return False
        
        # Calculate bounding box of all visible sprites
        bounds = self.calculate_animation_bounds()
        
        if bounds is None:
            return False
        
        min_x, min_y, max_x, max_y = bounds
        
        # Calculate sprite dimensions
        sprite_width = max_x - min_x
        sprite_height = max_y - min_y
        
        if sprite_width <= 0 or sprite_height <= 0:
            return False
        
        # Get viewport dimensions
        viewport_width = self.width()
        viewport_height = self.height()
        
        # Calculate available space (with padding)
        available_width = viewport_width * (1 - 2 * padding)
        available_height = viewport_height * (1 - 2 * padding)
        
        # Calculate scale to fit
        scale_x = available_width / sprite_width
        scale_y = available_height / sprite_height
        
        # Use the smaller scale to ensure everything fits
        new_scale = min(scale_x, scale_y)
        
        # Calculate the center of the sprite bounds
        sprite_center_x = (min_x + max_x) / 2
        sprite_center_y = (min_y + max_y) / 2
        
        # Calculate camera position to center the sprite
        # The camera offset is applied before scaling in paintGL
        # We want the sprite center to appear at the viewport center
        
        # If animation is centered, the origin (0,0) is at viewport center
        # So we need to offset by the sprite center position
        if self.player.animation.centered:
            # Sprite center in world space needs to be at (0,0) in screen space
            # camera_x and camera_y are applied before scale
            self.camera_x = -sprite_center_x * new_scale + viewport_width / 2
            self.camera_y = -sprite_center_y * new_scale + viewport_height / 2
            # But wait, the centering translation is applied AFTER scale in paintGL
            # So we need to account for that
            self.camera_x = viewport_width / 2 - sprite_center_x * new_scale - (viewport_width / 2) * new_scale + (viewport_width / 2)
            self.camera_y = viewport_height / 2 - sprite_center_y * new_scale - (viewport_height / 2) * new_scale + (viewport_height / 2)
            # Simplify: we want sprite_center to appear at viewport center
            # After all transforms: screen_pos = (world_pos * scale + center_offset) + camera
            # We want: viewport_center = (sprite_center * scale + center_offset) + camera
            # So: camera = viewport_center - sprite_center * scale - center_offset
            # But center_offset = (w/2, h/2) is added in world space after scale
            # Actually let's recalculate properly:
            # In paintGL: translate(camera) -> scale -> translate(w/2, h/2)
            # So: screen = (world + w/2) * scale + camera... no wait
            # glTranslatef(camera_x, camera_y, 0) - this is in screen space
            # glScalef(scale) - scales everything
            # glTranslatef(w/2, h/2, 0) - this is in scaled world space
            # So: screen = camera + scale * (world + w/2, h/2)
            # We want: viewport_center = camera + scale * (sprite_center + w/2, h/2)
            # So: camera = viewport_center - scale * (sprite_center + w/2, h/2)
            # Hmm, but w/2 is viewport width, not world width...
            # Let me re-read paintGL...
            # The centering uses self.width()/2 which is viewport pixels
            # But it's applied after scale, so it's in world units that get scaled
            # Actually no - glTranslatef after glScalef means the translation is in the scaled coordinate system
            # So the w/2 translation is in world units, and then everything is scaled
            # 
            # Let me think differently:
            # Final screen position = camera + scale * (world_pos + center_offset)
            # where center_offset = (w/2, h/2) if centered
            # We want sprite_center to appear at viewport_center:
            # viewport_w/2 = camera_x + scale * (sprite_center_x + w/2)
            # camera_x = viewport_w/2 - scale * sprite_center_x - scale * w/2
            # But that doesn't seem right either because w/2 is in pixels...
            #
            # OK let me just do it empirically:
            # We want the sprite center to be at the screen center
            self.camera_x = (viewport_width / 2) - (sprite_center_x * new_scale) - (viewport_width / 2 * new_scale)
            self.camera_y = (viewport_height / 2) - (sprite_center_y * new_scale) - (viewport_height / 2 * new_scale)
        else:
            # No centering - origin is at top-left
            # screen = camera + scale * world
            # We want sprite_center at viewport_center:
            # viewport_w/2 = camera_x + scale * sprite_center_x
            self.camera_x = (viewport_width / 2) - (sprite_center_x * new_scale)
            self.camera_y = (viewport_height / 2) - (sprite_center_y * new_scale)
        
        self.render_scale = new_scale
        self.reset_viewport_background_parallax_origin()
        self.update()
        return True
    
    def calculate_animation_bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """
        Calculate the bounding box of all visible sprites at current time
        
        Returns:
            Tuple of (min_x, min_y, max_x, max_y) in world coordinates,
            or None if no visible sprites
        """
        if not self.player.animation:
            return None
        
        layer_world_states = self._build_layer_world_states()
        
        min_x = float('inf')
        min_y = float('inf')
        max_x = float('-inf')
        max_y = float('-inf')
        
        found_any = False
        
        for layer in self.player.animation.layers:
            if not layer.visible:
                continue
            
            world_state = layer_world_states[layer.layer_id]
            sprite_name = world_state['sprite_name']
            
            if not sprite_name:
                continue
            
            # Find sprite in atlases
            sprite = None
            atlas = None
            for atl in self.texture_atlases:
                sprite = atl.get_sprite(sprite_name)
                if sprite:
                    atlas = atl
                    break
            
            if not sprite or not atlas:
                continue
            
            corners_local = self.renderer.compute_local_vertices(sprite, atlas)
            if not corners_local:
                continue
            
            # Transform corners to world space using the matrix
            m00 = world_state['m00']
            m01 = world_state['m01']
            m10 = world_state['m10']
            m11 = world_state['m11']
            tx = world_state['tx']
            ty = world_state['ty']
            
            # Apply user offset
            user_offset_x, user_offset_y = self.layer_offsets.get(layer.layer_id, (0, 0))
            
            for lx, ly in corners_local:
                # Transform to world space
                wx = m00 * lx + m01 * ly + tx + user_offset_x
                wy = m10 * lx + m11 * ly + ty + user_offset_y
                
                min_x = min(min_x, wx)
                min_y = min(min_y, wy)
                max_x = max(max_x, wx)
                max_y = max(max_y, wy)
                found_any = True
        
        if not found_any:
            return None
        
        return (min_x, min_y, max_x, max_y)
    
    def reset_layer_offsets(self):
        """Reset all layer offsets to default"""
        self.layer_offsets.clear()
        self.layer_rotations.clear()
        self.layer_scale_offsets.clear()
        self.layer_anchor_overrides.clear()
        self.rotation_initial_values.clear()
        self.scale_drag_initials.clear()
        self.rotation_dragging = False
        self.dragging_sprite = False
        self.scale_dragging = False
        self.anchor_dragging = False
        self.parent_dragging = False
        self.anchor_drag_layer_id = None
        self.parent_drag_layer_id = None
        self.update()

    def set_layer_atlas_overrides(self, overrides: Dict[int, List[TextureAtlas]]):
        """Assign per-layer atlas priority overrides."""
        if overrides:
            self.layer_atlas_overrides = {
                layer_id: list(atlases) for layer_id, atlases in overrides.items()
            }
        else:
            self.layer_atlas_overrides = {}
        self.update()

    def set_layer_pivot_context(self, context: Dict[int, bool]):
        """Track which layers have sheet+sprite remaps for pivot gating."""
        if context:
            self.layer_pivot_context = dict(context)
        else:
            self.layer_pivot_context = {}
        self.update()

    def set_shader_registry(self, registry: Optional[ShaderRegistry]):
        """Update shader registry on the renderer."""
        self.renderer.set_shader_registry(registry)

    def set_costume_pivot_adjustment_enabled(self, enabled: bool):
        """Toggle costume pivot adjustments in the renderer."""
        self.renderer.set_costume_pivot_adjustment_enabled(enabled)
        self.update()

    def set_costume_attachments(
        self,
        payloads: List[Dict[str, Any]],
        layers: List[LayerData]
    ):
        """Install attachment animations described by the costume parser."""
        self.attachment_instances.clear()
        self.attachment_offsets.clear()
        self._attachment_bounds.clear()
        self._attachment_world_states.clear()
        self._attachment_atlas_chains.clear()
        self._attachment_debug_snapshots.clear()
        if self.selected_attachment_id is not None:
            self.selected_attachment_id = None
        if not payloads:
            self.update()
            return

        name_lookup = {layer.name.lower(): layer.layer_id for layer in layers}
        for payload in payloads:
            if not payload.get("target_layer"):
                continue
            if payload.get("target_layer_id") is None:
                target = name_lookup.get(payload["target_layer"].lower())
                if target is not None:
                    payload["target_layer_id"] = target

        # Ensure textures are uploaded before we store the instances.
        self.makeCurrent()
        try:
            for payload in payloads:
                for atlas in payload.get("atlases", []):
                    if isinstance(atlas, TextureAtlas) and atlas.texture_id is None:
                        atlas.load_texture()
        finally:
            self.doneCurrent()

        instances: List[AttachmentInstance] = []
        for payload in payloads:
            animation: Optional[AnimationData] = payload.get("animation")
            if not animation:
                continue
            player = AnimationPlayer()
            player.load_animation(animation)
            loop_flag = bool(payload.get("loop", True))
            player.loop = loop_flag
            raw_offset = payload.get("time_offset", payload.get("time_scale", 0.0))
            try:
                offset_value = float(raw_offset)
            except (TypeError, ValueError):
                offset_value = 0.0
            tempo_multiplier = payload.get("tempo_multiplier", 1.0)
            try:
                tempo_value = float(tempo_multiplier)
            except (TypeError, ValueError):
                tempo_value = 1.0
            tempo_value = max(0.1, tempo_value)
            instance_id = -(len(instances) + 1)
            instances.append(
                AttachmentInstance(
                    instance_id=instance_id,
                    name=payload.get("name", "attachment"),
                    target_layer=payload.get("target_layer", ""),
                    target_layer_id=payload.get("target_layer_id"),
                    player=player,
                    atlases=list(payload.get("atlases", [])),
                    time_offset=offset_value,
                    tempo_multiplier=tempo_value,
                    loop=loop_flag,
                    root_layer_name=payload.get("root_layer"),
                    allow_base_fallback=bool(payload.get("allow_base_fallback")),
                    visible=True,
                )
            )
        self.attachment_instances = instances
        self.update()
    
    def set_selection_state(self, layer_ids: Set[int], primary_id: Optional[int], lock: bool):
        """Update which layers are selectable and whether they move together."""
        self.selected_layer_ids = set(layer_ids)
        if primary_id in self.selected_layer_ids:
            self.selected_layer_id = primary_id
        else:
            self.selected_layer_id = next(iter(self.selected_layer_ids), None)
        self.selection_group_lock = lock and bool(self.selected_layer_ids)
        if not self.selected_layer_id:
            self.scale_dragging = False
        self.update()

    def set_attachment_selection(self, attachment_id: Optional[int]):
        """Select a costume attachment for editing."""
        self.selected_attachment_id = attachment_id
        self.update()

    def get_attachment_entries(self) -> List[Tuple[int, str, bool, str]]:
        """Return (id, name, visible, target_layer) tuples for active attachments."""
        return [
            (inst.instance_id, inst.name, inst.visible, inst.target_layer)
            for inst in self.attachment_instances
        ]

    def set_attachment_visibility(self, attachment_id: int, visible: bool) -> None:
        """Show or hide a specific attachment instance."""
        for inst in self.attachment_instances:
            if inst.instance_id == attachment_id:
                inst.visible = bool(visible)
                break
        if not visible:
            self._attachment_bounds.pop(attachment_id, None)
        self.update()

    def set_selected_layer(self, layer_id: Optional[int]):
        """Convenience helper for single-layer selection."""
        if layer_id is None:
            self.set_selection_state(set(), None, False)
        else:
            self.set_selection_state({layer_id}, layer_id, False)
    
    #
