#!/usr/bin/env python3
"""Build metadata-only shader/resource dependency tables for selected Manhattan Cells.

The probe is intentionally scoped to ``mergedDrawableRoot*`` PrimitiveGroups:
it reports their exact shader-name references, correlates those names with
``NewShader`` definitions found in the same Cell, and lists only textual
string-parameter values plus local Texture definition metadata.  It never
writes P3D, DDS, vertex, index, normal or UV payload bytes to disk.

A string parameter is reported as an *image-reference candidate*, not a final
material interpretation.  Its name/value relation is useful evidence for the
next rendered UV/material regression, but shader templates, sampler meaning,
and cross-archive fallback resolution remain explicitly unproven.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import struct
import tempfile
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from typing import Any

from probe_static_geometry import (
    GEOMETRY,
    PRIMITIVE_GROUP,
    WORLD_MERGED_GEOMETRY_PREFIX,
    _parse_primitive_group,
    _p3d_string,
    _walk,
)

NEW_SHADER = 0x00011015
NEW_SHADER_STRING_PARAMETER = 0x00011016
TEXTURE = 0x00019000


def _children(records: list[dict[str, Any]]) -> dict[int | None, list[tuple[int, dict[str, Any]]]]:
    output: dict[int | None, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, record in enumerate(records):
        output[record["parent"]].append((index, record))
    return output


def _parse_exact_string_pair(payload: bytes, label: str) -> tuple[str, str]:
    first, offset = _p3d_string(payload)
    second, offset = _p3d_string(payload, offset)
    if offset != len(payload):
        raise ValueError(f"{label} has {len(payload) - offset} unexpected trailing bytes")
    return first, second


def _parse_new_shader_header(payload: bytes) -> dict[str, Any]:
    """Parse NewShader's stable name/u32/template/u32 header.

    The outer identifier and field sequence match the pre-existing Gibbed
    reference source; the two numeric fields are intentionally left unnamed
    because no rendering decision in this probe relies on them.
    """
    name, offset = _p3d_string(payload)
    if offset + 4 > len(payload):
        raise ValueError("NewShader lacks first uint32 field")
    first_unknown = struct.unpack_from("<I", payload, offset)[0]
    template, offset = _p3d_string(payload, offset + 4)
    if offset + 4 != len(payload):
        raise ValueError("NewShader header is not name/u32/template/u32 exactly")
    second_unknown = struct.unpack_from("<I", payload, offset)[0]
    return {
        "shader_name": name,
        "template_name": template,
        "header_field_after_name": first_unknown,
        "header_field_after_template": second_unknown,
    }


def _parse_texture_header(payload: bytes) -> dict[str, Any]:
    """Parse the textual/numeric header of a local Texture definition only."""
    name, offset = _p3d_string(payload)
    if offset + 36 != len(payload):
        raise ValueError(f"Texture header expected 36 bytes after name, got {len(payload) - offset}")
    version, width, height, bits_per_pixel, alpha_depth, mip_map_count, texture_type, usage_hint, priority = struct.unpack_from(
        "<9I", payload, offset)
    return {
        "texture_name": name,
        "version": version,
        "width": width,
        "height": height,
        "bits_per_pixel": bits_per_pixel,
        "alpha_depth": alpha_depth,
        "mip_map_count": mip_map_count,
        "texture_type": texture_type,
        "usage_hint": usage_hint,
        "priority": priority,
    }


def scan_cell_shader_dependencies(data: bytes, entry_name: str) -> dict[str, Any]:
    """Return an all-textual dependency table for one selected Cell."""
    if len(data) < 12:
        raise ValueError("file shorter than a Pure3D header")
    magic, _header_size, total_size = struct.unpack_from("<III", data, 0)
    if magic != 0xFF443350:
        raise ValueError(f"only LE Pure3D is implemented, magic={data[:4].hex()}")
    if total_size < 12 or total_size > len(data):
        raise ValueError(f"invalid declared total size {total_size} for {len(data)} bytes")

    records: list[dict[str, Any]] = []
    _walk(data, 12, total_size, None, 0, records)
    children = _children(records)
    errors: list[str] = []

    # First collect the names actually used by the safe world-core Geometry.
    used_by_shader: dict[str, dict[str, Any]] = {}
    world_geometry_names = []
    skipped_non_world_geometry_count = 0
    for geometry_index, geometry in enumerate(records):
        if geometry["type_id"] != GEOMETRY:
            continue
        try:
            geometry_name, _ = _p3d_string(geometry["payload"])
        except ValueError as exc:
            errors.append(f"Geometry#{geometry_index}: {exc}")
            continue
        if not geometry_name.casefold().startswith(WORLD_MERGED_GEOMETRY_PREFIX):
            skipped_non_world_geometry_count += 1
            continue
        world_geometry_names.append(geometry_name)
        for primitive_index, primitive in children[geometry_index]:
            if primitive["type_id"] != PRIMITIVE_GROUP:
                continue
            try:
                parsed = _parse_primitive_group(primitive["payload"])
            except ValueError as exc:
                errors.append(f"Geometry {geometry_name!r} PrimitiveGroup#{primitive_index}: {exc}")
                continue
            item = used_by_shader.setdefault(parsed["shader_name"], {
                "shader_name": parsed["shader_name"],
                "primitive_group_ref_count": 0,
                "geometry_names": set(),
                "primitive_types": Counter(),
            })
            item["primitive_group_ref_count"] += 1
            item["geometry_names"].add(geometry_name)
            item["primitive_types"][str(parsed["primitive_type"])] += 1

    # Then parse all local NewShader definitions and their directly owned
    # string-parameter chunks.  The parser keeps non-string child type counts
    # visible instead of pretending those fields have already been decoded.
    definitions: dict[str, dict[str, Any]] = {}
    for shader_index, shader in enumerate(records):
        if shader["type_id"] != NEW_SHADER:
            continue
        try:
            definition = _parse_new_shader_header(shader["payload"])
            params = []
            other_child_types: Counter[str] = Counter()
            for parameter_index, parameter in children[shader_index]:
                if parameter["type_id"] == NEW_SHADER_STRING_PARAMETER:
                    key, value = _parse_exact_string_pair(parameter["payload"], "NewShader string parameter")
                    params.append({"parameter_name": key, "value": value})
                else:
                    other_child_types[f"0x{parameter['type_id']:08X}"] += 1
            definition["string_parameters"] = sorted(params, key=lambda item: (item["parameter_name"], item["value"]))
            definition["non_string_direct_child_type_counts"] = dict(sorted(other_child_types.items()))
            existing = definitions.get(definition["shader_name"])
            if existing is not None:
                errors.append(f"duplicate local NewShader name {definition['shader_name']!r}")
            else:
                definitions[definition["shader_name"]] = definition
        except ValueError as exc:
            errors.append(f"NewShader#{shader_index}: {exc}")

    texture_definitions = []
    texture_names: set[str] = set()
    for texture_index, texture in enumerate(records):
        if texture["type_id"] != TEXTURE:
            continue
        try:
            parsed = _parse_texture_header(texture["payload"])
            if parsed["texture_name"] in texture_names:
                errors.append(f"duplicate local Texture name {parsed['texture_name']!r}")
            texture_names.add(parsed["texture_name"])
            texture_definitions.append(parsed)
        except ValueError as exc:
            errors.append(f"Texture#{texture_index}: {exc}")
    texture_definitions.sort(key=lambda item: item["texture_name"].casefold())

    referenced = []
    unresolved_shader_names = []
    all_candidate_names: set[str] = set()
    locally_resolved_candidate_names: set[str] = set()
    for shader_name, usage in sorted(used_by_shader.items(), key=lambda pair: pair[0].casefold()):
        item: dict[str, Any] = {
            "shader_name": shader_name,
            "primitive_group_ref_count": usage["primitive_group_ref_count"],
            "geometry_names": sorted(usage["geometry_names"]),
            "primitive_type_counts": dict(sorted(usage["primitive_types"].items())),
        }
        definition = definitions.get(shader_name)
        if definition is None:
            item["definition_status"] = "not found in this Cell"
            unresolved_shader_names.append(shader_name)
        else:
            item["definition_status"] = "local NewShader definition parsed"
            item["template_name"] = definition["template_name"]
            item["string_parameters"] = definition["string_parameters"]
            item["non_string_direct_child_type_counts"] = definition["non_string_direct_child_type_counts"]
            candidates = []
            for parameter in definition["string_parameters"]:
                value = parameter["value"]
                if not value:
                    continue
                all_candidate_names.add(value)
                locally_defined = value in texture_names
                if locally_defined:
                    locally_resolved_candidate_names.add(value)
                candidates.append({
                    **parameter,
                    "matches_local_texture_definition": locally_defined,
                })
            item["image_reference_candidates"] = candidates
        referenced.append(item)

    return {
        "entry_name": entry_name,
        "decompressed_size": len(data),
        "declared_total_size": total_size,
        "scope": "mergedDrawableRoot* PrimitiveGroup shader-name references plus textual local NewShader/Texture headers only",
        "world_geometry_names": world_geometry_names,
        "skipped_non_world_geometry_count": skipped_non_world_geometry_count,
        "core_primitive_group_count": sum(item["primitive_group_ref_count"] for item in used_by_shader.values()),
        "core_unique_shader_count": len(used_by_shader),
        "core_shader_references": referenced,
        "local_new_shader_definition_count": len(definitions),
        "local_texture_definition_count": len(texture_definitions),
        "local_texture_definitions": texture_definitions,
        "unique_image_reference_candidate_count": len(all_candidate_names),
        "image_reference_candidates_resolved_to_local_texture_count": len(locally_resolved_candidate_names),
        "unresolved_core_shader_names": unresolved_shader_names,
        "errors": errors,
    }


def _fetch_raw(base_url: str, rcf_path: str, entry_name: str, timeout: int) -> bytes:
    query = urllib.parse.urlencode({"path": rcf_path, "name": entry_name, "raw": "1"})
    url = base_url.rstrip("/") + "/api/rcf_entry?" + query
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not fetch {entry_name!r}: {exc}") from exc


def _atomic_json(path: str, value: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".cell_shader_dependencies_", suffix=".json", dir=directory)
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
    parser.add_argument("--entry", action="append", required=True)
    parser.add_argument("--out", required=True, help="metadata-only JSON output")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    entries = []
    for entry_name in args.entry:
        print(f"cataloguing shader references: {entry_name}")
        report = scan_cell_shader_dependencies(
            _fetch_raw(args.base_url, args.rcf_path, entry_name, args.timeout), entry_name)
        entries.append(report)
        print(
            f"  core groups={report['core_primitive_group_count']}, "
            f"core shaders={report['core_unique_shader_count']}, "
            f"local textures={report['local_texture_definition_count']}, "
            f"unresolved shaders={len(report['unresolved_core_shader_names'])}, errors={len(report['errors'])}")
    output = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "scope": "metadata-only selected Cell shader dependency research; no texture/geometry payload",
        "source": {"base_url": args.base_url.rstrip("/"), "rcf_path": args.rcf_path},
        "entries": entries,
    }
    _atomic_json(args.out, output)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
