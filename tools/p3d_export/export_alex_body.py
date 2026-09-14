#!/usr/bin/env python3
"""
Alex Mercer (alex_reg_body 部位组: 身体+头+外套) -> glTF (.glb) 一次性导出脚本。

数据格式依据: /home/user/prototype-p3d-toolkit/docs/vertex_format.md
第1-9节 (几何/骨骼) + 第8/8b/8c节 (Skin绑定/总装配图/材质贴图关联)。

用法:
    python3 export_alex_body.py --base-url https://xxxx.trycloudflare.com \
        --rcf-path "/mnt/hdd/新建文件夹/steamapps/common/Prototype/art.rcf" \
        --entry-name "\\art\\alex\\alex.p3d.rz" \
        --out alex_reg_body.glb

设计取舍 (第一版, 求"能看", 非最终生产质量):
    - 只导出 alex_reg_body 这一个 Composite_Drawable_2 组 (身体+头+外套三个 Skin,
      共享 alex_reg_body_skeleton)，不含手臂/武器/特效模型 —— 那些用独立骨架，
      留给后续版本按同样的模式扩展。
    - 骨骼绑定姿势 (bind pose) 直接取 Skeleton_Joint_2.rest_pose 级联算出的世界矩阵，
      不做任何姿势动画 —— 就是"T-pose/A-pose静止姿势"预览，符合当前需求。
    - 贴图: 只导出 color(baseColor) 贴图，转 PNG 内嵌进 glb；normal/specular
      贴图的转码逻辑已写好但先保留在代码里、默认关闭，避免第一版复杂度过高
      （可用 --with-normal-map 打开）。
"""
import argparse
import json
import struct
import urllib.parse
import urllib.request
import io
import sys

import numpy as np

try:
    import texture2ddecoder
except ImportError:
    texture2ddecoder = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import pygltflib
    from pygltflib import (
        GLTF2, Asset, Scene, Node, Mesh, Primitive, Attributes, Buffer,
        BufferView, Accessor, Skin, Material, PbrMetallicRoughness,
        TextureInfo, Texture as GLTFTexture, Image as GLTFImage, Sampler,
    )
except ImportError:
    print("请先 pip install pygltflib", file=sys.stderr)
    raise


# ---------------------------------------------------------------------------
# 1. 底层: 通过 viewer server 的 /api/rcf_entry 拉取 chunk 树 / 原始字节
# ---------------------------------------------------------------------------

class P3DSource:
    """重要: 这里**永远不传 max_depth 参数**。

    /api/rcf_entry 的 max_depth 是作用于"整个文件"从头开始的递归遍历深度上限
    (绝对深度，不是相对某个 chunk 的相对深度)。不同 max_depth 取值会导致
    dump_chunk() 输出的扁平列表元素个数不同 (更深的子孙节点被整体跳过)，从而
    导致同一个物理 chunk 在两次不同 max_depth 的调用里 global_index 编号不同。
    一旦跨调用混用不同 max_depth 拿到的 global_index 去做 offset 查询，会指向
    完全错误的 chunk。因此这里统一策略: 每次调用都用完整无限制深度遍历
    (不传 max_depth)，保证 global_index 在所有调用之间语义一致、可安全跨调用
    引用；要限定"某个 chunk 的子树"范围，用返回的绝对 depth 字段自己裁剪
    (见 get_subtree())，而不是依赖服务端的 max_depth 截断。
    """

    def __init__(self, base_url: str, rcf_path: str, entry_name: str):
        self.base_url = base_url.rstrip("/")
        self.rcf_path = rcf_path
        self.entry_name = entry_name

    def _api(self, params: dict) -> dict:
        qs = urllib.parse.urlencode(params)
        url = f"{self.base_url}/api/rcf_entry?{qs}"
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.loads(r.read())

    def chunks(self, offset=0, limit=500, type_filter=None,
               payload_preview=64) -> list:
        params = {
            "path": self.rcf_path,
            "name": self.entry_name,
            "offset": offset,
            "limit": limit,
            "payload_preview": payload_preview,
        }
        if type_filter is not None:
            params["type_filter"] = type_filter
        d = self._api(params)
        return d["chunks"]

    def chunk_at(self, global_index: int, payload_preview=64) -> dict:
        cs = self.chunks(offset=global_index, limit=1, payload_preview=payload_preview)
        assert cs and cs[0]["global_index"] == global_index, \
            f"expected chunk at {global_index}, got {cs}"
        return cs[0]

    def get_subtree(self, global_index: int, target_depth: int, max_items=8000,
                     payload_preview=64) -> list:
        """按服务端文档约定的边界规则拉取一个 chunk 的完整子树 (含自身):
        "a chunk's entire subtree is the contiguous run starting at its
        global_index and ending just before the next sibling/ancestor at
        depth <= target_depth"。target_depth 必须是调用方已知的、该 chunk
        自身的绝对 depth (从一次不受限的普通查询里读到的)。"""
        result = []
        offset = global_index
        got_root = False
        remaining = max_items
        while remaining > 0:
            batch = self.chunks(offset=offset, limit=min(remaining, 2000),
                                 payload_preview=payload_preview)
            if not batch:
                break
            stop = False
            for c in batch:
                if not got_root:
                    assert c["global_index"] == global_index, \
                        f"expected root {global_index}, got {c['global_index']}"
                    result.append(c)
                    got_root = True
                    continue
                if c["depth"] <= target_depth:
                    stop = True
                    break
                result.append(c)
            if stop:
                break
            offset = batch[-1]["global_index"] + 1
            remaining -= len(batch)
        return result

    def find_by_type(self, type_id_hex: str, payload_preview=64, limit=2000) -> list:
        return self.chunks(offset=0, limit=limit, type_filter=type_id_hex,
                            payload_preview=payload_preview)


