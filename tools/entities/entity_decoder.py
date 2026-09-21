"""Entity Mesh, Skinning, Skeleton and Animation Decoder for Pure3D (Prototype 1)."""
from __future__ import annotations

import base64
import math
import os
import struct
import sys
import zlib
from typing import Any

# Ensure world tools are accessible
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "world"))
from probe_static_geometry import (
    _walk, _p3d_string, _parse_primitive_group, SIG_LE
)
from probe_cell_shader_dependencies import (
    NEW_SHADER,
    NEW_SHADER_STRING_PARAMETER,
    _parse_exact_string_pair,
    _parse_new_shader_header,
)
from render_static_uv_candidates import (
    IMAGE_DATA,
    TEXTURE,
    TEXTURE_DDS,
    _parse_texture_dds_header,
)
from cell_materials import compressed_texture

# Pure3D Chunk IDs with evidence shared by the world material parsers.
# Prototype character packages observed on 2026-09-17 use NewShader=0x11015
# and string texture parameters=0x11016.  The old generic Shader=0x11000
# layout is deliberately not treated as interchangeable.

POLYSKIN = 0x00010001
GEOMETRY = 0x00010000
PRIMITIVE_GROUP = 0x00010020
VERTEX_DESCRIPTION = 0x00010014
VERTEX_LIST = 0x00010012
INDEX_LIST = 0x00010013

# Legacy (memory_imaged == 0) chunk IDs
LEGACY_POSITIONS = 0x00010005
LEGACY_NORMALS = 0x00010006
LEGACY_UVS = 0x00010007
LEGACY_INDICES = 0x0001000A
LEGACY_MATRICES = 0x0001000B
LEGACY_WEIGHTS = 0x0001000C

MATRIX_PALETTE = 0x0001000D

# Verified on Hydra (16 B) and Supreme Hunter (20 B) field_c=1 declaration
# records.  This is the first texture-coordinate semantic; its source value is
# the UV used by each package's verified ``color`` shader chain.
UV0_VERTEX_SEMANTIC = 0x00364509

SKELETON_2 = 0x00023000
SKELETON_JOINT_2 = 0x00023001

ANIMATION = 0x00121000
ANIM_ZLIB_BLOB = 0x02F00000
ANIM_GROUP_LIST = 0x00121002
ANIM_GROUP = 0x00121001
ANIM_ROT_INT16 = 0x00121112
ANIM_ROT_INT8 = 0x00121114
ANIM_LOCATOR = 0x00121120

# Inline Pure3D channel records.  Types 0x121102--0x121105 and 0x121112
# have grammars cross-checked against the bundled NetP3DLib reference. The
# Prototype-specific compact variants 0x121114/118/119 are retained as
# separate identifiers and accepted only through their independently exact
# length grammars below.
ANIM_VEC1_DOF = 0x00121102
ANIM_VEC2_DOF = 0x00121103
ANIM_VEC3_DOF = 0x00121104
ANIM_QUAT_FLOAT = 0x00121105
ANIM_VEC2_DOF_FLOAT16 = 0x00121118
ANIM_VEC3_FLOAT16 = 0x00121119

# The source stream stores only three IEEE-754 binary32 weights; the fourth is
# algebraically implicit.  Allow no more than four binary32 ulps below zero
# when reconstructing it, but preserve the computed value (no normalization or
# clamp).  The bound covers exact 2026-09-17 claws/blades/hammer samples whose
# most-negative reconstructed component is -5.960464477539063e-08.
_IMPLICIT_WEIGHT_ROUNDING_TOLERANCE = 4 * 2.0 ** -23


def _align4(n: int) -> int:
    return (n + 3) & ~3


def _legacy_skin_weights(w0: float, w1: float, w2: float) -> list[float]:
    """Pair semantic Weight_List A/B/C/D with raw Matrix_List D/C/B/A bytes."""
    return [1.0 - (w0 + w1 + w2), w2, w1, w0]


def _legacy_uv_channel(payloads: list[bytes], vertex_count: int, channel: int = 0) -> bytes | None:
    """Return Vector2 bytes from a complete UV_List channel (count + channel header)."""
    for payload in payloads:
        if len(payload) < 8:
            continue
        uv_count, uv_channel = struct.unpack_from("<II", payload, 0)
        if uv_channel == channel and uv_count >= vertex_count and len(payload) >= 8 + uv_count * 8:
            return payload[8 : 8 + vertex_count * 8]
    return None


def _descendant_indices(children_map: dict[int, list[int]], root: int) -> list[int]:
    """Return descendants in source-tree order without treating sibling chunks as children."""
    result: list[int] = []
    pending = list(reversed(children_map.get(root, [])))
    while pending:
        index = pending.pop()
        result.append(index)
        pending.extend(reversed(children_map.get(index, [])))
    return result


def _parse_counted_matrix_palette(payload: bytes) -> list[int]:
    """Parse the verified 0x1000D ``count + uint32[count]`` layout exactly."""
    if len(payload) < 4:
        raise ValueError("Matrix_Palette is shorter than its uint32 count")
    count = struct.unpack_from("<I", payload, 0)[0]
    expected = 4 + count * 4
    if len(payload) != expected:
        raise ValueError(
            f"Matrix_Palette length mismatch: count={count} requires {expected} bytes, "
            f"got {len(payload)}")
    return list(struct.unpack_from(f"<{count}I", payload, 4))


def _parse_memory_vertex_stream(payload: bytes, vertex_count: int) -> tuple[int, bytes, int]:
    """Validate a 0x10012 header and return ``(field_c, body, stride)``.

    The field_c tag, not chunk encounter order, identifies the packed main
    stream (2) versus the auxiliary UV stream (1).
    """
    if len(payload) < 12:
        raise ValueError("Memory_Image_Vertex_List is shorter than its 12-byte header")
    _field_a, _field_b, field_c, data_size = struct.unpack_from("<HHII", payload, 0)
    if len(payload) != 12 + data_size:
        raise ValueError(
            f"Memory_Image_Vertex_List byte_count={data_size}, actual={len(payload) - 12}")
    if vertex_count <= 0 or data_size % vertex_count:
        raise ValueError(
            f"Memory_Image_Vertex_List byte_count={data_size} is not divisible by "
            f"vertex_count={vertex_count}")
    return field_c, payload[12:], data_size // vertex_count


def _parse_memory_vertex_description(payload: bytes) -> tuple[int, int, list[dict[str, int]]]:
    """Parse and boundary-check one 0x10014 declaration table exactly.

    Each 17-byte entry carries a semantic hash, byte offset, stream stride and
    component count.  The decoder uses it only where a previously unknown
    auxiliary layout needs an explicit UV offset; it never guesses from the
    number of float-looking bytes.
    """
    if len(payload) < 16:
        raise ValueError("Memory_Image_Vertex_Description is shorter than its 16-byte header")
    _version, parameter, ref_buffer_size, description_size = struct.unpack_from("<IIII", payload, 0)
    if len(payload) != 16 + description_size:
        raise ValueError(
            f"Memory_Image_Vertex_Description description_size={description_size}, "
            f"actual={len(payload) - 16}")
    if description_size % 17:
        raise ValueError(
            f"Memory_Image_Vertex_Description description_size={description_size} is not a whole number of 17-byte records")
    attributes: list[dict[str, int]] = []
    for offset in range(16, len(payload), 17):
        semantic, _zero, byte_offset, stride, element_type, components, unknown = struct.unpack_from(
            "<IIIBBBH", payload, offset)
        attributes.append({
            "semantic": semantic,
            "offset": byte_offset,
            "stride": stride,
            "element_type": element_type,
            "components": components,
            "unknown": unknown,
        })
    return parameter, ref_buffer_size, attributes


