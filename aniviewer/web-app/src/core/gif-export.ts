import { createExportFramePlan } from './export-pipeline';
import { awaitExportTask } from './video-export';

export function gifFrameDelay(index: number, fps: number, duration: number): number {
  return Math.max(20, (Math.round(Math.min((index + 1) / fps, duration) * 100) - Math.round(index / fps * 100)) * 10);
}

export async function buildGifExport(options: {
  duration: number; frameRate: number; playbackRate: number; colors: number; repeat: number;
  captureCanvas: (time: number) => HTMLCanvasElement | OffscreenCanvas;
  signal: AbortSignal;
  onProgress?: (completed: number, total: number) => void;
}): Promise<{ blob: Blob; frames: number }> {
  if (typeof Worker === 'undefined') throw new Error('GIF export requires Web Worker support.');
  const plan = createExportFramePlan(options.duration, Math.min(50, options.frameRate), options.playbackRate);
  const worker = new Worker(new URL('./gif-export.worker.ts', import.meta.url), { type: 'module' });
  let timer: ReturnType<typeof setTimeout> | undefined;
  const request = (data: object, transfer: Transferable[] = []) => awaitExportTask(new Promise<{ buffer?: ArrayBuffer }>((resolve, reject) => {
    worker.onmessage = ({ data }) => {
      clearTimeout(timer);
      if (data.error) reject(new Error(data.error)); else resolve(data);
    };
    worker.onerror = (event) => { clearTimeout(timer); reject(new Error(event.message || 'GIF encoder failed.')); };
    timer = setTimeout(() => reject(new Error('GIF encoder stalled. Reduce resolution and retry.')), 60_000);
    worker.postMessage(data, transfer);
  }), options.signal);
  const scratch = document.createElement('canvas');
  const ctx = scratch.getContext('2d', { willReadFrequently: true })!;
  try {
    for (let index = 0; index < plan.animationTimes.length; index++) {
      options.signal.throwIfAborted();
      const canvas = options.captureCanvas(plan.animationTimes[index]!);
      if (canvas.width * canvas.height > 4096 * 4096) throw new Error('GIF frames exceed the 16 megapixel limit. Lower the resolution.');
      scratch.width = canvas.width; scratch.height = canvas.height;
      ctx.drawImage(canvas, 0, 0);
      const pixels = ctx.getImageData(0, 0, scratch.width, scratch.height).data;
      await request({ pixels, width: scratch.width, height: scratch.height, colors: Math.max(2, Math.min(256, options.colors)),
        delay: gifFrameDelay(index, plan.frameRate, plan.realDuration), repeat: options.repeat }, [pixels.buffer]);
      options.onProgress?.(index + 1, plan.animationTimes.length);
    }
    const result = await request({ finish: true });
    options.signal.throwIfAborted();
    return { blob: new Blob([result.buffer!], { type: 'image/gif' }), frames: plan.animationTimes.length };
  } finally { clearTimeout(timer); worker.terminate(); }
}
