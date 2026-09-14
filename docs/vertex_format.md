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

## 8. Skin (type_id = 0x00010001) —— "总装配图"绑定关系，已完整解码并三样本验证

这是回答"哪个网格(PolySkin)用哪副骨架(Skeleton)"的关键 chunk，是编写导出
脚本前的最后一块拼图。字段顺序（小端，紧跟在 chunk 通用12字节头之后）：

```
StringAlignedU8   name             # 与所在 PolySkin/父 chunk 同名，1字节长度前缀
                                    # （含padding，不含结尾计数规则见第327行"验证方法"）
                                    # + UTF8 内容 + 尾随 \0 补齐
uint32            version          # 恒为 3（3个样本全部一致）
StringAlignedU8   skeleton_name    # 绑定的骨架名字符串，用于按名字去同一 p3d 文件里
                                    # 查找对应的 Skeleton_2 (0x00004500) chunk
uint32            num_primitive_groups  # 该 Skin 下属 Primitive_Group 数量，
                                    # 与后续同深度出现的 0x00010020 子节点数一致
```

字符串编码规则与此前验证过的规则相同：第一字节 = 字符串字节数（若非4的倍数会
补 `\0` 对齐到4字节倍数，但长度字节记录的是补齐前的原始字节数——本例3个样本
的名字长度恰好都不需要额外对齐外的整数字节，实测时直接按"长度字节数量的原始
字符"截取、再 `split('\x00')[0]` 去除可能残留的 padding 即可稳健处理）。

**三样本交叉验证（alex.p3d.rz，全部62字节 payload，全部100%无余数解析完成）**：

| global_index | Skin name (= 所属PolySkin名) | version | skeleton_name | num_primitive_groups |
|---|---|---|---|---|
| 139187 | `alex_reg_body_alex_bodyShape` | 3 | `alex_reg_body_skeleton` | 1 |
| 139202 | `alex_reg_body_alex_headShape` | 3 | `alex_reg_body_skeleton` | 1 |
| 139217 | `alex_reg_body_AlexVestShape`  | 3 | `alex_reg_body_skeleton` | 1 |

结论：Alex Mercer 身体主模型的所有 PolySkin 部位（身体/头/外套）**全部绑定
同一副骨架** `alex_reg_body_skeleton`。导出脚本的绑定逻辑可简化为：
对每个 PolySkin，读取其内部 `Skin` chunk 拿到 `skeleton_name`，再在同一个
p3d 文件的 chunk 树里按 `NamedChunk.Name == skeleton_name` 查找
`Skeleton_2` chunk 即完成绑定；`num_primitive_groups` 用于确认/校验该
PolySkin 下应该有多少个 `Primitive_Group` 子网格（本例均为1，与实测吻合）。

**该字段定义来自 `references/netp3dlib` 的 `SkinChunk.cs` 完整实现**（不同于
此前 `SkeletonJoint2Chunk.cs` 残缺导致的踩坑，这次读写逻辑
`Name + Version + SkeletonName + NumPrimitiveGroups` 与实测字节完全吻合，
三个样本消耗字节数与 payload_len 分毫不差）。

**全文件范围扩展验证（type_filter=0x00010001 拉出 alex.p3d.rz 全部11个 Skin
chunk，payload_len 从62到102字节不等，全部100%无余数解析成功）**：

| Skin name | skeleton_name |
|---|---|
| `alex_reg_arms_alex_armsShape` | `alex_reg_arms_skeleton` |
| `alex_reg_body_alex_bodyShape` | `alex_reg_body_skeleton` |
| `alex_reg_body_alex_headShape` | `alex_reg_body_skeleton` |
| `alex_reg_body_AlexVestShape` | `alex_reg_body_skeleton` |
| `alex_reg_left_arm_alex_arms1Shape` | `alex_reg_left_arm_skeleton` |
| `groundspike_large_Groundspike_LargeShape` | `groundspike_large_skeleton` |
| `groundspike_mid_Groundspike_MediumShape` | `groundspike_mid_skeleton` |
| `groundspike_single_large_Groundspike_Single_LargeShape` | `groundspike_single_large_skeleton` |
| `groundspike_single_mid_Groundspike_Single_MediumShape` | `groundspike_single_mid_skeleton` |
| `groundspike_single_small_Groundspike_Single_SmallShape` | `groundspike_single_small_skeleton` |
| `groundspike_small_Groundspike_SmallShape` | `groundspike_small_skeleton` |

