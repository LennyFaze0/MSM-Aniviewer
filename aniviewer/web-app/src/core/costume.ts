import { costumeLayerColor } from './layer-color';
import { animationBlendVersion } from './animation-blend';
import { applyCostumeClones } from './costume-clones';
import { applyCostumeAtlasContext, type CostumeAtlas } from './costume-appearance';
import type { AnimationDocument, AnimationLayer } from './types';

export interface CostumeRemap {
  display_name: string;
  resource: string;
  sheet: string;
  frame_mappings: Array<{ from: string; to: string }>;
}

export interface CostumeDefinition {
  apply_shader: Array<{ node: string; resource: string }>;
  remaps: CostumeRemap[];
  clone_layers: Array<{
    name: string;
    resource: string;
    sheet: string;
    variant_index: number;
    new_layer: string;
    source_layer: string;
    reference_layer: string;
    insert_mode: number;
  }>;
  set_blend_layers: Array<{ name: string; blend_value: number }>;
  layer_colors: Array<{
    layer: string;
    rgba16: { r: number; g: number; b: number; a: number };
    rgba: { r: number; g: number; b: number; a: number };
    hex: string;
    [key: string]: unknown;
  }>;
  ae_anim_layers: Array<Record<string, unknown>>;
  sheet_remaps: Array<{ from: string; to: string }>;
}

class CostumeReader {
  private offset = 0;

  constructor(private readonly view: DataView) {}

  get position(): number {
    return this.offset;
  }

  get remaining(): number {
    return this.view.byteLength - this.offset;
  }

  seek(offset: number): void {
    if (offset < 0 || offset > this.view.byteLength) throw new Error('Invalid costume BIN offset.');
    this.offset = offset;
  }

  uint32(): number {
    this.require(4);
    const value = this.view.getUint32(this.offset, true);
    this.offset += 4;
    return value;
  }

  uint16(): number {
    this.require(2);
    const value = this.view.getUint16(this.offset, true);
    this.offset += 2;
    return value;
  }

  float32(): number {
    this.require(4);
    const value = this.view.getFloat32(this.offset, true);
    this.offset += 4;
    return value;
  }

  string(): string {
    const length = this.uint32();
    this.require(length);
    const bytes = new Uint8Array(this.view.buffer, this.view.byteOffset + this.offset, length);
    this.offset += Math.ceil(length / 4) * 4;
    this.require(0);
    let end = bytes.length;
    while (end && bytes[end - 1] === 0) end -= 1;
    return new TextDecoder().decode(bytes.subarray(0, end));
  }

  private require(length: number): void {
    if (this.offset + length > this.view.byteLength) {
      throw new Error('Unexpected end of costume BIN.');
    }
  }
}

function signed32(value: number): number {
  return value > 0x7fffffff ? value - 0x100000000 : value;
}

function parseAttachments(reader: CostumeReader, count: number): Array<Record<string, unknown>> {
  const attachments: Array<Record<string, unknown>> = [];
  for (let index = 0; index < count; index += 1) {
    const attachTo = reader.string();
    const resource = reader.string();
    const animation = reader.string();
    const raw = reader.float32();
    const tempoValue = Math.abs(raw) > 0 && Math.abs(raw) <= 0.0025;
    attachments.push({
      attach_to: attachTo,
      resource,
      animation,
      time_offset: tempoValue ? 0 : raw,
      time_scale: tempoValue ? 0 : raw,
      tempo_multiplier: tempoValue ? Math.max(0.1, Math.abs(raw) * 100) : 1,
      loop: true,
      raw_time_value: raw,
    });
  }
  return attachments;
}

function parseSheetRemaps(reader: CostumeReader, count = reader.uint32()): Array<{ from: string; to: string }> {
  return Array.from({ length: count }, () => ({ from: reader.string(), to: reader.string() }));
}

function parseTail(
  reader: CostumeReader,
): { attachments: Array<Record<string, unknown>>; sheets: Array<{ from: string; to: string }> } {
  const checkpoint = reader.position;
  try {
    const attachments = parseAttachments(reader, reader.uint32());
    const sheets = reader.remaining ? parseSheetRemaps(reader) : [];
    return { attachments, sheets };
  } catch {
    reader.seek(checkpoint);
    const sheets = parseSheetRemaps(reader);
    const attachments = reader.remaining ? parseAttachments(reader, reader.uint32()) : [];
    return { attachments, sheets };
  }
}

export function isCostumeDefinition(value: unknown): value is CostumeDefinition {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<CostumeDefinition>;
  return Array.isArray(candidate.remaps) && Array.isArray(candidate.clone_layers);
}

