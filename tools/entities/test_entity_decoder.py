import base64
import struct
import zlib
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'world')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from entity_decoder import (
    _legacy_skin_weights,
    _legacy_uv_channel,
    ANIMATION,
    ANIM_GROUP,
    ANIM_GROUP_LIST,
    ANIM_ROT_INT16,
    ANIM_VEC1_DOF,
    ANIM_VEC2_DOF_FLOAT16,
    ANIM_VEC3_DOF,
    ANIM_VEC3_FLOAT16,
    ANIM_ZLIB_BLOB,
    ANIM_LOCATOR,
    _decode_external_animation_channel,
    UV0_VERTEX_SEMANTIC,
    _decode_inline_animation_channel,
    _extract_character_uv_stream,
    _parse_counted_matrix_palette,
    _parse_memory_vertex_description,
    decode_entity_meshes,
    extract_entity_skeletons,
)
from test_cell_materials import (
    GEOMETRY,
    MEMORY_INDEX_LIST,
    MEMORY_VERTEX_LIST,
    PRIMITIVE_GROUP,
    ROOT,
    _chunk,
    _p3d_string,
    material_fixture,
)


def _packed_vertex(weights, palette_slots, position=(0.0, 0.0, 0.0)):
    """A single verified-layout 56-byte character vertex test fixture."""
    raw = bytearray(56)
    struct.pack_into("<3f", raw, 0, *position)
    struct.pack_into("<3f", raw, 40, *weights)
    struct.pack_into("<4B", raw, 52, *palette_slots)
    return bytes(raw)


def _skinned_character_fixture(aux_stride=8, first_weights=(0.25, 0.50, 0.0)):
    """Synthetic 0x1000D + dual-stream fixture with no game-derived data."""
    vertices = b"".join((
        _packed_vertex(first_weights, (0, 1, 2, 1)),
        _packed_vertex((0.00, 0.00, 0.0), (2, 2, 2, 2), (1.0, 0.0, 0.0)),
        _packed_vertex((0.20, 0.30, 0.4), (1, 0, 2, 1), (0.0, 1.0, 0.0)),
    ))
    main = _chunk(MEMORY_VERTEX_LIST, struct.pack("<HHII", 1, 2, 2, len(vertices)) + vertices)
    if aux_stride == 8:
        auxiliary_data = struct.pack("<6f", 0.25, 0.75, -0.5, 1.5, 1.25, -0.25)
    elif aux_stride == 12:
        auxiliary_data = b"".join((
            struct.pack("<I2f", 0xFFFFFFFF, 0.25, 0.75),
            struct.pack("<I2f", 0xFF00FF00, -0.5, 1.5),
            struct.pack("<I2f", 0xFFFF0000, 1.25, -0.25),
        ))
    else:
        raise ValueError("test fixture only supports stride 8 or 12")
    auxiliary = _chunk(MEMORY_VERTEX_LIST, struct.pack("<HHII", 1, 2, 1, len(auxiliary_data)) + auxiliary_data)
    indices = _chunk(MEMORY_INDEX_LIST, struct.pack("<HHII3H", 1, 2, 0, 6, 0, 1, 2))
    palette = _chunk(0x0001000D, struct.pack("<4I", 3, 46, 45, 63))
    primitive_payload = struct.pack("<I", 0) + _p3d_string("test_shader") + struct.pack(
        "<9I", 0, 12689, 3, 3, 3, 1, 1, 0, 0)
    primitive = _chunk(PRIMITIVE_GROUP, primitive_payload, main + auxiliary + indices + palette)
    geometry = _chunk(GEOMETRY, _p3d_string("test_character_shape"), primitive)
    return _chunk(ROOT, b"", geometry)