**关键结论（解决了此前悬而未决的疑问）**：`alex_reg_arms_skeleton`、
`alex_reg_body_skeleton`、`alex_reg_left_arm_skeleton` 是**三套完全独立的
骨架**，并非同一骨架的不同引用名——躯干、右臂("arms")、左臂分别使用不同
骨架文件（推测是为了让手臂能独立于身体做额外的动画混合，例如 Alex Mercer
将手臂变形为刀刃/锤子等武器形态时不影响身体骨架）。此外还发现
alex.p3d.rz 里内嵌了6个 "groundspike"（地刺）系列的独立小模型+骨架，
应为 Alex Mercer 地面突刺类技能的特效模型，每个变体（large/mid/small及
single前缀变体）都各自打包了自己的骨架。这说明**装配一个角色的完整外观
可能需要合并多副骨架**，导出脚本需要按 `skeleton_name` 分组处理，而不能
假设一个 p3d 文件只有一副骨架。

## 8b. Composite_Drawable_2 (0x00123000) + Composite_Drawable_Primitive (0x00123001)
    —— 真正的"总装配图"，比 Skin 更高一层，**对未来魔改换装/拆件极其重要**

在 Skin 之上还有一层"部位组"结构，把多个 Skin（Primitive）打包成一个
逻辑整体，并且**每组可以有自己独立的骨架引用**——这正是 Alex Mercer 能
把手臂单独变形成武器（刀刃/锤子等）而不牵连身体动画的底层机制。已用
alex.p3d.rz 全部10个样本（payload 50~119字节不等）100%无余数解析验证。

**Composite_Drawable_2 (0x00123000) 字段格式**：
```
uint32            version              # 实测恒为 0
StringAlignedU8   name                 # 部位组名，如 "alex_reg_body"
StringAlignedU8   skeleton_name        # 该组绑定的骨架名
uint32            num_primitives       # 下属 Composite_Drawable_Primitive 数量
```
（注意字段顺序与 Skin 不同：version 在最前面，name 在 skeleton_name 之前，
且没有 name 前导的 skin 自身版本号——两者是姊妹结构但字段顺序不能混用。）

**Composite_Drawable_Primitive (0x00123001) 字段格式**（作为 CD2 的直接子节点，
数量等于父节点的 num_primitives）：
```
uint32            version              # 实测恒为 0
uint32            create_instance      # 实测恒为 0，用途待查（可能与运行时实例化/池化有关）
StringAlignedU8   name                 # 引用某个 Skin 的名字（精确对应 Skin chunk 的 name 字段）
uint32            type                 # 实测恒为 2，可能是"这是一个Skin类型的Drawable"
                                        # （呼应 Dark Angel 文档里 Drawable::Type 枚举
                                        # {UNKNOWN,COMPOSITE,GEOMETRY,SKIN}，2很可能就是SKIN，
                                        # 待更多样本验证其他取值）
uint32            skeleton_joint_id    # 实测恒为 0，推测是"此部位挂载到骨架的第几号关节"
                                        # （类似 CompositeDrawableProp 的挂载点，武器/道具类
                                        # 部位可能会用到非0值，人形本体网格恒为0是合理的）
```

**alex.p3d.rz 完整"总装配图"清单（10组，全部验证通过）**：

| 部位组 (CD2 name) | 绑定骨架 | 下属 Skin(s) |
|---|---|---|
| `alex_reg_arms` | `alex_reg_arms_skeleton` | `alex_reg_arms_alex_armsShape` |
| `alex_reg_body` | `alex_reg_body_skeleton` | `alex_reg_body_alex_headShape`, `alex_reg_body_AlexVestShape`, `alex_reg_body_alex_bodyShape` |
| `alex_reg_left_arm` | `alex_reg_left_arm_skeleton` | `alex_reg_left_arm_alex_arms1Shape` |
| `groundspike_large` | `groundspike_large_skeleton` | `groundspike_large_Groundspike_LargeShape` |
| `groundspike_mid` | `groundspike_mid_skeleton` | `groundspike_mid_Groundspike_MediumShape` |
| `groundspike_single_large` | `groundspike_single_large_skeleton` | `groundspike_single_large_Groundspike_Single_LargeShape` |
| `groundspike_single_mid` | `groundspike_single_mid_skeleton` | `groundspike_single_mid_Groundspike_Single_MediumShape` |
| `groundspike_single_small` | `groundspike_single_small_skeleton` | `groundspike_single_small_Groundspike_Single_SmallShape` |
| `groundspike_small` | `groundspike_small_skeleton` | `groundspike_small_Groundspike_SmallShape` |

