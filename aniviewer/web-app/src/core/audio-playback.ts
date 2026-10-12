import type { AudioClipInput } from './types';
import { BufferedAudioPlayback } from './buffered-audio-playback';
import { MediaAudioPlayback } from './media-audio-playback';

const MAX_ENCODED_BYTES = 8 * 1024 * 1024;
const MAX_PCM_BYTES = 32 * 1024 * 1024;

/** Inspect before allocating PCM; large/unknown formats retain the media fallback. */
async function decodeClip(context: AudioContext, blob: Blob, current: () => boolean): Promise<AudioBuffer | undefined> {
  if (!current() || blob.size > MAX_ENCODED_BYTES) return;
  const { Input, BlobSource, ALL_FORMATS } = await import('mediabunny');
  if (!current()) return;
  const input = new Input({ source: new BlobSource(blob), formats: ALL_FORMATS });
  try {
    const track = await input.getPrimaryAudioTrack();
    if (!track) return;
    const duration = await track.computeDuration(), channels = await track.getNumberOfChannels();
    if (!current() || !Number.isFinite(duration) || duration <= 0 || channels < 1 || channels > 2
      || Math.ceil(duration * context.sampleRate) * channels * 4 > MAX_PCM_BYTES) return;
    const bytes = await blob.arrayBuffer();
    if (!current()) return;
    const buffer = await context.decodeAudioData(bytes);
    if (!current() || buffer.length * buffer.numberOfChannels * 4 > MAX_PCM_BYTES) return;
    return buffer;
  } finally { input.dispose(); }
}

export class AudioPlayback {
  private engine?: BufferedAudioPlayback | MediaAudioPlayback;
  private context?: AudioContext;
  private clip?: AudioClipInput;
  private generation = 0;
  private queue: Promise<void> = Promise.resolve();
  private playing = false;
  private time = 0;
  private volume = 1;
  private muted = false;
  private loop = false;
  private cycleDuration = 0;
  private disposed = false;
  private readonly onVisible = (): void => {
    if (!document.hidden && this.engine instanceof BufferedAudioPlayback) this.engine.resume();
  };
  onIssue?: (message: string) => void;
  onRecovered?: () => void;

  constructor() { if (typeof document !== 'undefined') document.addEventListener('visibilitychange', this.onVisible); }
  get name(): string | undefined { return this.clip?.name; }
  get sourceBlob(): Blob | undefined { return this.clip?.blob; }
  get duration(): number { return this.engine?.duration ?? 0; }
  get nativeLoopEnabled(): boolean { return this.engine?.nativeLoopEnabled ?? false; }
  get backend(): 'buffer' | 'media' | 'none' {
    return this.engine instanceof BufferedAudioPlayback ? 'buffer' : this.engine ? 'media' : 'none';
  }

  unlock(): void {
    // The asset lookup can still be pending on an iPhone's first Play tap.
    // Authorize the shared context now, not after that asynchronous work finishes.
    if (this.disposed || typeof AudioContext === 'undefined') return;
    try {
      this.context ??= new AudioContext({ latencyHint: 'playback' });
      if (this.context.state !== 'running') void this.context.resume().catch(() => {});
    } catch { /* The media fallback can still handle devices without Web Audio. */ }
  }

  async load(clip?: AudioClipInput): Promise<void> {
    const generation = ++this.generation;
    this.clearEngine();
    this.clip = clip; this.playing = false; this.time = 0; this.loop = false; this.cycleDuration = 0;
    if (!clip || this.disposed) return;
    const current = () => generation === this.generation && !this.disposed;
    if (clip.blob.size <= MAX_ENCODED_BYTES && typeof AudioContext !== 'undefined') {
      try {
        this.context ??= new AudioContext({ latencyHint: 'playback' });
        const buffered = new BufferedAudioPlayback(this.context, clip.name);
        this.attach(buffered);
        // One decode at a time; rapid track switching cannot accumulate PCM allocations.
        const task = this.queue.then(async () => {
          const buffer = await decodeClip(this.context!, clip.blob, current);
          if (buffer && current()) buffered.setBuffer(buffer);
        });
        this.queue = task.catch(() => {});
        await task;
        if (!current()) return;
        if (buffered.duration) {
          this.setLoop(this.loop, this.cycleDuration);
          return;
        }
      } catch { if (!current()) return; }
    }
    if (!current()) return;
    this.clearEngine();
    const media = new MediaAudioPlayback(); this.attach(media);
    await media.load(clip);
    if (!current()) return;
    this.setLoop(this.loop, this.cycleDuration);
    media.sync(this.time, this.playing);
  }

  private attach(engine: BufferedAudioPlayback | MediaAudioPlayback): void {
    this.engine = engine;
    engine.onIssue = (message) => { if (this.engine === engine) this.onIssue?.(message); };
    engine.onRecovered = () => { if (this.engine === engine) this.onRecovered?.(); };
    engine.setVolume(this.volume); engine.setMuted(this.muted);
  }

  private clearEngine(): void { this.engine?.destroy(); this.engine = undefined; }

  setLoop(enabled: boolean, duration: number): void {
    this.loop = enabled; this.cycleDuration = duration; this.engine?.setLoop(enabled, duration);
  }
  setVolume(value: number): void { this.volume = value; this.engine?.setVolume(value); }
  setMuted(muted: boolean): void { this.muted = muted; this.engine?.setMuted(muted); }
  seek(time: number): void { this.time = time; this.engine?.seek(time); }
  playbackTime(elapsed: number): number | undefined { return this.engine?.playbackTime(elapsed); }
  sync(time: number, playing: boolean, userGesture = false, restart = false): void {
    if (userGesture && !this.engine) this.unlock();
    this.time = time; this.playing = playing; this.engine?.sync(time, playing, userGesture, restart);
  }
  destroy(): void {
    this.disposed = true; ++this.generation; this.engine?.destroy(); this.engine = undefined; this.clip = undefined;
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', this.onVisible);
    void this.context?.close().catch(() => {});
  }
}
