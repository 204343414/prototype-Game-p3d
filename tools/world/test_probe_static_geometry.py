#!/usr/bin/env python3
"""Synthetic regression tests for static POSITION-bound probing."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_static_geometry import scan_static_geometry  # noqa: E402


def p3d_string(value):
    raw = value.encode("utf-8") + b"\0"
    return bytes([len(raw)]) + raw


def chunk(type_id, payload=b"", children=b""):
    header_size = 12 + len(payload)
    return struct.pack("<III", type_id, header_size, header_size + len(children)) + payload + children


def file_with(children):
    return chunk(0xFF443350, b"", children)


def group(shader, positions, stride=16, declared_bytes=None):
    assert stride >= 12 and stride % 4 == 0
    floats_per_vertex = stride // 4
    raw_vertices = b"".join(struct.pack("<" + "f" * floats_per_vertex, *point, *([0.0] * (floats_per_vertex - 3)))
                            for point in positions)
    byte_count = len(raw_vertices) if declared_bytes is None else declared_bytes
    memory_vertices = chunk(0x00010012, struct.pack("<III", 1, 2, byte_count) + raw_vertices)
    pg_header = struct.pack("<I", 1) + p3d_string(shader) + struct.pack(
        "<9I", 0, 0x3011, len(positions), len(positions) * 3, 0, 1, 1, 0, 0)
    return chunk(0x00010020, pg_header, memory_vertices)


def geometry(name, groups):
    return chunk(0x00010000, p3d_string(name) + struct.pack("<I", 1), b"".join(groups))


class StaticGeometryProbeTests(unittest.TestCase):
    def test_reports_geometry_group_strides_and_aggregate_position_bounds(self):
        data = file_with(b"".join([
            geometry("building_a", [group("building_shader", [(-1.0, 2.0, 3.0), (4.0, -5.0, 6.0)])]),
            geometry("road_b", [group("road_shader", [(7.0, 8.0, -9.0)], stride=20)]),
        ]))
        result = scan_static_geometry(data)
        self.assertEqual(result["format"], "Pure3D")
        self.assertEqual(result["geometry_count"], 2)
        self.assertEqual(result["position_group_count"], 2)
        self.assertEqual(result["vertex_stride_counts"], {"16": 1, "20": 1})
        self.assertEqual(result["world_position_min"], [-1.0, -5.0, -9.0])
        self.assertEqual(result["world_position_max"], [7.0, 8.0, 6.0])
        self.assertEqual(result["geometries"][0]["primitive_groups"][0]["shader_name"], "building_shader")

    def test_bad_memory_byte_count_is_recorded_without_faking_a_bound(self):
        data = file_with(geometry("broken", [group("shader", [(1.0, 2.0, 3.0)], declared_bytes=99)]))
        result = scan_static_geometry(data)
        group_result = result["geometries"][0]["primitive_groups"][0]
        self.assertEqual(result["position_group_count"], 0)
        self.assertIn("unavailable", group_result["position_bounds_status"])
        self.assertIsNone(result["world_position_min"])


if __name__ == "__main__":
    unittest.main()
