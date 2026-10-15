import {
  cloneViewerSettings,
  DEFAULT_KEYBINDS,
  DEFAULT_VIEWER_SETTINGS,
  keyboardEventToShortcut,
  normalizeViewerSettings,
  type ViewerSettings,
} from './viewer-settings';

type SettingsTab = 'export' | 'paths' | 'keybinds' | 'application';

export interface SettingsPaneOptions {
  trigger: HTMLButtonElement;
  settings: ViewerSettings;
  onPreview(settings: ViewerSettings): void;
  onSave(settings: ViewerSettings): void;
  onOpenFiles(): void;
  onOpenFolder(): void;
  onManageAssets(): void;
}

interface KeybindDefinition {
  key: string;
  label: string;
  group: string;
  description: string;
}

const KEYBINDS: readonly KeybindDefinition[] = [
  { key: 'toggle_playback', label: 'Play / Pause', group: 'Playback', description: 'Toggle animation playback.' },
  { key: 'toggle_loop', label: 'Toggle Loop', group: 'Playback', description: 'Toggle looping on the current animation.' },
  { key: 'undo', label: 'Undo', group: 'Edit', description: 'Undo the most recent transform action.' },
  { key: 'redo', label: 'Redo', group: 'Edit', description: 'Redo the most recently undone transform.' },
  { key: 'redo_alt', label: 'Redo (Alt)', group: 'Edit', description: 'Alternative redo shortcut.' },
  { key: 'move_tool', label: 'Move Tool', group: 'Tools', description: 'Activate sprite dragging.' },
  { key: 'select_tool', label: 'Select Tool', group: 'Tools', description: 'Select without moving sprites.' },
  { key: 'pan_tool', label: 'Pan Tool', group: 'Tools', description: 'Drag the camera without editing sprites.' },
  { key: 'toggle_rotation_gizmo', label: 'Rotation Tool', group: 'Tools', description: 'Activate rotation controls.' },
  { key: 'toggle_scale_gizmo', label: 'Scale Tool', group: 'Tools', description: 'Activate scaling controls.' },
  { key: 'fullscreen', label: 'Fullscreen', group: 'View', description: 'Toggle browser fullscreen mode.' },
  { key: 'toggle_controls_panel', label: 'Toggle Controls Panel', group: 'Panels', description: 'Show or hide the controls panel.' },
  { key: 'toggle_layers_panel', label: 'Toggle Layer Panel', group: 'Panels', description: 'Show or hide the layer list.' },
  { key: 'toggle_timeline_panel', label: 'Toggle Timeline', group: 'Panels', description: 'Show or hide the timeline panel.' },
  { key: 'toggle_focus_mode', label: 'Toggle Focus Mode', group: 'Panels', description: 'Hide or restore both side panels.' },
];

const escapeHtml = (value: unknown): string => String(value)
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#039;');

function valueAt(settings: ViewerSettings, path: string): unknown {
  return path.split('.').reduce<unknown>((value, key) => (
    value && typeof value === 'object' ? (value as Record<string, unknown>)[key] : undefined
  ), settings);
}

function setValueAt(settings: ViewerSettings, path: string, value: unknown): void {
  const keys = path.split('.');
  let target = settings as unknown as Record<string, unknown>;
  keys.slice(0, -1).forEach((key) => {
    target = target[key] as Record<string, unknown>;
  });
  target[keys.at(-1)!] = value;
}

function options(values: readonly (readonly [string, string | number])[], selected: unknown): string {
  return values.map(([label, value]) => (
    `<option value="${escapeHtml(value)}" ${String(selected) === String(value) ? 'selected' : ''}>${escapeHtml(label)}</option>`
  )).join('');
}

function availability(): string {
  return '<span class="settings-availability active">Web active</span>';
}

function field(label: string, control: string, hint = ''): string {
  return `
    <label class="settings-field">
      <span class="settings-field-copy"><strong>${escapeHtml(label)}</strong>${hint ? `<small>${escapeHtml(hint)}</small>` : ''}</span>
      <span class="settings-field-control">${control}${availability()}</span>
    </label>`;
}

