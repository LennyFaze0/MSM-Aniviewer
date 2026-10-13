import {
  Application,
  Container,
  ImageSource,
  Matrix,
  Rectangle,
  Sprite,
  Texture,
} from 'pixi.js';
import {
  animationDuration,
  animationRenderOrder,
  animationUsesDepth,
  calculateWorldStates,
} from '../core/animation';
import {
  costumeShaderBehavior,
  costumeShaderColor,
  costumeShaderFrame,
  costumeShaderUV,
  type CostumeShaderBehavior,
} from '../core/costume-shader';
import { resolveLayerColor } from '../core/layer-color';
import { animationBlendVersion, layerAnimationBlend } from '../core/animation-blend';
import { spriteMeshAnchor } from '../core/sprite-geometry';
import { costumeAlignmentPoint, costumeDrawOffset } from '../core/costume-appearance';
import { applyWorldLayerEdits, layerAnchor, pointHitsQuad, type LayerTransformEdit, type ViewPoint } from '../core/layer-transform';
import { applyTransformOperations, cloneOperations, type TransformOperation } from '../core/edit-constraints';
import { applyIKRigs, cloneIKRigs, jointOffset, jointWorld, type IKRig, type IKPose } from '../core/ik-constraints';
import type {
  AnimationDocument,
  AnimationLayer,
  AnimationTrack,
  AtlasSprite,
  ColorTuple,
  CostumeAttachmentInput,
  CostumeShaderTextureInput,
  DofParticleEmitter,
  DofParticleKey,
  DofParticlePrefabInput,
  DecodedTextureImage,
  LoadedAtlasInput,
  TextureImageResource,
  TextureAtlasData,
  WorldState,
} from '../core/types';

interface RuntimeAtlas {
  sourceId: number;
  data: TextureAtlasData;
  source: ImageSource;
  textures: Map<string, Texture>;
}

interface ResolvedSpriteTexture {
  sprite: AtlasSprite;
  texture: Texture;
  atlas: TextureAtlasData;
}

interface RuntimeShaderTexture {
  behavior: CostumeShaderBehavior;
  source: ImageSource;
  width: number;
  height: number;
  textures: Map<string, Texture>;
}

interface RuntimeAttachment {
  input: CostumeAttachmentInput;
  atlases: Map<number, RuntimeAtlas>;
  sprites: Map<number, Sprite>;
  targetLayerId?: number;
  duration: number;
  usesDepth: boolean;
}

interface RuntimeParticlePrefab {
  input: DofParticlePrefabInput;
  source: ImageSource;
  texture: Texture;
}

export interface AnimationExportBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LayerViewGeometry {
  id: number;
  corners: ViewPoint[];
  pivot: ViewPoint;
}

export interface AnimationExportInfo {
  name: string;
  duration: number;
  currentTime: number;
  playbackRate: number;
  animationBounds: AnimationExportBounds;
}

export interface AnimationExportCaptureOptions {
  time: number;
  bounds: AnimationExportBounds;
  resolution?: number;
  background?: [red: number, green: number, blue: number, alpha: number];
}

export interface AnimationExportSession {
  readonly info: AnimationExportInfo;
  measureContentBounds(
    sampleTimes: readonly number[],
    padding?: number,
    signal?: AbortSignal,
    onProgress?: (completed: number, total: number) => void,
  ): Promise<AnimationExportBounds>;
  capturePng(options: AnimationExportCaptureOptions): Promise<Blob>;
  captureCanvas(options: AnimationExportCaptureOptions): HTMLCanvasElement | OffscreenCanvas;
  captureVideoFrame(options: AnimationExportCaptureOptions, timing: { timestamp: number; duration: number }): VideoFrame;
}

interface ExportCanvas {
  toBlob?: (callback: (blob: Blob | null) => void, type?: string, quality?: number) => void;
  convertToBlob?: (options?: { type?: string; quality?: number }) => Promise<Blob>;
}

async function canvasToPngBlob(canvas: ExportCanvas): Promise<Blob> {
  if (canvas.convertToBlob) return canvas.convertToBlob({ type: 'image/png' });
  if (canvas.toBlob) {
    return new Promise<Blob>((resolve, reject) => {
      canvas.toBlob!((blob) => {
        if (blob) resolve(blob);
        else reject(new Error('The browser could not encode the rendered frame as PNG.'));
      }, 'image/png');
    });
  }
  throw new Error('This browser cannot encode canvas images as PNG.');
}

function checkExportAbort(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  throw signal.reason instanceof Error
    ? signal.reason
    : new DOMException('Export cancelled.', 'AbortError');
}

function textureForSprite(atlas: RuntimeAtlas, sprite: AtlasSprite): Texture {
  const cached = atlas.textures.get(sprite.name.toLowerCase());
  if (cached) return cached;

  const scale = atlas.data.hires ? 0.5 : 1;
  const displayedWidth = (sprite.rotated ? sprite.height : sprite.width) * scale;
  const displayedHeight = (sprite.rotated ? sprite.width : sprite.height) * scale;
  const originalWidth = Math.max(displayedWidth, sprite.originalWidth * scale);
  const originalHeight = Math.max(displayedHeight, sprite.originalHeight * scale);
  const texture = new Texture({
    source: atlas.source,
    // A 2x TextureSource exposes logical dimensions to Pixi, so atlas frame
    // coordinates must use the same logical-pixel space as orig and trim.
    frame: new Rectangle(
      sprite.x * scale,
      sprite.y * scale,
      sprite.width * scale,
      sprite.height * scale,
    ),
    orig: new Rectangle(0, 0, originalWidth, originalHeight),
    trim: new Rectangle(
      sprite.offsetX * scale,
      sprite.offsetY * scale,
      displayedWidth,
      displayedHeight,
    ),
    rotate: sprite.rotated ? 2 : 0,
  });
  atlas.textures.set(sprite.name.toLowerCase(), texture);
  return texture;
}

function blendMode(value = 0): 'normal' | 'add' | 'multiply' | 'screen' {
  if (value === 2) return 'add';
  if (value === 6) return 'multiply';
  if (value === 7) return 'screen';
  return 'normal';
}

function imageSize(image: TextureImageResource): { width: number; height: number } {
  return typeof HTMLImageElement !== 'undefined' && image instanceof HTMLImageElement
    ? { width: image.naturalWidth, height: image.naturalHeight }
    : { width: image.width, height: image.height };
}

