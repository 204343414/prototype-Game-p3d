#!/usr/bin/env python3
"""
Self-consistency test for rcf_extract.py.

We don't have real Prototype .rcf game files to test against (see
docs/roadmap.md), so this builds a *synthetic* .rcf file from scratch,
following the exact same byte layout documented in CementFile.Serialize
(references/gibbed-prototype/.../CementFile.cs), then verifies our
independent reader (rcf_extract.CementFile.load + extract()) can read it
back correctly, including name-hash lookups and .rz zlib decompression.

This proves internal self-consistency of our format understanding, but is
NOT a substitute for testing against a real game .rcf file, since our
"writer" here mirrors the same (possibly incomplete/misunderstood) spec as
our reader. Treat a pass here as "necessary but not sufficient".
"""
import os
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(__file__))
from rcf_extract import CementFile, extract, hash_file_name, MAGIC, RZ_MAGIC  # noqa: E402


def align(n, boundary):
    rem = n % boundary
    return n if rem == 0 else n + (boundary - rem)


def build_synthetic_rcf(path, files):
    """files: list of (name, data_bytes) tuples. Writes a v2.1 little-endian
    cement archive containing them, mirroring CementFile.Serialize's layout.
    """
    entries = []  # (name_hash, offset, size) -- offsets filled in later
    header_size = 24 + 8 + 1 + 1 + 1 + 1 + 4 * 6
    index_size = len(files) * 12
    data_start = align(header_size + index_size, 2048)

    offset = data_start
    payloads = []
    for name, data in files:
        entries.append((hash_file_name(name), offset, len(data)))
        payloads.append(data)
        offset += len(data)

    metadata_offset = align(offset, 2048)

    with open(path, "wb") as f:
        f.write(MAGIC)
        f.write(b"\x00" * 8)
        f.write(struct.pack("<BBBB", 2, 1, 0, 1))  # major, minor, endian(LE), unknown1
        index_offset = header_size
        f.write(struct.pack("<IIIIII", index_offset, index_size, metadata_offset, 0, 0, len(files)))
        # placeholder metadata_size, fixed up after we know it

        for name_hash, off, size in entries:
            f.write(struct.pack("<III", name_hash, off, size))

        f.seek(data_start)
        for data in payloads:
            f.write(data)

        f.seek(metadata_offset)
        f.write(struct.pack("<II", 2048, 0))  # names_alignment, names_unknown
        for name, _ in files:
            name_bytes = name.encode("ascii") + b"\x00"
            f.write(struct.pack("<III", 0, 0, 0))  # type_hash, alignment, unknown2
            f.write(struct.pack("<I", len(name_bytes)))
            f.write(name_bytes)
            f.write(b"\x00\x00\x00")  # Unknown3

        end = f.tell()
        metadata_size = end - metadata_offset

        # fix up metadata_size field in header
        f.seek(header_size - 4 - 4)  # right before unknown2+entryCount... recompute properly below


def build_synthetic_rcf_fixed(path, files):
    """Corrected single-pass builder: compute metadata_size up front by
    doing a dry-run write to a buffer, then write the real file in one pass
    with all header fields known ahead of time.
    """
    import io

    entries = []
    offset_cursor = 0
    payloads = []
    for name, data in files:
        entries.append([hash_file_name(name), 0, len(data)])
        payloads.append(data)

    header_size = 24 + 8 + 1 + 1 + 1 + 1 + 4 * 6
    index_size = len(files) * 12
    data_start = align(header_size + index_size, 2048)

    off = data_start
    for e, data in zip(entries, payloads):
        e[1] = off
        off += len(data)
    metadata_offset = align(off, 2048)

    meta_buf = io.BytesIO()
    meta_buf.write(struct.pack("<II", 2048, 0))
    for name, _ in files:
        name_bytes = name.encode("ascii") + b"\x00"
        meta_buf.write(struct.pack("<III", 0, 0, 0))
        meta_buf.write(struct.pack("<I", len(name_bytes)))
        meta_buf.write(name_bytes)
        meta_buf.write(b"\x00\x00\x00")
    metadata_size = meta_buf.tell()

    with open(path, "wb") as f:
        f.write(MAGIC.ljust(24, b"\x00"))
        f.write(b"\x00" * 8)
        f.write(struct.pack("<BBBB", 2, 1, 0, 1))
        f.write(struct.pack("<IIIIII", header_size, index_size, metadata_offset, metadata_size, 0, len(files)))
        for name_hash, o, size in entries:
            f.write(struct.pack("<III", name_hash, o, size))
        f.seek(data_start)
        for data in payloads:
            f.write(data)
        f.seek(metadata_offset)
        f.write(meta_buf.getvalue())


def make_rz_payload(raw: bytes) -> bytes:
    compressed = zlib.compress(raw)
    return struct.pack(">I", RZ_MAGIC) + struct.pack("<III", 0, len(raw), 0) + compressed


def main():
    tmpdir = tempfile.mkdtemp(prefix="rcf_test_")
    rcf_path = os.path.join(tmpdir, "synthetic.rcf")
    out_dir = os.path.join(tmpdir, "out")

    plain_data = b"hello prototype world\x00\x01\x02" * 10
    rz_inner = b"this is compressed test content " * 50
    files = [
        ("\\art\\characters\\dummy_fig.p3d", plain_data),
        ("\\art\\characters\\dummy_anim.p3d.rz", make_rz_payload(rz_inner)),
    ]

    build_synthetic_rcf_fixed(rcf_path, files)

    cement = CementFile.load(rcf_path)
    assert len(cement.entries) == 2, f"expected 2 entries, got {len(cement.entries)}"
    assert len(cement.metadatas) == 2, f"expected 2 metadatas, got {len(cement.metadatas)}"

    names = sorted(m.name for m in cement.metadatas)
    expected_names = sorted(n for n, _ in files)
    assert names == expected_names, f"metadata names mismatch: {names} != {expected_names}"

    # hash lookup roundtrip
    for name, _ in files:
        h = hash_file_name(name)
        md = cement.get_metadata(h)
        assert md is not None, f"hash lookup failed for {name}"
        assert md.name == name

    manifest = extract(rcf_path, out_dir, unpack_rz=True, overwrite=True, verbose=False)
    assert len(manifest) == 2

    extracted_plain = os.path.join(out_dir, "art", "characters", "dummy_fig.p3d")
    assert os.path.exists(extracted_plain), "plain entry not extracted to expected path"
    with open(extracted_plain, "rb") as f:
        assert f.read() == plain_data, "plain entry content mismatch"

    extracted_rz = os.path.join(out_dir, "art", "characters", "dummy_anim.p3d")
    assert os.path.exists(extracted_rz), ".rz entry not extracted (or extension not stripped)"
    with open(extracted_rz, "rb") as f:
        assert f.read() == rz_inner, ".rz entry decompressed content mismatch"

    print("OK: synthetic RCF round-trip (hash lookup + plain extraction + .rz zlib decompression) passed")
    print(f"(scratch dir: {tmpdir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
