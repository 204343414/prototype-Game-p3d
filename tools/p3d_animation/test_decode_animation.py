#!/usr/bin/env python3
"""Synthetic regression test for Prototype external-ZLIB animation decoding."""
import importlib.util
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("decode_animation", os.path.join(HERE, "decode_animation.py"))
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def p3d_string(value):
    raw = value.encode("utf-8") + b"\0"
    while len(raw) % 4:
        raw += b"\0"
    assert len(raw) <= 255
    return bytes([len(raw)]) + raw


def chunk(type_id, payload=b"", children=()):
    child_bytes = b"".join(children)
    header_size = 12 + len(payload)
    total_size = header_size + len(child_bytes)
    return struct.pack("<III", type_id, header_size, total_size) + payload + child_bytes


def make_fixture():
    # ROT frames [0, 15], then align4, then int16 xyz values. TRAN has one
    # frame after ROT's exact block end, exercising type-specific widths.
    rot_frames = struct.pack("<HH", 0, 15)
    rot_values = struct.pack("<hhhhhh", 0, 0, 0, 16384, 0, 0)
    tran_frames = struct.pack("<H", 2) + b"\0\0"
    # The final value block deliberately has no otherwise-unused alignment
    # tail: this matches the real alex_act_block blob.
    tran_values = struct.pack("<hhh", 100, -200, 300)
    blob = rot_frames + rot_values + tran_frames + tran_values
    assert len(blob) == 26

    zlib_payload = struct.pack("<I4sII", 0, b"ZLIB", len(blob), len(zlib.compress(blob))) + zlib.compress(blob)
    rot_locator = chunk(mod.CHANNEL_LOCATOR, struct.pack("<III", 0, 2, 0))
    rot_interp = chunk(mod.CHANNEL_INTERPOLATION, struct.pack("<Ii", 0, -1))
    rot_channel = chunk(mod.ROT_INT16, struct.pack("<I4sI", 1, b"ROT\0", 0), [rot_interp, rot_locator])
    tran_locator = chunk(mod.CHANNEL_LOCATOR, struct.pack("<III", 0, 1, 16))
    tran_interp = chunk(mod.CHANNEL_INTERPOLATION, struct.pack("<Ii", 0, -1))
    tran_channel = chunk(mod.VEC3_INT16, struct.pack("<I4sI", 0, b"TRAN", 0), [tran_interp, tran_locator])
    group = chunk(mod.ANIMATION_GROUP,
                  struct.pack("<I", 0) + p3d_string("Character_Root") + struct.pack("<II", 2, 2),
                  [rot_channel, tran_channel])
    group_list = chunk(mod.ANIMATION_GROUP_LIST, struct.pack("<II", 0, 1), [group])
    histogram_entry = chunk(0x00121009, struct.pack("<III", 0, mod.ROT_INT16, 1))
    animation_header = chunk(mod.ANIMATION_HEADER, struct.pack("<II", 0, 1), [chunk(0x00121008, struct.pack("<II", 0, 1), [histogram_entry])])
    # AnimationLimbReference is a direct Animation child: a P3D string plus
    # exactly 24 opaque bytes. The fixture keeps those bytes distinct from the
    # external ZLIB channel blob.
    limb_reference = chunk(mod.ANIMATION_LIMB_REFERENCE, p3d_string("leg_left") + bytes(range(24)))
    animation_payload = struct.pack("<I", 0) + p3d_string("synthetic_block") + b"PTRN" + struct.pack("<ffI", 16.0, 30.0, 1)
    animation = chunk(mod.ANIMATION, animation_payload,
                      [animation_header, chunk(mod.COMPRESSED_BLOB, zlib_payload), group_list, limb_reference])
    total_size = 12 + len(animation)
    return struct.pack("<III", mod.P3D_SIGNATURE_LE, 12, total_size) + animation


def main():
    decoded = mod.find_animation(make_fixture(), "synthetic_block")
    assert decoded["animation_type"] == "PTRN"
    assert decoded["num_frames"] == 16.0
    assert decoded["frame_rate"] == 30.0
    assert decoded["cyclic"] is True
    assert decoded["group_count"] == 1
    assert decoded["channel_count"] == 2
    assert len(decoded["limb_references"]) == 1
    reference = decoded["limb_references"][0]
    assert reference["limb"] == "leg_left"
    assert reference["source_chunk_offset"] > 0
    assert reference["unknown_bytes_hex"] == bytes(range(24)).hex()

    rot, tran = decoded["groups"][0]["channels"]
    assert rot["frames"] == [0, 15]
    assert rot["value_encoding"] == "quaternion_xyz_int16"
    assert rot["values"][0] == [0.0, 0.0, 0.0, 1.0]
    assert abs(rot["values"][1][0] - 16384 / 32767.0) < 1e-12
    assert tran["semantic"] == "TRAN"
    assert tran["blob_offset"] == 16
    assert tran["blob_data_end"] == 26
    assert tran["blob_padded_end"] == 28
    assert tran["raw_int16_values"] == [[100, -200, 300]]
    assert abs(tran["values"][0][1] - (-200 / 32767.0)) < 1e-12

    try:
        mod.find_animation(make_fixture(), "missing")
    except mod.P3DParseError:
        pass
    else:
        raise AssertionError("missing animation name must fail")

    print("OK: external-ZLIB Animation decoder passed synthetic layout, quaternion, TRAN, and failure checks")


if __name__ == "__main__":
    main()
