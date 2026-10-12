import type { AssetManager, ObjectInfo } from 'unityfs-js';
import {
  applyDofChromaticGradient,
  averageDofSpriteSaturation,
  dofChromaticColor,
  sampledDofChromaticGradient,
  type DofChromaticColor,
  type DofChromaticGradient,
} from './dof-chromatic';
import { textureImageFromRgbaPixels } from './texture-alpha';
import type {
  AnimationDocument,
  AnimationFrame,
  AnimationLayer,
  AnimationTrack,
  AtlasSprite,
  AudioClipInput,
  ColorTuple,
  DofParticleEmitter,
  DofParticlePrefabInput,
  DecodedTextureImage,
  LoadedAtlasInput,
} from './types';

type DataObject = Record<string, any>;

interface SpriteBinding {
  name: string;
  sourceId: number;
  textureInfo: ObjectInfo;
  alphaInfo?: ObjectInfo;
  atlasSprite: Omit<AtlasSprite, 'y'> & { unityY: number };
  anchor: [number, number];
}

interface AtlasBuilder {
  sourceId: number;
  textureInfo: ObjectInfo;
  alphaInfo?: ObjectInfo;
  sprites: Map<string, SpriteBinding>;
}

interface DecodedAtlas {
  sourceId: number;
  pixels: Uint8ClampedArray;
  width: number;
  height: number;
}

interface DofAnimationContext {
  info: ObjectInfo;
  structure: DataObject;
  imageNames: string[];
  bindings: Map<string, SpriteBinding>;
}

type DofShaderKind = 'normal' | 'gradient' | 'additive';

type DofKeys = Array<{ time: number; value: number; keytype: number }>;

interface DofChannels {
  posX: DofKeys;
  posY: DofKeys;
  depth: DofKeys;
  scaleX: DofKeys;
  scaleY: DofKeys;
  rotation: DofKeys;
  red: DofKeys;
  green: DofKeys;
  blue: DofKeys;
  opacity: DofKeys;
  sprite: DofKeys;
  flipX: DofKeys;
}

export interface DofBundleResult {
  document?: AnimationDocument;
  atlases: LoadedAtlasInput[];
  audio: AudioClipInput[];
  unityVersion?: string;
}

interface PvrtcDecoderModule {
  decode_pvrtc(
    data: Uint8Array,
    width: number,
    height: number,
    is2bpp: boolean,
  ): (ArrayLike<number> & { delete?: () => void }) | null;
  decode_astc(
    data: Uint8Array,
    width: number,
    height: number,
    blockWidth: number,
    blockHeight: number,
  ): (ArrayLike<number> & { delete?: () => void }) | null;
}

let pvrtcDecoderReady: Promise<PvrtcDecoderModule> | undefined;

function pvrtcDecoder(): Promise<PvrtcDecoderModule> {
  if (!pvrtcDecoderReady) {
    const baseUrl = import.meta.env.BASE_URL;
    // The Emscripten loader is part of the source graph so Vite can serve it in
    // development; its larger WASM payload remains a lazy static asset.
    // @ts-expect-error The vendored Emscripten module has no declaration file.
    pvrtcDecoderReady = import('../vendor/texture2ddecoder.js')
      .then(async (module) => {
        const createModule = module.default as (options: {
          locateFile: (path: string) => string;
        }) => Promise<PvrtcDecoderModule>;
        return createModule({ locateFile: (path) => `${baseUrl}wasm/${path}` });
      });
  }
  return pvrtcDecoderReady;
}

function numberValue(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function vector(value: DataObject | undefined, fallbackX = 0, fallbackY = 0): { x: number; y: number } {
  return {
    x: numberValue(value?.x, fallbackX),
    y: numberValue(value?.y, fallbackY),
  };
}

function pathId(pointer: DataObject | undefined): bigint | undefined {
  const value = pointer?.pathID ?? pointer?.m_PathID ?? pointer?.pathId ?? pointer?.m_PathId;
  if (value === undefined || value === null) return undefined;
  try {
    const parsed = BigInt(value);
    return parsed === 0n ? undefined : parsed;
  } catch {
    return undefined;
  }
}

function resolvePointer(manager: AssetManager, pointer: DataObject | undefined): ObjectInfo | undefined {
  const id = pathId(pointer);
  return id === undefined ? undefined : manager.getObjectInfoByPathId(id) ?? undefined;
}

function objectFields(info: ObjectInfo): DataObject {
  const object = info.object as DataObject;
  const fields = object?.fields as DataObject | undefined;
  if (fields && typeof fields === 'object') return fields;
  const parsed = info.assetFile?.getObjectUsingTreeJSON(info);
  return parsed && typeof parsed === 'object' ? parsed as DataObject : {};
}

function typeTreeFields(info: ObjectInfo): DataObject {
  const assetFile = info.assetFile;
  if (!assetFile) return {};

  // Unity 2022 sprites are newer than unityfs-js's handwritten Sprite parser.
  // Seeding its cache lets the generic type-tree reader handle those objects.
  const hadCachedObject = Object.prototype.hasOwnProperty.call(info, 'cachedObject');
  const cachedObject = info.cachedObject;
  info.cachedObject = {};
  try {
    const parsed = assetFile.getObjectUsingTreeJSON(info);
    return parsed && typeof parsed === 'object' ? parsed as DataObject : {};
  } finally {
    if (hadCachedObject) info.cachedObject = cachedObject;
    else delete info.cachedObject;
  }
}

function objectName(info: ObjectInfo): string {
  try {
    return String(info.name || (info.object as DataObject)?.name || '');
  } catch {
    return '';
  }
}

function dofStructure(info: ObjectInfo): DataObject | undefined {
  try {
    const fields = objectFields(info);
    const structure = fields.m_Structure ?? fields.Structure ?? fields;
    if (!Array.isArray(structure?.Nodes) || !Array.isArray(structure?.Images)) return undefined;
    return structure;
  } catch {
    return undefined;
  }
}

export function normalizeDofKeyframes(values: unknown): DofKeys {
  if (!Array.isArray(values)) return [];
  const byTime = new Map<number, DofKeys[number]>();
  values
    .map((entry) => ({
      time: numberValue(entry?.time),
      value: numberValue(entry?.value),
      keytype: Math.trunc(numberValue(entry?.keytype)),
    }))
    .filter((entry) => Number.isFinite(entry.time) && Number.isFinite(entry.value))
    .forEach((entry) => byTime.set(entry.time, entry));
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

function keyframes(channel: DataObject | undefined): DofKeys {
  return normalizeDofKeyframes(channel?.Values ?? channel?.values ?? []);
}

function immediate(keytype: number): number {
  if (keytype === 1) return 0;
  if (keytype === 0) return 1;
  return keytype;
}

function valueAt(
  keys: Array<{ time: number; value: number; keytype: number }>,
  time: number,
  fallback: number,
): number {
  if (!keys.length) return fallback;
  let previous = keys[0]!;
  if (time <= previous.time) return previous.value;
  for (let index = 1; index < keys.length; index += 1) {
    const next = keys[index]!;
    if (next.time > time) {
      if (immediate(previous.keytype) !== 0) return previous.value;
      const span = next.time - previous.time;
      const ratio = span > 0 ? (time - previous.time) / span : 0;
      return previous.value + (next.value - previous.value) * ratio;
    }
    previous = next;
  }
  return previous.value;
}

function keyAt(
  keys: Array<{ time: number; value: number; keytype: number }>,
  time: number,
): { time: number; value: number; keytype: number } | undefined {
  return keys.find((key) => Math.abs(key.time - time) < 1e-6);
}

function keyTypeAt(keys: Array<{ time: number; value: number; keytype: number }>, time: number): number {
  if (!keys.length) return 0;
  let result = keys[0]!.keytype;
  for (const key of keys) {
    if (key.time > time) break;
    result = key.keytype;
  }
  return result;
}

function nodeMapping(node: DataObject): number[] | undefined {
  const values = node.ImageIndexLocal2Global;
  if (!Array.isArray(values) || !values.length) return undefined;
  return values.map((value) => Math.trunc(numberValue(value, -1)));
}

function spriteNameFor(
  indexValue: number,
  mapping: number[] | undefined,
  imageNames: string[],
  fallback = '',
): string {
  const local = Math.round(indexValue);
  if (Math.abs(indexValue - local) > 1e-3) return fallback;
  const global = mapping && local >= 0 && local < mapping.length ? mapping[local]! : local;
  return global >= 0 && global < imageNames.length ? imageNames[global] || fallback : fallback;
}

function firstSpriteName(mapping: number[] | undefined, imageNames: string[]): string {
  if (mapping) {
    for (const index of mapping) {
      if (index >= 0 && imageNames[index]) return imageNames[index]!;
    }
  }
  return imageNames.find(Boolean) ?? '';
}

export function mappedDofSpriteNames(mapping: number[] | undefined, imageNames: string[]): string[] {
  if (!mapping) return [];
  const names: string[] = [];
  const seen = new Set<string>();
  for (const globalIndex of mapping) {
    const name = globalIndex >= 0 && globalIndex < imageNames.length ? imageNames[globalIndex] : '';
    const key = name?.toLowerCase();
    if (!name || !key || seen.has(key)) continue;
    seen.add(key);
    names.push(name);
  }
  return names;
}

function texturePointer(renderData: DataObject): DataObject | undefined {
  return renderData.texture ?? renderData.m_Texture;
}

function alphaPointer(renderData: DataObject): DataObject | undefined {
  return renderData.alphaTexture ?? renderData.m_AlphaTexture;
}

function byteView(value: unknown): Uint8Array | undefined {
  if (value instanceof Uint8Array) return value;
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  }
  if (Array.isArray(value)) return Uint8Array.from(value.map((entry) => numberValue(entry)));
  if (typeof value === 'string' && value.length >= 2 && value.length % 2 === 0 && /^[0-9a-f]+$/i.test(value)) {
    const bytes = new Uint8Array(value.length / 2);
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Number.parseInt(value.slice(index * 2, index * 2 + 2), 16);
    }
    return bytes;
  }
  return undefined;
}

