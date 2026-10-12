import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, test } from 'vitest';
import { normalizeAnimationDocument, parseAnimationBin, type BinFormat } from './bin-parser';
import type { AnimationDocument } from './types';

const fixtureRoot = process.env.ANIVIEWER_REVISION_PARITY;

describe.skipIf(!fixtureRoot)('native BIN parser parity', () => {
  const expectedTracks = [0, 6, 10, 10, 15, 15, 15];
  const expectedFormats: Array<BinFormat | undefined> = [
    undefined,
    'rev1_launch',
    'rev2',
    'choir',
    'rev4',
    'rev5',
    'rev6',
  ];

  for (let revision = 1; revision <= 6; revision += 1) {
    test(`auto-detects Rev ${revision} and preserves the reference pose`, () => {
      const bytes = readFileSync(join(fixtureRoot!, `rev${revision}.bin`));
      const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
      const parsed = parseAnimationBin(buffer, 'auto');
      const firstTrack = parsed.document.anims[0];
      const firstLayer = firstTrack?.layers[0];
      const firstFrame = firstLayer?.frames[0];

      expect(parsed.revision).toBe(revision);
      expect(parsed.format).toBe(expectedFormats[revision]);
      expect(parsed.document.anims).toHaveLength(expectedTracks[revision] ?? 0);
      expect(parsed.document.sources[0]?.src).toBe('monster_A_sheet.xml');
      expect(firstTrack?.name).toBe('01-A_Dance_01');
      expect(firstTrack?.layers).toHaveLength(16);
      expect(firstLayer).toMatchObject({ name: 'mouth', parent: 6, id: 0, src: 0 });
      expect(firstLayer?.anchor_x).toBeCloseTo(22);
      expect(firstLayer?.anchor_y).toBeCloseTo(10);
      expect(firstFrame?.pos).toMatchObject({ immediate: 0, x: 55, y: 47 });
      expect(firstFrame?.sprite).toMatchObject({ immediate: 0, string: 'A_mouth_01' });

      const referencePath = join(fixtureRoot!, `rev${revision}.json`);
      if (existsSync(referencePath)) {
        const expected = normalizeAnimationDocument(
          JSON.parse(readFileSync(referencePath, 'utf8')) as AnimationDocument,
        );
        expect(parsed.document.sources).toEqual(expected.sources);
        expect(parsed.document.anims).toEqual(expected.anims);
      }
    });
  }
});
