export const RETAINED_ASSETS_DATABASE = 'msm-aniviewer-assets-v1';

export interface AssetFileHandle {
  kind: 'file';
  name: string;
  getFile(): Promise<File>;
}

export interface AssetFolderHandle {
  kind: 'directory';
  name: string;
  values(): AsyncIterableIterator<AssetFileHandle | AssetFolderHandle>;
  queryPermission?(options: { mode: 'read' }): Promise<PermissionState>;
  requestPermission?(options: { mode: 'read' }): Promise<PermissionState>;
  isSameEntry?(other: AssetFolderHandle): Promise<boolean>;
}

export interface RetainedFile {
  path: string;
  name: string;
  blob: Blob;
  lastModified: number;
  folderId?: string;
  detached?: boolean;
}

export interface RetainedFolder {
  id: string;
  root: string;
  handle: AssetFolderHandle;
}

export interface RetainedSelection { path: string; track: number }
export interface RetainedState { enabled: boolean; selection?: RetainedSelection }
export interface RetainedSnapshot {
  files: RetainedFile[];
  folders: RetainedFolder[];
  state: RetainedState;
}

export function assetPath(file: File): string {
  return (file.webkitRelativePath || file.name).replaceAll('\\', '/').replace(/^\/+/, '').toLowerCase();
}

export function isRetainableAsset(name: string): boolean {
  return name.toLowerCase() === '__data'
    || /\.(?:bin|json|xml|avif|png|webp|jpe?g|ogg|wav|mp3|m4a|aac|flac|unity3d|bundle|zip)$/i.test(name);
}

export function retainFile(file: File, folderId?: string): RetainedFile {
  return { path: assetPath(file), name: file.name, blob: file.slice(0, file.size, file.type), lastModified: file.lastModified, folderId };
}

export function restoreFile(record: RetainedFile): File {
  const file = new File([record.blob], record.name, { type: record.blob.type, lastModified: record.lastModified });
  Object.defineProperty(file, 'webkitRelativePath', { value: record.path });
  return file;
}

export function hasFolderPicker(): boolean {
  return typeof window !== 'undefined' && typeof (window as unknown as {
    showDirectoryPicker?: unknown;
  }).showDirectoryPicker === 'function';
}

export function pickAssetFolder(): Promise<AssetFolderHandle> {
  return (window as unknown as {
    showDirectoryPicker(options: { mode: 'read'; id: string }): Promise<AssetFolderHandle>;
  }).showDirectoryPicker({ mode: 'read', id: 'aniviewer-game-assets' });
}

export async function folderPermission(handle: AssetFolderHandle): Promise<PermissionState> {
  try { return await handle.queryPermission?.({ mode: 'read' }) ?? 'prompt'; }
  catch { return 'denied'; }
}

export async function scanAssetFolder(folder: RetainedFolder): Promise<RetainedFile[]> {
  const files: RetainedFile[] = [];
  const visit = async (directory: AssetFolderHandle, path: string): Promise<void> => {
    for await (const entry of directory.values()) {
      if (!entry.name || entry.name === '.' || entry.name === '..' || /[/\\]/.test(entry.name)) {
        throw new Error('The folder returned an invalid file name. The saved copy was not changed.');
      }
      const childPath = `${path}/${entry.name}`;
      if (entry.kind === 'directory') {
        if (!entry.name.startsWith('.')) await visit(entry, childPath);
      } else if (isRetainableAsset(entry.name)) {
        const file = await entry.getFile();
        files.push({ ...retainFile(file, folder.id), path: childPath.toLowerCase() });
      }
    }
  };
  // Publish a replacement only after every directory and file was readable.
  await visit(folder.handle, folder.root);
  return files;
}

export function mergeFolderSnapshot(
  current: ReadonlyMap<string, RetainedFile>, folder: RetainedFolder, scanned: readonly RetainedFile[],
): { files: Map<string, RetainedFile>; puts: RetainedFile[]; deletes: string[] } {
  const files = new Map(current);
  const found = new Set(scanned.map((file) => file.path));
  const deletes: string[] = [];
  const puts: RetainedFile[] = [];
  for (const [path, record] of current) {
    if (record.folderId === folder.id && !found.has(path)) { files.delete(path); deletes.push(path); }
  }
  for (const record of scanned) {
    const previous = current.get(record.path);
    if (previous && previous.folderId === record.folderId && previous.lastModified === record.lastModified
      && previous.blob.size === record.blob.size) continue;
    puts.push(record);
    files.set(record.path, record);
  }
  return { files, puts, deletes };
}

function requestValue<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export class RetainedAssetStore {
  private async open(): Promise<IDBDatabase> {
    if (typeof indexedDB === 'undefined') throw new Error('This browser does not allow local asset storage.');
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(RETAINED_ASSETS_DATABASE, 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore('files', { keyPath: 'path' });
        request.result.createObjectStore('folders', { keyPath: 'id' });
        request.result.createObjectStore('state');
      };
      request.onsuccess = () => {
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('Close other viewer tabs to update the saved library.'));
    });
  }

  async read(): Promise<RetainedSnapshot> {
    const database = await this.open();
    try {
      const tx = database.transaction(['files', 'folders', 'state'], 'readonly');
      const [files, folders, state] = await Promise.all([
        requestValue<RetainedFile[]>(tx.objectStore('files').getAll()),
        requestValue<RetainedFolder[]>(tx.objectStore('folders').getAll()),
        requestValue<RetainedState | undefined>(tx.objectStore('state').get('library')),
      ]);
      return { files, folders, state: state ?? { enabled: false } };
    } finally { database.close(); }
  }

  async write(change: {
    puts?: RetainedFile[]; deletes?: string[]; folders?: RetainedFolder[];
    state?: RetainedState; clear?: boolean;
  }): Promise<RetainedFile[]> {
    const puts: RetainedFile[] = [];
    // File.slice() can still reference a mutable native file. Consume its bytes
    // before opening the transaction so cached copies survive edits/permission loss.
    // Stream one asset at a time rather than allocating an entire-library buffer.
    for (const record of change.puts ?? []) {
      const blob = record.detached ? record.blob : await new Response(record.blob.stream(), {
        headers: { 'Content-Type': record.blob.type || 'application/octet-stream' },
      }).blob();
      puts.push({ ...record, blob, detached: true });
    }
    const database = await this.open();
    try {
      await new Promise<void>((resolve, reject) => {
        const tx = database.transaction(['files', 'folders', 'state'], 'readwrite');
        tx.oncomplete = () => resolve();
        tx.onabort = () => reject(tx.error ?? new Error('Saving the asset library was cancelled.'));
        tx.onerror = () => { /* The abort handler reports failure after rollback. */ };
        try {
          if (change.clear) for (const name of ['files', 'folders', 'state']) tx.objectStore(name).clear();
          for (const path of change.deletes ?? []) tx.objectStore('files').delete(path);
          for (const record of puts) tx.objectStore('files').put(record);
          if (change.folders) {
            tx.objectStore('folders').clear();
            for (const folder of change.folders) tx.objectStore('folders').put(folder);
          }
          if (change.state) tx.objectStore('state').put(change.state, 'library');
        } catch (error) { tx.abort(); reject(error); }
      });
    } finally { database.close(); }
    return puts;
  }
}
