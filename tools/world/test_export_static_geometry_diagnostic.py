#!/usr/bin/env python3
"""Synthetic regressions for strict world-core triangle decoding."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export_static_geometry_diagnostic import (  # noqa: E402
    MEMORY_INDEX_LIST,
    _build_preview_html,
    scan_core_triangle_geometry,
)


def p3d_string(value):
    raw = value.encode("utf-8") + b"\0"
    return bytes([len(raw)]) + raw


def chunk(type_id, payload=b"", children=b""):
    header_size = 12 + len(payload)
    return struct.pack("<III", type_id, header_size, header_size + len(children)) + payload + children


def p3d_file(children):
    return chunk(0xFF443350, b"", children)


def triangle_group(vertices, indices, primitive_type=0):
    stride = 16
    raw_vertices = b"".join(
        struct.pack("<4f", x, y, z, 123.0) for x, y, z in vertices
    )
    # Static Manhattan's verified POSITION signature is deliberately required
    # by this strict core-only decoder.
    declaration = struct.pack("<IIIHHB", 0x2C929929, 0, 0, stride, 4, 0)
    vertex_list = chunk(0x00010012, struct.pack("<III", 0x20001, 0, len(raw_vertices)) + raw_vertices)
    index_list = chunk(
        MEMORY_INDEX_LIST,
        struct.pack("<III", 0x20001, 0, len(indices) * 2) + struct.pack("<" + "H" * len(indices), *indices),
    )
    description = chunk(0x00010014, struct.pack("<IIII", 1, 2, len(raw_vertices), len(declaration)) + declaration)
    header = struct.pack("<I", 1) + p3d_string("shader_key") + struct.pack(
        "<9I", primitive_type, 0x3011, len(vertices), len(indices), 0, 1, 1, 0, 0)
    return chunk(0x00010020, header, description + vertex_list + index_list)


def geometry(name, groups):
    return chunk(0x00010000, p3d_string(name) + struct.pack("<I", len(groups)), b"".join(groups))


class CoreTriangleDiagnosticTests(unittest.TestCase):
    def test_decodes_only_merged_world_triangle_lists(self):
        data = p3d_file(b"".join([
            geometry("localReusableProp", [triangle_group([(50, 50, 50), (51, 50, 50), (50, 51, 50)], [0, 1, 2])]),
            geometry("mergedDrawableRootNoShadow", [triangle_group([(0, 0, 0), (3, 0, 0), (0, 4, 0)], [0, 1, 2])]),
        ]))
        groups, report = scan_core_triangle_geometry(data, "\\art\\locations\\manhattan\\manhattan_Cell_77.p3d.rz")
        self.assertEqual(len(groups), 1)
        self.assertEqual(report["skipped_non_world_geometry_count"], 1)
        self.assertEqual(report["world_geometry_names"], ["mergedDrawableRootNoShadow"])
        self.assertEqual(report["accepted_vertex_count"], 3)
        self.assertEqual(report["accepted_index_count"], 3)
        self.assertEqual(report["accepted_triangle_count"], 1)
        self.assertEqual(report["position_min"], [0.0, 0.0, 0.0])
        self.assertEqual(report["position_max"], [3.0, 4.0, 0.0])
        self.assertEqual(report["vertex_stride_counts"], {"16": 1})
        self.assertEqual(report["repeated_index_triangle_count"], 0)
        self.assertEqual(report["zero_area_triangle_count"], 0)
        self.assertEqual(report["errors"], [])
        self.assertEqual(groups[0].index_max, 2)

        page = _build_preview_html(groups, report)
        self.assertIn("core triangle diagnostic", page)
        self.assertIn("mergedDrawableRootNoShadow", page)
        self.assertNotIn("__DATA__", page)
        self.assertNotIn("__DIAGNOSTICS__", page)

    def test_rejects_out_of_range_indices_instead_of_clamping_or_exporting(self):
        data = p3d_file(geometry(
            "mergedDrawableRootCastShadow",
            [triangle_group([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 3])],
        ))
        groups, report = scan_core_triangle_geometry(data, "cell_bad")
        self.assertEqual(groups, [])
        self.assertEqual(report["accepted_group_count"], 0)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("exceeds vertex_count", report["errors"][0])

    def test_rejects_non_triangle_list_instead_of_reinterpreting_it(self):
        data = p3d_file(geometry(
            "mergedDrawableRootCastShadow",
            [triangle_group([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], primitive_type=1)],
        ))
        groups, report = scan_core_triangle_geometry(data, "cell_strip")
        self.assertEqual(groups, [])
        self.assertEqual(report["accepted_group_count"], 0)
        self.assertIn("TriangleList=0", report["errors"][0])


if __name__ == "__main__":
    unittest.main()
