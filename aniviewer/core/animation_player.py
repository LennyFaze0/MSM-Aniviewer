"""
Animation Player
Handles animation playback, timing, and keyframe interpolation
"""

import math
import re
from typing import Callable, Dict, Optional, Tuple
from .data_structures import AnimationData, LayerData, KeyframeData


class AnimationPlayer:
    """Handles animation playback and interpolation"""
    
    _sprite_suffix_pattern = re.compile(r'^(.*?)(\d+)$')
    _MAX_DURATION_SECONDS = 2147483647 / 1000.0
    
    def __init__(self):
        self.animation: Optional[AnimationData] = None
        self.current_time: float = 0.0
        self.playing: bool = False
        self.loop: bool = True
        self.duration: float = 0.0
        self.playback_speed: float = 1.0
        self._sprite_name_resolver: Optional[Callable[[str], Optional[str]]] = None

    def set_sprite_name_resolver(
        self, resolver: Optional[Callable[[str], Optional[str]]]
    ) -> None:
        """Inject a resolver that can validate/remap sprite names."""
        self._sprite_name_resolver = resolver

    def _resolve_sprite_name(self, sprite_name: Optional[str]) -> str:
        """Resolve sprite names through atlas-aware lookup when available."""
        if not sprite_name:
            return ""
        name = str(sprite_name)
        resolver = self._sprite_name_resolver
        if resolver is None:
            return name
        try:
            resolved = resolver(name)
        except Exception:
            return name
        if isinstance(resolved, str) and resolved:
            return resolved
        return ""

    def load_animation(self, anim_data: AnimationData):
        """
        Load animation data
        
        Args:
            anim_data: Animation data to load
        """
        self.animation = anim_data
        self.current_time = 0.0
        self.calculate_duration()
    
    def calculate_duration(self):
        """Calculate animation duration from keyframes"""
        if not self.animation:
            self.duration = 0.0
            return

        def _safe_time(raw_time) -> float:
            try:
                value = float(raw_time)
            except (TypeError, ValueError):
                return 0.0
            if not math.isfinite(value) or value < 0.0:
                return 0.0
            return min(value, self._MAX_DURATION_SECONDS)

        max_time = 0.0
        global_lanes = getattr(self.animation, "global_keyframe_lanes", []) or []
        for lane in global_lanes:
            if lane and getattr(lane, "keyframes", None):
                for keyframe in lane.keyframes:
                    max_time = max(max_time, _safe_time(getattr(keyframe, "time", 0.0)))
        for layer in self.animation.layers:
            if layer.keyframes:
                for keyframe in layer.keyframes:
                    max_time = max(max_time, _safe_time(getattr(keyframe, "time", 0.0)))
            extra_lanes = getattr(layer, "extra_keyframe_lanes", []) or []
            for lane in extra_lanes:
                if lane and getattr(lane, "keyframes", None):
                    for keyframe in lane.keyframes:
                        max_time = max(max_time, _safe_time(getattr(keyframe, "time", 0.0)))

        self.duration = max_time
    
    def update(self, delta_time: float):
        """
        Update animation time
        
        Args:
            delta_time: Time elapsed since last update (in seconds)
        """
        if not self.playing or not self.animation:
            return
        
        speed = max(0.01, self.playback_speed)
        self.current_time += delta_time * speed
        
        if self.current_time > self.duration:
            if self.loop:
                self.current_time = 0.0
            else:
                self.current_time = self.duration
                self.playing = False
    
    def get_layer_state(
        self,
        layer: LayerData,
        time: float,
        *,
        include_additive: bool = True,
        exclude_lane: Optional[Tuple[str, int, int]] = None,
        include_global: bool = True
    ) -> Dict:
        """
        Get interpolated layer state at given time
        
        Args:
            layer: Layer to get state for
            time: Time to get state at
            include_additive: Include additive/global keyframe lanes
            exclude_lane: Optional (scope, layer_id, lane_index) to ignore
        
        Returns:
            Dictionary containing interpolated state values
        """
        def get_value_at_time(keyframes, attr_name, immediate_attr, default_val):
            # Filter keyframes that have this attribute set (immediate != -1)
            valid_kfs = [
                (kf.time, getattr(kf, attr_name), getattr(kf, immediate_attr))
                for kf in (keyframes or []) if getattr(kf, immediate_attr) != -1
            ]

            if not valid_kfs:
                return default_val

            # Find the last keyframe at or before current time
            prev_kf = None
            for kf_time, kf_val, kf_interp in valid_kfs:
                if kf_time <= time:
                    prev_kf = (kf_time, kf_val, kf_interp)

            if prev_kf is None:
                return valid_kfs[0][1]  # Return first keyframe value

            # If interpolation is NONE (1) or we're at/past the last keyframe, return current value
            if prev_kf[2] == 1:  # NONE interpolation
                return prev_kf[1]

            # Find next keyframe for interpolation
            next_kf = None
            for kf_time, kf_val, kf_interp in valid_kfs:
                if kf_time > time:
                    next_kf = (kf_time, kf_val, kf_interp)
                    break

            if next_kf is None:
                return prev_kf[1]  # No next keyframe, return current

            # LINEAR interpolation (0)
            if prev_kf[2] == 0:
                time_diff = next_kf[0] - prev_kf[0]
                if time_diff > 0:
                    t = (time - prev_kf[0]) / time_diff
                    return self.lerp(prev_kf[1], next_kf[1], t)

            return prev_kf[1]

        def get_sprite_at_time(keyframes):
            sprite_keyframes = [kf for kf in (keyframes or []) if kf.immediate_sprite != -1]
            prev_sprite = None
            next_sprite = None
            for kf in sprite_keyframes:
                if kf.time <= time:
                    prev_sprite = kf
                elif kf.time > time:
                    next_sprite = kf
                    break
            if prev_sprite:
                sprite_name = (
                    self._resolve_sprite_name(prev_sprite.sprite_name)
                    or prev_sprite.sprite_name
                )
                if prev_sprite.immediate_sprite == 0 and next_sprite:
                    interpolated = self._get_interpolated_sprite_name(prev_sprite, next_sprite, time)
                    if interpolated:
                        sprite_name = interpolated
                return sprite_name
            if sprite_keyframes:
                first_name = (
                    self._resolve_sprite_name(sprite_keyframes[0].sprite_name)
                    or sprite_keyframes[0].sprite_name
                )
                return first_name
            return None

        def lane_has_sprite(keyframes):
            return any(kf.immediate_sprite != -1 for kf in (keyframes or []))

        def lane_has_rgb(keyframes):
            return any(kf.immediate_rgb != -1 for kf in (keyframes or []))

        def eval_base_state(keyframes):
            return {
                'pos_x': get_value_at_time(keyframes, 'pos_x', 'immediate_pos', 0),
                'pos_y': get_value_at_time(keyframes, 'pos_y', 'immediate_pos', 0),
                'depth': get_value_at_time(keyframes, 'depth', 'immediate_depth', 0),
                'scale_x': get_value_at_time(keyframes, 'scale_x', 'immediate_scale', 100),
                'scale_y': get_value_at_time(keyframes, 'scale_y', 'immediate_scale', 100),
                'rotation': get_value_at_time(keyframes, 'rotation', 'immediate_rotation', 0),
                'opacity': get_value_at_time(keyframes, 'opacity', 'immediate_opacity', 100),
                'sprite_name': get_sprite_at_time(keyframes) or '',
                'r': int(get_value_at_time(keyframes, 'r', 'immediate_rgb', 255)),
                'g': int(get_value_at_time(keyframes, 'g', 'immediate_rgb', 255)),
                'b': int(get_value_at_time(keyframes, 'b', 'immediate_rgb', 255)),
                'a': int(get_value_at_time(keyframes, 'a', 'immediate_rgb', 255)),
            }

        if not layer.keyframes:
            base_state = {
                'pos_x': 0, 'pos_y': 0,
                'depth': 0,
                'scale_x': 100, 'scale_y': 100,
                'rotation': 0, 'opacity': 100,
                'sprite_name': '', 'r': 255, 'g': 255, 'b': 255, 'a': 255
            }
        else:
            base_state = eval_base_state(layer.keyframes)

        if not include_additive:
            if getattr(layer, "render_tags", None) and "neutral_color" in layer.render_tags:
                base_state['r'] = base_state['g'] = base_state['b'] = 255
                base_state['a'] = 255
            return base_state

        # Accumulate additive lanes (per-layer + global)
        delta_pos_x = 0.0
        delta_pos_y = 0.0
        delta_depth = 0.0
        delta_scale_x = 0.0
        delta_scale_y = 0.0
        delta_rotation = 0.0
        delta_opacity = 0.0

        sprite_name = base_state.get('sprite_name', '')
        r = int(base_state.get('r', 255))
        g = int(base_state.get('g', 255))
        b = int(base_state.get('b', 255))
        a = int(base_state.get('a', 255))

        exclude_scope: Optional[str] = None
        exclude_layer_id: Optional[int] = None
        exclude_lane_index: Optional[int] = None
        if exclude_lane:
            if isinstance(exclude_lane, tuple) and len(exclude_lane) >= 3:
                exclude_scope, exclude_layer_id, exclude_lane_index = exclude_lane[:3]
            else:
                exclude_scope = getattr(exclude_lane, "scope", None)
                exclude_layer_id = getattr(exclude_lane, "layer_id", None)
                exclude_lane_index = getattr(exclude_lane, "lane_index", None)

        def _should_skip_lane(scope: str, lane_index: int) -> bool:
            if not exclude_scope:
                return False
            return exclude_scope == scope and exclude_layer_id == layer.layer_id and exclude_lane_index == lane_index

        # Per-layer additive lanes
        extra_lanes = getattr(layer, "extra_keyframe_lanes", []) or []
        for idx, lane in enumerate(extra_lanes, start=1):
            if _should_skip_lane("layer", idx):
                continue
            lane_keyframes = getattr(lane, "keyframes", []) or []
            if not lane_keyframes:
                continue
            delta_pos_x += get_value_at_time(lane_keyframes, 'pos_x', 'immediate_pos', 0)
            delta_pos_y += get_value_at_time(lane_keyframes, 'pos_y', 'immediate_pos', 0)
            delta_depth += get_value_at_time(lane_keyframes, 'depth', 'immediate_depth', 0)
            delta_scale_x += get_value_at_time(lane_keyframes, 'scale_x', 'immediate_scale', 0)
            delta_scale_y += get_value_at_time(lane_keyframes, 'scale_y', 'immediate_scale', 0)
            delta_rotation += get_value_at_time(lane_keyframes, 'rotation', 'immediate_rotation', 0)
            delta_opacity += get_value_at_time(lane_keyframes, 'opacity', 'immediate_opacity', 0)
            if lane_has_sprite(lane_keyframes):
                lane_sprite = get_sprite_at_time(lane_keyframes)
                if lane_sprite is not None:
                    sprite_name = lane_sprite
            if lane_has_rgb(lane_keyframes):
                r = int(get_value_at_time(lane_keyframes, 'r', 'immediate_rgb', r))
                g = int(get_value_at_time(lane_keyframes, 'g', 'immediate_rgb', g))
                b = int(get_value_at_time(lane_keyframes, 'b', 'immediate_rgb', b))
                a = int(get_value_at_time(lane_keyframes, 'a', 'immediate_rgb', a))

        # Global additive lanes
        if include_global:
            global_lanes = getattr(self.animation, "global_keyframe_lanes", []) if self.animation else []
            for g_idx, lane in enumerate(global_lanes or []):
                if exclude_scope == "global" and exclude_lane_index == g_idx:
                    continue
                lane_keyframes = getattr(lane, "keyframes", []) or []
                if not lane_keyframes:
                    continue
                delta_pos_x += get_value_at_time(lane_keyframes, 'pos_x', 'immediate_pos', 0)
                delta_pos_y += get_value_at_time(lane_keyframes, 'pos_y', 'immediate_pos', 0)
                delta_depth += get_value_at_time(lane_keyframes, 'depth', 'immediate_depth', 0)
                delta_scale_x += get_value_at_time(lane_keyframes, 'scale_x', 'immediate_scale', 0)
                delta_scale_y += get_value_at_time(lane_keyframes, 'scale_y', 'immediate_scale', 0)
                delta_rotation += get_value_at_time(lane_keyframes, 'rotation', 'immediate_rotation', 0)
                delta_opacity += get_value_at_time(lane_keyframes, 'opacity', 'immediate_opacity', 0)
                if lane_has_sprite(lane_keyframes):
                    lane_sprite = get_sprite_at_time(lane_keyframes)
                    if lane_sprite is not None:
                        sprite_name = lane_sprite
                if lane_has_rgb(lane_keyframes):
                    r = int(get_value_at_time(lane_keyframes, 'r', 'immediate_rgb', r))
                    g = int(get_value_at_time(lane_keyframes, 'g', 'immediate_rgb', g))
                    b = int(get_value_at_time(lane_keyframes, 'b', 'immediate_rgb', b))
                    a = int(get_value_at_time(lane_keyframes, 'a', 'immediate_rgb', a))

        if getattr(layer, "render_tags", None) and "neutral_color" in layer.render_tags:
            r = g = b = 255
            a = 255

        return {
            'pos_x': base_state.get('pos_x', 0) + delta_pos_x,
            'pos_y': base_state.get('pos_y', 0) + delta_pos_y,
            'depth': base_state.get('depth', 0) + delta_depth,
            'scale_x': base_state.get('scale_x', 100) + delta_scale_x,
            'scale_y': base_state.get('scale_y', 100) + delta_scale_y,
            'rotation': base_state.get('rotation', 0) + delta_rotation,
            'opacity': base_state.get('opacity', 100) + delta_opacity,
            'sprite_name': sprite_name or '',
            'r': r, 'g': g, 'b': b, 'a': a,
        }

    def get_global_lane_delta(
        self,
        time: float,
        *,
        exclude_lane: Optional[Tuple[str, int, int]] = None,
    ) -> Dict:
        """
        Return the summed additive deltas from global keyframe lanes at a given time.

        Returns:
            Dict with delta values and optional sprite/RGB overrides:
              pos_x, pos_y, depth, scale_x, scale_y, rotation, opacity,
              sprite_name, has_sprite, r, g, b, a, has_rgb
        """
        def get_value_at_time(keyframes, attr_name, immediate_attr, default_val):
            valid_kfs = [
                (kf.time, getattr(kf, attr_name), getattr(kf, immediate_attr))
                for kf in (keyframes or []) if getattr(kf, immediate_attr) != -1
            ]
            if not valid_kfs:
                return default_val
            prev_kf = None
            for kf_time, kf_val, kf_interp in valid_kfs:
                if kf_time <= time:
                    prev_kf = (kf_time, kf_val, kf_interp)
            if prev_kf is None:
                return valid_kfs[0][1]
            if prev_kf[2] == 1:
                return prev_kf[1]
            next_kf = None
            for kf_time, kf_val, kf_interp in valid_kfs:
                if kf_time > time:
                    next_kf = (kf_time, kf_val, kf_interp)
                    break
            if next_kf is None:
                return prev_kf[1]
            if prev_kf[2] == 0:
                time_diff = next_kf[0] - prev_kf[0]
                if time_diff > 0:
                    t = (time - prev_kf[0]) / time_diff
                    return self.lerp(prev_kf[1], next_kf[1], t)
            return prev_kf[1]

        def get_sprite_at_time(keyframes):
            sprite_keyframes = [kf for kf in (keyframes or []) if kf.immediate_sprite != -1]
            prev_sprite = None
            next_sprite = None
            for kf in sprite_keyframes:
                if kf.time <= time:
                    prev_sprite = kf
                elif kf.time > time:
                    next_sprite = kf
                    break
            if prev_sprite:
                sprite_name = (
                    self._resolve_sprite_name(prev_sprite.sprite_name)
                    or prev_sprite.sprite_name
                )
                if prev_sprite.immediate_sprite == 0 and next_sprite:
                    interpolated = self._get_interpolated_sprite_name(prev_sprite, next_sprite, time)
                    if interpolated:
                        sprite_name = interpolated
                return sprite_name
            if sprite_keyframes:
                first_name = (
                    self._resolve_sprite_name(sprite_keyframes[0].sprite_name)
                    or sprite_keyframes[0].sprite_name
                )
                return first_name
            return None

        def lane_has_sprite(keyframes):
            return any(kf.immediate_sprite != -1 for kf in (keyframes or []))

        def lane_has_rgb(keyframes):
            return any(kf.immediate_rgb != -1 for kf in (keyframes or []))

        delta_pos_x = 0.0
        delta_pos_y = 0.0
        delta_depth = 0.0
        delta_scale_x = 0.0
        delta_scale_y = 0.0
        delta_rotation = 0.0
        delta_opacity = 0.0

        sprite_name: Optional[str] = None
        has_sprite = False
        r = 255
        g = 255
        b = 255
        a = 255
        has_rgb = False

        exclude_scope: Optional[str] = None
        exclude_layer_id: Optional[int] = None
        exclude_lane_index: Optional[int] = None
        if exclude_lane:
            if isinstance(exclude_lane, tuple) and len(exclude_lane) >= 3:
                exclude_scope, exclude_layer_id, exclude_lane_index = exclude_lane[:3]
            else:
                exclude_scope = getattr(exclude_lane, "scope", None)
                exclude_layer_id = getattr(exclude_lane, "layer_id", None)
                exclude_lane_index = getattr(exclude_lane, "lane_index", None)

        global_lanes = getattr(self.animation, "global_keyframe_lanes", []) if self.animation else []
        for g_idx, lane in enumerate(global_lanes or []):
            if exclude_scope == "global" and exclude_lane_index == g_idx:
                continue
            lane_keyframes = getattr(lane, "keyframes", []) or []
            if not lane_keyframes:
                continue
            delta_pos_x += get_value_at_time(lane_keyframes, 'pos_x', 'immediate_pos', 0)
            delta_pos_y += get_value_at_time(lane_keyframes, 'pos_y', 'immediate_pos', 0)
            delta_depth += get_value_at_time(lane_keyframes, 'depth', 'immediate_depth', 0)
            delta_scale_x += get_value_at_time(lane_keyframes, 'scale_x', 'immediate_scale', 0)
            delta_scale_y += get_value_at_time(lane_keyframes, 'scale_y', 'immediate_scale', 0)
            delta_rotation += get_value_at_time(lane_keyframes, 'rotation', 'immediate_rotation', 0)
            delta_opacity += get_value_at_time(lane_keyframes, 'opacity', 'immediate_opacity', 0)
            if lane_has_sprite(lane_keyframes):
                lane_sprite = get_sprite_at_time(lane_keyframes)
                if lane_sprite is not None:
                    sprite_name = lane_sprite
                    has_sprite = True
            if lane_has_rgb(lane_keyframes):
                r = int(get_value_at_time(lane_keyframes, 'r', 'immediate_rgb', r))
                g = int(get_value_at_time(lane_keyframes, 'g', 'immediate_rgb', g))
                b = int(get_value_at_time(lane_keyframes, 'b', 'immediate_rgb', b))
                a = int(get_value_at_time(lane_keyframes, 'a', 'immediate_rgb', a))
                has_rgb = True

        return {
            'pos_x': delta_pos_x,
            'pos_y': delta_pos_y,
            'depth': delta_depth,
            'scale_x': delta_scale_x,
            'scale_y': delta_scale_y,
            'rotation': delta_rotation,
            'opacity': delta_opacity,
            'sprite_name': sprite_name,
            'has_sprite': has_sprite,
            'r': r,
            'g': g,
            'b': b,
            'a': a,
            'has_rgb': has_rgb,
        }

    def get_layer_state_base(self, layer: LayerData, time: float) -> Dict:
        """Return layer state using only the base lane (no additive/global lanes)."""
        return self.get_layer_state(layer, time, include_additive=False)
    
    @staticmethod
    def lerp(a: float, b: float, t: float) -> float:
        """
        Linear interpolation between two values
        
        Args:
            a: Start value
            b: End value
            t: Interpolation factor (0-1)
        
        Returns:
            Interpolated value
        """
        return a + (b - a) * t
    
    def _get_interpolated_sprite_name(
        self,
        prev_kf: KeyframeData,
        next_kf: KeyframeData,
        time: float
    ) -> Optional[str]:
        """
        Return an interpolated sprite name when keyframes define a numeric range.
        """
        if not prev_kf.sprite_name or not next_kf.sprite_name:
            return None
        if next_kf.time <= prev_kf.time:
            return None
        
        match_prev = self._sprite_suffix_pattern.match(prev_kf.sprite_name)
        match_next = self._sprite_suffix_pattern.match(next_kf.sprite_name)
        if not match_prev or not match_next:
            return None
        if match_prev.group(1) != match_next.group(1):
            return None
        
        start_idx = int(match_prev.group(2))
        end_idx = int(match_next.group(2))
        if start_idx == end_idx:
            return self._resolve_sprite_name(prev_kf.sprite_name) or prev_kf.sprite_name
        
        duration = next_kf.time - prev_kf.time
        if duration <= 0:
            return self._resolve_sprite_name(prev_kf.sprite_name) or prev_kf.sprite_name
        
        ratio = (time - prev_kf.time) / duration
        ratio = max(0.0, min(0.9999, ratio))
        
        span = end_idx - start_idx
        steps = abs(span)
        if steps == 0:
            return self._resolve_sprite_name(prev_kf.sprite_name) or prev_kf.sprite_name
        
        advance = min(steps - 1, int(ratio * steps))
        if span > 0:
            candidate_idx = start_idx + advance
        else:
            candidate_idx = start_idx - advance

        # Preserve leading zeros from the suffix so atlas names continue to match
        suffix_width = len(match_prev.group(2))
        formatted_idx = str(candidate_idx).zfill(suffix_width)
        prefix = match_prev.group(1)
        interpolated_name = f"{prefix}{formatted_idx}"
        resolved = self._resolve_sprite_name(interpolated_name)
        if resolved:
            return resolved
        fallback = self._resolve_sprite_name(prev_kf.sprite_name)
        return fallback or prev_kf.sprite_name

    def set_playback_speed(self, speed: float):
        """Adjust playback speed multiplier (>0)."""
        if speed <= 0:
            speed = 0.01
        self.playback_speed = speed
