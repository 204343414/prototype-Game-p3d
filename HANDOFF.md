# 交接记事本 —— 给下一个接手的人/AI 看这一份就够了

> 这份文件的唯一目的：如果当前对话/会话突然中断，新开一个对话的 LLM
> （或人类）只要读这一个文件，就能知道"现在做到哪、下一步该干嘛"，
> 不需要翻聊天记录。**每完成一个阶段性成果就更新这里，不要拖延。**
> 详细的字节级格式定义不重复贴在这里，只给"结论 + 指向哪个文件/哪一节"。
>
> `docs/roadmap.md` 是项目最早期（拿到真实游戏文件之前）写的理论规划，
> **已经严重过时，不要看它，一切以本文件和 `docs/vertex_format.md`/
> `docs/animation_format.md` 为准**。项目级目标、建设顺序与验收门槛见
> `docs/PROJECT_TARGETS.md`。

最后更新：2026-09-14（归档文档更新 `2b55a0d` 已合入 GitHub 默认分支 `main`；后续工作以 `main` 为准）

## 项目一句话说明

给《Prototype》(2009) 游戏的 `.p3d` (Pure3D引擎) 资源文件做逆向解析，
最终目标是把角色模型（含骨骼动画、贴图）导出成 glTF，用于制作一个
VRChat 内的《Prototype》游戏考古展览/纪念馆（预览角色 + 如果可能预览地图）。

## 最终用户需求（务必记住，别偏离）

用户是想做 **VRChat 展览**，角色优先级（原话）：
Blackwatch黑色守望×2、海军陆战队×2、Hunter猎手、Alpha Hunter大猎手、
终极猎手、Elizabeth Greene(E妈)、Cross上校、**Alex Mercer(阿哥，当前模板角色)**、
各类强化感染僵尸。平民NPC可以不做。**若能顺带预览完整游戏地图会更好**。

策略：Alex 的静态流程（解析→绑定→贴图→glTF→预览）已作为模板跑通；现在先完成
一个可靠的动画垂直切片，再建立全角色/动画/音效的通用资产台账和解析基础。
设计可同步调研，但最终的批量模型导出与 VRChat 集成要等展览范围、性能预算和体验
设计基本收敛后再做。详见 `docs/PROJECT_TARGETS.md`；地图解析仍是一条独立、低优先级
的通用化路线。

## 当前状态：Alex Mercer 全身角色（T-pose，贴图+UV已修复正确）预览已成功，动画数据结构已完整逆向但尚未接入 glTF

### ✅ 已经做成、跑通、验证过的（不用再重做，直接复用）

1. **`export_alex_full.py`** —— Alex Mercer **全身**（身体+头+外套+右臂+
   左臂，共5个Skin，共享 `alex_reg_body_skeleton` 67根骨骼）一次性导出成
   合法 `.glb`。已验证：几何体正确、骨骼层级正确、贴图正确、UV正确。
   （`export_alex_body.py` 是它的前身，只导出身体3个Skin不含手臂，仍保留
   在仓库里当作更简单的参考实现，两者的贴图/UV修复都已同步）。
2. **`pack_preview_html.py`** —— 把任意 `.glb` 打包成一个自包含单文件
   HTML（three.js r128 全部内嵌，base64编码模型数据），可以在 workspace
   的沙盒 `allow-scripts` 无网络 iframe 预览器里直接跑起来看3D模型。
   带一个"📷 拍照"按钮（用 `canvas.toDataURL()` 截取真实渲染像素，
   `preserveDrawingBuffer:true`）和一个"UV调试面板"（旋转90°/左右镜像/
   上下镜像三个独立开关+实时状态文字），后续调试任何角色的UV朝向都可以
   直接用这个面板肉眼判断，不用再靠代码猜。
3. **`screenshot_preview.py` / `click_shot_and_grab.py`** —— 用
   `playwright` 起 headless Chromium 打开预览HTML，等加载完成后自动截图
   （前者直接截取页面，后者真的模拟点击"拍照"按钮再从弹出的`<img>`里取
   data URL）。**这是本轮引入的关键工具：不要只凭代码逻辑自信"应该是对
   的"，每次改完贴图/UV相关代码，都应该用这两个脚本实际截图看一眼再下
   结论**（血泪教训见下方"两个严重bug"）。
