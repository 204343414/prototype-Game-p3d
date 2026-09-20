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

最后核对：2026-09-17。当前状态：**已回滚至稳定基线 Commit `540e3ca`**。

## 2026-09-17 最新进度：Pure3D 实体陈列架、骨骼蒙皮与动作播放排查交接

### 1. 当前系统基线（已稳定回滚至 Commit `540e3ca`）
- **查看器前端架构（端口 8421）**：
  - 已彻底移除所有外部 CDN 依赖（`unpkg.com`），Three.js 0.160.0 ES 模块（`OrbitControls`, `GLTFLoader`, `BufferGeometryUtils`）已完整本地化打包到 `viewer/static/vendor/`。
  - 前端支持五大分类（主角形态 `powers`、载具 `vehicles`、剧情角色 `characters`、路人 NPC `pedestrians`、可破坏道具 `props`），支持网格卡片陈列与 3D 检视抽屉。
- **运行环境与服务**：
  - 远程主机：`liang@8700k.top`（通过 Cloudflare SSH 连接）。
  - 隔离测试预览目录：`/tmp/prototype-map-preview.LRlPDWCg`（端口 8421，严禁触碰 8420）。

### 2. 核心已知 Bug 与排查证据（给下一个 Agent 的关键突破点）

#### 🔴 Bug A: 骨骼蒙皮顶点拉丝 / 局部权重错乱（详见用户最新截图）
- **现象**：
  在利爪形态（`alex_claws.p3d.rz`）或部分肢体上，右爪正常跟随骨骼摆动，但左爪一部分手指/刀刃顶点出现向远处拉丝缩放（Stretching）。
- **底层证据与排查方向**：
  1. **骨骼调色板 `Matrix Palette (0x1000D)`**：
     - 在 Pure3D 结构中，`0x1000D` 的前 4 字节是 `uint32 count` 还是纯 `uint32[]` 数组？在 `alex_blades` / `alex_claws` / `alex_reg_body` 中需要实测每个 Skin 的局部调色板索引与顶点流中的 `blendIndices` 匹配关系。
  2. **衍生辅助骨骼（Helper Bones / Shoulder Connectors）**：
     - 用户指出：肩膀部分有衍生骨骼（如 `Shoulder_Con_L / Shoulder_Con_R`），移动它时不应该带动整条手臂位移，而是仅影响局部肌肉变形。需要检查骨骼父子级关系与世界变换矩阵计算。
  3. **SkinnedMesh 绑定矩阵**：
     - Three.js 中 `activeSkeleton = new THREE.Skeleton(bones, boneInverses)`，`boneInverses` 必须在 `skeletonGroup.updateMatrixWorld(true)` 之后由世界逆矩阵生成，避免多次调用 `.add()` 导致骨骼根节点被各子 Mesh 抢占移出。

#### 🔴 Bug B: 贴图 UV 镜像 / 倒置问题
- **现象**：
  部分模型贴图出现局部迷彩或图案左右/上下翻转。
- **底层证据**：
  - 顶点数据流：双流网格（Dual Stream）中，Stream 0 为 56 字节（位置/法线/切线/骨骼权重），Stream 1 为 12 字节（前 4 字节为顶点颜色，后 8 字节为 Float32 U/V）。
  - 需要在 Direct3D 9（DirectX UV 空间）与 WebGL（Three.js UV 空间）之间确立精准的零误差 UV 映射公式。

#### 🔴 Bug C: 动作播放与根运动（Root Motion）真实还原
- **用户核心诉求**：
  - **不要人为锁定或伪造动作**：严格忠实还原游戏原始动画文件（`0x121000`）中的所有通道。
  - **动画通道类型**：
    - `0x121102` / `0x121119`：`TRAN`（Float32 3D 位移向量，包含 `Motion_Root` 与 `Character_Root` 物理位移）。
    - `0x121112` / `0x121114` / `0x121118`：`ROT`（旋转四元数）。
  - 脚掌贴地：确保根骨骼和脚踝骨骼的高度、旋转精确解耦，不做动作时脚掌自然着地，做战斗/跳跃动作时物理曲线完全吻合。

#### 🔴 Bug D: 独立形态与通用动作库分离
- **设计原则**：
  - 保持各形态包（利刃、利爪、重锤、鞭拳）自身专属动作的独立性，先不与 Alex 基础库强行混淆。
  - 后续可根据游戏状态机（State Tree）设计可插拔的形态武器挂载方案。

## 最新重大突破：广告牌/时代广场巨幅海报与全城材质代码推导全面还原（2026-09-16）

