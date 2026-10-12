import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { MediaAudioPlayback as AudioPlayback } from './media-audio-playback';

class DelayedAudio extends EventTarget {
  preload = '';
  src = '';
  duration = 10;
  readyState = 4;
  paused = true;
  ended = false;
  seeking = false;
  volume = 1;
  muted = false;
  loop = false;
  playbackRate = 1;
  error: { code: number; message: string } | null = null;
  position = 0;
  seeks: number[] = [];
  get currentTime(): number { return this.position; }
  set currentTime(value: number) {
    this.seeks.push(value);
    this.position = value;
    this.seeking = true;
  }
  play = vi.fn((): Promise<void> => { this.paused = false; return Promise.resolve(); });
  pause = vi.fn(() => { this.paused = true; });
  load(): void {}
  removeAttribute(): void { this.src = ''; }
  finishSeek(): void { this.seeking = false; this.dispatchEvent(new Event('seeked')); }
}

const clip = { name: 'song.wav', path: 'song.wav', blob: new Blob(['audio']) };
let media: DelayedAudio;
let playback: AudioPlayback;
const flush = async (): Promise<void> => { await Promise.resolve(); await Promise.resolve(); };

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['performance'] });
  media = new DelayedAudio();
  vi.stubGlobal('Audio', function () { return media; });
  vi.stubGlobal('HTMLMediaElement', { HAVE_METADATA: 1, HAVE_FUTURE_DATA: 3 });
  playback = new AudioPlayback();
  await playback.load(clip);
});
afterEach(() => { playback.destroy(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('audio transport', () => {
  test('does not chase a moving animation clock while play is still starting', async () => {
    let started!: () => void;
    media.play.mockImplementation(() => new Promise<void>((resolve) => { started = resolve; }));
    playback.sync(0, true);
    for (let i = 1; i <= 60; i++) playback.sync(i / 60, true);
    expect(media.play).toHaveBeenCalledTimes(1);
    expect(media.seeks).toEqual([]);
    started();
    await flush();
  });

  test('lets a slow seek finish instead of restarting it every frame', async () => {
    playback.sync(0, true);
    await flush();
    playback.seek(3);
    for (let i = 1; i <= 60; i++) playback.sync(3 + i / 60, true);
    expect(media.seeks).toEqual([3]);
  });

  test('does not seek into a stalled or buffering decoder', async () => {
    playback.sync(0, true);
    await flush();
    media.readyState = 2;
    media.dispatchEvent(new Event('waiting'));
    for (let i = 1; i <= 90; i++) playback.sync(i / 60, true);
    expect(media.seeks).toEqual([]);
  });

  test('samples audio as the master clock without changing playback rate', async () => {
    playback.sync(0, true);
    await flush();
    vi.advanceTimersByTime(600);
    media.position = 0.4;
    playback.sync(0.6, true);
    expect(media.seeks).toEqual([]);
    expect(media.playbackRate).toBe(1);
    expect(playback.playbackTime(0.016)).toBe(0.4);
    media.position = 0.69;
    playback.sync(0.7, true);
    expect(media.playbackRate).toBe(1);
  });

  test('never seeks to catch up to a different animation clock, even with large lag', async () => {
    playback.sync(0, true);
    await flush();
    vi.advanceTimersByTime(600);
    playback.sync(2, true);
    expect(media.seeks).toEqual([]);
    media.position = 0.1;
    playback.sync(2.1, true);
    expect(media.seeks).toEqual([]);
    expect(playback.playbackTime(0.016)).toBe(0.1);
    media.finishSeek();
    media.position = 2.15;
    playback.sync(3, true);
    expect(media.seeks).toEqual([]);
  });

  test('scrubs immediately and loops only at an actual audio-time wrap', async () => {
    playback.sync(0, true);
    await flush();
    playback.seek(5);
    playback.seek(6);
    expect(media.seeks).toEqual([5, 6]);
    media.finishSeek();
    media.position = 6.01;
    playback.sync(6.01, true);
    expect(media.seeks).toEqual([5, 6]);
    playback.sync(0.01, true, false, true);
    expect(media.seeks).toEqual([5, 6, 0.01]);
  });

  test('does not restart naturally ended audio until the animation loops', async () => {
    playback.sync(0, true);
    await flush();
    media.position = 10;
    media.paused = true;
    media.ended = true;
    playback.sync(9.5, true);
    playback.sync(9.7, true);
    expect(media.play).toHaveBeenCalledTimes(1);
    expect(media.seeks).toEqual([]);
    playback.sync(0, true, false, true);
    expect(media.play).toHaveBeenCalledTimes(2);
    expect(media.seeks).toEqual([0]);
  });

  test('completes a loop restart when the browser pauses asynchronously during rewind', async () => {
    const issues = vi.fn();
    playback.onIssue = issues;
    playback.sync(0, true);
    await flush();
    media.position = 9.99;
    playback.sync(0, true, false, true);
    media.paused = true;
    media.ended = false;
    media.dispatchEvent(new Event('pause'));
    media.finishSeek();
    await flush();
    expect(issues).not.toHaveBeenCalled();
    expect(media.paused).toBe(false);
    expect(media.play).toHaveBeenCalledTimes(2);
    expect(media.seeks).toEqual([0]);
  });

  test.each([false, true])('retries a seek-aborted play once (seek completes before rejection: %s)', async (seekFirst) => {
    const issues = vi.fn();
    playback.onIssue = issues;
    playback.sync(0, true);
    await flush();
    media.position = 10;
    media.paused = true;
    let reject!: (reason: unknown) => void;
    media.play.mockImplementationOnce(() => new Promise<void>((_, fail) => { reject = fail; }));
    playback.sync(0, true, false, true);
    if (seekFirst) media.finishSeek();
    reject(new DOMException('Rewind interrupted playback', 'AbortError'));
    await flush();
    if (!seekFirst) media.finishSeek();
    await flush();
    expect(issues).not.toHaveBeenCalled();
    expect(media.paused).toBe(false);
    expect(media.play).toHaveBeenCalledTimes(3);
    for (let i = 0; i < 60; i++) playback.sync(i / 60, true);
    expect(media.play).toHaveBeenCalledTimes(3);
  });

  test.each(['pause', 'replace'])('cancels a pending rewind resume on %s', async (action) => {
    playback.sync(0, true);
    await flush();
    media.position = 9.99;
    playback.sync(0, true, false, true);
    media.paused = true;
    if (action === 'pause') playback.sync(0, false);
    else await playback.load(undefined);
    media.finishSeek();
    await flush();
    expect(media.play).toHaveBeenCalledTimes(1);
    expect(media.paused).toBe(true);
  });

  test('uses native looping only for matching cycle lengths and honors disabling it', async () => {
    playback.setLoop(true, 10);
    expect(media.loop).toBe(true);
    playback.setLoop(false, 10);
    expect(media.loop).toBe(false);
    playback.setLoop(true, 9.9);
    expect(media.loop).toBe(false);
    playback.setLoop(true, 12);
    expect(media.loop).toBe(false);
    playback.setLoop(true, 10 + 1e-8);
    expect(media.loop).toBe(true);
    await playback.load(undefined);
    expect(media.loop).toBe(false);
  });

  test('native cycle boundaries never issue a seek, pause, or new play request', async () => {
    playback.setLoop(true, 10);
    playback.sync(0, true);
    await flush();
    const pauses = media.pause.mock.calls.length;
    for (let i = 0; i < 4; i++) {
      media.position = 10;
      playback.sync(10, true);
      media.position = 0.1;
      expect(playback.playbackTime(0.016)).toBe(0.1);
      playback.sync(0.1, true, false, true);
    }
    expect(media.seeks).toEqual([]);
    expect(media.pause).toHaveBeenCalledTimes(pauses);
    expect(media.play).toHaveBeenCalledTimes(1);
  });

  test('holds silence when the animation outlasts its clip, then restarts on wrap', async () => {
    playback.sync(0, true);
    await flush();
    media.position = 9.99;
    playback.sync(10.1, true);
    expect(media.paused).toBe(true);
    playback.sync(12, true);
    expect(media.play).toHaveBeenCalledTimes(1);
    playback.sync(0, true, false, true);
    expect(media.play).toHaveBeenCalledTimes(2);
  });

  test('holds the animation clock during buffering and pending seeks, then follows actual audio', async () => {
    playback.sync(0, true);
    await flush();
    media.position = 0.5;
    expect(playback.playbackTime(0.016)).toBe(0.5);
    media.dispatchEvent(new Event('waiting'));
    for (let i = 0; i < 120; i++) expect(playback.playbackTime(0.016)).toBe(0.5);
    playback.seek(4);
    media.position = 0.5; // The decoder may still report its old position during a seek.
    expect(playback.playbackTime(0.016)).toBe(4);
    media.position = 4.1;
    media.finishSeek();
    expect(playback.playbackTime(0.016)).toBe(4.1);
    expect(media.seeks).toEqual([4]);
  });

  test('uses wall time only for the silent tail and releases the clock for failed/missing audio', async () => {
    playback.sync(0, true);
    await flush();
    media.position = 10;
    media.ended = true;
    expect(playback.playbackTime(0.1)).toBe(10);
    expect(playback.playbackTime(0.1)).toBeCloseTo(10.1);
    expect(playback.playbackTime(0.2)).toBeCloseTo(10.3);
    playback.sync(0, true, false, true);
    media.ended = false;
    media.finishSeek();
    expect(playback.playbackTime(0.1)).toBe(0);
    media.error = { code: 3, message: 'Decode failed' };
    media.dispatchEvent(new Event('error'));
    expect(playback.playbackTime(0.1)).toBeUndefined();
    await playback.load(undefined);
    expect(playback.playbackTime(0.1)).toBeUndefined();
  });

  test('scrubbing into a silent tail does not freeze the clock on the ended clip', async () => {
    playback.sync(14, true, true, true);
    expect(playback.playbackTime(0.1)).toBeCloseTo(14.1);
    playback.sync(3, true, true, true);
    expect(playback.playbackTime(0.1)).toBe(3);
    media.finishSeek();
    media.position = 3.1;
    expect(playback.playbackTime(0.1)).toBe(3.1);
  });

  test('does not infer a restart from coarse or backwards native clock readings', async () => {
    playback.sync(0, true);
    await flush();
    for (const time of [0.5, 0.8, 0.75, 0.75, 0.9, 1.2]) {
      media.position = time;
      playback.sync(playback.playbackTime(0.1)!, true);
    }
    expect(media.play).toHaveBeenCalledTimes(1);
    expect(media.seeks).toEqual([]);
    expect(media.playbackRate).toBe(1);
  });

  test('reports native interruptions instead of fighting an OS pause on every frame', async () => {
    const issues = vi.fn();
    playback.onIssue = issues;
    playback.sync(0, true);
    await flush();
    media.paused = true;
    media.dispatchEvent(new Event('pause'));
    for (let i = 0; i < 60; i++) playback.sync(i / 60, true);
    expect(media.play).toHaveBeenCalledTimes(1);
    expect(issues).toHaveBeenCalledOnce();
    expect(issues.mock.calls[0]![0]).toContain('interrupted');
    expect(playback.playbackTime(0.1)).toBeUndefined();
    playback.sync(1, true, true);
    expect(media.play).toHaveBeenCalledTimes(2);
  });

  test('does not treat zero or unknown duration during loading as the end of audio', async () => {
    media.readyState = 0;
    media.duration = 0;
    playback.sync(2, true, true);
    await flush();
    expect(media.play).toHaveBeenCalledTimes(1);
    expect(media.seeks).toEqual([]);
    media.duration = 10;
    media.readyState = 4;
    media.dispatchEvent(new Event('loadedmetadata'));
    expect(media.seeks).toEqual([2]);
  });

  test('reports blocked audio once and retries only on a fresh user action', async () => {
    const issues = vi.fn();
    playback.onIssue = issues;
    media.play.mockRejectedValueOnce(new DOMException('Gesture required', 'NotAllowedError'));
    playback.sync(0, true);
    await flush();
    for (let i = 1; i <= 60; i++) playback.sync(i / 60, true);
    expect(media.play).toHaveBeenCalledTimes(1);
    expect(issues).toHaveBeenCalledOnce();
    expect(issues.mock.calls[0]![0]).toContain('Tap Play or Unmute');
    playback.sync(1.1, true, true);
    await flush();
    expect(media.play).toHaveBeenCalledTimes(2);
  });

  test('ignores an old play rejection after pause and rapid restart', async () => {
    const issues = vi.fn();
    playback.onIssue = issues;
    let rejectOld!: (reason: unknown) => void;
    media.play.mockImplementationOnce(() => {
      media.paused = false;
      return new Promise<void>((_, reject) => { rejectOld = reject; });
    });
    playback.sync(0, true);
    playback.sync(0, false);
    playback.sync(0, true, true);
    rejectOld(new DOMException('Paused', 'AbortError'));
    await flush();
    expect(issues).not.toHaveBeenCalled();
    expect(media.paused).toBe(false);
    expect(media.play).toHaveBeenCalledTimes(2);
  });

  test('cancels old metadata waiters and ignores old play errors on track replacement', async () => {
    const issues = vi.fn();
    playback.onIssue = issues;
    let rejectOld!: (reason: unknown) => void;
    media.play.mockImplementationOnce(() => new Promise<void>((_, reject) => { rejectOld = reject; }));
    playback.sync(0, true);
    media.readyState = 0;
    const oldLoad = playback.load({ ...clip, name: 'old.wav' });
    await playback.load(undefined);
    await oldLoad;
    media.readyState = 4;
    await playback.load({ ...clip, name: 'new.wav' });
    playback.sync(0, true);
    rejectOld(new DOMException('Replaced', 'AbortError'));
    await flush();
    expect(playback.name).toBe('new.wav');
    expect(issues).not.toHaveBeenCalled();
  });

  test('surfaces decoding failures instead of retrying them every animation frame', async () => {
    const issues = vi.fn();
    playback.onIssue = issues;
    media.error = { code: 3, message: 'Decode failed' };
    media.dispatchEvent(new Event('error'));
    playback.sync(0, true, true);
    playback.sync(0.5, true);
    expect(issues).toHaveBeenCalledOnce();
    expect(issues.mock.calls[0]![0]).toContain('could not decode song.wav');
    expect(media.play).not.toHaveBeenCalled();
  });
});