function checkbox(settings: ViewerSettings, path: string, label: string, hint = '', disabled = false): string {
  return field(label, `<input type="checkbox" data-setting="${path}" ${valueAt(settings, path) ? 'checked' : ''} ${disabled ? 'disabled' : ''} />`, hint);
}

function numberInput(
  settings: ViewerSettings,
  path: string,
  label: string,
  minimum: number,
  maximum: number,
  step: number,
  hint = '',
  disabled = false,
): string {
  return field(label, `<input type="number" data-setting="${path}" min="${minimum}" max="${maximum}" step="${step}" value="${escapeHtml(valueAt(settings, path))}" ${disabled ? 'disabled' : ''} />`, hint);
}

function selectInput(
  settings: ViewerSettings,
  path: string,
  label: string,
  choices: readonly (readonly [string, string | number])[],
  hint = '',
  disabled = false,
): string {
  return field(label, `<select data-setting="${path}" ${disabled ? 'disabled' : ''}>${options(choices, valueAt(settings, path))}</select>`, hint);
}

function group(title: string, content: string, note = ''): string {
  return `<fieldset class="settings-group"><legend>${escapeHtml(title)}</legend>${note ? `<p class="settings-group-note">${escapeHtml(note)}</p>` : ''}${content}</fieldset>`;
}

function colorSetting(settings: ViewerSettings, key: 'primary' | 'secondary' | 'tertiary' | 'quaternary', label: string, hint: string): string {
  const enabledPath = `interface.${key}.enabled`;
  const colorPath = `interface.${key}.color`;
  return `<div class="settings-color-row">
    <label><input type="checkbox" data-setting="${enabledPath}" ${valueAt(settings, enabledPath) ? 'checked' : ''} /><span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(hint)}</small></span></label>
    <input type="color" data-setting="${colorPath}" data-color-for="${key}" value="${escapeHtml(valueAt(settings, colorPath))}" ${valueAt(settings, enabledPath) ? '' : 'disabled'} />
    <code>${escapeHtml(valueAt(settings, colorPath))}</code>
  </div>`;
}

export class SettingsPane {
  private readonly root: HTMLDivElement;
  private readonly content: HTMLDivElement;
  private saved: ViewerSettings;
  private draft: ViewerSettings;
  private activeTab: SettingsTab = 'application';
  private activeSections: Record<string, string> = {
    export: 'png',
    application: 'interface',
  };
  private closeTimer?: number;

  constructor(private readonly options: SettingsPaneOptions) {
    this.saved = cloneViewerSettings(options.settings);
    this.draft = cloneViewerSettings(options.settings);
    this.root = document.createElement('div');
    this.root.className = 'settings-layer';
    this.root.id = 'settingsLayer';
    this.root.hidden = true;
    this.root.innerHTML = `
      <button class="settings-backdrop" type="button" data-settings-action="cancel" tabindex="-1" aria-label="Close settings"></button>
      <section class="settings-drawer" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
        <header class="settings-header">
          <div><span class="settings-kicker">Viewer preferences</span><h2 id="settingsTitle">Settings</h2></div>
          <button class="icon-button settings-close" type="button" data-settings-action="cancel" aria-label="Close settings" title="Close settings">&times;</button>
        </header>
        <div class="settings-workspace">
          <nav class="settings-tabs" role="tablist" aria-label="Settings categories"></nav>
          <div class="settings-content"></div>
        </div>
        <footer class="settings-footer">
          <span class="settings-save-note">Preferences stay on this device and browser profile.</span>
          <div>
            <button class="button" type="button" data-settings-action="reset">Reset to Defaults</button>
            <button class="button" type="button" data-settings-action="cancel">Cancel</button>
            <button class="button primary" type="button" data-settings-action="save">Save</button>
          </div>
        </footer>
      </section>`;
    document.body.append(this.root);
    this.content = this.root.querySelector<HTMLDivElement>('.settings-content')!;
    this.root.addEventListener('click', (event) => this.onClick(event));
    this.root.addEventListener('input', (event) => this.onInput(event));
    this.root.addEventListener('change', (event) => this.onInput(event));
    this.root.addEventListener('keydown', (event) => this.onKeyDown(event));
    this.options.trigger.addEventListener('click', () => this.toggle());
  }

