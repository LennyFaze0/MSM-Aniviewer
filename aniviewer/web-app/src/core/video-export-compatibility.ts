import { awaitExportTask } from './video-export';
import { MAX_VIDEO_EXPORT_BYTES, type ExportAudioPcm, type VideoExportFormat } from './video-export-protocol';

export function compatibilityVideoMime(format: VideoExportFormat, audio: boolean): string | undefined {
  if (typeof MediaRecorder === 'undefined' || typeof HTMLCanvasElement.prototype.captureStream !== 'function') return undefined;
  if (format === 'mov') return undefined;
  const formats = format === 'auto' ? ['mp4', 'webm'] : [format];
  for (const container of formats) {
    const candidates = container === 'mp4'
      ? [`video/mp4;codecs=avc1.42E01E${audio ? ',mp4a.40.2' : ''}`, 'video/mp4']
      : [`video/webm;codecs=vp9${audio ? ',opus' : ''}`, `video/webm;codecs=vp8${audio ? ',opus' : ''}`, 'video/webm'];
    for (const mime of candidates) if (MediaRecorder.isTypeSupported(mime)) return mime;
  }
  return undefined;
}

export interface CompatibilityAudioContext {
  context: AudioContext;
  ready: Promise<void>;
}

/** Call from the Export button's gesture, before waiting for assets or bounds. */
export function prepareCompatibilityAudio(): CompatibilityAudioContext {
  const context = new AudioContext({ sampleRate: 48_000 });
  const ready = context.resume();
  void ready.catch(() => undefined);
  return { context, ready };
}

export async function buildCompatibilityVideo(options: {
  format: VideoExportFormat;
  width: number; height: number; frameRate: number; bitrate: number; duration: number;
  animationTimes: readonly number[];
  audio?: ExportAudioPcm;
  audioContext?: CompatibilityAudioContext;
  captureCanvas: (time: number) => HTMLCanvasElement | OffscreenCanvas;
  signal: AbortSignal;
  onProgress?: (elapsed: number, duration: number) => void;
}): Promise<{ blob: Blob; selection: { format: 'mp4' | 'webm' }; renderedFrames: number }> {
  const mimeType = compatibilityVideoMime(options.format, Boolean(options.audio));
  if (!mimeType) throw new Error(`This browser cannot record ${options.format.toUpperCase()}. Try Auto format or GIF/PNG; codec availability differs by browser.`);
  options.signal.throwIfAborted();
  if (document.hidden) throw new Error('Keep the viewer tab visible during compatibility export.');
  const canvas = document.createElement('canvas');
  canvas.width = options.width; canvas.height = options.height;
  const paint = canvas.getContext('2d', { alpha: false })!;
  const draw = (index: number) => {
    paint.drawImage(options.captureCanvas(options.animationTimes[index]!), 0, 0);
  };
  draw(0);
  const stream = canvas.captureStream(options.frameRate);
  let source: AudioBufferSourceNode | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let safetyTimer: ReturnType<typeof setTimeout> | undefined;
  let recorder: MediaRecorder | undefined;
  const chunks: Blob[] = [];
  let bytes = 0;
  let renderedFrames = 1;
  let cleanupListeners = () => {};
  try {
    if (options.audio) {
      if (!options.audioContext) throw new Error('Tap Export again to authorize compatibility audio.');
      const { context, ready } = options.audioContext;
      const timeout = new AbortController();
      safetyTimer = setTimeout(() => timeout.abort(new Error('Audio is blocked. Tap Export again, or turn off Include matched audio.')), 10_000);
      await awaitExportTask(awaitExportTask(ready, timeout.signal), options.signal);
      clearTimeout(safetyTimer);
      options.signal.throwIfAborted();
      const pcm = options.audio;
      const buffer = context.createBuffer(pcm.channels.length, pcm.channels[0]!.length, pcm.sampleRate);
      pcm.channels.forEach((channel, index) => buffer.copyToChannel(channel, index));
      source = context.createBufferSource(); source.buffer = buffer;
      const destination = context.createMediaStreamDestination();
      source.connect(destination); // Capture only, never route exported audio to the speakers.
      destination.stream.getAudioTracks().forEach((track) => stream.addTrack(track));
    }
    recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: options.bitrate, audioBitsPerSecond: 192_000 });
    const completed = new Promise<void>((resolve, reject) => {
      const fail = (error: Error) => { reject(error); if (recorder?.state !== 'inactive') recorder?.stop(); };
      const abort = () => fail(options.signal.reason instanceof Error ? options.signal.reason : new DOMException('Export cancelled.', 'AbortError'));
      const visibility = () => { if (document.hidden) fail(new Error('Compatibility export stopped because the tab was hidden. Keep it visible and retry.')); };
      options.signal.addEventListener('abort', abort, { once: true });
      document.addEventListener('visibilitychange', visibility);
      cleanupListeners = () => { options.signal.removeEventListener('abort', abort); document.removeEventListener('visibilitychange', visibility); };
      recorder!.ondataavailable = ({ data }) => {
        bytes += data.size;
        if (bytes > MAX_VIDEO_EXPORT_BYTES) { fail(new Error('Recording exceeds the 128 MiB export limit. Lower the bitrate.')); return; }
        if (data.size) chunks.push(data);
      };
      recorder!.onerror = () => fail(new Error('The browser recorder failed. Try Auto format, a lower resolution, or GIF.'));
      recorder!.onstop = () => resolve();
      // MediaRecorder is real-time, not the deterministic offline encoder. Wall-time scheduling may skip frames under load.
      recorder!.onstart = () => {
        if (options.signal.aborted) { abort(); return; }
        draw(0);
        const start = performance.now();
        const audioStart = options.audioContext?.context.currentTime ?? 0;
        source?.start();
        let previous = 0;
        const tick = () => {
          try {
            options.signal.throwIfAborted();
            const elapsed = source ? options.audioContext!.context.currentTime - audioStart : (performance.now() - start) / 1000;
            if (elapsed >= options.duration) { recorder!.stop(); return; }
            const index = Math.min(options.animationTimes.length - 1, Math.floor(elapsed * options.frameRate));
            if (index !== previous) { draw(index); renderedFrames++; previous = index; }
            options.onProgress?.(elapsed, options.duration);
            timer = setTimeout(tick, Math.max(1, Math.min(16, 500 / options.frameRate)));
          } catch (error) { fail(error instanceof Error ? error : new Error(String(error))); }
        };
        tick();
      };
      recorder!.start(500);
      safetyTimer = setTimeout(() => fail(new Error('The browser recorder stalled. Keep the tab visible and retry.')), (options.duration + 15) * 1000);
    });
    await completed;
    options.signal.throwIfAborted();
    if (!chunks.length) throw new Error('The browser recorder produced an empty file. Try another format.');
    const format = recorder.mimeType.includes('mp4') ? 'mp4' : 'webm';
    return { blob: new Blob(chunks, { type: format === 'mp4' ? 'video/mp4' : 'video/webm' }), selection: { format }, renderedFrames };
  } finally {
    clearTimeout(timer); clearTimeout(safetyTimer); cleanupListeners();
    try { source?.stop(); } catch { /* The recorder can fail before the source starts. */ }
    source?.disconnect();
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    stream.getTracks().forEach((track) => track.stop());
  }
}
