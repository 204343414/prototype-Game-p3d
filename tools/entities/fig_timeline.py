#!/usr/bin/env python3
"""Move timeline + audio event access built on the byte-verified FIG grammar.

Everything served from here is backed by the archaeology reports in audit/:
  - FIG node grammar and 64-bit name hash (archaeology-fig-grammar-decode.json)
  - effect path resolution rule (archaeology-fig-effect-path-resolution.json)
  - RADP codec layout (matches vgmstream decode_rad_ima_mono)

Field layouts used (only the proven subset):
  animation node value: f32 t@+4, u64 anim_name_hash@+12, f32 start_frame@+32,
      f32 end_frame@+36 (negative end = "to animation end"; kept raw).
  sound node value:     f32 t@+4, u64 event_name_hash@+8.
  spawn/execute value:  f32 t@+4, f32 t_end@+8, u32 path_len@+12, ASCII path.
  hit / cameraShakeRequest: f32 t@+4.
  motionTrail value:    u64 bone_name_hash@+52.
  effect value:         u64 emitter_hash@+12, space hash@+28, bone hash@+36.
Times are stored relative to their node (stage); absolute scheduling adds the
stage start_frame / fps. Unknown fields are never invented.
"""
from __future__ import annotations

import os
import re
import struct
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_ROOT, "tools", "rcf_unpack"))
sys.path.insert(0, os.path.join(_ROOT, "tools", "archaeology"))
import rcf_extract  # noqa: E402
from scan_exact_token_references import walk_p3d  # noqa: E402

_LOCK = threading.Lock()
_CACHE: dict = {}

FIG_PARENT = 0x20000701
FIG_CHILD = 0x20000702


def hash64(data: bytes) -> int:
    h = 0
    for c in data:
        signed = c if c < 128 else c - 256
        h = ((h * 0x1003F) ^ signed) & 0xFFFFFFFFFFFFFFFF
    return h


def _load_entry(rcf_path: str, exact_name: str) -> bytes:
    cement = rcf_extract.CementFile.load(rcf_path)
    nh = rcf_extract.hash_file_name(exact_name)
    entries = [e for e in cement.entries if e.name_hash == nh]
    if len(entries) != 1:
        raise ValueError("expected 1 entry for %r, got %d" % (exact_name, len(entries)))
    with open(rcf_path, "rb") as fh:
        fh.seek(entries[0].offset)
        raw = fh.read(entries[0].size)
    if exact_name.endswith(".rz"):
        return rcf_extract.decompress_rz_payload(raw)
    return raw


class _Node:
    __slots__ = ("key", "size", "voff", "off", "children")

    def __init__(self, key, size, voff, off):
        self.key, self.size, self.voff, self.off = key, size, voff, off
        self.children = []


def _parse_blob(blob: bytes):
    def parse_list(pos, end, depth):
        nodes = []
        while pos < end:
            key = struct.unpack_from("<Q", blob, pos)[0]
            if key == 0:
                return nodes, pos + 8, True
            size = struct.unpack_from("<I", blob, pos + 8)[0]
            if pos + 12 + size > end or depth > 300:
                return nodes, pos, False
            node = _Node(key, size, pos + 12, pos)
            kids, nxt, closed = parse_list(pos + 12 + size, end, depth + 1)
            node.children = kids
            nodes.append(node)
            if not closed:
                return nodes, nxt, False
            pos = nxt
        return nodes, pos, True

    roots, fin, ok = parse_list(12, len(blob), 0)
    if not (ok and fin == len(blob)):
        raise ValueError("FIG blob failed strict grammar parse")
    return roots


def _fig_blocks(data: bytes) -> dict:
    chunks = walk_p3d(data)
    out = {}
    for p in (c for c in chunks if c.type_id == FIG_PARENT):
        hdr = data[p.offset + 12:p.offset + p.header_size]
        token = None
        if hdr:
            ln = hdr[0]
            if 0 < ln < 64 and 1 + ln <= len(hdr):
                seg = hdr[1:1 + ln].split(b"\x00", 1)[0]
                if seg and all(32 <= b < 127 for b in seg):
                    token = seg.decode()
        kids = [c for c in chunks
                if c.type_id == FIG_CHILD and c.depth == p.depth + 1
                and p.offset < c.offset and c.end <= p.end]
        if token and kids:
            k = kids[0]
            bsz = struct.unpack_from("<I", data, k.offset + 12)[0]
            out[token] = data[k.offset + 16:k.offset + 16 + bsz]
    return out


_ALEX_BANKS = [r"audio\alex.p3d", r"audio\alex_hammerfist.p3d", r"audio\alex_blades.p3d",
               r"audio\alex_claws.p3d", r"audio\alex_whipfist.p3d"]