export function dofSpriteMeshBounds(
  fields: DataObject,
  renderData: DataObject,
): AtlasSprite['meshBounds'] {
  const vertexData = renderData.m_VertexData ?? renderData.vertexData;
  const raw = byteView(vertexData?.m_DataSize ?? vertexData?._typelessdata ?? vertexData?.data);
  const vertexCount = Math.trunc(numberValue(vertexData?.m_VertexCount ?? vertexData?.vertexCount));
  const channels = Array.isArray(vertexData?.m_Channels)
    ? vertexData.m_Channels as DataObject[]
    : Array.isArray(vertexData?.channels) ? vertexData.channels as DataObject[] : [];
  if (!raw?.length || vertexCount <= 0 || !channels.length) return undefined;

  const formatSizes: Record<number, number> = {
    0: 4, 1: 2, 2: 1, 3: 1, 4: 2, 5: 2,
    6: 1, 7: 1, 8: 2, 9: 2, 10: 4, 11: 4,
  };
  let stride = 0;
  let positionOffset = -1;
  for (const channel of channels) {
    const stream = Math.trunc(numberValue(channel.stream));
    const dimension = Math.trunc(numberValue(channel.dimension));
    const format = Math.trunc(numberValue(channel.format));
    const offset = Math.trunc(numberValue(channel.offset));
    const componentSize = formatSizes[format];
    if (stream !== 0 || dimension <= 0 || componentSize === undefined) continue;
    stride = Math.max(stride, offset + componentSize * dimension);
    if (positionOffset < 0 && format === 0 && dimension === 3) positionOffset = offset;
  }
  if (positionOffset < 0 || stride <= 0 || raw.length < vertexCount * stride) return undefined;

  const pixelsPerUnit = numberValue(fields.m_PixelsToUnits, 100) || 100;
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (let index = 0; index < vertexCount; index += 1) {
    const base = index * stride + positionOffset;
    const x = view.getFloat32(base, true) * pixelsPerUnit;
    const y = view.getFloat32(base + 4, true) * pixelsPerUnit;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return undefined;
    minX = Math.min(minX, x);
    minY = Math.min(minY, y);
    maxX = Math.max(maxX, x);
    maxY = Math.max(maxY, y);
  }
  return [minX, minY, maxX, maxY];
}

function parseSpriteBinding(
  manager: AssetManager,
  spriteInfo: ObjectInfo,
  sourceId: number,
  parsedFields?: DataObject,
): SpriteBinding | undefined {
  let fields: DataObject;
  try {
    fields = parsedFields ?? typeTreeFields(spriteInfo);
  } catch {
    return undefined;
  }
  const rect = fields.m_Rect ?? {};
  const offset = fields.m_Offset ?? {};
  const pivot = fields.m_Pivot ?? { x: 0.5, y: 0.5 };
  const renderData = fields.m_RD ?? {};
  const textureRect = renderData.textureRect ?? renderData.m_TextureRect ?? rect;
  const textureOffset = renderData.textureRectOffset ?? renderData.m_TextureRectOffset ?? { x: 0, y: 0 };
  const textureInfo = resolvePointer(manager, texturePointer(renderData));
  if (!textureInfo) return undefined;
  const alphaInfo = resolvePointer(manager, alphaPointer(renderData));
  const width = Math.max(0, numberValue(textureRect.width));
  const height = Math.max(0, numberValue(textureRect.height));
  const originalWidth = Math.max(width, numberValue(rect.width, width));
  const originalHeight = Math.max(height, numberValue(rect.height, height));
  const pivotPoint = vector(pivot, 0.5, 0.5);
  const trimOffset = vector(textureOffset);
  const spriteOffset = vector(offset);
  const name = String(fields.m_Name ?? objectName(spriteInfo) ?? 'Sprite');
  const settings = renderData.settings ?? renderData.m_Settings ?? {};
  const rotation = String(settings.packingRotation ?? settings.packing_rotation ?? 'None');
  return {
    name,
    sourceId,
    textureInfo,
    alphaInfo,
    atlasSprite: {
      name,
      x: numberValue(textureRect.x),
      unityY: numberValue(textureRect.y),
      width,
      height,
      pivotX: pivotPoint.x,
      pivotY: 1 - pivotPoint.y,
      offsetX: trimOffset.x,
      offsetY: Math.max(0, originalHeight - trimOffset.y - height),
      originalWidth,
      originalHeight,
      rotated: rotation === 'Rotate90' || rotation === '4',
      meshBounds: dofSpriteMeshBounds(fields, renderData),
    },
    anchor: [
      pivotPoint.x * originalWidth - spriteOffset.x,
      (1 - pivotPoint.y) * originalHeight + spriteOffset.y,
    ],
  };
}

