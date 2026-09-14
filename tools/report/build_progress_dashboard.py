#!/usr/bin/env python3
"""Build a self-contained, private HTML progress dashboard from metadata ledgers.

The dashboard embeds only metadata derived from the user's local installation:
entry names, sizes, structural counts, bounds and review IDs.  It intentionally
never embeds extracted P3D/GLB/DDS/audio payloads.  Its purpose is to make the
current character and Manhattan archaeology state reviewable before the generic
thumbnail/map renderer is complete.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from collections import defaultdict
from typing import Any


def load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def git_commits(repo: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "log", "--oneline", "-6"], cwd=repo, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return [line for line in out.stdout.splitlines() if line]
    except Exception:  # noqa: BLE001
        pass
    return []


def compact_cells(census: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cells = []
    layouts: dict[str, dict[str, Any]] = {}
    for record in census.get("records", []):
        cells.append({
            "id": record["cell_index"],
            "status": record.get("status"),
            "compressed": record.get("entry_size_compressed"),
            "decompressed": record.get("decompressed_size"),
            "geometry": record.get("geometry_count", 0),
            "groups": record.get("position_group_count", 0),
            "strides": record.get("vertex_stride_counts", {}),
            "boundsStatus": record.get("world_position_bounds_status"),
            "min": record.get("world_position_min"),
            "max": record.get("world_position_max"),
            "coreGeometry": record.get("merged_world_geometry_count", 0),
            "coreGroups": record.get("merged_world_position_group_count", 0),
            "coreBoundsStatus": record.get("merged_world_position_bounds_status"),
            "coreMin": record.get("merged_world_position_min"),
            "coreMax": record.get("merged_world_position_max"),
            "warnings": record.get("warning_count", 0),
        })
        for fingerprint in record.get("vertex_description_fingerprints", []):
            signature = fingerprint["layout_sha256"]
            item = layouts.setdefault(signature, {
                "id": signature,
                "count": 0,
                "cells": set(),
                "attributes": fingerprint["attributes"],
                "strides": set(),
            })
            item["count"] += fingerprint.get("primitive_group_count", 0)
            item["cells"].add(record["cell_index"])
            item["strides"].update(fingerprint.get("vertex_strides", []))
    cells.sort(key=lambda item: item["id"])
    compact_layouts = []
    for item in layouts.values():
        compact_layouts.append({
            "id": item["id"],
            "count": item["count"],
            "cellCount": len(item["cells"]),
            "strides": sorted(item["strides"]),
            "attributes": item["attributes"],
        })
    compact_layouts.sort(key=lambda item: (-item["count"], item["id"]))
    return cells, compact_layouts


def compact_artifacts(shelf: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in shelf.get("items", []):
        source = item["source"]
        result.append({
            "id": item["artifact_id"],
            "drawable": item.get("drawable_name"),
            "skeleton": item.get("skeleton_name"),
            "localSkeleton": item.get("skeleton_is_local"),
            "skins": item.get("polyskin_names", []),
            "skinCount": item.get("polyskin_reference_count", 0),
            "attachmentCount": len(item.get("rigid_attachment_references", [])),
            "source": source.get("entry_name"),
            "compressed": source.get("entry_size_compressed"),
            "decompressed": source.get("decompressed_size"),
        })
    return result


def js_data(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build_html(data: dict[str, Any]) -> str:
    payload = js_data(data)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prototype · Archive Field Lab</title>
<style>
:root{{--bg:#0b1114;--panel:#111b20;--panel2:#152329;--line:#283a40;--ink:#e6f0ee;--muted:#8ba29f;--acid:#9edb73;--cyan:#70d0ca;--orange:#ff9e5d;--red:#ed6870;--gold:#f1ca72;--mono:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;--sans:Inter,"Segoe UI",system-ui,sans-serif}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 78% -20%,#17373a 0,transparent 35%),var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;min-height:100vh}}
button,input,select,textarea{{font:inherit}} button{{cursor:pointer;color:inherit}} .app{{display:grid;grid-template-columns:244px 1fr;min-height:100vh}}
.sidebar{{position:sticky;top:0;height:100vh;padding:20px 14px;border-right:1px solid var(--line);background:rgba(9,16,19,.93);display:flex;flex-direction:column;gap:20px;z-index:4}}
.brand{{padding:5px 9px 0}} .brand-mark{{width:34px;height:34px;border:1px solid var(--acid);display:grid;place-items:center;color:var(--acid);font:bold 14px var(--mono);margin-bottom:10px;box-shadow:inset 0 0 18px #9edb7320}} .brand h1{{font-size:15px;letter-spacing:.07em;margin:0;text-transform:uppercase}} .brand p{{margin:5px 0;color:var(--muted);font-size:11px;line-height:1.5}}
.nav{{display:grid;gap:4px}} .nav button{{border:0;background:transparent;text-align:left;padding:11px 12px;border-radius:7px;color:var(--muted);display:flex;align-items:center;gap:10px}}.nav button:hover{{background:#1a292d;color:var(--ink)}}.nav button.active{{background:#203437;color:var(--acid);box-shadow:inset 2px 0 0 var(--acid)}}.nav .nav-num{{margin-left:auto;font:11px var(--mono);opacity:.7}}
.sidebar-foot{{margin-top:auto;border-top:1px solid var(--line);padding:13px 9px 0;color:var(--muted);font-size:11px;line-height:1.55}} .dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--acid);box-shadow:0 0 10px var(--acid);margin-right:7px}}
.main{{padding:30px clamp(22px,4vw,64px) 70px;overflow:hidden}}.top{{display:flex;align-items:flex-start;justify-content:space-between;gap:22px;margin:0 0 28px}}.eyebrow{{color:var(--acid);font:11px var(--mono);letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px}}.top h2{{font-size:clamp(24px,3vw,40px);line-height:1.08;margin:0;letter-spacing:-.04em}}.top p{{color:var(--muted);max-width:680px;margin:9px 0 0;line-height:1.65}}.snapshot{{border:1px solid var(--line);background:#101b1e;padding:11px 13px;min-width:180px;color:var(--muted);font:11px var(--mono);line-height:1.5}}.snapshot b{{color:var(--ink);font-weight:600}}
.page{{display:none;animation:fade .22s ease}}.page.active{{display:block}}@keyframes fade{{from{{opacity:.2;transform:translateY(5px)}}to{{opacity:1;transform:none}}}}
.grid-stats{{display:grid;grid-template-columns:repeat(4,minmax(130px,1fr));gap:12px}}.stat{{min-height:132px;border:1px solid var(--line);background:linear-gradient(145deg,#142126,#0e171b);padding:16px;position:relative;overflow:hidden}}.stat:after{{content:"";position:absolute;right:-25px;bottom:-30px;width:90px;height:90px;border:1px solid #ffffff0c;transform:rotate(45deg)}}.stat-label{{font:11px var(--mono);color:var(--muted);text-transform:uppercase;line-height:1.4}}.stat-value{{font-size:30px;font-weight:680;letter-spacing:-.05em;margin-top:12px}}.stat-note{{margin-top:5px;color:var(--muted);font-size:11px}}.stat.acid .stat-value{{color:var(--acid)}}.stat.cyan .stat-value{{color:var(--cyan)}}.stat.gold .stat-value{{color:var(--gold)}}.stat.orange .stat-value{{color:var(--orange)}}
.two-col{{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(280px,.8fr);gap:16px;margin-top:16px}}.card{{background:linear-gradient(145deg,#132126,#0e171b);border:1px solid var(--line);padding:18px}}.card h3{{font-size:13px;margin:0 0 12px;letter-spacing:.01em}}.card h3 small{{color:var(--muted);font:10px var(--mono);margin-left:8px}}.card p{{color:var(--muted);line-height:1.65;margin:0}}.milestones{{display:grid;gap:0;margin-top:3px}}.milestone{{display:grid;grid-template-columns:23px 1fr auto;gap:9px;align-items:center;padding:11px 0;border-bottom:1px solid #25363b}}.milestone:last-child{{border-bottom:0}}.check{{width:18px;height:18px;border-radius:50%;border:1px solid #48605d;display:grid;place-items:center;font-size:11px;color:transparent}}.check.done{{color:#0d1718;background:var(--acid);border-color:var(--acid)}}.milestone strong{{font-size:12px}}.milestone span{{font:10px var(--mono);color:var(--muted)}}.tag{{border:1px solid var(--line);padding:3px 6px;font:10px var(--mono);color:var(--muted);white-space:nowrap}}.tag.live{{color:var(--acid);border-color:#9edb7350}}.tag.warn{{color:var(--gold);border-color:#f1ca7250}}
.callout{{margin-top:16px;border-left:3px solid var(--cyan);background:#70d0ca0c;padding:14px 16px;line-height:1.65;color:#c8d9d7}}.callout b{{color:var(--cyan)}}.commit-list{{font:11px var(--mono);line-height:1.75;color:#a8b9b7;white-space:pre-wrap}}
.map-layout{{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:14px}}.map-shell{{border:1px solid var(--line);background:#0a1215;position:relative;min-height:560px;overflow:hidden}}#map{{display:block;width:100%;height:560px;touch-action:none;background:radial-gradient(circle at 50% 50%,#16313755,transparent 62%)}}.map-grid{{stroke:#71a8a61b;stroke-width:18}}.cell{{stroke:#0b1114;stroke-width:2;cursor:pointer;transition:opacity .12s}}.cell:hover{{stroke:#e8f5f3;stroke-width:5}}.cell.selected{{stroke:var(--gold);stroke-width:7}}.map-hud{{position:absolute;left:13px;top:12px;pointer-events:none;color:var(--muted);font:10px var(--mono);line-height:1.55;background:#0d191cdd;border:1px solid #2d4749;padding:7px 9px}}.map-buttons{{position:absolute;right:12px;top:12px;display:grid;gap:6px}}.map-buttons button{{width:33px;height:31px;border:1px solid #3d575a;background:#112024cc;color:var(--ink);font-size:16px}}.map-buttons button:hover{{border-color:var(--acid);color:var(--acid)}}.map-caption{{position:absolute;bottom:0;left:0;right:0;padding:9px 12px;background:#0b1316e8;color:var(--muted);font-size:11px;line-height:1.45}}.map-side{{display:grid;gap:14px;align-content:start}}.inspector{{min-height:225px}}.inspect-id{{color:var(--gold);font:700 21px var(--mono);margin-bottom:10px}}.kv{{display:grid;grid-template-columns:1fr auto;gap:7px 10px;border-top:1px solid #2a3b40;padding-top:10px;margin-top:8px;color:var(--muted);font-size:11px}}.kv b{{font:11px var(--mono);color:var(--ink);text-align:right;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.legend{{display:flex;flex-wrap:wrap;gap:8px;font-size:10px;color:var(--muted)}}.legend i{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}}.layout-list{{max-height:255px;overflow:auto;border-top:1px solid var(--line)}}.layout-row{{padding:9px 0;border-bottom:1px solid #223237;font:10px var(--mono);color:var(--muted)}}.layout-row b{{color:var(--ink);font-weight:500}}.layout-row .layout-count{{float:right;color:var(--acid)}}
.shelf-toolbar{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}}.shelf-toolbar input,.shelf-toolbar select{{color:var(--ink);border:1px solid var(--line);background:#101b1e;border-radius:5px;padding:9px 10px;outline:none}}.shelf-toolbar input{{min-width:min(100%,330px);flex:1}}.shelf-toolbar input:focus,.shelf-toolbar select:focus{{border-color:var(--cyan)}}.shelf-toolbar button,.btn{{background:#183034;border:1px solid #3c5a58;border-radius:5px;padding:9px 11px;color:#d9e9e6;font-size:12px}}.shelf-toolbar button:hover,.btn:hover{{border-color:var(--acid);color:var(--acid)}}.shelf-head{{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:10px;color:var(--muted);font-size:11px}}.shelf-head b{{color:var(--ink)}}.shelf-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}}.artifact{{border:1px solid var(--line);background:linear-gradient(145deg,#142126,#0d171a);padding:13px;min-height:184px;position:relative;overflow:hidden}}.artifact.focus{{border-color:#9edb7385;box-shadow:inset 0 0 30px #9edb730c}}.artifact .corner{{position:absolute;right:-12px;top:-15px;width:58px;height:58px;border:1px solid #ffffff12;transform:rotate(45deg)}}.artifact-id{{font:700 11px var(--mono);color:var(--gold);letter-spacing:.02em}}.artifact-title{{font-size:15px;font-weight:650;margin:10px 26px 3px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.artifact-source{{font:10px var(--mono);color:var(--muted);height:29px;overflow:hidden;line-height:1.45}}.artifact-meta{{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}}.artifact-meta span{{font:10px var(--mono);border:1px solid #33474b;color:#adc0bd;padding:3px 5px}}.artifact-skins{{color:var(--cyan);font:10px var(--mono);margin-top:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.focus-box{{position:absolute;right:11px;bottom:11px;display:flex;align-items:center;gap:5px;color:var(--muted);font-size:10px}}.focus-box input{{accent-color:var(--acid)}}.empty{{border:1px dashed #3a5051;color:var(--muted);padding:30px;text-align:center;grid-column:1/-1}}.pager{{display:flex;justify-content:center;align-items:center;gap:12px;margin:19px 0;color:var(--muted);font:11px var(--mono)}}.pager button{{border:1px solid var(--line);background:#101b1e;padding:7px 11px}}.pager button:disabled{{opacity:.35;cursor:not-allowed}}
.review-output{{margin-top:16px;display:grid;grid-template-columns:1fr auto;gap:10px;align-items:start;background:#0b1518;border:1px solid #2b4546;padding:12px}}.review-output textarea{{width:100%;min-height:65px;resize:vertical;background:#080d10;border:1px solid #293b3f;color:#c9ddda;padding:9px;font:11px var(--mono);line-height:1.5}}.note{{font-size:11px;color:var(--muted);line-height:1.55;margin-top:10px}}
@media(max-width:900px){{.app{{grid-template-columns:1fr}}.sidebar{{height:auto;position:relative;display:flex;flex-direction:row;align-items:center;overflow:auto;padding:10px;border-right:0;border-bottom:1px solid var(--line)}}.brand{{min-width:175px;padding:0}}.brand-mark{{display:none}}.nav{{display:flex}}.nav button{{white-space:nowrap}}.sidebar-foot{{display:none}}.main{{padding:24px 18px 55px}}.grid-stats{{grid-template-columns:repeat(2,1fr)}}.two-col,.map-layout{{grid-template-columns:1fr}}.map-shell,#map{{min-height:450px;height:450px}}.top{{display:block}}.snapshot{{margin-top:15px;width:max-content}}}}
</style>
</head>
<body>
<div class="app">
<aside class="sidebar">
 <div class="brand"><div class="brand-mark">P1</div><h1>Archive Field Lab</h1><p>Prototype · 私人考古工作台<br>metadata-only progress build</p></div>
 <nav class="nav">
  <button class="active" data-page="overview">◈ 总览 <span class="nav-num">00</span></button>
  <button data-page="map">▧ 曼哈顿 Cell <span class="nav-num">260</span></button>
  <button data-page="shelf">▦ 带骨架物品栏 <span class="nav-num">491</span></button>
 </nav>
 <div class="sidebar-foot"><span class="dot"></span>本地扫描数据已加载<br>不含模型、纹理或动画二进制。<br><span id="sideTime"></span></div>
</aside>
<main class="main">
 <section class="page active" id="overview">
  <header class="top"><div><div class="eyebrow">Current verified foundation</div><h2>不是“导出了多少”，<br>而是“哪些已经可信”。</h2><p>这里是当前角色、动画和全地图考古的状态面板。城市的 core POSITION 与 uint16 三角连接已在 Cell 2 / 3 严格通过；用户已在两个独立 WebGL 样本中确认 68-byte layout 的 `0x00364509 @ 24` 优于 `@ 32` 作为 color sampler coordinate。法线、完整贴图和实例层仍待解码。</p></div><div class="snapshot">SNAPSHOT<br><b id="snapshotTime">—</b><br><span id="snapshotSource">private local census</span></div></header>
  <div class="grid-stats" id="stats"></div>
  <div class="two-col">
   <article class="card"><h3>当前里程碑 <small>verified / deliberately limited</small></h3><div class="milestones" id="milestones"></div></article>
   <article class="card"><h3>地图阶段门 <small>no false full-map claim</small></h3><p><b style="color:var(--cyan)">已确认：</b>260 个基础 Cell、149 个静态可渲染 Cell、111 个 placeholder，与用户提供的第三方 viewer 计数一致；Cell 2 / 3 的 92 个 core group、62,088 个三角形通过 POSITION / uint16 index 严格检查；用户在两个 WebGL 样本中确认 68-byte layout 的 <code>0x00364509 @ 24</code> 是 color sampler coordinate。</p><div class="callout"><b>下一个真实输出：</b>验证同 layout 的 normal / tangent / vertex color 与 shader transform，再谨慎制作 2–4 Cell 的带材质拼接预览；不会将 local-instance 混入。</div></article>
  </div>
  <div class="two-col">
   <article class="card"><h3>角色物品栏协议 <small>human recognition loop</small></h3><p>已经有 236 个带骨架 P3D 包、491 个 CompositeDrawable 审阅条目。每一项都有稳定 <code>ARC-&lt;hash&gt;-&lt;ordinal&gt;</code>，不靠我猜角色名称。缩略图管线未完成前，卡片只显示结构证据；你勾选后可生成一段可直接发回给我的重点编号。</p></article>
   <article class="card"><h3>最近的项目记录 <small>git main</small></h3><div class="commit-list" id="commits"></div></article>
  </div>
 </section>
 <section class="page" id="map">
  <header class="top"><div><div class="eyebrow">World reconstruction · static layer</div><h2>曼哈顿 Cell 坐标覆盖</h2><p>这不是假装已经渲染的城市截图。每块轮廓来自已扫描到的 <code>mergedDrawableRoot*</code> 世界 POSITION bounds；拖拽平移、滚轮缩放，点击查看真实结构计数。</p></div><div class="snapshot">BASE CELLS<br><b>149 / 260 scanned</b><br>143 core merged roots</div></header>
  <div class="map-layout">
   <div class="map-shell"><svg id="map" role="img" aria-label="Manhattan Cell bounds map"></svg><div class="map-hud">WORLD X / Z<br>drag: pan · wheel: zoom<br><span id="mapReadout">loading…</span></div><div class="map-buttons"><button id="fitMap" title="适应全部核心 Cell">⊙</button><button id="zoomIn" title="放大">+</button><button id="zoomOut" title="缩小">−</button></div><div class="map-caption">仅绘制可验证的 <b>mergedDrawableRoot*</b> bounds。placeholder 和 local-space prop 不按编号假造空间位置。</div></div>
   <div class="map-side"><article class="card inspector"><h3>Cell 检视器 <small>click a region</small></h3><div id="inspector"></div></article><article class="card"><h3>状态图例</h3><div class="legend"><span><i style="background:#70d0ca"></i>核心世界几何</span><span><i style="background:#f1ca72"></i>当前选中</span><span><i style="background:#36494d"></i>placeholder（不绘制）</span></div><p class="note">基础层：149 个成功扫描、111 个空 placeholder、0 个解析错误。`_ft` gameplay/meta-object 配对层尚未混入。</p></article><article class="card"><h3>Vertex layout 家族 <small id="layoutCount"></small></h3><div class="layout-list" id="layouts"></div></article></div>
  </div>
 </section>
 <section class="page" id="shelf">
  <header class="top"><div><div class="eyebrow">Rigged candidate shelf · review IDs are stable</div><h2>带骨架“文物”物品栏</h2><p>这里故意没有用名称先筛“像不像角色”。所有通过结构条件的 CompositeDrawable 都保留：有命名骨架、并至少含一个 type=2 polyskin。真正的 3D 拍摄缩略图要等通用静态导出按布局验证后再接入。</p></div><div class="snapshot">RIGGED CANDIDATES<br><b>491 objects</b><br>236 source packages</div></header>
  <div class="callout"><b>当前是考古索引，不是模型照片墙。</b> 每张卡的 MB 是来源 P3D 包的解压后大小；一包可以装多个人、武器或机关，所以大小不等于某一个人体网格的精确大小。你现在就能用右下角勾选重点，之后把生成的编号消息发给我。</div>
  <div class="shelf-toolbar"><input id="artifactQuery" placeholder="搜索 ARC 编号、drawable、skeleton 或源路径…"><select id="skeletonFilter"><option value="all">全部骨架关联</option><option value="local">骨架在同包</option><option value="external">外部骨架引用</option></select><select id="sortArtifacts"><option value="size">按来源包 MB 从大到小</option><option value="name">按 drawable 名称</option><option value="id">按 ARC 编号</option></select><button id="clearFocus">清空本机勾选</button></div>
  <div class="shelf-head"><span id="artifactCount">—</span><span>每页 48 项 · <b>勾选状态仅保存在这个浏览器</b></span></div><div class="shelf-grid" id="artifactGrid"></div><div class="pager"><button id="prevPage">← 上一页</button><span id="pageInfo">—</span><button id="nextPage">下一页 →</button></div>
  <div class="review-output"><textarea id="reviewText" readonly aria-label="重点修缮消息">尚未勾选物品。</textarea><button class="btn" id="copyReview">复制消息</button></div><p class="note">建议消息格式：在勾选后的编号后补充你的记忆，例如“黑色守望军官候选”“驾驶兵”“建筑机关”“不做角色”。我会把它们回填为角色、路人、环境物、载具或重点修缮任务。</p>
 </section>
</main>
</div>
<script>
const DATA={payload};
const fmt=new Intl.NumberFormat('zh-CN');
const mib=n=>n==null?'—':(n/1048576).toFixed(n>=10485760?1:2)+' MiB';
const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const baseName=s=>String(s||'').split('\\\\').pop();
const cellById=new Map(DATA.cells.map(c=>[c.id,c]));
const statusCounts=DATA.cells.reduce((a,c)=>(a[c.status]=(a[c.status]||0)+1,a),{{}});
document.getElementById('snapshotTime').textContent=DATA.generatedAt.replace('T',' ').replace('+00:00',' UTC');
document.getElementById('sideTime').textContent='Build '+DATA.generatedAt.slice(0,10);
const stats=[
 ['地图基础 Cell',fmt.format(DATA.cells.length),'149 有静态内容','acid'],
 ['核心世界根',fmt.format(DATA.cells.filter(c=>c.coreBoundsStatus==='ok').length),'mergedDrawableRoot*','cyan'],
 ['静态 layout',fmt.format(DATA.layouts.length),'已从 vertex declaration 分组','gold'],
 ['带骨架审阅项',fmt.format(DATA.artifacts.length),'236 个来源 P3D 包','orange'],
];
document.getElementById('stats').innerHTML=stats.map(([a,b,c,d])=>`<div class="stat ${{d}}"><div class="stat-label">${{a}}</div><div class="stat-value">${{b}}</div><div class="stat-note">${{c}}</div></div>`).join('');
const milestones=[
 ['done','Alex 静态蒙皮','皮夹克 legacy Weight_List 已用户目检通过','verified'],
 ['done','Alex ROT-only 动画','11 个真实外部 PTRN 动作可预览','verified'],
 ['done','角色候选全量普查','2,219 P3D → 491 可审阅 CompositeDrawable','verified'],
 ['done','曼哈顿基础 Cell 普查','260 / 260；149 renderable；111 placeholder','verified'],
 ['done','Cell 2 / 3 core 三角连接','92 个 group、115,388 顶点、62,088 triangles；0 errors','verified'],
 ['done','68-byte layout color coordinate','两个 WebGL 样本：0x00364509 @ 24 优于 @ 32','verified'],
 ['done','68-byte layout surface vectors','三组几何证据：0xC206BCE7 @ 40 normal；0xA4176245 @ 52 tangent','verified'],
 ['live','Cell 2 合并 core material diagnostic','58 groups；8-texture budget 下 7 组已贴图、51 组灰色上下文','active'],

 ['live','静态地图 normal / shader transform','定向光渲染回归，再验证 vertex color、transform 与 multi-stream','active'],
 ['warn','完整 TRAN 动画标定','未验证的 int16 TRAN 不导出为“正确位移”','blocked'],
];
document.getElementById('milestones').innerHTML=milestones.map(([st,title,note,tag])=>`<div class="milestone"><i class="check ${{st==='done'?'done':''}}">✓</i><div><strong>${{title}}</strong><br><span>${{note}}</span></div><em class="tag ${{tag==='active'?'live':tag==='blocked'?'warn':''}}">${{tag}}</em></div>`).join('');
document.getElementById('commits').textContent=(DATA.commits||[]).join(String.fromCharCode(10))||'本地工作树未读取到提交记录';

// ---- Cell bounds map -----------------------------------------------------
const svg=document.getElementById('map'), NS='http://www.w3.org/2000/svg';
const core=DATA.cells.filter(c=>c.coreBoundsStatus==='ok'&&c.coreMin&&c.coreMax);
const global={{minX:Math.min(...core.map(c=>c.coreMin[0])),maxX:Math.max(...core.map(c=>c.coreMax[0])),minZ:Math.min(...core.map(c=>c.coreMin[2])),maxZ:Math.max(...core.map(c=>c.coreMax[2]))}};
const pad=.05*Math.max(global.maxX-global.minX,global.maxZ-global.minZ); const initial={{x:global.minX-pad,y:-global.maxZ-pad,w:(global.maxX-global.minX)+2*pad,h:(global.maxZ-global.minZ)+2*pad}}; let vb={{...initial}},selected=core.find(c=>c.id===2)||core[0],drag=null;
function setVB(){{svg.setAttribute('viewBox',`${{vb.x}} ${{vb.y}} ${{vb.w}} ${{vb.h}}`);document.getElementById('mapReadout').textContent=`${{Math.round(vb.w)}} × ${{Math.round(vb.h)}} world units`;}}
function density(c){{return Math.max(.22,Math.min(.94,Math.log10((c.coreGroups||1)+1)/5));}}
function drawMap(){{svg.innerHTML='';const grid=document.createElementNS(NS,'rect');grid.setAttribute('class','map-grid');grid.setAttribute('x',initial.x);grid.setAttribute('y',initial.y);grid.setAttribute('width',initial.w);grid.setAttribute('height',initial.h);grid.setAttribute('fill','none');svg.appendChild(grid);for(const c of core){{const r=document.createElementNS(NS,'rect');r.setAttribute('class','cell'+(c.id===selected.id?' selected':''));r.dataset.id=c.id;r.setAttribute('x',c.coreMin[0]);r.setAttribute('y',-c.coreMax[2]);r.setAttribute('width',Math.max(.15,c.coreMax[0]-c.coreMin[0]));r.setAttribute('height',Math.max(.15,c.coreMax[2]-c.coreMin[2]));r.setAttribute('fill',`rgba(112,208,202,${{density(c)}})`);r.addEventListener('click',()=>{{if(!drag||drag.moved<5){{selected=c;drawMap();inspect(c);}}}});svg.appendChild(r);}}setVB();}}
function inspect(c){{const b=c.coreMin&&c.coreMax?`X ${{c.coreMin[0].toFixed(1)}} → ${{c.coreMax[0].toFixed(1)}}<br>Z ${{c.coreMin[2].toFixed(1)}} → ${{c.coreMax[2].toFixed(1)}}`:'—';document.getElementById('inspector').innerHTML=`<div class="inspect-id">CELL_${{String(c.id).padStart(3,'0')}}</div><div class="kv"><span>状态</span><b>${{c.status}}</b><span>解压包大小</span><b>${{mib(c.decompressed)}}</b><span>所有 Geometry</span><b>${{fmt.format(c.geometry||0)}}</b><span>核心世界 Geometry</span><b>${{fmt.format(c.coreGeometry||0)}}</b><span>核心 PrimitiveGroup</span><b>${{fmt.format(c.coreGroups||0)}}</b><span>vertex stride</span><b>${{Object.keys(c.strides||{{}}).join(', ')||'—'}} B</b><span>世界范围</span><b style="white-space:normal">${{b}}</b></div>`;}}
function zoom(f){{const cx=vb.x+vb.w/2,cy=vb.y+vb.h/2;vb.w*=f;vb.h*=f;vb.x=cx-vb.w/2;vb.y=cy-vb.h/2;setVB();}}document.getElementById('fitMap').onclick=()=>{{vb={{...initial}};setVB();}};document.getElementById('zoomIn').onclick=()=>zoom(.72);document.getElementById('zoomOut').onclick=()=>zoom(1.4);
svg.addEventListener('wheel',e=>{{e.preventDefault();const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const at=p.matrixTransform(svg.getScreenCTM().inverse());const f=e.deltaY>0?1.17:.855;vb.x=at.x-(at.x-vb.x)*f;vb.y=at.y-(at.y-vb.y)*f;vb.w*=f;vb.h*=f;setVB();}},{{passive:false}});svg.addEventListener('pointerdown',e=>{{drag={{x:e.clientX,y:e.clientY,v:{{...vb}},moved:0}};svg.setPointerCapture(e.pointerId)}});svg.addEventListener('pointermove',e=>{{if(!drag)return;const r=svg.getBoundingClientRect(),dx=(e.clientX-drag.x)/r.width*drag.v.w,dy=(e.clientY-drag.y)/r.height*drag.v.h;drag.moved=Math.max(drag.moved,Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y));vb.x=drag.v.x-dx;vb.y=drag.v.y-dy;setVB();}});svg.addEventListener('pointerup',()=>setTimeout(()=>drag=null,0));drawMap();inspect(selected);
document.getElementById('layoutCount').textContent=`${{DATA.layouts.length}} detected`;
document.getElementById('layouts').innerHTML=DATA.layouts.slice(0,16).map(l=>`<div class="layout-row"><span class="layout-count">${{fmt.format(l.count)}} groups</span><b>${{l.id.slice(0,12)}}</b><br>stride ${{l.strides.join('/')}} B · ${{l.attributes.length}} attrs · ${{l.cellCount}} Cells<br>${{l.attributes.map(a=>a.semantic_hash+'@'+a.offset).join('  ')}}</div>`).join('');

// ---- Artifact review shelf ------------------------------------------------
const KEY='prototype-archive-focus-v1';let focus=new Set();try{{focus=new Set(JSON.parse(localStorage.getItem(KEY)||'[]'));}}catch(e){{}} let artifactPage=0; const pageSize=48;
function filteredArtifacts(){{const q=document.getElementById('artifactQuery').value.trim().toLowerCase(),filter=document.getElementById('skeletonFilter').value,sort=document.getElementById('sortArtifacts').value;let out=DATA.artifacts.filter(a=>{{const hay=[a.id,a.drawable,a.skeleton,a.source,...a.skins].join(' ').toLowerCase();return(!q||hay.includes(q))&&(filter==='all'||(filter==='local'?a.localSkeleton:!a.localSkeleton));}});out.sort((a,b)=>sort==='size'?(b.decompressed||0)-(a.decompressed||0):sort==='name'?String(a.drawable).localeCompare(String(b.drawable)):a.id.localeCompare(b.id));return out;}}
function persistFocus(){{try{{localStorage.setItem(KEY,JSON.stringify([...focus]));}}catch(e){{}}}}
function message(){{const picks=[...focus].sort();const area=document.getElementById('reviewText');area.value=picks.length?`重点修缮 / 待目检：\n${{picks.map(id=>`- ${{id}} — 请在此补充角色记忆或用途`).join(String.fromCharCode(10))}}\n\n请保留未列出的物品为未分类。`:'尚未勾选物品。';}}
function renderShelf(){{const list=filteredArtifacts(),total=Math.max(1,Math.ceil(list.length/pageSize));artifactPage=Math.max(0,Math.min(artifactPage,total-1));const chunk=list.slice(artifactPage*pageSize,(artifactPage+1)*pageSize);document.getElementById('artifactCount').innerHTML=`当前显示 <b>${{fmt.format(list.length)}}</b> / ${{fmt.format(DATA.artifacts.length)}} 项；已勾选 <b>${{focus.size}}</b> 项`;document.getElementById('artifactGrid').innerHTML=chunk.length?chunk.map(a=>`<article class="artifact ${{focus.has(a.id)?'focus':''}}"><div class="corner"></div><div class="artifact-id">${{esc(a.id)}}</div><div class="artifact-title" title="${{esc(a.drawable)}}">${{esc(a.drawable)}}</div><div class="artifact-source" title="${{esc(a.source)}}">${{esc(baseName(a.source))}}</div><div class="artifact-meta"><span>${{mib(a.decompressed)}}</span><span>${{a.skinCount}} skin</span><span>${{a.localSkeleton?'local skel':'external skel'}}</span><span>${{a.attachmentCount}} attach</span></div><div class="artifact-skins" title="${{esc(a.skins.join(', '))}}">${{esc(a.skins.join(' · ')||'no named polyskin')}}</div><label class="focus-box"><input type="checkbox" data-focus="${{esc(a.id)}}" ${{focus.has(a.id)?'checked':''}}>重点</label></article>`).join(''):'<div class="empty">没有符合当前过滤条件的审阅项。</div>';document.querySelectorAll('[data-focus]').forEach(box=>box.addEventListener('change',()=>{{box.checked?focus.add(box.dataset.focus):focus.delete(box.dataset.focus);persistFocus();message();renderShelf();}}));document.getElementById('pageInfo').textContent=`${{artifactPage+1}} / ${{total}}`;document.getElementById('prevPage').disabled=artifactPage===0;document.getElementById('nextPage').disabled=artifactPage>=total-1;}}
for(const id of ['artifactQuery','skeletonFilter','sortArtifacts']) document.getElementById(id).addEventListener(id==='artifactQuery'?'input':'change',()=>{{artifactPage=0;renderShelf();}});document.getElementById('prevPage').onclick=()=>{{artifactPage--;renderShelf();}};document.getElementById('nextPage').onclick=()=>{{artifactPage++;renderShelf();}};document.getElementById('clearFocus').onclick=()=>{{focus.clear();persistFocus();message();renderShelf();}};document.getElementById('copyReview').onclick=async()=>{{const b=document.getElementById('copyReview');try{{await navigator.clipboard.writeText(document.getElementById('reviewText').value);b.textContent='已复制 ✓';setTimeout(()=>b.textContent='复制消息',1200)}}catch(e){{document.getElementById('reviewText').focus();document.getElementById('reviewText').select();b.textContent='请手动复制';}}}};message();renderShelf();

// navigation
for(const button of document.querySelectorAll('.nav button'))button.addEventListener('click',()=>{{document.querySelectorAll('.nav button').forEach(x=>x.classList.toggle('active',x===button));document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===button.dataset.page));window.scrollTo({{top:0,behavior:'smooth'}});}});
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell-census", required=True)
    parser.add_argument("--rigged-shelf", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    args = parser.parse_args()
    census = load_json(args.cell_census)
    shelf = load_json(args.rigged_shelf)
    cells, layouts = compact_cells(census)
    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "cells": cells,
        "layouts": layouts,
        "artifacts": compact_artifacts(shelf),
        "commits": git_commits(args.repo),
    }
    html = build_html(payload)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(html)
    print(f"Wrote {args.out}: {len(cells)} Cells, {len(layouts)} vertex layouts, {len(payload['artifacts'])} artifacts")


if __name__ == "__main__":
    main()
