# Prototype P3D Archive Toolkit

> ⚠️ **使用前必读版权声明：[NOTICE.md](./NOTICE.md)**
> 本项目仅为文件格式研究/逆向工程工具，不含任何游戏资源本体。
> 提取内容仅限个人学习、技术研究与非商业同人创作用途，
> **严禁商用、严禁公开重新分发**。

> 一个面向《虐杀原形》(*Prototype*, Radical Entertainment, 2009) 及其续作
> 的资源考古 / 逆向工程 / 本地预览工具集。
>
> 目标：把 `.rcf` 归档里的 `.p3d` (Pure3D) 网格、骨骼、动画资源解析出来，
> 转换成 glTF 等通用格式，并提供一个可以在浏览器里通过
> `http://127.0.0.1:<port>` 直接查看/调试的本地 viewer —— 方便 VRChat 换装、
> 同人动画等"考古整活"用途。
>
> 集百家之长：本项目不重新发明轮子，而是系统性地整理、移植、验证社区里
> 已经存在但分散在各处（且大多只支持 Windows/.NET）的 Pure3D 逆向成果，
> 把它们做成一套能在 Linux（含 CI、容器、沙盒环境）上直接运行的、
> 面向 Python + Web 的现代工具链。也欢迎后来的人类或 AI 在这个基础上继续修。

> **当前状态与任务顺序（2026-09-14）**：Alex Mercer 全身静态预览（5 个
> Skin、骨架、贴图、原始 UV）已经真实渲染验证通过；下一项工程里程碑是让
> Alex 动画可靠播放，而不是重新排查 UV。完整交接见 [`HANDOFF.md`](./HANDOFF.md)，
> 项目地基/角色/动画/音效/设计/最终导出的顺序和验收门槛见
> [`docs/PROJECT_TARGETS.md`](./docs/PROJECT_TARGETS.md)。本文后部保留了一些
> 项目早期调研记录，不能替代前两份当前文档。

## 现状 TL;DR（写给下一个接手的人，人类或 AI 都一样）

- 《虐杀原形》使用 Radical Entertainment 自家的 **Pure3D 引擎**的一个变种。
  资源都塞在 `.rcf` 归档里，模型/骨骼/动画/贴图都以 `.p3d` chunk 树的形式存在。
- 本项目已用真实 `alex.p3d.rz` 跑通“从本地归档条目导出带蒙皮/贴图的 GLB，
  再用自包含 three.js 页面真实预览”的最小闭环。当前重点从静态模型验证转为
  通用解析地基、骨骼动画、角色普查和音效基础设施；最终 VRChat 导出留到展览
  设计与性能预算收敛之后。
- Prototype 官方/社区工具年代久远（2009-2012），全部是 Windows-only 的
  .NET / WinForms+DirectX GUI，且**从未真正解决蒙皮网格+动画的导出问题**
  （参见 `references/gibbed-prototype` 的历史 issue 讨论）。
- 幸运的是：Pure3D 引擎的姊妹产品《辛普森一家：飞车正传》(The Simpsons: Hit & Run)
  的 modding 社区规模大得多、活跃到现在（2026年仍有提交），已经把 Pure3D
  chunk 格式逆向得非常完整，包括顶点/骨骼/动画数据的具体二进制布局，
  以及之前长期是"TODO"的 LZR 压缩算法。这些成果可以拿来跟 Prototype 的
  chunk 定义交叉验证、直接复用（两边同源，字段几乎一一对应，只是部分
  chunk 换了 ID 或版本号，见下方"版本差异记录"）。

## 目录结构

