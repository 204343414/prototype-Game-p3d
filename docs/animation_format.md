# Prototype `Animation` (0x00121000) chunk数据组织范式

> 本文档首先记录已完整验证的 **Prototype外部 ZLIB channel family**，但不再把它误称为游戏中唯一的 Animation 布局。`alex.p3d` 的 `PTRN` 动画实际混用两类数据：① 本文重点的自定义压缩通道（`0x00121112` / `0x00121114` / `0x00121119`），其 keyframes 位于独立的`0x02F00000` ZLIB blob，由`0x00121120`给出定位信息；② 标准式内联通道（已观察到`0x00121101`–`0x00121104`）以及尚未解码的内联扩展`0x00121118`。因此，不能把`netp3dlib`的直读字段方式套用到第一类，但可将其作为第二类的格式线索。

## 1. 顶层发现

在`alex.p3d.rz`里（无需单独的动画文件），直接搜索`type_filter=0x00121000`可以枚举出**634个**具名动画chunk，例如`alex_act_death`、`alex_act_block`、`alex_act_brain_hurts_001`、`alex_act_answers_001_b`（+对应的`_cam1`摄像机动画）等。命名规则为`alex_act_<动作名>[_变体][_camN]`。

## 2. Chunk树结构（以真实样本`alex_act_block`验证，global_index=979起）

```
Animation (0x00121000)                         version,name,AnimationType(FourCC "PTRN"/"CAM "),NumFrames(float),FrameRate(float),Cyclic(u32)
├─ Animation_Header (0x00121006)                version(u32), NumGroups(u32) — 与Animation_Group_List的NumGroups一致
│  ├─ 0x00121008 (Channel类型直方图容器)          version(u32), NumEntries(u32)
│  │  ├─ 0x00121009 ×NumEntries                 version(u32), ChannelTypeID(u32,FourCC/chunk ID), Count(u32)
│  │  │    例: (0x121112, 26) (0x121114, 26) (0x121119, 8) — 用于引擎预分配内存，纯统计摘要
│  └─ 0x00121400                                 version(u32), 未知u32(=NumGroups? 观察值等于NumGroups=0x35=53)
├─ 0x02F00000  ★关键★ ZLIB压缩blob                version(u32,=0)+magic"ZLIB"(4字节)+decompressed_size(u32)+compressed_size(u32)+zlib_compressed_data[compressed_size]
│                                                 解压后得到一大块连续二进制，是全部Animation_Group的frames+values数据，按group在Animation_Group_List里出现的顺序首尾相接排列（无需额外分隔符，靠每个channel meta chunk里的count+offset字段定位）
├─ Animation_Group_List (0x00121002)             version(u32), NumGroups(u32，冗余，可从子节点数计算)
│  └─ Animation_Group (0x00121001) ×NumGroups     version(u32) + name(p3d string，通常=骨骼名，如"Pelvis"/"Hip_L"/"Spine_1") + GroupID(u32，全局关节索引，非从0连续，可能跳号) + NumChannels(u32)
│     └─ 1~2个channel节点，常见两种FourCC参数：
         "TRAN" → 位移通道，具体chunk类型为 0x00121119（自定义压缩vector3，非netp3dlib的Vector_3D_OF_Channel=0x121104）
         "ROT"  → 旋转通道，具体chunk类型为以下之一：
                   0x00121112 = 26次出现，主关节，值块=int16×3/值（6 bytes/value）
                   0x00121114 = 26次出现，末梢小关节（手指/下颚/脚尖等），值块=int8×3/值（3 bytes/value）
                   0x00121119 = 也用于极少数"TRAN"角色（Character_Root/Spine_1等），说明0x00121119不是固定"仅TRAN"专用，是一种通用压缩vector3类型，被同时用于位移和某些特殊旋转场合（需具体验证特例）
         每个channel节点下还有2个子chunk：
           Channel_Interpolation_Mode (0x00121110)   version(u32) + InterpMode(i32，观察值恒为-1=0xFFFFFFFF，插值模式未变化过，可能代表"默认/线性")
           0x00121120（数据定位符）                    version(u32) + Count(u32，帧数) + Offset(u32，在ZLIB解压后blob里的字节offset)
├─ AnimationLimbReference (0x00121401) ×4         仅PTRN骨骼动画具备；下节说明其已确认的外层布局
└─ 0x00121402                                      在PTRN样本中为`version=0, unknown=0`的8字节payload；语义未定
```

