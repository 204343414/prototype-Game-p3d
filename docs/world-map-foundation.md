# 全地图场景地基：曼哈顿 Cell 拼装考古

> 项目目标补充：在角色资料库之外，最终提供可浏览的**全曼哈顿静态场景预览**。
> 本文只记录用户合法本地安装中的结构元数据和验证计划；不把地图几何、贴图、游戏 archive 或导出的地图提交到仓库。

## 与角色工作并行的边界

地图分支当前只做“定位与台账”：确认档案、Cell 集合、静态几何/游戏逻辑的边界、材质来源和坐标拼装方法。它不抢占既定顺序中的角色蒙皮、角色动画和音效工作，也不提前做最终地图导出。

一个能可靠使用的整图预览必须按以下层次建立：

1. **Cell 几何布局**：正确加载并拼接静态建筑、道路、地面和可见 prop；
2. **材质与贴图绑定**：保留 shader/texture 的对应关系，不能只输出灰模；
3. **位置与层级**：证明每个 Cell 的坐标/网格编号关系，不能按文件排序硬拼；
4. **可选的 gameplay/动态物**：生成器、刷兵点、可破坏物、载具、灯光等应与静态城市分开处理；
5. **全景浏览器**：按距离分批加载/卸载 Cell，避免一次把整座城市塞进浏览器内存。

## 已定位的真实地图来源

### `cells.rcf` 是整图的主入口

2026-09-15 的只读 metadata census 发现游戏根目录有独立的 `cells.rcf`（约 633 MiB archive）。其中：

- 共 **520** 个已命名条目，且全部为 `.p3d.rz`；未知名条目为 0；
- 精确构成是 `\\art\\locations\\manhattan\\manhattan_Cell_0` 至 `manhattan_Cell_259`，以及每个编号对应的 `_ft` 同名条目；
- 即 **260 个基础 Cell + 260 个 `_ft` 配对文件**，而不是一个单独“曼哈顿.fbx”；
- 149 个基础 Cell 大于 33-byte placeholder，151 个 `_ft` 条目大于 placeholder。空条目应在全景中显示为“无静态 payload”，不是擅自当作损坏缺图。

因此，外部工具能预览“整个曼哈顿”是合理且可复现的目标：它们必须在某个层次上读取了这组 numbered Cell，而不只是导出了 `art.rcf` 中的一两个 `props.p3d`。

### 功能参照截图的交叉验证（不复制第三方工具）

用户于 2026-09-15 提供了一个第三方 Windows P3D viewer 的曼哈顿整图截图。其左侧资源树列出 `manhattan_Cell_N.p3d.rz` / `_ft`，中央是全城场景，右侧可逐 mesh 勾选；控制台可见 `Scene load progress: 260/260`，并报告 `cells=260`、`parsed=260`、`renderable=149`、`empty=111`。

这四个 Cell 统计值与本项目对真实 `cells.rcf` 的独立 metadata census **完全一致**。它是“目标效果可达、基础 Cell 集合选择正确”的外部交叉证据，但不是格式语义的唯一依据，也不复制、反编译或分发该 viewer 的代码/资源。我们自己的预览器需独立验证顶点布局、材质和实例变换。

目标体验可参考其能力而非其界面源码：全城主视窗、文件/Cell 树、每 mesh 或 Cell 的可见性勾选、加载进度/诊断，以及外部贴图解析状态。

### 基础 Cell 的完整 bounds 普查已完成

`/api/rcf_cell_geometry_manifest` 已对全部 260 个基础 Cell 做了本机、metadata-only 的 Geometry/POSITION census：

