"""Decode complete 3D entity geometry, UVs, textures and skeleton for web preview.

Optimized single-pass chunk extraction for sub-second responses.
"""
from __future__ import annotations

import base64
import struct
from typing import Any

from probe_static_geometry import (
    GEOMETRY, PRIMITIVE_GROUP, MEMORY_VERTEX_LIST, MEMORY_VERTEX_DESCRIPTION, _p3d_string
)
from render_static_uv_candidates import (
    TEXTURE, TEXTURE_DDS, IMAGE_DATA, _records, _parse_texture_dds_header
)
from cell_materials import compressed_texture

POLYSKIN = 0x00010001
MEMORY_INDEX_LIST = 0x00010013
SKELETON_V1 = 0x00002200
SKELETON_V2 = 0x00023000
SKELETON_JOINT = 0x00002201


def decode_entity_meshes(data: bytes, shape_filter: str | None = None) -> dict[str, Any]:
    records, children = _records(data)
    
    # 1. Direct lookup for Textures
    textures = {}
    for idx, record in enumerate(records):
        if record["type_id"] == TEXTURE:
            try:
                name = _p3d_string(record["payload"])[0]
                tex_children = [r for _, r in children.get(idx, [])]
                headers = [r for r in tex_children if r["type_id"] == TEXTURE_DDS]
                images = [r for r in tex_children if r["type_id"] == IMAGE_DATA]
                if headers and not images:
                    hdr_idx = [i for i, r in children.get(idx, []) if r["type_id"] == TEXTURE_DDS][0]
                    images = [r for _, r in children.get(hdr_idx, []) if r["type_id"] == IMAGE_DATA]
                if headers and images:
                    tname, w, h, mips, algorithm = _parse_texture_dds_header(headers[0]["payload"])
                    textures[name] = compressed_texture(images[0]["payload"], (w, h, mips, algorithm))
            except Exception:
                pass

    # 2. Extract Skeletons
    skeletons = []
    for idx, record in enumerate(records):
        if record["type_id"] in (SKELETON_V1, SKELETON_V2):
            try:
                skel_name = _p3d_string(record["payload"])[0]
                joints = []
                for _, child in children.get(idx, []):
                    if child["type_id"] == SKELETON_JOINT:
                        j_payload = child["payload"]
                        j_name, offset = _p3d_string(j_payload)
                        parent_idx = struct.unpack_from("<i", j_payload, offset)[0] if offset + 4 <= len(j_payload) else -1
                        joints.append({"name": j_name, "parent": parent_idx})
                skeletons.append({
                    "name": skel_name,
                    "joint_count": len(joints),
                    "joints": joints,
                })
            except Exception:
                pass

    # 3. Extract Meshes (Geometry / Polyskin)
    meshes = []
    for idx, record in enumerate(records):
        if record["type_id"] not in (POLYSKIN, GEOMETRY):
            continue
        try:
            geom_name, _ = _p3d_string(record["payload"])
        except Exception:
            continue
            
        if shape_filter and geom_name != shape_filter:
            continue
            
        for pidx, pg_rec in children.get(idx, []):
            if pg_rec["type_id"] != PRIMITIVE_GROUP:
                continue
            
            pg_payload = pg_rec["payload"]
            shader_name = ""
            vertex_count = 0
            index_count = 0
            try:
                if len(pg_payload) >= 4:
                    shader_name, offset = _p3d_string(pg_payload, 4)
                    if offset + 16 <= len(pg_payload):
                        _ptype, _fmt, vertex_count, index_count = struct.unpack_from("<4I", pg_payload, offset)
            except Exception:
                pass
                
            pg_children = children.get(pidx, [])
            v_lists = [r["payload"] for _, r in pg_children if r["type_id"] == MEMORY_VERTEX_LIST]
            i_lists = [r["payload"] for _, r in pg_children if r["type_id"] == MEMORY_INDEX_LIST]
            d_lists = [r["payload"] for _, r in pg_children if r["type_id"] == MEMORY_VERTEX_DESCRIPTION]
            
            if not v_lists or not i_lists:
                continue
                
            vl_payload = v_lists[0]
            il_payload = i_lists[0]
            
            if len(vl_payload) < 12 or len(il_payload) < 12:
                continue
                
            v_bytes = struct.unpack_from("<I", vl_payload, 8)[0]
            v_body = vl_payload[12:12 + v_bytes]
            
            if vertex_count <= 0:
                vertex_count = len(v_body) // 32
                
            if vertex_count <= 0 or v_bytes < vertex_count:
                continue
                
            stride = v_bytes // vertex_count if (v_bytes % vertex_count == 0) else 56
            
            pos_buf = bytearray(vertex_count * 12)
            uv_buf = bytearray(vertex_count * 8)
            
            uv_offset = 20 if stride in (64, 56, 28) else (24 if stride >= 32 else 16)
            if d_lists and len(d_lists[0]) >= 16:
                desc = d_lists[0]
                for off in range(16, len(desc), 17):
                    if off + 17 <= len(desc):
                        shash, _src, aoff, _st, _et, _ui = struct.unpack_from("<IIIHHB", desc, off)
                        if shash == 0x00364509: # TEXCOORD0
                            uv_offset = aoff
                            break

            for i in range(vertex_count):
                if i * stride + 12 <= len(v_body):
                    pos_buf[i * 12: i * 12 + 12] = v_body[i * stride: i * stride + 12]
                if i * stride + uv_offset + 8 <= len(v_body):
                    uv_buf[i * 8: i * 8 + 8] = v_body[i * stride + uv_offset: i * stride + uv_offset + 8]
                    
            i_bytes = struct.unpack_from("<I", il_payload, 8)[0]
            i_body = il_payload[12:12 + i_bytes]
            
            meshes.append({
                "geometry_name": geom_name,
                "shader_name": shader_name,
                "vertex_count": vertex_count,
                "triangle_count": len(i_body) // 6,
                "positions": base64.b64encode(pos_buf).decode("ascii"),
                "uv": base64.b64encode(uv_buf).decode("ascii"),
                "indices": base64.b64encode(i_body).decode("ascii"),
                "texture_key": list(textures.keys())[0] if textures else None,
            })

    return {
        "meshes": meshes,
        "textures": list(textures.values()),
        "skeletons": skeletons,
    }
