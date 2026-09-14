# Prototype `Animation` (0x00121000) chunk数据组织范式

> 本文档记录对`alex.p3d.rz`内嵌动画数据的完整逆向结果。**重要结论：Prototype的动画数据格式与`netp3dlib`（面向《辛普森一家：亡命天涯》等Radical早期游戏）定义的标准Pure3D Animation格式不同——Prototype把所有关键帧数值数据打包压缩进一个独立的ZLIB blob（`0x02F00000`），channel chunk本身只存"元数据指针"（类型/插值模式/帧数/blob内offset），不直接内联frames/values数组。** 這是理解Prototype动画数据的关键，务必按下文实现，不要照搬netp3dlib的直读字段方式。

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
└─ Animation_Group_List (0x00121002)             version(u32), NumGroups(u32，冗余，可从子节点数计算)
   └─ Animation_Group (0x00121001) ×NumGroups     version(u32) + name(p3d string，通常=骨骼名，如"Pelvis"/"Hip_L"/"Spine_1") + GroupID(u32，全局关节索引，非从0连续，可能跳号) + NumChannels(u32)
      └─ 1~2个channel节点，常见两种FourCC参数：
         "TRAN" → 位移通道，具体chunk类型为 0x00121119（自定义压缩vector3，非netp3dlib的Vector_3D_OF_Channel=0x121104）
         "ROT"  → 旋转通道，具体chunk类型为以下之一：
                   0x00121112 = 26次出现，主关节，值块=int16×3/值（6 bytes/value）
                   0x00121114 = 26次出现，末梢小关节（手指/下颚/脚尖等），值块=int8×3/值（3 bytes/value）
                   0x00121119 = 也用于极少数"TRAN"角色（Character_Root/Spine_1等），说明0x00121119不是固定"仅TRAN"专用，是一种通用压缩vector3类型，被同时用于位移和某些特殊旋转场合（需具体验证特例）
         每个channel节点下还有2个子chunk：
           Channel_Interpolation_Mode (0x00121110)   version(u32) + InterpMode(i32，观察值恒为-1=0xFFFFFFFF，插值模式未变化过，可能代表"默认/线性")
           0x00121120（数据定位符）                    version(u32) + Count(u32，帧数) + Offset(u32，在ZLIB解压后blob里的字节offset)
```

## 3. ZLIB blob内部布局（对每个channel，按`0x00121120`给出的`(count, offset)`去blob里读取）

```
blob[offset : offset + align4(count*2)]                     = frames: uint16[count]     （关键帧所在的帧号，非连续，如(0,1,2,3,5,8,11,...)）
blob[offset+align4(count*2) : ... + align4(count*V)]         = values: 見下表，V=每个值的字节数，紧跟在frames后面，起始位置同样4字节对齐
```
其中`align4(x) = (x+3) & ~3`（向上取整到4字节边界）。

### 已确认的3种channel值编码：

| Channel类型ID | 字节数/值(V) | 用途 | 解码公式 |
|---|---|---|---|
| `0x00121112` | 6 (int16×3) | 主要骨骼ROT（压缩四元数，去掉W分量） | `x,y,z = int16/32767`；`w = sqrt(1 - x²-y²-z²)`（等价于netp3dlib的`Compressed_Quaternion_Channel_2`算法，但ID不同——netp3dlib里`0x121112`叫`Compressed_Quaternion_Channel_2`，**恰好完全吻合**，说明这一类型netp3dlib的定义可直接照搬） |
| `0x00121114` | 3 (int8×3) | 末梢小关节ROT（更粗精度的压缩四元数） | `x,y,z = int8/127`；`w = sqrt(1 - x²-y²-z²)`（同上算法，字节宽度减半，netp3dlib未收录此变体，是Prototype自己的扩展类型） |
| `0x00121119` | 6 (int16×3) | 位移TRAN（也偶见于个别ROT，需具体核实） | `x,y,z = int16 / SCALE`（SCALE未标定，观察到原始int16范围可达±26000量级，暂以`/32767`或自定义bind-pose-relative scale处理，导出验证时需要用实际bindpose尺度校准；不是单位四元数，无需sqrt处理） |

## 4. 已用真实数据交叉验证的一致性检查

- `Animation_Header.NumGroups`(53) = `Animation_Group_List`实际子节点数(53) ✓
- `0x00121008`直方图统计的3种类型计数（26/26/8）= 逐个Animation_Group实际枚举出的channel类型计数总和（26/26/8）✓
- 相邻channel的`(count,offset)`可以精确预测下一个channel的offset：`next_offset = offset + align4(count*2) + align4(count*V)`，在全部53个group、60个channel上验证零误差 ✓
- 骨骼名（`Pelvis`/`Hip_L`/`Knee_L`/`Ankle_L`/`Ball_L`/`Spine_1`/`Spine_2`/`Spine_3`/`Clavicle_L`/`Shoulder_L`/`Elbow_L`/`Wrist_L`/`Index_Base_L`...等）与`alex_reg_body_skeleton`里已解析的67关节骨架的关节名完全对应 ✓

## 5. 与netp3dlib标准定义的差异总结

netp3dlib（面向未压缩/内联式Animation格式）里`Animation_Group`直接包含frames+values内联在channel chunk payload里；而Prototype版本把**同一份数据搬到了外部ZLIB blob**，channel chunk payload瘦身为仅剩"类型标识+count+offset"的定位符（`0x00121120`）。这是Prototype对存储做的自定义压缩优化，编写解析器时必须：
1. 先定位并解压`0x02F00000` blob（每个Animation chunk恰好1个）。
2. 遍历`Animation_Group_List`下每个`Animation_Group`，读取其channel节点的类型ID + `0x00121120`给出的`(count,offset)`。
3. 用上表公式去blob里按offset切片解码，而不是尝试从channel chunk自身payload里找frames/values（那里只有12字节的元数据）。

## 6. 尚待验证/待办

- `0x00121119`用作TRAN时的具体缩放系数（bind pose绝对坐标 vs 相对父骨骼偏移，需要用实际骨骼局部矩阵对照校准）尚未标定，应用动画到glTF前必须先解决。
- `CAM`(摄像机)类型的Animation结构未单独验证（推测与骨骼动画共用同一套group/channel机制，只是Animation_Group对应摄像机的位置/朝向/FOV等参数而非骨骼，需要时再抓样本验证）。
- 插值模式恒为`-1`，暂视为线性插值处理（Cyclic标记决定动画是否循环，可直接从Animation chunk头部读取）。
