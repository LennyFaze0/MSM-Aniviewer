import { test, expect, type Page } from '@playwright/test';
import { loadAudioFixture } from './helpers/audio-fixture';

async function probe(page: Page, delayDecode = false) {
  await page.addInitScript(({ delayDecode }) => {
    const state = { context: undefined as AudioContext | undefined, analyser: undefined as AnalyserNode | undefined,
      starts: 0, stops: 0, resumeCalls: 0, at: 0, offset: 0, duration: 0, loop: false,
      source: undefined as AudioBufferSourceNode | undefined, releaseDecode: undefined as (() => void) | undefined };
    Object.assign(window, { bufferProbe: state });
    window.AudioContext = new Proxy(window.AudioContext, {
      construct(target, args) {
        const context = Reflect.construct(target, args) as AudioContext; state.context = context;
        const decode = context.decodeAudioData.bind(context);
        context.decodeAudioData = async (bytes: ArrayBuffer) => {
          if (delayDecode) await new Promise<void>((resolve) => { state.releaseDecode = resolve; });
          return decode(bytes);
        };
        const resume = context.resume.bind(context);
        context.resume = () => { state.resumeCalls++; return resume(); };
        const gain = context.createGain.bind(context);
        context.createGain = () => {
          const node = gain(), analyser = context.createAnalyser(); state.analyser = analyser;
          analyser.connect(context.destination); node.connect(analyser);
          // Observe actual samples after volume/mute, without a second audible path.
          node.connect = (() => analyser) as typeof node.connect;
          return node;
        };
        const create = context.createBufferSource.bind(context);
        context.createBufferSource = () => {
          const source = create(), start = source.start.bind(source), stop = source.stop.bind(source);
          state.source = source;
          source.start = (when = 0, offset = 0, duration?: number) => {
            state.starts++; state.at = context.currentTime; state.offset = offset;
            state.duration = source.buffer!.duration; state.loop = source.loop;
            if (duration === undefined) start(when, offset); else start(when, offset, duration);
          };
          source.stop = (when = 0) => { state.stops++; stop(when); };
          return source;
        };
        return context;
      },
    });
  }, { delayDecode });
}
async function state(page: Page) {
  return page.evaluate(() => {
    const s = (window as any).bufferProbe;
    const samples = new Float32Array(s.analyser?.fftSize ?? 32);
    s.analyser?.getFloatTimeDomainData(samples);
    return { starts: s.starts, stops: s.stops, elapsed: s.context.currentTime - s.at, offset: s.offset,
      duration: s.duration, loop: s.source?.loop, contextState: s.context.state,
      peak: Math.max(...samples.map(Math.abs)), resumeCalls: s.resumeCalls,
      timeline: Number((document.querySelector('#timeline') as HTMLInputElement).value) };
  });
}

test('plays audible samples through the full synchronized track and repeats without media play or seek', async ({ page }) => {
  await probe(page); await loadAudioFixture(page, { buffered: true });
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-audio-backend', 'buffer');
  await page.locator('#playButton').click();
  await expect.poll(async () => (await state(page)).elapsed, { timeout: 8000 }).toBeGreaterThan(3.5);
  await expect.poll(async () => (await state(page)).peak).toBeGreaterThan(.01);
  expect((await state(page)).starts).toBe(1); expect((await state(page)).loop).toBe(true);
  await expect.poll(async () => (await state(page)).elapsed, { timeout: 8000 }).toBeGreaterThan(9);
  await expect.poll(async () => (await state(page)).peak).toBeGreaterThan(.01);
  const s = await state(page), audio = (s.elapsed + s.offset) % s.duration;
  expect(Math.abs(s.timeline - (audio * (4 / 4.1)) % 2)).toBeLessThan(.12);
  expect(s.starts).toBe(1); expect(s.stops).toBe(0);
  await page.locator('#loopButton').click();
  await expect(page.locator('#playButton')).toHaveAttribute('aria-label', 'Play', { timeout: 6000 });
});

test('pause, scrub, resume and mute operate once without truncating subsequent playback', async ({ page }) => {
  await probe(page); await loadAudioFixture(page, { buffered: true });
  await page.locator('#playButton').click();
  await expect.poll(async () => (await state(page)).elapsed).toBeGreaterThan(.3);
  await page.locator('#playButton').click();
  await page.locator('#timeline').evaluate((input: HTMLInputElement) => { input.value = '1'; input.dispatchEvent(new Event('input', { bubbles: true })); });
  await page.locator('#playButton').click();
  expect((await state(page)).offset).toBeCloseTo(1.025, 3);
  await page.locator('#audioButton').click(); await expect.poll(async () => (await state(page)).peak).toBe(0);
  await page.locator('#audioButton').click(); await expect.poll(async () => (await state(page)).peak).toBeGreaterThan(.01);
  expect((await state(page)).starts).toBe(2);
  await page.locator('#timeline').evaluate((input: HTMLInputElement) => { input.value = '.5'; input.dispatchEvent(new Event('input', { bubbles: true })); });
  expect((await state(page)).offset).toBeCloseTo(.5125, 3);
  expect((await state(page)).starts).toBe(3);
});

test('context suspension holds the animation; Unmute resumes the existing source in sync', async ({ page }) => {
  await probe(page); await loadAudioFixture(page, { buffered: true });
  await page.locator('#playButton').click(); await expect.poll(async () => (await state(page)).elapsed).toBeGreaterThan(.4);
  await page.evaluate(() => (window as any).bufferProbe.context.suspend());
  await expect(page.locator('#audioStatus')).toHaveAttribute('title', /context suspended.*held in sync/);
  await page.waitForTimeout(100); const held = await state(page);
  await page.waitForTimeout(400); expect((await state(page)).timeline).toBe(held.timeline);
  await page.locator('#audioButton').click(); await page.locator('#audioButton').click();
  await expect.poll(async () => (await state(page)).elapsed).toBeGreaterThan(held.elapsed + .2);
  expect((await state(page)).starts).toBe(1);
  await expect(page.locator('#audioStatus')).toHaveAttribute('title', '');
});

test('Play during decoding starts the entire track instead of losing its beginning', async ({ page }) => {
  await probe(page, true);
  await loadAudioFixture(page, { buffered: true, waitForAudio: false });
  await expect.poll(() => page.evaluate(() => Boolean((window as any).bufferProbe?.releaseDecode))).toBe(true);
  await page.locator('#playButton').click();
  await page.waitForTimeout(450); expect(Number(await page.locator('#timeline').inputValue())).toBe(0);
  await page.evaluate(() => (window as any).bufferProbe.releaseDecode());
  await expect(page.locator('#audioStatus')).toHaveText('01-kermit_01.wav');
  await expect.poll(async () => (await state(page)).peak).toBeGreaterThan(.01);
  expect((await state(page)).offset).toBe(0);
});

test('short clips keep a silent tail and restart across multiple longer animation cycles', async ({ page }) => {
  await probe(page); await loadAudioFixture(page, { buffered: true, standard: true, animationDuration: 1, audioDuration: .4 });
  await page.locator('#playButton').click();
  await expect.poll(async () => (await state(page)).starts, { timeout: 6000 }).toBeGreaterThanOrEqual(4);
  expect((await state(page)).resumeCalls).toBeLessThanOrEqual(1);
  await expect(page.locator('#audioStatus')).toHaveAttribute('title', '');
});
