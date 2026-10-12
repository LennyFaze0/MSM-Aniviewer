export type AudioResolutionContext = 'standard' | 'muppets' | 'dof';

interface AudioContextInput {
  kind?: string;
  detectedFormat?: string;
  sourceFormat?: string;
  entryName?: string;
}

const STANDARD_SILENT_TOKENS = new Set([
  'sleep',
  'sleeping',
  'idle',
  'idle1',
  'idle2',
  'idleloop',
  'inactive',
  'store',
  'shop',
  'market',
  'stand',
  'rest',
]);

const DOF_SILENT_TOKENS = new Set([
  ...STANDARD_SILENT_TOKENS,
  'pose',
  'breath',
  'blink',
  'mute',
  'muted',
  'silent',
  'silence',
  'quiet',
  'still',
  'calm',
]);

const MUPPET_ALIAS_GROUPS: readonly (readonly string[])[] = [
  ['rowlf', 'rawlf'],
  ['drteeth', 'dr_teeth', 'teeth'],
  ['swedishchef', 'swedish_chef'],
  ['sam_eagle', 'sam'],
  ['lew_zealand', 'lou'],
  ['mahnamahna', 'mahnah_mahnah', 'mahna'],
  ['marvinsuggs', 'suggs'],
  ['crazy_harry', 'harry'],
  ['tamborine', 'tambourine'],
  ['guitar_whatnot', 'guitar', 'accoustic', 'acoustic'],
  ['pot_whatnot', 'pot', 'pots'],
  ['twin_whatnot', 'twin', 'twins'],
  ['misspiggy', 'miss_piggy', 'piggy'],
  ['chicken', 'chickens'],
  ['80srobot', '80s_robot', 'robot'],
  ['muppetman', 'man'],
];

const MUPPET_ALIASES = new Map<string, readonly string[]>();
for (const group of MUPPET_ALIAS_GROUPS) {
  for (const value of group) MUPPET_ALIASES.set(value, group);
}

function addCandidate(candidates: string[], seen: Set<string>, value: string): void {
  const normalized = normalizeAudioKey(value);
  if (!normalized || seen.has(normalized)) return;
  seen.add(normalized);
  candidates.push(normalized);
}

function muppetBaseAliases(value: string): string[] {
  const normalized = normalizeAudioKey(value);
  const values = new Set<string>([normalized]);
  for (const alias of MUPPET_ALIASES.get(normalized) ?? []) values.add(alias);
  if (normalized.endsWith('_whatnot')) {
    const base = normalized.slice(0, -'_whatnot'.length);
    values.add(base);
    for (const alias of MUPPET_ALIASES.get(base) ?? []) values.add(alias);
  }
  return [...values].filter(Boolean);
}

function muppetAudioCandidates(trackName: string, entryName = ''): string[] {
  const normalized = normalizeAudioKey(trackName);
  const candidates: string[] = [];
  const seen = new Set<string>();
  addCandidate(candidates, seen, normalized);

  const leadingStage = /^(\d+)_/.exec(normalized)?.[1];
  let core = normalized.replace(/^\d+_/, '');
  const trailingTrack = /_(\d+)$/.exec(core)?.[1];
  if (trailingTrack) core = core.slice(0, -(trailingTrack.length + 1));

  const entryStem = normalizeAudioKey(entryName)
    .replace(/^muppet_/, '')
    .replace(/_stage\d+$/, '');
  const entryStage = /_stage(\d+)$/.exec(normalizeAudioKey(entryName))?.[1];
  const stage = leadingStage ?? entryStage;
  const bases = new Set<string>();
  for (const base of [core, entryStem]) {
    for (const alias of muppetBaseAliases(base)) bases.add(alias);
  }

  if (stage) {
    // Resolve every stage-qualified alias before considering any unscoped sample.
    // Generic Muppets clips are short triggered sounds, not full performance audio.
    for (const base of bases) {
      if (trailingTrack) addCandidate(candidates, seen, `${stage}_${base}_${trailingTrack}`);
      else addCandidate(candidates, seen, `${stage}_${base}_01`);
      addCandidate(candidates, seen, `${stage}_${base}`);
    }
    return candidates;
  }

  for (const base of bases) {
    if (trailingTrack) addCandidate(candidates, seen, `${base}_${trailingTrack}`);
    addCandidate(candidates, seen, base);
  }
  return candidates;
}

function dofAudioCandidates(trackName: string): string[] {
  const normalized = normalizeAudioKey(trackName);
  const candidates = new Set([normalized]);
  const parts = normalized.split('_').filter(Boolean);
  const appearances = new Set(['adult', 'baby', 'blue', 'green', 'orange', 'purple', 'red', 'yellow']);
  if (parts.length >= 6 && appearances.has(parts[2] ?? '')) {
    const monster = `${parts[0]}_${parts[1]}`;
    const island = parts.slice(3, -1).join('_');
    const variant = parts.at(-1);
    if (island && variant) candidates.add(`${island}_${monster}_${variant}`);
    if (island) candidates.add(`${island}_${monster}`);
  }
  return [...candidates].filter(Boolean);
}

export function normalizeAudioKey(value: string): string {
  const basename = value.replaceAll('\\', '/').split('/').at(-1) ?? value;
  return basename
    .replace(/\.[^.]+$/, '')
    .replace(/\.animbbb$/i, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

export function resolveAudioContext(input: AudioContextInput): AudioResolutionContext {
  if (input.kind === 'dof' || input.sourceFormat === 'dof') return 'dof';
  if (
    input.detectedFormat === 'muppets'
    || input.sourceFormat === 'muppets'
    || /^muppet_/i.test((input.entryName ?? '').replaceAll('\\', '/').split('/').at(-1) ?? '')
  ) return 'muppets';
  return 'standard';
}

export function shouldSkipAnimationAudio(
  trackName: string,
  context: AudioResolutionContext,
  monsterToken = '',
): boolean {
  const normalized = normalizeAudioKey(trackName);
  const tokens = new Set(normalized.split('_').filter(Boolean));
  const silentTokens = context === 'dof' ? DOF_SILENT_TOKENS : STANDARD_SILENT_TOKENS;
  if ([...tokens].some((token) => silentTokens.has(token))) return true;
  if (context === 'muppets' && tokens.has('structure')) return true;
  if (context !== 'standard' || !tokens.has('dance')) return false;

  const normalizedMonster = normalizeAudioKey(monsterToken);
  const canonicalMonsterFTrack = /^(?:\d+_)?f_dance_\d+$/.test(normalized);
  return normalizedMonster !== 'f' && !canonicalMonsterFTrack;
}

export function buildAnimationAudioCandidates(
  trackName: string,
  context: AudioResolutionContext,
  entryName = '',
): string[] {
  if (context === 'muppets') return muppetAudioCandidates(trackName, entryName);
  if (context === 'dof') return dofAudioCandidates(trackName);
  const normalized = normalizeAudioKey(trackName);
  return normalized ? [normalized] : [];
}
