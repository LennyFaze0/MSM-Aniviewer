import type {
  AnimationDocument,
  AnimationCloneLayer,
  AnimationFrame,
  AnimationLayer,
  AnimationSource,
  AnimationTrack,
  InterpolatedNumber,
  InterpolatedFont,
  InterpolatedPoint,
  InterpolatedSprite,
} from './types';

export type BinRevision = 1 | 2 | 3 | 4 | 5 | 6;
export type BinFormat =
  | 'rev1_launch'
  | 'legacy'
  | 'rev2'
  | 'muppets'
  | 'choir'
  | 'rev4'
  | 'rev5'
  | 'rev6';
export type BinRevisionMode = BinRevision | BinFormat | 'auto';

export interface ParsedBin {
  document: AnimationDocument;
  revision: BinRevision;
  format: BinFormat;
}

const MAX_SOURCES = 64;
const MAX_ANIMATIONS = 5000;
const MAX_LAYERS = 20000;
const MAX_FRAMES = 200000;
const textDecoder = new TextDecoder('ascii');

export class BinParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BinParseError';
  }
}

class Reader {
  readonly bytes: Uint8Array;
  readonly view: DataView;
  offset = 0;

  constructor(buffer: ArrayBuffer) {
    this.bytes = new Uint8Array(buffer);
    this.view = new DataView(buffer);
  }

  get size(): number {
    return this.bytes.byteLength;
  }

  get remaining(): number {
    return this.size - this.offset;
  }

  seek(offset: number): void {
    if (!Number.isInteger(offset) || offset < 0 || offset > this.size) {
      throw new BinParseError(`Invalid BIN seek to 0x${offset.toString(16)}.`);
    }
    this.offset = offset;
  }

  skip(length: number): void {
    this.ensure(length);
    this.offset += length;
  }

  align(size: number): void {
    const aligned = (this.offset + (size - 1)) & ~(size - 1);
    this.seek(aligned);
  }

  ensure(length: number): void {
    if (!Number.isInteger(length) || length < 0 || this.offset + length > this.size) {
      throw new BinParseError(`Unexpected end of BIN at 0x${this.offset.toString(16)}.`);
    }
  }

  u8(): number {
    this.ensure(1);
    return this.view.getUint8(this.offset++);
  }

  i8(): number {
    this.ensure(1);
    return this.view.getInt8(this.offset++);
  }

  u16(aligned = true): number {
    if (aligned) this.align(2);
    this.ensure(2);
    const value = this.view.getUint16(this.offset, true);
    this.offset += 2;
    return value;
  }

  i16(aligned = true): number {
    if (aligned) this.align(2);
    this.ensure(2);
    const value = this.view.getInt16(this.offset, true);
    this.offset += 2;
    return value;
  }

  u32(aligned = true): number {
    if (aligned) this.align(4);
    this.ensure(4);
    const value = this.view.getUint32(this.offset, true);
    this.offset += 4;
    return value;
  }

  i32(aligned = true): number {
    if (aligned) this.align(4);
    this.ensure(4);
    const value = this.view.getInt32(this.offset, true);
    this.offset += 4;
    return value;
  }

  f32(aligned = true): number {
    if (aligned) this.align(4);
    this.ensure(4);
    const value = this.view.getFloat32(this.offset, true);
    this.offset += 4;
    if (!Number.isFinite(value)) throw new BinParseError('BIN contains a non-finite number.');
    return value;
  }

  raw(length: number): Uint8Array {
    this.ensure(length);
    const value = this.bytes.subarray(this.offset, this.offset + length);
    this.offset += length;
    return value;
  }

  modernString(maxLength = 32 * 1024): string {
    const storedLength = this.u32();
    if (storedLength < 1 || storedLength - 1 > maxLength) {
      throw new BinParseError(`Invalid aligned string length ${storedLength}.`);
    }
    const length = storedLength - 1;
    const value = decodeAscii(this.raw(length));
    this.skip(length % 4 === 0 ? 4 : 4 - (length % 4));
    return value;
  }

  legacyString(maxLength = 32 * 1024): string {
    const length = this.u32(false);
    if (length === 0) return '';
    if (length > maxLength) throw new BinParseError(`Invalid legacy string length ${length}.`);
    const raw = this.raw(length);
    const end = raw.length && raw[raw.length - 1] === 0 ? raw.length - 1 : raw.length;
    const value = decodeAscii(raw.subarray(0, end));
    const padding = (4 - (length % 4)) % 4;
    if (padding) this.skip(padding);
    return value;
  }

  peekU32(): number {
    const start = this.offset;
    const value = this.u32(false);
    this.offset = start;
    return value;
  }
}

function decodeAscii(value: Uint8Array): string {
  const text = textDecoder.decode(value);
  if ([...text].some((character) => {
    const code = character.charCodeAt(0);
    return code !== 9 && code !== 10 && code !== 13 && (code < 32 || code > 126);
  })) {
    throw new BinParseError('BIN contains a non-printable string.');
  }
  return text;
}

function count(reader: Reader, maximum: number, label: string, allowZero = true): number {
  const value = reader.u32();
  if ((!allowZero && value === 0) || value > maximum) {
    throw new BinParseError(`Unreasonable ${label}: ${value}.`);
  }
  return value;
}

function immediate8(reader: Reader): number {
  const value = reader.i8();
  if (value < -1 || value > 1) throw new BinParseError(`Invalid immediate value ${value}.`);
  return value;
}

