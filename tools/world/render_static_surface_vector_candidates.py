#!/usr/bin/env python3
"""Private WebGL directional-light comparison for static normal/tangent candidates.

One already-validated ``mergedDrawableRoot*`` TriangleList is rendered twice
with the same source color-texture coordinate and decoded local texture.  The
left pane uses a selected float3 vector candidate for diffuse directional
lighting; the right pane substitutes a second candidate.  The output is an
explicit diagnostic, not a game shader or final material exporter.

All P3D/DDS input stays in memory.  The JSON report stores structural metadata
and aggregate vector evidence only; the explicitly requested HTML is a private
derived-payload preview and must not be committed/distributed.
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
from dataclasses import dataclass
from typing import Any

from export_static_geometry_diagnostic import MEMORY_INDEX_LIST, CoreGeometryError, _parse_memory_index_list, _parse_memory_vertex_list
from probe_static_geometry import (
    GEOMETRY, MEMORY_VERTEX_DESCRIPTION, MEMORY_VERTEX_LIST, PRIMITIVE_GROUP,
    _fingerprint_vertex_description, _parse_primitive_group, _p3d_string,
)
from probe_static_surface_vectors import analyze_surface_vectors
from render_static_uv_candidates import _children, _decode_local_texture_to_png, _fetch_raw, _records, _extract_uv_stream


@dataclass(frozen=True)
class LitCandidateMesh:
    entry_name: str
    geometry_name: str
    group_ordinal: int
    shader_name: str
    layout_sha256: str
    positions: bytes
    indices: bytes
    uv_hash: str
    uvs: bytes
    candidates: tuple[tuple[int, str, bytes], tuple[int, str, bytes]]


def _vector_stream(vertex_payload: bytes, vertex_count: int, stride: int, declaration: dict[str, Any], offset: int) -> tuple[str, bytes]:
    matches = [attribute for attribute in declaration["attributes"] if attribute["source"] == 0 and attribute["offset"] == offset]
    if len(matches) != 1:
        raise CoreGeometryError(f"expected exactly one declared source-0 float3 candidate at offset {offset}, found {len(matches)}")
    if offset < 0 or offset + 12 > stride:
        raise CoreGeometryError(f"float3 candidate offset {offset} does not fit stride {stride}")
    body = vertex_payload[12:]
    out = bytearray(vertex_count * 12)
    for number in range(vertex_count):
        vector = struct.unpack_from("<3f", body, number * stride + offset)
        if not all(math.isfinite(value) for value in vector):
            raise CoreGeometryError(f"non-finite vector at vertex {number}, offset {offset}")
        struct.pack_into("<3f", out, number * 12, *vector)
    return matches[0]["semantic_hash"], bytes(out)


def extract_lit_candidate_mesh(data: bytes, entry_name: str, geometry_name: str, group_ordinal: int, uv_offset: int, first_vector_offset: int, second_vector_offset: int) -> LitCandidateMesh:
    records, children = _records(data)
    geometry_index = next((index for index, record in enumerate(records)
                           if record["type_id"] == GEOMETRY and _p3d_string(record["payload"])[0] == geometry_name), None)
    if geometry_index is None:
        raise CoreGeometryError(f"Geometry {geometry_name!r} not found")
    if not geometry_name.casefold().startswith("mergeddrawableroot"):
        raise CoreGeometryError("refusing local-space Geometry; expected mergedDrawableRoot*")
    primitive_groups = [(index, record) for index, record in children[geometry_index] if record["type_id"] == PRIMITIVE_GROUP]
    if not 1 <= group_ordinal <= len(primitive_groups):
        raise CoreGeometryError(f"group ordinal {group_ordinal} outside 1..{len(primitive_groups)}")
    group_index, group_record = primitive_groups[group_ordinal - 1]
    group = _parse_primitive_group(group_record["payload"])
    if group["primitive_type"] != 0:
        raise CoreGeometryError(f"expected TriangleList=0, got {group['primitive_type']}")
    direct = children[group_index]
    vertex_lists = [record for _index, record in direct if record["type_id"] == MEMORY_VERTEX_LIST]
    index_lists = [record for _index, record in direct if record["type_id"] == MEMORY_INDEX_LIST]
    declarations = [record for _index, record in direct if record["type_id"] == MEMORY_VERTEX_DESCRIPTION]
    if not (len(vertex_lists) == len(index_lists) == len(declarations) == 1):
        raise CoreGeometryError("expected exactly one vertex list, index list, and declaration")
    declaration = _fingerprint_vertex_description(declarations[0]["payload"])
    stride, positions, _minimum, _maximum = _parse_memory_vertex_list(vertex_lists[0]["payload"], group["vertex_count"], declaration)
    indices, _imin, _imax, _repeat, _triangles = _parse_memory_index_list(index_lists[0]["payload"], group["vertex_count"], group["index_count"])
    uv_hash, uvs, _uvmin, _uvmax = _extract_uv_stream(vertex_lists[0]["payload"], group["vertex_count"], stride, declaration, uv_offset)
    first_hash, first = _vector_stream(vertex_lists[0]["payload"], group["vertex_count"], stride, declaration, first_vector_offset)
    second_hash, second = _vector_stream(vertex_lists[0]["payload"], group["vertex_count"], stride, declaration, second_vector_offset)
    return LitCandidateMesh(
        entry_name, geometry_name, group_ordinal, group["shader_name"], declaration["layout_sha256"],
        positions, indices, uv_hash, uvs,
        ((first_vector_offset, first_hash, first), (second_vector_offset, second_hash, second)),
    )


def _build_html(mesh: LitCandidateMesh, report: dict[str, Any], png: bytes) -> str:
    data = json.dumps({
        "p": base64.b64encode(mesh.positions).decode("ascii"),
        "i": base64.b64encode(mesh.indices).decode("ascii"),
        "uv": base64.b64encode(mesh.uvs).decode("ascii"),
        "a": base64.b64encode(mesh.candidates[0][2]).decode("ascii"),
        "b": base64.b64encode(mesh.candidates[1][2]).decode("ascii"),
        "image": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
    }, separators=(",", ":"))
    a, b = mesh.candidates
    template = r"""<!doctype html><html lang="zh-Hans"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Static normal/tangent lighting diagnostic</title><style>
