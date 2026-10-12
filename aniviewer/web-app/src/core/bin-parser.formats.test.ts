import { describe, expect, test } from 'vitest';
import {
  binFormatLabel,
  parseAnimationBin,
  parseLegacyMobile,
  parseRev1Launch,
  parseRev6,
} from './bin-parser';

class Writer {
  private readonly bytes: number[] = [];

  private align(size: number): void {
    while (this.bytes.length % size) this.bytes.push(0);
  }

  raw(value: Uint8Array): void {
    this.bytes.push(...value);
  }

  u8(value: number): void {
    this.bytes.push(value & 0xff);
  }

  i8(value: number): void {
    this.u8(value);
  }

  u16(value: number): void {
    this.align(2);
    const data = new ArrayBuffer(2);
    new DataView(data).setUint16(0, value, true);
    this.raw(new Uint8Array(data));
  }

  i16(value: number): void {
    this.align(2);
    const data = new ArrayBuffer(2);
    new DataView(data).setInt16(0, value, true);
    this.raw(new Uint8Array(data));
  }

  u32(value: number, aligned = true): void {
    if (aligned) this.align(4);
    const data = new ArrayBuffer(4);
    new DataView(data).setUint32(0, value, true);
    this.raw(new Uint8Array(data));
  }

  i32(value: number): void {
    this.align(4);
    const data = new ArrayBuffer(4);
    new DataView(data).setInt32(0, value, true);
    this.raw(new Uint8Array(data));
  }

  f32(value: number, aligned = true): void {
    if (aligned) this.align(4);
    const data = new ArrayBuffer(4);
    new DataView(data).setFloat32(0, value, true);
    this.raw(new Uint8Array(data));
  }

  modernString(value: string): void {
    const encoded = new TextEncoder().encode(value);
    this.u32(encoded.length + 1);
    this.bytes.push(...encoded);
    const padding = encoded.length % 4 === 0 ? 4 : 4 - (encoded.length % 4);
    for (let index = 0; index < padding; index += 1) this.bytes.push(0);
  }

  legacyString(value: string): void {
    const encoded = new TextEncoder().encode(value);
    this.u32(encoded.length + 1, false);
    this.bytes.push(...encoded, 0);
    while (this.bytes.length % 4) this.bytes.push(0);
  }

  result(): ArrayBuffer {
    return Uint8Array.from(this.bytes).buffer;
  }
}

function legacyFrameBlock(): Uint8Array {
  const block = new ArrayBuffer(0x68);
  const view = new DataView(block);
  view.setFloat32(0, 0, true);
  view.setFloat32(0x1c, 2, true);
  view.setFloat32(0x20, 3, true);
  view.setUint32(0x24, 0, true);
  view.setFloat32(0x28, 10, true);
  view.setFloat32(0x2c, 20, true);
  view.setUint32(0x30, 0, true);
  view.setFloat32(0x34, 100, true);
  view.setFloat32(0x38, 100, true);
  view.setUint32(0x3c, 0, true);
  view.setFloat32(0x40, 15, true);
  view.setUint32(0x44, 0, true);
  view.setFloat32(0x48, 100, true);
  return new Uint8Array(block);
}

function rev1Fixture(format: 'launch' | 'legacy'): ArrayBuffer {
  const writer = new Writer();
  writer.u32(1, false);
  writer.legacyString('monster_test_sheet.xml');
  writer.u32(0, false);
  writer.u32(0, false);
  writer.u32(1, false);
  writer.legacyString('Idle');
  writer.u32(64 | (64 << 16), false);
  writer.f32(0, false);
  writer.u32(1, false);
  writer.u32(1, false);
  writer.legacyString('root');
  writer.legacyString('');
  writer.u32(1, false);
  writer.u32(1, false);
  writer.u32(0, false);
  writer.u32(0, false);
  writer.u32(1, false);
  writer.raw(legacyFrameBlock());
  writer.u32(0, false);
  writer.legacyString('root_01');
  if (format === 'launch') {
    writer.f32(1, false);
    writer.f32(1, false);
    writer.f32(1, false);
    writer.legacyString('');
  } else {
    writer.raw(new Uint8Array(0x10));
    writer.u32(4, false);
    writer.raw(Uint8Array.from([1, 2, 3, 4]));
    writer.raw(new Uint8Array(8));
  }
  return writer.result();
}

