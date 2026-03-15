"""
Transform matrix utilities for 3D transformations
Provides functions to create and manipulate 4x4 transformation matrices
"""

import math
import numpy as np


def create_translation_matrix(x: float, y: float, z: float = 0) -> np.ndarray:
    """
    Create a 4x4 translation matrix
    
    Args:
        x: Translation along X axis
        y: Translation along Y axis
        z: Translation along Z axis (default: 0)
    
    Returns:
        4x4 numpy array representing the translation matrix
    """
    return np.array([
        [1, 0, 0, x],
        [0, 1, 0, y],
        [0, 0, 1, z],
        [0, 0, 0, 1]
    ], dtype=np.float32)


def create_rotation_matrix(angle_degrees: float) -> np.ndarray:
    """
    Create a 4x4 rotation matrix around Z axis
    
    Args:
        angle_degrees: Rotation angle in degrees
    
    Returns:
        4x4 numpy array representing the rotation matrix
    """
    angle_rad = math.radians(angle_degrees)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return np.array([
        [cos_a, -sin_a, 0, 0],
        [sin_a, cos_a, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 1]
    ], dtype=np.float32)


def create_scale_matrix(sx: float, sy: float, sz: float = 1) -> np.ndarray:
    """
    Create a 4x4 scale matrix
    
    Args:
        sx: Scale factor along X axis
        sy: Scale factor along Y axis
        sz: Scale factor along Z axis (default: 1)
    
    Returns:
        4x4 numpy array representing the scale matrix
    """
    return np.array([
        [sx, 0, 0, 0],
        [0, sy, 0, 0],
        [0, 0, sz, 0],
        [0, 0, 0, 1]
    ], dtype=np.float32)


def matrix_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Multiply two 4x4 matrices
    
    Args:
        a: First matrix
        b: Second matrix
    
    Returns:
        Result of matrix multiplication
    """
    return np.dot(a, b)
