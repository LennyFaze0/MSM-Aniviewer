import { describe, expect, it } from 'vitest';
import { Matrix, Transform } from 'pixi.js';
import { calculateWorldStates } from './animation';
import { applyIKRigs, jointOffset, jointWorld, solveIK, sourceIKBend, validateIKRigs, type IKRig } from './ik-constraints';
import type { AnimationTrack, WorldState } from './types';

const track: AnimationTrack = { name: 'IK animated affine fixture', width: 200, height: 200, layers: [0, 1, 2, 3].map((id) => ({
  id, name: `bone ${id}`, parent: id === 3 ? -1 : id - 1, src: 0, anchor_x: 3, anchor_y: 7,
  sprite_anchor_map: { swapped: [6, 11] as [number, number] },
  frames: [
    { time: 0, pos: { immediate: 0, x: 20 + id * 8, y: 30 }, rotation: { immediate: 0, value: id * 31 }, scale: { immediate: 0, x: 170, y: 55 }, sprite: { immediate: 1, string: 'first' } },
    { time: .5, pos: { immediate: 0, x: -6, y: 55 }, rotation: { immediate: 0, value: 72 }, scale: { immediate: 0, x: -30, y: 115 }, sprite: { immediate: 1, string: 'swapped' } },
    { time: 1, pos: { immediate: 0, x: 30, y: -7 }, scale: { immediate: 0, x: 0, y: 0 } },
  ],
})) };
const rig: IKRig = { id: 'limb', name: 'Limb', enabled: true, bend: 'source', fallbackBend: 1,
  target: { space: 'offset', x: 0, y: 0 }, bones: [
    { layerId: 0, start: { x: 0, y: 0 }, end: { x: 24, y: 0 }, followers: [2] },
    { layerId: 1, start: { x: 0, y: 0 }, end: { x: 24, y: 0 }, followers: [] },
  ],
};
const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);
const point = (s: WorldState, x: number, y: number) => ({ x: s.m00 * x + s.m01 * y + s.tx, y: s.m10 * x + s.m11 * y + s.ty });

describe('analytic IK geometry', () => {
  it('uses the bind fallback for numerically collinear joints, independent of bone length and roundoff sign', () => {
    for (const size of [1e-6, 1, 1e6]) for (const epsilon of [-1e-12, 0, 1e-12]) {
      expect(sourceIKBend({ x: size, y: 0 }, { x: size, y: size * epsilon }, 1)).toBe(1);
      expect(sourceIKBend({ x: size, y: 0 }, { x: -size, y: size * epsilon }, -1)).toBe(-1);
    }
    expect(sourceIKBend({ x: 1, y: 0 }, { x: 0, y: 1 }, 1)).toBe(-1);
    expect(sourceIKBend({ x: 1, y: 0 }, { x: 0, y: -1 }, -1)).toBe(1);
  });
  it('reaches targets in all quadrants, both bends, and clamps both reach limits without stretching', () => {
    for (const bend of [1, -1] as const) for (const lengths of [[10, 10], [20, 7], [7, 20], [.0001, .0002], [1e6, 2e6]]) {
      const [l1, l2] = lengths as [number, number];
      for (let i = 0; i < 32; i++) {
        const angle = i * Math.PI / 16, reach = (l1 + l2) * (i % 8) / 5;
        const root = { x: 3, y: -6 }, target = { x: root.x + reach * Math.cos(angle), y: root.y + reach * Math.sin(angle) };
        const solved = solveIK(root, { x: l1, y: 0 }, { x: 0, y: l2 }, target, bend)!;
        expect(solved).toBeDefined();
        const [a, b, c] = solved.points as [{ x: number; y: number }, { x: number; y: number }, { x: number; y: number }];
        expect(distance(a, b) / l1).toBeCloseTo(1, 8);
        expect(distance(b, c) / l2).toBeCloseTo(1, 8);
        const expectedReach = Math.max(Math.abs(l1 - l2), Math.min(l1 + l2, reach));
        expect(distance(a, c) / (l1 + l2)).toBeCloseTo(expectedReach / (l1 + l2), 10);
        expect(solved.residual / (l1 + l2)).toBeCloseTo(Math.abs(reach - expectedReach) / (l1 + l2), 10);
      }
    }
  });
  it('aims a single bone, preserves folded direction, and rejects collapsed or invalid inputs', () => {
    expect(solveIK({ x: 0, y: 0 }, { x: 10, y: 0 }, undefined, { x: 0, y: 20 }, 1)).toMatchObject({ points: [{ x: 0, y: 0 }, { x: 0, y: 10 }], residual: 10, limited: true });
    expect(solveIK({ x: 0, y: 0 }, { x: 0, y: 10 }, { x: 0, y: -10 }, { x: 0, y: 0 }, -1)!.points[1]).toEqual({ x: 0, y: 10 });
    expect(solveIK({ x: 0, y: 0 }, { x: 0, y: 0 }, undefined, { x: 2, y: 3 }, 1)).toBeUndefined();
    expect(solveIK({ x: 0, y: 0 }, { x: 2, y: 0 }, { x: 0, y: 0 }, { x: 2, y: 3 }, 1)).toBeUndefined();
    expect(solveIK({ x: 0, y: 0 }, { x: Infinity, y: 0 }, undefined, { x: 2, y: 3 }, 1)).toBeUndefined();
  });
});

