import type { AnimationTrack, WorldState } from './types';
import { layerAnchor, type ViewPoint } from './layer-transform';

export interface IKBone {
  layerId: number;
  start: ViewPoint;
  end: ViewPoint;
  followers: readonly number[];
}
export interface IKRig {
  id: string;
  name: string;
  enabled: boolean;
  bones: readonly IKBone[];
  target: ViewPoint & { space: 'offset' | 'world' };
  bend: 'source' | 'positive' | 'negative';
  fallbackBend: 1 | -1;
}
export interface IKPose {
  id: string;
  points: ViewPoint[];
  sourceTip: ViewPoint;
  target: ViewPoint;
  status: 'disabled' | 'idle' | 'solved' | 'limited' | 'degenerate';
  residual: number;
}

const add = (a: ViewPoint, b: ViewPoint): ViewPoint => ({ x: a.x + b.x, y: a.y + b.y });
const sub = (a: ViewPoint, b: ViewPoint): ViewPoint => ({ x: a.x - b.x, y: a.y - b.y });
const length = (a: ViewPoint): number => Math.hypot(a.x, a.y);
const finite = (point: ViewPoint): boolean => Number.isFinite(point.x) && Number.isFinite(point.y);

export function sourceIKBend(first: ViewPoint, second: ViewPoint | undefined, fallback: 1 | -1): 1 | -1 {
  if (!second) return fallback;
  const a = length(first), b = length(second);
  const sine = first.x / a * (second.y / b) - first.y / a * (second.x / b);
  // At a straight/folded pose roundoff cannot meaningfully choose an elbow side.
  return Number.isFinite(sine) && Math.abs(sine) > 1e-9 ? (-Math.sign(sine) as 1 | -1) : fallback;
}

export function cloneIKRigs(rigs: readonly IKRig[]): IKRig[] {
  return rigs.map((rig) => ({ ...rig, target: { ...rig.target }, bones: rig.bones.map((bone) => ({ ...bone, start: { ...bone.start }, end: { ...bone.end }, followers: [...bone.followers] })) }));
}

export function validateIKRigs(track: AnimationTrack, rigs: readonly IKRig[]): void {
  const validIds = new Set(track.layers.map((layer) => layer.id)), ids = new Set<string>(), owned = new Set<number>();
  for (const rig of rigs) {
    if (!rig.id || ids.has(rig.id)) throw new Error('Each IK chain needs a unique ID.');
    ids.add(rig.id);
    if (rig.bones.length < 1 || rig.bones.length > 2) throw new Error('IK supports one or two segments per chain.');
    if (!finite(rig.target) || !['offset', 'world'].includes(rig.target.space)) throw new Error('IK target coordinates must be finite.');
    if (!['source', 'positive', 'negative'].includes(rig.bend) || ![1, -1].includes(rig.fallbackBend) || typeof rig.enabled !== 'boolean') throw new Error('Invalid IK settings.');
    for (const bone of rig.bones) {
      if (!finite(bone.start) || !finite(bone.end) || length(sub(bone.end, bone.start)) < 1e-8) throw new Error('Place distinct joints for each segment.');
      for (const id of [bone.layerId, ...bone.followers]) {
        if (!validIds.has(id)) throw new Error('An IK segment is missing from this animation.');
        if (owned.has(id)) throw new Error('A segment cannot belong to two IK bones or chains. Remove its other IK assignment or rigid link first.');
        owned.add(id);
      }
    }
  }
}

/** Joint offsets are relative to the current sprite's authored anchor, not texture bounds. */
export function jointWorld(track: AnimationTrack, states: ReadonlyMap<number, WorldState>, id: number, offset: ViewPoint): ViewPoint | undefined {
  const layer = track.layers.find((entry) => entry.id === id), state = states.get(id);
  if (!layer || !state) return;
  const [ax, ay] = layerAnchor(layer, state.spriteName), x = ax + offset.x, y = ay + offset.y;
  return { x: state.m00 * x + state.m01 * y + state.tx, y: state.m10 * x + state.m11 * y + state.ty };
}

/** Used only during explicit joint placement. Playback never inverts a bone matrix. */
export function jointOffset(track: AnimationTrack, states: ReadonlyMap<number, WorldState>, id: number, point: ViewPoint): ViewPoint {
  const layer = track.layers.find((entry) => entry.id === id), state = states.get(id);
  if (!layer || !state || !finite(point)) throw new Error('Select an available segment before placing a joint.');
  const norm = Math.max(Math.abs(state.m00), Math.abs(state.m01), Math.abs(state.m10), Math.abs(state.m11));
  const a = state.m00 / norm, b = state.m01 / norm, c = state.m10 / norm, d = state.m11 / norm;
  const det = a * d - b * c;
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) throw new Error('This segment is collapsed at this frame. Scrub to a visible pose before placing joints.');
  const [ax, ay] = layerAnchor(layer, state.spriteName), x = (point.x - state.tx) / norm, y = (point.y - state.ty) / norm;
  const result = { x: (d * x - b * y) / det - ax, y: (a * y - c * x) / det - ay };
  if (!finite(result)) throw new Error('The joint position cannot be represented on this segment.');
  return result;
}

