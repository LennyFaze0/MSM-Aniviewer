"""
Texture Atlas management
Handles loading and managing sprite atlases from XML files
"""

import gzip
import io
import os
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image, UnidentifiedImageError
import numpy as np
from OpenGL.GL import *

try:
    from pypvr import Pypvr  # type: ignore
    _pvr_available_error: Optional[str] = None
except Exception as pvr_exc:  # pragma: no cover - optional dependency
    Pypvr = None  # type: ignore
    _pvr_available_error = str(pvr_exc)

try:
    # pillow-heif registers AVIF/HEIF loaders without requiring the user to
    # build Pillow with AVIF support.
    from pillow_heif import open_heif, register_heif_opener

    register_heif_opener()
    _heif_available_error: Optional[str] = None
except Exception as heif_exc:  # pragma: no cover - optional dependency
    print(f"Warning: Failed to enable HEIF/AVIF support: {heif_exc}")
    open_heif = None
    _heif_available_error = str(heif_exc)

try:  # pragma: no cover - optional dependency
    # Importing pillow_avif registers its Pillow plugin automatically.
    import pillow_avif as _pillow_avif_module  # type: ignore  # noqa: F401

    _avif_plugin_error: Optional[str] = None
except Exception as avif_exc:
    _pillow_avif_module = None  # type: ignore
    _avif_plugin_error = str(avif_exc)

HEIF_EXTENSIONS = ('.avif', '.avifs', '.heif', '.heic')

from .data_structures import SpriteInfo
from utils.binary_reader import BinaryReader