  get isOpen(): boolean {
    return !this.root.hidden && this.root.dataset.open === 'true';
  }

  open(): void {
    if (this.closeTimer !== undefined) window.clearTimeout(this.closeTimer);
    this.saved = cloneViewerSettings(this.options.settings);
    this.draft = cloneViewerSettings(this.saved);
    this.render();
    this.root.hidden = false;
    this.options.trigger.setAttribute('aria-expanded', 'true');
    requestAnimationFrame(() => {
      this.root.dataset.open = 'true';
      this.root.querySelector<HTMLButtonElement>('.settings-close')?.focus();
    });
  }

  close(restore = true): void {
    if (this.root.hidden) return;
    if (restore) this.options.onPreview(cloneViewerSettings(this.saved));
    this.root.dataset.open = 'false';
    this.options.trigger.setAttribute('aria-expanded', 'false');
    this.closeTimer = window.setTimeout(() => {
      this.root.hidden = true;
      this.options.trigger.focus();
    }, matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 400);
  }

  toggle(): void {
    if (this.isOpen) this.close();
    else this.open();
  }

  private render(): void {
    const tabs: readonly [SettingsTab, string][] = [
      ['export', 'Export'],
      ['paths', 'Paths'],
      ['keybinds', 'Keybinds'],
      ['application', 'Application'],
    ];
    const tabList = this.root.querySelector<HTMLElement>('.settings-tabs')!;
    tabList.innerHTML = tabs.map(([key, label]) => `<button type="button" role="tab" data-settings-tab="${key}" aria-selected="${this.activeTab === key}" class="${this.activeTab === key ? 'active' : ''}">${label}</button>`).join('');
    this.renderContent();
  }

  private renderContent(): void {
    if (this.activeTab === 'export') this.content.innerHTML = this.renderExport();
    if (this.activeTab === 'paths') this.content.innerHTML = this.renderPaths();
    if (this.activeTab === 'keybinds') this.content.innerHTML = this.renderKeybinds();
    if (this.activeTab === 'application') this.content.innerHTML = this.renderApplication();
    this.syncDependencies();
  }

  private sectionTabs(owner: 'export' | 'application', entries: readonly (readonly [string, string])[]): string {
    const active = this.activeSections[owner];
    return `<nav class="settings-section-tabs" aria-label="${owner} settings">${entries.map(([key, label]) => `<button type="button" data-settings-section-owner="${owner}" data-settings-section="${key}" class="${active === key ? 'active' : ''}">${escapeHtml(label)}</button>`).join('')}</nav>`;
  }

