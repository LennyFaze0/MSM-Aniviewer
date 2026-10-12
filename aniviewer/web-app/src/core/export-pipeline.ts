import {
  BlobReader,
  BlobWriter,
  ZipWriter,
} from '@zip.js/zip.js';

export type ExportFramingMode = 'animation' | 'content';

export interface ExportFramePlan {
  animationTimes: number[];
  boundsSampleTimes: number[];
  frameRate: number;
  playbackRate: number;
  realDuration: number;
}

export interface PngSequenceArchiveOptions {
  animationName: string;
  animationTimes: readonly number[];
  captureFrame: (animationTime: number, frameIndex: number) => Promise<Blob>;
  onProgress?: (completed: number, total: number) => void;
  signal?: AbortSignal;
}

function finitePositive(value: number, fallback: number): number {
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function throwIfAborted(signal?: AbortSignal): void {
  if (!signal?.aborted) return;
  throw signal.reason instanceof Error
    ? signal.reason
    : new DOMException('Export cancelled.', 'AbortError');
}

export function sanitizeExportName(value: string, fallback = 'animation'): string {
  const sanitized = value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^0-9a-zA-Z_-]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return sanitized || fallback;
}

export function createExportFramePlan(
  duration: number,
  frameRate: number,
  playbackRate = 1,
): ExportFramePlan {
  const safeDuration = Math.max(0, Number.isFinite(duration) ? duration : 0);
  const safeFrameRate = Math.max(1, Math.min(120, Math.round(finitePositive(frameRate, 30))));
  const safePlaybackRate = Math.max(0.001, finitePositive(playbackRate, 1));
  const realDuration = safeDuration / safePlaybackRate;
  const frameCount = Math.max(1, Math.ceil(realDuration * safeFrameRate - 1e-9));
  const animationTimes = Array.from({ length: frameCount }, (_, index) => (
    Math.min(safeDuration, index / safeFrameRate * safePlaybackRate)
  ));
  const boundsSampleTimes = [...animationTimes];
  if (!boundsSampleTimes.length || Math.abs(boundsSampleTimes.at(-1)! - safeDuration) > 1e-9) {
    boundsSampleTimes.push(safeDuration);
  }
  return {
    animationTimes,
    boundsSampleTimes,
    frameRate: safeFrameRate,
    playbackRate: safePlaybackRate,
    realDuration,
  };
}

export async function buildPngSequenceArchive(
  options: PngSequenceArchiveOptions,
): Promise<Blob> {
  if (!options.animationTimes.length) throw new Error('The export frame plan is empty.');
  const archiveWriter = new BlobWriter('application/zip');
  const archive = new ZipWriter(archiveWriter);
  const folderName = sanitizeExportName(options.animationName);
  const numberWidth = Math.max(5, String(options.animationTimes.length).length);

  try {
    for (let index = 0; index < options.animationTimes.length; index += 1) {
      throwIfAborted(options.signal);
      const frame = await options.captureFrame(options.animationTimes[index]!, index);
      throwIfAborted(options.signal);
      const frameNumber = String(index + 1).padStart(numberWidth, '0');
      await archive.add(
        `${folderName}/${folderName}_${frameNumber}.png`,
        new BlobReader(frame),
        // PNG data is already compressed. Storing it directly is faster and avoids wasted CPU.
        { level: 0 },
      );
      options.onProgress?.(index + 1, options.animationTimes.length);
      if ((index + 1) % 4 === 0) {
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
      }
    }
    throwIfAborted(options.signal);
    return await archive.close();
  } catch (error) {
    try {
      await archive.close();
    } catch {
      // The original capture/cancellation error is more useful than a close failure.
    }
    throw error;
  }
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
