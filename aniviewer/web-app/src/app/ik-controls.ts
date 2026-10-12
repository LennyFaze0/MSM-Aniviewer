import { sourceIKBend, type IKBone, type IKRig } from '../core/ik-constraints';
import type { ViewPoint } from '../core/layer-transform';
import type { AnimationRenderer } from '../renderer/animation-renderer';
import type { ViewerSession } from './viewer-session';
import './ik-controls.css';

type Joint = 'root' | 'bend' | 'tip';
type DrawSVG = (tag: string, attributes: Record<string, string>) => SVGElement;

export class IKControls {
  private readonly root = document.createElement('fieldset');
  private activeId = '';
  private nextId = 1;
  private pending?: Joint;
  private joints: { root?: ViewPoint; bend?: ViewPoint; lowerStart?: ViewPoint; tip?: ViewPoint } = {};
  private busy = false;

  constructor(host: HTMLElement, private readonly session: ViewerSession, private readonly renderer: AnimationRenderer,
    private readonly cancelGesture: () => void, private readonly redraw: () => void) {
    this.root.id = 'ikControls'; this.root.className = 'control-group ik-controls';
    this.root.innerHTML = `
      <legend>IK / Limb Bending</legend>
      <p class="control-hint">Drag an endpoint to aim one segment or bend a two-segment limb. Place joints explicitly; sprite hierarchies do not always describe bones.</p>
      <details id="ikSetup"><summary>Set up a new IK chain</summary>
        <label class="link-field">Chain type<select id="ikType"><option value="2">Two segments - bend a limb</option><option value="1">One segment - aim</option></select></label>
        <label class="link-field">Root / upper segment<select id="ikUpper"></select></label>
        <label class="link-field" id="ikLowerField">Lower segment<select id="ikLower"></select></label>
        <p class="control-hint">Pause at a clear pose. Press each placement button, then tap/click that joint in the viewport. For two segments, the bend point belongs to both pieces.</p>
        <div class="control-command-grid">
          <button type="button" class="button" data-ik-place="root">Place root</button>
          <button type="button" class="button" data-ik-place="bend">Place bend</button>
          <button type="button" class="button" data-ik-place="tip">Place tip</button>
          <button type="button" class="button" data-ik-command="cancel">Cancel placement</button>
        </div>
        <details><summary>Other sprites that follow each bone (optional)</summary>
          <p class="control-hint">Choose explicitly, including hidden sprites. No descendants or edit links are added automatically. Each sprite can belong to only one IK bone.</p>
          <div id="ikFollowers"></div>
        </details>
        <button type="button" class="button" data-ik-command="create">Create IK chain</button>
      </details>
      <label class="link-field">Active chain<select id="ikActive" aria-label="Active IK chain"></select></label>
      <div id="ikSettings" hidden>
        <label class="ik-enable"><input id="ikEnabled" type="checkbox"> Enable IK</label>
        <label class="link-field">Target behavior<select id="ikSpace"><option value="offset">Follow animation + offset</option><option value="world">Pin in world space</option></select></label>
        <label class="link-field">Bend direction<select id="ikBend"><option value="source">Follow original bend</option><option value="positive">Bend side A</option><option value="negative">Bend side B</option></select></label>
        <div class="transform-fields">
          <label>Target X (px)<input type="number" id="ikX" step="any"></label>
          <label>Target Y (px)<input type="number" id="ikY" step="any"></label>
        </div>
        <div class="control-command-grid">
          <button type="button" class="button" data-ik-nudge="-1,0">Target left</button><button type="button" class="button" data-ik-nudge="1,0">Target right</button>
          <button type="button" class="button" data-ik-nudge="0,-1">Target up</button><button type="button" class="button" data-ik-nudge="0,1">Target down</button>
          <button type="button" class="button" data-ik-command="reset">Reset target</button><button type="button" class="button" data-ik-command="remove">Remove chain</button>
        </div>
        <p id="ikScope" class="control-hint"></p>
      </div>
      <p id="ikStatus" class="control-hint" role="status"></p>
      <p id="ikError" class="control-hint" role="alert"></p>
      <details><summary>How IK interacts with animation</summary>
        <p class="control-hint">Original keyframes keep playing. Normal edits are evaluated first, then IK. Follow animation with a zero offset and the original bend makes no change. Pinning may counteract normal edits to keep the tip in place.</p>
        <p class="control-hint">Off completely removes this chain's solve, revealing the underlying animation and edits. This differs from edit links, whose Off setting only changes future edits. Undo restores IK setup, targets and enabled state.</p>
        <p class="control-hint">No stretching: unreachable targets stop at the closest reach. Explicit side A/B prevents bend flips when the original pose crosses straight. Other sprites keep their original animation. IK is session-only, applies animation-wide, and is included in image/video exports, not new keyframes or project saves.</p>
      </details>`;
    host.append(this.root);
    this.root.addEventListener('click', (event) => {
      const button = (event.target as Element).closest<HTMLButtonElement>('button');
      if (!button || this.busy) return;
      this.error();
      try {
        if (button.dataset.ikPlace) {
          this.cancelGesture(); this.renderer.pause(); this.pending = button.dataset.ikPlace as Joint;
          this.status(`Tap/click the ${this.pending} joint in the viewport. Escape cancels.`); this.redraw();
        } else if (button.dataset.ikNudge) {
          const [x, y] = button.dataset.ikNudge.split(',').map(Number), rig = this.active();
          if (rig) this.update({ ...rig, target: { ...rig.target, x: rig.target.x + x!, y: rig.target.y + y! } }, 'Nudge IK target');
        } else switch (button.dataset.ikCommand) {
          case 'cancel': this.cancelGesture(); break;
          case 'create': this.create(); break;
          case 'remove': this.cancelGesture(); this.session.removeIKRig(this.activeId); break;
          case 'reset': {
            const rig = this.active();
            if (rig) this.update({ ...rig, bend: 'source', target: { space: 'offset', x: 0, y: 0 } }, 'Reset IK target');
          }
        }
      } catch (error) { this.error(error instanceof Error ? error.message : String(error)); }
    });
    for (const id of ['ikType', 'ikUpper', 'ikLower']) this.select(id).addEventListener('change', () => {
      this.cancelGesture(); this.clearJoints(); this.refreshSetup(); this.redraw();
    });
    this.select('ikActive').addEventListener('change', () => {
      this.cancelGesture(); this.activeId = this.select('ikActive').value; this.refresh(); this.redraw();
    });
    for (const id of ['ikEnabled', 'ikSpace', 'ikBend', 'ikX', 'ikY']) this.root.querySelector(`#${id}`)!.addEventListener('change', () => {
      if (this.busy) return;
      const rig = this.active(); if (!rig) return;
      const next = structuredClone(rig);
      if (id === 'ikEnabled') next.enabled = this.input(id).checked;
      if (id === 'ikBend') next.bend = this.select(id).value as IKRig['bend'];
      if (id === 'ikSpace') {
        const pose = this.renderer.ikPose(rig.id); if (!pose) return;
        const space = this.select(id).value as IKRig['target']['space'];
        next.target = { space, x: pose.target.x - (space === 'offset' ? pose.sourceTip.x : 0), y: pose.target.y - (space === 'offset' ? pose.sourceTip.y : 0) };
      }
      if (id === 'ikX' || id === 'ikY') {
        if (!Number.isFinite(this.input(id).valueAsNumber)) { this.refresh(); return; }
        next.target[id === 'ikX' ? 'x' : 'y'] = this.input(id).valueAsNumber;
      }
      try { this.update(next, 'Configure IK'); } catch (error) { this.error(String(error)); }
    });
    session.subscribe((_snapshot, change) => {
      if (change === 'document' || change === 'track') { this.activeId = ''; this.refreshTrack(); }
      if (['time', 'edit', 'history', 'playback'].includes(change) && (this.pending || Object.keys(this.joints).length)) {
        this.pending = undefined; this.clearJoints(); this.status('Pose changed. Place joints again before creating a chain.');
      }
      if (['ik', 'history', 'document', 'track'].includes(change)) { this.refresh(); this.redraw(); }
    });
    this.refreshTrack(); this.refresh();
  }