- 149 个非 placeholder Cell 全部成功解析，111 个 33-byte placeholder 被显式记录，解析错误为 0；
- 共得到 7,546 个 Geometry 和 25,900 个具有可读 POSITION bounds 的 PrimitiveGroup；
- 合并后的 POSITION 范围为 X `[-1759.354, 1751.372]`、Y `[-32.572, 351.150]`、Z `[-2237.141, 1696.998]`；这为“Cell 几何使用同一世界尺度/坐标空间”提供了直接证据，但接缝仍要通过相邻 Cell 实际拼接验证；
- 静态 MemoryImage 顶点不是一个固定格式：观察到 18 种 stride（20–92 bytes；最常见 68、28、64、36、52 bytes）。因此第一版地图导出不能把 Alex 的 56-byte 蒙皮顶点布局或某一个 Cell 的 static layout 套用到整张地图。

这不是解析错误，而是一个已经量化的格式分支：地图预览器必须先按 static vertex stride/description 分类并验证 POSITION、NORMAL、UV 的实际偏移，才能写入一张正确贴图的 GLB。

进一步对 `0x00010014` MemoryImageVertexDescription 做了结构解码：所有基础 Cell 的 25,900 个 PrimitiveGroup 收敛为 **29 种稳定的 attribute layout**。每个 layout 明确记录语义哈希、byte offset、stride、element type 和 usage index；尚未给语义哈希擅自命名。所有 29 种 layout 都把同一哈希 `0x2C929929` 放在 offset 0，读取前三个 `float32` 得到上述有意义的世界 POSITION bounds。这是 POSITION 偏移的已验证证据；UV/NORMAL/TANGENT 哈希的最终名字与 glTF 映射仍须以贴图预览回归确认。

还发现一个直接影响首版整图的边界：一个 Cell 除城市合并网格外，也可携带像直升机、灯光等**局部坐标**的可复用 Geometry。已把名称前缀为 `mergedDrawableRoot` 的 Geometry 单独标为“合并世界静态几何”：

- 143 / 149 个非空基础 Cell 有此类根，共 258 个 Geometry、16,898 个可读 POSITION PrimitiveGroup；
- 6 个非空 Cell（5、24、98、126、139、244）没有该根，不能被错误地判为整图缺失；它们保留为单独的实例/特殊内容候选；
- 首版地图只尝试导出 `mergedDrawableRoot*`，防止将 local-space 的直升机/灯具网格错误堆到世界原点。其余 Geometry 与 `_ft` 层在第二阶段通过实例/MetaObject 关系再加入。

### 第一次严格三角连接诊断：Cell 2 与 Cell 3

在不把任何 P3D 原始数据写入仓库的前提下，新增了只读、core-only 的 `tools/world/export_static_geometry_diagnostic.py`。它只接受名称前缀为 `mergedDrawableRoot` 的 Geometry，并对每个 PrimitiveGroup 同时要求：LE P3D、`primitive_type=0` TriangleList、一个 MemoryImageVertexList、一个 MemoryImageIndexList、一个 VertexDescription、已验证的 offset-0 POSITION 声明，以及严格的 `uint16` 索引字节数、三元分组和索引范围。`primitive_type=0` 的 TriangleList 枚举也可由已存在的 NetP3D 参考子模块交叉查阅；实际 Cell 的索引边界与三元结构由本项目独立检查，不靠枚举名称替代验证。

2026-09-15 对两个真实基础 Cell 的结果如下（metadata-only report 保存于私有研究目录，非仓库资产）：

| Cell | world Geometry | PrimitiveGroup | POSITION 顶点 | uint16 索引 | 三角形 | 严格错误 / 重复索引三角形 / 零面积三角形 |
|---|---:|---:|---:|---:|---:|---:|
| `manhattan_Cell_2` | CastShadow、NoShadow | 58 | 47,510 | 85,698 | 28,566 | 0 / 0 / 0 |
| `manhattan_Cell_3` | CastShadow、NoShadow | 34 | 67,878 | 100,566 | 33,522 | 0 / 0 / 0 |

两者共通过了 92 个 group、115,388 个 POSITION 顶点和 62,088 个三角形；其 bounds 与先前的独立 Cell census 一致。生成器可在显式指定 `--preview-html` 时写一个**私有、无纹理的 WebGL 三角诊断页**，用于拖拽/缩放检查同一世界坐标空间；该页面只含为显示而派生的 POSITION/索引数据，不得提交或分发。它不是材质预览，也尚不构成“接缝已验证”的结论：UV、normal、vertex color 与 shader/texture 关联仍没有经过渲染回归。

