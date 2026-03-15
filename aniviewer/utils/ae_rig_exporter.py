"""
After Effects rig exporter.
Creates a JSON manifest plus assets that can be rebuilt in AE to match the viewer.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from PIL import Image, ImageDraw
import soundfile as sf

from core.data_structures import LayerData, SpriteInfo
from core.texture_atlas import TextureAtlas
from renderer.sprite_renderer import BlendMode
from OpenGL.GL import GL_MAX_TEXTURE_SIZE, glGetIntegerv


class AERigExporter:
    """Export the current viewer animation into an AE-friendly rig package."""

    EXPORT_VERSION = 1
    _sprite_suffix_pattern = re.compile(r"^(.*?)(\d+)$")

    def __init__(self, main_window):
        self.main_window = main_window
        self.gl_widget = main_window.gl_widget
        self.player = self.gl_widget.player
        self.renderer = self.gl_widget.renderer
        self.shader_registry = getattr(main_window, "shader_registry", None)
        self._sprite_asset_cache: Dict[Tuple, Dict[str, Any]] = {}
        self._sprite_lookup_cache: Dict[
            Tuple[str, Tuple[str, ...]],
            Tuple[Optional[SpriteInfo], Optional[TextureAtlas], str],
        ] = {}
        self._sprite_anchor_cache: Dict[Tuple[int, str], Optional[Tuple[float, float]]] = {}
        self._warnings: List[str] = []
        self._original_visibility: Dict[int, bool] = {}
        self._original_tile_batches: List[Any] = []
        self._original_attachments: List[Any] = []
        self._original_world_offset: Tuple[float, float] = (0.0, 0.0)
        self._ae_rig_mode: str = "auto"
        self._ae_mesh_mode: bool = False
        self._ae_output_scale: float = 1.0
        self._ae_quality: str = "balanced"
        self._ae_preserve_resolution: bool = False
        self._ae_full_res_multiplier: float = 1.0
        self._ae_match_viewport: bool = True
        self._ae_compression: str = "rle"
        self._max_texture_size: Optional[int] = None
        self._baked_render_scale: float = 1.0
        self._baked_render_size: Optional[Tuple[int, int]] = None

    def export(self, target_root: str) -> Tuple[bool, str]:
        """Export the active animation into an AE rig package."""
        animation = self.player.animation
        if not animation:
            return False, "No animation loaded."

        costume_snapshot = self._apply_costume_export_overrides(animation.layers)

        fps = max(1, int(self.main_window.control_panel.fps_spin.value()))
        playback_speed = float(self.main_window._get_export_playback_speed())
        real_duration = float(self.main_window._get_export_real_duration())
        frame_count = max(1, int(math.ceil(real_duration * fps)))

        export_settings = getattr(self.main_window, "export_settings", None)
        if export_settings:
            self._ae_rig_mode = getattr(export_settings, "ae_rig_mode", "auto") or "auto"
            self._ae_quality = getattr(export_settings, "ae_quality", "balanced") or "balanced"
            self._ae_preserve_resolution = bool(
                getattr(export_settings, "ae_preserve_resolution", False)
            )
            self._ae_full_res_multiplier = float(
                getattr(export_settings, "ae_full_res_multiplier", 1.0) or 1.0
            )
            self._ae_match_viewport = bool(
                getattr(export_settings, "ae_match_viewport", True)
            )
            self._ae_compression = getattr(export_settings, "ae_compression", "rle") or "rle"
            scale_percent = int(getattr(export_settings, "ae_scale", 100) or 100)
        else:
            self._ae_rig_mode = "auto"
            self._ae_quality = "balanced"
            self._ae_preserve_resolution = False
            self._ae_full_res_multiplier = 1.0
            self._ae_match_viewport = True
            self._ae_compression = "rle"
            scale_percent = 100
        self._ae_rig_mode = str(self._ae_rig_mode).lower()
        if self._ae_rig_mode not in ("auto", "rig", "mesh", "composite"):
            self._ae_rig_mode = "auto"
        self._ae_mesh_mode = self._ae_rig_mode == "mesh"
        self._ae_quality = str(self._ae_quality).lower()
        if self._ae_quality not in ("fast", "balanced", "high", "maximum"):
            self._ae_quality = "balanced"
        self._ae_compression = str(self._ae_compression).lower()
        if self._ae_compression not in ("raw", "rle"):
            self._ae_compression = "rle"
        if not math.isfinite(self._ae_full_res_multiplier) or self._ae_full_res_multiplier <= 0:
            self._ae_full_res_multiplier = 1.0
        scale_percent = max(25, min(400, int(scale_percent)))
        export_scale = scale_percent / 100.0
        full_res_scale = (
            self.main_window._get_full_resolution_scale()
            if self._ae_preserve_resolution
            else 1.0
        )
        native_scale = full_res_scale * max(1.0, self._ae_full_res_multiplier) if self._ae_preserve_resolution else 1.0
        if self._ae_preserve_resolution:
            export_scale = 1.0
            self._ae_match_viewport = False
        self._ae_output_scale = export_scale * native_scale
        if not math.isfinite(self._ae_output_scale) or self._ae_output_scale <= 0:
            self._ae_output_scale = 1.0
        self._ae_output_scale = max(0.25, min(32.0, float(self._ae_output_scale)))

        base_name = animation.name or getattr(self.main_window, "current_animation_name", "") or "animation"
        export_root = self.main_window._create_unique_export_folder(target_root, f"{base_name}_AE")

        sprites_dir = os.path.join(export_root, "sprites")
        baked_dir = os.path.join(export_root, "baked")
        audio_dir = os.path.join(export_root, "audio")
        os.makedirs(sprites_dir, exist_ok=True)
        os.makedirs(baked_dir, exist_ok=True)

        self._original_visibility = {layer.layer_id: layer.visible for layer in animation.layers}
        self._original_tile_batches = list(self.gl_widget.tile_batches)
        self._original_attachments = list(self.gl_widget.attachment_instances)
        self._original_world_offset = (self.renderer.world_offset_x, self.renderer.world_offset_y)

        original_time = self.player.current_time
        original_playing = bool(self.player.playing)
        original_mask_role = {layer.layer_id: layer.mask_role for layer in animation.layers}

        self.main_window._set_player_playing(False, sync_audio=False)

        try:
            layer_map = {layer.layer_id: layer for layer in animation.layers}
            layer_atlas_chain = self._build_layer_atlas_chains(animation.layers)
            pivot_context = getattr(self.gl_widget, "layer_pivot_context", {}) or {}

            mask_consumer_map = self._build_mask_consumer_map(animation.layers)

            sprite_timelines: Dict[int, List[Dict[str, Any]]] = {}
            sprite_sets: Dict[int, Set[str]] = {}
            sprite_first_times: Dict[int, Dict[str, float]] = {}

            for layer in animation.layers:
                chain = layer_atlas_chain.get(layer.layer_id, [])
                keys, used, first_times = self._sample_sprite_timeline(
                    layer, chain, fps, frame_count
                )
                sprite_timelines[layer.layer_id] = keys
                sprite_sets[layer.layer_id] = used
                sprite_first_times[layer.layer_id] = first_times

            layer_modes: Dict[int, str] = {}
            static_color: Dict[int, Tuple[Tuple[float, float, float], float, float]] = {}
            layer_blends: Dict[int, int] = {}

            for layer in animation.layers:
                used_sprites = sprite_sets.get(layer.layer_id, set())
                chain = layer_atlas_chain.get(layer.layer_id, [])
                shader_behavior = self._get_shader_behavior(layer.shader_name)
                shader_preset = self._get_shader_preset(layer.shader_name)
                layer_blends[layer.layer_id] = self._resolve_blend_mode(layer, shader_preset)

                dynamic_rgb = self._layer_has_dynamic_rgb(layer)
                dynamic_tint = bool(layer.color_animator or layer.color_gradient)
                needs_baked_image = dynamic_rgb or dynamic_tint or shader_behavior is not None
                uses_mesh = self._layer_uses_polygon_mesh(used_sprites, chain)
                if uses_mesh and not self._ae_mesh_mode:
                    needs_baked_image = True

                if layer.mask_role == "mask_source":
                    if needs_baked_image:
                        self._warnings.append(
                            f"Mask source '{layer.name}' uses dynamic effects; exporting as matte-only geometry."
                        )
                    needs_baked_image = False

                needs_baked_transform = self._layer_needs_baked_transform(layer)

                if not used_sprites:
                    mode = "null"
                elif needs_baked_image:
                    mode = "baked_image"
                elif needs_baked_transform:
                    mode = "baked_transform"
                else:
                    mode = "rig"
                layer_modes[layer.layer_id] = mode

                rgb_factor, alpha_factor = self._resolve_static_color(layer, shader_preset)
                preset_alpha = float(shader_preset.alpha_scale) if shader_preset else 1.0
                static_color[layer.layer_id] = (rgb_factor, alpha_factor, preset_alpha)

            self._propagate_baked_transforms(layer_modes, animation.layers)

            baked_image_layers = [
                layer for layer in animation.layers
                if layer_modes.get(layer.layer_id) == "baked_image"
            ]
            renderable_layers = [
                layer for layer in animation.layers
                if layer_modes.get(layer.layer_id) not in ("null",)
            ]
            visible_renderable = [layer for layer in renderable_layers if layer.visible]
            renderable_count = len(visible_renderable) or len(renderable_layers) or len(animation.layers)
            baked_ratio = len(baked_image_layers) / float(renderable_count) if renderable_count else 0.0
            baked_work = len(baked_image_layers) * frame_count
            ae_mode = self._ae_rig_mode
            if ae_mode == "composite":
                composite_only = True
                self._warnings.append(
                    "AE rig export forced to composite bake; per-layer rigging will be skipped."
                )
            elif ae_mode == "rig":
                composite_only = False
                if bool(baked_image_layers) and baked_ratio >= 0.7 and baked_work > 10000:
                    self._warnings.append(
                        "AE rig export forced to rig mode; polygon mesh layers will bake per-layer and may be slow."
                    )
            elif ae_mode == "mesh":
                composite_only = False
                if bool(baked_image_layers) and baked_ratio >= 0.7 and baked_work > 10000:
                    self._warnings.append(
                        "AE rig export set to mesh mode; triangle rigs will be generated for polygon sprites "
                        "and may create very large AE comps."
                    )
            else:
                composite_only = bool(baked_image_layers) and baked_ratio >= 0.7 and baked_work > 10000
            composite_sequence: Optional[Dict[str, Any]] = None

            manifest_layers: List[Dict[str, Any]] = []
            baked_layers: List[Dict[str, Any]] = []

            if composite_only:
                composite_dir = os.path.join(baked_dir, "composite")
                composite_sequence = {
                    "folder": os.path.relpath(composite_dir, export_root).replace("\\", "/"),
                    "pattern": "frame_%05d.png",
                    "frame_count": frame_count,
                }
                self._warnings.append(
                    "Composite bake enabled: polygon mesh layers dominate this animation; "
                    "exporting a single baked sequence to avoid an extremely long per-layer bake."
                )
                self._render_composite_sequence(
                    composite_dir,
                    fps,
                    frame_count,
                )
                manifest_layers.append(
                    {
                        "id": -9999,
                        "name": "Baked_Composite",
                        "parent_id": -1,
                        "stack_index": 0,
                        "blend_mode": BlendMode.STANDARD,
                        "visible": True,
                        "export_mode": "baked_image",
                        "mask_role": None,
                        "mask_key": None,
                        "mask_source_id": None,
                        "mask_only": False,
                        "layer_anchor": [0.0, 0.0],
                        "precomp": None,
                        "sprite_keys": [],
                        "transform": None,
                        "baked_sequence": composite_sequence,
                    }
                )

            if not composite_only:
                for idx, layer in enumerate(animation.layers):
                    mode = layer_modes.get(layer.layer_id, "rig")
                    chain = layer_atlas_chain.get(layer.layer_id, [])
                    used_sprites = sprite_sets.get(layer.layer_id, set())
                    sprite_keys = list(sprite_timelines.get(layer.layer_id, []))
                    first_times = sprite_first_times.get(layer.layer_id, {})
                    rgb_factor, alpha_factor, preset_alpha = static_color.get(
                        layer.layer_id,
                        ((1.0, 1.0, 1.0), 1.0, 1.0),
                    )

                    mask_only = layer.mask_role == "mask_source"
                    precomp = None
                    sprite_asset_map: Dict[str, str] = {}
                    if mode in ("rig", "baked_transform") and used_sprites:
                        precomp, sprite_asset_map = self._build_layer_precomp(
                            layer,
                            used_sprites,
                            first_times,
                            chain,
                            rgb_factor,
                            mask_only,
                            sprites_dir,
                            layer_map,
                            pivot_context,
                        )
                        sprite_keys = self._map_sprite_keys(sprite_keys, sprite_asset_map)

                    transform_keys = None
                    if mode in ("rig", "null"):
                        transform_keys = self._build_rig_transform_keys(
                            layer,
                            alpha_factor,
                            playback_speed,
                        )
                    elif mode == "baked_transform":
                        transform_keys = self._build_baked_transform_keys(
                            layer,
                            fps,
                            frame_count,
                            preset_alpha,
                            self._original_world_offset,
                        )

                    baked_sequence = None
                    if mode == "baked_image":
                        layer_dir = os.path.join(baked_dir, f"layer_{layer.layer_id}")
                        baked_sequence = {
                            "folder": os.path.relpath(layer_dir, export_root).replace("\\", "/"),
                            "pattern": "frame_%05d.png",
                            "frame_count": frame_count,
                        }
                        baked_layers.append(
                            {
                                "layer": layer,
                                "folder": layer_dir,
                                "mask_source_id": mask_consumer_map.get(layer.layer_id),
                            }
                        )

                    entry = {
                        "id": layer.layer_id,
                        "name": layer.name,
                        "parent_id": layer.parent_id,
                        "stack_index": idx,
                        "blend_mode": layer_blends.get(layer.layer_id, layer.blend_mode),
                        "visible": bool(layer.visible),
                        "export_mode": mode,
                        "mask_role": layer.mask_role,
                        "mask_key": layer.mask_key,
                        "mask_source_id": mask_consumer_map.get(layer.layer_id),
                        "mask_only": mask_only,
                        "layer_anchor": list(self._get_layer_anchor(layer)),
                        "precomp": precomp,
                        "sprite_keys": sprite_keys if precomp else [],
                        "transform": transform_keys,
                        "baked_sequence": baked_sequence,
                    }
                    manifest_layers.append(entry)

            if baked_layers:
                self._prepare_baked_render_scale()
                self._render_baked_sequences(
                    baked_layers,
                    baked_dir,
                    fps,
                    frame_count,
                )

            background_entry = self._export_tile_background(export_root, baked_dir)

            attachments: List[Dict[str, Any]] = []
            if not composite_only:
                attachments = self._export_attachments(
                    export_root,
                    sprites_dir,
                    baked_dir,
                    fps,
                    frame_count,
                )

            audio_entry = None
            if self.main_window.audio_manager.is_ready:
                try:
                    audio_speed, audio_mode = self.main_window._get_audio_export_config()
                    audio_segment = self.main_window.audio_manager.export_audio_segment(
                        real_duration,
                        speed=audio_speed,
                        pitch_mode=audio_mode,
                    )
                    if audio_segment:
                        samples, sample_rate = audio_segment
                        os.makedirs(audio_dir, exist_ok=True)
                        audio_path = os.path.join(audio_dir, "audio_track.wav")
                        sf.write(audio_path, samples, sample_rate)
                        audio_entry = {
                            "path": os.path.relpath(audio_path, export_root).replace("\\", "/"),
                            "sample_rate": sample_rate,
                            "duration": len(samples) / sample_rate if sample_rate else 0.0,
                        }
                except Exception as exc:
                    self._warnings.append(f"Failed to export audio track: {exc}")

            viewport_width, viewport_height = self._scaled_viewport_size()
            render_scale = self.gl_widget.render_scale if self._ae_match_viewport else 1.0
            camera_base_x = self.gl_widget.camera_x if self._ae_match_viewport else 0.0
            camera_base_y = self.gl_widget.camera_y if self._ae_match_viewport else 0.0
            camera_x = camera_base_x * self._ae_output_scale
            camera_y = camera_base_y * self._ae_output_scale
            world_offset_x = self._original_world_offset[0] * self._ae_output_scale
            world_offset_y = self._original_world_offset[1] * self._ae_output_scale
            manifest = {
                "version": self.EXPORT_VERSION,
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "animation": {
                    "name": animation.name,
                    "fps": fps,
                    "duration": real_duration,
                    "frame_count": frame_count,
                    "playback_speed": playback_speed,
                    "centered": bool(animation.centered),
                    "viewport_width": viewport_width,
                    "viewport_height": viewport_height,
                    "render_scale": render_scale,
                    "camera_x": camera_x,
                    "camera_y": camera_y,
                    "world_offset_x": world_offset_x,
                    "world_offset_y": world_offset_y,
                    "baked_scale": self._baked_render_scale,
                },
                "viewer_state": {
                    "position_scale": self.renderer.position_scale,
                    "base_world_scale": self.renderer.base_world_scale,
                    "local_position_multiplier": self.renderer.local_position_multiplier,
                    "anchor_bias_x": self.renderer.anchor_bias_x,
                    "anchor_bias_y": self.renderer.anchor_bias_y,
                    "rotation_bias": self.renderer.rotation_bias,
                    "scale_bias_x": self.renderer.scale_bias_x,
                    "scale_bias_y": self.renderer.scale_bias_y,
                    "trim_shift_multiplier": self.renderer.trim_shift_multiplier,
                    "parent_mix": self.renderer.parent_mix,
                },
                "sprites": list(self._sprite_asset_cache.values()),
                "layers": manifest_layers,
                "attachments": attachments,
                "background": background_entry,
                "composite_sequence": composite_sequence,
                "audio": audio_entry,
            }

            manifest_path = os.path.join(export_root, "ae_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=2)

            self._write_readme(export_root)
            self._write_report(export_root)
            self._copy_import_script(export_root)

        except Exception as exc:
            self._write_report(export_root)
            return False, f"AE rig export failed: {exc}"
        finally:
            for layer in animation.layers:
                if layer.layer_id in self._original_visibility:
                    layer.visible = self._original_visibility[layer.layer_id]
                if layer.layer_id in original_mask_role:
                    layer.mask_role = original_mask_role[layer.layer_id]
            self.gl_widget.tile_batches = self._original_tile_batches
            self.gl_widget.attachment_instances = self._original_attachments
            self.renderer.world_offset_x = self._original_world_offset[0]
            self.renderer.world_offset_y = self._original_world_offset[1]
            self.player.current_time = original_time
            self.main_window._set_player_playing(original_playing, sync_audio=False)
            if costume_snapshot:
                self._restore_costume_export_overrides(costume_snapshot)
            self.gl_widget.update()

        return True, f"AE rig export complete: {export_root}"

    # ----------------------------------------------------------------- helpers
    def _apply_costume_export_overrides(
        self,
        layers: List[LayerData]
    ) -> Optional[Dict[str, Any]]:
        get_entry = getattr(self.main_window, "_get_current_costume_entry", None)
        if not callable(get_entry):
            return None
        entry = get_entry()
        if not entry:
            return None
        load_costume = getattr(self.main_window, "_load_costume_definition", None)
        if not callable(load_costume):
            return None
        costume_data = load_costume(entry)
        if not costume_data:
            return None

        remap_map, sheet_names = self.main_window._build_remap_map(costume_data.get("remaps", []))
        sheet_alias, alias_targets = self.main_window._normalize_sheet_remaps(
            costume_data.get("sheet_remaps") or costume_data.get("swaps", [])
        )
        sheet_names.update(alias_targets)

        layer_remap_overrides: Dict[int, Dict[str, Any]] = {}
        if remap_map:
            for layer in layers:
                name = (layer.name or "").strip()
                if not name:
                    continue
                lower = name.lower()
                if lower in remap_map:
                    continue
                normalized = self.main_window._normalize_layer_label(name)
                if normalized:
                    info = remap_map.get(normalized.lower())
                    if info:
                        layer_remap_overrides[layer.layer_id] = info
                        continue
                overlay_ref = self.main_window._overlay_reference_name(layer)
                if overlay_ref:
                    info = remap_map.get(overlay_ref.lower())
                    if info:
                        layer_remap_overrides[layer.layer_id] = info

        costume_atlases = self.main_window._load_costume_atlases(sheet_names)
        if sheet_names and not costume_atlases:
            self._warnings.append(
                f"Costume atlases missing for '{entry.display_name}'."
            )

        base_atlases = list(self.gl_widget.texture_atlases or [])
        combined_atlases: List[TextureAtlas] = []
        for atlas in costume_atlases + base_atlases:
            if atlas and atlas not in combined_atlases:
                combined_atlases.append(atlas)

        overrides, pivot_context = self.main_window._build_layer_atlas_overrides(
            layers,
            remap_map,
            layer_remap_overrides,
            costume_atlases,
            sheet_alias,
        )

        snapshot = {
            "texture_atlases": list(self.gl_widget.texture_atlases or []),
            "layer_atlas_overrides": dict(getattr(self.gl_widget, "layer_atlas_overrides", {}) or {}),
            "layer_pivot_context": dict(getattr(self.gl_widget, "layer_pivot_context", {}) or {}),
        }

        if combined_atlases:
            self.gl_widget.texture_atlases = combined_atlases
        self.gl_widget.set_layer_atlas_overrides(overrides)
        self.gl_widget.set_layer_pivot_context(pivot_context)
        return snapshot

    def _restore_costume_export_overrides(self, snapshot: Dict[str, Any]) -> None:
        self.gl_widget.texture_atlases = list(snapshot.get("texture_atlases", []))
        self.gl_widget.set_layer_atlas_overrides(snapshot.get("layer_atlas_overrides", {}))
        self.gl_widget.set_layer_pivot_context(snapshot.get("layer_pivot_context", {}))

    def _prepare_baked_render_scale(self) -> None:
        if self._baked_render_size is not None:
            return
        width, height = self._scaled_viewport_size()
        max_size = self._get_max_texture_size()
        scale = 1.0
        if max_size and (width > max_size or height > max_size):
            scale = min(max_size / float(width), max_size / float(height))
        if scale <= 0.0 or not math.isfinite(scale):
            scale = 1.0
        if scale < 1.0:
            scaled_w = max(1, int(math.floor(width * scale)))
            scaled_h = max(1, int(math.floor(height * scale)))
            self._baked_render_size = (scaled_w, scaled_h)
            self._baked_render_scale = scale
            self._warnings.append(
                f"Baked render scaled to {scaled_w}x{scaled_h} (scale {scale:.3f}) "
                f"to fit GL max texture {max_size}."
            )
        else:
            self._baked_render_size = (width, height)
            self._baked_render_scale = 1.0

    def _get_max_texture_size(self) -> Optional[int]:
        if self._max_texture_size is not None:
            return self._max_texture_size
        try:
            self.gl_widget.makeCurrent()
            value = glGetIntegerv(GL_MAX_TEXTURE_SIZE)
        except Exception:
            self._max_texture_size = None
            return None
        finally:
            try:
                self.gl_widget.doneCurrent()
            except Exception:
                pass
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        try:
            self._max_texture_size = int(value) if value else None
        except (TypeError, ValueError):
            self._max_texture_size = None
        return self._max_texture_size

    def _build_layer_atlas_chains(self, layers: List[LayerData]) -> Dict[int, List[TextureAtlas]]:
        chains: Dict[int, List[TextureAtlas]] = {}
        base_atlases = list(self.gl_widget.texture_atlases or [])
        overrides = getattr(self.gl_widget, "layer_atlas_overrides", {}) or {}
        for layer in layers:
            chain = list(overrides.get(layer.layer_id, []) or [])
            if base_atlases:
                chain.extend(base_atlases)
            chains[layer.layer_id] = chain
        return chains

    def _build_mask_consumer_map(self, layers: List[LayerData]) -> Dict[int, int]:
        """Return a mapping of mask consumer layer id -> mask source layer id."""
        pending: Dict[str, int] = {}
        result: Dict[int, int] = {}
        for layer in reversed(layers):
            if not layer.visible:
                continue
            if layer.mask_role == "mask_source" and layer.mask_key:
                pending[layer.mask_key] = layer.layer_id
            elif layer.mask_role == "mask_consumer" and layer.mask_key:
                source_id = pending.get(layer.mask_key)
                if source_id is not None:
                    result[layer.layer_id] = source_id
                    pending.pop(layer.mask_key, None)
        return result

    def _sample_sprite_timeline(
        self,
        layer: LayerData,
        atlas_chain: List[TextureAtlas],
        fps: int,
        frame_count: int
    ) -> Tuple[List[Dict[str, Any]], Set[str], Dict[str, float]]:
        keys: List[Dict[str, Any]] = []
        used: Set[str] = set()
        first_times: Dict[str, float] = {}
        last_sprite: Optional[str] = None
        missing: Set[str] = set()

        sprite_keyframes = [
            kf for kf in layer.keyframes
            if getattr(kf, "immediate_sprite", -1) != -1
        ]
        sprite_keyframes.sort(key=lambda kf: kf.time)

        inv_speed = 1.0 / max(1e-6, self.main_window._get_export_playback_speed())

        def resolve_sprite_name(raw_name: str, anim_time: float) -> Optional[str]:
            if not raw_name:
                return None
            sprite, _, resolved = self._resolve_sprite(raw_name, atlas_chain)
            if not sprite:
                if raw_name not in missing:
                    missing.add(raw_name)
                    self._warnings.append(
                        f"Missing sprite '{raw_name}' on layer '{layer.name}'."
                    )
                return None
            used.add(resolved)
            if resolved not in first_times:
                first_times[resolved] = anim_time
            return resolved

        def split_suffix(name: str) -> Tuple[str, Optional[int], int]:
            match = self._sprite_suffix_pattern.match(name)
            if not match:
                return name, None, 0
            digits = match.group(2)
            return match.group(1), int(digits), len(digits)

        def expand_numeric_span(prev_name: str, next_name: str, start: float, end: float) -> List[Tuple[float, str]]:
            if not prev_name or not next_name or end <= start:
                return []
            base_a, num_a, width_a = split_suffix(prev_name)
            base_b, num_b, width_b = split_suffix(next_name)
            if base_a != base_b or num_a is None or num_b is None:
                return []
            gap = num_b - num_a
            steps = abs(gap)
            if steps <= 1:
                return []
            step_dir = 1 if gap > 0 else -1
            width = width_a if width_a else width_b
            results: List[Tuple[float, str]] = []
            span = end - start
            for step in range(1, steps):
                t = start + span * (step / float(steps))
                next_idx = num_a + step_dir * step
                if width:
                    sprite_name = f"{base_a}{next_idx:0{width}d}"
                else:
                    sprite_name = f"{base_a}{next_idx}"
                results.append((t, sprite_name))
            return results

        if sprite_keyframes:
            for idx, keyframe in enumerate(sprite_keyframes):
                sprite_name = keyframe.sprite_name or ""
                resolved_name = resolve_sprite_name(sprite_name, keyframe.time)
                if resolved_name != last_sprite:
                    keys.append({"time": keyframe.time * inv_speed, "sprite_id": resolved_name})
                    last_sprite = resolved_name

                next_keyframe = sprite_keyframes[idx + 1] if idx + 1 < len(sprite_keyframes) else None
                if not next_keyframe:
                    continue
                if keyframe.immediate_sprite == 0:
                    expanded = expand_numeric_span(
                        sprite_name,
                        next_keyframe.sprite_name or "",
                        keyframe.time,
                        next_keyframe.time,
                    )
                    for anim_time, expanded_name in expanded:
                        resolved_name = resolve_sprite_name(expanded_name, anim_time)
                        if resolved_name != last_sprite:
                            keys.append({"time": anim_time * inv_speed, "sprite_id": resolved_name})
                            last_sprite = resolved_name
        else:
            for frame_idx in range(frame_count):
                anim_time = self.main_window._get_export_frame_time(frame_idx, fps)
                state = self.player.get_layer_state(layer, anim_time)
                sprite_name = state.get("sprite_name") or ""
                resolved_name = resolve_sprite_name(sprite_name, anim_time)
                if resolved_name != last_sprite:
                    keys.append({"time": frame_idx / float(fps), "sprite_id": resolved_name})
                    last_sprite = resolved_name

        if not keys:
            keys.append({"time": 0.0, "sprite_id": None})
        elif keys[0]["time"] > 0.0:
            keys.insert(0, {"time": 0.0, "sprite_id": keys[0]["sprite_id"]})
        return keys, used, first_times

    def _map_sprite_keys(
        self,
        keys: List[Dict[str, Any]],
        sprite_map: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        mapped: List[Dict[str, Any]] = []
        for key in keys:
            name = key.get("sprite_id")
            asset_id = sprite_map.get(name) if name else None
            mapped.append({"time": key.get("time", 0.0), "sprite_id": asset_id})
        return mapped

    def _layer_uses_polygon_mesh(
        self,
        sprite_names: Set[str],
        atlas_chain: List[TextureAtlas]
    ) -> bool:
        for sprite_name in sprite_names:
            sprite, _, _ = self._resolve_sprite(sprite_name, atlas_chain)
            if sprite and sprite.has_polygon_mesh:
                return True
        return False

    def _layer_has_dynamic_rgb(self, layer: LayerData) -> bool:
        if layer.render_tags and "neutral_color" in layer.render_tags:
            return False
        values: List[Tuple[int, int, int, int]] = []
        for key in layer.keyframes:
            if getattr(key, "immediate_rgb", -1) != -1:
                values.append((key.r, key.g, key.b, key.a))
        if len(values) <= 1:
            return False
        first = values[0]
        return any(value != first for value in values[1:])

    def _layer_needs_baked_transform(self, layer: LayerData) -> bool:
        parent_mix = getattr(self.renderer, "parent_mix", 1.0)
        if layer.parent_id >= 0 and abs(parent_mix - 1.0) > 1e-5:
            self._warnings.append(
                f"Layer '{layer.name}' baked: parent_mix {parent_mix:.2f} not supported in AE."
            )
            return True
        offset = self.gl_widget.layer_offsets.get(layer.layer_id, (0.0, 0.0))
        if abs(offset[0]) > 1e-4 or abs(offset[1]) > 1e-4:
            self._warnings.append(
                f"Layer '{layer.name}' baked: user position offset present."
            )
            return True
        rotation = self.gl_widget.layer_rotations.get(layer.layer_id, 0.0)
        if abs(rotation) > 1e-4:
            self._warnings.append(
                f"Layer '{layer.name}' baked: user rotation offset present."
            )
            return True
        scale = self.gl_widget.layer_scale_offsets.get(layer.layer_id, (1.0, 1.0))
        if abs(scale[0] - 1.0) > 1e-4 or abs(scale[1] - 1.0) > 1e-4:
            self._warnings.append(
                f"Layer '{layer.name}' baked: user scale offset present."
            )
            return True
        return False

    def _propagate_baked_transforms(self, modes: Dict[int, str], layers: List[LayerData]) -> None:
        """Ensure children of baked layers also bake transforms."""
        layer_lookup = {layer.layer_id: layer for layer in layers}
        changed = True
        while changed:
            changed = False
            for layer in layers:
                parent = layer_lookup.get(layer.parent_id)
                if not parent:
                    continue
                parent_mode = modes.get(parent.layer_id, "rig")
                if parent_mode in ("baked_transform", "baked_image") and modes.get(layer.layer_id) in ("rig", "null"):
                    modes[layer.layer_id] = "baked_transform"
                    self._warnings.append(
                        f"Layer '{layer.name}' baked: parent '{parent.name}' baked."
                    )
                    changed = True

    def _resolve_blend_mode(self, layer: LayerData, preset) -> int:
        blend_override = None
        if preset and getattr(preset, "blend_mode", None):
            name = str(preset.blend_mode).upper()
            mapping = {
                "STANDARD": BlendMode.STANDARD,
                "PREMULT_ALPHA": BlendMode.PREMULT_ALPHA,
                "PREMULT_ALPHA_ALT": BlendMode.PREMULT_ALPHA_ALT,
                "PREMULT_ALPHA_ALT2": BlendMode.PREMULT_ALPHA_ALT2,
                "ADDITIVE": BlendMode.ADDITIVE,
                "MULTIPLY": BlendMode.MULTIPLY,
                "SCREEN": BlendMode.SCREEN,
                "INHERIT": BlendMode.INHERIT,
            }
            blend_override = mapping.get(name)
        return blend_override if blend_override is not None else layer.blend_mode

    def _resolve_static_color(
        self,
        layer: LayerData,
        preset
    ) -> Tuple[Tuple[float, float, float], float]:
        if layer.render_tags and "neutral_color" in layer.render_tags:
            rgb = (1.0, 1.0, 1.0)
            alpha = 1.0
        else:
            rgb_values = None
            for key in layer.keyframes:
                if getattr(key, "immediate_rgb", -1) != -1:
                    rgb_values = (key.r, key.g, key.b, key.a)
                    break
            if rgb_values is None:
                rgb_values = (255, 255, 255, 255)
            rgb = (
                rgb_values[0] / 255.0,
                rgb_values[1] / 255.0,
                rgb_values[2] / 255.0,
            )
            alpha = rgb_values[3] / 255.0

        tint = layer.color_tint or (1.0, 1.0, 1.0, 1.0)
        rgb = (
            rgb[0] * float(tint[0]),
            rgb[1] * float(tint[1]),
            rgb[2] * float(tint[2]),
        )
        alpha *= float(tint[3])

        if preset:
            rgb = (
                rgb[0] * preset.color_scale[0],
                rgb[1] * preset.color_scale[1],
                rgb[2] * preset.color_scale[2],
            )
            alpha *= float(preset.alpha_scale)

        rgb = tuple(max(0.0, min(1.0, value)) for value in rgb)
        alpha = max(0.0, min(1.0, alpha))
        return rgb, alpha

    def _build_layer_precomp(
        self,
        layer: LayerData,
        sprite_names: Set[str],
        first_times: Dict[str, float],
        atlas_chain: List[TextureAtlas],
        rgb_factor: Tuple[float, float, float],
        mask_only: bool,
        sprites_dir: str,
        layer_map: Dict[int, LayerData],
        pivot_context: Dict[int, bool],
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, str]]:
        if not sprite_names:
            return None, {}

        anchor_x, anchor_y = self._get_layer_anchor(layer)

        min_x = math.inf
        min_y = math.inf
        max_x = -math.inf
        max_y = -math.inf

        sprite_entries: List[Dict[str, Any]] = []
        sprite_asset_map: Dict[str, str] = {}
        sprite_payloads: List[Dict[str, Any]] = []

        for sprite_name in sorted(sprite_names):
            sprite, atlas, resolved = self._resolve_sprite(sprite_name, atlas_chain)
            if not sprite or not atlas:
                continue
            anim_time = first_times.get(resolved, 0.0)
            anchor_offset = self._get_sprite_anchor_offset(
                layer,
                resolved,
                anim_time,
                layer_map,
                atlas_chain,
                pivot_context,
            )
            local_vertices = self.renderer.compute_local_vertices(sprite, atlas)
            if anchor_offset:
                anchor_dx = anchor_offset[0] * self.renderer.base_world_scale * self.renderer.position_scale
                anchor_dy = anchor_offset[1] * self.renderer.base_world_scale * self.renderer.position_scale
                local_vertices = [(x + anchor_dx, y + anchor_dy) for x, y in local_vertices]
            if abs(self._ae_output_scale - 1.0) > 1e-6:
                local_vertices = [
                    (x * self._ae_output_scale, y * self._ae_output_scale)
                    for x, y in local_vertices
                ]

            if not local_vertices:
                continue

            sprite_min_x = min(x for x, _ in local_vertices)
            sprite_min_y = min(y for _, y in local_vertices)
            sprite_max_x = max(x for x, _ in local_vertices)
            sprite_max_y = max(y for _, y in local_vertices)

            min_x = min(min_x, sprite_min_x)
            min_y = min(min_y, sprite_min_y)
            max_x = max(max_x, sprite_max_x)
            max_y = max(max_y, sprite_max_y)

            asset_meta = self._export_sprite_asset(
                sprite,
                atlas,
                resolved,
                rgb_factor,
                mask_only,
                sprites_dir,
            )
            if not asset_meta:
                continue

            sprite_asset_map[resolved] = asset_meta["id"]
            sprite_payloads.append(
                {
                    "sprite": sprite,
                    "atlas": atlas,
                    "asset": asset_meta,
                    "min_x": sprite_min_x,
                    "min_y": sprite_min_y,
                    "local_vertices": local_vertices,
                }
            )

        if not sprite_payloads:
            return None, {}

        if not math.isfinite(min_x) or not math.isfinite(min_y):
            return None, {}

        width = max(1.0, max_x - min_x)
        height = max(1.0, max_y - min_y)

        for payload in sprite_payloads:
            entry = {
                "sprite_id": payload["asset"]["id"],
                "offset": [payload["min_x"] - min_x, payload["min_y"] - min_y],
            }
            if self._ae_mesh_mode and payload["sprite"].has_polygon_mesh:
                mesh_data = self._build_mesh_payload(
                    payload["sprite"],
                    payload["atlas"],
                    payload["local_vertices"],
                    min_x,
                    min_y,
                    payload["asset"],
                )
                if mesh_data:
                    entry["mesh"] = mesh_data
            sprite_entries.append(entry)

        precomp = {
            "width": width,
            "height": height,
            "anchor": [anchor_x - min_x, anchor_y - min_y],
            "origin": [min_x, min_y],
            "sprites": sprite_entries,
        }
        return precomp, sprite_asset_map

    def _build_rig_transform_keys(
        self,
        layer: LayerData,
        alpha_factor: float,
        playback_speed: float,
    ) -> Dict[str, List[Dict[str, Any]]]:
        inv_speed = 1.0 / max(1e-6, playback_speed)
        position_keys = self._build_property_keys(
            layer,
            "immediate_pos",
            default_value=(0.0, 0.0),
            time_scale=inv_speed,
            value_fn=lambda kf: (
                kf.pos_x * self.renderer.local_position_multiplier * self.renderer.base_world_scale * self.renderer.position_scale * self._ae_output_scale,
                kf.pos_y * self.renderer.local_position_multiplier * self.renderer.base_world_scale * self.renderer.position_scale * self._ae_output_scale,
            ),
        )
        scale_keys = self._build_property_keys(
            layer,
            "immediate_scale",
            default_value=(
                100.0 * self.renderer.scale_bias_x,
                100.0 * self.renderer.scale_bias_y,
            ),
            time_scale=inv_speed,
            value_fn=lambda kf: (
                kf.scale_x * self.renderer.scale_bias_x,
                kf.scale_y * self.renderer.scale_bias_y,
            ),
        )
        rotation_keys = self._build_property_keys(
            layer,
            "immediate_rotation",
            default_value=self.renderer.rotation_bias,
            time_scale=inv_speed,
            value_fn=lambda kf: kf.rotation + self.renderer.rotation_bias,
        )
        opacity_keys = self._build_property_keys(
            layer,
            "immediate_opacity",
            default_value=100.0 * alpha_factor,
            time_scale=inv_speed,
            value_fn=lambda kf: kf.opacity * alpha_factor,
        )

        return {
            "position": position_keys,
            "scale": scale_keys,
            "rotation": rotation_keys,
            "opacity": opacity_keys,
        }

    def _build_property_keys(
        self,
        layer: LayerData,
        immediate_attr: str,
        default_value: Any,
        time_scale: float,
        value_fn,
    ) -> List[Dict[str, Any]]:
        keys: List[Dict[str, Any]] = []
        for keyframe in layer.keyframes:
            immediate = getattr(keyframe, immediate_attr, -1)
            if immediate == -1:
                continue
            keys.append(
                {
                    "time": keyframe.time * time_scale,
                    "value": value_fn(keyframe),
                    "hold": immediate == 1,
                }
            )
        if not keys:
            return [{"time": 0.0, "value": default_value, "hold": True}]
        if keys[0]["time"] > 0.0:
            keys.insert(0, {"time": 0.0, "value": keys[0]["value"], "hold": True})
        return keys

    def _build_baked_transform_keys(
        self,
        layer: LayerData,
        fps: int,
        frame_count: int,
        alpha_scale: float,
        world_offset: Tuple[float, float],
    ) -> Dict[str, List[Dict[str, Any]]]:
        samples: List[Dict[str, Any]] = []
        tolerance = 1e-4
        last: Optional[Tuple[float, float, float, float, float, float]] = None

        for frame_idx in range(frame_count):
            anim_time = self.main_window._get_export_frame_time(frame_idx, fps)
            world_states = self.gl_widget._build_layer_world_states(
                anim_time=anim_time, apply_constraints=False
            )
            state = world_states.get(layer.layer_id)
            if not state:
                continue
            offset = self.gl_widget.layer_offsets.get(layer.layer_id, (0.0, 0.0))
            anchor_world_x = (
                state.get("anchor_world_x", state["tx"]) + offset[0] - world_offset[0]
            ) * self._ae_output_scale
            anchor_world_y = (
                state.get("anchor_world_y", state["ty"]) + offset[1] - world_offset[1]
            ) * self._ae_output_scale

            m00 = state["m00"]
            m01 = state["m01"]
            m10 = state["m10"]
            m11 = state["m11"]
            scale_x = math.hypot(m00, m10)
            det = m00 * m11 - m01 * m10
            if abs(scale_x) < 1e-8:
                scale_x = 0.0
                scale_y = 0.0
                rotation = 0.0
            else:
                scale_y = det / scale_x
                rotation = math.degrees(math.atan2(m10, m00))

            opacity = max(0.0, min(1.0, state.get("world_opacity", 1.0) * alpha_scale))

            current = (
                anchor_world_x,
                anchor_world_y,
                scale_x * 100.0,
                scale_y * 100.0,
                rotation,
                opacity * 100.0,
            )
            if last is None or not self._tuple_close(last, current, tolerance):
                samples.append(
                    {
                        "time": frame_idx / float(fps),
                        "position": [current[0], current[1]],
                        "scale": [current[2], current[3]],
                        "rotation": current[4],
                        "opacity": current[5],
                    }
                )
                last = current

        if not samples:
            samples.append(
                {
                    "time": 0.0,
                    "position": [0.0, 0.0],
                    "scale": [100.0, 100.0],
                    "rotation": 0.0,
                    "opacity": 100.0 * alpha_scale,
                }
            )

        return {"samples": samples}

    def _render_baked_sequences(
        self,
        baked_layers: List[Dict[str, Any]],
        baked_root: str,
        fps: int,
        frame_count: int,
    ) -> None:
        animation = self.player.animation
        if not animation:
            return

        self.gl_widget.attachment_instances = []
        self.gl_widget.tile_batches = []
        self.renderer.world_offset_x = 0.0
        self.renderer.world_offset_y = 0.0

        try:
            output_width, output_height = self._baked_render_size or self._scaled_viewport_size()
            render_scale = self._ae_output_scale * self._baked_render_scale
            for entry in baked_layers:
                layer = entry["layer"]
                layer_dir = entry["folder"]
                mask_source_id = entry.get("mask_source_id")
                os.makedirs(layer_dir, exist_ok=True)

                for anim_layer in animation.layers:
                    anim_layer.visible = False
                if self._original_visibility.get(layer.layer_id, True):
                    layer.visible = True
                if mask_source_id is not None:
                    mask_layer = next(
                        (candidate for candidate in animation.layers if candidate.layer_id == mask_source_id),
                        None,
                    )
                    if mask_layer and self._original_visibility.get(mask_layer.layer_id, True):
                        mask_layer.visible = True

                original_role = layer.mask_role
                if original_role == "mask_source":
                    layer.mask_role = None

                for frame_idx in range(frame_count):
                    anim_time = self.main_window._get_export_frame_time(frame_idx, fps)
                    self.player.current_time = anim_time
                    image = self.main_window.render_frame_to_image(
                        output_width,
                        output_height,
                        camera_override=(0.0, 0.0),
                        render_scale_override=render_scale,
                        apply_centering=False,
                        background_color=None,
                    )
                    if image is None:
                        continue
                    filename = os.path.join(layer_dir, f"frame_{frame_idx + 1:05d}.png")
                    image.save(filename, "PNG", **self._png_save_kwargs())

                layer.mask_role = original_role

        finally:
            for anim_layer in animation.layers:
                anim_layer.visible = self._original_visibility.get(anim_layer.layer_id, anim_layer.visible)
            self.renderer.world_offset_x = self._original_world_offset[0]
            self.renderer.world_offset_y = self._original_world_offset[1]
            self.gl_widget.tile_batches = self._original_tile_batches
            self.gl_widget.attachment_instances = self._original_attachments

    def _render_composite_sequence(
        self,
        composite_dir: str,
        fps: int,
        frame_count: int,
    ) -> None:
        """Bake the full sprite+attachment composite into a single sequence."""
        animation = self.player.animation
        if not animation:
            return

        original_tile_batches = list(self.gl_widget.tile_batches)
        original_attachments = list(self.gl_widget.attachment_instances)
        original_world_offset = (self.renderer.world_offset_x, self.renderer.world_offset_y)

        os.makedirs(composite_dir, exist_ok=True)

        self.gl_widget.tile_batches = []
        self.renderer.world_offset_x = 0.0
        self.renderer.world_offset_y = 0.0

        try:
            self._prepare_baked_render_scale()
            output_width, output_height = self._baked_render_size or self._scaled_viewport_size()
            render_scale = self._ae_output_scale * self._baked_render_scale
            for frame_idx in range(frame_count):
                anim_time = self.main_window._get_export_frame_time(frame_idx, fps)
                self.player.current_time = anim_time
                image = self.main_window.render_frame_to_image(
                    output_width,
                    output_height,
                    camera_override=(0.0, 0.0),
                    render_scale_override=render_scale,
                    apply_centering=False,
                    background_color=None,
                )
                if image is None:
                    continue
                filename = os.path.join(composite_dir, f"frame_{frame_idx + 1:05d}.png")
                image.save(filename, "PNG", **self._png_save_kwargs())
        finally:
            self.gl_widget.tile_batches = original_tile_batches
            self.gl_widget.attachment_instances = original_attachments
            self.renderer.world_offset_x = original_world_offset[0]
            self.renderer.world_offset_y = original_world_offset[1]

    def _export_tile_background(
        self,
        export_root: str,
        baked_dir: str,
    ) -> Optional[Dict[str, Any]]:
        if not self.gl_widget.tile_batches:
            return None

        animation = self.player.animation
        if not animation:
            return None

        self.gl_widget.attachment_instances = []
        self.renderer.world_offset_x = 0.0
        self.renderer.world_offset_y = 0.0

        try:
            for layer in animation.layers:
                layer.visible = False
            self._prepare_baked_render_scale()
            output_width, output_height = self._baked_render_size or self._scaled_viewport_size()
            render_scale = self._ae_output_scale * self._baked_render_scale
            image = self.main_window.render_frame_to_image(
                output_width,
                output_height,
                camera_override=(0.0, 0.0),
                render_scale_override=render_scale,
                apply_centering=False,
                background_color=None,
            )
            if image is None:
                return None
            out_path = os.path.join(baked_dir, "tiles.png")
            image.save(out_path, "PNG", **self._png_save_kwargs())
            return {"path": os.path.relpath(out_path, export_root).replace("\\", "/")}
        finally:
            for layer in animation.layers:
                layer.visible = self._original_visibility.get(layer.layer_id, layer.visible)
            self.renderer.world_offset_x = self._original_world_offset[0]
            self.renderer.world_offset_y = self._original_world_offset[1]
            self.gl_widget.attachment_instances = self._original_attachments

    def _export_attachments(
        self,
        export_root: str,
        sprites_dir: str,
        baked_dir: str,
        fps: int,
        frame_count: int,
    ) -> List[Dict[str, Any]]:
        attachments: List[Dict[str, Any]] = []
        instances = list(self.gl_widget.attachment_instances or [])
        if not instances:
            return attachments

        layer_index = {layer.layer_id: idx for idx, layer in enumerate(self.player.animation.layers or [])}
        for instance in instances:
            animation = instance.player.animation
            if not animation:
                continue
            chain = list(instance.atlases or [])
            if getattr(instance, "allow_base_fallback", False):
                chain.extend(self.gl_widget.texture_atlases or [])

            layer_map = {layer.layer_id: layer for layer in animation.layers}
            mask_consumer_map = self._build_mask_consumer_map(animation.layers)
            sprite_timelines: Dict[int, List[Dict[str, Any]]] = {}
            sprite_sets: Dict[int, Set[str]] = {}
            sprite_first_times: Dict[int, Dict[str, float]] = {}

            for layer in animation.layers:
                keys, used, first_times = self._sample_attachment_sprite_timeline(
                    instance, layer, chain, fps, frame_count
                )
                sprite_timelines[layer.layer_id] = keys
                sprite_sets[layer.layer_id] = used
                sprite_first_times[layer.layer_id] = first_times

            attachment_layers: List[Dict[str, Any]] = []
            bounds_min_x = math.inf
            bounds_min_y = math.inf
            bounds_max_x = -math.inf
            bounds_max_y = -math.inf

            for idx, layer in enumerate(animation.layers):
                used_sprites = sprite_sets.get(layer.layer_id, set())
                shader_preset = self._get_shader_preset(layer.shader_name)
                shader_behavior = self._get_shader_behavior(layer.shader_name)
                if shader_behavior or layer.color_animator or layer.color_gradient or self._layer_has_dynamic_rgb(layer):
                    self._warnings.append(
                        f"Attachment layer '{layer.name}' uses dynamic shading; export may differ."
                    )
                blend_mode = self._resolve_blend_mode(layer, shader_preset)
                rgb_factor, _ = self._resolve_static_color(layer, shader_preset)
                preset_alpha = float(shader_preset.alpha_scale) if shader_preset else 1.0
                mask_only = layer.mask_role == "mask_source"

                precomp, sprite_asset_map = self._build_layer_precomp(
                    layer,
                    used_sprites,
                    sprite_first_times.get(layer.layer_id, {}),
                    chain,
                    rgb_factor,
                    mask_only,
                    sprites_dir,
                    layer_map,
                    {},
                )
                sprite_keys = self._map_sprite_keys(
                    sprite_timelines.get(layer.layer_id, []),
                    sprite_asset_map,
                )

                transform_samples = self._build_attachment_transform_samples(
                    instance,
                    layer,
                    chain,
                    fps,
                    frame_count,
                    preset_alpha,
                    layer_map,
                )

                if precomp and transform_samples:
                    bounds_min_x, bounds_min_y, bounds_max_x, bounds_max_y = self._update_bounds_from_samples(
                        bounds_min_x,
                        bounds_min_y,
                        bounds_max_x,
                        bounds_max_y,
                        transform_samples.get("samples", []),
                        precomp,
                    )

                attachment_layers.append(
                    {
                        "id": layer.layer_id,
                        "name": layer.name,
                        "parent_id": -1,
                        "stack_index": idx,
                        "blend_mode": blend_mode,
                        "visible": bool(layer.visible),
                        "export_mode": "baked_transform",
                        "mask_role": layer.mask_role,
                        "mask_key": layer.mask_key,
                        "mask_source_id": mask_consumer_map.get(layer.layer_id),
                        "mask_only": mask_only,
                        "layer_anchor": list(self._compute_anchor_from_raw(layer.anchor_x, layer.anchor_y)),
                        "precomp": precomp,
                        "sprite_keys": sprite_keys if precomp else [],
                        "transform": transform_samples,
                        "baked_sequence": None,
                    }
                )

            if not math.isfinite(bounds_min_x):
                bounds_min_x = 0.0
                bounds_min_y = 0.0
                fallback_width, fallback_height = self._scaled_viewport_size()
                bounds_max_x = float(fallback_width)
                bounds_max_y = float(fallback_height)

            width = max(1.0, bounds_max_x - bounds_min_x)
            height = max(1.0, bounds_max_y - bounds_min_y)
            offset = [-bounds_min_x, -bounds_min_y]

            attachments.append(
                {
                    "name": instance.name,
                    "target_layer_id": instance.target_layer_id,
                    "stack_index": layer_index.get(instance.target_layer_id, 0) + 0.5,
                    "width": width,
                    "height": height,
                    "offset": offset,
                    "layers": attachment_layers,
                }
            )

        return attachments

    def _sample_attachment_sprite_timeline(
        self,
        instance,
        layer: LayerData,
        atlas_chain: List[TextureAtlas],
        fps: int,
        frame_count: int,
    ) -> Tuple[List[Dict[str, Any]], Set[str], Dict[str, float]]:
        keys: List[Dict[str, Any]] = []
        used: Set[str] = set()
        first_times: Dict[str, float] = {}
        last_sprite: Optional[str] = None

        for frame_idx in range(frame_count):
            master_time = self.main_window._get_export_frame_time(frame_idx, fps)
            local_time = self._compute_attachment_time(instance, master_time)
            state = instance.player.get_layer_state(layer, local_time)
            sprite_name = state.get("sprite_name") or ""
            resolved_name: Optional[str] = None
            if sprite_name:
                sprite, _, resolved = self._resolve_sprite(sprite_name, atlas_chain)
                if sprite:
                    resolved_name = resolved
                    used.add(resolved_name)
                    if resolved_name not in first_times:
                        first_times[resolved_name] = local_time
            if resolved_name != last_sprite:
                keys.append({"time": frame_idx / float(fps), "sprite_id": resolved_name})
                last_sprite = resolved_name

        if not keys:
            keys.append({"time": 0.0, "sprite_id": None})
        elif keys[0]["time"] > 0.0:
            keys.insert(0, {"time": 0.0, "sprite_id": keys[0]["sprite_id"]})
        return keys, used, first_times

    def _build_attachment_transform_samples(
        self,
        instance,
        layer: LayerData,
        atlas_chain: List[TextureAtlas],
        fps: int,
        frame_count: int,
        alpha_scale: float,
        layer_map: Dict[int, LayerData],
    ) -> Dict[str, List[Dict[str, Any]]]:
        samples: List[Dict[str, Any]] = []
        tolerance = 1e-4
        last: Optional[Tuple[float, float, float, float, float, float]] = None

        for frame_idx in range(frame_count):
            master_time = self.main_window._get_export_frame_time(frame_idx, fps)
            local_time = self._compute_attachment_time(instance, master_time)
            world_states: Dict[int, Dict] = {}
            for anim_layer in instance.player.animation.layers:
                state = self.renderer.calculate_world_state(
                    anim_layer,
                    local_time,
                    instance.player,
                    layer_map,
                    world_states,
                    atlas_chain,
                )
                world_states[anim_layer.layer_id] = state

            root_anchor = self.gl_widget._get_attachment_root_anchor(
                instance,
                instance.player.animation,
                world_states,
            )
            state = world_states.get(layer.layer_id)
            if not state:
                continue

            anchor_world_x = (
                state.get("anchor_world_x", state["tx"]) - root_anchor[0]
            ) * self._ae_output_scale
            anchor_world_y = (
                state.get("anchor_world_y", state["ty"]) - root_anchor[1]
            ) * self._ae_output_scale

            m00 = state["m00"]
            m01 = state["m01"]
            m10 = state["m10"]
            m11 = state["m11"]
            scale_x = math.hypot(m00, m10)
            det = m00 * m11 - m01 * m10
            if abs(scale_x) < 1e-8:
                scale_x = 0.0
                scale_y = 0.0
                rotation = 0.0
            else:
                scale_y = det / scale_x
                rotation = math.degrees(math.atan2(m10, m00))

            opacity = max(0.0, min(1.0, state.get("world_opacity", 1.0) * alpha_scale))

            current = (
                anchor_world_x,
                anchor_world_y,
                scale_x * 100.0,
                scale_y * 100.0,
                rotation,
                opacity * 100.0,
            )
            if last is None or not self._tuple_close(last, current, tolerance):
                samples.append(
                    {
                        "time": frame_idx / float(fps),
                        "position": [current[0], current[1]],
                        "scale": [current[2], current[3]],
                        "rotation": current[4],
                        "opacity": current[5],
                    }
                )
                last = current

        if not samples:
            samples.append(
                {
                    "time": 0.0,
                    "position": [0.0, 0.0],
                    "scale": [100.0, 100.0],
                    "rotation": 0.0,
                    "opacity": 100.0 * alpha_scale,
                }
            )
        return {"samples": samples}

    def _update_bounds_from_samples(
        self,
        min_x: float,
        min_y: float,
        max_x: float,
        max_y: float,
        samples: List[Dict[str, Any]],
        precomp: Dict[str, Any],
    ) -> Tuple[float, float, float, float]:
        width = float(precomp.get("width", 1.0))
        height = float(precomp.get("height", 1.0))
        anchor = precomp.get("anchor", [0.0, 0.0])
        anchor_x = float(anchor[0])
        anchor_y = float(anchor[1])
        corners = [
            (-anchor_x, -anchor_y),
            (width - anchor_x, -anchor_y),
            (width - anchor_x, height - anchor_y),
            (-anchor_x, height - anchor_y),
        ]
        for sample in samples:
            pos = sample.get("position", [0.0, 0.0])
            scale = sample.get("scale", [100.0, 100.0])
            rotation = float(sample.get("rotation", 0.0))
            scale_x = float(scale[0]) / 100.0
            scale_y = float(scale[1]) / 100.0
            theta = math.radians(rotation)
            cos_r = math.cos(theta)
            sin_r = math.sin(theta)
            m00 = scale_x * cos_r
            m01 = -scale_y * sin_r
            m10 = scale_x * sin_r
            m11 = scale_y * cos_r
            for cx, cy in corners:
                wx = m00 * cx + m01 * cy + float(pos[0])
                wy = m10 * cx + m11 * cy + float(pos[1])
                min_x = min(min_x, wx)
                min_y = min(min_y, wy)
                max_x = max(max_x, wx)
                max_y = max(max_y, wy)
        return min_x, min_y, max_x, max_y

    def _compute_attachment_time(self, instance, master_time: float) -> float:
        animation = instance.player.animation
        if not animation:
            return 0.0
        speed = max(0.1, float(instance.tempo_multiplier or 1.0))
        local_time = master_time * speed + float(instance.time_offset or 0.0)
        duration = instance.player.duration or 0.0
        if duration > 0:
            if instance.loop:
                local_time = math.fmod(local_time, duration)
                if local_time < 0:
                    local_time += duration
            else:
                local_time = max(0.0, min(local_time, duration))
        return max(0.0, local_time)

    def _get_layer_anchor(self, layer: LayerData) -> Tuple[float, float]:
        override = self.gl_widget.layer_anchor_overrides.get(layer.layer_id)
        if override:
            anchor_local_x, anchor_local_y = override
        else:
            anchor_local_x = layer.anchor_x
            anchor_local_y = layer.anchor_y
        anchor_x = (anchor_local_x + self.renderer.anchor_bias_x) * self.renderer.base_world_scale * self.renderer.position_scale
        anchor_y = (anchor_local_y + self.renderer.anchor_bias_y) * self.renderer.base_world_scale * self.renderer.position_scale
        if abs(self._ae_output_scale - 1.0) > 1e-6:
            anchor_x *= self._ae_output_scale
            anchor_y *= self._ae_output_scale
        return anchor_x, anchor_y

    def _compute_anchor_from_raw(self, anchor_x: float, anchor_y: float) -> Tuple[float, float]:
        """Compute scaled anchor without applying per-layer overrides."""
        scaled_x = (anchor_x + self.renderer.anchor_bias_x) * self.renderer.base_world_scale * self.renderer.position_scale
        scaled_y = (anchor_y + self.renderer.anchor_bias_y) * self.renderer.base_world_scale * self.renderer.position_scale
        if abs(self._ae_output_scale - 1.0) > 1e-6:
            scaled_x *= self._ae_output_scale
            scaled_y *= self._ae_output_scale
        return scaled_x, scaled_y

    def _resolve_sprite(
        self,
        sprite_name: str,
        atlas_chain: List[TextureAtlas],
    ) -> Tuple[Optional[SpriteInfo], Optional[TextureAtlas], str]:
        key = (sprite_name or "", self._atlas_chain_key(atlas_chain))
        cached = self._sprite_lookup_cache.get(key)
        if cached is not None:
            return cached
        if not sprite_name:
            result = (None, None, sprite_name)
        else:
            sprite, atlas, resolved = self.renderer._find_sprite_in_atlases(sprite_name, atlas_chain, allow_alias=True)
            result = (sprite, atlas, resolved)
        self._sprite_lookup_cache[key] = result
        return result

    def _get_sprite_anchor_offset(
        self,
        layer: LayerData,
        sprite_name: str,
        anim_time: float,
        layer_map: Dict[int, LayerData],
        atlas_chain: List[TextureAtlas],
        pivot_context: Dict[int, bool],
    ) -> Optional[Tuple[float, float]]:
        cache_key = (layer.layer_id, sprite_name)
        if cache_key in self._sprite_anchor_cache:
            return self._sprite_anchor_cache[cache_key]
        world_states: Dict[int, Dict] = {}
        state = self.renderer.calculate_world_state(
            layer,
            anim_time,
            self.player,
            layer_map,
            world_states,
            atlas_chain,
            getattr(self.gl_widget, "layer_atlas_overrides", {}),
            pivot_context,
        )
        offset = state.get("sprite_anchor_offset")
        self._sprite_anchor_cache[cache_key] = offset
        return offset

    def _export_sprite_asset(
        self,
        sprite: SpriteInfo,
        atlas: TextureAtlas,
        resolved_name: str,
        rgb_factor: Tuple[float, float, float],
        mask_only: bool,
        sprites_dir: str,
    ) -> Optional[Dict[str, Any]]:
        atlas_key = self._atlas_key(atlas)
        color_key = self._color_key(rgb_factor)
        cache_key = (atlas_key, resolved_name, color_key, bool(mask_only))
        if cache_key in self._sprite_asset_cache:
            return self._sprite_asset_cache[cache_key]

        atlas_image = self.main_window._load_atlas_image(atlas)
        if atlas_image is None:
            self._warnings.append(f"Failed to load atlas image for sprite '{resolved_name}'.")
            return None

        crop_box = (sprite.x, sprite.y, sprite.x + sprite.w, sprite.y + sprite.h)
        try:
            sprite_image = atlas_image.crop(crop_box)
        except Exception:
            self._warnings.append(f"Failed to crop sprite '{resolved_name}'.")
            return None

        if mask_only:
            sprite_image = self._build_mask_sprite(sprite, sprite_image.size)
        else:
            if rgb_factor != (1.0, 1.0, 1.0):
                sprite_image = self._apply_color_multiplier(sprite_image, rgb_factor)

        if sprite.rotated:
            sprite_image = sprite_image.rotate(90, expand=True)

        pixel_scale = self._ae_output_scale
        if abs(pixel_scale - 1.0) > 1e-6:
            resample = self._resolve_ae_resample_filter()
            new_width = max(1, int(round(sprite_image.width * pixel_scale)))
            new_height = max(1, int(round(sprite_image.height * pixel_scale)))
            sprite_image = sprite_image.resize((new_width, new_height), resample=resample)

        safe_name = self._sanitize_label(resolved_name)
        filename = f"{safe_name}_{atlas_key}"
        if mask_only:
            filename += "_mask"
        if rgb_factor != (1.0, 1.0, 1.0):
            filename += "_c" + "".join(f"{v:04d}" for v in color_key)
        filename += ".png"
        path = os.path.join(sprites_dir, filename)
        sprite_image.save(path, "PNG", **self._png_save_kwargs())

        asset_id = f"sprite_{len(self._sprite_asset_cache) + 1:04d}"
        metadata = {
            "id": asset_id,
            "name": resolved_name,
            "path": os.path.relpath(path, os.path.dirname(sprites_dir)).replace("\\", "/"),
            "width": sprite_image.width,
            "height": sprite_image.height,
            "scale": self._sprite_scale(atlas),
            "pixel_scale": pixel_scale,
            "mask": bool(mask_only),
        }
        self._sprite_asset_cache[cache_key] = metadata
        return metadata

    def _build_mask_sprite(self, sprite: SpriteInfo, size: Tuple[int, int]) -> Image.Image:
        width, height = size
        if sprite.has_polygon_mesh and sprite.vertices and sprite.triangles:
            mask = Image.new("L", (width, height), 0)
            draw = ImageDraw.Draw(mask)
            offset_dx = sprite.offset_x * self.renderer.trim_shift_multiplier - sprite.offset_x
            offset_dy = sprite.offset_y * self.renderer.trim_shift_multiplier - sprite.offset_y
            vertices = [(vx + offset_dx, vy + offset_dy) for vx, vy in sprite.vertices]
            for idx in range(0, len(sprite.triangles), 3):
                idx0 = sprite.triangles[idx]
                idx1 = sprite.triangles[idx + 1] if idx + 1 < len(sprite.triangles) else None
                idx2 = sprite.triangles[idx + 2] if idx + 2 < len(sprite.triangles) else None
                if idx1 is None or idx2 is None:
                    break
                if idx0 >= len(vertices) or idx1 >= len(vertices) or idx2 >= len(vertices):
                    continue
                draw.polygon(
                    [vertices[idx0], vertices[idx1], vertices[idx2]],
                    fill=255,
                )
            white = Image.new("L", (width, height), 255)
            return Image.merge("RGBA", (white, white, white, mask))
        return Image.new("RGBA", (width, height), (255, 255, 255, 255))

    def _apply_color_multiplier(
        self,
        image: Image.Image,
        rgb_factor: Tuple[float, float, float],
    ) -> Image.Image:
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        arr = np.array(image, dtype=np.float32)
        arr[..., 0] *= rgb_factor[0]
        arr[..., 1] *= rgb_factor[1]
        arr[..., 2] *= rgb_factor[2]
        arr[..., 0:3] = np.clip(arr[..., 0:3], 0.0, 255.0)
        return Image.fromarray(arr.astype(np.uint8), "RGBA")

    def _resolve_ae_resample_filter(self) -> int:
        mode = str(self._ae_quality).lower()
        if mode == "fast":
            return Image.NEAREST
        if mode == "balanced":
            return Image.BILINEAR
        if mode == "high":
            return Image.BICUBIC
        return Image.LANCZOS

    def _png_save_kwargs(self) -> Dict[str, Any]:
        if self._ae_compression == "raw":
            return {"compress_level": 0}
        return {"compress_level": 6}

    def _build_mesh_payload(
        self,
        sprite: SpriteInfo,
        atlas: TextureAtlas,
        local_vertices: List[Tuple[float, float]],
        origin_x: float,
        origin_y: float,
        asset_meta: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not sprite.has_polygon_mesh or not sprite.vertices_uv or not sprite.triangles:
            return None
        if not local_vertices:
            return None
        width = float(asset_meta.get("width", 0.0) or 0.0)
        height = float(asset_meta.get("height", 0.0) or 0.0)
        if width <= 0.0 or height <= 0.0:
            return None
        image_scale = float(asset_meta.get("pixel_scale", 1.0) or 1.0)

        atlas_w = float(atlas.image_width or width)
        atlas_h = float(atlas.image_height or height)

        src_vertices: List[List[float]] = []
        for uv_x, uv_y in sprite.vertices_uv:
            src_x = uv_x * atlas_w - sprite.x
            src_y = uv_y * atlas_h - sprite.y
            if sprite.rotated:
                src_x, src_y = self._rotate_point_ccw(src_x, src_y, sprite.w)
            if abs(image_scale - 1.0) > 1e-6:
                src_x *= image_scale
                src_y *= image_scale
            src_vertices.append([float(src_x), float(src_y)])

        dst_vertices = [
            [float(vx - origin_x), float(vy - origin_y)]
            for vx, vy in local_vertices
        ]

        triangles: List[Dict[str, Any]] = []
        tri_indices = list(sprite.triangles)
        if len(tri_indices) % 3 != 0:
            tri_indices = tri_indices[: len(tri_indices) - (len(tri_indices) % 3)]

        for idx in range(0, len(tri_indices), 3):
            i0 = tri_indices[idx]
            i1 = tri_indices[idx + 1]
            i2 = tri_indices[idx + 2]
            if (
                i0 >= len(src_vertices)
                or i1 >= len(src_vertices)
                or i2 >= len(src_vertices)
                or i0 >= len(dst_vertices)
                or i1 >= len(dst_vertices)
                or i2 >= len(dst_vertices)
            ):
                continue
            s0 = src_vertices[i0]
            s1 = src_vertices[i1]
            s2 = src_vertices[i2]
            d0 = dst_vertices[i0]
            d1 = dst_vertices[i1]
            d2 = dst_vertices[i2]
            corners = self._compute_affine_corners(s0, s1, s2, d0, d1, d2, width, height)
            if not corners:
                continue
            triangles.append(
                {
                    "src": [s0, s1, s2],
                    "corners": corners,
                }
            )

        if not triangles:
            return None
        return {
            "src_size": [width, height],
            "triangles": triangles,
        }

    @staticmethod
    def _compute_affine_corners(
        s0: List[float],
        s1: List[float],
        s2: List[float],
        d0: List[float],
        d1: List[float],
        d2: List[float],
        width: float,
        height: float,
    ) -> Optional[List[List[float]]]:
        mat = np.array(
            [
                [s0[0], s0[1], 1.0],
                [s1[0], s1[1], 1.0],
                [s2[0], s2[1], 1.0],
            ],
            dtype=np.float64,
        )
        det = np.linalg.det(mat)
        if abs(det) < 1e-8:
            return None
        target = np.array(
            [
                [d0[0], d0[1]],
                [d1[0], d1[1]],
                [d2[0], d2[1]],
            ],
            dtype=np.float64,
        )
        try:
            transform = np.linalg.solve(mat, target)
        except Exception:
            return None
        corners_src = np.array(
            [
                [0.0, 0.0, 1.0],
                [width, 0.0, 1.0],
                [0.0, height, 1.0],
                [width, height, 1.0],
            ],
            dtype=np.float64,
        )
        corners_dst = corners_src.dot(transform)
        return corners_dst.tolist()

    @staticmethod
    def _rotate_point_ccw(x: float, y: float, width: float) -> Tuple[float, float]:
        return (y, width - x)

    def _sprite_scale(self, atlas: TextureAtlas) -> float:
        hires_scale = 0.5 if getattr(atlas, "is_hires", False) else 1.0
        return hires_scale * float(self.renderer.position_scale)

    def _scaled_viewport_size(self) -> Tuple[int, int]:
        scale = self._ae_output_scale if self._ae_output_scale > 0 else 1.0
        width = max(1, int(round(self.gl_widget.width() * scale)))
        height = max(1, int(round(self.gl_widget.height() * scale)))
        return width, height

    def _write_readme(self, export_root: str) -> None:
        path = os.path.join(export_root, "README_AE.txt")
        lines = [
            "AE Rig Export",
            "",
            "1) Open After Effects.",
            "2) Run the script: File > Scripts > Run Script File...",
            "   Select import_ae_rig.jsx from this export folder.",
            "",
            "Recommended AE settings for best match:",
            "- Project Settings: 32-bit linear workspace.",
            "- Interpret all sprite PNGs as Straight (Unmatted) alpha.",
            "- Use Draft/Bilinear sampling if you want closest GL_LINEAR filtering.",
            "- Resolution Scale and Full Resolution Output are baked into comp sizes and PNGs.",
            "",
            "This export contains:",
            "- Manifest: ae_manifest.json",
            "- Sprite assets: sprites/",
            "- Baked sequences: baked/",
            "- Audio track (when available): audio/",
            "",
            "Note: Some exports may fall back to a single baked composite sequence",
            "when polygon mesh layers dominate. See ae_export_report.txt for details.",
            "",
            "Layers marked as baked_image are fully rasterized per-frame.",
            "Mask sources are exported as matte-only geometry.",
        ]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

    def _write_report(self, export_root: str) -> None:
        path = os.path.join(export_root, "ae_export_report.txt")
        if not self._warnings:
            content = "No warnings.\n"
        else:
            content = "\n".join(self._warnings) + "\n"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def _copy_import_script(self, export_root: str) -> None:
        project_root = Path(getattr(self.main_window, "project_root", Path.cwd()))
        script_path = project_root / "Resources" / "ae" / "import_ae_rig.jsx"
        if not script_path.exists():
            self._warnings.append("AE import script not found at Resources/ae/import_ae_rig.jsx.")
            return
        try:
            shutil.copy(str(script_path), os.path.join(export_root, "import_ae_rig.jsx"))
        except Exception as exc:
            self._warnings.append(f"Failed to copy AE script: {exc}")

    def _get_shader_preset(self, shader_name: Optional[str]):
        if not shader_name or not self.shader_registry:
            return None
        return self.shader_registry.get_preset(shader_name)

    def _get_shader_behavior(self, shader_name: Optional[str]):
        if not shader_name or not self.shader_registry:
            return None
        return self.shader_registry.get_behavior(shader_name)

    def _atlas_key(self, atlas: TextureAtlas) -> str:
        source = getattr(atlas, "source_name", None)
        if source:
            return self._sanitize_label(source)
        image_path = getattr(atlas, "image_path", None)
        if image_path:
            return self._sanitize_label(os.path.basename(image_path))
        return f"atlas_{id(atlas)}"

    def _atlas_chain_key(self, atlases: List[TextureAtlas]) -> Tuple[str, ...]:
        return tuple(self._atlas_key(atlas) for atlas in atlases if atlas)

    def _color_key(self, rgb_factor: Tuple[float, float, float]) -> Tuple[int, int, int]:
        return tuple(int(round(value * 10000)) for value in rgb_factor)

    @staticmethod
    def _tuple_close(a: Tuple[float, ...], b: Tuple[float, ...], tolerance: float) -> bool:
        return all(abs(x - y) <= tolerance for x, y in zip(a, b))

    @staticmethod
    def _sanitize_label(value: str) -> str:
        cleaned = re.sub(r"[^0-9a-zA-Z_-]+", "_", value or "").strip("_")
        return cleaned or "asset"