### 2.1 `AnimationLimbReference`（`0x00121401`）的新增静态证据

来自用户提供的旧版 P3DAddon 所带**较新** `Gibbed.Prototype.FileFormats.dll` 的离线元数据/IL审计（未加载或执行 DLL）：`[KnownType(0x00121401)]` 精确归属到 `AnimationLimbReference`。其 `Deserialize` 仅执行两步：`ReadStringAlignedU8()` 读取 `Limb`，随后 `ReadBytes(24)` 读取名为 `Unknown` 的24字节；`Serialize` 对称地原样写回两者。因此目前能可靠声称的是：这是具名 limb reference，且尾部是**固定24个不透明字节**；该 DLL 本身没有解释这24字节的含义或给出 TRAN 换算公式。

在私有 `alex.p3d` 的完整枚举中：634个 Animation 分为533个`PTRN`、67个`CAM`、34个`EXP`；**仅全部533个 `PTRN`**各有4个该节点（总计2132），固定顺序和名称均为 `leg_left`、`leg_right`、`arm_left`、`arm_right`。2132个记录的尾部长度均为24，开头8字节都为 little-endian `u32(0), u32(2)`。以 `alex_act_block` 为例，四个完整尾部若临时按 `u32,u32,float32×4` 显示为：

| Limb | 固定前缀 | 剩余16字节的浮点视图（**仅供比对，非已证实语义**） |
|---|---:|---:|
| `leg_left` | `(0, 2)` | `(0.0, 26.0, 26.0, 26.0)` |
| `leg_right` | `(0, 2)` | `(26.0, 26.0, 0.0, 26.0)` |
| `arm_left` | `(0, 2)` | `(-1.0, 25.0, 24.0, 27.0)` |
| `arm_right` | `(0, 2)` | `(-1.0, 32.0, 31.0, 20.0)` |

这四个记录与该动画中四个双通道 group 同名、共现，因而是 TRAN 标定的**有价值线索**；但目前仍不能把24字节中的数值擅自认定为平移 scale、offset 或 bind pose。解码器只无损输出 limb 名、chunk offset 和24字节 hex，等待能跨样本验证的公式。

## 3. ZLIB blob内部布局（对每个channel，按`0x00121120`给出的`(count, offset)`去blob里读取）

```
blob[offset : offset + align4(count*2)]                 = frames: uint16[count]     （关键帧所在的帧号，非连续，如(0,1,2,3,5,8,11,...)）
blob[offset+align4(count*2) : ... + count*V]            = values: 見下表，V=每个值的字节数，紧跟在frames后面，起始位置4字节对齐
```
其中`align4(x) = (x+3) & ~3`（向上取整到4字节边界）。**相邻 channel 的下一个
`offset` 使用 `align4(count*V)` 跳过当前 values 尾部的对齐字节；但整个 blob
最后一个 channel 的 values 尾部可以省略这段无意义 padding。**真实
`alex_act_block` 最后一个 `arm_right/ROT` 为 `count=53, V=6`：实际数据终点是
`10352 + align4(53*2) + 53*6 = 10778`，恰为 blob 长度；若错误地对末尾也强行
`align4(53*6)=320`，会读到不存在的 2 字节（10780）。解析器应对所有非末尾
channel 严格要求对齐后的下一个 offset，对最终 channel 则以未填充的实际 value
字节终点为准。

### 已确认的3种channel值编码：

