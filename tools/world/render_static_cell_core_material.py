#!/usr/bin/env python3
"""Private aggregate material diagnostic for one Manhattan Cell's world core.

Unlike single-PrimitiveGroup regression fixtures, this assembles all validated
``mergedDrawableRoot*`` TriangleLists from one selected base Cell into one
orbitable WebGL scene.  The currently evidenced 68-byte declaration receives
its source color coordinate at offset 24 and geometric normal candidate at
40.  A local ``NewShader`` color value is used only where it resolves to a
local DXT1/DXT5 Texture; all other accepted core groups remain intentionally
flat gray context rather than guessed material bindings.

Input P3D/DDS bytes are never written.  The JSON report is metadata-only; the
explicit HTML preview contains derivative display payload and is private.
"""
from __future__ import annotations
import argparse, base64, datetime as dt, json, os, struct, tempfile
from collections import Counter
from typing import Any
from export_static_geometry_diagnostic import MEMORY_INDEX_LIST, CoreGeometryError, _parse_memory_index_list, _parse_memory_vertex_list
from probe_static_geometry import GEOMETRY, MEMORY_VERTEX_DESCRIPTION, MEMORY_VERTEX_LIST, PRIMITIVE_GROUP, _fingerprint_vertex_description, _parse_primitive_group, _p3d_string
from probe_cell_shader_dependencies import NEW_SHADER, NEW_SHADER_STRING_PARAMETER, _parse_new_shader_header, _parse_exact_string_pair
from render_static_uv_candidates import _children, _decode_local_texture_to_png, _extract_uv_stream, _fetch_raw, _records
from render_static_surface_vector_candidates import _vector_stream

UV_OFFSET, NORMAL_OFFSET = 24, 40


def _shader_colors(records, children):
    result = {}
    for i, r in enumerate(records):
        if r['type_id'] != NEW_SHADER: continue
        try:
            h = _parse_new_shader_header(r['payload']); color = None
            for _, p in children[i]:
                if p['type_id'] == NEW_SHADER_STRING_PARAMETER:
                    k, v = _parse_exact_string_pair(p['payload'], 'NewShader parameter')
                    if k == 'color' and v: color = v
            result[h['shader_name']] = color
        except ValueError: pass
    return result


def _mesh_records(data: bytes, entry: str):
    records, children = _records(data); colors = _shader_colors(records, children)
    meshes, skipped = [], []
    for gi, geom in enumerate(records):
        if geom['type_id'] != GEOMETRY: continue
        name, _ = _p3d_string(geom['payload'])
        if not name.casefold().startswith('mergeddrawableroot'): continue
        ordinal = 0
        for pi, pg in children[gi]:
            if pg['type_id'] != PRIMITIVE_GROUP: continue
            ordinal += 1
            try:
                h = _parse_primitive_group(pg['payload'])
                if h['primitive_type'] != 0: raise CoreGeometryError('not TriangleList')
                direct = children[pi]
                vl = [r for _, r in direct if r['type_id'] == MEMORY_VERTEX_LIST]
                il = [r for _, r in direct if r['type_id'] == MEMORY_INDEX_LIST]
                vd = [r for _, r in direct if r['type_id'] == MEMORY_VERTEX_DESCRIPTION]
                if not (len(vl) == len(il) == len(vd) == 1): raise CoreGeometryError('not exactly one list/declaration')
                dec = _fingerprint_vertex_description(vd[0]['payload'])
                stride, pos, _, _ = _parse_memory_vertex_list(vl[0]['payload'], h['vertex_count'], dec)
                ind, _, _, _, tri = _parse_memory_index_list(il[0]['payload'], h['vertex_count'], h['index_count'])
                texture_name, uv, normal = None, None, None
                if stride == 68:
                    try:
                        _uv_hash, uv, _, _ = _extract_uv_stream(vl[0]['payload'], h['vertex_count'], stride, dec, UV_OFFSET)
                        _n_hash, normal = _vector_stream(vl[0]['payload'], h['vertex_count'], stride, dec, NORMAL_OFFSET)
                        texture_name = colors.get(h['shader_name'])
                    except CoreGeometryError: pass
                meshes.append({'geometry': name, 'ordinal': ordinal, 'shader': h['shader_name'], 'layout': dec['layout_sha256'], 'stride': stride, 'vertices': h['vertex_count'], 'triangles': tri, 'pos': pos, 'idx': ind, 'uv': uv, 'normal': normal, 'color_name': texture_name})
            except (CoreGeometryError, ValueError, struct.error) as exc:
                skipped.append(f'{name} group {ordinal}: {exc}')
    return records, children, meshes, skipped


