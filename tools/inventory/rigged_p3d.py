"""Structure-only census helpers for rigged Pure3D packages.

This module deliberately returns names/counts/header metadata only.  It does
not extract vertex buffers, textures, animation keys, or any other game asset
payload.  It is shared by the viewer-side RCF census endpoint and by its unit
tests, so qualification remains reproducible:

* a candidate must have a CompositeDrawable with a skeleton name and at least
  one direct type=2 (polyskin) reference;
* local skeleton availability and primitive-group shader names are reported as
  evidence, not silently assumed.
"""
from __future__ import annotations

import struct
from typing import Any

try:
    import inspect_p3d
except ImportError as exc:  # pragma: no cover - import setup is caller-specific
    raise ImportError("rigged_p3d requires tools/p3d_parser on sys.path") from exc


COMPOSITE_DRAWABLE = 0x00123000
COMPOSITE_PRIMITIVE = 0x00123001
SKELETON = 0x00023000
POLYSKIN = 0x00010001
PRIMITIVE_GROUP = 0x00010020


def _p3d_string(payload: bytes, offset: int) -> tuple[str, int]:
    """Read the compact P3D string form used by verified Prototype headers.

    The leading byte is the field width including a trailing NUL.  Keeping the
    bounds checks here means a malformed header becomes a per-record parse
    warning, rather than turning a broad inventory scan into a false result.
    """
    if offset >= len(payload):
        raise ValueError("missing P3D string length")
    width = payload[offset]
    end = offset + 1 + width
    if end > len(payload):
        raise ValueError("P3D string extends past header payload")
    raw = payload[offset + 1:end]
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace"), end


def _u32(payload: bytes, offset: int) -> int:
    if offset + 4 > len(payload):
        raise ValueError("missing uint32 header field")
    return struct.unpack_from("<I", payload, offset)[0]


def _decode_skeleton(payload: bytes) -> dict[str, Any]:
    name, offset = _p3d_string(payload, 0)
    return {
        "name": name,
        "version": _u32(payload, offset),
        "joint_count": _u32(payload, offset + 4),
        "partition_count": _u32(payload, offset + 8),
        "limb_count": _u32(payload, offset + 12),
    }


def _decode_composite(payload: bytes) -> dict[str, Any]:
    version = _u32(payload, 0)
    name, offset = _p3d_string(payload, 4)
    skeleton_name, offset = _p3d_string(payload, offset)
    return {
        "name": name,
        "skeleton_name": skeleton_name,
        "version": version,
        "declared_primitive_count": _u32(payload, offset),
    }


def _decode_composite_primitive(payload: bytes) -> dict[str, Any]:
    # Prototype's CompositeDrawablePolySkinReference header was verified on
    # real Alex/Blackwatch packages: two uint32s, a P3D string, then two u32s.
    name, offset = _p3d_string(payload, 8)
    return {
        "name": name,
        "primitive_type": _u32(payload, offset),
        "joint_index": _u32(payload, offset + 4),
    }


def _decode_primitive_group_shader(payload: bytes) -> str:
    # PrimitiveGroup begins Version(uint32), ShaderName(P3DString).  We only
    # retain that binding name, never the geometry/index/vertex payload.
    name, _ = _p3d_string(payload, 4)
    return name


def _walk(data: bytes, endian: str, declared_total_size: int) -> list[tuple[int, int, int, int, bytes]]:
    if declared_total_size < 12 or declared_total_size > len(data):
        raise ValueError(
            f"invalid declared total size {declared_total_size} for {len(data)} byte file")
    output: list[tuple[int, int, int, int, bytes]] = []
    inspect_p3d.dump_chunk(data, 12, declared_total_size, endian, 0, output)
    return output


def _direct_children(records: list[tuple[int, int, int, int, bytes]], parent_index: int) -> list[tuple[int, int, int, int, bytes]]:
    """Return direct children from dump_chunk's depth-first flat output."""
    parent_depth = records[parent_index][0]
    out = []
    for record in records[parent_index + 1:]:
        depth = record[0]
        if depth <= parent_depth:
            break
        if depth == parent_depth + 1:
            out.append(record)
    return out


def scan_p3d_bytes(data: bytes) -> dict[str, Any]:
    """Return a compact, content-free rigging/material census for one P3D.

    Raises ``ValueError`` for a non-P3D or structurally invalid root.  Individual
    malformed semantic headers are reported in ``parse_warnings`` so other
    valid candidates in the same package remain visible.
    """
    if len(data) < 12:
        raise ValueError("file too small to be a Pure3D file")
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic == inspect_p3d.SIG_LE:
        endian = "<"
        endian_name = "LE"
    elif magic == inspect_p3d.SIG_LE_SWAPPED:
        endian = ">"
        endian_name = "BE"
    else:
        raise ValueError(f"not a Pure3D file (magic bytes = {data[:4].hex()})")

    # Prototype assets inspected so far are LE.  Do not fake semantic BE
    # headers; a future BE sample will be visible as a warning instead.
    if endian != "<":
        raise ValueError("big-endian semantic census is not implemented")

    declared_total_size = struct.unpack_from(endian + "I", data, 8)[0]
    records = _walk(data, endian, declared_total_size)
    warnings: list[str] = []

    skeletons = []
    for _, type_id, _, _, payload in records:
        if type_id != SKELETON:
            continue
        try:
            skeletons.append(_decode_skeleton(payload))
        except ValueError as exc:
            warnings.append(f"Skeleton header: {exc}")

    composites = []
    for index, (_, type_id, _, _, payload) in enumerate(records):
        if type_id != COMPOSITE_DRAWABLE:
            continue
        try:
            composite = _decode_composite(payload)
            refs = []
            for _, child_type, _, _, child_payload in _direct_children(records, index):
                if child_type != COMPOSITE_PRIMITIVE:
                    continue
                try:
                    refs.append(_decode_composite_primitive(child_payload))
                except ValueError as exc:
                    warnings.append(f"CompositeDrawable {composite['name']!r} primitive: {exc}")
            composite["primitive_references"] = refs
            composite["polyskin_reference_count"] = sum(
                ref["primitive_type"] == 2 for ref in refs)
            composites.append(composite)
        except ValueError as exc:
            warnings.append(f"CompositeDrawable header: {exc}")

    shader_names = []
    polyskin_count = 0
    for _, type_id, _, _, payload in records:
        if type_id == POLYSKIN:
            polyskin_count += 1
        elif type_id == PRIMITIVE_GROUP:
            try:
                name = _decode_primitive_group_shader(payload)
                if name and name not in shader_names:
                    shader_names.append(name)
            except ValueError as exc:
                warnings.append(f"PrimitiveGroup shader header: {exc}")

    local_skeleton_names = {item["name"] for item in skeletons}
    rigged_composites = []
    for composite in composites:
        if composite["skeleton_name"] and composite["polyskin_reference_count"]:
            qualified = dict(composite)
            qualified["skeleton_is_local"] = composite["skeleton_name"] in local_skeleton_names
            rigged_composites.append(qualified)

    return {
        "format": "Pure3D",
        "file_size": len(data),
        "declared_total_size": declared_total_size,
        "endian": endian_name,
        "chunk_count": len(records),
        "skeletons": skeletons,
        "composites": composites,
        "rigged_composites": rigged_composites,
        "polyskin_count": polyskin_count,
        "shader_names": shader_names,
        "parse_warnings": warnings,
    }
