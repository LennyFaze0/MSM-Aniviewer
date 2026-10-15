import { expect, test } from '@playwright/test';

test('removed control tabs fall back to Files when remembered by an older build', async ({ page }) => {
  await page.goto('.');
  for (const removed of ['playback', 'spatial3d', 'terrain', 'rigging']) {
    await page.evaluate((tab) => localStorage.setItem('aniviewer.controlPanelTab', tab), removed);
    await page.reload();
    await expect(page.locator('.library-panel')).toHaveAttribute('data-active-control-tab', 'files');
    await expect(page.locator('#controlTab-files')).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('#controlPanel-files')).toBeVisible();
    await expect(page.locator(`#controlTab-${removed}, #controlPanel-${removed}`)).toHaveCount(0);
    expect(await page.evaluate(() => localStorage.getItem('aniviewer.controlPanelTab'))).toBe('files');
  }
});

test('settings expose only implemented controls and preserve supported video formats', async ({ page }, info) => {
  await page.goto('.');
  await page.locator('#settingsButton').click();
  const sections: Record<string, Record<string, string[]>> = {
    export: {
      png: ['export.png.scaleMultiplier', 'export.png.sequenceFps', 'export.png.framing', 'export.png.solidBackground', 'export.png.backgroundColor'],
      video: ['export.video.format', 'export.video.mode', 'export.video.bitrateMbps', 'export.video.includeAudio'],
      gif: ['export.gif.fps', 'export.gif.colors', 'export.gif.loop'],
      framing: ['export.framing.padding'],
    },
    application: {
      interface: ['interface.scale', 'interface.themeMode', ...['primary', 'secondary', 'tertiary', 'quaternary'].flatMap((color) => [`interface.${color}.enabled`, `interface.${color}.color`])],
      viewport: ['viewport.zoomToCursor', 'viewport.spriteFilter', 'viewport.particlesWorldSpace', 'viewport.particleCap'],
      conversion: ['conversion.automaticRevisionDetection', 'conversion.manualRevisionMode'],
      playback: ['playback.loop', 'playback.volume', 'playback.muted'],
      system: [],
    },
  };
  await expect(page.locator('.settings-tabs [role="tab"]')).toHaveText(['Export', 'Paths', 'Keybinds', 'Application']);
  for (const [category, entries] of Object.entries(sections)) {
    await page.locator(`[data-settings-tab="${category}"]`).click();
    expect(await page.locator('[data-settings-section]').evaluateAll((buttons) => buttons.map((node) => (node as HTMLElement).dataset.settingsSection))).toEqual(Object.keys(entries));
    for (const [section, fields] of Object.entries(entries)) {
      await page.locator(`[data-settings-section="${section}"]`).click();
      const visibleFields = await page.locator('[data-setting]').evaluateAll((controls) => controls.map((node) => (node as HTMLElement).dataset.setting).sort());
      expect(visibleFields).toEqual([...fields].sort());
      await expect(page.locator('.settings-pending, .settings-omission, .settings-availability.future, .settings-availability.partial')).toHaveCount(0);
      await expect(page.locator('#settingsLayer')).not.toContainText(/Port pending|not ported|not yet available/);
    }
  }
  await page.locator('[data-settings-tab="export"]').click();
  await page.locator('[data-settings-section="video"]').click();
  expect(await page.locator('[data-setting="export.video.format"] option').evaluateAll((options) => options.map((node) => (node as HTMLOptionElement).value))).toEqual(['auto', 'mp4', 'webm', 'mov']);
  await page.locator('[data-settings-tab="keybinds"]').click();
  await expect(page.locator('[data-keybind]:disabled')).toHaveCount(0);
  await expect(page.locator('[data-keybind="record_pose"], [data-keybind="copy_keyframes"], [data-keybind="paste_keyframes"], [data-keybind="free_transform"], [data-keybind="toggle_log_panel"]')).toHaveCount(0);
  await expect(page.locator('[data-keybind="toggle_playback"], [data-keybind="undo"], [data-keybind="move_tool"]')).toHaveCount(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath('implemented-settings.png') });
});

test('legacy preferences survive cleanup; supported playback and renderer settings still apply', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('aniviewer.viewerSettings.v1', JSON.stringify({
    viewport: { spriteFilter: 'trilinear', spatial3dEnabled: true, bloomEnabled: true },
    export: { mp4: { crf: 25 }, gif: { dither: true } },
  })));
  await page.goto('.');
  await page.locator('#settingsButton').click();
  await page.locator('[data-settings-section="viewport"]').click();
  const filter = page.locator('[data-setting="viewport.spriteFilter"]');
  await expect(filter).toHaveValue('bilinear');
  expect(await filter.locator('option').evaluateAll((options) => options.map((node) => (node as HTMLOptionElement).value))).toEqual(['nearest', 'bilinear']);
  await filter.selectOption('nearest');
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-sprite-filter', 'nearest');
  await page.locator('[data-settings-action="cancel"]').last().click();
  await expect(page.locator('#settingsLayer')).toBeHidden();
  await expect(page.locator('#viewportPanel')).toHaveAttribute('data-sprite-filter', 'trilinear');
  await page.locator('#settingsButton').click();
  await expect(filter).toHaveValue('bilinear');
  await page.locator('[data-setting="viewport.zoomToCursor"]').check();
  await page.locator('[data-settings-section="playback"]').click();
  await page.locator('[data-setting="playback.loop"]').uncheck();
  await page.locator('[data-setting="playback.volume"]').fill('0.35');
  await page.locator('[data-setting="playback.muted"]').check();
  await page.locator('[data-settings-action="save"]').click();
  await expect(page.locator('#settingsLayer')).toBeHidden();
  await expect(page.locator('#loopButton')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#audioVolume')).toHaveValue('35');
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('aniviewer.viewerSettings.v1')!));
  expect(saved).toMatchObject({
    viewport: { spriteFilter: 'trilinear', spatial3dEnabled: true, bloomEnabled: true, zoomToCursor: true },
    export: { mp4: { crf: 25 }, gif: { dither: true } },
    playback: { loop: false, volume: 0.35, muted: true },
  });
});