| Channel类型ID | 字节数/值(V) | 用途 | 解码公式 |
|---|---|---|---|
| `0x00121112` | 6 (int16×3) | 主要骨骼ROT（压缩四元数，去掉W分量） | `x,y,z = int16/32767`；`w = sqrt(1 - x²-y²-z²)`（等价于netp3dlib的`Compressed_Quaternion_Channel_2`算法，但ID不同——netp3dlib里`0x121112`叫`Compressed_Quaternion_Channel_2`，**恰好完全吻合**，说明这一类型netp3dlib的定义可直接照搬） |
| `0x00121114` | 3 (int8×3) | 末梢小关节ROT（更粗精度的压缩四元数） | `x,y,z = int8/127`；`w = sqrt(1 - x²-y²-z²)`（同上算法，字节宽度减半，netp3dlib未收录此变体，是Prototype自己的扩展类型） |
| `0x00121119` | 6 (int16×3) | 位移TRAN（也偶见于个别ROT，需具体核实） | `x,y,z = int16 / SCALE`（SCALE未标定，观察到原始int16范围可达±26000量级，暂以`/32767`或自定义bind-pose-relative scale处理，导出验证时需要用实际bindpose尺度校准；不是单位四元数，无需sqrt处理） |

## 4. 已用真实数据交叉验证的一致性检查

- `Animation_Header.NumGroups`(53) = `Animation_Group_List`实际子节点数(53) ✓
- `0x00121008`直方图统计的3种类型计数（26/26/8）= 逐个Animation_Group实际枚举出的channel类型计数总和（26/26/8）✓
- 相邻 channel 的`(count,offset)`可精确预测下一个 channel 的 offset：`next_offset = offset + align4(count*2) + align4(count*V)`，对全部59个“有后继”的 channel 零误差 ✓；最后一个 channel 允许省略 values 的无意义尾部 padding（见第3节） ✓
- 骨骼名（`Pelvis`/`Hip_L`/`Knee_L`/`Ankle_L`/`Ball_L`/`Spine_1`/`Spine_2`/`Spine_3`/`Clavicle_L`/`Shoulder_L`/`Elbow_L`/`Wrist_L`/`Index_Base_L`...等）与`alex_reg_body_skeleton`里已解析的67关节骨架的关节名完全对应 ✓

## 5. 与 netp3dlib 标准定义的关系与差异

netp3dlib（面向标准内联式 Animation 格式）中，`Animation_Group` 的标准 channel 会把 frames + values 直接置于自身 payload；这与 Prototype 中**外部 ZLIB channel family**不同：后者的 channel payload 是类型/参数元数据，`0x00121120`给出`(count, offset)`，关键帧实际在 ZLIB blob。解析该 family 必须：
1. 先定位并解压`0x02F00000` blob。
2. 遍历`Animation_Group_List`下每个`Animation_Group`，读取外部-family channel 的类型ID + `0x00121120`给出的`(count,offset)`。
3. 用上表公式在 blob 内按 offset 切片，不能尝试从这种 channel 自身 payload 中读取 frames/values。

这不是对所有 Prototype channel 的一概而论：对同一份私有 `alex.p3d` 的只读清点，已有标准内联样本：`0x00121101`（33个 group，`LCPH` 参数）、`0x00121102`（228个 group，`TRAN`）、`0x00121103`（14个 group，`TRAN`）、`0x00121104`（35个 group，`TRAN`）；它们都只有`0x00121110`插值子节点、没有`0x00121120` locator，并在自身 payload 携带帧和值。`0x00121118`亦有247个内联样本（主要`TRAN`），其完整编码尚待验证。当前 decoder 只接受上文已验证的外部 family，遇到这些内联类型会有意报出 unsupported，而不会静默误解数据。

## 6. 尚待验证/待办

- 为标准内联 `0x00121101`–`0x00121104` 添加各自经夹具验证的解码分支；为`0x00121118`确定类型、帧和值编码。完成前，不能声称“所有角色动画”均可导出。
- `0x00121119`用作TRAN时的具体缩放系数（bind pose绝对坐标 vs 相对父骨骼偏移，需要用实际骨骼局部矩阵对照校准）尚未标定，应用动画到glTF前必须先解决。
- `CAM`(摄像机)类型的Animation结构未单独验证（推测与骨骼动画共用同一套group/channel机制，只是Animation_Group对应摄像机的位置/朝向/FOV等参数而非骨骼，需要时再抓样本验证）。
- 插值模式恒为`-1`，暂视为线性插值处理（Cyclic标记决定动画是否循环，可直接从Animation chunk头部读取）。

## 7. 已落地的解码器（仍未接入 glTF）

