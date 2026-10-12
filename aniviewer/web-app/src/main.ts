import {
  ArrowLeftRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CloudDownload,
  createIcons,
  Download,
  Eraser,
  Eye,
  EyeOff,
  ExternalLink,
  FileJson,
  FileUp,
  Film,
  FolderOpen,
  Layers3,
  Maximize2,
  Palette,
  PanelLeft,
  PanelRight,
  Pause,
  Play,
  Repeat2,
  RotateCcw,
  Search,
  Settings,
  Upload,
  Volume2,
  VolumeX,
  X,
} from 'lucide';
import playIcon from '../../assets/Play Icon.png?url';
import pauseIcon from '../../assets/Pause Icon.png?url';
import loopEnabledIcon from '../../assets/Loop Icon Selected.png?url';
import loopDisabledIcon from '../../assets/Loop Icon Unselected.png?url';
import timelineThumb from '../../assets/Timeline Slider Icon Unselected.png?url';
import timelineThumbActive from '../../assets/Timeline Slider Icon Selected.png?url';
import timelineThumbLeft from '../../assets/Timeline Slider Icon Selected Left.png?url';
import timelineThumbRight from '../../assets/Timeline Slider Icon Selected Right.png?url';
import programIcon from '../../build_assets/aniviewer program icon.ico?url';
import './styles.css';
import { SpriteTransformControls } from './app/sprite-transform-controls';
import { ControlPanelTabs } from './app/control-panel-tabs';
import { AssetRetention } from './app/asset-retention';
import { ExportDownload } from './app/export-download';
import { MonsterBrowser } from './app/monster-browser';
import { SettingsPane } from './app/settings-pane';
import { ViewerSession } from './app/viewer-session';
import {
  keyboardEventMatchesShortcut,
  loadViewerSettings,
  saveViewerSettings,
  type ViewerSettings,
} from './app/viewer-settings';
import { AssetLibrary, type IndexedAnimation, type IndexedCostume } from './core/asset-library';
import { hasFolderPicker, pickAssetFolder, type RetainedSelection } from './core/retained-assets';
import { AudioPlayback } from './core/audio-playback';
import { normalizeAudioKey, resolveAudioContext } from './core/audio-resolution';
import {
  animationCycleForAudio,
  animationTimeForAudio,
  audioTimeForAnimation,
  DEFAULT_ANIMATION_AUDIO_TIMING,
  resolveAnimationAudioTiming,
  type AnimationAudioTiming,
} from './core/audio-synchronization';
import { binFormatLabel, type BinFormat, type BinRevisionMode } from './core/bin-parser';
import {
  buildDofManifestUrl,
  DEFAULT_DOF_CDN_BASE_URL,
  DOF_MANIFEST_PRESETS,
  filterDofManifest,
  normalizeDofBaseUrl,
  normalizeDofPlatform,
  parseDofBuildHistory,
  parseDofManifest,
  rememberDofBuild,
  type DofBundlePlatform,
  type DofManifestEntry,
} from './core/dof-asset-grabber';
import {
  buildPngSequenceArchive,
  createExportFramePlan,
  sanitizeExportName,
} from './core/export-pipeline';
import { buildGifExport } from './core/gif-export';
import { awaitExportTask, buildVideoExport, createVideoFramePlan, decodeExportAudio, validateVideoExport, videoExportUnavailableReason } from './core/video-export';
import type { VideoExportFormat } from './core/video-export-protocol';
import { buildCompatibilityVideo, compatibilityVideoMime, prepareCompatibilityAudio, type CompatibilityAudioContext } from './core/video-export-compatibility';
import {
  captureDofDownloadSnapshot,
  loadStoredDofDownloadDirectory,
  requestDofDownloadDirectory,
  supportsDofDownloadImport,
  waitForDownloadedDofBundles,
  type DofDownloadDirectoryHandle,
} from './core/dof-download-import';
import type { AnimationTrack } from './core/types';
import {
  AnimationRenderer,
  type AnimationExportBounds,
  type AnimationExportSession,
} from './renderer/animation-renderer';

const appRoot = document.querySelector<HTMLDivElement>('#app');
if (!appRoot) throw new Error('Application root is unavailable.');
const favicon = document.querySelector<HTMLLinkElement>('#appIcon');
if (favicon) favicon.href = programIcon;