class EntityDecoderTests(unittest.TestCase):
    def test_legacy_uv_list_skips_count_and_channel_headers(self):
        payload = struct.pack("<II4f", 2, 0, 0.25, 0.5, 0.75, 1.0)
        self.assertEqual(struct.unpack("<4f", _legacy_uv_channel([payload], 2)), (0.25, 0.5, 0.75, 1.0))

    def test_legacy_weight_list_semantics_pair_with_raw_dcba_matrix_bytes(self):
        for got,want in zip(_legacy_skin_weights(0.1, 0.2, 0.3), [0.4, 0.3, 0.2, 0.1]):
            self.assertAlmostEqual(got,want)
    def test_decodes_entity_meshes_and_textures(self):
        data = material_fixture(texture_name="test_mat.dds", tex_format=b"DXT1")
        result = decode_entity_meshes(data)
        self.assertEqual(len(result["meshes"]), 2)
        self.assertEqual(len(result["textures"]), 1)
        self.assertEqual(result["textures"][0]["format"], "DXT1")
        self.assertEqual(result["textures"][0]["width"], 4)
        self.assertEqual(result["textures"][0]["height"], 4)
        self.assertEqual(len(result["textures"][0]["mipmaps"][0]["data"]), 12)  # base64(DXT1 block=8 bytes)
        self.assertEqual(result["meshes"][0]["texture_key"], "entity_tex_test_mat.dds")
        self.assertEqual(result["meshes"][0]["vertex_count"], 6)
        self.assertEqual(result["meshes"][0]["triangle_count"], 2)
        self.assertEqual(result["diagnostics"], [])

    def test_counted_matrix_palette_excludes_its_count_word(self):
        self.assertEqual(_parse_counted_matrix_palette(struct.pack("<4I", 3, 46, 45, 63)), [46, 45, 63])
        with self.assertRaisesRegex(ValueError, "length mismatch"):
            _parse_counted_matrix_palette(struct.pack("<2I", 3, 46))

    def test_character_dual_stream_8byte_uv_and_counted_palette_map_exact_joint_indices(self):
        result = decode_entity_meshes(_skinned_character_fixture(aux_stride=8))
        self.assertEqual(result["diagnostics"], [])
        mesh = result["meshes"][0]
        self.assertEqual(struct.unpack("<4H", base64.b64decode(mesh["skin_indices"])[:8]), (46, 45, 63, 45))
        self.assertEqual(struct.unpack("<4f", base64.b64decode(mesh["skin_weights"])[:16]), (0.25, 0.5, 0.0, 0.25))
        self.assertEqual(struct.unpack("<2f", base64.b64decode(mesh["uv"])[:8]), (0.25, 0.75))

    def test_character_dual_stream_12byte_uv_skips_only_packed_colour_word(self):
        result = decode_entity_meshes(_skinned_character_fixture(aux_stride=12))
        self.assertEqual(result["diagnostics"], [])
        uv = base64.b64decode(result["meshes"][0]["uv"])
        self.assertEqual(struct.unpack("<6f", uv), (0.25, 0.75, -0.5, 1.5, 1.25, -0.25))
        self.assertEqual(_extract_character_uv_stream(struct.pack("<2f", 0.25, 0.75), 1, 8), struct.pack("<2f", 0.25, 0.75))

    def test_declared_uv0_offsets_decode_16_and_20byte_auxiliary_streams(self):
        def declaration(parameter, ref_size, attributes):
            records = b"".join(struct.pack("<IIIBBBH", *record) for record in attributes)
            return struct.pack("<4I", 0x00020001, parameter, ref_size, len(records)) + records

        # These are synthetic records exercising the exact 17-byte 0x10014
        # grammar.  In particular, UV0 is selected by its semantic hash, not
        # by assuming it is at the start of an arbitrary auxiliary record.
        desc16 = declaration(1, 16, (
            (UV0_VERTEX_SEMANTIC, 0, 0, 16, 0, 2, 0),
            (UV0_VERTEX_SEMANTIC + 1, 0, 8, 16, 0, 2, 0),
        ))
        parameter, ref_size, attributes = _parse_memory_vertex_description(desc16)
        self.assertEqual((parameter, ref_size), (1, 16))
        stream16 = struct.pack("<4f", 0.25, 0.75, 0.0, 0.0)
        self.assertEqual(_extract_character_uv_stream(stream16, 1, 16, attributes), struct.pack("<2f", 0.25, 0.75))

        desc20 = declaration(1, 20, (
            (0x3898FC04, 0, 0, 20, 0, 4, 0x100),
            (UV0_VERTEX_SEMANTIC, 0, 4, 20, 0, 2, 0),
            (UV0_VERTEX_SEMANTIC + 1, 0, 12, 20, 0, 2, 0),
        ))
        _parameter, _ref_size, attributes = _parse_memory_vertex_description(desc20)
        stream20 = struct.pack("<5f", 0.0, 0.25, 0.75, 0.0, 0.0)
        self.assertEqual(_extract_character_uv_stream(stream20, 1, 20, attributes), struct.pack("<2f", 0.25, 0.75))
        with self.assertRaisesRegex(ValueError, "exactly one declared UV0"):
            _extract_character_uv_stream(stream20, 1, 20, [])

    def test_inline_translation_and_quaternion_channel_grammars_preserve_all_samples(self):
        # Inline Vector_1D_OF: version/FourCC, dynamic axis=Y, full constant
        # vector, count, frames, then exactly one float per frame.
        vec1 = struct.pack("<I4sH3fI2H2f", 0, b"TRAN", 1, 10.0, 20.0, 30.0, 2, 4, 8, 1.25, 2.5)
        kind, track = _decode_inline_animation_channel(ANIM_VEC1_DOF, vec1, 4.0)
        self.assertEqual(kind, "pos")
        self.assertEqual(track, {"times": [1.0, 2.0], "values": [[10.0, 1.25, 30.0], [10.0, 2.5, 30.0]]})

        # Prototype's compact 2-DOF form retains the identical 26-byte
        # standard header but stores the two dynamic values as IEEE binary16.
        compact2 = struct.pack("<I4sH3fI2H4e", 0, b"TRAN", 0, 9.0, 8.0, 7.0, 2, 2, 6, 0.5, -0.5, 0.0, 1.0)
        kind, track = _decode_inline_animation_channel(ANIM_VEC2_DOF_FLOAT16, compact2, 2.0)
        self.assertEqual(kind, "pos")
        self.assertEqual(track["times"], [1.0, 3.0])
        self.assertEqual(track["values"][0], [9.0, 0.5, -0.5])
        self.assertEqual(track["values"][1], [9.0, 0.0, 1.0])

        # Compact Vector3 is uint16 frames plus three binary16 components;
        # values are not translated, offset, or key-reduced by the decoder.
        compact3 = struct.pack("<I4sI H3e", 0, b"TRAN", 1, 3, 0.0, 0.5, -0.5)
        kind, track = _decode_inline_animation_channel(ANIM_VEC3_FLOAT16, compact3, 3.0)
        self.assertEqual(kind, "pos")
        self.assertEqual(track, {"times": [1.0], "values": [[0.0, 0.5, -0.5]]})

        quat = struct.pack("<I4sI H3h", 1, b"ROT\0", 1, 0, 0, 0, 0)
        kind, track = _decode_inline_animation_channel(ANIM_ROT_INT16, quat, 30.0)
        self.assertEqual(kind, "rot")
        self.assertEqual(track, {"times": [0.0], "values": [[0.0, 0.0, 0.0, 1.0]]})
        with self.assertRaisesRegex(ValueError, "exact expected"):
            _decode_inline_animation_channel(ANIM_ROT_INT16, quat + b"\0", 30.0)

    def test_external_vector_locator_preserves_half_float_root_translation(self):
        # The locator owns only frame/value data.  The external 0x121119
        # header's inline count is zero, and values begin after u16 frames
        # rounded up to the source blob's four-byte alignment.
        vector3_header = struct.pack("<I4sI", 0, b"TRAN", 0)
        blob = struct.pack("<2H6e", 0, 30, 0.25, 1.0, -0.5, 2.0, 3.0, 4.0)
        kind, track = _decode_external_animation_channel(
            ANIM_VEC3_FLOAT16, vector3_header, struct.pack("<III", 0, 2, 0), blob, 30.0)
        self.assertEqual(kind, "pos")
        self.assertEqual(track["times"], [0.0, 1.0])
        self.assertEqual(track["values"], [[0.25, 1.0, -0.5], [2.0, 3.0, 4.0]])

        # 0x121118 retains its three source binary32 constants and replaces
        # precisely the two non-fixed axes with binary16 external values.
        vector2_header = struct.pack("<I4sH3fI", 0, b"TRAN", 1, 9.0, 8.0, 7.0, 0)
        blob = struct.pack("<H2xe", 5, 1.5) + struct.pack("<e", -2.0)
        kind, track = _decode_external_animation_channel(
            ANIM_VEC2_DOF_FLOAT16, vector2_header, struct.pack("<III", 0, 1, 0), blob, 10.0)
        self.assertEqual(kind, "pos")
        self.assertEqual(track["times"], [0.5])
        self.assertEqual(track["values"], [[1.5, 8.0, -2.0]])

    def test_zlib_parent_does_not_reclassify_a_sibling_inline_root_channel(self):
        # Real Prototype clips mix an external rotation/blob channel with an
        # inline sparse vector channel. Presence of a parent blob must not
        # discard this valid inline Motion_Root translation.
        inline_root = _chunk(
            ANIM_VEC3_DOF,
            struct.pack("<I4sI2H6f", 0, b"TRAN", 2, 0, 4, 0.0, 1.0, 0.0, 2.0, 1.0, -3.0),
        )
        group = _chunk(
            ANIM_GROUP,
            struct.pack("<I", 0) + _p3d_string("Motion_Root") + struct.pack("<II", 7, 1),
            inline_root,
        )
        raw_blob = b"zlib sibling is not this inline track"
        compressed = zlib.compress(raw_blob)
        zblob = _chunk(ANIM_ZLIB_BLOB, b"\0" * 8 + struct.pack("<II", len(raw_blob), len(compressed)) + compressed)
        group_list = _chunk(ANIM_GROUP_LIST, b"", group)
        animation = _chunk(
            ANIMATION,
            struct.pack("<I", 0) + _p3d_string("mixed_storage_root") + struct.pack("<4sffI", b"ANIM", 4.0, 4.0, 0),
            zblob + group_list,
        )
        result = decode_entity_meshes(_chunk(ROOT, b"", animation))
        self.assertEqual(result["diagnostics"], [])
        track = result["animations"][0]["groups"][0]["pos"]
        self.assertEqual(track["times"], [0.0, 1.0])
        self.assertEqual(track["values"], [[0.0, 1.0, 0.0], [2.0, 1.0, -3.0]])

    def test_source_animation_without_bone_transform_is_listed_not_silently_dropped(self):
        # Float_1 ``STE`` is a source-side metadata/event-style channel here,
        # intentionally not invented into a bone transform.  Its enclosing
        # valid 0x121000 clip must nevertheless remain visible to the caller.
        source_channel = _chunk(0x00121100, struct.pack("<I4sI2H2f", 0, b"STE\0", 2, 0, 4, 0.0, 1.0))
        group = _chunk(ANIM_GROUP,
                       struct.pack("<I", 0) + _p3d_string("metadata_group") + struct.pack("<II", 7, 1),
                       source_channel)
        group_list = _chunk(ANIM_GROUP_LIST, b"", group)
        animation = _chunk(ANIMATION,
                           struct.pack("<I", 0) + _p3d_string("source_only_action")
                           + struct.pack("<4sffI", b"ANIM", 5.0, 30.0, 0),
                           group_list)
        result = decode_entity_meshes(_chunk(ROOT, b"", animation))
        self.assertEqual(result["diagnostics"], [])
        self.assertEqual(len(result["animations"]), 1)
        self.assertEqual(result["animations"][0]["name"], "source_only_action")
        self.assertFalse(result["animations"][0]["playable"])
        self.assertEqual(result["animations"][0]["groups"], [])

    def test_packed_float32_rounding_residue_is_preserved_not_clamped(self):
        # This three-float sequence reconstructs a -4.470348358154297e-08
        # fourth component after binary32 storage, matching the observed
        # magnitude in the real blade package without embedding game bytes.
        result = decode_entity_meshes(_skinned_character_fixture(
            first_weights=(0.989411473274231, 0.00834706425666809, 0.0022415071725845337)))
        self.assertEqual(result["diagnostics"], [])
        weights = struct.unpack("<4f", base64.b64decode(result["meshes"][0]["skin_weights"])[:16])
        self.assertLess(weights[3], 0.0)
        self.assertGreater(weights[3], -1e-6)

    def test_materially_negative_implicit_weight_is_rejected(self):
        result = decode_entity_meshes(_skinned_character_fixture(first_weights=(1.0, 0.1, 0.0)))
        self.assertIsNone(result["meshes"][0]["skin_indices"])
        self.assertTrue(any("invalid packed skin weights" in item for item in result["diagnostics"]))

    def test_skin_uses_declared_external_skeleton_never_skeleton_zero_fallback(self):
        # This fixture encodes the exact Skin -> PrimitiveGroup and
        # CompositeDrawable2 -> Skin edges.  The local skeleton has a
        # deliberately different name, so list position cannot satisfy the
        # binding.
        identity = struct.pack("<16f", *(1.0 if i in (0, 5, 10, 15) else 0.0 for i in range(16)))

        def skeleton(name):
            joint = _chunk(0x00023001, _p3d_string("root") + struct.pack("<I", 0) + identity)
            header = _p3d_string(name) + struct.pack("<4I", 1, 1, 0, 0)
            return _chunk(0x00023000, header, joint)

        verts = _packed_vertex((0.0, 0.0, 0.0), (0, 0, 0, 0))
        main = _chunk(MEMORY_VERTEX_LIST, struct.pack("<HHII", 1, 2, 2, len(verts)) + verts)
        aux = _chunk(MEMORY_VERTEX_LIST, struct.pack("<HHII2f", 1, 2, 1, 8, 0.0, 0.0))
        indices = _chunk(MEMORY_INDEX_LIST, struct.pack("<HHII3H", 1, 2, 0, 6, 0, 0, 0))
        palette = _chunk(0x0001000D, struct.pack("<2I", 1, 0))
        pg_payload = struct.pack("<I", 0) + _p3d_string("test_shader") + struct.pack(
            "<9I", 0, 12689, 1, 3, 1, 1, 1, 0, 0)
        primitive = _chunk(PRIMITIVE_GROUP, pg_payload, main + aux + indices + palette)
        skin_name, donor_name = "armour_panel", "base_rig"
        skin = _chunk(0x00010001,
                      _p3d_string(skin_name) + struct.pack("<I", 3) + _p3d_string(donor_name) + struct.pack("<I", 1),
                      primitive)
        cd_primitive = _chunk(0x00123001,
                              struct.pack("<II", 0, 0) + _p3d_string(skin_name) + struct.pack("<II", 2, 0))
        composite = _chunk(0x00123000,
                           struct.pack("<I", 0) + _p3d_string("armour_assembly") + _p3d_string(donor_name) + struct.pack("<I", 1),
                           cd_primitive)
        data = _chunk(ROOT, b"", skeleton("wrong_local_rig") + skin + composite)

        donor_data = _chunk(ROOT, b"", skeleton(donor_name) + skeleton("unreferenced_donor_rig"))
        donors = extract_entity_skeletons(donor_data, source_entry=r"\art\alex\alex.p3d.rz")
        result = decode_entity_meshes(data, external_skeletons=donors)

        self.assertEqual(result["diagnostics"], [])
        self.assertEqual(result["meshes"][0]["skin_name"], skin_name)
        self.assertEqual(result["meshes"][0]["skeleton_name"], donor_name)
        by_name = {item["name"]: item for item in result["skeletons"]}
        # The unreferenced local rig is deliberately not materialised; source
        # file skeleton count must not dictate the assembled character rig.
        self.assertEqual(set(by_name), {donor_name})
        self.assertEqual(by_name[donor_name]["source_entry"], r"\art\alex\alex.p3d.rz")
        self.assertEqual(result["assemblies"][0]["name"], "armour_assembly")
        self.assertTrue(result["assemblies"][0]["primitives"][0]["skin_skeleton_matches"])

        unresolved = decode_entity_meshes(data)
        self.assertEqual(unresolved["meshes"][0]["skeleton_name"], donor_name)
        self.assertTrue(any("declared skeleton 'base_rig' is unavailable" in msg for msg in unresolved["diagnostics"]))