```
prototype-p3d-toolkit/
├── README.md                  <- 本文件，项目简介与历史背景
├── HANDOFF.md                 <- 当前技术状态、已验证结论与接手说明
├── docs/
│   ├── PROJECT_TARGETS.md     <- 项目目标、建设顺序与阶段验收门槛
│   ├── vertex_format.md       <- 网格、Skin、贴图、UV、骨架的已验证格式
│   ├── animation_format.md    <- Prototype 混合动画布局与外部 ZLIB family 的已验证格式
│   ├── p3daddon-static-audit.md <- 用户提供旧 P3DAddon 的只读静态审计与动画线索
│   ├── asset_inventory_schema.md <- RCF 元数据资产台账规范
│   ├── pure3d-format.md       <- Pure3D 文件/chunk 格式详细笔记
│   ├── chunk-id-crosswalk.md  <- Prototype <-> Hit&Run chunk ID 对照表
│   └── roadmap.md             <- 早期历史路线图（已过时）
├── tools/
│   ├── rcf_unpack/            <- .rcf (Cement) 归档解包（含合成测试）
│   ├── p3d_parser/            <- 通用 Pure3D chunk 树解析器
│   ├── p3d_export/            <- Alex 静态 GLB 导出、HTML 预览和截图验证脚本
│   ├── p3d_animation/         <- Prototype 外部 ZLIB 动画 blob decoder（诊断 JSON）
│   ├── p3d2gltf/              <- 通用 p3d -> glTF 解析/导出层（动画工程化待建）
│   └── inventory/             <- 解包目录/RCF 元数据清点工具（含测试）
├── viewer/                    <- 本地 web viewer（three.js）
├── run_viewer.sh              <- 一键启动本地查看器
├── samples/                   <- 测试/样例资源（不含受版权保护的游戏本体资源）
└── references/                <- 参考实现（仅供阅读/交叉验证）
```

## 已验证可用

- `tools/p3d_parser/inspect_p3d.py` — 通用 Pure3D chunk 树 walker。
  已经在 `references/gibbed-prototype/other/testfiles/*.p3d`
  （仓库自带的真实 Prototype 资源样本）上跑通，能正确识别文件头、
  chunk 层级、类型 ID、payload 长度。

  ```bash
  python3 tools/p3d_parser/inspect_p3d.py path/to/file.p3d
  ```

- `tools/rcf_unpack/rcf_extract.py` — `.rcf` (ATG CORE CEMENT LIBRARY) 归档
  解包器，从 `gibbed-prototype` 的 C# 源码逐行移植。合成 RCF 测试覆盖文件头、
  Entry/Metadata 表、文件名哈希和 `.rz` (zlib) 解压；随后已通过 viewer 的
  元数据 API 对用户本地真实 `art.rcf` 验证 RCF 2.1、2,601 条目和 2,601 条可
  解析 metadata 名称。完整的 payload 解包/导出仍应对每类资源走专门回归测试。

  ```bash
  python3 tools/rcf_unpack/rcf_extract.py path/to/art.rcf output_dir/ --rz -v
  ```

- `tools/p3d_animation/decode_animation.py` — 读取 Prototype `Animation`
  的外部 ZLIB blob 并输出私有诊断 JSON；已用合成夹具和真实
  `alex_act_block`（53 groups / 60 channels）验证。它保留 TRAN 原始整数值，
  目前**不**声称已完成 TRAN 标定或 glTF animation 导出；详见
  `docs/animation_format.md`。

- `tools/inventory/scan_assets.py` — 扫描一个已解包目录，按文件名/路径
  关键词把资源粗分类为角色/武器/形态变身/动画/关卡/音频/贴图/UI等，
  输出 JSON + Markdown 清单。目前分类关键词是基于命名习惯的猜测，
  需要用真实资源目录跑过之后持续修正关键词表。

  ```bash
  python3 tools/inventory/scan_assets.py path/to/unpacked_dir \
      --json docs/asset_inventory.json --md docs/asset_inventory.md
  ```

