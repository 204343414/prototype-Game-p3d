# 角色资产地基：目录、身份核验与未来预览约定

> 本文是角色工作开始前的**核验台账与界面约定**，不是最终导出清单。
> 它只记录从用户本人合法安装中读取到的名称/结构元数据；不提交、打包或分发任何游戏模型、贴图、动画或音频。

## 先后顺序

1. **先验证资产事实**：角色身份、变体、骨架、skin primitive 数与顶点布局必须分别记录；不可由文件名或记忆直接推断为已导出正确。
2. **再建可复用的角色目录**：每个已核验角色以一个稳定 `character_id` 和若干 `variant_id` 进入台账；模型、骨架、动画和验证状态分别挂接。
3. **之后才是预览展厅**：展厅以这个目录驱动，不把临时的调试 GLB 或猜测的动画当成最终资源。
4. **最终批量导出最后做**：只有角色外观、蒙皮和动画都逐项通过目检后才批量生成最终文件。

这保留了当前项目的优先级：动画基础 → 所有角色 → 所有角色动画 → 音效 → 最终导出。

## 本轮 NIS 结构普查（已核验）

2026-09-15 对 `art.rcf` 做了只读的条目元数据台账；在其中选出 **53 个压缩大小至少 1 MB 的主 NIS 场景包**，逐个请求其 `CompositeDrawable` 头部结构（无 raw payload 写入仓库）。结果共得到 194 个 CompositeDrawable 记录。

随后仅为核验结构，在用户授权的私有临时目录中提取并检查了少量候选 P3D；下表中的 primitive 数是 CompositeDrawable 直接子记录数，而非从截图猜出的“看起来有几片”。`type=2` 为 polyskin；`type=1` 是刚性/挂点类 primitive。这个区别必须保留。

| 角色/候选 | 已确认来源（私有安装中的 entry） | CompositeDrawable / skeleton | 已确认直接 primitive | 状态与结论 |
|---|---|---|---|---|
| 过场 Alex | `\\art\\nis\\NIS_7_3_a.p3d.rz`（许多其他 NIS 场景也复用） | `nis_alex` / `nis_alexSkeleton`（94 joints） | 4 个 type=2：`alex_headShape`、`alex_armsShape`、`alex_jacketShape`、`alex_bodyShape`；另有 `noseShadowFix2Shape` type=1 | **高细节过场 Alex 已定位**。它是普通 gameplay Alex 的独立候选，尚未把它纳入最终导出。 |
| 过场黑色守望小兵 | `\\art\\nis\\NIS_7_3_a.p3d.rz` | `nis_bw_trooper_2008` / `nis_bw_trooper_2008Skeleton`（62 joints） | 3 个 type=2：Head、Torso、Leg | **已定位**。这正是一个明确的“三个蒙皮 primitive”角色，但它是 NIS Blackwatch trooper，不应误标为超级士兵。 |
| 过场黑色守望军官 | `\\art\\nis\\NIS_0_4.p3d.rz` | `nis_bw_officer` / `nis_bw_officerSkeleton`（62 joints） | 3 个 type=2：Leg、Torso、Head | **已定位**。外观名称、用户记忆中的“上尉/军官”等具体身份仍须目检确认，暂不擅自等同。 |
| 过场无枪 Specialist | `\\art\\nis\\MNIS_4_4_2.p3d.rz` | `nis_specialist_noGun` / `nis_specialist_noGunSkeleton`（90 joints） | 3 个 type=2：Torso、Head、Legs | **已定位**，是独立的高细节候选。它是否对应用户所说的特殊兵种，留待预览和用户辨认。 |
| 过场超级士兵 | `\\art\\nis\\NIS_7_3_a.p3d.rz` | `NIS_supersoldier` / `NIS_supersoldierSkeleton`（67 joints） | 1 个 type=2：`SuperSoldier_NISShape` | **与“三 mesh 完整超级士兵”记忆不吻合**。目前只能确认这个 NIS 包里它是一个 polyskin primitive；不能把它说成三 mesh，也不能据此排除另一个未定位的变体。 |
| 普通超级士兵 | `\\art\\packages\\missions\\SuperSoldier\\SuperSoldier.p3d.rz` | `supersoldier` / `supersoldier_master_skeleton_skeleton` | 2 个 type=2 | 已在此前确认；不是上述三-mesh 记忆的直接证据。 |

### 与用户关心的兵种对应的 gameplay 候选（结构已核验，身份待目检）

`\\art\\packages\\missions\\Soldier\\Soldier.p3d.rz` 是一个汇合包：它包含黑色守望军官、小兵、飞行员和 `ped_sold_general` 等多个独立 CompositeDrawable。它们共享 67-joint、带 parts/limbs 的 gameplay 骨架族，但**共享骨架不等于共享外观、材质或动画兼容性已经验证**。

| 暂定目录位置 | 已确认 drawable / skeleton | 直接 type=2 primitive | 备注 |
|---|---|---:|---|
| 黑色守望小兵（gameplay） | `bw_trooper_2008` / `bw_trooper_2008_skeleton` | 1：`FullBody_00` | 同包也有 `_01` 变体；`bwtrooper_disguise` 私有包复核了这组结构。 |
| 黑色守望军官（gameplay） | `bw_officer` / `bw_officer_skeleton` | 1：`FullBody_00` | 同包也有 `_01` 变体；`bwofficer_disguise` 私有包复核了这组结构。 |
| 指挥官/一般士兵候选 | `ped_sold_general` / `ped_sold_general_skeleton` | 1：`FullBody_00` | 来源名字支持“general”这一候选，但不把它自动翻译成用户记忆中的具体军衔。 |
| 特殊驾驶/飞行人员候选 | `ped_m_pilot_cau` / `ped_m_pilot_cau_skeleton` | 2：UpperBody、LowerBody；另有 `_01` 的 1 个 FullBody 变体 | 这是目前最接近“特殊驾驶兵”的**名称候选**；需由实际预览和用户记忆确认。 |
| Specialist（gameplay） | `specialist` / `specialist_skeleton` | 3：head + 两个 body polyskin | 与 NIS `nis_specialist_noGun` 是不同 candidate，骨架/拆分数不同。 |

