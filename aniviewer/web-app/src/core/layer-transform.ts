import type { AnimationLayer, AnimationTrack, WorldState } from './types';

export interface LayerTransformEdit {
  offsetX: number;
  offsetY: number;
  rotation: number;
  scaleX: number;
  scaleY: number;
}

export const DEFAULT_LAYER_EDIT: Readonly<LayerTransformEdit> = {
  offsetX: 0, offsetY: 0, rotation: 0, scaleX: 1, scaleY: 1,
};

export function normalizeLayerEdit(edit: Partial<LayerTransformEdit>): LayerTransformEdit {
  const finite = (value: number | undefined, fallback: number) => Number.isFinite(value) ? Number(value) : fallback;
  const scale = (value: number | undefined) => {
    const number = finite(value, 1);
    return (number < 0 ? -1 : 1) * Math.max(.01, Math.min(100, Math.abs(number)));
  };
  return {
    offsetX: finite(edit.offsetX, 0), offsetY: finite(edit.offsetY, 0),
    rotation: finite(edit.rotation, 0), scaleX: scale(edit.scaleX), scaleY: scale(edit.scaleY),
  };
}

export function layerAnchor(layer: AnimationLayer, spriteName: string): [number, number] {
  return layer.sprite_anchor_map?.[spriteName]
    ?? Object.entries(layer.sprite_anchor_map ?? {}).find(([name]) => name.toLowerCase() === spriteName.toLowerCase())?.[1]
    ?? [layer.anchor_x, layer.anchor_y];
}

/** Apply user edits AFTER evaluating the original hierarchy. Children never inherit these offsets. */
export function applyWorldLayerEdits(
  track: AnimationTrack, states: ReadonlyMap<number, WorldState>, edits: ReadonlyMap<number, LayerTransformEdit>,
): Map<number, WorldState> {
  const result = new Map(states);
  for (const layer of track.layers) {
    const state = states.get(layer.id);
    const edit = edits.get(layer.id);
    if (!state || !edit) continue;
    const [ax, ay] = layerAnchor(layer, state.spriteName);
    const px = state.m00 * ax + state.m01 * ay + state.tx + edit.offsetX;
    const py = state.m10 * ax + state.m11 * ay + state.ty + edit.offsetY;
    const angle = edit.rotation * Math.PI / 180;
    const c = Math.cos(angle), s = Math.sin(angle);
    // Scale along the sprite's axes, then rotate in world space around its authored anchor.
    // Keeping the affine matrix avoids lossy TRS decomposition under non-uniform parent scales.
    const m00 = (c * state.m00 - s * state.m10) * edit.scaleX;
    const m10 = (s * state.m00 + c * state.m10) * edit.scaleX;
    const m01 = (c * state.m01 - s * state.m11) * edit.scaleY;
    const m11 = (s * state.m01 + c * state.m11) * edit.scaleY;
    result.set(layer.id, { ...state, m00, m01, m10, m11, tx: px - m00 * ax - m01 * ay, ty: py - m10 * ax - m11 * ay });
  }
  return result;
}

export function signedAngleDelta(from: number, to: number): number {
  return ((to - from + 540) % 360 + 360) % 360 - 180;
}

export interface ViewPoint { x: number; y: number }

export function pointHitsQuad(point: ViewPoint, corners: readonly ViewPoint[], tolerance = 0): boolean {
  let positive = false, negative = false, nearest = Infinity;
  for (let i = 0; i < corners.length; i++) {
    const a = corners[i]!, b = corners[(i + 1) % corners.length]!;
    const dx = b.x - a.x, dy = b.y - a.y;
    const cross = dx * (point.y - a.y) - dy * (point.x - a.x);
    if (cross > 1e-6) positive = true;
    if (cross < -1e-6) negative = true;
    const length = dx * dx + dy * dy;
    const t = length ? Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / length)) : 0;
    nearest = Math.min(nearest, Math.hypot(point.x - a.x - dx * t, point.y - a.y - dy * t));
  }
  return corners.length === 4 && ((!positive !== !negative) || nearest <= tolerance);
}
