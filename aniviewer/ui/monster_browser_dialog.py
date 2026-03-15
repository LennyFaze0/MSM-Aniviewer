"""
Monster Browser Dialog
Visual picker for monsters using book portrait thumbnails.
Optimized for performance with lazy loading and background thumbnail processing.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple
from concurrent.futures import ThreadPoolExecutor
import threading

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal, QObject, QRect
from PyQt6.QtGui import QPixmap, QColor, QImage, QPainter
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QApplication,
)
from PIL import Image


@dataclass
class MonsterVariantOption:
    """Represents an alternate BIN/JSON pair for a monster (e.g., island-specific files)."""

    display_name: str
    relative_path: str
    json_path: Optional[str]
    bin_path: Optional[str]
    variant_label: str
    stem: str

    @property
    def source_path(self) -> Optional[str]:
        return self.json_path or self.bin_path


@dataclass
class MonsterBrowserEntry:
    """Represents a monster file pair and associated portrait."""

    token: str
    display_name: str
    relative_path: str
    image_path: str
    json_path: Optional[str]
    bin_path: Optional[str]
    variants: List[MonsterVariantOption] = field(default_factory=list)
    island_numbers: List[int] = field(default_factory=list)
    island_labels: List[str] = field(default_factory=list)
    class_name: str = ""
    fam_name: str = ""
    genes: str = ""
    gene_graphics: List[str] = field(default_factory=list)
    entity_type: str = ""
    variant_types: List[str] = field(default_factory=list)
    has_downloads_source: bool = False
    has_game_source: bool = False
    search_blob: str = field(init=False)

    def __post_init__(self):
        self.rebuild_search_blob()

    def rebuild_search_blob(self):
        parts = [
            self.token or "",
            self.display_name or "",
            self.relative_path or "",
            (self.json_path or ""),
            (self.bin_path or ""),
        ]
        stems = []
        for path in (self.json_path, self.bin_path):
            if path:
                stems.append(Path(path).stem)
        parts.extend(stems)
        for variant in self.variants:
            parts.extend(
                [
                    variant.display_name,
                    variant.relative_path,
                    variant.variant_label,
                    variant.stem,
                    (variant.json_path or ""),
                    (variant.bin_path or ""),
                ]
            )
        if self.island_numbers:
            island_values: Set[int] = set()
            for value in self.island_numbers:
                try:
                    island = int(value)
                except (TypeError, ValueError):
                    continue
                if island > 0:
                    island_values.add(island)
            islands = sorted(island_values)
            if islands:
                parts.extend(str(island) for island in islands)
                parts.extend(f"island{island}" for island in islands)
                parts.extend(f"island_{island:03d}" for island in islands)
        parts.extend(self.island_labels or [])
        parts.extend(self.variant_types or [])
        parts.extend(self.gene_graphics or [])
        parts.extend(
            [
                self.class_name or "",
                self.fam_name or "",
                self.genes or "",
                self.entity_type or "",
            ]
        )
        if self.has_downloads_source:
            parts.append("downloads")
        if self.has_game_source:
            parts.append("game")
        self.search_blob = " ".join(part for part in parts if part).lower()

    def has_variants(self) -> bool:
        return bool(self.variants)


class ThumbnailLoader(QObject):
    """Background thumbnail loader with thread pool."""
    
    thumbnail_ready = pyqtSignal(str, QPixmap)  # image_path, pixmap
    
    def __init__(self, thumb_size: QSize, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._thumb_size = thumb_size
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._cache_limit = 512
        self._pending: Set[str] = set()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ThumbLoader")
        self._shutdown = False
        
    def shutdown(self):
        """Clean up the thread pool."""
        self._shutdown = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        
    def get_cached(self, image_path: str) -> Optional[QPixmap]:
        """Get thumbnail from cache if available (thread-safe)."""
        key = self._normalize_path(image_path)
        with self._lock:
            pixmap = self._cache.get(key)
            if pixmap is not None:
                self._cache.move_to_end(key)
            return pixmap
    
    def request_thumbnail(self, image_path: str):
        """Request a thumbnail to be loaded in background."""
        if self._shutdown:
            return
        key = self._normalize_path(image_path)
        with self._lock:
            if key in self._cache or key in self._pending:
                return
            self._pending.add(key)
        self._executor.submit(self._load_thumbnail, image_path, key)
    
    def _normalize_path(self, path: str) -> str:
        return os.path.normcase(os.path.abspath(path)) if path else ""
    
    def _load_thumbnail(self, image_path: str, key: str):
        """Load thumbnail in background thread."""
        if self._shutdown:
            return
        try:
            pixmap = self._load_and_scale(image_path)
            if pixmap and not pixmap.isNull():
                with self._lock:
                    self._cache[key] = pixmap
                    self._pending.discard(key)
                    # Evict old entries
                    while len(self._cache) > self._cache_limit:
                        self._cache.popitem(last=False)
                # Emit signal on main thread
                if not self._shutdown:
                    self.thumbnail_ready.emit(image_path, pixmap)
            else:
                with self._lock:
                    self._pending.discard(key)
        except Exception:
            with self._lock:
                self._pending.discard(key)
    
    def _load_and_scale(self, image_path: str) -> Optional[QPixmap]:
        """Load image and scale to thumbnail size."""
        if not image_path or not os.path.exists(image_path):
            return None
        
        # Try Qt native loading first (faster for common formats)
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            # Fall back to PIL for exotic formats
            pixmap = self._load_via_pillow(image_path)
        
        if pixmap and not pixmap.isNull():
            pixmap = pixmap.scaled(
                self._thumb_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        return pixmap
    
    def _load_via_pillow(self, path: str) -> Optional[QPixmap]:
        """Load image using PIL and convert to QPixmap."""
        try:
            with Image.open(path) as img:
                if img.mode not in ("RGBA", "RGB"):
                    img = img.convert("RGBA")
                # Convert to QImage directly without ImageQt (faster)
                if img.mode == "RGBA":
                    data = img.tobytes("raw", "BGRA")
                    qimage = QImage(data, img.width, img.height, QImage.Format.Format_ARGB32)
                else:
                    data = img.tobytes("raw", "BGR")
                    qimage = QImage(data, img.width, img.height, QImage.Format.Format_RGB888).rgbSwapped()
                # Must copy since data buffer will be freed
                return QPixmap.fromImage(qimage.copy())
        except Exception:
            return None


class MonsterCardWidget(QFrame):
    """Clickable card that shows a monster portrait and name."""

    _placeholder_pixmap: Optional[QPixmap] = None

    def __init__(
        self,
        thumb_size: QSize,
        click_callback: Callable[[MonsterBrowserEntry, Optional[MonsterVariantOption], str], None],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.entry: Optional[MonsterBrowserEntry] = None
        self.variant_option: Optional[MonsterVariantOption] = None
        self._callback = click_callback
        self._thumb_size = thumb_size
        self._thumbnail_loaded = False
        self._can_expand = False
        self._is_expanded = False
        
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(thumb_size.width() + 20, thumb_size.height() + 90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setFixedSize(thumb_size)
        layout.addWidget(self.image_label)

        self.name_label = QLabel("")
        self.name_label.setWordWrap(True)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("font-weight: bold;")
        self.name_label.setMaximumHeight(24)
        layout.addWidget(self.name_label)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_label.setStyleSheet("color: gray; font-size: 8pt;")
        self.detail_label.setMaximumHeight(36)
        layout.addWidget(self.detail_label)

        self.variant_hint_label = QLabel("")
        self.variant_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.variant_hint_label.setStyleSheet("color: #ffaa00; font-size: 8pt;")
        self.variant_hint_label.hide()
        layout.addWidget(self.variant_hint_label)

        self.load_button = QPushButton("Load Default")
        self.load_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.load_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_button.setVisible(False)
        self.load_button.clicked.connect(self._handle_load_clicked)
        layout.addWidget(self.load_button)
        
        # Set placeholder
        self._set_placeholder()

    @classmethod
    def _get_placeholder(cls, size: QSize) -> QPixmap:
        """Get or create a shared placeholder pixmap."""
        if cls._placeholder_pixmap is None or cls._placeholder_pixmap.size() != size:
            cls._placeholder_pixmap = QPixmap(size)
            cls._placeholder_pixmap.fill(QColor("#333333"))
        return cls._placeholder_pixmap

    def _set_placeholder(self):
        """Set the placeholder image."""
        self.image_label.setPixmap(self._get_placeholder(self._thumb_size))
        self._thumbnail_loaded = False

    def set_entry(
        self,
        entry: MonsterBrowserEntry,
        variant: Optional[MonsterVariantOption] = None,
        *,
        can_expand: bool = False,
        expanded: bool = False,
    ):
        """Update the card with a new entry or variant."""
        self.entry = entry
        self.variant_option = variant
        self._can_expand = bool(can_expand and variant is None)
        self._is_expanded = bool(expanded and self._can_expand)
        self._thumbnail_loaded = False
        self._set_placeholder()

        if variant:
            self.name_label.setText(variant.variant_label or variant.display_name)
            detail = variant.relative_path or variant.display_name or entry.relative_path
            self.detail_label.setText(detail)
            self.variant_hint_label.setText(entry.display_name)
            self.variant_hint_label.show()
            self.load_button.setVisible(False)
        else:
            self.name_label.setText(entry.display_name)
            self.detail_label.setText(entry.relative_path)
            if self._can_expand and entry.variants:
                arrow = "▼" if self._is_expanded else "▶"
                count = len(entry.variants)
                label = "variant" if count == 1 else "variants"
                self.variant_hint_label.setText(f"{arrow} {count} extra {label}")
                self.variant_hint_label.setToolTip("Click card to expand/collapse variants")
                self.variant_hint_label.show()
                self.load_button.setText("Load Default")
                self.load_button.setVisible(True)
            else:
                self.variant_hint_label.hide()
                self.load_button.setVisible(False)

    def set_thumbnail(self, pixmap: QPixmap):
        """Set the thumbnail image."""
        if pixmap and not pixmap.isNull():
            self.image_label.setPixmap(pixmap)
            self._thumbnail_loaded = True

    def needs_thumbnail(self) -> bool:
        """Check if this card needs its thumbnail loaded."""
        return self.entry is not None and not self._thumbnail_loaded

    def get_image_path(self) -> Optional[str]:
        """Get the image path for this card's entry."""
        return self.entry.image_path if self.entry else None

    def _handle_load_clicked(self):
        if callable(self._callback) and self.entry:
            self._callback(self.entry, None, "select")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if callable(self._callback) and self.entry:
                if self.variant_option is not None:
                    self._callback(self.entry, self.variant_option, "select")
                elif self._can_expand:
                    self._callback(self.entry, None, "toggle")
                else:
                    self._callback(self.entry, None, "select")
        super().mousePressEvent(event)