function immediate32(value: number): number {
  const raw = value & 0xff;
  const signed = raw < 0x80 ? raw : raw - 0x100;
  if (signed < -1 || signed > 1) throw new BinParseError(`Invalid immediate value ${signed}.`);
  return signed;
}

function dataValue(reader: Reader): InterpolatedNumber {
  return { immediate: immediate8(reader), value: reader.f32() };
}

function dataPoint(reader: Reader): InterpolatedPoint {
  return { immediate: immediate8(reader), x: reader.f32(), y: reader.f32() };
}

function dataRect(reader: Reader): InterpolatedPoint & { w: number; h: number } {
  return {
    immediate: immediate8(reader),
    x: reader.f32(),
    y: reader.f32(),
    w: reader.f32(),
    h: reader.f32(),
  };
}

function dataSprite(reader: Reader): InterpolatedSprite {
  return { immediate: immediate8(reader), string: reader.modernString() };
}

function readSource(reader: Reader): AnimationSource {
  return {
    src: reader.modernString(),
    id: reader.u16(),
    width: reader.u16(),
    height: reader.u16(),
  };
}

function assertTrailingPadding(reader: Reader): void {
  if (reader.remaining <= 0) return;
  const tail = reader.raw(reader.remaining);
  if (tail.some((value) => value !== 0)) {
    throw new BinParseError(`Unexpected trailing data at 0x${(reader.offset - tail.length).toString(16)}.`);
  }
}

function normalizeParent(value: number | string): number {
  if (typeof value === 'number' && Number.isInteger(value)) return value;
  const prefix = String(value).trim().split(':', 1)[0];
  const parsed = Number.parseInt(prefix ?? '', 10);
  return Number.isFinite(parsed) ? parsed : -1;
}

export function normalizeAnimationDocument(document: AnimationDocument): AnimationDocument {
  for (const animation of document.anims) {
    for (const layer of animation.layers) {
      layer.parent = normalizeParent(layer.parent as number | string);
      if (!Number.isFinite(layer.anchor_x) || !Number.isFinite(layer.anchor_y)) {
        const anchored = layer.frames.find((frame) => frame.anchor);
        layer.anchor_x = Number(anchored?.anchor?.x ?? 0);
        layer.anchor_y = Number(anchored?.anchor?.y ?? 0);
      }
    }
  }
  return document;
}

function validateDocument(document: AnimationDocument): void {
  if (!document.sources.length || !document.anims.length) {
    throw new BinParseError('BIN does not contain animation sources and tracks.');
  }
  for (const source of document.sources) {
    if (typeof source.src !== 'string' || !Number.isInteger(source.id)) {
      throw new BinParseError('BIN contains invalid source metadata.');
    }
    if ((source.width ?? 0) < 0 || (source.width ?? 0) > 16384
      || (source.height ?? 0) < 0 || (source.height ?? 0) > 16384) {
      throw new BinParseError(`BIN contains invalid dimensions for source '${source.src}'.`);
    }
  }
  for (const animation of document.anims) {
    if (!animation.name || animation.width < 0 || animation.width > 16384 || animation.height < 0 || animation.height > 16384) {
      throw new BinParseError('BIN contains invalid animation metadata.');
    }
    for (const layer of animation.layers) {
      if (!layer.name || layer.frames.length > MAX_FRAMES) {
        throw new BinParseError('BIN contains invalid layer metadata.');
      }
    }
  }
}

function rev6Frame(reader: Reader): AnimationFrame {
  return {
    time: reader.f32(),
    pos: dataPoint(reader),
    scale: dataPoint(reader),
    rotation: dataValue(reader),
    opacity: dataValue(reader),
    sprite: dataSprite(reader),
    rgb: {
      immediate: immediate8(reader),
      red: reader.u8(),
      green: reader.u8(),
      blue: reader.u8(),
    },
  };
}

function rev6Layer(reader: Reader): AnimationLayer {
  const name = reader.modernString();
  const type = reader.i32();
  const blend = reader.u32();
  if (blend > 7) throw new BinParseError(`Invalid Rev 6 blend mode ${blend}.`);
  const parent = reader.i16();
  const id = reader.i16();
  const src = reader.i16();
  const width = reader.u16();
  const height = reader.u16();
  const anchor_x = reader.f32();
  const anchor_y = reader.f32();
  const unk = reader.modernString();
  const frameCount = count(reader, MAX_FRAMES, `frame count for '${name}'`);
  const frames = Array.from({ length: frameCount }, () => rev6Frame(reader));
  return { name, type, blend, parent, id, src, width, height, anchor_x, anchor_y, unk, frames };
}

function maybeReadRev6Clones(reader: Reader): AnimationCloneLayer[] {
  const start = reader.offset;
  try {
    const cloneCount = reader.u32();
    if (cloneCount < 1 || cloneCount > 512 || reader.remaining < cloneCount * 16) throw new Error();
    const clones: AnimationCloneLayer[] = [];
    for (let index = 0; index < cloneCount; index += 1) {
      const newLayer = reader.modernString(512);
      const sourceLayer = reader.modernString(512);
      const referenceLayer = reader.modernString(512);
      const variantIndex = reader.u32();
      if (!newLayer || !sourceLayer) throw new Error();
      const signedVariant = variantIndex < 0x80000000 ? variantIndex : variantIndex - 0x100000000;
      clones.push({
        name: newLayer,
        resource: sourceLayer,
        sheet: referenceLayer,
        variant_index: variantIndex,
        new_layer: newLayer,
        source_layer: sourceLayer,
        reference_layer: referenceLayer,
        insert_mode: signedVariant < 0 ? -1 : 1,
      });
    }
    return clones;
  } catch {
    reader.seek(start);
    return [];
  }
}