class TextureAtlas:
    """Manages texture atlas loading and sprite information"""
    
    def __init__(self):
        self.texture_id: Optional[int] = None
        self.sprites: Dict[str, SpriteInfo] = {}
        self._lower_sprite_lookup: Dict[str, SpriteInfo] = {}
        self._sprite_suffix_lookup: Dict[str, SpriteInfo] = {}
        self._sprite_slug_lookup: Dict[str, SpriteInfo] = {}
        self._sprite_suffix_lookup: Dict[str, SpriteInfo] = {}
        self.image_width: int = 0
        self.image_height: int = 0
        self.logical_width: int = 0
        self.logical_height: int = 0
        self.image_path: str = ""
        self.is_hires: bool = False  # Track if this is a hi-res atlas
        self.source_name: Optional[str] = None  # filename/alias for sheet remap lookups
        self.xml_path: Optional[str] = None  # Source XML path for export/regeneration
        self.source_id: Optional[int] = None  # Numeric source id from animation JSON
        self.force_unpremultiply: bool = False  # Legacy atlases need straight-alpha input
        self.pivot_mode: Optional[str] = None  # Optional pivot handling mode (e.g., "dof")
        self.downscale_factor: float = 1.0  # Optional GPU downscale for preview use
        # Allow fuzzy sprite-name matching (token/slug/suffix fallbacks).
        # Disable for costume atlases to avoid accidental matches like "AB_head" -> "AB_head_puff_PVC".
        self.fuzzy_lookup_enabled: bool = True
    
    def load_from_xml(self, xml_path: str, data_root: str) -> bool:
        """
        Load texture atlas from XML file
        
        Args:
            xml_path: Path to the XML file
            data_root: Root directory for data files
        
        Returns:
            True if successful, False otherwise
        """
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            self.xml_path = xml_path
            def parse_float_pairs(element):
                if element is None or not element.text:
                    return []
                values = []
                for part in element.text.split():
                    try:
                        values.append(float(part))
                    except ValueError:
                        return []
                if len(values) % 2 != 0:
                    return []
                return [
                    (values[i], values[i + 1])
                    for i in range(0, len(values), 2)
                ]

            def parse_int_list(element):
                if element is None or not element.text:
                    return []
                ints = []
                for part in element.text.split():
                    try:
                        ints.append(int(part))
                    except ValueError:
                        return []
                return ints
            
            # Get image path from XML
            image_path = root.get('imagePath', '')
            if not image_path:
                return False

            # Check if this is a hi-res atlas (sprites are 2x size, need 0.5x scale)
            self.is_hires = root.get('hires', '').lower() == 'true'
            pivot_mode = root.get('pivotMode', '') or root.get('pivot_mode', '')
            pivot_mode = pivot_mode.strip().lower()
            self.pivot_mode = pivot_mode if pivot_mode else None

            # Try both .png and .avif extensions
            full_image_path = os.path.join(data_root, image_path)
            if not os.path.exists(full_image_path):
                base, ext = os.path.splitext(full_image_path)
                candidate_paths: List[str] = []

                def _append_with_gz(path: str) -> None:
                    if path not in candidate_paths:
                        candidate_paths.append(path)
                    gz_path = f"{path}.gz"
                    if gz_path not in candidate_paths:
                        candidate_paths.append(gz_path)

                _append_with_gz(full_image_path)
                if not ext:
                    for alt in ('.png', '.pvr', '.avif'):
                        _append_with_gz(base + alt)
                else:
                    normalized = ext.lower()
                    for alt in ('.png', '.pvr', '.avif'):
                        if normalized != alt:
                            _append_with_gz(base + alt)

                resolved_path = None
                for candidate in candidate_paths:
                    if os.path.exists(candidate):
                        resolved_path = candidate
                        break
                if not resolved_path:
                    return False
                full_image_path = resolved_path

            self.image_path = full_image_path
            declared_width = int(root.get('width', 0))
            declared_height = int(root.get('height', 0))
            self.logical_width = declared_width
            self.logical_height = declared_height
            actual_width, actual_height = self._probe_image_size(full_image_path)
            self.image_width = actual_width or declared_width
            self.image_height = actual_height or declared_height
            
            duplicate_names = set()
            self._lower_sprite_lookup.clear()
            self._sprite_suffix_lookup.clear()
            self._sprite_slug_lookup.clear()
            self._sprite_slug_lookup.clear()
            # Parse sprites
            for sprite_elem in root.findall('sprite'):
                name = sprite_elem.get('n', '')
                if not name:
                    continue
                if name in self.sprites:
                    duplicate_names.add(name)
                    continue
                raw_vertices = parse_float_pairs(sprite_elem.find('vertices'))
                raw_vertices_uv = parse_float_pairs(sprite_elem.find('verticesUV'))
                triangles = parse_int_list(sprite_elem.find('triangles'))
                vertices = raw_vertices if raw_vertices else []
                vertices_uv = []
                if (
                    raw_vertices
                    and raw_vertices_uv
                    and triangles
                    and len(raw_vertices) == len(raw_vertices_uv)
                    and self.image_width > 0
                    and self.image_height > 0
                ):
                    inv_w = 1.0 / self.image_width
                    inv_h = 1.0 / self.image_height
                    vertices_uv = [
                        (uv_x * inv_w, uv_y * inv_h)
                        for uv_x, uv_y in raw_vertices_uv
                    ]
                else:
                    triangles = []
                    vertices = []
                    vertices_uv = []
                sprite = SpriteInfo(
                    name=name,
                    x=int(sprite_elem.get('x', 0)),
                    y=int(sprite_elem.get('y', 0)),
                    w=int(sprite_elem.get('w', 0)),
                    h=int(sprite_elem.get('h', 0)),
                    pivot_x=float(sprite_elem.get('pX', 0.5)),
                    pivot_y=float(sprite_elem.get('pY', 0.5)),
                    offset_x=float(sprite_elem.get('oX', 0) or 0),
                    offset_y=float(sprite_elem.get('oY', 0) or 0),
                    original_w=float(sprite_elem.get('oW', 0) or 0),
                    original_h=float(sprite_elem.get('oH', 0) or 0),
                    rotated=sprite_elem.get('r', '') == 'y',
                    vertices=vertices,
                    vertices_uv=vertices_uv,
                    triangles=triangles
                )
                
                # Use original dimensions if specified, otherwise use sprite dimensions
                # For rotated sprites, the original dimensions are swapped relative to atlas dimensions
                if sprite.original_w == 0:
                    sprite.original_w = sprite.h if sprite.rotated else sprite.w
                if sprite.original_h == 0:
                    sprite.original_h = sprite.w if sprite.rotated else sprite.h
                
                # Calculate derived values (matching game's FUN_005862a0)
                # These represent the remaining trimmed space on right/bottom:
                # derived_w = oW - oX - w, derived_h = oH - oY - h (swap w/h when rotated).
                if sprite.original_w > 0 and sprite.original_h > 0:
                    if sprite.rotated:
                        sprite.derived_w = sprite.original_w - sprite.offset_y - sprite.h
                        sprite.derived_h = sprite.original_h - sprite.offset_x - sprite.w
                    else:
                        sprite.derived_w = sprite.original_w - sprite.offset_x - sprite.w
                        sprite.derived_h = sprite.original_h - sprite.offset_y - sprite.h
                else:
                    sprite.derived_w = 0.0
                    sprite.derived_h = 0.0
                
                self.sprites[sprite.name] = sprite
                self._register_sprite_aliases(sprite)

            if duplicate_names:
                print(f"Warning: duplicate sprite names skipped in {os.path.basename(xml_path)}: "
                      f"{', '.join(sorted(duplicate_names))}")
            
            return True
        except Exception as e:
            print(f"Error loading texture atlas: {e}")
            return False

    def load_from_binary_manifest(self, manifest_path: str, data_root: str) -> bool:
        """
        Load a simplified binary atlas manifest used by island tilesets.
        """
        try:
            reader = BinaryReader.from_file(manifest_path)
        except Exception as exc:
            print(f"Failed to open binary atlas '{manifest_path}': {exc}")
            return False

        try:
            self.xml_path = manifest_path
            sprite_map: Dict[str, SpriteInfo] = {}
            relative_image = reader.read_string()
            image_path = self._resolve_binary_image_path(relative_image, data_root)
            if not image_path:
                raise FileNotFoundError(relative_image)
            self.image_path = image_path
            self.source_name = os.path.basename(manifest_path)
            width, height = self._probe_image_size(image_path)
            self.image_width = width
            self.image_height = height
            self.logical_width = width
            self.logical_height = height
            self.is_hires = False

            sprite_count = reader.read_u32()
            self._lower_sprite_lookup.clear()
            self._sprite_suffix_lookup.clear()
            for _ in range(sprite_count):
                name = reader.read_string()
                x = reader.read_u16()
                y = reader.read_u16()
                w = reader.read_u16()
                h = reader.read_u16()
                sprite = SpriteInfo(
                    name=name,
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    original_w=w,
                    original_h=h,
                    pivot_x=0.5,
                    pivot_y=0.5,
                )
                sprite_map[name] = sprite
                self._register_sprite_aliases(sprite)
            self.sprites = sprite_map
            return True
        except Exception as exc:
            print(f"Failed to parse binary atlas '{manifest_path}': {exc}")
            return False

    def _resolve_binary_image_path(self, relative_path: str, data_root: str) -> Optional[str]:
        """Resolve a relative texture path from a binary manifest."""
        cleaned = relative_path.replace("\\", "/").lstrip("./")
        if not cleaned:
            return None
        if os.path.isabs(cleaned):
            candidates = self._binary_image_candidates(cleaned)
        else:
            candidate_roots = [data_root]
            parent = os.path.dirname(data_root)
            if parent and parent not in candidate_roots:
                candidate_roots.append(parent)
            if cleaned.startswith("data/"):
                cleaned = cleaned[5:]
            candidates = []
            for root in candidate_roots:
                base = os.path.join(root, *cleaned.split("/"))
                candidates.extend(self._binary_image_candidates(base))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def _binary_image_candidates(base_path: str) -> List[str]:
        """Return possible on-disk paths for an atlas texture."""
        base, ext = os.path.splitext(base_path)
        candidates: List[str] = []
        if ext:
            candidates.append(base_path)
            for alt in (".png", ".pvr", ".avif", ".dds"):
                if alt != ext:
                    candidates.append(base + alt)
        else:
            for alt in (".png", ".pvr", ".avif", ".dds"):
                candidates.append(base + alt)
        return candidates

    @staticmethod
    def _probe_image_size(image_path: str) -> Tuple[int, int]:
        """Return the on-disk pixel dimensions for the atlas texture."""
        try:
            with Image.open(image_path) as img:
                return img.width, img.height
        except Exception:
            return 0, 0

    @staticmethod
    def _slugify(value: str) -> str:
        """Normalize sprite names for fuzzy lookups."""
        return re.sub(r'[^a-z0-9]+', '', value.lower())

    def _register_sprite_aliases(self, sprite: SpriteInfo) -> None:
        """Add lookup entries for a sprite name and its common suffix variants."""
        lower = sprite.name.lower()
        self._lower_sprite_lookup[lower] = sprite
        tokens = [lower]
        if "_" in lower:
            tokens.append(lower.rsplit("_", 1)[-1])
        if "/" in lower:
            tokens.append(lower.rsplit("/", 1)[-1])
        if "-" in lower:
            tokens.append(lower.rsplit("-", 1)[-1])
        if " " in lower:
            tokens.append(lower.rsplit(" ", 1)[-1])
        for token in tokens:
            token = token.strip()
            if token and token not in self._sprite_suffix_lookup:
                self._sprite_suffix_lookup[token] = sprite
        slug = self._slugify(sprite.name)
        if slug and slug not in self._sprite_slug_lookup:
            self._sprite_slug_lookup[slug] = sprite
    
    def load_texture(self) -> bool:
        """
        Load the texture into OpenGL with premultiplied alpha
        
        The MSM game engine uses premultiplied alpha blending:
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        
        This requires textures where RGB values are pre-multiplied by alpha:
        R' = R * A, G' = G * A, B' = B * A
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Load image using PIL
            img = self._load_texture_image(self.image_path)
            img = img.convert('RGBA')
            scale = float(getattr(self, "downscale_factor", 1.0) or 1.0)
            if scale < 1.0:
                new_w = max(1, int(round(img.width * scale)))
                new_h = max(1, int(round(img.height * scale)))
                if new_w != img.width or new_h != img.height:
                    resample = (
                        Image.Resampling.BILINEAR
                        if hasattr(Image, "Resampling")
                        else Image.BILINEAR
                    )
                    img = img.resize((new_w, new_h), resample=resample)
            if self.force_unpremultiply and self._image_looks_premultiplied(img):
                img = self._ensure_straight_alpha(img)
            img_data = np.array(img, dtype=np.float32) / 255.0
            
            # Premultiply alpha: RGB = RGB * A
            # This is required for proper blending with GL_ONE, GL_ONE_MINUS_SRC_ALPHA
            alpha = img_data[:, :, 3:4]  # Keep as 3D array for broadcasting
            img_data[:, :, 0:3] *= alpha
            
            # Convert back to uint8
            img_data = (img_data * 255.0).astype(np.uint8)
            
            # Generate OpenGL texture
            if self.texture_id:
                glDeleteTextures(1, [self.texture_id])
                self.texture_id = None
            self.texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, img.width, img.height,
                        0, GL_RGBA, GL_UNSIGNED_BYTE, img_data)
            
            return True
        except Exception as e:
            print(f"Error loading texture: {e}")
            import traceback
            traceback.print_exc()
            return False

    @staticmethod
    def _ensure_straight_alpha(image: Image.Image) -> Image.Image:
        """Return a copy of an RGBA image with straight (unpremultiplied) color."""
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        arr = np.array(image, dtype=np.float32)
        alpha = arr[..., 3:4]
        mask = alpha > 1.0
        safe_alpha = np.where(mask, alpha, 1.0)
        rgb = np.where(mask, arr[..., :3] * 255.0 / safe_alpha, 0.0)
        arr[..., :3] = np.clip(rgb, 0.0, 255.0)
        arr[..., 3:4] = np.where(mask, alpha, 0.0)
        return Image.fromarray(arr.astype(np.uint8), "RGBA")

    @staticmethod
    def _image_looks_premultiplied(image: Image.Image) -> bool:
        """
        Heuristically determine if an RGBA image is premultiplied by checking how
        often RGB channels exceed their alpha. Modern PNG/AVIF atlases ship in
        straight-alpha form (RGB frequently > alpha) while legacy premultiplied
        textures keep RGB <= alpha for most texels.
        """
        if image.mode != "RGBA":
            return False
        arr = np.array(image, dtype=np.uint16)
        alpha = arr[..., 3]
        mask = alpha > 0
        total = int(np.count_nonzero(mask))
        if total == 0:
            return False
        alpha_3 = np.repeat(alpha[..., None], 3, axis=2)
        violations = np.count_nonzero(((arr[..., :3] > (alpha_3 + 1)) & mask[..., None]))
        violation_ratio = violations / (total * 3)
        return violation_ratio < 0.005


    def _load_texture_image(self, image_path: str) -> Image.Image:
        """
        Load the underlying spritesheet, falling back to pillow-heif when
        Pillow's native decoders cannot parse AVIF/HEIF sources end users
        extracted from the game.
        """
        suffix = os.path.splitext(image_path)[1].lower()
        errors: List[str] = []
        blob: Optional[bytes] = None
        virtual_path = image_path

        if suffix == '.gz':
            try:
                with gzip.open(image_path, 'rb') as gz_handle:
                    blob = gz_handle.read()
            except Exception as exc:
                raise RuntimeError(f"Failed to decompress texture '{os.path.basename(image_path)}': {exc}") from exc
            virtual_path = os.path.splitext(image_path)[0]
            suffix = os.path.splitext(virtual_path)[1].lower()
            image_path = virtual_path

        if suffix == '.pvr':
            pvr_image = self._decode_pvr_texture(image_path, errors, blob=blob)
            if pvr_image:
                return pvr_image

        try:
            if blob is not None:
                return Image.open(io.BytesIO(blob))
            return Image.open(image_path)
        except UnidentifiedImageError as pil_exc:
            if suffix not in HEIF_EXTENSIONS:
                raise
            errors.append(f"Pillow: {pil_exc}")

        heif_image = self._decode_with_pillow_heif(image_path, errors)
        if heif_image:
            return heif_image

        avif_image = self._decode_with_avif_plugin(image_path, errors)
        if avif_image:
            return avif_image

        raise RuntimeError(
            "Failed to decode HEIF/AVIF texture "
            f"'{os.path.basename(image_path)}'. Tried decoders:\n- "
            + "\n- ".join(errors)
        )

    def _decode_with_pillow_heif(self, image_path: str, errors: list[str]) -> Optional[Image.Image]:
        if open_heif is None:
            extra = f" ({_heif_available_error})" if _heif_available_error else ""
            errors.append(f"pillow-heif unavailable{extra or ''}")
            return None
        try:
            heif_file = open_heif(image_path, convert_hdr_to_8bit=False)
            return heif_file.to_pillow()
        except Exception as heif_exc:  # pragma: no cover - runtime-only path
            errors.append(f"pillow-heif: {heif_exc}")
            return None

    def _decode_with_avif_plugin(self, image_path: str, errors: list[str]) -> Optional[Image.Image]:
        if _pillow_avif_module is None:  # type: ignore
            extra = f" ({_avif_plugin_error})" if _avif_plugin_error else ""
            errors.append(f"pillow-avif-plugin unavailable{extra or ''}")
            return None
        try:
            return Image.open(image_path)
        except UnidentifiedImageError as avif_exc:
            errors.append(f"pillow-avif-plugin: {avif_exc}")
            return None

    def _decode_pvr_texture(
        self,
        image_path: str,
        errors: List[str],
        blob: Optional[bytes] = None,
    ) -> Optional[Image.Image]:
        """Decode legacy .pvr spritesheets into a PIL image."""
        if blob is None:
            try:
                with open(image_path, 'rb') as handle:
                    blob = handle.read()
            except Exception as exc:
                errors.append(f"PVRTC decode failed: {exc}")
                return None

        if len(blob) < 4:
            errors.append("PVRTC decode failed: file too small")
            return None

        magic = struct.unpack_from("<I", blob, 0)[0]
        try:
            if magic == 0x03525650:
                return self._decode_pvr_v3(blob, errors)
            # Some bundles prepend the header length; others embed the tag at offset 44.
            if magic == 0x21525650 or (len(blob) >= 48 and blob[44:48] == b'PVR!'):
                return self._decode_pvr_v2(blob, errors)
            errors.append("PVRTC decode failed: unsupported PVR header")
            return None
        except Exception as exc:
            errors.append(f"PVRTC decode failed: {exc}")
            return None

    def _decode_pvr_v3(self, blob: bytes, errors: List[str]) -> Optional[Image.Image]:
        if len(blob) < 52:
            raise RuntimeError("file too small")
        header = struct.unpack_from("<I I Q I I I I I I I I I", blob, 0)
        meta_size = header[11]
        height = header[5]
        width = header[6]
        pixel_format = header[2]
        if pixel_format in (0, 1):
            bpp = 2
            min_width = 16
            word_width = 8
            decoder = "pvrtc"
        elif pixel_format in (2, 3):
            bpp = 4
            min_width = 8
            word_width = 4
            decoder = "pvrtc"
        else:
            decoder = "raw"

        data_offset = 52 + meta_size
        data = blob[data_offset:]

        if decoder == "pvrtc":
            block_width = max(width, min_width) // word_width
            block_height = max(height, 8) // 4
            top_level_size = block_width * block_height * 8
            data = data[:top_level_size]
            if len(data) < top_level_size:
                raise RuntimeError("truncated PVRTC data")
            pixel_bytes = self._decode_pvrtc(data, width, height, bpp)
            alpha = pixel_bytes[..., 3]
            mask = alpha <= 1
            if np.any(mask):
                pixel_bytes[..., :3][mask] = 0
            return Image.fromarray(pixel_bytes.astype(np.uint8), "RGBA")

        raise RuntimeError(f"unsupported PVR v3 format {pixel_format}")

    def _decode_pvr_v2(self, blob: bytes, errors: List[str]) -> Optional[Image.Image]:
        if len(blob) < 52:
            raise RuntimeError("file too small")
        header = struct.unpack_from("<IIIIIIIIIIII", blob, 0)
        header_size, height, width, mipmaps, flags, data_size, bit_count, r_mask, g_mask, b_mask, a_mask, _tag = header
        data_offset = header_size
        if len(blob) < data_offset + data_size:
            raise RuntimeError("truncated PVR payload")

        pixel_format = flags & 0xFF

        if pixel_format in (0x08, 0x10, 0x0C):  # Treat as RGBA4444/ABGR4444 families
            return self._decode_raw_pvr_pixels(
                blob[data_offset:data_offset + data_size],
                width,
                height,
                bit_count,
                r_mask,
                g_mask,
                b_mask,
                a_mask,
            )
        if pixel_format in (0x18, 0x19):  # PVRTC 2bpp / 4bpp
            bpp = 2 if pixel_format == 0x18 else 4
            min_width = 16 if bpp == 2 else 8
            word_width = 8 if bpp == 2 else 4
            block_width = max(width, min_width) // word_width
            block_height = max(height, 8) // 4
            top_level_size = block_width * block_height * 8
            data = blob[data_offset:data_offset + top_level_size]
            if len(data) < top_level_size:
                raise RuntimeError("truncated PVRTC data")
            pixel_bytes = self._decode_pvrtc(data, width, height, bpp)
            alpha = pixel_bytes[..., 3]
            mask = alpha <= 1
            if np.any(mask):
                pixel_bytes[..., :3][mask] = 0
            return Image.fromarray(pixel_bytes.astype(np.uint8), "RGBA")

        raise RuntimeError(f"unsupported PVR v2 format {pixel_format:#x}")

    def _decode_raw_pvr_pixels(
        self,
        data: bytes,
        width: int,
        height: int,
        bit_count: int,
        r_mask: int,
        g_mask: int,
        b_mask: int,
        a_mask: int,
    ) -> Image.Image:
        """Decode uncompressed pixel formats (e.g., RGBA4444) found in older PVRs."""
        if bit_count not in (8, 16, 24, 32):
            raise RuntimeError(f"unsupported raw PVR bit depth {bit_count}")
        bytes_per_pixel = bit_count // 8
        expected = width * height * bytes_per_pixel
        if len(data) < expected:
            raise RuntimeError("truncated raw pixel payload")

        if bit_count == 16:
            values = np.frombuffer(data[:expected], dtype='<u2').reshape((height, width))
        elif bit_count == 32:
            values = np.frombuffer(data[:expected], dtype='<u4').reshape((height, width))
        else:
            # Expand smaller bit depths to 16-bit for mask logic
            dtype = np.uint16 if bit_count == 8 else np.uint32
            values = np.frombuffer(data[:expected], dtype=dtype).reshape((height, width))

        def extract_channel(mask: int) -> np.ndarray:
            if mask == 0:
                return np.zeros_like(values, dtype=np.float32)
            shift = (mask & -mask).bit_length() - 1
            bits = mask.bit_length() - shift
            max_value = (1 << bits) - 1
            channel = ((values & mask) >> shift).astype(np.float32)
            if max_value > 0:
                channel /= max_value
            return channel

        r = extract_channel(r_mask)
        g = extract_channel(g_mask)
        b = extract_channel(b_mask)
        a = extract_channel(a_mask)
        if not a_mask:
            a = np.ones_like(r, dtype=np.float32)

        rgba = np.stack((r, g, b, a), axis=-1)
        rgba = np.clip(rgba * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")

    @staticmethod
    def _decode_pvrtc(data: bytes, width: int, height: int, bpp: int) -> np.ndarray:
        """
        Decode PVRTC spritesheets using a software implementation based on
        Imagination Technologies' PVRTDecompress reference. Supports PVRTC1 2bpp
        and 4bpp textures so legacy VIP atlases match the in-game renderer.
        """
        decoder = _PVRTCDecoder(width, height, data, bpp)
        return decoder.decode()

    def get_sprite(self, name: str) -> Optional[SpriteInfo]:
        """
        Get sprite information by name
        
        Args:
            name: Name of the sprite
        
        Returns:
            SpriteInfo if found, None otherwise
        """
        if not name:
            return None
        working = name.strip()
        if ":" in working:
            prefix, suffix = working.rsplit(":", 1)
            if prefix.strip().isdigit():
                working = suffix.strip() or working
        sprite = self.sprites.get(working)
        if sprite:
            return sprite
        lower_name = working.lower()
        sprite = self._lower_sprite_lookup.get(lower_name)
        if sprite:
            return sprite
        # Common case: animation JSON stores sprite names without extension,
        # while atlas entries include ".png"/etc. Prefer deterministic extension
        # lookup before any fuzzy matching to avoid accidental collisions.
        known_exts = (".png", ".avif", ".dds", ".pvr", ".jpg", ".jpeg", ".webp")
        stem_name = os.path.splitext(lower_name)[0]
        extension_candidates: List[str] = []
        if lower_name == stem_name:
            extension_candidates.extend([f"{stem_name}{ext}" for ext in known_exts])
        else:
            extension_candidates.append(stem_name)
            extension_candidates.extend([f"{stem_name}{ext}" for ext in known_exts])
        for candidate_name in extension_candidates:
            sprite = self._lower_sprite_lookup.get(candidate_name)
            if sprite:
                return sprite
        if not self.fuzzy_lookup_enabled:
            return None
        suffix_candidate = self._sprite_suffix_lookup.get(lower_name.strip())
        if suffix_candidate:
            return suffix_candidate
        slug = self._slugify(working)
        if slug:
            sprite = self._sprite_slug_lookup.get(slug)
            if sprite:
                return sprite
            if len(slug) >= 3:
                for key, candidate in self._sprite_slug_lookup.items():
                    if key.endswith(slug) or slug.endswith(key):
                        return candidate
        token_candidates = [tok for tok in re.split(r'[^a-z0-9]+', lower_name) if tok]
        if len(token_candidates) >= 2:
            for candidate_name, candidate in self.sprites.items():
                candidate_tokens = [
                    tok for tok in re.split(r'[^a-z0-9]+', Path(candidate_name).stem.lower()) if tok
                ]
                if all(tok in candidate_tokens for tok in token_candidates):
                    return candidate

        # Some revisions prefix sprite names (e.g., "chicken2_body" vs "body").
        # Fall back to suffix matching so "body" can resolve to "chicken2_body".
        suffix_candidates = (
            lower_name,
            f"_{lower_name}",
            f"/{lower_name}",
        )
        for key, candidate in self._lower_sprite_lookup.items():
            if any(key.endswith(suffix) for suffix in suffix_candidates):
                return candidate
        return None


class _PVRTCDecoder:
    """
    Faithful PVRTC1 decoder (2bpp and 4bpp) based on Imagination Technologies'
    PVRTDecompress reference implementation (MIT-licensed). The code mirrors the
    original logic so legacy VIP atlases match the in-game rendering.
    """

    WORD_HEIGHT = 4
    MIN_HEIGHT = 8

    def __init__(self, width: int, height: int, data: bytes, bpp: int):
        if bpp not in (2, 4):
            raise ValueError(f"unsupported PVRTC bpp {bpp}")
        self.width = width
        self.height = height
        self.data = data
        self.bpp = bpp
        self.word_width = 8 if bpp == 2 else 4
        self.min_width = 16 if bpp == 2 else 8

    def decode(self) -> np.ndarray:
        true_width = max(self.width, self.min_width)
        true_height = max(self.height, self.MIN_HEIGHT)
        if not self._is_power_of_two(true_width) or not self._is_power_of_two(true_height):
            raise RuntimeError(
                f"PVRTC textures must be power-of-two (got {true_width}x{true_height})"
            )

        num_x_words = true_width // self.word_width
        num_y_words = true_height // self.WORD_HEIGHT
        total_words = num_x_words * num_y_words
        required_bytes = total_words * 8
        payload = self._prepare_payload(required_bytes)
        word_members = struct.unpack_from(f"<{total_words * 2}I", payload, 0)

        image = np.zeros((true_height, true_width, 4), dtype=np.uint8)
        for word_y in range(-1, num_y_words - 1):
            for word_x in range(-1, num_x_words - 1):
                indices = self._word_indices(word_x, word_y, num_x_words, num_y_words)
                offsets = [
                    self._twiddle_uv(num_x_words, num_y_words, *indices[key]) * 2
                    for key in ("P", "Q", "R", "S")
                ]
                words = [
                    (word_members[offset], word_members[offset + 1])
                    for offset in offsets
                ]
                block_pixels = self._pvrtc_get_pixels(words)
                self._map_block(image, block_pixels, indices)

        if true_width != self.width or true_height != self.height:
            return image[: self.height, : self.width].copy()
        return image

    def _prepare_payload(self, required_bytes: int) -> bytes:
        if len(self.data) < required_bytes:
            return self.data + b"\x00" * (required_bytes - len(self.data))
        return self.data[:required_bytes]

    @staticmethod
    def _is_power_of_two(value: int) -> bool:
        return value > 0 and (value & (value - 1)) == 0

    def _word_indices(
        self, word_x: int, word_y: int, num_x_words: int, num_y_words: int
    ) -> Dict[str, Tuple[int, int]]:
        return {
            "P": (self._wrap_word_index(num_x_words, word_x), self._wrap_word_index(num_y_words, word_y)),
            "Q": (self._wrap_word_index(num_x_words, word_x + 1), self._wrap_word_index(num_y_words, word_y)),
            "R": (self._wrap_word_index(num_x_words, word_x), self._wrap_word_index(num_y_words, word_y + 1)),
            "S": (self._wrap_word_index(num_x_words, word_x + 1), self._wrap_word_index(num_y_words, word_y + 1)),
        }

    @staticmethod
    def _wrap_word_index(num_words: int, word: int) -> int:
        return (word + num_words) % num_words

    @staticmethod
    def _twiddle_uv(x_size: int, y_size: int, x_pos: int, y_pos: int) -> int:
        min_dimension = x_size
        max_value = y_pos
        twiddled = 0
        src_bit_pos = 1
        dst_bit_pos = 1
        shift_count = 0

        if y_size < x_size:
            min_dimension = y_size
            max_value = x_pos

        while src_bit_pos < min_dimension:
            if y_pos & src_bit_pos:
                twiddled |= dst_bit_pos
            if x_pos & src_bit_pos:
                twiddled |= dst_bit_pos << 1
            src_bit_pos <<= 1
            dst_bit_pos <<= 2
            shift_count += 1

        max_value >>= shift_count
        twiddled |= max_value << (2 * shift_count)

        return twiddled

    def _pvrtc_get_pixels(self, words: List[Tuple[int, int]]) -> List[Tuple[int, int, int, int]]:
        mod_values = [[0] * 8 for _ in range(16)]
        mod_modes = [[0] * 8 for _ in range(16)]
        self._unpack_modulations(words[0], 0, 0, mod_values, mod_modes)
        self._unpack_modulations(words[1], self.word_width, 0, mod_values, mod_modes)
        self._unpack_modulations(words[2], 0, self.WORD_HEIGHT, mod_values, mod_modes)
        self._unpack_modulations(words[3], self.word_width, self.WORD_HEIGHT, mod_values, mod_modes)

        upscaled_a = self._interpolate_colors(
            self._get_color_a(words[0][1]),
            self._get_color_a(words[1][1]),
            self._get_color_a(words[2][1]),
            self._get_color_a(words[3][1]),
        )
        upscaled_b = self._interpolate_colors(
            self._get_color_b(words[0][1]),
            self._get_color_b(words[1][1]),
            self._get_color_b(words[2][1]),
            self._get_color_b(words[3][1]),
        )

        pixels: List[Tuple[int, int, int, int]] = [(0, 0, 0, 0)] * (self.word_width * self.WORD_HEIGHT)
        for y in range(self.WORD_HEIGHT):
            for x in range(self.word_width):
                mod = self._get_modulation_value(
                    mod_values, mod_modes, x + self.word_width // 2, y + self.WORD_HEIGHT // 2
                )
                punchthrough = mod > 10
                if punchthrough:
                    mod -= 10

                base_idx = y * self.word_width + x
                color_a = upscaled_a[base_idx]
                color_b = upscaled_b[base_idx]
                result_r = (color_a[0] * (8 - mod) + color_b[0] * mod) // 8
                result_g = (color_a[1] * (8 - mod) + color_b[1] * mod) // 8
                result_b = (color_a[2] * (8 - mod) + color_b[2] * mod) // 8
                result_a = 0 if punchthrough else (color_a[3] * (8 - mod) + color_b[3] * mod) // 8

                idx = base_idx if self.bpp == 2 else (y + x * self.WORD_HEIGHT)
                pixels[idx] = (
                    int(result_r) & 0xFF,
                    int(result_g) & 0xFF,
                    int(result_b) & 0xFF,
                    int(result_a) & 0xFF,
                )
        return pixels

    def _interpolate_colors(
        self,
        color_p: Tuple[int, int, int, int],
        color_q: Tuple[int, int, int, int],
        color_r: Tuple[int, int, int, int],
        color_s: Tuple[int, int, int, int],
    ) -> List[Tuple[int, int, int, int]]:
        word_width = self.word_width
        word_height = self.WORD_HEIGHT

        hp = [int(color_p[0]), int(color_p[1]), int(color_p[2]), int(color_p[3])]
        hq = [int(color_q[0]), int(color_q[1]), int(color_q[2]), int(color_q[3])]
        hr = [int(color_r[0]), int(color_r[1]), int(color_r[2]), int(color_r[3])]
        hs = [int(color_s[0]), int(color_s[1]), int(color_s[2]), int(color_s[3])]

        q_minus_p = [hq[i] - hp[i] for i in range(4)]
        s_minus_r = [hs[i] - hr[i] for i in range(4)]

        for i in range(4):
            hp[i] *= word_width
            hr[i] *= word_width

        out: List[Tuple[int, int, int, int]] = [(0, 0, 0, 0)] * (word_width * word_height)

        if self.bpp == 2:
            for x in range(word_width):
                result_vec = [4 * hp[i] for i in range(4)]
                d_y = [hr[i] - hp[i] for i in range(4)]
                for y in range(word_height):
                    red = ((result_vec[0] >> 7) + (result_vec[0] >> 2))
                    green = ((result_vec[1] >> 7) + (result_vec[1] >> 2))
                    blue = ((result_vec[2] >> 7) + (result_vec[2] >> 2))
                    alpha = ((result_vec[3] >> 5) + (result_vec[3] >> 1))
                    out[y * word_width + x] = (red, green, blue, alpha)
                    for i in range(4):
                        result_vec[i] += d_y[i]
                for i in range(4):
                    hp[i] += q_minus_p[i]
                    hr[i] += s_minus_r[i]
        else:
            for y in range(word_height):
                result_vec = [4 * hp[i] for i in range(4)]
                d_y = [hr[i] - hp[i] for i in range(4)]
                for x in range(word_width):
                    red = ((result_vec[0] >> 6) + (result_vec[0] >> 1))
                    green = ((result_vec[1] >> 6) + (result_vec[1] >> 1))
                    blue = ((result_vec[2] >> 6) + (result_vec[2] >> 1))
                    alpha = ((result_vec[3] >> 4) + result_vec[3])
                    out[y * word_width + x] = (red, green, blue, alpha)
                    for i in range(4):
                        result_vec[i] += d_y[i]
                for i in range(4):
                    hp[i] += q_minus_p[i]
                    hr[i] += s_minus_r[i]
        return out

    def _unpack_modulations(
        self,
        word: Tuple[int, int],
        offset_x: int,
        offset_y: int,
        mod_values: List[List[int]],
        mod_modes: List[List[int]],
    ) -> None:
        word_mod_mode = word[1] & 0x1
        modulation_bits = word[0]

        if self.bpp == 2:
            if word_mod_mode:
                if modulation_bits & 0x1:
                    if modulation_bits & (1 << 20):
                        word_mod_mode = 3
                    else:
                        word_mod_mode = 2
                    if modulation_bits & (1 << 21):
                        modulation_bits |= 1 << 20
                    else:
                        modulation_bits &= ~(1 << 20)
                if modulation_bits & 0x2:
                    modulation_bits |= 0x1
                else:
                    modulation_bits &= ~0x1
                for y in range(4):
                    for x in range(8):
                        mod_modes[x + offset_x][y + offset_y] = word_mod_mode
                        if ((x ^ y) & 1) == 0:
                            mod_values[x + offset_x][y + offset_y] = modulation_bits & 0x3
                            modulation_bits >>= 2
            else:
                for y in range(4):
                    for x in range(8):
                        mod_modes[x + offset_x][y + offset_y] = word_mod_mode
                        mod_values[x + offset_x][y + offset_y] = 0x3 if (modulation_bits & 1) else 0x0
                        modulation_bits >>= 1
        else:
            if word_mod_mode:
                for y in range(4):
                    for x in range(4):
                        value = modulation_bits & 0x3
                        if value == 1:
                            value = 4
                        elif value == 2:
                            value = 14
                        elif value == 3:
                            value = 8
                        mod_values[y + offset_y][x + offset_x] = value
                        modulation_bits >>= 2
            else:
                for y in range(4):
                    for x in range(4):
                        value = (modulation_bits & 0x3) * 3
                        if value > 3:
                            value -= 1
                        mod_values[y + offset_y][x + offset_x] = value
                        modulation_bits >>= 2

    @staticmethod
    def _get_color_a(color_data: int) -> Tuple[int, int, int, int]:
        if color_data & 0x8000:
            red = (color_data & 0x7C00) >> 10
            green = (color_data & 0x3E0) >> 5
            blue = (color_data & 0x1E) | ((color_data & 0x1E) >> 4)
            alpha = 0xF
        else:
            red = ((color_data & 0xF00) >> 7) | ((color_data & 0xF00) >> 11)
            green = ((color_data & 0xF0) >> 3) | ((color_data & 0xF0) >> 7)
            blue = ((color_data & 0xE) << 1) | ((color_data & 0xE) >> 2)
            alpha = (color_data & 0x7000) >> 11
        return (red, green, blue, alpha)

    @staticmethod
    def _get_color_b(color_data: int) -> Tuple[int, int, int, int]:
        if color_data & 0x80000000:
            red = (color_data & 0x7C000000) >> 26
            green = (color_data & 0x3E00000) >> 21
            blue = (color_data & 0x1F0000) >> 16
            alpha = 0xF
        else:
            red = ((color_data & 0xF000000) >> 23) | ((color_data & 0xF000000) >> 27)
            green = ((color_data & 0xF00000) >> 19) | ((color_data & 0xF00000) >> 23)
            blue = ((color_data & 0xF0000) >> 15) | ((color_data & 0xF0000) >> 19)
            alpha = (color_data & 0x70000000) >> 27
        return (red, green, blue, alpha)

    def _get_modulation_value(
        self,
        mod_values: List[List[int]],
        mod_modes: List[List[int]],
        x_pos: int,
        y_pos: int,
    ) -> int:
        if self.bpp == 2:
            rep_vals = (0, 3, 5, 8)
            mode = mod_modes[x_pos][y_pos]
            value = mod_values[x_pos][y_pos]
            if mode == 0:
                return rep_vals[value]
            if ((x_pos ^ y_pos) & 1) == 0:
                return rep_vals[value]
            if mode == 1:
                return (
                    rep_vals[mod_values[x_pos][y_pos - 1]]
                    + rep_vals[mod_values[x_pos][y_pos + 1]]
                    + rep_vals[mod_values[x_pos - 1][y_pos]]
                    + rep_vals[mod_values[x_pos + 1][y_pos]]
                    + 2
                ) // 4
            if mode == 2:
                return (
                    rep_vals[mod_values[x_pos - 1][y_pos]]
                    + rep_vals[mod_values[x_pos + 1][y_pos]]
                    + 1
                ) // 2
            return (
                rep_vals[mod_values[x_pos][y_pos - 1]]
                + rep_vals[mod_values[x_pos][y_pos + 1]]
                + 1
            ) // 2
        return mod_values[x_pos][y_pos]

    def _map_block(
        self,
        image: np.ndarray,
        block_pixels: List[Tuple[int, int, int, int]],
        indices: Dict[str, Tuple[int, int]],
    ) -> None:
        half_w = self.word_width // 2
        half_h = self.WORD_HEIGHT // 2
        for y in range(half_h):
            for x in range(half_w):
                self._write_pixel(
                    image,
                    indices["P"][0] * self.word_width + x + half_w,
                    indices["P"][1] * self.WORD_HEIGHT + y + half_h,
                    block_pixels[y * self.word_width + x],
                )
                self._write_pixel(
                    image,
                    indices["Q"][0] * self.word_width + x,
                    indices["Q"][1] * self.WORD_HEIGHT + y + half_h,
                    block_pixels[y * self.word_width + x + half_w],
                )
                self._write_pixel(
                    image,
                    indices["R"][0] * self.word_width + x + half_w,
                    indices["R"][1] * self.WORD_HEIGHT + y,
                    block_pixels[(y + half_h) * self.word_width + x],
                )
                self._write_pixel(
                    image,
                    indices["S"][0] * self.word_width + x,
                    indices["S"][1] * self.WORD_HEIGHT + y,
                    block_pixels[(y + half_h) * self.word_width + x + half_w],
                )

    @staticmethod
    def _write_pixel(image: np.ndarray, x: int, y: int, pixel: Tuple[int, int, int, int]) -> None:
        if 0 <= x < image.shape[1] and 0 <= y < image.shape[0]:
            image[y, x] = pixel