4. **贴图解码链路已完整验证**：DXT1/DXT3/DXT5解压用 `texture2ddecoder`，
   转码PNG用 `Pillow`，正确跳过 `Image_Data`(0x00019002) payload 开头
   132字节的DDS文件头（历史遗留bug，见下）。
5. **UV朝向已钉死结论**：U/V都不需要翻转，直接用游戏原始UV即可（历史
   遗留bug，见下）。**这个结论目前只在 Alex Mercer 一个角色的5个Skin上
   验证过**，换新角色时理论上应该一致，但强烈建议每个新角色第一次导出
   后都用UV调试面板肉眼复核，不要预设所有shader都一样。
6. **动画数据组织范式已完整逆向**（`docs/animation_format.md`）：
   `alex.p3d.rz` 文件本体内嵌了634个具名动画chunk（`alex_act_*`系列，
   如 `alex_act_death`/`alex_act_block`），**不需要额外找动画文件**。
   已破解 `Animation`(0x121000) 容器的完整层级（Header/Group_List/Group/
   Channel）+ Prototype 特有的**帧数据外部化压缩存储**方案（所有帧数据
   打包进一个 `0x02F00000` ZLIB blob，channel chunk 本身只留类型+
   count+offset 定位符）+ 3种channel值编码（int16×3压缩四元数/int8×3
   压缩四元数/int16×3位移向量）。用真实动画样本（`alex_act_block`，53个
   骨骼group、60个channel）做了逐字节零误差交叉验证。**这套理解目前
   完全停留在"读懂了、能手动解码"阶段，还没有写代码把某个具体动画的
   关键帧数据转换成 glTF 的 `animation` 轨道并让模型真的动起来**——
   这是下一步的第一优先级任务。

### 🐛 本轮修复的两个严重bug（教训写在这里，避免以后重犯同类错误）

**Bug 1：贴图花屏（彩色雪花噪点）**
- 根因：`Image_Data`(0x00019002) 的 payload **不是**纯裸DXT字节流，
  开头有132字节（4字节长度前缀+4字节`"DDS "`魔数+124字节标准
  `DDS_HEADER`结构），真正的DXT压缩数据从offset=132才开始。
- 之前的错误认知：文档曾经写"payload就是裸DXT字节流，无额外头部"，
  是只验证了payload_len的数量级近似（没跳字节头也大致对得上数量级）就
  下的过早结论，没有真正逐字节核对开头内容。
- 修复：导出脚本解码DXT前先校验 `payload[4:8]==b"DDS "`，再跳过前132
  字节取真正数据传给`texture2ddecoder`。已在 `docs/vertex_format.md`
  第8c.2节写了详细勘误。
- **教训：任何"数量级差不多吻合就想蒙混过关"的验证方式都不可靠，必须
  做零容差的精确字节数验证（比如本例：`174908-132=174776`，与理论
  mipmap链字节数完全相等，而不是"大概是那个量级"）。**

**Bug 2：UV朝向错误（脸部/五官贴图错位，像戴了歪的面具）**
- 根因：导出脚本里有一行 `uv[:, 1] = 1.0 - uv[:, 1]`，是凭
  "DirectX→OpenGL习惯"猜测加的V轴翻转，从未真正验证过，实际上完全
  不需要翻转。
- 定位方法：给预览页加了UV调试面板（旋转/左右镜像/上下镜像三个按钮+
  实时状态），用户现场调到"看着对"的组合是 `rot=180° flipU=是
  flipV=否`。用 headless Chromium 里真实的 three.js `texture.matrix`
  （不是手工心算）精确反推这组参数的等价采样公式，代入"导出代码已经
  翻转过一次v"的事实换算，得出"两次翻转刚好互相抵消"的结论，即正确
  做法是完全不翻转。
- 修复：删除 `export_alex_full.py`/`export_alex_body.py` 里那行错误
  代码。已重新导出+headless Chromium截图验证：五官、白衬衫领口清晰
  正常。
