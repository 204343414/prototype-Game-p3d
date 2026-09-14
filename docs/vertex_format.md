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

## 6. 待解码（未来步骤）

- `0x00010014` ×2（62/113字节payload）：疑似 render state / AABB，需要专门 fetch 分析。
- `0x00010003`（24字节）：疑似 bounding sphere（center Vector3 + radius float = 16字节，
  或 min/max AABB = 24字节，需专门验证）。
- `0x00010004`（16字节）：可能是 bounding sphere（center Vector3 12B + radius float 4B = 16字节，
  与payload长度精确吻合，是更可能的候选，待验证）。

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
