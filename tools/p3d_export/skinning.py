"""Small, format-specific helpers shared by the Prototype glTF exporters."""

import numpy as np


def _validate_stored_weights(stored_weights: np.ndarray) -> np.ndarray:
    stored = np.asarray(stored_weights)
    if stored.ndim != 2 or stored.shape[1] != 3:
        raise ValueError(f"expected shape (N, 3), got {stored.shape}")
    return stored


def p3d_packed_vertex_weights_to_gltf(stored_weights: np.ndarray) -> np.ndarray:
    """Build glTF WEIGHTS_0 for the packed 56-byte P3D vertex layout.

    Its three serialized floats align with its first three serialized
    matrix-index bytes. The fourth aligned weight is implicit:
    ``1 - sum(stored_weights)``. The resulting glTF slot order is therefore
    ``[stored[0], stored[1], stored[2], implicit]``.

    This has been validated with Alex's packed body, head, and arm meshes
    under actual ROT animation. Bind-pose rendering alone cannot reveal a
    slot-order error because every skin matrix is identity in that pose.
    """
    stored = _validate_stored_weights(stored_weights)
    implicit = 1.0 - stored.sum(axis=1, keepdims=True)
    return np.concatenate([stored, implicit], axis=1).astype(np.float32)


def p3d_weight_list_weights_to_gltf(stored_weights: np.ndarray) -> np.ndarray:
    """Build glTF WEIGHTS_0 for P3D's separate legacy Weight_List layout.

    The legacy list variant has a different ordering relative to the raw
    Matrix_List bytes retained by these exporters. Its validated glTF order
    is ``[implicit, stored[2], stored[0], stored[1]]``.

    AlexVestShape is the observed fixture. This mapping was confirmed by
    bone-proximity checks and a human visual animation review: the old packed
    mapping pinned most of the jacket to a clavicle and made its back rigid.
    """
    stored = _validate_stored_weights(stored_weights)
    implicit = 1.0 - stored.sum(axis=1, keepdims=True)
    return np.concatenate([implicit, stored[:, 2:3], stored[:, 0:1], stored[:, 1:2]], axis=1).astype(np.float32)
