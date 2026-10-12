import type { AudioClipInput } from './types';

const SEEK_TOLERANCE = 0.025;

export class MediaAudioPlayback {
  private readonly element = new Audio();
  private objectUrl?: string;
  private clip?: AudioClipInput;
  private starting?: symbol;
  private generation = 0;
  private finishLoad?: () => void;
  private wantedPlaying = false;
  private failed = false;
  private lastTarget?: number;
  private pendingSeek?: number;
  private seekTarget?: number;
  private tailTime?: number;
  private resumeAfterSeek = false;
  onIssue?: (message: string) => void;
  onRecovered?: () => void;

  private readonly onPlaying = (): void => {
    this.onRecovered?.();
  };
  private readonly onSeeked = (): void => {
    if (!this.element.seeking) {
      this.seekTarget = undefined;
      this.finishSeekPlayback();
    }
  };
  private readonly onMetadata = (): void => {
    if (this.pendingSeek !== undefined) this.seek(this.pendingSeek);
    this.finishSeekPlayback();
  };
  private readonly onPause = (): void => {
    if (this.clip && this.wantedPlaying && this.element.paused && !this.element.ended
      && !this.starting && !this.resumeAfterSeek && this.tailTime === undefined && !this.failed) {
      this.fail('Audio playback was interrupted. Tap Play or Unmute to resume.');
    }
  };
  private readonly onError = (): void => {
    if (!this.clip || this.failed) return;
    const code = this.element.error?.code;
    this.fail(code === 3 || code === 4
      ? `This browser could not decode ${this.clip.name}. Its audio format may not be supported.`
      : `Could not read audio ${this.clip.name}. Try reimporting the audio file.`);
  };

  constructor() {
    this.element.preload = 'auto';
    this.element.addEventListener('playing', this.onPlaying);
    this.element.addEventListener('seeked', this.onSeeked);
    this.element.addEventListener('loadedmetadata', this.onMetadata);
    this.element.addEventListener('error', this.onError);
    this.element.addEventListener('pause', this.onPause);
  }

  get name(): string | undefined {
    return this.clip?.name;
  }

  get sourceBlob(): Blob | undefined {
    return this.clip?.blob;
  }

  get duration(): number {
    return Number.isFinite(this.element.duration) ? this.element.duration : 0;
  }

  get nativeLoopEnabled(): boolean {
    return this.element.loop;
  }

  setLoop(enabled: boolean, cycleDuration: number): void {
    // Only absorb floating-point arithmetic noise, not a shorter clip's silent tail.
    this.element.loop = enabled && this.duration > 0 && Number.isFinite(cycleDuration)
      && Math.abs(this.duration - cycleDuration) <= 1e-6;
    if (this.element.loop) this.tailTime = undefined;
  }

  async load(clip?: AudioClipInput): Promise<void> {
    ++this.generation;
    this.finishLoad?.();
    this.starting = undefined;
    this.wantedPlaying = false;
    this.failed = false;
    this.lastTarget = undefined;
    this.pendingSeek = undefined;
    this.seekTarget = undefined;
    this.tailTime = undefined;
    this.resumeAfterSeek = false;
    this.element.loop = false;
    this.element.pause();
    this.element.removeAttribute('src');
    this.element.load();
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = undefined;
    this.clip = clip;
    if (!clip) return;
    this.objectUrl = URL.createObjectURL(clip.blob);
    this.element.src = this.objectUrl;
    await new Promise<void>((resolve) => {
      if (this.element.readyState >= HTMLMediaElement.HAVE_METADATA) {
        resolve();
        return;
      }
      const finish = (): void => {
        this.element.removeEventListener('loadedmetadata', finish);
        this.element.removeEventListener('error', finish);
        if (this.finishLoad === finish) this.finishLoad = undefined;
        resolve();
      };
      this.finishLoad = finish;
      this.element.addEventListener('loadedmetadata', finish, { once: true });
      this.element.addEventListener('error', finish, { once: true });
      this.element.load();
    });
  }

  setVolume(value: number): void {
    this.element.volume = Math.max(0, Math.min(1, value));
  }

  setMuted(muted: boolean): void {
    this.element.muted = muted;
  }

