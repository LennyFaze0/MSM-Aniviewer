import { BlobReader, ZipReader, type FileEntry } from '@zip.js/zip.js';
import { isAnimationDocument } from './animation';
import { parseTextureAtlas } from './atlas';
import {
  normalizeAnimationDocument,
  type BinFormat,
  type BinRevision,
  type BinRevisionMode,
} from './bin-parser';
import { BinParserWorkerClient } from './bin-worker-client';
import {
  buildAnimationAudioCandidates,
  normalizeAudioKey,
  resolveAudioContext,
  shouldSkipAnimationAudio,
} from './audio-resolution';
import {
  applyCostumeDefinition,
  isCostumeDefinition,
  parseCostumeBin,
  type CostumeDefinition,
} from './costume';
import { dofChromaticColor, type DofChromaticColor, type DofChromaticGradient } from './dof-chromatic';
import { costumeShaderBehavior } from './costume-shader';
import { costumeAttachmentAlignment } from './costume-appearance';
import { parseMuppetsAudioManifest } from './muppets-audio-manifest';
import { decodeTextureImage } from './texture-alpha';
import type { MonsterPortrait } from './monster-catalog';
import type {
  AnimationDocument,
  AnimationTrack,
  AtlasSprite,
  AudioClipInput,
  CostumeAttachmentInput,
  CostumeShaderTextureInput,
  DofParticlePrefabInput,
  LoadedAtlasInput,
  TextureAtlasData,
} from './types';

const IMAGE_EXTENSIONS = ['.avif', '.png', '.webp', '.jpg', '.jpeg'];
const AUDIO_EXTENSIONS = new Set(['.ogg', '.wav', '.mp3', '.m4a', '.aac', '.flac']);
const ANIMATION_EXTENSIONS = new Set(['.bin', '.json']);

let dofBundleToolsPromise: Promise<typeof import('./dof-bundle')> | undefined;

function loadDofBundleTools(): Promise<typeof import('./dof-bundle')> {
  dofBundleToolsPromise ??= import('./dof-bundle');
  return dofBundleToolsPromise;
}

interface LibraryAsset {
  path: string;
  name: string;
  size: number;
  origin: 'file' | 'zip';
  blob(): Promise<Blob>;
  arrayBuffer(): Promise<ArrayBuffer>;
  text(): Promise<string>;
}

export interface IndexedAnimation {
  path: string;
  name: string;
  size: number;
  kind: 'bin' | 'json' | 'dof';
  document?: AnimationDocument;
  detectedRevision?: BinRevision;
  detectedFormat?: BinFormat;
  parsedMode?: BinRevisionMode;
  extractedAtlases?: LoadedAtlasInput[];
  dofFamily?: string;
  dofAppearance?: string;
  asset: LibraryAsset;
}

export interface IndexedCostume {
  path: string;
  name: string;
  label: string;
  token: string;
  asset: LibraryAsset;
  definition?: CostumeDefinition;
}

export interface AtlasLoadReport {
  requested: number;
  loaded: number;
  missing: string[];
  skipped: string[];
}

interface IndexedAudio {
  name: string;
  path: string;
  normalized: string;
  blob(): Promise<Blob>;
}

interface IndexedMuppetsAudioBinding {
  manifestPath: string;
  audioPath?: string;
}

function normalizePath(value: string): string {
  const segments: string[] = [];
  for (const segment of value.replaceAll('\\', '/').split('/')) {
    if (!segment || segment === '.') continue;
    if (segment === '..') {
      segments.pop();
      continue;
    }
    segments.push(segment);
  }
  return segments.join('/').toLowerCase();
}

function basename(value: string): string {
  const normalized = normalizePath(value);
  return normalized.slice(normalized.lastIndexOf('/') + 1);
}

function extension(value: string): string {
  const name = basename(value);
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index) : '';
}

function filePath(file: File): string {
  const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
  return relativePath || file.name;
}

function withoutExtension(value: string): string {
  const index = value.lastIndexOf('.');
  return index >= 0 ? value.slice(0, index) : value;
}

function dirname(value: string): string {
  const normalized = normalizePath(value);
  const index = normalized.lastIndexOf('/');
  return index >= 0 ? normalized.slice(0, index) : '';
}

function joinPath(...parts: string[]): string {
  return normalizePath(parts.filter(Boolean).join('/'));
}

function parentDirectories(value: string): string[] {
  const directories: string[] = [];
  let current = dirname(value);
  while (current) {
    directories.push(current);
    const parent = dirname(current);
    if (!parent || parent === current) break;
    current = parent;
  }
  return directories;
}

function commonPathDepth(left: string, right: string): number {
  const leftParts = normalizePath(left).split('/');
  const rightParts = normalizePath(right).split('/');
  let depth = 0;
  while (depth < leftParts.length && leftParts[depth] === rightParts[depth]) depth += 1;
  return depth;
}

function isRenderableSourceReference(reference: string): boolean {
  const clean = normalizePath(reference);
  if (!clean) return false;
  const ext = extension(clean);
  if (ext === '.xml' || isImagePath(clean)) return true;
  if (ext) return false;
  return !/^(?:solids|footage)(?:\/|$)/i.test(clean);
}

