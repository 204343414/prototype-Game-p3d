# 用户提供 P3DAddon 工具包的静态审计（不执行旧工具）

## 范围与安全边界

为调查旧教程/Blender 插件是否含有 Prototype `TRAN`（特别是 `int16` 外部 blob）解码线索，对用户提供的 P3DAddon 工具包做了**只读静态审计**：

- 解压 Python 源文本并保留 archive 内的原始路径；
- 对托管 DLL 做 PE/ECMA-335 元数据和 IL 字节码解析；
- 读取 custom attribute、TypeDef、字段、方法名及序列化方法的静态 IL；
- 搜索目标 chunk ID 的 little-endian 原始字节。

没有安装、加载或执行 Blender 插件、Python.NET、任何 DLL 或旧版可执行文件；也没有提交工具包、DLL、游戏文件或游戏派生 JSON 到本仓库。

## 1. Python 插件层：网格/骨架，不是动画导入器

路径保留后，工具包内有10个 Python 成员：`Common/` 下3个，顶层面板/注册文件3个，以及 `Prototype1/`、`Prototype2/` 各自独立的 `ImportP3D.py` / `ExportP3D.py`。

此前按 basename 的便捷提取会覆盖同名 P1/P2 文件；本次路径保留检查避免了这个错误。两条分支的实际职责为：

- **P1 import**：调用 `Nixson.Prototype1.ImportP3D` 读取 polyskin/geometry/primitive group/skeleton，创建 Blender mesh、UV、normal、权重和 armature；
- **P1 export**：从选中的 mesh + armature 形成 primitive group 或 geometry；
- **P2 import/export**：同样围绕 drawable、顶点、面、UV、normal、权重、group 和 skeleton；
- 顶层 operator 只在 P1/P2 mesh/character/geometry 导入导出间分派。

这10个源文件没有动画、keyframe、channel、`TRAN`、`ROT`、ZLIB 或目标 `0x001211xx` / `0x00121401` 处理逻辑。因此，插件层不能作为 Alex 外部压缩动画流的实现来源。

## 2. Nixson managed wrappers：同样没有外部 animation codec

| DLL | SHA-256 | 静态结论 |
|---|---|---|
| `Nixson.Prototype1.dll` | `976375f2847ec6866d75cd6d5519babbf9a3b5012df1ca64b3a897caf4361c84` | TypeDef/MethodDef 为 P1 mesh、primitive、顶点/index/UV/normal/weight/group 和 skeleton 操作；`Importing.Animation` 为空壳，无 channel/blob decoder。 |
| `Nixson.Prototype2.dll` | `ee2135c48d2551aca3db045e1efee1fbaaec6d9aad4f74e38d5768a265fe2af0` | P2 mesh/drawable/primitive skin、vertex、index 等导入导出；无 animation-specific TypeDef。 |

两个 wrapper 都引用 `Gibbed.Prototype.FileFormats` 与 `Gibbed.IO`，但没有额外 animation codec 依赖；对`0x00121110`、`0x00121112`、`0x00121114`、`0x00121119`、`0x00121120`、`0x00121401`的原始 little-endian 字节扫描均为缺失。这与 Nixson 发布说明中“animation import/export 仍在 future work”的描述一致。

## 3. 新版 Gibbed DLL 带来的有效线索

工具包捆绑的 `Gibbed.Prototype.FileFormats.dll`（SHA-256 `6e2599a3beeaacb9d1347e03b2859eb02aecebc1240ea62cfc84fa126db2d63d`）比项目引用的较旧 Gibbed 源码多出 animation-adjacent 类型：

- `AnimationChannelHeader`：4个 `u32` 属性（`Type`、3个 unknown）；静态 IL 确认顺序读/写4个 `ReadValueU32` / `WriteValueU32`。
- `SomethingAnimationData`：先 serialize/deserialize 上述 header，再读/写 `Type` 字符串，最后读取固定16字节 `Data`。
- `AnimationBone`：`Pad(u32)`、U8-length string `Name`、`BoneId(u32)`、`AmountOfMatrices(u32)`。
- `AnimationLimbReference`：`Limb` 字符串，随后固定24字节 `Unknown`。

最关键的是 custom attribute 的所有权，而不只是 DLL 内偶然出现的数字：

```text
Gibbed.Prototype.FileFormats.Pure3D.AnimationLimbReference
    <- Gibbed.Prototype.FileFormats.Pure3D.KnownTypeAttribute..ctor
       custom-attribute blob: 0100011412000000
```

该 blob 的构造参数为 little-endian `0x00121401`，故可以把该 chunk ID 精确归属为 `AnimationLimbReference`。对应静态 IL 显示：

```text
Deserialize: Limb = ReadStringAlignedU8(); Unknown = ReadBytes(24)
Serialize:   WriteStringAlignedU8(Limb); WriteBytes(Unknown)
```

这证明其**外层记录布局**，但 DLL 对该24字节仅作原样读写，并未提供字段语义、`int16` 解压公式或任何 TRAN scale/reference-frame 结论。

## 4. 对当前动画工作的影响

`0x00121401` 因而不再是无名的“邻近 chunk”：它是具名 limb reference。私有 Alex 样本的完整只读枚举进一步确认，所有533个 `PTRN` 各带4个记录，顺序固定为 `leg_left`、`leg_right`、`arm_left`、`arm_right`，每个都是 name + 24 bytes。这些 metadata 已被项目 decoder 无损保留，但其尾部数值没有被猜测性地当作 translation scale。

结论是：旧工具/教程确实提供了一个此前缺失的可靠结构线索，足以改进 parser 与后续交叉样本标定；它**没有**直接实现 Alex 的 `0x00121119` 外部 ZLIB `TRAN` 解码或给出其最终 glTF 平移换算。下一步仍需以 bind pose、真实动画轨道和跨样本一致性来验证任何 scale/offset 假设。
