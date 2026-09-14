#!/usr/bin/env python3
"""Smoke test for inspect_p3d.py using the real Prototype sample files
that ship inside the gibbed/Gibbed.Prototype repository (MIT/zlib licensed,
cloned into references/gibbed-prototype).

Run with:
    python3 tools/p3d_parser/test_inspect_p3d.py
"""
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(__file__))
from inspect_p3d import dump_chunk, SIG_LE, SIG_LE_SWAPPED  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLES_DIR = os.path.join(REPO_ROOT, "references", "gibbed-prototype", "other", "testfiles")


def load_and_walk(path):
    with open(path, "rb") as f:
        data = f.read()
    magic = struct.unpack("<I", data[0:4])[0]
    assert magic in (SIG_LE, SIG_LE_SWAPPED), f"{path}: not a Pure3D file"
    endian = "<" if magic == SIG_LE else ">"
    total_size, = struct.unpack(endian + "I", data[8:12])
    assert 12 <= total_size <= len(data), f"{path}: declared size {total_size} out of range (file is {len(data)} bytes)"
    out = []
    dump_chunk(data, 12, total_size, endian, 0, out)
    assert len(out) > 0, f"{path}: no chunks found"
    return out


def main():
    if not os.path.isdir(SAMPLES_DIR):
        print(f"SKIP: sample dir not found at {SAMPLES_DIR} "
              f"(did you clone references/gibbed-prototype?)")
        return 0

    p3d_files = [f for f in os.listdir(SAMPLES_DIR) if f.endswith(".p3d")]
    assert p3d_files, "no .p3d sample files found"

    failures = 0
    for fname in sorted(p3d_files):
        path = os.path.join(SAMPLES_DIR, fname)
        try:
            chunks = load_and_walk(path)
            print(f"OK   {fname}: {len(chunks)} chunks parsed")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fname}: {e}")

    if failures:
        print(f"\n{failures} file(s) failed to parse.")
        return 1

    print(f"\nAll {len(p3d_files)} sample .p3d files parsed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
