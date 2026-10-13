"""Run the desktop costume policy without constructing a Qt window or loading textures."""
import ast
import copy
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Tuple

root = Path(__file__).resolve().parents[3]
exec(compile((root / 'core/data_structures.py').read_text(), 'desktop-data-structures', 'exec'))

TextureAtlas = Any

class BlendMode:
    STANDARD = 0

names = {
    '_apply_clone_layers', '_duplicate_layer', '_reverse_replacement_clone_visibility',
    '_update_canonical_clone_visibility', '_resolve_overlay_reference', '_layer_name_variants',
    '_normalize_layer_label', '_find_layer_in_lookup', '_alias_remap_entry',
    '_layer_default_opacity', '_build_remap_map', '_apply_remaps_to_layers', '_remap_sprite',
    '_layer_has_costume_color', '_color_tuple_is_identity', '_neutralize_keyframe_color',
    '_force_keyframe_opacity', '_enforce_costume_overlay_order', '_overlay_anchor_name',
    '_overlay_reference_name', '_normalize_costume_layer_blends', '_remap_targets_costume_sheet',
    '_tag_mixed_pivot_costume_frame_sequences', '_canonical_sheet_keys', '_sheet_base_name',
}
tree = ast.parse((root / 'ui/main_window.py').read_text())
methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
assert len(methods) == len(names)
cls = ast.ClassDef(name='Desktop', bases=[], keywords=[], body=methods, decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), 'desktop-costumes', 'exec'))
desktop = Desktop()
desktop.diagnostics = SimpleNamespace(log_clone=lambda *args, **kwargs: None)
desktop.log_widget = SimpleNamespace(log=lambda *args: None)
request = json.load(sys.stdin)
outputs = []
for case in request:
    costume = copy.deepcopy(case['costume'])
    atlases = []
    for atlas in case['atlases']:
        sprites = {s['name']: SpriteInfo(s['name'], s['x'], s['y'], s['width'], s['height'],
            s['pivotX'], s['pivotY'], s['offsetX'], s['offsetY'], s['originalWidth'], s['originalHeight']) for s in atlas['sprites']}
        atlases.append(SimpleNamespace(source_name=atlas['sourceName'], image_path=atlas['sourceName'],
            is_hires=atlas['hires'], get_sprite=lambda name, sprites=sprites: sprites.get(name)))
    tracks = []
    for track in case['document']['anims']:
        layers = [LayerData(l['name'], l['id'], l['parent'], l['anchor_x'], l['anchor_y'], l.get('blend', 0),
            [KeyframeData(f.get('time', 0), sprite_name=f.get('sprite', {}).get('string', ''),
                opacity=f.get('opacity', {}).get('value', 100), immediate_opacity=f.get('opacity', {}).get('immediate', -1)) for f in l['frames']]) for l in track['layers']]
        remaps, sheets = desktop._build_remap_map(costume['remaps'])
        overrides = {}
        desktop.canonical_layer_names = set()
        if track.get('clone_layers'):
            desktop._apply_clone_layers(layers, copy.deepcopy(track['clone_layers']), {}, set(), {}, label='canonical')
            desktop.canonical_layer_names = {layer.name.lower() for layer in layers if layer.layer_id not in {l['id'] for l in track['layers']}}
        desktop._apply_clone_layers(layers, costume['clone_layers'], remaps, sheets, overrides)
        desktop._reverse_replacement_clone_visibility(layers, costume['clone_layers'], remaps, overrides)
        desktop._apply_remaps_to_layers(layers, remaps, overrides)
        desktop._update_canonical_clone_visibility(layers, remaps, overrides)
        desktop._enforce_costume_overlay_order(layers)
        desktop._normalize_costume_layer_blends(layers, remaps, overrides)
        desktop._tag_mixed_pivot_costume_frame_sequences(layers, remaps, overrides, atlases)
        tracks.append([dict(name=l.name, parent=l.parent_id, anchor=[l.anchor_x, l.anchor_y], visible=l.visible,
            sprites=[f.sprite_name for f in l.keyframes], opacity=[f.opacity for f in l.keyframes],
            anchors={k.lower(): v for k, v in (l.sprite_anchor_map or {}).items()}) for l in layers])
    outputs.append(tracks)
json.dump(outputs, sys.stdout)
