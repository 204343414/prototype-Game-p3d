#!/usr/bin/env python3
"""
Smoke test for viewer/server.py.

Spins up the server as a subprocess bound to 127.0.0.1 on a scratch port,
restricted to a temporary directory (--root), and checks:

  1. /api/health responds
  2. /api/browse lists files/dirs correctly
  3. /api/browse rejects paths that escape --root (403)
  4. /api/file serves file content correctly
  5. /api/file rejects paths that escape --root (403)
  6. static frontend (/ and /index.html) is served
"""
import base64
import gzip
import json
import struct
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
PORT = 8709  # fixed scratch port, unlikely to collide

sys.path.insert(0, os.path.join(HERE, "..", "tools", "rcf_unpack"))
from test_rcf_extract import build_synthetic_rcf_fixed, make_rz_payload  # noqa: E402


sys.path.insert(0, os.path.join(HERE, "..", "tools", "world"))
from test_export_static_geometry_diagnostic import geometry, p3d_file, triangle_group
from test_cell_materials import material_fixture


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    tmpdir = tempfile.mkdtemp(prefix="viewer_test_")
    os.makedirs(os.path.join(tmpdir, "subdir"))
    with open(os.path.join(tmpdir, "hello.txt"), "w") as f:
        f.write("hello viewer test")
    with open(os.path.join(tmpdir, "subdir", "model.glb"), "wb") as f:
        f.write(b"\x00" * 16)

    proc = subprocess.Popen(
        [sys.executable, SERVER, "--host", "127.0.0.1", "--port", str(PORT), "--root", tmpdir],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{PORT}"
    try:
        # wait for server to come up
        for _ in range(50):
            try:
                status, body = get(base + "/api/health")
                if status == 200:
                    break
            except (urllib.error.URLError, ConnectionError):
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("server never became healthy")

        data = json.loads(body)
        assert data["ok"] is True
        assert data["root"] == os.path.abspath(tmpdir), data

        # browse root
        status, body = get(base + f"/api/browse?path={tmpdir}")
        assert status == 200, (status, body)
        data = json.loads(body)
        names = sorted(e["name"] for e in data["entries"])
        assert names == ["hello.txt", "subdir"], names

        # browse subdir
        sub_path = os.path.join(tmpdir, "subdir")
        status, body = get(base + f"/api/browse?path={sub_path}")
        data = json.loads(body)
        assert [e["name"] for e in data["entries"]] == ["model.glb"], data

        # escape attempt should 403
        status, body = get(base + "/api/browse?path=/etc")
        assert status == 403, (status, body)

        # file read
        status, body = get(base + f"/api/file?path={os.path.join(tmpdir, 'hello.txt')}")
        assert status == 200
        assert body == b"hello viewer test", body

        # file escape attempt
        status, body = get(base + "/api/file?path=/etc/passwd")
        assert status == 403, (status, body)

        # static frontend
        status, body = get(base + "/")
        assert status == 200
        assert b"<html" in body.lower()

        # --- /api/hexdump ---
        binfile = os.path.join(tmpdir, "binary.dat")
        with open(binfile, "wb") as f:
            f.write(bytes(range(256)))  # 0x00..0xFF, easy to verify hex output

        status, body = get(base + f"/api/hexdump?path={binfile}&offset=0&length=16")
        assert status == 200, (status, body)
        data = json.loads(body)
        assert data["hex"] == bytes(range(16)).hex(), data
        assert data["file_size"] == 256
        assert data["length"] == 16

        status, body = get(base + f"/api/hexdump?path={binfile}&offset=250&length=100")
        data = json.loads(body)
        assert data["length"] == 6, data  # clamped to actual remaining bytes

        status, body = get(base + "/api/hexdump?path=/etc/passwd")
        assert status == 403, (status, body)

        # --- /api/p3d ---
        # Build a minimal synthetic Pure3D file: root chunk containing one
        # child chunk, matching the format inspect_p3d.py already parses
        # (and which real .p3d samples in references/gibbed-prototype have
        # been verified against).
        import struct as _struct

        def build_chunk(type_id, payload, children_bytes=b""):
            header_size = 12 + len(payload)
            total_size = header_size + len(children_bytes)
            return (_struct.pack("<III", type_id, header_size, total_size)
                    + payload + children_bytes)

        child = build_chunk(0x00019001, b"hello-payload")
        root_payload = b""
        root = build_chunk(0xFF443350, root_payload, child)
        # inspect_p3d expects the very first 12 bytes to be the file-level
        # magic/header_size/total_size, then chunk data starting at offset 12
        p3d_bytes = root

        p3dfile = os.path.join(tmpdir, "sample.p3d")
        with open(p3dfile, "wb") as f:
            f.write(p3d_bytes)

        status, body = get(base + f"/api/p3d?path={p3dfile}")
        assert status == 200, (status, body)
        data = json.loads(body)
        assert data["endian"] == "LE", data
        assert data["chunk_count"] == 1, data
        assert data["chunks"][0]["type_id"] == "0x00019001", data
        assert data["chunks"][0]["payload_len"] == len(b"hello-payload")

        status, body = get(base + "/api/p3d?path=/etc/passwd")
        assert status in (400, 403), (status, body)

        # --- /api/p3d: type_filter / offset / limit paging ---
        # Build a slightly bigger synthetic file with several sibling
        # chunks of two different types, to exercise filtering real game
        # files down to just the chunk types you care about (needed since
        # real .p3d files can have tens of thousands of chunks total).
        many_children = b"".join(
            build_chunk(0x00121000 if i % 2 == 0 else 0x00019001, f"chunk-{i}".encode())
            for i in range(10)
        )
        multi_root = build_chunk(0xFF443350, b"", many_children)
        multi_p3dfile = os.path.join(tmpdir, "multi.p3d")
        with open(multi_p3dfile, "wb") as f:
            f.write(multi_root)

        status, body = get(base + f"/api/p3d?path={multi_p3dfile}&type_filter=0x00121000")
        assert status == 200, (status, body)
        data = json.loads(body)
        assert data["chunk_count"] == 5, data
        assert data["total_chunk_count_unfiltered"] == 10, data
        assert all(c["type_id"] == "0x00121000" for c in data["chunks"]), data
        # global_index must be each chunk's position in the FULL unfiltered
        # depth-first list (0,2,4,6,8 for our alternating fixture), not its
        # position within the filtered result set -- this is what lets a
        # caller follow up with offset=<global_index> to pull a specific
        # chunk's full subtree out of a huge real file.
        assert [c["global_index"] for c in data["chunks"]] == [0, 2, 4, 6, 8], data

        status, body = get(base + f"/api/p3d?path={multi_p3dfile}&limit=3")
        data = json.loads(body)
        assert data["chunks_shown"] == 3, data
        assert data["chunk_count"] == 10, data  # unfiltered total, not just what's shown

        status, body = get(base + f"/api/p3d?path={multi_p3dfile}&offset=8")
        data = json.loads(body)
        assert data["chunks_shown"] == 2, data

        # --- /api/rcf_manifest ---
        # Build a small synthetic .rcf (Cement archive) using the same
        # fixture helper as tools/rcf_unpack/test_rcf_extract.py, then ask
        # the server to parse it purely via rcf_extract.CementFile.load()
        # and report back header fields + entry manifest as JSON. This is
        # what lets a remote/real .rcf be validated against the parser
        # without ever transferring the (often huge) archive itself.
        rcf_path = os.path.join(tmpdir, "synthetic.rcf")
        plain_data = b"hello prototype world\x00\x01\x02" * 10
        rz_inner = b"this is compressed test content " * 50
        rcf_files = [
            (r"\art\alex\alex_fig.p3d", plain_data),
            (r"\art\alex\alex_tod.p3d.rz", make_rz_payload(rz_inner)),
        ]
        build_synthetic_rcf_fixed(rcf_path, rcf_files)

        status, body = get(base + f"/api/rcf_manifest?path={rcf_path}")
        assert status == 200, (status, body)
        data = json.loads(body)
        assert data["entry_count"] == 2, data
        assert data["known_count"] == 2, data
        assert data["unknown_count"] == 0, data
        names = sorted(e["name"] for e in data["entries"])
        assert names == sorted(n for n, _ in rcf_files), names

        status, body = get(base + f"/api/rcf_manifest?path={rcf_path}&name_filter=alex_fig")
        data = json.loads(body)
        assert len(data["entries"]) == 1, data
        assert data["entries"][0]["name"] == r"\art\alex\alex_fig.p3d", data

        status, body = get(base + "/api/rcf_manifest?path=/etc/passwd")
        assert status in (403, 404, 500), (status, body)

        # --- /api/rcf_entry ---
        # One-shot "extract by name + auto .rz decompress + parse chunk
        # tree" endpoint, built on top of the same synthetic .rcf, but this
        # time one entry's payload is itself a (tiny) synthetic Pure3D file
        # so we exercise the whole rcf -> rz -> chunk-tree pipeline at once.
        def build_chunk(type_id, payload, children_bytes=b""):
            header_size = 12 + len(payload)
            total_size = header_size + len(children_bytes)
            return (_struct.pack("<III", type_id, header_size, total_size)
                    + payload + children_bytes)

        entry_child = build_chunk(0x00019001, b"hello-payload")
        entry_p3d_bytes = build_chunk(0xFF443350, b"", entry_child)

        rcf2_path = os.path.join(tmpdir, "synthetic2.rcf")
        rcf2_files = [
            (r"\art\alex\alex_fig.p3d", plain_data),
            (r"\art\alex\alex_tod.p3d.rz", make_rz_payload(entry_p3d_bytes)),
        ]
        build_synthetic_rcf_fixed(rcf2_path, rcf2_files)

        entry_name_q = urllib.parse.quote(r"\art\alex\alex_tod.p3d.rz")
        status, body = get(base + f"/api/rcf_entry?path={rcf2_path}&name={entry_name_q}")
        assert status == 200, (status, body)
        data = json.loads(body)
        assert data["was_rz_compressed"] is True, data
        assert data["chunk_count"] == 1, data
        assert data["chunks"][0]["type_id"] == "0x00019001", data

        fig_name_q = urllib.parse.quote(r"\art\alex\alex_fig.p3d")
        status, body = get(base + f"/api/rcf_entry?path={rcf2_path}&name={fig_name_q}&raw=1")
        assert status == 200, (status, body)
        assert body == plain_data, body

        status, body = get(base + f"/api/rcf_entry?path={rcf2_path}&name=" + urllib.parse.quote(r"\art\nope.p3d"))
        assert status == 404, (status, body)

        # --- /api/rcf_rigged_manifest ---
        # This is deliberately a structural-only endpoint.  Build one tiny
        # rigged package and one valid but unrigged P3D, then confirm only the
        # former qualifies without any mesh/texture bytes leaving the server.
        def p3d_string(value):
            raw = value.encode("utf-8") + b"\0"
            return bytes([len(raw)]) + raw

        skeleton_payload = p3d_string("test_skeleton") + _struct.pack("<IIII", 1, 67, 13, 4)
        skeleton_chunk = build_chunk(0x00023000, skeleton_payload)
        ref_payload = (_struct.pack("<II", 1, 0) + p3d_string("test_body")
                       + _struct.pack("<II", 2, 0))
        reference_chunk = build_chunk(0x00123001, ref_payload)
        composite_payload = (_struct.pack("<I", 1) + p3d_string("test_soldier")
                             + p3d_string("test_skeleton") + _struct.pack("<I", 1))
        composite_chunk = build_chunk(0x00123000, composite_payload, reference_chunk)
        primitive_group = build_chunk(0x00010020, _struct.pack("<I", 1) + p3d_string("test_shader"))
        rigged_p3d = build_chunk(0xFF443350, b"", skeleton_chunk + composite_chunk + primitive_group)
        plain_p3d = build_chunk(0xFF443350, b"", build_chunk(0x00019001, b"texture"))
        rcf3_path = os.path.join(tmpdir, "synthetic_rigged.rcf")
        build_synthetic_rcf_fixed(rcf3_path, [
            (r"\art\props\plain.p3d.rz", make_rz_payload(plain_p3d)),
            (r"\art\characters\soldier.p3d.rz", make_rz_payload(rigged_p3d)),
        ])
        status, body = get(base + f"/api/rcf_rigged_manifest?path={rcf3_path}&limit=10")
        assert status == 200, (status, body)
        data = json.loads(body)
        assert data["eligible_entry_count"] == 2, data
        assert data["scanned_entry_count"] == 2, data
        assert data["complete"] is True, data
        assert data["returned_record_count"] == 1, data
        candidate = data["records"][0]
        assert candidate["entry_name"] == r"\art\characters\soldier.p3d.rz", candidate
        assert candidate["skeletons"][0]["joint_count"] == 67, candidate
        assert candidate["rigged_composites"][0]["name"] == "test_soldier", candidate
        assert candidate["rigged_composites"][0]["skeleton_is_local"] is True, candidate
        assert candidate["shader_names"] == ["test_shader"], candidate

        status, body = get(base + f"/api/rcf_rigged_manifest?path={rcf3_path}&limit=10&include_nonrigged=1")
        assert status == 200, (status, body)
        data = json.loads(body)
        assert data["returned_record_count"] == 2, data
        assert data["nonrigged_count_in_page"] == 1, data

        # --- /api/rcf_cell_geometry_manifest ---
        # A static Cell fixture has Geometry -> PrimitiveGroup -> memory vertex
        # list.  The map endpoint must retain only bounds/count metadata and
        # explicitly mark the paired 33-byte sparse Cell as a placeholder.
        map_vertices = _struct.pack("<4f", -1.0, 2.0, 3.0, 0.0) + _struct.pack("<4f", 4.0, -5.0, 6.0, 0.0)
        map_vertex_list = build_chunk(0x00010012, _struct.pack("<III", 1, 2, len(map_vertices)) + map_vertices)
        map_group_payload = (_struct.pack("<I", 1) + p3d_string("map_shader")
                             + _struct.pack("<9I", 0, 0x3011, 2, 6, 0, 1, 1, 0, 0))
        map_group = build_chunk(0x00010020, map_group_payload, map_vertex_list)
        map_geometry = build_chunk(0x00010000, p3d_string("map_geometry") + _struct.pack("<I", 1), map_group)
        map_p3d = build_chunk(0xFF443350, b"", map_geometry)
        rcf4_path = os.path.join(tmpdir, "synthetic_cells.rcf")
        build_synthetic_rcf_fixed(rcf4_path, [
            (r"\art\locations\manhattan\manhattan_Cell_0.p3d.rz", b"x" * 33),
            (r"\art\locations\manhattan\manhattan_Cell_2.p3d.rz", make_rz_payload(map_p3d)),
            (r"\art\locations\manhattan\manhattan_Cell_2_ft.p3d.rz", make_rz_payload(plain_p3d)),
        ])
        status, body = get(base + f"/api/rcf_cell_geometry_manifest?path={rcf4_path}&limit=10")
        assert status == 200, (status, body)
        data = json.loads(body)
        assert data["eligible_cell_count"] == 2, data
        assert data["scanned_cell_count"] == 2, data
        assert data["complete"] is True, data
        assert [record["cell_index"] for record in data["records"]] == [0, 2], data
        assert data["records"][0]["status"] == "placeholder", data
        scanned_cell = data["records"][1]
        assert scanned_cell["status"] == "scanned", scanned_cell
        assert scanned_cell["geometry_count"] == 1, scanned_cell
        assert scanned_cell["vertex_stride_counts"] == {"16": 1}, scanned_cell
        assert scanned_cell["world_position_min"] == [-1.0, -5.0, 3.0], scanned_cell
        assert scanned_cell["world_position_max"] == [4.0, 2.0, 6.0], scanned_cell

        # Exercise the real RCF -> RZ -> strict triangles -> packed response path.
        cells_path = os.path.join(tmpdir, "preview_cells.rcf")
        good = p3d_file(geometry("mergedDrawableRootNoShadow", [
            triangle_group([(0, 0, 0), (3, 0, 0), (0, 4, 0)], [0, 1, 2])]))
        bad = p3d_file(geometry("mergedDrawableRootNoShadow", [
            triangle_group([(0, 0, 0), (3, 0, 0), (0, 4, 0)], [0, 1, 9])]))
        prefix = "\\art\\locations\\manhattan\\manhattan_Cell_"
        build_synthetic_rcf_fixed(cells_path, [
            (prefix + "2.p3d.rz", make_rz_payload(good)),
            (prefix + "3.p3d.rz", make_rz_payload(bad)),
            (prefix + "0.p3d.rz", make_rz_payload(p3d_file(b""))),
            (prefix + "6.p3d.rz", b"RZ\0\0" + struct.pack("<III", 0, 65 * 1024 * 1024, 0)),
            (prefix + "7.p3d.rz", b"RZ\0\0" + struct.pack("<III", 0, 12, 0) + b"corrupt"),
            (prefix + "8.p3d.rz", b"RZ\0\0" + struct.pack("<III", 0, 0, 0)),
            (prefix + "9.p3d.rz", make_rz_payload(material_fixture())),
            (prefix + "10.p3d.rz", make_rz_payload(material_fixture(include_texture=False))),
        ])
        endpoint = base + "/api/rcf_cell_preview?" + urllib.parse.urlencode({"path": cells_path})
        status, body = get(endpoint + "&cell=2")
        assert status == 200, (status, body)
        preview = json.loads(body)
        assert preview["status"] == "ready" and preview["textured"] is False
        assert preview["report"]["accepted_triangle_count"] == 1
        assert struct.unpack("<3H", base64.b64decode(preview["meshes"][0]["i"])) == (0, 1, 2)
        assert len(base64.b64decode(preview["meshes"][0]["p"])) == 36
        assert json.loads(get(endpoint + "&cell=0")[1])["status"] == "empty"
        status, body = get(endpoint + "&cell=3")
        assert status == 422 and "error" in json.loads(body) and "meshes" not in json.loads(body)
        assert get(endpoint + "&cell=6")[0] == 413
        assert get(endpoint + "&cell=7")[0] == 422
        assert get(endpoint + "&cell=8")[0] == 422
        assert get(base + "/map-view.js")[0] == 200
        status, body = get(endpoint + "&cell=9&materials=1")
        assert status == 200, (status, body)
        textured = json.loads(body)
        assert textured["textured"] and textured["materials"]["linked_groups"] == 2
        assert len(textured["textures"]) == 1
        assert textured["textures"][0]["format"] == "DXT1"
        assert get(endpoint + "&cell=9&materials=bad")[0] == 400
        plain = json.loads(get(endpoint + "&cell=9")[1])
        assert plain["textured"] is False and "textures" not in plain
        shared_path = os.path.join(tmpdir, "shared_art.rcf")
        build_synthetic_rcf_fixed(shared_path, [
            (r"\art\locations\manhattan\textures.p3d.rz", make_rz_payload(material_fixture()))])
        query = endpoint + "&cell=10&materials=1&" + urllib.parse.urlencode({"shared_path": shared_path})
        status, body = get(query)
        assert status == 200 and json.loads(body)["materials"]["shared_groups"] == 2
        with urllib.request.urlopen(urllib.request.Request(query, headers={"Accept-Encoding": "gzip"})) as response:
            assert response.headers['Content-Encoding'] == 'gzip'
            assert json.loads(gzip.decompress(response.read()))['materials']['shared_groups'] == 2
        assert get(endpoint + "&cell=10&materials=1&shared_path=/etc/passwd")[0] == 403
        # Replacing shared source metadata invalidates its process-only index.
        build_synthetic_rcf_fixed(shared_path, [
            (r"\art\locations\manhattan\textures.p3d.rz", make_rz_payload(p3d_file(b"")))])
        assert json.loads(get(query)[1])["materials"]["shared_groups"] == 0

        for cell in ("-1", "260", "bad", "2_ft", "2.5"):
            assert get(endpoint + "&cell=" + cell)[0] == 400, cell
        assert get(endpoint)[0] == 400
        assert get(endpoint + "&cell=4")[0] == 404
        assert get(base + "/api/rcf_cell_preview?cell=2")[0] == 400
        assert get(base + "/api/rcf_cell_preview?cell=2&path=/etc/passwd")[0] == 403
        link = os.path.join(tmpdir, "outside.rcf")
        os.symlink("/etc/passwd", link)
        assert get(base + "/api/rcf_cell_preview?cell=2&path=" + link)[0] == 403
        assert get(base + "/api/heightmap_preview?path=" + cells_path)[0] == 404

        print("OK: viewer/server.py smoke test passed "
              "(browse + file + hexdump + p3d + rcf_manifest + rcf_rigged_manifest + rcf_cell_geometry_manifest + rcf_entry + rcf_cell_preview APIs, "
              "root confinement, static frontend)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