appRoot.innerHTML = `
  <div class="app-shell" data-mobile-panel="library">
    <header class="app-header">
      <div class="brand-block" aria-label="MSM Aniviewer">
        <span class="brand-mark" aria-hidden="true"><img src="${programIcon}" alt="" /></span>
        <div class="brand-copy">
          <strong>MSM Aniviewer</strong>
          <span>Web workspace</span>
        </div>
      </div>
      <div class="header-file" id="headerFile">No animation loaded</div>
      <div class="header-actions">
        <button class="button header-tool-button" id="fitButton" type="button" title="Fit animation to viewport" aria-label="Fit animation to viewport">
          <i data-lucide="rotate-ccw"></i><span>Fit View</span>
        </button>
        <button class="button header-tool-button" id="settingsButton" type="button" title="Open viewer settings" aria-label="Open viewer settings" aria-expanded="false" aria-controls="settingsLayer">
          <i data-lucide="settings"></i><span>Settings</span>
        </button>
      </div>
    </header>

    <main class="workspace">
      <aside class="side-panel library-panel" aria-label="Animation library">
        <div class="control-tab-strip">
          <button class="control-tab-scroll" type="button" data-control-tab-scroll="previous" title="Previous control tabs" aria-label="Previous control tabs">
            <i data-lucide="chevron-left"></i>
          </button>
          <div class="control-tab-list" role="tablist" aria-label="Control panel sections">
            <button id="controlTab-files" class="control-tab active" type="button" role="tab" data-control-tab="files" aria-controls="controlPanel-files" aria-selected="true">Files</button>
            <button id="controlTab-animation" class="control-tab" type="button" role="tab" data-control-tab="animation" aria-controls="controlPanel-animation" aria-selected="false" tabindex="-1">Animation</button>
            <button id="controlTab-playback" class="control-tab" type="button" role="tab" data-control-tab="playback" aria-controls="controlPanel-playback" aria-selected="false" tabindex="-1">Playback</button>
            <button id="controlTab-viewport" class="control-tab" type="button" role="tab" data-control-tab="viewport" aria-controls="controlPanel-viewport" aria-selected="false" tabindex="-1">Viewport</button>
            <button id="controlTab-spatial3d" class="control-tab" type="button" role="tab" data-control-tab="spatial3d" aria-controls="controlPanel-spatial3d" aria-selected="false" tabindex="-1">3D</button>
            <button id="controlTab-audio" class="control-tab" type="button" role="tab" data-control-tab="audio" aria-controls="controlPanel-audio" aria-selected="false" tabindex="-1">Audio</button>
            <button id="controlTab-sprites" class="control-tab" type="button" role="tab" data-control-tab="sprites" aria-controls="controlPanel-sprites" aria-selected="false" tabindex="-1">Sprites</button>
            <button id="controlTab-terrain" class="control-tab" type="button" role="tab" data-control-tab="terrain" aria-controls="controlPanel-terrain" aria-selected="false" tabindex="-1">Terrain</button>
            <button id="controlTab-export" class="control-tab" type="button" role="tab" data-control-tab="export" aria-controls="controlPanel-export" aria-selected="false" tabindex="-1">Export</button>
            <button id="controlTab-rigging" class="control-tab" type="button" role="tab" data-control-tab="rigging" aria-controls="controlPanel-rigging" aria-selected="false" tabindex="-1">Rigging</button>
            <button id="controlTab-system" class="control-tab" type="button" role="tab" data-control-tab="system" aria-controls="controlPanel-system" aria-selected="false" tabindex="-1">System</button>
          </div>
          <button class="control-tab-scroll" type="button" data-control-tab-scroll="next" title="Next control tabs" aria-label="Next control tabs">
            <i data-lucide="chevron-right"></i>
          </button>
        </div>

        <div class="control-tab-viewport">
          <section id="controlPanel-files" class="control-tab-panel" role="tabpanel" data-control-panel="files" aria-labelledby="controlTab-files">
            <div class="panel-heading control-section-heading">
              <div><i data-lucide="file-json"></i><h2>File Selection</h2></div>
              <span class="count-badge" id="fileCount">0 files</span>
            </div>
            <div class="panel-section control-panel-scroll">
              <div class="file-actions" aria-label="File selection actions">
                <button class="button" id="importFolderButton" type="button">
                  <i data-lucide="folder-open"></i><span>Open Game Folder</span>
                </button>
                <button class="button" id="importFilesButton" type="button">
                  <i data-lucide="upload"></i><span>Open Animation Files</span>
                </button>
                <button class="button" id="monsterBrowserButton" type="button" aria-haspopup="dialog" aria-controls="monsterBrowser">
                  <i data-lucide="search"></i><span>Monster Browser</span>
                </button>
                <button class="button" id="dofGrabberButton" type="button">
                  <i data-lucide="cloud-download"></i><span>Dawn of Fire Asset Grabber</span>
                </button>
              </div>
              <div id="assetRetentionPanel"></div>
              <label class="field-label" for="animationFileSelect">Animation file</label>
              <select id="animationFileSelect" disabled>
                <option>No animation BIN or JSON indexed</option>
              </select>
              <div class="revision-field">
                <label class="field-label" for="revisionModeSelect">BIN revision</label>
                <select id="revisionModeSelect">
                  <option value="auto">Automatic</option>
                  <option value="1">Rev 1</option>
                  <option value="legacy">Rev 1 (Legacy Mobile)</option>
                  <option value="2">Rev 2</option>
                  <option value="muppets">Rev 2 (My Muppets Show)</option>
                  <option value="3">Rev 3 (Monster Choir)</option>
                  <option value="4">Rev 4</option>
                  <option value="5">Rev 5</option>
                  <option value="6">Rev 6</option>
                </select>
              </div>
              <div class="library-meta">
                <span id="librarySize">0 B</span>
                <span id="revisionValue">Rev -</span>
              </div>
            </div>
          </section>

          <section id="controlPanel-animation" class="control-tab-panel animation-control-panel" role="tabpanel" data-control-panel="animation" aria-labelledby="controlTab-animation" hidden>
            <div class="panel-heading control-section-heading">
              <div><i data-lucide="film"></i><h2>Animation</h2></div>
              <span class="count-badge" id="trackCount">0</span>
            </div>
            <div class="panel-section animation-options">
              <div class="asset-option-field" id="appearanceField" hidden>
                <label class="field-label" for="appearanceSelect">Appearance</label>
                <select id="appearanceSelect"></select>
              </div>
              <div class="asset-option-field" id="dofStageField" hidden>
                <label class="field-label" for="dofStageSelect">Life stage</label>
                <select id="dofStageSelect">
                  <option value="all">All</option>
                  <option value="adult">Adult</option>
                  <option value="baby">Baby</option>
                </select>
              </div>
            </div>
            <div class="panel-heading section-heading track-section-heading">
              <div><h2>Select Animation</h2></div>
            </div>
            <div class="list-scroll track-list" id="trackList">
              <div class="quiet-state">No tracks loaded</div>
            </div>
          </section>

          <section id="controlPanel-playback" class="control-tab-panel" role="tabpanel" data-control-panel="playback" aria-labelledby="controlTab-playback" hidden>
            <div class="panel-heading control-section-heading"><div><i data-lucide="play"></i><h2>Playback</h2></div></div>
            <div class="control-panel-scroll">
              <fieldset class="control-group">
                <legend>Transport</legend>
                <div class="control-readout"><span>Animation</span><strong id="panelCurrentTrack">No animation</strong></div>
                <div class="control-command-grid">
                  <button class="button primary" id="panelPlayButton" type="button"><i data-lucide="play"></i><span>Play</span></button>
                  <button class="button active" id="panelLoopButton" type="button" aria-pressed="true"><i data-lucide="repeat-2"></i><span>Loop On</span></button>
                </div>
                <div class="control-readout"><span>Position</span><strong id="panelTimeValue">00:00.000 / 00:00.000</strong></div>
              </fieldset>
            </div>
          </section>

          <section id="controlPanel-viewport" class="control-tab-panel" role="tabpanel" data-control-panel="viewport" aria-labelledby="controlTab-viewport" hidden>
            <div class="panel-heading control-section-heading"><div><i data-lucide="maximize-2"></i><h2>Viewport</h2></div></div>
            <div class="control-panel-scroll">
              <fieldset class="control-group">
                <legend>Camera</legend>
                <button class="button control-wide-button" id="panelFitButton" type="button"><i data-lucide="maximize-2"></i><span>Fit Animation to View</span></button>
                <p class="control-hint">Use Pan in the viewport toolbar or drag with the middle mouse button. Scroll to zoom. On touch screens, two fingers pan and pinch-zoom without editing sprites.</p>
              </fieldset>
            </div>
          </section>

          <section id="controlPanel-spatial3d" class="control-tab-panel" role="tabpanel" data-control-panel="spatial3d" aria-labelledby="controlTab-spatial3d" hidden>
            <div class="panel-heading control-section-heading"><div><h2>Stereoscopic 3D</h2></div></div>
            <div class="control-panel-scroll"><div class="control-pending"><strong>3D controls are not available yet</strong><span>The desktop stereoscopic controls will be ported with the specialized viewer tools.</span></div></div>
          </section>

          <section id="controlPanel-audio" class="control-tab-panel" role="tabpanel" data-control-panel="audio" aria-labelledby="controlTab-audio" hidden>
            <div class="panel-heading control-section-heading"><div><i data-lucide="volume-2"></i><h2>Audio</h2></div></div>
            <div class="control-panel-scroll">
              <fieldset class="control-group">
                <legend>Audio Playback</legend>
                <button class="button control-wide-button" id="panelAudioButton" type="button" aria-pressed="false" disabled><i data-lucide="volume-2"></i><span>Mute Audio</span></button>
                <label class="control-slider-row" for="panelAudioVolume"><span>Volume</span><input id="panelAudioVolume" type="range" min="0" max="100" step="1" value="85" disabled /></label>
                <div class="control-readout"><span>Status</span><strong id="panelAudioStatus">No audio</strong></div>
              </fieldset>
            </div>
          </section>

          <section id="controlPanel-sprites" class="control-tab-panel" role="tabpanel" data-control-panel="sprites" aria-labelledby="controlTab-sprites" hidden>
            <div class="panel-heading control-section-heading"><div><h2>Sprites</h2></div></div>
            <div class="control-panel-scroll" id="spriteTransformInspector"></div>
          </section>

          <section id="controlPanel-terrain" class="control-tab-panel" role="tabpanel" data-control-panel="terrain" aria-labelledby="controlTab-terrain" hidden>
            <div class="panel-heading control-section-heading"><div><h2>Terrain</h2></div></div>
            <div class="control-panel-scroll"><div class="control-pending"><strong>Terrain controls are not available yet</strong><span>Island geometry and terrain alignment will arrive with specialized viewer support.</span></div></div>
          </section>

          <section id="controlPanel-export" class="control-tab-panel" role="tabpanel" data-control-panel="export" aria-labelledby="controlTab-export" hidden>
            <div class="panel-heading control-section-heading"><div><i data-lucide="download"></i><h2>Export</h2></div></div>
            <div class="control-panel-scroll">
              <fieldset class="control-group">
                <legend>PNG Export</legend>
                <div class="export-command-stack">
                  <button class="button primary control-wide-button" id="exportFrameButton" type="button" disabled><i data-lucide="download"></i><span>Export Current Frame (PNG)</span></button>
                  <button class="button control-wide-button" id="exportSequenceButton" type="button" disabled><i data-lucide="download"></i><span>Export Animation Frames (ZIP)</span></button>
                </div>
              </fieldset>
              <fieldset class="control-group">
                <legend>Video Export</legend>
                <label class="control-field-row" for="exportVideoFormat"><span>Format</span><select id="exportVideoFormat"><option value="auto">Auto (MP4 / WebM)</option><option value="mp4">MP4 (H.264)</option><option value="webm">WebM (VP9 / VP8)</option><option value="mov">MOV (H.264, opaque)</option></select></label>
                <label class="control-field-row" for="exportVideoMode"><span>Processing</span><select id="exportVideoMode"><option value="auto">Auto</option><option value="fast">Fast (WebCodecs)</option><option value="compatibility">Compatibility (real-time)</option></select></label>
                <label class="control-field-row" for="exportVideoBitrate"><span>Bitrate (Mbps)</span><input id="exportVideoBitrate" type="number" min="1" max="100" step="1" value="8" /></label>
                <label class="control-check-row"><input id="exportVideoAudio" type="checkbox" checked /><span>Include matched audio</span></label>
                <button class="button primary control-wide-button" id="exportVideoButton" type="button" disabled><i data-lucide="film"></i><span>Export Video</span></button>
                <p class="control-hint" id="exportVideoHint"></p>
              </fieldset>
              <fieldset class="control-group">
                <legend>Frame Settings</legend>
                <label class="control-field-row" for="exportFramingSelect"><span>Framing</span><select id="exportFramingSelect"><option value="animation">Animation canvas</option><option value="content">Stable content bounds</option></select></label>
                <label class="control-field-row" for="exportScaleSelect"><span>Resolution</span><select id="exportScaleSelect"><option value="1">1x</option><option value="2">2x</option><option value="3">3x</option><option value="4">4x</option><option value="5">5x</option><option value="6">6x</option><option value="7">7x</option><option value="8">8x</option></select></label>
                <label class="control-field-row" for="exportFpsInput"><span>Frame rate</span><input id="exportFpsInput" type="number" min="1" max="120" step="1" value="30" inputmode="numeric" /></label>
                <label class="control-check-row" for="exportBackgroundEnabled"><input id="exportBackgroundEnabled" type="checkbox" /><span>Fill background with solid color</span></label>
                <label class="control-field-row export-color-row" for="exportBackgroundColor"><span>Background</span><input id="exportBackgroundColor" type="color" value="#000000" disabled /></label>
                <p class="control-hint">Animation canvas uses the file's native dimensions. Stable content bounds measure the full animation once so sequence frames never jump or resize.</p>
              </fieldset>
              <fieldset class="control-group">
                <legend>Animated GIF</legend>
                <button class="button control-wide-button" id="exportGifButton" type="button" disabled>Export as GIF</button>
                <p class="control-hint">Works without WebCodecs or HTTPS. No audio; GIF has 1-bit transparency and up to 256 colors. Frame rate, colors, and repeat count are in Settings &gt; Export &gt; GIF; framing, resolution, and background use the controls above.</p>
              </fieldset>
              <button class="button control-wide-button export-cancel-button" id="exportCancelButton" type="button" hidden>Cancel Export</button>
              <div class="export-progress" id="exportProgress" role="status" aria-live="polite" hidden>
                <progress id="exportProgressBar" max="1" value="0"></progress>
                <span id="exportProgressText">Preparing export...</span>
              </div>
              <div id="exportResult" hidden></div>
              <div class="control-pending export-future-note"><strong>Desktop parity work</strong><span>PSD layers, After Effects rigs, transparent/ProRes video, layer passes, batch-all-animations, and animation BIN writing are still pending. MP4, MOV, WebM, GIF, PNG, and PNG ZIP are available with the limitations shown above.</span></div>
            </div>
          </section>

          <section id="controlPanel-rigging" class="control-tab-panel" role="tabpanel" data-control-panel="rigging" aria-labelledby="controlTab-rigging" hidden>
            <div class="panel-heading control-section-heading"><div><h2>Rigging</h2></div></div>
            <div class="control-panel-scroll"><div class="control-pending"><strong>Rigging controls are not available yet</strong><span>Constraints and the joint solver will be added only after deterministic editing support is in place.</span></div></div>
          </section>

          <section id="controlPanel-system" class="control-tab-panel" role="tabpanel" data-control-panel="system" aria-labelledby="controlTab-system" hidden>
            <div class="panel-heading control-section-heading"><div><h2>System</h2></div></div>
            <div class="control-panel-scroll">
              <fieldset class="control-group">
                <legend>Browser Runtime</legend>
                <div class="control-readout"><span>Processing</span><strong>Local</strong></div>
                <div class="control-readout"><span>BIN parsers</span><strong>Revisions 1-6</strong></div>
                <div class="control-readout"><span>Renderer</span><strong>WebGL</strong></div>
              </fieldset>
            </div>
          </section>
        </div>
      </aside>

      <section class="viewport-panel" id="viewportPanel" aria-label="Animation viewport">
        <div class="viewport-background"></div>
        <div class="viewport-empty" id="viewportEmpty">
          <div class="empty-mark"><i data-lucide="layers-3"></i></div>
          <strong>No animation loaded</strong>
          <span>Choose assets from the Files panel</span>
        </div>
        <div class="drop-indicator" id="dropIndicator">Drop files to add them</div>
      </section>

      <aside class="side-panel layers-panel" aria-label="Animation layers">
        <div class="panel-heading">
          <div><i data-lucide="layers-3"></i><h2>Layers</h2></div>
          <span class="count-badge" id="layerCount">0</span>
        </div>
        <div class="list-scroll layer-list" id="layerList">
          <div class="quiet-state">No layers loaded</div>
        </div>
      </aside>
    </main>

    <section class="timeline-panel" aria-label="Playback timeline">
      <button class="texture-button play-button" id="playButton" type="button" title="Play" aria-label="Play">
        <img id="playButtonImage" src="${playIcon}" alt="" />
      </button>
      <button class="texture-button loop-button active" id="loopButton" type="button" title="Loop enabled" aria-label="Loop enabled" aria-pressed="true">
        <img id="loopButtonImage" src="${loopEnabledIcon}" alt="" />
      </button>
      <button class="icon-button audio-button" id="audioButton" type="button" title="Mute audio" aria-label="Mute audio" aria-pressed="false" disabled>
        <i data-lucide="volume-2"></i>
      </button>
      <input id="audioVolume" class="audio-volume" type="range" min="0" max="100" step="1" value="85" aria-label="Audio volume" disabled />
      <div class="timeline-content">
        <input id="timeline" class="timeline-range" type="range" min="0" max="0.001" step="0.001" value="0" aria-label="Animation time" />
        <div class="timeline-meta">
          <span id="currentTrack">No animation</span>
          <span class="timeline-right-meta"><span id="audioStatus">No audio</span><span id="timeValue">00:00.000 / 00:00.000</span></span>
        </div>
      </div>
    </section>

    <nav class="mobile-panel-tabs" aria-label="Viewer panels">
      <button type="button" class="active" data-panel="library"><i data-lucide="panel-left"></i><span>Animations</span></button>
      <button type="button" data-panel="layers"><i data-lucide="panel-right"></i><span>Layers</span></button>
    </nav>

    <footer class="status-bar">
      <span class="status-dot"></span>
      <span id="statusText">Ready</span>
      <span class="local-state">Local processing</span>
    </footer>
  </div>
  <div class="modal-backdrop" id="dofGrabberBackdrop" hidden>
    <section class="grabber-dialog" role="dialog" aria-modal="true" aria-labelledby="dofGrabberTitle">
      <header class="grabber-header">
        <div>
          <i data-lucide="cloud-download"></i>
          <h2 id="dofGrabberTitle">Dawn of Fire Asset Grabber</h2>
        </div>
        <button class="icon-button" id="dofGrabberClose" type="button" title="Close asset grabber" aria-label="Close asset grabber">
          <i data-lucide="x"></i>
        </button>
      </header>

      <div class="grabber-source">
        <label>
          <span class="field-label">Known build</span>
          <select id="dofPresetSelect"></select>
        </label>
        <label>
          <span class="field-label">Platform</span>
          <select id="dofPlatformSelect">
            <option value="ios">iOS</option>
            <option value="android">Android</option>
          </select>
        </label>
        <label class="grabber-build-field">
          <span class="field-label">Build key</span>
          <input id="dofBuildKeyInput" list="dofBuildHistory" autocomplete="off" spellcheck="false" />
          <datalist id="dofBuildHistory"></datalist>
        </label>
        <details class="grabber-advanced">
          <summary>CDN source</summary>
          <label>
            <span class="field-label">Base URL</span>
            <input id="dofBaseUrlInput" type="url" spellcheck="false" />
          </label>
        </details>
      </div>

      <div class="grabber-command-bar">
        <button class="button primary" id="dofLoadManifestButton" type="button">
          <i data-lucide="cloud-download"></i><span>Load manifest</span>
        </button>
        <button class="button" id="dofImportManifestButton" type="button">
          <i data-lucide="file-up"></i><span>Import manifest</span>
        </button>
        <a class="icon-button link-button" id="dofOpenManifestLink" target="_blank" rel="noreferrer" title="Open remote manifest" aria-label="Open remote manifest">
          <i data-lucide="external-link"></i>
        </a>
        <span class="grabber-manifest-status" id="dofManifestStatus" data-status="idle">Manifest not loaded</span>
      </div>

      <div class="grabber-filter-bar">
        <label class="grabber-search">
          <i data-lucide="search"></i>
          <input id="dofBundleFilter" type="search" placeholder="Filter bundle names" autocomplete="off" />
        </label>
        <label class="check-control">
          <input id="dofStaticinfoOnly" type="checkbox" checked />
          <span>Staticinfo only</span>
        </label>
        <span class="grabber-counts" id="dofBundleCounts">0 visible / 0 selected / 0 total</span>
      </div>

      <div class="grabber-list list-scroll" id="dofBundleList">
        <div class="quiet-state">No manifest loaded</div>
      </div>

      <footer class="grabber-footer">
        <div class="grabber-selection-actions">
          <button class="button" id="dofSelectVisible" type="button" title="Select visible" aria-label="Select visible"><i data-lucide="check"></i><span>Select visible</span></button>
          <button class="button" id="dofClearVisible" type="button" title="Clear visible" aria-label="Clear visible"><i data-lucide="eraser"></i><span>Clear visible</span></button>
          <button class="button" id="dofInvertVisible" type="button" title="Invert visible" aria-label="Invert visible"><i data-lucide="arrow-left-right"></i><span>Invert visible</span></button>
        </div>
        <div class="grabber-download-actions">
          <button class="button" id="dofOpenDownloadedButton" type="button"><i data-lucide="upload"></i><span>Open files</span></button>
          <button class="button primary" id="dofDownloadChecked" type="button" disabled><i data-lucide="download"></i><span id="dofDownloadCheckedLabel">Download checked</span></button>
        </div>
      </footer>
    </section>
  </div>
  <input id="fileInput" type="file" multiple accept=".bin,.json,.xml,.png,.avif,.webp,.jpg,.jpeg,.ogg,.wav,.mp3,.m4a,.flac,.unity3d,.bundle,.zip" hidden />
  <input id="folderInput" type="file" multiple hidden />
  <input id="dofManifestInput" type="file" accept=".json,application/json" hidden />
`;

