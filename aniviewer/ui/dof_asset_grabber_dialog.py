"""
Dawn of Fire Asset Grabber
Standalone remote bundle downloader UI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QSettings, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressDialog,
    QVBoxLayout,
    QWidget,
)

from utils.dof_bundle_remote import (
    DEFAULT_DOF_CDN_BASE_URL,
    DofBundleManifestEntry,
    DofBundleRemoteError,
    download_dof_bundles,
    fetch_dof_bundle_manifest,
    normalize_dof_bundle_platform,
    normalize_dof_cdn_base_url,
)


class DofAssetGrabberDialog(QDialog):
    """Standalone UI for downloading DOF bundles from remote manifests."""

    download_completed = pyqtSignal(str, int)

    def __init__(self, settings: QSettings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = settings
        self._manifest_entries: List[DofBundleManifestEntry] = []
        self._manifest_url: str = ""
        self._busy: bool = False

        self.setWindowTitle("Dawn of Fire Asset Grabber")
        self.setMinimumWidth(900)
        self.setMinimumHeight(640)

        self._build_ui()
        self._load_saved_state()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        intro = QLabel(
            "Load a build manifest, select bundles, and download directly into your DOF assets root."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray; font-size: 9pt;")
        root.addWidget(intro)

        source_group = QGroupBox("Manifest Source")
        source_form = QFormLayout()

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText(DEFAULT_DOF_CDN_BASE_URL)
        source_form.addRow("CDN Base URL:", self.base_url_edit)

        key_row = QHBoxLayout()
        self.build_key_combo = QComboBox()
        self.build_key_combo.setEditable(True)
        self.build_key_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.build_key_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        key_row.addWidget(self.build_key_combo, 1)

        self.platform_combo = QComboBox()
        self.platform_combo.addItem("iOS", "ios")
        self.platform_combo.addItem("Android", "android")
        key_row.addWidget(self.platform_combo, 0)

        self.load_manifest_btn = QPushButton("Load Manifest")
        self.load_manifest_btn.clicked.connect(self._load_manifest)
        key_row.addWidget(self.load_manifest_btn, 0)

        self.try_saved_keys_btn = QPushButton("Try Saved Keys")
        self.try_saved_keys_btn.clicked.connect(self._try_saved_keys)
        key_row.addWidget(self.try_saved_keys_btn, 0)

        source_form.addRow("Build Key / Platform:", key_row)

        self.manifest_status_label = QLabel("Manifest not loaded.")
        self.manifest_status_label.setWordWrap(True)
        self.manifest_status_label.setStyleSheet("color: gray; font-size: 9pt;")
        source_form.addRow("", self.manifest_status_label)

        source_group.setLayout(source_form)
        root.addWidget(source_group)

        destination_group = QGroupBox("Destination")
        destination_form = QFormLayout()
        root_row = QHBoxLayout()
        self.dof_root_edit = QLineEdit()
        self.dof_root_edit.setPlaceholderText("Select DOF assets root (folder containing msmdata/ or bundles)")
        root_row.addWidget(self.dof_root_edit, 1)
        self.browse_root_btn = QPushButton("Browse...")
        self.browse_root_btn.clicked.connect(self._browse_dof_root)
        root_row.addWidget(self.browse_root_btn, 0)
        destination_form.addRow("DOF Assets Root:", root_row)
        destination_group.setLayout(destination_form)
        root.addWidget(destination_group)

        bundles_group = QGroupBox("Available Files")
        bundles_layout = QVBoxLayout()

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type part of bundle name (e.g. staticinfo_, tweedle, sounds)")
        self.filter_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.filter_edit, 1)
        self.staticinfo_only_check = QCheckBox("Staticinfo only")
        self.staticinfo_only_check.setChecked(True)
        self.staticinfo_only_check.toggled.connect(self._apply_filters)
        filter_row.addWidget(self.staticinfo_only_check, 0)
        bundles_layout.addLayout(filter_row)

        self.bundle_list = QListWidget()
        self.bundle_list.itemChanged.connect(self._update_counts)
        bundles_layout.addWidget(self.bundle_list, 1)

        select_row = QHBoxLayout()
        self.select_visible_btn = QPushButton("Select Visible")
        self.select_visible_btn.clicked.connect(self._select_visible)
        select_row.addWidget(self.select_visible_btn)
        self.clear_visible_btn = QPushButton("Clear Visible")
        self.clear_visible_btn.clicked.connect(self._clear_visible)
        select_row.addWidget(self.clear_visible_btn)
        self.invert_visible_btn = QPushButton("Invert Visible")
        self.invert_visible_btn.clicked.connect(self._invert_visible)
        select_row.addWidget(self.invert_visible_btn)
        select_row.addStretch(1)
        bundles_layout.addLayout(select_row)

        self.counts_label = QLabel("Visible: 0 | Checked: 0 | Total: 0")
        self.counts_label.setStyleSheet("color: gray; font-size: 9pt;")
        bundles_layout.addWidget(self.counts_label)

        bundles_group.setLayout(bundles_layout)
        root.addWidget(bundles_group, 1)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.download_checked_btn = QPushButton("Download Checked")
        self.download_checked_btn.clicked.connect(lambda: self._download(download_all_visible=False))
        action_row.addWidget(self.download_checked_btn)
        self.download_all_visible_btn = QPushButton("Download All Visible")
        self.download_all_visible_btn.clicked.connect(lambda: self._download(download_all_visible=True))
        action_row.addWidget(self.download_all_visible_btn)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        action_row.addWidget(self.close_btn)
        root.addLayout(action_row)

    def _load_saved_state(self) -> None:
        base_url = self.settings.value("dof/remote_base_url", DEFAULT_DOF_CDN_BASE_URL, type=str) or DEFAULT_DOF_CDN_BASE_URL
        self.base_url_edit.setText(normalize_dof_cdn_base_url(base_url))

        platform = normalize_dof_bundle_platform(self.settings.value("dof/remote_platform", "ios", type=str) or "ios")
        platform_idx = self.platform_combo.findData(platform)
        if platform_idx < 0:
            platform_idx = 0
        self.platform_combo.setCurrentIndex(platform_idx)

        preferred_key = self.settings.value("dof/remote_build_key", "", type=str) or ""
        self._populate_build_keys(preferred_key=preferred_key)

        dof_root = self.settings.value("dof_path", "", type=str) or ""
        self.dof_root_edit.setText(str(dof_root).strip())

        self._refresh_manifest_list()

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        enabled = not self._busy
        self.base_url_edit.setEnabled(enabled)
        self.build_key_combo.setEnabled(enabled)
        self.platform_combo.setEnabled(enabled)
        self.load_manifest_btn.setEnabled(enabled)
        self.try_saved_keys_btn.setEnabled(enabled)
        self.dof_root_edit.setEnabled(enabled)
        self.browse_root_btn.setEnabled(enabled)
        self.filter_edit.setEnabled(enabled)
        self.staticinfo_only_check.setEnabled(enabled)
        self.bundle_list.setEnabled(enabled)
        self.select_visible_btn.setEnabled(enabled)
        self.clear_visible_btn.setEnabled(enabled)
        self.invert_visible_btn.setEnabled(enabled)
        self.download_checked_btn.setEnabled(enabled)
        self.download_all_visible_btn.setEnabled(enabled)
        self.close_btn.setEnabled(enabled)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor if self._busy else Qt.CursorShape.ArrowCursor)
        if not self._busy:
            QApplication.restoreOverrideCursor()

    def _current_build_key(self) -> str:
        return str(self.build_key_combo.currentText() or "").strip()

    def _read_build_history(self) -> List[Dict[str, str]]:
        raw = self.settings.value("dof/remote_build_history_json", "", type=str) or ""
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            return []
        if not isinstance(parsed, list):
            return []
        out: List[Dict[str, str]] = []
        seen: set[str] = set()
        for item in parsed:
            if not isinstance(item, dict):
                continue
            key = str(item.get("build_key") or "").strip()
            if not key:
                continue
            platform = normalize_dof_bundle_platform(item.get("platform"))
            dedupe = f"{key}|{platform}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out.append({"build_key": key, "platform": platform})
        return out

    def _write_build_history(self, history: List[Dict[str, str]]) -> None:
        self.settings.setValue("dof/remote_build_history_json", json.dumps(history))

    def _populate_build_keys(self, preferred_key: str = "") -> None:
        history = self._read_build_history()
        keys: List[str] = []
        seen: set[str] = set()
        initial = preferred_key.strip() or self._current_build_key()
        if initial:
            keys.append(initial)
            seen.add(initial.lower())
        for item in history:
            key = str(item.get("build_key") or "").strip()
            if not key:
                continue
            marker = key.lower()
            if marker in seen:
                continue
            seen.add(marker)
            keys.append(key)

        self.build_key_combo.blockSignals(True)
        self.build_key_combo.clear()
        for key in keys:
            self.build_key_combo.addItem(key, key)
        self.build_key_combo.blockSignals(False)
        self.build_key_combo.setEditText(initial)

    def _remember_build_key(self, build_key: str, platform: str) -> None:
        key = str(build_key or "").strip()
        if not key:
            return
        platform_norm = normalize_dof_bundle_platform(platform)
        existing = self._read_build_history()
        updated: List[Dict[str, str]] = [{"build_key": key, "platform": platform_norm}]
        for item in existing:
            old_key = str(item.get("build_key") or "").strip()
            old_platform = normalize_dof_bundle_platform(item.get("platform"))
            if not old_key:
                continue
            if old_key == key and old_platform == platform_norm:
                continue
            updated.append({"build_key": old_key, "platform": old_platform})
        updated = updated[:30]
        self._write_build_history(updated)
        self._populate_build_keys(preferred_key=key)

    def _browse_dof_root(self) -> None:
        start_dir = self.dof_root_edit.text().strip() or self.settings.value("dof_path", "", type=str) or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select DOF Assets Root", str(start_dir))
        if not folder:
            return
        self.dof_root_edit.setText(folder)
        self.settings.setValue("dof_path", folder)

    def _load_manifest(self) -> None:
        base_url = normalize_dof_cdn_base_url(self.base_url_edit.text())
        build_key = self._current_build_key()
        platform = normalize_dof_bundle_platform(str(self.platform_combo.currentData() or "ios"))
        if not build_key:
            QMessageBox.warning(self, "Missing Build Key", "Enter a DOF build key first.")
            return

        self._set_busy(True)
        try:
            manifest_url, entries = fetch_dof_bundle_manifest(base_url=base_url, build_key=build_key, platform=platform)
        except DofBundleRemoteError as exc:
            self._set_busy(False)
            QMessageBox.warning(self, "Manifest Load Failed", str(exc))
            self.manifest_status_label.setText(str(exc))
            return
        self._set_busy(False)

        self._manifest_url = manifest_url
        self._manifest_entries = entries
        self._remember_build_key(build_key, platform)
        self.settings.setValue("dof/remote_base_url", base_url)
        self.settings.setValue("dof/remote_platform", platform)
        self.settings.setValue("dof/remote_build_key", build_key)

        self.manifest_status_label.setText(f"Loaded {len(entries)} bundles from {manifest_url}")
        self._refresh_manifest_list()

    def _try_saved_keys(self) -> None:
        base_url = normalize_dof_cdn_base_url(self.base_url_edit.text())
        platform = normalize_dof_bundle_platform(str(self.platform_combo.currentData() or "ios"))
        history = self._read_build_history()
        keys = [self._current_build_key()] + [item.get("build_key", "") for item in history]
        tried: set[str] = set()
        for raw_key in keys:
            key = str(raw_key or "").strip()
            if not key:
                continue
            marker = key.lower()
            if marker in tried:
                continue
            tried.add(marker)
            try:
                manifest_url, entries = fetch_dof_bundle_manifest(base_url=base_url, build_key=key, platform=platform)
            except DofBundleRemoteError:
                continue
            self.build_key_combo.setEditText(key)
            self._manifest_url = manifest_url
            self._manifest_entries = entries
            self._remember_build_key(key, platform)
            self.settings.setValue("dof/remote_base_url", base_url)
            self.settings.setValue("dof/remote_platform", platform)
            self.settings.setValue("dof/remote_build_key", key)
            self.manifest_status_label.setText(f"Loaded {len(entries)} bundles from {manifest_url}")
            self._refresh_manifest_list()
            return
        QMessageBox.warning(
            self,
            "No Saved Key Worked",
            "None of the current/saved keys returned a valid manifest for the selected platform.",
        )

    def _refresh_manifest_list(self) -> None:
        checked_before = set(self._checked_bundle_names())
        self.bundle_list.blockSignals(True)
        self.bundle_list.clear()

        for entry in self._manifest_entries:
            label = f"{entry.name}  ({self._format_size(entry.size)})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry.name)
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.size)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            default_checked = entry.name.lower().startswith("staticinfo_")
            checked = entry.name in checked_before or (not checked_before and default_checked)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.bundle_list.addItem(item)

        if self.bundle_list.count() <= 0:
            placeholder = QListWidgetItem("No bundles loaded")
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.bundle_list.addItem(placeholder)

        self.bundle_list.blockSignals(False)
        self._apply_filters()

    def _apply_filters(self) -> None:
        query = (self.filter_edit.text() or "").strip().lower()
        static_only = bool(self.staticinfo_only_check.isChecked())
        for index in range(self.bundle_list.count()):
            item = self.bundle_list.item(index)
            if item is None:
                continue
            name = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if not name:
                item.setHidden(False)
                continue
            hide = False
            if query and query not in name.lower():
                hide = True
            if static_only and not name.lower().startswith("staticinfo_"):
                hide = True
            item.setHidden(hide)
        self._update_counts()

    def _visible_items(self) -> List[QListWidgetItem]:
        items: List[QListWidgetItem] = []
        for index in range(self.bundle_list.count()):
            item = self.bundle_list.item(index)
            if item is None:
                continue
            if item.isHidden():
                continue
            if not item.data(Qt.ItemDataRole.UserRole):
                continue
            items.append(item)
        return items

    def _checked_bundle_names(self) -> List[str]:
        names: List[str] = []
        for index in range(self.bundle_list.count()):
            item = self.bundle_list.item(index)
            if item is None:
                continue
            name = item.data(Qt.ItemDataRole.UserRole)
            if not name:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                names.append(str(name))
        return names

    def _checked_visible_bundle_names(self) -> List[str]:
        names: List[str] = []
        for item in self._visible_items():
            name = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if not name:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                names.append(name)
        return names

    def _visible_bundle_names(self) -> List[str]:
        out: List[str] = []
        for item in self._visible_items():
            name = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if name:
                out.append(name)
        return out

    def _select_visible(self) -> None:
        self.bundle_list.blockSignals(True)
        for item in self._visible_items():
            item.setCheckState(Qt.CheckState.Checked)
        self.bundle_list.blockSignals(False)
        self._update_counts()

    def _clear_visible(self) -> None:
        self.bundle_list.blockSignals(True)
        for item in self._visible_items():
            item.setCheckState(Qt.CheckState.Unchecked)
        self.bundle_list.blockSignals(False)
        self._update_counts()

    def _invert_visible(self) -> None:
        self.bundle_list.blockSignals(True)
        for item in self._visible_items():
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
        self.bundle_list.blockSignals(False)
        self._update_counts()

    def _update_counts(self) -> None:
        visible = len(self._visible_items())
        checked_visible = len(self._checked_visible_bundle_names())
        total = len(self._manifest_entries)
        self.counts_label.setText(f"Visible: {visible} | Checked: {checked_visible} | Total: {total}")

    def _download(self, *, download_all_visible: bool) -> None:
        if not self._manifest_entries:
            QMessageBox.information(self, "No Manifest Loaded", "Load a manifest first.")
            return
        root = self.dof_root_edit.text().strip()
        if not root:
            self._browse_dof_root()
            root = self.dof_root_edit.text().strip()
        if not root:
            return
        if not os.path.isdir(root):
            QMessageBox.warning(self, "Invalid Root", "DOF assets root is not a valid directory.")
            return

        build_key = self._current_build_key()
        platform = normalize_dof_bundle_platform(str(self.platform_combo.currentData() or "ios"))
        if not build_key:
            QMessageBox.warning(self, "Missing Build Key", "Enter a DOF build key first.")
            return

        bundle_names = self._visible_bundle_names() if download_all_visible else self._checked_visible_bundle_names()
        if not bundle_names:
            QMessageBox.information(
                self,
                "No Bundles Selected",
                "There are no bundles selected/visible to download.",
            )
            return

        target_dir = os.path.join(root, "remote_dlc", build_key, platform)
        progress = QProgressDialog("Downloading bundles...", None, 0, len(bundle_names), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        self._set_busy(True)
        downloaded_count = 0
        failed_name: Optional[str] = None
        failed_error: Optional[str] = None
        try:
            for idx, name in enumerate(bundle_names):
                progress.setLabelText(f"Downloading {name} ({idx + 1}/{len(bundle_names)})...")
                QApplication.processEvents()
                try:
                    download_dof_bundles(
                        self._manifest_entries,
                        target_dir=target_dir,
                        selected_names=[name],
                        skip_existing=True,
                    )
                    downloaded_count += 1
                except DofBundleRemoteError as exc:
                    failed_name = name
                    failed_error = str(exc)
                    break
                progress.setValue(idx + 1)
        finally:
            self._set_busy(False)
            progress.close()

        if failed_error:
            QMessageBox.warning(
                self,
                "Download Failed",
                f"Failed while downloading '{failed_name or 'bundle'}':\n{failed_error}",
            )
            return

        self.settings.setValue("dof_path", root)
        self.settings.setValue("dof/remote_build_key", build_key)
        self.settings.setValue("dof/remote_platform", platform)
        self.settings.setValue("dof/remote_base_url", normalize_dof_cdn_base_url(self.base_url_edit.text()))
        self._remember_build_key(build_key, platform)
        self.download_completed.emit(root, downloaded_count)
        QMessageBox.information(
            self,
            "Download Complete",
            f"Downloaded/verified {downloaded_count} bundle(s) into:\n{target_dir}",
        )

    @staticmethod
    def _format_size(size: int) -> str:
        if size <= 0:
            return "unknown"
        value = float(size)
        for suffix in ("B", "KB", "MB", "GB"):
            if value < 1024.0 or suffix == "GB":
                if suffix == "B":
                    return f"{int(value)} {suffix}"
                return f"{value:.2f} {suffix}"
            value /= 1024.0
        return f"{size} B"

