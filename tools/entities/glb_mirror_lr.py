#!/usr/bin/env python3
"""Consistently mirror a GLB across the X=0 plane (left/right) in place semantics:

Prototype content targets Unity's left-handed pipeline. Consuming the
right-handed glTF data as-is produces a character whose *named* bones
(`*_L`/`*_R`) sit on the visually opposite side, which makes Unity's
Humanoid auto-mapping assign limbs flipped. This module mirrors every
relevant chunk of the file as one rigid operation so the result stays
internally consistent:

* mesh `POSITION` / `NORMAL` / `TANGENT` (tangent handedness flips too),
* triangle index winding,
* skin `inverseBindMatrices` (`S @ IBM @ S`),
* node `translation` / `rotation` / `matrix`,
* every animation `translation` and `rotation` sampler curve,
* morph-target POSITION/NORMAL deltas when present.

Bone and animation *names* are intentionally untouched.
"""
import json
import struct

import numpy as np

CT_F32 = 5126
CT_U16 = 5123
CT_U32 = 5125

_GLTF_MAGIC = 0x46546C67
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942

_MIRROR = np.diag([-1.0, 1.0, 1.0, 1.0])


def _load_glb(raw):
    magic, version, _length = struct.unpack_from("<III", raw, 0)
    if magic != _GLTF_MAGIC:
        raise ValueError("not a GLB (bad magic)")
    pos = 12
    gltf = None
    binbuf = None
    while pos < len(raw):
        clen, ctype = struct.unpack_from("<II", raw, pos)
        pos += 8
        chunk = raw[pos:pos + clen]
        pos += clen
        if ctype == _CHUNK_JSON:
            gltf = json.loads(chunk.decode("utf-8"))
        elif ctype == _CHUNK_BIN:
            binbuf = bytearray(chunk)
    if gltf is None or binbuf is None:
        raise ValueError("GLB missing JSON or BIN chunk")
    return gltf, binbuf


def _write_glb(path, gltf, binbuf):
    payload = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    bin_out = bytes(binbuf) + b"\x00" * ((4 - len(binbuf) % 4) % 4)
    total = 12 + 8 + len(payload) + 8 + len(bin_out)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", _GLTF_MAGIC, 2, total))
        fh.write(struct.pack("<II", len(payload), _CHUNK_JSON))
        fh.write(payload)
        fh.write(struct.pack("<II", len(bin_out), _CHUNK_BIN))
        fh.write(bin_out)


def mirror_gltf(gltf, binbuf):
    """Mirror gltf dict + BIN bytearray in place. Returns stats dict."""
    accessors = gltf.get("accessors", [])
    views = gltf.get("bufferViews", [])
    stats = {"positions": 0, "normals": 0, "tangents": 0, "winding": 0,
             "ibms": 0, "anim_translation": 0, "anim_rotation": 0,
             "nodes": 0, "morphs": 0}

    def f32_accessor(idx):
        acc = accessors[idx]
        if acc.get("componentType") != CT_F32:
            raise ValueError(f"accessor {idx}: expected float32, got {acc.get('componentType')}")
        ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[acc["type"]]
        view = views[acc["bufferView"]]
        off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride")
        count = acc["count"]
        if stride and stride != ncomp * 4:
            # Interleaved: edit element-by-element through a strided view.
            flat = np.frombuffer(binbuf, dtype=np.float32, count=stride * count // 4, offset=off)
            mat = flat.reshape(count, stride // 4)
            return mat[:, :ncomp], True
        arr = np.frombuffer(binbuf, dtype=np.float32, count=ncomp * count, offset=off)
        return arr.reshape(count, ncomp), False

    def negate_x(idx):
        arr, _strided = f32_accessor(idx)
        arr[:, 0] *= -1.0

    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "POSITION" in attrs:
                negate_x(attrs["POSITION"])
                stats["positions"] += 1
            if "NORMAL" in attrs:
                negate_x(attrs["NORMAL"])
                stats["normals"] += 1
            if "TANGENT" in attrs:
                arr, _ = f32_accessor(attrs["TANGENT"])
                arr[:, 0] *= -1.0
                arr[:, 3] *= -1.0  # mirroring flips tangent handedness
                stats["tangents"] += 1
            if "indices" in prim:
                acc = accessors[prim["indices"]]
                view = views[acc["bufferView"]]
                off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
                dtype = {CT_U16: np.uint16, CT_U32: np.uint32}[acc["componentType"]]
                indices = np.frombuffer(binbuf, dtype=dtype, count=acc["count"], offset=off)
                if acc["count"] % 3 == 0:
                    tris = indices.reshape(-1, 3)
                    indices[:] = tris[:, [0, 2, 1]].reshape(-1)
                    stats["winding"] += 1
            for target in prim.get("targets", []):
                if "POSITION" in target:
                    negate_x(target["POSITION"])
                    stats["morphs"] += 1
                if "NORMAL" in target:
                    negate_x(target["NORMAL"])

    for skin in gltf.get("skins", []):
        if "inverseBindMatrices" not in skin:
            continue
        arr, _ = f32_accessor(skin["inverseBindMatrices"])
        for i in range(arr.shape[0]):
            matrix = arr[i].reshape(4, 4).T  # glTF stores column-major
            matrix = _MIRROR @ matrix @ _MIRROR
            arr[i] = matrix.T.reshape(16)
        stats["ibms"] += 1

    for node in gltf.get("nodes", []):
        if "translation" in node:
            node["translation"][0] = -node["translation"][0]
            stats["nodes"] += 1
        if "rotation" in node:  # glTF order (x, y, z, w): mirror X flips y, z
            rot = node["rotation"]
            rot[1] = -rot[1]
            rot[2] = -rot[2]
        if "matrix" in node:  # column-major
            mat = np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
            node["matrix"] = (_MIRROR @ mat @ _MIRROR).T.reshape(16).tolist()

    for anim in gltf.get("animations", []):
        for channel in anim.get("channels", []):
            path = channel.get("target", {}).get("path")
            sampler = anim["samplers"][channel["sampler"]]
            if path == "translation":
                negate_x(sampler["output"])
                stats["anim_translation"] += 1
            elif path == "rotation":
                arr, _ = f32_accessor(sampler["output"])
                arr[:, 1] *= -1.0  # quaternion y
                arr[:, 2] *= -1.0  # quaternion z
                stats["anim_rotation"] += 1
    return stats


def mirror_file(source, destination):
    with open(source, "rb") as fh:
        raw = fh.read()
    gltf, binbuf = _load_glb(raw)
    stats = mirror_gltf(gltf, binbuf)
    _write_glb(destination, gltf, binbuf)
    return stats


def main():
    import sys
    stats = mirror_file(sys.argv[1], sys.argv[2])
    print("MIRROR_OK", json.dumps(stats))


if __name__ == "__main__":
    main()
