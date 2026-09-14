# 2009年3DM工作室工具反编译发现记录

> 来源：3DM工作室 2009 年发布的《虐杀原形》"贴图素材修改器第二版"
> （原始下载页：`down.gamersky.com/pc/200906/20468.shtml`，
> `dl.3dmgame.com/patch/6081.html`）。这是当年汉化组用于解包 `art.rcf`
> 并编辑 `.p3d` 内容的官方（民间授权，非游戏开发商官方）工具包。
>
> 压缩包 `3DM_Prototype_DDS_Trainer.rar` 内含：
> - `PackageManager.exe` — RCF 资源提取替换器（同时处理 `.rz` 压缩）
> - `P3DManipulator.exe` — P3D 包文件导入导出器 + 几种特殊 chunk 的转换器
> - `虐杀原形素材提取器.exe` — 贴图可视化查看/导入导出工具
> - `dds.8bi`、`FreeImage.dll` — DDS 处理相关的第三方 DLL 依赖
>
> 这三个 exe 都是 **VB.NET 编写的 .NET (Mono/.NET Framework) 程序**（非
> 原生 C++/DirectX），代码经过了变量名混淆（类名全是 `A`/`B`/`C`/`a`/`b`
> 这种单字母），但用 `ilspycmd` 完整反编译出了可读的 C# 源码，足以核对
> 关键的二进制格式解析逻辑。这是**独立于 Gibbed.Prototype /
> NetP3DLib 的第二方来源**，能互相交叉验证我们之前的格式假设。

## 已交叉验证 / 新确认的结论

### 1. `.rz` 压缩确实是标准 DEFLATE/zlib，不是 LZR（再次证实）

`PackageManager/B.cs` 里的 `.rz` 解压/压缩逻辑：

```csharp
// 解压 (A(D.c P_0, D.b P_1))：
//   读 8 字节 "RZ" + 6字节padding 头部
//   读一个 u32/u64 长度字段（原码里是 b.A() 返回 long）
//   调用内部 DEFLATE 解码器 (b.D 类) 把剩余字节流解压
if (Operators.CompareString(b.A(8), "RZ", false) != 0)
    throw new InvalidDataException();
long num = b.A();  // 记录的原始/未压缩长度
A(b, P_1.A());     // 实际解压过程

// 压缩 (a(D.c P_0, D.b P_1))：
b2.A("RZ", 8);      // 写 8 字节 "RZ" 头
b2.A(b.c());        // 写原始长度
b2.g(A(array));     // 写 DEFLATE 压缩后的数据
```

`D/c.cs` 里内嵌了一份**完整的、手写的 DEFLATE 编解码实现**（`class C`），
里面的静态表：

```csharp
global::D.C.A = new short[576] { 12, 8, 140, 8, 76, 8, ... };  // 固定长度 Huffman 码表
global::D.C.a = new short[60]  { 0, 5, 16, 5, 8, 5, ... };      // 距离码表
new C(..., 257, 286, 15);  // literal/length alphabet: 286 symbols, 最大15位编码
new C(..., 0, 30, 15);     // distance alphabet: 30 symbols
new C(..., 0, 19, 7);      // code-length alphabet: 19 symbols, 最大7位编码
```

这些数字（286个literal/length符号、30个distance符号、19个code-length
符号、最大15位/7位编码）**正是 RFC 1951 (DEFLATE) 标准里定义的确切参数**。
这与我们从 `Gibbed.Prototype.Unpack/Program.cs` 读到的结论完全一致：
**Prototype 的 `.rz` 就是标准 zlib/deflate，跟 Pure3D 引擎自己的 LZR
算法是两套完全不同的东西**。这是双重独立来源印证，可以视为已验证结论，
不再是"假设"。

唯一的格式细节差异：这份 2009 年工具读的头部是 `"RZ"` + **8字节**
（我们之前从 Gibbed 源码读到的是 `magic "RZ\0\0"(4字节) + unknown1(4)
+ uncompressed_size(4) + unknown2(4)`，共 16 字节头，其中 magic 部分
只有 4 字节 "RZ\0\0"）。需要注意 `b.A(8)` 在这份反编译代码里读的是**8个
字符**然后与字符串 `"RZ"` 比较——这大概率是遗留 VB6/VB.NET 习惯写法
（用定长字段读入再 trim/compare），不代表头部真的是 8 字节的 "RZ"
文本，具体还需要用真实 `.rz` 文件核对我们 `decompress_rz_payload()`
里的字段布局是否完全对齐。**这是下一步优先要用真实文件验证的点。**

### 2. RCF 格式核对：字段布局与我们的 `rcf_extract.py` 基本一致，但发现一处关键差异

