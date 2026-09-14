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

## 现状 TL;DR（写给下一个接手的人，人类或 AI 都一样）

- 《虐杀原形》使用 Radical Entertainment 自家的 **Pure3D 引擎**的一个变种。
  资源都塞在 `.rcf` 归档里，模型/骨骼/动画/贴图都以 `.p3d` chunk 树的形式存在。
- 目前**没有**任何项目做到「一键起本地 web 服务器 + 浏览器直接看 Prototype 模型」
  这个体验（不像 CryEngine 类游戏有类似 Hunt1896PAK 那样的先例）。
  这正是本项目要填的空白。
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
├── README.md                  <- 本文件，项目总纲 & 状态记录
├── docs/
│   ├── pure3d-format.md       <- Pure3D 文件/chunk 格式详细笔记
│   ├── chunk-id-crosswalk.md  <- Prototype <-> Hit&Run chunk ID 对照表
│   └── roadmap.md             <- 开发路线图与 TODO
├── tools/
│   ├── rcf_unpack/            <- .rcf (Cement) 归档解包 (Python 移植，自测通过，未测真实文件)
│   │   └── rcf_extract.py
│   ├── p3d_parser/            <- 通用 Pure3D chunk 树解析器 (Python)
│   │   └── inspect_p3d.py     <- 已验证可用：任意 .p3d 文件的 chunk 树 dump 工具
│   ├── p3d2gltf/              <- p3d (网格/骨骼/动画) -> glTF 转换器（待写）
│   └── inventory/             <- 资源清点扫描工具（按文件名关键词粗分类）
│       └── scan_assets.py
├── viewer/                    <- 本地 web viewer（three.js，浏览器 127.0.0.1 预览，已验证可用）
│   ├── server.py              <- 纯标准库 HTTP server：目录浏览 + 文件读取 API
│   └── static/index.html      <- 前端页面：输入路径浏览 + glTF/GLB 预览
├── run_viewer.sh              <- 一键启动本地查看器（见下方用法）
├── samples/                   <- 测试/样例资源（不含受版权保护的游戏本体资源）
└── references/                <- clone 下来的参考实现（仅供阅读/交叉验证，见 LICENSE 各自条款）
    ├── gibbed-prototype/      <- gibbed/Gibbed.Prototype（Prototype 专用，2012年停更）
    └── netp3dlib/             <- Hampo/NetP3DLib（Hit&Run 专用，持续维护，格式细节最全）
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
  解包器，从 `gibbed-prototype` 的 C# 源码逐行移植。已用**自造的合成 .rcf
  文件**验证了文件头解析、Entry/Metadata 表解析、文件名哈希查找、以及
  `.rz` (zlib) 解压逻辑的自洽性（`test_rcf_extract.py`），但**还没有用真实
  游戏 `.rcf` 文件测试过** —— 这是当前最需要验证的一环，一旦拿到
  `art.rcf` 之类的真实文件应优先跑这个。

  ```bash
  python3 tools/rcf_unpack/rcf_extract.py path/to/art.rcf output_dir/ --rz -v
  ```

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
  均正常工作。

  ```bash
  ./run_viewer.sh                              # 默认端口 8420，可浏览整个文件系统
  ./run_viewer.sh 8420 /path/to/unpacked        # 限制只能浏览指定目录（推荐，更安全）
  ```

  启动后终端会常驻显示 `http://127.0.0.1:8420/`，在浏览器打开即可：
  左侧输入本地路径 → 浏览目录 → 点击 `.glb`/`.gltf` 文件即可在右侧
  three.js 场景里预览（支持旋转/缩放/平移、自动播放第一个动画片段）。
  **注意**：目前还不支持直接预览原始 `.p3d`，需要先用 `tools/p3d2gltf`
  （待写）转换成 `.glb`。

## 还没做 / 需要真实游戏资源才能继续验证

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
