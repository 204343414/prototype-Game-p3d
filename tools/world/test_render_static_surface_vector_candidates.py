#!/usr/bin/env python3
"""Synthetic selection regression for WebGL normal/tangent comparison fixture."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_static_surface_vector_candidates import _build_html, extract_lit_candidate_mesh  # noqa: E402


def ps(value):
    raw = value.encode() + b"\0"
    return bytes([len(raw)]) + raw


def chunk(type_id, payload=b"", children=b""):
    size = 12 + len(payload)
    return struct.pack("<III", type_id, size, size + len(children)) + payload + children


def fixture():
    raw = bytearray()
    for point, uv in [((0, 0, 0), (0, 0)), ((2, 0, 0), (1, 0)), ((0, 3, 0), (0, 1))]:
        raw.extend(struct.pack("<4f", *point, 1)); raw.extend(struct.pack("<II", 0, 0))
        raw.extend(struct.pack("<2f", *uv)); raw.extend(struct.pack("<2f", 0, 0))
        raw.extend(struct.pack("<7f", 0, 0, 1, 1, 0, 0, 1))
    stride = 68
    declaration = b"".join([
        struct.pack("<IIIHHB", 0x2C929929, 0, 0, stride, 4, 0),
        struct.pack("<IIIHHB", 0x00364509, 0, 24, stride, 2, 0),
        struct.pack("<IIIHHB", 0xC206BCE7, 0, 40, stride, 3, 1),
        struct.pack("<IIIHHB", 0xA4176245, 0, 52, stride, 4, 1),
    ])
    children = chunk(0x10014, struct.pack("<IIII", 1, 2, len(raw), len(declaration)) + declaration)
    children += chunk(0x10012, struct.pack("<III", 0x20001, 0, len(raw)) + raw)
    children += chunk(0x10013, struct.pack("<III", 0x20001, 0, 6) + struct.pack("<3H", 0, 1, 2))
    pg = chunk(0x10020, struct.pack("<I", 1) + ps("shader") + struct.pack("<9I", 0, 0x3011, 3, 3, 0, 1, 1, 0, 0), children)
    geometry = chunk(0x10000, ps("mergedDrawableRootNoShadow") + struct.pack("<I", 1), pg)
    return chunk(0xFF443350, b"", geometry)


class SurfaceVectorRenderTests(unittest.TestCase):
    def test_extracts_same_validated_group_for_uv_normal_tangent_panes(self):
        mesh = extract_lit_candidate_mesh(fixture(), "cell", "mergedDrawableRootNoShadow", 1, 24, 40, 52)
        self.assertEqual(mesh.uv_hash, "0x00364509")
        self.assertEqual(mesh.candidates[0][0:2], (40, "0xC206BCE7"))
        self.assertEqual(mesh.candidates[1][0:2], (52, "0xA4176245"))
        self.assertEqual(len(mesh.positions), 36)
        self.assertEqual(len(mesh.uvs), 24)
        page = _build_html(mesh, {"note": "metadata"}, b"png")
        self.assertIn("surface-vector", page)
        self.assertNotIn("__DATA__", page)


if __name__ == "__main__":
    unittest.main()