- **重要说明**：这个bug的定位过程中，AI助手一度在"UV到底对不对"这件
  事上出现了自我怀疑、想撤销刚确认的用户结论去重新验证——这是不必要
  的低效行为，浪费了用户的时间和耐心。**用户当场明确纠正："你没错是
  我错了"，最终结论就是上面这条修复，UV已经确认修好了，不要在新会话
  里再去怀疑/重新折腾这个结论，除非未来真的发现新角色跑出来UV又不对
  才需要重新排查。**
- **教训：任何凭"行业惯例/经验猜测"加的坐标系转换代码，必须真正拿实际
  渲染结果验证，不能想当然。给用户一个可交互调试工具让其现场判断，
  比反复纯代码走查/自我怀疑快得多、也准得多。定位到用户给出的明确结论
  后要果断采纳、验证、收尾，不要在拿到结论后又反复横跳。**

### 📁 当前 `alex_full.glb`（全身，贴图+UV均已修复）的生成命令

```bash
cd /home/user/prototype-p3d-toolkit/tools/p3d_export
pip install pygltflib numpy pillow texture2ddecoder   # 如果新会话还没装

# 1) 从游戏文件导出全身 .glb（body+arms+left_arm 三个部位组，共5个Skin）
python3 export_alex_full.py \
  --base-url "<用户当前隧道URL，见下方'环境现状'>" \
  --rcf-path "/mnt/hdd/新建文件夹/steamapps/common/Prototype/art.rcf" \
  --entry-name "\art\alex\alex.p3d.rz" \
  --out /home/user/prototype-p3d-toolkit/samples/exported/alex_full.glb

# 2) 打包成自包含 HTML 预览页（可选：--cam/--target 调初始相机位置）
python3 pack_preview_html.py \
  --glb /home/user/prototype-p3d-toolkit/samples/exported/alex_full.glb \
  --out /home/user/alex_full_preview.html \
  --title "Alex Mercer - 全身" \
  --subtitle "T-pose，贴图+UV已修复" \
  --cam "0,1.65,-0.7" --target "0,1.6,0"

# 3)（可选）用 headless Chromium 自动截图验证，不用等 present_file
pip install playwright && python3 -m playwright install --with-deps chromium  # 如果新会话还没装
python3 click_shot_and_grab.py --html /home/user/alex_full_preview.html --out /tmp/shot.png
# 然后用 read_file 工具查看 /tmp/shot.png，确认渲染正常再呈现给用户
```

`.glb`/预览HTML 因内嵌游戏原始贴图数据（版权内容），**按 NOTICE.md 规定
不提交进git**（已在 `.gitignore` 排除 `samples/exported/`、`*.glb`、
`*.gltf`），只提交生成它们的Python脚本源码，每次新会话现场跑一遍即可
（几十秒内完成，不是重活）。

## 下一步要做的事（按用户明确给过的优先顺序）

### 1.（当前最优先）把动画接入 glTF，让模型真的能动起来

`docs/animation_format.md` 已经把"怎么读懂一个动画"的范式讲清楚了，
接下来要做的是**工程落地**：

1. 挑一个简单动画先试（建议 `alex_act_block` 或类似短动画，已经在
   `docs/animation_format.md` 里当作分析样本，数据最熟）。
2. 写解码代码：定位该动画的 `Animation`(0x121000) chunk → 找到
   `0x02F00000` ZLIB blob 解压 → 遍历 `Animation_Group_List` 下每个
   `Animation_Group`（每组对应一根骨骼，`name`字段就是骨骼名）→ 每组
   下的channel节点（`0x00121112`/`0x00121114`=压缩四元数两种精度，
   `0x00121119`=位移向量）→ 用 `0x00121120` 给出的 `(count, offset)`
   去blob里精确定位 frames(uint16数组)+values(见下表)。
3. **关键坑：`0x00121119`(TRAN位移通道) 的具体缩放系数目前还没有标定**
   （`docs/animation_format.md` 第6节明确写了这是"尚待验证"项）——原始
   int16值范围观察到能到±26000量级，不清楚除以32767还是其他系数才能
   还原成与骨骼局部矩阵同单位的真实位移。这是接入动画前必须先解决的
   唯一遗留空白，建议用 `Character_Root`（根骨骼，肯定有TRAN数据）的
   已知bind pose位置做锚点反推缩放系数。
