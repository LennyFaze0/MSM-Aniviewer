import { describe, expect, test } from 'vitest';
import { applyCostumeDefinition, type CostumeDefinition } from './costume';
import { costumeAlignmentPoint, costumeAtlasChain, costumeAttachmentAlignment, costumeDrawOffset, type CostumeAtlas } from './costume-appearance';
import { calculateWorldStates } from './animation';
import type { AnimationDocument, AnimationLayer, AtlasSprite, CostumeAttachmentInput } from './types';

const layer = (name = 'body', id = 0): AnimationLayer => ({
  name, id, src: 0, parent: -1, anchor_x: 10, anchor_y: 12,
  frames: [{ time: 0, sprite: { immediate: 1, string: 'base' }, pos: { immediate: 0, x: 30, y: 40 } }],
});
const sprite = (name: string, pivotX = .5, pivotY = .5): AtlasSprite => ({
  name, x: 0, y: 0, width: 30, height: 40, originalWidth: 40, originalHeight: 60,
  offsetX: 5, offsetY: 8, pivotX, pivotY, rotated: false,
});
const atlas = (id: number, name: string, sprites = [sprite('coat')]): CostumeAtlas => ({
  sourceId: id, sourceName: `xml_resources/${name}.xml`,
  data: { imagePath: `${name}.png`, width: 128, height: 128, hires: false, sprites: new Map(sprites.map((item) => [item.name.toLowerCase(), item])) },
});
const definition = (): CostumeDefinition => ({
  apply_shader: [], remaps: [], clone_layers: [], set_blend_layers: [], layer_colors: [], ae_anim_layers: [], sheet_remaps: [],
});
const base = (layers = [layer()]): AnimationDocument => ({ rev: 6, sources: [], anims: [{ name: 'test', width: 128, height: 128, layers }] });
const remap = (display_name: string, resource = 'coat', sheet = 'costume.xml') => ({ display_name, resource, sheet, frame_mappings: [] });
const clone = (new_layer: string, source_layer = 'body', reference_layer = 'body', insert_mode = 1) => ({
  name: new_layer, resource: source_layer, sheet: reference_layer, variant_index: insert_mode,
  new_layer, source_layer, reference_layer, insert_mode,
});