  private renderExport(): string {
    const sections = [
      ['png', 'PNG'], ['video', 'Fast Video'], ['gif', 'GIF'], ['framing', 'Framing'],
    ] as const;
    const active = this.activeSections.export;
    let body = '';
    if (active === 'video') body = group('Browser Video Encoding', [
      selectInput(this.draft, 'export.video.format', 'Format', [['Auto (MP4 / WebM)', 'auto'], ['MP4 (H.264)', 'mp4'], ['WebM (VP9 / VP8)', 'webm'], ['MOV (H.264, opaque)', 'mov']]),
      selectInput(this.draft, 'export.video.mode', 'Processing', [['Auto', 'auto'], ['Fast (WebCodecs)', 'fast'], ['Compatibility (real-time)', 'compatibility']]),
      numberInput(this.draft, 'export.video.bitrateMbps', 'Bitrate (Mbps)', 1, 100, 1),
      checkbox(this.draft, 'export.video.includeAudio', 'Include matched audio', 'Independent of playback mute. Idle tracks without matched audio remain silent.'),
    ].join(''), 'Fast mode uses WebCodecs in a worker (HTTPS/localhost). Compatibility uses the real-time browser recorder, including on HTTP where supported; keep the tab visible. Both use PNG frame rate, framing, scale, and background settings. Opaque video only.');
    if (active === 'png') body = group('PNG and PNG Sequence', [
      numberInput(this.draft, 'export.png.scaleMultiplier', 'Full-resolution multiplier', 1, 8, 1, 'Scales exported pixels from 1x through 8x.'),
      numberInput(this.draft, 'export.png.sequenceFps', 'Sequence frame rate', 1, 120, 1),
      selectInput(this.draft, 'export.png.framing', 'Framing', [['Animation canvas', 'animation'], ['Stable content bounds', 'content']]),
      checkbox(this.draft, 'export.png.solidBackground', 'Fill a solid background', 'Leave disabled for transparent PNG output.'),
      field('Background color', `<input type="color" data-setting="export.png.backgroundColor" value="${escapeHtml(this.draft.export.png.backgroundColor)}" ${this.draft.export.png.solidBackground ? '' : 'disabled'} />`),
    ].join(''), 'These settings are connected to the browser PNG and ZIP export pipeline.');
    if (active === 'gif') body = group('Animated GIF', [
      numberInput(this.draft, 'export.gif.fps', 'Frame rate', 1, 50, 1, 'GIF delay precision is 10 ms. Browser playback caps this path at 50 fps.'),
      selectInput(this.draft, 'export.gif.colors', 'Palette colors', [['16', 16], ['32', 32], ['64', 64], ['128', 128], ['256', 256]]),
      numberInput(this.draft, 'export.gif.loop', 'Loop count', 0, 100, 1, '0 means infinite.'),
    ].join(''), 'Worker-based GIF export is active. It uses the shared frame framing/resolution/background controls. GIF has no audio and only 1-bit transparency.');
    if (active === 'framing') body = group('Universal Export Framing', [
      numberInput(this.draft, 'export.framing.padding', 'Bounds padding (px)', 0, 256, 1, 'Applied to stable content bounds.'),
    ].join(''), 'Padding applies to stable content bounds for the current animation.');
    return `${this.sectionTabs('export', sections)}<div class="settings-section-body">${body}</div>`;
  }

  private renderPaths(): string {
    return `<div class="settings-single-page">
      ${group('Browser Asset Access', `
        <p class="settings-explanation">Import files or connect a game folder without uploading assets. Enable retention in File Selection to keep local copies across refreshes. Supported browsers also remember authorized folders and check them for updates at startup.</p>
        <div class="settings-path-actions">
          <button class="button primary" type="button" data-settings-action="open-folder">Open Game Folder</button>
          <button class="button" type="button" data-settings-action="open-files">Open Animation Files</button>
          <button class="button" type="button" data-settings-action="manage-assets">Manage retained assets</button>
        </div>
        <div class="settings-capability-list">
          <div><span>Processing location</span><strong>On this device</strong></div>
          <div><span>Saved asset library</span><strong>Opt-in IndexedDB storage</strong></div>
          <div><span>Live game folder updates</span><strong>Saved read-only handle where supported</strong></div>
          <div><span>DOF download directory permission</span><strong>Managed by Asset Grabber</strong></div>
        </div>
      `, 'The web equivalent keeps local processing intact while respecting browser file-access rules.')}
    </div>`;
  }

  private renderKeybinds(): string {
    const groups = [...new Set(KEYBINDS.map((entry) => entry.group))];
    return `<div class="settings-single-page settings-keybind-page">
      <p class="settings-explanation">Select a shortcut field and press the desired key combination. Backspace or Delete clears it.</p>
      ${groups.map((groupName) => group(groupName, KEYBINDS.filter((entry) => entry.group === groupName).map((entry) => `
        <label class="settings-keybind-row">
          <span><strong>${escapeHtml(entry.label)}</strong><small>${escapeHtml(entry.description)}</small></span>
          <input type="text" readonly data-keybind="${entry.key}" value="${escapeHtml(this.draft.keybinds[entry.key] ?? '')}" placeholder="None" />
          ${availability()}
        </label>`).join(''))).join('')}
      <button class="button" type="button" data-settings-action="reset-keybinds">Reset Keybinds to Defaults</button>
    </div>`;
  }