4. 把解码出的每帧骨骼局部变换（旋转四元数+位移，若无TRAN数据的骨骼
   位移用bind pose不变）转换成 glTF 的 `animation.channels` +
   `animation.samplers`（每个骨骼节点一个translation轨道+一个rotation
   轨道，用`0x121xxx`的`frames[]`换算时间戳：`time = frame / frame_rate`，
   `frame_rate`就是 `Animation` chunk头部的`FrameRate`字段）。
5. 验证方法：同样用 `pack_preview_html.py` + headless Chromium，但需要
   给预览页加一个"播放动画"的按钮（three.js `AnimationMixer`），肉眼
   确认模型真的按预期动作播放，不是骨骼乱飞或者穿模。

### 2. 全身角色/动画流程都跑顺后，横向扩展到其他角色

用户列出的优先级：Blackwatch黑色守望×2、海军陆战队×2、Hunter猎手、
Alpha Hunter大猎手、终极猎手、Elizabeth Greene(E妈)、Cross上校、各类
强化感染僵尸。**这些角色具体在哪个 `.p3d.rz` 文件里，目前完全没有查过**
——下一步需要先枚举用户游戏目录（`/mnt/hdd/新建文件夹/steamapps/common/
Prototype/`）下 `art.rcf` 里的全部条目名，找出这些角色对应的文件名
（大概率是类似 `blackwatch.p3d.rz`/`hunter.p3d.rz`/`greene.p3d.rz`这类
命名，需要实际枚举确认，不要猜）。定位到文件后，理论上可以直接复用
`export_alex_full.py` 的整套代码模式（部位组枚举→骨架共享判断→贴图
解码→UV原样使用），只需要改角色名/部位组名参数，但**要留意**：
- 不同角色的骨架关节数/命名可能不同，不能假设都是67关节。
- 不同角色的顶点格式可能有 `memory_imaged=0`（老式List chunk，见
  `vertex_format.md`第8d节）这种变体分支，需要留意兼容。
- UV朝向按上面的结论"原样使用不翻转"，但每个新角色应该用调试面板肉眼
  复核一次。

### 3. 之后：多角色"道具栏"选择/排版预览页面

用户要求"能一次性列出优先级角色，点哪个看哪个"的选择界面，等单角色+
动画流程对多个角色都跑通后再做，不要提前做，因为界面设计依赖于"每个
角色最终产出什么样的glb"这个还没确定的细节。

### 4. 更远期，优先级低，不用主动查

- 材质/贴图关联里几个"用途未知但不阻塞主线"的小尾巴：
  `Skeleton_Partition`(0x00023002)、`0x00010021`(Vertex_Compression_Hint)、
  `0x00122000`(Sort_Order)、`0x00010017`(Render_Status)——都已经在
  `docs/vertex_format.md`第10节记录过，不影响渲染，除非以后发现真的
  需要，不用主动去查。
- 地图/Room级chunk解析（用户要求"能预览完整地图更好"），目前完全没有
  实际做过，只在早期 `docs/pure3d-format.md`/`docs/2009-3dm-tool-findings.md`
  里有一些概念级线索（`tlDAPortalChunk`等类名），是独立于角色导出的
  另一条线，建议等角色流程完全稳定后再启动，不要现在分心。

## 格式逆向状态总览（几何/骨骼/材质/动画 四条数据链，全部打通）

全部记录在 `docs/vertex_format.md`（几何/骨骼/材质，第1-10节）和
`docs/animation_format.md`（动画）。这两份文档是格式定义的唯一权威
来源，本文件只做导航，细节请直接翻它们，不要凭记忆/印象转述。

关键结论速查表：

