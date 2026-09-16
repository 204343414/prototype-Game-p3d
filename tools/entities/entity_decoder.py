"""Decode complete 3D entity geometry, UVs, textures, skeletons and animations.

Multi-stream vertex buffer support (Stream 0 positions/normals, Stream 1 UVs for characters),
LOD0 prioritization, shape isolation, joint matrix tree, and animation track extraction.
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

POLYSKIN = 0x00010001
MEMORY_INDEX_LIST = 0x00010013
SKELETON_V1 = 0x00002200
SKELETON_V2 = 0x00023000
SKELETON_JOINT = 0x00002201
SKELETON_JOINT_V2 = 0x00023001
ANIMATION = 0x00121000
NEW_SHADER = 0x00011015
OLD_SHADER = 0x00010003
TEXTURE_PARAM = 0x00011016
TEXTURE_IMAGE_SPEC = 0x00019006


def _extract_dds_mips(img_payload: bytes, width: int, height: int, num_mips: int, fourcc: str) -> list[dict[str, Any]]:
    """Extract individual mip levels from DDS binary payload."""
    if len(img_payload) >= 4 and img_payload[:4] == b"DDS ":
        offset = 128
    elif len(img_payload) >= 8 and img_payload[4:8] == b"DDS ":
        offset = 132
    else:
        offset = 4 if len(img_payload) > 4 else 0

    block_size = 8 if fourcc == "DXT1" else 16
    mips = []
    w, h = width, height
    cur_offset = offset

    for _ in range(max(1, num_mips)):
        blocks_x = max(1, (w + 3) // 4)
        blocks_y = max(1, (h + 3) // 4)
        mip_bytes = blocks_x * blocks_y * block_size

        if cur_offset + mip_bytes <= len(img_payload):
            mip_data = img_payload[cur_offset:cur_offset + mip_bytes]
            mips.append({
                "width": w,
                "height": h,
                "data": base64.b64encode(mip_data).decode("ascii"),
            })
            cur_offset += mip_bytes
        else:
            break

        w = max(1, w // 2)
        h = max(1, h // 2)

    return mips


def decode_entity_meshes(data: bytes, shape_filter: str | None = None) -> dict[str, Any]:
    records, children = _records(data)
    
    # 1. Extract Textures (0x19000 -> 0x19006 -> 0x19002 or TEXTURE_DDS)
    textures: dict[str, dict[str, Any]] = {}
    for idx, record in enumerate(records):
        if record["type_id"] == TEXTURE:
            try:
                tname, _ = _p3d_string(record["payload"])
                tex_children = children.get(idx, [])
                
                # Check for 0x19006 child and nested 0x19002
                for cidx, ch in tex_children:
                    if ch["type_id"] == TEXTURE_IMAGE_SPEC:
                        _, off = _p3d_string(ch["payload"])
                        if off + 28 <= len(ch["payload"]):
                            _, w, h, _, mips_cnt, _ = struct.unpack_from("<6I", ch["payload"], off)
                            fourcc = ch["payload"][off+24:off+28].decode("latin-1", errors="ignore").strip("\x00") or "DXT5"
                            if fourcc not in ("DXT1", "DXT3", "DXT5"):
                                fourcc = "DXT5"
                            for _, gch in children.get(cidx, []):
                                if gch["type_id"] in (IMAGE_DATA, 0x19002):
                                    mips = _extract_dds_mips(gch["payload"], w, h, mips_cnt, fourcc)
                                    if mips:
                                        textures[tname] = {
                                            "key": tname,
                                            "format": fourcc,
                                            "mips": mips,
                                        }
                                        base_name = tname.rsplit(".", 1)[0]
                                        textures[base_name] = textures[tname]

                # Check direct TEXTURE_DDS header + IMAGE_DATA
                headers = [r for _, r in tex_children if r["type_id"] == TEXTURE_DDS]
                images = [r for _, r in tex_children if r["type_id"] == IMAGE_DATA]
                if headers and images:
                    _, w, h, mips_cnt, algo = _parse_texture_dds_header(headers[0]["payload"])
                    fourcc = algo if (isinstance(algo, str) and algo in ("DXT1", "DXT3", "DXT5")) else ("DXT1" if algo == 1 else "DXT5")
                    mips = _extract_dds_mips(images[0]["payload"], w, h, mips_cnt, fourcc)
                    if mips:
                        textures[tname] = {
                            "key": tname,
                            "format": fourcc,
                            "mips": mips,
                        }
                        base_name = tname.rsplit(".", 1)[0]
                        textures[base_name] = textures[tname]
            except Exception:
                pass

    # 2. Extract Shaders (0x11015 / 0x10003) -> Map Shader Name to Texture Name
    shader_to_texture: dict[str, str] = {}
    for idx, record in enumerate(records):
        if record["type_id"] in (NEW_SHADER, OLD_SHADER):
            try:
                sname, off = _p3d_string(record["payload"])
                color_tex = ""
                for _, ch in children.get(idx, []):
                    if ch["type_id"] in (TEXTURE_PARAM, 0x10008, 0x11005):
                        pname, poff = _p3d_string(ch["payload"])
                        pval, _ = _p3d_string(ch["payload"], poff)
                        if pname in ("color", "Base", "s_texture", "DIFFUSE", "COLOR", "g_txDiffuse", "g_BaseTexture"):
                            color_tex = pval
                            break
                        elif not color_tex and pval:
                            color_tex = pval
                if sname and color_tex:
                    shader_to_texture[sname] = color_tex
            except Exception:
                pass

    # 3. Extract Skeletons (Prioritize full body skeletons with highest joint count)
    skeletons = []
    for idx, record in enumerate(records):
        if record["type_id"] in (SKELETON_V1, SKELETON_V2):
            try:
                skel_name, _ = _p3d_string(record["payload"], 0)
                joints = []
                for _, child in children.get(idx, []):
                    if child["type_id"] in (SKELETON_JOINT, SKELETON_JOINT_V2):
                        j_payload = child["payload"]
                        j_name, offset = _p3d_string(j_payload, 0)
                        parent_idx = -1
                        matrix = [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]
                        if offset + 4 <= len(j_payload):
                            parent_idx = struct.unpack_from("<i", j_payload, offset)[0]
                        if offset + 68 <= len(j_payload):
                            m_floats = struct.unpack_from("<16f", j_payload, offset + 4)
                            matrix = [round(f, 4) for f in m_floats]
                        joints.append({
                            "name": j_name,
                            "parent": parent_idx,
                            "matrix": matrix,
                        })
                if joints:
                    skeletons.append({
                        "name": skel_name,
                        "joint_count": len(joints),
                        "joints": joints,
                    })
            except Exception:
                pass

    skeletons.sort(key=lambda s: s["joint_count"], reverse=True)

    # 4. Extract Animation Clips (0x121000)
    animations = []
    for idx, record in enumerate(records):
        if record["type_id"] == ANIMATION:
            try:
                p = record["payload"]
                anim_name, off = _p3d_string(p, 4) if len(p) > 4 else ("", 0)
                if not anim_name:
                    anim_name, off = _p3d_string(p, 0)
                if off + 12 <= len(p):
                    fourcc = p[off:off+4].decode("latin-1", errors="ignore").strip("\x00") or "PTRN"
                    num_frames, rate = struct.unpack_from("<2f", p, off + 4)
                    duration = round(num_frames / rate, 2) if rate > 0 else 0.0
                    animations.append({
                        "name": anim_name or f"Track_{len(animations) + 1}",
                        "type": fourcc,
                        "frames": round(num_frames),
                        "fps": round(rate, 1),
                        "duration": duration,
                    })
            except Exception:
                pass

    # Check for LOD0 meshes
    all_geom_names: list[str] = []
    for idx, record in enumerate(records):
        if record["type_id"] in (POLYSKIN, GEOMETRY):
            try:
                gn, _ = _p3d_string(record["payload"])
                all_geom_names.append(gn)
            except Exception:
                pass
    has_lod0 = any(n.endswith(("_00", "_LOD0", "_lod0")) for n in all_geom_names)

    # 5. Extract Meshes (Geometry / Polyskin)
    meshes = []
    for idx, record in enumerate(records):
        if record["type_id"] not in (POLYSKIN, GEOMETRY):
            continue
        try:
            geom_name, _ = _p3d_string(record["payload"])
        except Exception:
            continue
            
        # If shape filter is specified, only accept matching geometries
        if shape_filter:
            clean_shape = shape_filter.strip()
            if not (geom_name == clean_shape or geom_name.startswith(clean_shape) or clean_shape.startswith(geom_name)):
                continue
        elif has_lod0 and geom_name.endswith(("_11", "_21", "_31", "_LOD1", "_LOD2", "_lod1", "_lod2")):
            # Skip lower LOD models when high detail LOD0 exists
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
                
            pos_stride = v_bytes // vertex_count if (v_bytes % vertex_count == 0) else 56
            pos_buf = bytearray(vertex_count * 12)
            uv_buf = bytearray(vertex_count * 8)
            
            # 1. Unpack positions from Stream 0
            for i in range(vertex_count):
                if i * pos_stride + 12 <= len(v_body):
                    pos_buf[i * 12: i * 12 + 12] = v_body[i * pos_stride: i * pos_stride + 12]

            # 2. Resolve UV stream & offset
            uv_stream_idx = -1
            uv_offset = -1
            for didx, desc in enumerate(d_lists):
                if len(desc) >= 16:
                    for off in range(16, len(desc), 17):
                        if off + 17 <= len(desc):
                            shash, _, aoff, _, _, _ = struct.unpack_from("<IIIHHB", desc, off)
                            if shash == 0x00364509: # TEXCOORD0
                                uv_stream_idx = didx
                                uv_offset = aoff
                                break
                    if uv_stream_idx >= 0:
                        break

            if uv_stream_idx >= 0 and uv_stream_idx < len(v_lists):
                uv_vl = v_lists[uv_stream_idx]
                if len(uv_vl) >= 12:
                    uv_bytes = struct.unpack_from("<I", uv_vl, 8)[0]
                    uv_body = uv_vl[12:12 + uv_bytes]
                    uv_stride = uv_bytes // vertex_count if (v_bytes % vertex_count == 0) else (8 if uv_stream_idx > 0 else pos_stride)
                    for i in range(vertex_count):
                        src_idx = i * uv_stride + uv_offset
                        if src_idx + 8 <= len(uv_body):
                            uv_buf[i * 8: i * 8 + 8] = uv_body[src_idx: src_idx + 8]
            elif len(v_lists) > 1 and len(v_lists[1]) >= 12 + vertex_count * 8:
                # Direct Stream 1 fallback (8 bytes per vertex)
                uv_body = v_lists[1][12:]
                uv_buf[:vertex_count * 8] = uv_body[:vertex_count * 8]
            else:
                # Interleaved fallback on Stream 0
                uv_off = 16 if pos_stride in (52, 64, 80) else (20 if pos_stride in (56, 28) else 16)
                for i in range(vertex_count):
                    src_idx = i * pos_stride + uv_off
                    if src_idx + 8 <= len(v_body):
                        uv_buf[i * 8: i * 8 + 8] = v_body[src_idx: src_idx + 8]
                    
            # 3. Resolve Blend Weights & Skin Indices (POLYSKIN)
            palette = []
            for _, pch in pg_children:
                if pch["type_id"] in (0x0001000D, 0x0001000F):
                    p_data = pch["payload"]
                    p_cnt = len(p_data) // 4
                    palette = list(struct.unpack_from("<%dI" % p_cnt, p_data, 0))
                    break

            weight_off = -1
            index_off = -1
            for desc in d_lists:
                if len(desc) >= 16:
                    for off in range(16, len(desc), 17):
                        if off + 17 <= len(desc):
                            shash, _, aoff, _, _, _ = struct.unpack_from("<IIIHHB", desc, off)
                            if shash == 0xA4176245:  # BLENDWEIGHT
                                weight_off = aoff
                            elif shash == 0x73D5CBA7:  # BLENDINDICES
                                index_off = aoff

            skin_indices_b64 = None
            skin_weights_b64 = None

            if weight_off >= 0 and index_off >= 0:
                skin_indices_buf = bytearray(vertex_count * 8)
                skin_weights_buf = bytearray(vertex_count * 16)
                for i in range(vertex_count):
                    src_w = i * pos_stride + weight_off
                    src_i = i * pos_stride + index_off
                    if src_w + 16 <= len(v_body) and src_i + 4 <= len(v_body):
                        raw_w = struct.unpack_from("<4f", v_body, src_w)
                        raw_i = struct.unpack_from("<4B", v_body, src_i)
                        valid_w = [max(0.0, float(w)) for w in raw_w]
                        sum_w = sum(valid_w)
                        norm_w = [w / sum_w if sum_w > 0 else (1.0 if k == 0 else 0.0) for k, w in enumerate(valid_w)]
                        mapped_i = [palette[b] if b < len(palette) else b for b in raw_i]
                        struct.pack_into("<4H", skin_indices_buf, i * 8, *mapped_i)
                        struct.pack_into("<4f", skin_weights_buf, i * 16, *norm_w)
                skin_indices_b64 = base64.b64encode(skin_indices_buf).decode("ascii")
                skin_weights_b64 = base64.b64encode(skin_weights_buf).decode("ascii")

            i_bytes = struct.unpack_from("<I", il_payload, 8)[0]
            i_body = il_payload[12:12 + i_bytes]
            
            # Resolve texture key
            tex_name = shader_to_texture.get(shader_name)
            tex_key = None
            if tex_name and tex_name in textures:
                tex_key = textures[tex_name]["key"]
            elif tex_name and tex_name.rsplit(".", 1)[0] in textures:
                tex_key = textures[tex_name.rsplit(".", 1)[0]]["key"]
            elif textures:
                tex_key = list(textures.values())[0]["key"]

            mesh_dict = {
                "geometry_name": geom_name,
                "shader_name": shader_name,
                "vertex_count": vertex_count,
                "triangle_count": len(i_body) // 6,
                "positions": base64.b64encode(pos_buf).decode("ascii"),
                "uv": base64.b64encode(uv_buf).decode("ascii"),
                "indices": base64.b64encode(i_body).decode("ascii"),
                "texture_key": tex_key,
            }
            if skin_indices_b64 and skin_weights_b64:
                mesh_dict["skin_indices"] = skin_indices_b64
                mesh_dict["skin_weights"] = skin_weights_b64
            meshes.append(mesh_dict)

    # Return unique texture objects
    unique_textures = []
    seen_keys = set()
    for tex in textures.values():
        if tex["key"] not in seen_keys:
            seen_keys.add(tex["key"])
            unique_textures.append(tex)

    return {
        "meshes": meshes,
        "textures": unique_textures,
        "skeletons": skeletons,
        "animations": animations,
    }