  private renderApplication(): string {
    const sections = [
      ['interface', 'Interface'], ['viewport', 'Viewport'], ['conversion', 'Conversion'],
      ['playback', 'Playback'], ['system', 'System'],
    ] as const;
    const active = this.activeSections.application;
    let body = '';
    if (active === 'interface') body = [
      group('Interface Scale', numberInput(this.draft, 'interface.scale', 'UI scale', 0.5, 2, 0.05, 'Scales the complete viewer chrome from 50% through 200%.')),
      group('Appearance', selectInput(this.draft, 'interface.themeMode', 'Color mode', [['Follow operating system', 'system'], ['Dark', 'dark'], ['Light', 'light']], 'System mode tracks prefers-color-scheme.')),
      group('UI Theme Colors', [
        colorSetting(this.draft, 'primary', 'Primary (UI Neutral)', 'Panels, surfaces, and neutral controls.'),
        colorSetting(this.draft, 'secondary', 'Secondary (Accent)', 'Selections and primary actions.'),
        colorSetting(this.draft, 'tertiary', 'Tertiary (Markers / Tool State)', 'Tool states, indicators, and secondary highlights.'),
        colorSetting(this.draft, 'quaternary', 'Quaternary (Timeline Slider)', 'Timeline track and scrubber accents.'),
      ].join(''), 'Color overrides preserve the contrast direction of the selected light or dark mode.'),
    ].join('');
    if (active === 'viewport') body = [
      group('Camera and View', [
        checkbox(this.draft, 'viewport.zoomToCursor', 'Zoom towards mouse cursor', 'When disabled, wheel zoom remains centered in the viewport.'),
      ].join('')),
      group('Viewport Rendering', [
        field('Sprite resampling', `<select data-setting="viewport.spriteFilter">${options([
          ['Nearest (Pixel)', 'nearest'], ['Bilinear (Smooth)', 'bilinear'],
        ], this.draft.viewport.spriteFilter === 'nearest' ? 'nearest' : 'bilinear')}</select>`, 'Choose crisp pixels or smooth texture sampling.'),
      ].join('')),
      group('DOF Particles', [
        checkbox(this.draft, 'viewport.particlesWorldSpace', 'Force world-space simulation', 'Keeps emitted particles in world space after spawning.'),
        numberInput(this.draft, 'viewport.particleCap', 'Viewport particle cap', 0, 1000000, 50, 'Limits active sprites without changing source animation data.'),
      ].join('')),
    ].join('');
    if (active === 'conversion') body = [
      group('Animation Detection', [
        checkbox(this.draft, 'conversion.automaticRevisionDetection', 'Automatic revision detection', 'When disabled, the selected manual revision is used without parser probing.'),
        selectInput(this.draft, 'conversion.manualRevisionMode', 'Manual BIN revision', [
          ['Rev 1', '1'], ['Rev 1 (Legacy Mobile)', 'legacy'], ['Rev 2', '2'],
          ['Rev 2 (My Muppets Show)', 'muppets'], ['Rev 3 (Monster Choir)', '3'],
          ['Rev 4', '4'], ['Rev 5', '5'], ['Rev 6', '6'],
        ]),
      ].join('')),
    ].join('');
    if (active === 'playback') body = [
      group('Playback Defaults', [
        checkbox(this.draft, 'playback.loop', 'Loop animations by default'),
        numberInput(this.draft, 'playback.volume', 'Default audio volume', 0, 1, 0.05),
        checkbox(this.draft, 'playback.muted', 'Start audio muted'),
      ].join('')),
    ].join('');
    if (active === 'system') body = [
      group('Browser Runtime', `
        <div class="settings-capability-list">
          <div><span>Asset processing</span><strong>Local browser workers</strong></div>
          <div><span>Animation parser coverage</span><strong>BIN revisions 1-6</strong></div>
          <div><span>Renderer</span><strong>WebGL 2 / PixiJS</strong></div>
          <div><span>Persistent preferences</span><strong>Local storage</strong></div>
        </div>`),
    ].join('');
    return `${this.sectionTabs('application', sections)}<div class="settings-section-body">${body}</div>`;
  }

