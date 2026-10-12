import type { AtlasSprite } from './types';

export const DOF_CHROMATIC_COLORS = ['blue', 'green', 'orange', 'purple', 'red', 'yellow'] as const;
export type DofChromaticColor = typeof DOF_CHROMATIC_COLORS[number];

export interface DofChromaticGradient {
  color: DofChromaticColor;
  width: number;
  pixels: Uint8ClampedArray;
  source: 'static_data' | 'sampled';
}

const SAMPLED_GRADIENTS: Record<DofChromaticColor, string> = {
  blue: '00042700062b00092f000d3400123c001844001f4d002758002e6300386f00427c004b8a005596005da20167b00a74be137fcc1e8adb2a96e937a1f242aafa52b7ff62c5ff74d6fe87e1fb9beafaabf1f9c0f5facef8fbdffafceffcfefafefeffffff',
  green: '000829010a2a010c2b010e2b02122d03162f031d2f042332062b36083537093f3c0b48400c53420e5c460f664a12764c15835218925619a65719c35520d55c48e66785f179baf686dffa9bedfbabf6fcbdfaffd0fbfee5fdfef7fefefcffffff',
  orange: '0003270403260a06271105231805222206203006203e081f4d091b5d0c166d0d147d11138d140f9d180faa1c0dba2709cb3006d83c03e94b03f55d01fe6d00ff7c00fe8b00fe9800ffa700ffb202fec11cffcf42ffda67fee695fef3befdfbd9feffe7',
  purple: '01042903032b07042f0d033514043d1a044525034f2e035a39046748037253037e60048b71049380059f9005aba306b7be06c2d50bcce716d5f127d4fa3fccfc5cc1fe79b3fe95a7ffada0fec09ffecfa4ffdeafffe7bcfef0cbfff6d8fdfce4fdffe9',
  red: '2800132b00132e001433001539001640001747001850001a5a001c65001d70011f7c02228704239106269d0a28ae122cbf1930d12135e22d3aef373ef93f45ff4f4bff675cfe8271fc9d86fab49bfbc3acf9d1bffbded0fde8e0fff3f0fefcfbffffff',
  yellow: '010327040528090729140a291b0b2a25102a34132d44182f541c30642132762835893037983837a8403ab54b3dc8648e9a34bf1bf4bfad448fee23afee921ffee0bfef300fff601fef817fffc3afffd64fefe91ffffbafdffd9fefee7',
};