function rev6Animation(reader: Reader): AnimationTrack {
  const name = reader.modernString();
  const width = reader.u16();
  const height = reader.u16();
  const loop_offset = reader.f32();
  const centered = reader.u32();
  const layerCount = count(reader, 700, `layer count for '${name}'`);
  const layers = Array.from({ length: layerCount }, () => rev6Layer(reader));
  const clone_layers = maybeReadRev6Clones(reader);
  return { name, width, height, loop_offset, centered, layers, clone_layers };
}

export function parseRev6(buffer: ArrayBuffer): AnimationDocument {
  const reader = new Reader(buffer);
  const sourceCount = count(reader, MAX_SOURCES, 'source count', false);
  const sources = Array.from({ length: sourceCount }, () => readSource(reader));
  const animationCount = count(reader, 512, 'animation count', false);
  const anims = Array.from({ length: animationCount }, () => rev6Animation(reader));
  assertTrailingPadding(reader);
  return { rev: 6, blend_version: 2, source_format: 'rev6', source_revision: 6, sources, anims };
}

function rev4Frame(reader: Reader): AnimationFrame {
  return {
    time: reader.f32(),
    mask: dataRect(reader),
    anchor: dataPoint(reader),
    pos: dataPoint(reader),
    scale: dataPoint(reader),
    rotation: dataValue(reader),
    opacity: dataValue(reader),
    sprite: dataSprite(reader),
  };
}

function rev4Layer(reader: Reader): AnimationLayer {
  const name = reader.modernString();
  const type = reader.i32();
  const blend = reader.u32();
  if (blend > 7) throw new BinParseError(`Invalid Rev 4 blend mode ${blend}.`);
  const parent = reader.i16();
  const id = reader.u16();
  const src = reader.u16();
  const rgb = { red: reader.u16(), green: reader.u16(), blue: reader.u16() };
  const frameCount = count(reader, MAX_FRAMES, `frame count for '${name}'`);
  const frames = Array.from({ length: frameCount }, () => rev4Frame(reader));
  const anchor = frames.find((frame) => frame.anchor)?.anchor;
  return {
    name,
    type,
    blend,
    parent,
    id,
    src,
    anchor_x: Number(anchor?.x ?? 0),
    anchor_y: Number(anchor?.y ?? 0),
    frames,
    rgb,
  };
}

function modernAnimation(reader: Reader, layerReader: (reader: Reader) => AnimationLayer): AnimationTrack {
  const name = reader.modernString();
  const width = reader.u16();
  const height = reader.u16();
  const loop_offset = reader.f32();
  const centered = reader.u32();
  const layerCount = count(reader, MAX_LAYERS, `layer count for '${name}'`);
  const layers = Array.from({ length: layerCount }, () => layerReader(reader));
  return { name, width, height, loop_offset, centered, layers };
}

export function parseRev4(buffer: ArrayBuffer): AnimationDocument {
  const reader = new Reader(buffer);
  const sourceCount = count(reader, MAX_SOURCES, 'source count', false);
  const sources = Array.from({ length: sourceCount }, () => readSource(reader));
  const animationCount = count(reader, MAX_ANIMATIONS, 'animation count', false);
  const anims = Array.from({ length: animationCount }, () => modernAnimation(reader, rev4Layer));
  assertTrailingPadding(reader);
  return { rev: 4, source_format: 'rev4', source_revision: 4, sources, anims };
}

function dataFont(reader: Reader): InterpolatedFont {
  return {
    immediate: immediate8(reader),
    size: reader.i32(),
    align: reader.u32(),
    r: reader.u8(),
    g: reader.u8(),
    b: reader.u8(),
    type: reader.u8(),
    name: reader.modernString(),
    width: reader.f32(),
    height: reader.f32(),
  };
}

function rev2Frame(reader: Reader): AnimationFrame {
  const frame: AnimationFrame = {
    time: reader.f32(),
    mask: dataRect(reader),
    anchor: dataPoint(reader),
    pos: dataPoint(reader),
    scale: dataPoint(reader),
    rotation: dataValue(reader),
    opacity: dataValue(reader),
  };
  frame.cell = dataPoint(reader);
  frame.index = dataValue(reader);
  frame.depth = dataValue(reader);
  frame.sprite = dataSprite(reader);
  frame.font = dataFont(reader);
  return frame;
}

function rev2Layer(reader: Reader): AnimationLayer {
  const name = reader.modernString();
  const parentReference = reader.modernString();
  const id = reader.u32();
  const type = reader.i32();
  const src = reader.u32();
  const blend = reader.u32();
  if (blend > 7) throw new BinParseError(`Invalid Rev 2 blend mode ${blend}.`);
  const frameCount = count(reader, MAX_FRAMES, `frame count for '${name}'`);
  const frames = Array.from({ length: frameCount }, () => rev2Frame(reader));
  const anchor = frames.find((frame) => frame.anchor)?.anchor;
  return {
    name,
    type,
    blend,
    parent: normalizeParent(parentReference),
    id,
    src,
    anchor_x: Number(anchor?.x ?? 0),
    anchor_y: Number(anchor?.y ?? 0),
    frames,
  };
}