class MaterialChannelAliasTest(unittest.TestCase):
    """Shader templates name the base-colour map several different ways.

    Only 'color' used to be honoured, so every env_vehicle_military mesh (which
    calls it 'camo') and the whipfist ('diffuseTexture') exported untextured.
    """

    def test_known_color_aliases_map_to_color(self):
        from entity_decoder import _material_channel_for_parameter
        for parameter in ("color", "camo", "diffuseTexture", "add_color", "bottom"):
            self.assertEqual(_material_channel_for_parameter(parameter), "color", parameter)

    def test_normal_and_specular_aliases(self):
        from entity_decoder import _material_channel_for_parameter
        for parameter in ("normal", "normalmap", "rivetsNm"):
            self.assertEqual(_material_channel_for_parameter(parameter), "normal", parameter)
        for parameter in ("specular", "specularMap"):
            self.assertEqual(_material_channel_for_parameter(parameter), "specular", parameter)

    def test_non_material_parameters_are_ignored(self):
        # Reflection probes, decals, damage overlays and palette strips are not
        # a base map; guessing one of them as colour would tint whole vehicles.
        from entity_decoder import _material_channel_for_parameter
        for parameter in ("cube_map", "decals", "palette_strip", "glass_damage",
                          "overlay", "reflection", "grime", "gore_map", "damage"):
            self.assertIsNone(_material_channel_for_parameter(parameter), parameter)


if __name__ == '__main__':
    unittest.main()
