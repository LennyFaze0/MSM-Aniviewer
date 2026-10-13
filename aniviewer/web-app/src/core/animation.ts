import type {
  AnimationDocument,
  AnimationFrame,
  AnimationLayer,
  AnimationTrack,
  LayerState,
  WorldState,
} from './types';

const SPRITE_SUFFIX = /^(.*?)(\d+)(\.[^./\\]+)?$/;

function finiteNumber(value: unknown, fallback = 0): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function immediate(frame: AnimationFrame, channel: keyof AnimationFrame): number {
  const value = frame[channel];
  if (!value || typeof value !== 'object' || !('immediate' in value)) {
    return -1;
  }
  return finiteNumber(value.immediate, -1);
}

function evaluateNumber(
  frames: AnimationFrame[],
  time: number,
  channel: keyof AnimationFrame,
  read: (frame: AnimationFrame) => number,
  fallback: number,
): number {
  const keys = frames
    .filter((frame) => immediate(frame, channel) !== -1)
    .map((frame) => ({
      time: Math.max(0, finiteNumber(frame.time)),
      value: finiteNumber(read(frame), fallback),
      interpolation: immediate(frame, channel),
    }))
    .sort((left, right) => left.time - right.time);

  if (!keys.length) return fallback;

  let previous = keys[0];
  if (!previous) return fallback;
  if (time < previous.time) return previous.value;

  for (let index = 1; index < keys.length; index += 1) {
    const next = keys[index];
    if (!next) continue;
    if (next.time > time) {
      if (previous.interpolation !== 0) return previous.value;
      const span = next.time - previous.time;
      if (span <= 0) return previous.value;
      const ratio = Math.max(0, Math.min(1, (time - previous.time) / span));
      return previous.value + (next.value - previous.value) * ratio;
    }
    previous = next;
  }

  return previous.value;
}

function interpolateSprite(previous: AnimationFrame, next: AnimationFrame, time: number): string {
  const previousName = previous.sprite?.string ?? '';
  const nextName = next.sprite?.string ?? '';
  const previousMatch = SPRITE_SUFFIX.exec(previousName);
  const nextMatch = SPRITE_SUFFIX.exec(nextName);
  if (
    !previousMatch
    || !nextMatch
    || previousMatch[1] !== nextMatch[1]
    || (previousMatch[3] ?? '').toLowerCase() !== (nextMatch[3] ?? '').toLowerCase()
  ) return previousName;

  const start = Number.parseInt(previousMatch[2] ?? '', 10);
  const end = Number.parseInt(nextMatch[2] ?? '', 10);
  const duration = finiteNumber(next.time) - finiteNumber(previous.time);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start === end || duration <= 0) {
    return previousName;
  }

  const span = end - start;
  const steps = Math.abs(span);
  const ratio = Math.max(0, Math.min(0.9999, (time - finiteNumber(previous.time)) / duration));
  const advance = Math.min(steps - 1, Math.floor(ratio * steps));
  const value = span > 0 ? start + advance : start - advance;
  const width = (previousMatch[2] ?? '').length;
  return `${previousMatch[1]}${String(value).padStart(width, '0')}${previousMatch[3] ?? ''}`;
}

function evaluateSprite(frames: AnimationFrame[], time: number): string {
  const keys = frames
    .filter((frame) => immediate(frame, 'sprite') !== -1)
    .sort((left, right) => finiteNumber(left.time) - finiteNumber(right.time));
  if (!keys.length) return '';

  let previous = keys[0];
  if (!previous) return '';
  if (time < finiteNumber(previous.time)) return previous.sprite?.string ?? '';

  for (let index = 1; index < keys.length; index += 1) {
    const next = keys[index];
    if (!next) continue;
    if (finiteNumber(next.time) > time) {
      return immediate(previous, 'sprite') === 0
        ? interpolateSprite(previous, next, time)
        : previous.sprite?.string ?? '';
    }
    previous = next;
  }
  return previous.sprite?.string ?? '';
}

