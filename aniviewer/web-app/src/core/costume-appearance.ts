import type { CostumeDefinition, CostumeRemap } from './costume';
import type { AnimationLayer, AtlasSprite, CostumeAttachmentInput, LoadedAtlasInput, TextureAtlasData, WorldState } from './types';

export type CostumeAtlas = Pick<LoadedAtlasInput, 'sourceId' | 'sourceName' | 'data'>;

function sheetKey(name: string): string {
  return name.replace(/\\/g, '/').split('/').at(-1)!.replace(/\.[^.]+$/, '').toLowerCase().replace(/_sheet$/, '');
}

export function costumeAtlasChain(
  sheet: string,
  atlases: CostumeAtlas[],
  aliases: CostumeDefinition['sheet_remaps'] = [],
): CostumeAtlas[] {
  const matching = (name: string) => atlases.filter((atlas) => (
    sheetKey(atlas.sourceName || atlas.data.imagePath) === sheetKey(name)
  ));
  const direct = matching(sheet);
  if (direct.length) return direct;
  return aliases.filter((alias) => sheetKey(alias.from) === sheetKey(sheet))
    .flatMap((alias) => matching(alias.to));
}

function nonDefaultPivot(sprite: AtlasSprite): boolean {
  return Math.abs(sprite.pivotX - .5) > .001 || Math.abs(sprite.pivotY - .5) > .001;
}

export function applyCostumeAtlasContext(
  layer: AnimationLayer,
  remap: CostumeRemap,
  atlases: CostumeAtlas[],
  aliases: CostumeDefinition['sheet_remaps'],
): void {
  const valid = Boolean(remap.sheet && !/^empty(?:\.xml)?$/i.test(remap.sheet)
    && remap.resource && remap.resource.toLowerCase() !== 'empty');
  layer.costume_pivot_context = valid;
  if (!valid) return;
  const chain = costumeAtlasChain(remap.sheet, atlases, aliases);
  layer.atlas_override_ids = chain.map((atlas) => atlas.sourceId);
  const targets = [...new Set((remap.frame_mappings ?? []).map((entry) => entry.to).filter(Boolean))];
  const anchors = new Map<string, { anchor: [number, number]; nonDefault: boolean }>();
  for (const name of targets) {
    const atlas = chain.find((item) => item.data.sprites.has(name.toLowerCase()));
    const sprite = atlas?.data.sprites.get(name.toLowerCase());
    if (!atlas || !sprite || atlas.data.pivotMode?.startsWith('dof')) continue;
    const scale = atlas.data.hires ? .5 : 1;
    anchors.set(name, {
      anchor: [
        Number(((sprite.originalWidth || sprite.width) * scale * sprite.pivotX).toFixed(6)),
        Number(((sprite.originalHeight || sprite.height) * scale * sprite.pivotY).toFixed(6)),
      ],
      nonDefault: nonDefaultPivot(sprite),
    });
  }
  if (new Set([...anchors.values()].map((entry) => entry.nonDefault)).size < 2) return;
  const primary = anchors.get(remap.resource);
  if (primary && !primary.nonDefault) return;
  const counts = new Map<string, { anchor: [number, number]; count: number }>();
  for (const { anchor } of anchors.values()) {
    const key = anchor.join(',');
    counts.set(key, { anchor, count: (counts.get(key)?.count ?? 0) + 1 });
  }
  const dominant = [...counts.values()].sort((a, b) => (
    b.count - a.count || b.anchor[0] - a.anchor[0] || b.anchor[1] - a.anchor[1]
  ))[0]!;
  const anchor = primary?.anchor ?? dominant.anchor;
  layer.costume_sprite_anchors = Object.fromEntries([...anchors.keys()].map((name) => [name.toLowerCase(), [...anchor]]));
}