function rev6Fixture(source: string, includeClone = false): ArrayBuffer {
  const writer = new Writer();
  writer.u32(1);
  writer.modernString(source);
  writer.u16(0);
  writer.u16(0);
  writer.u16(0);
  writer.u32(1);
  writer.modernString('Idle');
  writer.u16(64);
  writer.u16(64);
  writer.f32(0);
  writer.u32(1);
  writer.u32(1);
  writer.modernString('root');
  writer.i32(1);
  writer.u32(0);
  writer.i16(-1);
  writer.i16(0);
  writer.i16(0);
  writer.u16(1);
  writer.u16(1);
  writer.f32(0);
  writer.f32(0);
  writer.modernString('');
  writer.u32(1);
  writer.f32(0);
  writer.i8(0);
  writer.f32(0);
  writer.f32(0);
  writer.i8(0);
  writer.f32(100);
  writer.f32(100);
  writer.i8(0);
  writer.f32(0);
  writer.i8(0);
  writer.f32(100);
  writer.i8(0);
  writer.modernString('root_01');
  writer.i8(-1);
  writer.u8(255);
  writer.u8(255);
  writer.u8(255);
  if (includeClone) {
    writer.u32(1);
    writer.modernString('costume_overlay');
    writer.modernString('root');
    writer.modernString('root');
    writer.u32(0xffffffff);
  }
  return writer.result();
}

describe('BIN format variants', () => {
  test('distinguishes Rev 1 launch and legacy-mobile layouts', () => {
    const launch = parseAnimationBin(rev1Fixture('launch'), 'auto', 'oldest.app/monster_test.bin');
    const legacy = parseAnimationBin(rev1Fixture('legacy'), 'auto', 'rev1_legacy/monster_test.bin');

    expect(launch).toMatchObject({ revision: 1, format: 'rev1_launch' });
    expect(launch.document.source_format).toBe('rev1_launch');
    expect(legacy).toMatchObject({ revision: 1, format: 'legacy' });
    expect(legacy.document.source_format).toBe('legacy');
    expect(legacy.document.anims[0]?.layers[0]?.frames[0]?.sprite?.string).toBe('root_01');
  });

  test('exposes both Rev 1 parsers directly', () => {
    expect(parseRev1Launch(rev1Fixture('launch')).anims).toHaveLength(1);
    expect(parseLegacyMobile(rev1Fixture('legacy')).anims).toHaveLength(1);
  });

  test.each(['', 'delete before export/lightning_strike.avi', 'Solids/reference']) (
    'preserves unresolved source metadata %j without rejecting the BIN',
    (source) => {
      const parsed = parseAnimationBin(rev6Fixture(source), 'auto', 'Rev 6/monster_test.bin');
      expect(parsed).toMatchObject({ revision: 6, format: 'rev6' });
      expect(parsed.document.sources[0]?.src).toBe(source);
      expect(parseRev6(rev6Fixture(source)).sources[0]?.src).toBe(source);
    },
  );

  test('provides desktop-equivalent format labels', () => {
    expect(binFormatLabel('rev1_launch')).toBe('Rev 1');
    expect(binFormatLabel('legacy')).toBe('Rev 1 (Legacy Mobile)');
    expect(binFormatLabel('muppets')).toBe('Rev 2 (My Muppets Show)');
    expect(binFormatLabel('choir')).toBe('Rev 3 (Monster Choir)');
  });

  test('preserves embedded Rev 6 clone-layer metadata', () => {
    const track = parseRev6(rev6Fixture('atlas.xml', true)).anims[0];
    expect(track?.clone_layers).toEqual([{
      name: 'costume_overlay',
      resource: 'root',
      sheet: 'root',
      variant_index: 0xffffffff,
      new_layer: 'costume_overlay',
      source_layer: 'root',
      reference_layer: 'root',
      insert_mode: -1,
    }]);
  });
});