const icons = {
  ArrowLeftRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CloudDownload,
  Download,
  Eraser,
  Eye,
  EyeOff,
  ExternalLink,
  FileJson,
  FileUp,
  Film,
  FolderOpen,
  Layers3,
  Maximize2,
  Palette,
  PanelLeft,
  PanelRight,
  Pause,
  Play,
  Repeat2,
  RotateCcw,
  Search,
  Settings,
  Upload,
  Volume2,
  VolumeX,
  X,
};

function hydrateIcons(): void {
  createIcons({ icons, attrs: { 'stroke-width': 1.8 } });
}

hydrateIcons();

const element = <T extends HTMLElement>(selector: string): T => {
  const found = document.querySelector<T>(selector);
  if (!found) throw new Error(`Required interface element is missing: ${selector}`);
  return found;
};

const shell = element<HTMLDivElement>('.app-shell');
const settingsButton = element<HTMLButtonElement>('#settingsButton');
const viewportPanel = element<HTMLElement>('#viewportPanel');
const viewportEmpty = element<HTMLDivElement>('#viewportEmpty');
const dropIndicator = element<HTMLDivElement>('#dropIndicator');
const fileInput = element<HTMLInputElement>('#fileInput');
const folderInput = element<HTMLInputElement>('#folderInput');
const dofManifestInput = element<HTMLInputElement>('#dofManifestInput');
const fileSelect = element<HTMLSelectElement>('#animationFileSelect');
const revisionSelect = element<HTMLSelectElement>('#revisionModeSelect');
const appearanceField = element<HTMLDivElement>('#appearanceField');
const appearanceSelect = element<HTMLSelectElement>('#appearanceSelect');
const dofStageField = element<HTMLDivElement>('#dofStageField');
const dofStageSelect = element<HTMLSelectElement>('#dofStageSelect');
const trackList = element<HTMLDivElement>('#trackList');
const layerList = element<HTMLDivElement>('#layerList');
const timeline = element<HTMLInputElement>('#timeline');
const playButton = element<HTMLButtonElement>('#playButton');
const playButtonImage = element<HTMLImageElement>('#playButtonImage');
const loopButton = element<HTMLButtonElement>('#loopButton');
const loopButtonImage = element<HTMLImageElement>('#loopButtonImage');
const audioButton = element<HTMLButtonElement>('#audioButton');
const audioVolume = element<HTMLInputElement>('#audioVolume');
const audioStatus = element<HTMLSpanElement>('#audioStatus');
const statusText = element<HTMLSpanElement>('#statusText');
const currentTrack = element<HTMLSpanElement>('#currentTrack');
const timeValue = element<HTMLSpanElement>('#timeValue');
const headerFile = element<HTMLDivElement>('#headerFile');
const fileCount = element<HTMLSpanElement>('#fileCount');
const librarySize = element<HTMLSpanElement>('#librarySize');
const revisionValue = element<HTMLSpanElement>('#revisionValue');
const trackCount = element<HTMLSpanElement>('#trackCount');
const layerCount = element<HTMLSpanElement>('#layerCount');
const dofGrabberBackdrop = element<HTMLDivElement>('#dofGrabberBackdrop');
const dofPresetSelect = element<HTMLSelectElement>('#dofPresetSelect');
const dofPlatformSelect = element<HTMLSelectElement>('#dofPlatformSelect');
const dofBuildKeyInput = element<HTMLInputElement>('#dofBuildKeyInput');
const dofBuildHistory = element<HTMLDataListElement>('#dofBuildHistory');
const dofBaseUrlInput = element<HTMLInputElement>('#dofBaseUrlInput');
const dofOpenManifestLink = element<HTMLAnchorElement>('#dofOpenManifestLink');
const dofManifestStatus = element<HTMLSpanElement>('#dofManifestStatus');
const dofBundleFilter = element<HTMLInputElement>('#dofBundleFilter');
const dofStaticinfoOnly = element<HTMLInputElement>('#dofStaticinfoOnly');
const dofBundleCounts = element<HTMLSpanElement>('#dofBundleCounts');
const dofBundleList = element<HTMLDivElement>('#dofBundleList');
const dofDownloadChecked = element<HTMLButtonElement>('#dofDownloadChecked');
const dofDownloadCheckedLabel = element<HTMLSpanElement>('#dofDownloadCheckedLabel');
const controlTabs = new ControlPanelTabs(element<HTMLElement>('.library-panel'), {
  initialTab: 'files',
  storageKey: 'aniviewer.controlPanelTab',
});
const panelPlayButton = element<HTMLButtonElement>('#panelPlayButton');
const panelLoopButton = element<HTMLButtonElement>('#panelLoopButton');
const panelFitButton = element<HTMLButtonElement>('#panelFitButton');
const panelCurrentTrack = element<HTMLElement>('#panelCurrentTrack');
const panelTimeValue = element<HTMLElement>('#panelTimeValue');
const panelAudioButton = element<HTMLButtonElement>('#panelAudioButton');
const panelAudioVolume = element<HTMLInputElement>('#panelAudioVolume');
const panelAudioStatus = element<HTMLElement>('#panelAudioStatus');
const exportFrameButton = element<HTMLButtonElement>('#exportFrameButton');
const exportSequenceButton = element<HTMLButtonElement>('#exportSequenceButton');
const exportGifButton = element<HTMLButtonElement>('#exportGifButton');
const exportVideoButton = element<HTMLButtonElement>('#exportVideoButton');
const exportVideoFormat = element<HTMLSelectElement>('#exportVideoFormat');
const exportVideoMode = element<HTMLSelectElement>('#exportVideoMode');
const exportVideoBitrate = element<HTMLInputElement>('#exportVideoBitrate');
const exportVideoAudio = element<HTMLInputElement>('#exportVideoAudio');
const exportCancelButton = element<HTMLButtonElement>('#exportCancelButton');
const exportFramingSelect = element<HTMLSelectElement>('#exportFramingSelect');
const exportScaleSelect = element<HTMLSelectElement>('#exportScaleSelect');
const exportFpsInput = element<HTMLInputElement>('#exportFpsInput');
const exportBackgroundEnabled = element<HTMLInputElement>('#exportBackgroundEnabled');
const exportBackgroundColor = element<HTMLInputElement>('#exportBackgroundColor');
const exportProgress = element<HTMLElement>('#exportProgress');
const exportProgressBar = element<HTMLProgressElement>('#exportProgressBar');
const exportProgressText = element<HTMLElement>('#exportProgressText');
const exportDownload = new ExportDownload(element<HTMLElement>('#exportResult'));

folderInput.setAttribute('webkitdirectory', '');
folderInput.setAttribute('directory', '');
document.documentElement.style.setProperty('--timeline-thumb', `url("${timelineThumb}")`);

let library = new AssetLibrary();
const renderer = new AnimationRenderer(viewportPanel);
const audioPlayback = new AudioPlayback();
const session = new ViewerSession();
let activeEntry: IndexedAnimation | undefined;
let baseDocument: IndexedAnimation['document'];
let baseAtlases: Awaited<ReturnType<AssetLibrary['loadAtlases']>> = [];
let activeCostumes: IndexedCostume[] = [];
let audioMuted = false;
let animationAudioTiming: AnimationAudioTiming = { ...DEFAULT_ANIMATION_AUDIO_TIMING };
let animationAudioLoopIndex = 0;
let importing = false;
let revisionMode: BinRevisionMode = 'auto';
let loadGeneration = 0;
let audioGeneration = 0;
let audioPreparing = false;
let audioLoadPromise: Promise<void> = Promise.resolve();
let dofStage = 'all';
let dofManifestEntries: DofManifestEntry[] = [];
let dofSelectedBundles = new Set<string>();
let dofAutoSelectedBundles = new Set<string>();
let dofGrabberBusy = false;
let dofDownloadDirectory: DofDownloadDirectoryHandle | undefined;
let dofDownloadAbortController: AbortController | undefined;
let exportAbortController: AbortController | undefined;
let exportBusy = false;
const transformControls = new SpriteTransformControls(viewportPanel, element('#spriteTransformInspector'), session, renderer);
session.subscribe((_snapshot, change) => {
  if (change !== 'selection') return;
  layerList.querySelectorAll<HTMLElement>('[data-layer-id]').forEach((row) => {
    const selected = session.isLayerSelected(Number(row.dataset.layerId));
    row.classList.toggle('selected', selected);
    row.setAttribute('aria-selected', String(selected));
  });
});
const viewerSettings = loadViewerSettings();
let dofBuildHistoryEntries = parseDofBuildHistory(localStorage.getItem('aniviewer.dofBuildHistory'));