describe('costume atlas and draw-only pivot parity', () => {
  test('routes shared names only to the authored sheet, with case/path/extension normalization', () => {
    const sheets = [atlas(3, 'wrong'), atlas(4, 'costume')];
    expect(costumeAtlasChain('XML_RESOURCES\\COSTUME.XML', sheets).map((item) => item.sourceId)).toEqual([4]);
    const costume = definition(); costume.remaps = [remap('body')];
    expect(applyCostumeDefinition(base(), costume, [3, 4], sheets).anims[0]!.layers[0]!.atlas_override_ids).toEqual([4]);
  });
  test('resolves sheet aliases only when the direct sheet is absent', () => {
    const sheets = [atlas(3, 'replacement')], aliases = [{ from: 'costume.xml', to: 'replacement.xml' }];
    expect(costumeAtlasChain('costume.xml', sheets, aliases).map((item) => item.sourceId)).toEqual([3]);
    expect(costumeAtlasChain('costume.xml', [...sheets, atlas(4, 'costume')], aliases).map((item) => item.sourceId)).toEqual([4]);
    expect(costumeAtlasChain('missing.xml', sheets, aliases)).toEqual([]);
  });
  test('keeps the full frame-swap sequence at its primary resource anchor without changing children', () => {
    const original = base([layer(), { ...layer('child', 1), parent: 0 }]);
    const costume = definition(); costume.remaps = [{ ...remap('body', 'lid1'), frame_mappings: [{ from: 'base', to: 'lid1' }, { from: 'other', to: 'lid2' }] }];
    const sheet = atlas(2, 'costume', [sprite('lid1', .2, .7), sprite('lid2')]);
    const output = applyCostumeDefinition(original, costume, [2], [sheet]);
    const body = output.anims[0]!.layers[0]!;
    expect(body.costume_sprite_anchors).toEqual({ lid1: [8, 42], lid2: [8, 42] });
    expect(costumeDrawOffset(body, sheet.data.sprites.get('lid1')!, sheet.data)).toEqual([2, -30]);
    expect(costumeDrawOffset(body, sheet.data.sprites.get('lid2')!, sheet.data)).toEqual([2, -30]);
    expect(calculateWorldStates(output.anims[0]!, 0).get(1)).toEqual(calculateWorldStates(original.anims[0]!, 0).get(1));
    expect(original.anims[0]!.layers[0]!.costume_sprite_anchors).toBeUndefined();
  });
  test('does not impose a sequence anchor when the primary resource uses the default pivot', () => {
    const costume = definition(); costume.remaps = [{ ...remap('body', 'lid2'), frame_mappings: [{ from: 'base', to: 'lid1' }, { from: 'other', to: 'lid2' }] }];
    const sheet = atlas(2, 'costume', [sprite('lid1', .2, .7), sprite('lid2')]);
    expect(applyCostumeDefinition(base(), costume, [2], [sheet]).anims[0]!.layers[0]!.costume_sprite_anchors).toBeUndefined();
  });
  test('uses the active desktop canvas pivot, honoring hires and excluding DOF/baseline layers', () => {
    const body = { ...layer(), costume_pivot_context: true }, part = sprite('coat', .2, .7), data = atlas(2, 'costume').data;
    expect(costumeDrawOffset(body, part, data)).toEqual([2, -30]);
    expect(costumeDrawOffset(body, part, { ...data, hires: true })).toEqual([6, -9]);
    expect(costumeDrawOffset(layer(), part, data)).toEqual([0, 0]);
    expect(costumeDrawOffset(body, part, { ...data, pivotMode: 'dof_rev6_baked' })).toEqual([0, 0]);
    expect(costumeDrawOffset(body, sprite('coat'), data)).toEqual([0, 0]);
  });
});

