import struct
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'world')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from entity_catalog import (
    CATEGORY_POWERS, CATEGORY_VEHICLES, CATEGORY_CHARACTERS, CATEGORY_PEDESTRIANS, CATEGORY_PROPS,
    classify_entry, inspect_p3d_package, parse_props_library,
    GEOMETRY, POLYSKIN, SKELETON_V1, SKELETON_V2, ANIMATION, COMPOSITE_DRAWABLE, TEXTURE
)

def _p3d_string(text):
    raw = text.encode('ascii') + b'\0'
    return bytes([len(raw)]) + raw

def _chunk(type_id, payload=b'', children=b''):
    header_size = 12 + len(payload)
    return struct.pack('<III', type_id, header_size, header_size + len(children)) + payload + children

class EntityCatalogTests(unittest.TestCase):
    def test_classifies_standard_paths(self):
        self.assertEqual(classify_entry(r"\art\alex\alex.p3d.rz"), CATEGORY_POWERS)
        self.assertEqual(classify_entry(r"\art\packages\powers\alex_claws\alex_claws.p3d.rz"), CATEGORY_POWERS)
        self.assertEqual(classify_entry(r"\art\packages\vehicles\ambulance\ambulance.p3d.rz"), CATEGORY_VEHICLES)
        self.assertEqual(classify_entry(r"\art\packages\missions\tank_ram_marine\tank_ram_marine.p3d.rz"), CATEGORY_VEHICLES)
        self.assertEqual(classify_entry(r"\art\packages\missions\DanaMercer\DanaMercer.p3d.rz"), CATEGORY_CHARACTERS)
        self.assertEqual(classify_entry(r"\art\packages\missions\Brawler\Brawler.p3d.rz"), CATEGORY_CHARACTERS)
        self.assertEqual(classify_entry(r"\art\packages\pedestrians\ped_f_LI_01_MT_01_MT_01\ped_f_LI_01_MT_01_MT_01.p3d.rz"), CATEGORY_PEDESTRIANS)
        self.assertEqual(classify_entry(r"\art\locations\manhattan\props.p3d.rz"), CATEGORY_PROPS)
        self.assertIsNone(classify_entry(r"\art\alex\alex_tod.p3d.rz"))
        self.assertIsNone(classify_entry(r"\art\packages\powers\alex_claws\alex_claws_fig.p3d.rz"))

    def test_inspects_package_structure(self):
        geom = _chunk(GEOMETRY, _p3d_string("clawMesh") + struct.pack('<I', 0))
        skel = _chunk(SKELETON_V1, _p3d_string("clawSkel"))
        anim = _chunk(ANIMATION, _p3d_string("claw_slash"))
        tex = _chunk(TEXTURE, _p3d_string("claw_tex.dds"))
        p3d = _chunk(0xFF443350, b'', geom + skel + anim + tex)
        
        info = inspect_p3d_package(p3d)
        self.assertIn("clawMesh", info["geometries"])
        self.assertIn("clawSkel", info["skeletons"])
        self.assertIn("claw_slash", info["animations"])
        self.assertIn("claw_tex.dds", info["textures"])

    def test_groups_props_destruction_shapes(self):
        s1 = _chunk(GEOMETRY, _p3d_string("waterTowerOS001InitialShape") + struct.pack('<I', 0))
        s2 = _chunk(GEOMETRY, _p3d_string("waterTowerOS001DestroyedShape") + struct.pack('<I', 0))
        s3 = _chunk(GEOMETRY, _p3d_string("airconditionerOS001InitialShape") + struct.pack('<I', 0))
        p3d = _chunk(0xFF443350, b'', s1 + s2 + s3)
        
        props = parse_props_library(p3d)
        names = {p["id"]: p["shapes"] for p in props}
        self.assertIn("waterTowerOS001", names)
        self.assertEqual(len(names["waterTowerOS001"]), 2)
        self.assertIn("airconditionerOS001", names)

if __name__ == '__main__':
    unittest.main()