def _state(game_root: str):
    with _LOCK:
        st = _CACHE.get(game_root)
        if st is not None:
            return st
        art = os.path.join(game_root, "art.rcf")
        startup = _load_entry(art, "\\art\\startup_fig.p3d.rz")
        alexfig = _load_entry(art, "\\art\\alex\\alex_fig.p3d.rz")
        blocks = _fig_blocks(startup)
        for tok, blob in _fig_blocks(alexfig).items():
            blocks.setdefault(tok, blob)
        parsed = {tok: _parse_blob(blob) for tok, blob in blocks.items()}

        # animation frame counts across all art P3Ds (name-hash keyed)
        frames_of = {}
        cement = rcf_extract.CementFile.load(art)
        with open(art, "rb") as fh:
            for md in cement.metadatas:
                if not md.name.endswith(".p3d.rz"):
                    continue
                nh = rcf_extract.hash_file_name(md.name)
                es = [e for e in cement.entries if e.name_hash == nh]
                if len(es) != 1:
                    continue
                fh.seek(es[0].offset)
                try:
                    data = rcf_extract.decompress_rz_payload(fh.read(es[0].size))
                except Exception:
                    continue
                i = 0
                while True:
                    i = data.find(b"\x00\x10\x12\x00", i + 1)
                    if i < 0:
                        break
                    cid, hs, ts = struct.unpack_from("<III", data, i)
                    if cid == 0x00121000 and 12 < hs < 200 and ts >= hs:
                        ln = data[i + 16]
                        nm = data[i + 17:i + 17 + ln].rstrip(b"\x00")
                        if nm and all(32 <= b < 127 for b in nm):
                            try:
                                fr, rate = struct.unpack_from("<ff", data, i + 17 + ln + 4)
                                if 0 < fr < 20000 and 0 < rate <= 120:
                                    frames_of[hash64(bytes(nm))] = (nm.decode(), fr, rate)
                            except struct.error:
                                pass

        # name dictionary for hashes we may emit (audio events via Patch names,
        # emitters, bones, misc)
        names: dict[int, str] = {}
        aud = os.path.join(game_root, "00audio.rcf")
        cema = rcf_extract.CementFile.load(aud)
        patch_pat = re.compile(rb"(?:Patch|AudioFile)\x00[\x01\x02]\x00\x00\x00(.{4})", re.S)
        bank_bytes = {}
        with open(aud, "rb") as fh:
            for bname in _ALEX_BANKS:
                nh = rcf_extract.hash_file_name(bname)
                es = [e for e in cema.entries if e.name_hash == nh]
                if len(es) != 1:
                    continue
                fh.seek(es[0].offset)
                data = fh.read(es[0].size)
                bank_bytes[bname] = data
                for m in patch_pat.finditer(data):
                    ln = struct.unpack("<I", m.group(1))[0]
                    if 0 < ln < 96:
                        s = data[m.end():m.end() + ln]
                        if s and all(32 <= b < 127 for b in s):
                            names[hash64(bytes(s))] = s.decode()
        effects_p3d = _load_entry(art, "\\art\\startup_effects.p3d.rz")
        for ch in walk_p3d(effects_p3d):
            if ch.type_id == 0x00023000 and ch.depth == 0:
                off = ch.offset + 12
                ln = effects_p3d[off]
                if 0 < ln < 64:
                    s = effects_p3d[off + 1:off + 1 + ln].rstrip(b"\x00")
                    if s and all(32 <= b < 127 for b in s):
                        names[hash64(bytes(s))] = s.decode()
        # motion-trail definition objects: 0x11015 shader chunks named
        # motionTrail* -> blend template + .dds texture name; 0x19000 texture
        # chunks hold the embedded DDS bytes.
        trail_defs = {}
        tex_chunks = {}
        for ch in walk_p3d(effects_p3d):
            if ch.type_id == 0x00011015 and ch.depth == 0:
                off = ch.offset + 12
                ln = effects_p3d[off]
                if 0 < ln < 64:
                    nm = effects_p3d[off + 1:off + 1 + ln].rstrip(b"\x00")
                    body = effects_p3d[ch.offset:ch.offset + ch.total_size]
                    texm = re.search(rb"([\x20-\x7e]{3,40}\.dds)", body)
                    tmplm = re.search(rb"fx_[a-z_]+", body)
                    if nm and texm:
                        trail_defs[hash64(bytes(nm))] = {
                            "name": nm.decode(),
                            "texture": texm.group(1).decode(),
                            "template": tmplm.group(0).decode() if tmplm else None,
                        }
                        names[hash64(bytes(nm))] = nm.decode()
            elif ch.type_id == 0x00019000 and ch.depth == 0:
                off = ch.offset + 12
                ln = effects_p3d[off]
                if 0 < ln < 64:
                    nm = effects_p3d[off + 1:off + 1 + ln].rstrip(b"\x00")
                    at = effects_p3d.find(b"DDS ", ch.offset, ch.offset + ch.total_size)
                    if nm and at > 0:
                        tex_chunks[nm.decode()] = effects_p3d[at:ch.offset + ch.total_size]
        for extra in ["Wrist_L", "Wrist_R", "Elbow_L", "Elbow_R", "Ball_L", "Ball_R",
                      "Head", "Pelvis", "Motion_Root", "Character_Root",
                      "Joint Space", "World Space", "Main character",
                      "AttackWindup", "AttackRelease", "play", "stop",
                      "Retrigger", "StopExisting", "Overlap",
                      "small", "medium", "large"]:
            names[hash64(extra.encode())] = extra

        keys = {k: hash64(k.encode()) for k in
                ["animation", "sound", "spawn", "execute", "tracks", "node", "bank",
                 "store", "conditions", "hit", "cameraShakeRequest", "motionState",
                 "motionTrail", "effect", "unlockable", "onEvent"]}
        st = {
            "blocks": blocks, "parsed": parsed, "frames_of": frames_of,
            "names": names, "keys": keys, "bank_bytes": bank_bytes,
            "trail_defs": trail_defs, "tex_chunks": tex_chunks,
        }
        _CACHE[game_root] = st
        return st