describe('costume clone and overlay parity', () => {
  test('a single replacement uses the original layer and hides the duplicate', () => {
    const costume = definition(); costume.clone_layers = [clone('body (1)')];
    costume.remaps = [remap('body', 'empty'), remap('body (1)')];
    const result = applyCostumeDefinition(base(), costume, []).anims[0]!.layers;
    expect(result.find((item) => item.name === 'body')).toMatchObject({ visible: true, frames: [{ sprite: { string: 'coat' } }] });
    expect(result.find((item) => item.name === 'body (1)')).toMatchObject({ visible: false, frames: [{ sprite: { string: 'empty' } }] });
  });
  test('multiple authored replacements remain separate overlays', () => {
    const costume = definition(); costume.clone_layers = [clone('hat'), clone('scarf')];
    costume.remaps = [remap('body', 'empty'), remap('hat', 'hat-art'), remap('scarf', 'scarf-art')];
    const result = applyCostumeDefinition(base(), costume, []).anims[0]!.layers;
    expect(result.map((item) => item.frames[0]!.sprite!.string)).toEqual(['hat-art', 'scarf-art', 'empty']);
    expect(result.slice(0, 2).every((item) => item.visible !== false)).toBe(true);
  });
  test('reuses embedded canonical clones and respects negative insertion mode', () => {
    const original = base(); original.anims[0]!.clone_layers = [clone('hat') as never, clone('unused') as never];
    const costume = definition(); costume.clone_layers = [clone('hat', 'body', 'body', -1)]; costume.remaps = [remap('hat')];
    const result = applyCostumeDefinition(original, costume, []).anims[0]!.layers;
    expect(result.map((item) => item.name)).toEqual(['unused', 'body', 'hat']);
    expect(result.find((item) => item.name === 'unused')!.visible).toBe(false);
    expect(result.find((item) => item.name === 'hat')!.visible).toBe(true);
  });
  test('repairs legacy swapped names without modifying the definition', () => {
    const costume = definition(); costume.clone_layers = [clone('body', 'hat')]; costume.remaps = [remap('hat')];
    const before = structuredClone(costume);
    expect(applyCostumeDefinition(base(), costume, []).anims[0]!.layers.map((item) => item.name)).toEqual(['hat', 'body']);
    expect(costume).toEqual(before);
  });
  test('does not activate an unused numbered canonical clone through its source remap', () => {
    const original = base(); original.anims[0]!.clone_layers = [clone('body (1)') as never];
    const costume = definition(); costume.remaps = [remap('body')];
    const result = applyCostumeDefinition(original, costume, []).anims[0]!.layers;
    expect(result.find((item) => item.name === 'body (1)')!.visible).toBe(false);
    expect(result.find((item) => item.name === 'body')!.frames[0]!.sprite?.string).toBe('coat');
  });
  test('shade-derived overlays are opaque and ordered above the body, with the source anchor', () => {
    const shade = { ...layer('body shade', 1), anchor_x: 19, blend: 2, frames: [{ time: 0, sprite: { string: 'base' }, opacity: { immediate: 0, value: 40 } }] };
    const costume = definition(); costume.clone_layers = [clone('apron', 'body shade', 'body shade')]; costume.remaps = [remap('apron')];
    const result = applyCostumeDefinition(base([layer(), shade]), costume, []).anims[0]!.layers;
    expect(result.map((item) => item.name)).toEqual(['apron', 'body', 'body shade']);
    expect(result[0]).toMatchObject({ anchor_x: 19, blend: 0, frames: [{ opacity: { value: 100 } }] });
    costume.set_blend_layers = [{ name: 'apron', blend_value: 7 }];
    expect(applyCostumeDefinition(base([layer(), shade]), costume, []).anims[0]!.layers[0]!.blend).toBe(7);
  });
  test('skips missing references instead of appending an arbitrarily ordered clone', () => {
    const costume = definition(); costume.clone_layers = [clone('hat', 'body', 'missing')];
    expect(applyCostumeDefinition(base(), costume, []).anims[0]!.layers).toHaveLength(1);
  });
});

describe('costume attachment alignment', () => {
  test('selects canvas alignment only for the same sheet and same original canvas', () => {
    const sheet = atlas(2, 'costume', [sprite('coat'), sprite('eyes')]);
    const attachment = {
      targetLayer: 'body', track: base([{ ...layer(), frames: [{ sprite: { string: 'eyes' } }] }]).anims[0]!,
      atlases: [sheet],
    } as CostumeAttachmentInput;
    expect(costumeAttachmentAlignment(attachment, [remap('body')], [sheet])).toBe('costume_canvas');
    expect(costumeAttachmentAlignment({ ...attachment, rootLayer: 'body' }, [remap('body')], [sheet])).toBe('rendered_anchor');
    expect(costumeAttachmentAlignment({ ...attachment, atlases: [atlas(0, 'different', [sprite('eyes')]) as never] }, [remap('body')], [sheet])).toBe('rendered_anchor');
    sheet.data.sprites.get('eyes')!.originalWidth = 50;
    expect(costumeAttachmentAlignment(attachment, [remap('body')], [sheet])).toBe('rendered_anchor');
  });
  test('alignment uses world-transformed draw offsets and trim, not a fixed pixel correction', () => {
    const body = { ...layer(), costume_pivot_context: true };
    const state = { ...calculateWorldStates(base([body]).anims[0]!, 0).get(0)!, m00: 0, m01: -2, m10: 2, m11: 0 };
    const part = sprite('coat', .2, .7), data = atlas(2, 'costume').data;
    const canvas = costumeAlignmentPoint(body, state, part, data, 'costume_canvas');
    expect(canvas).toEqual([state.tx + 60, state.ty + 4]);
    const anchor = costumeAlignmentPoint(body, state, part, data, 'rendered_anchor');
    expect(anchor).toEqual([state.tx + 20, state.ty + 34]);
  });
});