def build_preview_data(data: bytes, entry: str):
    records, children, meshes, skipped = _mesh_records(data, entry)
    texture_index, textures, texture_errors = {}, [], []
    for mesh in meshes:
        name = mesh['color_name']
        if not name or not mesh['uv'] or not mesh['normal']: continue
        if name not in texture_index:
            try:
                algorithm, width, height, png = _decode_local_texture_to_png(records, children, name)
                texture_index[name] = len(textures)
                textures.append({'name': name, 'algorithm': algorithm, 'width': width, 'height': height, 'image': 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')})
            except Exception as exc:  # missing local texture/DXT3 etc remain deliberate gray context
                texture_index[name] = None; texture_errors.append(f'{name}: {type(exc).__name__}: {exc}')
        mesh['texture'] = texture_index[name]
    compact = []
    for mesh in meshes:
        compact.append({'geometry': mesh['geometry'], 'ordinal': mesh['ordinal'], 'shader': mesh['shader'], 'stride': mesh['stride'], 'vertices': mesh['vertices'], 'triangles': mesh['triangles'], 'texture': mesh.get('texture'), 'p': base64.b64encode(mesh['pos']).decode('ascii'), 'i': base64.b64encode(mesh['idx']).decode('ascii'), 'uv': base64.b64encode(mesh['uv']).decode('ascii') if mesh['uv'] else None, 'n': base64.b64encode(mesh['normal']).decode('ascii') if mesh['normal'] else None})
    textured = [m for m in compact if m['texture'] is not None]
    report = {'entry_name': entry, 'scope': 'all mergedDrawableRoot TriangleLists; 68-byte color@24 + normal@40 only when local color Texture decodes; other groups gray', 'core_group_count': len(compact), 'core_vertex_count': sum(m['vertices'] for m in compact), 'core_triangle_count': sum(m['triangles'] for m in compact), 'textured_68_byte_group_count': len(textured), 'gray_context_group_count': len(compact)-len(textured), 'decoded_local_color_texture_count': len(textures), 'vertex_stride_counts': dict(sorted(Counter(str(m['stride']) for m in compact).items())), 'strict_group_errors': skipped, 'texture_decode_or_resolution_notes': texture_errors, 'conclusion_limit': 'aggregate diagnostic only; no cross-archive textures, non-68 UV rule, gameplay _ft, local props, V-axis decision, or final game shader claim'}
    return {'meshes': compact, 'textures': textures}, report


def _html(data, report):
    payload = json.dumps(data, separators=(',', ':'))
    safe = json.dumps(report, ensure_ascii=False, indent=2).replace('&','&amp;').replace('<','&lt;')
    template = r'''<!doctype html><html lang="zh-Hans"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Manhattan Cell core material diagnostic</title><style>:root{color-scheme:dark;--bg:#071018;--p:#0d1b24;--line:#294859;--ink:#e2edf3;--m:#91aab9;--lime:#a9e274}*{box-sizing:border-box}body{margin:0;display:grid;grid-template-columns:minmax(0,1fr) 350px;min-height:100vh;background:var(--bg);color:var(--ink);font:14px/1.45 ui-monospace,monospace}main{position:relative}canvas{display:block;width:100%;height:100vh;cursor:grab;background:radial-gradient(ellipse at 50% 45%,#193b4a,#07121a 62%,#020609)}canvas:active{cursor:grabbing}.head{position:absolute;top:16px;left:18px;pointer-events:none}.head h1{font:600 15px system-ui;margin:0 0 8px}.head p{margin:0;color:var(--m);max-width:690px}.pill{position:absolute;left:18px;bottom:16px;background:#07141cdd;border:1px solid var(--line);padding:8px;color:var(--m);font-size:11px}aside{max-height:100vh;overflow:auto;background:var(--p);border-left:1px solid var(--line);padding:20px}h2{font:600 13px system-ui;letter-spacing:.12em;text-transform:uppercase;color:var(--lime);margin:0 0 10px}p{color:var(--m);margin:0 0 15px}button{border:1px solid #486a7d;background:#12303d;color:var(--ink);border-radius:5px;padding:8px 10px;font:inherit;cursor:pointer;margin:0 6px 10px 0}button.active{color:var(--lime);border-color:var(--lime)}pre{font-size:11px;white-space:pre-wrap;word-break:break-word;background:#07131a;border:1px solid var(--line);padding:12px;border-radius:6px;margin:0}@media(max-width:800px){body{display:block}canvas{height:61vh}aside{border-left:0;border-top:1px solid var(--line);max-height:none}}</style></head><body><main><canvas id="c"></canvas><div class="head"><h1>曼哈顿 Cell · 合并 core material diagnostic</h1><p>已合并当前 Cell 的全部 <b>mergedDrawableRoot*</b> TriangleList。已验证 68-byte layout 的本地 color texture 显示材质；其余 group 明确保留灰色上下文，不假造贴图。</p></div><div class="pill" id="status">loading textures…</div></main><aside><h2>显示过滤</h2><button id="all" class="active">全部 core</button><button id="textured">仅已贴图</button><button id="gray">仅灰色上下文</button><button id="reset">重置视角</button><h2 style="margin-top:16px">metadata-only 记录</h2><pre>__REPORT__</pre></aside><script>const D=__DATA__,c=document.getElementById('c'),g=c.getContext('webgl',{antialias:true});if(!g){document.body.innerHTML='<p style="padding:2rem">浏览器未提供 WebGL；此合并预览需普通浏览器。</p>';throw Error('WebGL unavailable')}const vs=`attribute vec3 p;attribute vec2 uv;attribute vec3 n;uniform mat4 mvp;uniform float texed;uniform float nlit;varying vec2 t;varying float l;void main(){gl_Position=mvp*vec4(p,1.);t=uv;l=nlit>.5?.20+.80*max(0.,dot(normalize(n),normalize(vec3(.38,.72,.56)))):.58;}`;const fs=`precision mediump float;uniform sampler2D tex;uniform float texed;varying vec2 t;varying float l;void main(){vec3 a=texed>.5?texture2D(tex,t).rgb:vec3(.29,.34,.35);gl_FragColor=vec4(a*l,1.);}`;function S(t,x){const s=g.createShader(t);g.shaderSource(s,x);g.compileShader(s);if(!g.getShaderParameter(s,g.COMPILE_STATUS))throw Error(g.getShaderInfoLog(s));return s}const pr=g.createProgram();g.attachShader(pr,S(g.VERTEX_SHADER,vs));g.attachShader(pr,S(g.FRAGMENT_SHADER,fs));g.linkProgram(pr);g.useProgram(pr);const pl=g.getAttribLocation(pr,'p'),ul=g.getAttribLocation(pr,'uv'),nl=g.getAttribLocation(pr,'n'),ml=g.getUniformLocation(pr,'mvp'),tl=g.getUniformLocation(pr,'texed'),ll=g.getUniformLocation(pr,'nlit');function B(s){const r=atob(s),o=new Uint8Array(r.length);for(let i=0;i<r.length;i++)o[i]=r.charCodeAt(i);return o}function Q(t,a){const b=g.createBuffer();g.bindBuffer(t,b);g.bufferData(t,a,g.STATIC_DRAW);return b}const dummyUV=Q(g.ARRAY_BUFFER,new Float32Array([0,0])),dummyN=Q(g.ARRAY_BUFFER,new Float32Array([0,1,0]));const M=D.meshes.map(m=>({...m,p:Q(g.ARRAY_BUFFER,B(m.p)),i:Q(g.ELEMENT_ARRAY_BUFFER,B(m.i)),uv:m.uv?Q(g.ARRAY_BUFFER,B(m.uv)):null,n:m.n?Q(g.ARRAY_BUFFER,B(m.n)):null,count:B(m.i).byteLength/2}));const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];for(const m of M){const a=B(m.p),v=new DataView(a.buffer);for(let i=0;i<a.byteLength;i+=12)for(let k=0;k<3;k++){const z=v.getFloat32(i+4*k,true);lo[k]=Math.min(lo[k],z);hi[k]=Math.max(hi[k],z)}}const center=lo.map((v,i)=>(v+hi[i])/2),extent=Math.max(hi[0]-lo[0],hi[1]-lo[1],hi[2]-lo[2]),base=Math.max(extent*1.18,4);let yaw=-2.35,pitch=.58,dist=base,mode='all',drag=null;function norm(v){const q=Math.hypot(...v);return v.map(x=>x/q)}function cross(a,b){return[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]}function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]}function mul(a,b){const o=new Float32Array(16);for(let r=0;r<4;r++)for(let x=0;x<4;x++)o[x*4+r]=a[r]*b[x*4]+a[4+r]*b[x*4+1]+a[8+r]*b[x*4+2]+a[12+r]*b[x*4+3];return o}function per(f,a,n,z){const q=1/Math.tan(f/2),m=new Float32Array(16);m[0]=q/a;m[5]=q;m[10]=(z+n)/(n-z);m[11]=-1;m[14]=2*z*n/(n-z);return m}function look(e,t){const f=norm(t.map((v,i)=>v-e[i])),x=norm(cross(f,[0,1,0])),u=cross(x,f),m=new Float32Array(16);m[0]=x[0];m[1]=u[0];m[2]=-f[0];m[4]=x[1];m[5]=u[1];m[6]=-f[1];m[8]=x[2];m[9]=u[2];m[10]=-f[2];m[12]=-dot(x,e);m[13]=-dot(u,e);m[14]=dot(f,e);m[15]=1;return m}let T=[];let ready=0,started=false;function finished(){if(++ready===D.textures.length)start()}D.textures.forEach((x,i)=>{const im=new Image();im.onload=()=>{const t=g.createTexture();g.bindTexture(g.TEXTURE_2D,t);g.pixelStorei(g.UNPACK_FLIP_Y_WEBGL,false);g.texImage2D(g.TEXTURE_2D,0,g.RGBA,g.RGBA,g.UNSIGNED_BYTE,im);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_WRAP_S,g.REPEAT);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_WRAP_T,g.REPEAT);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_MIN_FILTER,g.LINEAR_MIPMAP_LINEAR);g.texParameteri(g.TEXTURE_2D,g.TEXTURE_MAG_FILTER,g.LINEAR);g.generateMipmap(g.TEXTURE_2D);T[i]=t;finished()};im.onerror=()=>{console.warn('texture image failed',x.name);finished()};im.src=x.image});function start(){if(started)return;started=true;document.getElementById('status').textContent=`${M.length} core groups · ${D.textures.length} local color textures · drag orbit / wheel zoom`;requestAnimationFrame(draw)}if(!D.textures.length)start();setTimeout(()=>{if(!started){console.warn('texture load timeout');start()}},4000);function draw(){const d=Math.min(devicePixelRatio||1,2),w=Math.floor(c.clientWidth*d),h=Math.floor(c.clientHeight*d);if(c.width!==w||c.height!==h){c.width=w;c.height=h}g.viewport(0,0,w,h);g.enable(g.DEPTH_TEST);g.disable(g.CULL_FACE);g.clearColor(.018,.045,.06,1);g.clear(g.COLOR_BUFFER_BIT|g.DEPTH_BUFFER_BIT);const cp=Math.cos(pitch),e=[center[0]+dist*Math.cos(yaw)*cp,center[1]+dist*Math.sin(pitch),center[2]+dist*Math.sin(yaw)*cp];g.uniformMatrix4fv(ml,false,mul(per(.77,w/h,Math.max(.01,extent*.0001),extent*20+100),look(e,center)));for(const m of M){const textured=m.texture!==null&&m.texture!==undefined&&!!T[m.texture];if(mode==='textured'&&!textured||mode==='gray'&&textured)continue;g.bindBuffer(g.ARRAY_BUFFER,m.p);g.enableVertexAttribArray(pl);g.vertexAttribPointer(pl,3,g.FLOAT,false,0,0);g.bindBuffer(g.ARRAY_BUFFER,m.uv||dummyUV);if(m.uv){g.enableVertexAttribArray(ul);g.vertexAttribPointer(ul,2,g.FLOAT,false,0,0)}else{g.disableVertexAttribArray(ul);g.vertexAttrib2f(ul,0,0)}g.bindBuffer(g.ARRAY_BUFFER,m.n||dummyN);if(m.n){g.enableVertexAttribArray(nl);g.vertexAttribPointer(nl,3,g.FLOAT,false,0,0)}else{g.disableVertexAttribArray(nl);g.vertexAttrib3f(nl,0,1,0)}g.uniform1f(tl,textured?1:0);g.uniform1f(ll,m.n?1:0);if(textured)g.bindTexture(g.TEXTURE_2D,T[m.texture]);g.bindBuffer(g.ELEMENT_ARRAY_BUFFER,m.i);g.drawElements(g.TRIANGLES,m.count,g.UNSIGNED_SHORT,0)}requestAnimationFrame(draw)}c.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];c.setPointerCapture(e.pointerId)});c.addEventListener('pointermove',e=>{if(!drag)return;yaw+=(e.clientX-drag[0])*.008;pitch=Math.max(-1.48,Math.min(1.48,pitch+(e.clientY-drag[1])*.008));drag=[e.clientX,e.clientY]});c.addEventListener('pointerup',()=>drag=null);c.addEventListener('wheel',e=>{e.preventDefault();dist=Math.max(base*.08,Math.min(base*8,dist*Math.exp(e.deltaY*.001)))},{passive:false});for(const id of ['all','textured','gray'])document.getElementById(id).onclick=()=>{mode=id;for(const x of ['all','textured','gray'])document.getElementById(x).classList.toggle('active',x===id)};document.getElementById('reset').onclick=()=>{yaw=-2.35;pitch=.58;dist=base};</script></body></html>'''
    return template.replace('__DATA__', payload).replace('__REPORT__', safe)


def _atomic(path, content, binary=False):
    d=os.path.dirname(os.path.abspath(path)) or '.';os.makedirs(d,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.cell_material_',dir=d)
    try:
        with os.fdopen(fd,'wb' if binary else 'w',encoding=None if binary else 'utf8') as f:f.write(content)
        os.replace(tmp,path)
    except Exception:
        try:os.unlink(tmp)
        except OSError:pass
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base-url',required=True);p.add_argument('--rcf-path',required=True);p.add_argument('--entry',required=True);p.add_argument('--out',required=True);p.add_argument('--preview-html',required=True);p.add_argument('--timeout',type=int,default=240);a=p.parse_args()
    data=_fetch_raw(a.base_url,a.rcf_path,a.entry,a.timeout); display,report=build_preview_data(data,a.entry)
    output={'schema_version':1,'generated_at_utc':dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),'source':{'base_url':a.base_url.rstrip('/'),'rcf_path':a.rcf_path},'report':report}
    _atomic(a.out,json.dumps(output,ensure_ascii=False,indent=2)+'\n');_atomic(a.preview_html,_html(display,report));print(f"core groups={report['core_group_count']} textured={report['textured_68_byte_group_count']} textures={report['decoded_local_color_texture_count']} gray={report['gray_context_group_count']}")

if __name__=='__main__':main()
