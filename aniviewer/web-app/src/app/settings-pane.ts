import {
  cloneViewerSettings,
  DEFAULT_KEYBINDS,
  DEFAULT_VIEWER_SETTINGS,
  keyboardEventToShortcut,
  normalizeViewerSettings,
  type ViewerSettings,
} from './viewer-settings';

type SettingsTab = 'export' | 'tools' | 'paths' | 'keybinds' | 'application';

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
  available: boolean;
}

const KEYBINDS: readonly KeybindDefinition[] = [
  { key: 'toggle_playback', label: 'Play / Pause', group: 'Playback', description: 'Toggle animation playback.', available: true },
  { key: 'toggle_loop', label: 'Toggle Loop', group: 'Playback', description: 'Toggle looping on the current animation.', available: true },
  { key: 'record_pose', label: 'Record Pose', group: 'Keyframes', description: 'Record the current pose to the active keyframe lane.', available: false },
  { key: 'undo', label: 'Undo', group: 'Edit', description: 'Undo the most recent transform action.', available: true },
  { key: 'redo', label: 'Redo', group: 'Edit', description: 'Redo the most recently undone transform.', available: true },
  { key: 'redo_alt', label: 'Redo (Alt)', group: 'Edit', description: 'Alternative redo shortcut.', available: true },
  { key: 'copy_keyframes', label: 'Copy Keyframes', group: 'Edit', description: 'Copy selected keyframes.', available: false },
  { key: 'paste_keyframes', label: 'Paste Keyframes', group: 'Edit', description: 'Paste copied keyframes.', available: false },
  { key: 'move_tool', label: 'Move Tool', group: 'Tools', description: 'Activate sprite dragging.', available: true },
  { key: 'select_tool', label: 'Select Tool', group: 'Tools', description: 'Select without moving sprites.', available: true },
  { key: 'pan_tool', label: 'Pan Tool', group: 'Tools', description: 'Drag the camera without editing sprites.', available: true },
  { key: 'free_transform', label: 'Free Transform', group: 'Tools', description: 'Activate the scale and rotation gizmos.', available: false },
  { key: 'toggle_rotation_gizmo', label: 'Rotation Tool', group: 'Tools', description: 'Activate rotation controls.', available: true },
  { key: 'toggle_scale_gizmo', label: 'Scale Tool', group: 'Tools', description: 'Activate scaling controls.', available: true },
  { key: 'fullscreen', label: 'Fullscreen', group: 'View', description: 'Toggle browser fullscreen mode.', available: true },
  { key: 'toggle_controls_panel', label: 'Toggle Controls Panel', group: 'Panels', description: 'Show or hide the controls panel.', available: true },
  { key: 'toggle_layers_panel', label: 'Toggle Layer Panel', group: 'Panels', description: 'Show or hide the layer list.', available: true },
  { key: 'toggle_timeline_panel', label: 'Toggle Timeline', group: 'Panels', description: 'Show or hide the timeline panel.', available: true },
  { key: 'toggle_log_panel', label: 'Toggle Log', group: 'Panels', description: 'Show or hide the log panel.', available: false },
  { key: 'toggle_focus_mode', label: 'Toggle Focus Mode', group: 'Panels', description: 'Hide or restore both side panels.', available: true },
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

function availability(kind: 'active' | 'partial' | 'future', label?: string): string {
  const text = label ?? (kind === 'active' ? 'Web active' : kind === 'partial' ? 'Browser mapped' : 'Port pending');
  return `<span class="settings-availability ${kind}">${escapeHtml(text)}</span>`;
}

function field(label: string, control: string, hint = '', state: 'active' | 'partial' | 'future' = 'active'): string {
  return `
    <label class="settings-field">
      <span class="settings-field-copy"><strong>${escapeHtml(label)}</strong>${hint ? `<small>${escapeHtml(hint)}</small>` : ''}</span>
      <span class="settings-field-control">${control}${availability(state)}</span>
    </label>`;
}

function checkbox(settings: ViewerSettings, path: string, label: string, hint = '', disabled = false, state: 'active' | 'partial' | 'future' = 'active'): string {
  return field(label, `<input type="checkbox" data-setting="${path}" ${valueAt(settings, path) ? 'checked' : ''} ${disabled ? 'disabled' : ''} />`, hint, state);
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
  state: 'active' | 'partial' | 'future' = 'active',
): string {
  return field(label, `<input type="number" data-setting="${path}" min="${minimum}" max="${maximum}" step="${step}" value="${escapeHtml(valueAt(settings, path))}" ${disabled ? 'disabled' : ''} />`, hint, state);
}

function selectInput(
  settings: ViewerSettings,
  path: string,
  label: string,
  choices: readonly (readonly [string, string | number])[],
  hint = '',
  disabled = false,
  state: 'active' | 'partial' | 'future' = 'active',
): string {
  return field(label, `<select data-setting="${path}" ${disabled ? 'disabled' : ''}>${options(choices, valueAt(settings, path))}</select>`, hint, state);
}

function group(title: string, content: string, note = ''): string {
  return `<fieldset class="settings-group"><legend>${escapeHtml(title)}</legend>${note ? `<p class="settings-group-note">${escapeHtml(note)}</p>` : ''}${content}</fieldset>`;
}

function pendingFeature(title: string, message: string): string {
  return `<article class="settings-pending"><div>${availability('future')}</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p></article>`;
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
    tools: 'animation-transfer',
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
      ['tools', 'Tools'],
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
    if (this.activeTab === 'tools') this.content.innerHTML = this.renderTools();
    if (this.activeTab === 'paths') this.content.innerHTML = this.renderPaths();
    if (this.activeTab === 'keybinds') this.content.innerHTML = this.renderKeybinds();
    if (this.activeTab === 'application') this.content.innerHTML = this.renderApplication();
    this.syncDependencies();
  }

  private sectionTabs(owner: 'export' | 'tools' | 'application', entries: readonly (readonly [string, string])[]): string {
    const active = this.activeSections[owner];
    return `<nav class="settings-section-tabs" aria-label="${owner} settings">${entries.map(([key, label]) => `<button type="button" data-settings-section-owner="${owner}" data-settings-section="${key}" class="${active === key ? 'active' : ''}">${escapeHtml(label)}</button>`).join('')}</nav>`;
  }

  private renderExport(): string {
    const sections = [
      ['png', 'PNG'], ['video', 'Fast Video'], ['gif', 'GIF'], ['mov', 'MOV'], ['webm', 'WebM'],
      ['mp4', 'MP4'], ['psd', 'PSD'], ['ae', 'AE Rig'], ['framing', 'Framing'],
    ] as const;
    const active = this.activeSections.export;
    let body = '';
    if (active === 'video') body = group('Browser Video Encoding', [
      selectInput(this.draft, 'export.video.format', 'Format', [['Auto (MP4 / WebM)', 'auto'], ['MP4 (H.264)', 'mp4'], ['WebM (VP9 / VP8)', 'webm'], ['MOV (H.264, opaque)', 'mov']]),
      selectInput(this.draft, 'export.video.mode', 'Processing', [['Auto', 'auto'], ['Fast (WebCodecs)', 'fast'], ['Compatibility (real-time)', 'compatibility']]),
      numberInput(this.draft, 'export.video.bitrateMbps', 'Bitrate (Mbps)', 1, 100, 1),
      checkbox(this.draft, 'export.video.includeAudio', 'Include matched audio', 'Independent of playback mute. Idle tracks without matched audio remain silent.'),
    ].join(''), 'Fast mode uses WebCodecs in a worker (HTTPS/localhost). Compatibility uses the real-time browser recorder, including on HTTP where supported; keep the tab visible. Both use PNG frame rate, framing, scale, and background settings. Opaque video only. The separate desktop presets below are not used by this browser path.');
    if (active === 'png') body = group('PNG and PNG Sequence', [
      numberInput(this.draft, 'export.png.compression', 'Compression', 0, 9, 1, 'Canvas PNG compression is selected by the browser.', true, 'partial'),
      checkbox(this.draft, 'export.png.fullResolution', 'Full-resolution output', 'Use the source canvas resolution rather than the current viewport size.'),
      numberInput(this.draft, 'export.png.scaleMultiplier', 'Full-resolution multiplier', 1, 8, 1, 'Scales exported pixels from 1x through 8x.'),
      numberInput(this.draft, 'export.png.sequenceFps', 'Sequence frame rate', 1, 120, 1),
      selectInput(this.draft, 'export.png.framing', 'Framing', [['Animation canvas', 'animation'], ['Stable content bounds', 'content']]),
      checkbox(this.draft, 'export.png.solidBackground', 'Fill a solid background', 'Leave disabled for transparent PNG output.'),
      field('Background color', `<input type="color" data-setting="export.png.backgroundColor" value="${escapeHtml(this.draft.export.png.backgroundColor)}" ${this.draft.export.png.solidBackground ? '' : 'disabled'} />`),
    ].join(''), 'These settings are connected to the browser PNG and ZIP export pipeline.');
    if (active === 'gif') body = group('Animated GIF', [
      numberInput(this.draft, 'export.gif.fps', 'Frame rate', 1, 50, 1, 'GIF delay precision is 10 ms. Browser playback caps this path at 50 fps.'),
      selectInput(this.draft, 'export.gif.colors', 'Palette colors', [['16', 16], ['32', 32], ['64', 64], ['128', 128], ['256', 256]]),
      numberInput(this.draft, 'export.gif.scale', 'Output scale (%)', 10, 200, 1, '', false, 'future'),
      checkbox(this.draft, 'export.gif.dither', 'Dither colors', '', false, 'future'),
      checkbox(this.draft, 'export.gif.optimize', 'Optimize frames', '', false, 'future'),
      numberInput(this.draft, 'export.gif.loop', 'Loop count', 0, 100, 1, '0 means infinite.'),
    ].join(''), 'Worker-based GIF export is active. It uses the shared frame framing/resolution/background controls. Dithering and frame optimization remain future options. GIF has no audio and only 1-bit transparency.');
    if (active === 'mov') body = group('QuickTime MOV', [
      selectInput(this.draft, 'export.mov.codec', 'Codec', [['Apple ProRes', 'prores_ks'], ['PNG', 'png'], ['Animation / QTRLE', 'qtrle'], ['H.264', 'libx264'], ['NVIDIA H.264', 'h264_nvenc'], ['NVIDIA HEVC', 'hevc_nvenc']], '', false, 'future'),
      selectInput(this.draft, 'export.mov.quality', 'Quality', [['Low', 'low'], ['Medium', 'medium'], ['High', 'high'], ['Lossless', 'lossless']], '', false, 'future'),
      checkbox(this.draft, 'export.mov.includeAudio', 'Embed audio', '', false, 'future'),
      checkbox(this.draft, 'export.mov.fullResolution', 'Full-resolution output', '', false, 'future'),
      numberInput(this.draft, 'export.mov.scaleMultiplier', 'Resolution multiplier', 1, 8, 1, '', false, 'future'),
    ].join(''), 'Opaque H.264 MOV is available through Fast Video settings. These desktop presets, including ProRes and alpha codecs, are not connected yet.');
    if (active === 'webm') body = group('WebM', [
      selectInput(this.draft, 'export.webm.codec', 'Codec', [['VP9', 'libvpx-vp9'], ['AV1', 'libaom-av1'], ['VP8', 'libvpx']], '', false, 'future'),
      numberInput(this.draft, 'export.webm.crf', 'Constant quality (CRF)', 0, 63, 1, '', false, 'future'),
      numberInput(this.draft, 'export.webm.speed', 'Encoder speed', 0, 8, 1, '', false, 'future'),
      checkbox(this.draft, 'export.webm.includeAudio', 'Embed audio', '', false, 'future'),
      checkbox(this.draft, 'export.webm.fullResolution', 'Full-resolution output', '', false, 'future'),
      numberInput(this.draft, 'export.webm.scaleMultiplier', 'Resolution multiplier', 1, 8, 1, '', false, 'future'),
    ].join(''), 'This preset will feed WebCodecs where the browser supports it.');
    if (active === 'mp4') body = group('MP4', [
      selectInput(this.draft, 'export.mp4.codec', 'Codec', [['H.264', 'libx264'], ['HEVC', 'libx265'], ['NVIDIA H.264', 'h264_nvenc'], ['NVIDIA HEVC', 'hevc_nvenc']], '', false, 'future'),
      numberInput(this.draft, 'export.mp4.crf', 'Constant quality (CRF)', 0, 51, 1, '', false, 'future'),
      selectInput(this.draft, 'export.mp4.preset', 'Preset', ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'].map((value) => [value, value] as const), '', false, 'future'),
      numberInput(this.draft, 'export.mp4.bitrateKbps', 'Bitrate (kbps)', 0, 200000, 100, '0 selects automatic bitrate.', false, 'future'),
      checkbox(this.draft, 'export.mp4.includeAudio', 'Embed audio', '', false, 'future'),
      selectInput(this.draft, 'export.mp4.pixelFormat', 'Pixel format', [['YUV 4:2:0', 'yuv420p'], ['YUV 4:4:4', 'yuv444p']], '', false, 'future'),
      checkbox(this.draft, 'export.mp4.faststart', 'Web fast-start metadata', '', false, 'future'),
      checkbox(this.draft, 'export.mp4.fullResolution', 'Full-resolution output', '', false, 'future'),
      numberInput(this.draft, 'export.mp4.scaleMultiplier', 'Resolution multiplier', 1, 8, 1, '', false, 'future'),
    ].join(''), 'Desktop codec names are retained as migration-friendly presets; the browser encoder will map supported codecs later.');
    if (active === 'psd') body = group('Layered Photoshop', [
      checkbox(this.draft, 'export.psd.includeHidden', 'Include hidden layers', '', false, 'future'),
      checkbox(this.draft, 'export.psd.preserveResolution', 'Preserve full resolution', '', false, 'future'),
      numberInput(this.draft, 'export.psd.fullResolutionMultiplier', 'Resolution multiplier', 1, 8, 1, '', false, 'future'),
      numberInput(this.draft, 'export.psd.scale', 'Output scale (%)', 25, 400, 1, '', false, 'future'),
      selectInput(this.draft, 'export.psd.quality', 'Resampling quality', [['Fast', 'fast'], ['Balanced', 'balanced'], ['High', 'high'], ['Maximum', 'maximum']], '', false, 'future'),
      selectInput(this.draft, 'export.psd.compression', 'Layer compression', [['Raw', 'raw'], ['RLE', 'rle']], '', false, 'future'),
      checkbox(this.draft, 'export.psd.cropCanvas', 'Crop canvas to content', '', false, 'future'),
      checkbox(this.draft, 'export.psd.matchViewport', 'Match viewer zoom and pan', '', false, 'future'),
    ].join(''), 'Layered PSD writing is not yet available in the browser build.');
    if (active === 'ae') body = group('After Effects Rig', [
      selectInput(this.draft, 'export.afterEffects.rigMode', 'Rig mode', [['Automatic', 'auto'], ['Layered', 'layered'], ['Mesh', 'mesh'], ['Composite', 'composite']], '', false, 'future'),
      checkbox(this.draft, 'export.afterEffects.preserveResolution', 'Preserve full resolution', '', false, 'future'),
      numberInput(this.draft, 'export.afterEffects.fullResolutionMultiplier', 'Resolution multiplier', 1, 8, 1, '', false, 'future'),
      numberInput(this.draft, 'export.afterEffects.scale', 'Output scale (%)', 25, 400, 1, '', false, 'future'),
      selectInput(this.draft, 'export.afterEffects.quality', 'Resampling quality', [['Fast', 'fast'], ['Balanced', 'balanced'], ['High', 'high'], ['Maximum', 'maximum']], '', false, 'future'),
      selectInput(this.draft, 'export.afterEffects.compression', 'Image compression', [['Raw', 'raw'], ['RLE', 'rle']], '', false, 'future'),
      checkbox(this.draft, 'export.afterEffects.matchViewport', 'Match viewer zoom and pan', '', false, 'future'),
    ].join(''), 'After Effects project and rig generation is scheduled after editing parity.');
    if (active === 'framing') body = group('Universal Export Framing', [
      selectInput(this.draft, 'export.framing.boundsScope', 'Bounds scope', [['Current animation', 'current'], ['All loaded animations', 'all']], 'All-loaded scope is saved but currently falls back to the active animation.', false, 'partial'),
      checkbox(this.draft, 'export.framing.explicitResolution', 'Force explicit output resolution', 'Fits output into the exact dimensions while preserving aspect ratio.', true, 'future'),
      numberInput(this.draft, 'export.framing.width', 'Output width', 16, 16384, 1, '', true, 'future'),
      numberInput(this.draft, 'export.framing.height', 'Output height', 16, 16384, 1, '', true, 'future'),
      numberInput(this.draft, 'export.framing.padding', 'Bounds padding (px)', 0, 256, 1, 'Applied to stable content bounds.', false, 'active'),
    ].join(''), 'Stable bounds and padding are active. Exact output dimensions will arrive with the generalized export compositor.');
    return `${this.sectionTabs('export', sections)}<div class="settings-section-body">${body}</div>`;
  }

  private renderTools(): string {
    const sections = [['animation-transfer', 'Anim Transfer'], ['shaders', 'Shaders']] as const;
    const body = this.activeSections.tools === 'shaders'
      ? pendingFeature('Shader Overrides', 'Custom fragment shaders, vertex shaders, LUT textures, color scaling, blend overrides, and reusable shader presets require the web shader-authoring stage.')
      : pendingFeature('Animation Transfer', 'Source and destination animation mapping, transform transfer, and transfer logs will return after browser editing and keyframe recording are stable.');
    return `${this.sectionTabs('tools', sections)}<div class="settings-section-body settings-pending-grid">${body}</div>`;
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
      <p class="settings-explanation">Select a shortcut field and press the desired key combination. Backspace or Delete clears it. Shortcuts for tools not yet present are shown for desktop parity but remain disabled.</p>
      ${groups.map((groupName) => group(groupName, KEYBINDS.filter((entry) => entry.group === groupName).map((entry) => `
        <label class="settings-keybind-row">
          <span><strong>${escapeHtml(entry.label)}</strong><small>${escapeHtml(entry.description)}</small></span>
          <input type="text" readonly data-keybind="${entry.key}" value="${escapeHtml(this.draft.keybinds[entry.key] ?? '')}" placeholder="None" ${entry.available ? '' : 'disabled'} />
          ${availability(entry.available ? 'active' : 'future')}
        </label>`).join(''))).join('')}
      <button class="button" type="button" data-settings-action="reset-keybinds">Reset Keybinds to Defaults</button>
    </div>`;
  }

  private renderApplication(): string {
    const sections = [
      ['interface', 'Interface'], ['viewport', 'Viewport'], ['conversion', 'Conversion'],
      ['playback', 'Playback'], ['system', 'System'], ['diagnostics', 'Diagnostics'],
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
        checkbox(this.draft, 'viewport.spatial3dEnabled', 'Stereoscopic 3D support', 'The control is retained for parity but stereoscopic rendering is not ported.', true, 'future'),
        selectInput(this.draft, 'viewport.spatial3dMode', '3D mode', [['Acer SpatialLabs', 'spatiallabs'], ['Side-by-Side', 'side_by_side'], ['Anaglyph', 'anaglyph']], '', true, 'future'),
      ].join('')),
      group('Viewport Rendering', [
        selectInput(this.draft, 'viewport.spriteFilter', 'Sprite resampling', [
          ['Nearest (Pixel)', 'nearest'], ['Bilinear (Smooth)', 'bilinear'],
          ['Nearest Mipmap Nearest', 'nearest_mipmap_nearest'], ['Linear Mipmap Nearest', 'linear_mipmap_nearest'],
          ['Nearest Mipmap Linear', 'nearest_mipmap_linear'], ['Trilinear (Mipmapped)', 'trilinear'],
          ['Bicubic (Mitchell)', 'bicubic'], ['Lanczos (Approx)', 'lanczos'],
          ['Anisotropic 2x', 'anisotropic_2x'], ['Anisotropic 4x', 'anisotropic_4x'],
          ['Anisotropic 8x', 'anisotropic_8x'], ['Anisotropic 16x', 'anisotropic_16x'],
        ], 'Nearest is exact; other desktop filters currently map to browser linear sampling.', false, 'partial'),
        numberInput(this.draft, 'viewport.spriteFilterStrength', 'Filter strength', 0, 1, 0.05, 'Stored for parity; browser texture sampling currently applies at full strength.', true, 'partial'),
        checkbox(this.draft, 'viewport.dofAlphaSmoothing', 'DOF alpha edge smoothing', 'Pending a renderer filter implementation.', true, 'future'),
        selectInput(this.draft, 'viewport.dofAlphaSmoothingMode', 'DOF smoothing mode', [['Normal', 'normal'], ['Strong', 'strong']], '', true, 'future'),
        numberInput(this.draft, 'viewport.dofAlphaSmoothingStrength', 'DOF edge strength', 0, 1, 0.05, '', true, 'future'),
        selectInput(this.draft, 'viewport.dofSpriteShader', 'DOF sprite shader', [
          ['Auto (From Animation Data)', 'auto'], ['Anim2D/Normal+Alpha', 'anim2d'],
          ['DawnOfFire/UnlitShader', 'dawnoffire_unlit'], ['Sprites/Default', 'sprites_default'],
          ['Unlit/Transparent', 'unlit_transparent'], ['Unlit/Transparent Masked', 'unlit_transparent_masked'],
        ], 'The web renderer currently follows imported animation and material data.', true, 'partial'),
      ].join('')),
      group('Post Processing', [
        checkbox(this.draft, 'viewport.postAaEnabled', 'Post-process antialiasing', '', true, 'future'),
        selectInput(this.draft, 'viewport.postAaMode', 'AA mode', [['FinalPass FXAA', 'fxaa'], ['SMAA Approximation', 'smaa']], '', true, 'future'),
        checkbox(this.draft, 'viewport.motionBlurEnabled', 'Motion blur', '', true, 'future'),
        checkbox(this.draft, 'viewport.bloomEnabled', 'Bloom', '', true, 'future'),
        checkbox(this.draft, 'viewport.vignetteEnabled', 'Vignette', '', true, 'future'),
        checkbox(this.draft, 'viewport.grainEnabled', 'Grain', '', true, 'future'),
        checkbox(this.draft, 'viewport.chromaticAberrationEnabled', 'Chromatic aberration', '', true, 'future'),
      ].join(''), 'Effects remain disabled until the web renderer has matching, testable filter behavior.'),
      group('DOF Particles', [
        checkbox(this.draft, 'viewport.particlesWorldSpace', 'Force world-space simulation', 'Keeps emitted particles in world space after spawning.'),
        numberInput(this.draft, 'viewport.particleCap', 'Viewport particle cap', 0, 1000000, 50, 'Limits active sprites without changing source animation data.'),
        numberInput(this.draft, 'viewport.particleDistanceSensitivity', 'Distance sensitivity', 0, 100, 0.05, 'Stored for rate-over-distance emitters.', true, 'partial'),
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
      group('Monster Name Rosetta', checkbox(this.draft, 'conversion.automaticRosettaRefresh', 'Automatic Rosetta refresh', 'The hosted build ships its name data statically; this preference is retained for parity.', true, 'partial')),
      `<article class="settings-omission"><strong>BIN converter intentionally omitted</strong><p>The converter is not part of this settings pane because it will receive a separate browser-native redesign.</p></article>`,
    ].join('');
    if (active === 'playback') body = [
      group('Playback Defaults', [
        checkbox(this.draft, 'playback.loop', 'Loop animations by default'),
        numberInput(this.draft, 'playback.volume', 'Default audio volume', 0, 1, 0.05),
        checkbox(this.draft, 'playback.muted', 'Start audio muted'),
      ].join('')),
      group('Playback and Metronome', [
        checkbox(this.draft, 'playback.metronomeEnabled', 'Enable metronome when playback starts', '', true, 'future'),
        checkbox(this.draft, 'playback.metronomeAudible', 'Enable audible metronome tick', '', true, 'future'),
        numberInput(this.draft, 'playback.timeSignatureNumerator', 'Time signature numerator', 1, 16, 1, '', true, 'future'),
        selectInput(this.draft, 'playback.timeSignatureDenominator', 'Time signature denominator', [['1', 1], ['2', 2], ['4', 4], ['8', 8], ['16', 16]], '', true, 'future'),
        checkbox(this.draft, 'playback.showBeatGrid', 'Show beat grid on timeline', '', true, 'future'),
        checkbox(this.draft, 'playback.allowBeatEditing', 'Allow dragging beat markers', '', true, 'future'),
      ].join(''), 'The settings mirror desktop defaults; the web metronome and editable beat grid are not ported yet.'),
    ].join('');
    if (active === 'system') body = [
      group('Browser Runtime', `
        <div class="settings-capability-list">
          <div><span>Asset processing</span><strong>Local browser workers</strong></div>
          <div><span>Animation parser coverage</span><strong>BIN revisions 1-6</strong></div>
          <div><span>Renderer</span><strong>WebGL 2 / PixiJS</strong></div>
          <div><span>Persistent preferences</span><strong>Local storage</strong></div>
        </div>`),
      pendingFeature('FFmpeg Tools', 'Desktop FFmpeg installation does not apply to a hosted browser app. Video exports will use browser encoders and muxers instead.'),
    ].join('');
    if (active === 'diagnostics') body = [
      group('Diagnostics and Logging', [
        checkbox(this.draft, 'diagnostics.enabled', 'Enable diagnostics logging', 'Exposes runtime diagnostic metadata for browser debugging.'),
        checkbox(this.draft, 'diagnostics.highlightProblemLayers', 'Highlight problem layers', 'Reserved for layer validation results.', true, 'partial'),
        checkbox(this.draft, 'diagnostics.throttleLayerUpdates', 'Throttle layer status updates', '', true, 'partial'),
        checkbox(this.draft, 'diagnostics.performanceLogging', 'Performance logging', 'Records coarse browser performance marks.', false, 'partial'),
        numberInput(this.draft, 'diagnostics.slowSampleThresholdMs', 'Slow sample threshold (ms)', 1, 5000, 1, '', false, 'partial'),
        numberInput(this.draft, 'diagnostics.reportIntervalSeconds', 'Report interval (seconds)', 2, 120, 1, '', false, 'partial'),
        selectInput(this.draft, 'diagnostics.minimumSeverity', 'Minimum severity', [['Debug', 'DEBUG'], ['Info', 'INFO'], ['Warning', 'WARNING'], ['Error', 'ERROR']], '', false, 'partial'),
        checkbox(this.draft, 'diagnostics.includeDebugPayloads', 'Include debug payloads', '', false, 'partial'),
        numberInput(this.draft, 'diagnostics.rateLimitPerSecond', 'Maximum events per second', 10, 1000, 10, '', false, 'partial'),
        numberInput(this.draft, 'diagnostics.maxEntries', 'Log history size', 100, 10000, 100, '', false, 'partial'),
      ].join(''), 'Browser diagnostics are kept in memory and developer tools; automatic filesystem log export is intentionally omitted because a web page cannot silently write to a user path.'),
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
