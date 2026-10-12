import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parseAnimationBin } from './bin-parser';
import { calculateWorldStates } from './animation';
import { applyIKRigs, jointWorld, type IKRig } from './ik-constraints';
import { applyWorldLayerEdits, DEFAULT_LAYER_EDIT } from './layer-transform';
import type { AnimationDocument } from './types';

const asset = process.env.ANIVIEWER_CONSTRAINT_ASSET;
describe.skipIf(!asset)('IK on private source animation keys', () => {
  it('keeps Off/idle exact and actively reaches explicit targets across keys and sprite swaps, including prior edits', () => {
    const bytes = readFileSync(asset!);
    const document: AnimationDocument = asset!.endsWith('.json') ? JSON.parse(bytes.toString())
      : parseAnimationBin(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), 'auto', asset!).document;
    const original = JSON.stringify(document);
    let samples = 0, solves = 0, maxJointError = 0, maxTipError = 0;
    for (const track of document.anims) {
      const times = [...new Set([0, ...track.layers.flatMap((layer) => layer.frames.map((frame) => frame.time))])].sort((a, b) => a - b);
      const duration = times.at(-1)!;
      const probes = [...new Set(times.flatMap((time, i) => [time, Math.max(0, time - 1e-6), Math.min(duration, time + 1e-6), (time + (times[i + 1] ?? time)) / 2]))];
      // Explicit test bones, not inferred anatomical joints or runtime asset-specific rules.
      for (let i = 0; i + 1 < track.layers.length; i += 2) {
        const first = track.layers[i]!.id, second = track.layers[i + 1]!.id;
        const rig: IKRig = { id: 'probe', name: 'Probe', enabled: true, bend: 'source', fallbackBend: 1, target: { space: 'offset', x: 0, y: 0 }, bones: [first, second].map((layerId) => ({ layerId, start: { x: 0, y: 0 }, end: { x: 30, y: 0 }, followers: [] })) };
        for (const time of probes) {
          const source = applyWorldLayerEdits(track, calculateWorldStates(track, time), new Map([[first, { ...DEFAULT_LAYER_EDIT, scaleX: -1.35, scaleY: .7, rotation: 37, offsetX: 17 }]]));
          for (const test of [rig, { ...rig, enabled: false, target: { space: 'world' as const, x: 300, y: -700 } }]) {
            const after = applyIKRigs(track, source, [test]);
            for (const [id, state] of source) expect(after.states.get(id)).toBe(state);
          }
          const start = jointWorld(track, source, first, rig.bones[0]!.start)!, end = jointWorld(track, source, first, rig.bones[0]!.end)!;
          const lowerStart = jointWorld(track, source, second, rig.bones[1]!.start)!, lowerEnd = jointWorld(track, source, second, rig.bones[1]!.end)!;
          const l1 = Math.hypot(end.x - start.x, end.y - start.y), l2 = Math.hypot(lowerEnd.x - lowerStart.x, lowerEnd.y - lowerStart.y);
          const reach = Math.max(l1, l2), target = { space: 'world' as const, x: start.x + reach * .8, y: start.y - reach * .6 };
          const result = applyIKRigs(track, source, [{ ...rig, target }]); samples++;
          if (l1 < 1e-9 || l2 < 1e-9) { expect(result.poses[0]!.status).toBe('degenerate'); continue; }
          const a = jointWorld(track, result.states, first, rig.bones[0]!.end)!;
          const b = jointWorld(track, result.states, second, rig.bones[1]!.start)!;
          const tip = jointWorld(track, result.states, second, rig.bones[1]!.end)!;
          maxJointError = Math.max(maxJointError, Math.hypot(a.x - b.x, a.y - b.y));
          maxTipError = Math.max(maxTipError, Math.hypot(target.x - tip.x, target.y - tip.y)); solves++;
          for (const [id, state] of source) if (id !== first && id !== second) expect(result.states.get(id)).toBe(state);
        }
      }
    }
    expect(solves).toBeGreaterThan(0); expect(maxJointError).toBeLessThan(1e-9); expect(maxTipError).toBeLessThan(1e-9);
    expect(JSON.stringify(document)).toBe(original);
    console.info(JSON.stringify({ tracks: document.anims.length, samples, solves, maxJointError, maxTipError, sourceKeyframesUnchanged: true }));
  }, 60_000);
});
