#!/usr/bin/env python3
"""Generic Pure3D chunk tree walker/dumper.
Based on the documented Pure3D file structure (donutteam.com / gibbed.us).
Does NOT know what chunk payloads mean semantically - just walks the
generic (Type, ChunkSize, TotalSize, Data[...], Children...) structure
and prints a tree with hex type IDs, so we can cross-reference against
known type tables from multiple reverse-engineering projects.
"""
import struct
import sys

SIG_LE = 0xFF443350   # 'P3D\xFF' little endian file
SIG_LE_SWAPPED = struct.unpack('<I', struct.pack('>I', SIG_LE))[0]

def read_u32(data, off, endian):
    return struct.unpack(endian + 'I', data[off:off+4])[0], off+4

def dump_chunk(data, off, end, endian, indent, out, max_depth=None, depth=0):
    while off < end:
        start = off
        type_id, off = read_u32(data, off, endian)
        header_size, off = read_u32(data, off, endian)
        total_size, off = read_u32(data, off, endian)
        payload_len = header_size - 12
        payload = data[off:off+payload_len]
        out.append((depth, type_id, header_size, total_size, payload))
        child_start = start + header_size
        child_end = start + total_size
        if max_depth is None or depth < max_depth:
            dump_chunk(data, child_start, child_end, endian, indent, out, max_depth, depth+1)
        off = child_end

def main(path, max_depth=None):
    with open(path, 'rb') as f:
        data = f.read()
    magic = struct.unpack('<I', data[0:4])[0]
    if magic == SIG_LE:
        endian = '<'
    elif magic == SIG_LE_SWAPPED:
        endian = '>'
    else:
        print(f"Not a Pure3D file (magic={data[0:4]!r})")
        return
    header_size, = struct.unpack(endian+'I', data[4:8])
    total_size, = struct.unpack(endian+'I', data[8:12])
    print(f"File: {path}  size={len(data)}  endian={'LE' if endian=='<' else 'BE'}  declared_total={total_size}")
    out = []
    dump_chunk(data, 12, total_size, endian, 0, out, max_depth)
    for depth, type_id, header_size, total_size, payload in out:
        preview = payload[:24]
        printable = ''.join(chr(b) if 32 <= b < 127 else '.' for b in preview)
        print(f"{'  '*depth}0x{type_id:08X}  hdr={header_size:6d} total={total_size:6d} payload_len={len(payload):6d}  [{printable}]")

if __name__ == '__main__':
    md = None
    if len(sys.argv) > 2:
        md = int(sys.argv[2])
    main(sys.argv[1], md)