function channelsFor(node: DataObject): DofChannels {
  const channels = Array.isArray(node.BezierFramesTypes) ? node.BezierFramesTypes : [];
  return {
    posX: keyframes(channels[0]),
    posY: keyframes(channels[1]),
    depth: keyframes(channels[2]),
    scaleX: keyframes(channels[3]),
    scaleY: keyframes(channels[4]),
    rotation: keyframes(channels[5]),
    red: keyframes(channels[8]),
    green: keyframes(channels[9]),
    blue: keyframes(channels[10]),
    opacity: keyframes(channels[11]),
    sprite: keyframes(channels[12]),
    flipX: keyframes(channels[13]),
  };
}

function collectTimes(channels: DofChannels, endTime: number): number[] {
  const times = new Set<number>();
  for (const keys of Object.values(channels) as DofKeys[]) {
    keys.forEach((key) => times.add(key.time));
  }
  if (Number.isFinite(endTime) && endTime >= 0) times.add(endTime);
  if (!times.size) times.add(0);
  return [...times].sort((left, right) => left - right);
}

function shaderRefKey(node: DataObject): string | undefined {
  const pointer = node.RenderTypeShader;
  if (!pointer || typeof pointer !== 'object') return undefined;
  const name = String(pointer.m_Name ?? pointer.name ?? '').trim().toLowerCase();
  if (name) return `name:${name}`;
  const id = pointer.pathID ?? pointer.m_PathID ?? pointer.pathId ?? pointer.m_PathId;
  if (id === undefined || id === null) return undefined;
  const fileId = pointer.fileID ?? pointer.m_FileID ?? pointer.fileId ?? 0;
  return `pptr:${String(fileId)}:${String(id)}`;
}

function shaderNameForKind(kind: DofShaderKind | undefined): string | undefined {
  if (kind === 'gradient') return 'Anim2D/ShaderGradientMap+Alpha';
  if (kind === 'additive') return 'Anim2D/Additive+Alpha';
  if (kind === 'normal') return 'Anim2D/Normal+Alpha';
  return undefined;
}

function layerLooksAdditive(value: unknown): boolean {
  const name = String(value ?? '').trim().toLowerCase();
  if (name.startsWith('sprite_light')) return true;
  const tokens = new Set(name.split(/[^a-z0-9]+/).filter(Boolean));
  return ['light', 'lights', 'glow', 'glows', 'glint', 'glints', 'spark', 'sparks', 'flare',
    'flares', 'emissive', 'emission', 'bloom', 'shine', 'shiny'].some((token) => tokens.has(token));
}

function atlasSpriteFor(binding: SpriteBinding, decoded: DecodedAtlas): AtlasSprite {
  return {
    ...binding.atlasSprite,
    y: decoded.height - binding.atlasSprite.unityY - binding.atlasSprite.height,
  };
}

function nodeBindings(context: DofAnimationContext, node: DataObject): SpriteBinding[] {
  const mapping = nodeMapping(node);
  const names = mappedDofSpriteNames(mapping, context.imageNames);
  if (!names.length) {
    const fallback = firstSpriteName(mapping, context.imageNames);
    if (fallback) names.push(fallback);
  }
  const seen = new Set<string>();
  return names.flatMap((name) => {
    const binding = context.bindings.get(name.toLowerCase());
    const key = binding ? `${binding.sourceId}:${binding.name.toLowerCase()}` : '';
    if (!binding || seen.has(key)) return [];
    seen.add(key);
    return [binding];
  });
}

function shaderSaturation(
  members: Array<{ context: DofAnimationContext; node: DataObject }>,
  decoded: Map<number, DecodedAtlas>,
): number | undefined {
  const spritesBySource = new Map<number, Map<string, AtlasSprite>>();
  members.forEach(({ context, node }) => {
    nodeBindings(context, node).forEach((binding) => {
      const atlas = decoded.get(binding.sourceId);
      if (!atlas) return;
      const sprites = spritesBySource.get(binding.sourceId) ?? new Map<string, AtlasSprite>();
      sprites.set(binding.name.toLowerCase(), atlasSpriteFor(binding, atlas));
      spritesBySource.set(binding.sourceId, sprites);
    });
  });
  const averages: number[] = [];
  spritesBySource.forEach((sprites, sourceId) => {
    const atlas = decoded.get(sourceId);
    if (!atlas) return;
    const average = averageDofSpriteSaturation(atlas.pixels, atlas.width, atlas.height, sprites.values());
    if (average !== undefined) averages.push(average);
  });
  return averages.length ? averages.reduce((total, value) => total + value, 0) / averages.length : undefined;
}

function inferDofShaderKinds(
  contexts: DofAnimationContext[],
  decoded: Map<number, DecodedAtlas>,
  color: DofChromaticColor | undefined,
): Map<string, DofShaderKind> {
  const grouped = new Map<string, Array<{ context: DofAnimationContext; node: DataObject }>>();
  contexts.forEach((context) => {
    (context.structure.Nodes as DataObject[]).forEach((node) => {
      if (numberValue(node.NodeType, -1) !== 1) return;
      const key = shaderRefKey(node);
      if (!key) return;
      const members = grouped.get(key) ?? [];
      members.push({ context, node });
      grouped.set(key, members);
    });
  });

  const kinds = new Map<string, DofShaderKind>();
  grouped.forEach((members, key) => {
    const named = key.startsWith('name:') ? key.slice('name:'.length) : '';
    if (named.includes('shadergradientmap')) kinds.set(key, 'gradient');
    else if (named.includes('additive')) kinds.set(key, 'additive');
    else if (named.includes('normal')) kinds.set(key, 'normal');
    else if (members.length && members.every(({ node }) => layerLooksAdditive(node.Name))) kinds.set(key, 'additive');
  });
  if (!color) return kinds;

  const nonAdditive = [...grouped.keys()].filter((key) => kinds.get(key) !== 'additive');
  if (nonAdditive.length !== 2) return kinds;
  const knownGradient = nonAdditive.find((key) => kinds.get(key) === 'gradient');
  const knownNormal = nonAdditive.find((key) => kinds.get(key) === 'normal');
  if (knownGradient || knownNormal) {
    const other = nonAdditive.find((key) => key !== (knownGradient ?? knownNormal));
    if (other) kinds.set(other, knownGradient ? 'normal' : 'gradient');
    return kinds;
  }

  const saturation = new Map(nonAdditive.map((key) => [key, shaderSaturation(grouped.get(key)!, decoded)]));
  const usable = nonAdditive.filter((key) => saturation.get(key) !== undefined)
    .sort((left, right) => saturation.get(left)! - saturation.get(right)!);
  if (usable.length === 2) {
    const low = saturation.get(usable[0]!)!;
    const high = saturation.get(usable[1]!)!;
    if (low <= 8 && high - low >= 20) {
      kinds.set(usable[0]!, 'gradient');
      kinds.set(usable[1]!, 'normal');
      return kinds;
    }
  }

  const colorHits = (key: string): number => (grouped.get(key) ?? []).filter(({ node }) => (
    String(node.Name ?? '').toLowerCase().includes(color)
  )).length;
  const ordered = [...nonAdditive].sort((left, right) => (
    colorHits(right) - colorHits(left)
    || (grouped.get(right)?.length ?? 0) - (grouped.get(left)?.length ?? 0)
  ));
  kinds.set(ordered[0]!, 'normal');
  kinds.set(ordered[1]!, 'gradient');
  return kinds;
}