def _extract_character_uv_stream(
    stream_body: bytes,
    vertex_count: int,
    stride: int,
    declarations: list[dict[str, int]] | None = None,
) -> bytes:
    """Extract verified float32 UV0 values from a character auxiliary stream.

    Alex establishes direct 8-byte UV and 12-byte packed-colour-plus-UV
    layouts. Hydra and both Supreme Hunter P3Ds additionally establish 16/20
    byte streams, but their UV0 offset is accepted only when their paired
    0x10014 table names it with ``UV0_VERTEX_SEMANTIC`` and exactly agrees on
    stride, components and byte bounds. No coordinate flip or axis exchange is
    applied.
    """
    if len(stream_body) != vertex_count * stride:
        raise ValueError("auxiliary vertex stream length does not match vertex count and stride")
    if stride == 8:
        return stream_body
    if stride == 12:
        # The 12B Alex declaration is [packed colour @ 0, UV0 @ 4].
        uv_offset = 4
    else:
        if declarations is None:
            raise ValueError(f"unverified auxiliary character vertex stride {stride}")
        matches = [attribute for attribute in declarations
                   if attribute["semantic"] == UV0_VERTEX_SEMANTIC
                   and attribute["stride"] == stride
                   and attribute["components"] == 2
                   and attribute["offset"] + 8 <= stride]
        if len(matches) != 1:
            raise ValueError(
                f"auxiliary stride {stride} needs exactly one declared UV0 semantic "
                f"0x{UV0_VERTEX_SEMANTIC:08X}; found {len(matches)}")
        uv_offset = matches[0]["offset"]
    uv = bytearray(vertex_count * 8)
    for index in range(vertex_count):
        src = index * stride + uv_offset
        uv[index * 8:index * 8 + 8] = stream_body[src:src + 8]
    return bytes(uv)


def _decode_local_texture(records: list[dict[str, Any]], children_map: dict[int, list[int]], index: int) -> dict[str, Any]:
    """Decode one local Texture through verified TextureDDS + count-prefixed DDS framing."""
    name, _ = _p3d_string(records[index]["payload"], 0)
    descendants = _descendant_indices(children_map, index)
    dds_headers = [records[i]["payload"] for i in descendants if records[i]["type_id"] == TEXTURE_DDS]
    image_payloads = [records[i]["payload"] for i in descendants if records[i]["type_id"] == IMAGE_DATA]
    if len(dds_headers) != 1 or len(image_payloads) != 1:
        raise ValueError(f"Texture {name!r} does not have exactly one TextureDDS and Image_Data descendant")
    dds_name, width, height, mip_count, algorithm = _parse_texture_dds_header(dds_headers[0])
    if dds_name != name:
        raise ValueError(f"TextureDDS name {dds_name!r} does not match Texture {name!r}")
    # compressed_texture verifies all of: count prefix, DDS magic/header,
    # TextureDDS/DDS agreement, each mip length, and absence of trailing bytes.
    decoded = compressed_texture(image_payloads[0], (width, height, mip_count, algorithm), max_edge=8192)
    return {
        "key": f"entity_tex_{name}",
        "name": name,
        "format": decoded["format"],
        "fourcc": decoded["format"],
        "width": width,
        "height": height,
        "mipmaps": decoded["mips"],
        "is_transparent": decoded["format"] in ("DXT3", "DXT5"),
    }


def _parse_skin_header(payload: bytes) -> dict[str, Any]:
    """Parse the exact ``Skin`` header: name, version, skeleton name, count."""
    name, offset = _p3d_string(payload, 0)
    if offset + 4 > len(payload):
        raise ValueError(f"Skin {name!r} is missing its version")
    version = struct.unpack_from("<I", payload, offset)[0]
    skeleton_name, offset = _p3d_string(payload, offset + 4)
    if offset + 4 != len(payload):
        raise ValueError(
            f"Skin {name!r} header has {len(payload) - offset} trailing bytes; expected its uint32 PrimitiveGroup count")
    primitive_group_count = struct.unpack_from("<I", payload, offset)[0]
    return {
        "name": name,
        "version": version,
        "skeleton_name": skeleton_name,
        "primitive_group_count": primitive_group_count,
    }


def _parse_composite_drawable_2_header(payload: bytes) -> dict[str, Any]:
    """Parse exact Composite_Drawable_2 (0x00123000) header bytes."""
    if len(payload) < 4:
        raise ValueError("Composite_Drawable_2 is shorter than its version")
    version = struct.unpack_from("<I", payload, 0)[0]
    name, offset = _p3d_string(payload, 4)
    skeleton_name, offset = _p3d_string(payload, offset)
    if offset + 4 != len(payload):
        raise ValueError(
            f"Composite_Drawable_2 {name!r} has {len(payload) - offset} trailing bytes; "
            "expected its uint32 primitive count")
    primitive_count = struct.unpack_from("<I", payload, offset)[0]
    return {
        "name": name,
        "version": version,
        "skeleton_name": skeleton_name,
        "primitive_count": primitive_count,
    }


def _parse_composite_drawable_primitive(payload: bytes) -> dict[str, Any]:
    """Parse exact Composite_Drawable_Primitive (0x00123001) link bytes."""
    if len(payload) < 8:
        raise ValueError("Composite_Drawable_Primitive is shorter than version + create_instance")
    version, create_instance = struct.unpack_from("<II", payload, 0)
    skin_name, offset = _p3d_string(payload, 8)
    if offset + 8 != len(payload):
        raise ValueError(
            f"Composite_Drawable_Primitive {skin_name!r} has {len(payload) - offset} trailing bytes; "
            "expected type + skeleton_joint_id")
    primitive_type, skeleton_joint_id = struct.unpack_from("<II", payload, offset)
    return {
        "skin_name": skin_name,
        "version": version,
        "create_instance": create_instance,
        "primitive_type": primitive_type,
        "skeleton_joint_id": skeleton_joint_id,
    }


def extract_entity_skeletons(data: bytes, *, source_entry: str | None = None) -> list[dict[str, Any]]:
    """Return only exact Skeleton_2 declarations from one Pure3D payload.

    This helper makes an explicit cross-entry assembly possible without ever
    treating an arbitrary first skeleton as a substitute.  It reads no mesh,
    texture, or animation data and carries the supplying entry as provenance.
    """
    if len(data) < 12:
        return []
    records: list[dict[str, Any]] = []
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic == SIG_LE:
        total_size = struct.unpack_from("<I", data, 8)[0]
        _walk(data, 12, min(total_size, len(data)), None, 0, records)
    else:
        _walk(data, 0, len(data), None, 0, records)
    children_map: dict[int, list[int]] = {}
    for i, record in enumerate(records):
        if record["parent"] is not None:
            children_map.setdefault(record["parent"], []).append(i)
    return _extract_skeletons_from_records(records, children_map, source_entry=source_entry)