**魔改/换装应用启示（供以后参考）**：
- 想给 Alex Mercer 换装/替换外套模型，理论上只需替换 `alex_reg_body` 组下
  `AlexVestShape` 对应的 Skin + 几何数据，保持同名引用即可被 CD2 正确拾取，
  不需要动骨架或其他两个部位（头/身体）。
- 手臂（`alex_reg_arms` 右臂 + `alex_reg_left_arm` 左臂）被拆成两个独立
  CD2 组、各自独立骨架，说明游戏引擎设计上支持"手臂单独换成武器形态的网格
  +骨架"而不影响身体——这对做"哪个部位能不能单独抽取/替换"的魔改判断
  非常关键：**同一 CD2 组内的多个 Skin 共享骨架，不同 CD2 组则完全独立**。
- `groundspike` 系列证明"技能特效模型"和"角色身体部位"用的是完全相同的
  CD2+Skin+Skeleton 数据结构，没有特殊格式——技能特效模型的替换/新增
  理论上遵循同一套逻辑，这对以后想做特效类魔改也是好消息。
- 再往上应该还有把多个 CD2 组合并成一个完整"角色对象"的顶层结构（对应
  Dark Angel 文档提到的 `Bundle`/`Object` 概念），以及贴图/材质引用的挂接点，
  仍是本文档"待解码"清单的后续目标。

## 8c. 材质/贴图关联 —— Texture (0x00019000/0x00019006) + NewShader (0x00011015)
    + 参数子chunk (0x00011016/17/18/20)，已完整解码并链路闭环验证

这块解决了"完整角色"最后一块拼图：怎么从一个 PrimitiveGroup 找到它实际
贴的图。链路是 `PrimitiveGroup.shader_name` → 同名 `NewShader` chunk →
其子节点里的贴图/数值参数 → 贴图参数值就是贴图资源名，可直接去同一 p3d
文件里按名字找到对应的 `Texture` chunk（贴图像素数据在其子节点里，见下）。

### 8c.1 Texture (type_id = 0x00019000) —— 通用P3D标准贴图头，字段来自 netp3dlib `TextureChunk.cs`

```
StringAlignedU8  name             # 贴图文件名，如 "alex_body_dm.dds"
uint32           version          # 实测恒为 14000
uint32           width            # 像素宽，实测均为2的幂（512/128/256等）
uint32           height           # 像素高
uint32           bpp              # 实测恒为 8（bits per pixel 的某种编码，非传统意义"8bpp"，
                                    # 因为实际是压缩纹理，可能是"每通道位数"或版本相关的固定值）
uint32           alpha_depth      # 8 = 有独立alpha通道（法线贴图/带透明通道贴图），1 = 无
uint32           num_mipmaps      # 实测恒为 1（可能引擎运行时自动生成mipmap，或没有预生成）
uint32           texture_type     # 枚举，实测恒为 0 = RGB（真实压缩格式在子节点 0x00019006 里）
uint32           usage_hint       # 枚举，实测恒为 0 = Static
uint32           priority         # 实测恒为 0
```
（8个样本100%无余数解析验证，涵盖身体/头部/技能特效贴图。样本详见下表。）

### 8c.2 Texture 的子节点 0x00019006 (TextureDDS) + 0x00019002 (Image_Data)
    —— 已完整验证，可直接拼出合法 .dds 文件

