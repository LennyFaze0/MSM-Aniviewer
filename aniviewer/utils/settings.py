"""
Settings Manager
Handles application settings persistence
"""

from PyQt6.QtCore import QSettings


class SettingsManager:
    """Manages application settings"""
    
    def __init__(self):
        self.settings = QSettings('MSMAnimationViewer', 'Settings')
    
    def get_game_path(self) -> str:
        """Get the saved game path"""
        return self.settings.value('game_path', '')
    
    def set_game_path(self, path: str):
        """Save the game path"""
        self.settings.setValue('game_path', path)
    
    def get_last_file(self) -> str:
        """Get the last opened file"""
        return self.settings.value('last_file', '')
    
    def set_last_file(self, filename: str):
        """Save the last opened file"""
        self.settings.setValue('last_file', filename)
    
    def get_window_geometry(self):
        """Get saved window geometry"""
        return self.settings.value('window_geometry')
    
    def set_window_geometry(self, geometry):
        """Save window geometry"""
        self.settings.setValue('window_geometry', geometry)
    
    def get_window_state(self):
        """Get saved window state"""
        return self.settings.value('window_state')
    
    def set_window_state(self, state):
        """Save window state"""
        self.settings.setValue('window_state', state)