def _extract_skeletons_from_records(
    records: list[dict[str, Any]],
    children_map: dict[int, list[int]],
    *,
    source_entry: str | None,
) -> list[dict[str, Any]]:
    skeletons: list[dict[str, Any]] = []
    for idx, record in enumerate(records):
        if record["type_id"] != SKELETON_2:
            continue
        p = record["payload"]
        try:
            skel_name, offset = _p3d_string(p, 0)
            if offset + 16 != len(p):
                raise ValueError("Skeleton_2 header length does not end after version/count fields")
            version, declared_joints, declared_partitions, num_limbs = struct.unpack_from("<IIII", p, offset)
            joints: list[dict[str, Any]] = []
            for ch_idx in children_map.get(idx, []):
                child = records[ch_idx]
                if child["type_id"] != SKELETON_JOINT_2:
                    continue
                jp = child["payload"]
                jname, joffset = _p3d_string(jp, 0)
                if joffset + 68 > len(jp):
                    raise ValueError(f"Skeleton joint {jname!r} is shorter than parent + 4x4 rest matrix")
                parent = struct.unpack_from("<I", jp, joffset)[0]
                matrix = struct.unpack_from("<16f", jp, joffset + 4)
                joints.append({
                    "name": jname,
                    "parent": parent,
                    # Preserve every source binary32 value. JSON serialisation
                    # is exact enough for IEEE-754 round-trip; display rounding
                    # must never enter the skin bind computation.
                    "matrix": list(matrix),
                })
            if len(joints) != declared_joints:
                raise ValueError(
                    f"Skeleton_2 {skel_name!r} declares {declared_joints} joints but has {len(joints)} direct Skeleton_Joint_2 children")
            skeletons.append({
                "name": skel_name,
                "joint_count": len(joints),
                "version": version,
                "partition_count": declared_partitions,
                "limb_count": num_limbs,
                "source_entry": source_entry,
                "joints": joints,
            })
        except (ValueError, struct.error):
            # The top-level decoder remains compatible with non-character
            # packages: malformed/unreadable Skeleton_2 is not silently used.
            continue
    return skeletons


def _channel_times(payload: bytes, frame_offset: int, count: int, fps: float) -> list[float]:
    if count < 0 or frame_offset < 0 or frame_offset + count * 2 > len(payload):
        raise ValueError("animation channel frame array is out of bounds")
    frames = struct.unpack_from(f"<{count}H", payload, frame_offset)
    if any(later < earlier for earlier, later in zip(frames, frames[1:])):
        raise ValueError("animation channel frame indices are not nondecreasing")
    return [frame / fps for frame in frames]


def _decode_inline_animation_channel(channel_type: int, payload: bytes, fps: float) -> tuple[str, dict[str, Any]] | None:
    """Decode a proven inline animation channel without curve modification.

    The returned ``rot`` / ``pos`` values are direct source samples.  In
    particular, root translations remain translations; no root lock, first
    frame subtraction, unit scaling, axis swap, or key reduction occurs here.
    """
    if fps <= 0:
        raise ValueError("animation frame rate must be positive")

    if channel_type in (ANIM_VEC1_DOF, ANIM_VEC2_DOF, ANIM_VEC2_DOF_FLOAT16):
        # These vector records also encode scale/shear variants. Their FourCC
        # selects a Three transform property rather than letting a scale record
        # masquerade as root motion.
        if len(payload) < 8:
            return None
        property_name = {b"TRAN": "pos", b"SCL\0": "scale"}.get(payload[4:8])
        if property_name is None:
            return None
        # Exact grammar shared by standard Vector_1D/2D_OF channels:
        # version, FourCC, fixed coordinate (u16), constants (3xf32), count.
        if len(payload) < 26:
            raise ValueError("2/1-DOF translation channel is shorter than its 26-byte header")
        _version, _parameter, fixed_coordinate = struct.unpack_from("<I4sH", payload, 0)
        constants = struct.unpack_from("<3f", payload, 10)
        count = struct.unpack_from("<I", payload, 22)[0]
        components = 1 if channel_type == ANIM_VEC1_DOF else 2
        component_size = 4 if channel_type in (ANIM_VEC1_DOF, ANIM_VEC2_DOF) else 2
        expected = 26 + count * 2 + count * components * component_size
        if len(payload) != expected:
            raise ValueError(
                f"translation channel 0x{channel_type:08X} length {len(payload)} != exact expected {expected}")
        if fixed_coordinate not in (0, 1, 2):
            raise ValueError(f"translation channel fixed coordinate {fixed_coordinate} is invalid")
        times = _channel_times(payload, 26, count, fps)
        value_offset = 26 + count * 2
        if channel_type == ANIM_VEC1_DOF:
            raw_values = [(value,) for value in struct.unpack_from(f"<{count}f", payload, value_offset)]
        elif channel_type == ANIM_VEC2_DOF:
            flat_values = struct.unpack_from(f"<{count * 2}f", payload, value_offset)
            raw_values = list(zip(flat_values[0::2], flat_values[1::2]))
        else:
            # The compact values are IEEE-754 binary16, not normalized int16:
            # e.g. raw ``00 3c`` is exactly 1.0 and recurs in the matching
            # source constants of the same 0x121118 records.
            flat_values = struct.unpack_from(f"<{count * 2}e", payload, value_offset)
            raw_values = list(zip(flat_values[0::2], flat_values[1::2]))

        values: list[list[float]] = []
        for raw_value in raw_values:
            if components == 1:
                xyz = list(constants)
                xyz[fixed_coordinate] = raw_value[0]
            else:
                dynamic_coordinates = [axis for axis in range(3) if axis != fixed_coordinate]
                xyz = list(constants)
                xyz[dynamic_coordinates[0]] = raw_value[0]
                xyz[dynamic_coordinates[1]] = raw_value[1]
            values.append(xyz)
        return property_name, {"times": times, "values": values}

    if channel_type == ANIM_VEC3_DOF:
        if len(payload) < 8 or payload[4:8] != b"TRAN":
            return None
        if len(payload) < 12:
            raise ValueError("Vector_3D_OF channel is shorter than its 12-byte header")
        _version, _parameter, count = struct.unpack_from("<I4sI", payload, 0)
        expected = 12 + count * 2 + count * 12
        if len(payload) != expected:
            raise ValueError(f"Vector_3D_OF length {len(payload)} != exact expected {expected}")
        times = _channel_times(payload, 12, count, fps)
        flat_values = struct.unpack_from(f"<{count * 3}f", payload, 12 + count * 2)
        return "pos", {"times": times, "values": [list(flat_values[i:i + 3]) for i in range(0, len(flat_values), 3)]}

    if channel_type == ANIM_VEC3_FLOAT16:
        if len(payload) < 8:
            return None
        property_name = {b"TRAN": "pos", b"SCL\0": "scale"}.get(payload[4:8])
        if property_name is None:
            return None
        # Prototype compact Vector3 grammar: version/FourCC/count, uint16
        # frame list, then IEEE-754 binary16 XYZ values. It is exact for every
        # selected-family payload audited on 2026-09-17.
        if len(payload) < 12:
            raise ValueError("compact Vector3 channel is shorter than its 12-byte header")
        _version, _parameter, count = struct.unpack_from("<I4sI", payload, 0)
        expected = 12 + count * 2 + count * 6
        if len(payload) != expected:
            raise ValueError(f"compact Vector3 length {len(payload)} != exact expected {expected}")
        times = _channel_times(payload, 12, count, fps)
        flat_values = struct.unpack_from(f"<{count * 3}e", payload, 12 + count * 2)
        return property_name, {"times": times, "values": [list(flat_values[i:i + 3])
                                                              for i in range(0, len(flat_values), 3)]}

    if channel_type in (ANIM_QUAT_FLOAT, ANIM_ROT_INT16, ANIM_ROT_INT8):
        if len(payload) < 8 or payload[4:8] != b"ROT\0":
            return None
        if len(payload) < 12:
            raise ValueError("rotation channel is shorter than its 12-byte header")
        _version, _parameter, count = struct.unpack_from("<I4sI", payload, 0)
        if channel_type == ANIM_QUAT_FLOAT:
            expected = 12 + count * 2 + count * 16
        elif channel_type == ANIM_ROT_INT16:
            expected = 12 + count * 2 + count * 6
        else:
            # Prototype compact quaternion: exact uint16 frames + int8 XYZ.
            expected = 12 + count * 2 + count * 3
        if len(payload) != expected:
            raise ValueError(f"rotation channel 0x{channel_type:08X} length {len(payload)} != exact expected {expected}")
        times = _channel_times(payload, 12, count, fps)
        value_offset = 12 + count * 2
        if channel_type == ANIM_QUAT_FLOAT:
            flat_values = struct.unpack_from(f"<{count * 4}f", payload, value_offset)
            values = [list(flat_values[i:i + 4]) for i in range(0, len(flat_values), 4)]
        else:
            item_format, divisor = ("h", 32767.0) if channel_type == ANIM_ROT_INT16 else ("b", 127.0)
            flat_values = struct.unpack_from(f"<{count * 3}{item_format}", payload, value_offset)
            values = []
            for i in range(0, len(flat_values), 3):
                x, y, z = (flat_values[i] / divisor, flat_values[i + 1] / divisor, flat_values[i + 2] / divisor)
                w2 = 1.0 - x * x - y * y - z * z
                if w2 < 0.0:
                    raise ValueError(f"compressed quaternion has negative reconstructed w²={w2!r}")
                values.append([x, y, z, math.sqrt(w2)])
        return "rot", {"times": times, "values": values}

    return None