> **⚠️ 2024勘误 (曾导致贴图渲染为彩色花屏/雪花噪点的重大bug，已修复)**：
> 下面原文说 `0x00019002` 的 payload "就是裸 DXT 压缩字节流本体，无额外头部"，
> **这个结论是错的**，是早期只看payload_len数量级"差不多对得上"就下的过早结论，
> 没有真正逐字节核对开头内容。实际上 `0x00019002` 的 payload 开头是:
> ```
> uint32           length_prefix    # =payload_len-4，冗余字段，等于后面全部数据的字节数
> char[4]          magic            # 固定 "DDS "，标准DDS文件魔数
> DDS_HEADER       header           # 标准124字节DDS_HEADER结构 (含dwSize/dwFlags/
>                                    # dwHeight/dwWidth/dwPitch/dwDepth/dwMipMapCount/
>                                    # 11个保留u32/DDS_PIXELFORMAT 32字节(含dwFourCC)/
>                                    # 其余caps字段)，跟标准DDS文件格式完全一致，可以
>                                    # 直接用标准DDS解析库解析
> ...                                # 之后才是真正的裸DXT压缩像素数据(含mipmap链)
> ```
> 即：**payload开头132字节(4+4+124)是"长度前缀+完整DDS文件头"，真正的DXT数据
> 从offset=132开始**。用512x512 DXT1贴图精确验证：`payload_len - 132 =
> 174908 - 132 = 174776`，与512x512 DXT1完整10级mipmap链的理论字节数
> (按每级 `max(1,ceil(w/4))*max(1,ceil(h/4))*8` 累加至1x1，算出恰好174776)
> **完全吻合，零误差**。之前的导出脚本(`export_alex_body.py`/
> `export_alex_full.py`)因为没跳过这132字节头部，把DDS文件头也当成DXT像素
> 数据丢给`texture2ddecoder`，导致整张贴图从第一个block起就全部错位，渲染
> 出来是彩色雪花噪点（用户实际预览时发现并反馈）。两个脚本均已修复：解码前
> 先校验`payload[4:8]==b"DDS "`，再跳过前132字节取真正的DXT数据传给解码器。
> 教训：往后遇到"数量级差不多吻合"就想通过的情况，都应该像本次一样做
> "预测的确切字节数 vs 实际payload_len精确相减"这种零容差验证，而不是满足
> 于数量级近似。

`0x00019006`(TextureDDS，来自 gibbed-prototype 的 `TextureDDS.cs`) 是
Prototype 专用的 DDS 元数据子节点，紧跟在 0x00019000 之后（depth+1），
给出真实的压缩格式；它自己的子节点 `0x00019002`(Image_Data，
`NetP3DLib.ChunkIdentifier` 命名，等价 gibbed 库的 `TextureData`) 的
payload 开头132字节是标准DDS文件头 (见上方勘误)，**之后才是**裸 DXT
压缩字节流本体（含mipmap链，实测 512x512 DXT1贴图总payload约174908字节，
减去132字节头部=174776字节，与理论mipmap链大小完全吻合）。2个样本100%
验证通过：

```
0x00019006 字段（紧跟 chunk 头部之后）：
StringAlignedU8  name             # 与父 Texture 同名，如 "alex_body_dm.dds"
uint32           version          # 实测恒为 1（注意与父 Texture.version=14000 不同，各自独立编号）
uint32           width
uint32           height
uint32           unknown4         # 实测恒为 8
uint32           unknown5         # 实测取值 1 或 8——观察到与父 Texture.alpha_depth 完全一致
                                    # （1=无独立alpha如_dm漫反射图, 8=有alpha如_nm/_sm贴图），
                                    # 推测就是 alpha_depth 在这里的重复记录
uint32           num_mipmaps      # 实测恒为 10（512→1像素刚好10级，与父 Texture.num_mipmaps=1 不同，
                                    # 说明父层的num_mipmaps含义可能是"是否含mipmap链"布尔值而非数量，
                                    # 这里的10才是真实mipmap级数）
FourCC(4字节ASCII) algorithm      # "DXT1"(0x31545844) / "DXT3" / "DXT5"，实测 _dm→DXT1(无alpha)，
                                    # _nm(法线贴图)→DXT5(带alpha)，符合行业惯例
```

**样本**：`alex_body_dm.dds`: 512x512, alpha_depth=1, mipmaps=10, **DXT1**；
`alex_body_nm.dds`: 512x512, alpha_depth=8, mipmaps=10, **DXT5**。

**拼出合法 .dds 文件的方法**：读取 `0x00019006` 拿到 width/height/mipmaps/
algorithm，再读其子节点 `0x00019002` 的完整 payload 作为像素数据，按标准
DDS 文件格式（128字节 `DDS ` magic + `DDS_HEADER` 结构，`dwFourCC` 填
DXT1/DXT3/DXT5，`dwMipMapCount` 填10，`dwFlags` 加上 `DDSD_MIPMAPCOUNT`）
拼接文件头再附加像素数据即可直接另存为可被任意图片工具打开的 `.dds` 文件
——这是贴图导出脚本的完整依据，尚未实际生成文件验证，留待导出脚本阶段执行。

