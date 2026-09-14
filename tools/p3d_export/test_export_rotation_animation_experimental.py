#!/usr/bin/env python3
"""Synthetic regression coverage for the narrow ROT-only glTF animation exporter."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from pygltflib import Asset, Buffer, GLTF2, Node, Scene

from export_rotation_animation_experimental import append_rotation_animation


def accessor_data(gltf: GLTF2, accessor_index: int, components: int) -> np.ndarray:
    accessor = gltf.accessors[accessor_index]
    view = gltf.bufferViews[accessor.bufferView]
    start = (view.byteOffset or 0) + (accessor.byteOffset or 0)
    return np.frombuffer(
        gltf.binary_blob(), dtype="<f4", count=accessor.count * components, offset=start,
    ).reshape(accessor.count, components)


class RotationOnlyExporterTests(unittest.TestCase):
    def test_converts_matrices_appends_direct_rotations_and_skips_tran(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            static_path = temporary / "static.glb"
            decoded_path = temporary / "decoded.json"
            output_path = temporary / "animated.glb"

            # A glTF column-major +90° Z local matrix with translation (2,3,4).
            gltf = GLTF2(asset=Asset(version="2.0"))
            gltf.nodes = [Node(name="Joint", matrix=[
                0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 2, 3, 4, 1,
            ])]
            gltf.scenes = [Scene(nodes=[0])]
            gltf.scene = 0
            gltf.buffers = [Buffer(byteLength=0)]
            gltf.set_binary_blob(b"")
            gltf.save_binary(static_path)

            rotations = [[0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.5, 0.8660254]]
            decoded_path.write_text(json.dumps({
                "name": "synthetic_block",
                "animation_type": "PTRN",
                "frame_rate": 20.0,
                "groups": [
                    {"name": "Joint", "channels": [
                        {"semantic": "ROT", "frames": [0, 10], "values": rotations},
                        {"semantic": "TRAN", "frames": [0], "values": [[9, 9, 9]]},
                    ]},
                    {"name": "limb_without_node", "channels": [
                        {"semantic": "ROT", "frames": [0], "values": [[0, 0, 0, 1]]},
                    ]},
                ],
            }), encoding="utf-8")

            report = append_rotation_animation(static_path, decoded_path, output_path)
            self.assertEqual(report["matrix_nodes_converted"], 1)
            self.assertEqual(report["rotation_tracks"], 1)
            self.assertEqual(report["skipped_non_node_rotations"], ["limb_without_node"])
            self.assertEqual(report["skipped_translation_groups"], ["Joint"])

            actual = GLTF2().load_binary(output_path)
            joint = actual.nodes[0]
            self.assertIsNone(joint.matrix)
            np.testing.assert_allclose(joint.translation, [2, 3, 4], rtol=0, atol=1e-6)
            np.testing.assert_allclose(joint.scale, [1, 1, 1], rtol=0, atol=1e-6)
            animation = actual.animations[-1]
            self.assertEqual(animation.name, "synthetic_block")
            self.assertEqual(len(animation.channels), 1)
            sampler = animation.samplers[animation.channels[0].sampler]
            np.testing.assert_allclose(accessor_data(actual, sampler.input, 1).ravel(), [0.0, 0.5])
            np.testing.assert_allclose(accessor_data(actual, sampler.output, 4), rotations, rtol=0, atol=1e-6)

    def test_rejects_non_ptrn_input(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            static_path = temporary / "static.glb"
            decoded_path = temporary / "decoded.json"
            gltf = GLTF2(asset=Asset(version="2.0"), nodes=[Node(name="Joint")],
                         scenes=[Scene(nodes=[0])], scene=0, buffers=[Buffer(byteLength=0)])
            gltf.set_binary_blob(b"")
            gltf.save_binary(static_path)
            decoded_path.write_text(json.dumps({"animation_type": "CAM"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "PTRN"):
                append_rotation_animation(static_path, decoded_path, temporary / "out.glb")


if __name__ == "__main__":
    unittest.main()
