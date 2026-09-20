import base64
import os
import sys
import struct
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from cell_materials import (
    COLOR_LAYOUT, ROAD_LAYOUT, ROAD_TEMPLATE, DECAL_LAYOUT, DECAL_TEMPLATES,
    INTERIOR_LAYOUT, INTERIOR_TEMPLATES, attach_local_materials, compressed_texture,
    build_texture_index
)
from export_static_geometry_diagnostic import scan_core_triangle_geometry, _preview_data, MEMORY_INDEX_LIST

ROOT = 0xFF443350
GEOMETRY = 0x00010000
PRIMITIVE_GROUP = 0x00010020
MEMORY_VERTEX_LIST = 0x00010012
MEMORY_INDEX_LIST = 0x00010013
VERTEX_DECLARATION = 0x00010014
NEW_SHADER = 0x00011015
NEW_SHADER_STRING_PARAMETER = 0x00011016
TEXTURE = 0x00019000
IMAGE_DATA = 0x00019002
TEXTURE_DDS = 0x00019006

def _chunk(type_id, payload=b'', children=b''):
    header_size = 12 + len(payload)
    return struct.pack('<III', type_id, header_size, header_size + len(children)) + payload + children

def _p3d_string(text):
    raw = text.encode('ascii') + b'\0'
    return bytes([len(raw)]) + raw

