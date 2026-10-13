import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, test } from 'vitest';
import { animationBlendVersion, layerAnimationBlend } from './animation-blend';
import type { AnimationDocument } from './types';

// Opt-in because ordinary web development requires Node, not Python or Qt.
test.skipIf(!process.env.ANIVIEWER_DESKTOP_BLEND_PARITY)('matches the desktop blend policy directly', () => {
  const documents: AnimationDocument[] = [undefined, 1, 2, 3].flatMap((blend_version) => (
    [[0, 1], [0, 1, 2], [1, 6], [1, 7], [0, 3], []].map((blends) => ({
      rev: 6, blend_version, sources: [],
      anims: [{ name: 'test', width: 1, height: 1, layers: blends.map((blend, id) => ({
        name: `layer ${id}`, id, blend, parent: -1, src: 0, anchor_x: 0, anchor_y: 0, frames: [],
      })) }],
    }))
  ));
  documents.push({ rev: 6, sources: [], anims: [{
    name: 'DOF', width: 1, height: 1,
    layers: ['', 'Anim2D/Normal+Alpha', 'custom/shader'].map((shader_name, id) => ({
      name: 'Sprite_light_01', id, blend: 0, shader_name, parent: -1, src: 0,
      anchor_x: 0, anchor_y: 0, frames: [],
    })),
  }] });
  const reference = execFileSync('python3', ['-c', `
import ast, json, sys
from typing import Dict, Optional
class BlendMode:
    STANDARD, PREMULT_ALPHA, ADDITIVE, PREMULT_ALPHA_ALT, PREMULT_ALPHA_ALT2, INHERIT, MULTIPLY, SCREEN = range(8)
with open(sys.argv[1], encoding='utf-8') as source:
    tree = ast.parse(source.read())
names = {'_determine_blend_version', '_normalize_blend_value', '_should_promote_light_layer_blend'}
methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
assert len(methods) == len(names)
cls = ast.ClassDef(name='Desktop', bases=[], keywords=[], body=methods, decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), 'desktop-policy', 'exec'))
desktop = Desktop()
result = []
for document in json.load(sys.stdin):
    version = desktop._determine_blend_version(document)
    modes = []
    for track in document['anims']:
        for layer in track['layers']:
            blend = desktop._normalize_blend_value(layer.get('blend', 0), version)
            light = desktop._should_promote_light_layer_blend(layer['name'], layer.get('shader_name'), blend)
            modes.append(2 if light else blend)
    result.append([version, modes])
print(json.dumps(result))
`, fileURLToPath(new URL('../../../ui/main_window.py', import.meta.url))], {
    input: JSON.stringify(documents), encoding: 'utf8',
  });
  expect(documents.map((document) => {
    const version = animationBlendVersion(document);
    return [version, document.anims.flatMap((track) => track.layers.map((layer) => layerAnimationBlend(layer, version)))];
  })).toEqual(JSON.parse(reference));
});
