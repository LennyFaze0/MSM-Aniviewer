import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, test } from 'vitest';
import { parseAnimationBin, type BinFormat, type BinRevision } from './bin-parser';

const corpusRoot = process.env.ANIVIEWER_REVISION_CORPUS;

interface CorpusCase {
  name: string;
  path: string;
  revision: BinRevision;
  format: BinFormat;
}

function expectedRevision(family: string): BinRevision | undefined {
  // This iOS Choir build retained the Rev 2 serializer; its Android sibling
  // and mainstream MSM 1.3.2 use the Choir/Rev 3 serializer.
  if (/monster choir ios/i.test(family)) return 2;
  const match = /\brev\s*([1-6])\b/i.exec(family);
  return match?.[1] ? Number(match[1]) as BinRevision : undefined;
}

function expectedFormat(family: string, revision: BinRevision): BinFormat {
  if (/my muppet show/i.test(family)) return 'muppets';
  if (/my singing muppets beta/i.test(family)) return 'rev2';
  if (/monster choir ios/i.test(family)) return 'rev2';
  if (/monster choir|monsters 1\.3\.2/i.test(family)) return 'choir';
  if (revision === 1) return 'rev1_launch';
  return `rev${revision}` as BinFormat;
}

function collectFiles(directory: string, family: string, revision: BinRevision, output: CorpusCase[]): void {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      collectFiles(path, family, revision, output);
      continue;
    }
    if (!/^(?:monster|muppet)_.*\.bin$/i.test(entry.name)) continue;
    if (entry.name.toLowerCase() === 'muppet_theatre.bin' || /_rev6\.bin$/i.test(entry.name)) continue;
    output.push({
      name: `${family}/${entry.name}`,
      path,
      revision,
      format: expectedFormat(family, revision),
    });
  }
}

const cases: CorpusCase[] = [];
if (corpusRoot) {
  for (const familyEntry of readdirSync(corpusRoot, { withFileTypes: true })) {
    if (!familyEntry.isDirectory()) continue;
    const revision = expectedRevision(familyEntry.name);
    if (!revision) continue;
    collectFiles(join(corpusRoot, familyEntry.name), familyEntry.name, revision, cases);
  }
}

describe.skipIf(!corpusRoot)('untouched animation BIN revision corpus', () => {
  test.each(cases)('$name', ({ path, revision, format }) => {
    const bytes = readFileSync(path);
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    const parsed = parseAnimationBin(buffer, 'auto', path);
    expect(parsed.revision).toBe(revision);
    expect(parsed.format).toBe(format);
    expect(parsed.document.anims.length).toBeGreaterThan(0);
  });
});