function chromaticSpriteBindings(
  contexts: DofAnimationContext[],
  kinds: Map<string, DofShaderKind>,
): Map<number, Map<string, SpriteBinding>> {
  const bySource = new Map<number, Map<string, SpriteBinding>>();
  contexts.forEach((context) => {
    (context.structure.Nodes as DataObject[]).forEach((node) => {
      const key = shaderRefKey(node);
      if (!key || kinds.get(key) !== 'gradient') return;
      nodeBindings(context, node).forEach((binding) => {
        const bindings = bySource.get(binding.sourceId) ?? new Map<string, SpriteBinding>();
        bindings.set(binding.name.toLowerCase(), binding);
        bySource.set(binding.sourceId, bindings);
      });
    });
  });
  return bySource;
}

function chooseBlend(node: DataObject, shaderKinds: Map<string, DofShaderKind>): number {
  const kind = shaderKinds.get(shaderRefKey(node) ?? '');
  if (kind === 'additive') return 2;
  const shader = String(node.RenderTypeShader?.m_Name ?? node.RenderTypeShader?.name ?? '').toLowerCase();
  const name = String(node.Name ?? '').toLowerCase();
  return shader.includes('additive') || layerLooksAdditive(name) ? 2 : 0;
}

function buildLayer(
  node: DataObject,
  order: number,
  endTime: number,
  imageNames: string[],
  bindings: Map<string, SpriteBinding>,
  shaderKinds: Map<string, DofShaderKind>,
): (AnimationLayer & { _depth: number; _order: number }) | undefined {
  if (numberValue(node.NodeType, -1) !== 1) return undefined;
  const channel = channelsFor(node);
  const mapping = nodeMapping(node);
  const spriteKeys = channel.sprite;
  const defaultSprite = spriteKeys.length
    ? spriteNameFor(spriteKeys[0]!.value, mapping, imageNames, firstSpriteName(mapping, imageNames))
    : firstSpriteName(mapping, imageNames);
  const defaultBinding = bindings.get(defaultSprite.toLowerCase());
  const usedSprites = new Set<string>(mappedDofSpriteNames(mapping, imageNames));
  if (defaultSprite) usedSprites.add(defaultSprite);
  spriteKeys.forEach((key) => {
    const name = spriteNameFor(key.value, mapping, imageNames, defaultSprite);
    if (name) usedSprites.add(name);
  });
  const spriteAnchorMap: Record<string, [number, number]> = {};
  usedSprites.forEach((name) => {
    const binding = bindings.get(name.toLowerCase());
    if (binding) spriteAnchorMap[name] = binding.anchor;
  });

  const offset = node.OffsetPos ?? {};
  const offsetX = numberValue(offset.x);
  const offsetY = numberValue(offset.y);
  const offsetZ = numberValue(offset.z);
  const imageScale = numberValue(node.ImageScale, 1) || 1;
  const frames: AnimationFrame[] = [];
  const times = collectTimes(channel, endTime);
  for (const time of times) {
    const frame: AnimationFrame = { time };
    const posX = keyAt(channel.posX, time);
    const posY = keyAt(channel.posY, time);
    if (posX || posY || (time === times[0] && (offsetX !== 0 || offsetY !== 0))) {
      const xType = posX?.keytype ?? keyTypeAt(channel.posX, time);
      const yType = posY?.keytype ?? keyTypeAt(channel.posY, time);
      frame.pos = {
        immediate: immediate(xType) === 0 || immediate(yType) === 0 ? 0 : 1,
        x: (valueAt(channel.posX, time, 0) + offsetX) * 2,
        y: -(valueAt(channel.posY, time, 0) + offsetY) * 2,
      };
    }
    const scaleX = keyAt(channel.scaleX, time);
    const scaleY = keyAt(channel.scaleY, time);
    const flipX = keyAt(channel.flipX, time);
    if (scaleX || scaleY || flipX || (time === times[0] && imageScale !== 1)) {
      let x = valueAt(channel.scaleX, time, 1) * imageScale;
      const y = valueAt(channel.scaleY, time, 1) * imageScale;
      if (valueAt(channel.flipX, time, 0) >= 0.5) x = -Math.abs(x);
      const xType = scaleX?.keytype ?? keyTypeAt(channel.scaleX, time);
      const yType = scaleY?.keytype ?? keyTypeAt(channel.scaleY, time);
      frame.scale = {
        immediate: immediate(xType) === 0 || immediate(yType) === 0 ? 0 : 1,
        x: x * 100,
        y: y * 100,
      };
    }
    const rotation = keyAt(channel.rotation, time);
    if (rotation) {
      frame.rotation = {
        immediate: immediate(rotation.keytype),
        value: -rotation.value * (180 / Math.PI),
      };
    }
    const opacity = keyAt(channel.opacity, time);
    if (opacity) frame.opacity = { immediate: immediate(opacity.keytype), value: opacity.value };
    const depth = keyAt(channel.depth, time);
    if (depth) frame.depth = { immediate: immediate(depth.keytype), value: depth.value + offsetZ };
    else if (time === times[0] && offsetZ !== 0) frame.depth = { immediate: 1, value: offsetZ };
    const sprite = keyAt(channel.sprite, time);
    if (sprite) {
      frame.sprite = {
        immediate: immediate(sprite.keytype),
        string: spriteNameFor(sprite.value, mapping, imageNames, defaultSprite),
      };
    } else if (time === times[0] && defaultSprite) {
      frame.sprite = { immediate: 1, string: defaultSprite };
    }
    const red = keyAt(channel.red, time);
    const green = keyAt(channel.green, time);
    const blue = keyAt(channel.blue, time);
    if (red || green || blue) {
      const rType = red?.keytype ?? keyTypeAt(channel.red, time);
      const gType = green?.keytype ?? keyTypeAt(channel.green, time);
      const bType = blue?.keytype ?? keyTypeAt(channel.blue, time);
      frame.rgb = {
        immediate: immediate(rType) === 0 || immediate(gType) === 0 || immediate(bType) === 0 ? 0 : 1,
        red: Math.round(valueAt(channel.red, time, 1) * 255),
        green: Math.round(valueAt(channel.green, time, 1) * 255),
        blue: Math.round(valueAt(channel.blue, time, 1) * 255),
      };
    }
    frames.push(frame);
  }

  return {
    name: String(node.Name ?? `Layer_${order}`),
    type: 1,
    blend: chooseBlend(node, shaderKinds),
    parent: node.Parent === null || node.Parent === undefined
      ? -1
      : Math.trunc(numberValue(node.Parent, -1)),
    id: order,
    src: defaultBinding?.sourceId ?? 0,
    width: 0,
    height: 0,
    anchor_x: defaultBinding?.anchor[0] ?? 0,
    anchor_y: defaultBinding?.anchor[1] ?? 0,
    frames,
    sprite_anchor_map: Object.keys(spriteAnchorMap).length ? spriteAnchorMap : null,
    shader_name: shaderNameForKind(shaderKinds.get(shaderRefKey(node) ?? '')),
    _depth: channel.depth.length ? channel.depth[0]!.value + offsetZ : offsetZ,
    _order: order,
  };
}

