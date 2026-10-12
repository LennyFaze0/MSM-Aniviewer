import { describe, expect, it } from 'vitest';
import { calculateWorldStates } from './animation';
import { applyWorldLayerEdits, DEFAULT_LAYER_EDIT, layerAnchor, normalizeLayerEdit, pointHitsQuad, signedAngleDelta } from './layer-transform';
import type { AnimationTrack } from './types';

const track: AnimationTrack = { name: 'Three generations', width: 200, height: 200, layers: [0, 1, 2].map((id) => ({
  id, name: `layer ${id}`, parent: id - 1, src: 0, anchor_x: 8, anchor_y: 5,
  sprite_anchor_map: id === 0 ? { frame2: [4, 9] as [number, number] } : undefined,
  frames: [
    { time: 0, pos: { immediate: 0, x: 20, y: 30 }, rotation: { immediate: 0, value: id * 21 }, scale: { immediate: 0, x: 175, y: 55 }, sprite: { immediate: 1, string: 'frame1' } },
    { time: 1, pos: { immediate: 0, x: -6, y: 55 }, rotation: { immediate: 0, value: 72 }, sprite: { immediate: 1, string: 'Frame2' } },
  ],
})) };

describe('non-inherited world edits', () => {
  it.each([0, .3, 1])('keeps children and grandchildren bit-identical at t=%s, including sheared parent bases', (time) => {
    const original = structuredClone(track);
    const states = calculateWorldStates(track, time);
    const result = applyWorldLayerEdits(track, states, new Map([[0, { offsetX: 13, offsetY: -11, rotation: 52, scaleX: -1.5, scaleY: .7 }]]));
    expect(result.get(1)).toBe(states.get(1));
    expect(result.get(2)).toBe(states.get(2));
    expect(track).toEqual(original);
    expect(result.get(0)).not.toEqual(states.get(0));
  });

  it.each([0, 1])('rotates/scales around the actual sprite anchor without moving its pivot at t=%s', (time) => {
    const states = calculateWorldStates(track, time);
    for (const layer of track.layers) {
      const before = states.get(layer.id)!;
      const edit = { offsetX: 2, offsetY: -11, rotation: 73, scaleX: .8, scaleY: -2 };
      const after = applyWorldLayerEdits(track, states, new Map([[layer.id, edit]])).get(layer.id)!;
      const [x, y] = layerAnchor(layer, before.spriteName);
      expect(after.m00 * x + after.m01 * y + after.tx).toBeCloseTo(before.m00 * x + before.m01 * y + before.tx + 2, 12);
      expect(after.m10 * x + after.m11 * y + after.ty).toBeCloseTo(before.m10 * x + before.m11 * y + before.ty - 11, 12);
    }
  });

  it('multi-selected parent and child each receive one delta, not two', () => {
    const states = calculateWorldStates(track, .5);
    const edit = { ...DEFAULT_LAYER_EDIT, offsetX: 12, offsetY: 3 };
    const output = applyWorldLayerEdits(track, states, new Map([[0, edit], [1, edit]]));
    for (const id of [0, 1]) {
      expect(output.get(id)!.tx).toBeCloseTo(states.get(id)!.tx + 12, 12);
      expect(output.get(id)!.ty).toBeCloseTo(states.get(id)!.ty + 3, 12);
    }
    expect(output.get(2)).toBe(states.get(2));
  });

  it('normalizes scale limits without NaNs or a singular zero scale', () => {
    expect(normalizeLayerEdit({ scaleX: 0, scaleY: -200, rotation: NaN, offsetX: Infinity }))
      .toEqual({ scaleX: .01, scaleY: -100, rotation: 0, offsetX: 0, offsetY: 0 });
  });

  it('unwraps rotations at the seam in either direction', () => {
    expect(signedAngleDelta(179, -179)).toBe(2);
    expect(signedAngleDelta(-179, 179)).toBe(-2);
  });

  it('hit-tests transformed bounds in either winding, with optional touch slop', () => {
    const quad = [{ x: 0, y: 10 }, { x: 10, y: 0 }, { x: 20, y: 10 }, { x: 10, y: 20 }];
    expect(pointHitsQuad({ x: 10, y: 10 }, quad)).toBe(true);
    expect(pointHitsQuad({ x: 10, y: 10 }, quad.toReversed())).toBe(true);
    expect(pointHitsQuad({ x: 2, y: 2 }, quad)).toBe(false);
    expect(pointHitsQuad({ x: 22, y: 10 }, quad, 3)).toBe(true);
    expect(pointHitsQuad({ x: 24, y: 10 }, quad, 3)).toBe(false);
  });
});
