/** One decoded clip; rendering only reads this clock, never corrects it with seeks. */
export class BufferedAudioPlayback {
  private buffer?: AudioBuffer;
  private source?: AudioBufferSourceNode;
  private readonly gain: GainNode;
  private offset = 0;
  private startedAt = 0;
  private playing = false;
  private volume = 1;
  private muted = false;
  private loop = false;
  private warned = false;
  private disposed = false;
  private resuming?: symbol;
  private readonly stateChanged = (): void => {
    if (this.disposed || !this.playing) return;
    if (this.context.state === 'running') {
      this.warned = false;
      this.onRecovered?.();
    } else if (!this.resuming) this.issue();
  };
  onIssue?: (message: string) => void;
  onRecovered?: () => void;

  constructor(private readonly context: AudioContext, private readonly name: string) {
    this.gain = context.createGain();
    this.gain.connect(context.destination);
    context.addEventListener('statechange', this.stateChanged);
  }

  get duration(): number { return this.buffer?.duration ?? 0; }
  get nativeLoopEnabled(): boolean { return this.loop; }

  setBuffer(buffer: AudioBuffer): void {
    if (this.disposed) return;
    this.buffer = buffer;
    if (this.playing) this.startSource();
  }

  setLoop(enabled: boolean, cycleDuration: number): void {
    const position = this.position();
    this.loop = enabled && this.duration > 0 && Number.isFinite(cycleDuration)
      && Math.abs(this.duration - cycleDuration) <= 1e-6;
    // Disabling after several loops must finish this cycle, not jump to the elapsed total.
    this.offset = position; this.startedAt = this.context.currentTime;
    if (this.source) this.source.loop = this.loop;
  }

  setVolume(value: number): void { this.volume = Math.max(0, Math.min(1, value)); this.updateGain(); }
  setMuted(muted: boolean): void { this.muted = muted; this.updateGain(); }
  private updateGain(): void { this.gain.gain.value = this.muted ? 0 : this.volume; }

  private position(): number {
    // Decoding and context suspension must not consume the start of the track.
    const time = this.offset + (this.playing && this.buffer ? Math.max(0, this.context.currentTime - this.startedAt) : 0);
    return this.loop && this.duration ? time % this.duration : time;
  }

  playbackTime(_elapsedSeconds: number): number | undefined {
    return this.playing ? this.position() : undefined;
  }

  seek(time: number): void {
    if (!Number.isFinite(time) || this.disposed) return;
    this.offset = Math.max(0, time); this.startedAt = this.context.currentTime;
    if (this.playing) this.startSource();
  }

  sync(time: number, playing: boolean, userGesture = false, restart = false): void {
    if (!Number.isFinite(time) || this.disposed) return;
    const started = playing && !this.playing;
    this.playing = playing;
    if (!playing) {
      this.stopSource(); this.offset = Math.max(0, time); return;
    }
    // Invoke resume synchronously inside Play/Unmute, even while decoding.
    if (userGesture || started) this.resume(userGesture);
    if (started || (restart && (!this.loop || userGesture))) this.seek(time);
  }

  resume(userGesture = false): void {
    if (this.disposed || !this.playing || this.context.state === 'running' || (this.resuming && !userGesture)) return;
    const token = Symbol(); this.resuming = token;
    void this.context.resume().then(() => {
      if (this.disposed || !this.playing || this.resuming !== token) return;
      if (this.context.state === 'running') { this.warned = false; this.onRecovered?.(); }
      else this.issue();
    }, () => { if (!this.disposed && this.playing && this.resuming === token) this.issue(); })
      .finally(() => { if (this.resuming === token) this.resuming = undefined; });
  }

  private startSource(): void {
    this.stopSource();
    this.startedAt = this.context.currentTime;
    if (!this.buffer || !this.playing) return;
    if (this.loop) this.offset %= this.duration;
    if (this.offset >= this.duration) return; // Intentional silent tail until the animation's next cycle.
    const source = this.context.createBufferSource();
    source.buffer = this.buffer; source.loop = this.loop;
    source.connect(this.gain); this.source = source;
    source.onended = () => {
      source.disconnect();
      if (this.source === source) this.source = undefined;
    };
    // No duration argument: the full decoded clip (and its native loop) owns the end.
    source.start(0, this.offset);
    if (this.context.state === 'running') { this.warned = false; this.onRecovered?.(); }
  }

  private stopSource(): void {
    if (!this.source) return;
    this.source.onended = null; this.source.stop(); this.source.disconnect(); this.source = undefined;
  }

  private issue(): void {
    if (this.warned) return;
    this.warned = true;
    this.onIssue?.(`Audio context ${this.context.state} for ${this.name}. Playback is held in sync. Tap Play or Unmute to resume.`);
  }

  destroy(): void {
    this.disposed = true; this.playing = false; this.stopSource(); this.buffer = undefined;
    this.gain.disconnect(); this.context.removeEventListener('statechange', this.stateChanged);
  }
}
