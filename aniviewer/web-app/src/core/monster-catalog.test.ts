import { describe, expect, test, vi } from 'vitest';
import type { IndexedAnimation } from './asset-library';
import { buildMonsterCatalog, filterMonsterCatalog, findMonsterPortrait, type MonsterPortrait } from './monster-catalog';

function source(path: string, extra: Partial<IndexedAnimation> = {}): IndexedAnimation {
  return { path, name: path.split('/').pop()!, kind: 'bin', size: 1, ...extra } as IndexedAnimation;
}

describe('monster browser catalog', () => {
  test('uses shipped Rosetta names and keeps rare monsters separate', () => {
    const entries = buildMonsterCatalog([
      source('monster_a.bin'), source('monster_e.bin'), source('monster_a_RARE.bin'),
      source('monster_rare_a.json', { kind: 'json' }),
    ]);
    expect(entries.map((entry) => entry.name)).toEqual(['Mammott', 'Rare Tweedle', 'Tweedle']);
    expect(entries[1]!.sources).toHaveLength(2);
  });

  test('retains every source path and BIN/JSON choice without duplicating cards', () => {
    const sources = [source('game1/monster_a.bin'), source('game2/monster_a.bin'), source('game1/monster_a.json', { kind: 'json' })];
    const entries = buildMonsterCatalog(sources);
    expect(entries).toHaveLength(1);
    expect(new Set(entries[0]!.sources)).toEqual(new Set(sources));
    expect(filterMonsterCatalog(entries, 'tweedle game2')).toEqual(entries);
    expect(filterMonsterCatalog(entries, 'monster_a.json')).toEqual(entries);
  });

  test('groups island sources while excluding costume and track helpers', () => {
    const entries = buildMonsterCatalog([
      source('monster_o_island1.bin'), source('monster_o_island2.bin'),
      source('monster_a_costume_01.bin'), source('monster_a_tracks.bin'), source('monster_ab.bin'),
    ]);
    expect(entries).toHaveLength(2);
    expect(entries.find((entry) => entry.token === 'o')!.sources).toHaveLength(2);
  });

  test('includes DOF appearances and unknown custom files without requiring portraits', () => {
    const entries = buildMonsterCatalog([
      source('staticinfo_acde_riff.unity3d', { kind: 'dof', dofFamily: 'acde_riff', dofAppearance: 'base' }),
      source('staticinfo_acde_riff_chromatic_red.unity3d', { kind: 'dof', dofFamily: 'acde_riff', dofAppearance: 'red' }),
      source('custom-animation.json', { kind: 'json' }),
    ]);
    expect(filterMonsterCatalog(entries, 'riff red', 'DOF')[0]!.sources).toHaveLength(2);
    expect(filterMonsterCatalog(entries, '', 'Other')).toHaveLength(1);
    expect(filterMonsterCatalog(entries, 'riff', 'MSM')).toHaveLength(0);
  });

  test('matches portrait tokens exactly, preferring the selected installation without decoding', () => {
    const sources = [source('old/data/monster_a.bin'), source('current/data/monster_a.bin')];
    const [entry] = buildMonsterCatalog(sources);
    const blob = vi.fn(async () => new Blob());
    const portraits: MonsterPortrait[] = [
      { token: 'a', path: 'old/gfx/book/monster_portrait_square_a.png', blob },
      { token: 'a', path: 'current/gfx/book/monster_portrait_square_a.png', blob },
      { token: 'ab', path: 'current/data/monster_portrait_square_ab.png', blob },
    ];
    expect(findMonsterPortrait(entry!, sources[1]!, portraits)).toBe(portraits[1]);
    expect(findMonsterPortrait(entry!, sources[0]!, portraits)).toBe(portraits[0]);
    expect(findMonsterPortrait(entry!, sources[0]!, [portraits[2]!])).toBeUndefined();
    expect(blob).not.toHaveBeenCalled();
  });

  test('matches rare prefix/suffix portrait variants without substituting common art', () => {
    const animation = source('monster_a_rare.bin');
    const [entry] = buildMonsterCatalog([animation]);
    const blob = async () => new Blob();
    const portrait = { token: 'rare_a', path: 'monster_portrait_square_rare_a.png', blob };
    expect(findMonsterPortrait(entry!, animation, [portrait])).toBe(portrait);
    expect(findMonsterPortrait(entry!, animation, [{ ...portrait, token: 'a' }])).toBeUndefined();
  });
});
