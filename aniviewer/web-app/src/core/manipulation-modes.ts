export const MANIPULATION_MODES = ['select', 'move', 'rotate', 'scale', 'pan'] as const;
export type ManipulationMode = typeof MANIPULATION_MODES[number];
export const modeLabel = (mode: ManipulationMode): string => mode[0]!.toUpperCase() + mode.slice(1);