function renderSourceIds(document: AnimationDocument): Set<number> {
  const ids = new Set<number>();
  for (const animation of document.anims) {
    for (const layer of animation.layers) {
      if (layer.type !== undefined && layer.type !== 1) continue;
      if (Number.isFinite(layer.src) && layer.src >= 0) ids.add(layer.src);
      for (const sourceId of layer.atlas_override_ids ?? []) {
        if (Number.isFinite(sourceId) && sourceId >= 0) ids.add(sourceId);
      }
    }
  }
  return ids;
}

function mimeType(value: string): string {
  const ext = extension(value);
  if (ext === '.xml') return 'application/xml';
  if (ext === '.json') return 'application/json';
  if (ext === '.png') return 'image/png';
  if (ext === '.avif') return 'image/avif';
  if (ext === '.webp') return 'image/webp';
  if (ext === '.jpg' || ext === '.jpeg') return 'image/jpeg';
  if (ext === '.ogg') return 'audio/ogg';
  if (ext === '.wav') return 'audio/wav';
  if (ext === '.mp3') return 'audio/mpeg';
  if (ext === '.m4a' || ext === '.aac') return 'audio/mp4';
  if (ext === '.flac') return 'audio/flac';
  return 'application/octet-stream';
}

function isImagePath(value: string): boolean {
  return IMAGE_EXTENSIONS.includes(extension(value));
}

function directAsset(file: File): LibraryAsset {
  const path = normalizePath(filePath(file));
  return {
    path,
    name: basename(path),
    size: file.size,
    origin: 'file',
    blob: async () => file,
    arrayBuffer: async () => file.arrayBuffer(),
    text: async () => file.text(),
  };
}

function archivedAsset(path: string, entry: FileEntry): LibraryAsset {
  const blob = async (): Promise<Blob> => new Blob([await entry.arrayBuffer()], { type: mimeType(path) });
  return {
    path,
    name: basename(path),
    size: entry.uncompressedSize,
    origin: 'zip',
    blob,
    arrayBuffer: async () => entry.arrayBuffer(),
    text: async () => (await blob()).text(),
  };
}

function isDofBundlePath(value: string): boolean {
  const ext = extension(value);
  return ext === '.unity3d' || ext === '.bundle' || basename(value) === '__data';
}

function isLikelyAudioBundle(value: string): boolean {
  return /(?:^|[/_])(sounds?|audio)(?:[._/]|$)/i.test(value);
}

function isDofParticleBundlePath(value: string): boolean {
  const normalized = normalizePath(value);
  const name = basename(normalized);
  return name === 'particles.unity3d'
    || name === 'particles.bundle'
    || (normalized.includes('/particles/') && name === '__data');
}

function isDofStaticDataBundlePath(value: string): boolean {
  return /^static_data\.(?:unity3d|bundle)$/i.test(basename(value));
}

function dofIdentity(value: string): { family: string; appearance: string } {
  let stem = withoutExtension(basename(value)).replace(/^staticinfo_/i, '');
  const match = /^(.*)_chromatic_([^_]+)$/i.exec(stem);
  if (match) return { family: match[1]!.toLowerCase(), appearance: match[2]!.toLowerCase() };
  stem = stem.toLowerCase();
  return { family: stem, appearance: 'base' };
}

function costumeToken(value: string): string {
  const stem = withoutExtension(basename(value)).toLowerCase();
  const match = /(?:^|_)costume_([a-z0-9]+(?:_[a-z0-9]+)*)_\d+(?:_|$)/.exec(stem);
  if (match) return match[1]!;
  const loose = /(?:^|_)costume_([a-z0-9]+(?:_[a-z0-9]+)*)/.exec(stem);
  return loose?.[1] ?? '';
}

function animationToken(entry: IndexedAnimation): string {
  if (entry.kind === 'dof') return entry.dofFamily ?? '';
  let stem = withoutExtension(basename(entry.name)).toLowerCase();
  const club = /^club\d+-\d+_monster_(.+)$/.exec(stem);
  if (club) return club[1]!;
  stem = stem.replace(/^monster_/, '').replace(/_island\d+$/, '');
  return stem;
}

function directImageAtlas(reference: string, image: LoadedAtlasInput['image']): TextureAtlasData {
  const width = image instanceof HTMLImageElement ? image.naturalWidth : image.width;
  const height = image instanceof HTMLImageElement ? image.naturalHeight : image.height;
  const name = withoutExtension(basename(reference));
  const sprite: AtlasSprite = {
    name,
    x: 0,
    y: 0,
    width,
    height,
    pivotX: 0.5,
    pivotY: 0.5,
    offsetX: 0,
    offsetY: 0,
    originalWidth: width,
    originalHeight: height,
    rotated: false,
  };
  return {
    imagePath: reference,
    width,
    height,
    hires: false,
    sprites: new Map([[name.toLowerCase(), sprite]]),
  };
}

