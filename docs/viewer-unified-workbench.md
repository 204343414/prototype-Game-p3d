# 统一工作台：当前可用范围

2026-09-15：`viewer/static/index.html` 是统一外壳，不能将占位入口计为完整功能。

## 地图（已接入真实数据）

- `map-view.js` 通过 `/api/rcf_manifest` 读取基础 Cell 列表，再按需请求
  `/api/rcf_cell_preview?path=<cells.rcf>&cell=N`。不需要完整解包。
- 严格复用 core 三角解析器，显示真实世界坐标灰模；不把原始字节图当高度图。
- 编号筛选、线框、相机复位、卸载及整城队列（512 MiB / 1000 万三角形预算），可暂停、恢复和重试失败项。
- 用户机器 8421 上已完整 API 核对260基础Cell，143城市主体/117无主体/0失败。
- `shared_path` 精确匹配 art.rcf 内 Manhattan 共享 Texture，9,391/16,898组材质已绑定；其他UV布局未猜。
- `map-request.mjs` 覆盖响应体超时与有限重试，gzip减少隧道传输；长期资产缓存尚未实现。
- 仍缺贴图/游戏 shader、实例、接缝验收及全城流式加载；没有宣称完整地图通过。

## 其他面板

- 角色/动画保留 GLB 加载路径，真实 Alex 快捷入口未接入；本轮不验收骨骼线。
- 音效归档为 `00audio.rcf`、`01audio.rcf`，不是 music.rcf / sound.rcf。
  当前摘要不是实时条目分类或播放器。
- 工具控件仍未接入，不能声称四个面板全部功能完好。
- 之前创建的临时骨骼生成器和测试 GLB 已清理，不混入正式样本。

## 验证与部署

服务器 smoke tests 直接运行 `python3 viewer/test_server.py`；self-update 回归是隔离
临时 Git fixture，不触碰用户主项目。`unittest discover -s viewer` 不会收集这些脚本。
临时预览路径、端口及证据见根目录 `HANDOFF.md`。原 8420 不变；原项目尚未正式部署；源码已推送至 openhands/manhattan-workbench-archive 分支。
前端目前仍依赖原有 unpkg three.js 加载；离线依赖管理留作后续，不在地图任务中重写。

## 地图导航

滚轮按指针方向缩放且保留最小观察距离；左键旋转、右键平移，双击表面聚焦局部，
重置视角返回总览。地图不会翻转到旋转中心下方；这不是有碰撞的漫游模式。
静态地图仅在视角/资源变化时绘制，减少空闲时反复渲染整城。
