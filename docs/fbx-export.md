# FBX、贴图与批量导出

本工具从用户自己的 Prototype 安装目录读取 RCF/P3D 数据。仓库不包含游戏归档、贴图、模型、音频或 Autodesk/Blender 二进制。

## 依赖

- Python 3.11 或更高版本；
- Prototype 的合法本地安装；
- Blender 4.5 LTS 命令行程序，用于把内部的源数据中间表示转换为真正的二进制 FBX 7.4。

推荐显式设置：

```bash
export PROTOTYPE_BLENDER=/absolute/path/to/blender
export PROTOTYPE_EXPORT_ROOT=/absolute/path/to/PrototypeExports
python3 viewer/server.py --host 127.0.0.1 --port 8421 --root /path/to/Prototype
```

未设置 `PROTOTYPE_EXPORT_ROOT` 时，为兼容当前研究环境，默认值是 `/mnt/hdd/PrototypeExports`。公开使用时建议始终设置它。`/api/health` 会报告实际导出根目录和 Blender 是否可用。

## 单个实体

实体卡片上的 **导出 FBX 资源包（贴图＋源动画）** 会下载一个 ZIP：

```text
entity-FBX.zip
├── entity.fbx
├── diffuse.png
├── normal.png
└── specular.png（源包存在时）
```

FBX 使用与自身同目录的贴图文件名，不依赖 Unity 对 FBX Embedded Media 的支持。

动画规则：

- 源动画关键帧不被改写；
- 每个有骨架的文件额外增加一个一帧 `000_A_POSE_BIND`；
- 它直接来自源绑定矩阵，并作为 FBX 默认 Take；
- 其他源 Action 通过独立 NLA Take 导出；
- 没有匹配骨架的摄像机或外部轨道会进入遗漏清单，不会错误绑定到角色。

## 批量实体导出

实体抽屉提供：

- 当前类别与搜索过滤；
- 服务器导出路径；
- 是否覆盖；
- **批量导出当前筛选结果**。

相对路径以 `PROTOTYPE_EXPORT_ROOT` 为根；绝对路径也必须位于该根目录内。服务器拒绝目录穿越和写入游戏目录。

输出结构：

```text
FBXBatch/
├── entity_name-sourcehash/
│   ├── entity_name.fbx
│   ├── diffuse.png
│   └── ...
└── batch-export-manifest.json
```

任务在后台串行执行，网页轮询 `/api/export_job` 显示总数、当前实体、成功数与错误数。单项失败不会终止整个批次。大型角色可能包含数百个动作，精确的无简化 FBX 烘焙可能耗时数分钟到二十分钟，并产生很大的文件。

## 地图导出

地图面板的导出路径留空时下载 `loaded-manhattan-cells-FBX.zip`；填写路径时，FBX 与贴图直接写入服务器目录。

当前范围是已加载且严格验证的 Manhattan Cell 城区主体和已解析材质。放置实体、特效、碰撞/导航及尚未解码的非核心记录不会被伪造，因此不能把当前结果称为完整地图。

## Unity 导入

1. 解压 FBX 资源 ZIP；
2. 保持 FBX 与 PNG 位于同一目录；
3. 将整个目录拖入 Unity `Assets`；
4. 在 Model Importer 的 Rig 页手工选择 Generic 或 Humanoid；
5. Humanoid 需要用户自行检查 Avatar 骨骼映射；
6. Clips 页中 `000_A_POSE_BIND` 是默认绑定姿势，其余为原始源动画。

工具不会创建 Unity 工程，也不会自动决定 Humanoid 映射或篡改源动画来迎合重定向。

## 验证

开发验证至少应覆盖：

- FBX 文件头为 `Kaydara FBX Binary`、版本 7400；
- FBX 回读后网格、Armature、材质和图片数量；
- 默认 Take 为 `000_A_POSE_BIND`；
- 至少一个源动画在首尾帧之间产生非零骨骼矩阵变化；
- 批量任务输出 manifest，并在单项错误后继续；
- 地图导出目录不会携带上一次任务的陈旧贴图。
