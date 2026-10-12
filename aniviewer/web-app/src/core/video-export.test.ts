import { afterEach, describe, expect, it, vi } from 'vitest';
import { awaitExportTask, buildVideoExport, createVideoFramePlan, validateVideoExport, videoExportUnavailableReason, videoFrameTiming } from './video-export';
import type { VideoWorkerReply, VideoWorkerRequest } from './video-export-protocol';

const config = { format: 'auto' as const, width: 64, height: 64, duration: 1, frameRate: 30, bitrate: 8_000_000 };
afterEach(() => vi.unstubAllGlobals());

describe('deterministic video exports', () => {
  it('repeats the animation, not the audio, for a full corrected Muppets cycle', () => {
    const plan = createVideoFramePlan(2, 10, 4 / 4.1, 2);
    expect(plan.realDuration).toBeCloseTo(4.1);
    expect(plan.animationTimes).toHaveLength(41);
    expect(plan.animationTimes[21]).toBeCloseTo(2.1 * 4 / 4.1 - 2);
    expect(Math.max(...plan.animationTimes)).toBeLessThan(2);
    expect(plan.boundsSampleTimes.at(-1)).toBe(2);
  });

  it('timestamps frames without cumulative rounding and trims the last frame', () => {
    expect(videoFrameTiming(1, 30, 1)).toEqual({ timestamp: 33333, duration: 33334 });
    expect(videoFrameTiming(2, 30, 0.08)).toEqual({ timestamp: 66667, duration: 13333 });
    expect(videoFrameTiming(300, 30, 20).timestamp).toBe(10_000_000);
  });

  it('rejects invalid dimensions, nonfinite timing, and excessive output memory', () => {
    expect(() => validateVideoExport(config)).not.toThrow();
    for (const value of [{ width: 63 }, { height: 10000 }, { duration: NaN }, { duration: 900 }, { frameRate: 0 }]) {
      expect(() => validateVideoExport({ ...config, ...value })).toThrow();
    }
  });

  it('explains insecure contexts and missing browser capabilities', () => {
    vi.stubGlobal('isSecureContext', false);
    expect(videoExportUnavailableReason()).toContain('HTTPS');
    vi.stubGlobal('isSecureContext', true);
    vi.stubGlobal('VideoEncoder', undefined);
    expect(videoExportUnavailableReason()).toContain('WebCodecs');
  });

  it('cancels preparation without waiting for a native decoder to finish', async () => {
    const controller = new AbortController();
    const work = awaitExportTask(new Promise(() => {}), controller.signal);
    controller.abort();
    await expect(work).rejects.toMatchObject({ name: 'AbortError' });
  });

  function workerHarness(stall = false) {
    const instances: FakeWorker[] = [];
    class FakeWorker {
      onmessage?: (message: { data: VideoWorkerReply }) => void;
      onerror?: (event: { message: string }) => void;
      terminate = vi.fn();
      inFlight = 0;
      maxInFlight = 0;
      constructor() { instances.push(this); }
      postMessage(message: VideoWorkerRequest) {
        if (message.type === 'frame') {
          this.maxInFlight = Math.max(this.maxInFlight, ++this.inFlight);
          if (stall) return;
        }
        setTimeout(() => {
          if (message.type === 'frame') this.inFlight--;
          this.onmessage?.({ data: { id: message.id,
            selection: message.type === 'init' ? { format: 'webm', videoCodec: 'vp9', hardwareAcceleration: 'no-preference' } : undefined,
            buffer: message.type === 'finish' ? new ArrayBuffer(16) : undefined,
          } });
        }, 5);
      }
    }
    vi.stubGlobal('isSecureContext', true);
    vi.stubGlobal('Worker', FakeWorker);
    vi.stubGlobal('VideoFrame', class {});
    vi.stubGlobal('VideoEncoder', class {});
    return instances;
  }

  it('bounds transferred frames and closes all renderer-owned frames', async () => {
    const workers = workerHarness();
    const close = vi.fn();
    const result = await buildVideoExport({ ...config, animationTimes: [0, .2, .4, .6, .8],
      signal: new AbortController().signal, captureFrame: () => ({ close }) as unknown as VideoFrame });
    expect(result.blob.type).toBe('video/webm');
    expect(close).toHaveBeenCalledTimes(5);
    expect(workers[0]!.maxInFlight).toBeLessThanOrEqual(2);
    expect(workers[0]!.terminate).toHaveBeenCalled();
  });

  it('terminates a stalled encoder immediately on cancellation', async () => {
    const workers = workerHarness(true);
    const controller = new AbortController();
    const job = buildVideoExport({ ...config, animationTimes: [0, .2, .4], signal: controller.signal,
      captureFrame: () => ({ close() {} }) as VideoFrame });
    const rejection = expect(job).rejects.toMatchObject({ name: 'AbortError' });
    await new Promise((resolve) => setTimeout(resolve, 20));
    controller.abort();
    await rejection;
    expect(workers[0]!.terminate).toHaveBeenCalled();
  });

  it('cleans up when rendering throws while another frame is in flight', async () => {
    const workers = workerHarness();
    let frames = 0;
    await expect(buildVideoExport({ ...config, animationTimes: [0, .2], signal: new AbortController().signal,
      captureFrame: () => {
        if (++frames > 1) throw new Error('Capture failed');
        return { close() {} } as VideoFrame;
      },
    })).rejects.toThrow('Capture failed');
    expect(workers[0]!.terminate).toHaveBeenCalled();
  });
});
