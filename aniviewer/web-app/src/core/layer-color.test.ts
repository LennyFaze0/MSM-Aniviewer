import { describe, expect, test } from 'vitest';
import { costumeLayerColor, resolveLayerColor } from './layer-color';
import type { AnimationLayer } from './types';

function layer(): AnimationLayer {
  return {
    name: 'body',
    parent: -1,
    id: 0,
    src: 0,
    anchor_x: 0,
    anchor_y: 0,
    frames: [],
  };
}

describe('per-segment color evaluation', () => {
  test('normalizes native rare costume RGBA records', () => {
    const target = layer();
    Object.assign(target, costumeLayerColor({
      rgba16: { r: 78, g: 227, b: 255, a: 255 },
      rgba: { r: 78, g: 227, b: 255, a: 255 },
    }));
    expect(resolveLayerColor(target, 0, 1)).toEqual([
      78 / 255,
      227 / 255,
      1,
      1,
    ]);
  });

  test('evaluates looping gradient and animated color metadata', () => {
    const gradient = layer();
    Object.assign(gradient, costumeLayerColor({
      gradient: [
        { position: 0, rgba: { r: 255, g: 0, b: 0, a: 255 } },
        { position: 1, rgba: { r: 0, g: 0, b: 255, a: 255 } },
      ],
      period: 2,
    }));
    expect(resolveLayerColor(gradient, 1, 2)).toEqual([0.5, 0, 0.5, 1]);

    const animated = layer();
    Object.assign(animated, costumeLayerColor({
      animation: [
        { time: 0, rgba: { r: 0, g: 255, b: 0, a: 255 } },
        { time: 1, rgba: { r: 255, g: 255, b: 0, a: 255 } },
      ],
      loop: false,
    }));
    expect(resolveLayerColor(animated, 0.5, 1)).toEqual([0.5, 1, 0, 1]);
  });
});