class MonsterBrowserDialog(QDialog):
    """Dialog that displays monster portraits for quick selection."""

    def __init__(
        self,
        entries: Iterable[MonsterBrowserEntry],
        *,
        initial_columns: int = 3,
        badge_data_roots: Optional[Iterable[Path]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Monster Browser")
        self.resize(900, 700)
        
        self._all_entries: List[MonsterBrowserEntry] = list(entries)
        self._filtered_entries: List[MonsterBrowserEntry] = list(self._all_entries)
        self.selected_entry: Optional[MonsterBrowserEntry] = None
        self._columns = max(1, initial_columns)
        self._thumb_size = QSize(140, 140)
        self._expanded_tokens: Set[str] = set()
        self._display_payloads: List[Tuple[MonsterBrowserEntry, Optional[MonsterVariantOption]]] = []
        
        # Card pool for recycling
        self._card_pool: List[MonsterCardWidget] = []
        self._visible_cards: Dict[int, MonsterCardWidget] = {}  # index -> card
        
        # Background thumbnail loader
        self._thumbnail_loader = ThumbnailLoader(self._thumb_size, self)
        self._thumbnail_loader.thumbnail_ready.connect(self._on_thumbnail_ready)
        
        # Debounced search
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._apply_pending_filter)
        self._pending_search: str = ""
        
        # Lazy loading timer
        self._lazy_load_timer = QTimer(self)
        self._lazy_load_timer.setSingleShot(True)
        self._lazy_load_timer.setInterval(50)
        self._lazy_load_timer.timeout.connect(self._load_visible_thumbnails)
        
        # Path to card mapping for thumbnail updates
        self._path_to_cards: Dict[str, List[MonsterCardWidget]] = {}
        self._decorated_thumb_cache: Dict[Tuple[str, bool, bool, bool], QPixmap] = {}
        self._badge_data_roots: List[Path] = []
        if badge_data_roots:
            seen_roots: Set[str] = set()
            for root in badge_data_roots:
                if not root:
                    continue
                try:
                    root_path = Path(root).resolve()
                except Exception:
                    root_path = Path(root)
                if not root_path.exists():
                    continue
                key = os.path.normcase(str(root_path))
                if key in seen_roots:
                    continue
                seen_roots.add(key)
                self._badge_data_roots.append(root_path)
        self._amber_badge_pixmap: Optional[QPixmap] = self._load_amber_badge_pixmap()
        self._composer_badge_pixmap: Optional[QPixmap] = self._load_composer_badge_pixmap()
        self._lgn_badge_pixmap: Optional[QPixmap] = self._load_lgn_badge_pixmap()

        self._setup_ui()
        self._populate_filter_options()
        self._apply_filter("")

    def _entry_is_amber(self, entry: Optional[MonsterBrowserEntry]) -> bool:
        if not entry:
            return False
        token = (entry.token or "").lower()
        display = (entry.display_name or "").lower()
        rel = (entry.relative_path or "").lower()
        json_path = (entry.json_path or "").lower()
        bin_path = (entry.bin_path or "").lower()
        image_path = (entry.image_path or "").lower()
        return (
            "amber" in token
            or "amber" in display
            or "amber" in rel
            or "amber" in json_path
            or "amber" in bin_path
            or "amber" in image_path
        )

    @staticmethod
    def _variant_is_amber(variant: Optional[MonsterVariantOption]) -> bool:
        if not variant:
            return False
        display = (variant.display_name or "").lower()
        rel = (variant.relative_path or "").lower()
        label = (variant.variant_label or "").lower()
        stem = (variant.stem or "").lower()
        json_path = (variant.json_path or "").lower()
        bin_path = (variant.bin_path or "").lower()
        return (
            "amber" in display
            or "amber" in rel
            or "amber" in label
            or "amber" in stem
            or "amber" in json_path
            or "amber" in bin_path
        )

    def _entry_is_composer(self, entry: Optional[MonsterBrowserEntry]) -> bool:
        if not entry:
            return False
        token = (entry.token or "").lower()
        display = (entry.display_name or "").lower()
        rel = (entry.relative_path or "").lower()
        json_path = (entry.json_path or "").lower()
        bin_path = (entry.bin_path or "").lower()
        image_path = (entry.image_path or "").lower()
        return (
            "composer" in token
            or "composer" in display
            or "composer" in rel
            or "composer" in json_path
            or "composer" in bin_path
            or "composer" in image_path
        )

    @staticmethod
    def _variant_is_composer(variant: Optional[MonsterVariantOption]) -> bool:
        if not variant:
            return False
        display = (variant.display_name or "").lower()
        rel = (variant.relative_path or "").lower()
        label = (variant.variant_label or "").lower()
        stem = (variant.stem or "").lower()
        json_path = (variant.json_path or "").lower()
        bin_path = (variant.bin_path or "").lower()
        return (
            "composer" in display
            or "composer" in rel
            or "composer" in label
            or "composer" in stem
            or "composer" in json_path
            or "composer" in bin_path
        )

    def _entry_is_lgn(self, entry: Optional[MonsterBrowserEntry]) -> bool:
        if not entry:
            return False
        token = (entry.token or "").lower()
        display = (entry.display_name or "").lower()
        rel = (entry.relative_path or "").lower()
        json_path = (entry.json_path or "").lower()
        bin_path = (entry.bin_path or "").lower()
        return (
            token.startswith("lgn_")
            or "monster_lgn_" in display
            or "monster_lgn_" in rel
            or "monster_lgn_" in json_path
            or "monster_lgn_" in bin_path
        )

    @staticmethod
    def _variant_is_lgn(variant: Optional[MonsterVariantOption]) -> bool:
        if not variant:
            return False
        display = (variant.display_name or "").lower()
        rel = (variant.relative_path or "").lower()
        label = (variant.variant_label or "").lower()
        stem = (variant.stem or "").lower()
        json_path = (variant.json_path or "").lower()
        bin_path = (variant.bin_path or "").lower()
        return (
            stem.startswith("monster_lgn_")
            or "monster_lgn_" in display
            or "monster_lgn_" in rel
            or "monster_lgn_" in label
            or "monster_lgn_" in json_path
            or "monster_lgn_" in bin_path
        )

    @staticmethod
    def _infer_data_root_from_image_path(image_path: str) -> Optional[Path]:
        if not image_path:
            return None
        try:
            path = Path(image_path).resolve()
        except Exception:
            path = Path(image_path)
        parts = list(path.parts)
        lower_parts = [part.lower() for part in parts]
        if "data" in lower_parts:
            idx = lower_parts.index("data")
            return Path(*parts[: idx + 1])
        if "gfx" in lower_parts and "book" in lower_parts:
            gfx_idx = lower_parts.index("gfx")
            if gfx_idx > 0:
                candidate = Path(*parts[:gfx_idx])
                if candidate.name.lower() == "data":
                    return candidate
                data_candidate = candidate / "data"
                if data_candidate.exists():
                    return data_candidate
        return None

    @staticmethod
    def _load_pixmap_file(path: Path) -> Optional[QPixmap]:
        if not path or not path.exists():
            return None
        pixmap = QPixmap(str(path))
        if pixmap and not pixmap.isNull():
            return pixmap
        try:
            with Image.open(path) as img:
                if img.mode not in ("RGBA", "RGB"):
                    img = img.convert("RGBA")
                if img.mode == "RGBA":
                    data = img.tobytes("raw", "BGRA")
                    qimage = QImage(data, img.width, img.height, QImage.Format.Format_ARGB32)
                else:
                    data = img.tobytes("raw", "BGR")
                    qimage = QImage(data, img.width, img.height, QImage.Format.Format_RGB888).rgbSwapped()
                return QPixmap.fromImage(qimage.copy())
        except Exception:
            return None

    def _iter_badge_data_roots(self) -> List[Path]:
        candidate_roots: List[Path] = []
        seen: Set[str] = set()

        for root in self._badge_data_roots:
            key = os.path.normcase(str(root))
            if key in seen:
                continue
            seen.add(key)
            candidate_roots.append(root)

        for entry in self._all_entries:
            root = self._infer_data_root_from_image_path(entry.image_path)
            if not root:
                continue
            key = os.path.normcase(str(root))
            if key in seen:
                continue
            seen.add(key)
            candidate_roots.append(root)
        return candidate_roots

    def _load_badge_from_atlas(
        self,
        *,
        xml_filename: str,
        sprite_name: str,
        fallback_stem: str,
    ) -> Optional[QPixmap]:
        for data_root in self._iter_badge_data_roots():
            xml_path = data_root / "xml_resources" / xml_filename
            if not xml_path.exists():
                continue
            try:
                root = ET.parse(xml_path).getroot()
            except Exception:
                continue
            image_attr = (root.attrib.get("imagePath") or "").strip()
            if not image_attr:
                continue
            sprite_node = None
            for node in root.findall("sprite"):
                if (node.attrib.get("n") or "") == sprite_name:
                    sprite_node = node
                    break
            if sprite_node is None:
                continue
            try:
                sx = int(float(sprite_node.attrib.get("x", "0")))
                sy = int(float(sprite_node.attrib.get("y", "0")))
                sw = int(float(sprite_node.attrib.get("w", "0")))
                sh = int(float(sprite_node.attrib.get("h", "0")))
            except Exception:
                continue
            if sw <= 0 or sh <= 0:
                continue

            image_candidates: List[Path] = []
            primary = data_root / Path(image_attr)
            image_candidates.append(primary)
            stem = primary.with_suffix("")
            for ext in (".avif", ".png", ".webp", ".jpg", ".jpeg"):
                image_candidates.append(stem.with_suffix(ext))
            image_candidates.append(data_root / "gfx" / "menu" / f"{fallback_stem}.avif")
            image_candidates.append(data_root / "gfx" / "menu" / f"{fallback_stem}.png")
            image_candidates.append(data_root / "gfx" / "menu" / f"{fallback_stem}.webp")

            for candidate in image_candidates:
                pixmap = self._load_pixmap_file(candidate)
                if not pixmap or pixmap.isNull():
                    continue
                if sx < 0 or sy < 0:
                    continue
                if sx + sw > pixmap.width() or sy + sh > pixmap.height():
                    continue
                cropped = pixmap.copy(sx, sy, sw, sh)
                if not cropped.isNull():
                    return cropped
        return None

    def _load_amber_badge_pixmap(self) -> Optional[QPixmap]:
        return self._load_badge_from_atlas(
            xml_filename="island_buttons03.xml",
            sprite_name="islands_button_isl022",
            fallback_stem="island_buttons03",
        )

    def _load_composer_badge_pixmap(self) -> Optional[QPixmap]:
        return self._load_badge_from_atlas(
            xml_filename="island_buttons03.xml",
            sprite_name="islands_button_isl011",
            fallback_stem="island_buttons03",
        )

    def _load_lgn_badge_pixmap(self) -> Optional[QPixmap]:
        return self._load_badge_from_atlas(
            xml_filename="island_buttons01.xml",
            sprite_name="islands_button_isl08",
            fallback_stem="island_buttons01",
        )

    def _apply_entry_thumbnail_overlay(
        self,
        entry: Optional[MonsterBrowserEntry],
        variant: Optional[MonsterVariantOption],
        image_path: str,
        pixmap: QPixmap,
    ) -> QPixmap:
        is_amber = self._entry_is_amber(entry) or self._variant_is_amber(variant)
        is_composer = self._entry_is_composer(entry) or self._variant_is_composer(variant)
        is_lgn = self._entry_is_lgn(entry) or self._variant_is_lgn(variant)
        cache_key = (
            os.path.normcase(os.path.abspath(image_path)) if image_path else "",
            bool(is_amber),
            bool(is_composer),
            bool(is_lgn),
        )
        cached = self._decorated_thumb_cache.get(cache_key)
        if cached is not None and not cached.isNull():
            return cached

        amber_badge = self._amber_badge_pixmap if is_amber else None
        composer_badge = self._composer_badge_pixmap if is_composer else None
        lgn_badge = self._lgn_badge_pixmap if is_lgn else None
        if amber_badge is not None and amber_badge.isNull():
            amber_badge = None
        if composer_badge is not None and composer_badge.isNull():
            composer_badge = None
        if lgn_badge is not None and lgn_badge.isNull():
            lgn_badge = None
        if amber_badge is None and composer_badge is None and lgn_badge is None:
            self._decorated_thumb_cache[cache_key] = pixmap
            return pixmap

        # Draw onto a fixed thumbnail-sized canvas so badge placement is stable
        # regardless of portrait crop dimensions.
        canvas_w = max(1, self._thumb_size.width())
        canvas_h = max(1, self._thumb_size.height())
        result = QPixmap(canvas_w, canvas_h)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        try:
            image_x = (canvas_w - pixmap.width()) // 2
            image_y = (canvas_h - pixmap.height()) // 2
            painter.drawPixmap(image_x, image_y, pixmap)

            # Keep badge size visually consistent across cards regardless of
            # portrait crop dimensions.
            thumb_edge = max(1, min(self._thumb_size.width(), self._thumb_size.height()))
            base_badge_size = max(18, int(round(thumb_edge * 0.45)))
            max_fit_size = min(canvas_w - 4, canvas_h - 4)
            if max_fit_size < 12:
                self._decorated_thumb_cache[cache_key] = result
                return result
            badge_size = max(12, min(base_badge_size, max_fit_size))
            margin = 4
            stack_height = 0
            if amber_badge is not None:
                badge = amber_badge.scaled(
                    badge_size,
                    badge_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = max(0, canvas_w - badge.width() - margin)
                y = max(0, canvas_h - badge.height() - margin - stack_height)
                painter.drawPixmap(x, y, badge)
                stack_height += badge.height() + 2
            if composer_badge is not None:
                badge = composer_badge.scaled(
                    badge_size,
                    badge_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = max(0, canvas_w - badge.width() - margin)
                y = max(0, canvas_h - badge.height() - margin - stack_height)
                painter.drawPixmap(x, y, badge)
                stack_height += badge.height() + 2
            if lgn_badge is not None:
                badge = lgn_badge.scaled(
                    badge_size,
                    badge_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = max(0, canvas_w - badge.width() - margin)
                y = max(0, canvas_h - badge.height() - margin - stack_height)
                painter.drawPixmap(x, y, badge)
        finally:
            painter.end()

        self._decorated_thumb_cache[cache_key] = result
        return result

    def _setup_ui(self):
        """Set up the dialog UI."""
        main_layout = QVBoxLayout(self)
        
        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Search:"))
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search monsters...")
        self.search_input.setClearButtonEnabled(True)
        toolbar.addWidget(self.search_input, 1)

        toolbar.addWidget(QLabel("Columns:"))
        self.columns_spin = QSpinBox()
        self.columns_spin.setRange(1, 8)
        self.columns_spin.setValue(self._columns)
        self.columns_spin.setFixedWidth(50)
        toolbar.addWidget(self.columns_spin)

        self.force_reexport_check = QCheckBox("Re-export JSON")
        self.force_reexport_check.setToolTip("Re-export JSON from BIN before loading")
        toolbar.addWidget(self.force_reexport_check)

        main_layout.addLayout(toolbar)

        self.content_tabs = QTabWidget()
        main_layout.addWidget(self.content_tabs, 1)

        # Browser tab
        browser_tab = QWidget()
        browser_layout = QVBoxLayout(browser_tab)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.setSpacing(6)

        # Scroll area with grid
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll)
        
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        self.grid_layout.setHorizontalSpacing(8)
        self.grid_layout.setVerticalSpacing(8)
        self.scroll_area.setWidget(self.grid_container)
        browser_layout.addWidget(self.scroll_area, 1)

        # Status bar
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")
        browser_layout.addWidget(self.status_label)
        self.content_tabs.addTab(browser_tab, "Browser")

        # Filtering options tab
        filter_tab = QWidget()
        filter_layout = QVBoxLayout(filter_tab)
        filter_layout.setContentsMargins(12, 12, 12, 12)
        filter_layout.setSpacing(8)
        self._suspend_filter_signals = False

        filter_hint = QLabel(
            "Filter by island/class/elements/variant/entity/asset health. "
            "Multiple island and element selections are supported."
        )
        filter_hint.setWordWrap(True)
        filter_hint.setStyleSheet("color: gray;")
        filter_layout.addWidget(filter_hint)

        island_header = QHBoxLayout()
        island_header.addWidget(QLabel("Islands:"))
        self.island_all_btn = QPushButton("All")
        self.island_none_btn = QPushButton("None")
        island_header.addStretch()
        island_header.addWidget(self.island_all_btn)
        island_header.addWidget(self.island_none_btn)
        filter_layout.addLayout(island_header)

        self.island_filter_list = QListWidget()
        self.island_filter_list.setMaximumHeight(150)
        filter_layout.addWidget(self.island_filter_list)

        class_row = QHBoxLayout()
        class_row.addWidget(QLabel("Class:"))
        self.class_filter_combo = QComboBox()
        class_row.addWidget(self.class_filter_combo, 1)
        class_row.addWidget(QLabel("Fam:"))
        self.fam_filter_combo = QComboBox()
        class_row.addWidget(self.fam_filter_combo, 1)
        filter_layout.addLayout(class_row)

        gene_mode_row = QHBoxLayout()
        gene_mode_row.addWidget(QLabel("Elements mode:"))
        self.genes_mode_combo = QComboBox()
        self.genes_mode_combo.addItem("Contains any", "any")
        self.genes_mode_combo.addItem("Contains all", "all")
        gene_mode_row.addWidget(self.genes_mode_combo, 1)
        self.genes_all_btn = QPushButton("All")
        self.genes_none_btn = QPushButton("None")
        gene_mode_row.addWidget(self.genes_all_btn)
        gene_mode_row.addWidget(self.genes_none_btn)
        filter_layout.addLayout(gene_mode_row)

        self.genes_filter_list = QListWidget()
        self.genes_filter_list.setMaximumHeight(130)
        filter_layout.addWidget(self.genes_filter_list)

        variant_header = QHBoxLayout()
        variant_header.addWidget(QLabel("Variant types:"))
        self.variant_all_btn = QPushButton("All")
        self.variant_none_btn = QPushButton("None")
        variant_header.addStretch()
        variant_header.addWidget(self.variant_all_btn)
        variant_header.addWidget(self.variant_none_btn)
        filter_layout.addLayout(variant_header)

        self.variant_filter_list = QListWidget()
        self.variant_filter_list.setMaximumHeight(120)
        filter_layout.addWidget(self.variant_filter_list)

        entity_row = QHBoxLayout()
        entity_row.addWidget(QLabel("Entity type:"))
        self.entity_filter_combo = QComboBox()
        entity_row.addWidget(self.entity_filter_combo, 1)
        entity_row.addWidget(QLabel("Asset health:"))
        self.asset_health_combo = QComboBox()
        entity_row.addWidget(self.asset_health_combo, 1)
        filter_layout.addLayout(entity_row)

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.clear_all_filters_btn = QPushButton("Clear All Filters")
        action_row.addWidget(self.clear_all_filters_btn)
        filter_layout.addLayout(action_row)
        filter_layout.addStretch(1)

        self.content_tabs.addTab(filter_tab, "Filtering Options")

        # Buttons
        button_row = QHBoxLayout()
        button_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        button_row.addWidget(close_btn)
        main_layout.addLayout(button_row)

        # Connect signals
        self.search_input.textChanged.connect(self._queue_filter_update)
        self.columns_spin.valueChanged.connect(self._update_columns)
        self.class_filter_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.fam_filter_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.genes_mode_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.entity_filter_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.asset_health_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.island_filter_list.itemChanged.connect(self._on_filters_changed)
        self.genes_filter_list.itemChanged.connect(self._on_filters_changed)
        self.variant_filter_list.itemChanged.connect(self._on_filters_changed)
        self.island_all_btn.clicked.connect(lambda: self._set_all_list_checks(self.island_filter_list, True))
        self.island_none_btn.clicked.connect(lambda: self._set_all_list_checks(self.island_filter_list, False))
        self.genes_all_btn.clicked.connect(lambda: self._set_all_list_checks(self.genes_filter_list, True))
        self.genes_none_btn.clicked.connect(lambda: self._set_all_list_checks(self.genes_filter_list, False))
        self.variant_all_btn.clicked.connect(lambda: self._set_all_list_checks(self.variant_filter_list, True))
        self.variant_none_btn.clicked.connect(lambda: self._set_all_list_checks(self.variant_filter_list, False))
        self.clear_all_filters_btn.clicked.connect(self._reset_all_filters)

    def _on_filters_changed(self, *_args):
        if self._suspend_filter_signals:
            return
        self._apply_filter(self.search_input.text() or "")

    def _entry_variant_type_set(self, entry: MonsterBrowserEntry) -> Set[str]:
        values = {str(value).strip().lower() for value in (entry.variant_types or []) if str(value).strip()}
        if values:
            return values
        hay = " ".join(
            [
                (entry.token or "").lower(),
                (entry.display_name or "").lower(),
                (entry.relative_path or "").lower(),
                (entry.json_path or "").lower(),
                (entry.bin_path or "").lower(),
            ]
        )
        if self._entry_is_lgn(entry):
            values.add("lgn")
        if self._entry_is_amber(entry):
            values.add("amber")
        if self._entry_is_composer(entry):
            values.add("composer")
        if "rare" in hay:
            values.add("rare")
        if "epic" in hay:
            values.add("epic")
        if "seasonal" in hay or re.search(r"(?:^|[_-])s\d{1,2}(?:$|[_-])", hay):
            values.add("seasonal")
        if (entry.entity_type or "").strip().lower() == "box_monster":
            values.add("box_monster")
        if not values:
            values.add("normal")
        return values

    @staticmethod
    def _set_list_values(list_widget: QListWidget, values: Iterable[Tuple[str, str]]) -> None:
        list_widget.blockSignals(True)
        list_widget.clear()
        for text, value in values:
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, value)
            list_widget.addItem(item)
        list_widget.blockSignals(False)

    @staticmethod
    def _checked_list_values(list_widget: QListWidget) -> Set[str]:
        selected: Set[str] = set()
        for idx in range(list_widget.count()):
            item = list_widget.item(idx)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if data is None:
                continue
            selected.add(str(data))
        return selected

    def _set_all_list_checks(self, list_widget: QListWidget, checked: bool, *, apply_filters: bool = True) -> None:
        previous_suspend = self._suspend_filter_signals
        self._suspend_filter_signals = True
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        list_widget.blockSignals(True)
        for idx in range(list_widget.count()):
            list_widget.item(idx).setCheckState(state)
        list_widget.blockSignals(False)
        self._suspend_filter_signals = previous_suspend
        if apply_filters and not previous_suspend:
            self._apply_filter(self.search_input.text() or "")

    def _reset_all_filters(self) -> None:
        self._suspend_filter_signals = True
        self.class_filter_combo.setCurrentIndex(0)
        self.fam_filter_combo.setCurrentIndex(0)
        self.genes_mode_combo.setCurrentIndex(0)
        self.entity_filter_combo.setCurrentIndex(0)
        self.asset_health_combo.setCurrentIndex(0)
        self._set_all_list_checks(self.island_filter_list, False, apply_filters=False)
        self._set_all_list_checks(self.genes_filter_list, False, apply_filters=False)
        self._set_all_list_checks(self.variant_filter_list, False, apply_filters=False)
        self._suspend_filter_signals = False
        self._apply_filter(self.search_input.text() or "")

    def _populate_filter_options(self):
        self._suspend_filter_signals = True
        try:
            island_labels: Dict[int, str] = {}
            for entry in self._all_entries:
                for idx, island_id in enumerate(entry.island_numbers or []):
                    try:
                        parsed = int(island_id)
                    except (TypeError, ValueError):
                        continue
                    if parsed <= 0:
                        continue
                    label = ""
                    if idx < len(entry.island_labels or []):
                        label = str((entry.island_labels or [])[idx]).strip()
                    if not label:
                        label = f"Island {parsed}"
                    island_labels.setdefault(parsed, label)
            island_values = [
                (f"{island_labels[island]} ({island})", str(island))
                for island in sorted(island_labels)
            ]
            self._set_list_values(self.island_filter_list, island_values)

            class_values = sorted({(entry.class_name or "").strip() for entry in self._all_entries if (entry.class_name or "").strip()})
            fam_values = sorted({(entry.fam_name or "").strip() for entry in self._all_entries if (entry.fam_name or "").strip()})
            self.class_filter_combo.clear()
            self.class_filter_combo.addItem("All classes", "")
            for value in class_values:
                self.class_filter_combo.addItem(value, value)

            self.fam_filter_combo.clear()
            self.fam_filter_combo.addItem("All families", "")
            for value in fam_values:
                self.fam_filter_combo.addItem(value, value)

            gene_values = sorted(
                {
                    value.strip()
                    for entry in self._all_entries
                    for value in (entry.gene_graphics or [])
                    if value and value.strip()
                }
            )
            self._set_list_values(
                self.genes_filter_list,
                [(value.replace("gene_", "").replace("_", " ").title(), value) for value in gene_values],
            )

            variant_values = sorted(
                {
                    value
                    for entry in self._all_entries
                    for value in self._entry_variant_type_set(entry)
                    if value
                }
            )
            variant_label_map = {
                "lgn": "Shugabush",
            }
            self._set_list_values(
                self.variant_filter_list,
                [
                    (
                        variant_label_map.get(value, value.replace("_", " ").title()),
                        value,
                    )
                    for value in variant_values
                ],
            )

            entity_values = sorted(
                {
                    (entry.entity_type or "").strip().lower()
                    for entry in self._all_entries
                    if (entry.entity_type or "").strip()
                }
            )
            self.entity_filter_combo.clear()
            self.entity_filter_combo.addItem("All entities", "")
            for value in entity_values:
                self.entity_filter_combo.addItem(value, value)

            self.asset_health_combo.clear()
            self.asset_health_combo.addItem("All assets", "all")
            self.asset_health_combo.addItem("Has JSON + BIN", "json_and_bin")
            self.asset_health_combo.addItem("JSON only", "json_only")
            self.asset_health_combo.addItem("BIN only", "bin_only")
            self.asset_health_combo.addItem("Missing portrait", "missing_portrait")
            self.asset_health_combo.addItem("Downloads override", "downloads")
            self.asset_health_combo.addItem("Game files only", "game_only")
        finally:
            self._suspend_filter_signals = False

    @staticmethod
    def _entry_matches_asset_mode(entry: MonsterBrowserEntry, mode: str) -> bool:
        mode = (mode or "all").strip().lower()
        has_json = bool(entry.json_path)
        has_bin = bool(entry.bin_path)
        has_portrait = bool(entry.image_path and os.path.exists(entry.image_path))
        if mode == "json_and_bin":
            return has_json and has_bin
        if mode == "json_only":
            return has_json and not has_bin
        if mode == "bin_only":
            return has_bin and not has_json
        if mode == "missing_portrait":
            return not has_portrait
        if mode == "downloads":
            return bool(entry.has_downloads_source)
        if mode == "game_only":
            return bool(entry.has_game_source and not entry.has_downloads_source)
        return True

    def _queue_filter_update(self, text: str):
        """Queue a filter update with debouncing."""
        self._pending_search = text or ""
        self._search_timer.start()

    def _apply_pending_filter(self):
        """Apply the pending search filter."""
        self._apply_filter(self._pending_search)

    def _apply_filter(self, text: str):
        """Filter entries and rebuild the grid."""
        normalized = text.lower().strip()
        tokens = [token for token in normalized.split() if token]
        selected_islands = {
            int(value)
            for value in self._checked_list_values(self.island_filter_list)
            if str(value).isdigit()
        }
        selected_class = (self.class_filter_combo.currentData() or "").strip()
        selected_fam = (self.fam_filter_combo.currentData() or "").strip()
        selected_entity = (self.entity_filter_combo.currentData() or "").strip().lower()
        selected_genes = {value.lower() for value in self._checked_list_values(self.genes_filter_list)}
        genes_mode = (self.genes_mode_combo.currentData() or "any").strip().lower()
        selected_variants = {value.lower() for value in self._checked_list_values(self.variant_filter_list)}
        asset_mode = (self.asset_health_combo.currentData() or "all").strip().lower()

        if (
            not tokens
            and not selected_islands
            and not selected_class
            and not selected_fam
            and not selected_entity
            and not selected_genes
            and not selected_variants
            and asset_mode == "all"
        ):
            self._filtered_entries = list(self._all_entries)
        else:
            filtered: List[MonsterBrowserEntry] = []
            for entry in self._all_entries:
                if selected_islands and not (selected_islands & set(entry.island_numbers or [])):
                    continue
                if selected_class and (entry.class_name or "").strip() != selected_class:
                    continue
                if selected_fam and (entry.fam_name or "").strip() != selected_fam:
                    continue
                entry_entity = (entry.entity_type or "").strip().lower()
                if selected_entity and entry_entity != selected_entity:
                    continue
                entry_genes = {value.lower() for value in (entry.gene_graphics or []) if value}
                if selected_genes:
                    if genes_mode == "all":
                        if not selected_genes.issubset(entry_genes):
                            continue
                    else:
                        if not (selected_genes & entry_genes):
                            continue
                entry_variants = self._entry_variant_type_set(entry)
                if selected_variants and not (selected_variants & entry_variants):
                    continue
                if not self._entry_matches_asset_mode(entry, asset_mode):
                    continue
                if tokens and not all(token in entry.search_blob for token in tokens):
                    continue
                filtered.append(entry)
            self._filtered_entries = filtered

        self._rebuild_grid()
        self._update_status(normalized, selected_islands)

    def _update_status(self, text: str, selected_islands: Set[int]):
        """Update the status label."""
        total = len(self._all_entries)
        match = len(self._filtered_entries)
        island_suffix = ""
        if selected_islands:
            ordered = sorted(selected_islands)
            if len(ordered) == 1:
                island_suffix = f" on island {ordered[0]}"
            else:
                island_suffix = f" on {len(ordered)} islands"
        if not total:
            self.status_label.setText("No monsters found in this dataset.")
        elif text and not match:
            self.status_label.setText(f"No monsters match '{text}'{island_suffix}.")
        elif selected_islands and not match:
            self.status_label.setText(f"No monsters found for selected filters{island_suffix}.")
        else:
            self.status_label.setText(f"Showing {match} of {total} monsters{island_suffix}.")

    def _update_columns(self, value: int):
        """Handle column count change."""
        self._columns = max(1, value)
        self._rebuild_grid()

    def _get_card(self) -> MonsterCardWidget:
        """Get a card from the pool or create a new one."""
        if self._card_pool:
            return self._card_pool.pop()
        return MonsterCardWidget(
            self._thumb_size,
            self._handle_card_clicked,
            parent=self.grid_container,
        )

    def _return_card(self, card: MonsterCardWidget):
        """Return a card to the pool."""
        card.hide()
        card.entry = None
        card.variant_option = None
        self._card_pool.append(card)

    def _clear_grid(self):
        """Clear all cards from the grid."""
        # Return all visible cards to pool
        for card in self._visible_cards.values():
            self._return_card(card)
        self._visible_cards.clear()
        self._path_to_cards.clear()
        
        # Remove any remaining widgets
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget and widget not in self._card_pool:
                widget.hide()

    def _rebuild_grid(self):
        """Rebuild the grid with current filtered entries."""
        self._clear_grid()
        
        if not self._filtered_entries:
            placeholder = QLabel("No monsters available.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid_layout.addWidget(placeholder, 0, 0)
            return

        visible_tokens = {entry.token for entry in self._filtered_entries}
        self._expanded_tokens.intersection_update(visible_tokens)

        self._display_payloads = []
        for entry in self._filtered_entries:
            self._display_payloads.append((entry, None))
            if entry.token in self._expanded_tokens and entry.variants:
                for variant in entry.variants:
                    self._display_payloads.append((entry, variant))

        columns = max(1, self._columns)
        
        # Create cards for all payloads (base entry + expanded variants)
        for idx, payload in enumerate(self._display_payloads):
            entry, variant = payload
            row = idx // columns
            col = idx % columns
            
            card = self._get_card()
            can_expand = variant is None and entry.has_variants()
            expanded = entry.token in self._expanded_tokens
            card.set_entry(entry, variant, can_expand=can_expand, expanded=expanded)
            card.show()
            
            self.grid_layout.addWidget(card, row, col)
            self._visible_cards[idx] = card
            
            # Track path to card mapping
            path = entry.image_path
            if path:
                if path not in self._path_to_cards:
                    self._path_to_cards[path] = []
                self._path_to_cards[path].append(card)

        # Add column stretch
        for col in range(columns):
            self.grid_layout.setColumnStretch(col, 1)
        
        # Schedule lazy loading
        self._lazy_load_timer.start()

    def _on_scroll(self):
        """Handle scroll events to trigger lazy loading."""
        self._lazy_load_timer.start()

    def _load_visible_thumbnails(self):
        """Load thumbnails for currently visible cards."""
        if not self._visible_cards:
            return
        
        viewport = self.scroll_area.viewport()
        if not viewport:
            return
        
        viewport_rect = viewport.rect()
        scroll_pos = self.scroll_area.verticalScrollBar().value()
        
        # Calculate visible area in grid coordinates
        visible_top = scroll_pos
        visible_bottom = scroll_pos + viewport_rect.height()
        
        # Check each card for visibility
        for idx, card in self._visible_cards.items():
            if not card.needs_thumbnail():
                continue
            
            # Get card position relative to scroll area
            card_pos = card.mapTo(self.grid_container, card.rect().topLeft())
            card_top = card_pos.y()
            card_bottom = card_top + card.height()
            
            # Check if card is visible (with some margin for preloading)
            margin = 200  # Preload cards slightly outside viewport
            if card_bottom >= visible_top - margin and card_top <= visible_bottom + margin:
                image_path = card.get_image_path()
                if image_path:
                    # Check cache first
                    cached = self._thumbnail_loader.get_cached(image_path)
                    if cached:
                        decorated = self._apply_entry_thumbnail_overlay(
                            card.entry,
                            card.variant_option,
                            image_path,
                            cached,
                        )
                        card.set_thumbnail(decorated)
                    else:
                        self._thumbnail_loader.request_thumbnail(image_path)

    def _on_thumbnail_ready(self, image_path: str, pixmap: QPixmap):
        """Handle thumbnail loaded from background thread."""
        cards = self._path_to_cards.get(image_path, [])
        for card in cards:
            if card.entry and card.entry.image_path == image_path:
                decorated = self._apply_entry_thumbnail_overlay(
                    card.entry,
                    card.variant_option,
                    image_path,
                    pixmap,
                )
                card.set_thumbnail(decorated)

    def _handle_card_clicked(
        self,
        entry: MonsterBrowserEntry,
        variant: Optional[MonsterVariantOption],
        action: str,
    ):
        """Handle card click or expansion toggle."""
        if action == "toggle" and variant is None and entry.has_variants():
            token = entry.token
            if token in self._expanded_tokens:
                self._expanded_tokens.remove(token)
            else:
                self._expanded_tokens.add(token)
            self._rebuild_grid()
            return
        self._select_entry(entry, variant)

    def _select_entry(
        self,
        entry: MonsterBrowserEntry,
        variant: Optional[MonsterVariantOption],
    ):
        """Finalize selection for either the base entry or a variant."""
        if variant:
            variant_label = variant.variant_label or variant.display_name or entry.display_name
            display_name = f"{entry.display_name} ({variant_label})"
            selection = MonsterBrowserEntry(
                token=entry.token,
                display_name=display_name,
                relative_path=variant.relative_path or entry.relative_path,
                image_path=entry.image_path,
                json_path=variant.json_path,
                bin_path=variant.bin_path,
                variants=[],
                island_numbers=list(entry.island_numbers),
                island_labels=list(entry.island_labels),
                class_name=entry.class_name,
                fam_name=entry.fam_name,
                genes=entry.genes,
                gene_graphics=list(entry.gene_graphics),
                entity_type=entry.entity_type,
                variant_types=list(entry.variant_types),
                has_downloads_source=entry.has_downloads_source,
                has_game_source=entry.has_game_source,
            )
        else:
            selection = entry
        self.selected_entry = selection
        self.accept()

    def force_reexport(self) -> bool:
        """Check if re-export is requested."""
        return self.force_reexport_check.isChecked()

    def column_count(self) -> int:
        """Get current column count."""
        return self._columns

    def closeEvent(self, event):
        """Clean up on close."""
        self._thumbnail_loader.shutdown()
        super().closeEvent(event)
    
    def reject(self):
        """Clean up on reject."""
        self._thumbnail_loader.shutdown()
        super().reject()
    
    def accept(self):
        """Clean up on accept."""
        self._thumbnail_loader.shutdown()
        super().accept()
