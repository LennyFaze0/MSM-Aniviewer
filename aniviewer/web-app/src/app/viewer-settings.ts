export const VIEWER_SETTINGS_STORAGE_KEY = 'aniviewer.viewerSettings.v1';

export type ThemeMode = 'system' | 'dark' | 'light';
export type SpriteFilterMode =
  | 'nearest'
  | 'bilinear'
  | 'nearest_mipmap_nearest'
  | 'linear_mipmap_nearest'
  | 'nearest_mipmap_linear'
  | 'trilinear'
  | 'bicubic'
  | 'lanczos'
  | 'anisotropic_2x'
  | 'anisotropic_4x'
  | 'anisotropic_8x'
  | 'anisotropic_16x';

export interface ThemeColorSetting {
  enabled: boolean;
  color: string;
}

export interface ViewerSettings {
  version: 1;
  interface: {
    scale: number;
    themeMode: ThemeMode;
    primary: ThemeColorSetting;
    secondary: ThemeColorSetting;
    tertiary: ThemeColorSetting;
    quaternary: ThemeColorSetting;
  };
  viewport: {
    zoomToCursor: boolean;
    spriteFilter: SpriteFilterMode;
    spriteFilterStrength: number;
    dofAlphaSmoothing: boolean;
    dofAlphaSmoothingMode: 'normal' | 'strong';
    dofAlphaSmoothingStrength: number;
    postAaEnabled: boolean;
    postAaMode: 'fxaa' | 'smaa';
    postAaStrength: number;
    motionBlurEnabled: boolean;
    motionBlurStrength: number;
    bloomEnabled: boolean;
    bloomStrength: number;
    bloomThreshold: number;
    bloomRadius: number;
    vignetteEnabled: boolean;
    vignetteStrength: number;
    grainEnabled: boolean;
    grainStrength: number;
    chromaticAberrationEnabled: boolean;
    chromaticAberrationStrength: number;
    dofSpriteShader: string;
    particlesWorldSpace: boolean;
    particleCap: number;
    particleDistanceSensitivity: number;
    spatial3dEnabled: boolean;
    spatial3dMode: 'spatiallabs' | 'side_by_side' | 'anaglyph';
  };
  playback: {
    loop: boolean;
    volume: number;
    muted: boolean;
    metronomeEnabled: boolean;
    metronomeAudible: boolean;
    timeSignatureNumerator: number;
    timeSignatureDenominator: 1 | 2 | 4 | 8 | 16;
    showBeatGrid: boolean;
    allowBeatEditing: boolean;
  };
  export: {
    video: { format: 'auto' | 'mp4' | 'webm' | 'mov'; mode: 'auto' | 'fast' | 'compatibility'; bitrateMbps: number; includeAudio: boolean };
    png: {
      compression: number;
      fullResolution: boolean;
      scaleMultiplier: number;
      sequenceFps: number;
      framing: 'animation' | 'content';
      solidBackground: boolean;
      backgroundColor: string;
    };
    gif: {
      fps: number;
      colors: 16 | 32 | 64 | 128 | 256;
      dither: boolean;
      optimize: boolean;
      loop: number;
      scale: number;
    };
    mov: {
      codec: string;
      quality: string;
      includeAudio: boolean;
      fullResolution: boolean;
      scaleMultiplier: number;
    };
    webm: {
      codec: string;
      crf: number;
      speed: number;
      includeAudio: boolean;
      fullResolution: boolean;
      scaleMultiplier: number;
    };
    mp4: {
      codec: string;
      crf: number;
      preset: string;
      bitrateKbps: number;
      includeAudio: boolean;
      fullResolution: boolean;
      scaleMultiplier: number;
      pixelFormat: string;
      faststart: boolean;
    };
    psd: {
      includeHidden: boolean;
      scale: number;
      quality: string;
      compression: string;
      cropCanvas: boolean;
      matchViewport: boolean;
      preserveResolution: boolean;
      fullResolutionMultiplier: number;
    };
    afterEffects: {
      rigMode: string;
      scale: number;
      quality: string;
      preserveResolution: boolean;
      fullResolutionMultiplier: number;
      matchViewport: boolean;
      compression: string;
    };
    framing: {
      boundsScope: 'current' | 'all';
      explicitResolution: boolean;
      width: number;
      height: number;
      padding: number;
    };
  };
  conversion: {
    automaticRevisionDetection: boolean;
    manualRevisionMode: '1' | 'legacy' | '2' | 'muppets' | '3' | '4' | '5' | '6';
    automaticRosettaRefresh: boolean;
  };
  diagnostics: {
    enabled: boolean;
    highlightProblemLayers: boolean;
    throttleLayerUpdates: boolean;
    performanceLogging: boolean;
    slowSampleThresholdMs: number;
    reportIntervalSeconds: number;
    minimumSeverity: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR';
    includeDebugPayloads: boolean;
    maxEntries: number;
    rateLimitPerSecond: number;
  };
  keybinds: Record<string, string>;
}

