#!/usr/bin/env python3
"""
本地资源查看器后端 (local asset viewer backend)

纯 Python 标准库实现（不需要 pip install 任何东西），职责：

  1. 托管 viewer/static/ 下的前端页面（three.js 查看器）
  2. 提供一个本地目录浏览 API，让前端的"输入本地文件路径"功能可以
     列出文件夹内容、找到 .glb/.gltf 文件
  3. 提供一个原始文件读取 API，把本地任意文件（模型/贴图等）用
     HTTP 提供给浏览器里的 three.js 加载

⚠️ 安全说明：这个 server 默认只绑定 127.0.0.1（本机回环地址），
不会暴露给局域网/公网。它会读取你在浏览器里输入路径所指向的**任意本地
文件**并返回给浏览器——这是设计上就如此（你自己的电脑，看自己的文件），
但**不要**把 --host 改成 0.0.0.0 并暴露到不受信任的网络环境，否则相当于
把你的文件系统内容开放给任何能访问这个端口的人。

Usage:
    python3 server.py [--port 8420] [--host 127.0.0.1] [--root /]

    --root 限制可浏览/可读取的根目录（默认为文件系统根 "/"，即不限制；
    如果你只想暴露某个解包出来的目录，传 --root /path/to/unpacked 更安全）
"""
import argparse
import http.server
import json
import mimetypes
import os
import socketserver
import struct
import sys
import urllib.parse

# Reuse the already-verified generic Pure3D chunk walker instead of
# duplicating chunk-tree logic here. This lets a collaborating AI ask the
# server to parse a real .p3d file server-side and get back structured
# JSON, instead of having to transfer/hexdump the whole binary file over
# the (potentially slow/unreliable) tunnel just to see its chunk tree.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "p3d_parser"))
try:
    import inspect_p3d  # noqa: E402
except ImportError:
    inspect_p3d = None

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "rcf_unpack"))
try:
    import rcf_extract  # noqa: E402
except ImportError:
    rcf_extract = None



STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")



