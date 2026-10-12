export interface MuppetsAudioBinding {
  trackName: string;
  audioPath?: string;
}

export interface MuppetsAudioManifest {
  identity: string;
  label: string;
  bindings: MuppetsAudioBinding[];
}

class ManifestReader {
  private readonly view: DataView;
  private readonly bytes: Uint8Array;
  offset = 0;

  constructor(buffer: ArrayBuffer) {
    this.view = new DataView(buffer);
    this.bytes = new Uint8Array(buffer);
  }

  get remaining(): number {
    return this.view.byteLength - this.offset;
  }

  u32(): number {
    if (this.remaining < 4) throw new Error('Unexpected end of Muppets audio manifest.');
    const value = this.view.getUint32(this.offset, true);
    this.offset += 4;
    return value;
  }

  string(maximum = 0x2000): string {
    const length = this.u32();
    if (length <= 0 || length > maximum || length > this.remaining) {
      throw new Error('Invalid string in Muppets audio manifest.');
    }
    const end = this.offset + length;
    const payloadEnd = this.bytes[end - 1] === 0 ? end - 1 : end;
    const value = new TextDecoder().decode(this.bytes.subarray(this.offset, payloadEnd));
    this.offset = (end + 3) & ~3;
    if (this.offset > this.view.byteLength) throw new Error('Invalid manifest string alignment.');
    return value;
  }

  skip(length: number): void {
    if (length < 0 || length > this.remaining) throw new Error('Truncated Muppets audio manifest.');
    this.offset += length;
  }

  looksLikeTrack(): boolean {
    if (this.remaining < 12) return false;
    const nameLength = this.view.getUint32(this.offset + 8, true);
    if (nameLength <= 0 || nameLength > 0x2000) return false;
    return ((this.offset + 12 + nameLength + 3) & ~3) <= this.view.byteLength;
  }
}

export function parseMuppetsAudioManifest(buffer: ArrayBuffer): MuppetsAudioManifest | undefined {
  try {
    const reader = new ManifestReader(buffer);
    const identity = reader.string(0x100);
    const label = reader.string(0x200);
    if (!/^[a-z0-9_ -]+$/i.test(identity) || !/animation/i.test(label)) return undefined;

    reader.u32(); // Manifest table size/version marker.
    const sampleCount = reader.u32();
    if (sampleCount > 256) return undefined;
    const samples = new Map<number, string>();
    for (let index = 0; index < sampleCount; index += 1) {
      const sampleId = reader.u32();
      const path = reader.string();
      if (!/\.(?:ogg|wav|mp3|m4a|aac|flac)$/i.test(path)) return undefined;
      samples.set(sampleId, path);
      samples.set(sampleId & 0xffff, path);
      samples.set(sampleId & 0xff, path);
      samples.set(sampleId | 0xff00, path);
    }

    const trackCount = reader.u32();
    if (trackCount <= 0 || trackCount > 1024) return undefined;
    const bindings: MuppetsAudioBinding[] = [];
    for (let index = 0; index < trackCount; index += 1) {
      const sampleReference = reader.u32();
      reader.u32(); // Legacy colour-block marker.
      const trackName = reader.string();
      if (!trackName) return undefined;
      bindings.push({
        trackName,
        audioPath: sampleReference === 0xffffffff ? undefined : samples.get(sampleReference),
      });
      if (index < trackCount - 1 && !reader.looksLikeTrack() && reader.remaining >= 16) {
        reader.skip(16);
      }
    }
    return { identity, label, bindings };
  } catch {
    return undefined;
  }
}
