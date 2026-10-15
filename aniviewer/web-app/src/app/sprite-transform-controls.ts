import { DEFAULT_LAYER_EDIT, signedAngleDelta, type LayerTransformEdit, type ViewPoint } from '../core/layer-transform';
import type { AnimationRenderer } from '../renderer/animation-renderer';
import type { ViewerSession } from './viewer-session';
import { EditConstraintControls } from './edit-constraint-controls';
import { IKControls } from './ik-controls';
import { ToolModePicker } from './tool-mode-picker';
import { MANIPULATION_MODES, modeLabel, type ManipulationMode } from '../core/manipulation-modes';
import { resolveEditTargets } from '../core/edit-constraints';
import './sprite-transform-controls.css';

type Tool = ManipulationMode;
type ScaleAxis = 'both' | 'x' | 'y';
interface Gesture {
  tool: Tool | 'ik' | 'place';
  ikId?: string;
  ikTarget?: ViewPoint;
  start: ViewPoint;
  camera: ReturnType<AnimationRenderer['getCamera']>;
  before: Map<number, LayerTransformEdit>;
  pivot?: ViewPoint;
  angle: number;
  rotation: number;
  active: boolean;
  radial: boolean;
}

const iconPaths: Record<string, string> = {
  select: 'M5 3l14 10-7 1-3 7z',
  close: 'M6 6l12 12M18 6L6 18',
  move: 'M12 3v18M3 12h18M8 7l4-4 4 4M8 17l4 4 4-4M7 8l-4 4 4 4M17 8l4 4-4 4',
  rotate: 'M5 9a8 8 0 1 1-1 7M5 3v6h6',
  scale: 'M4 14v6h6M14 4h6v6M4 20l7-7M20 4l-7 7',
  pan: 'M5 12V9a2 2 0 0 1 4 0V5a2 2 0 0 1 4 0v4a2 2 0 0 1 4 0v2a2 2 0 0 1 4 0v5c0 4-3 6-7 6h-2L3 14q-2-4 2-2z',
  left: 'M20 12H4m6-6-6 6 6 6', right: 'M4 12h16m-6-6 6 6-6 6',
  up: 'M12 20V4m-6 6 6-6 6 6', down: 'M12 4v16m-6-6 6 6 6-6',
};
function icon(name: string): string {
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${iconPaths[name]}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

export class SpriteTransformControls {
  private tool: Tool = 'select';
  private multi = false;
  private snap = false;
  private axis: ScaleAxis = 'both';
  private step = 1;
  private busy = false;
  private readonly dock = document.createElement('div');
  private readonly overlay = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  private readonly pointers = new Map<number, ViewPoint>();
  private gesture?: Gesture;
  private pinch?: { center: ViewPoint; distance: number; camera: ReturnType<AnimationRenderer['getCamera']> };
  private cameraOnly = false;
  private readonly constraintControls: EditConstraintControls;
  private readonly ikControls: IKControls;
  private readonly modePicker: ToolModePicker;
  private touchStart?: { event: PointerEvent; point: ViewPoint };

  constructor(
    private readonly host: HTMLElement,
    private readonly inspector: HTMLElement,
    private readonly session: ViewerSession,
    private readonly renderer: AnimationRenderer,
  ) {
    host.tabIndex = 0;
    host.setAttribute('aria-label', 'Animation viewport. Use the Tools button to choose a manipulation mode. Two fingers pan and zoom. Tool selection is also available in Sprites.');
    this.overlay.classList.add('transform-overlay');
    this.overlay.setAttribute('aria-hidden', 'true');
    this.overlay.setAttribute('preserveAspectRatio', 'none');
    host.append(this.overlay);
    this.dock.className = 'transform-dock';
    this.dock.id = 'transformDock';
    this.dock.innerHTML = `
      <div class="transform-options" role="group" aria-label="Sprite tools">
        <button type="button" class="button tool-launcher" id="toolModeButton" data-action="tools"><span>Tools</span><span data-current-tool>Select</span></button>
        <button type="button" class="button" data-action="undo" title="Undo transform (Ctrl/Cmd+Z)">Undo</button>
        <button type="button" class="button" data-action="redo" title="Redo transform (Ctrl/Cmd+Shift+Z)">Redo</button>
        <button type="button" class="button" data-action="multi" aria-pressed="false" title="Add/remove layers by tapping them">Multi</button>
        <button type="button" class="button" data-action="snap" aria-pressed="false" title="Snap drag amounts to the move step, 15 degrees, or 5%">Snap</button>
        <button type="button" class="button" data-action="clear" title="Clear selection">Clear</button>
      </div>
      <div class="transform-steps" data-step-panel="move" hidden>
        ${['left', 'up', 'down', 'right'].map((direction) => `<button type="button" class="button" data-action="${direction}" aria-label="Move ${direction}">${icon(direction)}</button>`).join('')}
        <select aria-label="Move step" id="transformMoveStep"><option value=".1">0.1 px</option><option value="1" selected>1 px</option><option value="10">10 px</option></select>
      </div>
      <div class="transform-steps" data-step-panel="rotate" hidden>
        <button type="button" class="button" data-action="rotate-left">-15 deg</button>
        <button type="button" class="button" data-action="rotate-right">+15 deg</button>
        <button type="button" class="button" data-action="rotate-quarter">+90 deg</button>
      </div>
      <div class="transform-steps" data-step-panel="scale" hidden>
        <button type="button" class="button" data-action="shrink">-5%</button>
        <button type="button" class="button" data-action="grow">+5%</button>
        <select id="transformScaleAxis" aria-label="Scale axes"><option value="both">Both axes</option><option value="x">X only</option><option value="y">Y only</option></select>
      </div>`;
    host.append(this.dock);
    inspector.innerHTML = `
      <fieldset class="control-group transform-inspector">
        <legend>Selected Sprites</legend>
        <label class="touch-tool-fallback">Manipulation mode
          <select id="touchToolSelect" aria-label="Manipulation mode">${MANIPULATION_MODES.map((mode) => `<option value="${mode}">${modeLabel(mode)}</option>`).join('')}</select>
          <span class="control-hint">Or open Tools in the bottom viewport toolbar. Tap a mode to choose it and return to editing.</span>
        </label>
        <p id="transformSelection" role="status">Select a sprite in the viewport or Layers panel.</p>
        <div class="transform-fields">
          ${[['offsetX', 'X offset (px)', 0], ['offsetY', 'Y offset (px)', 0], ['rotation', 'Rotation (deg)', 0], ['scaleX', 'Scale X (%)', 100], ['scaleY', 'Scale Y (%)', 100]].map(([key, label, value]) => `<label><span>${label}</span><input type="number" data-transform-field="${key}" aria-label="${label}" step="any" value="${value}" /></label>`).join('')}
        </div>
        <div class="control-command-grid">
          <button type="button" class="button" data-action="flip-x">Flip X</button>
          <button type="button" class="button" data-action="flip-y">Flip Y</button>
          <button type="button" class="button" data-action="reset">Reset selected</button>
        </div>
        <p class="control-hint">Animation-wide, session-only edits. Original keyframes stay intact. Changing animation, appearance, files or refreshing clears edits and links.</p>
        <details class="transform-help"><summary>Controls, linked edits and reset</summary>
        <p class="control-hint">Edits apply across this animation without changing its keyframes. Without edit links, other layers do not inherit your edits. Linked groups share the driver's anchor and use world-axis scaling.</p>
        <p class="control-hint">Numbers show your authored inputs, not a decomposition of the final pose. Reset removes edits authored by the selected driver, including their linked effects. Undo restores the previous state exactly.</p>
        <p class="control-hint">Select: tap/click a sprite. Multi: add or remove selections. Move: drag the sprite. Rotate/Scale: drag its handle or drag the sprite horizontally. Two fingers always pan/zoom the camera. Middle mouse or Pan drags the camera. Arrow keys nudge; Shift nudges 10x; Esc cancels a drag.</p>
        <p class="control-hint">Edits and links are session-only and reset when changing animation, appearance, or files. PNG/GIF/video exports include edits. Keyframe recording and editable project saves are not ported yet.</p>
        </details>
      </fieldset>`;
    const activate = (button: HTMLButtonElement) => {
      if (this.busy || button.disabled) return;
      if (button.dataset.action) this.action(button.dataset.action);
    };
    let touchButton: { id: number; button: HTMLButtonElement; x: number; y: number } | undefined;
    let consumedClick: { button: HTMLButtonElement; until: number } | undefined;
    this.dock.addEventListener('keydown', (event) => {
      // Focused buttons keep native activation instead of the global playback shortcut.
      if ((event.key === ' ' || event.key === 'Enter') && (event.target as Element).closest('button')) event.stopPropagation();
    });
    this.dock.addEventListener('pointerdown', (event) => {
      // A fresh mouse/pen/touch press is never a previous touch's follow-up click.
      consumedClick = undefined;
      const button = (event.target as Element).closest<HTMLButtonElement>('button');
      if (event.pointerType === 'touch' && button && !button.disabled) touchButton = { id: event.pointerId, button, x: event.clientX, y: event.clientY };
    });
    this.dock.addEventListener('pointermove', (event) => {
      if (touchButton?.id === event.pointerId && Math.hypot(event.clientX - touchButton.x, event.clientY - touchButton.y) > 10) touchButton = undefined;
    });
    this.dock.addEventListener('pointercancel', () => { touchButton = undefined; });
    this.dock.addEventListener('pointerup', (event) => {
      const touch = touchButton;
      touchButton = undefined;
      if (!touch || touch.id !== event.pointerId || !touch.button.contains(event.target as Node)) return;
      // Mobile browsers may suppress a compatibility click immediately after a viewport drag.
      // Activate touch commands on release; consume the optional follow-up click only once.
      consumedClick = { button: touch.button, until: performance.now() + 800 };
      event.preventDefault();
      activate(touch.button);
    });
    for (const root of [this.dock, inspector]) root.addEventListener('click', (event) => {
      const button = (event.target as Element).closest<HTMLButtonElement>('button');
      if (!button) return;
      // WebKit can label the synthesized touch click as pointerType="mouse".
      if (event.detail > 0 && consumedClick?.button === button && performance.now() < consumedClick.until) { consumedClick = undefined; return; }
      activate(button);
    });
    this.dock.querySelector<HTMLSelectElement>('#transformMoveStep')!.addEventListener('change', (event) => {
      this.step = Number((event.target as HTMLSelectElement).value);
    });
    this.dock.querySelector<HTMLSelectElement>('#transformScaleAxis')!.addEventListener('change', (event) => {
      this.axis = (event.target as HTMLSelectElement).value as ScaleAxis;
    });
    inspector.querySelectorAll<HTMLInputElement>('[data-transform-field]').forEach((input) => {
      input.addEventListener('change', () => {
        if (this.busy) return;
        const key = input.dataset.transformField as keyof LayerTransformEdit;
        const number = input.valueAsNumber;
        if (Number.isFinite(number)) {
          const value = key.startsWith('scale') ? number / 100 : number;
          this.editSelected((edit) => ({ ...edit, [key]: value }), `Set ${key}`);
        }
        this.refreshFields(true);
      });
    });
    this.modePicker = new ToolModePicker(this.dock.querySelector<HTMLButtonElement>('#toolModeButton')!, icon,
      () => this.cancelGesture(), (mode) => this.setTool(mode, true));
    inspector.querySelector<HTMLSelectElement>('#touchToolSelect')!.addEventListener('change', (event) => {
      if (!this.busy) this.setTool((event.target as HTMLSelectElement).value as Tool);
    });
    host.addEventListener('pointerdown', (event) => this.pointerDown(event));
    host.addEventListener('pointermove', (event) => this.pointerMove(event));
    host.addEventListener('pointerup', (event) => this.pointerEnd(event, false));
    host.addEventListener('pointercancel', (event) => this.pointerEnd(event, true));
    host.addEventListener('lostpointercapture', (event) => {
      if (this.pointers.has(event.pointerId)) this.cancelGesture();
    });
    host.addEventListener('contextmenu', (event) => { if (!(event.target as Element).closest('.transform-dock')) event.preventDefault(); });
    host.addEventListener('wheel', () => { if (this.pointers.size || this.gesture) this.cancelGesture(); }, { passive: true });
    window.addEventListener('blur', () => this.cancelGesture());
    document.addEventListener('visibilitychange', () => { if (document.hidden) this.cancelGesture(); });
    new ResizeObserver(() => this.renderer.setEditorInset(this.dock.hidden ? 0 : this.dock.offsetHeight)).observe(this.dock);
    let lastSize = '';
    new ResizeObserver(() => {
      const size = `${host.clientWidth}:${host.clientHeight}`;
      if (lastSize && size !== lastSize) this.cancelGesture();
      lastSize = size;
    }).observe(host);
    window.visualViewport?.addEventListener('resize', () => this.cancelGesture());
    renderer.onViewChange = () => this.drawOverlay();
    session.subscribe((snapshot, change) => {
      if (change === 'time' || change === 'loop') return;
      if (change === 'document' || change === 'track') this.cancelGesture();
      if (['selection', 'visibility', 'constraints'].includes(change) && (this.gesture || this.touchStart || this.modePicker.isOpen)) this.cancelGesture();
      if (change === 'playback' && snapshot.playing) this.cancelGesture();
      if (['edit', 'ik', 'history', 'document', 'track'].includes(change)) renderer.setTransformOperations(snapshot.transformOperations, snapshot.ikRigs);
      this.refresh();
    });
    this.constraintControls = new EditConstraintControls(inspector, session, () => this.cancelGesture());
    this.ikControls = new IKControls(inspector, session, renderer, () => this.cancelGesture(), () => this.drawOverlay());
    this.refresh();
  }

  setBusy(busy: boolean): void {
    if (busy) this.cancelGesture();
    this.busy = busy;
    this.dock.inert = busy;
    this.inspector.inert = busy;
    this.constraintControls.setBusy(busy);
    this.ikControls.setBusy(busy);
    this.refresh();
  }

  get multiSelect(): boolean { return this.multi; }

  command(command: string): void {
    if (this.busy || !this.session.track) return;
    if (['select', 'move', 'rotate', 'scale', 'pan'].includes(command)) this.setTool(command as Tool);
    else this.action(command);
  }

  private selected(): number[] {
    return this.session.transformLayerIds(this.session.snapshot().selectedLayerIds.filter((id) => this.session.isLayerVisible(id)));
  }

  private point(event: PointerEvent): ViewPoint {
    // Account for application CSS zoom as well as camera zoom and device pixel ratio.
    return this.renderer.clientToView(event.clientX, event.clientY);
  }

  private setTool(tool: Tool, fromPicker = false): void {
    this.cancelGesture(!fromPicker);
    this.tool = tool;
    this.refresh();
  }

  private action(action: string): void {
    if (action === 'tools') this.modePicker.toggle();
    else if (action === 'undo') { this.cancelGesture(); this.session.undo(); }
    else if (action === 'redo') { this.cancelGesture(); this.session.redo(); }
    else if (action === 'clear') { this.cancelGesture(); this.session.clearSelection(); }
    else if (action === 'multi') this.multi = !this.multi;
    else if (action === 'snap') this.snap = !this.snap;
    else if (action === 'reset') { this.cancelGesture(); this.session.resetLayerEdits(this.selected()); }
    else this.editSelected((edit) => {
      const next = { ...edit };
      if (action === 'left') next.offsetX -= this.step;
      if (action === 'right') next.offsetX += this.step;
      if (action === 'up') next.offsetY -= this.step;
      if (action === 'down') next.offsetY += this.step;
      if (action === 'rotate-left') next.rotation -= 15;
      if (action === 'rotate-right') next.rotation += 15;
      if (action === 'rotate-quarter') next.rotation += 90;
      if (action === 'flip-x') next.scaleX *= -1;
      if (action === 'flip-y') next.scaleY *= -1;
      if (action === 'shrink' || action === 'grow') {
        const amount = action === 'grow' ? .05 : -.05;
        if (this.axis !== 'y') next.scaleX = Math.sign(next.scaleX) * Math.max(.01, Math.abs(next.scaleX) + amount);
        if (this.axis !== 'x') next.scaleY = Math.sign(next.scaleY) * Math.max(.01, Math.abs(next.scaleY) + amount);
      }
      return next;
    }, action.replaceAll('-', ' '));
    this.refresh();
  }

  private editSelected(change: (edit: LayerTransformEdit) => LayerTransformEdit, label: string): void {
    if (this.busy || !this.selected().length) return;
    this.cancelGesture();
    this.renderer.pause();
    this.session.applyLayerEdits(new Map(this.selected().map((id) => [id, change(this.session.layerEdit(id))])), label);
  }

  private pointerDown(event: PointerEvent): void {
    if (this.busy || !this.session.track || (event.target as Element).closest('.transform-dock') || event.button > 1) return;
    event.preventDefault();
    this.host.focus({ preventScroll: true });
    this.host.setPointerCapture(event.pointerId);
    const point = this.point(event);
    this.pointers.set(event.pointerId, point);
    if (this.pointers.size >= 2) {
      this.touchStart = undefined;
      this.session.cancelTransform();
      this.ikControls.cancelPlacement();
      this.gesture = undefined;
      this.cameraOnly = true;
      const [a, b] = [...this.pointers.values()] as [ViewPoint, ViewPoint];
      this.pinch = { center: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, distance: Math.max(1, Math.hypot(b.x - a.x, b.y - a.y)), camera: this.renderer.getCamera() };
      return;
    }
    if (this.cameraOnly) return;
    if (event.pointerType === 'touch' && event.button === 0 && !this.ikControls.placing) {
      // Defer selection until release or a deliberate drag, tolerating touch jitter.
      this.touchStart = { event, point };
      return;
    }
    this.beginViewportGesture(event, point);
  }

  private beginViewportGesture(event: PointerEvent, point: ViewPoint): void {
    const ikId = (event.target as Element).closest<SVGElement>('[data-ik-target]')?.dataset.ikTarget;
    const rig = ikId && this.session.snapshot().ikRigs.find((entry) => entry.id === ikId && entry.enabled);
    if (event.button === 0 && (this.ikControls.placing || rig)) {
      this.renderer.pause();
      this.gesture = {
        tool: this.ikControls.placing ? 'place' : 'ik', ikId: rig ? rig.id : undefined, ikTarget: rig ? { ...rig.target } : undefined,
        start: point, camera: this.renderer.getCamera(), before: new Map(), angle: 0, rotation: 0, active: false, radial: false,
      };
      return;
    }
    const handle = (event.target as Element).closest<SVGElement>('[data-transform-handle]')?.dataset.transformHandle;
    const tool = event.button === 1 ? 'pan' : (handle as Tool | undefined) ?? this.tool;
    const hits = this.renderer.hitTestLayers(point, event.pointerType === 'touch' ? 10 : 0);
    const additive = this.multi || event.shiftKey || event.metaKey || event.ctrlKey;
    if (tool !== 'pan' && !handle) {
      if (hits.length) {
        // Alt-click cycles through overlapping sprite bounds; the Layers panel is the exact selector.
        const previous = hits.findIndex((id) => this.session.isLayerSelected(id));
        const id = event.altKey ? hits[(previous + 1) % hits.length]! : hits[0]!;
        if (tool === 'select' || !this.session.isLayerSelected(id) || additive) this.session.selectLayer(id, additive);
      } else if (tool === 'select' && !additive) this.session.clearSelection();
    }
    const before = new Map(this.selected().map((id) => [id, this.session.layerEdit(id)]));
    if (tool !== 'pan' && (tool === 'select' || !before.size || (!hits.length && !handle) || additive)) return;
    const pivot = this.renderer.layerViewGeometry([...before.keys()][0]!)?.pivot;
    this.gesture = {
      tool, start: point, camera: this.renderer.getCamera(), before, pivot,
      angle: pivot ? Math.atan2(point.y - pivot.y, point.x - pivot.x) * 180 / Math.PI : 0,
      rotation: 0, active: false, radial: Boolean(handle),
    };
  }

  private pointerMove(event: PointerEvent): void {
    if (!this.pointers.has(event.pointerId) || this.busy) return;
    event.preventDefault();
    const point = this.point(event);
    this.pointers.set(event.pointerId, point);
    if (this.cameraOnly) {
      if (!this.pinch || this.pointers.size < 2) return;
      const [a, b] = [...this.pointers.values()] as [ViewPoint, ViewPoint];
      const center = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const { camera, distance, center: start } = this.pinch;
      const zoom = Math.max(.05, Math.min(8, camera.zoom * Math.hypot(b.x - a.x, b.y - a.y) / distance));
      this.renderer.setCamera(center.x - (start.x - camera.x) * zoom / camera.zoom, center.y - (start.y - camera.y) * zoom / camera.zoom, zoom);
      return;
    }
    if (this.touchStart) {
      if (Math.hypot(event.clientX - this.touchStart.event.clientX, event.clientY - this.touchStart.event.clientY) <= 8) return;
      const start = this.touchStart; this.touchStart = undefined;
      if (start) this.beginViewportGesture(start.event, start.point);
    }
    const gesture = this.gesture;
    if (!gesture) return;
    const dx = point.x - gesture.start.x, dy = point.y - gesture.start.y;
    if (gesture.tool === 'place') return;
    if (!gesture.active) {
      if (Math.hypot(dx, dy) < 3) return;
      gesture.active = true;
      if (gesture.tool !== 'pan') {
        this.renderer.pause();
        if (gesture.tool === 'ik') this.session.beginIKTarget(gesture.ikId!);
        else this.session.beginTransform([...gesture.before.keys()]);
      }
    }
    if (gesture.tool === 'pan') {
      this.renderer.setCamera(gesture.camera.x + dx, gesture.camera.y + dy, gesture.camera.zoom);
      return;
    }
    const snap = this.snap || event.shiftKey;
    const quantize = (value: number, step: number) => snap ? Math.round(value / step) * step : value;
    if (gesture.tool === 'ik') {
      this.session.previewIKTarget(gesture.ikTarget!.x + quantize(dx / gesture.camera.zoom, this.step), gesture.ikTarget!.y + quantize(dy / gesture.camera.zoom, this.step));
      return;
    }
    if (gesture.radial && gesture.pivot && gesture.tool === 'rotate') {
      const angle = Math.atan2(point.y - gesture.pivot.y, point.x - gesture.pivot.x) * 180 / Math.PI;
      gesture.rotation += signedAngleDelta(gesture.angle, angle);
      gesture.angle = angle;
    }
    const scale = Math.exp(Math.max(-9, Math.min(9, dx * .01)));
    const edits = new Map<number, LayerTransformEdit>();
    for (const [id, initial] of gesture.before) {
      const edit = { ...initial };
      if (gesture.tool === 'move') {
        edit.offsetX += quantize(dx / gesture.camera.zoom, this.step);
        edit.offsetY += quantize(dy / gesture.camera.zoom, this.step);
      } else if (gesture.tool === 'rotate') edit.rotation += quantize(gesture.radial ? gesture.rotation : dx * .5, 15);
      else if (gesture.tool === 'scale') {
        if (this.axis !== 'y') edit.scaleX = Math.sign(initial.scaleX) * Math.max(.01, quantize(Math.abs(initial.scaleX) * scale, .05));
        if (this.axis !== 'x') edit.scaleY = Math.sign(initial.scaleY) * Math.max(.01, quantize(Math.abs(initial.scaleY) * scale, .05));
      }
      edits.set(id, edit);
    }
    this.session.previewTransform(edits);
  }

  private pointerEnd(event: PointerEvent, cancelled: boolean): void {
    if (!this.pointers.has(event.pointerId)) return;
    if (cancelled) { this.cancelGesture(); return; }
    if (this.touchStart) {
      const start = this.touchStart;
      const tap = Math.hypot(event.clientX - start.event.clientX, event.clientY - start.event.clientY) <= 8;
      this.touchStart = undefined;
      this.beginViewportGesture(start.event, start.point);
      if (!tap) this.pointerMove(event);
    } else this.pointerMove(event);
    this.pointers.delete(event.pointerId);
    if (this.host.hasPointerCapture(event.pointerId)) this.host.releasePointerCapture(event.pointerId);
    if (!this.cameraOnly && this.gesture?.tool === 'place') {
      const point = this.point(event);
      if (Math.hypot(point.x - this.gesture.start.x, point.y - this.gesture.start.y) < 8) this.ikControls.place(point);
    } else if (!this.cameraOnly && this.gesture?.active && this.gesture.tool !== 'pan') {
      if (this.gesture.tool === 'ik') this.session.commitIKTarget();
      else this.session.commitTransform(this.gesture.tool);
    }
    this.gesture = undefined;
    if (!this.pointers.size) { this.cameraOnly = false; this.pinch = undefined; }
    this.refresh();
  }

  cancelGesture(closeTools = true): void {
    this.touchStart = undefined;
    if (closeTools) this.modePicker?.close(false);
    this.ikControls?.cancelPlacement();
    this.gesture = undefined;
    this.pinch = undefined;
    this.cameraOnly = false;
    const ids = [...this.pointers.keys()];
    this.pointers.clear();
    this.session.cancelTransform();
    for (const id of ids) if (this.host.hasPointerCapture(id)) this.host.releasePointerCapture(id);
  }

  handleKey(event: KeyboardEvent): boolean {
    if (this.busy || !this.session.track || event.altKey) return false;
    if (event.key === 'Escape') {
      if (this.gesture || this.pointers.size || this.ikControls.placing) this.cancelGesture();
      else this.session.clearSelection();
      return true;
    }
    if (event.ctrlKey || event.metaKey) return false;
    const arrow = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }[event.key];
    if (arrow && this.selected().length) {
      const step = this.step;
      if (event.shiftKey) this.step *= 10;
      this.action(arrow); this.step = step;
      return true;
    }
    return false;
  }

  private refreshFields(force = false): void {
    const selected = this.selected();
    this.inspector.querySelectorAll<HTMLInputElement>('[data-transform-field]').forEach((input) => {
      input.disabled = this.busy || !selected.length;
      if (!force && document.activeElement === input) return;
      const key = input.dataset.transformField as keyof LayerTransformEdit;
      const values = selected.map((id) => this.session.layerEdit(id)[key]);
      const mixed = values.some((value) => value !== values[0]);
      input.value = mixed ? '' : String(Number(((values[0] ?? DEFAULT_LAYER_EDIT[key]) * (key.startsWith('scale') ? 100 : 1)).toFixed(4)));
      input.placeholder = mixed ? 'Mixed' : '';
    });
  }

  private refresh(): void {
    const selected = this.selected();
    this.dock.hidden = !this.session.track;
    this.host.dataset.transformTool = this.tool;
    this.modePicker.setMode(this.tool, Boolean(this.session.track) && !this.busy);
    const touchTool = this.inspector.querySelector<HTMLSelectElement>('#touchToolSelect')!;
    touchTool.value = this.tool; touchTool.disabled = this.busy || !this.session.track;
    this.host.dataset.selectedLayers = selected.join(',');
    const names = selected.map((id) => this.session.track?.layers.find((layer) => layer.id === id)?.name ?? String(id));
    const picked = this.session.snapshot().selectedLayerIds;
    const redirected = picked.some((id) => !selected.includes(id));
    this.inspector.querySelector('#transformSelection')!.textContent = names.length ? `${redirected ? 'Editing linked driver: ' : ''}${names.join(', ')}` : 'Select a sprite in the viewport or Layers panel.';
    this.dock.querySelector('[data-action="multi"]')!.setAttribute('aria-pressed', String(this.multi));
    this.dock.querySelector('[data-action="snap"]')!.setAttribute('aria-pressed', String(this.snap));
    this.dock.querySelector<HTMLButtonElement>('[data-action="undo"]')!.disabled = !this.session.history.canUndo || this.session.transforming;
    this.dock.querySelector<HTMLButtonElement>('[data-action="redo"]')!.disabled = !this.session.history.canRedo || this.session.transforming;
    this.dock.querySelector<HTMLButtonElement>('[data-action="clear"]')!.disabled = !this.session.snapshot().selectedLayerIds.length;
    this.dock.querySelectorAll<HTMLElement>('[data-step-panel]').forEach((panel) => { panel.hidden = !selected.length || panel.dataset.stepPanel !== this.tool; });
    this.inspector.querySelectorAll<HTMLButtonElement>('.transform-inspector button').forEach((button) => { button.disabled = !selected.length || this.busy; });
    this.refreshFields();
    this.drawOverlay();
  }

  private drawOverlay(): void {
    if (this.busy) { this.overlay.replaceChildren(); return; }
    this.overlay.setAttribute('viewBox', `0 0 ${this.host.clientWidth} ${this.host.clientHeight}`);
    const shapes = this.selected().map((id) => this.renderer.layerViewGeometry(id)).filter((value) => Boolean(value));
    let index = 0;
    const svg = (tag: string, attributes: Record<string, string>) => {
      let node = this.overlay.children[index] as SVGElement | undefined;
      if (node?.localName !== tag) {
        const replacement = document.createElementNS('http://www.w3.org/2000/svg', tag);
        if (node) node.replaceWith(replacement);
        else this.overlay.append(replacement);
        node = replacement;
      }
      for (const attribute of [...node.attributes]) if (!(attribute.name in attributes)) node.removeAttribute(attribute.name);
      for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
      index++;
      return node;
    };
    const finish = () => { while (this.overlay.children.length > index) this.overlay.lastElementChild!.remove(); };
    const selected = this.selected(), rules = this.session.snapshot().constraints;
    const followers = new Set(selected.flatMap((id) => resolveEditTargets(id, rules).map((target) => target.id)));
    for (const id of followers) {
      if (selected.includes(id)) continue;
      const shape = this.renderer.layerViewGeometry(id);
      if (shape) svg('polygon', { points: shape.corners.map((p) => `${p.x},${p.y}`).join(' '), class: 'transform-outline transform-linked-outline', 'data-linked-outline': String(id) });
    }
    for (const shape of shapes) {
      if (!shape) continue;
      svg('polygon', { points: shape.corners.map((p) => `${p.x},${p.y}`).join(' '), class: 'transform-outline', 'data-selection-outline': String(shape.id) });
      svg('circle', { cx: String(shape.pivot.x), cy: String(shape.pivot.y), r: '4', class: 'transform-pivot' });
    }
    this.ikControls?.draw(svg);
    const shape = shapes[0];
    if (!shape || !['rotate', 'scale'].includes(this.tool) || this.session.playing) { finish(); return; }
    const { pivot } = shape;
    const cornerX = Math.max(...shape.corners.map((p) => p.x));
    const cornerY = Math.min(...shape.corners.map((p) => p.y));
    const x = Math.max(24, Math.min(this.host.clientWidth - 24, this.tool === 'rotate' ? pivot.x : cornerX + 20));
    const y = Math.max(24, Math.min(this.host.clientHeight - this.dock.offsetHeight - 24, cornerY - 30));
    svg('line', { x1: String(pivot.x), y1: String(pivot.y), x2: String(x), y2: String(y), class: 'transform-guide' });
    svg('circle', { cx: String(x), cy: String(y), r: '22', class: 'transform-handle-hit', 'data-transform-handle': this.tool });
    svg(this.tool === 'rotate' ? 'circle' : 'rect', this.tool === 'rotate'
      ? { cx: String(x), cy: String(y), r: '8', class: 'transform-handle' }
      : { x: String(x - 7), y: String(y - 7), width: '14', height: '14', class: 'transform-handle' });
    finish();
  }
}
