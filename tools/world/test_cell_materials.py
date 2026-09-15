"""Real synthetic P3D/DDS paths; no game assets or mocked texture decoders."""
import base64
import struct
import unittest

from cell_materials import COLOR_LAYOUT, attach_local_materials, compressed_texture, build_texture_index
from export_static_geometry_diagnostic import scan_core_triangle_geometry, _preview_data
from test_export_static_geometry_diagnostic import chunk, p3d_string as ps, geometry, p3d_file


def dds_image(width=4, algorithm='DXT1'):
    header = bytearray(128)
    header[:4] = b'DDS '
    for offset, value in ((4, 124), (8, 0x21007), (12, width), (16, width), (28, width.bit_length()), (76, 32), (80, 4), (108, 0x401008)):
        struct.pack_into('<I', header, offset, value)
    header[84:88] = algorithm.encode('ascii')
    body = bytearray()
    for level in range(width.bit_length()):
        side = max(1, width >> level)
        body.extend(bytes([level % 256]) * max(1, (side + 3) // 4) ** 2 * (8 if algorithm == 'DXT1' else 16))
    data = bytes(header + body)
    return struct.pack('<I', len(data)) + data


def material_fixture(include_texture=True, unknown_layout=False):
    attributes = [(0x2C929929, 0, 0, 4, 0), (0x3898FC04, 7, 16, 4, 1),
                  (0x3898FC05, 7, 20, 4, 1), (0x00364509, 0, 24, 2, 0),
                  (0x0036450A, 0, 32, 2, 0), (0xC206BCE7, 0, 40, 3, 1),
                  (0xA4176245, 0, 52, 4, 1)]
    if unknown_layout:
        attributes[3] = (0x12345678, 0, 24, 2, 0)
    declaration = b''.join(struct.pack('<IIIHHB', h, src, off, 68, typ, idx) for h, src, off, typ, idx in attributes)
    raw = bytearray(3 * 68)
    for i, (x, y, z) in enumerate(((0, 0, 0), (2, 0, 0), (0, 2, 0))):
        struct.pack_into('<3f', raw, i * 68, x, y, z)
        struct.pack_into('<2f', raw, i * 68 + 24, x / 2, y / 2)
    desc = chunk(0x10014, struct.pack('<IIII', 0x20001, 2, len(raw), len(declaration)) + declaration)
    vl = chunk(0x10012, struct.pack('<III', 0x20001, 0, len(raw)) + raw)
    il = chunk(0x10013, struct.pack('<III3H', 0x20001, 0, 6, 0, 1, 2))
    pg = chunk(0x10020, struct.pack('<I', 1) + ps('shader') + struct.pack('<9I', 0, 0x3011, 3, 3, 0, 1, 1, 0, 0), desc + vl + il)
    shader = chunk(0x11015, ps('shader') + struct.pack('<I', 256) + ps('zCBV2') + struct.pack('<I', 1), chunk(0x11016, ps('color') + ps('brick')))
    tex = chunk(0x19000, ps('brick'), chunk(0x19006, ps('brick') + struct.pack('<6I4s', 0, 4, 4, 0, 0, 3, b'DXT1'), chunk(0x19002, dds_image())))
    return p3d_file(geometry('mergedDrawableRootNoShadow', [pg, pg]) + shader + (tex if include_texture else b''))


class CellMaterialTests(unittest.TestCase):
    def test_exact_layout_local_binding_and_dedup(self):
        data = material_fixture()
        groups, report = scan_core_triangle_geometry(data, 'test')
        self.assertFalse(report['errors'])
        self.assertEqual(groups[0].layout_sha256, COLOR_LAYOUT)
        meshes = _preview_data(groups)['meshes']
        textures, report = attach_local_materials(data, groups, meshes)
        self.assertEqual(report['linked_groups'], 2)
        self.assertEqual(len(textures), 1)
        self.assertEqual(meshes[0]['texture'], meshes[1]['texture'])
        self.assertEqual(struct.unpack('<6f', base64.b64decode(meshes[0]['uv'])), (0, 0, 1, 0, 0, 1))
        self.assertEqual(textures[0]['format'], 'DXT1')
        self.assertEqual(len(textures[0]['mips']), 3)

    def test_unknown_layout_or_missing_texture_stays_gray(self):
        for kwargs, reason in (({'unknown_layout': True}, 'unverified_uv_layout'), ({'include_texture': False}, 'unresolved_or_unsupported_local_texture')):
            data = material_fixture(**kwargs)
            groups, _ = scan_core_triangle_geometry(data, 'test')
            meshes = _preview_data(groups)['meshes']
            textures, report = attach_local_materials(data, groups, meshes)
            self.assertEqual(textures, [])
            self.assertEqual(report['gray_reasons'][reason], 2)
            self.assertIsNone(meshes[0]['texture'])

    def test_exact_shared_reference_resolves_missing_local_texture(self):
        shared = build_texture_index(material_fixture())
        data = material_fixture(include_texture=False)
        groups, _ = scan_core_triangle_geometry(data, 'test')
        meshes = _preview_data(groups)['meshes']
        textures, report = attach_local_materials(data, groups, meshes, shared)
        self.assertEqual(report['linked_groups'], 2)
        self.assertEqual(report['shared_groups'], 2)
        self.assertEqual(len(textures), 1)
        self.assertEqual(meshes[0]['texture_source'], 'shared')

    def test_local_texture_takes_precedence_over_shared(self):
        shared = build_texture_index(material_fixture())
        data = material_fixture()
        groups, _ = scan_core_triangle_geometry(data, 'test')
        meshes = _preview_data(groups)['meshes']
        _, report = attach_local_materials(data, groups, meshes, shared)
        self.assertEqual(report['shared_groups'], 0)
        self.assertEqual(meshes[0]['texture_source'], 'local')

    def test_keeps_original_bounded_mips(self):
        image = dds_image(512, 'DXT5')
        texture = compressed_texture(image, (512, 512, 10, 'DXT5'))
        self.assertEqual(texture['mips'][0]['width'], 256)
        self.assertEqual(base64.b64decode(texture['mips'][0]['data'])[0], 1)
        self.assertEqual(texture['mips'][-1]['width'], 1)

    def test_truncated_chain_with_correct_prefix_is_rejected(self):
        image = dds_image()
        truncated = struct.pack('<I', len(image) - 5) + image[4:-1]
        with self.assertRaisesRegex(ValueError, 'truncated DDS mip chain'):
            compressed_texture(truncated, (4, 4, 3, 'DXT1'))
        extra = struct.pack('<I', len(image) - 3) + image[4:] + b'x'
        with self.assertRaisesRegex(ValueError, 'trailing bytes'):
            compressed_texture(extra, (4, 4, 3, 'DXT1'))

    def test_refuses_mismatch_truncation_and_cubemap(self):
        image = dds_image()
        with self.assertRaises(ValueError):
            compressed_texture(image, (8, 8, 4, 'DXT1'))
        with self.assertRaises(ValueError):
            compressed_texture(image[:-1], (4, 4, 3, 'DXT1'))
        cube = bytearray(image)
        struct.pack_into('<I', cube, 4 + 112, 0x200)
        with self.assertRaises(ValueError):
            compressed_texture(bytes(cube), (4, 4, 3, 'DXT1'))
        with self.assertRaises(ValueError):
            compressed_texture(dds_image(4, 'DXT3'), (4, 4, 3, 'DXT3'))


if __name__ == '__main__':
    unittest.main()
