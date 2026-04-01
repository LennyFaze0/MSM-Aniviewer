"""
Log Widget
Displays log messages with color-coded severity levels
"""

from html import escape

from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtGui import QKeySequence, QPalette
from PyQt6.QtCore import Qt


class LogWidget(QTextEdit):
    """Widget for displaying logs"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumHeight(150)
        self.setUndoRedoEnabled(False)
    
    def keyPressEvent(self, event):
        """Let global shortcuts like Ctrl+Shift+Z bubble up to the main window."""
        if event.matches(QKeySequence.StandardKey.Redo) or (
            event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            and event.key() == Qt.Key.Key_Z
        ):
            event.ignore()
            return
        super().keyPressEvent(event)

    def _is_dark_mode(self) -> bool:
        """Determine whether the current widget palette is dark."""
        base = self.palette().color(QPalette.ColorRole.Base)
        luminance = (base.red() * 299 + base.green() * 587 + base.blue() * 114) / 1000
        return luminance < 128

    def _color_for_level(self, level: str) -> str:
        """Choose readable colors for each level in both light and dark themes."""
        is_dark = self._is_dark_mode()
        if level == "WARNING":
            return "#FFB74D" if is_dark else "#A65A00"
        if level == "ERROR":
            return "#FF6E6E" if is_dark else "#B00020"
        if level == "SUCCESS":
            return "#7DFF9E" if is_dark else "#2E7D32"
        # INFO / default text
        return "#FFFFFF" if is_dark else "#000000"
    
    def log(self, message: str, level: str = "INFO"):
        """
        Add a log message
        
        Args:
            message: Message to log
            level: Severity level (INFO, WARNING, ERROR, SUCCESS)
        """
        normalized_level = str(level).upper()
        color = self._color_for_level(normalized_level)
        safe_level = escape(normalized_level)
        safe_message = escape(str(message))

        self.append(f'<span style="color: {color};">[{safe_level}] {safe_message}</span>')
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