  private onClick(event: Event): void {
    const target = event.target as HTMLElement;
    const tab = target.closest<HTMLButtonElement>('[data-settings-tab]');
    if (tab) {
      this.activeTab = tab.dataset.settingsTab as SettingsTab;
      this.render();
      return;
    }
    const section = target.closest<HTMLButtonElement>('[data-settings-section]');
    if (section) {
      this.activeSections[section.dataset.settingsSectionOwner!] = section.dataset.settingsSection!;
      this.renderContent();
      return;
    }
    const action = target.closest<HTMLButtonElement>('[data-settings-action]')?.dataset.settingsAction;
    if (action === 'cancel') this.close(true);
    if (action === 'save') {
      this.saved = normalizeViewerSettings(this.draft);
      this.draft = cloneViewerSettings(this.saved);
      this.options.onSave(cloneViewerSettings(this.saved));
      this.close(false);
    }
    if (action === 'reset') {
      this.draft = cloneViewerSettings(DEFAULT_VIEWER_SETTINGS);
      this.preview();
      this.render();
    }
    if (action === 'reset-keybinds') {
      this.draft.keybinds = { ...DEFAULT_KEYBINDS };
      this.preview();
      this.renderContent();
    }
    if (action === 'open-files') {
      this.close(true);
      this.options.onOpenFiles();
    }
    if (action === 'open-folder') {
      this.close(true);
      this.options.onOpenFolder();
    }
    if (action === 'manage-assets') {
      this.close(true);
      this.options.onManageAssets();
    }
  }

  private onInput(event: Event): void {
    const input = event.target as HTMLInputElement | HTMLSelectElement;
    const path = input.dataset.setting;
    if (!path || input.disabled) return;
    let value: string | number | boolean = input.value;
    if (input instanceof HTMLInputElement && input.type === 'checkbox') value = input.checked;
    if (input instanceof HTMLInputElement && input.type === 'number') {
      if (!Number.isFinite(input.valueAsNumber)) return;
      value = input.valueAsNumber;
    }
    if (input instanceof HTMLSelectElement && typeof valueAt(this.draft, path) === 'number') {
      value = Number(input.value);
    }
    setValueAt(this.draft, path, value);
    if (input.dataset.colorFor && input.nextElementSibling) {
      input.nextElementSibling.textContent = input.value.toLowerCase();
    }
    this.syncDependencies();
    this.preview();
  }

  private onKeyDown(event: KeyboardEvent): void {
    if (event.key === 'Escape') {
      event.preventDefault();
      this.close(true);
      return;
    }
    const input = (event.target as HTMLElement).closest<HTMLInputElement>('[data-keybind]');
    if (!input || input.disabled) return;
    event.preventDefault();
    const value = event.key === 'Backspace' || event.key === 'Delete'
      ? ''
      : keyboardEventToShortcut(event);
    if (!value && event.key !== 'Backspace' && event.key !== 'Delete') return;
    input.value = value;
    this.draft.keybinds[input.dataset.keybind!] = value;
    this.preview();
  }

  private syncDependencies(): void {
    (['primary', 'secondary', 'tertiary', 'quaternary'] as const).forEach((key) => {
      const enabled = Boolean(valueAt(this.draft, `interface.${key}.enabled`));
      const color = this.content.querySelector<HTMLInputElement>(`[data-color-for="${key}"]`);
      if (color) color.disabled = !enabled;
    });
    const background = this.content.querySelector<HTMLInputElement>('[data-setting="export.png.backgroundColor"]');
    if (background) background.disabled = !this.draft.export.png.solidBackground;
    const manualRevision = this.content.querySelector<HTMLSelectElement>('[data-setting="conversion.manualRevisionMode"]');
    if (manualRevision) manualRevision.disabled = this.draft.conversion.automaticRevisionDetection;
  }

  private preview(): void {
    this.options.onPreview(normalizeViewerSettings(this.draft));
  }
}
