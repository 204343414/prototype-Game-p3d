#!/usr/bin/env python3
"""
RCF (ATG CORE CEMENT LIBRARY) archive extractor for Prototype (2009).

Pure-Python port of gibbed/Gibbed.Prototype's Gibbed.Prototype.Unpack tool
(references/gibbed-prototype/Gibbed.Prototype.Unpack/Program.cs) and its
underlying CementFile/Cement.Entry/Cement.Metadata format
(Gibbed.Prototype.FileFormats/CementFile.cs, Cement/Entry.cs, Cement/Metadata.cs).

License note: this is an independent re-implementation written for this
project, based on reading the MIT/zlib-licensed C# source referenced above
(see references/gibbed-prototype/license.txt). No original binary or game
asset is included here. See NOTICE.md for usage restrictions.

Usage:
    python3 rcf_extract.py <input.rcf> [output_dir] [--rz] [--overwrite] [-v]

    --rz          also decompress *.rz entries (zlib-compressed payloads,
                   magic "RZ\\0\\0") into their uncompressed form and strip
                   the .rz extension
    --overwrite   overwrite existing files in output_dir
    -v/--verbose  print each entry as it's extracted
"""
import argparse
import os
import struct
import sys
import zlib


MAGIC = b"ATG CORE CEMENT LIBRARY"
RZ_MAGIC = 0x525A0000  # 'RZ\x00\x00' read as big-endian uint32


def hash_file_name(name: str, seed: int = 0) -> int:
    """Port of StringHelpers.HashFileName (Gibbed.Prototype.FileFormats).

    A DJB2-family hash: seed = seed*31 + byte (with ASCII lowercase folding
    for bytes < 0x61, i.e. this hashes an effectively-lowercased name).
    Leading backslash is stripped before hashing.
    """
    if name.startswith("\\"):
        name = name[1:]
    seed &= 0xFFFFFFFF
    for b in name.encode("ascii", errors="strict"):
        seed = ((seed << 5) - seed) & 0xFFFFFFFF
        if b < 0x61:
            seed = (seed + (0x20 + b)) & 0xFFFFFFFF
        else:
            seed = (seed + b) & 0xFFFFFFFF
    return seed


class Entry:
    __slots__ = ("name_hash", "offset", "size")

    def __init__(self, name_hash, offset, size):
        self.name_hash = name_hash
        self.offset = offset
        self.size = size


class Metadata:
    __slots__ = ("type_hash", "alignment", "name")

    def __init__(self, type_hash, alignment, name):
        self.type_hash = type_hash
        self.alignment = alignment
        self.name = name


def read_u32(f, endian):
    return struct.unpack(endian + "I", f.read(4))[0]


def read_u8(f):
    return struct.unpack("B", f.read(1))[0]


def read_cstring_u32_prefixed(f, endian):
    """Port of Gibbed.IO's WriteStringU32/ReadStringU32.

    IMPORTANT: the uint32 length prefix is `byteCount + 1` -- it already
    includes the trailing NUL terminator byte as part of its own count
    (see StreamHelpers.WriteStringU32: `WriteValueS32(byteCount + 1, ...)`
    followed by `WriteStringZ` which writes byteCount bytes + 1 NUL byte,
    for a total of exactly `byteCount+1` bytes matching the length field).
    So: read `length` bytes total, and the LAST byte of that block is the
    NUL terminator (not a separate field after it).
    """
    length = read_u32(f, endian)
    if length == 0:
        return ""
    data = f.read(length)
    if len(data) != length:
        raise ValueError("truncated length-prefixed string")
    if data[-1:] != b"\x00":
        raise ValueError(f"expected NUL terminator as last byte of string block, got {data[-1:]!r}")
    return data[:-1].decode("ascii", errors="replace")


class CementFile:
    def __init__(self):
        self.endian = "<"
        self.major_version = 0
        self.minor_version = 0
        self.entries = []
        self.metadatas = []
        self._metadata_by_hash = None

    def get_metadata(self, name_hash):
        if self._metadata_by_hash is None:
            self._metadata_by_hash = {}
            for m in self.metadatas:
                self._metadata_by_hash[hash_file_name(m.name)] = m
        return self._metadata_by_hash.get(name_hash)

    @classmethod
    def load(cls, path):
        self = cls()
        with open(path, "rb") as f:
            magic = f.read(24)
            magic = magic.split(b"\x00", 1)[0]
            if magic != MAGIC:
                raise ValueError(f"not a cement/.rcf file (magic={magic!r})")

            f.read(8)  # padding

            self.major_version = read_u8(f)
            self.minor_version = read_u8(f)
            endian_flag = read_u8(f)
            unknown1 = read_u8(f)

            if self.major_version != 2 or self.minor_version != 1 or unknown1 != 1:
                raise ValueError(
                    f"unexpected cement version "
                    f"{self.major_version}.{self.minor_version} unk1={unknown1} "
                    f"(expected 2.1 / 1 -- format may differ)"
                )

            endian = "<" if endian_flag == 0 else ">"
            self.endian = endian

            index_offset = read_u32(f, endian)
            index_size = read_u32(f, endian)
            metadata_offset = read_u32(f, endian)
            metadata_size = read_u32(f, endian)
            unknown2 = read_u32(f, endian)
            entry_count = read_u32(f, endian)

            if unknown2 != 0:
                raise ValueError("unexpected non-zero unknown2 field in cement header")

            f.seek(index_offset)
            index_bytes = f.read(index_size)
            if len(index_bytes) != index_size:
                raise ValueError("truncated entry index table")
            pos = 0
            for _ in range(entry_count):
                name_hash, offset, size = struct.unpack_from(endian + "III", index_bytes, pos)
                pos += 12
                self.entries.append(Entry(name_hash, offset, size))
            if pos != len(index_bytes):
                raise ValueError("entry index table size mismatch")

            f.seek(metadata_offset)
            # First 8 bytes of the metadata table are always little-endian
            # regardless of file endianness (see CementFile.Serialize:
            # `output.WriteValueU32(2048, Endian.Little)` etc for the
            # "names alignment"/"names unknown" header fields).
            names_alignment = read_u32(f, "<")
            names_unknown = read_u32(f, "<")
            for _ in range(entry_count):
                # Metadata.Serialize always writes TypeHash/Alignment/unknown2
                # as Little Endian too (hardcoded `Endian.Little` in the C#),
                # only the Name string uses the file's actual endianness.
                type_hash = read_u32(f, "<")
                alignment = read_u32(f, "<")
                unknown2b = read_u32(f, "<")
                if unknown2b != 0:
                    raise ValueError("unexpected non-zero unknown2 in metadata entry")
                name = read_cstring_u32_prefixed(f, endian)
                unk3 = f.read(3)
                if unk3 != b"\x00\x00\x00":
                    raise ValueError("unexpected non-zero Unknown3 in metadata entry")
                self.metadatas.append(Metadata(type_hash, alignment, name))

        return self