class Handler(http.server.BaseHTTPRequestHandler):
    root_dir = "/"  # overridden by main() via a subclass factory

    def log_message(self, fmt, *args):
        # quieter default logging
        print("[viewer] " + (fmt % args))

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status=status)

    def _safe_resolve(self, rel_or_abs_path):
        """Resolve a user-supplied path, preventing escape via '..' when
        root_dir is not '/'.

        The frontend always works with absolute paths (it round-trips the
        "path" value returned by /api/browse), with the single exception of
        the initial "/" placeholder in the input box. So here:

          - "/" (or empty) always means "the configured root" (root_dir
            itself, or the real filesystem root "/" when unrestricted).
          - any other value is treated as an absolute path and, in
            restricted mode, must resolve to be inside root_dir.
        """
        if not rel_or_abs_path or rel_or_abs_path == "/":
            return os.path.abspath(self.root_dir)

        candidate = os.path.abspath(rel_or_abs_path)

        if self.root_dir == "/":
            # full filesystem mode: accept absolute paths as-is
            return candidate

        root_abs = os.path.abspath(self.root_dir)
        if not (candidate == root_abs or candidate.startswith(root_abs + os.sep)):
            raise PermissionError("path escapes configured root directory")
        return candidate

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/browse":
            return self._handle_browse(qs)
        if parsed.path == "/api/file":
            return self._handle_file(qs)
        if parsed.path == "/api/hexdump":
            return self._handle_hexdump(qs)
        if parsed.path == "/api/p3d":
            return self._handle_p3d(qs)
        if parsed.path == "/api/rcf_manifest":
            return self._handle_rcf_manifest(qs)
        if parsed.path == "/api/rcf_entry":
            return self._handle_rcf_entry(qs)
        if parsed.path == "/api/health":
            return self._send_json({"ok": True, "root": self.root_dir})

        # static file serving (frontend)
        return self._handle_static(parsed.path)

    def _handle_browse(self, qs):
        path = qs.get("path", ["/"])[0]
        try:
            target = self._safe_resolve(path)
        except PermissionError as e:
            return self._send_error_json(str(e), 403)

        if not os.path.isdir(target):
            return self._send_error_json(f"not a directory: {target}", 404)

        try:
            entries = []
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        size = -1 if is_dir else entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        is_dir, size = False, -1
                    entries.append({
                        "name": entry.name,
                        "is_dir": is_dir,
                        "size": size,
                    })
            entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        except PermissionError:
            return self._send_error_json(f"permission denied: {target}", 403)

        parent = os.path.dirname(target.rstrip(os.sep)) or os.sep
        self._send_json({
            "path": target,
            "parent": parent,
            "entries": entries,
        })

    def _handle_file(self, qs):
        path = qs.get("path", [None])[0]
        if not path:
            return self._send_error_json("missing ?path=", 400)
        try:
            target = self._safe_resolve(path)
        except PermissionError as e:
            return self._send_error_json(str(e), 403)

        if not os.path.isfile(target):
            return self._send_error_json(f"not a file: {target}", 404)

        ctype, _ = mimetypes.guess_type(target)
        if target.endswith(".glb"):
            ctype = "model/gltf-binary"
        elif target.endswith(".gltf"):
            ctype = "model/gltf+json"
        if not ctype:
            ctype = "application/octet-stream"

        size = os.path.getsize(target)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        with open(target, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _handle_hexdump(self, qs):
        """Read a byte range of an arbitrary local file and return it as
        JSON (hex string + best-effort ASCII preview), for safely inspecting
        binary files (like real game .rcf/.p3d archives) over the tunnel
        without risking bytes getting mangled by a text/markdown-oriented
        page fetcher.

        Query params:
          path   - required, absolute path to the file
          offset - byte offset to start at (default 0)
          length - number of bytes to read (default 256, max 65536 to keep
                   responses small and fast over a tunnel)
        """
        path = qs.get("path", [None])[0]
        if not path:
            return self._send_error_json("missing ?path=", 400)
        try:
            target = self._safe_resolve(path)
        except PermissionError as e:
            return self._send_error_json(str(e), 403)

        if not os.path.isfile(target):
            return self._send_error_json(f"not a file: {target}", 404)

        try:
            offset = int(qs.get("offset", ["0"])[0])
            length = int(qs.get("length", ["256"])[0])
        except ValueError:
            return self._send_error_json("offset/length must be integers", 400)

        length = max(0, min(length, 65536))
        file_size = os.path.getsize(target)

        with open(target, "rb") as f:
            f.seek(offset)
            data = f.read(length)

        ascii_preview = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
        self._send_json({
            "path": target,
            "file_size": file_size,
            "offset": offset,
            "length": len(data),
            "hex": data.hex(),
            "ascii": ascii_preview,
        })

    def _handle_p3d(self, qs):
        """Parse a local .p3d file server-side using the already-verified
        tools/p3d_parser/inspect_p3d.py chunk walker, and return the chunk
        tree as structured JSON. This lets a collaborating AI inspect a
        real game .p3d file's structure without transferring the whole
        (potentially large) binary file over the tunnel.

        Query params:
          path        - required, absolute path to a .p3d file
          max_depth   - optional, limit recursion depth (default: unlimited)
          payload_preview - optional, how many bytes of each chunk's payload
                       to include as a hex preview (default 32, max 256)
        """
        if inspect_p3d is None:
            return self._send_error_json("inspect_p3d module not available on server", 500)

        path = qs.get("path", [None])[0]
        if not path:
            return self._send_error_json("missing ?path=", 400)
        try:
            target = self._safe_resolve(path)
        except PermissionError as e:
            return self._send_error_json(str(e), 403)

        if not os.path.isfile(target):
            return self._send_error_json(f"not a file: {target}", 404)

        max_depth = qs.get("max_depth", [None])[0]
        max_depth = int(max_depth) if max_depth is not None else None
        payload_preview = int(qs.get("payload_preview", ["32"])[0])
        payload_preview = max(0, min(payload_preview, 256))

        try:
            with open(target, "rb") as f:
                data = f.read()

            if len(data) < 12:
                return self._send_error_json("file too small to be a Pure3D file", 400)

            magic, = struct.unpack("<I", data[0:4])
            if magic == inspect_p3d.SIG_LE:
                endian = "<"
            elif magic == inspect_p3d.SIG_LE_SWAPPED:
                endian = ">"
            else:
                return self._send_error_json(
                    f"not a Pure3D file (magic bytes = {data[0:4].hex()})", 400)

            header_size, = struct.unpack(endian + "I", data[4:8])
            total_size, = struct.unpack(endian + "I", data[8:12])

            out = []
            inspect_p3d.dump_chunk(data, 12, total_size, endian, 0, out, max_depth)

            chunks = []
            for depth, type_id, hdr_size, tot_size, payload in out:
                chunks.append({
                    "depth": depth,
                    "type_id": f"0x{type_id:08X}",
                    "header_size": hdr_size,
                    "total_size": tot_size,
                    "payload_len": len(payload),
                    "payload_hex_preview": payload[:payload_preview].hex(),
                })

            self._send_json({
                "path": target,
                "file_size": len(data),
                "endian": "LE" if endian == "<" else "BE",
                "declared_total_size": total_size,
                "chunk_count": len(chunks),
                "chunks": chunks,
            })
        except Exception as e:  # noqa: BLE001 - surface parse errors to the caller
            return self._send_error_json(f"parse error: {type(e).__name__}: {e}", 500)

    def _handle_rcf_manifest(self, qs):
        """Parse a local .rcf (Cement archive) file server-side using
        tools/rcf_unpack/rcf_extract.py's CementFile.load(), WITHOUT
        extracting/decompressing any entry payloads, and return the header
        fields + a manifest of entries (name, hash, offset, size, whether
        metadata matched) as JSON.

        This lets a collaborating AI validate the RCF parser against a
        real, possibly 100s-of-MB .rcf file without transferring the file
        itself over the tunnel -- only the resulting small JSON manifest
        crosses the wire.

        Query params:
          path        - required, absolute path to a .rcf file
          limit       - optional, max number of entries to include in the
                        "entries" list (default 200, use 0 for "all" --
                        careful, entry_count can be in the thousands)
          name_filter - optional, case-insensitive substring filter applied
                        to entry names before limiting (handy for e.g.
                        name_filter=alex to find a specific character's
                        files without listing thousands of entries)
        """
        if rcf_extract is None:
            return self._send_error_json("rcf_extract module not available on server", 500)

        path = qs.get("path", [None])[0]
        if not path:
            return self._send_error_json("missing ?path=", 400)
        try:
            target = self._safe_resolve(path)
        except PermissionError as e:
            return self._send_error_json(str(e), 403)

        if not os.path.isfile(target):
            return self._send_error_json(f"not a file: {target}", 404)

        limit = int(qs.get("limit", ["200"])[0])
        name_filter = qs.get("name_filter", [None])[0]

        try:
            cement = rcf_extract.CementFile.load(target)
        except Exception as e:  # noqa: BLE001
            return self._send_error_json(f"RCF parse error: {type(e).__name__}: {e}", 500)

        entries_out = []
        known_count = 0
        unknown_count = 0
        for entry in cement.entries:
            metadata = cement.get_metadata(entry.name_hash)
            if metadata is not None:
                known_count += 1
                name = metadata.name
            else:
                unknown_count += 1
                name = None

            if name_filter and (name is None or name_filter.lower() not in name.lower()):
                continue

            if limit == 0 or len(entries_out) < limit:
                entries_out.append({
                    "name_hash": f"0x{entry.name_hash:08X}",
                    "name": name,
                    "offset": entry.offset,
                    "size": entry.size,
                })

        self._send_json({
            "path": target,
            "file_size": os.path.getsize(target),
            "endian": "LE" if cement.endian == "<" else "BE",
            "major_version": cement.major_version,
            "minor_version": cement.minor_version,
            "entry_count": len(cement.entries),
            "metadata_count": len(cement.metadatas),
            "known_count": known_count,
            "unknown_count": unknown_count,
            "entries_shown": len(entries_out),
            "entries_truncated": (limit != 0 and (known_count + unknown_count) > limit and not name_filter),
            "entries": entries_out,
        })

    def _handle_rcf_entry(self, qs):
        """Extract a single named entry from a local .rcf archive (using
        rcf_extract.CementFile), auto-decompress it if it's .rz-wrapped
        (zlib), and return either the raw bytes (?raw=1) or, if it looks
        like a Pure3D (.p3d) file, its parsed chunk tree as JSON (same
        shape as /api/p3d).

        This is the one-shot "give me this character's model structure"
        entry point: no need to unpack the whole archive to disk first.

        Query params:
          path   - required, absolute path to the .rcf file
          name   - required, the entry's logical name as stored in the
                   Cement metadata table, e.g. \\art\\alex\\alex_fig.p3d.rz
                   (leading backslash optional, matched exactly against
                   metadata names -- case-sensitive, since that's how the
                   hash table is keyed)
          raw    - optional, "1" to return the (decompressed) raw bytes
                   with an appropriate Content-Type instead of a parsed
                   chunk tree (handy for saving out a .p3d/.dds/etc to
                   look at locally, or for chaining into other tools)
          max_depth / payload_preview - same meaning as in /api/p3d
        """
        if rcf_extract is None:
            return self._send_error_json("rcf_extract module not available on server", 500)
        if inspect_p3d is None:
            return self._send_error_json("inspect_p3d module not available on server", 500)

        path = qs.get("path", [None])[0]
        name = qs.get("name", [None])[0]
        if not path or not name:
            return self._send_error_json("missing ?path= or ?name=", 400)
        try:
            target = self._safe_resolve(path)
        except PermissionError as e:
            return self._send_error_json(str(e), 403)
        if not os.path.isfile(target):
            return self._send_error_json(f"not a file: {target}", 404)

        try:
            cement = rcf_extract.CementFile.load(target)
        except Exception as e:  # noqa: BLE001
            return self._send_error_json(f"RCF parse error: {type(e).__name__}: {e}", 500)

        lookup_name = name if name.startswith("\\") else "\\" + name
        name_hash = rcf_extract.hash_file_name(lookup_name)
        entry = next((e for e in cement.entries if e.name_hash == name_hash), None)
        if entry is None:
            return self._send_error_json(
                f"entry not found for name={lookup_name!r} (hash=0x{name_hash:08X})", 404)

        with open(target, "rb") as f:
            f.seek(entry.offset)
            raw = f.read(entry.size)

        is_rz = lookup_name.lower().endswith(".rz")
        try:
            data = rcf_extract.decompress_rz_payload(raw) if is_rz else raw
        except Exception as e:  # noqa: BLE001
            return self._send_error_json(f".rz decompression error: {type(e).__name__}: {e}", 500)

        want_raw = qs.get("raw", ["0"])[0] == "1"
        if want_raw:
            ctype = "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return

        if len(data) < 12:
            return self._send_error_json("entry too small to be a Pure3D file", 400)

        magic, = struct.unpack("<I", data[0:4])
        if magic == inspect_p3d.SIG_LE:
            endian = "<"
        elif magic == inspect_p3d.SIG_LE_SWAPPED:
            endian = ">"
        else:
            return self._send_error_json(
                f"not a Pure3D file after decompression (magic bytes = {data[0:4].hex()}); "
                f"pass &raw=1 to fetch the raw decompressed bytes instead", 400)

        header_size, = struct.unpack(endian + "I", data[4:8])
        total_size, = struct.unpack(endian + "I", data[8:12])

        max_depth = qs.get("max_depth", [None])[0]
        max_depth = int(max_depth) if max_depth is not None else None
        payload_preview = int(qs.get("payload_preview", ["32"])[0])
        payload_preview = max(0, min(payload_preview, 256))

        try:
            out = []
            inspect_p3d.dump_chunk(data, 12, total_size, endian, 0, out, max_depth)
            chunks = []
            for depth, type_id, hdr_size, tot_size, payload in out:
                chunks.append({
                    "depth": depth,
                    "type_id": f"0x{type_id:08X}",
                    "header_size": hdr_size,
                    "total_size": tot_size,
                    "payload_len": len(payload),
                    "payload_hex_preview": payload[:payload_preview].hex(),
                })
        except Exception as e:  # noqa: BLE001
            return self._send_error_json(f"chunk parse error: {type(e).__name__}: {e}", 500)

        self._send_json({
            "rcf_path": target,
            "entry_name": lookup_name,
            "entry_name_hash": f"0x{name_hash:08X}",
            "entry_offset": entry.offset,
            "entry_size_compressed": entry.size,
            "decompressed_size": len(data),
            "was_rz_compressed": is_rz,
            "endian": "LE" if endian == "<" else "BE",
            "declared_total_size": total_size,
            "chunk_count": len(chunks),
            "chunks": chunks,
        })

    def _handle_static(self, url_path):
        if url_path == "/":
            url_path = "/index.html"
        safe_rel = url_path.lstrip("/")
        target = os.path.abspath(os.path.join(STATIC_DIR, safe_rel))
        if not target.startswith(os.path.abspath(STATIC_DIR)):
            return self._send_error_json("forbidden", 403)
        if not os.path.isfile(target):
            return self._send_error_json(f"not found: {url_path}", 404)

        ctype, _ = mimetypes.guess_type(target)
        if not ctype:
            ctype = "application/octet-stream"
        size = os.path.getsize(target)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(target, "rb") as f:
            self.wfile.write(f.read())


def make_handler(root_dir):
    class BoundHandler(Handler):
        pass
    BoundHandler.root_dir = root_dir
    return BoundHandler


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    # Must be a class attribute (checked in server_bind(), before our
    # __init__ body would otherwise get a chance to set it on the instance).
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--host", default="127.0.0.1",
                         help="bind address; keep as 127.0.0.1 unless you know what you're doing")
    parser.add_argument("--root", default="/",
                         help="restrict browsing/reading to this directory (default: no restriction)")
    args = parser.parse_args()

    handler_cls = make_handler(os.path.abspath(args.root) if args.root != "/" else "/")

    with ReusableThreadingTCPServer((args.host, args.port), handler_cls) as httpd:
        url = f"http://{args.host}:{args.port}/"
        print("=" * 60)
        print(f"  Prototype P3D 本地查看器已启动")
        print(f"  在浏览器打开: {url}")
        print(f"  按 Ctrl+C 停止服务 (关闭此窗口/终止此进程也会停止)")
        print("=" * 60)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")


if __name__ == "__main__":
    main()
