import { expect, test } from '@playwright/test';
import { loadAudioFixture, audioState } from './helpers/audio-fixture';

test('keeps real audio advancing across animation cycles and supports pause, scrub and resume', async ({ page }) => {
  await loadAudioFixture(page);
  await page.locator('#playButton').click();
  await expect.poll(async () => (await audioState(page)).time).toBeGreaterThan(2.3);
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-audio-loop-index', '1');
  expect((await audioState(page)).errors).toBe(0);
  expect((await audioState(page)).seeks).toBeLessThanOrEqual(1);
  expect((await audioState(page)).rateWrites).toBe(0);
  await page.locator('#playButton').click();
  await expect.poll(async () => (await audioState(page)).paused).toBe(true);
  await page.locator('#timeline').evaluate((input: HTMLInputElement) => {
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await expect.poll(async () => Math.abs((await audioState(page)).time - 1.025)).toBeLessThan(0.1);
  await page.locator('#playButton').click();
  await expect.poll(async () => (await audioState(page)).time).toBeGreaterThan(1.4);
  await page.locator('#playButton').click();
});

test('does not interrupt audio to correct stale or coarse playback timestamps', async ({ page }) => {
  await loadAudioFixture(page, { irregularClock: true });
  await page.locator('#playButton').click();
  await expect.poll(async () => (await audioState(page)).time).toBeGreaterThan(3);
  const state = await audioState(page);
  expect(state.errors).toBe(0);
  expect(state.paused).toBe(false);
  expect(state.calls).toBe(1);
  expect(state.seekWrites).toBe(0);
  expect(state.rateWrites).toBe(0);
  await page.locator('#playButton').click();
});

test('natively repeats full audio cycles without another play request, and honors Loop Off', async ({ page }) => {
  await loadAudioFixture(page, { animationDuration: 0.6, audioDuration: 1.2, rejectReplay: true });
  expect((await audioState(page)).nativeLoop).toBe(true);
  await page.locator('#playButton').click();
  await expect.poll(async () => (await audioState(page)).wraps, { timeout: 8_000 }).toBeGreaterThanOrEqual(3);
  expect((await audioState(page)).calls).toBe(1);
  expect((await audioState(page)).seekWrites).toBe(0);
  expect((await audioState(page)).rateWrites).toBe(0);
  await expect(page.locator('#audioStatus')).toHaveAttribute('title', '');
  await page.locator('#loopButton').click();
  expect((await audioState(page)).nativeLoop).toBe(false);
  await expect(page.locator('#playButton')).toHaveAttribute('aria-label', 'Play');
  expect((await audioState(page)).paused).toBe(true);
});

test('keeps non-matching cycles looping even when the browser pauses during each rewind', async ({ page }) => {
  await loadAudioFixture(page, { standard: true, animationDuration: 0.6, audioDuration: 1.3, pauseDuringSeek: true });
  expect((await audioState(page)).nativeLoop).toBe(false);
  await page.locator('#playButton').click();
  await expect.poll(async () => (await audioState(page)).injectedPauses, { timeout: 8_000 }).toBeGreaterThanOrEqual(3);
  await expect.poll(async () => (await audioState(page)).paused).toBe(false);
  // WebKit may report unpaused before the queued seek-resume play call is observed.
  await expect.poll(async () => (await audioState(page)).calls, { timeout: 8_000 }).toBeGreaterThanOrEqual(4);
  expect((await audioState(page)).rateWrites).toBe(0);
  await expect(page.locator('#audioStatus')).toHaveAttribute('title', '');
  await page.locator('#playButton').click();
});

test('preserves a shorter clip\'s silent tail over repeated animation loops', async ({ page }) => {
  await loadAudioFixture(page, { standard: true, animationDuration: 1, audioDuration: 0.4 });
  expect((await audioState(page)).nativeLoop).toBe(false);
  await page.locator('#playButton').click();
  await expect.poll(async () => (await audioState(page)).calls, { timeout: 8_000 }).toBeGreaterThanOrEqual(4);
  expect((await audioState(page)).errors).toBe(0);
  expect((await audioState(page)).rateWrites).toBe(0);
  await expect(page.locator('#audioStatus')).toHaveAttribute('title', '');
  await page.locator('#playButton').click();
});

test('shows a blocked-playback explanation without frame retries and recovers on Play', async ({ page }) => {
  await loadAudioFixture(page, { blockFirstPlay: true });
  await page.locator('#playButton').click();
  await expect(page.locator('#audioStatus')).toHaveAttribute('title', /Audio was blocked.*Tap Play/);
  await expect.poll(async () => Number(await page.locator('#timeline').inputValue())).toBeGreaterThan(0.3);
  expect((await audioState(page)).calls).toBe(1);
  await page.locator('#playButton').click();
  await page.locator('#playButton').click();
  await expect.poll(async () => (await audioState(page)).time).toBeGreaterThan(0.5);
  expect((await audioState(page)).calls).toBe(2);
  await expect(page.locator('#audioStatus')).toHaveAttribute('title', '');
  await page.locator('#playButton').click();
});