根据用户"不要凭直觉，从代码引用与顶点声明反推"的最高原则，本轮完成了对全城所有 29 种底层二进制顶点声明（Chunk `0x10014`）、`NewShader` 参数引用以及 `art.rcf` 共享图集资源的全面解析与真实验证：

1. **查明广告牌/海报与时代广场专有图集来源与引用链**：
   - 时代广场与全城巨幅广告牌主图集：`art.rcf` 内的 `\art\billboards\billboards.p3d.rz`（包含 4 套 1024x1024 级主广告图集 `billboards_1024x1024_01..04_diffuse.dds`）。
   - 百老汇/时代广场特有广告与霓虹灯标牌：`\art\locations\manhattan_mini\textures.p3d.rz` 与各 Cell 包（恢复了松下 `panasonic_diffuse.dds`、DC漫画 `dc_comics_diffuse.dds`、1500百老汇股票滚屏 `1500broadway_tickertape_diffuse.dds`、好莱坞影像 `hollywoodvideosign_2_diffuse.dds` 等 534 张独立去重共享纹理）。
   - 对应 Shader 模板覆盖：`zCBV2_ao_billboard` (52B @ UV24), `env_videoscreen_blend` (32B @ UV24), `env_videoscreen1` (44B @ UV24), `env_videoticker` (44B @ UV24), `env_lit_sign` (44B/52B), `env_litMarquee` (68B @ UV24) 等。

2. **14 套严格证据链规则接入** (`tools/world/cell_materials.py`)：
   - **68B 标准环境** (`4f18391a`)：`color` @ UV24 (TEXCOORD0)
   - **68B 巢穴轻量室内** (`e6c6bff8`)：`color` @ UV24 (TEXCOORD0)，解决 Cell 239/249
   - **64B 室内建筑** (`3c20bc99`)：`color` @ UV20 (TEXCOORD0)
   - **60B 地形/草地/石头/杂物** (`ec4f5425`)：`bottom`/`color` @ UV24 (单 UV)
   - **56B 反射立面/碎屑** (`fb2000cf`)：`color` @ UV20 (单 UV)
   - **52B 沥青路面/草地/广告牌** (`2bdeba9c`)：道路 `bottom` @ UV32；广告牌/草地 `color`/`bottom` @ UV24
   - **52B 栅栏/铁网/NIS人物** (`930b61a4`)：`color` @ UV16 (单 UV)
   - **48B 巢穴深度感染区** (`dfe0a2c2`)：`color` @ UV20 (TEXCOORD0)
   - **44B 发光招牌** (`b185657b`)：`color` @ UV16 (TEXCOORD0)
   - **44B 字体标牌/铁丝网** (`e9cdf8f3`)：`ImageMap`/`color` @ UV24 (单 UV)，`source_alpha`
   - **36B 室内玻璃/实验器具** (`684f7ea2`)：`color` @ UV16 (单 UV)
   - **32B 地面 Decal / 斑马线** (`8c718e7c`)：`color` @ UV24，DXT3 支持，`source_alpha`
   - **28B 小型贴花/光效** (`31f14052`)：`color` @ UV20，`source_alpha`
   - **76B 人行道/马路牙子** (`214be15f`)：`color` @ UV24 (`street_sidewalk01_diffuse.dds` 等)

3. **全城 260 API 完整核验证明**：
   - **总绑定数由 9,391 飙升至 15,877 / 16,898 组（全城覆盖率达 93.96%）**。
   - **未解析纹理大幅降低至 70 组**（原 8,404 组）。
   - **几何零回退**：顶点 7,784,242 / 三角形 4,675,907，全城 260 个 Cell API 零报错。
   - 时代广场核心商业区恢复情况：
     - **Cell 56**：169 / 176 (96.0%)
     - **Cell 61**：218 / 229 (95.2%)
     - **Cell 66**：103 / 114 (90.4%)
     - **Cell 71**：110 / 122 (90.2%)
     - **Cell 76**：212 / 217 (97.7%)
     - **Cell 102**：181 / 187 (96.8%)

4. **查看器交互升级**：
   - 引入 WASD 漫游模式与滚轮调速。
   - 引入 Shift/Ctrl + 点击标记未贴图模型并导出 JSON 报告工具。
   - 已部署至用户主机隔离端口 8421：`http://127.0.0.1:8421/#map`。


## 地图缩放/拖拽修复（2026-09-15）

用户反馈滚轮放大后视角异常且无法正常拖动。对实际 Three.js r160 OrbitControls 的
浏览器测试复现：旧默认 `minDistance=0` 下，240 次滚轮将相机与 target 距离压到
0.0156 个世界单位；平移步长随该距离缩小。当前新增 `map-camera.js`：