### 第一次 shader / 贴图文本依赖台账：Cell 2 与 Cell 3

`tools/world/probe_cell_shader_dependencies.py` 已在相同的 core-only 范围内读取了 `NewShader` 的 name / template header、其直属的双字符串参数以及本 Cell 的 Texture header；输出严格是文本和尺寸等 metadata，不保存任何 DDS 或 P3D payload。PrimitiveGroup 到 shader 名的引用、同 Cell NewShader 定义和 Texture 名称由真实文件交叉关联，但双字符串参数目前仅标为**图像引用候选**，不可将参数名或 template 名直接等同于已实现的 glTF 材质语义。

| Cell | core PrimitiveGroup / unique shader | local NewShader / Texture definition | 非空图像引用候选 | 能同 Cell Texture 名匹配 | 未解析 core shader / parser error |
|---|---:|---:|---:|---:|---:|
| `manhattan_Cell_2` | 58 / 58 | 101 / 64 | 94 | 50 | 0 / 0 |
| `manhattan_Cell_3` | 34 / 34 | 83 / 48 | 58 | 35 | 0 / 0 |

这说明两个 Cell 的所有当前 core PrimitiveGroup 都能在**同一 Cell**找到其 NewShader 定义，且至少部分字符串值可立即匹配同 Cell Texture 定义；剩余候选很可能需要由共享 archive / 全局资源层解析，但在实际 DDS 解码和渲染前不能宣称它们就是缺失贴图。已观察到 `zCBV2_*building*`、`*road*`、`*sidewalk*` 等 template 文本和 `color`、`normal`、`grime` 等参数文本，它们为下一步选择代表性 shader/texture 回归样本提供依据，不是 UV、normal 或光照公式的结论。

### 第一个贴图 UV 渲染候选对照（结论待目检）

依赖台账已将 Cell 3 的 `mergedDrawableRootCastShadow` 第 18 个 PrimitiveGroup 的 shader `0c63a8bb069ec0cad9034c69bc28991f` 对应到同 Cell NewShader 的 `color=manhattan_Cell_3_02_plane0` 字符串候选。该 Texture 的嵌入 DDS header 可严格读为 `256×1024`、11 mip、`DXT5`，且带 count-prefixed DDS framing；私有环境已用 DXT5 decoder 成功解码出图像，未将 DDS/PNG 写入仓库。

为避免根据 hash 猜 UV，`tools/world/render_static_uv_candidates.py` 对这个已通过严格三角检查的 group（25,509 顶点、10,704 triangles、68-byte layout）生成了左右并列的私有 WebGL 诊断页：左边读取 `0x00364509 @ offset 24`，右边读取 `0x0036450A @ offset 32`，两者都直接使用文件中的 float2，默认**不**翻转 V；页面只提供一个显式的 V→1−V 对照开关。两组值均为有限 float2（前者范围约 U `-5.000..5.001`、V `0..0.172`；后者 U `-8.993..13.501`、V `0..0.688`）。

workspace 内嵌浏览器无法显示该 WebGL 页时，工具现可同时生成确定性的 CPU/PNG fallback。第二个、信息量更高的回归样本是 Cell 2 的同一 68-byte layout `mergedDrawableRootCastShadow` group 40：24,436 顶点 / 15,837 triangles，shader 的 `color=manhattan_Cell_2_00_plane0` 候选可同 Cell 的 `256×1024` DXT1 Texture 定义匹配。对同一个解码图像、同一份三角形和同一正交相机，原始 `0x00364509 @ 24` 一侧显示出连续、非均一的立面/构件纹理细节；`0x0036450A @ 32` 一侧则在相同区域呈大块近乎纯色/错误采样。该渲染差异足以把 **`0x00364509 @ offset 24` 定为该 68-byte declaration 的“primary color-texture coordinate”候选**，并排除 offset 32 作为这个 color sampler 的等价替代。

