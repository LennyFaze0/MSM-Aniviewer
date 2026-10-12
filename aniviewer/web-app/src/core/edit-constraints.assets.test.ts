import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parseAnimationBin } from './bin-parser';
import { calculateWorldStates } from './animation';
import { ALL_EDIT_CHANNELS, applyTransformOperations, planTransformOperations, type EditConstraint } from './edit-constraints';
import { DEFAULT_LAYER_EDIT, layerAnchor } from './layer-transform';
import type { AnimationDocument } from './types';

// Opt-in private corpus. No game files, paths or rig-specific branches are shipped.
const asset = process.env.ANIVIEWER_CONSTRAINT_ASSET;
describe.skipIf(!asset)('edit links against an untouched local animation asset', () => {
  it('preserves the common affine edit for every parent at keys, between keys, and around swaps', () => {
    const bytes = readFileSync(asset!);
    const document: AnimationDocument = asset!.toLowerCase().endsWith('.json') ? JSON.parse(bytes.toString())
      : parseAnimationBin(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), 'auto', asset!).document;
    const source = JSON.stringify(document);
    const delta = { offsetX: 17.25, offsetY: -9.75, rotation: 53, scaleX: -1.6, scaleY: .73 };
    let maxPointError = 0, samples = 0, points = 0, parentCases = 0;
    for (const track of document.anims) {
      const times = [...new Set([0, ...track.layers.flatMap((layer) => layer.frames.map((frame) => frame.time))])].sort((a, b) => a - b);
      const duration = times.at(-1)!;
      const probeTimes = [...new Set(times.flatMap((time, i) => [time, Math.max(0, time - 1e-6), Math.min(duration, time + 1e-6), (time + (times[i + 1] ?? time)) / 2]))];
      for (const driver of track.layers) {
        const members = new Set<number>(), pending = [driver.id];
        while (pending.length) {
          const parent = pending.pop();
          for (const layer of track.layers) if (layer.parent === parent && layer.id !== driver.id && !members.has(layer.id)) { members.add(layer.id); pending.push(layer.id); }
        }
        if (!members.size) continue;
        parentCases++;
        const rule: EditConstraint = { driverId: driver.id, memberIds: [...members], mode: 'rigid', enabled: true, channels: { ...ALL_EDIT_CHANNELS } };
        const operations = planTransformOperations(new Map([[driver.id, { ...DEFAULT_LAYER_EDIT }]]), new Map([[driver.id, delta]]), [rule]);
        for (const time of probeTimes) {
          samples++;
          const base = calculateWorldStates(track, time), edited = applyTransformOperations(track, base, operations);
          const state = base.get(driver.id)!, [ax, ay] = layerAnchor(driver, state.spriteName);
          const px = state.m00 * ax + state.m01 * ay + state.tx, py = state.m10 * ax + state.m11 * ay + state.ty;
          const angle = delta.rotation * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle);
          for (const [id, original] of base) {
            const actual = edited.get(id)!;
            if (id !== driver.id && !members.has(id)) { expect(actual).toBe(original); continue; }
            for (const [x, y] of [[0, 0], [16, 0], [0, 16], [16, 16]]) {
              const u = (original.m00 * x! + original.m01 * y! + original.tx - px) * delta.scaleX;
              const v = (original.m10 * x! + original.m11 * y! + original.ty - py) * delta.scaleY;
              const expectedX = px + delta.offsetX + c * u - s * v, expectedY = py + delta.offsetY + s * u + c * v;
              const error = Math.hypot(actual.m00 * x! + actual.m01 * y! + actual.tx - expectedX, actual.m10 * x! + actual.m11 * y! + actual.ty - expectedY);
              maxPointError = Math.max(maxPointError, error); points++;
            }
          }
        }
      }
    }
    expect(parentCases).toBeGreaterThan(0);
    expect(maxPointError).toBeLessThan(1e-9);
    expect(JSON.stringify(document)).toBe(source);
    console.info(JSON.stringify({ tracks: document.anims.length, parentCases, samples, points, maxPointError, sourceKeyframesUnchanged: true }));
  });
});
