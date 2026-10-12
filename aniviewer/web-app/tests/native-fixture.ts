import { BlobReader, BlobWriter, TextReader, Uint8ArrayReader, ZipWriter } from '@zip.js/zip.js';

class Writer {
  private readonly bytes: number[] = [];

  private align(size: number): void {
    while (this.bytes.length % size) this.bytes.push(0);
  }

  private append(buffer: ArrayBuffer): void {
    this.bytes.push(...new Uint8Array(buffer));
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
    this.append(data);
  }

  i16(value: number): void {
    this.align(2);
    const data = new ArrayBuffer(2);
    new DataView(data).setInt16(0, value, true);
    this.append(data);
  }

  u32(value: number): void {
    this.align(4);
    const data = new ArrayBuffer(4);
    new DataView(data).setUint32(0, value, true);
    this.append(data);
  }

  i32(value: number): void {
    this.align(4);
    const data = new ArrayBuffer(4);
    new DataView(data).setInt32(0, value, true);
    this.append(data);
  }

  f32(value: number): void {
    this.align(4);
    const data = new ArrayBuffer(4);
    new DataView(data).setFloat32(0, value, true);
    this.append(data);
  }

  string(value: string): void {
    const encoded = new TextEncoder().encode(value);
    this.u32(encoded.length + 1);
    this.bytes.push(...encoded);
    const padding = encoded.length % 4 === 0 ? 4 : 4 - (encoded.length % 4);
    for (let index = 0; index < padding; index += 1) this.bytes.push(0);
  }

  result(): Uint8Array {
    return Uint8Array.from(this.bytes);
  }
}

export function nativeRev6Fixture(): { bin: Uint8Array; xml: string; png: Buffer } {
  const writer = new Writer();
  writer.u32(1);
  writer.string('atlas.xml');
  writer.u16(0);
  writer.u16(1);
  writer.u16(1);
  writer.u32(1);
  writer.string('Idle');
  writer.u16(64);
  writer.u16(64);
  writer.f32(0);
  writer.u32(1);
  writer.u32(1);
  writer.string('sprite');
  writer.i32(1);
  writer.u32(0);
  writer.i16(-1);
  writer.i16(0);
  writer.i16(0);
  writer.u16(1);
  writer.u16(1);
  writer.f32(0);
  writer.f32(0);
  writer.string('');
  writer.u32(1);
  writer.f32(0);
  writer.i8(0);
  writer.f32(32);
  writer.f32(32);
  writer.i8(0);
  writer.f32(3200);
  writer.f32(3200);
  writer.i8(0);
  writer.f32(0);
  writer.i8(0);
  writer.f32(100);
  writer.i8(1);
  writer.string('dot');
  writer.i8(-1);
  writer.u8(255);
  writer.u8(255);
  writer.u8(255);

  return {
    bin: writer.result(),
    xml: '<TextureAtlas imagePath="atlas.png" width="1" height="1"><sprite n="dot" x="0" y="0" w="1" h="1" oW="1" oH="1"/></TextureAtlas>',
    png: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFgAI/ScL9WQAAAABJRU5ErkJggg==', 'base64'),
  };
}

export async function zippedNativeFixture(): Promise<Buffer> {
  const fixture = nativeRev6Fixture();
  const writer = new BlobWriter('application/zip');
  const zip = new ZipWriter(writer);
  await zip.add('data/xml_bin/monster_test.bin', new Uint8ArrayReader(fixture.bin));
  await zip.add('data/xml_resources/atlas.xml', new TextReader(fixture.xml));
  await zip.add('data/gfx/monsters/atlas.png', new BlobReader(new Blob([fixture.png], { type: 'image/png' })));
  const result = await zip.close();
  return Buffer.from(await result.arrayBuffer());
}

export async function zippedDuplicateAtlasFixture(): Promise<Buffer> {
  const fixture = nativeRev6Fixture();
  const writer = new BlobWriter('application/zip');
  const zip = new ZipWriter(writer);
  await zip.add(
    'old-install/data/xml_resources/atlas.xml',
    new TextReader(fixture.xml.replace('atlas.png', 'missing-old-install.png')),
  );
  await zip.add('current-install/data/xml_bin/monster_test.bin', new Uint8ArrayReader(fixture.bin));
  await zip.add('current-install/data/xml_resources/atlas.xml', new TextReader(fixture.xml));
  await zip.add(
    'current-install/data/gfx/monsters/atlas.png',
    new BlobReader(new Blob([fixture.png], { type: 'image/png' })),
  );
  const result = await zip.close();
  return Buffer.from(await result.arrayBuffer());
}