export const DEFAULT_KEYBINDS: Readonly<Record<string, string>> = Object.freeze({
  toggle_playback: 'Space',
  toggle_loop: '',
  record_pose: 'R',
  undo: 'Ctrl+Z',
  redo: 'Ctrl+Y',
  redo_alt: 'Ctrl+Shift+Z',
  copy_keyframes: 'Ctrl+C',
  paste_keyframes: 'Ctrl+V',
  move_tool: 'V',
  select_tool: 'Q',
  pan_tool: 'H',
  free_transform: 'Ctrl+T',
  toggle_rotation_gizmo: 'E',
  toggle_scale_gizmo: 'S',
  fullscreen: 'F11',
  toggle_controls_panel: '',
  toggle_layers_panel: '',
  toggle_timeline_panel: '',
  toggle_log_panel: '',
  toggle_focus_mode: '',
});

export const DEFAULT_VIEWER_SETTINGS: Readonly<ViewerSettings> = Object.freeze({
  version: 1,
  interface: {
    scale: 1,
    themeMode: 'system',
    primary: { enabled: false, color: '#303439' },
    secondary: { enabled: false, color: '#4c96d1' },
    tertiary: { enabled: false, color: '#b4b4b4' },
    quaternary: { enabled: false, color: '#4c96d1' },
  },
  viewport: {
    zoomToCursor: true,
    spriteFilter: 'bilinear',
    spriteFilterStrength: 1,
    dofAlphaSmoothing: false,
    dofAlphaSmoothingMode: 'normal',
    dofAlphaSmoothingStrength: 0.5,
    postAaEnabled: false,
    postAaMode: 'fxaa',
    postAaStrength: 0.5,
    motionBlurEnabled: false,
    motionBlurStrength: 0.35,
    bloomEnabled: false,
    bloomStrength: 0.15,
    bloomThreshold: 0.6,
    bloomRadius: 1.5,
    vignetteEnabled: false,
    vignetteStrength: 0.25,
    grainEnabled: false,
    grainStrength: 0.2,
    chromaticAberrationEnabled: false,
    chromaticAberrationStrength: 0.25,
    dofSpriteShader: 'auto',
    particlesWorldSpace: true,
    particleCap: 1000,
    particleDistanceSensitivity: 0.5,
    spatial3dEnabled: false,
    spatial3dMode: 'spatiallabs',
  },
  playback: {
    loop: true,
    volume: 0.85,
    muted: false,
    metronomeEnabled: false,
    metronomeAudible: true,
    timeSignatureNumerator: 4,
    timeSignatureDenominator: 4,
    showBeatGrid: false,
    allowBeatEditing: false,
  },
  export: {
    video: { format: 'auto', mode: 'auto', bitrateMbps: 8, includeAudio: true },
    png: {
      compression: 6,
      fullResolution: false,
      scaleMultiplier: 1,
      sequenceFps: 30,
      framing: 'animation',
      solidBackground: false,
      backgroundColor: '#000000',
    },
    gif: { fps: 15, colors: 256, dither: true, optimize: true, loop: 0, scale: 100 },
    mov: { codec: 'prores_ks', quality: 'high', includeAudio: true, fullResolution: false, scaleMultiplier: 1 },
    webm: { codec: 'libvpx-vp9', crf: 28, speed: 4, includeAudio: true, fullResolution: false, scaleMultiplier: 1 },
    mp4: {
      codec: 'libx264', crf: 18, preset: 'medium', bitrateKbps: 0,
      includeAudio: true, fullResolution: false, scaleMultiplier: 1,
      pixelFormat: 'yuv420p', faststart: true,
    },
    psd: {
      includeHidden: false, scale: 100, quality: 'balanced', compression: 'rle',
      cropCanvas: false, matchViewport: false, preserveResolution: false,
      fullResolutionMultiplier: 1,
    },
    afterEffects: {
      rigMode: 'auto', scale: 100, quality: 'balanced', preserveResolution: false,
      fullResolutionMultiplier: 1, matchViewport: true, compression: 'rle',
    },
    framing: { boundsScope: 'current', explicitResolution: false, width: 1920, height: 1080, padding: 8 },
  },
  conversion: {
    automaticRevisionDetection: true,
    manualRevisionMode: '6',
    automaticRosettaRefresh: true,
  },
  diagnostics: {
    enabled: false,
    highlightProblemLayers: false,
    throttleLayerUpdates: true,
    performanceLogging: false,
    slowSampleThresholdMs: 50,
    reportIntervalSeconds: 8,
    minimumSeverity: 'INFO',
    includeDebugPayloads: false,
    maxEntries: 2000,
    rateLimitPerSecond: 100,
  },
  keybinds: { ...DEFAULT_KEYBINDS },
} as ViewerSettings);