export function parseRev2(buffer: ArrayBuffer): AnimationDocument {
  const reader = new Reader(buffer);
  const sourceCount = count(reader, MAX_SOURCES, 'source count', false);
  const sources = Array.from({ length: sourceCount }, () => readSource(reader));
  const animationCount = count(reader, MAX_ANIMATIONS, 'animation count', false);
  const anims = Array.from({ length: animationCount }, () => modernAnimation(reader, rev2Layer));
  assertTrailingPadding(reader);
  return { rev: 2, source_format: 'rev2', source_revision: 2, sources, anims };
}

function decodeImmediateLoose(value: number): number {
  const raw = value & 0xff;
  return raw < 0x80 ? raw : raw - 0x100;
}

function muppetsFrame(reader: Reader, index: number, previousTime: number): {
  frame: AnimationFrame;
  anchor: { x: number; y: number };
} {
  const block = reader.raw(0x4c);
  const view = new DataView(block.buffer, block.byteOffset, block.byteLength);
  const rawTime = view.getFloat32(0, true);
  const invalidTime = !Number.isFinite(rawTime)
    || Math.abs(rawTime) > 1_000_000
    || (index > 0 && rawTime + 0.0001 < previousTime);
  const time = invalidTime ? previousTime + (index > 0 ? 1 / 30 : 0) : rawTime;
  const immediate = (offset: number): number => decodeImmediateLoose(view.getUint32(offset, true));
  const finiteFloat = (offset: number): number => {
    const value = view.getFloat32(offset, true);
    if (!Number.isFinite(value)) throw new BinParseError('Muppets BIN contains a non-finite transform.');
    return value;
  };
  const spriteImmediate = decodeImmediateLoose(reader.u32(false));
  const sprite = reader.legacyString(0x2000);
  return {
    anchor: { x: finiteFloat(0x1c), y: finiteFloat(0x20) },
    frame: {
      time,
      pos: { immediate: immediate(0x24), x: finiteFloat(0x28), y: finiteFloat(0x2c) },
      scale: { immediate: immediate(0x30), x: finiteFloat(0x34), y: finiteFloat(0x38) },
      rotation: { immediate: immediate(0x3c), value: finiteFloat(0x40) },
      opacity: { immediate: immediate(0x44), value: finiteFloat(0x48) },
      sprite: { immediate: spriteImmediate, string: sprite },
      rgb: { immediate: -1, red: 255, green: 255, blue: 255 },
    },
  };
}

function muppetsLayer(reader: Reader): { layer: AnimationLayer; parentId: number } {
  const name = reader.legacyString(0x2000);
  const parentReference = reader.legacyString(0x2000);
  const id = reader.u32(false);
  const type = reader.u32(false);
  const src = reader.u32(false);
  const rawBlend = reader.u32(false);
  const frameCount = countLegacy(reader, MAX_FRAMES, `frame count for '${name}'`);
  const frames: AnimationFrame[] = [];
  let anchor = { x: 0, y: 0 };
  let previousTime = 0;
  for (let index = 0; index < frameCount; index += 1) {
    const parsed = muppetsFrame(reader, index, previousTime);
    if (index === 0) anchor = parsed.anchor;
    previousTime = parsed.frame.time;
    frames.push(parsed.frame);
  }
  const prefix = parentReference.includes(':') ? parentReference.split(':', 1)[0] : undefined;
  const parsedParent = prefix === undefined ? -1 : Number.parseInt(prefix, 10);
  return {
    parentId: Number.isFinite(parsedParent) ? parsedParent : -1,
    layer: {
      name,
      type,
      blend: rawBlend < 8 ? rawBlend : 0,
      parent: -1,
      id,
      src,
      width: 0,
      height: 0,
      anchor_x: anchor.x,
      anchor_y: anchor.y,
      frames,
    },
  };
}

function muppetsAnimation(reader: Reader): AnimationTrack {
  const name = reader.legacyString(0x2000);
  const packed = reader.u32(false);
  const width = packed & 0xffff;
  const height = packed >>> 16;
  const loop_offset = reader.f32(false);
  const centered = reader.u32(false);
  const layerCount = countLegacy(reader, MAX_LAYERS, `layer count for '${name}'`, false);
  const parsedLayers = Array.from({ length: layerCount }, () => muppetsLayer(reader));
  const idToIndex = new Map(parsedLayers.map(({ layer }, index) => [layer.id, index]));
  for (const parsed of parsedLayers) {
    parsed.layer.parent = parsed.parentId < 0 ? -1 : idToIndex.get(parsed.parentId) ?? -1;
  }
  return {
    name,
    width,
    height,
    loop_offset,
    centered,
    layers: parsedLayers.map(({ layer }) => layer),
    clone_layers: [],
  };
}