- 最小距离按场景尺度设定（至少 2），限制最大距离及极角，启用鼠标指向缩放；
- 双击实际表面重新聚焦，重置视角回到当前地图范围；重置前清理残余阻尼；
- 离开地图标签恢复原相机/控制参数，避免影响人物预览；
- 静态地图按需重绘，用户开始操作后整城队列结束不强行覆盖其视角。

真实浏览器回归使用 city-scale 合成几何、实际 OrbitControls/CDP 鼠标事件（非 mock），
240 次滚轮后距离维持 2.8289，左键旋转、右键平移、总览复位和双击聚焦均通过。
新前端也已在用户真实 Cell 2 上加载并截图确认；本轮未重新跑完整城市性能基准。
这不是碰撞检测或第一人称漫游，不能承诺相机永不穿建筑。

已更新用户 8421 的前端文件，无需重启服务器；打开
`http://127.0.0.1:8421/?v=camera-navigation#map` 获取新版本。原 8420 和游戏文件保持不动。

## 最新增量：整城核对与共享材质（2026-09-15）

用户要求先完整加载地图，再设计长期缓存；本轮没有生成持久几何/贴图缓存。

- 用户机器上实际完整 API 核对 **260/260** 基础 Cell：**143 ready、117 empty、0 error**。
  empty 包含既有 111 placeholder 与 6 个无 merged world root 的特殊 Cell；不等于缺失。
- 城市主体合计 **7,784,242 顶点 / 4,675,907 三角形 / 16,898 PrimitiveGroup**。
- 在 `art.rcf` 的 `\art\locations\manhattan\textures.p3d.rz` 找到 192 个共享 Texture 名称。
  只在本 Cell 没有同名 Texture 时按 **NewShader color 精确名称**查共享资源；本地优先、
  歧义/坏 DDS 不猜。接口新增 `shared_path=<art.rcf>`，受 root 和 realpath 约束。
- 共享材质接入后：**9,391/16,898 组**有可绑定贴图（原为 1,056）；其中 8,335 组来自共享源。
  559 张按内容去重的压缩纹理 mip，总计 26,298,624 bytes。按组数约 55.6%，不是画面面积覆盖率。
- 剩余灰色：7,125 组 UV layout 未验证，381 组纹理缺失/不支持，1 组 color binding 不明确。
  不宣称全部材质、全部 shader、_ft/局部实例、无接缝或 VRChat 可直接使用。
- 私有统计报告：用户机器 `/tmp/prototype-map-preview.LRlPDWCg/whole-city-audit.json`。
  仅统计/布局/缺失原因，不含顶点、索引或纹理 payload。整轮本机 API 审核约 55 秒，非浏览器加载耗时。
- `map-request.mjs` 对网络断线/超时、429、5xx 有限重试；422 等格式错误不反复请求。
  整城继续按钮重试失败项。压缩响应降低隧道传输量，不能把网络等待解释成路径编码错误。
- 共享纹理只有**进程内、单 archive 索引**，按路径+mtime_ns+size 失效；服务器重启消失，
  与用户要求推迟的长期 Cell 缓存不是一回事。前端全城驻留预算仍为 512 MiB / 1000 万三角形。
- 当前入口仍为用户本机 `http://127.0.0.1:8421/?v=shared-recovery#map`，原项目/8420 未修改。
  如有旧页需刷新加载新版代码；新版本地图 API 与前端必须配套，不能只复制 index.html。
- 浏览器整城队列已结束：143 个区块、4,675,907 三角形、559 张贴图、9,391/16,898 组材质，
  驻留估算 316.1 MiB，与本机 API 核对一致。已在中途截图看到共享贴图补齐后的楼体和屋顶；
  最终整城截图工具未返回图像，不能据队列成功声称逐块视觉/接缝验收完成。
- 测试：viewer smoke、7 项真实合成材质单测、4 项 Node 原生 HTTP 重试测试（无 fetch mock）通过。

## 历史里程碑：单 Cell 灰模接入（后续已扩展整城）

本轮撤掉了错误的 `/api/heightmap_preview`（任意 P3D 字节转灰度图不是地图），
改为 `/api/rcf_cell_preview?path=<cells.rcf>&cell=N`，复用严格 core 三角解析器。
`viewer/static/map-view.js` 从真实 manifest 列出基础 Cell，按需加载、保留源坐标，
提供编号筛选、线框、重置视角和卸载；最多驻留 4 Cell / 50 万三角形。
未知布局/坏索引拒绝整个预览，不静默省略为“完整场景”；接口限制单 Cell 64 MiB。