# ---------------------------------------------------------------------------
# 2. P3D 基础类型解析 helper
# ---------------------------------------------------------------------------

def read_p3dstring(b: bytes, i: int):
    ln = b[i]
    i += 1
    raw = b[i:i + ln]
    i += ln
    s = raw.split(b"\x00")[0].decode("utf-8", "replace")
    return s, i


def u32(b, i):
    return struct.unpack_from("<I", b, i)[0], i + 4


def f32(b, i):
    return struct.unpack_from("<f", b, i)[0], i + 4


# ---------------------------------------------------------------------------
# 3. 各 chunk 类型的具体解析函数 (对照 docs/vertex_format.md)
# ---------------------------------------------------------------------------

def parse_skin(payload: bytes):
    """0x00010001 Skin -- 见 vertex_format.md 第8节"""
    i = 0
    name, i = read_p3dstring(payload, i)
    version, i = u32(payload, i)
    skeleton_name, i = read_p3dstring(payload, i)
    num_pg, i = u32(payload, i)
    return dict(name=name, version=version, skeleton_name=skeleton_name,
                num_primitive_groups=num_pg)


def parse_composite_drawable_2(payload: bytes):
    """0x00123000 -- 见 vertex_format.md 第8b节"""
    i = 0
    version, i = u32(payload, i)
    name, i = read_p3dstring(payload, i)
    skeleton_name, i = read_p3dstring(payload, i)
    num_prim, i = u32(payload, i)
    return dict(version=version, name=name, skeleton_name=skeleton_name,
                num_primitives=num_prim)


def parse_composite_drawable_primitive(payload: bytes):
    """0x00123001 -- 见 vertex_format.md 第8b节"""
    i = 0
    version, i = u32(payload, i)
    create_instance, i = u32(payload, i)
    name, i = read_p3dstring(payload, i)
    typ, i = u32(payload, i)
    joint_id, i = u32(payload, i)
    return dict(version=version, create_instance=create_instance, name=name,
                type=typ, skeleton_joint_id=joint_id)


def parse_primitive_group_header(payload: bytes):
    """0x00010020 -- 见 vertex_format.md 第1节"""
    i = 0
    version, i = u32(payload, i)
    shader_name, i = read_p3dstring(payload, i)
    primitive_type, i = u32(payload, i)
    vertex_type, i = u32(payload, i)
    num_vertices, i = u32(payload, i)
    num_indices, i = u32(payload, i)
    num_matrices, i = u32(payload, i)
    memory_imaged, i = u32(payload, i)
    optimized, i = u32(payload, i)
    vertex_animated, i = u32(payload, i)
    vertex_animation_mask, i = u32(payload, i)
    return dict(version=version, shader_name=shader_name,
                primitive_type=primitive_type, vertex_type=vertex_type,
                num_vertices=num_vertices, num_indices=num_indices,
                num_matrices=num_matrices, memory_imaged=memory_imaged,
                optimized=optimized, vertex_animated=vertex_animated,
                vertex_animation_mask=vertex_animation_mask)


def parse_vertex_buffer_56(payload: bytes, num_vertices: int):
    """field_c=2 变体, 56字节/顶点, 见第3a节. payload 已跳过12字节通用头。"""
    assert len(payload) == 56 * num_vertices, (len(payload), num_vertices)
    dt = np.dtype([
        ("position", "<f4", 3),
        ("normal", "<f4", 3),
        ("tangent", "<f4", 3),
        ("tangent_w", "<f4"),
        ("blend_weight_123", "<f4", 3),
        ("blend_index", "u1", 4),
    ])
    arr = np.frombuffer(payload, dtype=dt, count=num_vertices)
    return arr


def parse_vertex_buffer_12(payload: bytes, num_vertices: int):
    """field_c=1 变体, 12字节/顶点 (颜色+UV), 见第3b节。"""
    assert len(payload) == 12 * num_vertices, (len(payload), num_vertices)
    dt = np.dtype([
        ("color", "<u4"),
        ("uv", "<f4", 2),
    ])
    arr = np.frombuffer(payload, dtype=dt, count=num_vertices)
    return arr


def parse_count_prefixed_f32_array(payload: bytes, ncomp: int):
    """通用: 4字节count前缀 + count个float[ncomp]，用于 Position/Normal/Weight/
    UV(不含channel字段)/Tangent(0x10028) 等 List 类型。"""
    cnt, = struct.unpack_from("<I", payload, 0)
    arr = np.frombuffer(payload, dtype="<f4", count=cnt * ncomp, offset=4)
    return arr.reshape(cnt, ncomp)


