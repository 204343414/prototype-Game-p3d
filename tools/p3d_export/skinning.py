"""Small, format-specific helpers shared by the Prototype glTF exporters."""

import numpy as np


def p3d_stored_weights_to_gltf(stored_weights: np.ndarray) -> np.ndarray:
    """Return glTF WEIGHTS_0 slots for P3D's three stored blend weights.

    In Prototype's vertex record, the three serialized float weights align with
    the first three serialized matrix-index bytes.  The fourth aligned weight
    is implicit: ``1 - sum(stored_weights)``.  Thus the glTF slot order is
    ``[stored[0], stored[1], stored[2], implicit]``.

    Keeping that alignment matters only once a skeleton is posed: bind-pose
    rendering cannot expose an incorrect ordering because every skin matrix is
    then identity.
    """
    stored = np.asarray(stored_weights)
    if stored.ndim != 2 or stored.shape[1] != 3:
        raise ValueError(f"expected shape (N, 3), got {stored.shape}")
    implicit = 1.0 - stored.sum(axis=1, keepdims=True)
    return np.concatenate([stored, implicit], axis=1).astype(np.float32)