- SSH 在用户机器真实验证：Cell 2 = 58 groups / 47,510 顶点 / 28,566 triangles；
  Cell 3 = 34 / 67,878 / 33,522；各约 0.31 秒本机请求时间（不是公网加载耗时）。
- 浏览器经私有 SSH 转发，实际看到 Cell 2 灰模和 Cell 2+3 合并视图，共 62,088 triangles。
  码头、建筑、吊机可见；源坐标保留，两个区块未强行拼合。Cell 0 API 返回 empty。
- 这解锁的是**无贴图灰模显示**，不是旧材质诊断已修好、接缝通过或全城完成。
  当前测试浏览器中文字体缺失，截图有方框；不作为模型问题继续反复调参。
- 用户本机预览入口：`http://127.0.0.1:8421/#map`。临时源码目录
  `/tmp/prototype-map-preview.LRlPDWCg`，日志 `server.log`，PID 文件 `server.pid`。
  仅绑定回环，用户原项目及 8420 未修改；临时环境重启后可能消失，尚未正式部署。
- 只传输了明确列出的源码文件到用户主机；没有将游戏资源提交 Git 或放到公开目录。
  此前容器上 `0.0.0.0:12000 --root /` 的宽权限测试服务已停止。
- 测试：`python3 viewer/test_server.py`（真实合成 RCF→RZ→几何 HTTP 链路，含
  越界、symlink、坏索引、空 Cell、超预算/坏压缩输入）及 core decoder 3 项测试通过。
  本轮未验证人物样本/骨骼新实现，不沿用此前“所有功能完美”的结论。

## 用户补充：样本页不等于统一入口

2026-09-15 用户明确：所示全城截图来自 B 站他人的收费预览器，仅作能力参照，
不是本项目的运行结果。用户希望独立开发并在 GitHub 公开工具项目；这不等于授权
本轮推送、改变仓库可见性或公开游戏资产，也不依赖获取收费工具源码。

此前 agent 已做出人物与动画样本，但以独立 Web 子页面提供，**尚无串联这些成果的
统一产品入口**。`viewer/static/index.html` 是已有的基础文件查看页，
`build_progress_dashboard.py` 是报告生成器，二者都不能据名称推断为已整合的首页。
样本页存在由用户确认；人物/动画产物位置与可重复生成入口仍待定位；已找回的地图子页见下节。

后续建议先补轻量导航与样本登记，复用已有页面，不另做一套模型渲染器；地图与音效
入口如实显示当前状态，不用占位图伪装成可用预览。该入口工作尚未实现。

### 已找回的私有样本：Cell 3 group 18 UV 对照页

用户上传 `/workspace/manhattan_cell_3_group18_uv_candidate_diagnostic.html`，
1,251,552 bytes，SHA-256 `a64c583b18f94442a978971e95a85a2fddf74222631e9fe6bdbbdff6375bbc19`。
这是本项目独立的 UV 诊断子页，不是第三方收费预览器、整图、完整 Cell 或动画样本。

本次直接读取嵌入数据并验证：25,509 顶点、32,112 个 uint16 索引 / 10,704 三角形，
索引最大 25,508；POSITION 和两组 float2 UV 长度及有限值检查通过；内嵌 PNG header
为 256×1024。元数据将源纹理记为 DXT5；本次未重新解码源 DDS，也未做 WebGL 目检。
左右分别比较 68-byte layout 的 offset 24 / 32，不能据结构检查重新裁定 V 轴规则。

主体使用自包含原生 WebGL，无 CDN 库依赖；尾部另有 Cloudflare 注入的 challenge
脚本，会动态请求 `/cdn-cgi/challenge-platform/scripts/jsd/main.js`，不是渲染依赖。
原件保持不变，尚未执行脚本或发布页面。该文件含游戏派生几何/纹理，留在仓库外；
后续清理应通过原创生成器或经确认编辑，不把整个产物纳入公开 Git。

## 本次核对的证据与部署边界

| 来源 | 核对结果 | 验证边界 |
|---|---|---|
| 本轮工作区 | `d35bf87`（Archive homepage 链接修复），浅克隆 | 不代表用户机器已部署，也非实时查询远端最新分支 |
| 用户 checkout / `GET /api/health` | 均为 `8c3bacc`；本机 8420 返回 200；`run_viewer.sh` 有未提交修改 | 未覆盖、pull 或重启 |
| 两端 SHA-256 | `viewer/server.py`、`viewer/static/index.html`、ROT-only exporter 内容一致 | 后续地图诊断和 report 工具不能视为已部署 |
| 用户反馈 + 动画格式文档 §8 | 用户确认骨骼/动画已解决；已有 Alex ROT-only 播放和蒙皮修复记录 | 不等于全部 TRAN、全部角色/动作已支持 |
| `tools/report/build_progress_dashboard.py` | 记录 11 个真实外部 PTRN 动作可预览；Cell 2 合并场景未稳定可见、已归档 | 本轮未重新播放历史画面；生成器不等于已部署页面 |
| 本轮只读归档 API | 复核 `art.rcf` 2,601 条、`cells.rcf` 520 条及两个 audio archive 的条目统计 | 未重跑全量几何普查、导出、渲染或音频解码 |

