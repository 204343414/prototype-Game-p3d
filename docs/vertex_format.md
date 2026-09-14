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
- Skin (0x00010001) 与 CompositeDrawable 的关系：需要搞清楚一个角色完整模型是如何从多个
  PolySkin部位 + 多个Skeleton + 材质/贴图引用组装起来的高层结构，这是下一步写导出脚本前
  必须理清的"总装配图"。


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