def _resolve_path_payload(st, path: str):
    """Resolve //block/segments to its node and list proven payload items."""
    blocks, keys, names = st["blocks"], st["keys"], st["names"]
    parts = path.lstrip("/").split("/")
    tok, segs = parts[0], parts[1:]
    blob = blocks.get(tok)
    if blob is None:
        return None
    roots = st["parsed"][tok]
    cur = roots[0]
    for seg in segs:
        want = hash64(seg.encode())
        found = None
        frontier = list(cur.children)
        depth = 0
        while frontier and found is None and depth < 3:
            nxt = []
            for c in frontier:
                if c.size >= 8 and struct.unpack_from("<Q", blob, c.voff)[0] == want:
                    found = c
                    break
                nxt.extend(c.children)
            frontier = nxt
            depth += 1
        if found is None:
            return None
        cur = found
    items = []

    def scan(n, depth=0):
        for c in n.children:
            if c.key == keys["effect"] and c.size >= 44:
                em = struct.unpack_from("<Q", blob, c.voff + 12)[0]
                bn = struct.unpack_from("<Q", blob, c.voff + 36)[0]
                items.append({"kind": "effect",
                              "emitter": names.get(em, "%#x" % em),
                              "bone": names.get(bn)})
            elif c.key == keys["motionTrail"] and c.size >= 116:
                bn = struct.unpack_from("<Q", blob, c.voff + 52)[0]
                dh = struct.unpack_from("<Q", blob, c.voff + 12)[0]
                offa = struct.unpack_from("<fff", blob, c.voff + 84)
                offb = struct.unpack_from("<fff", blob, c.voff + 104)
                item = {"kind": "motionTrail", "bone": names.get(bn, "%#x" % bn),
                        "edge_a": [round(x, 4) for x in offa],
                        "edge_b": [round(x, 4) for x in offb]}
                td = st["trail_defs"].get(dh)
                if td:
                    item["def"] = td["name"]
                    item["template"] = td["template"]
                    item["texture"] = td["texture"]
                items.append(item)
            elif c.key == keys["sound"] and c.size >= 16:
                t = struct.unpack_from("<f", blob, c.voff + 4)[0]
                sh = struct.unpack_from("<Q", blob, c.voff + 8)[0]
                items.append({"kind": "sound", "t": round(t, 4),
                              "event": names.get(sh, "%#x" % sh)})
            if depth < 6:
                scan(c, depth + 1)
    scan(cur)
    return items


def build_timeline(game_root: str, anim_name: str):
    """All FIG usages of one animation, with stage frame windows + events."""
    st = _state(game_root)
    keys, names = st["keys"], st["names"]
    want = hash64(anim_name.encode())
    fps = 30.0
    info = st["frames_of"].get(want)
    usages = []
    for tok, roots in st["parsed"].items():
        blob = st["blocks"][tok]

        def walk(n, anc):
            if n.key == keys["tracks"]:
                anims = [c for c in n.children if c.key == keys["animation"] and c.size >= 40]
                hit_windows = []
                for a in anims:
                    ah = struct.unpack_from("<Q", blob, a.voff + 12)[0]
                    if ah == want:
                        f0, f1 = struct.unpack_from("<ff", blob, a.voff + 32)
                        hit_windows.append((f0, f1))
                if hit_windows:
                    f0, f1 = hit_windows[0]
                    events = []
                    for c in n.children:
                        v = c.voff
                        if c.key == keys["sound"] and c.size >= 16:
                            t = struct.unpack_from("<f", blob, v + 4)[0]
                            sh = struct.unpack_from("<Q", blob, v + 8)[0]
                            ev = {"kind": "sound", "t": round(t, 4),
                                  "event": names.get(sh, "%#x" % sh)}
                            if c.size >= 72:
                                vb = struct.unpack_from("<Q", blob, v + 16)[0]
                                md = struct.unpack_from("<Q", blob, v + 64)[0]
                                ev["verb"] = names.get(vb, "%#x" % vb)
                                ev["volume"] = round(struct.unpack_from("<f", blob, v + 24)[0], 3)
                                ev["mode"] = names.get(md, "%#x" % md)
                            events.append(ev)
                        elif c.key in (keys["spawn"], keys["execute"]) and c.size > 16:
                            t = struct.unpack_from("<f", blob, v + 4)[0]
                            ln = struct.unpack_from("<I", blob, v + 12)[0]
                            if 0 < ln < 240 and 16 + ln <= c.size:
                                s = blob[v + 16:v + 16 + ln]
                                if all(32 <= b < 127 for b in s):
                                    path = s.decode()
                                    ev = {"kind": "spawn" if c.key == keys["spawn"] else "execute",
                                          "t": round(t, 4), "path": path}
                                    payload = _resolve_path_payload(st, path) if path.startswith("//") else None
                                    if payload:
                                        ev["payload"] = payload
                                    events.append(ev)
                        elif c.key == keys["hit"] and c.size >= 8:
                            t = struct.unpack_from("<f", blob, v + 4)[0]
                            events.append({"kind": "hit", "t": round(t, 4)})
                        elif c.key == keys["cameraShakeRequest"] and c.size >= 8:
                            t = struct.unpack_from("<f", blob, v + 4)[0]
                            ev = {"kind": "cameraShake", "t": round(t, 4)}
                            if c.size >= 16:
                                pr = struct.unpack_from("<Q", blob, v + 8)[0]
                                ev["preset"] = names.get(pr, "%#x" % pr)
                            events.append(ev)
                        elif c.key == keys["motionState"] and c.size >= 16:
                            ms = struct.unpack_from("<Q", blob, v + 8)[0]
                            events.append({"kind": "motionState", "t": 0.0,
                                           "state": names.get(ms, "%#x" % ms)})
                        elif c.key == keys["onEvent"] and c.size > 40:
                            # state-tree transition edge: u32 len @+24, path @+28
                            t = struct.unpack_from("<f", blob, v + 4)[0]
                            ln = struct.unpack_from("<I", blob, v + 24)[0]
                            if 0 < ln < 200 and 28 + ln <= c.size:
                                sp = blob[v + 28:v + 28 + ln]
                                if all(32 <= b < 127 for b in sp):
                                    events.append({"kind": "transition",
                                                   "t": round(t, 4),
                                                   "target": sp.decode()})
                    events.sort(key=lambda e: e["t"])
                    usages.append({
                        "block": tok, "tracks_offset": n.off,
                        "start_frame": round(f0, 3), "end_frame": round(f1, 3),
                        "events": events,
                    })
            for c in n.children:
                walk(c, anc + [n])
        for r in roots:
            walk(r, [])
    return {
        "animation": anim_name,
        "found": bool(usages),
        "frames": info[1] if info else None,
        "fps": info[2] if info else fps,
        "claim_boundary": (
            "Times are raw f32 seconds relative to each stage node; stage start/end are raw "
            "frame fields from the animation node (negative end = raw sentinel, not decoded). "
            "Only hash-verified names are shown; unknown hashes stay as hex."),
        "usages": usages,
    }


