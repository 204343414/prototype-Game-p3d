#!/usr/bin/env python3
"""Decode Prototype's external-blob skeletal animation data into JSON.

Prototype does *not* store key-frame arrays inside individual channel chunks.
Each Animation (0x00121000) owns a ZLIB child (0x02F00000); channel locator
chunks (0x00121120) point into its decompressed byte blob. This module parses
that representation into JSON-friendly Python dictionaries while retaining
strict byte-boundary validation.

It intentionally stops before glTF export: TRAN scale/reference-frame
calibration is still a separately documented task. The decoded JSON makes that
calibration and later export testable without copying game assets into Git.

Examples:

  # Analyze an already available, locally extracted P3D file.
  python3 tools/p3d_animation/decode_animation.py \
    --p3d /private/path/alex.p3d --animation alex_act_block \
    --out /tmp/alex_act_block.decoded.json

  # Fetch only into process memory through a user's local viewer tunnel.
  python3 tools/p3d_animation/decode_animation.py \
    --base-url https://your-tunnel.trycloudflare.com \
    --rcf-path '/private/path/Prototype/art.rcf' \
    --entry-name '\art\alex\alex.p3d.rz' --animation alex_act_block \
    --out /tmp/alex_act_block.decoded.json

Do not commit P3D files or decoded JSON generated from game assets. The source
code and its synthetic unit test are the versioned artifacts.
"""
import argparse
import json
import math
import struct
import urllib.parse
import urllib.request
import zlib

P3D_SIGNATURE_LE = 0xFF443350
P3D_SIGNATURE_BE = struct.unpack("<I", struct.pack(">I", P3D_SIGNATURE_LE))[0]

ANIMATION = 0x00121000
ANIMATION_HEADER = 0x00121006
ANIMATION_GROUP_LIST = 0x00121002
ANIMATION_GROUP = 0x00121001
CHANNEL_INTERPOLATION = 0x00121110
CHANNEL_LOCATOR = 0x00121120
COMPRESSED_BLOB = 0x02F00000

ROT_INT16 = 0x00121112
ROT_INT8 = 0x00121114
VEC3_INT16 = 0x00121119
KNOWN_CHANNEL_TYPES = {ROT_INT16, ROT_INT8, VEC3_INT16}


class P3DParseError(ValueError):
    """Raised for a malformed or unsupported P3D animation representation."""


def align4(size):
    return (size + 3) & ~3


def _require(condition, message):
    if not condition:
        raise P3DParseError(message)


def read_p3d_string(data, offset):
    _require(offset < len(data), "P3D string has no length byte")
    length = data[offset]
    offset += 1
    end = offset + length
    _require(end <= len(data), f"P3D string overruns payload: end={end}, size={len(data)}")
    raw = data[offset:end]
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace"), end


def _u32(data, offset, endian="<"):
    _require(offset + 4 <= len(data), f"uint32 at {offset} overruns {len(data)} bytes")
    return struct.unpack_from(endian + "I", data, offset)[0]


def _f32(data, offset, endian="<"):
    _require(offset + 4 <= len(data), f"float32 at {offset} overruns {len(data)} bytes")
    return struct.unpack_from(endian + "f", data, offset)[0]


class Chunk:
    """A parsed generic Pure3D chunk, retaining child hierarchy and payload."""

    __slots__ = ("type_id", "header_size", "total_size", "start", "payload", "children")

    def __init__(self, type_id, header_size, total_size, start, payload, children):
        self.type_id = type_id
        self.header_size = header_size
        self.total_size = total_size
        self.start = start
        self.payload = payload
        self.children = children


def _parse_chunk_range(data, start, end, endian):
    chunks = []
    offset = start
    while offset < end:
        _require(offset + 12 <= end, f"truncated chunk header at 0x{offset:X}")
        type_id, header_size, total_size = struct.unpack_from(endian + "III", data, offset)
        _require(header_size >= 12, f"chunk 0x{type_id:08X} at 0x{offset:X} has header_size={header_size} < 12")
        _require(total_size >= header_size,
                 f"chunk 0x{type_id:08X} at 0x{offset:X} has total_size < header_size")
        chunk_end = offset + total_size
        _require(chunk_end <= end,
                 f"chunk 0x{type_id:08X} at 0x{offset:X} overruns parent: end=0x{chunk_end:X}, parent=0x{end:X}")
        payload_end = offset + header_size
        payload = data[offset + 12:payload_end]
        children = _parse_chunk_range(data, payload_end, chunk_end, endian)
        chunks.append(Chunk(type_id, header_size, total_size, offset, payload, children))
        offset = chunk_end
    _require(offset == end, f"child chunks stopped at 0x{offset:X}, expected 0x{end:X}")
    return chunks