function buildTrack(
  info: ObjectInfo,
  structure: DataObject,
  imageNames: string[],
  bindings: Map<string, SpriteBinding>,
  shaderKinds: Map<string, DofShaderKind>,
): AnimationTrack {
  const size = structure.size ?? structure.Size ?? {};
  const endTime = numberValue(structure.EndTime);
  const layers = (structure.Nodes as DataObject[])
    .map((node, index) => buildLayer(node, index, endTime, imageNames, bindings, shaderKinds))
    .filter((layer): layer is NonNullable<typeof layer> => Boolean(layer))
    .sort((left, right) => right._depth - left._depth || left._order - right._order);
  const idMap = new Map(layers.map((layer, index) => [layer.id, index]));
  layers.forEach((layer, index) => {
    layer.parent = idMap.get(layer.parent) ?? -1;
    layer.id = index;
    delete (layer as Partial<typeof layer>)._depth;
    delete (layer as Partial<typeof layer>)._order;
  });
  return {
    name: objectName(info).replace(/\.ANIMBBB$/i, ''),
    width: Math.round(numberValue(size.x, 250)),
    height: Math.round(numberValue(size.y, 250)),
    loop_offset: numberValue(structure.Looping) ? 0 : -1,
    centered: 1,
    layers,
    particle_emitters: (Array.isArray(structure.ParticleNodes) ? structure.ParticleNodes : [])
      .map(particleEmitter)
      .filter((emitter): emitter is DofParticleEmitter => Boolean(emitter)),
  };
}

function particleEmitter(node: DataObject): DofParticleEmitter | undefined {
  const prefabPathId = pathId(node.ParticleSystemPrefab);
  if (prefabPathId === undefined) return undefined;
  const offset = node.OffsetPos ?? {};
  const channels: Record<number, DofKeys> = {};
  const frameArrays = Array.isArray(node.BezierFramesTypes) ? node.BezierFramesTypes : [];
  frameArrays.forEach((channel, index) => {
    const values = keyframes(channel);
    if (values.length) channels[index] = values;
  });
  return {
    name: String(node.Name ?? 'Particle'),
    prefabPathId: prefabPathId.toString(),
    offset: [numberValue(offset.x), numberValue(offset.y), numberValue(offset.z)],
    imageScale: numberValue(node.ImageScale, 1) || 1,
    channels,
  };
}

async function texturePixels(manager: AssetManager, info: ObjectInfo): Promise<{
  raw: Uint8Array;
  width: number;
  height: number;
}> {
  const texture = info.object as DataObject;
  const format = String(texture.textureFormat ?? '');
  if (/^Alpha8$/i.test(format) || /^PVRTC_(?:RGB|RGBA)[24]$/i.test(format) || /^ASTC_(?:RGB|RGBA)_\d+x\d+$/i.test(format)) {
    const width = numberValue(texture.width);
    const height = numberValue(texture.height);
    let compressed = texture.data as Uint8Array | null | undefined;
    const stream = texture.streamData as DataObject | null | undefined;
    if ((!compressed || !compressed.length) && stream?.path) {
      compressed = manager.resolveResource(
        String(stream.path),
        numberValue(stream.offset),
        numberValue(stream.size),
      ) ?? undefined;
    }
    if (!compressed?.length) {
      throw new Error(`Texture '${objectName(info)}' is missing its external ${format} data.`);
    }

    if (/^Alpha8$/i.test(format)) {
      const pixelCount = width * height;
      if (compressed.length < pixelCount) {
        throw new Error(`Texture '${objectName(info)}' contains incomplete Alpha8 data.`);
      }
      const rgba = new Uint8Array(pixelCount * 4);
      for (let y = 0; y < height; y += 1) {
        const sourceRow = y * width;
        const targetRow = (height - y - 1) * width;
        for (let x = 0; x < width; x += 1) {
          const target = (targetRow + x) * 4;
          rgba[target] = 255;
          rgba[target + 1] = 255;
          rgba[target + 2] = 255;
          rgba[target + 3] = compressed[sourceRow + x]!;
        }
      }
      return { raw: rgba, width, height };
    }

    const decoder = await pvrtcDecoder();
    let decoded: (ArrayLike<number> & { delete?: () => void }) | null;
    if (/^PVRTC_/i.test(format)) {
      decoded = decoder.decode_pvrtc(compressed, width, height, /2$/i.test(format));
    } else {
      const block = /_(\d+)x(\d+)$/i.exec(format);
      if (!block) throw new Error(`Texture '${objectName(info)}' has an invalid ${format} block size.`);
      decoded = decoder.decode_astc(compressed, width, height, Number(block[1]), Number(block[2]));
    }
    if (!decoded || decoded.length !== width * height * 4) {
      throw new Error(`Texture '${objectName(info)}' did not decode from ${format}.`);
    }
    const bgra = new Uint8Array(decoded.length);
    for (let index = 0; index < decoded.length; index += 1) bgra[index] = decoded[index]!;
    decoded.delete?.();

    const rgba = new Uint8Array(bgra.length);
    const rowSize = width * 4;
    for (let y = 0; y < height; y += 1) {
      const sourceRow = y * rowSize;
      const targetRow = (height - y - 1) * rowSize;
      for (let x = 0; x < rowSize; x += 4) {
        rgba[targetRow + x] = bgra[sourceRow + x + 2]!;
        rgba[targetRow + x + 1] = bgra[sourceRow + x + 1]!;
        rgba[targetRow + x + 2] = bgra[sourceRow + x]!;
        rgba[targetRow + x + 3] = bgra[sourceRow + x + 3]!;
      }
    }
    return { raw: rgba, width, height };
  }

  const result = await manager.exportFile(info, { type: 'rgbaArray', worker: true });
  const data = result?.data;
  const raw = data?.raw;
  if (!(raw instanceof Uint8Array) && !(raw instanceof Uint8ClampedArray)) {
    throw new Error(`Texture '${objectName(info)}' did not decode to RGBA pixels.`);
  }
  return { raw: new Uint8Array(raw), width: numberValue(data.width), height: numberValue(data.height) };
}

