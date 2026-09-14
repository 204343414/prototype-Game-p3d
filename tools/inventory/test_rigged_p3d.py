#!/usr/bin/env python3
"""Synthetic regression tests for the content-free rigged P3D census."""
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PARSER_DIR = os.path.join(HERE, "..", "p3d_parser")
sys.path.insert(0, HERE)
sys.path.insert(0, PARSER_DIR)

from rigged_p3d import scan_p3d_bytes  # noqa: E402


def p3d_string(value):
    encoded = value.encode("utf-8") + b"\0"
    assert len(encoded) <= 255
    return bytes([len(encoded)]) + encoded


def chunk(type_id, payload=b"", children=b""):
    header_size = 12 + len(payload)
    return struct.pack("<III", type_id, header_size, header_size + len(children)) + payload + children


def p3d(children):
    return chunk(0xFF443350, b"", children)


def skeleton(name, joints=67):
    return chunk(0x00023000, p3d_string(name) + struct.pack("<IIII", 1, joints, 13, 4))


def composite_primitive(name, primitive_type=2, joint=0):
    return chunk(0x00123001, struct.pack("<II", 1, 0) + p3d_string(name) + struct.pack("<II", primitive_type, joint))


def composite(name, skeleton_name, children):
    payload = struct.pack("<I", 1) + p3d_string(name) + p3d_string(skeleton_name)
    payload += struct.pack("<I", sum(1 for _ in children))
    return chunk(0x00123000, payload, b"".join(children))


def primitive_group(shader_name):
    return chunk(0x00010020, struct.pack("<I", 1) + p3d_string(shader_name))


class RiggedP3DTests(unittest.TestCase):
    def test_reports_rigged_composite_skeleton_and_shader_metadata(self):
        body = p3d(b"".join([
            skeleton("soldier_skeleton"),
            composite("soldier", "soldier_skeleton", [
                composite_primitive("soldier_head"),
                composite_primitive("soldier_body"),
                composite_primitive("rifle", primitive_type=1, joint=42),
            ]),
            chunk(0x00010001, b""),
            primitive_group("soldier_body_shader"),
            primitive_group("soldier_body_shader"),
        ]))
        result = scan_p3d_bytes(body)
        self.assertEqual(result["format"], "Pure3D")
        self.assertEqual(result["skeletons"][0]["name"], "soldier_skeleton")
        self.assertEqual(result["skeletons"][0]["joint_count"], 67)
        self.assertEqual(result["polyskin_count"], 1)
        self.assertEqual(result["shader_names"], ["soldier_body_shader"])
        self.assertEqual(len(result["rigged_composites"]), 1)
        candidate = result["rigged_composites"][0]
        self.assertEqual(candidate["name"], "soldier")
        self.assertTrue(candidate["skeleton_is_local"])
        self.assertEqual(candidate["declared_primitive_count"], 3)
        self.assertEqual(candidate["polyskin_reference_count"], 2)

    def test_external_skeleton_is_visible_and_non_p3d_is_rejected(self):
        body = p3d(composite("external_rig", "other_file_skeleton", [composite_primitive("body")]))
        result = scan_p3d_bytes(body)
        self.assertEqual(len(result["rigged_composites"]), 1)
        self.assertFalse(result["rigged_composites"][0]["skeleton_is_local"])
        with self.assertRaisesRegex(ValueError, "not a Pure3D"):
            scan_p3d_bytes(b"nope" * 20)


if __name__ == "__main__":
    unittest.main()
