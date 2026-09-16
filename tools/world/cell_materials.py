"""Attach evidenced base textures to already validated world-core groups.

DDS blocks remain compressed for WebGL S3TC upload; no image decoder dependency,
no guessing UV layouts; shared textures require exact names, not fuzzy fallback.
All UV streams and parameter bindings are derived from binary vertex declarations
and NewShader chunk parameters.
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

# Verified Vertex Declarations
LAYOUT_68_STD = "4f18391af9d6a09508392a25fdb0db6533f3cf2e6c00a73b806ade842c356316"
LAYOUT_68_HIVE = "e6c6bff84a3eda5444ab413fba598076633aeb0f74f84af9848708bccc164311"
LAYOUT_64_INT = "3c20bc994be09119067e505e093e67e163b38ae75c310ba90c48de6adf7240c9"
LAYOUT_60_TERRAIN = "ec4f54255db1a23a9c9837079fd748cece0992a7366b1bde1d1d8a7ac01aefb7"
LAYOUT_56_REFLECT = "fb2000cf4a1d47101cfadab7fa8a2be630e6ef5817a3a3d6cb46e9df51ceec69"
LAYOUT_52_ROAD = "2bdeba9cb4f0ddf6b8ed4fc00d04003b9d691b3297b7b0d2cd96cb9874a2cc53"
LAYOUT_52_PROPS = "930b61a454caf459554484a080866bfcc70ffd3898eeb43901e4405c7d4a9953"
LAYOUT_48_HIVE = "dfe0a2c2f6788e64e7f6ccf69a39eb43a066938768c2c49df18d3e6b1f35dd42"
LAYOUT_44_LIT = "b185657b0e13eee1e1b276eb35aaea8f3fa14754aa5107be54f24846c3d8a062"
LAYOUT_44_ALPHA = "e9cdf8f352a1ba2e1ef2c02c610e74f14ef96d669e0ee9b5da5459392e273f08"
LAYOUT_36_PROPS = "684f7ea2758fadf01825ca7d3d43c1791f1b4c08f0deb43c6bad11c279860d82"
LAYOUT_32_DECAL = "8c718e7ca0d35395bd5607c9399588cde7ddc1042040d6cbef4ac4b7e1ef4172"
LAYOUT_28_DECAL = "31f1405229547d7c67c8227bda07aa22a762c2f6ea60aa88383377da240b9044"
LAYOUT_76_SIDEWALK = "214be15fd9b2c18aed0f27868b1e6d59dee5121f6c5a8cb0ee5f266b33f34059"

# Backward compatibility aliases
COLOR_LAYOUT = LAYOUT_68_STD
ROAD_LAYOUT = LAYOUT_52_ROAD
ROAD_TEMPLATE = "zCBV2_env_road"
DECAL_LAYOUT = LAYOUT_32_DECAL
DECAL_TEMPLATES = frozenset(('env_decal', 'env_decal_nm', 'env_decal_bias_nm', 'env_decal_bias', 'env_decal_cube_reflect'))
INTERIOR_LAYOUT = LAYOUT_64_INT
INTERIOR_TEMPLATES = frozenset((
    'env_building_noglass', 'ao_nis', 'ao_building_noglass', 'env_color_noglass',
    'env_color_glass', 'env_building_glass_grime'
))

SUPPORTED_PARAM_KEYS = ('color', 'bottom', 'top', 'ImageMap', 'add_color', 'cube_map')


def classify_material_group(group, template, parameter, textured):
    """Assign an evidence label without claiming gameplay/state semantics."""
    name = f'{group.geometry_name} {template or ""}'.casefold()
    if template == ROAD_TEMPLATE:
        return 'road_surface'
    if 'sidewalk' in name:
        return 'sidewalk'
    if 'decal' in name or group.layout_sha256 in (LAYOUT_32_DECAL, LAYOUT_28_DECAL):
        return 'ground_decal_or_overlay'
    if not textured:
        return 'untextured_unknown'
    if 'building' in name or 'env_' in name:
        return 'textured_environment'
    return 'textured_other'


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
    if algorithm not in ('DXT1', 'DXT3', 'DXT5'):
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
                header = _parse_new_shader_header(record['payload'])
                name = header['shader_name']
                parameters = {k: [] for k in SUPPORTED_PARAM_KEYS}
                for _, child in children.get(index, []):
                    if child['type_id'] == NEW_SHADER_STRING_PARAMETER:
                        key, value = _parse_exact_string_pair(child['payload'], 'NewShader parameter')
                        if key in parameters:
                            parameters[key].append(value)
                bindings = {key: values[0] if len(values) == 1 else None for key, values in parameters.items()}
                shader = (header['template_name'], bindings)
                shaders[name] = shader if name not in shaders or shaders[name] == shader else None
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
    road_groups = 0
    decal_groups = 0
    interior_groups = 0

    for group, mesh in zip(groups, meshes):
        mesh['texture'] = None
        shader = shaders.get(group.shader_name)
        template = shader[0] if shader else None
        bindings = shader[1] if shader else {}
        stride = group.vertex_stride
        layout_sha = group.layout_sha256
        mesh['shader_template'] = template
        mesh['material_class'] = classify_material_group(group, template, None, False)
        mode = None

        # Determine exact texture parameter & UV offset from vertex declaration semantics
        if stride == 68 and layout_sha == LAYOUT_68_STD:
            parameter, uv_offset = 'color', 24
        elif stride == 68 and layout_sha == LAYOUT_68_HIVE:
            parameter, uv_offset = 'color', 24
        elif stride == 64 and layout_sha == LAYOUT_64_INT:
            parameter, uv_offset = 'color', 20
            if template not in INTERIOR_TEMPLATES:
                reasons['unverified_shader_template'] += 1
                continue
        elif stride == 60 and layout_sha == LAYOUT_60_TERRAIN:
            if template == 'zCBV2_env_terrain':
                parameter = 'bottom' if bindings.get('bottom') else ('top' if bindings.get('top') else 'color')
            else:
                parameter = 'color'
            uv_offset = 24
        elif stride == 56 and layout_sha == LAYOUT_56_REFLECT:
            parameter, uv_offset = 'color', 20
        elif stride == 52 and layout_sha == LAYOUT_52_ROAD:
            if template == 'zCBV2_env_road':
                parameter, uv_offset = 'bottom', 32
            elif template == 'zCBV2_env_grass':
                parameter, uv_offset = 'bottom', 24
            else:
                parameter, uv_offset = 'color', 24
        elif stride == 52 and layout_sha == LAYOUT_52_PROPS:
            parameter, uv_offset = 'color', 16
        elif stride == 48 and layout_sha == LAYOUT_48_HIVE:
            parameter, uv_offset = ('bottom', 20) if template == 'zCBV2_env_road' else ('color', 20)
        elif stride == 44 and layout_sha == LAYOUT_44_LIT:
            parameter, uv_offset = 'color', 16
        elif stride == 44 and layout_sha == LAYOUT_44_ALPHA:
            parameter = 'ImageMap' if bindings.get('ImageMap') else 'color'
            uv_offset = 24
            mode = 'source_alpha'
        elif stride == 36 and layout_sha == LAYOUT_36_PROPS:
            parameter = 'color' if bindings.get('color') else 'cube_map'
            uv_offset = 16
        elif stride == 32 and layout_sha == LAYOUT_32_DECAL:
            parameter, uv_offset = 'color', 24
            if template not in DECAL_TEMPLATES:
                reasons['unverified_shader_template'] += 1
                continue
            mode = 'source_alpha'
        elif stride == 28 and layout_sha == LAYOUT_28_DECAL:
            parameter = 'color' if bindings.get('color') else 'add_color'
            uv_offset = 20
            mode = 'source_alpha'
        elif stride == 76 and layout_sha == LAYOUT_76_SIDEWALK:
            parameter, uv_offset = 'color', 24
        else:
            reasons['unverified_uv_layout'] += 1
            continue

        color = bindings.get(parameter) if shader is not None else None
        if not color:
            reasons[f'missing_or_ambiguous_{parameter}_binding'] += 1
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

        payload = vertex_lists.get((group.geometry_name, group.group_ordinal))
        if not payload:
            reasons['missing_vertex_list'] += 1
            continue

        uv = bytearray(group.vertex_count * 8)
        valid = True
        for i in range(group.vertex_count):
            u, v = struct.unpack_from('<2f', payload, 12 + i * stride + uv_offset)
            if not math.isfinite(u) or not math.isfinite(v):
                valid = False
                break
            struct.pack_into('<2f', uv, i * 8, u, v)

        if not valid:
            reasons['invalid_uv_values'] += 1
            continue

        origin = 'local' if color in textures else 'shared'
        shared_groups += origin == 'shared'
        road_groups += parameter == 'bottom'
        interior_groups += layout_sha in (LAYOUT_64_INT, LAYOUT_68_HIVE, LAYOUT_48_HIVE)
        if mode:
            mesh['preview_render_mode'] = mode
        if layout_sha in (LAYOUT_32_DECAL, LAYOUT_28_DECAL):
            decal_groups += 1

        mesh.update(texture=key, texture_source=origin, texture_parameter=parameter,
                    material_class=classify_material_group(group, template, parameter, True),
                    shader_template=template,
                    uv=base64.b64encode(uv).decode('ascii'))
        linked += 1

    used = {mesh['texture'] for mesh in meshes if mesh['texture']}
    packed = [value for key, value in packed.items() if key in used]
    class_counts = Counter(mesh.get('material_class', 'unclassified') for mesh in meshes)
    return packed, {
        'linked_groups': linked, 'shared_groups': shared_groups, 'road_groups': road_groups,
        'material_class_counts': dict(sorted(class_counts.items())), 'decal_groups': decal_groups,
        'interior_groups': interior_groups,
        'gray_groups': len(meshes) - linked,
        'gray_reasons': dict(reasons), 'texture_count': len(packed),
        'compressed_texture_bytes': sum(t['bytes'] for t in packed),
        'scope': 'code-derived vertex declarations (68/64/60/56/52/48/44/36/32/28/76B) and exact shader parameter references; local/shared DXT1/DXT3/DXT5; source mip <=256; no grime/specular/normal map inference'
    }