function imageSource(
  input: DecodedTextureImage,
  resolution = 1,
  scaleMode: 'linear' | 'nearest' = 'linear',
): ImageSource {
  const source = new ImageSource({
    resource: input.image,
    alphaMode: input.alphaMode,
    resolution,
  });
  source.scaleMode = scaleMode;
  return source;
}

function normalizedLayerName(value: string): string {
  return value.trim().toLowerCase().replace(/\.[^.]+$/, '').replace(/\s+\d+$/, '');
}

function runtimeAtlases(
  inputs: LoadedAtlasInput[],
  scaleMode: 'linear' | 'nearest' = 'linear',
): Map<number, RuntimeAtlas> {
  const atlases = new Map<number, RuntimeAtlas>();
  for (const input of inputs) {
    const source = imageSource(input, input.data.hires ? 2 : 1, scaleMode);
    atlases.set(input.sourceId, {
      sourceId: input.sourceId,
      data: input.data,
      source,
      textures: new Map(),
    });
  }
  return atlases;
}

function destroyAtlases(atlases: Map<number, RuntimeAtlas>): void {
  for (const atlas of atlases.values()) {
    atlas.textures.forEach((texture) => texture.destroy(false));
    atlas.source.destroy();
  }
  atlases.clear();
}

function particleChannel(keys: DofParticleKey[] | undefined, time: number, fallback: number): number {
  if (!keys?.length) return fallback;
  let previous = keys[0]!;
  if (time <= previous.time) return previous.value;
  for (let index = 1; index < keys.length; index += 1) {
    const next = keys[index]!;
    if (next.time > time) {
      if (previous.keytype !== 1) return previous.value;
      const span = next.time - previous.time;
      const ratio = span > 0 ? (time - previous.time) / span : 0;
      return previous.value + (next.value - previous.value) * ratio;
    }
    previous = next;
  }
  return previous.value;
}

function seededRandom(seed: number, salt: number): number {
  let value = (seed ^ Math.imul(salt + 1, 0x9e3779b1)) >>> 0;
  value ^= value << 13;
  value ^= value >>> 17;
  value ^= value << 5;
  return (value >>> 0) / 0x100000000;
}

