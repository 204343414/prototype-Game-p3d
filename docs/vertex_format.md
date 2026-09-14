# Prototype (.p3d) 顶点/索引/骨骼数据格式 —— 已验证结构文档

本文档记录对 `alex.p3d.rz` 中 `alex_reg_body_alex_bodyShape`
(PolySkin, global_index=139187) 这个部位的 `PrimitiveGroup`
(0x00010020, global_index=139189) 及其子 chunk 的完整逆向结果。
所有字段均已用实际二进制数据做统计/范围检验通过（见下方"验证证据"）。

## 1. PrimitiveGroup (type_id = 0x00010020)

字段顺序（小端）：

```
uint32          version
StringAlignedU8 shader_name      # 1字节长度 + ASCII + 补零对齐到4字节倍数
uint32          primitive_type   # 0=TriangleList (来自 NetP3DLib PrimitiveTypes 枚举)
uint32          vertex_type      # 位标志，见下方 VertexTypes
uint32          num_vertices
uint32          num_indices
uint32          num_matrices     # 本部位蒙皮用到的骨骼数（Matrix_Palette 大小）
uint32          memory_imaged    # 1 = 顶点数据以 Memory_Image_Vertex_List 打包存储
uint32          optimized
uint32          vertex_animated
uint32          vertex_animation_mask
```

实测值（alex_reg_body_alex_bodyShape）：
```
version=0
shader_name="f9c41998c0ee0204151120620104ac90"
primitive_type=0 (TriangleList)
vertex_type=0xB1B1
num_vertices=2512
num_indices=7137
num_matrices=18
memory_imaged=1
optimized=1
vertex_animated=0
vertex_animation_mask=0
```

`vertex_type=0xB1B1` 按 NetP3DLib 的 `PrimitiveGroupChunk.VertexTypes`
标志位解析（来自 `references/netp3dlib`）：
```
低4位 (UV数量掩码)     = 1        -> 1组UV
bit4  Normals          = 1
bit5  Colours          = 1
bit6  Specular         = 0
bit7  Matrices         = 1
bit8  Weights          = 1
bit9  Size             = 0
bit10 W                = 0
bit11 Binormal         = 0
bit12 Tangent          = 1
bit13 Position         = 1
bit14 Colours2         = 0
bit15-17 ColourCount   = 1
```
即：Position + Normals + 1×UV + Colours + Tangent + Matrices(Palette) + Weights，
与实际观察到的子 chunk 集合完全吻合。

## 2. 子 chunk 一览（PrimitiveGroup 的直接子节点）

| type_id      | 出现次数 | 含义                                                  |
|--------------|---------|-------------------------------------------------------|
| 0x00010014   | ×2      | 未解码（疑似 AABB / bounding box 或 render state）      |
| 0x00010012   | ×2      | Memory_Image_Vertex_List —— 见下方两种变体              |
| 0x00010013   | ×1      | 索引缓冲 (Index_List)，16位索引                          |
| 0x0001000D   | ×1      | Matrix_Palette（骨骼索引表，num_matrices=18项）         |
| 0x00010003   | ×1      | 未解码，24字节 payload（疑似 bounding sphere: center+radius 或类似摘要） |
| 0x00010004   | ×1      | 未解码，16字节 payload                                  |

## 3. 0x00010012 chunk 通用头部（12字节）

```
uint16 field_a      # 恒为1，用途未知（可能是sub-version）
uint16 field_b      # 恒为2，用途未知
uint32 field_c      # 变体标识：2=主顶点缓冲(56B/vert)，1=颜色+UV缓冲(12B/vert)
uint32 data_size    # 紧随其后的数据体总字节数（不含这12字节头）
```
`data_size` 与 `12 + data_size == payload_len` 精确吻合（已验证）。

### 3a. 变体 field_c=2：主顶点缓冲，56字节/顶点（2512顶点，payload_len=140684）

已完整解码并做统计学验证（法线/切线单位向量、权重和≤1、骨骼索引∈[0,17]）：

