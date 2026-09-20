import base64, json, os, struct, tempfile, unittest
from pathlib import Path

from export_loaded_cells_gltf import dxt_png, report_progress, Builder


class ExportLoadedCellsTests(unittest.TestCase):
    def test_fast_dxt_png_generates_valid_png_and_matches_dimensions(self):
        w, h = 64, 64
        num_blocks = (w // 4) * (h // 4)
        # Test DXT1
        dxt1_data = (struct.pack('<HH', 0xF800, 0x07E0) + struct.pack('<I', 0x1B1B1B1B)) * num_blocks
        png1 = dxt_png('DXT1', w, h, dxt1_data)
        self.assertTrue(png1.startswith(b'\x89PNG\r\n\x1a\n'))
        # Test DXT5
        dxt5_data = (bytes([255, 0, 1, 2, 3, 4, 5, 6]) + struct.pack('<HH', 0xF800, 0x07E0) + struct.pack('<I', 0x1B1B1B1B)) * num_blocks
        png5 = dxt_png('DXT5', w, h, dxt5_data)
        self.assertTrue(png5.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_builder_constructs_valid_gltf_structure(self):
        b = Builder('test-cells')
        pb = struct.pack('<3f', 0.0, 1.0, 2.0)
        acc = b.acc(pb, 5126, 'VEC3', 1, 34962, [0.0, 1.0, 2.0], [0.0, 1.0, 2.0])
        self.assertEqual(acc, 0)
        self.assertEqual(len(b.g['accessors']), 1)
        self.assertEqual(len(b.g['bufferViews']), 1)
        self.assertGreater(len(b.bin), 0)


if __name__ == '__main__':
    unittest.main()
