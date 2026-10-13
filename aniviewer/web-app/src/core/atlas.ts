import type { AtlasSprite, TextureAtlasData } from './types';

function numericAttribute(element: Element, name: string, fallback = 0): number {
  const raw = element.getAttribute(name);
  if (raw === null || raw.trim() === '') return fallback;
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

function booleanAttribute(element: Element, name: string): boolean {
  const value = (element.getAttribute(name) ?? '').trim().toLowerCase();
  return value === 'true' || value === '1' || value === 'yes' || value === 'y';
}

function meshBounds(element: Element): AtlasSprite['meshBounds'] {
  const values = (element.querySelector(':scope > vertices')?.textContent ?? '')
    .trim()
    .split(/\s+/)
    .map(Number);
  if (values.length < 6 || values.length % 2 !== 0 || values.some((value) => !Number.isFinite(value))) {
    return undefined;
  }
  const xs: number[] = [];
  const ys: number[] = [];
  for (let index = 0; index < values.length; index += 2) {
    xs.push(values[index]!);
    ys.push(values[index + 1]!);
  }
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

export function parseTextureAtlas(xml: string): TextureAtlasData {
  const document = new DOMParser().parseFromString(xml, 'application/xml');
  const parseError = document.querySelector('parsererror');
  if (parseError) throw new Error('The selected texture atlas XML is malformed.');

  const root = document.documentElement;
  if (!root || root.tagName.toLowerCase() !== 'textureatlas') {
    throw new Error('The selected XML is not a TextureAtlas document.');
  }

  const sprites = new Map<string, AtlasSprite>();
  root.querySelectorAll('sprite').forEach((element) => {
    const name = element.getAttribute('n') ?? element.getAttribute('name') ?? '';
    if (!name) return;
    const width = numericAttribute(element, 'w');
    const height = numericAttribute(element, 'h');
    const rotated = booleanAttribute(element, 'r');
    sprites.set(name.toLowerCase(), {
      name,
      x: numericAttribute(element, 'x'),
      y: numericAttribute(element, 'y'),
      width,
      height,
      pivotX: numericAttribute(element, 'pX', 0.5),
      pivotY: numericAttribute(element, 'pY', 0.5),
      offsetX: numericAttribute(element, 'oX'),
      offsetY: numericAttribute(element, 'oY'),
      originalWidth: numericAttribute(element, 'oW', rotated ? height : width),
      originalHeight: numericAttribute(element, 'oH', rotated ? width : height),
      rotated,
      meshBounds: meshBounds(element),
    });
  });

  return {
    imagePath: root.getAttribute('imagePath') ?? root.getAttribute('imagepath') ?? '',
    pivotMode: (root.getAttribute('pivotMode') || root.getAttribute('pivot_mode') || '').trim().toLowerCase() || undefined,
    width: numericAttribute(root, 'width'),
    height: numericAttribute(root, 'height'),
    hires: booleanAttribute(root, 'hires'),
    sprites,
  };
}
