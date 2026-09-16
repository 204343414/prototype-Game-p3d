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
from functools import lru_cache
import http.server
import gzip
import json
import mimetypes
import os
import re
import socketserver
import struct
import subprocess
import sys
import threading
import urllib.parse
import zlib

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

# Content-free, package-wide skeleton/CompositeDrawable census.  It only
# returns names and structural counts, never vertex, texture, or animation
# payloads, which makes it suitable for building a user-owned asset ledger.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "inventory"))
try:
    import rigged_p3d  # noqa: E402
except ImportError:
    rigged_p3d = None

# Static-world probe used for Cell coordinate/vertex-layout census.  It
# returns bounds and header metadata only; no world geometry is sent through
# the endpoint.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "world"))
try:
    import probe_static_geometry  # noqa: E402
    import export_static_geometry_diagnostic as cell_geometry  # noqa: E402
    from cell_materials import attach_local_materials, build_texture_index  # noqa: E402
except ImportError:
    probe_static_geometry = None
    cell_geometry = None



CELL_PREVIEW_LOCK = threading.Lock()
MAX_CELL_BYTES = 64 * 1024 * 1024

class PreviewBudgetError(ValueError):
    pass


def _read_preview_entry(path, name):
    archive = rcf_extract.CementFile.load(path)
    name_hash = rcf_extract.hash_file_name(name)
    entry = next((e for e in archive.entries if e.name_hash == name_hash), None)
    if entry is None:
        raise FileNotFoundError(f"entry not found: {name}")
    if entry.size > MAX_CELL_BYTES:
        raise PreviewBudgetError("entry exceeds 64 MiB preview budget")
    with open(path, "rb") as stream:
        stream.seek(entry.offset)
        raw = stream.read(entry.size)
    if len(raw) != entry.size:
        raise ValueError("truncated archive entry")
    if raw[:4] == b"RZ\0\0" and len(raw) >= 16:
        declared_size = struct.unpack_from("<I", raw, 8)[0]
        if declared_size == 0:
            raise ValueError("empty RZ decoded size")
        if declared_size > MAX_CELL_BYTES:
            raise PreviewBudgetError("decoded entry exceeds 64 MiB preview budget")
    return rcf_extract.decompress_rz_payload(raw)


@lru_cache(maxsize=1)
def _shared_texture_index(path, mtime_ns, size):
    # Comprehensive process-only shared texture index from art.rcf.
    archive = rcf_extract.CementFile.load(path)
    combined = {}

    # Priority shared packages
    shared_packages = (
        r"\art\locations\manhattan\textures.p3d.rz",
        r"\art\billboards\billboards.p3d.rz",
        r"\art\locations\manhattan_mini\textures.p3d.rz",
        r"\art\locations\manhattan\props.p3d.rz",
    )

    entries_by_name = {}
    for entry in archive.entries:
        meta = archive.get_metadata(entry.name_hash)
        if meta and meta.name:
            entries_by_name[meta.name] = entry

    with open(path, "rb") as f:
        for pkg in shared_packages:
            entry = entries_by_name.get(pkg)
            if entry is not None:
                try:
                    f.seek(entry.offset)
                    raw = f.read(entry.size)
                    data = rcf_extract.decompress_rz_payload(raw) if raw.startswith(b"RZ") else raw
                    texs = build_texture_index(data)
                    for k, v in texs.items():
                        if v is not None and k not in combined:
                            combined[k] = v
                except Exception:
                    continue

        # Also index specialized Times Square / Broadway billboards from manhattan_mini cells
        for name, entry in entries_by_name.items():
            if name.startswith(r"\art\locations\manhattan_mini\manhattan_mini_Cell_") and name.endswith(".p3d.rz"):
                try:
                    f.seek(entry.offset)
                    raw = f.read(entry.size)
                    data = rcf_extract.decompress_rz_payload(raw) if raw.startswith(b"RZ") else raw
                    texs = build_texture_index(data)
                    for k, v in texs.items():
                        if v is not None and k not in combined:
                            combined[k] = v
                except Exception:
                    continue

    return combined


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# The git repo this server itself lives in -- used by /api/self_update to
# `git pull` and by /api/health to report the currently-running commit, so
# a remote collaborator can push a fix and confirm it's actually live
# without asking the human to run commands and paste output back.
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git_commit():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _origin_default_branch():
    """Return origin's advertised default branch without trusting local config.

    A previous implementation hard-coded ``master``. That makes the remote
    self-update endpoint fail as soon as a repository's default branch is
    renamed/deleted (as happened when this project's work was merged to
    ``main``). ``git ls-remote --symref origin HEAD`` is the remote-side
    source of truth and works even if local ``origin/HEAD`` is stale.
    """
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--symref", "origin", "HEAD"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                # Expected form: "ref: refs/heads/main\tHEAD"
                fields = line.split()
                if len(fields) == 3 and fields[0] == "ref:" and fields[2] == "HEAD":
                    ref = fields[1]
                    prefix = "refs/heads/"
                    if ref.startswith(prefix) and len(ref) > len(prefix):
                        return ref[len(prefix):]
    except Exception:  # noqa: BLE001
        pass

    # A local clone may already know the remote default even if the remote
    # cannot be queried temporarily. This fallback deliberately does not
    # guess "master" or "main".
    try:
        out = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
        )
        ref = out.stdout.strip()
        if out.returncode == 0 and ref.startswith("origin/") and len(ref) > len("origin/"):
            return ref[len("origin/"):]
    except Exception:  # noqa: BLE001
        pass
    return None



