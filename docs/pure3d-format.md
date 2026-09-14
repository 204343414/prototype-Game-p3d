# Pure3D 文件格式笔记

来源：交叉验证自
- [donutteam.com Pure3D Files 文档](https://docs.donutteam.com/docs/Pure3DFiles/DataStructure)（Hit & Run 社区）
- `references/gibbed-prototype/Gibbed.Prototype.FileFormats/Pure3DFile.cs`（Prototype 专用实现）
- `references/netp3dlib/.../P3D/P3DFile.cs`（Hit & Run 持续维护实现）

三份独立来源的文件头 / chunk 结构描述**完全一致**，可以认为这部分是可靠的。

## 文件头 (12 字节)

| 字段 | 类型 | 说明 |
|---|---|---|
| Signature | uint32 | 见下表 |
| HeaderSize | uint32 | 恒为 12 |
| Size | uint32 | 整个文件大小（含头） |

### 签名表

| Hex (小端读出) | 含义 |
|---|---|
| `0xFF443350` (`P3D\xFF`) | 小端，未压缩 |
| `0x503344FF` (`\xFFD3P`) | 大端，未压缩 |
| `0x5A443350` (`P3DZ`) | 小端，**LZR压缩** |
| `0x503344E1`... 见代码 `0x5033445A` (`ZD3P`) | 大端，压缩 |

判断方式：读前 4 字节为 uint32（按小端），
- 等于 `0xFF443350` → 小端未压缩
- 等于其 byte-swap → 大端未压缩（数据其余部分也要按大端读）
- 压缩标志同理，额外要先做 LZR 解压才能拿到真正的 chunk 树

## Chunk 结构（递归，无限层级）

每个 chunk 的头部固定 12 字节：

| 字段 | 类型 | 说明 |
|---|---|---|
| Type | uint32 | chunk 类型 ID（见 `chunk-id-crosswalk.md`） |
| ChunkSize (`HeaderSize`) | uint32 | 本 chunk 头 + payload 的字节数（不含子 chunk） |
| TotalSize | uint32 | 本 chunk 头 + payload + 所有子孙 chunk 的总字节数 |
| Data | byte[ChunkSize-12] | payload，具体结构取决于 Type |
| (children...) | — | 从 `offset + ChunkSize` 开始，直到 `offset + TotalSize` |

已验证：`tools/p3d_parser/inspect_p3d.py` 用这个逻辑正确 dump 出了
`dummy.p3d`（含 PNG 贴图、字体资源）和多个 `.p3d`/`.fig` 样本的完整 chunk 树。

## 基础数据类型（用于 chunk payload 内部字段）

| 类型 | 字节数 | 结构 |
|---|---|---|
| Vector2 | 8 | float X, Y |
| Vector3 | 12 | float X, Y, Z |
| Quaternion | 16 | float W, X, Y, Z （注意 W 在前！与很多引擎习惯的 X,Y,Z,W 顺序不同，写转换代码时容易踩坑）|
| Matrix4x4 | 64 | 16 个 float，行主序 M11..M44 |
| Colour | 4 | uint8 R,G,B,A |
| P3DString | 可变 | 1 字节长度前缀 + ASCII 字符串内容（无 NUL 终止符）；见 `NetP3DLib` 的 `P3DString`/`ReadP3DString`，具体对齐规则待补充验证 |
| FourCC | 4 | 4 个 ASCII 字符，用作动画通道的"骨骼名/参数名"标识 |

## LZR 压缩

Radical Entertainment 自研的类 LZ77 压缩算法，donutteam 文档长期标注为
"TODO"未写出算法，但 **`references/netp3dlib/.../P3D/LZR_Compression.cs`
里有完整的可读 C# 实现**（含压缩与解压两个方向）。核心解压逻辑：

- 压缩文件按 4096 字节（`LZR_BLOCK_SIZE`）分块，每块前有
  `compressedLength` (uint32) + `blockSize` (uint32，即解压后大小)。
- 块内是逐 token 解析：
  - 控制字节 `code`：
    - `code > 15`：这是一个"匹配"(match/backreference) token。
      - `matchLength = code & 0xF`，如果是 0 则要继续读变长长度字节
        （0x00 字节表示 +255，直到读到非 0 字节为止，参见代码里的
        `while (tmp == 0) { matchLength += 255; ...}` 模式，是一种常见的
        变长游程编码技巧）。
      - 再读 1 字节 `tmpByte`，`matchOffset = (code >> 4) | (tmpByte << 4)`，
        即 offset 由 code 高 4 位 + tmpByte 组成的 12 位数字。
      - 从 `output[written - matchOffset]` 开始复制 `matchLength` 字节
        到输出（典型的 LZ 滑动窗口复制，注意要允许 overlap 复制，即
        一边写一边读刚写的数据，off-by-one 需要照抄 C# 实现的顺序）。
    - `code <= 15`：这是一个"字面量游程"(literal run) token。
      - `runLength = code`，如果是 0 也用同样的变长扩展规则；
        且这种情况下**固定先复制 15 字节字面量**（代码里的
        `br.Read(output, ..., 15); written += 15;`，这是个容易漏掉的细节），
        然后再追加 `runLength` 字节字面量。
      - 否则直接复制 `runLength` 字节字面量到输出。
- 移植到 Python 时建议**逐行照抄** C# 版本的控制流，不要"优化"或"简化"，
  因为这类位打包格式的边界条件（尤其是 0 长度的变长扩展、以及
  runLength==0 时固定多读 15 字节这种特例）非常容易出 off-by-one 错误。

**尚未验证**：手头没有真正压缩过的 Prototype `.p3d`/`.rcf` 样本，
所以这段移植代码写出来后必须找一个已知内容的压缩文件做 round-trip 测试
（压缩后再解压，比对是否和原文件字节一致），或者至少用游戏里带压缩
签名的真实文件测试解压结果是否能被下游 chunk parser 正常解析。

## RCF 归档格式（待补充）

`.rcf` 文件是 Prototype/Scarface 的容器格式（"Cement"），存放许多
`.p3d`/其他资源文件。参考实现：
- `references/gibbed-prototype/Gibbed.Prototype.FileFormats/CementFile.cs`
  + `Cement/Entry.cs` + `Cement/Metadata.cs`
- 社区独立实现：[ermaccer/SCFExtract](https://github.com/ermaccer/SCFExtract)、
  [NixsonLai/RcfTools](https://github.com/NixsonLai/RcfTools)、
  [gert7/cement-extractor](https://github.com/gert7/cement-extractor)

尚未深入阅读这几份代码整理笔记，是下一步的 TODO（见 `roadmap.md`）。
