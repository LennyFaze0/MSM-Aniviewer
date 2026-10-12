import { describe, expect, test } from 'vitest';
import { parseMuppetsAudioManifest } from './muppets-audio-manifest';

function manifestFixture(): ArrayBuffer {
  const bytes: number[] = [];
  const u32 = (value: number): void => {
    bytes.push(value & 0xff, (value >>> 8) & 0xff, (value >>> 16) & 0xff, (value >>> 24) & 0xff);
  };
  const string = (value: string): void => {
    const encoded = new TextEncoder().encode(`${value}\0`);
    u32(encoded.length);
    bytes.push(...encoded);
    while (bytes.length % 4) bytes.push(0);
  };

  string('rawlf');
  string('rawlf animation');
  u32(110);
  u32(1);
  u32(72);
  string('audio/music/01-rawlf_01.ogg');
  u32(2);
  u32(72);
  u32(110);
  string('01-rowlf_01');
  u32(73);
  u32(110);
  string('Idle');
  return new Uint8Array(bytes).buffer;
}

describe('Muppets audio manifest', () => {
  test('reads exact track bindings and intentional empty bindings', () => {
    expect(parseMuppetsAudioManifest(manifestFixture())).toEqual({
      identity: 'rawlf',
      label: 'rawlf animation',
      bindings: [
        { trackName: '01-rowlf_01', audioPath: 'audio/music/01-rawlf_01.ogg' },
        { trackName: 'Idle', audioPath: undefined },
      ],
    });
  });

  test('rejects unrelated binary data', () => {
    expect(parseMuppetsAudioManifest(new Uint8Array([1, 2, 3, 4]).buffer)).toBeUndefined();
  });
});