export function evaluateLayer(layer: AnimationLayer, time: number): LayerState {
  const frames = layer.frames ?? [];
  // Rev 4/5 store static recolors on the layer, while Rev 6 uses RGB keys.
  // Active keys take precedence; inactive placeholder keys must not erase the tint.
  const color = (channel: 'red' | 'green' | 'blue'): number => {
    const fallback = finiteNumber(layer.rgb?.[channel], 255);
    return Math.round(evaluateNumber(
      frames, time, 'rgb', (frame) => finiteNumber(frame.rgb?.[channel], 255), fallback,
    ));
  };
  return {
    posX: evaluateNumber(frames, time, 'pos', (frame) => finiteNumber(frame.pos?.x), 0),
    posY: evaluateNumber(frames, time, 'pos', (frame) => finiteNumber(frame.pos?.y), 0),
    scaleX: evaluateNumber(frames, time, 'scale', (frame) => finiteNumber(frame.scale?.x, 100), 100),
    scaleY: evaluateNumber(frames, time, 'scale', (frame) => finiteNumber(frame.scale?.y, 100), 100),
    rotation: evaluateNumber(frames, time, 'rotation', (frame) => finiteNumber(frame.rotation?.value), 0),
    opacity: evaluateNumber(frames, time, 'opacity', (frame) => finiteNumber(frame.opacity?.value, 100), 100),
    depth: evaluateNumber(frames, time, 'depth', (frame) => finiteNumber(frame.depth?.value), 0),
    spriteName: evaluateSprite(frames, time),
    red: color('red'),
    green: color('green'),
    blue: color('blue'),
    alpha: Math.round(evaluateNumber(frames, time, 'rgb', (frame) => finiteNumber(frame.rgb?.alpha, 255), 255)),
  };
}

export function calculateWorldStates(track: AnimationTrack, time: number): Map<number, WorldState> {
  const layerMap = new Map(track.layers.map((layer) => [layer.id, layer]));
  const states = new Map<number, WorldState>();
  const visiting = new Set<number>();

  const calculate = (layer: AnimationLayer): WorldState => {
    const cached = states.get(layer.id);
    if (cached) return cached;

    const local = evaluateLayer(layer, time);
    const radians = (local.rotation * Math.PI) / 180;
    const cosine = Math.cos(radians);
    const sine = Math.sin(radians);
    const scaleX = local.scaleX / 100;
    const scaleY = local.scaleY / 100;
    const m00 = scaleX * cosine;
    const m01 = -scaleY * sine;
    const m10 = scaleX * sine;
    const m11 = scaleY * cosine;
    const mappedAnchor = local.spriteName
      ? layer.sprite_anchor_map?.[local.spriteName]
        ?? Object.entries(layer.sprite_anchor_map ?? {}).find(
          ([name]) => name.toLowerCase() === local.spriteName.toLowerCase(),
        )?.[1]
      : undefined;
    const anchorX = mappedAnchor?.[0] ?? layer.anchor_x;
    const anchorY = mappedAnchor?.[1] ?? layer.anchor_y;
    const tx = local.posX - (m00 * anchorX + m01 * anchorY);
    const ty = local.posY - (m10 * anchorX + m11 * anchorY);

    let world: WorldState = { ...local, m00, m01, m10, m11, tx, ty };
    const parent = layer.parent >= 0 ? layerMap.get(layer.parent) : undefined;
    if (parent && !visiting.has(parent.id)) {
      visiting.add(layer.id);
      const parentState = calculate(parent);
      visiting.delete(layer.id);
      world = {
        ...local,
        m00: parentState.m00 * m00 + parentState.m01 * m10,
        m01: parentState.m00 * m01 + parentState.m01 * m11,
        m10: parentState.m10 * m00 + parentState.m11 * m10,
        m11: parentState.m10 * m01 + parentState.m11 * m11,
        tx: parentState.m00 * tx + parentState.m01 * ty + parentState.tx,
        ty: parentState.m10 * tx + parentState.m11 * ty + parentState.ty,
      };
    }

    states.set(layer.id, world);
    return world;
  };

  track.layers.forEach(calculate);
  return states;
}

export function animationUsesDepth(track: AnimationTrack): boolean {
  return track.layers.some((layer) => layer.frames.some((frame) => (
    frame.depth && finiteNumber(frame.depth.immediate, -1) !== -1
  )));
}

export function animationRenderOrder(
  track: AnimationTrack,
  states: Map<number, WorldState>,
  usesDepth = animationUsesDepth(track),
): AnimationLayer[] {
  if (!usesDepth) return [...track.layers].reverse();
  return track.layers
    .map((layer, order) => ({ layer, order }))
    .sort((left, right) => (
      (states.get(left.layer.id)?.depth ?? 0) - (states.get(right.layer.id)?.depth ?? 0)
      || left.order - right.order
    ))
    .map(({ layer }) => layer);
}

export function animationDuration(track: AnimationTrack): number {
  let duration = 0;
  for (const layer of track.layers) {
    for (const frame of layer.frames ?? []) {
      duration = Math.max(duration, Math.max(0, finiteNumber(frame.time)));
    }
  }
  return duration;
}

export function isAnimationDocument(value: unknown): value is AnimationDocument {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<AnimationDocument>;
  return (
    Array.isArray(candidate.sources) &&
    Array.isArray(candidate.anims) &&
    candidate.anims.every((animation) => animation && Array.isArray(animation.layers))
  );
}