def _decode_external_animation_channel(
    channel_type: int,
    payload: bytes,
    locator_payload: bytes,
    zlib_blob: bytes,
    fps: float,
) -> tuple[str, dict[str, Any]] | None:
    """Decode one locator-backed channel from an Animation ZLIB blob.

    The outer Animation can contain both locator-backed and inline channels.
    This function is intentionally called only for a channel with exactly one
    direct locator child; siblings without one continue through the inline
    grammar.  The grammar below is independently asserted against every
    locator channel in the catalogued character families:

    * locator = ``u32 zero, u32 key_count, u32 blob_offset``;
    * the blob has ``u16[key_count]`` frames at ``blob_offset``, padded to 4;
    * vector values follow the aligned frames as the same component layout as
      the inline form; external channel headers have their inline count set to
      zero and contain no inline frames/values.

    No trailing-blob assertion is valid here: one decompressed Animation blob
    holds several independently located channel segments.  Bounds, header
    size, FourCC, zero inline count, and exact value width are all asserted.
    """
    if fps <= 0:
        raise ValueError("external animation frame rate must be positive")
    if len(locator_payload) != 12:
        raise ValueError("external animation locator must be exactly 12 bytes")
    locator_unknown, key_count, blob_offset = struct.unpack_from("<III", locator_payload, 0)
    if locator_unknown != 0:
        raise ValueError(f"external animation locator reserved field is {locator_unknown}, expected 0")
    frame_end = blob_offset + key_count * 2
    value_offset = _align4(frame_end)
    if blob_offset > len(zlib_blob) or frame_end > len(zlib_blob):
        raise ValueError("external animation frame array is out of ZLIB bounds")
    frames = struct.unpack_from(f"<{key_count}H", zlib_blob, blob_offset)
    if any(later < earlier for earlier, later in zip(frames, frames[1:])):
        raise ValueError("external animation frames are not nondecreasing")
    times = [frame / fps for frame in frames]

    def require_header(expected_len: int, fourccs: dict[bytes, str], count_offset: int) -> str | None:
        if len(payload) != expected_len:
            raise ValueError(
                f"external channel 0x{channel_type:08X} header length {len(payload)} != {expected_len}")
        property_name = fourccs.get(payload[4:8])
        if property_name is None:
            return None
        inline_count = struct.unpack_from("<I", payload, count_offset)[0]
        if inline_count != 0:
            raise ValueError(
                f"external channel 0x{channel_type:08X} has inline count {inline_count}, expected 0")
        return property_name

    if channel_type in (ANIM_VEC1_DOF, ANIM_VEC2_DOF, ANIM_VEC2_DOF_FLOAT16):
        property_name = require_header(26, {b"TRAN": "pos", b"SCL\0": "scale"}, 22)
        if property_name is None:
            return None
        _version, _parameter, fixed_coordinate = struct.unpack_from("<I4sH", payload, 0)
        constants = struct.unpack_from("<3f", payload, 10)
        if fixed_coordinate not in (0, 1, 2):
            raise ValueError(f"external translation fixed coordinate {fixed_coordinate} is invalid")
        components = 1 if channel_type == ANIM_VEC1_DOF else 2
        item_format = "f" if channel_type in (ANIM_VEC1_DOF, ANIM_VEC2_DOF) else "e"
        value_end = value_offset + key_count * components * struct.calcsize("<" + item_format)
        if value_end > len(zlib_blob):
            raise ValueError("external vector value array is out of ZLIB bounds")
        values_flat = struct.unpack_from(f"<{key_count * components}{item_format}", zlib_blob, value_offset)
        values: list[list[float]] = []
        for index in range(key_count):
            xyz = list(constants)
            if components == 1:
                xyz[fixed_coordinate] = values_flat[index]
            else:
                dynamic_coordinates = [axis for axis in range(3) if axis != fixed_coordinate]
                xyz[dynamic_coordinates[0]] = values_flat[index * 2]
                xyz[dynamic_coordinates[1]] = values_flat[index * 2 + 1]
            values.append(xyz)
        return property_name, {"times": times, "values": values}

    if channel_type in (ANIM_VEC3_DOF, ANIM_VEC3_FLOAT16):
        property_name = require_header(12, {b"TRAN": "pos", b"SCL\0": "scale"}, 8)
        if property_name is None:
            return None
        item_format = "f" if channel_type == ANIM_VEC3_DOF else "e"
        value_end = value_offset + key_count * 3 * struct.calcsize("<" + item_format)
        if value_end > len(zlib_blob):
            raise ValueError("external Vector3 value array is out of ZLIB bounds")
        flat_values = struct.unpack_from(f"<{key_count * 3}{item_format}", zlib_blob, value_offset)
        return property_name, {
            "times": times,
            "values": [list(flat_values[i:i + 3]) for i in range(0, len(flat_values), 3)],
        }

    if channel_type in (ANIM_ROT_INT16, ANIM_ROT_INT8):
        property_name = require_header(12, {b"ROT\0": "rot"}, 8)
        if property_name is None:
            return None
        item_format, divisor = ("h", 32767.0) if channel_type == ANIM_ROT_INT16 else ("b", 127.0)
        value_end = value_offset + key_count * 3 * struct.calcsize("<" + item_format)
        if value_end > len(zlib_blob):
            raise ValueError("external compressed quaternion array is out of ZLIB bounds")
        flat_values = struct.unpack_from(f"<{key_count * 3}{item_format}", zlib_blob, value_offset)
        values = []
        for value_index in range(0, len(flat_values), 3):
            x, y, z = (flat_values[value_index] / divisor,
                       flat_values[value_index + 1] / divisor,
                       flat_values[value_index + 2] / divisor)
            w2 = 1.0 - x * x - y * y - z * z
            if w2 < 0.0:
                raise ValueError(f"external compressed quaternion has negative reconstructed w²={w2!r}")
            values.append([x, y, z, math.sqrt(w2)])
        return "rot", {"times": times, "values": values}

    return None


