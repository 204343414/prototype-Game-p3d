import struct
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'world')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from entity_decoder import decode_entity_meshes
from test_cell_materials import material_fixture

class EntityDecoderTests(unittest.TestCase):
    def test_decodes_entity_meshes_and_textures(self):
        data = material_fixture(texture_name="test_mat.dds", tex_format=b"DXT1")
        result = decode_entity_meshes(data)
        self.assertEqual(len(result["meshes"]), 2)
        self.assertEqual(len(result["textures"]), 1)
        self.assertEqual(result["textures"][0]["format"], "DXT1")
        self.assertEqual(result["meshes"][0]["vertex_count"], 6)
        self.assertEqual(result["meshes"][0]["triangle_count"], 2)

if __name__ == '__main__':
    unittest.main()
