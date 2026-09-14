# Chunk ID 对照表：Prototype ↔ The Simpsons: Hit & Run (NetP3DLib)

两个游戏都基于 Radical Entertainment 的 Pure3D 引擎，但版本不同、chunk ID
分配也不完全一致（Hit & Run 更老，Prototype 是后期引擎的重度魔改版）。
本表把 `gibbed-prototype`（Prototype 专用，字段已知）和 `netp3dlib`
（Hit & Run 专用，字段最全、且有完整的顶点/骨骼/动画二进制布局）的
已知 chunk 定义做交叉映射，供写 Prototype 端解析器时参考字段结构。

**图例**：
- ✅ = 字段结构已确认一致（两边独立实现但字段顺序/类型相同）
- 🟡 = 语义相同但版本不同（字段有差异，需要分别处理）
- ❓ = 仅一边有实现，尚未验证是否兼容
- Prototype 端字段来自 `references/gibbed-prototype/.../Pure3D/*.cs` 的 `KnownType` 特性
- Hit&Run 端字段来自 `references/netp3dlib/.../P3D/Enums/ChunkIdentifier.cs` + 对应 Chunk 类

## 核心网格/骨骼/动画链路（最重要，做 glTF 导出必须懂）

| 语义 | Prototype ID (hex) | Prototype 类名 | Hit&Run ID (hex) | Hit&Run 类名 | 状态 | 备注 |
|---|---|---|---|---|---|---|
| 网格容器 | `0x00010000` | `Geometry` | `0x00010000` (`Mesh`) | `MeshChunk` | ✅ | 字段一致：Name, Version, NumPrimitiveGroups |
| 蒙皮网格容器 | `0x00010001` | `PolySkin` | `0x00010001` (`Skin`) | `SkinChunk` | 🟡 | Prototype: Name,Unknown1,SkeletonName,Unknown3；H&R: Name,Version,SkeletonName,NumPrimGroups。字段数量对不上，**Unknown1/Unknown3 到底是什么需要用真实文件验证** |
| 图元组（真正的顶点/索引数据） | `0x00010020` | `U00010020_PrimitiveGroup` | `0x00010020` (`Primitive_Group`) | `PrimitiveGroupChunk` | ✅ | 字段完全一致：Version, ShaderName, PrimitiveType, VertexType, NumVertices, NumIndices, NumMatrices, MemoryImaged, Optimized, VertexAnimated, VertexAnimationMask —— **这是最重要的一致性证据，说明两边核心网格格式同源** |
| 顶点位置列表 | *(子chunk，Prototype端未见对应 KnownType，会落入 Unknown)* | — | `0x00010005` (`Position_List`) | `PositionListChunk` | ❓ | H&R: uint32 NumPositions + Vector3[]。**需要用真实 Prototype 网格文件验证 ID 是否相同** |
| 顶点法线列表 | 同上未知 | — | `0x00010006` (`Normal_List`) | `NormalListChunk` | ❓ | 同上，结构：uint32 Count + Vector3[] |
| UV 列表 | 同上未知 | — | `0x00010007` (`UV_List`) | `UVListChunk` | ❓ | 结构：int32 NumUVs + uint32 Channel + Vector2[]，注意 Channel 字段在 Count 后面 |
| 索引列表 | 同上未知 | — | `0x0001000A` (`Index_List`) | `IndexListChunk` | ❓ | 结构：uint32 NumIndices + uint32[] |
| 权重列表（蒙皮） | 同上未知 | — | `0x0001000C` (`Weight_List`) | `WeightListChunk` | ❓ | 结构：uint32 NumWeights + Vector3[]，另有 `1-sum` 隐含权重；它与 raw Matrix_List 的槽位排列是布局相关的。已验证拆分 List 路径的输出顺序为 `[implicit,stored2,stored0,stored1]`，详见 vertex_format.md §8.5.1。 |
| 矩阵索引（每顶点绑定的骨骼） | 同上未知 | — | `0x0001000B` (`Matrix_List`) / `0x0001000D` (`Matrix_Palette`) | `MatrixListChunk` / `MatrixPaletteChunk` | ❓ | Matrix_List: 每顶点 4 个 byte（骨骼索引，ABCD 顺序注意是倒序写入 DCBA）；Matrix_Palette: uint32 数组，是 PrimitiveGroup 用到的全局骨骼索引表 |
| 骨骼容器 | `0x00023000` | `Skeleton` | `0x00023000` (`Skeleton_2`) | `Skeleton2Chunk` | ✅ | 字段一致：Name, Version, NumJoints, NumPartitions, NumLimbs。**注意 H&R 还有一个更老的 `Skeleton`=`0x4500`，Prototype 用的是新版 ID，对应 H&R 的 `Skeleton_2`，不要弄混** |
| 骨骼关节 | *(子chunk，Prototype端未见对应)* | — | `0x00023001` (`Skeleton_Joint_2`) | `SkeletonJoint2Chunk` | ❓ | 结构：Name(P3DString), Parent(uint32,父关节索引), RestPose(Matrix4x4，绑定姿势矩阵)。**比老版 SkeletonJointChunk (0x4501) 简单很多**，没有 DOF/FreeAxis 等编辑器专用字段 |
| 动画容器 | `0x00121000` | `Animation` | `0x00121000` (`Animation`) | `AnimationChunk` | ✅ | 字段完全一致：Version, Name, AnimationType(FourCC/enum), NumFrames(float), FrameRate(float), Cyclic(uint32/bool) |
| 动画分组 | *(未见对应)* | — | `0x00121001` (`Animation_Group`) | `AnimationGroupChunk` | ❓ | 每个 AnimationGroup 对应一根骨骼/一个可动对象，内部挂多个 Channel chunk |
| 四元数动画通道（旋转关键帧，未压缩） | *(未见对应)* | — | `0x00121110` (`Quaternion_Channel`) | `QuaternionChannelChunk` | ❓ | 结构：Version, Param(FourCC,通常是骨骼名/轴标识), NumFrames, Frames(ushort[]，帧号), Values(Quaternion[]，**注意 W 在前**) |
| 四元数动画通道（压缩） | *(未见对应)* | — | `0x00121111` (`Compressed_Quaternion_Channel`) | `CompressedQuaternionChannelChunk` | ❓ | 同上但 Values 用 int16 定点数编码（除以 32767 还原到 [-1,1]），**是动作类游戏最常用的旋转关键帧压缩方式，Prototype 大概率用这个** |