describe('IK animation projection', () => {
  it('source bend preserves the original elbow branch for small edits on a connected chain', () => {
    const source = calculateWorldStates(track, 0), upper = source.get(0)!, lower = source.get(1)!;
    source.set(0, { ...upper, m00: 1, m01: 0, m10: 0, m11: 1, tx: -3, ty: -7 });
    source.set(1, { ...lower, m00: .6, m01: -.8, m10: .8, m11: .6, tx: 24 - .6 * 3 + .8 * 7, ty: -.8 * 3 - .6 * 7 });
    const result = applyIKRigs(track, source, [{ ...rig, target: { space: 'offset', x: 1e-6, y: -1e-6 } }]);
    for (const id of [0, 1]) for (const key of ['m00', 'm01', 'm10', 'm11', 'tx', 'ty'] as const) {
      expect(Math.abs(result.states.get(id)![key] - source.get(id)![key])).toBeLessThan(1e-4);
    }
  });
  it.each([0, .17, .49999, .5, .50001, .8, 1])('is exactly identity while off or unedited, including sprite swaps and singular keys, t=%s', (time) => {
    const source = calculateWorldStates(track, time);
    for (const test of [rig, { ...rig, enabled: false, target: { space: 'world' as const, x: -200, y: 700 } }]) {
      const result = applyIKRigs(track, source, [test]);
      expect(result.states).toEqual(source);
      for (const [id, state] of source) expect(result.states.get(id)).toBe(state);
    }
  });
  it.each([0, .17, .49999, .5, .50001, .8])('connects joints exactly while preserving each full affine shape and explicit followers, t=%s', (time) => {
    const json = JSON.stringify(track), source = calculateWorldStates(track, time), snapshot = structuredClone(source);
    const a = jointWorld(track, source, 0, rig.bones[0]!.start)!, b = jointWorld(track, source, 0, rig.bones[0]!.end)!;
    const c = jointWorld(track, source, 1, rig.bones[1]!.start)!, d = jointWorld(track, source, 1, rig.bones[1]!.end)!;
    const l1 = distance(a, b), l2 = distance(c, d), reach = Math.max(l1, l2);
    const target = { space: 'world' as const, x: a.x + .8 * reach, y: a.y + .6 * reach };
    const setup = { ...rig, target };
    const result = applyIKRigs(track, source, [setup]), after = result.states;
    expect(result.poses[0]!.status).toBe('solved');
    expect(distance(jointWorld(track, after, 0, rig.bones[0]!.end)!, jointWorld(track, after, 1, rig.bones[1]!.start)!)).toBeLessThan(1e-10);
    expect(distance(jointWorld(track, after, 1, rig.bones[1]!.end)!, target)).toBeLessThan(1e-10);
    expect(distance(jointWorld(track, after, 0, rig.bones[0]!.start)!, a)).toBeLessThan(1e-10);
    for (const id of [0, 1, 2]) {
      const before = source.get(id)!, next = after.get(id)!;
      // Independent corner-distance oracle: no shape stretch, shear loss or reflection loss.
      expect(distance(point(before, 0, 0), point(before, 19, 31))).toBeCloseTo(distance(point(next, 0, 0), point(next, 19, 31)), 10);
      expect(before.m00 * before.m11 - before.m01 * before.m10).toBeCloseTo(next.m00 * next.m11 - next.m01 * next.m10, 10);
      const matrix = new Matrix(next.m00, next.m10, next.m01, next.m11, next.tx, next.ty), pixi = new Transform(); pixi.setFromMatrix(matrix);
      for (const key of ['a', 'b', 'c', 'd', 'tx', 'ty'] as const) expect(pixi.matrix[key]).toBeCloseTo(matrix[key], 10);
    }
    expect(distance(point(source.get(0)!, 3, 8), point(source.get(2)!, 19, 4))).toBeCloseTo(distance(point(after.get(0)!, 3, 8), point(after.get(2)!, 19, 4)), 10);
    expect(after.get(3)).toBe(source.get(3));
    expect(applyIKRigs(track, source, [setup])).toEqual(result);
    expect(source).toEqual(snapshot); expect(JSON.stringify(track)).toBe(json);
  });
  it('keeps degenerate poses unchanged and maps explicit joints through reflected/sheared anchors', () => {
    const source = calculateWorldStates(track, .5), world = { x: -28, y: 14 };
    const local = jointOffset(track, source, 1, world);
    expect(distance(jointWorld(track, source, 1, local)!, world)).toBeLessThan(1e-10);
    const collapsed = calculateWorldStates(track, 1);
    expect(() => jointOffset(track, collapsed, 1, world)).toThrow(/collapsed/);
    const result = applyIKRigs(track, collapsed, [{ ...rig, target: { space: 'offset', x: 5, y: -10 } }]);
    expect(result.states).toEqual(collapsed); expect(result.poses[0]!.status).toBe('degenerate');
  });
  it('validates disjoint explicit ownership, finite inputs, missing layers and zero-length bones', () => {
    expect(() => validateIKRigs(track, [rig])).not.toThrow();
    expect(() => validateIKRigs(track, [rig, { ...rig, id: 'other', enabled: false }])).toThrow(/two IK/);
    expect(() => validateIKRigs(track, [{ ...rig, bones: [rig.bones[0]!, { ...rig.bones[1]!, followers: [0] }] }])).toThrow(/two IK/);
    expect(() => validateIKRigs(track, [{ ...rig, target: { space: 'world', x: NaN, y: 0 } }])).toThrow(/finite/);
    expect(() => validateIKRigs(track, [{ ...rig, bones: [{ ...rig.bones[0]!, layerId: 900 }] }])).toThrow(/missing/);
    expect(() => validateIKRigs(track, [{ ...rig, bones: [{ ...rig.bones[0]!, end: rig.bones[0]!.start }] }])).toThrow(/distinct/);
  });
});
