import type { AssetLibrary, IndexedAnimation } from '../core/asset-library';
import {
  buildMonsterCatalog, filterMonsterCatalog, findMonsterPortrait,
  type MonsterCatalogEntry, type MonsterPortrait,
} from '../core/monster-catalog';
import './monster-browser.css';

interface MonsterBrowserOptions {
  library: AssetLibrary;
  activeEntry(): IndexedAnimation | undefined;
  onSelect(entry: IndexedAnimation): void;
  onOpenFiles(): void;
  onOpenFolder(): void;
}

const PAGE_SIZE = 48;

export class MonsterBrowser {
  private readonly dialog = document.createElement('dialog');
  private readonly search: HTMLInputElement;
  private readonly category: HTMLSelectElement;
  private readonly grid: HTMLDivElement;
  private readonly summary: HTMLParagraphElement;
  private readonly source: HTMLSelectElement;
  private readonly load: HTMLButtonElement;
  private readonly pageLabel: HTMLSpanElement;
  private readonly previous: HTMLButtonElement;
  private readonly next: HTMLButtonElement;
  private entries: MonsterCatalogEntry[] = [];
  private portraits: MonsterPortrait[] = [];
  private selected?: MonsterCatalogEntry;
  private selectedSource?: IndexedAnimation;
  private page = 0;
  private generation = 0;
  private searchTimer = 0;
  private readonly urls = new Set<string>();
  private opener?: HTMLElement;

  constructor(private readonly options: MonsterBrowserOptions) {
    this.dialog.id = 'monsterBrowser';
    this.dialog.className = 'monster-browser';
    this.dialog.setAttribute('aria-labelledby', 'monsterBrowserTitle');
    this.dialog.setAttribute('aria-describedby', 'monsterBrowserHint');
    this.dialog.innerHTML = `
      <div class="monster-browser-layout">
        <header class="monster-browser-heading">
          <h2 id="monsterBrowserTitle">Monster Browser</h2>
          <button type="button" class="button" data-close aria-label="Close Monster Browser">Close</button>
        </header>
        <p id="monsterBrowserHint">Browse imported assets. Choose a monster, then load its animation file.</p>
        <div class="monster-browser-filters">
          <label>Search monsters<input type="search" placeholder="Name, monster code, or file path..." autofocus /></label>
          <label>Source<select data-category>
            <option value="">All sources</option><option value="MSM">My Singing Monsters</option>
            <option value="DOF">Dawn of Fire</option><option value="Other">Other animations</option>
          </select></label>
        </div>
        <p class="monster-browser-summary" role="status" aria-live="polite"></p>
        <div class="monster-browser-grid" role="group" aria-label="Available monsters"></div>
        <div class="monster-browser-pages">
          <button type="button" class="button" data-previous aria-label="Previous monster page">Previous</button>
          <span></span>
          <button type="button" class="button" data-next aria-label="Next monster page">Next</button>
        </div>
        <footer class="monster-browser-footer">
          <label>Animation source<select data-source aria-label="Monster animation source"></select></label>
          <button type="button" class="button primary" data-load>Load Monster</button>
          <div class="monster-browser-imports">
            <button type="button" class="button" data-folder>Open Game Folder</button>
            <button type="button" class="button" data-files>Open Animation Files</button>
          </div>
        </footer>
      </div>`;
    document.body.append(this.dialog);
    const get = <T extends HTMLElement>(selector: string): T => this.dialog.querySelector<T>(selector)!;
    this.search = get('input');
    this.category = get('[data-category]');
    this.grid = get('.monster-browser-grid');
    this.summary = get('.monster-browser-summary');
    this.source = get('[data-source]');
    this.load = get('[data-load]');
    this.pageLabel = get('.monster-browser-pages span');
    this.previous = get('[data-previous]');
    this.next = get('[data-next]');
    get('[data-close]').addEventListener('click', () => this.close());
    this.dialog.addEventListener('cancel', (event) => { event.preventDefault(); this.close(); });
    this.dialog.addEventListener('click', (event) => {
      const rect = this.dialog.getBoundingClientRect();
      if (event.target === this.dialog && (event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom)) this.close();
    });
    this.search.addEventListener('input', () => {
      window.clearTimeout(this.searchTimer);
      this.searchTimer = window.setTimeout(() => { this.page = 0; this.render(); }, 150);
    });
    this.category.addEventListener('change', () => { this.page = 0; this.render(); });
    this.previous.addEventListener('click', () => { this.page--; this.render(); });
    this.next.addEventListener('click', () => { this.page++; this.render(); });
    this.source.addEventListener('change', () => {
      this.selectedSource = this.selected?.sources.find((entry) => entry.path === this.source.value);
      this.source.title = this.selectedSource?.path ?? '';
      this.renderCards();
    });
    this.load.addEventListener('click', () => {
      if (!this.selectedSource) return;
      const source = this.selectedSource;
      this.close();
      this.options.onSelect(source);
    });
    get('[data-folder]').addEventListener('click', () => { this.close(); this.options.onOpenFolder(); });
    get('[data-files]').addEventListener('click', () => { this.close(); this.options.onOpenFiles(); });
  }

  get isOpen(): boolean { return this.dialog.open; }

