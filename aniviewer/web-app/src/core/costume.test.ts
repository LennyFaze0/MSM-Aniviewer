import { describe, expect, test } from 'vitest';
import type { AnimationDocument } from './types';
import { applyCostumeDefinition, type CostumeDefinition } from './costume';

function baseDocument(): AnimationDocument {
  return {
    rev: 6,
    sources: [{ src: 'base.xml', id: 0 }],
    anims: [{
      name: 'Idle',
      width: 128,
      height: 128,
      layers: [{
        name: 'head',
        parent: -1,
        id: 0,
        src: 0,
        anchor_x: 0,
        anchor_y: 0,
        frames: [{
          time: 0,
          sprite: { immediate: 1, string: 'A_head.png' },
          rgb: { immediate: 0, red: 80, green: 100, blue: 120 },
        }],
      }],
    }],
  };
}

function costume(): CostumeDefinition {
  return {
    apply_shader: [],
    remaps: [{
      display_name: 'hat',
      resource: 'A_partyhat.png',
      sheet: 'monster_A_costume_01_sheet.xml',
      frame_mappings: [],
    }],
    clone_layers: [{
      name: 'hat',
      resource: 'head',
      sheet: 'head',
      variant_index: 1,
      new_layer: 'hat',
      source_layer: 'head',
      reference_layer: 'head',
      insert_mode: 1,
    }],
    set_blend_layers: [],
    layer_colors: [],
    ae_anim_layers: [],
    sheet_remaps: [],
  };
}

describe('costume application', () => {
  test('uses a remap resource for cloned layers without frame mappings', () => {
    const result = applyCostumeDefinition(baseDocument(), costume(), [4]);
    const layers = result.anims[0]!.layers;

    expect(layers.map((layer) => layer.name)).toEqual(['hat', 'head']);
    expect(layers[0]!.frames[0]!.sprite?.string).toBe('A_partyhat.png');
    expect(layers[0]!.atlas_override_ids).toEqual([4]);
    expect(layers[0]!.frames[0]!.rgb).toMatchObject({ red: 255, green: 255, blue: 255 });
    expect(layers[1]!.frames[0]!.sprite?.string).toBe('A_head.png');
  });

  test('prefers exact frame mappings over the resource fallback', () => {
    const definition = costume();
    definition.remaps[0]!.frame_mappings.push({ from: 'A_head.png', to: 'A_hat_blink.png' });
    const result = applyCostumeDefinition(baseDocument(), definition, [7]);

    expect(result.anims[0]!.layers[0]!.frames[0]!.sprite?.string).toBe('A_hat_blink.png');
  });

  test('attaches costume shader resources to their target layers', () => {
    const definition = costume();
    definition.apply_shader.push({ node: 'head.png', resource: 'ShaderCostumeA3' });
    const result = applyCostumeDefinition(baseDocument(), definition, []);

    expect(result.anims[0]!.layers.find((layer) => layer.name === 'head')?.shader_name)
      .toBe('ShaderCostumeA3');
  });
});
