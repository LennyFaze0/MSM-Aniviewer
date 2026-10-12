/// <reference lib="webworker" />

import { parseAnimationBin, type BinRevisionMode } from '../core/bin-parser';

interface ParseMessage {
  id: number;
  buffer: ArrayBuffer;
  mode: BinRevisionMode;
  path: string;
}

self.addEventListener('message', (event: MessageEvent<ParseMessage>) => {
  const { id, buffer, mode, path } = event.data;
  try {
    const result = parseAnimationBin(buffer, mode, path);
    self.postMessage({
      id,
      document: result.document,
      revision: result.revision,
      format: result.format,
    });
  } catch (error) {
    self.postMessage({ id, error: error instanceof Error ? error.message : String(error) });
  }
});
