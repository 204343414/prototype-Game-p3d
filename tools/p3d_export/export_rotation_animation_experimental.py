#!/usr/bin/env python3
"""Append a verified ROT-only external-family Prototype clip to a static GLB.

This is deliberately an *experimental narrow exporter*, not general animation
support.  It accepts the private diagnostic JSON produced by
``tools/p3d_animation/decode_animation.py`` and emits only decoded ``ROT``
tracks for matching glTF node names.  In particular, it does not emit the
unresolved 0x00121119 ``TRAN`` tracks, nor do its output files contain game
assets unless the caller supplies them locally.

A glTF node with ``matrix`` may not itself be animation-targeted.  The static
Alex exporter therefore writes P3D rest transforms as matrices, and this tool
first decomposes every affine matrix node to an equivalent local glTF TRS.
Matrices with shear or degenerate scale are rejected rather than approximated.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from pygltflib import (
    Accessor,
    Animation,
    AnimationChannel,
    AnimationChannelTarget,
    AnimationSampler,
    BufferView,
    FLOAT,
    GLTF2,
)


def quaternion_from_rotation_matrix(matrix: np.ndarray) -> list[float]:
    """Return a normalized glTF ``[x, y, z, w]`` quaternion from a 3×3 matrix."""
    m00, m01, m02 = matrix[0]
    m10, m11, m12 = matrix[1]
    m20, m21, m22 = matrix[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        quaternion = ((m21 - m12) / scale, (m02 - m20) / scale,
                      (m10 - m01) / scale, 0.25 * scale)
    elif m00 > m11 and m00 > m22:
        scale = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        quaternion = (0.25 * scale, (m01 + m10) / scale,
                      (m02 + m20) / scale, (m21 - m12) / scale)
    elif m11 > m22:
        scale = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        quaternion = ((m01 + m10) / scale, 0.25 * scale,
                      (m12 + m21) / scale, (m02 - m20) / scale)
    else:
        scale = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        quaternion = ((m02 + m20) / scale, (m12 + m21) / scale,
                      0.25 * scale, (m10 - m01) / scale)
    magnitude = math.sqrt(sum(component * component for component in quaternion))
    if magnitude < 1e-12:
        raise ValueError("matrix has no usable rotation")
    return [float(component / magnitude) for component in quaternion]


def convert_matrix_nodes_to_trs(gltf: GLTF2) -> int:
    """Replace every affine, non-sheared glTF node matrix with equivalent TRS."""
    converted = 0
    for node in gltf.nodes or []:
        if node.matrix is None:
            continue
        if len(node.matrix) != 16:
            raise ValueError(f"node {node.name!r} has a {len(node.matrix)}-value matrix")
        # glTF matrix arrays use column-major order.  The static P3D exporter
        # serializes a row-vector P3D matrix directly, which correctly becomes
        # its equivalent transposed column-vector matrix on this read.
        matrix = np.asarray(node.matrix, dtype=np.float64).reshape((4, 4), order="F")
        if not np.allclose(matrix[3], (0.0, 0.0, 0.0, 1.0), atol=1e-5):
            raise ValueError(f"node {node.name!r} has a non-affine matrix")
        rotation_scale = matrix[:3, :3].copy()
        scale = np.linalg.norm(rotation_scale, axis=0)
        if np.any(scale < 1e-8):
            raise ValueError(f"node {node.name!r} has a degenerate scale")
        rotation = rotation_scale / scale
        if np.linalg.det(rotation) < 0.0:
            # Preserve exactly one reflected axis instead of silently changing
            # handedness.  Alex's joints do not take this branch.
            scale[0] *= -1.0
            rotation[:, 0] *= -1.0
        if not np.allclose(rotation.T @ rotation, np.identity(3), atol=1e-5):
            raise ValueError(f"node {node.name!r} matrix contains shear; cannot safely animate it")
        node.matrix = None
        node.translation = [float(value) for value in matrix[:3, 3]]
        node.rotation = quaternion_from_rotation_matrix(rotation)
        node.scale = [float(value) for value in scale]
        converted += 1
    return converted


def append_buffer_view(blob: bytearray, gltf: GLTF2, raw: bytes) -> int:
    """Append aligned binary data and return its glTF bufferView index."""
    offset = len(blob)
    blob.extend(raw)
    blob.extend(b"\0" * ((-len(blob)) % 4))
    gltf.bufferViews.append(BufferView(buffer=0, byteOffset=offset, byteLength=len(raw)))
    return len(gltf.bufferViews) - 1


def append_accessor(
    blob: bytearray,
    gltf: GLTF2,
    raw: bytes,
    count: int,
    accessor_type: str,
    *,
    minimum: list[float] | None = None,
    maximum: list[float] | None = None,
) -> int:
    view = append_buffer_view(blob, gltf, raw)
    accessor = Accessor(bufferView=view, componentType=FLOAT, count=count, type=accessor_type)
    if minimum is not None:
        accessor.min = minimum
    if maximum is not None:
        accessor.max = maximum
    gltf.accessors.append(accessor)
    return len(gltf.accessors) - 1


def append_rotation_animation(
    static_glb: str | Path,
    decoded_animation_json: str | Path,
    output_glb: str | Path,
) -> dict[str, Any]:
    """Append direct decoded ROT keys and return a non-game-data export report."""
    gltf = GLTF2().load_binary(str(static_glb))
    decoded = json.loads(Path(decoded_animation_json).read_text(encoding="utf-8"))
    if decoded.get("animation_type") != "PTRN":
        raise ValueError(f"expected a PTRN animation, got {decoded.get('animation_type')!r}")
    frame_rate = float(decoded["frame_rate"])
    if not math.isfinite(frame_rate) or frame_rate <= 0.0:
        raise ValueError(f"invalid frame_rate {frame_rate!r}")

    matrix_nodes_converted = convert_matrix_nodes_to_trs(gltf)
    node_by_name: dict[str, int] = {}
    for index, node in enumerate(gltf.nodes or []):
        if not node.name:
            continue
        if node.name in node_by_name:
            raise ValueError(f"duplicate node name {node.name!r}; target mapping would be ambiguous")
        node_by_name[node.name] = index

    blob = bytearray(gltf.binary_blob() or b"")
    animation = Animation(name=decoded.get("name", "Prototype_ROT_experiment"), samplers=[], channels=[])
    skipped_non_node_rotations: set[str] = set()
    skipped_translation_groups: set[str] = set()
    skipped_other_channels: set[str] = set()
    seen_target_nodes: set[int] = set()

    for group in decoded.get("groups", []):
        group_name = group.get("name", "<unnamed>")
        node_index = node_by_name.get(group_name)
        for channel in group.get("channels", []):
            semantic = channel.get("semantic")
            if semantic == "TRAN":
                skipped_translation_groups.add(group_name)
                continue
            if semantic != "ROT":
                skipped_other_channels.add(f"{group_name}:{semantic}")
                continue
            if node_index is None:
                skipped_non_node_rotations.add(group_name)
                continue
            if node_index in seen_target_nodes:
                raise ValueError(f"multiple ROT channels target node {group_name!r}")

            frames = np.asarray(channel["frames"], dtype="<f4")
            rotations = np.asarray(channel["values"], dtype="<f4")
            if frames.ndim != 1 or len(frames) == 0:
                raise ValueError(f"{group_name!r}: ROT track has no frames")
            if rotations.shape != (len(frames), 4):
                raise ValueError(f"{group_name!r}: expected {len(frames)} VEC4 rotations, got {rotations.shape}")
            if not np.isfinite(rotations).all():
                raise ValueError(f"{group_name!r}: ROT track contains non-finite values")
            norms = np.linalg.norm(rotations, axis=1)
            if not np.allclose(norms, 1.0, atol=2e-4):
                raise ValueError(f"{group_name!r}: ROT keys are not normalized quaternions")
            times = frames / frame_rate
            if np.any(np.diff(times) < 0.0):
                raise ValueError(f"{group_name!r}: ROT frames are not monotonic")

            input_accessor = append_accessor(
                blob, gltf, times.tobytes(), len(times), "SCALAR",
                minimum=[float(times.min())], maximum=[float(times.max())],
            )
            output_accessor = append_accessor(
                blob, gltf, rotations.tobytes(), len(rotations), "VEC4",
            )
            sampler_index = len(animation.samplers)
            animation.samplers.append(AnimationSampler(
                input=input_accessor, output=output_accessor, interpolation="LINEAR",
            ))
            animation.channels.append(AnimationChannel(
                sampler=sampler_index,
                target=AnimationChannelTarget(node=node_index, path="rotation"),
            ))
            seen_target_nodes.add(node_index)

    if not animation.channels:
        raise ValueError("no decoded ROT tracks mapped to static GLB node names")
    gltf.animations = (gltf.animations or []) + [animation]
    if not gltf.buffers:
        raise ValueError("static GLB has no binary buffer")
    gltf.buffers[0].byteLength = len(blob)
    gltf.set_binary_blob(bytes(blob))
    gltf.save_binary(str(output_glb))
    return {
        "output": str(output_glb),
        "animation_name": animation.name,
        "rotation_tracks": len(animation.channels),
        "matrix_nodes_converted": matrix_nodes_converted,
        "skipped_non_node_rotations": sorted(skipped_non_node_rotations),
        "skipped_translation_groups": sorted(skipped_translation_groups),
        "skipped_other_channels": sorted(skipped_other_channels),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-glb", required=True, help="locally generated static GLB")
    parser.add_argument("--animation-json", required=True, help="private JSON from decode_animation.py")
    parser.add_argument("--out", required=True, help="output animated GLB")
    arguments = parser.parse_args()
    report = append_rotation_animation(arguments.static_glb, arguments.animation_json, arguments.out)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