export class AssetLibrary {
  private readonly filesByPath = new Map<string, LibraryAsset>();
  private readonly filesByName = new Map<string, LibraryAsset[]>();
  private readonly animationPaths = new Set<string>();
  private readonly archives = new Set<string>();
  private readonly archiveReaders: Array<ZipReader<Blob>> = [];
  private readonly binParser = new BinParserWorkerClient();
  private readonly audioByPath = new Map<string, IndexedAudio>();
  private readonly muppetsAudioBindings = new Map<string, IndexedMuppetsAudioBinding[]>();
  private readonly costumePaths = new Set<string>();
  private readonly particleBundlePaths = new Set<string>();
  private readonly particleBundleAssets = new Map<string, LibraryAsset>();
  private readonly particlePrefabsById = new Map<string, DofParticlePrefabInput>();
  private readonly chromaticBundlePaths = new Set<string>();
  private readonly chromaticBundleAssets = new Map<string, LibraryAsset>();
  private readonly parsedChromaticBundlePaths = new Set<string>();
  private readonly chromaticGradients = new Map<DofChromaticColor, DofChromaticGradient>();
  private atlasLoadReportValue: AtlasLoadReport = { requested: 0, loaded: 0, missing: [], skipped: [] };
  private inputBytes = 0;
  readonly animations: IndexedAnimation[] = [];
  readonly costumes: IndexedCostume[] = [];

  get fileCount(): number {
    return this.filesByPath.size;
  }

  get totalBytes(): number {
    return this.inputBytes;
  }

  async dispose(): Promise<void> {
    this.binParser.dispose();
    for (const reader of this.archiveReaders) await reader.close().catch(() => undefined);
    this.archiveReaders.length = 0;
  }

  monsterPortraits(): MonsterPortrait[] {
    const portraits: MonsterPortrait[] = [];
    for (const asset of this.filesByPath.values()) {
      if (!IMAGE_EXTENSIONS.includes(extension(asset.name))) continue;
      const match = /^monster_portrait_square_(.+)$/.exec(withoutExtension(asset.name));
      if (!match || /(?:^|_)black(?:_|$)/.test(match[1]!)) continue;
      portraits.push({ token: match[1]!, path: asset.path, blob: () => asset.blob() });
    }
    return portraits;
  }

  async addFiles(files: Iterable<File>): Promise<{ added: number; animations: number; archives: number }> {
    let added = 0;
    let animationCount = 0;
    let archiveCount = 0;

    for (const file of files) {
      if (extension(file.name) === '.zip') {
        const archiveKey = `${filePath(file).toLowerCase()}|${file.size}|${file.lastModified}`;
        if (this.archives.has(archiveKey)) continue;
        this.archives.add(archiveKey);
        this.inputBytes += file.size;
        const indexed = await this.addArchive(file);
        added += indexed.added;
        animationCount += indexed.animations;
        archiveCount += 1;
        continue;
      }

      const asset = directAsset(file);
      if (!asset.path || this.filesByPath.has(asset.path)) continue;
      this.inputBytes += file.size;
      this.addAsset(asset);
      added += 1;
      this.indexAudio(asset);
      const isMuppetsAudioManifest = await this.indexMuppetsAudioManifest(asset);
      const isCostume = await this.indexCostume(asset, true);
      if (isDofStaticDataBundlePath(asset.path)) {
        this.indexDofChromaticBundle(asset);
      } else if (isDofParticleBundlePath(asset.path) || basename(asset.path) === '__data') {
        const particleBundle = await this.indexDofParticleBundle(asset);
        if (!particleBundle && isDofBundlePath(asset.path)) animationCount += this.indexDofBundle(asset);
      } else if (isDofBundlePath(asset.path)) {
        if (isLikelyAudioBundle(asset.path)) await this.indexDofAudioBundle(asset);
        else animationCount += this.indexDofBundle(asset);
      } else if (!isCostume && !isMuppetsAudioManifest) {
        animationCount += await this.indexAnimation(asset, true);
      }
    }

    this.animations.sort((left, right) => {
      if (left.kind === 'dof' && right.kind === 'dof' && left.dofFamily === right.dofFamily) {
        if (left.dofAppearance === 'base' && right.dofAppearance !== 'base') return -1;
        if (right.dofAppearance === 'base' && left.dofAppearance !== 'base') return 1;
      }
      return left.path.localeCompare(right.path);
    });
    return { added, animations: animationCount, archives: archiveCount };
  }

  private async addArchive(file: File): Promise<{ added: number; animations: number }> {
    const reader = new ZipReader(new BlobReader(file), { checkAmbiguity: true });
    const entries = await reader.getEntries({ checkAmbiguity: true });
    if (entries.length > 100_000) {
      await reader.close();
      throw new Error(`${file.name} contains too many ZIP entries to index safely.`);
    }
    this.archiveReaders.push(reader);
    let added = 0;
    let animations = 0;
    for (const entry of entries) {
      if (entry.directory) continue;
      const path = normalizePath(`${filePath(file)}/${entry.filename}`);
      if (!path || this.filesByPath.has(path)) continue;
      const asset = archivedAsset(path, entry);
      this.addAsset(asset);
      added += 1;
      this.indexAudio(asset);
      const isMuppetsAudioManifest = await this.indexMuppetsAudioManifest(asset);
      const isCostume = await this.indexCostume(asset, false);
      if (isDofStaticDataBundlePath(asset.path)) {
        this.indexDofChromaticBundle(asset);
      } else if (isDofParticleBundlePath(asset.path) || basename(asset.path) === '__data') {
        const particleBundle = await this.indexDofParticleBundle(asset);
        if (!particleBundle && isDofBundlePath(asset.path)) animations += this.indexDofBundle(asset);
      } else if (isDofBundlePath(asset.path)) {
        if (isLikelyAudioBundle(asset.path)) await this.indexDofAudioBundle(asset);
        else animations += this.indexDofBundle(asset);
      } else if (!isCostume && !isMuppetsAudioManifest) {
        animations += await this.indexAnimation(asset, false);
      }
    }
    return { added, animations };
  }

