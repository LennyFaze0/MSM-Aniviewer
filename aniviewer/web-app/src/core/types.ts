export interface AnimationSource {
  src: string;
  id: number;
  width?: number;
  height?: number;
}

export interface InterpolatedNumber {
  immediate?: number;
  value?: number;
}

export interface InterpolatedPoint {
  immediate?: number;
  x?: number;
  y?: number;
}

export interface InterpolatedSprite {
  immediate?: number;
  string?: string;
}

export interface InterpolatedColor {
  immediate?: number;
  red?: number;
  green?: number;
  blue?: number;
  alpha?: number;
}

export interface InterpolatedFont {
  immediate?: number;
  size?: number;
  align?: number;
  r?: number;
  g?: number;
  b?: number;
  type?: number;
  name?: string;
  width?: number;
  height?: number;
}

export interface AnimationFrame {
  time: number;
  anchor?: InterpolatedPoint;
  mask?: InterpolatedPoint & { w?: number; h?: number };
  pos?: InterpolatedPoint;
  scale?: InterpolatedPoint;
  rotation?: InterpolatedNumber;
  opacity?: InterpolatedNumber;
  cell?: InterpolatedPoint;
  index?: InterpolatedNumber;
  depth?: InterpolatedNumber;
  sprite?: InterpolatedSprite;
  font?: InterpolatedFont;
  rgb?: InterpolatedColor;
}

export interface AnimationLayer {
  name: string;
  type?: number;
  blend?: number;
  parent: number;
  id: number;
  src: number;
  width?: number;
  height?: number;
  anchor_x: number;
  anchor_y: number;
  unk?: string;
  frames: AnimationFrame[];
  rgb?: Omit<InterpolatedColor, 'immediate' | 'alpha'>;
  sprite_anchor_map?: Record<string, [number, number]> | null;
  atlas_override_ids?: number[];
  visible?: boolean;
  costume_pivot_context?: boolean;
  // Draw-only anchors: unlike sprite_anchor_map, these must never move child transforms.
  costume_sprite_anchors?: Record<string, [number, number]>;
  shader_name?: string;
  color_tint?: ColorTuple;
  color_tint_hdr?: ColorTuple;
  color_gradient?: LayerColorGradient;
  color_animator?: LayerColorAnimator;
  color_metadata?: Record<string, unknown>;
}

export interface AnimationCloneLayer {
  name: string;
  resource: string;
  sheet: string;
  variant_index: number;
  new_layer: string;
  source_layer: string;
  reference_layer: string;
  insert_mode: -1 | 1;
}

export type ColorTuple = [red: number, green: number, blue: number, alpha: number];

export interface LayerColorStop {
  position?: number;
  time?: number;
  color: ColorTuple;
  interpolation?: string;
}

export interface LayerColorGradient {
  stops: LayerColorStop[];
  mode?: string;
  loop?: boolean;
  period?: number;
  duration?: number;
  offset?: number;
  speed?: number;
  ping_pong?: boolean;
}

export interface LayerColorKeyframe {
  time: number;
  color: ColorTuple;
  interpolation?: string;
}

export interface LayerColorAnimator {
  keyframes: LayerColorKeyframe[];
  duration?: number;
  loop?: boolean;
  mode?: string;
  offset?: number;
  speed?: number;
}

export interface AnimationTrack {
  name: string;
  width: number;
  height: number;
  loop_offset?: number;
  centered?: number | boolean;
  layers: AnimationLayer[];
  clone_layers?: AnimationCloneLayer[];
  particle_emitters?: DofParticleEmitter[];
}

export interface DofParticleKey {
  time: number;
  value: number;
  keytype: number;
}

export interface DofParticleEmitter {
  name: string;
  prefabPathId: string;
  offset: [number, number, number];
  imageScale: number;
  channels: Record<number, DofParticleKey[]>;
}

export interface ParticleColorKey {
  time: number;
  color: ColorTuple;
}

export interface ParticleScalarKey {
  time: number;
  value: number;
}

export interface DofParticlePrefabInput {
  pathId: string;
  name: string;
  image: TextureImageResource;
  alphaMode: TextureAlphaMode;
  sourceAlphaFormat: SourceAlphaFormat;
  blend: number;
  materialColor: ColorTuple;
  transformPosition: [number, number, number];
  emissionRate: [number, number];
  lifetime: [number, number];
  speed: [number, number];
  size: [number, number];
  rotation: [number, number];
  gravity: [number, number];
  moveWithTransform: boolean;
  startColor: [ColorTuple, ColorTuple];
  colorOverLifetime: ParticleColorKey[];
  alphaOverLifetime: ParticleScalarKey[];
  sizeOverLifetime: ParticleScalarKey[];
  shapeRadius: number;
  shapeAngle: number;
}

export interface AnimationDocument {
  rev: number;
  blend_version?: number;
  /** Preserve the base document's interpretation while applying costume overrides. */
  render_blend_version?: number;
  source_format?: string;
  source_revision?: number;
  legacy_format?: boolean;
  sources: AnimationSource[];
  anims: AnimationTrack[];
}

export interface AudioClipInput {
  name: string;
  path: string;
  blob: Blob;
}

export interface LayerState {
  posX: number;
  posY: number;
  scaleX: number;
  scaleY: number;
  rotation: number;
  opacity: number;
  depth: number;
  spriteName: string;
  red: number;
  green: number;
  blue: number;
  alpha: number;
}

export interface WorldState extends LayerState {
  m00: number;
  m01: number;
  m10: number;
  m11: number;
  tx: number;
  ty: number;
}

export interface AtlasSprite {
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  pivotX: number;
  pivotY: number;
  offsetX: number;
  offsetY: number;
  originalWidth: number;
  originalHeight: number;
  rotated: boolean;
  meshBounds?: [minX: number, minY: number, maxX: number, maxY: number];
}

export interface TextureAtlasData {
  imagePath: string;
  pivotMode?: string;
  width: number;
  height: number;
  hires: boolean;
  sprites: Map<string, AtlasSprite>;
}

export type TextureImageResource = ImageBitmap | HTMLImageElement | HTMLCanvasElement | OffscreenCanvas;
export type TextureAlphaMode = 'premultiplied-alpha' | 'premultiply-alpha-on-upload';
export type SourceAlphaFormat = 'opaque' | 'premultiplied' | 'straight';

export interface DecodedTextureImage {
  image: TextureImageResource;
  alphaMode: TextureAlphaMode;
  sourceAlphaFormat: SourceAlphaFormat;
}

export interface LoadedAtlasInput extends DecodedTextureImage {
  sourceId: number;
  sourceName?: string;
  data: TextureAtlasData;
}

export interface CostumeShaderTextureInput extends DecodedTextureImage {
  shaderName: string;
}

export interface CostumeAttachmentInput {
  name: string;
  targetLayer: string;
  track: AnimationTrack;
  blendVersion?: number;
  atlases: LoadedAtlasInput[];
  timeOffset: number;
  tempoMultiplier: number;
  loop: boolean;
  rootLayer?: string;
  alignmentMode?: 'rendered_anchor' | 'costume_canvas';
}
