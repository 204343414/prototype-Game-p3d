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
import hashlib
import concurrent.futures
import json
import mimetypes
import os
import re
import shutil
import socketserver
import struct
import subprocess
import sys
import threading
import urllib.parse
import uuid
import zipfile
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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "entities"))
try:
    import probe_static_geometry  # noqa: E402
    import export_static_geometry_diagnostic as cell_geometry  # noqa: E402
    from cell_materials import attach_local_materials, build_texture_index  # noqa: E402
    from entity_catalog import build_entity_catalog  # noqa: E402
    from entity_decoder import decode_entity_meshes, extract_entity_skeletons  # noqa: E402
except ImportError:
    probe_static_geometry = None
    cell_geometry = None
    build_entity_catalog = None
    decode_entity_meshes = None
    extract_entity_skeletons = None


# These package-to-package donors are not a name heuristic: each entry below
# was traced through real Composite_Drawable_2 -> Skin -> skeleton_name bytes.
# A donor is used only to provide a missing *exact* skeleton name; it never
# replaces a locally declared skeleton and no archive-wide guessing is done.
ENTITY_SKELETON_DONORS = {
    # P3D-resident skin names were matched exactly to Skeleton_2 headers in
    # the listed donor entry.  Add a new row only after the same raw-byte
    # audit; filenames and joint counts are never considered a match.
    r"\art\packages\powers\alex_armour\alex_armour.p3d.rz": (
        r"\art\alex\alex.p3d.rz",
    ),
    r"\art\packages\missions\soldier\soldier.p3d.rz": (
        r"\art\startup.p3d.rz",
    ),
    r"\art\packages\missions\supersoldier\supersoldier.p3d.rz": (
        r"\art\startup.p3d.rz",
    ),
    r"\art\packages\missions\supersoldiere10m4\supersoldiere10m4.p3d.rz": (
        r"\art\startup.p3d.rz",
    ),
    r"\art\packages\missions\leaderhunter\leaderhunter.p3d.rz": (
        r"\art\startup.p3d.rz",
    ),
    # Follow-up closure audit: exact Skin headers below name a skeleton which
    # this package does not declare.  Each donor name was located byte-for-byte
    # in startup/alex before adding the row; no filename or joint-count match.
    r"\art\packages\powers\soldier_disguise\soldier_disguise.p3d.rz": (
        r"\art\startup.p3d.rz",
    ),
    r"\art\packages\powers\alex_shield\alex_shield.p3d.rz": (
        r"\art\alex\alex.p3d.rz",
    ),
    r"\art\packages\pedestrians\shared.p3d.rz": (
        r"\art\startup.p3d.rz",
    ),
    r"\art\packages\missions\brawler\brawler.p3d.rz": (
        r"\art\startup.p3d.rz",
    ),
    r"\art\packages\missions\mission1\mission1.p3d.rz": (
        r"\art\startup.p3d.rz",
    ),
    r"\art\packages\missions\permanentcharacterspackage\permanentcharacterspackage.p3d.rz": (
        r"\art\startup.p3d.rz",
    ),
}


def _canonical_entry_name(name):
    return name.replace("/", "\\").lower()


@lru_cache(maxsize=8)
def _explicit_entity_skeleton_donors(archive_path, archive_mtime_ns, archive_size, entry_name):
    """Load only the explicitly audited cross-package skeleton declarations."""
    del archive_mtime_ns, archive_size  # cache invalidation inputs, not payload fields
    donor_entries = ENTITY_SKELETON_DONORS.get(_canonical_entry_name(entry_name), ())
    skeletons = []
    for donor_entry in donor_entries:
        donor_data = _read_preview_entry(archive_path, donor_entry)
        skeletons.extend(extract_entity_skeletons(donor_data, source_entry=donor_entry))
    return skeletons


CELL_PREVIEW_LOCK = threading.Lock()
EXPORT_LOCK = threading.Lock()
EXPORT_JOBS_LOCK = threading.Lock()
EXPORT_JOBS: dict[str, dict] = {}
EXPORT_ROOT = os.path.realpath(os.environ.get("PROTOTYPE_EXPORT_ROOT", "/mnt/hdd/PrototypeExports"))
BLENDER_BIN = os.environ.get("PROTOTYPE_BLENDER", shutil.which("blender") or os.path.join(EXPORT_ROOT, "tools", "blender-4.5.3-linux-x64", "blender"))
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
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "entities"))
    import fig_timeline
    import actor_state_runtime
    from alex_controller_runtime import AlexController
except Exception:
    fig_timeline = None
    actor_state_runtime = None
    AlexController = None

