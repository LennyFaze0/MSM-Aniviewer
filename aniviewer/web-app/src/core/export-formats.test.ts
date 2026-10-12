import { afterEach, describe, expect, it, vi } from 'vitest';
import { gifFrameDelay } from './gif-export';
import { compatibilityVideoMime } from './video-export-compatibility';

afterEach(() => vi.unstubAllGlobals());

describe('browser export formats', () => {
  it('rounds GIF timing cumulatively rather than introducing drift every frame', () => {
    const delays = Array.from({ length: 15 }, (_, index) => gifFrameDelay(index, 15, 1));
    expect(delays.every((delay) => delay === 60 || delay === 70)).toBe(true);
    expect(delays.reduce((sum, delay) => sum + delay, 0)).toBe(1000);
    expect(gifFrameDelay(0, 50, .02)).toBe(20);
  });

  it('selects actual recorder codecs without renaming unsupported formats', () => {
    vi.stubGlobal('HTMLCanvasElement', class { captureStream() {} });
    vi.stubGlobal('MediaRecorder', { isTypeSupported: (mime: string) => mime.startsWith('video/webm') });
    expect(compatibilityVideoMime('auto', true)).toBe('video/webm;codecs=vp9,opus');
    expect(compatibilityVideoMime('webm', false)).toBe('video/webm;codecs=vp9');
    expect(compatibilityVideoMime('mp4', true)).toBeUndefined();
    expect(compatibilityVideoMime('mov', false)).toBeUndefined();
  });

  it('handles missing APIs without crashing an export button handler', () => {
    vi.stubGlobal('MediaRecorder', undefined);
    expect(compatibilityVideoMime('auto', true)).toBeUndefined();
  });
});
