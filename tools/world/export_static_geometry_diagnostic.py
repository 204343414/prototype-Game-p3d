#!/usr/bin/env python3
"""Strict, core-only triangle diagnostics for Prototype Manhattan Cell geometry.

This is deliberately *not* a general P3D exporter.  It reads only the
``mergedDrawableRoot*`` Geometry in explicitly selected base Cells and accepts
only the structure independently observed in real Manhattan samples:

* LE Pure3D chunks;
* ``PrimitiveGroup`` type 0 (TriangleList; corroborated by NetP3DLib);
* exactly one memory-image vertex list, 16-bit index list and vertex
  declaration per primitive group;
* the verified static POSITION declaration at offset 0; and
* a valid uint16 index stream with three in-range indices per triangle.

The regular JSON report is metadata-only: it records counts, bounds and
validation statistics but never stores vertex or index data.  Supplying
``--preview-html`` explicitly makes a private, standalone WebGL diagnostic
preview.  That preview contains only the derived POSITION and index streams
needed to inspect the selected cells; it is never a repository artifact and
uses neither textures nor guessed material/UV/normal semantics.

P3D bytes are fetched from the user's local viewer only into process memory;
they are not written to disk by this command.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import json
import math
import os
import struct
import tempfile
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from probe_static_geometry import (
    GEOMETRY,
    MEMORY_VERTEX_DESCRIPTION,
    MEMORY_VERTEX_LIST,
    PRIMITIVE_GROUP,
    WORLD_MERGED_GEOMETRY_PREFIX,
    _fingerprint_vertex_description,
    _parse_primitive_group,
    _p3d_string,
    _walk,
)

MEMORY_INDEX_LIST = 0x00010013
# This hash is not a name guess: every observed Manhattan static declaration
# has it at offset 0, and interpreting it as float32x3 produces finite,
# city-scale bounds in the complete Cell census.  The rest of the declaration
# remains intentionally unnamed until texture-rendered regression proves it.
VERIFIED_POSITION_HASH = "0x2C929929"
TRIANGLE_LIST = 0


class CoreGeometryError(ValueError):
    """A selected world-core primitive did not meet the strict diagnostic ABI."""


@dataclass(frozen=True)
class DecodedCoreGroup:
    entry_name: str
    geometry_name: str
    group_ordinal: int
    shader_name: str
    layout_sha256: str
    vertex_stride: int
    positions: bytes
    indices: bytes
    vertex_count: int
    index_count: int
    position_min: list[float]
    position_max: list[float]
    index_min: int
    index_max: int
    repeated_index_triangle_count: int
    zero_area_triangle_count: int

    @property
    def triangle_count(self) -> int:
        return self.index_count // 3


def _direct_children(records: list[dict[str, Any]], parent: int) -> list[tuple[int, dict[str, Any]]]:
    return [(index, record) for index, record in enumerate(records) if record["parent"] == parent]


def _parse_memory_vertex_list(payload: bytes, vertex_count: int, declaration: dict[str, Any]) -> tuple[int, bytes, list[float], list[float]]:
    if len(payload) < 12:
        raise CoreGeometryError("MemoryImageVertexList header is shorter than 12 bytes")
    _version, _param, byte_count = struct.unpack_from("<III", payload, 0)
    body = payload[12:]
    if byte_count != len(body):
        raise CoreGeometryError(f"MemoryImageVertexList byte_count={byte_count}, actual={len(body)}")
    if vertex_count <= 0 or byte_count % vertex_count:
        raise CoreGeometryError(f"vertex data {byte_count} bytes is not divisible by vertex_count={vertex_count}")
    stride = byte_count // vertex_count

    attributes = declaration["attributes"]
    matching = [attribute for attribute in attributes if attribute["semantic_hash"] == VERIFIED_POSITION_HASH]
    if len(matching) != 1:
        raise CoreGeometryError(
            f"expected exactly one verified POSITION hash {VERIFIED_POSITION_HASH}, found {len(matching)}")
    position = matching[0]
    if position["source"] != 0 or position["offset"] != 0:
        raise CoreGeometryError(
            "verified POSITION declaration is not source 0 / offset 0 "
            f"(source={position['source']}, offset={position['offset']})")
    if position["stride"] != stride:
        raise CoreGeometryError(
            f"POSITION declaration stride={position['stride']} does not match vertex data stride={stride}")
    if stride < 12 or stride % 4:
        raise CoreGeometryError(f"unsupported float32 POSITION-compatible stride {stride}")

    packed_positions = bytearray(vertex_count * 12)
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    for number, offset in enumerate(range(0, len(body), stride)):
        x, y, z = struct.unpack_from("<3f", body, offset)
        if not all(math.isfinite(value) for value in (x, y, z)):
            raise CoreGeometryError(f"non-finite POSITION at vertex {number}")
        struct.pack_into("<3f", packed_positions, number * 12, x, y, z)
        for axis, value in enumerate((x, y, z)):
            minimum[axis] = min(minimum[axis], value)
            maximum[axis] = max(maximum[axis], value)
    return stride, bytes(packed_positions), minimum, maximum


def _parse_memory_index_list(payload: bytes, vertex_count: int, expected_count: int) -> tuple[bytes, int, int, int, int]:
    if len(payload) < 12:
        raise CoreGeometryError("MemoryImageIndexList header is shorter than 12 bytes")
    _version, _param, byte_count = struct.unpack_from("<III", payload, 0)
    body = payload[12:]
    if byte_count != len(body):
        raise CoreGeometryError(f"MemoryImageIndexList byte_count={byte_count}, actual={len(body)}")
    if byte_count != expected_count * 2:
        raise CoreGeometryError(
            f"uint16 index bytes={byte_count}, expected PrimitiveGroup index_count={expected_count} × 2")
    if expected_count == 0 or expected_count % 3:
        raise CoreGeometryError(f"TriangleList index_count must be a nonzero multiple of 3, got {expected_count}")

    indices = struct.unpack("<" + "H" * expected_count, body)
    index_min, index_max = min(indices), max(indices)
    if index_max >= vertex_count:
        raise CoreGeometryError(
            f"index range [{index_min}, {index_max}] exceeds vertex_count={vertex_count}")

    repeated = 0
    for offset in range(0, expected_count, 3):
        first, second, third = indices[offset:offset + 3]
        if first == second or second == third or first == third:
            repeated += 1
    return body, index_min, index_max, repeated, expected_count // 3


def _count_zero_area_triangles(packed_positions: bytes, indices: bytes) -> int:
    """Count triangles with finite but exactly/near-exactly collapsed POSITION area.

    The tolerance is intentionally scale-relative and diagnostic only.  Such
    triangles are retained in a preview because their existence is not a file
    corruption claim; the count lets later rendered regressions distinguish a
    decoding problem from authored degenerate/shadow geometry.
    """
    count = 0
    values = struct.unpack("<" + "H" * (len(indices) // 2), indices)
    for offset in range(0, len(values), 3):
        a, b, c = values[offset:offset + 3]
        ax, ay, az = struct.unpack_from("<3f", packed_positions, a * 12)
        bx, by, bz = struct.unpack_from("<3f", packed_positions, b * 12)
        cx, cy, cz = struct.unpack_from("<3f", packed_positions, c * 12)
        abx, aby, abz = bx - ax, by - ay, bz - az
        acx, acy, acz = cx - ax, cy - ay, cz - az
        cross_x = aby * acz - abz * acy
        cross_y = abz * acx - abx * acz
        cross_z = abx * acy - aby * acx
        scale = max(
            abs(abx), abs(aby), abs(abz), abs(acx), abs(acy), abs(acz), 1.0,
        )
        if cross_x * cross_x + cross_y * cross_y + cross_z * cross_z <= (scale * scale * 1e-12) ** 2:
            count += 1
    return count


def scan_core_triangle_geometry(data: bytes, entry_name: str) -> tuple[list[DecodedCoreGroup], dict[str, Any]]:
    """Decode and validate selected merged-world triangle lists from one Cell.

    Returns packed positions/indices only in process memory alongside a safe
    metadata report.  A group with any ABI mismatch is rejected and reported;
    callers must refuse a preview whenever errors are present.
    """
    if len(data) < 12:
        raise CoreGeometryError("file is shorter than a Pure3D header")
    magic, _header_size, declared_total_size = struct.unpack_from("<III", data, 0)
    if magic != 0xFF443350:
        raise CoreGeometryError(f"only LE Pure3D is supported, magic={data[:4].hex()}")
    if declared_total_size < 12 or declared_total_size > len(data):
        raise CoreGeometryError(f"invalid declared total size {declared_total_size} for {len(data)} bytes")

    records: list[dict[str, Any]] = []
    _walk(data, 12, declared_total_size, None, 0, records)
    children = defaultdict(list)
    for index, record in enumerate(records):
        children[record["parent"]].append((index, record))

    groups: list[DecodedCoreGroup] = []
    errors: list[str] = []
    world_geometry_names: list[str] = []
    non_world_geometry_count = 0
    for geometry_index, geometry in enumerate(records):
        if geometry["type_id"] != GEOMETRY:
            continue
        try:
            geometry_name, _ = _p3d_string(geometry["payload"])
        except ValueError as exc:
            errors.append(f"Geometry#{geometry_index}: invalid name: {exc}")
            continue
        if not geometry_name.casefold().startswith(WORLD_MERGED_GEOMETRY_PREFIX):
            non_world_geometry_count += 1
            continue
        world_geometry_names.append(geometry_name)
        group_ordinal = 0
        for group_index, group_record in children[geometry_index]:
            if group_record["type_id"] != PRIMITIVE_GROUP:
                continue
            group_ordinal += 1
            label = f"{geometry_name} group#{group_ordinal} (record {group_index})"
            try:
                group = _parse_primitive_group(group_record["payload"])
                if group["primitive_type"] != TRIANGLE_LIST:
                    raise CoreGeometryError(
                        f"only PrimitiveGroup TriangleList=0 is accepted, got {group['primitive_type']}")
                direct = children[group_index]
                vertex_lists = [record for _index, record in direct if record["type_id"] == MEMORY_VERTEX_LIST]
                index_lists = [record for _index, record in direct if record["type_id"] == MEMORY_INDEX_LIST]
                declarations = [record for _index, record in direct if record["type_id"] == MEMORY_VERTEX_DESCRIPTION]
                if len(vertex_lists) != 1 or len(index_lists) != 1 or len(declarations) != 1:
                    raise CoreGeometryError(
                        "expected exactly one MemoryImageVertexList / MemoryImageIndexList / "
                        f"MemoryImageVertexDescription, got {len(vertex_lists)} / {len(index_lists)} / {len(declarations)}")
                declaration = _fingerprint_vertex_description(declarations[0]["payload"])
                stride, positions, position_min, position_max = _parse_memory_vertex_list(
                    vertex_lists[0]["payload"], group["vertex_count"], declaration)
                indices, index_min, index_max, repeated, _triangles = _parse_memory_index_list(
                    index_lists[0]["payload"], group["vertex_count"], group["index_count"])
                zero_area = _count_zero_area_triangles(positions, indices)
                groups.append(DecodedCoreGroup(
                    entry_name=entry_name,
                    geometry_name=geometry_name,
                    group_ordinal=group_ordinal,
                    shader_name=group["shader_name"],
                    layout_sha256=declaration["layout_sha256"],
                    vertex_stride=stride,
                    positions=positions,
                    indices=indices,
                    vertex_count=group["vertex_count"],
                    index_count=group["index_count"],
                    position_min=position_min,
                    position_max=position_max,
                    index_min=index_min,
                    index_max=index_max,
                    repeated_index_triangle_count=repeated,
                    zero_area_triangle_count=zero_area,
                ))
            except (CoreGeometryError, ValueError, struct.error) as exc:
                errors.append(f"{label}: {exc}")

    report_groups = [
        {
            "entry_name": item.entry_name,
            "geometry_name": item.geometry_name,
            "group_ordinal": item.group_ordinal,
            "shader_name": item.shader_name,
            "layout_sha256": item.layout_sha256,
            "vertex_stride": item.vertex_stride,
            "vertex_count": item.vertex_count,
            "index_count": item.index_count,
            "triangle_count": item.triangle_count,
            "position_min": item.position_min,
            "position_max": item.position_max,
            "index_min": item.index_min,
            "index_max": item.index_max,
            "repeated_index_triangle_count": item.repeated_index_triangle_count,
            "zero_area_triangle_count": item.zero_area_triangle_count,
        }
        for item in groups
    ]
    mins = [item.position_min for item in groups]
    maxs = [item.position_max for item in groups]
    report = {
        "entry_name": entry_name,
        "decompressed_size": len(data),
        "declared_total_size": declared_total_size,
        "scope": "mergedDrawableRoot* Geometry only; TriangleList positions and uint16 indices only",
        "skipped_non_world_geometry_count": non_world_geometry_count,
        "world_geometry_names": world_geometry_names,
        "accepted_group_count": len(groups),
        "accepted_vertex_count": sum(item.vertex_count for item in groups),
        "accepted_index_count": sum(item.index_count for item in groups),
        "accepted_triangle_count": sum(item.triangle_count for item in groups),
        "vertex_stride_counts": {str(key): value for key, value in sorted(Counter(item.vertex_stride for item in groups).items())},
        "position_min": [min(item[axis] for item in mins) for axis in range(3)] if mins else None,
        "position_max": [max(item[axis] for item in maxs) for axis in range(3)] if maxs else None,
        "repeated_index_triangle_count": sum(item.repeated_index_triangle_count for item in groups),
        "zero_area_triangle_count": sum(item.zero_area_triangle_count for item in groups),
        "errors": errors,
        "groups": report_groups,
    }
    return groups, report


def _fetch_raw(base_url: str, rcf_path: str, entry_name: str, timeout: int) -> bytes:
    query = urllib.parse.urlencode({"path": rcf_path, "name": entry_name, "raw": "1"})
    url = base_url.rstrip("/") + "/api/rcf_entry?" + query
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not fetch {entry_name!r}: {exc}") from exc


def _atomic_text(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".core_geometry_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_json(path: str, value: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _preview_data(groups: Iterable[DecodedCoreGroup]) -> dict[str, Any]:
    packed = []
    for item in groups:
        packed.append({
            "entry": item.entry_name,
            "geometry": item.geometry_name,
            "group": item.group_ordinal,
            "vertices": item.vertex_count,
            "triangles": item.triangle_count,
            "p": base64.b64encode(item.positions).decode("ascii"),
            "i": base64.b64encode(item.indices).decode("ascii"),
        })
    return {"meshes": packed}


def _build_preview_html(groups: list[DecodedCoreGroup], report: dict[str, Any]) -> str:
    """Build a self-contained, untextured WebGL viewport from validated data."""
    safe_report = {
        key: report[key] for key in (
            "scope", "accepted_group_count", "accepted_vertex_count", "accepted_triangle_count",
            "position_min", "position_max", "vertex_stride_counts", "repeated_index_triangle_count",
            "zero_area_triangle_count", "errors", "world_geometry_names",
        )
    }
    data = json.dumps(_preview_data(groups), separators=(",", ":"), ensure_ascii=False)
    diagnostics = html.escape(json.dumps(safe_report, ensure_ascii=False, indent=2))
    title = html.escape(", ".join(sorted({item.entry_name.rsplit("\\", 1)[-1] for item in groups})))
    template = r"""<!doctype html>
