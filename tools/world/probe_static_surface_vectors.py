#!/usr/bin/env python3
"""Measure normal/tangent *candidates* against validated static Cell triangles.

This metadata-only probe is deliberately narrower than a material exporter.  A
selected ``mergedDrawableRoot*`` TriangleList must first satisfy the strict
POSITION + uint16-index checks.  The caller then gives two declared source-0
float offsets.  The probe measures unit-vector length, interpolated candidate
normal alignment to geometric face normals, candidate-vector orthogonality,
and the fourth tangent component distribution.

It provides structural/geometric evidence only.  It does not claim final game
lighting, normal-map convention, shader transform, or glTF mapping: those need
an additional rendered-material regression.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import struct
import tempfile
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any

from export_static_geometry_diagnostic import (
    MEMORY_INDEX_LIST,
    CoreGeometryError,
    _parse_memory_index_list,
    _parse_memory_vertex_list,
)
from probe_static_geometry import (
    GEOMETRY,
    MEMORY_VERTEX_DESCRIPTION,
    MEMORY_VERTEX_LIST,
    PRIMITIVE_GROUP,
    _fingerprint_vertex_description,
    _parse_primitive_group,
    _p3d_string,
    _walk,
)


def _children(records: list[dict[str, Any]]) -> dict[int | None, list[tuple[int, dict[str, Any]]]]:
    output: dict[int | None, list[tuple[int, dict[str, Any]]]] = {}
    for index, record in enumerate(records):
        output.setdefault(record["parent"], []).append((index, record))
    return output


def _finite_float3_stream(vertex_payload: bytes, vertex_count: int, stride: int, declaration: dict[str, Any], offset: int, label: str) -> tuple[str, list[tuple[float, float, float]]]:
    matches = [attribute for attribute in declaration["attributes"] if attribute["source"] == 0 and attribute["offset"] == offset]
    if len(matches) != 1:
        raise CoreGeometryError(f"{label}: expected exactly one source-0 declaration at offset {offset}, found {len(matches)}")
    if offset < 0 or offset + 12 > stride:
        raise CoreGeometryError(f"{label}: float3 at offset {offset} does not fit stride={stride}")
    body = vertex_payload[12:]
    values = []
    for vertex in range(vertex_count):
        vector = struct.unpack_from("<3f", body, vertex * stride + offset)
        if not all(math.isfinite(value) for value in vector):
            raise CoreGeometryError(f"{label}: non-finite float3 at vertex {vertex}")
        values.append(vector)
    return matches[0]["semantic_hash"], values


def _normalise(value: tuple[float, float, float]) -> tuple[float, float, float] | None:
    length = math.sqrt(sum(component * component for component in value))
    if length <= 1e-12:
        return None
    return tuple(component / length for component in value)


def _summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "minimum": None, "mean": None, "median": None, "maximum": None}
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    median = ordered[midpoint] if len(ordered) % 2 else (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
    return {
        "count": len(values),
        "minimum": min(values),
        "mean": sum(values) / len(values),
        "median": median,
        "maximum": max(values),
    }


def analyze_surface_vectors(
    data: bytes,
    entry_name: str,
    geometry_name: str,
    group_ordinal: int,
    normal_offset: int,
    tangent_offset: int,
    tangent_w_offset: int,
) -> dict[str, Any]:
    """Return safe aggregate vector evidence for one strict core TriangleList."""
    if len(data) < 12:
        raise ValueError("file shorter than Pure3D header")
    magic, _header_size, total_size = struct.unpack_from("<III", data, 0)
    if magic != 0xFF443350:
        raise ValueError(f"only LE Pure3D is supported, magic={data[:4].hex()}")
    if total_size < 12 or total_size > len(data):
        raise ValueError(f"invalid total size {total_size} for {len(data)} bytes")
    records: list[dict[str, Any]] = []
    _walk(data, 12, total_size, None, 0, records)
    children = _children(records)
    geometry_index = next((index for index, record in enumerate(records)
                           if record["type_id"] == GEOMETRY and _p3d_string(record["payload"])[0] == geometry_name), None)
    if geometry_index is None:
        raise CoreGeometryError(f"Geometry {geometry_name!r} not found")
    if not geometry_name.casefold().startswith("mergeddrawableroot"):
        raise CoreGeometryError("refusing local-space geometry; expected mergedDrawableRoot*")
    primitive_groups = [(index, record) for index, record in children[geometry_index] if record["type_id"] == PRIMITIVE_GROUP]
    if not 1 <= group_ordinal <= len(primitive_groups):
        raise CoreGeometryError(f"group ordinal {group_ordinal} outside 1..{len(primitive_groups)}")
    group_index, group_record = primitive_groups[group_ordinal - 1]
    group = _parse_primitive_group(group_record["payload"])
    if group["primitive_type"] != 0:
        raise CoreGeometryError(f"expected TriangleList=0, got {group['primitive_type']}")
    direct = children[group_index]
    vertex_lists = [record for _index, record in direct if record["type_id"] == MEMORY_VERTEX_LIST]
    index_lists = [record for _index, record in direct if record["type_id"] == MEMORY_INDEX_LIST]
    declarations = [record for _index, record in direct if record["type_id"] == MEMORY_VERTEX_DESCRIPTION]
    if len(vertex_lists) != len(index_lists) != len(declarations) != 1:
        raise CoreGeometryError("expected exactly one vertex list, index list, and declaration")
    declaration = _fingerprint_vertex_description(declarations[0]["payload"])
    stride, positions_bytes, _pos_min, _pos_max = _parse_memory_vertex_list(vertex_lists[0]["payload"], group["vertex_count"], declaration)
    index_bytes, _minimum, _maximum, repeated, triangle_count = _parse_memory_index_list(
        index_lists[0]["payload"], group["vertex_count"], group["index_count"])
    normal_hash, normals = _finite_float3_stream(vertex_lists[0]["payload"], group["vertex_count"], stride, declaration, normal_offset, "normal candidate")
    tangent_hash, tangents = _finite_float3_stream(vertex_lists[0]["payload"], group["vertex_count"], stride, declaration, tangent_offset, "tangent candidate")
    if tangent_w_offset < 0 or tangent_w_offset + 4 > stride:
        raise CoreGeometryError(f"tangent W offset {tangent_w_offset} does not fit stride={stride}")
    raw_vertex_data = vertex_lists[0]["payload"][12:]
    tangent_w = [struct.unpack_from("<f", raw_vertex_data, number * stride + tangent_w_offset)[0] for number in range(group["vertex_count"])]
    if not all(math.isfinite(value) for value in tangent_w):
        raise CoreGeometryError("tangent W has non-finite value")

    positions = [struct.unpack_from("<3f", positions_bytes, number * 12) for number in range(group["vertex_count"])]
    indices = struct.unpack("<" + "H" * (len(index_bytes) // 2), index_bytes)
    face_dots = []
    tangent_face_dots = []
    face_count_nonzero = 0
    for offset in range(0, len(indices), 3):
        first, second, third = indices[offset:offset + 3]
        a, b, c = positions[first], positions[second], positions[third]
        ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        face = _normalise((
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        ))
        if face is None:
            continue
        face_count_nonzero += 1
        normal_average = tuple(sum(normals[index][axis] for index in (first, second, third)) / 3.0 for axis in range(3))
        tangent_average = tuple(sum(tangents[index][axis] for index in (first, second, third)) / 3.0 for axis in range(3))
        normal_unit = _normalise(normal_average)
        tangent_unit = _normalise(tangent_average)
        if normal_unit is not None:
            face_dots.append(sum(normal_unit[axis] * face[axis] for axis in range(3)))
        if tangent_unit is not None:
            tangent_face_dots.append(sum(tangent_unit[axis] * face[axis] for axis in range(3)))

    vertex_normal_tangent_dots = [sum(normal[axis] * tangent[axis] for axis in range(3))
                                  for normal, tangent in zip(normals, tangents)]
    normal_lengths = [math.sqrt(sum(component * component for component in vector)) for vector in normals]
    tangent_lengths = [math.sqrt(sum(component * component for component in vector)) for vector in tangents]
    w_counts = Counter(tangent_w)
    return {
        "entry_name": entry_name,
        "scope": "one strict mergedDrawableRoot TriangleList; aggregate candidate-vector geometry evidence only, no vertex payload",
        "geometry_name": geometry_name,
        "primitive_group_ordinal": group_ordinal,
        "shader_name": group["shader_name"],
        "layout_sha256": declaration["layout_sha256"],
        "vertex_stride": stride,
        "vertex_count": group["vertex_count"],
        "triangle_count": triangle_count,
        "repeated_index_triangle_count": repeated,
        "normal_candidate": {
            "semantic_hash": normal_hash,
            "offset": normal_offset,
            "float3_length": _summary(normal_lengths),
            "face_alignment_signed_dot": _summary(face_dots),
            "face_alignment_mean_absolute_dot": sum(abs(value) for value in face_dots) / len(face_dots) if face_dots else None,
            "face_alignment_fraction_abs_dot_at_least_0_9": sum(abs(value) >= 0.9 for value in face_dots) / len(face_dots) if face_dots else None,
        },
        "tangent_candidate": {
            "semantic_hash": tangent_hash,
            "offset": tangent_offset,
            "float3_length": _summary(tangent_lengths),
            "face_alignment_signed_dot": _summary(tangent_face_dots),
            "face_alignment_mean_absolute_dot": sum(abs(value) for value in tangent_face_dots) / len(tangent_face_dots) if tangent_face_dots else None,
            "normal_tangent_vertex_dot": _summary(vertex_normal_tangent_dots),
            "normal_tangent_vertex_mean_absolute_dot": sum(abs(value) for value in vertex_normal_tangent_dots) / len(vertex_normal_tangent_dots),
            "w_offset": tangent_w_offset,
            "w_value_counts": {str(key): value for key, value in sorted(w_counts.items())},
        },
        "nonzero_face_count": face_count_nonzero,
        "conclusion_limit": "geometric vector evidence only; render normal/tangent behavior separately before a material/export conclusion",
    }


def _fetch_raw(base_url: str, rcf_path: str, entry_name: str, timeout: int) -> bytes:
    query = urllib.parse.urlencode({"path": rcf_path, "name": entry_name, "raw": "1"})
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/rcf_entry?" + query, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not fetch {entry_name!r}: {exc}") from exc


def _atomic_json(path: str, value: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".static_surface_vectors_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--rcf-path", required=True)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--geometry", required=True)
    parser.add_argument("--group-ordinal", required=True, type=int)
    parser.add_argument("--normal-offset", required=True, type=int)
    parser.add_argument("--tangent-offset", required=True, type=int)
    parser.add_argument("--tangent-w-offset", required=True, type=int)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    report = analyze_surface_vectors(
        _fetch_raw(args.base_url, args.rcf_path, args.entry, args.timeout),
        args.entry, args.geometry, args.group_ordinal,
        args.normal_offset, args.tangent_offset, args.tangent_w_offset,
    )
    output = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": {"base_url": args.base_url.rstrip("/"), "rcf_path": args.rcf_path},
        "report": report,
    }
    _atomic_json(args.out, output)
    print(
        f"{report['geometry_name']} group {report['primitive_group_ordinal']}: "
        f"normal face |dot| mean={report['normal_candidate']['face_alignment_mean_absolute_dot']:.6f}, "
        f"tangent-normal |dot| mean={report['tangent_candidate']['normal_tangent_vertex_mean_absolute_dot']:.6f}")
    print(f"wrote metadata-only report {args.out}")


if __name__ == "__main__":
    main()
