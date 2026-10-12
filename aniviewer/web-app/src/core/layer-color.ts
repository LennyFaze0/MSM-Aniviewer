import type {
  AnimationLayer,
  ColorTuple,
  LayerColorAnimator,
  LayerColorGradient,
  LayerColorKeyframe,
  LayerColorStop,
} from './types';

type DataObject = Record<string, any>;

function finite(value: unknown): number | undefined {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function firstFinite(...values: unknown[]): number | undefined {
  for (const value of values) {
    const parsed = finite(value);
    if (parsed !== undefined) return parsed;
  }
  return undefined;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function tuple(value: unknown): ColorTuple | undefined {
  if (!Array.isArray(value) || value.length < 4) return undefined;
  const channels = value.slice(0, 4).map(finite);
  if (channels.some((channel) => channel === undefined)) return undefined;
  return channels.map((channel) => clamp01(channel!)) as ColorTuple;
}

function payloadColor(value: DataObject): ColorTuple {
  const rgba = value.rgba && typeof value.rgba === 'object' ? value.rgba as DataObject : undefined;
  const rgba16 = value.rgba16 && typeof value.rgba16 === 'object' ? value.rgba16 as DataObject : undefined;
  const direct = value.color && typeof value.color === 'object' && !Array.isArray(value.color)
    ? value.color as DataObject
    : value;
  const source = rgba ?? rgba16 ?? direct;
  const scale = rgba ? 255 : rgba16 ? 65535 : Math.max(
    firstFinite(source.r, source.red) ?? 1,
    firstFinite(source.g, source.green) ?? 1,
    firstFinite(source.b, source.blue) ?? 1,
    firstFinite(source.a, source.alpha) ?? 1,
  ) > 1 ? 255 : 1;
  return [
    clamp01((firstFinite(source.r, source.red) ?? scale) / scale),
    clamp01((firstFinite(source.g, source.green) ?? scale) / scale),
    clamp01((firstFinite(source.b, source.blue) ?? scale) / scale),
    clamp01((firstFinite(source.a, source.alpha) ?? scale) / scale),
  ];
}

function colorFrom(value: unknown): ColorTuple | undefined {
  return tuple(value) ?? (value && typeof value === 'object' ? payloadColor(value as DataObject) : undefined);
}

function gradientSource(entry: DataObject): unknown[] | undefined {
  for (const candidate of [entry.gradient, entry.gradient_stops, entry.color_ramp, entry.ramp]) {
    if (Array.isArray(candidate)) return candidate;
    if (candidate && typeof candidate === 'object') {
      const nested = (candidate as DataObject).stops ?? (candidate as DataObject).colors;
      if (Array.isArray(nested)) return nested;
    }
  }
  if ((entry.mode === 'gradient' || entry.mode === 'ramp') && Array.isArray(entry.stops)) return entry.stops;
  return undefined;
}

function normalizeStops(values: unknown[]): LayerColorStop[] {
  const stops = values.flatMap((raw, index): LayerColorStop[] => {
    if (!raw || typeof raw !== 'object') return [];
    const value = raw as DataObject;
    const color = colorFrom(value.color ?? value);
    if (!color) return [];
    return [{
      position: firstFinite(value.position, value.offset, value.t, value.percent),
      time: firstFinite(value.time, value.frame),
      color,
      interpolation: String(value.interpolation ?? value.mode ?? ''),
    }];
  });
  const maxTime = Math.max(0, ...stops.map((stop) => stop.time ?? 0));
  stops.forEach((stop, index) => {
    if (stop.position === undefined) {
      stop.position = stop.time !== undefined && maxTime > 0
        ? clamp01(stop.time / maxTime)
        : index / Math.max(1, stops.length - 1);
    }
  });
  return stops.sort((left, right) => (left.position ?? 0) - (right.position ?? 0));
}

function animationSource(entry: DataObject): { values: unknown[]; source: DataObject } | undefined {
  for (const key of ['animation', 'timeline', 'keyframes', 'frames']) {
    const candidate = entry[key];
    if (Array.isArray(candidate)) return { values: candidate, source: entry };
    if (candidate && typeof candidate === 'object') {
      const source = candidate as DataObject;
      const values = source.keyframes ?? source.frames;
      if (Array.isArray(values)) return { values, source };
    }
  }
  const animated = entry.animated ?? entry.anim;
  if (animated && typeof animated === 'object') {
    const source = animated as DataObject;
    const values = source.keyframes ?? source.frames;
    if (Array.isArray(values)) return { values, source };
  }
  return undefined;
}

export function costumeLayerColor(entry: DataObject): Pick<
  AnimationLayer,
  'color_tint' | 'color_tint_hdr' | 'color_gradient' | 'color_animator' | 'color_metadata'
> {
  const color_tint = payloadColor(entry);
  let color_gradient: LayerColorGradient | undefined;
  const rawStops = gradientSource(entry);
  if (rawStops) {
    const stops = normalizeStops(rawStops);
    if (stops.length >= 2) {
      color_gradient = {
        stops,
        mode: String(entry.gradient_mode ?? entry.mode ?? 'loop').toLowerCase(),
        loop: entry.loop === undefined ? undefined : Boolean(entry.loop),
        period: firstFinite(entry.gradient_period, entry.period, entry.duration),
        offset: firstFinite(entry.gradient_offset, entry.start_time, entry.offset) ?? 0,
        speed: firstFinite(entry.gradient_speed, entry.speed, entry.tempo_multiplier) ?? 1,
        ping_pong: Boolean(entry.ping_pong),
      };
    }
  }

  let color_animator: LayerColorAnimator | undefined;
  const rawAnimation = animationSource(entry);
  if (rawAnimation) {
    const frames = rawAnimation.values.flatMap((raw, index): LayerColorKeyframe[] => {
      if (!raw || typeof raw !== 'object') return [];
      const value = raw as DataObject;
      const color = colorFrom(value.color ?? value);
      if (!color) return [];
      return [{
        time: firstFinite(value.time, value.t, value.frame) ?? index,
        color,
        interpolation: String(value.interpolation ?? value.mode ?? ''),
      }];
    }).sort((left, right) => left.time - right.time);
    if (frames.length >= 2) {
      const start = frames[0]!.time;
      frames.forEach((frame) => { frame.time -= start; });
      color_animator = {
        keyframes: frames,
        duration: firstFinite(entry.animation_duration, rawAnimation.source.duration, entry.duration)
          ?? frames.at(-1)!.time,
        loop: entry.loop === undefined ? true : Boolean(entry.loop),
        mode: String(entry.animation_mode ?? entry.mode ?? 'loop').toLowerCase(),
        offset: firstFinite(entry.start_time, entry.offset) ?? 0,
        speed: firstFinite(entry.animation_speed, entry.speed, entry.tempo_multiplier) ?? 1,
      };
    }
  }

  return {
    color_tint,
    color_tint_hdr: entry.rgba16 ? payloadColor({ rgba16: entry.rgba16 }) : color_tint,
    color_gradient,
    color_animator,
    color_metadata: structuredClone(entry),
  };
}

function lerpColor(left: ColorTuple, right: ColorTuple, ratio: number): ColorTuple {
  return left.map((value, index) => value + (right[index]! - value) * ratio) as ColorTuple;
}

function between<T extends { time: number; color: ColorTuple; interpolation?: string }>(
  values: T[],
  time: number,
): ColorTuple {
  let previous = values[0]!;
  for (let index = 1; index < values.length; index += 1) {
    const next = values[index]!;
    if (next.time > time) {
      const span = next.time - previous.time;
      if (span <= 0) return next.color;
      if ((previous.interpolation || next.interpolation || '').toLowerCase() === 'hold') return previous.color;
      return lerpColor(previous.color, next.color, clamp01((time - previous.time) / span));
    }
    previous = next;
  }
  return previous.color;
}

function evaluateAnimator(animator: LayerColorAnimator, time: number): ColorTuple | undefined {
  if (!animator.keyframes.length) return undefined;
  const duration = Math.max(0.001, animator.duration ?? animator.keyframes.at(-1)?.time ?? 1);
  let localTime = (time - (animator.offset ?? 0)) * (animator.speed ?? 1);
  if (animator.loop !== false) localTime = ((localTime % duration) + duration) % duration;
  else localTime = Math.max(0, Math.min(duration, localTime));
  return between(animator.keyframes, localTime);
}

function evaluateGradient(gradient: LayerColorGradient, time: number, animationDuration: number): ColorTuple | undefined {
  if (!gradient.stops.length) return undefined;
  if (gradient.stops.length === 1) return gradient.stops[0]!.color;
  const period = Math.max(0.001, gradient.period ?? gradient.duration ?? animationDuration ?? 1);
  const localTime = (time - (gradient.offset ?? 0)) * (gradient.speed ?? 1);
  const mode = gradient.mode || (gradient.ping_pong ? 'pingpong' : 'loop');
  let normalized: number;
  if (mode === 'once' || gradient.loop === false) normalized = clamp01(localTime / period);
  else if (mode === 'pingpong') {
    const phase = ((localTime / period) % 2 + 2) % 2;
    normalized = 1 - Math.abs(phase - 1);
  } else normalized = ((localTime / period) % 1 + 1) % 1;
  const values = gradient.stops.map((stop) => ({
    time: stop.position ?? 0,
    color: stop.color,
    interpolation: stop.interpolation,
  }));
  return between(values, normalized);
}

export function resolveLayerColor(layer: AnimationLayer, time: number, duration: number): ColorTuple {
  return (layer.color_animator && evaluateAnimator(layer.color_animator, time))
    ?? (layer.color_gradient && evaluateGradient(layer.color_gradient, time, duration))
    ?? tuple(layer.color_tint)
    ?? [1, 1, 1, 1];
}
