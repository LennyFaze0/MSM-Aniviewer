"""
File Loader
Utilities for loading and parsing animation JSON files
"""

import json
from typing import Dict, Optional


def load_json_animation(json_path: str) -> Optional[Dict]:
    """
    Load animation data from JSON file
    
    Args:
        json_path: Path to the JSON file
    
    Returns:
        Dictionary containing animation data, or None if failed
    """
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"Error loading JSON file: {e}")
        return None
