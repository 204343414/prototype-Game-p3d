# 交接记事本 —— 给下一个接手的人/AI 看这一份就够了

> 这份文件的唯一目的：如果当前对话/会话突然中断，新开一个对话的 LLM
> （或人类）只要读这一个文件，就能知道"现在做到哪、下一步该干嘛"，
> 不需要翻聊天记录。**每完成一个阶段性成果就更新这里，不要拖延。**
> 详细的字节级格式定义不重复贴在这里，只给"结论 + 指向哪个文件/哪一节"。

最后更新：2026-09-14（对应 git commit `69786d4`）

## 项目一句话说明

给《Prototype》(2009) 游戏的 `.p3d` (Pure3D引擎) 资源文件做逆向解析，
最终目标是把角色模型（含骨骼动画、贴图）导出成 glTF，用于制作一个
VRChat 内的《Prototype》游戏考古展览/纪念馆（预览角色 + 如果可能预览地图）。

## 最终用户需求（务必记住，别偏离）

用户是想做 **VRChat 展览**，角色优先级（原话）：
Blackwatch黑色守望×2、海军陆战队×2、Hunter猎手、Alpha Hunter大猎手、
终极猎手、Elizabeth Greene(E妈)、Cross上校、**Alex Mercer(阿哥，当前模板角色)**、
各类强化感染僵尸。平民NPC可以不做。**若能顺带预览完整游戏地图会更好**。

策略：先把 Alex Mercer 一个角色的完整流程（解析→绑定→贴图→glTF→预览）
跑通当模板，再横向复制到其他角色；地图解析器要写得通用。

**当前这一步用户要的是**：先做出一个 **T-pose 角色的预览**（哪怕先只有
Alex Mercer 一个角色能看），之后再考虑"道具栏/角色选择界面"式的多角色
排版预览页面。**预览渲染在 workspace 的 file viewer 里进行**（不是要求
搭一个复杂的 web app），如果技术上做不到就明说，交给下一个对话接手。

## 当前状态：Alex Mercer 身体部位组(T-pose) 已经能生成 .glb 并在浏览器里预览成功

**本轮完成的关键里程碑**：
- `tools/p3d_export/export_alex_body.py`：一次性 Python 脚本，把
  `alex_reg_body` 部位组（身体+头+外套三个Skin，共享67根骨骼的
  `alex_reg_body_skeleton`）的顶点/索引/蒙皮权重/骨骼层级/baseColor贴图
  全部拉取、解析、拼装成一个合法的 `.glb` 文件。已跑通验证成功
  （生成的 glb 约1.87MB，pygltflib能正常load_binary读回，
  scenes/nodes/meshes/skins/materials/images数量均符合预期）。
