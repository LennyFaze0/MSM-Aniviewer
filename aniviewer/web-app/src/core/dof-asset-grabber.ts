export const DEFAULT_DOF_CDN_BASE_URL = 'https://dlc2.bbbgame.net/monster_babies/dlc';

export type DofBundlePlatform = 'ios' | 'android';

export interface DofManifestEntry {
  name: string;
  size: number;
  url: string;
}

export interface DofManifestPreset {
  label: string;
  buildKey: string;
  platform: DofBundlePlatform;
  manifestPath: string;
}

export interface DofBuildHistoryEntry {
  buildKey: string;
  platform: DofBundlePlatform;
}

export const DOF_MANIFEST_PRESETS: DofManifestPreset[] = [
  {
    label: 'iOS 00018241',
    buildKey: '00018241-2807c732a3da',
    platform: 'ios',
    manifestPath: 'dof-manifests/00018241-2807c732a3da/ios/bundles.json',
  },
  {
    label: 'Android 00018345',
    buildKey: '00018345-082b08f909da',
    platform: 'android',
    manifestPath: 'dof-manifests/00018345-082b08f909da/android/bundles.json',
  },
];

function cleanBuildKey(value: string): string {
  return value.trim();
}

function cleanBundleName(value: string): string | undefined {
  const name = value.trim();
  if (!name || name === '.' || name === '..' || /[\\/\0]/.test(name)) return undefined;
  return name;
}

function manifestSize(value: unknown): number {
  const candidate = value && typeof value === 'object'
    ? (value as Record<string, unknown>).size
    : undefined;
  const parsed = Number(candidate);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : -1;
}

export function normalizeDofPlatform(value: string): DofBundlePlatform {
  return value.trim().toLowerCase() === 'android' ? 'android' : 'ios';
}

export function normalizeDofBaseUrl(value: string): string {
  const candidate = value.trim() || DEFAULT_DOF_CDN_BASE_URL;
  const parsed = new URL(candidate);
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
    throw new Error('The CDN base must use HTTP or HTTPS.');
  }
  parsed.hash = '';
  parsed.search = '';
  return parsed.toString().replace(/\/$/, '');
}

export function buildDofManifestUrl(
  baseUrl: string,
  buildKey: string,
  platform: DofBundlePlatform,
): string {
  const key = cleanBuildKey(buildKey);
  if (!key) throw new Error('A DOF build key is required.');
  return `${normalizeDofBaseUrl(baseUrl)}/${encodeURIComponent(key)}/${platform}/bundles.json`;
}

export function buildDofBundleUrl(
  baseUrl: string,
  buildKey: string,
  platform: DofBundlePlatform,
  bundleName: string,
): string {
  const key = cleanBuildKey(buildKey);
  const name = cleanBundleName(bundleName);
  if (!key) throw new Error('A DOF build key is required.');
  if (!name) throw new Error('A valid bundle name is required.');
  return `${normalizeDofBaseUrl(baseUrl)}/${encodeURIComponent(key)}/${platform}/${encodeURIComponent(name)}`;
}

export function parseDofManifest(
  value: unknown,
  baseUrl: string,
  buildKey: string,
  platform: DofBundlePlatform,
): DofManifestEntry[] {
  const candidates: Array<{ name: string; metadata: unknown }> = [];
  if (Array.isArray(value)) {
    value.forEach((item) => {
      if (!item || typeof item !== 'object') return;
      const record = item as Record<string, unknown>;
      const name = record.name ?? record.bundle ?? record.path;
      if (typeof name === 'string') candidates.push({ name, metadata: record });
    });
  } else if (value && typeof value === 'object') {
    Object.entries(value as Record<string, unknown>).forEach(([name, metadata]) => {
      candidates.push({ name, metadata });
    });
  } else {
    throw new Error('The DOF manifest must contain an object or array.');
  }

  const entries = new Map<string, DofManifestEntry>();
  candidates.forEach(({ name: rawName, metadata }) => {
    const name = cleanBundleName(rawName);
    if (!name) return;
    const key = name.toLowerCase();
    if (entries.has(key)) return;
    entries.set(key, {
      name,
      size: manifestSize(metadata),
      url: buildDofBundleUrl(baseUrl, buildKey, platform, name),
    });
  });
  return [...entries.values()].sort((left, right) => {
    const leftName = left.name.toLowerCase();
    const rightName = right.name.toLowerCase();
    if (leftName === rightName) return 0;
    return leftName < rightName ? -1 : 1;
  });
}

export function filterDofManifest(
  entries: DofManifestEntry[],
  query: string,
  staticinfoOnly: boolean,
): DofManifestEntry[] {
  const needle = query.trim().toLowerCase();
  return entries.filter((entry) => {
    const normalized = entry.name.toLowerCase();
    if (staticinfoOnly && !normalized.startsWith('staticinfo_')) return false;
    return !needle || normalized.includes(needle);
  });
}

export function parseDofBuildHistory(value: string | null): DofBuildHistoryEntry[] {
  if (!value) return [];
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    const seen = new Set<string>();
    const history: DofBuildHistoryEntry[] = [];
    parsed.forEach((item) => {
      if (!item || typeof item !== 'object') return;
      const record = item as Record<string, unknown>;
      const buildKey = typeof record.buildKey === 'string' ? cleanBuildKey(record.buildKey) : '';
      if (!buildKey) return;
      const platform = normalizeDofPlatform(String(record.platform ?? 'ios'));
      const marker = `${buildKey.toLowerCase()}|${platform}`;
      if (seen.has(marker)) return;
      seen.add(marker);
      history.push({ buildKey, platform });
    });
    return history.slice(0, 30);
  } catch {
    return [];
  }
}

export function rememberDofBuild(
  history: DofBuildHistoryEntry[],
  buildKey: string,
  platform: DofBundlePlatform,
): DofBuildHistoryEntry[] {
  const key = cleanBuildKey(buildKey);
  if (!key) return history.slice(0, 30);
  return [
    { buildKey: key, platform },
    ...history.filter((entry) => entry.buildKey !== key || entry.platform !== platform),
  ].slice(0, 30);
}