- `viewer/` — 本地 web 查看器。纯 Python 标准库后端（无需 `pip install`
  任何东西）+ three.js 前端。已用自动化测试（`viewer/test_server.py`）
  验证目录浏览 API、文件读取 API、`--root` 越权访问拦截、静态页面服务
  均正常工作；另有 `viewer/test_self_update.py` 专门验证下面的自更新接口。

  ```bash
  ./run_viewer.sh                              # 默认端口 8420，可浏览整个文件系统
  ./run_viewer.sh 8420 /path/to/unpacked        # 限制只能浏览指定目录（推荐，更安全）
  ```

  额外提供几个供协作 AI 通过隧道 URL 远程调用的只读/半只读 API
  （均已用合成数据自动化测试覆盖，细节见 `viewer/server.py` 里各
  `_handle_*` 方法的 docstring）：

  - `GET /api/hexdump?path=...&offset=&length=` — 读任意文件的字节区间
  - `GET /api/p3d?path=...` — 解析任意 `.p3d` 文件的 chunk 树
  - `GET /api/rcf_manifest?path=...&name_filter=&limit=` — 解析 `.rcf`
    归档的完整条目清单（文件名、哈希、offset、size），不解压任何内容
  - `GET /api/rcf_entry?path=...&name=...&raw=` — 一步到位：按文件名从
    `.rcf` 里取出一个条目、自动做 `.rz` 解压，返回其 chunk 树（或
    `&raw=1` 拿解压后的原始字节）
  - `POST /api/self_update` — 让 server 自己 `git pull` 并在**原端口**
    原地重启（公网隧道 URL 不变），免去人工 `git pull` + 重开终端的
    步骤。⚠️ 这意味着拿到隧道 URL 的人也能触发这个操作，风险等级和
    "隧道本身不加访问口令"这条已知的设计取舍一致，见 `run_viewer.sh`
    顶部的安全须知。

  启动后终端会常驻显示 `http://127.0.0.1:8420/`，在浏览器打开即可：
  左侧输入本地路径 → 浏览目录 → 点击 `.glb`/`.gltf` 文件即可在右侧
  three.js 场景里预览（支持旋转/缩放/平移、自动播放第一个动画片段）。
  **注意**：目前还不支持直接预览原始 `.p3d`，需要先用 `tools/p3d2gltf`
  （待写）转换成 `.glb`。

## 重大发现：2009年3DM工作室汉化工具反编译交叉验证

当年汉化组用的《虐杀原形》素材编辑工具（`PackageManager.exe` /
`P3DManipulator.exe`，2009年3DM工作室出品）是 .NET 程序，已用 `ilspycmd`
完整反编译出可读源码，**独立验证**了我们的格式假设：

- ✅ **再次证实 `.rz` 是标准 DEFLATE/zlib**：反编译代码里内嵌了一份手写的
  DEFLATE 编解码器，用的正是 RFC1951 标准参数（286 literal/length符号、
  30 distance符号、19 code-length符号），与从 Gibbed.Prototype 源码
  读到的结论完全吻合，双重来源印证。
- ✅ **P3D chunk 树结构确认**：`type_id + total_size + payload_size` 共
  12字节头 + payload + 递归子chunk，与我们 `inspect_p3d.py` 的实现一致。
- 🆕 **新发现两组此前未知的 chunk**：`0x00019002`（贴图，可能是DDS而非
  只有PNG）配合4字节头、`0x00018201`/`0x00018202`（字符串索引表对，
  疑似本地化文本相关）。已补充进 `docs/chunk-id-crosswalk.md`。
- ⚠️ **发现一处需要用真实文件核实的差异**：RCF 文件头的 magic 字段
  这份反编译代码读的是 32 字节，而我们从 Gibbed 源码实现的是 24字节
  magic + 8字节独立padding——两者数值上可能等价，但需要真实 `.rcf`
  文件的 hex dump 才能确认，直接关系到后续所有 offset 计算是否正确。

详见 `docs/2009-3dm-tool-findings.md`（完整反编译发现记录）。

## 历史未完成项（仅作早期研究记录，勿据此排期）

> ⚠️ 以下内容写于拿到真实游戏文件之前，其中“真实 RCF 未验证”“glTF 待写”等
> 描述已被后续 Alex 实测成果部分推翻。当前任务请只看 `HANDOFF.md` 与
> `docs/PROJECT_TARGETS.md`。

1. **压缩支持**：`.rcf` 里大部分 `.p3d` 是 LZR 压缩过的（签名 `P3DZ`）。
   `references/netp3dlib` 里有完整的 LZR 解压 C# 实现，需要移植成 Python
   （`docs/roadmap.md` 有详细计划），但目前手头没有压缩样本文件测试。
