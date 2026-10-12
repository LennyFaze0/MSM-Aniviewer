import { createExportFramePlan, type ExportFramePlan } from './export-pipeline';
import {
  MAX_EXPORT_PCM_BYTES,
  MAX_VIDEO_EXPORT_BYTES,
  type ExportAudioPcm,
  type VideoEncoderSelection,
  type VideoExportInit,
  type VideoWorkerCommand,
  type VideoWorkerReply,
} from './video-export-protocol';

export function videoExportUnavailableReason(): string | undefined {
  if (!globalThis.isSecureContext) return 'Fast video export requires HTTPS or localhost. PNG exports still work here.';
  if (typeof Worker === 'undefined' || typeof VideoFrame === 'undefined' || typeof VideoEncoder === 'undefined') {
    return 'This browser does not expose WebCodecs video encoding. Use PNG sequences or a browser with WebCodecs support.';
  }
  return undefined;
}

export function createVideoFramePlan(
  duration: number, fps: number, playbackRate: number, loops = 1,
): ExportFramePlan {
  const count = Math.max(1, Math.floor(Number.isFinite(loops) ? loops : 1));
  const plan = createExportFramePlan(duration * count, fps, playbackRate);
  if (duration > 0 && count > 1) {
    plan.animationTimes = plan.animationTimes.map((time) => time % duration);
    plan.boundsSampleTimes = [...plan.animationTimes, duration];
  }
  return plan;
}

export function validateVideoExport(options: VideoExportInit): void {
  if (![options.width, options.height].every((value) => Number.isInteger(value) && value >= 2 && value <= 8192 && value % 2 === 0)) {
    throw new Error('Video dimensions must be even and between 2 and 8192 pixels. Lower the export resolution.');
  }
  if (!Number.isFinite(options.duration) || options.duration <= 0
    || !Number.isFinite(options.frameRate) || options.frameRate < 1 || options.frameRate > 120
    || !Number.isFinite(options.bitrate) || options.bitrate < 100_000 || options.bitrate > 100_000_000) {
    throw new Error('Invalid video duration, frame rate, or bitrate.');
  }
  if (options.width * options.height * 4 > MAX_EXPORT_PCM_BYTES) {
    throw new Error('Video frames are too large for the browser export memory budget. Lower the resolution.');
  }
  if (options.duration * (options.bitrate + 192_000) / 8 > MAX_VIDEO_EXPORT_BYTES) {
    throw new Error('Estimated video size exceeds the 128 MiB in-memory export limit. Lower the bitrate or export PNG frames.');
  }
}

export function videoFrameTiming(index: number, fps: number, duration: number): { timestamp: number; duration: number } {
  const timestamp = Math.round(index / fps * 1_000_000);
  const end = Math.round(Math.min((index + 1) / fps, duration) * 1_000_000);
  return { timestamp, duration: Math.max(1, end - timestamp) };
}

export function awaitExportTask<T>(task: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => { signal.removeEventListener('abort', abort); reject(signal.reason ?? new DOMException('Export cancelled.', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true });
    task.then((value) => { signal.removeEventListener('abort', abort); resolve(value); },
      (error) => { signal.removeEventListener('abort', abort); reject(error); });
    if (signal.aborted) abort();
  });
}

export async function decodeExportAudio(blob: Blob, duration: number, signal: AbortSignal): Promise<ExportAudioPcm> {
  signal.throwIfAborted();
  // Decode independently of the playback element, with no live audio device or autoplay requirement.
  const context = new OfflineAudioContext(2, 1, 48_000);
  const bytes = await awaitExportTask(blob.arrayBuffer(), signal);
  const decoded = await awaitExportTask(context.decodeAudioData(bytes), signal);
  signal.throwIfAborted();
  const length = Math.min(decoded.length, Math.ceil(duration * decoded.sampleRate));
  if (decoded.numberOfChannels > 2 || decoded.length * decoded.numberOfChannels * 4 > MAX_EXPORT_PCM_BYTES) {
    throw new Error('This audio clip exceeds the stereo/128 MiB export limit. Disable Include matched audio to export video only.');
  }
  const channels = Array.from({ length: decoded.numberOfChannels }, (_, channel) => (
    decoded.getChannelData(channel).slice(0, length)
  ));
  return { sampleRate: decoded.sampleRate, channels };
}