ALEX_SIM_LOCK = threading.RLock()
ALEX_SIMULATORS = {}

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
        if parsed.path == "/api/entities":
            return self._handle_entities(qs)
        if parsed.path == "/api/entity_mesh":
            return self._handle_entity_mesh(qs)
        if parsed.path == "/api/move_timeline":
            return self._handle_move_timeline(qs)
        if parsed.path == "/api/audio_event":
            return self._handle_audio_event(qs)
        if parsed.path == "/api/fx_texture":
            return self._handle_fx_texture(qs)
        if parsed.path == "/api/audio_banks":
            return self._handle_audio_banks(qs)
        if parsed.path == "/api/audio_bank":
            return self._handle_audio_bank(qs)
        if parsed.path == "/api/input_moves":
            return self._handle_input_moves(qs)
        if parsed.path == "/api/condition_vocab":
            return self._handle_condition_vocab(qs)
        if parsed.path == "/api/move_graph":
            return self._handle_move_graph(qs)
        if parsed.path == "/api/player_actor_manifest":
            return self._handle_player_actor_manifest(qs)
        if parsed.path == "/api/state_owner_ir":
            return self._handle_state_owner_ir(qs)
        if parsed.path == "/api/alex_simulator":
            return self._handle_alex_simulator_get(qs)
        if parsed.path == "/api/export_asset":
            return self._handle_export_asset(qs)
        if parsed.path == "/api/export_loaded_cells":
            return self._handle_export_loaded_cells(qs)
        if parsed.path == "/api/export_entity":
            return self._handle_export_entity(qs)
        if parsed.path == "/api/export_job":
            return self._handle_export_job(qs)
        if parsed.path == "/api/export_entities_batch_download":
            return self._handle_export_entities_batch_download(qs)
        if parsed.path == "/api/export_directories":
            return self._handle_export_directories(qs)
        if parsed.path == "/api/health":
            return self._send_json({
                "ok": True,
                "root": self.root_dir,
                "git_commit": _git_commit(),
                "pid": os.getpid(),
                "export_root": EXPORT_ROOT,
                "blender": BLENDER_BIN,
                "blender_available": os.path.isfile(BLENDER_BIN),
            })

        # static file serving (frontend)
        return self._handle_static(parsed.path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/self_update":
            return self._handle_self_update()
        if parsed.path == "/api/alex_simulator":
            return self._handle_alex_simulator_post()
        if parsed.path == "/api/export_entities_batch":
            return self._handle_export_entities_batch()
        if parsed.path == "/api/export_directory":
            return self._handle_create_export_directory()
        return self._send_error_json(f"not found: {parsed.path}", 404)

    def _alex_simulator(self, session="default", reset=False):
        if AlexController is None:
            raise RuntimeError("Alex simulator runtime unavailable")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", session):
            raise ValueError("invalid session")
        with ALEX_SIM_LOCK:
            if reset or session not in ALEX_SIMULATORS:
                manifest = os.path.join(REPO_DIR, "audit", "player-actor-manifest-alex.json")
                ALEX_SIMULATORS[session] = AlexController(self.root_dir, manifest)
            return ALEX_SIMULATORS[session]

    def _handle_alex_simulator_get(self, qs):
        try:
            session = qs.get("session", ["default"])[0]
            sim = self._alex_simulator(session)
            with ALEX_SIM_LOCK:
                return self._send_json(sim.snapshot())
        except (ValueError, RuntimeError, KeyError, OSError) as exc:
            return self._send_error_json(str(exc), 422)

    def _handle_alex_simulator_post(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 65536:
                return self._send_error_json("request body too large", 413)
            payload = json.loads(self.rfile.read(length) or b"{}")
            session = str(payload.get("session", "default"))
            action = payload.get("action", "snapshot")
            with ALEX_SIM_LOCK:
                sim = self._alex_simulator(session, action == "reset")
                if action == "switch_power": sim.switch_power(str(payload["name"]))
                elif action == "attack_down": sim.attack_down(str(payload.get("button", "Attack")))
                elif action == "attack_up": sim.attack_up(str(payload.get("button", "Attack")))
                elif action == "action_e": sim.action_e(bool(payload.get("down", True)))
                elif action == "jump_down": sim.jump_down()
                elif action == "jump_up": sim.jump_up()
                elif action == "move": sim.set_move_keys(set(payload.get("keys", [])))
                elif action == "world_context": sim.set_world_context(**dict(payload.get("context", {})))
                elif action == "tick": sim.tick(max(0.0, min(float(payload.get("dt", 0)), 0.25)))
                elif action not in ("snapshot", "reset"): raise ValueError("unknown action")
                return self._send_json(sim.snapshot())
        except (ValueError, RuntimeError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
            return self._send_error_json(str(exc), 422)

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

    def _handle_move_timeline(self, qs):
        """FIG move timeline for one animation name (byte-verified grammar).

        Returns every FIG tracks-group that references the animation, with its
        raw stage frame window and the raw event times (sound/spawn/execute/
        hit/cameraShake/motionState). Nothing is interpolated or invented;
        see tools/entities/fig_timeline.py for the field provenance.
        """
        if fig_timeline is None:
            return self._send_error_json("fig_timeline module unavailable", 503)
        anim = qs.get("anim", [None])[0]
        if not anim:
            return self._send_error_json("missing anim parameter", 400)
        try:
            data = fig_timeline.build_timeline(self.root_dir, anim)
            return self._send_json(data)
        except Exception as exc:
            return self._send_error_json(f"move_timeline: {exc}", 500)

    def _handle_audio_event(self, qs):
        """Decode one audio event (FIG sound reference = Patch name, or a raw
        AudioFile name) from the Alex audio banks to a mono PCM16 WAV.

        RADP codec layout matches vgmstream decode_rad_ima_mono; the Patch ->
        files edge uses the exact u32(5)+files\x00+count list already audited.
        variant selects among the Patch file list (default 0).
        """
        if fig_timeline is None:
            return self._send_error_json("fig_timeline module unavailable", 503)
        event = qs.get("event", [None])[0]
        if not event:
            return self._send_error_json("missing event parameter", 400)
        try:
            variant = int(qs.get("variant", ["0"])[0])
        except ValueError:
            variant = 0
        bank_hint = qs.get("bank", [None])[0]
        try:
            if bank_hint:
                fig_timeline._bank_data(self.root_dir, bank_hint)
            got = fig_timeline.decode_audio_event(self.root_dir, event, variant)
        except Exception as exc:
            return self._send_error_json(f"audio_event: {exc}", 500)
        if got is None:
            return self._send_error_json(f"audio event not found: {event}", 404)
        rate, pcm, source = got
        import io
        import wave as _wave
        buf = io.BytesIO()
        with _wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        body = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Audio-Source", source)
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(body)
        return None

    def _handle_fx_texture(self, qs):
        """Serve a motion-trail definition texture as PNG.

        name = trail definition object (e.g. motionTrail007). The chain
        0x11015 shader -> texture name -> 0x19000 embedded DDS lives in
        startup_effects.p3d and is decoded (DXT1/DXT5, top mip) server-side.
        Response headers carry the blend template and texture provenance.
        """
        if fig_timeline is None:
            return self._send_error_json("fig_timeline module unavailable", 503)
        name = qs.get("name", [None])[0]
        if not name:
            return self._send_error_json("missing name parameter", 400)
        try:
            got = fig_timeline.get_fx_texture_png(self.root_dir, name)
        except Exception as exc:
            return self._send_error_json(f"fx_texture: {exc}", 500)
        if got is None:
            return self._send_error_json(f"trail definition not found: {name}", 404)
        png, meta = got
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.send_header("X-Fx-Template", str(meta.get("template")))
        self.send_header("X-Fx-Texture", str(meta.get("texture")))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(png)
        return None

    def _handle_audio_banks(self, qs):
        """List top-level sfx banks in 00audio.rcf (dialogue summarised)."""
        if fig_timeline is None:
            return self._send_error_json("fig_timeline module unavailable", 503)
        try:
            return self._send_json(fig_timeline.list_audio_banks(self.root_dir))
        except Exception as exc:
            return self._send_error_json(f"audio_banks: {exc}", 500)

    def _handle_audio_bank(self, qs):
        """Patch groups + loose AudioFiles of one bank, with RADP durations."""
        if fig_timeline is None:
            return self._send_error_json("fig_timeline module unavailable", 503)
        bank = qs.get("bank", [None])[0]
        if not bank:
            return self._send_error_json("missing bank parameter", 400)
        try:
            return self._send_json(fig_timeline.list_bank_contents(self.root_dir, bank))
        except Exception as exc:
            return self._send_error_json(f"audio_bank: {exc}", 500)

    def _handle_input_moves(self, qs):
        """Input-condition move list of one FIG block (mouse simulation)."""
        if fig_timeline is None:
            return self._send_error_json("fig_timeline module unavailable", 503)
        block = qs.get("block", [None])[0]
        if not block:
            return self._send_error_json("missing block parameter", 400)
        try:
            return self._send_json(fig_timeline.build_input_moves(self.root_dir, block))
        except Exception as exc:
            return self._send_error_json(f"input_moves: {exc}", 500)

    def _handle_condition_vocab(self, qs):
        """Full census of FIG condition kinds per block (foundation audit)."""
        if fig_timeline is None:
            return self._send_error_json("fig_timeline module unavailable", 503)
        try:
            return self._send_json(fig_timeline.condition_vocab(self.root_dir))
        except Exception as exc:
            return self._send_error_json(f"condition_vocab: {exc}", 500)

    def _handle_state_owner_ir(self, qs):
        """Ownership-first FIG subtree for simulator archaeology; not executable."""
        if actor_state_runtime is None:
            return self._send_error_json("actor_state_runtime module unavailable", 503)
        block = qs.get("block", [None])[0]
        owner = qs.get("owner", [None])[0]
        if not block or owner is None:
            return self._send_error_json("missing block/owner parameter", 400)
        try:
            return self._send_json(actor_state_runtime.build_owner_ir(self.root_dir, block, int(owner)))
        except Exception as exc:
            return self._send_error_json(f"state_owner_ir: {exc}", 500)

    def _handle_player_actor_manifest(self, qs):
        """Canonical state-first actor manifest for tooling/Unity handoff."""
        actor = qs.get("actor", ["alex"])[0].lower()
        if actor != "alex":
            return self._send_error_json("only the audited alex manifest exists", 404)
        path = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "audit", "player-actor-manifest-alex.json"))
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return self._send_json(json.load(handle))
        except Exception as exc:
            return self._send_error_json(f"player_actor_manifest: {exc}", 500)

    def _handle_move_graph(self, qs):
        """Stage graph (frame windows + conditions + events) of one move bank."""
        if fig_timeline is None:
            return self._send_error_json("fig_timeline module unavailable", 503)
        block = qs.get("block", [None])[0]
        bank = qs.get("bank", [None])[0]
        if not block or bank is None:
            return self._send_error_json("missing block/bank parameter", 400)
        try:
            data = fig_timeline.build_move_graph(self.root_dir, block, int(bank))
            return self._send_json(data)
        except Exception as exc:
            return self._send_error_json(f"move_graph: {exc}", 500)

    def _handle_entities(self, qs):
        """Return the 4-category shelf of all entities in art.rcf."""
        if build_entity_catalog is None:
            return self._send_error_json("entity_catalog module unavailable", 503)
        art_path = qs.get("path", [None])[0] or os.path.join(self.root_dir, "art.rcf")
        try:
            target = os.path.realpath(self._safe_resolve(art_path))
            if not os.path.isfile(target):
                return self._send_error_json(f"art.rcf not found at {target}", 404)
            catalog = build_entity_catalog(target)
            return self._send_json(catalog)
        except Exception as exc:
            return self._send_error_json(f"Entity catalog: {exc}", 500)

    def _handle_entity_mesh(self, qs):
        """Return 3D geometry, UVs, textures and skeleton for a specific entity."""
        if decode_entity_meshes is None or extract_entity_skeletons is None:
            return self._send_error_json("entity_decoder module unavailable", 503)
        art_path = qs.get("path", [None])[0] or os.path.join(self.root_dir, "art.rcf")
        entry = qs.get("entry", [None])[0]
        shape = qs.get("shape", [None])[0]
        if not entry:
            return self._send_error_json("missing ?entry=", 400)
        try:
            target = os.path.realpath(self._safe_resolve(art_path))
            stat = os.stat(target)
            data = _read_preview_entry(target, entry)
            external_skeletons = _explicit_entity_skeleton_donors(
                target, stat.st_mtime_ns, stat.st_size, entry)
            result = decode_entity_meshes(
                data, shape_filter=shape, external_skeletons=external_skeletons)
            return self._send_json(result)
        except Exception as exc:
            return self._send_error_json(f"Entity mesh: {exc}", 500)

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

    def _send_attachment(self, path, download_name, content_type="application/octet-stream"):
        if not os.path.isfile(path):
            return self._send_error_json("导出文件尚未生成", 404)
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _handle_export_asset(self, qs):
        name = qs.get("name", [""])[0]
        base = EXPORT_ROOT
        assets = {
            "alex-fbx": (f"{base}/gltf/alex/alex-FBX.zip", "prototype-alex-FBX.zip", "application/zip"),
            "alex-report": (f"{base}/gltf/alex/export-report.json", "prototype-alex-export-report.json", "application/json"),
            "audio-summary-json": (f"{base}/audio_wav/summary.json", "prototype-audio-summary.json", "application/json"),
            "audio-summary-csv": (f"{base}/audio_wav/summary.csv", "prototype-audio-summary.csv", "text/csv"),
            "audio-manifest": (f"{base}/audio_wav/manifest.jsonl", "prototype-audio-manifest.jsonl", "application/x-ndjson"),
        }
        item = assets.get(name)
        if item is None:
            return self._send_error_json("未知导出资源", 404)
        return self._send_attachment(*item)

    def _resolve_export_dir(self, requested):
        root = EXPORT_ROOT
        candidate = os.path.realpath(requested if os.path.isabs(requested) else os.path.join(root, requested or "."))
        if os.path.commonpath([root, candidate]) != root:
            raise PermissionError(f"导出路径必须位于 {EXPORT_ROOT} 内")
        return candidate

    def _safe_export_dir(self, requested):
        candidate = self._resolve_export_dir(requested)
        os.makedirs(candidate, exist_ok=True)
        return candidate

    def _handle_export_directories(self, qs):
        try:
            candidate = self._resolve_export_dir(qs.get("path", ["."])[0])
            if not os.path.isdir(candidate):
                return self._send_error_json("所选导出目录不存在", 404)
            directories = []
            for item in os.scandir(candidate):
                if item.name.startswith(".") or not item.is_dir(follow_symlinks=False):
                    continue
                resolved = os.path.realpath(item.path)
                if os.path.commonpath([EXPORT_ROOT, resolved]) == EXPORT_ROOT:
                    directories.append(item.name)
            directories.sort(key=str.casefold)
            relative = os.path.relpath(candidate, EXPORT_ROOT)
            if relative == ".":
                relative = ""
            parent = os.path.dirname(relative) if relative else None
            return self._send_json({
                "root": EXPORT_ROOT,
                "current": relative,
                "parent": parent,
                "directories": directories,
            })
        except (OSError, ValueError, PermissionError) as exc:
            return self._send_error_json(str(exc), 400)

    def _handle_create_export_directory(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                return self._send_error_json("新建文件夹请求为空或过大", 400)
            payload = json.loads(self.rfile.read(length))
            parent = self._resolve_export_dir(str(payload.get("parent", ".")))
            name = str(payload.get("name", "")).strip()
            if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
                return self._send_error_json("文件夹名称无效", 400)
            target = self._resolve_export_dir(os.path.join(parent, name))
            os.makedirs(target, exist_ok=False)
            relative = os.path.relpath(target, EXPORT_ROOT)
            return self._send_json({"ok": True, "path": relative}, 201)
        except FileExistsError:
            return self._send_error_json("同名文件夹已存在", 409)
        except (OSError, ValueError, PermissionError, json.JSONDecodeError) as exc:
            return self._send_error_json(str(exc), 400)

    def _handle_export_job(self, qs):
        job_id = qs.get("id", [""])[0]
        with EXPORT_JOBS_LOCK:
            job = EXPORT_JOBS.get(job_id)
            if job is None and re.fullmatch(r"[0-9a-f]{8,32}", job_id or ""):
                # 服务重启后内存任务表会丢，但已完成的 ZIP/manifest 按固定规则仍在磁盘上；
                # 用磁盘痕迹重建一份只读快照，避免前端轮询旧任务报 404。
                for filename, kind in ((f"entity-batch-{job_id}.zip", "entities"),):
                    package = os.path.join(EXPORT_ROOT, "gltf", filename)
                    if os.path.isfile(package):
                        manifest_dir = os.path.join(EXPORT_ROOT, "gltf", f"entity-batch-{job_id}")
                        manifest_path = os.path.join(manifest_dir, "batch-export-manifest.json")
                        results, errors = [], []
                        if os.path.isfile(manifest_path):
                            try:
                                with open(manifest_path, "r", encoding="utf-8") as mh:
                                    manifest_doc = json.load(mh)
                                results = manifest_doc.get("results", [])
                                errors = manifest_doc.get("errors", [])
                            except Exception:
                                results, errors = [], []
                        job = {
                            "id": job_id,
                            "type": kind,
                            "status": "completed" if not errors else "completed_with_errors",
                            "total": len(results) + len(errors),
                            "completed": len(results) + len(errors),
                            "current": None,
                            "results": results,
                            "errors": errors,
                            "output_dir": manifest_dir,
                            "package": package,
                            "download_url": f"/api/export_entities_batch_download?job_id={job_id}",
                            "recovered_from_disk": True,
                        }
                        break
            if job is None:
                return self._send_error_json("导出任务不存在", 404)
            snapshot = json.loads(json.dumps(job, ensure_ascii=False))
        return self._send_json(snapshot)

    def _handle_export_entities_batch_download(self, qs):
        job_id = qs.get("job_id", [""])[0]
        with EXPORT_JOBS_LOCK:
            job = EXPORT_JOBS.get(job_id)
        package = None
        if job is not None:
            if job.get("status") not in ("completed", "completed_with_errors"):
                return self._send_error_json("导出任务尚未完成", 409)
            package = job.get("package")
        else:
            # 服务重启后内存任务表会丢，但已完成的 ZIP 仍按固定规则存放在 EXPORT_ROOT
            if not re.fullmatch(r"[0-9a-f]{8,32}", job_id or ""):
                return self._send_error_json("导出任务不存在", 404)
            fallback = os.path.join(EXPORT_ROOT, "gltf", f"entity-batch-{job_id}.zip")
            if os.path.isfile(fallback):
                package = fallback
        if not package or not os.path.isfile(package):
            return self._send_error_json("压缩包文件未找到", 404)
        return self._send_attachment(package, "prototype-entities-batch.zip", "application/zip")

    def _handle_export_entities_batch(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1024 * 1024:
                return self._send_error_json("批量导出请求为空或过大", 400)
            payload = json.loads(self.rfile.read(length))
            entries = payload.get("entries")
            requested_dir = payload.get("output_dir", "")
            package_zip = payload.get("package", "") == "zip"
            overwrite = bool(payload.get("overwrite", False))
            if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
                return self._send_error_json("entries 必须包含 1..1000 个实体", 400)
            if not requested_dir and not package_zip:
                return self._send_error_json("批量导出必须指定 output_dir", 400)
            job_id = uuid.uuid4().hex[:16]
            if package_zip:
                output_dir = os.path.join(EXPORT_ROOT, "gltf", f"entity-batch-{job_id}")
            else:
                output_dir = self._safe_export_dir(requested_dir)
            clean_entries = []
            for item in entries:
                if not isinstance(item, dict):
                    raise ValueError("实体记录必须是对象")
                entry = item.get("entry", "")
                name = item.get("name", "")
                if not entry.startswith("\\") or len(entry) > 512:
                    raise ValueError(f"非法实体 entry: {entry!r}")
                clean_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or hashlib.sha256(entry.encode()).hexdigest()[:12]
                clean_entries.append({"entry": entry, "name": clean_name})
        except (ValueError, TypeError, json.JSONDecodeError, PermissionError) as exc:
            return self._send_error_json(str(exc), 400)
        if not os.path.isfile(BLENDER_BIN):
            return self._send_error_json(f"找不到 Blender：{BLENDER_BIN}；请设置 PROTOTYPE_BLENDER", 503)
        if not EXPORT_LOCK.acquire(blocking=False):
            return self._send_error_json("另一个导出任务正在运行", 429)
        job = {"id": job_id, "type": "entities", "status": "queued", "output_dir": output_dir,
               "total": len(clean_entries), "completed": 0, "current": None, "results": [], "errors": [],
               "package": None, "download_url": None}
        with EXPORT_JOBS_LOCK:
            EXPORT_JOBS[job_id] = job
        port = self.server.server_address[1]
        repo = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
        max_workers = max(2, min(4, (os.cpu_count() or 2) - 1))
        staging_root = os.path.join(EXPORT_ROOT, ".batch-staging", job_id)

        class ItemSkipped(Exception):
            pass

        def export_one(index, item):
            name, entry = item["name"], item["entry"]
            with EXPORT_JOBS_LOCK: job["current"] = name
            digest = hashlib.sha256(entry.encode("utf-8")).hexdigest()[:10]
            target = os.path.join(output_dir, f"{name}-{digest}")
            try:
                if os.path.exists(target) and not overwrite:
                    raise ItemSkipped("目标已存在；未启用覆盖")
                stage = os.path.join(staging_root, digest)
                shutil.rmtree(stage, ignore_errors=True); os.makedirs(stage, exist_ok=True)
                exporter = os.path.join(repo, "tools", "entities", "export_entity_gltf.py")
                converter = os.path.join(repo, "tools", "entities", "convert_glb_to_fbx.py")
                blender = BLENDER_BIN
                gltf_run = subprocess.run([sys.executable, exporter, "--server", f"http://127.0.0.1:{port}",
                                           "--entry", entry, "--output", stage, "--name", name],
                                          capture_output=True, text=True, timeout=1800)
                if gltf_run.returncode:
                    raise RuntimeError((gltf_run.stderr or gltf_run.stdout)[-2000:])
                glb, fbx = os.path.join(stage, name + ".glb"), os.path.join(stage, name + ".fbx")
                fbx_env = dict(os.environ, PROTOTYPE_FBX_MIRROR_LR="1")
                fbx_run = subprocess.run([blender, "--background", "--python", converter, "--", glb, fbx],
                                         capture_output=True, text=True, timeout=1800, env=fbx_env)
                if fbx_run.returncode or not os.path.isfile(fbx):
                    raise RuntimeError((fbx_run.stderr or fbx_run.stdout)[-4000:])
                shutil.rmtree(target, ignore_errors=True); os.makedirs(target, exist_ok=True)
                shutil.copy2(fbx, os.path.join(target, name + ".fbx"))
                conversion = json.load(open(fbx + ".conversion.json", encoding="utf-8"))
                for texture_name in conversion.get("textures", []):
                    source_texture = os.path.join(stage, os.path.basename(texture_name))
                    if os.path.isfile(source_texture): shutil.copy2(source_texture, os.path.join(target, os.path.basename(texture_name)))
                return {"name": name, "entry": entry, "path": target,
                        "fbx_bytes": os.path.getsize(fbx), "source_actions": max(0, len(conversion.get("actions", [])) - 1),
                        "textures": len(conversion.get("textures", []))}, None
            except ItemSkipped:
                raise
            except Exception as item_exc:
                return None, {"name": item.get("name"), "entry": item.get("entry"),
                              "error": f"{type(item_exc).__name__}: {item_exc}"}
        def worker():
            try:
                with EXPORT_JOBS_LOCK: job["status"] = "running"
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {pool.submit(export_one, i, item): i for i, item in enumerate(clean_entries)}
                    done_count = 0
                    for fut in concurrent.futures.as_completed(futures):
                        index = futures[fut]
                        done_count += 1
                        try:
                            result, error = fut.result()
                            with EXPORT_JOBS_LOCK:
                                if result: job["results"].append(result)
                                if error: job["errors"].append(error)
                                job["completed"] = done_count
                        except ItemSkipped:
                            with EXPORT_JOBS_LOCK:
                                item = clean_entries[index]
                                job["errors"].append({"name": item.get("name"), "entry": item.get("entry"), "error": "目标已存在；未启用覆盖"})
                                job["completed"] = done_count
                manifest = os.path.join(output_dir, "batch-export-manifest.json")
                with EXPORT_JOBS_LOCK:
                    job["status"] = "completed" if not job["errors"] else "completed_with_errors"
                    job["current"] = None
                    with open(manifest, "w", encoding="utf-8") as handle: json.dump(job, handle, ensure_ascii=False, indent=2)
                if package_zip and job["results"]:
                    package = os.path.join(os.path.dirname(output_dir), f"entity-batch-{job_id}.zip")
                    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
                        for root_dir, _dirs, files in os.walk(output_dir):
                            for file_name in files:
                                full = os.path.join(root_dir, file_name)
                                archive.write(full, os.path.relpath(full, output_dir))
                    with EXPORT_JOBS_LOCK:
                        job["package"] = package
                        job["download_url"] = f"/api/export_entities_batch_download?job_id={job_id}"
            except Exception as exc:
                with EXPORT_JOBS_LOCK:
                    job["status"] = "failed"; job["current"] = None
                    job["errors"].append({"error": f"{type(exc).__name__}: {exc}"})
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)
                EXPORT_LOCK.release()
        threading.Thread(target=worker, name=f"fbx-export-{job_id}", daemon=True).start()
        return self._send_json({"job_id": job_id, "status": "queued", "output_dir": output_dir}, 202)

    def _handle_export_entity(self, qs):
        entry = qs.get("entry", [""])[0]
        requested_name = qs.get("name", [""])[0]
        if not entry or len(entry) > 512 or not entry.startswith("\\"):
            return self._send_error_json("缺少或非法的实体 entry", 400)
        if not os.path.isfile(BLENDER_BIN):
            return self._send_error_json(f"找不到 Blender：{BLENDER_BIN}；请设置 PROTOTYPE_BLENDER", 503)
        if not EXPORT_LOCK.acquire(blocking=False):
            return self._send_error_json("另一个导出任务正在运行", 429)
        try:
            digest = hashlib.sha256(entry.encode("utf-8")).hexdigest()[:16]
            clean_name = re.sub(r"[^A-Za-z0-9._-]+", "_", requested_name).strip("._")[:80] or "prototype-entity"
            output = os.path.join(EXPORT_ROOT, "gltf", "entities", digest)
            glb = os.path.join(output, f"{clean_name}.glb")
            tool = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "tools", "entities", "export_entity_gltf.py"))
            command = [sys.executable, tool, "--server", "http://127.0.0.1:8421", "--entry", entry,
                       "--output", output, "--name", clean_name]
            result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
            if result.returncode:
                detail = (result.stderr or result.stdout or "实体导出器失败")[-2000:]
                return self._send_error_json(detail, 500)
            fbx = os.path.join(output, f"{clean_name}.fbx")
            blender = BLENDER_BIN
            converter = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "tools", "entities", "convert_glb_to_fbx.py"))
            converted = subprocess.run([blender, "--background", "--python", converter, "--", glb, fbx],
                                       capture_output=True, text=True, timeout=1800,
                                       env=dict(os.environ, PROTOTYPE_FBX_MIRROR_LR="1"))
            if converted.returncode or not os.path.isfile(fbx):
                detail = (converted.stderr or converted.stdout or "FBX 转换失败")[-4000:]
                return self._send_error_json(detail, 500)
            package = os.path.join(output, f"{clean_name}-FBX.zip")
            seen_hashes = set()
            seen_names = set()
            with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.write(fbx, f"{clean_name}.fbx")
                conversion_report = fbx + ".conversion.json"
                if os.path.isfile(conversion_report):
                    with open(conversion_report, "r", encoding="utf-8") as handle:
                        texture_names = json.load(handle).get("textures", [])
                    for texture_name in texture_names:
                        base_name = os.path.basename(texture_name)
                        if base_name in seen_names:
                            continue
                        texture_path = os.path.join(output, base_name)
                        if os.path.isfile(texture_path):
                            with open(texture_path, "rb") as tf:
                                h = hashlib.sha256(tf.read()).hexdigest()
                            if h in seen_hashes:
                                continue
                            seen_hashes.add(h)
                            seen_names.add(base_name)
                            archive.write(texture_path, base_name)
            return self._send_attachment(package, f"{clean_name}-FBX.zip", "application/zip")
        except subprocess.TimeoutExpired:
            return self._send_error_json("实体导出超过 30 分钟", 504)
        finally:
            EXPORT_LOCK.release()

    def _handle_export_loaded_cells(self, qs):
        is_download = qs.get("download", ["0"])[0] in ("1", "true")
        job_id = qs.get("job_id", [""])[0]
        kind = qs.get("kind", [""])[0]
        if is_download and job_id:
            with EXPORT_JOBS_LOCK:
                job = EXPORT_JOBS.get(job_id)
            if not job or job.get("status") != "completed":
                return self._send_error_json("导出任务未就绪或不存在", 404)
            package = job.get("package")
            if not package or not os.path.isfile(package):
                return self._send_error_json("压缩包文件未找到", 404)
            if kind == "manifest":
                output_dir = os.path.dirname(package)
                manifest_path = os.path.join(output_dir, "textures-manifest.json")
                if not os.path.isfile(manifest_path):
                    return self._send_error_json("贴图分辨率清单暂未生成（可能是旧任务）", 404)
                return self._send_attachment(manifest_path, "textures-manifest.json", "application/json")
            return self._send_attachment(package, "loaded-manhattan-cells-FBX.zip", "application/zip")

        raw_cells = qs.get("cells", [""])[0]
        archive = qs.get("path", [""])[0]
        shared = qs.get("shared_path", [""])[0]
        requested_output = qs.get("output_path", [""])[0]
        is_async = qs.get("async", ["0"])[0] in ("1", "true")

        if not re.fullmatch(r"\d{1,3}(?:,\d{1,3})*", raw_cells):
            return self._send_error_json("cells 必须是逗号分隔的 0..259 编号", 400)
        cells = sorted(set(int(value) for value in raw_cells.split(",")))
        if not cells or any(value > 259 for value in cells):
            return self._send_error_json("Cell 编号超出 0..259", 400)
        try:
            archive = self._safe_resolve(archive)
            if shared:
                shared = self._safe_resolve(shared)
        except PermissionError as exc:
            return self._send_error_json(str(exc), 403)
        if not os.path.isfile(archive) or (shared and not os.path.isfile(shared)):
            return self._send_error_json("归档路径不存在", 404)
        if not os.path.isfile(BLENDER_BIN):
            return self._send_error_json(f"找不到 Blender：{BLENDER_BIN}；请设置 PROTOTYPE_BLENDER", 503)
        if not EXPORT_LOCK.acquire(blocking=False):
            return self._send_error_json("另一个导出任务正在运行", 429)

        job_id = uuid.uuid4().hex[:16]
        output = os.path.join(EXPORT_ROOT, "gltf", "web-loaded-cells")
        job = {
            "id": job_id,
            "type": "map",
            "status": "running",
            "total": len(cells),
            "completed": 0,
            "stage": "正在初始化导出...",
            "progress": 0.0,
            "current": None,
            "output_dir": requested_output or None,
            "download_url": None,
            "fbx": None,
            "textures": 0,
            "package": None,
            "error": None,
            "results": [],
            "errors": [],
        }
        with EXPORT_JOBS_LOCK:
            EXPORT_JOBS[job_id] = job

        def run_export():
            try:
                shutil.rmtree(output, ignore_errors=True)
                os.makedirs(output, exist_ok=True)
                tool = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "tools", "entities", "export_loaded_cells_gltf.py"))
                command = [sys.executable, tool, "--server", "http://127.0.0.1:8421", "--archive", archive,
                           "--cells", ",".join(map(str, cells)), "--output", output, "--name", "loaded-manhattan-cells"]
                if shared:
                    command.extend(["--shared", shared])

                proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                last_lines = []
                while True:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if not line:
                        continue
                    line_str = line.strip()
                    last_lines.append(line_str)
                    if len(last_lines) > 50:
                        last_lines.pop(0)
                    if line_str.startswith("PROGRESS:"):
                        try:
                            prog_data = json.loads(line_str[9:].strip())
                            with EXPORT_JOBS_LOCK:
                                job["completed"] = prog_data.get("completed", job["completed"])
                                job["total"] = prog_data.get("total", job["total"])
                                job["stage"] = prog_data.get("stage", job["stage"])
                                job["progress"] = min(90.0, float(prog_data.get("percent", job["progress"])))
                        except Exception:
                            pass
                ret = proc.wait(timeout=1800)
                if ret != 0:
                    detail = "\n".join(last_lines)[-2000:] or "导出器失败"
                    raise RuntimeError(detail)

                with EXPORT_JOBS_LOCK:
                    job["stage"] = "正在调用 Blender 转换为 FBX..."
                    job["progress"] = 92.0

                glb = os.path.join(output, "loaded-manhattan-cells.glb")
                fbx = os.path.join(output, "loaded-manhattan-cells.fbx")
                blender = BLENDER_BIN
                converter = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "tools", "entities", "convert_glb_to_fbx.py"))
                converted = subprocess.run([blender, "--background", "--python", converter, "--", glb, fbx],
                                           capture_output=True, text=True, timeout=1800)
                if converted.returncode or not os.path.isfile(fbx):
                    detail = (converted.stderr or converted.stdout or "FBX 转换失败")[-4000:]
                    raise RuntimeError(detail)

                with EXPORT_JOBS_LOCK:
                    job["stage"] = "正在去重贴图并打包..."
                    job["progress"] = 96.0

                conversion_report = fbx + ".conversion.json"
                texture_names = []
                if os.path.isfile(conversion_report):
                    with open(conversion_report, "r", encoding="utf-8") as handle:
                        texture_names = json.load(handle).get("textures", [])

                package = os.path.join(output, "loaded-manhattan-cells-FBX.zip")
                texture_stats = None
                manifest_path = os.path.join(output, "textures-manifest.json")
                if os.path.isfile(manifest_path):
                    try:
                        with open(manifest_path, "r", encoding="utf-8") as mh:
                            tex_doc = json.load(mh)
                        texture_stats = {
                            "count": tex_doc.get("textureCount", 0),
                            "maxSize": tex_doc.get("maxTextureSize", "?"),
                            "buckets": tex_doc.get("sizeBuckets", {}),
                        }
                    except Exception:
                        texture_stats = None
                seen_hashes = set()
                seen_names = set()
                written_count = 0
                with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as zip_h:
                    zip_h.write(fbx, "loaded-manhattan-cells.fbx")
                    manifest_path = os.path.join(output, "textures-manifest.json")
                    if os.path.isfile(manifest_path):
                        zip_h.write(manifest_path, "textures-manifest.json")
                    for texture_name in texture_names:
                        base_name = os.path.basename(texture_name)
                        if base_name in seen_names:
                            continue
                        texture_path = os.path.join(output, base_name)
                        if os.path.isfile(texture_path):
                            with open(texture_path, "rb") as tf:
                                h = hashlib.sha256(tf.read()).hexdigest()
                            if h in seen_hashes:
                                continue
                            seen_hashes.add(h)
                            seen_names.add(base_name)
                            zip_h.write(texture_path, base_name)
                            written_count += 1

                if requested_output:
                    destination = self._safe_export_dir(requested_output)
                    shutil.copy2(fbx, os.path.join(destination, "loaded-manhattan-cells.fbx"))
                    copied_count = 0
                    dest_seen_hashes = set()
                    dest_seen_names = set()
                    for texture_name in texture_names:
                        base_name = os.path.basename(texture_name)
                        if base_name in dest_seen_names:
                            continue
                        texture_path = os.path.join(output, base_name)
                        if os.path.isfile(texture_path):
                            with open(texture_path, "rb") as tf:
                                h = hashlib.sha256(tf.read()).hexdigest()
                            if h in dest_seen_hashes:
                                continue
                            dest_seen_hashes.add(h)
                            dest_seen_names.add(base_name)
                            shutil.copy2(texture_path, os.path.join(destination, base_name))
                            copied_count += 1
                    with EXPORT_JOBS_LOCK:
                        job.update({
                            "status": "completed",
                            "stage": "导出完成",
                            "progress": 100.0,
                            "output_dir": destination,
                            "fbx": "loaded-manhattan-cells.fbx",
                            "textures": copied_count,
                            "texture_stats": texture_stats,
                            "package": package,
                        })
                else:
                    with EXPORT_JOBS_LOCK:
                        job.update({
                            "status": "completed",
                            "stage": "导出完成，正在准备下载",
                            "progress": 100.0,
                            "download_url": f"/api/export_loaded_cells?download=1&job_id={job_id}",
                            "manifest_url": f"/api/export_loaded_cells?download=1&kind=manifest&job_id={job_id}",
                            "fbx": "loaded-manhattan-cells.fbx",
                            "textures": written_count,
                            "texture_stats": texture_stats,
                            "package": package,
                        })
            except Exception as exc:
                with EXPORT_JOBS_LOCK:
                    job.update({
                        "status": "failed",
                        "stage": f"导出失败: {exc}",
                        "error": str(exc),
                    })
            finally:
                EXPORT_LOCK.release()

        if is_async:
            threading.Thread(target=run_export, daemon=True).start()
            return self._send_json({"job_id": job_id, "status": "running"}, 202)
        else:
            run_export()
            with EXPORT_JOBS_LOCK:
                final_job = json.loads(json.dumps(job, ensure_ascii=False))
            if final_job["status"] == "failed":
                return self._send_error_json(final_job.get("error") or "导出失败", 500)
            if requested_output:
                return self._send_json({"status": "completed", "output_dir": final_job["output_dir"],
                                        "cells": cells, "fbx": "loaded-manhattan-cells.fbx",
                                        "textures": final_job["textures"]})
            return self._send_attachment(final_job["package"], "loaded-manhattan-cells-FBX.zip", "application/zip")

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
        # HTML entry points must never be served from browser cache, or the
        # ?v= version bump on module URLs cannot take effect after deploys.
        # Versioned static assets stay cacheable.
        if target.endswith(".html"):
            self.send_header("Cache-Control", "no-cache, must-revalidate")
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