def decompress_rz_payload(raw: bytes) -> bytes:
    """Decode a Prototype `.rz` entry: 'RZ\\0\\0' magic + 3 x uint32
    (unknown1, uncompressed_size, unknown2) header, then a raw zlib
    (deflate, RFC1950) stream. NOT the Pure3D "LZR" algorithm -- this is
    a different, simpler compression layer used at the RCF entry level.
    """
    if len(raw) < 16 or struct.unpack(">I", raw[0:4])[0] != RZ_MAGIC:
        return raw  # not actually RZ-wrapped; return as-is

    unknown1, uncompressed_size, unknown2 = struct.unpack("<III", raw[4:16])
    if unknown1 != 0 or unknown2 != 0:
        raise ValueError("unexpected non-zero fields in .rz header")

    decompressor = zlib.decompressobj()
    data = decompressor.decompress(raw[16:], uncompressed_size)
    if len(data) != uncompressed_size:
        raise ValueError(
            f".rz decompression size mismatch: expected {uncompressed_size}, got {len(data)}"
        )
    return data


def extract(input_path, output_dir, unpack_rz=False, overwrite=False, verbose=False):
    cement = CementFile.load(input_path)
    os.makedirs(output_dir, exist_ok=True)

    unknown_dir = os.path.join(output_dir, "__UNKNOWN")
    manifest = []  # (entry_name, name_hash_hex, size, known)

    with open(input_path, "rb") as f:
        total = len(cement.entries)
        pad = len(str(total))
        for i, entry in enumerate(cement.entries, 1):
            metadata = cement.get_metadata(entry.name_hash)
            unpacking = False

            if metadata is None:
                entry_name = os.path.join("__UNKNOWN", f"{entry.name_hash:08X}")
                known = False
            else:
                entry_name = metadata.name
                if entry_name.startswith("\\"):
                    entry_name = entry_name[1:]
                entry_name = entry_name.replace("\\", os.sep)
                known = True
                if unpack_rz and entry_name.endswith(".rz"):
                    unpacking = True
                    entry_name = entry_name[: -len(".rz")]

            manifest.append((entry_name, f"{entry.name_hash:08X}", entry.size, known))

            entry_path = os.path.join(output_dir, entry_name)
            if not overwrite and os.path.exists(entry_path):
                if verbose:
                    print(f"[{i:>{pad}}/{total}] SKIP (exists) {entry_name}")
                continue

            if verbose:
                print(f"[{i:>{pad}}/{total}] {entry_name}")

            f.seek(entry.offset)
            raw = f.read(entry.size)

            os.makedirs(os.path.dirname(entry_path) or ".", exist_ok=True)
            with open(entry_path, "wb") as out:
                if unpacking:
                    try:
                        out.write(decompress_rz_payload(raw))
                    except Exception as e:  # noqa: BLE001
                        print(f"  WARNING: failed to decompress {entry_name}: {e}", file=sys.stderr)
                        out.write(raw)
                else:
                    out.write(raw)

    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input_rcf")
    parser.add_argument("output_dir", nargs="?", default=None)
    parser.add_argument("--rz", action="store_true", help="decompress *.rz entries")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing files")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--manifest", default=None, help="write a JSON manifest of all entries to this path")
    args = parser.parse_args()

    output_dir = args.output_dir or (os.path.splitext(args.input_rcf)[0] + "_unpack")

    manifest = extract(
        args.input_rcf,
        output_dir,
        unpack_rz=args.rz,
        overwrite=args.overwrite,
        verbose=args.verbose,
    )

    print(f"\nExtracted {len(manifest)} entries to {output_dir}")
    unknown_count = sum(1 for _, _, _, known in manifest if not known)
    if unknown_count:
        print(f"({unknown_count} entries had no metadata name match -- saved under __UNKNOWN/<hash>)")

    if args.manifest:
        import json
        with open(args.manifest, "w") as f:
            json.dump(
                [{"name": n, "hash": h, "size": s, "known": k} for n, h, s, k in manifest],
                f,
                indent=2,
            )
        print(f"Manifest written to {args.manifest}")


if __name__ == "__main__":
    main()