def _parse_type_filter(qs):
    """Parse an optional &type_filter=0x00123000,0x00123001 query param into
    a set of ints, or None if not provided. Used to let a caller pull out
    just the chunk types they care about (e.g. CompositeDrawable/Skeleton)
    from a huge real .p3d file (tens of thousands of chunks) without paging
    through the whole flat chunk list.
    """
    raw = qs.get("type_filter", [None])[0]
    if not raw:
        return None
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        out.add(int(part, 16) if part.lower().startswith("0x") else int(part, 16))
    return out


def _chunks_to_json(out, payload_preview, type_filter=None, offset=0, limit=None):
    """Shared post-processing for dump_chunk() output: turn the raw
    (depth, type_id, header_size, total_size, payload) tuples into the JSON
    shape used by both /api/p3d and /api/rcf_entry, with optional
    type-ID filtering and offset/limit paging (both needed for real game
    files, which can have tens of thousands of chunks).

    Each returned chunk includes "global_index": its position in the FULL
    (unfiltered) flat depth-first traversal. Since dump_chunk() emits a
    node immediately before its children (pre-order), a chunk's entire
    subtree is the contiguous run starting at its global_index and ending
    just before the next sibling/ancestor at <= its own depth. So to
    inspect one specific chunk's full subtree found via type_filter, issue
    a follow-up request with offset=<that chunk's global_index> and NO
    type_filter, then stop reading once depth drops back to <= the
    original chunk's depth.
    """
    indexed = list(enumerate(out))
    if type_filter is not None:
        indexed = [(i, t) for i, t in indexed if t[1] in type_filter]
    total_matching = len(indexed)
    if limit is not None:
        indexed = indexed[offset:offset + limit]
    else:
        indexed = indexed[offset:]
    chunks = []
    for global_index, (depth, type_id, hdr_size, tot_size, payload) in indexed:
        chunks.append({
            "global_index": global_index,
            "depth": depth,
            "type_id": f"0x{type_id:08X}",
            "header_size": hdr_size,
            "total_size": tot_size,
            "payload_len": len(payload),
            "payload_hex_preview": payload[:payload_preview].hex(),
        })
    return chunks, total_matching


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
        if parsed.path == "/api/rcf_rigged_manifest":
            return self._handle_rcf_rigged_manifest(qs)
        if parsed.path == "/api/rcf_cell_geometry_manifest":
            return self._handle_rcf_cell_geometry_manifest(qs)
        if parsed.path == "/api/rcf_entry":
            return self._handle_rcf_entry(qs)
        if parsed.path == "/api/rcf_cell_preview":
            return self._handle_cell_preview(qs)
        if parsed.path == "/api/health":
            return self._send_json({
                "ok": True,
                "root": self.root_dir,
                "git_commit": _git_commit(),
                "pid": os.getpid(),
            })

        # static file serving (frontend)
        return self._handle_static(parsed.path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/self_update":
            return self._handle_self_update()
        return self._send_error_json(f"not found: {parsed.path}", 404)

    # Special exit code used to signal "please relaunch me" to a wrapping
    # shell loop (run_viewer.sh), as opposed to a real exit (Ctrl+C, crash,
    # etc). Chosen to be outside the 0-2/126-165/SIGNAL-derived ranges a
    # process would normally exit with, to avoid ambiguity.
    SELF_UPDATE_EXIT_CODE = 78

    def _handle_self_update(self):
        """Pull the latest code for this server's own git repo, then exit
        this process with a special exit code (SELF_UPDATE_EXIT_CODE) that
        tells the wrapping shell loop in run_viewer.sh "relaunch me, this
        wasn't a real shutdown". This keeps the new server process inside
        run_viewer.sh's own process tree/session, so its `trap cleanup EXIT`
        (which tears down the Cloudflare/SSH tunnel) does NOT fire -- the
        tunnel keeps running, unaffected, pointed at the same port, so the
        public URL stays the same the whole time.

        (An earlier version of this spawned a detached "watcher" subprocess
        to relaunch the server itself, independently of run_viewer.sh. That
        orphaned the new server process from run_viewer.sh's process tree,
        so run_viewer.sh saw its `python3 server.py` child exit, considered
        itself done, and its EXIT trap killed the tunnel out from under the
        (still running!) new server. Delegating the relaunch decision to
        run_viewer.sh itself avoids that whole class of bug.)

        ⚠️ This intentionally lets anyone who has this tunnel's URL trigger
        `git pull` + a process restart in this repo -- same trust boundary
        already accepted for the read-only endpoints (no auth token on the
        tunnel by design, see run_viewer.sh). It does NOT run arbitrary
        shell commands from the request; it only ever runs a hardcoded
        `git pull` in REPO_DIR, then exits with a fixed special code.
        """
        before = _git_commit()
        try:
            branch_out = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
            )
            current_branch = branch_out.stdout.strip() or None
        except Exception:  # noqa: BLE001
            current_branch = None

        update_branch = _origin_default_branch()
        if not update_branch:
            return self._send_error_json(
                "could not determine origin's default branch; refusing to guess a branch for self-update",
                500,
            )

        def _do_pull():
            # Be explicit about the branch, but resolve it from the remote's
            # HEAD instead of relying on local branch.<name>.merge settings.
            # This supports repositories that use main, master, or another
            # default branch and still avoids a stale local upstream silently
            # reporting "already up to date".
            return subprocess.run(
                ["git", "pull", "origin", update_branch, "--ff-only"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=60,
            )

        def _remote_branch_sha():
            # Ground truth for the branch selected above, independent of local
            # fetch/tracking state. Cross-checking it detects a stale caching
            # mirror/proxy that has not yet observed a recent push.
            try:
                out = subprocess.run(
                    ["git", "ls-remote", "origin", f"refs/heads/{update_branch}"],
                    cwd=REPO_DIR, capture_output=True, text=True, timeout=15,
                )
                if out.returncode == 0 and out.stdout.strip():
                    return out.stdout.split()[0][:7]
            except Exception:  # noqa: BLE001
                pass
            return None

        try:
            result = _do_pull()
        except Exception as e:  # noqa: BLE001
            return self._send_error_json(f"git pull failed to run: {type(e).__name__}: {e}", 500)

        after = _git_commit()
        remote_sha = _remote_branch_sha()
        retried = False

        def _mismatch(local, remote):
            return bool(local and remote and not local.startswith(remote[:7]) and not remote.startswith(local[:7]))

        # If the pull "succeeded" but local HEAD still doesn't match what
        # selected remote default branch actually points at, this is very likely a stale
        # caching mirror/proxy -- wait a moment and retry once before
        # reporting a possibly-false "up to date".
        if result.returncode == 0 and _mismatch(after, remote_sha):
            import time
            time.sleep(2)
            try:
                result = _do_pull()
                after = _git_commit()
                remote_sha = _remote_branch_sha()
                retried = True
            except Exception:  # noqa: BLE001
                pass

        pull_ok = result.returncode == 0
        mirror_stale = _mismatch(after, remote_sha)

        response = {
            "pull_ok": pull_ok,
            "current_branch": current_branch,
            "retried_once": retried,
            "remote_branch": update_branch,
            "remote_branch_sha_short": remote_sha,
            "mirror_may_be_stale": mirror_stale,
            "git_stdout": result.stdout.strip(),
            "git_stderr": result.stderr.strip(),
            "commit_before": before,
            "commit_after": after,
            "changed": before != after,
            "will_restart": pull_ok,
        }
        self._send_json(response)

        if not pull_ok:
            return

        try:
            self.wfile.flush()
        except Exception:  # noqa: BLE001
            pass

        def _exit_soon():
            import time
            time.sleep(0.2)
            os._exit(Handler.SELF_UPDATE_EXIT_CODE)

        import threading
        threading.Thread(target=_exit_soon, daemon=True).start()

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
          type_filter - optional, comma-separated hex chunk type IDs (e.g.
                       "0x00123000,0x00123001") to only return matching
                       chunks -- essential for real game files that can
                       have tens of thousands of chunks total
          offset/limit - optional paging over the (possibly filtered) flat
                       chunk list (limit default 500, max 2000; limit=0
                       means "no limit", use with care on huge files)
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
            type_filter = _parse_type_filter(qs)
        except ValueError as e:
            return self._send_error_json(f"bad type_filter: {e}", 400)
        offset = int(qs.get("offset", ["0"])[0])
        limit_raw = int(qs.get("limit", ["500"])[0])
        limit = None if limit_raw == 0 else max(1, min(limit_raw, 2000))

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
            chunks, total_matching = _chunks_to_json(out, payload_preview, type_filter, offset, limit)

            self._send_json({
                "path": target,
                "file_size": len(data),
                "endian": "LE" if endian == "<" else "BE",
                "declared_total_size": total_size,
                "chunk_count": total_matching if type_filter is not None else len(out),
                "total_chunk_count_unfiltered": len(out),
                "chunks_shown": len(chunks),
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

    def _handle_rcf_rigged_manifest(self, qs):
        """Return a paged, metadata-only census of rigged P3D packages in an RCF.

        Unlike calling /api/rcf_entry once for every package, this opens the
        Cement archive once per page, then checks each selected .p3d.rz entry
        server-side.  The response never exposes geometry, texture pixels, or
        animation keys: it contains only entry metadata, CompositeDrawable and
        Skeleton names/counts, and PrimitiveGroup shader binding names.

        Query params:
          path                  required absolute RCF path
          offset                index in the stable, name-sorted P3D entry list
          limit                 packages to inspect this page (1..100, default 25)
          min_compressed_size   optional non-negative byte threshold, default 0
          include_nonrigged     1 to include structural summaries of P3Ds which
                                do not qualify as a rigged CompositeDrawable;
                                default only returns qualifying records.

        A qualifying record has a CompositeDrawable that names a skeleton and
        directly references at least one type=2 polyskin primitive.  The
        response separately records whether that named skeleton is present in
        the same package, rather than inventing cross-package linkage.
        """
        if rcf_extract is None:
            return self._send_error_json("rcf_extract module not available on server", 500)
        if rigged_p3d is None:
            return self._send_error_json("rigged_p3d module not available on server", 500)

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
            limit = int(qs.get("limit", ["25"])[0])
            min_size = int(qs.get("min_compressed_size", ["0"])[0])
        except ValueError:
            return self._send_error_json("offset, limit and min_compressed_size must be integers", 400)
        if offset < 0 or min_size < 0:
            return self._send_error_json("offset and min_compressed_size must be non-negative", 400)
        limit = max(1, min(limit, 100))
        include_nonrigged = qs.get("include_nonrigged", ["0"])[0] == "1"

        try:
            cement = rcf_extract.CementFile.load(target)
        except Exception as e:  # noqa: BLE001
            return self._send_error_json(f"RCF parse error: {type(e).__name__}: {e}", 500)

        eligible = []
        for entry in cement.entries:
            metadata = cement.get_metadata(entry.name_hash)
            name = metadata.name if metadata is not None else None
            if (name is not None and name.lower().endswith(".p3d.rz")
                    and entry.size >= min_size):
                eligible.append((name, entry))
        eligible.sort(key=lambda item: item[0].casefold())
        page = eligible[offset:offset + limit]

        records = []
        nonrigged_count = 0
        error_count = 0
        for name, entry in page:
            try:
                with open(target, "rb") as handle:
                    handle.seek(entry.offset)
                    compressed = handle.read(entry.size)
                data = rcf_extract.decompress_rz_payload(compressed)
                scan = rigged_p3d.scan_p3d_bytes(data)
            except Exception as e:  # noqa: BLE001 - preserve a per-package result
                error_count += 1
                if include_nonrigged:
                    records.append({
                        "entry_name": name,
                        "entry_name_hash": f"0x{entry.name_hash:08X}",
                        "entry_offset": entry.offset,
                        "entry_size_compressed": entry.size,
                        "scan_error": f"{type(e).__name__}: {e}",
                    })
                continue

            record = {
                "entry_name": name,
                "entry_name_hash": f"0x{entry.name_hash:08X}",
                "entry_offset": entry.offset,
                "entry_size_compressed": entry.size,
                "decompressed_size": scan["file_size"],
                "declared_total_size": scan["declared_total_size"],
                "chunk_count": scan["chunk_count"],
                "skeletons": scan["skeletons"],
                "rigged_composites": scan["rigged_composites"],
                "polyskin_count": scan["polyskin_count"],
                "shader_names": scan["shader_names"],
                "parse_warnings": scan["parse_warnings"],
            }
            if scan["rigged_composites"]:
                records.append(record)
            else:
                nonrigged_count += 1
                if include_nonrigged:
                    records.append(record)

        self._send_json({
            "path": target,
            "scope": "known-name .p3d.rz entries only; structural headers only; no asset payloads",
            "qualification": (
                "CompositeDrawable with named skeleton and at least one direct "
                "type=2 polyskin primitive"),
            "min_compressed_size": min_size,
            "eligible_entry_count": len(eligible),
            "entry_offset": offset,
            "requested_limit": limit,
            "scanned_entry_count": len(page),
            "next_entry_offset": offset + len(page),
            "complete": offset + len(page) >= len(eligible),
            "returned_record_count": len(records),
            "nonrigged_count_in_page": nonrigged_count,
            "scan_error_count_in_page": error_count,
            "records": records,
        })

    def _handle_rcf_cell_geometry_manifest(self, qs):
        """Census a page of numbered Manhattan base Cells without sending P3D data.

        This endpoint is deliberately specific to the confirmed ``cells.rcf``
        naming convention.  It emits only package metadata, counts, observed
        memory-image vertex strides, and aggregate POSITION bounds.  The
        ``_ft`` companion entries are excluded because they are a separately
        verified gameplay/meta-object layer, not initial static city geometry.

        Query params:
          path    required absolute cells.rcf path
          offset  index in numeric Cell order, default 0
          limit   Cells per page (1..25, default 10)
          detail  "summary" (default) or "full"; full includes per-Geometry
                  header/bounds metadata, never vertex or texture payloads.
        """
        if rcf_extract is None:
            return self._send_error_json("rcf_extract module not available on server", 500)
        if probe_static_geometry is None:
            return self._send_error_json("probe_static_geometry module not available on server", 500)

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
            limit = int(qs.get("limit", ["10"])[0])
        except ValueError:
            return self._send_error_json("offset and limit must be integers", 400)
        if offset < 0:
            return self._send_error_json("offset must be non-negative", 400)
        limit = max(1, min(limit, 25))
        detail = qs.get("detail", ["summary"])[0]
        if detail not in ("summary", "full"):
            return self._send_error_json("detail must be summary or full", 400)

        try:
            cement = rcf_extract.CementFile.load(target)
        except Exception as e:  # noqa: BLE001
            return self._send_error_json(f"RCF parse error: {type(e).__name__}: {e}", 500)

        cell_pattern = re.compile(r"^\\art\\locations\\manhattan\\manhattan_Cell_(\d+)\.p3d\.rz$", re.IGNORECASE)
        cells = []
        for entry in cement.entries:
            metadata = cement.get_metadata(entry.name_hash)
            name = metadata.name if metadata is not None else None
            match = cell_pattern.match(name or "")
            if match:
                cells.append((int(match.group(1)), name, entry))
        cells.sort(key=lambda item: item[0])
        page = cells[offset:offset + limit]
        records = []
        for cell_index, name, entry in page:
            basic = {
                "cell_index": cell_index,
                "entry_name": name,
                "entry_name_hash": f"0x{entry.name_hash:08X}",
                "entry_offset": entry.offset,
                "entry_size_compressed": entry.size,
            }
            # The known 33-byte entries carry no useful P3D geometry payload.
            # Report them explicitly instead of treating sparse world space as
            # an extractor error or transferring a pointless decompression.
            if entry.size == 33:
                basic["status"] = "placeholder"
                records.append(basic)
                continue
            try:
                with open(target, "rb") as handle:
                    handle.seek(entry.offset)
                    compressed = handle.read(entry.size)
                data = rcf_extract.decompress_rz_payload(compressed)
                scan = probe_static_geometry.scan_static_geometry(data)
                basic.update({
                    "status": "scanned",
                    "decompressed_size": scan["decompressed_size"],
                    "geometry_count": scan["geometry_count"],
                    "position_group_count": scan["position_group_count"],
                    "vertex_stride_counts": scan["vertex_stride_counts"],
                    "vertex_description_fingerprints": scan["vertex_description_fingerprints"],
                    "world_position_bounds_status": scan["world_position_bounds_status"],
                    "world_position_min": scan["world_position_min"],
                    "world_position_max": scan["world_position_max"],
                    "merged_world_geometry_count": scan["merged_world_geometry_count"],
                    "merged_world_position_group_count": scan["merged_world_position_group_count"],
                    "merged_world_position_bounds_status": scan["merged_world_position_bounds_status"],
                    "merged_world_position_min": scan["merged_world_position_min"],
                    "merged_world_position_max": scan["merged_world_position_max"],
                    "warning_count": len(scan["warnings"]),
                })
                if detail == "full":
                    basic["geometries"] = scan["geometries"]
                    basic["warnings"] = scan["warnings"]
            except Exception as e:  # noqa: BLE001 - preserve a failing Cell's identity
                basic.update({"status": "scan_error", "scan_error": f"{type(e).__name__}: {e}"})
            records.append(basic)

        self._send_json({
            "path": target,
            "scope": (
                "numbered Manhattan base Cells only; structural headers and POSITION bounds; "
                "no P3D geometry/texture payloads; _ft companion entries excluded"),
            "eligible_cell_count": len(cells),
            "entry_offset": offset,
            "requested_limit": limit,
            "scanned_cell_count": len(page),
            "next_entry_offset": offset + len(page),
            "complete": offset + len(page) >= len(cells),
            "detail": detail,
            "records": records,
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
          type_filter / offset / limit - same meaning as in /api/p3d
                   (essential for the big real character .p3d files,
                   which can have tens of thousands of chunks total)
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
        # /api/rcf_entry is also used to pull full raw data buffers (e.g.
        # complete vertex/index streams, tens to hundreds of KB) out of a
        # single chunk for offline analysis, so allow a much larger preview
        # cap here than the generic /api/p3d endpoint's 256-byte cap.
        payload_preview = max(0, min(payload_preview, 8 * 1024 * 1024))
        try:
            type_filter = _parse_type_filter(qs)
        except ValueError as e:
            return self._send_error_json(f"bad type_filter: {e}", 400)
        offset_p = int(qs.get("offset", ["0"])[0])
        limit_raw = int(qs.get("limit", ["500"])[0])
        limit = None if limit_raw == 0 else max(1, min(limit_raw, 2000))

        try:
            out = []
            inspect_p3d.dump_chunk(data, 12, total_size, endian, 0, out, max_depth)
            chunks, total_matching = _chunks_to_json(out, payload_preview, type_filter, offset_p, limit)
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
            "chunk_count": total_matching if type_filter is not None else len(out),
            "total_chunk_count_unfiltered": len(out),
            "chunks_shown": len(chunks),
            "chunks": chunks,
        })

    def _handle_cell_preview(self, qs):
        """Return strict world-core triangles and optional verified local color textures.

        `path` is an RCF inside --root, `cell` is 0..259. Payload buffers are
        base64 little-endian float32 POSITION / uint16 index streams. Optional
        materials=1 includes bounded original DXT mip blocks and known UVs. No files
        are extracted. Unsupported groups fail the whole preview, not silently
        disappear from a supposedly complete scene.
        """
        if cell_geometry is None or rcf_extract is None:
            return self._send_error_json("Cell geometry decoder unavailable", 503)
        path = qs.get("path", [None])[0]
        cell = qs.get("cell", [""])[0]
        shared_path = qs.get("shared_path", [""])[0]
        materials = qs.get("materials", ["0"])[0]
        if materials not in ("0", "1"):
            return self._send_error_json("materials must be 0 or 1", 400)
        if not path or not re.fullmatch(r"[0-9]{1,3}", cell) or int(cell) > 259:
            return self._send_error_json("requires path and integer cell in 0..259", 400)
        try:
            target = os.path.realpath(self._safe_resolve(path))
            root = os.path.realpath(self.root_dir)
            if os.path.commonpath([root, target]) != root:
                raise PermissionError("path escapes configured root directory")
            if shared_path:
                shared_path = os.path.realpath(self._safe_resolve(shared_path))
                if os.path.commonpath([root, shared_path]) != root:
                    raise PermissionError("shared path escapes configured root directory")
        except PermissionError as exc:
            return self._send_error_json(str(exc), 403)
        if not os.path.isfile(target):
            return self._send_error_json("archive not found", 404)
        if not CELL_PREVIEW_LOCK.acquire(blocking=False):
            return self._send_error_json("another Cell is decoding; retry shortly", 429)
        try:
            name = f"\\art\\locations\\manhattan\\manhattan_Cell_{int(cell)}.p3d.rz"
            data = _read_preview_entry(target, name)
            groups, report = cell_geometry.scan_core_triangle_geometry(data, name)
            if report["errors"]:
                return self._send_json({"error": "unsupported or invalid core geometry", "report": report}, 422)
            result = cell_geometry._preview_data(groups)
            result.update(cell=int(cell), status="ready" if groups else "empty", textured=False, report=report)
            if materials == "1" and groups:
                shared = None
                shared_error = None
                if shared_path:
                    try:
                        stat = os.stat(shared_path)
                        shared = _shared_texture_index(shared_path, stat.st_mtime_ns, stat.st_size)
                    except (ValueError, OSError, struct.error, zlib.error) as exc:
                        shared_error = str(exc)
                textures, material_report = attach_local_materials(data, groups, result["meshes"], shared)
                material_report["shared_error"] = shared_error
                result.update(textures=textures, materials=material_report, textured=bool(textures))
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            use_gzip = bool(re.search(r"(?:^|,)\s*gzip\s*(?:,|$)", self.headers.get("Accept-Encoding", "")))
            if use_gzip:
                body = gzip.compress(body, compresslevel=1)
            self.send_response(200)
            self.send_header("Vary", "Accept-Encoding")
            if use_gzip:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except PreviewBudgetError as exc:
            return self._send_error_json(str(exc), 413)
        except FileNotFoundError as exc:
            return self._send_error_json(str(exc), 404)
        except (ValueError, OSError, struct.error, zlib.error) as exc:
            return self._send_error_json(f"Cell preview: {exc}", 422)
        finally:
            CELL_PREVIEW_LOCK.release()

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