### 8c.3 NewShader (type_id = 0x00011015，**Prototype 游戏专用**，非通用P3D标准 Shader=0x11000)

字段格式（来自 gibbed-prototype 的 `NewShader.cs`，5个样本100%无余数解析验证）：
```
StringAlignedU8  name             # 材质哈希名，如 "f9c41998c0ee0204151120620104ac90"
                                    # ——精确等于 PrimitiveGroup.shader_name 字段的值，这就是关联key
uint32           unknown2         # 实测恒为 256 (0x100)，用途未知
StringAlignedU8  shader_template  # 人类可读的着色器模板名，如 "char_alex_cloth"/"char_alex_armor"/"char_alex"
uint32           unknown4         # 实测取值 10~12，可能是模板变体/版本号
```

紧随其后（depth+1）的子节点是该材质的参数字典，数量不定：
- **0x00011016 (纹理参数)**：`StringAlignedU8 param_name + StringAlignedU8 value`
  ——`value` 为空字符串代表"未设置"，非空则是贴图文件名（对应某个 Texture chunk 的 name）。
  实测出现的 param_name：`color`（贴图色）、`normal`（法线贴图）、`specular`（高光贴图）、
  `consume_colour`/`consume_normal`/`consume_refl`（未设置，空值，可能是某种"消耗/覆盖"槽位）。
- **0x00011017 (浮点参数)**：`StringAlignedU8 param_name + float32 value`
  ——如 `refl_amounts=0.1`、`rimLightIntensity=1.0`。
- **0x00011018 (Vector2参数)**：`StringAlignedU8 param_name + float32 x + float32 y`
  ——如 `spec_params=(0.249,0.656)`、`wrap_flattening=(0.206,0.45)`。
- **0x00011020 (16字节前，仅4字节payload)**：`uint16 unknown1 + uint16 unknown2`，
  实测恒为 `(1,1)`，用途未知，紧跟在 NewShader 之后、参数列表之前，可能是"参数数量"或版本标志。
- **0x0900000A**：payload 是与父 NewShader.name 完全相同的哈希字符串（原始P3D字符串编码），
  用途未知（可能是某种校验/回显节点），不影响材质关联链路，可先忽略。

### 8c.4 完整链路闭环验证（alex_reg_body_alex_bodyShape 部位）

```
PrimitiveGroup(139189).shader_name = "f9c41998c0ee0204151120620104ac90"
        │  （精确字符串匹配，已验证 ==）
        ▼
NewShader(global_index=12).name    = "f9c41998c0ee0204151120620104ac90"
NewShader(global_index=12).shader_template = "char_alex_cloth"
        │  子节点参数字典（global_index 19~24）
        ▼
  color    = "alex_body_dm.dds"   (漫反射贴图 Diffuse Map)
  normal   = "alex_body_nm.dds"   (法线贴图 Normal Map)
  specular = "alex_body_sm.dds"   (高光贴图 Specular Map)
        │  （按贴图名去同一 p3d 文件里找同名 Texture chunk）
        ▼
Texture(global_index=0): name="alex_body_dm.dds", 512x512, alpha_depth=1
Texture(global_index=4): name="alex_body_nm.dds", 512x512, alpha_depth=8 (有alpha，法线贴图常见)
Texture(global_index=8): name="alex_body_sm.dds", 512x512, alpha_depth=1
```

**结论：材质关联的完整数据链已打通**——`几何(PrimitiveGroup) → 材质哈希名
→ NewShader → 贴图参数字典 → 贴图文件名 → Texture chunk → DDS像素数据`。
命名规律总结（供贴图分类参考）：`_dm`=diffuse/漫反射色贴图，`_nm`=normal/
法线贴图，`_sm`=specular/高光贴图。导出脚本可以直接把 `color`/`normal`/
`specular` 三个参数映射为 glTF 的 `baseColorTexture`/`normalTexture`/
`metallicRoughnessTexture`(或自定义扩展)三个贴图槽位。

## 8d. memory_imaged=0 变体：老式"独立 List chunk"顶点存储格式
    —— 首次在 alex_reg_body_AlexVestShape(外套) 上发现，已完整解析验证

