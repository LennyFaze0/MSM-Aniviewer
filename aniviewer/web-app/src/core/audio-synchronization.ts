export interface AnimationAudioTiming {
  animationPlaybackRate: number;
  loopMultiplier: number;
}

export const DEFAULT_ANIMATION_AUDIO_TIMING: AnimationAudioTiming = {
  animationPlaybackRate: 1,
  loopMultiplier: 1,
};

export function resolveAnimationAudioTiming(
  animationDuration: number,
  audioDuration: number,
): AnimationAudioTiming {
  if (
    !Number.isFinite(animationDuration)
    || !Number.isFinite(audioDuration)
    || animationDuration <= 0.001
    || audioDuration <= 0.001
  ) return { ...DEFAULT_ANIMATION_AUDIO_TIMING };

  const ratio = audioDuration / animationDuration;
  const roundedRatio = Math.round(ratio);
  const loopMultiplier = (
    ratio >= 1.5
    && roundedRatio >= 2
    && roundedRatio <= 4
    && Math.abs(ratio - roundedRatio) <= 0.12
  ) ? roundedRatio : 1;

  return {
    animationPlaybackRate: Math.max(
      0.25,
      Math.min(4, (animationDuration * loopMultiplier) / audioDuration),
    ),
    loopMultiplier,
  };
}

export function audioTimeForAnimation(
  animationTime: number,
  animationDuration: number,
  timing: AnimationAudioTiming,
  loopIndex: number,
): number {
  const rate = Math.max(0.001, timing.animationPlaybackRate);
  const cycle = Math.max(0, Math.min(timing.loopMultiplier - 1, Math.floor(loopIndex)));
  const boundedTime = Math.max(0, Math.min(animationDuration, animationTime));
  return ((cycle * Math.max(0, animationDuration)) + boundedTime) / rate;
}

/** Do not clamp to one cycle: the renderer needs overflow to count animation loops. */
export function animationTimeForAudio(
  audioTime: number,
  animationDuration: number,
  timing: AnimationAudioTiming,
  loopIndex: number,
): number {
  return Math.max(0, audioTime * timing.animationPlaybackRate - loopIndex * animationDuration);
}

export function animationCycleForAudio(
  audioTime: number,
  animationDuration: number,
  timing: AnimationAudioTiming,
): number {
  if (animationDuration <= 0) return 0;
  return Math.max(0, Math.min(timing.loopMultiplier - 1,
    Math.floor(audioTime * timing.animationPlaybackRate / animationDuration)));
}