  get placing(): boolean { return Boolean(this.pending); }
  active(): IKRig | undefined { return this.session.snapshot().ikRigs.find((rig) => rig.id === this.activeId); }
  private select(id: string): HTMLSelectElement { return this.root.querySelector(`#${id}`)!; }
  private input(id: string): HTMLInputElement { return this.root.querySelector(`#${id}`)!; }
  private status(text: string): void { this.root.querySelector('#ikStatus')!.textContent = text; }
  private error(text = ''): void { this.root.querySelector('#ikError')!.textContent = text; }
  private get two(): boolean { return this.select('ikType').value === '2'; }
  private get upper(): number { return Number(this.select('ikUpper').value); }
  private get lower(): number { return Number(this.select('ikLower').value); }
  setBusy(busy: boolean): void { this.busy = busy; this.root.disabled = busy || !this.session.track; }

  cancelPlacement(): void {
    if (!this.pending) return;
    this.pending = undefined; this.status('Placement cancelled. Completed joints are kept.'); this.redraw();
  }

  place(point: ViewPoint): void {
    const joint = this.pending; if (!joint) return;
    try {
      if (this.two && this.upper === this.lower) throw new Error('Choose two different segments.');
      const offset = this.renderer.jointOffsetAtView(joint === 'tip' && this.two ? this.lower : this.upper, point);
      if (joint === 'bend') {
        const lowerStart = this.renderer.jointOffsetAtView(this.lower, point);
        this.joints.lowerStart = lowerStart;
      }
      this.joints[joint] = offset; this.pending = undefined; this.error();
      this.status(`${joint[0]!.toUpperCase() + joint.slice(1)} placed. Complete the remaining joints, then create the chain.`);
      this.updatePlacementLabels(); this.redraw();
    } catch (error) { this.error(error instanceof Error ? error.message : String(error)); }
  }

