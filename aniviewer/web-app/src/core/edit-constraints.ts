import type { AnimationTrack, WorldState } from './types';
import { layerAnchor, type LayerTransformEdit } from './layer-transform';

export interface EditChannels { movement: boolean; rotation: boolean; scale: boolean }
export interface EditConstraint {
  driverId: number;
  memberIds: readonly number[];
  mode: 'rigid' | 'selective';
  enabled: boolean;
  channels: EditChannels;
}
export interface EditTarget { id: number; channels: EditChannels }
export interface TransformOperation {
  driverId: number;
  delta: LayerTransformEdit;
  targets: readonly EditTarget[];
  scaleSpace: 'local' | 'world';
}
export const ALL_EDIT_CHANNELS: Readonly<EditChannels> = { movement: true, rotation: true, scale: true };
const NONE: EditChannels = { movement: false, rotation: false, scale: false };
const intersect = (a: EditChannels, b: EditChannels): EditChannels => ({
  movement: a.movement && b.movement, rotation: a.rotation && b.rotation, scale: a.scale && b.scale,
});
const any = (c: EditChannels) => c.movement || c.rotation || c.scale;

export function cloneConstraints(rules: readonly EditConstraint[]): EditConstraint[] {
  return rules.map((rule) => ({ ...rule, memberIds: [...rule.memberIds], channels: { ...rule.channels } }));
}
export function cloneOperations(operations: readonly TransformOperation[]): TransformOperation[] {
  return operations.map((op) => ({ ...op, delta: { ...op.delta }, targets: op.targets.map((target) => ({ ...target, channels: { ...target.channels } })) }));
}

/** Validate disabled rules too: enabling a saved rule must never reveal an ambiguous graph. */
export function validateConstraints(track: AnimationTrack, rules: readonly EditConstraint[]): void {
  const ids = new Set(track.layers.map((layer) => layer.id));
  const drivers = new Set<number>();
  const owners = new Map<number, number>();
  for (const rule of rules) {
    if (!ids.has(rule.driverId)) throw new Error('The link driver is not in this animation.');
    if (drivers.has(rule.driverId)) throw new Error('Each driver can have only one link rule.');
    drivers.add(rule.driverId);
    if (!['rigid', 'selective'].includes(rule.mode)) throw new Error('Unknown link mode.');
    if (typeof rule.enabled !== 'boolean' || ['movement', 'rotation', 'scale'].some((channel) => typeof rule.channels[channel as keyof EditChannels] !== 'boolean')) throw new Error('Invalid link channels.');
    if (!rule.memberIds.length) throw new Error('Choose at least one linked segment.');
    for (const member of rule.memberIds) {
      if (!ids.has(member)) throw new Error('A linked segment is not in this animation.');
      if (member === rule.driverId) throw new Error('A segment cannot link to itself.');
      if (owners.has(member)) throw new Error('A segment cannot have two edit-link parents (or appear twice). Remove its other link first.');
      owners.set(member, rule.driverId);
    }
  }
  for (const id of ids) {
    const path = new Set<number>();
    let cursor: number | undefined = id;
    while (cursor !== undefined) {
      if (path.has(cursor)) throw new Error('This link would create a cycle.');
      path.add(cursor);
      cursor = owners.get(cursor);
    }
  }
}

/** Rigid members edit their group driver; selective members remain independently editable. */
export function editDriver(id: number, rules: readonly EditConstraint[]): number {
  const seen = new Set<number>();
  while (!seen.has(id)) {
    seen.add(id);
    const owner = rules.find((rule) => rule.enabled && rule.mode === 'rigid' && rule.memberIds.includes(id));
    if (!owner) break;
    id = owner.driverId;
  }
  return id;
}

export function resolveEditTargets(driverId: number, rules: readonly EditConstraint[]): EditTarget[] {
  const targets: EditTarget[] = [];
  const visited = new Set<number>();
  const visit = (id: number, channels: EditChannels) => {
    if (visited.has(id) || !any(channels)) return;
    visited.add(id);
    targets.push({ id, channels: { ...channels } });
    const rule = rules.find((entry) => entry.enabled && entry.driverId === id);
    if (!rule) return;
    const next = intersect(channels, rule.mode === 'rigid' ? ALL_EDIT_CHANNELS : rule.channels);
    for (const member of rule.memberIds) visit(member, next);
  };
  visit(driverId, ALL_EDIT_CHANNELS);
  return targets;
}