export interface SettingsStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function cloneDefaults(): ViewerSettings {
  return JSON.parse(JSON.stringify(DEFAULT_VIEWER_SETTINGS)) as ViewerSettings;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function mergeKnown<T>(defaults: T, candidate: unknown): T {
  if (Array.isArray(defaults)) return [...defaults] as T;
  if (!defaults || typeof defaults !== 'object') {
    return (typeof candidate === typeof defaults ? candidate : defaults) as T;
  }
  const source = record(candidate);
  const merged: Record<string, unknown> = {};
  for (const [key, fallback] of Object.entries(defaults as Record<string, unknown>)) {
    merged[key] = mergeKnown(fallback, source[key]);
  }
  return merged as T;
}

function clamp(value: unknown, minimum: number, maximum: number, fallback: number): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(minimum, Math.min(maximum, numeric)) : fallback;
}

function integer(value: unknown, minimum: number, maximum: number, fallback: number): number {
  return Math.round(clamp(value, minimum, maximum, fallback));
}

function oneOf<T extends string | number>(value: unknown, values: readonly T[], fallback: T): T {
  return values.includes(value as T) ? value as T : fallback;
}

function hexColor(value: unknown, fallback: string): string {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value.toLowerCase() : fallback;
}

export function normalizeViewerSettings(value: unknown): ViewerSettings {
  const settings = mergeKnown(cloneDefaults(), value);
  settings.version = 1;
  settings.interface.scale = clamp(settings.interface.scale, 0.5, 2, 1);
  settings.interface.themeMode = oneOf(settings.interface.themeMode, ['system', 'dark', 'light'], 'system');
  (['primary', 'secondary', 'tertiary', 'quaternary'] as const).forEach((key) => {
    settings.interface[key].color = hexColor(
      settings.interface[key].color,
      DEFAULT_VIEWER_SETTINGS.interface[key].color,
    );
  });
  settings.viewport.spriteFilter = oneOf(settings.viewport.spriteFilter, [
    'nearest', 'bilinear', 'nearest_mipmap_nearest', 'linear_mipmap_nearest',
    'nearest_mipmap_linear', 'trilinear', 'bicubic', 'lanczos',
    'anisotropic_2x', 'anisotropic_4x', 'anisotropic_8x', 'anisotropic_16x',
  ], 'bilinear');
  settings.viewport.spriteFilterStrength = clamp(settings.viewport.spriteFilterStrength, 0, 1, 1);
  settings.viewport.dofAlphaSmoothingMode = oneOf(settings.viewport.dofAlphaSmoothingMode, ['normal', 'strong'], 'normal');
  settings.viewport.dofAlphaSmoothingStrength = clamp(settings.viewport.dofAlphaSmoothingStrength, 0, 1, 0.5);
  settings.viewport.postAaMode = oneOf(settings.viewport.postAaMode, ['fxaa', 'smaa'], 'fxaa');
  settings.viewport.postAaStrength = clamp(settings.viewport.postAaStrength, 0, 1, 0.5);
  settings.viewport.motionBlurStrength = clamp(settings.viewport.motionBlurStrength, 0, 1, 0.35);
  settings.viewport.bloomStrength = clamp(settings.viewport.bloomStrength, 0, 2, 0.15);
  settings.viewport.bloomThreshold = clamp(settings.viewport.bloomThreshold, 0, 2, 0.6);
  settings.viewport.bloomRadius = clamp(settings.viewport.bloomRadius, 0.1, 8, 1.5);
  settings.viewport.vignetteStrength = clamp(settings.viewport.vignetteStrength, 0, 1, 0.25);
  settings.viewport.grainStrength = clamp(settings.viewport.grainStrength, 0, 1, 0.2);
  settings.viewport.chromaticAberrationStrength = clamp(settings.viewport.chromaticAberrationStrength, 0, 1, 0.25);
  settings.viewport.particleCap = integer(settings.viewport.particleCap, 0, 1_000_000, 1000);
  settings.viewport.particleDistanceSensitivity = clamp(settings.viewport.particleDistanceSensitivity, 0, 100, 0.5);
  settings.viewport.spatial3dMode = oneOf(settings.viewport.spatial3dMode, ['spatiallabs', 'side_by_side', 'anaglyph'], 'spatiallabs');
  settings.playback.volume = clamp(settings.playback.volume, 0, 1, 0.85);
  settings.playback.timeSignatureNumerator = integer(settings.playback.timeSignatureNumerator, 1, 16, 4);
  settings.playback.timeSignatureDenominator = oneOf(
    Number(settings.playback.timeSignatureDenominator), [1, 2, 4, 8, 16] as const, 4,
  );
  settings.conversion.manualRevisionMode = oneOf(
    settings.conversion.manualRevisionMode,
    ['1', 'legacy', '2', 'muppets', '3', '4', '5', '6'] as const,
    '6',
  );
  settings.export.png.compression = integer(settings.export.png.compression, 0, 9, 6);
  settings.export.png.scaleMultiplier = clamp(settings.export.png.scaleMultiplier, 1, 8, 1);
  settings.export.png.sequenceFps = integer(settings.export.png.sequenceFps, 1, 120, 30);
  settings.export.png.framing = oneOf(settings.export.png.framing, ['animation', 'content'], 'animation');
  settings.export.png.backgroundColor = hexColor(settings.export.png.backgroundColor, '#000000');
  settings.export.gif.fps = integer(settings.export.gif.fps, 1, 60, 15);
  settings.export.gif.colors = oneOf(Number(settings.export.gif.colors), [16, 32, 64, 128, 256] as const, 256);
  settings.export.gif.loop = integer(settings.export.gif.loop, 0, 100, 0);
  settings.export.gif.scale = integer(settings.export.gif.scale, 10, 200, 100);
  settings.export.mov.scaleMultiplier = clamp(settings.export.mov.scaleMultiplier, 1, 8, 1);
  settings.export.video.format = oneOf(settings.export.video.format, ['auto', 'mp4', 'webm', 'mov'] as const, 'auto');
  settings.export.video.mode = oneOf(settings.export.video.mode, ['auto', 'fast', 'compatibility'] as const, 'auto');
  settings.export.video.bitrateMbps = integer(settings.export.video.bitrateMbps, 1, 100, 8);
  settings.export.webm.crf = integer(settings.export.webm.crf, 0, 63, 28);
  settings.export.webm.speed = integer(settings.export.webm.speed, 0, 8, 4);
  settings.export.webm.scaleMultiplier = clamp(settings.export.webm.scaleMultiplier, 1, 8, 1);
  settings.export.mp4.crf = integer(settings.export.mp4.crf, 0, 51, 18);
  settings.export.mp4.bitrateKbps = integer(settings.export.mp4.bitrateKbps, 0, 200000, 0);
  settings.export.mp4.scaleMultiplier = clamp(settings.export.mp4.scaleMultiplier, 1, 8, 1);
  settings.export.psd.scale = integer(settings.export.psd.scale, 25, 400, 100);
  settings.export.psd.fullResolutionMultiplier = clamp(settings.export.psd.fullResolutionMultiplier, 1, 8, 1);
  settings.export.afterEffects.scale = integer(settings.export.afterEffects.scale, 25, 400, 100);
  settings.export.afterEffects.fullResolutionMultiplier = clamp(settings.export.afterEffects.fullResolutionMultiplier, 1, 8, 1);
  settings.export.framing.boundsScope = oneOf(settings.export.framing.boundsScope, ['current', 'all'], 'current');
  settings.export.framing.width = integer(settings.export.framing.width, 16, 16384, 1920);
  settings.export.framing.height = integer(settings.export.framing.height, 16, 16384, 1080);
  settings.export.framing.padding = clamp(settings.export.framing.padding, 0, 256, 8);
  settings.diagnostics.slowSampleThresholdMs = clamp(settings.diagnostics.slowSampleThresholdMs, 1, 5000, 50);
  settings.diagnostics.reportIntervalSeconds = clamp(settings.diagnostics.reportIntervalSeconds, 2, 120, 8);
  settings.diagnostics.maxEntries = integer(settings.diagnostics.maxEntries, 100, 10000, 2000);
  settings.diagnostics.rateLimitPerSecond = integer(settings.diagnostics.rateLimitPerSecond, 10, 1000, 100);
  settings.diagnostics.minimumSeverity = oneOf(
    settings.diagnostics.minimumSeverity,
    ['DEBUG', 'INFO', 'WARNING', 'ERROR'],
    'INFO',
  );
  settings.keybinds = {
    ...DEFAULT_KEYBINDS,
    ...Object.fromEntries(Object.entries(record(settings.keybinds)).map(([key, shortcut]) => [
      key,
      typeof shortcut === 'string' ? shortcut.slice(0, 80) : '',
    ])),
  };
  return settings;
}

