#!/usr/bin/env python3
"""Synthetic regressions for Cell NewShader/Texture metadata correlation."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_cell_shader_dependencies import scan_cell_shader_dependencies  # noqa: E402


def p3d_string(value):
    raw = value.encode("utf-8") + b"\0"
    return bytes([len(raw)]) + raw


def chunk(type_id, payload=b"", children=b""):
    header_size = 12 + len(payload)
    return struct.pack("<III", type_id, header_size, header_size + len(children)) + payload + children


def p3d_file(children):
    return chunk(0xFF443350, b"", children)


def primitive_group(shader_name, primitive_type=0):
    payload = struct.pack("<I", 1) + p3d_string(shader_name) + struct.pack(
        "<9I", primitive_type, 0x3011, 3, 3, 0, 1, 1, 0, 0)
    return chunk(0x00010020, payload)


def geometry(name, groups):
    return chunk(0x00010000, p3d_string(name) + struct.pack("<I", len(groups)), b"".join(groups))


def new_shader(name, template, parameters):
    payload = p3d_string(name) + struct.pack("<I", 256) + p3d_string(template) + struct.pack("<I", 2)
    children = b"".join(chunk(0x00011016, p3d_string(key) + p3d_string(value)) for key, value in parameters)
    return chunk(0x00011015, payload, children)


def texture(name, width=256, height=128):
    return chunk(0x00019000, p3d_string(name) + struct.pack("<9I", 14000, width, height, 8, 1, 1, 0, 0, 0))


class CellShaderDependencyTests(unittest.TestCase):
    def test_correlates_only_core_shader_references_with_local_textures(self):
        data = p3d_file(b"".join([
            geometry("localWheel", [primitive_group("local_shader")]),
            geometry("mergedDrawableRootNoShadow", [primitive_group("world_shader"), primitive_group("missing_shader")]),
            new_shader("world_shader", "zCBV2_env_building", [
                ("color", "brick_diffuse.dds"),
                ("normal", "brick_normal.dds"),
                ("grime", ""),
            ]),
            new_shader("unused_shader", "other", [("color", "unused.dds")]),
            texture("brick_diffuse.dds"),
            texture("brick_normal.dds", 64, 64),
        ]))
        report = scan_cell_shader_dependencies(data, "cell_7")
        self.assertEqual(report["world_geometry_names"], ["mergedDrawableRootNoShadow"])
        self.assertEqual(report["skipped_non_world_geometry_count"], 1)
        self.assertEqual(report["core_primitive_group_count"], 2)
        self.assertEqual(report["core_unique_shader_count"], 2)
        self.assertEqual(report["local_new_shader_definition_count"], 2)
        self.assertEqual(report["local_texture_definition_count"], 2)
        self.assertEqual(report["unique_image_reference_candidate_count"], 2)
        self.assertEqual(report["image_reference_candidates_resolved_to_local_texture_count"], 2)
        self.assertEqual(report["unresolved_core_shader_names"], ["missing_shader"])
        world = next(item for item in report["core_shader_references"] if item["shader_name"] == "world_shader")
        self.assertEqual(world["definition_status"], "local NewShader definition parsed")
        self.assertEqual(world["template_name"], "zCBV2_env_building")
        self.assertEqual(world["primitive_type_counts"], {"0": 1})
        self.assertEqual(
            world["image_reference_candidates"],
            [
                {"parameter_name": "color", "value": "brick_diffuse.dds", "matches_local_texture_definition": True},
                {"parameter_name": "normal", "value": "brick_normal.dds", "matches_local_texture_definition": True},
            ],
        )
        self.assertEqual(report["errors"], [])

    def test_records_bad_local_shader_metadata_without_inventing_a_binding(self):
        malformed_shader = chunk(0x00011015, p3d_string("world_shader") + struct.pack("<I", 256))
        data = p3d_file(b"".join([
            geometry("mergedDrawableRootCastShadow", [primitive_group("world_shader")]),
            malformed_shader,
        ]))
        report = scan_cell_shader_dependencies(data, "cell_bad")
        self.assertEqual(report["core_primitive_group_count"], 1)
        self.assertEqual(report["unresolved_core_shader_names"], ["world_shader"])
        self.assertEqual(report["core_shader_references"][0]["definition_status"], "not found in this Cell")
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("NewShader", report["errors"][0])


if __name__ == "__main__":
    unittest.main()