## 纹理/贴图

| 语义 | Prototype ID | Prototype 类名 | Hit&Run 对应 | 状态 | 备注 |
|---|---|---|---|---|---|
| 纹理容器 | `0x00019000` | `Texture` | `0x00019000`? (`ImageChunk`? 需核实) | 🟡 | 已用样本文件验证 Prototype 侧结构：子节点含 name+编号 |
| PNG 内嵌纹理 | `0x00019001` | `TexturePNG` | — | ✅(单边验证) | 已在 `dummy.p3d` 样本中实测：payload 内直接是完整 PNG 文件字节流（`89 50 4E 47 ...IHDR`），非常好处理 |
| 纹理原始数据 | `0x00019002` | `TextureData` | — | 🟡(有矛盾待验证) | 实测于 `dummy.p3d`，是 TexturePNG 的子chunk，装的就是 PNG 字节本体。**但** 2009年3DM工作室工具`P3DManipulator.exe`（反编译验证，见`docs/2009-3dm-tool-findings.md`）把独立导出的`0-00019002.bin`文件当成"开头4字节+DDS数据"处理（转DDS时去掉前4字节）。两者可能并不矛盾：`0x00019002`也许是通用"纹理原始字节"容器，内容视贴图编码方式而定（PNG或DDS），4字节开头在DDS场景下可能是一个长度字段。需要用真实游戏资源（尤其角色贴图，大概率是DDS而非PNG）验证具体是哪种情况 |
| DDS 纹理 | `0x00019006` | `TextureDDS` | — | ❓ | Prototype 角色贴图大概率用这个（DDS压缩纹理），比PNG常见，需要真实资源验证 |
| 纹理来源/外部引用 | `0x00019003` | `TextureSource` | — | ❓ | 猜测是指向外部贴图文件的引用，而非内嵌 |
| 索引文本/字符串表（编号推测） | `0x00018201` + `0x00018202` | 未在gibbed/netp3dlib中确认对应类名 | — | 🆕(新发现,来自3DM工具反编译) | 见`docs/2009-3dm-tool-findings.md`：`0x00018202`是字符串索引表(char/offset/length三元组数组)，`0x00018201`是被索引指向的实际字符数据块，两者配对使用，3DM工具称其可转换为"带索引Agemo文本"，疑似本地化/多字节文本相关chunk。此前完全未记录，需要在真实文件里定位验证 |