# ---------------- audio ----------------

_IMA_STEP = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
             34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
             157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
             724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499,
             2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630,
             9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
             27086, 29794, 32767]
_IMA_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


def _decode_rad_ima_mono(data: bytes):
    out = []
    nblocks, rem = divmod(len(data), 0x14)
    if rem:
        raise ValueError("RADP data not multiple of 0x14")
    for b in range(nblocks):
        off = b * 0x14
        step_index = struct.unpack_from("<h", data, off)[0]
        hist = struct.unpack_from("<h", data, off + 2)[0]
        step_index = max(0, min(88, step_index))
        for i in range(32):
            byte = data[off + 4 + i // 2]
            nib = (byte >> (4 if (i & 1) else 0)) & 0xF
            step = _IMA_STEP[step_index]
            delta = step >> 3
            if nib & 1:
                delta += step >> 2
            if nib & 2:
                delta += step >> 1
            if nib & 4:
                delta += step
            if nib & 8:
                delta = -delta
            hist += delta
            hist = -32768 if hist < -32768 else (32767 if hist > 32767 else hist)
            step_index = max(0, min(88, step_index + _IMA_INDEX[nib]))
            out.append(hist)
    return out


def _find_audiofile(bank: bytes, name: bytes):
    needle = (b"AudioFile\x00\x02\x00\x00\x00" + struct.pack("<I", len(name))
              + name + b"\x00")
    at = bank.find(needle)
    if at < 0:
        return None
    p = at + len(needle)
    ln2 = struct.unpack_from("<I", bank, p)[0]
    p += 4 + ln2 + 1
    unk = struct.unpack_from("<I", bank, p)[0]
    if unk not in (0, 1):
        raise ValueError("unexpected unk_count %d" % unk)
    p += 4
    ln3 = struct.unpack_from("<I", bank, p)[0]
    codec = bank[p + 4:p + 4 + ln3]
    p += 4 + ln3 + 1
    if codec != b"radp" or bank[p:p + 4] != b"RADP":
        raise ValueError("unsupported codec %r" % codec)
    p += 4
    channels, sample_rate, _unk2, data_size = struct.unpack_from("<IIII", bank, p)
    payload = bank[p + 16:p + 16 + data_size]
    if len(payload) != data_size or channels != 1:
        raise ValueError("bad RADP payload")
    return sample_rate, payload


def _patch_files(bank: bytes, patch_name: bytes):
    """Return the exact files list of a Patch object (u32(5)+files\\0+count)."""
    needle = (b"Patch\x00\x01\x00\x00\x00" + struct.pack("<I", len(patch_name))
              + patch_name + b"\x00")
    at = bank.find(needle)
    if at < 0:
        return None
    marker = b"\x05\x00\x00\x00files\x00"
    fat = bank.find(marker, at, at + 4096)
    if fat < 0:
        return None
    p = fat + len(marker)
    count = struct.unpack_from("<I", bank, p)[0]
    p += 4
    files = []
    for _ in range(min(count, 64)):
        ln = struct.unpack_from("<I", bank, p)[0]
        if not (0 < ln < 96):
            break
        s = bank[p + 4:p + 4 + ln]
        files.append(s.decode("ascii", "replace"))
        p += 4 + ln + 1
    return files


def decode_audio_event(game_root: str, event: str, index: int = 0):
    """event = Patch name (FIG sound reference) or a direct AudioFile name.
    Returns (sample_rate, pcm16 bytes, source_file_name)."""
    st = _state(game_root)
    ev = event.encode()
    banks = dict(st["bank_bytes"])
    for bname, bank in banks.items():
        files = _patch_files(bank, ev)
        chosen = None
        if files:
            chosen = files[index % len(files)].encode()
        elif _find_audiofile(bank, ev) is not None:
            chosen = ev
        if chosen is None:
            continue
        got = _find_audiofile(bank, chosen)
        if got is None:
            continue
        rate, payload = got
        samples = _decode_rad_ima_mono(payload)
        pcm = struct.pack("<%dh" % len(samples), *samples)
        return rate, pcm, chosen.decode()
    return None


# ---------------- fx textures (motion trails etc.) ----------------

def _decode_dxt_to_png(dds: bytes) -> bytes:
    """Decode a DXT1/DXT5 DDS (top mip only) to PNG bytes. Pure python."""
    import zlib
    h, w = struct.unpack_from("<II", dds, 12)
    fourcc = dds[84:88]
    data = dds[128:]
    rows = [[0] * (w * 4) for _ in range(h)]
    pos = 0

    def rgb565(c):
        return (((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63,
                (c & 31) * 255 // 31)

    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            if fourcc == b"DXT5":
                a0, a1 = data[pos], data[pos + 1]
                abits = int.from_bytes(data[pos + 2:pos + 8], "little")
                c0, c1 = struct.unpack_from("<HH", data, pos + 8)
                cbits = struct.unpack_from("<I", data, pos + 12)[0]
                pos += 16
            elif fourcc == b"DXT1":
                c0, c1 = struct.unpack_from("<HH", data, pos)
                cbits = struct.unpack_from("<I", data, pos + 4)[0]
                pos += 8
            else:
                raise ValueError("unsupported DDS fourcc %r" % fourcc)
            r0, g0, b0 = rgb565(c0)
            r1, g1, b1 = rgb565(c1)
            pal = [(r0, g0, b0), (r1, g1, b1),
                   ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3),
                   ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3)]
            for py in range(4):
                for px in range(4):
                    idx = (cbits >> (2 * (py * 4 + px))) & 3
                    if fourcc == b"DXT5":
                        ai = (abits >> (3 * (py * 4 + px))) & 7
                        if a0 > a1:
                            alpha = [a0, a1, (6 * a0 + a1) // 7, (5 * a0 + 2 * a1) // 7,
                                     (4 * a0 + 3 * a1) // 7, (3 * a0 + 4 * a1) // 7,
                                     (2 * a0 + 5 * a1) // 7, (a0 + 6 * a1) // 7][ai]
                        else:
                            alpha = [a0, a1, (4 * a0 + a1) // 5, (3 * a0 + 2 * a1) // 5,
                                     (2 * a0 + 3 * a1) // 5, (a0 + 4 * a1) // 5, 0, 255][ai]
                    else:
                        alpha = 255
                    x, y = bx + px, by + py
                    if x < w and y < h:
                        r, g, b = pal[idx]
                        rows[y][x * 4:x * 4 + 4] = [r, g, b, alpha]
    def chunk(tag, payload):
        c = tag + payload
        return struct.pack(">I", len(payload)) + c + struct.pack(">I", zlib.crc32(c))
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def get_fx_texture_png(game_root: str, trail_name: str):
    """Return (png_bytes, meta) for a trail definition name, or None."""
    st = _state(game_root)
    for dh, td in st["trail_defs"].items():
        if td["name"] == trail_name:
            dds = st["tex_chunks"].get(td["texture"])
            if dds is None:
                return None
            key = "png:" + td["texture"]
            png = st.get(key)
            if png is None:
                png = _decode_dxt_to_png(dds)
                st[key] = png
            return png, td
    return None


# ---------------- audio library listing ----------------

def list_audio_banks(game_root: str):
    r"""Top-level sfx banks in 00audio.rcf (audio\*.p3d). The 13k+ entries
    under audio\english\AudioFile are per-line dialogue files; they are
    reported as a single summary category, not expanded."""
    aud = os.path.join(game_root, "00audio.rcf")
    cem = rcf_extract.CementFile.load(aud)
    banks = []
    dialogue_count = 0
    for md in cem.metadatas:
        if md.name.startswith("audio\\english"):
            dialogue_count += 1
            continue
        if md.name.startswith("audio\\") and md.name.endswith(".p3d"):
            nh = rcf_extract.hash_file_name(md.name)
            es = [e for e in cem.entries if e.name_hash == nh]
            if len(es) == 1:
                banks.append({"bank": md.name, "bytes": es[0].size})
    banks.sort(key=lambda b: b["bank"])
    return {"banks": banks, "dialogue_entries_not_expanded": dialogue_count}


def _bank_data(game_root: str, bank_name: str) -> bytes:
    st = _state(game_root)
    cached = st["bank_bytes"].get(bank_name)
    if cached is not None:
        return cached
    aud = os.path.join(game_root, "00audio.rcf")
    cem = rcf_extract.CementFile.load(aud)
    nh = rcf_extract.hash_file_name(bank_name)
    es = [e for e in cem.entries if e.name_hash == nh]
    if len(es) != 1:
        raise ValueError("bank not found: %r" % bank_name)
    with open(aud, "rb") as fh:
        fh.seek(es[0].offset)
        data = fh.read(es[0].size)
    st["bank_bytes"][bank_name] = data
    return data


_AF_PAT = re.compile(rb"AudioFile\x00\x02\x00\x00\x00(.{4})", re.S)
_PA_PAT = re.compile(rb"Patch\x00\x01\x00\x00\x00(.{4})", re.S)


def list_bank_contents(game_root: str, bank_name: str):
    """Patch groups (event name + member files) and loose AudioFiles with
    real durations from the RADP headers. Non-radp codecs are labelled."""
    data = _bank_data(game_root, bank_name)
    files = {}
    for m in _AF_PAT.finditer(data):
        ln = struct.unpack("<I", m.group(1))[0]
        if not (0 < ln < 96):
            continue
        nm = data[m.end():m.end() + ln]
        if not all(32 <= b < 127 for b in nm):
            continue
        name = nm.decode()
        at = data.find(b"RADP", m.end(), m.end() + ln + 260)
        dur = None
        codec = None
        if at > 0:
            ch, sr, _unk, sz = struct.unpack_from("<IIII", data, at + 4)
            if ch == 1 and 0 < sr < 200000:
                dur = round(sz / 0x14 * 32 / sr, 2)
                codec = "radp"
        files.setdefault(name, {"name": name, "duration": dur, "codec": codec})
    patches = []
    used = set()
    for m in _PA_PAT.finditer(data):
        ln = struct.unpack("<I", m.group(1))[0]
        if not (0 < ln < 96):
            continue
        nm = data[m.end():m.end() + ln]
        if not all(32 <= b < 127 for b in nm):
            continue
        pname = nm.decode()
        members = _patch_files(data, nm) or []
        for f in members:
            used.add(f)
        patches.append({
            "event": pname,
            "files": [files.get(f, {"name": f, "duration": None, "codec": None})
                      for f in members],
        })
    loose = [v for k, v in sorted(files.items()) if k not in used]
    patches.sort(key=lambda p: p["event"])
    return {"bank": bank_name, "patches": patches, "loose_files": loose,
            "claim_boundary": (
                "Durations are computed from each RADP header (data_size/0x14*32/rate). "
                "Patch membership is the exact files list. Codec None = no RADP header "
                "found near the name (not decoded by this tool).")}


# ---------------- input move graph (mouse simulation foundation) ----------------

_BUTTON_WORDS = ["Attack", "Special", "Jump", "Action", "Target", "Sprint",
                 "Grab", "Block", "Use", "Fire"]
_STATE_WORDS = ["Pressed", "Down", "Up", "Released"]


def build_input_moves(game_root: str, block_token: str):
    """All banks in one FIG block whose conditions contain an input record.

    Returns moves with: button/state/hold threshold, sequence id (combo
    ordinal from tracks.sequence), the first animation of the move subtree
    (with its stage frame window), and the bank offset for provenance.
    """
    st = _state(game_root)
    blob = st["blocks"].get(block_token)
    if blob is None:
        return {"block": block_token, "found": False, "moves": []}
    roots = st["parsed"][block_token]
    keys = st["keys"]
    names = dict(st["names"])
    for w in _BUTTON_WORDS + _STATE_WORDS + _BUTTON_WORDS_FULL + _AXIS_WORDS:
        names[hash64(w.encode())] = w
    K_axis = hash64(b"axisDirection")
    K_input = hash64(b"input")
    K_seq = hash64(b"sequence")
    K_bank = hash64(b"bank")
    K_cond = keys["conditions"]
    K_anim = keys["animation"]

    moves = []

    def first_in_subtree(n, key, size_min):
        stack = [n]
        while stack:
            cur = stack.pop(0)
            if cur.key == key and cur.size >= size_min:
                return cur
            stack.extend(cur.children)
        return None

    def anims_in_subtree(n):
        out = []
        stack = [n]
        while stack:
            cur = stack.pop(0)
            if cur.key == K_anim and cur.size >= 40:
                ah = struct.unpack_from("<Q", blob, cur.voff + 12)[0]
                f0, f1 = struct.unpack_from("<ff", blob, cur.voff + 32)
                nm = st["frames_of"].get(ah)
                out.append({"anim": nm[0] if nm else "%#x" % ah,
                            "start_frame": round(f0, 1), "end_frame": round(f1, 1)})
            stack.extend(cur.children)
        return out

    def walk(n):
        if n.key == K_bank:
            conds = [c for c in n.children if c.key == K_cond]
            inp = None
            for c in conds:
                inp = first_in_subtree(c, K_input, 40)
                if inp:
                    break
            if inp is not None:
                v = blob[inp.voff:inp.voff + 40]
                btn = struct.unpack_from("<Q", v, 0)[0]
                stt = struct.unpack_from("<Q", v, 8)[0]
                hold = struct.unpack_from("<f", v, 24)[0]
                seqn = first_in_subtree(n, K_seq, 20)
                seq_id = None
                if seqn is not None and seqn.size == 20:
                    seq_id = struct.unpack_from("<I", blob, seqn.voff + 12)[0]
                # optional direction condition next to the input record
                axis = None
                for c in conds:
                    ax = first_in_subtree(c, K_axis, 16)
                    if ax is not None:
                        src = struct.unpack_from("<Q", blob, ax.voff)[0]
                        ang, tol = struct.unpack_from("<ff", blob, ax.voff + 8)
                        axis = {"source": names.get(src, "%#x" % src),
                                "angle_deg": round(ang, 1),
                                "tolerance_deg": round(tol, 1)}
                        break
                anims = anims_in_subtree(n)
                if anims:
                    mv = {
                        "bank_offset": n.off,
                        "button": names.get(btn, "%#x" % btn),
                        "state": names.get(stt, "%#x" % stt),
                        "hold_seconds": round(hold, 3),
                        "sequence_id": seq_id,
                        "animations": anims[:6],
                        "primary_anim": anims[0]["anim"],
                    }
                    if axis:
                        mv["axis"] = axis
                    moves.append(mv)
        for c in n.children:
            walk(c)
    for r in roots:
        walk(r)
    moves.sort(key=lambda m: (m["sequence_id"] is None, m["sequence_id"] or 0))
    return {
        "block": block_token, "found": bool(moves), "moves": moves,
        "claim_boundary": (
            "input/state/hold and sequence ids are raw decoded fields; combo chaining "
            "on repeated clicks is a player-simulation convention (ascending sequence "
            "ids within one store), not a fully decoded engine arbitration."),
    }


# ---------------- condition vocabulary (full census) ----------------

_COND_WORDS = ["random", "input", "className", "grabbableClass", "conditionGroup",
    "targetDistance", "reactionHitHitType", "event", "materialType",
    "variableIntCompare", "message", "lua", "grabSlotGrabbableClass",
    "motionState", "charge", "false", "reactionHitAttackType", "playbackState",
    "velocity", "health", "supportingSurface", "timerCompare", "consumptionName",
    "grabSlot", "grabSlotObjectTemplate", "event_String", "emotionalState",
    "targeting", "reactionHitCharge", "targetConditionGroup", "grabSlotClassName",
    "alertVariable", "targetAlertState", "axisDirection", "variableNameCompare",
    "maybe", "reactionHitImpulseDirection", "or", "and", "not", "unlockable",
    "sequence", "opportunity"]
_BUTTON_WORDS_FULL = ["Attack", "Special", "Jump", "Action", "Target",
    "MovementAxis", "Run", "Taunt", "DpadUp", "DpadDown", "DpadLeft",
    "DpadRight", "LeftBumper", "RightBumper", "RightStickTrigger"]
_AXIS_WORDS = ["Movement", "Movement Raw", "Camera", "Look"]


def condition_vocab(game_root: str):
    """Census: which condition kinds appear in each FIG block, with counts."""
    st = _state(game_root)
    names = dict(st["names"])
    for w in _COND_WORDS + _BUTTON_WORDS_FULL + _STATE_WORDS + _AXIS_WORDS:
        names[hash64(w.encode())] = w
    K_cond = st["keys"]["conditions"]
    out = {}
    for tok, roots in st["parsed"].items():
        blob = st["blocks"][tok]
        counts = {}

        def walk(n, in_cond):
            if in_cond and n.key != 0:
                nm = names.get(n.key, "%#x" % n.key)
                counts[nm] = counts.get(nm, 0) + 1
            for c in n.children:
                walk(c, n.key == K_cond)
        for r in roots:
            walk(r, False)
        if counts:
            out[tok] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
    return {"blocks": out,
            "claim_boundary": (
                "Kind names are exact hash64 matches against DLL .rdata strings. "
                "Value layouts are only decoded for input/charge/sequence/opportunity/"
                "axisDirection; other kinds are counted but not interpreted.")}

# marker: sound_verb decode present


# ---------------- move stage graph (state-tree runner backend) ----------------

def build_move_graph(game_root: str, block_token: str, bank_offset: int):
    # RETIRED 2026-09-18: this function flattened every tracks-bearing
    # descendant in document order and retained only the first animation per
    # tracks node. Blade attack-B and Hammerfist charge are byte-level
    # counterexamples. Do not expose that list as an executable graph.
    return {
        "found": False,
        "deprecated": True,
        "reason": (
            "disabled: document order is not an execution graph; bank/node "
            "condition ownership and all co-owned animation tracks must be "
            "resolved first"
        ),
        "block": block_token,
        "bank_offset": bank_offset,
        "stages": [],
    }

    # Preserved temporarily below for archaeological diffing only; unreachable.
    st = _state(game_root)
    blob = st["blocks"].get(block_token)
    if blob is None:
        return {"found": False, "reason": "no such block"}
    roots = st["parsed"][block_token]
    keys = st["keys"]
    names = dict(st["names"])
    for w in _BUTTON_WORDS_FULL + _STATE_WORDS + ["play", "stop", "Retrigger",
                                                  "StopExisting", "Overlap",
                                                  "small", "medium", "large"]:
        names[hash64(w.encode())] = w
    K_input = hash64(b"input")
    K_charge = hash64(b"charge")
    K_seq = hash64(b"sequence")

    target = [None]

    def find(n):
        if n.off == bank_offset:
            target[0] = n
            return True
        return any(find(c) for c in n.children)
    for r in roots:
        if find(r):
            break
    if target[0] is None:
        return {"found": False, "reason": "bank offset not found"}

    fps = 30.0
    stages = []

    def cond_summary(node):
        out = []
        for c in node.children:
            if c.key != keys["conditions"]:
                continue
            def walk_cond(cn):
                if cn.key == K_input and cn.size == 40:
                    b = struct.unpack_from("<Q", blob, cn.voff)[0]
                    stt = struct.unpack_from("<Q", blob, cn.voff + 8)[0]
                    hold = struct.unpack_from("<f", blob, cn.voff + 24)[0]
                    out.append({"kind": "input", "button": names.get(b, "%#x" % b),
                                "state": names.get(stt, "%#x" % stt),
                                "hold_seconds": round(hold, 3)})
                elif cn.key == K_charge:
                    out.append({"kind": "charge"})
                else:
                    nm = names.get(cn.key)
                    if nm and nm not in ("or", "and", "not"):
                        out.append({"kind": nm, "evaluated": False})
                for cc in cn.children:
                    walk_cond(cc)
            walk_cond(c)
        return out

    def stage_events(tracks_node):
        events = []
        for c in tracks_node.children:
            v = c.voff
            if c.key == keys["sound"] and c.size >= 16:
                t = struct.unpack_from("<f", blob, v + 4)[0]
                sh = struct.unpack_from("<Q", blob, v + 8)[0]
                ev = {"kind": "sound", "t": round(t, 4),
                      "event": names.get(sh, "%#x" % sh)}
                if c.size >= 72:
                    vb = struct.unpack_from("<Q", blob, v + 16)[0]
                    md = struct.unpack_from("<Q", blob, v + 64)[0]
                    ev["verb"] = names.get(vb, "%#x" % vb)
                    ev["volume"] = round(struct.unpack_from("<f", blob, v + 24)[0], 3)
                    ev["mode"] = names.get(md, "%#x" % md)
                events.append(ev)
            elif c.key in (hash64(b"spawn"), hash64(b"execute")) and c.size > 16:
                t = struct.unpack_from("<f", blob, v + 4)[0]
                ln = struct.unpack_from("<I", blob, v + 12)[0]
                if 0 < ln < 240 and 16 + ln <= c.size:
                    sp = blob[v + 16:v + 16 + ln]
                    if all(32 <= b < 127 for b in sp):
                        path = sp.decode()
                        ev = {"kind": "spawn" if c.key == hash64(b"spawn") else "execute",
                              "t": round(t, 4), "path": path}
                        payload = _resolve_path_payload(st, path) if path.startswith("//") else None
                        if payload:
                            ev["payload"] = payload
                        events.append(ev)
            elif c.key == keys["hit"] and c.size >= 8:
                events.append({"kind": "hit",
                               "t": round(struct.unpack_from("<f", blob, v + 4)[0], 4)})
            elif c.key == keys["cameraShakeRequest"] and c.size >= 16:
                pr = struct.unpack_from("<Q", blob, v + 8)[0]
                events.append({"kind": "cameraShake",
                               "t": round(struct.unpack_from("<f", blob, v + 4)[0], 4),
                               "preset": names.get(pr, "%#x" % pr)})
        events.sort(key=lambda e: e["t"])
        return events

    def visit(node):
        tracks = [c for c in node.children if c.key == keys["tracks"]]
        for tr in tracks:
            anims = [c for c in tr.children
                     if c.key == keys["animation"] and c.size >= 40]
            if anims:
                a = anims[0]
                ah = struct.unpack_from("<Q", blob, a.voff + 12)[0]
                f0, f1 = struct.unpack_from("<ff", blob, a.voff + 32)
                nm = st["frames_of"].get(ah)
                conds = cond_summary(node)
                hold_loop = any(
                    c.get("kind") == "charge"
                    or (c.get("kind") == "input" and c.get("state") == "Down")
                    for c in conds)
                stages.append({
                    "node_offset": node.off,
                    "anim": nm[0] if nm else "%#x" % ah,
                    "total_frames": nm[1] if nm else None,
                    "fps": nm[2] if nm else fps,
                    "start_frame": round(f0, 1),
                    "end_frame": round(f1, 1),
                    "hold_loop": hold_loop,
                    "conditions": conds,
                    "events": stage_events(tr),
                })
        for c in node.children:
            visit(c)

    visit(target[0])
    # hold_loop correction: the loop stage is the one BEFORE a stage gated on
    # button release / charge completion (verified: hammerfist hold=115..120
    # precedes release gated on input-Up|charge).
    for i, stg in enumerate(stages):
        stg["hold_loop"] = False
        if i + 1 < len(stages):
            nxt = stages[i + 1]["conditions"]
            if any(c.get("kind") == "charge"
                   or (c.get("kind") == "input" and c.get("state") in ("Up", "Released"))
                   for c in nxt):
                stg["hold_loop"] = True
    return {
        "found": bool(stages), "block": block_token, "bank_offset": bank_offset,
        "stages": stages,
        "claim_boundary": (
            "Stage order = document order of tracks-bearing nodes inside the bank "
            "(matches ascending frame windows on verified samples). hold_loop derives "
            "from decoded charge/input-Down conditions. Condition kinds flagged "
            "evaluated:false are reported but not evaluated by the viewer."),
    }