export function parseMuppets(buffer: ArrayBuffer): AnimationDocument {
  const reader = new Reader(buffer);
  const version = reader.u32(false);
  if (version !== 1) throw new BinParseError(`Unsupported Muppets container version ${version}.`);
  const sheet = reader.legacyString(0x2000);
  reader.u32(false);
  reader.u32(false);
  const animationCount = countLegacy(reader, MAX_ANIMATIONS, 'Muppets animation count', false);
  const anims = Array.from({ length: animationCount }, () => muppetsAnimation(reader));
  assertTrailingPadding(reader);
  return {
    rev: 6,
    blend_version: 2,
    source_format: 'muppets',
    source_revision: 2,
    legacy_format: true,
    sources: [{ src: sheet, id: 0, width: 0, height: 0 }],
    anims,
  };
}

function floatAt(block: Uint8Array, offset: number): number {
  const value = new DataView(block.buffer, block.byteOffset, block.byteLength).getFloat32(offset, true);
  if (!Number.isFinite(value)) throw new BinParseError('Legacy BIN contains a non-finite number.');
  return value;
}

function immediateAt(block: Uint8Array, offset: number): number {
  return immediate32(new DataView(block.buffer, block.byteOffset, block.byteLength).getUint32(offset, true));
}

function legacyFrame(block: Uint8Array, sprite: string, spriteImmediate: number): AnimationFrame {
  return {
    time: floatAt(block, 0),
    pos: { immediate: immediateAt(block, 0x24), x: floatAt(block, 0x28), y: floatAt(block, 0x2c) },
    scale: { immediate: immediateAt(block, 0x30), x: floatAt(block, 0x34), y: floatAt(block, 0x38) },
    rotation: { immediate: immediateAt(block, 0x3c), value: floatAt(block, 0x40) },
    opacity: { immediate: immediateAt(block, 0x44), value: floatAt(block, 0x48) },
    sprite: { immediate: spriteImmediate, string: sprite },
    rgb: { immediate: -1, red: 255, green: 255, blue: 255 },
  };
}

function legacyLayerBase(
  name: string,
  parentReference: string,
  meta: Uint8Array,
  anchor: { x: number; y: number },
  frames: AnimationFrame[],
): AnimationLayer {
  const view = new DataView(meta.buffer, meta.byteOffset, meta.byteLength);
  const blend = view.getUint32(12, true);
  return {
    name,
    parent: normalizeParent(parentReference),
    id: view.getUint32(0, true),
    type: view.getUint32(4, true),
    src: view.getUint32(8, true),
    blend: blend < 8 ? blend : 0,
    width: 0,
    height: 0,
    anchor_x: anchor.x,
    anchor_y: anchor.y,
    unk: '',
    frames,
  };
}

function legacyAnimation(reader: Reader, layerReader: (reader: Reader) => AnimationLayer): AnimationTrack {
  const name = reader.legacyString();
  const packed = reader.u32(false);
  const width = packed & 0xffff;
  const height = packed >>> 16;
  const loop_offset = reader.f32(false);
  const centered = reader.u32(false);
  const layerCount = countLegacy(reader, MAX_LAYERS, `layer count for '${name}'`);
  const layers = Array.from({ length: layerCount }, () => layerReader(reader));
  return { name, width, height, loop_offset, centered, layers, clone_layers: [] };
}

function countLegacy(reader: Reader, maximum: number, label: string, allowZero = true): number {
  const value = reader.u32(false);
  if ((!allowZero && value === 0) || value > maximum) throw new BinParseError(`Unreasonable ${label}: ${value}.`);
  return value;
}

function rev1Layer(reader: Reader): AnimationLayer {
  const name = reader.legacyString();
  const parent = reader.legacyString();
  const meta = reader.raw(0x10);
  const frameCount = countLegacy(reader, MAX_FRAMES, `frame count for '${name}'`);
  const frames: AnimationFrame[] = [];
  let currentSprite = '';
  let anchor = { x: 0, y: 0 };
  for (let index = 0; index < frameCount; index += 1) {
    const block = reader.raw(0x68);
    reader.u32(false);
    const sprite = reader.legacyString(0x1000);
    reader.f32(false);
    reader.f32(false);
    reader.f32(false);
    reader.legacyString(0x1000);
    if (index === 0) anchor = { x: floatAt(block, 0x1c), y: floatAt(block, 0x20) };
    if (sprite) currentSprite = sprite;
    frames.push(legacyFrame(block, currentSprite, sprite ? 0 : -1));
  }
  return legacyLayerBase(name, parent, meta, anchor, frames);
}

export function parseRev1(buffer: ArrayBuffer): AnimationDocument {
  const reader = new Reader(buffer);
  const version = reader.u32(false);
  if (version !== 1) throw new BinParseError(`Unsupported Rev 1 container version ${version}.`);
  const sheet = reader.legacyString();
  reader.u32(false);
  reader.u32(false);
  const animationCount = countLegacy(reader, MAX_ANIMATIONS, 'animation count', false);
  const anims = Array.from({ length: animationCount }, () => legacyAnimation(reader, rev1Layer));
  assertTrailingPadding(reader);
  return {
    rev: 6,
    blend_version: 2,
    source_format: 'rev1_launch',
    source_revision: 1,
    legacy_format: true,
    sources: [{ src: sheet, id: 0, width: 0, height: 0 }],
    anims,
  };
}

export const parseRev1Launch = parseRev1;