:root{color-scheme:dark;--bg:#071118;--panel:#0d1b25;--line:#2d4c5e;--ink:#e2eef4;--mute:#91aab9;--lime:#aae477;--orange:#ffb46b}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;grid-template-columns:minmax(0,1fr) 350px;background:var(--bg);color:var(--ink);font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}main{position:relative;min-height:100vh}canvas{display:block;width:100%;height:100vh;cursor:grab;background:radial-gradient(ellipse at 50% 45%,#19384a,#07131c 63%,#030609)}canvas:active{cursor:grabbing}.head{position:absolute;left:18px;top:16px;pointer-events:none}.head h1{font:600 15px/1.2 system-ui,sans-serif;letter-spacing:.04em;margin:0 0 7px}.head p{margin:0;max-width:710px;color:var(--mute)}.tag{position:absolute;top:82px;width:calc(50% - 24px);padding:8px 10px;border:1px solid #4a6c7f;background:#07141ddd;color:var(--lime);font-size:11px}.left{left:18px}.right{left:calc(50% + 6px)}aside{max-height:100vh;overflow:auto;border-left:1px solid var(--line);padding:20px;background:var(--panel)}h2{font:600 13px/1.2 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.12em;margin:0 0 10px;color:var(--lime)}p{margin:0 0 16px;color:var(--mute)}button{border:1px solid #46677a;background:#102d3b;color:var(--ink);border-radius:5px;padding:8px 10px;font:inherit;cursor:pointer;margin:0 6px 10px 0}button:hover{background:#19495c}pre{margin:0;white-space:pre-wrap;word-break:break-word;font-size:11px;padding:12px;border:1px solid var(--line);border-radius:6px;background:#07131b;color:#b9d0db}small{display:block;margin-top:17px;color:var(--mute)}@media(max-width:800px){body{display:block}canvas{height:61vh}.tag{top:90px}aside{border-left:0;border-top:1px solid var(--line);max-height:none}}
</style></head><body><main><canvas id="c"></canvas><div class="head"><h1>静态 surface-vector 定向光对照</h1><p>同一已验证 TriangleList、同一 color-coordinate 与同一解码贴图；仅替换定向光所用的 float3。拖拽旋转，滚轮缩放。</p></div><div class="tag left">A · __A_HASH__ @ __A_OFFSET__ · normal candidate</div><div class="tag right">B · __B_HASH__ @ __B_OFFSET__ · tangent candidate substituted as normal</div></main><aside><h2>验收边界</h2><p>若 A 的明暗面更连续、合理，而 B 出现与几何面不符的条纹/反相/不稳定明暗，这可验证 A 作为定向光 normal 的候选。它仍不是完整游戏 shader、normal map 或 glTF 最终材质。</p><button id="reset">重置视角</button><h2 style="margin-top:17px">metadata-only 记录</h2><pre>__REPORT__</pre><small>私有诊断 HTML 内含派生 POSITION/index/一个已确认 color coordinate/两个 vector candidate 与解码贴图；不得提交或分发。</small></aside><script>
const D=__DATA__,c=document.getElementById('c'),g=c.getContext('webgl',{antialias:true});if(!g){document.body.innerHTML='<p style="padding:2rem">浏览器未提供 WebGL，请在普通浏览器中打开。</p>';throw Error('WebGL unavailable')}const vs=`attribute vec3 p;attribute vec2 uv;attribute vec3 n;uniform mat4 mvp;varying vec2 t;varying float l;void main(){gl_Position=mvp*vec4(p,1.);t=uv;l=.18+.82*max(0.,dot(normalize(n),normalize(vec3(.38,.72,.56))));}`;const fs=`precision mediump float;uniform sampler2D tex;varying vec2 t;varying float l;void main(){gl_FragColor=vec4(texture2D(tex,t).rgb*l,1.);}`;function s(t,x){const z=g.createShader(t);g.shaderSource(z,x);g.compileShader(z);if(!g.getShaderParameter(z,g.COMPILE_STATUS))throw Error(g.getShaderInfoLog(z));return z}const pr=g.createProgram();g.attachShader(pr,s(g.VERTEX_SHADER,vs));g.attachShader(pr,s(g.FRAGMENT_SHADER,fs));g.linkProgram(pr);if(!g.getProgramParameter(pr,g.LINK_STATUS))throw Error(g.getProgramInfoLog(pr));g.useProgram(pr);const pl=g.getAttribLocation(pr,'p'),ul=g.getAttribLocation(pr,'uv'),nl=g.getAttribLocation(pr,'n'),ml=g.getUniformLocation(pr,'mvp');function b(x){const r=atob(x),o=new Uint8Array(r.length);for(let i=0;i<r.length;i++)o[i]=r.charCodeAt(i);return o}function q(t,x){const z=g.createBuffer();g.bindBuffer(t,z);g.bufferData(t,x,g.STATIC_DRAW);return z}const pb=q(g.ARRAY_BUFFER,b(D.p)),ub=q(g.ARRAY_BUFFER,b(D.uv)),ib=q(g.ELEMENT_ARRAY_BUFFER,b(D.i)),nb=[q(g.ARRAY_BUFFER,b(D.a)),q(g.ARRAY_BUFFER,b(D.b))],idxCount=b(D.i).byteLength/2,posRaw=b(D.p),dv=new DataView(posRaw.buffer),lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(let i=0;i<posRaw.byteLength;i+=12)for(let k=0;k<3;k++){const v=dv.getFloat32(i+4*k,true);lo[k]=Math.min(lo[k],v);hi[k]=Math.max(hi[k],v)}const center=lo.map((v,k)=>(v+hi[k])/2),extent=Math.max(hi[0]-lo[0],hi[1]-lo[1],hi[2]-lo[2]),base=Math.max(extent*1.22,3);let yaw=-2.35,pitch=.5,dist=base,drag=null;function norm(v){const x=Math.hypot(...v);return v.map(y=>y/x)}function cross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]}function mul(a,b){const o=new Float32Array(16);for(let r=0;r<4;r++)for(let x=0;x<4;x++)o[x*4+r]=a[r]*b[x*4]+a[4+r]*b[x*4+1]+a[8+r]*b[x*4+2]+a[12+r]*b[x*4+3];return o}function persp(f,a,n,z){const q=1/Math.tan(f/2),m=new Float32Array(16);m[0]=q/a;m[5]=q;m[10]=(z+n)/(n-z);m[11]=-1;m[14]=2*z*n/(n-z);return m}function look(e,t){const f=norm(t.map((v,i)=>v-e[i])),x=norm(cross(f,[0,1,0])),u=cross(x,f),m=new Float32Array(16);m[0]=x[0];m[1]=u[0];m[2]=-f[0];m[4]=x[1];m[5]=u[1];m[6]=-f[1];m[8]=x[2];m[9]=u[2];m[10]=-f[2];m[12]=-dot(x,e);m[13]=-dot(u,e);m[14]=dot(f,e);m[15]=1;return m}const tex=g.createTexture(),img=new Image();img.onload=()=>{g.bindTexture(g.TEXTURE_2D,tex);g.pixelStorei(g.UNPACK_FLIP_Y_WEBGL,false);g.texImage2D(g.TEXTURE_2D,0,g.RGBA,g.RGBA,g.UNSIGNED_BYTE,img);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_WRAP_S,g.REPEAT);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_WRAP_T,g.REPEAT);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_MIN_FILTER,g.LINEAR_MIPMAP_LINEAR);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_MAG_FILTER,g.LINEAR);g.generateMipmap(g.TEXTURE_2D);requestAnimationFrame(draw)};img.src=D.image;function draw(){const d=Math.min(devicePixelRatio||1,2),w=Math.floor(c.clientWidth*d),h=Math.floor(c.clientHeight*d);if(c.width!==w||c.height!==h){c.width=w;c.height=h}g.enable(g.DEPTH_TEST);g.disable(g.CULL_FACE);g.clearColor(.02,.05,.07,1);g.clear(g.COLOR_BUFFER_BIT|g.DEPTH_BUFFER_BIT);const cp=Math.cos(pitch),eye=[center[0]+dist*Math.cos(yaw)*cp,center[1]+dist*Math.sin(pitch),center[2]+dist*Math.sin(yaw)*cp],m=look(eye,center);g.useProgram(pr);g.bindBuffer(g.ARRAY_BUFFER,pb);g.enableVertexAttribArray(pl);g.vertexAttribPointer(pl,3,g.FLOAT,false,0,0);g.bindBuffer(g.ARRAY_BUFFER,ub);g.enableVertexAttribArray(ul);g.vertexAttribPointer(ul,2,g.FLOAT,false,0,0);g.bindBuffer(g.ELEMENT_ARRAY_BUFFER,ib);for(let k=0;k<2;k++){g.viewport(k*w/2,0,w/2,h);g.uniformMatrix4fv(ml,false,mul(persp(.77,(w/2)/h,Math.max(.01,extent*.0001),extent*20+100),m));g.bindBuffer(g.ARRAY_BUFFER,nb[k]);g.enableVertexAttribArray(nl);g.vertexAttribPointer(nl,3,g.FLOAT,false,0,0);g.drawElements(g.TRIANGLES,idxCount,g.UNSIGNED_SHORT,0)}requestAnimationFrame(draw)}c.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];c.setPointerCapture(e.pointerId)});c.addEventListener('pointermove',e=>{if(!drag)return;yaw+=(e.clientX-drag[0])*.008;pitch=Math.max(-1.48,Math.min(1.48,pitch+(e.clientY-drag[1])*.008));drag=[e.clientX,e.clientY]});c.addEventListener('pointerup',()=>drag=null);c.addEventListener('wheel',e=>{e.preventDefault();dist=Math.max(base*.08,Math.min(base*8,dist*Math.exp(e.deltaY*.001)))},{passive:false});document.getElementById('reset').onclick=()=>{yaw=-2.35;pitch=.5;dist=base};
</script></body></html>"""
    return (template.replace("__A_HASH__", html.escape(a[1])).replace("__A_OFFSET__", str(a[0]))
            .replace("__B_HASH__", html.escape(b[1])).replace("__B_OFFSET__", str(b[0]))
            .replace("__REPORT__", html.escape(json.dumps(report, ensure_ascii=False, indent=2)))
            .replace("__DATA__", data))


def _atomic_json(path: str, value: dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".surface_vector_render_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except OSError: pass
        raise


def _atomic_text(path: str, value: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".surface_vector_render_", suffix=".html", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle: handle.write(value)
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except OSError: pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True); parser.add_argument("--rcf-path", required=True)
    parser.add_argument("--entry", required=True); parser.add_argument("--geometry", required=True)
    parser.add_argument("--group-ordinal", required=True, type=int); parser.add_argument("--texture", required=True)
    parser.add_argument("--uv-offset", required=True, type=int); parser.add_argument("--normal-offset", required=True, type=int)
    parser.add_argument("--tangent-offset", required=True, type=int); parser.add_argument("--tangent-w-offset", required=True, type=int)
    parser.add_argument("--out", required=True); parser.add_argument("--preview-html", required=True); parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    data = _fetch_raw(args.base_url, args.rcf_path, args.entry, args.timeout)
    mesh = extract_lit_candidate_mesh(data, args.entry, args.geometry, args.group_ordinal, args.uv_offset, args.normal_offset, args.tangent_offset)
    records, children = _records(data)
    algorithm, width, height, png = _decode_local_texture_to_png(records, children, args.texture)
    vector_report = analyze_surface_vectors(data, args.entry, args.geometry, args.group_ordinal, args.normal_offset, args.tangent_offset, args.tangent_w_offset)
    report = {"scope": "directional-light candidate comparison; one selected core group only", "texture": {"name": args.texture, "algorithm": algorithm, "width": width, "height": height}, "surface_vector_evidence": vector_report, "conclusion_limit": "A/B diffuse lighting only; not final game shader or normal-map validation"}
    output = {"schema_version": 1, "generated_at_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(), "source": {"base_url": args.base_url.rstrip("/"), "rcf_path": args.rcf_path}, "report": report}
    _atomic_json(args.out, output); _atomic_text(args.preview_html, _build_html(mesh, report, png))
    print(f"rendered vector candidates {mesh.candidates[0][1]}@{mesh.candidates[0][0]} vs {mesh.candidates[1][1]}@{mesh.candidates[1][0]}; wrote {args.preview_html}")


if __name__ == "__main__":
    main()