def parse_uv_list(payload: bytes):
    """0x00010007 UV_List: count(u32) + channel(u32) + count个Vector2"""
    cnt, = struct.unpack_from("<I", payload, 0)
    channel, = struct.unpack_from("<I", payload, 4)
    arr = np.frombuffer(payload, dtype="<f4", count=cnt * 2, offset=8)
    return arr.reshape(cnt, 2), channel


def parse_colour_list(payload: bytes):
    """0x00010008 Colour_List: count(u32) + count个uint32 (打包RGBA/BGRA)"""
    cnt, = struct.unpack_from("<I", payload, 0)
    arr = np.frombuffer(payload, dtype="<u4", count=cnt, offset=4)
    return arr


def parse_blend_index_list(payload: bytes):
    """0x0001000B (netp3dlib 命名为 Matrix_List, 但在本模型里实际承载的是每顶点
    4字节 blend_index abcd，用途/取值范围经实测与顶点blend_index字段完全一致):
    count(u32) + count个 byte[4]"""
    cnt, = struct.unpack_from("<I", payload, 0)
    arr = np.frombuffer(payload, dtype="u1", count=cnt * 4, offset=4)
    return arr.reshape(cnt, 4)


def parse_index_list_u32(payload: bytes):
    """0x0001000A Index_List: count(u32) + count个 uint32 索引 (与 Memory_Image
    版本的16位索引不同，这里是32位)。"""
    cnt, = struct.unpack_from("<I", payload, 0)
    arr = np.frombuffer(payload, dtype="<u4", count=cnt, offset=4)
    return arr


def parse_matrix_palette(payload: bytes):
    """0x0001000D -- 见第5节"""
    count, = struct.unpack_from("<I", payload, 0)
    indices = np.frombuffer(payload, dtype="<u4", count=count, offset=4)
    return indices


def parse_skeleton_2_header(payload: bytes):
    i = 0
    name, i = read_p3dstring(payload, i)
    version, i = u32(payload, i)
    num_joints, i = u32(payload, i)
    num_partitions, i = u32(payload, i)
    num_limbs, i = u32(payload, i)
    return dict(name=name, version=version, num_joints=num_joints,
                num_partitions=num_partitions, num_limbs=num_limbs)


def parse_skeleton_joint_2(payload: bytes):
    i = 0
    name, i = read_p3dstring(payload, i)
    parent, i = u32(payload, i)
    mat_floats = struct.unpack_from("<16f", payload, i)
    i += 64
    # 剩余 54 字节 trailing, 忽略
    rest_pose = np.array(mat_floats, dtype=np.float64).reshape(4, 4)  # row-major
    return dict(name=name, parent=parent, rest_pose_row_major=rest_pose)


def parse_texture_header(payload: bytes):
    """0x00019000 -- 见第8c.1节"""
    i = 0
    name, i = read_p3dstring(payload, i)
    version, i = u32(payload, i)
    width, i = u32(payload, i)
    height, i = u32(payload, i)
    bpp, i = u32(payload, i)
    alpha_depth, i = u32(payload, i)
    num_mipmaps, i = u32(payload, i)
    texture_type, i = u32(payload, i)
    usage_hint, i = u32(payload, i)
    priority, i = u32(payload, i)
    return dict(name=name, version=version, width=width, height=height,
                bpp=bpp, alpha_depth=alpha_depth, num_mipmaps=num_mipmaps,
                texture_type=texture_type, usage_hint=usage_hint,
                priority=priority)


def parse_texture_dds_header(payload: bytes):
    """0x00019006 -- 见第8c.2节"""
    i = 0
    name, i = read_p3dstring(payload, i)
    version, i = u32(payload, i)
    width, i = u32(payload, i)
    height, i = u32(payload, i)
    unknown4, i = u32(payload, i)
    unknown5, i = u32(payload, i)
    num_mipmaps, i = u32(payload, i)
    algorithm = payload[i:i + 4]
    i += 4
    return dict(name=name, version=version, width=width, height=height,
                unknown4=unknown4, unknown5=unknown5, num_mipmaps=num_mipmaps,
                algorithm=algorithm.decode("ascii", "replace"))


def parse_new_shader_header(payload: bytes):
    """0x00011015 -- 见第8c.3节"""
    i = 0
    name, i = read_p3dstring(payload, i)
    unknown2, i = u32(payload, i)
    shader_template, i = read_p3dstring(payload, i)
    unknown4, i = u32(payload, i)
    return dict(name=name, unknown2=unknown2, shader_template=shader_template,
                unknown4=unknown4)