function legacyMobileLayer(reader: Reader): AnimationLayer {
  const name = reader.legacyString();
  const parent = reader.legacyString();
  const meta = reader.raw(0x10);
  const frameCount = countLegacy(reader, MAX_FRAMES, `frame count for '${name}'`);
  const frames: AnimationFrame[] = [];
  let anchor = { x: 0, y: 0 };
  let extraBlockPresent: boolean | undefined;

  for (let index = 0; index < frameCount; index += 1) {
    const block = reader.raw(0x68);
    reader.u32(false); // post flags
    const sprite = reader.legacyString(0x1000);
    reader.skip(0x10);

    if (extraBlockPresent === undefined) {
      if (reader.remaining < 4) throw new BinParseError(`Truncated legacy-mobile layer '${name}'.`);
      const extraLength = reader.peekU32();
      extraBlockPresent = extraLength <= 0x4000 && extraLength <= reader.remaining;
    }
    if (extraBlockPresent) {
      const extraLength = reader.u32(false);
      if (extraLength > 0x4000) {
        throw new BinParseError(`Invalid legacy-mobile frame payload length ${extraLength}.`);
      }
      reader.raw(extraLength);
      const padding = (4 - (extraLength % 4)) % 4;
      if (padding) reader.skip(padding);
      reader.skip(8);
    }

    if (index === 0) anchor = { x: floatAt(block, 0x1c), y: floatAt(block, 0x20) };
    frames.push(legacyFrame(block, sprite, sprite ? 0 : -1));
  }

  return legacyLayerBase(name, parent, meta, anchor, frames);
}

export function parseLegacyMobile(buffer: ArrayBuffer): AnimationDocument {
  const reader = new Reader(buffer);
  const version = reader.u32(false);
  if (version !== 1) throw new BinParseError(`Unsupported legacy-mobile container version ${version}.`);
  const sheet = reader.legacyString();
  reader.u32(false);
  reader.u32(false);
  const animationCount = countLegacy(reader, MAX_ANIMATIONS, 'legacy-mobile animation count', false);
  const anims = Array.from(
    { length: animationCount },
    () => legacyAnimation(reader, legacyMobileLayer),
  );
  assertTrailingPadding(reader);
  return {
    rev: 6,
    blend_version: 2,
    source_format: 'legacy',
    source_revision: 1,
    legacy_format: true,
    sources: [{ src: sheet, id: 0, width: 0, height: 0 }],
    anims,
  };
}

function rev3Layer(reader: Reader): AnimationLayer {
  const name = reader.legacyString(0x2000);
  const parent = reader.legacyString(0x2000);
  const meta = reader.raw(0x10);
  reader.u32(false);
  reader.u32(false);
  const frameCount = countLegacy(reader, MAX_FRAMES, `frame count for '${name}'`);
  const frames: AnimationFrame[] = [];
  let currentSprite = '';
  let anchor = { x: 0, y: 0 };
  for (let index = 0; index < frameCount; index += 1) {
    const block = reader.raw(0x68);
    reader.u32(false);
    const sprite = reader.legacyString(0x2000);
    reader.u32(false);
    reader.u32(false);
    reader.u32(false);
    reader.legacyString(0x2000);
    for (let tail = 0; tail < 4; tail += 1) reader.u32(false);
    if (index === 0) anchor = { x: floatAt(block, 0x1c), y: floatAt(block, 0x20) };
    if (sprite) currentSprite = sprite;
    frames.push(legacyFrame(block, currentSprite, sprite ? 0 : -1));
  }
  return legacyLayerBase(name, parent, meta, anchor, frames);
}

export function parseRev3(buffer: ArrayBuffer): AnimationDocument {
  const reader = new Reader(buffer);
  const sourceCount = countLegacy(reader, MAX_SOURCES, 'source count', false);
  const sources = Array.from({ length: sourceCount }, (_, index): AnimationSource => {
    const src = reader.legacyString(0x2000);
    const first = reader.u32(false);
    reader.u32(false);
    return { src, id: first || index, width: 0, height: 0 };
  });
  const animationCount = countLegacy(reader, MAX_ANIMATIONS, 'animation count', false);
  const anims = Array.from({ length: animationCount }, () => legacyAnimation(reader, rev3Layer));
  for (const animation of anims) {
    const idToIndex = new Map(animation.layers.map((layer, index) => [layer.id, index]));
    for (const layer of animation.layers) {
      if (layer.parent >= 0) layer.parent = idToIndex.get(layer.parent) ?? -1;
    }
  }
  assertTrailingPadding(reader);
  return {
    rev: 6,
    blend_version: 2,
    source_format: 'choir',
    source_revision: 3,
    legacy_format: true,
    sources,
    anims,
  };
}

interface LayerProbe {
  score: number;
  hasPrefix: boolean;
}

function peekModernString(reader: Reader, offset: number, maxLength: number): { start: number; text: string; end: number } | undefined {
  const start = (offset + 3) & ~3;
  if (start + 4 > reader.size) return undefined;
  const length = reader.view.getUint32(start, true);
  if (length < 1 || length > maxLength || start + 4 + length - 1 > reader.size) return undefined;
  try {
    const text = decodeAscii(reader.bytes.subarray(start + 4, start + 4 + length - 1));
    if (!text) return undefined;
    const rawLength = length - 1;
    const end = start + 4 + rawLength + (rawLength % 4 === 0 ? 4 : 4 - (rawLength % 4));
    return { start, text, end };
  } catch {
    return undefined;
  }
}