```
offset  size  field
0       12    position: Vector3 (float x,y,z)      # 模型空间坐标
12      12    normal:   Vector3 (float x,y,z)       # 单位向量，验证：|normal|≈1.0000 (全部2512个顶点)
24      12    tangent:  Vector3 (float x,y,z)       # 单位向量，验证：|tangent|≈1.0000 (全部顶点)
36      4     tangent_w: float                       # 副切线手性(handedness)，只取 -1.0 或 +1.0 两个值
                                                       # (bitangent = cross(normal, tangent) * tangent_w)
40      4     blend_weight[1]: float                 # 第2根骨骼权重
44      4     blend_weight[2]: float                 # 第3根骨骼权重
48      4     blend_weight[3]: float                 # 第4根骨骼权重
                                                       # 第1根骨骼权重是隐含的: w0 = 1 - (w1+w2+w3)
                                                       # 验证：w0+w1+w2+w3 恒为1（w0隐含算出恒>=0，无负值溢出）
52      1     blend_index[0]: uint8                  # 索引进 Matrix_Palette (0..num_matrices-1)
53      1     blend_index[1]: uint8
54      1     blend_index[2]: uint8
55      1     blend_index[3]: uint8
                                                       # 验证：全部2512*4=10048个索引值均 <= 17 (= num_matrices-1)
```
即 stride=56 = 12(pos)+12(normal)+12(tangent)+4(tangent_w)+12(3个权重)+4(4个索引字节)。

注意：这个变体里**没有独立的 UV / 顶点色字段**——UV 和顶点色被拆到另一个
0x00010012 实例（field_c=1）里，两个顶点流通过顶点序号一一对应
（都是 2512 个顶点，按相同顺序排列）。

### 3b. 变体 field_c=1：颜色+UV缓冲，12字节/顶点（2512顶点，payload_len=30156）

```
offset  size  field
0       4     vertex_color: uint32 (打包RGBA或BGRA，实测本模型全部顶点=0xFFFFFFFF，即纯白，无烘焙AO)
4       4     u: float
8       4     v: float
```
即 stride=12 = 4(color)+4(u)+4(v)。UV 范围实测 u∈[-1,1], v≈[-1,0.994]（注意不是标准[0,1]范围，
导出时可能需要按具体shader的UV wrap/scale规则处理，或简单地直接使用，多数查看器对越界UV会自动wrap）。

## 4. 0x00010013 索引缓冲

头部(12字节)与 0x00010012 相同的三字段结构（field_a/field_b/field_c + data_size），
实测 field_a=1, field_b=2, field_c=0, data_size=14274 (= 7137*2，与 payload_len-12 吻合)。

数据体：`num_indices` 个 `uint16`（16位小端索引），按 `primitive_type=0`
(TriangleList) 每3个一组构成一个三角形。

## 5. 0x0001000D Matrix_Palette（骨骼索引表）

```
uint32   count            # 实测=18，与 PrimitiveGroup.num_matrices 吻合
uint32[] bone_indices     # count 个骨骼索引（指向 Skeleton 的骨骼列表）
```
实测 alex_reg_body_alex_bodyShape 的 18 个骨骼索引：
```
[18, 10, 11, 9, 8, 3, 12, 4, 43, 63, 14, 13, 36, 37, 15, 35, ...]
```
（注：这是 Matrix_Palette 内的“局部骨骼索引”，顶点里的 blend_index 字段
是索引进**这个数组**、而不是直接索引进 Skeleton 的骨骼列表——需要两次
查表：`skeleton_bone_index = matrix_palette[blend_index]`。这是标准的
"调色板蒙皮 / palette skinning" 做法，用于让顶点里只需存 1 字节索引
即可覆盖任意数量的骨骼。）

## 6. 0x00010014 Memory_Image_Vertex_Description（顶点声明表）—— 已解码，与独立推导完全吻合

官方 chunk 名来自 `references/netp3dlib/.../ChunkIdentifier.cs`：
`Memory_Image_Vertex_Description = 0x10014`。它是配对在每个
`Memory_Image_Vertex_List` (0x00010012) 前面的"顶点属性声明表"，
描述该顶点缓冲每个属性字段的偏移/大小/类型，与我们独立通过统计法
反推出的字段布局（第3节）**完全交叉验证一致**。

头部（16字节）：
```
uint32 version              # 实测=0x00020001
uint32 param                # 实测与对应 Memory_Image_Vertex_List 的 field_c 相同（2 或 1）
uint32 ref_buffer_size      # 对应顶点缓冲的数据体大小(不含其12字节头)，实测与0x00010012的data_size字段完全一致
uint32 desc_size            # 紧随其后声明表的字节数
```