def parse_texture_param(payload: bytes):
    """0x00011016"""
    i = 0
    name, i = read_p3dstring(payload, i)
    value, i = read_p3dstring(payload, i)
    return name, value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--rcf-path", required=True)
    ap.add_argument("--entry-name", required=True)
    ap.add_argument("--group-name", default="alex_reg_body",
                    help="要导出的 Composite_Drawable_2 部位组名 (默认 alex_reg_body)")
    ap.add_argument("--out", default="alex_reg_body.glb")
    ap.add_argument("--with-normal-map", action="store_true")
    args = ap.parse_args()

    src = P3DSource(args.base_url, args.rcf_path, args.entry_name)
    print(f"[1/6] 定位 Composite_Drawable_2 组 '{args.group_name}' ...")

    cd2_list = src.find_by_type("0x00123000", payload_preview=200)
    target_cd2 = None
    for c in cd2_list:
        b = bytes.fromhex(c["payload_hex_preview"])
        info = parse_composite_drawable_2(b)
        if info["name"] == args.group_name:
            target_cd2 = (c, info)
            break
    if target_cd2 is None:
        names = [parse_composite_drawable_2(bytes.fromhex(c["payload_hex_preview"]))["name"]
                 for c in cd2_list]
        raise SystemExit(f"没找到部位组 {args.group_name!r}，可用: {names}")

    cd2_chunk, cd2_info = target_cd2
    print(f"    找到: {cd2_info}")

    # 拉取该 CD2 的直接子节点 (Composite_Drawable_Primitive 列表)
    children = src.get_subtree(cd2_chunk["global_index"], target_depth=cd2_chunk["depth"],
                                payload_preview=200)
    prim_names = []
    for c in children:
        if c["global_index"] == cd2_chunk["global_index"]:
            continue
        if c["type_id"] != "0x00123001":
            continue
        b = bytes.fromhex(c["payload_hex_preview"])
        info = parse_composite_drawable_primitive(b)
        prim_names.append(info["name"])
    print(f"    该组下 {len(prim_names)} 个 Skin: {prim_names}")

    print(f"[2/6] 定位骨架 '{cd2_info['skeleton_name']}' 并解码骨骼层级 ...")
    skel_list = src.find_by_type("0x00023000", payload_preview=60)
    target_skel = None
    for c in skel_list:
        b = bytes.fromhex(c["payload_hex_preview"])
        i = 0
        name, i = read_p3dstring(b, i)
        if name == cd2_info["skeleton_name"]:
            target_skel = c
            break
    if target_skel is None:
        raise SystemExit(f"没找到骨架 {cd2_info['skeleton_name']!r}")

    skel_header_chunk = src.chunk_at(target_skel["global_index"], payload_preview=64)
    skel_header = parse_skeleton_2_header(bytes.fromhex(skel_header_chunk["payload_hex_preview"]))
    print(f"    骨架头: {skel_header}")

    # 骨骼节点是该 Skeleton_2 chunk 的直接子节点 (depth+1), 数量 = num_joints
    joint_chunks = src.get_subtree(target_skel["global_index"], target_depth=target_skel["depth"],
                                    payload_preview=200)
    joints = []
    for c in joint_chunks:
        if c["global_index"] == target_skel["global_index"]:
            continue
        if c["type_id"] != "0x00023001":
            continue
        b = bytes.fromhex(c["payload_hex_preview"])
        joints.append(parse_skeleton_joint_2(b))
    assert len(joints) == skel_header["num_joints"], (len(joints), skel_header["num_joints"])
    print(f"    解码出 {len(joints)} 根骨骼")

    # 级联算世界矩阵 (行主序, 行向量约定: world[i] = local[i] @ world[parent[i]])
    world_mats = [None] * len(joints)
    for idx, j in enumerate(joints):
        if idx == 0:
            world_mats[0] = j["rest_pose_row_major"]
        else:
            world_mats[idx] = j["rest_pose_row_major"] @ world_mats[j["parent"]]
    joint_names = [j["name"] for j in joints]
    print(f"    根骨骼: {joint_names[0]}, 骨骼名前10: {joint_names[:10]}")

    print(f"[3/6] 拉取每个 Skin 的几何数据 (顶点/索引/调色板) ...")
    skin_list = src.find_by_type("0x00010001", payload_preview=200)
    skins_info = {}
    for c in skin_list:
        b = bytes.fromhex(c["payload_hex_preview"])
        info = parse_skin(b)
        if info["name"] in prim_names:
            skins_info[info["name"]] = dict(chunk=c, info=info)

    meshes_data = []  # 每个元素: dict(name, positions, normals, tangents, uvs, colors, indices, joint_indices_local, weights, material_shader_name)
    for pname in prim_names:
        entry = skins_info.get(pname)
        if entry is None:
            print(f"    !! 警告: 没找到 Skin {pname}, 跳过")
            continue
        skin_chunk = entry["chunk"]
        gi = skin_chunk["global_index"]
        print(f"    -- Skin '{pname}' @ global_index={gi}")

        # 拉出该 Skin 完整子树，覆盖 PrimitiveGroup 及其全部子chunk
        sub = src.get_subtree(gi, target_depth=skin_chunk["depth"], payload_preview=8 * 1024 * 1024)
        by_idx = {c["global_index"]: c for c in sub}

        # 找到该 Skin 下第一个 (也是唯一一个, num_primitive_groups=1) PrimitiveGroup
        pg_chunk = None
        for c in sub:
            if c["global_index"] == gi:
                continue
            if c["type_id"] == "0x00010020":
                pg_chunk = c
                break
        assert pg_chunk is not None, f"Skin {pname} 下没找到 PrimitiveGroup"
        pg_header = parse_primitive_group_header(bytes.fromhex(pg_chunk["payload_hex_preview"]))
        print(f"       PrimitiveGroup: num_vertices={pg_header['num_vertices']} "
              f"num_indices={pg_header['num_indices']} num_matrices={pg_header['num_matrices']} "
              f"shader_name={pg_header['shader_name']}")

        pg_gi = pg_chunk["global_index"]
        # PG 的直接子节点 (depth = pg.depth+1 相对): 用 subtree 拉出来后按 global_index 顺序过滤
        pg_children = [c for c in sub if c["global_index"] > pg_gi and c["depth"] == pg_chunk["depth"] + 1]

        nv = pg_header["num_vertices"]

        if pg_header["memory_imaged"] == 1:
            # 见 vertex_format.md 第1-6节: 打包在 Memory_Image_Vertex_List 里
            vbuf_56 = None
            vbuf_12 = None
            idxbuf = None
            matrix_palette = None
            for c in pg_children:
                if c["type_id"] == "0x00010012":
                    raw = bytes.fromhex(c["payload_hex_preview"])
                    field_c, = struct.unpack_from("<I", raw, 4)
                    body = raw[12:]
                    if field_c == 2:
                        vbuf_56 = parse_vertex_buffer_56(body, nv)
                    elif field_c == 1:
                        vbuf_12 = parse_vertex_buffer_12(body, nv)
                elif c["type_id"] == "0x00010013":
                    raw = bytes.fromhex(c["payload_hex_preview"])
                    body = raw[12:]
                    idxbuf = np.frombuffer(body, dtype="<u2", count=pg_header["num_indices"])
                elif c["type_id"] == "0x0001000D":
                    raw = bytes.fromhex(c["payload_hex_preview"])
                    matrix_palette = parse_matrix_palette(raw)

            assert vbuf_56 is not None and vbuf_12 is not None and idxbuf is not None \
                and matrix_palette is not None, f"Skin {pname}: 缺子chunk (memory_imaged=1)"
            assert len(matrix_palette) == pg_header["num_matrices"]

            positions = vbuf_56["position"].astype(np.float32)
            normals = vbuf_56["normal"].astype(np.float32)
            blend_weight_123 = vbuf_56["blend_weight_123"].astype(np.float32)
            blend_index = vbuf_56["blend_index"].astype(np.uint8)
            uvs = vbuf_12["uv"].astype(np.float32)
            colors = vbuf_12["color"]
            indices = idxbuf.astype(np.uint32)

        else:
            # memory_imaged=0: 老式"独立 List chunk"存储方式 (本次在 AlexVestShape
            # 上首次发现，见 docs/vertex_format.md 新增章节)。字段拆分到多个独立
            # chunk 里，每个都是 "count(u32) + count个记录" 的通用格式。
            raw_by_type = {}
            for c in pg_children:
                raw_by_type.setdefault(c["type_id"], bytes.fromhex(c["payload_hex_preview"]))

            positions = parse_count_prefixed_f32_array(raw_by_type["0x00010005"], 3).astype(np.float32)
            normals = parse_count_prefixed_f32_array(raw_by_type["0x00010006"], 3).astype(np.float32)
            uvs, _channel = parse_uv_list(raw_by_type["0x00010007"])
            uvs = uvs.astype(np.float32)
            colors = parse_colour_list(raw_by_type["0x00010008"])
            blend_index_arr = parse_blend_index_list(raw_by_type["0x0001000B"])
            blend_weight_123 = parse_count_prefixed_f32_array(raw_by_type["0x0001000C"], 3).astype(np.float32)
            matrix_palette = parse_matrix_palette(raw_by_type["0x0001000D"])
            indices = parse_index_list_u32(raw_by_type["0x0001000A"]).astype(np.uint32)
            blend_index = blend_index_arr.astype(np.uint8)

            assert len(positions) == nv and len(normals) == nv and len(uvs) == nv \
                and len(colors) == nv and len(blend_index) == nv and len(blend_weight_123) == nv, \
                f"Skin {pname}: List长度与num_vertices不一致"
            assert len(matrix_palette) == pg_header["num_matrices"]

        meshes_data.append(dict(
            name=pname,
            positions=positions,
            normals=normals,
            blend_weight_123=blend_weight_123,
            blend_index=blend_index,
            uvs=uvs,
            colors=colors,
            indices=indices,
            matrix_palette=matrix_palette,
            shader_name=pg_header["shader_name"],
        ))

    print(f"[4/6] 解析材质/贴图 ...")
    newshader_list = src.find_by_type("0x00011015", payload_preview=200)
    newshader_by_name = {}
    for c in newshader_list:
        b = bytes.fromhex(c["payload_hex_preview"])
        info = parse_new_shader_header(b)
        newshader_by_name[info["name"]] = dict(chunk=c, info=info)

    texture_list = src.find_by_type("0x00019000", payload_preview=200)
    texture_by_name = {}
    for c in texture_list:
        b = bytes.fromhex(c["payload_hex_preview"])
        info = parse_texture_header(b)
        texture_by_name[info["name"]] = c

    mesh_textures = {}  # mesh name -> dict(color=png_bytes or None, normal=..., specular=...)
    for m in meshes_data:
        shname = m["shader_name"]
        ns = newshader_by_name.get(shname)
        color_tex = normal_tex = specular_tex = None
        if ns is not None:
            ns_gi = ns["chunk"]["global_index"]
            ns_children = src.get_subtree(ns_gi, target_depth=ns["chunk"]["depth"], payload_preview=200)
            for c in ns_children:
                if c["global_index"] == ns_gi or c["type_id"] != "0x00011016":
                    continue
                b = bytes.fromhex(c["payload_hex_preview"])
                pname, pval = parse_texture_param(b)
                if pname == "color" and pval:
                    color_tex = pval
                elif pname == "normal" and pval:
                    normal_tex = pval
                elif pname == "specular" and pval:
                    specular_tex = pval
        mesh_textures[m["name"]] = dict(color=color_tex, normal=normal_tex, specular=specular_tex)
        print(f"    Skin '{m['name']}' shader={shname!r} -> "
              f"color={color_tex} normal={normal_tex} specular={specular_tex}")

    def fetch_and_decode_dds(tex_name: str):
        """按贴图名找 Texture(0x19000) + TextureDDS(0x19006) + Image_Data(0x19002),
        解压 DXT 数据为 RGBA numpy 数组, 返回 (width, height, rgba_bytes) 或 None。"""
        tchunk = texture_by_name.get(tex_name)
        if tchunk is None:
            print(f"       !! 贴图 {tex_name} 未在 Texture 列表中找到")
            return None
        tgi = tchunk["global_index"]
        children = src.get_subtree(tgi, target_depth=tchunk["depth"], payload_preview=8 * 1024 * 1024)
        dds_chunk = None
        imgdata_chunk = None
        for c in children:
            if c["global_index"] == tgi:
                continue
            if c["type_id"] == "0x00019006" and dds_chunk is None:
                dds_chunk = c
            elif c["type_id"] == "0x00019002" and imgdata_chunk is None:
                imgdata_chunk = c
        if dds_chunk is None or imgdata_chunk is None:
            print(f"       !! {tex_name}: 缺 TextureDDS/Image_Data 子节点")
            return None
        dds_info = parse_texture_dds_header(bytes.fromhex(dds_chunk["payload_hex_preview"]))
        raw_full = bytes.fromhex(imgdata_chunk["payload_hex_preview"])
        w, h, algo = dds_info["width"], dds_info["height"], dds_info["algorithm"]

        # 重要修复: 0x00019002 (Image_Data) 的 payload 不是裸 DXT 字节流，而是
        # "4字节长度前缀 + 标准DDS文件头(DDS_MAGIC 4字节+DDS_HEADER 124字节)"
        # 共132字节头部 + 裸DXT压缩数据。之前误将头部一起喂给解码器导致花屏。
        # 详见 docs/vertex_format.md 第8c.2节修订说明。
        DDS_HEADER_TOTAL = 4 + 4 + 124
        if raw_full[4:8] != b"DDS ":
            print(f"       !! {tex_name}: Image_Data 开头不是预期的 DDS 头 "
                  f"(实际={raw_full[4:8]!r})，按裸DXT流回退处理")
            raw = raw_full
        else:
            raw = raw_full[DDS_HEADER_TOTAL:]

        if texture2ddecoder is None:
            print("       !! texture2ddecoder 未安装, 无法解码 DXT, 跳过贴图")
            return None
        if algo == "DXT1":
            rgba = texture2ddecoder.decode_bc1(raw, w, h)
        elif algo == "DXT3":
            rgba = texture2ddecoder.decode_bc2(raw, w, h)
        elif algo == "DXT5":
            rgba = texture2ddecoder.decode_bc3(raw, w, h)
        else:
            print(f"       !! 未知压缩算法 {algo}, 跳过")
            return None
        # texture2ddecoder 返回的是 BGRA byte order 的 bytes
        arr = np.frombuffer(rgba, dtype=np.uint8).reshape(h, w, 4).copy()
        arr = arr[:, :, [2, 1, 0, 3]]  # BGRA -> RGBA
        return w, h, arr

    print(f"[5/6] 解码+转码贴图为 PNG ...")
    texture_png_cache = {}  # tex_name -> png bytes

    def get_png_for(tex_name):
        if tex_name is None:
            return None
        if tex_name in texture_png_cache:
            return texture_png_cache[tex_name]
        result = fetch_and_decode_dds(tex_name)
        if result is None:
            texture_png_cache[tex_name] = None
            return None
        w, h, rgba = result
        img = Image.fromarray(rgba, mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
        texture_png_cache[tex_name] = png_bytes
        print(f"       {tex_name}: {w}x{h} -> PNG {len(png_bytes)} bytes")
        return png_bytes

    for m in meshes_data:
        mt = mesh_textures[m["name"]]
        mt["color_png"] = get_png_for(mt["color"])
        if args.with_normal_map:
            mt["normal_png"] = get_png_for(mt["normal"])
        else:
            mt["normal_png"] = None

    print(f"[6/6] 组装 glTF (.glb) ...")
    build_glb(args.out, joints, joint_names, world_mats, meshes_data, mesh_textures)
    print(f"完成: {args.out}")


# ---------------------------------------------------------------------------
# 4. glTF 组装
# ---------------------------------------------------------------------------

def row_major_to_gltf_column_major_floats(m_row_major: np.ndarray):
    """P3D 矩阵是行主序 (row-major, row-vector 约定 v' = v @ M)。
    glTF 要求列主序 (column-major) 的 16 个 float，且是标准 v' = M @ v 约定。
    对于 row-vector 约定的行主序矩阵 M_rm，其等价的 column-vector 约定矩阵是
    M_cv = M_rm^T；而 column-major 存储 M_cv 又相当于按行读出 M_cv 的转置，
    即 M_cv 的 column-major 展开 == M_rm 的 row-major 展开（转两次抵消）。
    因此: 直接把 M_rm 按行优先展开成16个float，正好就是glTF要的column-major
    序列。用一个最简单的方式验证: glTF 的 T*R*S 组合、以及导入后在three.js
    里位置摆放是否合理来做 sanity check。
    """
    return [float(x) for x in m_row_major.flatten(order="C")]


def build_glb(out_path, joints, joint_names, world_mats, meshes_data, mesh_textures):
    gltf = GLTF2()
    gltf.asset = Asset(generator="prototype-p3d-toolkit export_alex_body.py", version="2.0")

    blob = bytearray()

    def add_buffer_view(data: bytes, target=None):
        offset = len(blob)
        blob.extend(data)
        # glTF 建议 4 字节对齐
        pad = (-len(blob)) % 4
        blob.extend(b"\x00" * pad)
        bv = BufferView(buffer=0, byteOffset=offset, byteLength=len(data))
        if target is not None:
            bv.target = target
        gltf.bufferViews.append(bv)
        return len(gltf.bufferViews) - 1

    def add_accessor(bv_index, comp_type, count, a_type, mins=None, maxs=None,
                      normalized=False, byte_offset=0):
        acc = Accessor(bufferView=bv_index, componentType=comp_type, count=count,
                        type=a_type, byteOffset=byte_offset)
        if mins is not None:
            acc.min = mins
        if maxs is not None:
            acc.max = maxs
        if normalized:
            acc.normalized = True
        gltf.accessors.append(acc)
        return len(gltf.accessors) - 1

    # ---- 1) 骨骼节点 ----
    joint_node_indices = []
    for idx, name in enumerate(joint_names):
        node = Node(name=name)
        joint_node_indices.append(len(gltf.nodes))
        gltf.nodes.append(node)

    # 用局部矩阵设置每个节点的 matrix (local transform), 建立父子关系
    for idx, j in enumerate(joints):
        node = gltf.nodes[joint_node_indices[idx]]
        node.matrix = row_major_to_gltf_column_major_floats(j["rest_pose_row_major"])
        if idx != 0:  # 根节点没有父
            parent_node = gltf.nodes[joint_node_indices[j["parent"]]]
            if parent_node.children is None:
                parent_node.children = []
            parent_node.children.append(joint_node_indices[idx])

    skeleton_root_node = joint_node_indices[0]

    # inverseBindMatrices: 每根骨骼世界矩阵的逆 (还是按 P3D 的 row-vector 约定求逆,
    # 逆矩阵在 row-vector 约定下仍然满足 v_local = v_world @ inverse(world))
    inv_bind_data = bytearray()
    for wm in world_mats:
        inv = np.linalg.inv(wm)
        inv_flat = row_major_to_gltf_column_major_floats(inv)
        inv_bind_data.extend(struct.pack("<16f", *inv_flat))
    ibm_bv = add_buffer_view(bytes(inv_bind_data))
    ibm_acc = add_accessor(ibm_bv, pygltflib.FLOAT, len(joints), "MAT4")

    skin_idx_gltf = Skin(inverseBindMatrices=ibm_acc, joints=joint_node_indices,
                          skeleton=skeleton_root_node)
    gltf.skins.append(skin_idx_gltf)
    skin_gltf_index = 0

    # ---- 2) 材质 + 贴图 (按需, 每个mesh一个material) ----
    def add_material_for(mesh_name, tex_info):
        mat = Material(name=f"mat_{mesh_name}")
        pbr = PbrMetallicRoughness(metallicFactor=0.0, roughnessFactor=0.8)
        color_png = tex_info.get("color_png")
        if color_png:
            img_idx = len(gltf.images)
            gltf.images.append(GLTFImage())  # uri/bufferView 稍后统一填充 (用 bufferView 方式内嵌)
            bv = add_buffer_view(color_png)
            gltf.images[img_idx].bufferView = bv
            gltf.images[img_idx].mimeType = "image/png"
            sampler_idx = 0
            if len(gltf.samplers) == 0:
                gltf.samplers.append(Sampler())
            tex_idx = len(gltf.textures)
            gltf.textures.append(GLTFTexture(source=img_idx, sampler=sampler_idx))
            pbr.baseColorTexture = TextureInfo(index=tex_idx)
        else:
            pbr.baseColorFactor = [0.75, 0.75, 0.75, 1.0]
        mat.pbrMetallicRoughness = pbr
        gltf.materials.append(mat)
        return len(gltf.materials) - 1

    # ---- 3) 每个 Skin -> 一个 glTF mesh (primitive) ----
    mesh_root_children = []
    for m in meshes_data:
        n_vert = len(m["positions"])
        n_idx = len(m["indices"])

        pos_bv = add_buffer_view(m["positions"].tobytes(), target=pygltflib.ARRAY_BUFFER)
        pos_min = m["positions"].min(axis=0).tolist()
        pos_max = m["positions"].max(axis=0).tolist()
        pos_acc = add_accessor(pos_bv, pygltflib.FLOAT, n_vert, "VEC3", mins=pos_min, maxs=pos_max)

        norm_bv = add_buffer_view(m["normals"].tobytes(), target=pygltflib.ARRAY_BUFFER)
        norm_acc = add_accessor(norm_bv, pygltflib.FLOAT, n_vert, "VEC3")

        # 重要修复 (UV bug，同步 export_alex_full.py 的修复，详见其注释):
        # 不应该翻转V轴，直接使用游戏原始UV即可。
        uv = m["uvs"].copy()
        uv_bv = add_buffer_view(uv.tobytes(), target=pygltflib.ARRAY_BUFFER)
        uv_acc = add_accessor(uv_bv, pygltflib.FLOAT, n_vert, "VEC2")

        # 顶点色 uint32 打包 -> 拆成 RGBA uint8 归一化
        color_u8 = np.ascontiguousarray(m["colors"]).view(np.uint8).reshape(n_vert, 4).astype(np.float32) / 255.0
        color_bv = add_buffer_view(color_u8.tobytes(), target=pygltflib.ARRAY_BUFFER)
        color_acc = add_accessor(color_bv, pygltflib.FLOAT, n_vert, "VEC4")

        # 权重: w0 = 1-(w1+w2+w3), 拼成 VEC4
        w123 = m["blend_weight_123"]
        w0 = 1.0 - w123.sum(axis=1, keepdims=True)
        weights4 = np.concatenate([w0, w123], axis=1).astype(np.float32)
        weights_bv = add_buffer_view(weights4.tobytes(), target=pygltflib.ARRAY_BUFFER)
        weights_acc = add_accessor(weights_bv, pygltflib.FLOAT, n_vert, "VEC4")

        # 关节索引: blend_index 是 Matrix_Palette 内的局部索引, 需要:
        #   palette_bone_idx = matrix_palette[blend_index]  (指向本骨架的关节顺序索引)
        #   而 glTF skin.joints 数组顺序就是本骨架的关节顺序 (我们前面 1:1 构造的),
        #   所以 palette_bone_idx 直接就是 skin.joints 的局部下标, 不需要再转换。
        palette = m["matrix_palette"]
        blend_idx = m["blend_index"].astype(np.uint32)
        joints4 = palette[blend_idx].astype(np.uint16)  # (n_vert, 4)
        joints_bv = add_buffer_view(joints4.tobytes(), target=pygltflib.ARRAY_BUFFER)
        joints_acc = add_accessor(joints_bv, pygltflib.UNSIGNED_SHORT, n_vert, "VEC4")

        idx_bv = add_buffer_view(m["indices"].astype(np.uint32).tobytes(),
                                  target=pygltflib.ELEMENT_ARRAY_BUFFER)
        idx_acc = add_accessor(idx_bv, pygltflib.UNSIGNED_INT, n_idx, "SCALAR")

        mat_idx = add_material_for(m["name"], mesh_textures[m["name"]])

        prim = Primitive(
            attributes=Attributes(POSITION=pos_acc, NORMAL=norm_acc, TEXCOORD_0=uv_acc,
                                   COLOR_0=color_acc, JOINTS_0=joints_acc, WEIGHTS_0=weights_acc),
            indices=idx_acc, material=mat_idx,
        )
        mesh = Mesh(name=m["name"], primitives=[prim])
        mesh_idx = len(gltf.meshes)
        gltf.meshes.append(mesh)

        mesh_node = Node(name=f"node_{m['name']}", mesh=mesh_idx, skin=skin_gltf_index)
        mesh_node_idx = len(gltf.nodes)
        gltf.nodes.append(mesh_node)
        mesh_root_children.append(mesh_node_idx)

    # ---- 4) 场景根节点 ----
    scene_root_children = [skeleton_root_node] + mesh_root_children
    gltf.scenes.append(Scene(nodes=scene_root_children))
    gltf.scene = 0

    gltf.buffers.append(Buffer(byteLength=len(blob)))
    gltf.set_binary_blob(bytes(blob))
    gltf.save_binary(out_path)


if __name__ == "__main__":
    main()
