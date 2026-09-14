#!/usr/bin/env python3
"""Render two explicit static-vertex UV candidates against one selected Cell texture.

This private diagnostic is purposely narrow.  It accepts one
``mergedDrawableRoot*`` PrimitiveGroup already compatible with the strict core
triangle decoder, extracts two caller-specified declared offsets as float32x2,
and shows the *unchanged source values* side by side against a selected DXT
texture embedded in the same Cell.  A V-flip control is available only as a
visual experiment; neither result is automatically promoted to a named UV
semantic.

The command fetches the P3D only into memory.  Its JSON report contains only
metadata/ranges.  The explicitly requested preview HTML contains derived
POSITION/index/two candidate streams and a decoded texture image, so it is a
private inspection output and must not be committed or distributed.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import io
import json
import math
import os
import struct
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from probe_static_geometry import (
    GEOMETRY,
    MEMORY_VERTEX_DESCRIPTION,
    MEMORY_VERTEX_LIST,
    PRIMITIVE_GROUP,
    _fingerprint_vertex_description,
    _parse_primitive_group,
    _p3d_string,
    _walk,
)
from export_static_geometry_diagnostic import (
    MEMORY_INDEX_LIST,
    CoreGeometryError,
    _parse_memory_index_list,
    _parse_memory_vertex_list,
)

TEXTURE = 0x00019000
TEXTURE_DDS = 0x00019006
IMAGE_DATA = 0x00019002


@dataclass(frozen=True)
class CandidateMesh:
    entry_name: str
    geometry_name: str
    group_ordinal: int
    shader_name: str
    layout_sha256: str
    vertex_stride: int
    positions: bytes
    indices: bytes
    uv_streams: tuple[tuple[int, str, bytes, list[float], list[float]], ...]
    position_min: list[float]
    position_max: list[float]
    triangle_count: int


def _children(records: list[dict[str, Any]]) -> dict[int | None, list[tuple[int, dict[str, Any]]]]:
    output: dict[int | None, list[tuple[int, dict[str, Any]]]] = {}
    for index, record in enumerate(records):
        output.setdefault(record["parent"], []).append((index, record))
    return output


def _records(data: bytes) -> tuple[list[dict[str, Any]], dict[int | None, list[tuple[int, dict[str, Any]]]]]:
    if len(data) < 12:
        raise ValueError("file shorter than a Pure3D header")
    magic, _header_size, total_size = struct.unpack_from("<III", data, 0)
    if magic != 0xFF443350:
        raise ValueError(f"only LE Pure3D is implemented, magic={data[:4].hex()}")
    if total_size < 12 or total_size > len(data):
        raise ValueError(f"invalid declared total size {total_size} for {len(data)} bytes")
    records: list[dict[str, Any]] = []
    _walk(data, 12, total_size, None, 0, records)
    return records, _children(records)


def _extract_uv_stream(vertex_list_payload: bytes, vertex_count: int, stride: int, declaration: dict[str, Any], offset: int) -> tuple[str, bytes, list[float], list[float]]:
    matches = [attribute for attribute in declaration["attributes"] if attribute["source"] == 0 and attribute["offset"] == offset]
    if len(matches) != 1:
        raise CoreGeometryError(f"expected one declared source-0 attribute at offset {offset}, found {len(matches)}")
    if offset < 0 or offset + 8 > stride:
        raise CoreGeometryError(f"float32x2 candidate offset {offset} does not fit stride {stride}")
    body = vertex_list_payload[12:]
    packed = bytearray(vertex_count * 8)
    minimum = [math.inf, math.inf]
    maximum = [-math.inf, -math.inf]
    for number in range(vertex_count):
        u, v = struct.unpack_from("<2f", body, number * stride + offset)
        if not (math.isfinite(u) and math.isfinite(v)):
            raise CoreGeometryError(f"candidate offset {offset} has a non-finite float2 at vertex {number}")
        struct.pack_into("<2f", packed, number * 8, u, v)
        minimum[0] = min(minimum[0], u)
        minimum[1] = min(minimum[1], v)
        maximum[0] = max(maximum[0], u)
        maximum[1] = max(maximum[1], v)
    return matches[0]["semantic_hash"], bytes(packed), minimum, maximum


def extract_candidate_mesh(data: bytes, entry_name: str, geometry_name: str, group_ordinal: int, offsets: list[int]) -> CandidateMesh:
    """Validate one selected world-core group and retain two candidate float2 streams."""
    if len(offsets) != 2 or offsets[0] == offsets[1]:
        raise ValueError("provide exactly two distinct --uv-offset values")
    records, children = _records(data)
    geometry_index = None
    for index, record in enumerate(records):
        if record["type_id"] != GEOMETRY:
            continue
        name, _ = _p3d_string(record["payload"])
        if name == geometry_name:
            geometry_index = index
            break
    if geometry_index is None:
        raise CoreGeometryError(f"Geometry {geometry_name!r} was not found")
    if not geometry_name.casefold().startswith("mergeddrawableroot"):
        raise CoreGeometryError("refusing a local-space Geometry; name must begin mergedDrawableRoot")

    primitive_groups = [(index, record) for index, record in children[geometry_index] if record["type_id"] == PRIMITIVE_GROUP]
    if group_ordinal < 1 or group_ordinal > len(primitive_groups):
        raise CoreGeometryError(f"group ordinal {group_ordinal} is outside 1..{len(primitive_groups)}")
    group_index, group_record = primitive_groups[group_ordinal - 1]
    parsed = _parse_primitive_group(group_record["payload"])
    if parsed["primitive_type"] != 0:
        raise CoreGeometryError(f"only TriangleList=0 is supported, got primitive_type={parsed['primitive_type']}")
    direct = children[group_index]
    vertex_lists = [record for _index, record in direct if record["type_id"] == MEMORY_VERTEX_LIST]
    index_lists = [record for _index, record in direct if record["type_id"] == MEMORY_INDEX_LIST]
    declarations = [record for _index, record in direct if record["type_id"] == MEMORY_VERTEX_DESCRIPTION]
    if len(vertex_lists) != 1 or len(index_lists) != 1 or len(declarations) != 1:
        raise CoreGeometryError(
            "expected one vertex list / index list / vertex declaration, got "
            f"{len(vertex_lists)} / {len(index_lists)} / {len(declarations)}")
    declaration = _fingerprint_vertex_description(declarations[0]["payload"])
    stride, positions, pos_min, pos_max = _parse_memory_vertex_list(vertex_lists[0]["payload"], parsed["vertex_count"], declaration)
    indices, _index_min, _index_max, _repeated, triangles = _parse_memory_index_list(
        index_lists[0]["payload"], parsed["vertex_count"], parsed["index_count"])
    streams = tuple(
        (offset, *_extract_uv_stream(vertex_lists[0]["payload"], parsed["vertex_count"], stride, declaration, offset))
        for offset in offsets
    )
    return CandidateMesh(
        entry_name=entry_name,
        geometry_name=geometry_name,
        group_ordinal=group_ordinal,
        shader_name=parsed["shader_name"],
        layout_sha256=declaration["layout_sha256"],
        vertex_stride=stride,
        positions=positions,
        indices=indices,
        uv_streams=streams,
        position_min=pos_min,
        position_max=pos_max,
        triangle_count=triangles,
    )


def _descendants(root: int, children: dict[int | None, list[tuple[int, dict[str, Any]]]]) -> list[tuple[int, dict[str, Any]]]:
    result = []
    todo = list(reversed(children.get(root, [])))
    while todo:
        index, record = todo.pop()
        result.append((index, record))
        todo.extend(reversed(children.get(index, [])))
    return result


def _parse_texture_dds_header(payload: bytes) -> tuple[str, int, int, int, str]:
    name, offset = _p3d_string(payload)
    if offset + 28 != len(payload):
        raise ValueError(f"TextureDDS {name!r} expected 28 header bytes after name, got {len(payload) - offset}")
    _version, width, height, _unknown4, _unknown5, mip_count, algorithm = struct.unpack_from("<6I4s", payload, offset)
    return name, width, height, mip_count, algorithm.decode("ascii", "replace")


def _decode_local_texture_to_png(records: list[dict[str, Any]], children: dict[int | None, list[tuple[int, dict[str, Any]]]], texture_name: str) -> tuple[str, int, int, bytes]:
    try:
        import texture2ddecoder
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - environment setup branch
        raise RuntimeError("private UV preview needs Pillow and texture2ddecoder; install the latter with pip") from exc

    texture_index = None
    for index, record in enumerate(records):
        if record["type_id"] == TEXTURE and _p3d_string(record["payload"])[0] == texture_name:
            texture_index = index
            break
    if texture_index is None:
        raise ValueError(f"Texture {texture_name!r} is not defined in this Cell")
    dds = next((record for _index, record in _descendants(texture_index, children) if record["type_id"] == TEXTURE_DDS), None)
    image = next((record for _index, record in _descendants(texture_index, children) if record["type_id"] == IMAGE_DATA), None)
    if dds is None or image is None:
        raise ValueError(f"Texture {texture_name!r} is missing TextureDDS or ImageData")
    dds_name, width, height, mip_count, algorithm = _parse_texture_dds_header(dds["payload"])
    if dds_name != texture_name:
        raise ValueError(f"TextureDDS name {dds_name!r} does not match Texture {texture_name!r}")
    image_payload = image["payload"]
    if len(image_payload) < 132:
        raise ValueError(f"Texture {texture_name!r} ImageData is shorter than DDS header framing")
    declared_bytes = struct.unpack_from("<I", image_payload, 0)[0]
    if declared_bytes != len(image_payload) - 4 or image_payload[4:8] != b"DDS ":
        raise ValueError(f"Texture {texture_name!r} ImageData is not expected count-prefixed DDS data")
    compressed = image_payload[132:]
    # texture2ddecoder exposes BC1 and BC3 in the installed build.  Do not
    # claim that DXT3 is supported merely because it is conceptually BC2;
    # its decoder entry point is absent here and must be added/validated
    # separately before a DXT3 material preview is allowed.
    if algorithm == "DXT1":
        bgra = texture2ddecoder.decode_bc1(compressed, width, height)
    elif algorithm == "DXT5":
        bgra = texture2ddecoder.decode_bc3(compressed, width, height)
    else:
        raise ValueError(f"Texture {texture_name!r} uses unsupported DDS algorithm {algorithm!r}")
    if len(bgra) != width * height * 4:
        raise ValueError(f"decoder returned {len(bgra)} bytes for {width}×{height} RGBA")
    rgba = bytearray(bgra)
    rgba[0::4], rgba[2::4] = bgra[2::4], bgra[0::4]
    stream = io.BytesIO()
    Image.frombytes("RGBA", (width, height), bytes(rgba)).save(stream, format="PNG")
    return algorithm, width, height, stream.getvalue()


def build_metadata_report(mesh: CandidateMesh, texture_name: str, algorithm: str, texture_width: int, texture_height: int) -> dict[str, Any]:
    return {
        "entry_name": mesh.entry_name,
        "scope": "one strict mergedDrawableRoot TriangleList group, two caller-selected float32x2 attribute candidates, one local DXT texture",
        "geometry_name": mesh.geometry_name,
        "primitive_group_ordinal": mesh.group_ordinal,
        "shader_name": mesh.shader_name,
        "layout_sha256": mesh.layout_sha256,
        "vertex_stride": mesh.vertex_stride,
        "vertex_count": len(mesh.positions) // 12,
        "index_count": len(mesh.indices) // 2,
        "triangle_count": mesh.triangle_count,
        "position_min": mesh.position_min,
        "position_max": mesh.position_max,
        "uv_candidates": [
            {"offset": offset, "semantic_hash": signature, "float2_min": minimum, "float2_max": maximum}
            for offset, signature, _stream, minimum, maximum in mesh.uv_streams
        ],
        "texture": {"name": texture_name, "algorithm": algorithm, "width": texture_width, "height": texture_height},
        "conclusion_limit": "visual candidate comparison only; neither offset is automatically named UV0/UV1 and no V-axis convention is assumed",
    }


def _preview_html(mesh: CandidateMesh, report: dict[str, Any], png: bytes) -> str:
    uv_data = [
        {"offset": offset, "hash": signature, "data": base64.b64encode(stream).decode("ascii")}
        for offset, signature, stream, _minimum, _maximum in mesh.uv_streams
    ]
    data = json.dumps({
        "positions": base64.b64encode(mesh.positions).decode("ascii"),
        "indices": base64.b64encode(mesh.indices).decode("ascii"),
        "uv": uv_data,
        "image": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
    }, separators=(",", ":"))
    diagnostics = html.escape(json.dumps(report, ensure_ascii=False, indent=2))
    template = r"""<!doctype html><html lang="zh-Hans"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Static UV candidate diagnostic</title><style>
