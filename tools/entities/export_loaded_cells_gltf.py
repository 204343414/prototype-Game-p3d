#!/usr/bin/env python3
"""Export validated, already-decodable Manhattan Cell core geometry to glTF.
Extracts full-resolution uncompressed textures from original DDS mips and builds glTF/GLB.
"""
from __future__ import annotations
import argparse, base64, concurrent.futures, json, os, re, struct, sys, urllib.parse, urllib.request
from pathlib import Path

# Insert world & rcf_unpack tools to path
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_REPO, "tools", "rcf_unpack"))
sys.path.insert(0, os.path.join(_REPO, "tools", "world"))
sys.path.insert(0, os.path.join(_REPO, "tools", "entities"))

try:
    import rcf_extract
    from export_static_geometry_diagnostic import scan_core_triangle_geometry, _preview_data
    from cell_materials import attach_local_materials, build_texture_index
    DIRECT_DECODE_AVAILABLE = True
except Exception:
    DIRECT_DECODE_AVAILABLE = False

from export_entity_gltf import Builder, dxt_png, safe, write_glb


def report_progress(completed: int, total: int, stage: str, percent: float | None = None):
    if percent is None:
        percent = (completed / max(1, total)) * 100.0
    payload = json.dumps({"completed": completed, "total": total, "stage": stage, "percent": round(percent, 1)}, ensure_ascii=False)
    print(f"PROGRESS: {payload}", flush=True)


def get_http(url: str, params: dict) -> dict:
    req_url = url + '?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(req_url, timeout=600) as resp:
        return json.load(resp)


def load_shared_index(shared_path: str) -> dict:
    if not shared_path or not os.path.isfile(shared_path):
        return {}
    try:
        archive = rcf_extract.CementFile.load(shared_path)
        combined = {}
        shared_packages = (
            r"\art\locations\manhattan\textures.p3d.rz",
            r"\art\billboards\billboards.p3d.rz",
            r"\art\locations\manhattan_mini\textures.p3d.rz",
            r"\art\locations\manhattan\props.p3d.rz",
        )
        entries_by_name = {}
        for entry in archive.entries:
            meta = archive.get_metadata(entry.name_hash)
            if meta and meta.name:
                entries_by_name[meta.name] = entry

        with open(shared_path, "rb") as f:
            for pkg in shared_packages:
                entry = entries_by_name.get(pkg)
                if entry is not None:
                    try:
                        f.seek(entry.offset)
                        raw = f.read(entry.size)
                        data = rcf_extract.decompress_rz_payload(raw) if raw.startswith(b"RZ") else raw
                        texs = build_texture_index(data, max_edge=8192)
                        for k, v in texs.items():
                            if v is not None and k not in combined:
                                combined[k] = v
                    except Exception:
                        continue

            for name, entry in entries_by_name.items():
                if name.startswith(r"\art\locations\manhattan_mini\manhattan_mini_Cell_") and name.endswith(".p3d.rz"):
                    try:
                        f.seek(entry.offset)
                        raw = f.read(entry.size)
                        data = rcf_extract.decompress_rz_payload(raw) if raw.startswith(b"RZ") else raw
                        texs = build_texture_index(data, max_edge=8192)
                        for k, v in texs.items():
                            if v is not None and k not in combined:
                                combined[k] = v
                    except Exception:
                        continue
        return combined
    except Exception:
        return {}


