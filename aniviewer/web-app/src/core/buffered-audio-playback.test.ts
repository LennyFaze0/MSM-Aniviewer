import { describe, expect, test, vi } from 'vitest';
import { BufferedAudioPlayback } from './buffered-audio-playback';

class Context extends EventTarget {
  currentTime = 0;
  state = 'running';
  destination = {};
  sources: Array<ReturnType<Context['createBufferSource']>> = [];
  gain = { gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() };
  resume = vi.fn(async () => { this.state = 'running'; this.dispatchEvent(new Event('statechange')); });
  createGain() { return this.gain; }
  createBufferSource() {
    const node = { buffer: undefined as AudioBuffer | undefined, loop: false, onended: null as (() => void) | null,
      connect: vi.fn(), disconnect: vi.fn(), start: vi.fn(), stop: vi.fn() };
    this.sources.push(node); return node;
  }
  interrupt() { this.state = 'interrupted'; this.dispatchEvent(new Event('statechange')); }
}
function fixture(ready = true) {
  const context = new Context(), player = new BufferedAudioPlayback(context as unknown as AudioContext, 'test.wav');
  const buffer = { duration: 4, length: 192000, numberOfChannels: 1 } as AudioBuffer;
  if (ready) player.setBuffer(buffer);
  return { context, player, buffer };
}

describe('buffered audio clock and transport', () => {
  test('plays the complete buffer once without media seeks or rate correction', () => {
    const { context, player } = fixture();
    player.sync(0, true, true);
    for (let i = 1; i <= 250; i++) {
      context.currentTime = i / 60;
      player.sync(i / 60, true);
      expect(player.playbackTime(.5)).toBeCloseTo(i / 60);
    }
    expect(context.sources).toHaveLength(1);
    expect(context.sources[0]!.start).toHaveBeenCalledExactlyOnceWith(0, 0);
    expect(context.sources[0]!.stop).not.toHaveBeenCalled();
  });
  test('native loops do not create new sources or request fresh permission', () => {
    const { context, player } = fixture();
    player.setLoop(true, 4); player.sync(0, true, true);
    for (const time of [4.1, 8.2, 16.3, 24.1]) {
      context.currentTime = time;
      player.sync(time % 4, true, false, true);
      expect(player.playbackTime(0)).toBeCloseTo(time % 4);
    }
    expect(context.sources).toHaveLength(1); expect(context.sources[0]!.loop).toBe(true);
    expect(context.resume).not.toHaveBeenCalled();
  });
  test('loop off finishes the current cycle, including after skipped render callbacks', () => {
    const { context, player } = fixture();
    player.setLoop(true, 4); player.sync(0, true);
    context.currentTime = 19; player.setLoop(false, 4);
    expect(player.playbackTime(0)).toBe(3); expect(context.sources[0]!.loop).toBe(false);
    context.currentTime = 20.1; expect(player.playbackTime(0)).toBeCloseTo(4.1);
  });
  test('scrubbing a natively looping source is explicit and replaces only that source', () => {
    const { context, player } = fixture();
    player.setLoop(true, 4); player.sync(0, true);
    context.currentTime = .5; player.sync(2, true, true, true);
    expect(context.sources[0]!.stop).toHaveBeenCalledOnce();
    expect(context.sources[1]!.start).toHaveBeenCalledExactlyOnceWith(0, 2);
    context.currentTime = .7; expect(player.playbackTime(0)).toBeCloseTo(2.2);
  });
  test('pause/resume, mute and volume do not consume time or leak previous sources', () => {
    const { context, player } = fixture();
    player.sync(0, true); context.currentTime = 1; player.sync(1, false);
    context.currentTime = 10; expect(player.playbackTime(0)).toBeUndefined();
    player.setVolume(.4); player.setMuted(true); expect(context.gain.gain.value).toBe(0);
    player.setMuted(false); expect(context.gain.gain.value).toBe(.4);
    player.sync(1, true, true); expect(context.sources[1]!.start).toHaveBeenCalledWith(0, 1);
    context.currentTime = 10.5; expect(player.playbackTime(0)).toBe(1.5);
    player.sync(1.4, true, true); expect(context.sources).toHaveLength(2); // Unmute isn't a scrub.
  });
  test('holds the clock through an iOS interruption and resumes the same source', async () => {
    const { context, player } = fixture(); const warning = vi.fn(), recovered = vi.fn();
    player.onIssue = warning; player.onRecovered = recovered;
    player.sync(0, true); context.currentTime = 1.5; context.interrupt();
    for (let i = 0; i < 120; i++) { player.sync(1.5, true); expect(player.playbackTime(1)).toBe(1.5); }
    expect(warning).toHaveBeenCalledOnce(); expect(context.resume).not.toHaveBeenCalled();
    player.sync(1.5, true, true); await Promise.resolve();
    expect(context.resume).toHaveBeenCalledOnce(); expect(context.sources).toHaveLength(1);
    context.currentTime = 2; expect(player.playbackTime(0)).toBe(2); expect(recovered).toHaveBeenCalled();
  });
  test('an early Play tap unlocks the context without skipping audio during decode', async () => {
    const { context, player, buffer } = fixture(false); context.state = 'suspended';
    player.sync(0, true, true); expect(context.resume).toHaveBeenCalledOnce();
    context.currentTime = 3; expect(player.playbackTime(1)).toBe(0);
    player.setBuffer(buffer); expect(context.sources[0]!.start).toHaveBeenCalledWith(0, 0);
    context.currentTime = 3.5; expect(player.playbackTime(1)).toBe(.5);
  });
  test('pause while decoding prevents delayed audio from starting', () => {
    const { context, player, buffer } = fixture(false);
    player.sync(0, true); player.sync(0, false); player.setBuffer(buffer);
    expect(context.sources).toHaveLength(0); expect(player.playbackTime(1)).toBeUndefined();
  });
  test('fresh gestures can retry a pending permission request, never frame callbacks', async () => {
    const { context, player } = fixture(); context.state = 'suspended';
    context.resume.mockImplementationOnce(() => new Promise(() => {}));
    player.sync(0, true); for (let i = 0; i < 100; i++) player.sync(0, true);
    expect(context.resume).toHaveBeenCalledOnce();
    player.sync(0, true, true); await Promise.resolve(); expect(context.state).toBe('running');
    expect(context.resume).toHaveBeenCalledTimes(2); expect(context.sources).toHaveLength(1);
  });
  test('short clips preserve silent tails and mismatched cycles restart without resume calls', () => {
    const { context, player } = fixture(); player.setLoop(true, 6); player.sync(0, true);
    expect(player.nativeLoopEnabled).toBe(false);
    context.currentTime = 5; context.sources[0]!.onended?.();
    expect(player.playbackTime(.2)).toBe(5);
    context.currentTime = 6.1; player.sync(.1, true, false, true);
    expect(context.sources[1]!.start).toHaveBeenCalledWith(0, .1);
    expect(context.resume).not.toHaveBeenCalled();
  });
  test('destroy detaches listeners and prevents late decode or resume from playing', () => {
    const { context, player, buffer } = fixture(); const warning = vi.fn(); player.onIssue = warning;
    player.sync(0, true); player.destroy(); player.setBuffer(buffer); player.sync(0, true);
    context.interrupt(); expect(warning).not.toHaveBeenCalled();
    expect(context.sources).toHaveLength(1); expect(context.sources[0]!.stop).toHaveBeenCalledOnce();
    expect(context.gain.disconnect).toHaveBeenCalledOnce();
  });
});