# NewShader string parameters that carry a base-colour / normal / specular
# map under a template-specific name.  A survey of every NewShader in all 662
# art.rcf packages produced the parameter vocabulary below; only names that are
# unambiguously the stated channel are mapped.  Military vehicles in particular
# name their diffuse "camo" (template env_vehicle_military / _militarybw) and
# the whipfist names it "diffuseTexture" (char_alex_whipfist) — both were
# previously discarded, which is why those meshes rendered flat-coloured.
_COLOR_PARAMETERS = (
    "color",            # the overwhelmingly common case
    "camo",             # env_vehicle_military, env_vehicle_militarybw, env_vehicle_rivets
    "diffuseTexture",   # char_alex_whipfist
    "add_color",        # fx_add_soft
    "bottom",           # env_road
)
_NORMAL_PARAMETERS = (
    "normal",
    "normalmap",        # env_vehicle_husk
    "rivetsNm",         # env_vehicle_rivets
)
_SPECULAR_PARAMETERS = (
    "specular",
    "specularMap",      # char_NIS, char_NIS_alpha, char_NIS_meshMapperTNM, char_SupremeHunter
)
_MATERIAL_CHANNEL_ALIASES = {}
for _channel, _names in (
    ("color", _COLOR_PARAMETERS),
    ("normal", _NORMAL_PARAMETERS),
    ("specular", _SPECULAR_PARAMETERS),
):
    for _name in _names:
        _MATERIAL_CHANNEL_ALIASES[_name.lower()] = _channel
del _channel, _names, _name


def _material_channel_for_parameter(parameter: str) -> str | None:
    """Map a NewShader string parameter name onto a glTF material channel.

    Returns None for parameters that are not a base-colour/normal/specular map
    (decals, cube maps, damage overlays, palette strips and similar), so they
    are ignored rather than guessed at.
    """
    return _MATERIAL_CHANNEL_ALIASES.get(parameter.lower())