  private addAsset(asset: LibraryAsset): void {
    this.filesByPath.set(asset.path, asset);
    const namedFiles = this.filesByName.get(asset.name) ?? [];
    namedFiles.push(asset);
    this.filesByName.set(asset.name, namedFiles);
  }

  private async indexAnimation(asset: LibraryAsset, inspectJson: boolean): Promise<number> {
    const ext = extension(asset.name);
    if (!ANIMATION_EXTENSIONS.has(ext) || this.animationPaths.has(asset.path)) return 0;

    if (ext === '.json' && inspectJson) {
      try {
        const value: unknown = JSON.parse(await asset.text());
        if (!isAnimationDocument(value)) return 0;
        const document = normalizeAnimationDocument(value);
        this.animations.push({
          path: asset.path,
          name: asset.name,
          size: asset.size,
          kind: 'json',
          document,
          detectedRevision: (document.source_revision ?? document.rev) as BinRevision,
          asset,
        });
      } catch {
        return 0;
      }
    } else {
      this.animations.push({
        path: asset.path,
        name: asset.name,
        size: asset.size,
        kind: ext === '.bin' ? 'bin' : 'json',
        asset,
      });
    }

    this.animationPaths.add(asset.path);
    return 1;
  }

  private indexDofBundle(asset: LibraryAsset): number {
    if (this.animationPaths.has(asset.path)) return 0;
    const identity = dofIdentity(asset.name);
    this.animations.push({
      path: asset.path,
      name: asset.name,
      size: asset.size,
      kind: 'dof',
      detectedRevision: 6,
      dofFamily: identity.family,
      dofAppearance: identity.appearance,
      asset,
    });
    this.animationPaths.add(asset.path);
    return 1;
  }

  private indexAudio(asset: LibraryAsset): void {
    if (!AUDIO_EXTENSIONS.has(extension(asset.path)) || this.audioByPath.has(asset.path)) return;
    this.audioByPath.set(asset.path, {
      name: asset.name,
      path: asset.path,
      normalized: normalizeAudioKey(asset.name),
      blob: () => asset.blob(),
    });
  }

  private async indexMuppetsAudioManifest(asset: LibraryAsset): Promise<boolean> {
    if (!/^\d{3}[_-].+\.bin$/i.test(asset.name)) return false;
    const manifest = parseMuppetsAudioManifest(await asset.arrayBuffer());
    if (!manifest) return false;
    for (const binding of manifest.bindings) {
      const key = normalizeAudioKey(binding.trackName);
      if (!key) continue;
      const indexed = this.muppetsAudioBindings.get(key) ?? [];
      indexed.push({ manifestPath: asset.path, audioPath: binding.audioPath });
      this.muppetsAudioBindings.set(key, indexed);
    }
    return true;
  }

  private addExtractedAudio(clips: AudioClipInput[]): void {
    clips.forEach((clip) => {
      const key = normalizePath(clip.path);
      if (this.audioByPath.has(key)) return;
      this.audioByPath.set(key, {
        name: clip.name,
        path: clip.path,
        normalized: normalizeAudioKey(clip.name),
        blob: async () => clip.blob,
      });
    });
  }

  private async indexDofAudioBundle(asset: LibraryAsset): Promise<void> {
    if (asset.size > 512 * 1024 * 1024) return;
    try {
      const { extractDofBundle } = await loadDofBundleTools();
      const extracted = await extractDofBundle(await asset.arrayBuffer(), asset.path);
      this.addExtractedAudio(extracted.audio);
    } catch {
      // Audio discovery is optional; malformed or unsupported bundles remain recoverable.
    }
  }

  private async indexDofParticleBundle(asset: LibraryAsset): Promise<boolean> {
    if (this.particleBundlePaths.has(asset.path)) return true;
    if (asset.size > 128 * 1024 * 1024) return false;
    this.particleBundlePaths.add(asset.path);
    this.particleBundleAssets.set(asset.path, asset);
    return true;
  }

  private indexDofChromaticBundle(asset: LibraryAsset): void {
    if (this.chromaticBundlePaths.has(asset.path)) return;
    this.chromaticBundlePaths.add(asset.path);
    this.chromaticBundleAssets.set(asset.path, asset);
    this.chromaticGradients.clear();
    this.animations.forEach((entry) => {
      if (entry.kind !== 'dof' || entry.dofAppearance === 'base') return;
      entry.document = undefined;
      entry.extractedAtlases = undefined;
    });
  }