`tools/p3d_animation/decode_animation.py` 已将本文件第1–5节的格式范式实现为
可独立调用的 decoder。它可以读取私有本地 `.p3d`，或通过用户自己运行的 viewer
`/api/rcf_entry?raw=1` 在**进程内存**中取得一个条目，然后输出私有的 JSON 诊断数据。
工具严格校验 P3D/chunk 边界、ZLIB header 与解压长度、group/channel 数、frames 单调性、
blob offset/对齐连续性和最终无 padding 尾部规则；同时会无损记录直系
`AnimationLimbReference (0x00121401)` 的 limb 名和24字节 opaque 数据。`test_decode_animation.py`
使用合成 P3D 夹具覆盖 int16 ROT、TRAN、ZLIB blob、limb reference 和末尾2-byte
padding省略场景。标准内联 channel 的解码是独立待办，当前不应把它们伪装成外部 blob。

以真实 `alex_act_block` 的私有数据运行已成功解出 **53 groups / 60 channels**。TRAN
在输出中同时保留原始 `int16×3` 值以及仅用于检查范围的 `/32767` 归一化值；该归一化
值**不是**最终 glTF translation，不能把它当成第6节仍待标定的缩放/参考系结论。

下一步是把 decoder 结果同 Alex bind pose 对照，给 TRAN 建立有证据、可回归的标定，
然后才增加 glTF animation channels/samplers。生成自真实游戏条目的 `.p3d` 和 JSON
不可提交进 Git。

## 8. 窄范围 glTF ROT-only 实验导出器（已落地，但不是完整动画支持）

`tools/p3d_export/export_rotation_animation_experimental.py` 将由
`decode_animation.py` 生成的本地诊断 JSON 追加到一个本地静态 GLB：

```bash
python3 tools/p3d_export/export_rotation_animation_experimental.py \
  --static-glb path/to/static.glb \
  --animation-json path/to/decoded-ptrn.json \
  --out path/to/animated-rot-only.glb
```

它的刻意边界如下：

- 仅接受 `PTRN`，且仅写入已验证的 `ROT` 为 glTF `rotation` 通道；解出的
  `[x,y,z,w]` 四元数按原值写入（无反转、无 bind 乘法）。
- 按 group/bone 名匹配静态 GLB node；没有相应 node 的 limb ROT 会明确列入
  `skipped_non_node_rotations`。
- 静态 Alex 导出器的骨骼 node 是矩阵形式，而 glTF 禁止动画目标同时使用
  `matrix`。该工具会先把每个无 shear 的仿射 local matrix 无损分解为 TRS；
  遇到退化或 shear 矩阵会失败，不会猜测近似结果。
- 所有 `TRAN`，包括 `Character_Root`、`Spine_1`、wrist grapple 以及四个
  limb reference group，都会明确跳过并列入 `skipped_translation_groups`；
  因此输出不含根运动，也不能视为动作的完整还原。
- 仍以 `LINEAR` 写入关键帧：这是根据当前外部 family 的 `InterpMode=-1`
  观察作出的暂定选择，不外推到未解码的内联 channel family。

这个工具应配合当前版本的静态 Alex 导出器使用。后者已修正两个只有在播放时
才会暴露的蒙皮槽位错误：56-byte packed vertex 流的 glTF `WEIGHTS_0` 是
`[stored0, stored1, stored2, 1-sum]`；而独立 legacy `Weight_List`（Alex 的
皮夹克）在保留原始 `Matrix_List` byte 顺序时必须为
`[1-sum, stored2, stored0, stored1]`。把 packed 排列误套到皮夹克上会令约
80% 顶点被右锁骨主导，后背变成僵硬套筒；两种布局都已在真实 ROT 播放和
合成回归测试中分别验证。bind pose 中所有 skin matrix 都是 identity，不能
单凭静态外观验证这些槽位。

私有 Alex `alex_act_block` 垂直切片已用于可视化验证：输出 48 条真实骨骼
ROT track、无 matrix node；四个没有对应静态 node 的 limb ROT 与 8 个
TRAN group 被显式跳过。以固定的开始、中段、结束时间渲染时，角色始终保持
连贯人体蒙皮并有可见姿势变化。此验证只证明这个 **ROT-only** 切片以及上述
weight 对齐；它不标定 `0x00121119`，不代表所有角色/所有动画均已支持。