export function parseCostumeBin(buffer: ArrayBuffer): CostumeDefinition {
  const reader = new CostumeReader(new DataView(buffer));
  const apply_shader = Array.from({ length: reader.uint32() }, () => ({
    node: reader.string(),
    resource: reader.string(),
  }));
  const remaps = Array.from({ length: reader.uint32() }, () => {
    const frame_mappings = Array.from({ length: reader.uint32() }, () => ({
      from: reader.string(),
      to: reader.string(),
    }));
    return {
      display_name: reader.string(),
      resource: reader.string(),
      sheet: reader.string(),
      frame_mappings,
    };
  });
  const clone_layers = Array.from({ length: reader.uint32() }, () => {
    const source = reader.string();
    const name = reader.string();
    const reference = reader.string();
    const variant = reader.uint32();
    return {
      name,
      resource: source,
      sheet: reference,
      variant_index: variant,
      new_layer: name,
      source_layer: source,
      reference_layer: reference,
      insert_mode: signed32(variant),
    };
  });
  const set_blend_layers = Array.from({ length: reader.uint32() }, () => ({
    name: reader.string(),
    blend_value: reader.uint32(),
  }));

  const tailOffset = reader.position;
  const errors: unknown[] = [];
  for (const withColors of [true, false]) {
    reader.seek(tailOffset);
    try {
      const layer_colors = withColors
        ? Array.from({ length: reader.uint32() }, () => {
            const layer = reader.string();
            const rgba16 = {
              r: reader.uint16(),
              g: reader.uint16(),
              b: reader.uint16(),
              a: reader.uint16(),
            };
            const rgba = {
              r: Math.min(255, rgba16.r),
              g: Math.min(255, rgba16.g),
              b: Math.min(255, rgba16.b),
              a: Math.min(255, rgba16.a),
            };
            return {
              layer,
              rgba16,
              rgba,
              hex: `#${Object.values(rgba).map((part) => part.toString(16).padStart(2, '0')).join('').toUpperCase()}`,
            };
          })
        : [];
      const tail = parseTail(reader);
      if (reader.remaining) throw new Error('Costume BIN has unparsed trailing bytes.');
      return {
        apply_shader,
        remaps,
        clone_layers,
        set_blend_layers,
        layer_colors,
        ae_anim_layers: tail.attachments,
        sheet_remaps: tail.sheets,
      };
    } catch (error) {
      errors.push(error);
    }
  }
  throw errors.at(-1) ?? new Error('Unable to parse costume BIN.');
}

function layerLookup(layers: AnimationLayer[]): Map<string, AnimationLayer> {
  return new Map(layers.map((layer) => [layer.name.toLowerCase(), layer]));
}

function matchingLayer(lookup: Map<string, AnimationLayer>, name: string): AnimationLayer | undefined {
  const normalized = name.trim().toLowerCase();
  return lookup.get(normalized)
    ?? lookup.get(normalized.replace(/\.[^.]+$/, ''))
    ?? lookup.get(normalized.replace(/\s+\d+$/, ''));
}

export function applyCostumeDefinition(
  base: AnimationDocument,
  costume: CostumeDefinition,
  atlasIds: number[],
  atlases?: CostumeAtlas[],
): AnimationDocument {
  const document = structuredClone(base);
  document.render_blend_version = base.render_blend_version ?? animationBlendVersion(base);
  for (const track of document.anims) {
    const clones = applyCostumeClones(track, costume);
    const lookup = layerLookup(track.layers);

    for (const layer of track.layers) {
      const remap = clones.remaps.get(layer.id);
      if (!remap) continue;
      const exact = new Map((remap.frame_mappings ?? []).map((mapping) => [mapping.from, mapping.to]));
      const folded = new Map((remap.frame_mappings ?? []).map((mapping) => [mapping.from.toLowerCase(), mapping.to]));
      const hasExistingColor = Boolean(layer.color_tint || layer.color_gradient || layer.color_animator);
      for (const frame of layer.frames) {
        const sprite = frame.sprite?.string ?? '';
        const replacement = exact.get(sprite) ?? folded.get(sprite.toLowerCase()) ?? remap.resource;
        if (!replacement) continue;
        if (frame.sprite) frame.sprite.string = replacement;
        else frame.sprite = { immediate: -1, string: replacement };
        if (!hasExistingColor) {
          // Replacement artwork is already colored, including on static-tint BIN revisions.
          layer.rgb = { red: 255, green: 255, blue: 255 };
          frame.rgb = {
            ...frame.rgb,
            immediate: Math.max(0, frame.rgb?.immediate ?? 0),
            red: 255,
            green: 255,
            blue: 255,
          };
        }
        if (clones.opaque.has(layer.id)) frame.opacity = { immediate: Math.max(0, frame.opacity?.immediate ?? 0), value: 100 };
      }
      if (atlases) applyCostumeAtlasContext(layer, remap, atlases, costume.sheet_remaps ?? []);
      else if (atlasIds.length) layer.atlas_override_ids = [...atlasIds];
      if (remap.sheet.toLowerCase().includes('costume') && (clones.opaque.has(layer.id) || clones.overlay.has(layer.id))) layer.blend = 0;
    }

    for (const override of costume.set_blend_layers ?? []) {
      const layer = matchingLayer(lookup, override.name);
      if (layer) layer.blend = override.blend_value;
    }
    for (const shader of costume.apply_shader ?? []) {
      const layer = shader.node ? matchingLayer(lookup, shader.node) : undefined;
      if (layer && shader.resource) layer.shader_name = shader.resource;
    }
    for (const color of costume.layer_colors ?? []) {
      const layer = matchingLayer(lookup, color.layer);
      if (!layer) continue;
      Object.assign(layer, costumeLayerColor(color));
    }
  }
  return document;
}
