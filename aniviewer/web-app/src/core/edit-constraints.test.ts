import { describe, expect, it } from 'vitest';
import { Matrix, Transform } from 'pixi.js';
import { calculateWorldStates } from './animation';
import { ALL_EDIT_CHANNELS, applyTransformOperations, editDriver, planTransformOperations, resolveEditTargets, validateConstraints, type EditConstraint } from './edit-constraints';
import { applyWorldLayerEdits, DEFAULT_LAYER_EDIT, layerAnchor } from './layer-transform';
import type { AnimationTrack, WorldState } from './types';

export const animatedConstraintTrack: AnimationTrack = { name: 'Animated nested affine rig', width: 200, height: 200, layers: [0, 1, 2, 3].map((id) => ({
  id, name: `segment ${id}`, parent: id === 3 ? -1 : id - 1, src: 0, anchor_x: 8, anchor_y: 5,
  sprite_anchor_map: { swapped: [4, 12] as [number, number] },
  frames: [
    { time: 0, pos: { immediate: 0, x: 20 + id * 8, y: 30 }, rotation: { immediate: 0, value: id * 31 }, scale: { immediate: 0, x: 170, y: 55 }, sprite: { immediate: 1, string: 'first' } },
    { time: .5, pos: { immediate: 0, x: -6, y: 55 }, rotation: { immediate: 0, value: 72 }, scale: { immediate: 0, x: -30, y: 115 }, sprite: { immediate: 1, string: 'swapped' } },
    { time: 1, pos: { immediate: 0, x: 30, y: -7 }, scale: { immediate: 0, x: 0, y: 0 } },
  ],
})) };
export const rigidRule: EditConstraint = { driverId: 0, memberIds: [1, 2], mode: 'rigid', enabled: true, channels: { ...ALL_EDIT_CHANNELS } };
const change = { offsetX: 7, offsetY: -11, rotation: 43, scaleX: -1.7, scaleY: .6 };
const keys = ['m00', 'm01', 'm10', 'm11', 'tx', 'ty'] as const;
const assertMatrix = (actual: WorldState, expected: WorldState) => keys.forEach((key) => expect(actual[key]).toBeCloseTo(expected[key], 10));

describe('edit-link affine projection', () => {
  it.each([0, .17, .49999, .5, .50001, .8, 1])('applies one common transform without changing existing keyframes at t=%s', (time) => {
    const track = structuredClone(animatedConstraintTrack), source = JSON.stringify(track);
    const states = calculateWorldStates(track, time);
    const operations = planTransformOperations(new Map([[0, { ...DEFAULT_LAYER_EDIT }]]), new Map([[0, change]]), [rigidRule]);
    const result = applyTransformOperations(track, states, operations);
    const parent = states.get(0)!, [ax, ay] = layerAnchor(track.layers[0]!, parent.spriteName);
    const px = parent.m00 * ax + parent.m01 * ay + parent.tx, py = parent.m10 * ax + parent.m11 * ay + parent.ty;
    // Independent point oracle, not the implementation's matrix coefficients.
    const project = (x: number, y: number) => {
      x = (x - px) * change.scaleX; y = (y - py) * change.scaleY;
      const r = change.rotation * Math.PI / 180;
      return [px + change.offsetX + Math.cos(r) * x - Math.sin(r) * y, py + change.offsetY + Math.sin(r) * x + Math.cos(r) * y];
    };
    for (const id of [0, 1, 2]) {
      const before = states.get(id)!, after = result.get(id)!;
      for (const [x, y] of [[0, 0], [4, 7], [16, 16], [8, 5]]) {
        const expected = project(before.m00 * x! + before.m01 * y! + before.tx, before.m10 * x! + before.m11 * y! + before.ty);
        expect(after.m00 * x! + after.m01 * y! + after.tx).toBeCloseTo(expected[0]!, 10);
        expect(after.m10 * x! + after.m11 * y! + after.ty).toBeCloseTo(expected[1]!, 10);
      }
      expect(after.spriteName).toBe(before.spriteName);
      expect(after.opacity).toBe(before.opacity);
      expect(keys.every((key) => Number.isFinite(after[key]))).toBe(true);
    }
    expect(result.get(3)).toBe(states.get(3));
    expect(JSON.stringify(track)).toBe(source);
    // Repeated render/scrub/export evaluation cannot accumulate changes.
    expect(applyTransformOperations(track, states, operations)).toEqual(result);
  });

  it.each(Array.from({ length: 8 }, (_, mask) => mask))('selective channel mask %s changes only the requested operations', (mask) => {
    const channels = { movement: Boolean(mask & 1), rotation: Boolean(mask & 2), scale: Boolean(mask & 4) };
    const rules: EditConstraint[] = [{ ...rigidRule, mode: 'selective', channels }];
    const before = new Map([[0, { ...DEFAULT_LAYER_EDIT }]]), after = new Map([[0, change]]);
    const states = calculateWorldStates(animatedConstraintTrack, .2);
    const result = applyTransformOperations(animatedConstraintTrack, states, planTransformOperations(before, after, rules));
    const filtered = { offsetX: channels.movement ? change.offsetX : 0, offsetY: channels.movement ? change.offsetY : 0, rotation: channels.rotation ? change.rotation : 0, scaleX: channels.scale ? change.scaleX : 1, scaleY: channels.scale ? change.scaleY : 1 };
    const expected = applyTransformOperations(animatedConstraintTrack, states, planTransformOperations(before, new Map([[0, filtered]]), [rigidRule]));
    for (const id of [1, 2]) assertMatrix(result.get(id)!, expected.get(id)!);
    if (!mask) { expect(result.get(1)).toBe(states.get(1)); expect(result.get(2)).toBe(states.get(2)); }
  });

  it('off retains the independent transform behavior, including local-axis scale and untouched descendants', () => {
    const states = calculateWorldStates(animatedConstraintTrack, .3);
    const operations = planTransformOperations(new Map([[0, { ...DEFAULT_LAYER_EDIT }]]), new Map([[0, change]]), [{ ...rigidRule, enabled: false }]);
    const output = applyTransformOperations(animatedConstraintTrack, states, operations);
    assertMatrix(output.get(0)!, applyWorldLayerEdits(animatedConstraintTrack, states, new Map([[0, change]])).get(0)!);
    expect(output.get(1)).toBe(states.get(1)); expect(output.get(2)).toBe(states.get(2));
  });

  it('retains shear and reflections through the actual Pixi transform decomposition', () => {
    const states = calculateWorldStates(animatedConstraintTrack, .3);
    const result = applyTransformOperations(animatedConstraintTrack, states, planTransformOperations(new Map([[0, { ...DEFAULT_LAYER_EDIT }]]), new Map([[0, change]]), [rigidRule]));
    for (const state of result.values()) {
      const matrix = new Matrix(state.m00, state.m10, state.m01, state.m11, state.tx, state.ty);
      const transform = new Transform(); transform.setFromMatrix(matrix);
      const reconstructed = transform.matrix;
      for (const key of ['a', 'b', 'c', 'd', 'tx', 'ty'] as const) expect(reconstructed[key]).toBeCloseTo(matrix[key], 10);
    }
  });
});

