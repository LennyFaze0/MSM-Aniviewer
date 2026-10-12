import rosetta from '../../../docs/monster_name_file_rosetta.txt?raw';
import type { IndexedAnimation } from './asset-library';

export interface MonsterPortrait {
  token: string;
  path: string;
  blob(): Promise<Blob>;
}

export interface MonsterCatalogEntry {
  id: string;
  name: string;
  token: string;
  category: 'MSM' | 'DOF' | 'Other';
  sources: IndexedAnimation[];
  searchText: string;
}

const names = new Map<string, string>();
for (const line of rosetta.split(/\r?\n/)) {
  const [label, file] = line.split(' = ').map((part) => part.trim());
  if (!label || !file) continue;
  const stem = file.replace(/\.bin$/i, '').toLowerCase();
  // Rosetta includes island aliases for the same file. Keep the shortest name.
  if (!names.has(stem) || label.length < names.get(stem)!.length) names.set(stem, label);
}

function canonicalToken(token: string): string {
  return token.toLowerCase().replace(/_island\d+$/, '').replace(/^(rare|epic)_(.+)$/, '$2_$1');
}

function title(value: string): string {
  return value.split(/[_\s]+/).filter(Boolean).map((word) => word[0]!.toUpperCase() + word.slice(1)).join(' ');
}

function identity(source: IndexedAnimation): Pick<MonsterCatalogEntry, 'token' | 'category' | 'name'> | undefined {
  const stem = source.name.replace(/\.[^.]+$/, '').toLowerCase();
  if (source.kind === 'dof') {
    const family = source.dofFamily ?? stem.replace(/^staticinfo_/, '');
    const readable = family.replace(/^[a-z0-9]+_/, '');
    return { token: family, category: 'DOF', name: title(readable || family) };
  }
  const monster = /^(?:club\d+-\d+_)?monster_(.+)$/.exec(stem);
  if (monster) {
    if (/(?:^|_)(?:costumes?|tracks?)(?:_|$)/.test(monster[1]!)) return undefined;
    const token = canonicalToken(monster[1]!);
    const name = names.get(`monster_${token}`) ?? names.get(stem) ?? title(token);
    return { token, category: 'MSM', name };
  }
  // Custom JSONs and older games remain accessible even without a Rosetta name.
  return { token: stem, category: 'Other', name: title(stem) };
}

export function buildMonsterCatalog(sources: readonly IndexedAnimation[]): MonsterCatalogEntry[] {
  const groups = new Map<string, MonsterCatalogEntry>();
  for (const source of sources) {
    const info = identity(source);
    if (!info) continue;
    const id = `${info.category}:${info.token}`;
    let entry = groups.get(id);
    if (!entry) {
      entry = { ...info, id, sources: [], searchText: '' };
      groups.set(id, entry);
    }
    entry.sources.push(source);
  }
  for (const entry of groups.values()) {
    entry.sources.sort((a, b) => a.path.localeCompare(b.path));
    entry.searchText = [entry.name, entry.token, entry.category, ...entry.sources.flatMap((source) => [
      source.path, source.dofAppearance ?? '', names.get(source.name.replace(/\.[^.]+$/, '').toLowerCase()) ?? '',
    ])].join(' ').toLowerCase();
  }
  return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
}

export function filterMonsterCatalog(
  entries: readonly MonsterCatalogEntry[], query: string, category: string = '',
): MonsterCatalogEntry[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return entries.filter((entry) => (!category || entry.category === category)
    && words.every((word) => entry.searchText.includes(word)));
}

export function findMonsterPortrait(
  entry: MonsterCatalogEntry, source: IndexedAnimation, portraits: readonly MonsterPortrait[],
): MonsterPortrait | undefined {
  const sourceToken = canonicalToken(source.name.replace(/\.[^.]+$/, '').replace(/^monster_/i, ''));
  const tokens = new Set([sourceToken, entry.token]);
  const candidates = portraits.filter((portrait) => tokens.has(canonicalToken(portrait.token)));
  const sourceParts = source.path.toLowerCase().split('/');
  const sharedRoot = (path: string): number => {
    const parts = path.toLowerCase().split('/');
    let count = 0;
    while (count < Math.min(parts.length, sourceParts.length) && parts[count] === sourceParts[count]) count++;
    return count;
  };
  // Do not borrow art from a different imported installation when a local match exists.
  return candidates.sort((a, b) => sharedRoot(b.path) - sharedRoot(a.path) || a.path.localeCompare(b.path))[0];
}