2. **顶点/骨骼/动画 chunk 的 Python 解析**：字段结构已经通过对照
   `gibbed-prototype` + `netp3dlib` 两份源码确认（见
   `docs/chunk-id-crosswalk.md`），但还没有在真正带蒙皮网格的 Prototype
   `.p3d` 文件上跑通过 —— 需要真实游戏解包出来的角色模型文件来验证。
3. **glTF 导出**：管线设计已经想清楚（见 `docs/roadmap.md`），代码骨架待写。
4. **Web viewer**：计划直接复用/魔改现成的开源 three.js viewer
   （如 `sboez/3D-Models-Load`），把它包进一个本地 HTTP server，
   目录结构已预留 `viewer/`。

## 人类 + AI 协作模式：本地文件隧道

`run_viewer.sh` 支持 `--tunnel` 参数，会启动一条公网隧道，把
`viewer/server.py` 的目录浏览 / 文件读取 API 通过一个临时公网 URL
暴露出来。协作的 AI 拿到这个 URL 之后，可以直接读取被 `--root` 限定的
目录内容（比如整个项目目录），不需要你手动复制粘贴代码或文件结构。

隧道有两种实现方式，脚本会自动选择（`TUNNEL_MODE=auto`，默认）：

1. **[Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)**：
   需要先下载一个约 40MB 的 `cloudflared` 二进制文件（首次使用，缓存
   在 `.cloudflared/`）。**在国内网络环境下，GitHub Releases 的下载经常
   被限速甚至卡住不动**——脚本设置了 25 秒下载超时，超时会自动改用
   下面的 SSH 隧道方式，不会无限期卡住。
2. **SSH 隧道（[localhost.run](https://localhost.run/)）**：用系统自带的
   `ssh` 命令直接连出去，不需要下载任何额外程序，通常比下载 GitHub
   二进制文件更容易穿过网络限制。首次连接会自动接受主机指纹。

```bash
./run_viewer.sh 8420 /path/to/prototype-p3d-toolkit --tunnel

# 如果知道自己的网络下载 GitHub Releases 比较困难，可以直接强制用 SSH 隧道，
# 跳过 cloudflared 下载尝试，启动更快：
TUNNEL_MODE=ssh ./run_viewer.sh 8420 /path/to/prototype-p3d-toolkit --tunnel
```

终端会打印出一个 URL（cloudflared 是 `https://xxxx-yyyy.trycloudflare.com`
形式，SSH 隧道是 `https://xxxxx.lhr.life` 形式），把它发给协作的 AI 即可
（例如让它访问 `<url>/api/browse?path=/home/you/prototype-p3d-toolkit`
或 `<url>/api/file?path=/home/you/prototype-p3d-toolkit/README.md`）。

⚠️ **安全提醒**：这条隧道在开着的时候，任何拿到这个 URL 的人都能读取
`--root` 范围内的所有文件（URL 本身是随机生成、难以被扫到，但不代表
绝对安全）。用完记得 `Ctrl+C` 关掉；不要把 `--root` 指向包含真实游戏
资源本体的目录再对外开隧道。

## 贡献 / 协作方式

这个仓库是"人类 + AI 协作考古"项目。约定：
- 每次重要的格式逆向发现，都写进 `docs/`，不要只留在 commit message 里。
- 遇到无法验证的假设，标注清楚"未验证"，不要把猜测当结论写死。
- 优先复用/移植现有开源实现，注明来源和 license，而不是盲目重新造轮子。

## 参考资料 & 致谢

- Rick "Gibbed" — [gibbed/Gibbed.Prototype](https://github.com/gibbed/Gibbed.Prototype)
- Hampo (Proddy) / Loren (duckdotapk) — [Hampo/NetP3DLib](https://github.com/Hampo/NetP3DLib)
- Donut Team — [Pure3D Files 文档](https://docs.donutteam.com/docs/Pure3DFiles/Intro)
- ermaccer — [SCFExtract](https://github.com/ermaccer/SCFExtract)（Scarface/.rcf 提取）
- handsomematt — [Pure3D](https://github.com/handsomematt/Pure3D)（另一份 .NET Pure3D 库）