/** Capture scope at authoring time. Later link changes must not reinterpret earlier edits. */
export function planTransformOperations(
  before: ReadonlyMap<number, LayerTransformEdit>, after: ReadonlyMap<number, LayerTransformEdit>, rules: readonly EditConstraint[],
): TransformOperation[] {
  const depth = (id: number) => {
    let count = 0;
    const seen = new Set<number>();
    let rule = rules.find((r) => r.enabled && r.memberIds.includes(id));
    while (rule && !seen.has(rule.driverId)) {
      seen.add(rule.driverId); count++;
      const parent = rule.driverId;
      rule = rules.find((r) => r.enabled && r.memberIds.includes(parent));
    }
    return count;
  };
  const operations: TransformOperation[] = [];
  const covered = new Map<number, EditChannels>();
  for (const id of [...before.keys()].sort((a, b) => depth(a) - depth(b) || a - b)) {
    const initial = before.get(id)!, final = after.get(id) ?? initial;
    const claimed = covered.get(id) ?? NONE;
    const delta: LayerTransformEdit = {
      offsetX: claimed.movement ? 0 : final.offsetX - initial.offsetX,
      offsetY: claimed.movement ? 0 : final.offsetY - initial.offsetY,
      rotation: claimed.rotation ? 0 : final.rotation - initial.rotation,
      scaleX: claimed.scale ? 1 : final.scaleX / initial.scaleX,
      scaleY: claimed.scale ? 1 : final.scaleY / initial.scaleY,
    };
    const active = { movement: delta.offsetX !== 0 || delta.offsetY !== 0, rotation: delta.rotation !== 0, scale: delta.scaleX !== 1 || delta.scaleY !== 1 };
    if (!any(active)) continue;
    const targets = resolveEditTargets(id, rules).map((target) => ({ id: target.id, channels: intersect(target.channels, active) })).filter((target) => any(target.channels));
    for (const target of targets) {
      const old = covered.get(target.id) ?? NONE;
      covered.set(target.id, { movement: old.movement || target.channels.movement, rotation: old.rotation || target.channels.rotation, scale: old.scale || target.channels.scale });
    }
    operations.push({ driverId: id, delta, targets, scaleSpace: rules.some((r) => r.enabled && r.driverId === id) ? 'world' : 'local' });
  }
  return operations;
}

/** A pure, post-animation operation stack. Never feeds user edits into the source hierarchy. */
export function applyTransformOperations(
  track: AnimationTrack, states: ReadonlyMap<number, WorldState>, operations: readonly TransformOperation[],
): Map<number, WorldState> {
  const result = new Map(states);
  if (!operations.length) return result;
  const layers = new Map(track.layers.map((layer) => [layer.id, layer]));
  for (const op of operations) {
    const driver = result.get(op.driverId), layer = layers.get(op.driverId);
    if (!driver || !layer) continue;
    const [ax, ay] = layerAnchor(layer, driver.spriteName);
    const px = driver.m00 * ax + driver.m01 * ay + driver.tx;
    const py = driver.m10 * ax + driver.m11 * ay + driver.ty;
    for (const target of op.targets) {
      const state = result.get(target.id);
      if (!state) continue;
      const delta = op.delta, channels = target.channels;
      const radians = channels.rotation ? delta.rotation * Math.PI / 180 : 0;
      const c = Math.cos(radians), s = Math.sin(radians);
      const sx = channels.scale ? delta.scaleX : 1, sy = channels.scale ? delta.scaleY : 1;
      const dx = channels.movement ? delta.offsetX : 0, dy = channels.movement ? delta.offsetY : 0;
      let m00: number, m01: number, m10: number, m11: number, tx: number, ty: number;
      if (op.scaleSpace === 'local') {
        // Independent edits retain the pre-constraint tool's local-axis scaling semantics.
        m00 = (c * state.m00 - s * state.m10) * sx;
        m10 = (s * state.m00 + c * state.m10) * sx;
        m01 = (c * state.m01 - s * state.m11) * sy;
        m11 = (s * state.m01 + c * state.m11) * sy;
        tx = px + dx - m00 * ax - m01 * ay;
        ty = py + dy - m10 * ax - m11 * ay;
      } else {
        // D = T(p + move) R(angle) S(scale) T(-p). No inverse or TRS decomposition,
        // even if the game's animated driver matrix is sheared, reflected or singular.
        const a = c * sx, b = -s * sy, d = s * sx, e = c * sy;
        m00 = a * state.m00 + b * state.m10;
        m01 = a * state.m01 + b * state.m11;
        m10 = d * state.m00 + e * state.m10;
        m11 = d * state.m01 + e * state.m11;
        tx = px + dx + a * (state.tx - px) + b * (state.ty - py);
        ty = py + dy + d * (state.tx - px) + e * (state.ty - py);
      }
      result.set(target.id, { ...state, m00, m01, m10, m11, tx, ty });
    }
  }
  return result;
}