:root{color-scheme:dark;--bg:#061017;--panel:#0b1923;--line:#294656;--ink:#e1edf3;--muted:#8da6b6;--lime:#a7e276;--orange:#ffad62}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;grid-template-columns:minmax(0,1fr) 350px;background:var(--bg);color:var(--ink);font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}main{position:relative;min-height:100vh}canvas{display:block;width:100%;height:100vh;cursor:grab;background:radial-gradient(ellipse at 50% 45%,#173545,#07121b 63%,#030709)}canvas:active{cursor:grabbing}.heading{position:absolute;left:18px;top:16px;pointer-events:none}.heading h1{font:600 15px/1.2 system-ui,sans-serif;letter-spacing:.04em;margin:0 0 8px}.heading p{margin:0;color:var(--muted);max-width:700px}.label{position:absolute;top:82px;width:calc(50% - 24px);padding:8px 10px;border:1px solid #466575;background:#07131bdd;color:var(--lime);font-size:11px}.left{left:18px}.right{left:calc(50% + 6px)}aside{border-left:1px solid var(--line);background:var(--panel);padding:20px;overflow:auto;max-height:100vh}h2{font:600 13px/1.2 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.12em;margin:0 0 10px;color:var(--lime)}p{margin:0 0 16px;color:var(--muted)}button{border:1px solid #466575;background:#102d3a;color:var(--ink);border-radius:5px;padding:8px 10px;font:inherit;cursor:pointer;margin:0 6px 10px 0}button:hover{background:#194557}button.active{border-color:var(--orange);color:var(--orange)}pre{white-space:pre-wrap;word-break:break-word;padding:12px;margin:0;border:1px solid var(--line);border-radius:6px;background:#06121b;color:#b8ceda;font-size:11px}small{display:block;color:var(--muted);margin-top:18px}@media(max-width:800px){body{display:block}canvas{height:61vh}.label{top:90px}aside{max-height:none;border-left:0;border-top:1px solid var(--line)}}
</style></head><body><main><canvas id="c"></canvas><div class="heading"><h1>静态材质 UV 候选对照</h1><p>同一严格验证的 TriangleList 与同一解码 DXT 图像。拖拽：旋转；滚轮：缩放。默认是两个 attribute 的<strong>原始 float2</strong>，未翻转。</p></div><div class="label left">候选 A · hash __HASH_A__ · offset __OFFSET_A__ · 原始值</div><div class="label right">候选 B · hash __HASH_B__ · offset __OFFSET_B__ · 原始值</div></main><aside><h2>比较控制</h2><p>V 翻转只是现场对照开关，不会把任何结果写回解析规则。</p><button id="raw" class="active">原始 V</button><button id="flip">V → 1 − V</button><button id="reset">重置视角</button><h2 style="margin-top:17px">metadata-only 记录</h2><pre>__REPORT__</pre><small>私有诊断 HTML 内含所选 group 的派生 POSITION/index/候选 float2 以及解码图像；不得提交或分发。只有清晰的渲染证据才能决定哪个候选对应哪个采样槽位；normal、tangent、vertex color 和 shader 公式均不在本页作结论。</small></aside><script>
const DATA=__DATA__,canvas=document.getElementById('c'),gl=canvas.getContext('webgl',{antialias:true});if(!gl){document.body.innerHTML='<p style="padding:2rem">浏览器未提供 WebGL；请用普通 Chrome、Edge 或 Firefox 打开。</p>';throw Error('WebGL unavailable')}
const vs=`attribute vec3 p;attribute vec2 uv;uniform mat4 mvp;uniform float flipV;varying vec2 t;void main(){gl_Position=mvp*vec4(p,1.);t=vec2(uv.x,flipV>.5?1.-uv.y:uv.y);}`;const fs=`precision mediump float;uniform sampler2D tex;varying vec2 t;void main(){gl_FragColor=vec4(texture2D(tex,t).rgb,1.);}`;function sh(t,s){let q=gl.createShader(t);gl.shaderSource(q,s);gl.compileShader(q);if(!gl.getShaderParameter(q,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(q));return q}const pr=gl.createProgram();gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs));gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs));gl.linkProgram(pr);if(!gl.getProgramParameter(pr,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(pr));gl.useProgram(pr);const pl=gl.getAttribLocation(pr,'p'),ul=gl.getAttribLocation(pr,'uv'),ml=gl.getUniformLocation(pr,'mvp'),fl=gl.getUniformLocation(pr,'flipV');function b64(s){const r=atob(s),a=new Uint8Array(r.length);for(let i=0;i<r.length;i++)a[i]=r.charCodeAt(i);return a}function buf(target,src){const b=gl.createBuffer();gl.bindBuffer(target,b);gl.bufferData(target,src,gl.STATIC_DRAW);return b}const pos=buf(gl.ARRAY_BUFFER,b64(DATA.positions)),idx=buf(gl.ELEMENT_ARRAY_BUFFER,b64(DATA.indices)),uv=DATA.uv.map(x=>buf(gl.ARRAY_BUFFER,b64(x.data))),count=b64(DATA.indices).byteLength/2;const posBytes=b64(DATA.positions),dv=new DataView(posBytes.buffer),lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(let i=0;i<posBytes.byteLength;i+=12)for(let a=0;a<3;a++){const v=dv.getFloat32(i+a*4,true);lo[a]=Math.min(lo[a],v);hi[a]=Math.max(hi[a],v)}const target=lo.map((v,a)=>(v+hi[a])/2),extent=Math.max(hi[0]-lo[0],hi[1]-lo[1],hi[2]-lo[2]),base=Math.max(extent*1.22,3);let yaw=-2.35,pitch=.5,dist=base,flipV=0,drag=null;
function norm(v){const n=Math.hypot(...v);return v.map(x=>x/n)}function cross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]}function mul(a,b){const o=new Float32Array(16);for(let r=0;r<4;r++)for(let c=0;c<4;c++)o[c*4+r]=a[r]*b[c*4]+a[4+r]*b[c*4+1]+a[8+r]*b[c*4+2]+a[12+r]*b[c*4+3];return o}function persp(f,a,n,z){const q=1/Math.tan(f/2),m=new Float32Array(16);m[0]=q/a;m[5]=q;m[10]=(z+n)/(n-z);m[11]=-1;m[14]=2*z*n/(n-z);return m}function look(e,c){const f=norm(c.map((v,i)=>v-e[i])),s=norm(cross(f,[0,1,0])),u=cross(s,f),m=new Float32Array(16);m[0]=s[0];m[1]=u[0];m[2]=-f[0];m[4]=s[1];m[5]=u[1];m[6]=-f[1];m[8]=s[2];m[9]=u[2];m[10]=-f[2];m[12]=-dot(s,e);m[13]=-dot(u,e);m[14]=dot(f,e);m[15]=1;return m}const tex=gl.createTexture(),img=new Image();img.onload=()=>{gl.bindTexture(gl.TEXTURE_2D,tex);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,false);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,img);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.REPEAT);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.REPEAT);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.generateMipmap(gl.TEXTURE_2D);requestAnimationFrame(draw)};img.src=DATA.image;
function draw(){const d=Math.min(devicePixelRatio||1,2),w=Math.floor(canvas.clientWidth*d),h=Math.floor(canvas.clientHeight*d);if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}gl.enable(gl.DEPTH_TEST);gl.disable(gl.CULL_FACE);gl.clearColor(.018,.045,.065,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);const cp=Math.cos(pitch),eye=[target[0]+dist*Math.cos(yaw)*cp,target[1]+dist*Math.sin(pitch),target[2]+dist*Math.sin(yaw)*cp];const matrix=look(eye,target);gl.useProgram(pr);gl.uniform1f(fl,flipV);gl.bindBuffer(gl.ARRAY_BUFFER,pos);gl.enableVertexAttribArray(pl);gl.vertexAttribPointer(pl,3,gl.FLOAT,false,0,0);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,idx);for(let pane=0;pane<2;pane++){gl.viewport(pane*w/2,0,w/2,h);gl.uniformMatrix4fv(ml,false,mul(persp(.77,(w/2)/h,Math.max(.01,extent*.0001),extent*20+100),matrix));gl.bindBuffer(gl.ARRAY_BUFFER,uv[pane]);gl.enableVertexAttribArray(ul);gl.vertexAttribPointer(ul,2,gl.FLOAT,false,0,0);gl.drawElements(gl.TRIANGLES,count,gl.UNSIGNED_SHORT,0)}requestAnimationFrame(draw)}
canvas.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!drag)return;yaw+=(e.clientX-drag[0])*.008;pitch=Math.max(-1.48,Math.min(1.48,pitch+(e.clientY-drag[1])*.008));drag=[e.clientX,e.clientY]});canvas.addEventListener('pointerup',()=>drag=null);canvas.addEventListener('wheel',e=>{e.preventDefault();dist=Math.max(base*.08,Math.min(base*8,dist*Math.exp(e.deltaY*.001)))},{passive:false});const rawButton=document.getElementById('raw'),flipButton=document.getElementById('flip');rawButton.onclick=()=>{flipV=0;rawButton.classList.add('active');flipButton.classList.remove('active')};flipButton.onclick=()=>{flipV=1;flipButton.classList.add('active');rawButton.classList.remove('active')};document.getElementById('reset').onclick=()=>{yaw=-2.35;pitch=.5;dist=base};
</script></body></html>"""
    first, second = uv_data
    return (template
            .replace("__HASH_A__", html.escape(first["hash"]))
            .replace("__OFFSET_A__", str(first["offset"]))
            .replace("__HASH_B__", html.escape(second["hash"]))
            .replace("__OFFSET_B__", str(second["offset"]))
            .replace("__REPORT__", diagnostics)
            .replace("__DATA__", data))


def _software_uv_comparison_png(mesh: CandidateMesh, texture_png: bytes) -> bytes:
    """Software-render a private A/B UV comparison for viewers without WebGL.

    This is an orthographic diagnostic rasterizer, not a game renderer: it
    does no lighting, normal mapping, alpha processing, mip selection or
    perspective correction.  Its narrow value is that it applies each source
    float2 stream to the exact same validated triangles and decoded texels,
    allowing a visible A/B check in the workspace image viewer.
    """
    try:
        import numpy as np
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - setup branch
        raise RuntimeError("software UV fallback needs numpy and Pillow") from exc

    texture_image = Image.open(io.BytesIO(texture_png)).convert("RGBA")
    # The initial fallback used nearest base-level texel fetches, which turn a
    # genuinely repeating facade texture into colored snow under minification.
    # Build a standard box-filtered mip chain and choose a per-triangle level
    # from the affine UV screen derivatives, matching the diagnostic intent of
    # WebGL's mipmapped sampler much more closely (still not a game renderer).
    mipmaps = [np.asarray(texture_image, dtype=np.uint8)]
    while mipmaps[-1].shape[0] > 1 or mipmaps[-1].shape[1] > 1:
        previous = mipmaps[-1]
        resized = Image.fromarray(previous, mode="RGBA").resize(
            (max(1, previous.shape[1] // 2), max(1, previous.shape[0] // 2)),
            Image.Resampling.BOX,
        )
        mipmaps.append(np.asarray(resized, dtype=np.uint8))
    texture_height, texture_width = mipmaps[0].shape[:2]
    positions = np.frombuffer(mesh.positions, dtype="<f4").reshape(-1, 3).astype(np.float64)
    indices = np.frombuffer(mesh.indices, dtype="<u2").reshape(-1, 3)
    center = (positions.min(axis=0) + positions.max(axis=0)) * 0.5
    extent = float(np.max(positions.max(axis=0) - positions.min(axis=0)))
    # Stable diagonal view chosen only from the world bounds; it makes the
    # compare reproducible without treating an unverified normal as a camera
    # or culling input.
    eye = center + np.array([0.95, 0.72, 1.18]) * max(extent, 1.0) * 1.7
    forward = center - eye
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, np.array([0.0, 1.0, 0.0]))
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    projected_x = (positions - center) @ right
    projected_y = (positions - center) @ up
    depth = (positions - eye) @ forward
    span_x = float(projected_x.max() - projected_x.min()) or 1.0
    span_y = float(projected_y.max() - projected_y.min()) or 1.0
    scale = max(span_x / 420.0, span_y / 500.0) * 1.08
    pane_width, pane_height, header = 440, 548, 68
    x = (projected_x - (projected_x.min() + projected_x.max()) * 0.5) / scale + pane_width * 0.5
    y = -(projected_y - (projected_y.min() + projected_y.max()) * 0.5) / scale + pane_height * 0.5

    def render(uv_bytes: bytes) -> Image.Image:
        uv = np.frombuffer(uv_bytes, dtype="<f4").reshape(-1, 2).astype(np.float64)
        pixels = np.zeros((pane_height, pane_width, 4), dtype=np.uint8)
        pixels[:, :, :] = (6, 18, 27, 255)
        zbuffer = np.full((pane_height, pane_width), np.inf, dtype=np.float64)
        for tri in indices:
            ids = tri.astype(np.int64)
            px, py = x[ids], y[ids]
            min_x = max(0, int(math.floor(px.min())))
            max_x = min(pane_width - 1, int(math.ceil(px.max())))
            min_y = max(0, int(math.floor(py.min())))
            max_y = min(pane_height - 1, int(math.ceil(py.max())))
            if min_x > max_x or min_y > max_y:
                continue
            denominator = (py[1] - py[2]) * (px[0] - px[2]) + (px[2] - px[1]) * (py[0] - py[2])
            if abs(denominator) < 1e-10:
                continue
            grid_y, grid_x = np.mgrid[min_y:max_y + 1, min_x:max_x + 1]
            grid_x = grid_x.astype(np.float64) + 0.5
            grid_y = grid_y.astype(np.float64) + 0.5
            weight0 = ((py[1] - py[2]) * (grid_x - px[2]) + (px[2] - px[1]) * (grid_y - py[2])) / denominator
            weight1 = ((py[2] - py[0]) * (grid_x - px[2]) + (px[0] - px[2]) * (grid_y - py[2])) / denominator
            weight2 = 1.0 - weight0 - weight1
            inside = (weight0 >= -1e-8) & (weight1 >= -1e-8) & (weight2 >= -1e-8)
            tri_depth = weight0 * depth[ids[0]] + weight1 * depth[ids[1]] + weight2 * depth[ids[2]]
            local_depth = zbuffer[min_y:max_y + 1, min_x:max_x + 1]
            take = inside & (tri_depth < local_depth)
            if not np.any(take):
                continue
            u = weight0 * uv[ids[0], 0] + weight1 * uv[ids[1], 0] + weight2 * uv[ids[2], 0]
            v = weight0 * uv[ids[0], 1] + weight1 * uv[ids[1], 1] + weight2 * uv[ids[2], 1]
            weight0_dx, weight0_dy = (py[1] - py[2]) / denominator, (px[2] - px[1]) / denominator
            weight1_dx, weight1_dy = (py[2] - py[0]) / denominator, (px[0] - px[2]) / denominator
            weight2_dx, weight2_dy = -weight0_dx - weight1_dx, -weight0_dy - weight1_dy
            du_dx = weight0_dx * uv[ids[0], 0] + weight1_dx * uv[ids[1], 0] + weight2_dx * uv[ids[2], 0]
            dv_dx = weight0_dx * uv[ids[0], 1] + weight1_dx * uv[ids[1], 1] + weight2_dx * uv[ids[2], 1]
            du_dy = weight0_dy * uv[ids[0], 0] + weight1_dy * uv[ids[1], 0] + weight2_dy * uv[ids[2], 0]
            dv_dy = weight0_dy * uv[ids[0], 1] + weight1_dy * uv[ids[1], 1] + weight2_dy * uv[ids[2], 1]
            rho = max(
                math.hypot(du_dx * texture_width, dv_dx * texture_height),
                math.hypot(du_dy * texture_width, dv_dy * texture_height),
            )
            mip_level = min(len(mipmaps) - 1, max(0, int(math.floor(math.log2(max(rho, 1.0))))))
            sampled_texture = mipmaps[mip_level]
            sampled_height, sampled_width = sampled_texture.shape[:2]
            tex_x = np.floor(np.mod(u, 1.0) * sampled_width).astype(np.int64) % sampled_width
            tex_y = np.floor(np.mod(v, 1.0) * sampled_height).astype(np.int64) % sampled_height
            local_pixels = pixels[min_y:max_y + 1, min_x:max_x + 1]
            sampled = sampled_texture[tex_y, tex_x].copy()
            # Treat texture alpha as opaque intentionally: alpha/material
            # behavior is not within this UV-only diagnostic's claim.
            sampled[:, :, 3] = 255
            local_pixels[take] = sampled[take]
            local_depth[take] = tri_depth[take]
        return Image.fromarray(pixels, mode="RGBA")

    left = render(mesh.uv_streams[0][2])
    right_image = render(mesh.uv_streams[1][2])
    canvas = Image.new("RGBA", (pane_width * 2, pane_height + header), (5, 13, 20, 255))
    canvas.alpha_composite(left, (0, header))
    canvas.alpha_composite(right_image, (pane_width, header))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, pane_width * 2 - 1, header - 1), fill=(11, 31, 42, 255))
    first, second = mesh.uv_streams
    draw.text((12, 11), "STATIC UV CANDIDATE A/B - SOFTWARE FALLBACK", fill=(181, 226, 154, 255))
    draw.text((12, 35), f"A: {first[1]} @ {first[0]}  |  original float2", fill=(218, 233, 242, 255))
    draw.text((pane_width + 12, 35), f"B: {second[1]} @ {second[0]}  |  original float2", fill=(218, 233, 242, 255))
    draw.line((pane_width, header, pane_width, pane_height + header), fill=(79, 116, 130, 255), width=1)
    stream = io.BytesIO()
    canvas.save(stream, format="PNG")
    return stream.getvalue()


def _fetch_raw(base_url: str, rcf_path: str, entry_name: str, timeout: int) -> bytes:
    query = urllib.parse.urlencode({"path": rcf_path, "name": entry_name, "raw": "1"})
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/rcf_entry?" + query, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not fetch {entry_name!r}: {exc}") from exc


def _atomic_json(path: str, value: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_text(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".static_uv_candidates_", suffix=".tmp", dir=directory)
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


def _atomic_bytes(path: str, content: bytes) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".static_uv_candidates_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--rcf-path", required=True)
    parser.add_argument("--entry", required=True)
    parser.add_argument("--geometry", required=True)
    parser.add_argument("--group-ordinal", type=int, required=True, help="1-based PrimitiveGroup ordinal inside selected Geometry")
    parser.add_argument("--uv-offset", action="append", type=int, required=True, help="declared source-0 candidate byte offset; supply exactly twice")
    parser.add_argument("--texture", required=True, help="exact local Texture name to decode")
    parser.add_argument("--out", required=True, help="metadata-only JSON output")
    parser.add_argument("--preview-html", required=True, help="private derived-payload WebGL output")
    parser.add_argument("--preview-png", help="optional private software-rendered fallback PNG for viewers without WebGL")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    data = _fetch_raw(args.base_url, args.rcf_path, args.entry, args.timeout)
    mesh = extract_candidate_mesh(data, args.entry, args.geometry, args.group_ordinal, args.uv_offset)
    records, children = _records(data)
    algorithm, width, height, png = _decode_local_texture_to_png(records, children, args.texture)
    report = build_metadata_report(mesh, args.texture, algorithm, width, height)
    output = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": {"base_url": args.base_url.rstrip("/"), "rcf_path": args.rcf_path},
        "report": report,
    }
    _atomic_json(args.out, output)
    _atomic_text(args.preview_html, _preview_html(mesh, report, png))
    if args.preview_png:
        _atomic_bytes(args.preview_png, _software_uv_comparison_png(mesh, png))
    print(f"validated {mesh.geometry_name} group {mesh.group_ordinal}: {len(mesh.positions)//12} vertices, {mesh.triangle_count} triangles")
    print(f"decoded {args.texture}: {width}x{height} {algorithm}; wrote private preview {args.preview_html}")
    if args.preview_png:
        print(f"wrote software fallback PNG {args.preview_png}")
    print(f"wrote metadata-only report {args.out}")


if __name__ == "__main__":
    main()