  private clearJoints(): void { this.joints = {}; this.updatePlacementLabels(); }
  private updatePlacementLabels(): void {
    this.root.querySelectorAll<HTMLButtonElement>('[data-ik-place]').forEach((button) => {
      const joint = button.dataset.ikPlace as Joint;
      button.textContent = `${this.joints[joint] ? 'Reposition' : 'Place'} ${joint}`;
    });
  }
  private refreshTrack(): void {
    this.pending = undefined; this.clearJoints();
    for (const id of ['ikUpper', 'ikLower']) this.select(id).replaceChildren(...(this.session.track?.layers ?? []).map((layer) => new Option(`${layer.name} (#${layer.id})`, String(layer.id))));
    this.select('ikLower').selectedIndex = Math.min(1, this.select('ikLower').options.length - 1);
    this.refreshSetup(); this.setBusy(this.busy);
  }
  private refreshSetup(): void {
    this.root.querySelector<HTMLElement>('#ikLowerField')!.hidden = !this.two;
    this.root.querySelector<HTMLElement>('[data-ik-place="bend"]')!.hidden = !this.two;
    const followers = this.root.querySelector('#ikFollowers')!; followers.replaceChildren();
    for (const [slot, id] of (this.two ? [['upper', this.upper], ['lower', this.lower]] : [['upper', this.upper]]) as [string, number][]) {
      const title = document.createElement('p'); title.textContent = `${slot === 'upper' ? 'Root / upper' : 'Lower'} bone followers`;
      const list = document.createElement('div'); list.className = 'link-members';
      for (const layer of this.session.track?.layers ?? []) {
        if (layer.id === id || (this.two && [this.upper, this.lower].includes(layer.id))) continue;
        const label = document.createElement('label'), input = document.createElement('input'), text = document.createElement('span');
        input.type = 'checkbox'; input.dataset.ikFollower = slot; input.value = String(layer.id); text.textContent = `${layer.name} (#${layer.id})`;
        label.append(input, text); list.append(label);
      }
      followers.append(title, list);
    }
  }

