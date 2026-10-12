import { BlobReader, ZipReader } from '@zip.js/zip.js';
import { describe, expect, test, vi } from 'vitest';
import {
  buildPngSequenceArchive,
  createExportFramePlan,
  sanitizeExportName,
} from './export-pipeline';

describe('browser export pipeline', () => {
  test('maps real output frames to animation time through playback-rate correction', () => {
    const plan = createExportFramePlan(2, 4, 2);

    expect(plan.realDuration).toBe(1);
    expect(plan.animationTimes).toEqual([0, 0.5, 1, 1.5]);
    expect(plan.boundsSampleTimes).toEqual([0, 0.5, 1, 1.5, 2]);
  });

  test('creates one frame for a zero-duration animation and clamps invalid settings', () => {
    const plan = createExportFramePlan(0, Number.NaN, 0);

    expect(plan.frameRate).toBe(30);
    expect(plan.playbackRate).toBe(1);
    expect(plan.animationTimes).toEqual([0]);
    expect(plan.boundsSampleTimes).toEqual([0]);
  });

  test('sanitizes animation names for portable downloads', () => {
    expect(sanitizeExportName('  Dén / Idle:01  ')).toBe('Den_Idle_01');
    expect(sanitizeExportName('***')).toBe('animation');
  });

  test('builds a predictably named uncompressed PNG sequence archive', async () => {
    const progress = vi.fn();
    const captureFrame = vi.fn(async (time: number) => (
      new Blob([`png:${time}`], { type: 'image/png' })
    ));

    const blob = await buildPngSequenceArchive({
      animationName: 'Test Idle',
      animationTimes: [0, 0.5],
      captureFrame,
      onProgress: progress,
    });
    const archive = new ZipReader(new BlobReader(blob));
    const entries = await archive.getEntries();

    expect(entries.map((entry) => entry.filename)).toEqual([
      'Test_Idle/Test_Idle_00001.png',
      'Test_Idle/Test_Idle_00002.png',
    ]);
    expect(captureFrame).toHaveBeenNthCalledWith(1, 0, 0);
    expect(captureFrame).toHaveBeenNthCalledWith(2, 0.5, 1);
    expect(progress).toHaveBeenLastCalledWith(2, 2);
    await archive.close();
  });

  test('stops before capture when an export is cancelled', async () => {
    const controller = new AbortController();
    controller.abort(new DOMException('Export cancelled.', 'AbortError'));
    const captureFrame = vi.fn(async () => new Blob());

    await expect(buildPngSequenceArchive({
      animationName: 'Idle',
      animationTimes: [0],
      captureFrame,
      signal: controller.signal,
    })).rejects.toMatchObject({ name: 'AbortError' });
    expect(captureFrame).not.toHaveBeenCalled();
  });
});