`PackageManager/A.cs` 里的 `class a`（继承自 `C.B`，是 RCF 归档的核心
解析类）：

```csharp
internal new const string A = "ATG CORE CEMENT LIBRARY";
...
if (Operators.CompareString(b.A(32), "ATG CORE CEMENT LIBRARY", false) != 0)
    throw new InvalidDataException();
int num = b.A();       // 应为 major/minor/endian/unknown1 组成的4字节，或紧接着的一个字段
this.m_A = b.A();       // index_offset
int num2 = b.A();      // index_size (推测)
int num3 = b.A();      // metadata_offset (推测)
int num4 = b.A();      // metadata_size (推测)
int num5 = b.A();      // unknown2 (推测)
int num6 = b.A();      // entry_count
```

**关键差异点**：这里 `b.A(32)` 读了 **32 字节**做 magic 字符串比较，
而我们从 Gibbed.Prototype 源码（`CementFile.cs`）读到、并已经写进
`rcf_extract.py` 的实现是 **24 字节** magic + 8 字节独立 padding
字段。需要确认：
  - 究竟是 magic 字段本身就是 32 字节（`"ATG CORE CEMENT LIBRARY"`
    23个字符，补 NUL 到 32 字节），
  - 还是这份反编译代码把"24字节magic + 8字节padding"这两个字段合并
    一次性读取比较（`b.A(32)` 只是读 32 字节转字符串，`Operators.
    CompareString` 用值比较时，只要前 23 字节匹配、后面全是 NUL
    padding，字符串比较可能依然成立，取决于 VB `CompareString` 对
    末尾NUL的处理方式）。

  **这是目前 `rcf_extract.py` 里最需要用真实 `.rcf` 文件核实的头部
  字段边界问题**，直接影响 `index_offset` 等后续字段的偏移量是否算对。
  在拿到真实 `art.rcf` 后，第一件事应该是打印出文件开头 64 字节的
  hex dump，逐字节核对魔数/padding边界。

  Entry 表和 Metadata 表的解析逻辑与我们的实现在结构上一致：
  - Entry: `(offset, size, ??)` 三个 u32（对应我们的 NameHash/Offset/Size，
    但这份代码里似乎是按 `array[i]=num8(entry index?)`,
    `array2[i]=num9(offset)`, `array3[i]=num10(size)` 处理，需要仔细
    核对哪个字段对应 NameHash）
  - Metadata: 每条目 `(num17, num18, num19, num20=name_length, name)`，
    其中 `num18` 有一个特殊校验 `if (num18 != this.m_a) throw` ——
    `this.m_a` 是文件头里读出的一个固定值（读取顺序上是 metadata表
    起始处的一个"公共字段"），这与我们 `rcf_extract.py` 里
    `names_alignment=2048` 的角色一致，只是这里的代码把它当作
    每条 metadata 都必须相同的"校验值"而不是只读一次的表头。
  - 每条 metadata 后面还有 `b.a(); b.a(); b.a();`（读 3 个字节但丢弃），
    对应我们实现里的 `Unknown3`（3字节，固定 000000）。

  **总体结论**：核心结构（Entry表 + Metadata表 + 文件名哈希/对齐机制）
  与我们已实现的版本高度吻合，说明大方向是对的；但头部 24 vs 32 字节
  的边界问题需要用真实文件验证后再决定是否要修正
  `rcf_extract.py`。

### 3. P3D 内部结构确认：就是我们已知的 chunk 树（尺寸+类型的递归格式）

`P3DManipulator` 里的 `A/B.cs`：

```csharp
public class B  // 代表一个 P3D chunk 节点
{
    internal int A;         // chunk type ID (int, 4 bytes)
    internal byte[] A;      // chunk 自身的 payload bytes（不含子chunk）
    internal List<B> A;     // 子 chunk 列表（递归）

    public B(C.A P_0)
    {
        ...
        long num = b2.D();         // 当前位置
        this.A = b2.A();           // 读 chunk type ID
        int num2 = b2.A();         // 读 chunk 总长度（含头部）
        int num3 = b2.A();         // 读 payload 长度（不含子chunk）
        int num4 = num2 - 12;      // 减去头部固定 12 字节，得到 "内容+子chunk" 总长
        this.A = b2.A(num4);       // 读取 payload bytes（这里似乎把payload和子chunk数据一起读了？需要核对）
        long num5 = num + num3;
        while (b2.D() < num5)       // 在 [文件起点..num+num3) 范围内循环读子 chunk
            this.A.Add(new B(C.A.A(b2)));
    }
}
```