class EncoderWorker {
  private readonly worker = new Worker(new URL('./video-export.worker.ts', import.meta.url), { type: 'module' });
  private nextId = 0;
  private pending = new Map<number, { resolve: (reply: VideoWorkerReply) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  private failure?: Error;
  private readonly abort = () => this.close(this.signal.reason instanceof Error
    ? this.signal.reason : new DOMException('Export cancelled.', 'AbortError'));

  constructor(private readonly signal: AbortSignal) {
    this.worker.onmessage = ({ data }: MessageEvent<VideoWorkerReply>) => {
      if (data.error) { this.close(new Error(data.error)); return; }
      const request = this.pending.get(data.id);
      if (!request) return;
      clearTimeout(request.timer);
      this.pending.delete(data.id);
      request.resolve(data);
    };
    this.worker.onerror = (event) => this.close(new Error(event.message || 'Video encoder worker failed.'));
    this.worker.onmessageerror = () => this.close(new Error('Could not transfer a frame to the video encoder.'));
    signal.addEventListener('abort', this.abort, { once: true });
    if (signal.aborted) this.abort();
  }

  request(command: VideoWorkerCommand, transfer: Transferable[] = []): Promise<VideoWorkerReply> {
    if (this.failure) return Promise.reject(this.failure);
    const id = ++this.nextId;
    const promise = new Promise<VideoWorkerReply>((resolve, reject) => {
      const timer = setTimeout(() => this.close(new Error('The video encoder stopped responding. Try a lower resolution or another browser.')), 60_000);
      this.pending.set(id, { resolve, reject, timer });
      try { this.worker.postMessage({ ...command, id }, transfer); }
      catch (error) { this.close(error instanceof Error ? error : new Error(String(error))); }
    });
    // A later queued frame can fail before the caller awaits the oldest one.
    void promise.catch(() => undefined);
    return promise;
  }

  close(error = new Error('Video encoder closed.')): void {
    this.failure ??= error;
    this.worker.terminate();
    this.signal.removeEventListener('abort', this.abort);
    for (const request of this.pending.values()) { clearTimeout(request.timer); request.reject(this.failure); }
    this.pending.clear();
  }
}

export interface VideoExportOptions extends VideoExportInit {
  animationTimes: readonly number[];
  captureFrame: (time: number, timing: { timestamp: number; duration: number }) => VideoFrame;
  signal: AbortSignal;
  onEncoder?: (selection: VideoEncoderSelection) => void;
  onProgress?: (completed: number, total: number) => void;
}

export async function buildVideoExport(options: VideoExportOptions): Promise<{ blob: Blob; selection: VideoEncoderSelection; elapsedSeconds: number }> {
  const unavailable = videoExportUnavailableReason();
  if (unavailable) throw new Error(unavailable);
  validateVideoExport(options);
  if (!options.animationTimes.length) throw new Error('The export frame plan is empty.');
  options.signal.throwIfAborted();
  const started = performance.now();
  const encoder = new EncoderWorker(options.signal);
  try {
    const { animationTimes, captureFrame, signal, onEncoder, onProgress, ...init } = options;
    const ready = await encoder.request({ type: 'init', options: init }, init.audio?.channels.map((channel) => channel.buffer) ?? []);
    const selection = ready.selection!;
    onEncoder?.(selection);
    const pending: Promise<VideoWorkerReply>[] = [];
    let completed = 0;
    for (let index = 0; index < animationTimes.length; index += 1) {
      signal.throwIfAborted();
      const frame = captureFrame(animationTimes[index]!, videoFrameTiming(index, init.frameRate, init.duration));
      try { pending.push(encoder.request({ type: 'frame', frame }, [frame])); }
      finally { frame.close(); }
      // Bounded pipeline: rendering may overlap native encoding, never accumulating an animation's raw frames.
      if (pending.length >= 2) {
        await pending.shift();
        onProgress?.(++completed, animationTimes.length);
      }
      if (index % 4 === 0) await new Promise<void>((resolve) => setTimeout(resolve, 0));
    }
    for (const frame of pending) { await frame; onProgress?.(++completed, animationTimes.length); }
    signal.throwIfAborted();
    const result = await encoder.request({ type: 'finish' });
    signal.throwIfAborted();
    return {
      blob: new Blob([result.buffer!], { type: selection.format === 'mov' ? 'video/quicktime' : selection.format === 'mp4' ? 'video/mp4' : 'video/webm' }),
      selection,
      elapsedSeconds: (performance.now() - started) / 1000,
    };
  } finally { encoder.close(); }
}