声明表每条目 17 字节：
```
uint32 name_hash    # 属性语义的字符串哈希（如"position"/"normal"等，具体哈希算法待确认，
                     #  但用途已通过位置和分量数确定，无需破解哈希本身）
uint32 zero          # 恒为0，用途未知（可能是保留字段）
uint32 offset        # 该属性在每个顶点记录内的字节偏移
uint8  vertex_stride # 整个顶点记录的总字节数（对本PrimitiveGroup的所有条目均相同，如56或12）
uint8  elem_type     # 恒为0，用途未知（可能永远是"float"类型标记，未见其它取值）
uint8  num_components# 该属性的分量个数（如3=Vector3, 4=Vector4/含w, 1=打包uint32）
uint16 unknown       # 剩余2字节，观测到 0x0001 或 0x0000，可能是"是否归一化"或"用途子标志"
```

**56字节/顶点缓冲（field_c=2）的声明表实测（5个属性，与第3a节完全吻合）**：
| offset | vertex_stride | num_components | 对应字段（已用统计法独立验证） |
|--------|--------------|-----------------|-------------------------------|
| 0      | 56           | 3               | position (Vector3)            |
| 12     | 56           | 3               | normal (Vector3, 单位向量)     |
| 24     | 56           | 4               | tangent + tangent_w (Vector4) |
| 40     | 56           | 3               | blend_weight[1..3] (3×float)  |
| 52     | 56           | 4               | blend_index[0..3] (4×uint8)   |

**12字节/顶点缓冲（field_c=1）的声明表实测（2个属性，与第3b节完全吻合）**：
| offset | vertex_stride | num_components | 对应字段 |
|--------|--------------|-----------------|----------|
| 0      | 12           | 4(打包)         | vertex_color (uint32 RGBA/BGRA) |
| 4      | 12           | 2               | uv (Vector2)                     |

这是**最强的交叉验证证据**：文件自带的顶点声明表与我们仅凭统计规律
（单位向量、权重和、索引范围）独立反推出的字段布局逐字节精确吻合，
证明第3节的顶点格式解读完全正确、可直接用于导出脚本。

## 7. 已解码：0x00010003 Bounding_Box / 0x00010004 Bounding_Sphere

官方枚举名确认（`references/netp3dlib/.../ChunkIdentifier.cs`）：
`Bounding_Box = 0x10003`，`Bounding_Sphere = 0x10004`。

```
Bounding_Box (24字节):
    float min_x, min_y, min_z
    float max_x, max_y, max_z

Bounding_Sphere (16字节):
    float center_x, center_y, center_z
    float radius
```

实测 alex_reg_body_alex_bodyShape：
```
Bounding_Box: min=(-0.2229, -0.0018, -0.2223) max=(0.2229, 1.8849, 0.1717)
              —— 与顶点缓冲中实际 pos.x/y/z 的 min/max 完全一致（已验证）
Bounding_Sphere: center=(0.0000, 0.9415, -0.0253) radius=0.9891
              —— 与从上述AABB计算出的中心点和半对角线长度精确吻合到小数点后6位（已验证）
```

## 8. 待解码（后续步骤）

- `0x00010001` (Skin) 本体的62字节 payload：从 hex 可见明文字符串
  `"alex_reg_body_alex_headShape"` + 数字3 + `"alex_reg_body_skeleton"`，
  推测是"皮肤名 + 关联骨骼数(或版本) + 对应骨架(Skeleton)引用名"的字符串对，
  需要专门解析字符串对齐规则（用第一节的 StringAlignedU8 规则应该可以直接套用）。
- `0x00010021` (Vertex_Compression_Hint，32字节)：8个uint32，值全是0或0x20(32)，
  用途待查（可能是每个属性的量化精度提示，与压缩/量化有关，正常渲染流程可忽略）。
- `0x00122000` (Sort_Order，8字节)：2个float，值为(0.0, 0.5)，可能是渲染排序权重，可先忽略。
- `0x00010017` (Render_Status，4字节)：uint32=1，可能是"是否可见/启用"标志，可先忽略。

## 验证方法总结（供后续复用）

对每个"数据体紧随定长头部"的 chunk，用 `(payload_len - header_size) / count`
是否为整数来验证头部大小和记录数假设；对已进入具体字段阶段，进一步用
物理约束做交叉验证：
- 法线/切线应为单位向量（模长≈1.0）
- 骨骼混合权重之和应 ≤ 1.0（含隐含的第一权重）
- 骨骼索引应在 [0, num_matrices-1] 范围内
- 顶点色/UV 等值域应落在合理范围（如颜色分量0-255，UV常见于[-1,1]或[0,1]附近）

这套方法在数据没有官方文档、仅凭 gibbed-prototype (Prototype专用)
与 netp3dlib (通用P3D标准) 两个开源参考库的部分线索时，被证明是可靠的
逆向路径，后续解码其余角色/地图资源时应继续沿用。