def decode_entity_meshes(
    data: bytes,
    shape_filter: str | None = None,
    *,
    external_skeletons: list[dict[str, Any]] | None = None,
    external_textures: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Decode one package, binding every Skin only to its declared skeleton name.

    ``external_skeletons`` is an explicitly audited donor list supplied by the
    server for package assemblies.  It may fill a name absent from this P3D,
    but it can never replace an identically named local declaration.

    ``external_textures`` maps a texture *name* to an already-decoded texture
    record drawn from an audited shared package.  A shader in this P3D may name
    a texture that the package does not carry (vehicle tyres, tank treads,
    driver skins and the whipfist spike all live in shared packages); such a
    name is resolved here instead of being silently dropped, which is what left
    those meshes untextured.  A donor never overrides a local texture of the
    same name, and only names a local shader actually asks for are pulled in.
    """
    if len(data) < 12:
        return {"meshes": [], "textures": [], "skeletons": [], "animations": [], "assemblies": [], "diagnostics": []}

    records: list[dict[str, Any]] = []
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic == SIG_LE:
        total_size = struct.unpack_from("<I", data, 8)[0]
        _walk(data, 12, min(total_size, len(data)), None, 0, records)
    else:
        _walk(data, 0, len(data), None, 0, records)

    children_map: dict[int, list[int]] = {}
    for i, r in enumerate(records):
        p = r["parent"]
        if p is not None:
            children_map.setdefault(p, []).append(i)

    # 1. Extract local Texture → TextureDDS → Image_Data and
    # PrimitiveGroup shader name → NewShader → color parameter links.  These
    # chains intentionally reject malformed or ambiguous records instead of
    # guessing a nearby texture parameter.
    textures: dict[str, dict[str, Any]] = {}
    diagnostics: list[str] = []
    for idx, record in enumerate(records):
        if record["type_id"] != TEXTURE:
            continue
        try:
            texture = _decode_local_texture(records, children_map, idx)
            if texture["name"] in textures:
                raise ValueError(f"duplicate Texture name {texture['name']!r}")
            textures[texture["name"]] = texture
        except (ValueError, struct.error) as exc:
            diagnostics.append(f"Texture#{idx}: {exc}")

    shader_to_textures: dict[str, dict[str, str]] = {}
    for idx, record in enumerate(records):
        if record["type_id"] != NEW_SHADER:
            continue
        try:
            shader_header = _parse_new_shader_header(record["payload"])
            channel_values: dict[str, list[str]] = {}
            for ch_idx in children_map.get(idx, []):
                child = records[ch_idx]
                if child["type_id"] != NEW_SHADER_STRING_PARAMETER:
                    continue
                parameter, value = _parse_exact_string_pair(child["payload"], "NewShader string parameter")
                channel = _material_channel_for_parameter(parameter)
                if channel and value:
                    channel_values.setdefault(channel, []).append(value)
            resolved: dict[str, str] = {}
            for parameter, values in channel_values.items():
                if len(values) == 1:
                    resolved[parameter] = values[0]
                elif len(values) > 1:
                    diagnostics.append(
                        f"NewShader#{idx} {shader_header['shader_name']!r}: ambiguous {parameter} parameters")
            if resolved:
                shader_to_textures[shader_header["shader_name"]] = resolved
        except (ValueError, struct.error) as exc:
            diagnostics.append(f"NewShader#{idx}: {exc}")

    # 1b. Shared-package textures.  Every texture name this package's shaders
    # reference but do not carry is looked up in the audited donor index.  A
    # local declaration always wins, so a donor can only fill a real gap.
    wanted_texture_names = {
        name for channels in shader_to_textures.values() for name in channels.values()
    }
    resolved_donor_textures: dict[str, dict[str, Any]] = {}
    unresolved_texture_names: set[str] = set()
    if external_textures:
        for name in sorted(wanted_texture_names - set(textures)):
            donor = external_textures.get(name)
            if donor is not None:
                resolved_donor_textures[name] = donor

    # 2. Extract exact local Skeleton_2 declarations, then add only audited
    # external donors whose names are absent locally.  The name is the P3D
    # binding key; list position and joint count are not valid substitutes.
    # The Skin header is the only mesh-to-skeleton binding authority.  Gather
    # its names before building any rigs, so a package such as Soldier (which
    # also carries unrelated weapon/projectile skeletons) does not create an
    # accidental "first" or all-entry character rig.
    required_skeleton_names: set[str] = set()
    for record in records:
        if record["type_id"] != POLYSKIN:
            continue
        try:
            required_skeleton_names.add(_parse_skin_header(record["payload"])["skeleton_name"])
        except (ValueError, struct.error):
            continue

    local_skeletons = _extract_skeletons_from_records(records, children_map, source_entry=None)
    skeletons = [skeleton for skeleton in local_skeletons
                 if skeleton["name"] in required_skeleton_names]
    skeleton_by_name = {skeleton["name"]: skeleton for skeleton in skeletons}
    # Do not materialise every skeleton in a donor package either.  The donor
    # is a source of exact declarations only; this package's Skin headers
    # decide which names are required for this assembly.
    for supplied in external_skeletons or []:
        if not isinstance(supplied, dict):
            continue
        supplied_name = supplied.get("name")
        supplied_joints = supplied.get("joints")
        if (not isinstance(supplied_name, str) or not supplied_name
                or not isinstance(supplied_joints, list)
                or supplied_name not in required_skeleton_names):
            continue
        if supplied_name not in skeleton_by_name:
            skeletons.append(supplied)
            skeleton_by_name[supplied_name] = supplied

    # 3. Decode inline Pure3D clips and the previously supported external-ZLIB
    # clips. The channel parser only accepts an exact per-type byte length;
    # unsupported channel kinds do not get recast as translations or rotations.
    animations = []
    for idx, record in enumerate(records):
        if record["type_id"] != ANIMATION:
            continue
        try:
            p = record["payload"]
            if len(p) < 4:
                raise ValueError("Animation is shorter than its version field")
            _version = struct.unpack_from("<I", p, 0)[0]
            anim_name, off = _p3d_string(p, 4)
            if off + 16 != len(p):
                raise ValueError("Animation header does not end after type/frame-rate/cyclic fields")
            fourcc = p[off:off + 4].decode("ascii", errors="strict")
            num_frames, frame_rate, cyclic = struct.unpack_from("<ffI", p, off + 4)
            if frame_rate <= 0:
                raise ValueError(f"Animation {anim_name!r} has non-positive frame rate {frame_rate!r}")
            fps = frame_rate

            zlib_blob = None
            group_list_idx = None
            for ch_idx in children_map.get(idx, []):
                ch = records[ch_idx]
                if ch["type_id"] == ANIM_ZLIB_BLOB:
                    z_payload = ch["payload"]
                    if len(z_payload) < 16:
                        raise ValueError("Animation ZLIB blob is shorter than its 16-byte header")
                    uncomp_size, comp_size = struct.unpack_from("<II", z_payload, 8)
                    if len(z_payload) != 16 + comp_size:
                        raise ValueError("Animation ZLIB compressed length does not match its header")
                    zlib_blob = zlib.decompress(z_payload[16:])
                    if len(zlib_blob) != uncomp_size:
                        raise ValueError("Animation ZLIB decompressed length does not match its header")
                elif ch["type_id"] == ANIM_GROUP_LIST:
                    if group_list_idx is not None:
                        raise ValueError("Animation has ambiguous duplicate Animation_Group_List children")
                    group_list_idx = ch_idx

            if group_list_idx is None:
                continue
            groups = []
            for g_idx in children_map.get(group_list_idx, []):
                g_rec = records[g_idx]
                if g_rec["type_id"] != ANIM_GROUP:
                    continue
                gp = g_rec["payload"]
                gname, goff = _p3d_string(gp, 4)
                if goff + 8 != len(gp):
                    raise ValueError(f"Animation group {gname!r} has trailing or truncated header data")
                _gid, declared_channel_count = struct.unpack_from("<II", gp, goff)
                direct_channel_indices = children_map.get(g_idx, [])
                if len(direct_channel_indices) != declared_channel_count:
                    raise ValueError(
                        f"Animation group {gname!r} declares {declared_channel_count} channels but has "
                        f"{len(direct_channel_indices)} direct children")

                rot_track = None
                pos_track = None
                scale_track = None
                for c_idx in direct_channel_indices:
                    c_ch = records[c_idx]
                    cid = c_ch["type_id"]
                    # A single Animation may carry its dense rotation curves
                    # in an external ZLIB blob *and* retain sparse vector
                    # channels inline.  Locator presence, not merely blob
                    # presence on the parent, decides the grammar for this
                    # individual channel.
                    locator_payloads = [records[l_idx]["payload"]
                                        for l_idx in children_map.get(c_idx, [])
                                        if records[l_idx]["type_id"] == ANIM_LOCATOR]
                    if locator_payloads:
                        if zlib_blob is None:
                            raise ValueError(f"external animation channel 0x{cid:08X} has locator but no Animation ZLIB blob")
                        if len(locator_payloads) != 1:
                            raise ValueError(f"external animation channel 0x{cid:08X} has ambiguous locators")
                        decoded = _decode_external_animation_channel(
                            cid, c_ch["payload"], locator_payloads[0], zlib_blob, fps)
                    else:
                        decoded = _decode_inline_animation_channel(cid, c_ch["payload"], fps)

                    if decoded is None:
                        continue
                    kind, track = decoded
                    if kind == "rot":
                        if rot_track is not None:
                            raise ValueError(f"Animation group {gname!r} has multiple rotation channels")
                        rot_track = track
                    elif kind == "pos":
                        if pos_track is not None:
                            raise ValueError(f"Animation group {gname!r} has multiple translation channels")
                        pos_track = track
                    elif kind == "scale":
                        if scale_track is not None:
                            raise ValueError(f"Animation group {gname!r} has multiple scale channels")
                        scale_track = track

                if rot_track is not None or pos_track is not None or scale_track is not None:
                    group: dict[str, Any] = {"name": gname}
                    if rot_track is not None:
                        group["rot"] = rot_track
                    if pos_track is not None:
                        group["pos"] = pos_track
                    if scale_track is not None:
                        group["scale"] = scale_track
                    groups.append(group)

            # Retain every structurally valid 0x121000 record in the API.
            # A source clip made only of metadata/event channels (for example
            # Float_1 ``STE``) has no proven Three bone-transform equivalent,
            # but silently omitting its name recreates the former Super
            # Soldier-style catalogue blind spot. ``playable`` explicitly
            # distinguishes it from a skeletal clip instead of fabricating a
            # pose.
            animations.append({
                "name": anim_name,
                "type": fourcc,
                "frames": int(num_frames),
                "fps": fps,
                "duration": num_frames / fps,
                "cyclic": bool(cyclic),
                "groups": groups,
                "playable": bool(groups),
            })
        except (ValueError, struct.error, UnicodeDecodeError, zlib.error) as exc:
            diagnostics.append(f"Animation#{idx}: {exc}")

    # 4. Trace each PrimitiveGroup through its owning Skin (rather than
    # attaching it to skeletons[0]).  A generic Geometry has no Skin binding
    # and intentionally remains unskinned.
    skin_groups: list[tuple[str, str | None, str | None, int, dict[str, Any]]] = []
    skin_headers: dict[str, dict[str, Any]] = {}
    for idx, record in enumerate(records):
        if record["type_id"] == POLYSKIN:
            try:
                skin = _parse_skin_header(record["payload"])
                if skin["name"] in skin_headers:
                    raise ValueError(f"duplicate Skin name {skin['name']!r}")
                child_groups = [ch_idx for ch_idx in children_map.get(idx, [])
                                if records[ch_idx]["type_id"] == PRIMITIVE_GROUP]
                if len(child_groups) != skin["primitive_group_count"]:
                    raise ValueError(
                        f"Skin {skin['name']!r} declares {skin['primitive_group_count']} PrimitiveGroups "
                        f"but has {len(child_groups)} direct children")
                skin_headers[skin["name"]] = skin
                if skin["skeleton_name"] not in skeleton_by_name:
                    diagnostics.append(
                        f"Skin {skin['name']!r}: declared skeleton {skin['skeleton_name']!r} is unavailable; "
                        "its mesh will not be rebound to a different skeleton")
                for ch_idx in child_groups:
                    skin_groups.append((skin["name"], skin["name"], skin["skeleton_name"], ch_idx, records[ch_idx]))
            except (ValueError, struct.error) as exc:
                diagnostics.append(f"Skin#{idx}: {exc}")
        elif record["type_id"] in (GEOMETRY, 0x00012000):
            try:
                geom_name, _ = _p3d_string(record["payload"])
                for ch_idx in children_map.get(idx, []):
                    if records[ch_idx]["type_id"] == PRIMITIVE_GROUP:
                        skin_groups.append((geom_name, None, None, ch_idx, records[ch_idx]))
            except (ValueError, struct.error) as exc:
                diagnostics.append(f"Geometry#{idx}: {exc}")

    # Composite_Drawable_2 is a second, independently validated binding edge:
    # its direct primitive children must name the Skin and repeat its skeleton.
    assemblies: list[dict[str, Any]] = []
    for idx, record in enumerate(records):
        if record["type_id"] != 0x00123000:
            continue
        try:
            composite = _parse_composite_drawable_2_header(record["payload"])
            links = []
            for ch_idx in children_map.get(idx, []):
                if records[ch_idx]["type_id"] == 0x00123001:
                    links.append(_parse_composite_drawable_primitive(records[ch_idx]["payload"]))
            if len(links) != composite["primitive_count"]:
                raise ValueError(
                    f"Composite_Drawable_2 {composite['name']!r} declares {composite['primitive_count']} primitives "
                    f"but has {len(links)} direct children")
            for link in links:
                skin = skin_headers.get(link["skin_name"])
                link["skin_skeleton_matches"] = skin is not None and skin["skeleton_name"] == composite["skeleton_name"]
                if skin is None:
                    diagnostics.append(
                        f"Composite_Drawable_2 {composite['name']!r}: referenced Skin {link['skin_name']!r} is absent")
                elif not link["skin_skeleton_matches"]:
                    diagnostics.append(
                        f"Composite_Drawable_2 {composite['name']!r}: Skin {link['skin_name']!r} binds "
                        f"{skin['skeleton_name']!r}, not {composite['skeleton_name']!r}")
            composite["primitives"] = links
            assemblies.append(composite)
        except (ValueError, struct.error) as exc:
            diagnostics.append(f"Composite_Drawable_2#{idx}: {exc}")

    decoded_meshes = []

    for geom_name, skin_name, skeleton_name, pg_idx, pg_rec in skin_groups:
        if shape_filter and shape_filter not in geom_name and (skin_name is None or shape_filter not in skin_name):
            continue

        try:
            pg = _parse_primitive_group(pg_rec["payload"])
        except Exception:
            continue

        shader_name = pg["shader_name"]
        vertex_count = pg["vertex_count"]
        index_count = pg["index_count"]
        memory_imaged = pg["memory_imaged"]

        if vertex_count <= 0:
            continue

        pg_children_indices = children_map.get(pg_idx, [])
        pg_children = [records[i] for i in pg_children_indices]

        # A Matrix_Palette is a counted uint32 array.  Never use its count as
        # a joint index and never fall back to a raw blend byte on a failed
        # palette lookup.
        palette: list[int] = []
        palette_chunks = [child["payload"] for child in pg_children if child["type_id"] == MATRIX_PALETTE]
        if palette_chunks:
            if len(palette_chunks) != 1:
                diagnostics.append(f"PrimitiveGroup {geom_name!r}: ambiguous Matrix_Palette chunks")
            else:
                try:
                    palette = _parse_counted_matrix_palette(palette_chunks[0])
                    if pg["matrix_count"] != len(palette):
                        diagnostics.append(
                            f"PrimitiveGroup {geom_name!r}: header matrix_count={pg['matrix_count']} "
                            f"does not equal Matrix_Palette count={len(palette)}")
                        palette = []
                except (ValueError, struct.error) as exc:
                    diagnostics.append(f"PrimitiveGroup {geom_name!r}: {exc}")

        pos_buf = bytearray(vertex_count * 12)
        uv_buf: bytes | bytearray = bytearray(vertex_count * 8)
        i_body = b""
        skin_indices_b64 = None
        skin_weights_b64 = None

        if memory_imaged == 1:
            stream_records: list[tuple[int, bytes, int]] = []
            declarations_by_field: dict[int, list[dict[str, int]]] = {}
            i_lists = [child["payload"] for child in pg_children if child["type_id"] == INDEX_LIST]
            for child in pg_children:
                if child["type_id"] == VERTEX_DESCRIPTION:
                    try:
                        field, ref_size, declarations = _parse_memory_vertex_description(child["payload"])
                        if ref_size != 0 and ref_size % vertex_count:
                            raise ValueError(
                                f"Memory_Image_Vertex_Description ref_buffer_size={ref_size} is not divisible by vertex_count={vertex_count}")
                        if field in declarations_by_field:
                            raise ValueError(f"ambiguous duplicate declaration for field_c={field}")
                        declarations_by_field[field] = declarations
                    except (ValueError, struct.error) as exc:
                        diagnostics.append(f"PrimitiveGroup {geom_name!r}: {exc}")
                elif child["type_id"] == VERTEX_LIST:
                    try:
                        stream_records.append(_parse_memory_vertex_stream(child["payload"], vertex_count))
                    except (ValueError, struct.error) as exc:
                        diagnostics.append(f"PrimitiveGroup {geom_name!r}: {exc}")
            if not stream_records or len(i_lists) != 1:
                diagnostics.append(f"PrimitiveGroup {geom_name!r}: missing/ambiguous vertex or index stream")
                continue

            # The verified character streams have field_c=2 for positions /
            # skin data and field_c=1 for UV data.  A single non-character
            # stream is retained as an unskinned compatibility path; it never
            # receives guessed UV or skin offsets.
            main_candidates = [stream for stream in stream_records if stream[0] == 2]
            aux_candidates = [stream for stream in stream_records if stream[0] == 1]
            if len(main_candidates) == 1:
                main_field, main_body, main_stride = main_candidates[0]
            elif len(main_candidates) == 0 and len(stream_records) == 1:
                main_field, main_body, main_stride = stream_records[0]
            else:
                diagnostics.append(f"PrimitiveGroup {geom_name!r}: missing/ambiguous field_c=2 main stream")
                continue
            if main_stride < 12:
                diagnostics.append(f"PrimitiveGroup {geom_name!r}: main vertex stride {main_stride} cannot contain POSITION")
                continue

            for i in range(vertex_count):
                src_idx = i * main_stride
                pos_buf[i * 12:i * 12 + 12] = main_body[src_idx:src_idx + 12]

            if aux_candidates:
                if len(aux_candidates) != 1:
                    diagnostics.append(f"PrimitiveGroup {geom_name!r}: ambiguous field_c=1 auxiliary stream")
                else:
                    _aux_field, aux_body, aux_stride = aux_candidates[0]
                    try:
                        uv_buf = _extract_character_uv_stream(
                            aux_body, vertex_count, aux_stride, declarations_by_field.get(_aux_field))
                    except ValueError as exc:
                        diagnostics.append(f"PrimitiveGroup {geom_name!r}: {exc}")

            if main_field == 2 and main_stride == 56 and palette:
                skin_indices_buf = bytearray(vertex_count * 8)
                skin_weights_buf = bytearray(vertex_count * 16)
                skinning_error = None
                for i in range(vertex_count):
                    off = i * 56
                    w0, w1, w2 = struct.unpack_from("<3f", main_body, off + 40)
                    w3 = 1.0 - (w0 + w1 + w2)
                    raw_weights = [w0, w1, w2, w3]
                    raw_indices = struct.unpack_from("<4B", main_body, off + 52)
                    if (not all(math.isfinite(weight) for weight in raw_weights)
                            or any(weight < 0.0 for weight in (w0, w1, w2))
                            or w3 < -_IMPLICIT_WEIGHT_ROUNDING_TOLERANCE):
                        skinning_error = f"vertex {i} has invalid packed skin weights"
                        break
                    if any(index >= len(palette) for index in raw_indices):
                        skinning_error = f"vertex {i} has blend index outside Matrix_Palette count {len(palette)}"
                        break
                    mapped_i = [palette[index] for index in raw_indices]
                    struct.pack_into("<4H", skin_indices_buf, i * 8, *mapped_i)
                    struct.pack_into("<4f", skin_weights_buf, i * 16, *raw_weights)
                if skinning_error:
                    diagnostics.append(f"PrimitiveGroup {geom_name!r}: {skinning_error}")
                else:
                    skin_indices_b64 = base64.b64encode(skin_indices_buf).decode("ascii")
                    skin_weights_b64 = base64.b64encode(skin_weights_buf).decode("ascii")

            il_payload = i_lists[0]
            try:
                _ia, _ib, _ic, i_bytes = struct.unpack_from("<HHII", il_payload, 0)
                if len(il_payload) != 12 + i_bytes:
                    raise ValueError(
                        f"Memory_Image_Index_List byte_count={i_bytes}, actual={len(il_payload) - 12}")
                i_body = il_payload[12:]
            except (ValueError, struct.error) as exc:
                diagnostics.append(f"PrimitiveGroup {geom_name!r}: {exc}")
                continue

        else:
            # Legacy Memory Imaged == 0 Path (Alex jacket / AlexVestShape)
            pos_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_POSITIONS]
            uv_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_UVS]
            idx_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_INDICES]
            mat_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_MATRICES]
            wt_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_WEIGHTS]

            if pos_chunks and len(pos_chunks[0]) >= 4 + vertex_count * 12:
                pos_buf[:vertex_count * 12] = pos_chunks[0][4 : 4 + vertex_count * 12]

            # UV_List has two u32 header fields: NumUVs and Channel.  The
            # previous legacy path skipped only NumUVs, so Channel and every
            # following float were shifted into the UV stream by four bytes.
            # Select source channel 0 and preserve its exact Vector2 order.
            legacy_uv = _legacy_uv_channel(uv_chunks, vertex_count)
            if legacy_uv is not None:
                uv_buf[:vertex_count * 8] = legacy_uv
            elif uv_chunks:
                diagnostics.append(f"PrimitiveGroup {geom_name!r}: no complete legacy UV channel 0")

            if idx_chunks and len(idx_chunks[0]) >= 4:
                n_idx = struct.unpack_from("<I", idx_chunks[0], 0)[0]
                raw_idx = idx_chunks[0][4:]
                if len(raw_idx) == n_idx * 2:
                    i_body = raw_idx
                elif len(raw_idx) == n_idx * 4:
                    u32_arr = struct.unpack_from(f"<{n_idx}I", raw_idx, 0)
                    i_body = struct.pack(f"<{n_idx}H", *u32_arr)
                else:
                    i_body = raw_idx[:n_idx * 2]

            if mat_chunks and wt_chunks and palette:
                m_body = mat_chunks[0][4:]
                w_body = wt_chunks[0][4:]
                skin_indices_buf = bytearray(vertex_count * 8)
                skin_weights_buf = bytearray(vertex_count * 16)
                skinning_error = None

                for i in range(vertex_count):
                    if i * 4 + 4 > len(m_body) or i * 12 + 12 > len(w_body):
                        skinning_error = f"legacy skin lists truncate at vertex {i}"
                        break
                    raw_indices = struct.unpack_from("<4B", m_body, i * 4)
                    w0, w1, w2 = struct.unpack_from("<3f", w_body, i * 12)
                    # Matrix_List stores semantic A,B,C,D as raw bytes D,C,B,A.
                    # Weight_List is semantic A=x, B=y, C=z, D=implicit.
                    raw_weights = _legacy_skin_weights(w0, w1, w2)
                    implicit = raw_weights[0]
                    if (not all(math.isfinite(weight) for weight in raw_weights)
                            or any(weight < 0.0 for weight in (w0, w1, w2))
                            or implicit < -_IMPLICIT_WEIGHT_ROUNDING_TOLERANCE):
                        skinning_error = f"legacy vertex {i} has invalid skin weights"
                        break
                    if any(index >= len(palette) for index in raw_indices):
                        skinning_error = f"legacy vertex {i} has blend index outside Matrix_Palette count {len(palette)}"
                        break
                    mapped_i = [palette[index] for index in raw_indices]
                    struct.pack_into("<4H", skin_indices_buf, i * 8, *mapped_i)
                    struct.pack_into("<4f", skin_weights_buf, i * 16, *raw_weights)

                if skinning_error:
                    diagnostics.append(f"PrimitiveGroup {geom_name!r}: {skinning_error}")
                else:
                    skin_indices_b64 = base64.b64encode(skin_indices_buf).decode("ascii")
                    skin_weights_b64 = base64.b64encode(skin_weights_buf).decode("ascii")

        if len(i_body) < 6:
            continue

        material_names = shader_to_textures.get(shader_name, {})
        material_texture_keys = {}
        for channel, name in material_names.items():
            if name in textures:
                material_texture_keys[channel] = textures[name]["key"]
            elif name in resolved_donor_textures:
                material_texture_keys[channel] = resolved_donor_textures[name]["key"]
            else:
                unresolved_texture_names.add(name)
        tex_key = material_texture_keys.get("color")

        decoded_meshes.append({
            "geometry_name": geom_name,
            "skin_name": skin_name,
            "skeleton_name": skeleton_name,
            "shader_name": shader_name,
            "texture_key": tex_key,
            "material_texture_keys": material_texture_keys,
            "vertex_count": vertex_count,
            "triangle_count": len(i_body) // 6,
            "positions": base64.b64encode(pos_buf).decode("ascii"),
            "uv": base64.b64encode(uv_buf).decode("ascii"),
            "indices": base64.b64encode(i_body).decode("ascii"),
            "skin_indices": skin_indices_b64,
            "skin_weights": skin_weights_b64,
        })

    if unresolved_texture_names:
        diagnostics.append(
            "unresolved texture names (not local, no audited donor): "
            + ", ".join(sorted(unresolved_texture_names)))

    return {
        "meshes": decoded_meshes,
        "textures": list(textures.values()) + [
            dict(t, donor=True) for t in resolved_donor_textures.values()],
        "skeletons": skeletons,
        "animations": animations,
        "assemblies": assemblies,
        "diagnostics": diagnostics,
    }
