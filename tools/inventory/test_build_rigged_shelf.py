#!/usr/bin/env python3
"""Regression tests for stable artifact IDs and review metadata flattening."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_rigged_shelf import build_shelf  # noqa: E402


class BuildRiggedShelfTests(unittest.TestCase):
    def fixture(self):
        return {
            "schema_version": 1,
            "source": {"base_url": "https://viewer.example", "rcf_path": "/game/art.rcf"},
            "scope": "all known-name .p3d.rz entries, without filename/category filtering",
            "qualification": "CompositeDrawable with skeleton and polyskin",
            "progress": {"complete": True, "eligible_entry_count": 3},
            "records": [{
                "entry_name": r"\art\z\small.p3d.rz",
                "entry_name_hash": "0xDEADBEEF",
                "entry_size_compressed": 100,
                "decompressed_size": 200,
                "shader_names": ["z_shader"],
                "rigged_composites": [{
                    "name": "small",
                    "skeleton_name": "small_skeleton",
                    "skeleton_is_local": True,
                    "declared_primitive_count": 2,
                    "polyskin_reference_count": 1,
                    "primitive_references": [
                        {"name": "small_body", "primitive_type": 2, "joint_index": 0},
                        {"name": "small_hat", "primitive_type": 1, "joint_index": 5},
                    ],
                }],
            }, {
                "entry_name": r"\art\a\large.p3d.rz",
                "entry_name_hash": "0x0123ABCD",
                "entry_size_compressed": 1000,
                "decompressed_size": 2000,
                "shader_names": ["a_shader"],
                "rigged_composites": [{
                    "name": "large_a", "skeleton_name": "a", "skeleton_is_local": False,
                    "declared_primitive_count": 1, "polyskin_reference_count": 1,
                    "primitive_references": [{"name": "a_body", "primitive_type": 2, "joint_index": 0}],
                }, {
                    "name": "large_b", "skeleton_name": "b", "skeleton_is_local": True,
                    "declared_primitive_count": 1, "polyskin_reference_count": 1,
                    "primitive_references": [{"name": "b_body", "primitive_type": 2, "joint_index": 0}],
                }],
            }],
        }

    def test_items_are_size_sorted_stably_identified_and_unreviewed(self):
        shelf = build_shelf(self.fixture(), generated_at="2026-09-15T00:00:00+00:00")
        self.assertEqual(shelf["item_count"], 3)
        self.assertEqual(
            [item["artifact_id"] for item in shelf["items"]],
            ["ARC-0123ABCD-01", "ARC-0123ABCD-02", "ARC-DEADBEEF-01"],
        )
        first = shelf["items"][0]
        self.assertEqual(first["polyskin_names"], ["a_body"])
        self.assertEqual(first["review"]["priority"], "unmarked")
        last = shelf["items"][-1]
        self.assertEqual(last["rigid_attachment_references"], [{"name": "small_hat", "joint_index": 5}])

    def test_incomplete_inventory_is_rejected(self):
        data = self.fixture()
        data["progress"]["complete"] = False
        with self.assertRaisesRegex(ValueError, "incomplete"):
            build_shelf(data)


if __name__ == "__main__":
    unittest.main()