<html lang="zh-Hans"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Manhattan core triangle diagnostic</title>
<style>
:root{color-scheme:dark;--bg:#071018;--panel:#0c1a25;--line:#284256;--ink:#dbe9f2;--muted:#8da6b6;--cyan:#44d5e9;--orange:#fdac5f}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--ink);font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;display:grid;grid-template-columns:minmax(0,1fr) 345px}main{min-height:100vh;position:relative}canvas{width:100%;height:100vh;display:block;cursor:grab;background:radial-gradient(ellipse at 50% 40%,#142c3d 0,#09141d 45%,#04080c 100%)}canvas:active{cursor:grabbing}.hud{position:absolute;top:16px;left:16px;pointer-events:none}.hud h1{font:600 15px/1.2 system-ui,sans-serif;letter-spacing:.04em;margin:0 0 8px}.hud p{margin:0;color:var(--muted);max-width:620px}.hud b{color:var(--orange)}aside{border-left:1px solid var(--line);background:var(--panel);padding:20px;overflow:auto;max-height:100vh}h2{font:600 13px/1.2 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.12em;margin:0 0 10px;color:var(--cyan)}p{margin:0 0 16px;color:var(--muted)}button{border:1px solid #3e6278;color:var(--ink);background:#102b39;border-radius:5px;padding:8px 10px;font:inherit;cursor:pointer;margin:0 6px 12px 0}button:hover{background:#164052}.legend{display:flex;align-items:center;gap:8px;color:var(--muted);margin:4px 0}.dot{width:11px;height:11px;border-radius:50%;background:var(--orange)}pre{white-space:pre-wrap;word-break:break-word;padding:12px;margin:0;border:1px solid var(--line);border-radius:6px;background:#07131d;color:#b8ceda;font-size:11px}small{color:var(--muted);display:block;margin-top:18px}@media(max-width:780px){body{display:block}aside{max-height:none;border-left:0;border-top:1px solid var(--line)}canvas{height:60vh}}
</style></head><body><main><canvas id="c"></canvas><div class="hud"><h1>曼哈顿 · core triangle diagnostic</h1><p><b>真实 POSITION + uint16 index</b>，仅 mergedDrawableRoot*；拖拽旋转，滚轮缩放。无纹理、无 UV/normal 语义猜测。</p></div></main><aside><h2>选中范围</h2><p>__TITLE__</p><button id="reset">重置视角</button><button id="wire">线框：关</button><div class="legend"><i class="dot"></i>按 PrimitiveGroup 稳定着色；深度测试已开启。</div><h2 style="margin-top:23px">严格验证记录</h2><pre>__DIAGNOSTICS__</pre><small>这是私有诊断预览，导出的 HTML 含所选 Cell 的派生 POSITION/索引数据；不要提交或分发。通过本项只能证明静态核心网格的坐标与三角连接，不能证明纹理、UV、normal 或材质映射。</small></aside>
<script>
const DATA=__DATA__;
const canvas=document.getElementById('c'),gl=canvas.getContext('webgl',{antialias:true});
if(!gl){document.body.innerHTML='<p style="padding:2rem">浏览器未提供 WebGL；请在普通 Chrome/Edge/Firefox 中打开此私有诊断页面。</p>';throw Error('WebGL unavailable')}
const vs=`attribute vec3 p;uniform mat4 mvp;uniform vec3 tint;varying vec3 c;void main(){gl_Position=mvp*vec4(p,1.);c=tint;}`;
const fs=`precision mediump float;varying vec3 c;void main(){gl_FragColor=vec4(c,1.);}`;
function shader(type,src){const s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s}
const program=gl.createProgram();gl.attachShader(program,shader(gl.VERTEX_SHADER,vs));gl.attachShader(program,shader(gl.FRAGMENT_SHADER,fs));gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));gl.useProgram(program);
const posLoc=gl.getAttribLocation(program,'p'),mvpLoc=gl.getUniformLocation(program,'mvp'),tintLoc=gl.getUniformLocation(program,'tint');
function bytes(s){const raw=atob(s),out=new Uint8Array(raw.length);for(let n=0;n<raw.length;n++)out[n]=raw.charCodeAt(n);return out}
const meshes=DATA.meshes.map((item,n)=>{const p=gl.createBuffer(),i=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,p);gl.bufferData(gl.ARRAY_BUFFER,bytes(item.p),gl.STATIC_DRAW);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,i);const idx=bytes(item.i);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,idx,gl.STATIC_DRAW);const hue=(n*0.61803398875)%1, v=.60, a=hue*6.2831853;return {...item,p,i,count:idx.byteLength/2,color:[v+.27*Math.cos(a),v+.22*Math.cos(a-2.1),v+.18*Math.cos(a+2.1)]}});
const all=[];for(const item of meshes){const b=bytes(item.p);for(let k=0;k<b.byteLength;k+=12){const dv=new DataView(b.buffer,k,12);all.push(dv.getFloat32(0,true),dv.getFloat32(4,true),dv.getFloat32(8,true))}}
const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(let k=0;k<all.length;k+=3)for(let a=0;a<3;a++){lo[a]=Math.min(lo[a],all[k+a]);hi[a]=Math.max(hi[a],all[k+a])}const target=lo.map((x,a)=>(x+hi[a])/2),extent=Math.max(hi[0]-lo[0],hi[1]-lo[1],hi[2]-lo[2]),baseDistance=Math.max(extent*1.2,8);
let yaw=-2.35,pitch=.56,distance=baseDistance,wire=false,drag=null;
function m4(){return new Float32Array(16)}function identity(){const m=m4();m[0]=m[5]=m[10]=m[15]=1;return m}function mul(a,b){const out=m4();for(let r=0;r<4;r++)for(let c=0;c<4;c++)out[c*4+r]=a[r]*b[c*4]+a[4+r]*b[c*4+1]+a[8+r]*b[c*4+2]+a[12+r]*b[c*4+3];return out}function perspective(fov,aspect,near,far){const f=1/Math.tan(fov/2),m=m4();m[0]=f/aspect;m[5]=f;m[10]=(far+near)/(near-far);m[11]=-1;m[14]=2*far*near/(near-far);return m}function lookAt(e,c){let zx=e[0]-c[0],zy=e[1]-c[1],zz=e[2]-c[2],zl=Math.hypot(zx,zy,zz);zx/=zl;zy/=zl;zz/=zl;let xx=zy,xy=-zx,xz=0,xl=Math.hypot(xx,xy,xz);xx/=xl;xy/=xl;xz/=xl;const yx=yy=yz=0;const y0=zy*xz-zz*xy,y1=zz*xx-zx*xz,y2=zx*xy-zy*xx,m=identity();m[0]=xx;m[1]=y0;m[2]=zx;m[4]=xy;m[5]=y1;m[6]=zy;m[8]=xz;m[9]=y2;m[10]=zz;m[12]=-(xx*e[0]+xy*e[1]+xz*e[2]);m[13]=-(y0*e[0]+y1*e[1]+y2*e[2]);m[14]=-(zx*e[0]+zy*e[1]+zz*e[2]);return m}
function draw(){const dpr=Math.min(devicePixelRatio||1,2),w=Math.floor(canvas.clientWidth*dpr),h=Math.floor(canvas.clientHeight*dpr);if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}gl.viewport(0,0,w,h);gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);gl.clearColor(.015,.035,.052,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);const cp=Math.cos(pitch),eye=[target[0]+distance*Math.cos(yaw)*cp,target[1]+distance*Math.sin(pitch),target[2]+distance*Math.sin(yaw)*cp];gl.uniformMatrix4fv(mvpLoc,false,mul(perspective(.77,w/h,Math.max(.01,extent*.0001),extent*20+100),lookAt(eye,target)));gl.enableVertexAttribArray(posLoc);for(const item of meshes){gl.bindBuffer(gl.ARRAY_BUFFER,item.p);gl.vertexAttribPointer(posLoc,3,gl.FLOAT,false,0,0);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,item.i);gl.uniform3fv(tintLoc,item.color);gl.drawElements(wire?gl.LINES:gl.TRIANGLES,item.count,gl.UNSIGNED_SHORT,0)}requestAnimationFrame(draw)}requestAnimationFrame(draw);
canvas.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!drag)return;yaw+=(e.clientX-drag[0])*.008;pitch=Math.max(-1.5,Math.min(1.5,pitch+(e.clientY-drag[1])*.008));drag=[e.clientX,e.clientY]});canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('wheel',e=>{e.preventDefault();distance=Math.max(baseDistance*.08,Math.min(baseDistance*8,distance*Math.exp(e.deltaY*.001)))},{passive:false});document.getElementById('reset').onclick=()=>{yaw=-2.35;pitch=.56;distance=baseDistance};document.getElementById('wire').onclick=e=>{wire=!wire;e.target.textContent='线框：'+(wire?'开':'关')};
</script></body></html>"""
    return (template
            .replace("__TITLE__", title)
            .replace("__DATA__", data)
            .replace("__DIAGNOSTICS__", diagnostics))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--rcf-path", required=True)
    parser.add_argument("--entry", action="append", required=True, help="base Cell entry; repeat for a small selected set")
    parser.add_argument("--out", required=True, help="metadata-only JSON report output")
    parser.add_argument("--preview-html", help="explicit private WebGL diagnostic preview output")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    all_groups: list[DecodedCoreGroup] = []
    reports = []
    for entry_name in args.entry:
        print(f"validating core triangle structure: {entry_name}")
        groups, report = scan_core_triangle_geometry(
            _fetch_raw(args.base_url, args.rcf_path, entry_name, args.timeout), entry_name)
        reports.append(report)
        all_groups.extend(groups)
        print(
            f"  accepted {report['accepted_group_count']} groups / {report['accepted_triangle_count']} triangles; "
            f"errors={len(report['errors'])}")

    output = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "scope": "metadata only: strict mergedDrawableRoot TriangleList POSITION/index validation; no asset payload",
        "source": {"base_url": args.base_url.rstrip("/"), "rcf_path": args.rcf_path},
        "entries": reports,
        "all_entries_error_count": sum(len(report["errors"]) for report in reports),
    }
    _atomic_json(args.out, output)
    if output["all_entries_error_count"]:
        raise SystemExit(
            f"refusing preview: {output['all_entries_error_count']} strict core-geometry errors; see {args.out}")
    if args.preview_html:
        if not all_groups:
            raise SystemExit(f"refusing preview: no validated merged-world triangle groups; see {args.out}")
        _atomic_text(args.preview_html, _build_preview_html(all_groups, {
            "scope": output["scope"],
            "accepted_group_count": sum(report["accepted_group_count"] for report in reports),
            "accepted_vertex_count": sum(report["accepted_vertex_count"] for report in reports),
            "accepted_triangle_count": sum(report["accepted_triangle_count"] for report in reports),
            "position_min": [min(report["position_min"][axis] for report in reports) for axis in range(3)],
            "position_max": [max(report["position_max"][axis] for report in reports) for axis in range(3)],
            "vertex_stride_counts": dict(Counter(
                stride for report in reports for stride, count in report["vertex_stride_counts"].items() for _ in range(count))),
            "repeated_index_triangle_count": sum(report["repeated_index_triangle_count"] for report in reports),
            "zero_area_triangle_count": sum(report["zero_area_triangle_count"] for report in reports),
            "errors": [],
            "world_geometry_names": [name for report in reports for name in report["world_geometry_names"]],
        }))
        print(f"wrote private diagnostic preview {args.preview_html}")
    print(f"wrote metadata-only report {args.out}")


if __name__ == "__main__":
    main()