这个结论仍有明确上限：尚未决定该 hash 的通用显示名、没有把 32 命名为第二 UV、没有裁定 V 轴方向，也没有将规则套到其余 28 layouts；normal、tangent、vertex color 和完整 shader 公式也仍需单独回归。

### `art.rcf` 提供共享美术资源

在 `art.rcf` 内已定位到 290 个 `\\art\\locations\\manhattan...` 相关 P3D 包（压缩总量约 87.5 MiB），包括：

- `textures.p3d`、`props.p3d`、`midgeo.p3d`；
- exterior / interior height map；
- `manhattan_mini` 的小规模/替代资源；
- 关卡元数据目录。

初步整图不能简单把这些包和 260 个 Cell 全部盲合并：它们的作用、引用方向、LOD/mini 关系与坐标仍需由 P3D 结构和实际预览验证。

## 对一个真实 Cell 配对的结构核验

为避免把 `_ft` 名字凭空扩写，私有环境只读检查了 `manhattan_Cell_2` 及其同号 `_ft`：

| 条目 | 解压后大小 | 已验证结构 | 当前解释 |
|---|---:|---|---|
| `manhattan_Cell_2.p3d.rz` | 9,525,700 bytes | 45 个 `0x00010000` Geometry、108 个 `0x00010020` PrimitiveGroup，且含 texture/shader 条目 | **静态城市几何候选**。其中出现合并可绘制根、道路/建筑材质等；应作为 Cell 渲染管线的第一类输入。 |
| `manhattan_Cell_2_ft.p3d.rz` | 162,137 bytes | 9 个 FightDefinition、484 个 MetaObjectDefinition + 484 个 MetaObjectData；未出现上述 Geometry/PrimitiveGroup 组 | **非静态几何层**。当前只可确认其承载 gameplay/对象定义类数据；第一版整图应分开记录、默认不拿它当建筑网格。 |

这个结论是“首版静态地图可先以基础 Cell 为核心”的证据，**不是**对 `_ft` 全称或最终游戏语义的猜测。

## 下一步的可验证工作

1. POSITION + uint16 TriangleList 的 core-only 诊断已在 Cell 2 / 3 通过；68-byte declaration 的 primary color-texture candidate 已在 Cell 2 group 40 获得 `0x00364509 @ 24` 的渲染证据。下一步验证 NORMAL / tangent / vertex color，再只把已验证部分扩到相同 declaration；其余 28 layouts 仍不能从 hash 外观猜名称；
2. 对照 Cell 的 PrimitiveGroup shader 名和 `art.rcf` 的纹理资源，建立只含引用名的材质依赖表；
3. 用 `mergedDrawableRoot*` 的 2–4 个空间相邻 Cell 做低细节**有材质**拼装预览，检查 seam / 坐标 / UV；不混入 local-space Geometry 或 gameplay `_ft`；
4. 只有在相邻 Cell 无错位、UV/纹理绑定正常后，才扩到 143 个有合并世界根的非空基础 Cell；
5. 再决定其余 6 个特殊 Cell、mini / midgeo / height map / `_ft` 是否作为独立图层和何时加载。

如果前 2–4 Cell 出现系统性的坐标、顶点布局、UV 或材质错误，应停止扩大范围并先记录/说明；不得把坏的单 Cell 转换批量复制为“全曼哈顿预览”。

## 未来地图界面

地图不会混入角色的“文物货架”。展厅里应有独立的“场景 / 曼哈顿”入口：

- 大图或俯视概览进入；
- 260 格 Cell 覆盖层，区分基础 geometry、placeholder、未验证和已验证；
- 单 Cell 开关、邻格拼装检查与加载状态；
- 鼠标拖拽绕视角、滚轮缩放，且以距离流式加载；
- 人物可作为后续独立 overlay 加入，不把角色模型烘焙进城市基础几何。
