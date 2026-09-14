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

### 基础 Cell 的完整 bounds 普查已完成

`/api/rcf_cell_geometry_manifest` 已对全部 260 个基础 Cell 做了本机、metadata-only 的 Geometry/POSITION census：

- 149 个非 placeholder Cell 全部成功解析，111 个 33-byte placeholder 被显式记录，解析错误为 0；
- 共得到 7,546 个 Geometry 和 25,900 个具有可读 POSITION bounds 的 PrimitiveGroup；
- 合并后的 POSITION 范围为 X `[-1759.354, 1751.372]`、Y `[-32.572, 351.150]`、Z `[-2237.141, 1696.998]`；这为“Cell 几何使用同一世界尺度/坐标空间”提供了直接证据，但接缝仍要通过相邻 Cell 实际拼接验证；
- 静态 MemoryImage 顶点不是一个固定格式：观察到 18 种 stride（20–92 bytes；最常见 68、28、64、36、52 bytes）。因此第一版地图导出不能把 Alex 的 56-byte 蒙皮顶点布局或某一个 Cell 的 static layout 套用到整张地图。

这不是解析错误，而是一个已经量化的格式分支：地图预览器必须先按 static vertex stride/description 分类并验证 POSITION、NORMAL、UV 的实际偏移，才能写入一张正确贴图的 GLB。

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

1. 选一组相邻/不同编号的非空基础 Cell，读取 Geometry 顶点 position 的 bounds，验证它们是否已经处在共同世界坐标，或需要索引→格点变换；
2. 对照 Cell 的 PrimitiveGroup shader 名和 `art.rcf` 的纹理资源，建立只含引用名的材质依赖表；
3. 用低细节、不含 gameplay `_ft` 的 2–4 Cell 拼装预览做 seam / 坐标验证；
4. 只有在相邻 Cell 无错位、UV/纹理绑定正常后，才扩到全 149 个非空基础 Cell；
5. 再决定 mini / midgeo / height map / `_ft` 是否作为独立图层和何时加载。

如果前 2–4 Cell 出现系统性的坐标、顶点布局、UV 或材质错误，应停止扩大范围并先记录/说明；不得把坏的单 Cell 转换批量复制为“全曼哈顿预览”。

## 未来地图界面

地图不会混入角色的“文物货架”。展厅里应有独立的“场景 / 曼哈顿”入口：

- 大图或俯视概览进入；
- 260 格 Cell 覆盖层，区分基础 geometry、placeholder、未验证和已验证；
- 单 Cell 开关、邻格拼装检查与加载状态；
- 鼠标拖拽绕视角、滚轮缩放，且以距离流式加载；
- 人物可作为后续独立 overlay 加入，不把角色模型烘焙进城市基础几何。
