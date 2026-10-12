import { applyPalette, GIFEncoder, quantize } from 'gifenc';
import { MAX_VIDEO_EXPORT_BYTES } from './video-export-protocol';

const gif = GIFEncoder();
self.onmessage = ({ data }: MessageEvent<{
  pixels?: Uint8ClampedArray; width: number; height: number; colors: number; delay: number; repeat: number; finish?: boolean;
}>) => {
  try {
    if (data.finish) {
      gif.finish();
      const bytes = gif.bytes();
      postMessage({ buffer: bytes.buffer }, [bytes.buffer]);
      return;
    }
    const pixels = data.pixels!;
    let transparent = false;
    for (let i = 3; i < pixels.length; i += 4) if (pixels[i]! < 128) { transparent = true; break; }
    const palette = quantize(pixels, data.colors - Number(transparent), { format: 'rgb565' });
    const indexed = applyPalette(pixels, palette, 'rgb565');
    const transparentIndex = transparent ? palette.length : 0;
    if (transparent) {
      palette.push([0, 0, 0]);
      for (let i = 0; i < indexed.length; i++) if (pixels[i * 4 + 3]! < 128) indexed[i] = transparentIndex;
    }
    gif.writeFrame(indexed, data.width, data.height, {
      palette, delay: data.delay, repeat: data.repeat, transparent, transparentIndex, dispose: 2,
    });
    if (gif.bytesView().byteLength > MAX_VIDEO_EXPORT_BYTES) throw new Error('GIF exceeds the 128 MiB export limit. Lower the resolution or frame rate.');
    postMessage({ ready: true });
  } catch (error) { postMessage({ error: error instanceof Error ? error.message : String(error) }); }
};
