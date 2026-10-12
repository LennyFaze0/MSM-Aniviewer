import type { DecodedTextureImage, SourceAlphaFormat, TextureImageResource } from './types';

export type { SourceAlphaFormat } from './types';

const SAMPLE_GRID = 8;
const SAMPLE_TILE_EDGE = 32;
const TRANSLUCENT_MIN = 2;
const TRANSLUCENT_MAX = 253;
const CHANNEL_TOLERANCE = 1;
const PREMULTIPLIED_VIOLATION_LIMIT = 0.005;

function canvasFor(width: number, height: number): OffscreenCanvas | HTMLCanvasElement {
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(width, height);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function imageDimensions(image: TextureImageResource): { width: number; height: number } {
  if (typeof HTMLImageElement !== 'undefined' && image instanceof HTMLImageElement) {
    return { width: image.naturalWidth, height: image.naturalHeight };
  }
  return { width: image.width, height: image.height };
}

export function classifyTextureAlpha(pixels: Uint8ClampedArray | Uint8Array): SourceAlphaFormat {
  let translucentPixels = 0;
  let violatingChannels = 0;

  for (let offset = 0; offset + 3 < pixels.length; offset += 4) {
    const alpha = pixels[offset + 3]!;
    if (alpha < TRANSLUCENT_MIN || alpha > TRANSLUCENT_MAX) continue;
    translucentPixels += 1;
    violatingChannels += Number(pixels[offset]! > alpha + CHANNEL_TOLERANCE);
    violatingChannels += Number(pixels[offset + 1]! > alpha + CHANNEL_TOLERANCE);
    violatingChannels += Number(pixels[offset + 2]! > alpha + CHANNEL_TOLERANCE);
  }

  if (!translucentPixels) return 'opaque';
  const violationRatio = violatingChannels / (translucentPixels * 3);
  return violationRatio < PREMULTIPLIED_VIOLATION_LIMIT ? 'premultiplied' : 'straight';
}

export function unpremultiplyRgbaPixels(pixels: Uint8ClampedArray): void {
  for (let offset = 0; offset + 3 < pixels.length; offset += 4) {
    const alpha = pixels[offset + 3]!;
    if (!alpha) {
      pixels[offset] = 0;
      pixels[offset + 1] = 0;
      pixels[offset + 2] = 0;
      continue;
    }
    if (alpha === 255) continue;
    pixels[offset] = Math.min(255, Math.round(pixels[offset]! * 255 / alpha));
    pixels[offset + 1] = Math.min(255, Math.round(pixels[offset + 1]! * 255 / alpha));
    pixels[offset + 2] = Math.min(255, Math.round(pixels[offset + 2]! * 255 / alpha));
  }
}

async function inspectImageAlpha(image: TextureImageResource): Promise<SourceAlphaFormat> {
  const dimensions = imageDimensions(image);
  const tileWidth = Math.max(1, Math.min(SAMPLE_TILE_EDGE, dimensions.width));
  const tileHeight = Math.max(1, Math.min(SAMPLE_TILE_EDGE, dimensions.height));
  const columns = Math.max(1, Math.min(SAMPLE_GRID, Math.ceil(dimensions.width / tileWidth)));
  const rows = Math.max(1, Math.min(SAMPLE_GRID, Math.ceil(dimensions.height / tileHeight)));
  const width = tileWidth * columns;
  const height = tileHeight * rows;
  const canvas = canvasFor(width, height);
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return 'opaque';
  context.clearRect(0, 0, width, height);
  context.imageSmoothingEnabled = false;
  for (let row = 0; row < rows; row += 1) {
    const sourceY = rows === 1
      ? 0
      : Math.round((dimensions.height - tileHeight) * row / (rows - 1));
    for (let column = 0; column < columns; column += 1) {
      const sourceX = columns === 1
        ? 0
        : Math.round((dimensions.width - tileWidth) * column / (columns - 1));
      context.drawImage(
        image,
        sourceX,
        sourceY,
        tileWidth,
        tileHeight,
        column * tileWidth,
        row * tileHeight,
        tileWidth,
        tileHeight,
      );
    }
  }
  return classifyTextureAlpha(context.getImageData(0, 0, width, height).data);
}

async function loadHtmlImage(file: Blob): Promise<HTMLImageElement> {
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.decoding = 'async';
    image.src = url;
    await image.decode();
    return image;
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function normalizeHtmlImage(
  image: HTMLImageElement,
  sourceAlphaFormat: SourceAlphaFormat,
): Promise<DecodedTextureImage> {
  if (sourceAlphaFormat !== 'premultiplied') {
    return { image, alphaMode: 'premultiply-alpha-on-upload', sourceAlphaFormat };
  }

  const { width, height } = imageDimensions(image);
  const canvas = canvasFor(width, height);
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) return { image, alphaMode: 'premultiply-alpha-on-upload', sourceAlphaFormat: 'straight' };
  context.drawImage(image, 0, 0);
  const imageData = context.getImageData(0, 0, width, height);
  unpremultiplyRgbaPixels(imageData.data);
  context.clearRect(0, 0, width, height);
  context.putImageData(imageData, 0, 0);
  return { image: canvas, alphaMode: 'premultiply-alpha-on-upload', sourceAlphaFormat };
}

/** Decode arbitrary game or user images into a single-premultiplied Pixi upload contract. */
export async function decodeTextureImage(file: Blob): Promise<DecodedTextureImage> {
  if (typeof createImageBitmap === 'function') {
    let preserved: ImageBitmap | undefined;
    try {
      preserved = await createImageBitmap(file, { premultiplyAlpha: 'none' });
      const sourceAlphaFormat = await inspectImageAlpha(preserved);
      if (sourceAlphaFormat !== 'straight') {
        return { image: preserved, alphaMode: 'premultiplied-alpha', sourceAlphaFormat };
      }
      preserved.close();
      preserved = undefined;
      const premultiplied = await createImageBitmap(file, { premultiplyAlpha: 'premultiply' });
      return { image: premultiplied, alphaMode: 'premultiplied-alpha', sourceAlphaFormat };
    } catch {
      preserved?.close();
      // Fall through for browsers with incomplete ImageBitmap format support.
    }
  }

  const image = await loadHtmlImage(file);
  return normalizeHtmlImage(image, await inspectImageAlpha(image));
}

export async function textureImageFromRgbaPixels(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  knownSourceAlphaFormat?: SourceAlphaFormat,
): Promise<DecodedTextureImage> {
  const sourceAlphaFormat = knownSourceAlphaFormat ?? classifyTextureAlpha(pixels);
  const imageData = new ImageData(Uint8ClampedArray.from(pixels), width, height);
  const image = await createImageBitmap(imageData, {
    premultiplyAlpha: sourceAlphaFormat === 'straight' ? 'premultiply' : 'none',
  });
  return { image, alphaMode: 'premultiplied-alpha', sourceAlphaFormat };
}
