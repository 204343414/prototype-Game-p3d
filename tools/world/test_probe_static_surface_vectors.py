#!/usr/bin/env python3
"""Synthetic tests for static surface-vector geometric evidence."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_static_surface_vectors import analyze_surface_vectors  # noqa: E402


def p3d_string(value):
    raw = value.encode("utf-8") + b"\0"
    return bytes([len(raw)]) + raw


def chunk(type_id, payload=b"", children=b""):
    header_size = 12 + len(payload)
    return struct.pack("<III", type_id, header_size, header_size + len(children)) + payload + children


def p3d_file(children):
    return chunk(0xFF443350, b"", children)


def strict_group():
    # 68-byte stream: POSITION at 0, intentionally unclassified candidate
    # float3 normal at 40 and float4 tangent/sign at 52.
    vertices = ((0, 0, 0), (2, 0, 0), (0, 3, 0))
    raw = bytearray()
    for point in vertices:
        raw.extend(struct.pack("<4f", *point, 1.0))
        raw.extend(struct.pack("<II", 0, 0))
        raw.extend(struct.pack("<2f", .1, .2))
        raw.extend(struct.pack("<2f", .3, .4))
        raw.extend(struct.pack("<7f", 0, 0, 1, 1, 0, 0, -1))
    stride = 68
    declaration = b"".join([
        struct.pack("<IIIHHB", 0x2C929929, 0, 0, stride, 4, 0),
        struct.pack("<IIIHHB", 0xC206BCE7, 0, 40, stride, 3, 1),
        struct.pack("<IIIHHB", 0xA4176245, 0, 52, stride, 4, 1),
    ])
    desc = chunk(0x00010014, struct.pack("<IIII", 1, 2, len(raw), len(declaration)) + declaration)
    vlist = chunk(0x00010012, struct.pack("<III", 0x20001, 0, len(raw)) + raw)
    ilist = chunk(0x00010013, struct.pack("<III", 0x20001, 0, 6) + struct.pack("<3H", 0, 1, 2))
    pg = chunk(0x00010020, struct.pack("<I", 1) + p3d_string("shader") + struct.pack(
        "<9I", 0, 0x3011, 3, 3, 0, 1, 1, 0, 0), desc + vlist + ilist)
    return chunk(0x00010000, p3d_string("mergedDrawableRootNoShadow") + struct.pack("<I", 1), pg)


class StaticSurfaceVectorTests(unittest.TestCase):
    def test_matches_geometric_normal_and_orthogonal_tangent_without_claiming_shader_output(self):
        report = analyze_surface_vectors(
            p3d_file(strict_group()), "cell", "mergedDrawableRootNoShadow", 1, 40, 52, 64,
        )
        normal = report["normal_candidate"]
        tangent = report["tangent_candidate"]
        self.assertEqual(normal["semantic_hash"], "0xC206BCE7")
        self.assertEqual(normal["float3_length"]["minimum"], 1.0)
        self.assertEqual(normal["face_alignment_mean_absolute_dot"], 1.0)
        self.assertEqual(normal["face_alignment_fraction_abs_dot_at_least_0_9"], 1.0)
        self.assertEqual(tangent["semantic_hash"], "0xA4176245")
        self.assertEqual(tangent["face_alignment_mean_absolute_dot"], 0.0)
        self.assertEqual(tangent["normal_tangent_vertex_mean_absolute_dot"], 0.0)
        self.assertEqual(tangent["w_value_counts"], {"-1.0": 3})
        self.assertIn("geometric vector evidence", report["conclusion_limit"])


if __name__ == "__main__":
    unittest.main()
