#!/usr/bin/env python3
"""Synthetic tests for static float2 candidate extraction (not texture decoding)."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_static_uv_candidates import build_metadata_report, extract_candidate_mesh  # noqa: E402


def p3d_string(value):
    raw = value.encode("utf-8") + b"\0"
    return bytes([len(raw)]) + raw


def chunk(type_id, payload=b"", children=b""):
    header_size = 12 + len(payload)
    return struct.pack("<III", type_id, header_size, header_size + len(children)) + payload + children


def p3d_file(children):
    return chunk(0xFF443350, b"", children)


def group(vertices, uv_a, uv_b):
    assert len(vertices) == len(uv_a) == len(uv_b)
    raw = bytearray()
    for point, first, second in zip(vertices, uv_a, uv_b):
        raw.extend(struct.pack("<4f", *point, 1.0))       # position at 0, unclassified float at 12
        raw.extend(struct.pack("<II", 0xFFFFFFFF, 0xFF000000))  # packed/unclassified bytes at 16
        raw.extend(struct.pack("<2f", *first))             # candidate A at 24
        raw.extend(struct.pack("<2f", *second))            # candidate B at 32
        raw.extend(struct.pack("<7f", 0, 0, 1, 1, 0, 0, 1))  # candidate normal/tangent storage, not used here
    stride = 68
    assert len(raw) == len(vertices) * stride
    declaration = b"".join([
        struct.pack("<IIIHHB", 0x2C929929, 0, 0, stride, 4, 0),
        struct.pack("<IIIHHB", 0x00364509, 0, 24, stride, 2, 0),
        struct.pack("<IIIHHB", 0x0036450A, 0, 32, stride, 2, 0),
    ])
    desc = chunk(0x00010014, struct.pack("<IIII", 1, 2, len(raw), len(declaration)) + declaration)
    vertices_chunk = chunk(0x00010012, struct.pack("<III", 0x20001, 0, len(raw)) + raw)
    indices_chunk = chunk(0x00010013, struct.pack("<III", 0x20001, 0, 6) + struct.pack("<3H", 0, 1, 2))
    header = struct.pack("<I", 1) + p3d_string("shader_abc") + struct.pack(
        "<9I", 0, 0x3011, len(vertices), 3, 0, 1, 1, 0, 0)
    return chunk(0x00010020, header, desc + vertices_chunk + indices_chunk)


def geometry(name, groups):
    return chunk(0x00010000, p3d_string(name) + struct.pack("<I", len(groups)), b"".join(groups))


class StaticUVCandidateTests(unittest.TestCase):
    def test_extracts_exact_declared_float2_candidates_without_flipping_values(self):
        data = p3d_file(geometry("mergedDrawableRootCastShadow", [group(
            [(0, 0, 0), (2, 0, 0), (0, 3, 0)],
            [(0.25, 0.5), (1.25, 0.5), (0.25, 1.5)],
            [(7.0, -2.0), (8.0, -2.0), (7.0, -1.0)],
        )]))
        mesh = extract_candidate_mesh(data, "cell_test", "mergedDrawableRootCastShadow", 1, [24, 32])
        self.assertEqual(mesh.vertex_stride, 68)
        self.assertEqual(mesh.triangle_count, 1)
        self.assertEqual(mesh.shader_name, "shader_abc")
        self.assertEqual(mesh.position_min, [0.0, 0.0, 0.0])
        self.assertEqual(mesh.position_max, [2.0, 3.0, 0.0])
        self.assertEqual(mesh.uv_streams[0][0:2], (24, "0x00364509"))
        self.assertEqual(mesh.uv_streams[0][3:5], ([0.25, 0.5], [1.25, 1.5]))
        self.assertEqual(mesh.uv_streams[1][0:2], (32, "0x0036450A"))
        self.assertEqual(mesh.uv_streams[1][3:5], ([7.0, -2.0], [8.0, -1.0]))
        self.assertEqual(struct.unpack_from("<2f", mesh.uv_streams[0][2], 0), (0.25, 0.5))
        report = build_metadata_report(mesh, "test.dds", "DXT5", 256, 1024)
        self.assertEqual(report["texture"]["name"], "test.dds")
        self.assertIn("neither offset", report["conclusion_limit"])

    def test_refuses_local_geometry_even_if_its_vertex_layout_matches(self):
        data = p3d_file(geometry("vehicleAtOrigin", [group(
            [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 0)] * 3, [(0, 0)] * 3,
        )]))
        with self.assertRaisesRegex(ValueError, "mergedDrawableRoot"):
            extract_candidate_mesh(data, "cell_test", "vehicleAtOrigin", 1, [24, 32])


if __name__ == "__main__":
    unittest.main()