export function loadViewerSettings(storage: SettingsStorage | undefined = globalThis.localStorage): ViewerSettings {
  if (!storage) return cloneDefaults();
  try {
    const serialized = storage.getItem(VIEWER_SETTINGS_STORAGE_KEY);
    return serialized ? normalizeViewerSettings(JSON.parse(serialized)) : cloneDefaults();
  } catch {
    return cloneDefaults();
  }
}

export function saveViewerSettings(settings: ViewerSettings, storage: SettingsStorage | undefined = globalThis.localStorage): ViewerSettings {
  const normalized = normalizeViewerSettings(settings);
  storage?.setItem(VIEWER_SETTINGS_STORAGE_KEY, JSON.stringify(normalized));
  return normalized;
}

export function resetViewerSettings(storage: SettingsStorage | undefined = globalThis.localStorage): ViewerSettings {
  storage?.removeItem(VIEWER_SETTINGS_STORAGE_KEY);
  return cloneDefaults();
}

export function cloneViewerSettings(settings: ViewerSettings): ViewerSettings {
  return JSON.parse(JSON.stringify(settings)) as ViewerSettings;
}

export function keyboardEventToShortcut(event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'>): string {
  const rawKey = event.key;
  if (['Control', 'Alt', 'Shift', 'Meta'].includes(rawKey)) return '';
  const key = rawKey === ' ' ? 'Space'
    : rawKey.length === 1 ? rawKey.toUpperCase()
      : rawKey;
  return [
    event.ctrlKey ? 'Ctrl' : '',
    event.altKey ? 'Alt' : '',
    event.shiftKey ? 'Shift' : '',
    event.metaKey ? 'Meta' : '',
    key,
  ].filter(Boolean).join('+');
}

export function keyboardEventMatchesShortcut(
  event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'>,
  shortcut: string,
): boolean {
  if (!shortcut.trim()) return false;
  return keyboardEventToShortcut(event).toLowerCase() === shortcut.trim().toLowerCase();
}