| 数据链 | 状态 | 权威文档位置 |
|---|---|---|
| 顶点/索引/PrimitiveGroup | ✅ 完整解码+落地验证 | vertex_format.md 第1-6节 |
| 包围盒/球 | ✅ 完整解码 | vertex_format.md 第7节 |
| Skin绑定关系 | ✅ 完整解码+三样本验证 | vertex_format.md 第8节 |
| Composite_Drawable_2部位组 | ✅ 完整解码 | vertex_format.md 第8b节 |
| 材质/贴图关联+DDS解码 | ✅ 完整解码+落地验证（含bug修复记录） | vertex_format.md 第8c节 |
| UV朝向约定 | ✅ 已钉死结论（不翻转） | vertex_format.md 第3b节的"最终结论"框 |
| memory_imaged=0老式顶点格式 | ✅ 完整解码 | vertex_format.md 第8d节 |
| 骨架层级Skeleton_2 | ✅ 完整解码+落地验证 | vertex_format.md 第9节 |
| 三份骨架(body/arms/left_arm)等价性 | ✅ 已验证可共享 | 见下方"关键数据点" |
| 动画数据组织范式 | ✅ 完整逆向，未接入glTF | animation_format.md 全文 |
| 动画TRAN位移缩放系数 | ❌ 未标定 | animation_format.md 第6节"尚待验证" |

## 关键数据点（避免重新查一遍）

- **alex.p3d.rz 里的9个 Composite_Drawable_2 部位组**：
  `alex_reg_body`(身体+头+外套,3 Skin)、`alex_reg_arms`(右臂,1 Skin)、
  `alex_reg_left_arm`(左臂,1 Skin)——这3个是角色部位；其余6个
  (`groundspike_*`系列)是道具，非角色部位。
- **三份骨架（body/arms/left_arm）逐关节比对结果**：67关节/13分区/4肢体，
  关节名和父子关系完全一致，仅 `Spine_1`/`Spine_2`/`Eye_R`/`Eye_L` 4个
  关节矩阵有≤0.13的微小浮点误差，**可视为同一骨架的等价副本**，全身
  导出统一用 `alex_reg_body_skeleton` 作为共享骨架（已在
  `export_alex_full.py`里做了运行时断言校验这个假设，换角色时如果
  断言失败会报错提醒，不会静默出错）。
- **动画数量**：`alex.p3d.rz` 内嵌 **634个** `Animation`(0x121000) chunk，
  命名规则 `alex_act_<动作名>[_变体][_camN]`，`type`字段为 `PTRN`(骨骼
  动画)或`CAM `(摄像机动画，未深入分析，理论上应该复用同一套
  Group/Channel机制)。

## 环境/基础设施现状（怎么连上用户的真实游戏文件）

- 用户本地运行 `run_viewer.sh` 起了一个本地 HTTP API 服务器（代码在
  `viewer/server.py`），通过 Cloudflare Quick Tunnel 暴露一个临时公网 URL。
  **这个 URL 每次用户重开隧道都会变**，当前记录的
  `https://journalists-fit-snap-buttons.trycloudflare.com` **可能已经
  失效**——如果新会话开始时这个 URL 连不通，需要请用户重新运行
  `run_viewer.sh --tunnel` 并把新 URL 发过来。
- 该 API 有 `POST /api/self_update` 端点，能让用户那台机器上跑着的
  server 自己从 **GitHub 远程默认分支** fast-forward 到最新 commit 并原端口重启。
  **不要把分支名写死为 `master`**：GitHub 当前只保留默认分支 `main`。新版本
  `viewer/server.py` 会用 `git ls-remote --symref origin HEAD` 自行解析默认分支。
  如果远程 server 仍是旧版（旧版硬编码拉取已删除的 `origin/master`），这一次
  需要用户在本机手动执行 `git fetch origin && git switch main && git pull --ff-only
  origin main` 后重启 `run_viewer.sh --tunnel`；之后才可以继续安全使用 self-update。
  每次新会话开始时，先 `GET /api/health` 检查 commit，再按需调用该端点。
- 关键 API：`GET /api/rcf_entry?path=<rcf路径>&name=<归档内路径>&type_filter=
  0x前缀十六进制&offset=&limit=&payload_preview=`——从 `.rcf` 归档里按名字
  取出条目（自动解压`.rz`），解析成chunk树JSON返回，`payload_preview`最大
  支持 8MB（一次性拿完整顶点/贴图缓冲用这个）。**重要**：`P3DSource`类
  （`export_alex_full.py`里定义）的 docstring 里写明了一个容易踩的坑：
  **永远不要传 `max_depth` 参数**，因为它是作用于"整个文件"从头开始的
  绝对深度上限，不同取值会导致 `global_index` 编号在不同调用之间不一致，
  跨调用混用会指向错误的chunk。要限定某个chunk的子树范围，用返回结果
  自带的 `depth` 字段自己裁剪（见 `P3DSource.get_subtree()`的实现）。