用户项目 `samples/` 只有 `.gitkeep`；游戏根目录与下一层未发现 HTML/JSON 预览产物。
未搜索整个 home 或其他 agent 私有空间，除用户随后上传的 Cell 3 UV 子页外，其他旧页面/缓存路径仍待定位。
不要把“本轮没找到产物”误写成“以前没有做成”。本轮未安装远端依赖、修改远端文件或上传游戏资源。

## 项目与当前优先级

研究《Prototype》(2009) 的 RCF/Pure3D 资源，建立本地考古预览器，最终用于私人的
VRChat 展览。角色、地图、音效各自维护来源、验证状态和缺口，不提交原始/派生游戏资产。

- **已建立的角色基线**：Alex 全身静态、骨架、贴图、播放时 weight 对齐和 ROT-only 动画；保持回归，不重复猜 UV/蒙皮规则。
- **角色候选库**：2,219 个 P3D 包结构普查记录了 236 包 / 491 个 rigged CompositeDrawable 和稳定 review ID；是元数据货架，不是 491 个已验证人物模型。见 [角色地基](docs/character-foundation.md)。
- **当前地图主线**：260 基础 Cell 普查、Cell 2/3 core 三角与部分 68-byte layout 证据已建立；合并材质场景的稳定显示仍是阶段门。见 [地图地基](docs/world-map-foundation.md)。
- **当前音效主线**：两个 audio archive 清单已复核，实际容器/编码/试听链尚未实现，见下方队列。
- **保留队列**：完整 TRAN 标定、内联动画解码、全角色覆盖、最终 VRChat 批量导出，不抢占当前地图/音效主线。

排期见 [项目目标](docs/PROJECT_TARGETS.md)；旧 `docs/roadmap.md` 仅作历史参考。
以下保留已验证技术细节，范围以实际样本为准。

## 已建立的 Alex 技术基线

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
   data URL）。**这是既有的关键验证工具：不要只凭代码逻辑自信"应该是对
   的"，每次改完贴图/UV相关代码，都应该用这两个脚本实际截图看一眼再下
   结论**（血泪教训见下方"两个严重bug"）。
4. **贴图解码链路已完整验证**：DXT1/DXT3/DXT5解压用 `texture2ddecoder`，
   转码PNG用 `Pillow`，正确跳过 `Image_Data`(0x00019002) payload 开头
   132字节的DDS文件头（历史遗留bug，见下）。
5. **UV朝向已钉死结论**：U/V都不需要翻转，直接用游戏原始UV即可（历史
   遗留bug，见下）。**这个结论目前只在 Alex Mercer 一个角色的5个Skin上
   验证过**，换新角色时理论上应该一致，但强烈建议每个新角色第一次导出
   后都用UV调试面板肉眼复核，不要预设所有shader都一样。
6. **动画：外部 ZLIB decoder 与 ROT-only glTF 导出已落地；全格式尚未完成**
   （权威说明见`docs/animation_format.md`）。`alex.p3d.rz` 文件本体内嵌634个
   具名 Animation chunk，分为533个`PTRN`骨骼动画、67个`CAM`、34个`EXP`，不需
   额外寻找动画文件。`tools/p3d_animation/decode_animation.py` 已对自定义外部
   ZLIB family（`0x00121112` int16×3 ROT、`0x00121114` int8×3 ROT、
   `0x00121119` int16×3 vector；子 locator 为`0x00121120`）实现严格解析；
   `alex_act_block` 的53个 group/60个 channel 已成功解出，且验证最后一个
   values block 可省略对齐尾字节。**重要勘误**：游戏同一动画体系还混用标准
   内联 channel `0x00121101`–`0x00121104`，以及未定编码的内联`0x00121118`；
   current decoder 对它们会明确报 unsupported，不能再称“所有 keyframes 都在
   外部 ZLIB blob”。真实 `alex_act_block` 已通过 ROT-only 导出/播放验证（48 条骨骼 ROT track）；
   全面“所有角色动画”前仍须补齐内联分支。`0x00121119` TRAN 的最终
   scale/reference-frame 仍未标定，故还不能生成可信 glTF translation。ROT-only
   exporter 显式跳过 TRAN 与没有对应 node 的 limb ROT，见动画文档 §8。
