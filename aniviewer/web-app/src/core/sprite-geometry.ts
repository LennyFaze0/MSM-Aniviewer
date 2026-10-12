import type { AtlasSprite } from './types';

export function spriteMeshAnchor(
  sprite: AtlasSprite,
): [x: number, y: number] {
  const bounds = sprite.meshBounds;
  if (!bounds || sprite.rotated) return [0, 0];
  const originalWidth = Math.max(sprite.width, sprite.originalWidth);
  const originalHeight = Math.max(sprite.height, sprite.originalHeight);
  return [
    originalWidth > 0 ? (sprite.offsetX - bounds[0]) / originalWidth : 0,
    originalHeight > 0 ? (sprite.offsetY - bounds[1]) / originalHeight : 0,
  ];
}