def _synthetic_dds(width=4, height=4, mips=1, format_fourcc=b'DXT1'):
    block_size = 8 if format_fourcc == b'DXT1' else 16
    total_data = bytearray()
    w, h = width, height
    for _ in range(mips):
        blocks = max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
        total_data.extend(b'\x01' * (blocks * block_size))
        w, h = max(1, w // 2), max(1, h // 2)
    
    header = bytearray(128)
    header[:4] = b'DDS '
    struct.pack_into('<I', header, 4, 124)
    struct.pack_into('<I', header, 8, 0x1 | 0x2 | 0x4 | 0x1000 | (0x20000 if mips > 1 else 0))
    struct.pack_into('<II', header, 12, height, width)
    struct.pack_into('<I', header, 20, len(total_data))
    struct.pack_into('<I', header, 28, mips)
    struct.pack_into('<I', header, 76, 32)
    struct.pack_into('<I', header, 80, 4)
    header[84:88] = format_fourcc
    struct.pack_into('<I', header, 108, 0x1000 | (0x400008 if mips > 1 else 0))
    dds = bytes(header) + bytes(total_data)
    return struct.pack('<I', len(dds)) + dds

def _decl_bytes(layout_kind='68_standard'):
    if layout_kind == '68_standard':
        attrs = [
            struct.pack('<IIIHHB', 0x2C929929, 0, 0, 68, 4, 0),
            struct.pack('<IIIHHB', 0x3898FC04, 7, 16, 68, 4, 1),
            struct.pack('<IIIHHB', 0x3898FC05, 7, 20, 68, 4, 1),
            struct.pack('<IIIHHB', 0x00364509, 0, 24, 68, 2, 0),
            struct.pack('<IIIHHB', 0x0036450A, 0, 32, 68, 2, 0),
            struct.pack('<IIIHHB', 0xC206BCE7, 0, 40, 68, 3, 1),
            struct.pack('<IIIHHB', 0xA4176245, 0, 52, 68, 4, 1),
        ]
        stride = 68
    elif layout_kind == '52_road':
        attrs = [
            struct.pack('<IIIHHB', 0x2C929929, 0, 0, 52, 4, 0),
            struct.pack('<IIIHHB', 0x3898FC04, 7, 16, 52, 4, 1),
            struct.pack('<IIIHHB', 0x3898FC05, 7, 20, 52, 4, 1),
            struct.pack('<IIIHHB', 0x00364509, 0, 24, 52, 2, 0),
            struct.pack('<IIIHHB', 0x0036450A, 0, 32, 52, 2, 0),
            struct.pack('<IIIHHB', 0xC206BCE7, 0, 40, 52, 3, 1),
        ]
        stride = 52
    elif layout_kind == '32_decal':
        attrs = [
            struct.pack('<IIIHHB', 0x2C929929, 0, 0, 32, 4, 0),
            struct.pack('<IIIHHB', 0x3898FC04, 7, 16, 32, 4, 1),
            struct.pack('<IIIHHB', 0x3898FC05, 7, 20, 32, 4, 1),
            struct.pack('<IIIHHB', 0x00364509, 0, 24, 32, 2, 0),
        ]
        stride = 32
    elif layout_kind == '64_interior':
        attrs = [
            struct.pack('<IIIHHB', 0x2C929929, 0, 0, 64, 4, 0),
            struct.pack('<IIIHHB', 0x3898FC04, 7, 16, 64, 4, 1),
            struct.pack('<IIIHHB', 0x00364509, 0, 20, 64, 2, 0),
            struct.pack('<IIIHHB', 0x0036450A, 0, 28, 64, 2, 0),
            struct.pack('<IIIHHB', 0xC206BCE7, 0, 36, 64, 3, 1),
            struct.pack('<IIIHHB', 0xA4176245, 0, 48, 64, 4, 1),
        ]
        stride = 64
    else:
        raise ValueError(f"unknown {layout_kind}")
    return b''.join(attrs), stride

def _triangle_group_chunks(shader_name, layout_kind, u_val=0.5, v_val=0.25):
    decl_raw, stride = _decl_bytes(layout_kind)
    triangles = [
        (-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
        (0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0),
    ]
    raw_vertices = bytearray(len(triangles) * stride)
    for i, (x, y, z) in enumerate(triangles):
        struct.pack_into('<fff', raw_vertices, i * stride, x, y, z)
        for offset in (16, 20, 24, 32):
            if offset + 8 <= stride:
                struct.pack_into('<ff', raw_vertices, i * stride + offset, u_val, v_val)
    indices = [0, 1, 2, 3, 4, 5]
    
    vertex_list = _chunk(MEMORY_VERTEX_LIST, struct.pack("<III", 0x20001, 0, len(raw_vertices)) + bytes(raw_vertices))
    index_list = _chunk(
        MEMORY_INDEX_LIST,
        struct.pack("<III", 0x20001, 0, len(indices) * 2) + struct.pack("<" + "H" * len(indices), *indices),
    )
    description = _chunk(VERTEX_DECLARATION, struct.pack("<IIII", 1, 2, len(raw_vertices), len(decl_raw)) + decl_raw)
    pg_header = struct.pack("<I", 1) + _p3d_string(shader_name) + struct.pack(
        "<9I", 0, 0x3011, len(triangles), len(indices), 0, 1, 1, 0, 0)
    return _chunk(PRIMITIVE_GROUP, pg_header, description + vertex_list + index_list)

def material_fixture(texture_name='wood', param_name='color', layout_kind='68_standard', template='env_building', tex_format=b'DXT1', include_texture=True):
    pg1 = _triangle_group_chunks('mat_a', layout_kind)
    pg2 = _triangle_group_chunks('mat_a', layout_kind)
    geom = _chunk(GEOMETRY, _p3d_string('mergedDrawableRootNoShadow') + struct.pack('<I', 2), pg1 + pg2)
    tex_chunks = b''
    if include_texture:
        dds = _synthetic_dds(width=4, height=4, mips=1, format_fourcc=tex_format)
        tex_dds = _chunk(TEXTURE_DDS, _p3d_string(texture_name) + struct.pack('<6I', 1, 4, 4, 0, 0, 1) + tex_format)
        img = _chunk(IMAGE_DATA, dds)
        tex_chunks = _chunk(TEXTURE, _p3d_string(texture_name) + struct.pack('<IIII', 4, 4, 8, 1), tex_dds + img)
    param = _chunk(NEW_SHADER_STRING_PARAMETER, _p3d_string(param_name) + _p3d_string(texture_name))
    shader = _chunk(NEW_SHADER, _p3d_string('mat_a') + struct.pack('<I', 0) + _p3d_string(template) + struct.pack('<I', 0), param)
    return _chunk(ROOT, b'', geom + tex_chunks + shader)

class CellMaterialTests(unittest.TestCase):
    def test_links_local_texture_for_standard_68byte_building_layout(self):
        data = material_fixture()
        groups, rep = scan_core_triangle_geometry(data, 'test')
        self.assertEqual(len(groups), 2)
        meshes = _preview_data(groups)['meshes']
        textures, report = attach_local_materials(data, groups, meshes)
        self.assertEqual(report['linked_groups'], 2)
        self.assertEqual(report['gray_groups'], 0)
        self.assertEqual(len(textures), 1)
        self.assertEqual(meshes[0]['texture'], textures[0]['key'])
        self.assertEqual(meshes[0]['texture_parameter'], 'color')

    def test_links_52byte_road_layout_from_bottom_texture(self):
        data = material_fixture(param_name='bottom', layout_kind='52_road', template='zCBV2_env_road')
        groups, rep = scan_core_triangle_geometry(data, 'test')
        self.assertEqual(len(groups), 2)
        meshes = _preview_data(groups)['meshes']
        textures, report = attach_local_materials(data, groups, meshes)
        self.assertEqual(report['linked_groups'], 2)
        self.assertEqual(report['road_groups'], 2)
        self.assertEqual(meshes[0]['texture_parameter'], 'bottom')

    def test_links_32byte_decal_layout(self):
        data = material_fixture(layout_kind='32_decal', template='env_decal')
        groups, rep = scan_core_triangle_geometry(data, 'test')
        self.assertEqual(len(groups), 2)
        meshes = _preview_data(groups)['meshes']
        textures, report = attach_local_materials(data, groups, meshes)
        self.assertEqual(report['linked_groups'], 2)
        self.assertEqual(report['decal_groups'], 2)
        self.assertEqual(meshes[0]['preview_render_mode'], 'source_alpha')

    def test_links_64byte_interior_layout(self):
        data = material_fixture(layout_kind='64_interior', template='env_building_noglass')
        groups, rep = scan_core_triangle_geometry(data, 'test')
        self.assertEqual(len(groups), 2)
        meshes = _preview_data(groups)['meshes']
        textures, report = attach_local_materials(data, groups, meshes)
        self.assertEqual(report['linked_groups'], 2)
        self.assertEqual(report['interior_groups'], 2)
        self.assertEqual(report['gray_groups'], 0)

    def test_dxt3_compressed_texture_support(self):
        dds = _synthetic_dds(format_fourcc=b'DXT3')
        tex = compressed_texture(dds, (4, 4, 1, 'DXT3'))
        self.assertEqual(tex['format'], 'DXT3')

    def test_exact_shared_reference_resolves_missing_local_texture(self):
        shared = build_texture_index(material_fixture(texture_name='shared_wood'))
        data = material_fixture(texture_name='shared_wood', include_texture=False)
        groups, _ = scan_core_triangle_geometry(data, 'test')
        meshes = _preview_data(groups)['meshes']
        textures, report = attach_local_materials(data, groups, meshes, shared)
        self.assertEqual(report['linked_groups'], 2)
        self.assertEqual(report['shared_groups'], 2)
        self.assertEqual(len(textures), 1)
        self.assertEqual(meshes[0]['texture_source'], 'shared')

    def test_local_texture_takes_precedence_over_shared(self):
        shared = build_texture_index(material_fixture(texture_name='wood'))
        data = material_fixture(texture_name='wood')
        groups, _ = scan_core_triangle_geometry(data, 'test')
        meshes = _preview_data(groups)['meshes']
        _, report = attach_local_materials(data, groups, meshes, shared)
        self.assertEqual(report['shared_groups'], 0)
        self.assertEqual(meshes[0]['texture_source'], 'local')

    def test_keeps_original_bounded_mips_dxt1_dxt3_dxt5(self):
        for fmt in (b'DXT1', b'DXT3', b'DXT5'):
            image = _synthetic_dds(width=512, height=512, mips=10, format_fourcc=fmt)
            texture_full = compressed_texture(image, (512, 512, 10, fmt.decode('ascii')))
            self.assertEqual(texture_full['mips'][0]['width'], 512)
            self.assertEqual(base64.b64decode(texture_full['mips'][0]['data'])[0], 1)
            self.assertEqual(texture_full['mips'][-1]['width'], 1)
            texture_bounded = compressed_texture(image, (512, 512, 10, fmt.decode('ascii')), max_edge=256)
            self.assertEqual(texture_bounded['mips'][0]['width'], 256)
            self.assertEqual(base64.b64decode(texture_bounded['mips'][0]['data'])[0], 1)
            self.assertEqual(texture_bounded['mips'][-1]['width'], 1)

    def test_truncated_chain_with_correct_prefix_is_rejected(self):
        image = _synthetic_dds(width=4, height=4, mips=1)
        truncated = struct.pack('<I', len(image) - 5) + image[4:-1]
        with self.assertRaisesRegex(ValueError, 'truncated DDS mip chain'):
            compressed_texture(truncated, (4, 4, 1, 'DXT1'))
        extra = struct.pack('<I', len(image) - 3) + image[4:] + b'x'
        with self.assertRaisesRegex(ValueError, 'trailing bytes'):
            compressed_texture(extra, (4, 4, 1, 'DXT1'))

    def test_refuses_mismatch_truncation_and_cubemap(self):
        image = _synthetic_dds(width=4, height=4, mips=1)
        with self.assertRaises(ValueError):
            compressed_texture(image, (8, 8, 4, 'DXT1'))
        with self.assertRaises(ValueError):
            compressed_texture(image[:-1], (4, 4, 1, 'DXT1'))
        cube = bytearray(image)
        struct.pack_into('<I', cube, 4 + 112, 0x200)
        with self.assertRaises(ValueError):
            compressed_texture(bytes(cube), (4, 4, 1, 'DXT1'))
        with self.assertRaises(ValueError):
            compressed_texture(_synthetic_dds(width=4, height=4, format_fourcc=b'DXT2'), (4, 4, 1, 'DXT2'))

if __name__ == '__main__':
    unittest.main()