7. **播放时蒙皮槽位已修复**：packed 56-byte 顶点用
   `[stored0, stored1, stored2, 1-sum]`；legacy `Weight_List` 保持 Matrix_List
   顺序时用 `[1-sum, stored2, stored0, stored1]`。静态 bind pose 不能验证 weight
   槽位；已有真实播放与合成回归证据，见 `skinning.py` 和动画文档 §8。

### 🆕 2026-09-14：旧教程 P3DAddon 的只读静态审计成果

- 已按 archive 原路径重新提取并审阅10个 P3DAddon Python 文件；P1/P2 都是
  mesh/UV/normal/weight/skeleton 的导入导出路径，没有 animation/keyframe/TRAN/ZLIB
  实现。旧工具没有被安装、加载或执行。
- `Nixson.Prototype1.dll` / `Nixson.Prototype2.dll` 的静态元数据同样只显示 mesh/
  skeleton 操作；未发现 Alex 外部 channel IDs 的实现。
- 工具包捆绑、比仓库旧源码更丰富的 `Gibbed.Prototype.FileFormats.dll` 则给出一条
  可验证线索：`[KnownType(0x00121401)]` 精确归属为 `AnimationLimbReference`，其
  静态 IL 是 `Limb = ReadStringAlignedU8()` 后原样读写固定24字节`Unknown`。
  私有 Alex 全量只读验证：533个 PTRN 都恰有4项，固定为`leg_left`/`leg_right`/
  `arm_left`/`arm_right`。decoder 已无损输出它们的 name、chunk offset、24-byte hex，
  合成测试和全部533个 PTRN 的 reference-outer-layout 检查均通过。
- 这四项与四个双通道 limb group 共现，是 TRAN 标定的有价值线索；但 DLL 本身没有
  解释24字节，因此**绝不能**把其中的任何数擅自认定为 translation scale/offset。
  详情：`docs/p3daddon-static-audit.md` 和`docs/animation_format.md` §2.1。

### 🐛 历史已修复的两个严重bug（教训写在这里，避免以后重犯同类错误）

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

### 历史静态预览生成命令（保留参数参考，不直接照抄环境路径）

以下来自旧 agent 环境，不是本轮运行记录或 11 动作页的重建命令。优先找回现有产物；
按需重建时改用实际 checkout、已准备的私有输出目录及用户本机 `http://127.0.0.1:8420`，
并先确认隔离依赖安装方案。旧路径与临时 URL 不保证存在，不自动运行下列安装步骤。

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
`*.gltf`），只提交生成它们的脚本源码。先复用现有产物，仅在必要时重建，不保证旧耗时或环境可复现。

## 下一步：地图与音效（2026-09-15 用户确认）

### 0. 接住现有成果，不重复考古

1. 比对两端 `git status --short`、HEAD 和 `/api/health`，保留用户本地修改。
2. 定位 Alex 动画页、地图诊断页及私有 census/shelf JSON 的路径或生成命令。
   `tools/report/build_progress_dashboard.py` 是独立报告生成器，不是 8420 首页；
   元数据 bounds 地图也不是已加载城市三角形的完整地图。
3. 按需准备隔离依赖环境，不预装大批软件。当前用户默认 Python 缺少 NumPy、
   pygltflib、Playwright，不能假定导出和 headless 渲染开箱即用。

### 1. 地图：稳定单 Cell → 相邻 Cell → 整图按需加载

已有记录：260 基础 Cell = 149 非 placeholder + 111 placeholder；29 个 vertex layout，
143 个 Cell 有 `mergedDrawableRoot*`。Cell 2/3 的 92 个 core group、62,088 个三角形
通过严格连接诊断；68-byte layout 的 `color @24` 有两次用户 WebGL 对照证据，
normal/tangent 有三组几何证据。这些是既有成果，本轮未重跑全量普查。

**最新阻塞**：进度页记录 Cell 2 合并材质 diagnostic 未通过稳定可见验收，已经归档。
不能仅因脚本生成 HTML 就报告“单 Cell 完成”。恢复时先定位旧产物并复现失败，区分
页面交付/WebGL/相机/数据/材质等原因。重大阻塞先说明方案，不盲目调 shader 或扩大范围。

单 Cell 验收后，依据实际 bounds 选择 2–4 个空间相邻 Cell，验证位置、接缝、UV、
材质依赖和加载预算，再扩展整图。local-space 物件与 `_ft` 独立处理，不堆到世界原点。
具体数字、布局范围和工具见地图地基文档，不能把 Alex UV 规则外推给所有 static layout。

### 2. 音效：容器识别 → 小样本试听 → 索引