function peekRev5Layer(reader: Reader, offset: number): LayerProbe | undefined {
  let best: LayerProbe | undefined;
  for (const hasPrefix of [false, true]) {
    const value = peekModernString(reader, offset + (hasPrefix ? 8 : 0), 128);
    if (!value || !/[A-Za-z]/.test(value.text) || value.end + 32 > reader.size) continue;
    const header = (value.end + 3) & ~3;
    const type = reader.view.getInt32(header, true);
    const blend = reader.view.getUint32(header + 4, true);
    const parent = reader.view.getInt16(header + 8, true);
    const frameCount = reader.view.getUint32(header + 28, true);
    if (type < -128 || type > 128 || blend > 128 || frameCount > 10000) continue;
    let score = hasPrefix ? 1 : 0;
    if (blend <= 8) score += 2;
    if (frameCount <= 500) score += 2;
    if (parent >= -1024 && parent <= 1024) score += 1;
    if (value.text.includes('_') || value.text.includes(' ')) score += 1;
    if (!best || score > best.score) best = { score, hasPrefix };
  }
  return best;
}

function findRev5Layer(reader: Reader, offset: number): { offset: number; probe: LayerProbe } | undefined {
  const direct = peekRev5Layer(reader, offset);
  if (direct) return { offset, probe: direct };
  const limit = Math.min(reader.size - 4, offset + 131072);
  for (let scan = (offset + 3) & ~3; scan < limit; scan += 4) {
    const probe = peekRev5Layer(reader, scan);
    if (probe && probe.score >= 2) return { offset: scan, probe };
  }
  return undefined;
}

function peekRev5Animation(reader: Reader, offset: number): number | undefined {
  const value = peekModernString(reader, offset, 128);
  if (!value || !/[A-Za-z]/.test(value.text)) return undefined;
  const header = (value.end + 1) & ~1;
  if (header + 16 > reader.size) return undefined;
  const width = reader.view.getUint16(header, true);
  const height = reader.view.getUint16(header + 2, true);
  const aligned = (header + 7) & ~3;
  const loop = reader.view.getFloat32(aligned, true);
  const centered = reader.view.getUint32(aligned + 4, true);
  const layers = reader.view.getUint32(aligned + 8, true);
  if (!width || !height || width > 8192 || height > 8192 || !Number.isFinite(loop) || loop < -1000 || loop > 10000 || centered > 16 || !layers || layers > 700) return undefined;
  return value.start;
}

function findRev5Animation(reader: Reader, offset: number): number | undefined {
  const direct = peekRev5Animation(reader, offset);
  if (direct !== undefined) return direct;
  const limit = Math.min(reader.size - 4, offset + 262144);
  for (let scan = (offset + 3) & ~3; scan < limit; scan += 4) {
    const candidate = peekRev5Animation(reader, scan);
    if (candidate !== undefined) return candidate;
  }
  return undefined;
}

function rev5Frame(reader: Reader): { frame: AnimationFrame; anchor: { x: number; y: number } } {
  const time = reader.f32();
  reader.skip(0x18);
  const anchor = { x: reader.f32(), y: reader.f32() };
  const point32 = (): InterpolatedPoint => ({ immediate: immediate32(reader.u32()), x: reader.f32(), y: reader.f32() });
  const value32 = (): InterpolatedNumber => ({ immediate: immediate32(reader.u32()), value: reader.f32() });
  const pos = point32();
  const scale = point32();
  const rotation = value32();
  const opacity = value32();
  const spriteImmediate = immediate32(reader.u32());
  const sprite = reader.modernString();
  return {
    anchor,
    frame: {
      time,
      pos,
      scale,
      rotation,
      opacity,
      sprite: { immediate: spriteImmediate, string: sprite },
      rgb: { immediate: -1, red: 255, green: 255, blue: 255 },
    },
  };
}

function rev5Layer(reader: Reader, index: number): AnimationLayer {
  if (index > 0) {
    const found = findRev5Layer(reader, reader.offset);
    if (!found) throw new BinParseError(`Unable to locate Rev 5 layer near 0x${reader.offset.toString(16)}.`);
    reader.seek(found.offset);
    if (found.probe.hasPrefix) {
      reader.u32();
      reader.u32();
    }
  }
  const name = reader.modernString();
  const type = reader.i32();
  const blend = reader.u32();
  const parent = reader.i16();
  const id = reader.u16();
  const src = reader.u16();
  const rgb = { red: Math.min(255, reader.u16()), green: Math.min(255, reader.u16()), blue: Math.min(255, reader.u16()) };
  const markerA = reader.u32();
  const markerB = reader.u32();
  if (markerA !== 1 || markerB !== 0) throw new BinParseError(`Invalid Rev 5 layer markers for '${name}'.`);
  const frameCount = count(reader, 10000, `frame count for '${name}'`);
  const frames: AnimationFrame[] = [];
  let anchor_x = 0;
  let anchor_y = 0;
  let haveAnchor = false;
  for (let frameIndex = 0; frameIndex < frameCount; frameIndex += 1) {
    const parsed = rev5Frame(reader);
    if (!haveAnchor && (parsed.anchor.x !== 0 || parsed.anchor.y !== 0)) {
      anchor_x = parsed.anchor.x;
      anchor_y = parsed.anchor.y;
      haveAnchor = true;
    }
    frames.push(parsed.frame);
  }
  return {
    name,
    type,
    blend,
    parent,
    id,
    src,
    width: 0,
    height: 0,
    anchor_x,
    anchor_y,
    unk: '',
    frames,
    rgb,
  };
}

