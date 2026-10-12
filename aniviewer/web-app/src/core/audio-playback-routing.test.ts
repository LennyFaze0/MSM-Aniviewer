import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { AudioPlayback } from './audio-playback';

const metadata = vi.hoisted(() => ({ duration: 4, channels: 1, dispose: vi.fn() }));
vi.mock('mediabunny', () => ({
  ALL_FORMATS: [], BlobSource: class {},
  Input: class {
    getPrimaryAudioTrack = async () => ({ computeDuration: async () => metadata.duration, getNumberOfChannels: async () => metadata.channels });
    dispose = metadata.dispose;
  },
}));
class Context extends EventTarget {
  state = 'suspended'; currentTime = 0; sampleRate = 48000; destination = {};
  gain = { gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() };
  createGain = () => this.gain;
  createBufferSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn(), start: vi.fn(), stop: vi.fn(), loop: false }));
  decodeAudioData = vi.fn(async (_bytes: ArrayBuffer) => ({ duration: 4, length: 192000, numberOfChannels: 1 } as AudioBuffer));
  resume = vi.fn(async () => { this.state = 'running'; });
  close = vi.fn(async () => { this.state = 'closed'; });
}
class Audio extends EventTarget {
  readyState = 4; duration = 4; currentTime = 0; paused = true; ended = false; loop = false;
  play = vi.fn(async () => { this.paused = false; });
  pause() { this.paused = true; }
  load() {}
  removeAttribute() {}
}
let context: Context, player: AudioPlayback;
const clip = (name = 'a.wav') => ({ name, path: name, blob: new Blob(['test']) });
beforeEach(() => {
  metadata.duration = 4; metadata.channels = 1; metadata.dispose.mockClear();
  context = new Context();
  vi.stubGlobal('AudioContext', function () { return context; });
  vi.stubGlobal('Audio', Audio); vi.stubGlobal('HTMLMediaElement', { HAVE_METADATA: 1 });
  player = new AudioPlayback();
});
afterEach(() => { player.destroy(); vi.unstubAllGlobals(); });

test('routes bounded decoded clips to Web Audio and keeps their original export blob', async () => {
  const input = clip(); player.setVolume(.35); player.setMuted(true);
  await player.load(input);
  expect(player.backend).toBe('buffer'); expect(player.duration).toBe(4); expect(player.sourceBlob).toBe(input.blob);
  expect(context.gain.gain.value).toBe(0); player.setMuted(false); expect(context.gain.gain.value).toBe(.35);
  expect(metadata.dispose).toHaveBeenCalledOnce();
});
test.each(['encoded', 'pcm', 'channels', 'unknown-duration'])('preflights %s before decoding, preserving the native fallback', async (reason) => {
  const input = clip();
  if (reason === 'encoded') Object.defineProperty(input.blob, 'size', { value: 8 * 1024 * 1024 + 1 });
  if (reason === 'pcm') metadata.duration = 300;
  if (reason === 'channels') metadata.channels = 8;
  if (reason === 'unknown-duration') metadata.duration = NaN;
  await player.load(input); expect(player.backend).toBe('media'); expect(context.decodeAudioData).not.toHaveBeenCalled();
});
test('unsupported decoding and mismatched decoded size fall back without retaining PCM', async () => {
  context.decodeAudioData.mockRejectedValueOnce(new DOMException('Unsupported', 'EncodingError'));
  await player.load(clip()); expect(player.backend).toBe('media');
  context.decodeAudioData.mockResolvedValueOnce({ duration: 400, length: 48000 * 400, numberOfChannels: 2 } as AudioBuffer);
  await player.load(clip()); expect(player.backend).toBe('media');
});
test('missing Web Audio or context initialization failure keeps media playback', async () => {
  vi.stubGlobal('AudioContext', undefined); await player.load(clip()); expect(player.backend).toBe('media');
  vi.stubGlobal('AudioContext', function () { throw new Error('Device unavailable'); });
  await player.load(clip()); expect(player.backend).toBe('media');
});
test('a Play tap before asset resolution unlocks the same context used by the later clip', async () => {
  player.sync(0, true, true); expect(context.resume).toHaveBeenCalledOnce();
  await player.load(clip()); player.sync(0, true);
  expect(context.resume).toHaveBeenCalledOnce(); expect(context.createBufferSource).toHaveBeenCalledOnce();
});
test('serializes decoding and discards stale results after a rapid track change', async () => {
  let finish!: (buffer: AudioBuffer) => void;
  context.decodeAudioData.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const old = player.load(clip('old.wav'));
  await vi.waitFor(() => expect(context.decodeAudioData).toHaveBeenCalledOnce());
  player.sync(0, true, true);
  const latest = player.load(clip('new.wav'));
  expect(context.decodeAudioData).toHaveBeenCalledOnce();
  finish({ duration: 4, length: 192000, numberOfChannels: 1 } as AudioBuffer);
  await old; await latest;
  expect(player.name).toBe('new.wav'); expect(player.backend).toBe('buffer');
  expect(context.decodeAudioData).toHaveBeenCalledTimes(2);
  expect(context.createBufferSource).not.toHaveBeenCalled();
  player.sync(0, true, true); expect(context.createBufferSource).toHaveBeenCalledOnce();
});
test('unloading during decode cannot install a stale clip or start sound', async () => {
  let finish!: (buffer: AudioBuffer) => void;
  context.decodeAudioData.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const loading = player.load(clip());
  await vi.waitFor(() => expect(context.decodeAudioData).toHaveBeenCalledOnce());
  await player.load(undefined); finish({ duration: 4, length: 192000, numberOfChannels: 1 } as AudioBuffer); await loading;
  expect(player.backend).toBe('none'); expect(player.sourceBlob).toBeUndefined();
  expect(context.createBufferSource).not.toHaveBeenCalled();
});