2026-09-15 经用户机器本地 `/api/rcf_manifest` 只读复核：

| Archive | 条目数 / 可解析名称 | 名称扩展名 | 当前结论 |
|---|---:|---|---|
| `00audio.rcf` | 13,486 / 13,486 | 全部 `.p3d` | `audio/english/AudioFile/…` 哈希式路径（此处用正斜杠展示） |
| `01audio.rcf` | 1,128 / 1,128 | 全部 `.p3d` | 两包共 14,614 条，不是去重后的音效数 |
| `00woi.rcf` | 25 / 25 | 13 `.bik` + 12 `.p3d` | 视频/配套容器候选，不作为纯音效计数 |

只读取条目元数据；没有验证音频 chunk、codec、时长、采样率或可播放性。其他 `woi`
archive 本轮仅确认存在。不要凭 `.p3d` 扩展名认定为模型，也不要凭 FFmpeg 已安装就
声称可解码。抽取少量代表条目的文件头/chunk 结构，在本机识别封装与编码后再选 decoder；
一条可重复的本地试听路径通过后，再建立检索、分类、失败报告和缓存。角色/动作关联需
额外引用证据，不由哈希式文件名猜测；原始与转码音频均不入库、不公开分发。

### 3. 保留队列：角色目录与完整动画

全 `art.rcf` rigged census、稳定 review shelf 和元数据主页生成器已经存在。
Blackwatch/NIS/gameplay 候选来源见角色地基文档；身份、通用缩略图、完整动画覆盖
仍逐项核验。只维护已有 Alex 播放基线，不为地图/音效重写这套流程。

## 格式逆向状态总览（按已验证角色样本限定范围）

全部记录在 `docs/vertex_format.md`（几何/骨骼/材质，第1-10节）和
`docs/animation_format.md`（动画）。这两份文档是角色格式证据来源；地图与角色普查另见对应地基文档。
技术结论必须保留样本和验证范围，本文件只做导航，细节请直接翻它们，不要凭记忆/印象转述。

关键结论速查表：

| 数据链 | 状态 | 权威文档位置 |
|---|---|---|
| 顶点/索引/PrimitiveGroup | ✅ 完整解码+落地验证 | vertex_format.md 第1-6节 |
| 包围盒/球 | ✅ 完整解码 | vertex_format.md 第7节 |
| Skin绑定关系 | ✅ 完整解码+三样本验证 | vertex_format.md 第8节 |
| Composite_Drawable_2部位组 | ✅ 完整解码 | vertex_format.md 第8b节 |
| 材质/贴图关联+DDS解码 | ✅ 完整解码+落地验证（含bug修复记录） | vertex_format.md 第8c节 |
| Alex UV朝向约定 | ✅ 不翻转；不外推到地图 | vertex_format.md 第3b节的"最终结论"框 |
| memory_imaged=0老式顶点格式 | ✅ 完整解码 | vertex_format.md 第8d节 |
| 骨架层级Skeleton_2 | ✅ 完整解码+落地验证 | vertex_format.md 第9节 |
| 三份骨架(body/arms/left_arm)等价性 | ✅ 已验证可共享 | 见下方"关键数据点" |
| 外部 ZLIB animation family | ✅ 严格解码；ROT-only 已接入 glTF | animation_format.md 第2–4、7–8节 |
| 标准内联 / `0x00121118` animation channels | 🟡 已识别，尚未解码 | animation_format.md 第5–6节 |
| 动画 TRAN 位移 scale/reference frame | ❌ 未标定 | animation_format.md 第6节"尚待验证" |

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
- **动画数量**：`alex.p3d.rz` 内嵌 **634个** `Animation`(0x121000) chunk：
  533个`PTRN`（骨骼）、67个`CAM`、34个`EXP`。`PTRN` 使用外部 ZLIB 和标准内联
  channel 的混合布局；不可假定 CAM/EXP 与其结构完全相同。

## 开发与连接方式（2026-09-15 核对）

- 用户项目：`/home/liang/prototype-Game-p3d`；游戏根目录：
  `/mnt/hdd/新建文件夹/steamapps/common/Prototype`。SSH 已以 `liang` 成功登录，
  使用客户端 `cloudflared access ssh --hostname <用户提供的主机名>`；首次必须核对
  用户提供的主机指纹。会话私钥、凭据及临时 tunnel URL 不写入仓库。
- **优先 SSH 调度、本机解析**：在用户机器调用 `http://127.0.0.1:8420` 的现有 API，
  能复用就不重写；只返回统计、错误和必要验收结果。网页仍用于真实视觉/试听验收。
  SSH 不自动提供 GPU 渲染；已识别 GTX 1080，但尚未验证 GPU 渲染环境。
