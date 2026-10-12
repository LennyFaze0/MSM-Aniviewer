import { expect, type Page } from '@playwright/test';
import { nativeRev6Fixture } from '../native-fixture';

export function toneWav(duration: number): Buffer {
  const rate = 8_000;
  const samples = Math.round(duration * rate);
  const wav = Buffer.alloc(44 + samples * 2);
  wav.write('RIFF', 0);
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24);
  wav.writeUInt32LE(rate * 2, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write('data', 36);
  wav.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) wav.writeInt16LE(Math.round(Math.sin(i * 440 * Math.PI * 2 / rate) * 1000), 44 + i * 2);
  return wav;
}

export interface AudioFixtureOptions {
  buffered?: boolean;
  waitForAudio?: boolean;
  blockFirstPlay?: boolean;
  irregularClock?: boolean;
  animationDuration?: number;
  audioDuration?: number;
  standard?: boolean;
  pauseDuringSeek?: boolean;
  rejectReplay?: boolean;
}

export async function loadAudioFixture(page: Page, options: AudioFixtureOptions = {}): Promise<void> {
  const { animationDuration = 2, audioDuration = 4.1, standard = false } = options;
  await page.addInitScript(({ buffered, blockFirstPlay, irregularClock, pauseDuringSeek, rejectReplay }) => {
    // Explicitly exercise the media fallback; buffered transport has its own suite.
    if (!buffered) Object.defineProperty(window, 'AudioContext', { configurable: true, value: undefined });
    const state = { element: undefined as HTMLAudioElement | undefined, calls: 0, seeks: 0, errors: 0,
      seekWrites: 0, rateWrites: 0, wraps: 0, lastTime: 0, injectedPauses: 0 };
    Object.assign(window, { audioTestState: state });
    window.Audio = new Proxy(window.Audio, {
      construct(target, args) {
        const audio = Reflect.construct(target, args) as HTMLAudioElement;
        state.element = audio;
        const time = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')!;
        const rate = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'playbackRate')!;
        Object.defineProperty(audio, 'currentTime', {
          get() {
            const actual = time.get!.call(audio) as number;
            if (actual < state.lastTime - 0.1) state.wraps++;
            state.lastTime = actual;
            if (!irregularClock) return actual;
            // Exercise coarse timing and a stale decoder clock without assuming
            // Chromium's smooth native timing reproduces an iPhone's behaviour.
            if (actual >= 0.5 && actual < 1.5) return 0.5;
            return Math.floor(actual * 4) / 4;
          },
          set(value: number) { state.seekWrites++; time.set!.call(audio, value); },
        });
        Object.defineProperty(audio, 'playbackRate', {
          get() { return rate.get!.call(audio); },
          set(value: number) { state.rateWrites++; rate.set!.call(audio, value); },
        });
        audio.addEventListener('seeking', () => {
          state.seeks++;
          if (pauseDuringSeek && !audio.paused) {
            state.injectedPauses++;
            audio.pause();
          }
        });
        audio.addEventListener('error', () => state.errors++);
        const play = audio.play.bind(audio);
        audio.play = () => {
          state.calls++;
          if (blockFirstPlay && state.calls === 1) return Promise.reject(new DOMException('Tap needed', 'NotAllowedError'));
          if (rejectReplay && state.calls > 1) return Promise.reject(new DOMException('A new tap is required', 'NotAllowedError'));
          return play();
        };
        return audio;
      },
    });
  }, options);
  await page.goto('.');
  const document = {
    rev: 6, source_format: standard ? 'rev6' : 'muppets', source_revision: standard ? 6 : 2,
    sources: [{ src: 'dot.png', id: 0 }],
    anims: [{ name: '01-kermit_01', width: 64, height: 64, layers: [{
      name: 'dot', type: 1, parent: -1, id: 0, src: 0, anchor_x: 0.5, anchor_y: 0.5,
      frames: [{ time: 0, pos: { immediate: 0, x: 32, y: 32 },
        scale: { immediate: 0, x: 3200, y: 3200 }, opacity: { immediate: 0, value: 100 },
        sprite: { immediate: -1, string: '' } }, { time: animationDuration }],
    }] }],
  };
  await page.locator('#fileInput').setInputFiles([
    { name: 'audio_fixture.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(document)) },
    { name: 'dot.png', mimeType: 'image/png', buffer: nativeRev6Fixture().png },
    { name: '01-kermit_01.wav', mimeType: 'audio/wav', buffer: toneWav(audioDuration) },
  ]);
  if (options.waitForAudio === false) return;
  await expect(page.locator('#audioStatus')).toHaveText('01-kermit_01.wav');
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-audio-loop-multiplier', standard ? '1' : '2');
}

export async function audioState(page: Page) {
  return page.evaluate(() => {
    const state = (window as unknown as { audioTestState: {
      element: HTMLAudioElement; calls: number; seeks: number; errors: number;
      seekWrites: number; rateWrites: number;
      wraps: number; injectedPauses: number;
    } }).audioTestState;
    return { time: state.element.currentTime, paused: state.element.paused,
      calls: state.calls, seeks: state.seeks, errors: state.errors,
      seekWrites: state.seekWrites, rateWrites: state.rateWrites,
      wraps: state.wraps, injectedPauses: state.injectedPauses, nativeLoop: state.element.loop };
  });
}
