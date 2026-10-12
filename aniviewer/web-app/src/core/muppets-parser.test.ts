import { describe, expect, test } from 'vitest';
import { parseAnimationBin } from './bin-parser';

class Writer {
  private readonly bytes: number[] = [];

  u32(value: number): void {
    const data = new ArrayBuffer(4);
    new DataView(data).setUint32(0, value, true);
    this.raw(new Uint8Array(data));
  }

  f32(value: number): void {
    const data = new ArrayBuffer(4);
    new DataView(data).setFloat32(0, value, true);
    this.raw(new Uint8Array(data));
  }

  string(value: string): void {
    const encoded = new TextEncoder().encode(value);
    this.u32(encoded.length + 1);
    this.bytes.push(...encoded, 0);
    while (this.bytes.length % 4) this.bytes.push(0);
  }

  raw(value: Uint8Array): void {
    this.bytes.push(...value);
  }

  result(): ArrayBuffer {
    return Uint8Array.from(this.bytes).buffer;
  }
}

function frame(writer: Writer, time: number, sprite: string, x: number): void {
  const block = new ArrayBuffer(0x4c);
  const view = new DataView(block);
  view.setFloat32(0, time, true);
  view.setFloat32(0x1c, 2, true);
  view.setFloat32(0x20, 3, true);
  view.setUint32(0x24, 0, true);
  view.setFloat32(0x28, x, true);
  view.setFloat32(0x2c, 20, true);
  view.setUint32(0x30, 0, true);
  view.setFloat32(0x34, 100, true);
  view.setFloat32(0x38, 100, true);
  view.setUint32(0x3c, 0, true);
  view.setFloat32(0x40, 0, true);
  view.setUint32(0x44, 0, true);
  view.setFloat32(0x48, 100, true);
  writer.raw(new Uint8Array(block));
  writer.u32(0);
  writer.string(sprite);
}

function layer(
  writer: Writer,
  name: string,
  parent: string,
  id: number,
  frames: Array<{ time: number; x: number }>,
): void {
  writer.string(name);
  writer.string(parent);
  writer.u32(id);
  writer.u32(1);
  writer.u32(0);
  writer.u32(0);
  writer.u32(frames.length);
  for (const item of frames) frame(writer, item.time, `${name}_sprite`, item.x);
}

function muppetsFixture(): ArrayBuffer {
  const writer = new Writer();
  writer.u32(1);
  writer.string('muppet_test_sheet.xml');
  writer.u32(0);
  writer.u32(0);
  writer.u32(1);
  writer.string('Idle');
  writer.u32(64 | (64 << 16));
  writer.f32(0);
  writer.u32(1);
  writer.u32(2);
  layer(writer, 'root', '', 10, [{ time: 0, x: 10 }, { time: -1, x: 11 }]);
  layer(writer, 'child', '10:root', 20, [{ time: 0, x: 5 }]);
  return writer.result();
}

describe('My Muppets Show BIN parsing', () => {
  test('detects the dedicated Rev 2 layout without relying on its filename', () => {
    const parsed = parseAnimationBin(muppetsFixture(), 'auto', 'generic-animation.bin');
    const animation = parsed.document.anims[0]!;

    expect(parsed.revision).toBe(2);
    expect(parsed.format).toBe('muppets');
    expect(parsed.document).toMatchObject({
      source_format: 'muppets',
      source_revision: 2,
      sources: [{ src: 'muppet_test_sheet.xml', id: 0 }],
    });
    expect(animation).toMatchObject({ name: 'Idle', width: 64, height: 64 });
    expect(animation.layers[0]).toMatchObject({ name: 'root', id: 10, parent: -1, anchor_x: 2, anchor_y: 3 });
    expect(animation.layers[1]).toMatchObject({ name: 'child', id: 20, parent: 0 });
    expect(animation.layers[0]?.frames[1]?.time).toBeCloseTo(1 / 30);
  });

  test('accepts the Muppets layout through the manual Rev 2 option', () => {
    const parsed = parseAnimationBin(muppetsFixture(), 2, 'muppet_test.bin');
    expect(parsed.document.source_format).toBe('muppets');
  });
});