  private async dofChromaticGradient(
    color: DofChromaticColor,
  ): Promise<DofChromaticGradient | undefined> {
    if (this.chromaticGradients.has(color)) return this.chromaticGradients.get(color);
    for (const asset of this.chromaticBundleAssets.values()) {
      if (this.parsedChromaticBundlePaths.has(asset.path)) continue;
      this.parsedChromaticBundlePaths.add(asset.path);
      try {
        const { extractDofChromaticGradients } = await loadDofBundleTools();
        const gradients = await extractDofChromaticGradients(await asset.arrayBuffer());
        gradients.forEach((gradient, key) => this.chromaticGradients.set(key, gradient));
      } catch {
        // The sampled desktop ramps remain available if a static_data build is unsupported.
      }
      if (this.chromaticGradients.has(color)) break;
    }
    return this.chromaticGradients.get(color);
  }

  private async ensureDofParticlePrefabs(document: AnimationDocument): Promise<void> {
    const missing = new Set(document.anims.flatMap((track) => (
      (track.particle_emitters ?? []).map((emitter) => emitter.prefabPathId)
    )).filter((pathId) => !this.particlePrefabsById.has(pathId)));
    if (!missing.size) return;
    for (const asset of this.particleBundleAssets.values()) {
      try {
        const { extractDofParticleLibrary } = await loadDofBundleTools();
        const prefabs = await extractDofParticleLibrary(await asset.arrayBuffer(), missing);
        prefabs.forEach((prefab) => {
          this.particlePrefabsById.set(prefab.pathId, prefab);
          missing.delete(prefab.pathId);
        });
      } catch (error) {
        console.warn(
          `[DOF particles] Could not read ${asset.name}: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
      if (!missing.size) break;
    }
  }

  dofParticlePrefabs(): DofParticlePrefabInput[] {
    return [...this.particlePrefabsById.values()];
  }

  private async indexCostume(asset: LibraryAsset, inspectJson: boolean): Promise<boolean> {
    if (this.costumePaths.has(asset.path)) return true;
    if (!/(?:^|_)costume_/i.test(asset.name)) return false;
    const ext = extension(asset.path);
    if (ext !== '.bin' && ext !== '.json') return false;
    let definition: CostumeDefinition | undefined;
    if (ext === '.json' && inspectJson) {
      try {
        const value: unknown = JSON.parse(await asset.text());
        if (!isCostumeDefinition(value)) return false;
        definition = value;
      } catch {
        return false;
      }
    }
    const token = costumeToken(asset.name);
    if (!token) return false;
    const stem = withoutExtension(asset.name).replace(/^.*?costume_/i, '').replaceAll('_', ' ');
    this.costumes.push({
      path: asset.path,
      name: asset.name,
      label: stem.replace(/\b\w/g, (letter) => letter.toUpperCase()),
      token,
      asset,
      definition,
    });
    this.costumePaths.add(asset.path);
    this.costumes.sort((left, right) => left.label.localeCompare(right.label));
    return true;
  }

  async loadDocument(entry: IndexedAnimation, revisionMode: BinRevisionMode): Promise<AnimationDocument> {
    if (entry.size > 64 * 1024 * 1024) {
      throw new Error(`${entry.name} exceeds the 64 MB browser animation limit.`);
    }
    if (entry.kind === 'json') {
      if (entry.document) return entry.document;
      const value: unknown = JSON.parse(await entry.asset.text());
      if (!isAnimationDocument(value)) throw new Error(`${entry.name} is not an animation JSON document.`);
      entry.document = normalizeAnimationDocument(value);
      entry.detectedRevision = (entry.document.source_revision ?? entry.document.rev) as BinRevision;
      return entry.document;
    }

    if (entry.kind === 'dof') {
      if (entry.document && entry.extractedAtlases) {
        await this.ensureDofParticlePrefabs(entry.document);
        return entry.document;
      }
      if (entry.size > 512 * 1024 * 1024) {
        throw new Error(`${entry.name} exceeds the 512 MB browser Unity bundle limit.`);
      }
      const { extractDofBundle } = await loadDofBundleTools();
      const color = dofChromaticColor(entry.name);
      const extracted = await extractDofBundle(
        await entry.asset.arrayBuffer(),
        entry.path,
        color ? await this.dofChromaticGradient(color) : undefined,
      );
      this.addExtractedAudio(extracted.audio);
      if (!extracted.document || !extracted.document.anims.length) {
        throw new Error(`${entry.name} does not contain any DOF ANIMBBB animations.`);
      }
      entry.document = extracted.document;
      entry.extractedAtlases = extracted.atlases;
      await this.ensureDofParticlePrefabs(entry.document);
      return entry.document;
    }

    if (entry.document && entry.parsedMode === revisionMode) return entry.document;
    const parsed = await this.binParser.parse(await entry.asset.blob(), revisionMode, entry.path);
    entry.document = parsed.document;
    entry.detectedRevision = parsed.revision;
    entry.detectedFormat = parsed.format;
    entry.parsedMode = revisionMode;
    return entry.document;
  }

  private resolve(reference: string, relativeTo?: string): LibraryAsset | undefined {
    const normalized = normalizePath(reference);
    if (!normalized) return undefined;
    const context = normalizePath(relativeTo ?? '');
    const base = basename(normalized);
    const probes = new Set<string>([normalized]);
    if (normalized.startsWith('data/')) probes.add(normalized.slice('data/'.length));

    if (context) {
      for (const parent of parentDirectories(context)) {
        probes.add(joinPath(parent, normalized));
        if (normalized.startsWith('data/')) probes.add(joinPath(parent, normalized.slice('data/'.length)));
        if (extension(base) === '.xml') {
          probes.add(joinPath(parent, 'xml_resources', base));
          probes.add(joinPath(parent, 'xml_resources_dof', base));
        } else if (isImagePath(base)) {
          probes.add(joinPath(parent, 'gfx', 'monsters', base));
          probes.add(joinPath(parent, 'gfx', 'costumes', base));
          probes.add(joinPath(parent, 'gfx', base));
        }
      }
    }

    for (const probe of probes) {
      const exact = this.filesByPath.get(probe);
      if (exact) return exact;
    }

    const candidates = new Map<string, LibraryAsset>();
    for (const probe of probes) {
      for (const asset of this.filesByName.get(basename(probe)) ?? []) candidates.set(asset.path, asset);
    }
    if (!candidates.size) return undefined;
    return [...candidates.values()].sort((left, right) => {
      const score = (asset: LibraryAsset): number => {
        const suffixMatch = [...probes].some((probe) => (
          asset.path === probe || asset.path.endsWith(`/${probe}`)
        ));
        return (suffixMatch ? 1_000_000 : 0)
          + commonPathDepth(asset.path, context) * 10_000
          - Math.abs(asset.path.split('/').length - context.split('/').length);
      };
      const difference = score(right) - score(left);
      return difference || left.path.localeCompare(right.path);
    })[0];
  }

  private resolveImage(reference: string, relativeTo?: string): LibraryAsset | undefined {
    const direct = this.resolve(reference, relativeTo);
    if (direct) return direct;
    const stem = withoutExtension(reference);
    for (const imageExtension of IMAGE_EXTENSIONS) {
      const alternate = this.resolve(`${stem}${imageExtension}`, relativeTo);
      if (alternate) return alternate;
    }
    return undefined;
  }

  private resolveAtlas(reference: string, relativeTo?: string): LibraryAsset | undefined {
    const clean = reference.trim();
    if (!clean) return undefined;
    const references = extension(clean) ? [clean] : [`${clean}.xml`, clean];
    for (const candidate of references) {
      const asset = this.resolve(candidate, relativeTo);
      if (asset && extension(asset.path) === '.xml') return asset;
    }
    return undefined;
  }

  atlasLoadReport(): AtlasLoadReport {
    return {
      ...this.atlasLoadReportValue,
      missing: [...this.atlasLoadReportValue.missing],
      skipped: [...this.atlasLoadReportValue.skipped],
    };
  }

  async loadAtlases(
    document: AnimationDocument,
    entry?: IndexedAnimation,
    relativeTo?: string,
  ): Promise<LoadedAtlasInput[]> {
    if (entry?.kind === 'dof' && entry.extractedAtlases) {
      this.atlasLoadReportValue = {
        requested: entry.extractedAtlases.length,
        loaded: entry.extractedAtlases.length,
        missing: [],
        skipped: [],
      };
      return entry.extractedAtlases;
    }
    const atlases: LoadedAtlasInput[] = [];
    const missing: string[] = [];
    const skipped: string[] = [];
    const usedSourceIds = renderSourceIds(document);
    const contextPath = entry?.path ?? relativeTo;
    const renderSources = document.sources.filter((source) => (
      usedSourceIds.has(source.id) && isRenderableSourceReference(source.src)
    ));

    for (const source of document.sources) {
      if (!usedSourceIds.has(source.id) || isRenderableSourceReference(source.src)) continue;
      if (source.src.trim()) skipped.push(source.src);
    }

    for (const source of renderSources) {
      if (isImagePath(source.src)) {
        const imageFile = this.resolveImage(source.src, contextPath);
        if (!imageFile) {
          missing.push(source.src);
          continue;
        }
        if (imageFile.size > 512 * 1024 * 1024) {
          throw new Error(`${imageFile.name} exceeds the 512 MB browser texture limit.`);
        }
        const decoded = await decodeTextureImage(await imageFile.blob());
        atlases.push({ sourceId: source.id, data: directImageAtlas(source.src, decoded.image), ...decoded });
        continue;
      }
      const xmlFile = this.resolveAtlas(source.src, contextPath);
      if (!xmlFile) {
        missing.push(source.src);
        continue;
      }
      if (xmlFile.size > 16 * 1024 * 1024) {
        throw new Error(`${xmlFile.name} exceeds the 16 MB browser atlas XML limit.`);
      }
      const atlas = parseTextureAtlas(await xmlFile.text());
      const imageReference = atlas.imagePath || source.src.replace(/\.xml$/i, '.png');
      const imageFile = this.resolveImage(imageReference, xmlFile.path);
      if (!imageFile) {
        missing.push(imageReference);
        continue;
      }
      if (imageFile.size > 512 * 1024 * 1024) {
        throw new Error(`${imageFile.name} exceeds the 512 MB browser texture limit.`);
      }
      atlases.push({ sourceId: source.id, sourceName: xmlFile.path, data: atlas, ...await decodeTextureImage(await imageFile.blob()) });
    }

    this.atlasLoadReportValue = {
      requested: renderSources.length,
      loaded: atlases.length,
      missing: [...new Set(missing)],
      skipped: [...new Set(skipped)],
    };
    if (missing.length && !atlases.length) {
      const shown = missing.slice(0, 5).join(', ');
      const remainder = missing.length > 5 ? ` and ${missing.length - 5} more` : '';
      throw new Error(
        `Missing required texture asset${missing.length === 1 ? '' : 's'}: ${shown}${remainder}. `
        + 'Open the game folder or add the matching atlas XML and spritesheet.',
      );
    }
    return atlases;
  }

  dofVariants(entry: IndexedAnimation): IndexedAnimation[] {
    if (entry.kind !== 'dof' || !entry.dofFamily) return [];
    return this.animations
      .filter((candidate) => candidate.kind === 'dof' && candidate.dofFamily === entry.dofFamily)
      .sort((left, right) => {
        if (left.dofAppearance === 'base') return -1;
        if (right.dofAppearance === 'base') return 1;
        return (left.dofAppearance ?? '').localeCompare(right.dofAppearance ?? '');
      });
  }

  costumesFor(entry: IndexedAnimation): IndexedCostume[] {
    if (entry.kind === 'dof') return [];
    const token = animationToken(entry);
    return this.costumes.filter((costume) => costume.token === token || costume.token.endsWith(`_${token}`));
  }

  private async loadAtlasReference(
    reference: string,
    sourceId: number,
    relativeTo?: string,
  ): Promise<LoadedAtlasInput | undefined> {
    const clean = reference.trim();
    if (!clean || /^(?:empty|none)$/i.test(clean)) return undefined;
    const xmlFile = this.resolveAtlas(clean, relativeTo);
    if (!xmlFile) return undefined;
    const atlas = parseTextureAtlas(await xmlFile.text());
    const imageFile = this.resolveImage(atlas.imagePath || clean.replace(/\.xml$/i, '.png'), xmlFile.path);
    if (!imageFile) return undefined;
    return { sourceId, sourceName: xmlFile.path, data: atlas, ...await decodeTextureImage(await imageFile.blob()) };
  }

  async loadCostumeAppearance(
    costume: IndexedCostume,
    baseDocument: AnimationDocument,
    baseAtlases: LoadedAtlasInput[],
  ): Promise<{
    document: AnimationDocument;
    atlases: LoadedAtlasInput[];
    shaderTextures: CostumeShaderTextureInput[];
    attachments: CostumeAttachmentInput[];
  }> {
    if (!costume.definition) {
      const buffer = await costume.asset.arrayBuffer();
      if (extension(costume.path) === '.json') {
        const value: unknown = JSON.parse(new TextDecoder().decode(buffer));
        if (!isCostumeDefinition(value)) throw new Error(`${costume.name} is not a costume definition.`);
        costume.definition = value;
      } else {
        costume.definition = parseCostumeBin(buffer);
      }
    }
    const sheetNames = new Set<string>();
    costume.definition.remaps.forEach((remap) => {
      if (remap.sheet) sheetNames.add(remap.sheet);
    });
    costume.definition.sheet_remaps.forEach((remap) => {
      if (remap.to) sheetNames.add(remap.to);
    });
    costume.definition.clone_layers?.forEach((clone) => {
      if (/\.xml$/i.test(clone.sheet)) sheetNames.add(clone.sheet);
    });
    let sourceId = Math.max(-1, ...baseAtlases.map((atlas) => atlas.sourceId)) + 1;
    const costumeAtlases: LoadedAtlasInput[] = [];
    for (const sheet of sheetNames) {
      const atlas = await this.loadAtlasReference(sheet, sourceId, costume.path);
      if (!atlas) continue;
      costumeAtlases.push(atlas);
      sourceId += 1;
    }
    const ids = costumeAtlases.map((atlas) => atlas.sourceId);
    const shaderTextures: CostumeShaderTextureInput[] = [];
    const loadedShaderNames = new Set<string>();
    const texturePrefix = this.costumeTexturePrefix(costume.name);
    for (const shader of costume.definition.apply_shader ?? []) {
      const behavior = costumeShaderBehavior(shader.resource);
      if (!behavior?.requiresTexture || !texturePrefix) continue;
      const shaderKey = shader.resource.toLowerCase();
      if (loadedShaderNames.has(shaderKey)) continue;
      const nodeKey = shader.node.trim().toLowerCase().replace(/\.[^.]+$/, '');
      const remap = costume.definition.remaps.find((candidate) => (
        candidate.display_name.trim().toLowerCase().replace(/\.[^.]+$/, '') === nodeKey
      ));
      const remapBase = remap?.sheet
        ? withoutExtension(remap.sheet).replace(/_sheet$/i, '')
        : undefined;
      const suffix = behavior.textureSuffix ?? '';
      const textureFile = (remapBase ? this.resolveImage(`${remapBase}${suffix}`, costume.path) : undefined)
        ?? this.resolveImage(`${texturePrefix}${suffix}`, costume.path);
      if (!textureFile) continue;
      loadedShaderNames.add(shaderKey);
      shaderTextures.push({
        shaderName: shader.resource,
        ...await decodeTextureImage(await textureFile.blob()),
      });
    }
    const attachments: CostumeAttachmentInput[] = [];
    const attachmentResources = new Map<string, {
      document: AnimationDocument;
      atlases: LoadedAtlasInput[];
    }>();
    for (const raw of costume.definition.ae_anim_layers ?? []) {
      const resource = String(raw.resource ?? '').trim();
      const animationName = String(raw.animation ?? '').trim();
      const targetLayer = String(raw.attach_to ?? '').trim();
      if (!resource || !targetLayer) continue;
      const resourceKey = normalizePath(resource);
      let loaded = attachmentResources.get(resourceKey);
      if (!loaded) {
        const asset = this.resolve(resource, costume.path);
        if (!asset || !ANIMATION_EXTENSIONS.has(extension(asset.path))) continue;
        let document: AnimationDocument;
        if (extension(asset.path) === '.json') {
          const value: unknown = JSON.parse(await asset.text());
          if (!isAnimationDocument(value)) continue;
          document = normalizeAnimationDocument(value);
        } else {
          document = (await this.binParser.parse(await asset.blob(), 'auto', asset.path)).document;
        }
        loaded = { document, atlases: await this.loadAtlases(document, undefined, asset.path) };
        attachmentResources.set(resourceKey, loaded);
      }
      const track = loaded.document.anims.find((candidate) => (
        !animationName || candidate.name.toLowerCase() === animationName.toLowerCase()
      ));
      if (!track) continue;
      const rawOffset = Number(raw.time_offset ?? raw.time_scale ?? 0);
      const rawTempo = Number(raw.tempo_multiplier ?? 1);
      attachments.push({
        name: animationName || targetLayer,
        targetLayer,
        track: { ...track, layers: track.layers.map((layer) => ({ ...layer, costume_pivot_context: true })) },
        blendVersion: loaded.document.blend_version,
        atlases: loaded.atlases,
        timeOffset: Number.isFinite(rawOffset) ? rawOffset : 0,
        tempoMultiplier: Number.isFinite(rawTempo) ? Math.max(0.1, rawTempo) : 1,
        loop: raw.loop === undefined ? true : Boolean(raw.loop),
        rootLayer: String(raw.root_layer ?? raw.root_socket ?? '').trim() || undefined,
      });
    }
    for (const attachment of attachments) {
      attachment.alignmentMode = costumeAttachmentAlignment(attachment, costume.definition.remaps, costumeAtlases);
    }
    return {
      document: applyCostumeDefinition(baseDocument, costume.definition, ids, costumeAtlases),
      atlases: [...costumeAtlases, ...baseAtlases],
      shaderTextures,
      attachments,
    };
  }

  private costumeTexturePrefix(costumeName: string): string | undefined {
    let stem = withoutExtension(basename(costumeName));
    let clubPrefix = '';
    const club = /^(club\d+-\d+)_costume_(.+)$/i.exec(stem);
    if (club) {
      clubPrefix = `${club[1]}_`;
      stem = `costume_${club[2]}`;
    }
    const match = /^costume_(.+)_(\d+)$/i.exec(stem);
    if (!match) return undefined;
    return `${clubPrefix}monster_${match[1]}_costume_${match[2]}`;
  }

  async resolveAudio(track: AnimationTrack, entry?: IndexedAnimation): Promise<AudioClipInput | undefined> {
    const document = entry?.document;
    const context = resolveAudioContext({
      kind: entry?.kind,
      detectedFormat: entry?.detectedFormat,
      sourceFormat: document?.source_format,
      entryName: entry?.name,
    });
    if (shouldSkipAnimationAudio(track.name, context, entry ? animationToken(entry) : '')) return undefined;

    if (context === 'muppets') {
      const bindings = [...(this.muppetsAudioBindings.get(normalizeAudioKey(track.name)) ?? [])]
        .sort((left, right) => {
          const difference = commonPathDepth(right.manifestPath, entry?.path ?? '')
            - commonPathDepth(left.manifestPath, entry?.path ?? '');
          return difference || left.manifestPath.localeCompare(right.manifestPath);
        });
      if (bindings.length) {
        const binding = bindings[0]!;
        if (!binding.audioPath) return undefined;
        const asset = this.resolve(binding.audioPath, binding.manifestPath);
        return asset ? { name: asset.name, path: asset.path, blob: await asset.blob() } : undefined;
      }
    }

    for (const layer of track.layers) {
      if (layer.type !== 2 && layer.type !== 3) continue;
      const source = document?.sources.find((candidate) => candidate.id === layer.src)
        ?? document?.sources[layer.src];
      if (!source || !AUDIO_EXTENSIONS.has(extension(source.src))) continue;
      const asset = this.resolve(source.src, entry?.path);
      if (asset) return { name: asset.name, path: asset.path, blob: await asset.blob() };
    }

    const candidates = buildAnimationAudioCandidates(track.name, context, entry?.name);
    for (const candidate of candidates) {
      const matches = [...this.audioByPath.values()]
        .filter((audio) => audio.normalized === candidate)
        .sort((left, right) => {
          const difference = commonPathDepth(right.path, entry?.path ?? '')
            - commonPathDepth(left.path, entry?.path ?? '');
          return difference || left.path.localeCompare(right.path);
        });
      const match = matches[0];
      if (match) return { name: match.name, path: match.path, blob: await match.blob() };
    }
    return undefined;
  }
}