function rev5Animation(reader: Reader, index: number): AnimationTrack | undefined {
  if (index > 0) {
    const found = findRev5Animation(reader, reader.offset);
    if (found === undefined) return undefined;
    reader.seek(found);
  }
  const name = reader.modernString();
  const width = reader.u16();
  const height = reader.u16();
  const loop_offset = reader.f32();
  const centered = reader.u32();
  const layerCount = count(reader, 700, `layer count for '${name}'`, false);
  const layers = Array.from({ length: layerCount }, (_, layerIndex) => rev5Layer(reader, layerIndex));
  return { name, width, height, loop_offset, centered, layers, clone_layers: [] };
}

export function parseRev5(buffer: ArrayBuffer): AnimationDocument {
  const reader = new Reader(buffer);
  const sourceCount = count(reader, MAX_SOURCES, 'source count', false);
  const sources = Array.from({ length: sourceCount }, () => readSource(reader));
  const animationCount = count(reader, 512, 'animation count', false);
  const anims: AnimationTrack[] = [];
  for (let index = 0; index < animationCount; index += 1) {
    const animation = rev5Animation(reader, index);
    if (!animation) break;
    anims.push(animation);
  }
  if (!anims.length) throw new BinParseError('No Rev 5 animations could be parsed.');
  return { rev: 6, blend_version: 2, source_format: 'rev5', source_revision: 5, sources, anims };
}

interface ParserCandidate {
  format: BinFormat;
  label: string;
  revision: BinRevision;
  parse: (buffer: ArrayBuffer) => AnimationDocument;
}

const formatParsers: Record<BinFormat, ParserCandidate> = {
  rev1_launch: { format: 'rev1_launch', label: 'Rev 1', revision: 1, parse: parseRev1Launch },
  legacy: { format: 'legacy', label: 'Rev 1 (Legacy Mobile)', revision: 1, parse: parseLegacyMobile },
  rev2: { format: 'rev2', label: 'Rev 2', revision: 2, parse: parseRev2 },
  muppets: { format: 'muppets', label: 'Rev 2 (My Muppets Show)', revision: 2, parse: parseMuppets },
  choir: { format: 'choir', label: 'Rev 3 (Monster Choir)', revision: 3, parse: parseRev3 },
  rev4: { format: 'rev4', label: 'Rev 4', revision: 4, parse: parseRev4 },
  rev5: { format: 'rev5', label: 'Rev 5', revision: 5, parse: parseRev5 },
  rev6: { format: 'rev6', label: 'Rev 6', revision: 6, parse: parseRev6 },
};

export function binFormatLabel(format: BinFormat): string {
  return formatParsers[format].label;
}

function hintedRevision(path: string): BinRevision | undefined {
  const match = /\brev(?:ision)?[\s_-]*([1-6])\b/i.exec(path);
  if (match?.[1]) return Number(match[1]) as BinRevision;
  if (/muppet(?:s|_)/i.test(path)) return 2;
  if (/monster[\s_-]*choir/i.test(path)) return 3;
  if (/legacy[\s_-]*mobile|rev1[\s_-]*legacy|oldest\.app|monsters102\.app/i.test(path)) return 1;
  return undefined;
}

function revisionParsers(revision: BinRevision, path: string): ParserCandidate[] {
  if (revision === 1) {
    const launch = formatParsers.rev1_launch;
    const legacy = formatParsers.legacy;
    return /legacy[\s_-]*mobile|rev1[\s_-]*legacy/i.test(path) ? [legacy, launch] : [launch, legacy];
  }
  if (revision === 2) {
    const standard = formatParsers.rev2;
    const muppets = formatParsers.muppets;
    return /muppet(?:s|_)/i.test(path) ? [muppets, standard] : [standard, muppets];
  }
  return [{
    3: formatParsers.choir,
    4: formatParsers.rev4,
    5: formatParsers.rev5,
    6: formatParsers.rev6,
  }[revision]];
}

export function parseAnimationBin(buffer: ArrayBuffer, mode: BinRevisionMode = 'auto', path = ''): ParsedBin {
  if (buffer.byteLength < 24) throw new BinParseError('BIN is too small to contain an animation.');
  const revisionOrder: BinRevision[] = mode === 'auto'
    ? ([hintedRevision(path), 6, 5, 4, 3, 2, 1].filter(
        (value, index, values): value is BinRevision => Boolean(value) && values.indexOf(value) === index,
      ))
    : [];
  const candidates = mode === 'auto'
    ? revisionOrder.flatMap((revision) => revisionParsers(revision, path))
    : typeof mode === 'number'
      ? revisionParsers(mode, path)
      : [formatParsers[mode]];
  const failures: string[] = [];
  for (const candidate of candidates) {
    try {
      const document = normalizeAnimationDocument(candidate.parse(buffer.slice(0)));
      validateDocument(document);
      return { document, revision: candidate.revision, format: candidate.format };
    } catch (error) {
      failures.push(`${candidate.label}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  if (mode !== 'auto') {
    const label = typeof mode === 'number' ? `Rev ${mode}` : binFormatLabel(mode);
    throw new BinParseError(`This animation BIN does not match ${label}. Choose Automatic or select the correct revision.`);
  }
  const detail = failures.slice(0, 3).join(' ');
  throw new BinParseError(`Unable to parse animation BIN. ${detail ?? ''}`.trim());
}