**背景**：第1节的 `PrimitiveGroup.memory_imaged` 字段之前只观察到取值1
（=顶点数据打包进 `Memory_Image_Vertex_List`，见第2-6节）。写导出脚本
实测 `alex_reg_body` 组的第三个 Skin（`AlexVestShape`，外套）时才发现
`memory_imaged=0`，此时顶点各属性被拆分成多个独立的 `xxx_List` chunk，
是每个属性各占一个 chunk 的"旧式"存储方式（对应 netp3dlib 里
`OldPrimitiveGroupChunk` 一系的 List chunk 定义），而不是第2-6节的打包格式。
**两种变体承载的字段语义完全相同，只是物理存储布局不同**，导出脚本需要
分支处理（已在 `tools/p3d_export/export_alex_body.py` 里实现并验证）。

各 List chunk 均为通用形式 `count(uint32) + count 个定长记录`，已用
`AlexVestShape`（num_vertices=1268, num_indices=3594, num_matrices=10）
100%验证：

| type_id | 官方名(netp3dlib) | 记录格式 | 验证结果 |
|---|---|---|---|
| `0x00010005` | Position_List | count + count×Vector3 | 1268条，坐标范围y∈[1.04,1.72]（外套覆盖身体上半部分，合理） |
| `0x00010006` | Normal_List | count + count×Vector3 | 1268条，单位向量 |
| `0x00010007` | UV_List | count(u32)+**channel(u32)**+count×Vector2 | 1268条，channel=0 |
| `0x00010008` | Colour_List | count + count×uint32(打包RGBA) | 1268条，全部=0xFFFFFFFF(纯白) |
| `0x0001000B` | (netp3dlib命名Matrix_List，**实测本模型语义是blend_index**) | count + count×byte[4] | 1268条，取值范围[0,9]，精确等于`< num_matrices=10`，与主格式`blend_index`字段语义完全一致 |
| `0x0001000C` | Weight_List | count + count×Vector3 | 1268条，即`blend_weight[1..3]`，w0=1-sum，sum∈(0.8,1.0]，与主格式weight语义一致 |
| `0x0001000A` | Index_List | count + count×**uint32**（注意不是16位！） | 3594个，取值范围[0,1267]=`[0,num_vertices-1]` |
| `0x0001000D` | Matrix_Palette | 同第5节，无变化 | count=10，与`num_matrices`一致 |
| `0x00010028` | (未在参考库中命名，实测是Tangent+W) | count + count×Vector4 | 1268条，xyz单位向量，w∈{-1,+1} —— 与主格式`tangent+tangent_w`字段完全对应 |

**结论**：导出脚本需要先读 `PrimitiveGroup.memory_imaged` 字段分支：
`=1` 时按第2-6节的打包格式解析；`=0` 时改为分别读取上表这些独立 List
chunk，按顶点序号一一对应组装成同样的顶点属性数组，两条路径最终产出的
数据结构应完全一致，可以直接复用后续的蒙皮/材质处理逻辑。这次发现在
本轮编写 `export_alex_body.py` 导出脚本、对 `AlexVestShape` 实测时才浮现，
说明**同一个角色的不同部位/不同版本资源，存储格式变体不能想当然假设一致，
每个新部位/新角色都应该先检查 `memory_imaged` 标志再决定解析路径**。

## 9. Skeleton_2 / Skeleton_Joint_2 —— 骨架层级与静止姿势矩阵，已完整解码并验证

Alex 用的是"2"版本骨架格式（`Skeleton_2 = 0x00023000`,
`Skeleton_Joint_2 = 0x00023001`；老版本 `Skeleton=0x4500` /
`Skeleton_Joint=0x4501` 在本模型中未使用）。定位方式：
`type_filter=0x00023000` 枚举出 `alex_reg_arms_skeleton` 等骨架节点。

### 9.1 Skeleton_2 (0x00023000) 头部

```
StringAlignedU8 name          # 骨架名，如 "alex_reg_arms_skeleton"
uint32          version       # 实测=1
uint32          num_joints    # 实测=67
uint32          num_partitions# 实测=13（对应 Skeleton_Partition 子chunk数，未展开分析）
uint32          num_limbs     # 实测=4（用途未知，可能与IK链或武器挂点分组有关）
```
注意 `StringAlignedU8` 在此处的语义与 gibbed-prototype 的
`ReadStringAlignedU8`说明文字略有出入但字节布局一致：**长度字节存的
是"补零对齐到4字节倍数后的字符串字段总长度"（不含长度字节本身），
不是原始字符串长度**——例如 `"alex_reg_arms_skeleton"` 长22字符，
长度字节实测为24（22补齐到24），字符串取前22个非零字符即可（用
`.rstrip(b'\x00')` 简单处理即可，不需要精确计算补零位数）。