  seek(time: number): void {
    if (!this.clip || !Number.isFinite(time)) return;
    this.lastTarget = time;
    this.tailTime = !this.element.loop && this.duration > 0 && time >= this.duration ? time : undefined;
    // A user can press Play/scrub before iOS has allowed metadata to load.
    if (this.element.readyState < HTMLMediaElement.HAVE_METADATA || !this.duration) {
      this.pendingSeek = time;
      return;
    }
    this.pendingSeek = undefined;
    const bounded = Math.max(0, Math.min(this.element.duration, time));
    if (Math.abs(this.element.currentTime - bounded) <= SEEK_TOLERANCE) return;
    this.seekTarget = bounded;
    this.element.currentTime = bounded;
  }

  /** Audio is the clock. Sampling it must never seek, change rate, or restart it. */
  playbackTime(elapsedSeconds: number): number | undefined {
    if (!this.clip || !this.wantedPlaying || this.failed) return undefined;
    // A shorter clip leaves a silent tail; let the animation finish/loop normally.
    if (this.tailTime !== undefined) {
      this.tailTime += Math.max(0, elapsedSeconds);
      return this.tailTime;
    }
    if (this.pendingSeek !== undefined) return this.pendingSeek;
    if (this.seekTarget !== undefined) return this.seekTarget;
    if (this.element.readyState < HTMLMediaElement.HAVE_METADATA) return this.lastTarget ?? 0;
    if (this.element.ended && !this.element.loop) this.tailTime = this.element.currentTime;
    return this.element.currentTime;
  }

  sync(time: number, playing: boolean, userGesture = false, restart = false): void {
    if (!this.clip || !Number.isFinite(time)) return;
    if (userGesture && !this.element.error) this.failed = false;
    const started = playing && !this.wantedPlaying;
    this.lastTarget = time;
    this.wantedPlaying = playing;
    if (!playing) {
      this.pause();
      this.seek(time);
      return;
    }
    if (this.failed) return;
    if (!this.element.loop && this.duration > 0 && time >= this.duration) {
      this.tailTime ??= time;
      this.pause();
      return;
    }
    // Only transport actions may reposition/restart media, never a clock difference.
    if (!started && !restart && !userGesture) return;
    if (this.element.loop && restart && !started && !userGesture) return;
    this.resumeAfterSeek = true;
    this.seek(time);
    if (this.element.paused && !this.starting) this.play();
    this.finishSeekPlayback();
  }

  private finishSeekPlayback(): void {
    if (!this.resumeAfterSeek || this.seekTarget !== undefined || this.pendingSeek !== undefined || this.starting) return;
    // One completion-bound resume, not a retry from every animation frame.
    this.resumeAfterSeek = false;
    if (this.wantedPlaying && !this.failed && this.element.paused) this.play();
  }

  private play(): void {
    const token = Symbol();
    const generation = this.generation;
    this.starting = token;
    void this.element.play().catch((error: unknown) => {
      if (generation !== this.generation || this.starting !== token) return;
      const name = error instanceof Error ? error.name : '';
      // A seek can abort the initial play request. Its completion owns one retry.
      if (name === 'AbortError' && this.resumeAfterSeek) return;
      this.fail(name === 'NotAllowedError'
        ? `Audio was blocked by the browser. Tap Play or Unmute to enable ${this.name}.`
        : `Audio could not start for ${this.name} (${name || 'playback error'}). Tap Play to retry.`);
    }).finally(() => {
      if (this.starting === token) {
        this.starting = undefined;
        this.finishSeekPlayback();
      }
    });
  }

  private pause(): void {
    this.starting = undefined;
    this.resumeAfterSeek = false;
    if (!this.element.paused) this.element.pause();
  }

  private fail(message: string): void {
    if (this.failed) return;
    this.failed = true;
    this.resumeAfterSeek = false;
    this.onIssue?.(message);
  }

  destroy(): void {
    void this.load(undefined);
    this.element.removeEventListener('playing', this.onPlaying);
    this.element.removeEventListener('seeked', this.onSeeked);
    this.element.removeEventListener('loadedmetadata', this.onMetadata);
    this.element.removeEventListener('error', this.onError);
    this.element.removeEventListener('pause', this.onPause);
  }
}