  private create(): void {
    const { root, bend, lowerStart, tip } = this.joints;
    if (!root || !tip || (this.two && (!bend || !lowerStart))) throw new Error('Place all joints in the viewport first.');
    const followers = (slot: string) => [...this.root.querySelectorAll<HTMLInputElement>(`[data-ik-follower="${slot}"]:checked`)].map((input) => Number(input.value));
    const bones: IKBone[] = [{ layerId: this.upper, start: root, end: this.two ? bend! : tip, followers: followers('upper') }];
    if (this.two) bones.push({ layerId: this.lower, start: lowerStart!, end: tip, followers: followers('lower') });
    const name = bones.map((bone) => this.session.track?.layers.find((layer) => layer.id === bone.layerId)?.name).join(' / ');
    const id = `ik-${this.nextId++}`;
    const vectors = bones.map((bone) => {
      const start = this.renderer.jointView(bone.layerId, bone.start)!, end = this.renderer.jointView(bone.layerId, bone.end)!;
      return { x: end.x - start.x, y: end.y - start.y };
    });
    const rig: IKRig = { id, name, bones, enabled: true, target: { space: 'offset', x: 0, y: 0 }, bend: 'source', fallbackBend: sourceIKBend(vectors[0]!, vectors[1], 1) };
    this.cancelGesture(); this.session.setIKRig(rig, 'Create IK chain');
    this.activeId = id; this.clearJoints(); this.root.querySelector<HTMLDetailsElement>('#ikSetup')!.open = false;
    this.refresh(); this.redraw();
  }
  private update(rig: IKRig, label: string): void {
    this.cancelGesture(); this.renderer.pause(); this.session.setIKRig(rig, label); this.error();
  }
  private refresh(): void {
    const rigs = this.session.snapshot().ikRigs;
    if (!rigs.some((rig) => rig.id === this.activeId)) this.activeId = rigs[0]?.id ?? '';
    const list = this.select('ikActive'); list.replaceChildren(...rigs.map((rig) => new Option(rig.name, rig.id)));
    if (!rigs.length) list.append(new Option('No IK chains yet', ''));
    list.value = this.activeId;
    const rig = this.active(); this.root.querySelector<HTMLElement>('#ikSettings')!.hidden = !rig;
    if (rig) {
      this.input('ikEnabled').checked = rig.enabled;
      this.select('ikSpace').value = rig.target.space; this.select('ikBend').value = rig.bend;
      this.select('ikBend').disabled = rig.bones.length === 1;
      for (const [id, value] of [['ikX', rig.target.x], ['ikY', rig.target.y]] as const) this.input(id).value = String(value);
      this.root.querySelector('#ikScope')!.textContent = rig.bones.map((bone) => {
        const names = [bone.layerId, ...bone.followers].map((id) => this.session.track?.layers.find((layer) => layer.id === id)?.name);
        return names.join(' + ');
      }).join(' | ');
    } else if (!this.pending) this.status('Create a chain to get a draggable target.');
  }

  draw(svg: DrawSVG): void {
    const rig = this.active(), pose = rig && this.renderer.ikPose(rig.id);
    if (pose && rig) {
      // Draw the evaluated bone endpoints separately: source animation may have
      // intentional gaps, and an idle/disabled overlay must not pretend to join them.
      for (const bone of rig.bones) {
        const points = [this.renderer.jointView(bone.layerId, bone.start), this.renderer.jointView(bone.layerId, bone.end)];
        if (!points[0] || !points[1]) continue;
        svg('polyline', { points: points.map((point) => `${point!.x},${point!.y}`).join(' '), class: `ik-chain${rig.enabled ? '' : ' ik-disabled'}` });
        for (const point of points) svg('circle', { cx: String(point!.x), cy: String(point!.y), r: '4', class: 'ik-joint' });
      }
      if (rig.enabled) {
        const target = this.renderer.worldToView(pose.target), end = this.renderer.worldToView(pose.points.at(-1)!);
        svg('line', { x1: String(end.x), y1: String(end.y), x2: String(target.x), y2: String(target.y), class: 'transform-guide' });
        svg('circle', { cx: String(target.x), cy: String(target.y), r: '22', class: 'transform-handle-hit', 'data-ik-target': rig.id });
        svg('circle', { cx: String(target.x), cy: String(target.y), r: '9', class: 'ik-target' });
        svg('path', { d: `M${target.x - 13} ${target.y}h26M${target.x} ${target.y - 13}v26`, class: 'ik-chain' });
      }
      if (!this.pending && !Object.keys(this.joints).length) this.status(pose.status === 'disabled' ? 'IK off. Underlying animation and edits are unchanged.'
        : pose.status === 'limited' ? `Out of reach by ${pose.residual.toFixed(2)} px. Bones keep their lengths.`
          : pose.status === 'degenerate' ? 'Collapsed bone at this frame. IK skipped; underlying pose preserved.'
            : pose.status === 'idle' ? 'Following original animation. Drag the target or nudge it below.' : 'IK active. Drag the target; two fingers pan/zoom.');
    }
    for (const key of ['root', 'bend', 'tip'] as const) {
      const offset = this.joints[key]; if (!offset) continue;
      const point = this.renderer.jointView(key === 'tip' && this.two ? this.lower : this.upper, offset);
      if (point) svg('circle', { cx: String(point.x), cy: String(point.y), r: '6', class: 'ik-joint', 'data-ik-joint': key });
    }
  }
}