### 9.2 Skeleton_Joint_2 (0x00023001) 每个骨骼节点

```
StringAlignedU8 name           # 骨骼名，如 "Pelvis", "Hip_L", "Knee_L" ...
uint32          parent          # 父骨骼在本骨架内的顺序索引（0-based，深度优先出现顺序）
                                 # 根骨骼(idx=0)的 parent 字段等于自己的索引(0)，即"自环"作为根的标记
float[16]       rest_pose       # 4x4矩阵，行主序(row-major)，是相对于父骨骼的局部变换
                                 # （不是世界空间！需要沿父链级联相乘才能得到世界/bind pose矩阵）
byte[54]        trailing        # 額外54字节，在本模型全部67个骨骼中取值完全相同（见下方hex），
                                 # 推测是与老版本 SkeletonJointChunk 的
                                 # DOF/FreeAxis/PrimaryAxis/SecondaryAxis/TwistAxis (5×int32=20字节)
                                 # 类似的关节约束/IK参数，本模型未使用非默认值，
                                 # 渲染/蒙皮阶段可以直接忽略，无需解析其内部字段。
                                 # 完整hex(供参考): 00000000 00000000 00000000 0000803f 0000803f 0000803f
                                 #   00000000 00000000 00000000 00000000 00000000 00000000 01000100 00000000
```

矩阵是行主序、行向量约定（`v' = v * M`），级联世界矩阵的算法：
```
world[0] = rest_pose[0]                                  # 根节点：局部即世界
world[i] = rest_pose[i] * world[parent[i]]   (i != 0)     # 矩阵乘法顺序：局部矩阵在左
```

**验证结果（已用实际67骨骼数据完整通过）**：
1. 层级结构合法：除根节点(idx=0, `Motion_Root`)外，所有 `parent` 索引均严格小于自身索引
   （保证是深度优先顺序排列的树，无环、无前向引用）。
2. 骨骼命名完全符合标准人形骨架拓扑：
   `Motion_Root → Balance_Root → Character_Root → Pelvis` 分叉出
   `Hip_L/R → Knee_L/R → Ankle_L/R → Ball_L/R`（双腿）和
   `Spine_1→2→3 → Clavicle_L/R → Shoulder → Elbow → Forarm → Wrist → 5根手指(Base→中节→End)`（双臂+手指）、
   `Neck → Head → Jaw/Brow/EyeLid/Eye_L/Eye_R`（面部骨骼）、
   以及 `Root_Grapple` / `L_Wrist_Grapple` / `R_Wrist_Grapple`（游戏内"钩爪"技能用的挂点骨骼，Prototype特有）。
3. 局部平移量是合理的人体骨长：大腿(Hip→Knee)长0.44、小腿(Knee→Ankle)长0.42，左右腿骨长完全对称（仅x符号相反）。
4. 级联计算出的世界坐标构成一个合理的站立人形（T/A-pose）：
   ```
   Motion_Root: y=1.0000        (根节点位于身体中部略下)
   Pelvis:      y=1.0669
   Hip_L:       y=0.9706, x=0.0996
   Knee_L:      y=0.5305
   Ankle_L:     y=0.1130        (接近地面，符合脚踝高度)
   Head:        y=1.6803        (合理的成年男性身高)
   Wrist_L:     x=+0.5062, Wrist_R: x=-0.5062  (左右手腕对称，符合T-pose/A-pose张开姿态)
   ```
5. **与第5节 Matrix_Palette 的骨骼索引精确吻合**：
   `alex_reg_body_alex_bodyShape` 的调色板索引 `[18,10,11,9,8,3,12,4,...]`
   对应到本骨架的 `[Forarm_L, Ankle_R, Ball_R, Knee_R, Hip_R, Pelvis, Spine_1, Hip_L, ...]`
   —— 全部是身体主干和四肢的骨骼，与"身体网格"应该关联的蒙皮骨骼完全符合直觉，
   证明 Matrix_Palette 里的索引确实是"索引进同一个骨架的关节顺序列表"这一假设正确。