- 用户游戏路径：`/mnt/hdd/新建文件夹/steamapps/common/Prototype`，
  `art.rcf` 里 `\art\alex\alex.p3d.rz` 是 Alex Mercer 模型（本项目当前
  所有已验证数据均来自这一个文件，包括动画数据也在这个文件里，不需要
  额外找）。**其他角色的文件名尚未查过，见上方"下一步"第2条**。

## 本地开发环境 / Git 相关（有个坑，务必看完）

- assistant 自己有一份独立 git clone 在 `/home/user/prototype-p3d-toolkit`；
  **GitHub 当前唯一远程分支/默认分支是 `main`**，不要再向已删除的 `master` push。
- 远程仓库：`git@github.com:204343414/prototype-Game-p3d.git`（SSH方式）。
- **⚠️ 关键坑：`.git/config` 是workspace快照排除的敏感凭据路径之一
  （连同 `.git/credentials`/`.git-credentials`/`.netrc`），不会跨对话
  持久化！这意味着每次开新会话，`git remote` 配置很可能已经丢失
  （`git push`/`git fetch` 会报 "origin does not appear to be a git
  repository"）。新会话的固定检查顺序：
  1. `git remote -v` 看是否为空；为空则运行
     `git remote add origin git@github.com:204343414/prototype-Game-p3d.git`
  2. 对项目 SSH 私钥执行 `chmod 600 <私钥路径>`；需要指定身份时使用
     `GIT_SSH_COMMAND='ssh -i <私钥路径> -o IdentitiesOnly=yes'`。
  3. `git fetch origin && git switch main && git pull --ff-only origin main`
  4. 最后用 `git push origin main`。不要假设旧的 SSH config、私钥文件名或
     本地追踪分支在 workspace 快照之后仍存在。
- **每次做出格式发现，先写进 `docs/vertex_format.md`/`docs/animation_format.md`
  对应章节，再commit+push，再回来更新这份 `HANDOFF.md`**——顺序不要反，
  两份格式文档才是权威来源，这份文件只是"导航牌"。

## 一些容易踩坑的经验教训汇总

- P3D 字符串是 `1字节长度 + UTF8内容 + 补\0到长度` 格式，长度字节记录的
  是**补齐后**的长度，不是原始字符数——每次解析都要用hex实测校验。
- 第三方参考库（`references/gibbed-prototype` 为 Prototype 专用 C# 库，
  `references/netp3dlib` 为通用 P3D 标准 C# 库）的字段定义**曾经出现过
  不完整/过时/甚至这次发现"格式范式完全不同"的情况**（Prototype的
  Animation数据把帧数据外部化压缩存进独立blob，netp3dlib假设是内联
  存储）——**任何用参考库反推出的结论，最终都必须用真实游戏数据逐字节
  交叉验证，不能直接信代码**，但本轮验证过的部分（Skin/CompositeDrawable2/
  NewShader/TextureChunk/TextureDDS/压缩四元数编码算法等）已经100%精确
  验证通过，可以放心直接用。
- **任何"数量级差不多吻合就想蒙混过关"的验证方式都不可靠**，必须做零
  容差的精确字节数验证（贴图bug的教训）。
- **任何凭"行业惯例/经验猜测"加的坐标系转换代码，必须真正拿实际渲染
  结果验证**，不能想当然（UV翻转bug的教训）。给用户一个可交互调试工具
  让其现场判断，比反复纯代码走查快得多、准得多。
- **拿到用户给出的明确调试结论后要果断采纳并收尾，不要在验证过程中
  反复自我怀疑、来回折腾**——用户的耐心是有限资源，与其自己纠结"这个
  结论到底对不对"，不如直接用代码/工具去验证一次拿到确凿证据，然后
  往前走。
- 每次改完贴图/UV/几何相关的导出代码，**必须用 `screenshot_preview.py`
  或 `click_shot_and_grab.py` 实际截图看一眼**，不要只凭代码逻辑推理
  "应该是对的"就呈现给用户——这是本轮最大的效率教训，多次因为跳过这一
  步而来回拉锯。