- 过程中发现`alex_reg_body_AlexVestShape`(外套)用的是**跟身体/头部不同的
  顶点存储格式**(`memory_imaged=0`，老式"独立List chunk"而非"打包
  Memory_Image格式")——已完整解析并写进 `docs/vertex_format.md` 第8d节，
  导出脚本已做分支兼容处理。
- 贴图管线：用 `texture2ddecoder` 库解压DXT1/DXT5裸数据成RGBA，
  `Pillow`转码成PNG内嵌进glb（第一版只接入了baseColor贴图，normal/specular
  贴图的转码逻辑已写好但默认关闭，见脚本 `--with-normal-map` 参数）。
- 预览方案：workspace 文件预览器**不支持直接渲染 .glb 3D文件**（只支持
  文本/Markdown/HTML/SVG/图片/音频/视频/PDF/CSV/Office几类）。解法是写了
  一个**自包含单文件HTML**（`viewer/vendor/three/`里下载好的 three.js r128
  + GLTFLoader.js + OrbitControls.js 全部内嵌进`<script>`标签，模型数据
  base64编码内嵌），不依赖任何外部CDN/网络，可以在沙盒`allow-scripts`
  无网络iframe里正常跑 three.js 渲染。生成脚本见下方"如何重新生成预览"。
- **重要**：导出的 `.glb`/预览用HTML 因为内嵌了游戏原始贴图数据（版权内容），
  按 `NOTICE.md` 的规定**不提交进git仓库**（已加入 `.gitignore`：
  `samples/exported/`、`*.glb`、`*.gltf`），只提交生成它们的 Python 脚本
  源代码。每次新会话需要预览时，重新跑一次导出脚本+HTML打包脚本即可
  （不到30秒，见下方命令）。

**如何重新生成预览**（新会话如果 `samples/exported/` 下没有文件，跑这个，
两个脚本都已存成正式文件，不用现场重写）：
```bash
cd /home/user/prototype-p3d-toolkit/tools/p3d_export
pip install pygltflib numpy pillow texture2ddecoder   # 如果还没装

# 1) 从游戏文件导出 .glb
python3 export_alex_body.py \
  --base-url "<用户当前隧道URL>" \
  --rcf-path "/mnt/hdd/新建文件夹/steamapps/common/Prototype/art.rcf" \
  --entry-name "\art\alex\alex.p3d.rz" \
  --out /home/user/prototype-p3d-toolkit/samples/exported/alex_reg_body.glb

# 2) 打包成自包含 HTML 预览页 (three.js全部内嵌，无外部依赖)
python3 pack_preview_html.py \
  --glb /home/user/prototype-p3d-toolkit/samples/exported/alex_reg_body.glb \
  --out /home/user/prototype-p3d-toolkit/samples/exported/alex_reg_body_preview.html \
  --title "Alex Mercer 身体部位组预览" \
  --subtitle "alex_reg_body (身体+头部+外套, T/A-pose静止姿势)"
```
`pack_preview_html.py` 是通用的（不针对Alex专用），传入任意 `.glb` +
标题/副标题/相机位置参数即可生成对应预览页，后续做其他角色/"道具栏
多角色选择页"时可以直接复用这个脚本，不用改。

## 格式逆向状态：三条数据链全部打通（几何/骨骼/材质）

### 已经 100% 解码并验证过的（可以直接拿来写导出代码，不用再猜）：

全部记录在 `docs/vertex_format.md`，按章节：

1. **顶点/索引/PrimitiveGroup** (第1-6节)：`0x00010020` PrimitiveGroup 头部、
   `0x00010012` 顶点缓冲（56B主缓冲+12B颜色UV缓冲两种）、`0x00010013`索引缓冲、
   `0x0001000D` Matrix_Palette（骨骼调色板）、`0x00010014`顶点声明表。
2. **包围盒/球** (第7节)：`0x00010003`/`0x00010004`。
3. **Skin 绑定关系** (第8节)：`0x00010001` Skin chunk = `name+version+skeleton_name
   +num_primitive_groups`，回答"这块网格用哪副骨架"。alex.p3d.rz 里有11个Skin，
   身体/右臂/左臂是**三套独立骨架**（不是同一骨架别名）。
4. **总装配图** (第8b节)：`Composite_Drawable_2`(0x00123000) + 
   `Composite_Drawable_Primitive`(0x00123001)，比 Skin 更高一层，把多个 Skin
   打包成"部位组"（如 `alex_reg_body` 组含头/外套/身体三个Skin）。
   **对以后做换装/拆件魔改极重要**——同组内Skin共享骨架，不同组完全独立。
5. **材质/贴图关联** (第8c节，本轮新完成)：完整链路
   `PrimitiveGroup.shader_name` → 同名 `NewShader`(0x00011015) chunk →
   参数字典子chunk(`0x00011016`纹理/`0x00011017`浮点/`0x00011018`向量2) →
   贴图文件名(如`alex_body_dm.dds`) → 同名 `Texture`(0x00019000) chunk →
   `TextureDDS`(0x00019006，给出DXT1/DXT5压缩格式+真实mipmap数) →
   `Image_Data`(0x00019002，**payload就是裸DXT压缩字节流本体**)。
   已给出"如何拼DDS文件头+裸数据=合法.dds文件"的方案，但**还没有实际
   生成过一个.dds文件去验证**（这是本轮遗留的唯一"已知格式但未落地测试"项）。
6. **骨架层级** (第9节)：`Skeleton_2`/`Skeleton_Joint_2`，含局部矩阵→世界矩阵
   级联公式（已验证，见文档），67根骨骼数据完整，T-pose/A-pose姿态可复现。

### 结论：几何+骨骼+材质三条数据链全部打通，具备写glTF导出脚本的全部前置知识。

## 下一步要做的事（按顺序）

1. **写一个一次性 Python 导出脚本**（建议放在 `tools/p3d_export/` 或类似目录），
   针对 Alex Mercer 的 `alex_reg_body` 组（身体+头+外套三个Skin，共享
   `alex_reg_body_skeleton`），把顶点/索引/蒙皮权重/骨骼层级/贴图全部拼成
   一个 `.glb` 文件：
   - 几何+骨骼部分：按 `docs/vertex_format.md` 第1-9节的格式读取即可，
     已全部验证，不需要再猜。
   - 贴图部分：需要先把 DXT1/DXT5 裸数据解压成 RGB/RGBA 像素（可以用
     `Pillow` + 自己实现 DXT 解码，或者找现成的库如 `texture2ddecoder`/
     `imagecodecs`，Python 环境里搜一下能不能直接 pip 装），因为 glTF
     标准贴图槽不直接支持 DXT，需要转码成 PNG 内嵌进 glb。
     （备选：如果解压库不好找，也可以先按第8c.2节方案拼出合法 `.dds` 文件，
     再用系统工具/Pillow-DDS插件转 PNG，两步走。）
   - 用 `pygltflib` 或手写 JSON+二进制 buffer 拼 `.glb`（Python 生态常见做法）。
2. **验证 T-pose 预览**：生成的 `.glb` 丢进 workspace，用 `present_file`
   打开——workspace 的文件预览器据了解支持不了 3D glb 直接转起来看
   （只列出支持格式：文本/Markdown/HTML/SVG/图片/音频/视频/PDF/CSV/Office），
   **所以大概率需要写一个简单的 HTML+Three.js（或 model-viewer web component）
   页面来加载这个 .glb 并渲染出来，把这个 HTML 作为要展示的"预览文件"**，
   或者用 `start_process` 起一个本地 http 服务器 + 用 `<model-viewer>`
   网页组件（CDN引入，但沙盒iframe无网络，所以要把 three.js/model-viewer
   的 JS 库内容也内嵌/离线打包进 HTML，不能指望外部CDN能加载）。
   **这是当前最大的不确定项，需要下一步动手试才知道可行性，如果卡住了
   要如实告诉用户，不要硬撑。**
3. 做完 Alex Mercer 单角色预览后，用户要的下一步是**"道具栏界面"式的
   多角色选择/排版预览页面**——一次性列出上面14项优先角色列表，点哪个看哪个。
   这个要等单角色流程跑通后再做。
4. 更远期：材质/贴图关联里还有几个"用途未知但不阻塞主线"的小尾巴
   （`0x00011020`、`0x0900000A`、`Skeleton_Partition`），优先级低，
   不用主动去查，除非导出脚本实测发现真的需要它们。
5. 更远期：地图/Room 级 chunk 解析（用户要求"能预览完整地图更好"），
   目前只有 Dark Angel 文档里的概念级线索（`tlDAPortalChunk`等类名），
   没有做过实际解析，是独立于角色导出的另一条线，可以在角色流程稳定后再启动。

## 环境/基础设施现状（怎么连上用户的真实游戏文件）

- 用户本地运行 `run_viewer.sh` 起了一个本地 HTTP API 服务器（代码在
  `viewer/server.py`），通过 Cloudflare Quick Tunnel 暴露一个临时公网 URL。
  **这个 URL 每次用户重开隧道都会变**，当前记录的
  `https://journalists-fit-snap-buttons.trycloudflare.com` **可能已经失效**
  ——如果新会话开始时这个 URL 连不通，需要请用户重新运行 `run_viewer.sh
  --tunnel` 并把新 URL 发过来。
- 该 API 有 `POST /api/self_update` 端点，能让用户那台机器上跑着的
  server 自己 `git pull` 到最新 commit（无需用户手动操作）。**每次新会话
  一开始，应该先调用一次这个端点，确认对方代码是最新的**，用法见
  `README.md` 及 `viewer/server.py` 里 `_handle_self_update` 的实现。
- 关键 API：`GET /api/rcf_entry?path=<rcf路径>&name=<归档内路径>&type_filter=
  0x前缀十六进制&offset=&limit=&payload_preview=`——从 `.rcf` 归档里按名字
  取出条目（自动解压`.rz`），解析成chunk树JSON返回，`payload_preview`最大
  支持 8MB（一次性拿完整顶点/贴图缓冲用这个）。
- 用户游戏路径：`/mnt/hdd/新建文件夹/steamapps/common/Prototype`，
  `art.rcf` 里 `\art\alex\alex.p3d.rz` 是 Alex Mercer 模型（本项目当前
  所有已验证数据均来自这一个文件）。

## 本地开发环境

- assistant 自己有一份独立 git clone 在 `/home/user/prototype-p3d-toolkit`
  （即当前你正在读这份文件的地方），分支 `master`，用于开发/测试/commit/push。
- 远程仓库：`https://github.com/204343414/prototype-Game-p3d`（国内访问
  可能需要走 `ghfast.top` 镜像，具体见 git remote 配置或 self_update 的
  git 输出日志）。
- **每次做出格式发现，先写进 `docs/vertex_format.md` 对应章节，再
  commit+push，再回来更新这份 `HANDOFF.md`**——顺序不要反，`vertex_format.md`
  才是格式定义的唯一权威来源，这份文件只是"导航牌"。

## 一些容易踩坑的经验教训（详见 docs/vertex_format.md 底部"验证方法总结"）

- P3D 字符串是 `1字节长度 + UTF8内容 + 补\0到长度` 格式，长度字节记录的是
  **补齐后**的长度，不是原始字符数——每次解析都要用 hex 实测校验，不要
  凭字符串长度直接套公式。
- 第三方参考库（`references/gibbed-prototype` 为 Prototype 专用 C# 库，
  `references/netp3dlib` 为通用 P3D 标准 C# 库）的字段定义**曾经出现过
  不完整/过时的情况**（如 `SkeletonJoint2Chunk.cs` 漏了54字节没建模），
  必须用实测 hex 数据交叉验证，不能直接信代码。但本轮用到的 `SkinChunk.cs`
  `CompositeDrawable2Chunk.cs`/`NewShader.cs`/`TextureChunk.cs`/
  `TextureDDS.cs` 等均已 100% 精确验证通过，可以放心直接用。
