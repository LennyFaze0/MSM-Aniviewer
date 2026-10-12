export interface ColorWaveChannel {
  channel: 'r' | 'g' | 'b' | 'a';
  min: number;
  max: number;
  frequency?: number;
  phase?: number;
}

export interface CostumeShaderBehavior {
  name: string;
  textureSuffix?: string;
  replaceBaseSprite: boolean;
  mappingMode: 'strip' | 'sheet';
  frameAxis: 'u' | 'v';
  cycleTime: number;
  frameThresholds: number[];
  frameValues: number[];
  requiresTexture: boolean;
  colorWave?: {
    frequency: number;
    channels: ColorWaveChannel[];
  };
}

const BEHAVIORS: Record<string, CostumeShaderBehavior> = {
  shadercostumea3: {
    name: 'shadercostumea3',
    textureSuffix: '_sequence',
    replaceBaseSprite: true,
    mappingMode: 'strip',
    frameAxis: 'u',
    cycleTime: 4,
    frameThresholds: [0.03, 0.06, 0.56, 0.59, 0.63, 0.69],
    frameValues: [1, 2, 3, 4, 5, 6, 0],
    requiresTexture: true,
  },
  shadercostumee9: {
    name: 'shadercostumee9',
    textureSuffix: '_sequence',
    replaceBaseSprite: true,
    mappingMode: 'sheet',
    frameAxis: 'v',
    cycleTime: 3,
    frameThresholds: [0.03, 0.09, 0.17, 0.24, 0.3, 0.38, 0.51, 0.56, 0.68, 0.76, 0.8, 0.88, 0.95, 0.99],
    frameValues: [1, 2, 1, 2, 3, 2, 3, 2, 3, 2, 1, 2, 3, 1, 0],
    requiresTexture: true,
  },
  shadercostumeabe3rare: {
    name: 'shadercostumeabe3rare',
    textureSuffix: '_sheet',
    replaceBaseSprite: true,
    mappingMode: 'sheet',
    frameAxis: 'u',
    cycleTime: 1,
    frameThresholds: [],
    frameValues: [0],
    requiresTexture: true,
  },
  shadercostumeace2: {
    name: 'shadercostumeace2',
    replaceBaseSprite: false,
    mappingMode: 'strip',
    frameAxis: 'u',
    cycleTime: 1,
    frameThresholds: [],
    frameValues: [0],
    requiresTexture: false,
    colorWave: {
      frequency: 2,
      channels: [
        { channel: 'r', min: 0.7, max: 1.2 },
        { channel: 'g', min: 0.6, max: 1.35, phase: 0.7 },
        { channel: 'b', min: 0.85, max: 1.35, phase: 1.4 },
      ],
    },
  },
};

export function costumeShaderBehavior(name: string | undefined): CostumeShaderBehavior | undefined {
  return name ? BEHAVIORS[name.trim().toLowerCase()] : undefined;
}

export function costumeShaderFrame(behavior: CostumeShaderBehavior, time: number): number {
  if (behavior.cycleTime <= 0) return 0;
  const cycleTime = ((time % behavior.cycleTime) + behavior.cycleTime) % behavior.cycleTime;
  for (let index = 0; index < behavior.frameThresholds.length; index += 1) {
    if (cycleTime < behavior.frameThresholds[index]!) return behavior.frameValues[index] ?? 0;
  }
  return behavior.frameValues[behavior.frameThresholds.length] ?? 0;
}

export function costumeShaderColor(
  behavior: CostumeShaderBehavior,
  time: number,
): [red: number, green: number, blue: number, alpha: number] {
  const result: [number, number, number, number] = [1, 1, 1, 1];
  const wave = behavior.colorWave;
  if (!wave) return result;
  for (const channel of wave.channels) {
    const frequency = channel.frequency ?? wave.frequency;
    const phase = channel.phase ?? 0;
    const center = (channel.max + channel.min) / 2;
    const amplitude = (channel.max - channel.min) / 2;
    const value = Math.max(0, center + amplitude * Math.sin(time * frequency + phase));
    result[{ r: 0, g: 1, b: 2, a: 3 }[channel.channel]] = value;
  }
  return result;
}