def parse_p3d(data):
    """Return all top-level chunks after validating the generic P3D envelope."""
    _require(len(data) >= 12, "file is smaller than the 12-byte P3D header")
    magic = _u32(data, 0, "<")
    if magic == P3D_SIGNATURE_LE:
        endian = "<"
    elif magic == P3D_SIGNATURE_BE:
        endian = ">"
    else:
        raise P3DParseError(f"not a Pure3D file (magic={data[:4].hex()})")
    header_size = _u32(data, 4, endian)
    total_size = _u32(data, 8, endian)
    _require(header_size >= 12, f"file header_size={header_size} < 12")
    _require(total_size <= len(data), f"file declares {total_size} bytes, but only {len(data)} are available")
    _require(total_size >= header_size, "file total_size is smaller than header_size")
    # Existing project tooling starts chunks at byte 12. The bytes between 12
    # and header_size belong to the file header, should a future P3D variant
    # declare an extended one.
    return _parse_chunk_range(data, header_size, total_size, endian), endian


def walk(chunks):
    for chunk in chunks:
        yield chunk
        yield from walk(chunk.children)


def _first_child(chunk, type_id, label):
    matches = [child for child in chunk.children if child.type_id == type_id]
    _require(len(matches) == 1,
             f"{label} at 0x{chunk.start:X} needs exactly one 0x{type_id:08X} child; got {len(matches)}")
    return matches[0]


def parse_animation_header(payload, endian="<"):
    _require(len(payload) >= 17, "Animation payload too small")
    version = _u32(payload, 0, endian)
    name, offset = read_p3d_string(payload, 4)
    _require(offset + 16 <= len(payload), f"Animation {name!r} payload is truncated")
    animation_type = payload[offset:offset + 4].rstrip(b"\0").decode("ascii", "replace")
    num_frames = _f32(payload, offset + 4, endian)
    frame_rate = _f32(payload, offset + 8, endian)
    cyclic = _u32(payload, offset + 12, endian)
    _require(frame_rate > 0, f"Animation {name!r} has invalid frame_rate={frame_rate}")
    return {
        "name": name,
        "version": version,
        "animation_type": animation_type,
        "num_frames": num_frames,
        "frame_rate": frame_rate,
        "cyclic": bool(cyclic),
    }


def decompress_blob(blob_chunk, endian="<"):
    payload = blob_chunk.payload
    _require(len(payload) >= 16, "compressed animation blob header is shorter than 16 bytes")
    version = _u32(payload, 0, endian)
    magic = payload[4:8]
    _require(magic == b"ZLIB", f"animation blob magic must be ZLIB, got {magic!r}")
    decompressed_size = _u32(payload, 8, endian)
    compressed_size = _u32(payload, 12, endian)
    _require(16 + compressed_size == len(payload),
             f"animation blob compressed_size={compressed_size} does not match payload_len={len(payload)}")
    try:
        decoded = zlib.decompress(payload[16:])
    except zlib.error as exc:
        raise P3DParseError(f"could not zlib-decompress animation blob: {exc}") from exc
    _require(len(decoded) == decompressed_size,
             f"animation blob decoded size={len(decoded)}, header says {decompressed_size}")
    return decoded, {"version": version, "decompressed_size": decompressed_size, "compressed_size": compressed_size}


def _fourcc(payload, offset):
    _require(offset + 4 <= len(payload), "channel semantic FourCC is truncated")
    return payload[offset:offset + 4].rstrip(b"\0").decode("ascii", "replace")