“海军陆战队”目前不能仅凭 `art.rcf` 路径中含 `marine` 的 vehicle 或 infected 条目就认定为目标步兵。它保留为一个待定位的阵营/外观目标；下一步先依据完整目录和用户目检建立正确映射，而不是依文件名强行归类。

## 角色台账的最小记录

后续每个条目至少要有下列字段，缺失时应显示为“未核验”而不是补猜：

- `character_id`：稳定目录 ID，例如 `blackwatch_trooper`（不依赖源文件偶然改名）。
- `variant_id`：gameplay / NIS / 服装或损伤变体等。
- `display_name`：面向展厅的名称；若用户确认“上尉”或其他记忆称呼，记录为用户确认的别名，不覆盖源证据。
- `source_entry`：用户私有安装中的 RCF entry 名，只作为可重现定位信息。
- `drawable_name`、`skeleton_name`、`joint_count`、`skin_primitive_count`：来自实际 P3D 结构扫描的事实。
- `mesh_layout_status`：`uninspected` / `validated` / `blocked`；必须按各 mesh 独立记录，防止重演 Alex 夹克的 Weight_List 错配。
- `static_preview_status`、`animation_preview_status`、`blocked_reason`、`verification_evidence`。

## 全量“带骨架货架”普查（不按名称预筛）

用户选择的第一轮范围是：**全 `art.rcf`，不按文件名、阵营或目录预筛，只按真实 P3D 结构判断。** 因此先用一个 metadata-only census 逐项读取所有已命名的 `.p3d.rz` 条目；执行策略不是“只找角色”，而是先找出有命名骨架且直接引用至少一个 `type=2` polyskin 的 CompositeDrawable。

2026-09-15 的完整结果：

- 扫描：2,219 个 `.p3d.rz` 包，累计压缩大小约 559.5 MiB；
- 命中：236 个包、491 个 rigged CompositeDrawable；
- 未命中该严格结构条件：1,983 个包；解析错误：0；
- 结果排序：按**解压后的实际 P3D 大小由大到小**，同时保存压缩大小，绝不以名称大小或截图印象替代；
- 结果只包含路径、大小、骨架/CompositeDrawable/primitive/shader 名称和计数，不含任何顶点、贴图像素、动画 key 或其它资源 payload。

这就是之后“非筛选货架”的后端地基：它会刻意包含非人物候选。例如排在最大两项的是 `infectedSpawnerDoor` / `infectedSpawnerWall` 等地点 prop；它们不会因我猜测“不像角色”而被悄悄删掉。相反，角色、路人、载具、可动机关、废墟中的绑定物都可先显式出现在货架，再由预览和人工判断分类。

每一个 CompositeDrawable 已生成一个稳定 review ID，例如 `ARC-ADF91921-04`。ID 来自 RCF 条目哈希和该包中的 primitive ordinal，不依赖不可靠的显示名称。未来缩略卡和用户消息都使用这个 ID：例如“`ARC-…-04` 是黑色守望小兵，重点修缮；`ARC-…-02` 是环境物，不做角色”即可精确回填台账。

注意：当前这 491 个条目已经是**可审计的候选物品栏**，但还不是 491 张假装正确的 3D 渲染图。批量缩略图之前仍需要把通用静态 P3D→GLB 路径按每个顶点布局/骨架组合验证；否则会把 Alex 夹克曾经出现过的蒙皮错误成批复制到货架。缩略图生成是下一层，不能跳过这道地基。

## 未来预览展厅的交互约定（暂不替代当前调试页）

当前 Alex 页面是为了验证 skinning 和 ROT-only 动画的工程调试页，因此刻意简洁。稳定资产目录建立后，展厅应是：

1. **大图入口**：每个阵营或章节有一张大幅背景/主题图。
2. **“文件夹式”角色卡**：角色卡使用同一套中性灯光、同一机位自动拍摄的 3D 缩略图，而不是临时截图；点击卡片进入该角色。
3. **角色详情**：有可旋转、缩放的 3D 模型以及“模型 / 变体 / 动画 / 骨架检查”分区。游戏资源可用性、尚未验证的 TRAN 和任何 blocked 项必须显式标识。
4. **动画详情**：点击动画条目后播放；时间轴永远可见、可拖动、可精确回到 0 秒。ROT-only 和完整 TRS 动画必须视觉上明确区分。

鼠标预设应避免把看模型和控制动画混在一起：

- **拖拽模型**：环绕旋转；滚轮：缩放。这已经是现有 Alex 调试预览的实际行为。
- **时间轴上滚轮**：逐步前/后拖动动画时间；默认只在指针位于时间轴区域时生效，避免用户想缩放模型时误跳帧。
- **双击 / 重置按钮**：回到标准观察机位；播放、暂停和逐帧/精确拖动保持独立按钮。

等目录里至少有一批经过外观核验的角色后，再制作这个展厅外观；现在先把来源、骨架、变体、蒙皮布局和动画状态打实。
