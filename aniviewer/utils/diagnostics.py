from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QTimer


@dataclass
class DiagnosticsConfig:
    """User-configurable toggles for runtime diagnostics."""

    enabled: bool = False
    highlight_layers: bool = True
    throttle_updates: bool = True
    log_clone_events: bool = True
    log_canonical_events: bool = True
    log_remap_events: bool = False
    log_sheet_events: bool = False
    log_visibility_events: bool = False
    log_shader_events: bool = False
    log_color_events: bool = False
    log_attachment_events: bool = False
    include_debug_payloads: bool = False
    max_entries: int = 2000
    update_interval_ms: int = 500
    layer_status_duration_sec: float = 6.0
    rate_limit_per_sec: int = 120
    minimum_severity: str = "INFO"
    auto_export_enabled: bool = False
    auto_export_interval_sec: int = 120
    export_path: str = ""


class DiagnosticsManager(QObject):
    """Collects runtime diagnostics and mirrors them in the UI."""

    SEVERITY_ORDER = {
        "DEBUG": 0,
        "INFO": 1,
        "SUCCESS": 1,
        "WARNING": 2,
        "ERROR": 3,
    }

    CATEGORY_FLAGS = {
        "clone": "log_clone_events",
        "canonical": "log_canonical_events",
        "remap": "log_remap_events",
        "sheet": "log_sheet_events",
        "visibility": "log_visibility_events",
        "shader": "log_shader_events",
        "color": "log_color_events",
        "attachment": "log_attachment_events",
        "general": None,
    }

    def __init__(self, layer_panel, log_widget, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.layer_panel = layer_panel
        self.log_widget = log_widget
        self.config = DiagnosticsConfig()
        self.events: Deque[Dict[str, object]] = deque()
        self._layer_status: Dict[int, Dict[str, object]] = {}
        self._rate_window: Deque[datetime] = deque()
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._flush_layer_statuses)
        self._export_timer = QTimer(self)
        self._export_timer.timeout.connect(self._auto_export)
        self._pending_export_path: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #
    def apply_config(self, config: DiagnosticsConfig):
        """Apply user preferences."""
        self.config = config
        self._update_timer.setInterval(max(50, config.update_interval_ms))
        if config.enabled and config.throttle_updates:
            self._update_timer.start()
        else:
            self._update_timer.stop()

        if config.enabled and config.auto_export_enabled and config.export_path:
            self._export_timer.setInterval(max(5_000, config.auto_export_interval_sec * 1000))
            self._export_timer.start()
        else:
            self._export_timer.stop()

        if not config.enabled:
            self._layer_status.clear()
            self.events.clear()
            if self.layer_panel:
                self.layer_panel.clear_layer_statuses()

    # ------------------------------------------------------------------ #
    # Logging helpers
    # ------------------------------------------------------------------ #
    def log_clone(self, message: str, *, layer_id: Optional[int] = None,
                  severity: str = "INFO", extra: Optional[Dict[str, object]] = None):
        self._log("clone", message, layer_id=layer_id, severity=severity, extra=extra)

    def log_canonical(self, message: str, *, layer_id: Optional[int] = None,
                      severity: str = "INFO", extra: Optional[Dict[str, object]] = None):
        self._log("canonical", message, layer_id=layer_id, severity=severity, extra=extra)

    def log_remap(self, message: str, *, layer_id: Optional[int] = None,
                  severity: str = "INFO", extra: Optional[Dict[str, object]] = None):
        self._log("remap", message, layer_id=layer_id, severity=severity, extra=extra)

    def log_visibility(self, message: str, *, layer_id: Optional[int] = None,
                       severity: str = "INFO", extra: Optional[Dict[str, object]] = None):
        self._log("visibility", message, layer_id=layer_id, severity=severity, extra=extra)

    def log_sheet(self, message: str, *, layer_id: Optional[int] = None,
                  severity: str = "INFO", extra: Optional[Dict[str, object]] = None):
        self._log("sheet", message, layer_id=layer_id, severity=severity, extra=extra)

    def log_shader(self, message: str, *, layer_id: Optional[int] = None,
                   severity: str = "INFO", extra: Optional[Dict[str, object]] = None):
        self._log("shader", message, layer_id=layer_id, severity=severity, extra=extra)

    def log_color(self, message: str, *, layer_id: Optional[int] = None,
                  severity: str = "INFO", extra: Optional[Dict[str, object]] = None):
        self._log("color", message, layer_id=layer_id, severity=severity, extra=extra)

    def log_attachment(self, message: str, *, layer_id: Optional[int] = None,
                       severity: str = "INFO", extra: Optional[Dict[str, object]] = None):
        self._log("attachment", message, layer_id=layer_id, severity=severity, extra=extra)

    def log_general(self, message: str, *, layer_id: Optional[int] = None,
                    severity: str = "INFO", extra: Optional[Dict[str, object]] = None):
        self._log("general", message, layer_id=layer_id, severity=severity, extra=extra)

    def _log(
        self,
        category: str,
        message: str,
        *,
        layer_id: Optional[int],
        severity: str,
        extra: Optional[Dict[str, object]],
    ):
        cfg = self.config
        if not cfg.enabled:
            return

        flag_name = self.CATEGORY_FLAGS.get(category)
        if flag_name and not getattr(cfg, flag_name, False):
            return

        if self.SEVERITY_ORDER.get(severity, 0) < self.SEVERITY_ORDER.get(cfg.minimum_severity, 0):
            return

        now = datetime.utcnow()
        self._rate_window.append(now)
        window_start = now - timedelta(seconds=1)
        while self._rate_window and self._rate_window[0] < window_start:
            self._rate_window.popleft()
        if cfg.rate_limit_per_sec and len(self._rate_window) > cfg.rate_limit_per_sec:
            return

        payload = {
            "timestamp": now.isoformat(timespec="milliseconds") + "Z",
            "category": category,
            "severity": severity,
            "message": message,
        }
        if cfg.include_debug_payloads and extra:
            payload["extra"] = extra
        if layer_id is not None:
            payload["layer_id"] = layer_id
        self.events.append(payload)
        while len(self.events) > cfg.max_entries:
            self.events.popleft()

        if self.log_widget:
            prefix = f"Diag/{category}"
            self.log_widget.log(f"{prefix}: {message}", level=severity)

        if layer_id is not None and cfg.highlight_layers and self.layer_panel:
            expires_at = now + timedelta(seconds=cfg.layer_status_duration_sec)
            self._layer_status[layer_id] = {
                "text": message,
                "severity": severity,
                "expires": expires_at,
            }
            if not cfg.throttle_updates:
                self._flush_layer_statuses()

    # ------------------------------------------------------------------ #
    # Export / persistence helpers
    # ------------------------------------------------------------------ #
    def export_to_file(self, filepath: str) -> Tuple[bool, str]:
        """Persist the in-memory diagnostics log."""
        if not filepath:
            return False, "No export path specified."
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as handle:
                for event in list(self.events):
                    line = f"[{event['timestamp']}] {event['severity']} {event['category']}: {event['message']}"
                    if self.config.include_debug_payloads and "extra" in event:
                        line += f" | {json.dumps(event['extra'], ensure_ascii=False)}"
                    handle.write(line + "\n")
            return True, f"Diagnostics exported to {filepath}"
        except Exception as exc:  # pragma: no cover - UI handles message
            return False, f"Failed to export diagnostics: {exc}"

    def _auto_export(self):
        if not (self.config.enabled and self.config.auto_export_enabled and self.config.export_path):
            return
        base = self.config.export_path
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        if os.path.isdir(base):
            filename = os.path.join(base, f"diagnostics-{timestamp}.log")
        else:
            root, ext = os.path.splitext(base)
            filename = f"{root}-{timestamp}{ext or '.log'}"
        success, _ = self.export_to_file(filename)
        if success:
            self._pending_export_path = filename

    # ------------------------------------------------------------------ #
    # UI sync helpers
    # ------------------------------------------------------------------ #
    def _flush_layer_statuses(self):
        if not self.layer_panel or not self._layer_status:
            return
        now = datetime.utcnow()
        expired = [layer_id for layer_id, payload in self._layer_status.items()
                   if payload["expires"] <= now]
        for layer_id in expired:
            self._layer_status.pop(layer_id, None)
            self.layer_panel.update_layer_status(layer_id, "", "")
        for layer_id, payload in list(self._layer_status.items()):
            self.layer_panel.update_layer_status(layer_id, payload["text"], payload["severity"])

    def clear(self):
        self.events.clear()
        self._layer_status.clear()
        if self.layer_panel:
            self.layer_panel.clear_layer_statuses()

    def refresh_layer_statuses(self):
        """Force an immediate status update."""
        self._flush_layer_statuses()