def decode_single_cell_direct(archive_path: str, entries_by_name: dict, cell: int, shared_index: dict) -> dict:
    name = f"\\art\\locations\\manhattan\\manhattan_Cell_{cell}.p3d.rz"
    entry = entries_by_name.get(name)
    if entry is None:
        return {"cell": cell, "status": "missing", "meshes": [], "textures": [], "report": {}, "materials": {}}
    with open(archive_path, "rb") as f:
        f.seek(entry.offset)
        raw = f.read(entry.size)
    data = rcf_extract.decompress_rz_payload(raw) if raw.startswith(b"RZ") else raw
    groups, report = scan_core_triangle_geometry(data, name)
    if report.get("errors"):
        return {"cell": cell, "status": "error", "error": "unsupported or invalid core geometry", "report": report, "meshes": [], "textures": []}
    preview = _preview_data(groups)
    meshes = preview["meshes"]
    textures, mat_report = attach_local_materials(data, groups, meshes, shared_index, max_edge=8192)
    return {
        "cell": cell,
        "status": "ready" if groups else "empty",
        "meshes": meshes,
        "textures": textures,
        "report": report,
        "materials": mat_report,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--server', default='http://127.0.0.1:8421')
    p.add_argument('--archive', required=True)
    p.add_argument('--shared', default='')
    p.add_argument('--cells', required=True, help='comma/range list, or all')
    p.add_argument('--output', required=True)
    p.add_argument('--name', default='loaded-manhattan-cells')
    a = p.parse_args()

    if a.cells == 'all':
        cells = list(range(260))
    else:
        cells = []
        for x in a.cells.split(','):
            q = x.split('-', 1)
            cells.extend(range(int(q[0]), int(q[-1]) + 1))
    cells = sorted(set(cells))

    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    img_dir = out / 'textures'
    img_dir.mkdir(exist_ok=True)

    b = Builder(a.name)
    tex_by_key = {}
    tex_filenames = {}
    used_filenames = set()
    mats = {}
    reports = []
    loaded = []

    total_cells = len(cells)
    report_progress(0, total_cells, f"准备解析 {total_cells} 个区块...", 5.0)

    # Check if direct archive reading can be used for speed
    use_direct = DIRECT_DECODE_AVAILABLE and os.path.isfile(a.archive)
    cell_results = {}

    if use_direct:
        report_progress(0, total_cells, "正在读取 RCF 归档索引与共享材质库...", 10.0)
        shared_index = load_shared_index(a.shared)
        archive = rcf_extract.CementFile.load(a.archive)
        entries_by_name = {}
        for entry in archive.entries:
            meta = archive.get_metadata(entry.name_hash)
            if meta and meta.name:
                entries_by_name[meta.name] = entry

        report_progress(0, total_cells, f"正在并行解析 {total_cells} 个区块几何与无损材质...", 15.0)
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, os.cpu_count() or 4)) as executor:
            future_to_cell = {
                executor.submit(decode_single_cell_direct, a.archive, entries_by_name, cell, shared_index): cell
                for cell in cells
            }
            completed_count = 0
            for future in concurrent.futures.as_completed(future_to_cell):
                cell = future_to_cell[future]
                completed_count += 1
                try:
                    res = future.result()
                    cell_results[cell] = res
                except Exception as exc:
                    cell_results[cell] = {"cell": cell, "status": "error", "error": str(exc), "meshes": [], "textures": []}
                pct = 15.0 + (completed_count / max(1, total_cells)) * 45.0
                if completed_count % 5 == 0 or completed_count == total_cells:
                    report_progress(completed_count, total_cells, f"正在解析区块几何 ({completed_count}/{total_cells})...", pct)
    else:
        # Fallback to HTTP API
        for idx, cell in enumerate(cells):
            d = get_http(a.server + '/api/rcf_cell_preview', {'path': a.archive, 'cell': cell, 'materials': 1, 'shared_path': a.shared})
            cell_results[cell] = d
            pct = 10.0 + ((idx + 1) / max(1, total_cells)) * 50.0
            report_progress(idx + 1, total_cells, f"正在从服务器获取区块 {cell} ({idx+1}/{total_cells})...", pct)

    # Collect all textures across all cells to decode and deduplicate
    report_progress(total_cells, total_cells, "正在提取并转换原版无损贴图...", 62.0)
    all_textures = {}
    for cell in cells:
        d = cell_results.get(cell, {})
        for t in d.get('textures', []):
            k = t.get('key')
            if k and k not in all_textures:
                all_textures[k] = t

    unique_textures = list(all_textures.values())
    total_tex = len(unique_textures)

    tex_manifest_entries = []
    for tex_idx, t in enumerate(unique_textures):
        k = t['key']
        if k in tex_by_key:
            continue
        mip = t['mips'][0]  # Full-resolution top mip level (uncompressed native resolution)
        raw_mip_data = base64.b64decode(mip['data']) if isinstance(mip['data'], str) else mip['data']
        png_bytes = dxt_png(t['format'], mip['width'], mip['height'], raw_mip_data)

        stem = safe(Path(t.get('name') or k).stem)
        fn = stem + '.png'
        n = 2
        while fn.lower() in used_filenames:
            fn = f"{stem}_{n}.png"
            n += 1
        used_filenames.add(fn.lower())
        tex_filenames[k] = fn
        (img_dir / fn).write_bytes(png_bytes)

        b.g['images'].append({'name': t.get('name') or k, 'uri': 'textures/' + fn})
        b.g['textures'].append({'source': len(b.g['images']) - 1, 'name': t.get('name') or k})
        tex_by_key[k] = len(b.g['textures']) - 1

        tex_manifest_entries.append({
            'file': fn,
            'sourceName': t.get('name') or k,
            'pngSize': f"{mip['width']}x{mip['height']}",
            'width': mip['width'],
            'height': mip['height'],
            'originalDdsSize': f"{t.get('original_width', mip['width'])}x{t.get('original_height', mip['height'])}",
            'ddsFormat': t['format'],
            'mipLevelsKeptFromSource': len(t.get('mips', [])),
            'sha256': k,
        })

        if (tex_idx + 1) % 10 == 0 or (tex_idx + 1) == total_tex:
            pct = 62.0 + ((tex_idx + 1) / max(1, total_tex)) * 18.0
            report_progress(tex_idx + 1, total_tex, f"正在生成无损贴图 ({tex_idx+1}/{total_tex})...", pct)

    # Texture resolution manifest: verifiable proof that no downscaling happened.
    size_buckets = {}
    for entry in tex_manifest_entries:
        bucket = entry['pngSize']
        size_buckets[bucket] = size_buckets.get(bucket, 0) + 1
    size_buckets = dict(sorted(size_buckets.items(), key=lambda kv: (
        -int(kv[0].split('x')[0]) * int(kv[0].split('x')[1]), -int(kv[0].split('x')[0])))
    ) if size_buckets else {}
    manifest_doc = {
        'name': a.name,
        'pipeline': 'RCF -> DDS (native full mip chain, max_edge=8192) -> DXT decode -> PNG (no resizing anywhere)',
        'textureCount': len(tex_manifest_entries),
        'maxTextureSize': (
            f"{max(e['width'] for e in tex_manifest_entries)}x{max(e['height'] for e in tex_manifest_entries)}"
            if tex_manifest_entries else 'none'
        ),
        'sizeBuckets': size_buckets,
        'textures': tex_manifest_entries,
    }
    (out / 'textures-manifest.json').write_text(json.dumps(manifest_doc, ensure_ascii=False, indent=2))

    # Build glTF mesh nodes
    report_progress(total_cells, total_cells, "正在组装 glTF 场景与网格数据...", 82.0)
    for cell in cells:
        d = cell_results.get(cell, {})
        reports.append({
            'cell': cell,
            'status': d.get('status'),
            'geometryReport': d.get('report'),
            'materialReport': d.get('materials'),
        })
        if d.get('error'):
            continue
        if d.get('status') != 'ready':
            continue

        cell_node = {
            'name': f'Cell {cell}',
            'children': [],
            'extras': {'sourceCell': cell, 'sourceArchive': a.archive},
        }
        b.g['nodes'].append(cell_node)
        ci = len(b.g['nodes']) - 1
        b.g['scenes'][0]['nodes'].append(ci)

        for m in d.get('meshes', []):
            pb = base64.b64decode(m['p'])
            v = struct.unpack('<%df' % (len(pb) // 4), pb)
            attrs = {
                'POSITION': b.acc(
                    pb, 5126, 'VEC3', len(v) // 3, 34962,
                    [min(v[i::3]) for i in range(3)],
                    [max(v[i::3]) for i in range(3)],
                )
            }
            ub = base64.b64decode(m.get('uv') or '')
            if ub:
                attrs['TEXCOORD_0'] = b.acc(ub, 5126, 'VEC2', len(ub) // 8, 34962)
            ib = base64.b64decode(m['i'])
            ia = b.acc(ib, 5123, 'SCALAR', len(ib) // 2, 34963)
            key = m.get('texture')
            if key not in mats:
                mat = {
                    'name': key or 'unresolved material',
                    'pbrMetallicRoughness': {'metallicFactor': 0, 'roughnessFactor': 0.9},
                    'doubleSided': True,
                    'extras': {'sourceTextureKey': key},
                }
                if key in tex_by_key:
                    mat['pbrMetallicRoughness']['baseColorTexture'] = {'index': tex_by_key[key]}
                b.g['materials'].append(mat)
                mats[key] = len(b.g['materials']) - 1

            prim = {'attributes': attrs, 'indices': ia, 'material': mats[key], 'mode': 4}
            b.g['meshes'].append({
                'name': m['geometry'],
                'primitives': [prim],
                'extras': {
                    'sourceEntry': m.get('entry'),
                    'sourceGroup': m.get('group'),
                    'shaderTemplate': m.get('shader_template'),
                    'materialClass': m.get('material_class'),
                    'textureSource': m.get('texture_source'),
                    'textureParameter': m.get('texture_parameter'),
                },
            })
            b.g['nodes'].append({'name': f'{m["geometry"]} group {m["group"]}', 'mesh': len(b.g['meshes']) - 1})
            cell_node['children'].append(len(b.g['nodes']) - 1)
        loaded.append(cell)

    report_progress(total_cells, total_cells, "正在输出 GLB 与 glTF 模型文件...", 88.0)
    b.g['asset']['extras'] = {
        'scope': 'validated merged Manhattan Cell core triangles and resolved uncompressed color textures only',
        'completeMap': False,
        'reason': 'placed models, effects, collision and other source record classes are not yet decoded',
    }
    b.g['buffers'][0]['byteLength'] = len(b.bin)
    base = safe(a.name)
    b.g['buffers'][0]['uri'] = base + '.bin'
    (out / (base + '.bin')).write_bytes(b.bin)
    (out / (base + '.gltf')).write_text(json.dumps(b.g, ensure_ascii=False, indent=2))
    write_glb(b.g, b.bin, out / (base + '.glb'), out)

    (out / 'unsupported-records.json').write_text(json.dumps({
        'completeMap': False,
        'requestedCells': cells,
        'loadedCells': loaded,
        'knownUnsupported': [
            'placed model references and transforms',
            'effects',
            'collision and navigation',
            'non-core geometry groups rejected by strict decoder',
        ],
        'cellReports': reports,
    }, ensure_ascii=False, indent=2))

    report_progress(total_cells, total_cells, "glTF/GLB 生成完成", 90.0)
    print(out / (base + '.gltf'))


if __name__ == '__main__':
    main()
