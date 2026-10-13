import type { AnimationDocument, AnimationLayer } from './types';

function blendInteger(value: unknown): number {
  if (typeof value === 'string' && !/^[+-]?\d+$/.test(value.trim())) return 0;
  if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') return 0;
  const number = Number(value);
  return Number.isFinite(number) ? Math.trunc(number) : 0;
}

/** Match the desktop's _determine_blend_version, including tagged 0/1-only files. */
export function animationBlendVersion(document: AnimationDocument): number {
  const version = document.blend_version;
  const hasModernModes = document.anims.some((track) => (
    track.layers.some((layer) => blendInteger(layer.blend) >= 2)
  ));
  return typeof version === 'number' && Number.isInteger(version) && version >= 2 && hasModernModes
    ? version
    : 1;
}

export function normalizeAnimationBlend(value: unknown, version: number): number {
  const blend = blendInteger(value);
  if (blend < 0 || blend > 7) return 0;
  // Old exports use 1 for additive; retain modern IDs in mixed/unlabelled files.
  return version < 2 && blend === 1 ? 2 : blend;
}

export function layerAnimationBlend(layer: AnimationLayer, version: number): number {
  const blend = normalizeAnimationBlend(layer.blend, version);
  const name = layer.name.trim().toLowerCase();
  const shader = (layer.shader_name ?? '').trim().toLowerCase();
  // Same compatibility rule as desktop _should_promote_light_layer_blend.
  return blend === 0 && name.startsWith('sprite_light') && (!shader || shader === 'anim2d/normal+alpha')
    ? 2
    : blend;
}
