import source from '../content/viewer-manual.txt?raw';
import { findManualMatches, parseManualDocument, type ManualBlock, type ManualMatch } from '../core/manual-document';
import './manual-reader.css';

const icon = (path: string) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

export class ManualReader {
  private readonly model = parseManualDocument(source);
  private readonly references = new Map(this.model.sections.flatMap((section) => {
    const label = section.label.match(/^\({2,3}(.+?)\){2,3}$/)?.[1];
    return label ? [[label, section.id] as const] : [];
  }));
  private readonly linkPattern = new RegExp(`https?://[^\\s]+|\\b(?:${[...this.references.keys()].map((label) => label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\b`, 'g');
  private readonly dialog = document.createElement('dialog');
  private readonly body: HTMLElement;
  private readonly search: HTMLInputElement;
  private readonly counter: HTMLOutputElement;
  private readonly sectionSelect: HTMLSelectElement;
  private readonly previous: HTMLButtonElement;
  private readonly next: HTMLButtonElement;
  private readonly closeButton: HTMLButtonElement;
  private readonly navigation: Array<{ id: string; button: HTMLButtonElement; target: HTMLElement }> = [];
  private matches: ManualMatch[] = [];
  private matchIndex = -1;
  private animation?: Animation;
  private closing = false;
  private backdropDown = false;
  private opener?: HTMLElement;

  constructor(private readonly trigger: HTMLButtonElement, private readonly onOpen: () => void) {
    this.dialog.id = 'manualReader';
    this.dialog.className = 'manual-reader';
    this.dialog.setAttribute('aria-labelledby', 'manualReaderTitle');
    this.dialog.innerHTML = `
      <header class="manual-reader-header">
        <h2 id="manualReaderTitle"></h2>
        <button class="button manual-close" type="button" aria-label="Close manual" autofocus>${icon('M6 6l12 12M18 6L6 18')}</button>
      </header>
      <div class="manual-reader-search">
        <input id="manualSearch" type="search" aria-label="Search manual" placeholder="Search manual" autocomplete="off" />
        <div class="manual-search-actions">
          <output id="manualSearchCount" aria-label="Search matches" aria-live="polite"></output>
          <button class="button" type="button" data-manual-previous aria-label="Previous match" disabled>${icon('M6 15l6-6 6 6')}</button>
          <button class="button" type="button" data-manual-next aria-label="Next match" disabled>${icon('M6 9l6 6 6-6')}</button>
        </div>
      </div>
      <select id="manualSectionSelect" class="manual-section-select" aria-label="Manual section"></select>
      <div class="manual-reader-layout">
        <nav class="manual-reader-nav" aria-label="Manual sections"></nav>
        <article id="manualDocument" class="manual-document" tabindex="0" aria-label="Manual text"></article>
      </div>`;
    this.dialog.querySelector('#manualReaderTitle')!.textContent = this.model.title;
    this.body = this.dialog.querySelector('#manualDocument')!;
    this.search = this.dialog.querySelector('#manualSearch')!;
    this.counter = this.dialog.querySelector('#manualSearchCount')!;
    this.sectionSelect = this.dialog.querySelector('#manualSectionSelect')!;
    this.previous = this.dialog.querySelector('[data-manual-previous]')!;
    this.next = this.dialog.querySelector('[data-manual-next]')!;
    this.closeButton = this.dialog.querySelector('.manual-close')!;
    this.render();
    document.body.append(this.dialog);
    trigger.setAttribute('aria-haspopup', 'dialog');
    trigger.setAttribute('aria-controls', this.dialog.id);
    trigger.setAttribute('aria-expanded', 'false');
    trigger.addEventListener('click', () => this.open());
    this.closeButton.addEventListener('click', () => this.close());
    this.search.addEventListener('input', () => this.searchText());
    this.previous.addEventListener('click', () => this.goMatch(this.matchIndex - 1));
    this.next.addEventListener('click', () => this.goMatch(this.matchIndex + 1));
    this.sectionSelect.addEventListener('change', () => this.jump(this.sectionSelect.value));
    this.body.addEventListener('scroll', () => this.updateNavigation(), { passive: true });
    this.dialog.addEventListener('cancel', (event) => { event.preventDefault(); this.close(); });
    this.dialog.addEventListener('keydown', (event) => this.onKeyDown(event));
    this.dialog.addEventListener('pointerdown', (event) => {
      this.backdropDown = event.target === this.dialog && this.outside(event);
    });
    this.dialog.addEventListener('pointerup', (event) => {
      const dismiss = this.backdropDown && event.target === this.dialog && this.outside(event);
      this.backdropDown = false;
      if (dismiss) this.close();
    });
    this.dialog.addEventListener('pointercancel', () => { this.backdropDown = false; });
    window.addEventListener('resize', () => { if (this.isOpen) this.position(); });
    window.visualViewport?.addEventListener('resize', () => { if (this.isOpen) this.position(); });
    window.visualViewport?.addEventListener('scroll', () => { if (this.isOpen) this.position(); });
  }

