import {
  RetainedAssetStore, folderPermission, hasFolderPicker, mergeFolderSnapshot,
  isRetainableAsset, restoreFile, retainFile, scanAssetFolder,
  type AssetFolderHandle, type RetainedFile, type RetainedFolder, type RetainedSelection,
} from '../core/retained-assets';
import './asset-retention.css';

interface AssetRetentionOptions {
  root: HTMLElement;
  onSnapshot(files: File[], selection?: RetainedSelection): Promise<void>;
  canRefresh(): boolean;
}

function bytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MB`;
  return `${(size / 1024 ** 3).toFixed(2)} GB`;
}

export class AssetRetention {
  private readonly store = new RetainedAssetStore();
  private files = new Map<string, RetainedFile>();
  private folders: RetainedFolder[] = [];
  private readonly folderStatus = new Map<string, string>();
  private enabled = false;
  private requestedEnabled?: boolean;
  private unsaved = false;
  private selection?: RetainedSelection;
  private queue: Promise<void> = Promise.resolve();
  private pending = 0;
  private selectionTimer = 0;
  private storageDetail = '';
  private message = 'Session only. Assets are not uploaded or saved between visits.';
  private readonly checkbox: HTMLInputElement;
  private readonly status: HTMLElement;
  private readonly folderList: HTMLElement;
  private readonly refreshButton: HTMLButtonElement;
  private readonly clearButton: HTMLButtonElement;
  private readonly retryButton: HTMLButtonElement;

  constructor(private readonly options: AssetRetentionOptions) {
    options.root.className = 'asset-retention';
    options.root.innerHTML = `
      <label class="asset-retention-toggle"><input id="rememberAssetsCheckbox" type="checkbox" />Remember imported assets on this device</label>
      <p id="assetRetentionStatus" role="status" aria-live="polite"></p>
      <small>Stores local copies, including ZIPs. Original files are never changed. Turning this off removes saved copies, not the current session.</small>
      <div class="asset-retention-actions">
        <button id="refreshSavedAssetsButton" class="button" type="button">Refresh saved folders</button>
        <button id="clearSavedAssetsButton" class="button" type="button">Forget saved library</button>
        <button id="retrySaveAssetsButton" class="button" type="button" hidden>Retry saving library</button>
      </div>
      <div class="asset-retention-folders"></div>`;
    this.checkbox = options.root.querySelector('input')!;
    this.status = options.root.querySelector('#assetRetentionStatus')!;
    this.folderList = options.root.querySelector('.asset-retention-folders')!;
    this.refreshButton = options.root.querySelector('#refreshSavedAssetsButton')!;
    this.clearButton = options.root.querySelector('#clearSavedAssetsButton')!;
    this.retryButton = options.root.querySelector('#retrySaveAssetsButton')!;
    this.checkbox.addEventListener('change', () => {
      const enabled = this.checkbox.checked;
      this.requestedEnabled = enabled;
      void this.run(() => this.setEnabled(enabled)).finally(() => {
        this.requestedEnabled = undefined;
        this.render();
      });
    });
    this.clearButton.addEventListener('click', () => {
      if (window.confirm('Remove saved asset copies and game-folder connections from this browser? Your original files and current viewport will not be changed.')) {
        void this.run(() => this.setEnabled(false));
      }
    });
    this.refreshButton.addEventListener('click', () => { void this.run(() => this.refreshFolders()); });
    this.retryButton.addEventListener('click', () => { void this.run(() => this.setEnabled(true)); });
    this.render();
  }

  private run(operation: () => Promise<void>): Promise<void> {
    this.pending++;
    this.render();
    const result = this.queue.then(operation).catch((error: unknown) => {
      this.message = `Could not finish: ${error instanceof Error ? error.message : String(error)}. Existing saved data was kept.`;
    }).finally(() => { this.pending--; this.render(); });
    this.queue = result;
    return result;
  }

  start(): Promise<void> {
    return this.run(async () => {
      let saved;
      try { saved = await this.store.read(); }
      catch (error) {
        this.message = `Asset storage unavailable; session imports still work. ${error instanceof Error ? error.message : String(error)}`;
        return;
      }
      this.enabled = saved.state.enabled;
      if (!this.enabled) return;
      this.message = 'Restoring saved assets...';
      this.render();
      this.files = new Map(saved.files.map((file) => [file.path, file]));
      this.folders = saved.folders;
      this.selection = saved.state.selection;
      await this.options.onSnapshot(saved.files.map(restoreFile), this.selection);
      this.message = 'Saved library restored.';
      await this.refreshFolders();
      await this.updateStorageDetail();
    });
  }

  importFiles(files: Iterable<File>): Promise<void> {
    const records = [...files].filter((file) => isRetainableAsset(file.name)).map((file) => retainFile(file));
    return this.run(async () => {
      if (!records.length) { this.message = 'No supported animation, image, audio, XML, bundle, or ZIP assets selected.'; return; }
      const next = new Map(this.files);
      for (const record of records) next.set(record.path, record);
      await this.apply(next, records, []);
    });
  }

  importFolder(handle: AssetFolderHandle): Promise<void> {
    return this.run(async () => {
      let folder: RetainedFolder | undefined;
      for (const previous of this.folders) {
        try { if (await handle.isSameEntry?.(previous.handle)) { folder = previous; break; } } catch { /* Keep separate if identity cannot be verified. */ }
      }
      if (!folder) {
        let root = handle.name.toLowerCase();
        let suffix = 2;
        while (this.folders.some((entry) => entry.root === root)) root = `${handle.name.toLowerCase()} (${suffix++})`;
        folder = { id: crypto.randomUUID(), root, handle };
      }
      this.message = `Reading ${folder.root}...`;
      this.render();
      const scanned = await scanAssetFolder(folder);
      const merged = mergeFolderSnapshot(this.files, folder, scanned);
      const folders = this.folders.filter((entry) => entry.id !== folder.id).concat(folder);
      await this.apply(merged.files, merged.puts, merged.deletes, folders);
      this.folderStatus.set(folder.id, 'Connected');
    });
  }

  rememberSelection(selection: RetainedSelection): void {
    this.selection = selection;
    window.clearTimeout(this.selectionTimer);
    if (!this.enabled) return;
    this.selectionTimer = window.setTimeout(() => {
      void this.run(async () => {
        if (this.enabled) await this.store.write({ state: { enabled: true, selection: this.selection } });
      });
    }, 250);
  }

  private async setEnabled(enabled: boolean): Promise<void> {
    window.clearTimeout(this.selectionTimer);
    if (enabled) {
      this.message = 'Saving local copies. Keep this tab open until saving finishes...';
      this.render();
      // Request durable storage from the user's action; browsers may decline it.
      const persistent = await navigator.storage?.persist?.().catch(() => false);
      const saved = await this.store.write({ clear: true, puts: [...this.files.values()], folders: this.folders, state: { enabled: true, selection: this.selection } });
      for (const file of saved) this.files.set(file.path, file);
      this.enabled = true;
      this.unsaved = false;
      this.message = persistent ? 'Library retention enabled with persistent storage.' : 'Library retention enabled. Browser storage limits and eviction rules apply.';
      await this.updateStorageDetail();
    } else {
      await this.store.write({ clear: true });
      this.enabled = false;
      this.unsaved = false;
      this.folders = [];
      this.folderStatus.clear();
      this.storageDetail = '';
      this.message = 'Saved copies and connections removed. Current session and original files are unchanged.';
    }
  }

  private async apply(
    next: Map<string, RetainedFile>, puts: RetainedFile[], deletes: string[], folders = this.folders,
  ): Promise<void> {
    if (!this.options.canRefresh()) throw new Error('Finish or cancel the export before changing the asset library');
    if (puts.length || deletes.length) await this.options.onSnapshot([...next.values()].map(restoreFile), this.selection);
    this.files = next;
    this.folders = folders;
    this.message = this.enabled ? 'Saving asset changes...' : 'Imported for this session. Enable retention to keep these assets after refresh.';
    this.render();
    if (this.enabled) {
      try {
        const saved = await this.store.write({
          clear: this.unsaved, puts: this.unsaved ? [...this.files.values()] : puts,
          deletes, folders, state: { enabled: true, selection: this.selection },
        });
        for (const file of saved) this.files.set(file.path, file);
        this.unsaved = false;
        this.message = 'Library saved on this device.';
        await this.updateStorageDetail();
      } catch (error) {
        this.unsaved = true;
        this.message = `Loaded for this session, but NOT saved: ${error instanceof Error ? error.message : String(error)}. The previous saved copy is intact; retry retention before closing.`;
      }
    }
  }

  private async refreshFolders(): Promise<void> {
    if (!this.options.canRefresh()) throw new Error('Finish or cancel the export before refreshing');
    for (const folder of this.folders) {
      if (!hasFolderPicker() || await folderPermission(folder.handle) !== 'granted') {
        this.folderStatus.set(folder.id, hasFolderPicker() ? 'Permission needed; using saved copy' : 'Live folder access unavailable; using saved copy');
        continue;
      }
      try {
        this.message = `Checking ${folder.root} for updated assets...`;
        this.render();
        const scanned = await scanAssetFolder(folder);
        const merged = mergeFolderSnapshot(this.files, folder, scanned);
        if (merged.puts.length || merged.deletes.length) await this.apply(merged.files, merged.puts, merged.deletes);
        this.folderStatus.set(folder.id, 'Connected; checked for updates');
      } catch (error) {
        this.folderStatus.set(folder.id, `Refresh failed; saved copy kept: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    if (!this.unsaved) this.message = this.folders.length ? 'Folder checks finished. Saved copies remain available if a folder cannot be read.' : 'Saved library ready. Imported copies can be updated by adding files or folders again.';
  }

  private async updateStorageDetail(): Promise<void> {
    try {
      const estimate = await navigator.storage?.estimate?.();
      this.storageDetail = estimate?.quota ? `Site storage: ${bytes(estimate.usage ?? 0)} / ${bytes(estimate.quota)} estimated.` : '';
    } catch { this.storageDetail = ''; }
  }

  private render(): void {
    this.checkbox.checked = this.requestedEnabled ?? this.enabled;
    this.checkbox.disabled = this.pending > 0;
    this.refreshButton.disabled = this.pending > 0 || !this.folders.length || !hasFolderPicker();
    this.clearButton.disabled = this.pending > 0 || !this.enabled;
    this.retryButton.hidden = !this.unsaved;
    this.retryButton.disabled = this.pending > 0;
    const size = [...this.files.values()].reduce((sum, file) => sum + file.blob.size, 0);
    this.status.textContent = `${this.message} ${this.files.size} asset files / ${bytes(size)}. ${this.storageDetail}`;
    this.options.root.dataset.busy = String(this.pending > 0);
    this.options.root.dataset.enabled = String(this.enabled);
    this.folderList.replaceChildren();
    for (const folder of this.folders) {
      const row = document.createElement('div');
      const label = document.createElement('span');
      label.textContent = `${folder.root}: ${this.folderStatus.get(folder.id) ?? 'Saved folder'}`;
      row.append(label);
      if (hasFolderPicker()) {
        const reconnect = document.createElement('button');
        reconnect.type = 'button';
        reconnect.className = 'button';
        reconnect.textContent = 'Reconnect';
        reconnect.setAttribute('aria-label', `Reconnect ${folder.root}`);
        reconnect.disabled = this.pending > 0;
        reconnect.addEventListener('click', () => {
          // Permission requests must originate directly from a user gesture.
          const permission = folder.handle.requestPermission?.({ mode: 'read' }) ?? Promise.resolve('denied');
          void this.run(async () => {
            if (await permission !== 'granted') throw new Error('Folder access was not granted');
            await this.refreshFolders();
          });
        });
        row.append(reconnect);
      }
      this.folderList.append(row);
    }
  }
}
