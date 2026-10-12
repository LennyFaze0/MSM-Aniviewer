import { describe, expect, it } from 'vitest';
import {
  DEFAULT_VIEWER_SETTINGS,
  keyboardEventMatchesShortcut,
  keyboardEventToShortcut,
  loadViewerSettings,
  normalizeViewerSettings,
  saveViewerSettings,
  type SettingsStorage,
} from './viewer-settings';

class MemoryStorage implements SettingsStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

describe('viewer settings', () => {
  it('loads defaults when stored JSON is invalid', () => {
    const storage = new MemoryStorage();
    storage.setItem('aniviewer.viewerSettings.v1', '{broken');
    expect(loadViewerSettings(storage)).toEqual(DEFAULT_VIEWER_SETTINGS);
  });

  it('normalizes user-controlled numeric and color values', () => {
    const settings = normalizeViewerSettings({
      interface: {
        scale: 99,
        primary: { enabled: true, color: 'not-a-color' },
      },
      viewport: { particleCap: -50 },
      export: {
        video: { format: 'invalid', bitrateMbps: 999 },
        png: { sequenceFps: 900, backgroundColor: '#ABCDEF' },
        framing: { width: 1, height: 99999, padding: -2 },
      },
    });
    expect(settings.interface.scale).toBe(2);
    expect(settings.interface.primary.color).toBe('#303439');
    expect(settings.viewport.particleCap).toBe(0);
    expect(settings.export.png.sequenceFps).toBe(120);
    expect(settings.export.video).toMatchObject({ format: 'auto', bitrateMbps: 100, includeAudio: true });
    expect(settings.export.png.backgroundColor).toBe('#abcdef');
    expect(settings.export.framing).toMatchObject({ width: 16, height: 16384, padding: 0 });
  });

  it('round-trips persisted browser preferences', () => {
    const storage = new MemoryStorage();
    const settings = normalizeViewerSettings(DEFAULT_VIEWER_SETTINGS);
    settings.interface.themeMode = 'light';
    settings.viewport.spriteFilter = 'nearest';
    settings.keybinds.toggle_loop = 'L';
    saveViewerSettings(settings, storage);
    expect(loadViewerSettings(storage)).toMatchObject({
      interface: { themeMode: 'light' },
      viewport: { spriteFilter: 'nearest' },
      keybinds: { toggle_loop: 'L' },
    });
  });
});

describe('viewer keybind normalization', () => {
  it('formats modifiers in a stable desktop-compatible order', () => {
    expect(keyboardEventToShortcut({
      key: 'z', ctrlKey: true, altKey: false, shiftKey: true, metaKey: false,
    })).toBe('Ctrl+Shift+Z');
    expect(keyboardEventToShortcut({
      key: ' ', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false,
    })).toBe('Space');
  });

  it('matches shortcuts without case sensitivity', () => {
    expect(keyboardEventMatchesShortcut({
      key: 'l', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false,
    }, 'L')).toBe(true);
    expect(keyboardEventMatchesShortcut({
      key: 'l', ctrlKey: true, altKey: false, shiftKey: false, metaKey: false,
    }, 'L')).toBe(false);
  });
});