export interface IKSolution { points: ViewPoint[]; residual: number; limited: boolean }

/** Closed-form one/two-bone solve. Targets outside the reachable annulus are clamped. */
export function solveIK(root: ViewPoint, firstVector: ViewPoint, secondVector: ViewPoint | undefined, target: ViewPoint, bend: 1 | -1): IKSolution | undefined {
  const first = length(firstVector), second = secondVector ? length(secondVector) : 0;
  if (![root, firstVector, target].every(finite) || (secondVector && !finite(secondVector)) || first < 1e-9 || (secondVector && second < 1e-9)) return;
  const distance = length(sub(target, root));
  const direction = distance > 1e-12 ? { x: (target.x - root.x) / distance, y: (target.y - root.y) / distance }
    : { x: firstVector.x / first, y: firstVector.y / first };
  let points: ViewPoint[];
  if (!secondVector) {
    points = [root, { x: root.x + direction.x * first, y: root.y + direction.y * first }];
  } else {
    const reach = Math.max(Math.abs(first - second), Math.min(first + second, distance));
    const end = { x: root.x + direction.x * reach, y: root.y + direction.y * reach };
    let knee: ViewPoint;
    if (reach < 1e-12) {
      // Equal-length bones folded onto the root have infinitely many valid elbows.
      // Retain the original upper direction rather than dividing by zero.
      knee = add(root, firstVector);
    } else {
      const unit = Math.max(first, second, reach), l1 = first / unit, l2 = second / unit, r = reach / unit;
      const cosine = Math.max(-1, Math.min(1, (l1 * l1 + r * r - l2 * l2) / (2 * l1 * r)));
      const along = first * cosine, perpendicular = first * Math.sqrt(Math.max(0, 1 - cosine * cosine)) * bend;
      knee = { x: root.x + direction.x * along - direction.y * perpendicular, y: root.y + direction.y * along + direction.x * perpendicular };
    }
    points = [root, knee, end];
  }
  if (!points.every(finite)) return;
  const residual = length(sub(points.at(-1)!, target));
  return { points, residual, limited: residual > Math.max(first + second, 1) * 1e-9 };
}

export function applyIKRigs(track: AnimationTrack, source: ReadonlyMap<number, WorldState>, rigs: readonly IKRig[]): { states: Map<number, WorldState>; poses: IKPose[] } {
  const states = new Map(source), poses: IKPose[] = [];
  for (const rig of rigs) {
    const segments = rig.bones.map((bone) => ({
      bone, start: jointWorld(track, source, bone.layerId, bone.start), end: jointWorld(track, source, bone.layerId, bone.end),
    }));
    if (segments.some((segment) => !segment.start || !segment.end)) continue;
    const root = segments[0]!.start!, sourceTip = segments.at(-1)!.end!;
    const target = rig.target.space === 'world' ? { x: rig.target.x, y: rig.target.y } : add(sourceTip, rig.target);
    const sourcePoints = [root, ...segments.map((segment) => segment.end!)];
    const pose: IKPose = { id: rig.id, points: sourcePoints, sourceTip, target, status: rig.enabled ? 'idle' : 'disabled', residual: 0 };
    poses.push(pose);
    // Off is a literal no-write branch. An untouched, animation-relative target is
    // also an exact identity, including existing keys with intentional joint offsets.
    if (!rig.enabled || (rig.bend === 'source' && target.x === sourceTip.x && target.y === sourceTip.y)) continue;
    const vectors = segments.map((segment) => sub(segment.end!, segment.start!));
    const bend = rig.bend === 'source' ? sourceIKBend(vectors[0]!, vectors[1], rig.fallbackBend) : rig.bend === 'positive' ? 1 : -1;
    const solution = solveIK(root, vectors[0]!, vectors[1], target, bend);
    if (!solution) { pose.status = 'degenerate'; continue; }
    pose.points = solution.points; pose.residual = solution.residual; pose.status = solution.limited ? 'limited' : 'solved';
    for (const [index, segment] of segments.entries()) {
      const before = segment.start!, after = solution.points[index]!;
      const oldVector = vectors[index]!, newVector = sub(solution.points[index + 1]!, after);
      const angle = Math.atan2(newVector.y, newVector.x) - Math.atan2(oldVector.y, oldVector.x);
      const c = Math.cos(angle), s = Math.sin(angle);
      for (const id of [segment.bone.layerId, ...segment.bone.followers]) {
        const state = source.get(id);
        if (!state) continue;
        states.set(id, {
          ...state,
          m00: c * state.m00 - s * state.m10, m01: c * state.m01 - s * state.m11,
          m10: s * state.m00 + c * state.m10, m11: s * state.m01 + c * state.m11,
          tx: after.x + c * (state.tx - before.x) - s * (state.ty - before.y),
          ty: after.y + s * (state.tx - before.x) + c * (state.ty - before.y),
        });
      }
    }
  }
  return { states, poses };
}
