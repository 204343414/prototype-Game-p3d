#!/usr/bin/env python3
"""Synthetic regression for aggregate Cell-core material diagnostic selection."""
import os
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_static_cell_core_material import _html, build_preview_data  # noqa: E402


def ps(value):
    raw = value.encode() + b"\0"
    return bytes([len(raw)]) + raw


def chunk(type_id, payload=b"", children=b""):
    hs = 12 + len(payload)
    return struct.pack("<III", type_id, hs, hs + len(children)) + payload + children


def mesh_group():
    raw = bytearray()
    for pos, uv in [((0, 0, 0), (0, 0)), ((2, 0, 0), (1, 0)), ((0, 3, 0), (0, 1))]:
        raw.extend(struct.pack("<4f", *pos, 1.0)); raw.extend(struct.pack("<II", 0, 0))
        raw.extend(struct.pack("<2f", *uv)); raw.extend(struct.pack("<2f", 0, 0))
        raw.extend(struct.pack("<7f", 0, 0, 1, 1, 0, 0, 1))
    stride = 68
    declaration = b"".join([
        struct.pack("<IIIHHB", 0x2C929929, 0, 0, stride, 4, 0),
        struct.pack("<IIIHHB", 0x00364509, 0, 24, stride, 2, 0),
        struct.pack("<IIIHHB", 0xC206BCE7, 0, 40, stride, 3, 1),
    ])
    d = chunk(0x10014, struct.pack("<IIII", 1, 2, len(raw), len(declaration)) + declaration)
    v = chunk(0x10012, struct.pack("<III", 0x20001, 0, len(raw)) + raw)
    i = chunk(0x10013, struct.pack("<III", 0x20001, 0, 6) + struct.pack("<3H", 0, 1, 2))
    header = struct.pack("<I", 1) + ps("shader_a") + struct.pack("<9I", 0, 0x3011, 3, 3, 0, 1, 1, 0, 0)
    return chunk(0x10020, header, d + v + i)


def fixture():
    shader = chunk(0x11015, ps("shader_a") + struct.pack("<I", 256) + ps("zCBV2") + struct.pack("<I", 1), chunk(0x11016, ps("color") + ps("brick.dds")))
    geometry = chunk(0x10000, ps("mergedDrawableRootNoShadow") + struct.pack("<I", 1), mesh_group())
    return chunk(0xFF443350, b"", shader + geometry)


class AggregateCellMaterialTests(unittest.TestCase):
    def test_selects_only_68_byte_local_color_material_and_keeps_report_payload_free(self):
        png = b"\x89PNG\r\n\x1a\nminimal"
        with patch("render_static_cell_core_material._decode_local_texture_to_png", return_value=("DXT1", 4, 4, png)):
            display, report = build_preview_data(fixture(), "cell_test")
        self.assertEqual(report["core_group_count"], 1)
        self.assertEqual(report["core_vertex_count"], 3)
        self.assertEqual(report["textured_68_byte_group_count"], 1)
        self.assertEqual(report["gray_context_group_count"], 0)
        self.assertEqual(report["decoded_local_color_texture_count"], 1)
        self.assertEqual(display["textures"][0]["name"], "brick.dds")
        self.assertNotIn("p", report)
        page = _html(display, report)
        self.assertIn("合并 core material diagnostic", page)
        self.assertNotIn("__DATA__", page)


if __name__ == "__main__":
    unittest.main()
