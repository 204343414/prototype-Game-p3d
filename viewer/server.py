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
import urllib.parse


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
