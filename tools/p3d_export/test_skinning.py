#!/usr/bin/env python3
"""Regression tests for Prototype blend-weight slot order."""

import unittest

import numpy as np

from skinning import p3d_stored_weights_to_gltf


class P3DStoredWeightsToGltfTests(unittest.TestCase):
    def test_stored_weights_stay_aligned_to_first_three_index_slots(self):
        stored = np.array([[0.20, 0.30, 0.40], [0.0, 0.0, 0.0]], dtype=np.float32)
        actual = p3d_stored_weights_to_gltf(stored)
        expected = np.array([[0.20, 0.30, 0.40, 0.10], [0.0, 0.0, 0.0, 1.0]], dtype=np.float32)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-7)
        np.testing.assert_allclose(actual.sum(axis=1), [1.0, 1.0], rtol=0, atol=1e-7)

    def test_rejects_a_non_vector3_weight_array(self):
        with self.assertRaises(ValueError):
            p3d_stored_weights_to_gltf(np.zeros((2, 4), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