## 复合可绘制对象（CompositeDrawable，Prototype 特有的高层封装）

Prototype 引入了 `CompositeDrawable`（0x00123000 系列）这一层，把
"一个骨架 + 多个 PolySkin(蒙皮网格) + 表情混合器"打包在一起，对应游戏里
一个完整可渲染角色。Hit&Run 也有类似概念但 ID 不同
（`Composite_Drawable_2 = 0x00123000`，凑巧同 ID！可能是同版本的证据）：

| 语义 | Prototype ID | 字段（来自 gibbed 源码） |
|---|---|---|
| 复合可绘制对象根节点 | `0x00123000` `CompositeDrawable` | Version, Name, SkeletonName, NumPrimitives |
| 蒙皮网格引用 | `0x00123001` `CompositeDrawablePolySkinReference` | Unknown1, Unknown2, PolySkinName, Unknown4, Unknown5 |
| 表情混合器引用 | `0x00123003` `CompositeDrawableExpressionMixerReference` | Unknown1, ExpressionMixerName |

**这是实际导出角色模型时最重要的入口点**：先找 `CompositeDrawable`，
读它的 `SkeletonName` 找到对应骨骼，再遍历子节点里的
`CompositeDrawablePolySkinReference` 找到所有蒙皮网格部件（Prototype
角色经常是"身体+衣服+武器"分成多个 PolySkin，一起绑到同一副骨架上）。

## 其它已确认的 Prototype 专有 chunk（供完整性参考）

| ID | 类名 | 用途 |
|---|---|---|
| `0x07F00000` | `MetaObjectDefinition` | 游戏对象元数据定义（关卡编辑器用，非美术资源） |
| `0x07F00001` | `MetaObjectData` | 游戏对象元数据实例，任务/关卡文件里大量出现（见 `e09m01_tod.p3d` 样本） |
| `0x20000701` | `FightDefinition` | 格斗系统定义（`.fig` 文件的根节点） |
| `0x20000702` | `FightData` | 格斗系统原始数据块 |
| `0x00022000` | `TextureFont` | 字体资源（`dummy.p3d` 样本里的 `pure3d_debug_font`） |
| `0x00022001` | `TextureGlyphList` | 字形列表 |
| `0x00015880` | `ParticleSystem` | 粒子系统 |
| `0x07020000` | `Physics` | 物理属性 |
| `0xFE000000` | `Registry2` | 全局注册表/索引 |

## 下一步验证计划

1. 拿到真实的 Prototype 角色 `.p3d`（哪怕只有一个，比如 Alex Mercer 的
   `alex_fig.p3d`，社区 mod 页面提到过这个文件名），用
   `tools/p3d_parser/inspect_p3d.py` dump 出完整 chunk 树。
2. 对着这份 dump，把所有"未见对应/Unknown"的 chunk ID 记录下来，
   逐个去 `netp3dlib` 的 `ChunkIdentifier.cs` 里查有没有同 ID 的定义，
   有的话直接按它的字段结构试着解析、验证数值是否合理（比如
   NumVertices 是否和后续 Position_List 的实际数据条数吻合）。
3. 特别关注 `PolySkin` 的 `Unknown1`/`Unknown3` 两个字段到底是什么
   （对比 H&R 的 `SkinChunk` 多了一个 `Version` 和 `NumPrimitiveGroups`，
   Prototype 版本字段数不同，可能字段含义也变了，不能想当然套用）。
