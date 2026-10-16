import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { findManualMatches, parseManualDocument } from './manual-document';

const source = readFileSync(new URL('../content/viewer-manual.txt', import.meta.url), 'utf8');
const dividers = new Set([
  '0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o0o',
  'XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX',
  '------------------------------------',
]);

describe('author-supplied manual', () => {
  it('keeps the integrated document byte-for-byte, including spelling and punctuation', () => {
    expect(createHash('sha256').update(source).digest('hex'))
      .toBe('7977cbde19cbaca807cf6df7f921d1646e0b6fdf5fc7175295f33f524123e0a8');
  });

  it('preserves every original character outside the explicitly requested heading and examples', () => {
    const original = source.slice(0, source.indexOf('\n(((BIN FILE EXAMPLES)))'))
      .replace('(((ANIMATION TAB)))', '(((ANIMATION TAB)))EXPORT TAB')
      .replace('BIN FILE EXAMPLES: 001_B.bin and 001_kermit.bin', '-------')
      .replace('BIN FILE EXAMPLES: monster_a.bin and muppet_kermit_stage01.bin', '-------')
      .replace('XML FILE EXAMPLES: monster_A_sheet.xml and kermit_sheet.xml', '-------');
    expect(createHash('sha256').update(original).digest('hex'))
      .toBe('d05ffbdf2686f860ec011e7d94e9dd6a80350dee023f92244eee56aa8021cf7e');
  });

  it('removes only the approved divider lines, without adding or losing any other text', () => {
    const doc = parseManualDocument(source);
    const expected = source.split('\n').filter((line) => !dividers.has(line)).join('\n');
    expect(doc.text).toBe(expected);
    expect(doc.sections.flatMap((section) => section.blocks.map((block) => block.text)).join('')).toBe(expected);
    expect(doc.text).toContain('for examples see BIN FILE EXAMPLES: 001_B.bin and 001_kermit.bin');
    expect(doc.text).not.toContain('see -------');
    expect(doc.text).toContain('(((ANIMATION TAB)))');
    expect(doc.text).not.toContain('(((ANIMATION TAB)))EXPORT TAB');
    expect(doc.sections.map((section) => section.label)).toEqual([
      source.split('\n')[0], '(((FILES TAB)))', '(((ANIMATION TAB)))',
      '(((AUDIO TAB)))', '((SPRITES TAB))', '(((EXPORT TAB)))', '(((SYSTEM TAB)))',
      '(((BIN FILE EXAMPLES)))', '(((XML FILE EXAMPLES)))',
    ]);
    expect(doc.sections.flatMap((section) => section.blocks).filter((block) => block.label).map((block) => block.label))
      .toContain('Dawn of Fire Asset Grabber');
  });

  it('preserves CRLF, inline dashes and paragraphs when organizing sections', () => {
    const input = '\r\n\r\nTitle\r\n\r\nXXXXXXXX\r\n\r\n(((SECTION)))\r\n\r\nFILE -\r\n\r\nText ------- stays.\r\n';
    const doc = parseManualDocument(input);
    expect(doc.text).toBe(input.replace('XXXXXXXX\r\n', ''));
    expect(doc.sections.flatMap((section) => section.blocks.map((block) => block.text)).join('')).toBe(doc.text);
    expect(doc.title).toBe('Title');
    expect(parseManualDocument('').sections).toEqual([]);
  });

  it('searches literal text case-insensitively without modifying it', () => {
    expect(findManualMatches('Ab[CD] ab[cd]', ' AB[cd] ')).toEqual([{ start: 0, end: 6 }, { start: 7, end: 13 }]);
    expect(findManualMatches('text', ' ')).toEqual([]);
    expect(findManualMatches('text', 'missing')).toEqual([]);
    expect(findManualMatches('aaaa', 'aa')).toEqual([{ start: 0, end: 2 }, { start: 2, end: 4 }]);
  });
});
