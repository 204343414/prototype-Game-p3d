"""Attach evidenced local color textures to already validated world-core groups.

DDS blocks remain compressed for WebGL S3TC upload; no image decoder dependency,
no guessing UV layouts; shared textures require exact names, not fuzzy fallback.
"""
import base64
import hashlib
import math
import struct
from collections import Counter

from probe_static_geometry import GEOMETRY, PRIMITIVE_GROUP, MEMORY_VERTEX_LIST, _p3d_string
from probe_cell_shader_dependencies import (
    NEW_SHADER, NEW_SHADER_STRING_PARAMETER, _parse_new_shader_header, _parse_exact_string_pair,
)
from render_static_uv_candidates import (
    TEXTURE, TEXTURE_DDS, IMAGE_DATA, _records, _descendants, _parse_texture_dds_header,
)

COLOR_LAYOUT = "4f18391af9d6a09508392a25fdb0db6533f3cf2e6c00a73b806ade842c356316"


def compressed_texture(image, expected, max_edge=256):
    """Validate DDS framing and preserve a bounded subset of original mip levels."""
    if len(image) < 132 or struct.unpack_from('<I', image)[0] != len(image) - 4:
        raise ValueError('invalid count-prefixed DDS length')
    dds = image[4:]
    if dds[:4] != b'DDS ' or struct.unpack_from('<I', dds, 4)[0] != 124:
        raise ValueError('invalid DDS header')
    height, width = struct.unpack_from('<II', dds, 12)
    mips = max(1, struct.unpack_from('<I', dds, 28)[0])
    algorithm = dds[84:88].decode('ascii', 'replace')
    if (width, height, mips, algorithm) != expected:
        raise ValueError('TextureDDS and DDS header disagree')
    if algorithm not in ('DXT1', 'DXT5'):
        raise ValueError('unverified compression format')
    if not all(0 < n <= 8192 and n & (n - 1) == 0 for n in (width, height)):
        raise ValueError('unsupported texture dimensions')
    if mips > max(width, height).bit_length():
        raise ValueError('invalid mip count')
    if struct.unpack_from('<I', dds, 76)[0] != 32 or not struct.unpack_from('<I', dds, 80)[0] & 4:
        raise ValueError('invalid compressed DDS pixel format')
    if struct.unpack_from('<I', dds, 112)[0] != 0:
        raise ValueError('cube/volume DDS is not a color texture')
    block_size = 8 if algorithm == 'DXT1' else 16
    offset, w, h = 128, width, height
    selected = []
    byte_count = 0
    for _ in range(mips):
        length = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block_size
        if offset + length > len(dds):
            raise ValueError('truncated DDS mip chain')
        if max(w, h) <= max_edge:
            selected.append({'width': w, 'height': h, 'data': base64.b64encode(dds[offset:offset + length]).decode('ascii')})
            byte_count += length
        offset += length
        w, h = max(1, w // 2), max(1, h // 2)
    if offset != len(dds):
        raise ValueError('unexpected DDS trailing bytes')
    if not selected:
        raise ValueError('no source mip within preview texture budget')
    return {'key': hashlib.sha256(dds).hexdigest(), 'format': algorithm, 'mips': selected,
            'bytes': byte_count, 'original_width': width, 'original_height': height}


def _read_texture(records, children, index, color):
    descendants = [r for _, r in _descendants(index, children)]
    headers = [r for r in descendants if r['type_id'] == TEXTURE_DDS]
    images = [r for r in descendants if r['type_id'] == IMAGE_DATA]
    if len(headers) != 1 or len(images) != 1:
        raise ValueError('ambiguous or missing DDS payload')
    name, w, h, mips, algorithm = _parse_texture_dds_header(headers[0]['payload'])
    if name != color:
        raise ValueError('TextureDDS name mismatch')
    return compressed_texture(images[0]['payload'], (w, h, mips, algorithm))


def build_texture_index(data):
    records, children = _records(data)
    textures = {}
    for index, record in enumerate(records):
        if record['type_id'] != TEXTURE:
            continue
        name = _p3d_string(record['payload'])[0]
        if name in textures:
            textures[name] = None
            continue
        try:
            textures[name] = _read_texture(records, children, index, name)
        except (ValueError, struct.error):
            textures[name] = None
    return textures


def attach_local_materials(data, groups, meshes, shared_textures=None):
    records, children = _records(data)
    shaders, textures, vertex_lists = {}, {}, {}
    for index, record in enumerate(records):
        kind = record['type_id']
        if kind == NEW_SHADER:
            try:
                name = _parse_new_shader_header(record['payload'])['shader_name']
                colors = []
                for _, child in children.get(index, []):
                    if child['type_id'] == NEW_SHADER_STRING_PARAMETER:
                        key, value = _parse_exact_string_pair(child['payload'], 'NewShader parameter')
                        if key == 'color' and value:
                            colors.append(value)
                color = colors[0] if len(colors) == 1 else None
                shaders[name] = color if name not in shaders or shaders[name] == color else None
            except (ValueError, struct.error):
                continue
        elif kind == TEXTURE:
            try:
                name = _p3d_string(record['payload'])[0]
                textures[name] = index if name not in textures else None
            except ValueError:
                continue
        elif kind == GEOMETRY:
            name = _p3d_string(record['payload'])[0]
            ordinal = 0
            for pi, child in children.get(index, []):
                if child['type_id'] != PRIMITIVE_GROUP:
                    continue
                ordinal += 1
                vl = [r['payload'] for _, r in children.get(pi, []) if r['type_id'] == MEMORY_VERTEX_LIST]
                if len(vl) == 1:
                    vertex_lists[(name, ordinal)] = vl[0]

    resolved, packed = {}, {}
    reasons = Counter()
    linked = 0
    shared_groups = 0
    for group, mesh in zip(groups, meshes):
        mesh['texture'] = None
        if group.vertex_stride != 68 or group.layout_sha256 != COLOR_LAYOUT:
            reasons['unverified_uv_layout'] += 1
            continue
        color = shaders.get(group.shader_name)
        if not color:
            reasons['missing_or_ambiguous_color_binding'] += 1
            continue
        if color not in resolved:
            try:
                if color in textures:
                    index = textures[color]
                    if index is None:
                        raise ValueError('ambiguous local Texture')
                    texture = _read_texture(records, children, index, color)
                else:
                    texture = (shared_textures or {}).get(color)
                    if texture is None:
                        raise ValueError('missing exact shared Texture')
                packed[texture['key']] = texture
                resolved[color] = texture['key']
            except (ValueError, struct.error):
                resolved[color] = None
        key = resolved[color]
        if key is None:
            reasons['unresolved_or_unsupported_local_texture'] += 1
            continue
        payload = vertex_lists[(group.geometry_name, group.group_ordinal)]
        uv = bytearray(group.vertex_count * 8)
        valid = True
        for i in range(group.vertex_count):
            u, v = struct.unpack_from('<2f', payload, 12 + i * 68 + 24)
            if not math.isfinite(u) or not math.isfinite(v):
                valid = False
                break
            struct.pack_into('<2f', uv, i * 8, u, v)
        if not valid:
            reasons['invalid_uv_values'] += 1
            continue
        origin = 'local' if color in textures else 'shared'
        shared_groups += origin == 'shared'
        mesh.update(texture=key, texture_source=origin, uv=base64.b64encode(uv).decode('ascii'))
        linked += 1
    used = {mesh['texture'] for mesh in meshes if mesh['texture']}
    packed = [value for key, value in packed.items() if key in used]
    return packed, {'linked_groups': linked, 'shared_groups': shared_groups, 'gray_groups': len(meshes) - linked,
                    'gray_reasons': dict(reasons), 'texture_count': len(packed),
                    'compressed_texture_bytes': sum(t['bytes'] for t in packed),
                    'scope': 'exact verified 68-byte layout, local/exact shared color DXT1/DXT5; source mip <=256; no shader/normal map/UV flip inference'}