/** Desktop sprite_anchor_offset, in logical pixels, applied to drawing only. */
export function costumeDrawOffset(
  layer: AnimationLayer,
  sprite: AtlasSprite,
  atlas: TextureAtlasData,
): [number, number] {
  const mapped = layer.costume_sprite_anchors?.[sprite.name.toLowerCase()];
  if (mapped) return [layer.anchor_x - mapped[0], layer.anchor_y - mapped[1]];
  if (layer.costume_sprite_anchors) return [0, 0];
  if (!layer.costume_pivot_context || !nonDefaultPivot(sprite)
    || atlas.pivotMode === 'dof' || atlas.pivotMode === 'dof_rev6_baked') return [0, 0];
  const scale = atlas.hires ? .5 : 1;
  // The active desktop configuration disables the legacy subtract-original-anchor formula.
  return [
    layer.anchor_x - (sprite.originalWidth || sprite.width) * scale * sprite.pivotX,
    layer.anchor_y - (sprite.originalHeight || sprite.height) * scale * sprite.pivotY,
  ];
}

export function costumeAttachmentAlignment(
  attachment: CostumeAttachmentInput,
  remaps: CostumeDefinition['remaps'],
  atlases: CostumeAtlas[],
): CostumeAttachmentInput['alignmentMode'] {
  if (attachment.rootLayer) return 'rendered_anchor';
  const remap = remaps.find((entry) => entry.display_name.trim().toLowerCase() === attachment.targetLayer.trim().toLowerCase());
  if (!remap || !remap.resource || remap.resource.toLowerCase() === 'empty') return 'rendered_anchor';
  const targetAtlas = costumeAtlasChain(remap.sheet, atlases).find((atlas) => atlas.data.sprites.has(remap.resource.toLowerCase()));
  const target = targetAtlas?.data.sprites.get(remap.resource.toLowerCase());
  const roots = attachment.track.layers.filter((layer) => layer.parent < 0);
  const rootSprite = (roots.length ? roots : attachment.track.layers)
    .flatMap((layer) => layer.frames).find((frame) => frame.sprite?.string)?.sprite?.string;
  const rootAtlas = rootSprite && attachment.atlases.find((atlas) => atlas.data.sprites.has(rootSprite.toLowerCase()));
  const root = rootAtlas && rootAtlas.data.sprites.get(rootSprite!.toLowerCase());
  if (!target || !root || !targetAtlas || !rootAtlas) return 'rendered_anchor';
  if (sheetKey(targetAtlas.sourceName || targetAtlas.data.imagePath) !== sheetKey(rootAtlas.sourceName || rootAtlas.data.imagePath)) return 'rendered_anchor';
  const tw = target.originalWidth || target.width, th = target.originalHeight || target.height;
  const rw = root.originalWidth || root.width, rh = root.originalHeight || root.height;
  return tw > 0 && th > 0 && Math.abs(tw - rw) <= .001 && Math.abs(th - rh) <= .001
    ? 'costume_canvas' : 'rendered_anchor';
}

export function costumeAlignmentPoint(
  layer: AnimationLayer,
  state: WorldState,
  sprite: AtlasSprite | undefined,
  atlas: TextureAtlasData | undefined,
  mode: CostumeAttachmentInput['alignmentMode'],
): [number, number] {
  const offset = sprite && atlas ? costumeDrawOffset(layer, sprite, atlas) : [0, 0];
  let x = offset[0]!, y = offset[1]!;
  if (mode !== 'costume_canvas') {
    const mapped = layer.sprite_anchor_map?.[state.spriteName];
    x += mapped?.[0] ?? layer.anchor_x;
    y += mapped?.[1] ?? layer.anchor_y;
    if (sprite && atlas && !sprite.meshBounds && atlas.pivotMode !== 'dof') {
      x += sprite.offsetX * (atlas.hires ? .5 : 1);
      y += sprite.offsetY * (atlas.hires ? .5 : 1);
    }
  }
  return [state.tx + state.m00 * x + state.m01 * y, state.ty + state.m10 * x + state.m11 * y];
}