function escapeHtml(value: string): string {
  const node = document.createElement('span');
  node.textContent = value;
  return node.innerHTML;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(value >= 100 ? 0 : value >= 10 ? 1 : 2)} ${unit}`;
}

function formatTime(seconds: number): string {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const minutes = Math.floor(safe / 60);
  const remainder = safe - minutes * 60;
  return `${String(minutes).padStart(2, '0')}:${remainder.toFixed(3).padStart(6, '0')}`;
}

function setStatus(message: string, state: 'ready' | 'busy' | 'warning' | 'error' = 'ready'): void {
  statusText.textContent = message;
  shell.dataset.status = state;
}

function setExportBusy(busy: boolean): void {
  exportBusy = busy;
  transformControls.setBusy(busy);
  const unavailable = !session.track;
  exportFrameButton.disabled = busy || unavailable;
  exportSequenceButton.disabled = busy || unavailable;
  exportGifButton.disabled = busy || unavailable;
  // Keep unsupported actions clickable so a mobile tap produces an explanation, not a silent disabled control.
  exportVideoButton.disabled = busy || unavailable;
  exportVideoMode.disabled = busy;
  exportVideoFormat.disabled = busy;
  exportVideoBitrate.disabled = busy;
  exportVideoAudio.disabled = busy;
  settingsButton.disabled = busy;
  layerList.inert = busy;
  timeline.disabled = busy || unavailable;
  exportFramingSelect.disabled = busy;
  exportScaleSelect.disabled = busy;
  exportFpsInput.disabled = busy;
  exportBackgroundEnabled.disabled = busy;
  exportBackgroundColor.disabled = busy || !exportBackgroundEnabled.checked;
  exportCancelButton.hidden = !busy;
  const fastUnavailable = videoExportUnavailableReason();
  element<HTMLElement>('#exportVideoHint').textContent = `${fastUnavailable ? 'Fast encoding unavailable here; Auto uses the real-time recorder if supported.' : 'Fast encoding uses a worker and native acceleration when supported.'} Compatibility works without WebCodecs, including local HTTP testing, but takes at least the animation duration and requires this tab to stay visible. Video is opaque; no background fill becomes black. Audio follows the full synchronized cycle, independent of mute. MOV currently uses H.264, not ProRes.`;
}

function updateExportProgress(completed: number, total: number, message: string): void {
  exportProgress.hidden = false;
  exportProgressBar.max = Math.max(1, total);
  exportProgressBar.value = Math.max(0, Math.min(exportProgressBar.max, completed));
  exportProgressText.textContent = message;
}

function selectedExportBackground(): [number, number, number, number] | undefined {
  if (!exportBackgroundEnabled.checked) return undefined;
  const match = /^#([0-9a-f]{6})$/i.exec(exportBackgroundColor.value);
  if (!match) return [0, 0, 0, 1];
  const value = Number.parseInt(match[1]!, 16);
  return [
    ((value >> 16) & 0xff) / 255,
    ((value >> 8) & 0xff) / 255,
    (value & 0xff) / 255,
    1,
  ];
}

async function resolveExportBounds(
  exportSession: AnimationExportSession,
  sampleTimes: readonly number[],
  signal: AbortSignal,
  progressOffset = 0,
  progressTotal = sampleTimes.length,
): Promise<AnimationExportBounds> {
  if (exportFramingSelect.value !== 'content') return exportSession.info.animationBounds;
  return exportSession.measureContentBounds(
    sampleTimes,
    viewerSettings.export.framing.padding,
    signal,
    (completed) => {
      updateExportProgress(
        progressOffset + completed,
        progressTotal,
        `Measuring stable bounds ${completed}/${sampleTimes.length}`,
      );
    },
  );
}

function exportFailureMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return 'Export cancelled';
  return error instanceof Error ? error.message : String(error);
}

async function exportCurrentFrame(): Promise<void> {
  if (exportBusy || !session.track) return;
  const controller = new AbortController();
  exportAbortController = controller;
  setExportBusy(true);
  updateExportProgress(0, 1, 'Preparing current frame...');
  setStatus('Exporting current frame', 'busy');
  try {
    const blob = await renderer.withExportSession(async (exportSession) => {
      const samples = [exportSession.info.currentTime];
      const bounds = await resolveExportBounds(exportSession, samples, controller.signal, 0, 2);
      controller.signal.throwIfAborted();
      updateExportProgress(1, 2, 'Rendering PNG...');
      const png = await exportSession.capturePng({
        time: exportSession.info.currentTime,
        bounds,
        resolution: Number(exportScaleSelect.value),
        background: selectedExportBackground(),
      });
      const scale = Number(exportScaleSelect.value);
      updateExportProgress(
        2,
        2,
        `PNG ready (${Math.ceil(bounds.width * scale)}x${Math.ceil(bounds.height * scale)})`,
      );
      return png;
    });
    const name = sanitizeExportName(session.track.name);
    const frameNumber = Math.max(1, Math.floor(session.currentTime * Number(exportFpsInput.value)) + 1);
    exportDownload.present(blob, `${name}_${String(frameNumber).padStart(5, '0')}.png`);
    setStatus('Current frame exported as PNG');
  } catch (error) {
    const message = exportFailureMessage(error);
    updateExportProgress(0, 1, message);
    setStatus(message, message === 'Export cancelled' ? 'warning' : 'error');
  } finally {
    exportAbortController = undefined;
    setExportBusy(false);
  }
}

async function exportPngSequence(): Promise<void> {
  if (exportBusy || !session.track) return;
  const controller = new AbortController();
  exportAbortController = controller;
  setExportBusy(true);
  updateExportProgress(0, 1, 'Preparing PNG sequence...');
  setStatus('Exporting animation frames', 'busy');
  try {
    const result = await renderer.withExportSession(async (exportSession) => {
      const plan = createExportFramePlan(
        exportSession.info.duration,
        Number(exportFpsInput.value),
        exportSession.info.playbackRate,
      );
      const measureSteps = exportFramingSelect.value === 'content'
        ? plan.boundsSampleTimes.length
        : 0;
      const totalSteps = measureSteps + plan.animationTimes.length;
      const bounds = await resolveExportBounds(
        exportSession,
        plan.boundsSampleTimes,
        controller.signal,
        0,
        totalSteps,
      );
      controller.signal.throwIfAborted();
      const archive = await buildPngSequenceArchive({
        animationName: exportSession.info.name,
        animationTimes: plan.animationTimes,
        signal: controller.signal,
        captureFrame: (time) => exportSession.capturePng({
          time,
          bounds,
          resolution: Number(exportScaleSelect.value),
          background: selectedExportBackground(),
        }),
        onProgress: (completed, total) => updateExportProgress(
          measureSteps + completed,
          totalSteps,
          `Encoding frame ${completed}/${total}`,
        ),
      });
      return { archive, frameCount: plan.animationTimes.length, bounds };
    });
    const name = sanitizeExportName(session.track.name);
    exportDownload.present(result.archive, `${name}_png_sequence.zip`);
    updateExportProgress(
      1,
      1,
      `${result.frameCount} frame${result.frameCount === 1 ? '' : 's'} ready (${Math.ceil(result.bounds.width * Number(exportScaleSelect.value))}x${Math.ceil(result.bounds.height * Number(exportScaleSelect.value))})`,
    );
    setStatus(`${result.frameCount} PNG frame${result.frameCount === 1 ? '' : 's'} exported`);
  } catch (error) {
    const message = exportFailureMessage(error);
    updateExportProgress(0, 1, message);
    setStatus(message, message === 'Export cancelled' ? 'warning' : 'error');
  } finally {
    exportAbortController = undefined;
    setExportBusy(false);
  }
}

async function exportVideo(): Promise<void> {
  if (exportBusy || !session.track) return;
  const controller = new AbortController();
  exportAbortController = controller;
  setExportBusy(true);
  updateExportProgress(0, 1, 'Preparing video...');
  setStatus('Exporting video locally', 'busy');
  const started = performance.now();
  // Snapshot UI choices before any await. Playback controls do not alter an in-progress export.
  const resolution = Number(exportScaleSelect.value);
  const background = selectedExportBackground() ?? [0, 0, 0, 1] as [number, number, number, number];
  const settings = { ...viewerSettings.export.video };
  const compatibility = settings.mode === 'compatibility' || (settings.mode === 'auto' && Boolean(videoExportUnavailableReason()));
  let compatibilityAudio: CompatibilityAudioContext | undefined;
  try {
    if (compatibility && !compatibilityVideoMime(settings.format, false)) throw new Error('This browser cannot record that format. Try Auto format or GIF/PNG. MOV requires the fast encoder.');
    if (!compatibility && videoExportUnavailableReason()) throw new Error(`${videoExportUnavailableReason()} Select Compatibility processing or export GIF/PNG instead.`);
    // Resume from this button gesture, before awaiting asset metadata or an encoder.
    if (settings.includeAudio && (compatibility || (settings.mode === 'auto' && compatibilityVideoMime(settings.format, true)))) {
      compatibilityAudio = prepareCompatibilityAudio();
    }
    await awaitExportTask(audioLoadPromise, controller.signal);
    const audioBlob = settings.includeAudio ? audioPlayback.sourceBlob : undefined;
    controller.signal.throwIfAborted();
    const result = await renderer.withExportSession(async (exportSession) => {
      const plan = createVideoFramePlan(exportSession.info.duration, Number(exportFpsInput.value), exportSession.info.playbackRate, audioBlob ? animationAudioTiming.loopMultiplier : 1);
      const duration = Math.max(1 / plan.frameRate, plan.realDuration);
      const bounds = await resolveExportBounds(exportSession, plan.boundsSampleTimes, controller.signal);
      // Pad the right/bottom by at most one output pixel for chroma subsampling, never stretch the artwork.
      const width = Math.ceil(bounds.width * resolution / 2) * 2;
      const height = Math.ceil(bounds.height * resolution / 2) * 2;
      const videoBounds = { ...bounds, width: width / resolution, height: height / resolution };
      const options = { format: settings.format, width, height, frameRate: plan.frameRate, bitrate: settings.bitrateMbps * 1_000_000, duration };
      validateVideoExport(options);
      updateExportProgress(0, plan.animationTimes.length, audioBlob ? 'Decoding matched audio...' : 'Checking browser encoders...');
      const audio = audioBlob ? await decodeExportAudio(audioBlob, duration, controller.signal) : undefined;
      const recordCompatibility = async (pcm: typeof audio) => {
        updateExportProgress(0, duration, 'Compatibility recording (real-time). Keep this tab visible...');
        const video = await buildCompatibilityVideo({
          ...options, audio: pcm, audioContext: compatibilityAudio, animationTimes: plan.animationTimes, signal: controller.signal,
          captureCanvas: (time) => exportSession.captureCanvas({ time, bounds: videoBounds, resolution, background }),
          onProgress: (elapsed, total) => updateExportProgress(elapsed, total, `Compatibility recording: ${elapsed.toFixed(1)} / ${total.toFixed(1)}s`),
        });
        return { ...video, width, height, frames: video.renderedFrames, duration, name: exportSession.info.name, hasAudio: Boolean(pcm), compatibility: true };
      };
      if (compatibility) return recordCompatibility(audio);
      let encoderLabel = '';
      try {
        const video = await buildVideoExport({
          ...options, audio, animationTimes: plan.animationTimes, signal: controller.signal,
          captureFrame: (time, timing) => exportSession.captureVideoFrame({ time, bounds: videoBounds, resolution, background }, timing),
          onEncoder: (selection) => {
            encoderLabel = `${selection.format.toUpperCase()} / ${selection.videoCodec.toUpperCase()}`;
            updateExportProgress(0, plan.animationTimes.length, `${encoderLabel}: encoding...`);
          },
          onProgress: (completed, total) => updateExportProgress(completed, total,
            completed === total ? `${encoderLabel}: finishing video...` : `${encoderLabel}: frame ${completed}/${total}`),
        });
        return { ...video, width, height, frames: plan.animationTimes.length, duration, name: exportSession.info.name, hasAudio: Boolean(audio), compatibility: false };
      } catch (error) {
        controller.signal.throwIfAborted();
        if (settings.mode !== 'auto' || !compatibilityVideoMime(settings.format, Boolean(audioBlob))) throw error;
        setStatus('Fast encoder unavailable; switching to real-time compatibility recording', 'warning');
        // The worker may already own the PCM buffers. Decode again only on this fallback path.
        const pcm = audioBlob && audio?.channels.some((channel) => channel.byteLength === 0)
          ? await decodeExportAudio(audioBlob, duration, controller.signal) : audio;
        return recordCompatibility(pcm);
      }
    });
    controller.signal.throwIfAborted();
    exportDownload.present(result.blob, `${sanitizeExportName(result.name)}.${result.selection.format}`);
    const seconds = (performance.now() - started) / 1000;
    const summary = `${result.selection.format.toUpperCase()} ready: ${result.width}x${result.height}, ${result.frames} ${result.compatibility ? 'frames drawn' : 'frames'}${result.hasAudio ? ', with audio' : ', no audio'}, ${formatBytes(result.blob.size)} in ${seconds.toFixed(1)}s${result.compatibility ? ' (real-time compatibility)' : ''}`;
    updateExportProgress(1, 1, summary);
    setStatus(summary);
  } catch (error) {
    const detail = exportFailureMessage(error);
    const message = detail !== 'Export cancelled' && !compatibility
      ? `${detail} If fast encoding is unavailable, choose Compatibility processing and tap Export again.` : detail;
    updateExportProgress(0, 1, message);
    setStatus(message, message === 'Export cancelled' ? 'warning' : 'error');
  } finally {
    if (compatibilityAudio && compatibilityAudio.context.state !== 'closed') void compatibilityAudio.context.close().catch(() => undefined);
    exportAbortController = undefined;
    setExportBusy(false);
  }
}

async function exportGif(): Promise<void> {
  if (exportBusy || !session.track) return;
  const controller = new AbortController();
  exportAbortController = controller;
  setExportBusy(true);
  updateExportProgress(0, 1, 'Preparing GIF...');
  setStatus('Encoding GIF in a worker', 'busy');
  const gifSettings = { ...viewerSettings.export.gif };
  const resolution = Number(exportScaleSelect.value);
  const background = selectedExportBackground();
  try {
    await awaitExportTask(audioLoadPromise, controller.signal);
    const result = await renderer.withExportSession(async (exportSession) => {
      const plan = createExportFramePlan(exportSession.info.duration, Math.min(50, gifSettings.fps), exportSession.info.playbackRate);
      const bounds = await resolveExportBounds(exportSession, plan.boundsSampleTimes, controller.signal);
      return buildGifExport({
        duration: exportSession.info.duration, playbackRate: exportSession.info.playbackRate,
        frameRate: gifSettings.fps, colors: gifSettings.colors, repeat: gifSettings.loop,
        signal: controller.signal,
        captureCanvas: (time) => exportSession.captureCanvas({ time, bounds, resolution, background }),
        onProgress: (completed, total) => updateExportProgress(completed, total, `Encoding GIF frame ${completed}/${total}`),
      });
    });
    exportDownload.present(result.blob, `${sanitizeExportName(session.track.name)}.gif`);
    updateExportProgress(1, 1, `GIF ready: ${result.frames} frames, ${formatBytes(result.blob.size)}`);
    setStatus(`GIF ready: ${result.frames} frames`);
  } catch (error) {
    const message = exportFailureMessage(error);
    updateExportProgress(0, 1, message);
    setStatus(message, message === 'Export cancelled' ? 'warning' : 'error');
  } finally { exportAbortController = undefined; setExportBusy(false); }
}

function currentDofPlatform(): DofBundlePlatform {
  return normalizeDofPlatform(dofPlatformSelect.value);
}

function matchingDofPreset() {
  const buildKey = dofBuildKeyInput.value.trim();
  const platform = currentDofPlatform();
  return DOF_MANIFEST_PRESETS.find(
    (preset) => preset.buildKey === buildKey && preset.platform === platform,
  );
}

function setDofManifestStatus(message: string, state: 'idle' | 'busy' | 'ready' | 'error' = 'idle'): void {
  dofManifestStatus.textContent = message;
  dofManifestStatus.dataset.status = state;
}

function refreshDofBuildOptions(): void {
  dofPresetSelect.innerHTML = [
    ...DOF_MANIFEST_PRESETS.map(
      (preset, index) => `<option value="${index}">${escapeHtml(preset.label)}</option>`,
    ),
    '<option value="custom">Custom build</option>',
  ].join('');
  const preset = matchingDofPreset();
  dofPresetSelect.value = preset ? String(DOF_MANIFEST_PRESETS.indexOf(preset)) : 'custom';

  const known = new Map<string, DofBundlePlatform>();
  DOF_MANIFEST_PRESETS.forEach((entry) => known.set(entry.buildKey, entry.platform));
  dofBuildHistoryEntries.forEach((entry) => known.set(entry.buildKey, entry.platform));
  dofBuildHistory.innerHTML = [...known]
    .map(([buildKey, platform]) => `<option value="${escapeHtml(buildKey)}">${platform}</option>`)
    .join('');
}

function refreshDofManifestLink(): void {
  try {
    dofOpenManifestLink.href = buildDofManifestUrl(
      dofBaseUrlInput.value,
      dofBuildKeyInput.value,
      currentDofPlatform(),
    );
    dofOpenManifestLink.removeAttribute('aria-disabled');
  } catch {
    dofOpenManifestLink.removeAttribute('href');
    dofOpenManifestLink.setAttribute('aria-disabled', 'true');
  }
  refreshDofBuildOptions();
}

function visibleDofBundles(): DofManifestEntry[] {
  return filterDofManifest(dofManifestEntries, dofBundleFilter.value, dofStaticinfoOnly.checked);
}

function isDofParticleSupportBundle(name: string): boolean {
  return /^particles\.(?:unity3d|bundle)$/i.test(name);
}

function isDofStaticDataSupportBundle(name: string): boolean {
  return /^static_data\.(?:unity3d|bundle)$/i.test(name);
}

function reconcileDofSupportSelection(): void {
  const hasMonster = dofManifestEntries.some((entry) => (
    entry.name.toLowerCase().startsWith('staticinfo_') && dofSelectedBundles.has(entry.name)
  ));
  const hasChromaticMonster = dofManifestEntries.some((entry) => (
    /^staticinfo_.*_chromatic_(?:blue|green|orange|purple|red|yellow)\.(?:unity3d|bundle)$/i.test(entry.name)
    && dofSelectedBundles.has(entry.name)
  ));
  const required = new Set(dofManifestEntries.filter((entry) => (
    (hasMonster && isDofParticleSupportBundle(entry.name))
    || (hasChromaticMonster && isDofStaticDataSupportBundle(entry.name))
  )).map((entry) => entry.name));
  dofAutoSelectedBundles.forEach((name) => {
    if (required.has(name)) return;
    dofSelectedBundles.delete(name);
    dofAutoSelectedBundles.delete(name);
  });
  required.forEach((name) => {
    if (dofSelectedBundles.has(name)) return;
    dofSelectedBundles.add(name);
    dofAutoSelectedBundles.add(name);
  });
}

function updateDofBundleSummary(visible = visibleDofBundles()): void {
  const selected = dofManifestEntries.filter((entry) => dofSelectedBundles.has(entry.name));
  const selectedBytes = selected.reduce((total, entry) => total + Math.max(0, entry.size), 0);
  dofBundleCounts.textContent = `${visible.length} visible / ${selected.length} selected / ${dofManifestEntries.length} total${selected.length ? ` (${formatBytes(selectedBytes)})` : ''}`;
  dofDownloadChecked.disabled = dofGrabberBusy || selected.length === 0;
}

function renderDofBundleList(): void {
  const visible = visibleDofBundles();
  updateDofBundleSummary(visible);

  if (!dofManifestEntries.length) {
    dofBundleList.innerHTML = '<div class="quiet-state">No manifest loaded</div>';
    return;
  }
  if (!visible.length) {
    dofBundleList.innerHTML = '<div class="quiet-state">No matching bundles</div>';
    return;
  }

  dofBundleList.innerHTML = visible.map((entry) => `
    <div class="grabber-bundle-row" data-bundle-name="${escapeHtml(entry.name)}">
      <input type="checkbox" aria-label="Select ${escapeHtml(entry.name)}" ${dofSelectedBundles.has(entry.name) ? 'checked' : ''} />
      <span class="grabber-bundle-name" title="${escapeHtml(entry.name)}">${escapeHtml(entry.name)}</span>
      <span class="grabber-bundle-size">${entry.size >= 0 ? formatBytes(entry.size) : 'Unknown'}</span>
      <a class="grabber-download-link" href="${escapeHtml(entry.url)}" target="_blank" rel="noreferrer" download="${escapeHtml(entry.name)}" title="Download ${escapeHtml(entry.name)}" aria-label="Download ${escapeHtml(entry.name)}">
        <i data-lucide="download"></i>
      </a>
    </div>
  `).join('');
  dofBundleList.querySelectorAll<HTMLElement>('[data-bundle-name]').forEach((row) => {
    const name = row.dataset.bundleName;
    const checkbox = row.querySelector<HTMLInputElement>('input[type="checkbox"]');
    if (!name || !checkbox) return;
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) {
        dofSelectedBundles.add(name);
        dofAutoSelectedBundles.delete(name);
      } else {
        dofSelectedBundles.delete(name);
        dofAutoSelectedBundles.delete(name);
      }
      reconcileDofSupportSelection();
      updateDofBundleSummary(visible);
    });
  });
  hydrateIcons();
}

function rememberCurrentDofBuild(): void {
  dofBuildHistoryEntries = rememberDofBuild(
    dofBuildHistoryEntries,
    dofBuildKeyInput.value,
    currentDofPlatform(),
  );
  localStorage.setItem('aniviewer.dofBuildHistory', JSON.stringify(dofBuildHistoryEntries));
  refreshDofBuildOptions();
}

function applyDofManifest(value: unknown, sourceLabel: string): void {
  const entries = parseDofManifest(
    value,
    dofBaseUrlInput.value,
    dofBuildKeyInput.value,
    currentDofPlatform(),
  );
  if (!entries.length) throw new Error('The manifest contains no valid bundle entries.');
  dofManifestEntries = entries;
  dofSelectedBundles = new Set();
  dofAutoSelectedBundles = new Set();
  rememberCurrentDofBuild();
  renderDofBundleList();
  setDofManifestStatus(`Loaded ${entries.length} bundles from ${sourceLabel}`, 'ready');
}

function setDofGrabberBusy(busy: boolean): void {
  dofGrabberBusy = busy;
  [
    dofPresetSelect,
    dofPlatformSelect,
    dofBuildKeyInput,
    dofBaseUrlInput,
    element<HTMLButtonElement>('#dofLoadManifestButton'),
    element<HTMLButtonElement>('#dofImportManifestButton'),
  ].forEach((control) => { control.disabled = busy; });
  renderDofBundleList();
}

async function loadDofManifest(): Promise<void> {
  let manifestUrl: string;
  try {
    manifestUrl = buildDofManifestUrl(
      dofBaseUrlInput.value,
      dofBuildKeyInput.value,
      currentDofPlatform(),
    );
  } catch (error) {
    setDofManifestStatus(error instanceof Error ? error.message : String(error), 'error');
    return;
  }
  refreshDofManifestLink();
  setDofGrabberBusy(true);
  setDofManifestStatus('Loading manifest', 'busy');
  try {
    const preset = matchingDofPreset();
    const useSnapshot = preset && normalizeDofBaseUrl(dofBaseUrlInput.value) === DEFAULT_DOF_CDN_BASE_URL;
    const requestUrl = useSnapshot
      ? `${import.meta.env.BASE_URL}${preset.manifestPath}`
      : manifestUrl;
    const response = await fetch(requestUrl, { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`Manifest request returned HTTP ${response.status}.`);
    applyDofManifest(await response.json(), useSnapshot ? preset.label : manifestUrl);
  } catch (error) {
    const crossOrigin = new URL(manifestUrl).origin !== window.location.origin;
    setDofManifestStatus(
      crossOrigin
        ? 'Remote manifest access was blocked; open or import bundles.json.'
        : error instanceof Error ? error.message : String(error),
      'error',
    );
  } finally {
    setDofGrabberBusy(false);
  }
}

function openDofGrabber(): void {
  dofGrabberBackdrop.hidden = false;
  document.body.classList.add('modal-open');
  dofBundleFilter.focus();
  if (!dofManifestEntries.length) void loadDofManifest();
}

function closeDofGrabber(): void {
  dofDownloadAbortController?.abort();
  dofDownloadAbortController = undefined;
  dofGrabberBackdrop.hidden = true;
  document.body.classList.remove('modal-open');
  element<HTMLButtonElement>('#dofGrabberButton').focus();
}

function updateVisibleDofSelection(mode: 'select' | 'clear' | 'invert'): void {
  visibleDofBundles().forEach((entry) => {
    if (mode === 'select') dofSelectedBundles.add(entry.name);
    else if (mode === 'clear') dofSelectedBundles.delete(entry.name);
    else if (dofSelectedBundles.has(entry.name)) dofSelectedBundles.delete(entry.name);
    else dofSelectedBundles.add(entry.name);
  });
  reconcileDofSupportSelection();
  renderDofBundleList();
}

function startDofBundleDownloads(selected: DofManifestEntry[]): void {
  selected.forEach((entry) => {
    const anchor = document.createElement('a');
    anchor.href = entry.url;
    anchor.target = '_blank';
    anchor.rel = 'noreferrer';
    anchor.download = entry.name;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  });
}

async function downloadCheckedDofBundles(): Promise<void> {
  const selected = dofManifestEntries.filter((entry) => dofSelectedBundles.has(entry.name));
  if (!selected.length) return;
  if (!supportsDofDownloadImport()) {
    startDofBundleDownloads(selected);
    setDofManifestStatus(
      `Started ${selected.length} bundle download${selected.length === 1 ? '' : 's'}; use Open files to import`,
      'ready',
    );
    return;
  }

  setDofGrabberBusy(true);
  setDofManifestStatus('Connecting to the Downloads folder', 'busy');
  const controller = new AbortController();
  dofDownloadAbortController?.abort();
  dofDownloadAbortController = controller;
  let downloadsStarted = false;
  try {
    dofDownloadDirectory = await requestDofDownloadDirectory(dofDownloadDirectory);
    const baseline = await captureDofDownloadSnapshot(dofDownloadDirectory, selected);
    startDofBundleDownloads(selected);
    downloadsStarted = true;
    setDofManifestStatus(`Waiting for downloads (0/${selected.length} detected)`, 'busy');
    const files = await waitForDownloadedDofBundles(dofDownloadDirectory, selected, {
      baseline,
      signal: controller.signal,
      onProgress: (found, total) => {
        setDofManifestStatus(`Waiting for downloads (${found}/${total} detected)`, 'busy');
      },
    });
    setDofManifestStatus(`Importing ${files.length} downloaded bundle${files.length === 1 ? '' : 's'}`, 'ready');
    dofDownloadAbortController = undefined;
    closeDofGrabber();
    await importFiles(files);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (!downloadsStarted) {
        startDofBundleDownloads(selected);
        setDofManifestStatus('Folder access cancelled; downloads started for manual import', 'ready');
      }
    } else {
      if (!downloadsStarted) startDofBundleDownloads(selected);
      setDofManifestStatus(
        `${error instanceof Error ? error.message : String(error)}${downloadsStarted ? '' : ' Downloads started for manual import.'}`,
        'error',
      );
    }
  } finally {
    if (dofDownloadAbortController === controller) dofDownloadAbortController = undefined;
    setDofGrabberBusy(false);
  }
}

function updateLibrarySummary(): void {
  fileCount.textContent = `${library.fileCount} file${library.fileCount === 1 ? '' : 's'}`;
  librarySize.textContent = formatBytes(library.totalBytes);
}

function renderFileOptions(): void {
  if (!library.animations.length) {
    fileSelect.disabled = true;
    fileSelect.innerHTML = '<option>No animation BIN or JSON indexed</option>';
    return;
  }
  fileSelect.disabled = false;
  fileSelect.innerHTML = library.animations
    .map((entry, index) => `<option value="${index}">[${entry.kind.toUpperCase()}] ${escapeHtml(entry.name)}</option>`)
    .join('');
  if (activeEntry) {
    const activeIndex = library.animations.indexOf(activeEntry);
    if (activeIndex >= 0) fileSelect.value = String(activeIndex);
  }
}

function visibleTrackIndices(): number[] {
  const tracks = session.document?.anims ?? [];
  return tracks
    .map((_, index) => index)
    .filter((index) => {
      if (activeEntry?.kind !== 'dof' || dofStage === 'all') return true;
      return new RegExp(`(?:^|_)${dofStage}(?:_|$)`, 'i').test(tracks[index]?.name ?? '');
    });
}

function renderTracks(): void {
  const tracks = session.document?.anims ?? [];
  const visible = visibleTrackIndices();
  trackCount.textContent = visible.length === tracks.length ? String(tracks.length) : `${visible.length}/${tracks.length}`;
  if (!visible.length) {
    trackList.innerHTML = '<div class="quiet-state">No tracks loaded</div>';
    return;
  }
  trackList.innerHTML = visible
    .map(
      (trackIndex, displayIndex) => `
        <button type="button" class="list-row ${trackIndex === session.trackIndex ? 'active' : ''}" data-track-index="${trackIndex}">
          <span class="row-index">${String(displayIndex + 1).padStart(2, '0')}</span>
          ${(() => {
            const track = tracks[trackIndex]!;
            return `
          <span class="row-name">${escapeHtml(track.name)}</span>
          <span class="row-count">${track.layers.length}</span>`;
          })()}
        </button>`,
    )
    .join('');
  trackList.querySelectorAll<HTMLButtonElement>('[data-track-index]').forEach((button) => {
    button.addEventListener('click', () => selectTrack(Number(button.dataset.trackIndex)));
  });
}

function renderAppearanceOptions(): void {
  if (!activeEntry) {
    appearanceField.hidden = true;
    return;
  }
  if (activeEntry.kind === 'dof') {
    const variants = library.dofVariants(activeEntry);
    appearanceField.hidden = variants.length <= 1;
    appearanceSelect.innerHTML = variants
      .map((variant) => {
        const index = library.animations.indexOf(variant);
        const label = variant.dofAppearance === 'base'
          ? 'Base'
          : (variant.dofAppearance ?? 'Variant').replace(/\b\w/g, (letter) => letter.toUpperCase());
        return `<option value="bundle:${index}">${escapeHtml(label)}</option>`;
      })
      .join('');
    appearanceSelect.value = `bundle:${library.animations.indexOf(activeEntry)}`;
    activeCostumes = [];
    return;
  }
  activeCostumes = library.costumesFor(activeEntry);
  appearanceField.hidden = activeCostumes.length === 0;
  appearanceSelect.innerHTML = [
    '<option value="base">Base</option>',
    ...activeCostumes.map((costume, index) => `<option value="costume:${index}">${escapeHtml(costume.label)}</option>`),
  ].join('');
  appearanceSelect.value = 'base';
}

function updateDofStageOptions(): void {
  const names = session.document?.anims.map((track) => track.name) ?? [];
  const hasAdult = names.some((name) => /(?:^|_)adult(?:_|$)/i.test(name));
  const hasBaby = names.some((name) => /(?:^|_)baby(?:_|$)/i.test(name));
  dofStageField.hidden = activeEntry?.kind !== 'dof' || !(hasAdult && hasBaby);
  if (dofStageField.hidden) dofStage = 'all';
  dofStageSelect.value = dofStage;
}

function renderLayers(track?: AnimationTrack): void {
  const layers = track?.layers ?? [];
  layerCount.textContent = String(layers.length);
  if (!layers.length) {
    layerList.innerHTML = '<div class="quiet-state">No layers loaded</div>';
    return;
  }
  layerList.innerHTML = layers
    .map(
      (layer) => `
        <div class="list-row layer-row ${session.isLayerSelected(layer.id) ? 'selected' : ''} ${session.isLayerVisible(layer.id) ? '' : 'hidden-layer'}" data-layer-id="${layer.id}" data-shader-name="${escapeHtml(layer.shader_name ?? '')}" role="option" tabindex="0" aria-selected="${session.isLayerSelected(layer.id)}">
          <button type="button" class="visibility-button" title="Toggle ${escapeHtml(layer.name)}" aria-label="Toggle ${escapeHtml(layer.name)}">
            <i data-lucide="${session.isLayerVisible(layer.id) ? 'eye' : 'eye-off'}"></i>
          </button>
          <span class="row-name">${escapeHtml(layer.name || `Layer ${layer.id}`)}</span>
          <span class="row-count">${layer.id}</span>
        </div>`,
    )
    .join('');
  layerList.querySelectorAll<HTMLElement>('[data-layer-id]').forEach((row) => {
    const id = Number(row.dataset.layerId);
    const button = row.querySelector<HTMLButtonElement>('.visibility-button');
    const updateLayerSelection = (additive: boolean): void => {
      transformControls.cancelGesture();
      session.selectLayer(id, additive || transformControls.multiSelect);
      layerList.querySelectorAll<HTMLElement>('[data-layer-id]').forEach((candidate) => {
        const selected = session.isLayerSelected(Number(candidate.dataset.layerId));
        candidate.classList.toggle('selected', selected);
        candidate.setAttribute('aria-selected', String(selected));
      });
    };
    button?.addEventListener('click', (event) => {
      event.stopPropagation();
      const visible = session.toggleLayerVisible(id);
      renderer.setLayerVisible(id, visible);
      row.classList.toggle('hidden-layer', !visible);
      if (button) button.innerHTML = `<i data-lucide="${visible ? 'eye' : 'eye-off'}"></i>`;
      hydrateIcons();
    });
    row.addEventListener('click', (event) => {
      updateLayerSelection(event.ctrlKey || event.metaKey || event.shiftKey);
    });
    row.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      updateLayerSelection(event.ctrlKey || event.metaKey || event.shiftKey);
    });
  });
  hydrateIcons();
}

function synchronizedAudioTime(animationTime: number): number {
  return audioTimeForAnimation(
    animationTime,
    renderer.getDuration(),
    animationAudioTiming,
    animationAudioLoopIndex,
  );
}

function syncAudioPlayback(animationTime: number, playing: boolean): void {
  audioPlayback.sync(synchronizedAudioTime(animationTime), playing);
}

function configureAudioLoop(): void {
  audioPlayback.setLoop(session.loop,
    renderer.getDuration() * animationAudioTiming.loopMultiplier / animationAudioTiming.animationPlaybackRate);
}

renderer.playbackTimeSource = (elapsedSeconds) => {
  if (audioPreparing) return renderer.getTime();
  const audioTime = audioPlayback.playbackTime(elapsedSeconds);
  if (audioTime === undefined) return undefined;
  if (audioPlayback.nativeLoopEnabled) {
    // Native audio wraps without ending or needing another play() gesture. Derive
    // the animation sub-cycle directly, including after a background-tab return.
    animationAudioLoopIndex = animationCycleForAudio(audioTime, renderer.getDuration(), animationAudioTiming);
    viewportPanel.dataset.audioLoopIndex = String(animationAudioLoopIndex);
  }
  const time = animationTimeForAudio(
    audioTime, renderer.getDuration(), animationAudioTiming, animationAudioLoopIndex,
  );
  return audioPlayback.nativeLoopEnabled ? Math.min(renderer.getDuration(), time) : time;
};

audioPlayback.onIssue = (message) => {
  audioStatus.title = message;
  panelAudioStatus.title = message;
  setStatus(message, 'warning');
};
audioPlayback.onRecovered = () => {
  if (audioStatus.title && statusText.textContent === audioStatus.title) setStatus('Audio playback resumed');
  audioStatus.title = '';
  panelAudioStatus.title = '';
};

async function loadAudioForTrack(track?: AnimationTrack): Promise<void> {
  const generation = ++audioGeneration;
  audioPreparing = true;
  viewportPanel.dataset.audioBackend = 'loading';
  try {
    animationAudioTiming = { ...DEFAULT_ANIMATION_AUDIO_TIMING };
    animationAudioLoopIndex = 0;
    viewportPanel.dataset.audioLoopMultiplier = '1';
    viewportPanel.dataset.audioLoopIndex = '0';
    renderer.setPlaybackSpeed(1);
    await audioPlayback.load(undefined);
    audioButton.disabled = true;
    audioVolume.disabled = true;
    panelAudioButton.disabled = true;
    panelAudioVolume.disabled = true;
    audioStatus.textContent = 'No audio';
    panelAudioStatus.textContent = 'No audio';
    audioStatus.title = '';
    panelAudioStatus.title = '';
    if (!track) return;
    const clip = await library.resolveAudio(track, activeEntry);
    if (generation !== audioGeneration) return;
    await audioPlayback.load(clip);
    if (generation !== audioGeneration) return;
    viewportPanel.dataset.audioBackend = audioPlayback.backend;
    const audioContext = resolveAudioContext({
      kind: activeEntry?.kind,
      detectedFormat: activeEntry?.detectedFormat,
      sourceFormat: activeEntry?.document?.source_format,
      entryName: activeEntry?.name,
    });
    if (
      clip
      && audioContext === 'muppets'
      && /^\d+_/.test(normalizeAudioKey(track.name))
    ) {
      animationAudioTiming = resolveAnimationAudioTiming(renderer.getDuration(), audioPlayback.duration);
      renderer.setPlaybackSpeed(animationAudioTiming.animationPlaybackRate);
      viewportPanel.dataset.audioLoopMultiplier = String(animationAudioTiming.loopMultiplier);
    }
    audioButton.disabled = !clip;
    audioVolume.disabled = !clip;
    panelAudioButton.disabled = !clip;
    panelAudioVolume.disabled = !clip;
    audioStatus.textContent = clip?.name ?? 'No audio';
    panelAudioStatus.textContent = clip?.name ?? 'No audio';
    panelAudioVolume.value = audioVolume.value;
    audioPlayback.setVolume(Number(audioVolume.value) / 100);
    audioPlayback.setMuted(audioMuted);
    configureAudioLoop();
    audioPlayback.seek(synchronizedAudioTime(Number(timeline.value)));
    // Play may have been pressed while the clip was preparing.
    syncAudioPlayback(Number(timeline.value), renderer.isPlaying());
  } finally {
    if (generation === audioGeneration) {
      audioPreparing = false;
      viewportPanel.dataset.audioBackend = audioPlayback.backend;
    }
  }
}

function hasMissingDofParticles(track?: AnimationTrack): boolean {
  if (activeEntry?.kind !== 'dof' || !track?.particle_emitters?.length) return false;
  const available = new Set(library.dofParticlePrefabs().map((prefab) => prefab.pathId));
  return track.particle_emitters.some((emitter) => !available.has(emitter.prefabPathId));
}

function reportMissingDofParticles(track?: AnimationTrack): boolean {
  if (!hasMissingDofParticles(track)) return false;
  setStatus(
    `Particle asset missing for ${track?.name}: add particles.unity3d or its extracted particles/__data file`,
    'error',
  );
  return true;
}

function selectTrack(index: number): void {
  if (exportBusy) {
    setStatus('Finish or cancel the current export before changing animations', 'warning');
    return;
  }
  const track = session.document?.anims[index];
  if (!track || !session.setTrack(index)) return;
  renderer.setTrack(index);
  currentTrack.textContent = track.name;
  panelCurrentTrack.textContent = track.name;
  renderTracks();
  renderLayers(track);
  audioLoadPromise = loadAudioForTrack(track).catch((error) => {
    setStatus(`Could not prepare audio: ${error instanceof Error ? error.message : String(error)}`, 'warning');
  });
  reportMissingDofParticles(track);
  setExportBusy(exportBusy);
  if (activeEntry) assetRetention.rememberSelection({ path: activeEntry.path, track: index });
}

async function loadEntry(index: number, preferredTrack?: number, failOnError = false): Promise<void> {
  if (exportBusy) {
    setStatus('Finish or cancel the current export before loading another file', 'warning');
    return;
  }
  const entry = library.animations[index];
  if (!entry) return;
  const generation = ++loadGeneration;
  activeEntry = entry;
  revisionSelect.disabled = entry.kind !== 'bin';
  setStatus(
    entry.kind === 'bin'
      ? `Parsing ${entry.name} in background`
      : entry.kind === 'dof'
        ? `Extracting animations and textures from ${entry.name}`
        : `Loading ${entry.name}`,
    'busy',
  );
  try {
    const document = await library.loadDocument(entry, revisionMode);
    if (generation !== loadGeneration) return;
    setStatus(`Resolving atlas assets for ${entry.name}`, 'busy');
    const atlases = await library.loadAtlases(document, entry);
    const atlasReport = library.atlasLoadReport();
    if (generation !== loadGeneration) return;
    session.setDocument(document);
    baseDocument = document;
    baseAtlases = atlases;
    renderer.load(document, atlases, [], [], library.dofParticlePrefabs());
    headerFile.textContent = entry.name;
    const displayedRevision = entry.detectedRevision ?? document.source_revision ?? document.rev;
    revisionValue.textContent = entry.detectedFormat
      ? binFormatLabel(entry.detectedFormat)
      : document.source_format === 'muppets'
        ? 'Rev 2 (My Muppets Show)'
        : document.source_format === 'choir'
          ? 'Rev 3 (Monster Choir)'
          : document.source_format === 'legacy'
            ? 'Rev 1 (Legacy Mobile)'
            : document.source_format === 'dof'
              ? 'DOF Unity'
              : `Rev ${displayedRevision}`;
    viewportEmpty.hidden = true;
    renderFileOptions();
    renderAppearanceOptions();
    updateDofStageOptions();
    const visible = visibleTrackIndices();
    const initialTrack = preferredTrack !== undefined && visible.includes(preferredTrack) ? preferredTrack : visible[0] ?? 0;
    selectTrack(initialTrack);
    controlTabs.activate('animation');
    if (!hasMissingDofParticles(document.anims[initialTrack])) {
      const loadedMessage = `${document.anims.length} animation${document.anims.length === 1 ? '' : 's'} loaded from ${entry.kind.toUpperCase()}`;
      if (atlasReport.missing.length) {
        const preview = atlasReport.missing.slice(0, 3).join(', ');
        const remainder = atlasReport.missing.length > 3
          ? ` and ${atlasReport.missing.length - 3} more`
          : '';
        setStatus(
          `${loadedMessage}; missing texture source${atlasReport.missing.length === 1 ? '' : 's'}: ${preview}${remainder}`,
          'warning',
        );
      } else {
        setStatus(loadedMessage);
      }
    }
  } catch (error) {
    if (generation !== loadGeneration) return;
    viewportEmpty.hidden = false;
    setStatus(error instanceof Error ? error.message : String(error), 'error');
    if (failOnError) throw error;
  }
}

async function importFiles(files: Iterable<File>): Promise<void> {
  const selected = [...files];
  await viewerReady;
  if (exportBusy) {
    setStatus('Finish or cancel the current export before importing files', 'warning');
    return;
  }
  await assetRetention.importFiles(selected);
  fileInput.value = '';
  folderInput.value = '';
}

async function replaceLibrary(files: File[], selected?: RetainedSelection): Promise<void> {
  if (exportBusy) throw new Error('Finish or cancel the export before updating assets');
  importing = true;
  ++loadGeneration;
  const next = new AssetLibrary();
  const old = library;
  const oldEntry = activeEntry;
  const protectExisting = Boolean(oldEntry?.document && baseDocument === oldEntry.document);
  const preferredPath = selected?.path ?? activeEntry?.path;
  const preferredTrack = selected?.track ?? session.trackIndex;
  setStatus('Indexing selected assets', 'busy');
  try {
    await next.addFiles(files);
    library = next;
    activeEntry = undefined;
    updateLibrarySummary();
    renderFileOptions();
    if (library.animations.length) {
      const index = library.animations.findIndex((entry) => entry.path === preferredPath);
      // New/incomplete imports must remain selectable so users can add missing
      // atlases or correct the parser revision. Protect an already working source.
      await loadEntry(Math.max(0, index), index >= 0 ? preferredTrack : undefined,
        protectExisting && library.animations[Math.max(0, index)]?.path === oldEntry?.path);
    } else {
      session.setDocument(undefined);
      renderer.load({ rev: 6, sources: [], anims: [] }, []);
      await audioPlayback.load(undefined);
      baseDocument = undefined;
      baseAtlases = [];
      activeCostumes = [];
      renderTracks();
      renderLayers();
      renderAppearanceOptions();
      viewportEmpty.hidden = false;
      headerFile.textContent = 'No animation loaded';
      setStatus(`${library.fileCount} asset${library.fileCount === 1 ? '' : 's'} indexed; no animation BIN or JSON found`);
    }
    await old.dispose();
  } catch (error) {
    library = old;
    activeEntry = oldEntry;
    updateLibrarySummary();
    renderFileOptions();
    viewportEmpty.hidden = Boolean(baseDocument);
    await next.dispose();
    setStatus(error instanceof Error ? error.message : String(error), 'error');
    throw error;
  } finally {
    importing = false;
    monsterBrowser.refresh();
  }
}

const initialDofPreset = DOF_MANIFEST_PRESETS[0]!;
dofBaseUrlInput.value = DEFAULT_DOF_CDN_BASE_URL;
dofBuildKeyInput.value = initialDofPreset.buildKey;
dofPlatformSelect.value = initialDofPreset.platform;
refreshDofBuildOptions();
refreshDofManifestLink();
renderDofBundleList();
if (supportsDofDownloadImport()) {
  dofDownloadCheckedLabel.textContent = 'Download + import';
  dofDownloadChecked.dataset.importMode = 'automatic';
  void loadStoredDofDownloadDirectory().then((handle) => {
    if (!dofDownloadDirectory) dofDownloadDirectory = handle;
  });
} else {
  dofDownloadChecked.dataset.importMode = 'manual';
}

const openFiles = (): void => fileInput.click();
const openFolder = (): void => {
  if (exportBusy) { setStatus('Finish or cancel the export before opening a folder', 'warning'); return; }
  if (!hasFolderPicker()) { folderInput.click(); return; }
  // Invoke the native picker before any await, preserving user activation.
  void pickAssetFolder().then(async (handle) => {
    await viewerReady;
    await assetRetention.importFolder(handle);
  }).catch((error: unknown) => {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    if (error instanceof DOMException && error.name === 'NotSupportedError') { folderInput.click(); return; }
    setStatus(error instanceof Error ? error.message : String(error), 'error');
  });
};
const assetRetention = new AssetRetention({
  root: element('#assetRetentionPanel'),
  onSnapshot: replaceLibrary,
  canRefresh: () => !exportBusy,
});
const monsterBrowser = new MonsterBrowser({
  get library() { return library; },
  activeEntry: () => activeEntry,
  onSelect: (entry) => {
    const index = library.animations.indexOf(entry);
    if (index >= 0) void loadEntry(index);
  },
  onOpenFiles: openFiles,
  onOpenFolder: openFolder,
});
element<HTMLButtonElement>('#monsterBrowserButton').addEventListener('click', () => {
  if (importing || exportBusy) {
    setStatus('Finish importing or exporting before opening the Monster Browser', 'warning');
    return;
  }
  monsterBrowser.open();
});
element<HTMLButtonElement>('#dofGrabberButton').addEventListener('click', openDofGrabber);
element<HTMLButtonElement>('#dofGrabberClose').addEventListener('click', closeDofGrabber);
element<HTMLButtonElement>('#dofLoadManifestButton').addEventListener('click', () => void loadDofManifest());
element<HTMLButtonElement>('#dofImportManifestButton').addEventListener('click', () => dofManifestInput.click());
element<HTMLButtonElement>('#dofSelectVisible').addEventListener('click', () => updateVisibleDofSelection('select'));
element<HTMLButtonElement>('#dofClearVisible').addEventListener('click', () => updateVisibleDofSelection('clear'));
element<HTMLButtonElement>('#dofInvertVisible').addEventListener('click', () => updateVisibleDofSelection('invert'));
element<HTMLButtonElement>('#dofDownloadChecked').addEventListener('click', () => void downloadCheckedDofBundles());
element<HTMLButtonElement>('#dofOpenDownloadedButton').addEventListener('click', () => {
  closeDofGrabber();
  fileInput.click();
});
dofPresetSelect.addEventListener('change', () => {
  if (dofPresetSelect.value === 'custom') return;
  const preset = DOF_MANIFEST_PRESETS[Number(dofPresetSelect.value)];
  if (!preset) return;
  dofBaseUrlInput.value = DEFAULT_DOF_CDN_BASE_URL;
  dofBuildKeyInput.value = preset.buildKey;
  dofPlatformSelect.value = preset.platform;
  refreshDofManifestLink();
  void loadDofManifest();
});
dofPlatformSelect.addEventListener('change', refreshDofManifestLink);
dofBuildKeyInput.addEventListener('input', refreshDofManifestLink);
dofBaseUrlInput.addEventListener('input', refreshDofManifestLink);
dofBundleFilter.addEventListener('input', renderDofBundleList);
dofStaticinfoOnly.addEventListener('change', renderDofBundleList);
dofManifestInput.addEventListener('change', async () => {
  const file = dofManifestInput.files?.[0];
  if (!file) return;
  try {
    applyDofManifest(JSON.parse(await file.text()), file.name);
  } catch (error) {
    setDofManifestStatus(error instanceof Error ? error.message : String(error), 'error');
  } finally {
    dofManifestInput.value = '';
  }
});
dofOpenManifestLink.addEventListener('click', (event) => {
  if (!dofOpenManifestLink.hasAttribute('href')) event.preventDefault();
});
dofGrabberBackdrop.addEventListener('click', (event) => {
  if (event.target === dofGrabberBackdrop) closeDofGrabber();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !dofGrabberBackdrop.hidden) closeDofGrabber();
  if (event.defaultPrevented || settingsPane.isOpen || monsterBrowser.isOpen || !dofGrabberBackdrop.hidden) return;
  const target = event.target as HTMLElement | null;
  if (target?.matches('input, select, textarea, [contenteditable="true"]')) return;
  if (executeKeybind(event) || transformControls.handleKey(event)) event.preventDefault();
});
element<HTMLButtonElement>('#importFilesButton').addEventListener('click', openFiles);
element<HTMLButtonElement>('#importFolderButton').addEventListener('click', openFolder);
element<HTMLButtonElement>('#fitButton').addEventListener('click', () => renderer.fitToView());
panelFitButton.addEventListener('click', () => renderer.fitToView());
exportFrameButton.addEventListener('click', () => void exportCurrentFrame());
exportSequenceButton.addEventListener('click', () => void exportPngSequence());
exportVideoButton.addEventListener('click', () => void exportVideo());
exportGifButton.addEventListener('click', () => void exportGif());
exportVideoFormat.addEventListener('change', () => {
  viewerSettings.export.video.format = exportVideoFormat.value as VideoExportFormat;
  persistViewerSettings();
});
exportVideoMode.addEventListener('change', () => {
  viewerSettings.export.video.mode = exportVideoMode.value as 'auto' | 'fast' | 'compatibility';
  persistViewerSettings();
});
exportVideoAudio.addEventListener('change', () => {
  viewerSettings.export.video.includeAudio = exportVideoAudio.checked;
  persistViewerSettings();
});
exportVideoBitrate.addEventListener('change', () => {
  const value = Number(exportVideoBitrate.value);
  const bitrate = Math.max(1, Math.min(100, Number.isFinite(value) ? Math.round(value) : 8));
  exportVideoBitrate.value = String(bitrate);
  viewerSettings.export.video.bitrateMbps = bitrate;
  persistViewerSettings();
});
exportCancelButton.addEventListener('click', () => {
  exportAbortController?.abort(new DOMException('Export cancelled.', 'AbortError'));
});
exportBackgroundEnabled.addEventListener('change', () => {
  setExportBusy(exportBusy);
  viewerSettings.export.png.solidBackground = exportBackgroundEnabled.checked;
  persistViewerSettings();
});
exportBackgroundColor.addEventListener('change', () => {
  viewerSettings.export.png.backgroundColor = exportBackgroundColor.value;
  persistViewerSettings();
});
exportFramingSelect.addEventListener('change', () => {
  viewerSettings.export.png.framing = exportFramingSelect.value as 'animation' | 'content';
  persistViewerSettings();
});
exportScaleSelect.addEventListener('change', () => {
  viewerSettings.export.png.scaleMultiplier = Number(exportScaleSelect.value);
  persistViewerSettings();
});
exportFpsInput.addEventListener('change', () => {
  const value = Number(exportFpsInput.value);
  exportFpsInput.value = String(Math.max(1, Math.min(120, Number.isFinite(value) ? Math.round(value) : 30)));
  viewerSettings.export.png.sequenceFps = Number(exportFpsInput.value);
  persistViewerSettings();
});
fileInput.addEventListener('change', () => importFiles(fileInput.files ?? []));
folderInput.addEventListener('change', () => importFiles(folderInput.files ?? []));
fileSelect.addEventListener('change', () => loadEntry(Number(fileSelect.value)));
appearanceSelect.addEventListener('change', async () => {
  if (exportBusy) {
    setStatus('Finish or cancel the current export before changing appearances', 'warning');
    return;
  }
  const value = appearanceSelect.value;
  if (value.startsWith('bundle:')) {
    await loadEntry(Number(value.slice('bundle:'.length)));
    return;
  }
  if (!activeEntry || !baseDocument) return;
  const generation = ++loadGeneration;
  const previousTrackIndex = session.trackIndex;
  try {
    if (value === 'base') {
      session.setDocument(baseDocument);
      renderer.load(baseDocument, baseAtlases, [], [], library.dofParticlePrefabs());
    } else {
      const costume = activeCostumes[Number(value.slice('costume:'.length))];
      if (!costume) return;
      setStatus(`Applying ${costume.label}`, 'busy');
      const appearance = await library.loadCostumeAppearance(costume, baseDocument, baseAtlases);
      if (generation !== loadGeneration) return;
      session.setDocument(appearance.document);
      renderer.load(
        appearance.document,
        appearance.atlases,
        appearance.shaderTextures,
        appearance.attachments,
      );
    }
    updateDofStageOptions();
    const document = session.document;
    if (!document) return;
    const nextTrack = document.anims[previousTrackIndex] ? previousTrackIndex : visibleTrackIndices()[0] ?? 0;
    selectTrack(nextTrack);
    setStatus(value === 'base' ? 'Base appearance restored' : 'Costume appearance applied');
  } catch (error) {
    setStatus(error instanceof Error ? error.message : String(error), 'error');
  }
});
dofStageSelect.addEventListener('change', () => {
  if (exportBusy) {
    setStatus('Finish or cancel the current export before changing life stages', 'warning');
    return;
  }
  dofStage = dofStageSelect.value;
  const visible = visibleTrackIndices();
  if (!visible.includes(session.trackIndex)) selectTrack(visible[0] ?? 0);
  else renderTracks();
});
function revisionModeFromValue(value: string): BinRevisionMode {
  return value === 'auto'
    ? 'auto'
    : /^[1-6]$/.test(value)
      ? Number(value) as BinRevisionMode
      : value as BinFormat;
}

revisionSelect.addEventListener('change', () => {
  const value = revisionSelect.value;
  revisionMode = revisionModeFromValue(value);
  viewerSettings.conversion.automaticRevisionDetection = value === 'auto';
  if (value !== 'auto') {
    viewerSettings.conversion.manualRevisionMode = value as ViewerSettings['conversion']['manualRevisionMode'];
  }
  persistViewerSettings();
  if (!activeEntry || activeEntry.kind !== 'bin') return;
  const index = library.animations.indexOf(activeEntry);
  if (index >= 0) void loadEntry(index);
});

const togglePlayback = (): void => {
  if (exportBusy) return;
  if (!renderer.isPlaying() && renderer.getDuration() > 0) {
    if (Number(timeline.value) >= renderer.getDuration()) {
      animationAudioLoopIndex = 0;
      viewportPanel.dataset.audioLoopIndex = '0';
      renderer.setTime(0);
    }
    // Invoke play directly from the tap, not a later animation-frame retry.
    audioPlayback.sync(synchronizedAudioTime(Number(timeline.value)), true, true);
  }
  renderer.togglePlayback();
};
const updateAudioMuteControls = (): void => {
  const icon = audioMuted ? 'volume-x' : 'volume-2';
  const title = audioMuted ? 'Unmute audio' : 'Mute audio';
  audioButton.setAttribute('aria-pressed', String(audioMuted));
  audioButton.title = title;
  audioButton.setAttribute('aria-label', title);
  audioButton.innerHTML = `<i data-lucide="${icon}"></i>`;
  panelAudioButton.setAttribute('aria-pressed', String(audioMuted));
  panelAudioButton.innerHTML = `<i data-lucide="${icon}"></i><span>${audioMuted ? 'Unmute Audio' : 'Mute Audio'}</span>`;
  hydrateIcons();
};
const toggleAudioMuted = (): void => {
  audioMuted = !audioMuted;
  audioPlayback.setMuted(audioMuted);
  if (!audioMuted && renderer.isPlaying()) {
    audioPlayback.sync(synchronizedAudioTime(Number(timeline.value)), true, true);
  }
  updateAudioMuteControls();
  viewerSettings.playback.muted = audioMuted;
  persistViewerSettings();
};
const updateAudioVolume = (value: string): void => {
  audioVolume.value = value;
  panelAudioVolume.value = value;
  audioPlayback.setVolume(Number(value) / 100);
};
const updateLoopControls = (loop: boolean): void => {
  configureAudioLoop();
  loopButton.classList.toggle('active', loop);
  loopButton.setAttribute('aria-pressed', String(loop));
  loopButton.title = loop ? 'Loop enabled' : 'Loop disabled';
  loopButton.setAttribute('aria-label', loopButton.title);
  loopButtonImage.src = loop ? loopEnabledIcon : loopDisabledIcon;
  panelLoopButton.classList.toggle('active', loop);
  panelLoopButton.setAttribute('aria-pressed', String(loop));
  panelLoopButton.innerHTML = `<i data-lucide="repeat-2"></i><span>${loop ? 'Loop On' : 'Loop Off'}</span>`;
  hydrateIcons();
};
const toggleLoop = (): void => {
  const loop = session.toggleLoop();
  renderer.setLoop(loop);
  updateLoopControls(loop);
  viewerSettings.playback.loop = loop;
  persistViewerSettings();
};

function applyViewerSettings(settings: ViewerSettings): void {
  const root = document.documentElement;
  root.dataset.themeMode = settings.interface.themeMode;
  root.style.setProperty('--ui-scale', String(settings.interface.scale));
  root.style.setProperty('--ui-scale-inverse', String(1 / settings.interface.scale));
  (['primary', 'secondary', 'tertiary', 'quaternary'] as const).forEach((key) => {
    const value = settings.interface[key];
    const datasetKey = `theme${key[0]!.toUpperCase()}${key.slice(1)}`;
    root.dataset[datasetKey] = String(value.enabled);
    root.style.setProperty(`--theme-${key}`, value.color);
  });

  renderer.setPreferences({
    spriteFilter: settings.viewport.spriteFilter,
    particleCap: settings.viewport.particleCap,
    particlesWorldSpace: settings.viewport.particlesWorldSpace,
    zoomToCursor: settings.viewport.zoomToCursor,
  });
  viewportPanel.dataset.diagnosticsEnabled = String(settings.diagnostics.enabled);
  viewportPanel.dataset.performanceLogging = String(settings.diagnostics.performanceLogging);

  exportFramingSelect.value = settings.export.png.framing;
  exportScaleSelect.value = String(Math.max(1, Math.min(8, Math.round(settings.export.png.scaleMultiplier))));
  exportFpsInput.value = String(settings.export.png.sequenceFps);
  exportVideoFormat.value = settings.export.video.format;
  exportVideoMode.value = settings.export.video.mode;
  exportVideoBitrate.value = String(settings.export.video.bitrateMbps);
  exportVideoAudio.checked = settings.export.video.includeAudio;
  exportBackgroundEnabled.checked = settings.export.png.solidBackground;
  exportBackgroundColor.value = settings.export.png.backgroundColor;
  setExportBusy(exportBusy);

  audioMuted = settings.playback.muted;
  updateAudioVolume(String(Math.round(settings.playback.volume * 100)));
  audioPlayback.setMuted(audioMuted);
  updateAudioMuteControls();
  session.setLoop(settings.playback.loop);
  renderer.setLoop(settings.playback.loop);
  updateLoopControls(settings.playback.loop);

  const revisionValue = settings.conversion.automaticRevisionDetection
    ? 'auto'
    : settings.conversion.manualRevisionMode;
  revisionMode = revisionModeFromValue(revisionValue);
  revisionSelect.value = revisionValue;
  requestAnimationFrame(() => renderer.fitToView());
}

function persistViewerSettings(): void {
  Object.assign(viewerSettings, saveViewerSettings(viewerSettings));
}

const settingsPane = new SettingsPane({
  trigger: settingsButton,
  settings: viewerSettings,
  onPreview: applyViewerSettings,
  onSave: (settings) => {
    Object.assign(viewerSettings, saveViewerSettings(settings));
    applyViewerSettings(viewerSettings);
    setStatus('Settings saved');
  },
  onOpenFiles: openFiles,
  onOpenFolder: openFolder,
  onManageAssets: () => {
    shell.dataset.mobilePanel = 'library';
    shell.classList.remove('controls-panel-hidden');
    controlTabs.activate('files');
    element('#assetRetentionPanel').scrollIntoView({ block: 'nearest' });
    element('#rememberAssetsCheckbox').focus();
  },
});

function toggleShellPanel(className: string): void {
  shell.classList.toggle(className);
  requestAnimationFrame(() => renderer.fitToView());
}

function executeKeybind(event: KeyboardEvent): boolean {
  const keybinds = viewerSettings.keybinds;
  for (const [key, command] of Object.entries({
    select_tool: 'select', move_tool: 'move', pan_tool: 'pan', toggle_rotation_gizmo: 'rotate', toggle_scale_gizmo: 'scale',
    undo: 'undo', redo: 'redo', redo_alt: 'redo',
  })) {
    const shortcut = keybinds[key] ?? '';
    const macUndoAlias = ['undo', 'redo', 'redo_alt'].includes(key) && event.metaKey && !event.ctrlKey
      && keyboardEventMatchesShortcut({ ...event, key: event.key, altKey: event.altKey, shiftKey: event.shiftKey, ctrlKey: true, metaKey: false }, shortcut);
    if (keyboardEventMatchesShortcut(event, shortcut) || macUndoAlias) {
      transformControls.command(command);
      return true;
    }
  }
  if (keyboardEventMatchesShortcut(event, keybinds.toggle_playback ?? '')) {
    togglePlayback();
  } else if (keyboardEventMatchesShortcut(event, keybinds.toggle_loop ?? '')) {
    toggleLoop();
  } else if (keyboardEventMatchesShortcut(event, keybinds.fullscreen ?? '')) {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void document.documentElement.requestFullscreen();
  } else if (keyboardEventMatchesShortcut(event, keybinds.toggle_controls_panel ?? '')) {
    toggleShellPanel('controls-panel-hidden');
  } else if (keyboardEventMatchesShortcut(event, keybinds.toggle_layers_panel ?? '')) {
    toggleShellPanel('layers-panel-hidden');
  } else if (keyboardEventMatchesShortcut(event, keybinds.toggle_timeline_panel ?? '')) {
    toggleShellPanel('timeline-panel-hidden');
  } else if (keyboardEventMatchesShortcut(event, keybinds.toggle_focus_mode ?? '')) {
    toggleShellPanel('focus-mode');
  } else {
    return false;
  }
  return true;
}

applyViewerSettings(viewerSettings);

playButton.addEventListener('click', togglePlayback);
panelPlayButton.addEventListener('click', togglePlayback);
audioButton.addEventListener('click', toggleAudioMuted);
panelAudioButton.addEventListener('click', toggleAudioMuted);
audioVolume.addEventListener('input', () => {
  updateAudioVolume(audioVolume.value);
  viewerSettings.playback.volume = Number(audioVolume.value) / 100;
  persistViewerSettings();
});
panelAudioVolume.addEventListener('input', () => {
  updateAudioVolume(panelAudioVolume.value);
  viewerSettings.playback.volume = Number(panelAudioVolume.value) / 100;
  persistViewerSettings();
});
loopButton.addEventListener('click', toggleLoop);
panelLoopButton.addEventListener('click', toggleLoop);
timeline.addEventListener('input', () => {
  if (exportBusy) return;
  transformControls.cancelGesture();
  animationAudioLoopIndex = 0;
  viewportPanel.dataset.audioLoopIndex = '0';
  audioPlayback.sync(synchronizedAudioTime(Number(timeline.value)), renderer.isPlaying(), true, true);
  renderer.setTime(Number(timeline.value));
});

renderer.onLoop = (completedCycles) => {
  const nextCycle = animationAudioLoopIndex + completedCycles;
  animationAudioLoopIndex = nextCycle % animationAudioTiming.loopMultiplier;
  viewportPanel.dataset.audioLoopIndex = String(animationAudioLoopIndex);
  if (nextCycle >= animationAudioTiming.loopMultiplier) {
    audioPlayback.sync(synchronizedAudioTime(renderer.getTime()), true, false, true);
  }
};

renderer.onPlaybackChange = (playing) => {
  session.setPlaying(playing);
  playButtonImage.src = playing ? pauseIcon : playIcon;
  playButton.title = playing ? 'Pause' : 'Play';
  playButton.setAttribute('aria-label', playButton.title);
  panelPlayButton.innerHTML = `<i data-lucide="${playing ? 'pause' : 'play'}"></i><span>${playing ? 'Pause' : 'Play'}</span>`;
  hydrateIcons();
  syncAudioPlayback(renderer.getTime(), playing);
};
renderer.onTimeChange = (time, duration) => {
  session.setTime(time);
  timeline.max = String(Math.max(0.001, duration));
  timeline.value = String(time);
  timeValue.textContent = `${formatTime(time)} / ${formatTime(duration)}`;
  panelTimeValue.textContent = timeValue.textContent;
  syncAudioPlayback(time, renderer.isPlaying());
};

let lastPointerX = 0;
timeline.addEventListener('pointerdown', (event) => {
  lastPointerX = event.clientX;
  document.documentElement.style.setProperty('--timeline-thumb', `url("${timelineThumbActive}")`);
  timeline.setPointerCapture(event.pointerId);
});
timeline.addEventListener('pointermove', (event) => {
  if (!timeline.hasPointerCapture(event.pointerId)) return;
  const icon = event.clientX < lastPointerX ? timelineThumbLeft : timelineThumbRight;
  document.documentElement.style.setProperty('--timeline-thumb', `url("${icon}")`);
  lastPointerX = event.clientX;
});
const releaseTimeline = (): void => {
  document.documentElement.style.setProperty('--timeline-thumb', `url("${timelineThumb}")`);
};
timeline.addEventListener('pointerup', releaseTimeline);
timeline.addEventListener('pointercancel', releaseTimeline);

for (const eventName of ['dragenter', 'dragover'] as const) {
  viewportPanel.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropIndicator.classList.add('visible');
  });
}
for (const eventName of ['dragleave', 'drop'] as const) {
  viewportPanel.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropIndicator.classList.remove('visible');
  });
}
viewportPanel.addEventListener('drop', (event) => {
  const files = event.dataTransfer?.files;
  if (files?.length) void importFiles(files);
});

document.querySelectorAll<HTMLButtonElement>('.mobile-panel-tabs [data-panel]').forEach((button) => {
  button.addEventListener('click', () => {
    const panel = button.dataset.panel ?? 'library';
    shell.dataset.mobilePanel = panel;
    document.querySelectorAll('.mobile-panel-tabs button').forEach((candidate) => {
      candidate.classList.toggle('active', candidate === button);
    });
  });
});

const viewerReady = renderer.initialize().then(() => assetRetention.start());
await viewerReady;

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  const currentCacheName = 'msm-aniviewer-web-v5';
  const registerServiceWorker = (): void => {
    void navigator.serviceWorker
      .register(`${import.meta.env.BASE_URL}sw.js`, {
        scope: import.meta.env.BASE_URL,
        updateViaCache: 'none',
      })
      .then(async (registration) => {
        await registration.update();
        const cacheNames = await caches.keys();
        await Promise.all(cacheNames
          .filter((name) => name.startsWith('msm-aniviewer-web-') && name !== currentCacheName)
          .map((name) => caches.delete(name)));
      });
  };
  if (document.readyState === 'complete') registerServiceWorker();
  else window.addEventListener('load', registerServiceWorker, { once: true });
}