  open(): void {
    if (this.isOpen) return;
    this.opener = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    this.search.value = '';
    this.category.value = '';
    this.page = 0;
    this.dialog.showModal();
    this.refresh();
    this.search.focus();
  }

  close(): void {
    window.clearTimeout(this.searchTimer);
    this.releasePortraits();
    this.dialog.close();
    this.opener?.focus();
  }

  refresh(): void {
    if (!this.isOpen) return;
    this.entries = buildMonsterCatalog(this.options.library.animations);
    this.portraits = this.options.library.monsterPortraits();
    const active = this.options.activeEntry();
    this.selected = this.entries.find((entry) => entry.sources.includes(active!));
    this.selectedSource = active;
    if (this.selected && !this.search.value && !this.category.value) {
      this.page = Math.floor(this.entries.indexOf(this.selected) / PAGE_SIZE);
    }
    this.render();
  }

  private filtered(): MonsterCatalogEntry[] {
    return filterMonsterCatalog(this.entries, this.search.value, this.category.value);
  }

  private render(): void {
    const filtered = this.filtered();
    this.page = Math.max(0, Math.min(this.page, Math.ceil(filtered.length / PAGE_SIZE) - 1));
    if (!filtered.includes(this.selected!)) {
      this.selected = undefined;
      this.selectedSource = undefined;
    }
    this.summary.textContent = `${filtered.length} of ${this.entries.length} entries available`;
    this.pageLabel.textContent = `Page ${this.page + 1} of ${Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))}`;
    this.previous.disabled = this.page === 0;
    this.next.disabled = (this.page + 1) * PAGE_SIZE >= filtered.length;
    this.renderSelection();
    this.renderCards();
    this.grid.scrollTop = 0;
  }

  private renderSelection(): void {
    this.source.replaceChildren();
    for (const source of this.selected?.sources ?? []) {
      const option = document.createElement('option');
      option.value = source.path;
      option.textContent = `${source.kind.toUpperCase()} | ${source.path}`;
      this.source.append(option);
    }
    if (!this.selected) this.source.add(new Option('Select a monster above', ''));
    this.source.disabled = !this.selected;
    this.load.disabled = !this.selectedSource;
    this.source.value = this.selectedSource?.path ?? '';
    this.source.title = this.selectedSource?.path ?? '';
  }

  private releasePortraits(): void {
    this.generation++;
    for (const url of this.urls) URL.revokeObjectURL(url);
    this.urls.clear();
  }

  private renderCards(): void {
    this.releasePortraits();
    const generation = this.generation;
    const entries = this.filtered().slice(this.page * PAGE_SIZE, (this.page + 1) * PAGE_SIZE);
    this.grid.replaceChildren();
    const jobs: Array<() => Promise<void>> = [];
    for (const entry of entries) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'monster-browser-card';
      button.dataset.monsterId = entry.id;
      button.setAttribute('aria-pressed', String(entry === this.selected));
      button.setAttribute('aria-label', entry.name);
      const art = document.createElement('span');
      art.className = 'monster-browser-art';
      art.setAttribute('aria-hidden', 'true');
      art.textContent = entry.token.toUpperCase();
      const name = document.createElement('strong');
      name.textContent = entry.name;
      const detail = document.createElement('span');
      detail.className = 'monster-browser-detail';
      detail.textContent = `${entry.category} | ${entry.sources.length} file${entry.sources.length === 1 ? '' : 's'}`;
      button.title = `${entry.name}\n${entry.sources.map((source) => source.path).join('\n')}`;
      button.append(art, name, detail);
      button.addEventListener('click', () => {
        if (this.selected !== entry) this.selectedSource = entry.sources[0];
        this.selected = entry;
        this.grid.querySelectorAll('button').forEach((card) => card.setAttribute('aria-pressed', String(card === button)));
        this.renderSelection();
      });
      this.grid.append(button);
      const source = entry === this.selected ? this.selectedSource ?? entry.sources[0]! : entry.sources[0]!;
      const portrait = findMonsterPortrait(entry, source, this.portraits);
      if (!portrait) { art.title = 'No matching portrait imported'; continue; }
      jobs.push(async () => {
        try {
          const blob = await portrait.blob();
          if (generation !== this.generation || !this.isOpen) return;
          const url = URL.createObjectURL(blob);
          this.urls.add(url);
          const img = document.createElement('img');
          img.alt = '';
          img.decoding = 'async';
          img.onload = () => { if (generation === this.generation) art.replaceChildren(img); };
          img.src = url;
        } catch {
          // A missing/corrupt portrait must not prevent loading the animation.
          art.title = 'Portrait unavailable; animation can still be loaded';
        }
      });
    }
    if (!entries.length) {
      const empty = document.createElement('p');
      empty.className = 'monster-browser-empty';
      empty.textContent = this.entries.length
        ? 'No matches. Try another name, file path, or source filter.'
        : 'No animations imported yet. Open a game folder or animation files below. Include the gfx/book folder for monster portraits.';
      this.grid.append(empty);
    }
    // Only the visible page is decoded, with bounded ZIP extraction concurrency.
    const worker = async (): Promise<void> => {
      while (generation === this.generation && this.isOpen && jobs.length) await jobs.shift()!();
    };
    const workerCount = Math.min(4, jobs.length);
    for (let index = 0; index < workerCount; index++) void worker();
  }
}