这与我们 `tools/p3d_parser/inspect_p3d.py`（已在 4 个真实样本文件上
跑通）里实现的 chunk 树遍历逻辑**完全一致**：每个 chunk 头是
`type_id(u32) + total_size(u32) + payload_size(u32)` 共 12 字节，
之后是 payload，再往后（在 `total_size` 范围内）是子 chunk 列表，
递归解析直到 `payload_size` 边界。这是双重独立来源印证 Pure3D chunk
树格式的正确性，进一步坐实了我们已有的 `inspect_p3d.py` 实现。

顶层文件格式：`P3D` 3字节 ASCII 头 + 根 chunk：

```csharp
string text = b2.A(3);
if (text != "P3D") throw new InvalidDataException();
b2.d(0L);
this.A = new B(C.A.A(b2));  // 从文件开头（含"P3D"三字节）重新解析成根chunk
```

有意思的是它先读3字节校验，然后 seek 回 0 再整体当 chunk 解析——说明
"P3D" 这3个字节实际上是根 chunk 的 type ID 的前3个字节（一个 ASCII 编码
的 magic type id），而不是一个独立于 chunk 结构之外的文件头。这个细节
值得在我们的 `inspect_p3d.py` 里也确认一下根 chunk 的 type ID 具体数值。

### 4. 新发现：两个特殊 chunk / 数据格式的转换关系（此前完全未知）

来自 `A/C.cs`（`P3DManipulator.exe` 的命令行入口逻辑）：

| 触发条件（文件名匹配） | 转换目标 | 说明 |
|---|---|---|
| `0-00019002.bin` | → `.dds`（去掉开头4字节后就是标准DDS） | chunk type `0x00019002` 大概率是"贴图/纹理数据" chunk，payload去掉4字节小头部就是完整DDS贴图文件 |
| `0-00019002.dds` | → `.bin`（在DDS前面加回4字节长度头） | 上一条的逆操作，写回时头部4字节 = 写入前的4字节长度值（`b11.A((int)b10.c())`，即 DDS 文件自身的字节长度） |
| `*-00018202.bin` + `*-00018202\0-00018201.bin`（同目录配对） | → `.idx` + `.txt`（"带索引Agemo文本"） | `0x00018202` 是一个字符串索引表（char/offset/length 三元组数组，参考 `A/A.cs` 里的 `class a`），`0x00018201` 是被索引指向的实际字符数据块。两者配对使用，是本地化/多字节文本相关的 chunk（"Agemo"疑似内部代号，可能与东亚多字节字符编码转换有关，因为代码里用了 UTF-16/UTF-16BE/GB18030 等编码） |

这两组 chunk type ID（`0x00019002` 贴图、`0x00018201`/`0x00018202`
文本索引对）**在我们目前的 `docs/chunk-id-crosswalk.md` 里没有记录**，
是这次反编译带来的全新信息，需要补充进 chunk ID 对照表，后续遇到真实
`.p3d` 文件时可以按图索骥去验证。

### 5. 未直接找到的信息

- 没有在这几个反编译出的 exe 里找到明确的"文件名哈希函数"实现
  （对应我们 `hash_file_name()` 那套 DJB2 变体算法）。这个工具似乎
  是靠"Entry 表和 Metadata 表按记录顺序一一对应"来关联文件名，而不是
  通过重新计算哈希来查找——这跟 Gibbed.Prototype 的实现思路不同，
  也是个需要拿真实文件验证两种假设谁对的点（顺序对应 vs 哈希查找）。
- `虐杀原形素材提取器.exe`（贴图查看器）还没有反编译/详细阅读，
  可能会有更多贴图格式相关的细节，后续有需要可以再挖。

## 后续行动项

1. **拿到真实 `art.rcf` 后第一件事**：hex dump 文件开头 64-128 字节，
   逐字节核对：
   - magic 究竟是 24 字节还是 32 字节（决定后续所有 offset 计算）
   - Entry 表三个 u32 字段的真实含义和顺序（NameHash在前还是Offset在前）
   - metadata 表里 `this.m_a`（疑似 names_alignment=2048）的真实数值
2. 把 `0x00019002`（贴图chunk）、`0x00018201`/`0x00018202`（文本索引对）
   补充进 `docs/chunk-id-crosswalk.md`。
3. 如果找到真实的 `.p3d` 角色模型文件，可以用这份反编译出的
   `A/B.cs` chunk 遍历逻辑（本质上和我们的 `inspect_p3d.py` 一样）
   交叉验证解析是否正确，尤其关注根 chunk 的 type ID 数值。
4. 反编译产物已保存在沙盒 `~/uploads/decompiled/`，因体积和授权原因
   **不适合提交进公开仓库**（这是第三方商业工具的反编译产物，且
   `NOTICE.md` 明确本项目不分发游戏资源/第三方工具本体）——仅作为
   本地分析笔记的依据，结论摘要记录在本文档即可，不上传原始反编译代码。
