import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, test } from 'vitest';
import {
  buildAnimationAudioCandidates,
  normalizeAudioKey,
  shouldSkipAnimationAudio,
} from './audio-resolution';
import { parseMuppetsAudioManifest } from './muppets-audio-manifest';
import { parseAnimationBin } from './bin-parser';
import type { AnimationDocument } from './types';

const fixturePath = process.env.ANIVIEWER_MUPPETS_BIN;
const referencePath = process.env.ANIVIEWER_MUPPETS_REFERENCE;
const corpusPath = process.env.ANIVIEWER_MUPPETS_DIRECTORY;
const inferredAudioPath = corpusPath ? join(corpusPath, '..', 'audio', 'music') : undefined;
const audioCorpusPath = process.env.ANIVIEWER_MUPPETS_AUDIO_DIRECTORY ?? inferredAudioPath;

describe.skipIf(!fixturePath || !referencePath)('My Muppets Show BIN parser parity', () => {
  test('matches the desktop converter frame for frame', () => {
    const bytes = readFileSync(fixturePath!);
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    const expected = JSON.parse(readFileSync(referencePath!, 'utf8')) as AnimationDocument;
    const parsed = parseAnimationBin(buffer, 'auto', fixturePath!);
    const actual = parsed.document;

    expect(parsed.revision).toBe(2);
    expect(parsed.format).toBe('muppets');
    expect(actual).toMatchObject({
      rev: expected.rev,
      blend_version: expected.blend_version,
      source_format: 'muppets',
      source_revision: 2,
      legacy_format: true,
      sources: expected.sources,
    });
    expect(actual.anims).toHaveLength(expected.anims.length);

    for (let animationIndex = 0; animationIndex < expected.anims.length; animationIndex += 1) {
      const expectedAnimation = expected.anims[animationIndex]!;
      const actualAnimation = actual.anims[animationIndex]!;
      expect(actualAnimation).toMatchObject({
        name: expectedAnimation.name,
        width: expectedAnimation.width,
        height: expectedAnimation.height,
        loop_offset: expectedAnimation.loop_offset,
        centered: expectedAnimation.centered,
      });
      expect(actualAnimation.layers).toHaveLength(expectedAnimation.layers.length);

      for (let layerIndex = 0; layerIndex < expectedAnimation.layers.length; layerIndex += 1) {
        const expectedLayer = expectedAnimation.layers[layerIndex]!;
        const actualLayer = actualAnimation.layers[layerIndex]!;
        expect(actualLayer).toMatchObject({
          name: expectedLayer.name,
          type: expectedLayer.type,
          blend: expectedLayer.blend,
          parent: expectedLayer.parent,
          id: expectedLayer.id,
          src: expectedLayer.src,
          width: expectedLayer.width,
          height: expectedLayer.height,
          anchor_x: expectedLayer.anchor_x,
          anchor_y: expectedLayer.anchor_y,
        });
        expect(actualLayer.frames).toEqual(expectedLayer.frames);
      }
    }
  });
});

describe.skipIf(!corpusPath)('My Muppets Show BIN corpus', () => {
  const files = corpusPath
    ? readdirSync(corpusPath).filter((name) => (
        /^muppet_.*\.bin$/i.test(name)
        && !/_rev6\.bin$/i.test(name)
        && name.toLowerCase() !== 'muppet_theatre.bin'
      ))
    : [];

  test.each(files)('parses %s', (name) => {
    const path = join(corpusPath!, name);
    const bytes = readFileSync(path);
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    const parsed = parseAnimationBin(buffer, 'auto', path);
    expect(parsed.document.anims.length).toBeGreaterThan(0);
    expect(parsed.document.sources[0]?.src).toMatch(/\.(?:xml|png)$/i);
  });
});

describe.skipIf(!corpusPath || !audioCorpusPath || !existsSync(audioCorpusPath))(
  'My Muppets Show audio corpus',
  () => {
    test('honors manifest bindings without substituting generic samples', () => {
      const audioNames = new Set(
        readdirSync(audioCorpusPath!)
          .filter((name) => /\.(?:ogg|wav|mp3|m4a|aac|flac)$/i.test(name))
          .map(normalizeAudioKey),
      );
      const manifestBindings = new Map<string, string | undefined>();
      for (const name of readdirSync(corpusPath!).filter((candidate) => /^\d{3}[_-].+\.bin$/i.test(candidate))) {
        const bytes = readFileSync(join(corpusPath!, name));
        const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
        const manifest = parseMuppetsAudioManifest(buffer);
        for (const binding of manifest?.bindings ?? []) {
          manifestBindings.set(normalizeAudioKey(binding.trackName), binding.audioPath);
        }
      }

      const missingManifestAudio: string[] = [];
      let mappedCount = 0;
      let intentionallySilentCount = 0;

      for (const name of readdirSync(corpusPath!).filter((candidate) => (
        /^muppet_.*\.bin$/i.test(candidate)
        && !/_rev6\.bin$/i.test(candidate)
        && candidate.toLowerCase() !== 'muppet_theatre.bin'
      ))) {
        const path = join(corpusPath!, name);
        const bytes = readFileSync(path);
        const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
        const document = parseAnimationBin(buffer, 'auto', path).document;
        for (const animation of document.anims) {
          if (shouldSkipAnimationAudio(animation.name, 'muppets')) continue;
          const trackKey = normalizeAudioKey(animation.name);
          if (manifestBindings.has(trackKey)) {
            const audioPath = manifestBindings.get(trackKey);
            if (!audioPath) {
              intentionallySilentCount += 1;
            } else if (!audioNames.has(normalizeAudioKey(audioPath))) {
              missingManifestAudio.push(`${name}: ${animation.name} -> ${audioPath}`);
            } else {
              mappedCount += 1;
            }
            continue;
          }

          const candidates = buildAnimationAudioCandidates(animation.name, 'muppets', name);
          const stage = /^(\d+)_/.exec(trackKey)?.[1];
          if (stage) {
            expect(candidates.every((candidate) => candidate.startsWith(`${stage}_`))).toBe(true);
          }
        }
      }

      expect(mappedCount).toBeGreaterThan(0);
      expect(intentionallySilentCount).toBeGreaterThan(0);
      expect(missingManifestAudio).toEqual([]);
    });
  },
);