- 用户默认环境：Python 3.13.5、Pillow、FFmpeg 可用；NumPy、pygltflib、trimesh、
  Playwright、pytest 未找到；Blender/Chromium 未在 PATH 找到。未普查其他虚拟环境。
  内存约 16 GiB，核对时 available 约 3.9 GiB，任务应分批并可续跑。
- Quick Tunnel URL 失效不代表查看器停止。SSH 用户权限不受 viewer `--root` 限制，
  只操作授权范围。公开 viewer URL 可读取开放资源；`POST /api/self_update` 能拉代码
  并重启，本轮未调用，后续须确认部署授权。不要把游戏产物或秘密放进公开服务目录。
- 长任务需可续跑批处理和私有日志，不能依赖 SSH 长连接；GLB、派生 HTML、音频、
  含关键帧的 JSON 留在私有产物目录，不提交。纯元数据报告也需明确保存位置与复现命令。
- 本轮 agent 工作区：`/workspace/project/prototype-Game-p3d`，独立浅克隆。
  不假设旧 `/home/user/...` 路径、密钥或缓存仍在；查历史前检查 shallow 状态，按需补历史。
  部署前比较实际文件，不能单凭提交标题判断功能差异。
- 格式证据、复现命令和验收结果应及时同步到本文及目标清单；提交、推送、PR、部署
  分开处理，未经用户明确授权不推送、不更新运行服务，不照搬旧交接里的自动 push 步骤。

关键 API：`/api/health`、`/api/rcf_manifest`、`/api/rcf_rigged_manifest`、
`/api/rcf_cell_geometry_manifest`、`/api/rcf_entry`；台账 schema 见
`docs/asset_inventory_schema.md`。跨请求用 `P3DSource` 追踪 chunk 时**不要传
`max_depth`**，它会改变跨调用的 `global_index`；应使用返回的 `depth` 本地裁剪子树。

## 一些容易踩坑的经验教训汇总

- P3D 字符串是 `1字节长度 + UTF8内容 + 补\0到长度` 格式，长度字节记录的
  是**补齐后**的长度，不是原始字符数——每次解析都要用hex实测校验。
- 第三方参考库（`references/gibbed-prototype` 为 Prototype 专用 C# 库，
  `references/netp3dlib` 为通用 P3D 标准 C# 库）的字段定义**曾经出现过
  不完整/过时/甚至这次发现"格式范式完全不同"的情况**（Prototype的
  Animation数据把帧数据外部化压缩存进独立blob，netp3dlib假设是内联
  存储）——**任何用参考库反推出的结论，最终都必须用真实游戏数据逐字节
  交叉验证，不能直接信代码**，但既有样本验证过的部分（Skin/CompositeDrawable2/
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
  "应该是对的"就呈现给用户——这是既有工作的重要效率教训，多次因为跳过这一
  步而来回拉锯。

## 2026-09-20 — FBX/音频/模拟器公开同步

- 实体解码器现支持颜色/法线/镜面贴图、Skeleton/Skin、TRAN/ROT/SCALE 动画通道、旧格式 Matrix/Weight 正确配对和 `UV_List(count, channel, Vector2[])`。
- 实体和已加载 Manhattan Cells 可导出真正的二进制 FBX 7.4；转换依赖用户本机 Blender 4.5 LTS，不在仓库分发 Blender。
- 每个有骨架的导出新增独立 `000_A_POSE_BIND` 默认 Take；源动画关键帧保持不变，通过独立 NLA Take 导出。不要把 Armature 切到 REST 后烘焙，否则所有 Take 会静止。
- Unity 贴图以 FBX 同目录 PNG 交付，不依赖 Embedded Media。单项下载为 FBX 资源 ZIP；批量路径输出为每实体独立目录。
- 实体抽屉支持按当前类别/搜索过滤批量导出、覆盖策略、后台任务状态及安全输出路径。地图可下载 ZIP或保存到安全输出路径。
- 输出路径只允许位于 `PROTOTYPE_EXPORT_ROOT`；Blender 由 `PROTOTYPE_BLENDER` 指定。详见 `docs/fbx-export.md`。
- `tools/audio/extract_all_audio.py` 可递归提取 archive entry 中的所有 AudioFile 对象并将 RADP 写为 WAV；支持 1..32 声道和可恢复运行。
- 仓库不包含游戏本体、导出 FBX/PNG/WAV、Blender、RCF、DLL 或其他受版权保护字节。
- 当前地图 FBX 仍仅覆盖严格验证的 Cell 城区主体和已解析材质；放置实体、特效、碰撞/导航等未解码记录不得声称完整。