function canvasFor(width: number, height: number): OffscreenCanvas | HTMLCanvasElement {
  if ('OffscreenCanvas' in window) return new OffscreenCanvas(width, height);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

async function atlasPixels(
  manager: AssetManager,
  textureInfo: ObjectInfo,
  alphaInfo?: ObjectInfo,
): Promise<{ pixels: Uint8ClampedArray; width: number; height: number }> {
  const base = await texturePixels(manager, textureInfo);
  const pixels = new Uint8ClampedArray(base.raw);
  if (alphaInfo) {
    try {
      const alpha = await texturePixels(manager, alphaInfo);
      let alphaPixels = new Uint8ClampedArray(alpha.raw);
      if (alpha.width !== base.width || alpha.height !== base.height) {
        const sourceCanvas = canvasFor(alpha.width, alpha.height);
        sourceCanvas.getContext('2d')?.putImageData(new ImageData(alphaPixels, alpha.width, alpha.height), 0, 0);
        const targetCanvas = canvasFor(base.width, base.height);
        const targetContext = targetCanvas.getContext('2d');
        targetContext?.drawImage(sourceCanvas, 0, 0, base.width, base.height);
        alphaPixels = targetContext?.getImageData(0, 0, base.width, base.height).data ?? alphaPixels;
      }
      let alphaVaries = false;
      for (let index = 3; index < alphaPixels.length; index += 4) {
        if (alphaPixels[index] !== 255) {
          alphaVaries = true;
          break;
        }
      }
      for (let index = 0; index < pixels.length; index += 4) {
        pixels[index + 3] = alphaVaries
          ? alphaPixels[index + 3]!
          : Math.round(alphaPixels[index]! * 0.2126 + alphaPixels[index + 1]! * 0.7152 + alphaPixels[index + 2]! * 0.0722);
      }
    } catch {
      // A color atlas with usable native alpha is still renderable without its split mask.
    }
  }
  return { pixels, width: base.width, height: base.height };
}

async function imageFromAtlasPixels(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
): Promise<DecodedTextureImage> {
  // Unity's Texture2D decoder returns straight RGBA, including chromatic rewrites.
  return textureImageFromRgbaPixels(pixels, width, height, 'straight');
}

async function atlasImage(
  manager: AssetManager,
  textureInfo: ObjectInfo,
  alphaInfo?: ObjectInfo,
): Promise<DecodedTextureImage & { width: number; height: number }> {
  const decoded = await atlasPixels(manager, textureInfo, alphaInfo);
  return {
    ...await imageFromAtlasPixels(decoded.pixels, decoded.width, decoded.height),
    width: decoded.width,
    height: decoded.height,
  };
}

async function decodeAtlases(
  manager: AssetManager,
  builders: Map<string, AtlasBuilder>,
): Promise<Map<number, DecodedAtlas>> {
  const decoded = new Map<number, DecodedAtlas>();
  for (const builder of builders.values()) {
    const atlas = await atlasPixels(manager, builder.textureInfo, builder.alphaInfo);
    decoded.set(builder.sourceId, { sourceId: builder.sourceId, ...atlas });
  }
  return decoded;
}

async function buildAtlases(
  builders: Map<string, AtlasBuilder>,
  decodedAtlases: Map<number, DecodedAtlas>,
): Promise<LoadedAtlasInput[]> {
  const atlases: LoadedAtlasInput[] = [];
  for (const builder of builders.values()) {
    const decoded = decodedAtlases.get(builder.sourceId);
    if (!decoded) continue;
    const sprites = new Map<string, AtlasSprite>();
    builder.sprites.forEach((binding, key) => {
      sprites.set(key, {
        ...binding.atlasSprite,
        y: decoded.height - binding.atlasSprite.unityY - binding.atlasSprite.height,
      });
    });
    atlases.push({
      sourceId: builder.sourceId,
      ...await imageFromAtlasPixels(decoded.pixels, decoded.width, decoded.height),
      data: {
        imagePath: `bundle://${objectName(builder.textureInfo)}`,
        width: decoded.width,
        height: decoded.height,
        hires: false,
        sprites,
      },
    });
  }
  return atlases;
}

async function extractAudio(manager: AssetManager, bundlePath: string): Promise<AudioClipInput[]> {
  const audio: AudioClipInput[] = [];
  for (const info of manager.getObjectInfosByClass('AudioClip')) {
    try {
      const result = await manager.exportFile(info, { type: 'blob' });
      const blob = result?.data?.raw;
      if (!(blob instanceof Blob) || !blob.size) continue;
      const name = objectName(info) || result.src?.split('/').at(-1) || 'audio';
      const extension = result.fileType ? `.${result.fileType}` : '';
      audio.push({ name, path: `${bundlePath}/${name}${extension}`, blob });
    } catch {
      // Some Unity clips only resolve after their external resource file is supplied.
    }
  }
  return audio;
}

function pair(entry: unknown): [string, DataObject] | undefined {
  if (Array.isArray(entry) && typeof entry[0] === 'string' && entry[1] && typeof entry[1] === 'object') {
    return [entry[0], entry[1] as DataObject];
  }
  if (entry && typeof entry === 'object') {
    const value = entry as DataObject;
    const key = value.first ?? value.key;
    const payload = value.second ?? value.value;
    if (typeof key === 'string' && payload && typeof payload === 'object') return [key, payload];
  }
  return undefined;
}

function colorTuple(value: DataObject | undefined, fallback: ColorTuple = [1, 1, 1, 1]): ColorTuple {
  return [
    numberValue(value?.r, fallback[0]),
    numberValue(value?.g, fallback[1]),
    numberValue(value?.b, fallback[2]),
    numberValue(value?.a, fallback[3]),
  ];
}

function curveRange(value: DataObject | undefined, fallback = 0): [number, number] {
  if (!value) return [fallback, fallback];
  const scalar = numberValue(value.scalar, fallback);
  const minimum = numberValue(value.minScalar, scalar);
  return Math.trunc(numberValue(value.minMaxState)) === 3
    ? [Math.min(minimum, scalar), Math.max(minimum, scalar)]
    : [scalar, scalar];
}

function curveKeys(value: DataObject | undefined): Array<{ time: number; value: number }> {
  const curve = value?.maxCurve ?? value?.minCurve ?? value;
  const keys = Array.isArray(curve?.m_Curve) ? curve.m_Curve : [];
  return keys.map((key: DataObject) => ({
    time: numberValue(key.time),
    value: numberValue(key.value, 1),
  })).sort((left: { time: number }, right: { time: number }) => left.time - right.time);
}

function gradientKeys(value: DataObject | undefined): {
  colors: Array<{ time: number; color: ColorTuple }>;
  alpha: Array<{ time: number; value: number }>;
} {
  const gradient = value?.maxGradient ?? value?.minGradient ?? value;
  if (!gradient) return { colors: [], alpha: [] };
  const colors = Array.from({ length: Math.max(0, Math.trunc(numberValue(gradient.m_NumColorKeys))) }, (_, index) => ({
    time: numberValue(gradient[`ctime${index}`]) / 65535,
    color: colorTuple(gradient[`key${index}`]),
  })).sort((left, right) => left.time - right.time);
  const alpha = Array.from({ length: Math.max(0, Math.trunc(numberValue(gradient.m_NumAlphaKeys))) }, (_, index) => ({
    time: numberValue(gradient[`atime${index}`]) / 65535,
    value: numberValue(gradient[`key${index}`]?.a, 1),
  })).sort((left, right) => left.time - right.time);
  return { colors, alpha };
}

function vector3(value: DataObject | undefined): [number, number, number] {
  return [numberValue(value?.x), numberValue(value?.y), numberValue(value?.z)];
}

export async function extractDofParticleLibrary(
  buffer: ArrayBuffer,
  requestedPathIds?: ReadonlySet<string>,
): Promise<DofParticlePrefabInput[]> {
  const { load } = await import('unityfs-js');
  const manager = await load(buffer);
  try {
    const particleSystems = new Map(manager.getObjectInfosByClass('ParticleSystem').map((info) => [info.pathID.toString(), info]));
    const particleRenderers = new Map(manager.getObjectInfosByClass('ParticleSystemRenderer').map((info) => [info.pathID.toString(), info]));
    const transforms = new Map(manager.getObjectInfosByClass('Transform').map((info) => [info.pathID.toString(), info]));
    const textureCache = new Map<string, DecodedTextureImage>();
    const prefabs: DofParticlePrefabInput[] = [];

    for (const gameObject of manager.getObjectInfosByClass('GameObject')) {
      if (requestedPathIds?.size && !requestedPathIds.has(gameObject.pathID.toString())) continue;
      const fields = objectFields(gameObject);
      const components = Array.isArray(fields.m_Component) ? fields.m_Component : [];
      let systemInfo: ObjectInfo | undefined;
      let rendererInfo: ObjectInfo | undefined;
      let transformInfo: ObjectInfo | undefined;
      for (const component of components) {
        const pointer = component?.component ?? component?.m_Component;
        const id = pathId(pointer)?.toString();
        if (!id) continue;
        systemInfo ??= particleSystems.get(id);
        rendererInfo ??= particleRenderers.get(id);
        transformInfo ??= transforms.get(id);
      }
      if (!systemInfo || !rendererInfo) continue;
      const system = objectFields(systemInfo);
      const renderer = objectFields(rendererInfo);
      const materialPointer = (Array.isArray(renderer.m_Materials) ? renderer.m_Materials : [])
        .find((pointer: DataObject) => pathId(pointer) !== undefined);
      const materialInfo = resolvePointer(manager, materialPointer);
      if (!materialInfo) continue;
      const material = objectFields(materialInfo);
      const saved = material.m_SavedProperties ?? {};
      const colors = new Map<string, ColorTuple>();
      const floats = new Map<string, number>();
      (Array.isArray(saved.m_Colors) ? saved.m_Colors : []).forEach((entry: unknown) => {
        const parsed = pair(entry);
        if (parsed) colors.set(parsed[0], colorTuple(parsed[1]));
      });
      (Array.isArray(saved.m_Floats) ? saved.m_Floats : []).forEach((entry: unknown) => {
        if (Array.isArray(entry) && typeof entry[0] === 'string') floats.set(entry[0], numberValue(entry[1]));
        else if (entry && typeof entry === 'object') {
          const value = entry as DataObject;
          if (typeof value.first === 'string') floats.set(value.first, numberValue(value.second));
        }
      });
      let textureInfo: ObjectInfo | undefined;
      for (const entry of Array.isArray(saved.m_TexEnvs) ? saved.m_TexEnvs : []) {
        const parsed = pair(entry);
        if (parsed?.[0] !== '_MainTex') continue;
        textureInfo = resolvePointer(manager, parsed[1].m_Texture);
        if (textureInfo) break;
      }
      if (!textureInfo) continue;
      const textureKey = textureInfo.pathID.toString();
      let textureImage = textureCache.get(textureKey);
      if (!textureImage) {
        textureImage = await atlasImage(manager, textureInfo);
        textureCache.set(textureKey, textureImage);
      }

      const initial = system.InitialModule ?? {};
      const emission = system.EmissionModule ?? {};
      const shape = system.ShapeModule ?? {};
      const colorModule = system.ColorModule ?? {};
      const sizeModule = system.SizeModule ?? {};
      const transform = transformInfo ? objectFields(transformInfo) : {};
      const startGradient = initial.startColor ?? {};
      const materialColor: ColorTuple = [1, 1, 1, 1];
      for (const key of ['_TintColor', '_Color', '_MainColor', '_BaseColor', '_EmissionColor']) {
        const value = colors.get(key);
        if (!value) continue;
        for (let index = 0; index < 4; index += 1) {
          materialColor[index] = materialColor[index]! * value[index]!;
        }
      }
      const intensity = floats.get('_Intensity') ?? floats.get('_IntensityTint') ?? floats.get('_Glow');
      if (intensity !== undefined) {
        materialColor[0] *= intensity;
        materialColor[1] *= intensity;
        materialColor[2] *= intensity;
      }
      const lifetime = curveRange(initial.startLifetime, 1);
      const speed = curveRange(initial.startSpeed, 0);
      const size = curveRange(initial.startSize, 1);
      const rotation = curveRange(initial.startRotation, 0);
      const gravity = curveRange(initial.gravityModifier, 0);
      const colorKeys = gradientKeys(colorModule.enabled ? colorModule.gradient : undefined);
      const shaderInfo = resolvePointer(manager, material.m_Shader);
      const shaderName = shaderInfo ? String(objectFields(shaderInfo).m_Name ?? objectName(shaderInfo)) : '';
      prefabs.push({
        pathId: gameObject.pathID.toString(),
        name: String(fields.m_Name ?? objectName(gameObject) ?? 'Particle'),
        image: textureImage.image,
        alphaMode: textureImage.alphaMode,
        sourceAlphaFormat: textureImage.sourceAlphaFormat,
        blend: /additive/i.test(shaderName) ? 2 : 0,
        materialColor,
        transformPosition: vector3(transform.m_LocalPosition),
        emissionRate: curveRange(emission.enabled === false ? undefined : emission.rateOverTime, 0),
        lifetime,
        speed,
        size,
        rotation,
        gravity,
        moveWithTransform: system.moveWithTransform === undefined ? true : Boolean(system.moveWithTransform),
        startColor: [colorTuple(startGradient.minColor), colorTuple(startGradient.maxColor)],
        colorOverLifetime: colorKeys.colors,
        alphaOverLifetime: colorKeys.alpha,
        sizeOverLifetime: sizeModule.enabled ? curveKeys(sizeModule.curve) : [],
        shapeRadius: numberValue(shape.radius?.value ?? shape.radius),
        shapeAngle: numberValue(shape.angle),
      });
    }
    if (requestedPathIds?.size && !prefabs.length) {
      const candidates = manager.getObjectInfosByClass('GameObject')
        .map((info) => `${String(objectFields(info).m_Name ?? objectName(info))}:${info.pathID.toString()}`)
        .filter((value) => /particle/i.test(value))
        .slice(0, 12);
      throw new Error(
        `Requested prefab ID(s) ${[...requestedPathIds].join(', ')} were not found; `
        + `bundle candidates include ${candidates.join(', ') || 'none'}.`,
      );
    }
    return prefabs;
  } finally {
    manager.dispose();
  }
}

export async function extractDofChromaticGradients(
  buffer: ArrayBuffer,
): Promise<Map<DofChromaticColor, DofChromaticGradient>> {
  const { load } = await import('unityfs-js');
  const manager = await load(buffer);
  try {
    const gradients = new Map<DofChromaticColor, DofChromaticGradient>();
    for (const info of manager.getObjectInfosByClass('Texture2D')) {
      const name = objectName(info).toLowerCase();
      const color = dofChromaticColor(name);
      if (!color || !name.includes('gradient') || !name.includes('chromatic')) continue;
      try {
        const decoded = await texturePixels(manager, info);
        if (decoded.width <= 0 || decoded.height <= 0) continue;
        const row = Math.min(decoded.height - 1, Math.floor(decoded.height / 2));
        const start = row * decoded.width * 4;
        gradients.set(color, {
          color,
          width: decoded.width,
          pixels: new Uint8ClampedArray(decoded.raw.slice(start, start + decoded.width * 4)),
          source: 'static_data',
        });
      } catch {
        // A single unavailable texture should not discard the other color LUTs.
      }
    }
    return gradients;
  } finally {
    manager.dispose();
  }
}

export async function extractDofBundle(
  buffer: ArrayBuffer,
  bundlePath: string,
  chromaticGradient?: DofChromaticGradient,
): Promise<DofBundleResult> {
  const { load } = await import('unityfs-js');
  const manager = await load(buffer);
  try {
    const alphaByTexture = new Map<string, ObjectInfo>();
    for (const info of manager.getObjectInfosByClass('MonoBehaviour')) {
      if (!/\.TPP$/i.test(objectName(info))) continue;
      const fields = objectFields(info);
      const textureInfo = resolvePointer(manager, fields.texture);
      const alphaInfo = resolvePointer(manager, fields.alpha);
      if (textureInfo && alphaInfo) alphaByTexture.set(textureInfo.pathID.toString(), alphaInfo);
    }

    const animationInfos = manager
      .getObjectInfosByClass('MonoBehaviour')
      .filter((info) => /\.ANIMBBB$/i.test(objectName(info)) && dofStructure(info));
    const builders = new Map<string, AtlasBuilder>();
    const spriteFields = new Map<string, DataObject>();
    const contexts: DofAnimationContext[] = [];
    let nextSourceId = 0;

    for (const info of animationInfos) {
      const structure = dofStructure(info);
      if (!structure) continue;
      const imageNames: string[] = [];
      const bindings = new Map<string, SpriteBinding>();
      for (const image of structure.Images as DataObject[]) {
        const spriteInfo = resolvePointer(manager, image?.sprite ?? image?.Sprite);
        if (!spriteInfo) {
          imageNames.push('');
          continue;
        }
        const spriteKey = spriteInfo.pathID.toString();
        let fields = spriteFields.get(spriteKey);
        try {
          if (!fields) {
            fields = typeTreeFields(spriteInfo);
            spriteFields.set(spriteKey, fields);
          }
        } catch {
          imageNames.push('');
          continue;
        }
        const renderData = fields.m_RD ?? {};
        const textureInfo = resolvePointer(manager, texturePointer(renderData));
        if (!textureInfo) {
          imageNames.push('');
          continue;
        }
        const textureKey = textureInfo.pathID.toString();
        let builder = builders.get(textureKey);
        if (!builder) {
          builder = {
            sourceId: nextSourceId++,
            textureInfo,
            alphaInfo: resolvePointer(manager, alphaPointer(renderData))
              ?? alphaByTexture.get(textureKey),
            sprites: new Map(),
          };
          builders.set(textureKey, builder);
        }
        const binding = parseSpriteBinding(manager, spriteInfo, builder.sourceId, fields);
        if (!binding) {
          imageNames.push('');
          continue;
        }
        imageNames.push(binding.name);
        bindings.set(binding.name.toLowerCase(), binding);
        builder.sprites.set(binding.name.toLowerCase(), binding);
      }
      contexts.push({ info, structure, imageNames, bindings });
    }

    const audio = await extractAudio(manager, bundlePath);
    if (!contexts.length) return { atlases: [], audio, unityVersion: manager.getMetadata().unityVersion ?? undefined };
    const decodedAtlases = await decodeAtlases(manager, builders);
    const color = dofChromaticColor(bundlePath);
    const shaderKinds = inferDofShaderKinds(contexts, decodedAtlases, color);
    const gradient = color
      ? chromaticGradient?.color === color ? chromaticGradient : sampledDofChromaticGradient(color)
      : undefined;
    let chromaticSpriteCount = 0;
    if (gradient) {
      chromaticSpriteBindings(contexts, shaderKinds).forEach((bindings, sourceId) => {
        const decoded = decodedAtlases.get(sourceId);
        if (!decoded) return;
        chromaticSpriteCount += applyDofChromaticGradient(
          decoded.pixels,
          decoded.width,
          decoded.height,
          [...bindings.values()].map((binding) => atlasSpriteFor(binding, decoded)),
          gradient,
        );
      });
      if (chromaticSpriteCount) {
        console.info(
          `[DOF chromatic] Applied ${gradient.source} ${gradient.color} gradient to `
          + `${chromaticSpriteCount} sprite segment${chromaticSpriteCount === 1 ? '' : 's'} in ${bundlePath}.`,
        );
      }
    }
    const tracks = contexts.map((context) => buildTrack(
      context.info,
      context.structure,
      context.imageNames,
      context.bindings,
      shaderKinds,
    ));
    const atlases = await buildAtlases(builders, decodedAtlases);
    return {
      document: {
        rev: 6,
        blend_version: tracks.some((track) => track.layers.some((layer) => (layer.blend ?? 0) >= 2)) ? 2 : 1,
        source_format: 'dof',
        source_revision: 6,
        sources: atlases.map((atlas) => ({
          src: atlas.data.imagePath,
          id: atlas.sourceId,
          width: atlas.data.width,
          height: atlas.data.height,
        })),
        anims: tracks.sort((left, right) => left.name.localeCompare(right.name)),
      },
      atlases,
      audio,
      unityVersion: manager.getMetadata().unityVersion ?? undefined,
    };
  } finally {
    manager.dispose();
  }
}
