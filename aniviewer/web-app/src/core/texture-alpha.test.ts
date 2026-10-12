import { describe, expect, test } from 'vitest';
import { classifyTextureAlpha, unpremultiplyRgbaPixels } from './texture-alpha';

describe('texture alpha handling', () => {
  test('recognizes stored premultiplied monster pixels', () => {
    const pixels = new Uint8ClampedArray(Array.from({ length: 16 }, () => [
      128, 64, 16, 128,
    ]).flat());
    expect(classifyTextureAlpha(pixels)).toBe('premultiplied');
  });

  test('recognizes straight-alpha custom image pixels', () => {
    const pixels = new Uint8ClampedArray(Array.from({ length: 16 }, () => [
      255, 128, 32, 128,
    ]).flat());
    expect(classifyTextureAlpha(pixels)).toBe('straight');
  });

  test('does not manufacture an alpha distinction for opaque images', () => {
    expect(classifyTextureAlpha(new Uint8ClampedArray([
      250, 200, 100, 255,
      0, 0, 0, 0,
    ]))).toBe('opaque');
  });

  test('unpremultiplies RGB while preserving alpha and transparent black', () => {
    const pixels = new Uint8ClampedArray([
      128, 64, 16, 128,
      20, 30, 40, 0,
      20, 30, 40, 255,
    ]);
    unpremultiplyRgbaPixels(pixels);
    expect([...pixels]).toEqual([
      255, 128, 32, 128,
      0, 0, 0, 0,
      20, 30, 40, 255,
    ]);
  });
});
