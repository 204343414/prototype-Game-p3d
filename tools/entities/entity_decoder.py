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

# Pure3D Chunk IDs
TEXTURE_ALT = 0x00019000
TEXTURE_DDS = 0x00019006
IMAGE_DATA_ALT = 0x00019002
IMAGE_DATA_ALT2 = 0x00019007

TEXTURE = 0x00010000
IMAGE = 0x00010004
IMAGE_DATA = 0x00010005
NEW_SHADER = 0x00011000
OLD_SHADER = 0x00010002
TEXTURE_PARAM = 0x00011004

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
MATRIX_PALETTE_ALT = 0x0001000F

SKELETON_2 = 0x00023000
SKELETON_JOINT_2 = 0x00023001

ANIMATION = 0x00121000
ANIM_ZLIB_BLOB = 0x02F00000
ANIM_GROUP_LIST = 0x00121002
ANIM_GROUP = 0x00121001
ANIM_ROT_INT16 = 0x00121112
ANIM_ROT_INT8 = 0x00121114
ANIM_LOCATOR = 0x00121120


def _align4(n: int) -> int:
    return (n + 3) & ~3


def _extract_dds_mips(img_payload: bytes, width: int, height: int, num_mips: int, fourcc: str) -> list[dict[str, Any]]:
    mips = []
    block_size = 16 if fourcc in ("DXT3", "DXT5") else 8
    curr_w, curr_h = width, height
    pos = 0
    for _ in range(num_mips):
        blocks_x = max(1, (curr_w + 3) // 4)
        blocks_y = max(1, (curr_h + 3) // 4)
        mip_size = blocks_x * blocks_y * block_size
        if pos + mip_size <= len(img_payload):
            mips.append({
                "data": base64.b64encode(img_payload[pos:pos + mip_size]).decode("ascii"),
                "width": curr_w,
                "height": curr_h,
            })
            pos += mip_size
        curr_w = max(1, curr_w // 2)
        curr_h = max(1, curr_h // 2)
    return mips


def decode_entity_meshes(data: bytes, shape_filter: str | None = None) -> dict[str, Any]:
    """Decodes all meshes, skeletons, skin weights, and real animation tracks from a Pure3D payload."""
    if len(data) < 12:
        return {"meshes": [], "textures": [], "skeletons": [], "animations": []}

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

    # 1. Extract Textures & Shaders
    textures = {}
    for idx, record in enumerate(records):
        if record["type_id"] in (TEXTURE, TEXTURE_ALT):
            try:
                name, _ = _p3d_string(record["payload"], 0)
                tex_w, tex_h, bpp, num_mips = 0, 0, 0, 0
                fourcc = "DXT1"
                raw_data = b""

                for ch_idx in children_map.get(idx, []):
                    ch = records[ch_idx]
                    if ch["type_id"] in (IMAGE, TEXTURE_DDS):
                        p = ch["payload"]
                        _, poff = _p3d_string(p, 0)
                        if poff + 16 <= len(p):
                            tex_w, tex_h, bpp, _, num_mips, ftype = struct.unpack_from("<IIIIII", p, poff)
                            if ftype == 2:
                                fourcc = "DXT3"
                            elif ftype == 3:
                                fourcc = "DXT5"
                            elif len(p) >= poff + 28:
                                # FourCC may follow
                                fcc = p[poff + 24:poff + 28].decode("ascii", errors="ignore").rstrip("\x00")
                                if fcc in ("DXT1", "DXT3", "DXT5"):
                                    fourcc = fcc
                    elif ch["type_id"] in (IMAGE_DATA, IMAGE_DATA_ALT, IMAGE_DATA_ALT2):
                        p = ch["payload"]
                        if p.startswith(b"DDS "):
                            raw_data = p[128:]  # skip standard DDS 128-byte header
                            if tex_w == 0 and len(p) >= 20:
                                tex_h, tex_w = struct.unpack_from("<II", p, 12)
                        elif len(p) >= 4:
                            d_len = struct.unpack_from("<I", p, 0)[0]
                            raw_data = p[4:4 + d_len] if d_len + 4 <= len(p) else p[4:]

                if name and raw_data:
                    tex_w = tex_w or 4
                    tex_h = tex_h or 4
                    mips = _extract_dds_mips(raw_data, tex_w, tex_h, num_mips or 1, fourcc)
                    textures[name] = {
                        "key": f"entity_tex_{name}",
                        "name": name,
                        "format": fourcc,
                        "fourcc": fourcc,
                        "width": tex_w,
                        "height": tex_h,
                        "mipmaps": mips,
                        "is_transparent": fourcc in ("DXT3", "DXT5"),
                    }
            except Exception:
                pass

    shader_to_texture = {}
    for idx, record in enumerate(records):
        if record["type_id"] in (NEW_SHADER, OLD_SHADER):
            try:
                sname, off = _p3d_string(record["payload"])
                color_tex = ""
                for ch_idx in children_map.get(idx, []):
                    ch = records[ch_idx]
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

    # 2. Extract Skeleton_2 (0x00023000)
    skeletons = []
    for idx, record in enumerate(records):
        if record["type_id"] == SKELETON_2:
            try:
                p = record["payload"]
                skel_name, off = _p3d_string(p, 0)
                ver, num_joints, num_part, num_limbs = struct.unpack_from("<IIII", p, off)
                joints = []
                for ch_idx in children_map.get(idx, []):
                    child = records[ch_idx]
                    if child["type_id"] == SKELETON_JOINT_2:
                        jp = child["payload"]
                        jname, joff = _p3d_string(jp, 0)
                        pidx = struct.unpack_from("<I", jp, joff)[0]
                        m_floats = struct.unpack_from("<16f", jp, joff + 4)
                        joints.append({
                            "name": jname,
                            "parent": pidx,
                            "matrix": [round(f, 6) for f in m_floats],
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

    # 3. Extract External ZLIB Animation Clips (0x00121000)
    animations = []
    for idx, record in enumerate(records):
        if record["type_id"] == ANIMATION:
            try:
                p = record["payload"]
                ver = struct.unpack_from("<I", p, 0)[0]
                anim_name, off = _p3d_string(p, 4)
                fourcc = p[off:off + 4].decode("ascii", errors="ignore")
                num_frames, frame_rate, cyclic = struct.unpack_from("<ffI", p, off + 4)

                zlib_blob = None
                group_list_idx = None
                for ch_idx in children_map.get(idx, []):
                    ch = records[ch_idx]
                    if ch["type_id"] == ANIM_ZLIB_BLOB:
                        z_payload = ch["payload"]
                        if len(z_payload) >= 16:
                            uncomp_sz, comp_sz = struct.unpack_from("<II", z_payload, 8)
                            raw_zlib = z_payload[16 : 16 + comp_sz]
                            zlib_blob = zlib.decompress(raw_zlib)
                    elif ch["type_id"] == ANIM_GROUP_LIST:
                        group_list_idx = ch_idx

                if zlib_blob and group_list_idx is not None:
                    groups = []
                    fps = frame_rate if frame_rate > 0 else 30.0

                    for g_idx in children_map.get(group_list_idx, []):
                        g_rec = records[g_idx]
                        if g_rec["type_id"] == ANIM_GROUP:
                            gp = g_rec["payload"]
                            gname, goff = _p3d_string(gp, 4)
                            gid, n_channels = struct.unpack_from("<II", gp, goff)

                            rot_track = None
                            for c_idx in children_map.get(g_idx, []):
                                c_ch = records[c_idx]
                                cid = c_ch["type_id"]
                                loc_key_count = 0
                                loc_blob_offset = 0
                                for l_idx in children_map.get(c_idx, []):
                                    l_ch = records[l_idx]
                                    if l_ch["type_id"] == ANIM_LOCATOR:
                                        lp = l_ch["payload"]
                                        if len(lp) >= 12:
                                            _, loc_key_count, loc_blob_offset = struct.unpack_from("<III", lp, 0)

                                if loc_key_count > 0:
                                    times = []
                                    quats = []
                                    frame_bytes = _align4(loc_key_count * 2)
                                    val_offset = loc_blob_offset + frame_bytes

                                    if loc_blob_offset + frame_bytes <= len(zlib_blob):
                                        raw_frames = struct.unpack_from(f"<{loc_key_count}H", zlib_blob, loc_blob_offset)
                                        times = [round(f / fps, 4) for f in raw_frames]

                                    if cid == ANIM_ROT_INT16 and val_offset + loc_key_count * 6 <= len(zlib_blob):
                                        raw_vals = struct.unpack_from(f"<{loc_key_count * 3}h", zlib_blob, val_offset)
                                        for k in range(loc_key_count):
                                            rx = raw_vals[k * 3 + 0] / 32767.0
                                            ry = raw_vals[k * 3 + 1] / 32767.0
                                            rz = raw_vals[k * 3 + 2] / 32767.0
                                            w2 = max(0.0, 1.0 - rx * rx - ry * ry - rz * rz)
                                            rw = math.sqrt(w2)
                                            quats.append([round(rx, 5), round(ry, 5), round(rz, 5), round(rw, 5)])
                                    elif cid == ANIM_ROT_INT8 and val_offset + loc_key_count * 3 <= len(zlib_blob):
                                        raw_vals = struct.unpack_from(f"<{loc_key_count * 3}b", zlib_blob, val_offset)
                                        for k in range(loc_key_count):
                                            rx = raw_vals[k * 3 + 0] / 127.0
                                            ry = raw_vals[k * 3 + 1] / 127.0
                                            rz = raw_vals[k * 3 + 2] / 127.0
                                            w2 = max(0.0, 1.0 - rx * rx - ry * ry - rz * rz)
                                            rw = math.sqrt(w2)
                                            quats.append([round(rx, 5), round(ry, 5), round(rz, 5), round(rw, 5)])

                                    if times and quats and len(times) == len(quats):
                                        rot_track = {
                                            "times": times,
                                            "values": quats,
                                        }
                                        break

                            if rot_track:
                                groups.append({
                                    "name": gname,
                                    "rot": rot_track,
                                })

                    if groups:
                        animations.append({
                            "name": anim_name,
                            "type": fourcc,
                            "frames": int(num_frames),
                            "fps": round(fps, 2),
                            "duration": round(num_frames / fps, 3),
                            "cyclic": bool(cyclic),
                            "groups": groups,
                        })
            except Exception:
                pass

    # 4. Extract PolySkin & Geometries
    skin_groups = []
    for idx, record in enumerate(records):
        if record["type_id"] in (POLYSKIN, GEOMETRY, 0x00012000):
            try:
                geom_name, _ = _p3d_string(record["payload"])
                for ch_idx in children_map.get(idx, []):
                    ch = records[ch_idx]
                    if ch["type_id"] == PRIMITIVE_GROUP:
                        skin_groups.append((geom_name, ch_idx, ch))
            except Exception:
                pass

    decoded_meshes = []

    for geom_name, pg_idx, pg_rec in skin_groups:
        if shape_filter and shape_filter not in geom_name:
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

        # Check Matrix Palette
        pal_lists = [c["payload"] for c in pg_children if c["type_id"] in (MATRIX_PALETTE, MATRIX_PALETTE_ALT)]
        palette = []
        if pal_lists:
            p_data = pal_lists[0]
            p_cnt = len(p_data) // 4
            palette = list(struct.unpack_from(f"<{p_cnt}I", p_data, 0))

        pos_buf = bytearray(vertex_count * 12)
        uv_buf = bytearray(vertex_count * 8)
        i_body = b""
        skin_indices_b64 = None
        skin_weights_b64 = None

        if memory_imaged == 1:
            # Memory Imaged Path (Stream 0 + Stream 1)
            v_lists = [c["payload"] for c in pg_children if c["type_id"] == VERTEX_LIST]
            i_lists = [c["payload"] for c in pg_children if c["type_id"] == INDEX_LIST]
            if not v_lists or not i_lists:
                continue

            v_body = v_lists[0][12:]
            pos_stride = len(v_body) // vertex_count if vertex_count > 0 else 56

            for i in range(vertex_count):
                src_idx = i * pos_stride
                if src_idx + 12 <= len(v_body):
                    pos_buf[i * 12 : i * 12 + 12] = v_body[src_idx : src_idx + 12]

            if len(v_lists) > 1 and len(v_lists[1]) >= 12 + vertex_count * 8:
                uv_body = v_lists[1][12:]
                uv_stride = len(uv_body) // vertex_count
                for i in range(vertex_count):
                    src_u = i * uv_stride
                    if src_u + 8 <= len(uv_body):
                        uv_buf[i * 8 : i * 8 + 8] = uv_body[src_u : src_u + 8]
            else:
                uv_off = 16 if pos_stride in (52, 64, 80) else (20 if pos_stride in (56, 28) else 16)
                for i in range(vertex_count):
                    src_idx = i * pos_stride + uv_off
                    if src_idx + 8 <= len(v_body):
                        uv_buf[i * 8 : i * 8 + 8] = v_body[src_idx : src_idx + 8]

            if pos_stride == 56 and len(v_body) >= vertex_count * 56 and palette:
                skin_indices_buf = bytearray(vertex_count * 8)
                skin_weights_buf = bytearray(vertex_count * 16)
                for i in range(vertex_count):
                    off = i * 56
                    w0, w1, w2 = struct.unpack_from("<3f", v_body, off + 40)
                    w3 = max(0.0, 1.0 - (w0 + w1 + w2))
                    raw_weights = [w0, w1, w2, w3]
                    sum_w = sum(raw_weights)
                    norm_w = [w / sum_w if sum_w > 0 else (1.0 if k == 0 else 0.0) for k, w in enumerate(raw_weights)]
                    b0, b1, b2, b3 = struct.unpack_from("<4B", v_body, off + 52)
                    mapped_i = [palette[b] if b < len(palette) else b for b in (b0, b1, b2, b3)]
                    struct.pack_into("<4H", skin_indices_buf, i * 8, *mapped_i)
                    struct.pack_into("<4f", skin_weights_buf, i * 16, *norm_w)

                skin_indices_b64 = base64.b64encode(skin_indices_buf).decode("ascii")
                skin_weights_b64 = base64.b64encode(skin_weights_buf).decode("ascii")

            il_payload = i_lists[0]
            if len(il_payload) >= 12:
                i_bytes = struct.unpack_from("<I", il_payload, 8)[0]
                i_body = il_payload[12:12 + i_bytes]

        else:
            # Legacy Memory Imaged == 0 Path (Alex jacket / AlexVestShape)
            pos_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_POSITIONS]
            uv_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_UVS]
            idx_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_INDICES]
            mat_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_MATRICES]
            wt_chunks = [c["payload"] for c in pg_children if c["type_id"] == LEGACY_WEIGHTS]

            if pos_chunks and len(pos_chunks[0]) >= 4 + vertex_count * 12:
                pos_buf[:vertex_count * 12] = pos_chunks[0][4 : 4 + vertex_count * 12]

            if uv_chunks and len(uv_chunks[0]) >= 4 + vertex_count * 8:
                uv_buf[:vertex_count * 8] = uv_chunks[0][4 : 4 + vertex_count * 8]

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

                for i in range(vertex_count):
                    if i * 4 + 4 <= len(m_body) and i * 12 + 12 <= len(w_body):
                        b0, b1, b2, b3 = struct.unpack_from("<4B", m_body, i * 4)
                        w0, w1, w2 = struct.unpack_from("<3f", w_body, i * 12)
                        implicit = max(0.0, 1.0 - (w0 + w1 + w2))
                        raw_weights = [implicit, w2, w0, w1]
                        sum_w = sum(raw_weights)
                        norm_w = [w / sum_w if sum_w > 0 else (1.0 if k == 0 else 0.0) for k, w in enumerate(raw_weights)]
                        mapped_i = [palette[b] if b < len(palette) else b for b in (b0, b1, b2, b3)]
                        struct.pack_into("<4H", skin_indices_buf, i * 8, *mapped_i)
                        struct.pack_into("<4f", skin_weights_buf, i * 16, *norm_w)

                skin_indices_b64 = base64.b64encode(skin_indices_buf).decode("ascii")
                skin_weights_b64 = base64.b64encode(skin_weights_buf).decode("ascii")

        if len(i_body) < 6:
            continue

        tex_name = shader_to_texture.get(shader_name)
        tex_key = None
        if tex_name and tex_name in textures:
            tex_key = textures[tex_name]["key"]

        decoded_meshes.append({
            "geometry_name": geom_name,
            "shader_name": shader_name,
            "texture_key": tex_key,
            "vertex_count": vertex_count,
            "triangle_count": len(i_body) // 6,
            "positions": base64.b64encode(pos_buf).decode("ascii"),
            "uv": base64.b64encode(uv_buf).decode("ascii"),
            "indices": base64.b64encode(i_body).decode("ascii"),
            "skin_indices": skin_indices_b64,
            "skin_weights": skin_weights_b64,
        })

    return {
        "meshes": decoded_meshes,
        "textures": list(textures.values()),
        "skeletons": skeletons,
        "animations": animations,
    }
