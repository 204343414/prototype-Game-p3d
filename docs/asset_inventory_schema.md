# RCF 资产元数据台账规范

> 本规范定义项目的**元数据**资产台账。它记录怎样定位和研究用户本地合法安装中的资源，绝不把游戏资源内容加入仓库。

## 边界

- 可记录：归档本地路径、版本、条目名、名称哈希、偏移、压缩后大小、启发式分类、解析/预览状态、生成命令、源归档哈希（后续可补）。
- 不可记录进 Git：提取后的模型、贴图、音频、归档文件、GLB/GLTF、DDS/PNG/WAV 等资源内容。
- 生成的完整台账默认写在项目工作树外（如 `/tmp` 或用户私有研究目录）；如果未来要提交一个精简的公开台账，须先单独确认其范围与版权风险。

## 生成工具

[`tools/inventory/rcf_inventory.py`](../tools/inventory/rcf_inventory.py) 通过本地 viewer 的 `GET /api/rcf_manifest` 请求 `limit=0` 取得完整 RCF **条目元数据**。它不会请求 `raw=1`、不会解压或保存 entry payload。

```bash
python3 tools/inventory/rcf_inventory.py \
  --base-url 'https://<当前隧道>.trycloudflare.com' \
  --rcf-path '/本机/Prototype/art.rcf' \
  --json /tmp/art_rcf_inventory.json \
  --md /tmp/art_rcf_inventory.md
```

输出 JSON 是机器可读主记录；Markdown 是受限长度的人类浏览报告。分类来自文件名启发式，不能当作已验证的格式或角色身份结论。

## 全量带骨架普查（结构而非名称筛选）

当目标是建立角色/可动对象的候选货架而非按文件名猜角色时，使用 viewer 的分页 `GET /api/rcf_rigged_manifest` 和客户端 [`tools/inventory/rigged_inventory.py`](../tools/inventory/rigged_inventory.py)：

```bash
python3 tools/inventory/rigged_inventory.py \
  --base-url 'https://<当前隧道>.trycloudflare.com' \
  --rcf-path '/本机/Prototype/art.rcf' \
  --out /private/research/art_rigged_inventory.json
```

该扫描覆盖所有**已知名称**的 `.p3d.rz` 包，不按 `alex`、`soldier`、`characters` 等词过滤。一个候选的结构条件是：`CompositeDrawable` 有骨架名，且其直接子引用至少有一个 `type=2` polyskin。响应/输出仅保留 entry 元数据、骨架/CompositeDrawable/primitive/shader 的名称和计数；绝不输出顶点、索引、贴图、动画 key 或 raw asset payload。

随后 [`tools/inventory/build_rigged_shelf.py`](../tools/inventory/build_rigged_shelf.py) 将完整普查扁平为稳定 `ARC-<entry-hash>-<ordinal>` review ID 的物品栏元数据。它给未来缩略图和人工勾选提供精确引用，但它本身不生成或伪造模型预览。

## JSON schema v1

顶层字段：

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定为 `1`。字段语义变更时递增。 |
| `generated_at_utc` | 生成时刻，ISO 8601 UTC。 |
| `source` | 归档路径、大小、端序、版本及 API 报告的计数。 |
| `summary` | 本台账实际写入条目数，及按分类/扩展名统计的计数。 |
| `entries` | 按名称排序的条目元数据列表。 |

`entries[]` 中每项：

| 字段 | 含义 |
|---|---|
| `name` | RCF metadata 中的逻辑条目名；未知时为 `null`。 |
| `name_hash` | API 返回的十六进制条目哈希。 |
| `offset` / `size` | RCF 内偏移与压缩存储大小。不是提取后资源大小。 |
| `extension` | 逻辑扩展名；`.p3d.rz` 保持成一项，便于筛选。 |
| `categories` | 名称启发式分类；可为多个值，未知名称使用 `unresolved_name`。 |

## 后续人工补充字段（不由 v1 扫描器伪造）

当真实解析或预览完成后，角色/动画/音频的工作台账可在此 schema 基础上增加独立的手工审核层：

- `verification_status`：`discovered`、`located`、`parsed`、`static_preview_passed`、`animation_preview_passed`、`blocked`。
- `verification_evidence`：对应格式文档章节、测试名称、无版权资源内容的截图/日志摘要或 commit。
- `character_id` / `variant_id`：经过实际 chunk、骨架或人工目检确认后才填写；不可由文件名猜测直接写死。
- `skeleton_name`、`joint_count`、`animation_count`、`audio_format`：只在解析结果已验证后填写。
- `blocked_reason`：变体顶点格式、未知编码、缺少骨架、损坏数据等明确原因。

保持“扫描发现”和“已验证事实”分层，是避免把早期命名猜测误当项目结论的基础。