def _parse_channel(channel, blob, endian):
    _require(channel.type_id in KNOWN_CHANNEL_TYPES,
             f"unsupported Animation channel type 0x{channel.type_id:08X}")
    _require(len(channel.payload) >= 12,
             f"channel 0x{channel.type_id:08X} payload is shorter than 12 bytes")
    channel_version = _u32(channel.payload, 0, endian)
    semantic = _fourcc(channel.payload, 4)
    interpolation = _first_child(channel, CHANNEL_INTERPOLATION, "Animation channel")
    locator = _first_child(channel, CHANNEL_LOCATOR, "Animation channel")
    _require(len(interpolation.payload) >= 8, "Interpolation payload is shorter than 8 bytes")
    _require(len(locator.payload) >= 12, "Channel locator payload is shorter than 12 bytes")
    interpolation_mode = struct.unpack_from(endian + "i", interpolation.payload, 4)[0]
    count = _u32(locator.payload, 4, endian)
    offset = _u32(locator.payload, 8, endian)

    if channel.type_id == ROT_INT16:
        value_width, encoding = 6, "quaternion_xyz_int16"
    elif channel.type_id == ROT_INT8:
        value_width, encoding = 3, "quaternion_xyz_int8"
    else:
        value_width, encoding = 6, "vector3_int16"

    frames_size = align4(count * 2)
    value_bytes = count * value_width
    value_padding = align4(value_bytes)
    data_end = offset + frames_size + value_bytes
    padded_end = offset + frames_size + value_padding
    _require(data_end <= len(blob),
             f"channel {semantic!r} at blob offset {offset} spans through {data_end}, blob size={len(blob)}")
    frames = list(struct.unpack_from(endian + f"{count}H", blob, offset)) if count else []
    _require(frames == sorted(frames), f"channel {semantic!r} frames are not monotonically non-decreasing")

    values_offset = offset + frames_size
    values = []
    if channel.type_id == ROT_INT16:
        raw_values = struct.iter_unpack(endian + "hhh", blob[values_offset:values_offset + count * value_width])
        for x_raw, y_raw, z_raw in raw_values:
            x, y, z = x_raw / 32767.0, y_raw / 32767.0, z_raw / 32767.0
            # Small quantisation overshoots are clamped before sqrt; a larger
            # overshoot is malformed compressed-quaternion data.
            rest = 1.0 - x * x - y * y - z * z
            _require(rest >= -1e-4, f"quaternion int16 norm overshoot: {rest}")
            values.append([x, y, z, math.sqrt(max(0.0, rest))])
    elif channel.type_id == ROT_INT8:
        raw_values = struct.iter_unpack("bbb", blob[values_offset:values_offset + count * value_width])
        for x_raw, y_raw, z_raw in raw_values:
            x, y, z = x_raw / 127.0, y_raw / 127.0, z_raw / 127.0
            rest = 1.0 - x * x - y * y - z * z
            _require(rest >= -1e-3, f"quaternion int8 norm overshoot: {rest}")
            values.append([x, y, z, math.sqrt(max(0.0, rest))])
    else:
        # This is intentionally *not* scaled to a final local translation:
        # 0x00121119 TRAN's unit scale/reference frame is an open, separately
        # documented calibration task. Preserve exact raw values plus a
        # provisional normalized view for inspection only.
        raw_values = list(struct.iter_unpack(endian + "hhh", blob[values_offset:values_offset + count * value_width]))
        values = [[x / 32767.0, y / 32767.0, z / 32767.0] for x, y, z in raw_values]
    return {
        "type_id": f"0x{channel.type_id:08X}",
        "version": channel_version,
        "semantic": semantic,
        "interpolation_mode": interpolation_mode,
        "count": count,
        "blob_offset": offset,
        "blob_data_end": data_end,
        "blob_padded_end": padded_end,
        "value_encoding": encoding,
        "frames": frames,
        "values": values,
        **({"raw_int16_values": [list(v) for v in raw_values]} if channel.type_id == VEC3_INT16 else {}),
    }


