#!/usr/bin/env python3
"""Probe static Pure3D geometry and world-coordinate bounds without exporting assets.

The command fetches selected user-owned RCF entries into memory from the local
viewer, parses only structural headers plus vertex POSITION float triples, and
writes a metadata-only JSON report.  It never writes the fetched P3D bytes,
indices, normals, UVs, textures, or other asset payloads to disk.

It is intentionally a probe rather than a universal exporter.  Its first job
is to establish whether world Cell geometry uses a shared coordinate system and
to report the observed memory-image vertex strides before a map preview is
attempted.
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

try:
    import numpy as np
except ImportError:  # The viewer server intentionally has no third-party dependency requirement.
    np = None

SIG_LE = 0xFF443350
SIG_LE_SWAPPED = struct.unpack("<I", struct.pack(">I", SIG_LE))[0]
GEOMETRY = 0x00010000
PRIMITIVE_GROUP = 0x00010020
MEMORY_VERTEX_LIST = 0x00010012


def _p3d_string(payload: bytes, offset: int = 0) -> tuple[str, int]:
    if offset >= len(payload):
        raise ValueError("missing P3D string length")
    width = payload[offset]
    end = offset + 1 + width
    if end > len(payload):
        raise ValueError("P3D string exceeds payload")
    return payload[offset + 1:end].split(b"\0", 1)[0].decode("utf-8", "replace"), end


def _walk(data: bytes, start: int, end: int, parent: int | None, depth: int, output: list[dict[str, Any]]) -> None:
    offset = start
    while offset < end:
        if offset + 12 > end:
            raise ValueError(f"truncated chunk header at 0x{offset:X}")
        type_id, header_size, total_size = struct.unpack_from("<III", data, offset)
        if header_size < 12 or total_size < header_size or offset + total_size > end:
            raise ValueError(
                f"invalid chunk at 0x{offset:X}: header={header_size}, total={total_size}, parent_end=0x{end:X}")
        record = {
            "type_id": type_id,
            "header_size": header_size,
            "total_size": total_size,
            "payload": data[offset + 12:offset + header_size],
            "parent": parent,
            "depth": depth,
        }
        output.append(record)
        own_index = len(output) - 1
        _walk(data, offset + header_size, offset + total_size, own_index, depth + 1, output)
        offset += total_size
    if offset != end:
        raise ValueError("chunk walk ended at an invalid boundary")


def _parse_primitive_group(payload: bytes) -> dict[str, Any]:
    if len(payload) < 4:
        raise ValueError("PrimitiveGroup payload too short")
    version = struct.unpack_from("<I", payload, 0)[0]
    shader_name, offset = _p3d_string(payload, 4)
    if offset + 36 > len(payload):
        raise ValueError("PrimitiveGroup header truncated after shader name")
    fields = struct.unpack_from("<9I", payload, offset)
    return {
        "version": version,
        "shader_name": shader_name,
        "primitive_type": fields[0],
        "vertex_type": fields[1],
        "vertex_count": fields[2],
        "index_count": fields[3],
        "matrix_count": fields[4],
        "memory_imaged": fields[5],
        "optimized": fields[6],
        "vertex_animated": fields[7],
        "vertex_animation_mask": fields[8],
    }


def _direct_children(records: list[dict[str, Any]], parent_index: int) -> list[dict[str, Any]]:
    return [record for record in records if record["parent"] == parent_index]


def _bounds_from_memory_vertex_list(payload: bytes, vertex_count: int) -> tuple[int, list[float], list[float]]:
    """Report the stride and POSITION bounds for a packed static vertex list.

    The verified map samples have POSITION as float32x3 at offset 0.  This
    function enforces the byte-count relation before reading it and exposes the
    observed stride in output; it does not guess UV/normal/tangent layouts.
    """
    if len(payload) < 12:
        raise ValueError("MemoryImageVertexList header too short")
    _version, _param, byte_count = struct.unpack_from("<III", payload, 0)
    body = payload[12:]
    if byte_count != len(body):
        raise ValueError(f"MemoryImageVertexList byte_count={byte_count}, actual={len(body)}")
    if vertex_count <= 0:
        raise ValueError("PrimitiveGroup has zero vertices")
    if byte_count % vertex_count:
        raise ValueError(f"vertex byte_count {byte_count} not divisible by vertex_count {vertex_count}")
    stride = byte_count // vertex_count
    if stride < 12 or stride % 4:
        raise ValueError(f"unsupported POSITION-compatible vertex stride {stride}")
    if np is not None:
        floats = np.frombuffer(body, dtype="<f4").reshape(vertex_count, stride // 4)
        positions = floats[:, :3]
        if not np.isfinite(positions).all():
            raise ValueError("non-finite POSITION data")
        minimum = positions.min(axis=0).astype(float).tolist()
        maximum = positions.max(axis=0).astype(float).tolist()
    else:
        # A dependency-free fallback for the viewer server.  The probe only
        # needs the first POSITION float3 at each stride, not any other vertex
        # attribute, so it can remain a pure standard-library operation.
        minimum = [math.inf, math.inf, math.inf]
        maximum = [-math.inf, -math.inf, -math.inf]
        for offset in range(0, len(body), stride):
            position = struct.unpack_from("<3f", body, offset)
            if not all(math.isfinite(value) for value in position):
                raise ValueError("non-finite POSITION data")
            for axis, value in enumerate(position):
                minimum[axis] = min(minimum[axis], value)
                maximum[axis] = max(maximum[axis], value)
    return stride, minimum, maximum


def scan_static_geometry(data: bytes) -> dict[str, Any]:
    """Return geometry names/counts/POSITION bounds; no original content bytes."""
    if len(data) < 12:
        raise ValueError("file too small to be Pure3D")
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic == SIG_LE:
        endian = "LE"
    elif magic == SIG_LE_SWAPPED:
        raise ValueError("big-endian Pure3D geometry probe is not implemented")
    else:
        raise ValueError(f"not a Pure3D file (magic={data[:4].hex()})")
    total_size = struct.unpack_from("<I", data, 8)[0]
    if total_size < 12 or total_size > len(data):
        raise ValueError(f"invalid file total size {total_size} for {len(data)} bytes")

    records: list[dict[str, Any]] = []
    _walk(data, 12, total_size, None, 0, records)
    result_geometries = []
    stride_counts: Counter[int] = Counter()
    all_mins = []
    all_maxs = []
    warnings = []

    for index, record in enumerate(records):
        if record["type_id"] != GEOMETRY:
            continue
        try:
            name, _ = _p3d_string(record["payload"])
        except ValueError as exc:
            warnings.append(f"Geometry#{index}: {exc}")
            continue
        groups = []
        for child_index, child in enumerate(records):
            if child["parent"] != index or child["type_id"] != PRIMITIVE_GROUP:
                continue
            try:
                group = _parse_primitive_group(child["payload"])
            except ValueError as exc:
                warnings.append(f"Geometry {name!r} PrimitiveGroup#{child_index}: {exc}")
                continue
            memory_lists = [
                candidate for candidate in _direct_children(records, child_index)
                if candidate["type_id"] == MEMORY_VERTEX_LIST
            ]
            if len(memory_lists) != 1:
                group["position_bounds_status"] = f"expected one MemoryImageVertexList, found {len(memory_lists)}"
                groups.append(group)
                continue
            try:
                stride, minimum, maximum = _bounds_from_memory_vertex_list(
                    memory_lists[0]["payload"], group["vertex_count"])
                group["vertex_stride"] = stride
                group["position_min"] = minimum
                group["position_max"] = maximum
                group["position_bounds_status"] = "ok"
                stride_counts[stride] += 1
                all_mins.append(minimum)
                all_maxs.append(maximum)
            except ValueError as exc:
                group["position_bounds_status"] = f"unavailable: {exc}"
            groups.append(group)
        result_geometries.append({"name": name, "primitive_groups": groups})

    if all_mins:
        mins = [min(bounds[axis] for bounds in all_mins) for axis in range(3)]
        maxs = [max(bounds[axis] for bounds in all_maxs) for axis in range(3)]
        world_bounds_status = "ok"
    else:
        mins = maxs = None
        world_bounds_status = "no POSITION-compatible memory-image groups"

    return {
        "format": "Pure3D",
        "endian": endian,
        "decompressed_size": len(data),
        "declared_total_size": total_size,
        "chunk_count": len(records),
        "geometry_count": len(result_geometries),
        "position_group_count": sum(stride_counts.values()),
        "vertex_stride_counts": {str(key): value for key, value in sorted(stride_counts.items())},
        "world_position_bounds_status": world_bounds_status,
        "world_position_min": mins,
        "world_position_max": maxs,
        "geometries": result_geometries,
        "warnings": warnings,
    }


def _fetch_raw(base_url: str, rcf_path: str, entry_name: str, timeout: int) -> bytes:
    query = urllib.parse.urlencode({"path": rcf_path, "name": entry_name, "raw": "1"})
    url = base_url.rstrip("/") + "/api/rcf_entry?" + query
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not fetch {entry_name!r}: {exc}") from exc


def _atomic_json(path: str, data: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".static_geometry_probe_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
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
    parser.add_argument("--entry", action="append", required=True, help="RCF entry name; repeat for multiple Cells")
    parser.add_argument("--out", required=True, help="metadata-only JSON output path")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    entries = []
    for name in args.entry:
        print(f"probing {name}…")
        result = scan_static_geometry(_fetch_raw(args.base_url, args.rcf_path, name, args.timeout))
        result["entry_name"] = name
        entries.append(result)
        print(
            f"  {result['geometry_count']} Geometry, {result['position_group_count']} position groups, "
            f"bounds={result['world_position_bounds_status']}")
    report = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "scope": "metadata-only geometry names/counts and POSITION bounds; no game asset payloads",
        "source": {"base_url": args.base_url.rstrip("/"), "rcf_path": args.rcf_path},
        "entries": entries,
    }
    _atomic_json(args.out, report)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