interface SpriteRect {
  name: string;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function luminance(red: number, green: number, blue: number): number {
  return clamp(Math.round(0.299 * red + 0.587 * green + 0.114 * blue), 0, 255);
}

function parseStops(encoded: string): Array<[number, number, number]> {
  const stops: Array<[number, number, number]> = [];
  for (let index = 0; index + 5 < encoded.length; index += 6) {
    stops.push([
      Number.parseInt(encoded.slice(index, index + 2), 16),
      Number.parseInt(encoded.slice(index + 2, index + 4), 16),
      Number.parseInt(encoded.slice(index + 4, index + 6), 16),
    ]);
  }
  return stops;
}

export function dofChromaticColor(value: string): DofChromaticColor | undefined {
  const tokens = value.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  return DOF_CHROMATIC_COLORS.find((color) => tokens.includes(color));
}

export function sampledDofChromaticGradient(color: DofChromaticColor): DofChromaticGradient {
  const stops = parseStops(SAMPLED_GRADIENTS[color]);
  const width = 256;
  const pixels = new Uint8ClampedArray(width * 4);
  const positions = stops.map((_, index) => (index === stops.length - 1 ? width - 1 : index * 8));
  let lower = 0;
  for (let x = 0; x < width; x += 1) {
    while (lower + 1 < positions.length - 1 && x > positions[lower + 1]!) lower += 1;
    const upper = Math.min(stops.length - 1, lower + 1);
    const span = positions[upper]! - positions[lower]!;
    const ratio = span > 0 ? (x - positions[lower]!) / span : 0;
    const left = stops[lower]!;
    const right = stops[upper]!;
    const target = x * 4;
    pixels[target] = Math.round(left[0] + (right[0] - left[0]) * ratio);
    pixels[target + 1] = Math.round(left[1] + (right[1] - left[1]) * ratio);
    pixels[target + 2] = Math.round(left[2] + (right[2] - left[2]) * ratio);
    pixels[target + 3] = 255;
  }
  return { color, width, pixels, source: 'sampled' };
}

function spriteRects(sprites: Iterable<AtlasSprite>, width: number, height: number): SpriteRect[] {
  const rects: SpriteRect[] = [];
  const seen = new Set<string>();
  for (const sprite of sprites) {
    const x0 = clamp(Math.round(sprite.x), 0, width);
    const y0 = clamp(Math.round(sprite.y), 0, height);
    const x1 = clamp(Math.round(sprite.x + sprite.width), 0, width);
    const y1 = clamp(Math.round(sprite.y + sprite.height), 0, height);
    const key = `${x0}:${y0}:${x1}:${y1}`;
    if (x1 <= x0 || y1 <= y0 || seen.has(key)) continue;
    seen.add(key);
    rects.push({ name: sprite.name, x0, y0, x1, y1 });
  }
  return rects;
}

export function averageDofSpriteSaturation(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  sprites: Iterable<AtlasSprite>,
): number | undefined {
  let total = 0;
  let spriteCount = 0;
  for (const rect of spriteRects(sprites, width, height)) {
    let spriteTotal = 0;
    let pixelCount = 0;
    for (let y = rect.y0; y < rect.y1; y += 1) {
      for (let x = rect.x0; x < rect.x1; x += 1) {
        const offset = (y * width + x) * 4;
        if (pixels[offset + 3] === 0) continue;
        const red = pixels[offset]!;
        const green = pixels[offset + 1]!;
        const blue = pixels[offset + 2]!;
        spriteTotal += Math.max(red, green, blue) - Math.min(red, green, blue);
        pixelCount += 1;
      }
    }
    if (!pixelCount) continue;
    total += spriteTotal / pixelCount;
    spriteCount += 1;
  }
  return spriteCount ? total / spriteCount : undefined;
}

function directionalAxis(
  pixels: Uint8ClampedArray,
  width: number,
  rect: SpriteRect,
): [centerX: number, centerY: number, axisX: number, axisY: number, minimum: number, maximum: number] | undefined {
  let sumX = 0;
  let sumY = 0;
  let sumXX = 0;
  let sumYY = 0;
  let sumXY = 0;
  let count = 0;
  for (let y = rect.y0; y < rect.y1; y += 1) {
    for (let x = rect.x0; x < rect.x1; x += 1) {
      if (pixels[(y * width + x) * 4 + 3] === 0) continue;
      sumX += x;
      sumY += y;
      sumXX += x * x;
      sumYY += y * y;
      sumXY += x * y;
      count += 1;
    }
  }
  if (count < 2) return undefined;
  const centerX = sumX / count;
  const centerY = sumY / count;
  const covarianceXX = sumXX / count - centerX * centerX;
  const covarianceYY = sumYY / count - centerY * centerY;
  const covarianceXY = sumXY / count - centerX * centerY;
  let axisX: number;
  let axisY: number;
  if (Math.abs(covarianceXY) > 1e-9 || Math.abs(covarianceXX - covarianceYY) > 1e-9) {
    const angle = 0.5 * Math.atan2(2 * covarianceXY, covarianceXX - covarianceYY);
    axisX = Math.cos(angle);
    axisY = Math.sin(angle);
  } else if (rect.y1 - rect.y0 >= rect.x1 - rect.x0) {
    axisX = 0;
    axisY = 1;
  } else {
    axisX = 1;
    axisY = 0;
  }
  if ((Math.abs(axisY) >= Math.abs(axisX) && axisY < 0) || (Math.abs(axisY) < Math.abs(axisX) && axisX < 0)) {
    axisX = -axisX;
    axisY = -axisY;
  }
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  for (let y = rect.y0; y < rect.y1; y += 1) {
    for (let x = rect.x0; x < rect.x1; x += 1) {
      if (pixels[(y * width + x) * 4 + 3] === 0) continue;
      const projection = (x - centerX) * axisX + (y - centerY) * axisY;
      minimum = Math.min(minimum, projection);
      maximum = Math.max(maximum, projection);
    }
  }
  return maximum - minimum > 1e-9
    ? [centerX, centerY, axisX, axisY, minimum, maximum]
    : undefined;
}

export function applyDofChromaticGradient(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  sprites: Iterable<AtlasSprite>,
  gradient: DofChromaticGradient,
): number {
  const rects = spriteRects(sprites, width, height);
  let globalMinimum = 255;
  let globalMaximum = 0;
  let hasOpaquePixel = false;
  for (const rect of rects) {
    for (let y = rect.y0; y < rect.y1; y += 1) {
      for (let x = rect.x0; x < rect.x1; x += 1) {
        const offset = (y * width + x) * 4;
        if (pixels[offset + 3] === 0) continue;
        const value = luminance(pixels[offset]!, pixels[offset + 1]!, pixels[offset + 2]!);
        globalMinimum = Math.min(globalMinimum, value);
        globalMaximum = Math.max(globalMaximum, value);
        hasOpaquePixel = true;
      }
    }
  }
  const globalRange = hasOpaquePixel ? globalMaximum - globalMinimum : 0;

  for (const rect of rects) {
    let localMinimum = 255;
    let localMaximum = 0;
    let localOpaque = false;
    for (let y = rect.y0; y < rect.y1; y += 1) {
      for (let x = rect.x0; x < rect.x1; x += 1) {
        const offset = (y * width + x) * 4;
        if (pixels[offset + 3] === 0) continue;
        const value = luminance(pixels[offset]!, pixels[offset + 1]!, pixels[offset + 2]!);
        localMinimum = Math.min(localMinimum, value);
        localMaximum = Math.max(localMaximum, value);
        localOpaque = true;
      }
    }
    if (!localOpaque) continue;
    const localRange = localMaximum - localMinimum;
    const axis = localRange < 4 ? directionalAxis(pixels, width, rect) : undefined;
    for (let y = rect.y0; y < rect.y1; y += 1) {
      for (let x = rect.x0; x < rect.x1; x += 1) {
        const offset = (y * width + x) * 4;
        const alpha = pixels[offset + 3]!;
        if (alpha === 0) continue;
        const value = luminance(pixels[offset]!, pixels[offset + 1]!, pixels[offset + 2]!);
        let normalized: number;
        if (globalRange >= 4) normalized = (value - globalMinimum) / globalRange;
        else if (localRange >= 4) normalized = (value - localMinimum) / localRange;
        else if (axis) {
          const [centerX, centerY, axisX, axisY, minimum, maximum] = axis;
          normalized = ((x - centerX) * axisX + (y - centerY) * axisY - minimum) / (maximum - minimum);
        } else if (rect.y1 - rect.y0 >= rect.x1 - rect.x0) {
          normalized = (y - rect.y0) / Math.max(1, rect.y1 - rect.y0 - 1);
        } else {
          normalized = (x - rect.x0) / Math.max(1, rect.x1 - rect.x0 - 1);
        }
        const gradientX = Math.round(clamp(normalized, 0, 1) * (gradient.width - 1));
        const gradientOffset = gradientX * 4;
        pixels[offset] = gradient.pixels[gradientOffset]!;
        pixels[offset + 1] = gradient.pixels[gradientOffset + 1]!;
        pixels[offset + 2] = gradient.pixels[gradientOffset + 2]!;
        pixels[offset + 3] = Math.round(alpha * gradient.pixels[gradientOffset + 3]! / 255);
      }
    }
  }
  return rects.length;
}