  get isOpen(): boolean { return this.dialog.open; }

  open(): void {
    if (this.isOpen && !this.closing) return;
    this.animation?.cancel();
    this.closing = false;
    if (!this.isOpen) {
      this.opener = document.activeElement instanceof HTMLElement ? document.activeElement : this.trigger;
      this.onOpen();
      this.dialog.showModal();
    }
    this.trigger.setAttribute('aria-expanded', 'true');
    this.position();
    this.closeButton.focus({ preventScroll: true });
    this.updateNavigation();
    this.animate(false);
  }

  close(): void {
    if (!this.isOpen || this.closing) return;
    this.closing = true;
    this.backdropDown = false;
    this.animation?.cancel();
    this.animate(true);
  }

  private animate(closing: boolean): void {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
      if (closing) this.finishClose();
      return;
    }
    const down = { transform: 'translateY(100%)', opacity: 0 };
    const up = { transform: 'translateY(0)', opacity: 1 };
    const animation = this.dialog.animate(closing ? [up, down] : [down, up], {
      duration: closing ? 220 : 380, easing: 'cubic-bezier(.2,.8,.2,1)',
    });
    this.animation = animation;
    animation.onfinish = () => {
      if (this.animation !== animation) return;
      this.animation = undefined;
      if (closing) this.finishClose();
    };
  }

  private finishClose(): void {
    this.dialog.close();
    this.closing = false;
    this.trigger.setAttribute('aria-expanded', 'false');
    (this.opener?.isConnected ? this.opener : this.trigger).focus({ preventScroll: true });
  }

  private position(): void {
    const view = window.visualViewport;
    const width = view?.width ?? window.innerWidth, height = view?.height ?? window.innerHeight;
    const compact = width <= 760 || height <= 500;
    this.dialog.dataset.compact = String(compact);
    const panelWidth = Math.min(1180, width - (compact ? 0 : 24));
    const panelHeight = Math.min(980, height - (compact ? 8 : 24));
    this.dialog.style.width = `${Math.max(0, panelWidth)}px`;
    this.dialog.style.height = `${Math.max(0, panelHeight)}px`;
    this.dialog.style.left = `${(view?.offsetLeft ?? 0) + (width - panelWidth) / 2}px`;
    this.dialog.style.top = `${(view?.offsetTop ?? 0) + height - panelHeight}px`;
  }

  private render(): void {
    const nav = this.dialog.querySelector('nav')!;
    const addLink = (id: string, label: string, target: HTMLElement, nested: boolean) => {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = label;
      button.className = nested ? 'manual-nav-link manual-nav-nested' : 'manual-nav-link';
      button.dataset.manualTarget = id;
      button.addEventListener('click', () => this.jump(id));
      nav.append(button);
      this.sectionSelect.add(new Option(label, id));
      this.navigation.push({ id, button, target });
    };
    for (const entry of this.model.sections) {
      const section = document.createElement('section');
      section.id = entry.id; section.className = 'manual-card';
      addLink(entry.id, entry.label, section, false);
      for (const block of entry.blocks) {
        const node = this.renderBlock(block);
        section.append(node);
        if (block.kind === 'heading' || block.kind === 'title') section.setAttribute('aria-labelledby', node.id);
        if (block.label) addLink(node.id, block.label, node, true);
      }
      this.body.append(section);
    }
  }

  private renderBlock(block: ManualBlock): HTMLElement {
    const tag = ({ title: 'h1', heading: 'h2', subheading: 'h3', paragraph: 'p', space: 'span' } as const)[block.kind];
    const node = document.createElement(tag);
    node.id = block.id; node.className = `manual-${block.kind}`;
    if (block.kind === 'space') node.setAttribute('aria-hidden', 'true');
    const lead = block.kind === 'paragraph' ? block.text.match(/^(?:\[[^\]\r\n]+\](?: \([^\r\n]+\))?|[^\r\n]{1,90}? -)(?=\s)/)?.[0] : undefined;
    let offset = 0;
    if (lead) {
      const strong = document.createElement('strong');
      strong.textContent = lead; node.append(strong); offset = lead.length;
    }
    const links = block.kind === 'paragraph' ? this.linkPattern : /https?:\/\/[^\s]+/g;
    for (const match of block.text.slice(offset).matchAll(links)) {
      const start = block.text.indexOf(match[0], offset);
      node.append(document.createTextNode(block.text.slice(offset, start)));
      const link = document.createElement('a');
      link.textContent = match[0];
      const reference = this.references.get(match[0]);
      if (reference) {
        link.href = `#${reference}`;
        link.dataset.manualReference = reference;
        link.addEventListener('click', (event) => { event.preventDefault(); this.jump(reference); });
      } else {
        link.href = match[0]; link.target = '_blank'; link.rel = 'noopener noreferrer';
      }
      node.append(link); offset = start + match[0].length;
    }
    node.append(document.createTextNode(block.text.slice(offset)));
    return node;
  }

  private jump(id: string): void {
    const target = this.navigation.find((entry) => entry.id === id)?.target;
    if (target) this.scrollTo(target);
  }

  private scrollTo(target: HTMLElement): void {
    const top = this.body.scrollTop + target.getBoundingClientRect().top - this.body.getBoundingClientRect().top - 16;
    this.body.scrollTo({ top, behavior: 'instant' });
    this.updateNavigation();
  }

  private updateNavigation(): void {
    const top = this.body.getBoundingClientRect().top + 32;
    let current = this.navigation[0];
    for (const entry of this.navigation) if (entry.target.getBoundingClientRect().top <= top) current = entry;
    for (const entry of this.navigation) {
      if (entry === current) entry.button.setAttribute('aria-current', 'location');
      else entry.button.removeAttribute('aria-current');
    }
    if (current) this.sectionSelect.value = current.id;
  }

  private searchText(): void {
    this.body.querySelectorAll('mark').forEach((node) => node.replaceWith(document.createTextNode(node.textContent ?? '')));
    this.body.normalize();
    this.matches = findManualMatches(this.model.text, this.search.value);
    this.matchIndex = -1;
    const walker = document.createTreeWalker(this.body, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    while (walker.nextNode()) nodes.push(walker.currentNode as Text);
    let offset = 0;
    for (const node of nodes) {
      const text = node.data, end = offset + text.length;
      const hits = this.matches.map((match, index) => ({ ...match, index })).filter((match) => match.start < end && match.end > offset);
      if (hits.length) {
        const fragment = document.createDocumentFragment();
        let cursor = 0;
        for (const match of hits) {
          const start = Math.max(0, match.start - offset), stop = Math.min(text.length, match.end - offset);
          fragment.append(document.createTextNode(text.slice(cursor, start)));
          const mark = document.createElement('mark');
          mark.dataset.manualMatch = String(match.index); mark.textContent = text.slice(start, stop);
          fragment.append(mark); cursor = stop;
        }
        fragment.append(document.createTextNode(text.slice(cursor))); node.replaceWith(fragment);
      }
      offset = end;
    }
    this.previous.disabled = this.next.disabled = !this.matches.length;
    this.counter.textContent = this.search.value.trim() ? `0 / ${this.matches.length}` : '';
    if (this.matches.length) this.goMatch(0);
  }

  private goMatch(index: number): void {
    if (!this.matches.length) return;
    this.matchIndex = (index + this.matches.length) % this.matches.length;
    this.counter.textContent = `${this.matchIndex + 1} / ${this.matches.length}`;
    for (const mark of this.body.querySelectorAll<HTMLElement>('mark')) {
      mark.classList.toggle('manual-current-match', Number(mark.dataset.manualMatch) === this.matchIndex);
    }
    const target = this.body.querySelector<HTMLElement>('.manual-current-match');
    if (target) this.scrollTo(target);
  }

  private onKeyDown(event: KeyboardEvent): void {
    // A modal reader must not send editing or playback shortcuts to the viewport.
    event.stopPropagation();
    if (event.key === 'Escape') { event.preventDefault(); this.close(); return; }
    if (this.closing) { event.preventDefault(); return; }
    if (event.key === 'Enter' && event.target === this.search) {
      event.preventDefault(); this.goMatch(this.matchIndex + (event.shiftKey ? -1 : 1));
    }
    if (event.key === 'Tab') {
      const focusable = [...this.dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input, select, a[href], [tabindex="0"]')].filter((node) => node.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }

  private outside(event: PointerEvent): boolean {
    const b = this.dialog.getBoundingClientRect();
    return event.clientX < b.left || event.clientX > b.right || event.clientY < b.top || event.clientY > b.bottom;
  }
}