describe('edit-link graph', () => {
  it('intersects nested channels, redirects rigid members, and rejects ambiguous ownership/cycles', () => {
    const rules: EditConstraint[] = [{ ...rigidRule, memberIds: [1], mode: 'selective', channels: { movement: true, rotation: false, scale: true } }, { ...rigidRule, driverId: 1, memberIds: [2] }];
    validateConstraints(animatedConstraintTrack, rules);
    expect(resolveEditTargets(0, rules)).toEqual([{ id: 0, channels: ALL_EDIT_CHANNELS }, { id: 1, channels: rules[0]!.channels }, { id: 2, channels: rules[0]!.channels }]);
    expect(editDriver(2, rules)).toBe(1);
    expect(editDriver(2, rules.map((rule) => ({ ...rule, mode: 'rigid' })))).toBe(0);
    expect(() => validateConstraints(animatedConstraintTrack, [...rules, { ...rigidRule, driverId: 2, memberIds: [0], enabled: false }])).toThrow(/cycle/);
    expect(() => validateConstraints(animatedConstraintTrack, [rigidRule, { ...rigidRule, driverId: 3, memberIds: [2] }])).toThrow(/two edit-link/);
    expect(() => validateConstraints(animatedConstraintTrack, [{ ...rigidRule, memberIds: [0] }])).toThrow(/itself/);
    expect(() => validateConstraints(animatedConstraintTrack, [{ ...rigidRule, memberIds: [999] }])).toThrow(/not in/);
  });

  it('multi-selection claims each channel once but allows independently selected blocked channels', () => {
    const rule: EditConstraint = { ...rigidRule, mode: 'selective', channels: { movement: true, rotation: false, scale: false } };
    const before = new Map([2, 0, 1].map((id) => [id, { ...DEFAULT_LAYER_EDIT }]));
    const after = new Map([2, 0, 1].map((id) => [id, { ...DEFAULT_LAYER_EDIT, offsetX: 10, rotation: 15 }]));
    const operations = planTransformOperations(before, after, [rule]);
    expect(operations.map((op) => op.driverId)).toEqual([0, 1, 2]);
    expect(operations.slice(1).every((op) => op.delta.offsetX === 0 && op.delta.rotation === 15)).toBe(true);
    const moveOnly = planTransformOperations(before, new Map([2, 0, 1].map((id) => [id, { ...DEFAULT_LAYER_EDIT, offsetX: 10 }])), [rigidRule]);
    expect(moveOnly).toHaveLength(1);
  });
});