### 9.3 关键结论：蒙皮渲染完整数据链已打通

至此，从顶点到最终变形姿势的完整数据链已经全部解码并交叉验证：
```
顶点.blend_index[k] （0..3, 见第3a节）
    -> 查 PrimitiveGroup 的 Matrix_Palette（0x0001000D, 见第5节）第k项
    -> 得到该 PrimitiveGroup 所属骨架（Skeleton_2）内的关节顺序索引
    -> 查该 Skeleton_Joint_2 的世界/bind-pose矩阵（沿父链级联，见9.2节算法）
    -> 顶点最终位置 = Σ_k blend_weight[k] * (vertex_local_pos * bindpose_inverse[k] * current_pose[k])
       （标准线性混合蒙皮 LBS 公式；bindpose_inverse 需要对bind-pose世界矩阵求逆）
```
后续导出 glTF 时，可以直接：
- 把每个 Skeleton_Joint_2 映射成一个 glTF `node`（层级关系用 `parent` 字段还原）
- 把 rest_pose 矩阵（转置或按 glTF 列主序要求调整）作为节点的 local transform
- 用级联算出的世界矩阵的逆，作为 glTF skin 的 `inverseBindMatrices`
- 顶点的 blend_index 需要先通过 Matrix_Palette 转换成"骨架关节顺序索引"，
  再重新映射成"该 mesh 的 glTF skin.joints 数组下标"（glTF 要求 JOINTS_0
  attribute 里的索引是相对 skin.joints 数组的局部索引，不是全局骨架索引）

## 10. 待解码（后续步骤，更新）

- ~~`0x00010001` (Skin) 本体绑定关系~~ **已解码，见第8节**。仍待确认：
  之前发现的 `alex_reg_arms_skeleton` 与 `alex_reg_body_skeleton` 究竟是
  同一骨架的不同引用名、还是身体和手臂真的用了两套独立骨架——需要在
  alex.p3d.rz 全文件范围内搜索所有出现的 `skeleton_name` 取值去重后确认，
  并逐一核对每个是否都能在同一文件里找到对应的 `Skeleton_2` chunk。
- `0x00010021` (Vertex_Compression_Hint，32字节)：8个uint32，值全是0或0x20(32)，
  用途待查（可能是每个属性的量化精度提示，与压缩/量化有关，正常渲染流程可忽略）。
- `0x00122000` (Sort_Order，8字节)：2个float，值为(0.0, 0.5)，可能是渲染排序权重，可先忽略。
- `0x00010017` (Render_Status，4字节)：uint32=1，可能是"是否可见/启用"标志，可先忽略。
- `Skeleton_Partition` (0x00023002, num_partitions=13个)：用途未知，可能与LOD或蒙皮分组优化有关，
  正常渲染流程理论上可以忽略，优先级低。
- ~~Skin (0x00010001) 与 CompositeDrawable 的关系~~ **已解码，见第8b节**——
  `Composite_Drawable_2` + `Composite_Drawable_Primitive` 就是比 Skin 更高一层的"部位组"总装配结构。
- ~~材质/贴图关联~~ **已解码并链路闭环验证，见第8c节**——
  `PrimitiveGroup.shader_name → NewShader → 参数字典 → Texture chunk → TextureDDS → Image_Data`。
  剩余待办：尚未实际生成一个 `.dds` 文件验证像素数据能否被标准工具正确打开
  （已知格式定义，仅未做"落地生成文件"这一步的实测），留待编写贴图导出脚本时一并完成。
- ~~"下一步优先级"~~ **已完成落地验证**：`tools/p3d_export/export_alex_body.py`
  已成功把 `alex_reg_body` 组（身体+头+外套三个Skin，共享 `alex_reg_body_skeleton`
  67根骨骼）完整导出成一个可用的 `.glb` 文件（含蒙皮权重/骨骼层级/baseColor贴图），
  证明本文档记录的全部格式定义可以直接拼装出合法的 glTF 2.0 二进制文件，
  不只是"理论上正确"。过程中意外发现并解决了第8d节的"老式独立List格式"
  分支（外套用的是与身体/头部不同的顶点存储变体）。剩余次要待办
  （Skeleton_Partition用途、0x0900000A节点用途、normal/specular贴图暂未接入
  glTF输出等）优先级较低，可按需回头补充，不阻塞主线推进。


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
