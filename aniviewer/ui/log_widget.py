"""
Log Widget
Displays log messages with color-coded severity levels
"""

from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtGui import QKeySequence
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
    
    def log(self, message: str, level: str = "INFO"):
        """
        Add a log message
        
        Args:
            message: Message to log
            level: Severity level (INFO, WARNING, ERROR, SUCCESS)
        """
        color = {
            "INFO": "black",
            "WARNING": "orange",
            "ERROR": "red",
            "SUCCESS": "green"
        }.get(level, "black")
        
        self.append(f'<span style="color: {color};">[{level}] {message}</span>')
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
