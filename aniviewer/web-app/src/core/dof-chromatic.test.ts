import { describe, expect, test } from 'vitest';
import {
  applyDofChromaticGradient,
  averageDofSpriteSaturation,
  dofChromaticColor,
  sampledDofChromaticGradient,
} from './dof-chromatic';
import type { AtlasSprite } from './types';

function sprite(name: string, x: number, width: number): AtlasSprite {
  return {
    name,
    x,
    y: 0,
    width,
    height: 1,
    pivotX: 0.5,
    pivotY: 0.5,
    offsetX: 0,
    offsetY: 0,
    originalWidth: width,
    originalHeight: 1,
    rotated: false,
  };
}

describe('DOF chromatic gradients', () => {
  test('identifies only authored chromatic color tokens', () => {
    expect(dofChromaticColor('staticinfo_a_tweedle_chromatic_blue.unity3d')).toBe('blue');
    expect(dofChromaticColor('staticinfo_b_potbelly.unity3d')).toBeUndefined();
  });

  test('maps grayscale luminance through the sampled desktop LUT', () => {
    const pixels = new Uint8ClampedArray([
      0, 0, 0, 255,
      128, 128, 128, 200,
      255, 255, 255, 128,
      250, 30, 20, 255,
    ]);
    const gradient = sampledDofChromaticGradient('blue');
    expect(applyDofChromaticGradient(pixels, 4, 1, [sprite('gradient', 0, 3)], gradient)).toBe(1);
    expect([...pixels.slice(0, 4)]).toEqual([...gradient.pixels.slice(0, 3), 255]);
    expect([...pixels.slice(8, 12)]).toEqual([...gradient.pixels.slice(-4, -1), 128]);
    expect([...pixels.slice(12, 16)]).toEqual([250, 30, 20, 255]);
  });

  test('distinguishes grayscale shader sprites from authored color sprites', () => {
    const pixels = new Uint8ClampedArray([
      20, 20, 20, 255,
      220, 220, 220, 255,
      10, 100, 220, 255,
      240, 30, 80, 255,
    ]);
    expect(averageDofSpriteSaturation(pixels, 4, 1, [sprite('gray', 0, 2)])).toBe(0);
    expect(averageDofSpriteSaturation(pixels, 4, 1, [sprite('color', 2, 2)])).toBeGreaterThan(100);
  });
});
