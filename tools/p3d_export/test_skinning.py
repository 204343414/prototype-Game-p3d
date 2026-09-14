#!/usr/bin/env python3
"""Regression tests for Prototype blend-weight slot orders."""

import unittest

import numpy as np

from skinning import (
    p3d_packed_vertex_weights_to_gltf,
    p3d_weight_list_weights_to_gltf,
)


class P3DWeightSlotTests(unittest.TestCase):
    def setUp(self):
        self.stored = np.array([[0.20, 0.30, 0.40], [0.0, 0.0, 0.0]], dtype=np.float32)

    def test_packed_vertex_weights_align_to_first_three_raw_index_slots(self):
        actual = p3d_packed_vertex_weights_to_gltf(self.stored)
        expected = np.array([[0.20, 0.30, 0.40, 0.10], [0.0, 0.0, 0.0, 1.0]], dtype=np.float32)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-7)
        np.testing.assert_allclose(actual.sum(axis=1), [1.0, 1.0], rtol=0, atol=1e-7)

    def test_legacy_weight_list_uses_its_own_raw_matrix_list_order(self):
        actual = p3d_weight_list_weights_to_gltf(self.stored)
        expected = np.array([[0.10, 0.40, 0.20, 0.30], [1.0, 0.0, 0.0, 0.0]], dtype=np.float32)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-7)
        np.testing.assert_allclose(actual.sum(axis=1), [1.0, 1.0], rtol=0, atol=1e-7)

    def test_rejects_a_non_vector3_weight_array(self):
        with self.assertRaises(ValueError):
            p3d_packed_vertex_weights_to_gltf(np.zeros((2, 4), dtype=np.float32))
        with self.assertRaises(ValueError):
            p3d_weight_list_weights_to_gltf(np.zeros((2, 4), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
