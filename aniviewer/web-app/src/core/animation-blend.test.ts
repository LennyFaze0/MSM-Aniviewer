import { describe, expect, test } from 'vitest';
import { animationBlendVersion, layerAnimationBlend, normalizeAnimationBlend } from './animation-blend';
import type { AnimationDocument, AnimationLayer } from './types';

function document(blends: number[], version?: number): AnimationDocument {
  return {
    rev: 6, blend_version: version, sources: [],
    anims: [{ name: 'test', width: 1, height: 1, layers: blends.map((blend, id) => ({
      name: `layer ${id}`, id, blend, parent: -1, src: 0, anchor_x: 0, anchor_y: 0, frames: [],
    })) }],
  };
}

describe('desktop animation blend compatibility', () => {
  test.each([undefined, 1, 2, 3])('treats 0/1-only exports as legacy even with metadata %s', (version) => {
    const doc = document([0, 1], version);
    expect(animationBlendVersion(doc)).toBe(1);
    expect(layerAnimationBlend(doc.anims[0]!.layers[1]!, animationBlendVersion(doc))).toBe(2);
    expect(doc.anims[0]!.layers[1]!.blend).toBe(1);
  });

  test('uses the whole file, not just the selected track, to detect modern modes', () => {
    const doc = document([0, 1], 2);
    doc.anims.push(document([2]).anims[0]!);
    expect(animationBlendVersion(doc)).toBe(2);
    expect(normalizeAnimationBlend(1, animationBlendVersion(doc))).toBe(1);
  });

  test.each([undefined, 1])('preserves hybrid modes with metadata %s', (version) => {
    const doc = document([1, 2, 6, 7], version);
    const resolved = animationBlendVersion(doc);
    expect(resolved).toBe(1);
    expect([1, 2, 6, 7].map((id) => normalizeAnimationBlend(id, resolved))).toEqual([2, 2, 6, 7]);
  });

  test('keeps modern alpha and other canonical modes intact', () => {
    expect(Array.from({ length: 8 }, (_, id) => normalizeAnimationBlend(id, 2)))
      .toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect([-1, 8, undefined, NaN, 'bad'].map((id) => normalizeAnimationBlend(id, 1)))
      .toEqual([0, 0, 0, 0, 0]);
  });

  test('ports the DOF light-layer compatibility rule without changing other shaders', () => {
    const layer: AnimationLayer = { ...document([0]).anims[0]!.layers[0]!, name: 'Sprite_light_01' };
    expect(layerAnimationBlend(layer, 2)).toBe(2);
    expect(layerAnimationBlend({ ...layer, shader_name: 'Anim2D/Normal+Alpha' }, 2)).toBe(2);
    expect(layerAnimationBlend({ ...layer, shader_name: 'custom/shader' }, 2)).toBe(0);
    expect(layerAnimationBlend({ ...layer, name: 'body' }, 2)).toBe(0);
    expect(layerAnimationBlend({ ...layer, blend: 1 }, 2)).toBe(1);
  });
});