function stringSeed(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function rangeValue(range: [number, number], ratio: number): number {
  return range[0] + (range[1] - range[0]) * ratio;
}

function sampleScalarKeys(keys: Array<{ time: number; value: number }>, time: number, fallback = 1): number {
  if (!keys.length) return fallback;
  let previous = keys[0]!;
  if (time <= previous.time) return previous.value;
  for (let index = 1; index < keys.length; index += 1) {
    const next = keys[index]!;
    if (next.time > time) {
      const span = next.time - previous.time;
      const ratio = span > 0 ? (time - previous.time) / span : 0;
      return previous.value + (next.value - previous.value) * ratio;
    }
    previous = next;
  }
  return previous.value;
}

function sampleColorKeys(keys: Array<{ time: number; color: ColorTuple }>, time: number): ColorTuple {
  if (!keys.length) return [1, 1, 1, 1];
  let previous = keys[0]!;
  if (time <= previous.time) return previous.color;
  for (let index = 1; index < keys.length; index += 1) {
    const next = keys[index]!;
    if (next.time > time) {
      const span = next.time - previous.time;
      const ratio = span > 0 ? (time - previous.time) / span : 0;
      return previous.color.map((value, channel) => (
        value + (next.color[channel]! - value) * ratio
      )) as ColorTuple;
    }
    previous = next;
  }
  return previous.color;
}

export class AnimationRenderer {
  private readonly application = new Application();
  private readonly scene = new Container();
  private readonly layerSprites = new Map<number, Sprite>();
  private readonly visibleLayers = new Map<number, boolean>();
  private layerEdits: ReadonlyMap<number, LayerTransformEdit> = new Map();
  private transformOperations?: readonly TransformOperation[];
  private ikRigs: IKRig[] = [];
  private ikPoses: IKPose[] = [];
  private worldStates = new Map<number, WorldState>();
  private readonly atlases = new Map<number, RuntimeAtlas>();
  private readonly shaderTextures = new Map<string, RuntimeShaderTexture>();
  private readonly attachments: RuntimeAttachment[] = [];
  private readonly particlePrefabs = new Map<string, RuntimeParticlePrefab>();
  private particleSprites: Sprite[] = [];
  private document?: AnimationDocument;
  private blendVersion = 1;
  private track?: AnimationTrack;
  private currentTime = 0;
  private duration = 0;
  private playbackSpeed = 1;
  private playing = false;
  private loop = true;
  private zoom = 1;
  private editorInset = 0;
  private initialized = false;
  private usesDepth = false;
  private renderOrderKey = '';
  private exportActive = false;
  private spriteScaleMode: 'linear' | 'nearest' = 'linear';
  private particleCap = 1000;
  private forceParticleWorldSpace = true;
  private zoomToCursor = true;
  onTimeChange?: (time: number, duration: number) => void;
  onPlaybackChange?: (playing: boolean) => void;
  onLoop?: (completedCycles: number) => void;
  onViewChange?: () => void;
  playbackTimeSource?: (elapsedSeconds: number) => number | undefined;

  constructor(private readonly host: HTMLElement) {}

  async initialize(): Promise<void> {
    if (this.initialized) return;
    await this.application.init({
      resizeTo: this.host,
      preference: 'webgl',
      preferWebGLVersion: 2,
      antialias: true,
      autoDensity: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
      backgroundAlpha: 0,
      powerPreference: 'high-performance',
    });
    this.application.canvas.className = 'viewport-canvas';
    this.host.appendChild(this.application.canvas);
    this.host.addEventListener('wheel', (event) => this.zoomFromWheel(event), { passive: false });
    this.application.stage.addChild(this.scene);
    this.application.ticker.add((ticker) => {
      if (!this.playing || !this.track || this.duration <= 0) return;
      const elapsedSeconds = ticker.elapsedMS / 1000;
      const mediaTime = this.playbackTimeSource?.(elapsedSeconds);
      // Follow audible playback, including buffering. Silent/unsupported tracks
      // retain the uncapped wall clock; export rendering never uses this source.
      this.currentTime = mediaTime !== undefined && Number.isFinite(mediaTime)
        ? Math.max(0, mediaTime)
        : this.currentTime + elapsedSeconds * this.playbackSpeed;
      if (this.currentTime > this.duration) {
        if (this.loop) {
          const completedCycles = Math.max(1, Math.floor(this.currentTime / this.duration));
          this.currentTime %= this.duration;
          this.onLoop?.(completedCycles);
        } else {
          this.currentTime = this.duration;
          this.playing = false;
          this.onPlaybackChange?.(false);
        }
      }
      this.render();
      this.onTimeChange?.(this.currentTime, this.duration);
    });
    new ResizeObserver(() => {
      this.application.renderer.resize(this.host.clientWidth, this.host.clientHeight);
      this.updateScenePlacement();
    }).observe(this.host);
    this.initialized = true;
  }

  load(
    document: AnimationDocument,
    atlasInputs: LoadedAtlasInput[],
    shaderInputs: CostumeShaderTextureInput[] = [],
    attachmentInputs: CostumeAttachmentInput[] = [],
    particleInputs: DofParticlePrefabInput[] = [],
  ): void {
    this.destroySceneAssets();
    this.document = document;
    this.blendVersion = document.render_blend_version ?? animationBlendVersion(document);
    runtimeAtlases(atlasInputs, this.spriteScaleMode).forEach((atlas, sourceId) => this.atlases.set(sourceId, atlas));
    for (const input of shaderInputs) {
      const behavior = costumeShaderBehavior(input.shaderName);
      if (!behavior) continue;
      const dimensions = imageSize(input.image);
      this.shaderTextures.set(input.shaderName.toLowerCase(), {
        behavior,
        source: imageSource(input, 1, this.spriteScaleMode),
        width: dimensions.width,
        height: dimensions.height,
        textures: new Map(),
      });
    }
    attachmentInputs.forEach((input) => {
      this.attachments.push({
        input,
        atlases: runtimeAtlases(input.atlases, this.spriteScaleMode),
        sprites: new Map(),
        duration: animationDuration(input.track),
        usesDepth: animationUsesDepth(input.track),
      });
    });
    particleInputs.forEach((input) => {
      const source = imageSource(input, 1, this.spriteScaleMode);
      this.particlePrefabs.set(input.pathId, {
        input,
        source,
        texture: new Texture({ source }),
      });
    });
    this.host.dataset.particlePrefabCount = String(this.particlePrefabs.size);
    const textureInputs: DecodedTextureImage[] = [
      ...atlasInputs,
      ...shaderInputs,
      ...attachmentInputs.flatMap((input) => input.atlases),
      ...particleInputs,
    ];
    this.host.dataset.premultipliedSourceCount = String(
      textureInputs.filter((input) => input.sourceAlphaFormat === 'premultiplied').length,
    );
    this.host.dataset.straightSourceCount = String(
      textureInputs.filter((input) => input.sourceAlphaFormat === 'straight').length,
    );
    if (document.anims.length) this.setTrack(0);
  }

  setTrack(index: number): void {
    const next = this.document?.anims[index];
    if (!next) return;
    this.track = next;
    this.duration = animationDuration(next);
    this.playbackSpeed = 1;
    this.currentTime = 0;
    this.playing = false;
    this.layerSprites.clear();
    this.visibleLayers.clear();
    this.layerEdits = new Map();
    this.transformOperations = undefined;
    this.ikRigs = [];
    this.ikPoses = [];
    this.scene.removeChildren().forEach((child) => child.destroy());
    this.usesDepth = animationUsesDepth(next);
    this.renderOrderKey = '';
    this.attachments.forEach((attachment) => {
      attachment.sprites.clear();
      attachment.targetLayerId = undefined;
    });
    this.particleSprites = [];
    this.host.dataset.particleEmitterCount = String(next.particle_emitters?.length ?? 0);
    this.host.dataset.activeParticleCount = '0';

    for (const layer of next.layers) {
      const sprite = new Sprite(Texture.EMPTY);
      sprite.label = layer.name;
      sprite.blendMode = blendMode(layerAnimationBlend(layer, this.blendVersion));
      this.scene.addChild(sprite);
      this.layerSprites.set(layer.id, sprite);
      this.visibleLayers.set(layer.id, layer.visible !== false);
    }
    const layerByName = new Map(next.layers.map((layer) => [normalizedLayerName(layer.name), layer]));
    this.attachments.forEach((attachment) => {
      attachment.targetLayerId = layerByName.get(normalizedLayerName(attachment.input.targetLayer))?.id;
      if (attachment.targetLayerId === undefined) return;
      for (const layer of attachment.input.track.layers) {
        const sprite = new Sprite(Texture.EMPTY);
        sprite.label = `${attachment.input.name}: ${layer.name}`;
        sprite.blendMode = blendMode(layerAnimationBlend(layer, attachment.input.blendVersion ?? this.blendVersion));
        this.scene.addChild(sprite);
        attachment.sprites.set(layer.id, sprite);
      }
    });
    this.render();
    this.fitToView();
    this.onPlaybackChange?.(false);
    this.onTimeChange?.(0, this.duration);
  }

  setTime(time: number): void {
    if (this.exportActive) return;
    this.currentTime = Math.max(0, Math.min(this.duration, time));
    this.render();
    this.onTimeChange?.(this.currentTime, this.duration);
  }

  togglePlayback(): void {
    if (this.exportActive || !this.track || this.duration <= 0) return;
    if (!this.playing && this.currentTime >= this.duration) this.currentTime = 0;
    this.playing = !this.playing;
    this.onPlaybackChange?.(this.playing);
  }

  setLoop(loop: boolean): void {
    this.loop = loop;
  }

  setPlaybackSpeed(speed: number): void {
    this.playbackSpeed = Number.isFinite(speed) ? Math.max(0.25, Math.min(4, speed)) : 1;
    this.host.dataset.animationPlaybackSpeed = String(this.playbackSpeed);
  }

  setPreferences(preferences: {
    spriteFilter: string;
    particleCap: number;
    particlesWorldSpace: boolean;
    zoomToCursor: boolean;
  }): void {
    this.spriteScaleMode = preferences.spriteFilter === 'nearest' ? 'nearest' : 'linear';
    this.particleCap = Number.isFinite(preferences.particleCap)
      ? Math.max(0, Math.min(1_000_000, Math.round(preferences.particleCap)))
      : 1000;
    this.forceParticleWorldSpace = preferences.particlesWorldSpace;
    this.zoomToCursor = preferences.zoomToCursor;
    this.atlases.forEach((atlas) => { atlas.source.scaleMode = this.spriteScaleMode; });
    this.shaderTextures.forEach((runtime) => { runtime.source.scaleMode = this.spriteScaleMode; });
    this.attachments.forEach((attachment) => {
      attachment.atlases.forEach((atlas) => { atlas.source.scaleMode = this.spriteScaleMode; });
    });
    this.particlePrefabs.forEach((runtime) => { runtime.source.scaleMode = this.spriteScaleMode; });
    this.host.dataset.spriteFilter = preferences.spriteFilter;
    this.host.dataset.particleCap = String(this.particleCap);
    if (this.track) this.render();
  }

  getDuration(): number {
    return this.duration;
  }

  getTime(): number {
    return this.currentTime;
  }

  isPlaying(): boolean {
    return this.playing;
  }

  async withExportSession<T>(
    callback: (session: AnimationExportSession) => Promise<T>,
  ): Promise<T> {
    if (!this.track) throw new Error('Load an animation before exporting.');
    if (this.exportActive) throw new Error('Another export is already running.');

    const originalTime = this.currentTime;
    const originalPlaying = this.playing;
    this.exportActive = true;
    this.playing = false;
    if (originalPlaying) this.onPlaybackChange?.(false);

    const animationBounds: AnimationExportBounds = {
      x: 0,
      y: 0,
      width: Math.max(1, Math.ceil(this.track.width || 1)),
      height: Math.max(1, Math.ceil(this.track.height || 1)),
    };
    const info: AnimationExportInfo = {
      name: this.track.name || 'animation',
      duration: this.duration,
      currentTime: originalTime,
      playbackRate: this.playbackSpeed,
      animationBounds,
    };
    const captureCanvas = (options: AnimationExportCaptureOptions) => {
      const resolution = Math.max(1, Math.min(8, Number(options.resolution) || 1));
      const outputWidth = Math.ceil(options.bounds.width * resolution);
      const outputHeight = Math.ceil(options.bounds.height * resolution);
      if (outputWidth > 16384 || outputHeight > 16384) {
        throw new Error(`Export resolution ${outputWidth}x${outputHeight} exceeds the 16384px browser limit.`);
      }
      this.currentTime = Math.max(0, Math.min(this.duration, options.time));
      this.render();
      const canvas = this.application.renderer.extract.canvas({
        target: this.scene,
        frame: new Rectangle(
          options.bounds.x,
          options.bounds.y,
          options.bounds.width,
          options.bounds.height,
        ),
        resolution,
        clearColor: options.background ?? [0, 0, 0, 0],
        antialias: true,
      });
      return canvas;
    };
    const session: AnimationExportSession = {
      info,
      measureContentBounds: async (sampleTimes, padding = 0, signal, onProgress) => {
        let minX = Number.POSITIVE_INFINITY;
        let minY = Number.POSITIVE_INFINITY;
        let maxX = Number.NEGATIVE_INFINITY;
        let maxY = Number.NEGATIVE_INFINITY;
        for (let index = 0; index < sampleTimes.length; index += 1) {
          checkExportAbort(signal);
          this.currentTime = Math.max(0, Math.min(this.duration, sampleTimes[index] ?? 0));
          this.render();
          const bounds = this.scene.getLocalBounds();
          if (bounds.isValid && bounds.width > 0 && bounds.height > 0) {
            minX = Math.min(minX, bounds.minX);
            minY = Math.min(minY, bounds.minY);
            maxX = Math.max(maxX, bounds.maxX);
            maxY = Math.max(maxY, bounds.maxY);
          }
          onProgress?.(index + 1, sampleTimes.length);
          if ((index + 1) % 8 === 0) {
            await new Promise<void>((resolve) => setTimeout(resolve, 0));
          }
        }
        checkExportAbort(signal);
        if (!Number.isFinite(minX) || !Number.isFinite(minY)) return animationBounds;
        const safePadding = Math.max(0, Number.isFinite(padding) ? padding : 0);
        const left = Math.floor(minX - safePadding * 0.5);
        const top = Math.floor(minY - safePadding * 0.5);
        const right = Math.ceil(maxX + safePadding * 0.5);
        const bottom = Math.ceil(maxY + safePadding * 0.5);
        return {
          x: left,
          y: top,
          width: Math.max(1, right - left),
          height: Math.max(1, bottom - top),
        };
      },
      capturePng: (options) => canvasToPngBlob(captureCanvas(options)),
      captureCanvas: (options) => captureCanvas(options) as HTMLCanvasElement | OffscreenCanvas,
      captureVideoFrame: (options, timing) => new VideoFrame(
        captureCanvas(options) as HTMLCanvasElement,
        { ...timing, alpha: 'discard' },
      ),
    };

    try {
      return await callback(session);
    } finally {
      this.currentTime = originalTime;
      this.render();
      this.playing = originalPlaying;
      this.exportActive = false;
      this.onTimeChange?.(originalTime, this.duration);
      if (originalPlaying) this.onPlaybackChange?.(true);
    }
  }

  setLayerVisible(layerId: number, visible: boolean): void {
    this.visibleLayers.set(layerId, visible);
    const sprite = this.layerSprites.get(layerId);
    if (sprite) sprite.visible = visible;
    this.onViewChange?.();
  }

  setLayerEdits(edits: ReadonlyMap<number, LayerTransformEdit>): void {
    if (this.exportActive) return;
    this.layerEdits = new Map(edits);
    this.transformOperations = undefined;
    this.render();
  }

  setTransformOperations(operations: readonly TransformOperation[], rigs: readonly IKRig[] = this.ikRigs): void {
    if (this.exportActive) return;
    this.transformOperations = cloneOperations(operations);
    this.ikRigs = cloneIKRigs(rigs);
    this.render();
  }

  pause(): void {
    if (!this.playing || this.exportActive) return;
    this.playing = false;
    this.onPlaybackChange?.(false);
  }

  getCamera(): { x: number; y: number; zoom: number } {
    return { x: this.scene.position.x, y: this.scene.position.y, zoom: this.zoom };
  }

  clientToView(clientX: number, clientY: number): ViewPoint {
    const rect = this.application.canvas.getBoundingClientRect();
    return {
      x: (clientX - rect.left) * this.application.screen.width / rect.width,
      y: (clientY - rect.top) * this.application.screen.height / rect.height,
    };
  }

  viewToWorld(point: ViewPoint): ViewPoint {
    return { x: (point.x - this.scene.position.x) / this.zoom, y: (point.y - this.scene.position.y) / this.zoom };
  }

  worldToView(point: ViewPoint): ViewPoint {
    return { x: point.x * this.zoom + this.scene.position.x, y: point.y * this.zoom + this.scene.position.y };
  }

  ikPose(id: string): IKPose | undefined {
    const pose = this.ikPoses.find((entry) => entry.id === id);
    return pose ? structuredClone(pose) : undefined;
  }

  jointOffsetAtView(id: number, point: ViewPoint): ViewPoint {
    if (!this.track) throw new Error('Load an animation first.');
    return jointOffset(this.track, this.worldStates, id, this.viewToWorld(point));
  }

  jointView(id: number, offset: ViewPoint): ViewPoint | undefined {
    if (!this.track) return;
    const point = jointWorld(this.track, this.worldStates, id, offset);
    return point ? this.worldToView(point) : undefined;
  }

  setEditorInset(height: number): void {
    const firstShow = this.editorInset === 0 && height > 0;
    this.editorInset = Math.max(0, height);
    if (firstShow && this.track) this.fitToView();
    this.onViewChange?.();
  }

  setCamera(x: number, y: number, zoom = this.zoom): void {
    if (!this.track || this.exportActive || ![x, y, zoom].every(Number.isFinite)) return;
    this.zoom = Math.max(.05, Math.min(8, zoom));
    this.scene.position.set(x, y);
    this.scene.scale.set(this.zoom);
    this.onViewChange?.();
  }

  layerViewGeometry(id: number): LayerViewGeometry | undefined {
    const sprite = this.layerSprites.get(id);
    const layer = this.track?.layers.find((item) => item.id === id);
    const state = this.worldStates.get(id);
    if (!sprite?.visible || !sprite.renderable || sprite.alpha <= 0 || !layer || !state) return;
    const { orig, trim } = sprite.texture;
    const x = (trim?.x ?? 0) - sprite.anchor.x * orig.width;
    const y = (trim?.y ?? 0) - sprite.anchor.y * orig.height;
    const width = trim?.width ?? orig.width, height = trim?.height ?? orig.height;
    const project = (px: number, py: number): ViewPoint => ({
      x: (state.m00 * px + state.m01 * py + state.tx) * this.zoom + this.scene.position.x,
      y: (state.m10 * px + state.m11 * py + state.ty) * this.zoom + this.scene.position.y,
    });
    return {
      id, corners: [project(x, y), project(x + width, y), project(x + width, y + height), project(x, y + height)],
      pivot: project(...layerAnchor(layer, state.spriteName)),
    };
  }

  hitTestLayers(point: ViewPoint, tolerance = 0): number[] {
    if (!this.track) return [];
    return animationRenderOrder(this.track, this.worldStates, this.usesDepth).reverse()
      .filter((layer) => {
        const geometry = this.layerViewGeometry(layer.id);
        return geometry && pointHitsQuad(point, geometry.corners, tolerance);
      }).map((layer) => layer.id);
  }

  layerVisible(layerId: number): boolean {
    return this.visibleLayers.get(layerId) !== false;
  }

  fitToView(): void {
    if (!this.track) return;
    const bounds = this.scene.getLocalBounds();
    const hasSpriteBounds = bounds.isValid && bounds.width > 0 && bounds.height > 0;
    const width = Math.max(1, hasSpriteBounds ? bounds.width : this.track.width || 384);
    const height = Math.max(1, hasSpriteBounds ? bounds.height : this.track.height || 384);
    const availableWidth = Math.max(1, this.host.clientWidth - 80);
    const availableHeight = Math.max(1, this.host.clientHeight - this.editorInset - 80);
    this.zoom = Math.min(2.5, Math.max(0.1, Math.min(availableWidth / width, availableHeight / height)));
    this.updateScenePlacement();
  }

  private updateScenePlacement(): void {
    if (!this.track) return;
    const bounds = this.scene.getLocalBounds();
    const hasSpriteBounds = bounds.isValid && bounds.width > 0 && bounds.height > 0;
    const centerX = hasSpriteBounds ? (bounds.minX + bounds.maxX) / 2 : this.track.width / 2;
    const centerY = hasSpriteBounds ? (bounds.minY + bounds.maxY) / 2 : this.track.height / 2;
    this.scene.scale.set(this.zoom);
    this.scene.position.set(
      this.host.clientWidth / 2 - centerX * this.zoom,
      (this.host.clientHeight - this.editorInset) / 2 - centerY * this.zoom,
    );
    this.onViewChange?.();
  }

  private zoomFromWheel(event: WheelEvent): void {
    if (!this.track || this.exportActive || !Number.isFinite(event.deltaY) || event.deltaY === 0) return;
    if ((event.target as Element).closest('.transform-dock')) return;
    event.preventDefault();
    const previousZoom = this.zoom;
    const factor = Math.exp(-event.deltaY * 0.0015);
    const nextZoom = Math.max(0.05, Math.min(8, previousZoom * factor));
    if (Math.abs(nextZoom - previousZoom) < 1e-6) return;
    const point = this.clientToView(event.clientX, event.clientY);
    const anchorX = this.zoomToCursor ? point.x : this.host.clientWidth / 2;
    const anchorY = this.zoomToCursor ? point.y : (this.host.clientHeight - this.editorInset) / 2;
    const ratio = nextZoom / previousZoom;
    this.scene.position.set(
      anchorX - (anchorX - this.scene.position.x) * ratio,
      anchorY - (anchorY - this.scene.position.y) * ratio,
    );
    this.scene.scale.set(nextZoom);
    this.zoom = nextZoom;
    this.onViewChange?.();
  }

  private findTextureIn(
    atlases: Map<number, RuntimeAtlas>,
    layer: AnimationLayer,
    spriteName: string,
  ): ResolvedSpriteTexture | undefined {
    const preferred = atlases.get(layer.src);
    const overrides = (layer.atlas_override_ids ?? [])
      .map((sourceId) => atlases.get(sourceId))
      .filter((atlas): atlas is RuntimeAtlas => Boolean(atlas));
    const candidates = preferred
      ? [...overrides, preferred, ...atlases.values()]
      : [...overrides, ...atlases.values()];
    const key = spriteName.toLowerCase();
    const visited = new Set<number>();
    for (const atlas of candidates) {
      if (visited.has(atlas.sourceId)) continue;
      visited.add(atlas.sourceId);
      const sprite = atlas.data.sprites.get(key);
      if (sprite) return { sprite, atlas: atlas.data, texture: textureForSprite(atlas, sprite) };
    }
    return undefined;
  }

  private findTexture(layer: AnimationLayer, spriteName: string): ResolvedSpriteTexture | undefined {
    return this.findTextureIn(this.atlases, layer, spriteName);
  }

  private setSpriteAnchor(sprite: Sprite, layer: AnimationLayer, resolved: ResolvedSpriteTexture): void {
    const mesh = spriteMeshAnchor(resolved.sprite);
    const offset = costumeDrawOffset(layer, resolved.sprite, resolved.atlas);
    sprite.anchor.set(
      mesh[0] - offset[0] / Math.max(1e-6, sprite.texture.orig.width),
      mesh[1] - offset[1] / Math.max(1e-6, sprite.texture.orig.height),
    );
  }

  private attachmentAlignmentPoint(
    layer: AnimationLayer, state: WorldState, atlases: Map<number, RuntimeAtlas>,
    mode: CostumeAttachmentInput['alignmentMode'],
  ): [number, number] {
    const resolved = this.findTextureIn(atlases, layer, state.spriteName || layer.name);
    return costumeAlignmentPoint(layer, state, resolved?.sprite, resolved?.atlas, mode);
  }

  private shaderTexture(
    layer: AnimationLayer,
    spriteName: string,
    baseTexture: Texture,
  ): Texture | undefined {
    const shaderName = layer.shader_name?.toLowerCase();
    const runtime = shaderName ? this.shaderTextures.get(shaderName) : undefined;
    if (!runtime?.behavior.replaceBaseSprite) return undefined;
    const frameCount = Math.max(1, ...runtime.behavior.frameValues.map((value) => Math.round(value) + 1));
    const slot = Math.max(0, Math.min(frameCount - 1, Math.round(costumeShaderFrame(runtime.behavior, this.currentTime))));
    const baseFrame = baseTexture.frame;
    const cacheKey = [
      spriteName.toLowerCase(), slot, baseTexture.uid,
      baseFrame.x, baseFrame.y, baseFrame.width, baseFrame.height,
    ].join('|');
    const cached = runtime.textures.get(cacheKey);
    if (cached) return cached;

    const texture = new Texture({
      source: runtime.source,
      frame: new Rectangle(0, 0, runtime.width, runtime.height),
      orig: baseTexture.orig.clone(),
      trim: baseTexture.trim?.clone(),
    });
    // Preserve the packed sprite's corner ordering, including rotated frames.
    // Per-corner clamping also handles UVs at the edge without stretching its geometry.
    for (const index of [0, 1, 2, 3] as const) {
      const x = `x${index}` as const, y = `y${index}` as const;
      const uv = costumeShaderUV(runtime.behavior, slot, [baseTexture.uvs[x], baseTexture.uvs[y]],
        baseFrame, [baseTexture.source.width, baseTexture.source.height], Boolean(baseTexture.rotate));
      texture.uvs[x] = uv[0];
      texture.uvs[y] = uv[1];
    }
    runtime.textures.set(cacheKey, texture);
    return texture;
  }

  private render(): void {
    if (!this.track) return;
    const originalStates = calculateWorldStates(this.track, this.currentTime);
    const editedStates = this.transformOperations
      ? applyTransformOperations(this.track, originalStates, this.transformOperations)
      : applyWorldLayerEdits(this.track, originalStates, this.layerEdits);
    const ik = applyIKRigs(this.track, editedStates, this.ikRigs);
    const worldStates = ik.states;
    this.ikPoses = ik.poses;
    this.worldStates = worldStates;
    const renderLayers = animationRenderOrder(this.track, worldStates, this.usesDepth);
    const attachmentSprites = new Map<number, Array<{ key: string; sprite: Sprite }>>();
    this.attachments.forEach((attachment, attachmentIndex) => {
      if (attachment.targetLayerId === undefined) return;
      const entries = this.renderAttachment(attachment, worldStates);
      const bucket = attachmentSprites.get(attachment.targetLayerId) ?? [];
      entries.forEach((entry) => bucket.push({
        key: `${attachmentIndex}:${entry.layerId}`,
        sprite: entry.sprite,
      }));
      attachmentSprites.set(attachment.targetLayerId, bucket);
    });
    const ordered: Array<{ key: string; sprite: Sprite }> = [];
    renderLayers.forEach((layer) => {
      const sprite = this.layerSprites.get(layer.id);
      if (sprite) ordered.push({ key: `base:${layer.id}`, sprite });
      ordered.push(...(attachmentSprites.get(layer.id) ?? []));
    });
    this.renderParticles().forEach((sprite, index) => {
      ordered.push({ key: `particle:${index}`, sprite });
    });
    const renderOrderKey = ordered.map((entry) => entry.key).join(',');
    if (renderOrderKey !== this.renderOrderKey) {
      ordered.forEach((entry, index) => {
        if (this.scene.getChildIndex(entry.sprite) !== index) this.scene.setChildIndex(entry.sprite, index);
      });
      this.renderOrderKey = renderOrderKey;
    }
    for (const layer of this.track.layers) {
      const sprite = this.layerSprites.get(layer.id);
      const state = worldStates.get(layer.id);
      if (!sprite || !state) continue;
      const requestedSprite = state.spriteName || layer.name;
      const resolved = requestedSprite ? this.findTexture(layer, requestedSprite) : undefined;
      if (resolved) {
        sprite.texture = this.shaderTexture(layer, requestedSprite, resolved.texture) ?? resolved.texture;
        this.setSpriteAnchor(sprite, layer, resolved);
      }
      sprite.renderable = Boolean(resolved);
      sprite.visible = this.layerVisible(layer.id);
      const behavior = costumeShaderBehavior(layer.shader_name);
      const shaderColor: [number, number, number, number] = behavior
        ? costumeShaderColor(behavior, this.currentTime)
        : [1, 1, 1, 1];
      const layerColor = resolveLayerColor(layer, this.currentTime, this.duration);
      sprite.alpha = Math.max(0, Math.min(
        1,
        (state.opacity / 100) * (state.alpha / 255) * layerColor[3] * shaderColor[3],
      ));
      sprite.tint =
        (Math.round(Math.max(0, Math.min(255, state.red * layerColor[0] * shaderColor[0]))) << 16) |
        (Math.round(Math.max(0, Math.min(255, state.green * layerColor[1] * shaderColor[1]))) << 8) |
        Math.round(Math.max(0, Math.min(255, state.blue * layerColor[2] * shaderColor[2])));
      sprite.setFromMatrix(
        new Matrix(state.m00, state.m10, state.m01, state.m11, state.tx, state.ty),
      );
    }
    if (!this.exportActive) this.onViewChange?.();
  }

  private layerAnchor(layer: AnimationLayer, state: WorldState): [number, number] {
    const mapped = state.spriteName
      ? layer.sprite_anchor_map?.[state.spriteName]
        ?? Object.entries(layer.sprite_anchor_map ?? {}).find(
          ([name]) => name.toLowerCase() === state.spriteName.toLowerCase(),
        )?.[1]
      : undefined;
    const anchorX = mapped?.[0] ?? layer.anchor_x;
    const anchorY = mapped?.[1] ?? layer.anchor_y;
    return [
      state.m00 * anchorX + state.m01 * anchorY + state.tx,
      state.m10 * anchorX + state.m11 * anchorY + state.ty,
    ];
  }

  private attachmentTime(attachment: RuntimeAttachment): number {
    const duration = attachment.duration;
    let time = this.currentTime * Math.max(0.1, attachment.input.tempoMultiplier) + attachment.input.timeOffset;
    if (duration <= 0) return Math.max(0, time);
    if (attachment.input.loop) time = ((time % duration) + duration) % duration;
    else time = Math.max(0, Math.min(duration, time));
    return time;
  }

  private attachmentRoot(
    attachment: RuntimeAttachment,
    states: Map<number, WorldState>,
  ): { layer: AnimationLayer; anchor: [number, number] } | undefined {
    const preferred = normalizedLayerName(attachment.input.rootLayer ?? '');
    const roots = attachment.input.track.layers.filter((layer) => layer.parent < 0);
    const layer = (preferred
      ? attachment.input.track.layers.find((candidate) => normalizedLayerName(candidate.name) === preferred)
      : undefined)
      ?? ['root', 'main', 'mid', 'mg', 'middle'].flatMap((name) => (
        roots.filter((candidate) => normalizedLayerName(candidate.name) === name)
      ))[0]
      ?? roots[0]
      ?? attachment.input.track.layers[0];
    const state = layer ? states.get(layer.id) : undefined;
    return layer && state ? { layer, anchor: this.attachmentAlignmentPoint(
      layer, state, attachment.atlases, attachment.input.alignmentMode,
    ) } : undefined;
  }

  private renderAttachment(
    attachment: RuntimeAttachment,
    baseStates: Map<number, WorldState>,
  ): Array<{ layerId: number; sprite: Sprite }> {
    if (!this.track || attachment.targetLayerId === undefined) return [];
    const targetLayer = this.track.layers.find((layer) => layer.id === attachment.targetLayerId);
    const targetState = targetLayer ? baseStates.get(targetLayer.id) : undefined;
    if (!targetLayer || !targetState || !this.layerVisible(targetLayer.id)) {
      attachment.sprites.forEach((sprite) => { sprite.visible = false; });
      return [];
    }

    const localTime = this.attachmentTime(attachment);
    const states = calculateWorldStates(attachment.input.track, localTime);
    const root = this.attachmentRoot(attachment, states);
    if (!root) return [];
    const targetAnchor = this.attachmentAlignmentPoint(targetLayer, targetState, this.atlases, attachment.input.alignmentMode);
    const renderLayers = animationRenderOrder(attachment.input.track, states, attachment.usesDepth);
    const rendered: Array<{ layerId: number; sprite: Sprite }> = [];
    for (const layer of renderLayers) {
      const sprite = attachment.sprites.get(layer.id);
      const state = states.get(layer.id);
      if (!sprite || !state) continue;
      const requestedSprite = state.spriteName || layer.name;
      const resolved = requestedSprite
        ? this.findTextureIn(attachment.atlases, layer, requestedSprite)
        : undefined;
      if (resolved) {
        sprite.texture = resolved.texture;
        this.setSpriteAnchor(sprite, layer, resolved);
      }
      const m00 = targetState.m00 * state.m00 + targetState.m01 * state.m10;
      const m01 = targetState.m00 * state.m01 + targetState.m01 * state.m11;
      const m10 = targetState.m10 * state.m00 + targetState.m11 * state.m10;
      const m11 = targetState.m10 * state.m01 + targetState.m11 * state.m11;
      const childX = state.tx - root.anchor[0];
      const childY = state.ty - root.anchor[1];
      const tx = targetState.m00 * childX + targetState.m01 * childY + targetAnchor[0];
      const ty = targetState.m10 * childX + targetState.m11 * childY + targetAnchor[1];
      const color = resolveLayerColor(layer, localTime, attachment.duration);
      sprite.renderable = Boolean(resolved);
      sprite.visible = Boolean(resolved) && layer.visible !== false;
      sprite.alpha = Math.max(0, Math.min(1, (state.opacity / 100) * (state.alpha / 255) * color[3]));
      sprite.tint =
        (Math.round(Math.max(0, Math.min(255, state.red * color[0]))) << 16) |
        (Math.round(Math.max(0, Math.min(255, state.green * color[1]))) << 8) |
        Math.round(Math.max(0, Math.min(255, state.blue * color[2])));
      sprite.setFromMatrix(new Matrix(m00, m10, m01, m11, tx, ty));
      rendered.push({ layerId: layer.id, sprite });
    }
    return rendered;
  }

  private emitterPosition(emitter: DofParticleEmitter, time: number): [number, number] {
    const prefab = this.particlePrefabs.get(emitter.prefabPathId)?.input;
    const x = particleChannel(emitter.channels[0], time, 0)
      + emitter.offset[0]
      + (prefab?.transformPosition[0] ?? 0);
    const y = particleChannel(emitter.channels[1], time, 0)
      + emitter.offset[1]
      + (prefab?.transformPosition[1] ?? 0);
    return [x * 2, -y * 2];
  }

  private renderParticles(): Sprite[] {
    const emitters = this.track?.particle_emitters ?? [];
    const rendered: Sprite[] = [];
    const maxParticles = this.particleCap;
    for (const emitter of emitters) {
      const runtime = this.particlePrefabs.get(emitter.prefabPathId);
      if (!runtime || rendered.length >= maxParticles) continue;
      const prefab = runtime.input;
      const rate = Math.max(0, (prefab.emissionRate[0] + prefab.emissionRate[1]) * 0.5);
      const maximumLife = Math.max(0.05, prefab.lifetime[1]);
      if (rate <= 0) continue;
      const first = Math.max(0, Math.floor((this.currentTime - maximumLife) * rate));
      const last = Math.floor(this.currentTime * rate);
      const seed = stringSeed(`${emitter.name}|${emitter.prefabPathId}`);
      for (let index = first; index <= last && rendered.length < maxParticles; index += 1) {
        const spawnTime = (index + 0.5) / rate;
        if (spawnTime > this.currentTime) continue;
        const emissionScale = Math.max(0, particleChannel(emitter.channels[12], spawnTime, 1));
        if (emissionScale <= 0 || seededRandom(seed, index * 13) > Math.min(1, emissionScale)) continue;
        const life = Math.max(0.05, rangeValue(prefab.lifetime, seededRandom(seed, index * 13 + 1)));
        const age = this.currentTime - spawnTime;
        if (age < 0 || age > life) continue;
        const normalizedAge = Math.max(0, Math.min(1, age / life));
        const originTime = this.forceParticleWorldSpace
          ? spawnTime
          : prefab.moveWithTransform ? this.currentTime : spawnTime;
        const [originX, originY] = this.emitterPosition(emitter, originTime);
        const nodeRotation = particleChannel(emitter.channels[5], spawnTime, 0);
        const cone = (seededRandom(seed, index * 13 + 2) - 0.5) * prefab.shapeAngle * (Math.PI / 180);
        const direction = -Math.PI / 2 + nodeRotation + cone;
        const speed = rangeValue(prefab.speed, seededRandom(seed, index * 13 + 3)) * 100;
        const radius = prefab.shapeRadius * 100 * (seededRandom(seed, index * 13 + 4) * 2 - 1);
        const gravity = rangeValue(prefab.gravity, seededRandom(seed, index * 13 + 5)) * 100;
        const x = originX + Math.cos(direction) * (radius + speed * age);
        const y = originY + Math.sin(direction) * (radius + speed * age) + gravity * age * age * 0.5;
        const startSize = rangeValue(prefab.size, seededRandom(seed, index * 13 + 6));
        const size = startSize * sampleScalarKeys(prefab.sizeOverLifetime, normalizedAge, 1)
          * emitter.imageScale;
        const startColor = prefab.startColor[0].map((value, channel) => (
          value + (prefab.startColor[1][channel]! - value) * seededRandom(seed, index * 13 + 7)
        )) as ColorTuple;
        const lifetimeColor = sampleColorKeys(prefab.colorOverLifetime, normalizedAge);
        const emitterColor: ColorTuple = [
          particleChannel(emitter.channels[8], this.currentTime, 1),
          particleChannel(emitter.channels[9], this.currentTime, 1),
          particleChannel(emitter.channels[10], this.currentTime, 1),
          particleChannel(emitter.channels[11], this.currentTime, 100) / 100,
        ];
        const color = startColor.map((value, channel) => (
          value * lifetimeColor[channel]! * prefab.materialColor[channel]! * emitterColor[channel]!
        )) as ColorTuple;
        color[3] *= sampleScalarKeys(prefab.alphaOverLifetime, normalizedAge, 1);

        let sprite = this.particleSprites[rendered.length];
        if (!sprite) {
          sprite = new Sprite(runtime.texture);
          sprite.anchor.set(0.5);
          this.scene.addChild(sprite);
          this.particleSprites.push(sprite);
        }
        sprite.texture = runtime.texture;
        sprite.blendMode = blendMode(prefab.blend);
        sprite.visible = true;
        sprite.renderable = true;
        sprite.position.set(x, y);
        sprite.rotation = rangeValue(prefab.rotation, seededRandom(seed, index * 13 + 8)) + nodeRotation;
        sprite.scale.set(
          size * 100 / Math.max(1, runtime.source.width),
          size * 100 / Math.max(1, runtime.source.height),
        );
        sprite.alpha = Math.max(0, Math.min(1, color[3]));
        sprite.tint =
          (Math.round(Math.max(0, Math.min(255, color[0] * 255))) << 16) |
          (Math.round(Math.max(0, Math.min(255, color[1] * 255))) << 8) |
          Math.round(Math.max(0, Math.min(255, color[2] * 255)));
        rendered.push(sprite);
      }
    }
    for (let index = rendered.length; index < this.particleSprites.length; index += 1) {
      this.particleSprites[index]!.visible = false;
    }
    this.host.dataset.activeParticleCount = String(rendered.length);
    return rendered;
  }

  private destroySceneAssets(): void {
    this.playing = false;
    this.scene.removeChildren().forEach((child) => child.destroy());
    this.layerSprites.clear();
    this.usesDepth = false;
    this.renderOrderKey = '';
    destroyAtlases(this.atlases);
    for (const runtime of this.shaderTextures.values()) {
      runtime.textures.forEach((texture) => texture.destroy(false));
      runtime.source.destroy();
    }
    this.shaderTextures.clear();
    this.attachments.forEach((attachment) => destroyAtlases(attachment.atlases));
    this.attachments.length = 0;
    this.particleSprites = [];
    for (const runtime of this.particlePrefabs.values()) {
      runtime.texture.destroy(false);
      runtime.source.destroy();
    }
    this.particlePrefabs.clear();
    this.host.dataset.particlePrefabCount = '0';
    this.host.dataset.particleEmitterCount = '0';
    this.host.dataset.activeParticleCount = '0';
  }
}