def decode_animation(animation_chunk, endian="<"):
    """Decode one 0x00121000 chunk to a JSON-serializable animation dictionary."""
    _require(animation_chunk.type_id == ANIMATION,
             f"expected Animation chunk 0x{ANIMATION:08X}, got 0x{animation_chunk.type_id:08X}")
    header = parse_animation_header(animation_chunk.payload, endian)
    header_chunk = _first_child(animation_chunk, ANIMATION_HEADER, "Animation")
    group_list = _first_child(animation_chunk, ANIMATION_GROUP_LIST, "Animation")
    blob_chunk = _first_child(animation_chunk, COMPRESSED_BLOB, "Animation")
    _require(len(header_chunk.payload) >= 8, "Animation_Header payload is shorter than 8 bytes")
    _require(len(group_list.payload) >= 8, "Animation_Group_List payload is shorter than 8 bytes")
    header_group_count = _u32(header_chunk.payload, 4, endian)
    list_group_count = _u32(group_list.payload, 4, endian)
    groups = [child for child in group_list.children if child.type_id == ANIMATION_GROUP]
    _require(header_group_count == list_group_count == len(groups),
             f"Animation {header['name']!r} group count mismatch: header={header_group_count}, "
             f"list={list_group_count}, children={len(groups)}")
    blob, blob_info = decompress_blob(blob_chunk, endian)

    decoded_groups = []
    all_channels = []
    for group in groups:
        _require(len(group.payload) >= 13, "Animation_Group payload too small")
        group_version = _u32(group.payload, 0, endian)
        name, position = read_p3d_string(group.payload, 4)
        _require(position + 8 <= len(group.payload), f"Animation_Group {name!r} payload is truncated")
        group_id = _u32(group.payload, position, endian)
        num_channels = _u32(group.payload, position + 4, endian)
        channel_nodes = [child for child in group.children if child.type_id in KNOWN_CHANNEL_TYPES]
        _require(len(channel_nodes) == num_channels,
                 f"Animation_Group {name!r} declares {num_channels} channels, found {len(channel_nodes)} known channels")
        channels = [_parse_channel(node, blob, endian) for node in channel_nodes]
        decoded_groups.append({
            "name": name,
            "version": group_version,
            "group_id": group_id,
            "channels": channels,
        })
        all_channels.extend(channels)

    # Prototype packs frames+values blocks in exact group traversal order. A
    # non-final values block is padded to four bytes because the next channel
    # starts aligned. The final block may omit otherwise-unused tail padding
    # (alex_act_block does), so it ends at raw value bytes instead.
    expected_offset = 0
    for index, channel in enumerate(all_channels):
        _require(channel["blob_offset"] == expected_offset,
                 f"animation blob layout has a gap/reorder before offset {channel['blob_offset']}; "
                 f"expected {expected_offset}")
        if index + 1 < len(all_channels):
            expected_offset = channel["blob_padded_end"]
        else:
            expected_offset = channel["blob_data_end"]
    _require(expected_offset == len(blob),
             f"animation blob layout ends at {expected_offset}, blob size={len(blob)}")

    return {
        **header,
        "source_chunk_offset": animation_chunk.start,
        "blob": blob_info,
        "group_count": len(decoded_groups),
        "channel_count": len(all_channels),
        "groups": decoded_groups,
    }


def find_animation(data, name):
    chunks, endian = parse_p3d(data)
    matches = []
    for chunk in walk(chunks):
        if chunk.type_id != ANIMATION:
            continue
        header = parse_animation_header(chunk.payload, endian)
        if header["name"] == name:
            matches.append(chunk)
    _require(len(matches) == 1, f"expected exactly one Animation named {name!r}; found {len(matches)}")
    return decode_animation(matches[0], endian)


def fetch_rcf_entry(base_url, rcf_path, entry_name, timeout=180):
    """Fetch one decompressed entry into memory through the trusted viewer API."""
    params = {"path": rcf_path, "name": entry_name, "raw": 1}
    url = base_url.rstrip("/") + "/api/rcf_entry?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"could not fetch RCF entry from viewer API: {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--p3d", help="private local path to a decompressed .p3d file")
    source.add_argument("--base-url", help="viewer tunnel/base URL used with --rcf-path and --entry-name")
    parser.add_argument("--rcf-path", help="RCF path on viewer host (required with --base-url)")
    parser.add_argument("--entry-name", help="logical entry name on viewer host (required with --base-url)")
    parser.add_argument("--animation", required=True, help="exact Animation chunk name")
    parser.add_argument("--out", required=True, help="private JSON output path; do not commit game-derived output")
    args = parser.parse_args()

    if args.base_url:
        if not args.rcf_path or not args.entry_name:
            parser.error("--base-url requires both --rcf-path and --entry-name")
        data = fetch_rcf_entry(args.base_url, args.rcf_path, args.entry_name)
    else:
        if args.rcf_path or args.entry_name:
            parser.error("--rcf-path/--entry-name may only be used with --base-url")
        with open(args.p3d, "rb") as handle:
            data = handle.read()

    decoded = find_animation(data, args.animation)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(decoded, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Decoded {decoded['name']}: {decoded['group_count']} groups, {decoded['channel_count']} channels")
    print(f"Private JSON output: {args.out}")


if __name__ == "__main__":
    main()
