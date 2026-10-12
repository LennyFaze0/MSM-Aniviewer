import { describe, expect, it } from 'vitest';
import { spriteMeshAnchor } from './sprite-geometry';
import type { AtlasSprite } from './types';

describe('sprite mesh geometry', () => {
  it('places a sprite quad at its authored pivot-local mesh origin', () => {
    const sprite: AtlasSprite = {
      name: 'A_adult_wing_05',
      x: 53,
      y: 298,
      width: 87,
      height: 80,
      pivotX: -0.143678,
      pivotY: 1.225,
      offsetX: 0,
      offsetY: 0,
      originalWidth: 87,
      originalHeight: 80,
      rotated: false,
      meshBounds: [12.5, 18, 99.5, 98],
    };

    expect(spriteMeshAnchor(sprite)).toEqual([-12.5 / 87, -18 / 80]);
  });

  it('leaves ordinary and rotated atlas sprites at the regular quad origin', () => {
    const sprite = {
      name: 'body', x: 0, y: 0, width: 20, height: 30,
      pivotX: 0.5, pivotY: 0.5, offsetX: 0, offsetY: 0,
      originalWidth: 20, originalHeight: 30, rotated: false,
    } satisfies AtlasSprite;
    expect(spriteMeshAnchor(sprite)).toEqual([0, 0]);
    expect(spriteMeshAnchor({ ...sprite, rotated: true, meshBounds: [2, 3, 22, 33] })).toEqual([0, 0]);
  });
});
