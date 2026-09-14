# 路线图 / TODO

## Phase 0 — 调研（已完成大部分）

- [x] 确认 Prototype 使用 Pure3D 引擎变种，找到专用工具
      (`gibbed/Gibbed.Prototype`)
- [x] 找到姊妹项目 Hit&Run 的现代 C# 实现 (`Hampo/NetP3DLib`)，
      格式细节比 Prototype 专用工具完整得多
- [x] 验证文件头/chunk 树结构，写出能跑的通用 chunk dumper
      (`tools/p3d_parser/inspect_p3d.py`)，已在仓库自带样本文件上测试通过
- [x] 交叉比对核心 mesh/skeleton/animation chunk 定义，产出对照表
      (`docs/chunk-id-crosswalk.md`)
- [ ] 阅读 `.rcf` (Cement) 归档格式的具体实现细节，写进
      `docs/pure3d-format.md` 的 "RCF 归档格式" 章节（目前只列了参考链接，
      没有细读代码）

## Phase 1 — 拿到真实游戏资源后要做的第一批验证

> 这些都需要用户提供的真实游戏文件才能推进，纯读源码走不下去了。

- [ ] 用真实的 `art.rcf` 测试解包工具（先试试直接编译
      `references/gibbed-prototype/Gibbed.Prototype.Unpack`
      —— 需要 mono 或 .NET SDK 环境；备选用 SCFExtract）
- [ ] 从解包结果里找一个带蒙皮网格的角色 `.p3d`
      （优先找 Alex Mercer，社区 mod 页面提到过 `alex_fig.p3d`、
      `alex_tod.p3d` 这些文件名，参考 Nexus mod 页面的安装说明）
- [ ] 用 chunk dumper 跑一遍，把所有 chunk ID 记录下来，
      对照 `chunk-id-crosswalk.md` 逐个核实/补完
- [ ] 判断文件是否被 LZR 压缩（检查签名是否为 `P3DZ`/`ZD3P`）；
      如果是，先完成 Phase 2 的解压移植再继续

## Phase 2 — 核心解析器实现（Python）

- [ ] `tools/p3d_parser/lzr_decompress.py`
      —— 移植 `references/netp3dlib/.../LZR_Compression.cs` 的解压逻辑
      （压缩方向优先级低，考古只需要解压）。
      验收标准：找一个已知内容的压缩 `.p3d` 文件，解压后能被
      chunk dumper 正常解析、且 chunk 树结构合理（不报错、大小吻合）
- [ ] `tools/p3d_parser/chunks.py`
      —— 把 `chunk-id-crosswalk.md` 里已确认结构的 chunk 类型
      （Mesh/Skin/PrimitiveGroup/Skeleton2/SkeletonJoint2/Animation/
      各种 List/Channel chunk）逐个实现成 Python dataclass + 
      `from_bytes(data) -> T` 解析函数。
      设计原则：仿照 `NetP3DLib` 的思路，用一个 `ChunkID -> parser函数`
      的注册表（类似 Gibbed 的 `NodeFactory` / `KnownTypeAttribute`），
      未知 chunk 类型不报错，只是原样保留 payload 字节，方便后续排查
- [ ] `tools/p3d_parser/model.py`
      —— 把已解析的 chunk 树组装成一个中间表示（IR）：
      `Mesh { vertices, normals, uvs, indices, bone_weights, bone_indices }`
      `Skeleton { joints: [{name, parent_index, rest_pose}] }`
      `AnimationClip { name, frame_rate, num_frames, bone_tracks: {bone_name: {frame: quaternion}} }`
      这一层要跟 chunk 格式解耦，方便以后接不同的导出器（glTF/FBX/...）

## Phase 3 — glTF 导出

- [ ] `tools/p3d2gltf/export.py`
      —— 用 `pygltflib` 或手写最小 glTF writer，把中间表示（IR）转换成
      带蒙皮 + 动画的 `.glb`。关键坑位：
      - Pure3D 的 Quaternion 是 (W,X,Y,Z)，glTF 要求 (X,Y,Z,W)，转换时注意顺序
      - Pure3D Matrix4x4 是行主序，glTF/three.js 是列主序，做骨骼矩阵转换时
        要转置或者直接用行向量运算，全程保持一致，不要中途混用约定
      - 骨骼层级：Pure3D 用 `Parent`（父关节的索引），glTF 用节点树的父子关系，
        直接映射即可，但注意 Parent=0xFFFFFFFF 或类似哨兵值可能表示"根骨骼无父"，
        需要在真实数据里确认哨兵值具体是多少
      - 顶点的 bone indices 存在 `Matrix_List`（每顶点4个byte，注意读取顺序是
        DCBA 而非 ABCD，参照 NetP3DLib `Matrix` 类的 `Write`/构造函数）
      - 动画关键帧的 `frame` 是"第几帧"（配合 Animation chunk 的
        `FrameRate` 换算成秒），不是直接的时间戳，导出 glTF 动画采样点时
        要做 `time = frame / frame_rate` 的换算
- [ ] CLI 封装：`python -m p3d2gltf input.p3d output.glb`

## Phase 4 — 本地 Web Viewer

- [ ] `viewer/server.py`
      —— 一个极简本地 HTTP server（Flask/http.server 皆可），
      监听 `0.0.0.0:<port>`（sandbox/局域网环境下要绑 0.0.0.0，不要 127.0.0.1，
      否则外部代理转发不进来），提供：
      - 静态文件服务：托管 three.js viewer 前端
      - 一个 `/convert` 接口：上传 `.p3d`（或 `.rcf` 内部路径），
        后端跑 Phase 3 的转换器，返回 `.glb`，前端直接加载展示
- [ ] 前端直接魔改现成的开源 three.js viewer
      （候选：`sboez/3D-Models-Load`，已支持 glTF + 动画播放 + 材质检查，
      不需要从头写），改动点主要是接上 `/convert` 接口的上传/加载流程

## 已知风险 / 不确定项

- Pure3D 版本差异：Prototype 可能对某些 chunk 做了魔改（字段增删），
  不能完全照抄 Hit&Run 的实现，必须用真实数据反复验证，宁可"解析失败
  抛异常"也不要"解析出错误数据却不报错"——每个 chunk parser 都应该
  校验 payload 长度是否和读取的字段总长度吻合，不吻合就报错，方便定位问题。
- LZR 解压算法未经真实压缩样本验证，存在把 C# 位运算细节移植错的风险，
  必须找真实压缩文件做验证，不能只凭读代码"看起来对"就认为完成了。
- Prototype 2（续作）可能用了进一步魔改的引擎版本（甚至换引擎），
  本项目目前只针对第一代《虐杀原形》(2009)。
