"""
Core module for MSM Animation Viewer
Contains data structures, animation logic, and texture management
"""

from .data_structures import (
    SpriteInfo,
    KeyframeData,
    LayerData,
    AnimationData
)
from .animation_player import AnimationPlayer
from .texture_atlas import TextureAtlas
from .transform import (
    create_translation_matrix,
    create_rotation_matrix,
    create_scale_matrix,
    matrix_multiply
)

__all__ = [
    'SpriteInfo',
    'KeyframeData',
    'LayerData',
    'AnimationData',
    'AnimationPlayer',
    'TextureAtlas',
    'create_translation_matrix',
    'create_rotation_matrix',
    'create_scale_matrix',
    'matrix_multiply',
]
